"""Submit ORCA optimization and optional frequency jobs through managed projects."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from moltage.app.connection_service import ServerConnectionService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_planning import create_initial_project, project_directory_candidates
from moltage.app.project_submission import allocate_remote_project_directory
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    MANAGED_METADATA_DIRECTORY,
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
    append_orca_frequency_step,
    remote_step_directory,
)
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.orca.batch import render_orca_submit_script
from moltage.orca.evidence import parse_orca_final_xyz
from moltage.orca.input_writer import render_orca_frequency_input, render_orca_optimization_input
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteExecutorError,
    RemotePathNotFoundError,
)
from moltage.remote.orca_runtime import OrcaRuntimeError, require_usable_orca_runtime
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.slurm import (
    SlurmSubmissionError,
    build_sbatch_submission_command,
    parse_sbatch_parsable_output,
)
from moltage.remote.slurm_discovery import resolve_slurm_for_submission
from moltage.remote.slurm_discovery import slurm_command_path
from moltage.remote.slurm_cancel import (
    SlurmCancellationError,
    request_slurm_cancellation_once,
)
from moltage.remote.slurm_status import (
    SchedulerStatusKind,
    SlurmStatusError,
    query_slurm_job_status,
)
from moltage.remote.step_inputs import upload_new_files_atomically


ORCA_OPT_INPUT = "orca_opt.inp"
ORCA_OPT_OUTPUT = "orca_opt.out"
ORCA_OPT_SCRIPT = "submit.orca.sh"
ORCA_FREQ_INPUT = "orca_freq.inp"
ORCA_FREQ_OUTPUT = "orca_freq.out"
ORCA_FREQ_SCRIPT = "submit.orca.freq.sh"


class OrcaSubmissionError(RuntimeError):
    """A sanitized ORCA preflight, preparation, or submission failure."""


class OrcaSubmissionOutcomeUnknown(OrcaSubmissionError):
    """The scheduler may have accepted the job; automatic retry is forbidden."""


@dataclass(frozen=True, slots=True)
class OrcaOptimizationSubmissionRequest:
    profile: ServerProfile
    base_name: str
    source_molecule_name: str
    structure: MolecularStructure
    settings: OrcaOptimizationSettings
    supplied_password: str | None = field(default=None, repr=False, compare=False)
    project_id: UUID | None = None
    resubmission_source_project: CalculationProject | None = None
    profile_rebind_confirmed: bool = False

    def __post_init__(self) -> None:
        if self.project_id is not None and not isinstance(self.project_id, UUID):
            raise TypeError("project_id must be a UUID or None")
        if self.resubmission_source_project is not None:
            if not isinstance(self.resubmission_source_project, CalculationProject):
                raise TypeError("resubmission source must be a CalculationProject")
            if (
                self.resubmission_source_project.workflow_kind
                is not CalculationWorkflowKind.ORCA
            ):
                raise ValueError("resubmission source must be an ORCA project")
        if not isinstance(self.profile_rebind_confirmed, bool):
            raise TypeError("profile_rebind_confirmed must be boolean")


@dataclass(frozen=True, slots=True)
class OrcaFrequencySubmissionRequest:
    profile: ServerProfile
    project: CalculationProject
    optimized_structure: MolecularStructure
    settings: OrcaFrequencySettings
    supplied_password: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class OrcaSubmissionResult:
    project: CalculationProject
    step: ProjectStepRecord
    remote_step_directory: str
    job_id: str
    cluster_name: str | None
    input_hashes: tuple[tuple[str, str], ...]


class OrcaSubmissionService:
    """Apply existing remote-safety semantics without entering the FHI workflow."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        project_id_factory: Callable[[], UUID] = uuid4,
        temporary_id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._now_factory = now_factory
        self._project_id_factory = project_id_factory
        self._temporary_id_factory = temporary_id_factory

    def submit_optimization(
        self,
        request: OrcaOptimizationSubmissionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> OrcaSubmissionResult:
        if not isinstance(request, OrcaOptimizationSubmissionRequest):
            raise TypeError("request must be OrcaOptimizationSubmissionRequest")
        profile, preset, runtime = _preflight_profile(request.profile)
        preset = _preset_for_settings(preset, request.settings)
        _require_matching_version(request.settings, runtime.version_evidence.version_family)
        input_bytes = render_orca_optimization_input(
            request.structure, request.settings
        ).encode("utf-8")
        created_at = _aware_now(self._now_factory)
        project_id = request.project_id or self._project_id_factory()
        first_name = next(iter(project_directory_candidates(request.base_name, created_at.date())))
        candidate = create_initial_project(
            base_name=request.base_name,
            remote_directory_name=first_name,
            source_molecule_name=request.source_molecule_name,
            server_profile_id=profile.profile_id,
            remote_project_root=profile.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=created_at,
            project_id=project_id,
        )
        candidate = _with_orca_step(
            candidate,
            replace(
                candidate.steps[0],
                orca_optimization_settings=request.settings,
                orca_runtime=runtime,
                orca_submitted_elements=tuple(atom.element for atom in request.structure),
            ),
        )
        script_bytes = render_orca_submit_script(
            preset,
            runtime,
            project_id,
            request.settings,
            mail_settings=profile.slurm_mail_settings,
        ).encode("utf-8")
        files = {ORCA_OPT_INPUT: input_bytes, ORCA_OPT_SCRIPT: script_bytes}
        report = progress or (lambda _message: None)
        report(f"Connecting to {profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            profile, request.supplied_password
        )
        try:
            report("Validating ORCA and scheduler...")
            require_usable_orca_runtime(executor, runtime)
            scheduler = resolve_slurm_for_submission(executor, preset)
            _require_workspace(executor, profile.remote_project_root)
            repository = RemoteProjectRepository(
                executor, temporary_id_factory=self._temporary_id_factory
            )
            if request.resubmission_source_project is not None:
                report("Checking and cancelling the prior ORCA job if it is active...")
                self._cancel_resubmission_source(
                    executor,
                    repository,
                    request,
                    preset,
                    scheduler,
                )
            report("Creating managed ORCA project...")
            directory_name, remote_path = allocate_remote_project_directory(
                executor, profile.remote_project_root, request.base_name, created_at.date()
            )
            project = replace(
                candidate,
                remote_directory_name=directory_name,
                remote_project_path=remote_path,
            )
            executor.mkdir(str(PurePosixPath(remote_path) / MANAGED_METADATA_DIRECTORY))
            repository.write_initial(project)
            self._local_index_repository.mark_seen(project)
            return self._upload_and_submit(
                executor,
                repository,
                project,
                ProjectStepKind.ORCA_OPTIMIZATION,
                files,
                ORCA_OPT_SCRIPT,
                scheduler,
                profile,
                report,
            )
        except (OrcaSubmissionError, OrcaSubmissionOutcomeUnknown):
            raise
        except (OrcaRuntimeError, SlurmSubmissionError, RemoteExecutorError, ValueError) as error:
            raise OrcaSubmissionError(str(error)) from None
        finally:
            executor.close()

    def _cancel_resubmission_source(
        self,
        executor,
        repository: RemoteProjectRepository,
        request: OrcaOptimizationSubmissionRequest,
        preset,
        scheduler,
    ) -> None:
        """Cancel only an authoritatively active source job before resubmission."""

        source = request.resubmission_source_project
        assert source is not None
        if PurePosixPath(source.remote_project_path).parent != PurePosixPath(
            request.profile.remote_project_root
        ):
            raise OrcaSubmissionError(
                "The ORCA resubmission source is outside the selected server workspace"
            )
        current = repository.load(
            source.remote_project_path,
            preserve_remote_errors=True,
        )
        if current.project_id != source.project_id or current.revision != source.revision:
            raise OrcaSubmissionError(
                "The source ORCA project changed; refresh it before resubmitting"
            )
        if (
            current.server_profile_id != request.profile.profile_id
            and not request.profile_rebind_confirmed
        ):
            raise OrcaSubmissionError(
                "Confirm the current server profile before resubmitting this ORCA project"
            )
        step = _active_orca_step(current)
        if step.state not in {
            ProjectStepState.QUEUED,
            ProjectStepState.RUNNING,
            ProjectStepState.UNKNOWN,
        }:
            return
        if step.job_id is None:
            raise OrcaSubmissionError(
                "The prior ORCA calculation is unresolved and has no exact Job ID"
            )
        if (
            step.scheduler_kind is not None
            and step.scheduler_kind is not scheduler.scheduler_kind
        ):
            raise OrcaSubmissionError(
                "The prior ORCA job uses a different scheduler configuration"
            )
        effective = replace(
            preset,
            slurm_bin_directory=scheduler.bin_directory,
        )
        try:
            status = query_slurm_job_status(
                executor,
                squeue_path=slurm_command_path(effective, "squeue"),
                sacct_path=slurm_command_path(effective, "sacct"),
                job_id=step.job_id,
                profile_username=request.profile.username,
                lsf_env_directory=scheduler.lsf_env_directory,
                lsf_library_directory=scheduler.lsf_library_directory,
                lsf_server_directory=scheduler.lsf_server_directory,
            )
        except SlurmStatusError as error:
            raise OrcaSubmissionError(
                f"The prior ORCA Job status could not be verified: {error}"
            ) from None
        if status.kind in {
            SchedulerStatusKind.COMPLETED,
            SchedulerStatusKind.FAILED,
        }:
            return
        if status.kind not in {
            SchedulerStatusKind.QUEUED,
            SchedulerStatusKind.RUNNING,
        }:
            raise OrcaSubmissionError(
                "The prior ORCA Job state is not authoritative; refresh before resubmitting"
            )
        try:
            request_slurm_cancellation_once(
                executor,
                scancel_path=slurm_command_path(effective, "scancel"),
                job_id=step.job_id,
                lsf_env_directory=scheduler.lsf_env_directory,
                lsf_library_directory=scheduler.lsf_library_directory,
                lsf_server_directory=scheduler.lsf_server_directory,
            )
        except RemoteCommandOutcomeUnknown:
            raise OrcaSubmissionOutcomeUnknown(
                "Cancellation outcome for the prior ORCA Job is unknown; the new job was not submitted"
            ) from None
        except SlurmCancellationError as error:
            raise OrcaSubmissionError(str(error)) from None
        changed = _with_orca_step(
            current,
            replace(
                step,
                state=ProjectStepState.UNKNOWN,
                scheduler_state="CANCEL_REQUESTED",
                last_error=(
                    "Cancellation requested automatically before ORCA optimization resubmission"
                ),
            ),
        )
        updated = repository.persist_update(
            changed,
            updated_at=_aware_now(self._now_factory),
        )
        self._local_index_repository.mark_seen(
            updated,
            bound_server_profile_id=request.profile.profile_id,
        )

    def submit_frequency(
        self,
        request: OrcaFrequencySubmissionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> OrcaSubmissionResult:
        if not isinstance(request, OrcaFrequencySubmissionRequest):
            raise TypeError("request must be OrcaFrequencySubmissionRequest")
        profile, preset, runtime = _preflight_profile(request.profile)
        preset = _preset_for_settings(preset, request.settings)
        project = request.project
        if project.workflow_kind is not CalculationWorkflowKind.ORCA:
            raise OrcaSubmissionError("Frequency can be submitted only for an ORCA project")
        optimization = project.steps[0]
        if optimization.orca_optimization_settings != request.settings.source_optimization:
            raise OrcaSubmissionError("Frequency scientific settings differ from the source optimization")
        if optimization.orca_optimization_result is None or not optimization.orca_optimization_result.succeeded:
            raise OrcaSubmissionError("Verified ORCA optimization success is required")
        _require_matching_version(request.settings.source_optimization, runtime.version_evidence.version_family)
        input_bytes = render_orca_frequency_input(
            request.optimized_structure, request.settings
        ).encode("utf-8")
        script_bytes = render_orca_submit_script(
            preset,
            runtime,
            project.project_id,
            request.settings,
            frequency=True,
            mail_settings=profile.slurm_mail_settings,
        ).encode("utf-8")
        report = progress or (lambda _message: None)
        executor = self._connection_service.connect_for_remote_operation(
            profile, request.supplied_password
        )
        try:
            report("Validating ORCA, source geometry, and scheduler...")
            require_usable_orca_runtime(executor, runtime)
            scheduler = resolve_slurm_for_submission(executor, preset)
            repository = RemoteProjectRepository(
                executor, temporary_id_factory=self._temporary_id_factory
            )
            current = repository.load(project.remote_project_path)
            if current.project_id != project.project_id or current.revision != project.revision:
                raise OrcaSubmissionError("Remote ORCA project changed; refresh before submitting frequency")
            xyz_path = str(PurePosixPath(current.remote_project_path) / "orca_opt.xyz")
            xyz = executor.read_bytes(xyz_path)
            parsed = parse_orca_final_xyz(xyz, _submitted_structure(executor, current))
            if parsed.atoms != request.optimized_structure.atoms:
                raise OrcaSubmissionError("Frequency geometry differs from verified orca_opt.xyz")
            candidate = append_orca_frequency_step(current)
            frequency_step = replace(
                candidate.steps[-1],
                orca_frequency_settings=request.settings,
                orca_runtime=runtime,
            )
            candidate = _with_orca_step(candidate, frequency_step)
            candidate = repository.persist_update(candidate, updated_at=_aware_now(self._now_factory))
            frequency_directory = remote_step_directory(candidate, ProjectStepKind.ORCA_FREQUENCY)
            executor.mkdir(frequency_directory)
            self._local_index_repository.mark_seen(candidate)
            return self._upload_and_submit(
                executor,
                repository,
                candidate,
                ProjectStepKind.ORCA_FREQUENCY,
                {ORCA_FREQ_INPUT: input_bytes, ORCA_FREQ_SCRIPT: script_bytes},
                ORCA_FREQ_SCRIPT,
                scheduler,
                profile,
                report,
            )
        except (OrcaSubmissionError, OrcaSubmissionOutcomeUnknown):
            raise
        except (OrcaRuntimeError, SlurmSubmissionError, RemoteExecutorError, ValueError) as error:
            raise OrcaSubmissionError(str(error)) from None
        finally:
            executor.close()

    def _upload_and_submit(
        self,
        executor,
        repository,
        project,
        step_kind,
        files,
        script_filename,
        scheduler,
        profile,
        report,
    ) -> OrcaSubmissionResult:
        step_directory = remote_step_directory(project, step_kind)
        report("Uploading and verifying ORCA inputs...")
        hashes = upload_new_files_atomically(
            executor,
            step_directory,
            files,
            temporary_id_factory=self._temporary_id_factory,
        )
        command = build_sbatch_submission_command(
            step_directory,
            scheduler.sbatch_path,
            script_filename,
            lsf_env_directory=scheduler.lsf_env_directory,
            lsf_library_directory=scheduler.lsf_library_directory,
            lsf_server_directory=scheduler.lsf_server_directory,
        )
        report(f"Submitting {scheduler_display_name(scheduler.scheduler_kind)} job...")
        try:
            result = executor.execute(command)
        except RemoteCommandOutcomeUnknown:
            unknown = _update_step(
                project,
                step_kind,
                state=ProjectStepState.UNKNOWN,
                input_hashes=hashes,
                last_error="Scheduler submission outcome is unknown; no automatic retry was performed.",
                scheduler_kind=scheduler.scheduler_kind,
                submit_script_filename=script_filename,
            )
            repository.persist_update(unknown, updated_at=_aware_now(self._now_factory))
            raise OrcaSubmissionOutcomeUnknown(
                "Scheduler submission outcome is unknown; inspect the scheduler before retrying"
            ) from None
        if result.exit_status != 0:
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip().splitlines()
            message = "Scheduler rejected the ORCA job"
            if detail:
                message += ": " + detail[0][:300]
            failed = _update_step(
                project,
                step_kind,
                state=ProjectStepState.FAILED,
                input_hashes=hashes,
                last_error=message,
                scheduler_kind=scheduler.scheduler_kind,
                submit_script_filename=script_filename,
                finished_at=_aware_now(self._now_factory),
            )
            repository.persist_update(failed, updated_at=_aware_now(self._now_factory))
            raise OrcaSubmissionError(message)
        try:
            receipt = parse_sbatch_parsable_output(result.stdout)
        except SlurmSubmissionError:
            unknown = _update_step(
                project,
                step_kind,
                state=ProjectStepState.UNKNOWN,
                input_hashes=hashes,
                last_error="Scheduler returned an ambiguous ORCA submission receipt; no automatic retry was performed.",
                scheduler_kind=scheduler.scheduler_kind,
                submit_script_filename=script_filename,
            )
            repository.persist_update(unknown, updated_at=_aware_now(self._now_factory))
            raise OrcaSubmissionOutcomeUnknown(
                "Scheduler returned an ambiguous submission receipt; inspect it before retrying"
            ) from None
        submitted_at = _aware_now(self._now_factory)
        scheduler_output = (
            "orca_freq.scheduler.out"
            if step_kind is ProjectStepKind.ORCA_FREQUENCY
            else "orca_opt.scheduler.out"
        )
        queued = _update_step(
            project,
            step_kind,
            state=ProjectStepState.QUEUED,
            job_id=receipt.job_id,
            cluster_name=receipt.cluster_name,
            submitted_at=submitted_at,
            input_hashes=hashes,
            scheduler_kind=scheduler.scheduler_kind,
            submit_script_filename=script_filename,
            slurm_output_filename=scheduler_output,
            last_error=None,
        )
        updated = repository.persist_update(queued, updated_at=submitted_at)
        self._local_index_repository.mark_seen(
            updated, bound_server_profile_id=profile.profile_id
        )
        step = next(item for item in updated.steps if item.kind is step_kind)
        return OrcaSubmissionResult(
            updated,
            step,
            step_directory,
            receipt.job_id,
            receipt.cluster_name,
            hashes,
        )


def _preflight_profile(profile):
    if not isinstance(profile, ServerProfile):
        raise TypeError("ORCA submission requires a ServerProfile")
    if profile.execution_preset is None:
        raise OrcaSubmissionError("Configure shared Cluster settings before ORCA submission")
    if profile.orca_runtime is None:
        raise OrcaSubmissionError("Configure and validate ORCA for this server profile")
    return profile, profile.execution_preset, profile.orca_runtime


def _preset_for_settings(preset, settings):
    """Apply explicit stage resources while retaining profile-owned site data."""

    return replace(
        preset,
        nodes=settings.scheduler_nodes,
        ntasks=settings.process_count,
        cpus_per_task=1,
        runtime_minutes=settings.runtime_minutes,
        memory_gb=settings.scheduler_memory_gb,
        omp_num_threads=1,
    )


def _require_matching_version(settings, family):
    if settings.version_family != family:
        if family is None:
            raise OrcaSubmissionError(
                "Version-specific ORCA settings require a verified supported runtime version"
            )
        raise OrcaSubmissionError(
            "ORCA settings do not match the currently verified runtime version"
        )


def _require_workspace(executor, path):
    try:
        item = executor.stat(path)
    except RemotePathNotFoundError:
        raise OrcaSubmissionError(f"Remote project workspace does not exist: {path}") from None
    if not item.is_directory:
        raise OrcaSubmissionError(f"Remote project workspace is not a directory: {path}")


def _with_orca_step(project, step):
    return replace(
        project,
        steps=tuple(step if item.kind is step.kind else item for item in project.steps),
    )


def _update_step(project, step_kind, **changes):
    current = next(item for item in project.steps if item.kind is step_kind)
    return _with_orca_step(project, replace(current, **changes))


def _submitted_structure(executor, project):
    from moltage.orca.input_writer import parse_rendered_orca_structure

    return parse_rendered_orca_structure(
        executor.read_bytes(str(PurePosixPath(project.remote_project_path) / ORCA_OPT_INPUT))
    )


def _active_orca_step(project: CalculationProject) -> ProjectStepRecord:
    for step in reversed(project.steps):
        if step.state not in {
            ProjectStepState.NOT_STARTED,
            ProjectStepState.SKIPPED,
        }:
            return step
    return project.steps[0]


def _aware_now(factory):
    value = factory()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ORCA submission timestamps must be timezone-aware")
    return value
