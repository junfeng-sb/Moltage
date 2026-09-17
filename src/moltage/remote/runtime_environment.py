"""Render only explicitly configured runtime setup and launcher operations."""

import shlex

from moltage.domain.server_profile import (
    FhiAimsRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    SlurmAitranssLaunchMode,
    runtime_launcher_kind,
    validate_srun_launcher_path,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.remote.executor import RemotePathNotFoundError


class RuntimeConfigurationError(ValueError):
    """An explicitly configured runtime is unavailable; not a Slurm absence."""


def verify_srun_launcher(executor, path: str) -> str:
    """Verify one exact absolute ``srun`` candidate without PATH lookup or scan."""

    try:
        normalized = validate_srun_launcher_path(path)
    except ValueError as error:
        raise RuntimeConfigurationError(str(error)) from None
    quoted = shlex.quote(normalized)
    result = executor.execute(
        f"test -f {quoted} && test -r {quoted} && test -x {quoted}"
    )
    if result.exit_status != 0:
        raise RuntimeConfigurationError(
            f"Configured AITRANSS srun executable is unavailable: {normalized}"
        )
    return normalized


def verify_aitranss_launch_for_submission(executor, preset) -> None:
    """Fail closed unless the selected Slurm Step-4 launch policy is usable."""

    if preset.scheduler_kind is SchedulerKind.LSF:
        return
    mode = preset.slurm_aitranss_launch_mode
    if mode is SlurmAitranssLaunchMode.DIRECT:
        return
    if mode is SlurmAitranssLaunchMode.SRUN:
        if preset.slurm_aitranss_srun_path is None:
            raise RuntimeConfigurationError(
                "Slurm Step 4 srun mode requires a verified absolute srun executable"
            )
        verify_srun_launcher(executor, preset.slurm_aitranss_srun_path)
        return
    raise RuntimeConfigurationError(
        "Select direct or srun for Slurm Step 4 before submitting"
    )


def verify_configured_runtime(executor, executable_path, environment, launcher_path=None):
    """Check explicit files before submission, without running either program."""
    paths = (executable_path,) if launcher_path is None else (executable_path, launcher_path)
    commands = list(runtime_environment_commands(environment))
    for path in paths:
        quoted = shlex.quote(path)
        commands.append(f"test -f {quoted} && test -r {quoted} && test -x {quoted} || exit 3")
    commands.append("printf '__AT_RUNTIME_AVAILABLE__\\n'")
    result = executor.execute("bash -lc " + shlex.quote("; ".join(commands)))
    if result.exit_status != 0 or b"__AT_RUNTIME_AVAILABLE__" not in result.stdout.splitlines():
        raise RuntimeConfigurationError("Configured runtime file, launcher or environment is unavailable")
    try:
        magic = executor.read_file_head(executable_path, 4)
    except RemotePathNotFoundError:
        raise RuntimeConfigurationError("Configured executable no longer exists") from None
    if magic != b"\x7fELF":
        raise RuntimeConfigurationError("Select the actual Linux calculation binary, not a startup script")


def verify_configured_fhi_runtime(executor, preset):
    if preset.fhi_runtime is not None:
        runtime = preset.fhi_runtime
        verify_configured_runtime(executor, runtime.executable_path,
                                  runtime.environment, runtime.launcher_path)


def runtime_environment_commands(environment: RuntimeEnvironment) -> tuple[str, ...]:
    """Setup scripts here are user-selected execution, never discovered scripts."""

    if environment.mode is RuntimeEnvironmentMode.NONE:
        return ()
    if environment.mode is RuntimeEnvironmentMode.MODULES:
        return (
            'module purge || exit "$?"',
            *(f'module load {shlex.quote(module)} || exit "$?"' for module in environment.modules),
        )
    if environment.mode is RuntimeEnvironmentMode.SCRIPT:
        return (f'source {shlex.quote(environment.setup_script)} || exit "$?"',)
    raise ValueError("Runtime environment is Auto; resolve it before running a job")


def render_configured_fhi_launch(runtime: FhiAimsRuntimeConfiguration, ntasks: int) -> str:
    """Use exactly one foreground launcher; its exit status is the batch status."""

    if isinstance(ntasks, bool) or not isinstance(ntasks, int) or ntasks < 1:
        raise ValueError("MPI task count must be positive")
    launcher = shlex.quote(runtime.launcher_path)
    executable = shlex.quote(runtime.executable_path)
    if runtime_launcher_kind(runtime.launcher_path) == "srun":
        return f"exec {launcher} --kill-on-bad-exit=1 {executable}"
    # Do not apply srun flags, pipelines, backgrounding, or an outer allocation.
    # mpirun's MPI-specific process-abort behavior must be accepted on the server;
    # this line preserves its known return status without substituting MPI options.
    return f"exec {launcher} -n {ntasks} {executable}"
