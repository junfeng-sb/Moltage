"""Atomic upload and SHA256 verification for one calculation-step input set."""

from collections.abc import Callable, Mapping
import hashlib
from pathlib import PurePosixPath
import re

from moltage.remote.executor import (
    RemoteExecutor,
    RemoteExecutorError,
    RemotePathNotFoundError,
    RemoteRenameError,
)


STEP_INPUT_FILENAMES = ("geometry.in", "control.in", "submit.sh")


class StepInputTransferError(RuntimeError):
    """Raised when the exact step input set cannot be written atomically."""


class StepInputChecksumError(StepInputTransferError):
    """Raised when one final remote input differs from its local bytes."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(f"SHA256 verification failed for {filename}")


class NewRemoteFileConflictError(StepInputTransferError):
    """Raised before writes when a supposedly new remote file already exists."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(f"remote file already exists: {filename}")


def upload_step_inputs_atomically(
    executor: RemoteExecutor,
    remote_step_directory: str,
    files: Mapping[str, bytes],
    *,
    temporary_id_factory: Callable[[], str],
) -> None:
    """Write exactly three files through unique temporary paths and renames."""

    _validate_input_set(remote_step_directory, files)
    for filename, data in files.items():
        temporary_id = temporary_id_factory()
        if (
            not isinstance(temporary_id, str)
            or _TEMPORARY_ID.fullmatch(temporary_id) is None
        ):
            raise StepInputTransferError(
                "step-input temporary identity must be one safe path component"
            )
        destination = str(PurePosixPath(remote_step_directory) / filename)
        temporary = destination + ".tmp-" + temporary_id
        try:
            executor.write_bytes(temporary, data)
            executor.rename(temporary, destination)
        except RemoteRenameError as error:
            raise StepInputTransferError(
                f"temporary upload succeeded but atomic rename failed for "
                f"{filename}: {error}"
            ) from None
        except RemoteExecutorError as error:
            raise StepInputTransferError(
                f"remote upload failed for {filename}: {error}"
            ) from None


def verify_step_input_sha256(
    executor: RemoteExecutor,
    remote_step_directory: str,
    files: Mapping[str, bytes],
) -> tuple[tuple[str, str], ...]:
    """Read each final file and require its SHA256 to match local bytes."""

    _validate_input_set(remote_step_directory, files)
    hashes: list[tuple[str, str]] = []
    for filename, local_data in files.items():
        destination = str(PurePosixPath(remote_step_directory) / filename)
        local_hash = hashlib.sha256(local_data).hexdigest()
        try:
            remote_data = executor.read_bytes(destination)
        except RemoteExecutorError as error:
            raise StepInputTransferError(
                f"remote verification read failed for {filename}: {error}"
            ) from None
        if hashlib.sha256(remote_data).hexdigest() != local_hash:
            raise StepInputChecksumError(filename)
        hashes.append((filename, local_hash))
    return tuple(hashes)


def upload_new_files_atomically(
    executor: RemoteExecutor,
    remote_directory: str,
    files: Mapping[str, bytes],
    *,
    temporary_id_factory: Callable[[], str],
) -> tuple[tuple[str, str], ...]:
    """Preflight absence, upload named new files, and verify exact SHA256."""

    _validate_named_files(remote_directory, files)
    for filename in files:
        destination = str(PurePosixPath(remote_directory) / filename)
        try:
            executor.stat(destination)
        except RemotePathNotFoundError:
            continue
        except RemoteExecutorError:
            raise
        raise NewRemoteFileConflictError(filename)

    for filename, data in files.items():
        temporary_id = temporary_id_factory()
        if (
            not isinstance(temporary_id, str)
            or _TEMPORARY_ID.fullmatch(temporary_id) is None
        ):
            raise StepInputTransferError(
                "new-file temporary identity must be one safe path component"
            )
        destination = str(PurePosixPath(remote_directory) / filename)
        temporary = destination + ".tmp-" + temporary_id
        try:
            executor.write_bytes(temporary, data)
            executor.rename(temporary, destination)
        except RemoteRenameError as error:
            raise StepInputTransferError(
                f"temporary upload succeeded but atomic rename failed for "
                f"{filename}: {error}"
            ) from None
        except RemoteExecutorError:
            raise

    hashes: list[tuple[str, str]] = []
    for filename, data in files.items():
        destination = str(PurePosixPath(remote_directory) / filename)
        remote_data = executor.read_bytes(destination)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(remote_data).hexdigest() != digest:
            raise StepInputChecksumError(filename)
        hashes.append((filename, digest))
    return tuple(hashes)


def verify_existing_file_sha256(
    executor: RemoteExecutor,
    remote_directory: str,
    filename: str,
    expected_digest: str,
) -> None:
    """Require one immutable existing remote input to match its accepted hash."""

    _validate_named_files(remote_directory, {filename: b""})
    if re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise StepInputTransferError(f"accepted SHA256 is unavailable for {filename}")
    try:
        data = executor.read_bytes(str(PurePosixPath(remote_directory) / filename))
    except RemotePathNotFoundError:
        raise StepInputTransferError(f"accepted remote input is missing: {filename}") from None
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise StepInputChecksumError(filename)


def replace_existing_file_atomically(
    executor: RemoteExecutor,
    remote_directory: str,
    filename: str,
    replacement: bytes,
    *,
    expected_existing_digest: str,
    temporary_id_factory: Callable[[], str],
) -> str:
    """Verify one existing file, atomically replace it, and verify its new SHA256."""

    _validate_named_files(remote_directory, {filename: replacement})
    verify_existing_file_sha256(
        executor,
        remote_directory,
        filename,
        expected_existing_digest,
    )
    temporary_id = temporary_id_factory()
    if (
        not isinstance(temporary_id, str)
        or _TEMPORARY_ID.fullmatch(temporary_id) is None
    ):
        raise StepInputTransferError(
            "replacement temporary identity must be one safe path component"
        )
    destination = str(PurePosixPath(remote_directory) / filename)
    temporary = destination + ".tmp-" + temporary_id
    try:
        executor.write_bytes(temporary, replacement)
        executor.rename(temporary, destination)
    except RemoteRenameError as error:
        raise StepInputTransferError(
            f"temporary upload succeeded but atomic rename failed for {filename}: "
            f"{error}"
        ) from None
    except RemoteExecutorError:
        raise
    digest = hashlib.sha256(replacement).hexdigest()
    remote_data = executor.read_bytes(destination)
    if hashlib.sha256(remote_data).hexdigest() != digest:
        raise StepInputChecksumError(filename)
    return digest


def _validate_named_files(
    remote_directory: str,
    files: Mapping[str, bytes],
) -> None:
    if (
        not isinstance(remote_directory, str)
        or not remote_directory.startswith("/")
        or str(PurePosixPath(remote_directory)) != remote_directory
    ):
        raise StepInputTransferError(
            "remote directory must be an absolute normalized POSIX path"
        )
    if not files:
        raise StepInputTransferError("at least one remote file is required")
    for filename, data in files.items():
        if (
            not isinstance(filename, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename) is None
        ):
            raise StepInputTransferError("remote filename must be one safe component")
        if not isinstance(data, bytes):
            raise StepInputTransferError(f"{filename} upload data must be bytes")


def _validate_input_set(
    remote_step_directory: str,
    files: Mapping[str, bytes],
) -> None:
    if tuple(files) != STEP_INPUT_FILENAMES:
        raise StepInputTransferError(
            "step upload requires exactly geometry.in, control.in, and submit.sh "
            "in that order"
        )
    if (
        not isinstance(remote_step_directory, str)
        or not remote_step_directory.startswith("/")
        or str(PurePosixPath(remote_step_directory)) != remote_step_directory
    ):
        raise StepInputTransferError(
            "remote step directory must be an absolute normalized POSIX path"
        )
    for filename, data in files.items():
        if not isinstance(data, bytes):
            raise StepInputTransferError(f"{filename} upload data must be bytes")


_TEMPORARY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
