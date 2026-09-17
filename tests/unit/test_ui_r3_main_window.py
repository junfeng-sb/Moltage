import gc
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QToolButton,
    QWidget,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from moltage.gui.bond_detection_dialog import BondDetectionDialog
from moltage.gui.theme import DEFAULT_THEME_ID, DEFAULT_THEME_MANAGER
from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER
from tools.molecule_viewer_demo import (
    MoleculeViewerDemo,
    _TightBindingWorkspace,
    _UpdateLogDialog,
    _ViewerPickMode,
)


REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


class UiR3MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.window = MoleculeViewerDemo()
        cls.window.resize(900, 650)
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()
        cls.window = None
        gc.collect()

    def setUp(self) -> None:
        self.window._connectivity_multiplier = DEFAULT_CONNECTIVITY_MULTIPLIER
        self.window.resize(900, 650)
        self.window.show()
        self.window._au_tool_dock.show()
        self.application.processEvents()

    def test_exact_top_level_menu_order_and_contents(self) -> None:
        self.assertEqual(
            [action.text() for action in self.window.menuBar().actions()],
            [
                "File",
                "Projects",
                "Calculation",
                "Server",
                "Settings",
                "Help",
            ],
        )
        self.assertEqual(
            [action.text() for action in self.window._file_menu.actions()],
            ["New Geometry...", "", "Export Current View..."],
        )
        self.assertEqual(
            [action.text() for action in self.window._projects_menu.actions()],
            ["Project Manager..."],
        )
        self.assertEqual(
            [action.text() for action in self.window._calculation_menu.actions()],
            [
                "External Programs",
                "FHI-aims",
                "ORCA",
                "",
                "Local Analysis",
                "Local Tight-Binding Transmission...",
            ],
        )
        self.assertFalse(
            self.window._calculation_menu.actions()[0].isEnabled()
        )
        self.assertFalse(
            self.window._calculation_menu.actions()[4].isEnabled()
        )
        self.assertEqual(
            [action.text() for action in self.window._fhi_calculation_menu.actions()],
            [
                "Transmission",
                "Step 1 — Molecule Optimization...",
                "Step 2 — Molecule–Au Optimization...",
                "Step 3 — Transport Convergence...",
                "Step 4 — Transmission...",
                "",
                "Other Analysis",
                "Electron Density Difference...",
            ],
        )
        self.assertFalse(self.window._fhi_calculation_menu.actions()[0].isEnabled())
        self.assertFalse(self.window._fhi_calculation_menu.actions()[6].isEnabled())
        self.assertEqual(
            [action.text() for action in self.window._orca_calculation_menu.actions()],
            [
                "Transmission",
                "Step 1 — Optimization...",
                "Step 2 — WBL Transmission...",
                "",
                "Other Analysis",
                "Run Frequency...",
            ],
        )
        self.assertFalse(self.window._orca_calculation_menu.actions()[0].isEnabled())
        self.assertFalse(self.window._orca_calculation_menu.actions()[4].isEnabled())
        self.assertEqual(
            [action.text() for action in self.window._server_menu.actions()],
            ["Manage Servers..."],
        )
        self.assertEqual(
            [action.text() for action in self.window._settings_menu.actions()],
            ["View...", "Bond Detection...", "Email Notifications..."],
        )

    def test_local_tight_binding_opens_only_from_loaded_geometry(self) -> None:
        probe = MoleculeViewerDemo()
        probe.resize(900, 650)
        probe.setWindowOpacity(0)
        probe.show()
        self.application.processEvents()
        try:
            self.assertFalse(probe._tight_binding_action.isEnabled())
            self.assertTrue(probe._load(REFERENCE_XYZ_PATH))
            self.application.processEvents()
            self.assertTrue(probe._tight_binding_action.isEnabled())

            workspace = probe._new_tight_binding_workspace()
            self.application.processEvents()

            self.assertIsInstance(workspace, _TightBindingWorkspace)
            self.assertIn("Tight Binding", workspace.display_title)
            self.assertIs(
                probe._workspace_tabs.currentWidget(),
                workspace.content,
            )
        finally:
            probe.close()
            probe.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.application.processEvents()

    def test_new_geometry_uses_supported_geometry_chooser_and_existing_loader(self) -> None:
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(REFERENCE_XYZ_PATH), "XYZ (*.xyz)"),
        ) as chooser, patch.object(self.window, "_load") as load:
            self.window._new_geometry_action.trigger()

        chooser.assert_called_once_with(
            self.window,
            "New Geometry",
            "",
            "Supported Geometry (*.xyz *.mol *.in *.next_step *.cube *.cub);;"
            "XYZ (*.xyz);;MOL (*.mol);;IN (*.in);;NEXT STEP (*.next_step);;"
            "CUBE (*.cube);;CUB (*.cub)",
        )
        load.assert_called_once_with(REFERENCE_XYZ_PATH)

    def test_empty_workspace_centers_open_prompt_and_reuses_open_handler(self) -> None:
        probe = MoleculeViewerDemo()
        probe.resize(900, 650)
        probe.show()
        self.application.processEvents()
        try:
            workspace = probe._active_geometry_workspace()
            self.assertIsNotNone(workspace)
            self.assertIs(
                workspace.viewer_stack.currentWidget(),
                workspace.empty_state_widget,
            )
            message = workspace.empty_state_widget.findChild(
                QLabel,
                "emptyGeometryMessage",
            )
            self.assertEqual(
                message.text(),
                "Open a geometry file (.xyz, .mol, .in, .next_step) or Cube "
                "file (.cube, .cub) to begin.",
            )
            self.assertFalse(workspace.status_panel.isVisibleTo(probe))
            self.assertEqual(workspace.operation_label.text(), "")

            with patch.object(probe, "_open_xyz") as open_geometry:
                workspace.open_geometry_button.click()
                self.application.processEvents()
            open_geometry.assert_called_once_with()

            self.assertTrue(probe._load(REFERENCE_XYZ_PATH))
            self.application.processEvents()
            self.assertIs(workspace.viewer_stack.currentWidget(), workspace.viewer)
            self.assertTrue(workspace.status_panel.isVisibleTo(probe))
        finally:
            probe.close()
            probe.deleteLater()
            self.application.processEvents()

    def test_no_manual_reload_or_multiplier_surface_remains(self) -> None:
        self.assertFalse(hasattr(self.window, "_reload_button"))
        self.assertTrue(callable(self.window._reload))
        self.assertFalse(hasattr(self.window, "_multiplier"))
        self.assertIsNone(self.window.findChild(QDoubleSpinBox, "connectivityMultiplier"))

        action_texts = {
            action.text() for action in self.window.findChildren(QAction)
        }
        button_texts = {
            button.text() for button in self.window.findChildren(QPushButton)
        }
        toolbar_labels = {
            label.text() for label in self.window._main_toolbar.findChildren(QLabel)
        }
        self.assertNotIn("Reload", action_texts | button_texts)
        self.assertNotIn("Multiplier:", toolbar_labels)

    def test_bond_detection_dialog_preserves_factor_contract(self) -> None:
        dialog = BondDetectionDialog(DEFAULT_CONNECTIVITY_MULTIPLIER, self.window)
        try:
            factor = dialog.findChild(QDoubleSpinBox, "bondThresholdFactor")
            formula = dialog.findChild(QLabel, "bondDetectionFormula")
            self.assertEqual(dialog.windowTitle(), "Bond Detection")
            self.assertIsNotNone(factor)
            self.assertEqual(factor.decimals(), 2)
            self.assertEqual(factor.minimum(), 0.01)
            self.assertEqual(factor.maximum(), 10.00)
            self.assertAlmostEqual(factor.singleStep(), 0.10)
            self.assertTrue(factor.keyboardTracking())
            self.assertEqual(factor.text(), "1.10")
            self.assertEqual(dialog.selected_factor(), 1.10)
            self.assertIn("Dimensionless factor", formula.text())
            self.assertIn("distance <= factor ×", formula.text())
            self.assertIn("covalent radius of atom A", formula.text())
            self.assertIn("covalent radius of atom B", formula.text())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_bond_detection_acceptance_keeps_live_preview_factor(self) -> None:
        class AcceptedDialog:
            def __init__(inner_self, current_factor, parent=None):
                self.assertEqual(current_factor, 1.10)
                self.assertIs(parent, self.window)
                inner_self._preview_callback = None
                inner_self.preview_factor_changed = inner_self

            def connect(inner_self, callback):
                inner_self._preview_callback = callback

            def exec(inner_self):
                inner_self._preview_callback(1.25)
                return QDialog.DialogCode.Accepted

            def selected_factor(inner_self):
                return 1.25

        with patch(
            "tools.molecule_viewer_demo.BondDetectionDialog",
            AcceptedDialog,
        ), patch.object(self.window, "_reload") as reload_structure:
            self.window._bond_detection_action.trigger()

        self.assertEqual(self.window._connectivity_multiplier, 1.25)
        reload_structure.assert_not_called()

    def test_toolbar_has_only_immediate_manipulation_groups(self) -> None:
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
        expected_actions = (
            self.window._geometry_undo_action,
            self.window._geometry_redo_action,
            self.window._reset_view_action,
            self.window._element_labels_action,
            self.window._distance_measure_action,
            self.window._angle_measure_action,
            self.window._rotate_bond_action,
            self.window._delete_atom_action,
            self.window._replace_atom_action,
            self.window._electrode_builder_action,
        )
        for action in expected_actions:
            self.assertEqual(visible_actions.count(action), 1)
            button = self.window._main_toolbar.widgetForAction(action)
            self.assertIsInstance(button, QToolButton)
            self.assertIs(button.defaultAction(), action)
            self.assertFalse(action.icon().isNull())
            self.assertNotEqual(action.toolTip(), "")

    def test_electrode_builder_uses_the_dock_toggle_action(self) -> None:
        action = self.window._electrode_builder_action
        self.assertIs(action, self.window._au_tool_dock.toggleViewAction())
        self.assertIs(
            self.window._electrode_builder_toggle.defaultAction(),
            action,
        )
        self.assertEqual(action.toolTip(), "Show/Hide Electrode Builder")

        self.window._au_tool_dock.hide()
        self.application.processEvents()
        self.assertFalse(action.isChecked())
        action.trigger()
        self.application.processEvents()
        self.assertTrue(self.window._au_tool_dock.isVisible())
        self.assertTrue(action.isChecked())

    def test_formal_title_and_visible_naming(self) -> None:
        self.assertEqual(self.window.windowTitle(), "Moltage")
        self.assertEqual(self.window._au_tool_dock.windowTitle(), "Electrode Builder")
        visible_text = [
            self.window.windowTitle(),
            self.window._au_tool_dock.windowTitle(),
            *(action.text() for action in self.window.findChildren(QAction)),
            *(action.toolTip() for action in self.window.findChildren(QAction)),
            *(label.text() for label in self.window.findChildren(QLabel)),
            *(button.text() for button in self.window.findChildren(QPushButton)),
            *(button.toolTip() for button in self.window.findChildren(QToolButton)),
        ]
        self.assertTrue(all("Au Tool" not in text for text in visible_text))
        self.assertNotIn("validation", self.window.windowTitle().lower())

    def test_relocated_actions_keep_their_existing_handlers(self) -> None:
        calls = []

        def recorder(name):
            def record(instance):
                calls.append((name, instance))

            return record

        replacements = {
            "_open_xyz": recorder("file"),
            "_open_projects": recorder("projects"),
            "_submit_aims_optimization": recorder("step1"),
            "_continue_project_step2": recorder("step2"),
            "_continue_project_step3": recorder("step3"),
            "_continue_project_step4": recorder("step4"),
            "_open_server_profiles": recorder("server"),
            "_open_view_settings": recorder("view"),
            "_open_bond_detection": recorder("bond"),
            "_open_email_notifications": recorder("email"),
        }
        with patch.multiple(MoleculeViewerDemo, **replacements):
            probe = MoleculeViewerDemo()
            try:
                actions = (
                    ("file", probe._new_geometry_action),
                    ("projects", probe._projects_action),
                    ("step1", probe._submit_aims_action),
                    ("step2", probe._continue_step2_action),
                    ("step3", probe._continue_step3_action),
                    ("step4", probe._continue_step4_action),
                    ("server", probe._servers_action),
                    ("view", probe._view_settings_action),
                    ("bond", probe._bond_detection_action),
                    ("email", probe._email_notifications_action),
                )
                for _, action in actions:
                    action.setEnabled(True)
                    action.trigger()
                self.assertEqual(
                    calls,
                    [(name, probe) for name, _ in actions],
                )
            finally:
                probe.close()
                probe.deleteLater()
                self.application.processEvents()

    def test_unused_measurement_panel_is_hidden(self) -> None:
        self.window._set_pick_mode(_ViewerPickMode.NORMAL, announce=False)
        self.window._measurement_session.clear()
        self.window._measurement_table.setRowCount(0)
        self.window._update_measurement_empty_state()
        self.assertEqual(self.window._measurement_heading.text(), "Measurements")
        self.assertEqual(self.window._measurement_table.accessibleName(), "Measurements")
        self.assertEqual(self.window._measurement_empty_state.text(), "No measurements")
        self.assertFalse(self.window._measurement_panel.isVisibleTo(self.window))
        self.assertFalse(self.window._measurement_empty_state.isVisibleTo(self.window))
        self.assertFalse(self.window._measurement_table.isVisible())

    def test_top_right_update_log_menu_opens_bundled_read_only_log(self) -> None:
        button = self.window._update_log_button
        corner = self.window.menuBar().cornerWidget(
            Qt.Corner.TopRightCorner
        )
        self.assertIs(corner, self.window._menu_corner_controls)
        self.assertIs(corner.layout().itemAt(0).widget(), self.window._theme_button)
        self.assertIs(corner.layout().itemAt(1).widget(), button)
        self.assertFalse(button.icon().isNull())
        self.assertEqual(
            [action.text() for action in button.menu().actions()],
            ["Update Log..."],
        )

        with patch("tools.molecule_viewer_demo._UpdateLogDialog") as dialog_type:
            self.window._update_log_action.trigger()

        dialog_type.assert_called_once()
        content, parent = dialog_type.call_args.args
        self.assertIn("Moltage 更新日志", content)
        self.assertEqual(
            content,
            (PROJECT_ROOT / "resources" / "update_log.txt").read_text(
                encoding="utf-8"
            ),
        )
        self.assertIs(parent, self.window)
        dialog_type.return_value.exec.assert_called_once_with()

        dialog = _UpdateLogDialog(content, self.window)
        try:
            log_view = dialog.findChild(QPlainTextEdit, "updateLogText")
            self.assertTrue(log_view.isReadOnly())
            self.assertEqual(log_view.toPlainText(), content)
        finally:
            dialog.deleteLater()

    def test_help_menu_exposes_about_and_bundled_legal_notices(self) -> None:
        self.assertEqual(
            [action.text() for action in self.window._help_menu.actions()],
            ["License and Third-Party Notices...", "About Moltage"],
        )

        with patch("tools.molecule_viewer_demo._UpdateLogDialog") as dialog_type:
            self.window._license_notices_action.trigger()
        content, parent = dialog_type.call_args.args
        self.assertIn("GNU GENERAL PUBLIC LICENSE", content)
        self.assertIn("Third-Party Notices", content)
        self.assertIs(parent, self.window)
        dialog_type.return_value.exec.assert_called_once_with()

        with patch("tools.molecule_viewer_demo.QMessageBox.about") as about:
            self.window._about_action.trigger()
        about.assert_called_once()
        self.assertIn("Moltage 0.2.1", about.call_args.args[2])
        self.assertIn("GNU GPL version 3 only", about.call_args.args[2])

    def test_theme_button_switches_registered_themes_and_stays_left_of_updates(
        self,
    ) -> None:
        try:
            button = self.window._theme_button
            toolbar_icon_size = self.window._main_toolbar.iconSize()
            reset_icon_size = self.window._reset_view_button.iconSize()
            window_geometry = self.window.geometry()
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.accessibleName(), "Themes")
            self.assertEqual(
                tuple(self.window._theme_actions),
                tuple(theme.theme_id for theme in DEFAULT_THEME_MANAGER.themes),
            )

            self.window._theme_actions["event_horizon"].trigger()
            self.application.processEvents()
            self.assertEqual(
                DEFAULT_THEME_MANAGER.active_theme_id,
                "event_horizon",
            )
            self.assertTrue(
                self.window._theme_actions["event_horizon"].isChecked()
            )
            self.assertIn("Event Horizon", button.toolTip())
            self.assertFalse(self.window._reset_view_action.icon().isNull())
            self.assertEqual(
                sum(
                    action.isChecked()
                    for action in self.window._theme_actions.values()
                ),
                1,
            )
            self.assertEqual(
                self.window._main_toolbar.iconSize(),
                toolbar_icon_size,
            )
            self.assertEqual(
                self.window._reset_view_button.iconSize(),
                reset_icon_size,
            )
            self.assertEqual(self.window.geometry(), window_geometry)
        finally:
            self.window._select_theme(DEFAULT_THEME_ID)
            self.application.processEvents()

    def test_theme_menu_uses_separated_light_and_dark_columns(self) -> None:
        try:
            panel = self.window._theme_panel
            layout = panel.layout()
            light_column = panel.findChild(QWidget, "themeLightColumn")
            dark_column = panel.findChild(QWidget, "themeDarkColumn")
            column_divider = panel.findChild(QFrame, "themeColumnDivider")

            self.assertIs(layout.itemAt(0).widget(), light_column)
            self.assertIs(layout.itemAt(1).widget(), column_divider)
            self.assertIs(layout.itemAt(2).widget(), dark_column)
            self.assertEqual(column_divider.frameShape(), QFrame.Shape.VLine)
            self.assertEqual(
                light_column.findChild(QLabel, "themeLightHeader").text(),
                "Light",
            )
            self.assertEqual(
                dark_column.findChild(QLabel, "themeDarkHeader").text(),
                "Dark",
            )
            self.assertEqual(
                light_column.findChild(
                    QFrame,
                    "themeLightHeaderDivider",
                ).frameShape(),
                QFrame.Shape.HLine,
            )
            self.assertEqual(
                dark_column.findChild(
                    QFrame,
                    "themeDarkHeaderDivider",
                ).frameShape(),
                QFrame.Shape.HLine,
            )
            self.assertEqual(
                [
                    choice.text()
                    for choice in light_column.findChildren(QRadioButton)
                ],
                [
                    theme.display_name
                    for theme in DEFAULT_THEME_MANAGER.themes
                    if not theme.is_dark
                ],
            )
            self.assertEqual(
                [
                    choice.text()
                    for choice in dark_column.findChildren(QRadioButton)
                ],
                [
                    theme.display_name
                    for theme in DEFAULT_THEME_MANAGER.themes
                    if theme.is_dark
                ],
            )

            self.window._theme_choice_buttons["event_horizon"].click()
            self.application.processEvents()
            self.assertEqual(
                DEFAULT_THEME_MANAGER.active_theme_id,
                "event_horizon",
            )
            self.assertTrue(
                self.window._theme_choice_buttons["event_horizon"].isChecked()
            )
            self.assertEqual(
                sum(
                    choice.isChecked()
                    for choice in self.window._theme_choice_buttons.values()
                ),
                1,
            )
        finally:
            self.window._select_theme(DEFAULT_THEME_ID)
            self.application.processEvents()

    def test_rotate_bond_svg_is_purple_ball_arrow_and_rotation_arc(self) -> None:
        svg = (PROJECT_ROOT / "resources" / "icons" / "rotate_bond.svg").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="ballGradient"', svg)
        self.assertIn('id="arrowGradient"', svg)
        self.assertIn('<circle cx="5.1" cy="12" r="3.2"', svg)
        self.assertIn("l5.72 4.45", svg)
        self.assertIn('id="rotationBack"', svg)
        self.assertIn('stroke-dasharray="1.35 1.18"', svg)
        self.assertIn('id="rotationFront"', svg)
        self.assertIn('id="rotationArrowhead"', svg)


if __name__ == "__main__":
    unittest.main()
