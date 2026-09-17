import unittest

from moltage.domain.connectivity import Bond, Connectivity


class BondTests(unittest.TestCase):
    def test_indexes_are_stored_in_canonical_order(self) -> None:
        bond = Bond(first_index=2, second_index=0, distance=1.2)

        self.assertEqual((bond.first_index, bond.second_index), (0, 2))

    def test_self_connection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "two different atom indexes"):
            Bond(first_index=1, second_index=1, distance=0.0)

    def test_negative_index_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            Bond(first_index=-1, second_index=1, distance=0.5)

    def test_negative_distance_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            Bond(first_index=0, second_index=1, distance=-0.1)


class ConnectivityTests(unittest.TestCase):
    def test_bonds_are_sorted_and_iterable(self) -> None:
        later = Bond(first_index=1, second_index=2, distance=1.0)
        earlier = Bond(first_index=0, second_index=1, distance=1.0)

        connectivity = Connectivity(atom_count=3, bonds=(later, earlier))

        self.assertEqual(tuple(connectivity), (earlier, later))
        self.assertEqual(len(connectivity), 2)

    def test_duplicate_undirected_pair_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate bond"):
            Connectivity(
                atom_count=2,
                bonds=(
                    Bond(0, 1, 1.0),
                    Bond(1, 0, 1.0),
                ),
            )

    def test_out_of_range_bond_index_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            Connectivity(
                atom_count=2,
                bonds=(Bond(0, 2, 1.0),),
            )


if __name__ == "__main__":
    unittest.main()
