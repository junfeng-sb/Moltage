import gc
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt
from PySide6.QtGui import QAction
from qt_test_support import wait_until
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.molecule_viewer_demo import MoleculeViewerDemo


REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


class UiR2ToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.window = MoleculeViewerDemo()
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
        self.window.resize(1280, 900)
        self.application.processEvents()

    def test_desktop_menus_own_exact_canonical_actions(self) -> None:
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
        menu = self.window._calculation_menu
        self.assertEqual(menu.title(), "Calculation")
        self.assertEqual(menu.objectName(), "calculationMenu")
        self.assertEqual(
            [
                None if action.isSeparator() else action.text()
                for action in menu.actions()
            ],
            [
                "External Programs",
                "FHI-aims",
                "ORCA",
                None,
                "Local Analysis",
                "Local Tight-Binding Transmission...",
            ],
        )
        self.assertEqual(
            [
                None if action.isSeparator() else action.text()
                for action in self.window._fhi_calculation_menu.actions()
            ],
            [
                "Transmission",
                "Step 1 — Molecule Optimization...",
                "Step 2 — Molecule–Au Optimization...",
                "Step 3 — Transport Convergence...",
                "Step 4 — Transmission...",
                None,
                "Other Analysis",
                "Electron Density Difference...",
            ],
        )
        self.assertEqual(
            [
                None if action.isSeparator() else action.text()
                for action in self.window._orca_calculation_menu.actions()
            ],
            [
                "Transmission",
                "Step 1 — Optimization...",
                "Step 2 — WBL Transmission...",
                None,
                "Other Analysis",
                "Run Frequency...",
            ],
        )

        menu_ownership = (
            (self.window._file_menu, self.window._new_geometry_action),
            (self.window._projects_menu, self.window._projects_action),
            (self.window._server_menu, self.window._servers_action),
            (self.window._settings_menu, self.window._view_settings_action),
            (self.window._settings_menu, self.window._bond_detection_action),
            (
                self.window._settings_menu,
                self.window._email_notifications_action,
            ),
            (self.window._fhi_calculation_menu, self.window._submit_aims_action),
            (self.window._fhi_calculation_menu, self.window._continue_step2_action),
            (self.window._fhi_calculation_menu, self.window._continue_step3_action),
            (self.window._fhi_calculation_menu, self.window._continue_step4_action),
        )
        canonical_actions = tuple(action for _, action in menu_ownership)
        for owner, action in menu_ownership:
            self.assertEqual(owner.actions().count(action), 1)
        for action in canonical_actions:
            self.assertIs(action.parent(), self.window)

        calculation_actions = (
            menu.actions()[0],
            self.window._fhi_calculation_menu.menuAction(),
            self.window._orca_calculation_menu.menuAction(),
            menu.actions()[3],
            menu.actions()[4],
            self.window._tight_binding_action,
        )
        self.assertTrue(calculation_actions[3].isSeparator())
        self.assertEqual(tuple(menu.actions()), calculation_actions)
        all_actions = tuple(self.window.findChildren(QAction))
        for action in canonical_actions:
            self.assertEqual(
                [
                    candidate
                    for candidate in all_actions
                    if candidate.objectName() == action.objectName()
                ],
                [action],
            )

    def test_workflow_actions_do_not_occupy_main_toolbar(self) -> None:
        workflow_actions = {
            self.window._projects_action,
            self.window._servers_action,
            self.window._email_notifications_action,
            self.window._generate_aims_action,
            self.window._submit_aims_action,
            self.window._continue_step2_action,
            self.window._continue_step3_action,
            self.window._continue_step4_action,
            self.window._density_action,
            self.window._tight_binding_action,
        }
        self.assertTrue(
            workflow_actions.isdisjoint(self.window._main_toolbar.actions())
        )
        workflow_texts = {action.text() for action in workflow_actions}
        self.assertTrue(
            workflow_texts.isdisjoint(
                button.text()
                for button in self.window._main_toolbar.findChildren(
                    QPushButton
                )
            )
        )

    def test_direct_tools_stay_icon_only_on_main_toolbar(self) -> None:
        toolbar_widgets = {
            self.window._main_toolbar.widgetForAction(action)
            for action in self.window._main_toolbar.actions()
        }
        for button, accessible_name in (
            (
                self.window._element_labels_button,
                "Show/Hide Element Labels",
            ),
            (self.window._distance_measure_button, "Measure Distance"),
            (self.window._angle_measure_button, "Measure Angle"),
            (self.window._rotate_bond_button, "Rotate Bond"),
            (self.window._delete_atom_button, "Delete Atom"),
            (self.window._replace_atom_button, "Replace Atom"),
        ):
            self.assertIn(button, toolbar_widgets)
            self.assertIsInstance(button, QToolButton)
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.accessibleName(), accessible_name)
            self.assertNotEqual(button.toolTip(), "")
        self.assertEqual(
            self.window._main_toolbar.toolButtonStyle(),
            Qt.ToolButtonStyle.ToolButtonIconOnly,
        )

        self.assertIn(self.window._au_tool_toggle, toolbar_widgets)
        self.assertIs(
            self.window._au_tool_toggle.defaultAction(),
            self.window._au_tool_dock.toggleViewAction(),
        )
        self.assertNotIn(
            self.window._au_tool_toggle.defaultAction(),
            self.window._calculation_menu.actions(),
        )

    def test_direct_viewer_modes_remain_mutually_exclusive(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        self.application.processEvents()
        buttons = (
            self.window._distance_measure_button,
            self.window._angle_measure_button,
            self.window._rotate_bond_button,
            self.window._delete_atom_button,
        )
        for active_button in buttons:
            active_button.click()
            self.application.processEvents()
            self.assertEqual(
                tuple(button.isChecked() for button in buttons),
                tuple(button is active_button for button in buttons),
            )
        self.window._rotate_bond_button.click()
        self.application.processEvents()

    def test_history_actions_are_first_in_the_normal_toolbar(self) -> None:
        main_actions = self.window._main_toolbar.actions()
        calculation_actions = self.window._calculation_menu.actions()
        for action, button in (
            (
                self.window._geometry_undo_action,
                self.window._geometry_undo_button,
            ),
            (
                self.window._geometry_redo_action,
                self.window._geometry_redo_button,
            ),
        ):
            self.assertIn(action, main_actions)
            self.assertNotIn(action, calculation_actions)
            self.assertIs(button.defaultAction(), action)
        self.assertEqual(
            main_actions[:2],
            [
                self.window._geometry_undo_action,
                self.window._geometry_redo_action,
            ],
        )
        self.assertFalse(hasattr(self.window, "_history_toolbar"))

    def test_busy_state_is_the_menu_action_state(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        self.application.processEvents()
        self.assertTrue(self.window._submit_aims_action.isEnabled())
        self.assertTrue(self.window._projects_action.isEnabled())

        with patch.object(
            self.window,
            "_launch_pending_submission_worker",
        ) as launch:
            self.window._start_submission(object(), object())
            launch.assert_called_once_with()
            self.assertFalse(self.window._submit_aims_action.isEnabled())
            self.assertFalse(self.window._projects_action.isEnabled())
            self.assertFalse(self.window._continue_step2_action.isEnabled())
            self.assertFalse(self.window._continue_step3_action.isEnabled())
            self.assertFalse(self.window._continue_step4_action.isEnabled())

        self.window._finish_submission_action()
        self.assertTrue(self.window._submit_aims_action.isEnabled())
        self.assertTrue(self.window._projects_action.isEnabled())

    def test_narrow_native_window_keeps_permanent_controls_visible(self) -> None:
        self.window.resize(900, 650)
        self.window.show()

        def controls_visible():
            extension = self.window._main_toolbar.findChild(
                QToolButton, "qt_toolbar_ext_button",
            )
            return (
                self.window.size() == QSize(900, 650)
                and (extension is None or not extension.isVisible())
                and all(widget.isVisibleTo(self.window) for widget in (
                    self.window._distance_measure_button,
                    self.window._angle_measure_button,
                    self.window._rotate_bond_button,
                    self.window._delete_atom_button,
                    self.window._replace_atom_button,
                    self.window._element_labels_button,
                    self.window._au_tool_toggle,
                    self.window._geometry_undo_button,
                    self.window._geometry_redo_button,
                    self.window.menuBar(),
                ))
                and not self.window.menuBar().actionGeometry(
                    self.window._calculation_menu.menuAction(),
                ).isEmpty()
            )

        self.application.processEvents()
        wait_until(controls_visible, message="narrow-window toolbar layout timed out")

        extension = self.window._main_toolbar.findChild(
            QToolButton,
            "qt_toolbar_ext_button",
        )
        self.assertTrue(extension is None or not extension.isVisible())
        for widget in (
            self.window._distance_measure_button,
            self.window._angle_measure_button,
            self.window._rotate_bond_button,
            self.window._delete_atom_button,
            self.window._replace_atom_button,
            self.window._element_labels_button,
            self.window._au_tool_toggle,
            self.window._geometry_undo_button,
            self.window._geometry_redo_button,
        ):
            self.assertTrue(widget.isVisibleTo(self.window))
        self.assertTrue(self.window.menuBar().isVisibleTo(self.window))
        self.assertFalse(
            self.window.menuBar()
            .actionGeometry(self.window._calculation_menu.menuAction())
            .isEmpty()
        )


if __name__ == "__main__":
    unittest.main()
