"""Minimal asynchronous project recovery UI for one selected server."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from uuid import UUID

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_evidence import SLURM_TASK_OUT_OF_MEMORY
from moltage.aitranss.output import aitranss_failure_reason
from moltage.app.connection_service import (
    AuthenticationError,
    ConnectionTestError,
    PasswordRequiredError,
)
from moltage.app.local_project_index import (
    LocalProjectIndexError,
    RecycledProjectReference,
)
from moltage.app.project_geometry import (
    ProjectGeometryViewError,
    ProjectGeometryViewKind,
    ProjectGeometryViewRequest,
    ProjectGeometryViewResult,
    ProjectGeometryViewService,
    project_geometry_view_kinds,
)
from moltage.app.project_management import (
    ACTIVE_OR_UNRESOLVED_DELETE_MESSAGE,
    PermanentDensityTaskDeletionRequest,
    PermanentProjectDeletionRequest,
    PermanentProjectDeletionResult,
    ProjectManagementError,
    ProjectManagementService,
    density_task_deletion_block_reason,
    project_deletion_block_reason,
)
from moltage.app.project_presentation import (
    ProjectPresentationRecord,
    ProjectSortCriterion,
    ProjectViewMode,
    format_project_submission_timestamp,
    project_presentation_record,
    sort_project_presentations,
)
from moltage.app.project_recovery import (
    ProjectDiscoveryResult,
    ProjectProfileRebindRequired,
    ProjectRecoveryError,
    ProjectRecoveryService,
    ProjectRecoverySnapshot,
    Step3OomCancellationError,
    Step3OomCancellationOutcome,
    Step3OomCancellationRequest,
    Step3OomCancellationResult,
    StepRuntimeEvidence,
    slurm_failure_reason,
)
from moltage.app.orca_recovery import OrcaRecoveryService
from moltage.app.orca_wbl import (
    OrcaWblRequest,
    OrcaWblService,
    OrcaWblServiceResult,
)
from moltage.app.task_restart import (
    ProjectTaskCancellationOutcome,
    ProjectTaskRestartError,
    ProjectTaskRestartRequest,
    ProjectTaskRestartResult,
    ProjectTaskRestartService,
)
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.server_profile import ServerProfile
from moltage.domain.server_profile import (
    SlurmExecutionPreset,
    runtime_hours_from_minutes,
    runtime_minutes_from_hours,
)
from moltage.app.transport_submission import (
    Step3RetryRequest,
    Step4ExplicitRetryRequest,
    TransportSubmissionResult,
    TransportWorkflowSubmissionService,
)
from moltage.gui.project_submission import (
    email_notification_summary,
    optimization_summary,
    resource_summary,
)
from moltage.gui.project_list_view import (
    PROJECT_PRESENTATION_ROLE,
    PROJECT_UUID_ROLE,
    TILE_GRID_SIZE,
    ProjectItemDelegate,
    ProjectSortButton,
)
from moltage.gui.orca_dialogs import OrcaWblSettingsDialog, OrcaWblWorker
from moltage.gui.workspace_tabs import (
    OrcaWblWorkspaceRequest,
    TransmissionWorkspaceRequest,
)
from moltage.orca.wbl_artifacts import wbl_presentation_from_result
from moltage.remote.executor import (
    RemoteExecutorError,
)
from moltage.remote.project_delete import (
    RemoteProjectDeletionError,
    RemoteProjectDeletionOutcomeUnknown,
)
from moltage.remote.known_hosts import (
    HostKeyMismatch,
    KnownHostStore,
    UnknownHostKey,
)
from moltage.remote.secrets import SecretStore
from moltage.remote.slurm_discovery import (
    SlurmConfigurationError,
    SlurmDiscoveryError,
)
from moltage.remote.slurm_cancel import SlurmCancellationError
from moltage.remote.slurm_status import SlurmStatusError
from moltage.app.density_workflow import (
    DensityCancellationError,
    DensityCancellationOutcome,
    DensityCancellationResult,
    DensityTask,
)
from moltage.gui.density_workspace import DensityWorker
from moltage.gui.status_refresh import StatusRefreshSession, StatusRefreshTimeout


@dataclass(frozen=True, slots=True)
class RecoveryErrorPresentation:
    title: str
    message: str


class _RecoverySignals(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    stopped = Signal()
    finished = Signal(object)
    density_discovered = Signal(object)


@dataclass(frozen=True, slots=True)
class _ProjectRefreshResult:
    recovery: ProjectDiscoveryResult | ProjectRecoverySnapshot
    density_discovery: object | None = None


class OrcaCancellationWorker(QRunnable):
    """Request cancellation for one revalidated active ORCA job."""

    def __init__(
        self,
        service: OrcaRecoveryService,
        profile: ServerProfile,
        project,
        supplied_password: str | None,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._profile = profile
        self._project = project
        self._supplied_password = supplied_password
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.cancel_active_job(
                self._profile,
                self._project,
                supplied_password=self._supplied_password,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._supplied_password = None
            self.signals.finished.emit(self)


class ProjectGeometryViewWorker(QRunnable):
    """Read and parse one requested project geometry off the GUI thread."""

    def __init__(
        self,
        service: ProjectGeometryViewService,
        request: ProjectGeometryViewRequest,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.load(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)


class Step3RetryWorker(QRunnable):
    """Run one explicitly confirmed retry off the GUI thread."""

    def __init__(
        self,
        service: TransportWorkflowSubmissionService,
        request: Step3RetryRequest,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.retry_step3(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)


class Step3OomCancellationWorker(QRunnable):
    """Run one explicitly confirmed exact-job cancellation off the GUI thread."""

    def __init__(
        self,
        service: ProjectRecoveryService,
        request: Step3OomCancellationRequest,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.cancel_active_step3_oom_job(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)


class ProjectTaskRestartWorker(QRunnable):
    """Abort one exact active task and prepare its local restart draft."""

    def __init__(
        self,
        service: ProjectTaskRestartService,
        request: ProjectTaskRestartRequest,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.abort_and_prepare(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)


class Step4ExplicitRetryWorker(QRunnable):
    """Run one explicitly confirmed Step-4 overlap retry off the GUI thread."""

    def __init__(
        self,
        service: TransportWorkflowSubmissionService,
        request: Step4ExplicitRetryRequest,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.retry_step4_with_explicit_self_energy(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)


class ProjectPermanentDeletionWorker(QRunnable):
    """Delete one already-confirmed project without blocking the GUI thread."""

    def __init__(
        self,
        service: ProjectManagementService,
        request: PermanentProjectDeletionRequest | PermanentDensityTaskDeletionRequest,
    ) -> None:
        super().__init__()
        self.signals = _RecoverySignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.permanently_delete(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)


class DeleteProjectDialog(QDialog):
    """Choose reversible local recycle or guarded permanent server deletion."""

    def __init__(
        self,
        target: ProjectRecoverySnapshot | DensityTask,
        *,
        permanent_block_reason: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if isinstance(target, ProjectRecoverySnapshot):
            delete_block_reason = project_deletion_block_reason(
                target.project,
                scheduler_status_kind=target.scheduler_status_kind,
            )
            display_name = target.project.display_name
            noun = "project"
        elif isinstance(target, DensityTask):
            delete_block_reason = density_task_deletion_block_reason(target)
            display_name = target.name
            noun = "density task"
        else:
            raise TypeError("Delete requires a recovery snapshot or density task")
        if delete_block_reason is not None:
            raise ProjectManagementError(delete_block_reason)
        self._noun = noun
        self.setWindowTitle("Delete Task" if noun == "density task" else "Delete Project")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Task:" if noun == "density task" else "Project:", QLabel(display_name, self))
        layout.addLayout(form)
        self._explanation = QLabel(self)
        self._explanation.setObjectName("deleteProjectExplanation")
        self._explanation.setWordWrap(True)
        layout.addWidget(self._explanation)
        self._permanent = QCheckBox(
            f"Also delete server {noun} files permanently",
            self,
        )
        self._permanent.setObjectName("deleteServerFilesPermanently")
        self._permanent.setChecked(False)
        self._permanent.setEnabled(permanent_block_reason is None)
        if permanent_block_reason is not None:
            self._permanent.setToolTip(permanent_block_reason)
        self._permanent.toggled.connect(self._update_delete_mode)
        layout.addWidget(self._permanent)
        self._permanent_block = QLabel(permanent_block_reason or "", self)
        self._permanent_block.setObjectName("deleteProjectPermanentBlock")
        self._permanent_block.setWordWrap(True)
        self._permanent_block.setVisible(permanent_block_reason is not None)
        layout.addWidget(self._permanent_block)
        buttons = QDialogButtonBox(self)
        self._confirm = buttons.addButton(
            "Move to Recycle Bin",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._confirm.setObjectName("deleteProjectConfirm")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_delete_mode(False)

    def delete_server_files_permanently(self) -> bool:
        return self._permanent.isChecked()

    @Slot(bool)
    def _update_delete_mode(self, permanent: bool) -> None:
        if permanent:
            self._explanation.setText(
                f"This permanently deletes the exact managed {self._noun} directory "
                "from the server. It cannot be restored from Project Recycle Bin."
            )
            self._confirm.setText("Delete Permanently")
            return
        self._explanation.setText(
            f"The {self._noun} will be removed from the main Project Manager and moved "
            "to Project Recycle Bin. Server files and running jobs will not be "
            "changed."
        )
        self._confirm.setText("Move to Recycle Bin")


class CalculationProjectsDialog(QDialog):
    """Discover, display, refresh, and recover one managed project at a time."""

    geometry_workspace_requested = Signal(object)
    transmission_workspace_requested = Signal(object)
    restart_draft_requested = Signal(object)
    project_snapshot_updated = Signal(object)
    density_workspace_requested = Signal(object)
    orca_wbl_workspace_requested = Signal(object)
    orca_wbl_operation_status = Signal(object, str)
    orca_optimization_resubmit_requested = Signal(object)

    def __init__(
        self,
        profiles: Iterable[ServerProfile],
        last_selected_profile_id: UUID | None,
        recovery_service: ProjectRecoveryService,
        secret_store: SecretStore,
        known_hosts: KnownHostStore,
        parent: QWidget | None = None,
        *,
        project_geometry_service: ProjectGeometryViewService | None = None,
        project_task_restart_service: ProjectTaskRestartService | None = None,
        transport_submission_service: TransportWorkflowSubmissionService | None = None,
        project_management_service: ProjectManagementService | None = None,
        project_workspace_open: Callable[[UUID], bool] | None = None,
        project_has_external_operation: Callable[[UUID], bool] | None = None,
        density_service=None,
        orca_recovery_service: OrcaRecoveryService | None = None,
        orca_wbl_service: OrcaWblService | None = None,
    ) -> None:
        super().__init__(parent)
        checked_profiles = tuple(profiles)
        if not checked_profiles:
            raise ValueError("at least one saved server profile is required")
        if any(not isinstance(item, ServerProfile) for item in checked_profiles):
            raise TypeError("profiles must contain ServerProfile records")
        self._recovery_service = recovery_service
        self._density_service = density_service
        self._orca_recovery_service = orca_recovery_service
        self._orca_wbl_service = orca_wbl_service
        self._density_tasks = ()
        self._density_problems = ()
        self._project_geometry_service = project_geometry_service
        self._project_task_restart_service = project_task_restart_service
        self._secret_store = secret_store
        self._known_hosts = known_hosts
        self._transport_submission_service = transport_submission_service
        self._project_management_service = project_management_service
        self._project_workspace_open = project_workspace_open
        if project_has_external_operation is not None and not callable(
            project_has_external_operation
        ):
            raise TypeError("external project-operation lookup must be callable")
        self._project_has_external_operation = project_has_external_operation
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._workers: set[object] = set()
        self._snapshots: tuple[ProjectRecoverySnapshot, ...] = ()
        self._records: tuple[ProjectPresentationRecord, ...] = ()
        self._recycled_snapshot_cache: dict[UUID, ProjectRecoverySnapshot | DensityTask] = {}
        self._selected_snapshot: ProjectRecoverySnapshot | None = None
        self._selected_profile: ServerProfile | None = None
        self._sort_criterion = ProjectSortCriterion.SUBMISSION_DATE
        self._sort_reversed = False
        self._view_mode = ProjectViewMode.DETAILS
        self._in_recycle_bin = False
        self._busy = False
        self._stoppable_refresh_worker: StatusRefreshSession | None = None
        self._open_after_refresh = False
        self._pending_operation: tuple[str | None, bool] | None = None
        self._pending_geometry_selection: tuple[
            ProjectRecoverySnapshot,
            ProjectStepKind,
            ProjectGeometryViewKind,
            bool,
        ] | None = None
        self._pending_restart_selection: tuple[
            ProjectRecoverySnapshot,
            ProjectStepKind,
            bool,
        ] | None = None
        self._trust_retry_pending = False
        self._trust_retry_used = False
        self._kill_suppressed_attempts: set[tuple[UUID, str]] = set()
        self._restart_suppressed_attempts: set[tuple[UUID, str]] = set()
        self._density_cancel_suppressed_attempts: set[tuple[UUID, str]] = set()
        self._pending_orca_wbl_snapshot: ProjectRecoverySnapshot | None = None
        self._pending_orca_wbl_profile: ServerProfile | None = None

        self.setWindowTitle("Calculation Projects")
        self.setMinimumSize(620, 430)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        layout = QVBoxLayout(self)

        server_row = QFormLayout()
        self._server = QComboBox(self)
        self._server.setObjectName("projectsServerProfile")
        for profile in checked_profiles:
            self._server.addItem(profile.name, profile)
        selected_index = next(
            (
                index
                for index, profile in enumerate(checked_profiles)
                if profile.profile_id == last_selected_profile_id
            ),
            0,
        )
        self._server.setCurrentIndex(selected_index)
        self._server.currentIndexChanged.connect(self._server_changed)
        server_row.addRow("Server:", self._server)
        layout.addLayout(server_row)

        self._refresh = QPushButton("Refresh", self)
        self._refresh.setObjectName("projectsRefresh")
        self._refresh.clicked.connect(self._refresh_or_stop)
        layout.addWidget(self._refresh)

        project_header = QHBoxLayout()
        self._list_heading = QLabel("Projects:", self)
        self._list_heading.setObjectName("projectsListHeading")
        project_header.addWidget(self._list_heading)
        project_header.addStretch(1)
        self._sort = ProjectSortButton(self)
        self._sort.criterion_changed.connect(self._sort_criterion_changed)
        self._sort.reverse_requested.connect(self._reverse_sort)
        self._sort.update_label(self._sort_criterion, reversed_order=False)
        project_header.addWidget(self._sort)
        self._view = QComboBox(self)
        self._view.setObjectName("projectsView")
        for mode in ProjectViewMode:
            self._view.addItem(mode.value, mode)
        self._view.currentIndexChanged.connect(self._view_changed)
        project_header.addWidget(self._view)
        self._recycle_bin = QToolButton(self)
        self._recycle_bin.setObjectName("projectsRecycleBin")
        self._recycle_bin.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self._recycle_bin.setToolTip("Project Recycle Bin")
        self._recycle_bin.clicked.connect(self._show_recycle_bin)
        project_header.addWidget(self._recycle_bin)
        self._back_to_projects = QPushButton("Back to Projects", self)
        self._back_to_projects.setObjectName("projectsBackFromRecycle")
        self._back_to_projects.clicked.connect(self._show_projects)
        self._back_to_projects.hide()
        project_header.addWidget(self._back_to_projects)
        layout.addLayout(project_header)
        self._project_list = QListWidget(self)
        self._project_list.setObjectName("projectsList")
        self._project_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._project_delegate = ProjectItemDelegate(
            lambda: self._view_mode,
            self._project_list,
        )
        self._recycle_delegate = QStyledItemDelegate(self._project_list)
        self._project_list.setItemDelegate(self._project_delegate)
        self._project_list.viewport().installEventFilter(self)
        self._project_list.currentItemChanged.connect(self._selection_changed)
        layout.addWidget(self._project_list, stretch=1)

        self._status = QLabel(
            "Press Refresh to discover managed projects on the selected server.",
            self,
        )
        self._status.setObjectName("projectsStatus")
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._status)

        self._busy_area = QWidget(self)
        self._busy_area.setObjectName("projectsBusyArea")
        busy_layout = QHBoxLayout(self._busy_area)
        busy_layout.setContentsMargins(0, 0, 0, 0)
        self._busy_indicator = QProgressBar(self._busy_area)
        self._busy_indicator.setObjectName("projectsBusyIndicator")
        self._busy_indicator.setRange(0, 0)
        self._busy_indicator.setTextVisible(False)
        self._progress_text = QLabel(self._busy_area)
        self._progress_text.setObjectName("projectsProgressText")
        self._progress_text.setWordWrap(True)
        busy_layout.addWidget(self._busy_indicator)
        busy_layout.addWidget(self._progress_text, stretch=1)
        self._busy_area.hide()
        layout.addWidget(self._busy_area)

        primary_actions = QHBoxLayout()
        primary_actions.setObjectName("projectsPrimaryActions")
        workflow_actions = QHBoxLayout()
        workflow_actions.setObjectName("projectsWorkflowActions")
        self._open = QPushButton("Open / Recover", self)
        self._open.setObjectName("projectsOpen")
        self._open.clicked.connect(self._open_selected)
        self._refresh_status = QPushButton("Refresh Status", self)
        self._refresh_status.setObjectName("projectsRefreshStatus")
        self._refresh_status.clicked.connect(self._refresh_selected)
        self._kill_step3 = QPushButton("Kill Job", self)
        self._kill_step3.setObjectName("projectsKillStep3")
        self._kill_step3.clicked.connect(self._kill_selected_step3)
        self._cancel_orca = QPushButton("Cancel ORCA Job...", self)
        self._cancel_orca.setObjectName("projectsCancelOrca")
        self._cancel_orca.clicked.connect(self._cancel_selected_orca)
        self._resubmit_orca = QPushButton("Resubmit Optimization...", self)
        self._resubmit_orca.setObjectName("projectsResubmitOrca")
        self._resubmit_orca.clicked.connect(self._resubmit_selected_orca)
        self._retry_step3 = QPushButton("Resubmit Step 3...", self)
        self._retry_step3.setObjectName("projectsRetryStep3")
        self._retry_step3.clicked.connect(self._retry_selected_step3)
        self._retry_step4 = QPushButton("Retry with explicit self-energy...", self)
        self._retry_step4.setObjectName("projectsRetryStep4Explicit")
        self._retry_step4.clicked.connect(self._retry_selected_step4)
        self._view_transmission = QPushButton("View Transmission", self)
        self._view_transmission.setObjectName("projectsViewTransmission")
        self._view_transmission.clicked.connect(self._view_selected_transmission)
        self._view_orca_wbl = QPushButton("View WBL Transmission", self)
        self._view_orca_wbl.setObjectName("projectsViewOrcaWbl")
        self._view_orca_wbl.clicked.connect(self._view_selected_orca_wbl)
        self._delete_project = QPushButton("Delete Project...", self)
        self._delete_project.setObjectName("projectsDelete")
        self._delete_project.clicked.connect(self._delete_selected_project)
        self._restore_project = QPushButton("Restore", self)
        self._restore_project.setObjectName("projectsRestore")
        self._restore_project.clicked.connect(self._restore_selected_project)
        self._restore_project.hide()
        self._close = QPushButton("Close", self)
        self._close.setObjectName("projectsClose")
        self._close.clicked.connect(self.reject)
        primary_actions.addWidget(self._open)
        primary_actions.addWidget(self._refresh_status)
        primary_actions.addWidget(self._view_transmission)
        primary_actions.addWidget(self._view_orca_wbl)
        primary_actions.addStretch(1)
        primary_actions.addWidget(self._delete_project)
        primary_actions.addWidget(self._restore_project)

        workflow_actions.addWidget(self._kill_step3)
        workflow_actions.addWidget(self._cancel_orca)
        workflow_actions.addWidget(self._resubmit_orca)
        workflow_actions.addWidget(self._retry_step3)
        workflow_actions.addWidget(self._retry_step4)
        workflow_actions.addStretch(1)
        workflow_actions.addWidget(self._close)

        layout.addLayout(primary_actions)
        layout.addLayout(workflow_actions)
        self._update_controls()

    def selected_recovery(
        self,
    ) -> tuple[ProjectRecoverySnapshot, ServerProfile]:
        if self._selected_snapshot is None or self._selected_profile is None:
            raise RuntimeError("no recovered project was selected")
        return self._selected_snapshot, self._selected_profile

    def has_active_remote_operation(self, *, include_status_refresh: bool = True) -> bool:
        """Report worker ownership independently of dialog visibility."""

        if not include_status_refresh:
            return any(worker is not self._stoppable_refresh_worker for worker in self._workers)
        return self._busy or bool(self._workers)

    def stop_status_refresh(self) -> None:
        """Detach an active refresh immediately, including during app shutdown."""

        if self._stoppable_refresh_worker is not None:
            self._stoppable_refresh_worker.request_stop()

    def external_operation_state_changed(self) -> None:
        """Refresh controls after a main-window submission lifecycle change."""

        self._update_controls()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Open the geometry menu only for a direct status-indicator click."""

        if (
            watched is self._project_list.viewport()
            and event.type() is QEvent.Type.MouseButtonRelease
            and not self._busy
            and not self._in_recycle_bin
            and event.button()
            in {Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton}
        ):
            position = event.position().toPoint()
            item = self._project_list.itemAt(position)
            record = (
                item.data(PROJECT_PRESENTATION_ROLE)
                if item is not None
                else None
            )
            if isinstance(record, ProjectPresentationRecord):
                item_rect = self._project_list.visualItemRect(item)
                for indicator_index, rectangle in enumerate(
                    self._project_delegate.indicator_rects(
                        item_rect,
                        self._view_mode,
                        len(record.indicators),
                    )
                ):
                    if rectangle.contains(position):
                        self._project_list.setCurrentItem(item)
                        step_kind = record.indicators[indicator_index].step_kind
                        if step_kind is None:
                            continue
                        if event.button() is Qt.MouseButton.LeftButton:
                            self._show_step_geometry_menu(
                                record.snapshot,
                                step_kind,
                                event.globalPosition().toPoint(),
                            )
                        else:
                            self._show_step_task_menu(
                                record.snapshot,
                                step_kind,
                                event.globalPosition().toPoint(),
                            )
                        event.accept()
                        return True
            elif (
                isinstance(record, DensityTask)
                and event.button() is Qt.MouseButton.RightButton
            ):
                item_rect = self._project_list.visualItemRect(item)
                if any(
                    rectangle.contains(position)
                    for rectangle in self._project_delegate.density_indicator_rects(
                        item_rect, self._view_mode
                    )
                ):
                    self._project_list.setCurrentItem(item)
                    self._show_density_task_menu(
                        record, event.globalPosition().toPoint()
                    )
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _show_step_task_menu(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
        global_position,
    ) -> None:
        menu = self._step_task_menu(snapshot, step_kind)
        menu.exec(global_position)

    def _step_task_menu(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
    ) -> QMenu:
        """Expose cancellation only for the exact current QUEUED/RUNNING task."""

        menu = QMenu(self)
        step = next(
            (item for item in snapshot.project.steps if item.kind is step_kind),
            None,
        )
        if step is None:
            unavailable = menu.addAction("No submitted task is available")
            unavailable.setEnabled(False)
            return menu
        eligible = (
            snapshot.project.workflow_kind
            is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            and
            snapshot.active_step_kind is step_kind
            and step.state in {ProjectStepState.QUEUED, ProjectStepState.RUNNING}
            and step.job_id is not None
            and self._project_task_restart_service is not None
            and (snapshot.project.project_id, step.job_id)
            not in self._restart_suppressed_attempts
        )
        action = menu.addAction("Abort Task and Create Restart Draft...")
        action.setObjectName("abortTaskCreateRestartDraft")
        action.setEnabled(eligible)
        if eligible:
            action.triggered.connect(
                lambda _checked=False: self._request_task_restart(
                    snapshot,
                    step_kind,
                )
            )
        return menu

    def _request_task_restart(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
    ) -> None:
        if self._busy or self._project_task_restart_service is None:
            return
        step = next(item for item in snapshot.project.steps if item.kind is step_kind)
        if (
            snapshot.active_step_kind is not step_kind
            or step.state not in {ProjectStepState.QUEUED, ProjectStepState.RUNNING}
            or step.job_id is None
        ):
            return
        confirmed = self._confirm_rebind_if_needed(snapshot)
        if confirmed is None:
            return
        if not self._confirm_task_restart(snapshot, step_kind, step.job_id):
            self._status.setText("Task cancellation was cancelled.")
            return
        self._trust_retry_pending = False
        self._trust_retry_used = False
        self._start_task_restart(
            snapshot,
            step_kind,
            profile_rebind_confirmed=confirmed,
        )

    def _confirm_task_restart(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
        job_id: str,
    ) -> bool:
        number = tuple(ProjectStepKind).index(step_kind) + 1
        step = next(
            item for item in snapshot.project.steps if item.kind is step_kind
        )
        scheduler_name = scheduler_display_name(
            step.scheduler_kind or SchedulerKind.SLURM
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"Abort {scheduler_name} Task")
        box.setText(
            "Abort the exact active task and load its pre-run geometry as an "
            "editable restart draft?\n\n"
            f"Project: {snapshot.project.remote_directory_name}\n"
            f"Step: {number}\n"
            f"{scheduler_name} Job ID: {job_id}\n\n"
            "The original project files will not be overwritten. A modified "
            "Step 1–3 draft is submitted as a new project."
        )
        abort = box.addButton(
            "Abort and Load Draft",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is abort

    def _show_step_geometry_menu(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
        global_position,
    ) -> None:
        menu = self._step_geometry_menu(snapshot, step_kind)
        menu.exec(global_position)

    def _step_geometry_menu(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
    ) -> QMenu:
        """Build the exact context menu justified by one step's state."""

        menu = QMenu(self)
        if step_kind is ProjectStepKind.ORCA_WBL_TRANSMISSION:
            action = menu.addAction("View Transmission")
            action.setObjectName("viewOrcaWblTransmission")
            action.setEnabled(snapshot.can_view_orca_wbl)
            if action.isEnabled():
                action.triggered.connect(
                    lambda _checked=False: self._request_orca_wbl_view(snapshot)
                )
            return menu
        labels = {
            ProjectGeometryViewKind.INPUT: "View Input Geometry",
            ProjectGeometryViewKind.OUTPUT: "View Output Geometry",
            ProjectGeometryViewKind.TRANSPORT: "View Geometry",
        }
        choices = project_geometry_view_kinds(snapshot.project, step_kind)
        if not choices:
            unavailable = menu.addAction("No geometry available")
            unavailable.setEnabled(False)
            return menu
        for view_kind in choices:
            action = menu.addAction(labels[view_kind])
            action.setData(view_kind)
            action.triggered.connect(
                lambda _checked=False, selected=view_kind: (
                    self._request_step_geometry(
                        snapshot,
                        step_kind,
                        selected,
                    )
                )
            )
        return menu

    def _request_step_geometry(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
        view_kind: ProjectGeometryViewKind,
    ) -> None:
        if self._busy:
            return
        if self._project_geometry_service is None:
            self.show_geometry_view_error(
                ProjectGeometryViewError("Project geometry viewing is unavailable.")
            )
            return
        confirmed = self._confirm_rebind_if_needed(snapshot)
        if confirmed is None:
            return
        self._trust_retry_pending = False
        self._trust_retry_used = False
        self._start_geometry_view(
            snapshot,
            step_kind,
            view_kind,
            profile_rebind_confirmed=confirmed,
        )

    def update_profile(self, profile: ServerProfile) -> None:
        """Use a saved profile update for future dialog operations only."""

        if not isinstance(profile, ServerProfile):
            raise TypeError("profile update requires a ServerProfile")
        for index in range(self._server.count()):
            current = self._server.itemData(index)
            if (
                isinstance(current, ServerProfile)
                and current.profile_id == profile.profile_id
            ):
                self._server.setItemText(index, profile.name)
                self._server.setItemData(index, profile)
                if (
                    self._selected_profile is not None
                    and self._selected_profile.profile_id == profile.profile_id
                ):
                    self._selected_profile = profile
                return

    @Slot()
    def _discover(self) -> None:
        self._start_operation(None, profile_rebind_confirmed=False)

    @Slot()
    def _refresh_or_stop(self) -> None:
        worker = self._stoppable_refresh_worker
        if worker is None or worker not in self._workers:
            self._discover()
            return
        worker.request_stop()

    @Slot()
    def _refresh_selected(self) -> None:
        if self._current_density_task() is not None:
            self._refresh_density_task()
            return
        snapshot = self._current_snapshot()
        if snapshot is None:
            return
        confirmed = self._confirm_rebind_if_needed(snapshot)
        if confirmed is None:
            return
        self._open_after_refresh = False
        self._start_operation(
            snapshot.project.remote_project_path,
            profile_rebind_confirmed=confirmed,
        )

    @Slot()
    def _open_selected(self) -> None:
        if (task := self._current_density_task()) is not None:
            self.density_workspace_requested.emit((task, self._current_profile()))
            return
        snapshot = self._current_snapshot()
        if snapshot is None:
            return
        confirmed = self._confirm_rebind_if_needed(snapshot)
        if confirmed is None:
            return
        self._open_after_refresh = True
        self._start_operation(
            snapshot.project.remote_project_path,
            profile_rebind_confirmed=confirmed,
        )

    @Slot(object)
    def _sort_criterion_changed(self, criterion: object) -> None:
        if self._in_recycle_bin:
            return
        try:
            checked = ProjectSortCriterion(criterion)
        except (TypeError, ValueError):
            return
        self._sort_criterion = checked
        self._sort_reversed = False
        self._sort.update_label(checked, reversed_order=False)
        self._render_snapshots()

    @Slot()
    def _reverse_sort(self) -> None:
        if self._in_recycle_bin:
            return
        self._sort_reversed = not self._sort_reversed
        self._sort.update_label(
            self._sort_criterion,
            reversed_order=self._sort_reversed,
        )
        self._render_snapshots()

    @Slot()
    def _view_changed(self) -> None:
        if self._in_recycle_bin:
            return
        try:
            mode = ProjectViewMode(self._view.currentData())
        except (TypeError, ValueError):
            return
        selected_id = self._selected_project_id()
        self._view_mode = mode
        self._configure_project_view()
        self._project_list.viewport().update()
        self._select_project_id(selected_id)

    @Slot()
    def _show_recycle_bin(self) -> None:
        if self._busy:
            return
        service = self._project_management_service
        if service is None:
            self._show_error(
                ProjectManagementError("Project Recycle Bin is unavailable.")
            )
            return
        try:
            entries = service.recycled_projects()
        except Exception as error:
            self._show_local_project_management_error(error)
            return
        self._in_recycle_bin = True
        self._list_heading.setText("Project Recycle Bin:")
        self._sort.hide()
        self._view.hide()
        self._recycle_bin.hide()
        self._back_to_projects.show()
        self._render_recycled(entries)
        self._status.setText(
            f"Project Recycle Bin contains {len(entries)} local entr"
            f"{'y' if len(entries) == 1 else 'ies'}. Server files are untouched."
        )
        self._update_controls()

    @Slot()
    def _show_projects(self) -> None:
        if self._busy:
            return
        self._in_recycle_bin = False
        self._list_heading.setText("Projects:")
        self._sort.show()
        self._view.show()
        self._recycle_bin.show()
        self._back_to_projects.hide()
        self._configure_project_view()
        self._render_snapshots()
        self._status.setText(
            "Press Refresh to reconcile projects on the selected server."
        )

    @Slot()
    def _delete_selected_project(self) -> None:
        if self._busy:
            return
        target = self._current_density_task() or self._current_snapshot()
        service = self._project_management_service
        if target is None or service is None:
            return
        delete_block_reason = self._project_delete_block_reason(target)
        if delete_block_reason is not None:
            self._show_local_project_management_error(
                ProjectManagementError(delete_block_reason)
            )
            return
        block_reason = self._permanent_delete_block_reason(target)
        dialog = DeleteProjectDialog(
            target,
            permanent_block_reason=block_reason,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText("Deletion was cancelled.")
            return
        if not dialog.delete_server_files_permanently():
            try:
                if isinstance(target, DensityTask):
                    entry = service.move_density_task_to_recycle(
                        self._current_profile(), target
                    )
                else:
                    entry = service.move_to_recycle(
                        self._current_profile(), target
                    )
            except Exception as error:
                self._show_local_project_management_error(error)
                return
            self._recycled_snapshot_cache[entry.project_id] = target
            self._snapshots = tuple(
                item
                for item in self._snapshots
                if item.project.project_id != entry.project_id
            )
            self._density_tasks = tuple(
                item
                for item in self._density_tasks
                if item.task_id != entry.project_id
            )
            self._render_snapshots()
            self._status.setText(
                f"{entry.display_name} moved to Project Recycle Bin locally. "
                "Server files and running jobs were not changed."
            )
            return
        block_reason = self._permanent_delete_block_reason(target)
        if block_reason is not None:
            self._show_error(ProjectManagementError(block_reason))
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
            if not accepted:
                self._status.setText("Permanent deletion was cancelled.")
                return
            request = (
                PermanentDensityTaskDeletionRequest(profile, target, password)
                if isinstance(target, DensityTask)
                else PermanentProjectDeletionRequest(
                    profile,
                    target.project,
                    password,
                    scheduler_status_kind=target.scheduler_status_kind,
                )
            )
        except Exception as error:
            self._show_error(error)
            return
        worker = ProjectPermanentDeletionWorker(service, request)
        worker.signals.succeeded.connect(self._permanent_delete_succeeded)
        worker.signals.failed.connect(self._permanent_delete_failed)
        worker.signals.finished.connect(self._permanent_delete_finished)
        worker.signals.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress("Revalidating the exact managed project...")
        self._update_controls()
        self._thread_pool.start(worker)

    @Slot()
    def _restore_selected_project(self) -> None:
        if self._busy or not self._in_recycle_bin:
            return
        service = self._project_management_service
        item = self._project_list.currentItem()
        entry = (
            item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        )
        if service is None or not isinstance(entry, RecycledProjectReference):
            return
        try:
            restored = service.restore(entry)
            remaining = service.recycled_projects()
        except Exception as error:
            self._show_local_project_management_error(error)
            return
        self._recycled_snapshot_cache.pop(restored.project_id, None)
        self._render_recycled(remaining)
        self._status.setText(
            f"{restored.display_name} restored locally. Return to Projects and "
            "use Refresh to rediscover its authoritative remote state."
        )

    def _permanent_delete_block_reason(
        self,
        target: ProjectRecoverySnapshot | DensityTask,
    ) -> str | None:
        delete_block_reason = self._project_delete_block_reason(target)
        if delete_block_reason is not None:
            return delete_block_reason
        if isinstance(target, DensityTask):
            try:
                target_profile_id = UUID(target.data["context"]["profile_id"])
            except (KeyError, TypeError, ValueError):
                return "The density task has an invalid server-profile identity."
            target_id = target.task_id
        else:
            target_profile_id = target.project.server_profile_id
            target_id = target.project.project_id
        if target_profile_id != self._current_profile().profile_id:
            return (
                "Permanent deletion requires the authoritative item and selected "
                "server-profile UUIDs to match."
            )
        if self._project_workspace_open is None:
            return (
                "Permanent deletion safety cannot verify open project workspaces."
            )
        try:
            is_open = self._project_workspace_open(target_id)
        except Exception:
            return (
                "Permanent deletion safety could not verify open project workspaces."
            )
        if is_open:
            if isinstance(target, DensityTask):
                return (
                    "Close this density task's open calculation and result workspace "
                    "tabs before permanently deleting its server files."
                )
            return (
                "Close this project's open Geometry and Transmission workspace tabs "
                "before permanently deleting its server files."
            )
        return None

    def _project_delete_block_reason(
        self,
        target: ProjectRecoverySnapshot | DensityTask,
    ) -> str | None:
        if isinstance(target, DensityTask):
            reason = density_task_deletion_block_reason(target)
            target_id = target.task_id
        else:
            reason = project_deletion_block_reason(
                target.project,
                scheduler_status_kind=target.scheduler_status_kind,
            )
            target_id = target.project.project_id
        if reason is not None:
            return reason
        lookup = self._project_has_external_operation
        if lookup is None:
            return None
        try:
            active = lookup(target_id)
        except Exception:
            return ACTIVE_OR_UNRESOLVED_DELETE_MESSAGE
        return ACTIVE_OR_UNRESOLVED_DELETE_MESSAGE if active else None

    @Slot(object)
    def _permanent_delete_succeeded(self, result: object) -> None:
        if not isinstance(result, PermanentProjectDeletionResult):
            self._show_error(
                RuntimeError("Permanent project deletion returned invalid data")
            )
            return
        was_density_task = any(
            item.task_id == result.project_id for item in self._density_tasks
        )
        self._snapshots = tuple(
            item
            for item in self._snapshots
            if item.project.project_id != result.project_id
        )
        self._density_tasks = tuple(
            item for item in self._density_tasks if item.task_id != result.project_id
        )
        self._recycled_snapshot_cache.pop(result.project_id, None)
        self._render_snapshots()
        self._status.setText(
            "Permanently deleted server "
            f"{'density task' if was_density_task else 'project'} "
            f"{result.remote_project_path}."
        )

    @Slot(object)
    def _permanent_delete_failed(self, error: object) -> None:
        self._show_project_deletion_error(error)

    @Slot(object)
    def _permanent_delete_finished(self, worker: object) -> None:
        if not isinstance(worker, ProjectPermanentDeletionWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()

    def calculate_orca_wbl(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
        *,
        contact_atom_selector: Callable[[str], int | None] | None = None,
    ) -> None:
        """Start WBL from a recovered ORCA Geometry workspace."""

        if (
            not _orca_wbl_eligible(snapshot)
            or self._orca_wbl_service is None
            or self._busy
        ):
            return
        if not isinstance(profile, ServerProfile):
            self._show_error(TypeError("ORCA WBL requires a saved server profile"))
            return
        assert snapshot is not None and snapshot.optimized_structure is not None
        dialog_parent = self._interactive_operation_parent()
        if snapshot.connectivity is None:
            self._show_error(
                RuntimeError(
                    "ORCA WBL contact detection requires recovered molecular connectivity"
                )
            )
            return
        try:
            dialog = OrcaWblSettingsDialog(
                snapshot.optimized_structure,
                snapshot.connectivity,
                dialog_parent,
                contact_atom_selector=contact_atom_selector,
            )
        except Exception as error:
            self._show_error(error)
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText(
                "ORCA WBL setup closed; no analysis was started."
            )
            return
        settings = dialog.selected_settings()
        try:
            accepted, password = self._password_for(profile)
        except Exception as error:
            self._show_error(error)
            return
        if not accepted:
            self._status.setText(
                "ORCA WBL authentication cancelled; no analysis was started."
            )
            return
        request = OrcaWblRequest(
            profile,
            snapshot.project.remote_project_path,
            settings,
            password,
        )
        worker = OrcaWblWorker(self._orca_wbl_service, request)
        worker.signals.progress.connect(self._orca_wbl_progress)
        worker.signals.project_updated.connect(self._orca_wbl_project_updated)
        worker.signals.succeeded.connect(self._orca_wbl_succeeded)
        worker.signals.failed.connect(self._orca_wbl_failed)
        worker.signals.finished.connect(self._orca_wbl_finished)
        self._pending_orca_wbl_snapshot = snapshot
        self._pending_orca_wbl_profile = profile
        self._workers.add(worker)
        self._busy = True
        preparing = "Preparing verified ORCA WBL evidence..."
        self._show_busy_progress(preparing)
        self.orca_wbl_operation_status.emit(snapshot.project.project_id, preparing)
        self._update_controls()
        self._thread_pool.start(worker)
        QMessageBox.information(
            dialog_parent,
            "ORCA Step 2 started",
            "The WBL transmission analysis has started.\n\n"
            "Progress and the Step 2 status light will update in the current "
            "workspace and in Projects. The completed ORCA optimization result "
            "will be reused; no new geometry optimization is being run.",
        )

    @Slot(str)
    def _orca_wbl_progress(self, message: str) -> None:
        self._operation_progress(message)
        snapshot = self._pending_orca_wbl_snapshot
        if snapshot is not None and message:
            self.orca_wbl_operation_status.emit(
                snapshot.project.project_id,
                message,
            )

    @Slot(object)
    def _orca_wbl_project_updated(self, project: object) -> None:
        snapshot = self._pending_orca_wbl_snapshot
        if (
            snapshot is None
            or not isinstance(project, CalculationProject)
            or project.project_id != snapshot.project.project_id
        ):
            return
        step = next(
            item
            for item in project.steps
            if item.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
        )
        if step.state is ProjectStepState.RUNNING:
            message = "ORCA WBL transmission is running."
        elif step.state is ProjectStepState.SUCCEEDED:
            message = (
                "ORCA WBL transmission completed and provenance artifacts were "
                "saved (HYPOTHESIS)."
            )
        elif step.state is ProjectStepState.FAILED:
            message = step.last_error or "ORCA WBL transmission failed."
        else:
            return
        updated = replace(
            snapshot,
            project=project,
            active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
            status_message=message,
        )
        self._pending_orca_wbl_snapshot = updated
        self._replace_snapshot(updated)
        self.project_snapshot_updated.emit(updated)
        self._status.setText(message)
        self.orca_wbl_operation_status.emit(project.project_id, message)

    @Slot(object)
    def _orca_wbl_succeeded(self, result: object) -> None:
        if not isinstance(result, OrcaWblServiceResult):
            self._show_error(RuntimeError("ORCA WBL calculation returned invalid data"))
            return
        snapshot = self._pending_orca_wbl_snapshot
        if (
            snapshot is None
            or snapshot.project.project_id != result.project.project_id
        ):
            self._show_error(
                RuntimeError("ORCA project selection changed during WBL analysis")
            )
            return
        step = next(
            item
            for item in result.project.steps
            if item.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
        )
        evidence = step.orca_wbl_result
        if step.state is not ProjectStepState.SUCCEEDED or evidence is None:
            self._show_error(RuntimeError("ORCA WBL success result has no completed stage evidence"))
            return
        if (
            snapshot.project != result.project
            or snapshot.active_step_kind is not ProjectStepKind.ORCA_WBL_TRANSMISSION
        ):
            self._orca_wbl_project_updated(result.project)
        snapshot = self._pending_orca_wbl_snapshot
        assert snapshot is not None
        try:
            presentation = wbl_presentation_from_result(
                result.analysis,
                source_hashes={
                    "orca_opt.gbw": evidence.source_gbw_sha256,
                    "orca_wavefunction.json": evidence.wavefunction_json_sha256,
                },
            )
        except Exception as error:
            self._show_error(
                RuntimeError(
                    f"ORCA WBL completed, but result presentation failed: {error}"
                )
            )
            return
        updated = replace(
            snapshot,
            project=result.project,
            active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
            status_message=(
                "ORCA WBL transmission completed and provenance artifacts were "
                "saved (HYPOTHESIS)."
            ),
            orca_wbl_presentation=presentation,
        )
        self._replace_snapshot(updated)
        self.project_snapshot_updated.emit(updated)
        self._status.setText(updated.status_message)
        self.orca_wbl_workspace_requested.emit(
            OrcaWblWorkspaceRequest.from_snapshot(updated)
        )

    @Slot(object)
    def _orca_wbl_failed(self, error: object) -> None:
        snapshot = self._pending_orca_wbl_snapshot
        if snapshot is not None:
            presentation = recovery_error_presentation(error)
            self.orca_wbl_operation_status.emit(
                snapshot.project.project_id,
                f"{presentation.title}: {presentation.message}",
            )
        self._show_error(error)

    @Slot(object)
    def _orca_wbl_finished(self, worker: object) -> None:
        if not isinstance(worker, OrcaWblWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._pending_orca_wbl_snapshot = None
        self._pending_orca_wbl_profile = None
        self._hide_busy_progress()
        self._update_controls()

    @Slot()
    def _view_selected_orca_wbl(self) -> None:
        snapshot = self._current_snapshot()
        if snapshot is None or not snapshot.can_view_orca_wbl or self._busy:
            return
        self._request_orca_wbl_view(snapshot)

    @Slot()
    def _resubmit_selected_orca(self) -> None:
        snapshot = self._current_snapshot()
        if (
            not _orca_resubmit_eligible(snapshot)
            or self._busy
        ):
            return
        assert snapshot is not None
        structure = snapshot.optimized_structure or snapshot.submitted_structure
        assert structure is not None
        self.orca_optimization_resubmit_requested.emit(
            (snapshot, self._current_profile(), structure)
        )

    def _request_orca_wbl_view(
        self,
        snapshot: ProjectRecoverySnapshot,
    ) -> None:
        try:
            request = OrcaWblWorkspaceRequest.from_snapshot(snapshot)
        except Exception as error:
            self._show_error(error)
            return
        self.orca_wbl_workspace_requested.emit(request)

    @Slot()
    def _cancel_selected_orca(self) -> None:
        snapshot = self._current_snapshot()
        if (
            snapshot is None
            or snapshot.project.workflow_kind is not CalculationWorkflowKind.ORCA
            or snapshot.active_step.job_id is None
            or snapshot.active_step.state
            not in {
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
                ProjectStepState.UNKNOWN,
            }
            or self._orca_recovery_service is None
            or self._busy
        ):
            return
        scheduler = scheduler_display_name(
            snapshot.active_step.scheduler_kind or SchedulerKind.SLURM
        )
        answer = QMessageBox.question(
            self,
            "Cancel ORCA Job",
            f"Request cancellation of exact {scheduler} Job "
            f"{snapshot.active_step.job_id}?\n\n"
            "The result remains UNKNOWN until Refresh Status confirms a terminal state.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
        except Exception as error:
            self._show_error(error)
            return
        if not accepted:
            return
        worker = OrcaCancellationWorker(
            self._orca_recovery_service,
            profile,
            snapshot.project,
            password,
        )
        worker.signals.succeeded.connect(self._orca_cancel_succeeded)
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(self._orca_cancel_finished)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress(
            f"Revalidating {scheduler} Job {snapshot.active_step.job_id} before cancellation..."
        )
        self._update_controls()
        self._thread_pool.start(worker)

    @Slot(object)
    def _orca_cancel_succeeded(self, project: object) -> None:
        if not isinstance(project, CalculationProject):
            self._show_error(RuntimeError("ORCA cancellation returned invalid data"))
            return
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.project.project_id != project.project_id:
            self._show_error(RuntimeError("ORCA project selection changed during cancellation"))
            return
        updated = replace(
            snapshot,
            project=project,
            active_step_kind=next(
                step.kind
                for step in reversed(project.steps)
                if step.state is not ProjectStepState.NOT_STARTED
            ),
            status_message=(
                "ORCA cancellation was requested. Refresh Status to obtain the "
                "authoritative terminal scheduler state."
            ),
        )
        self._replace_snapshot(updated)
        self.project_snapshot_updated.emit(updated)
        self._status.setText(updated.status_message)

    @Slot(object)
    def _orca_cancel_finished(self, worker: object) -> None:
        if not isinstance(worker, OrcaCancellationWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()

    @Slot()
    def _kill_selected_step3(self) -> None:
        snapshot = self._current_snapshot()
        if snapshot is None or not snapshot.can_kill_step3_oom:
            return
        job_id = snapshot.active_step.job_id
        assert job_id is not None
        scheduler_name = scheduler_display_name(
            snapshot.active_step.scheduler_kind or SchedulerKind.SLURM
        )
        attempt_key = (snapshot.project.project_id, job_id)
        if attempt_key in self._kill_suppressed_attempts:
            return
        if not self._confirm_step3_oom_kill(job_id, scheduler_name):
            self._status.setText("Job cancellation was cancelled.")
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
            if not accepted:
                self._status.setText("Job cancellation was cancelled.")
                return
            request = Step3OomCancellationRequest(
                profile,
                snapshot.project,
                password,
            )
        except Exception as error:
            self._show_error(error)
            return
        worker = Step3OomCancellationWorker(
            self._recovery_service,
            request,
        )
        worker.signals.succeeded.connect(self._kill_succeeded)
        worker.signals.failed.connect(self._kill_failed)
        worker.signals.finished.connect(self._kill_finished)
        worker.signals.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress(
            f"Revalidating {scheduler_name} Job {job_id} before cancellation..."
        )
        self._update_controls()
        self._thread_pool.start(worker)

    def _confirm_step3_oom_kill(
        self,
        job_id: str,
        scheduler_name: str = "Slurm",
    ) -> bool:
        box, kill = self._step3_oom_kill_confirmation(job_id, scheduler_name)
        box.exec()
        return box.clickedButton() is kill

    def _step3_oom_kill_confirmation(
        self,
        job_id: str,
        scheduler_name: str = "Slurm",
    ) -> tuple[QMessageBox, QPushButton]:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"Kill {scheduler_name} Job")
        box.setText(
            "This job has reported out-of-memory failures but is still "
            "running.\n\n"
            f"Kill {scheduler_name} Job {job_id}?"
        )
        kill = box.addButton("Kill Job", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        return box, kill

    @Slot()
    def _retry_selected_step3(self) -> None:
        snapshot = self._current_snapshot()
        service = self._transport_submission_service
        if snapshot is None or not snapshot.can_retry_step3 or service is None:
            return
        profile = self._current_profile()
        preset = snapshot.step3_retry_preset
        if preset is None:
            self._show_error(
                ProjectRecoveryError(
                    snapshot.step3_retry_preparation_error
                    or "Authoritative previous Step-3 resources are unavailable. "
                    "Refresh before retrying."
                )
            )
            return
        dialog = Step3RetryDialog(snapshot, profile, preset, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText("Step-3 retry was cancelled.")
            return
        try:
            accepted, password = self._password_for(profile)
            if not accepted:
                self._status.setText("Step-3 retry was cancelled.")
                return
            request = Step3RetryRequest(
                profile,
                snapshot.project,
                dialog.selected_preset(),
                password,
            )
        except Exception as error:
            self._show_error(error)
            return
        worker = Step3RetryWorker(service, request)
        worker.signals.succeeded.connect(self._retry_succeeded)
        worker.signals.failed.connect(self._operation_failed)
        worker.signals.finished.connect(self._retry_finished)
        worker.signals.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress("Preparing explicit Step-3 retry...")
        self._update_controls()
        self._thread_pool.start(worker)

    @Slot()
    def _retry_selected_step4(self) -> None:
        snapshot = self._current_snapshot()
        service = self._transport_submission_service
        if (
            snapshot is None
            or not snapshot.can_retry_step4_explicit
            or service is None
        ):
            return
        profile = self._current_profile()
        dialog = Step4ExplicitRetryDialog(snapshot, profile, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText("Step-4 explicit-self-energy retry was cancelled.")
            return
        try:
            accepted, password = self._password_for(profile)
            if not accepted:
                self._status.setText("Step-4 explicit-self-energy retry was cancelled.")
                return
            assert snapshot.optimized_structure is not None
            assert snapshot.transport_evidence is not None
            assert snapshot.surface_proposal is not None
            assert snapshot.step4_self_energy_plan is not None
            assert snapshot.step4_attempt01_tcontrol is not None
            request = Step4ExplicitRetryRequest(
                profile,
                snapshot.project,
                snapshot.optimized_structure,
                snapshot.transport_evidence,
                snapshot.surface_proposal,
                snapshot.step4_self_energy_plan,
                snapshot.step4_attempt01_tcontrol,
                supplied_password=password,
            )
        except Exception as error:
            self._show_error(error)
            return
        worker = Step4ExplicitRetryWorker(service, request)
        worker.signals.succeeded.connect(self._retry_step4_succeeded)
        worker.signals.failed.connect(self._operation_failed)
        worker.signals.finished.connect(self._retry_step4_finished)
        worker.signals.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress("Preparing explicit Step-4 self-energy retry...")
        self._update_controls()
        self._thread_pool.start(worker)

    @Slot()
    def _view_selected_transmission(self) -> None:
        snapshot = self._current_snapshot()
        if snapshot is None or not snapshot.can_view_transmission:
            return
        try:
            request = TransmissionWorkspaceRequest.from_snapshot(snapshot)
        except Exception as error:
            self.show_transmission_view_error(error)
            return
        self.transmission_workspace_requested.emit(request)

    def show_transmission_view_error(self, error: object) -> None:
        """Present a local chart/workspace failure without changing science state."""

        message = next(
            (
                line.strip()
                for line in str(error).splitlines()
                if line.strip()
            ),
            "The validated transmission result could not be displayed.",
        )[:400]
        self._status.setText(f"Transmission view failed: {message}")
        if self.isVisible():
            QMessageBox.critical(self, "Transmission view failed", message)

    def show_geometry_view_error(self, error: object) -> None:
        """Present geometry inspection failure without changing project state."""

        presentation = recovery_error_presentation(error)
        if presentation.title == "Project recovery failed":
            presentation = RecoveryErrorPresentation(
                "Geometry view failed",
                presentation.message,
            )
        self._status.setText(
            f"{presentation.title}: {presentation.message}"
        )
        if self.isVisible():
            QMessageBox.critical(
                self,
                presentation.title,
                presentation.message,
            )

    @Slot(object)
    def _kill_succeeded(self, result: object) -> None:
        if not isinstance(result, Step3OomCancellationResult):
            self._show_error(RuntimeError("Job cancellation returned invalid data"))
            return
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.project.project_id != result.project_id:
            self._show_error(RuntimeError("cancelled project selection changed"))
            return
        scheduler_name = scheduler_display_name(
            snapshot.active_step.scheduler_kind or SchedulerKind.SLURM
        )
        attempt_key = (result.project_id, result.job_id)
        if result.outcome is Step3OomCancellationOutcome.ALREADY_TERMINAL:
            assert result.snapshot is not None
            self._clear_kill_suppression_for_project(result.project_id)
            self._replace_snapshot(result.snapshot)
            self._status.setText(result.snapshot.status_message)
            return
        self._kill_suppressed_attempts.add(attempt_key)
        if result.outcome is Step3OomCancellationOutcome.REQUESTED:
            self._status.setText(
                f"Cancellation requested for {scheduler_name} Job {result.job_id}. "
                "Use Refresh Status to retrieve its terminal scheduler state."
            )
        else:
            self._status.setText(
                f"Cancellation outcome for {scheduler_name} Job {result.job_id} "
                "is unknown "
                "after dispatch. Do not send another cancellation; use Refresh "
                "Status."
            )
        self._update_controls()

    @Slot(object)
    def _kill_failed(self, error: object) -> None:
        self._show_error(error)

    @Slot(object)
    def _kill_finished(self, worker: object) -> None:
        if not isinstance(worker, Step3OomCancellationWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()

    @Slot(object)
    def _retry_succeeded(self, result: object) -> None:
        if not isinstance(result, TransportSubmissionResult):
            self._show_error(RuntimeError("Step-3 retry returned invalid data"))
            return
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.project.project_id != result.project.project_id:
            self._show_error(RuntimeError("retried project selection changed"))
            return
        updated = replace(
            snapshot,
            project=result.project,
            active_step_kind=ProjectStepKind.TRANSPORT_CONVERGENCE,
            status_message=(
                "Step 3 retry submitted as "
                f"{scheduler_display_name(result.step.scheduler_kind or SchedulerKind.SLURM)} "
                f"job {result.job_id}; "
                f"output will be {result.step.slurm_output_filename}."
            ),
            optimized_structure=None,
            connectivity=None,
            transport_evidence=None,
            surface_proposal=None,
            transport_preparation_error=None,
            runtime_evidence=None,
            step3_retry_preset=None,
            step3_retry_preparation_error=None,
        )
        self._replace_snapshot(updated)
        self._status.setText(updated.status_message)

    @Slot(object)
    def _retry_finished(self, worker: object) -> None:
        if not isinstance(worker, Step3RetryWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()

    @Slot(object)
    def _retry_step4_succeeded(self, result: object) -> None:
        if not isinstance(result, TransportSubmissionResult):
            self._show_error(RuntimeError("Step-4 retry returned invalid data"))
            return
        snapshot = self._current_snapshot()
        if snapshot is None or snapshot.project.project_id != result.project.project_id:
            self._show_error(RuntimeError("retried project selection changed"))
            return
        updated = replace(
            snapshot,
            project=result.project,
            active_step_kind=ProjectStepKind.TRANSMISSION,
            status_message=(
                "Step 4 explicit-self-energy retry submitted as "
                f"{scheduler_display_name(result.step.scheduler_kind or SchedulerKind.SLURM)} job "
                f"{result.job_id}; output will be {result.step.slurm_output_filename}."
            ),
            optimized_structure=None,
            connectivity=None,
            transport_evidence=None,
            surface_proposal=None,
            transport_preparation_error=None,
            step4_attempt01_tcontrol=None,
            step4_tcontrol_settings=None,
            step4_self_energy_plan=None,
        )
        self._replace_snapshot(updated)
        self._status.setText(updated.status_message)

    @Slot(object)
    def _retry_step4_finished(self, worker: object) -> None:
        if not isinstance(worker, Step4ExplicitRetryWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()

    def _confirm_rebind_if_needed(
        self,
        snapshot: ProjectRecoverySnapshot,
    ) -> bool | None:
        if snapshot.profile_rebind_confirmed:
            return True
        if not snapshot.requires_profile_rebind:
            return False
        profile = self._current_profile()
        answer = QMessageBox.question(
            self,
            "Confirm local server binding",
            "This project was created with another local server-profile "
            "identity.\n\n"
            f"Current server: {profile.host}\n"
            f"Remote workspace: {profile.remote_project_root}\n\n"
            "Use this server profile to access the project on this computer?\n\n"
            "The historical server-profile ID in the remote manifest will not "
            "be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._status.setText("Local server-profile binding was not changed.")
            return None
        return True

    def _start_operation(
        self,
        remote_project_path: str | None,
        *,
        profile_rebind_confirmed: bool,
    ) -> None:
        if self._busy:
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
        except Exception as error:
            self._show_error(error)
            return
        if not accepted:
            self._status.setText("Project recovery was cancelled.")
            return
        self._pending_operation = (
            remote_project_path,
            profile_rebind_confirmed,
        )
        service, density_service = self._recovery_service, self._density_service

        def refresh(stop_token, progress):
            density = None
            if remote_project_path is None:
                result = service.discover_and_refresh(
                    profile, password, progress=progress, stop_token=stop_token,
                )
                if not isinstance(result, ProjectDiscoveryResult):
                    raise RuntimeError("project discovery returned an invalid result")
                if density_service is not None:
                    stop_token.checkpoint()
                    density = density_service.discover(profile, password, stop_token=stop_token)
            else:
                result = service.refresh_project(
                    profile, remote_project_path,
                    profile_rebind_confirmed=profile_rebind_confirmed,
                    supplied_password=password, progress=progress, stop_token=stop_token,
                )
                if not isinstance(result, ProjectRecoverySnapshot):
                    raise RuntimeError("project refresh returned an invalid result")
            stop_token.checkpoint()
            return _ProjectRefreshResult(result, density)

        worker = StatusRefreshSession(refresh, self)
        worker.succeeded.connect(self._refresh_succeeded)
        worker.failed.connect(self._operation_failed)
        worker.stopped.connect(self._operation_stopped)
        worker.finished.connect(self._operation_finished)
        worker.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._stoppable_refresh_worker = worker
        self._busy = True
        self._show_busy_progress(f"Connecting to {profile.name}...")
        self._update_controls()
        worker.start()

    @Slot(object)
    def _refresh_succeeded(self, result: object) -> None:
        if not isinstance(result, _ProjectRefreshResult):
            self._show_error(RuntimeError("project refresh returned invalid data"))
            return
        if result.density_discovery is not None:
            self._density_discovered(result.density_discovery)
        self._operation_succeeded(result.recovery)

    def _start_geometry_view(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
        view_kind: ProjectGeometryViewKind,
        *,
        profile_rebind_confirmed: bool,
    ) -> None:
        if self._busy:
            return
        service = self._project_geometry_service
        if service is None:
            self.show_geometry_view_error(
                ProjectGeometryViewError("Project geometry viewing is unavailable.")
            )
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
            if not accepted:
                self._status.setText("Geometry viewing was cancelled.")
                return
            request = ProjectGeometryViewRequest(
                profile,
                snapshot.project,
                step_kind,
                view_kind,
                profile_rebind_confirmed=profile_rebind_confirmed,
                supplied_password=password,
            )
        except Exception as error:
            self.show_geometry_view_error(error)
            return
        self._pending_geometry_selection = (
            snapshot,
            step_kind,
            view_kind,
            profile_rebind_confirmed,
        )
        worker = ProjectGeometryViewWorker(service, request)
        worker.signals.succeeded.connect(self._geometry_view_succeeded)
        worker.signals.failed.connect(self._geometry_view_failed)
        worker.signals.finished.connect(self._geometry_view_finished)
        worker.signals.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress(f"Connecting to {profile.name}...")
        self._update_controls()
        self._thread_pool.start(worker)

    def _start_task_restart(
        self,
        snapshot: ProjectRecoverySnapshot,
        step_kind: ProjectStepKind,
        *,
        profile_rebind_confirmed: bool,
    ) -> None:
        if self._busy:
            return
        service = self._project_task_restart_service
        if service is None:
            self._show_error(
                ProjectTaskRestartError("Task restart is unavailable.")
            )
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
            if not accepted:
                self._status.setText("Task cancellation was cancelled.")
                return
            request = ProjectTaskRestartRequest(
                profile,
                snapshot.project,
                step_kind,
                profile_rebind_confirmed=profile_rebind_confirmed,
                supplied_password=password,
            )
        except Exception as error:
            self._show_error(error)
            return
        self._pending_restart_selection = (
            snapshot,
            step_kind,
            profile_rebind_confirmed,
        )
        worker = ProjectTaskRestartWorker(service, request)
        worker.signals.succeeded.connect(self._task_restart_succeeded)
        worker.signals.failed.connect(self._task_restart_failed)
        worker.signals.finished.connect(self._task_restart_finished)
        worker.signals.progress.connect(self._operation_progress)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress(
            f"Revalidating Step {tuple(ProjectStepKind).index(step_kind) + 1} "
            "before cancellation..."
        )
        self._update_controls()
        self._thread_pool.start(worker)

    @Slot(str)
    def _operation_progress(self, message: str) -> None:
        if self._busy and message:
            self._show_busy_progress(message)

    def _password_for(self, profile: ServerProfile) -> tuple[bool, str | None]:
        if profile.save_password:
            saved = self._secret_store.get_password(profile.profile_id)
            if saved:
                return True, None
        dialog_parent = self._interactive_operation_parent()
        password, accepted = QInputDialog.getText(
            dialog_parent,
            "Password required",
            f"Password for {profile.username}@{profile.host}:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return False, None
        if not password:
            QMessageBox.critical(
                dialog_parent,
                "Password required",
                "Enter a password for this remote operation.",
            )
            return False, None
        return True, password

    def _interactive_operation_parent(self) -> QWidget:
        """Return the visible owner for prompts launched outside this dialog.

        Opening a recovered geometry accepts and hides the project manager.  ORCA
        Step 2 is then started from the main-window Calculation menu, so parenting
        its modal prompts to this hidden dialog can place them behind the main
        window on Windows and make submission appear to do nothing.
        """

        if self.isVisible():
            return self
        parent = self.parentWidget()
        return parent if parent is not None else self

    @Slot(object)
    def _operation_succeeded(self, result: object) -> None:
        self._trust_retry_pending = False
        self._trust_retry_used = False
        if isinstance(result, ProjectDiscoveryResult):
            self._kill_suppressed_attempts.clear()
            self._restart_suppressed_attempts.clear()
            self._snapshots = result.snapshots
            self._render_snapshots()
            if result.problems:
                self._status.setText(
                    f"Discovered {len(result.snapshots)} managed project(s); "
                    f"{len(result.problems)} malformed or inaccessible manifest(s) "
                    "were reported."
                )
            else:
                self._status.setText(
                    f"Discovered {len(result.snapshots)} managed project(s)."
                )
            if self._density_tasks or self._density_problems:
                self._status.setText(self._status.text() + f" {len(self._density_tasks)} density task(s). " + "\n".join(self._density_problems))
            return
        if not isinstance(result, ProjectRecoverySnapshot):
            self._show_error(RuntimeError("project recovery returned invalid data"))
            return
        self._clear_kill_suppression_for_project(result.project.project_id)
        self._restart_suppressed_attempts = {
            item
            for item in self._restart_suppressed_attempts
            if item[0] != result.project.project_id
        }
        self._replace_snapshot(result)
        self.project_snapshot_updated.emit(result)
        self._status.setText(result.status_message)
        if self._open_after_refresh:
            self._open_after_refresh = False
            structure_available = result.optimized_structure is not None or (
                result.project.workflow_kind is CalculationWorkflowKind.ORCA
                and result.submitted_structure is not None
            )
            if not structure_available:
                self._status.setText(
                    result.status_message
                    + " The project has no validated ORCA or optimized structure "
                    "to open yet."
                )
                return
            self._selected_snapshot = result
            self._selected_profile = self._current_profile()
            self.accept()

    @Slot(object)
    def _geometry_view_succeeded(self, result: object) -> None:
        self._trust_retry_pending = False
        self._trust_retry_used = False
        if not isinstance(result, ProjectGeometryViewResult):
            self.show_geometry_view_error(
                RuntimeError("Project geometry view returned invalid data")
            )
            return
        self._status.setText(
            f"Opened read-only {result.source_filename} for "
            f"{result.project_name}."
        )
        self.geometry_workspace_requested.emit(result)

    @Slot(object)
    def _task_restart_succeeded(self, result: object) -> None:
        self._trust_retry_pending = False
        self._trust_retry_used = False
        if not isinstance(result, ProjectTaskRestartResult):
            self._show_error(
                RuntimeError("Task restart returned an invalid result")
            )
            return
        attempt_key = (result.project_id, result.job_id)
        self._restart_suppressed_attempts.add(attempt_key)
        snapshot = self._current_snapshot()
        scheduler_name = (
            scheduler_display_name(
                snapshot.active_step.scheduler_kind or SchedulerKind.SLURM
            )
            if snapshot is not None
            and snapshot.project.project_id == result.project_id
            else "Scheduler"
        )
        if result.outcome is ProjectTaskCancellationOutcome.UNKNOWN:
            self._status.setText(
                f"Cancellation outcome unknown for {scheduler_name} Job "
                f"{result.job_id}. "
                "No restart draft was opened and cancellation was not retried. "
                "Use Refresh Status."
            )
            return
        assert result.draft is not None
        if result.outcome is ProjectTaskCancellationOutcome.REQUESTED:
            self._status.setText(
                f"Cancellation requested for {scheduler_name} Job {result.job_id}. "
                "The restart draft is editable, but submission remains locked "
                "until Refresh Status confirms a terminal state."
            )
        else:
            self._status.setText(
                f"{scheduler_name} Job {result.job_id} was already terminal. "
                "Refresh Status "
                "to durably reconcile it before submitting the restart draft."
            )
        self.restart_draft_requested.emit(result.draft)

    @Slot(object)
    def _task_restart_failed(self, error: object) -> None:
        if (
            isinstance(error, UnknownHostKey)
            and not self._trust_retry_used
            and self.isVisible()
        ):
            if self._confirm_unknown_host(error):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_error(trust_error)
                    return
                self._trust_retry_used = True
                self._trust_retry_pending = True
                self._status.setText(
                    "Host key trusted; retrying task preparation once."
                )
                return
            self._status.setText(
                "Task cancellation was cancelled; the unknown host key was not trusted."
            )
            return
        self._trust_retry_pending = False
        self._show_error(error)

    @Slot(object)
    def _task_restart_finished(self, worker: object) -> None:
        if not isinstance(worker, ProjectTaskRestartWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()
        if self._trust_retry_pending and self._pending_restart_selection is not None:
            self._trust_retry_pending = False
            snapshot, step_kind, confirmed = self._pending_restart_selection
            self._start_task_restart(
                snapshot,
                step_kind,
                profile_rebind_confirmed=confirmed,
            )

    @Slot(object)
    def _geometry_view_failed(self, error: object) -> None:
        if (
            isinstance(error, UnknownHostKey)
            and not self._trust_retry_used
            and self.isVisible()
        ):
            if self._confirm_unknown_host(error):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self.show_geometry_view_error(trust_error)
                    return
                self._trust_retry_used = True
                self._trust_retry_pending = True
                self._status.setText(
                    "Host key trusted; retrying the geometry view once."
                )
                return
            self._status.setText(
                "Geometry viewing was cancelled; the unknown host key was not "
                "trusted."
            )
            return
        self._trust_retry_pending = False
        self.show_geometry_view_error(error)

    @Slot(object)
    def _geometry_view_finished(self, worker: object) -> None:
        if not isinstance(worker, ProjectGeometryViewWorker):
            return
        self._workers.discard(worker)
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()
        if (
            self._trust_retry_pending
            and self._pending_geometry_selection is not None
        ):
            self._trust_retry_pending = False
            snapshot, step_kind, view_kind, confirmed = (
                self._pending_geometry_selection
            )
            self._start_geometry_view(
                snapshot,
                step_kind,
                view_kind,
                profile_rebind_confirmed=confirmed,
            )

    @Slot(object)
    def _operation_failed(self, error: object) -> None:
        if isinstance(error, StatusRefreshTimeout):
            self._trust_retry_pending = False
            self._open_after_refresh = False
            self._pending_operation = None
            self._status.setText(str(error))
            return
        if (
            isinstance(error, UnknownHostKey)
            and not self._trust_retry_used
            and self.isVisible()
        ):
            if self._confirm_unknown_host(error):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_error(trust_error)
                    return
                self._trust_retry_used = True
                self._trust_retry_pending = True
                self._status.setText(
                    "Host key trusted; retrying the remote operation once."
                )
                return
            self._status.setText(
                "Project recovery cancelled; the unknown host key was not trusted."
            )
            return
        self._trust_retry_pending = False
        self._show_error(error)

    @Slot()
    def _operation_stopped(self) -> None:
        self._trust_retry_pending = False
        self._open_after_refresh = False
        self._pending_operation = None
        self._status.setText(
            "Server refresh stopped. Already completed authoritative status "
            "updates were not rolled back."
        )

    @Slot(object)
    def _operation_finished(self, worker: object) -> None:
        if worker is not self._stoppable_refresh_worker or worker not in self._workers:
            return
        self._workers.discard(worker)
        if self._stoppable_refresh_worker is worker:
            self._stoppable_refresh_worker = None
        worker.deleteLater()
        if self._workers:
            return
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()
        if self._trust_retry_pending and self._pending_operation is not None:
            self._trust_retry_pending = False
            path, confirmed = self._pending_operation
            self._start_operation(
                path,
                profile_rebind_confirmed=confirmed,
            )

    def _confirm_unknown_host(self, error: UnknownHostKey) -> bool:
        info = error.info
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unknown SSH host key")
        box.setText("Trust this host key for project recovery?")
        box.setInformativeText(
            f"Host: {info.hostname}:{info.port}\n"
            f"Algorithm: {info.algorithm}\n"
            f"SHA256: {info.sha256_fingerprint}"
        )
        trust = box.addButton("Trust and Save", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is trust

    def _show_error(self, error: object) -> None:
        presentation = recovery_error_presentation(error)
        self._status.setText(
            f"{presentation.title}: {presentation.message}"
        )
        if self.isVisible():
            QMessageBox.critical(self, presentation.title, presentation.message)

    def _show_project_deletion_error(self, error: object) -> None:
        if isinstance(error, RemoteProjectDeletionOutcomeUnknown):
            presentation = RecoveryErrorPresentation(
                "Deletion outcome unknown",
                "Deletion outcome unknown. Reconnect and Refresh.",
            )
        elif isinstance(error, UnknownHostKey):
            presentation = RecoveryErrorPresentation(
                "Unknown SSH host key",
                "Trust this server through the existing connection workflow, then "
                "explicitly request permanent deletion again.",
            )
        elif isinstance(
            error,
            (
                RemoteProjectDeletionError,
                ProjectManagementError,
                LocalProjectIndexError,
            ),
        ):
            presentation = RecoveryErrorPresentation(
                "Project deletion failed",
                str(error),
            )
        else:
            presentation = recovery_error_presentation(error)
        self._status.setText(f"{presentation.title}: {presentation.message}")
        if self.isVisible():
            QMessageBox.critical(self, presentation.title, presentation.message)

    def _show_local_project_management_error(self, error: object) -> None:
        title = (
            "Project list update failed"
            if isinstance(error, LocalProjectIndexError)
            else "Project management failed"
        )
        message = str(error) if isinstance(error, Exception) else "Local update failed"
        self._status.setText(f"{title}: {message}")
        if self.isVisible():
            QMessageBox.critical(self, title, message)

    def _show_busy_progress(self, message: str) -> None:
        self._progress_text.setText(message)
        self._busy_area.show()

    def _hide_busy_progress(self) -> None:
        self._busy_area.hide()
        self._progress_text.clear()

    @Slot()
    def _server_changed(self) -> None:
        if self._busy:
            return
        self._in_recycle_bin = False
        self._list_heading.setText("Projects:")
        self._sort.show()
        self._view.show()
        self._recycle_bin.show()
        self._back_to_projects.hide()
        self._snapshots = ()
        self._density_tasks = ()
        self._density_problems = ()
        self._records = ()
        self._project_list.clear()
        self._status.setText(
            "Press Refresh to discover managed projects on the selected server."
        )
        self._update_controls()

    @Slot()
    def _selection_changed(self) -> None:
        if self._in_recycle_bin:
            item = self._project_list.currentItem()
            entry = (
                item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            )
            if isinstance(entry, RecycledProjectReference):
                self._status.setText(
                    f"{entry.display_name} is suppressed locally. Its server "
                    "directory has not been moved or deleted."
                )
            self._update_controls()
            return
        snapshot = self._current_snapshot()
        if snapshot is not None:
            self._status.setText(snapshot.status_message)
        elif (task := self._current_density_task()) is not None:
            self._status.setText(f"Job {task.job_id or 'receipt unknown'} — {task.message}\n{task.remote_path}")
        self._update_controls()

    def _current_density_task(self):
        if self._in_recycle_bin: return None
        item = self._project_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        return data if isinstance(data, DensityTask) else None

    @Slot(object)
    def _density_discovered(self, result):
        tasks, self._density_problems = result
        try:
            recycled_ids = self._recycled_ids_for_current_profile()
        except Exception as error:
            self._density_tasks = ()
            self._show_local_project_management_error(error)
            return
        self._density_tasks = tuple(
            task for task in tasks if task.task_id not in recycled_ids
        )
        active = {
            (task.task_id, task.job_id)
            for task in self._density_tasks
            if task.job_id is not None and task.state in {"QUEUED", "RUNNING"}
        }
        self._density_cancel_suppressed_attempts.intersection_update(active)

    def update_density_task(self, task):
        if str(self._current_profile().profile_id) != task.data["context"]["profile_id"]:
            return
        try:
            recycled_ids = self._recycled_ids_for_current_profile()
        except Exception as error:
            self._show_local_project_management_error(error)
            return
        if task.task_id in recycled_ids:
            self._density_tasks = tuple(
                item for item in self._density_tasks if item.task_id != task.task_id
            )
            self._render_snapshots()
            return
        self._density_cancel_suppressed_attempts = {
            key
            for key in self._density_cancel_suppressed_attempts
            if key[0] != task.task_id
            or (
                task.job_id is not None
                and key[1] == task.job_id
                and task.state in {"QUEUED", "RUNNING"}
            )
        }
        self._density_tasks = tuple(t for t in self._density_tasks if t.task_id != task.task_id) + (task,)
        self._render_snapshots()

    def _recycled_ids_for_current_profile(self) -> set[UUID]:
        service = self._project_management_service
        if service is None:
            return set()
        profile_id = self._current_profile().profile_id
        return {
            entry.project_id
            for entry in service.recycled_projects()
            if entry.server_profile_id == profile_id
        }

    def _show_density_task_menu(self, task, global_position):
        self._density_task_menu(task).exec(global_position)

    def _density_task_menu(self, task):
        menu = QMenu(self)
        eligible = (
            task.state in {"QUEUED", "RUNNING"}
            and task.job_id is not None
            and self._density_service is not None
            and (task.task_id, task.job_id)
            not in self._density_cancel_suppressed_attempts
        )
        action = menu.addAction("Cancel Density Job...")
        action.setObjectName("cancelDensityJob")
        action.setEnabled(eligible)
        if eligible:
            action.triggered.connect(
                lambda _checked=False: self._request_density_cancel(task)
            )
        return menu

    def _request_density_cancel(self, task):
        if (
            self._busy
            or self._density_service is None
            or task.job_id is None
            or task.state not in {"QUEUED", "RUNNING"}
            or (task.task_id, task.job_id)
            in self._density_cancel_suppressed_attempts
        ):
            return
        scheduler_name = _density_scheduler_name(task)
        answer = QMessageBox.question(
            self,
            f"Cancel {scheduler_name} density job",
            f"Cancel the exact shared {scheduler_name} job?\n\n"
            f"Task: {task.name}\n"
            f"Job ID: {task.job_id}\n\n"
            "Total, Subset 1 and Subset 2 run sequentially in this one job. "
            "Cancelling it stops the current component and prevents remaining "
            "components from starting.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
        except Exception as error:
            self._show_error(error)
            return
        if not accepted:
            self._status.setText("Density job cancellation was cancelled.")
            return
        worker = DensityWorker(
            lambda: self._density_service.cancel(
                profile, task.remote_path, password
            )
        )
        worker.signals.succeeded.connect(self._density_cancel_succeeded)
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(self._density_finished)
        self._workers.add(worker)
        self._busy = True
        self._show_busy_progress(
            f"Revalidating {scheduler_name} Job {task.job_id} before cancellation..."
        )
        self._update_controls()
        self._thread_pool.start(worker)

    @Slot(object)
    def _density_cancel_succeeded(self, result):
        if not isinstance(result, DensityCancellationResult):
            self._show_error(
                RuntimeError("Density cancellation returned invalid data")
            )
            return
        current = self._current_density_task()
        if current is None or current.task_id != result.task.task_id:
            self._show_error(RuntimeError("cancelled density selection changed"))
            return
        attempt_key = (result.task.task_id, result.job_id)
        if result.outcome is DensityCancellationOutcome.ALREADY_TERMINAL:
            self._density_cancel_suppressed_attempts.discard(attempt_key)
            self.update_density_task(result.task)
            self._status.setText(result.task.message)
            return
        self._density_cancel_suppressed_attempts.add(attempt_key)
        self.update_density_task(result.task)
        scheduler_name = _density_scheduler_name(result.task)
        if result.outcome is DensityCancellationOutcome.REQUESTED:
            self._status.setText(
                f"Cancellation requested for {scheduler_name} Job {result.job_id}. "
                "Use Refresh Status to retrieve its terminal state."
            )
        else:
            self._status.setText(
                f"Cancellation outcome for {scheduler_name} Job {result.job_id} "
                "is unknown "
                "after dispatch. Do not send another cancellation; use Refresh "
                "Status."
            )

    def _refresh_density_task(self):
        if self._busy or self._density_service is None: return
        task = self._current_density_task()
        profile = self._current_profile()
        try:
            accepted, password = self._password_for(profile)
        except Exception as error:
            self._show_error(error)
            return
        if not accepted: return
        service = self._density_service
        worker = StatusRefreshSession(
            lambda stop_token, _progress: service.refresh(
                profile, task.remote_path, password, stop_token=stop_token,
            ),
            self,
        )
        worker.succeeded.connect(self.update_density_task)
        worker.failed.connect(self._operation_failed)
        worker.stopped.connect(self._operation_stopped)
        worker.finished.connect(self._operation_finished)
        self._workers.add(worker)
        self._stoppable_refresh_worker = worker
        self._busy = True
        self._show_busy_progress("Refreshing density task…")
        self._update_controls()
        worker.start()

    @Slot(object)
    def _density_finished(self, worker):
        self._workers.discard(worker)
        self._busy = False
        self._hide_busy_progress()
        self._update_controls()

    def _current_profile(self) -> ServerProfile:
        profile = self._server.currentData()
        if not isinstance(profile, ServerProfile):
            raise RuntimeError("selected server profile is unavailable")
        return profile

    def _current_snapshot(self) -> ProjectRecoverySnapshot | None:
        if self._in_recycle_bin:
            return None
        item = self._project_list.currentItem()
        if item is None:
            return None
        record = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(record, ProjectPresentationRecord):
            return record.snapshot
        return record if isinstance(record, ProjectRecoverySnapshot) else None

    def _render_snapshots(self) -> None:
        if self._in_recycle_bin:
            return
        selected_id = self._selected_project_id()
        profile = self._current_profile()
        records = tuple(
            project_presentation_record(
                snapshot,
                server_profile_id=profile.profile_id,
            )
            for snapshot in self._snapshots
        )
        self._records = sort_project_presentations(
            records,
            self._sort_criterion,
            reversed_order=self._sort_reversed,
        )
        self._project_list.clear()
        for record in self._records:
            item = QListWidgetItem(project_list_text(record.snapshot))
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setData(PROJECT_PRESENTATION_ROLE, record)
            item.setData(PROJECT_UUID_ROLE, str(record.project_id))
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                record.remote_directory_basename,
            )
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole,
                "; ".join(
                    indicator.tooltip for indicator in record.indicators
                ),
            )
            self._project_list.addItem(item)
        for task in sorted(self._density_tasks, key=lambda t: t.data["created_at"], reverse=True):
            item = QListWidgetItem(f"{task.name}\n电子密度差计算 — {task.state} — Job {task.job_id or '—'}")
            item.setData(Qt.ItemDataRole.UserRole, task)
            item.setData(PROJECT_PRESENTATION_ROLE, task)
            item.setData(PROJECT_UUID_ROLE, str(task.task_id))
            item.setToolTip(task.message)
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole,
                "; ".join(
                    f"{name}: {state}"
                    for name, state in task.component_states.items()
                ),
            )
            self._project_list.addItem(item)
        self._configure_project_view()
        if not self._select_project_id(selected_id) and self._project_list.count():
            self._project_list.setCurrentRow(0)
        self._update_controls()

    def _render_recycled(
        self,
        entries: tuple[RecycledProjectReference, ...],
    ) -> None:
        self._project_list.clear()
        self._project_list.setItemDelegate(self._recycle_delegate)
        self._project_list.setViewMode(QListView.ViewMode.ListMode)
        self._project_list.setGridSize(QSize())
        self._project_list.setWrapping(False)
        self._project_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        profile_names = {
            profile.profile_id: profile.name
            for index in range(self._server.count())
            if isinstance((profile := self._server.itemData(index)), ServerProfile)
        }
        ordered = sorted(
            entries,
            key=lambda entry: (
                -entry.recycled_at.timestamp(),
                entry.display_name.casefold(),
                str(entry.project_id),
            ),
        )
        for entry in ordered:
            profile_name = profile_names.get(entry.server_profile_id, "Saved server")
            submitted = format_project_submission_timestamp(entry.submitted_at)
            recycled = format_project_submission_timestamp(entry.recycled_at)
            item = QListWidgetItem(
                f"{entry.display_name}\n"
                f"Server: {profile_name} — Submitted: {submitted} — "
                f"Recycled: {recycled}"
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._project_list.addItem(item)
        if self._project_list.count():
            self._project_list.setCurrentRow(0)
        self._update_controls()

    def _selected_project_id(self) -> UUID | None:
        if (task := self._current_density_task()) is not None:
            return task.task_id
        snapshot = self._current_snapshot()
        return None if snapshot is None else snapshot.project.project_id

    def _select_project_id(self, project_id: UUID | None) -> bool:
        if project_id is None:
            return False
        expected = str(project_id)
        for row in range(self._project_list.count()):
            item = self._project_list.item(row)
            if item.data(PROJECT_UUID_ROLE) == expected:
                self._project_list.setCurrentRow(row)
                return True
        return False

    def _configure_project_view(self) -> None:
        self._project_list.setItemDelegate(self._project_delegate)
        if self._view_mode is ProjectViewMode.TILES:
            self._project_list.setViewMode(QListView.ViewMode.IconMode)
            self._project_list.setResizeMode(QListView.ResizeMode.Adjust)
            self._project_list.setMovement(QListView.Movement.Static)
            self._project_list.setWrapping(True)
            self._project_list.setGridSize(TILE_GRID_SIZE)
            self._project_list.setSpacing(2)
            self._project_list.setUniformItemSizes(True)
            self._project_list.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            return
        self._project_list.setViewMode(QListView.ViewMode.ListMode)
        self._project_list.setResizeMode(QListView.ResizeMode.Adjust)
        self._project_list.setMovement(QListView.Movement.Static)
        self._project_list.setWrapping(False)
        self._project_list.setGridSize(QSize())
        self._project_list.setSpacing(0)
        self._project_list.setUniformItemSizes(
            self._view_mode is ProjectViewMode.COMPACT
        )
        self._project_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

    def _replace_snapshot(self, snapshot: ProjectRecoverySnapshot) -> None:
        self._snapshots = tuple(
            snapshot
            if item.project.project_id == snapshot.project.project_id
            else item
            for item in self._snapshots
        )
        current_id = snapshot.project.project_id
        self._render_snapshots()
        self._select_project_id(current_id)

    def _clear_kill_suppression_for_project(self, project_id: UUID) -> None:
        self._kill_suppressed_attempts = {
            item for item in self._kill_suppressed_attempts if item[0] != project_id
        }

    def _update_controls(self) -> None:
        snapshot = self._current_snapshot()
        density_task = self._current_density_task()
        deletion_target = density_task or snapshot
        has_selection = deletion_target is not None
        recycle_selection = (
            self._in_recycle_bin
            and self._project_list.currentItem() is not None
            and isinstance(
                self._project_list.currentItem().data(Qt.ItemDataRole.UserRole),
                RecycledProjectReference,
            )
        )
        self._server.setEnabled(not self._busy and not self._in_recycle_bin)
        refresh_running = (
            self._stoppable_refresh_worker is not None
            and self._stoppable_refresh_worker in self._workers
        )
        if refresh_running:
            self._refresh.setText("Stop")
            self._refresh.setEnabled(True)
        else:
            self._refresh.setText("Refresh")
            self._refresh.setEnabled(not self._busy and not self._in_recycle_bin)
        self._project_list.setEnabled(not self._busy)
        self._sort.setEnabled(not self._in_recycle_bin)
        self._view.setEnabled(not self._in_recycle_bin)
        self._recycle_bin.setEnabled(
            not self._busy
            and not self._in_recycle_bin
            and self._project_management_service is not None
        )
        self._back_to_projects.setEnabled(not self._busy)
        for widget in (
            self._open,
            self._refresh_status,
            self._delete_project,
            self._cancel_orca,
            self._resubmit_orca,
            self._view_orca_wbl,
        ):
            widget.setVisible(not self._in_recycle_bin)
        fhi_actions_visible = (
            not self._in_recycle_bin
            and snapshot is not None
            and snapshot.project.workflow_kind is CalculationWorkflowKind.FHI_AIMS_AITRANSS
        )
        for widget in (self._retry_step3, self._retry_step4, self._view_transmission):
            widget.setVisible(fhi_actions_visible)
        self._restore_project.setVisible(self._in_recycle_bin)
        self._open.setEnabled(not self._busy and has_selection)
        self._refresh_status.setEnabled(not self._busy and has_selection)
        if density_task is not None:
            self._open.setEnabled(not self._busy)
            self._refresh_status.setEnabled(not self._busy and self._density_service is not None)
        kill_eligible = (
            snapshot is not None
            and snapshot.can_kill_step3_oom
            and snapshot.active_step.job_id is not None
            and (
                snapshot.project.project_id,
                snapshot.active_step.job_id,
            )
            not in self._kill_suppressed_attempts
        )
        self._kill_step3.setVisible(not self._in_recycle_bin and kill_eligible)
        self._kill_step3.setEnabled(not self._busy and kill_eligible)
        orca_cancel_eligible = (
            snapshot is not None
            and snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA
            and snapshot.active_step.job_id is not None
            and snapshot.active_step.state
            in {
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
                ProjectStepState.UNKNOWN,
            }
            and self._orca_recovery_service is not None
        )
        self._cancel_orca.setVisible(
            not self._in_recycle_bin
            and snapshot is not None
            and snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA
        )
        self._cancel_orca.setEnabled(not self._busy and orca_cancel_eligible)
        orca_selected = (
            not self._in_recycle_bin
            and snapshot is not None
            and snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA
        )
        self._resubmit_orca.setVisible(orca_selected)
        self._resubmit_orca.setEnabled(
            not self._busy and _orca_resubmit_eligible(snapshot)
        )
        self._view_orca_wbl.setVisible(orca_selected)
        self._view_orca_wbl.setEnabled(
            not self._busy
            and snapshot is not None
            and snapshot.can_view_orca_wbl
        )
        self._retry_step3.setEnabled(
            not self._busy
            and snapshot is not None
            and snapshot.can_retry_step3
            and self._transport_submission_service is not None
        )
        self._retry_step4.setEnabled(
            not self._busy
            and snapshot is not None
            and snapshot.can_retry_step4_explicit
            and self._transport_submission_service is not None
        )
        self._view_transmission.setEnabled(
            not self._busy
            and snapshot is not None
            and snapshot.can_view_transmission
        )
        self._delete_project.setEnabled(
            not self._busy
            and has_selection
            and self._project_management_service is not None
            and deletion_target is not None
            and self._project_delete_block_reason(deletion_target) is None
        )
        self._delete_project.setText(
            "Delete Task..." if density_task is not None else "Delete Project..."
        )
        self._restore_project.setEnabled(not self._busy and recycle_selection)
        self._close.setEnabled(True)
        self._close.setText(
            "Close (operation continues)" if self._busy else "Close"
        )


class Step3RetryDialog(QDialog):
    """Edit retry resources while keeping geometry/control science locked."""

    def __init__(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
        preset: SlurmExecutionPreset,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not snapshot.can_retry_step3:
            raise ValueError("project is not eligible for Step-3 retry")
        self._base_preset = preset
        self._selected_preset: SlurmExecutionPreset | None = None
        scheduler_kind = preset.scheduler_kind
        scheduler_name = scheduler_display_name(scheduler_kind)
        self.setWindowTitle(f"Resubmit Step 3 — {scheduler_name}")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "This creates a new retry script/output and submits the unchanged "
            "geometry.in and control.in calculation once.",
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        form.addRow("Project:", QLabel(snapshot.project.remote_directory_name, self))
        form.addRow("Previous Job ID:", QLabel(snapshot.active_step.job_id or "—", self))
        retry_reason = (
            slurm_failure_reason(snapshot.active_step.last_error)
            or snapshot.active_step.last_error
            or "unknown"
        )
        form.addRow("Reason:", QLabel(retry_reason, self))
        science = QLabel("Locked — existing geometry.in and control.in SHA256", self)
        science.setObjectName("step3RetryScienceLocked")
        form.addRow("Scientific inputs:", science)
        retry_email = QLabel(email_notification_summary(profile), self)
        retry_email.setObjectName("step3RetryEmailSummary")
        form.addRow("Email notification:", retry_email)
        self._nodes = _positive_spinbox(preset.nodes, self)
        self._ntasks = _positive_spinbox(preset.ntasks, self)
        self._cpus = _positive_spinbox(
            preset.cpus_per_task if scheduler_kind is SchedulerKind.SLURM else 1,
            self,
        )
        self._runtime = QDoubleSpinBox(self)
        self._runtime.setObjectName("step3RetryRuntimeHours")
        self._runtime.setRange(0.1, 1_000_000.0)
        self._runtime.setDecimals(1)
        self._runtime.setSingleStep(0.5)
        self._runtime.setSuffix(" hours")
        self._runtime.setValue(runtime_hours_from_minutes(preset.runtime_minutes))
        self._memory = _positive_spinbox(preset.memory_gb, self)
        self._memory.setObjectName("step3RetryMemoryGb")
        self._memory.setSuffix(" GB")
        self._omp = _positive_spinbox(
            preset.omp_num_threads if scheduler_kind is SchedulerKind.SLURM else 1,
            self,
        )
        form.addRow(
            "Nodes:" if scheduler_kind is SchedulerKind.SLURM else "Execution hosts:",
            self._nodes,
        )
        form.addRow(
            "MPI tasks:"
            if scheduler_kind is SchedulerKind.SLURM
            else "MPI job slots / ranks:",
            self._ntasks,
        )
        if scheduler_kind is SchedulerKind.SLURM:
            form.addRow("CPUs per task:", self._cpus)
        form.addRow("Maximum runtime:", self._runtime)
        form.addRow(
            "Memory limit per node:"
            if scheduler_kind is SchedulerKind.SLURM
            else "Memory reservation (LSF rusage):",
            self._memory,
        )
        if scheduler_kind is SchedulerKind.SLURM:
            form.addRow("OpenMP threads:", self._omp)
        else:
            execution_model = QLabel(
                "Pure MPI: one CPU and one OpenMP thread per rank. MPI job "
                "slots must divide evenly across the requested execution hosts.",
                self,
            )
            execution_model.setObjectName("step3RetryLsfExecutionModel")
            execution_model.setWordWrap(True)
            form.addRow("Execution model:", execution_model)
        layout.addLayout(form)
        buttons = QDialogButtonBox(self)
        submit = buttons.addButton(
            "Submit REAL Step-3 Retry",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        submit.setObjectName("submitStep3Retry")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if snapshot.active_step.last_error in {
            "OUT_OF_MEMORY",
            SLURM_TASK_OUT_OF_MEMORY,
        }:
            self._memory.setFocus(Qt.FocusReason.OtherFocusReason)
            self._memory.lineEdit().selectAll()

    def selected_preset(self) -> SlurmExecutionPreset:
        if self._selected_preset is None:
            raise RuntimeError("Step-3 retry resources were not accepted")
        return self._selected_preset

    @Slot()
    def _accept_settings(self) -> None:
        is_slurm = self._base_preset.scheduler_kind is SchedulerKind.SLURM
        try:
            selected = replace(
                self._base_preset,
                nodes=self._nodes.value(),
                ntasks=self._ntasks.value(),
                cpus_per_task=self._cpus.value() if is_slurm else 1,
                runtime_minutes=runtime_minutes_from_hours(self._runtime.value()),
                memory_gb=self._memory.value(),
                omp_num_threads=self._omp.value() if is_slurm else 1,
                unset_slurm_export_env=(
                    self._base_preset.unset_slurm_export_env if is_slurm else False
                ),
            )
        except (TypeError, ValueError) as error:
            QMessageBox.critical(self, "Invalid retry resources", str(error))
            return
        self._selected_preset = selected
        self.accept()


class Step4ExplicitRetryDialog(QDialog):
    """Read-only explanation and explicit confirmation for the reviewed fix."""

    def __init__(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not snapshot.can_retry_step4_explicit:
            raise ValueError("project is not eligible for explicit Step-4 retry")
        plan = snapshot.step4_self_energy_plan
        settings = snapshot.step4_tcontrol_settings
        assert plan is not None and settings is not None
        self.setWindowTitle("Retry Step 4 with Explicit Self-Energy")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "AITRANSS's automatic electrode-interface detection selected atoms "
            "from the opposite electrode. The retry can use the known electrode "
            "construction provenance to define the reservoir interface explicitly "
            "without changing the optimized geometry.",
            self,
        )
        explanation.setObjectName("step4ExplicitRetryExplanation")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        form.addRow("Project:", QLabel(snapshot.project.remote_directory_name, self))
        form.addRow("Previous Job ID:", QLabel(snapshot.active_step.job_id or "—", self))
        form.addRow("Left interface atoms:", QLabel(str(plan.left.selected_count), self))
        form.addRow("Right interface atoms:", QLabel(str(plan.right.selected_count), self))
        form.addRow("nlayers:", QLabel(str(settings.nlayers), self))
        form.addRow(
            "s1i/s2i/s3i:",
            QLabel(f"{settings.s1i} / {settings.s2i} / {settings.s3i}", self),
        )
        isolation = QLabel("Geometry unchanged — Step 3 results reused", self)
        isolation.setObjectName("step4ExplicitRetryIsolation")
        form.addRow("Scientific inputs:", isolation)
        notification = QLabel(email_notification_summary(profile), self)
        notification.setObjectName("step4ExplicitRetryEmailSummary")
        form.addRow("Email notification:", notification)
        layout.addLayout(form)
        fallback = QLabel(
            "Alternatively, changing electrode geometry requires returning to "
            "electrode construction and rerunning Step 3.",
            self,
        )
        fallback.setWordWrap(True)
        layout.addWidget(fallback)
        buttons = QDialogButtonBox(self)
        submit = buttons.addButton(
            "Submit REAL Step-4 Retry",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        submit.setObjectName("submitStep4ExplicitRetry")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _positive_spinbox(value: int, parent: QWidget) -> QSpinBox:
    widget = QSpinBox(parent)
    widget.setRange(1, 2_000_000_000)
    widget.setValue(value)
    return widget


class Step2ContinuationConfirmationDialog(QDialog):
    """Confirm a real Step-2 submission inside an existing project."""

    def __init__(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
        settings: AimsOptimizationSettings,
        *,
        temporary_password_required: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not snapshot.can_continue_step2:
            raise ValueError("project is not eligible for Step-2 continuation")
        if profile.execution_preset is None:
            raise ValueError("selected server has no Cluster Execution Settings")
        scheduler_name = scheduler_display_name(
            profile.execution_preset.scheduler_kind
        )
        self._temporary_password_required = temporary_password_required
        self.setWindowTitle(f"Confirm REAL Step-2 {scheduler_name} Submission")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "This action creates molecule_Au in the existing project and sends "
            f"one real {scheduler_name} submission request.",
            self,
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        form.addRow("Project:", QLabel(snapshot.project.remote_directory_name))
        form.addRow("Current:", QLabel("Step 1 — SUCCEEDED"))
        form.addRow(
            "Next:",
            QLabel("Step 2 — Molecule + contact-Au optimization"),
        )
        form.addRow(
            "Remote:",
            QLabel(
                str(
                    PurePosixPath(snapshot.project.remote_project_path)
                    / "molecule_Au"
                )
            ),
        )
        form.addRow("Server:", QLabel(profile.name))
        form.addRow("Resources:", QLabel(resource_summary(profile.execution_preset)))
        step2_email = QLabel(email_notification_summary(profile), self)
        step2_email.setObjectName("step2EmailSummary")
        form.addRow("Email notification:", step2_email)
        form.addRow("Optimization:", QLabel(optimization_summary(settings)))
        self._password: QLineEdit | None = None
        if temporary_password_required:
            self._password = QLineEdit(self)
            self._password.setObjectName("step2TemporaryPassword")
            self._password.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Password (memory only):", self._password)
        layout.addLayout(form)
        buttons = QDialogButtonBox(self)
        self._submit = buttons.addButton(
            "Submit REAL Step 2",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def take_temporary_password(self) -> str | None:
        if self._password is None:
            return None
        password = self._password.text()
        self._password.clear()
        return password or None

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
                "Enter the password for this Step-2 submission.",
            )
            return
        self._submit.setEnabled(False)
        self.accept()


def _density_scheduler_name(task: DensityTask) -> str:
    """Read scheduler provenance from the current density attempt."""

    return scheduler_display_name(
        SchedulerKind(
            task.attempt.get("scheduler_kind", SchedulerKind.SLURM.value)
        )
    )


def _orca_frequency_eligible(
    snapshot: ProjectRecoverySnapshot | None,
) -> bool:
    if (
        snapshot is None
        or snapshot.project.workflow_kind is not CalculationWorkflowKind.ORCA
        or snapshot.optimized_structure is None
    ):
        return False
    optimization = snapshot.project.steps[0]
    return (
        optimization.kind is ProjectStepKind.ORCA_OPTIMIZATION
        and optimization.state is ProjectStepState.SUCCEEDED
        and optimization.orca_optimization_result is not None
        and optimization.orca_optimization_result.succeeded
        and not any(
            step.kind is ProjectStepKind.ORCA_FREQUENCY
            for step in snapshot.project.steps
        )
    )


def _orca_wbl_eligible(
    snapshot: ProjectRecoverySnapshot | None,
) -> bool:
    if (
        snapshot is None
        or snapshot.project.workflow_kind is not CalculationWorkflowKind.ORCA
        or snapshot.optimized_structure is None
    ):
        return False
    optimization = snapshot.project.steps[0]
    evidence = optimization.orca_optimization_result
    return (
        optimization.kind is ProjectStepKind.ORCA_OPTIMIZATION
        and optimization.state is ProjectStepState.SUCCEEDED
        and evidence is not None
        and evidence.succeeded
        and evidence.wbl_input_ready
        and evidence.gbw_sha256 is not None
        and not any(
            step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
            for step in snapshot.project.steps
        )
    )


def _orca_resubmit_eligible(
    snapshot: ProjectRecoverySnapshot | None,
) -> bool:
    return bool(
        snapshot is not None
        and snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA
        and snapshot.project.steps[0].kind is ProjectStepKind.ORCA_OPTIMIZATION
        and snapshot.project.steps[0].orca_optimization_settings is not None
        and (snapshot.optimized_structure is not None or snapshot.submitted_structure is not None)
    )


def project_list_text(snapshot: ProjectRecoverySnapshot) -> str:
    if snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA:
        return _orca_project_list_text(snapshot)
    step_number = {
        ProjectStepKind.MOLECULE_OPT: 1,
        ProjectStepKind.MOLECULE_AU_OPT: 2,
        ProjectStepKind.TRANSPORT_CONVERGENCE: 3,
        ProjectStepKind.TRANSMISSION: 4,
    }[snapshot.active_step_kind]
    job = (
        f"\nJob {snapshot.active_step.job_id}"
        if snapshot.active_step.job_id is not None
        else ""
    )
    reason = slurm_failure_reason(
        snapshot.active_step.last_error
    ) or aitranss_failure_reason(snapshot.active_step.last_error)
    reason_line = f"\nReason: {reason}" if reason is not None else ""
    scheduler_line = ""
    if (
        reason is not None
        and snapshot.active_step.scheduler_state is not None
        and snapshot.active_step.state is ProjectStepState.FAILED
    ):
        scheduler_kind = (
            snapshot.active_step.scheduler_kind or SchedulerKind.SLURM
        )
        scheduler_line = (
            f"\n{scheduler_display_name(scheduler_kind)}: "
            f"{snapshot.active_step.scheduler_state}"
        )
    active_suffix = (
        " — OOM detected"
        if snapshot.runtime_evidence
        is StepRuntimeEvidence.TASK_OOM_DETECTED
        else ""
    )
    if snapshot.project.starting_step is ProjectStepKind.MOLECULE_AU_OPT:
        start_line = "\nStep 1 — SKIPPED (imported contact-Au geometry)"
    elif snapshot.project.starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
        start_line = (
            "\nSteps 1–2 — SKIPPED (imported pre-optimized molecule–Au; "
            "electrodes recorded)"
        )
    else:
        start_line = ""
    return (
        f"{snapshot.project.remote_directory_name}\n"
        f"Step {step_number} — {snapshot.active_step.state.value}{active_suffix}"
        f"{job}{reason_line}{scheduler_line}{start_line}"
    )


def _orca_project_list_text(snapshot: ProjectRecoverySnapshot) -> str:
    step = snapshot.active_step
    stage = {
        ProjectStepKind.ORCA_OPTIMIZATION: "ORCA optimization",
        ProjectStepKind.ORCA_WBL_TRANSMISSION: "ORCA WBL transmission",
        ProjectStepKind.ORCA_FREQUENCY: "ORCA frequency",
    }[step.kind]
    lines = [
        snapshot.project.remote_directory_name,
        f"{stage} — {step.state.value}",
    ]
    if step.job_id is not None:
        scheduler = scheduler_display_name(step.scheduler_kind or SchedulerKind.SLURM)
        lines.append(f"{scheduler} Job {step.job_id}: {step.scheduler_state or 'unresolved'}")
    if step.kind is ProjectStepKind.ORCA_OPTIMIZATION:
        evidence = step.orca_optimization_result
        if evidence is not None:
            lines.append(
                "ORCA: "
                + ("normal termination" if evidence.normal_termination else "normal termination unverified")
            )
            lines.append(
                "Optimization: "
                + ("converged" if evidence.optimization_converged else "convergence unverified")
            )
            lines.append(
                "Final geometry: "
                + ("verified" if evidence.final_xyz_valid else "unverified")
            )
            lines.append(
                "Future WBL input: "
                + ("available" if evidence.wbl_input_ready else "not ready")
            )
    elif step.kind is ProjectStepKind.ORCA_FREQUENCY:
        evidence = step.orca_frequency_result
        if evidence is not None:
            lines.append(f"Frequency: {evidence.completion.value}")
            lines.append(
                "Imaginary modes: "
                + evidence.imaginary_classification.value.replace("_", " ").lower()
            )
    else:
        evidence = step.orca_wbl_result
        if evidence is not None:
            lines.append(f"Model: {evidence.model_classification}")
            lines.append(
                "T_total(E_F): " + format(evidence.t_total_at_fermi, ".6g")
            )
    if step.last_error:
        lines.append(f"Reason: {step.last_error}")
    return "\n".join(lines)


def recovery_error_presentation(error: object) -> RecoveryErrorPresentation:
    """Keep connection, scheduler, manifest, and scientific failures distinct."""

    message = str(error) if isinstance(error, Exception) else "Recovery failed"
    title = "Project recovery failed"
    if isinstance(error, StatusRefreshTimeout):
        title = "Status refresh timed out"
    elif isinstance(error, AuthenticationError):
        title = "Authentication failed"
    elif isinstance(error, PasswordRequiredError):
        title = "Password required"
    elif isinstance(error, HostKeyMismatch):
        title = "SSH host key mismatch"
    elif isinstance(error, (ConnectionTestError, RemoteExecutorError)):
        title = "Connection failed"
    elif isinstance(error, SlurmDiscoveryError):
        title = "Scheduler not detected"
    elif isinstance(error, SlurmConfigurationError):
        title = "Scheduler configuration invalid"
    elif isinstance(error, SlurmStatusError):
        title = "Scheduler status unavailable"
    elif isinstance(error, SlurmCancellationError):
        title = "Job cancellation failed"
    elif isinstance(error, DensityCancellationError):
        title = "Job cancellation unavailable"
    elif isinstance(error, Step3OomCancellationError):
        title = "Job cancellation unavailable"
    elif isinstance(error, ProjectTaskRestartError):
        title = "Task restart unavailable"
    elif isinstance(error, ProjectProfileRebindRequired):
        title = "Server profile confirmation required"
    elif isinstance(error, ProjectRecoveryError):
        title = "Project recovery failed"
    return RecoveryErrorPresentation(title, message)
