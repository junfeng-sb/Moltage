"""Read-only chooser for one existing remote project directory."""

from pathlib import PurePosixPath

from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)


class RemoteDirectoryDialog(QDialog):
    """Navigate existing remote folders and return the current directory."""

    directory_requested = Signal(str)

    def __init__(
        self,
        initial_directory: str,
        parent: QWidget | None = None,
        *,
        window_title: str = "Choose Remote Project Workspace",
        explanation: str = (
            "Choose an existing directory on this server. Calculation project "
            "folders will be created under the selected directory."
        ),
        selection_label: str = "remote project workspace",
    ) -> None:
        super().__init__(parent)
        self._current_directory = normalize_remote_directory(initial_directory)
        self._selected_directory: str | None = None
        self._busy = False
        self._loaded = False
        self._selection_label = selection_label

        self.setWindowTitle(window_title)
        self.setMinimumSize(620, 430)
        layout = QVBoxLayout(self)
        explanation_label = QLabel(explanation, self)
        explanation_label.setWordWrap(True)
        layout.addWidget(explanation_label)

        path_row = QHBoxLayout()
        self._up_button = QPushButton("Up", self)
        self._up_button.setObjectName("remoteDirectoryUp")
        self._up_button.clicked.connect(self._request_parent)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.setObjectName("remoteDirectoryRefresh")
        self._refresh_button.clicked.connect(self.request_current_directory)
        self._path = QLineEdit(self._current_directory, self)
        self._path.setObjectName("remoteDirectoryPath")
        self._path.setReadOnly(True)
        path_row.addWidget(self._up_button)
        path_row.addWidget(self._refresh_button)
        path_row.addWidget(self._path, 1)
        layout.addLayout(path_row)

        self._directories = QListWidget(self)
        self._directories.setObjectName("remoteDirectories")
        self._directories.itemDoubleClicked.connect(self._request_child)
        layout.addWidget(self._directories, 1)

        self._status = QLabel("Connecting to the server...", self)
        self._status.setObjectName("remoteDirectoryStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._cancel_button = QPushButton("Cancel", self)
        self._cancel_button.clicked.connect(self.reject)
        self._select_button = QPushButton("Select Folder", self)
        self._select_button.setObjectName("selectRemoteDirectory")
        self._select_button.clicked.connect(self._select_current)
        buttons.addWidget(self._cancel_button)
        buttons.addWidget(self._select_button)
        layout.addLayout(buttons)
        self._update_actions()

    @property
    def selected_directory(self) -> str:
        if self.result() != QDialog.DialogCode.Accepted:
            raise RuntimeError(f"Remote {self._selection_label} was not selected")
        if self._selected_directory is None:
            raise RuntimeError("Remote directory selection is incomplete")
        return self._selected_directory

    @Slot()
    def request_current_directory(self) -> None:
        if not self._busy:
            self.directory_requested.emit(self._current_directory)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        if self._busy:
            self._status.setText("Loading remote directories...")
        self._update_actions()

    def show_directory(
        self,
        directory: str,
        child_directories: tuple[str, ...],
    ) -> None:
        normalized = normalize_remote_directory(directory)
        self._current_directory = normalized
        self._path.setText(normalized)
        self._directories.clear()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        for name in child_directories:
            if not _is_remote_entry_name(name):
                continue
            self._directories.addItem(QListWidgetItem(icon, name))
        self._loaded = True
        self._status.setText(
            f"{self._directories.count()} subfolder(s). Double-click to open."
        )
        self._update_actions()

    def show_error(self, message: str) -> None:
        self._status.setText(message)

    @Slot(QListWidgetItem)
    def _request_child(self, item: QListWidgetItem) -> None:
        if self._busy or not _is_remote_entry_name(item.text()):
            return
        child = str(PurePosixPath(self._current_directory) / item.text())
        self.directory_requested.emit(child)

    @Slot()
    def _request_parent(self) -> None:
        if self._busy or self._current_directory == "/":
            return
        self.directory_requested.emit(
            str(PurePosixPath(self._current_directory).parent)
        )

    @Slot()
    def _select_current(self) -> None:
        if self._busy or not self._loaded:
            return
        self._selected_directory = self._current_directory
        self.accept()

    def _update_actions(self) -> None:
        enabled = not self._busy
        self._directories.setEnabled(enabled)
        self._refresh_button.setEnabled(enabled)
        self._up_button.setEnabled(
            enabled and self._loaded and self._current_directory != "/"
        )
        self._select_button.setEnabled(enabled and self._loaded)
        self._cancel_button.setEnabled(enabled)

    def reject(self) -> None:
        if self._busy:
            return
        super().reject()


class RemoteDirectorySignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class RemoteDirectoryWorker(QRunnable):
    """Connect, list one remote directory through SFTP, and close."""

    def __init__(
        self,
        connection_service,
        profile,
        directory: str,
        password: str | None,
    ) -> None:
        super().__init__()
        self.signals = RemoteDirectorySignals()
        self._connection_service = connection_service
        self._profile = profile
        self._directory = directory
        self._password = password
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        executor = None
        try:
            executor = self._connection_service.connect_for_remote_operation(
                self._profile,
                self._password,
            )
            names = tuple(
                sorted(
                    (
                        entry.name
                        for entry in executor.list_directory(self._directory)
                        if entry.is_directory
                    ),
                    key=str.casefold,
                )
            )
            executor.close()
            executor = None
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit((self._directory, names))
        finally:
            if executor is not None:
                try:
                    executor.close()
                except Exception:
                    pass
            self._password = None
            self.signals.finished.emit(self)


def normalize_remote_directory(value: object) -> str:
    """Normalize one absolute POSIX directory for display and navigation."""

    if not isinstance(value, str):
        raise ValueError("Remote directory must be an absolute POSIX path")
    normalized = value.strip().rstrip("/") or "/"
    if not normalized.startswith("/") or normalized.startswith("//"):
        raise ValueError("Remote directory must be an absolute POSIX path")
    if any(character in normalized for character in ("\x00", "\n", "\r", "\\")):
        raise ValueError("Remote directory contains unsupported characters")
    if any(part in {"", ".", ".."} for part in normalized.split("/")[1:]):
        if normalized != "/":
            raise ValueError(
                "Remote directory must not contain empty, '.' or '..' components"
            )
    return normalized


def _is_remote_entry_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and not any(
            character in value
            for character in ("/", "\\", "\x00", "\n", "\r")
        )
    )
