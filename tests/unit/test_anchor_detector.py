import unittest
from pathlib import Path

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.structure.anchor_detector import (
    detect_anchors,
    paired_dicyano_groups,
)
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.xyz import read_xyz


REFERENCE_XYZ_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase1b"
    / "synthetic_dual_ncs.xyz"
)


def topology(
    elements: tuple[str, ...],
    pairs: tuple[tuple[int, int], ...],
) -> tuple[MolecularStructure, Connectivity]:
    structure = MolecularStructure(
        atoms=tuple(
            Atom(index, element, float(index), 0.0, 0.0)
            for index, element in enumerate(elements)
        )
    )
    connectivity = Connectivity(
        atom_count=len(structure),
        bonds=tuple(Bond(first, second, 1.0) for first, second in pairs),
    )
    return structure, connectivity


class AnchorCandidateTests(unittest.TestCase):
    def test_stores_atom_indexes_in_deterministic_order(self) -> None:
        candidate = AnchorCandidate(
            AnchorKind.SMe,
            3,
            (5, 3, 4),
            attached_au_indices=(8, 6),
        )

        self.assertEqual(candidate.atom_indices, (3, 4, 5))
        self.assertEqual(candidate.attached_au_indices, (6, 8))

    def test_attached_au_indexes_default_to_empty(self) -> None:
        candidate = AnchorCandidate(AnchorKind.SH, 1, (1, 2))

        self.assertEqual(candidate.attached_au_indices, ())

    def test_rejects_duplicate_atom_indexes(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            AnchorCandidate(AnchorKind.SH, 1, (1, 1))

    def test_requires_binding_atom_to_belong_to_group(self) -> None:
        with self.assertRaisesRegex(ValueError, "must belong"):
            AnchorCandidate(AnchorKind.SH, 1, (2, 3))

    def test_rejects_duplicate_attached_au_indexes(self) -> None:
        with self.assertRaisesRegex(ValueError, "attached Au indexes must be unique"):
            AnchorCandidate(AnchorKind.SH, 1, (1,), (3, 3))

    def test_rejects_invalid_attached_au_index(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            AnchorCandidate(AnchorKind.SH, 1, (1,), (-1,))

    def test_rejects_attached_au_overlap_with_anchor_group(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            AnchorCandidate(AnchorKind.SH, 1, (1, 2), (2,))


class DetectAnchorsTests(unittest.TestCase):
    def test_reference_molecule_has_exactly_two_ncs_anchors(self) -> None:
        structure = read_xyz(REFERENCE_XYZ_PATH)
        connectivity = infer_connectivity(
            structure,
            load_default_covalent_radii(),
        )

        candidates = detect_anchors(structure, connectivity)

        self.assertEqual(
            candidates,
            (
                AnchorCandidate(AnchorKind.NCS, 8, (6, 7, 8)),
                AnchorCandidate(AnchorKind.NCS, 11, (9, 10, 11)),
            ),
        )

    def test_detects_explicit_nh2_topology(self) -> None:
        structure, connectivity = topology(
            ("C", "N", "H", "H"),
            ((0, 1), (1, 2), (1, 3)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.NH2, 1, (1, 2, 3)),),
        )

    def test_detects_explicit_sh_topology(self) -> None:
        structure, connectivity = topology(
            ("C", "S", "H"),
            ((0, 1), (1, 2)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.SH, 1, (1, 2)),),
        )

    def test_detects_explicit_sme_topology(self) -> None:
        structure, connectivity = topology(
            ("C", "S", "C", "H", "H", "H"),
            ((0, 1), (1, 2), (2, 3), (2, 4), (2, 5)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.SMe, 1, (1, 2, 3, 4, 5)),),
        )

    def test_detects_sme_with_germanium_backbone(self) -> None:
        structure, connectivity = topology(
            ("Ge", "S", "C", "H", "H", "H"),
            ((0, 1), (1, 2), (2, 3), (2, 4), (2, 5)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.SMe, 1, (1, 2, 3, 4, 5)),),
        )

    def test_rejects_ambiguous_dimethyl_sulfide_topology(self) -> None:
        structure, connectivity = topology(
            ("S", "C", "H", "H", "H", "C", "H", "H", "H"),
            (
                (0, 1),
                (0, 5),
                (1, 2),
                (1, 3),
                (1, 4),
                (5, 6),
                (5, 7),
                (5, 8),
            ),
        )

        self.assertEqual(detect_anchors(structure, connectivity), ())

    def test_detects_ncs_with_directly_attached_au(self) -> None:
        structure, connectivity = topology(
            ("C", "N", "C", "S", "Au"),
            ((0, 1), (1, 2), (2, 3), (3, 4)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.NCS, 3, (1, 2, 3), (4,)),),
        )

    def test_detects_nh2_with_directly_attached_au(self) -> None:
        structure, connectivity = topology(
            ("C", "N", "H", "H", "Au"),
            ((0, 1), (1, 2), (1, 3), (1, 4)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.NH2, 1, (1, 2, 3), (4,)),),
        )

    def test_detects_germanium_sme_with_directly_attached_au(self) -> None:
        structure, connectivity = topology(
            ("Ge", "S", "C", "H", "H", "H", "Au"),
            ((0, 1), (1, 2), (2, 3), (2, 4), (2, 5), (1, 6)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.SMe, 1, (1, 2, 3, 4, 5), (6,)),),
        )

    def test_detects_sh_with_hydrogen_and_directly_attached_au(self) -> None:
        structure, connectivity = topology(
            ("C", "S", "H", "Au"),
            ((0, 1), (1, 2), (1, 3)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.SH, 1, (1, 2), (3,)),),
        )

    def test_detects_dehydrogenated_au_bound_sh_workflow_topology(self) -> None:
        structure, connectivity = topology(
            ("C", "S", "Au"),
            ((0, 1), (1, 2)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.SH, 1, (1,), (2,)),),
        )

    def test_detects_pyridine_n_topology(self) -> None:
        structure, connectivity = topology(
            ("N", "C", "C", "C", "C", "C"),
            ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(
                    AnchorKind.PYRIDINE_N,
                    0,
                    (0, 1, 2, 3, 4, 5),
                ),
            ),
        )

    def test_detects_pyridine_n_with_directly_attached_au(self) -> None:
        structure, connectivity = topology(
            ("N", "C", "C", "C", "C", "C", "Au"),
            (
                (0, 1),
                (1, 2),
                (2, 3),
                (3, 4),
                (4, 5),
                (0, 5),
                (0, 6),
            ),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(
                    AnchorKind.PYRIDINE_N,
                    0,
                    (0, 1, 2, 3, 4, 5),
                    (6,),
                ),
            ),
        )

    def test_pyridine_n_uses_lexicographically_smallest_valid_ring(self) -> None:
        structure, connectivity = topology(
            ("N", "C", "C", "C", "C", "C", "C", "C", "C"),
            (
                (0, 1),
                (0, 5),
                (1, 2),
                (2, 3),
                (3, 4),
                (4, 5),
                (1, 6),
                (6, 7),
                (7, 8),
                (8, 5),
            ),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(
                    AnchorKind.PYRIDINE_N,
                    0,
                    (0, 1, 2, 3, 4, 5),
                ),
            ),
        )

    def test_au_not_connected_to_binding_atom_is_not_recorded(self) -> None:
        structure, connectivity = topology(
            ("N", "C", "C", "C", "C", "C", "Au"),
            (
                (0, 1),
                (1, 2),
                (2, 3),
                (3, 4),
                (4, 5),
                (0, 5),
                (3, 6),
            ),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(
                    AnchorKind.PYRIDINE_N,
                    0,
                    (0, 1, 2, 3, 4, 5),
                ),
            ),
        )

    def test_detects_free_terminal_alkynyl_with_explicit_hydrogen(self) -> None:
        structure, connectivity = topology(
            ("C", "C", "C", "H"),
            ((0, 1), (1, 2), (2, 3)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(
                    AnchorKind.ALKYNYL_C,
                    2,
                    (1, 2, 3),
                ),
            ),
        )

    def test_free_terminal_alkynyl_requires_explicit_hydrogen(self) -> None:
        structure, connectivity = topology(
            ("C", "C", "C"),
            ((0, 1), (1, 2)),
        )

        self.assertEqual(detect_anchors(structure, connectivity), ())

    def test_detects_au_bound_alkynyl_continuity_form(self) -> None:
        structure, connectivity = topology(
            ("C", "C", "C", "Au"),
            ((0, 1), (1, 2), (2, 3)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(
                    AnchorKind.ALKYNYL_C,
                    2,
                    (1, 2),
                    (3,),
                ),
            ),
        )

    def test_detects_unoccupied_cyano_n(self) -> None:
        structure, connectivity = topology(
            ("N", "C", "C"),
            ((0, 1), (1, 2)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.CYANO_N, 0, (0, 1)),),
        )

    def test_detects_au_bound_cyano_n(self) -> None:
        structure, connectivity = topology(
            ("Au", "N", "C", "C"),
            ((0, 1), (1, 2), (2, 3)),
        )

        self.assertEqual(
            detect_anchors(structure, connectivity),
            (AnchorCandidate(AnchorKind.CYANO_N, 1, (1, 2), (0,)),),
        )

    def test_single_dicyano_group_degrades_to_two_cyano_n_sites(self) -> None:
        structure, connectivity = topology(
            ("S", "C", "C", "N", "C", "N"),
            ((0, 1), (1, 2), (2, 3), (1, 4), (4, 5)),
        )

        self.assertEqual(paired_dicyano_groups(structure, connectivity), ())
        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(AnchorKind.CYANO_N, 3, (2, 3)),
                AnchorCandidate(AnchorKind.CYANO_N, 5, (4, 5)),
            ),
        )

    def test_two_dicyano_groups_add_centers_without_replacing_cyano_n(self) -> None:
        structure, connectivity = topology(
            (
                "S", "C", "C", "N", "C", "N", "O",
                "C", "C", "N", "C", "N", "Au", "Au",
            ),
            (
                (0, 1), (1, 2), (2, 3), (1, 4), (4, 5), (1, 12),
                (6, 7), (7, 8), (8, 9), (7, 10), (10, 11), (7, 13),
            ),
        )

        groups = paired_dicyano_groups(structure, connectivity)

        self.assertEqual(
            tuple(
                (
                    group.center_carbon_index,
                    group.reference_atom_index,
                    group.cyano_pairs,
                )
                for group in groups
            ),
            (
                (1, 0, ((2, 3), (4, 5))),
                (7, 6, ((8, 9), (10, 11))),
            ),
        )
        self.assertEqual(
            detect_anchors(structure, connectivity),
            (
                AnchorCandidate(AnchorKind.DICYANO_C, 1, (1, 2, 3, 4, 5), (12,)),
                AnchorCandidate(AnchorKind.CYANO_N, 3, (2, 3)),
                AnchorCandidate(AnchorKind.CYANO_N, 5, (4, 5)),
                AnchorCandidate(AnchorKind.DICYANO_C, 7, (7, 8, 9, 10, 11), (13,)),
                AnchorCandidate(AnchorKind.CYANO_N, 9, (8, 9)),
                AnchorCandidate(AnchorKind.CYANO_N, 11, (10, 11)),
            ),
        )

    def test_dicyano_center_rejects_hydrogen_as_third_neighbor(self) -> None:
        structure, connectivity = topology(
            (
                "H", "C", "C", "N", "C", "N", "O",
                "C", "C", "N", "C", "N",
            ),
            (
                (0, 1), (1, 2), (2, 3), (1, 4), (4, 5),
                (6, 7), (7, 8), (8, 9), (7, 10), (10, 11),
            ),
        )

        self.assertEqual(paired_dicyano_groups(structure, connectivity), ())
        self.assertEqual(
            tuple(anchor.kind for anchor in detect_anchors(structure, connectivity)),
            (AnchorKind.CYANO_N,) * 4,
        )

    def test_dicyano_center_requires_exactly_three_non_au_neighbors(self) -> None:
        structure, connectivity = topology(
            (
                "S", "C", "C", "N", "C", "N", "F", "O",
                "C", "C", "N", "C", "N",
            ),
            (
                (0, 1), (1, 2), (2, 3), (1, 4), (4, 5), (1, 6),
                (7, 8), (8, 9), (9, 10), (8, 11), (11, 12),
            ),
        )

        self.assertEqual(paired_dicyano_groups(structure, connectivity), ())
        self.assertFalse(
            any(
                anchor.kind is AnchorKind.DICYANO_C
                for anchor in detect_anchors(structure, connectivity)
            )
        )

    def test_more_than_two_dicyano_groups_do_not_activate_centers(self) -> None:
        elements = ("O", "C", "C", "N", "C", "N") * 3
        pairs = tuple(
            (offset + first, offset + second)
            for offset in (0, 6, 12)
            for first, second in ((0, 1), (1, 2), (2, 3), (1, 4), (4, 5))
        )
        structure, connectivity = topology(elements, pairs)

        self.assertEqual(paired_dicyano_groups(structure, connectivity), ())
        anchors = detect_anchors(structure, connectivity)
        self.assertEqual(len(anchors), 6)
        self.assertTrue(all(anchor.kind is AnchorKind.CYANO_N for anchor in anchors))


if __name__ == "__main__":
    unittest.main()
