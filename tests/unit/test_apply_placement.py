import math
import unittest

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.junction import AppliedAuPlacement, AuPlacementProposal
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.apply_placement import (
    AuPlacementApplicationError,
    apply_au_placements,
)
from moltage.junction.au_placement import propose_au_placement
from moltage.junction.placement_defaults import (
    load_default_au_placement_defaults,
)
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.vdw_radii import load_default_vdw_radii


def system(
    atom_data: tuple[tuple[str, float, float, float], ...],
    pairs: tuple[tuple[int, int], ...],
) -> tuple[MolecularStructure, Connectivity]:
    structure = MolecularStructure(
        tuple(
            Atom(index, element, x, y, z)
            for index, (element, x, y, z) in enumerate(atom_data)
        )
    )
    connectivity = Connectivity(
        len(structure),
        tuple(
            Bond(
                first,
                second,
                math.dist(
                    atom_data[first][1:],
                    atom_data[second][1:],
                ),
            )
            for first, second in pairs
        ),
    )
    return structure, connectivity


def pairs(connectivity: Connectivity) -> tuple[tuple[int, int], ...]:
    return tuple(
        (bond.first_index, bond.second_index) for bond in connectivity
    )


class AppliedAuPlacementInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure, self.connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("Au", 2.0, 0.0, 0.0)),
            ((0, 1),),
        )

    def test_accepts_complete_mapping_and_added_au_partition(self) -> None:
        result = AppliedAuPlacement(
            self.structure,
            self.connectivity,
            (0,),
            (),
            (1,),
        )

        self.assertEqual(result.old_to_new_indices, (0,))
        self.assertEqual(result.removed_atom_indices, ())
        self.assertEqual(result.added_au_indices, (1,))

    def test_rejects_connectivity_count_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "atom count"):
            AppliedAuPlacement(
                self.structure,
                Connectivity(1, ()),
                (0,),
                (),
                (1,),
            )

    def test_rejects_invalid_mapping_and_removal_correspondence(self) -> None:
        invalid_cases = (
            ((0, 0), (), (1,), "mapped indexes"),
            ((0, None), (), (1,), "exactly"),
            ((0,), (0,), (1,), "exactly"),
            ((2,), (), (1,), "outside"),
        )
        for mapping, removed, added, message in invalid_cases:
            with self.subTest(mapping=mapping, removed=removed):
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    AppliedAuPlacement(
                        self.structure,
                        self.connectivity,
                        mapping,
                        removed,
                        added,
                    )
        with self.assertRaisesRegex(TypeError, "mapped index must be an integer"):
            AppliedAuPlacement(
                self.structure,
                self.connectivity,
                ("0",),
                (),
                (1,),
            )

    def test_rejects_invalid_or_duplicate_added_au_indexes(self) -> None:
        for added, message in (((2,), "outside"), ((1, 1), "unique")):
            with self.subTest(added=added):
                with self.assertRaisesRegex(ValueError, message):
                    AppliedAuPlacement(
                        self.structure,
                        self.connectivity,
                        (0,),
                        (),
                        added,
                    )


class ApplyAuPlacementsTests(unittest.TestCase):
    def test_no_removal_preserves_source_and_appends_exact_au(self) -> None:
        structure, connectivity = system(
            (
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", 2.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.CYANO_N, 0, (0, 1)),
            0.12345678901234566,
            -2.25,
            3.5,
        )
        source_atoms = structure.atoms
        source_bonds = connectivity.bonds

        result = apply_au_placements(structure, connectivity, (proposal,))

        self.assertIs(structure.atoms, source_atoms)
        self.assertIs(connectivity.bonds, source_bonds)
        self.assertIsNot(result.structure, structure)
        self.assertIsNot(result.connectivity, connectivity)
        self.assertTrue(
            all(
                result.structure[index] is not structure[index]
                for index in range(len(structure))
            )
        )
        self.assertEqual(
            tuple((atom.element, atom.x, atom.y, atom.z) for atom in result.structure),
            (
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", 2.0, 0.0, 0.0),
                ("Au", proposal.x, proposal.y, proposal.z),
            ),
        )
        self.assertEqual(result.old_to_new_indices, (0, 1, 2))
        self.assertEqual(result.removed_atom_indices, ())
        self.assertEqual(result.added_au_indices, (3,))
        self.assertEqual(pairs(result.connectivity), ((0, 1), (0, 3), (1, 2)))
        new_bond = next(
            bond
            for bond in result.connectivity
            if (bond.first_index, bond.second_index) == (0, 3)
        )
        self.assertEqual(
            new_bond.distance,
            math.dist((0.0, 0.0, 0.0), (proposal.x, proposal.y, proposal.z)),
        )

    def test_sh_removal_reindexes_and_redetects_attached_au(self) -> None:
        structure, connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
                ("H", 1.0, 1.0, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.SH, 1, (1, 2)),
            2.25,
            -0.75,
            0.125,
            remove_atom_indices=(2,),
        )

        result = apply_au_placements(structure, connectivity, (proposal,))

        self.assertEqual(result.old_to_new_indices, (0, 1, None))
        self.assertEqual(result.removed_atom_indices, (2,))
        self.assertEqual(result.added_au_indices, (2,))
        self.assertEqual(tuple(atom.element for atom in result.structure), ("C", "S", "Au"))
        self.assertEqual(
            (result.structure[2].x, result.structure[2].y, result.structure[2].z),
            (proposal.x, proposal.y, proposal.z),
        )
        self.assertEqual(pairs(result.connectivity), ((0, 1), (1, 2)))
        self.assertEqual(
            detect_anchors(result.structure, result.connectivity),
            (AnchorCandidate(AnchorKind.SH, 1, (1,), (2,)),),
        )

    def test_alkynyl_removal_preserves_chain_and_redetects(self) -> None:
        structure, connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", 2.0, 0.0, 0.0),
                ("H", 3.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.ALKYNYL_C, 2, (1, 2, 3)),
            4.05,
            0.0,
            0.0,
            remove_atom_indices=(3,),
        )

        result = apply_au_placements(structure, connectivity, (proposal,))

        self.assertEqual(result.old_to_new_indices, (0, 1, 2, None))
        self.assertEqual(result.added_au_indices, (3,))
        self.assertEqual(tuple(atom.element for atom in result.structure), ("C", "C", "C", "Au"))
        self.assertEqual(pairs(result.connectivity), ((0, 1), (1, 2), (2, 3)))
        self.assertEqual(
            detect_anchors(result.structure, result.connectivity),
            (
                AnchorCandidate(
                    AnchorKind.ALKYNYL_C,
                    2,
                    (1, 2),
                    (3,),
                ),
            ),
        )

    def test_free_sh_and_alkynyl_require_one_scheduled_terminal_h(self) -> None:
        cases = (
            (
                AnchorCandidate(AnchorKind.SH, 1, (1, 2)),
                (
                    ("C", 0.0, 0.0, 0.0),
                    ("S", 1.0, 0.0, 0.0),
                    ("H", 2.0, 0.0, 0.0),
                ),
                ((0, 1), (1, 2)),
            ),
            (
                AnchorCandidate(AnchorKind.ALKYNYL_C, 2, (1, 2, 3)),
                (
                    ("C", 0.0, 0.0, 0.0),
                    ("C", 1.0, 0.0, 0.0),
                    ("C", 2.0, 0.0, 0.0),
                    ("H", 3.0, 0.0, 0.0),
                ),
                ((0, 1), (1, 2), (2, 3)),
            ),
        )
        for anchor, atom_data, bond_pairs in cases:
            with self.subTest(kind=anchor.kind):
                structure, connectivity = system(atom_data, bond_pairs)
                proposal = AuPlacementProposal(anchor, 5.0, 0.0, 0.0)

                with self.assertRaisesRegex(
                    AuPlacementApplicationError,
                    "exactly one scheduled terminal H removal",
                ):
                    apply_au_placements(structure, connectivity, (proposal,))

    def test_terminal_removal_must_be_the_directly_bonded_anchor_h(self) -> None:
        structure, connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
                ("H", 2.0, 0.0, 0.0),
                ("H", 4.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.SH, 1, (1, 2, 3)),
            5.0,
            0.0,
            0.0,
            remove_atom_indices=(3,),
        )

        with self.assertRaisesRegex(
            AuPlacementApplicationError,
            "directly connected to its binding atom",
        ):
            apply_au_placements(structure, connectivity, (proposal,))

    def test_ncs_application_redetects_attached_au(self) -> None:
        structure, connectivity = system(
            (
                ("C", -2.0, 0.0, 0.0),
                ("N", -1.0, 0.0, 0.0),
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.NCS, 3, (1, 2, 3)),
            3.34,
            0.0,
            0.0,
        )

        result = apply_au_placements(structure, connectivity, (proposal,))

        self.assertEqual(
            detect_anchors(result.structure, result.connectivity),
            (AnchorCandidate(AnchorKind.NCS, 3, (1, 2, 3), (4,)),),
        )

    def test_two_proposals_are_order_independent_and_use_original_indexes(self) -> None:
        structure, connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
                ("H", 1.0, 1.0, 0.0),
                ("N", 10.0, 0.0, 0.0),
                ("C", 11.0, 0.0, 0.0),
                ("C", 12.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (3, 4), (4, 5)),
        )
        sh = AuPlacementProposal(
            AnchorCandidate(AnchorKind.SH, 1, (1, 2)),
            2.0,
            -2.0,
            0.0,
            remove_atom_indices=(2,),
        )
        cyano = AuPlacementProposal(
            AnchorCandidate(AnchorKind.CYANO_N, 3, (3, 4)),
            8.0,
            0.0,
            0.0,
        )

        sorted_result = apply_au_placements(structure, connectivity, (sh, cyano))
        reverse_result = apply_au_placements(structure, connectivity, (cyano, sh))

        self.assertEqual(reverse_result, sorted_result)
        self.assertEqual(
            reverse_result.old_to_new_indices,
            (0, 1, None, 2, 3, 4),
        )
        self.assertEqual(reverse_result.removed_atom_indices, (2,))
        self.assertEqual(reverse_result.added_au_indices, (5, 6))
        self.assertEqual(
            tuple(
                (atom.x, atom.y, atom.z)
                for atom in reverse_result.structure
                if atom.element == "Au"
            ),
            ((sh.x, sh.y, sh.z), (cyano.x, cyano.y, cyano.z)),
        )
        self.assertIn((2, 6), pairs(reverse_result.connectivity))

    def test_real_preview_coordinates_are_copied_with_exact_float_equality(self) -> None:
        structure, connectivity = system(
            (
                ("C", -2.0, 0.0, 0.0),
                ("N", -1.0, 0.0, 0.0),
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3)),
        )
        anchor = AnchorCandidate(AnchorKind.NCS, 3, (1, 2, 3))
        defaults = load_default_au_placement_defaults()[AnchorKind.NCS]
        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=load_default_vdw_radii(),
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None

        result = apply_au_placements(structure, connectivity, (proposal,))
        added = result.structure[result.added_au_indices[0]]

        self.assertEqual((added.x, added.y, added.z), (proposal.x, proposal.y, proposal.z))

    def test_existing_source_au_remains_in_place_before_new_appended_au(self) -> None:
        structure, connectivity = system(
            (
                ("C", -3.0, 0.0, 0.0),
                ("Au", -2.0, 0.5, 0.25),
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", 2.0, 0.0, 0.0),
            ),
            ((0, 1), (2, 3), (3, 4)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.CYANO_N, 2, (2, 3)),
            -2.2,
            0.0,
            0.0,
        )

        result = apply_au_placements(structure, connectivity, (proposal,))

        self.assertEqual(result.structure[1], structure[1])
        self.assertEqual(result.structure[1].element, "Au")
        self.assertEqual(result.added_au_indices, (5,))
        self.assertEqual(result.structure[5].element, "Au")
        self.assertEqual(pairs(result.connectivity), ((0, 1), (2, 3), (2, 5), (3, 4)))

    def test_does_not_infer_extra_edges_near_new_au(self) -> None:
        structure, connectivity = system(
            (
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", 2.0, 0.0, 0.0),
                ("C", 0.1, 0.1, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.CYANO_N, 0, (0, 1)),
            0.1,
            0.1,
            0.1,
        )

        result = apply_au_placements(structure, connectivity, (proposal,))

        self.assertEqual(pairs(result.connectivity), ((0, 1), (0, 4), (1, 2)))
        self.assertNotIn((3, 4), pairs(result.connectivity))

    def test_rejects_invalid_counts_duplicates_occupied_and_conflicts(self) -> None:
        structure, connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
                ("H", 2.0, 0.0, 0.0),
                ("N", 5.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        first = AuPlacementProposal(
            AnchorCandidate(AnchorKind.SH, 1, (1, 2)),
            3.0,
            0.0,
            0.0,
            remove_atom_indices=(2,),
        )
        duplicate_binding = AuPlacementProposal(
            AnchorCandidate(AnchorKind.NCS, 1, (1,)),
            4.0,
            0.0,
            0.0,
        )
        conflict = AuPlacementProposal(
            AnchorCandidate(AnchorKind.CYANO_N, 2, (2, 3)),
            6.0,
            0.0,
            0.0,
        )
        occupied = AuPlacementProposal(
            AnchorCandidate(AnchorKind.SH, 1, (1,), (3,)),
            3.0,
            0.0,
            0.0,
        )

        with self.assertRaisesRegex(AuPlacementApplicationError, "one or two"):
            apply_au_placements(structure, connectivity, ())
        with self.assertRaisesRegex(AuPlacementApplicationError, "one or two"):
            apply_au_placements(structure, connectivity, (first, first, first))
        with self.assertRaisesRegex(AuPlacementApplicationError, "unique binding"):
            apply_au_placements(
                structure,
                connectivity,
                (first, duplicate_binding),
            )
        with self.assertRaisesRegex(AuPlacementApplicationError, "already has"):
            apply_au_placements(structure, connectivity, (occupied,))
        with self.assertRaisesRegex(AuPlacementApplicationError, "binding atoms"):
            apply_au_placements(structure, connectivity, (first, conflict))

    def test_rejects_out_of_range_proposal_indexes(self) -> None:
        structure, connectivity = system(
            (("N", 0.0, 0.0, 0.0), ("C", 1.0, 0.0, 0.0)),
            ((0, 1),),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.CYANO_N, 0, (0, 2)),
            -2.0,
            0.0,
            0.0,
        )

        with self.assertRaisesRegex(AuPlacementApplicationError, "outside"):
            apply_au_placements(structure, connectivity, (proposal,))


if __name__ == "__main__":
    unittest.main()
