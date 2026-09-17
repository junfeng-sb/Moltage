import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QInputDialog,
    QPushButton,
    QGroupBox,
    QTabWidget,
)

from moltage.structure.cube import CubeCoordinateUnit
from moltage.app.user_view_preferences import (
    PersistedOrbitalLighting,
    UserViewPreferencesRepository,
)
from moltage.gui.view_settings_dialog import ViewSettingsDialog
from moltage.gui.view_export import ViewExportRequest
from moltage.visualization.orbital_surface import (
    DEFAULT_ORBITAL_AMBIENT,
    DEFAULT_ORBITAL_LIGHT_INTENSITY,
    DEFAULT_ORBITAL_SPECULAR,
    DEFAULT_ORBITAL_SHININESS,
    MAXIMUM_ORBITAL_AMBIENT,
    MINIMUM_ORBITAL_AMBIENT,
    ORBITAL_AMBIENT_STEP,
    ORBITAL_ISOVALUE_STEP,
    MAXIMUM_ORBITAL_LIGHT_INTENSITY,
    MINIMUM_ORBITAL_LIGHT_INTENSITY,
    ORBITAL_LIGHT_INTENSITY_STEP,
    OrbitalSurfacePreferences,
    OrbitalSurfaceResolution,
    OrbitalSurfaceStyle,
)
from moltage.visualization.view_preferences import ViewPreferences
from tools.molecule_viewer_demo import MoleculeViewerDemo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "cube" / "signed_orbital.cube"
REFERENCE_XYZ = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


class CubeViewSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_orbital_controls_appear_only_when_cube_state_is_supplied(self) -> None:
        ordinary = ViewSettingsDialog(ViewPreferences(), ("C",))
        cube = ViewSettingsDialog(
            ViewPreferences(),
            ("C",),
            orbital_preferences=OrbitalSurfacePreferences(),
            orbital_maximum_isovalue=0.06,
        )
        try:
            ordinary_tabs = ordinary.findChild(QTabWidget, "viewSettingsTabs")
            cube_tabs = cube.findChild(QTabWidget, "viewSettingsTabs")
            self.assertEqual(ordinary_tabs.count(), 1)
            self.assertEqual(ordinary_tabs.tabText(0), "Molecule")
            self.assertEqual(cube_tabs.count(), 2)
            self.assertEqual(
                tuple(cube_tabs.tabText(index) for index in range(2)),
                ("Molecule", "Isosurface"),
            )
            self.assertEqual(cube_tabs.widget(1).objectName(), "orbitalSurfaceTab")
            spin = cube.findChild(QDoubleSpinBox, "orbitalIsovalue")
            ambient = cube.findChild(QDoubleSpinBox, "orbitalAmbientLight")
            intensity = cube.findChild(
                QDoubleSpinBox,
                "orbitalLightIntensity",
            )
            style = cube.findChild(QComboBox, "orbitalSurfaceStyle")
            self.assertEqual(spin.minimum(), 0.0)
            self.assertEqual(spin.maximum(), 0.06)
            self.assertEqual(spin.decimals(), 6)
            self.assertEqual(spin.singleStep(), ORBITAL_ISOVALUE_STEP)
            self.assertFalse(spin.keyboardTracking())
            self.assertEqual(style.count(), 2)
            self.assertEqual(ambient.minimum(), MINIMUM_ORBITAL_AMBIENT)
            self.assertEqual(ambient.maximum(), MAXIMUM_ORBITAL_AMBIENT)
            self.assertEqual(ambient.singleStep(), ORBITAL_AMBIENT_STEP)
            self.assertEqual(ambient.value(), DEFAULT_ORBITAL_AMBIENT)
            self.assertEqual(ambient.value(), 0.90)
            self.assertEqual(
                intensity.minimum(),
                MINIMUM_ORBITAL_LIGHT_INTENSITY,
            )
            self.assertEqual(
                intensity.maximum(),
                MAXIMUM_ORBITAL_LIGHT_INTENSITY,
            )
            self.assertEqual(
                intensity.singleStep(),
                ORBITAL_LIGHT_INTENSITY_STEP,
            )
            self.assertEqual(
                intensity.value(),
                DEFAULT_ORBITAL_LIGHT_INTENSITY,
            )
            self.assertEqual(intensity.value(), 0.82)
            specular = cube.findChild(QDoubleSpinBox, "orbitalSpecular")
            shininess = cube.findChild(QDoubleSpinBox, "orbitalShininess")
            self.assertEqual((specular.minimum(), specular.maximum()), (0.0, 1.0))
            self.assertEqual(specular.value(), DEFAULT_ORBITAL_SPECULAR)
            self.assertEqual(specular.singleStep(), 0.05)
            self.assertEqual((shininess.minimum(), shininess.maximum()), (1.0, 128.0))
            self.assertEqual(shininess.value(), DEFAULT_ORBITAL_SHININESS)
            self.assertEqual(shininess.singleStep(), 1.0)
            surface_group = cube.findChild(QGroupBox, "orbitalSurfaceGroup")
            material_group = cube.findChild(QGroupBox, "orbitalMaterialGroup")
            grid = cube_tabs.widget(1).layout()
            self.assertIs(grid.itemAtPosition(0, 0).widget(), surface_group)
            self.assertIs(grid.itemAtPosition(0, 1).widget(), material_group)
        finally:
            ordinary.close()
            cube.close()
            ordinary.deleteLater()
            cube.deleteLater()

    def test_density_view_can_opt_in_to_resolution_control(self) -> None:
        generic = ViewSettingsDialog(
            ViewPreferences(),
            ("C",),
            orbital_preferences=OrbitalSurfacePreferences(),
            orbital_maximum_isovalue=0.06,
        )
        density = ViewSettingsDialog(
            ViewPreferences(),
            ("C",),
            orbital_preferences=OrbitalSurfacePreferences(
                resolution=OrbitalSurfaceResolution.MEDIUM
            ),
            orbital_maximum_isovalue=0.06,
            orbital_resolution_control=True,
        )
        previews = []
        density.preview_orbital_preferences_changed.connect(previews.append)
        try:
            self.assertIsNone(
                generic.findChild(QComboBox, "orbitalSurfaceResolution")
            )
            selector = density.findChild(
                QComboBox,
                "orbitalSurfaceResolution",
            )
            self.assertEqual(selector.count(), 3)
            self.assertIs(
                OrbitalSurfaceResolution(selector.currentData()),
                OrbitalSurfaceResolution.MEDIUM,
            )
            selector.setCurrentIndex(
                selector.findData(OrbitalSurfaceResolution.LOW)
            )
            self.assertIs(
                density.selected_orbital_preferences().resolution,
                OrbitalSurfaceResolution.LOW,
            )
            self.assertIs(previews[-1].resolution, OrbitalSurfaceResolution.LOW)
            self.assertIn("Display only", selector.toolTip())
        finally:
            generic.close()
            density.close()
            generic.deleteLater()
            density.deleteLater()

    def test_each_step_and_lobe_color_change_emits_live_cube_preview(self) -> None:
        dialog = ViewSettingsDialog(
            ViewPreferences(),
            ("C",),
            orbital_preferences=OrbitalSurfacePreferences(),
            orbital_maximum_isovalue=0.06,
        )
        previews = []
        dialog.preview_orbital_preferences_changed.connect(previews.append)
        try:
            spin = dialog.findChild(QDoubleSpinBox, "orbitalIsovalue")
            spin.stepUp()
            self.assertEqual(previews[-1].isovalue, 0.025)
            dialog._set_orbital_color("positive", (10, 20, 30))
            dialog._set_orbital_color("negative", (40, 50, 60))
            ambient = dialog.findChild(QDoubleSpinBox, "orbitalAmbientLight")
            ambient.setValue(0.40)
            intensity = dialog.findChild(
                QDoubleSpinBox,
                "orbitalLightIntensity",
            )
            intensity.setValue(0.65)
            dialog.findChild(QDoubleSpinBox, "orbitalSpecular").setValue(0.60)
            dialog.findChild(QDoubleSpinBox, "orbitalShininess").setValue(48)
            style = dialog.findChild(QComboBox, "orbitalSurfaceStyle")
            style.setCurrentIndex(
                style.findData(OrbitalSurfaceStyle.SEMI_TRANSPARENT)
            )
            selected = dialog.selected_orbital_preferences()
            self.assertEqual(selected.positive_color, (10, 20, 30))
            self.assertEqual(selected.negative_color, (40, 50, 60))
            self.assertEqual(selected.ambient, 0.40)
            self.assertEqual(selected.light_intensity, 0.65)
            self.assertEqual(selected.specular, 0.60)
            self.assertEqual(selected.shininess, 48.0)
            self.assertIs(
                selected.style,
                OrbitalSurfaceStyle.SEMI_TRANSPARENT,
            )
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_enter_commits_numeric_value_without_closing_settings(self) -> None:
        dialog = ViewSettingsDialog(
            ViewPreferences(),
            ("C",),
            orbital_preferences=OrbitalSurfacePreferences(),
            orbital_maximum_isovalue=0.06,
        )
        previews = []
        dialog.preview_orbital_preferences_changed.connect(previews.append)
        try:
            dialog.show()
            self.application.processEvents()
            dialog.findChild(QTabWidget, "viewSettingsTabs").setCurrentIndex(1)
            for name, text, attribute, expected in (
                ("orbitalLightIntensity", "0.35", "light_intensity", 0.35),
                ("orbitalSpecular", "0.55", "specular", 0.55),
                ("orbitalShininess", "48", "shininess", 48.0),
            ):
                with self.subTest(control=name):
                    control = dialog.findChild(QDoubleSpinBox, name)
                    control.setFocus()
                    self.application.processEvents()
                    self.assertIs(self.application.focusWidget(), control)
                    control.lineEdit().selectAll()
                    QTest.keyClicks(control, text)
                    QTest.keyClick(control, Qt.Key.Key_Return)
                    self.application.processEvents()

                    self.assertTrue(dialog.isVisible())
                    self.assertEqual(control.value(), expected)
                    self.assertEqual(getattr(previews[-1], attribute), expected)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_recommended_material_is_explicit_and_emits_one_preview(self) -> None:
        original = OrbitalSurfacePreferences(
            isovalue=0.03, ambient=0.85, light_intensity=0.45,
            positive_color=(10, 20, 30),
            style=OrbitalSurfaceStyle.SEMI_TRANSPARENT,
            specular=0.1, shininess=80,
        )
        dialog = ViewSettingsDialog(
            ViewPreferences(), ("C",), orbital_preferences=original,
            orbital_maximum_isovalue=0.06,
        )
        previews = []
        dialog.preview_orbital_preferences_changed.connect(previews.append)
        try:
            self.assertEqual(dialog.selected_orbital_preferences(), original)
            dialog.findChild(QPushButton, "recommendedOrbitalMaterial").click()
            self.assertEqual(len(previews), 1)
            self.assertEqual(
                previews[0],
                replace(original, ambient=0.55, light_intensity=0.95,
                        specular=0.30, shininess=32.0),
            )
        finally:
            dialog.close()
            dialog.deleteLater()


class CubeWorkspaceTests(unittest.TestCase):
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

    def test_cube_opens_as_read_only_geometry_with_measurement_and_view_enabled(self) -> None:
        workspace = self.window._open_local_geometry(FIXTURE)
        self.application.processEvents()

        self.assertIsNotNone(workspace)
        self.assertTrue(workspace.read_only)
        self.assertIsNotNone(workspace.scalar_field)
        self.assertIsNotNone(workspace.orbital_preferences)
        self.assertTrue(workspace.viewer._scene._orbital_surface.actor.GetVisibility())
        self.assertTrue(workspace.viewer._hover_picking_enabled)
        self.assertFalse(workspace.viewer._hover_bond_picking_enabled)
        workspace.viewer._set_hover_target(("atom", 0))
        self.assertEqual(
            workspace.viewer._scene._atom_highlight_ring_polydata.GetNumberOfLines(),
            1,
        )
        hover_labels = (
            workspace.viewer._scene._hover_atom_label_polydata.GetPointData().GetAbstractArray(
                "atom_index_label"
            )
        )
        self.assertEqual(hover_labels.GetValue(0), "1")
        self.assertTrue(workspace.viewer._scene._orbital_surface.actor.GetVisibility())
        workspace.viewer._set_hover_target(None)
        self.assertTrue(self.window._distance_measure_action.isEnabled())
        self.assertTrue(self.window._angle_measure_action.isEnabled())
        self.assertTrue(self.window._reset_view_action.isEnabled())
        self.assertFalse(self.window._rotate_bond_action.isEnabled())
        self.assertFalse(self.window._delete_atom_action.isEnabled())
        self.assertFalse(self.window._replace_atom_action.isEnabled())
        self.assertFalse(self.window._electrode_builder_action.isEnabled())
        self.assertFalse(self.window._generate_aims_action.isEnabled())
        self.assertFalse(self.window._submit_aims_action.isEnabled())
        self.assertEqual(
            workspace.viewer._scene._primary_highlight_indices,
            (),
        )
        self.assertEqual(
            workspace.viewer._scene._secondary_highlight_indices,
            (),
        )
        self.assertFalse(
            workspace.anchor_highlight_legend.isVisibleTo(self.window)
        )

    def test_cube_capture_preserves_current_surface_state_and_scales_pixels(self) -> None:
        workspace = self.window._open_local_geometry(FIXTURE)
        self.application.processEvents()
        before_preferences = workspace.orbital_preferences
        before_extractions = (
            workspace.viewer._scene._orbital_surface.extraction_count
        )
        base = workspace.viewer.export_pixel_size()

        image = workspace.viewer.capture_image(2)

        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), base.width() * 2)
        self.assertEqual(image.height(), base.height() * 2)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 255)
        self.assertEqual(workspace.orbital_preferences, before_preferences)
        self.assertEqual(
            workspace.viewer._scene._orbital_surface.extraction_count,
            before_extractions,
        )
        self.assertTrue(self.window._export_current_view_action.isEnabled())

    def test_file_export_routes_to_the_active_cube_canvas(self) -> None:
        self.window._open_local_geometry(FIXTURE)
        self.application.processEvents()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cube-current-view.png"
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.selected_request = ViewExportRequest(destination, 1)
            with patch(
                "tools.molecule_viewer_demo.ViewExportDialog",
                return_value=dialog,
            ):
                self.window._export_current_view()

            self.assertTrue(destination.is_file())
            self.assertGreater(destination.stat().st_size, 0)
            dialog.deleteLater.assert_called_once_with()

    def test_anchor_highlights_follow_au_tool_visibility(self) -> None:
        workspace = self.window._open_local_geometry(REFERENCE_XYZ)
        self.application.processEvents()
        initial = (
            workspace.viewer._scene._primary_highlight_indices,
            workspace.viewer._scene._secondary_highlight_indices,
        )
        self.assertTrue(initial[0])
        self.assertTrue(
            workspace.anchor_highlight_legend.isVisibleTo(self.window)
        )

        self.window._electrode_builder_action.trigger()
        self.application.processEvents()
        self.assertEqual(
            (
                workspace.viewer._scene._primary_highlight_indices,
                workspace.viewer._scene._secondary_highlight_indices,
            ),
            ((), ()),
        )
        self.assertFalse(
            workspace.anchor_highlight_legend.isVisibleTo(self.window)
        )

        self.window._electrode_builder_action.trigger()
        self.application.processEvents()
        self.assertEqual(
            (
                workspace.viewer._scene._primary_highlight_indices,
                workspace.viewer._scene._secondary_highlight_indices,
            ),
            initial,
        )
        self.assertTrue(
            workspace.anchor_highlight_legend.isVisibleTo(self.window)
        )

    def test_active_cube_view_dialog_applies_isovalue_and_fixed_transparency(self) -> None:
        workspace = self.window._open_local_geometry(FIXTURE)
        self.application.processEvents()
        surface = workspace.viewer._scene._orbital_surface
        extraction_count = surface.extraction_count

        def drive_dialog() -> None:
            dialog = self.window.findChild(ViewSettingsDialog)
            self.assertIsNotNone(dialog)
            spin = dialog.findChild(QDoubleSpinBox, "orbitalIsovalue")
            ambient = dialog.findChild(QDoubleSpinBox, "orbitalAmbientLight")
            intensity = dialog.findChild(
                QDoubleSpinBox,
                "orbitalLightIntensity",
            )
            style = dialog.findChild(QComboBox, "orbitalSurfaceStyle")
            spin.stepUp()
            ambient.setValue(0.40)
            intensity.setValue(0.65)
            style.setCurrentIndex(
                style.findData(OrbitalSurfaceStyle.SEMI_TRANSPARENT)
            )
            dialog.accept()

        QTimer.singleShot(0, drive_dialog)
        self.window._open_view_settings()

        self.assertEqual(workspace.orbital_preferences.isovalue, 0.025)
        self.assertIs(
            workspace.orbital_preferences.style,
            OrbitalSurfaceStyle.SEMI_TRANSPARENT,
        )
        self.assertEqual(workspace.orbital_preferences.ambient, 0.40)
        self.assertEqual(workspace.orbital_preferences.light_intensity, 0.65)
        self.assertEqual(surface.actor.GetProperty().GetAmbient(), 0.40)
        self.assertEqual(surface.actor.GetProperty().GetDiffuse(), 0.60)
        positive = surface._lookup_table.GetTableValue(1)
        self.assertEqual(
            tuple(round(component * 255) for component in positive[:3]),
            tuple(
                round(component * 0.65)
                for component in workspace.orbital_preferences.positive_color
            ),
        )
        self.assertEqual(surface.extraction_count, extraction_count + 1)

    def test_accepted_lighting_survives_a_new_application_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = UserViewPreferencesRepository(
                Path(directory) / "view_preferences.json"
            )
            first = MoleculeViewerDemo(repository)
            second = None
            try:
                first.show()
                first._open_local_geometry(FIXTURE)

                def accept_lighting() -> None:
                    dialog = first.findChild(ViewSettingsDialog)
                    dialog.findChild(
                        QDoubleSpinBox,
                        "orbitalAmbientLight",
                    ).setValue(0.65)
                    dialog.findChild(
                        QDoubleSpinBox,
                        "orbitalLightIntensity",
                    ).setValue(0.35)
                    dialog.findChild(QDoubleSpinBox, "orbitalSpecular").setValue(0.55)
                    dialog.findChild(QDoubleSpinBox, "orbitalShininess").setValue(48)
                    dialog.accept()

                QTimer.singleShot(0, accept_lighting)
                first._open_view_settings()
                self.assertEqual(
                    repository.load(),
                    PersistedOrbitalLighting(0.65, 0.35, 0.55, 48.0),
                )

                second = MoleculeViewerDemo(repository)
                workspace = second._open_local_geometry(FIXTURE)
                self.assertEqual(workspace.orbital_preferences.ambient, 0.65)
                self.assertEqual(
                    workspace.orbital_preferences.light_intensity,
                    0.35,
                )
                self.assertEqual(workspace.orbital_preferences.specular, 0.55)
                self.assertEqual(workspace.orbital_preferences.shininess, 48.0)

                def cancel_material() -> None:
                    dialog = second.findChild(ViewSettingsDialog)
                    dialog.findChild(QPushButton, "recommendedOrbitalMaterial").click()
                    self.assertEqual(workspace.orbital_preferences.ambient, 0.55)
                    dialog.reject()

                QTimer.singleShot(0, cancel_material)
                second._open_view_settings()
                self.assertEqual(workspace.orbital_preferences.ambient, 0.65)
                self.assertEqual(workspace.orbital_preferences.light_intensity, 0.35)
                self.assertEqual(workspace.orbital_preferences.specular, 0.55)
                self.assertEqual(workspace.orbital_preferences.shininess, 48.0)
                self.assertEqual(repository.load(), PersistedOrbitalLighting(0.65, 0.35, 0.55, 48.0))
            finally:
                first.close()
                first.deleteLater()
                if second is not None:
                    second.close()
                    second.deleteLater()

    def test_legacy_lighting_file_preserves_user_values_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "view_preferences.json"
            text = ('{"schema_version": 1, "orbital_lighting": '
                    '{"ambient": 0.75, "light_intensity": 0.45}}')
            path.write_text(text, encoding="utf-8")
            self.assertEqual(
                UserViewPreferencesRepository(path).load(),
                PersistedOrbitalLighting(0.75, 0.45, 0.30, 32.0),
            )
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_fhi_aims_cube_prompts_for_units_and_uses_the_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fhi.cube"
            path.write_text(
                FIXTURE.read_text(encoding="utf-8").replace(
                    "Cube data generated by ORCA",
                    "CUBE FILE written by FHI-AIMS",
                    1,
                ),
                encoding="utf-8",
            )
            with patch.object(
                QInputDialog,
                "getItem",
                return_value=("Bohr (atomic units)", True),
            ) as choose:
                workspace = self.window._open_local_geometry(path)

        self.assertIsNotNone(workspace)
        choose.assert_called_once()
        self.assertIs(
            workspace.scalar_field.source_coordinate_unit,
            CubeCoordinateUnit.BOHR,
        )

    def test_unknown_cube_unit_prompt_can_cancel_without_an_orphan_tab(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.cube"
            path.write_text(
                FIXTURE.read_text(encoding="utf-8").replace(
                    "Cube data generated by ORCA",
                    "Unidentified Cube producer",
                    1,
                ),
                encoding="utf-8",
            )
            tab_count = self.window._workspace_tabs.count()
            with patch.object(
                QInputDialog,
                "getItem",
                return_value=("", False),
            ):
                workspace = self.window._open_local_geometry(path)

        self.assertIsNone(workspace)
        self.assertEqual(self.window._workspace_tabs.count(), tab_count)


if __name__ == "__main__":
    unittest.main()
