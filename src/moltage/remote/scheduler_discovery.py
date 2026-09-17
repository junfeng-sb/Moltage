"""Bounded, shared discovery for the supported Slurm and LSF schedulers."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import shlex

from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.server_profile import (
    SlurmCommandMode,
    SlurmExecutionPreset,
    validate_lsf_env_directory,
    validate_lsf_library_directory,
    validate_lsf_server_directory,
    validate_slurm_bin_directory,
)
from moltage.remote.executor import RemoteExecutor
from moltage.remote.lsf_environment import render_lsf_client_command
from moltage.remote.slurm_discovery import (
    SlurmConfigurationError,
    SlurmDiscoveryError,
    SlurmDiscoveryResult,
    SlurmDiscoverySource,
    SlurmVerificationError,
    _resolve_slurm_for_submission_only,
    validate_scheduler_command_path,
    verify_sbatch_path,
)


class SchedulerAmbiguityError(SlurmDiscoveryError):
    """Raised when both complete scheduler command sets are visible."""


_MARKER = "__MOLTAGE_SCHEDULER__="
_LSF_ENV_MARKER = "__MOLTAGE_LSF_ENVDIR__="
_LSF_ATTEMPT_MARKER = "__MOLTAGE_LSF_ATTEMPT__="
_LOOKUP_INNER = (
    "for command_name in sbatch bsub; do "
    "candidate=$(command -v \"$command_name\" 2>/dev/null || true); "
    "if [ -n \"$candidate\" ]; then "
    f"printf '{_MARKER}%s|%s|%s|%s|%s|%s|%s\\n' "
    '"$command_name" "$candidate" "${LSF_ENVDIR-}" "${LSF_CONFDIR-}" '
    '"${LSF_BINDIR-}" "${LSF_LIBDIR-}" "${LSF_SERVERDIR-}"; '
    "fi; done; "
    "if ! command -v bsub >/dev/null 2>&1; then "
    "for candidate in "
    '"${LSF_BINDIR:+$LSF_BINDIR/bsub}" '
    '"${LSF_SERVERDIR:+${LSF_SERVERDIR%/*}/bin/bsub}"; do '
    "if [ -n \"$candidate\" ] && [ -x \"$candidate\" ]; then "
    f"printf '{_MARKER}%s|%s|%s|%s|%s|%s|%s\\n' bsub \"$candidate\" "
    '"${LSF_ENVDIR-}" "${LSF_CONFDIR-}" "${LSF_BINDIR-}" '
    '"${LSF_LIBDIR-}" "${LSF_SERVERDIR-}"; '
    "break; fi; done; fi"
)
CURRENT_ENVIRONMENT_DISCOVERY_COMMAND = _LOOKUP_INNER
LOGIN_SHELL_DISCOVERY_COMMAND = "bash -lc " + shlex.quote(_LOOKUP_INNER)


def discover_scheduler(
    executor: RemoteExecutor,
    preferred_kind: SchedulerKind | None = None,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> SlurmDiscoveryResult:
    """Discover a complete command set, using at most one login-shell fallback."""

    allowed = (
        frozenset(SchedulerKind)
        if preferred_kind is None
        else frozenset({SchedulerKind(preferred_kind)})
    )
    rejected_candidates: list[str] = []
    for command, source in (
        (
            CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
            SlurmDiscoverySource.CURRENT_ENVIRONMENT,
        ),
        (LOGIN_SHELL_DISCOVERY_COMMAND, SlurmDiscoverySource.LOGIN_SHELL),
    ):
        result = executor.execute(command)
        candidates = _parse_lookup_result(result, allowed)
        verified: list[SlurmDiscoveryResult] = []
        for (
            kind,
            path,
            advertised_env,
            advertised_conf,
            advertised_bin,
            advertised_library,
            advertised_server,
        ) in candidates:
            try:
                verified.append(
                    verify_scheduler_submit_path(
                        executor,
                        path,
                        kind,
                        source,
                        lsf_env_directory=lsf_env_directory,
                        lsf_library_directory=lsf_library_directory,
                        lsf_server_directory=lsf_server_directory,
                        advertised_lsf_env_directory=advertised_env,
                        advertised_lsf_conf_directory=advertised_conf,
                        advertised_lsf_bin_directory=advertised_bin,
                        advertised_lsf_library_directory=advertised_library,
                        advertised_lsf_server_directory=advertised_server,
                    )
                )
            except SlurmVerificationError as error:
                detail = (
                    f"{scheduler_display_name(kind)} candidate {path}: {error}"
                )
                if detail not in rejected_candidates:
                    rejected_candidates.append(detail)
                continue
        if len(verified) == 1:
            return verified[0]
        if len(verified) > 1:
            raise SchedulerAmbiguityError(
                "Both complete Slurm and LSF command sets are available. "
                "Select the scheduler type and command directory manually."
            )

    requested = (
        scheduler_display_name(next(iter(allowed)))
        if len(allowed) == 1
        else "Slurm or LSF"
    )
    if rejected_candidates:
        raise SlurmDiscoveryError(
            f"{requested} command candidates were found but failed verification: "
            + "; ".join(rejected_candidates[:4])
        )
    raise SlurmDiscoveryError(
        f"{requested} could not be detected automatically in the remote "
        "environment or its login shell."
    )


def resolve_scheduler_for_submission(
    executor: RemoteExecutor,
    preset: SlurmExecutionPreset,
) -> SlurmDiscoveryResult:
    """Resolve exactly the scheduler declared by the persisted preset."""

    if not isinstance(preset, SlurmExecutionPreset):
        raise TypeError("Scheduler preflight requires a SlurmExecutionPreset")
    if preset.scheduler_kind is SchedulerKind.SLURM:
        return _resolve_slurm_for_submission_only(executor, preset)

    directory = preset.slurm_bin_directory
    if preset.slurm_command_mode is SlurmCommandMode.MANUAL:
        if directory is None:
            raise SlurmConfigurationError(
                "", "Manual LSF command mode requires a command directory."
            )
        if any(
            value is None
            for value in (
                preset.lsf_env_directory,
                preset.lsf_library_directory,
                preset.lsf_server_directory,
            )
        ):
            raise SlurmConfigurationError(
                directory,
                "Manual LSF command mode requires the configuration, library, "
                "and server directories.",
            )
        try:
            return verify_scheduler_directory(
                executor,
                directory,
                SchedulerKind.LSF,
                SlurmDiscoverySource.MANUAL,
                lsf_env_directory=preset.lsf_env_directory,
                lsf_library_directory=preset.lsf_library_directory,
                lsf_server_directory=preset.lsf_server_directory,
            )
        except SlurmVerificationError as error:
            raise SlurmConfigurationError(
                directory,
                f"The configured LSF command directory is invalid: {directory}. "
                f"{error}",
            ) from None

    if directory is not None:
        try:
            return verify_scheduler_directory(
                executor,
                directory,
                SchedulerKind.LSF,
                SlurmDiscoverySource.CACHED_AUTOMATIC,
                lsf_env_directory=preset.lsf_env_directory,
                lsf_library_directory=preset.lsf_library_directory,
                lsf_server_directory=preset.lsf_server_directory,
            )
        except SlurmVerificationError:
            pass
    return discover_scheduler(
        executor,
        SchedulerKind.LSF,
        lsf_env_directory=preset.lsf_env_directory,
        lsf_library_directory=preset.lsf_library_directory,
        lsf_server_directory=preset.lsf_server_directory,
    )


def verify_scheduler_directory(
    executor: RemoteExecutor,
    bin_directory: str,
    scheduler_kind: SchedulerKind,
    discovery_source: SlurmDiscoverySource = SlurmDiscoverySource.MANUAL,
    *,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> SlurmDiscoveryResult:
    """Verify one explicit command directory without searching elsewhere."""

    directory = validate_slurm_bin_directory(bin_directory)
    kind = SchedulerKind(scheduler_kind)
    if (
        kind is SchedulerKind.LSF
        and SlurmDiscoverySource(discovery_source) is SlurmDiscoverySource.MANUAL
        and any(
            value is None
            for value in (
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            )
        )
    ):
        raise SlurmVerificationError(
            "Manual LSF verification requires the configuration, library, "
            "and server directories."
        )
    command_name = "sbatch" if kind is SchedulerKind.SLURM else "bsub"
    return verify_scheduler_submit_path(
        executor,
        str(PurePosixPath(directory) / command_name),
        kind,
        discovery_source,
        lsf_env_directory=lsf_env_directory,
        lsf_library_directory=lsf_library_directory,
        lsf_server_directory=lsf_server_directory,
    )


def verify_scheduler_submit_path(
    executor: RemoteExecutor,
    candidate: str,
    scheduler_kind: SchedulerKind,
    discovery_source: SlurmDiscoverySource,
    *,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
    advertised_lsf_env_directory: str | None = None,
    advertised_lsf_conf_directory: str | None = None,
    advertised_lsf_bin_directory: str | None = None,
    advertised_lsf_library_directory: str | None = None,
    advertised_lsf_server_directory: str | None = None,
) -> SlurmDiscoveryResult:
    """Verify the complete command set and the supported query interface."""

    kind = SchedulerKind(scheduler_kind)
    if kind is SchedulerKind.SLURM:
        result = verify_sbatch_path(executor, candidate, discovery_source)
        directory = result.bin_directory
        paths = tuple(
            validate_scheduler_command_path(
                str(PurePosixPath(directory) / command_name), command_name
            )
            for command_name in ("squeue", "sacct", "scancel")
        )
        check = executor.execute(
            " && ".join(f"test -x {shlex.quote(path)}" for path in paths)
        )
        if check.exit_status != 0:
            raise SlurmVerificationError(
                "The Slurm command set is incomplete; squeue, sacct, and "
                "scancel are all required."
            )
        return result
    return _verify_lsf_submit_path(
        executor,
        candidate,
        discovery_source,
        lsf_env_directory=lsf_env_directory,
        lsf_library_directory=lsf_library_directory,
        lsf_server_directory=lsf_server_directory,
        advertised_lsf_env_directory=advertised_lsf_env_directory,
        advertised_lsf_conf_directory=advertised_lsf_conf_directory,
        advertised_lsf_bin_directory=advertised_lsf_bin_directory,
        advertised_lsf_library_directory=advertised_lsf_library_directory,
        advertised_lsf_server_directory=advertised_lsf_server_directory,
    )


def _verify_lsf_submit_path(
    executor: RemoteExecutor,
    candidate: str,
    discovery_source: SlurmDiscoverySource,
    *,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
    advertised_lsf_env_directory: str | None = None,
    advertised_lsf_conf_directory: str | None = None,
    advertised_lsf_bin_directory: str | None = None,
    advertised_lsf_library_directory: str | None = None,
    advertised_lsf_server_directory: str | None = None,
) -> SlurmDiscoveryResult:
    bsub_path = validate_scheduler_command_path(candidate, "bsub")
    directory = str(PurePosixPath(bsub_path).parent)
    paths = {
        name: validate_scheduler_command_path(
            str(PurePosixPath(directory) / name), name
        )
        for name in ("bsub", "bjobs", "bhist", "bkill", "lsid")
    }
    client_directories = _lsf_client_directory_candidates(
        bsub_path,
        explicit_env=lsf_env_directory,
        explicit_library=lsf_library_directory,
        explicit_server=lsf_server_directory,
        advertised_env=advertised_lsf_env_directory,
        advertised_conf=advertised_lsf_conf_directory,
        advertised_bin=advertised_lsf_bin_directory,
        advertised_library=advertised_lsf_library_directory,
        advertised_server=advertised_lsf_server_directory,
    )
    checks = " && ".join(
        f"test -x {shlex.quote(path)}" for path in paths.values()
    )
    attempts = " ".join(
        (
            f"if test -r {shlex.quote(str(PurePosixPath(item.env_directory) / 'lsf.conf'))} "
            f"&& test -d {shlex.quote(item.library_directory)} "
            f"&& test -r {shlex.quote(item.library_directory)} "
            f"&& test -d {shlex.quote(item.server_directory)} "
            f"&& test -x {shlex.quote(item.server_directory)}; then "
            "lsf_layout_found=1; "
            f"printf '\\n{_LSF_ATTEMPT_MARKER}%s|%s|%s\\n' "
            f"{shlex.quote(item.env_directory)} "
            f"{shlex.quote(item.library_directory)} "
            f"{shlex.quote(item.server_directory)}; "
            f"if {render_lsf_client_command(shlex.quote(paths['lsid']), env_directory=item.env_directory, bin_directory=directory, library_directory=item.library_directory, server_directory=item.server_directory)}; "
            f"then printf '\\n{_LSF_ENV_MARKER}%s|%s|%s\\n' "
            f"{shlex.quote(item.env_directory)} "
            f"{shlex.quote(item.library_directory)} "
            f"{shlex.quote(item.server_directory)}; "
            "exit 0; fi; fi;"
        )
        for item in client_directories
    )
    result = executor.execute(
        f"if ! ( {checks} ); then exit 125; fi; "
        f"lsf_layout_found=0; {attempts} "
        "if [ \"$lsf_layout_found\" -eq 0 ]; then exit 124; fi; exit 126"
    )
    if result.exit_status != 0:
        if result.exit_status == 125:
            message = (
                "The LSF command directory does not contain an executable "
                "bsub, bjobs, bhist, bkill, and lsid command set."
            )
        elif result.exit_status == 124:
            message = (
                "No complete LSF client-directory set was found in the bounded "
                "candidates: a readable lsf.conf plus readable library and "
                "executable server directories are required."
            )
        elif result.exit_status == 126:
            detail = _remote_error_excerpt(result)
            message = "lsid rejected every complete LSF client environment"
            message += f": {detail}" if detail else "."
        else:
            message = "LSF client verification returned an unsupported result."
        raise SlurmVerificationError(message)
    selected = _selected_lsf_directories(result.stdout)
    if selected is None:
        raise SlurmVerificationError(
            "LSF verification did not identify the client directories used by lsid."
        )
    version_text = _plausible_lsf_version(result.stdout, result.stderr)
    if version_text is None:
        raise SlurmVerificationError(
            f"{paths['lsid']} did not return a plausible LSF identity."
        )
    return SlurmDiscoveryResult(
        bsub_path,
        directory,
        version_text,
        SlurmDiscoverySource(discovery_source),
        SchedulerKind.LSF,
        selected.env_directory,
        selected.library_directory,
        selected.server_directory,
    )


def _parse_lookup_result(result, allowed):
    if result is None or result.exit_status != 0:
        return ()
    try:
        text = result.stdout.decode("utf-8")
    except (AttributeError, UnicodeError):
        return ()
    found = []
    seen = set()
    for line in text.splitlines():
        if not line.startswith(_MARKER):
            continue
        payload = line[len(_MARKER) :]
        fields = payload.split("|")
        if len(fields) != 7:
            continue
        (
            command_name,
            path,
            advertised_env,
            advertised_conf,
            advertised_bin,
            advertised_library,
            advertised_server,
        ) = fields
        kind = (
            SchedulerKind.SLURM
            if command_name == "sbatch"
            else SchedulerKind.LSF
            if command_name == "bsub"
            else None
        )
        if kind is None or kind not in allowed or kind in seen:
            continue
        try:
            validate_scheduler_command_path(path, command_name)
        except SlurmVerificationError:
            continue
        seen.add(kind)
        found.append(
            (
                kind,
                path,
                advertised_env or None,
                advertised_conf or None,
                advertised_bin or None,
                advertised_library or None,
                advertised_server or None,
            )
        )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class _LsfClientDirectories:
    env_directory: str
    library_directory: str
    server_directory: str


def _lsf_client_directory_candidates(
    bsub_path: str,
    *,
    explicit_env: str | None,
    explicit_library: str | None,
    explicit_server: str | None,
    advertised_env: str | None,
    advertised_conf: str | None,
    advertised_bin: str | None,
    advertised_library: str | None,
    advertised_server: str | None,
) -> tuple[_LsfClientDirectories, ...]:
    """Return bounded client-directory bundles; never scan or source a script."""

    command_directory = PurePosixPath(bsub_path).parent
    normalized_bin = validate_slurm_bin_directory(str(command_directory))
    machine_directory = command_directory.parent
    derived_library = str(machine_directory / "lib")
    derived_server = str(machine_directory / "etc")
    candidates: list[_LsfClientDirectories] = []

    def add(env_value, library_value, server_value) -> None:
        try:
            item = _LsfClientDirectories(
                validate_lsf_env_directory(env_value),
                validate_lsf_library_directory(library_value),
                validate_lsf_server_directory(server_value),
            )
        except Exception:
            return
        if item not in candidates:
            candidates.append(item)

    if explicit_env is not None:
        add(
            explicit_env,
            explicit_library or derived_library,
            explicit_server or derived_server,
        )
        return tuple(candidates)

    advertised_bin_matches = False
    if isinstance(advertised_bin, str) and advertised_bin:
        try:
            advertised_bin_matches = (
                validate_slurm_bin_directory(advertised_bin) == normalized_bin
            )
        except Exception:
            advertised_bin_matches = False

    advertised_envs = tuple(
        value
        for value in (advertised_env, advertised_conf)
        if isinstance(value, str) and value
    )
    if advertised_bin_matches:
        for env_value in advertised_envs:
            add(
                env_value,
                explicit_library or advertised_library or derived_library,
                explicit_server or advertised_server or derived_server,
            )

    raw_env_candidates: list[str] = [*advertised_envs, "/etc"]
    for ancestor in command_directory.parents[:4]:
        if str(ancestor) != "/":
            raw_env_candidates.append(str(ancestor / "conf"))
    if command_directory.parent != PurePosixPath("/"):
        raw_env_candidates.append(str(command_directory.parent / "etc"))
    for env_value in raw_env_candidates:
        add(
            env_value,
            explicit_library or derived_library,
            explicit_server or derived_server,
        )
    return tuple(candidates)


def _selected_lsf_directories(stdout) -> _LsfClientDirectories | None:
    if not isinstance(stdout, bytes):
        return None
    try:
        text = stdout.decode("utf-8")
    except UnicodeError:
        return None
    selected = [
        line[len(_LSF_ENV_MARKER) :].split("|")
        for line in text.splitlines()
        if line.startswith(_LSF_ENV_MARKER)
    ]
    if len(selected) != 1 or len(selected[0]) != 3:
        return None
    try:
        return _LsfClientDirectories(
            validate_lsf_env_directory(selected[0][0]),
            validate_lsf_library_directory(selected[0][1]),
            validate_lsf_server_directory(selected[0][2]),
        )
    except Exception:
        return None


def _remote_error_excerpt(result) -> str:
    for data in (
        getattr(result, "stderr", None),
        getattr(result, "stdout", None),
    ):
        if not isinstance(data, bytes):
            continue
        text = data.decode("utf-8", errors="replace")
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and not line.startswith(_LSF_ATTEMPT_MARKER)
            and not line.startswith(_LSF_ENV_MARKER)
        ]
        if lines:
            return " ".join(lines)[:300]
    return ""


def _plausible_lsf_version(stdout, stderr):
    for data in (stdout, stderr):
        if not isinstance(data, bytes):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            continue
        for line in text.splitlines():
            candidate = line.strip()
            if candidate.startswith(
                (_MARKER, _LSF_ATTEMPT_MARKER, _LSF_ENV_MARKER)
            ):
                continue
            if candidate and _LSF_VERSION_WORD.search(candidate):
                return candidate[:300]
    return None


_LSF_VERSION_WORD = re.compile(
    r"(?:IBM\s+Spectrum\s+LSF|Platform\s+LSF|OpenLava|\bLSF\b)", re.I
)
