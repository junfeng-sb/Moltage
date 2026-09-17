import re
import tempfile
import unittest
from pathlib import Path

from moltage.aims.control_writer import (
    AimsControlGenerationError,
    render_control_in,
)
from moltage.aims.input_bundle import (
    ExistingAimsInputError,
    build_aims_optimization_inputs,
    write_aims_optimization_inputs,
)
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AimsSettingsValidationError,
    AtomAimsSettings,
    Relativity,
    SpeciesAccuracy,
    SpinInitializationMode,
    SpinSettings,
    VdwMethod,
    XCFunctional,
)
from moltage.aims.species_library import (
    InMemorySpeciesLibrary,
    MissingSpeciesDefaultError,
)
from moltage.domain.structure import Atom, MolecularStructure
from species_test_support import synthetic_species_block, synthetic_species_library


class AimsInputBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hydrogen_structure = MolecularStructure(
            (
                Atom(0, "H", 0.0, 0.5, -1.0),
                Atom(1, "H", 1.25, 0.0, 0.0),
            ),
            comment="source remains immutable",
        )

    def test_default_profile_and_tight_species_are_generated(self) -> None:
        bundle = build_aims_optimization_inputs(
            self.hydrogen_structure,
            AimsOptimizationSettings(),
            synthetic_species_library(),
        )

        self.assertEqual(
            bundle.geometry_text,
            "atom 0.0 0.5 -1.0 H\natom 1.25 0.0 0.0 H\n",
        )
        for expected in (
            "xc pbe",
            "spin none",
            "charge 0.",
            "relativistic atomic_zora scalar",
            "relax_geometry bfgs 1.e-2",
            "vdw_correction_hirshfeld",
            "output dipole",
            "output cube eigenstate homo-2",
            "cube filename orbital_HOMO_minus_2.cube",
            "output cube eigenstate lumo+2",
            "cube filename orbital_LUMO_plus_2.cube",
            "SYNTHETIC TEST SPECIES tight",
        ):
            self.assertIn(expected, bundle.control_text)
        self.assertEqual(_active_species_names(bundle.control_text), ("H",))

    def test_all_six_xc_mappings_are_exact(self) -> None:
        expected = {
            XCFunctional.PBE: "pbe",
            XCFunctional.PBE0: "pbe0",
            XCFunctional.BLYP: "blyp",
            XCFunctional.B3LYP: "b3lyp",
            XCFunctional.REVPBE: "revpbe",
            XCFunctional.AM05: "am05",
        }
        for functional, keyword in expected.items():
            with self.subTest(functional=functional):
                control = self._build(AimsOptimizationSettings(xc=functional)).control_text
                self.assertIn(f"xc {keyword}\n", control)

    def test_hybrid_functionals_receive_lvl_fast_only(self) -> None:
        for functional in XCFunctional:
            with self.subTest(functional=functional):
                control = self._build(AimsOptimizationSettings(xc=functional)).control_text
                self.assertEqual(
                    "RI_method LVL_fast" in control,
                    functional in {XCFunctional.PBE0, XCFunctional.B3LYP},
                )

    def test_all_three_vdw_mappings_are_exact_and_unsupported_variants_are_absent(self) -> None:
        expected = {
            VdwMethod.NONE: None,
            VdwMethod.TS_HIRSHFELD: "vdw_correction_hirshfeld",
            VdwMethod.TS_LIBMBD: "vdw_ts",
        }
        all_keywords = tuple(keyword for keyword in expected.values() if keyword)
        unsupported_keywords = (
            "many_body_dispersion",
            "many_body_dispersion_nl",
            "vdw_correction_hirshfeld_sc",
        )
        for method, keyword in expected.items():
            with self.subTest(method=method):
                control = self._build(AimsOptimizationSettings(vdw=method)).control_text
                present = tuple(
                    item for item in all_keywords
                    if re.search(rf"^{re.escape(item)}$", control, re.MULTILINE)
                )
                self.assertEqual(present, () if keyword is None else (keyword,))
                for unsupported_keyword in unsupported_keywords:
                    self.assertNotIn(unsupported_keyword, control)

    def test_both_relativity_mappings_are_exact(self) -> None:
        expected = {
            Relativity.ATOMIC_ZORA_SCALAR: "atomic_zora scalar",
            Relativity.NONE: "none",
        }
        for relativity, keyword in expected.items():
            with self.subTest(relativity=relativity):
                control = self._build(
                    AimsOptimizationSettings(relativity=relativity)
                ).control_text
                self.assertIn(f"relativistic {keyword}\n", control)

    def test_spin_off_emits_no_spin_initialization_or_fixed_moment(self) -> None:
        bundle = self._build(AimsOptimizationSettings())

        self.assertIn("spin none\n", bundle.control_text)
        self.assertNotIn("default_initial_moment", bundle.control_text)
        self.assertNotIn("fixed_spin_moment", bundle.control_text)
        self.assertNotIn("initial_moment", bundle.geometry_text)

    def test_per_atom_spin_and_charge_follow_the_correct_atom_without_reordering(self) -> None:
        original_atoms = self.hydrogen_structure.atoms
        settings = AimsOptimizationSettings(
            spin=SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.PER_ATOM,
            ),
            atom_settings=(
                AtomAimsSettings(1, initial_moment=2.0, initial_charge=-0.5),
                AtomAimsSettings(0, initial_charge=3.25),
            ),
        )

        bundle = self._build(settings)

        self.assertEqual(
            bundle.geometry_text,
            "atom 0.0 0.5 -1.0 H\n"
            "initial_charge 3.25\n"
            "atom 1.25 0.0 0.0 H\n"
            "initial_moment 2.0\n"
            "initial_charge -0.5\n",
        )
        self.assertIn("spin collinear\n", bundle.control_text)
        self.assertIs(self.hydrogen_structure.atoms, original_atoms)

    def test_uniform_initial_moment_is_per_atom_control_setting(self) -> None:
        settings = AimsOptimizationSettings(
            spin=SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.UNIFORM_DEFAULT,
                uniform_initial_moment=1.25,
            )
        )

        bundle = self._build(settings)

        self.assertIn("spin collinear\n", bundle.control_text)
        self.assertIn("default_initial_moment 1.25\n", bundle.control_text)
        self.assertNotIn("initial_moment", bundle.geometry_text)

    def test_fixed_total_spin_is_separate_from_initial_density(self) -> None:
        settings = AimsOptimizationSettings(
            spin=SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.PER_ATOM,
                fixed_spin_moment=4,
            ),
            atom_settings=(AtomAimsSettings(0, initial_moment=2.0),),
        )

        control = self._build(settings).control_text

        self.assertIn("spin collinear\n", control)
        self.assertIn("fixed_spin_moment 4\n", control)

    def test_total_charge_formats_positive_negative_zero_and_fractional_values(self) -> None:
        expected = {
            0: "0.",
            2: "2.",
            -3: "-3.",
            0.625: "0.625",
            -1.25: "-1.25",
        }
        for charge, rendered in expected.items():
            with self.subTest(charge=charge):
                control = self._build(
                    AimsOptimizationSettings(total_charge=charge)
                ).control_text
                self.assertIn(f"charge {rendered}\n", control)

    def test_global_accuracy_selects_exact_directory_without_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = self._test_library(
                Path(directory),
                tuple(("H", accuracy) for accuracy in SpeciesAccuracy),
            )
            for accuracy in SpeciesAccuracy:
                with self.subTest(accuracy=accuracy):
                    bundle = build_aims_optimization_inputs(
                        self.hydrogen_structure,
                        AimsOptimizationSettings(species_accuracy=accuracy),
                        library,
                    )
                    self.assertEqual(_active_species_names(bundle.control_text), ("H",))
                    self.assertIn(
                        f"# SYNTHETIC TEST SPECIES {accuracy.value}",
                        bundle.control_text,
                    )
                    self.assertNotIn(f"H_{accuracy.value}", bundle.geometry_text)

    def test_mixed_accuracy_aliases_and_blocks_follow_first_geometry_appearance(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "C", 0, 0, 0),
                Atom(1, "C", 1, 0, 0),
                Atom(2, "C", 2, 0, 0),
                Atom(3, "Au", 3, 0, 0),
            )
        )
        settings = AimsOptimizationSettings(
            atom_settings=(
                AtomAimsSettings(1, species_accuracy=SpeciesAccuracy.LIGHT),
                AtomAimsSettings(2, species_accuracy=SpeciesAccuracy.LIGHT),
                AtomAimsSettings(
                    3,
                    species_accuracy=SpeciesAccuracy.REALLY_TIGHT,
                ),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            library = self._test_library(
                Path(directory),
                (
                    ("C", SpeciesAccuracy.TIGHT),
                    ("C", SpeciesAccuracy.LIGHT),
                    ("Au", SpeciesAccuracy.REALLY_TIGHT),
                ),
            )

            bundle = build_aims_optimization_inputs(structure, settings, library)

        self.assertEqual(
            tuple(line.split()[-1] for line in bundle.geometry_text.splitlines()),
            ("C", "C_light", "C_light", "Au_really_tight"),
        )
        self.assertEqual(
            _active_species_names(bundle.control_text),
            ("C", "C_light", "Au_really_tight"),
        )

    def test_only_species_referenced_by_geometry_are_loaded(self) -> None:
        structure = MolecularStructure(
            tuple(
                Atom(index, element, float(index), 0, 0)
                for index, element in enumerate(("C", "H", "S", "Au"))
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            library = self._test_library(
                Path(directory),
                tuple(
                    (element, SpeciesAccuracy.TIGHT)
                    for element in ("C", "H", "S", "Au", "N", "O", "Cl", "Ru", "Co")
                ),
            )

            bundle = build_aims_optimization_inputs(
                structure,
                AimsOptimizationSettings(),
                library,
            )

        self.assertEqual(_active_species_names(bundle.control_text), ("C", "H", "S", "Au"))

    def test_missing_requested_accuracy_never_falls_back(self) -> None:
        structure = MolecularStructure((Atom(0, "C", 0, 0, 0),))
        with tempfile.TemporaryDirectory() as directory:
            library = self._test_library(
                Path(directory),
                (("C", SpeciesAccuracy.LIGHT),),
            )
            with self.assertRaises(MissingSpeciesDefaultError) as caught:
                build_aims_optimization_inputs(
                    structure,
                    AimsOptimizationSettings(),
                    library,
                )
        self.assertIn("element=C", str(caught.exception))
        self.assertIn("accuracy=tight", str(caught.exception))

    def test_atom_override_index_must_exist_in_structure(self) -> None:
        settings = AimsOptimizationSettings(
            atom_settings=(AtomAimsSettings(9, initial_charge=0.5),)
        )
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "index does not exist: 9",
        ):
            self._build(settings)

    def test_structure_identity_coordinates_order_and_comment_are_unchanged(self) -> None:
        before = self.hydrogen_structure
        atoms_before = before.atoms

        first = self._build(AimsOptimizationSettings())
        second = self._build(AimsOptimizationSettings())

        self.assertIs(self.hydrogen_structure, before)
        self.assertIs(self.hydrogen_structure.atoms, atoms_before)
        self.assertEqual(self.hydrogen_structure.comment, "source remains immutable")
        self.assertEqual(first, second)

    def test_file_writer_rejects_implicit_overwrite_and_allows_explicit_overwrite(self) -> None:
        bundle = self._build(AimsOptimizationSettings())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "calculation"
            geometry_path, control_path = write_aims_optimization_inputs(
                bundle,
                destination,
            )
            geometry_path.write_text("user geometry\n", encoding="utf-8")

            with self.assertRaises(ExistingAimsInputError):
                write_aims_optimization_inputs(bundle, destination)
            self.assertEqual(
                geometry_path.read_text(encoding="utf-8"),
                "user geometry\n",
            )

            write_aims_optimization_inputs(bundle, destination, overwrite=True)
            self.assertEqual(geometry_path.read_text(encoding="utf-8"), bundle.geometry_text)
            self.assertEqual(control_path.read_text(encoding="utf-8"), bundle.control_text)

    def test_control_writer_explicitly_rejects_corrupted_unsupported_enums(self) -> None:
        library = synthetic_species_library()
        block = library.load("H", SpeciesAccuracy.TIGHT)
        for field_name, expected_error in (
            ("xc", "unsupported XC functional"),
            ("vdw", "unsupported vdW method"),
        ):
            with self.subTest(field_name=field_name):
                settings = AimsOptimizationSettings()
                object.__setattr__(settings, field_name, "unsupported")
                with self.assertRaisesRegex(
                    AimsControlGenerationError,
                    expected_error,
                ):
                    render_control_in(settings, (block,))

    def _build(self, settings: AimsOptimizationSettings):
        return build_aims_optimization_inputs(
            self.hydrogen_structure,
            settings,
            synthetic_species_library(),
        )

    def _test_library(
        self,
        root: Path,
        entries: tuple[tuple[str, SpeciesAccuracy], ...],
    ) -> InMemorySpeciesLibrary:
        del root
        return InMemorySpeciesLibrary(
            synthetic_species_block(element, accuracy)
            for element, accuracy in entries
        )


def _active_species_names(control_text: str) -> tuple[str, ...]:
    return tuple(
        match.group(1)
        for line in control_text.splitlines()
        if (match := re.fullmatch(r"\s*species\s+(\S+)\s*", line))
    )


if __name__ == "__main__":
    unittest.main()
