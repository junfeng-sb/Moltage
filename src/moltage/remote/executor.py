"""Narrow remote-operation boundary without provider objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import Protocol

from moltage.domain.server_profile import ServerProfile


class RemoteExecutorError(RuntimeError):
    """Base error for sanitized remote infrastructure failures."""


class RemoteAuthenticationError(RemoteExecutorError):
    """Raised only when the SSH server rejects authentication."""


class RemoteConnectionError(RemoteExecutorError):
    """Raised when a secure SSH/SFTP connection cannot be established."""


class RemotePathNotFoundError(RemoteExecutorError):
    pass


class RemotePathNotDirectoryError(RemoteExecutorError):
    pass


class RemotePathAlreadyExistsError(RemoteExecutorError):
    pass


class RemoteRenameError(RemoteExecutorError):
    pass


class RemoteCommandOutcomeUnknown(RemoteExecutorError):
    """Raised when a command may have run but no reliable result was received."""


class RemoteOperationStopped(RuntimeError):
    """Raised when the user stops one cancellable remote status operation."""


class RemoteOperationStopToken:
    """Cooperatively stop a remote status operation and its active session."""

    def __init__(self) -> None:
        self._requested = Event()
        self._lock = Lock()
        self._executor: RemoteExecutor | None = None

    @property
    def is_requested(self) -> bool:
        return self._requested.is_set()

    def request_stop(self) -> None:
        """Record the request and close any bound session without blocking Qt."""

        self._requested.set()
        with self._lock:
            executor = self._executor
        if executor is not None:
            Thread(
                target=_close_executor_quietly,
                args=(executor,),
                name="moltage-refresh-stop",
                daemon=True,
            ).start()

    def checkpoint(self) -> None:
        if self.is_requested:
            raise RemoteOperationStopped("Remote refresh stopped by the user.")

    def bind_executor(self, executor: "RemoteExecutor") -> None:
        """Bind only the session owned by this one stoppable operation."""

        with self._lock:
            if self._requested.is_set():
                stop_before_bind = True
            else:
                self._executor = executor
                stop_before_bind = False
        if stop_before_bind:
            _close_executor_quietly(executor)
            raise RemoteOperationStopped("Remote refresh stopped by the user.")

    def unbind_executor(self, executor: "RemoteExecutor") -> None:
        with self._lock:
            if self._executor is executor:
                self._executor = None


@dataclass(frozen=True, slots=True)
class ConnectionRequest:
    """One password-only connection request with a redacted representation."""

    profile: ServerProfile
    password: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("connection request requires a ServerProfile")
        if not isinstance(self.password, str) or not self.password:
            raise ValueError("connection request requires a nonempty password")


@dataclass(frozen=True, slots=True)
class RemotePathStat:
    is_directory: bool
    size: int | None = None


@dataclass(frozen=True, slots=True)
class RemoteDirectoryEntry:
    name: str
    is_directory: bool


@dataclass(frozen=True, slots=True)
class RemoteCommandResult:
    exit_status: int
    stdout: bytes
    stderr: bytes


class RemoteExecutor(Protocol):
    """Operations needed by connection checks and managed-project storage."""

    def connect(self, request: ConnectionRequest) -> None: ...

    def close(self) -> None: ...

    def stat(self, path: str) -> RemotePathStat: ...

    def list_directory(self, path: str) -> tuple[RemoteDirectoryEntry, ...]: ...

    def mkdir(self, path: str) -> None: ...

    def read_bytes(self, path: str) -> bytes: ...

    def download_file(
        self,
        path: str,
        destination: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> None: ...

    def read_file_head(self, path: str, max_bytes: int) -> bytes: ...

    def read_file_tail(self, path: str, max_bytes: int) -> bytes: ...

    def write_bytes(self, path: str, data: bytes) -> None: ...

    def rename(self, source: str, destination: str) -> None: ...

    def execute(self, command: str) -> RemoteCommandResult: ...


def _close_executor_quietly(executor: RemoteExecutor) -> None:
    try:
        executor.close()
    except Exception:
        # Closing the transport is best-effort; the worker checkpoint remains
        # authoritative and this path never dispatches a scheduler mutation.
        pass
