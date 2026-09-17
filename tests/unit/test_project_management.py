"""Focused PM-R1 safety tests for permanent managed-project deletion."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.density_workflow import DensityTask, DensityWorkflowService
from moltage.app.project_management import (
    PermanentDensityTaskDeletionRequest,
    PermanentProjectDeletionRequest,
    ProjectManagementError,
    ProjectManagementService,
)
from moltage.aims.density_difference import DensitySettings
from moltage.domain.density_difference import DensityGrid
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
    RemoteConnectionError,
    RemotePathNotFoundError,
    RemotePathStat,
)
from moltage.remote.project_delete import (
    RemoteProjectDeletionError,
    RemoteProjectDeletionOutcomeUnknown,
    delete_remote_project_once,
    verify_remote_project_root_for_deletion,
)
from moltage.remote.project_manifest import serialize_project_manifest
from moltage.remote.project_repository import RemoteProjectRepositoryError
from moltage.remote.slurm_status import SchedulerStatusKind
from phase2b1_test_support import example_project, profile
from synthetic_test_data import SYNTHETIC_REMOTE_ROOT
from test_density_difference import partition
from test_density_workflow import MemoryConnection, MemoryRemote


class ScriptedDeleteExecutor:
    """Fake read/execute boundary; it never touches a real remote host."""

    def __init__(self, *, manifest_project=None, density_task=None, outcomes=()) -> None:
        self.manifest_project = manifest_project
        self.density_task = density_task
        self.outcomes = list(outcomes)
        self.commands: list[str] = []
        self.read_paths: list[str] = []
        self.close_calls = 0

    def execute(self, command):
        self.commands.append(command)
        if not self.outcomes:
            raise AssertionError(f"unexpected remote command: {command}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def read_bytes(self, path):
        self.read_paths.append(path)
        if self.manifest_project is not None:
            expected = (
                self.manifest_project.remote_project_path
                + "/.moltage/project.json"
            )
            payload = serialize_project_manifest(self.manifest_project).encode("utf-8")
        elif self.density_task is not None:
            expected = self.density_task.remote_path + "/.moltage/density.json"
            payload = (json.dumps(self.density_task.data) + "\n").encode("utf-8")
        else:
            raise RemotePathNotFoundError("manifest missing")
        if path != expected:
            raise AssertionError(f"unexpected manifest read: {path}")
        return payload

    def stat(self, path):
        if self.manifest_project is not None:
            expected = (
                self.manifest_project.remote_project_path
                + "/.moltage/project.json"
            )
            payload = serialize_project_manifest(self.manifest_project).encode("utf-8")
        elif self.density_task is not None:
            expected = self.density_task.remote_path + "/.moltage/density.json"
            payload = (json.dumps(self.density_task.data) + "\n").encode("utf-8")
        else:
            raise RemotePathNotFoundError("manifest missing")
        if path != expected:
            raise RemotePathNotFoundError(path)
        return RemotePathStat(False, len(payload))

    def close(self):
        self.close_calls += 1


class FakeConnectionService:
    def __init__(self, executor) -> None:
        self.executor = executor
        self.calls = []

    def connect_for_remote_operation(self, selected_profile, supplied_password=None):
        self.calls.append((selected_profile, supplied_password))
        return self.executor


def _result(exit_status=0):
    return RemoteCommandResult(exit_status, b"", b"")


def _profile_for(project):
    return replace(
        profile(profile_id=project.server_profile_id),
        remote_project_root=SYNTHETIC_REMOTE_ROOT,
    )


def _with_step_state(project, state):
    step = replace(
        project.steps[1],
        state=state,
        job_id="44001" if state is not ProjectStepState.NOT_STARTED else None,
        submitted_at=(
            project.created_at
            if state is not ProjectStepState.NOT_STARTED
            else None
        ),
    )
    return replace(project, steps=(project.steps[0], step, *project.steps[2:]))


def _snapshot_for(project, *, scheduler_status_kind=None):
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.MOLECULE_AU_OPT,
        "test project-management snapshot",
        scheduler_status_kind=scheduler_status_kind,
    )


class RemoteProjectDeleteBoundaryTests(unittest.TestCase):
    def test_commands_quote_one_exact_target_and_never_use_wildcards(self) -> None:
        target = "/workspace/Project A.20300830"
        quoted = "'/workspace/Project A.20300830'"
        executor = ScriptedDeleteExecutor(outcomes=(_result(), _result(), _result()))

        verify_remote_project_root_for_deletion(executor, target)
        delete_remote_project_once(executor, target)

        self.assertEqual(
            executor.commands,
            [
                f"test -d {quoted} && test ! -L {quoted}",
                (
                    f"test -d {quoted} && test ! -L {quoted} "
                    f"&& rm -rf -- {quoted}"
                ),
                f"test ! -e {quoted} && test ! -L {quoted}",
            ],
        )
        self.assertNotIn("*", "\n".join(executor.commands))
        self.assertNotIn("/workspace/ProjectB", "\n".join(executor.commands))
        self.assertNotEqual(executor.commands[1], "rm -rf -- /workspace")

    def test_symlink_or_missing_root_is_refused_before_destructive_dispatch(self) -> None:
        executor = ScriptedDeleteExecutor(outcomes=(_result(1),))

        with self.assertRaisesRegex(
            RemoteProjectDeletionError,
            "missing, is not a directory, or is a symlink",
        ):
            verify_remote_project_root_for_deletion(
                executor,
                "/workspace/ProjectA.20300830",
            )

        self.assertEqual(len(executor.commands), 1)
        self.assertNotIn("rm -rf", executor.commands[0])

    def test_known_delete_failure_is_not_retried(self) -> None:
        executor = ScriptedDeleteExecutor(outcomes=(_result(23),))

        with self.assertRaises(RemoteProjectDeletionError):
            delete_remote_project_once(executor, "/workspace/ProjectA.20300830")

        self.assertEqual(len(executor.commands), 1)
        self.assertEqual(executor.commands[0].count("rm -rf"), 1)

    def test_ambiguous_dispatch_is_typed_unknown_and_not_retried(self) -> None:
        executor = ScriptedDeleteExecutor(
            outcomes=(RemoteCommandOutcomeUnknown("lost after dispatch"),)
        )

        with self.assertRaisesRegex(
            RemoteProjectDeletionOutcomeUnknown,
            "Deletion outcome unknown. Reconnect and Refresh.",
        ):
            delete_remote_project_once(executor, "/workspace/ProjectA.20300830")

        self.assertEqual(len(executor.commands), 1)
        self.assertEqual(executor.commands[0].count("rm -rf"), 1)

    def test_postverification_transport_loss_is_unknown_without_second_delete(self) -> None:
        executor = ScriptedDeleteExecutor(
            outcomes=(_result(), RemoteConnectionError("link lost"))
        )

        with self.assertRaises(RemoteProjectDeletionOutcomeUnknown):
            delete_remote_project_once(executor, "/workspace/ProjectA.20300830")

        self.assertEqual(len(executor.commands), 2)
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)
        self.assertTrue(executor.commands[-1].startswith("test ! -e "))

    def test_postverification_known_presence_is_a_definite_failure(self) -> None:
        executor = ScriptedDeleteExecutor(outcomes=(_result(), _result(1)))

        with self.assertRaisesRegex(
            RemoteProjectDeletionError,
            "still exists",
        ):
            delete_remote_project_once(executor, "/workspace/ProjectA.20300830")

        self.assertEqual(len(executor.commands), 2)
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)

    def test_unsafe_lexical_targets_are_rejected_without_remote_calls(self) -> None:
        for target in ("", "/", "relative/project", "/workspace/../project"):
            executor = ScriptedDeleteExecutor()
            with self.subTest(target=target), self.assertRaises(ValueError):
                delete_remote_project_once(executor, target)
            self.assertEqual(executor.commands, [])


class ProjectManagementServiceDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.index = LocalProjectIndexRepository(
            Path(self.temporary_directory.name) / "known_projects.json"
        )
        self.project = example_project()
        self.profile = _profile_for(self.project)

    def _service(self, executor):
        connection = FakeConnectionService(executor)
        return ProjectManagementService(connection, self.index), connection

    def _seed_local_state(self):
        self.index.mark_seen(
            self.project,
            bound_server_profile_id=self.profile.profile_id,
        )
        self.index.recycle(
            self.project,
            bound_server_profile_id=self.profile.profile_id,
            submitted_at=None,
            recycled_at=self.project.created_at,
        )

    def _density_task(self, state="FAILED"):
        remote = MemoryRemote()
        workflow = DensityWorkflowService(
            MemoryConnection(remote),
            Path(self.temporary_directory.name) / "density-cache",
        )
        task = workflow.submit(
            self.profile,
            "Density_Example",
            partition(),
            DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), 0.1),
        )
        if state == task.state:
            return task
        attempt = dict(task.attempt)
        attempt["state"] = state
        attempt["component_states"] = {
            name: state for name in attempt["components"]
        }
        return DensityTask(
            task.remote_path,
            task.data | {"attempts": [attempt]},
        )

    def test_active_and_unknown_projects_are_refused_before_connecting(self) -> None:
        for state in (
            ProjectStepState.QUEUED,
            ProjectStepState.RUNNING,
            ProjectStepState.UNKNOWN,
        ):
            executor = ScriptedDeleteExecutor()
            _service, connection = self._service(executor)
            with self.subTest(state=state), self.assertRaisesRegex(
                ProjectManagementError,
                "active or unresolved work",
            ):
                PermanentProjectDeletionRequest(
                    self.profile,
                    _with_step_state(self.project, state),
                    "temporary-secret",
                )
            self.assertEqual(connection.calls, [])
            self.assertEqual(executor.commands, [])

    def test_local_recycle_refuses_active_and_typed_unresolved_without_mutation(self):
        cases = tuple(
            (
                _with_step_state(self.project, state),
                None,
                state.value,
            )
            for state in (
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
                ProjectStepState.UNKNOWN,
            )
        ) + tuple(
            (
                self.project,
                status_kind,
                status_kind.value,
            )
            for status_kind in (
                SchedulerStatusKind.ACCOUNTING_PENDING,
                SchedulerStatusKind.UNRESOLVED,
            )
        )
        for project, status_kind, label in cases:
            with self.subTest(case=label):
                temporary_directory = tempfile.TemporaryDirectory()
                self.addCleanup(temporary_directory.cleanup)
                index = LocalProjectIndexRepository(
                    Path(temporary_directory.name) / "known_projects.json"
                )
                index.mark_seen(
                    project,
                    bound_server_profile_id=self.profile.profile_id,
                )
                executor = ScriptedDeleteExecutor()
                connection = FakeConnectionService(executor)
                service = ProjectManagementService(connection, index)

                with self.assertRaisesRegex(
                    ProjectManagementError,
                    "active or unresolved work",
                ):
                    service.move_to_recycle(
                        self.profile,
                        _snapshot_for(
                            project,
                            scheduler_status_kind=status_kind,
                        ),
                    )

                self.assertEqual(len(index.load()), 1)
                self.assertEqual(index.load_recycled(), ())
                self.assertEqual(connection.calls, [])
                self.assertEqual(executor.commands, [])

    def test_scheduler_completed_remains_terminal_for_local_recycle(self):
        project = _with_step_state(
            self.project,
            ProjectStepState.SCHEDULER_COMPLETED,
        )
        executor = ScriptedDeleteExecutor()
        service, connection = self._service(executor)

        entry = service.move_to_recycle(
            self.profile,
            _snapshot_for(project),
        )

        self.assertEqual(entry.project_id, project.project_id)
        self.assertEqual(len(self.index.load_recycled()), 1)
        self.assertEqual(connection.calls, [])
        self.assertEqual(executor.commands, [])

    def test_typed_scheduler_uncertainty_refuses_permanent_request(self):
        for status_kind in (
            SchedulerStatusKind.ACCOUNTING_PENDING,
            SchedulerStatusKind.UNRESOLVED,
        ):
            with self.subTest(status=status_kind), self.assertRaisesRegex(
                ProjectManagementError,
                "active or unresolved work",
            ):
                PermanentProjectDeletionRequest(
                    self.profile,
                    self.project,
                    scheduler_status_kind=status_kind,
                )

    def test_root_outside_root_and_non_direct_child_are_rejected_locally(self) -> None:
        unsafe_projects = (
            replace(
                self.project,
                remote_directory_name="work",
                remote_project_path="/remote/work",
            ),
            replace(
                self.project,
                remote_directory_name="ProjectA",
                remote_project_path="/elsewhere/ProjectA",
            ),
            replace(
                self.project,
                remote_directory_name="ProjectA",
                remote_project_path="/remote/work/nested/ProjectA",
            ),
        )
        for unsafe in unsafe_projects:
            with self.subTest(path=unsafe.remote_project_path), self.assertRaisesRegex(
                ProjectManagementError,
                "one exact first-level managed project",
            ):
                PermanentProjectDeletionRequest(self.profile, unsafe)

    def test_symlink_refusal_does_not_read_manifest_or_delete(self) -> None:
        executor = ScriptedDeleteExecutor(
            manifest_project=self.project,
            outcomes=(_result(1),),
        )
        service, _connection = self._service(executor)

        with self.assertRaises(RemoteProjectDeletionError):
            service.permanently_delete(
                PermanentProjectDeletionRequest(
                    self.profile,
                    self.project,
                    "temporary-secret",
                )
            )

        self.assertEqual(executor.read_paths, [])
        self.assertEqual(len(executor.commands), 1)
        self.assertNotIn("rm -rf", executor.commands[0])
        self.assertEqual(executor.close_calls, 1)

    def test_missing_manifest_propagates_and_never_deletes(self) -> None:
        executor = ScriptedDeleteExecutor(outcomes=(_result(),))
        service, _connection = self._service(executor)

        with self.assertRaises(RemotePathNotFoundError):
            service.permanently_delete(
                PermanentProjectDeletionRequest(self.profile, self.project)
            )

        self.assertEqual(len(executor.commands), 1)
        self.assertNotIn("rm -rf", executor.commands[0])
        self.assertEqual(executor.close_calls, 1)

    def test_manifest_uuid_profile_or_revision_change_is_stale_and_never_deleted(self) -> None:
        changed_projects = (
            replace(
                self.project,
                project_id=UUID("33333333-3333-4333-8333-333333333333"),
            ),
            replace(
                self.project,
                server_profile_id=UUID("44444444-4444-4444-8444-444444444444"),
            ),
            replace(
                self.project,
                revision=self.project.revision + 1,
                updated_at=self.project.updated_at + timedelta(minutes=1),
            ),
        )
        for current in changed_projects:
            with self.subTest(current=current):
                executor = ScriptedDeleteExecutor(
                    manifest_project=current,
                    outcomes=(_result(),),
                )
                service, _connection = self._service(executor)
                expected_error = (
                    RemoteProjectRepositoryError
                    if current.server_profile_id != self.profile.profile_id
                    else ProjectManagementError
                )
                with self.assertRaises(expected_error):
                    service.permanently_delete(
                        PermanentProjectDeletionRequest(
                            self.profile,
                            self.project,
                        )
                    )
                self.assertEqual(len(executor.commands), 1)
                self.assertNotIn("rm -rf", executor.commands[0])
                self.assertEqual(executor.close_calls, 1)

    def test_confirmed_success_cleans_known_and_recycle_entries(self) -> None:
        self._seed_local_state()
        executor = ScriptedDeleteExecutor(
            manifest_project=self.project,
            outcomes=(_result(), _result(), _result()),
        )
        service, connection = self._service(executor)

        result = service.permanently_delete(
            PermanentProjectDeletionRequest(
                self.profile,
                self.project,
                "temporary-secret",
            )
        )

        self.assertEqual(result.project_id, self.project.project_id)
        self.assertEqual(result.remote_project_path, self.project.remote_project_path)
        self.assertEqual(
            connection.calls,
            [(self.profile, "temporary-secret")],
        )
        self.assertEqual(
            executor.read_paths,
            [self.project.remote_project_path + "/.moltage/project.json"],
        )
        self.assertEqual(len(executor.commands), 3)
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)
        self.assertNotIn("*", "\n".join(executor.commands))
        self.assertEqual(executor.close_calls, 1)
        self.assertEqual(self.index.load(), ())
        self.assertEqual(self.index.load_recycled(), ())

    def test_definite_delete_failure_preserves_local_state(self) -> None:
        self._seed_local_state()
        executor = ScriptedDeleteExecutor(
            manifest_project=self.project,
            outcomes=(_result(), _result(1)),
        )
        service, _connection = self._service(executor)

        with self.assertRaises(RemoteProjectDeletionError):
            service.permanently_delete(
                PermanentProjectDeletionRequest(self.profile, self.project)
            )

        self.assertEqual(len(executor.commands), 2)
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)
        self.assertEqual(len(self.index.load()), 1)
        self.assertEqual(len(self.index.load_recycled()), 1)

    def test_ambiguous_dispatch_preserves_local_state_without_retry(self) -> None:
        self._seed_local_state()
        executor = ScriptedDeleteExecutor(
            manifest_project=self.project,
            outcomes=(
                _result(),
                RemoteCommandOutcomeUnknown("lost after dispatch"),
            ),
        )
        service, _connection = self._service(executor)

        with self.assertRaisesRegex(
            RemoteProjectDeletionOutcomeUnknown,
            "Deletion outcome unknown. Reconnect and Refresh.",
        ):
            service.permanently_delete(
                PermanentProjectDeletionRequest(self.profile, self.project)
            )

        self.assertEqual(len(executor.commands), 2)
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)
        self.assertEqual(len(self.index.load()), 1)
        self.assertEqual(len(self.index.load_recycled()), 1)
        self.assertEqual(executor.close_calls, 1)

    def test_postverification_transport_loss_preserves_local_state_without_retry(self) -> None:
        self._seed_local_state()
        executor = ScriptedDeleteExecutor(
            manifest_project=self.project,
            outcomes=(
                _result(),
                _result(),
                RemoteConnectionError("link lost after delete"),
            ),
        )
        service, _connection = self._service(executor)

        with self.assertRaises(RemoteProjectDeletionOutcomeUnknown):
            service.permanently_delete(
                PermanentProjectDeletionRequest(self.profile, self.project)
            )

        self.assertEqual(len(executor.commands), 3)
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)
        self.assertEqual(len(self.index.load()), 1)
        self.assertEqual(len(self.index.load_recycled()), 1)
        self.assertEqual(executor.close_calls, 1)

    def test_density_task_active_guard_and_terminal_local_recycle_match_projects(self):
        queued = self._density_task("QUEUED")
        executor = ScriptedDeleteExecutor()
        service, connection = self._service(executor)

        with self.assertRaisesRegex(ProjectManagementError, "active or unresolved"):
            PermanentDensityTaskDeletionRequest(self.profile, queued)
        with self.assertRaisesRegex(ProjectManagementError, "active or unresolved"):
            service.move_density_task_to_recycle(self.profile, queued)
        self.assertEqual(connection.calls, [])
        self.assertEqual(executor.commands, [])

        failed = DensityTask(
            queued.remote_path,
            queued.data
            | {
                "attempts": [
                    queued.attempt
                    | {
                        "state": "FAILED",
                        "component_states": {
                            name: "FAILED" for name in queued.attempt["components"]
                        },
                    }
                ]
            },
        )
        entry = service.move_density_task_to_recycle(self.profile, failed)
        self.assertEqual(entry.project_id, failed.task_id)
        self.assertEqual(entry.remote_project_path, failed.remote_path)
        self.assertEqual(entry.submitted_at, failed.created_at)
        self.assertEqual(self.index.load(), ())
        self.assertEqual(self.index.load_recycled(), (entry,))
        self.assertEqual(connection.calls, [])

    def test_density_permanent_delete_revalidates_exact_manifest_and_forgets_tombstone(self):
        task = self._density_task("FAILED")
        local_service, _connection = self._service(ScriptedDeleteExecutor())
        local_service.move_density_task_to_recycle(self.profile, task)
        executor = ScriptedDeleteExecutor(
            density_task=task,
            outcomes=(_result(), _result(), _result()),
        )
        service, connection = self._service(executor)

        result = service.permanently_delete(
            PermanentDensityTaskDeletionRequest(
                self.profile, task, "temporary-secret"
            )
        )

        self.assertEqual(result.project_id, task.task_id)
        self.assertEqual(result.remote_project_path, task.remote_path)
        self.assertEqual(connection.calls, [(self.profile, "temporary-secret")])
        self.assertEqual(
            executor.read_paths,
            [task.remote_path + "/.moltage/density.json"],
        )
        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 1)
        self.assertEqual(self.index.load_recycled(), ())

    def test_density_manifest_change_blocks_permanent_delete_before_rm(self):
        task = self._density_task("FAILED")
        changed = DensityTask(task.remote_path, task.data | {"name": "Changed"})
        executor = ScriptedDeleteExecutor(
            density_task=changed,
            outcomes=(_result(),),
        )
        service, _connection = self._service(executor)

        with self.assertRaisesRegex(
            ProjectManagementError,
            "density task changed before deletion",
        ):
            service.permanently_delete(
                PermanentDensityTaskDeletionRequest(self.profile, task)
            )

        self.assertEqual(sum("rm -rf" in item for item in executor.commands), 0)
        self.assertEqual(executor.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
