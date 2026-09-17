import math
import unittest
from pathlib import Path

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.junction import AuPlacementProposal
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.apply_placement import apply_au_placements
from moltage.junction.au_placement import (
    AuPlacementError,
    AuPlacementParameters,
    _pyridine_mirror_directions,
    angle_reference_atom_index,
    angle_reference_atom_indices,
    propose_au_placement,
    propose_au_placements,
)
from moltage.junction.placement_defaults import (
    load_default_au_placement_defaults,
    load_default_dicyano_cyano_n_placement_defaults,
    load_default_ncs_placement_defaults,
    placement_defaults_for_anchor,
)
from moltage.junction.steric_orientation import (
    cone_orientation_candidates,
    select_single_orientation,
    steric_clearance_for_point,
    with_nh2_local_h_scores,
)
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import (
    load_default_covalent_radii,
)
from moltage.structure.vdw_radii import load_default_vdw_radii
from moltage.structure.xyz import read_xyz


REFERENCE_XYZ_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase1b"
    / "synthetic_dual_ncs.xyz"
)


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
        tuple(Bond(first, second, 1.0) for first, second in pairs),
    )
    return structure, connectivity


def dicyano_system() -> tuple[MolecularStructure, Connectivity]:
    return system(
        (
            ("S", -12.0, 0.0, 0.0),
            ("C", -10.0, 0.0, 0.0),
            ("C", -9.0, 0.7, 0.0),
            ("N", -8.0, 1.4, 0.0),
            ("C", -9.0, -0.7, 0.0),
            ("N", -8.0, -1.4, 0.0),
            ("O", 12.0, 0.0, 0.0),
            ("C", 10.0, 0.0, 0.0),
            ("C", 9.0, 0.7, 0.0),
            ("N", 8.0, 1.4, 0.0),
            ("C", 9.0, -0.7, 0.0),
            ("N", 8.0, -1.4, 0.0),
        ),
        (
            (0, 1), (1, 2), (2, 3), (1, 4), (4, 5),
            (6, 7), (7, 8), (8, 9), (7, 10), (10, 11),
        ),
    )


def coordinates(proposal: AuPlacementProposal) -> tuple[float, float, float]:
    return proposal.x, proposal.y, proposal.z


def coordinates_of(
    structure: MolecularStructure,
    atom_index: int,
) -> tuple[float, float, float]:
    atom = structure[atom_index]
    return atom.x, atom.y, atom.z


def vector(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        end_component - start_component
        for start_component, end_component in zip(start, end, strict=True)
    )


def dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(
        first_component * second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def angle_degrees(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    cosine = dot(first, second) / (math.hypot(*first) * math.hypot(*second))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


class AuPlacementProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.anchor = AnchorCandidate(AnchorKind.SH, 1, (1, 2), (4,))

    def test_normalizes_coordinates_and_remove_indexes(self) -> None:
        proposal = AuPlacementProposal(
            self.anchor,
            1,
            2,
            3,
            remove_atom_indices=(3, 2),
        )

        self.assertEqual((proposal.x, proposal.y, proposal.z), (1.0, 2.0, 3.0))
        self.assertEqual(proposal.remove_atom_indices, (2, 3))

    def test_rejects_non_finite_coordinates(self) -> None:
        with self.assertRaisesRegex(ValueError, "proposal x must be finite"):
            AuPlacementProposal(self.anchor, math.inf, 0.0, 0.0)

    def test_protects_binding_atom_and_attached_au_from_removal(self) -> None:
        with self.assertRaisesRegex(ValueError, "binding atom"):
            AuPlacementProposal(
                self.anchor,
                0.0,
                0.0,
                0.0,
                remove_atom_indices=(1,),
            )
        with self.assertRaisesRegex(ValueError, "attached Au"):
            AuPlacementProposal(
                self.anchor,
                0.0,
                0.0,
                0.0,
                remove_atom_indices=(4,),
            )


class AuPlacementGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vdw_radii = load_default_vdw_radii()
        cls.defaults = load_default_au_placement_defaults()

    def test_ncs_reproduces_approved_distance_and_angle(self) -> None:
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
        defaults = self.defaults[AnchorKind.NCS]

        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=self.vdw_radii,
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        sulfur = (1.0, 0.0, 0.0)
        sulfur_to_carbon = (-1.0, 0.0, 0.0)
        sulfur_to_au = vector(sulfur, coordinates(proposal))
        self.assertAlmostEqual(
            math.hypot(*sulfur_to_au),
            2.34,
            places=12,
        )
        self.assertAlmostEqual(
            angle_degrees(sulfur_to_carbon, sulfur_to_au),
            170.0,
            places=10,
        )
        self.assertEqual(
            angle_reference_atom_index(structure, connectivity, anchor),
            2,
        )

    def test_pyridine_n_has_two_exact_equal_angle_mirrors(self) -> None:
        half_sqrt_three = math.sqrt(3.0) / 2.0
        structure, connectivity = system(
            (
                ("N", 1.0, 0.0, 0.0),
                ("C", 0.5, half_sqrt_three, 0.0),
                ("C", -0.5, half_sqrt_three, 0.0),
                ("C", -1.0, 0.0, 0.0),
                ("C", -0.5, -half_sqrt_three, 0.0),
                ("C", 0.5, -half_sqrt_three, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)),
        )
        anchor = AnchorCandidate(AnchorKind.PYRIDINE_N, 0, range(6))

        defaults = self.defaults[AnchorKind.PYRIDINE_N]
        references = angle_reference_atom_indices(
            structure, connectivity, anchor
        )
        directions = _pyridine_mirror_directions(
            vector((1.0, 0.0, 0.0), coordinates_of(structure, references[0])),
            vector((1.0, 0.0, 0.0), coordinates_of(structure, references[1])),
            defaults.angle_degrees,
        )

        self.assertEqual(len(directions), 2)
        self.assertAlmostEqual(directions[0][2], -directions[1][2])
        self.assertGreater(abs(directions[0][2]), 0.0)
        for direction in directions:
            au = tuple(
                1.0 + defaults.distance_angstrom * direction[0]
                if component == 0
                else defaults.distance_angstrom * direction[component]
                for component in range(3)
            )
            n_to_au = vector((1.0, 0.0, 0.0), au)
            self.assertAlmostEqual(math.hypot(*n_to_au), 2.15, places=12)
            for reference in references:
                self.assertAlmostEqual(
                    angle_degrees(
                        vector(
                            (1.0, 0.0, 0.0),
                            coordinates_of(structure, reference),
                        ),
                        n_to_au,
                    ),
                    119.0,
                    places=10,
                )

    def test_pyridine_n_remote_obstacle_selects_open_mirror(self) -> None:
        half_sqrt_three = math.sqrt(3.0) / 2.0
        base_atoms = (
            ("N", 1.0, 0.0, 0.0),
            ("C", 0.5, half_sqrt_three, 0.0),
            ("C", -0.5, half_sqrt_three, 0.0),
            ("C", -1.0, 0.0, 0.0),
            ("C", -0.5, -half_sqrt_three, 0.0),
            ("C", 0.5, -half_sqrt_three, 0.0),
        )
        base_structure, base_connectivity = system(
            base_atoms,
            ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)),
        )
        anchor = AnchorCandidate(AnchorKind.PYRIDINE_N, 0, range(6))
        defaults = self.defaults[AnchorKind.PYRIDINE_N]
        references = angle_reference_atom_indices(
            base_structure, base_connectivity, anchor
        )
        directions = _pyridine_mirror_directions(
            vector((1.0, 0.0, 0.0), coordinates_of(base_structure, references[0])),
            vector((1.0, 0.0, 0.0), coordinates_of(base_structure, references[1])),
            defaults.angle_degrees,
        )
        blocked_point = tuple(
            (1.0, 0.0, 0.0)[component]
            + defaults.distance_angstrom * directions[0][component]
            for component in range(3)
        )
        structure, connectivity = system(
            base_atoms + (("C", *blocked_point),),
            ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (0, 5)),
        )

        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii={"Au": 0.1, "C": 0.1},
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        expected = tuple(
            (1.0, 0.0, 0.0)[component]
            + defaults.distance_angstrom * directions[1][component]
            for component in range(3)
        )
        for actual, wanted in zip(coordinates(proposal), expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=12)

    def test_pyridine_n_impossible_equal_angle_fails(self) -> None:
        angle = math.radians(160.0)
        structure, connectivity = system(
            (
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", math.cos(angle), math.sin(angle), 0.0),
            ),
            ((0, 1), (0, 2)),
        )
        anchor = AnchorCandidate(AnchorKind.PYRIDINE_N, 0, (0, 1, 2))
        defaults = self.defaults[AnchorKind.PYRIDINE_N]

        with self.assertRaisesRegex(AuPlacementError, "impossible"):
            propose_au_placement(
                structure,
                connectivity,
                anchor,
                defaults.distance_angstrom,
                defaults.angle_degrees,
                vdw_radii=self.vdw_radii,
            )

    def test_sh_uses_backbone_cone_and_records_future_h_removal(self) -> None:
        structure, connectivity = system(
            (
                ("C", 1.0, 0.0, 0.0),
                ("S", 0.0, 0.0, 0.0),
                ("H", -0.5, 1.0, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        anchor = AnchorCandidate(AnchorKind.SH, 1, (1, 2))

        defaults = self.defaults[AnchorKind.SH]
        original_atoms = structure.atoms
        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=self.vdw_radii,
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        s_to_au = coordinates(proposal)
        self.assertAlmostEqual(math.hypot(*s_to_au), 2.35)
        self.assertAlmostEqual(angle_degrees((1.0, 0.0, 0.0), s_to_au), 105.0)
        self.assertEqual(proposal.remove_atom_indices, (2,))
        self.assertIs(structure.atoms, original_atoms)

    def test_nh2_uses_backbone_axis_and_requested_cone(self) -> None:
        structure, connectivity = system(
            (
                ("C", 1.0, 0.0, 0.0),
                ("N", 0.0, 0.0, 0.0),
                ("H", 0.0, 1.0, 1.0),
                ("H", 0.0, 1.0, -1.0),
            ),
            ((0, 1), (1, 2), (1, 3)),
        )
        anchor = AnchorCandidate(AnchorKind.NH2, 1, (1, 2, 3))

        defaults = self.defaults[AnchorKind.NH2]
        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=self.vdw_radii,
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        n_to_au = coordinates(proposal)
        self.assertAlmostEqual(math.hypot(*n_to_au), 2.42)
        self.assertAlmostEqual(
            angle_degrees((1.0, 0.0, 0.0), n_to_au),
            120.0,
        )
        self.assertEqual(
            angle_reference_atom_index(structure, connectivity, anchor),
            0,
        )

    def test_sme_uses_methyl_sulfur_axis_for_c_or_ge_backbone(self) -> None:
        coordinates_by_index = (
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (1.0, 0.0, 1.0),
        )
        pairs = ((0, 1), (1, 2), (2, 3), (2, 4), (2, 5))
        anchor = AnchorCandidate(AnchorKind.SMe, 1, (1, 2, 3, 4, 5))
        proposals = []
        for backbone_element in ("Ge", "C"):
            elements = (backbone_element, "S", "C", "H", "H", "H")
            structure, connectivity = system(
                tuple(
                    (element, *coordinates_by_index[index])
                    for index, element in enumerate(elements)
                ),
                pairs,
            )
            defaults = self.defaults[AnchorKind.SMe]
            proposal = propose_au_placement(
                structure,
                connectivity,
                anchor,
                defaults.distance_angstrom,
                defaults.angle_degrees,
                vdw_radii=self.vdw_radii,
            )
            self.assertIsNotNone(proposal)
            assert proposal is not None
            proposals.append(proposal)
            s_to_au = coordinates(proposal)
            self.assertAlmostEqual(math.hypot(*s_to_au), 2.40)
            self.assertAlmostEqual(
                angle_degrees((1.0, 0.0, 0.0), s_to_au),
                100.0,
            )
            self.assertEqual(
                angle_reference_atom_index(structure, connectivity, anchor),
                2,
            )

        self.assertEqual(coordinates(proposals[0]), coordinates(proposals[1]))

    def test_alkynyl_c_uses_adjacent_carbon_cone_and_schedules_h(self) -> None:
        structure, connectivity = system(
            (
                ("C", -2.0, 0.0, 0.0),
                ("C", -1.0, 0.0, 0.0),
                ("C", 0.0, 0.0, 0.0),
                ("H", 1.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3)),
        )
        anchor = AnchorCandidate(AnchorKind.ALKYNYL_C, 2, (1, 2, 3))
        defaults = self.defaults[AnchorKind.ALKYNYL_C]
        original_atoms = structure.atoms

        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=self.vdw_radii,
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        binding_to_au = coordinates(proposal)
        self.assertAlmostEqual(math.hypot(*binding_to_au), 2.05)
        self.assertAlmostEqual(
            angle_degrees((-1.0, 0.0, 0.0), binding_to_au),
            179.0,
        )
        self.assertEqual(proposal.remove_atom_indices, (3,))
        self.assertIs(structure.atoms, original_atoms)

    def test_cyano_n_uses_carbon_reference_cone(self) -> None:
        structure, connectivity = system(
            (
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("C", 2.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2)),
        )
        anchor = AnchorCandidate(AnchorKind.CYANO_N, 0, (0, 1))
        defaults = self.defaults[AnchorKind.CYANO_N]

        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=self.vdw_radii,
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        n_to_au = coordinates(proposal)
        self.assertAlmostEqual(math.hypot(*n_to_au), 2.20)
        self.assertAlmostEqual(
            angle_degrees((1.0, 0.0, 0.0), n_to_au),
            179.0,
        )

    def test_paired_dicyano_n_uses_contextual_out_of_plane_defaults(self) -> None:
        structure, connectivity = dicyano_system()
        anchor = next(
            item
            for item in detect_anchors(structure, connectivity)
            if item.kind is AnchorKind.CYANO_N
        )
        defaults = placement_defaults_for_anchor(
            structure,
            connectivity,
            anchor,
            self.defaults,
            load_default_dicyano_cyano_n_placement_defaults(),
        )

        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii={"Au": 0.1, "C": 0.1, "N": 0.1, "O": 0.1, "S": 0.1},
        )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        nitrogen = coordinates_of(structure, anchor.binding_atom_index)
        carbon = coordinates_of(structure, anchor.atom_indices[0])
        if structure[anchor.atom_indices[0]].element != "C":
            carbon = coordinates_of(structure, anchor.atom_indices[1])
        n_to_au = vector(nitrogen, coordinates(proposal))
        self.assertAlmostEqual(math.hypot(*n_to_au), 2.00, places=12)
        self.assertAlmostEqual(
            angle_degrees(vector(nitrogen, carbon), n_to_au),
            121.0,
            places=10,
        )
        self.assertGreater(abs(n_to_au[2]) / math.hypot(*n_to_au), 0.85)

    def test_paired_dicyano_centers_are_symmetric_and_opposite_sided(self) -> None:
        structure, connectivity = dicyano_system()
        anchors = tuple(
            item
            for item in detect_anchors(structure, connectivity)
            if item.kind is AnchorKind.DICYANO_C
        )
        defaults = self.defaults[AnchorKind.DICYANO_C]
        parameters = tuple(
            AuPlacementParameters(
                anchor,
                defaults.distance_angstrom,
                defaults.angle_degrees,
            )
            for anchor in anchors
        )
        radii = {"Au": 0.1, "C": 0.1, "N": 0.1, "O": 0.1, "S": 0.1}

        proposals = propose_au_placements(
            structure,
            connectivity,
            parameters,
            vdw_radii=radii,
        )
        repeated = propose_au_placements(
            structure,
            connectivity,
            parameters,
            vdw_radii=radii,
        )

        self.assertEqual(proposals, repeated)
        self.assertEqual(len(proposals), 2)
        self.assertLess(proposals[0].z * proposals[1].z, 0.0)
        for anchor, proposal in zip(anchors, proposals, strict=True):
            center = coordinates_of(structure, anchor.binding_atom_index)
            reference_index = angle_reference_atom_index(
                structure,
                connectivity,
                anchor,
            )
            self.assertIsNotNone(reference_index)
            assert reference_index is not None
            center_to_au = vector(center, coordinates(proposal))
            self.assertAlmostEqual(math.hypot(*center_to_au), 2.10, places=12)
            self.assertAlmostEqual(
                angle_degrees(
                    vector(center, coordinates_of(structure, reference_index)),
                    center_to_au,
                ),
                111.0,
                places=10,
            )
            nitrogen_indices = tuple(
                index
                for index in anchor.atom_indices
                if structure[index].element == "N"
            )
            au_c_n_angles = tuple(
                angle_degrees(
                    center_to_au,
                    vector(center, coordinates_of(structure, index)),
                )
                for index in nitrogen_indices
            )
            self.assertAlmostEqual(
                au_c_n_angles[0],
                au_c_n_angles[1],
                places=10,
            )
            self.assertAlmostEqual(
                math.dist(
                    coordinates(proposal),
                    coordinates_of(structure, nitrogen_indices[0]),
                ),
                math.dist(
                    coordinates(proposal),
                    coordinates_of(structure, nitrogen_indices[1]),
                ),
                places=10,
            )

        applied = apply_au_placements(structure, connectivity, proposals)
        redetected = detect_anchors(applied.structure, applied.connectivity)
        self.assertEqual(applied.added_au_indices, (12, 13))
        self.assertEqual(
            tuple(
                anchor.attached_au_indices
                for anchor in redetected
                if anchor.kind is AnchorKind.DICYANO_C
            ),
            ((12,), (13,)),
        )
        self.assertEqual(
            sum(anchor.kind is AnchorKind.CYANO_N for anchor in redetected),
            4,
        )

    def test_nh2_maximin_precedes_larger_total_h_distance(self) -> None:
        structure, connectivity = system(
            (
                ("N", 0.0, 0.0, 0.0),
                ("C", 1.0, 0.0, 0.0),
                ("H", 0.693, 1.202, 1.061),
                ("H", -0.211, 0.147, -0.893),
            ),
            ((0, 1), (0, 2), (0, 3)),
        )
        anchor = AnchorCandidate(AnchorKind.NH2, 0, (0, 2, 3))
        defaults = self.defaults[AnchorKind.NH2]
        candidates = with_nh2_local_h_scores(
            cone_orientation_candidates(
                structure,
                connectivity,
                0,
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                defaults.distance_angstrom,
                defaults.angle_degrees,
                self.vdw_radii,
            ),
            structure,
            (2, 3),
        )
        maximin = select_single_orientation(candidates)
        total_only = max(
            candidates,
            key=lambda candidate: (
                round(candidate.nh2_total_h_distance or 0.0, 12),
                -candidate.candidate_order,
            ),
        )

        self.assertGreater(
            maximin.nh2_min_h_distance or 0.0,
            total_only.nh2_min_h_distance or 0.0,
        )
        self.assertLess(
            maximin.nh2_total_h_distance or 0.0,
            total_only.nh2_total_h_distance or 0.0,
        )
        proposal = propose_au_placement(
            structure,
            connectivity,
            anchor,
            defaults.distance_angstrom,
            defaults.angle_degrees,
            vdw_radii=self.vdw_radii,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(coordinates(proposal), maximin.point)

    def test_attached_au_returns_no_new_single_proposal(self) -> None:
        structure, connectivity = system(
            (
                ("C", -2.0, 0.0, 0.0),
                ("N", -1.0, 0.0, 0.0),
                ("C", 0.0, 0.0, 0.0),
                ("S", 1.0, 0.0, 0.0),
                ("Au", 3.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3), (3, 4)),
        )
        anchor = AnchorCandidate(AnchorKind.NCS, 3, (1, 2, 3), (4,))

        self.assertIsNone(
            propose_au_placement(
                structure,
                connectivity,
                anchor,
                2.34,
                170.0,
                vdw_radii=self.vdw_radii,
            )
        )

    def test_missing_angle_and_invalid_parameters_fail_explicitly(self) -> None:
        structure, connectivity = system(
            (
                ("C", 1.0, 0.0, 0.0),
                ("N", 0.0, 0.0, 0.0),
                ("H", 0.0, 1.0, 1.0),
                ("H", 0.0, 1.0, -1.0),
            ),
            ((0, 1), (1, 2), (1, 3)),
        )
        anchor = AnchorCandidate(AnchorKind.NH2, 1, (1, 2, 3))

        with self.assertRaisesRegex(AuPlacementError, "requires an angle"):
            propose_au_placement(
                structure,
                connectivity,
                anchor,
                2.5,
                vdw_radii=self.vdw_radii,
            )
        for invalid_distance in (0.0, -1.0, math.inf, math.nan):
            with self.subTest(distance=invalid_distance):
                with self.assertRaisesRegex(ValueError, "greater than zero"):
                    propose_au_placement(
                        structure,
                        connectivity,
                        anchor,
                        invalid_distance,
                        120.0,
                        vdw_radii=self.vdw_radii,
                    )
        for invalid_angle in (0.0, -1.0, 180.1, math.inf, math.nan):
            with self.subTest(angle=invalid_angle):
                with self.assertRaisesRegex(ValueError, "at most 180"):
                    propose_au_placement(
                        structure,
                        connectivity,
                        anchor,
                        2.5,
                        invalid_angle,
                        vdw_radii=self.vdw_radii,
                    )


class ReferenceMoleculePlacementTests(unittest.TestCase):
    def test_synthetic_dual_ncs_joint_preview_is_deterministic_and_non_mutating(self) -> None:
        structure = read_xyz(REFERENCE_XYZ_PATH)
        connectivity = infer_connectivity(
            structure,
            load_default_covalent_radii(),
        )
        anchors = detect_anchors(structure, connectivity)
        defaults = load_default_ncs_placement_defaults()
        original_atoms = structure.atoms
        parameters = tuple(
            AuPlacementParameters(
                anchor,
                defaults.distance_angstrom,
                defaults.angle_degrees,
            )
            for anchor in anchors
        )
        vdw_radii = load_default_vdw_radii()

        proposals = propose_au_placements(
            structure,
            connectivity,
            parameters,
            vdw_radii=vdw_radii,
        )
        repeated = propose_au_placements(
            structure,
            connectivity,
            parameters,
            vdw_radii=vdw_radii,
        )

        self.assertEqual(
            anchors,
            (
                AnchorCandidate(AnchorKind.NCS, 8, (6, 7, 8)),
                AnchorCandidate(AnchorKind.NCS, 11, (9, 10, 11)),
            ),
        )
        self.assertEqual(proposals, repeated)
        self.assertEqual(len(proposals), 2)
        directions = []
        for anchor, proposal in zip(anchors, proposals, strict=True):
            sulfur = structure[anchor.binding_atom_index]
            carbon_index = angle_reference_atom_index(
                structure,
                connectivity,
                anchor,
            )
            self.assertIsNotNone(carbon_index)
            assert carbon_index is not None
            carbon = structure[carbon_index]
            sulfur_coordinates = (sulfur.x, sulfur.y, sulfur.z)
            sulfur_to_carbon = vector(
                sulfur_coordinates,
                (carbon.x, carbon.y, carbon.z),
            )
            sulfur_to_au = vector(sulfur_coordinates, coordinates(proposal))
            directions.append(
                tuple(component / math.hypot(*sulfur_to_au) for component in sulfur_to_au)
            )
            self.assertAlmostEqual(
                math.hypot(*sulfur_to_au),
                2.34,
                places=12,
            )
            self.assertAlmostEqual(
                angle_degrees(sulfur_to_carbon, sulfur_to_au),
                170.0,
                places=10,
            )
            self.assertGreaterEqual(
                steric_clearance_for_point(
                    structure,
                    connectivity,
                    anchor.binding_atom_index,
                    coordinates(proposal),
                    vdw_radii,
                ).minimum,
                0.0,
            )
            self.assertIs(proposal.anchor, anchor)

        self.assertLess(dot(directions[0], directions[1]), -0.9)
        self.assertGreaterEqual(
            math.dist(coordinates(proposals[0]), coordinates(proposals[1])),
            2.0 * vdw_radii["Au"],
        )
        self.assertIs(structure.atoms, original_atoms)

    def test_joint_service_rejects_zero_three_or_duplicate_sites(self) -> None:
        structure, connectivity = system(
            (("S", 0.0, 0.0, 0.0), ("H", 1.0, 0.0, 0.0)),
            ((0, 1),),
        )
        anchor = AnchorCandidate(AnchorKind.SH, 0, (0, 1))
        parameter = AuPlacementParameters(anchor, 2.3)

        for invalid in ((), (parameter, parameter, parameter)):
            with self.subTest(count=len(invalid)):
                with self.assertRaisesRegex(AuPlacementError, "one or two"):
                    propose_au_placements(
                        structure,
                        connectivity,
                        invalid,
                        vdw_radii=load_default_vdw_radii(),
                    )
        with self.assertRaisesRegex(AuPlacementError, "must be unique"):
            propose_au_placements(
                structure,
                connectivity,
                (parameter, parameter),
                vdw_radii=load_default_vdw_radii(),
            )

    def test_mixed_ncs_and_pyridine_candidate_sets_are_deterministic(self) -> None:
        # Keep this deterministic-selection fixture away from the packaged
        # steric cutoff; this is synthetic geometry, not a scientific default.
        ring_side = 1.4
        half_height = ring_side * math.sqrt(3.0) / 2.0
        structure, connectivity = system(
            (
                ("C", -13.0, 0.0, 0.0),
                ("N", -12.0, 0.0, 0.0),
                ("C", -11.0, 0.0, 0.0),
                ("S", -10.0, 0.0, 0.0),
                ("N", 9.0, 0.0, 0.0),
                ("C", 9.0 + 0.5 * ring_side, half_height, 0.0),
                ("C", 9.0 + 1.5 * ring_side, half_height, 0.0),
                ("C", 9.0 + 2.0 * ring_side, 0.0, 0.0),
                ("C", 9.0 + 1.5 * ring_side, -half_height, 0.0),
                ("C", 9.0 + 0.5 * ring_side, -half_height, 0.0),
            ),
            (
                (0, 1),
                (1, 2),
                (2, 3),
                (4, 5),
                (5, 6),
                (6, 7),
                (7, 8),
                (8, 9),
                (4, 9),
            ),
        )
        ncs = AnchorCandidate(AnchorKind.NCS, 3, (1, 2, 3))
        pyridine = AnchorCandidate(AnchorKind.PYRIDINE_N, 4, range(4, 10))
        defaults = load_default_au_placement_defaults()
        parameters = tuple(
            AuPlacementParameters(
                anchor,
                defaults[anchor.kind].distance_angstrom,
                defaults[anchor.kind].angle_degrees,
            )
            for anchor in (ncs, pyridine)
        )

        proposals = propose_au_placements(
            structure,
            connectivity,
            parameters,
            vdw_radii=load_default_vdw_radii(),
        )
        repeated = propose_au_placements(
            structure,
            connectivity,
            parameters,
            vdw_radii=load_default_vdw_radii(),
        )

        self.assertEqual(proposals, repeated)
        self.assertEqual(tuple(item.anchor.kind for item in proposals), (
            AnchorKind.NCS,
            AnchorKind.PYRIDINE_N,
        ))
        directions = tuple(
            tuple(
                component / math.hypot(*direction)
                for component in direction
            )
            for direction in (
                vector((-10.0, 0.0, 0.0), coordinates(proposals[0])),
                vector((9.0, 0.0, 0.0), coordinates(proposals[1])),
            )
        )
        self.assertLess(dot(*directions), -0.8)


if __name__ == "__main__":
    unittest.main()
