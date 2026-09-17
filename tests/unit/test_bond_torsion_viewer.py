import gc
import unittest
from math import radians

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qt_test_support import wait_until

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.visualization.bond_torsion import (
    rotate_structure_about_bond,
    structure_coordinates,
)
from moltage.visualization.measurements import DistanceMeasurement
from moltage.visualization.molecule_scene import (
    _add,
    _display_point,
    _normalized,
    _rotate_vector_about_axis,
    _subtract,
)
from tools.molecule_viewer_demo import MoleculeViewerDemo, _ViewerPickMode


class BondTorsionViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
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
        gc.collect()

    def setUp(self):
        self.structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 1.0, 0.0),
                Atom(1, "C", 0.0, 0.0, 0.0),
                Atom(2, "C", 1.0, 0.0, 0.0),
                Atom(3, "H", 1.0, 1.0, 0.0),
                Atom(4, "Na", 5.0, 0.0, 0.0),
                Atom(5, "Cl", 6.0, 0.0, 0.0),
            ),
            comment="torsion viewer fixture",
        )
        self.connectivity = Connectivity(
            6,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.0),
                Bond(2, 3, 1.0),
                Bond(4, 5, 1.0),
            ),
        )
        self.radii = {
            "C": 0.76,
            "H": 0.31,
            "Na": 1.66,
            "Cl": 1.02,
        }
        self._install_structure(self.structure, self.connectivity)

    def _install_structure(self, structure, connectivity):
        window = self.window
        window._prepare_for_structure_replacement()
        window._viewer.set_molecule(structure, connectivity, self.radii)
        window._source_path = None
        window._source_structure = structure
        window._source_connectivity = connectivity
        window._structure = structure
        window._connectivity = connectivity
        window._covalent_radii = self.radii
        window._anchors = ()
        window._site_controls = {}
        window._electrode_site_controls = {}
        window._current_proposals = ()
        window._electrode_current_proposal = None
        window._applied_result = None
        window._applied_electrode_result = None
        window._set_measurement_tools_enabled(True)
        self.application.processEvents()

    def _select_bridge(self):
        if not self.window._rotate_bond_button.isChecked():
            self.window._rotate_bond_button.click()
        self.window._bond_rotation_edge_picked(1, 2)
        self.application.processEvents()
        return self.window._torsion_session

    def _numeric_angle(self, value):
        self.window._start_torsion_numeric_edit()
        self.window._torsion_numeric_input.setText(str(value))
        self.window._commit_torsion_numeric_edit()
        self.application.processEvents()

    def _widget_point_for_world(self, world_point):
        viewer = self.window._viewer
        render_window = viewer._vtk_widget.GetRenderWindow()
        render_window.Render()
        display = _display_point(viewer._renderer, world_point)
        self.assertIsNotNone(display)
        return self._widget_point_for_display(display)

    def _widget_point_for_display(self, display):
        viewer = self.window._viewer
        render_window = viewer._vtk_widget.GetRenderWindow()
        render_width, render_height = render_window.GetSize()
        widget_width = max(1, viewer._vtk_widget.width())
        widget_height = max(1, viewer._vtk_widget.height())
        return QPoint(
            round(display[0] * widget_width / render_width),
            round((render_height - 1 - display[1]) * widget_height / render_height),
        )

    def test_toolbar_mode_is_accessible_and_exclusive_with_measurements(self):
        measurement = self.window._measurement_session.add_distance(
            self.structure,
            0,
            3,
        )
        self.window._sync_measurement_view()
        self.window._angle_measure_button.click()
        self.window._report_picked_atom(0)

        self.window._rotate_bond_button.click()

        self.assertFalse(self.window._rotate_bond_button.icon().isNull())
        self.assertEqual(self.window._rotate_bond_button.toolTip(), "Rotate Bond")
        self.assertEqual(
            self.window._rotate_bond_button.accessibleName(),
            "Rotate Bond",
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.BOND_ROTATION)
        self.assertTrue(self.window._rotate_bond_button.isChecked())
        self.assertFalse(self.window._distance_measure_button.isChecked())
        self.assertFalse(self.window._angle_measure_button.isChecked())
        self.assertEqual(self.window._measurement_selection, ())
        self.assertEqual(
            self.window._measurement_session.measurements,
            (measurement,),
        )
        self.assertTrue(self.window._viewer._bond_rotation_enabled)

    def test_history_controls_are_first_in_the_normal_toolbar(self):
        main_toolbar = self.window._main_toolbar
        undo_action = self.window._geometry_undo_action
        redo_action = self.window._geometry_redo_action
        undo_button = self.window._geometry_undo_button
        redo_button = self.window._geometry_redo_button

        self.assertEqual(
            self.window.toolBarArea(main_toolbar),
            Qt.ToolBarArea.TopToolBarArea,
        )
        self.assertEqual(main_toolbar.actions()[:2], [undo_action, redo_action])
        self.assertIs(
            self.window._distance_measure_button.parentWidget(),
            main_toolbar,
        )
        self.assertIs(undo_button.defaultAction(), undo_action)
        self.assertIs(redo_button.defaultAction(), redo_action)
        self.assertFalse(undo_action.isCheckable())
        self.assertFalse(redo_action.isCheckable())
        self.assertEqual(undo_button.toolTip(), "Undo")
        self.assertEqual(redo_button.toolTip(), "Redo")
        self.assertEqual(undo_button.accessibleName(), "Undo")
        self.assertEqual(redo_button.accessibleName(), "Redo")
        self.assertFalse(undo_button.icon().isNull())
        self.assertFalse(redo_button.icon().isNull())
        self.assertEqual(main_toolbar.iconSize(), QSize(20, 20))
        self.assertFalse(undo_action.isEnabled())
        self.assertFalse(redo_action.isEnabled())

    def test_valid_bridge_selection_ignores_counterion_and_shows_gizmo(self):
        connectivity = self.window._connectivity

        session = self._select_bridge()

        self.assertIsNotNone(session)
        self.assertEqual(session.analysis.component_count_before, 2)
        self.assertEqual(session.analysis.component_count_after, 3)
        self.assertEqual(session.fixed_indices, (0, 1))
        self.assertEqual(session.rotating_indices, (2, 3))
        self.assertNotIn(4, session.fixed_indices + session.rotating_indices)
        self.assertNotIn(5, session.fixed_indices + session.rotating_indices)
        scene = self.window._viewer._scene
        self.assertEqual(scene._selected_bond_polydata.GetNumberOfLines(), 1)
        self.assertGreater(scene._torsion_circle_polydata.GetNumberOfLines(), 0)
        self.assertEqual(scene._torsion_angle_text_actor.GetInput(), "0.0°")
        self.assertTrue(scene._torsion_angle_text_actor.GetVisibility())
        self.assertTrue(scene._torsion_handle_actor.GetVisibility())
        self.assertFalse(self.window._viewer._torsion_angle_editor.isVisible())
        self.assertIs(
            self.window._torsion_numeric_input.parentWidget(),
            self.window._viewer._torsion_angle_editor,
        )
        self.assertEqual(self.window._torsion_numeric_input.text(), "0.0")
        self.assertIs(self.window._connectivity, connectivity)

    def test_native_bond_click_maps_rendered_cell_to_connectivity_edge(self):
        self.window._rotate_bond_button.click()
        point = self._widget_point_for_world((0.5, 0.0, 0.0))

        QTest.mouseClick(
            self.window._viewer._vtk_widget,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            point,
        )
        self.application.processEvents()

        session = self.window._torsion_session
        self.assertIsNotNone(session)
        self.assertEqual(session.selected_edge, (1, 2))

    def test_native_selected_bond_drag_has_linear_gain_and_owns_mouse(self):
        self._select_bridge()
        viewer = self.window._viewer
        start = self._widget_point_for_world((0.5, 0.0, 0.0))
        display = viewer._display_position(start)
        target = viewer._scene.pick_torsion_target(*display)
        self.assertIn(target, {"selected_bond", "moving_ray", "circle"})
        vtk_direction = viewer._scene.torsion_drag_direction(
            target,
            *display,
        )
        qt_direction = (vtk_direction[0], -vtk_direction[1])
        end = QPoint(
            start.x() + round(qt_direction[0] * 100.0),
            start.y() + round(qt_direction[1] * 100.0),
        )
        camera = viewer._renderer.GetActiveCamera()
        camera_before = (
            tuple(camera.GetPosition()),
            tuple(camera.GetFocalPoint()),
            tuple(camera.GetViewUp()),
        )

        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(start),
            QPointF(start),
            QPointF(viewer._vtk_widget.mapToGlobal(start)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(end),
            QPointF(end),
            QPointF(viewer._vtk_widget.mapToGlobal(end)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(end),
            QPointF(end),
            QPointF(viewer._vtk_widget.mapToGlobal(end)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(viewer.eventFilter(viewer._vtk_widget, press_event))
        self.assertTrue(viewer.eventFilter(viewer._vtk_widget, move_event))
        self.assertTrue(viewer.eventFilter(viewer._vtk_widget, release_event))
        self.application.processEvents()

        self.assertAlmostEqual(
            self.window._torsion_session.relative_angle_degrees,
            30.0,
            delta=0.35,
        )
        self.assertEqual(len(self.window._geometry_undo_history), 1)
        self.assertEqual(
            (
                tuple(camera.GetPosition()),
                tuple(camera.GetFocalPoint()),
                tuple(camera.GetViewUp()),
            ),
            camera_before,
        )

    def test_native_spherical_handle_direction_follows_pointer_around_axis(self):
        self._select_bridge()
        viewer = self.window._viewer
        scene = viewer._scene
        center = scene._torsion_center
        gizmo = scene._torsion_gizmo
        fixed = scene._point_for_atom(gizmo.fixed_atom_index)
        rotating = scene._point_for_atom(gizmo.rotating_atom_index)
        axis = _normalized(_subtract(rotating, fixed), "test torsion axis")
        camera = viewer._renderer.GetActiveCamera()
        camera.SetPosition(*_add(center, tuple(value * 8.0 for value in axis)))
        camera.SetFocalPoint(*center)
        camera.SetViewUp(0.0, 1.0, 0.0)
        viewer._renderer.ResetCameraClippingRange()
        viewer._vtk_widget.GetRenderWindow().Render()

        start = self._widget_point_for_world(scene._torsion_handle_position)
        display = viewer._display_position(start)
        self.assertEqual(
            scene.pick_torsion_target(*display),
            "rotation_handle",
        )
        radial = _subtract(scene._torsion_handle_position, center)
        target_world = _add(
            center,
            _rotate_vector_about_axis(radial, axis, radians(60.0)),
        )
        end = self._widget_point_for_world(target_world)
        camera_before = (
            tuple(camera.GetPosition()),
            tuple(camera.GetFocalPoint()),
            tuple(camera.GetViewUp()),
        )

        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(start),
            QPointF(start),
            QPointF(viewer._vtk_widget.mapToGlobal(start)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(end),
            QPointF(end),
            QPointF(viewer._vtk_widget.mapToGlobal(end)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(end),
            QPointF(end),
            QPointF(viewer._vtk_widget.mapToGlobal(end)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(viewer.eventFilter(viewer._vtk_widget, press_event))
        self.assertTrue(viewer.eventFilter(viewer._vtk_widget, move_event))
        self.application.processEvents()

        self.assertFalse(viewer._torsion_angle_editor.isVisible())
        self.assertTrue(scene._torsion_angle_text_actor.GetVisibility())
        moved_handle = self._widget_point_for_world(
            scene._torsion_handle_position
        )
        self.assertLessEqual((moved_handle - end).manhattanLength(), 3)
        self.assertTrue(viewer.eventFilter(viewer._vtk_widget, release_event))
        self.application.processEvents()

        self.assertAlmostEqual(
            self.window._torsion_session.relative_angle_degrees,
            60.0,
            delta=0.75,
        )
        self.assertEqual(len(self.window._geometry_undo_history), 1)
        self.assertEqual(
            (
                tuple(camera.GetPosition()),
                tuple(camera.GetFocalPoint()),
                tuple(camera.GetViewUp()),
            ),
            camera_before,
        )

    def test_ring_edge_rejection_changes_no_geometry_or_undo_state(self):
        ring_structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "C", 1.0, 0.0, 0.0),
                Atom(2, "C", 0.5, 0.8, 0.0),
            )
        )
        ring_connectivity = Connectivity(
            3,
            (Bond(0, 1, 1.0), Bond(1, 2, 1.0), Bond(0, 2, 1.0)),
        )
        self._install_structure(ring_structure, ring_connectivity)
        before = structure_coordinates(ring_structure)
        self.window._rotate_bond_button.click()

        self.window._bond_rotation_edge_picked(0, 1)

        self.assertIsNone(self.window._torsion_session)
        self.assertEqual(
            structure_coordinates(self.window._structure),
            before,
        )
        self.assertFalse(self.window._geometry_undo_history.can_undo)
        self.assertIn("another path", self.window._operation_label.text())
        self.assertEqual(
            self.window._viewer._scene._selected_bond_polydata.GetNumberOfLines(),
            0,
        )

    def test_one_live_drag_is_one_undo_and_invalidates_measurements_on_motion(self):
        measurement = self.window._measurement_session.add_distance(
            self.structure,
            0,
            3,
        )
        self.window._sync_measurement_view()
        connectivity = self.window._connectivity
        bonds = connectivity.bonds
        original = structure_coordinates(self.structure)
        session = self._select_bridge()

        self.window._torsion_drag_started()
        self.window._torsion_drag_changed(10.0)
        self.window._torsion_drag_changed(20.0)
        self.window._torsion_drag_changed(30.0)
        self.window._torsion_drag_finished()

        self.assertEqual(session.relative_angle_degrees, 30.0)
        self.assertEqual(len(self.window._geometry_undo_history), 1)
        self.assertEqual(self.window._measurement_session.measurements, ())
        self.assertEqual(self.window._measurement_table.rowCount(), 0)
        self.assertEqual(
            self.window._viewer._scene._measurement_annotations,
            (),
        )
        self.assertNotIn(
            measurement,
            self.window._measurement_session.measurements,
        )
        current = structure_coordinates(self.window._structure)
        self.assertNotEqual(current, original)
        self.assertIs(
            self.window._active_geometry_workspace().structure,
            self.window._structure,
        )
        self.assertEqual(current[4:], original[4:])
        self.assertIs(self.window._connectivity, connectivity)
        self.assertEqual(connectivity.bonds, bonds)
        self.assertTrue(self.window._geometry_undo_action.isEnabled())

        self.window._geometry_undo_action.trigger()

        self.assertEqual(
            structure_coordinates(self.window._structure),
            original,
        )
        self.assertIs(
            self.window._active_geometry_workspace().structure,
            self.window._structure,
        )
        self.assertIsNone(self.window._torsion_session)
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.BOND_ROTATION)
        self.assertEqual(self.window._measurement_session.measurements, ())
        self.assertIs(self.window._connectivity, connectivity)
        self.assertTrue(self.window._geometry_redo_action.isEnabled())

        self.window._geometry_redo_action.trigger()

        self.assertEqual(
            structure_coordinates(self.window._structure),
            current,
        )
        self.assertIs(
            self.window._active_geometry_workspace().structure,
            self.window._structure,
        )
        self.assertIsNone(self.window._torsion_session)
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.BOND_ROTATION)
        self.assertIs(self.window._connectivity, connectivity)

    def test_no_op_drag_keeps_completed_measurement_and_adds_no_history(self):
        measurement = self.window._measurement_session.add_distance(
            self.structure,
            0,
            3,
        )
        self.window._sync_measurement_view()
        self._select_bridge()

        self.window._torsion_drag_started()
        self.window._torsion_drag_finished()

        self.assertEqual(
            self.window._measurement_session.measurements,
            (measurement,),
        )
        self.assertFalse(self.window._geometry_undo_history.can_undo)
        self.assertFalse(self.window._geometry_undo_history.can_redo)

    def test_numeric_input_is_absolute_from_current_baseline(self):
        session = self._select_bridge()
        baseline = self.window._structure

        self._numeric_angle(40.0)
        at_forty = structure_coordinates(self.window._structure)
        self._numeric_angle(60.0)

        expected = rotate_structure_about_bond(
            baseline,
            session.fixed_endpoint,
            session.rotating_endpoint,
            session.rotating_indices,
            60.0,
        )
        self.assertEqual(session.relative_angle_degrees, 60.0)
        self.assertNotEqual(
            structure_coordinates(self.window._structure),
            at_forty,
        )
        for actual, target in zip(
            structure_coordinates(self.window._structure),
            structure_coordinates(expected),
            strict=True,
        ):
            for actual_component, target_component in zip(
                actual,
                target,
                strict=True,
            ):
                self.assertAlmostEqual(actual_component, target_component, places=12)
        self.assertEqual(len(self.window._geometry_undo_history), 2)

    def test_on_canvas_angle_field_enter_commits_to_working_structure(self):
        session = self._select_bridge()
        before = structure_coordinates(self.window._structure)
        field = self.window._torsion_numeric_input
        viewer = self.window._viewer
        scene = viewer._scene
        self.assertFalse(field.hasFocus())
        self.assertFalse(viewer._torsion_angle_editor.isVisible())
        self.assertTrue(scene._torsion_angle_text_actor.GetVisibility())

        label_display = scene.torsion_label_display_position()
        self.assertIsNotNone(label_display)
        QTest.mouseClick(
            viewer._vtk_widget,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            self._widget_point_for_display(label_display),
        )
        self.application.processEvents()

        wait_until(
            lambda: field.hasFocus()
            and viewer._torsion_angle_editor.isVisibleTo(viewer._vtk_widget),
            message="on-canvas torsion editor did not become active",
        )
        self.assertFalse(scene._torsion_angle_text_actor.GetVisibility())
        field.selectAll()
        QTest.keyClicks(field, "47.5")

        QTest.keyClick(field, Qt.Key.Key_Return)
        self.application.processEvents()

        self.assertAlmostEqual(session.relative_angle_degrees, 47.5)
        self.assertNotEqual(structure_coordinates(self.window._structure), before)
        self.assertIs(
            self.window._active_geometry_workspace().structure,
            self.window._structure,
        )
        self.assertEqual(len(self.window._geometry_undo_history), 1)
        self.assertFalse(field.isVisible())
        self.assertFalse(field.hasFocus())
        self.assertFalse(viewer._torsion_angle_editor.isVisible())
        self.assertTrue(scene._torsion_angle_text_actor.GetVisibility())
        self.assertEqual(scene._torsion_angle_text_actor.GetInput(), "+47.5°")

    def test_numeric_escape_cancels_without_geometry_or_history(self):
        self._select_bridge()
        before = structure_coordinates(self.window._structure)
        self.window._start_torsion_numeric_edit()
        self.window._torsion_numeric_input.setText("92")

        QTest.keyClick(self.window._torsion_numeric_input, Qt.Key.Key_Escape)
        self.application.processEvents()

        self.assertFalse(self.window._torsion_numeric_input.isVisible())
        self.assertEqual(self.window._torsion_numeric_input.text(), "0")
        self.assertEqual(structure_coordinates(self.window._structure), before)
        self.assertFalse(self.window._geometry_undo_history.can_undo)
        self.assertTrue(
            self.window._viewer._scene._torsion_angle_text_actor.GetVisibility()
        )

    def test_side_switch_rebases_without_motion_or_undo(self):
        session = self._select_bridge()
        self._numeric_angle(40.0)
        before = structure_coordinates(self.window._structure)
        history_depth = len(self.window._geometry_undo_history)

        self.window._switch_torsion_side()

        self.assertEqual(structure_coordinates(self.window._structure), before)
        self.assertEqual(session.fixed_indices, (2, 3))
        self.assertEqual(session.rotating_indices, (0, 1))
        self.assertEqual(session.relative_angle_degrees, 0.0)
        self.assertEqual(len(self.window._geometry_undo_history), history_depth)
        scene = self.window._viewer._scene
        fixed_end = scene._torsion_fixed_ray_polydata.GetPoint(1)
        moving_end = scene._torsion_moving_ray_polydata.GetPoint(1)
        self.assertEqual(fixed_end, moving_end)
        self.assertEqual(scene._torsion_angle_text_actor.GetInput(), "0.0°")

    def test_five_level_undo_redo_actions_and_shortcuts_share_restore_paths(self):
        self.assertFalse(self.window._geometry_undo_action.isEnabled())
        self.assertFalse(self.window._geometry_redo_action.isEnabled())
        self._select_bridge()
        states = [structure_coordinates(self.window._structure)]
        for angle in (10.0, 20.0, 30.0, 40.0, 50.0):
            self._numeric_angle(angle)
            states.append(structure_coordinates(self.window._structure))
        self.assertEqual(len(self.window._geometry_undo_history), 5)
        self.assertTrue(self.window._geometry_undo_action.isEnabled())
        self.assertFalse(self.window._geometry_redo_action.isEnabled())
        self.assertEqual(
            self.window._geometry_undo_action.shortcut().matches(
                QKeySequence(QKeySequence.StandardKey.Undo)
            ),
            QKeySequence.SequenceMatch.ExactMatch,
        )
        self.assertEqual(
            self.window._geometry_redo_action.shortcut().matches(
                QKeySequence(QKeySequence.StandardKey.Redo)
            ),
            QKeySequence.SequenceMatch.ExactMatch,
        )

        self.window._geometry_undo_action.trigger()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            states[4],
        )
        self.window.setFocus()
        QTest.keyClick(
            self.window,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.application.processEvents()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            states[3],
        )
        for expected in (states[2], states[1], states[0]):
            self.window._geometry_undo_action.trigger()
            self.assertEqual(
                structure_coordinates(self.window._structure),
                expected,
            )
        after_five = structure_coordinates(self.window._structure)
        self.window._geometry_undo_action.trigger()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            after_five,
        )
        self.assertFalse(self.window._geometry_undo_action.isEnabled())
        self.assertTrue(self.window._geometry_redo_action.isEnabled())

        self.window._geometry_redo_action.trigger()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            states[1],
        )
        self.window.setFocus()
        QTest.keySequence(
            self.window,
            QKeySequence(QKeySequence.StandardKey.Redo),
        )
        self.application.processEvents()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            states[2],
        )
        for expected in (states[3], states[4], states[5]):
            self.window._geometry_redo_action.trigger()
            self.assertEqual(
                structure_coordinates(self.window._structure),
                expected,
            )
        after_five_redos = structure_coordinates(self.window._structure)
        self.window._geometry_redo_action.trigger()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            after_five_redos,
        )
        self.assertTrue(self.window._geometry_undo_action.isEnabled())
        self.assertFalse(self.window._geometry_redo_action.isEnabled())

    def test_new_geometry_edit_after_undo_discards_redo_branch(self):
        self._select_bridge()
        for angle in (10.0, 20.0, 30.0):
            self._numeric_angle(angle)
        self.window._geometry_undo_action.trigger()
        self.window._geometry_undo_action.trigger()
        self.assertTrue(self.window._geometry_undo_history.can_redo)
        self.assertTrue(self.window._geometry_redo_action.isEnabled())
        self.assertTrue(self.window._geometry_redo_action.isEnabled())

        before_new_edit = structure_coordinates(self.window._structure)
        self._select_bridge()
        self._numeric_angle(25.0)
        after_new_edit = structure_coordinates(self.window._structure)

        self.assertNotEqual(after_new_edit, before_new_edit)
        self.assertFalse(self.window._geometry_undo_history.can_redo)
        self.assertFalse(self.window._geometry_redo_action.isEnabled())
        self.assertFalse(self.window._geometry_redo_action.isEnabled())
        QTest.keySequence(
            self.window,
            QKeySequence(QKeySequence.StandardKey.Redo),
        )
        self.application.processEvents()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            after_new_edit,
        )

    def test_turning_rotation_mode_off_keeps_undo_and_redo_history(self):
        self._select_bridge()
        self._numeric_angle(30.0)
        changed = structure_coordinates(self.window._structure)
        self.window._geometry_undo_action.trigger()
        self.assertTrue(self.window._geometry_redo_action.isEnabled())

        self.window._rotate_bond_button.click()

        self.assertEqual(self.window._pick_mode, _ViewerPickMode.NORMAL)
        self.assertFalse(self.window._rotate_bond_button.isChecked())
        self.assertTrue(self.window._geometry_redo_action.isEnabled())
        self.window._geometry_redo_action.trigger()
        self.assertEqual(
            structure_coordinates(self.window._structure),
            changed,
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.NORMAL)

    def test_structure_replacement_clears_both_histories_and_shortcuts(self):
        self._select_bridge()
        self._numeric_angle(30.0)
        self._numeric_angle(60.0)
        self.window._geometry_undo_action.trigger()
        self.assertTrue(self.window._geometry_undo_history.can_undo)
        self.assertTrue(self.window._geometry_undo_history.can_redo)
        replacement = MolecularStructure(
            (
                Atom(0, "C", 10.0, 0.0, 0.0),
                Atom(1, "C", 11.0, 0.0, 0.0),
            )
        )
        replacement_connectivity = Connectivity(2, (Bond(0, 1, 1.0),))

        self._install_structure(replacement, replacement_connectivity)
        before = structure_coordinates(replacement)
        QTest.keyClick(
            self.window,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.ControlModifier,
        )
        QTest.keySequence(
            self.window,
            QKeySequence(QKeySequence.StandardKey.Redo),
        )
        self.application.processEvents()

        self.assertEqual(structure_coordinates(self.window._structure), before)
        self.assertFalse(self.window._geometry_undo_history.can_undo)
        self.assertFalse(self.window._geometry_undo_history.can_redo)
        self.assertFalse(self.window._geometry_undo_action.isEnabled())
        self.assertFalse(self.window._geometry_redo_action.isEnabled())
        self.assertIsNone(self.window._torsion_session)
        self.assertEqual(
            self.window._viewer._scene._selected_bond_polydata.GetNumberOfLines(),
            0,
        )

    def test_au_interaction_takes_mode_ownership_from_rotation(self):
        self._select_bridge()

        self.window._enter_au_placement_mode()

        self.assertEqual(self.window._pick_mode, _ViewerPickMode.AU_PLACEMENT)
        self.assertFalse(self.window._rotate_bond_button.isChecked())
        self.assertFalse(self.window._viewer._bond_rotation_enabled)
        self.assertIsNone(self.window._torsion_session)
        self.assertEqual(
            self.window._viewer._scene._selected_bond_polydata.GetNumberOfLines(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
