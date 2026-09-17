import math
import unittest

from moltage.domain.structure import Atom, MolecularStructure
from moltage.visualization.measurements import (
    AngleMeasurement,
    DistanceMeasurement,
    MeasurementError,
    MeasurementSession,
    angle_degrees_from_cosine,
)


def _structure(*coordinates):
    return MolecularStructure(
        tuple(
            Atom(index, "C", *point)
            for index, point in enumerate(coordinates)
        )
    )


class MeasurementSessionTests(unittest.TestCase):
    def test_distance_uses_exact_cartesian_coordinates(self):
        structure = _structure((0.0, 0.0, 0.0), (1.0, 2.0, 2.0))
        session = MeasurementSession()

        measurement = session.add_distance(structure, 0, 1)

        self.assertIsInstance(measurement, DistanceMeasurement)
        self.assertEqual(measurement.measurement_id, 1)
        self.assertEqual((measurement.atom_a, measurement.atom_b), (0, 1))
        self.assertEqual(measurement.value_angstrom, 3.0)
        self.assertEqual(session.measurements, (measurement,))

    def test_angle_is_abc_with_b_as_vertex(self):
        right_angle = _structure(
            (1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        )
        ordinary_angle = _structure(
            (3.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
        )
        session = MeasurementSession()

        right = session.add_angle(right_angle, 0, 1, 2)
        ordinary = session.add_angle(ordinary_angle, 0, 1, 2)

        self.assertIsInstance(right, AngleMeasurement)
        self.assertEqual(right.value_degrees, 90.0)
        self.assertAlmostEqual(ordinary.value_degrees, 45.0, places=12)
        self.assertNotAlmostEqual(
            ordinary.value_degrees,
            math.degrees(math.atan2(1.0, 2.0)),
            places=6,
        )
        self.assertEqual(
            (ordinary.atom_a, ordinary.atom_b, ordinary.atom_c),
            (0, 1, 2),
        )

    def test_cosine_roundoff_is_clamped_before_acos(self):
        self.assertEqual(angle_degrees_from_cosine(1.0 + 1.0e-12), 0.0)
        self.assertEqual(angle_degrees_from_cosine(-1.0 - 1.0e-12), 180.0)
        self.assertFalse(math.isnan(angle_degrees_from_cosine(1.0 + 1.0e-12)))
        self.assertFalse(math.isnan(angle_degrees_from_cosine(-1.0 - 1.0e-12)))

    def test_duplicate_or_zero_length_selections_create_no_record(self):
        structure = _structure(
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0),
        )
        session = MeasurementSession()

        invalid_calls = (
            lambda: session.add_distance(structure, 0, 0),
            lambda: session.add_distance(structure, 0, 3),
            lambda: session.add_angle(structure, 0, 0, 2),
            lambda: session.add_angle(structure, 0, 1, 0),
            lambda: session.add_angle(structure, 0, 1, 1),
            lambda: session.add_angle(structure, 3, 0, 2),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(MeasurementError):
                    call()
                self.assertEqual(session.measurements, ())

    def test_creation_order_stable_ids_and_exact_deletion(self):
        structure = _structure(
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        session = MeasurementSession()
        first = session.add_distance(structure, 0, 1)
        second = session.add_angle(structure, 1, 0, 2)
        third = session.add_distance(structure, 2, 3)

        deleted = session.delete(second.measurement_id)

        self.assertIs(deleted, second)
        self.assertEqual(session.measurements, (first, third))
        fourth = session.add_angle(structure, 1, 0, 3)
        self.assertEqual(fourth.measurement_id, 4)
        session.clear()
        self.assertEqual(session.measurements, ())
        fifth = session.add_distance(structure, 0, 2)
        self.assertEqual(fifth.measurement_id, 5)

    def test_atom_mapping_removes_only_related_records_and_is_restorable(self):
        structure = _structure(
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (3.0, 1.0, 0.0),
        )
        session = MeasurementSession()
        first = session.add_distance(structure, 0, 1)
        removed_angle = session.add_angle(structure, 0, 1, 2)
        surviving = session.add_distance(structure, 2, 3)
        snapshot = session.snapshot()

        removed = session.remap_atom_indices((0, None, 1, 2))

        self.assertEqual(removed, (first, removed_angle))
        self.assertEqual(
            session.measurements,
            (
                DistanceMeasurement(
                    surviving.measurement_id,
                    1,
                    2,
                    surviving.value_angstrom,
                ),
            ),
        )
        session.restore(snapshot)
        self.assertEqual(session.measurements, (first, removed_angle, surviving))
        next_record = session.add_distance(structure, 0, 3)
        self.assertEqual(next_record.measurement_id, 4)


if __name__ == "__main__":
    unittest.main()
