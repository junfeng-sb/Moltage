import gc
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.domain.bond_display import (
    BondDisplayOrder,
    ConnectivitySource,
)
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.periodic_table_dialog import PeriodicTableDialog
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.vdw_radii import load_default_vdw_radii
from moltage.visualization.measurements import (
    DistanceMeasurement,
    MeasurementSession,
)
from species_test_support import synthetic_species_library
from tools.molecule_viewer_demo import MoleculeViewerDemo, _ViewerPickMode


class AtomEditingViewerTests(unittest.TestCase):
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
        self.structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "C", 1.0, 0.0, 0.0),
                Atom(2, "O", 2.0, 0.0, 0.0),
                Atom(3, "H", 3.0, 1.0, 0.0),
            ),
            comment="atom editing viewer",
        )
        self.connectivity = Connectivity(
            4,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.0),
                Bond(2, 3, 2 ** 0.5),
            ),
        )
        self.orders = (
            BondDisplayOrder(0, 1, 1),
            BondDisplayOrder(1, 2, 2),
            BondDisplayOrder(2, 3, 3),
        )
        self._install(self.structure, self.connectivity, self.orders)

    def _install(self, structure, connectivity, orders) -> None:
        window = self.window
        workspace = window._active_geometry_workspace()
        workspace.read_only = False
        workspace.coordinate_only = False
        workspace.restart_draft = None
        window._prepare_for_structure_replacement()
        radii = load_default_covalent_radii()
        window._viewer.set_molecule(structure, connectivity, radii, orders)
        window._source_path = None
        window._source_structure = structure
        window._source_connectivity = connectivity
        window._source_connectivity_source = ConnectivitySource.EXPLICIT
        window._source_bond_display_orders = orders
        window._structure = structure
        window._connectivity = connectivity
        window._connectivity_source = ConnectivitySource.EXPLICIT
        window._bond_display_orders = orders
        window._covalent_radii = radii
        window._vdw_radii = load_default_vdw_radii()
        window._anchors = detect_anchors(structure, connectivity)
        window._measurement_session = MeasurementSession()
        window._measurement_selection = ()
        window._site_controls = {}
        window._electrode_site_controls = {}
        window._current_proposals = ()
        window._applied_result = None
        window._reset_electrode_session_state()
        window._confirmed = False
        window._rebuild_anchor_site_controls()
        window._rebuild_electrode_controls()
        window._set_measurement_tools_enabled(True)
        window._store_bound_geometry_workspace(capture_builder_visibility=False)
        window._route_active_workspace()
        self.application.processEvents()

    def test_delete_is_continuous_selectively_remaps_measurements_and_undo_redo(self):
        session = self.window._measurement_session
        first = session.add_distance(self.structure, 0, 1)
        second = session.add_angle(self.structure, 0, 1, 2)
        third = session.add_distance(self.structure, 2, 3)
        self.window._sync_measurement_view()
        self.window._set_pick_mode(_ViewerPickMode.DELETE_ATOM)

        self.window._report_picked_atom(1)

        self.assertEqual(
            tuple(atom.element for atom in self.window._structure),
            ("C", "O", "H"),
        )
        self.assertEqual(
            self.window._bond_display_orders,
            (BondDisplayOrder(1, 2, 3),),
        )
        self.assertEqual(
            self.window._measurement_session.measurements,
            (
                DistanceMeasurement(
                    third.measurement_id,
                    1,
                    2,
                    third.value_angstrom,
                ),
            ),
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.DELETE_ATOM)
        self.assertTrue(self.window._delete_atom_action.isChecked())

        self.window._geometry_undo_action.trigger()
        self.assertEqual(self.window._structure, self.structure)
        self.assertEqual(self.window._connectivity, self.connectivity)
        self.assertEqual(self.window._bond_display_orders, self.orders)
        self.assertEqual(
            self.window._measurement_session.measurements,
            (first, second, third),
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.DELETE_ATOM)

        self.window._geometry_redo_action.trigger()
        self.assertEqual(
            tuple(atom.element for atom in self.window._structure),
            ("C", "O", "H"),
        )
        self.assertEqual(len(self.window._measurement_session.measurements), 1)
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.DELETE_ATOM)

    def test_delete_allows_empty_structure_and_undo_restores_it(self):
        structure = MolecularStructure((Atom(0, "Au", 0.0, 0.0, 0.0),))
        self._install(structure, Connectivity(1, ()), ())
        self.window._set_pick_mode(_ViewerPickMode.DELETE_ATOM)

        self.window._report_picked_atom(0)

        self.assertEqual(len(self.window._structure), 0)
        self.assertEqual(self.window._connectivity, Connectivity(0, ()))
        self.assertTrue(self.window._delete_atom_action.isChecked())
        self.window._geometry_undo_action.trigger()
        self.assertEqual(self.window._structure, structure)

    def test_periodic_table_and_continuous_replace_preserve_measurements(self):
        dialog = PeriodicTableDialog(self.window)
        try:
            buttons = dialog.element_buttons
            self.assertEqual(len(buttons), 118)
            self.assertEqual(sum(button.isEnabled() for button in buttons.values()), 83)
            self.assertTrue(buttons["Bi"].isEnabled())
            self.assertFalse(buttons["Po"].isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()

        measurement = self.window._measurement_session.add_distance(
            self.structure,
            0,
            3,
        )
        self.window._sync_measurement_view()

        class AcceptedPeriodicTable:
            selected_element = "N"

            def __init__(inner_self, parent):
                del parent

            def exec(inner_self):
                return QDialog.DialogCode.Accepted

        with patch(
            "tools.molecule_viewer_demo.PeriodicTableDialog",
            AcceptedPeriodicTable,
        ):
            self.window._replace_atom_button.click()

        self.window._report_picked_atom(0)
        self.window._report_picked_atom(2)

        self.assertEqual(
            tuple(atom.element for atom in self.window._structure),
            ("N", "C", "N", "H"),
        )
        self.assertEqual(self.window._connectivity, self.connectivity)
        self.assertEqual(self.window._bond_display_orders, self.orders)
        self.assertEqual(
            self.window._measurement_session.measurements,
            (measurement,),
        )
        self.assertEqual(self.window._pick_mode, _ViewerPickMode.REPLACE_ATOM)
        self.assertTrue(self.window._replace_atom_action.isChecked())

        self.window._geometry_undo_action.trigger()
        self.assertEqual(
            tuple(atom.element for atom in self.window._structure),
            ("N", "C", "O", "H"),
        )
        self.window._geometry_redo_action.trigger()
        self.assertEqual(
            tuple(atom.element for atom in self.window._structure),
            ("N", "C", "N", "H"),
        )

    def test_generated_geometry_in_uses_the_atom_edited_working_structure(self):
        self.window._replacement_element = "N"
        self.window._set_pick_mode(_ViewerPickMode.REPLACE_ATOM)
        self.window._report_picked_atom(0)
        self.window._set_pick_mode(_ViewerPickMode.DELETE_ATOM)
        self.window._report_picked_atom(3)
        expected = render_geometry_in(self.window._structure)

        plan = AimsOptimizationInputPlan(
            self.window._structure,
            AimsOptimizationSettings(),
        )

        bundle = plan.materialize(synthetic_species_library())

        self.assertEqual(bundle.geometry_text, expected)

    def test_identity_editing_is_disabled_for_read_only_and_coordinate_only_tabs(self):
        workspace = self.window._active_geometry_workspace()
        workspace.read_only = True
        self.window._route_active_workspace()
        self.assertFalse(self.window._delete_atom_action.isEnabled())
        self.assertFalse(self.window._replace_atom_action.isEnabled())

        workspace.read_only = False
        workspace.coordinate_only = True
        self.window._route_active_workspace()
        self.assertFalse(self.window._delete_atom_action.isEnabled())
        self.assertFalse(self.window._replace_atom_action.isEnabled())
        self.assertTrue(self.window._rotate_bond_action.isEnabled())


if __name__ == "__main__":
    unittest.main()
