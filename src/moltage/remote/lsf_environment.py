"""Explicit, validated LSF client-environment rendering."""

import shlex

from moltage.domain.server_profile import (
    validate_lsf_env_directory,
    validate_lsf_library_directory,
    validate_lsf_server_directory,
    validate_slurm_bin_directory,
)


def render_lsf_client_command(
    command: str,
    *,
    env_directory: str,
    bin_directory: str,
    library_directory: str,
    server_directory: str,
) -> str:
    """Prefix a command with the complete validated LSF client environment."""

    if (
        not isinstance(command, str)
        or not command
        or "\x00" in command
        or "\n" in command
        or "\r" in command
    ):
        raise ValueError("LSF client command must be nonempty single-line text")
    env_directory = validate_lsf_env_directory(env_directory)
    bin_directory = validate_slurm_bin_directory(bin_directory)
    library_directory = validate_lsf_library_directory(library_directory)
    server_directory = validate_lsf_server_directory(server_directory)
    assignments = (
        f"LSF_ENVDIR={env_directory}",
        f"LSF_BINDIR={bin_directory}",
        f"LSF_LIBDIR={library_directory}",
        f"LSF_SERVERDIR={server_directory}",
    )
    rendered = " ".join(shlex.quote(item) for item in assignments)
    # IBM's profile.lsf also prepends these machine-dependent directories to
    # the process search paths.  Preserve (rather than replace) the remote
    # session values; all inserted directory text passed the shell-safe path
    # validators above.
    search_paths = (
        f'"PATH={bin_directory}:{server_directory}${{PATH:+:$PATH}}"',
        f'"LD_LIBRARY_PATH={library_directory}'
        '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"',
    )
    return f"env {rendered} {' '.join(search_paths)} {command}"
