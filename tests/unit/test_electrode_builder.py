import math
import unittest

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.au_pyramid import generate_au_pyramid
from moltage.domain.electrode import ElectrodeContactSite
from moltage.junction.electrode_builder import (
    ElectrodeBuilderError,
    align_electrode_pyramid,
    apply_electrode_placement,
    eligible_electrode_contact_sites,
    minimum_cross_cluster_distance_squared,
    optimize_joint_roll,
    propose_electrode_placement,
    target_axis_for_site,
)
from synthetic_structure_test_support import synthetic_step2_state


class ElectrodeBuilderTests(unittest.TestCase):
    def test_sites_come_only_from_recognized_anchors_with_one_attached_au(self) -> None:
        structure, connectivity, anchors, sites = synthetic_step2_state()
        self.assertEqual(len(structure), 18)
        self.assertEqual(tuple(site.sort_key for site in sites), ((8, 16), (11, 17)))
        self.assertEqual(tuple(site.anchor.kind for site in sites), (AnchorKind.NCS, AnchorKind.NCS))
        self.assertEqual(sites, eligible_electrode_contact_sites(structure, connectivity, anchors))

    def test_builder_requires_exactly_two_eligible_sites_without_guessing(self) -> None:
        structure, connectivity, _, sites = synthetic_step2_state()
        with self.assertRaisesRegex(ElectrodeBuilderError, "found 1"):
            propose_electrode_placement(structure, connectivity, sites[:1], sites[:1])
        third_anchor = AnchorCandidate(
            AnchorKind.NCS, 0, (0,), attached_au_indices=(1,)
        )
        third_site = ElectrodeContactSite(third_anchor, 1)
        with self.assertRaisesRegex(ElectrodeBuilderError, "found 3"):
            propose_electrode_placement(
                structure, connectivity, sites + (third_site,), sites
            )

    def test_default_one_side_preview_is_au56_and_reuses_apex(self) -> None:
        structure, connectivity, _, sites = synthetic_step2_state()
        proposal = propose_electrode_placement(
            structure, connectivity, sites, (sites[1],)
        )
        cluster = proposal.clusters[0]
        self.assertEqual(cluster.side, "RIGHT")
        self.assertEqual(cluster.pyramid_layers, 6)
        self.assertEqual(cluster.roll_degrees, 0)
        self.assertEqual(cluster.apex_atom_index, sites[1].contact_au_index)
        apex = structure[sites[1].contact_au_index]
        self.assertEqual(cluster.transformed_coordinates[0], (apex.x, apex.y, apex.z))
        self.assertEqual(len(cluster.local_to_global_indices), 56)
        self.assertEqual(len(cluster.new_atom_indices), 55)
        self.assertEqual(len(proposal.preview_structure), 73)

    def test_supported_sizes_produce_dynamic_mappings(self) -> None:
        structure, connectivity, _, sites = synthetic_step2_state()
        for layers, total in ((2, 4), (6, 56), (10, 220)):
            with self.subTest(layers=layers):
                proposal = propose_electrode_placement(
                    structure,
                    connectivity,
                    sites,
                    (sites[0],),
                    pyramid_layers=layers,
                )
                cluster = proposal.clusters[0]
                self.assertEqual(len(cluster.local_to_global_indices), total)
                self.assertEqual(len(cluster.new_atom_indices), total - 1)
                self.assertEqual(len(proposal.preview_structure), 18 + total - 1)

    def test_rigid_alignment_handles_parallel_antiparallel_and_normal_axes(self) -> None:
        pyramid = generate_au_pyramid(6)
        apex = (2.3, -1.1, 0.7)
        targets = (
            pyramid.principal_axis,
            tuple(-component for component in pyramid.principal_axis),
            (0.0, 0.0, 1.0),
        )
        for target in targets:
            binding = tuple(a - b for a, b in zip(apex, target, strict=True))
            transformed = align_electrode_pyramid(pyramid, binding, apex)
            self.assertEqual(transformed[0], apex)
            base = pyramid.base_layer_local_indices
            centroid = tuple(
                sum(transformed[index][component] for index in base) / len(base)
                for component in range(3)
            )
            raw_axis = tuple(centroid[i] - apex[i] for i in range(3))
            length = math.hypot(*raw_axis)
            actual_axis = tuple(value / length for value in raw_axis)
            expected_length = math.hypot(*target)
            expected_axis = tuple(value / expected_length for value in target)
            for actual, expected in zip(actual_axis, expected_axis, strict=True):
                self.assertAlmostEqual(actual, expected, places=10)
            for first in range(len(pyramid.structure)):
                for second in range(first + 1, len(pyramid.structure)):
                    self.assertAlmostEqual(
                        math.dist(transformed[first], transformed[second]),
                        math.dist(
                            _atom_coordinates(pyramid.structure[first]),
                            _atom_coordinates(pyramid.structure[second]),
                        ),
                        places=10,
                    )

    def test_target_axes_follow_binding_to_contact_direction(self) -> None:
        structure, _, _, sites = synthetic_step2_state()
        for site in sites:
            binding = structure[site.anchor.binding_atom_index]
            contact = structure[site.contact_au_index]
            raw = (contact.x - binding.x, contact.y - binding.y, contact.z - binding.z)
            length = math.hypot(*raw)
            expected = tuple(component / length for component in raw)
            for actual, target in zip(target_axis_for_site(structure, site), expected, strict=True):
                self.assertAlmostEqual(actual, target, places=12)

    def test_joint_roll_is_deterministic_and_sides_remain_independent(self) -> None:
        structure, connectivity, _, sites = synthetic_step2_state()
        first = propose_electrode_placement(
            structure, connectivity, sites, sites, pyramid_layers=6
        )
        second = propose_electrode_placement(
            structure,
            connectivity,
            tuple(reversed(sites)),
            tuple(reversed(sites)),
            pyramid_layers=6,
        )
        self.assertEqual(first, second)
        rolls = tuple(cluster.roll_degrees for cluster in first.clusters)
        self.assertTrue(all(0 <= value < 360 for value in rolls))
        self.assertNotEqual(rolls[0], rolls[1])
        self.assertEqual(tuple(cluster.side for cluster in first.clusters), ("LEFT", "RIGHT"))

    def test_roll_objective_excludes_only_fixed_apex_pair_for_dynamic_sizes(self) -> None:
        first = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
        second = ((0.0, 0.0, 0.0), (10.0, 1.0, 0.0), (11.0, 1.0, 0.0))
        score = minimum_cross_cluster_distance_squared(first, second)
        expected = min(
            sum((a - b) ** 2 for a, b in zip(left, right, strict=True))
            for i, left in enumerate(first)
            for j, right in enumerate(second)
            if (i, j) != (0, 0)
        )
        self.assertEqual(score, expected)

    def test_joint_roll_ties_choose_lexicographically_smallest_angles(self) -> None:
        first = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        second = ((0.0, 100.0, 0.0), (1.0, 100.0, 0.0))
        selection = optimize_joint_roll(first, (1.0, 0.0, 0.0), second, (1.0, 0.0, 0.0))
        self.assertEqual((selection.first_degrees, selection.second_degrees), (0, 0))

    def test_apply_promotes_exact_default_preview_without_apex_duplication(self) -> None:
        structure, connectivity, _, sites = synthetic_step2_state()
        proposal = propose_electrode_placement(structure, connectivity, sites, sites)
        result = apply_electrode_placement(structure, connectivity, proposal)
        self.assertIs(result.structure, proposal.preview_structure)
        self.assertIs(result.connectivity, proposal.preview_connectivity)
        self.assertEqual(len(result.structure), 128)
        self.assertEqual(len(result.added_au_indices), 110)
        self.assertEqual(result.added_au_indices, tuple(range(18, 128)))
        self.assertEqual(tuple(cluster.apex_atom_index for cluster in proposal.clusters), (16, 17))
        self.assertNotIn(16, result.added_au_indices)
        self.assertNotIn(17, result.added_au_indices)
        with self.assertRaisesRegex(ElectrodeBuilderError, "structure changed"):
            apply_electrode_placement(result.structure, result.connectivity, proposal)


def _atom_coordinates(atom):
    return atom.x, atom.y, atom.z


if __name__ == "__main__":
    unittest.main()
