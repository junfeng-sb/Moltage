import gc
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from moltage.aims.geometry_writer import render_geometry_in
from moltage.visualization.measurements import (
    AngleMeasurement,
    DistanceMeasurement,
)
from moltage.visualization.molecule_scene import (
    MEASUREMENT_HIGHLIGHT_COLOR_RGB,
    PRIMARY_HIGHLIGHT_COLOR_RGB,
    SECONDARY_HIGHLIGHT_COLOR_RGB,
    _dash_segments,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.molecule_viewer_demo import MoleculeViewerDemo, _ViewerPickMode


REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


class ManualMeasurementViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.temporary = tempfile.TemporaryDirectory()
        cls.source_path = Path(cls.temporary.name) / "measurement.xyz"
        cls.source_path.write_text(
            "6\n"
            "manual measurement fixture\n"
            "C 0.0 0.0 0.0\n"
            "N 1.0 2.0 2.0\n"
            "O 1.0 0.0 0.0\n"
            "H 0.0 1.0 0.0\n"
            "S 2.0 0.0 0.0\n"
            "Cl 0.0 0.0 1.0\n",
            encoding="utf-8",
        )
        cls.window = MoleculeViewerDemo()
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()
        cls.window = None
        cls.temporary.cleanup()
        gc.collect()

    def setUp(self):
        self.window._load(self.source_path)
        self.application.processEvents()
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.NORMAL)
        self.assertEqual(self.window._measurement_table.rowCount(), 0)

    def _pick(self, *atom_indices):
        for atom_index in atom_indices:
            self.window._report_picked_atom(atom_index)
            self.application.processEvents()

    def _measure_distance(self, atom_a=0, atom_b=1):
        if not self.window._distance_measure_button.isChecked():
            self.window._distance_measure_button.click()
        self._pick(atom_a, atom_b)

    def _measure_angle(self, atom_a=2, atom_b=0, atom_c=3):
        if not self.window._angle_measure_button.isChecked():
            self.window._angle_measure_button.click()
        self._pick(atom_a, atom_b, atom_c)

    def test_measurement_panel_appears_only_after_a_completed_result(self):
        panel = self.window._measurement_panel
        self.assertFalse(panel.isVisibleTo(self.window))

        self.window._distance_measure_button.click()
        self.application.processEvents()
        self.assertFalse(panel.isVisibleTo(self.window))
        self.assertFalse(
            self.window._measurement_empty_state.isVisibleTo(self.window)
        )

        self.window._distance_measure_button.click()
        self.application.processEvents()
        self.assertFalse(panel.isVisibleTo(self.window))

        self._measure_distance()
        self.assertTrue(panel.isVisibleTo(self.window))
        self.assertTrue(self.window._measurement_table.isVisibleTo(self.window))

        self.window._distance_measure_button.click()
        self.application.processEvents()
        self.assertTrue(panel.isVisibleTo(self.window))

        self.window._measurement_session.clear()
        self.window._sync_measurement_view()
        self.application.processEvents()
        self.assertFalse(panel.isVisibleTo(self.window))

    def test_distance_tool_nonbonded_measurement_and_stable_id_binding(self):
        structure = self.window._structure
        connectivity = self.window._connectivity
        original_bonds = connectivity.bonds
        self.assertFalse(
            any(
                (bond.first_index, bond.second_index) == (0, 1)
                for bond in connectivity
            )
        )
        self.assertFalse(self.window._distance_measure_button.icon().isNull())
        self.assertEqual(
            self.window._distance_measure_button.toolTip(),
            "Measure Distance",
        )
        self.assertEqual(
            self.window._distance_measure_button.accessibleName(),
            "Measure Distance",
        )

        self._measure_distance()

        measurement = self.window._measurement_session.measurements[0]
        self.assertIsInstance(measurement, DistanceMeasurement)
        self.assertEqual(measurement.value_angstrom, 3.0)
        self.assertTrue(self.window._distance_measure_button.isChecked())
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.DISTANCE)
        self.assertEqual(self.window._measurement_table.rowCount(), 1)
        self.assertEqual(
            self.window._measurement_table.item(0, 1).text(),
            "C0 – N1",
        )
        self.assertEqual(self.window._measurement_table.item(0, 2).text(), "3.000")
        table_id = self.window._measurement_table.item(0, 0).data(
            Qt.ItemDataRole.UserRole
        )
        overlay = self.window._viewer._scene._measurement_annotations[0]
        self.assertEqual(table_id, measurement.measurement_id)
        self.assertEqual(overlay.measurement_id, measurement.measurement_id)
        self.assertIn("3.00 Å", self.window._viewer._scene._annotation_texts)
        self.assertIs(self.window._structure, structure)
        self.assertIs(self.window._connectivity, connectivity)
        self.assertEqual(connectivity.bonds, original_bonds)

    def test_measurement_picking_keeps_viewport_until_first_result_without_refit(self):
        viewer = self.window._viewer
        camera = viewer._renderer.GetActiveCamera()
        camera.Azimuth(17.0)
        viewport_size = viewer.size()
        camera_state = (
            camera.GetPosition(),
            camera.GetFocalPoint(),
            camera.GetViewUp(),
            camera.GetParallelScale(),
            camera.GetViewAngle(),
        )

        self.window._distance_measure_button.click()
        self.application.processEvents()
        self.assertEqual(viewer.size(), viewport_size)
        self._pick(0)
        self.assertEqual(viewer.size(), viewport_size)
        self.assertFalse(self.window._measurement_panel.isVisibleTo(self.window))

        self._pick(1)

        self.assertTrue(self.window._measurement_panel.isVisibleTo(self.window))
        self.assertEqual(self.window._measurement_table.rowCount(), 1)
        self.assertEqual(
            (
                camera.GetPosition(),
                camera.GetFocalPoint(),
                camera.GetViewUp(),
                camera.GetParallelScale(),
                camera.GetViewAngle(),
            ),
            camera_state,
        )

    def test_angle_panel_waits_for_third_atom(self):
        viewer = self.window._viewer
        viewport_size = viewer.size()
        self.window._angle_measure_button.click()
        self.application.processEvents()
        self.assertEqual(viewer.size(), viewport_size)
        for index in (2, 0):
            self._pick(index)
            self.assertFalse(self.window._measurement_panel.isVisibleTo(self.window))
            self.assertEqual(viewer.size(), viewport_size)
        self._pick(3)
        self.assertTrue(self.window._measurement_panel.isVisibleTo(self.window))
        self.assertEqual(self.window._measurement_table.rowCount(), 1)

    def test_clear_removes_records_overlays_and_partial_picks_but_keeps_tool(self):
        structure = self.window._structure
        connectivity = self.window._connectivity
        self._measure_distance()
        self._measure_angle()
        self._pick(2, 0)
        scene = self.window._viewer._scene
        self.assertTrue(scene._measurement_pick_indices)
        clear = self.window._measurement_panel.findChild(
            QPushButton, "clearMeasurements"
        )

        clear.click()
        self.application.processEvents()

        self.assertEqual(self.window._measurement_session.measurements, ())
        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(self.window._measurement_table.rowCount(), 0)
        self.assertEqual(scene._measurement_annotations, ())
        self.assertEqual(scene._measurement_pick_indices, ())
        self.assertEqual(scene._measurement_pick_labels, ())
        self.assertEqual(scene._rendered_measurement_ids, ())
        self.assertEqual(scene._dash_polydata.GetNumberOfLines(), 0)
        self.assertEqual(scene._arc_polydata.GetNumberOfLines(), 0)
        self.assertFalse(self.window._measurement_panel.isVisibleTo(self.window))
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.ANGLE)
        self.assertTrue(self.window._angle_measure_button.isChecked())
        self.assertIs(self.window._structure, structure)
        self.assertIs(self.window._connectivity, connectivity)

        self._pick(2, 0, 3)
        self.assertEqual(self.window._measurement_table.rowCount(), 1)
        self.assertTrue(self.window._measurement_panel.isVisibleTo(self.window))

    def test_distance_pick_feedback_has_dedicated_style_and_clears_on_finish(
        self,
    ):
        self.window._distance_measure_button.click()

        self._pick(0)

        scene = self.window._viewer._scene
        self.assertEqual(scene._measurement_pick_indices, (0,))
        self.assertEqual(scene._measurement_pick_labels, ("1",))
        shell_colors = scene._highlight_polydata.GetPointData().GetArray(
            "shell_color"
        )
        self.assertEqual(
            tuple(int(value) for value in shell_colors.GetTuple3(0)),
            MEASUREMENT_HIGHLIGHT_COLOR_RGB,
        )
        self.assertNotEqual(
            MEASUREMENT_HIGHLIGHT_COLOR_RGB,
            PRIMARY_HIGHLIGHT_COLOR_RGB,
        )
        self.assertNotEqual(
            MEASUREMENT_HIGHLIGHT_COLOR_RGB,
            SECONDARY_HIGHLIGHT_COLOR_RGB,
        )

        render_window = self.window._viewer._vtk_widget.GetRenderWindow()
        with (
            patch.object(
                self.window._viewer,
                "set_measurement_annotations",
                wraps=self.window._viewer.set_measurement_annotations,
            ) as annotations,
            patch.object(
                self.window._viewer,
                "set_measurement_pick_feedback",
                wraps=self.window._viewer.set_measurement_pick_feedback,
            ) as feedback,
            patch.object(
                self.window._viewer,
                "set_highlighted_atom_indices",
                wraps=self.window._viewer.set_highlighted_atom_indices,
            ) as highlights,
            patch.object(
                render_window,
                "Render",
                wraps=render_window.Render,
            ) as render,
        ):
            self._pick(1)

        annotations.assert_called_once()
        self.assertEqual(annotations.call_args.kwargs["pick_feedback"], ())
        feedback.assert_not_called()
        highlights.assert_not_called()
        render.assert_called_once_with()
        self.assertEqual(scene._measurement_pick_indices, ())
        self.assertEqual(scene._measurement_pick_labels, ())
        self.assertTrue(
            all(
                not actor.GetVisibility()
                for actor in scene._measurement_pick_text_actors
            )
        )
        self.assertEqual(len(self.window._measurement_session), 1)

    def test_angle_tool_uses_b_as_vertex_and_repeats_without_reactivation(self):
        self.assertFalse(self.window._angle_measure_button.icon().isNull())
        self.assertEqual(self.window._angle_measure_button.toolTip(), "Measure Angle")
        self.assertEqual(
            self.window._angle_measure_button.accessibleName(),
            "Measure Angle",
        )

        self._measure_angle(2, 0, 3)
        self._pick(4, 0, 1)

        first, second = self.window._measurement_session.measurements
        self.assertIsInstance(first, AngleMeasurement)
        self.assertEqual((first.atom_a, first.atom_b, first.atom_c), (2, 0, 3))
        self.assertEqual(first.value_degrees, 90.0)
        self.assertAlmostEqual(second.value_degrees, 70.5287793655, places=9)
        self.assertTrue(self.window._angle_measure_button.isChecked())
        self.assertFalse(self.window._distance_measure_button.isChecked())
        self.assertEqual(self.window._measurement_table.rowCount(), 2)
        self.assertEqual(
            self.window._measurement_table.item(0, 1).text(),
            "O2 – C0 – H3",
        )
        angle_geometry = (
            self.window._viewer._scene._measurement_annotations[0].annotation
        )
        self.assertEqual(angle_geometry.vertex, (0.0, 0.0, 0.0))
        self.assertEqual(angle_geometry.reference_point, (1.0, 0.0, 0.0))
        self.assertEqual(angle_geometry.target_point, (0.0, 1.0, 0.0))
        bonded_pairs = {
            (bond.first_index, bond.second_index)
            for bond in self.window._connectivity
        }
        self.assertIn((0, 2), bonded_pairs)
        self.assertIn((0, 3), bonded_pairs)
        self.assertGreater(
            self.window._viewer._scene._dash_polydata.GetNumberOfLines(),
            0,
        )

    def test_angle_pick_order_and_nonbonded_guides_preserve_connectivity(self):
        connectivity = self.window._connectivity
        original_bonds = connectivity.bonds
        bonded_pairs = {
            (bond.first_index, bond.second_index) for bond in connectivity
        }
        self.assertNotIn((0, 1), bonded_pairs)
        self.assertNotIn((0, 4), bonded_pairs)
        self.window._angle_measure_button.click()

        self._pick(1)
        scene = self.window._viewer._scene
        self.assertEqual(scene._measurement_pick_labels, ("1",))
        self._pick(0)
        self.assertEqual(scene._measurement_pick_indices, (1, 0))
        self.assertEqual(scene._measurement_pick_labels, ("1", "2"))

        render_window = self.window._viewer._vtk_widget.GetRenderWindow()
        with (
            patch.object(
                self.window._viewer,
                "set_measurement_annotations",
                wraps=self.window._viewer.set_measurement_annotations,
            ) as annotations,
            patch.object(
                self.window._viewer,
                "set_measurement_pick_feedback",
                wraps=self.window._viewer.set_measurement_pick_feedback,
            ) as feedback,
            patch.object(
                self.window._viewer,
                "set_highlighted_atom_indices",
                wraps=self.window._viewer.set_highlighted_atom_indices,
            ) as highlights,
            patch.object(
                render_window,
                "Render",
                wraps=render_window.Render,
            ) as render,
        ):
            self._pick(4)

        annotations.assert_called_once()
        self.assertEqual(annotations.call_args.kwargs["pick_feedback"], ())
        feedback.assert_not_called()
        highlights.assert_not_called()
        render.assert_called_once_with()
        measurement = self.window._measurement_session.measurements[0]
        self.assertIsInstance(measurement, AngleMeasurement)
        self.assertEqual(
            (measurement.atom_a, measurement.atom_b, measurement.atom_c),
            (1, 0, 4),
        )
        annotation = scene._measurement_annotations[0].annotation
        expected_guide_count = len(
            _dash_segments(annotation.reference_point, annotation.vertex)
        ) + len(_dash_segments(annotation.vertex, annotation.target_point))
        self.assertEqual(
            scene._dash_polydata.GetNumberOfLines(),
            expected_guide_count,
        )
        self.assertGreater(scene._arc_polydata.GetNumberOfLines(), 0)
        self.assertIn(
            f"{measurement.value_degrees:.1f}°",
            scene._annotation_texts,
        )
        self.assertEqual(scene._measurement_pick_indices, ())
        self.assertEqual(scene._measurement_pick_labels, ())
        self.assertIs(self.window._connectivity, connectivity)
        self.assertEqual(connectivity.bonds, original_bonds)

    def test_ordinary_atom_click_uses_platform_drag_threshold(self):
        viewer = self.window._viewer
        drag_threshold = QApplication.startDragDistance()
        self.assertGreater(drag_threshold, 0)

        def mouse_event(event_type, position, button, buttons):
            return QMouseEvent(
                event_type,
                QPointF(position),
                QPointF(position),
                QPointF(viewer._vtk_widget.mapToGlobal(position)),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )

        start = QPoint(40, 40)
        accepted_release = QPoint(40 + drag_threshold, 40)
        rejected_release = QPoint(40 + drag_threshold + 1, 60)
        with patch.object(viewer, "_pick_at") as pick_at:
            viewer.eventFilter(
                viewer._vtk_widget,
                mouse_event(
                    QEvent.Type.MouseButtonPress,
                    start,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                ),
            )
            viewer.eventFilter(
                viewer._vtk_widget,
                mouse_event(
                    QEvent.Type.MouseButtonRelease,
                    accepted_release,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                ),
            )
            self.application.processEvents()
            pick_at.assert_called_once_with(accepted_release)

            pick_at.reset_mock()
            second_start = QPoint(40, 60)
            viewer.eventFilter(
                viewer._vtk_widget,
                mouse_event(
                    QEvent.Type.MouseButtonPress,
                    second_start,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                ),
            )
            viewer.eventFilter(
                viewer._vtk_widget,
                mouse_event(
                    QEvent.Type.MouseButtonRelease,
                    rejected_release,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                ),
            )
            self.application.processEvents()
            pick_at.assert_not_called()

    def test_duplicate_selections_and_escape_create_no_measurement(self):
        self.window._distance_measure_button.click()
        self._pick(0, 0)
        self.assertEqual(self.window._measurement_session.measurements, ())
        self.assertEqual(self.window._measurement_selection, (0,))
        self.assertIn(
            "different second atom",
            self.window._operation_label.text(),
        )

        self.window._viewer._vtk_widget.setFocus()
        QTest.keyClick(self.window._viewer._vtk_widget, Qt.Key.Key_Escape)
        self.application.processEvents()
        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(
            self.window._viewer._scene._measurement_pick_labels,
            (),
        )
        self.assertTrue(self.window._distance_measure_button.isChecked())

        self.window._angle_measure_button.click()
        for sequence in ((2, 2, 3), (2, 0, 2), (2, 0, 0)):
            with self.subTest(sequence=sequence):
                self._pick(*sequence)
                self.assertEqual(self.window._measurement_session.measurements, ())
                QTest.keyClick(self.window, Qt.Key.Key_Escape)
                self.application.processEvents()
        self.assertTrue(self.window._angle_measure_button.isChecked())

    def test_escape_clears_partial_angle_feedback_but_keeps_completed_measurement(
        self,
    ):
        self._measure_distance(0, 1)
        completed = self.window._measurement_session.measurements[0]
        self.window._angle_measure_button.click()
        self._pick(2, 0)
        scene = self.window._viewer._scene
        self.assertEqual(scene._measurement_pick_indices, (2, 0))
        self.assertEqual(scene._measurement_pick_labels, ("1", "2"))

        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.application.processEvents()

        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(scene._measurement_pick_indices, ())
        self.assertEqual(scene._measurement_pick_labels, ())
        self.assertTrue(self.window._angle_measure_button.isChecked())
        self.assertEqual(
            self.window._measurement_session.measurements,
            (completed,),
        )
        self.assertEqual(
            scene._rendered_measurement_ids,
            (completed.measurement_id,),
        )

    def test_modes_are_exclusive_and_au_tool_cancels_partial_measurement(self):
        self.window._distance_measure_button.click()
        self._pick(0)
        self.window._angle_measure_button.click()

        self.assertFalse(self.window._distance_measure_button.isChecked())
        self.assertTrue(self.window._angle_measure_button.isChecked())
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.ANGLE)
        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(
            self.window._viewer._scene._measurement_pick_labels,
            (),
        )

        self._pick(2)
        self.window._au_tool_visibility_toggled(True)

        self.assertEqual(self.window._pick_mode, _ViewerPickMode.AU_PLACEMENT)
        self.assertFalse(self.window._distance_measure_button.isChecked())
        self.assertFalse(self.window._angle_measure_button.isChecked())
        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(
            self.window._viewer._scene._measurement_pick_labels,
            (),
        )
        self._pick(0)
        self.assertEqual(self.window._measurement_session.measurements, ())

    def test_delete_middle_row_uses_id_and_preserves_other_overlays(self):
        structure = self.window._structure
        connectivity = self.window._connectivity
        self._measure_distance(0, 1)
        self._measure_angle(2, 0, 3)
        self._measure_distance(2, 3)
        created_ids = tuple(
            item.measurement_id
            for item in self.window._measurement_session.measurements
        )
        self.assertEqual(
            created_ids,
            tuple(range(created_ids[0], created_ids[0] + 3)),
        )
        table = self.window._measurement_table
        second_item = table.item(1, 0)
        position = table.visualItemRect(second_item).center()
        with patch("tools.molecule_viewer_demo.QMenu") as menu_type:
            menu = menu_type.return_value
            delete_action = object()
            menu.addAction.return_value = delete_action
            menu.exec.return_value = delete_action
            self.window._show_measurement_context_menu(position)

        self.assertEqual(
            tuple(
                item.measurement_id
                for item in self.window._measurement_session.measurements
            ),
            (created_ids[0], created_ids[2]),
        )
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(
            tuple(
                table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(table.rowCount())
            ),
            (created_ids[0], created_ids[2]),
        )
        self.assertEqual(
            self.window._viewer._scene._rendered_measurement_ids,
            (created_ids[0], created_ids[2]),
        )
        scene = self.window._viewer._scene
        self.assertEqual(scene._arc_polydata.GetNumberOfLines(), 0)
        self.assertTrue(
            all(not text.endswith("°") for text in scene._annotation_texts)
        )
        expected_distance_dash_count = sum(
            len(_dash_segments(overlay.annotation.start, overlay.annotation.end))
            for overlay in scene._measurement_annotations
        )
        self.assertEqual(
            scene._dash_polydata.GetNumberOfLines(),
            expected_distance_dash_count,
        )
        self.assertIs(self.window._structure, structure)
        self.assertIs(self.window._connectivity, connectivity)

    def test_reload_clears_records_table_overlays_partial_selection_and_mode(self):
        self._measure_distance(0, 1)
        self.window._angle_measure_button.click()
        self._pick(2, 0)
        self.assertEqual(len(self.window._measurement_session), 1)
        self.assertEqual(self.window._measurement_selection, (2, 0))
        self.assertEqual(
            self.window._viewer._scene._measurement_pick_labels,
            ("1", "2"),
        )

        self.window._reload()
        self.application.processEvents()

        self.assertEqual(self.window._measurement_session.measurements, ())
        self.assertEqual(self.window._measurement_table.rowCount(), 0)
        self.assertEqual(
            self.window._viewer._scene._measurement_annotations,
            (),
        )
        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(
            self.window._viewer._scene._measurement_pick_labels,
            (),
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.NORMAL)
        self.assertFalse(self.window._distance_measure_button.isChecked())
        self.assertFalse(self.window._angle_measure_button.isChecked())
        self.assertIs(self.window._structure, self.window._source_structure)

    def test_saved_geometry_is_identical_with_measurements(self):
        structure = self.window._structure
        expected = render_geometry_in(structure)
        self._measure_distance(0, 1)
        self._measure_angle(2, 0, 3)
        self.window._applied_result = object()

        output_path = Path(self.temporary.name) / "measured_geometry.in"
        self.window._save_geometry_to_path(output_path)

        self.assertEqual(output_path.read_text(encoding="utf-8"), expected)
        self.assertNotIn("measurement", output_path.read_text(encoding="utf-8"))

    def test_contact_au_application_clears_manual_measurements(self):
        self.window._load(REFERENCE_XYZ_PATH)
        self.application.processEvents()
        self._measure_distance(0, 1)
        self.assertEqual(len(self.window._measurement_session), 1)
        controls = tuple(self.window._site_controls.values())
        controls[0].checkbox.setChecked(True)
        controls[1].checkbox.setChecked(True)
        self.application.processEvents()

        self.window._done_button.click()
        self.application.processEvents()

        self.assertTrue(self.window._confirmed)
        self.assertEqual(self.window._measurement_session.measurements, ())
        self.assertEqual(self.window._measurement_table.rowCount(), 0)
        self.assertEqual(
            self.window._viewer._scene._measurement_annotations,
            (),
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.NORMAL)


if __name__ == "__main__":
    unittest.main()
