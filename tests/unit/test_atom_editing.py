import unittest

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.structure.atom_editing import (
    AtomEditingError,
    delete_atom,
    replace_atom,
)


class AtomEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "N", 1.0, 0.0, 0.0),
                Atom(2, "O", 2.0, 0.0, 0.0),
                Atom(3, "H", 3.0, 0.0, 0.0),
            ),
            comment="editable",
        )
        self.connectivity = Connectivity(
            4,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.0),
                Bond(2, 3, 1.0),
            ),
        )

    def test_delete_removes_incident_edges_and_compacts_indexes(self) -> None:
        result = delete_atom(self.structure, self.connectivity, 1)

        self.assertEqual(
            tuple((atom.index, atom.element) for atom in result.structure),
            ((0, "C"), (1, "O"), (2, "H")),
        )
        self.assertEqual(result.old_to_new_indices, (0, None, 1, 2))
        self.assertEqual(
            tuple((bond.first_index, bond.second_index) for bond in result.connectivity),
            ((1, 2),),
        )
        self.assertEqual(result.structure.comment, "editable")

    def test_deleting_the_only_atom_produces_an_empty_structure(self) -> None:
        result = delete_atom(
            MolecularStructure((Atom(0, "Au", 0.0, 0.0, 0.0),)),
            Connectivity(1, ()),
            0,
        )

        self.assertEqual(len(result.structure), 0)
        self.assertEqual(result.connectivity, Connectivity(0, ()))
        self.assertEqual(result.old_to_new_indices, (None,))

    def test_replace_preserves_coordinates_indexes_and_connectivity(self) -> None:
        result = replace_atom(self.structure, self.connectivity, 1, "Ru")

        self.assertEqual(result.structure[1], Atom(1, "Ru", 1.0, 0.0, 0.0))
        self.assertEqual(result.connectivity, self.connectivity)
        self.assertEqual(result.old_to_new_indices, (0, 1, 2, 3))

    def test_invalid_index_is_explicit(self) -> None:
        with self.assertRaises(AtomEditingError):
            delete_atom(self.structure, self.connectivity, 4)


if __name__ == "__main__":
    unittest.main()
