from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QTableWidget

from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.gui.project_submission import NewProjectSelection
from moltage.gui.workspace_tabs import (
    LocalGeometryWorkspaceIdentity,
    TransmissionWorkspaceRequest,
    WorkspaceKind,
)
from moltage.visualization.bond_torsion import structure_coordinates
from phase2b1_test_support import profile
from test_project_recovery import TEST_PROFILE
from test_projects_dialog import _snapshot, _step4_success_snapshot
from tools.molecule_viewer_demo import MoleculeViewerDemo


class UiR4WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.path_a = root / "A.xyz"
        self.path_b = root / "B.xyz"
        self.same_name_a = root / "one" / "molecule.xyz"
        self.same_name_b = root / "two" / "molecule.xyz"
        self.same_name_a.parent.mkdir()
        self.same_name_b.parent.mkdir()
        self.path_a.write_text(_torsion_xyz(0.0, "A"), encoding="utf-8")
        self.path_b.write_text(_torsion_xyz(8.0, "B"), encoding="utf-8")
        self.same_name_a.write_text(_torsion_xyz(0.0, "same A"), encoding="utf-8")
        self.same_name_b.write_text(_torsion_xyz(8.0, "same B"), encoding="utf-8")
        self.window = MoleculeViewerDemo()
        self.window.resize(900, 650)
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def test_two_live_geometries_preserve_coordinates_without_source_reread(self):
        workspace_a = self.window._open_local_geometry(self.path_a)
        workspace_b = self.window._open_local_geometry(self.path_b)
        self.assertEqual(self.window._workspace_tabs.count(), 2)
        original_a = structure_coordinates(workspace_a.structure)
        original_b = structure_coordinates(workspace_b.structure)

        self.window._focus_workspace(workspace_a)
        self._rotate_active_geometry(35.0)
        changed_a = structure_coordinates(self.window._structure)
        self.assertNotEqual(changed_a, original_a)

        with patch(
            "tools.molecule_viewer_demo.load_geometry",
            side_effect=AssertionError("tab switching must not reread geometry"),
        ):
            self.window._focus_workspace(workspace_b)
            self.assertEqual(
                structure_coordinates(self.window._structure),
                original_b,
            )
            self.window._focus_workspace(workspace_a)
            self.assertEqual(
                structure_coordinates(self.window._structure),
                changed_a,
            )

    def test_torsion_undo_redo_history_is_workspace_local(self):
        workspace_a = self.window._open_local_geometry(self.path_a)
        workspace_b = self.window._open_local_geometry(self.path_b)
        original_a = structure_coordinates(workspace_a.structure)
        original_b = structure_coordinates(workspace_b.structure)

        self.window._focus_workspace(workspace_a)
        self._rotate_active_geometry(25.0)
        changed_a = structure_coordinates(self.window._structure)
        self.assertTrue(self.window._geometry_undo_action.isEnabled())

        self.window._focus_workspace(workspace_b)
        self.assertFalse(self.window._geometry_undo_action.isEnabled())
        self.assertFalse(self.window._geometry_redo_action.isEnabled())
        self.window._focus_workspace(workspace_a)
        self.assertTrue(self.window._geometry_undo_action.isEnabled())
        self.window._geometry_undo_action.trigger()
        self.assertEqual(structure_coordinates(self.window._structure), original_a)
        self.assertEqual(structure_coordinates(workspace_b.structure), original_b)
        self.assertTrue(self.window._geometry_redo_action.isEnabled())
        self.window._geometry_redo_action.trigger()
        self.assertEqual(structure_coordinates(self.window._structure), changed_a)
        self.assertEqual(structure_coordinates(workspace_b.structure), original_b)

    def test_measurements_are_local_and_absent_from_transmission(self):
        workspace_a = self.window._open_local_geometry(self.path_a)
        workspace_b = self.window._open_local_geometry(self.path_b)
        self.window._focus_workspace(workspace_a)
        self.window._distance_measure_action.setChecked(True)
        self.window._report_picked_atom(0)
        self.window._report_picked_atom(3)
        self.assertEqual(self.window._measurement_table.rowCount(), 1)

        self.window._focus_workspace(workspace_b)
        self.assertEqual(self.window._measurement_table.rowCount(), 0)
        transmission = self._open_transmission()
        self.assertIs(self.window._active_workspace(), transmission)
        self.assertIsNone(
            transmission.content.findChild(QTableWidget, "measurementTable")
        )
        self.window._focus_workspace(workspace_a)
        self.assertEqual(self.window._measurement_table.rowCount(), 1)

    def test_electrode_builder_proposal_and_content_are_workspace_local(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "phase1b"
            / "synthetic_dual_ncs.xyz"
        )
        other = Path(self.temporary_directory.name) / "other.xyz"
        other.write_bytes(fixture.read_bytes())
        workspace_a = self.window._open_local_geometry(fixture)
        workspace_b = self.window._open_local_geometry(other)

        self.window._focus_workspace(workspace_a)
        first = tuple(self.window._site_controls.values())[0]
        first.checkbox.setChecked(True)
        self.application.processEvents()
        proposal = self.window._current_proposals
        self.assertEqual(len(proposal), 1)
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            1,
        )
        builder_a = self.window._electrode_builder_stack.currentWidget()

        self.window._focus_workspace(workspace_b)
        self.assertEqual(self.window._current_proposals, ())
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertIsNot(
            self.window._electrode_builder_stack.currentWidget(),
            builder_a,
        )
        self.window._focus_workspace(workspace_a)
        self.assertEqual(self.window._current_proposals, proposal)
        self.assertIs(
            self.window._electrode_builder_stack.currentWidget(),
            builder_a,
        )

    def test_canonical_actions_route_only_to_active_workspace(self):
        workspace_a = self.window._open_local_geometry(self.path_a)
        workspace_b = self.window._open_local_geometry(self.path_b)
        with patch.object(workspace_a.viewer, "reset_camera") as reset_a, patch.object(
            workspace_b.viewer,
            "reset_camera",
        ) as reset_b:
            self.window._focus_workspace(workspace_a)
            self.window._reset_view_action.trigger()
            reset_a.assert_called_once_with()
            reset_b.assert_not_called()
            self.window._focus_workspace(workspace_b)
            self.window._reset_view_action.trigger()
            reset_b.assert_called_once_with()
            reset_a.assert_called_once_with()

        self.window._distance_measure_action.setChecked(True)
        self.assertEqual(self.window._pick_mode.value, "DISTANCE")
        self.assertEqual(workspace_a.picked_atom_label.text(), "Selected atom: none")
        workspace_a.viewer.atom_picked.emit(0)
        self.application.processEvents()
        self.assertEqual(workspace_a.picked_atom_label.text(), "Selected atom: none")
        self.window._focus_workspace(workspace_a)
        self.assertFalse(self.window._distance_measure_action.isChecked())
        self.window._focus_workspace(workspace_b)
        self.assertTrue(self.window._distance_measure_action.isChecked())

        self.window._element_labels_action.setChecked(True)
        transmission = self._open_transmission()
        self.assertEqual(transmission.kind, WorkspaceKind.TRANSMISSION)
        for action in (
            self.window._geometry_undo_action,
            self.window._geometry_redo_action,
            self.window._element_labels_action,
            self.window._distance_measure_action,
            self.window._angle_measure_action,
            self.window._rotate_bond_action,
            self.window._electrode_builder_action,
            self.window._submit_aims_action,
            self.window._continue_step2_action,
            self.window._continue_step3_action,
            self.window._continue_step4_action,
        ):
            self.assertFalse(action.isEnabled())
        self.assertTrue(self.window._element_labels_action.isChecked())
        self.assertTrue(self.window._reset_view_action.isEnabled())
        self.assertTrue(self.window._au_tool_dock.isHidden())
        self.window._focus_workspace(workspace_b)
        self.assertTrue(self.window._element_labels_action.isEnabled())
        self.assertTrue(self.window._element_labels_action.isChecked())

    def test_step1_action_uses_exact_active_geometry_at_fake_boundary(self):
        workspace_a = self.window._open_local_geometry(self.path_a)
        workspace_b = self.window._open_local_geometry(self.path_b)
        selected_profile = profile()
        selection = NewProjectSelection(
            selected_profile,
            "routing_test",
            ProjectStepKind.MOLECULE_OPT,
            "routing_test_20300830",
        )
        repository = MagicMock()
        repository.load.return_value = SimpleNamespace(
            profiles=(selected_profile,),
            last_selected_profile_id=selected_profile.profile_id,
        )
        dependencies = SimpleNamespace(
            profile_repository=repository,
            secret_store=MagicMock(get_password=MagicMock(return_value=None)),
        )
        project_dialog = MagicMock()
        project_dialog.exec.return_value = QDialog.DialogCode.Accepted
        project_dialog.selected_project.return_value = selection
        settings_dialog = MagicMock()
        settings_dialog.exec.return_value = QDialog.DialogCode.Accepted
        settings_dialog.selected_settings.return_value = AimsOptimizationSettings()
        confirmation = MagicMock()
        confirmation.exec.return_value = QDialog.DialogCode.Accepted
        confirmation.take_temporary_password.return_value = "temporary"

        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.NewCalculationProjectDialog",
            return_value=project_dialog,
        ), patch(
            "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
            return_value=confirmation,
        ), patch.object(
            self.window,
            "_start_submission",
        ) as start_submission:
            self.window._focus_workspace(workspace_a)
            self.window._submit_aims_action.trigger()
            self.window._focus_workspace(workspace_b)
            self.window._submit_aims_action.trigger()

        structures = tuple(
            call.args[1].input_plan.structure
            for call in start_submission.call_args_list
        )
        self.assertEqual(structures, (workspace_a.structure, workspace_b.structure))
        self.assertEqual(start_submission.call_count, 2)

    def test_local_and_managed_geometry_identity_deduplication(self):
        workspace = self.window._open_local_geometry(self.same_name_a)
        duplicate = self.window._open_local_geometry(
            self.same_name_a.parent / "." / self.same_name_a.name
        )
        self.assertIs(duplicate, workspace)
        self.assertEqual(self.window._workspace_tabs.count(), 1)
        second = self.window._open_local_geometry(self.same_name_b)
        self.assertIsNot(second, workspace)
        self.assertEqual(self.window._workspace_tabs.count(), 2)
        self.assertEqual(workspace.display_title, second.display_title)
        self.assertNotEqual(workspace.identity, second.identity)
        self.assertIsInstance(workspace.identity, LocalGeometryWorkspaceIdentity)

        managed_one = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        managed_two = replace(
            managed_one,
            project=replace(managed_one.project, project_id=uuid4()),
        )
        first_managed = self.window._open_recovered_geometry(
            managed_one,
            TEST_PROFILE,
        )
        second_managed = self.window._open_recovered_geometry(
            managed_two,
            TEST_PROFILE,
        )
        self.assertIsNot(first_managed, second_managed)
        self.assertEqual(first_managed.display_title, second_managed.display_title)
        self.assertNotEqual(first_managed.identity, second_managed.identity)

    def test_transmission_identity_axis_isolation_dedup_and_close(self):
        snapshot = _step4_success_snapshot()
        request = TransmissionWorkspaceRequest.from_snapshot(snapshot)
        first = self.window._open_transmission_workspace(request)
        duplicate = self.window._open_transmission_workspace(
            TransmissionWorkspaceRequest.from_snapshot(snapshot)
        )
        self.assertIs(duplicate, first)
        self.assertEqual(self._transmission_count(), 1)

        changed_step = replace(
            snapshot.active_step,
            job_id="different-job",
            submit_script_filename="submit.aitranss.retry99.sh",
            slurm_output_filename="aitranss.retry99.out",
        )
        changed_project = replace(
            snapshot.project,
            steps=(*snapshot.project.steps[:3], changed_step),
        )
        changed_snapshot = replace(snapshot, project=changed_project)
        second = self.window._open_transmission_workspace(
            TransmissionWorkspaceRequest.from_snapshot(changed_snapshot)
        )
        self.assertIsNot(second, first)
        self.assertEqual(self._transmission_count(), 2)

        first.content._energy_axis.setRange(-1.0, 1.0)
        self.window._focus_workspace(second)
        self.assertEqual(second.content._energy_axis.min(), -2.0)
        self.assertEqual(second.content._energy_axis.max(), 2.0)
        self.window._focus_workspace(first)
        self.assertEqual(first.content._energy_axis.min(), -1.0)
        self.assertEqual(first.content._energy_axis.max(), 1.0)
        self.window._reset_view_action.trigger()
        self.assertEqual(first.content._energy_axis.min(), -2.0)
        self.assertEqual(first.content._energy_axis.max(), 2.0)

        first_index = self.window._workspace_tabs.indexOf(first.content)
        self.window._close_workspace_tab(first_index)
        self.assertEqual(self._transmission_count(), 1)
        self.assertIn(second.content, self.window._workspaces_by_widget)

    def test_transmission_routes_view_settings_and_current_view_export(self):
        transmission = self._open_transmission()

        self.assertIs(
            self.window._active_export_target(),
            transmission.content,
        )
        self.assertTrue(self.window._export_current_view_action.isEnabled())
        with patch.object(
            transmission.content,
            "open_view_settings",
        ) as open_settings:
            self.window._view_settings_action.trigger()

        open_settings.assert_called_once_with()
        before_geometry = self.window.geometry()
        settings = transmission.content.visual_settings
        transmission.content._apply_visual_settings(
            replace(
                settings,
                canvas=replace(
                    settings.canvas,
                    export_width=1200,
                    export_height=800,
                ),
            )
        )
        image = transmission.content.capture_image(1)
        self.application.processEvents()
        self.assertEqual((image.width(), image.height()), (1200, 800))
        self.assertEqual(self.window.geometry(), before_geometry)

    def test_closing_tabs_preserves_neighbors_and_main_window(self):
        workspace_a = self.window._open_local_geometry(self.path_a)
        workspace_b = self.window._open_local_geometry(self.path_b)
        original_b = structure_coordinates(workspace_b.structure)
        transmission = self._open_transmission()

        index_a = self.window._workspace_tabs.indexOf(workspace_a.content)
        self.window._close_workspace_tab(index_a)
        self.assertEqual(structure_coordinates(workspace_b.structure), original_b)
        self.assertNotIn(workspace_a.content, self.window._workspaces_by_widget)

        index_transmission = self.window._workspace_tabs.indexOf(
            transmission.content
        )
        self.window._close_workspace_tab(index_transmission)
        self.assertIs(self.window._active_workspace(), workspace_b)
        self.assertTrue(self.window._distance_measure_action.isEnabled())

        index_b = self.window._workspace_tabs.indexOf(workspace_b.content)
        self.window._close_workspace_tab(index_b)
        self.assertEqual(self.window._workspace_tabs.count(), 0)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window._reset_view_action.isEnabled())

    def test_tab_close_does_not_cancel_or_remove_remote_worker(self):
        workspace = self.window._open_local_geometry(self.path_a)
        worker = MagicMock()
        self.window._submission_running = True
        self.window._submission_workers.add(worker)
        self.window._submission_origin_workspace_id = workspace.runtime_id

        index = self.window._workspace_tabs.indexOf(workspace.content)
        self.window._close_workspace_tab(index)

        self.assertTrue(self.window._submission_running)
        self.assertIn(worker, self.window._submission_workers)
        self.assertIn(
            workspace.runtime_id,
            self.window._retired_geometry_workspaces,
        )
        worker.cancel.assert_not_called()

        self.window._submission_workers.clear()
        self.window._submission_running = False
        self.window._submission_origin_workspace_id = None
        self.window._release_retired_workspace(workspace.runtime_id)

    def _rotate_active_geometry(self, angle: float) -> None:
        self.window._rotate_bond_action.setChecked(True)
        self.window._bond_rotation_edge_picked(1, 2)
        self.assertIsNotNone(self.window._torsion_session)
        self.window._start_torsion_numeric_edit()
        self.window._torsion_numeric_input.setText(str(angle))
        self.window._commit_torsion_numeric_edit()
        self.application.processEvents()

    def _open_transmission(self):
        return self.window._open_transmission_workspace(
            TransmissionWorkspaceRequest.from_snapshot(
                _step4_success_snapshot()
            )
        )

    def _transmission_count(self) -> int:
        return sum(
            workspace.kind is WorkspaceKind.TRANSMISSION
            for workspace in self.window._workspaces_by_widget.values()
        )


def _torsion_xyz(offset: float, comment: str) -> str:
    return (
        "4\n"
        f"{comment}\n"
        f"C {offset:.1f} 1.0 0.0\n"
        f"C {offset:.1f} 0.0 0.0\n"
        f"C {offset + 1.4:.1f} 0.0 0.0\n"
        f"H {offset + 1.4:.1f} 1.0 0.0\n"
    )


if __name__ == "__main__":
    unittest.main()
