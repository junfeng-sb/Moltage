import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from qt_test_support import wait_until
from PySide6.QtWidgets import QApplication, QDockWidget

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.tight_binding_workspace import TightBindingWorkspace
from moltage.structure.covalent_radii import load_default_covalent_radii


class TightBindingWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "N", 1.2, 0.0, 0.0),
                Atom(2, "C", 2.4, 0.0, 0.0),
            )
        )
        self.connectivity = Connectivity(
            3,
            (Bond(0, 1, 1.2), Bond(1, 2, 1.2)),
        )
        anchors = (
            AnchorCandidate(AnchorKind.ALKYNYL_C, 0, (0,)),
            AnchorCandidate(AnchorKind.ALKYNYL_C, 2, (2,)),
        )
        self.widget = TightBindingWorkspace(
            self.structure,
            self.connectivity,
            anchors,
            load_default_covalent_radii(),
            source_title="fixture.xyz",
        )

    def tearDown(self) -> None:
        if self.widget.result_window is not None:
            self.widget.result_window.close()
        self.widget.viewer.close()
        self.widget.close()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def _complete_parameters(self) -> None:
        self.widget._onsite_type_edits["C"].setText("0")
        self.widget._onsite_type_edits["N"].setText("0.2")
        self.widget._hopping_type_edits[("C", "N")].setText("-1")
        for field, text in (
            (self.widget.gamma_left, "0.5"),
            (self.widget.gamma_right, "0.7"),
            (self.widget.eta, "1e-5"),
            (self.widget.energy_start, "-1"),
            (self.widget.energy_end, "1"),
            (self.widget.energy_step, "0.05"),
        ):
            field.setText(text)

    def test_candidates_prefill_contacts_but_no_scientific_values(self) -> None:
        self.assertEqual(self.widget.left_contact.value(), 1)
        self.assertEqual(self.widget.right_contact.value(), 3)
        self.assertFalse(self.widget.calculate_button.isEnabled())
        self.assertTrue(
            all(not field.text() for field in self.widget._onsite_type_edits.values())
        )
        atom_labels, bond_labels = self.widget.viewer._scene.parameter_label_texts
        self.assertEqual(atom_labels, ())
        self.assertEqual(bond_labels, ())
        self.assertIsNone(self.widget.result_window)
        features = self.widget.matrix_dock.features()
        self.assertTrue(features & QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.assertTrue(features & QDockWidget.DockWidgetFeature.DockWidgetFloatable)

    def test_exact_overrides_update_matrix_and_hover_labels(self) -> None:
        self._complete_parameters()
        self.widget.settings_tabs.setCurrentIndex(1)
        self.widget._atom_picked(1)
        self.widget.atom_override.setText("-0.4")
        self.widget.settings_tabs.setCurrentIndex(2)
        self.widget._bond_picked(1, 2)
        self.widget.bond_override.setText("0.75")

        model = self.widget._resolved_model()

        self.assertEqual(model.onsite_energies_ev, (0.0, -0.4, 0.0))
        self.assertEqual(
            tuple(item.value_ev for item in model.hoppings),
            (-1.0, 0.75),
        )
        matrix = self.widget.matrix_model
        self.assertEqual(matrix.data(matrix.index(1, 2)), "0.75")
        self.assertEqual(matrix.data(matrix.index(2, 1)), "0.75")
        self.assertEqual(matrix.data(matrix.index(0, 2)), "0")
        self.assertEqual(
            self.widget.viewer._scene.parameter_label_texts,
            ((), ()),
        )
        self.widget.viewer._set_hover_target(("atom", 1))
        atom_labels, bond_labels = self.widget.viewer._scene.parameter_label_texts
        self.assertEqual(atom_labels, ("2 N: ε=-0.4 eV",))
        self.assertEqual(bond_labels, ())
        self.assertEqual(self.widget.viewer._scene.hover_highlight, (1, None))
        self.widget.viewer._set_hover_target(("bond", (1, 2)))
        atom_labels, bond_labels = self.widget.viewer._scene.parameter_label_texts
        self.assertEqual(atom_labels, ())
        self.assertEqual(bond_labels, ("2–3: t=0.75 eV",))
        self.assertEqual(
            self.widget.viewer._scene.hover_highlight,
            (None, (1, 2)),
        )
        self.widget.viewer._set_hover_target(None)
        self.assertEqual(self.widget.viewer._scene.parameter_label_texts, ((), ()))
        self.assertTrue(self.widget.calculate_button.isEnabled())

    def test_mouse_move_drives_one_hover_target_and_leave_clears_it(self) -> None:
        viewer = self.widget.viewer
        point = QPointF(20.0, 20.0)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            point,
            point,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        with (
            patch.object(viewer, "_display_position", return_value=(20, 20)),
            patch.object(viewer, "_atom_index_at_display", return_value=1),
        ):
            viewer.eventFilter(viewer._vtk_widget, move)
            wait_until(
                lambda: viewer._scene.hover_highlight == (1, None)
                and viewer._scene.parameter_label_texts[0] == ("2 N: ε=— eV",),
                message="tight-binding hover feedback timed out",
            )

        self.assertEqual(viewer._scene.hover_highlight, (1, None))
        self.assertEqual(
            viewer._scene.parameter_label_texts[0],
            ("2 N: ε=— eV",),
        )

        viewer.eventFilter(viewer._vtk_widget, QEvent(QEvent.Type.Leave))

        self.assertEqual(viewer._scene.hover_highlight, (None, None))
        self.assertEqual(viewer._scene.parameter_label_texts, ((), ()))

    def test_calculate_then_recalculate_updates_the_same_plot(self) -> None:
        self._complete_parameters()
        self.assertIsNone(self.widget.result_window)
        self.widget._calculate_clicked()
        self.assertIsNotNone(self.widget.result_window)
        self.assertTrue(self.widget.result_window.isVisible())
        self.assertTrue(self.widget._thread_pool.waitForDone(5_000))
        self.application.processEvents()
        first_series = self.widget.result_window.plot._series
        self.assertEqual(first_series.count(), 40)
        tick_texts = tuple(
            label.text()
            for label in self.widget.result_window.plot._chart_view.power_tick_labels
        )
        self.assertTrue(tick_texts)
        self.assertTrue(
            all(text.startswith("10") and "E" not in text for text in tick_texts)
        )

        self.widget.gamma_left.setText("0.9")
        self.widget._recalculate_timer.stop()
        self.widget._recalculate_latest()
        self.assertTrue(self.widget._thread_pool.waitForDone(5_000))
        self.application.processEvents()

        self.assertIs(self.widget.result_window.plot._series, first_series)
        self.assertEqual(first_series.count(), 40)
        self.assertIn("Calculated 40 energy points", self.widget.status.text())


if __name__ == "__main__":
    unittest.main()
