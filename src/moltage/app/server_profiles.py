"""Deterministic local server-profile repository and secret orchestration."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    FhiAimsRuntimeConfiguration,
    LsfResourceRequirementMode,
    RuntimeDiscoveryHints,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    RuntimeLocation,
    OrcaRuntimeConfiguration,
    ServerProfile,
    ServerProfileValidationError,
    SlurmCommandMode,
    SlurmAitranssLaunchMode,
    SlurmExecutionPreset,
    validate_lsf_env_directory,
    validate_lsf_library_directory,
    validate_lsf_server_directory,
    validate_slurm_bin_directory,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.orca.catalog import OrcaVersionEvidence, OrcaVersionFamily
from moltage.remote.secrets import SecretStore


_PROFILE_STORE_VERSION = 12
_SCHEMA_11_PROFILE_STORE_VERSION = 11
_SCHEMA_10_PROFILE_STORE_VERSION = 10
_SCHEMA_9_PROFILE_STORE_VERSION = 9
_SCHEMA_8_PROFILE_STORE_VERSION = 8
_SCHEMA_7_PROFILE_STORE_VERSION = 7
_SCHEMA_6_PROFILE_STORE_VERSION = 6
_SCHEMA_5_PROFILE_STORE_VERSION = 5
_SCHEMA_4_PROFILE_STORE_VERSION = 4
_PREVIOUS_PROFILE_STORE_VERSION = 3
_SCHEMA_2_PROFILE_STORE_VERSION = 2
_LEGACY_PROFILE_STORE_VERSION = 1


class ServerProfileRepositoryError(RuntimeError):
    """Raised for malformed, duplicate, or unwritable profile state."""


@dataclass(frozen=True, slots=True)
class ServerProfileCollection:
    profiles: tuple[ServerProfile, ...]
    last_selected_profile_id: UUID | None


class ServerProfileRepository:
    """Persist only non-secret profile state in one injected JSON path."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> ServerProfileCollection:
        if not self.path.exists():
            return ServerProfileCollection((), None)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("profile-store root must be an object")
            schema_version = raw.get("schema_version")
            if schema_version not in {
                _LEGACY_PROFILE_STORE_VERSION,
                _SCHEMA_2_PROFILE_STORE_VERSION,
                _PREVIOUS_PROFILE_STORE_VERSION,
                _SCHEMA_4_PROFILE_STORE_VERSION,
                _SCHEMA_5_PROFILE_STORE_VERSION,
                _SCHEMA_6_PROFILE_STORE_VERSION,
                _SCHEMA_7_PROFILE_STORE_VERSION,
                _SCHEMA_8_PROFILE_STORE_VERSION,
                _SCHEMA_9_PROFILE_STORE_VERSION,
                _SCHEMA_10_PROFILE_STORE_VERSION,
                _SCHEMA_11_PROFILE_STORE_VERSION,
                _PROFILE_STORE_VERSION,
            }:
                raise ValueError("unsupported profile-store schema version")
            profiles = tuple(
                _profile_from_dict(item, schema_version)
                for item in raw["profiles"]
            )
            selected_raw = raw["last_selected_profile_id"]
            selected = UUID(selected_raw) if selected_raw is not None else None
        except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
            raise ServerProfileRepositoryError(
                f"server profile store is unreadable or malformed: {error}"
            ) from None
        _validate_unique_names(profiles)
        if selected is not None and selected not in {
            profile.profile_id for profile in profiles
        }:
            raise ServerProfileRepositoryError(
                "last-selected profile ID does not identify a saved profile"
            )
        return ServerProfileCollection(profiles, selected)

    def save(self, profile: ServerProfile) -> ServerProfileCollection:
        if not isinstance(profile, ServerProfile):
            raise TypeError("profile must be a ServerProfile")
        current = self.load()
        profiles = tuple(
            profile if item.profile_id == profile.profile_id else item
            for item in current.profiles
        )
        if profile.profile_id not in {item.profile_id for item in current.profiles}:
            profiles = (*profiles, profile)
        _validate_unique_names(profiles)
        updated = ServerProfileCollection(
            tuple(sorted(profiles, key=lambda item: (item.name.casefold(), str(item.profile_id)))),
            profile.profile_id,
        )
        self._write(updated)
        return updated

    def set_last_selected(self, profile_id: UUID) -> ServerProfileCollection:
        current = self.load()
        if profile_id not in {profile.profile_id for profile in current.profiles}:
            raise ServerProfileRepositoryError(
                "last-selected profile ID does not identify a saved profile"
            )
        updated = replace(current, last_selected_profile_id=profile_id)
        self._write(updated)
        return updated

    def delete(self, profile_id: UUID) -> ServerProfileCollection:
        current = self.load()
        profiles = tuple(
            profile for profile in current.profiles if profile.profile_id != profile_id
        )
        if len(profiles) == len(current.profiles):
            raise ServerProfileRepositoryError("server profile does not exist")
        selected = (
            None
            if current.last_selected_profile_id == profile_id
            else current.last_selected_profile_id
        )
        updated = ServerProfileCollection(profiles, selected)
        self._write(updated)
        return updated

    def cache_automatic_slurm_directory(
        self,
        profile_id: UUID,
        bin_directory: str,
        lsf_env_directory: str | None = None,
        lsf_library_directory: str | None = None,
        lsf_server_directory: str | None = None,
    ) -> ServerProfileCollection:
        """Persist only verified automatic scheduler-location inputs."""

        normalized = validate_slurm_bin_directory(bin_directory)
        normalized_lsf_env = (
            validate_lsf_env_directory(lsf_env_directory)
            if lsf_env_directory is not None
            else None
        )
        normalized_lsf_library = (
            validate_lsf_library_directory(lsf_library_directory)
            if lsf_library_directory is not None
            else None
        )
        normalized_lsf_server = (
            validate_lsf_server_directory(lsf_server_directory)
            if lsf_server_directory is not None
            else None
        )
        current = self.load()
        profile = next(
            (item for item in current.profiles if item.profile_id == profile_id),
            None,
        )
        if profile is None:
            raise ServerProfileRepositoryError("server profile does not exist")
        preset = profile.execution_preset
        if preset is None:
            raise ServerProfileRepositoryError(
                "server profile has no Cluster Execution Settings"
            )
        if preset.slurm_command_mode is not SlurmCommandMode.AUTOMATIC:
            raise ServerProfileRepositoryError(
                "automatic scheduler location cannot be cached for a Manual profile"
            )
        updated = replace(
            profile,
            execution_preset=replace(
                preset,
                slurm_bin_directory=normalized,
                lsf_env_directory=normalized_lsf_env,
                lsf_library_directory=normalized_lsf_library,
                lsf_server_directory=normalized_lsf_server,
            ),
        )
        return self.save(updated)

    def _write(self, collection: ServerProfileCollection) -> None:
        document = {
            "schema_version": _PROFILE_STORE_VERSION,
            "last_selected_profile_id": (
                str(collection.last_selected_profile_id)
                if collection.last_selected_profile_id is not None
                else None
            ),
            "profiles": [_profile_to_dict(profile) for profile in collection.profiles],
        }
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(text, encoding="utf-8", newline="\n")
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ServerProfileRepositoryError(
                "server profiles could not be persisted"
            ) from None


class ServerProfileService:
    """Coordinate profile persistence with UUID-keyed credential semantics."""

    def __init__(
        self,
        repository: ServerProfileRepository,
        secret_store: SecretStore,
    ) -> None:
        self.repository = repository
        self.secret_store = secret_store

    def save(
        self,
        profile: ServerProfile,
        *,
        supplied_password: str | None = None,
    ) -> ServerProfileCollection:
        self._validate_candidate(profile)
        if profile.save_password and supplied_password is not None:
            self.secret_store.set_password(profile.profile_id, supplied_password)
        elif not profile.save_password:
            self.secret_store.delete_password(profile.profile_id)
        return self.repository.save(profile)

    def save_as(
        self,
        profile: ServerProfile,
        new_name: str,
        *,
        supplied_password: str | None = None,
    ) -> ServerProfile:
        copied = replace(profile, profile_id=uuid4(), name=new_name)
        self.save(copied, supplied_password=supplied_password)
        return copied

    def delete(self, profile_id: UUID) -> ServerProfileCollection:
        self.secret_store.delete_password(profile_id)
        return self.repository.delete(profile_id)

    def has_saved_password(self, profile_id: UUID) -> bool:
        return self.secret_store.get_password(profile_id) is not None

    def _validate_candidate(self, candidate: ServerProfile) -> None:
        current = self.repository.load()
        duplicate = next(
            (
                profile
                for profile in current.profiles
                if profile.profile_id != candidate.profile_id
                and profile.name.casefold() == candidate.name.casefold()
            ),
            None,
        )
        if duplicate is not None:
            raise ServerProfileRepositoryError(
                f"a server profile named {candidate.name!r} already exists"
            )


def _validate_unique_names(profiles: tuple[ServerProfile, ...]) -> None:
    names: dict[str, str] = {}
    ids: set[UUID] = set()
    for profile in profiles:
        normalized = profile.name.casefold()
        if normalized in names:
            raise ServerProfileRepositoryError(
                f"duplicate server profile names are not allowed: {profile.name!r}"
            )
        if profile.profile_id in ids:
            raise ServerProfileRepositoryError(
                f"duplicate server profile ID: {profile.profile_id}"
            )
        names[normalized] = profile.name
        ids.add(profile.profile_id)


def _profile_to_dict(profile: ServerProfile) -> dict[str, object]:
    preset = profile.execution_preset
    preset_document = None
    if preset is not None:
        preset_document = {
            "nodes": preset.nodes,
            "ntasks": preset.ntasks,
            "cpus_per_task": preset.cpus_per_task,
            "runtime_minutes": preset.runtime_minutes,
            "memory_gb": preset.memory_gb,
            "no_requeue": preset.no_requeue,
            "export_none": preset.export_none,
            "unset_slurm_export_env": preset.unset_slurm_export_env,
            "omp_num_threads": preset.omp_num_threads,
            "module_purge": preset.module_purge,
            "modules": list(preset.modules),
            "launch_command": preset.launch_command,
            "slurm_output_filename": preset.slurm_output_filename,
            "slurm_command_mode": preset.slurm_command_mode.value,
            "slurm_bin_directory": preset.slurm_bin_directory,
            "lsf_env_directory": preset.lsf_env_directory,
            "lsf_library_directory": preset.lsf_library_directory,
            "lsf_server_directory": preset.lsf_server_directory,
            "fhi_species_defaults_path": preset.fhi_species_defaults_path,
            "slurm_account": preset.slurm_account,
            "slurm_partition": preset.slurm_partition,
            "slurm_qos": preset.slurm_qos,
            "lsf_queue": preset.lsf_queue,
            "lsf_project": preset.lsf_project,
            "lsf_resource_requirement_mode": (
                None
                if preset.lsf_resource_requirement_mode is None
                else preset.lsf_resource_requirement_mode.value
            ),
            "slurm_aitranss_launch_mode": (
                None
                if preset.slurm_aitranss_launch_mode is None
                else preset.slurm_aitranss_launch_mode.value
            ),
            "slurm_aitranss_srun_path": preset.slurm_aitranss_srun_path,
            "fhi_runtime": (
                None if preset.fhi_runtime is None else {
                    "executable_path": preset.fhi_runtime.executable_path,
                    "launcher_path": preset.fhi_runtime.launcher_path,
                    "environment": _environment_to_dict(preset.fhi_runtime.environment),
                }
            ),
            "scheduler_kind": preset.scheduler_kind.value,
        }
    runtime = profile.aitranss_runtime
    runtime_document = None
    if runtime is not None:
        runtime_document = {
            "modules": list(runtime.modules),
            "executable_path": runtime.executable_path,
            "environment": _environment_to_dict(runtime.environment),
        }
    orca_runtime = profile.orca_runtime
    orca_runtime_document = None
    if orca_runtime is not None:
        evidence = orca_runtime.version_evidence
        orca_runtime_document = {
            "executable_path": orca_runtime.executable_path,
            "environment": _environment_to_dict(orca_runtime.environment),
            "version_evidence": {
                "version_text": evidence.version_text,
                "version": evidence.version,
                "version_family": (
                    evidence.version_family.value
                    if evidence.version_family is not None
                    else None
                ),
                "detection_source": evidence.detection_source,
            },
        }
    return {
        "profile_id": str(profile.profile_id),
        "name": profile.name,
        "host": profile.host,
        "port": profile.port,
        "username": profile.username,
        "remote_project_root": profile.remote_project_root,
        "save_password": profile.save_password,
        "auto_connect": profile.auto_connect,
        "email_notification_enabled": profile.email_notification_enabled,
        "email_notification_recipient": profile.email_notification_recipient,
        "execution_preset": preset_document,
        "aitranss_runtime": runtime_document,
        "runtime_hints": _runtime_hints_to_dict(profile.runtime_hints),
        "orca_runtime": orca_runtime_document,
    }


def _profile_from_dict(raw: object, schema_version: int) -> ServerProfile:
    if not isinstance(raw, dict):
        raise ValueError("profile entry must be an object")
    preset_raw = raw["execution_preset"]
    if preset_raw is None:
        if schema_version == _LEGACY_PROFILE_STORE_VERSION:
            raise ValueError("legacy execution preset must be an object")
        preset = None
    elif not isinstance(preset_raw, dict):
        raise ValueError("execution preset must be an object or null")
    elif schema_version == _LEGACY_PROFILE_STORE_VERSION:
        preset = _legacy_execution_preset(preset_raw)
    elif schema_version == _SCHEMA_2_PROFILE_STORE_VERSION:
        preset = _schema_2_execution_preset(preset_raw)
    else:
        preset = _current_execution_preset(
            preset_raw,
            schema_version=schema_version,
            scheduler_kind=(
                SchedulerKind(preset_raw["scheduler_kind"])
                if schema_version >= _SCHEMA_6_PROFILE_STORE_VERSION
                else SchedulerKind.SLURM
            ),
            normalize_legacy_lsf=(
                schema_version == _SCHEMA_6_PROFILE_STORE_VERSION
            ),
        )
    runtime = (
        None
        if schema_version < _SCHEMA_4_PROFILE_STORE_VERSION
        else _aitranss_runtime_from_dict(raw["aitranss_runtime"])
    )
    orca_runtime = (
        _orca_runtime_from_dict(raw.get("orca_runtime"))
        if schema_version >= _PROFILE_STORE_VERSION
        else None
    )
    try:
        return ServerProfile(
            profile_id=UUID(raw["profile_id"]),
            name=raw["name"],
            host=raw["host"],
            port=raw["port"],
            username=raw["username"],
            remote_project_root=raw["remote_project_root"],
            save_password=raw["save_password"],
            auto_connect=raw["auto_connect"],
            execution_preset=preset,
            aitranss_runtime=runtime,
            runtime_hints=_runtime_hints_from_dict(raw.get("runtime_hints")),
            orca_runtime=orca_runtime,
            email_notification_enabled=raw.get(
                "email_notification_enabled",
                False,
            ),
            email_notification_recipient=raw.get(
                "email_notification_recipient"
            ),
        )
    except (KeyError, TypeError, ValueError, ServerProfileValidationError) as error:
        raise ValueError(f"invalid server profile: {error}") from None


def _aitranss_runtime_from_dict(
    raw: object,
) -> AitranssRuntimeConfiguration | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("AITRANSS runtime must be an object or null")
    try:
        return AitranssRuntimeConfiguration(
            modules=tuple(raw["modules"]),
            executable_path=raw["executable_path"],
            environment=_environment_from_dict(raw.get("environment")),
        )
    except (KeyError, TypeError, ValueError, ServerProfileValidationError) as error:
        raise ValueError(f"invalid AITRANSS runtime: {error}") from None


def _orca_runtime_from_dict(raw: object) -> OrcaRuntimeConfiguration | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("ORCA runtime must be an object or null")
    try:
        evidence_raw = raw["version_evidence"]
        if not isinstance(evidence_raw, dict):
            raise TypeError("ORCA version evidence must be an object")
        family_raw = evidence_raw["version_family"]
        evidence = OrcaVersionEvidence(
            version_text=evidence_raw["version_text"],
            version=evidence_raw["version"],
            version_family=(
                OrcaVersionFamily(family_raw) if family_raw is not None else None
            ),
            detection_source=evidence_raw["detection_source"],
        )
        return OrcaRuntimeConfiguration(
            executable_path=raw["executable_path"],
            environment=_environment_from_dict(raw["environment"]),
            version_evidence=evidence,
        )
    except (KeyError, TypeError, ValueError, ServerProfileValidationError) as error:
        raise ValueError(f"invalid ORCA runtime: {error}") from None


def _current_execution_preset(
    raw: dict[str, object],
    *,
    schema_version: int,
    scheduler_kind: SchedulerKind = SchedulerKind.SLURM,
    normalize_legacy_lsf: bool = False,
) -> SlurmExecutionPreset:
    try:
        lsf_legacy = (
            normalize_legacy_lsf and scheduler_kind is SchedulerKind.LSF
        )
        fhi_runtime = _fhi_runtime_from_dict(raw.get("fhi_runtime"))
        legacy_srun_path = (
            fhi_runtime.launcher_path
            if (
                schema_version <= _SCHEMA_10_PROFILE_STORE_VERSION
                and scheduler_kind is SchedulerKind.SLURM
                and fhi_runtime is not None
                and fhi_runtime.launcher_path.rsplit("/", 1)[-1] == "srun"
            )
            else None
        )
        return SlurmExecutionPreset(
            nodes=raw["nodes"],
            ntasks=raw["ntasks"],
            cpus_per_task=raw["cpus_per_task"],
            runtime_minutes=raw["runtime_minutes"],
            memory_gb=raw["memory_gb"],
            no_requeue=raw["no_requeue"],
            export_none=raw["export_none"],
            unset_slurm_export_env=(
                False if lsf_legacy else raw["unset_slurm_export_env"]
            ),
            omp_num_threads=(1 if lsf_legacy else raw["omp_num_threads"]),
            module_purge=raw["module_purge"],
            modules=tuple(raw["modules"]),
            launch_command=raw["launch_command"],
            slurm_output_filename=raw["slurm_output_filename"],
            slurm_command_mode=SlurmCommandMode(raw["slurm_command_mode"]),
            slurm_bin_directory=raw["slurm_bin_directory"],
            lsf_env_directory=raw.get("lsf_env_directory"),
            lsf_library_directory=raw.get("lsf_library_directory"),
            lsf_server_directory=raw.get("lsf_server_directory"),
            fhi_species_defaults_path=raw.get("fhi_species_defaults_path"),
            slurm_account=(
                raw.get("slurm_account")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else None
            ),
            slurm_partition=(
                raw.get("slurm_partition")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else None
            ),
            slurm_qos=(
                raw.get("slurm_qos")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else None
            ),
            lsf_queue=(
                raw.get("lsf_queue")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else None
            ),
            lsf_project=(
                raw.get("lsf_project")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else None
            ),
            lsf_resource_requirement_mode=(
                raw.get("lsf_resource_requirement_mode")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else (
                    LsfResourceRequirementMode.SPAN_RUSAGE
                    if scheduler_kind is SchedulerKind.LSF
                    else None
                )
            ),
            slurm_aitranss_launch_mode=(
                raw.get("slurm_aitranss_launch_mode")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else (
                    SlurmAitranssLaunchMode.SRUN
                    if scheduler_kind is SchedulerKind.SLURM
                    else None
                )
            ),
            slurm_aitranss_srun_path=(
                raw.get("slurm_aitranss_srun_path")
                if schema_version >= _SCHEMA_11_PROFILE_STORE_VERSION
                else legacy_srun_path
            ),
            fhi_runtime=fhi_runtime,
            scheduler_kind=scheduler_kind,
        )
    except (KeyError, TypeError, ValueError, ServerProfileValidationError) as error:
        raise ValueError(f"invalid execution preset: {error}") from None


def _environment_to_dict(environment: RuntimeEnvironment | None):
    if environment is None:
        return None
    return {"mode": environment.mode.value, "modules": list(environment.modules),
            "setup_script": environment.setup_script}


def _environment_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Runtime environment must be an object")
    return RuntimeEnvironment(RuntimeEnvironmentMode(raw["mode"]),
                              tuple(raw["modules"]), raw["setup_script"])


def _fhi_runtime_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("FHI-aims runtime must be an object")
    return FhiAimsRuntimeConfiguration(raw["executable_path"], raw["launcher_path"],
                                       _environment_from_dict(raw["environment"]))


def _runtime_hints_to_dict(hints):
    if hints is None:
        return None
    return {
        "fhi_aims": {"location": hints.fhi_aims.location,
                     "kind": hints.fhi_aims.kind.value,
                     "environment": _environment_to_dict(hints.fhi_aims.environment)},
        "mpi_launcher": hints.mpi_launcher,
        "fhi_species_defaults_path": hints.fhi_species_defaults_path,
        "aitranss": {"location": hints.aitranss.location,
                     "kind": hints.aitranss.kind.value,
                     "environment": _environment_to_dict(hints.aitranss.environment)},
        "orca": {"location": hints.orca.location,
                 "kind": hints.orca.kind.value,
                 "environment": _environment_to_dict(hints.orca.environment)},
    }


def _runtime_hints_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Runtime hints must be an object")
    def location(value):
        if not isinstance(value, dict):
            raise ValueError("Runtime location must be an object")
        return RuntimeLocation(value["location"], _environment_from_dict(value["environment"]),
                               value["kind"])
    return RuntimeDiscoveryHints(
        location(raw["fhi_aims"]),
        raw["mpi_launcher"],
        location(raw["aitranss"]),
        raw.get("fhi_species_defaults_path"),
        location(raw.get("orca", {
            "location": "",
            "kind": "DIRECTORY",
            "environment": {"mode": "AUTO", "modules": [], "setup_script": None},
        })),
    )


def _schema_2_execution_preset(raw: dict[str, object]) -> SlurmExecutionPreset:
    try:
        return SlurmExecutionPreset(
            nodes=raw["nodes"],
            ntasks=raw["ntasks"],
            cpus_per_task=raw["cpus_per_task"],
            runtime_minutes=raw["runtime_minutes"],
            memory_gb=raw["memory_gb"],
            no_requeue=raw["no_requeue"],
            export_none=raw["export_none"],
            unset_slurm_export_env=raw["unset_slurm_export_env"],
            omp_num_threads=raw["omp_num_threads"],
            module_purge=raw["module_purge"],
            modules=tuple(raw["modules"]),
            launch_command=raw["launch_command"],
            slurm_output_filename=raw["slurm_output_filename"],
            slurm_command_mode=SlurmCommandMode.AUTOMATIC,
            slurm_bin_directory=None,
            slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.SRUN,
        )
    except (KeyError, TypeError, ValueError, ServerProfileValidationError) as error:
        raise ValueError(f"invalid execution preset: {error}") from None


def _legacy_execution_preset(raw: dict[str, object]) -> SlurmExecutionPreset:
    try:
        runtime_minutes = _legacy_runtime_minutes(raw["wall_time"])
        memory_gb = _legacy_memory_gb(raw["memory"])
        return SlurmExecutionPreset(
            nodes=raw["nodes"],
            ntasks=raw["ntasks"],
            cpus_per_task=raw["cpus_per_task"],
            runtime_minutes=runtime_minutes,
            memory_gb=memory_gb,
            no_requeue=raw["no_requeue"],
            export_none=raw["export_none"],
            unset_slurm_export_env=raw["unset_slurm_export_env"],
            omp_num_threads=raw["omp_num_threads"],
            module_purge=raw["module_purge"],
            modules=tuple(raw["modules"]),
            launch_command=raw["launch_command"],
            slurm_output_filename=raw["slurm_output_filename"],
            slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.SRUN,
        )
    except (KeyError, TypeError, ValueError, ServerProfileValidationError) as error:
        raise ValueError(
            "legacy execution preset cannot be converted safely; re-enter "
            f"Cluster Execution Settings: {error}"
        ) from None


def _legacy_runtime_minutes(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("wall_time must be HOURS:MM:SS text")
    parts = value.strip().split(":")
    if (
        len(parts) != 3
        or not all(part.isdigit() for part in parts)
        or len(parts[1]) != 2
        or len(parts[2]) != 2
    ):
        raise ValueError("wall_time must be HOURS:MM:SS text")
    hours, minutes, seconds = (int(part) for part in parts)
    if minutes > 59 or seconds > 59 or seconds != 0:
        raise ValueError("wall_time cannot be represented as whole minutes")
    total_minutes = hours * 60 + minutes
    if total_minutes <= 0:
        raise ValueError("wall_time must be positive")
    return total_minutes


def _legacy_memory_gb(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("memory must be integer GB text")
    normalized = value.strip().upper()
    if normalized.endswith("G"):
        normalized = normalized[:-1]
    if not normalized.isdigit():
        raise ValueError("memory must be an integer with optional G suffix")
    memory_gb = int(normalized)
    if memory_gb <= 0:
        raise ValueError("memory must be positive")
    return memory_gb
