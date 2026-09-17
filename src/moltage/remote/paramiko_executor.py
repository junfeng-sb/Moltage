"""Strict password-only Paramiko implementation of the remote boundary."""

import errno
import stat as stat_module
import time
from typing import Callable

import paramiko

from moltage.remote.executor import (
    ConnectionRequest,
    RemoteAuthenticationError,
    RemoteCommandResult,
    RemoteCommandOutcomeUnknown,
    RemoteConnectionError,
    RemoteDirectoryEntry,
    RemoteExecutorError,
    RemotePathAlreadyExistsError,
    RemotePathNotFoundError,
    RemotePathStat,
    RemoteRenameError,
)
from moltage.remote.known_hosts import (
    HostKeyMismatch,
    KnownHostStore,
    UnknownHostKey,
    host_key_info,
)


_COMMAND_READ_CHUNK_SIZE = 64 * 1024
_COMMAND_POLL_INTERVAL_SECONDS = 0.01


class _UnknownKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(
        self,
        known_hosts: KnownHostStore,
        hostname: str,
        port: int,
    ) -> None:
        self._known_hosts = known_hosts
        self._hostname = hostname
        self._port = port

    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        del client, hostname
        self._known_hosts.verify(self._hostname, self._port, key)


class ParamikoRemoteExecutor:
    """Keep all raw Paramiko objects inside one infrastructure adapter."""

    def __init__(
        self,
        known_hosts: KnownHostStore,
        *,
        client_factory: Callable[[], paramiko.SSHClient] = paramiko.SSHClient,
    ) -> None:
        self._known_hosts = known_hosts
        self._client_factory = client_factory
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def connect(self, request: ConnectionRequest) -> None:
        self.close()
        profile = request.profile
        client = self._client_factory()
        if self._known_hosts.path.exists():
            try:
                client.load_host_keys(str(self._known_hosts.path))
            except (OSError, paramiko.SSHException):
                raise RemoteExecutorError(
                    "the application known-hosts file could not be loaded"
                ) from None
        client.set_missing_host_key_policy(
            _UnknownKeyPolicy(self._known_hosts, profile.host, profile.port)
        )
        # A refresh may interrupt the session while SSH/SFTP is opening.
        # Retain the client before the blocking handshake, not only after it.
        self._client = client
        try:
            client.connect(
                hostname=profile.host,
                port=profile.port,
                username=profile.username,
                password=request.password,
                allow_agent=False,
                look_for_keys=False,
                timeout=15.0,
                auth_timeout=15.0,
                banner_timeout=15.0,
            )
            sftp = client.open_sftp()
        except (UnknownHostKey, HostKeyMismatch):
            client.close()
            raise
        except paramiko.BadHostKeyException as error:
            client.close()
            raise HostKeyMismatch(
                profile.host,
                profile.port,
                host_key_info(profile.host, profile.port, error.expected_key).sha256_fingerprint,
                host_key_info(profile.host, profile.port, error.key).sha256_fingerprint,
            ) from None
        except paramiko.AuthenticationException:
            client.close()
            raise RemoteAuthenticationError(
                f"Password authentication failed for {profile.name}"
            ) from None
        except (OSError, paramiko.SSHException):
            client.close()
            raise RemoteConnectionError(
                f"Could not establish a secure SSH/SFTP connection to {profile.name}"
            ) from None
        if self._client is not client:
            sftp.close()
            client.close()
            raise RemoteConnectionError("The SSH/SFTP connection was interrupted")
        self._sftp = sftp

    def close(self) -> None:
        sftp = self._sftp
        client = self._client
        first_error: BaseException | None = None
        try:
            if sftp is not None:
                try:
                    sftp.close()
                except BaseException as error:
                    first_error = error
            if client is not None:
                try:
                    client.close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            self._sftp = None
            self._client = None
        if first_error is not None:
            raise first_error

    def stat(self, path: str) -> RemotePathStat:
        try:
            attributes = self._require_sftp().stat(path)
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote path does not exist: {path}"
                ) from None
            raise RemoteExecutorError(f"could not inspect remote path: {path}") from None
        return RemotePathStat(
            is_directory=stat_module.S_ISDIR(attributes.st_mode),
            size=getattr(attributes, "st_size", None),
        )

    def list_directory(self, path: str) -> tuple[RemoteDirectoryEntry, ...]:
        try:
            entries = self._require_sftp().listdir_attr(path)
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote directory does not exist: {path}"
                ) from None
            raise RemoteExecutorError(
                f"could not list remote directory: {path}"
            ) from None
        return tuple(
            sorted(
                (
                    RemoteDirectoryEntry(
                        entry.filename,
                        stat_module.S_ISDIR(entry.st_mode),
                    )
                    for entry in entries
                ),
                key=lambda entry: entry.name,
            )
        )

    def mkdir(self, path: str) -> None:
        sftp = self._require_sftp()
        try:
            sftp.mkdir(path)
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise RemotePathAlreadyExistsError(
                    f"remote path already exists: {path}"
                ) from None
            # SFTP v3 has no dedicated "already exists" status. Some servers
            # return a generic SSH_FX_FAILURE, which Paramiko exposes without
            # errno. A same-session stat *after* the authoritative mkdir
            # failure safely confirms that the candidate name is occupied;
            # it is never used as an exists-before-mkdir allocation check.
            if error.errno is None:
                try:
                    sftp.stat(path)
                except OSError:
                    pass
                else:
                    raise RemotePathAlreadyExistsError(
                        f"remote path already exists: {path}"
                    ) from None
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote parent directory does not exist: {path}"
                ) from None
            raise RemoteExecutorError(
                f"could not create remote directory: {path}"
            ) from None

    def read_bytes(self, path: str) -> bytes:
        try:
            with self._require_sftp().open(path, "rb") as remote_file:
                return remote_file.read()
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote file does not exist: {path}"
                ) from None
            raise RemoteExecutorError(f"could not read remote file: {path}") from None

    def download_file(
        self,
        path: str,
        destination: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Stream one remote file to disk with non-authoritative byte progress."""

        if not isinstance(destination, str) or not destination or "\x00" in destination:
            raise ValueError("local download destination must be nonempty NUL-free text")

        callback = None
        if progress is not None:
            def callback(transferred, total):
                try:
                    progress(int(transferred), int(total))
                except Exception:
                    # Presentation must never change the transfer outcome.
                    pass

        try:
            self._require_sftp().get(path, destination, callback=callback)
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote file does not exist: {path}"
                ) from None
            raise RemoteExecutorError(
                f"could not download remote file: {path}"
            ) from None
        except (EOFError, paramiko.SSHException):
            raise RemoteExecutorError(
                f"could not download remote file: {path}"
            ) from None

    def read_file_head(self, path: str, max_bytes: int) -> bytes:
        """Read at most the requested initial bytes without downloading the file."""

        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise ValueError("remote head size must be a positive integer")
        try:
            with self._require_sftp().open(path, "rb") as remote_file:
                return remote_file.read(max_bytes)
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote file does not exist: {path}"
                ) from None
            raise RemoteExecutorError(
                f"could not read remote file head: {path}"
            ) from None
        except (EOFError, paramiko.SSHException):
            raise RemoteExecutorError(
                f"could not read remote file head: {path}"
            ) from None

    def read_file_tail(self, path: str, max_bytes: int) -> bytes:
        """Read at most the requested final bytes without downloading the file."""

        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise ValueError("remote tail size must be a positive integer")
        sftp = self._require_sftp()
        try:
            size = sftp.stat(path).st_size
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise RemoteExecutorError(
                    f"remote file size is unavailable: {path}"
                )
            with sftp.open(path, "rb") as remote_file:
                remote_file.seek(max(0, size - max_bytes))
                return remote_file.read(max_bytes)
        except RemoteExecutorError:
            raise
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise RemotePathNotFoundError(
                    f"remote file does not exist: {path}"
                ) from None
            raise RemoteExecutorError(
                f"could not read remote file tail: {path}"
            ) from None
        except (EOFError, paramiko.SSHException):
            raise RemoteExecutorError(
                f"could not read remote file tail: {path}"
            ) from None

    def write_bytes(self, path: str, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("remote writes require bytes")
        try:
            with self._require_sftp().open(path, "wb") as remote_file:
                remote_file.write(data)
        except OSError:
            raise RemoteExecutorError(f"could not write remote file: {path}") from None

    def rename(self, source: str, destination: str) -> None:
        try:
            self._require_sftp().posix_rename(source, destination)
        except (OSError, IOError):
            raise RemoteRenameError(
                f"atomic remote rename failed: {source} -> {destination}"
            ) from None

    def execute(self, command: str) -> RemoteCommandResult:
        if not isinstance(command, str) or not command or "\x00" in command:
            raise ValueError("remote command must be nonempty NUL-free text")
        client = self._require_client()
        try:
            _stdin, stdout, stderr = client.exec_command(command)
        except Exception:
            raise RemoteCommandOutcomeUnknown(
                "remote command outcome is unknown after dispatch began"
            ) from None
        channel = None
        stdout_bytes = bytearray()
        stderr_bytes = bytearray()
        result: RemoteCommandResult | None = None
        try:
            channel = stdout.channel
            while True:
                made_progress = False
                if channel.recv_ready():
                    stdout_bytes.extend(
                        channel.recv(_COMMAND_READ_CHUNK_SIZE)
                    )
                    made_progress = True
                if channel.recv_stderr_ready():
                    stderr_bytes.extend(
                        channel.recv_stderr(_COMMAND_READ_CHUNK_SIZE)
                    )
                    made_progress = True

                exit_ready = channel.exit_status_ready()
                stdout_pending = channel.recv_ready()
                stderr_pending = channel.recv_stderr_ready()
                streams_complete = channel.eof_received or channel.closed
                if channel.closed and not exit_ready and not stdout_pending and not stderr_pending:
                    # An interrupted refresh has no exit status to wait for.
                    # Retain UNKNOWN rather than polling a closed channel forever.
                    break
                if (
                    streams_complete
                    and exit_ready
                    and not stdout_pending
                    and not stderr_pending
                ):
                    exit_status = channel.recv_exit_status()
                    result = RemoteCommandResult(
                        exit_status,
                        bytes(stdout_bytes),
                        bytes(stderr_bytes),
                    )
                    break
                if (
                    not made_progress
                    and not stdout_pending
                    and not stderr_pending
                ):
                    time.sleep(_COMMAND_POLL_INTERVAL_SECONDS)
        except Exception:
            # The outcome is unknown until both streams are complete and the
            # remote exit status has been retrieved successfully.
            pass
        if channel is not None:
            try:
                channel.close()
            except Exception:
                # Cleanup is best-effort. Once ``result`` exists, a close error
                # cannot erase the already-known remote-command outcome.
                pass
        if result is None:
            raise RemoteCommandOutcomeUnknown(
                "remote command outcome is unknown after dispatch"
            ) from None
        return result

    def _require_client(self) -> paramiko.SSHClient:
        if self._client is None:
            raise RemoteExecutorError("remote executor is not connected")
        return self._client

    def _require_sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise RemoteExecutorError("remote executor is not connected")
        return self._sftp
