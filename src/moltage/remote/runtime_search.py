"""Bounded, evidence-led runtime discovery; never launch scientific programs."""

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
import posixpath
import re
import shlex
import time

from moltage.domain.server_profile import (
    RuntimeDiscoveryHints, RuntimeEnvironment, RuntimeEnvironmentMode, RuntimeLocationKind,
    ServerProfileValidationError, normalize_module_name,
    validate_fhi_species_defaults_path, validate_remote_executable_path,
)
from moltage.remote.executor import RemotePathNotFoundError
from moltage.remote.runtime_discovery import (
    AITRANSS_EXECUTABLE_COMMAND, FHI_AIMS_EXECUTABLE_COMMAND,
    RuntimeCandidate, RuntimeDiscoveryError, RuntimeDiscoveryResult,
    build_module_listing_command, is_fhi_aims_executable_name,
    parse_terse_module_listing,
)
from moltage.aitranss.runtime import is_aitranss_executable_name
from moltage.remote.runtime_environment import runtime_environment_commands


MAX_CANDIDATES = 64
MAX_COMMANDS = 128
MAX_SECONDS = 120
MAX_SCRIPT_BYTES = 64 * 1024
MAX_SEARCH_DEPTH = 3
MAX_PATH_DIRECTORIES = 32
MAX_INDEX_CANDIDATES_PER_PROGRAM = 32
MAX_ADVERTISED_LAYOUT_DIRECTORIES = 192
MAX_MODULE_CATALOG_LINES = 256
MAX_PROGRAM_MODULE_ENVIRONMENTS = 12
MAX_SUPPORT_MODULE_CANDIDATES = 12
MAX_SUPPORT_MODULE_PROBES = 20
MAX_SUPPORT_DEPTH = 4
MAX_SUPPORT_FRONTIER = 6
MAX_SPECIES_CONTAINERS = 4
MAX_SPECIES_CHILDREN_PER_CONTAINER = 32
MAX_SPECIES_ROOT_CANDIDATES = 16
OPTIONAL_COMMAND_RESERVE = 16
_NONE = RuntimeEnvironment(RuntimeEnvironmentMode.NONE)
_LIBRARY_NAME = re.compile(r"[A-Za-z0-9_.+-]+")
_FHI_NAMES = (
    FHI_AIMS_EXECUTABLE_COMMAND,
    "aims.scalapack.mpi.x",
    "aims.cluster.scalapack.mpi.x",
    "aims.step3.scalapack.mpi.x",
    "run_aims.mpi",
)
_AITRANSS_NAMES = (
    AITRANSS_EXECUTABLE_COMMAND,
    "aitranss",
)
_PATH_NAMES = (*_FHI_NAMES, *_AITRANSS_NAMES, "srun", "mpirun")
_IGNORED_PATH_MARKER = "__AT_IGNORED_PATHS__="
_INDEX_UNAVAILABLE_MARKER = "__AT_INDEX_UNAVAILABLE__"
_ADVERTISED_LAYOUT_LIMIT_MARKER = "__AT_ADVERTISED_LAYOUT_LIMIT__"
_FHI_MANUAL_NAMES = (
    FHI_AIMS_EXECUTABLE_COMMAND,
    "aims.scalapack.mpi.x",
    "aims.cluster.scalapack.mpi.x",
    "aims.step3.scalapack.mpi.x",
    "aims.x",
    "aims*.x",
)
_AITRANSS_MANUAL_NAMES = (
    AITRANSS_EXECUTABLE_COMMAND,
    "aitranss*.x",
    "aitranss",
)


class _SearchLimitReached(RuntimeDiscoveryError):
    """A normal search budget stop, never a transport failure."""


@dataclass(frozen=True, slots=True)
class _DependencyReport:
    notes: tuple[str, ...]
    needed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    path_hints: tuple[str, ...] = ()
    inspected: bool = False
    located: tuple[tuple[str, str], ...] = ()

    @property
    def resolved(self):
        return self.inspected and not self.missing


class _Search:
    def __init__(self, executor, *, allow_setup_scripts=False):
        self.executor = executor
        self.allow_setup_scripts = allow_setup_scripts
        self.started = time.monotonic()
        self.calls = 0
        self.diagnostics = []
        self.cache = {}
        self.head_cache = {}
        self._indexed = None
        self.index_available = None

    def read_head(self, path, size):
        key = (path, size)
        if key not in self.head_cache:
            self.head_cache[key] = self.executor.read_file_head(path, size)
        return self.head_cache[key]

    def execute(self, body, environment=_NONE):
        if environment.mode is RuntimeEnvironmentMode.SCRIPT and not self.allow_setup_scripts:
            raise RuntimeDiscoveryError(
                "The selected setup script requires explicit permission to execute during environment preparation"
            )
        setup = runtime_environment_commands(environment)
        command = "bash -lc " + shlex.quote("; ".join((*setup, body)))
        return self.execute_command(command)

    def execute_command(self, command):
        if command in self.cache:
            return self.cache[command]
        if self.calls >= MAX_COMMANDS or time.monotonic() - self.started >= MAX_SECONDS:
            raise _SearchLimitReached("Runtime search limit reached; narrow the installation directory")
        self.calls += 1
        result = self.executor.execute(command)
        self.cache[command] = result
        return result

    def text(self, result):
        try:
            return result.stdout.decode("utf-8")
        except UnicodeError:
            raise RuntimeDiscoveryError("Runtime query returned invalid UTF-8") from None

    def paths_on_path(self, names, environment):
        # One cached inventory per environment serves both programs and MPI.
        names_text = " ".join(shlex.quote(name) for name in _PATH_NAMES)
        result = self.execute(
            "# MOLTAGE_RUNTIME_PATH\n"
            "count=0; ignored=0; "
            f"for name in {names_text}; do "
            'while IFS= read -r candidate; do '
            '[ -n "$candidate" ] || continue; '
            'count=$((count + 1)); '
            f'[ "$count" -le {MAX_CANDIDATES} ] || exit 5; '
            'case "$candidate" in /*) ;; *) ignored=$((ignored + 1)); continue ;; esac; '
            'candidate_name=${candidate##*/}; candidate_directory=${candidate%/*}; '
            '[ -n "$candidate_directory" ] || candidate_directory=/; '
            'canonical_directory=$(CDPATH= cd -P -- "$candidate_directory" 2>/dev/null && pwd -P) '
            '|| { ignored=$((ignored + 1)); continue; }; '
            'candidate="${canonical_directory%/}/$candidate_name"; '
            'printf "%s\\t%s\\n" "$name" "$candidate"; '
            'done < <(type -a -p -- "$name" 2>/dev/null); done; '
            f'printf "{_IGNORED_PATH_MARKER}%s\\n" "$ignored"',
            environment,
        )
        if result.exit_status == 5:
            raise RuntimeDiscoveryError("Too many PATH candidates; narrow the installation directory")
        if result.exit_status != 0:
            self.diagnostics.append("Runtime PATH query failed in the selected environment")
            return {}
        text = self.text(result)
        ignored = self._reported_ignored_paths(text)
        found = {}
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0] in names:
                try:
                    path = validate_remote_executable_path(parts[1], "Discovered command")
                except ServerProfileValidationError:
                    ignored += 1
                    continue
                if path not in found.setdefault(parts[0], []):
                    found[parts[0]].append(path)
        if ignored:
            self.diagnostics.append(
                f"Ignored {ignored} unsafe or non-canonical PATH runtime candidate(s)"
            )
        if sum(map(len, found.values())) > MAX_CANDIDATES:
            raise RuntimeDiscoveryError("Too many PATH candidates; narrow the installation directory")
        return {name: tuple(paths) for name, paths in found.items()}

    def path_family(self, program, environment):
        """Last cheap path fallback: versioned names, no recursive PATH crawl."""
        pattern = "aims*.x" if program == "FHI-aims" else "aitranss*.x"
        result = self.execute(
            "# MOLTAGE_RUNTIME_FAMILY\n"
            "printf '__AT_CANDIDATES_BEGIN__\\n'; "
            'IFS=: read -r -a directories <<< "$PATH"; count=0; checked=0; ignored=0; '
            'for directory in "${directories[@]}"; do '
            'case "$directory" in /*) ;; *) continue ;; esac; '
            'checked=$((checked + 1)); '
            f'if [ "$checked" -gt {MAX_PATH_DIRECTORIES} ]; then '
            "printf '__AT_PATH_LIMIT__\\n'; break; fi; "
            'canonical_directory=$(CDPATH= cd -P -- "$directory" 2>/dev/null && pwd -P) '
            '|| { ignored=$((ignored + 1)); continue; }; '
            f'for candidate in "$canonical_directory"/{pattern}; do '
            '[ -f "$candidate" ] && [ -x "$candidate" ] || continue; '
            'printf "%s\\n" "$candidate"; count=$((count + 1)); '
            f'[ "$count" -le {MAX_CANDIDATES} ] || exit 5; '
            f"done; done; printf '__AT_CANDIDATES_END__\\n{_IGNORED_PATH_MARKER}%s\\n' \"$ignored\"", environment,
        )
        if result.exit_status == 5:
            raise RuntimeDiscoveryError("Too many PATH candidates; narrow the installation directory")
        if result.exit_status != 0:
            self.diagnostics.append(f"{program}: versioned PATH lookup unavailable in this environment")
            return ()
        return self.candidate_paths(result)

    def indexed_paths(self):
        """Query an existing filename database once; never traverse the filesystem."""

        if self._indexed is not None:
            return self._indexed
        result = self.execute(
            "# MOLTAGE_RUNTIME_INDEX\n"
            "printf '__AT_CANDIDATES_BEGIN__\\n'; "
            "index_tool=$(type -P plocate 2>/dev/null || type -P locate 2>/dev/null || true); "
            'if [ -z "$index_tool" ]; then '
            f"printf '{_INDEX_UNAVAILABLE_MARKER}\\n__AT_CANDIDATES_END__\\n'; exit 0; fi; "
            "query_index () { "
            '"$index_tool" -b -e "$1" 2>/dev/null || '
            '"$index_tool" "$1" 2>/dev/null || true; }; '
            "emit_indexed () { label=$1; pattern=$2; count=0; "
            'while IFS= read -r candidate; do '
            'case "$candidate" in /*) ;; *) continue ;; esac; '
            'resolved=$(readlink -f -- "$candidate" 2>/dev/null || true); '
            '[ -n "$resolved" ] && [ -f "$resolved" ] && [ -x "$resolved" ] || continue; '
            'printf "%s\\t%s\\n" "$label" "$resolved"; count=$((count + 1)); '
            f'if [ "$count" -ge {MAX_INDEX_CANDIDATES_PER_PROGRAM} ]; then '
            'printf "__AT_INDEX_LIMIT__=%s\\n" "$label"; return; fi; '
            'done < <(query_index "$pattern"); }; '
            "emit_indexed FHI-aims 'aims*.x'; "
            "emit_indexed AITRANSS 'aitranss*.x'; "
            "emit_indexed AITRANSS 'aitranss'; "
            "printf '__AT_CANDIDATES_END__\\n'",
            _NONE,
        )
        if result.exit_status != 0:
            self.index_available = False
            self.diagnostics.append(
                "The server filename index could not be queried; no filesystem scan was substituted"
            )
            self._indexed = {"FHI-aims": (), "AITRANSS": ()}
            return self._indexed
        text = self.text(result)
        self.index_available = _INDEX_UNAVAILABLE_MARKER not in text
        if not self.index_available:
            self._indexed = {"FHI-aims": (), "AITRANSS": ()}
            return self._indexed
        lines = text.splitlines()
        if lines.count("__AT_CANDIDATES_BEGIN__") != 1 or lines.count("__AT_CANDIDATES_END__") != 1:
            raise RuntimeDiscoveryError("Runtime filename-index response was incomplete or ambiguous")
        lines = lines[lines.index("__AT_CANDIDATES_BEGIN__") + 1:lines.index("__AT_CANDIDATES_END__")]
        found = {"FHI-aims": [], "AITRANSS": []}
        ignored = 0
        for line in lines:
            if line == _INDEX_UNAVAILABLE_MARKER:
                continue
            if line.startswith("__AT_INDEX_LIMIT__="):
                label = line.split("=", 1)[1]
                self.diagnostics.append(
                    f"{label}: filename-index result limit reached; partial indexed results retained"
                )
                continue
            parts = line.split("\t")
            if len(parts) != 2 or parts[0] not in found:
                continue
            label, raw_path = parts
            try:
                path = validate_remote_executable_path(raw_path, "Indexed runtime candidate")
            except ServerProfileValidationError:
                ignored += 1
                continue
            name = PurePosixPath(path).name.casefold()
            valid_name = (
                label == "FHI-aims" and name.startswith("aims") and name.endswith(".x")
            ) or (
                label == "AITRANSS" and (name == "aitranss" or
                                          name.startswith("aitranss") and name.endswith(".x"))
            )
            if valid_name and path not in found[label]:
                found[label].append(path)
        if ignored:
            self.diagnostics.append(
                f"Ignored {ignored} unsafe or non-canonical filename-index candidate(s)"
            )
        self._indexed = {label: tuple(paths) for label, paths in found.items()}
        return self._indexed

    def layout_paths(self, roots, program, environment):
        """Inspect only immediate conventional siblings below evidence-derived roots."""

        safe_roots = []
        for root in roots:
            try:
                normalized = validate_remote_executable_path(root, "Derived installation root")
            except ServerProfileValidationError:
                continue
            if normalized not in safe_roots:
                safe_roots.append(normalized)
        if not safe_roots:
            return ()
        roots_text = " ".join(shlex.quote(root) for root in safe_roots)
        patterns = (
            ("aims*.mpi.x", "aims*.scalapack*.x", "aims.x")
            if program == "FHI-aims"
            else ("aitranss*.x", "aitranss")
        )
        patterns_text = " ".join(shlex.quote(pattern) for pattern in patterns)
        result = self.execute(
            "# MOLTAGE_RUNTIME_LAYOUT\n"
            "printf '__AT_CANDIDATES_BEGIN__\\n'; count=0; "
            f"for root in {roots_text}; do "
            'for directory in "$root" "$root/bin" "$root/scripts" "$root/libexec"; do '
            'canonical_directory=$(CDPATH= cd -P -- "$directory" 2>/dev/null && pwd -P) || continue; '
            f"for pattern in {patterns_text}; do "
            'for candidate in "$canonical_directory"/$pattern; do '
            '[ -f "$candidate" ] && [ -x "$candidate" ] || continue; '
            'printf "%s\\n" "$candidate"; count=$((count + 1)); '
            f'[ "$count" -le {MAX_CANDIDATES} ] || exit 5; '
            'done; done; done; done; printf "__AT_CANDIDATES_END__\\n"',
            environment,
        )
        if result.exit_status == 5:
            raise RuntimeDiscoveryError(
                "Too many candidates in evidence-derived installation layouts; enter a narrower directory"
            )
        if result.exit_status != 0:
            self.diagnostics.append(
                f"{program}: evidence-derived installation layout could not be inspected"
            )
            return ()
        return self.candidate_paths(result)

    def advertised_layout_paths(self, program, environment):
        """Inspect shallow program-named layouts anchored by login PATH entries.

        This is deliberately not a recursive filesystem search.  Each canonical
        PATH directory contributes only its immediate installation root and the
        conventional ``opt``, ``apps`` and ``software`` containers directly
        below it; only child names containing ``aims`` are expanded.
        """

        patterns = (
            ("aims*.mpi.x", "aims*.scalapack*.x", "aims.x")
            if program == "FHI-aims"
            else ("aitranss*.x", "aitranss")
        )
        patterns_text = " ".join(shlex.quote(pattern) for pattern in patterns)
        result = self.execute(
            "# MOLTAGE_RUNTIME_ADVERTISED_LAYOUT\n"
            "printf '__AT_CANDIDATES_BEGIN__\\n'; "
            "count=0; checked=0; limited=0; "
            "shopt -s nullglob nocaseglob; declare -A seen_anchors; "
            "emit_directory () { "
            'directory=$1; [ "$checked" -lt '
            f"{MAX_ADVERTISED_LAYOUT_DIRECTORIES}"
            ' ] || { limited=1; return; }; checked=$((checked + 1)); '
            'canonical_directory=$(CDPATH= cd -P -- "$directory" 2>/dev/null && pwd -P) '
            "|| return; "
            f"for pattern in {patterns_text}; do "
            'for candidate in "$canonical_directory"/$pattern; do '
            '[ -f "$candidate" ] && [ -x "$candidate" ] || continue; '
            'printf "%s\\n" "$candidate"; count=$((count + 1)); '
            f'[ "$count" -le {MAX_CANDIDATES} ] || exit 5; '
            "done; done; }; "
            'IFS=: read -r -a path_directories <<< "${PATH-}"; '
            'for path_directory in "${path_directories[@]}"; do '
            'case "$path_directory" in /*) ;; *) continue ;; esac; '
            'canonical_path=$(CDPATH= cd -P -- "$path_directory" 2>/dev/null && pwd -P) '
            "|| continue; "
            'anchor=${canonical_path%/*}; [ -n "$anchor" ] || continue; '
            'if [ -n "${seen_anchors[$anchor]+set}" ]; then continue; fi; '
            'seen_anchors[$anchor]=1; '
            'emit_directory "$canonical_path"; '
            'emit_directory "$anchor/bin"; emit_directory "$anchor/scripts"; '
            'emit_directory "$anchor/libexec"; '
            'for installation in "$anchor"/*aims* "$anchor"/opt/*aims* '
            '"$anchor"/apps/*aims* "$anchor"/software/*aims*; do '
            '[ -d "$installation" ] || continue; '
            'emit_directory "$installation"; emit_directory "$installation/bin"; '
            'emit_directory "$installation/scripts"; '
            'emit_directory "$installation/libexec"; '
            "done; done; "
            f'[ "$limited" = 0 ] || printf "{_ADVERTISED_LAYOUT_LIMIT_MARKER}\\n"; '
            'printf "__AT_CANDIDATES_END__\\n"',
            environment,
        )
        if result.exit_status == 5:
            raise RuntimeDiscoveryError(
                "Too many candidates in PATH-advertised installation layouts; "
                "enter a narrower directory"
            )
        if result.exit_status != 0:
            self.diagnostics.append(
                f"{program}: PATH-advertised installation layouts could not be inspected"
            )
            return ()
        return self.candidate_paths(result)

    def location_paths(self, location, program, environment, kind=RuntimeLocationKind.DIRECTORY):
        path = shlex.quote(location)
        pattern = "aims*.x" if program == "FHI-aims" else "aitranss*.x"
        expected = "-d" if kind is RuntimeLocationKind.DIRECTORY else "-f"
        result = self.execute(
            "# MOLTAGE_RUNTIME_LOCATION\n"
            f"test {expected} {path} || exit 3; "
            "printf '__AT_CANDIDATES_BEGIN__\\n'; "
            f"if [ -d {path} ]; then "
            'printf "__AT_DIRECTORY__\\n"; '
            f"find -H {path} -maxdepth {MAX_SEARCH_DEPTH} "
            r"\( -type f -o -type l \) "
            f"-iname {shlex.quote(pattern)} -print | head -n {MAX_CANDIDATES + 1}; "
            'status=${PIPESTATUS[0]}; [ "$status" = 0 ] || [ "$status" = 141 ]; '
            f"elif [ -f {path} ]; then printf '%s\\n' {path}; else exit 3; fi; "
            'status=$?; printf "__AT_CANDIDATES_END__\\n"; exit "$status"',
            environment,
        )
        if result.exit_status != 0:
            self.diagnostics.append(f"{program}: supplied location is unavailable or could not be searched: {location}")
            return ()
        paths = self.candidate_paths(result)
        if not paths:
            self.diagnostics.append(f"{program}: no executable candidate in the supplied directory: {location}")
        return paths

    def candidate_paths(self, result):
        text = self.text(result)
        ignored = self._reported_ignored_paths(text)
        lines = text.splitlines()
        if lines.count("__AT_CANDIDATES_BEGIN__") != 1 or lines.count("__AT_CANDIDATES_END__") != 1:
            raise RuntimeDiscoveryError("Runtime candidate response was incomplete or ambiguous")
        lines = lines[lines.index("__AT_CANDIDATES_BEGIN__") + 1:lines.index("__AT_CANDIDATES_END__")]
        if lines and lines[0] == "__AT_DIRECTORY__":
            lines = lines[1:]
        if "__AT_PATH_LIMIT__" in lines:
            lines.remove("__AT_PATH_LIMIT__")
            self.diagnostics.append("Versioned PATH lookup reached its directory limit; supply an installation directory")
        if _ADVERTISED_LAYOUT_LIMIT_MARKER in lines:
            lines.remove(_ADVERTISED_LAYOUT_LIMIT_MARKER)
            self.diagnostics.append(
                "PATH-advertised layout lookup reached its shallow directory limit; "
                "partial directed results retained"
            )
        if len(lines) > MAX_CANDIDATES:
            raise RuntimeDiscoveryError("Too many runtime candidates; narrow the installation directory")
        candidates = []
        for line in lines:
            try:
                path = validate_remote_executable_path(line, "Runtime candidate")
            except ServerProfileValidationError:
                ignored += 1
                continue
            if path not in candidates:
                candidates.append(path)
        if ignored:
            self.diagnostics.append(
                f"Ignored {ignored} unsafe or non-canonical PATH runtime candidate(s)"
            )
        return tuple(candidates)

    @staticmethod
    def _reported_ignored_paths(text):
        values = [
            line.removeprefix(_IGNORED_PATH_MARKER)
            for line in text.splitlines()
            if line.startswith(_IGNORED_PATH_MARKER)
        ]
        if len(values) > 1 or (values and not values[0].isdigit()):
            raise RuntimeDiscoveryError("Runtime PATH response had an invalid diagnostic marker")
        return int(values[0]) if values else 0

    def probe(self, path, environment, *, scientific=False, report_failure=True):
        quoted = shlex.quote(path)
        result = self.execute(
            "# MOLTAGE_RUNTIME_FILE\n"
            f"test -f {quoted} && test -r {quoted} && test -x {quoted} || exit 3; "
            f"resolved=$(readlink -f -- {quoted}) || exit 3; "
            'printf "__AT_REALPATH__=%s\\n" "$resolved"', environment,
        )
        if result.exit_status != 0:
            if report_failure:
                self.diagnostics.append(f"Not a readable executable file: {path}")
            return None
        values = [line.removeprefix("__AT_REALPATH__=") for line in self.text(result).splitlines()
                  if line.startswith("__AT_REALPATH__=")]
        if len(values) != 1:
            raise RuntimeDiscoveryError("Executable path response was ambiguous")
        resolved = validate_remote_executable_path(values[0], "Resolved executable")
        if scientific:
            try:
                magic = self.read_head(resolved, 4)
            except RemotePathNotFoundError:
                if report_failure:
                    self.diagnostics.append(f"Executable disappeared during discovery: {resolved}")
                return None
            if magic != b"\x7fELF":
                if report_failure:
                    self.diagnostics.append(f"Select the actual Linux calculation executable, not a wrapper: {path}")
                return None
        return resolved

    def species_roots(self, executable_path, environment, manual_hint=None):
        """Inspect only exact or executable-derived species-default locations."""

        executable = validate_remote_executable_path(
            executable_path,
            "FHI-aims executable",
        )
        if manual_hint is not None:
            seeds = (validate_fhi_species_defaults_path(manual_hint),)
            containers = ()
        else:
            executable_directory = PurePosixPath(executable).parent
            installation_root = (
                executable_directory.parent
                if executable_directory.name.casefold()
                in {"bin", "scripts", "libexec", "lib", "lib64"}
                else executable_directory
            )
            parent = installation_root.parent
            seeds = tuple(
                dict.fromkeys(
                    str(path)
                    for path in (
                        installation_root,
                        installation_root / "species_defaults",
                        installation_root / "share" / "species_defaults",
                        parent / "species_defaults",
                    )
                    if str(path) != "/"
                )
            )
            containers = tuple(
                path
                for path in seeds
                if PurePosixPath(path).name == "species_defaults"
            )[:MAX_SPECIES_CONTAINERS]

        seed_text = " ".join(shlex.quote(path) for path in seeds)
        container_text = " ".join(
            shlex.quote(path) for path in containers
        )
        result = self.execute(
            "# MOLTAGE_SPECIES_ROOTS\n"
            "printf '__AT_SPECIES_BEGIN__\\n'; "
            "declare -A seen_species; count=0; "
            "check_species_root () { "
            "candidate=$1; [ -d \"$candidate\" ] && [ -r \"$candidate\" ] || return; "
            "resolved=$(readlink -f -- \"$candidate\" 2>/dev/null || true); "
            "[ -n \"$resolved\" ] && [ -d \"$resolved/light\" ] "
            "&& [ -d \"$resolved/tight\" ] "
            "&& [ -d \"$resolved/really_tight\" ] || return; "
            "[ -r \"$resolved/light\" ] && [ -r \"$resolved/tight\" ] "
            "&& [ -r \"$resolved/really_tight\" ] || return; "
            "case \"$resolved\" in /*) ;; *) return ;; esac; "
            "if [ -z \"${seen_species[$resolved]+set}\" ]; then "
            "seen_species[$resolved]=1; printf '%s\\n' \"$resolved\"; "
            "count=$((count + 1)); "
            f"[ \"$count\" -le {MAX_SPECIES_ROOT_CANDIDATES} ] || exit 5; "
            "fi; }; "
            f"for candidate in {seed_text}; do check_species_root \"$candidate\"; done; "
            f"for container in {container_text}; do "
            "[ -d \"$container\" ] || continue; child_count=0; "
            "for child in \"$container\"/*; do [ -d \"$child\" ] || continue; "
            "child_count=$((child_count + 1)); "
            f"[ \"$child_count\" -le {MAX_SPECIES_CHILDREN_PER_CONTAINER} ] || exit 6; "
            "check_species_root \"$child\"; done; done; "
            "printf '__AT_SPECIES_END__\\n'",
            environment,
        )
        if result.exit_status == 5:
            raise RuntimeDiscoveryError(
                "Too many FHI-aims species-root candidates in bounded installation layouts"
            )
        if result.exit_status == 6:
            raise RuntimeDiscoveryError(
                "FHI-aims species-default container exceeds the bounded child limit; "
                "enter the exact species definitions root"
            )
        if result.exit_status != 0:
            self.diagnostics.append(
                "FHI-aims species definitions root could not be inspected"
            )
            return ()
        lines = self.text(result).splitlines()
        if (
            lines.count("__AT_SPECIES_BEGIN__") != 1
            or lines.count("__AT_SPECIES_END__") != 1
        ):
            raise RuntimeDiscoveryError(
                "FHI-aims species-root response was incomplete or ambiguous"
            )
        values = lines[
            lines.index("__AT_SPECIES_BEGIN__") + 1:
            lines.index("__AT_SPECIES_END__")
        ]
        roots = []
        for value in values:
            try:
                root = validate_fhi_species_defaults_path(value)
            except ServerProfileValidationError:
                continue
            if root not in roots:
                roots.append(root)
        if len(roots) > MAX_SPECIES_ROOT_CANDIDATES:
            raise RuntimeDiscoveryError(
                "Too many FHI-aims species-root candidates in bounded installation layouts"
            )
        return tuple(roots)

    def wrapper_target(self, wrapper, environment):
        """Only the reviewed dirname/readlink + literal aims-name wrapper grammar."""
        resolved = self.probe(wrapper, environment)
        if resolved is None:
            return None
        try:
            raw = self.read_head(resolved, MAX_SCRIPT_BYTES + 1)
        except RemotePathNotFoundError:
            return None
        if len(raw) > MAX_SCRIPT_BYTES:
            self.diagnostics.append(f"Startup script exceeds the read limit: {wrapper}")
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            return None
        assignments = re.findall(r"(?m)^aims=['\"](aims[\w.+-]*\.x)['\"]\s*$", text)
        if (
            len(assignments) != 1
            or re.search(r"(?m)^psd=\$\(dirname\s+\$\(readlink\s+-f\s+\$0\)\)\s*$", text) is None
            or re.search(r"(?m)^mpirun\s+-n\s+\$cpus\s+\$\{psd\}/\.\./bin/\$\{aims\}(?:\s|$)", text) is None
        ):
            self.diagnostics.append(f"Startup script is not a supported static layout: {wrapper}")
            return None
        # Later conditional heap-array assignments are not selected by this default branch.
        target = posixpath.normpath(str(PurePosixPath(resolved).parent / "../bin" / assignments[0]))
        return validate_remote_executable_path(target, "Wrapper executable")

    def dependencies(self, path, environment):
        """Inspect direct ELF dependencies only; never call ldd or the executable."""
        result = self.execute(
            "# MOLTAGE_RUNTIME_DEPENDENCIES\n"
            'if ! command -v readelf >/dev/null 2>&1; then exit 4; fi; '
            f"readelf -d -- {shlex.quote(path)} || exit $?; "
            'printf "__AT_LIBPATH__=%s\\n" "${LD_LIBRARY_PATH-}"', environment,
        )
        if result.exit_status != 0:
            return _DependencyReport(
                ("Dependency metadata unavailable (readelf); startup has not been tested",)
            )
        text = self.text(result)
        needed = tuple(dict.fromkeys(re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", text)))
        if not needed:
            return _DependencyReport(
                ("No direct dynamic dependencies reported; startup has not been tested",),
                inspected=True,
            )
        if len(needed) > 64 or any(_LIBRARY_NAME.fullmatch(name) is None for name in needed):
            return _DependencyReport(
                ("Dependency metadata exceeds the supported inspection scope",),
                needed=needed,
            )
        search_paths = re.findall(r"\((?:RUNPATH|RPATH)\).*?\[([^\]]*)\]", text)
        search_paths.extend(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("__AT_LIBPATH__="))
        directories = []
        for value in search_paths:
            for item in value.split(":"):
                item = item.replace("${ORIGIN}", str(PurePosixPath(path).parent)).replace("$ORIGIN", str(PurePosixPath(path).parent))
                if item.startswith("/") and "$" not in item:
                    item = posixpath.normpath(item)
                    if item != "/" and item not in directories:
                        directories.append(item)
        dirs = " ".join(shlex.quote(item) for item in directories)
        names = " ".join(shlex.quote(name) for name in needed)
        result = self.execute(
            "# MOLTAGE_RUNTIME_LIBRARY_CHECK\n"
            'cache_tool=$(type -P ldconfig); '
            'if [ -z "$cache_tool" ] && [ -x /sbin/ldconfig ]; then cache_tool=/sbin/ldconfig; fi; '
            f"for name in {names}; do found=; "
            f"for directory in {dirs}; do "
            'if [ -r "$directory/$name" ]; then found="$directory/$name"; break; fi; done; '
            'if [ -z "$found" ] && [ -n "$cache_tool" ]; then '
            'candidate=$("$cache_tool" -p 2>/dev/null | awk -v name="$name" \'$1 == name {print $NF; exit}\'); '
            'if [ -r "$candidate" ]; then found="$candidate"; fi; fi; '
            'printf "__AT_LIBRARY__=%s|%s\\n" "$name" "$found"; done', environment,
        )
        if result.exit_status != 0:
            return _DependencyReport(
                ("Library locations could not be checked; startup has not been tested",),
                needed,
                needed,
                tuple(directories),
                True,
            )
        locations = {}
        for line in self.text(result).splitlines():
            if line.startswith("__AT_LIBRARY__=") and "|" in line:
                name, located = line.removeprefix("__AT_LIBRARY__=").split("|", 1)
                locations[name] = located
        missing = tuple(name for name in needed if not locations.get(name))
        if missing:
            return _DependencyReport(
                ("Direct libraries not located in the configured search paths: " + ", ".join(missing),),
                needed,
                missing,
                tuple(directories),
                True,
                tuple((name, path) for name, path in locations.items() if path),
            )
        return _DependencyReport(
            ("Direct library files located; MPI/ABI compatibility and startup have not been tested",),
            needed,
            (),
            tuple(directories),
            True,
            tuple((name, path) for name, path in locations.items() if path),
        )


def discover_runtime_configuration(
    executor,
    hints: RuntimeDiscoveryHints,
    current_modules=(),
    *,
    allow_setup_scripts=False,
):
    """Resolve advertised runtimes with one bounded, read-only evidence search."""

    search = _Search(executor, allow_setup_scripts=allow_setup_scripts)
    normalized_current_modules = tuple(
        normalize_module_name(module) for module in current_modules
    )
    requests = {"FHI-aims": hints.fhi_aims, "AITRANSS": hints.aitranss}
    candidates = {label: [] for label in requests}
    attempted = set()
    launcher_evidence = []
    support_probe_counts = {label: 0 for label in requests}
    optional_limit_notes = set()

    def optional_budget_available(area, estimated_calls=4):
        if search.calls + estimated_calls <= MAX_COMMANDS - OPTIONAL_COMMAND_RESERVE:
            return True
        note = (
            f"{area} stopped before exhausting the runtime command budget; "
            "direct results and manual completion details were retained"
        )
        if note not in optional_limit_notes:
            optional_limit_notes.add(note)
            search.diagnostics.append(note)
        return False

    def candidate_ready(label, candidate):
        if not candidate.environment_resolved:
            return False
        if label == "FHI-aims":
            return bool(
                (candidate.launcher_path or candidate.launcher_candidates)
                and candidate.species_root_path
            )
        return True

    def resolved(label):
        return any(candidate_ready(label, item) for item in candidates[label])

    def needs_automatic_search(label):
        if label == "FHI-aims" and any(
            item.environment_resolved
            and (item.launcher_path or item.launcher_candidates)
            and len(item.species_root_candidates) > 1
            for item in candidates[label]
        ):
            return False
        return (
            requests[label].environment.mode is RuntimeEnvironmentMode.AUTO
            and not resolved(label)
        )

    def environments_for(request):
        if request.environment.mode is not RuntimeEnvironmentMode.AUTO:
            return (request.environment,)
        environments = []
        if normalized_current_modules:
            environments.append(
                RuntimeEnvironment(
                    RuntimeEnvironmentMode.MODULES,
                    normalized_current_modules,
                )
            )
        environments.append(_NONE)
        return tuple(dict.fromkeys(environments))

    def known_roots():
        roots = []
        for item in (*candidates["FHI-aims"], *candidates["AITRANSS"]):
            executable_parent = PurePosixPath(item.executable_path).parent
            root = (
                executable_parent.parent
                if executable_parent.name.casefold()
                in {"bin", "scripts", "libexec", "lib", "lib64"}
                else executable_parent
            )
            if str(root) != "/" and str(root) not in roots:
                roots.append(str(root))
            for directory in item.dependency_search_paths:
                path = PurePosixPath(directory)
                root = (
                    path.parent
                    if path.name.casefold() in {"bin", "scripts", "libexec", "lib", "lib64"}
                    else path
                )
                if str(root) != "/" and str(root) not in roots:
                    roots.append(str(root))
        return tuple(roots)

    def discover_launchers(environment, names=("srun", "mpirun"), *, quiet=False):
        """Collect launcher paths independently; compatibility still needs an FHI build."""

        if hints.mpi_launcher:
            proposed_paths = (hints.mpi_launcher,)
        else:
            found = search.paths_on_path(names, environment)
            proposed_paths = tuple(
                path for paths in found.values() for path in paths
            )
        by_realpath = {}
        for proposed in proposed_paths:
            if PurePosixPath(proposed).name not in names:
                continue
            realpath = search.probe(
                proposed,
                environment,
                report_failure=not quiet,
            )
            if realpath is not None:
                by_realpath.setdefault(realpath, proposed)
                evidence = (environment, proposed, realpath)
                if evidence not in launcher_evidence:
                    launcher_evidence.append(evidence)
        return tuple(by_realpath.values())

    def add_paths(
        label,
        environment,
        paths,
        *,
        wrapper_targets=(),
        better_than=None,
        quiet_failures=False,
    ):
        request = requests[label]
        added = []
        for path in dict.fromkeys(paths):
            executable = search.probe(
                path,
                environment,
                scientific=True,
                report_failure=not quiet_failures,
            )
            if executable is None:
                continue
            if any(
                item.executable_path == executable
                and item.environment == environment
                for item in candidates[label]
            ):
                continue
            dependency = search.dependencies(executable, environment)
            notes = list(dependency.notes)
            executable_name = PurePosixPath(executable).name
            supported_name = (
                is_fhi_aims_executable_name(executable_name)
                if label == "FHI-aims"
                else is_aitranss_executable_name(executable_name)
            )
            if not supported_name:
                notes.append(
                    f"{label} file located, but its executable name is unsupported"
                )
            environment_resolved = (
                request.environment.mode is not RuntimeEnvironmentMode.AUTO
                or dependency.resolved
            )
            launcher = None
            launcher_candidates = ()
            species_root = None
            species_root_candidates = ()
            if label == "FHI-aims":
                launcher_names = (
                    ("mpirun",)
                    if path in wrapper_targets or executable in wrapper_targets
                    else ("srun", "mpirun")
                )
                discovered_launchers = discover_launchers(
                    environment,
                    launcher_names,
                    quiet=quiet_failures,
                )
                launcher_candidates = _compatible_launcher_candidates(
                    discovered_launchers,
                    dependency,
                )
                if discovered_launchers and not launcher_candidates:
                    notes.append(
                        "Located mpirun candidate(s) do not match the MPI library "
                        "installation resolved for this executable"
                    )
                if hints.mpi_launcher and launcher_candidates:
                    launcher = launcher_candidates[0]
                else:
                    if len(launcher_candidates) == 1:
                        launcher = launcher_candidates[0]
                    elif len(launcher_candidates) > 1:
                        notes.append(
                            "Multiple launchers found; same-name paths retain PATH "
                            "priority. Select the launcher matching this FHI-aims build"
                        )
                if launcher is None:
                    notes.append("MPI launcher still needs configuration")
                species_root_candidates = search.species_roots(
                    executable,
                    environment,
                    hints.fhi_species_defaults_path,
                )
                if len(species_root_candidates) == 1:
                    species_root = species_root_candidates[0]
                elif len(species_root_candidates) > 1:
                    notes.append(
                        "Multiple FHI-aims species definitions roots found; "
                        "select the root matching this executable"
                    )
                else:
                    notes.append(
                        "FHI-aims species definitions root still needs configuration"
                    )
            candidate = RuntimeCandidate(
                " / ".join(environment.modules) or environment.mode.value,
                environment.modules,
                path,
                executable,
                environment,
                launcher,
                tuple(notes),
                environment_resolved,
                launcher_candidates,
                dependency.missing,
                dependency.path_hints,
                species_root,
                species_root_candidates,
            )
            if better_than is not None and not (
                _candidate_gap_key(label, candidate) < better_than
            ):
                continue
            candidates[label].append(candidate)
            added.append(candidate)
            if len(candidates[label]) > MAX_CANDIDATES:
                raise RuntimeDiscoveryError(
                    "Too many runtime candidates; enter a narrower installation directory"
                )
        return tuple(added)

    def in_environment(
        label,
        environment,
        *,
        seed_paths=(),
        default_sources=True,
        quiet_failures=False,
    ):
        request = requests[label]
        roots = known_roots()
        key = (
            label,
            environment,
            tuple(seed_paths),
            default_sources,
            roots,
        )
        if key in attempted:
            return ()
        attempted.add(key)
        if label == "FHI-aims" and default_sources:
            discover_launchers(environment, quiet=quiet_failures)
        wrapper_targets = set()
        paths = []
        if request.location and default_sources:
            paths.extend(
                search.location_paths(
                    request.location,
                    label,
                    environment,
                    request.kind,
                )
            )
        else:
            paths.extend(seed_paths)
            if default_sources:
                names = _FHI_NAMES if label == "FHI-aims" else _AITRANSS_NAMES
                path_commands = search.paths_on_path(names, environment)
                paths.extend(
                    path
                    for name, values in path_commands.items()
                    if name != "run_aims.mpi"
                    for path in values
                )
                for wrapper in path_commands.get("run_aims.mpi", ()):
                    target = search.wrapper_target(wrapper, environment)
                    if target is not None:
                        wrapper_targets.add(target)
                        paths.append(target)
                if not resolved(label) and roots:
                    paths.extend(search.layout_paths(roots, label, environment))
                if environment == _NONE and not paths:
                    paths.extend(
                        search.advertised_layout_paths(label, environment)
                    )
        added = list(
            add_paths(
                label,
                environment,
                paths,
                wrapper_targets=wrapper_targets,
                quiet_failures=quiet_failures,
            )
        )
        if (
            default_sources
            and not request.location
            and not added
        ):
            added.extend(
                add_paths(
                    label,
                    environment,
                    search.path_family(label, environment),
                    quiet_failures=quiet_failures,
                )
            )
        return tuple(added)

    def runtime_module_environments():
        """List only modules advertised under the two scientific program names."""

        contexts = set()
        prefixes = tuple(
            module
            for module in normalized_current_modules
            if "aims" not in module.casefold() and "aitranss" not in module.casefold()
        )
        tiers = (
            ("avail", ("aims", "aitranss")),
            ("avail", ("AIMS", "AITRANSS")),
            ("spider", ("aims", "aitranss")),
            ("spider", ("AIMS", "AITRANSS")),
        )
        for subcommand, terms in tiers:
            for prefix in dict.fromkeys(((), prefixes)):
                result = search.execute_command(
                    build_module_listing_command(subcommand, terms, prefix)
                )
                if result.exit_status != 0:
                    continue
                batch = []
                for module in parse_terse_module_listing(search.text(result)):
                    modules = tuple(dict.fromkeys((*prefix, module)))
                    environment = RuntimeEnvironment(
                        RuntimeEnvironmentMode.MODULES,
                        modules,
                    )
                    if environment in contexts:
                        continue
                    contexts.add(environment)
                    batch.append(environment)
                    if len(contexts) >= MAX_PROGRAM_MODULE_ENVIRONMENTS:
                        search.diagnostics.append(
                            "Program-module discovery reached its bounded environment limit; "
                            "enter a known program module manually if the required version was omitted"
                        )
                        if batch:
                            yield tuple(batch)
                        return
                if batch:
                    yield tuple(batch)

    def support_module_batches(base, terms):
        """List only families justified by an observed missing dependency/launcher."""

        prefix = (
            base.environment.modules
            if base.environment is not None
            and base.environment.mode is RuntimeEnvironmentMode.MODULES
            else ()
        )
        queries = (
            _build_filtered_module_catalog_command(terms, prefix),
            build_module_listing_command("spider", terms, prefix),
        )
        for command in queries:
            result = search.execute_command(command)
            if result.exit_status != 0:
                continue
            listed, truncated = _parse_support_module_listing(
                search.text(result),
                terms,
                limit=MAX_MODULE_CATALOG_LINES,
            )
            if truncated:
                search.diagnostics.append(
                    "Support-module listing was capped at "
                    f"{MAX_MODULE_CATALOG_LINES} relevant candidates; "
                    "partial directed results retained"
                )
            if listed:
                yield prefix, listed
                return

    def resolve_support_modules(label):
        """Extend environments only along strictly improving observable gap states."""

        if requests[label].environment.mode is not RuntimeEnvironmentMode.AUTO:
            return
        frontier = _best_partial_frontier(label, candidates[label])
        seen = {
            (item.executable_path, item.environment)
            for item in candidates[label]
        }
        preferred_modules = ()
        if label == "AITRANSS":
            preferred_modules = tuple(
                dict.fromkeys(
                    module
                    for item in candidates["FHI-aims"]
                    if candidate_ready("FHI-aims", item)
                    for module in item.modules
                )
            )
        for _depth in range(MAX_SUPPORT_DEPTH):
            improved = []
            for base in frontier:
                unmet = _candidate_unmet(label, base)
                gap = _candidate_gap_key(label, base)
                term_groups = _support_module_term_groups(unmet)
                if not term_groups:
                    continue
                base_improved = False
                for terms in term_groups:
                    for prefix, modules in support_module_batches(base, terms):
                        ranked_modules = _rank_support_modules(
                            modules,
                            base,
                            preferred_modules,
                        )
                        for module in ranked_modules[:MAX_SUPPORT_MODULE_CANDIDATES]:
                            if module in prefix:
                                continue
                            environment = RuntimeEnvironment(
                                RuntimeEnvironmentMode.MODULES,
                                tuple(dict.fromkeys((*prefix, module))),
                            )
                            identity = (base.executable_path, environment)
                            if identity in seen:
                                continue
                            if support_probe_counts[label] >= MAX_SUPPORT_MODULE_PROBES:
                                search.diagnostics.append(
                                    "Evidence-guided support-module probing reached its bounded "
                                    "candidate limit; enter the remaining module sequence manually"
                                )
                                return
                            if not optional_budget_available(
                                f"{label} support-module discovery"
                            ):
                                return
                            seen.add(identity)
                            support_probe_counts[label] += 1
                            found = add_paths(
                                label,
                                environment,
                                (base.executable_path,),
                                better_than=gap,
                                quiet_failures=True,
                            )
                            if not found:
                                continue
                            improved.extend(found)
                            base_improved = True
                            if any(candidate_ready(label, item) for item in found):
                                return
                            # Recompute the observed gap before trying another
                            # dependency family.  Do not enumerate equivalent
                            # MPI or BLAS implementations after one improved.
                            break
                        if base_improved:
                            break
                    if base_improved:
                        break
            if not improved:
                return
            frontier = _best_partial_frontier(label, improved)
            if resolved(label):
                return

    def reuse_fhi_environment_for_aitranss():
        """Search AITRANSS again after FHI support modules establish an environment."""

        if not needs_automatic_search("AITRANSS"):
            return
        reusable_environments = tuple(
            dict.fromkeys(
                item.environment
                for item in candidates["FHI-aims"]
                if item.environment is not None
                and candidate_ready("FHI-aims", item)
            )
        )
        known_paths = tuple(
            dict.fromkeys(item.executable_path for item in candidates["AITRANSS"])
        )
        if indexed is not None:
            known_paths = tuple(
                dict.fromkeys((*known_paths, *indexed["AITRANSS"]))
            )
        for environment in reusable_environments:
            in_environment(
                "AITRANSS",
                environment,
                seed_paths=known_paths,
                default_sources=True,
                quiet_failures=True,
            )
            if resolved("AITRANSS"):
                return

    initial_environments = {
        label: environments_for(request) for label, request in requests.items()
    }
    indexed = None
    try:
        # Stage 1: user constraints, PATH, wrappers, and sibling layouts.
        for label, request in requests.items():
            environments = initial_environments[label]
            if label == "AITRANSS" and request.environment.mode is RuntimeEnvironmentMode.AUTO:
                environments = tuple(
                    dict.fromkeys(
                        (*(
                            item.environment
                            for item in candidates["FHI-aims"]
                            if item.environment is not None
                        ), *environments)
                    )
                )
            for environment in environments:
                in_environment(label, environment)
                if resolved(label):
                    break

        # Stage 2: query an existing filename index once. Absence never triggers find /.
        missing_path_labels = tuple(
            label
            for label in requests
            if needs_automatic_search(label)
            and not requests[label].location
            and not candidates[label]
        )
        if missing_path_labels:
            indexed = search.indexed_paths()
            for label in missing_path_labels:
                if (
                    not needs_automatic_search(label)
                    or requests[label].location
                    or not indexed[label]
                ):
                    continue
                environments = tuple(
                    dict.fromkeys(
                        (*(
                            item.environment
                            for item in candidates[label]
                            if item.environment is not None
                        ), *initial_environments[label])
                    )
                )
                for environment in environments:
                    in_environment(
                        label,
                        environment,
                        seed_paths=indexed[label],
                        default_sources=False,
                    )
                    if resolved(label):
                        break

            # Newly indexed paths can expose a conventional sibling installation.
            for label in missing_path_labels:
                if not needs_automatic_search(label) or requests[label].location:
                    continue
                for environment in initial_environments[label]:
                    in_environment(label, environment)
                    if resolved(label):
                        break

        # Stage 3: complete known binaries from their observed dependency gaps
        # before spending the budget on program-name module alternatives.
        if needs_automatic_search("FHI-aims") and candidates["FHI-aims"]:
            resolve_support_modules("FHI-aims")
        if needs_automatic_search("AITRANSS") and candidates["AITRANSS"]:
            resolve_support_modules("AITRANSS")
        reuse_fhi_environment_for_aitranss()

        # Stage 4: program-named module metadata remains a direct source for
        # programs which the cheaper path/evidence stages did not complete.
        if any(needs_automatic_search(label) for label in requests):
            program_environment_count = 0
            stop_program_modules = False
            for environments in runtime_module_environments():
                pending = tuple(
                    label for label in requests if needs_automatic_search(label)
                )
                for environment in environments:
                    if program_environment_count >= MAX_PROGRAM_MODULE_ENVIRONMENTS:
                        stop_program_modules = True
                        break
                    if not optional_budget_available(
                        "Program-module discovery",
                        estimated_calls=6,
                    ):
                        stop_program_modules = True
                        break
                    program_environment_count += 1
                    for label in pending:
                        in_environment(
                            label,
                            environment,
                            quiet_failures=True,
                        )
                if stop_program_modules:
                    break
                if not any(needs_automatic_search(label) for label in requests):
                    break

        # Stage 5: complete candidates introduced by program modules, then let
        # AITRANSS reuse the resulting FHI environment. No MPI x MKL product is built.
        for label in requests:
            if not needs_automatic_search(label):
                continue
            if candidates[label]:
                resolve_support_modules(label)
        reuse_fhi_environment_for_aitranss()
    except _SearchLimitReached as error:
        if not any(candidates.values()):
            raise
        search.diagnostics.append(str(error) + "; partial results retained")

    final_candidates = {}
    for label, items in candidates.items():
        final_candidates[label] = _retain_best_candidates(label, items)
        if not final_candidates[label]:
            search.diagnostics.append(
                f"{label}: no executable candidate found through the advertised search sources"
            )
        elif not any(candidate_ready(label, item) for item in final_candidates[label]):
            search.diagnostics.append(
                f"{label}: located candidate(s) remain incomplete and were not auto-applied"
            )
    missing = _missing_runtime_requirements(
        requests,
        final_candidates,
        hints,
        search.index_available,
        tuple(launcher_evidence),
    )
    if not final_candidates["FHI-aims"] and launcher_evidence:
        search.diagnostics.append(
            "MPI launcher candidate(s) found independently, but compatibility cannot be "
            "associated until a FHI-aims executable is selected: "
            + ", ".join(dict.fromkeys(item[1] for item in launcher_evidence))
        )
    return RuntimeDiscoveryResult(
        final_candidates["FHI-aims"],
        final_candidates["AITRANSS"],
        tuple(dict.fromkeys(search.diagnostics)),
        missing,
    )


def _candidate_unmet(label, candidate):
    unmet = set(candidate.missing_dependencies) if not candidate.environment_resolved else set()
    if not candidate.environment_resolved and not unmet:
        unmet.add("__environment__")
    if (
        label == "FHI-aims"
        and not candidate.launcher_path
        and not candidate.launcher_candidates
    ):
        unmet.add("__launcher__")
    if label == "FHI-aims" and not candidate.species_root_path:
        unmet.add("__species_root__")
    return frozenset(unmet)


def _candidate_gap_key(label, candidate):
    """Order observable gaps without requiring every intermediate set to nest."""

    missing_dependencies = (
        candidate.missing_dependencies
        if not candidate.environment_resolved
        else ()
    )
    dependency_metadata_unknown = int(
        not candidate.environment_resolved and not missing_dependencies
    )
    launcher_missing = int(
        label == "FHI-aims"
        and not candidate.launcher_path
        and not candidate.launcher_candidates
    )
    species_root_missing = int(
        label == "FHI-aims" and not candidate.species_root_path
    )
    return (
        dependency_metadata_unknown,
        len(missing_dependencies),
        launcher_missing,
        species_root_missing,
    )


def _best_partial_frontier(label, items):
    """Keep bounded, meaningfully distinct partial states for module completion."""

    ordered = sorted(
        (item for item in items if _candidate_unmet(label, item)),
        key=lambda item: (_candidate_gap_key(label, item), len(item.modules)),
    )
    frontier = []
    signatures = set()
    for item in ordered:
        signature = (
            item.executable_path,
            _candidate_unmet(label, item),
            item.launcher_path,
            item.launcher_candidates,
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        frontier.append(item)
        if len(frontier) >= MAX_SUPPORT_FRONTIER:
            break
    return tuple(frontier)


def _support_module_terms(unmet):
    return tuple(
        dict.fromkeys(
            term
            for group in _support_module_term_groups(unmet)
            for term in group
        )
    )


def _support_module_term_groups(unmet):
    """Keep dependency families separate and defer launcher-only MPI search."""

    lowered = " ".join(item.casefold() for item in unmet)
    groups = []
    if any(token in lowered for token in ("mkl", "scalapack", "blas", "lapack")):
        groups.append(("mkl", "scalapack"))
    if any(
        token in lowered
        for token in ("ifcore", "ifport", "imf", "irc", "svml", "intlc", "iomp")
    ):
        groups.append(("intel", "oneapi", "compiler"))
    if any(token in lowered for token in ("gfortran", "quadmath", "gomp")):
        groups.append(("gcc", "compiler"))
    if any(token in lowered for token in ("mpi", "fabric", "open-pal", "open-rte")):
        groups.append(("mpi", "openmpi"))
    elif "__launcher__" in unmet:
        groups.append(("mpi", "openmpi"))
    return tuple(groups)


def _build_filtered_module_catalog_command(terms, prefix=()):
    """Read one module catalog and filter anywhere in namespaced module names."""

    # Reuse the public builder's validation boundary for both search terms and
    # prefix module names.  Some Tcl Modules sites do not match `avail mkl`
    # against `apps/mkl-*`, so the production query must not depend on that
    # implementation-specific prefix behavior.
    build_module_listing_command("avail", tuple(terms), tuple(prefix))
    modules = tuple(normalize_module_name(module) for module in prefix)
    regex = "|".join(re.escape(term) for term in terms)
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
            ": MOLTAGE_MODULE_CATALOG",
            "{ module -t avail 2>&1 || true; } | "
            "grep -Ei -- " + shlex.quote(regex) + " | "
            f"head -n {MAX_MODULE_CATALOG_LINES}",
        )
    )
    return "bash -lc " + shlex.quote("; ".join(commands))


def _rank_support_modules(modules, base, preferred=()):
    """Prefer module names matching versions/families in observed ELF paths."""

    evidence = " ".join(
        (*base.missing_dependencies, *base.dependency_search_paths)
    ).casefold()
    ignored = {
        "apps", "bin", "compiler", "compilers", "core", "intel", "intel64",
        "lib", "linux", "mkl", "mpi", "release", "scalapack", "soft",
    }
    words = {
        token
        for token in re.findall(r"[a-z][a-z0-9]*|\d{4}", evidence)
        if token not in ignored and (len(token) >= 4 or token.isdigit())
    }
    versions = set(re.findall(r"\d{4}(?:\.\d+)+", evidence))

    preferred_order = {module: index for index, module in enumerate(preferred)}

    def score(index_and_module):
        index, module = index_and_module
        lowered = module.casefold()
        matched_words = sum(2 for token in words if token in lowered)
        matched_versions = sum(5 for token in versions if token in lowered)
        obsolete_penalty = 20 if "obsolete" in lowered else 0
        preferred_rank = preferred_order.get(module)
        return (
            0 if preferred_rank is not None else 1,
            preferred_rank if preferred_rank is not None else 0,
            -(matched_words + matched_versions - obsolete_penalty),
            index,
        )

    return tuple(
        module for _index, module in sorted(enumerate(modules), key=score)
    )


def _compatible_launcher_candidates(launchers, dependency):
    """Keep mpirun only when its installation owns the resolved MPI libraries."""

    mpi_locations = tuple(
        path
        for name, path in dependency.located
        if name.casefold().startswith(("libmpi", "libopen-rte", "libopen-pal"))
    )
    mpi_path_is_explicit = any(
        location == hint.rstrip("/")
        or location.startswith(hint.rstrip("/") + "/")
        for location in mpi_locations
        for hint in dependency.path_hints
    )
    if not mpi_locations or not mpi_path_is_explicit:
        return tuple(launchers)
    compatible = []
    for launcher in launchers:
        path = PurePosixPath(launcher)
        if path.name == "srun":
            compatible.append(launcher)
            continue
        if path.name != "mpirun" or len(path.parents) < 2:
            continue
        prefix = str(path.parent.parent).rstrip("/")
        if any(
            location == prefix or location.startswith(prefix + "/")
            for location in mpi_locations
        ):
            compatible.append(launcher)
    return tuple(compatible)


def _parse_support_module_listing(
    text,
    terms,
    *,
    limit=MAX_SUPPORT_MODULE_CANDIDATES,
):
    """Keep only safe module names whose family matches observed requirements."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("support-module listing limit must be a positive integer")
    modules = []
    truncated = False
    folded_terms = tuple(term.casefold() for term in terms)
    for raw_token in text.split():
        token = raw_token.strip().strip(",")
        while token.endswith(("(D)", "(L)", "(default)")):
            token = token.rsplit("(", 1)[0]
        if token.endswith(":"):
            continue
        token = token.removeprefix("/")
        try:
            module = normalize_module_name(token)
        except Exception:
            continue
        lowered = module.casefold()
        if not any(term in lowered for term in folded_terms) or module in modules:
            continue
        if len(modules) >= limit:
            truncated = True
            continue
        modules.append(module)
    return tuple(modules), truncated


def _retain_best_candidates(label, items):
    """Discard dominated partial environments while retaining verified alternatives."""

    unique = []
    identities = set()
    for item in items:
        identity = (
            item.executable_path,
            item.environment,
            item.launcher_path,
            item.launcher_candidates,
            item.species_root_path,
            item.species_root_candidates,
        )
        if identity not in identities:
            identities.add(identity)
            unique.append(item)
    complete = [item for item in unique if not _candidate_unmet(label, item)]
    if complete:
        return tuple(complete)
    retained = []
    for path in dict.fromkeys(item.executable_path for item in unique):
        same_path = [item for item in unique if item.executable_path == path]
        best_gap = min(_candidate_gap_key(label, item) for item in same_path)
        best = sorted(
            (
                item
                for item in same_path
                if _candidate_gap_key(label, item) == best_gap
            ),
            key=lambda item: len(item.modules),
        )
        signatures = set()
        for item in best:
            signature = (
                _candidate_unmet(label, item),
                item.launcher_path,
                item.launcher_candidates,
                item.species_root_path,
                item.species_root_candidates,
            )
            if signature in signatures:
                continue
            signatures.add(signature)
            retained.append(item)
    return tuple(retained[:MAX_SUPPORT_FRONTIER])


def _missing_runtime_requirements(
    requests,
    candidates,
    hints,
    index_available,
    launcher_evidence=(),
):
    """Describe only unresolved input fields, including target names and UI action."""

    messages = []
    path_missing = False
    for label in ("FHI-aims", "AITRANSS"):
        items = candidates[label]
        complete = [item for item in items if not _candidate_unmet(label, item)]
        if complete:
            continue
        if not items:
            path_missing = True
            names = _FHI_MANUAL_NAMES if label == "FHI-aims" else _AITRANSS_MANUAL_NAMES
            supplied = requests[label].location
            prefix = (
                f"The supplied location {supplied} did not resolve to an executable. "
                if supplied
                else ""
            )
            messages.append(
                f"Missing {label} > Remote path. {prefix}Possible target names: "
                f"{', '.join(names)}. In Manual Configuration > {label}, enter "
                "an installation directory or the exact executable file, then select Find Missing."
            )
            if label == "FHI-aims":
                launcher_paths = tuple(
                    dict.fromkeys(proposed for _environment, proposed, _realpath in launcher_evidence)
                )
                supplied_launcher = (
                    f" The supplied path {hints.mpi_launcher} was not verified."
                    if hints.mpi_launcher and not launcher_paths
                    else ""
                )
                evidence_detail = (
                    " Launcher candidate(s) found independently: "
                    + ", ".join(launcher_paths)
                    + ". Select or enter the compatible one after the FHI-aims "
                    "executable is known; discovery cannot infer MPI/ABI compatibility "
                    "from the launcher alone."
                    if launcher_paths
                    else " Possible target names: mpirun, srun."
                )
                messages.append(
                    "Missing FHI-aims > MPI launcher selection."
                    f"{evidence_detail}{supplied_launcher} In Manual Configuration > "
                    "FHI-aims > MPI launcher, enter the compatible launcher's absolute path."
                )
                messages.append(
                    "Missing FHI-aims > Species definitions root. Enter a "
                    "canonical absolute remote directory whose immediate children "
                    "include light, tight, and really_tight."
                )
            continue
        best_size = min(len(_candidate_unmet(label, item)) for item in items)
        best = [item for item in items if len(_candidate_unmet(label, item)) == best_size]
        if not any(item.environment_resolved for item in best):
            libraries = tuple(
                dict.fromkeys(
                    library
                    for item in best
                    for library in item.missing_dependencies
                )
            )
            detail = (
                " Direct libraries still unresolved: " + ", ".join(libraries) + "."
                if libraries
                else " Dependency metadata could not establish a runnable environment."
            )
            messages.append(
                f"Missing {label} > Environment.{detail} In Manual Configuration > "
                f"{label} > Environment, choose Modules and enter compatible module names "
                "in load order, or choose Setup script and enter an explicitly trusted "
                "environment-only script."
            )
        if label == "FHI-aims" and not any(
            item.launcher_path or item.launcher_candidates for item in best
        ):
            supplied = (
                f" The supplied path {hints.mpi_launcher} was not verified."
                if hints.mpi_launcher
                else ""
            )
            messages.append(
                "Missing FHI-aims > MPI launcher. Possible target names: mpirun, srun."
                f"{supplied} In Manual Configuration > FHI-aims > MPI launcher, enter "
                "the compatible launcher's absolute path."
            )
        if label == "FHI-aims" and not any(
            item.species_root_path for item in best
        ):
            roots = tuple(
                dict.fromkeys(
                    root
                    for item in best
                    for root in item.species_root_candidates
                )
            )
            if roots:
                messages.append(
                    "Missing FHI-aims > Species definitions root selection. "
                    "Multiple valid roots were found: "
                    + ", ".join(roots)
                    + ". In Manual Configuration > FHI-aims, select the root "
                    "that belongs to the configured executable."
                )
            else:
                supplied = (
                    " The supplied root was unavailable or did not satisfy the path contract."
                    if hints.fhi_species_defaults_path
                    else ""
                )
                messages.append(
                    "Missing FHI-aims > Species definitions root."
                    + supplied
                    + " Enter a canonical absolute remote directory whose immediate "
                    "children include light, tight, and really_tight."
                )
    if path_missing and index_available is False:
        messages.append(
            "No usable plocate/locate filename index was available. A runtime absent from "
            "PATH, module metadata, and evidence-derived installation layouts cannot be "
            "located without a manual installation-directory hint; no filesystem-wide scan "
            "was attempted."
        )
    return tuple(dict.fromkeys(messages))


def discover_server_runtimes(executor, current_modules=()):
    """Use one search budget while retaining the accepted legacy srun settings UI."""
    result = discover_runtime_configuration(executor, RuntimeDiscoveryHints(), current_modules)
    groups = (
        (result.fhi_aims_candidates, is_fhi_aims_executable_name),
        (result.aitranss_candidates, is_aitranss_executable_name),
    )
    if all(items and all(item.environment.mode is RuntimeEnvironmentMode.MODULES
                         and item.environment_resolved
                         and accepts(PurePosixPath(item.executable_path).name)
                         and PurePosixPath(item.command_v_path).name
                         == PurePosixPath(item.executable_path).name for item in items)
           for items, accepts in groups) and all(
               item.species_root_path is not None
               for item in result.fhi_aims_candidates) and all(
               any(PurePosixPath(path).name == "srun" for path in
                   (*item.launcher_candidates, *((item.launcher_path,) if item.launcher_path else ())))
               for item in result.fhi_aims_candidates):
        # Only canonical, module-backed results use the old presentation path,
        # which preserves user srun flags and existing batch-script semantics.
        return RuntimeDiscoveryResult(
            tuple(replace(item, environment=None) for item in result.fhi_aims_candidates),
            tuple(replace(item, environment=None) for item in result.aitranss_candidates),
            result.diagnostics,
            result.missing_requirements,
        )
    return result
