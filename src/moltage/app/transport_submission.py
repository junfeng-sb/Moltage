"""Explicit Step-3 retry and Step-4 AITRANSS submission orchestration."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
from pathlib import PurePosixPath
import shlex
from uuid import UUID, uuid4

from moltage.aims.recovery import (
    parse_control_species_elements,
    parse_molecular_geometry,
)
from moltage.aims.transport_evidence import (
    ORBITAL_HEADER_MAX_BYTES,
    TransportCompletionEvidence,
    parse_aims_nsaos,
    parse_transport_spin_mode,
)
from moltage.aitranss.output import AitranssFailureCode
from moltage.aitranss.self_energy import (
    PartitionedSelfEnergyPlan,
    build_partitioned_self_energy_plan,
    render_self_energy,
    validate_self_energy_round_trip,
)
from moltage.aitranss.slurm import (
    AitranssExecutionSettings,
    render_aitranss_submit_script,
    render_step3_retry_script,
)
from moltage.aitranss.runtime import is_aitranss_executable_name
from moltage.aitranss.tcontrol import (
    TControlSettings,
    parse_tcontrol,
    render_tcontrol,
    render_tcontrol_replacing_self_energy,
    render_tcontrol_preserving_self_energy,
    render_tcontrol_with_self_energy,
)
from moltage.aitranss.transmission import submitted_tcontrol_bytes
from moltage.app.connection_service import ServerConnectionService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_submission import (
    ProjectStateRecordingError,
    ProjectSubmissionError,
    SbatchRejectedError,
    SubmissionOutcomeUnknown,
)
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectStepAttempt,
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
    remote_step_directory,
)
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    ServerProfile,
    SlurmExecutionPreset,
)
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_surface import (
    ElectrodeSurfaceProposal,
    resolve_project_electrode_surfaces,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteExecutor,
    RemoteExecutorError,
    RemotePathNotFoundError,
)
from moltage.remote.aitranss_discovery import (
    AitranssDiscoveryResult,
    discover_aitranss_executable,
)
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.slurm import (
    SlurmSubmissionError,
    build_sbatch_submission_command,
    parse_sbatch_parsable_output,
    parse_submit_script_output_filename,
)
from moltage.remote.slurm_discovery import resolve_slurm_for_submission
from moltage.remote.runtime_environment import (
    verify_aitranss_launch_for_submission,
    verify_configured_fhi_runtime,
)
from moltage.remote.step_inputs import (
    NewRemoteFileConflictError,
    StepInputChecksumError,
    StepInputTransferError,
    upload_new_files_atomically,
    replace_existing_file_atomically,
    verify_existing_file_sha256,
)


STEP3_RETRY_SCRIPT_PREFIX = "submit.retry"
STEP3_RETRY_OUTPUT_PREFIX = "aims.dft.retry"
STEP4_TCONTROL_FILENAME = "tcontrol"
STEP4_SCRIPT_FILENAME = "submit.aitranss.sh"
STEP4_OUTPUT_FILENAME = "aitranss.out"
STEP4_ATTEMPT01_TCONTROL_FILENAME = "tcontrol.attempt01"
STEP4_RETRY_SELF_ENERGY_FILENAME = "self.energy.retry02.in"
STEP4_RETRY_SCRIPT_FILENAME = "submit.aitranss.retry02.sh"
STEP4_RETRY_OUTPUT_FILENAME = "aitranss.retry02.out"
STEP4_ATTEMPT02_TCONTROL_FILENAME = "tcontrol.attempt02"
STEP4_RETRY03_SELF_ENERGY_FILENAME = "self.energy.retry03.in"
STEP4_RETRY03_SCRIPT_FILENAME = "submit.aitranss.retry03.sh"
STEP4_RETRY03_OUTPUT_FILENAME = "aitranss.retry03.out"
_UNKNOWN_SUBMISSION_MESSAGE = (
    "Submission outcome is unknown because the remote command result was lost."
)


class Step3RetryConflictError(ProjectSubmissionError):
    """Raised before sbatch when a Step-3 retry is no longer pristine."""


class Step4SubmissionConflictError(ProjectSubmissionError):
    """Raised before sbatch when Step-4 state/files are no longer pristine."""


class Step4ExplicitRetryConflictError(ProjectSubmissionError):
    """Raised before sbatch when the reviewed Step-4 retry is no longer safe."""


class Step4SettingsRetryConflictError(ProjectSubmissionError):
    """Raised before sbatch when a user-edited cancelled Step-4 retry is unsafe."""


class AitranssExecutableUnavailableError(ProjectSubmissionError):
    """Raised when no verified absolute AITRANSS executable is available."""


def _configured_aitranss_runtime(
    profile: ServerProfile,
) -> AitranssRuntimeConfiguration:
    runtime = profile.aitranss_runtime
    if runtime is None:
        raise AitranssExecutableUnavailableError(
            "AITRANSS runtime is not configured; run server runtime discovery "
            "in Cluster Execution Settings"
        )
    return runtime


def _runtime_for_verified_path(
    profile: ServerProfile,
    verified_path: str,
) -> AitranssRuntimeConfiguration:
    runtime = _configured_aitranss_runtime(profile)
    if runtime.executable_path != verified_path:
        raise AitranssExecutableUnavailableError(
            "verified AITRANSS path does not match the saved profile runtime; "
            "run server runtime discovery again"
        )
    return runtime


def _validate_verified_aitranss_path(profile: ServerProfile, path: str) -> None:
    runtime = profile.aitranss_runtime
    if runtime is not None and runtime.environment is not None:
        _runtime_for_verified_path(profile, path)
        return
    if (not isinstance(path, str) or not path.startswith("/")
            or not is_aitranss_executable_name(PurePosixPath(path).name)):
        raise AitranssExecutableUnavailableError(
            "AITRANSS executable is unavailable: a verified absolute "
            "AITRANSS executable path is required")


@dataclass(frozen=True, slots=True)
class Step3RetryRequest:
    profile: ServerProfile
    project: CalculationProject
    retry_preset: SlurmExecutionPreset
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("Step-3 retry requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("Step-3 retry requires a CalculationProject")
        if not isinstance(self.retry_preset, SlurmExecutionPreset):
            raise TypeError("Step-3 retry requires SlurmExecutionPreset resources")
        _validate_step3_retry_state(self.project)
        _validate_project_profile_scope(self.project, self.profile)
        _validate_password(self.supplied_password)
        saved = self.profile.execution_preset
        if saved is None:
            raise ValueError("saved Cluster Execution Settings are unavailable")
        fixed_fields = (
            "no_requeue",
            "export_none",
            "unset_slurm_export_env",
            "module_purge",
            "modules",
            "launch_command",
            "fhi_runtime",
            "slurm_command_mode",
            "slurm_bin_directory",
        )
        if any(
            getattr(self.retry_preset, name) != getattr(saved, name)
            for name in fixed_fields
        ):
            raise ValueError(
                "Step-3 retry may change resources only; environment and "
                "FHI-aims launch settings must remain unchanged"
            )


@dataclass(frozen=True, slots=True)
class Step4SubmissionRequest:
    profile: ServerProfile
    project: CalculationProject
    structure: MolecularStructure
    evidence: TransportCompletionEvidence
    tcontrol_settings: TControlSettings
    execution_settings: AitranssExecutionSettings
    verified_aitranss_path: str
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("Step-4 submission requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("Step-4 submission requires a CalculationProject")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("Step-4 submission requires recovered geometry.in")
        if not isinstance(self.evidence, TransportCompletionEvidence):
            raise TypeError("Step-4 submission requires validated Step-3 evidence")
        if not isinstance(self.tcontrol_settings, TControlSettings):
            raise TypeError("Step-4 submission requires TControlSettings")
        if not isinstance(self.execution_settings, AitranssExecutionSettings):
            raise TypeError("Step-4 submission requires execution settings")
        _validate_verified_aitranss_path(self.profile, self.verified_aitranss_path)
        _validate_step4_state(self.project)
        _validate_project_profile_scope(self.project, self.profile)
        _validate_password(self.supplied_password)
        # Complete local science validation before any connection/mutation.
        render_tcontrol(
            self.tcontrol_settings,
            self.structure,
            self.evidence.spin_mode,
        )


@dataclass(frozen=True, slots=True)
class Step4SettingsRetryRequest:
    """One explicit edited-tcontrol retry after a durable CANCELLED attempt."""

    profile: ServerProfile
    project: CalculationProject
    structure: MolecularStructure
    evidence: TransportCompletionEvidence
    tcontrol_settings: TControlSettings
    execution_settings: AitranssExecutionSettings
    self_energy_filename: str | None
    verified_aitranss_path: str
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("Step-4 settings retry requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("Step-4 settings retry requires a CalculationProject")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("Step-4 settings retry requires recovered geometry.in")
        if not isinstance(self.evidence, TransportCompletionEvidence):
            raise TypeError("Step-4 settings retry requires Step-3 evidence")
        if not isinstance(self.tcontrol_settings, TControlSettings):
            raise TypeError("Step-4 settings retry requires TControlSettings")
        if not isinstance(self.execution_settings, AitranssExecutionSettings):
            raise TypeError("Step-4 settings retry requires execution settings")
        _validate_verified_aitranss_path(self.profile, self.verified_aitranss_path)
        _validate_step4_settings_retry_state(self.project)
        _validate_project_profile_scope(self.project, self.profile)
        _validate_password(self.supplied_password)
        render_tcontrol_preserving_self_energy(
            self.tcontrol_settings,
            self.structure,
            self.evidence.spin_mode,
            self.self_energy_filename,
        )


@dataclass(frozen=True, slots=True)
class Step4ExplicitRetryRequest:
    """Fully validated local evidence for the one reviewed overlap retry."""

    profile: ServerProfile
    project: CalculationProject
    structure: MolecularStructure
    evidence: TransportCompletionEvidence
    surface_proposal: ElectrodeSurfaceProposal
    self_energy_plan: PartitionedSelfEnergyPlan
    attempt01_tcontrol: bytes = field(repr=False)
    execution_settings: AitranssExecutionSettings = field(
        default_factory=AitranssExecutionSettings
    )
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("Step-4 retry requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("Step-4 retry requires a CalculationProject")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("Step-4 retry requires recovered geometry.in")
        if not isinstance(self.evidence, TransportCompletionEvidence):
            raise TypeError("Step-4 retry requires validated Step-3 evidence")
        if not isinstance(self.surface_proposal, ElectrodeSurfaceProposal):
            raise TypeError("Step-4 retry requires Phase-2D electrode provenance")
        if not isinstance(self.self_energy_plan, PartitionedSelfEnergyPlan):
            raise TypeError("Step-4 retry requires a partitioned self-energy plan")
        if not isinstance(self.attempt01_tcontrol, bytes):
            raise TypeError("Step-4 retry requires exact attempt-1 tcontrol bytes")
        if not isinstance(self.execution_settings, AitranssExecutionSettings):
            raise TypeError("Step-4 retry requires AITRANSS execution settings")
        _validate_step4_explicit_retry_state(self.project)
        _validate_project_profile_scope(self.project, self.profile)
        _validate_password(self.supplied_password)
        if self.surface_proposal.structure != self.structure:
            raise ValueError("electrode provenance belongs to another geometry")
        parsed = parse_tcontrol(self.attempt01_tcontrol, self.structure)
        if parsed.self_energy_filename is not None:
            raise ValueError("attempt-1 tcontrol must use automatic interface detection")
        if parsed.settings.natoms != self.evidence.natoms:
            raise ValueError("attempt-1 tcontrol and Step-3 NATOMS disagree")
        if parsed.settings.nsaos != self.evidence.nsaos:
            raise ValueError("attempt-1 tcontrol and Step-3 NSAOS disagree")
        if parsed.spin_mode is not self.evidence.spin_mode:
            raise ValueError("attempt-1 tcontrol and Step-3 spin mode disagree")
        expected_plan = build_partitioned_self_energy_plan(
            self.structure,
            self.surface_proposal,
            parsed.settings,
        )
        if expected_plan != self.self_energy_plan:
            raise ValueError("self-energy plan is not the deterministic current plan")
        render_self_energy(self.self_energy_plan)
        render_tcontrol_with_self_energy(
            self.attempt01_tcontrol,
            STEP4_RETRY_SELF_ENERGY_FILENAME,
            self.structure,
        )


@dataclass(frozen=True, slots=True)
class Step4ReaderCompatibleRetryRequest:
    """Validated local evidence for the one authorized retry03 repair."""

    profile: ServerProfile
    project: CalculationProject
    structure: MolecularStructure
    evidence: TransportCompletionEvidence
    surface_proposal: ElectrodeSurfaceProposal
    self_energy_plan: PartitionedSelfEnergyPlan
    attempt02_tcontrol: bytes = field(repr=False)
    execution_settings: AitranssExecutionSettings = field(
        default_factory=AitranssExecutionSettings
    )
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("Step-4 retry03 requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("Step-4 retry03 requires a CalculationProject")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("Step-4 retry03 requires recovered geometry.in")
        if not isinstance(self.evidence, TransportCompletionEvidence):
            raise TypeError("Step-4 retry03 requires validated Step-3 evidence")
        if not isinstance(self.surface_proposal, ElectrodeSurfaceProposal):
            raise TypeError("Step-4 retry03 requires Phase-2D electrode provenance")
        if not isinstance(self.self_energy_plan, PartitionedSelfEnergyPlan):
            raise TypeError("Step-4 retry03 requires a partitioned self-energy plan")
        if not isinstance(self.attempt02_tcontrol, bytes):
            raise TypeError("Step-4 retry03 requires exact attempt-2 tcontrol bytes")
        if not isinstance(self.execution_settings, AitranssExecutionSettings):
            raise TypeError("Step-4 retry03 requires AITRANSS execution settings")
        _validate_step4_reader_compatible_retry_state(self.project)
        _validate_project_profile_scope(self.project, self.profile)
        _validate_password(self.supplied_password)
        if self.surface_proposal.structure != self.structure:
            raise ValueError("electrode provenance belongs to another geometry")
        parsed = parse_tcontrol(self.attempt02_tcontrol, self.structure)
        if parsed.self_energy_filename != STEP4_RETRY_SELF_ENERGY_FILENAME:
            raise ValueError(
                "attempt-2 tcontrol must reference self.energy.retry02.in"
            )
        if parsed.settings.natoms != self.evidence.natoms:
            raise ValueError("attempt-2 tcontrol and Step-3 NATOMS disagree")
        if parsed.settings.nsaos != self.evidence.nsaos:
            raise ValueError("attempt-2 tcontrol and Step-3 NSAOS disagree")
        if parsed.spin_mode is not self.evidence.spin_mode:
            raise ValueError("attempt-2 tcontrol and Step-3 spin mode disagree")
        expected_plan = build_partitioned_self_energy_plan(
            self.structure,
            self.surface_proposal,
            parsed.settings,
        )
        if expected_plan != self.self_energy_plan:
            raise ValueError("self-energy plan is not the deterministic current plan")
        rendered_self_energy = render_self_energy(self.self_energy_plan)
        validate_self_energy_round_trip(rendered_self_energy, self.self_energy_plan)
        render_tcontrol_replacing_self_energy(
            self.attempt02_tcontrol,
            STEP4_RETRY_SELF_ENERGY_FILENAME,
            STEP4_RETRY03_SELF_ENERGY_FILENAME,
            self.structure,
        )


@dataclass(frozen=True, slots=True)
class TransportSubmissionResult:
    project: CalculationProject
    step: ProjectStepRecord
    remote_step_directory: str
    job_id: str
    cluster_name: str | None
    uploaded_hashes: tuple[tuple[str, str], ...]


class TransportWorkflowSubmissionService:
    """One short-lived connection per explicit retry or Step-4 submission."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        temporary_id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._now_factory = now_factory
        self._temporary_id_factory = temporary_id_factory

    def preflight_aitranss(
        self,
        profile: ServerProfile,
        supplied_password: str | None = None,
    ) -> AitranssDiscoveryResult:
        """Perform one read-only configured-environment executable lookup."""

        if not isinstance(profile, ServerProfile):
            raise TypeError("AITRANSS preflight requires a ServerProfile")
        preset = profile.execution_preset
        if preset is None:
            raise AitranssExecutableUnavailableError(
                "Cluster Execution Settings are unavailable"
            )
        runtime = _configured_aitranss_runtime(profile)
        executor = self._connection_service.connect_for_remote_operation(
            profile,
            supplied_password,
        )
        try:
            return discover_aitranss_executable(executor, runtime)
        finally:
            executor.close()

    def retry_step3(
        self,
        request: Step3RetryRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> TransportSubmissionResult:
        """Submit one resource-only retry without rewriting scientific inputs."""

        if not isinstance(request, Step3RetryRequest):
            raise TypeError("retry_step3 requires Step3RetryRequest")
        scheduler_name = scheduler_display_name(request.retry_preset.scheduler_kind)
        submit_command_name = (
            "bsub"
            if request.retry_preset.scheduler_kind is SchedulerKind.LSF
            else "sbatch"
        )
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            slurm = resolve_slurm_for_submission(executor, request.retry_preset)
            verify_configured_fhi_runtime(executor, request.retry_preset)
            repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            project = self._load_matching_project(repository, request.project)
            _validate_step3_retry_state(project)
            step = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
            directory = remote_step_directory(project, step.kind)
            accepted_hashes = dict(step.input_hashes)
            report("Verifying unchanged Step-3 scientific inputs...")
            for filename in ("geometry.in", "control.in"):
                expected = accepted_hashes.get(filename)
                if expected is None:
                    raise Step3RetryConflictError(
                        f"accepted Step-3 SHA256 is unavailable for {filename}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step.kind,
                    )
                try:
                    verify_existing_file_sha256(
                        executor,
                        directory,
                        filename,
                        expected,
                    )
                except (StepInputTransferError, StepInputChecksumError) as error:
                    raise Step3RetryConflictError(
                        f"Step-3 retry input conflict: {error}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step.kind,
                    ) from None

            attempt_number = len(step.attempts) + 2
            script_filename = f"{STEP3_RETRY_SCRIPT_PREFIX}{attempt_number:02d}.sh"
            output_filename = f"{STEP3_RETRY_OUTPUT_PREFIX}{attempt_number:02d}.out"
            script = render_step3_retry_script(
                request.retry_preset,
                project.project_id,
                output_filename,
                mail_settings=request.profile.slurm_mail_settings,
            ).encode("utf-8")
            prior_attempt = _archive_current_attempt(executor, directory, step)
            try:
                _require_absent(executor, directory, output_filename)
            except NewRemoteFileConflictError as error:
                raise Step3RetryConflictError(
                    f"Step-3 retry file conflict: {error}",
                    remote_project_path=project.remote_project_path,
                    step_kind=step.kind,
                ) from None
            report("Uploading a new Step-3 retry script...")
            try:
                retry_hashes = upload_new_files_atomically(
                    executor,
                    directory,
                    {script_filename: script},
                    temporary_id_factory=self._temporary_id_factory,
                )
            except NewRemoteFileConflictError as error:
                raise Step3RetryConflictError(
                    f"Step-3 retry file conflict: {error}",
                    remote_project_path=project.remote_project_path,
                    step_kind=step.kind,
                ) from None

            command = build_sbatch_submission_command(
                directory,
                slurm.sbatch_path,
                script_filename,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
            report(f"Submitting one Step-3 retry to {scheduler_name}...")
            submitted_at = _aware_now(self._now_factory)
            try:
                result = executor.execute(command)
            except RemoteCommandOutcomeUnknown:
                unknown = replace(
                    step,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=_merge_hashes(step.input_hashes, retry_hashes),
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=request.retry_preset.scheduler_kind,
                    submit_script_filename=script_filename,
                    slurm_output_filename=output_filename,
                    attempts=step.attempts + (prior_attempt,),
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" Inspect {scheduler_name} before any further action.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step.kind,
                ) from None
            except RemoteExecutorError:
                raise
            if result.exit_status != 0:
                raise SbatchRejectedError(
                    f"{scheduler_name} rejected the Step-3 retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step.kind,
                )
            try:
                receipt = parse_sbatch_parsable_output(result.stdout)
            except SlurmSubmissionError:
                unknown = replace(
                    step,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=_merge_hashes(step.input_hashes, retry_hashes),
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=request.retry_preset.scheduler_kind,
                    submit_script_filename=script_filename,
                    slurm_output_filename=output_filename,
                    attempts=step.attempts + (prior_attempt,),
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" {submit_command_name} returned an unsupported receipt; "
                    f"inspect {scheduler_name}.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step.kind,
                ) from None
            queued = replace(
                step,
                state=ProjectStepState.QUEUED,
                job_id=receipt.job_id,
                cluster_name=receipt.cluster_name,
                submitted_at=submitted_at,
                started_at=None,
                finished_at=None,
                input_hashes=_merge_hashes(step.input_hashes, retry_hashes),
                last_error=None,
                scheduler_state=None,
                scheduler_kind=request.retry_preset.scheduler_kind,
                submit_script_filename=script_filename,
                slurm_output_filename=output_filename,
                attempts=step.attempts + (prior_attempt,),
            )
            updated = self._persist_and_mark(
                repository,
                project,
                queued,
                request.profile,
            )
            return TransportSubmissionResult(
                updated,
                _project_step(updated, step.kind),
                directory,
                receipt.job_id,
                receipt.cluster_name,
                retry_hashes,
            )
        finally:
            executor.close()

    def submit_step4(
        self,
        request: Step4SubmissionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> TransportSubmissionResult:
        """Upload only tcontrol/script and dispatch Step 4 at most once."""

        if not isinstance(request, Step4SubmissionRequest):
            raise TypeError("submit_step4 requires Step4SubmissionRequest")
        preset = request.profile.execution_preset
        if preset is None:
            raise ProjectSubmissionError("Cluster Execution Settings are unavailable")
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        submit_command_name = (
            "bsub" if preset.scheduler_kind is SchedulerKind.LSF else "sbatch"
        )
        runtime = _runtime_for_verified_path(
            request.profile,
            request.verified_aitranss_path,
        )
        tcontrol = render_tcontrol(
            request.tcontrol_settings,
            request.structure,
            request.evidence.spin_mode,
        ).encode("utf-8")
        script = render_aitranss_submit_script(
            profile_preset=preset,
            settings=request.execution_settings,
            project_id=request.project.project_id,
            executable_path=request.verified_aitranss_path,
            aitranss_modules=runtime.modules,
            runtime=runtime,
            output_filename=STEP4_OUTPUT_FILENAME,
            mail_settings=request.profile.slurm_mail_settings,
        ).encode("utf-8")
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            slurm = resolve_slurm_for_submission(executor, preset)
            verify_aitranss_launch_for_submission(executor, preset)
            repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            project = self._load_matching_project(repository, request.project)
            _validate_step4_state(project)
            step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
            step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
            directory = remote_step_directory(project, step4.kind)
            report("Revalidating Step-3 files for Step 4...")
            executable_check = executor.execute(
                "test -f "
                + shlex.quote(request.verified_aitranss_path)
                + " && test -x "
                + shlex.quote(request.verified_aitranss_path)
            )
            if executable_check.exit_status != 0:
                raise AitranssExecutableUnavailableError(
                    "the previously resolved AITRANSS path is no longer a regular "
                    "executable file",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            required = ["geometry.in", "basis-indices.out", "omat.aims"]
            required.extend(request.evidence.orbital_filenames)
            for filename in required:
                _require_nonempty(executor, directory, filename)
            geometry = executor.read_bytes(
                str(PurePosixPath(directory) / "geometry.in")
            )
            actual_geometry_hash = hashlib.sha256(geometry).hexdigest()
            accepted_geometry_hash = dict(step3.input_hashes).get("geometry.in")
            if actual_geometry_hash != request.evidence.geometry_sha256 or (
                accepted_geometry_hash is not None
                and actual_geometry_hash != accepted_geometry_hash
            ):
                raise Step4SubmissionConflictError(
                    "Step-3 geometry.in changed before Step-4 submission",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            report("Uploading tcontrol and the distinct Step-4 script...")
            try:
                hashes = upload_new_files_atomically(
                    executor,
                    directory,
                    {
                        STEP4_TCONTROL_FILENAME: tcontrol,
                        STEP4_SCRIPT_FILENAME: script,
                    },
                    temporary_id_factory=self._temporary_id_factory,
                )
            except NewRemoteFileConflictError as error:
                raise Step4SubmissionConflictError(
                    f"Step-4 file conflict: {error}",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None

            command = build_sbatch_submission_command(
                directory,
                slurm.sbatch_path,
                STEP4_SCRIPT_FILENAME,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
            report(f"Submitting one AITRANSS process to {scheduler_name}...")
            submitted_at = _aware_now(self._now_factory)
            try:
                result = executor.execute(command)
            except RemoteCommandOutcomeUnknown:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    submitted_at=submitted_at,
                    input_hashes=hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_OUTPUT_FILENAME,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" Inspect {scheduler_name} before any further action.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            except RemoteExecutorError:
                raise
            if result.exit_status != 0:
                raise SbatchRejectedError(
                    f"{scheduler_name} rejected the Step-4 submission",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                receipt = parse_sbatch_parsable_output(result.stdout)
            except SlurmSubmissionError:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    submitted_at=submitted_at,
                    input_hashes=hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_OUTPUT_FILENAME,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" {submit_command_name} returned an unsupported receipt; "
                    f"inspect {scheduler_name}.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            queued = replace(
                step4,
                state=ProjectStepState.QUEUED,
                job_id=receipt.job_id,
                cluster_name=receipt.cluster_name,
                submitted_at=submitted_at,
                input_hashes=hashes,
                last_error=None,
                scheduler_kind=preset.scheduler_kind,
                submit_script_filename=STEP4_SCRIPT_FILENAME,
                slurm_output_filename=STEP4_OUTPUT_FILENAME,
            )
            updated = self._persist_and_mark(
                repository,
                project,
                queued,
                request.profile,
            )
            return TransportSubmissionResult(
                updated,
                _project_step(updated, step4.kind),
                directory,
                receipt.job_id,
                receipt.cluster_name,
                hashes,
            )
        finally:
            executor.close()

    def retry_step4_with_explicit_self_energy(
        self,
        request: Step4ExplicitRetryRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> TransportSubmissionResult:
        """Perform the one reviewed overlap retry with at-most-once dispatch."""

        if not isinstance(request, Step4ExplicitRetryRequest):
            raise TypeError(
                "retry_step4_with_explicit_self_energy requires "
                "Step4ExplicitRetryRequest"
            )
        preset = request.profile.execution_preset
        if preset is None:
            raise Step4ExplicitRetryConflictError(
                "Cluster Execution Settings are unavailable"
            )
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        submit_command_name = (
            "bsub" if preset.scheduler_kind is SchedulerKind.LSF else "sbatch"
        )
        runtime = _configured_aitranss_runtime(request.profile)
        retry_tcontrol = render_tcontrol_with_self_energy(
            request.attempt01_tcontrol,
            STEP4_RETRY_SELF_ENERGY_FILENAME,
            request.structure,
        )
        self_energy = render_self_energy(request.self_energy_plan)
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            # All remote discovery and scientific checks precede the first write.
            slurm = resolve_slurm_for_submission(executor, preset)
            verify_aitranss_launch_for_submission(executor, preset)
            aitranss = discover_aitranss_executable(executor, runtime)
            repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            project = self._load_matching_project(repository, request.project)
            _validate_step4_explicit_retry_state(project)
            step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
            step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
            directory = remote_step_directory(project, step4.kind)
            report("Revalidating frozen Step-3 and failed attempt-1 evidence...")

            required = (
                "geometry.in",
                "control.in",
                "basis-indices.out",
                "omat.aims",
                *request.evidence.orbital_filenames,
                STEP4_SCRIPT_FILENAME,
                STEP4_OUTPUT_FILENAME,
                STEP4_TCONTROL_FILENAME,
            )
            for filename in required:
                _require_nonempty(executor, directory, filename)

            geometry = executor.read_bytes(
                str(PurePosixPath(directory) / "geometry.in")
            )
            control = executor.read_bytes(
                str(PurePosixPath(directory) / "control.in")
            )
            accepted_step3 = dict(step3.input_hashes)
            for filename, data in (("geometry.in", geometry), ("control.in", control)):
                expected = accepted_step3.get(filename)
                if expected is None or hashlib.sha256(data).hexdigest() != expected:
                    raise Step4ExplicitRetryConflictError(
                        f"accepted Step-3 SHA256 is unavailable or changed for {filename}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    )
            if hashlib.sha256(geometry).hexdigest() != request.evidence.geometry_sha256:
                raise Step4ExplicitRetryConflictError(
                    "Step-3 geometry evidence changed before Step-4 retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                remote_structure = parse_molecular_geometry(
                    geometry,
                    parse_control_species_elements(control),
                    source_name="geometry.in",
                )
            except ValueError as error:
                raise Step4ExplicitRetryConflictError(
                    f"Step-3 geometry/control validation failed: {error}",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            if remote_structure.atoms != request.structure.atoms:
                raise Step4ExplicitRetryConflictError(
                    "recovered Step-3 geometry differs from retry preparation",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            if parse_transport_spin_mode(control) is not request.evidence.spin_mode:
                raise Step4ExplicitRetryConflictError(
                    "Step-3 spin evidence changed before retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            for filename in request.evidence.orbital_filenames:
                nsaos = parse_aims_nsaos(
                    executor.read_file_head(
                        str(PurePosixPath(directory) / filename),
                        ORBITAL_HEADER_MAX_BYTES,
                    ),
                    source_name=filename,
                )
                if nsaos != request.evidence.nsaos:
                    raise Step4ExplicitRetryConflictError(
                        f"{filename} NSAOS changed before retry",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    )

            accepted_step4 = dict(step4.input_hashes)
            attempt_tcontrol_digest = accepted_step4.get(STEP4_TCONTROL_FILENAME)
            attempt_script_digest = accepted_step4.get(STEP4_SCRIPT_FILENAME)
            if attempt_tcontrol_digest is None or attempt_script_digest is None:
                raise Step4ExplicitRetryConflictError(
                    "attempt-1 tcontrol/script SHA256 provenance is unavailable",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            active_tcontrol = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_TCONTROL_FILENAME)
            )
            attempt_script = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_SCRIPT_FILENAME)
            )
            if (
                active_tcontrol != request.attempt01_tcontrol
                or hashlib.sha256(active_tcontrol).hexdigest()
                != attempt_tcontrol_digest
            ):
                raise Step4ExplicitRetryConflictError(
                    "attempt-1 active tcontrol changed before preservation",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            if hashlib.sha256(attempt_script).hexdigest() != attempt_script_digest:
                raise Step4ExplicitRetryConflictError(
                    "attempt-1 submit script changed",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            parsed_tcontrol = parse_tcontrol(active_tcontrol, remote_structure)
            if (
                parsed_tcontrol.self_energy_filename is not None
                or parsed_tcontrol.settings.output_filename != "TE.dat"
                or parsed_tcontrol.settings.natoms != request.evidence.natoms
                or parsed_tcontrol.settings.nsaos != request.evidence.nsaos
                or parsed_tcontrol.spin_mode is not request.evidence.spin_mode
            ):
                raise Step4ExplicitRetryConflictError(
                    "attempt-1 tcontrol science no longer matches Step-3 evidence",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            remote_surface = resolve_project_electrode_surfaces(
                project,
                remote_structure,
            )
            remote_plan = build_partitioned_self_energy_plan(
                remote_structure,
                remote_surface,
                parsed_tcontrol.settings,
            )
            if (
                remote_surface.left_local_to_global_zero_based
                != request.surface_proposal.left_local_to_global_zero_based
                or remote_surface.right_local_to_global_zero_based
                != request.surface_proposal.right_local_to_global_zero_based
                or _plan_signature(remote_plan) != _plan_signature(request.self_energy_plan)
            ):
                raise Step4ExplicitRetryConflictError(
                    "remote electrode provenance/membership differs from reviewed retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )

            for filename in (
                "TE.dat",
                STEP4_ATTEMPT01_TCONTROL_FILENAME,
                STEP4_RETRY_SELF_ENERGY_FILENAME,
                STEP4_RETRY_SCRIPT_FILENAME,
                STEP4_RETRY_OUTPUT_FILENAME,
            ):
                try:
                    _require_absent(executor, directory, filename)
                except NewRemoteFileConflictError as error:
                    raise Step4ExplicitRetryConflictError(
                        f"Step-4 retry file conflict: {error}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    ) from None

            retry_script = render_aitranss_submit_script(
                profile_preset=preset,
                settings=request.execution_settings,
                project_id=project.project_id,
                executable_path=aitranss.executable_path,
                aitranss_modules=runtime.modules,
                runtime=runtime,
                output_filename=STEP4_RETRY_OUTPUT_FILENAME,
                mail_settings=request.profile.slurm_mail_settings,
            ).encode("utf-8")
            prior_attempt = ProjectStepAttempt(
                job_id=step4.job_id or "",
                submitted_at=step4.submitted_at,
                finished_at=step4.finished_at,
                terminal_scheduler_state=step4.scheduler_state,
                failure_reason=step4.last_error,
                submit_script_filename=STEP4_SCRIPT_FILENAME,
                slurm_output_filename=STEP4_OUTPUT_FILENAME,
                input_hashes=(
                    (STEP4_ATTEMPT01_TCONTROL_FILENAME, attempt_tcontrol_digest),
                    (STEP4_SCRIPT_FILENAME, attempt_script_digest),
                ),
                scheduler_kind=step4.scheduler_kind or SchedulerKind.SLURM,
            )

            report("Preserving attempt-1 tcontrol and installing explicit self-energy...")
            archive_hashes = upload_new_files_atomically(
                executor,
                directory,
                {STEP4_ATTEMPT01_TCONTROL_FILENAME: active_tcontrol},
                temporary_id_factory=self._temporary_id_factory,
            )
            if dict(archive_hashes)[STEP4_ATTEMPT01_TCONTROL_FILENAME] != attempt_tcontrol_digest:
                raise Step4ExplicitRetryConflictError(
                    "historical tcontrol SHA256 changed during preservation"
                )
            self_energy_hashes = upload_new_files_atomically(
                executor,
                directory,
                {STEP4_RETRY_SELF_ENERGY_FILENAME: self_energy},
                temporary_id_factory=self._temporary_id_factory,
            )
            retry_tcontrol_digest = replace_existing_file_atomically(
                executor,
                directory,
                STEP4_TCONTROL_FILENAME,
                retry_tcontrol,
                expected_existing_digest=attempt_tcontrol_digest,
                temporary_id_factory=self._temporary_id_factory,
            )
            script_hashes = upload_new_files_atomically(
                executor,
                directory,
                {STEP4_RETRY_SCRIPT_FILENAME: retry_script},
                temporary_id_factory=self._temporary_id_factory,
            )
            retry_hashes = tuple(
                {
                    STEP4_TCONTROL_FILENAME: retry_tcontrol_digest,
                    **dict(self_energy_hashes),
                    **dict(script_hashes),
                }.items()
            )

            command = build_sbatch_submission_command(
                directory,
                slurm.sbatch_path,
                STEP4_RETRY_SCRIPT_FILENAME,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
            report("Submitting exactly one explicit-self-energy Step-4 retry...")
            submitted_at = _aware_now(self._now_factory)
            attempts = step4.attempts + (prior_attempt,)
            try:
                result = executor.execute(command)
            except RemoteCommandOutcomeUnknown:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=retry_hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_RETRY_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_RETRY_OUTPUT_FILENAME,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" Inspect {scheduler_name} before any further action; "
                    "never retry automatically.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            except RemoteExecutorError:
                raise
            if result.exit_status != 0:
                rejected = replace(
                    step4,
                    state=ProjectStepState.FAILED,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=submitted_at,
                    input_hashes=retry_hashes,
                    last_error="SUBMISSION_REJECTED",
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_RETRY_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_RETRY_OUTPUT_FILENAME,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, rejected, request.profile)
                raise SbatchRejectedError(
                    f"{scheduler_name} rejected the explicit Step-4 retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                receipt = parse_sbatch_parsable_output(result.stdout)
            except SlurmSubmissionError:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=retry_hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_RETRY_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_RETRY_OUTPUT_FILENAME,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" {submit_command_name} returned an unsupported receipt; "
                    f"inspect {scheduler_name}.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            queued = replace(
                step4,
                state=ProjectStepState.QUEUED,
                job_id=receipt.job_id,
                cluster_name=receipt.cluster_name,
                submitted_at=submitted_at,
                started_at=None,
                finished_at=None,
                input_hashes=retry_hashes,
                last_error=None,
                scheduler_state=None,
                scheduler_kind=preset.scheduler_kind,
                submit_script_filename=STEP4_RETRY_SCRIPT_FILENAME,
                slurm_output_filename=STEP4_RETRY_OUTPUT_FILENAME,
                attempts=attempts,
            )
            updated = self._persist_and_mark(
                repository,
                project,
                queued,
                request.profile,
            )
            return TransportSubmissionResult(
                updated,
                _project_step(updated, step4.kind),
                directory,
                receipt.job_id,
                receipt.cluster_name,
                retry_hashes,
            )
        finally:
            executor.close()

    def retry_cancelled_step4_with_settings(
        self,
        request: Step4SettingsRetryRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> TransportSubmissionResult:
        """Preserve one cancelled attempt and submit edited tcontrol exactly once."""

        if not isinstance(request, Step4SettingsRetryRequest):
            raise TypeError(
                "retry_cancelled_step4_with_settings requires "
                "Step4SettingsRetryRequest"
            )
        preset = request.profile.execution_preset
        if preset is None:
            raise Step4SettingsRetryConflictError(
                "Cluster Execution Settings are unavailable"
            )
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        submit_command_name = (
            "bsub" if preset.scheduler_kind is SchedulerKind.LSF else "sbatch"
        )
        runtime = _runtime_for_verified_path(
            request.profile,
            request.verified_aitranss_path,
        )
        retry_tcontrol = render_tcontrol_preserving_self_energy(
            request.tcontrol_settings,
            request.structure,
            request.evidence.spin_mode,
            request.self_energy_filename,
        ).encode("utf-8")
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            slurm = resolve_slurm_for_submission(executor, preset)
            verify_aitranss_launch_for_submission(executor, preset)
            repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            project = self._load_matching_project(repository, request.project)
            _validate_step4_settings_retry_state(project)
            step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
            step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
            directory = remote_step_directory(project, step4.kind)
            report("Revalidating unchanged Step-3 and current Step-4 inputs...")

            executable_check = executor.execute(
                "test -f "
                + shlex.quote(request.verified_aitranss_path)
                + " && test -x "
                + shlex.quote(request.verified_aitranss_path)
            )
            if executable_check.exit_status != 0:
                raise AitranssExecutableUnavailableError(
                    "the verified AITRANSS path is no longer executable",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            geometry = executor.read_bytes(
                str(PurePosixPath(directory) / "geometry.in")
            )
            control = executor.read_bytes(
                str(PurePosixPath(directory) / "control.in")
            )
            accepted_step3 = dict(step3.input_hashes)
            for filename, data in (("geometry.in", geometry), ("control.in", control)):
                expected = accepted_step3.get(filename)
                if expected is None or hashlib.sha256(data).hexdigest() != expected:
                    raise Step4SettingsRetryConflictError(
                        f"accepted Step-3 SHA256 changed for {filename}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    )
            if hashlib.sha256(geometry).hexdigest() != request.evidence.geometry_sha256:
                raise Step4SettingsRetryConflictError(
                    "Step-3 geometry differs from the restart draft source",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            remote_structure = parse_molecular_geometry(
                geometry,
                parse_control_species_elements(control),
                source_name="geometry.in",
            )
            if remote_structure.atoms != request.structure.atoms:
                raise Step4SettingsRetryConflictError(
                    "Step-4 retry cannot change geometry; restart Step 3 instead",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            if parse_transport_spin_mode(control) is not request.evidence.spin_mode:
                raise Step4SettingsRetryConflictError(
                    "Step-3 spin evidence changed before retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )

            current_script_name = step4.submit_script_filename
            current_output_name = step4.slurm_output_filename
            if current_script_name is None or current_output_name is None:
                raise Step4SettingsRetryConflictError(
                    "current Step-4 attempt filenames are unavailable"
                )
            accepted_step4 = dict(step4.input_hashes)
            current_tcontrol = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_TCONTROL_FILENAME)
            )
            submitted_current = submitted_tcontrol_bytes(current_tcontrol)
            current_tcontrol_digest = accepted_step4.get(STEP4_TCONTROL_FILENAME)
            current_script_digest = accepted_step4.get(current_script_name)
            current_script = executor.read_bytes(
                str(PurePosixPath(directory) / current_script_name)
            )
            if (
                current_tcontrol_digest is None
                or hashlib.sha256(submitted_current).hexdigest()
                != current_tcontrol_digest
                or current_script_digest is None
                or hashlib.sha256(current_script).hexdigest()
                != current_script_digest
            ):
                raise Step4SettingsRetryConflictError(
                    "current Step-4 tcontrol/script provenance changed"
                )
            parsed_current = parse_tcontrol(submitted_current, remote_structure)
            if parsed_current.self_energy_filename != request.self_energy_filename:
                raise Step4SettingsRetryConflictError(
                    "current self-energy input changed before retry"
                )
            retained_hashes: dict[str, str] = {}
            if request.self_energy_filename is not None:
                expected_self_energy = accepted_step4.get(
                    request.self_energy_filename
                )
                if expected_self_energy is None:
                    raise Step4SettingsRetryConflictError(
                        "current self-energy SHA256 provenance is unavailable"
                    )
                self_energy = executor.read_bytes(
                    str(PurePosixPath(directory) / request.self_energy_filename)
                )
                if hashlib.sha256(self_energy).hexdigest() != expected_self_energy:
                    raise Step4SettingsRetryConflictError(
                        "current self-energy file changed before retry"
                    )
                retained_hashes[request.self_energy_filename] = expected_self_energy

            current_attempt_number = len(step4.attempts) + 1
            retry_attempt_number = current_attempt_number + 1
            archive_tcontrol_name = (
                f"tcontrol.attempt{current_attempt_number:02d}"
            )
            retry_script_name = (
                f"submit.aitranss.retry{retry_attempt_number:02d}.sh"
            )
            retry_output_name = f"aitranss.retry{retry_attempt_number:02d}.out"
            for filename in (
                archive_tcontrol_name,
                retry_script_name,
                retry_output_name,
            ):
                _require_absent(executor, directory, filename)

            retry_script = render_aitranss_submit_script(
                profile_preset=preset,
                settings=request.execution_settings,
                project_id=project.project_id,
                executable_path=request.verified_aitranss_path,
                aitranss_modules=runtime.modules,
                runtime=runtime,
                output_filename=retry_output_name,
                mail_settings=request.profile.slurm_mail_settings,
            ).encode("utf-8")
            prior_attempt = ProjectStepAttempt(
                job_id=step4.job_id or "",
                submitted_at=step4.submitted_at,
                finished_at=step4.finished_at,
                terminal_scheduler_state=step4.scheduler_state,
                failure_reason=step4.last_error,
                submit_script_filename=current_script_name,
                slurm_output_filename=current_output_name,
                input_hashes=(
                    (archive_tcontrol_name, current_tcontrol_digest),
                    (current_script_name, current_script_digest),
                    *tuple(retained_hashes.items()),
                ),
                scheduler_kind=step4.scheduler_kind or SchedulerKind.SLURM,
            )
            report("Preserving the cancelled Step-4 attempt inputs...")
            archive_hashes = upload_new_files_atomically(
                executor,
                directory,
                {archive_tcontrol_name: submitted_current},
                temporary_id_factory=self._temporary_id_factory,
            )
            if dict(archive_hashes)[archive_tcontrol_name] != current_tcontrol_digest:
                raise Step4SettingsRetryConflictError(
                    "historical tcontrol SHA256 changed during preservation"
                )
            retry_tcontrol_digest = replace_existing_file_atomically(
                executor,
                directory,
                STEP4_TCONTROL_FILENAME,
                retry_tcontrol,
                expected_existing_digest=hashlib.sha256(current_tcontrol).hexdigest(),
                temporary_id_factory=self._temporary_id_factory,
            )
            script_hashes = upload_new_files_atomically(
                executor,
                directory,
                {retry_script_name: retry_script},
                temporary_id_factory=self._temporary_id_factory,
            )
            retry_hashes = tuple(
                {
                    STEP4_TCONTROL_FILENAME: retry_tcontrol_digest,
                    **retained_hashes,
                    **dict(script_hashes),
                }.items()
            )

            command = build_sbatch_submission_command(
                directory,
                slurm.sbatch_path,
                retry_script_name,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
            submitted_at = _aware_now(self._now_factory)
            attempts = step4.attempts + (prior_attempt,)
            report("Submitting exactly one edited Step-4 retry...")
            try:
                result = executor.execute(command)
            except RemoteCommandOutcomeUnknown:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=retry_hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=retry_script_name,
                    slurm_output_filename=retry_output_name,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" Inspect {scheduler_name} before any further action; "
                    "never retry automatically.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            if result.exit_status != 0:
                rejected = replace(
                    step4,
                    state=ProjectStepState.FAILED,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=submitted_at,
                    input_hashes=retry_hashes,
                    last_error="SUBMISSION_REJECTED",
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=retry_script_name,
                    slurm_output_filename=retry_output_name,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, rejected, request.profile)
                raise SbatchRejectedError(
                    f"{scheduler_name} rejected the edited Step-4 retry",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                receipt = parse_sbatch_parsable_output(result.stdout)
            except SlurmSubmissionError:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=retry_hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=retry_script_name,
                    slurm_output_filename=retry_output_name,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" {submit_command_name} returned an unsupported receipt; "
                    f"inspect {scheduler_name}.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            queued = replace(
                step4,
                state=ProjectStepState.QUEUED,
                job_id=receipt.job_id,
                cluster_name=receipt.cluster_name,
                submitted_at=submitted_at,
                started_at=None,
                finished_at=None,
                input_hashes=retry_hashes,
                last_error=None,
                scheduler_state=None,
                scheduler_kind=preset.scheduler_kind,
                submit_script_filename=retry_script_name,
                slurm_output_filename=retry_output_name,
                attempts=attempts,
            )
            updated = self._persist_and_mark(
                repository,
                project,
                queued,
                request.profile,
            )
            return TransportSubmissionResult(
                updated,
                _project_step(updated, step4.kind),
                directory,
                receipt.job_id,
                receipt.cluster_name,
                retry_hashes,
            )
        finally:
            executor.close()

    def retry_step4_with_reader_compatible_self_energy(
        self,
        request: Step4ReaderCompatibleRetryRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> TransportSubmissionResult:
        """Submit the one authorized retry03 after exact retry02 validation."""

        if not isinstance(request, Step4ReaderCompatibleRetryRequest):
            raise TypeError(
                "retry_step4_with_reader_compatible_self_energy requires "
                "Step4ReaderCompatibleRetryRequest"
            )
        preset = request.profile.execution_preset
        if preset is None:
            raise Step4ExplicitRetryConflictError(
                "Cluster Execution Settings are unavailable"
            )
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        submit_command_name = (
            "bsub" if preset.scheduler_kind is SchedulerKind.LSF else "sbatch"
        )
        runtime = _configured_aitranss_runtime(request.profile)
        retry_tcontrol = render_tcontrol_replacing_self_energy(
            request.attempt02_tcontrol,
            STEP4_RETRY_SELF_ENERGY_FILENAME,
            STEP4_RETRY03_SELF_ENERGY_FILENAME,
            request.structure,
        )
        self_energy = render_self_energy(request.self_energy_plan)
        validate_self_energy_round_trip(self_energy, request.self_energy_plan)
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            # Discovery and every authoritative retry02 check precede the first write.
            slurm = resolve_slurm_for_submission(executor, preset)
            verify_aitranss_launch_for_submission(executor, preset)
            aitranss = discover_aitranss_executable(executor, runtime)
            repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            project = self._load_matching_project(repository, request.project)
            _validate_step4_reader_compatible_retry_state(project)
            step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
            step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
            directory = remote_step_directory(project, step4.kind)
            report("Revalidating frozen Step-3 and failed retry02 evidence...")

            required = (
                "geometry.in",
                "control.in",
                "basis-indices.out",
                "omat.aims",
                *request.evidence.orbital_filenames,
                STEP4_ATTEMPT01_TCONTROL_FILENAME,
                STEP4_SCRIPT_FILENAME,
                STEP4_OUTPUT_FILENAME,
                STEP4_TCONTROL_FILENAME,
                STEP4_RETRY_SELF_ENERGY_FILENAME,
                STEP4_RETRY_SCRIPT_FILENAME,
                STEP4_RETRY_OUTPUT_FILENAME,
            )
            for filename in required:
                _require_nonempty(executor, directory, filename)

            geometry = executor.read_bytes(
                str(PurePosixPath(directory) / "geometry.in")
            )
            control = executor.read_bytes(
                str(PurePosixPath(directory) / "control.in")
            )
            accepted_step3 = dict(step3.input_hashes)
            for filename, data in (("geometry.in", geometry), ("control.in", control)):
                expected = accepted_step3.get(filename)
                if expected is None or hashlib.sha256(data).hexdigest() != expected:
                    raise Step4ExplicitRetryConflictError(
                        f"accepted Step-3 SHA256 is unavailable or changed for {filename}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    )
            if hashlib.sha256(geometry).hexdigest() != request.evidence.geometry_sha256:
                raise Step4ExplicitRetryConflictError(
                    "Step-3 geometry evidence changed before retry03",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                remote_structure = parse_molecular_geometry(
                    geometry,
                    parse_control_species_elements(control),
                    source_name="geometry.in",
                )
            except ValueError as error:
                raise Step4ExplicitRetryConflictError(
                    f"Step-3 geometry/control validation failed: {error}",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            if remote_structure.atoms != request.structure.atoms:
                raise Step4ExplicitRetryConflictError(
                    "recovered Step-3 geometry differs from retry03 preparation",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            if parse_transport_spin_mode(control) is not request.evidence.spin_mode:
                raise Step4ExplicitRetryConflictError(
                    "Step-3 spin evidence changed before retry03",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            for filename in request.evidence.orbital_filenames:
                nsaos = parse_aims_nsaos(
                    executor.read_file_head(
                        str(PurePosixPath(directory) / filename),
                        ORBITAL_HEADER_MAX_BYTES,
                    ),
                    source_name=filename,
                )
                if nsaos != request.evidence.nsaos:
                    raise Step4ExplicitRetryConflictError(
                        f"{filename} NSAOS changed before retry03",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    )

            accepted_step4 = dict(step4.input_hashes)
            attempt02_tcontrol_digest = accepted_step4.get(STEP4_TCONTROL_FILENAME)
            attempt02_self_energy_digest = accepted_step4.get(
                STEP4_RETRY_SELF_ENERGY_FILENAME
            )
            attempt02_script_digest = accepted_step4.get(
                STEP4_RETRY_SCRIPT_FILENAME
            )
            if any(
                digest is None
                for digest in (
                    attempt02_tcontrol_digest,
                    attempt02_self_energy_digest,
                    attempt02_script_digest,
                )
            ):
                raise Step4ExplicitRetryConflictError(
                    "retry02 tcontrol/self-energy/script SHA256 provenance is unavailable",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            active_tcontrol = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_TCONTROL_FILENAME)
            )
            attempt02_self_energy = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_RETRY_SELF_ENERGY_FILENAME)
            )
            attempt02_script = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_RETRY_SCRIPT_FILENAME)
            )
            if (
                active_tcontrol != request.attempt02_tcontrol
                or hashlib.sha256(active_tcontrol).hexdigest()
                != attempt02_tcontrol_digest
            ):
                raise Step4ExplicitRetryConflictError(
                    "retry02 active tcontrol changed before preservation",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            if (
                hashlib.sha256(attempt02_self_energy).hexdigest()
                != attempt02_self_energy_digest
            ):
                raise Step4ExplicitRetryConflictError(
                    "retry02 self-energy file changed",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            if hashlib.sha256(attempt02_script).hexdigest() != attempt02_script_digest:
                raise Step4ExplicitRetryConflictError(
                    "retry02 submit script changed",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                attempt02_output = parse_submit_script_output_filename(
                    attempt02_script
                )
            except SlurmSubmissionError as error:
                raise Step4ExplicitRetryConflictError(
                    f"retry02 submit script is invalid: {error}",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            if attempt02_output != STEP4_RETRY_OUTPUT_FILENAME:
                raise Step4ExplicitRetryConflictError(
                    "retry02 submit script output provenance changed",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )

            attempt01 = step4.attempts[0]
            attempt01_hashes = dict(attempt01.input_hashes)
            attempt01_tcontrol_digest = attempt01_hashes.get(
                STEP4_ATTEMPT01_TCONTROL_FILENAME
            )
            attempt01_script_digest = attempt01_hashes.get(STEP4_SCRIPT_FILENAME)
            if attempt01_tcontrol_digest is None or attempt01_script_digest is None:
                raise Step4ExplicitRetryConflictError(
                    "attempt-1 tcontrol/script SHA256 provenance is unavailable",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            attempt01_tcontrol = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_ATTEMPT01_TCONTROL_FILENAME)
            )
            attempt01_script = executor.read_bytes(
                str(PurePosixPath(directory) / STEP4_SCRIPT_FILENAME)
            )
            if (
                hashlib.sha256(attempt01_tcontrol).hexdigest()
                != attempt01_tcontrol_digest
                or hashlib.sha256(attempt01_script).hexdigest()
                != attempt01_script_digest
            ):
                raise Step4ExplicitRetryConflictError(
                    "attempt-1 preserved input evidence changed",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )

            parsed_tcontrol = parse_tcontrol(active_tcontrol, remote_structure)
            if (
                parsed_tcontrol.self_energy_filename
                != STEP4_RETRY_SELF_ENERGY_FILENAME
                or parsed_tcontrol.settings.output_filename != "TE.dat"
                or parsed_tcontrol.settings.natoms != request.evidence.natoms
                or parsed_tcontrol.settings.nsaos != request.evidence.nsaos
                or parsed_tcontrol.spin_mode is not request.evidence.spin_mode
            ):
                raise Step4ExplicitRetryConflictError(
                    "retry02 tcontrol science no longer matches Step-3 evidence",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            remote_surface = resolve_project_electrode_surfaces(
                project,
                remote_structure,
            )
            remote_plan = build_partitioned_self_energy_plan(
                remote_structure,
                remote_surface,
                parsed_tcontrol.settings,
            )
            if (
                remote_surface.left_local_to_global_zero_based
                != request.surface_proposal.left_local_to_global_zero_based
                or remote_surface.right_local_to_global_zero_based
                != request.surface_proposal.right_local_to_global_zero_based
                or _plan_signature(remote_plan) != _plan_signature(request.self_energy_plan)
            ):
                raise Step4ExplicitRetryConflictError(
                    "remote electrode provenance/membership differs from retry03 plan",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )

            for filename in (
                "TE.dat",
                STEP4_ATTEMPT02_TCONTROL_FILENAME,
                STEP4_RETRY03_SELF_ENERGY_FILENAME,
                STEP4_RETRY03_SCRIPT_FILENAME,
                STEP4_RETRY03_OUTPUT_FILENAME,
            ):
                try:
                    _require_absent(executor, directory, filename)
                except NewRemoteFileConflictError as error:
                    raise Step4ExplicitRetryConflictError(
                        f"Step-4 retry03 file conflict: {error}",
                        remote_project_path=project.remote_project_path,
                        step_kind=step4.kind,
                    ) from None

            retry_script = render_aitranss_submit_script(
                profile_preset=preset,
                settings=request.execution_settings,
                project_id=project.project_id,
                executable_path=aitranss.executable_path,
                aitranss_modules=runtime.modules,
                runtime=runtime,
                output_filename=STEP4_RETRY03_OUTPUT_FILENAME,
                mail_settings=request.profile.slurm_mail_settings,
            ).encode("utf-8")
            prior_attempt = ProjectStepAttempt(
                job_id=step4.job_id or "",
                submitted_at=step4.submitted_at,
                finished_at=step4.finished_at,
                terminal_scheduler_state=step4.scheduler_state,
                failure_reason=step4.last_error,
                submit_script_filename=STEP4_RETRY_SCRIPT_FILENAME,
                slurm_output_filename=STEP4_RETRY_OUTPUT_FILENAME,
                input_hashes=(
                    (STEP4_ATTEMPT02_TCONTROL_FILENAME, attempt02_tcontrol_digest),
                    (STEP4_RETRY_SELF_ENERGY_FILENAME, attempt02_self_energy_digest),
                    (STEP4_RETRY_SCRIPT_FILENAME, attempt02_script_digest),
                ),
                scheduler_kind=step4.scheduler_kind or SchedulerKind.SLURM,
            )

            report("Preserving retry02 tcontrol and installing retry03 inputs...")
            archive_hashes = upload_new_files_atomically(
                executor,
                directory,
                {STEP4_ATTEMPT02_TCONTROL_FILENAME: active_tcontrol},
                temporary_id_factory=self._temporary_id_factory,
            )
            if (
                dict(archive_hashes)[STEP4_ATTEMPT02_TCONTROL_FILENAME]
                != attempt02_tcontrol_digest
            ):
                raise Step4ExplicitRetryConflictError(
                    "attempt-2 tcontrol SHA256 changed during preservation"
                )
            self_energy_hashes = upload_new_files_atomically(
                executor,
                directory,
                {STEP4_RETRY03_SELF_ENERGY_FILENAME: self_energy},
                temporary_id_factory=self._temporary_id_factory,
            )
            retry_tcontrol_digest = replace_existing_file_atomically(
                executor,
                directory,
                STEP4_TCONTROL_FILENAME,
                retry_tcontrol,
                expected_existing_digest=attempt02_tcontrol_digest,
                temporary_id_factory=self._temporary_id_factory,
            )
            script_hashes = upload_new_files_atomically(
                executor,
                directory,
                {STEP4_RETRY03_SCRIPT_FILENAME: retry_script},
                temporary_id_factory=self._temporary_id_factory,
            )
            retry_hashes = tuple(
                {
                    STEP4_TCONTROL_FILENAME: retry_tcontrol_digest,
                    **dict(self_energy_hashes),
                    **dict(script_hashes),
                }.items()
            )

            command = build_sbatch_submission_command(
                directory,
                slurm.sbatch_path,
                STEP4_RETRY03_SCRIPT_FILENAME,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
            report("Submitting exactly one reader-compatible Step-4 retry03...")
            submitted_at = _aware_now(self._now_factory)
            attempts = step4.attempts + (prior_attempt,)
            try:
                result = executor.execute(command)
            except RemoteCommandOutcomeUnknown:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=retry_hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_RETRY03_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_RETRY03_OUTPUT_FILENAME,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" Inspect {scheduler_name} before any further action; "
                    "never retry automatically.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            except RemoteExecutorError:
                raise
            if result.exit_status != 0:
                rejected = replace(
                    step4,
                    state=ProjectStepState.FAILED,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=submitted_at,
                    input_hashes=retry_hashes,
                    last_error="SUBMISSION_REJECTED",
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_RETRY03_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_RETRY03_OUTPUT_FILENAME,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, rejected, request.profile)
                raise SbatchRejectedError(
                    f"{scheduler_name} rejected the reader-compatible Step-4 retry03",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                )
            try:
                receipt = parse_sbatch_parsable_output(result.stdout)
            except SlurmSubmissionError:
                unknown = replace(
                    step4,
                    state=ProjectStepState.UNKNOWN,
                    job_id=None,
                    cluster_name=None,
                    submitted_at=submitted_at,
                    started_at=None,
                    finished_at=None,
                    input_hashes=retry_hashes,
                    last_error=_UNKNOWN_SUBMISSION_MESSAGE,
                    scheduler_state=None,
                    scheduler_kind=preset.scheduler_kind,
                    submit_script_filename=STEP4_RETRY03_SCRIPT_FILENAME,
                    slurm_output_filename=STEP4_RETRY03_OUTPUT_FILENAME,
                    attempts=attempts,
                )
                self._persist_and_mark(repository, project, unknown, request.profile)
                raise SubmissionOutcomeUnknown(
                    _UNKNOWN_SUBMISSION_MESSAGE
                    + f" {submit_command_name} returned an unsupported receipt; "
                    f"inspect {scheduler_name}.",
                    remote_project_path=project.remote_project_path,
                    step_kind=step4.kind,
                ) from None
            queued = replace(
                step4,
                state=ProjectStepState.QUEUED,
                job_id=receipt.job_id,
                cluster_name=receipt.cluster_name,
                submitted_at=submitted_at,
                started_at=None,
                finished_at=None,
                input_hashes=retry_hashes,
                last_error=None,
                scheduler_state=None,
                scheduler_kind=preset.scheduler_kind,
                submit_script_filename=STEP4_RETRY03_SCRIPT_FILENAME,
                slurm_output_filename=STEP4_RETRY03_OUTPUT_FILENAME,
                attempts=attempts,
            )
            updated = self._persist_and_mark(
                repository,
                project,
                queued,
                request.profile,
            )
            return TransportSubmissionResult(
                updated,
                _project_step(updated, step4.kind),
                directory,
                receipt.job_id,
                receipt.cluster_name,
                retry_hashes,
            )
        finally:
            executor.close()

    def _load_matching_project(
        self,
        repository: RemoteProjectRepository,
        expected: CalculationProject,
    ) -> CalculationProject:
        project = repository.load(
            expected.remote_project_path,
            preserve_remote_errors=True,
        )
        if project.project_id != expected.project_id:
            raise ProjectSubmissionError("remote project identity changed")
        if project.revision != expected.revision:
            raise ProjectSubmissionError("remote project changed; refresh first")
        return project

    def _persist_and_mark(
        self,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        profile: ServerProfile,
    ) -> CalculationProject:
        now = _aware_now(self._now_factory)
        candidate = replace(
            project,
            steps=tuple(step if item.kind is step.kind else item for item in project.steps),
        )
        try:
            updated = repository.persist_update(candidate, updated_at=now)
            self._local_index_repository.mark_seen(
                updated,
                bound_server_profile_id=profile.profile_id,
            )
        except Exception as error:
            scheduler_name = scheduler_display_name(
                step.scheduler_kind or SchedulerKind.SLURM
            )
            raise ProjectStateRecordingError(
                f"{scheduler_name} outcome could not be recorded durably: {error}",
                remote_project_path=project.remote_project_path,
                step_kind=step.kind,
                job_id=step.job_id,
            ) from None
        return updated


def _archive_current_attempt(
    executor: RemoteExecutor,
    directory: str,
    step: ProjectStepRecord,
) -> ProjectStepAttempt:
    if step.job_id is None:
        raise Step3RetryConflictError("failed Step 3 has no prior Job ID")
    script_filename = step.submit_script_filename or "submit.sh"
    output_filename = step.slurm_output_filename
    if output_filename is None:
        try:
            output_filename = parse_submit_script_output_filename(
                executor.read_bytes(
                    str(PurePosixPath(directory) / script_filename)
                )
            )
        except (RemotePathNotFoundError, ValueError) as error:
            raise Step3RetryConflictError(
                f"prior Step-3 attempt metadata is unavailable: {error}"
            ) from None
    current_hashes = dict(step.input_hashes)
    attempt_hashes = tuple(
        (filename, current_hashes[filename])
        for filename in ("geometry.in", "control.in", script_filename)
        if filename in current_hashes
    )
    return ProjectStepAttempt(
        job_id=step.job_id,
        submitted_at=step.submitted_at,
        finished_at=step.finished_at,
        terminal_scheduler_state=step.scheduler_state,
        failure_reason=step.last_error,
        submit_script_filename=script_filename,
        slurm_output_filename=output_filename,
        input_hashes=attempt_hashes,
        scheduler_kind=step.scheduler_kind or SchedulerKind.SLURM,
    )


def _validate_step3_retry_state(project: CalculationProject) -> None:
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step3.state is not ProjectStepState.FAILED or step3.job_id is None:
        raise Step3RetryConflictError(
            "Step 3 must be terminal FAILED with a known Job ID before retry",
            remote_project_path=project.remote_project_path,
            step_kind=step3.kind,
        )
    if step4.state is not ProjectStepState.NOT_STARTED:
        raise Step3RetryConflictError(
            "Step 4 must remain NOT_STARTED before a Step-3 retry",
            remote_project_path=project.remote_project_path,
            step_kind=step3.kind,
        )


def _validate_step4_state(project: CalculationProject) -> None:
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step3.state is not ProjectStepState.SUCCEEDED:
        raise Step4SubmissionConflictError(
            "Step 3 must be SUCCEEDED before Step 4",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )


def _validate_step4_explicit_retry_state(project: CalculationProject) -> None:
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step3.state is not ProjectStepState.SUCCEEDED:
        raise Step4ExplicitRetryConflictError(
            "Step 3 must remain SUCCEEDED before the explicit Step-4 retry",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )
    if (
        step4.state is not ProjectStepState.FAILED
        or step4.job_id is None
        or step4.last_error
        != AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value
        or step4.submit_script_filename != STEP4_SCRIPT_FILENAME
        or step4.slurm_output_filename != STEP4_OUTPUT_FILENAME
        or step4.attempts
    ):
        raise Step4ExplicitRetryConflictError(
            "Step 4 must be the first typed electrode-overlap failure before retry",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )


def _validate_step4_settings_retry_state(project: CalculationProject) -> None:
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step3.state is not ProjectStepState.SUCCEEDED:
        raise Step4SettingsRetryConflictError(
            "Step 3 must remain SUCCEEDED before an edited Step-4 retry",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )
    if (
        step4.state is not ProjectStepState.FAILED
        or step4.job_id is None
        or step4.scheduler_state != "CANCELLED"
        or step4.submit_script_filename is None
        or step4.slurm_output_filename is None
    ):
        raise Step4SettingsRetryConflictError(
            "Step 4 must be durably CANCELLED with an exact current attempt "
            "before an edited retry",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )


def _validate_step4_reader_compatible_retry_state(
    project: CalculationProject,
) -> None:
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step3.state is not ProjectStepState.SUCCEEDED:
        raise Step4ExplicitRetryConflictError(
            "Step 3 must remain SUCCEEDED before retry03",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )
    if (
        step4.state is not ProjectStepState.FAILED
        or step4.job_id is None
        or step4.last_error
        != AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR.value
        or step4.scheduler_state != "COMPLETED"
        or step4.submit_script_filename != STEP4_RETRY_SCRIPT_FILENAME
        or step4.slurm_output_filename != STEP4_RETRY_OUTPUT_FILENAME
        or len(step4.attempts) != 1
    ):
        raise Step4ExplicitRetryConflictError(
            "Step 4 must be the reconciled retry02 self-energy format failure",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )
    attempt01 = step4.attempts[0]
    if (
        attempt01.failure_reason
        != AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value
        or attempt01.submit_script_filename != STEP4_SCRIPT_FILENAME
        or attempt01.slurm_output_filename != STEP4_OUTPUT_FILENAME
    ):
        raise Step4ExplicitRetryConflictError(
            "Step-4 attempt-1 overlap provenance is unavailable",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )


def _plan_signature(plan: PartitionedSelfEnergyPlan) -> tuple[object, ...]:
    """Compare scientific membership without depending on structure comments."""

    def selection_signature(selection) -> tuple[object, ...]:
        return (
            selection.side,
            selection.candidate_global_zero_based,
            tuple(
                (
                    layer.plane_number,
                    layer.distance_angstrom,
                    layer.global_zero_based,
                    layer.template_local_indices,
                    layer.leakage_text,
                    layer.leakage,
                )
                for layer in selection.layers
            ),
        )

    return (
        tuple(
            (atom.index, atom.element, atom.x, atom.y, atom.z)
            for atom in plan.structure
        ),
        selection_signature(plan.left),
        selection_signature(plan.right),
        plan.tolerance_angstrom,
    )
    if (
        step4.state is not ProjectStepState.NOT_STARTED
        or step4.job_id is not None
        or step4.cluster_name is not None
        or step4.submitted_at is not None
        or step4.started_at is not None
        or step4.finished_at is not None
        or step4.input_hashes
        or step4.last_error is not None
        or step4.scheduler_state is not None
        or step4.submit_script_filename is not None
        or step4.slurm_output_filename is not None
        or step4.attempts
    ):
        raise Step4SubmissionConflictError(
            "Step 4 must have pristine NOT_STARTED state",
            remote_project_path=project.remote_project_path,
            step_kind=step4.kind,
        )


def _validate_project_profile_scope(
    project: CalculationProject,
    profile: ServerProfile,
) -> None:
    if PurePosixPath(project.remote_project_path).parent != PurePosixPath(
        profile.remote_project_root
    ):
        raise ValueError("project is outside the selected server workspace")


def _validate_password(password: str | None) -> None:
    if password is not None and (not isinstance(password, str) or not password):
        raise ValueError("temporary password must be nonempty text")


def _project_step(project: CalculationProject, kind: ProjectStepKind) -> ProjectStepRecord:
    return next(step for step in project.steps if step.kind is kind)


def _merge_hashes(
    existing: tuple[tuple[str, str], ...],
    added: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    merged = dict(existing)
    merged.update(added)
    return tuple(merged.items())


def _require_absent(
    executor: RemoteExecutor,
    directory: str,
    filename: str,
) -> None:
    try:
        executor.stat(str(PurePosixPath(directory) / filename))
    except RemotePathNotFoundError:
        return
    raise NewRemoteFileConflictError(filename)


def _require_nonempty(
    executor: RemoteExecutor,
    directory: str,
    filename: str,
) -> None:
    try:
        stat = executor.stat(str(PurePosixPath(directory) / filename))
    except RemotePathNotFoundError:
        raise Step4SubmissionConflictError(
            f"Missing required Step-3 output: {filename}"
        ) from None
    if stat.is_directory or stat.size is None or stat.size <= 0:
        raise Step4SubmissionConflictError(
            f"Missing required Step-3 output: {filename}"
        )


def _aware_now(factory: Callable[[], datetime]) -> datetime:
    value = factory()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("submission timestamp must be timezone-aware")
    return value
