"""Server connection management and asynchronous read-only testing."""

from dataclasses import replace
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from moltage.app.connection_service import (
    AuthenticationError,
    ConnectionTestError,
    ConnectionTestSuccess,
    PasswordRequiredError,
    ServerConnectionService,
)
from moltage.app.paths import known_hosts_path, server_profiles_path
from moltage.app.server_profiles import (
    ServerProfileCollection,
    ServerProfileRepository,
    ServerProfileService,
)
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    ServerProfile,
    SlurmExecutionPreset,
)
from moltage.gui.cluster_execution_dialog import (
    ClusterExecutionSettingsDialog,
)
from moltage.gui.remote_directory_dialog import (
    RemoteDirectoryDialog,
    RemoteDirectoryWorker,
    normalize_remote_directory,
)
from moltage.remote.executor import RemoteExecutorError
from moltage.remote.known_hosts import (
    HostKeyMismatch,
    HostKeyInfo,
    KnownHostStore,
    UnknownHostKey,
)
from moltage.remote.paramiko_executor import ParamikoRemoteExecutor
from moltage.remote.secrets import WindowsCredentialSecretStore


class _ConnectionSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class _ConnectionWorker(QRunnable):

    def __init__(
        self,
        connection_service: ServerConnectionService,
        profile: ServerProfile,
        password: str | None,
    ) -> None:
        super().__init__()
        self.signals = _ConnectionSignals()
        self._connection_service = connection_service
        self._profile = profile
        self._password = password
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._connection_service.test_connection(
                self._profile,
                self._password,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._password = None
            self.signals.finished.emit(self)


_RemoteDirectoryWorker = RemoteDirectoryWorker


class ServerProfilesDialog(QDialog):
    """Manage connection fields and test one remote workspace safely."""

    def __init__(
        self,
        profile_service: ServerProfileService,
        connection_service: ServerConnectionService,
        known_hosts: KnownHostStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_service = profile_service
        self._connection_service = connection_service
        self._known_hosts = known_hosts
        self._editing_profile_id = uuid4()
        self._editing_execution_preset: SlurmExecutionPreset | None = None
        self._editing_aitranss_runtime: AitranssRuntimeConfiguration | None = None
        self._editing_runtime_hints = None
        self._editing_email_notification_enabled = False
        self._editing_email_notification_recipient: str | None = None
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._workers: set[_ConnectionWorker] = set()
        self._retry_profile: ServerProfile | None = None
        self._retry_password: str | None = None
        self._trust_retry_used = False
        self._pending_trusted_retry = False
        self._remote_directory_dialog: RemoteDirectoryDialog | None = None
        self._remote_directory_profile: ServerProfile | None = None
        self._remote_directory_workers: set[_RemoteDirectoryWorker] = set()
        self._remote_directory_requested_path: str | None = None
        self._remote_directory_password: str | None = None
        self._remote_directory_retry_pending = False
        self._remote_directory_trust_retry_used = False
        self._remote_directory_password_retry_used = False

        self.setWindowTitle("Server Connections")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)

        selection = QFormLayout()
        self._profile_selector = QComboBox(self)
        self._profile_selector.setObjectName("serverProfileSelector")
        self._profile_selector.currentIndexChanged.connect(
            self._profile_selection_changed
        )
        selection.addRow("Server profile:", self._profile_selector)
        layout.addLayout(selection)

        profile_group = QGroupBox("Connection", self)
        profile_form = QFormLayout(profile_group)
        self._name = QLineEdit(profile_group)
        self._host = QLineEdit(profile_group)
        self._port = QLineEdit(profile_group)
        self._port.setText("22")
        self._username = QLineEdit(profile_group)
        self._password = QLineEdit(profile_group)
        self._password.setObjectName("serverPassword")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        password_container = QWidget(profile_group)
        password_layout = QHBoxLayout(password_container)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.addWidget(self._password)
        self._password_visibility_button = QToolButton(password_container)
        self._password_visibility_button.setObjectName("passwordVisibilityButton")
        self._password_visibility_button.setText("👁")
        self._password_visibility_button.setCheckable(True)
        self._password_visibility_button.setToolTip("Show password")
        self._password_visibility_button.setAccessibleName("Show password")
        self._password_visibility_button.toggled.connect(
            self._set_password_visible
        )
        password_layout.addWidget(self._password_visibility_button)
        self._save_password = QCheckBox("Save password securely", profile_group)
        self._auto_connect = QCheckBox("Auto connect", profile_group)
        self._remote_root = QLineEdit(profile_group)
        workspace_container = QWidget(profile_group)
        workspace_layout = QVBoxLayout(workspace_container)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_path_container = QWidget(workspace_container)
        workspace_path_layout = QHBoxLayout(workspace_path_container)
        workspace_path_layout.setContentsMargins(0, 0, 0, 0)
        workspace_path_layout.addWidget(self._remote_root, 1)
        self._browse_workspace_button = QToolButton(workspace_path_container)
        self._browse_workspace_button.setObjectName("browseRemoteProjectWorkspace")
        self._browse_workspace_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self._browse_workspace_button.setToolTip(
            "Choose an existing project workspace directory on this server"
        )
        self._browse_workspace_button.setAccessibleName(
            "Browse remote project workspace"
        )
        self._browse_workspace_button.clicked.connect(
            self._browse_remote_workspace
        )
        workspace_path_layout.addWidget(self._browse_workspace_button)
        workspace_layout.addWidget(workspace_path_container)
        self._workspace_helper = QLabel(
            "All calculation project folders will be created under this "
            "working directory.",
            workspace_container,
        )
        self._workspace_helper.setWordWrap(True)
        workspace_layout.addWidget(self._workspace_helper)
        profile_form.addRow("Profile name:", self._name)
        profile_form.addRow("Host:", self._host)
        profile_form.addRow("Port:", self._port)
        profile_form.addRow("Username:", self._username)
        profile_form.addRow("Password:", password_container)
        profile_form.addRow("", self._save_password)
        profile_form.addRow("", self._auto_connect)
        profile_form.addRow("Remote project workspace:", workspace_container)
        layout.addWidget(profile_group)

        self._status = QLabel("No network connection is opened on dialog launch.", self)
        self._status.setWordWrap(True)
        self._status.setObjectName("serverProfileStatus")
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        self._test_button = QPushButton("Test Connection", self)
        self._cluster_button = QPushButton("Cluster Settings...", self)
        self._new_button = QPushButton("New", self)
        self._save_button = QPushButton("Save", self)
        self._save_as_button = QPushButton("Save As", self)
        self._delete_button = QPushButton("Delete", self)
        self._close_button = QPushButton("Close", self)
        self._test_button.clicked.connect(self._test_connection)
        self._cluster_button.clicked.connect(self._open_cluster_settings)
        self._new_button.clicked.connect(self._new_profile)
        self._save_button.clicked.connect(self._save_profile)
        self._save_as_button.clicked.connect(self._save_as_profile)
        self._delete_button.clicked.connect(self._delete_profile)
        self._close_button.clicked.connect(self.accept)
        for button in (
            self._test_button,
            self._cluster_button,
            self._new_button,
            self._save_button,
            self._save_as_button,
            self._delete_button,
            self._close_button,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self._refresh_profiles(self._profile_service.repository.load())

    def _refresh_profiles(
        self,
        collection: ServerProfileCollection,
        selected_profile_id: UUID | None = None,
    ) -> None:
        target = selected_profile_id or collection.last_selected_profile_id
        self._profile_selector.blockSignals(True)
        self._profile_selector.clear()
        for profile in collection.profiles:
            self._profile_selector.addItem(profile.name, profile.profile_id)
        self._profile_selector.blockSignals(False)
        if target is not None:
            index = self._profile_selector.findData(target)
            if index >= 0:
                self._profile_selector.setCurrentIndex(index)
                self._load_profile(collection.profiles[index])
                return
        if collection.profiles:
            self._profile_selector.setCurrentIndex(0)
            self._load_profile(collection.profiles[0])
        else:
            self._new_profile()

    @Slot(int)
    def _profile_selection_changed(self, index: int) -> None:
        if index < 0:
            return
        profile_id = self._profile_selector.itemData(index)
        collection = self._profile_service.repository.set_last_selected(profile_id)
        profile = next(
            item for item in collection.profiles if item.profile_id == profile_id
        )
        self._load_profile(profile)

    def _load_profile(self, profile: ServerProfile) -> None:
        self._editing_profile_id = profile.profile_id
        self._editing_execution_preset = profile.execution_preset
        self._editing_aitranss_runtime = profile.aitranss_runtime
        self._editing_runtime_hints = profile.runtime_hints
        self._editing_email_notification_enabled = (
            profile.email_notification_enabled
        )
        self._editing_email_notification_recipient = (
            profile.email_notification_recipient
        )
        self._name.setText(profile.name)
        self._host.setText(profile.host)
        self._port.setText(str(profile.port))
        self._username.setText(profile.username)
        self._password.clear()
        self._set_password_visible(False)
        saved = self._profile_service.has_saved_password(profile.profile_id)
        self._password.setPlaceholderText(
            "Saved password available" if saved else ""
        )
        self._save_password.setChecked(profile.save_password)
        self._auto_connect.setChecked(profile.auto_connect)
        self._remote_root.setText(profile.remote_project_root)
        self._delete_button.setEnabled(True)
        self._cluster_button.setEnabled(True)

    @Slot()
    def _new_profile(self) -> None:
        self._editing_profile_id = uuid4()
        self._editing_execution_preset = None
        self._editing_aitranss_runtime = None
        self._editing_runtime_hints = None
        self._editing_email_notification_enabled = False
        self._editing_email_notification_recipient = None
        for field in (
            self._name,
            self._host,
            self._username,
            self._password,
            self._remote_root,
        ):
            field.clear()
        self._port.setText("22")
        self._set_password_visible(False)
        self._password.setPlaceholderText("")
        for checkbox in (
            self._save_password,
            self._auto_connect,
        ):
            checkbox.setChecked(False)
        self._profile_selector.setCurrentIndex(-1)
        self._delete_button.setEnabled(False)
        self._cluster_button.setEnabled(False)
        self._status.setText("Enter a new server profile; no connection is open.")

    @Slot(bool)
    def _set_password_visible(self, visible: bool) -> None:
        self._password.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )
        self._password_visibility_button.blockSignals(True)
        self._password_visibility_button.setChecked(visible)
        self._password_visibility_button.blockSignals(False)
        label = "Hide password" if visible else "Show password"
        self._password_visibility_button.setToolTip(label)
        self._password_visibility_button.setAccessibleName(label)

    @Slot()
    def _save_profile(self) -> None:
        try:
            profile = self._collect_profile()
            collection = self._profile_service.save(
                profile,
                supplied_password=self._entered_password(),
            )
        except Exception as error:
            self._show_validation_error(error)
            return
        self._password.clear()
        self._refresh_profiles(collection, profile.profile_id)
        self._status.setText(f"Saved server profile: {profile.name}")

    @Slot()
    def _save_as_profile(self) -> None:
        try:
            draft = self._collect_profile()
            copied = self._profile_service.save_as(
                draft,
                draft.name,
                supplied_password=self._entered_password(),
            )
            collection = self._profile_service.repository.load()
        except Exception as error:
            self._show_validation_error(error)
            return
        self._password.clear()
        self._refresh_profiles(collection, copied.profile_id)
        self._status.setText(f"Saved new server profile: {copied.name}")

    @Slot()
    def _delete_profile(self) -> None:
        saved_ids = {
            profile.profile_id
            for profile in self._profile_service.repository.load().profiles
        }
        if self._editing_profile_id not in saved_ids:
            return
        answer = QMessageBox.question(
            self,
            "Delete server profile?",
            "Delete this local profile and its saved credential? Remote projects "
            "will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            collection = self._profile_service.delete(self._editing_profile_id)
        except Exception as error:
            self._show_validation_error(error)
            return
        self._refresh_profiles(collection)
        self._status.setText("Deleted local profile and associated saved credential.")

    @Slot()
    def _open_cluster_settings(self) -> None:
        collection = self._profile_service.repository.load()
        profile = next(
            (
                item
                for item in collection.profiles
                if item.profile_id == self._editing_profile_id
            ),
            None,
        )
        if profile is None:
            self._show_validation_error(
                ValueError(
                    "Save the server connection before configuring Cluster "
                    "Execution Settings"
                )
            )
            return
        dialog = ClusterExecutionSettingsDialog(
            profile,
            self,
            connection_service=self._connection_service,
            known_hosts=self._known_hosts,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = replace(
                profile,
                execution_preset=dialog.selected_preset(),
                aitranss_runtime=dialog.selected_aitranss_runtime(),
                runtime_hints=dialog.selected_runtime_hints(),
                orca_runtime=getattr(
                    dialog,
                    "selected_orca_runtime",
                    lambda: profile.orca_runtime,
                )(),
            )
            collection = self._profile_service.save(updated)
        except Exception as error:
            self._show_validation_error(error)
            return
        self._refresh_profiles(collection, updated.profile_id)
        self._status.setText(
            f"Saved Cluster Execution Settings for {updated.name}"
        )

    @Slot()
    def _browse_remote_workspace(self) -> None:
        if self._workers or self._remote_directory_workers:
            return
        try:
            entered_root = self._remote_root.text().strip()
            initial_directory = normalize_remote_directory(entered_root or "/")
            browse_profile = self._collect_profile(
                remote_project_root=initial_directory
            )
        except Exception as error:
            self._show_validation_error(error)
            return

        dialog = RemoteDirectoryDialog(initial_directory, self)
        self._remote_directory_dialog = dialog
        self._remote_directory_profile = browse_profile
        self._remote_directory_requested_path = None
        self._remote_directory_password = self._entered_password()
        self._remote_directory_retry_pending = False
        self._remote_directory_trust_retry_used = False
        self._remote_directory_password_retry_used = False
        dialog.directory_requested.connect(self._request_remote_directory)
        dialog.request_current_directory()
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._remote_root.setText(dialog.selected_directory)
                self._status.setText(
                    "Selected remote project workspace: "
                    + dialog.selected_directory
                )
        finally:
            self._remote_directory_dialog = None
            self._remote_directory_profile = None
            self._remote_directory_requested_path = None
            self._remote_directory_password = None
            self._remote_directory_retry_pending = False

    @Slot(str)
    def _request_remote_directory(self, directory: str) -> None:
        if (
            self._remote_directory_dialog is None
            or self._remote_directory_profile is None
            or self._remote_directory_workers
        ):
            return
        self._remote_directory_requested_path = directory
        self._remote_directory_retry_pending = False
        self._start_remote_directory_worker(
            directory,
            self._remote_directory_password,
        )

    def _start_remote_directory_worker(
        self,
        directory: str,
        password: str | None,
    ) -> None:
        dialog = self._remote_directory_dialog
        profile = self._remote_directory_profile
        if dialog is None or profile is None:
            return
        dialog.set_busy(True)
        worker = _RemoteDirectoryWorker(
            self._connection_service,
            profile,
            directory,
            password,
        )
        worker.signals.succeeded.connect(self._remote_directory_succeeded)
        worker.signals.failed.connect(self._remote_directory_failed)
        worker.signals.finished.connect(self._remote_directory_worker_finished)
        self._remote_directory_workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(object)
    def _remote_directory_succeeded(self, result: object) -> None:
        self._remote_directory_retry_pending = False
        if (
            self._remote_directory_dialog is None
            or not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], str)
            or not isinstance(result[1], tuple)
            or any(not isinstance(item, str) for item in result[1])
        ):
            self._show_remote_directory_error(
                RuntimeError("Remote directory listing returned an invalid result")
            )
            return
        directory, names = result
        self._remote_directory_dialog.show_directory(directory, names)

    @Slot(object)
    def _remote_directory_failed(self, error: object) -> None:
        dialog = self._remote_directory_dialog
        profile = self._remote_directory_profile
        if dialog is None or profile is None:
            return
        if (
            isinstance(error, PasswordRequiredError)
            and not self._remote_directory_password_retry_used
        ):
            password, accepted = QInputDialog.getText(
                dialog,
                "Password required",
                f"Password for {profile.name}:",
                QLineEdit.EchoMode.Password,
            )
            if accepted and password:
                self._remote_directory_password_retry_used = True
                self._remote_directory_password = password
                self._remote_directory_retry_pending = True
                dialog.show_error(
                    "Password received; retrying remote directory listing."
                )
                return
            dialog.show_error("Remote directory browsing cancelled.")
            return
        if (
            isinstance(error, UnknownHostKey)
            and not self._remote_directory_trust_retry_used
        ):
            if self._confirm_unknown_host(error.info):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_remote_directory_error(trust_error)
                    return
                self._remote_directory_trust_retry_used = True
                self._remote_directory_retry_pending = True
                dialog.show_error(
                    "Host key trusted; retrying remote directory listing."
                )
                return
            dialog.show_error(
                "Remote directory browsing cancelled; host key was not trusted."
            )
            return
        self._remote_directory_retry_pending = False
        self._show_remote_directory_error(error)

    @Slot(object)
    def _remote_directory_worker_finished(self, worker: object) -> None:
        if not isinstance(worker, _RemoteDirectoryWorker):
            return
        self._remote_directory_workers.discard(worker)
        if self._remote_directory_workers:
            return
        if (
            self._remote_directory_retry_pending
            and self._remote_directory_requested_path is not None
        ):
            self._remote_directory_retry_pending = False
            self._start_remote_directory_worker(
                self._remote_directory_requested_path,
                self._remote_directory_password,
            )
            return
        if self._remote_directory_dialog is not None:
            self._remote_directory_dialog.set_busy(False)

    def _show_remote_directory_error(self, error: object) -> None:
        message = (
            str(error)
            if isinstance(error, Exception)
            else "Remote directory browsing failed"
        )
        if isinstance(error, AuthenticationError):
            title = "Authentication failed"
        elif isinstance(error, PasswordRequiredError):
            title = "Password required"
        elif isinstance(error, HostKeyMismatch):
            title = "SSH host key mismatch"
        elif isinstance(error, (ConnectionTestError, RemoteExecutorError)):
            title = "Connection failed"
        else:
            title = "Remote directory unavailable"
        dialog = self._remote_directory_dialog
        if dialog is not None:
            dialog.show_error(message)
        QMessageBox.critical(dialog or self, title, message)

    @Slot()
    def _test_connection(self) -> None:
        try:
            profile = self._collect_profile()
        except Exception as error:
            self._show_validation_error(error)
            return
        self._trust_retry_used = False
        self._pending_trusted_retry = False
        self._retry_profile = profile
        self._retry_password = self._entered_password()
        self._start_connection_worker(profile, self._retry_password)

    def _start_connection_worker(
        self,
        profile: ServerProfile,
        password: str | None,
    ) -> None:
        self._set_test_running(True)
        worker = _ConnectionWorker(self._connection_service, profile, password)
        worker.signals.succeeded.connect(self._connection_succeeded)
        worker.signals.failed.connect(self._connection_failed)
        worker.signals.finished.connect(self._worker_finished)
        self._workers.add(worker)
        self._status.setText(f"Testing secure connection to {profile.name}...")
        self._thread_pool.start(worker)

    @Slot(object)
    def _connection_succeeded(self, result: object) -> None:
        if not isinstance(result, ConnectionTestSuccess):
            self._connection_failed(RuntimeError("invalid connection-test result"))
            return
        self._pending_trusted_retry = False
        self._status.setText(result.message)
        QMessageBox.information(self, "Connection successful", result.message)

    @Slot(object)
    def _connection_failed(self, error: object) -> None:
        if isinstance(error, UnknownHostKey) and not self._trust_retry_used:
            if self._confirm_unknown_host(error.info):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_connection_error(trust_error)
                    return
                self._trust_retry_used = True
                self._pending_trusted_retry = True
                self._status.setText(
                    "Host key trusted; retrying after the current connection closes."
                )
                return
            self._pending_trusted_retry = False
            self._status.setText("Connection cancelled; host key was not trusted.")
            return
        self._pending_trusted_retry = False
        self._show_connection_error(error)

    def _confirm_unknown_host(self, info: HostKeyInfo) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unknown SSH host key")
        box.setText("Trust this host key?")
        box.setInformativeText(
            f"Host: {info.hostname}:{info.port}\n"
            f"Algorithm: {info.algorithm}\n"
            f"SHA256: {info.sha256_fingerprint}"
        )
        trust_button = box.addButton(
            "Trust and Save",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is trust_button

    def _show_connection_error(self, error: object) -> None:
        message = str(error) if isinstance(error, Exception) else "Connection failed"
        self._status.setText(message)
        QMessageBox.critical(self, "Connection failed", message)

    @Slot(object)
    def _worker_finished(self, worker: object) -> None:
        if not isinstance(worker, _ConnectionWorker):
            return
        self._workers.discard(worker)
        if not self._workers:
            if self._pending_trusted_retry and self._retry_profile is not None:
                self._pending_trusted_retry = False
                self._start_connection_worker(
                    self._retry_profile,
                    self._retry_password,
                )
                return
            self._retry_password = None
            self._set_test_running(False)

    def _set_test_running(self, running: bool) -> None:
        for button in (
            self._test_button,
            self._new_button,
            self._save_button,
            self._save_as_button,
            self._close_button,
            self._browse_workspace_button,
        ):
            button.setEnabled(not running)
        profile_is_saved = (
            self._profile_selector.findData(self._editing_profile_id) >= 0
        )
        self._delete_button.setEnabled(not running and profile_is_saved)
        self._cluster_button.setEnabled(profile_is_saved)

    def accept(self) -> None:
        if self._workers or self._remote_directory_workers:
            self._status.setText("Wait for Test Connection to finish before closing.")
            return
        super().accept()

    def reject(self) -> None:
        if self._workers or self._remote_directory_workers:
            self._status.setText("Wait for Test Connection to finish before closing.")
            return
        super().reject()

    def _collect_profile(
        self,
        *,
        remote_project_root: str | None = None,
    ) -> ServerProfile:
        return ServerProfile(
            profile_id=self._editing_profile_id,
            name=self._name.text(),
            host=self._host.text(),
            port=_required_integer(self._port, "Port"),
            username=self._username.text(),
            remote_project_root=(
                self._remote_root.text()
                if remote_project_root is None
                else remote_project_root
            ),
            save_password=self._save_password.isChecked(),
            auto_connect=self._auto_connect.isChecked(),
            execution_preset=self._editing_execution_preset,
            aitranss_runtime=self._editing_aitranss_runtime,
            runtime_hints=self._editing_runtime_hints,
            email_notification_enabled=(
                self._editing_email_notification_enabled
            ),
            email_notification_recipient=(
                self._editing_email_notification_recipient
            ),
        )

    def _entered_password(self) -> str | None:
        return self._password.text() or None

    def _show_validation_error(self, error: Exception) -> None:
        message = str(error)
        self._status.setText(message)
        QMessageBox.critical(self, "Invalid server profile", message)


def create_server_profiles_dialog(
    parent: QWidget | None = None,
) -> ServerProfilesDialog:
    """Build the production dialog without opening a network connection."""

    secret_store = WindowsCredentialSecretStore()
    profile_service = ServerProfileService(
        ServerProfileRepository(server_profiles_path()),
        secret_store,
    )
    known_hosts = KnownHostStore(known_hosts_path())
    connection_service = ServerConnectionService(
        secret_store,
        lambda: ParamikoRemoteExecutor(known_hosts),
    )
    return ServerProfilesDialog(
        profile_service,
        connection_service,
        known_hosts,
        parent,
    )


def _required_integer(field: QLineEdit, label: str) -> int:
    text = field.text().strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{label} must be an integer") from None
