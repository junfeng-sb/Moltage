from array import array
import tempfile
import unittest
from pathlib import Path

from moltage.domain.bond_display import ConnectivitySource
from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.cube import (
    BOHR_TO_ANGSTROM,
    CubeCoordinateUnit,
    CubeFormatError,
    CubeScalarField,
    CubeSourceKind,
    recommend_cube_coordinate_unit,
    read_cube,
)
from moltage.structure.geometry_loader import load_geometry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "cube" / "signed_orbital.cube"


class CubeReaderTests(unittest.TestCase):
    def test_reads_single_signed_field_and_converts_atomic_units(self) -> None:
        parsed = read_cube(FIXTURE)

        self.assertEqual(tuple(atom.element for atom in parsed.structure), ("H", "C"))
        self.assertAlmostEqual(parsed.structure[1].x, 2.0 * BOHR_TO_ANGSTROM)
        self.assertEqual(parsed.scalar_field.dimensions, (2, 2, 2))
        self.assertEqual(
            parsed.scalar_field.axis_vectors_angstrom,
            (
                (BOHR_TO_ANGSTROM, 0.0, 0.0),
                (0.0, BOHR_TO_ANGSTROM, 0.0),
                (0.0, 0.0, BOHR_TO_ANGSTROM),
            ),
        )
        self.assertEqual(
            tuple(parsed.scalar_field.values),
            (-0.04, -0.02, 0.0, 0.02, 0.04, 0.06, -0.06, 0.01),
        )
        self.assertIsInstance(parsed.scalar_field.values, array)
        self.assertIs(
            parsed.scalar_field.source_coordinate_unit,
            CubeCoordinateUnit.BOHR,
        )
        self.assertIs(parsed.scalar_field.source_kind, CubeSourceKind.ORCA)
        self.assertIsNone(parsed.scalar_field.dataset_id)
        self.assertEqual(parsed.scalar_field.minimum, -0.06)
        self.assertEqual(parsed.scalar_field.maximum, 0.06)
        self.assertEqual(parsed.scalar_field.maximum_absolute_value, 0.06)

    def test_cube_and_cub_suffixes_share_the_geometry_loader(self) -> None:
        radii = load_default_covalent_radii()
        with tempfile.TemporaryDirectory() as directory:
            for suffix in (".cube", ".cub"):
                target = Path(directory) / f"orbital{suffix}"
                target.write_bytes(FIXTURE.read_bytes())
                loaded = load_geometry(
                    target,
                    radii,
                    multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
                )
                with self.subTest(suffix=suffix):
                    self.assertIsNotNone(loaded.scalar_field)
                    self.assertIs(
                        loaded.connectivity_source,
                        ConnectivitySource.INFERRED,
                    )
                    self.assertEqual(len(loaded.connectivity), 1)

    def test_reads_orca_single_orbital_record_with_negative_atom_count(self) -> None:
        original = FIXTURE.read_text(encoding="utf-8")
        orbital = original.replace(
            "    2    0.000000",
            "   -2    0.000000",
            1,
        ).replace(
            " -4.00000E-02",
            "    1  245\n -4.00000E-02",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orbital.cube"
            path.write_text(orbital, encoding="utf-8")

            parsed = read_cube(path)

        self.assertEqual(len(parsed.structure), 2)
        self.assertEqual(parsed.scalar_field.dataset_id, 245)
        self.assertEqual(parsed.scalar_field.dimensions, (2, 2, 2))
        self.assertEqual(len(parsed.scalar_field.values), 8)

    def test_reads_wrapped_single_orbital_identifier_record(self) -> None:
        original = FIXTURE.read_text(encoding="utf-8")
        orbital = original.replace(
            "    2    0.000000",
            "   -2    0.000000",
            1,
        ).replace(
            " -4.00000E-02",
            "    1\n  245\n -4.00000E-02",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrapped.cube"
            path.write_text(orbital, encoding="utf-8")

            parsed = read_cube(path)

        self.assertEqual(parsed.scalar_field.dataset_id, 245)

    def test_rejects_true_multi_dataset_axis_sign_and_wrong_scalar_count(self) -> None:
        original = FIXTURE.read_text(encoding="utf-8")
        multi = original.replace(
            "    2    0.000000",
            "   -2    0.000000",
            1,
        ).replace(
            " -4.00000E-02",
            "    2  245  246\n -4.00000E-02",
            1,
        )
        cases = {
            "multi.cube": multi,
            "negative-grid-count.cube": original.replace(
                "    2    1.000000",
                "   -2    1.000000",
                1,
            ),
            "truncated.cube": original.rsplit("1.00000E-02", 1)[0],
            "extra.cube": original + " 9.00000E-02\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, text in cases.items():
                path = Path(directory) / name
                path.write_text(text, encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(CubeFormatError):
                    read_cube(path)

    def test_fhi_aims_and_unknown_unit_recommendations_are_explicit(self) -> None:
        original = FIXTURE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fhi = root / "fhi.cube"
            fhi.write_text(
                original.replace(
                    "Cube data generated by ORCA",
                    "CUBE FILE written by FHI-AIMS",
                    1,
                ),
                encoding="utf-8",
            )
            unknown = root / "unknown.cube"
            unknown.write_text(
                original.replace(
                    "Cube data generated by ORCA",
                    "Unidentified Cube producer",
                    1,
                ),
                encoding="utf-8",
            )

            fhi_recommendation = recommend_cube_coordinate_unit(fhi)
            unknown_recommendation = recommend_cube_coordinate_unit(unknown)
            parsed_angstrom = read_cube(
                fhi,
                coordinate_unit=CubeCoordinateUnit.ANGSTROM,
            )
            parsed_bohr = read_cube(
                fhi,
                coordinate_unit=CubeCoordinateUnit.BOHR,
            )

        self.assertIs(fhi_recommendation.source_kind, CubeSourceKind.FHI_AIMS)
        self.assertIs(
            fhi_recommendation.coordinate_unit,
            CubeCoordinateUnit.ANGSTROM,
        )
        self.assertTrue(fhi_recommendation.requires_confirmation)
        self.assertIs(unknown_recommendation.source_kind, CubeSourceKind.UNKNOWN)
        self.assertIs(
            unknown_recommendation.coordinate_unit,
            CubeCoordinateUnit.BOHR,
        )
        self.assertTrue(unknown_recommendation.requires_confirmation)
        self.assertAlmostEqual(parsed_angstrom.structure[1].x, 2.0)
        self.assertAlmostEqual(
            parsed_bohr.structure[1].x,
            2.0 * BOHR_TO_ANGSTROM,
        )

    def test_scalar_field_accepts_non_collinear_skew_axes_only(self) -> None:
        field = CubeScalarField(
            (2, 2, 2),
            (1.0, 2.0, 3.0),
            ((0.5, 0.0, 0.0), (0.2, 0.4, 0.0), (0.1, 0.1, 0.3)),
            (0.0,) * 8,
        )
        self.assertEqual(field.dimensions, (2, 2, 2))
        with self.assertRaisesRegex(ValueError, "non-zero and non-coplanar"):
            CubeScalarField(
                (2, 2, 2),
                (0.0, 0.0, 0.0),
                ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)),
                (0.0,) * 8,
            )


if __name__ == "__main__":
    unittest.main()
