"""Bounded read-only discovery across common environment-module layouts."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import shlex

from moltage.aitranss.runtime import (
    AITRANSS_EXECUTABLE_COMMAND,
    is_aitranss_executable_name,
)
from moltage.domain.server_profile import (
    RuntimeEnvironment,
    normalize_module_name,
    validate_remote_executable_path,
)
from moltage.remote.executor import RemoteExecutor


FHI_AIMS_EXECUTABLE_COMMAND = "aims.x"
MODULE_LISTING_QUERIES = (
    ("avail", "aims"),
    ("avail", "aitranss"),
    ("avail", "AIMS"),
    ("avail", "AITRANSS"),
    ("spider", "aims"),
    ("spider", "aitranss"),
    ("spider", "AIMS"),
    ("spider", "AITRANSS"),
)
MAX_RUNTIME_MODULE_CANDIDATES = 64
_COMMAND_MARKER = "__MOLTAGE_COMMAND__="
_RESOLVED_MARKER = "__MOLTAGE_RESOLVED__="
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TRAILING_DECORATION = re.compile(r"(?:\([^()\s]*\)|<[^<>\s]*>)$")


class RuntimeDiscoveryError(RuntimeError):
    """Raised when no supported module environment exposes both runtimes."""


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    module_name: str
    modules: tuple[str, ...]
    command_v_path: str
    executable_path: str
    environment: RuntimeEnvironment | None = None
    launcher_path: str | None = None
    notes: tuple[str, ...] = ()
    environment_resolved: bool = True
    launcher_candidates: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    dependency_search_paths: tuple[str, ...] = ()
    species_root_path: str | None = None
    species_root_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeDiscoveryResult:
    fhi_aims_candidates: tuple[RuntimeCandidate, ...]
    aitranss_candidates: tuple[RuntimeCandidate, ...]
    diagnostics: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    verified_srun_paths: tuple[str, ...] = ()


def discover_runtimes(
    executor: RemoteExecutor,
    current_fhi_modules: tuple[str, ...],
) -> RuntimeDiscoveryResult:
    """Find exact accepted executable identities through bounded module queries."""

    current_modules = tuple(
        normalize_module_name(module) for module in current_fhi_modules
    )
    prefix_modules = tuple(
        module for module in current_modules if not _is_runtime_module_name(module)
    )
    fhi_candidates: list[RuntimeCandidate] = []
    aitranss_candidates: list[RuntimeCandidate] = []

    if current_modules:
        configured_name = _configured_runtime_label(current_modules)
        _append_verified_candidate(
            fhi_candidates,
            _verify_candidate(
                executor,
                module_name=configured_name,
                modules=current_modules,
                executable_command=FHI_AIMS_EXECUTABLE_COMMAND,
            ),
        )
        _append_verified_candidate(
            aitranss_candidates,
            _verify_candidate(
                executor,
                module_name=configured_name,
                modules=current_modules,
                executable_command=AITRANSS_EXECUTABLE_COMMAND,
            ),
        )
        if fhi_candidates and aitranss_candidates:
            return RuntimeDiscoveryResult(
                tuple(fhi_candidates),
                tuple(aitranss_candidates),
            )

    listing_environments = [()]
    if prefix_modules:
        listing_environments.append(prefix_modules)

    listed_contexts: list[tuple[str, tuple[str, ...]]] = []
    listing_succeeded = False
    for environment in listing_environments:
        for subcommand, search_term in MODULE_LISTING_QUERIES:
            command = build_module_listing_command(
                subcommand,
                search_term,
                environment,
            )
            result = executor.execute(command)
            if result.exit_status != 0:
                continue
            try:
                listing = result.stdout.decode("utf-8")
            except UnicodeError:
                continue
            listing_succeeded = True
            for module_name in parse_terse_module_listing(listing):
                context = (module_name, environment)
                if context not in listed_contexts:
                    listed_contexts.append(context)
                    if len(listed_contexts) > MAX_RUNTIME_MODULE_CANDIDATES:
                        raise RuntimeDiscoveryError(
                            "The server reported too many runtime-related module "
                            "candidates; narrow the Environment modules list first"
                        )

    for module_name, environment in listed_contexts:
        modules = _ordered_unique((*environment, module_name))
        _append_verified_candidate(
            fhi_candidates,
            _verify_candidate(
                executor,
                module_name=module_name,
                modules=modules,
                executable_command=FHI_AIMS_EXECUTABLE_COMMAND,
            ),
        )
        _append_verified_candidate(
            aitranss_candidates,
            _verify_candidate(
                executor,
                module_name=module_name,
                modules=modules,
                executable_command=AITRANSS_EXECUTABLE_COMMAND,
            ),
        )

    if not listing_succeeded and not fhi_candidates and not aitranss_candidates:
        raise RuntimeDiscoveryError(
            "The server environment-module command could not list runtime "
            "candidates using supported Lmod or Environment Modules queries"
        )
    if not fhi_candidates:
        raise RuntimeDiscoveryError(
            "No verified module environment exposes the accepted FHI-aims "
            f"executable identity {FHI_AIMS_EXECUTABLE_COMMAND}"
        )
    if not aitranss_candidates:
        raise RuntimeDiscoveryError(
            "No verified module environment exposes the accepted AITRANSS "
            f"executable identity {AITRANSS_EXECUTABLE_COMMAND}"
        )
    return RuntimeDiscoveryResult(
        tuple(fhi_candidates),
        tuple(aitranss_candidates),
    )


def build_module_listing_command(
    subcommand: str,
    search_term: str | tuple[str, ...],
    prefix_modules: tuple[str, ...] = (),
) -> str:
    """Build one isolated terse Lmod/Environment Modules listing command."""

    if subcommand not in {"avail", "spider"}:
        raise ValueError("module listing subcommand must be avail or spider")
    terms = (search_term,) if isinstance(search_term, str) else search_term
    if not terms or any(
        term
        not in {
            "aims",
            "aitranss",
            "AIMS",
            "AITRANSS",
            "mpi",
            "mkl",
            "MPI",
            "MKL",
            "scalapack",
            "openmpi",
            "intel",
            "oneapi",
            "compiler",
            "gcc",
        }
        for term in terms
    ):
        raise ValueError("unsupported runtime module search term")
    modules = tuple(normalize_module_name(module) for module in prefix_modules)
    commands = ["module purge >/dev/null 2>&1 || exit $?"]
    commands.extend(
        "module load " + shlex.quote(module) + " >/dev/null 2>&1 || exit $?"
        for module in modules
    )
    commands.extend(
        (
            "export LMOD_COLORIZE=no",
            "export LMOD_TERSE_DECORATIONS=no",
            "export MODULES_COLOR=never",
            "export MODULES_AVAIL_TERSE_OUTPUT=",
            "export MODULES_SPIDER_TERSE_OUTPUT=",
        )
    )
    if isinstance(search_term, str):
        commands.append("module -t " + subcommand + " " + shlex.quote(search_term) + " 2>&1")
    else:
        # Share login/module initialization for both names, retaining ordinary
        # nonzero-result fallback when none of the bounded queries succeeds.
        commands.append("listing_ok=0")
        commands.extend("module -t " + subcommand + " " + shlex.quote(term)
                        + " 2>&1 && listing_ok=1" for term in terms)
        commands.append('[ "$listing_ok" = 1 ]')
    return "bash -lc " + shlex.quote("; ".join(commands))


def parse_terse_module_listing(text: str) -> tuple[str, ...]:
    """Extract safe runtime-related names from terse Lmod or Modules output."""

    if not isinstance(text, str):
        raise TypeError("environment-module listing must be text")
    modules: list[str] = []
    for raw_line in text.splitlines():
        line = _ANSI_ESCAPE.sub("", raw_line).strip()
        if not line:
            continue
        for raw_token in line.split():
            module = _module_name_from_listing_token(raw_token)
            if module is None or module in modules:
                continue
            modules.append(module)
            if len(modules) > MAX_RUNTIME_MODULE_CANDIDATES:
                raise RuntimeDiscoveryError(
                    "The server reported too many runtime-related module candidates"
                )
    return tuple(modules)


def launch_command_with_verified_fhi_path(
    current_launch_command: str,
    executable_path: str,
) -> str:
    """Preserve existing srun options and replace only its executable path."""

    executable = validate_remote_executable_path(
        executable_path,
        "FHI-aims executable",
    )
    if not is_fhi_aims_executable_name(PurePosixPath(executable).name):
        raise RuntimeDiscoveryError(
            "verified FHI-aims path has an unsupported executable name"
        )
    try:
        tokens = shlex.split(current_launch_command, posix=True)
    except ValueError:
        raise RuntimeDiscoveryError(
            "current FHI-aims launch command is not valid shell text"
        ) from None
    if not tokens:
        return shlex.join(("srun", executable))
    if len(tokens) < 2 or tokens[0] != "srun" or tokens[-1].startswith("-"):
        raise RuntimeDiscoveryError(
            "current FHI-aims launch command must be an srun command ending "
            "with one executable"
        )
    tokens[-1] = executable
    return shlex.join(tokens)


def _module_name_from_listing_token(raw_token: str) -> str | None:
    token = raw_token.strip().strip(",")
    while _TRAILING_DECORATION.search(token) is not None:
        token = _TRAILING_DECORATION.sub("", token)
    # Some Tcl Environment Modules installations render terse names with a
    # presentation-only leading slash (for example /math/example-1.0),
    # while diagnostics and `module load` use math/example-1.0. Directory
    # headings retain their trailing colon and must not be converted.
    if token.endswith(":"):
        return None
    token = token.removeprefix("/")
    if (
        not token
        or token.endswith("/")
    ):
        return None
    try:
        module = normalize_module_name(token)
    except Exception:
        return None
    return module if _is_runtime_module_name(module) else None


def _is_runtime_module_name(module_name: str) -> bool:
    return "aims" in module_name.casefold() or "aitranss" in module_name.casefold()


def _configured_runtime_label(modules: tuple[str, ...]) -> str:
    return next(
        (
            module
            for module in reversed(modules)
            if _is_runtime_module_name(module)
        ),
        modules[-1],
    )


def _ordered_unique(modules: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(modules))


def _append_verified_candidate(
    candidates: list[RuntimeCandidate],
    candidate: RuntimeCandidate | None,
) -> None:
    if candidate is None:
        return
    identity = (candidate.modules, candidate.executable_path)
    if all((item.modules, item.executable_path) != identity for item in candidates):
        candidates.append(candidate)


def _verify_candidate(
    executor: RemoteExecutor,
    *,
    module_name: str,
    modules: tuple[str, ...],
    executable_command: str,
) -> RuntimeCandidate | None:
    commands = ["module purge >/dev/null 2>&1 || exit $?"]
    commands.extend(
        "module load " + shlex.quote(module) + " >/dev/null 2>&1 || exit $?"
        for module in modules
    )
    commands.extend(
        (
            "candidate=$(command -v "
            + shlex.quote(executable_command)
            + " 2>/dev/null || true)",
            'resolved=""',
            'if [ -n "$candidate" ]; then '
            'resolved=$(readlink -f -- "$candidate" 2>/dev/null || true); fi',
            '[ -n "$candidate" ] && [ -n "$resolved" ] '
            '&& [ -f "$resolved" ] && [ -x "$resolved" ] || exit 3',
            f"printf '{_COMMAND_MARKER}%s\\n' \"$candidate\"",
            f"printf '{_RESOLVED_MARKER}%s\\n' \"$resolved\"",
        )
    )
    result = executor.execute("bash -lc " + shlex.quote("; ".join(commands)))
    if result.exit_status != 0:
        return None
    try:
        text = result.stdout.decode("utf-8")
        command_path = _one_marked_path(
            text,
            _COMMAND_MARKER,
            executable_command,
        )
        resolved_path = _one_marked_path(
            text,
            _RESOLVED_MARKER,
            executable_command,
        )
    except (UnicodeError, RuntimeDiscoveryError):
        return None
    if command_path is None or resolved_path is None:
        return None
    return RuntimeCandidate(
        module_name=module_name,
        modules=modules,
        command_v_path=command_path,
        executable_path=resolved_path,
    )


def _one_marked_path(
    text: str,
    marker: str,
    executable_command: str,
) -> str | None:
    values = tuple(
        line[len(marker) :]
        for line in text.splitlines()
        if line.startswith(marker)
    )
    if len(values) != 1:
        raise RuntimeDiscoveryError("runtime lookup markers are ambiguous")
    if not values[0]:
        return None
    try:
        path = validate_remote_executable_path(values[0], "discovered executable")
    except Exception:
        raise RuntimeDiscoveryError(
            "runtime lookup returned an unsafe executable path"
        ) from None
    if PurePosixPath(path).name != executable_command:
        raise RuntimeDiscoveryError(
            "runtime lookup returned the wrong executable identity"
        )
    return path


def is_fhi_aims_executable_name(name: str) -> bool:
    """Return whether a basename belongs to the supported FHI-aims family."""

    if not isinstance(name, str):
        return False
    lowered = name.casefold()
    return lowered == "aims.x" or (
        lowered.startswith("aims.") and lowered.endswith(".x")
    )
