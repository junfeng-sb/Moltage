from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_recovery import (
    ProjectRecoveryService,
    Step3OomCancellationError,
    Step3OomCancellationOutcome,
    Step3OomCancellationRequest,
)
from moltage.aims.transport_evidence import SLURM_TASK_OUT_OF_MEMORY
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
)
from moltage.remote.project_manifest import serialize_project_manifest
from moltage.remote.slurm_cancel import SlurmCancellationRejected
from test_phase3b_recovery import (
    NOW,
    PROFILE,
    REVIEWED_TASK_OOM_OUTPUT,
    _install,
    _project_and_files,
)
from test_project_recovery import FixedConnectionService, RecoveryRemoteExecutor


class CancellationRemoteExecutor(RecoveryRemoteExecutor):
    def __init__(self):
        super().__init__()
        self.scancel_result = RemoteCommandResult(0, b"", b"")
        self.scancel_error = None

    def execute(self, command):
        if command.startswith("/usr/bin/scancel "):
            self.commands.append(command)
            self.operations.append(("execute", command))
            if self.scancel_error is not None:
                raise self.scancel_error
            return self.scancel_result
        return super().execute(command)


class CloseFailingCancellationRemote(CancellationRemoteExecutor):
    def close(self):
        raise RuntimeError("cleanup failed after known result")


class Step3OomCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.remote = CancellationRemoteExecutor()
        self.service = ProjectRecoveryService(
            FixedConnectionService(self.remote),
            LocalProjectIndexRepository(
                Path(self.temporary.name) / "known_projects.json"
            ),
            now_factory=lambda: NOW,
        )
        self.project, files = _project_and_files()
        files["aims.dft.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(self.remote, self.project, files)
        self.remote.squeue_stdout = b"41001|RUNNING\n"

    def _request(self):
        return Step3OomCancellationRequest(PROFILE, self.project)

    def _scancel_commands(self):
        return [command for command in self.remote.commands if "scancel" in command]

    def test_success_targets_exact_current_job_once_without_manifest_mutation(self):
        manifest = (
            self.project.remote_project_path + "/.moltage/project.json"
        )
        before = self.remote.files[manifest]

        result = self.service.cancel_active_step3_oom_job(self._request())

        self.assertIs(result.outcome, Step3OomCancellationOutcome.REQUESTED)
        self.assertEqual(result.job_id, "41001")
        self.assertEqual(self._scancel_commands(), ["/usr/bin/scancel 41001"])
        self.assertEqual(self.remote.files[manifest], before)
        self.assertFalse(any("sbatch --parsable" in item for item in self.remote.commands))
        self.assertFalse(any(item[0] in {"write", "rename"} for item in self.remote.operations))

    def test_ambiguous_dispatch_returns_unknown_and_never_retries(self):
        self.remote.scancel_error = RemoteCommandOutcomeUnknown("lost after dispatch")

        result = self.service.cancel_active_step3_oom_job(self._request())

        self.assertIs(result.outcome, Step3OomCancellationOutcome.UNKNOWN)
        self.assertEqual(self._scancel_commands(), ["/usr/bin/scancel 41001"])

    def test_definite_rejection_keeps_running_state_and_does_not_retry(self):
        self.remote.scancel_result = RemoteCommandResult(1, b"", b"denied")
        manifest = self.project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest]

        with self.assertRaises(SlurmCancellationRejected):
            self.service.cancel_active_step3_oom_job(self._request())

        self.assertEqual(self._scancel_commands(), ["/usr/bin/scancel 41001"])
        self.assertEqual(self.remote.files[manifest], before)

    def test_already_terminal_race_reconciles_without_scancel(self):
        self.remote.squeue_stdout = b""
        self.remote.sacct_stdout = b"41001|CANCELLED|0:15\n"

        result = self.service.cancel_active_step3_oom_job(self._request())

        self.assertIs(
            result.outcome,
            Step3OomCancellationOutcome.ALREADY_TERMINAL,
        )
        self.assertEqual(self._scancel_commands(), [])
        self.assertIs(result.snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(result.snapshot.active_step.scheduler_state, "CANCELLED")
        self.assertEqual(
            result.snapshot.active_step.last_error,
            SLURM_TASK_OUT_OF_MEMORY,
        )
        self.assertTrue(result.snapshot.can_retry_step3)

    def test_changed_current_job_fails_closed_before_scancel(self):
        current = self.project.steps[2]
        changed = replace(current, job_id="41006")
        changed_project = replace(
            self.project,
            revision=self.project.revision + 1,
            steps=(*self.project.steps[:2], changed, self.project.steps[3]),
        )
        manifest = self.project.remote_project_path + "/.moltage/project.json"
        self.remote.files[manifest] = serialize_project_manifest(changed_project).encode()

        with self.assertRaises(Step3OomCancellationError):
            self.service.cancel_active_step3_oom_job(self._request())

        self.assertEqual(self._scancel_commands(), [])

    def test_near_miss_output_never_dispatches(self):
        directory = self.project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/aims.dft.out"] = (
            b"memory usage high; possible OOM risk\n"
        )

        with self.assertRaises(Step3OomCancellationError):
            self.service.cancel_active_step3_oom_job(self._request())

        self.assertEqual(self._scancel_commands(), [])

    def test_changed_current_script_hash_fails_before_scancel(self):
        hashes = dict(self.project.steps[2].input_hashes)
        hashes["submit.sh"] = "0" * 64
        changed_step = replace(
            self.project.steps[2],
            input_hashes=tuple(hashes.items()),
        )
        changed_project = replace(
            self.project,
            revision=self.project.revision + 1,
            steps=(*self.project.steps[:2], changed_step, self.project.steps[3]),
        )
        manifest = self.project.remote_project_path + "/.moltage/project.json"
        self.remote.files[manifest] = serialize_project_manifest(changed_project).encode()

        with self.assertRaisesRegex(RuntimeError, "submit script changed"):
            self.service.cancel_active_step3_oom_job(self._request())

        self.assertEqual(self._scancel_commands(), [])

    def test_repeated_refresh_detects_only_and_never_calls_scancel(self):
        first = self.service.refresh_project(PROFILE, self.project.remote_project_path)
        second = self.service.refresh_project(PROFILE, self.project.remote_project_path)

        self.assertTrue(first.can_kill_step3_oom)
        self.assertTrue(second.can_kill_step3_oom)
        self.assertEqual(self._scancel_commands(), [])

    def test_known_result_survives_executor_close_failure(self):
        remote = CloseFailingCancellationRemote()
        project, files = _project_and_files()
        files["aims.dft.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(remote, project, files)
        remote.squeue_stdout = b"41001|RUNNING\n"
        service = ProjectRecoveryService(
            FixedConnectionService(remote),
            LocalProjectIndexRepository(
                Path(self.temporary.name) / "close-failure-projects.json"
            ),
            now_factory=lambda: NOW,
        )

        result = service.cancel_active_step3_oom_job(
            Step3OomCancellationRequest(PROFILE, project)
        )

        self.assertIs(result.outcome, Step3OomCancellationOutcome.REQUESTED)
        self.assertEqual(
            [item for item in remote.commands if "scancel" in item],
            ["/usr/bin/scancel 41001"],
        )


if __name__ == "__main__":
    unittest.main()
