"""Recover durable projects and reconcile one explicit scheduler snapshot."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
import hashlib
from math import isclose
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from moltage.aims.recovery import (
    OUTPUT_TAIL_MAX_BYTES,
    AimsRecoveryError,
    assess_geometry_optimization_output,
    parse_control_species_elements,
    parse_molecular_geometry,
    recover_optimized_structure,
)
from moltage.aims.transport_evidence import (
    AIMS_OUTPUT_HEAD_MAX_BYTES,
    AIMS_OUTPUT_TAIL_MAX_BYTES,
    OUT_OF_MEMORY_REASON,
    ORBITAL_HEADER_MAX_BYTES,
    SLURM_TASK_OUT_OF_MEMORY,
    TIMEOUT_REASON,
    TransportCompletionEvidence,
    TransportEvidenceError,
    has_exact_normal_termination,
    has_slurm_task_oom_signature,
    parse_aims_natoms,
    parse_aims_nsaos,
    parse_transport_spin_mode,
)
from moltage.aitranss.output import (
    AITRANSS_OUTPUT_TAIL_MAX_BYTES,
    AitranssFailureCode,
    aitranss_failure_detail,
    aitranss_failure_reason,
    classify_aitranss_fatal_output,
    has_aitranss_transmission_success,
)
from moltage.aitranss.self_energy import (
    PartitionedSelfEnergyPlan,
    SelfEnergyError,
    build_partitioned_self_energy_plan,
)
from moltage.aitranss.tcontrol import (
    TControlError,
    TControlSettings,
    parse_tcontrol,
)
from moltage.aitranss.transmission import (
    TRANSMISSION_RESULT_MAX_BYTES,
    TransmissionDataError,
    TransmissionResult,
    parse_te_dat,
    parse_transmission_request,
    submitted_tcontrol_bytes,
    validate_transmission_grid,
)
from moltage.app.connection_service import ServerConnectionService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_orbital_cube import (
    ProjectOrbitalCubeArtifact,
    ProjectOrbitalCubeCatalog,
    discover_step1_orbital_cubes,
)
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    CalculationProject,
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
    remote_step_directory,
)
from moltage.orca.evidence import (
    OrcaEvidenceError,
    OrcaFrequencyCompletion,
    OrcaFrequencyEvidence,
    OrcaImaginaryModeClassification,
    parse_orca_final_xyz,
    parse_orca_frequency_evidence,
    parse_orca_optimization_output,
)
from moltage.orca.input_writer import parse_rendered_orca_structure
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.orca.wbl import WblSpinTreatment
from moltage.orca.wbl_artifacts import (
    WBL_CSV_FILENAME,
    WBL_JSON_FILENAME,
    OrcaWblArtifactError,
    OrcaWblPresentation,
    parse_wbl_presentation,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.scheduler import (
    SchedulerKind,
    scheduler_display_name,
)
from moltage.domain.server_profile import ServerProfile, SlurmExecutionPreset
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_surface import (
    ElectrodeSurfaceError,
    ElectrodeSurfaceProposal,
    resolve_project_electrode_surfaces,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteExecutor,
    RemoteOperationStopToken,
    RemoteOperationStopped,
    RemotePathNotFoundError,
)
from moltage.remote.project_repository import (
    ManagedProjectProblem,
    RemoteProjectRepository,
)
from moltage.remote.slurm import (
    SlurmSubmissionError,
    parse_submit_script_output_filename,
    preset_with_submit_script_resources,
)
from moltage.remote.slurm_cancel import (
    request_slurm_cancellation_once,
)
from moltage.remote.slurm_discovery import (
    resolve_slurm_for_submission,
    slurm_command_path,
)
from moltage.remote.slurm_status import (
    SchedulerStatusKind,
    SlurmJobStatus,
    query_slurm_job_status,
    validate_job_id,
)
from moltage.structure.connectivity import infer_connectivity


ORCA_OPT_INPUT = "orca_opt.inp"
ORCA_OPT_OUTPUT = "orca_opt.out"
ORCA_FREQ_INPUT = "orca_freq.inp"
ORCA_FREQ_OUTPUT = "orca_freq.out"
from moltage.structure.covalent_radii import load_default_covalent_radii


class ProjectRecoveryError(RuntimeError):
    """Raised when authoritative project recovery cannot proceed safely."""


class ProjectProfileRebindRequired(ProjectRecoveryError):
    """Raised until a cross-machine local profile binding is confirmed."""

    def __init__(self, project: CalculationProject, profile: ServerProfile) -> None:
        super().__init__(
            "This project was created with another local server-profile identity."
        )
        self.project = project
        self.profile = profile


class Step3OomCancellationError(ProjectRecoveryError):
    """Raised when an exact active-OOM cancellation cannot proceed safely."""


class StepRuntimeEvidence(StrEnum):
    """Derived, non-persistent health evidence for one active attempt."""

    TASK_OOM_DETECTED = "TASK_OOM_DETECTED"


class Step3OomCancellationOutcome(StrEnum):
    """Known application outcomes of one explicit cancellation operation."""

    REQUESTED = "REQUESTED"
    UNKNOWN = "UNKNOWN"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


ACTIVE_TASK_OOM_DETAIL = (
    "检测到任务因内存不足发生进程终止，但调度器作业仍在运行。"
)


@dataclass(frozen=True, slots=True)
class Step3OomCancellationRequest:
    """Stale UI identity that must match the reloaded current attempt."""

    profile: ServerProfile
    project: CalculationProject
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_profile(self.profile)
        if not isinstance(self.project, CalculationProject):
            raise TypeError("Step-3 cancellation requires a CalculationProject")
        _validate_selected_root(self.profile, self.project.remote_project_path)
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("temporary password must be nonempty text or None")
        if _active_step_kind(self.project) is not ProjectStepKind.TRANSPORT_CONVERGENCE:
            raise Step3OomCancellationError(
                "Only the current Step-3 job can be cancelled by this action."
            )
        step = _project_step(
            self.project,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        if (
            step.state is not ProjectStepState.RUNNING
            or step.job_id is None
            or step.submit_script_filename is None
            or step.slurm_output_filename is None
        ):
            raise Step3OomCancellationError(
                "Refresh before cancelling: the active Step-3 attempt identity "
                "is incomplete or no longer RUNNING."
            )


@dataclass(frozen=True, slots=True)
class Step3OomCancellationResult:
    """One known outcome; terminal races may include a reconciled snapshot."""

    outcome: Step3OomCancellationOutcome
    project_id: UUID
    job_id: str
    snapshot: "ProjectRecoverySnapshot | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Step3OomCancellationOutcome):
            raise TypeError("Step-3 cancellation outcome is unsupported")
        if not isinstance(self.project_id, UUID):
            raise TypeError("Step-3 cancellation requires a project UUID")
        validate_job_id(self.job_id)
        if self.outcome is Step3OomCancellationOutcome.ALREADY_TERMINAL:
            if not isinstance(self.snapshot, ProjectRecoverySnapshot):
                raise TypeError("terminal cancellation outcome requires a snapshot")
        elif self.snapshot is not None:
            raise TypeError("non-terminal cancellation outcomes cannot carry a snapshot")


def slurm_failure_reason(failure_code: str | None) -> str | None:
    """Map the two reviewed terminal Slurm OOM classifications for presentation."""

    if failure_code in {"OUT_OF_MEMORY", SLURM_TASK_OUT_OF_MEMORY}:
        return OUT_OF_MEMORY_REASON
    return None


@dataclass(frozen=True, slots=True)
class ProjectRecoverySnapshot:
    project: CalculationProject
    active_step_kind: ProjectStepKind
    status_message: str
    optimized_structure: MolecularStructure | None = None
    connectivity: Connectivity | None = None
    requires_profile_rebind: bool = False
    profile_rebind_confirmed: bool = False
    transport_evidence: TransportCompletionEvidence | None = None
    surface_proposal: ElectrodeSurfaceProposal | None = None
    transport_preparation_error: str | None = None
    step4_attempt01_tcontrol: bytes | None = None
    step4_tcontrol_settings: TControlSettings | None = None
    step4_self_energy_plan: PartitionedSelfEnergyPlan | None = None
    transmission_result: TransmissionResult | None = None
    transmission_result_filename: str | None = None
    transmission_result_error: str | None = None
    runtime_evidence: StepRuntimeEvidence | None = None
    step3_retry_preset: SlurmExecutionPreset | None = None
    step3_retry_preparation_error: str | None = None
    scheduler_status_kind: SchedulerStatusKind | None = None
    orbital_cubes: tuple[ProjectOrbitalCubeArtifact, ...] = ()
    orbital_cube_diagnostic: str | None = None
    submitted_structure: MolecularStructure | None = None
    orca_output_bytes: bytes | None = None
    orca_trajectory_available: bool = False
    orca_wbl_presentation: OrcaWblPresentation | None = None

    @property
    def active_step(self) -> ProjectStepRecord:
        return _project_step(self.project, self.active_step_kind)

    @property
    def can_continue_step2(self) -> bool:
        return (
            self.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            _project_step(self.project, ProjectStepKind.MOLECULE_OPT).state
            is ProjectStepState.SUCCEEDED
            and _project_step(
                self.project,
                ProjectStepKind.MOLECULE_AU_OPT,
            ).state
            is ProjectStepState.NOT_STARTED
            and self.optimized_structure is not None
        )

    @property
    def can_retry_step3(self) -> bool:
        return (
            self.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            self.active_step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE
            and self.active_step.state is ProjectStepState.FAILED
            and self.active_step.job_id is not None
            and self.active_step.last_error
            in {TIMEOUT_REASON, "OUT_OF_MEMORY", SLURM_TASK_OUT_OF_MEMORY}
        )

    @property
    def can_kill_step3_oom(self) -> bool:
        return (
            self.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            self.active_step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE
            and self.active_step.state is ProjectStepState.RUNNING
            and self.active_step.job_id is not None
            and self.active_step.submit_script_filename is not None
            and self.active_step.slurm_output_filename is not None
            and self.runtime_evidence is StepRuntimeEvidence.TASK_OOM_DETECTED
        )

    @property
    def can_continue_step4(self) -> bool:
        return (
            self.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            self.active_step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE
            and self.active_step.state is ProjectStepState.SUCCEEDED
            and self.optimized_structure is not None
            and self.transport_evidence is not None
            and self.surface_proposal is not None
        )

    @property
    def can_retry_step4_explicit(self) -> bool:
        return (
            self.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            self.active_step_kind is ProjectStepKind.TRANSMISSION
            and self.active_step.state is ProjectStepState.FAILED
            and self.active_step.job_id is not None
            and self.active_step.last_error
            == AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value
            and self.optimized_structure is not None
            and self.transport_evidence is not None
            and self.surface_proposal is not None
            and self.step4_attempt01_tcontrol is not None
            and self.step4_tcontrol_settings is not None
            and self.step4_self_energy_plan is not None
        )

    @property
    def can_view_transmission(self) -> bool:
        return (
            self.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            self.active_step_kind is ProjectStepKind.TRANSMISSION
            and self.active_step.state is ProjectStepState.SUCCEEDED
            and self.transmission_result is not None
            and self.transmission_result_filename is not None
        )

    @property
    def can_view_orca_wbl(self) -> bool:
        return bool(
            self.project.workflow_kind is CalculationWorkflowKind.ORCA
            and self.orca_wbl_presentation is not None
            and any(
                step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
                and step.state is ProjectStepState.SUCCEEDED
                and step.orca_wbl_result is not None
                for step in self.project.steps
            )
        )


@dataclass(frozen=True, slots=True)
class ProjectDiscoveryResult:
    snapshots: tuple[ProjectRecoverySnapshot, ...]
    problems: tuple[ManagedProjectProblem, ...]


@dataclass(frozen=True, slots=True)
class _SchedulerPaths:
    squeue: str
    sacct: str
    scancel: str
    lsf_env_directory: str | None = None
    lsf_library_directory: str | None = None
    lsf_server_directory: str | None = None


class ProjectRecoveryService:
    """Use one short-lived connection for explicit discovery or refresh."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        temporary_id_factory: Callable[[], str] = lambda: uuid4().hex,
        covalent_radii_loader: Callable[[], Mapping[str, float]] = (
            load_default_covalent_radii
        ),
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._now_factory = now_factory
        self._temporary_id_factory = temporary_id_factory
        self._covalent_radii_loader = covalent_radii_loader

    def discover_and_refresh(
        self,
        profile: ServerProfile,
        supplied_password: str | None = None,
        *,
        progress: Callable[[str], None] | None = None,
        stop_token: RemoteOperationStopToken | None = None,
    ) -> ProjectDiscoveryResult:
        """Discover first-level manifests and reconcile matching projects once."""

        _require_profile(profile)
        report = _progress_reporter(progress, stop_token)
        report(f"Connecting to {profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            profile,
            supplied_password,
            **({"stop_token": stop_token} if stop_token is not None else {}),
        )
        try:
            if stop_token is not None:
                stop_token.bind_executor(executor)
            report("Opening remote project workspace...")
            repository = self._repository(executor)
            report("Reading managed projects...")
            discovery = repository.discover(
                profile.remote_project_root,
                preserve_remote_errors=True,
            )
            recycled_project_ids = {
                item.project_id
                for item in self._local_index_repository.load_recycled(
                    server_profile_id=profile.profile_id
                )
            }
            local_profile_bindings = {
                (
                    item.project_id,
                    item.remote_project_path,
                    item.server_profile_id,
                )
                for item in self._local_index_repository.load()
            }
            scheduler_paths: _SchedulerPaths | None = None

            def paths() -> _SchedulerPaths:
                nonlocal scheduler_paths
                if scheduler_paths is None:
                    scheduler_paths = _resolve_scheduler_paths(executor, profile)
                return scheduler_paths

            snapshots: list[ProjectRecoverySnapshot] = []
            for project in discovery.projects:
                if stop_token is not None:
                    stop_token.checkpoint()
                _validate_selected_root(profile, project.remote_project_path)
                if project.project_id in recycled_project_ids:
                    continue
                profile_identity_differs = (
                    project.server_profile_id != profile.profile_id
                )
                local_profile_binding_confirmed = (
                    profile_identity_differs
                    and (
                        project.project_id,
                        project.remote_project_path,
                        profile.profile_id,
                    )
                    in local_profile_bindings
                )
                if profile_identity_differs and not local_profile_binding_confirmed:
                    snapshots.append(
                        _snapshot(
                            project,
                            status_message=(
                                "Local server-profile confirmation is required "
                                "before status refresh."
                            ),
                            requires_profile_rebind=True,
                        )
                    )
                    continue
                snapshot = self._reconcile(
                    executor,
                    repository,
                    project,
                    paths,
                    profile,
                    report,
                )
                if local_profile_binding_confirmed:
                    snapshot = replace(
                        snapshot,
                        profile_rebind_confirmed=True,
                    )
                self._mark_seen(snapshot.project, profile)
                snapshots.append(snapshot)
            report("Updating project list...")
            return ProjectDiscoveryResult(
                tuple(
                    sorted(
                        snapshots,
                        key=lambda item: item.project.remote_project_path,
                    )
                ),
                discovery.problems,
            )
        except Exception:
            if stop_token is not None:
                stop_token.checkpoint()
            raise
        finally:
            _close_stoppable_executor(executor, stop_token)

    def refresh_project(
        self,
        profile: ServerProfile,
        remote_project_path: str,
        *,
        profile_rebind_confirmed: bool = False,
        supplied_password: str | None = None,
        progress: Callable[[str], None] | None = None,
        stop_token: RemoteOperationStopToken | None = None,
    ) -> ProjectRecoverySnapshot:
        """Reload one authoritative manifest and reconcile it exactly once."""

        _require_profile(profile)
        if not isinstance(profile_rebind_confirmed, bool):
            raise TypeError("profile rebind confirmation must be boolean")
        _validate_selected_root(profile, remote_project_path)
        report = _progress_reporter(progress, stop_token)
        report(f"Connecting to {profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            profile,
            supplied_password,
            **({"stop_token": stop_token} if stop_token is not None else {}),
        )
        try:
            if stop_token is not None:
                stop_token.bind_executor(executor)
            report("Opening remote project workspace...")
            repository = self._repository(executor)
            report("Reading managed projects...")
            project = repository.load(
                remote_project_path,
                preserve_remote_errors=True,
            )
            profile_identity_differs = (
                project.server_profile_id != profile.profile_id
            )
            local_profile_binding_confirmed = (
                profile_identity_differs
                and self._local_index_repository.is_bound_to_profile(
                    project,
                    bound_server_profile_id=profile.profile_id,
                )
            )
            effective_profile_rebind_confirmation = (
                profile_rebind_confirmed or local_profile_binding_confirmed
            )
            if (
                profile_identity_differs
                and not effective_profile_rebind_confirmation
            ):
                raise ProjectProfileRebindRequired(project, profile)
            scheduler_paths: _SchedulerPaths | None = None

            def paths() -> _SchedulerPaths:
                nonlocal scheduler_paths
                if scheduler_paths is None:
                    scheduler_paths = _resolve_scheduler_paths(executor, profile)
                return scheduler_paths

            snapshot = self._reconcile(
                executor,
                repository,
                project,
                paths,
                profile,
                report,
            )
            if profile_identity_differs:
                snapshot = replace(
                    snapshot,
                    profile_rebind_confirmed=True,
                )
            report("Updating project list...")
            self._mark_seen(snapshot.project, profile)
            return snapshot
        except Exception:
            if stop_token is not None:
                stop_token.checkpoint()
            raise
        finally:
            _close_stoppable_executor(executor, stop_token)

    def cancel_active_step3_oom_job(
        self,
        request: Step3OomCancellationRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> Step3OomCancellationResult:
        """Revalidate and dispatch at most one exact-job scancel request."""

        if not isinstance(request, Step3OomCancellationRequest):
            raise TypeError(
                "cancel_active_step3_oom_job requires Step3OomCancellationRequest"
            )
        report = _progress_reporter(progress)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            result = self._cancel_active_step3_oom_job(
                executor,
                request,
                report,
            )
        except Exception:
            _close_without_replacing_outcome(executor)
            raise
        _close_without_replacing_outcome(executor)
        return result

    def _cancel_active_step3_oom_job(
        self,
        executor: RemoteExecutor,
        request: Step3OomCancellationRequest,
        progress: Callable[[str], None],
    ) -> Step3OomCancellationResult:
        progress("Reloading the authoritative Step-3 attempt...")
        repository = self._repository(executor)
        project = repository.load(
            request.project.remote_project_path,
            preserve_remote_errors=True,
        )
        expected = _project_step(
            request.project,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        if project.project_id != request.project.project_id:
            raise Step3OomCancellationError(
                "Project identity changed before cancellation. Refresh first."
            )
        if _active_step_kind(project) is not ProjectStepKind.TRANSPORT_CONVERGENCE:
            raise Step3OomCancellationError(
                "The active project step changed before cancellation. Refresh first."
            )
        current = _project_step(
            project,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        if any(
            getattr(current, name) != getattr(expected, name)
            for name in (
                "job_id",
                "submit_script_filename",
                "slurm_output_filename",
            )
        ):
            raise Step3OomCancellationError(
                "The current Step-3 attempt changed before cancellation. "
                "Refresh first."
            )
        if (
            current.job_id is None
            or current.submit_script_filename is None
            or current.slurm_output_filename is None
        ):
            raise Step3OomCancellationError(
                "The current Step-3 attempt identity is incomplete. Refresh first."
            )
        validate_job_id(current.job_id)

        _require_scheduler_binding(request.profile, current)

        scheduler_name = _scheduler_name(current)
        progress(f"Checking the exact {scheduler_name} job before cancellation...")
        paths = _resolve_scheduler_paths(executor, request.profile)
        status = query_slurm_job_status(
            executor,
            squeue_path=paths.squeue,
            sacct_path=paths.sacct,
            job_id=current.job_id,
            profile_username=request.profile.username,
            lsf_env_directory=paths.lsf_env_directory,
            lsf_library_directory=paths.lsf_library_directory,
            lsf_server_directory=paths.lsf_server_directory,
        )
        if not (
            status.kind is SchedulerStatusKind.RUNNING
            and status.scheduler_state == "RUNNING"
            and current.state
            in {ProjectStepState.QUEUED, ProjectStepState.RUNNING}
        ):
            if status.kind in {
                SchedulerStatusKind.COMPLETED,
                SchedulerStatusKind.FAILED,
            }:
                snapshot = self._reconcile(
                    executor,
                    repository,
                    project,
                    lambda: paths,
                    request.profile,
                    progress,
                )
                self._mark_seen(snapshot.project, request.profile)
                return Step3OomCancellationResult(
                    Step3OomCancellationOutcome.ALREADY_TERMINAL,
                    project.project_id,
                    current.job_id,
                    snapshot,
                )
            raise Step3OomCancellationError(
                "The exact Step-3 job is no longer confirmed RUNNING. "
                "Refresh before cancelling."
            )

        _read_current_step3_submit_script(executor, project, current)
        output_path = str(
            PurePosixPath(remote_step_directory(project, current.kind))
            / current.slurm_output_filename
        )
        output_tail = executor.read_file_tail(
            output_path,
            AIMS_OUTPUT_TAIL_MAX_BYTES,
        )
        if not has_slurm_task_oom_signature(output_tail):
            raise Step3OomCancellationError(
                "The exact current Step-3 output no longer contains the reviewed "
                "task-OOM evidence. Refresh before cancelling."
            )

        progress(
            f"Requesting cancellation of {scheduler_name} Job {current.job_id}..."
        )
        try:
            request_slurm_cancellation_once(
                executor,
                scancel_path=paths.scancel,
                job_id=current.job_id,
                lsf_env_directory=paths.lsf_env_directory,
                lsf_library_directory=paths.lsf_library_directory,
                lsf_server_directory=paths.lsf_server_directory,
            )
        except RemoteCommandOutcomeUnknown:
            return Step3OomCancellationResult(
                Step3OomCancellationOutcome.UNKNOWN,
                project.project_id,
                current.job_id,
            )
        return Step3OomCancellationResult(
            Step3OomCancellationOutcome.REQUESTED,
            project.project_id,
            current.job_id,
        )

    def _repository(self, executor: RemoteExecutor) -> RemoteProjectRepository:
        return RemoteProjectRepository(
            executor,
            temporary_id_factory=self._temporary_id_factory,
        )

    def _reconcile(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        scheduler_paths: Callable[[], _SchedulerPaths],
        profile: ServerProfile,
        progress: Callable[[str], None],
    ) -> ProjectRecoverySnapshot:
        if project.workflow_kind is CalculationWorkflowKind.ORCA:
            return self._reconcile_orca(
                executor,
                repository,
                project,
                scheduler_paths,
                profile,
                progress,
            )
        step_kind = _active_step_kind(project)
        step = _project_step(project, step_kind)

        if step.state is ProjectStepState.SUCCEEDED:
            if step_kind is ProjectStepKind.TRANSMISSION:
                progress("Reading completed transmission result...")
                return self._recover_succeeded_transmission(
                    executor,
                    project,
                    step,
                )
            progress("Reading completed calculation results...")
            if step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE:
                return self._recover_succeeded_transport(executor, project, step)
            structure, connectivity, control_text = self._recover_structure(
                executor,
                project,
                step_kind,
            )
            orbital_catalog = self._orbital_cube_catalog(
                executor,
                project,
                step_kind,
                control_text,
            )
            continuation_note = ""
            return _snapshot(
                project,
                status_message=(
                    f"{_step_label(step_kind)} completed successfully; optimized "
                    "geometry recovered."
                    + continuation_note
                ),
                optimized_structure=structure,
                connectivity=connectivity,
                orbital_cubes=orbital_catalog.available,
                orbital_cube_diagnostic=orbital_catalog.diagnostic,
            )
        if step.state is ProjectStepState.FAILED and step.job_id is None:
            return _snapshot(
                project,
                status_message=step.last_error or "Calculation step failed.",
            )
        if step.state is ProjectStepState.SCHEDULER_COMPLETED:
            # Scheduler completion is durable evidence.  A later refresh may
            # finish scientific assessment, but must never regress to RUNNING.
            if step.kind is ProjectStepKind.TRANSMISSION:
                if step.job_id is None:
                    raise ProjectRecoveryError(
                        "Step 4 scheduler-completed state has no Job ID"
                    )
                validate_job_id(step.job_id)
                scheduler_name = _scheduler_name(step)
                progress(f"Checking {scheduler_name} status...")
                _require_scheduler_binding(profile, step)
                paths = scheduler_paths()
                status = query_slurm_job_status(
                    executor,
                    squeue_path=paths.squeue,
                    sacct_path=paths.sacct,
                    job_id=step.job_id,
                    profile_username=profile.username,
                    lsf_env_directory=paths.lsf_env_directory,
                    lsf_library_directory=paths.lsf_library_directory,
                    lsf_server_directory=paths.lsf_server_directory,
                )
                if status.kind is SchedulerStatusKind.COMPLETED:
                    return self._assess_completed(
                        executor,
                        repository,
                        project,
                        step,
                        progress,
                        scheduler_status=status,
                    )
                if status.kind is SchedulerStatusKind.FAILED:
                    return self._assess_transmission_scheduler_failure(
                        executor,
                        repository,
                        project,
                        step,
                        status,
                        progress,
                    )
                return _snapshot(
                    project,
                    status_message=(
                        "Step 4 remains scheduler-completed in the manifest; "
                        f"current {scheduler_name} accounting is not yet "
                        "authoritative for "
                        "scientific success."
                    ),
                )
            return self._assess_completed(
                executor,
                repository,
                project,
                step,
                progress,
            )
        if step.state in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}:
            return _snapshot(project, status_message="Calculation step has not started.")
        if step.state is ProjectStepState.UNKNOWN and step.job_id is None:
            return _snapshot(
                project,
                status_message=(
                    "Submission outcome is unknown and no Job ID is available. "
                    "Manual scheduler inspection is required."
                ),
            )
        if step.job_id is None:
            raise ProjectRecoveryError(
                f"{_step_label(step_kind)} state {step.state.value} has no Job ID"
            )
        validate_job_id(step.job_id)
        scheduler_name = _scheduler_name(step)
        progress(f"Checking {scheduler_name} status...")
        _require_scheduler_binding(profile, step)
        paths = scheduler_paths()
        status = query_slurm_job_status(
            executor,
            squeue_path=paths.squeue,
            sacct_path=paths.sacct,
            job_id=step.job_id,
            profile_username=profile.username,
            lsf_env_directory=paths.lsf_env_directory,
            lsf_library_directory=paths.lsf_library_directory,
            lsf_server_directory=paths.lsf_server_directory,
        )
        if status.kind is SchedulerStatusKind.QUEUED:
            changed = replace(
                step,
                state=ProjectStepState.QUEUED,
                last_error=None,
            )
            updated = self._persist_step(repository, project, changed)
            return _snapshot(
                updated,
                status_message=(
                    f"{scheduler_name} reports {status.scheduler_state}."
                ),
            )
        if status.kind is SchedulerStatusKind.RUNNING:
            changed = replace(
                step,
                state=ProjectStepState.RUNNING,
                last_error=None,
            )
            updated = self._persist_step(repository, project, changed)
            if (
                step.kind is ProjectStepKind.TRANSPORT_CONVERGENCE
                and status.scheduler_state == "RUNNING"
                and changed.slurm_output_filename is not None
            ):
                output_path = str(
                    PurePosixPath(remote_step_directory(updated, changed.kind))
                    / changed.slurm_output_filename
                )
                try:
                    output_tail = executor.read_file_tail(
                        output_path,
                        AIMS_OUTPUT_TAIL_MAX_BYTES,
                    )
                    task_oom_detected = (
                        has_slurm_task_oom_signature(output_tail)
                    )
                except (RemotePathNotFoundError, TransportEvidenceError):
                    pass
                else:
                    if task_oom_detected:
                        return _snapshot(
                            updated,
                            status_message=ACTIVE_TASK_OOM_DETAIL,
                            runtime_evidence=(
                                StepRuntimeEvidence.TASK_OOM_DETECTED
                            ),
                        )
            return _snapshot(
                updated,
                status_message=(
                    f"{scheduler_name} reports {status.scheduler_state}."
                ),
            )
        if status.kind is SchedulerStatusKind.ACCOUNTING_PENDING:
            return _snapshot(
                project,
                status_message=(
                    "Scheduler accounting has not reported the job yet. "
                    "Refresh again shortly."
                ),
                scheduler_status_kind=status.kind,
            )
        if status.kind is SchedulerStatusKind.UNRESOLVED:
            return _snapshot(
                project,
                status_message=(
                    "Scheduler returned an unrecognized state "
                    f"{status.scheduler_state}; persistent project state was retained."
                ),
                scheduler_status_kind=status.kind,
            )
        if status.kind is SchedulerStatusKind.FAILED:
            if step.kind is ProjectStepKind.TRANSMISSION:
                return self._assess_transmission_scheduler_failure(
                    executor,
                    repository,
                    project,
                    step,
                    status,
                    progress,
                )
            return self._assess_fhi_aims_scheduler_failure(
                executor,
                repository,
                project,
                step,
                status,
                profile,
            )
        if status.kind is SchedulerStatusKind.COMPLETED:
            return self._assess_completed(
                executor,
                repository,
                project,
                step,
                progress,
                scheduler_status=status,
            )
        raise AssertionError("unhandled scheduler status")

    def _reconcile_orca(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        scheduler_paths: Callable[[], _SchedulerPaths],
        profile: ServerProfile,
        progress: Callable[[str], None],
    ) -> ProjectRecoverySnapshot:
        """Reconcile ORCA without applying legacy FHI step assumptions."""

        step = _project_step(project, _active_step_kind(project))
        if step.state is ProjectStepState.NOT_STARTED:
            return self._orca_context_snapshot(
                executor,
                project,
                "ORCA stage has not started.",
            )
        if step.state is ProjectStepState.FAILED and step.job_id is None:
            return self._orca_context_snapshot(
                executor,
                project,
                step.last_error or "ORCA stage failed before submission.",
            )
        if (
            step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
            and step.state is ProjectStepState.RUNNING
        ):
            return self._orca_context_snapshot(
                executor,
                project,
                "ORCA WBL transmission is running.",
            )
        if step.state in {
            ProjectStepState.SUCCEEDED,
            ProjectStepState.SCHEDULER_COMPLETED,
        }:
            return self._recover_orca_stage(executor, repository, project, step)
        if step.state is ProjectStepState.UNKNOWN and step.job_id is None:
            return self._orca_context_snapshot(
                executor,
                project,
                (
                    "ORCA submission outcome is unknown and no Job ID is available. "
                    "Manual scheduler inspection is required."
                ),
            )
        if step.job_id is None:
            raise ProjectRecoveryError(
                f"ORCA stage state {step.state.value} has no Job ID"
            )
        validate_job_id(step.job_id)
        _require_scheduler_binding(profile, step)
        paths = scheduler_paths()
        scheduler_name = _scheduler_name(step)
        progress(f"Checking {scheduler_name} status for ORCA...")
        status = query_slurm_job_status(
            executor,
            squeue_path=paths.squeue,
            sacct_path=paths.sacct,
            job_id=step.job_id,
            profile_username=profile.username,
            lsf_env_directory=paths.lsf_env_directory,
            lsf_library_directory=paths.lsf_library_directory,
            lsf_server_directory=paths.lsf_server_directory,
        )
        if status.kind is SchedulerStatusKind.QUEUED:
            updated = self._persist_step(
                repository,
                project,
                replace(
                    step,
                    state=ProjectStepState.QUEUED,
                    scheduler_state=status.scheduler_state,
                    last_error=None,
                ),
            )
            return self._orca_context_snapshot(
                executor,
                updated,
                f"{scheduler_name} reports {status.scheduler_state}.",
                scheduler_status_kind=status.kind,
            )
        if status.kind is SchedulerStatusKind.RUNNING:
            updated = self._persist_step(
                repository,
                project,
                replace(
                    step,
                    state=ProjectStepState.RUNNING,
                    scheduler_state=status.scheduler_state,
                    last_error=None,
                ),
            )
            return self._orca_context_snapshot(
                executor,
                updated,
                f"{scheduler_name} reports {status.scheduler_state}.",
                scheduler_status_kind=status.kind,
            )
        if status.kind in {
            SchedulerStatusKind.ACCOUNTING_PENDING,
            SchedulerStatusKind.UNRESOLVED,
        }:
            return self._orca_context_snapshot(
                executor,
                project,
                (
                    "Scheduler accounting is not yet authoritative for the ORCA job."
                    if status.kind is SchedulerStatusKind.ACCOUNTING_PENDING
                    else f"Scheduler returned unrecognized state {status.scheduler_state}; "
                    "the persistent ORCA state was retained."
                ),
                scheduler_status_kind=status.kind,
            )
        if status.kind is SchedulerStatusKind.FAILED:
            message = (
                f"{scheduler_name} job ended with {status.scheduler_state} "
                f"(exit code {status.exit_code})"
            )
            updated = self._persist_step(
                repository,
                project,
                replace(
                    step,
                    state=ProjectStepState.FAILED,
                    scheduler_state=status.scheduler_state,
                    finished_at=step.finished_at or _aware_now(self._now_factory),
                    last_error=message,
                ),
            )
            return self._orca_context_snapshot(
                executor,
                updated,
                message,
                scheduler_status_kind=status.kind,
            )
        if status.kind is SchedulerStatusKind.COMPLETED:
            completed = self._persist_step(
                repository,
                project,
                replace(
                    step,
                    state=ProjectStepState.SCHEDULER_COMPLETED,
                    scheduler_state=status.scheduler_state or "COMPLETED",
                    finished_at=step.finished_at or _aware_now(self._now_factory),
                    last_error=None,
                ),
            )
            completed_step = _project_step(completed, step.kind)
            return self._recover_orca_stage(
                executor,
                repository,
                completed,
                completed_step,
            )
        raise AssertionError("unhandled ORCA scheduler status")

    def _orca_context_snapshot(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        status_message: str,
        *,
        scheduler_status_kind: SchedulerStatusKind | None = None,
    ) -> ProjectRecoverySnapshot:
        """Attach only ORCA-native geometry evidence to an in-progress snapshot."""

        wbl_presentation, wbl_diagnostic = (
            self._read_successful_orca_wbl_presentation(executor, project)
        )
        if wbl_diagnostic is not None:
            status_message = f"{status_message} {wbl_diagnostic}"
        active = _project_step(project, _active_step_kind(project))
        if active.kind is ProjectStepKind.ORCA_OPTIMIZATION:
            root = PurePosixPath(project.remote_project_path)
            input_bytes = _read_optional_remote_bytes(
                executor,
                str(root / ORCA_OPT_INPUT),
            )
            if input_bytes is None:
                diagnostic = f"Missing required ORCA artifact: {ORCA_OPT_INPUT}"
                return _snapshot(
                    project,
                    status_message=f"{status_message} Structure retrieval unavailable: {diagnostic}",
                    scheduler_status_kind=scheduler_status_kind,
                    orca_wbl_presentation=wbl_presentation,
                )
            diagnostic = _orca_input_hash_diagnostic(
                project.steps[0],
                ORCA_OPT_INPUT,
                input_bytes,
            )
            try:
                if diagnostic is not None:
                    raise OrcaEvidenceError(diagnostic)
                submitted = parse_rendered_orca_structure(input_bytes)
                if tuple(atom.element for atom in submitted) != active.orca_submitted_elements:
                    raise OrcaEvidenceError(
                        "Submitted ORCA atom identity differs from the manifest"
                    )
            except (ValueError, OrcaEvidenceError) as error:
                return _snapshot(
                    project,
                    status_message=(
                        f"{status_message} Structure retrieval unavailable: {error}"
                    ),
                    scheduler_status_kind=scheduler_status_kind,
                    orca_wbl_presentation=wbl_presentation,
                )
            return _snapshot(
                project,
                status_message=status_message,
                submitted_structure=submitted,
                connectivity=infer_connectivity(
                    submitted,
                    self._covalent_radii_loader(),
                ),
                scheduler_status_kind=scheduler_status_kind,
                orca_wbl_presentation=wbl_presentation,
            )

        try:
            optimized, connectivity = _recover_orca_optimized_geometry(
                executor,
                project,
                self._covalent_radii_loader(),
            )
        except ProjectRecoveryError as error:
            return _snapshot(
                project,
                status_message=(
                    f"{status_message} Structure retrieval unavailable: {error}"
                ),
                scheduler_status_kind=scheduler_status_kind,
                orca_wbl_presentation=wbl_presentation,
            )
        return _snapshot(
            project,
            status_message=status_message,
            optimized_structure=optimized,
            connectivity=connectivity,
            scheduler_status_kind=scheduler_status_kind,
            orca_wbl_presentation=wbl_presentation,
        )

    def _recover_orca_stage(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> ProjectRecoverySnapshot:
        if step.kind is ProjectStepKind.ORCA_OPTIMIZATION:
            return self._recover_orca_optimization(
                executor,
                repository,
                project,
                step,
            )
        if step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION:
            return self._recover_orca_wbl(executor, project, step)
        return self._recover_orca_frequency(
            executor,
            repository,
            project,
            step,
        )

    def _recover_orca_wbl(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> ProjectRecoverySnapshot:
        if step.state is ProjectStepState.SUCCEEDED:
            presentation, diagnostic = self._read_successful_orca_wbl_presentation(
                executor,
                project,
            )
        else:
            presentation = self._read_orca_wbl_presentation(executor, project, step)
            diagnostic = None
        optimized, connectivity = _recover_orca_optimized_geometry(
            executor,
            project,
            self._covalent_radii_loader(),
        )
        return _snapshot(
            project,
            status_message=(
                "ORCA WBL transmission artifacts were verified "
                f"({presentation.model_classification})."
                if presentation is not None
                else diagnostic or "ORCA WBL presentation is unavailable."
            ),
            optimized_structure=optimized,
            connectivity=connectivity,
            orca_wbl_presentation=presentation,
        )

    def _read_successful_orca_wbl_presentation(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
    ) -> tuple[OrcaWblPresentation | None, str | None]:
        step = next(
            (
                item
                for item in project.steps
                if item.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
                and item.state is ProjectStepState.SUCCEEDED
            ),
            None,
        )
        if step is None:
            return None, None
        try:
            return self._read_orca_wbl_presentation(executor, project, step), None
        except ProjectRecoveryError as error:
            return None, f"WBL presentation unavailable: {error}"

    def _read_orca_wbl_presentation(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> OrcaWblPresentation:
        evidence = step.orca_wbl_result
        if step.orca_wbl_settings is None or evidence is None:
            raise ProjectRecoveryError(
                "Successful ORCA WBL stage has incomplete manifest evidence"
            )
        root = PurePosixPath(remote_step_directory(project, step.kind))
        expected = dict(evidence.artifact_hashes)
        required = (WBL_JSON_FILENAME, WBL_CSV_FILENAME)
        if any(filename not in expected for filename in required):
            raise ProjectRecoveryError(
                "ORCA WBL manifest does not identify the required result artifacts"
            )
        contents: dict[str, bytes] = {}
        for filename in required:
            content = _read_optional_remote_bytes(executor, str(root / filename))
            if content is None:
                raise ProjectRecoveryError(
                    f"Missing required ORCA WBL artifact: {filename}"
                )
            if hashlib.sha256(content).hexdigest() != expected[filename]:
                raise ProjectRecoveryError(
                    f"ORCA WBL artifact hash mismatch: {filename}"
                )
            contents[filename] = content
        try:
            presentation = parse_wbl_presentation(
                contents[WBL_JSON_FILENAME],
                contents[WBL_CSV_FILENAME],
            )
        except OrcaWblArtifactError as error:
            raise ProjectRecoveryError(str(error)) from None
        source_hashes = dict(presentation.source_hashes)
        if (
            presentation.model_id != evidence.model_id
            or presentation.model_classification != evidence.model_classification
            or not _wbl_summary_matches(presentation, evidence)
            or source_hashes.get("orca_opt.gbw") != evidence.source_gbw_sha256
            or source_hashes.get("orca_wavefunction.json")
            != evidence.wavefunction_json_sha256
        ):
            raise ProjectRecoveryError(
                "ORCA WBL result content differs from its manifest evidence"
            )
        return presentation

    def _recover_orca_optimization(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> ProjectRecoverySnapshot:
        root = PurePosixPath(project.remote_project_path)
        input_bytes = _read_optional_remote_bytes(executor, str(root / ORCA_OPT_INPUT))
        output = _read_optional_remote_bytes(executor, str(root / ORCA_OPT_OUTPUT))
        submitted = None
        input_diagnostic = None
        if input_bytes is None:
            input_diagnostic = f"Missing required ORCA artifact: {ORCA_OPT_INPUT}"
        else:
            input_diagnostic = _orca_input_hash_diagnostic(
                step,
                ORCA_OPT_INPUT,
                input_bytes,
            )
            if input_diagnostic is None:
                try:
                    submitted = parse_rendered_orca_structure(input_bytes)
                    expected = step.orca_submitted_elements
                    if tuple(atom.element for atom in submitted) != expected:
                        raise OrcaEvidenceError(
                            "Submitted ORCA atom identity differs from the manifest"
                        )
                except (ValueError, OrcaEvidenceError) as error:
                    input_diagnostic = str(error)
                    submitted = None

        parsed = parse_orca_optimization_output(output) if output else None
        optimized = None
        xyz_bytes = _read_optional_remote_bytes(executor, str(root / "orca_opt.xyz"))
        xyz_diagnostic = None
        if submitted is not None and xyz_bytes is not None:
            try:
                optimized = parse_orca_final_xyz(xyz_bytes, submitted)
            except OrcaEvidenceError as error:
                xyz_diagnostic = str(error)
        elif xyz_bytes is None:
            xyz_diagnostic = "Missing required ORCA artifact: orca_opt.xyz"
        else:
            xyz_diagnostic = "Final geometry cannot be verified without submitted atom evidence"

        diagnostic = input_diagnostic
        if output is None:
            diagnostic = diagnostic or f"Missing required ORCA artifact: {ORCA_OPT_OUTPUT}"
        elif parsed is not None and parsed.explicit_nonconvergence:
            diagnostic = "ORCA reported optimization nonconvergence"
        elif parsed is not None and not parsed.normal_termination:
            diagnostic = "ORCA normal termination was not confirmed"
        elif parsed is not None and not parsed.optimization_converged:
            diagnostic = "ORCA optimization convergence was not confirmed"
        diagnostic = diagnostic or xyz_diagnostic
        gbw = _read_optional_remote_bytes(executor, str(root / "orca_opt.gbw"))
        evidence = OrcaOptimizationResultEvidence(
            scheduler_succeeded=True,
            normal_termination=bool(parsed and parsed.normal_termination),
            optimization_converged=bool(
                parsed
                and parsed.optimization_converged
                and not parsed.explicit_nonconvergence
            ),
            final_xyz_valid=optimized is not None,
            wbl_input_ready=bool(gbw),
            output_sha256=(hashlib.sha256(output).hexdigest() if output else None),
            xyz_sha256=(hashlib.sha256(xyz_bytes).hexdigest() if optimized is not None else None),
            gbw_sha256=(hashlib.sha256(gbw).hexdigest() if gbw else None),
            diagnostic=diagnostic,
        )
        if parsed is not None and parsed.explicit_nonconvergence:
            state = ProjectStepState.FAILED
        elif evidence.succeeded:
            state = ProjectStepState.SUCCEEDED
        else:
            state = ProjectStepState.SCHEDULER_COMPLETED
        changed = replace(
            step,
            state=state,
            scheduler_state=step.scheduler_state or "COMPLETED",
            finished_at=step.finished_at or _aware_now(self._now_factory),
            last_error=diagnostic,
            orca_optimization_result=evidence,
        )
        updated = self._persist_step(repository, project, changed)
        display_structure = optimized or submitted
        connectivity = (
            infer_connectivity(display_structure, self._covalent_radii_loader())
            if display_structure is not None
            else None
        )
        trajectory = _remote_nonempty(
            executor,
            str(root / "orca_opt_trj.xyz"),
        )
        return _snapshot(
            updated,
            status_message=(
                "ORCA optimization converged and the final geometry was verified; "
                + (
                    "future WBL input is available."
                    if evidence.wbl_input_ready
                    else "future WBL input is not ready because orca_opt.gbw is unavailable."
                )
                if evidence.succeeded
                else diagnostic or "ORCA optimization result is unverified."
            ),
            optimized_structure=optimized,
            connectivity=connectivity,
            submitted_structure=submitted,
            orca_output_bytes=output,
            orca_trajectory_available=trajectory,
        )

    def _recover_orca_frequency(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> ProjectRecoverySnapshot:
        root = PurePosixPath(project.remote_project_path)
        frequency_root = PurePosixPath(remote_step_directory(project, step.kind))
        output = _read_optional_remote_bytes(
            executor,
            str(frequency_root / ORCA_FREQ_OUTPUT),
        )
        frequency_input = _read_optional_remote_bytes(
            executor,
            str(frequency_root / ORCA_FREQ_INPUT),
        )
        hessian = _read_optional_remote_bytes(
            executor,
            str(frequency_root / "orca_freq.hess"),
        )
        input_diagnostic = (
            f"Missing required ORCA artifact: {ORCA_FREQ_INPUT}"
            if frequency_input is None
            else _orca_input_hash_diagnostic(
                step,
                ORCA_FREQ_INPUT,
                frequency_input,
            )
        )
        if input_diagnostic is not None:
            evidence = OrcaFrequencyEvidence(
                OrcaFrequencyCompletion.UNVERIFIED,
                OrcaImaginaryModeClassification.UNVERIFIED,
                (),
                None,
                input_diagnostic,
            )
        elif output is None:
            evidence = OrcaFrequencyEvidence(
                OrcaFrequencyCompletion.UNVERIFIED,
                OrcaImaginaryModeClassification.UNVERIFIED,
                (),
                None,
                f"Missing required ORCA artifact: {ORCA_FREQ_OUTPUT}",
            )
        else:
            evidence = parse_orca_frequency_evidence(
                output,
                hessian,
                atom_count=len(project.steps[0].orca_submitted_elements),
            )
        state = (
            ProjectStepState.SUCCEEDED
            if evidence.completion is OrcaFrequencyCompletion.FREQUENCY_COMPLETED
            else ProjectStepState.SCHEDULER_COMPLETED
        )
        changed = replace(
            step,
            state=state,
            scheduler_state=step.scheduler_state or "COMPLETED",
            finished_at=step.finished_at or _aware_now(self._now_factory),
            last_error=evidence.diagnostic,
            orca_frequency_result=evidence,
        )
        updated = self._persist_step(repository, project, changed)
        submitted = None
        optimized = None
        connectivity = None
        input_bytes = _read_optional_remote_bytes(executor, str(root / ORCA_OPT_INPUT))
        xyz_bytes = _read_optional_remote_bytes(executor, str(root / "orca_opt.xyz"))
        if input_bytes is not None:
            try:
                submitted = parse_rendered_orca_structure(input_bytes)
                if xyz_bytes is not None:
                    optimized = parse_orca_final_xyz(xyz_bytes, submitted)
                    connectivity = infer_connectivity(
                        optimized,
                        self._covalent_radii_loader(),
                    )
            except (ValueError, OrcaEvidenceError):
                optimized = None
                connectivity = None
        if state is ProjectStepState.SUCCEEDED:
            classification = evidence.imaginary_classification.value.replace("_", " ").lower()
            message = f"ORCA frequency completed; {classification}."
        else:
            message = evidence.diagnostic or "ORCA frequency result is unverified."
        wbl_presentation, wbl_diagnostic = (
            self._read_successful_orca_wbl_presentation(executor, updated)
        )
        if wbl_diagnostic is not None:
            message = f"{message} {wbl_diagnostic}"
        return _snapshot(
            updated,
            status_message=message,
            optimized_structure=optimized,
            connectivity=connectivity,
            submitted_structure=submitted,
            orca_output_bytes=output,
            orca_wbl_presentation=wbl_presentation,
        )

    def _scheduler_failure(
        self,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        status: SlurmJobStatus,
        *,
        failure_reason: str | None = None,
        step3_retry_preset: SlurmExecutionPreset | None = None,
        step3_retry_preparation_error: str | None = None,
    ) -> ProjectRecoverySnapshot:
        if failure_reason is not None:
            message = failure_reason
        elif (
            step.kind is not ProjectStepKind.TRANSMISSION
            and status.scheduler_state == "OUT_OF_MEMORY"
        ):
            message = "OUT_OF_MEMORY"
        elif step.kind is ProjectStepKind.TRANSPORT_CONVERGENCE:
            if status.scheduler_state == "TIMEOUT":
                message = TIMEOUT_REASON
            elif status.scheduler_state in _AUTHORITATIVE_SLURM_FAILURE_REASONS:
                message = status.scheduler_state
            else:
                message = "unknown"
        else:
            message = (
                f"{_scheduler_name(step)} job ended with {status.scheduler_state} "
                f"(exit code {status.exit_code})"
            )
        changed = replace(
            step,
            state=ProjectStepState.FAILED,
            finished_at=step.finished_at or _aware_now(self._now_factory),
            last_error=message,
            scheduler_state=status.scheduler_state,
        )
        updated = self._persist_step(repository, project, changed)
        return _snapshot(
            updated,
            status_message=slurm_failure_reason(message) or message,
            step3_retry_preset=step3_retry_preset,
            step3_retry_preparation_error=step3_retry_preparation_error,
        )

    def _assess_fhi_aims_scheduler_failure(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        status: SlurmJobStatus,
        profile: ServerProfile,
    ) -> ProjectRecoverySnapshot:
        """Optionally refine only an already-terminal FAILED parent from its output."""

        failure_reason = None
        if (
            status.scheduler_state in {"FAILED", "CANCELLED"}
            and step.slurm_output_filename
        ):
            output_path = str(
                PurePosixPath(remote_step_directory(project, step.kind))
                / step.slurm_output_filename
            )
            try:
                output_tail = executor.read_file_tail(
                    output_path,
                    AIMS_OUTPUT_TAIL_MAX_BYTES,
                )
                task_oom_detected = (
                    has_slurm_task_oom_signature(output_tail)
                )
            except (RemotePathNotFoundError, TransportEvidenceError):
                pass
            else:
                if task_oom_detected:
                    failure_reason = SLURM_TASK_OUT_OF_MEMORY
        retry_preset = None
        retry_preparation_error = None
        if step.kind is ProjectStepKind.TRANSPORT_CONVERGENCE:
            base_preset = profile.execution_preset
            if base_preset is None:
                retry_preparation_error = (
                    "Cluster Execution Settings are unavailable for Step-3 retry."
                )
            else:
                try:
                    script = _read_current_step3_submit_script(
                        executor,
                        project,
                        step,
                    )
                    retry_preset = preset_with_submit_script_resources(
                        base_preset,
                        script,
                    )
                except (
                    ProjectRecoveryError,
                    RemotePathNotFoundError,
                    SlurmSubmissionError,
                ) as error:
                    retry_preparation_error = _concise(str(error))
        return self._scheduler_failure(
            repository,
            project,
            step,
            status,
            failure_reason=failure_reason,
            step3_retry_preset=retry_preset,
            step3_retry_preparation_error=retry_preparation_error,
        )

    def _assess_completed(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        progress: Callable[[str], None],
        *,
        scheduler_status: SlurmJobStatus | None = None,
    ) -> ProjectRecoverySnapshot:
        if step.kind is ProjectStepKind.TRANSMISSION:
            if scheduler_status is None:
                raise ProjectRecoveryError(
                    "Step 4 success assessment requires authoritative scheduler "
                    "status"
                )
            return self._assess_transmission_completed(
                executor,
                repository,
                project,
                step,
                scheduler_status,
                progress,
            )
        if step.kind is ProjectStepKind.TRANSPORT_CONVERGENCE:
            return self._assess_transport_completed(
                executor,
                repository,
                project,
                step,
                progress,
            )
        progress("Reading completed calculation results...")
        step_directory = remote_step_directory(project, step.kind)
        try:
            submit_script = executor.read_bytes(
                str(PurePosixPath(step_directory) / "submit.sh")
            )
            output_filename = parse_submit_script_output_filename(submit_script)
        except (RemotePathNotFoundError, SlurmSubmissionError) as error:
            return self._scientific_failure(
                repository,
                project,
                step,
                "Submitted scheduler output metadata is unavailable or invalid: "
                f"{error}",
            )
        output_path = str(PurePosixPath(step_directory) / output_filename)
        try:
            output_tail = executor.read_file_tail(
                output_path,
                OUTPUT_TAIL_MAX_BYTES,
            )
        except RemotePathNotFoundError:
            changed = replace(
                step,
                state=ProjectStepState.SCHEDULER_COMPLETED,
                last_error=None,
            )
            updated = self._persist_step(repository, project, changed)
            return _snapshot(
                updated,
                status_message=(
                    "Scheduler completed; FHI-aims output is not yet available. "
                    "Refresh again."
                ),
            )

        try:
            assessment = assess_geometry_optimization_output(output_tail)
        except AimsRecoveryError as error:
            return self._scientific_failure(
                repository,
                project,
                step,
                str(error),
            )
        if not assessment.succeeded:
            return self._scientific_failure(
                repository,
                project,
                step,
                assessment.message or "FHI-aims scientific success was not confirmed.",
            )

        try:
            structure, connectivity, control_text = self._recover_structure(
                executor,
                project,
                step.kind,
            )
        except RemotePathNotFoundError as error:
            return self._scientific_failure(
                repository,
                project,
                step,
                f"Required optimization recovery file is missing: {error}",
            )
        except (AimsRecoveryError, TypeError, ValueError) as error:
            return self._scientific_failure(
                repository,
                project,
                step,
                str(error),
            )

        changed = replace(
            step,
            state=ProjectStepState.SUCCEEDED,
            finished_at=_aware_now(self._now_factory),
            last_error=None,
        )
        updated = self._persist_step(repository, project, changed)
        orbital_catalog = self._orbital_cube_catalog(
            executor,
            updated,
            step.kind,
            control_text,
        )
        continuation_note = (
            " Transport continuation is not implemented yet."
            if step.kind is ProjectStepKind.MOLECULE_AU_OPT
            else ""
        )
        convergence_note = (
            " Geometry convergence marker was detected."
            if assessment.geometry_convergence_detected
            else ""
        )
        return _snapshot(
            updated,
            status_message=(
                f"{_step_label(step.kind)} completed successfully. "
                "FHI-aims terminated normally; "
                "geometry.in.next_step was recovered."
                + convergence_note
                + continuation_note
            ),
            optimized_structure=structure,
            connectivity=connectivity,
            orbital_cubes=orbital_catalog.available,
            orbital_cube_diagnostic=orbital_catalog.diagnostic,
        )

    def _assess_transport_completed(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        progress: Callable[[str], None],
    ) -> ProjectRecoverySnapshot:
        """Assess fixed-geometry Step 3 without optimization-only evidence."""

        progress("Reading completed Step-3 transport evidence...")
        try:
            structure, connectivity, evidence = self._read_transport_evidence(
                executor,
                project,
                step,
            )
        except TransportEvidenceError as error:
            return self._scientific_failure(
                repository,
                project,
                step,
                str(error),
                scheduler_state="COMPLETED",
            )
        changed = replace(
            step,
            state=ProjectStepState.SUCCEEDED,
            finished_at=_aware_now(self._now_factory),
            last_error=None,
            scheduler_state="COMPLETED",
            submit_script_filename=step.submit_script_filename or "submit.sh",
            slurm_output_filename=(
                step.slurm_output_filename
                or self._step_output_filename(executor, project, step)
            ),
        )
        updated = self._persist_step(repository, project, changed)
        return self._transport_snapshot(
            updated,
            structure,
            connectivity,
            evidence,
            "Step 3 completed successfully; fixed geometry.in and all required "
            "AITRANSS prerequisite evidence were validated.",
        )

    def _assess_transmission_completed(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        status: SlurmJobStatus,
        progress: Callable[[str], None],
    ) -> ProjectRecoverySnapshot:
        """Require real positive completion evidence and a valid authoritative result."""

        progress("Reading terminal Step-4 output...")
        output_tail, output_filename = self._read_transmission_output(
            executor,
            project,
            step,
        )
        failure = (
            classify_aitranss_fatal_output(output_tail)
            if output_tail is not None
            else None
        )
        if failure is not None:
            return self._aitranss_failure(
                executor,
                repository,
                project,
                step,
                failure,
                output_filename,
                scheduler_state="COMPLETED",
            )
        if status.exit_code != "0:0":
            return self._scheduler_failure(repository, project, step, status)
        try:
            (
                result,
                result_filename,
                provenance_note,
            ) = self._read_validated_transmission_result(
                executor,
                project,
                step,
                output_tail,
            )
        except (RemotePathNotFoundError, TransmissionDataError) as error:
            changed = replace(
                step,
                state=ProjectStepState.SCHEDULER_COMPLETED,
                finished_at=step.finished_at or _aware_now(self._now_factory),
                last_error=None,
                scheduler_state="COMPLETED",
            )
            updated = self._persist_step(repository, project, changed)
            detail = _concise(str(error))
            output_note = (
                f" Current-attempt output: {output_filename}."
                if output_filename is not None
                else " The current-attempt output filename is unavailable."
            )
            return _snapshot(
                updated,
                status_message=(
                    "Step 4 scheduler completed; scientific success remains "
                    f"unassessed: {detail}." + output_note
                ),
                transmission_result_error=detail,
            )

        changed = replace(
            step,
            state=ProjectStepState.SUCCEEDED,
            finished_at=step.finished_at or _aware_now(self._now_factory),
            last_error=None,
            scheduler_state="COMPLETED",
        )
        updated = self._persist_step(repository, project, changed)
        return _snapshot(
            updated,
            status_message=(
                "Step 4 completed successfully; AITRANSS positive completion "
                f"evidence and {len(result.points)} points from "
                f"{result_filename} were validated."
                + (f" {provenance_note}" if provenance_note is not None else "")
            ),
            transmission_result=result,
            transmission_result_filename=result_filename,
        )

    def _assess_transmission_scheduler_failure(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        status: SlurmJobStatus,
        progress: Callable[[str], None],
    ) -> ProjectRecoverySnapshot:
        """Prefer a reviewed AITRANSS fatal over a generic terminal status."""

        progress("Reading terminal Step-4 output...")
        output_tail, output_filename = self._read_transmission_output(
            executor,
            project,
            step,
        )
        failure = (
            classify_aitranss_fatal_output(output_tail)
            if output_tail is not None
            else None
        )
        if failure is not None:
            return self._aitranss_failure(
                executor,
                repository,
                project,
                step,
                failure,
                output_filename,
                scheduler_state=status.scheduler_state,
            )
        return self._scheduler_failure(repository, project, step, status)

    def _read_transmission_output(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> tuple[bytes | None, str | None]:
        """Read only the output explicitly owned by the current Step-4 record."""

        output_filename = step.slurm_output_filename
        if output_filename is None:
            return None, None
        output_path = str(
            PurePosixPath(remote_step_directory(project, step.kind))
            / output_filename
        )
        try:
            output_tail = executor.read_file_tail(
                output_path,
                AITRANSS_OUTPUT_TAIL_MAX_BYTES,
            )
        except RemotePathNotFoundError:
            return None, output_filename
        return output_tail, output_filename

    def _read_validated_transmission_result(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
        output_tail: bytes | None,
    ) -> tuple[TransmissionResult, str, str | None]:
        """Validate current-attempt output and the result named by active tcontrol."""

        if output_tail is None:
            raise TransmissionDataError(
                "current-attempt AITRANSS output is unavailable"
            )
        directory = PurePosixPath(
            remote_step_directory(project, ProjectStepKind.TRANSMISSION)
        )
        tcontrol = executor.read_bytes(str(directory / "tcontrol"))
        request = parse_transmission_request(tcontrol)
        if not has_aitranss_transmission_success(
            output_tail,
            expected_output_filename=request.output_filename,
        ):
            raise TransmissionDataError(
                "established AITRANSS positive completion markers are incomplete"
            )
        result_path = str(directory / request.output_filename)
        stat = executor.stat(result_path)
        if stat.is_directory or stat.size is None or stat.size <= 0:
            raise TransmissionDataError(
                f"{request.output_filename} is missing or empty"
            )
        if stat.size > TRANSMISSION_RESULT_MAX_BYTES:
            raise TransmissionDataError(
                f"{request.output_filename} exceeds the reviewed result-size limit"
            )
        data = executor.read_bytes(result_path)
        if len(data) != stat.size:
            raise TransmissionDataError(
                f"{request.output_filename} changed while it was being read"
            )
        result = parse_te_dat(data, source_name=request.output_filename)
        validate_transmission_grid(result, request)
        provenance_note = _tcontrol_provenance_note(
            tcontrol,
            dict(step.input_hashes).get("tcontrol"),
        )
        return result, request.output_filename, provenance_note

    def _recover_succeeded_transmission(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> ProjectRecoverySnapshot:
        output_tail, output_filename = self._read_transmission_output(
            executor,
            project,
            step,
        )
        try:
            (
                result,
                result_filename,
                provenance_note,
            ) = self._read_validated_transmission_result(
                executor,
                project,
                step,
                output_tail,
            )
        except (RemotePathNotFoundError, TransmissionDataError) as error:
            detail = _concise(str(error))
            return _snapshot(
                project,
                status_message=(
                    "Step 4 is recorded as successful, but its authoritative "
                    f"transmission result cannot currently be opened: {detail}."
                ),
                transmission_result_error=detail,
            )
        return _snapshot(
            project,
            status_message=(
                "Step 4 is recorded as successful; "
                f"{len(result.points)} points from {result_filename} were validated."
                + (f" {provenance_note}" if provenance_note is not None else "")
            ),
            transmission_result=result,
            transmission_result_filename=result_filename,
        )

    def _aitranss_failure(
        self,
        executor: RemoteExecutor,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        failure: AitranssFailureCode,
        output_filename: str | None,
        *,
        scheduler_state: str,
    ) -> ProjectRecoverySnapshot:
        reason = aitranss_failure_reason(failure)
        detail = aitranss_failure_detail(failure)
        if reason is None or detail is None:
            raise AssertionError("unhandled AITRANSS failure code")
        changed = replace(
            step,
            state=ProjectStepState.FAILED,
            finished_at=step.finished_at or _aware_now(self._now_factory),
            last_error=failure.value,
            scheduler_state=scheduler_state,
        )
        updated = self._persist_step(repository, project, changed)
        attempt = f"Job {step.job_id}" if step.job_id is not None else "Step-4 job"
        output = output_filename or "unavailable current-attempt output"
        message = (
            f"{reason}。{detail} "
            f"{attempt}; output: {output}."
        )
        if failure is not AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP:
            return _snapshot(updated, status_message=message)
        try:
            step3 = _project_step(updated, ProjectStepKind.TRANSPORT_CONVERGENCE)
            structure, connectivity, evidence = self._read_transport_evidence(
                executor,
                updated,
                step3,
            )
            surface = resolve_project_electrode_surfaces(updated, structure)
            directory = remote_step_directory(updated, ProjectStepKind.TRANSMISSION)
            attempt01 = executor.read_bytes(
                str(PurePosixPath(directory) / "tcontrol")
            )
            accepted_hash = dict(changed.input_hashes).get("tcontrol")
            if (
                accepted_hash is None
                or hashlib.sha256(attempt01).hexdigest() != accepted_hash
            ):
                raise TControlError(
                    "attempt-1 tcontrol SHA256 does not match persisted provenance"
                )
            parsed = parse_tcontrol(attempt01, structure)
            if parsed.self_energy_filename is not None:
                raise TControlError(
                    "failed attempt already references an explicit self-energy"
                )
            if (
                parsed.settings.natoms != evidence.natoms
                or parsed.settings.nsaos != evidence.nsaos
                or parsed.spin_mode is not evidence.spin_mode
            ):
                raise TControlError(
                    "attempt-1 tcontrol disagrees with validated Step-3 evidence"
                )
            plan = build_partitioned_self_energy_plan(
                structure,
                surface,
                parsed.settings,
            )
        except (
            ElectrodeSurfaceError,
            RemotePathNotFoundError,
            SelfEnergyError,
            TControlError,
            TransportEvidenceError,
        ) as error:
            preparation_error = _concise(str(error))
            return _snapshot(
                updated,
                status_message=(
                    message
                    + " Explicit-self-energy retry preparation is unavailable: "
                    + preparation_error
                ),
                transport_preparation_error=preparation_error,
            )
        return _snapshot(
            updated,
            status_message=(
                message
                + " A reviewed retry can define the interface from Phase-2D "
                "electrode provenance without changing geometry."
            ),
            optimized_structure=structure,
            connectivity=connectivity,
            transport_evidence=evidence,
            surface_proposal=surface,
            step4_attempt01_tcontrol=attempt01,
            step4_tcontrol_settings=parsed.settings,
            step4_self_energy_plan=plan,
        )

    def _recover_succeeded_transport(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> ProjectRecoverySnapshot:
        structure, connectivity, evidence = self._read_transport_evidence(
            executor,
            project,
            step,
        )
        return self._transport_snapshot(
            project,
            structure,
            connectivity,
            evidence,
            "Step 3 succeeded; exact transport geometry and surface proposal recovered.",
        )

    def _transport_snapshot(
        self,
        project: CalculationProject,
        structure: MolecularStructure,
        connectivity: Connectivity,
        evidence: TransportCompletionEvidence,
        message: str,
    ) -> ProjectRecoverySnapshot:
        surface: ElectrodeSurfaceProposal | None = None
        preparation_error: str | None = None
        try:
            surface = resolve_project_electrode_surfaces(project, structure)
        except ElectrodeSurfaceError as error:
            preparation_error = _concise(str(error))
            message += " Step-4 surface preparation is unavailable: " + preparation_error
        return _snapshot(
            project,
            status_message=message,
            optimized_structure=structure,
            connectivity=connectivity,
            transport_evidence=evidence,
            surface_proposal=surface,
            transport_preparation_error=preparation_error,
        )

    def _read_transport_evidence(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> tuple[MolecularStructure, Connectivity, TransportCompletionEvidence]:
        directory = remote_step_directory(project, step.kind)
        output_filename = self._step_output_filename(executor, project, step)
        output_path = str(PurePosixPath(directory) / output_filename)
        _require_nonempty_remote_file(executor, output_path, output_filename)
        output_tail = executor.read_file_tail(
            output_path,
            AIMS_OUTPUT_TAIL_MAX_BYTES,
        )
        if not has_exact_normal_termination(output_tail):
            raise TransportEvidenceError("unknown")

        geometry_path = str(PurePosixPath(directory) / "geometry.in")
        control_path = str(PurePosixPath(directory) / "control.in")
        _require_nonempty_remote_file(executor, geometry_path, "geometry.in")
        _require_nonempty_remote_file(executor, control_path, "control.in")
        geometry_text = executor.read_bytes(geometry_path)
        control_text = executor.read_bytes(control_path)
        geometry_sha256 = hashlib.sha256(geometry_text).hexdigest()
        expected_geometry_hash = dict(step.input_hashes).get("geometry.in")
        if (
            expected_geometry_hash is not None
            and geometry_sha256 != expected_geometry_hash
        ):
            raise TransportEvidenceError(
                "Step-3 geometry.in SHA256 does not match the accepted input"
            )
        try:
            species = parse_control_species_elements(control_text)
            structure = parse_molecular_geometry(
                geometry_text,
                species,
                source_name="geometry.in",
            )
        except AimsRecoveryError as error:
            raise TransportEvidenceError(str(error)) from None
        spin_mode = parse_transport_spin_mode(control_text)

        for filename in ("basis-indices.out", "omat.aims"):
            _require_nonempty_remote_file(
                executor,
                str(PurePosixPath(directory) / filename),
                filename,
            )
        orbital_filenames = (
            ("alpha.aims", "beta.aims")
            if spin_mode.value == "collinear"
            else ("mos.aims",)
        )
        nsaos_values: list[int] = []
        for filename in orbital_filenames:
            path = str(PurePosixPath(directory) / filename)
            _require_nonempty_remote_file(executor, path, filename)
            nsaos_values.append(
                parse_aims_nsaos(
                    executor.read_file_head(path, ORBITAL_HEADER_MAX_BYTES),
                    source_name=filename,
                )
            )
        if len(set(nsaos_values)) != 1:
            raise TransportEvidenceError(
                "Step-3 alpha/beta NSAOS values do not agree"
            )
        natoms = parse_aims_natoms(
            executor.read_file_head(output_path, AIMS_OUTPUT_HEAD_MAX_BYTES)
        )
        if natoms != len(structure):
            raise TransportEvidenceError(
                "Step-3 atom count mismatch: aims.dft.out reports "
                f"{natoms}, but geometry.in contains {len(structure)} atom records"
            )
        evidence = TransportCompletionEvidence(
            natoms,
            nsaos_values[0],
            spin_mode,
            geometry_sha256,
        )
        connectivity = infer_connectivity(
            structure,
            self._covalent_radii_loader(),
        )
        return structure, connectivity, evidence

    def _step_output_filename(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step: ProjectStepRecord,
    ) -> str:
        if step.slurm_output_filename is not None:
            return step.slurm_output_filename
        directory = remote_step_directory(project, step.kind)
        script_filename = step.submit_script_filename or "submit.sh"
        try:
            script = executor.read_bytes(
                str(PurePosixPath(directory) / script_filename)
            )
        except RemotePathNotFoundError:
            raise TransportEvidenceError(
                f"Missing required Step-3 input: {script_filename}"
            ) from None
        try:
            return parse_submit_script_output_filename(script)
        except SlurmSubmissionError as error:
            raise TransportEvidenceError(
                "Submitted scheduler output metadata is unavailable or invalid: "
                f"{error}"
            ) from None

    def _recover_structure(
        self,
        executor: RemoteExecutor,
        project: CalculationProject,
        step_kind: ProjectStepKind,
    ) -> tuple[MolecularStructure, Connectivity, bytes]:
        step_directory = remote_step_directory(project, step_kind)
        control_text = executor.read_bytes(
            str(PurePosixPath(step_directory) / "control.in")
        )
        original_geometry = executor.read_bytes(
            str(PurePosixPath(step_directory) / "geometry.in")
        )
        next_geometry = executor.read_bytes(
            str(PurePosixPath(step_directory) / "geometry.in.next_step")
        )
        structure = recover_optimized_structure(
            control_text=control_text,
            original_geometry_text=original_geometry,
            next_geometry_text=next_geometry,
        )
        connectivity = infer_connectivity(
            structure,
            self._covalent_radii_loader(),
        )
        return structure, connectivity, control_text

    @staticmethod
    def _orbital_cube_catalog(
        executor: RemoteExecutor,
        project: CalculationProject,
        step_kind: ProjectStepKind,
        control_text: bytes,
    ) -> ProjectOrbitalCubeCatalog:
        if step_kind is not ProjectStepKind.MOLECULE_OPT:
            return ProjectOrbitalCubeCatalog()
        return discover_step1_orbital_cubes(
            executor,
            remote_step_directory(project, step_kind),
            control_text,
        )

    def _scientific_failure(
        self,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        step: ProjectStepRecord,
        message: str,
        *,
        scheduler_state: str | None = None,
    ) -> ProjectRecoverySnapshot:
        concise = _concise(message)
        changed = replace(
            step,
            state=ProjectStepState.FAILED,
            finished_at=step.finished_at or _aware_now(self._now_factory),
            last_error=concise,
            scheduler_state=scheduler_state or step.scheduler_state,
        )
        updated = self._persist_step(repository, project, changed)
        return _snapshot(updated, status_message=concise)

    def _persist_step(
        self,
        repository: RemoteProjectRepository,
        project: CalculationProject,
        changed_step: ProjectStepRecord,
    ) -> CalculationProject:
        candidate = replace(
            project,
            steps=tuple(
                changed_step if item.kind is changed_step.kind else item
                for item in project.steps
            ),
        )
        if candidate == project:
            return project
        return repository.persist_update(
            candidate,
            updated_at=_aware_now(self._now_factory),
            preserve_remote_errors=True,
        )

    def _mark_seen(
        self,
        project: CalculationProject,
        profile: ServerProfile,
    ) -> None:
        self._local_index_repository.mark_seen(
            project,
            bound_server_profile_id=profile.profile_id,
        )


def _resolve_scheduler_paths(
    executor: RemoteExecutor,
    profile: ServerProfile,
) -> _SchedulerPaths:
    preset = profile.execution_preset
    if preset is None:
        raise ProjectRecoveryError(
            f"Configure Cluster Execution Settings for {profile.name} before "
            "refreshing project status."
        )
    resolved = resolve_slurm_for_submission(executor, preset)
    effective = replace(preset, slurm_bin_directory=resolved.bin_directory)
    return _SchedulerPaths(
        slurm_command_path(effective, "squeue"),
        slurm_command_path(effective, "sacct"),
        slurm_command_path(effective, "scancel"),
        resolved.lsf_env_directory,
        resolved.lsf_library_directory,
        resolved.lsf_server_directory,
    )


def _require_scheduler_binding(
    profile: ServerProfile,
    step: ProjectStepRecord,
) -> None:
    """Never query a persisted Job ID through a different scheduler type."""

    preset = profile.execution_preset
    if preset is None or step.scheduler_kind is None:
        return
    if preset.scheduler_kind is not step.scheduler_kind:
        raise ProjectRecoveryError(
            f"This job was submitted through {step.scheduler_kind.value}, but the "
            f"current server profile is configured for {preset.scheduler_kind.value}. "
            "Restore the matching scheduler setting before refreshing or cancelling."
        )


def _read_current_step3_submit_script(
    executor: RemoteExecutor,
    project: CalculationProject,
    step: ProjectStepRecord,
) -> bytes:
    """Read and hash-bind the manifest-selected current Step-3 script."""

    if step.kind is not ProjectStepKind.TRANSPORT_CONVERGENCE:
        raise ProjectRecoveryError(
            "Step-3 submit-script provenance requires the Step-3 record."
        )
    script_filename = step.submit_script_filename
    if script_filename is None:
        raise ProjectRecoveryError(
            "The current Step-3 submit-script filename is unavailable."
        )
    expected_hash = dict(step.input_hashes).get(script_filename)
    if expected_hash is None:
        raise ProjectRecoveryError(
            f"The accepted SHA256 is unavailable for {script_filename}."
        )
    script_path = str(
        PurePosixPath(remote_step_directory(project, step.kind))
        / script_filename
    )
    script = executor.read_bytes(script_path)
    actual_hash = hashlib.sha256(script).hexdigest()
    if actual_hash != expected_hash:
        raise ProjectRecoveryError(
            f"The current Step-3 submit script changed: {script_filename}."
        )
    return script


def _close_without_replacing_outcome(executor: RemoteExecutor) -> None:
    """Best-effort cleanup after the command outcome has been classified."""

    try:
        executor.close()
    except Exception:
        pass


def _close_stoppable_executor(
    executor: RemoteExecutor,
    stop_token: RemoteOperationStopToken | None,
) -> None:
    """Release one refresh session without masking an accepted stop request."""

    if stop_token is not None:
        stop_token.unbind_executor(executor)
    try:
        executor.close()
    except Exception:
        if stop_token is None or not stop_token.is_requested:
            raise


def _active_step_kind(project: CalculationProject) -> ProjectStepKind:
    for step in reversed(project.steps):
        kind = step.kind
        state = step.state
        if state not in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}:
            return kind
    # A freshly initialized direct-start project can be recovered between its
    # durable initial manifest and the first submission-state update.
    starting_step = project.starting_step
    if starting_step is None:  # CalculationProject validation makes this unreachable.
        raise ProjectRecoveryError("managed project has no recorded starting step")
    return starting_step


def _snapshot(
    project: CalculationProject,
    *,
    status_message: str,
    optimized_structure: MolecularStructure | None = None,
    connectivity: Connectivity | None = None,
    requires_profile_rebind: bool = False,
    profile_rebind_confirmed: bool = False,
    transport_evidence: TransportCompletionEvidence | None = None,
    surface_proposal: ElectrodeSurfaceProposal | None = None,
    transport_preparation_error: str | None = None,
    step4_attempt01_tcontrol: bytes | None = None,
    step4_tcontrol_settings: TControlSettings | None = None,
    step4_self_energy_plan: PartitionedSelfEnergyPlan | None = None,
    transmission_result: TransmissionResult | None = None,
    transmission_result_filename: str | None = None,
    transmission_result_error: str | None = None,
    runtime_evidence: StepRuntimeEvidence | None = None,
    step3_retry_preset: SlurmExecutionPreset | None = None,
    step3_retry_preparation_error: str | None = None,
    scheduler_status_kind: SchedulerStatusKind | None = None,
    orbital_cubes: tuple[ProjectOrbitalCubeArtifact, ...] = (),
    orbital_cube_diagnostic: str | None = None,
    submitted_structure: MolecularStructure | None = None,
    orca_output_bytes: bytes | None = None,
    orca_trajectory_available: bool = False,
    orca_wbl_presentation: OrcaWblPresentation | None = None,
) -> ProjectRecoverySnapshot:
    return ProjectRecoverySnapshot(
        project=project,
        active_step_kind=_active_step_kind(project),
        status_message=status_message,
        optimized_structure=optimized_structure,
        connectivity=connectivity,
        requires_profile_rebind=requires_profile_rebind,
        profile_rebind_confirmed=profile_rebind_confirmed,
        transport_evidence=transport_evidence,
        surface_proposal=surface_proposal,
        transport_preparation_error=transport_preparation_error,
        step4_attempt01_tcontrol=step4_attempt01_tcontrol,
        step4_tcontrol_settings=step4_tcontrol_settings,
        step4_self_energy_plan=step4_self_energy_plan,
        transmission_result=transmission_result,
        transmission_result_filename=transmission_result_filename,
        transmission_result_error=transmission_result_error,
        runtime_evidence=runtime_evidence,
        step3_retry_preset=step3_retry_preset,
        step3_retry_preparation_error=step3_retry_preparation_error,
        scheduler_status_kind=scheduler_status_kind,
        orbital_cubes=orbital_cubes,
        orbital_cube_diagnostic=orbital_cube_diagnostic,
        submitted_structure=submitted_structure,
        orca_output_bytes=orca_output_bytes,
        orca_trajectory_available=orca_trajectory_available,
        orca_wbl_presentation=orca_wbl_presentation,
    )


def _read_optional_remote_bytes(
    executor: RemoteExecutor,
    path: str,
) -> bytes | None:
    try:
        data = executor.read_bytes(path)
    except RemotePathNotFoundError:
        return None
    return data or None


def _remote_nonempty(executor: RemoteExecutor, path: str) -> bool:
    try:
        item = executor.stat(path)
    except RemotePathNotFoundError:
        return False
    return not item.is_directory and bool(item.size)


def _tcontrol_provenance_note(
    tcontrol: bytes,
    expected_sha256: str | None,
) -> str | None:
    """Report byte provenance diagnostically without deciding result success."""

    if expected_sha256 is None:
        return (
            "Submitted tcontrol SHA256 is unavailable; success was determined "
            "from current-attempt output and semantic result validation."
        )
    if hashlib.sha256(tcontrol).hexdigest() == expected_sha256:
        return None
    try:
        submitted = submitted_tcontrol_bytes(tcontrol)
    except TransmissionDataError:
        submitted = None
    if (
        submitted is not None
        and hashlib.sha256(submitted).hexdigest() == expected_sha256
    ):
        return None
    return (
        "Current tcontrol differs byte-for-byte from its submitted SHA256; "
        "success was determined from current-attempt output and semantic result "
        "validation."
    )


def _project_step(
    project: CalculationProject,
    step_kind: ProjectStepKind,
) -> ProjectStepRecord:
    return next(step for step in project.steps if step.kind is step_kind)


def _step_label(step_kind: ProjectStepKind) -> str:
    return {
        ProjectStepKind.MOLECULE_OPT: "Step 1",
        ProjectStepKind.MOLECULE_AU_OPT: "Step 2",
        ProjectStepKind.TRANSPORT_CONVERGENCE: "Step 3",
        ProjectStepKind.TRANSMISSION: "Step 4",
        ProjectStepKind.ORCA_OPTIMIZATION: "ORCA optimization",
        ProjectStepKind.ORCA_WBL_TRANSMISSION: "ORCA WBL transmission",
        ProjectStepKind.ORCA_FREQUENCY: "ORCA frequency",
    }[step_kind]


def _recover_orca_optimized_geometry(
    executor: RemoteExecutor,
    project: CalculationProject,
    covalent_radii,
) -> tuple[MolecularStructure, Connectivity]:
    root = PurePosixPath(project.remote_project_path)
    optimization = project.steps[0]
    input_bytes = _read_optional_remote_bytes(executor, str(root / ORCA_OPT_INPUT))
    xyz_bytes = _read_optional_remote_bytes(executor, str(root / "orca_opt.xyz"))
    if input_bytes is None or xyz_bytes is None:
        raise ProjectRecoveryError(
            "Verified ORCA optimized geometry artifacts are unavailable"
        )
    diagnostic = _orca_input_hash_diagnostic(
        optimization,
        ORCA_OPT_INPUT,
        input_bytes,
    )
    if diagnostic is not None:
        raise ProjectRecoveryError(diagnostic)
    try:
        submitted = parse_rendered_orca_structure(input_bytes)
        optimized = parse_orca_final_xyz(xyz_bytes, submitted)
    except (ValueError, OrcaEvidenceError) as error:
        raise ProjectRecoveryError(str(error)) from None
    evidence = optimization.orca_optimization_result
    if (
        evidence is None
        or evidence.xyz_sha256 is None
        or hashlib.sha256(xyz_bytes).hexdigest() != evidence.xyz_sha256
    ):
        raise ProjectRecoveryError(
            "Current ORCA optimized geometry differs from verified evidence"
        )
    return optimized, infer_connectivity(
        optimized,
        covalent_radii,
    )


def _wbl_summary_matches(presentation, evidence) -> bool:
    if presentation.spin_treatment is not evidence.spin_treatment:
        return False
    if not isclose(
        presentation.t_total_at_fermi,
        evidence.t_total_at_fermi,
        rel_tol=1.0e-11,
        abs_tol=1.0e-14,
    ):
        return False
    if (
        presentation.spin_treatment
        is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
    ):
        return presentation.top_total == evidence.top_total
    return all(
        isclose(left, right, rel_tol=1.0e-11, abs_tol=1.0e-14)
        for left, right in (
            (presentation.t_alpha_at_fermi, evidence.t_alpha_at_fermi),
            (presentation.t_beta_at_fermi, evidence.t_beta_at_fermi),
        )
    ) and (
        presentation.top_alpha == evidence.top_alpha
        and presentation.top_beta == evidence.top_beta
    )


def _orca_input_hash_diagnostic(
    step: ProjectStepRecord,
    filename: str,
    content: bytes,
) -> str | None:
    expected = dict(step.input_hashes).get(filename)
    if expected is None:
        return f"Missing submitted SHA256 evidence for {filename}"
    if hashlib.sha256(content).hexdigest() != expected:
        return f"Submitted ORCA artifact hash mismatch: {filename}"
    return None


def _scheduler_name(step: ProjectStepRecord) -> str:
    """Name the scheduler recorded for this attempt, including legacy records."""

    return scheduler_display_name(step.scheduler_kind or SchedulerKind.SLURM)


def _require_nonempty_remote_file(
    executor: RemoteExecutor,
    path: str,
    filename: str,
) -> None:
    try:
        stat = executor.stat(path)
    except RemotePathNotFoundError:
        raise TransportEvidenceError(
            f"Missing required Step-3 output: {filename}"
        ) from None
    if stat.is_directory or stat.size is None or stat.size <= 0:
        raise TransportEvidenceError(
            f"Missing required Step-3 output: {filename}"
        )


def _validate_selected_root(
    profile: ServerProfile,
    remote_project_path: str,
) -> None:
    if (
        not isinstance(remote_project_path, str)
        or PurePosixPath(remote_project_path).parent
        != PurePosixPath(profile.remote_project_root)
    ):
        raise ProjectRecoveryError(
            "project recovery is limited to first-level managed projects in the "
            "selected server workspace"
        )


def _require_profile(profile: ServerProfile) -> None:
    if not isinstance(profile, ServerProfile):
        raise TypeError("project recovery requires a ServerProfile")


def _aware_now(now_factory: Callable[[], datetime]) -> datetime:
    value = now_factory()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("project recovery timestamps must be timezone-aware")
    return value


def _concise(message: str) -> str:
    first_line = next(
        (line.strip() for line in str(message).splitlines() if line.strip()),
        "Scientific optimization success was not confirmed.",
    )
    return first_line[:400]


def _progress_reporter(
    progress: Callable[[str], None] | None,
    stop_token: RemoteOperationStopToken | None = None,
) -> Callable[[str], None]:
    """Keep optional UI progress strictly non-authoritative."""

    def report(message: str) -> None:
        if stop_token is not None:
            stop_token.checkpoint()
        if progress is None:
            return
        try:
            progress(message)
        except RemoteOperationStopped:
            # Cancellation is control flow, not a presentation-only failure.
            raise
        except Exception:
            # Presentation failure must not change recovery state or control flow.
            return

    return report


_AUTHORITATIVE_SLURM_FAILURE_REASONS = frozenset(
    {
        "BOOT_FAIL",
        "CANCELLED",
        "DEADLINE",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
    }
)
