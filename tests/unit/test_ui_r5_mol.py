import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from moltage.domain.bond_display import ConnectivitySource
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.workspace_tabs import TransmissionWorkspaceRequest
from moltage.structure.connectivity import infer_connectivity
from moltage.visualization.bond_torsion import BondTorsionSession
from moltage.visualization.molecule_scene import BOND_TUBE_RADIUS
from moltage.visualization.view_preferences import ViewPreferences
from test_projects_dialog import _step4_success_snapshot
from tools.molecule_viewer_demo import MoleculeViewerDemo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "ui_r5"


class UiR5MolWorkspaceTests(unittest.TestCase):
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

    def test_file_chooser_has_exact_supported_xyz_and_mol_filters(self) -> None:
        selected = FIXTURES / "double.mol"
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(selected), "MOL (*.mol)"),
        ) as chooser:
            self.window._new_geometry_action.trigger()
            self.application.processEvents()

        self.assertEqual(
            chooser.call_args.args[3],
            "Supported Geometry (*.xyz *.mol *.in *.next_step *.cube *.cub);;"
            "XYZ (*.xyz);;MOL (*.mol);;IN (*.in);;NEXT STEP (*.next_step);;"
            "CUBE (*.cube);;CUB (*.cub)",
        )
        self.assertEqual(self.window._source_path, selected)

    def test_mol_graph_is_explicit_and_multiplier_independent_in_the_viewer(self) -> None:
        self.window._connectivity_multiplier = 0.10

        workspace = self.window._open_local_geometry(FIXTURES / "mixed.mol")

        self.assertIsNotNone(workspace)
        self.assertIs(
            self.window._source_connectivity_source,
            ConnectivitySource.EXPLICIT,
        )
        self.assertIs(
            self.window._connectivity_source,
            ConnectivitySource.EXPLICIT,
        )
        self.assertEqual(len(self.window._connectivity), 3)
        self.assertEqual(
            tuple(record.order for record in self.window._bond_display_orders),
            (1, 2, 3),
        )
        self.assertEqual(
            self.window._viewer._scene._bond_polydata.GetNumberOfLines(),
            6,
        )

    def test_view_thickness_preserves_mol_strands_offsets_and_metadata(self) -> None:
        workspace = self.window._open_local_geometry(FIXTURES / "mixed.mol")
        scene = workspace.viewer._scene
        baseline_points = tuple(
            scene._bond_polydata.GetPoint(index)
            for index in range(scene._bond_polydata.GetNumberOfPoints())
        )
        orders = workspace.bond_display_orders
        connectivity = workspace.connectivity

        for scale in (0.5, 2.0):
            with self.subTest(scale=scale):
                self.window._apply_view_preferences(
                    ViewPreferences(
                        bond_thickness_scale=scale,
                        show_element_labels=True,
                        hide_hydrogen=True,
                    )
                )
                self.assertAlmostEqual(
                    scene._tube_filter.GetRadius(),
                    BOND_TUBE_RADIUS * scale,
                )
                self.assertEqual(scene._bond_polydata.GetNumberOfLines(), 6)
                self.assertEqual(
                    tuple(
                        scene._bond_polydata.GetPoint(index)
                        for index in range(scene._bond_polydata.GetNumberOfPoints())
                    ),
                    baseline_points,
                )
                self.assertIs(workspace.connectivity, connectivity)
                self.assertIs(workspace.bond_display_orders, orders)
                self.assertEqual(
                    tuple(record.order for record in orders),
                    (1, 2, 3),
                )

    def test_bond_detection_regenerates_xyz_only_and_preserves_mol_working_state(self) -> None:
        mol_path = self.root / "explicit.mol"
        xyz_path = self.root / "inferred.xyz"
        shutil.copyfile(FIXTURES / "double.mol", mol_path)
        xyz_path.write_text(
            "2\nInferred\nC 0.0 0.0 0.0\nO 1.23 0.0 0.0\n",
            encoding="utf-8",
        )
        mol_workspace = self.window._open_local_geometry(mol_path)
        xyz_workspace = self.window._open_local_geometry(xyz_path)
        mol_structure = mol_workspace.structure
        mol_connectivity = mol_workspace.connectivity
        mol_orders = mol_workspace.bond_display_orders

        class AcceptedBondDetectionDialog:
            def __init__(inner_self, current_factor, parent=None):
                self.assertEqual(current_factor, 1.10)
                self.assertIs(parent, self.window)
                inner_self._preview_callback = None
                inner_self.preview_factor_changed = inner_self

            def connect(inner_self, callback):
                inner_self._preview_callback = callback

            def exec(inner_self):
                inner_self._preview_callback(1.20)
                return QDialog.DialogCode.Accepted

            def selected_factor(inner_self):
                return 1.20

        with patch(
            "tools.molecule_viewer_demo.BondDetectionDialog",
            AcceptedBondDetectionDialog,
        ), patch(
            "tools.molecule_viewer_demo.infer_connectivity",
            wraps=infer_connectivity,
        ) as inference:
            self.window._open_bond_detection()
            self.application.processEvents()

        self.assertEqual(inference.call_count, 1)
        self.assertEqual(inference.call_args.kwargs["multiplier"], 1.20)
        self.assertIs(mol_workspace.structure, mol_structure)
        self.assertIs(mol_workspace.connectivity, mol_connectivity)
        self.assertEqual(mol_workspace.bond_display_orders, mol_orders)
        self.assertIs(
            mol_workspace.connectivity_source,
            ConnectivitySource.EXPLICIT,
        )
        self.assertIs(
            xyz_workspace.connectivity_source,
            ConnectivitySource.INFERRED,
        )

    def test_mol_metadata_is_workspace_local_and_path_identity_rules_are_unchanged(self) -> None:
        first_path = self.root / "one" / "molecule.mol"
        second_path = self.root / "two" / "molecule.mol"
        first_path.parent.mkdir()
        second_path.parent.mkdir()
        shutil.copyfile(FIXTURES / "double.mol", first_path)
        shutil.copyfile(FIXTURES / "triple.mol", second_path)

        first = self.window._open_local_geometry(first_path)
        duplicate = self.window._open_local_geometry(
            first_path.parent / "." / first_path.name
        )
        second = self.window._open_local_geometry(second_path)

        self.assertIs(duplicate, first)
        self.assertIsNot(second, first)
        self.assertEqual(self.window._workspace_tabs.count(), 2)
        self.assertEqual(first.display_title, second.display_title)
        self.assertNotEqual(first.identity, second.identity)
        self.window._focus_workspace(first)
        self.assertEqual(
            tuple(record.order for record in self.window._bond_display_orders),
            (2,),
        )
        self.assertEqual(
            self.window._viewer._scene._bond_polydata.GetNumberOfLines(),
            2,
        )
        self.window._focus_workspace(second)
        self.assertEqual(
            tuple(record.order for record in self.window._bond_display_orders),
            (3,),
        )
        self.assertEqual(
            self.window._viewer._scene._bond_polydata.GetNumberOfLines(),
            3,
        )

    def test_xyz_mol_and_transmission_tabs_coexist_without_state_or_action_leakage(self) -> None:
        xyz_path = self.root / "A.xyz"
        xyz_path.write_text(
            "2\nXYZ\nC 0.0 0.0 0.0\nC 1.34 0.0 0.0\n",
            encoding="utf-8",
        )
        xyz_workspace = self.window._open_local_geometry(xyz_path)
        mol_workspace = self.window._open_local_geometry(FIXTURES / "double.mol")
        transmission = self.window._open_transmission_workspace(
            TransmissionWorkspaceRequest.from_snapshot(
                _step4_success_snapshot()
            )
        )

        self.assertEqual(self.window._workspace_tabs.count(), 3)
        self.assertIs(self.window._active_workspace(), transmission)
        self.assertFalse(self.window._rotate_bond_action.isEnabled())
        self.window._focus_workspace(xyz_workspace)
        self.assertEqual(
            tuple(record.order for record in self.window._bond_display_orders),
            (1,),
        )
        self.assertEqual(
            self.window._viewer._scene._bond_polydata.GetNumberOfLines(),
            1,
        )
        self.window._focus_workspace(mol_workspace)
        self.assertEqual(
            tuple(record.order for record in self.window._bond_display_orders),
            (2,),
        )
        self.assertEqual(
            self.window._viewer._scene._bond_polydata.GetNumberOfLines(),
            2,
        )
        self.assertTrue(self.window._rotate_bond_action.isEnabled())

    def test_coordinate_edit_and_torsion_eligibility_preserve_explicit_orders(self) -> None:
        self.window._open_local_geometry(FIXTURES / "mixed.mol")
        orders = self.window._bond_display_orders
        connectivity = self.window._connectivity
        session = BondTorsionSession(self.window._structure, connectivity, 1, 2)
        self.assertEqual(session.selected_edge, (1, 2))
        translated = MolecularStructure(
            tuple(
                Atom(atom.index, atom.element, atom.x, atom.y, atom.z + 0.25)
                for atom in self.window._structure
            ),
            comment=self.window._structure.comment,
        )

        self.window._apply_torsion_structure(translated)

        self.assertIs(self.window._connectivity, connectivity)
        self.assertEqual(self.window._bond_display_orders, orders)
        self.assertEqual(
            self.window._viewer._scene._bond_display_orders,
            orders,
        )
        self.assertEqual(
            self.window._viewer._scene._bond_polydata.GetNumberOfLines(),
            6,
        )

    def test_loaded_mol_keeps_frozen_native_900_by_650_controls_visible(self) -> None:
        self.window._open_local_geometry(FIXTURES / "mixed.mol")
        self.window.resize(900, 650)
        self.application.processEvents()

        self.assertEqual((self.window.width(), self.window.height()), (900, 650))
        for widget in (
            self.window._distance_measure_button,
            self.window._angle_measure_button,
            self.window._rotate_bond_button,
            self.window._geometry_undo_button,
            self.window._geometry_redo_button,
        ):
            self.assertTrue(widget.isVisibleTo(self.window))
        self.assertTrue(self.window.menuBar().isVisibleTo(self.window))


if __name__ == "__main__":
    unittest.main()
