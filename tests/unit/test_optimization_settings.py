import unittest

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
from moltage.aims.orbital_cube import FRONTIER_ORBITAL_ORDER


class OptimizationSettingsTests(unittest.TestCase):
    def test_vdw_methods_are_exactly_the_approved_mvp_set(self) -> None:
        self.assertEqual(
            tuple(VdwMethod),
            (
                VdwMethod.NONE,
                VdwMethod.TS_HIRSHFELD,
                VdwMethod.TS_LIBMBD,
            ),
        )

    def test_project_defaults_are_frozen(self) -> None:
        settings = AimsOptimizationSettings()

        self.assertIs(settings.xc, XCFunctional.PBE)
        self.assertIs(settings.vdw, VdwMethod.TS_HIRSHFELD)
        self.assertIs(settings.relativity, Relativity.ATOMIC_ZORA_SCALAR)
        self.assertIs(settings.species_accuracy, SpeciesAccuracy.TIGHT)
        self.assertEqual(settings.force_threshold, 1.0e-2)
        self.assertTrue(settings.output_dipole)
        self.assertEqual(
            settings.orbital_cubes.frontier_orbitals,
            FRONTIER_ORBITAL_ORDER,
        )
        self.assertEqual(settings.orbital_cubes.eigenstate_indices, ())
        self.assertIsNone(settings.orbital_cubes.grid_spacing_angstrom)
        self.assertEqual(settings.total_charge, 0.0)
        self.assertFalse(settings.spin.enabled)
        self.assertEqual(settings.atom_settings, ())

    def test_force_threshold_must_be_positive_and_finite(self) -> None:
        for value in (0.0, -0.01, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    AimsSettingsValidationError,
                    "force threshold",
                ):
                    AimsOptimizationSettings(force_threshold=value)

    def test_duplicate_atom_override_records_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "duplicate atom override",
        ):
            AimsOptimizationSettings(
                atom_settings=(
                    AtomAimsSettings(0, initial_charge=0.25),
                    AtomAimsSettings(0, species_accuracy=SpeciesAccuracy.LIGHT),
                )
            )

    def test_fixed_total_moment_requires_spin_collinear(self) -> None:
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "fixed_spin_moment requires spin collinear",
        ):
            SpinSettings(fixed_spin_moment=4)

    def test_per_atom_spin_requires_nonzero_explicit_moment(self) -> None:
        spin = SpinSettings(
            enabled=True,
            initialization_mode=SpinInitializationMode.PER_ATOM,
        )
        for atom_settings in (
            (),
            (AtomAimsSettings(0, initial_moment=0.0),),
        ):
            with self.subTest(atom_settings=atom_settings):
                with self.assertRaisesRegex(
                    AimsSettingsValidationError,
                    "explicit nonzero initial moment",
                ):
                    AimsOptimizationSettings(
                        spin=spin,
                        atom_settings=atom_settings,
                    )

    def test_uniform_spin_requires_supplied_nonzero_per_atom_value(self) -> None:
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "requires a moment per atom",
        ):
            SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.UNIFORM_DEFAULT,
            )
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "must be nonzero",
        ):
            SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.UNIFORM_DEFAULT,
                uniform_initial_moment=0.0,
            )

    def test_spin_off_rejects_per_atom_initial_moment(self) -> None:
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "initial moments require spin collinear",
        ):
            AimsOptimizationSettings(
                atom_settings=(AtomAimsSettings(0, initial_moment=2.0),)
            )

    def test_atom_initial_charge_is_independent_of_total_charge(self) -> None:
        settings = AimsOptimizationSettings(
            total_charge=1.5,
            atom_settings=(
                AtomAimsSettings(0, initial_charge=-7.25),
                AtomAimsSettings(1, initial_charge=0.125),
            ),
        )

        self.assertEqual(settings.total_charge, 1.5)
        self.assertEqual(
            tuple(item.initial_charge for item in settings.atom_settings),
            (-7.25, 0.125),
        )


if __name__ == "__main__":
    unittest.main()
