import unittest

from PySide6.QtWidgets import QApplication, QGroupBox

from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AimsSettingsValidationError,
    AtomAimsSettings,
    Relativity,
    SpeciesAccuracy,
    SpinInitializationMode,
    VdwMethod,
    XCFunctional,
)
from moltage.aims.orbital_cube import (
    FRONTIER_ORBITAL_ORDER,
    FrontierOrbital,
)
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.optimization_dialog import (
    AimsOptimizationSettingsDialog,
    AtomOverrideDialog,
)


class OptimizationDialogSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.structure = MolecularStructure(
            (
                Atom(0, "Co", 0, 0, 0),
                Atom(1, "C", 1, 0, 0),
                Atom(2, "Au", 2, 0, 0),
            )
        )

    def test_dialog_defaults_match_project_profile_and_labels(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)

        self.assertEqual(dialog.windowTitle(), "FHI-aims Optimization Settings")
        self.assertEqual(dialog._xc.currentText(), "PBE")
        self.assertEqual(
            tuple(
                dialog._vdw.itemText(index)
                for index in range(dialog._vdw.count())
            ),
            ("None", "TS (Hirshfeld)", "TS (libmbd)"),
        )
        self.assertEqual(dialog._vdw.currentText(), "TS (Hirshfeld)")
        self.assertEqual(dialog._relativity.currentText(), "Atomic ZORA scalar")
        self.assertEqual(dialog._species_accuracy.currentText(), "Tight")
        self.assertEqual(dialog._force_threshold.value(), 1.0e-2)
        self.assertFalse(dialog._spin_enabled.isChecked())
        self.assertEqual(dialog._spin_state.text(), "spin none")
        self.assertFalse(dialog._spin_options.isEnabled())
        self.assertFalse(dialog._charged_system.isChecked())
        self.assertFalse(dialog._total_charge.isEnabled())
        self.assertTrue(dialog._output_dipole.isChecked())
        self.assertTrue(
            all(
                dialog._frontier_orbital_checks[item].isChecked()
                for item in FRONTIER_ORBITAL_ORDER
            )
        )
        self.assertFalse(dialog._additional_orbitals_enabled.isChecked())
        self.assertFalse(dialog._orbital_eigenstates.isEnabled())
        self.assertEqual(dialog._orbital_eigenstates.text(), "")
        self.assertEqual(dialog._orbital_grid_spacing.text(), "")
        self.assertEqual(dialog._override_list.count(), 0)
        self.assertEqual(dialog._collect_settings(), AimsOptimizationSettings())
        dialog.close()

    def test_frontier_custom_states_and_grid_spacing_collect(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        dialog._frontier_orbital_checks[
            FrontierOrbital.HOMO_MINUS_2
        ].setChecked(False)
        dialog._frontier_orbital_checks[
            FrontierOrbital.LUMO_PLUS_2
        ].setChecked(False)
        dialog._additional_orbitals_enabled.setChecked(True)
        dialog._orbital_eigenstates.setText("31, 12")
        dialog._orbital_grid_spacing.setText("0.08")

        settings = dialog._collect_settings().orbital_cubes

        self.assertNotIn(FrontierOrbital.HOMO_MINUS_2, settings.frontier_orbitals)
        self.assertNotIn(FrontierOrbital.LUMO_PLUS_2, settings.frontier_orbitals)
        self.assertEqual(settings.eigenstate_indices, (12, 31))
        self.assertEqual(settings.grid_spacing_angstrom, 0.08)
        dialog.close()

    def test_unchecking_additional_states_disables_and_ignores_saved_text(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        dialog._additional_orbitals_enabled.setChecked(True)
        dialog._orbital_eigenstates.setText("12, 31")
        dialog._additional_orbitals_enabled.setChecked(False)

        self.assertFalse(dialog._orbital_eigenstates.isEnabled())
        self.assertEqual(
            dialog._collect_settings().orbital_cubes.eigenstate_indices,
            (),
        )
        dialog.close()

    def test_settings_are_arranged_in_two_compact_columns(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        columns = dialog.layout().itemAt(0).layout()

        self.assertIsNotNone(columns)
        self.assertEqual(columns.count(), 2)
        self.assertIsNotNone(
            dialog.findChild(QGroupBox, "optimizationGeneralGroup")
        )
        self.assertLess(dialog.sizeHint().height(), 800)
        dialog.close()

    def test_per_atom_spin_requires_user_supplied_nonzero_moment(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        dialog._spin_enabled.setChecked(True)

        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "explicit nonzero initial moment",
        ):
            dialog._collect_settings()

        dialog._atom_settings[0] = AtomAimsSettings(0, initial_moment=2.0)
        settings = dialog._collect_settings()
        self.assertTrue(settings.spin.enabled)
        self.assertIs(
            settings.spin.initialization_mode,
            SpinInitializationMode.PER_ATOM,
        )
        self.assertEqual(settings.atom_settings[0].initial_moment, 2.0)
        dialog.close()

    def test_uniform_spin_fixed_total_charge_and_all_curated_choices_collect(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        dialog._xc.setCurrentIndex(dialog._xc.findData(XCFunctional.B3LYP))
        dialog._vdw.setCurrentIndex(dialog._vdw.findData(VdwMethod.TS_LIBMBD))
        dialog._relativity.setCurrentIndex(
            dialog._relativity.findData(Relativity.NONE)
        )
        dialog._species_accuracy.setCurrentIndex(
            dialog._species_accuracy.findData(SpeciesAccuracy.REALLY_TIGHT)
        )
        dialog._spin_enabled.setChecked(True)
        dialog._uniform_spin.setChecked(True)
        dialog._uniform_moment.setValue(1.5)
        dialog._fixed_spin.setChecked(True)
        dialog._fixed_spin_value.setValue(4.0)
        dialog._charged_system.setChecked(True)
        dialog._total_charge.setValue(-0.5)

        settings = dialog._collect_settings()

        self.assertIs(settings.xc, XCFunctional.B3LYP)
        self.assertIs(settings.vdw, VdwMethod.TS_LIBMBD)
        self.assertIs(settings.relativity, Relativity.NONE)
        self.assertIs(settings.species_accuracy, SpeciesAccuracy.REALLY_TIGHT)
        self.assertIs(
            settings.spin.initialization_mode,
            SpinInitializationMode.UNIFORM_DEFAULT,
        )
        self.assertEqual(settings.spin.uniform_initial_moment, 1.5)
        self.assertEqual(settings.spin.fixed_spin_moment, 4.0)
        self.assertEqual(settings.total_charge, -0.5)
        dialog.close()

    def test_uniform_spin_has_no_automatic_nonzero_moment(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        dialog._spin_enabled.setChecked(True)
        dialog._uniform_spin.setChecked(True)

        self.assertEqual(dialog._uniform_moment.value(), 0.0)
        with self.assertRaisesRegex(
            AimsSettingsValidationError,
            "must be nonzero",
        ):
            dialog._collect_settings()
        dialog.close()

    def test_compact_override_summary_uses_zero_based_atom_references(self) -> None:
        dialog = AimsOptimizationSettingsDialog(self.structure)
        dialog._atom_settings = {
            0: AtomAimsSettings(0, initial_moment=2.0),
            2: AtomAimsSettings(
                2,
                species_accuracy=SpeciesAccuracy.LIGHT,
                initial_charge=-0.5,
            ),
        }
        dialog._refresh_override_list()

        self.assertEqual(dialog._override_list.count(), 2)
        self.assertEqual(dialog._override_list.item(0).text(), "Co0    moment=2.0")
        self.assertEqual(
            dialog._override_list.item(1).text(),
            "Au2    accuracy=light, initial_charge=-0.5",
        )
        dialog.close()

    def test_atom_initial_moment_controls_are_inactive_when_spin_is_off(self) -> None:
        editor = AtomOverrideDialog(
            self.structure,
            allow_initial_moment=False,
        )

        self.assertFalse(editor._moment_enabled.isEnabled())
        self.assertFalse(editor._moment.isEnabled())
        self.assertEqual(editor._accuracy.itemText(0), "Inherit")
        editor.close()

    def test_cancel_does_not_create_an_accepted_settings_result(self) -> None:
        initial = AimsOptimizationSettings()
        dialog = AimsOptimizationSettingsDialog(self.structure, initial)

        dialog.reject()

        with self.assertRaises(RuntimeError):
            dialog.selected_settings()
        self.assertEqual(initial, AimsOptimizationSettings())


if __name__ == "__main__":
    unittest.main()
