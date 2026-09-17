"""Credential resolution and read-only Test Connection orchestration."""

from dataclasses import dataclass
from typing import Callable

from moltage.domain.server_profile import ServerProfile
from moltage.remote.executor import (
    ConnectionRequest,
    RemoteAuthenticationError,
    RemoteExecutor,
    RemoteExecutorError,
    RemoteOperationStopToken,
    RemotePathNotDirectoryError,
)
from moltage.remote.known_hosts import HostKeyMismatch, UnknownHostKey
from moltage.remote.secrets import SecretStore


class PasswordRequiredError(RuntimeError):
    pass


class AutoConnectUnavailableError(RuntimeError):
    pass


class ConnectionTestError(RuntimeError):
    pass


class AuthenticationError(ConnectionTestError):
    """Raised only for a confirmed password-authentication rejection."""


@dataclass(frozen=True, slots=True)
class ConnectionTestSuccess:
    profile_name: str
    remote_project_root: str

    @property
    def message(self) -> str:
        return f"Connected to {self.profile_name}\nRoot: {self.remote_project_root}"


class ServerConnectionService:
    """Open short-lived password-only sessions only when an operation needs one."""

    def __init__(
        self,
        secret_store: SecretStore,
        executor_factory: Callable[[], RemoteExecutor],
    ) -> None:
        self._secret_store = secret_store
        self._executor_factory = executor_factory

    def test_connection(
        self,
        profile: ServerProfile,
        supplied_password: str | None = None,
    ) -> ConnectionTestSuccess:
        executor = self._connect(profile, supplied_password, automatic=False)
        try:
            root_stat = executor.stat(profile.remote_project_root)
            if not root_stat.is_directory:
                raise RemotePathNotDirectoryError(
                    "configured remote project root is not a directory: "
                    + profile.remote_project_root
                )
            executor.list_directory(profile.remote_project_root)
        except RemoteExecutorError as error:
            raise ConnectionTestError(str(error)) from None
        except Exception:
            raise ConnectionTestError(
                f"Connection test failed for {profile.name}"
            ) from None
        finally:
            executor.close()
        return ConnectionTestSuccess(profile.name, profile.remote_project_root)

    def connect_automatically(self, profile: ServerProfile) -> RemoteExecutor:
        """Connect on demand only when profile policy and saved secret permit it."""

        if not profile.auto_connect:
            raise AutoConnectUnavailableError(
                f"automatic connection is disabled for {profile.name}"
            )
        return self._connect(profile, None, automatic=True)

    def connect_for_remote_operation(
        self,
        profile: ServerProfile,
        supplied_password: str | None = None,
        *,
        stop_token: RemoteOperationStopToken | None = None,
    ) -> RemoteExecutor:
        """Open one explicit short-lived connection for a user-requested operation."""

        return self._connect(
            profile,
            supplied_password,
            automatic=False,
            action_name="remote operation",
            stop_token=stop_token,
        )

    def _connect(
        self,
        profile: ServerProfile,
        supplied_password: str | None,
        *,
        automatic: bool,
        action_name: str | None = None,
        stop_token: RemoteOperationStopToken | None = None,
    ) -> RemoteExecutor:
        password = supplied_password
        if password is None and profile.save_password:
            password = self._secret_store.get_password(profile.profile_id)
        if not password:
            action = (
                "automatic connection"
                if automatic
                else action_name or "connection test"
            )
            raise PasswordRequiredError(
                f"A password is required for the {action} to {profile.name}"
            )
        try:
            executor = self._executor_factory()
        except Exception:
            raise ConnectionTestError(
                f"Could not prepare a secure connection to {profile.name}"
            ) from None
        if stop_token is not None:
            stop_token.bind_executor(executor)
        try:
            executor.connect(ConnectionRequest(profile, password))
            if stop_token is not None:
                stop_token.checkpoint()
        except (UnknownHostKey, HostKeyMismatch):
            executor.close()
            if stop_token is not None:
                stop_token.checkpoint()
            raise
        except RemoteAuthenticationError as error:
            executor.close()
            if stop_token is not None:
                stop_token.checkpoint()
            message = str(error)
            if password:
                message = message.replace(password, "[redacted]")
            message = next(
                (line.strip() for line in message.splitlines() if line.strip()),
                f"Password authentication failed for {profile.name}",
            )
            raise AuthenticationError(message[:400]) from None
        except RemoteExecutorError as error:
            executor.close()
            if stop_token is not None:
                stop_token.checkpoint()
            message = str(error)
            if password:
                message = message.replace(password, "[redacted]")
            message = next(
                (line.strip() for line in message.splitlines() if line.strip()),
                "Secure connection failed",
            )
            raise ConnectionTestError(message[:400]) from None
        except Exception:
            executor.close()
            if stop_token is not None:
                stop_token.checkpoint()
            raise ConnectionTestError(
                f"Could not establish a secure connection to {profile.name}"
            ) from None
        return executor
