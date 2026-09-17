import unittest

from moltage.domain.structure import Atom, MolecularStructure


class MolecularStructureTests(unittest.TestCase):
    def test_preserves_zero_based_order_and_basic_access(self) -> None:
        atoms = (
            Atom(index=0, element="h", x=0, y=0, z=0),
            Atom(index=1, element="H", x=1, y=0, z=0),
        )

        structure = MolecularStructure(atoms=atoms, comment="hydrogen")

        self.assertEqual(len(structure), 2)
        self.assertEqual([atom.index for atom in structure], [0, 1])
        self.assertEqual([atom.element for atom in structure], ["H", "H"])
        self.assertIs(structure[0], atoms[0])
        self.assertIs(structure[1], atoms[1])
        self.assertEqual(structure.comment, "hydrogen")
        with self.assertRaises(IndexError):
            _ = structure[-1]

    def test_requires_atom_indexes_to_match_collection_positions(self) -> None:
        atom = Atom(index=1, element="H", x=0, y=0, z=0)

        with self.assertRaisesRegex(ValueError, "expected 0, found 1"):
            MolecularStructure(atoms=(atom,))


if __name__ == "__main__":
    unittest.main()
