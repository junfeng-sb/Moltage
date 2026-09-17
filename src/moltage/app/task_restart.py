"""Abort one exact active task and prepare an immutable local restart draft."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
from pathlib import PurePosixPath
from uuid import UUID

from moltage.aims.input_parser import (
    AimsInputParsingError,
    parse_generated_optimization_inputs,
    parse_generated_transport_convergence_inputs,
)
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.aims.transport_evidence import (
    TransportCompletionEvidence,
)
from moltage.aitranss.slurm import (
    AitranssExecutionSettings,
    parse_aitranss_execution_settings,
)
from moltage.aitranss.tcontrol import (
    TControlError,
    TControlSettings,
    parse_tcontrol,
)
from moltage.aitranss.transmission import submitted_tcontrol_bytes
from moltage.app.connection_service import ServerConnectionService
from moltage.app.project_recovery import ProjectProfileRebindRequired
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectElectrodeClusterProvenance,
    ProjectRestartProvenance,
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
    remote_step_directory,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.scheduler import (
    SchedulerKind,
    scheduler_display_name,
)
from moltage.domain.server_profile import ServerProfile, SlurmExecutionPreset
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_surface import (
    ElectrodeSurfaceProposal,
    resolve_project_electrode_surfaces,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteExecutor,
)
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.slurm import preset_with_submit_script_resources
from moltage.remote.slurm_cancel import request_slurm_cancellation_once
from moltage.remote.slurm_discovery import (
    resolve_slurm_for_submission,
    slurm_command_path,
)
from moltage.remote.slurm_status import (
    SchedulerStatusKind,
    query_slurm_job_status,
    validate_job_id,
)
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii


class ProjectTaskRestartError(RuntimeError):
    """Raised before mutation when a task cannot safely become a restart draft."""


class ProjectTaskCancellationOutcome(StrEnum):
    REQUESTED = "REQUESTED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProjectTaskRestartRequest:
    profile: ServerProfile
    project: CalculationProject
    step_kind: ProjectStepKind
    profile_rebind_confirmed: bool = False
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("task restart requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("task restart requires a CalculationProject")
        if not isinstance(self.step_kind, ProjectStepKind):
            raise TypeError("task restart step is unsupported")
        if not isinstance(self.profile_rebind_confirmed, bool):
            raise TypeError("profile-rebind confirmation must be boolean")
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("temporary password must be nonempty text or None")
        _validate_selected_root(self.profile, self.project.remote_project_path)
        if _active_step_kind(self.project) is not self.step_kind:
            raise ProjectTaskRestartError(
                "Only the current active project task can be aborted. Refresh first."
            )
        step = _project_step(self.project, self.step_kind)
        if step.state not in {ProjectStepState.QUEUED, ProjectStepState.RUNNING}:
            raise ProjectTaskRestartError(
                "Task restart requires a QUEUED or RUNNING current task."
            )
        if step.job_id is None:
            raise ProjectTaskRestartError(
                "The active task has no exact scheduler Job ID."
            )
        validate_job_id(step.job_id)


@dataclass(frozen=True, slots=True)
class ProjectTaskRestartDraft:
    """Exact pre-run inputs plus parsed editable settings for one local draft."""

    profile: ServerProfile
    source_project: CalculationProject
    source_step: ProjectStepKind
    source_job_id: str
    source_structure: MolecularStructure
    connectivity: Connectivity
    source_geometry_sha256: str
    optimization_settings: AimsOptimizationSettings | None = None
    transport_settings: TransportConvergenceSettings | None = None
    tcontrol_settings: TControlSettings | None = None
    tcontrol_self_energy_filename: str | None = None
    step4_execution_settings: AitranssExecutionSettings | None = None
    transport_evidence: TransportCompletionEvidence | None = None
    surface_proposal: ElectrodeSurfaceProposal | None = None
    electrode_provenance: tuple[ProjectElectrodeClusterProvenance, ...] = ()
    profile_rebind_confirmed: bool = False
    source_terminal_confirmed: bool = False

    @property
    def restart_provenance(self) -> ProjectRestartProvenance:
        return ProjectRestartProvenance(
            self.source_project.project_id,
            self.source_step,
            self.source_job_id,
            self.source_geometry_sha256,
        )

    @property
    def display_title(self) -> str:
        number = tuple(ProjectStepKind).index(self.source_step) + 1
        return (
            f"{self.source_project.remote_directory_name} — "
            f"Step {number} Restart Draft"
        )

    @property
    def coordinate_only(self) -> bool:
        return self.source_step in {
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            ProjectStepKind.TRANSMISSION,
        }


@dataclass(frozen=True, slots=True)
class ProjectTaskRestartResult:
    outcome: ProjectTaskCancellationOutcome
    project_id: UUID
    step_kind: ProjectStepKind
    job_id: str
    draft: ProjectTaskRestartDraft | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProjectTaskCancellationOutcome):
            raise TypeError("task cancellation outcome is unsupported")
        if not isinstance(self.project_id, UUID):
            raise TypeError("task restart result requires a project UUID")
        if not isinstance(self.step_kind, ProjectStepKind):
            raise TypeError("task restart result step is unsupported")
        validate_job_id(self.job_id)
        if self.outcome is ProjectTaskCancellationOutcome.UNKNOWN:
            if self.draft is not None:
                raise TypeError("unknown cancellation outcome cannot expose a draft")
        elif not isinstance(self.draft, ProjectTaskRestartDraft):
            raise TypeError("known cancellation outcome requires a restart draft")


class ProjectTaskRestartService:
    """Read exact inputs, then dispatch at most one exact-ID scancel."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        *,
        covalent_radii_loader: Callable[[], Mapping[str, float]] = (
            load_default_covalent_radii
        ),
    ) -> None:
        self._connection_service = connection_service
        self._covalent_radii_loader = covalent_radii_loader

    def abort_and_prepare(
        self,
        request: ProjectTaskRestartRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> ProjectTaskRestartResult:
        if not isinstance(request, ProjectTaskRestartRequest):
            raise TypeError("abort_and_prepare requires ProjectTaskRestartRequest")
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            result = self._abort_and_prepare(executor, request, report)
        except Exception:
            _close_without_replacing_outcome(executor)
            raise
        _close_without_replacing_outcome(executor)
        return result

    def _abort_and_prepare(
        self,
        executor: RemoteExecutor,
        request: ProjectTaskRestartRequest,
        progress: Callable[[str], None],
    ) -> ProjectTaskRestartResult:
        progress("Reloading the authoritative current task...")
        project = RemoteProjectRepository(executor).load(
            request.project.remote_project_path,
            preserve_remote_errors=True,
        )
        if project.project_id != request.project.project_id:
            raise ProjectTaskRestartError(
                "Project identity changed before cancellation. Refresh first."
            )
        if project.revision != request.project.revision:
            raise ProjectTaskRestartError(
                "Project revision changed before cancellation. Refresh first."
            )
        if (
            project.server_profile_id != request.profile.profile_id
            and not request.profile_rebind_confirmed
        ):
            raise ProjectProfileRebindRequired(project, request.profile)
        if _active_step_kind(project) is not request.step_kind:
            raise ProjectTaskRestartError(
                "The active project task changed before cancellation. Refresh first."
            )
        expected = _project_step(request.project, request.step_kind)
        current = _project_step(project, request.step_kind)
        if (
            current.job_id != expected.job_id
            or current.state not in {
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
            }
        ):
            raise ProjectTaskRestartError(
                "The current task attempt changed before cancellation. Refresh first."
            )
        assert current.job_id is not None
        validate_job_id(current.job_id)

        preset = request.profile.execution_preset
        if preset is None:
            raise ProjectTaskRestartError(
                "Cluster Execution Settings are unavailable for this task."
            )
        if (
            current.scheduler_kind is not None
            and current.scheduler_kind is not preset.scheduler_kind
        ):
            raise ProjectTaskRestartError(
                f"This job was submitted through {current.scheduler_kind.value}, "
                f"but the current profile uses {preset.scheduler_kind.value}. "
                "Restore the matching scheduler setting before aborting it."
            )
        scheduler_name = scheduler_display_name(
            current.scheduler_kind or SchedulerKind.SLURM
        )
        progress(f"Checking the exact {scheduler_name} job...")
        slurm = resolve_slurm_for_submission(executor, preset)
        effective_preset = replace(
            preset,
            slurm_bin_directory=slurm.bin_directory,
        )
        status = query_slurm_job_status(
            executor,
            squeue_path=slurm_command_path(effective_preset, "squeue"),
            sacct_path=slurm_command_path(effective_preset, "sacct"),
            job_id=current.job_id,
            profile_username=request.profile.username,
            lsf_env_directory=slurm.lsf_env_directory,
            lsf_library_directory=slurm.lsf_library_directory,
            lsf_server_directory=slurm.lsf_server_directory,
        )
        if status.kind not in {
            SchedulerStatusKind.QUEUED,
            SchedulerStatusKind.RUNNING,
            SchedulerStatusKind.COMPLETED,
            SchedulerStatusKind.FAILED,
        }:
            raise ProjectTaskRestartError(
                "The exact Job is not authoritatively active or terminal. "
                "Refresh before aborting."
            )

        progress("Reading and validating the pre-run scientific inputs...")
        draft = self._prepare_draft(
            executor,
            request.profile,
            project,
            current,
            request.profile_rebind_confirmed,
        )
        if status.kind in {
            SchedulerStatusKind.COMPLETED,
            SchedulerStatusKind.FAILED,
        }:
            return ProjectTaskRestartResult(
                ProjectTaskCancellationOutcome.ALREADY_TERMINAL,
                project.project_id,
                current.kind,
                current.job_id,
                draft,
            )

        progress(
            f"Requesting cancellation of {scheduler_name} Job {current.job_id}..."
        )
        try:
            request_slurm_cancellation_once(
                executor,
                scancel_path=slurm_command_path(
                    effective_preset,
                    "scancel",
                ),
                job_id=current.job_id,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
        except RemoteCommandOutcomeUnknown:
            return ProjectTaskRestartResult(
                ProjectTaskCancellationOutcome.UNKNOWN,
                project.project_id,
                current.kind,
                current.job_id,
            )
        return ProjectTaskRestartResult(
            ProjectTaskCancellationOutcome.REQUESTED,
            project.project_id,
            current.kind,
            current.job_id,
            draft,
        )

    def _prepare_draft(
        self,
        executor: RemoteExecutor,
        profile: ServerProfile,
        project: CalculationProject,
        step: ProjectStepRecord,
        profile_rebind_confirmed: bool,
    ) -> ProjectTaskRestartDraft:
        geometry_step = (
            ProjectStepKind.TRANSPORT_CONVERGENCE
            if step.kind is ProjectStepKind.TRANSMISSION
            else step.kind
        )
        geometry_record = _project_step(project, geometry_step)
        directory = PurePosixPath(remote_step_directory(project, geometry_step))
        geometry = _read_hashed(
            executor,
            directory,
            "geometry.in",
            geometry_record,
        )
        control = _read_hashed(
            executor,
            directory,
            "control.in",
            geometry_record,
        )
        geometry_hash = hashlib.sha256(geometry).hexdigest()
        base_preset = profile.execution_preset
        assert base_preset is not None
        source_script_name = geometry_record.submit_script_filename or "submit.sh"
        source_script = _read_hashed(
            executor,
            directory,
            source_script_name,
            geometry_record,
        )
        attempt_preset = preset_with_submit_script_resources(
            base_preset,
            source_script,
        )
        restart_profile = replace(profile, execution_preset=attempt_preset)

        optimization: AimsOptimizationSettings | None = None
        transport: TransportConvergenceSettings | None = None
        tcontrol_settings: TControlSettings | None = None
        tcontrol_self_energy: str | None = None
        execution: AitranssExecutionSettings | None = None
        evidence: TransportCompletionEvidence | None = None
        surface: ElectrodeSurfaceProposal | None = None
        electrode_provenance: tuple[
            ProjectElectrodeClusterProvenance, ...
        ] = ()
        try:
            if geometry_step in {
                ProjectStepKind.MOLECULE_OPT,
                ProjectStepKind.MOLECULE_AU_OPT,
            }:
                parsed = parse_generated_optimization_inputs(
                    geometry,
                    control,
                )
                structure = parsed.structure
                optimization = parsed.settings
            else:
                parsed_transport = parse_generated_transport_convergence_inputs(
                    geometry,
                    control,
                )
                structure = parsed_transport.structure
                transport = parsed_transport.settings
                surface = resolve_project_electrode_surfaces(project, structure)
                electrode_provenance = _restart_electrode_provenance(
                    project,
                    surface,
                )
        except (AimsInputParsingError, TypeError, ValueError) as error:
            raise ProjectTaskRestartError(
                "The original generated scientific settings cannot be recovered "
                f"exactly: {error}"
            ) from None

        if step.kind is ProjectStepKind.TRANSMISSION:
            step4_directory = PurePosixPath(remote_step_directory(project, step.kind))
            tcontrol_raw = _read_hashed_allowing_submitted_tcontrol(
                executor,
                step4_directory,
                "tcontrol",
                step,
            )
            submitted_tcontrol = submitted_tcontrol_bytes(tcontrol_raw)
            try:
                parsed_tcontrol = parse_tcontrol(submitted_tcontrol, structure)
            except TControlError as error:
                raise ProjectTaskRestartError(
                    f"The current Step-4 tcontrol cannot be recovered: {error}"
                ) from None
            script_name = step.submit_script_filename
            if script_name is None:
                raise ProjectTaskRestartError(
                    "The current Step-4 submit script filename is unavailable."
                )
            step4_script = _read_hashed(
                executor,
                step4_directory,
                script_name,
                step,
            )
            execution = parse_aitranss_execution_settings(step4_script)
            tcontrol_settings = parsed_tcontrol.settings
            tcontrol_self_energy = parsed_tcontrol.self_energy_filename
            evidence = TransportCompletionEvidence(
                parsed_tcontrol.settings.natoms,
                parsed_tcontrol.settings.nsaos,
                parsed_tcontrol.spin_mode,
                geometry_hash,
            )

        connectivity = infer_connectivity(
            structure,
            self._covalent_radii_loader(),
        )
        return ProjectTaskRestartDraft(
            profile=restart_profile,
            source_project=project,
            source_step=step.kind,
            source_job_id=step.job_id or "",
            source_structure=structure,
            connectivity=connectivity,
            source_geometry_sha256=geometry_hash,
            optimization_settings=optimization,
            transport_settings=transport,
            tcontrol_settings=tcontrol_settings,
            tcontrol_self_energy_filename=tcontrol_self_energy,
            step4_execution_settings=execution,
            transport_evidence=evidence,
            surface_proposal=surface,
            electrode_provenance=electrode_provenance,
            profile_rebind_confirmed=profile_rebind_confirmed,
            source_terminal_confirmed=False,
        )


def _read_hashed(
    executor: RemoteExecutor,
    directory: PurePosixPath,
    filename: str,
    step: ProjectStepRecord,
) -> bytes:
    expected = dict(step.input_hashes).get(filename)
    if expected is None:
        raise ProjectTaskRestartError(
            f"Accepted SHA256 provenance is unavailable for {filename}."
        )
    data = executor.read_bytes(str(directory / filename))
    if hashlib.sha256(data).hexdigest() != expected:
        raise ProjectTaskRestartError(
            f"Remote {filename} no longer matches its accepted SHA256."
        )
    return data


def _read_hashed_allowing_submitted_tcontrol(
    executor: RemoteExecutor,
    directory: PurePosixPath,
    filename: str,
    step: ProjectStepRecord,
) -> bytes:
    expected = dict(step.input_hashes).get(filename)
    if expected is None:
        raise ProjectTaskRestartError(
            f"Accepted SHA256 provenance is unavailable for {filename}."
        )
    data = executor.read_bytes(str(directory / filename))
    if hashlib.sha256(data).hexdigest() == expected:
        return data
    submitted = submitted_tcontrol_bytes(data)
    if hashlib.sha256(submitted).hexdigest() != expected:
        raise ProjectTaskRestartError(
            f"Remote {filename} no longer matches its submitted SHA256."
        )
    return data


def _restart_electrode_provenance(
    project: CalculationProject,
    surface: ElectrodeSurfaceProposal,
) -> tuple[ProjectElectrodeClusterProvenance, ...]:
    records = (
        surface.left_provenance,
        surface.right_provenance,
    )
    if project.electrode_provenance and records != project.electrode_provenance:
        raise ProjectTaskRestartError(
            "Restart electrode identities differ from authoritative project metadata."
        )
    return records


def _project_step(
    project: CalculationProject,
    kind: ProjectStepKind,
) -> ProjectStepRecord:
    return next(step for step in project.steps if step.kind is kind)


def _active_step_kind(project: CalculationProject) -> ProjectStepKind:
    for step in reversed(project.steps):
        if step.state not in {
            ProjectStepState.NOT_STARTED,
            ProjectStepState.SKIPPED,
        }:
            return step.kind
    assert project.starting_step is not None
    return project.starting_step


def _validate_selected_root(profile: ServerProfile, project_path: str) -> None:
    if PurePosixPath(project_path).parent != PurePosixPath(
        profile.remote_project_root
    ):
        raise ProjectTaskRestartError(
            "Task restart is limited to first-level managed projects in the "
            "selected server workspace."
        )


def _close_without_replacing_outcome(executor: RemoteExecutor) -> None:
    try:
        executor.close()
    except Exception:
        pass
