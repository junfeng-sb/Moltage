"""Compact project planning/confirmation dialogs and one submission worker."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.connection_service import (
    AuthenticationError,
    ConnectionTestError,
    PasswordRequiredError,
)
from moltage.app.project_planning import (
    StartStepRecommendation,
    project_directory_candidates,
    validate_project_base_name,
)
from moltage.app.project_submission import (
    ExistingProjectStepSubmissionRequest,
    MissingClusterSettingsError,
    NewProjectSubmissionRequest,
    ProjectAllocationError,
    ProjectChecksumError,
    ProjectContinuationConflictError,
    ProjectPreparationError,
    ProjectStateRecordingError,
    ProjectSubmissionError,
    ProjectSubmissionResult,
    ProjectSubmissionService,
    SbatchRejectedError,
    SubmissionOutcomeUnknown,
    TransportConvergenceConflictError,
    TransportConvergenceSubmissionRequest,
    record_submission_lifecycle_event,
)
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
)
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.server_profile import ServerProfile, SlurmExecutionPreset
from moltage.remote.executor import RemoteExecutorError
from moltage.remote.known_hosts import HostKeyMismatch
from moltage.remote.slurm_discovery import (
    SlurmConfigurationError,
    SlurmDiscoveryError,
)


_STEP_CHOICES = (
    ("Step 1 — Molecule optimization", ProjectStepKind.MOLECULE_OPT),
    (
        "Step 2 — Molecule + contact-Au optimization",
        ProjectStepKind.MOLECULE_AU_OPT,
    ),
)


@dataclass(frozen=True, slots=True)
class NewProjectSelection:
    profile: ServerProfile
    base_name: str
    starting_step: ProjectStepKind
    nominal_directory_name: str
    workflow_kind: CalculationWorkflowKind = (
        CalculationWorkflowKind.FHI_AIMS_AITRANSS
    )


@dataclass(frozen=True, slots=True)
class SubmissionErrorPresentation:
    title: str
    message: str


class NewCalculationProjectDialog(QDialog):
    """Confirm server, safe project stem, and an explicit supported start."""

    def __init__(
        self,
        profiles: Iterable[ServerProfile],
        last_selected_profile_id: UUID | None,
        default_base_name: str,
        recommendation: StartStepRecommendation,
        *,
        preview_date: date,
        cluster_settings_callback: (
            Callable[[ServerProfile], ServerProfile | None] | None
        ) = None,
        fixed_starting_step: ProjectStepKind | None = None,
        preselected_engine: CalculationWorkflowKind | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        checked_profiles = tuple(profiles)
        if any(not isinstance(item, ServerProfile) for item in checked_profiles):
            raise TypeError("profiles must contain ServerProfile records")
        if not checked_profiles:
            raise ValueError("at least one saved server profile is required")
        if not isinstance(recommendation, StartStepRecommendation):
            raise TypeError("recommendation must be a StartStepRecommendation")
        if not isinstance(preview_date, date):
            raise TypeError("preview date must be a date")
        if fixed_starting_step not in {
            None,
            ProjectStepKind.MOLECULE_OPT,
            ProjectStepKind.MOLECULE_AU_OPT,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        }:
            raise ValueError("fixed project start must be Step 1, Step 2, or Step 3")
        if preselected_engine is not None:
            try:
                preselected_engine = CalculationWorkflowKind(preselected_engine)
            except (TypeError, ValueError):
                raise ValueError("preselected calculation engine is unsupported") from None
        if (
            fixed_starting_step is not None
            and preselected_engine is CalculationWorkflowKind.ORCA
        ):
            raise ValueError("fixed FHI-aims project start cannot use ORCA")

        self._preview_date = preview_date
        self._cluster_settings_callback = cluster_settings_callback
        self._fixed_starting_step = fixed_starting_step
        self._selection: NewProjectSelection | None = None
        self.setWindowTitle("New Calculation Project")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._server = QComboBox(self)
        self._server.setObjectName("submissionServerProfile")
        for item in checked_profiles:
            self._server.addItem(item.name, item)
        selected_index = next(
            (
                index
                for index, item in enumerate(checked_profiles)
                if item.profile_id == last_selected_profile_id
            ),
            -1,
        )
        self._server.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        form.addRow("Server:", self._server)

        self._engine = QComboBox(self)
        self._engine.setObjectName("submissionCalculationEngine")
        self._engine.addItem(
            "FHI-aims / AITRANSS",
            CalculationWorkflowKind.FHI_AIMS_AITRANSS,
        )
        if fixed_starting_step is None:
            self._engine.addItem("ORCA", CalculationWorkflowKind.ORCA)
        else:
            self._engine.setEnabled(False)
        if preselected_engine is not None:
            index = self._engine.findData(preselected_engine)
            if index < 0:
                raise ValueError("preselected calculation engine is unavailable")
            self._engine.setCurrentIndex(index)
            self._engine.setEnabled(False)
        form.addRow("Calculation engine:", self._engine)

        self._base_name = QLineEdit(default_base_name, self)
        self._base_name.setObjectName("submissionProjectBaseName")
        form.addRow("Project name:", self._base_name)

        self._detected = QLabel(recommendation.reason, self)
        self._detected.setObjectName("submissionDetectedStructure")
        self._detected.setWordWrap(True)
        form.addRow("Detected structure:", self._detected)

        self._starting_step = QComboBox(self)
        self._starting_step.setObjectName("submissionStartingStep")
        self._recommendation = recommendation
        self._populate_starting_steps()
        form.addRow("Starting stage:", self._starting_step)
        layout.addLayout(form)

        self._step_warning = QLabel(self)
        self._step_warning.setObjectName("submissionStepWarning")
        self._step_warning.setWordWrap(True)
        self._step_warning.setProperty("uiTone", "warning")
        layout.addWidget(self._step_warning)

        summary = QGroupBox("Remote submission preview", self)
        summary_form = QFormLayout(summary)
        self._remote_preview = QLabel(self)
        self._remote_preview.setObjectName("submissionRemotePreview")
        self._cluster_summary = QLabel(self)
        self._cluster_summary.setObjectName("submissionClusterSummary")
        self._cluster_summary.setWordWrap(True)
        self._email_summary = QLabel(self)
        self._email_summary.setObjectName("submissionEmailSummary")
        self._cluster_status = QLabel(self)
        self._cluster_status.setObjectName("submissionClusterStatus")
        self._cluster_status.setWordWrap(True)
        self._cluster_settings = QPushButton("Cluster Settings...", self)
        self._cluster_settings.setObjectName("submissionClusterSettings")
        self._cluster_settings.clicked.connect(self._open_cluster_settings)
        summary_form.addRow("Remote project preview:", self._remote_preview)
        summary_form.addRow("Cluster:", self._cluster_summary)
        summary_form.addRow("Email notification:", self._email_summary)
        summary_form.addRow("", self._cluster_status)
        summary_form.addRow("", self._cluster_settings)
        layout.addWidget(summary)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._continue_button = self._buttons.addButton(
            "Continue",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._continue_button.setObjectName("submissionContinue")
        self._buttons.rejected.connect(self.reject)
        self._continue_button.clicked.connect(self._validate_and_accept)
        layout.addWidget(self._buttons)

        self._server.currentIndexChanged.connect(self._refresh)
        self._engine.currentIndexChanged.connect(self._engine_changed)
        self._starting_step.currentIndexChanged.connect(self._refresh)
        self._base_name.textChanged.connect(self._refresh)
        self._refresh()

    def selected_project(self) -> NewProjectSelection:
        if self._selection is None:
            raise RuntimeError("new calculation project was not confirmed")
        return self._selection

    @Slot()
    def _refresh(self, *_ignored) -> None:
        profile = self._current_profile()
        base_name = self._base_name.text()
        try:
            candidate = next(
                iter(project_directory_candidates(base_name, self._preview_date))
            )
        except Exception:
            self._remote_preview.setText("Enter a valid project name.")
            name_is_valid = False
        else:
            self._remote_preview.setText(
                str(PurePosixPath(profile.remote_project_root) / candidate)
            )
            name_is_valid = True

        preset = profile.execution_preset
        if preset is None:
            self._cluster_summary.setText("Not configured")
            self._cluster_status.setText(
                f"Configure Cluster Execution Settings for {profile.name} "
                "before submitting."
            )
        elif (
            self._current_workflow() is CalculationWorkflowKind.ORCA
            and profile.orca_runtime is None
        ):
            self._cluster_summary.setText(resource_summary(preset))
            self._cluster_status.setText(
                f"Configure and validate ORCA for {profile.name} before submitting."
            )
        else:
            self._cluster_summary.setText(resource_summary(preset))
            self._cluster_status.clear()
        self._email_summary.setText(email_notification_summary(profile))
        self._cluster_settings.setVisible(
            preset is None or self._cluster_settings_callback is not None
        )

        step = self._current_step()
        if step is ProjectStepKind.MOLECULE_AU_OPT:
            warning = (
                "Step 1 was not performed by this project and will be marked "
                "SKIPPED — imported contact-Au geometry."
            )
        elif step is ProjectStepKind.TRANSPORT_CONVERGENCE:
            warning = (
                "Steps 1 and 2 were not performed by this project and will be "
                "marked SKIPPED — imported pre-optimized molecule–Au geometry."
            )
        else:
            warning = ""
        self._step_warning.setText(warning)
        self._continue_button.setEnabled(
            name_is_valid
            and preset is not None
            and isinstance(step, ProjectStepKind)
            and (
                self._current_workflow()
                is CalculationWorkflowKind.FHI_AIMS_AITRANSS
                or profile.orca_runtime is not None
            )
        )

    @Slot()
    def _engine_changed(self, *_ignored) -> None:
        self._populate_starting_steps()
        self._refresh()

    def _populate_starting_steps(self) -> None:
        previous = self._starting_step.currentData() if self._starting_step.count() else None
        self._starting_step.clear()
        if self._fixed_starting_step is not None:
            self._starting_step.addItem(
                step_label(self._fixed_starting_step),
                self._fixed_starting_step.value,
            )
            self._starting_step.setEnabled(False)
            return
        self._starting_step.setEnabled(True)
        if self._current_workflow() is CalculationWorkflowKind.ORCA:
            self._starting_step.addItem(
                "ORCA molecule optimization",
                ProjectStepKind.ORCA_OPTIMIZATION.value,
            )
            return
        if self._recommendation.recommended_step is None:
            self._starting_step.addItem("Choose Step 1 or Step 2…", None)
        for label, value in _STEP_CHOICES:
            self._starting_step.addItem(label, value.value)
        desired = previous or (
            self._recommendation.recommended_step.value
            if self._recommendation.recommended_step is not None
            else None
        )
        index = self._starting_step.findData(desired)
        if index >= 0:
            self._starting_step.setCurrentIndex(index)

    @Slot()
    def _open_cluster_settings(self) -> None:
        if self._cluster_settings_callback is None:
            return
        try:
            updated = self._cluster_settings_callback(self._current_profile())
        except Exception as error:
            QMessageBox.critical(self, "Cluster Settings", str(error))
            return
        if updated is None:
            return
        if not isinstance(updated, ServerProfile):
            QMessageBox.critical(
                self,
                "Cluster Settings",
                "Cluster Settings returned an invalid server profile.",
            )
            return
        index = self._server.currentIndex()
        self._server.setItemText(index, updated.name)
        self._server.setItemData(index, updated)
        self._refresh()

    @Slot()
    def _validate_and_accept(self) -> None:
        try:
            profile = self._current_profile()
            base_name = validate_project_base_name(self._base_name.text())
            step = self._current_step()
            if step is None:
                raise ValueError("Choose Step 1 or Step 2 explicitly")
            if profile.execution_preset is None:
                raise ValueError(
                    f"Configure Cluster Execution Settings for {profile.name} "
                    "before submitting."
                )
            workflow = self._current_workflow()
            if workflow is CalculationWorkflowKind.ORCA and profile.orca_runtime is None:
                raise ValueError(
                    f"Configure and validate ORCA for {profile.name} before submitting."
                )
            nominal_name = next(
                iter(project_directory_candidates(base_name, self._preview_date))
            )
        except Exception as error:
            QMessageBox.critical(self, "Cannot continue", str(error))
            return
        self._selection = NewProjectSelection(
            profile,
            base_name,
            step,
            nominal_name,
            workflow,
        )
        self.accept()

    def _current_profile(self) -> ServerProfile:
        profile = self._server.currentData()
        if not isinstance(profile, ServerProfile):
            raise RuntimeError("select a saved server profile")
        return profile

    def _current_step(self) -> ProjectStepKind | None:
        value = self._starting_step.currentData()
        if value is None:
            return None
        try:
            return ProjectStepKind(value)
        except (TypeError, ValueError):
            return None

    def _current_workflow(self) -> CalculationWorkflowKind:
        value = self._engine.currentData()
        if isinstance(value, CalculationWorkflowKind):
            return value
        return CalculationWorkflowKind(value)


class SubmissionConfirmationDialog(QDialog):
    """Final summary whose Submit button authorizes real remote mutation."""

    def __init__(
        self,
        selection: NewProjectSelection,
        settings: AimsOptimizationSettings | TransportConvergenceSettings,
        *,
        temporary_password_required: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(selection, NewProjectSelection):
            raise TypeError("selection must be a NewProjectSelection")
        if selection.starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
            if not isinstance(settings, TransportConvergenceSettings):
                raise TypeError("Step-3 confirmation requires Step-3 settings")
        elif not isinstance(settings, AimsOptimizationSettings):
            raise TypeError("optimization confirmation requires optimization settings")
        self._temporary_password_required = temporary_password_required
        preset = selection.profile.execution_preset
        if preset is None:
            raise ValueError("final confirmation requires Cluster Execution Settings")
        scheduler_name = scheduler_display_name(preset.scheduler_kind)
        self.setWindowTitle(f"Confirm REAL {scheduler_name} Submission")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)

        warning = QLabel(
            "Submit will create a real remote project and make at most one "
            f"real {scheduler_name} submission request.",
            self,
        )
        warning.setWordWrap(True)
        warning.setProperty("uiTone", "critical")
        layout.addWidget(warning)

        form = QFormLayout()
        form.addRow("Server:", QLabel(selection.profile.name, self))
        form.addRow("Project:", QLabel(selection.nominal_directory_name, self))
        form.addRow(
            "Starting stage:",
            QLabel(step_label(selection.starting_step), self),
        )
        form.addRow(
            "Remote workspace:",
            QLabel(selection.profile.remote_project_root, self),
        )
        resources = QLabel(resource_summary(preset), self)
        resources.setWordWrap(True)
        form.addRow("Resources:", resources)
        email = QLabel(email_notification_summary(selection.profile), self)
        email.setObjectName("confirmationEmailSummary")
        form.addRow("Email notification:", email)
        aims = QLabel(_submission_settings_summary(settings), self)
        aims.setWordWrap(True)
        form.addRow("FHI-aims:", aims)
        layout.addLayout(form)

        if selection.starting_step is ProjectStepKind.MOLECULE_AU_OPT:
            skipped = QLabel(
                "The current geometry already contains the recognized linker "
                "contact Au atoms. Step 1 was not performed and will be marked "
                "SKIPPED.",
                self,
            )
            skipped.setObjectName("confirmationStepWarning")
            skipped.setProperty("uiTone", "warning")
            skipped.setWordWrap(True)
            layout.addWidget(skipped)
        elif selection.starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
            skipped = QLabel(
                "Start Step 3 from this imported molecule–Au geometry only if it "
                "has already been appropriately optimized. Steps 1 and 2 were "
                "not performed and will be marked SKIPPED; both applied electrode "
                "clusters will be recorded in project provenance.",
                self,
            )
            skipped.setObjectName("confirmationStepWarning")
            skipped.setProperty("uiTone", "critical")
            skipped.setWordWrap(True)
            layout.addWidget(skipped)

        self._password: QLineEdit | None = None
        if temporary_password_required:
            password_form = QFormLayout()
            self._password = QLineEdit(self)
            self._password.setObjectName("submissionTemporaryPassword")
            self._password.setEchoMode(QLineEdit.EchoMode.Password)
            self._password.setPlaceholderText("Used for this submission only")
            password_form.addRow("Password:", self._password)
            layout.addLayout(password_form)
        else:
            credential = QLabel(
                "The saved Windows Credential Manager password will be used.",
                self,
            )
            credential.setWordWrap(True)
            layout.addWidget(credential)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._submit_button = self._buttons.addButton(
            "Submit",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._submit_button.setObjectName("confirmRealSubmission")
        self._buttons.rejected.connect(self.reject)
        self._submit_button.clicked.connect(self._validate_and_accept)
        layout.addWidget(self._buttons)

    def take_temporary_password(self) -> str | None:
        if self._password is None:
            return None
        value = self._password.text()
        self._password.clear()
        return value or None

    @Slot()
    def _validate_and_accept(self) -> None:
        if (
            self._temporary_password_required
            and self._password is not None
            and not self._password.text()
        ):
            QMessageBox.critical(
                self,
                "Password required",
                "Enter the password for this submission.",
            )
            return
        self._submit_button.setEnabled(False)
        self.accept()


class _SubmissionSignals(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class ProjectSubmissionWorker(QRunnable):
    """Run all remote operations outside the Qt GUI thread."""

    def __init__(
        self,
        service: ProjectSubmissionService,
        request: (
            NewProjectSubmissionRequest
            | ExistingProjectStepSubmissionRequest
            | TransportConvergenceSubmissionRequest
        ),
    ) -> None:
        super().__init__()
        if not isinstance(service, ProjectSubmissionService):
            raise TypeError("service must be a ProjectSubmissionService")
        if not isinstance(
            request,
            (
                NewProjectSubmissionRequest,
                ExistingProjectStepSubmissionRequest,
                TransportConvergenceSubmissionRequest,
            ),
        ):
            raise TypeError("request must be a supported project submission request")
        self.signals = _SubmissionSignals()
        self._service: ProjectSubmissionService | None = service
        self._request: (
            NewProjectSubmissionRequest
            | ExistingProjectStepSubmissionRequest
            | TransportConvergenceSubmissionRequest
            | None
        ) = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        service = self._service
        request = self._request
        if service is None or request is None:
            record_submission_lifecycle_event(
                "worker_failure_emitted",
                exception_type="WorkerAlreadyUsed",
            )
            self.signals.failed.emit(
                RuntimeError("submission worker was already used")
            )
            record_submission_lifecycle_event("worker_finished_emitted")
            self.signals.finished.emit(self)
            return
        project_id, step_kind = _worker_request_context(request)
        record_submission_lifecycle_event(
            "worker_started",
            project_id=project_id,
            step_kind=step_kind,
        )
        result: ProjectSubmissionResult | None = None
        try:
            if isinstance(request, NewProjectSubmissionRequest):
                result = service.create_and_submit_project(
                    request,
                    progress=self.signals.progress.emit,
                )
            elif isinstance(request, ExistingProjectStepSubmissionRequest):
                result = service.continue_project_with_step2(
                    request,
                    progress=self.signals.progress.emit,
                )
            else:
                result = service.continue_project_with_step3(
                    request,
                    progress=self.signals.progress.emit,
                )
            if not isinstance(result, ProjectSubmissionResult):
                raise RuntimeError("submission service returned an invalid result")
        except Exception as error:
            record_submission_lifecycle_event(
                "worker_failure_emitted",
                project_id=project_id,
                step_kind=step_kind,
                job_id=(
                    error.job_id
                    if isinstance(error, ProjectSubmissionError)
                    else None
                ),
                exception_type=type(error).__name__,
            )
            self.signals.failed.emit(error)
        else:
            record_submission_lifecycle_event(
                "worker_success_emitted",
                project_id=result.project.project_id,
                step_kind=result.step.kind,
                job_id=result.job_id,
            )
            self.signals.succeeded.emit(result)
        finally:
            self._request = None
            self._service = None
            record_submission_lifecycle_event(
                "worker_finished_emitted",
                project_id=(
                    result.project.project_id
                    if isinstance(result, ProjectSubmissionResult)
                    else project_id
                ),
                step_kind=step_kind,
                job_id=(
                    result.job_id
                    if isinstance(result, ProjectSubmissionResult)
                    else None
                ),
            )
            self.signals.finished.emit(self)


def _worker_request_context(
    request: (
        NewProjectSubmissionRequest
        | ExistingProjectStepSubmissionRequest
        | TransportConvergenceSubmissionRequest
    ),
) -> tuple[UUID | None, ProjectStepKind]:
    if isinstance(request, ExistingProjectStepSubmissionRequest):
        return request.project.project_id, ProjectStepKind.MOLECULE_AU_OPT
    if isinstance(request, TransportConvergenceSubmissionRequest):
        return (
            request.project.project_id,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
    return None, request.starting_step


def submission_error_presentation(
    error: object,
    request: (
        NewProjectSubmissionRequest
        | ExistingProjectStepSubmissionRequest
        | TransportConvergenceSubmissionRequest
        | None
    ) = None,
) -> SubmissionErrorPresentation:
    """Map typed submission failures to stable, secret-safe GUI feedback."""

    title = "Submission failed"
    message = str(error) if isinstance(error, Exception) else "Submission failed"
    if isinstance(error, SubmissionOutcomeUnknown):
        title = "Submission outcome unknown"
    elif isinstance(error, ProjectChecksumError):
        title = "Remote checksum verification failed"
    elif isinstance(error, MissingClusterSettingsError):
        title = "Cluster Settings required"
    elif isinstance(error, AuthenticationError):
        title = "Authentication failed"
    elif isinstance(error, PasswordRequiredError):
        title = "Password required"
    elif isinstance(error, ConnectionTestError):
        title = "Connection failed"
    elif isinstance(error, HostKeyMismatch):
        title = "SSH host key mismatch"
    elif isinstance(error, RemoteExecutorError):
        title = "Connection failed"
    elif isinstance(error, SlurmDiscoveryError):
        scheduler = _request_scheduler_name(request)
        title = f"{scheduler} not detected" if scheduler else "Scheduler not detected"
        server = request.profile.name if request is not None else "the selected server"
        message = (
            f"{scheduler or 'The scheduler'} could not be detected automatically on {server}.\n"
            "No remote calculation project was created.\n"
            "Retry detection or enter the scheduler command directory manually."
        )
    elif isinstance(error, SlurmConfigurationError):
        scheduler = _request_scheduler_name(request)
        title = (
            f"{scheduler} configuration invalid"
            if scheduler
            else "Scheduler configuration invalid"
        )
    elif isinstance(error, TransportConvergenceConflictError):
        title = "Step-3 continuation conflict"
    elif isinstance(error, ProjectContinuationConflictError):
        title = "Step-2 continuation conflict"
    elif isinstance(error, ProjectAllocationError):
        title = "Remote project creation failed"
    elif isinstance(error, ProjectPreparationError):
        title = "Remote input preparation failed"
    elif isinstance(error, SbatchRejectedError):
        scheduler = _request_scheduler_name(request)
        title = f"{scheduler or 'Scheduler'} submission failed"
        server = request.profile.name if request is not None else "Unknown"
        remote_path = getattr(error, "remote_project_path", None)
        project = (
            PurePosixPath(remote_path).name
            if isinstance(remote_path, str) and remote_path
            else _request_project_name(request)
        )
        message = (
            f"The project and verified input files were created, but "
            f"{scheduler or 'the scheduler'} "
            "rejected the job submission.\n"
            f"Server: {server}\n"
            f"Project: {project}\n"
            f"Reason: {message}"
        )
    elif isinstance(error, ProjectStateRecordingError):
        title = "Project state update failed"

    if request is not None and request.supplied_password:
        message = message.replace(request.supplied_password, "[redacted]")
    remote_path = getattr(error, "remote_project_path", None)
    if (
        isinstance(remote_path, str)
        and remote_path
        and remote_path not in message
    ):
        message += f"\nRemote project: {remote_path}"
    return SubmissionErrorPresentation(title, message)


def _request_project_name(
    request: (
        NewProjectSubmissionRequest
        | ExistingProjectStepSubmissionRequest
        | TransportConvergenceSubmissionRequest
        | None
    ),
) -> str:
    if isinstance(request, NewProjectSubmissionRequest):
        return request.base_name
    if isinstance(request, ExistingProjectStepSubmissionRequest):
        return request.project.remote_directory_name
    if isinstance(request, TransportConvergenceSubmissionRequest):
        return request.project.remote_directory_name
    return "Unknown"


def resource_summary(preset: SlurmExecutionPreset) -> str:
    if not isinstance(preset, SlurmExecutionPreset):
        raise TypeError("resource summary requires a SlurmExecutionPreset")
    if preset.scheduler_kind is SchedulerKind.LSF:
        return (
            f"LSF: {preset.nodes} execution host"
            f"{'s' if preset.nodes != 1 else ''}; "
            f"{preset.ntasks} MPI job slots / ranks; pure MPI; "
            f"{preset.runtime_minutes / 60.0:.1f} hours maximum; "
            f"{preset.memory_gb} GB LSF rusage memory reservation"
        )
    return (
        f"Slurm: {preset.nodes} node{'s' if preset.nodes != 1 else ''}; "
        f"{preset.ntasks} MPI tasks; {preset.cpus_per_task} CPU/task; "
        f"{preset.runtime_minutes / 60.0:.1f} hours maximum; "
        f"{preset.memory_gb} GB/node memory limit"
    )


def _request_scheduler_name(
    request: (
        NewProjectSubmissionRequest
        | ExistingProjectStepSubmissionRequest
        | TransportConvergenceSubmissionRequest
        | None
    ),
) -> str:
    if request is None or request.profile.execution_preset is None:
        return ""
    return scheduler_display_name(request.profile.execution_preset.scheduler_kind)


def email_notification_summary(profile: ServerProfile) -> str:
    """Return the read-only notification value shown by submission dialogs."""

    if not isinstance(profile, ServerProfile):
        raise TypeError("email notification summary requires a ServerProfile")
    settings = profile.slurm_mail_settings
    return "Off" if settings is None else settings.recipient


def optimization_summary(settings: AimsOptimizationSettings) -> str:
    if not isinstance(settings, AimsOptimizationSettings):
        raise TypeError("optimization summary requires AimsOptimizationSettings")
    spin = "spin collinear" if settings.spin.enabled else "spin none"
    return (
        f"{settings.xc.value.upper()} / {settings.species_accuracy.value} / "
        f"{settings.vdw.value} / {spin} / charge {settings.total_charge:g}"
    )


def _submission_settings_summary(
    settings: AimsOptimizationSettings | TransportConvergenceSettings,
) -> str:
    if isinstance(settings, AimsOptimizationSettings):
        return optimization_summary(settings)
    if not isinstance(settings, TransportConvergenceSettings):
        raise TypeError("submission settings are unsupported")
    spin = "spin collinear" if settings.spin.enabled else "spin none"
    return (
        f"{settings.xc.value.upper()} / {settings.species_accuracy.value} / "
        f"{spin} / charge {settings.total_charge:g}; Gaussian "
        f"{settings.occupation_width:g}; SCF limit {settings.sc_iter_limit}"
    )


def step_label(step: ProjectStepKind) -> str:
    for label, value in _STEP_CHOICES:
        if value is step:
            return label
    if step is ProjectStepKind.TRANSPORT_CONVERGENCE:
        return "Step 3 — Transport convergence"
    raise ValueError("submission labels support only Step 1, Step 2, or Step 3")
