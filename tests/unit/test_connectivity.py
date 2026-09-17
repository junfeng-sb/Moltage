import unittest
from pathlib import Path

from moltage.domain.structure import Atom, MolecularStructure
from moltage.structure.connectivity import (
    DEFAULT_CONNECTIVITY_MULTIPLIER,
    UnsupportedElementError,
    infer_connectivity,
)
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.xyz import read_xyz


REFERENCE_XYZ_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase1b"
    / "synthetic_dual_ncs.xyz"
)


def two_carbon_structure(distance: float) -> MolecularStructure:
    return MolecularStructure(
        atoms=(
            Atom(0, "C", 0.0, 0.0, 0.0),
            Atom(1, "C", distance, 0.0, 0.0),
        )
    )


class InferConnectivityTests(unittest.TestCase):
    def test_threshold_controls_connection(self) -> None:
        at_threshold = infer_connectivity(
            two_carbon_structure(1.5),
            {"C": 0.75},
            multiplier=1.0,
        )
        above_threshold = infer_connectivity(
            two_carbon_structure(1.500001),
            {"C": 0.75},
            multiplier=1.0,
        )

        self.assertEqual(len(at_threshold), 1)
        self.assertAlmostEqual(at_threshold.bonds[0].distance, 1.5)
        self.assertEqual(len(above_threshold), 0)

    def test_multiplier_changes_connectivity(self) -> None:
        structure = two_carbon_structure(1.6)

        smaller = infer_connectivity(structure, {"C": 0.75}, multiplier=1.0)
        larger = infer_connectivity(structure, {"C": 0.75}, multiplier=1.1)

        self.assertEqual(len(smaller), 0)
        self.assertEqual(len(larger), 1)

    def test_packaged_carbon_radius_controls_the_default_cutoff(self) -> None:
        radii = load_default_covalent_radii()
        cutoff = DEFAULT_CONNECTIVITY_MULTIPLIER * 2.0 * radii["C"]

        at_cutoff = infer_connectivity(
            two_carbon_structure(cutoff),
            radii,
        )
        above_cutoff = infer_connectivity(
            two_carbon_structure(cutoff + 1.0e-6),
            radii,
        )

        self.assertEqual(len(at_cutoff), 1)
        self.assertEqual(len(above_cutoff), 0)

    def test_unsupported_element_is_reported_explicitly(self) -> None:
        structure = MolecularStructure(atoms=(Atom(0, "Xe", 0.0, 0.0, 0.0),))

        with self.assertRaisesRegex(UnsupportedElementError, "unsupported element"):
            infer_connectivity(structure, {"C": 0.76})

    def test_invalid_multiplier_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            infer_connectivity(two_carbon_structure(1.0), {"C": 0.75}, 0.0)

    def test_reference_molecule_has_stable_zero_based_edges(self) -> None:
        structure = read_xyz(REFERENCE_XYZ_PATH)

        connectivity = infer_connectivity(
            structure,
            load_default_covalent_radii(),
        )

        self.assertEqual(len(structure), 16)
        self.assertEqual(len(connectivity), 16)
        self.assertEqual(
            {(bond.first_index, bond.second_index) for bond in connectivity},
            {
                (0, 1),
                (0, 5),
                (0, 12),
                (1, 2),
                (1, 6),
                (2, 3),
                (2, 13),
                (3, 4),
                (3, 14),
                (4, 5),
                (4, 9),
                (5, 15),
                (6, 7),
                (7, 8),
                (9, 10),
                (10, 11),
            },
        )


if __name__ == "__main__":
    unittest.main()
