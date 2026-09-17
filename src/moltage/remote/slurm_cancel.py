"""One-shot exact-job Slurm cancellation through a verified absolute path."""

import shlex
from pathlib import PurePosixPath

from moltage.remote.executor import RemoteCommandResult, RemoteExecutor
from moltage.remote.slurm_discovery import (
    validate_scheduler_command_path,
    validate_slurm_command_path,
)
from moltage.remote.slurm_status import validate_job_id
from moltage.remote.lsf_environment import render_lsf_client_command


class SlurmCancellationError(RuntimeError):
    """Base error for a definite, safely classified cancellation failure."""


class SlurmCancellationRejected(SlurmCancellationError):
    """Raised when scancel returns a known normal nonzero result."""


def build_scancel_command(
    scancel_path: str,
    job_id: str,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> str:
    """Build one exact decimal-job cancellation command without selectors."""

    command_name = PurePosixPath(scancel_path).name
    if command_name == "scancel":
        executable = validate_slurm_command_path(scancel_path, "scancel")
    elif command_name == "bkill":
        executable = validate_scheduler_command_path(scancel_path, "bkill")
    else:
        raise SlurmCancellationError(
            "scheduler cancel command must end in /scancel or /bkill"
        )
    normalized_job_id = validate_job_id(job_id)
    command = f"{shlex.quote(executable)} {normalized_job_id}"
    if command_name == "bkill":
        if any(
            value is None
            for value in (
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            )
        ):
            raise SlurmCancellationError(
                "LSF cancellation requires the configuration, library, and "
                "server directories"
            )
        try:
            return render_lsf_client_command(
                command,
                env_directory=lsf_env_directory,
                bin_directory=str(PurePosixPath(executable).parent),
                library_directory=lsf_library_directory,
                server_directory=lsf_server_directory,
            )
        except (TypeError, ValueError) as error:
            raise SlurmCancellationError(str(error)) from None
    if any(
        value is not None
        for value in (
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    ):
        raise SlurmCancellationError(
            "LSF client directories cannot be applied to scancel"
        )
    return command


def request_slurm_cancellation_once(
    executor: RemoteExecutor,
    *,
    scancel_path: str,
    job_id: str,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> None:
    """Dispatch one exact scancel/bkill request; never retry ambiguity."""

    result = executor.execute(
        build_scancel_command(
            scancel_path,
            job_id,
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    )
    if not isinstance(result, RemoteCommandResult):
        raise SlurmCancellationError(
            "scheduler cancellation returned an invalid command result"
        )
    if result.exit_status != 0:
        raise SlurmCancellationRejected(
            f"Scheduler rejected cancellation of Job {job_id}."
        )
