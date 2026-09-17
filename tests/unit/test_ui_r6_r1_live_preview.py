import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox

from moltage.aims.geometry_writer import render_geometry_in
from moltage.domain.bond_display import ConnectivitySource
from moltage.gui.bond_detection_dialog import BondDetectionDialog
from moltage.gui.view_settings_dialog import ViewSettingsDialog
from moltage.visualization.molecule_scene import (
    BOND_TUBE_RADIUS,
    ELEMENT_COLORS_RGB,
)
from moltage.visualization.view_preferences import ViewPreferences
from tools.molecule_viewer_demo import MoleculeViewerDemo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)
MIXED_MOL_PATH = PROJECT_ROOT / "tests" / "fixtures" / "ui_r5" / "mixed.mol"


class _CallbackSignal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, value) -> None:
        for callback in tuple(self._callbacks):
            callback(value)


def _display_color(scene, atom_index: int) -> tuple[int, int, int]:
    indices = scene._atom_polydata.GetPointData().GetArray("atom_index")
    colors = scene._atom_polydata.GetPointData().GetArray("display_color")
    for row in range(indices.GetNumberOfTuples()):
        if int(indices.GetValue(row)) == atom_index:
            return tuple(int(value) for value in colors.GetTuple3(row))
    raise AssertionError(f"displayed atom {atom_index} was not found")


def _view_dialog_driver(preview, result, observe):
    class DrivenViewDialog:
        def __init__(self, preferences, elements, parent=None):
            self.initial_preferences = preferences
            self.elements = elements
            self.parent = parent
            self.preview_preferences_changed = _CallbackSignal()

        def exec(self):
            self.preview_preferences_changed.emit(preview)
            observe(self)
            return result

        def selected_preferences(self):
            return preview

    return DrivenViewDialog


def _bond_dialog_driver(preview_values, result, observe):
    class DrivenBondDialog:
        def __init__(self, current_factor, parent=None):
            self.initial_factor = current_factor
            self.parent = parent
            self.preview_factor_changed = _CallbackSignal()

        def exec(self):
            for factor in preview_values:
                self.preview_factor_changed.emit(factor)
                observe(self, factor)
            return result

        def selected_factor(self):
            return preview_values[-1]

    return DrivenBondDialog


class UiR6R1DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_numeric_controls_emit_each_value_change_without_acceptance(self) -> None:
        view_dialog = ViewSettingsDialog(ViewPreferences(), ("C",))
        bond_dialog = BondDetectionDialog(1.10)
        view_previews = []
        bond_previews = []
        view_dialog.preview_preferences_changed.connect(view_previews.append)
        bond_dialog.preview_factor_changed.connect(bond_previews.append)
        try:
            view_dialog.show()
            bond_dialog.show()
            self.application.processEvents()
            thickness = view_dialog.findChild(
                QDoubleSpinBox,
                "bondThicknessScale",
            )
            factor = bond_dialog.findChild(
                QDoubleSpinBox,
                "bondThresholdFactor",
            )

            for value in (1.05, 1.10, 1.15):
                thickness.setValue(value)
                self.application.processEvents()
                self.assertTrue(view_dialog.isVisible())
                self.assertEqual(view_previews[-1].bond_thickness_scale, value)
            for value in (1.20, 1.30):
                factor.setValue(value)
                self.application.processEvents()
                self.assertTrue(bond_dialog.isVisible())
                self.assertEqual(bond_previews[-1], value)

            self.assertTrue(thickness.keyboardTracking())
            self.assertTrue(factor.keyboardTracking())
        finally:
            view_dialog.close()
            bond_dialog.close()
            view_dialog.deleteLater()
            bond_dialog.deleteLater()

    def test_qcolor_cancel_rolls_back_nested_preview_and_accept_keeps_it(self) -> None:
        class FakeColorDialog:
            preview_colors = ()
            result = QDialog.DialogCode.Rejected

            def __init__(self, initial, parent=None):
                self.initial = initial
                self.parent = parent
                self.currentColorChanged = _CallbackSignal()
                self._selected = initial

            def setWindowTitle(self, title):
                self.title = title

            def exec(self):
                for color in self.preview_colors:
                    self._selected = QColor(*color)
                    self.currentColorChanged.emit(self._selected)
                return self.result

            def selectedColor(self):
                return self._selected

        dialog = ViewSettingsDialog(ViewPreferences(), ("C", "N"))
        previews = []
        dialog.preview_preferences_changed.connect(previews.append)
        try:
            FakeColorDialog.preview_colors = ((255, 0, 0), (0, 0, 255))
            FakeColorDialog.result = QDialog.DialogCode.Rejected
            with patch(
                "moltage.gui.view_settings_dialog.QColorDialog",
                FakeColorDialog,
            ):
                dialog._choose_element_color("C")

            self.assertEqual(
                previews[-3].element_color_overrides["C"],
                (255, 0, 0),
            )
            self.assertEqual(
                previews[-2].element_color_overrides["C"],
                (0, 0, 255),
            )
            self.assertEqual(dict(previews[-1].element_color_overrides), {})
            self.assertEqual(
                dialog._display_color("C"),
                ELEMENT_COLORS_RGB["C"],
            )

            FakeColorDialog.preview_colors = ((12, 34, 56),)
            FakeColorDialog.result = QDialog.DialogCode.Accepted
            with patch(
                "moltage.gui.view_settings_dialog.QColorDialog",
                FakeColorDialog,
            ):
                dialog._choose_element_color("C")

            self.assertEqual(
                dialog.selected_preferences().element_color_overrides["C"],
                (12, 34, 56),
            )
            dialog._reset_element_color("C")
            self.assertEqual(dict(previews[-1].element_color_overrides), {})
            dialog._set_element_color("C", (1, 2, 3))
            dialog._set_element_color("N", (4, 5, 6))
            dialog._reset_all_element_colors()
            self.assertEqual(dict(previews[-1].element_color_overrides), {})
        finally:
            dialog.close()
            dialog.deleteLater()


class UiR6R1MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
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
        gc.collect()

    def _copy_xyz(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(REFERENCE_XYZ_PATH.read_bytes())
        return path

    def _threshold_xyz(self, name: str = "threshold.xyz") -> Path:
        path = self.root / name
        path.write_text(
            "2\nthreshold-sensitive pair\n"
            "C 0.000 0.000 0.000\n"
            "C 1.750 0.000 0.000\n",
            encoding="utf-8",
        )
        return path

    def test_thickness_and_colors_preview_all_geometry_tabs_and_cancel_exactly(self):
        workspace_a = self.window._open_local_geometry(self._copy_xyz("a.xyz"))
        workspace_b = self.window._open_local_geometry(self._copy_xyz("b.xyz"))
        committed = self.window._view_preferences
        structures = (workspace_a.structure, workspace_b.structure)
        connectivities = (workspace_a.connectivity, workspace_b.connectivity)
        identities = (workspace_a.identity, workspace_b.identity)
        cameras = (
            workspace_a.viewer._renderer.GetActiveCamera(),
            workspace_b.viewer._renderer.GetActiveCamera(),
        )
        cameras[0].SetPosition(11.0, 2.0, 3.0)
        cameras[1].SetPosition(21.0, 4.0, 5.0)
        camera_positions = tuple(camera.GetPosition() for camera in cameras)
        measurement_session = workspace_a.measurement_session
        measurement_session.add_distance(workspace_a.structure, 0, 1)
        preview = ViewPreferences(
            bond_thickness_scale=1.50,
            element_color_overrides={"C": (10, 20, 30)},
        )
        observed = []

        def observe(_dialog) -> None:
            observed.append(self.window._view_preferences)
            for workspace in (workspace_a, workspace_b):
                scene = workspace.viewer._scene
                self.assertAlmostEqual(
                    scene._tube_filter.GetRadius(),
                    BOND_TUBE_RADIUS * 1.50,
                )
                carbon_index = next(
                    atom.index for atom in workspace.structure if atom.element == "C"
                )
                self.assertEqual(
                    _display_color(scene, carbon_index),
                    (10, 20, 30),
                )
            self.assertEqual(
                tuple(camera.GetPosition() for camera in cameras),
                camera_positions,
            )
            self.assertEqual(len(measurement_session.measurements), 1)

        driven = _view_dialog_driver(
            preview,
            QDialog.DialogCode.Rejected,
            observe,
        )
        with patch("tools.molecule_viewer_demo.ViewSettingsDialog", driven):
            self.window._open_view_settings()

        self.assertEqual(observed, [preview])
        self.assertIs(self.window._view_preferences, committed)
        self.assertEqual(
            (workspace_a.structure, workspace_b.structure),
            structures,
        )
        self.assertEqual(
            (workspace_a.connectivity, workspace_b.connectivity),
            connectivities,
        )
        self.assertEqual((workspace_a.identity, workspace_b.identity), identities)
        self.assertEqual(
            tuple(camera.GetPosition() for camera in cameras),
            camera_positions,
        )
        self.assertEqual(len(measurement_session.measurements), 1)
        for workspace in (workspace_a, workspace_b):
            scene = workspace.viewer._scene
            self.assertAlmostEqual(scene._tube_filter.GetRadius(), BOND_TUBE_RADIUS)
            carbon_index = next(
                atom.index for atom in workspace.structure if atom.element == "C"
            )
            self.assertEqual(
                _display_color(scene, carbon_index),
                ELEMENT_COLORS_RGB["C"],
            )

    def test_view_settings_ok_keeps_current_preview_and_reopens_from_it(self) -> None:
        workspace = self.window._open_local_geometry(self._copy_xyz("ok.xyz"))
        preview = ViewPreferences(
            bond_thickness_scale=2.00,
            element_color_overrides={"C": (90, 80, 70)},
        )

        driven = _view_dialog_driver(
            preview,
            QDialog.DialogCode.Accepted,
            lambda _dialog: None,
        )
        with patch("tools.molecule_viewer_demo.ViewSettingsDialog", driven):
            self.window._open_view_settings()

        self.assertEqual(self.window._view_preferences, preview)
        self.assertAlmostEqual(
            workspace.viewer._scene._tube_filter.GetRadius(),
            BOND_TUBE_RADIUS * 2.00,
        )
        reopened = ViewSettingsDialog(self.window._view_preferences, ("C",))
        try:
            self.assertEqual(
                reopened.selected_preferences().element_color_overrides["C"],
                (90, 80, 70),
            )
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_mol_strands_preview_thickness_and_color_then_cancel_unchanged(self):
        workspace = self.window._open_local_geometry(MIXED_MOL_PATH)
        scene = workspace.viewer._scene
        connectivity = workspace.connectivity
        orders = workspace.bond_display_orders
        line_count = scene._bond_polydata.GetNumberOfLines()
        preview = ViewPreferences(
            bond_thickness_scale=0.50,
            element_color_overrides={"C": (5, 15, 25)},
        )

        def observe(_dialog) -> None:
            self.assertAlmostEqual(
                scene._tube_filter.GetRadius(),
                BOND_TUBE_RADIUS * 0.50,
            )
            self.assertEqual(scene._bond_polydata.GetNumberOfLines(), line_count)
            self.assertEqual(_display_color(scene, 0), (5, 15, 25))
            self.assertIs(workspace.connectivity, connectivity)
            self.assertIs(workspace.bond_display_orders, orders)
            self.assertEqual(
                tuple(record.order for record in workspace.bond_display_orders),
                (1, 2, 3),
            )

        driven = _view_dialog_driver(
            preview,
            QDialog.DialogCode.Rejected,
            observe,
        )
        with patch("tools.molecule_viewer_demo.ViewSettingsDialog", driven):
            self.window._open_view_settings()

        self.assertAlmostEqual(scene._tube_filter.GetRadius(), BOND_TUBE_RADIUS)
        self.assertEqual(scene._bond_polydata.GetNumberOfLines(), line_count)
        self.assertEqual(_display_color(scene, 0), ELEMENT_COLORS_RGB["C"])
        self.assertIs(workspace.connectivity, connectivity)
        self.assertIs(workspace.bond_display_orders, orders)

    def test_xyz_bond_preview_cancel_restores_exact_graph_and_viewer_state(self):
        workspace = self.window._open_local_geometry(self._threshold_xyz())
        self.assertIs(
            workspace.connectivity_source,
            ConnectivitySource.INFERRED,
        )
        self.assertEqual(len(workspace.connectivity), 0)
        original_connectivity = workspace.connectivity
        original_source_connectivity = workspace.source_connectivity
        original_structure = workspace.structure
        original_geometry = render_geometry_in(original_structure)
        measurement_session = workspace.measurement_session
        measurement_session.add_distance(original_structure, 0, 1)
        self.window._sync_measurement_view()
        rendered_measurements = (
            workspace.viewer._scene._rendered_measurement_ids
        )
        camera = workspace.viewer._renderer.GetActiveCamera()
        camera.SetPosition(8.0, 4.0, 2.0)
        camera_position = camera.GetPosition()
        workspace.picked_atom_label.setText("Selected atom: C1")
        observations = []

        def observe(_dialog, factor) -> None:
            observations.append(factor)
            self.assertEqual(self.window._connectivity_multiplier, factor)
            self.assertEqual(len(workspace.connectivity), 1)
            self.assertEqual(
                workspace.viewer._scene._bond_polydata.GetNumberOfLines(),
                1,
            )
            self.assertIs(workspace.structure, original_structure)
            self.assertEqual(render_geometry_in(workspace.structure), original_geometry)
            self.assertEqual(camera.GetPosition(), camera_position)
            self.assertEqual(len(measurement_session.measurements), 1)
            self.assertEqual(
                workspace.viewer._scene._rendered_measurement_ids,
                rendered_measurements,
            )
            self.assertEqual(workspace.picked_atom_label.text(), "Selected atom: C1")

        driven = _bond_dialog_driver(
            (1.20,),
            QDialog.DialogCode.Rejected,
            observe,
        )
        with patch("tools.molecule_viewer_demo.BondDetectionDialog", driven):
            self.window._open_bond_detection()

        self.assertEqual(observations, [1.20])
        self.assertEqual(self.window._connectivity_multiplier, 1.10)
        self.assertIs(workspace.connectivity, original_connectivity)
        self.assertIs(workspace.source_connectivity, original_source_connectivity)
        self.assertEqual(len(workspace.connectivity), 0)
        self.assertEqual(
            workspace.viewer._scene._bond_polydata.GetNumberOfLines(),
            0,
        )
        self.assertIs(workspace.structure, original_structure)
        self.assertEqual(render_geometry_in(workspace.structure), original_geometry)
        self.assertEqual(camera.GetPosition(), camera_position)
        self.assertEqual(len(measurement_session.measurements), 1)
        self.assertEqual(
            workspace.viewer._scene._rendered_measurement_ids,
            rendered_measurements,
        )
        self.assertEqual(workspace.picked_atom_label.text(), "Selected atom: C1")

    def test_xyz_bond_preview_ok_commits_current_factor_and_graph(self) -> None:
        workspace = self.window._open_local_geometry(self._threshold_xyz())
        structure = workspace.structure
        geometry = render_geometry_in(structure)
        driven = _bond_dialog_driver(
            (1.20,),
            QDialog.DialogCode.Accepted,
            lambda _dialog, _factor: self.assertEqual(
                len(workspace.connectivity),
                1,
            ),
        )

        with patch("tools.molecule_viewer_demo.BondDetectionDialog", driven):
            self.window._open_bond_detection()

        self.assertEqual(self.window._connectivity_multiplier, 1.20)
        self.assertEqual(len(workspace.connectivity), 1)
        self.assertIs(workspace.structure, structure)
        self.assertEqual(render_geometry_in(workspace.structure), geometry)

    def test_bond_preview_propagates_to_every_inferred_geometry_tab(self) -> None:
        workspace_a = self.window._open_local_geometry(
            self._threshold_xyz("threshold_a.xyz")
        )
        workspace_b = self.window._open_local_geometry(
            self._threshold_xyz("threshold_b.xyz")
        )

        def observe(_dialog, _factor) -> None:
            self.assertEqual(len(workspace_a.connectivity), 1)
            self.assertEqual(len(workspace_b.connectivity), 1)
            self.assertEqual(
                workspace_a.viewer._scene._bond_polydata.GetNumberOfLines(),
                1,
            )
            self.assertEqual(
                workspace_b.viewer._scene._bond_polydata.GetNumberOfLines(),
                1,
            )

        driven = _bond_dialog_driver(
            (1.20,),
            QDialog.DialogCode.Rejected,
            observe,
        )
        with patch("tools.molecule_viewer_demo.BondDetectionDialog", driven):
            self.window._open_bond_detection()

        self.assertEqual(len(workspace_a.connectivity), 0)
        self.assertEqual(len(workspace_b.connectivity), 0)

    def test_mol_explicit_graph_orders_and_strands_ignore_all_factor_previews(self):
        workspace = self.window._open_local_geometry(MIXED_MOL_PATH)
        connectivity = workspace.connectivity
        orders = workspace.bond_display_orders
        structure = workspace.structure
        line_count = workspace.viewer._scene._bond_polydata.GetNumberOfLines()

        def observe(_dialog, factor) -> None:
            self.assertEqual(self.window._connectivity_multiplier, factor)
            self.assertIs(workspace.connectivity, connectivity)
            self.assertIs(workspace.structure, structure)
            self.assertIs(workspace.bond_display_orders, orders)
            self.assertEqual(
                tuple(record.order for record in workspace.bond_display_orders),
                (1, 2, 3),
            )
            self.assertEqual(
                workspace.viewer._scene._bond_polydata.GetNumberOfLines(),
                line_count,
            )

        driven = _bond_dialog_driver(
            (0.50, 2.00, 1.30),
            QDialog.DialogCode.Rejected,
            observe,
        )
        with patch("tools.molecule_viewer_demo.BondDetectionDialog", driven):
            self.window._open_bond_detection()

        self.assertEqual(self.window._connectivity_multiplier, 1.10)
        self.assertIs(workspace.connectivity, connectivity)
        self.assertIs(workspace.bond_display_orders, orders)
        self.assertEqual(
            workspace.viewer._scene._bond_polydata.GetNumberOfLines(),
            line_count,
        )


if __name__ == "__main__":
    unittest.main()
