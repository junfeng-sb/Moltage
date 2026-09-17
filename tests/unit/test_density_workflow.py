"""In-memory remote operations only: never opens SSH or dispatches a job."""

from dataclasses import replace
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import unittest
from unittest.mock import patch

from moltage.app.density_workflow import (
    DensityCancellationOutcome,
    DensityIntegrityError,
    DensityRecoveryPhase,
    DensityTask,
    DensityWorkflowService,
)
from moltage.aims.density_difference import DensityInputPlan, DensitySettings
from moltage.aims.species_library import species_default_filename
from moltage.domain.density_difference import DensityGrid, COMPONENTS
from moltage.remote.executor import (RemoteCommandResult, RemotePathNotFoundError, RemotePathAlreadyExistsError,
    RemotePathStat, RemoteDirectoryEntry, RemoteCommandOutcomeUnknown, RemoteConnectionError)
from moltage.remote.executor import RemoteOperationStopped, RemoteOperationStopToken
from test_density_difference import partition, output, component_files
from phase2b1_test_support import profile
from species_test_support import install_remote_species
from species_test_support import TEST_SPECIES_ROOT, synthetic_species_text
from moltage.aims.optimization_settings import SpeciesAccuracy


class MemoryRemote:
    def __init__(self):
        self.files = {}
        self.dirs = {"/srv/moltage-test/projects"}
        self.commands = []
        self.reads = []
        self.dispatch_error = None
        self.connection_error = False
        self.receipt_error = False
        self.scheduler_state = "RUNNING"
        self.exit_code = "0:0"
        self.next_job = 101
        self.cancel_error = None
        self.cancel_exit_status = 0
        self.closed = 0
        self.missing_species_paths = set()

    def _synthetic_species_bytes(self, path):
        if path in self.missing_species_paths:
            return None
        match = re.fullmatch(
            re.escape(TEST_SPECIES_ROOT)
            + r"/(light|tight|really_tight)/\d+_([A-Za-z]+)_default",
            path,
        )
        if match is None:
            return None
        return synthetic_species_text(
            match.group(2),
            SpeciesAccuracy(match.group(1)),
        ).encode("utf-8")

    def close(self): self.closed += 1
    def stat(self, path):
        if path in self.dirs: return RemotePathStat(True)
        data = self.files.get(path)
        if data is None:
            data = self._synthetic_species_bytes(path)
        if data is None: raise RemotePathNotFoundError(path)
        return RemotePathStat(False, len(data))
    def mkdir(self, path):
        if path in self.dirs: raise RemotePathAlreadyExistsError(path)
        self.dirs.add(path)
    def list_directory(self, path):
        return tuple(RemoteDirectoryEntry(PurePosixPath(p).name, p in self.dirs) for p in sorted(self.dirs | set(self.files)) if str(PurePosixPath(p).parent) == path)
    def read_bytes(self, path):
        self.reads.append(path)
        data = self.files.get(path)
        if data is None:
            data = self._synthetic_species_bytes(path)
        if data is None: raise RemotePathNotFoundError(path)
        return data
    def download_file(self, path, destination, progress=None):
        self.reads.append(path)
        if path not in self.files: raise RemotePathNotFoundError(path)
        data = self.files[path]
        midpoint = len(data) // 2
        if progress is not None:
            progress(midpoint, len(data))
        Path(destination).write_bytes(data)
        if progress is not None:
            progress(len(data), len(data))
    def read_file_tail(self, path, limit): return self.read_bytes(path)[-limit:]
    def write_bytes(self, path, data):
        if self.receipt_error and path.endswith("density.json") is False and "density.json.tmp-" in path and b'"job_id": "101"' in data:
            raise RemoteConnectionError("Synthetic failure saving receipt")
        self.files[path] = data
    def rename(self, source, destination): self.files[destination] = self.files.pop(source)
    def execute(self, command):
        self.commands.append(command)
        if self.connection_error: raise RemoteConnectionError("Synthetic transport failure")
        if command == "command -v sbatch": return RemoteCommandResult(0, b"/usr/bin/sbatch\n", b"")
        if command.endswith("sbatch --version"): return RemoteCommandResult(0, b"slurm 23.11.0\n", b"")
        if "--parsable submit.sh" in command:
            if self.dispatch_error: raise self.dispatch_error
            job = self.next_job
            self.next_job += 1
            return RemoteCommandResult(0, f"{job}\n".encode(), b"")
        if "/squeue " in command:
            data = f"{self.next_job - 1}|RUNNING\n".encode() if self.scheduler_state == "RUNNING" else b""
            return RemoteCommandResult(0, data, b"")
        if "/sacct " in command:
            return RemoteCommandResult(0, f"{self.next_job - 1}|{self.scheduler_state}|{self.exit_code}\n".encode(), b"")
        if "/scancel " in command:
            if self.cancel_error is not None:
                raise self.cancel_error
            return RemoteCommandResult(self.cancel_exit_status, b"", b"")
        raise AssertionError(command)


class MemoryConnection:
    def __init__(self, remote): self.remote = remote
    def connect_for_remote_operation(self, profile, supplied_password=None, *, stop_token=None):
        if stop_token is not None:
            stop_token.bind_executor(self.remote)
        return self.remote


class DensityWorkflowTests(unittest.TestCase):
    def test_stop_during_status_read_does_not_persist_a_late_density_update(self):
        task = self.service.submit(
            self.profile, "SyntheticStoppedRefresh", self.part,
            DensitySettings(), DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        before = dict(self.remote.files)
        token = RemoteOperationStopToken()
        execute = self.remote.execute

        def stop_during_query(command):
            result = execute(command)
            token.request_stop()
            return result

        with patch.object(self.remote, "execute", side_effect=stop_during_query):
            with self.assertRaises(RemoteOperationStopped):
                self.service.refresh(self.profile, task.remote_path, stop_token=token)
        self.assertEqual(self.remote.files, before)
        self.assertFalse(any("/scancel " in command for command in self.remote.commands))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.remote = MemoryRemote()
        self.service = DensityWorkflowService(MemoryConnection(self.remote), Path(self.temp.name) / "cache")
        self.profile = replace(profile(), aitranss_runtime=None)
        self.part = partition()
        self.grid = DensityGrid((0, 0, 0), (2, 2, 2), 0.1)
        self.input_plan = DensityInputPlan(
            self.part,
            DensitySettings(),
            self.grid,
        )
        install_remote_species(
            self.remote,
            self.input_plan.species_requirements,
            root=self.profile.execution_preset.fhi_species_defaults_path,
        )

    def submit(self): return self.service.submit(self.profile, "Example", self.part, DensitySettings(), self.grid, "SECRET_TEST_ONLY")
    def outputs(self, task):
        destination = Path(self.temp.name) / "generated"
        destination.mkdir()
        directories = component_files(destination, self.part, self.grid)
        for name in COMPONENTS:
            for filename in ("aims.out", "density.cube"):
                self.remote.files[f"{task.remote_path}/attempt01/{name}/{filename}"] = (directories[name] / filename).read_bytes()

    def test_one_job_manifest_context_no_secrets_and_discovery(self):
        task = self.submit()
        self.assertEqual(task.job_id, "101")
        self.assertEqual(task.settings, DensitySettings())
        self.assertEqual(sum("--parsable submit.sh" in c for c in self.remote.commands), 1)
        self.assertNotIn(b"SECRET_TEST_ONLY", b"".join(self.remote.files.values()))
        tasks, problems = self.service.discover(self.profile)
        self.assertFalse(problems)
        self.assertEqual(tasks[0].task_id, task.task_id)
        self.assertGreater(self.remote.closed, 0)
        with self.assertRaises(ValueError): self.service.refresh(replace(self.profile, host="elsewhere.invalid"), task.remote_path)

    def test_missing_species_definition_prevents_all_remote_mutation(self):
        requirement = self.input_plan.species_requirements[0]
        missing_path = str(
            PurePosixPath(TEST_SPECIES_ROOT)
            / requirement.accuracy.value
            / species_default_filename(requirement.element, requirement.accuracy)
        )
        self.remote.files.pop(missing_path, None)
        self.remote.missing_species_paths.add(missing_path)
        original_directories = set(self.remote.dirs)

        with self.assertRaisesRegex(
            RuntimeError,
            "Required FHI-aims species definition is missing",
        ):
            self.submit()

        self.assertEqual(self.remote.dirs, original_directories)
        self.assertFalse(
            any(path.startswith("/srv/moltage-test/projects/Example_Density") for path in self.remote.files)
        )
        self.assertFalse(any("--parsable submit.sh" in item for item in self.remote.commands))

    def test_three_controls_use_one_validated_species_block_set(self):
        task = self.submit()
        species_reads = [
            path
            for path in self.remote.reads
            if path.startswith(self.profile.execution_preset.fhi_species_defaults_path + "/")
        ]
        self.assertEqual(
            len(species_reads),
            len(set(self.input_plan.species_requirements)),
        )
        controls = tuple(
            self.remote.files[
                f"{task.remote_path}/attempt01/{component}/control.in"
            ]
            for component in COMPONENTS
        )
        for requirement in self.input_plan.species_requirements:
            marker = (
                f"# SYNTHETIC TEST SPECIES {requirement.accuracy.value}"
            ).encode("utf-8")
            self.assertTrue(all(marker in control for control in controls))

    def test_legacy_density_manifest_is_discovered_and_updated_in_place(self):
        task = self.submit()
        current_directory = task.remote_path + "/.moltage"
        legacy_directory = task.remote_path + "/.aims_transport"
        current_manifest = current_directory + "/density.json"
        legacy_manifest = legacy_directory + "/density.json"
        self.remote.dirs.remove(current_directory)
        self.remote.dirs.add(legacy_directory)
        self.remote.files[legacy_manifest] = self.remote.files.pop(current_manifest)

        tasks, problems = self.service.discover(self.profile)
        refreshed = self.service.refresh(self.profile, task.remote_path)

        self.assertFalse(problems)
        self.assertEqual(tasks[0].task_id, task.task_id)
        self.assertEqual(refreshed.state, "RUNNING")
        self.assertIn(legacy_manifest, self.remote.files)
        self.assertNotIn(current_manifest, self.remote.files)

    def test_unknown_dispatch_is_durable_typed_and_never_retried(self):
        self.remote.dispatch_error = RemoteCommandOutcomeUnknown("Synthetic ambiguous dispatch")
        with self.assertRaises(RemoteCommandOutcomeUnknown) as caught: self.submit()
        task = caught.exception.density_task
        self.assertEqual(task.state, "UNKNOWN")
        self.remote.dispatch_error = None
        with self.assertRaises(ValueError): self.service.retry(self.profile, task.remote_path)
        self.assertEqual(sum("--parsable submit.sh" in c for c in self.remote.commands), 1)

    def test_receipt_recording_loss_recovered_locally_without_dispatch(self):
        self.remote.receipt_error = True
        with self.assertRaises(RemoteConnectionError) as caught: self.submit()
        self.remote.receipt_error = False
        task = self.service.refresh(self.profile, caught.exception.density_task.remote_path)
        self.assertEqual(task.job_id, "101")
        self.assertEqual(task.state, "RUNNING")
        self.assertEqual(sum("--parsable submit.sh" in c for c in self.remote.commands), 1)

    def test_transport_not_scientific_failure(self):
        task = self.submit()
        self.remote.connection_error = True
        with self.assertRaises(RemoteConnectionError): self.service.refresh(self.profile, task.remote_path)
        stored = json.loads(self.remote.files[task.remote_path + "/.moltage/density.json"])
        self.assertEqual(stored["attempts"][-1]["state"], "QUEUED")

    def test_scheduler_zero_insufficient_then_recovery_and_cached_cubes(self):
        task = self.submit()
        self.remote.scheduler_state = "COMPLETED"
        self.assertEqual(self.service.refresh(self.profile, task.remote_path).state, "FAILED")
        self.outputs(task)
        self.remote.reads.clear()
        ready = self.service.refresh(self.profile, task.remote_path)
        self.assertEqual(ready.state, "OUTPUT_READY")
        self.assertFalse(any(p.endswith(".cube") for p in self.remote.reads))
        completed, result = self.service.recover(self.profile, task.remote_path)
        self.assertEqual(completed.state, "COMPLETE")
        self.assertEqual(result.electron_gains, (-0.1, 0.2, -0.1))
        self.remote.reads.clear()
        self.service.recover(self.profile, task.remote_path)
        self.assertFalse(any(p.endswith(".cube") for p in self.remote.reads))

    def test_legacy_profile_without_species_root_can_recover_existing_task(self):
        task = self.submit()
        self.outputs(task)
        self.remote.scheduler_state = "COMPLETED"
        legacy_profile = replace(
            self.profile,
            execution_preset=replace(
                self.profile.execution_preset,
                fhi_species_defaults_path=None,
            ),
        )
        self.remote.reads.clear()

        refreshed = self.service.refresh(legacy_profile, task.remote_path)
        completed, _result = self.service.recover(
            legacy_profile,
            task.remote_path,
        )

        self.assertEqual(refreshed.state, "OUTPUT_READY")
        self.assertEqual(completed.state, "COMPLETE")
        self.assertFalse(any(path.endswith("_default") for path in self.remote.reads))

    def test_recovery_reports_streamed_file_and_aggregate_byte_progress(self):
        task = self.submit()
        self.outputs(task)
        self.remote.scheduler_state = "COMPLETED"
        progress = []

        completed, _result = self.service.recover(
            self.profile,
            task.remote_path,
            progress=progress.append,
        )

        self.assertEqual(completed.state, "COMPLETE")
        phases = {event.phase for event in progress}
        self.assertIn(DensityRecoveryPhase.LOCATING, phases)
        self.assertIn(DensityRecoveryPhase.DOWNLOADING, phases)
        self.assertIn(DensityRecoveryPhase.PROCESSING, phases)
        downloads = [
            event
            for event in progress
            if event.phase is DensityRecoveryPhase.DOWNLOADING
        ]
        self.assertTrue(
            any("Subset 2 / density.cube" in event.message for event in downloads)
        )
        self.assertEqual(downloads[-1].retrieved_bytes, downloads[-1].total_bytes)
        self.assertEqual(
            [event.retrieved_bytes for event in downloads],
            sorted(event.retrieved_bytes for event in downloads),
        )
        self.assertFalse(
            tuple((Path(self.temp.name) / "cache").rglob("*.tmp-*"))
        )

    def test_interrupted_stream_never_installs_or_leaves_a_partial_cube(self):
        task = self.submit()
        self.outputs(task)
        self.remote.scheduler_state = "COMPLETED"
        download = self.remote.download_file

        def interrupted(path, destination, progress=None):
            if path.endswith("/total/density.cube"):
                Path(destination).write_bytes(b"partial")
                raise RemoteConnectionError("Synthetic transfer interruption")
            download(path, destination, progress)

        self.remote.download_file = interrupted
        with self.assertRaises(RemoteConnectionError):
            self.service.recover(self.profile, task.remote_path)

        cache = Path(self.temp.name) / "cache" / str(task.task_id)
        self.assertFalse((cache / "attempt01" / "total" / "density.cube").exists())
        self.assertFalse(tuple(cache.rglob("*.tmp-*")))

    def test_retry_keeps_validated_components_and_immutable_science(self):
        task = self.submit()
        self.outputs(task)
        failed_output = task.remote_path + "/attempt01/subset2/aims.out"
        self.remote.files[failed_output] = b"STOP\n"
        self.remote.scheduler_state = "FAILED"
        self.remote.exit_code = "1:0"
        historical = dict(self.remote.files)
        retry = self.service.retry(self.profile, task.remote_path)
        self.assertEqual(retry.job_id, "102")
        self.assertEqual(retry.attempt["components"]["total"]["attempt"], 1)
        self.assertEqual(retry.attempt["components"]["subset2"]["attempt"], 2)
        script = self.remote.files[task.remote_path + "/attempt02/submit.sh"]
        self.assertNotIn(b"# total", script)
        self.assertIn(b"# subset2", script)
        self.assertEqual(self.remote.files[task.remote_path + "/attempt02/subset2/control.in"], historical[task.remote_path + "/attempt01/subset2/control.in"])
        for path, contents in historical.items():
            if "/attempt01/" in path: self.assertEqual(self.remote.files[path], contents)

    def test_byte_preserving_retry_does_not_require_species_root(self):
        task = self.submit()
        self.outputs(task)
        failed_output = task.remote_path + "/attempt01/subset2/aims.out"
        self.remote.files[failed_output] = b"STOP\n"
        self.remote.scheduler_state = "FAILED"
        self.remote.exit_code = "1:0"
        original_control = self.remote.files[
            task.remote_path + "/attempt01/subset2/control.in"
        ]
        legacy_profile = replace(
            self.profile,
            execution_preset=replace(
                self.profile.execution_preset,
                fhi_species_defaults_path=None,
            ),
        )
        self.remote.reads.clear()

        retry = self.service.retry(legacy_profile, task.remote_path)

        self.assertEqual(retry.job_id, "102")
        self.assertEqual(
            self.remote.files[task.remote_path + "/attempt02/subset2/control.in"],
            original_control,
        )
        self.assertFalse(any(path.endswith("_default") for path in self.remote.reads))

    def test_tampered_input_blocks_retry(self):
        task = self.submit()
        self.remote.scheduler_state = "FAILED"
        self.remote.files[task.remote_path + "/attempt01/total/geometry.in"] += b"# changed\n"
        with self.assertRaises(DensityIntegrityError): self.service.retry(self.profile, task.remote_path)
        self.assertEqual(sum("--parsable submit.sh" in c for c in self.remote.commands), 1)

    def test_cleanup_error_does_not_lose_known_submission(self):
        def fail_close(): raise RuntimeError("Synthetic cleanup failure")
        self.remote.close = fail_close
        with self.assertWarns(RuntimeWarning): task = self.submit()
        self.assertEqual(task.job_id, "101")
        self.assertEqual(task.state, "QUEUED")

    def test_invalid_cube_stays_failed_and_permits_explicit_retry(self):
        task = self.submit()
        self.outputs(task)
        self.remote.scheduler_state = "COMPLETED"
        self.remote.files[task.remote_path + "/attempt01/subset2/density.cube"] = b"broken cube"
        with self.assertRaises(ValueError): self.service.recover(self.profile, task.remote_path)
        failed = self.service.refresh(self.profile, task.remote_path)
        self.assertEqual(failed.state, "FAILED")
        retry = self.service.retry(self.profile, task.remote_path)
        self.assertEqual(retry.attempt["components"]["subset2"]["attempt"], 2)

    def test_final_hirshfeld_valid_on_failed_scheduler_can_be_recovered_without_job(self):
        task = self.submit()
        self.outputs(task)
        self.remote.scheduler_state = "FAILED"
        self.remote.exit_code = "1:0"
        completed, result = self.service.recover(self.profile, task.remote_path)
        self.assertEqual(completed.state, "COMPLETE")
        self.assertIn("FAILED", completed.message)
        self.assertEqual(self.service.refresh(self.profile, task.remote_path).state, "COMPLETE")
        self.assertEqual(sum("--parsable submit.sh" in c for c in self.remote.commands), 1)

    def test_three_component_progress_follows_the_sequential_outputs(self):
        task = self.submit()
        self.assertEqual(
            task.component_states,
            {"total": "QUEUED", "subset1": "NOT_STARTED", "subset2": "NOT_STARTED"},
        )
        destination = Path(self.temp.name) / "progress"
        destination.mkdir()
        directories = component_files(destination, self.part, self.grid)
        for filename in ("aims.out", "density.cube"):
            self.remote.files[
                f"{task.remote_path}/attempt01/total/{filename}"
            ] = (directories["total"] / filename).read_bytes()
        running = self.service.refresh(self.profile, task.remote_path)
        self.assertEqual(
            running.component_states,
            {"total": "OUTPUT_READY", "subset1": "RUNNING", "subset2": "NOT_STARTED"},
        )
        self.remote.scheduler_state = "FAILED"
        self.remote.exit_code = "1:0"
        self.remote.files[
            task.remote_path + "/attempt01/subset1/aims.out"
        ] = b"STOP\n"
        failed = self.service.refresh(self.profile, task.remote_path)
        self.assertEqual(
            failed.component_states,
            {"total": "OUTPUT_READY", "subset1": "FAILED", "subset2": "NOT_STARTED"},
        )

    def test_scheduler_cancellation_is_a_distinct_terminal_state(self):
        task = self.submit()
        self.remote.scheduler_state = "CANCELLED"
        cancelled = self.service.refresh(self.profile, task.remote_path)

        self.assertEqual(cancelled.state, "CANCELLED")
        self.assertEqual(
            cancelled.component_states,
            {
                "total": "CANCELLED",
                "subset1": "NOT_STARTED",
                "subset2": "NOT_STARTED",
            },
        )
        stored = json.loads(
            self.remote.files[
                task.remote_path + "/.moltage/density.json"
            ]
        )
        self.assertEqual(stored["attempts"][-1]["state"], "CANCELLED")
        result = self.service.cancel(self.profile, task.remote_path)
        self.assertIs(
            result.outcome, DensityCancellationOutcome.ALREADY_TERMINAL
        )
        self.assertFalse(
            any("/scancel " in command for command in self.remote.commands)
        )

    def test_density_cancellation_uses_the_exact_shared_job_once(self):
        task = self.submit()
        result = self.service.cancel(self.profile, task.remote_path)
        self.assertIs(result.outcome, DensityCancellationOutcome.REQUESTED)
        self.assertEqual(result.job_id, "101")
        self.assertEqual(
            [command for command in self.remote.commands if "/scancel " in command],
            ["/usr/bin/scancel 101"],
        )

    def test_ambiguous_density_cancellation_is_not_retried(self):
        task = self.submit()
        self.remote.cancel_error = RemoteCommandOutcomeUnknown(
            "Synthetic ambiguous cancellation"
        )
        result = self.service.cancel(self.profile, task.remote_path)
        self.assertIs(result.outcome, DensityCancellationOutcome.UNKNOWN)
        self.assertEqual(
            sum("/scancel " in command for command in self.remote.commands), 1
        )

    def test_terminal_density_job_is_reconciled_without_scancel(self):
        task = self.submit()
        self.remote.scheduler_state = "FAILED"
        self.remote.exit_code = "1:0"
        result = self.service.cancel(self.profile, task.remote_path)
        self.assertIs(
            result.outcome, DensityCancellationOutcome.ALREADY_TERMINAL
        )
        self.assertEqual(
            sum("/scancel " in command for command in self.remote.commands), 0
        )
