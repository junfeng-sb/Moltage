import unittest
from pathlib import PurePosixPath

from moltage.aims.optimization_settings import SpeciesAccuracy
from moltage.aims.species_library import (
    InMemorySpeciesLibrary,
    MalformedSpeciesDefaultError,
    MissingSpeciesDefaultError,
    SpeciesDefaultBlock,
    SpeciesLibraryError,
    species_default_block_from_text,
    species_default_filename,
)
from species_test_support import (
    TEST_SPECIES_ROOT,
    synthetic_species_block,
    synthetic_species_library,
    synthetic_species_text,
)


class SpeciesLibraryTests(unittest.TestCase):
    def test_exact_filename_uses_atomic_number_and_element(self) -> None:
        self.assertEqual(
            species_default_filename("H", SpeciesAccuracy.TIGHT),
            "01_H_default",
        )
        self.assertEqual(
            species_default_filename("Au", SpeciesAccuracy.REALLY_TIGHT),
            "79_Au_default",
        )

    def test_in_memory_provider_loads_exact_element_and_accuracy(self) -> None:
        library = synthetic_species_library(
            ("H", "C"),
            (SpeciesAccuracy.LIGHT, SpeciesAccuracy.TIGHT),
        )

        hydrogen = library.load("H", SpeciesAccuracy.TIGHT)
        carbon = library.load("C", SpeciesAccuracy.LIGHT)

        self.assertIn("SYNTHETIC TEST SPECIES tight", hydrogen.text)
        self.assertIn("species H\n", hydrogen.text)
        self.assertIn("SYNTHETIC TEST SPECIES light", carbon.text)
        self.assertIn("species C\n", carbon.text)

    def test_alias_rewrites_only_the_active_species_declaration(self) -> None:
        library = synthetic_species_library(
            ("C",),
            (SpeciesAccuracy.LIGHT,),
        )
        source_text = synthetic_species_text("C", SpeciesAccuracy.LIGHT)

        aliased = library.load(
            "C",
            SpeciesAccuracy.LIGHT,
            species_name="C_light",
        )

        self.assertEqual(
            aliased.text.replace("species C_light\n", "species C\n", 1),
            source_text,
        )

    def test_missing_exact_block_never_substitutes_another_accuracy(self) -> None:
        library = synthetic_species_library(
            ("Au",),
            (SpeciesAccuracy.LIGHT,),
        )

        with self.assertRaises(MissingSpeciesDefaultError) as caught:
            library.load("Au", SpeciesAccuracy.TIGHT)

        message = str(caught.exception)
        self.assertIn("element=Au", message)
        self.assertIn("accuracy=tight", message)

    def test_malformed_text_without_unique_expected_declaration_fails(self) -> None:
        cases = {
            "missing": "# no active declaration\n",
            "wrong": "species C\n",
            "duplicate": "species H\nspecies H\n",
        }
        path = PurePosixPath(TEST_SPECIES_ROOT) / "tight" / "01_H_default"
        for name, text in cases.items():
            with self.subTest(name=name), self.assertRaises(
                MalformedSpeciesDefaultError
            ):
                species_default_block_from_text(
                    "H",
                    SpeciesAccuracy.TIGHT,
                    path,
                    text,
                )

    def test_in_memory_provider_revalidates_supplied_blocks(self) -> None:
        valid = synthetic_species_block("H", SpeciesAccuracy.TIGHT)
        invalid = SpeciesDefaultBlock(
            "C",
            "C",
            SpeciesAccuracy.TIGHT,
            PurePosixPath(TEST_SPECIES_ROOT) / "tight" / "06_C_default",
            "species N\n",
        )
        with self.assertRaises(MalformedSpeciesDefaultError):
            InMemorySpeciesLibrary((valid, invalid))

    def test_unsupported_accuracy_fails_without_substitution(self) -> None:
        with self.assertRaisesRegex(SpeciesLibraryError, "unsupported"):
            species_default_filename("H", "intermediate")


if __name__ == "__main__":
    unittest.main()
