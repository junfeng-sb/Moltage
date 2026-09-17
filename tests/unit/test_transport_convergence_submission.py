from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from species_test_support import TEST_SPECIES_ROOT, synthetic_species_library
from moltage.aims.transport_convergence_bundle import (
    TransportConvergenceInputPlan,
    build_transport_convergence_aims_inputs,
)
from moltage.aims.species_library import species_default_filename
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.app.project_submission import (
    ProjectSubmissionService,
    SbatchRejectedError,
    SubmissionOutcomeUnknown,
    TransportConvergenceConflictError,
    TransportConvergenceSubmissionRequest,
)
from moltage.app.transport_convergence import (
    TransportConvergenceContext,
    prepare_transport_convergence_submission_bundle,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from moltage.remote.executor import RemoteCommandOutcomeUnknown, RemoteCommandResult
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from phase2b1_test_support import profile
from synthetic_structure_test_support import (
    synthetic_extended_electrode_placement,
    synthetic_step2_state,
)
from test_project_continuation import NOW, PROFILE_ID, _successful_step1_project
from test_project_submission import (
    FixedConnectionService,
    MemoryRemoteExecutor,
    _temporary_ids,
)


def _successful_step2_project():
    project = _successful_step1_project(revision=4)
    step2 = replace(
        project.steps[1],
        state=ProjectStepState.SUCCEEDED,
        job_id="22222",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=(
            ("geometry.in", "step2-geometry"),
            ("control.in", "step2-control"),
            ("submit.sh", "step2-submit"),
        ),
    )
    return replace(
        project,
        revision=5,
        steps=(project.steps[0], step2, project.steps[2], project.steps[3]),
    )


class TransportConvergenceSubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source, connectivity, _anchors, sites = synthetic_step2_state()
        cls.source = source
        cls.connectivity = connectivity
        cls.applied = synthetic_extended_electrode_placement()

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
        self.project = _successful_step2_project()
        snapshot = ProjectRecoverySnapshot(
            self.project,
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=self.source,
            connectivity=self.connectivity,
        )
        self.context = TransportConvergenceContext(
            snapshot,
            self.source,
            self.applied.structure,
            self.applied,
        )
        self.input_plan = TransportConvergenceInputPlan(
            self.context.working_structure,
            TransportConvergenceSettings(),
        )
        aims_inputs = self.input_plan.materialize(synthetic_species_library())
        self.bundle = prepare_transport_convergence_submission_bundle(
            aims_inputs,
            self.selected_profile.execution_preset,
            self.project.project_id,
        )
        self.request = TransportConvergenceSubmissionRequest(
            self.selected_profile,
            self.context,
            self.input_plan,
            "temporary-secret",
        )
        self.service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=lambda: NOW,
            temporary_id_factory=_temporary_ids(),
        )
        self.historical = self._seed_remote(self.project)

    def _seed_remote(self, project):
        root = project.remote_project_path
        metadata = root + "/.moltage"
        step2_root = root + "/molecule_Au"
        self.executor.directories.update((root, metadata, step2_root))
        historical = {
            root + "/geometry.in": b"STEP1 GEOMETRY\n",
            root + "/control.in": b"STEP1 CONTROL\n",
            root + "/submit.sh": b"STEP1 SUBMIT\n",
            root + "/geometry.in.next_step": b"STEP1 OPTIMIZED\n",
            step2_root + "/geometry.in": b"STEP2 GEOMETRY\n",
            step2_root + "/control.in": b"STEP2 CONTROL\n",
            step2_root + "/submit.sh": b"STEP2 SUBMIT\n",
            step2_root + "/geometry.in.next_step": b"STEP2 OPTIMIZED\n",
        }
        self.executor.files.update(historical)
        self.executor.files[metadata + "/project.json"] = (
            serialize_project_manifest(project).encode("utf-8")
        )
        return historical

    def _dispatches(self):
        return tuple(
            item[1]
            for item in self.executor.operations
            if item[0] == "execute" and item[1].endswith("--parsable submit.sh")
        )

    def test_step3_uses_same_project_uploads_exact_path_and_preserves_history(self):
        result = self.service.continue_project_with_step3(self.request)

        self.assertEqual(result.project.project_id, self.project.project_id)
        self.assertEqual(
            result.project.remote_project_path,
            self.project.remote_project_path,
        )
        self.assertEqual(result.project.steps[:2], self.project.steps[:2])
        self.assertEqual(result.project.steps[3], self.project.steps[3])
        self.assertIs(result.step.kind, ProjectStepKind.TRANSPORT_CONVERGENCE)
        self.assertIs(result.step.state, ProjectStepState.QUEUED)
        self.assertEqual(result.job_id, "12345")
        self.assertEqual(dict(result.step.input_hashes), dict(self.bundle.input_hashes))
        self.assertEqual(
            result.remote_step_directory,
            self.project.remote_project_path + "/molecule_Au/transport",
        )
        for path, data in self.historical.items():
            self.assertEqual(self.executor.files[path], data)
        transport_root = result.remote_step_directory
        self.assertEqual(
            {
                path.removeprefix(transport_root + "/")
                for path in self.executor.files
                if path.startswith(transport_root + "/")
            },
            {"geometry.in", "control.in", "submit.sh"},
        )
        self.assertEqual(
            self.executor.files[transport_root + "/geometry.in"],
            self.bundle.geometry_bytes,
        )
        self.assertIn(
            f"#SBATCH --job-name=AT-{self.project.project_id.hex[:8].upper()}-S3",
            self.executor.files[transport_root + "/submit.sh"].decode("utf-8"),
        )
        uploaded_script = self.executor.files[
            transport_root + "/submit.sh"
        ].decode("utf-8")
        self.assertEqual(uploaded_script.count("--kill-on-bad-exit=1"), 1)
        self.assertEqual(
            uploaded_script.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )
        self.assertEqual(
            self._dispatches(),
            (
                "cd /srv/moltage-test/projects/MoleculeA.20300102/molecule_Au/transport "
                "&& /usr/bin/sbatch --parsable submit.sh",
            ),
        )
        self.assertEqual(self.index.load()[0].project_id, self.project.project_id)
        self.assertEqual(
            result.project.electrode_provenance,
            self.context.electrode_provenance,
        )
        self.assertEqual(
            parse_project_manifest(
                self.executor.files[
                    self.project.remote_project_path
                    + "/.moltage/project.json"
                ]
            ).electrode_provenance,
            self.context.electrode_provenance,
        )

    def test_existing_transport_directory_is_typed_conflict_without_overwrite(self):
        transport_root = self.project.remote_project_path + "/molecule_Au/transport"
        self.executor.directories.add(transport_root)
        sentinel_path = transport_root + "/manual.txt"
        self.executor.files[sentinel_path] = b"preserve"

        with self.assertRaises(TransportConvergenceConflictError):
            self.service.continue_project_with_step3(self.request)

        self.assertEqual(self.executor.files[sentinel_path], b"preserve")
        self.assertEqual(self._dispatches(), ())
        self.assertFalse(
            any(
                operation[0] == "write"
                and operation[1].startswith(transport_root + "/")
                for operation in self.executor.operations
            )
        )

    def test_missing_species_stops_before_transport_directory_creation(self):
        requirement = self.input_plan.species_requirements[0]
        path = (
            f"{TEST_SPECIES_ROOT}/{requirement.accuracy.value}/"
            f"{species_default_filename(requirement.element, requirement.accuracy)}"
        )
        self.executor.missing_species_paths.add(path)
        transport_root = self.project.remote_project_path + "/molecule_Au/transport"

        with self.assertRaisesRegex(
            RuntimeError,
            f"element={requirement.element}, accuracy={requirement.accuracy.value}",
        ):
            self.service.continue_project_with_step3(self.request)

        self.assertNotIn(transport_root, self.executor.directories)
        self.assertEqual(self._dispatches(), ())
        self.assertFalse(
            any(
                operation[0] in {"mkdir", "write", "rename"}
                and len(operation) > 1
                and operation[1].startswith(transport_root)
                for operation in self.executor.operations
            )
        )

    def test_post_dispatch_unknown_is_recorded_once_without_retry(self):
        self.executor.execute_error = RemoteCommandOutcomeUnknown("lost")

        with self.assertRaises(SubmissionOutcomeUnknown):
            self.service.continue_project_with_step3(self.request)

        self.assertEqual(len(self._dispatches()), 1)
        manifest = parse_project_manifest(
            self.executor.files[
                self.project.remote_project_path
                + "/.moltage/project.json"
            ]
        )
        self.assertEqual(manifest.steps[:2], self.project.steps[:2])
        self.assertIs(manifest.steps[2].state, ProjectStepState.UNKNOWN)
        self.assertTrue(manifest.steps[2].input_hashes)
        self.assertEqual(manifest.steps[3], self.project.steps[3])
        self.assertEqual(
            manifest.electrode_provenance,
            self.context.electrode_provenance,
        )

    def test_rejected_step3_keeps_authoritative_electrode_provenance(self):
        self.executor.command_result = RemoteCommandResult(
            1,
            b"",
            b"synthetic scheduler rejection\n",
        )

        with self.assertRaises(SbatchRejectedError):
            self.service.continue_project_with_step3(self.request)

        manifest = parse_project_manifest(
            self.executor.files[
                self.project.remote_project_path + "/.moltage/project.json"
            ]
        )
        self.assertIs(manifest.steps[2].state, ProjectStepState.FAILED)
        self.assertEqual(
            manifest.electrode_provenance,
            self.context.electrode_provenance,
        )

    def test_stale_project_revision_stops_before_transport_creation(self):
        authoritative = replace(self.project, revision=self.project.revision + 1)
        manifest_path = (
            self.project.remote_project_path + "/.moltage/project.json"
        )
        self.executor.files[manifest_path] = serialize_project_manifest(
            authoritative
        ).encode("utf-8")

        with self.assertRaisesRegex(
            TransportConvergenceConflictError,
            "revision changed",
        ):
            self.service.continue_project_with_step3(self.request)

        self.assertNotIn(
            self.project.remote_project_path + "/molecule_Au/transport",
            self.executor.directories,
        )
        self.assertEqual(self._dispatches(), ())


if __name__ == "__main__":
    unittest.main()
