"""Import one completed remote ORCA optimization into a managed project."""

from datetime import date
from pathlib import PurePosixPath

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from moltage.app.orca_import import (
    OrcaImportError,
    OrcaImportRequest,
    OrcaImportValidation,
    OrcaImportValidationRequest,
    default_managed_base_name,
)
from moltage.app.project_planning import (
    ProjectPlanningError,
    project_directory_candidates,
    validate_project_base_name,
)
from moltage.domain.server_profile import ServerProfile
from moltage.remote.executor import (
    RemoteOperationStopToken,
    RemoteOperationStopped,
)
from moltage.gui.remote_directory_dialog import (
    RemoteDirectoryDialog,
    RemoteDirectoryWorker,
    normalize_remote_directory,
)
from moltage.orca.project_evidence import OrcaImportWavefunctionReadiness


DIALOG_TITLE = "Import Existing ORCA Optimization"
DIALOG_EXPLANATION = (
    "Import a completed ORCA optimization from a configured remote server. "
    "Moltage will not rerun ORCA or modify the source directory."
)
PATH_PLACEHOLDER = "/remote/path/to/completed/orca_calculation"


class OrcaImportSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class OrcaImportValidationWorker(QRunnable):
    """Read and validate one remote ORCA directory off the GUI thread."""

    def __init__(self, service, request) -> None:
        super().__init__()
        self.signals = OrcaImportSignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.validate(self._request)
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = None
            self.signals.finished.emit(self)


class OrcaImportWorker(QRunnable):
    """Create one managed project from validated external ORCA evidence."""

    def __init__(self, service, request) -> None:
        super().__init__()
        self.signals = OrcaImportSignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.import_optimization(self._request)
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = None
            self.signals.finished.emit(self)


class ImportExistingOrcaOptimizationDialog(QDialog):
    """Collect one server, source directory and managed name, then import."""

    project_imported = Signal(object)
    server_settings_requested = Signal()

    def __init__(
        self,
        profiles: tuple[ServerProfile, ...],
        last_selected_profile_id,
        import_service,
        connection_service,
        secret_store,
        parent: QWidget | None = None,
        *,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._profiles = tuple(profiles)
        self._import_service = import_service
        self._connection_service = connection_service
        self._secret_store = secret_store
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._workers: set[QRunnable] = set()
        self._remote_directory_dialog: RemoteDirectoryDialog | None = None
        self._remote_directory_workers: set[RemoteDirectoryWorker] = set()
        self._remote_directory_password: str | None = None
        self._validation: OrcaImportValidation | None = None
        self._candidate_choice_active = False
        self._stop_token: RemoteOperationStopToken | None = None
        self._busy = False
        self._imported_project = None

        self.setWindowTitle(DIALOG_TITLE)
        self.setObjectName("importExistingOrcaOptimization")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)

        explanation = QLabel(DIALOG_EXPLANATION, self)
        explanation.setObjectName("orcaImportExplanation")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self._server = QComboBox(self)
        self._server.setObjectName("orcaImportServerProfile")
        for profile in self._profiles:
            self._server.addItem(profile.name, profile.profile_id)
        if last_selected_profile_id is not None:
            index = self._server.findData(last_selected_profile_id)
            if index >= 0:
                self._server.setCurrentIndex(index)
        self._server.currentIndexChanged.connect(self._server_changed)
        form.addRow("Server", self._server)

        path_row = QHBoxLayout()
        self._path = QLineEdit(self)
        self._path.setObjectName("orcaImportSourceDirectory")
        self._path.setPlaceholderText(PATH_PLACEHOLDER)
        self._path.textChanged.connect(self._source_changed)
        self._browse = QPushButton("Browse...", self)
        self._browse.setObjectName("orcaImportBrowse")
        self._browse.clicked.connect(self._browse_source_directory)
        self._validate = QPushButton("Validate", self)
        self._validate.setObjectName("orcaImportValidate")
        self._validate.clicked.connect(self._start_validation)
        path_row.addWidget(self._path, 1)
        path_row.addWidget(self._browse)
        path_row.addWidget(self._validate)
        path_container = QWidget(self)
        path_container.setLayout(path_row)
        form.addRow("Remote ORCA optimization directory", path_container)

        self._candidates = QComboBox(self)
        self._candidates.setObjectName("orcaImportCandidate")
        self._candidates.currentIndexChanged.connect(self._candidate_changed)
        self._candidates.setVisible(False)
        self._candidate_label = QLabel("Result to import", self)
        self._candidate_label.setVisible(False)
        form.addRow(self._candidate_label, self._candidates)

        self._project_name = QLineEdit(self)
        self._project_name.setObjectName("orcaImportProjectName")
        self._project_name.textChanged.connect(self._refresh_destination_preview)
        form.addRow("Managed project name", self._project_name)

        self._destination = QLabel("", self)
        self._destination.setObjectName("orcaImportDestinationPreview")
        self._destination.setWordWrap(True)
        self._destination.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("Moltage will create", self._destination)
        layout.addLayout(form)

        results = QGroupBox("Validation result", self)
        results.setObjectName("orcaImportResults")
        results_layout = QVBoxLayout(results)
        self._results = QLabel("Validate the remote directory to continue.", results)
        self._results.setObjectName("orcaImportResultText")
        self._results.setWordWrap(True)
        self._results.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        results_layout.addWidget(self._results)
        layout.addWidget(results)

        self._status = QLabel("", self)
        self._status.setObjectName("orcaImportStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        self._server_settings = QPushButton("Server Settings...", self)
        self._server_settings.setObjectName("orcaImportServerSettings")
        self._server_settings.clicked.connect(self._open_server_settings)
        buttons.addWidget(self._server_settings)
        buttons.addStretch(1)
        self._stop = QPushButton("Stop", self)
        self._stop.setObjectName("orcaImportStop")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(self._stop_operation)
        buttons.addWidget(self._stop)
        self._close = QPushButton("Close", self)
        self._close.setObjectName("orcaImportClose")
        self._close.clicked.connect(self.reject)
        self._import = QPushButton("Import", self)
        self._import.setObjectName("orcaImportConfirm")
        self._import.setDefault(True)
        self._import.clicked.connect(self._start_import)
        buttons.addWidget(self._close)
        buttons.addWidget(self._import)
        layout.addLayout(buttons)

        if not self._profiles:
            self._results.setText(
                "No server is configured. Add a server connection in Server "
                "Settings, then reopen this dialog."
            )
        self._refresh_destination_preview()
        self._update_actions()

    @property
    def imported_project(self):
        return self._imported_project

    @property
    def validation(self) -> OrcaImportValidation | None:
        return self._validation

    def selected_profile(self) -> ServerProfile | None:
        profile_id = self._server.currentData()
        return next(
            (item for item in self._profiles if item.profile_id == profile_id),
            None,
        )

    def set_source_directory(self, directory: str) -> None:
        self._path.setText(directory)

    def set_managed_project_name(self, name: str) -> None:
        self._project_name.setText(name)

    @Slot(int)
    def _server_changed(self, _index: int) -> None:
        self._discard_validation()
        self._refresh_destination_preview()
        self._update_actions()

    @Slot(str)
    def _source_changed(self, value: str) -> None:
        self._discard_validation()
        if not self._project_name.text().strip():
            derived = default_managed_base_name(value.strip())
            if value.strip():
                self._project_name.setText(derived)
        self._update_actions()

    @Slot(int)
    def _candidate_changed(self, _index: int) -> None:
        if self._busy or not self._candidate_choice_active:
            return
        self._start_validation()

    def _discard_validation(self) -> None:
        self._validation = None
        self._results.setText("Validate the remote directory to continue.")

    @Slot()
    def _refresh_destination_preview(self) -> None:
        self._destination.setText(self._destination_text())
        self._update_actions()

    def _destination_text(self) -> str:
        profile = self.selected_profile()
        if profile is None:
            return "Select a configured server to see the destination."
        try:
            base_name = validate_project_base_name(self._project_name.text().strip())
        except ProjectPlanningError:
            return (
                f"{profile.remote_project_root}/<managed project name>  "
                "(letters, digits, '_' and '-' only)"
            )
        candidate = next(iter(project_directory_candidates(base_name, date.today())))
        destination = str(PurePosixPath(profile.remote_project_root) / candidate)
        return (
            f"{destination}\nThe source directory is not modified. WBL results "
            "are written under this managed project."
        )

    @Slot()
    def _open_server_settings(self) -> None:
        self.server_settings_requested.emit()

    @Slot()
    def _browse_source_directory(self) -> None:
        if self._busy or self._remote_directory_workers:
            return
        profile = self.selected_profile()
        if profile is None:
            self._require_server_profile()
            return
        resolved, password = self._password_for(profile)
        if not resolved:
            return
        entered = self._path.text().strip()
        try:
            initial = normalize_remote_directory(entered or "/")
        except ValueError:
            initial = "/"
        dialog = RemoteDirectoryDialog(
            initial,
            self,
            window_title="Choose Remote ORCA Optimization Directory",
            explanation=(
                "Choose the existing remote directory that contains the "
                "completed ORCA optimization. Moltage only reads it."
            ),
            selection_label="ORCA optimization directory",
        )
        self._remote_directory_dialog = dialog
        self._remote_directory_password = password
        dialog.directory_requested.connect(self._request_remote_directory)
        dialog.request_current_directory()
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._path.setText(dialog.selected_directory)
        finally:
            self._remote_directory_dialog = None
            self._remote_directory_password = None

    @Slot(str)
    def _request_remote_directory(self, directory: str) -> None:
        dialog = self._remote_directory_dialog
        profile = self.selected_profile()
        if dialog is None or profile is None or self._remote_directory_workers:
            return
        dialog.set_busy(True)
        worker = RemoteDirectoryWorker(
            self._connection_service,
            profile,
            directory,
            self._remote_directory_password,
        )
        worker.signals.succeeded.connect(self._remote_directory_succeeded)
        worker.signals.failed.connect(self._remote_directory_failed)
        worker.signals.finished.connect(self._remote_directory_finished)
        self._remote_directory_workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(object)
    def _remote_directory_succeeded(self, result: object) -> None:
        dialog = self._remote_directory_dialog
        if dialog is None or not isinstance(result, tuple):
            return
        directory, names = result
        dialog.set_busy(False)
        dialog.show_directory(directory, names)

    @Slot(object)
    def _remote_directory_failed(self, error: object) -> None:
        dialog = self._remote_directory_dialog
        if dialog is None:
            return
        dialog.set_busy(False)
        dialog.show_error(str(error))

    @Slot(object)
    def _remote_directory_finished(self, worker: object) -> None:
        self._remote_directory_workers.discard(worker)

    @Slot()
    def _start_validation(self) -> None:
        if self._busy:
            return
        profile = self.selected_profile()
        if profile is None:
            self._require_server_profile()
            return
        directory = self._path.text().strip()
        if not directory:
            self._results.setText(
                "Enter or browse to the remote ORCA optimization directory."
            )
            return
        resolved, password = self._password_for(profile)
        if not resolved:
            return
        stem = self._candidates.currentData() if self._candidate_choice_active else None
        self._stop_token = RemoteOperationStopToken()
        self._set_busy(True, "Validating the remote ORCA directory...")
        self._validation = None
        worker = OrcaImportValidationWorker(
            self._import_service,
            OrcaImportValidationRequest(
                profile, directory, stem, password, self._stop_token
            ),
        )
        worker.signals.succeeded.connect(self._validation_succeeded)
        worker.signals.failed.connect(self._operation_failed)
        worker.signals.finished.connect(self._worker_finished)
        self._workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(object)
    def _validation_succeeded(self, result: object) -> None:
        if not isinstance(result, OrcaImportValidation):
            return
        self._validation = result
        self._show_candidates(result)
        self._results.setText(_describe_validation(result))
        self._status.setText("")
        self._update_actions()

    @Slot()
    def _start_import(self) -> None:
        if self._busy:
            return
        validation = self._validation
        profile = self.selected_profile()
        if validation is None or not validation.importable or profile is None:
            return
        try:
            base_name = validate_project_base_name(self._project_name.text().strip())
        except ProjectPlanningError as error:
            self._results.setText(str(error))
            return
        resolved, password = self._password_for(profile)
        if not resolved:
            return
        self._stop_token = RemoteOperationStopToken()
        self._set_busy(True, "Creating the managed ORCA project...")
        worker = OrcaImportWorker(
            self._import_service,
            OrcaImportRequest(
                profile,
                validation,
                base_name,
                PurePosixPath(validation.source_directory).name or base_name,
                password,
                self._stop_token,
            ),
        )
        worker.signals.succeeded.connect(self._import_succeeded)
        worker.signals.failed.connect(self._operation_failed)
        worker.signals.finished.connect(self._worker_finished)
        self._workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(object)
    def _import_succeeded(self, result: object) -> None:
        self._imported_project = result
        self.project_imported.emit(result)
        QMessageBox.information(
            self,
            "ORCA optimization imported",
            "The completed ORCA optimization was imported as "
            f"{result.remote_project_path}.\n\nOpen Project Manager, refresh this "
            "project, and use its first status indicator to open the optimized "
            "geometry before running Step 2 — WBL Transmission.",
        )
        self.accept()

    @Slot()
    def _stop_operation(self) -> None:
        token = self._stop_token
        if token is not None:
            self._status.setText("Stopping the remote operation...")
            token.request_stop()

    @Slot(object)
    def _operation_failed(self, error: object) -> None:
        if isinstance(error, RemoteOperationStopped):
            message = "The remote operation was stopped. No project was created."
        elif isinstance(error, (OrcaImportError, ValueError, RuntimeError)):
            message = str(error)
        else:
            message = "The remote operation failed."
        self._results.setText(message)
        self._status.setText("")

    @Slot(object)
    def _worker_finished(self, worker: object) -> None:
        self._workers.discard(worker)
        if not self._workers:
            self._stop_token = None
            self._set_busy(False, "")

    def _show_candidates(self, validation: OrcaImportValidation) -> None:
        stems = validation.candidate_stems
        show = len(stems) > 1
        blocker = self._candidates.blockSignals(True)
        self._candidates.clear()
        for stem in stems:
            self._candidates.addItem(stem, stem)
        if validation.selected is not None:
            index = self._candidates.findData(validation.selected.stem)
            if index >= 0:
                self._candidates.setCurrentIndex(index)
        self._candidates.blockSignals(blocker)
        self._candidate_choice_active = show
        self._candidates.setVisible(show)
        self._candidate_label.setVisible(show)

    def _password_for(self, profile: ServerProfile) -> tuple[bool, str | None]:
        if profile.save_password and self._secret_store.get_password(
            profile.profile_id
        ):
            return True, None
        password, accepted = QInputDialog.getText(
            self,
            "Password required",
            f"Password for {profile.username}@{profile.host}:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted or not password:
            return False, None
        return True, password

    def _require_server_profile(self) -> None:
        self._results.setText(
            "No server is configured. Add a server connection in Server "
            "Settings, then validate this directory again."
        )

    def _set_busy(self, busy: bool, message: str) -> None:
        self._busy = bool(busy)
        self._status.setText(message)
        self._update_actions()

    def _update_actions(self) -> None:
        enabled = not self._busy
        has_profile = self.selected_profile() is not None
        self._server.setEnabled(enabled and bool(self._profiles))
        self._path.setEnabled(enabled)
        self._browse.setEnabled(enabled and has_profile)
        self._validate.setEnabled(enabled and has_profile)
        self._candidates.setEnabled(enabled)
        self._project_name.setEnabled(enabled)
        self._close.setEnabled(enabled)
        self._stop.setEnabled(self._busy and self._stop_token is not None)
        self._server_settings.setEnabled(enabled)
        self._import.setEnabled(
            enabled
            and has_profile
            and self._validation is not None
            and self._validation.importable
            and _valid_base_name(self._project_name.text())
        )

    def reject(self) -> None:
        if self._busy:
            return
        super().reject()


def _valid_base_name(value: str) -> bool:
    try:
        validate_project_base_name(value.strip())
    except ProjectPlanningError:
        return False
    return True


def _describe_validation(validation: OrcaImportValidation) -> str:
    """Render validation as user-facing statements, never parser internals."""

    if validation.selection_required:
        return (
            f"{validation.blocking_reason}\n\nCompleted calculations found: "
            + ", ".join(validation.candidate_stems)
        )
    if validation.blocking_reason is not None:
        return validation.blocking_reason
    evidence = validation.evidence
    assert evidence is not None
    candidate = evidence.candidate
    identity = evidence.identity
    lines = [
        f"ORCA output: detected ({candidate.output_filename})",
        "Normal termination: verified",
        "Geometry optimization: converged",
        f"Wavefunction source: {candidate.wavefunction_filename}",
        f"Optimized geometry: available ({candidate.geometry_filename}, "
        f"{len(evidence.optimized_structure)} atoms)",
        f"Charge and multiplicity: {identity.charge} / {identity.multiplicity}",
    ]
    if validation.coordinate_path is not None:
        lines.append(
            f"Starting coordinates: {validation.coordinate_path} (read through "
            "*xyzfile and written inline into the managed orca_opt.inp; the "
            "source input is not modified)"
        )
    if identity.method is None:
        lines.append(
            "Method and basis: not recognized from the submitted input; WBL will "
            "require manually specified contact AO indices."
        )
    else:
        basis = identity.basis.value if identity.basis is not None else "none"
        lines.append(f"Method and basis: {identity.method.value} / {basis}")
    if validation.wavefunction_readiness is OrcaImportWavefunctionReadiness.READY:
        lines.append("WBL readiness: ready")
    else:
        lines.append(
            "WBL readiness: configuration required — "
            + (validation.readiness_diagnostic or "")
        )
    if validation.molden_filenames:
        lines.append(
            "Note: an existing Molden file was found and is not used. Moltage "
            "derives WBL evidence from the .gbw wavefunction."
        )
    return "\n".join(lines)
