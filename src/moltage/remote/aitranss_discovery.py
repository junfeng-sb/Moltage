"""Read-only lookup in a profile-configured Step-4 AITRANSS environment."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import shlex

from moltage.aitranss.runtime import (
    is_aitranss_executable_name,
)
from moltage.domain.server_profile import AitranssRuntimeConfiguration
from moltage.remote.executor import RemoteExecutor
from moltage.remote.runtime_environment import (
    RuntimeConfigurationError,
    verify_configured_runtime,
)


AITRANSS_MARKER = "__MOLTAGE_AITRANSS__="
AITRANSS_RESOLVED_MARKER = "__MOLTAGE_AITRANSS_RESOLVED__="


class AitranssDiscoveryError(RuntimeError):
    """Raised when the configured Step-4 environment is not still verified."""


@dataclass(frozen=True, slots=True)
class AitranssDiscoveryResult:
    executable_path: str
    command_v_path: str
    tcontrol_utility_path: str | None = None


def discover_aitranss_executable(
    executor: RemoteExecutor,
    runtime: AitranssRuntimeConfiguration,
) -> AitranssDiscoveryResult:
    """Reverify the configured AITRANSS command without ever running it."""

    if not isinstance(runtime, AitranssRuntimeConfiguration):
        raise TypeError(
            "AITRANSS discovery requires a configured per-profile runtime"
        )

    if runtime.environment is not None:
        try:
            verify_configured_runtime(executor, runtime.executable_path, runtime.environment)
        except RuntimeConfigurationError as error:
            raise AitranssDiscoveryError(str(error)) from None
        return AitranssDiscoveryResult(runtime.executable_path, runtime.executable_path)

    executable_name = PurePosixPath(runtime.executable_path).name
    if not is_aitranss_executable_name(executable_name):
        raise AitranssDiscoveryError(
            "the configured AITRANSS executable name is unsupported"
        )

    commands = ["module purge"]
    commands.extend(
        "module load " + shlex.quote(module) for module in runtime.modules
    )
    commands.extend([
        "candidate=$(command -v "
        + shlex.quote(executable_name)
        + " 2>/dev/null || true)",
        'resolved=""',
        'if [ -n "$candidate" ]; then '
        'resolved=$(readlink -f -- "$candidate" 2>/dev/null || true); fi',
        f"printf '{AITRANSS_MARKER}%s\\n' \"$candidate\"",
        f"printf '{AITRANSS_RESOLVED_MARKER}%s\\n' \"$resolved\"",
    ])
    result = executor.execute("bash -lc " + shlex.quote("; ".join(commands)))
    if result.exit_status != 0:
        raise AitranssDiscoveryError(
            "AITRANSS executable lookup failed in the configured module environment"
        )
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeError:
        raise AitranssDiscoveryError(
            "AITRANSS executable lookup returned invalid UTF-8"
        ) from None
    command_v_path = _one_marked_path(
        text,
        AITRANSS_MARKER,
        executable_name,
    )
    executable = _one_marked_path(
        text,
        AITRANSS_RESOLVED_MARKER,
        executable_name,
    )
    if command_v_path is None or executable is None:
        raise AitranssDiscoveryError(
            "AITRANSS executable is unavailable: command -v "
            f"{executable_name} returned no resolvable path in the "
            "configured module environment"
        )
    if executable != runtime.executable_path:
        raise AitranssDiscoveryError(
            "the configured AITRANSS executable path is stale; run server "
            "runtime discovery again in Cluster Execution Settings"
        )
    check = executor.execute(
        "test -f "
        + shlex.quote(executable)
        + " && test -x "
        + shlex.quote(executable)
    )
    if check.exit_status != 0:
        raise AitranssDiscoveryError(
            "the resolved AITRANSS path is not a regular executable file"
        )
    return AitranssDiscoveryResult(executable, command_v_path)


def _one_marked_path(
    text: str,
    marker: str,
    basename: str,
) -> str | None:
    values = tuple(
        line[len(marker) :]
        for line in text.splitlines()
        if line.startswith(marker)
    )
    if len(values) != 1:
        raise AitranssDiscoveryError("AITRANSS lookup markers are ambiguous")
    if not values[0]:
        return None
    path = PurePosixPath(values[0])
    if not path.is_absolute() or path.name != basename or str(path) != values[0]:
        raise AitranssDiscoveryError(
            f"discovered {basename} path is not a canonical absolute path"
        )
    return values[0]
