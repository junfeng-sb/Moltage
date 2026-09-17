from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_planning import create_initial_project
from moltage.app.project_submission import (
    ExistingProjectStepSubmissionRequest,
    ProjectContinuationConflictError,
    ProjectSubmissionService,
    SubmissionOutcomeUnknown,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.structure import Atom, MolecularStructure
from moltage.remote.executor import RemoteCommandOutcomeUnknown
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from moltage.remote.slurm import render_submit_script
from phase2b1_test_support import profile
from test_project_submission import (
    FixedConnectionService,
    MemoryRemoteExecutor,
    _temporary_ids,
)


NOW = datetime(2030, 8, 27, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("77777777-7777-4777-8777-777777777777")
PROFILE_ID = UUID("88888888-8888-4888-8888-888888888888")


def _successful_step1_project(*, profile_id=PROFILE_ID, revision=3):
    project = create_initial_project(
        base_name="MoleculeA",
        remote_directory_name="MoleculeA.20300102",
        source_molecule_name="MoleculeA.xyz",
        server_profile_id=profile_id,
        remote_project_root="/srv/moltage-test/projects",
        starting_step=ProjectStepKind.MOLECULE_OPT,
        now=NOW,
        project_id=PROJECT_ID,
    )
    step1 = project.steps[0]
    succeeded = replace(
        step1,
        state=ProjectStepState.SUCCEEDED,
        job_id="11111",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=(
            ("geometry.in", "step1-geometry"),
            ("control.in", "step1-control"),
            ("submit.sh", "step1-submit"),
        ),
    )
    return replace(
        project,
        revision=revision,
        steps=(succeeded, *project.steps[1:]),
    )


def _step2_plan():
    structure = MolecularStructure(
        (
            Atom(0, "C", 0.0, 0.0, 0.0),
            Atom(1, "Au", 2.0, 0.0, 0.0),
        )
    )
    return AimsOptimizationInputPlan(
        structure,
        AimsOptimizationSettings(),
    )


def _seed_remote(executor, project, selected_profile):
    root = project.remote_project_path
    metadata = root + "/.moltage"
    executor.directories.update((root, metadata))
    historical = {
        root + "/geometry.in": b"STEP1 GEOMETRY\n",
        root + "/control.in": b"STEP1 CONTROL\n",
        root + "/submit.sh": render_submit_script(
            selected_profile.execution_preset,
            project.project_id,
            ProjectStepKind.MOLECULE_OPT,
        ).encode("utf-8"),
        root + "/geometry.in.next_step": b"STEP1 OPTIMIZED GEOMETRY\n",
    }
    executor.files.update(historical)
    executor.files[metadata + "/project.json"] = serialize_project_manifest(
        project
    ).encode("utf-8")
    return historical


class ProjectContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.index = LocalProjectIndexRepository(
            Path(self.temporary.name) / "known_projects.json"
        )
        self.executor = MemoryRemoteExecutor()
        self.selected_profile = profile(
            profile_id=PROFILE_ID,
            save_password=False,
        )
        self.service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=lambda: NOW,
            temporary_id_factory=_temporary_ids(),
        )

    def test_step1_to_step2_reuses_same_project_and_preserves_history(self):
        project = _successful_step1_project()
        historical = _seed_remote(self.executor, project, self.selected_profile)
        request = ExistingProjectStepSubmissionRequest(
            self.selected_profile,
            project,
            _step2_plan(),
            "temporary-secret",
        )

        result = self.service.continue_project_with_step2(request)

        self.assertEqual(result.project.project_id, project.project_id)
        self.assertEqual(result.project.remote_project_path, project.remote_project_path)
        self.assertIs(result.project.steps[0].state, ProjectStepState.SUCCEEDED)
        self.assertIs(result.project.steps[1].state, ProjectStepState.QUEUED)
        self.assertEqual(result.project.steps[1].job_id, "12345")
        self.assertTrue(result.project.steps[1].input_hashes)
        self.assertIs(result.project.steps[2].state, ProjectStepState.NOT_STARTED)
        self.assertIs(result.project.steps[3].state, ProjectStepState.NOT_STARTED)
        self.assertIn(project.remote_project_path + "/molecule_Au", self.executor.directories)
        self.assertNotIn(
            project.remote_project_path + "/molecule_Au/transport",
            self.executor.directories,
        )
        for path, data in historical.items():
            self.assertEqual(self.executor.files[path], data)
        step2_root = project.remote_project_path + "/molecule_Au"
        self.assertEqual(
            {
                path.removeprefix(step2_root + "/")
                for path in self.executor.files
                if path.startswith(step2_root + "/")
            },
            {"geometry.in", "control.in", "submit.sh"},
        )
        dispatches = [
            item[1]
            for item in self.executor.operations
            if len(item) >= 2
            and item[0] == "execute"
            and item[1].endswith("--parsable submit.sh")
        ]
        self.assertEqual(
            dispatches,
            [
                "cd /srv/moltage-test/projects/MoleculeA.20300102/molecule_Au "
                "&& /usr/bin/sbatch --parsable submit.sh"
            ],
        )
        step2_script = self.executor.files[step2_root + "/submit.sh"].decode("utf-8")
        self.assertEqual(step2_script.count("--kill-on-bad-exit=1"), 1)
        self.assertEqual(
            step2_script.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )

    def test_step2_uses_current_profile_mail_settings_once(self):
        project = _successful_step1_project()
        enabled_profile = replace(
            self.selected_profile,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        historical = _seed_remote(self.executor, project, self.selected_profile)

        self.service.continue_project_with_step2(
            ExistingProjectStepSubmissionRequest(
                enabled_profile,
                project,
                _step2_plan(),
                "temporary-secret",
            )
        )

        for path, data in historical.items():
            self.assertEqual(self.executor.files[path], data)
        script = self.executor.files[
            project.remote_project_path + "/molecule_Au/submit.sh"
        ].decode("utf-8")
        self.assertEqual(script.count("#SBATCH --mail-user=user@example.com"), 1)
        self.assertEqual(script.count("#SBATCH --mail-type=END,FAIL"), 1)
        self.assertNotIn("TIME_LIMIT", script)

    def test_preexisting_molecule_au_stops_before_upload_without_overwrite(self):
        project = _successful_step1_project()
        historical = _seed_remote(self.executor, project, self.selected_profile)
        step2_root = project.remote_project_path + "/molecule_Au"
        self.executor.directories.add(step2_root)
        self.executor.files[step2_root + "/manual.txt"] = b"preserve"

        with self.assertRaises(ProjectContinuationConflictError):
            self.service.continue_project_with_step2(
                ExistingProjectStepSubmissionRequest(
                    self.selected_profile,
                    project,
                    _step2_plan(),
                    "temporary-secret",
                )
            )

        self.assertEqual(self.executor.files[step2_root + "/manual.txt"], b"preserve")
        for path, data in historical.items():
            self.assertEqual(self.executor.files[path], data)
        self.assertFalse(
            any(
                item[0] == "write" and item[1].startswith(step2_root + "/")
                for item in self.executor.operations
            )
        )

    def test_stale_recovered_revision_requires_refresh_before_directory_creation(self):
        recovered = _successful_step1_project(revision=3)
        authoritative = replace(recovered, revision=4, updated_at=NOW)
        _seed_remote(self.executor, authoritative, self.selected_profile)

        with self.assertRaisesRegex(
            ProjectContinuationConflictError,
            "revision changed",
        ):
            self.service.continue_project_with_step2(
                ExistingProjectStepSubmissionRequest(
                    self.selected_profile,
                    recovered,
                    _step2_plan(),
                    "temporary-secret",
                )
            )

        self.assertNotIn(
            recovered.remote_project_path + "/molecule_Au",
            self.executor.directories,
        )

    def test_ambiguous_post_dispatch_loss_is_unknown_and_never_retried(self):
        project = _successful_step1_project()
        _seed_remote(self.executor, project, self.selected_profile)
        self.executor.execute_error = RemoteCommandOutcomeUnknown("lost")

        with self.assertRaises(SubmissionOutcomeUnknown):
            self.service.continue_project_with_step2(
                ExistingProjectStepSubmissionRequest(
                    self.selected_profile,
                    project,
                    _step2_plan(),
                    "temporary-secret",
                )
            )

        dispatches = [
            item
            for item in self.executor.operations
            if item[0] == "execute" and item[1].endswith("--parsable submit.sh")
        ]
        self.assertEqual(len(dispatches), 1)
        manifest = parse_project_manifest(
            self.executor.files[
                project.remote_project_path + "/.moltage/project.json"
            ]
        )
        self.assertIs(manifest.steps[1].state, ProjectStepState.UNKNOWN)
        self.assertIs(manifest.steps[0].state, ProjectStepState.SUCCEEDED)

    def test_local_rebind_remains_current_after_step2_submission(self):
        historical_id = UUID("99999999-9999-4999-8999-999999999999")
        project = _successful_step1_project(profile_id=historical_id)
        _seed_remote(self.executor, project, self.selected_profile)

        result = self.service.continue_project_with_step2(
            ExistingProjectStepSubmissionRequest(
                self.selected_profile,
                project,
                _step2_plan(),
                "temporary-secret",
            )
        )

        self.assertEqual(result.project.server_profile_id, historical_id)
        self.assertEqual(
            self.index.load()[0].server_profile_id,
            self.selected_profile.profile_id,
        )

    def test_invalid_continuation_state_fails_before_connection(self):
        project = _successful_step1_project()
        queued_step2 = replace(
            project.steps[1],
            state=ProjectStepState.QUEUED,
            job_id="22222",
            submitted_at=NOW,
        )
        invalid = replace(
            project,
            steps=(project.steps[0], queued_step2, *project.steps[2:]),
        )

        with self.assertRaises(ProjectContinuationConflictError):
            ExistingProjectStepSubmissionRequest(
                self.selected_profile,
                invalid,
                _step2_plan(),
            )
        self.assertEqual(self.executor.operations, [])


if __name__ == "__main__":
    unittest.main()
