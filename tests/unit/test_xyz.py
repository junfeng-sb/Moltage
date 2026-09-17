import tempfile
import unittest
from pathlib import Path

from moltage.structure.xyz import XYZParseError, parse_xyz, read_xyz


class XYZReaderTests(unittest.TestCase):
    def test_parses_h2_and_preserves_atom_order(self) -> None:
        structure = parse_xyz(
            """2
hydrogen
H 0 0 0
H 1 0 0
"""
        )

        self.assertEqual(len(structure), 2)
        self.assertEqual(structure.comment, "hydrogen")
        self.assertEqual([atom.index for atom in structure], [0, 1])
        self.assertEqual([atom.element for atom in structure], ["H", "H"])
        self.assertEqual(
            [(atom.x, atom.y, atom.z) for atom in structure],
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        )

    def test_reads_h2o_from_a_utf8_file(self) -> None:
        xyz_text = """3
water
O 0 0 0
H 1 0 0
H -1 0 0
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "water.xyz"
            path.write_text(xyz_text, encoding="utf-8")

            structure = read_xyz(path)

        self.assertEqual(len(structure), 3)
        self.assertEqual([atom.element for atom in structure], ["O", "H", "H"])
        self.assertEqual(structure[2].x, -1.0)

    def test_rejects_declared_atom_count_mismatch(self) -> None:
        xyz_text = """2
incomplete
H 0 0 0
"""

        with self.assertRaisesRegex(
            XYZParseError, "declared 2 atoms but found 1 atom records"
        ):
            parse_xyz(xyz_text)

    def test_rejects_invalid_coordinate_with_line_context(self) -> None:
        xyz_text = """1
invalid coordinate
H not-a-number 0 0
"""

        with self.assertRaisesRegex(
            XYZParseError, "line 3: coordinates must be floating-point numbers"
        ):
            parse_xyz(xyz_text)


if __name__ == "__main__":
    unittest.main()
