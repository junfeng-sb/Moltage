import unittest

from vtkmodules.vtkRenderingCore import vtkRenderer

from moltage.domain.bond_display import BondDisplayOrder
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.visualization.molecule_scene import (
    MULTIPLE_BOND_STRAND_SEPARATION,
    MoleculeScene,
    TorsionGizmo,
    bond_strand_segments,
)


class MultipleBondGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "C", 1.4, 0.0, 0.0),
                Atom(2, "N", 2.7, 0.0, 0.0),
                Atom(3, "O", 3.8, 0.0, 0.0),
            )
        )
        self.connectivity = Connectivity(
            4,
            (
                Bond(0, 1, 1.4),
                Bond(1, 2, 1.3),
                Bond(2, 3, 1.1),
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
            {"C": 0.76, "N": 0.71, "O": 0.66},
            self.orders,
        )

    def test_single_double_triple_use_one_two_three_strands_but_three_logical_edges(self) -> None:
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 6)
        self.assertEqual(self.scene._bond_edges, ((0, 1), (1, 2), (2, 3)))
        logical_ids = self.scene._bond_polydata.GetCellData().GetArray(
            "bond_index"
        )
        self.assertEqual(
            tuple(
                int(logical_ids.GetValue(index))
                for index in range(logical_ids.GetNumberOfTuples())
            ),
            (0, 1, 1, 2, 2, 2),
        )

    def test_coordinate_only_update_preserves_orders_and_rebuilds_all_strands(self) -> None:
        translated = MolecularStructure(
            tuple(
                Atom(atom.index, atom.element, atom.x, atom.y + 2.0, atom.z)
                for atom in self.structure
            )
        )

        self.scene.update_molecule_coordinates(translated)

        self.assertEqual(self.scene._bond_display_orders, self.orders)
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 6)
        self.assertTrue(
            all(
                abs(self.scene._bond_polydata.GetPoint(index)[1] - 2.0)
                < 1.0e-12
                for index in range(
                    self.scene._bond_polydata.GetNumberOfPoints()
                )
            )
        )

    def test_selected_multiple_bond_highlights_every_strand(self) -> None:
        reference = self.scene.reference_direction_for_bond(2, 3)

        self.scene.set_torsion_gizmo(TorsionGizmo(2, 3, reference, 0.0))

        self.assertEqual(
            self.scene._selected_bond_polydata.GetNumberOfLines(),
            3,
        )

    def test_default_metadata_keeps_xyz_style_connectivity_visually_single(self) -> None:
        scene = MoleculeScene(vtkRenderer())
        scene.set_molecule(
            self.structure,
            self.connectivity,
            {"C": 0.76, "N": 0.71, "O": 0.66},
        )
        self.assertEqual(scene._bond_polydata.GetNumberOfLines(), 3)
        self.assertEqual(
            tuple(record.order for record in scene._bond_display_orders),
            (1, 1, 1),
        )

    def test_parallel_offsets_are_deterministic_symmetric_and_camera_independent(self) -> None:
        start = (0.0, 0.0, 0.0)
        end = (2.0, 0.0, 0.0)

        double = bond_strand_segments(start, end, 2)
        triple = bond_strand_segments(start, end, 3)
        self.renderer.GetActiveCamera().SetPosition(5.0, 7.0, 9.0)

        self.assertEqual(double, bond_strand_segments(start, end, 2))
        self.assertEqual(triple, bond_strand_segments(start, end, 3))
        self.assertAlmostEqual(double[0][0][2], -MULTIPLE_BOND_STRAND_SEPARATION / 2)
        self.assertAlmostEqual(double[1][0][2], MULTIPLE_BOND_STRAND_SEPARATION / 2)
        self.assertEqual(triple[1], (start, end))
        self.assertAlmostEqual(
            sum(segment[0][2] for segment in triple),
            0.0,
        )
        for segment in double + triple:
            self.assertEqual(
                tuple(
                    segment[1][axis] - segment[0][axis]
                    for axis in range(3)
                ),
                (2.0, 0.0, 0.0),
            )


if __name__ == "__main__":
    unittest.main()
