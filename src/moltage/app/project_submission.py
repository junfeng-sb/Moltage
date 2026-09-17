"""Create one managed project and submit one verified Step 1, 2, or 3 job."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import hashlib
import logging
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from moltage.aims.input_bundle import AimsInputBundle, AimsOptimizationInputPlan
from moltage.aims.transport_convergence_bundle import (
    TransportConvergenceAimsInputs,
    TransportConvergenceInputPlan,
)
from moltage.app.connection_service import ServerConnectionService
from moltage.app.imported_start import ImportedTransportStartContext
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_planning import (
    create_initial_project,
    project_directory_candidates,
    validate_project_base_name,
)
from moltage.app.server_profiles import ServerProfileRepository
from moltage.app.species_acquisition import (
    SpeciesAcquisitionError,
    acquire_species_library,
)
from moltage.app.transport_convergence import TransportConvergenceContext
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
    MANAGED_METADATA_DIRECTORY,
    ProjectElectrodeClusterProvenance,
    ProjectRestartProvenance,
    required_initial_directories,
    remote_step_directory,
)
from moltage.domain.server_profile import ServerProfile, SlurmCommandMode
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteExecutor,
    RemoteExecutorError,
    RemotePathAlreadyExistsError,
    RemotePathNotFoundError,
)
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.slurm import (
    SlurmSubmissionError,
    build_sbatch_submission_command,
    parse_sbatch_parsable_output,
    parse_submit_script_output_filename,
    render_submit_script,
)
from moltage.remote.runtime_environment import verify_configured_fhi_runtime
from moltage.remote.slurm_discovery import (
    SlurmConfigurationError,
    SlurmDiscoveryError,
    SlurmDiscoverySource,
    resolve_slurm_for_submission,
)
from moltage.remote.step_inputs import (
    StepInputChecksumError,
    StepInputTransferError,
    upload_step_inputs_atomically,
    verify_step_input_sha256,
)


SUBMISSION_LIFECYCLE_LOGGER_NAME = "moltage.submission.lifecycle"
_SUBMISSION_LIFECYCLE_LOGGER = logging.getLogger(
    SUBMISSION_LIFECYCLE_LOGGER_NAME
)


def record_submission_lifecycle_event(
    event: str,
    *,
    project_id: UUID | str | None = None,
    step_kind: ProjectStepKind | str | None = None,
    job_id: str | None = None,
    exception_type: str | None = None,
) -> None:
    """Write one bounded, secret-free diagnostic milestone if logging is available."""

    fields = (
        ("event", event),
        ("project_id", project_id),
        ("step", step_kind.value if isinstance(step_kind, ProjectStepKind) else step_kind),
        ("job_id", job_id),
        ("exception_type", exception_type),
    )
    try:
        _SUBMISSION_LIFECYCLE_LOGGER.info(
            " ".join(
                f"{name}={_diagnostic_token(value)}"
                for name, value in fields
                if value is not None
            )
        )
    except Exception:
        # Diagnostics are explicitly non-authoritative and must never alter a
        # submission result, including when a handler or filesystem fails.
        return


class ProjectSubmissionError(RuntimeError):
    """Base sanitized failure for one explicit project submission."""

    def __init__(
        self,
        message: str,
        *,
        remote_project_path: str | None = None,
        step_kind: ProjectStepKind | None = None,
        job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.remote_project_path = remote_project_path
        self.step_kind = step_kind
        self.job_id = job_id


class MissingClusterSettingsError(ProjectSubmissionError):
    pass


class ProjectAllocationError(ProjectSubmissionError):
    pass


class ProjectPreparationError(ProjectSubmissionError):
    pass


class ProjectChecksumError(ProjectPreparationError):
    pass


UploadVerificationError = ProjectChecksumError


class SbatchRejectedError(ProjectSubmissionError):
    pass


SlurmSubmissionRejected = SbatchRejectedError


class SubmissionOutcomeUnknown(ProjectSubmissionError):
    pass


class ProjectStateRecordingError(ProjectSubmissionError):
    pass


ManifestUpdateError = ProjectStateRecordingError


class ProjectContinuationConflictError(ProjectSubmissionError):
    """Raised before upload when existing continuation state/files are unsafe."""


class TransportConvergenceConflictError(ProjectContinuationConflictError):
    """Raised when an existing project cannot safely begin Step 3."""


@dataclass(frozen=True, slots=True)
class NewProjectSubmissionRequest:
    profile: ServerProfile
    base_name: str
    source_molecule_name: str
    starting_step: ProjectStepKind
    input_plan: AimsOptimizationInputPlan | TransportConvergenceInputPlan = field(
        repr=False
    )
    supplied_password: str | None = field(default=None, repr=False, compare=False)
    imported_transport_context: ImportedTransportStartContext | None = field(
        default=None,
        repr=False,
    )
    restart_provenance: ProjectRestartProvenance | None = None
    restart_source_project: CalculationProject | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    restart_electrode_provenance: tuple[
        ProjectElectrodeClusterProvenance, ...
    ] = ()
    restart_profile_rebind_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("submission profile must be a ServerProfile")
        validate_project_base_name(self.base_name)
        if (
            not isinstance(self.source_molecule_name, str)
            or not self.source_molecule_name.strip()
            or "\x00" in self.source_molecule_name
            or "\n" in self.source_molecule_name
            or "\r" in self.source_molecule_name
        ):
            raise ValueError("source molecule name must be one nonempty line")
        if self.starting_step not in {
            ProjectStepKind.MOLECULE_OPT,
            ProjectStepKind.MOLECULE_AU_OPT,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        }:
            raise ValueError("new projects may start only at Step 1, Step 2, or Step 3")
        if self.starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
            if not isinstance(self.input_plan, TransportConvergenceInputPlan):
                raise TypeError(
                    "direct Step-3 submission requires a transport-convergence input plan"
                )
            if self.restart_provenance is None:
                if not isinstance(
                    self.imported_transport_context,
                    ImportedTransportStartContext,
                ):
                    raise TypeError(
                        "direct Step-3 submission requires imported electrode provenance"
                    )
                if (
                    self.input_plan.structure
                    is not self.imported_transport_context.working_structure
                ):
                    raise ValueError(
                        "direct Step-3 inputs are not the accepted electrode structure"
                    )
                if self.restart_electrode_provenance:
                    raise ValueError(
                        "normal direct Step-3 submission cannot use restart provenance"
                    )
            else:
                if self.imported_transport_context is not None:
                    raise ValueError(
                        "restart Step-3 submission cannot use imported-start context"
                    )
                if len(self.restart_electrode_provenance) != 2:
                    raise ValueError(
                        "restart Step-3 submission requires two electrode mappings"
                    )
        else:
            if not isinstance(self.input_plan, AimsOptimizationInputPlan):
                raise TypeError(
                    "optimization submission requires an AimsOptimizationInputPlan"
                )
            if self.imported_transport_context is not None:
                raise ValueError(
                    "imported electrode provenance is supported only for Step 3"
                )
            if self.restart_electrode_provenance:
                raise ValueError(
                    "restart electrode provenance is supported only for Step 3"
                )
        if self.restart_provenance is None:
            if self.restart_source_project is not None:
                raise ValueError(
                    "restart source project requires restart provenance"
                )
        else:
            if not isinstance(self.restart_provenance, ProjectRestartProvenance):
                raise TypeError("restart provenance is invalid")
            if not isinstance(self.restart_source_project, CalculationProject):
                raise TypeError(
                    "restart submission requires its authoritative source project"
                )
            if (
                self.restart_source_project.project_id
                != self.restart_provenance.source_project_id
            ):
                raise ValueError("restart source project identity does not match")
            source_step = _project_step(
                self.restart_source_project,
                self.restart_provenance.source_step,
            )
            if source_step.job_id != self.restart_provenance.source_job_id:
                raise ValueError("restart source Job ID does not match")
            expected_start = {
                ProjectStepKind.MOLECULE_OPT: ProjectStepKind.MOLECULE_OPT,
                ProjectStepKind.MOLECULE_AU_OPT: ProjectStepKind.MOLECULE_AU_OPT,
                ProjectStepKind.TRANSPORT_CONVERGENCE: (
                    ProjectStepKind.TRANSPORT_CONVERGENCE
                ),
                ProjectStepKind.TRANSMISSION: (
                    ProjectStepKind.TRANSPORT_CONVERGENCE
                ),
            }[self.restart_provenance.source_step]
            if self.starting_step is not expected_start:
                raise ValueError(
                    "restart project starting step does not match the source task"
                )
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("a supplied submission password must be nonempty text")
        if not isinstance(self.restart_profile_rebind_confirmed, bool):
            raise TypeError("restart profile-rebind confirmation must be boolean")


@dataclass(frozen=True, slots=True)
class ExistingProjectStepSubmissionRequest:
    profile: ServerProfile
    project: CalculationProject
    input_plan: AimsOptimizationInputPlan = field(repr=False)
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("continuation profile must be a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("continuation project must be a CalculationProject")
        _validate_step2_continuation_state(self.project)
        if (
            PurePosixPath(self.project.remote_project_path).parent
            != PurePosixPath(self.profile.remote_project_root)
        ):
            raise ValueError(
                "continuation project is outside the selected server workspace"
            )
        if not isinstance(self.input_plan, AimsOptimizationInputPlan):
            raise TypeError(
                "continuation input plan must be an AimsOptimizationInputPlan"
            )
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("a supplied continuation password must be nonempty text")


@dataclass(frozen=True, slots=True)
class TransportConvergenceSubmissionRequest:
    """One fully preflighted Step-3 continuation in an existing project."""

    profile: ServerProfile
    context: TransportConvergenceContext
    input_plan: TransportConvergenceInputPlan = field(repr=False)
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("Step-3 continuation profile must be a ServerProfile")
        if not isinstance(self.context, TransportConvergenceContext):
            raise TypeError(
                "Step-3 continuation requires an eligible transport context"
            )
        _validate_step3_continuation_state(self.context.project)
        if (
            PurePosixPath(self.context.project.remote_project_path).parent
            != PurePosixPath(self.profile.remote_project_root)
        ):
            raise ValueError(
                "Step-3 project is outside the selected server workspace"
            )
        if not isinstance(self.input_plan, TransportConvergenceInputPlan):
            raise TypeError(
                "Step-3 continuation requires a transport-convergence input plan"
            )
        if self.input_plan.structure is not self.context.working_structure:
            raise ValueError(
                "Step-3 input-plan geometry is not the accepted working structure"
            )
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("a supplied Step-3 password must be nonempty text")

    @property
    def project(self) -> CalculationProject:
        return self.context.project


@dataclass(frozen=True, slots=True)
class ProjectSubmissionResult:
    project: CalculationProject
    step: ProjectStepRecord
    remote_step_directory: str
    job_id: str
    cluster_name: str | None
    geometry_sha256: str
    control_sha256: str
    submit_sha256: str


class ProjectSubmissionService:
    """Allocate, initialize, upload, submit once, and persist the outcome."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        server_profile_repository: ServerProfileRepository | None = None,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        project_id_factory: Callable[[], UUID] = uuid4,
        temporary_id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._server_profile_repository = server_profile_repository
        self._now_factory = now_factory
        self._project_id_factory = project_id_factory
        self._temporary_id_factory = temporary_id_factory

    def create_and_submit_project(
        self,
        request: NewProjectSubmissionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> ProjectSubmissionResult:
        """Perform one user-authorized allocation and at most one sbatch call."""

        if not isinstance(request, NewProjectSubmissionRequest):
            raise TypeError("request must be a NewProjectSubmissionRequest")
        report = progress or (lambda _message: None)
        preset = request.profile.execution_preset
        if preset is None:
            raise MissingClusterSettingsError(
                f"Configure Cluster Execution Settings for {request.profile.name} "
                "before submitting."
            )
        scheduler_name = scheduler_display_name(preset.scheduler_kind)

        created_at = _aware_now(self._now_factory)
        project_id = self._project_id_factory()
        first_candidate = next(
            iter(project_directory_candidates(request.base_name, created_at.date()))
        )
        # Validate the complete local project model before any connection or mkdir.
        create_initial_project(
            base_name=request.base_name,
            remote_directory_name=first_candidate,
            source_molecule_name=request.source_molecule_name,
            server_profile_id=request.profile.profile_id,
            remote_project_root=request.profile.remote_project_root,
            starting_step=request.starting_step,
            now=created_at,
            project_id=project_id,
            electrode_provenance=(
                request.imported_transport_context.electrode_provenance
                if request.imported_transport_context is not None
                else request.restart_electrode_provenance
            ),
            restart_provenance=request.restart_provenance,
        )
        report(f"Connecting to {request.profile.name}...")
        record_submission_lifecycle_event(
            "connection_started",
            project_id=project_id,
            step_kind=request.starting_step,
        )
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        record_submission_lifecycle_event(
            "connection_established",
            project_id=project_id,
            step_kind=request.starting_step,
        )
        try:
            report(f"Checking {scheduler_name} command location...")
            verify_configured_fhi_runtime(executor, preset)
            try:
                slurm = resolve_slurm_for_submission(executor, preset)
            except SlurmConfigurationError:
                raise
            except SlurmDiscoveryError:
                raise SlurmDiscoveryError(
                    f"{scheduler_name} could not be detected automatically on "
                    f"{request.profile.name}. Retry detection or enter the "
                    f"{scheduler_name} command directory manually."
                ) from None
            self._cache_automatic_slurm_location(
                request.profile,
                slurm.bin_directory,
                slurm.lsf_env_directory,
                slurm.lsf_library_directory,
                slurm.lsf_server_directory,
                slurm.discovery_source,
                report,
            )
            input_files = _materialize_input_files(
                executor,
                request.input_plan,
                preset.fhi_species_defaults_path,
                render_submit_script(
                    preset,
                    project_id,
                    request.starting_step,
                    mail_settings=request.profile.slurm_mail_settings,
                ),
                project_id=project_id,
                step_kind=request.starting_step,
                progress=report,
            )
            if request.restart_provenance is not None:
                report("Revalidating the terminal restart source...")
                _validate_restart_source_before_allocation(
                    executor,
                    request,
                )
            try:
                _validate_remote_workspace(
                    executor,
                    request.profile.remote_project_root,
                )
            except ProjectAllocationError as error:
                raise ProjectAllocationError(
                    _safe_message(error, request.supplied_password)
                ) from None
            report("Creating project...")
            try:
                directory_name, remote_project_path = (
                    allocate_remote_project_directory(
                        executor,
                        request.profile.remote_project_root,
                        request.base_name,
                        created_at.date(),
                    )
                )
            except ProjectAllocationError as error:
                raise ProjectAllocationError(
                    _safe_message(error, request.supplied_password)
                ) from None

            project = create_initial_project(
                base_name=request.base_name,
                remote_directory_name=directory_name,
                source_molecule_name=request.source_molecule_name,
                server_profile_id=request.profile.profile_id,
                remote_project_root=request.profile.remote_project_root,
                starting_step=request.starting_step,
                now=created_at,
                project_id=project_id,
                electrode_provenance=(
                    request.imported_transport_context.electrode_provenance
                    if request.imported_transport_context is not None
                    else request.restart_electrode_provenance
                ),
                restart_provenance=request.restart_provenance,
            )
            manifest_repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            manifest_written = False
            try:
                metadata_directory = str(
                    PurePosixPath(remote_project_path) / MANAGED_METADATA_DIRECTORY
                )
                executor.mkdir(metadata_directory)
                manifest_repository.write_initial(project)
                manifest_written = True
                self._local_index_repository.mark_seen(project)
                for relative_directory in required_initial_directories(
                    request.starting_step
                )[1:]:
                    executor.mkdir(
                        str(PurePosixPath(remote_project_path) / relative_directory)
                    )
            except Exception as error:
                message = (
                    "Managed project initialization failed: "
                    + _safe_message(error, request.supplied_password)
                )
                if manifest_written:
                    _record_and_raise(
                        manifest_repository,
                        self._local_index_repository,
                        project,
                        request.starting_step,
                        state=ProjectStepState.FAILED,
                        message=message,
                        exception_type=ProjectPreparationError,
                        now_factory=self._now_factory,
                        finished=True,
                        supplied_password=request.supplied_password,
                    )
                raise ProjectPreparationError(
                    message,
                    remote_project_path=remote_project_path,
                    step_kind=request.starting_step,
                ) from None

            return submit_project_step(
                executor=executor,
                manifest_repository=manifest_repository,
                local_index_repository=self._local_index_repository,
                project=project,
                step_kind=request.starting_step,
                input_files=input_files,
                now_factory=self._now_factory,
                temporary_id_factory=self._temporary_id_factory,
                progress=report,
                supplied_password=request.supplied_password,
                sbatch_path=slurm.sbatch_path,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
            )
        finally:
            record_submission_lifecycle_event(
                "connection_close_started",
                project_id=project_id,
                step_kind=request.starting_step,
            )
            try:
                executor.close()
            except Exception as error:
                record_submission_lifecycle_event(
                    "connection_close_failed",
                    project_id=project_id,
                    step_kind=request.starting_step,
                    exception_type=type(error).__name__,
                )
                raise
            else:
                record_submission_lifecycle_event(
                    "connection_close_finished",
                    project_id=project_id,
                    step_kind=request.starting_step,
                )

    def continue_project_with_step2(
        self,
        request: ExistingProjectStepSubmissionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> ProjectSubmissionResult:
        """Create only molecule_Au and submit Step 2 in the existing project."""

        if not isinstance(request, ExistingProjectStepSubmissionRequest):
            raise TypeError(
                "continuation request must be an ExistingProjectStepSubmissionRequest"
            )
        report = progress or (lambda _message: None)
        preset = request.profile.execution_preset
        if preset is None:
            raise MissingClusterSettingsError(
                f"Configure Cluster Execution Settings for {request.profile.name} "
                "before submitting."
            )
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        _validate_step2_continuation_state(request.project)
        report(f"Connecting to {request.profile.name}...")
        record_submission_lifecycle_event(
            "connection_started",
            project_id=request.project.project_id,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        record_submission_lifecycle_event(
            "connection_established",
            project_id=request.project.project_id,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        try:
            report(f"Checking {scheduler_name} command location...")
            slurm = resolve_slurm_for_submission(executor, preset)
            verify_configured_fhi_runtime(executor, preset)
            self._cache_automatic_slurm_location(
                request.profile,
                slurm.bin_directory,
                slurm.lsf_env_directory,
                slurm.lsf_library_directory,
                slurm.lsf_server_directory,
                slurm.discovery_source,
                report,
            )
            input_files = _materialize_input_files(
                executor,
                request.input_plan,
                preset.fhi_species_defaults_path,
                render_submit_script(
                    preset,
                    request.project.project_id,
                    ProjectStepKind.MOLECULE_AU_OPT,
                    mail_settings=request.profile.slurm_mail_settings,
                ),
                project_id=request.project.project_id,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                progress=report,
            )
            manifest_repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            authoritative = manifest_repository.load(
                request.project.remote_project_path,
                preserve_remote_errors=True,
            )
            if authoritative.project_id != request.project.project_id:
                raise ProjectContinuationConflictError(
                    "remote project identity changed before Step 2 continuation",
                    remote_project_path=request.project.remote_project_path,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                )
            if authoritative.revision != request.project.revision:
                raise ProjectContinuationConflictError(
                    "remote project revision changed; refresh before continuing Step 2",
                    remote_project_path=request.project.remote_project_path,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                )
            _validate_step2_continuation_state(authoritative)
            step_directory = remote_step_directory(
                authoritative,
                ProjectStepKind.MOLECULE_AU_OPT,
            )
            try:
                executor.stat(step_directory)
            except RemotePathNotFoundError:
                pass
            else:
                raise ProjectContinuationConflictError(
                    "Step 2 is NOT_STARTED but the remote molecule_Au path "
                    f"already exists: {step_directory}",
                    remote_project_path=authoritative.remote_project_path,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                )
            report("Creating molecule_Au for Step 2...")
            try:
                executor.mkdir(step_directory)
            except RemotePathAlreadyExistsError:
                raise ProjectContinuationConflictError(
                    "The remote molecule_Au path appeared before Step 2 upload: "
                    f"{step_directory}",
                    remote_project_path=authoritative.remote_project_path,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                ) from None

            return submit_project_step(
                executor=executor,
                manifest_repository=manifest_repository,
                local_index_repository=self._local_index_repository,
                project=authoritative,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                input_files=input_files,
                now_factory=self._now_factory,
                temporary_id_factory=self._temporary_id_factory,
                progress=report,
                supplied_password=request.supplied_password,
                sbatch_path=slurm.sbatch_path,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
                local_server_profile_id=request.profile.profile_id,
            )
        finally:
            record_submission_lifecycle_event(
                "connection_close_started",
                project_id=request.project.project_id,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            )
            try:
                executor.close()
            except Exception as error:
                record_submission_lifecycle_event(
                    "connection_close_failed",
                    project_id=request.project.project_id,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                    exception_type=type(error).__name__,
                )
                raise
            else:
                record_submission_lifecycle_event(
                    "connection_close_finished",
                    project_id=request.project.project_id,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                )

    def continue_project_with_step3(
        self,
        request: TransportConvergenceSubmissionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> ProjectSubmissionResult:
        """Create transport and submit Step 3 once in the existing project."""

        if not isinstance(request, TransportConvergenceSubmissionRequest):
            raise TypeError(
                "Step-3 request must be a TransportConvergenceSubmissionRequest"
            )
        report = progress or (lambda _message: None)
        preset = request.profile.execution_preset
        if preset is None:
            raise MissingClusterSettingsError(
                f"Configure Cluster Execution Settings for {request.profile.name} "
                "before submitting."
            )
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        project = request.project
        _validate_step3_continuation_state(project)
        report(f"Connecting to {request.profile.name}...")
        record_submission_lifecycle_event(
            "connection_started",
            project_id=project.project_id,
            step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        record_submission_lifecycle_event(
            "connection_established",
            project_id=project.project_id,
            step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        try:
            report(f"Checking {scheduler_name} command location...")
            slurm = resolve_slurm_for_submission(executor, preset)
            verify_configured_fhi_runtime(executor, preset)
            self._cache_automatic_slurm_location(
                request.profile,
                slurm.bin_directory,
                slurm.lsf_env_directory,
                slurm.lsf_library_directory,
                slurm.lsf_server_directory,
                slurm.discovery_source,
                report,
            )
            input_files = _materialize_input_files(
                executor,
                request.input_plan,
                preset.fhi_species_defaults_path,
                render_submit_script(
                    preset,
                    project.project_id,
                    ProjectStepKind.TRANSPORT_CONVERGENCE,
                    mail_settings=request.profile.slurm_mail_settings,
                ),
                project_id=project.project_id,
                step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                progress=report,
            )
            manifest_repository = RemoteProjectRepository(
                executor,
                temporary_id_factory=self._temporary_id_factory,
            )
            authoritative = manifest_repository.load(
                project.remote_project_path,
                preserve_remote_errors=True,
            )
            if authoritative.project_id != project.project_id:
                raise TransportConvergenceConflictError(
                    "remote project identity changed before Step 3",
                    remote_project_path=project.remote_project_path,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                )
            if authoritative.revision != project.revision:
                raise TransportConvergenceConflictError(
                    "remote project revision changed; refresh before Step 3",
                    remote_project_path=project.remote_project_path,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                )
            _validate_step3_continuation_state(authoritative)
            authoritative = replace(
                authoritative,
                electrode_provenance=request.context.electrode_provenance,
                legacy_electrode_recovery_allowed=False,
            )
            step_directory = remote_step_directory(
                authoritative,
                ProjectStepKind.TRANSPORT_CONVERGENCE,
            )
            try:
                executor.stat(step_directory)
            except RemotePathNotFoundError:
                pass
            else:
                raise TransportConvergenceConflictError(
                    "Step 3 is NOT_STARTED but the remote "
                    f"molecule_Au/transport path already exists: {step_directory}",
                    remote_project_path=authoritative.remote_project_path,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                )

            report("Creating molecule_Au/transport for Step 3...")
            try:
                executor.mkdir(step_directory)
            except RemotePathAlreadyExistsError:
                raise TransportConvergenceConflictError(
                    "The remote molecule_Au/transport path appeared before "
                    f"Step-3 upload: {step_directory}",
                    remote_project_path=authoritative.remote_project_path,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                ) from None
            except RemotePathNotFoundError:
                raise TransportConvergenceConflictError(
                    "The existing project has no molecule_Au directory for Step 3",
                    remote_project_path=authoritative.remote_project_path,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                ) from None

            return submit_project_step(
                executor=executor,
                manifest_repository=manifest_repository,
                local_index_repository=self._local_index_repository,
                project=authoritative,
                step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                input_files=input_files,
                now_factory=self._now_factory,
                temporary_id_factory=self._temporary_id_factory,
                progress=report,
                supplied_password=request.supplied_password,
                sbatch_path=slurm.sbatch_path,
                lsf_env_directory=slurm.lsf_env_directory,
                lsf_library_directory=slurm.lsf_library_directory,
                lsf_server_directory=slurm.lsf_server_directory,
                local_server_profile_id=request.profile.profile_id,
            )
        finally:
            record_submission_lifecycle_event(
                "connection_close_started",
                project_id=project.project_id,
                step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
            )
            try:
                executor.close()
            except Exception as error:
                record_submission_lifecycle_event(
                    "connection_close_failed",
                    project_id=project.project_id,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                    exception_type=type(error).__name__,
                )
                raise
            else:
                record_submission_lifecycle_event(
                    "connection_close_finished",
                    project_id=project.project_id,
                    step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
                )

    def _cache_automatic_slurm_location(
        self,
        profile: ServerProfile,
        bin_directory: str,
        lsf_env_directory: str | None,
        lsf_library_directory: str | None,
        lsf_server_directory: str | None,
        source: SlurmDiscoverySource,
        progress: Callable[[str], None],
    ) -> None:
        preset = profile.execution_preset
        if (
            self._server_profile_repository is None
            or preset is None
            or preset.slurm_command_mode is not SlurmCommandMode.AUTOMATIC
            or source not in {
                SlurmDiscoverySource.CURRENT_ENVIRONMENT,
                SlurmDiscoverySource.LOGIN_SHELL,
                SlurmDiscoverySource.CACHED_AUTOMATIC,
            }
            or (
                preset.slurm_bin_directory == bin_directory
                and preset.lsf_env_directory == lsf_env_directory
                and preset.lsf_library_directory == lsf_library_directory
                and preset.lsf_server_directory == lsf_server_directory
            )
        ):
            return
        try:
            self._server_profile_repository.cache_automatic_slurm_directory(
                profile.profile_id,
                bin_directory,
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            )
        except Exception as error:
            scheduler_name = scheduler_display_name(preset.scheduler_kind)
            progress(
                f"Warning: the verified {scheduler_name} location is being used for this "
                "submission but could not be cached locally: "
                + _safe_message(error, None)
            )


def allocate_remote_project_directory(
    executor: RemoteExecutor,
    remote_workspace: str,
    base_name: str,
    local_date: date,
) -> tuple[str, str]:
    """Claim the first candidate whose authoritative mkdir succeeds."""

    for candidate in project_directory_candidates(base_name, local_date):
        path = str(PurePosixPath(remote_workspace) / candidate)
        try:
            executor.mkdir(path)
        except RemotePathAlreadyExistsError:
            continue
        except RemoteExecutorError as error:
            raise ProjectAllocationError(
                f"remote project directory could not be created: {error}"
            ) from None
        return candidate, path
    raise AssertionError("project directory candidate generator ended unexpectedly")


def _validate_restart_source_before_allocation(
    executor: RemoteExecutor,
    request: NewProjectSubmissionRequest,
) -> None:
    """Fail closed before mkdir if the source task is still active or changed."""

    provenance = request.restart_provenance
    expected = request.restart_source_project
    if provenance is None or expected is None:
        raise ProjectPreparationError(
            "restart source provenance is incomplete"
        )
    repository = RemoteProjectRepository(executor)
    source = repository.load(
        expected.remote_project_path,
        preserve_remote_errors=True,
    )
    if source.project_id != provenance.source_project_id:
        raise ProjectPreparationError(
            "restart source project identity changed; Refresh before submitting"
        )
    if (
        source.server_profile_id != request.profile.profile_id
        and not request.restart_profile_rebind_confirmed
    ):
        raise ProjectPreparationError(
            "restart source requires confirmed local server-profile binding"
        )
    source_step = _project_step(source, provenance.source_step)
    if source_step.job_id != provenance.source_job_id:
        raise ProjectPreparationError(
            "restart source attempt changed; Refresh before submitting"
        )
    if source_step.state not in {
        ProjectStepState.FAILED,
        ProjectStepState.SCHEDULER_COMPLETED,
        ProjectStepState.SUCCEEDED,
    }:
        raise ProjectPreparationError(
            "restart source Job is not durably terminal; Refresh Status before "
            "submitting the draft"
        )
    geometry_step = (
        ProjectStepKind.TRANSPORT_CONVERGENCE
        if provenance.source_step is ProjectStepKind.TRANSMISSION
        else provenance.source_step
    )
    geometry_record = _project_step(source, geometry_step)
    expected_hash = dict(geometry_record.input_hashes).get("geometry.in")
    if expected_hash != provenance.source_geometry_sha256:
        raise ProjectPreparationError(
            "restart source geometry provenance changed; Refresh and recreate "
            "the draft"
        )
    geometry_path = str(
        PurePosixPath(remote_step_directory(source, geometry_step)) / "geometry.in"
    )
    actual_hash = hashlib.sha256(executor.read_bytes(geometry_path)).hexdigest()
    if actual_hash != provenance.source_geometry_sha256:
        raise ProjectPreparationError(
            "restart source geometry.in no longer matches its accepted SHA256"
        )


def submit_project_step(
    *,
    executor: RemoteExecutor,
    manifest_repository: RemoteProjectRepository,
    local_index_repository: LocalProjectIndexRepository,
    project: CalculationProject,
    step_kind: ProjectStepKind,
    input_files: Mapping[str, bytes],
    now_factory: Callable[[], datetime],
    temporary_id_factory: Callable[[], str],
    progress: Callable[[str], None],
    sbatch_path: str,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
    supplied_password: str | None = None,
    local_server_profile_id: UUID | None = None,
) -> ProjectSubmissionResult:
    """Upload and submit one existing supported NOT_STARTED step without retrying."""

    if step_kind not in {
        ProjectStepKind.MOLECULE_OPT,
        ProjectStepKind.MOLECULE_AU_OPT,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
    }:
        raise ProjectSubmissionError(
            "submission is supported only for Step 1, Step 2, or Step 3"
        )
    step = _project_step(project, step_kind)
    if step.state is not ProjectStepState.NOT_STARTED:
        raise ProjectSubmissionError(
            f"{step_kind.value} must be NOT_STARTED before submission",
            remote_project_path=project.remote_project_path,
            step_kind=step_kind,
        )
    step_directory = remote_step_directory(project, step_kind)

    progress("Uploading input files...")
    record_submission_lifecycle_event(
        "upload_started",
        project_id=project.project_id,
        step_kind=step_kind,
    )
    try:
        upload_step_inputs_atomically(
            executor,
            step_directory,
            input_files,
            temporary_id_factory=temporary_id_factory,
        )
    except StepInputTransferError as error:
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.FAILED,
            message=_safe_message(error, supplied_password),
            exception_type=ProjectPreparationError,
            now_factory=now_factory,
            finished=True,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )
    record_submission_lifecycle_event(
        "upload_completed",
        project_id=project.project_id,
        step_kind=step_kind,
    )

    progress("Verifying remote files...")
    record_submission_lifecycle_event(
        "input_verification_started",
        project_id=project.project_id,
        step_kind=step_kind,
    )
    try:
        input_hashes = verify_step_input_sha256(
            executor,
            step_directory,
            input_files,
        )
    except StepInputChecksumError as error:
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.FAILED,
            message=str(error),
            exception_type=ProjectChecksumError,
            now_factory=now_factory,
            finished=True,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )
    except StepInputTransferError as error:
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.FAILED,
            message=_safe_message(error, supplied_password),
            exception_type=ProjectPreparationError,
            now_factory=now_factory,
            finished=True,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )
    record_submission_lifecycle_event(
        "input_verification_completed",
        project_id=project.project_id,
        step_kind=step_kind,
    )

    scheduler_kind = (
        SchedulerKind.LSF
        if PurePosixPath(sbatch_path).name == "bsub"
        else SchedulerKind.SLURM
    )
    scheduler_name = scheduler_display_name(scheduler_kind)
    unknown_submission_message = (
        f"{scheduler_name} submission outcome unknown; automatic retry was not "
        "performed."
    )
    command = build_sbatch_submission_command(
        step_directory,
        sbatch_path,
        lsf_env_directory=lsf_env_directory,
        lsf_library_directory=lsf_library_directory,
        lsf_server_directory=lsf_server_directory,
    )
    progress(f"Submitting {scheduler_name} job...")
    record_submission_lifecycle_event(
        "sbatch_dispatch_started",
        project_id=project.project_id,
        step_kind=step_kind,
    )
    try:
        command_result = executor.execute(command)
    except RemoteCommandOutcomeUnknown:
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.UNKNOWN,
            message=unknown_submission_message,
            exception_message=(
                unknown_submission_message
                + f" Inspect {scheduler_name} before resubmitting."
            ),
            exception_type=SubmissionOutcomeUnknown,
            now_factory=now_factory,
            input_hashes=input_hashes,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )
    except RemoteExecutorError as error:
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.FAILED,
            message=(
                f"{scheduler_name} submission could not be dispatched: "
                + _safe_message(error, supplied_password)
            ),
            exception_type=SbatchRejectedError,
            now_factory=now_factory,
            input_hashes=input_hashes,
            finished=True,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )

    if command_result.exit_status != 0:
        scheduler_detail = _concise_remote_text(
            command_result.stderr,
            supplied_password,
        )
        message = f"{scheduler_name} rejected the submission"
        if scheduler_detail:
            message += f": {scheduler_detail}"
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.FAILED,
            message=message,
            exception_type=SbatchRejectedError,
            now_factory=now_factory,
            input_hashes=input_hashes,
            finished=True,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )

    try:
        receipt = parse_sbatch_parsable_output(command_result.stdout)
    except SlurmSubmissionError:
        _record_and_raise(
            manifest_repository,
            local_index_repository,
            project,
            step_kind,
            state=ProjectStepState.UNKNOWN,
            message=unknown_submission_message,
            exception_message=(
                unknown_submission_message
                + f" {PurePosixPath(sbatch_path).name} returned an unsupported "
                f"receipt; inspect {scheduler_name} "
                "before resubmitting."
            ),
            exception_type=SubmissionOutcomeUnknown,
            now_factory=now_factory,
            input_hashes=input_hashes,
            supplied_password=supplied_password,
            local_server_profile_id=local_server_profile_id,
        )
    record_submission_lifecycle_event(
        "sbatch_returned_job_id",
        project_id=project.project_id,
        step_kind=step_kind,
        job_id=receipt.job_id,
    )

    progress("Recording project state...")
    submitted_at = _aware_now(now_factory)
    queued_project = _with_step(
        project,
        replace(
            step,
            state=ProjectStepState.QUEUED,
            job_id=receipt.job_id,
            cluster_name=receipt.cluster_name,
            submitted_at=submitted_at,
            input_hashes=input_hashes,
            last_error=None,
            scheduler_state=None,
            scheduler_kind=scheduler_kind,
            submit_script_filename=(
                "submit.sh"
                if step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE
                else step.submit_script_filename
            ),
            slurm_output_filename=(
                parse_submit_script_output_filename(input_files["submit.sh"])
                if step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE
                else step.slurm_output_filename
            ),
        ),
    )
    try:
        updated_project = manifest_repository.persist_update(
            queued_project,
            updated_at=submitted_at,
        )
    except Exception as error:
        raise ProjectStateRecordingError(
            f"{scheduler_name} accepted job "
            f"{receipt.job_id}, but QUEUED state could not be recorded: "
            + _safe_message(error, supplied_password),
            remote_project_path=project.remote_project_path,
            step_kind=step_kind,
            job_id=receipt.job_id,
        ) from None
    record_submission_lifecycle_event(
        "manifest_persisted",
        project_id=project.project_id,
        step_kind=step_kind,
        job_id=receipt.job_id,
    )
    try:
        local_index_repository.mark_seen(
            updated_project,
            bound_server_profile_id=local_server_profile_id,
        )
    except Exception as error:
        raise ProjectStateRecordingError(
            f"{scheduler_name} job "
            f"{receipt.job_id} is QUEUED remotely, but the local project index "
            "could not be updated: "
            + _safe_message(error, supplied_password),
            remote_project_path=project.remote_project_path,
            step_kind=step_kind,
            job_id=receipt.job_id,
        ) from None
    record_submission_lifecycle_event(
        "local_index_persisted",
        project_id=project.project_id,
        step_kind=step_kind,
        job_id=receipt.job_id,
    )

    hashes = dict(input_hashes)
    return ProjectSubmissionResult(
        project=updated_project,
        step=_project_step(updated_project, step_kind),
        remote_step_directory=step_directory,
        job_id=receipt.job_id,
        cluster_name=receipt.cluster_name,
        geometry_sha256=hashes["geometry.in"],
        control_sha256=hashes["control.in"],
        submit_sha256=hashes["submit.sh"],
    )


def _validate_step2_continuation_state(project: CalculationProject) -> None:
    step1 = _project_step(project, ProjectStepKind.MOLECULE_OPT)
    step2 = _project_step(project, ProjectStepKind.MOLECULE_AU_OPT)
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step1.state is not ProjectStepState.SUCCEEDED:
        raise ProjectContinuationConflictError(
            "Step 1 must be SUCCEEDED before continuing to Step 2",
            remote_project_path=project.remote_project_path,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
    if step2.state is not ProjectStepState.NOT_STARTED:
        raise ProjectContinuationConflictError(
            "Step 2 must be NOT_STARTED before continuation",
            remote_project_path=project.remote_project_path,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
    if any(
        step.state is not ProjectStepState.NOT_STARTED
        for step in (step3, step4)
    ):
        raise ProjectContinuationConflictError(
            "Step 3 and Step 4 must remain NOT_STARTED during Phase 2C",
            remote_project_path=project.remote_project_path,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )


def _validate_step3_continuation_state(project: CalculationProject) -> None:
    step2 = _project_step(project, ProjectStepKind.MOLECULE_AU_OPT)
    step3 = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    step4 = _project_step(project, ProjectStepKind.TRANSMISSION)
    if step2.state is not ProjectStepState.SUCCEEDED:
        raise TransportConvergenceConflictError(
            "Step 2 must be SUCCEEDED before continuing to Step 3",
            remote_project_path=project.remote_project_path,
            step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
    if (
        step3.state is not ProjectStepState.NOT_STARTED
        or step3.job_id is not None
        or step3.cluster_name is not None
        or step3.submitted_at is not None
        or step3.started_at is not None
        or step3.finished_at is not None
        or step3.input_hashes
        or step3.last_error is not None
        or step3.scheduler_state is not None
        or step3.submit_script_filename is not None
        or step3.slurm_output_filename is not None
        or step3.attempts
    ):
        raise TransportConvergenceConflictError(
            "Step 3 must have pristine NOT_STARTED state before submission",
            remote_project_path=project.remote_project_path,
            step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
    if step4.state is not ProjectStepState.NOT_STARTED:
        raise TransportConvergenceConflictError(
            "Step 4 must remain NOT_STARTED during Phase 3A",
            remote_project_path=project.remote_project_path,
            step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )

def _validate_remote_workspace(
    executor: RemoteExecutor,
    remote_workspace: str,
) -> None:
    try:
        workspace_stat = executor.stat(remote_workspace)
    except RemotePathNotFoundError:
        raise ProjectAllocationError(
            f"Remote project workspace does not exist: {remote_workspace}"
        ) from None
    except RemoteExecutorError as error:
        raise ProjectAllocationError(
            f"Remote project workspace could not be inspected: {error}"
        ) from None
    if not workspace_stat.is_directory:
        raise ProjectAllocationError(
            f"Remote project workspace is not a directory: {remote_workspace}"
        )


def _materialize_input_files(
    executor: RemoteExecutor,
    plan: AimsOptimizationInputPlan | TransportConvergenceInputPlan,
    species_root: str | None,
    submit_script: str,
    *,
    project_id: UUID,
    step_kind: ProjectStepKind,
    progress: Callable[[str], None],
) -> dict[str, bytes]:
    """Acquire exact remote definitions and render before any remote mutation."""

    record_submission_lifecycle_event(
        "input_preparation_started",
        project_id=project_id,
        step_kind=step_kind,
    )
    progress("Reading required FHI-aims species definitions...")
    try:
        species_library = acquire_species_library(
            executor,
            species_root,
            plan.species_requirements,
        )
        bundle = plan.materialize(species_library)
    except SpeciesAcquisitionError as error:
        raise ProjectPreparationError(
            "FHI-aims input preparation failed before remote project changes: "
            f"{error}",
            step_kind=step_kind,
        ) from None
    input_files = _input_file_bytes(bundle, submit_script)
    if tuple(input_files) != ("geometry.in", "control.in", "submit.sh"):
        raise ProjectPreparationError(
            "Input preparation did not produce the exact required file set",
            step_kind=step_kind,
        )
    record_submission_lifecycle_event(
        "input_preparation_completed",
        project_id=project_id,
        step_kind=step_kind,
    )
    return input_files


def _input_file_bytes(
    bundle: AimsInputBundle | TransportConvergenceAimsInputs,
    submit_script: str,
) -> dict[str, bytes]:
    return {
        "geometry.in": bundle.geometry_text.encode("utf-8"),
        "control.in": bundle.control_text.encode("utf-8"),
        "submit.sh": submit_script.encode("utf-8"),
    }


def _record_and_raise(
    manifest_repository: RemoteProjectRepository,
    local_index_repository: LocalProjectIndexRepository,
    project: CalculationProject,
    step_kind: ProjectStepKind,
    *,
    state: ProjectStepState,
    message: str,
    exception_type: type[ProjectSubmissionError],
    now_factory: Callable[[], datetime],
    input_hashes: tuple[tuple[str, str], ...] = (),
    finished: bool = False,
    supplied_password: str | None = None,
    exception_message: str | None = None,
    local_server_profile_id: UUID | None = None,
) -> None:
    timestamp = _aware_now(now_factory)
    step = _project_step(project, step_kind)
    changed = replace(
        step,
        state=state,
        input_hashes=input_hashes,
        last_error=message,
        finished_at=timestamp if finished else None,
    )
    candidate = _with_step(project, changed)
    try:
        updated = manifest_repository.persist_update(
            candidate,
            updated_at=timestamp,
        )
        local_index_repository.mark_seen(
            updated,
            bound_server_profile_id=local_server_profile_id,
        )
    except Exception as state_error:
        raise exception_type(
            (exception_message or message)
            + "; project state could not also be recorded: "
            + _safe_message(state_error, supplied_password),
            remote_project_path=project.remote_project_path,
            step_kind=step_kind,
        ) from None
    raise exception_type(
        exception_message or message,
        remote_project_path=project.remote_project_path,
        step_kind=step_kind,
    )


def _project_step(
    project: CalculationProject,
    step_kind: ProjectStepKind,
) -> ProjectStepRecord:
    return next(step for step in project.steps if step.kind is step_kind)


def _with_step(
    project: CalculationProject,
    changed_step: ProjectStepRecord,
) -> CalculationProject:
    return replace(
        project,
        steps=tuple(
            changed_step if step.kind is changed_step.kind else step
            for step in project.steps
        ),
    )


def _aware_now(now_factory: Callable[[], datetime]) -> datetime:
    value = now_factory()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("submission timestamps must be timezone-aware")
    return value


def _safe_message(error: object, secret: str | None) -> str:
    text = str(error) if isinstance(error, BaseException) else str(error)
    if secret:
        text = text.replace(secret, "[redacted]")
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return (first_line or "operation failed")[:400]


def _concise_remote_text(data: bytes, secret: str | None) -> str:
    try:
        text = data.decode("utf-8", errors="replace")
    except AttributeError:
        return "invalid scheduler error output"
    return _safe_message(text, secret)


def _diagnostic_token(value: object) -> str:
    """Keep lifecycle-log fields single-line, bounded, and non-structural."""

    return (
        str(value)
        .replace("\x00", "?")
        .replace("\r", "?")
        .replace("\n", "?")[:160]
    )
