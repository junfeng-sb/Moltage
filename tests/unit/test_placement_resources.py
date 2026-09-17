import tempfile
import tomllib
import unittest
from pathlib import Path

from moltage.domain.anchor import AnchorKind
from moltage.junction.placement_defaults import (
    PlacementDefaultsError,
    load_au_placement_defaults,
    load_default_au_placement_defaults,
    load_default_dicyano_cyano_n_placement_defaults,
    load_default_ncs_placement_defaults,
)
from moltage.structure.vdw_radii import (
    DEFAULT_VDW_RADII_PATH,
    VdwRadiiError,
    load_default_vdw_radii,
    load_vdw_radii,
)


SUPPORTED_VDW_ELEMENTS = tuple(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V "
    "Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc "
    "Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Sm Eu Gd "
    "Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi".split()
)


def synthetic_vdw_resource(
    elements: tuple[str, ...] = SUPPORTED_VDW_ELEMENTS,
) -> str:
    metadata = (
        "[metadata]\n"
        'citation = "Synthetic citation"\n'
        'source_url = "https://example.invalid/vdw"\n'
        'source_version = "synthetic-v1"\n'
        'license = "Synthetic permissive license"\n'
        'license_url = "https://example.invalid/license"\n'
        'units = "angstrom"\n'
        'original_units = "angstrom"\n'
        'radius_definition = "Synthetic free-atom radius."\n'
        'coverage = "Synthetic H-Bi except Pm coverage."\n'
        'curation_note = "Synthetic test data only."\n'
        "\n[radii]\n"
    )
    return metadata + "".join(f"{element} = 1.0\n" for element in elements)


class PlacementDefaultResourceTests(unittest.TestCase):
    def test_all_approved_defaults_are_exact(self) -> None:
        defaults = load_default_au_placement_defaults()
        expected = {
            AnchorKind.NCS: (2.34, 170.0),
            AnchorKind.SMe: (2.40, 100.0),
            AnchorKind.PYRIDINE_N: (2.15, 119.0),
            AnchorKind.NH2: (2.42, 120.0),
            AnchorKind.SH: (2.35, 105.0),
            AnchorKind.ALKYNYL_C: (2.05, 179.0),
            AnchorKind.CYANO_N: (2.20, 179.0),
            AnchorKind.DICYANO_C: (2.10, 111.0),
        }

        self.assertEqual(set(defaults), set(AnchorKind))
        for kind, values in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(
                    (
                        defaults[kind].distance_angstrom,
                        defaults[kind].angle_degrees,
                    ),
                    values,
                )
        self.assertEqual(
            load_default_ncs_placement_defaults(),
            defaults[AnchorKind.NCS],
        )
        dicyano_n = load_default_dicyano_cyano_n_placement_defaults()
        self.assertEqual(
            (dicyano_n.distance_angstrom, dicyano_n.angle_degrees),
            (2.00, 121.0),
        )

    def test_rejects_incomplete_or_extra_anchor_sections(self) -> None:
        complete_text = (
            Path(__file__).resolve().parents[2]
            / "resources"
            / "anchors"
            / "au_placement_defaults.toml"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing-section.toml"
            missing_path.write_text(
                complete_text.replace(
                    "\n[CYANO_N]\ndistance_angstrom = 2.20\n"
                    "angle_degrees = 179.0\n",
                    "\n",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PlacementDefaultsError,
                "exactly.*eight",
            ):
                load_au_placement_defaults(missing_path)

            extra_path = Path(directory) / "extra-section.toml"
            extra_path.write_text(
                complete_text + "\n[UNAPPROVED]\ndistance_angstrom=1.0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PlacementDefaultsError,
                "exactly.*eight",
            ):
                load_au_placement_defaults(extra_path)

    def test_missing_or_malformed_defaults_fail_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            with self.assertRaisesRegex(
                PlacementDefaultsError,
                "does not exist",
            ):
                load_au_placement_defaults(missing)

            malformed = Path(directory) / "malformed.toml"
            malformed.write_text("[NCS\n", encoding="utf-8")
            with self.assertRaisesRegex(
                PlacementDefaultsError,
                "malformed TOML",
            ):
                load_au_placement_defaults(malformed)


class VdwRadiusResourceTests(unittest.TestCase):
    def test_approved_table_contains_h_through_bi_except_pm(self) -> None:
        radii = load_default_vdw_radii()

        self.assertEqual(tuple(radii), SUPPORTED_VDW_ELEMENTS)
        self.assertEqual(len(radii), 82)
        self.assertNotIn("Pm", radii)
        self.assertEqual(radii["H"], 1.674686)
        self.assertEqual(radii["C"], 1.910118)
        self.assertEqual(radii["S"], 2.063421)
        self.assertEqual(radii["Au"], 2.253766)
        self.assertEqual(radii["Bi"], 2.348488)

    def test_default_resource_records_approved_provenance_and_conversion(self) -> None:
        with DEFAULT_VDW_RADII_PATH.open("rb") as stream:
            metadata = tomllib.load(stream)["metadata"]

        self.assertEqual(
            metadata["doi"],
            "10.26434/chemrxiv-2024-m3rtp-v2",
        )
        self.assertEqual(metadata["license"], "CC-BY-4.0")
        self.assertEqual(metadata["units"], "angstrom")
        self.assertEqual(metadata["original_units"], "bohr")
        self.assertIn("0.529177210544", metadata["curation_note"])
        self.assertIn("free-atom", metadata["radius_definition"].lower())

    def test_missing_and_malformed_resources_fail_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            with self.assertRaisesRegex(VdwRadiiError, "does not exist"):
                load_vdw_radii(missing)

            malformed = Path(directory) / "malformed.toml"
            malformed.write_text("[radii\n", encoding="utf-8")
            with self.assertRaisesRegex(VdwRadiiError, "malformed TOML"):
                load_vdw_radii(malformed)

    def test_source_agnostic_metadata_and_exact_coverage_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            resource_path = Path(directory) / "synthetic.toml"
            resource_path.write_text(
                synthetic_vdw_resource(),
                encoding="utf-8",
            )
            self.assertEqual(
                tuple(load_vdw_radii(resource_path)),
                SUPPORTED_VDW_ELEMENTS,
            )

            resource_path.write_text(
                synthetic_vdw_resource().replace(
                    'radius_definition = "Synthetic free-atom radius."\n',
                    "",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                VdwRadiiError,
                "metadata.radius_definition must be a non-empty string",
            ):
                load_vdw_radii(resource_path)

            resource_path.write_text(
                synthetic_vdw_resource(SUPPORTED_VDW_ELEMENTS[:-1]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(VdwRadiiError, "missing: Bi"):
                load_vdw_radii(resource_path)

            resource_path.write_text(
                synthetic_vdw_resource().replace(
                    "Au = 1.0",
                    "Au = 0.0",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(VdwRadiiError, "greater than zero"):
                load_vdw_radii(resource_path)


if __name__ == "__main__":
    unittest.main()
