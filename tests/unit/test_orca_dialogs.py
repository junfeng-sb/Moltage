"""Qt tests for structured ORCA optimization and frequency choices."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from moltage.app.orca_submission import (
    OrcaOptimizationSubmissionRequest,
    OrcaSubmissionService,
)
from moltage.app.orca_wbl import OrcaWblRequest, OrcaWblService
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
)
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER
from moltage.gui.orca_dialogs import (
    OrcaFrequencySettingsDialog,
    OrcaOptimizationSettingsDialog,
    OrcaSubmissionConfirmationDialog,
    OrcaSubmissionWorker,
    OrcaWblSettingsDialog,
    OrcaWblWorker,
)
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaOptimizationConvergence,
    OrcaScfConvergence,
    OrcaVersionEvidence,
    OrcaVersionFamily,
    method_capability,
)
from moltage.orca.settings import OrcaOptimizationSettings
from moltage.orca.wbl import (
    OrcaWblContactSettings,
    OrcaWblSettings,
    WblContactSubspaceMode,
    WblLinkerKind,
    WblParameterStatus,
)
from phase2b1_test_support import profile


def water():
    return MolecularStructure(
        (
            Atom(0, "O", 0.0, 0.0, 0.0),
            Atom(1, "H", 0.7, 0.5, 0.0),
            Atom(2, "H", -0.7, 0.5, 0.0),
        ),
        "synthetic water",
    )


def synthetic_dithiol():
    return MolecularStructure(
        (
            Atom(0, "H", -2.0, 0.0, 0.0),
            Atom(1, "S", -1.0, 0.0, 0.0),
            Atom(2, "C", 0.0, 0.0, 0.0),
            Atom(3, "S", 1.0, 0.0, 0.0),
            Atom(4, "H", 2.0, 0.0, 0.0),
        ),
        "synthetic dithiol",
    )


def synthetic_connectivity(structure):
    return Connectivity(
        len(structure),
        (
            Bond(0, 1, 1.0),
            Bond(1, 2, 1.0),
            Bond(2, 3, 1.0),
            Bond(3, 4, 1.0),
        ),
    )


def synthetic_diisothiocyanate():
    return MolecularStructure(
        (
            Atom(0, "S", 0.0, 0.0, 0.0),
            Atom(1, "C", 1.6, 0.0, 0.0),
            Atom(2, "N", 2.8, 0.0, 0.0),
            Atom(3, "C", 4.2, 0.0, 0.0),
            Atom(4, "C", 5.6, 0.0, 0.0),
            Atom(5, "N", 7.0, 0.0, 0.0),
            Atom(6, "C", 8.2, 0.0, 0.0),
            Atom(7, "S", 9.8, 0.0, 0.0),
        ),
        "synthetic di-isothiocyanate",
    )


def chain_connectivity(structure):
    return Connectivity(
        len(structure),
        tuple(Bond(index, index + 1, 1.0) for index in range(len(structure) - 1)),
    )


def unbonded_contact_connectivity(structure):
    """Connectivity a too-small bond threshold factor leaves for the dithiol."""

    return Connectivity(len(structure), (Bond(1, 2, 1.0), Bond(2, 3, 1.0)))


def orca_profile(family=OrcaVersionFamily.V6_1):
    value = "6.1.2" if family is OrcaVersionFamily.V6_1 else "5.0.4"
    return replace(
        profile(),
        orca_runtime=OrcaRuntimeConfiguration(
            f"/apps/example/orca-{family.value}/orca",
            RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
            OrcaVersionEvidence(
                f"Program Version {value}",
                value,
                family,
                "synthetic validation",
            ),
        ),
    )


def source_settings(method=OrcaMethod.PBE0):
    capability = method_capability(method)
    return OrcaOptimizationSettings(
        method=method,
        basis=OrcaBasis.DEF2_TZVP if capability.requires_basis else None,
        dispersion=OrcaDispersion.NONE,
        version_family=OrcaVersionFamily.V6_1,
    )


class OrcaDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_submission_worker_survives_until_queued_finished_delivery(self):
        service = OrcaSubmissionService(object(), object())
        request = OrcaOptimizationSubmissionRequest(
            profile=orca_profile(),
            base_name="SyntheticOrcaResubmit",
            source_molecule_name="synthetic.xyz",
            structure=water(),
            settings=source_settings(),
        )

        worker = OrcaSubmissionWorker(service, request)

        self.assertFalse(worker.autoDelete())

    def test_wbl_worker_survives_until_queued_finished_delivery(self):
        contact_status = WblParameterStatus.HYPOTHESIS
        settings = OrcaWblSettings(
            left=OrcaWblContactSettings(
                0,
                WblLinkerKind.SH,
                gamma0_ev=0.25,
                parameter_status=contact_status,
            ),
            right=OrcaWblContactSettings(
                1,
                WblLinkerKind.SH,
                gamma0_ev=0.25,
                parameter_status=contact_status,
            ),
            fermi_energy_ev=-5.1,
            energy_min_relative_ev=-5.0,
            energy_max_relative_ev=5.0,
            energy_step_ev=0.01,
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
        )
        service = OrcaWblService(object(), object())
        request = OrcaWblRequest(
            orca_profile(),
            "/work/example/SyntheticOrca",
            settings,
        )

        worker = OrcaWblWorker(service, request)

        self.assertFalse(worker.autoDelete())

    def test_optimization_has_no_scientific_choice_defaults_and_filters_version(self):
        dialog = OrcaOptimizationSettingsDialog(water(), orca_profile(OrcaVersionFamily.V5_0))

        self.assertIsNone(dialog._method.currentData())
        self.assertIsNone(dialog._basis.currentData())
        self.assertEqual(dialog._charge.value(), 0)
        self.assertEqual(dialog._multiplicity.value(), 1)
        self.assertFalse(dialog._use_max_core.isChecked())
        self.assertFalse(dialog._max_core.isEnabled())
        methods = tuple(dialog._method.itemData(index) for index in range(dialog._method.count()))
        self.assertNotIn(OrcaMethod.WB97M_D4REV, methods)
        dialog.close()

    def test_composite_method_locks_basis_and_dispersion(self):
        dialog = OrcaOptimizationSettingsDialog(water(), orca_profile())
        dialog._basis.setCurrentIndex(dialog._basis.findData(OrcaBasis.DEF2_TZVP))
        dialog._dispersion.setCurrentIndex(dialog._dispersion.findData(OrcaDispersion.D4))
        dialog._method.setCurrentIndex(dialog._method.findData(OrcaMethod.B97_3C))
        self.application.processEvents()

        self.assertFalse(dialog._basis.isEnabled())
        self.assertIsNone(dialog._basis.currentData())
        self.assertFalse(dialog._dispersion.isEnabled())
        self.assertEqual(dialog._dispersion.currentData(), OrcaDispersion.NONE)
        dialog.close()

    def test_resubmission_prefills_all_persisted_optimization_settings(self):
        initial = OrcaOptimizationSettings(
            method=OrcaMethod.PBE0,
            basis=OrcaBasis.DEF2_TZVP,
            dispersion=OrcaDispersion.D4,
            charge=-1,
            multiplicity=2,
            optimization_convergence=OrcaOptimizationConvergence.TIGHTOPT,
            coordinate_system=OrcaCoordinateSystem.CARTESIAN,
            scf_convergence=OrcaScfConvergence.TIGHTSCF,
            process_count=6,
            max_core_mb=3072,
            version_family=OrcaVersionFamily.V6_1,
            scheduler_nodes=2,
            runtime_minutes=180,
            scheduler_memory_gb=24,
        )

        dialog = OrcaOptimizationSettingsDialog(
            water(),
            orca_profile(),
            initial_settings=initial,
        )

        self.assertEqual(dialog._build_settings(), initial)
        self.assertTrue(dialog._use_max_core.isChecked())
        dialog.close()

    def test_optimization_runtime_is_entered_in_hours_and_stored_in_minutes(self):
        dialog = OrcaOptimizationSettingsDialog(water(), orca_profile())
        preset_minutes = orca_profile().execution_preset.runtime_minutes

        self.assertIsInstance(dialog._runtime, QDoubleSpinBox)
        self.assertEqual(dialog._runtime.suffix(), " h")
        self.assertAlmostEqual(dialog._runtime.value(), preset_minutes / 60.0)
        labels = " | ".join(item.text() for item in dialog.findChildren(QLabel))
        self.assertNotIn("minutes", labels)

        dialog._runtime.setValue(2.5)

        self.assertEqual(dialog._build_settings().runtime_minutes, 150)
        dialog.close()

    def test_preloaded_odd_minute_runtime_survives_the_hours_field_unchanged(self):
        initial = replace(source_settings(), runtime_minutes=101)
        dialog = OrcaOptimizationSettingsDialog(
            water(), orca_profile(), initial_settings=initial
        )

        self.assertEqual(dialog._build_settings().runtime_minutes, 101)
        dialog.close()

    def test_frequency_runtime_is_entered_in_hours_and_stored_in_minutes(self):
        dialog = OrcaFrequencySettingsDialog(source_settings(), water(), orca_profile())

        self.assertIsInstance(dialog._runtime, QDoubleSpinBox)
        self.assertEqual(dialog._runtime.suffix(), " h")
        dialog._mode.setCurrentIndex(
            dialog._mode.findData(OrcaFrequencyMode.NUMFREQ)
        )
        dialog._runtime.setValue(12.0)

        dialog._validate_and_accept()

        self.assertEqual(dialog.selected_settings().runtime_minutes, 720)
        dialog.close()

    def test_parity_error_is_reported_without_automatic_correction(self):
        dialog = OrcaOptimizationSettingsDialog(water(), orca_profile())
        dialog._method.setCurrentIndex(dialog._method.findData(OrcaMethod.PBE0))
        dialog._basis.setCurrentIndex(dialog._basis.findData(OrcaBasis.DEF2_TZVP))
        dialog._multiplicity.setValue(2)

        with patch.object(QMessageBox, "critical") as critical:
            dialog._validate_and_accept()

        critical.assert_called_once()
        self.assertIn("incompatible", critical.call_args.args[2])
        self.assertEqual(dialog._multiplicity.value(), 2)
        dialog.close()

    def test_frequency_inherits_science_requires_explicit_mode_and_shows_cost(self):
        source = source_settings()
        dialog = OrcaFrequencySettingsDialog(source, water(), orca_profile())

        self.assertIsNone(dialog._mode.currentData())
        labels = "\n".join(item.text() for item in dialog.findChildren(QLabel))
        self.assertIn("PBE0", labels)
        self.assertIn("NUMFREQ", labels)
        with patch.object(QMessageBox, "critical") as critical:
            dialog._validate_and_accept()
        critical.assert_called_once()

        dialog._mode.setCurrentIndex(dialog._mode.findData(OrcaFrequencyMode.NUMFREQ))
        dialog._validate_and_accept()
        selected = dialog.selected_settings()
        self.assertEqual(selected.source_optimization, source)
        self.assertIs(selected.mode, OrcaFrequencyMode.NUMFREQ)
        self.assertIsNone(selected.max_core_mb)

    def test_frequency_disables_freq_when_catalog_evidence_is_absent(self):
        source = source_settings()
        unsupported = replace(
            method_capability(source.method),
            analytical_frequency_supported=False,
        )
        with patch("moltage.gui.orca_dialogs.method_capability", return_value=unsupported):
            dialog = OrcaFrequencySettingsDialog(source, water(), orca_profile())

        index = dialog._mode.findData(OrcaFrequencyMode.FREQ)
        self.assertFalse(dialog._mode.model().item(index).isEnabled())
        dialog.close()

    def test_confirmation_previews_exact_input_and_script_and_clears_password(self):
        server = replace(orca_profile(), save_password=False)
        dialog = OrcaSubmissionConfirmationDialog(
            server,
            "SyntheticOrca.20300102",
            "ORCA optimization",
            "! PBE0 DEF2-TZVP OPT\n",
            "#!/bin/bash\nexec /apps/example/orca-6.1/orca orca_opt.inp\n",
            temporary_password_required=True,
        )

        self.assertIn("REAL", dialog.windowTitle())
        preview = dialog.findChild(QPlainTextEdit, "orcaSubmissionPreview")
        self.assertIsNotNone(preview)
        self.assertIn("ORCA input", preview.toPlainText())
        self.assertIn("! PBE0 DEF2-TZVP OPT", preview.toPlainText())
        self.assertIn("exec /apps/example/orca-6.1/orca", preview.toPlainText())
        dialog._password.setText("temporary-secret")
        self.assertEqual(dialog.take_temporary_password(), "temporary-secret")
        self.assertEqual(dialog._password.text(), "")
        dialog.close()

    def test_wbl_prefills_two_linkers_but_requires_explicit_gamma(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
        )

        self.assertEqual(dialog._left.atom.currentData(), 1)
        self.assertEqual(dialog._right.atom.currentData(), 3)
        self.assertEqual(dialog._left.linker.currentData(), WblLinkerKind.SH)
        self.assertEqual(dialog._right.linker.currentData(), WblLinkerKind.SH)
        self.assertIn("SH (automatic)", dialog._left.linker_summary.text())
        self.assertIn("S valence 3p", dialog._left.subspace.itemText(0))
        self.assertEqual(dialog._left.gamma0.value(), 0.0)
        self.assertEqual(dialog._right.gamma0.value(), 0.0)
        self.assertTrue(dialog._same_gamma.isChecked())
        self.assertFalse(dialog._right.gamma0.isEnabled())
        self.assertEqual(dialog._fermi.value(), -5.1)
        self.assertEqual(dialog._energy_min.value(), -5.0)
        self.assertEqual(dialog._energy_max.value(), 5.0)
        self.assertEqual(dialog._energy_step.value(), 0.01)
        self.assertFalse(dialog._left.advanced.isVisible())
        self.assertEqual(
            dialog._left.status.currentData(),
            WblParameterStatus.HYPOTHESIS,
        )
        validation = dialog.findChild(QLabel, "orcaWblValidationMessage")
        self.assertIsNotNone(validation)
        self.assertIn("positive value for left Γ₀", validation.text())
        with patch.object(QMessageBox, "critical") as critical:
            dialog._validate_and_accept()
        critical.assert_called_once()
        self.assertIn("WBL cannot start", validation.text())
        self.assertIn("positive left Γ₀", validation.text())
        dialog.close()

    def test_wbl_collects_one_based_contacts_and_manual_aos(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
        )
        for controls, atom_index in ((dialog._left, 1), (dialog._right, 3)):
            controls.atom.setCurrentIndex(controls.atom.findData(atom_index))
            controls.linker.setCurrentIndex(
                controls.linker.findData(WblLinkerKind.SH)
            )
        dialog._left.gamma0.setValue(0.25)
        dialog._right.status.setCurrentIndex(
            dialog._right.status.findData(WblParameterStatus.CALIBRATED)
        )
        dialog._left.subspace.setCurrentIndex(
            dialog._left.subspace.findData(WblContactSubspaceMode.MANUAL_AO)
        )
        dialog._left.manual_aos.setText("4, 7")
        dialog._fermi.setValue(-5.1)
        dialog._energy_min.setValue(-3.0)
        dialog._energy_max.setValue(3.0)
        dialog._energy_step.setValue(0.1)

        dialog._validate_and_accept()
        selected = dialog.selected_settings()

        self.assertEqual(selected.left.atom_index, 1)
        self.assertEqual(selected.right.atom_index, 3)
        self.assertEqual(selected.left.manual_ao_indices, (3, 6))
        self.assertIs(
            selected.right.parameter_status,
            WblParameterStatus.CALIBRATED,
        )
        self.assertEqual(selected.left.gamma0_ev, 0.25)
        self.assertEqual(selected.right.gamma0_ev, 0.25)

    def test_wbl_right_gamma_mirrors_left_until_unchecked(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
        )

        dialog._left.gamma0.setValue(0.37)
        self.assertEqual(dialog._right.gamma0.value(), 0.37)
        dialog._same_gamma.setChecked(False)
        self.assertTrue(dialog._right.gamma0.isEnabled())
        dialog._right.gamma0.setValue(0.52)
        dialog._left.gamma0.setValue(0.41)
        self.assertEqual(dialog._right.gamma0.value(), 0.52)
        dialog.close()

    def test_wbl_manual_viewer_selection_restores_contact_and_linker(self):
        structure = synthetic_dithiol()
        requested = []
        selections = iter((3, None))

        def select_contact(side):
            requested.append(side)
            self.assertFalse(dialog.isVisible())
            self.assertIsNone(self.application.modalWindow())
            selected = next(selections)
            QTimer.singleShot(0, dialog.accept if selected is not None else dialog.reject)
            return selected

        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
            contact_atom_selector=select_contact,
        )

        manual_index = dialog._left.atom.findData("MANUAL_SELECT_IN_VIEWER")
        for expected in (QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected):
            with self.subTest(result=expected):
                QTimer.singleShot(0, lambda: dialog._left.atom.setCurrentIndex(manual_index))
                self.assertEqual(dialog.exec(), expected)
                self.assertEqual(dialog._left.atom.currentData(), 3)
                self.assertEqual(dialog._left.linker.currentData(), WblLinkerKind.SH)
                self.assertFalse(dialog.isVisible())
                self.assertIsNone(self.application.modalWindow())
                self.assertIsNone(self.application.activeModalWidget())
        self.assertEqual(requested, ["left", "left"])
        dialog.close()

    def test_wbl_gamma_editor_rejects_letters_and_advanced_is_explicit(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
        )

        dialog.show()
        self.application.processEvents()
        self.assertIn("Enter value", dialog._left.gamma0.text())
        QTest.mouseClick(
            dialog._left.gamma0.lineEdit(),
            Qt.MouseButton.LeftButton,
        )
        self.application.processEvents()
        self.assertNotIn("Enter value", dialog._left.gamma0.text())
        self.assertEqual(dialog._left.gamma0.text().strip(), "eV")
        QTest.keyClicks(dialog._left.gamma0.lineEdit(), "0.42")
        self.assertAlmostEqual(dialog._left.gamma0.value(), 0.42)
        state, _text, _position = dialog._left.gamma0.lineEdit().validator().validate(
            "letters",
            0,
        )
        self.assertEqual(state.name, "Invalid")
        dialog._advanced_button.setChecked(True)
        self.assertFalse(dialog._left.advanced.isHidden())
        self.assertIn("Model hypothesis", dialog._left.status.currentText())
        self.assertIn("contact direction", dialog._left.subspace.itemText(0))
        dialog.close()

    def test_wbl_advanced_contacts_use_columns_and_restore_collapsed_size(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
        )
        dialog.show()
        self.application.processEvents()
        compact_size = dialog.size()
        left_group = dialog._left.advanced.parentWidget()
        right_group = dialog._right.advanced.parentWidget()
        self.assertEqual(left_group.geometry().top(), right_group.geometry().top())
        self.assertLess(left_group.geometry().left(), right_group.geometry().left())

        dialog._advanced_button.setChecked(True)
        self.application.processEvents()
        self.assertFalse(dialog._left.advanced.isHidden())
        self.assertGreater(dialog.height(), compact_size.height())

        dialog._advanced_button.setChecked(False)
        self.application.processEvents()
        self.assertEqual(dialog.size(), compact_size)
        dialog.close()

    def test_wbl_detection_reports_the_current_bond_threshold_factor(self):
        structure = synthetic_dithiol()

        recognized = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=1.10,
        )
        summary = recognized.findChild(QLabel, "orcaWblDetectionSummary")
        self.assertIn("factor 1.10", summary.text())
        self.assertIn("2 supported contact atoms", summary.text())
        self.assertEqual(recognized._left.atom.currentData(), 1)
        recognized.close()

        unrecognized = OrcaWblSettingsDialog(
            structure,
            unbonded_contact_connectivity(structure),
            connectivity_multiplier=0.90,
        )
        summary = unrecognized.findChild(QLabel, "orcaWblDetectionSummary")
        self.assertIn("factor 0.90", summary.text())
        self.assertIn("recognized no supported contact", summary.text())
        self.assertIsNone(unrecognized._left.atom.currentData())
        unrecognized.close()

    def test_wbl_undetected_linker_runs_on_an_explicit_orbital_projection(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            unbonded_contact_connectivity(structure),
            connectivity_multiplier=0.90,
        )
        validation = dialog.findChild(QLabel, "orcaWblValidationMessage")

        for controls, atom_index in ((dialog._left, 1), (dialog._right, 3)):
            controls.atom.setCurrentIndex(controls.atom.findData(atom_index))
        self.assertTrue(dialog._advanced_button.isChecked())
        self.assertIn("select the linker", validation.text())

        for controls in (dialog._left, dialog._right):
            controls.linker.setCurrentIndex(
                controls.linker.findData(WblLinkerKind.SH)
            )
        self.assertIn("one S-H bond", validation.text())
        self.assertIn("Settings > Bond Detection", validation.text())

        dialog._left.gamma0.setValue(0.25)
        with patch.object(QMessageBox, "critical") as critical:
            dialog._validate_and_accept()
        critical.assert_called_once()
        self.assertIn("cannot be resolved", validation.text())

        for controls, direction in (
            (dialog._left, "-1, 0, 0"),
            (dialog._right, "1, 0, 0"),
        ):
            controls.subspace.setCurrentIndex(
                controls.subspace.findData(
                    WblContactSubspaceMode.S_3P_DIRECTIONAL
                )
            )
            controls.manual_direction.setText(direction)
        dialog._validate_and_accept()
        selected = dialog.selected_settings()

        self.assertIs(
            selected.left.subspace_mode,
            WblContactSubspaceMode.S_3P_DIRECTIONAL,
        )
        self.assertEqual(selected.left.manual_direction, (-1.0, 0.0, 0.0))
        self.assertEqual(selected.right.manual_direction, (1.0, 0.0, 0.0))
        self.assertEqual(selected.connectivity_multiplier, 0.90)
        dialog.close()

    def test_wbl_projection_direction_is_built_from_a_reference_atom(self):
        structure = synthetic_dithiol()
        dialog = OrcaWblSettingsDialog(
            structure,
            unbonded_contact_connectivity(structure),
            connectivity_multiplier=0.90,
        )
        dialog._left.atom.setCurrentIndex(dialog._left.atom.findData(1))

        with patch.object(
            QInputDialog,
            "getItem",
            return_value=("1 - H (toward)", True),
        ) as chooser:
            dialog._left.direction_from_atom.click()

        offered = chooser.call_args.args[3]
        self.assertIn("1 - H (toward)", offered)
        self.assertIn("1 - H (away from)", offered)
        self.assertNotIn("2 - S (toward)", offered)
        self.assertEqual(
            dialog._left.manual_direction.text(),
            "-1.000000, 0.000000, 0.000000",
        )
        dialog.close()

    def test_wbl_prefills_ncs_sulfur_contacts_with_legacy_all_p_projection(self):
        structure = synthetic_diisothiocyanate()
        dialog = OrcaWblSettingsDialog(
            structure,
            chain_connectivity(structure),
            connectivity_multiplier=1.10,
        )

        self.assertEqual(dialog._left.atom.currentData(), 0)
        self.assertEqual(dialog._right.atom.currentData(), 7)
        self.assertEqual(dialog._left.linker.currentData(), WblLinkerKind.NCS)
        self.assertIn("NCS (automatic)", dialog._left.linker_summary.text())
        self.assertIn("S all p orbitals", dialog._left.subspace.itemText(0))
        self.assertGreaterEqual(
            dialog._left.linker.findData(WblLinkerKind.NCS),
            0,
        )

        dialog._left.gamma0.setValue(0.30)
        dialog._validate_and_accept()
        selected = dialog.selected_settings()

        self.assertIs(selected.left.linker, WblLinkerKind.NCS)
        self.assertEqual(selected.left.atom_index, 0)
        self.assertEqual(selected.right.atom_index, 7)
        self.assertIs(
            selected.left.subspace_mode,
            WblContactSubspaceMode.AUTO,
        )
        dialog.close()

    def test_wbl_resubmission_starts_from_the_previous_step_2_settings(self):
        structure = synthetic_dithiol()
        previous = OrcaWblSettings(
            OrcaWblContactSettings(
                1, WblLinkerKind.SH, 0.25, WblParameterStatus.HYPOTHESIS
            ),
            OrcaWblContactSettings(
                3,
                WblLinkerKind.SH,
                0.4,
                WblParameterStatus.CALIBRATED,
                WblContactSubspaceMode.S_3P_DIRECTIONAL,
                manual_direction=(1.0, 0.0, 0.0),
            ),
            -4.8,
            -3.0,
            3.0,
            0.1,
            1.30,
        )
        dialog = OrcaWblSettingsDialog(
            structure,
            synthetic_connectivity(structure),
            connectivity_multiplier=1.10,
            initial_settings=previous,
            replacing_result=True,
        )

        notice = dialog.findChild(QLabel, "orcaWblReplacementNotice")
        self.assertIn("replaces this project's previous WBL", notice.text())
        self.assertIn("previous result is kept", notice.text())
        self.assertTrue(any(button.text() == "Recalculate Step 2" for button in dialog.findChildren(QPushButton)))
        self.assertFalse(dialog._same_gamma.isChecked())
        self.assertTrue(dialog._advanced_button.isChecked())
        self.assertFalse(dialog._right.advanced.isHidden())
        summary = dialog.findChild(QLabel, "orcaWblDetectionSummary")
        self.assertIn("previous Step 2 run used factor 1.30", summary.text())

        dialog._validate_and_accept()

        # Everything is carried over except the bond threshold factor, which
        # always follows the bonds the session currently shows.
        self.assertEqual(
            dialog.selected_settings(),
            replace(previous, connectivity_multiplier=1.10),
        )
        dialog.close()


if __name__ == "__main__":
    unittest.main()
