import unittest

from moltage.domain.bond_display import (
    BondDisplayOrder,
    extend_bond_display_orders,
    remap_bond_display_orders,
    single_bond_display_orders,
    validate_bond_display_orders,
)
from moltage.domain.connectivity import Bond, Connectivity


class BondDisplayMetadataTests(unittest.TestCase):
    def test_metadata_is_separate_and_must_cover_connectivity_exactly(self) -> None:
        connectivity = Connectivity(3, (Bond(0, 1, 1.2), Bond(1, 2, 1.3)))
        records = (
            BondDisplayOrder(0, 1, 2),
            BondDisplayOrder(1, 2, 3),
        )

        self.assertEqual(validate_bond_display_orders(connectivity, records), records)
        self.assertFalse(hasattr(connectivity.bonds[0], "order"))
        with self.assertRaisesRegex(ValueError, "match logical connectivity"):
            validate_bond_display_orders(connectivity, records[:1])

    def test_authoritative_reindex_mapping_preserves_survivors_and_sets_new_edges_single(self) -> None:
        source = Connectivity(
            4,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.0),
                Bond(2, 3, 1.0),
            ),
        )
        orders = (
            BondDisplayOrder(0, 1, 2),
            BondDisplayOrder(1, 2, 1),
            BondDisplayOrder(2, 3, 3),
        )
        result = Connectivity(
            4,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.0),
                Bond(2, 3, 2.3),
            ),
        )

        remapped = remap_bond_display_orders(
            source,
            orders,
            result,
            (0, 1, None, 2),
        )

        self.assertEqual(
            tuple((record.edge, record.order) for record in remapped),
            (((0, 1), 2), ((1, 2), 1), ((2, 3), 1)),
        )

    def test_electrode_style_extension_preserves_source_and_makes_added_edges_single(self) -> None:
        source = Connectivity(2, (Bond(0, 1, 1.2),))
        result = Connectivity(
            4,
            (
                Bond(0, 1, 1.2),
                Bond(1, 2, 2.3),
                Bond(2, 3, 2.8),
            ),
        )

        extended = extend_bond_display_orders(
            source,
            (BondDisplayOrder(0, 1, 3),),
            result,
        )

        self.assertEqual(
            tuple(record.order for record in extended),
            (3, 1, 1),
        )

    def test_xyz_style_default_is_visual_single_for_every_edge(self) -> None:
        connectivity = Connectivity(3, (Bond(0, 2, 2.0), Bond(0, 1, 1.0)))
        self.assertEqual(
            single_bond_display_orders(connectivity),
            (
                BondDisplayOrder(0, 1, 1),
                BondDisplayOrder(0, 2, 1),
            ),
        )


if __name__ == "__main__":
    unittest.main()
