"""One explicit density task/job, immutable attempts, existing SSH/Slurm transport."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from collections.abc import Callable
import json
from enum import Enum, StrEnum
import re
import warnings
from pathlib import Path, PurePosixPath
import shlex
from uuid import UUID, uuid4

from moltage.aims.density_difference import DensityInputBundle, DensityInputPlan, DensitySettings, DensityElectronicState, parse_density_output
from moltage.aims.optimization_settings import SpeciesAccuracy, SpinSettings, SpinInitializationMode, XCFunctional
from moltage.app.density_results import build_density_result, validate_component
from moltage.app.project_planning import validate_project_base_name, project_directory_candidates
from moltage.app.species_acquisition import acquire_species_library
from moltage.domain.density_difference import COMPONENTS, DensityGrid, FragmentPartition
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.structure import Atom, MolecularStructure
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteOperationStopToken,
    RemotePathAlreadyExistsError,
    RemotePathNotFoundError,
)
from moltage.remote.project_repository import (
    ManagedMetadataLocationError,
    managed_metadata_directory_path,
    resolve_existing_managed_metadata_file,
)
from moltage.remote.runtime_environment import verify_configured_fhi_runtime
from moltage.remote.slurm import render_fhi_batch_parts, build_sbatch_submission_command, parse_sbatch_parsable_output
from moltage.remote.slurm_discovery import resolve_slurm_for_submission
from moltage.remote.slurm_cancel import request_slurm_cancellation_once
from moltage.remote.slurm_status import query_slurm_job_status, SchedulerStatusKind
from moltage.remote.step_inputs import upload_new_files_atomically, replace_existing_file_atomically


MANIFEST = "density.json"
ACTIVE = frozenset({"UNKNOWN", "QUEUED", "RUNNING", "UNRESOLVED"})
COMPONENT_PROGRESS_STATES = frozenset(
    {
        "NOT_STARTED",
        "QUEUED",
        "RUNNING",
        "OUTPUT_READY",
        "COMPLETE",
        "FAILED",
        "CANCELLED",
        "UNKNOWN",
        "UNRESOLVED",
    }
)


class DensityIntegrityError(RuntimeError):
    """Historical evidence changed; never turn this into a runnable retry."""


class DensityCancellationError(RuntimeError):
    """The exact current density job cannot be cancelled safely."""


class DensityCancellationOutcome(StrEnum):
    """Known application outcomes of one exact-job cancellation request."""

    REQUESTED = "REQUESTED"
    UNKNOWN = "UNKNOWN"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


class DensityRecoveryPhase(StrEnum):
    """Non-authoritative presentation stages for explicit result recovery."""

    LOCATING = "LOCATING"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"


@dataclass(frozen=True, slots=True)
class DensityRecoveryProgress:
    phase: DensityRecoveryPhase
    message: str
    current_file_bytes: int = 0
    current_file_total_bytes: int | None = None
    retrieved_bytes: int = 0
    total_bytes: int | None = None


def _progress_reporter(progress):
    """Keep optional progress presentation outside workflow authority."""

    def report(event):
        if progress is None:
            return
        try:
            progress(event)
        except Exception:
            pass

    return report


def _file_sha256(path):
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class _DensityTransferTracker:
    def __init__(self, report, sizes):
        self._report = report
        self._sizes = sizes
        self._completed = 0
        self.total_bytes = (
            sum(sizes.values())
            if all(size is not None for size in sizes.values())
            else None
        )

    @property
    def retrieved_bytes(self):
        return self._completed

    @staticmethod
    def _label(name):
        return {"total": "Total", "subset1": "Subset 1", "subset2": "Subset 2"}[name]

    def update(self, name, filename, current, reported_total):
        expected = self._sizes[(name, filename)]
        file_total = expected if expected is not None else (
            reported_total if reported_total >= 0 else None
        )
        self._report(
            DensityRecoveryProgress(
                DensityRecoveryPhase.DOWNLOADING,
                f"Downloading {self._label(name)} / {filename}",
                max(0, current),
                file_total,
                self._completed + max(0, current),
                self.total_bytes,
            )
        )

    def complete(self, name, filename, size, *, cached=False):
        self._completed += size
        expected = self._sizes[(name, filename)]
        self._report(
            DensityRecoveryProgress(
                DensityRecoveryPhase.DOWNLOADING,
                (
                    f"Using validated local cache for {self._label(name)} / {filename}"
                    if cached
                    else f"Downloaded {self._label(name)} / {filename}"
                ),
                size,
                expected if expected is not None else size,
                self._completed,
                self.total_bytes,
            )
        )


def _close_density_executor(executor):
    """Cleanup must not replace a known dispatch outcome or primary transport error."""
    try:
        executor.close()
    except Exception as error:
        warnings.warn(f"Density SSH cleanup failed ({type(error).__name__}); the recorded command outcome is unchanged.", RuntimeWarning)


@dataclass(frozen=True)
class DensityTask:
    remote_path: str
    data: dict

    @property
    def task_id(self): return UUID(self.data["task_id"])
    @property
    def name(self): return self.data["name"]
    @property
    def created_at(self):
        value = datetime.fromisoformat(self.data["created_at"])
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Density task creation time must be timezone-aware")
        return value
    @property
    def attempt(self): return self.data["attempts"][-1]
    @property
    def state(self): return self.attempt["state"]
    @property
    def job_id(self): return self.attempt.get("job_id")
    @property
    def message(self): return self.attempt.get("message", self.state)
    @property
    def component_states(self):
        raw = self.attempt.get("component_states")
        if (
            isinstance(raw, dict)
            and set(raw) == set(COMPONENTS)
            and all(value in COMPONENT_PROGRESS_STATES for value in raw.values())
        ):
            return {name: raw[name] for name in COMPONENTS}
        return _sequential_component_states(
            self.attempt["components"],
            {
                "PREPARED": "NOT_STARTED",
                "QUEUED": "QUEUED",
                "RUNNING": "RUNNING",
                "OUTPUT_READY": "OUTPUT_READY",
                "COMPLETE": "COMPLETE",
                "FAILED": "FAILED",
                "CANCELLED": "CANCELLED",
                "UNKNOWN": "UNKNOWN",
                "UNRESOLVED": "UNRESOLVED",
            }[self.state],
        )
    @property
    def partition(self):
        structure = MolecularStructure(tuple(Atom(i, *row) for i, row in enumerate(self.data["atoms"])))
        return FragmentPartition(structure, tuple(self.data["subset1"]), tuple(self.data["subset2"]))
    @property
    def grid(self):
        raw = self.data["grid"]
        return DensityGrid(tuple(raw["center"]), tuple(raw["dimensions"]), raw["spacing"])
    @property
    def settings(self):
        raw = dict(self.data["settings"])
        for name in COMPONENTS:
            state = raw[name]
            spin = dict(state["spin"])
            if spin["initialization_mode"] is not None:
                spin["initialization_mode"] = SpinInitializationMode(spin["initialization_mode"])
            raw[name] = DensityElectronicState(state["charge"], SpinSettings(**spin))
        return DensitySettings(**(raw | {"xc": XCFunctional(raw["xc"]), "species_accuracy": SpeciesAccuracy(raw["species_accuracy"])}))


@dataclass(frozen=True, slots=True)
class DensityCancellationResult:
    """One exact-job cancellation outcome and its last reconciled task."""

    outcome: DensityCancellationOutcome
    task: DensityTask
    job_id: str

    def __post_init__(self):
        if not isinstance(self.outcome, DensityCancellationOutcome):
            raise TypeError("density cancellation outcome is unsupported")
        if not isinstance(self.task, DensityTask):
            raise TypeError("density cancellation requires a DensityTask")
        if self.task.job_id != self.job_id:
            raise ValueError("density cancellation result job identity changed")


def _sequential_component_states(components, current_state):
    """Map one sequential allocation to three independently visible stages."""

    states = {}
    unvalidated = [
        name for name in COMPONENTS if not components[name].get("validated")
    ]
    for name in COMPONENTS:
        states[name] = (
            "COMPLETE" if components[name].get("validated") else "NOT_STARTED"
        )
    if current_state in {"UNKNOWN", "UNRESOLVED"}:
        for name in unvalidated:
            states[name] = current_state
    elif current_state in {"OUTPUT_READY", "COMPLETE"}:
        for name in unvalidated:
            states[name] = current_state
    elif unvalidated:
        states[unvalidated[0]] = current_state
    return states


def _context(profile):
    return {"profile_id": str(profile.profile_id), "host": profile.host, "port": profile.port,
            "username": profile.username, "root": profile.remote_project_root}


def density_task_from_manifest(profile, remote_path, data):
    """Validate one authoritative density manifest already read from its root."""

    path = PurePosixPath(remote_path)
    if str(path.parent) != profile.remote_project_root or path.name in {"", ".", ".."}:
        raise ValueError("Density task must be directly under this server's working root")
    if data.get("kind") != "ELECTRON_DENSITY_DIFFERENCE" or data.get("schema") != 1:
        raise ValueError("Unsupported density task manifest")
    if data.get("context") != _context(profile):
        raise ValueError("Density task belongs to a different server/profile context")
    task = DensityTask(str(path), data)
    task.task_id, task.created_at, task.partition, task.grid, task.settings
    attempts = data["attempts"]
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("Density attempt history is missing")
    for number, attempt in enumerate(attempts, 1):
        if attempt["number"] != number or attempt["state"] not in ACTIVE | {"PREPARED", "FAILED", "CANCELLED", "OUTPUT_READY", "COMPLETE"}:
            raise ValueError("Invalid density attempt history")
        try:
            SchedulerKind(
                attempt.get("scheduler_kind", SchedulerKind.SLURM.value)
            )
        except (TypeError, ValueError):
            raise ValueError("Invalid density scheduler provenance") from None
        for name, component in attempt["components"].items():
            if name not in COMPONENTS or not 1 <= component["attempt"] <= number:
                raise ValueError("Invalid component provenance")
            hashes = component["input_hashes"]
            if set(hashes) != {"geometry.in", "control.in"} or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()):
                raise ValueError("Density input checksums are missing or invalid")
        if set(attempt["components"]) != set(COMPONENTS):
            raise ValueError("Incomplete density component manifest")
        component_states = attempt.get("component_states")
        if component_states is not None and (
            not isinstance(component_states, dict)
            or set(component_states) != set(COMPONENTS)
            or any(
                value not in COMPONENT_PROGRESS_STATES
                for value in component_states.values()
            )
        ):
            raise ValueError("Invalid density component progress")
    return task


def _json(data):
    def encode(value):
        if isinstance(value, Enum): return value.value
        raise TypeError(f"Unsupported manifest value: {type(value).__name__}")
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False, default=encode) + "\n").encode("utf-8")


def serialize_density_task_manifest(task: DensityTask) -> bytes:
    """Return the canonical persisted form used for manifest identity checks."""

    if not isinstance(task, DensityTask):
        raise TypeError("density manifest serialization requires a DensityTask")
    return _json(task.data)


def render_density_script(preset, task_id, component_directories, mail_settings=None):
    """The `exec` launcher is contained in a subshell, never a pipeline/background job."""
    lines, launch = render_fhi_batch_parts(preset, f"AT-{task_id.hex[:8].upper()}-DENSITY", mail_settings)
    last_directive = max(
        i
        for i, line in enumerate(lines)
        if line.startswith(("#SBATCH", "#BSUB"))
    )
    lines.insert(last_directive + 1, "set -e")
    for name in COMPONENTS:
        if name not in component_directories:
            continue
        directory = component_directories[name]
        if not isinstance(directory, str) or not directory.startswith("/") or any(c in directory for c in "\n\r\x00"):
            raise ValueError("Density component requires a safe absolute directory")
        lines.extend([f"# {name}", f"( cd {shlex.quote(directory)} && {launch} ) > {shlex.quote(directory + '/aims.out')} 2>&1",
                      f"test -s {shlex.quote(directory + '/density.cube')}",
                      f"grep -Eq '^[[:space:]]*Self-consistency cycle converged\\.[[:space:]]*$' {shlex.quote(directory + '/aims.out')}",
                      f"grep -Eq '^[[:space:]]*Have a nice day\\.[[:space:]]*$' {shlex.quote(directory + '/aims.out')}"])
    return "\n".join(lines) + "\n"


class DensityWorkflowService:
    def __init__(self, connection_service, cache_directory):
        self.connection_service = connection_service
        self.cache_directory = Path(cache_directory)

    def _load(self, executor, profile, remote_path):
        path = PurePosixPath(remote_path)
        if str(path.parent) != profile.remote_project_root or path.name in {"", ".", ".."}:
            raise ValueError("Density task must be directly under this server's working root")
        manifest_path, manifest_stat = resolve_existing_managed_metadata_file(
            executor,
            str(path),
            MANIFEST,
        )
        if manifest_stat.is_directory:
            raise ValueError("density manifest path is a directory")
        data = json.loads(executor.read_bytes(manifest_path))
        return density_task_from_manifest(profile, str(path), data)

    def _save(self, executor, task, previous=None):
        if previous is None:
            directory = managed_metadata_directory_path(task.remote_path)
        else:
            manifest_path, _ = resolve_existing_managed_metadata_file(
                executor,
                task.remote_path,
                MANIFEST,
            )
            directory = str(PurePosixPath(manifest_path).parent)
        payload = _json(task.data)
        if previous is None:
            upload_new_files_atomically(executor, directory, {MANIFEST: payload}, temporary_id_factory=lambda: uuid4().hex)
        else:
            replace_existing_file_atomically(executor, directory, MANIFEST, payload,
                expected_existing_digest=sha256(_json(previous.data)).hexdigest(), temporary_id_factory=lambda: uuid4().hex)
        self._cache_manifest(task)

    def _cache_manifest(self, task):
        destination = self.cache_directory / str(task.task_id)
        destination.mkdir(parents=True, exist_ok=True)
        temporary = destination / (MANIFEST + ".tmp")
        temporary.write_bytes(_json(task.data))
        temporary.replace(destination / MANIFEST)

    def discover(
        self,
        profile,
        supplied_password=None,
        *,
        stop_token: RemoteOperationStopToken | None = None,
    ):
        if stop_token is not None:
            stop_token.checkpoint()
        executor = self.connection_service.connect_for_remote_operation(
            profile, supplied_password,
            **({"stop_token": stop_token} if stop_token is not None else {}),
        )
        try:
            if stop_token is not None:
                stop_token.bind_executor(executor)
                stop_token.checkpoint()
            tasks, problems = [], []
            for entry in executor.list_directory(profile.remote_project_root):
                if stop_token is not None:
                    stop_token.checkpoint()
                if not entry.is_directory or entry.name.startswith(".") or "/" in entry.name or entry.name in {".", ".."}:
                    continue
                path = str(PurePosixPath(profile.remote_project_root) / entry.name)
                try:
                    tasks.append(self._load(executor, profile, path))
                except RemotePathNotFoundError:
                    continue
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    ManagedMetadataLocationError,
                ) as error:
                    problems.append(f"{entry.name}: {error}")
            if stop_token is not None:
                stop_token.checkpoint()
            return tuple(tasks), tuple(problems)
        except Exception:
            if stop_token is not None:
                stop_token.checkpoint()
            raise
        finally:
            if stop_token is not None:
                stop_token.unbind_executor(executor)
            _close_density_executor(executor)

    def submit(self, profile, name, partition, settings, grid, supplied_password=None):
        validate_project_base_name(name)
        preset = profile.execution_preset
        if preset is None:
            raise ValueError("Configure this server's FHI-aims runtime and cluster resources first")
        input_plan = DensityInputPlan(partition, settings, grid)
        executor = self.connection_service.connect_for_remote_operation(profile, supplied_password)
        try:
            scheduler = resolve_slurm_for_submission(executor, preset)
            verify_configured_fhi_runtime(executor, preset)
            species_library = acquire_species_library(
                executor,
                preset.fhi_species_defaults_path,
                input_plan.species_requirements,
            )
            bundle = input_plan.materialize(species_library)
            now = datetime.now(timezone.utc)
            for candidate in project_directory_candidates(name + "_Density", now.date()):
                remote_path = str(PurePosixPath(profile.remote_project_root) / candidate)
                try:
                    executor.mkdir(remote_path)
                    break
                except RemotePathAlreadyExistsError:
                    continue
            executor.mkdir(managed_metadata_directory_path(remote_path))
            data = {"kind": "ELECTRON_DENSITY_DIFFERENCE", "schema": 1, "task_id": str(uuid4()),
                    "name": name, "context": _context(profile), "created_at": now.isoformat(),
                    "atoms": [[a.element, a.x, a.y, a.z] for a in partition.structure],
                    "subset1": partition.subset1, "subset2": partition.subset2,
                    "settings": json.loads(_json(asdict(settings))), "grid": asdict(grid), "attempts": []}
            task = DensityTask(remote_path, data)
            return self._prepare_dispatch(executor, profile, task, bundle, scheduler, previous=None)
        finally:
            _close_density_executor(executor)

    def _prepare_dispatch(self, executor, profile, task, bundle, scheduler, previous):
        number = len(task.data["attempts"]) + 1
        attempt_dir = f"{task.remote_path}/attempt{number:02d}"
        executor.mkdir(attempt_dir)
        components, run = {}, {}
        files = dict(bundle.files)
        for name in COMPONENTS:
            old = task.attempt["components"][name] if task.data["attempts"] else None
            if old is not None and old.get("validated"):
                components[name] = dict(old)
                continue
            directory = attempt_dir + "/" + name
            executor.mkdir(directory)
            inputs = {filename: files[name + "/" + filename] for filename in ("geometry.in", "control.in")}
            hashes = dict(upload_new_files_atomically(executor, directory, inputs, temporary_id_factory=lambda: uuid4().hex))
            components[name] = {"attempt": number, "validated": False, "input_hashes": hashes}
            run[name] = directory
        scheduler_kind = profile.execution_preset.scheduler_kind
        scheduler_name = scheduler_display_name(scheduler_kind)
        preset = replace(
            profile.execution_preset,
            slurm_output_filename=(
                "lsf.out" if scheduler_kind is SchedulerKind.LSF else "slurm.out"
            ),
        )
        script = render_density_script(preset, task.task_id, run, profile.slurm_mail_settings).encode()
        upload_new_files_atomically(executor, attempt_dir, {"submit.sh": script, "atom_map.csv": files["atom_map.csv"]}, temporary_id_factory=lambda: uuid4().hex)
        attempt = {"number": number, "state": "UNKNOWN", "job_id": None, "components": components,
                   "component_states": _sequential_component_states(components, "UNKNOWN"),
                   "scheduler_bin": scheduler.bin_directory,
                   "scheduler_kind": preset.scheduler_kind.value,
                   "lsf_env_directory": scheduler.lsf_env_directory,
                   "lsf_library_directory": scheduler.lsf_library_directory,
                   "lsf_server_directory": scheduler.lsf_server_directory,
                   "resources": asdict(preset),
                   "message": "Submission receipt not yet recorded; do not retry an ambiguous dispatch."}
        updated = DensityTask(task.remote_path, task.data | {"attempts": [*task.data["attempts"], attempt]})
        # Durable UNKNOWN before dispatch: a crash/lost SSH response must never permit automatic resubmission.
        self._save(executor, updated, previous)
        command = build_sbatch_submission_command(
            attempt_dir,
            scheduler.sbatch_path,
            lsf_env_directory=scheduler.lsf_env_directory,
            lsf_library_directory=scheduler.lsf_library_directory,
            lsf_server_directory=scheduler.lsf_server_directory,
        )
        final = updated
        try:
            result = executor.execute(command)  # exactly once; transport exceptions retain their type
            if result.exit_status != 0:
                changes = {
                    "state": "FAILED",
                    "component_states": _sequential_component_states(
                        components, "FAILED"
                    ),
                    "message": (
                        f"{scheduler_name} rejected the request "
                        f"(exit {result.exit_status})."
                    ),
                }
            else:
                receipt = parse_sbatch_parsable_output(result.stdout)
                changes = {
                    "state": "QUEUED",
                    "job_id": receipt.job_id,
                    "component_states": _sequential_component_states(
                        components, "QUEUED"
                    ),
                    "message": f"{scheduler_name} Job {receipt.job_id} submitted.",
                }
            final = self._changed(updated, **changes)
            self._cache_manifest(final)
            self._save(executor, final, updated)
        except Exception as error:
            # Keep the original error type AND lock the UI to the durable attempt.
            error.density_task = final
            raise
        return final

    @staticmethod
    def _changed(task, **changes):
        return DensityTask(task.remote_path, task.data | {"attempts": [*task.data["attempts"][:-1], task.attempt | changes]})

    def _component_directory(self, task, name):
        return f"{task.remote_path}/attempt{task.attempt['components'][name]['attempt']:02d}/{name}"

    def _component_observation(self, executor, task, name):
        """Return ready/present/error without hiding a transport failure."""

        directory = self._component_directory(task, name)
        try:
            output = executor.read_file_tail(
                directory + "/aims.out", 4 * 1024 * 1024
            )
        except RemotePathNotFoundError as error:
            return False, False, str(error)
        try:
            parse_density_output(output, task.partition.geometry(name))
            cube = executor.stat(directory + "/density.cube")
            if cube.size is None or cube.size <= 0:
                raise ValueError("Density Cube is empty")
        except (RemotePathNotFoundError, ValueError) as error:
            return False, True, str(error)
        return True, True, None

    def _observed_component_states(
        self,
        executor,
        task,
        status_kind,
        *,
        terminal_component_state="FAILED",
    ):
        if terminal_component_state not in {"FAILED", "CANCELLED"}:
            raise ValueError("Unsupported terminal density component state")
        components = task.attempt["components"]
        if status_kind is SchedulerStatusKind.QUEUED:
            return _sequential_component_states(components, "QUEUED"), {}
        if status_kind in {
            SchedulerStatusKind.ACCOUNTING_PENDING,
            SchedulerStatusKind.UNRESOLVED,
        }:
            return _sequential_component_states(components, "UNRESOLVED"), {}

        observations = {}
        for name in COMPONENTS:
            if not components[name].get("validated"):
                observations[name] = self._component_observation(
                    executor, task, name
                )

        if status_kind is SchedulerStatusKind.RUNNING:
            states = _sequential_component_states(components, "NOT_STARTED")
            active_assigned = False
            for name in COMPONENTS:
                if components[name].get("validated"):
                    continue
                ready, _present, _error = observations[name]
                if ready:
                    states[name] = "OUTPUT_READY"
                elif not active_assigned:
                    states[name] = "RUNNING"
                    active_assigned = True
            return states, observations

        states = _sequential_component_states(components, "NOT_STARTED")
        failed_seen = False
        for name in COMPONENTS:
            if components[name].get("validated"):
                continue
            ready, present, _error = observations[name]
            if ready:
                states[name] = "OUTPUT_READY"
            elif present or not failed_seen:
                states[name] = terminal_component_state
                failed_seen = True
        return states, observations

    def _refresh(self, executor, profile, task, *, stop_token=None):
        if stop_token is not None:
            stop_token.checkpoint()
        if task.state == "COMPLETE":
            return task
        if not task.job_id:
            receipt_path = self.cache_directory / str(task.task_id) / MANIFEST
            if task.state == "UNKNOWN" and receipt_path.exists():
                cached = DensityTask(task.remote_path, json.loads(receipt_path.read_bytes()))
                if (cached.data.get("context") == task.data["context"] and cached.data.get("task_id") == task.data["task_id"]
                    and cached.attempt["number"] == task.attempt["number"] and cached.attempt["components"] == task.attempt["components"]
                    and cached.job_id):
                    recovered = self._changed(
                        task,
                        job_id=cached.job_id,
                        state="QUEUED",
                        component_states=_sequential_component_states(
                            task.attempt["components"], "QUEUED"
                        ),
                        message=(
                            "Recovered the locally recorded scheduler receipt; "
                            "no resubmission."
                        ),
                    )
                    if stop_token is not None:
                        stop_token.checkpoint()
                    self._save(executor, recovered, task)
                    task = recovered
            if not task.job_id: return task
        bin_dir = task.attempt["scheduler_bin"]
        scheduler_kind = SchedulerKind(
            task.attempt.get("scheduler_kind", SchedulerKind.SLURM.value)
        )
        preset = profile.execution_preset
        if preset is not None and preset.scheduler_kind is not scheduler_kind:
            raise ValueError(
                f"This density job was submitted through {scheduler_kind.value}, "
                f"but the current profile uses {preset.scheduler_kind.value}. "
                "Restore the matching scheduler setting before refreshing."
            )
        active_name = "bjobs" if scheduler_kind is SchedulerKind.LSF else "squeue"
        accounting_name = "bhist" if scheduler_kind is SchedulerKind.LSF else "sacct"
        lsf_env_directory = task.attempt.get("lsf_env_directory")
        lsf_library_directory = task.attempt.get("lsf_library_directory")
        lsf_server_directory = task.attempt.get("lsf_server_directory")
        if scheduler_kind is SchedulerKind.LSF and any(
            not value
            for value in (
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            )
        ):
            if preset is None:
                raise ValueError(
                    "Restore the LSF server profile before refreshing this density job."
                )
            resolved = resolve_slurm_for_submission(executor, preset)
            lsf_env_directory = resolved.lsf_env_directory
            lsf_library_directory = resolved.lsf_library_directory
            lsf_server_directory = resolved.lsf_server_directory
        status = query_slurm_job_status(executor, squeue_path=bin_dir + "/" + active_name, sacct_path=bin_dir + "/" + accounting_name,
                                       job_id=task.job_id, profile_username=profile.username,
                                       lsf_env_directory=lsf_env_directory,
                                       lsf_library_directory=lsf_library_directory,
                                       lsf_server_directory=lsf_server_directory)
        scheduler_cancelled = (
            status.kind is SchedulerStatusKind.FAILED
            and status.scheduler_state == "CANCELLED"
        )
        state = "CANCELLED" if scheduler_cancelled else status.kind.value
        detail = f"{status.scheduler_state or state}; ExitCode={status.exit_code or 'unavailable'}"
        component_states, observations = self._observed_component_states(
            executor,
            task,
            status.kind,
            terminal_component_state=(
                "CANCELLED" if scheduler_cancelled else "FAILED"
            ),
        )
        if status.kind is SchedulerStatusKind.COMPLETED:
            errors = [
                f"{name}: {observation[2]}"
                for name, observation in observations.items()
                if not observation[0]
            ]
            state = "FAILED" if errors or task.attempt.get("result_validation_failed") else "OUTPUT_READY"
            detail += "\n" + ("\n".join(errors) if errors else "All three outputs present. Recover results to validate Cube data/grid." if state != "COMPLETE" else "All three component outputs and grids validated.")
        elif status.kind is SchedulerStatusKind.ACCOUNTING_PENDING:
            state = "UNRESOLVED"
        updated = self._changed(
            task,
            state=state,
            component_states=component_states,
            message=detail,
            lsf_env_directory=lsf_env_directory,
            lsf_library_directory=lsf_library_directory,
            lsf_server_directory=lsf_server_directory,
        )
        if stop_token is not None:
            stop_token.checkpoint()
        self._save(executor, updated, task)
        return updated

    def refresh(self, profile, remote_path, supplied_password=None, *, stop_token=None):
        if stop_token is not None:
            stop_token.checkpoint()
        executor = self.connection_service.connect_for_remote_operation(
            profile, supplied_password,
            **({"stop_token": stop_token} if stop_token is not None else {}),
        )
        try:
            if stop_token is not None:
                stop_token.bind_executor(executor)
                stop_token.checkpoint()
            task = self._refresh(
                executor, profile, self._load(executor, profile, remote_path),
                stop_token=stop_token,
            )
            if stop_token is not None:
                stop_token.checkpoint()
            return task
        except Exception:
            if stop_token is not None:
                stop_token.checkpoint()
            raise
        finally:
            if stop_token is not None:
                stop_token.unbind_executor(executor)
            _close_density_executor(executor)

    def _output_sizes(self, executor, task):
        sizes = {}
        for name in COMPONENTS:
            directory = self._component_directory(task, name)
            for filename in ("aims.out", "density.cube"):
                stat = executor.stat(directory + "/" + filename)
                if stat.is_directory:
                    raise ValueError(f"Density result is not a file: {name}/{filename}")
                if stat.size is not None and (
                    isinstance(stat.size, bool) or stat.size < 0
                ):
                    raise ValueError(f"Density result size is invalid: {name}/{filename}")
                sizes[(name, filename)] = stat.size
        return sizes

    def _download_component(
        self,
        executor,
        task,
        name,
        *,
        tracker=None,
        output_sizes=None,
    ):
        component = task.attempt["components"][name]
        directory = self._component_directory(task, name)
        local = self.cache_directory / str(task.task_id) / f"attempt{component['attempt']:02d}" / name
        local.mkdir(parents=True, exist_ok=True)
        for filename, digest in component["input_hashes"].items():
            if filename not in {"geometry.in", "control.in"}:
                raise ValueError("Unexpected density input file")
            raw = executor.read_bytes(directory + "/" + filename)
            if sha256(raw).hexdigest() != digest:
                raise DensityIntegrityError(f"Immutable {name}/{filename} was changed")
            (local / filename).write_bytes(raw)
        for filename in ("aims.out", "density.cube"):
            target = local / filename
            remote = directory + "/" + filename
            expected_size = (
                output_sizes[(name, filename)]
                if output_sizes is not None
                else executor.stat(remote).size
            )
            # A validated immutable terminal result can use its content-addressed local cache.
            digest = component.get("output_hashes", {}).get(filename)
            if digest and target.exists() and _file_sha256(target) == digest:
                local_size = target.stat().st_size
                if expected_size is not None and local_size != expected_size:
                    raise DensityIntegrityError(
                        f"Previously validated {name}/{filename} changed size remotely"
                    )
                if tracker is not None:
                    tracker.complete(name, filename, local_size, cached=True)
                continue
            temporary = target.with_name(target.name + ".tmp-" + uuid4().hex)
            try:
                executor.download_file(
                    remote,
                    str(temporary),
                    (
                        None
                        if tracker is None
                        else lambda current, total, selected=name, selected_file=filename: tracker.update(
                            selected, selected_file, current, total
                        )
                    ),
                )
                actual_size = temporary.stat().st_size
                if expected_size is not None and actual_size != expected_size:
                    raise DensityIntegrityError(
                        f"Remote {name}/{filename} size changed during recovery"
                    )
                actual_digest = _file_sha256(temporary)
                if digest and actual_digest != digest:
                    raise DensityIntegrityError(
                        f"Previously validated {name}/{filename} was changed"
                    )
                temporary.replace(target)
                if tracker is not None:
                    tracker.complete(name, filename, actual_size)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return local

    def recover(
        self,
        profile,
        remote_path,
        supplied_password=None,
        *,
        progress: Callable[[DensityRecoveryProgress], None] | None = None,
    ):
        report = _progress_reporter(progress)
        report(
            DensityRecoveryProgress(
                DensityRecoveryPhase.LOCATING,
                "Connecting and locating density result files…",
            )
        )
        executor = self.connection_service.connect_for_remote_operation(profile, supplied_password)
        try:
            task = self._refresh(executor, profile, self._load(executor, profile, remote_path))
            if task.state not in {
                "OUTPUT_READY",
                "COMPLETE",
                "FAILED",
                "CANCELLED",
            }:
                raise ValueError("Three completed density outputs are required; " + task.message)
            sizes = self._output_sizes(executor, task)
            tracker = _DensityTransferTracker(report, sizes)
            directories = {
                name: self._download_component(
                    executor,
                    task,
                    name,
                    tracker=tracker,
                    output_sizes=sizes,
                )
                for name in COMPONENTS
            }
            try:
                result = build_density_result(
                    task.partition,
                    task.grid,
                    directories,
                    progress=lambda message: report(
                        DensityRecoveryProgress(
                            DensityRecoveryPhase.PROCESSING,
                            message,
                            retrieved_bytes=tracker.retrieved_bytes,
                            total_bytes=tracker.total_bytes,
                        )
                    ),
                )
            except ValueError as error:
                failed = self._changed(task, state="FAILED", result_validation_failed=True, message=str(error))
                self._save(executor, failed, task)
                raise
            components = {name: task.attempt["components"][name] | {"validated": True, "output_hashes": {
                filename: _file_sha256(directories[name] / filename) for filename in ("aims.out", "density.cube")
            }} for name in COMPONENTS}
            complete = self._changed(
                task,
                state="COMPLETE",
                components=components,
                component_states={name: "COMPLETE" for name in COMPONENTS},
                message="Three fixed-geometry SCFs, Hirshfeld records and common-grid densities validated.\nScheduler evidence: " + task.message,
            )
            self._save(executor, complete, task)
            return complete, replace(result, manifest=json.loads(_json(complete.data)))
        finally:
            _close_density_executor(executor)

    def retry(self, profile, remote_path, supplied_password=None):
        executor = self.connection_service.connect_for_remote_operation(profile, supplied_password)
        try:
            task = self._refresh(executor, profile, self._load(executor, profile, remote_path))
            if task.state not in {"FAILED", "CANCELLED"}:
                raise ValueError(
                    "Retry requires a confirmed failed or cancelled inactive "
                    "attempt; UNKNOWN is never retried"
                )
            components = {name: dict(record) for name, record in task.attempt["components"].items()}
            for name in COMPONENTS:
                try:
                    directory = self._download_component(executor, task, name)
                    validate_component(directory, task.partition.geometry(name), task.grid)
                except (RemotePathNotFoundError, ValueError) as error:
                    components[name]["validation_error"] = str(error)
                    components[name]["validated"] = False
                else:
                    components[name].update(validated=True, output_hashes={filename: _file_sha256(directory / filename) for filename in ("aims.out", "density.cube")})
            checked = self._changed(task, components=components)
            if all(record.get("validated") for record in components.values()):
                raise ValueError("All component results validate; recover results instead of submitting a redundant job")
            scheduler = resolve_slurm_for_submission(executor, profile.execution_preset)
            verify_configured_fhi_runtime(executor, profile.execution_preset)
            files = []
            for name in COMPONENTS:
                directory = self._component_directory(task, name)
                for filename in ("geometry.in", "control.in"):
                    raw = executor.read_bytes(directory + "/" + filename)
                    if sha256(raw).hexdigest() != components[name]["input_hashes"][filename]:
                        raise DensityIntegrityError("Historical density input was changed")
                    files.append((name + "/" + filename, raw))
            files.append(("atom_map.csv", executor.read_bytes(task.remote_path + "/attempt01/atom_map.csv")))
            bundle = DensityInputBundle(task.partition, task.settings, task.grid, tuple(files))
            return self._prepare_dispatch(executor, profile, checked, bundle, scheduler, previous=task)
        finally:
            _close_density_executor(executor)

    def cancel(self, profile, remote_path, supplied_password=None):
        """Cancel the exact shared density scheduler job at most once."""

        executor = self.connection_service.connect_for_remote_operation(
            profile, supplied_password
        )
        try:
            task = self._load(executor, profile, remote_path)
            if task.job_id is None:
                raise DensityCancellationError(
                    "The density task has no exact scheduler Job ID. Refresh first."
                )
            job_id = task.job_id
            task = self._refresh(executor, profile, task)
            if task.job_id != job_id:
                raise DensityCancellationError(
                    "The density attempt changed before cancellation. Refresh first."
                )
            if task.state in {
                "FAILED",
                "CANCELLED",
                "OUTPUT_READY",
                "COMPLETE",
            }:
                return DensityCancellationResult(
                    DensityCancellationOutcome.ALREADY_TERMINAL,
                    task,
                    job_id,
                )
            if task.state not in {"QUEUED", "RUNNING"}:
                raise DensityCancellationError(
                    "The exact density job is not authoritatively active. Refresh first."
                )
            try:
                request_slurm_cancellation_once(
                    executor,
                    scancel_path=(
                        task.attempt["scheduler_bin"]
                        + (
                            "/bkill"
                            if SchedulerKind(
                                task.attempt.get(
                                    "scheduler_kind", SchedulerKind.SLURM.value
                                )
                            )
                            is SchedulerKind.LSF
                            else "/scancel"
                        )
                    ),
                    job_id=job_id,
                    lsf_env_directory=task.attempt.get("lsf_env_directory"),
                    lsf_library_directory=task.attempt.get(
                        "lsf_library_directory"
                    ),
                    lsf_server_directory=task.attempt.get(
                        "lsf_server_directory"
                    ),
                )
            except RemoteCommandOutcomeUnknown:
                return DensityCancellationResult(
                    DensityCancellationOutcome.UNKNOWN,
                    task,
                    job_id,
                )
            return DensityCancellationResult(
                DensityCancellationOutcome.REQUESTED,
                task,
                job_id,
            )
        finally:
            _close_density_executor(executor)
