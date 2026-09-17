import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDoubleSpinBox
from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow

from moltage.aims.geometry_writer import render_geometry_in
from moltage.domain.bond_display import BondDisplayOrder, ConnectivitySource
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.view_settings_dialog import ViewSettingsDialog
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.vdw_radii import load_default_vdw_radii
from moltage.visualization.molecule_scene import (
    BOND_TUBE_RADIUS,
    ELEMENT_COLORS_RGB,
    DistanceAnnotation,
    MeasurementOverlayAnnotation,
    MoleculeScene,
    PreviewAtom,
    TorsionGizmo,
    _display_point,
)
from moltage.visualization.view_preferences import ViewPreferences
from tools.molecule_viewer_demo import MoleculeViewerDemo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


def _display_colors_by_atom(scene: MoleculeScene):
    indices = scene._atom_polydata.GetPointData().GetArray("atom_index")
    colors = scene._atom_polydata.GetPointData().GetArray("display_color")
    return {
        int(indices.GetValue(row)): tuple(
            int(component) for component in colors.GetTuple3(row)
        )
        for row in range(indices.GetNumberOfTuples())
    }


def _bond_ids(scene: MoleculeScene) -> tuple[int, ...]:
    values = scene._bond_polydata.GetCellData().GetArray("bond_index")
    return tuple(
        int(values.GetValue(row)) for row in range(values.GetNumberOfTuples())
    )


class ViewPreferencesTests(unittest.TestCase):
    def test_defaults_are_frozen_viewer_behavior(self) -> None:
        preferences = ViewPreferences()

        self.assertEqual(preferences.bond_thickness_scale, 1.00)
        self.assertEqual(dict(preferences.element_color_overrides), {})
        self.assertFalse(preferences.show_element_labels)
        self.assertFalse(preferences.hide_hydrogen)
        self.assertEqual(
            preferences.display_color("C", ELEMENT_COLORS_RGB["C"]),
            ELEMENT_COLORS_RGB["C"],
        )

    def test_values_are_validated_normalized_and_immutable(self) -> None:
        source = {"au": (1, 2, 3)}
        preferences = ViewPreferences(
            bond_thickness_scale=2,
            element_color_overrides=source,
            show_element_labels=True,
            hide_hydrogen=True,
        )
        source["au"] = (9, 9, 9)

        self.assertEqual(preferences.bond_thickness_scale, 2.0)
        self.assertEqual(dict(preferences.element_color_overrides), {"Au": (1, 2, 3)})
        with self.assertRaises(TypeError):
            preferences.element_color_overrides["C"] = (4, 5, 6)
        for invalid in (0.49, 2.01, float("nan"), True):
            with self.subTest(invalid=invalid), self.assertRaises(
                (TypeError, ValueError)
            ):
                ViewPreferences(bond_thickness_scale=invalid)


class ViewSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_compact_controls_union_open_elements_and_active_overrides(self) -> None:
        preferences = ViewPreferences(element_color_overrides={"Au": (1, 2, 3)})
        dialog = ViewSettingsDialog(preferences, ("H", "C"))
        try:
            thickness = dialog.findChild(QDoubleSpinBox, "bondThicknessScale")
            labels = dialog.findChild(QCheckBox, "showElementSymbols")
            hide_h = dialog.findChild(QCheckBox, "hideHydrogenAtoms")
            self.assertEqual(dialog.windowTitle(), "View Settings")
            self.assertEqual(dialog._elements, ("H", "C", "Au"))
            self.assertEqual(thickness.minimum(), 0.50)
            self.assertEqual(thickness.maximum(), 2.00)
            self.assertEqual(thickness.decimals(), 2)
            self.assertAlmostEqual(thickness.singleStep(), 0.05)
            self.assertEqual(thickness.value(), 1.00)
            self.assertFalse(labels.isChecked())
            self.assertFalse(hide_h.isChecked())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_per_element_override_reset_and_reset_all_use_frozen_palette(self) -> None:
        dialog = ViewSettingsDialog(ViewPreferences(), ("C", "N"))
        try:
            dialog._set_element_color("C", (10, 20, 30))
            dialog._set_element_color("N", (40, 50, 60))
            self.assertEqual(
                dict(dialog.selected_preferences().element_color_overrides),
                {"C": (10, 20, 30), "N": (40, 50, 60)},
            )
            dialog._reset_element_color("C")
            self.assertEqual(dialog._display_color("C"), ELEMENT_COLORS_RGB["C"])
            self.assertEqual(
                dict(dialog.selected_preferences().element_color_overrides),
                {"N": (40, 50, 60)},
            )
            dialog._reset_all_element_colors()
            self.assertEqual(
                dict(dialog.selected_preferences().element_color_overrides),
                {},
            )
            self.assertEqual(dialog._display_color("N"), ELEMENT_COLORS_RGB["N"])
        finally:
            dialog.close()
            dialog.deleteLater()


class ViewPreferenceSceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = MolecularStructure(
            (
                Atom(0, "H", -1.0, 0.0, 0.0),
                Atom(1, "C", 0.0, 0.0, 0.0),
                Atom(2, "C", 1.4, 0.0, 0.0),
                Atom(3, "N", 2.7, 0.0, 0.0),
            )
        )
        self.connectivity = Connectivity(
            4,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.4),
                Bond(2, 3, 1.3),
            ),
        )
        self.orders = (
            BondDisplayOrder(0, 1, 1),
            BondDisplayOrder(1, 2, 2),
            BondDisplayOrder(2, 3, 3),
        )
        self.renderer = vtkRenderer()
        self.scene = MoleculeScene(self.renderer)
        self.scene.set_molecule(
            self.structure,
            self.connectivity,
            {"H": 0.31, "C": 0.76, "N": 0.71},
            self.orders,
        )

    def test_thickness_scales_every_strand_without_changing_offsets_or_metadata(self):
        baseline_points = tuple(
            self.scene._bond_polydata.GetPoint(index)
            for index in range(self.scene._bond_polydata.GetNumberOfPoints())
        )
        self.assertAlmostEqual(self.scene._tube_filter.GetRadius(), BOND_TUBE_RADIUS)
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 6)

        self.scene.set_view_preferences(ViewPreferences(bond_thickness_scale=2.0))

        self.assertAlmostEqual(
            self.scene._tube_filter.GetRadius(),
            BOND_TUBE_RADIUS * 2.0,
        )
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 6)
        self.assertEqual(self.scene._bond_display_orders, self.orders)
        self.assertEqual(self.scene._bond_edges, ((0, 1), (1, 2), (2, 3)))
        self.assertEqual(
            tuple(
                self.scene._bond_polydata.GetPoint(index)
                for index in range(self.scene._bond_polydata.GetNumberOfPoints())
            ),
            baseline_points,
        )

    def test_element_override_updates_all_matching_atoms_and_reset_restores_default(self):
        self.scene.set_view_preferences(
            ViewPreferences(element_color_overrides={"C": (12, 34, 56)})
        )
        colors = _display_colors_by_atom(self.scene)
        self.assertEqual(colors[1], (12, 34, 56))
        self.assertEqual(colors[2], (12, 34, 56))
        self.assertEqual(colors[0], ELEMENT_COLORS_RGB["H"])
        self.assertEqual(colors[3], ELEMENT_COLORS_RGB["N"])

        self.scene.set_view_preferences(ViewPreferences())

        self.assertEqual(
            _display_colors_by_atom(self.scene),
            {
                index: ELEMENT_COLORS_RGB[atom.element]
                for index, atom in enumerate(self.structure)
            },
        )

    def test_labels_follow_visible_atoms_are_unpickable_and_restore(self) -> None:
        self.assertEqual(self.scene._element_label_actors, ())
        self.scene.set_view_preferences(ViewPreferences(show_element_labels=True))
        self.assertEqual(
            tuple(actor.GetInput() for actor in self.scene._element_label_actors),
            ("H", "C", "C", "N"),
        )
        self.assertTrue(
            all(not actor.GetPickable() for actor in self.scene._element_label_actors)
        )
        self.assertEqual(
            tuple(actor.GetPosition() for actor in self.scene._element_label_actors),
            tuple((atom.x, atom.y, atom.z) for atom in self.structure),
        )

        self.scene.set_view_preferences(
            ViewPreferences(show_element_labels=True, hide_hydrogen=True)
        )
        self.assertEqual(
            tuple(actor.GetInput() for actor in self.scene._element_label_actors),
            ("C", "C", "N"),
        )
        selection_ids = self.scene._atom_polydata.GetPointData().GetArray(
            "atom_index"
        )
        self.assertEqual(
            tuple(
                int(selection_ids.GetValue(index))
                for index in range(selection_ids.GetNumberOfTuples())
            ),
            (1, 2, 3),
        )
        self.assertTrue(self.scene._atom_actor.GetPickable())

        self.scene.set_view_preferences(ViewPreferences(show_element_labels=False))
        self.assertEqual(self.scene._element_label_actors, ())

    def test_unpickable_labels_do_not_interfere_with_atom_selection(self) -> None:
        self.scene.set_view_preferences(ViewPreferences(show_element_labels=True))
        self.assertTrue(
            all(not actor.GetPickable() for actor in self.scene._element_label_actors)
        )
        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(600, 400)
        render_window.AddRenderer(self.renderer)
        self.renderer.ResetCamera()
        render_window.Render()
        center = _display_point(self.renderer, (2.7, 0.0, 0.0))
        self.assertIsNotNone(center)

        selected = self.scene.pick_atom_index(round(center[0]), round(center[1]))

        self.assertEqual(selected, 3)
        render_window.RemoveRenderer(self.renderer)

    def test_hide_h_filters_glyph_bond_and_highlight_but_retains_model(self) -> None:
        self.scene.set_highlighted_atom_indices((0, 1), ())
        structure_before = self.structure
        connectivity_before = self.connectivity

        self.scene.set_view_preferences(ViewPreferences(hide_hydrogen=True))

        self.assertEqual(self.scene._atom_polydata.GetNumberOfPoints(), 3)
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 5)
        self.assertEqual(_bond_ids(self.scene), (1, 1, 2, 2, 2))
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 1)
        highlight_ids = self.scene._highlight_polydata.GetPointData().GetArray(
            "atom_index"
        )
        self.assertEqual(int(highlight_ids.GetValue(0)), 1)
        self.assertIs(self.structure, structure_before)
        self.assertIs(self.connectivity, connectivity_before)
        self.assertEqual(tuple(atom.element for atom in self.structure), ("H", "C", "C", "N"))
        self.assertEqual(self.scene._bond_edges, ((0, 1), (1, 2), (2, 3)))
        self.assertEqual(self.scene._bond_display_orders, self.orders)

        self.scene.set_view_preferences(ViewPreferences())

        self.assertEqual(self.scene._atom_polydata.GetNumberOfPoints(), 4)
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 6)
        self.assertEqual(_bond_ids(self.scene), (0, 1, 1, 2, 2, 2))
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 2)

    def test_hidden_h_color_and_measurement_state_survive_until_restored(self) -> None:
        measurement = MeasurementOverlayAnnotation(
            1,
            DistanceAnnotation((-1.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        )
        self.scene.set_measurement_annotations((measurement,))
        self.scene.set_measurement_pick_feedback((0,))
        preferences = ViewPreferences(
            element_color_overrides={"H": (255, 20, 147)},
            hide_hydrogen=True,
        )

        self.scene.set_view_preferences(preferences)

        self.assertNotIn(0, _display_colors_by_atom(self.scene))
        self.assertEqual(self.scene._measurement_annotations, (measurement,))
        self.assertEqual(self.scene._measurement_pick_indices, (0,))
        self.assertEqual(self.scene._annotation_texts, ("1.00 Å",))
        self.assertTrue(
            all(
                not actor.GetVisibility()
                for actor in self.scene._measurement_pick_text_actors
            )
        )

        self.scene.set_view_preferences(
            ViewPreferences(element_color_overrides={"H": (255, 20, 147)})
        )
        self.assertEqual(_display_colors_by_atom(self.scene)[0], (255, 20, 147))
        self.assertEqual(self.scene._measurement_annotations, (measurement,))
        self.assertTrue(self.scene._measurement_pick_text_actors[0].GetVisibility())

    def test_hidden_h_torsion_visual_is_suppressed_without_clearing_edit_state(self):
        reference = self.scene.reference_direction_for_bond(0, 1)
        gizmo = TorsionGizmo(0, 1, reference, 20.0)
        self.scene.set_torsion_gizmo(gizmo)
        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 1)

        self.scene.set_view_preferences(ViewPreferences(hide_hydrogen=True))

        self.assertEqual(self.scene._torsion_gizmo, gizmo)
        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 0)
        self.assertFalse(self.scene._torsion_angle_text_actor.GetVisibility())

        self.scene.set_view_preferences(ViewPreferences())
        self.assertEqual(self.scene._torsion_gizmo, gizmo)
        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 1)
        self.assertTrue(self.scene._torsion_angle_text_actor.GetVisibility())
        self.assertTrue(self.scene._torsion_handle_actor.GetVisibility())

    def test_coordinate_update_moves_labels_without_camera_change(self) -> None:
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(4.0, 5.0, 6.0)
        self.scene.set_view_preferences(ViewPreferences(show_element_labels=True))
        translated = MolecularStructure(
            tuple(
                Atom(atom.index, atom.element, atom.x, atom.y + 2.0, atom.z)
                for atom in self.structure
            )
        )

        self.scene.update_molecule_coordinates(translated)

        self.assertEqual(camera.GetPosition(), (4.0, 5.0, 6.0))
        self.assertEqual(
            tuple(actor.GetPosition() for actor in self.scene._element_label_actors),
            tuple((atom.x, atom.y, atom.z) for atom in translated),
        )

    def test_preview_au_uses_override_without_changing_preview_coordinates(self):
        preview = PreviewAtom((5.0, 6.0, 7.0), 0.6, ELEMENT_COLORS_RGB["Au"], "Au")
        self.scene.set_preview_atoms((preview,))

        self.scene.set_view_preferences(
            ViewPreferences(element_color_overrides={"Au": (10, 20, 30)})
        )

        colors = self.scene._preview_polydata.GetPointData().GetArray(
            "preview_color"
        )
        self.assertEqual(tuple(map(int, colors.GetTuple3(0))), (10, 20, 30))
        self.assertEqual(self.scene._preview_polydata.GetPoint(0), preview.coordinates)
        self.assertEqual(self.scene._preview_atoms, (preview,))


class ViewSettingsMainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MoleculeViewerDemo()
        self.window.resize(900, 650)
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        gc.collect()

    def test_menu_toolbar_icon_order_and_one_logical_label_state(self) -> None:
        self.assertEqual(
            [action.text() for action in self.window._settings_menu.actions()],
            ["View...", "Bond Detection...", "Email Notifications..."],
        )
        visible_actions = [
            action
            for action in self.window._main_toolbar.actions()
            if action.isVisible()
        ]
        self.assertEqual(
            [None if action.isSeparator() else action.text() for action in visible_actions],
            [
                "Undo",
                "Redo",
                None,
                "Reset View",
                "Element Labels",
                None,
                "Distance",
                "Angle",
                "Rotate Bond",
                "Delete Atom",
                "Replace Atom",
                None,
                "Electrode Builder",
            ],
        )
        action = self.window._element_labels_action
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())
        self.assertTrue(action.isEnabled())
        self.assertEqual(action.toolTip(), "Show/Hide Element Labels")
        self.assertFalse(action.icon().isNull())
        icon_text = (PROJECT_ROOT / "resources" / "icons" / "element_labels.svg").read_text(
            encoding="utf-8"
        )
        self.assertIn("<circle", icon_text)
        self.assertIn("#30343b", icon_text)
        self.assertNotIn("gradient", icon_text.lower())

        action.trigger()
        self.application.processEvents()
        self.assertTrue(action.isChecked())
        self.assertTrue(self.window._view_preferences.show_element_labels)
        self.assertTrue(
            self.window._viewer._scene._view_preferences.show_element_labels
        )

        dialog = ViewSettingsDialog(
            self.window._view_preferences,
            (),
            self.window,
        )
        try:
            self.assertTrue(
                dialog.findChild(QCheckBox, "showElementSymbols").isChecked()
            )
        finally:
            dialog.close()
            dialog.deleteLater()

        action.trigger()
        self.application.processEvents()
        self.assertFalse(action.isChecked())
        self.assertFalse(self.window._view_preferences.show_element_labels)
        reflected = ViewSettingsDialog(
            self.window._view_preferences,
            (),
            self.window,
        )
        try:
            self.assertFalse(
                reflected.findChild(QCheckBox, "showElementSymbols").isChecked()
            )
        finally:
            reflected.close()
            reflected.deleteLater()

    def test_accepted_dialog_updates_action_and_cancel_changes_nothing(self) -> None:
        accepted = ViewPreferences(
            bond_thickness_scale=1.5,
            element_color_overrides={"C": (1, 2, 3)},
            show_element_labels=True,
            hide_hydrogen=True,
        )

        class AcceptedDialog:
            def __init__(inner_self, preferences, elements, parent=None):
                self.assertIs(preferences, self.window._view_preferences)
                self.assertEqual(elements, set())
                self.assertIs(parent, self.window)
                inner_self._preview_callback = None
                inner_self.preview_preferences_changed = inner_self

            def connect(inner_self, callback):
                inner_self._preview_callback = callback

            def exec(inner_self):
                inner_self._preview_callback(accepted)
                return QDialog.DialogCode.Accepted

            def selected_preferences(inner_self):
                return accepted

        with patch(
            "tools.molecule_viewer_demo.ViewSettingsDialog",
            AcceptedDialog,
        ):
            self.window._view_settings_action.trigger()
        self.assertIs(self.window._view_preferences, accepted)
        self.assertTrue(self.window._element_labels_action.isChecked())

        class RejectedDialog(AcceptedDialog):
            def exec(inner_self):
                inner_self._preview_callback(ViewPreferences())
                return QDialog.DialogCode.Rejected

        with patch(
            "tools.molecule_viewer_demo.ViewSettingsDialog",
            RejectedDialog,
        ):
            self.window._view_settings_action.trigger()
        self.assertIs(self.window._view_preferences, accepted)

    def test_all_open_and_future_geometry_workspaces_share_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path_a = root / "a.xyz"
            path_b = root / "b.xyz"
            path_a.write_bytes(REFERENCE_XYZ_PATH.read_bytes())
            path_b.write_bytes(REFERENCE_XYZ_PATH.read_bytes())
            workspace_a = self.window._open_local_geometry(path_a)
            workspace_b = self.window._open_local_geometry(path_b)
            structures = (workspace_a.structure, workspace_b.structure)
            connectivities = (workspace_a.connectivity, workspace_b.connectivity)
            cameras = (
                workspace_a.viewer._renderer.GetActiveCamera(),
                workspace_b.viewer._renderer.GetActiveCamera(),
            )
            cameras[0].SetPosition(10.0, 1.0, 2.0)
            cameras[1].SetPosition(20.0, 3.0, 4.0)
            camera_positions = tuple(camera.GetPosition() for camera in cameras)
            preferences = ViewPreferences(
                bond_thickness_scale=0.5,
                element_color_overrides={"C": (12, 34, 56)},
                show_element_labels=True,
                hide_hydrogen=True,
            )

            self.window._apply_view_preferences(preferences)

            for workspace in (workspace_a, workspace_b):
                self.assertIs(workspace.viewer._scene._view_preferences, preferences)
                self.assertNotIn(
                    "H",
                    tuple(
                        actor.GetInput()
                        for actor in workspace.viewer._scene._element_label_actors
                    ),
                )
            self.assertEqual(
                tuple(camera.GetPosition() for camera in cameras),
                camera_positions,
            )
            self.assertEqual((workspace_a.structure, workspace_b.structure), structures)
            self.assertEqual(
                (workspace_a.connectivity, workspace_b.connectivity),
                connectivities,
            )

            workspace_c = self.window._create_geometry_workspace(
                identity=None,
                display_title="Geometry C",
                select=False,
            )
            self.assertIs(workspace_c.viewer._scene._view_preferences, preferences)

    def test_extreme_view_preferences_do_not_change_geometry_serialization(self) -> None:
        workspace = self.window._open_local_geometry(REFERENCE_XYZ_PATH)
        structure = workspace.structure
        connectivity = workspace.connectivity
        orders = workspace.bond_display_orders
        before = render_geometry_in(structure)

        self.window._apply_view_preferences(
            ViewPreferences(
                bond_thickness_scale=2.0,
                element_color_overrides={
                    "C": (255, 0, 255),
                    "H": (0, 255, 0),
                    "S": (0, 0, 0),
                },
                show_element_labels=True,
                hide_hydrogen=True,
            )
        )

        self.assertIs(workspace.structure, structure)
        self.assertIs(workspace.connectivity, connectivity)
        self.assertIs(workspace.bond_display_orders, orders)
        self.assertEqual(render_geometry_in(workspace.structure), before)

    def test_reset_view_changes_camera_only_and_keeps_preferences(self) -> None:
        workspace = self.window._open_local_geometry(REFERENCE_XYZ_PATH)
        preferences = ViewPreferences(
            bond_thickness_scale=1.5,
            element_color_overrides={"C": (9, 8, 7)},
            show_element_labels=True,
            hide_hydrogen=True,
        )
        self.window._apply_view_preferences(preferences)
        self.window._route_active_workspace()

        with patch.object(workspace.viewer, "reset_camera") as reset_camera:
            self.window._reset_view_action.trigger()

        reset_camera.assert_called_once_with()
        self.assertIs(self.window._view_preferences, preferences)
        self.assertIs(workspace.viewer._scene._view_preferences, preferences)

    def test_hide_h_round_trip_preserves_explicit_mol_graph_and_order(self) -> None:
        mol_text = (
            "C-H explicit\n"
            "  Moltage\n"
            "\n"
            "  2  1  0  0  0  0            999 V2000\n"
            "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "    1.0900    0.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "  1  2  1  0  0  0  0\n"
            "M  END\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c_h.mol"
            path.write_text(mol_text, encoding="utf-8")
            workspace = self.window._open_local_geometry(path)
            connectivity = workspace.connectivity
            orders = workspace.bond_display_orders
            scene = workspace.viewer._scene
            self.assertIs(
                workspace.connectivity_source,
                ConnectivitySource.EXPLICIT,
            )
            self.assertEqual(scene._atom_polydata.GetNumberOfPoints(), 2)
            self.assertEqual(scene._bond_polydata.GetNumberOfLines(), 1)

            self.window._apply_view_preferences(
                ViewPreferences(hide_hydrogen=True)
            )

            self.assertEqual(scene._atom_polydata.GetNumberOfPoints(), 1)
            self.assertEqual(scene._bond_polydata.GetNumberOfLines(), 0)
            self.assertIs(workspace.connectivity, connectivity)
            self.assertIs(workspace.bond_display_orders, orders)
            self.assertEqual(tuple(atom.element for atom in workspace.structure), ("C", "H"))

            self.window._apply_view_preferences(ViewPreferences())

            self.assertEqual(scene._atom_polydata.GetNumberOfPoints(), 2)
            self.assertEqual(scene._bond_polydata.GetNumberOfLines(), 1)
            self.assertIs(workspace.connectivity, connectivity)
            self.assertIs(workspace.bond_display_orders, orders)

    def test_sh_contact_application_is_identical_with_h_visible_or_hidden(self) -> None:
        def run(hide_hydrogen: bool):
            window = MoleculeViewerDemo()
            try:
                structure = MolecularStructure(
                    (
                        Atom(0, "C", 1.0, 0.0, 0.0),
                        Atom(1, "S", 0.0, 0.0, 0.0),
                        Atom(2, "H", -0.5, 1.0, 0.0),
                    )
                )
                connectivity = Connectivity(
                    3,
                    (Bond(0, 1, 1.0), Bond(1, 2, 5.0**0.5 / 2.0)),
                )
                anchors = detect_anchors(structure, connectivity)
                radii = load_default_covalent_radii()
                window._apply_view_preferences(
                    ViewPreferences(hide_hydrogen=hide_hydrogen)
                )
                window._source_structure = structure
                window._source_connectivity = connectivity
                window._structure = structure
                window._connectivity = connectivity
                window._covalent_radii = radii
                window._vdw_radii = load_default_vdw_radii()
                window._anchors = anchors
                window._viewer.set_molecule(structure, connectivity, radii)
                window._rebuild_anchor_site_controls()
                controls = window._site_controls[anchors[0]]
                controls.checkbox.setChecked(True)
                self.application.processEvents()
                proposal = window._current_proposals[0]
                self.assertEqual(proposal.remove_atom_indices, (2,))
                window._done_button.click()
                self.application.processEvents()
                return (
                    window._structure,
                    window._connectivity,
                    window._applied_result.removed_atom_indices,
                    window._applied_result.added_au_indices,
                )
            finally:
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.application.processEvents()

        visible_result = run(False)
        hidden_result = run(True)

        self.assertEqual(hidden_result, visible_result)
        self.assertEqual(
            tuple(atom.element for atom in hidden_result[0]),
            ("C", "S", "Au"),
        )
        self.assertEqual(hidden_result[2], (2,))


if __name__ == "__main__":
    unittest.main()
