from dataclasses import replace
import unittest

from moltage.app.project_geometry import (
    ProjectGeometryViewError,
    ProjectGeometryViewKind,
    ProjectGeometryViewRequest,
    ProjectGeometryViewService,
    project_geometry_view_kinds,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)
from moltage.remote.project_manifest import serialize_project_manifest
from test_project_recovery import (
    FixedConnectionService,
    RecoveryRemoteExecutor,
    TEST_PROFILE,
    _bundle,
    _direct_step3_project,
    _project,
)


class ProjectGeometryAvailabilityTests(unittest.TestCase):
    def test_step1_and_step2_expose_output_only_after_success(self):
        queued = _project(ProjectStepState.QUEUED)
        self.assertEqual(
            project_geometry_view_kinds(
                queued,
                ProjectStepKind.MOLECULE_OPT,
            ),
            (ProjectGeometryViewKind.INPUT,),
        )
        succeeded = _project(ProjectStepState.SUCCEEDED)
        self.assertEqual(
            project_geometry_view_kinds(
                succeeded,
                ProjectStepKind.MOLECULE_OPT,
            ),
            (
                ProjectGeometryViewKind.INPUT,
                ProjectGeometryViewKind.OUTPUT,
            ),
        )
        self.assertEqual(
            project_geometry_view_kinds(
                succeeded,
                ProjectStepKind.MOLECULE_AU_OPT,
            ),
            (),
        )
        succeeded_step2 = replace(
            succeeded,
            steps=(
                succeeded.steps[0],
                replace(
                    succeeded.steps[1],
                    state=ProjectStepState.SUCCEEDED,
                ),
                *succeeded.steps[2:],
            ),
        )
        self.assertEqual(
            project_geometry_view_kinds(
                succeeded_step2,
                ProjectStepKind.MOLECULE_AU_OPT,
            ),
            (
                ProjectGeometryViewKind.INPUT,
                ProjectGeometryViewKind.OUTPUT,
            ),
        )

    def test_skipped_steps_have_no_geometry_and_step3_step4_share_transport(self):
        project = _direct_step3_project(ProjectStepState.RUNNING)
        self.assertEqual(
            project_geometry_view_kinds(project, ProjectStepKind.MOLECULE_OPT),
            (),
        )
        self.assertEqual(
            project_geometry_view_kinds(project, ProjectStepKind.MOLECULE_AU_OPT),
            (),
        )
        for step_kind in (
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            ProjectStepKind.TRANSMISSION,
        ):
            self.assertEqual(
                project_geometry_view_kinds(project, step_kind),
                (ProjectGeometryViewKind.TRANSPORT,),
            )


class ProjectGeometryViewServiceTests(unittest.TestCase):
    def _service(self, project):
        executor = RecoveryRemoteExecutor()
        executor.add_project(project, _bundle())
        return ProjectGeometryViewService(FixedConnectionService(executor)), executor

    def test_input_reads_only_control_and_geometry_and_never_mutates_remote(self):
        project = _project(ProjectStepState.RUNNING)
        service, executor = self._service(project)
        result = service.load(
            ProjectGeometryViewRequest(
                TEST_PROFILE,
                project,
                ProjectStepKind.MOLECULE_OPT,
                ProjectGeometryViewKind.INPUT,
                supplied_password="temporary",
            )
        )

        self.assertEqual(result.source_filename, "geometry.in")
        self.assertEqual(len(result.structure), 2)
        self.assertEqual(result.workspace_role, "STEP_1_INPUT_GEOMETRY")
        self.assertEqual(
            result.display_title,
            "MoleculeA.20300102 — Step 1 Input Geometry",
        )
        read_paths = [item[1] for item in executor.operations if item[0] == "read"]
        self.assertTrue(read_paths[-2].endswith("/control.in"))
        self.assertTrue(read_paths[-1].endswith("/geometry.in"))
        self.assertFalse(
            any(path.endswith("geometry.in.next_step") for path in read_paths)
        )
        self.assertFalse(
            any(
                item[0] in {"write", "rename", "execute"}
                for item in executor.operations
            )
        )
        self.assertTrue(executor.closed)

    def test_successful_output_uses_next_step_and_validates_chemistry(self):
        project = _project(ProjectStepState.SUCCEEDED)
        service, executor = self._service(project)
        step_directory = remote_step_directory(
            project,
            ProjectStepKind.MOLECULE_OPT,
        )
        executor.files[f"{step_directory}/orbital_HOMO.cube"] = b"cube-data"
        result = service.load(
            ProjectGeometryViewRequest(
                TEST_PROFILE,
                project,
                ProjectStepKind.MOLECULE_OPT,
                ProjectGeometryViewKind.OUTPUT,
            )
        )

        self.assertEqual(result.source_filename, "geometry.in.next_step")
        self.assertEqual(result.workspace_role, "STEP_1_OUTPUT_GEOMETRY")
        self.assertNotEqual(result.structure.atoms[0].x, 0.0)
        self.assertIsNotNone(result.orbital_binding)
        assert result.orbital_binding is not None
        self.assertEqual(result.orbital_binding.profile, TEST_PROFILE)
        self.assertEqual(result.orbital_binding.project.project_id, project.project_id)
        self.assertEqual(result.orbital_binding.expected_structure, result.structure)
        self.assertIn(
            "orbital_HOMO.cube",
            tuple(item.filename for item in result.orbital_binding.artifacts),
        )
        self.assertTrue(
            any(
                item[0] == "read" and item[1].endswith("geometry.in.next_step")
                for item in executor.operations
            )
        )

    def test_input_geometry_does_not_advertise_optimized_orbital_cubes(self):
        project = _project(ProjectStepState.SUCCEEDED)
        service, _executor = self._service(project)
        result = service.load(
            ProjectGeometryViewRequest(
                TEST_PROFILE,
                project,
                ProjectStepKind.MOLECULE_OPT,
                ProjectGeometryViewKind.INPUT,
            )
        )

        self.assertIsNone(result.orbital_binding)

    def test_step3_and_step4_requests_share_exact_transport_workspace_role(self):
        project = _direct_step3_project(ProjectStepState.RUNNING)
        roles = []
        titles = []
        for clicked_step in (
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            ProjectStepKind.TRANSMISSION,
        ):
            service, _executor = self._service(project)
            result = service.load(
                ProjectGeometryViewRequest(
                    TEST_PROFILE,
                    project,
                    clicked_step,
                    ProjectGeometryViewKind.TRANSPORT,
                )
            )
            roles.append(result.workspace_role)
            titles.append(result.display_title)
            self.assertEqual(result.source_filename, "geometry.in")
        self.assertEqual(roles, ["STEP_3_4_TRANSPORT_GEOMETRY"] * 2)
        self.assertEqual(
            titles,
            ["ImportedJunction.20300102 — Step 3/4 Transport Geometry"] * 2,
        )

    def test_missing_requested_geometry_fails_explicitly(self):
        project = _project(ProjectStepState.SUCCEEDED)
        service, executor = self._service(project)
        executor.files.pop(
            f"{project.remote_project_path}/geometry.in.next_step"
        )
        with self.assertRaisesRegex(
            ProjectGeometryViewError,
            "missing required geometry.in.next_step",
        ):
            service.load(
                ProjectGeometryViewRequest(
                    TEST_PROFILE,
                    project,
                    ProjectStepKind.MOLECULE_OPT,
                    ProjectGeometryViewKind.OUTPUT,
                )
            )

    def test_reloaded_manifest_must_still_authorize_output_geometry(self):
        succeeded = _project(ProjectStepState.SUCCEEDED)
        service, executor = self._service(succeeded)
        running = _project(ProjectStepState.RUNNING)
        manifest_path = (
            f"{running.remote_project_path}/.moltage/project.json"
        )
        executor.files[manifest_path] = serialize_project_manifest(running).encode(
            "utf-8"
        )

        with self.assertRaisesRegex(
            ProjectGeometryViewError,
            "no longer available",
        ):
            service.load(
                ProjectGeometryViewRequest(
                    TEST_PROFILE,
                    succeeded,
                    ProjectStepKind.MOLECULE_OPT,
                    ProjectGeometryViewKind.OUTPUT,
                )
            )


if __name__ == "__main__":
    unittest.main()
