"""Validated non-secret server and cluster-execution configuration."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import StrEnum
import math
from pathlib import PurePosixPath
import re
from uuid import UUID

from moltage.domain.scheduler import SchedulerKind
from moltage.orca.catalog import OrcaVersionEvidence


class ServerProfileValidationError(ValueError):
    """Raised when a server profile or execution preset is invalid."""


_MODULE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]*")
_SCHEDULER_SITE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+@/-]*")
_SCHEDULER_OUTPUT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHELL_CONTROL_CHARACTERS = frozenset(";&|<>`$(){}[]*?!\\\"'")
_EMAIL_LOCAL_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*")
_EMAIL_DOMAIN_LABEL = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)


class SlurmCommandMode(StrEnum):
    """How one server profile locates its Slurm command directory."""

    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"


class LsfResourceRequirementMode(StrEnum):
    """The two supported LSF resource-request policies."""

    SITE_DEFAULT = "SITE_DEFAULT"
    SPAN_RUSAGE = "SPAN_RUSAGE"


class SlurmAitranssLaunchMode(StrEnum):
    """The only supported Step 4 launch policies on Slurm."""

    DIRECT = "DIRECT"
    SRUN = "SRUN"


class RuntimeEnvironmentMode(StrEnum):
    AUTO = "AUTO"
    NONE = "NONE"
    MODULES = "MODULES"
    SCRIPT = "SCRIPT"


class RuntimeLocationKind(StrEnum):
    DIRECTORY = "DIRECTORY"
    EXECUTABLE = "EXECUTABLE"


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    """One explicit environment, or an unresolved discovery request."""

    mode: RuntimeEnvironmentMode = RuntimeEnvironmentMode.AUTO
    modules: tuple[str, ...] = ()
    setup_script: str | None = None

    def __post_init__(self) -> None:
        mode = RuntimeEnvironmentMode(self.mode)
        modules = tuple(normalize_module_name(item) for item in self.modules)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "modules", modules)
        if mode is RuntimeEnvironmentMode.MODULES:
            if not modules:
                raise ServerProfileValidationError("Enter at least one environment module")
        elif modules:
            raise ServerProfileValidationError("Modules require the Modules environment mode")
        if mode is RuntimeEnvironmentMode.SCRIPT:
            object.__setattr__(self, "setup_script", validate_remote_executable_path(
                self.setup_script, "Environment setup script"
            ))
        elif self.setup_script is not None:
            raise ServerProfileValidationError("A setup script requires the Setup script mode")


@dataclass(frozen=True, slots=True)
class RuntimeLocation:
    """A user-supplied directory/file constraint, not a verified executable."""

    location: str = ""
    environment: RuntimeEnvironment = field(default_factory=RuntimeEnvironment)
    kind: RuntimeLocationKind = RuntimeLocationKind.DIRECTORY

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RuntimeLocationKind(self.kind))
        if not isinstance(self.location, str):
            raise ServerProfileValidationError("Runtime location must be text")
        if self.location.strip():
            object.__setattr__(self, "location", validate_remote_executable_path(
                self.location.strip().rstrip("/"), "Runtime location"
            ))
        else:
            object.__setattr__(self, "location", "")
        if not isinstance(self.environment, RuntimeEnvironment):
            raise ServerProfileValidationError("Runtime environment is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeDiscoveryHints:
    """Partial per-server inputs shared by manual configuration and discovery."""

    fhi_aims: RuntimeLocation = field(default_factory=RuntimeLocation)
    mpi_launcher: str = ""
    aitranss: RuntimeLocation = field(default_factory=RuntimeLocation)
    fhi_species_defaults_path: str | None = None
    orca: RuntimeLocation = field(default_factory=RuntimeLocation)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.fhi_aims, RuntimeLocation)
            or not isinstance(self.aitranss, RuntimeLocation)
            or not isinstance(self.orca, RuntimeLocation)
        ):
            raise ServerProfileValidationError("Runtime search locations are invalid")
        if not isinstance(self.mpi_launcher, str):
            raise ServerProfileValidationError("MPI launcher must be text")
        if self.mpi_launcher.strip():
            launcher = validate_remote_executable_path(self.mpi_launcher, "MPI launcher")
            runtime_launcher_kind(launcher)
            object.__setattr__(self, "mpi_launcher", launcher)
        else:
            object.__setattr__(self, "mpi_launcher", "")
        species_path = self.fhi_species_defaults_path
        if species_path is not None:
            species_path = validate_fhi_species_defaults_path(species_path)
        object.__setattr__(self, "fhi_species_defaults_path", species_path)


@dataclass(frozen=True, slots=True)
class FhiAimsRuntimeConfiguration:
    """Resolved launch inputs. File discovery is not execution acceptance."""

    executable_path: str
    launcher_path: str
    environment: RuntimeEnvironment

    def __post_init__(self) -> None:
        for name in ("executable_path", "launcher_path"):
            object.__setattr__(self, name, validate_remote_executable_path(
                getattr(self, name), name.replace("_", " ")
            ))
        runtime_launcher_kind(self.launcher_path)
        if not isinstance(self.environment, RuntimeEnvironment) or self.environment.mode is RuntimeEnvironmentMode.AUTO:
            raise ServerProfileValidationError("Resolve the FHI-aims environment before submitting")


def runtime_launcher_kind(path: str) -> str:
    """Recognize only the two approved launcher interfaces, never shell commands."""

    name = PurePosixPath(path).name
    if name in {"srun", "mpirun"}:
        return name
    raise ServerProfileValidationError("MPI launcher must be an srun or mpirun executable path")


@dataclass(frozen=True, slots=True)
class SlurmExecutionPreset:
    """Structured scheduler settings; the historical name remains API-compatible.

    ``memory_gb`` is scheduler-qualified: it is a per-node memory limit for
    Slurm and an ``rusage[mem=]`` scheduling reservation for LSF, whose scope
    follows the site's LSF configuration.  The persisted ``scheduler_kind``
    therefore always owns the meaning of the resource values; callers must
    never reinterpret a preset through another scheduler.  LSF additionally
    persists the directories needed to initialize the LSF client without a
    login/setup script; they are not runtime-module or calculation-environment
    configuration.
    """

    nodes: int
    ntasks: int
    cpus_per_task: int
    runtime_minutes: int
    memory_gb: int
    no_requeue: bool = True
    export_none: bool = True
    unset_slurm_export_env: bool = True
    omp_num_threads: int = 1
    module_purge: bool = True
    modules: tuple[str, ...] = ()
    launch_command: str = ""
    slurm_output_filename: str = ""
    slurm_command_mode: SlurmCommandMode = SlurmCommandMode.AUTOMATIC
    slurm_bin_directory: str | None = None
    fhi_runtime: FhiAimsRuntimeConfiguration | None = None
    scheduler_kind: SchedulerKind = SchedulerKind.SLURM
    lsf_env_directory: str | None = None
    lsf_library_directory: str | None = None
    lsf_server_directory: str | None = None
    fhi_species_defaults_path: str | None = None
    slurm_account: str | None = None
    slurm_partition: str | None = None
    slurm_qos: str | None = None
    lsf_queue: str | None = None
    lsf_project: str | None = None
    lsf_resource_requirement_mode: LsfResourceRequirementMode | None = None
    slurm_aitranss_launch_mode: SlurmAitranssLaunchMode | None = None
    slurm_aitranss_srun_path: str | None = None

    def __post_init__(self) -> None:
        try:
            scheduler_kind = SchedulerKind(self.scheduler_kind)
        except (TypeError, ValueError):
            raise ServerProfileValidationError(
                "Scheduler type must be SLURM or LSF"
            ) from None
        object.__setattr__(self, "scheduler_kind", scheduler_kind)
        if self.fhi_runtime is not None and not isinstance(self.fhi_runtime, FhiAimsRuntimeConfiguration):
            raise ServerProfileValidationError("FHI-aims runtime configuration is invalid")
        for field_name in (
            "nodes",
            "ntasks",
            "cpus_per_task",
            "runtime_minutes",
            "memory_gb",
            "omp_num_threads",
        ):
            _require_positive_int(getattr(self, field_name), field_name)
        for field_name in (
            "no_requeue",
            "export_none",
            "unset_slurm_export_env",
            "module_purge",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ServerProfileValidationError(
                    f"{field_name.replace('_', ' ')} must be a boolean"
                )

        modules = tuple(normalize_module_name(item) for item in self.modules)
        object.__setattr__(self, "modules", modules)

        launch_command = (
            ""
            if isinstance(self.launch_command, str) and not self.launch_command.strip()
            else _single_line_text(self.launch_command, "launch command")
        )
        object.__setattr__(self, "launch_command", launch_command)

        object.__setattr__(
            self,
            "slurm_output_filename",
            normalize_scheduler_output_filename(
                self.slurm_output_filename,
                allow_empty=True,
            ),
        )

        try:
            command_mode = SlurmCommandMode(self.slurm_command_mode)
        except (TypeError, ValueError):
            raise ServerProfileValidationError(
                "Scheduler command mode must be AUTOMATIC or MANUAL"
            ) from None
        object.__setattr__(self, "slurm_command_mode", command_mode)

        bin_directory = self.slurm_bin_directory
        if bin_directory is not None:
            bin_directory = validate_slurm_bin_directory(bin_directory)
        if command_mode is SlurmCommandMode.MANUAL and bin_directory is None:
            command_name = (
                "sbatch"
                if scheduler_kind is SchedulerKind.SLURM
                else "bsub"
            )
            raise ServerProfileValidationError(
                f"Manual scheduler command mode requires the directory containing {command_name}"
            )
        if scheduler_kind is SchedulerKind.LSF and self.cpus_per_task != 1:
            raise ServerProfileValidationError(
                "LSF execution currently models MPI slots directly; CPUs per task must be 1"
            )
        if scheduler_kind is SchedulerKind.LSF and self.omp_num_threads != 1:
            raise ServerProfileValidationError(
                "LSF FHI-aims execution currently supports pure MPI only; OpenMP threads must be 1"
            )
        if scheduler_kind is SchedulerKind.LSF and self.unset_slurm_export_env:
            raise ServerProfileValidationError(
                "Clearing SLURM_EXPORT_ENV is a Slurm-only setting"
            )
        lsf_env_directory = self.lsf_env_directory
        if lsf_env_directory is not None:
            lsf_env_directory = validate_lsf_env_directory(lsf_env_directory)
        lsf_library_directory = self.lsf_library_directory
        if lsf_library_directory is not None:
            lsf_library_directory = validate_lsf_library_directory(
                lsf_library_directory
            )
        lsf_server_directory = self.lsf_server_directory
        if lsf_server_directory is not None:
            lsf_server_directory = validate_lsf_server_directory(
                lsf_server_directory
            )
        if scheduler_kind is SchedulerKind.SLURM and any(
            value is not None
            for value in (
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            )
        ):
            raise ServerProfileValidationError(
                "LSF client directories are valid only for an LSF preset"
            )
        object.__setattr__(self, "slurm_bin_directory", bin_directory)
        object.__setattr__(self, "lsf_env_directory", lsf_env_directory)
        object.__setattr__(
            self, "lsf_library_directory", lsf_library_directory
        )
        object.__setattr__(
            self, "lsf_server_directory", lsf_server_directory
        )
        for field_name, label in (
            ("slurm_account", "Slurm account"),
            ("slurm_partition", "Slurm partition"),
            ("slurm_qos", "Slurm QoS"),
            ("lsf_queue", "LSF queue"),
            ("lsf_project", "LSF project"),
        ):
            object.__setattr__(
                self,
                field_name,
                normalize_scheduler_site_identifier(
                    getattr(self, field_name),
                    label,
                ),
            )
        if scheduler_kind is SchedulerKind.LSF and any(
            value is not None
            for value in (
                self.slurm_account,
                self.slurm_partition,
                self.slurm_qos,
            )
        ):
            raise ServerProfileValidationError(
                "Slurm site selectors are valid only for a Slurm preset"
            )
        if scheduler_kind is SchedulerKind.SLURM and any(
            value is not None
            for value in (self.lsf_queue, self.lsf_project)
        ):
            raise ServerProfileValidationError(
                "LSF site selectors are valid only for an LSF preset"
            )

        resource_mode = self.lsf_resource_requirement_mode
        if scheduler_kind is SchedulerKind.LSF:
            try:
                resource_mode = (
                    LsfResourceRequirementMode.SITE_DEFAULT
                    if resource_mode is None
                    else LsfResourceRequirementMode(resource_mode)
                )
            except (TypeError, ValueError):
                raise ServerProfileValidationError(
                    "LSF resource requirement mode must be SITE_DEFAULT or SPAN_RUSAGE"
                ) from None
        elif resource_mode is not None:
            raise ServerProfileValidationError(
                "LSF resource requirement mode is valid only for an LSF preset"
            )
        object.__setattr__(self, "lsf_resource_requirement_mode", resource_mode)
        if (
            resource_mode is LsfResourceRequirementMode.SPAN_RUSAGE
            and (self.nodes > self.ntasks or self.ntasks % self.nodes != 0)
        ):
            raise ServerProfileValidationError(
                "LSF MPI job slots must divide evenly across the requested execution hosts"
            )

        launch_mode = self.slurm_aitranss_launch_mode
        if launch_mode is not None:
            try:
                launch_mode = SlurmAitranssLaunchMode(launch_mode)
            except (TypeError, ValueError):
                raise ServerProfileValidationError(
                    "Slurm AITRANSS launch mode must be DIRECT or SRUN"
                ) from None
        srun_path = self.slurm_aitranss_srun_path
        if srun_path is not None:
            if isinstance(srun_path, str) and not srun_path.strip():
                srun_path = None
            else:
                srun_path = validate_srun_launcher_path(srun_path)
        if scheduler_kind is SchedulerKind.LSF and (
            launch_mode is not None or srun_path is not None
        ):
            raise ServerProfileValidationError(
                "Slurm AITRANSS launch settings are valid only for a Slurm preset"
            )
        if launch_mode is None and srun_path is not None:
            raise ServerProfileValidationError(
                "Select SRUN before configuring an AITRANSS srun executable"
            )
        if launch_mode is SlurmAitranssLaunchMode.DIRECT and srun_path is not None:
            raise ServerProfileValidationError(
                "Direct AITRANSS launch must not configure an srun executable"
            )
        object.__setattr__(self, "slurm_aitranss_launch_mode", launch_mode)
        object.__setattr__(self, "slurm_aitranss_srun_path", srun_path)

        species_path = self.fhi_species_defaults_path
        if species_path is not None:
            species_path = validate_fhi_species_defaults_path(species_path)
        object.__setattr__(self, "fhi_species_defaults_path", species_path)


@dataclass(frozen=True, slots=True)
class SlurmMailSettings:
    """One validated recipient for the selected scheduler's native mail policy."""

    recipient: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recipient",
            normalize_email_notification_recipient(self.recipient),
        )


@dataclass(frozen=True, slots=True)
class AitranssRuntimeConfiguration:
    """Verified per-profile module environment and absolute executable path."""

    modules: tuple[str, ...]
    executable_path: str
    environment: RuntimeEnvironment | None = None

    def __post_init__(self) -> None:
        modules = tuple(normalize_module_name(item) for item in self.modules)
        if self.environment is not None:
            if not isinstance(self.environment, RuntimeEnvironment) or self.environment.mode is RuntimeEnvironmentMode.AUTO:
                raise ServerProfileValidationError("Resolve the AITRANSS environment before submitting")
            if modules != self.environment.modules:
                raise ServerProfileValidationError("AITRANSS module and environment settings disagree")
        if not modules and self.environment is None:
            raise ServerProfileValidationError(
                "AITRANSS runtime requires at least one environment module"
            )
        object.__setattr__(self, "modules", modules)
        object.__setattr__(
            self,
            "executable_path",
            validate_remote_executable_path(
                self.executable_path,
                "AITRANSS executable",
            ),
        )


@dataclass(frozen=True, slots=True)
class OrcaRuntimeConfiguration:
    """Verified profile-scoped ORCA driver and environment evidence."""

    executable_path: str
    environment: RuntimeEnvironment
    version_evidence: OrcaVersionEvidence

    def __post_init__(self) -> None:
        path = validate_remote_executable_path(self.executable_path, "ORCA executable")
        if PurePosixPath(path).name != "orca":
            raise ServerProfileValidationError("ORCA executable path must name orca")
        object.__setattr__(self, "executable_path", path)
        if (
            not isinstance(self.environment, RuntimeEnvironment)
            or self.environment.mode is RuntimeEnvironmentMode.AUTO
        ):
            raise ServerProfileValidationError(
                "Resolve the ORCA environment before saving a verified runtime"
            )
        if not isinstance(self.version_evidence, OrcaVersionEvidence):
            raise ServerProfileValidationError("ORCA version evidence is invalid")


@dataclass(frozen=True, slots=True)
class ServerProfile:
    """Persistent server identity and non-secret connection configuration."""

    profile_id: UUID
    name: str
    host: str
    port: int
    username: str
    remote_project_root: str
    save_password: bool
    auto_connect: bool
    execution_preset: SlurmExecutionPreset | None
    email_notification_enabled: bool = False
    email_notification_recipient: str | None = None
    aitranss_runtime: AitranssRuntimeConfiguration | None = None
    runtime_hints: RuntimeDiscoveryHints | None = None
    orca_runtime: OrcaRuntimeConfiguration | None = None

    def __post_init__(self) -> None:
        if self.runtime_hints is not None and not isinstance(self.runtime_hints, RuntimeDiscoveryHints):
            raise ServerProfileValidationError("Runtime discovery hints are invalid")
        if not isinstance(self.profile_id, UUID):
            raise ServerProfileValidationError("profile ID must be a UUID")
        object.__setattr__(self, "name", _trimmed_text(self.name, "profile name"))
        object.__setattr__(self, "host", _single_line_text(self.host, "host"))
        _require_positive_int(self.port, "port")
        if self.port > 65535:
            raise ServerProfileValidationError("port must be at most 65535")
        object.__setattr__(
            self,
            "username",
            _single_line_text(self.username, "username"),
        )
        remote_root = _single_line_text(
            self.remote_project_root,
            "remote project root",
        ).rstrip("/") or "/"
        if not remote_root.startswith("/") or "\\" in remote_root:
            raise ServerProfileValidationError(
                "remote project root must be an absolute POSIX path"
            )
        if any(part in {"", ".", ".."} for part in remote_root.split("/")[1:]):
            if remote_root != "/":
                raise ServerProfileValidationError(
                    "remote project root must not contain empty, '.' or '..' components"
                )
        object.__setattr__(self, "remote_project_root", remote_root)
        for field_name in (
            "save_password",
            "auto_connect",
            "email_notification_enabled",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ServerProfileValidationError(
                    f"{field_name.replace('_', ' ')} must be a boolean"
                )
        if self.execution_preset is not None and not isinstance(
            self.execution_preset,
            SlurmExecutionPreset,
        ):
            raise ServerProfileValidationError(
                "execution preset must be a SlurmExecutionPreset or None"
            )
        if self.aitranss_runtime is not None and not isinstance(
            self.aitranss_runtime,
            AitranssRuntimeConfiguration,
        ):
            raise ServerProfileValidationError(
                "AITRANSS runtime must be an AitranssRuntimeConfiguration or None"
            )
        if self.orca_runtime is not None and not isinstance(
            self.orca_runtime,
            OrcaRuntimeConfiguration,
        ):
            raise ServerProfileValidationError(
                "ORCA runtime must be an OrcaRuntimeConfiguration or None"
            )
        recipient = self.email_notification_recipient
        if recipient is not None:
            if not isinstance(recipient, str):
                raise ServerProfileValidationError(
                    "email notification recipient must be text or None"
                )
            if not recipient.strip():
                recipient = None
            else:
                recipient = normalize_email_notification_recipient(recipient)
        if self.email_notification_enabled and recipient is None:
            raise ServerProfileValidationError(
                "email notification recipient is required when notifications are enabled"
            )
        object.__setattr__(self, "email_notification_recipient", recipient)

    @property
    def slurm_mail_settings(self) -> SlurmMailSettings | None:
        """Return active scheduler-mail settings; retain the historical API name."""

        if not self.email_notification_enabled:
            return None
        recipient = self.email_notification_recipient
        if recipient is None:  # Guard the invariant for callers and type checkers.
            raise ServerProfileValidationError(
                "email notification recipient is required when notifications are enabled"
            )
        return SlurmMailSettings(recipient)


def normalize_email_notification_recipient(value: object) -> str:
    """Normalize one safe ASCII mailbox suitable for an SBATCH directive."""

    if not isinstance(value, str):
        raise ServerProfileValidationError(
            "email notification recipient must be text"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ServerProfileValidationError(
            "email notification recipient must not contain control characters"
        )
    recipient = value.strip()
    if not recipient:
        raise ServerProfileValidationError(
            "email notification recipient must not be empty"
        )
    if len(recipient) > 254 or recipient.count("@") != 1:
        raise ServerProfileValidationError(
            "email notification recipient must be one address in local@domain form"
        )
    local_part, domain = recipient.split("@")
    labels = domain.split(".")
    if (
        len(local_part) > 64
        or _EMAIL_LOCAL_PART.fullmatch(local_part) is None
        or local_part.endswith(".")
        or ".." in local_part
        or len(labels) < 2
        or any(_EMAIL_DOMAIN_LABEL.fullmatch(label) is None for label in labels)
    ):
        raise ServerProfileValidationError(
            "email notification recipient must be one address in local@domain form"
        )
    return recipient


def runtime_minutes_from_hours(hours: object) -> int:
    """Convert user-facing numeric hours to normalized whole minutes."""

    if isinstance(hours, bool) or not isinstance(hours, (int, float, Decimal)):
        raise ServerProfileValidationError("runtime hours must be a positive number")
    if isinstance(hours, float) and not math.isfinite(hours):
        raise ServerProfileValidationError("runtime hours must be a positive number")
    try:
        normalized = Decimal(str(hours))
        if not normalized.is_finite() or normalized <= 0:
            raise InvalidOperation
        minutes = int(
            (normalized * Decimal(60)).to_integral_value(
                rounding=ROUND_HALF_EVEN
            )
        )
    except (InvalidOperation, OverflowError, ValueError):
        raise ServerProfileValidationError(
            "runtime hours must be a positive number"
        ) from None
    if minutes <= 0:
        raise ServerProfileValidationError("runtime hours must be a positive number")
    return minutes


def runtime_hours_from_minutes(runtime_minutes: object) -> float:
    """Convert normalized whole minutes for display as numeric hours."""

    _require_positive_int(runtime_minutes, "runtime_minutes")
    return runtime_minutes / 60.0


def normalize_module_name(value: object) -> str:
    """Normalize one ordered environment-module name without shell syntax."""

    module_name = _single_line_text(value, "module name")
    if _MODULE_NAME.fullmatch(module_name) is None:
        raise ServerProfileValidationError(
            "module names may contain only letters, numbers, '.', '_', '+', "
            "'-', and '/'"
        )
    return module_name


def normalize_scheduler_site_identifier(
    value: object,
    label: str,
) -> str | None:
    """Normalize one optional scheduler identifier, never directive syntax."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ServerProfileValidationError(f"{label} must be text or blank")
    normalized = value.strip()
    if not normalized:
        return None
    if (
        len(normalized) > 128
        or _SCHEDULER_SITE_IDENTIFIER.fullmatch(normalized) is None
    ):
        raise ServerProfileValidationError(
            f"{label} must start with a letter or number and contain only "
            "letters, numbers, '.', '_', '+', '@', '/', or '-'"
        )
    return normalized


def validate_slurm_bin_directory(value: object) -> str:
    """Return one safe absolute POSIX directory containing Slurm commands."""

    if isinstance(value, str) and any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ServerProfileValidationError(
            "Slurm command directory must not contain control characters"
        )
    directory = _single_line_text(value, "Slurm command directory").rstrip("/")
    if not directory:
        directory = "/"
    if directory == "/":
        raise ServerProfileValidationError(
            "Slurm command directory must not be the filesystem root"
        )
    if not directory.startswith("/") or directory.startswith("//"):
        raise ServerProfileValidationError(
            "Slurm command directory must be an absolute POSIX path"
        )
    if any(
        ord(character) < 32
        or ord(character) == 127
        or character in _SHELL_CONTROL_CHARACTERS
        for character in directory
    ):
        raise ServerProfileValidationError(
            "Slurm command directory contains shell-control characters"
        )
    parts = directory.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise ServerProfileValidationError(
            "Slurm command directory must not contain empty, '.' or '..' components"
        )
    path = PurePosixPath(directory)
    if path.name == "sbatch":
        raise ServerProfileValidationError(
            "Enter the directory containing sbatch, not the sbatch executable itself."
        )
    return str(path)


def validate_lsf_env_directory(value: object) -> str:
    """Return a safe absolute directory whose ``lsf.conf`` will be used."""

    directory = _validate_lsf_client_directory(
        value, "LSF configuration directory"
    )
    if PurePosixPath(directory).name == "lsf.conf":
        raise ServerProfileValidationError(
            "Enter the directory containing lsf.conf, not the lsf.conf file itself."
        )
    return directory


def validate_lsf_library_directory(value: object) -> str:
    """Return one safe absolute LSF library directory."""

    return _validate_lsf_client_directory(value, "LSF library directory")


def validate_lsf_server_directory(value: object) -> str:
    """Return one safe absolute LSF server/helper directory."""

    return _validate_lsf_client_directory(value, "LSF server directory")


def validate_fhi_species_defaults_path(value: object) -> str:
    """Return the canonical remote root containing FHI-aims accuracy folders."""

    return _validate_remote_directory(
        value,
        "FHI-aims species definitions root",
    )


def _validate_lsf_client_directory(value: object, label: str) -> str:
    """Validate one non-root canonical POSIX directory used by LSF clients."""

    return _validate_remote_directory(value, label)


def _validate_remote_directory(value: object, label: str) -> str:
    """Validate one non-root canonical, shell-safe absolute POSIX directory."""

    if isinstance(value, str) and any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ServerProfileValidationError(
            f"{label} must not contain control characters"
        )
    directory = _single_line_text(value, label).rstrip("/")
    if not directory:
        directory = "/"
    if directory == "/":
        raise ServerProfileValidationError(
            f"{label} must not be the filesystem root"
        )
    if (
        not directory.startswith("/")
        or directory.startswith("//")
        or "\\" in directory
        or any(character in _SHELL_CONTROL_CHARACTERS for character in directory)
    ):
        raise ServerProfileValidationError(
            f"{label} must be a safe absolute POSIX path"
        )
    parts = directory.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise ServerProfileValidationError(
            f"{label} must not contain empty, '.' or '..' components"
        )
    path = PurePosixPath(directory)
    if str(path) != directory:
        raise ServerProfileValidationError(
            f"{label} must be canonical"
        )
    return str(path)


def validate_remote_executable_path(value: object, label: str) -> str:
    """Return one canonical, shell-safe absolute POSIX executable path."""

    if isinstance(value, str) and any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ServerProfileValidationError(
            f"{label} path must not contain control characters"
        )
    path_text = _single_line_text(value, f"{label} path")
    if (
        not path_text.startswith("/")
        or path_text.startswith("//")
        or "\\" in path_text
        or any(character in _SHELL_CONTROL_CHARACTERS for character in path_text)
    ):
        raise ServerProfileValidationError(
            f"{label} path must be a safe absolute POSIX path"
        )
    parts = path_text.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise ServerProfileValidationError(
            f"{label} path must not contain empty, '.' or '..' components"
        )
    path = PurePosixPath(path_text)
    if str(path) != path_text or path.name in {"", ".", ".."}:
        raise ServerProfileValidationError(
            f"{label} path must be canonical and name one executable"
        )
    return str(path)


def validate_srun_launcher_path(value: object) -> str:
    """Return one canonical absolute path whose executable name is ``srun``."""

    path = validate_remote_executable_path(value, "AITRANSS srun executable")
    if PurePosixPath(path).name != "srun":
        raise ServerProfileValidationError(
            "AITRANSS srun executable path must name srun"
        )
    return path


def _require_positive_int(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ServerProfileValidationError(
            f"{field_name.replace('_', ' ')} must be a positive integer"
        )


def _trimmed_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ServerProfileValidationError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized:
        raise ServerProfileValidationError(f"{field_name} must not be empty")
    if "\x00" in normalized:
        raise ServerProfileValidationError(f"{field_name} must not contain NUL")
    return normalized


def _single_line_text(value: object, field_name: str) -> str:
    normalized = _trimmed_text(value, field_name)
    if "\n" in normalized or "\r" in normalized:
        raise ServerProfileValidationError(
            f"{field_name} must contain exactly one line"
        )
    return normalized


def normalize_scheduler_output_filename(
    value: object,
    *,
    allow_empty: bool = False,
) -> str:
    """Validate one scheduler output filename, never a directory or path."""

    if not isinstance(value, str):
        raise ServerProfileValidationError("Output file name must be text")
    normalized = value.strip()
    if not normalized:
        if allow_empty:
            return ""
        raise ServerProfileValidationError("Output file name must not be empty")
    if any(
        character in normalized
        for character in ("/", "\\", "\x00", "\n", "\r")
    ):
        raise ServerProfileValidationError(
            "Output file name must be one filename, not a path"
        )
    if _SCHEDULER_OUTPUT_FILENAME.fullmatch(normalized) is None:
        raise ServerProfileValidationError(
            "Output file name must start with a letter or number and contain "
            "only letters, numbers, dots, underscores, or hyphens"
        )
    return normalized
