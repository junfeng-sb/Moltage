"""Bounded, site-neutral ORCA runtime validation and Slurm discovery."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import shlex

from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    RuntimeLocation,
    ServerProfileValidationError,
    normalize_module_name,
    validate_remote_executable_path,
)
from moltage.orca.catalog import OrcaVersionEvidence, parse_orca_version_evidence
from moltage.remote.runtime_environment import runtime_environment_commands


MAX_ORCA_MODULE_CANDIDATES = 32
_PATH_MARKER = "__MOLTAGE_ORCA_PATH__="
_CANONICAL_MARKER = "__MOLTAGE_ORCA_CANONICAL__="
_CONFIGURED_MARKER = "__MOLTAGE_ORCA_CONFIGURED__="
_MODULE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]*")


class OrcaRuntimeError(RuntimeError):
    """Raised when ORCA runtime evidence is missing or contradictory."""


@dataclass(frozen=True, slots=True)
class OrcaRuntimeCandidate:
    runtime: OrcaRuntimeConfiguration
    source_label: str


@dataclass(frozen=True, slots=True)
class OrcaDiscoveryResult:
    candidates: tuple[OrcaRuntimeCandidate, ...]
    diagnostics: tuple[str, ...] = ()


def validate_orca_runtime(
    executor,
    executable_path: str,
    environment: RuntimeEnvironment,
    *,
    detection_source: str = "manual",
) -> OrcaRuntimeConfiguration:
    """Validate one exact path/environment without running a calculation."""

    try:
        configured = validate_remote_executable_path(executable_path, "ORCA executable")
    except ServerProfileValidationError as error:
        raise OrcaRuntimeError(str(error)) from None
    if PurePosixPath(configured).name != "orca":
        raise OrcaRuntimeError("ORCA executable path must name orca")
    if not isinstance(environment, RuntimeEnvironment) or environment.mode is RuntimeEnvironmentMode.AUTO:
        raise OrcaRuntimeError("Select None, Modules, or Setup Script before validating ORCA")
    setup = runtime_environment_commands(environment)
    quoted = shlex.quote(configured)
    body = (
        *setup,
        f"test -f {quoted} && test -r {quoted} && test -x {quoted} || exit 31",
        f"configured=$(readlink -f -- {quoted}) || exit 32",
        'resolved=$(command -v orca 2>/dev/null) || exit 33',
        'resolved=$(readlink -f -- "$resolved") || exit 34',
        f'printf "{_CONFIGURED_MARKER}%s\\n{_PATH_MARKER}%s\\n" "$configured" "$resolved"',
    )
    result = executor.execute("bash -lc " + shlex.quote("; ".join(body)))
    if result.exit_status != 0:
        reasons = {
            31: "Configured ORCA path is not a readable regular executable",
            32: "Configured ORCA path cannot be resolved canonically",
            33: "The selected environment does not expose orca on PATH",
            34: "The environment ORCA path cannot be resolved canonically",
        }
        raise OrcaRuntimeError(reasons.get(result.exit_status, "ORCA environment validation failed"))
    markers = _markers(result.stdout)
    canonical = markers.get(_CONFIGURED_MARKER)
    resolved = markers.get(_PATH_MARKER)
    if canonical != configured:
        raise OrcaRuntimeError(
            f"Configured ORCA path is not canonical; resolved path is {canonical or 'unavailable'}"
        )
    if resolved != configured:
        raise OrcaRuntimeError(
            "The selected environment resolves orca to a different executable: "
            f"{resolved or 'unavailable'}"
        )
    version_result = executor.execute(
        "bash -lc " + shlex.quote("; ".join((*setup, f"{quoted} --version")))
    )
    combined = version_result.stdout + b"\n" + version_result.stderr
    evidence = parse_orca_version_evidence(combined, detection_source=detection_source)
    return OrcaRuntimeConfiguration(configured, environment, evidence)


def discover_orca_runtimes(
    executor,
    *,
    scheduler_kind: SchedulerKind,
    configured_location: RuntimeLocation | None = None,
) -> OrcaDiscoveryResult:
    """Discover ORCA only through login/configured PATH and bounded modules."""

    if SchedulerKind(scheduler_kind) is not SchedulerKind.SLURM:
        raise OrcaRuntimeError(
            "Automatic ORCA discovery is currently supported only for Slurm profiles"
        )
    diagnostics: list[str] = []
    candidates: list[OrcaRuntimeCandidate] = []
    environments: list[tuple[str, RuntimeEnvironment]] = [
        ("login PATH", RuntimeEnvironment(RuntimeEnvironmentMode.NONE))
    ]
    if configured_location is not None:
        environment = configured_location.environment
        if environment.mode is not RuntimeEnvironmentMode.AUTO:
            environments.append(("configured environment", environment))
    for label, environment in environments:
        candidate = _candidate_from_environment(executor, environment, label, diagnostics)
        _append_candidate(candidates, candidate)

    module_names = _module_candidates(executor, diagnostics)
    for module_name in module_names:
        environment = RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, (module_name,))
        candidate = _candidate_from_environment(
            executor,
            environment,
            f"module {module_name}",
            diagnostics,
        )
        _append_candidate(candidates, candidate)
    return OrcaDiscoveryResult(tuple(candidates), tuple(dict.fromkeys(diagnostics)))


def require_usable_orca_runtime(executor, runtime: OrcaRuntimeConfiguration) -> None:
    """Revalidate persisted runtime identity before any remote mutation."""

    current = validate_orca_runtime(
        executor,
        runtime.executable_path,
        runtime.environment,
        detection_source="submission preflight",
    )
    expected = runtime.version_evidence
    actual = current.version_evidence
    if expected.version != actual.version or expected.version_family != actual.version_family:
        raise OrcaRuntimeError(
            "Configured ORCA version evidence changed; validate the server profile again"
        )
    if actual.version is not None and actual.version_family is None:
        raise OrcaRuntimeError(
            f"ORCA {actual.version} is outside the reviewed 5.0.x, 6.0.x, and 6.1.x families"
        )


def _candidate_from_environment(executor, environment, label, diagnostics):
    setup = runtime_environment_commands(environment)
    result = executor.execute(
        "bash -lc "
        + shlex.quote(
            "; ".join(
                (
                    *setup,
                    'candidate=$(command -v orca 2>/dev/null) || exit 41',
                    'candidate=$(readlink -f -- "$candidate") || exit 42',
                    'test -f "$candidate" && test -r "$candidate" && test -x "$candidate" || exit 43',
                    f'printf "{_CANONICAL_MARKER}%s\\n" "$candidate"',
                )
            )
        )
    )
    if result.exit_status != 0:
        diagnostics.append(f"{label}: no verified ORCA executable")
        return None
    path = _markers(result.stdout).get(_CANONICAL_MARKER)
    if path is None:
        diagnostics.append(f"{label}: ORCA identity evidence was incomplete")
        return None
    try:
        path = validate_remote_executable_path(path, "Discovered ORCA executable")
        runtime = validate_orca_runtime(
            executor,
            path,
            environment,
            detection_source=label,
        )
    except (ServerProfileValidationError, OrcaRuntimeError) as error:
        diagnostics.append(f"{label}: {error}")
        return None
    return OrcaRuntimeCandidate(runtime, label)


def _module_candidates(executor, diagnostics):
    result = executor.execute(
        "bash -lc "
        + shlex.quote(
            "module purge >/dev/null 2>&1 || exit $?; "
            "export LMOD_COLORIZE=no MODULES_COLOR=never; "
            "module -t avail orca 2>&1"
        )
    )
    if result.exit_status != 0:
        diagnostics.append("ORCA module catalog query was unavailable")
        return ()
    try:
        text = (result.stdout + b"\n" + result.stderr).decode("utf-8")
    except UnicodeDecodeError:
        diagnostics.append("ORCA module catalog returned invalid UTF-8")
        return ()
    names: list[str] = []
    for line in text.splitlines():
        for raw in line.strip().split():
            name = raw.rstrip(":").replace("(default)", "")
            if "orca" not in name.casefold() or _MODULE_NAME.fullmatch(name) is None:
                continue
            try:
                name = normalize_module_name(name)
            except ServerProfileValidationError:
                continue
            if name not in names:
                names.append(name)
            if len(names) >= MAX_ORCA_MODULE_CANDIDATES:
                diagnostics.append(
                    f"ORCA module candidates were limited to {MAX_ORCA_MODULE_CANDIDATES}"
                )
                return tuple(names)
    return tuple(names)


def _append_candidate(candidates, candidate):
    if candidate is None:
        return
    identity = (
        candidate.runtime.executable_path,
        candidate.runtime.environment.mode,
        candidate.runtime.environment.modules,
        candidate.runtime.environment.setup_script,
    )
    if all(
        (
            item.runtime.executable_path,
            item.runtime.environment.mode,
            item.runtime.environment.modules,
            item.runtime.environment.setup_script,
        )
        != identity
        for item in candidates
    ):
        candidates.append(candidate)


def _markers(output: bytes) -> dict[str, str]:
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        raise OrcaRuntimeError("ORCA validation returned invalid UTF-8") from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        for marker in (_PATH_MARKER, _CANONICAL_MARKER, _CONFIGURED_MARKER):
            if line.startswith(marker):
                values[marker] = line[len(marker):].strip()
    return values
