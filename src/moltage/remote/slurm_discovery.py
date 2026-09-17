"""Generic discovery and verification of one absolute Slurm sbatch path."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
import shlex

from moltage.domain.server_profile import (
    ServerProfileValidationError,
    SlurmCommandMode,
    SlurmExecutionPreset,
    validate_lsf_env_directory,
    validate_lsf_library_directory,
    validate_lsf_server_directory,
    validate_slurm_bin_directory,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.remote.executor import RemoteExecutor


class SlurmDiscoveryError(RuntimeError):
    """Raised when neither bounded discovery strategy finds verified sbatch."""


class SlurmVerificationError(RuntimeError):
    """Raised when one exact absolute sbatch candidate cannot be verified."""


class SlurmConfigurationError(RuntimeError):
    """Raised when an explicitly configured Manual Slurm location is unusable."""

    def __init__(self, bin_directory: str, message: str) -> None:
        super().__init__(message)
        self.bin_directory = bin_directory


class SlurmDiscoverySource(StrEnum):
    CURRENT_ENVIRONMENT = "CURRENT_ENVIRONMENT"
    LOGIN_SHELL = "LOGIN_SHELL"
    CACHED_AUTOMATIC = "CACHED_AUTOMATIC"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class SlurmDiscoveryResult:
    sbatch_path: str
    bin_directory: str
    version_text: str
    discovery_source: SlurmDiscoverySource
    scheduler_kind: SchedulerKind = SchedulerKind.SLURM
    lsf_env_directory: str | None = None
    lsf_library_directory: str | None = None
    lsf_server_directory: str | None = None

    def __post_init__(self) -> None:
        kind = SchedulerKind(self.scheduler_kind)
        source = SlurmDiscoverySource(self.discovery_source)
        object.__setattr__(self, "scheduler_kind", kind)
        object.__setattr__(self, "discovery_source", source)
        if kind is SchedulerKind.LSF:
            if any(
                value is None
                for value in (
                    self.lsf_env_directory,
                    self.lsf_library_directory,
                    self.lsf_server_directory,
                )
            ):
                raise ValueError(
                    "A verified LSF scheduler requires its configuration, "
                    "library, and server directories"
                )
            object.__setattr__(
                self,
                "lsf_env_directory",
                validate_lsf_env_directory(self.lsf_env_directory),
            )
            object.__setattr__(
                self,
                "lsf_library_directory",
                validate_lsf_library_directory(self.lsf_library_directory),
            )
            object.__setattr__(
                self,
                "lsf_server_directory",
                validate_lsf_server_directory(self.lsf_server_directory),
            )
        elif any(
            value is not None
            for value in (
                self.lsf_env_directory,
                self.lsf_library_directory,
                self.lsf_server_directory,
            )
        ):
            raise ValueError(
                "LSF client directories are valid only for an LSF result"
            )

    @property
    def submit_path(self) -> str:
        """Return the verified submit command for either supported scheduler."""

        return self.sbatch_path


CURRENT_ENVIRONMENT_DISCOVERY_COMMAND = "command -v sbatch"
_LOGIN_SHELL_INNER_COMMAND = (
    'candidate=$(command -v sbatch 2>/dev/null) && '
    'printf "__MOLTAGE_SBATCH__=%s\\n" "$candidate"'
)
LOGIN_SHELL_DISCOVERY_COMMAND = (
    "bash -lc " + shlex.quote(_LOGIN_SHELL_INNER_COMMAND)
)
LOGIN_SHELL_RESULT_MARKER = "__MOLTAGE_SBATCH__="


def discover_slurm(executor: RemoteExecutor) -> SlurmDiscoveryResult:
    """Try the current exec environment, then one marked login-shell lookup."""

    current = _execute_for_discovery(
        executor,
        CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
    )
    candidate = _current_environment_candidate(current)
    if candidate is not None:
        try:
            return verify_sbatch_path(
                executor,
                candidate,
                SlurmDiscoverySource.CURRENT_ENVIRONMENT,
            )
        except SlurmVerificationError:
            pass

    login_shell = _execute_for_discovery(
        executor,
        LOGIN_SHELL_DISCOVERY_COMMAND,
    )
    candidate = _marked_login_shell_candidate(login_shell)
    if candidate is not None:
        try:
            return verify_sbatch_path(
                executor,
                candidate,
                SlurmDiscoverySource.LOGIN_SHELL,
            )
        except SlurmVerificationError:
            pass

    raise SlurmDiscoveryError(
        "Slurm could not be detected automatically in the remote environment "
        "or its login shell."
    )


def resolve_slurm_for_submission(
    executor: RemoteExecutor,
    preset: SlurmExecutionPreset,
) -> SlurmDiscoveryResult:
    """Compatibility facade resolving the preset's declared scheduler."""

    from moltage.remote.scheduler_discovery import (
        resolve_scheduler_for_submission,
    )

    return resolve_scheduler_for_submission(executor, preset)


def _resolve_slurm_for_submission_only(
    executor: RemoteExecutor,
    preset: SlurmExecutionPreset,
) -> SlurmDiscoveryResult:
    """Resolve Slurm only; used by the generic scheduler facade."""

    if not isinstance(preset, SlurmExecutionPreset):
        raise TypeError("Slurm preflight requires a SlurmExecutionPreset")

    if preset.slurm_command_mode is SlurmCommandMode.MANUAL:
        directory = preset.slurm_bin_directory
        if directory is None:
            raise SlurmConfigurationError(
                "",
                "Manual Slurm command mode requires a command directory.",
            )
        try:
            return verify_sbatch_path(
                executor,
                slurm_command_path(preset, "sbatch"),
                SlurmDiscoverySource.MANUAL,
            )
        except SlurmVerificationError as error:
            raise SlurmConfigurationError(
                directory,
                f"The configured Slurm command directory is invalid: {directory}. "
                f"{error}",
            ) from None

    if preset.slurm_bin_directory is not None:
        try:
            return verify_sbatch_path(
                executor,
                slurm_command_path(preset, "sbatch"),
                SlurmDiscoverySource.CACHED_AUTOMATIC,
            )
        except SlurmVerificationError:
            pass
    return discover_slurm(executor)


def verify_sbatch_path(
    executor: RemoteExecutor,
    candidate: str,
    discovery_source: SlurmDiscoverySource,
) -> SlurmDiscoveryResult:
    """Execute one exact absolute candidate with ``--version`` and validate it."""

    sbatch_path = validate_sbatch_path(candidate)
    try:
        source = SlurmDiscoverySource(discovery_source)
    except (TypeError, ValueError):
        raise TypeError("invalid Slurm discovery source") from None
    result = executor.execute(f"{shlex.quote(sbatch_path)} --version")
    if result.exit_status != 0:
        raise SlurmVerificationError(
            f"{sbatch_path} --version returned a nonzero status."
        )
    version_text = _plausible_version_text(result.stdout, result.stderr)
    if version_text is None:
        raise SlurmVerificationError(
            f"{sbatch_path} did not return a plausible Slurm version."
        )
    bin_directory = str(PurePosixPath(sbatch_path).parent)
    return SlurmDiscoveryResult(
        sbatch_path,
        bin_directory,
        version_text,
        source,
        SchedulerKind.SLURM,
    )


def slurm_command_path(
    preset: SlurmExecutionPreset,
    command_name: str,
) -> str:
    """Derive one supported absolute scheduler command path."""

    if not isinstance(preset, SlurmExecutionPreset):
        raise TypeError("Slurm command path requires a SlurmExecutionPreset")
    if command_name not in _SUPPORTED_SLURM_COMMANDS:
        raise ValueError(
            "supported Slurm commands are sbatch, squeue, sacct, and scancel"
        )
    if preset.slurm_bin_directory is None:
        raise SlurmVerificationError("Slurm command directory is not configured")
    actual_name = (
        _LSF_COMMAND_MAP[command_name]
        if preset.scheduler_kind is SchedulerKind.LSF
        else command_name
    )
    return validate_scheduler_command_path(
        str(PurePosixPath(preset.slurm_bin_directory) / actual_name),
        actual_name,
    )


def validate_sbatch_path(value: object) -> str:
    """Validate one unambiguous absolute POSIX path whose basename is sbatch."""

    return validate_slurm_command_path(value, "sbatch")


def validate_slurm_command_path(value: object, command_name: str) -> str:
    """Validate an absolute path for one narrowly supported Slurm command."""

    if command_name not in _SUPPORTED_SLURM_COMMANDS:
        raise ValueError(
            "supported Slurm commands are sbatch, squeue, sacct, and scancel"
        )

    if not isinstance(value, str) or not value or value != value.strip():
        raise SlurmVerificationError(
            f"{command_name} must be one absolute executable path"
        )
    if "\x00" in value or "\n" in value or "\r" in value:
        raise SlurmVerificationError(
            f"{command_name} has an unsafe executable path"
        )
    path = PurePosixPath(value)
    if not path.is_absolute() or path.name != command_name:
        raise SlurmVerificationError(
            f"{command_name} must have an absolute path ending in /{command_name}"
        )
    try:
        bin_directory = validate_slurm_bin_directory(str(path.parent))
    except ServerProfileValidationError as error:
        raise SlurmVerificationError(str(error)) from None
    normalized = str(PurePosixPath(bin_directory) / command_name)
    if normalized != value:
        raise SlurmVerificationError(
            f"{command_name} has a non-canonical executable path"
        )
    return normalized


def validate_scheduler_command_path(value: object, command_name: str) -> str:
    """Validate one command from the complete Slurm or LSF command sets."""

    if command_name in _SUPPORTED_SLURM_COMMANDS:
        return validate_slurm_command_path(value, command_name)
    if command_name not in _SUPPORTED_LSF_COMMANDS:
        raise ValueError("unsupported scheduler command")
    if not isinstance(value, str) or not value or value != value.strip():
        raise SlurmVerificationError(
            f"{command_name} must be one absolute executable path"
        )
    if "\x00" in value or "\n" in value or "\r" in value:
        raise SlurmVerificationError(
            f"{command_name} has an unsafe executable path"
        )
    path = PurePosixPath(value)
    if not path.is_absolute() or path.name != command_name:
        raise SlurmVerificationError(
            f"{command_name} must have an absolute path ending in /{command_name}"
        )
    try:
        bin_directory = validate_slurm_bin_directory(str(path.parent))
    except ServerProfileValidationError as error:
        raise SlurmVerificationError(str(error)) from None
    normalized = str(PurePosixPath(bin_directory) / command_name)
    if normalized != value:
        raise SlurmVerificationError(
            f"{command_name} has a non-canonical executable path"
        )
    return normalized


def _execute_for_discovery(executor: RemoteExecutor, command: str):
    return executor.execute(command)


def _current_environment_candidate(result) -> str | None:
    if result is None or result.exit_status != 0:
        return None
    text = _decode_utf8(result.stdout)
    if text is None:
        return None
    candidate = text.rstrip("\r\n")
    if not candidate or "\n" in candidate or "\r" in candidate:
        return None
    try:
        return validate_sbatch_path(candidate)
    except SlurmVerificationError:
        return None


def _marked_login_shell_candidate(result) -> str | None:
    if result is None or result.exit_status != 0:
        return None
    text = _decode_utf8(result.stdout)
    if text is None:
        return None
    marked = [
        line[len(LOGIN_SHELL_RESULT_MARKER) :]
        for line in text.splitlines()
        if line.startswith(LOGIN_SHELL_RESULT_MARKER)
    ]
    if len(marked) != 1:
        return None
    try:
        return validate_sbatch_path(marked[0])
    except SlurmVerificationError:
        return None


def _plausible_version_text(stdout: bytes, stderr: bytes) -> str | None:
    for data in (stdout, stderr):
        text = _decode_utf8(data)
        if text is None:
            continue
        for line in text.splitlines():
            candidate = line.strip()
            if candidate and _SLURM_VERSION_WORD.search(candidate):
                return candidate[:300]
    return None


def _decode_utf8(data: object) -> str | None:
    if not isinstance(data, bytes):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeError:
        return None


_SLURM_VERSION_WORD = re.compile(r"(?<![A-Za-z0-9])slurm(?![A-Za-z0-9])", re.I)
_SUPPORTED_SLURM_COMMANDS = frozenset(
    {"sbatch", "squeue", "sacct", "scancel"}
)
_SUPPORTED_LSF_COMMANDS = frozenset(
    {"bsub", "bjobs", "bhist", "bkill", "lsid"}
)
_LSF_COMMAND_MAP = {
    "sbatch": "bsub",
    "squeue": "bjobs",
    "sacct": "bhist",
    "scancel": "bkill",
}
