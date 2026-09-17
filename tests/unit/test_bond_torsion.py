import math
import unittest

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.visualization.bond_torsion import (
    BondTorsionError,
    BondTorsionSession,
    GeometryUndoHistory,
    NonRotatableEdgeError,
    analyze_rotatable_edge,
    default_group_assignment,
    drag_angle_from_total_displacement,
    normalize_signed_degrees,
    rotate_structure_about_bond,
    structure_coordinates,
    structure_with_coordinates,
)


def _connectivity(atom_count, pairs):
    return Connectivity(
        atom_count,
        tuple(Bond(first, second, 1.0) for first, second in pairs),
    )


def _structure(points, elements=None):
    symbols = elements or ("C",) * len(points)
    return MolecularStructure(
        tuple(
            Atom(index, symbol, *point)
            for index, (symbol, point) in enumerate(
                zip(symbols, points, strict=True)
            )
        ),
        comment="torsion fixture",
    )


class RotatableEdgeAnalysisTests(unittest.TestCase):
    def test_simple_existing_bridge_is_rotatable(self):
        connectivity = _connectivity(5, ((0, 1), (1, 2), (2, 3), (3, 4)))

        analysis = analyze_rotatable_edge(connectivity, 1, 2)

        self.assertTrue(analysis.is_rotatable)
        self.assertEqual(analysis.group_a, (0, 1))
        self.assertEqual(analysis.group_b, (2, 3, 4))
        self.assertEqual(analysis.component_count_before, 1)
        self.assertEqual(analysis.component_count_after, 2)

    def test_bridge_with_counterion_uses_before_plus_one(self):
        connectivity = _connectivity(
            6,
            ((0, 1), (1, 2), (2, 3), (4, 5)),
        )

        analysis = analyze_rotatable_edge(connectivity, 1, 2)

        self.assertTrue(analysis.is_rotatable)
        self.assertEqual(analysis.component_count_before, 2)
        self.assertEqual(analysis.component_count_after, 3)
        self.assertEqual(analysis.group_a, (0, 1))
        self.assertEqual(analysis.group_b, (2, 3))
        self.assertNotIn(4, analysis.group_a + analysis.group_b)
        self.assertNotIn(5, analysis.group_a + analysis.group_b)

    def test_multiple_disconnected_fragments_do_not_enter_endpoint_groups(self):
        connectivity = _connectivity(
            9,
            ((0, 1), (1, 2), (3, 4), (5, 6), (6, 7), (7, 8)),
        )

        analysis = analyze_rotatable_edge(connectivity, 6, 7)

        self.assertTrue(analysis.is_rotatable)
        self.assertEqual(analysis.component_count_before, 3)
        self.assertEqual(analysis.component_count_after, 4)
        self.assertEqual(analysis.group_a, (5, 6))
        self.assertEqual(analysis.group_b, (7, 8))

    def test_ring_edge_is_rejected_without_endpoint_split(self):
        connectivity = _connectivity(3, ((0, 1), (1, 2), (0, 2)))

        analysis = analyze_rotatable_edge(connectivity, 0, 1)

        self.assertFalse(analysis.is_rotatable)
        self.assertEqual(analysis.component_count_before, 1)
        self.assertEqual(analysis.component_count_after, 1)
        self.assertEqual(analysis.group_a, analysis.group_b)
        structure = _structure(((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        with self.assertRaisesRegex(NonRotatableEdgeError, "another path"):
            BondTorsionSession(structure, connectivity, 0, 1)

    def test_non_edge_is_not_promoted_from_atom_pair(self):
        connectivity = _connectivity(3, ((0, 1), (1, 2)))

        with self.assertRaisesRegex(BondTorsionError, "not a Connectivity edge"):
            analyze_rotatable_edge(connectivity, 0, 2)

    def test_larger_group_is_fixed_and_unrelated_fragments_are_ignored(self):
        connectivity = _connectivity(
            7,
            ((0, 1), (1, 2), (2, 3), (3, 4), (5, 6)),
        )
        analysis = analyze_rotatable_edge(connectivity, 2, 3)

        assignment = default_group_assignment(analysis)

        self.assertEqual(assignment.fixed_indices, (0, 1, 2))
        self.assertEqual(assignment.rotating_indices, (3, 4))
        self.assertEqual(assignment.fixed_endpoint, 2)
        self.assertEqual(assignment.rotating_endpoint, 3)

    def test_equal_size_tie_fixes_lower_minimum_index_group(self):
        connectivity = _connectivity(4, ((0, 1), (1, 2), (2, 3)))
        analysis = analyze_rotatable_edge(connectivity, 1, 2)

        assignment = default_group_assignment(analysis)

        self.assertEqual(assignment.fixed_indices, (0, 1))
        self.assertEqual(assignment.rotating_indices, (2, 3))
        self.assertEqual(assignment.fixed_endpoint, 1)
        self.assertEqual(assignment.rotating_endpoint, 2)


class RigidBondRotationTests(unittest.TestCase):
    def setUp(self):
        self.structure = _structure(
            (
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (2.0, 1.0, 0.0),
                (1.0, 0.0, 1.0),
                (-2.0, 3.0, 4.0),
            )
        )

    def test_plus_and_minus_ninety_follow_oriented_axis_right_hand_rule(self):
        plus = rotate_structure_about_bond(
            self.structure,
            0,
            1,
            (1, 2, 3),
            90.0,
        )
        minus = rotate_structure_about_bond(
            self.structure,
            0,
            1,
            (1, 2, 3),
            -90.0,
        )

        self.assertEqual((plus[0].x, plus[0].y, plus[0].z), (0.0, 0.0, 0.0))
        self.assertEqual((plus[1].x, plus[1].y, plus[1].z), (1.0, 0.0, 0.0))
        self.assertAlmostEqual(plus[2].x, 2.0)
        self.assertAlmostEqual(plus[2].y, 0.0, places=12)
        self.assertAlmostEqual(plus[2].z, 1.0, places=12)
        self.assertAlmostEqual(plus[3].y, -1.0, places=12)
        self.assertAlmostEqual(plus[3].z, 0.0, places=12)
        self.assertAlmostEqual(minus[2].y, 0.0, places=12)
        self.assertAlmostEqual(minus[2].z, -1.0, places=12)
        self.assertAlmostEqual(minus[3].y, 1.0, places=12)
        self.assertAlmostEqual(minus[3].z, 0.0, places=12)

    def test_rotation_preserves_axis_fixed_unrelated_and_internal_distances(self):
        rotated = rotate_structure_about_bond(
            self.structure,
            0,
            1,
            (1, 2, 3),
            73.5,
        )

        self.assertIs(rotated[0], self.structure[0])
        self.assertIs(rotated[1], self.structure[1])
        self.assertIs(rotated[4], self.structure[4])
        self.assertEqual(rotated.comment, self.structure.comment)
        for first, second in ((1, 2), (1, 3), (2, 3)):
            before = math.dist(
                structure_coordinates(self.structure)[first],
                structure_coordinates(self.structure)[second],
            )
            after = math.dist(
                structure_coordinates(rotated)[first],
                structure_coordinates(rotated)[second],
            )
            self.assertAlmostEqual(after, before, places=12)

    def test_zero_angle_returns_exact_baseline_object(self):
        rotated = rotate_structure_about_bond(
            self.structure,
            0,
            1,
            (1, 2),
            360.0,
        )

        self.assertIs(rotated, self.structure)

    def test_side_switch_preserves_coordinates_and_rebases_angle(self):
        connectivity = _connectivity(4, ((0, 1), (1, 2), (2, 3)))
        structure = _structure(
            ((0, 1, 0), (0, 0, 0), (1, 0, 0), (1, 1, 0))
        )
        session = BondTorsionSession(structure, connectivity, 1, 2)
        current = session.structure_at(40.0)
        coordinates = structure_coordinates(current)

        session.switch_rotating_side(current)

        self.assertEqual(structure_coordinates(current), coordinates)
        self.assertEqual(session.relative_angle_degrees, 0.0)
        self.assertEqual(session.fixed_indices, (2, 3))
        self.assertEqual(session.rotating_indices, (0, 1))
        self.assertEqual(session.fixed_endpoint, 2)
        self.assertEqual(session.rotating_endpoint, 1)
        self.assertIs(session.structure_at(0.0), current)


class TorsionInteractionMathTests(unittest.TestCase):
    def test_drag_mapping_is_linear_and_directional(self):
        self.assertAlmostEqual(
            drag_angle_from_total_displacement(100.0, 0.0, (1.0, 0.0)),
            30.0,
        )
        self.assertAlmostEqual(
            drag_angle_from_total_displacement(-100.0, 0.0, (1.0, 0.0)),
            -30.0,
        )
        self.assertAlmostEqual(
            drag_angle_from_total_displacement(0.0, 300.0, (0.0, 2.0)),
            90.0,
        )

    def test_total_displacement_is_independent_of_event_frequency(self):
        one_event = drag_angle_from_total_displacement(300.0, 0.0, (1.0, 0.0))
        many_events = drag_angle_from_total_displacement(
            sum((10.0,) * 30),
            0.0,
            (1.0, 0.0),
        )

        self.assertEqual(one_event, many_events)
        self.assertEqual(one_event, 90.0)

    def test_signed_normalization_uses_required_interval(self):
        self.assertEqual(normalize_signed_degrees(0), 0.0)
        self.assertEqual(normalize_signed_degrees(180), 180.0)
        self.assertEqual(normalize_signed_degrees(-180), 180.0)
        self.assertEqual(normalize_signed_degrees(540), 180.0)
        self.assertEqual(normalize_signed_degrees(181), -179.0)
        self.assertEqual(normalize_signed_degrees(-181), 179.0)


class GeometryUndoHistoryTests(unittest.TestCase):
    def test_default_history_undoes_and_redoes_five_exact_coordinate_states(self):
        base = _structure(((0, 0, 0), (1, 0, 0)))
        states = tuple(
            structure_with_coordinates(
                base,
                ((float(index), 0.0, 0.0), (1.0, float(index), 0.0)),
            )
            for index in range(6)
        )
        history = GeometryUndoHistory()
        for prior in states[:-1]:
            history.push(prior)

        self.assertEqual(len(history), 5)
        self.assertTrue(history.can_undo)
        self.assertFalse(history.can_redo)

        current = states[-1]
        for expected in reversed(states[:-1]):
            current = history.undo(current)
            self.assertEqual(
                structure_coordinates(current),
                structure_coordinates(expected),
            )

        self.assertFalse(history.can_undo)
        self.assertTrue(history.can_redo)
        self.assertIsNone(history.undo(current))

        for expected in states[1:]:
            current = history.redo(current)
            self.assertEqual(
                structure_coordinates(current),
                structure_coordinates(expected),
            )

        self.assertTrue(history.can_undo)
        self.assertFalse(history.can_redo)
        self.assertIsNone(history.redo(current))

    def test_history_cap_discards_only_the_oldest_state_after_six_edits(self):
        base = _structure(((0, 0, 0), (1, 0, 0)))
        states = tuple(
            structure_with_coordinates(
                base,
                ((float(index), 0.0, 0.0), (1.0, float(index), 0.0)),
            )
            for index in range(7)
        )
        history = GeometryUndoHistory()
        for prior in states[:-1]:
            history.push(prior)

        current = states[-1]
        restored = []
        while history.can_undo:
            current = history.undo(current)
            restored.append(structure_coordinates(current))

        self.assertEqual(len(restored), 5)
        self.assertEqual(
            restored,
            [structure_coordinates(state) for state in reversed(states[1:6])],
        )
        self.assertIsNone(history.undo(current))

    def test_new_edit_after_undo_clears_the_redo_branch(self):
        base = _structure(((0, 0, 0), (1, 0, 0)))
        states = tuple(
            structure_with_coordinates(
                base,
                ((float(index), 0.0, 0.0), (1.0, float(index), 0.0)),
            )
            for index in range(5)
        )
        history = GeometryUndoHistory()
        for prior in states[:3]:
            history.push(prior)

        current = history.undo(states[3])
        current = history.undo(current)
        self.assertTrue(history.can_redo)

        history.push(current)

        self.assertFalse(history.can_redo)
        self.assertIsNone(history.redo(states[4]))

    def test_undo_identity_mismatch_clears_both_history_directions(self):
        carbon = tuple(
            _structure(
                ((float(index), 0, 0), (1, float(index), 0)),
                ("C", "C"),
            )
            for index in range(3)
        )
        nitrogen = _structure(((0, 0, 0), (1, 0, 0)), ("C", "N"))
        history = GeometryUndoHistory()
        history.push(carbon[0])
        history.push(carbon[1])
        history.undo(carbon[2])
        self.assertTrue(history.can_undo)
        self.assertTrue(history.can_redo)

        with self.assertRaisesRegex(BondTorsionError, "structure replacement"):
            history.undo(nitrogen)

        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)

    def test_redo_identity_mismatch_clears_both_history_directions(self):
        carbon = tuple(
            _structure(
                ((float(index), 0, 0), (1, float(index), 0)),
                ("C", "C"),
            )
            for index in range(3)
        )
        nitrogen = _structure(((0, 0, 0), (1, 0, 0)), ("C", "N"))
        history = GeometryUndoHistory()
        history.push(carbon[0])
        history.push(carbon[1])
        history.undo(carbon[2])

        with self.assertRaisesRegex(BondTorsionError, "structure replacement"):
            history.redo(nitrogen)

        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)


if __name__ == "__main__":
    unittest.main()
