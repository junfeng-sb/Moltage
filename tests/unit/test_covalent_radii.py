import tempfile
import tomllib
import unittest
from pathlib import Path

from moltage.structure.covalent_radii import (
    CovalentRadiiError,
    DEFAULT_COVALENT_RADII_PATH,
    load_covalent_radii,
    load_default_covalent_radii,
)
from moltage.visualization.molecule_scene import ELEMENT_COLORS_RGB


SUPPORTED_ELEMENTS_Z1_TO_Z83 = (
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
)


def synthetic_covalent_resource(
    elements: tuple[str, ...] = SUPPORTED_ELEMENTS_Z1_TO_Z83,
) -> str:
    metadata = (
        "[metadata]\n"
        'citation = "Synthetic citation"\n'
        'source_url = "https://example.invalid/covalent"\n'
        'source_version = "synthetic-v1"\n'
        'license = "Synthetic permissive license"\n'
        'license_url = "https://example.invalid/license"\n'
        'units = "angstrom"\n'
        'original_units = "angstrom"\n'
        'radius_definition = "Synthetic additive radius."\n'
        'coverage = "Synthetic H-Bi coverage."\n'
        'curation_note = "Synthetic test data only."\n'
        "\n[radii]\n"
    )
    return metadata + "".join(f"{element} = 1.0\n" for element in elements)


class CovalentRadiiTests(unittest.TestCase):
    def test_default_resource_covers_exactly_elements_z1_to_z83(self) -> None:
        radii = load_default_covalent_radii()

        self.assertEqual(tuple(radii), SUPPORTED_ELEMENTS_Z1_TO_Z83)
        self.assertEqual(len(radii), 83)
        self.assertEqual(
            {
                element: radii[element]
                for element in ("H", "C", "O", "Cr", "Co", "Au", "Bi")
            },
            {
                "H": 0.32,
                "C": 0.75,
                "O": 0.63,
                "Cr": 1.22,
                "Co": 1.11,
                "Au": 1.24,
                "Bi": 1.51,
            },
        )

    def test_default_resource_records_approved_provenance(self) -> None:
        with DEFAULT_COVALENT_RADII_PATH.open("rb") as stream:
            metadata = tomllib.load(stream)["metadata"]

        self.assertEqual(metadata["doi"], "10.1002/chem.200800987")
        self.assertEqual(metadata["license"], "CC0-1.0")
        self.assertEqual(metadata["units"], "angstrom")
        self.assertIn("R(AB) = r(A) + r(B)", metadata["radius_definition"])
        self.assertIn("abff999b6bc6a893252eed7df0fae52ddfeed9dd", metadata["source_version"])

    def test_visualization_colors_cover_exactly_elements_z1_to_z83(self) -> None:
        self.assertEqual(tuple(ELEMENT_COLORS_RGB), SUPPORTED_ELEMENTS_Z1_TO_Z83)
        self.assertEqual(len(ELEMENT_COLORS_RGB), 83)

    def test_missing_resource_is_reported_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_path = Path(temporary_directory) / "missing.toml"

            with self.assertRaisesRegex(
                CovalentRadiiError, "resource does not exist"
            ):
                load_covalent_radii(missing_path)

    def test_malformed_toml_is_reported_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            resource_path = Path(temporary_directory) / "malformed.toml"
            resource_path.write_text("[metadata\n", encoding="utf-8")

            with self.assertRaisesRegex(CovalentRadiiError, "malformed TOML"):
                load_covalent_radii(resource_path)

    def test_source_agnostic_metadata_and_exact_coverage_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            resource_path = Path(temporary_directory) / "synthetic.toml"
            resource_path.write_text(
                synthetic_covalent_resource(),
                encoding="utf-8",
            )
            self.assertEqual(
                tuple(load_covalent_radii(resource_path)),
                SUPPORTED_ELEMENTS_Z1_TO_Z83,
            )

            resource_path.write_text(
                synthetic_covalent_resource().replace(
                    'license_url = "https://example.invalid/license"\n',
                    "",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CovalentRadiiError,
                "metadata.license_url must be a non-empty string",
            ):
                load_covalent_radii(resource_path)

            resource_path.write_text(
                synthetic_covalent_resource(SUPPORTED_ELEMENTS_Z1_TO_Z83[:-1]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CovalentRadiiError, "missing: Bi"):
                load_covalent_radii(resource_path)

            resource_path.write_text(
                synthetic_covalent_resource().replace(
                    "H = 1.0",
                    "H = 0.0",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CovalentRadiiError, "greater than zero"):
                load_covalent_radii(resource_path)


if __name__ == "__main__":
    unittest.main()
