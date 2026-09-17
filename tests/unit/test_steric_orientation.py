import math
import unittest

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.steric_orientation import (
    OrientationCandidate,
    StericOrientationError,
    cone_orientation_candidates,
    fixed_orientation_candidates,
    select_orientation_pair,
    select_single_orientation,
    steric_clearance_for_point,
)
from moltage.structure.vdw_radii import load_default_vdw_radii


VDW = {"Au": 1.0, "C": 1.0}


def system(
    atom_data: tuple[tuple[str, float, float, float], ...],
    pairs: tuple[tuple[int, int], ...] = (),
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


class StericClearanceTests(unittest.TestCase):
    def test_packaged_radii_control_the_au_carbon_clearance_boundary(self) -> None:
        radii = load_default_vdw_radii()
        contact_distance = radii["Au"] + radii["C"]
        boundary_structure, boundary_connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", contact_distance, 0.0, 0.0))
        )
        overlap_structure, overlap_connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", contact_distance - 1.0e-5, 0.0, 0.0))
        )

        boundary = steric_clearance_for_point(
            boundary_structure,
            boundary_connectivity,
            0,
            (0.0, 0.0, 0.0),
            radii,
        )
        overlap = steric_clearance_for_point(
            overlap_structure,
            overlap_connectivity,
            0,
            (0.0, 0.0, 0.0),
            radii,
        )

        self.assertEqual(boundary.minimum, 0.0)
        self.assertLess(overlap.minimum, 0.0)

    def test_excludes_graph_distances_zero_one_and_two(self) -> None:
        structure, connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("C", 0.1, 0.0, 0.0),
                ("C", 0.2, 0.0, 0.0),
                ("C", 3.0, 0.0, 0.0),
            ),
            ((0, 1), (1, 2), (2, 3)),
        )

        clearance = steric_clearance_for_point(
            structure,
            connectivity,
            0,
            (0.0, 0.0, 0.0),
            VDW,
        )

        self.assertEqual(clearance.minimum, 1.0)
        self.assertEqual(clearance.closest_atom_index, 3)

    def test_exact_zero_clearance_is_feasible_but_overlap_is_not(self) -> None:
        boundary_structure, boundary_connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", 4.0, 0.0, 0.0))
        )
        boundary = fixed_orientation_candidates(
            boundary_structure,
            boundary_connectivity,
            0,
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            2.0,
            VDW,
        )
        self.assertEqual(boundary[0].clearance, 0.0)

        overlap_structure, overlap_connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", 3.99, 0.0, 0.0))
        )
        with self.assertRaisesRegex(
            StericOrientationError,
            "no sterically valid",
        ):
            fixed_orientation_candidates(
                overlap_structure,
                overlap_connectivity,
                0,
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                2.0,
                VDW,
            )

    def test_missing_required_radius_fails_without_fallback(self) -> None:
        structure, connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("Pm", 5.0, 0.0, 0.0))
        )

        with self.assertRaisesRegex(
            StericOrientationError,
            "no approved.*Pm",
        ):
            steric_clearance_for_point(
                structure,
                connectivity,
                0,
                (0.0, 0.0, 0.0),
                VDW,
            )

    def test_explicit_scheduled_removal_is_not_a_steric_obstacle(self) -> None:
        structure, connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", 2.0, 0.0, 0.0))
        )
        with self.assertRaisesRegex(StericOrientationError, "no sterically"):
            fixed_orientation_candidates(
                structure,
                connectivity,
                0,
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                2.0,
                VDW,
            )

        candidates = fixed_orientation_candidates(
            structure,
            connectivity,
            0,
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            2.0,
            VDW,
            excluded_atom_indices=(1,),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].point, (2.0, 0.0, 0.0))


class StericOrientationSelectionTests(unittest.TestCase):
    def test_cone_selects_direction_opposite_a_remote_blocker(self) -> None:
        structure, connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", 5.0, 0.0, 0.0))
        )
        candidates = cone_orientation_candidates(
            structure,
            connectivity,
            0,
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
            5.0,
            90.0,
            VDW,
        )

        selected = select_single_orientation(candidates)

        self.assertLess(len(candidates), 360)
        self.assertTrue(all(item.clearance >= 0.0 for item in candidates))
        self.assertAlmostEqual(selected.point[0], -5.0, places=10)
        self.assertAlmostEqual(selected.point[1], 0.0, places=10)
        self.assertAlmostEqual(selected.point[2], 0.0, places=10)

    def test_joint_selection_prefers_opposed_directions(self) -> None:
        structure, connectivity = system(
            (("C", -10.0, 0.0, 0.0), ("C", 10.0, 0.0, 0.0))
        )
        first = cone_orientation_candidates(
            structure,
            connectivity,
            0,
            (-10.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
            2.0,
            90.0,
            VDW,
        )
        second = cone_orientation_candidates(
            structure,
            connectivity,
            1,
            (10.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
            2.0,
            90.0,
            VDW,
        )

        selected_first, selected_second = select_orientation_pair(
            first,
            second,
            VDW,
        )
        direction_dot = sum(
            a * b
            for a, b in zip(
                selected_first.direction,
                selected_second.direction,
                strict=True,
            )
        )
        self.assertAlmostEqual(direction_dot, -1.0, places=10)

    def test_joint_selection_rejects_all_overlapping_pairs(self) -> None:
        first = (
            OrientationCandidate(
                point=(0.0, 0.0, 0.0),
                direction=(1.0, 0.0, 0.0),
                clearance=1.0,
                azimuth_degrees=0,
            ),
        )
        second = (
            OrientationCandidate(
                point=(2.0, 0.0, 0.0),
                direction=(-1.0, 0.0, 0.0),
                clearance=1.0,
                azimuth_degrees=0,
            ),
        )

        self.assertEqual(
            select_orientation_pair(first, second, VDW),
            (first[0], second[0]),
        )

        overlapping = (
            OrientationCandidate(
                point=(1.99, 0.0, 0.0),
                direction=(-1.0, 0.0, 0.0),
                clearance=1.0,
                azimuth_degrees=0,
            ),
        )

        with self.assertRaisesRegex(
            StericOrientationError,
            "no non-overlapping",
        ):
            select_orientation_pair(first, overlapping, VDW)

    def test_nh2_pair_maximin_precedes_remote_and_direction_preferences(self) -> None:
        safer_for_both_h = OrientationCandidate(
            point=(0.0, 0.0, 0.0),
            direction=(1.0, 0.0, 0.0),
            clearance=1.0,
            azimuth_degrees=0,
            candidate_order=0,
            nh2_min_h_distance=3.0,
            nh2_total_h_distance=6.0,
        )
        asymmetric_close_h = OrientationCandidate(
            point=(0.0, 5.0, 0.0),
            direction=(-1.0, 0.0, 0.0),
            clearance=10.0,
            azimuth_degrees=180,
            candidate_order=180,
            nh2_min_h_distance=2.0,
            nh2_total_h_distance=100.0,
        )
        other_site = OrientationCandidate(
            point=(20.0, 0.0, 0.0),
            direction=(1.0, 0.0, 0.0),
            clearance=10.0,
            azimuth_degrees=None,
        )

        selected, _ = select_orientation_pair(
            (safer_for_both_h, asymmetric_close_h),
            (other_site,),
            VDW,
        )

        self.assertIs(selected, safer_for_both_h)

    def test_rotating_blocker_by_grid_angle_rotates_selected_point(self) -> None:
        angle = math.radians(37.0)
        rotation = (
            (math.cos(angle), -math.sin(angle)),
            (math.sin(angle), math.cos(angle)),
        )

        def rotate_xy(point: tuple[float, float, float]):
            return (
                rotation[0][0] * point[0] + rotation[0][1] * point[1],
                rotation[1][0] * point[0] + rotation[1][1] * point[1],
                point[2],
            )

        original_structure, original_connectivity = system(
            (("C", 0.0, 0.0, 0.0), ("C", 5.0, 0.0, 0.0))
        )
        rotated_blocker = rotate_xy((5.0, 0.0, 0.0))
        rotated_structure, rotated_connectivity = system(
            (
                ("C", 0.0, 0.0, 0.0),
                ("C", *rotated_blocker),
            )
        )
        original = select_single_orientation(
            cone_orientation_candidates(
                original_structure,
                original_connectivity,
                0,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                5.0,
                90.0,
                VDW,
            )
        )
        rotated = select_single_orientation(
            cone_orientation_candidates(
                rotated_structure,
                rotated_connectivity,
                0,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                5.0,
                90.0,
                VDW,
            )
        )

        expected = rotate_xy(original.point)
        self.assertLess(math.dist(expected, rotated.point), 1.0e-10)


if __name__ == "__main__":
    unittest.main()
