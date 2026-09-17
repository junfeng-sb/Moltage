import tempfile
import unittest
from pathlib import Path

from moltage.aims.geometry_writer import render_geometry_in
from moltage.domain.bond_display import ConnectivitySource
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.geometry_loader import (
    SUPPORTED_GEOMETRY_SUFFIXES,
    UnsupportedGeometryFormatError,
    has_supported_geometry_extension,
    load_geometry,
)
from moltage.structure.mol_v2000 import (
    MolV2000ParseError,
    UnsupportedMolBondTypeError,
    UnsupportedMolVersionError,
    parse_mol_v2000,
    read_mol_v2000,
)
from moltage.structure.xyz import parse_xyz


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "ui_r5"


class MolV2000ParserTests(unittest.TestCase):
    def test_valid_single_double_triple_and_mixed_orders(self) -> None:
        expectations = {
            "single.mol": (2, (1,)),
            "double.mol": (2, (2,)),
            "triple.mol": (2, (3,)),
            "mixed.mol": (4, (1, 2, 3)),
        }
        for name, (atom_count, orders) in expectations.items():
            with self.subTest(name=name):
                parsed = read_mol_v2000(FIXTURES / name)
                self.assertEqual(len(parsed.structure), atom_count)
                self.assertEqual(len(parsed.connectivity), len(orders))
                self.assertEqual(
                    tuple(record.order for record in parsed.bond_display_orders),
                    orders,
                )
                self.assertEqual(
                    tuple(record.edge for record in parsed.bond_display_orders),
                    tuple(
                        (bond.first_index, bond.second_index)
                        for bond in parsed.connectivity
                    ),
                )

    def test_atom_order_coordinates_and_element_normalization_are_preserved(self) -> None:
        text = (FIXTURES / "single.mol").read_text(encoding="utf-8")
        text = text.replace(" C   0", " cl  0", 1)

        parsed = parse_mol_v2000(text)

        self.assertEqual(tuple(atom.index for atom in parsed.structure), (0, 1))
        self.assertEqual(
            tuple(atom.element for atom in parsed.structure),
            ("Cl", "O"),
        )
        self.assertEqual(
            (parsed.structure[1].x, parsed.structure[1].y, parsed.structure[1].z),
            (1.2, 0.0, 0.0),
        )

    def test_lf_and_crlf_parse_identically(self) -> None:
        lf = (FIXTURES / "mixed.mol").read_text(encoding="utf-8")

        parsed_lf = parse_mol_v2000(lf)
        parsed_crlf = parse_mol_v2000(lf.replace("\n", "\r\n"))

        self.assertEqual(parsed_lf, parsed_crlf)

    def test_bad_index_and_truncated_bond_block_are_rejected(self) -> None:
        for name in ("bad_index.mol", "truncated_bonds.mol"):
            with self.subTest(name=name), self.assertRaises(MolV2000ParseError):
                read_mol_v2000(FIXTURES / name)

    def test_v3000_and_aromatic_bonds_have_explicit_unsupported_errors(self) -> None:
        with self.assertRaisesRegex(UnsupportedMolVersionError, "V3000"):
            read_mol_v2000(FIXTURES / "v3000.mol")
        with self.assertRaisesRegex(UnsupportedMolBondTypeError, "bond type 4"):
            read_mol_v2000(FIXTURES / "aromatic_type4.mol")

    def test_self_bond_contradictory_duplicate_and_missing_end_are_rejected(self) -> None:
        valid = (FIXTURES / "single.mol").read_text(encoding="utf-8")
        self_bond = valid.replace("  1  2  1", "  1  1  1")
        with self.assertRaisesRegex(MolV2000ParseError, "one atom twice"):
            parse_mol_v2000(self_bond)

        lines = valid.splitlines()
        lines[3] = lines[3].replace("  2  1", "  2  2", 1)
        lines.insert(-1, "  2  1  2  0  0  0  0")
        with self.assertRaisesRegex(MolV2000ParseError, "contradictory duplicate"):
            parse_mol_v2000("\n".join(lines) + "\n")

        without_end = "\n".join(valid.splitlines()[:-1]) + "\n"
        with self.assertRaisesRegex(MolV2000ParseError, "missing.*M  END"):
            parse_mol_v2000(without_end)

    def test_sdf_trailing_record_marker_is_rejected(self) -> None:
        text = (FIXTURES / "single.mol").read_text(encoding="utf-8")
        with self.assertRaisesRegex(MolV2000ParseError, "SDF"):
            parse_mol_v2000(text + "$$$$\n")


class GeometryLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.radii = load_default_covalent_radii()

    def test_extension_dispatch_preserves_explicit_mol_and_infers_xyz_as_single(self) -> None:
        mol = load_geometry(
            FIXTURES / "double.mol",
            self.radii,
            multiplier=0.10,
        )
        self.assertIs(mol.connectivity_source, ConnectivitySource.EXPLICIT)
        self.assertEqual(len(mol.connectivity), 1)
        self.assertEqual(tuple(item.order for item in mol.bond_display_orders), (2,))

        with tempfile.TemporaryDirectory() as temporary_directory:
            xyz_path = Path(temporary_directory) / "same.xyz"
            xyz_path.write_text(
                "2\nSame atoms\nC 0.0 0.0 0.0\nO 1.2 0.0 0.0\n",
                encoding="utf-8",
            )
            xyz = load_geometry(xyz_path, self.radii, multiplier=1.10)
        self.assertIs(xyz.connectivity_source, ConnectivitySource.INFERRED)
        self.assertEqual(len(xyz.connectivity), 1)
        self.assertEqual(tuple(item.order for item in xyz.bond_display_orders), (1,))

    def test_mol_order_is_scientifically_isolated_from_geometry_in(self) -> None:
        mol = read_mol_v2000(FIXTURES / "double.mol")
        xyz = parse_xyz(
            "2\nSame atoms\nC 0.0 0.0 0.0\nC 1.34 0.0 0.0\n"
        )

        self.assertEqual(mol.structure.atoms, xyz.atoms)
        self.assertEqual(
            render_geometry_in(mol.structure),
            render_geometry_in(xyz),
        )

    def test_unknown_extension_is_rejected_without_content_sniffing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "geometry.sdf"
            path.write_text("not inspected", encoding="utf-8")
            with self.assertRaisesRegex(
                UnsupportedGeometryFormatError,
                "unsupported geometry file extension",
            ):
                load_geometry(path, self.radii, multiplier=1.10)

    def test_supported_geometry_extensions_are_one_explicit_casefolded_set(self) -> None:
        self.assertEqual(
            SUPPORTED_GEOMETRY_SUFFIXES,
            (".xyz", ".mol", ".in", ".next_step", ".cube", ".cub"),
        )
        for path in (
            "molecule.xyz",
            "molecule.XYZ",
            "molecule.mol",
            "molecule.cube",
            "molecule.cub",
            "molecule.MOL",
            "geometry.in",
            "geometry.IN",
            "geometry.in.next_step",
            "geometry.in.NEXT_STEP",
        ):
            with self.subTest(path=path):
                self.assertTrue(has_supported_geometry_extension(path))
        for path in ("molecule.sdf", "molecule.pdb", "molecule.mol.gz", "molecule"):
            with self.subTest(path=path):
                self.assertFalse(has_supported_geometry_extension(path))

    def test_local_fhi_aims_input_and_standalone_next_step_reuse_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            control = root / "control.in"
            geometry = root / "geometry.in"
            next_step = root / "geometry.in.next_step"
            control.write_text(
                "species C_alias\n nucleus 6\n"
                "species N_alias\n nucleus 7\n",
                encoding="utf-8",
            )
            geometry.write_text(
                "atom 0 0 0 C_alias\natom 1.2 0 0 N_alias\n",
                encoding="utf-8",
            )
            next_step.write_text(
                "atom 0.25 0 0 C_alias\natom 1.45 0 0 N_alias\n",
                encoding="utf-8",
            )

            submitted = load_geometry(geometry, self.radii, multiplier=1.10)
            geometry.unlink()
            optimized = load_geometry(next_step, self.radii, multiplier=1.10)

        self.assertIs(submitted.connectivity_source, ConnectivitySource.INFERRED)
        self.assertIs(optimized.connectivity_source, ConnectivitySource.INFERRED)
        self.assertEqual(tuple(atom.element for atom in submitted.structure), ("C", "N"))
        self.assertEqual(tuple(atom.element for atom in optimized.structure), ("C", "N"))
        self.assertEqual(optimized.structure[0].x, 0.25)
        self.assertEqual(tuple(item.order for item in optimized.bond_display_orders), (1,))

    def test_local_element_input_and_standalone_next_step_do_not_require_control(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geometry = root / "geometry.in"
            next_step = root / "geometry.in.next_step"
            geometry.write_text(
                "atom 0 0 0 C\natom 1.2 0 0 N\n",
                encoding="utf-8",
            )
            next_step.write_text(
                "atom 0.2 0 0 C\natom 1.4 0 0 N\n",
                encoding="utf-8",
            )

            submitted = load_geometry(geometry, self.radii, multiplier=1.10)
            geometry.unlink()
            optimized = load_geometry(next_step, self.radii, multiplier=1.10)

        self.assertEqual(tuple(atom.element for atom in submitted.structure), ("C", "N"))
        self.assertEqual(optimized.structure[0].x, 0.2)

    def test_local_fhi_aims_geometry_never_falls_back_to_xyz_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "control.in").write_text(
                "species C\n nucleus 6\n",
                encoding="utf-8",
            )
            geometry = root / "geometry.in"
            geometry.write_text(
                "1\nXYZ content with the wrong extension\nC 0 0 0\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsupported active geometry"):
                load_geometry(geometry, self.radii, multiplier=1.10)


if __name__ == "__main__":
    unittest.main()
