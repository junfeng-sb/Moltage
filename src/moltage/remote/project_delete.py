"""One exact, at-most-once managed-project directory deletion boundary."""

from pathlib import PurePosixPath
import shlex

from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteExecutor,
    RemoteExecutorError,
)


class RemoteProjectDeletionError(RuntimeError):
    """Raised for a known refusal or known unsuccessful deletion."""


class RemoteProjectDeletionOutcomeUnknown(RemoteExecutorError):
    """Raised after deletion dispatch when absence cannot be established."""


def verify_remote_project_root_for_deletion(
    executor: RemoteExecutor,
    remote_project_path: str,
) -> None:
    """Reject a missing, non-directory, or symlink project root read-only."""

    target = _validated_target(remote_project_path)
    result = executor.execute(
        f"test -d {shlex.quote(target)} && test ! -L {shlex.quote(target)}"
    )
    if result.exit_status != 0:
        raise RemoteProjectDeletionError(
            "The exact server project root is missing, is not a directory, or is a symlink."
        )


def delete_remote_project_once(
    executor: RemoteExecutor,
    remote_project_path: str,
) -> None:
    """Delete one validated root once, then confirm that its lexical path is absent."""

    target = _validated_target(remote_project_path)
    quoted = shlex.quote(target)
    destructive_command = (
        f"test -d {quoted} && test ! -L {quoted} && rm -rf -- {quoted}"
    )
    try:
        result = executor.execute(destructive_command)
    except RemoteCommandOutcomeUnknown:
        raise RemoteProjectDeletionOutcomeUnknown(
            "Deletion outcome unknown. Reconnect and Refresh."
        ) from None
    if result.exit_status != 0:
        raise RemoteProjectDeletionError(
            "The server refused permanent deletion of the exact project directory."
        )

    verification_command = f"test ! -e {quoted} && test ! -L {quoted}"
    try:
        verification = executor.execute(verification_command)
    except RemoteExecutorError:
        raise RemoteProjectDeletionOutcomeUnknown(
            "Deletion outcome unknown. Reconnect and Refresh."
        ) from None
    if verification.exit_status != 0:
        raise RemoteProjectDeletionError(
            "The server project directory still exists after the deletion request."
        )


def _validated_target(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("remote project deletion target must be nonempty NUL-free text")
    if "\n" in value or "\r" in value or not value.startswith("/"):
        raise ValueError("remote project deletion target must be one absolute POSIX path")
    path = PurePosixPath(value)
    if str(path) != value or value == "/" or not path.name or ".." in path.parts:
        raise ValueError("remote project deletion target is unsafe")
    return value
