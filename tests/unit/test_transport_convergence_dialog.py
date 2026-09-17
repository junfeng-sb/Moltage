import gc
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

from moltage.aims.optimization_settings import (
    SpeciesAccuracy,
    XCFunctional,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.aims.orbital_cube import OrbitalCubeOutputSettings
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.app.transport_convergence import TransportConvergenceContext
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.gui.transport_convergence_dialog import (
    Step3SubmissionConfirmationDialog,
    TransportConvergenceSettingsDialog,
)
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from phase2b1_test_support import profile
from synthetic_structure_test_support import synthetic_step2_state
from test_project_recovery import _project


class TransportConvergenceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        source, connectivity, _anchors, sites = synthetic_step2_state()
        proposal = propose_electrode_placement(
            source,
            connectivity,
            sites,
            sites,
        )
        applied = apply_electrode_placement(source, connectivity, proposal)
        project = _project(
            ProjectStepState.SUCCEEDED,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        snapshot = ProjectRecoverySnapshot(
            project,
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=source,
            connectivity=connectivity,
        )
        cls.context = TransportConvergenceContext(
            snapshot,
            source,
            applied.structure,
            applied,
        )

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()
        gc.collect()

    def test_dialog_defaults_match_reference_and_has_no_relaxation_control(self):
        dialog = TransportConvergenceSettingsDialog(
            self.context.working_structure
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.windowTitle(), "Step 3 — Transport Convergence")
        self.assertIs(dialog._xc.currentData(), XCFunctional.PBE)
        self.assertIs(
            dialog._species_accuracy.currentData(),
            SpeciesAccuracy.TIGHT,
        )
        self.assertIsNone(dialog._spin_mode.currentData())
        self.assertEqual(dialog._charge.text(), "0")
        self.assertEqual(dialog._occupation_width.text(), "0.01")
        self.assertEqual(dialog._n_max_pulay.text(), "10")
        self.assertEqual(dialog._charge_mix_param.text(), "0.2")
        self.assertEqual(dialog._sc_accuracy_rho.text(), "1E-5")
        self.assertEqual(dialog._sc_accuracy_eev.text(), "1E-3")
        self.assertEqual(dialog._sc_accuracy_etot.text(), "1E-6")
        self.assertEqual(dialog._sc_iter_limit.text(), "500")
        self.assertEqual(dialog._orbital_eigenstates.text(), "")
        self.assertEqual(dialog._orbital_grid_spacing.text(), "")
        fixed = dialog.findChild(QLabel, "step3FixedDirectives").text()
        self.assertIn("output aitranss", fixed)
        self.assertIn("KS_method serial", fixed)
        self.assertIn("restart aims.restart", fixed)
        self.assertIn("No geometry relaxation", fixed)
        self.assertFalse(
            any(
                "relax" in field.objectName().lower()
                for field in dialog.findChildren(QLineEdit)
            )
        )

        with patch.object(QMessageBox, "critical") as critical:
            dialog._validate_and_accept()
        critical.assert_not_called()
        self.assertEqual(dialog.selected_settings(), TransportConvergenceSettings())

    def test_absolute_orbitals_and_optional_spacing_are_editable(self):
        dialog = TransportConvergenceSettingsDialog(
            self.context.working_structure
        )
        self.addCleanup(dialog.deleteLater)
        dialog._orbital_eigenstates.setText("154, 152")
        dialog._orbital_grid_spacing.setText("0.12")

        dialog._validate_and_accept()

        self.assertEqual(
            dialog.selected_settings().orbital_cubes,
            OrbitalCubeOutputSettings(
                eigenstate_indices=(152, 154),
                grid_spacing_angstrom=0.12,
            ),
        )

    def test_invalid_scf_text_is_not_clamped_or_accepted(self):
        dialog = TransportConvergenceSettingsDialog(
            self.context.working_structure
        )
        self.addCleanup(dialog.deleteLater)
        dialog._occupation_width.setText("0")

        with patch.object(QMessageBox, "critical") as critical:
            dialog._validate_and_accept()

        critical.assert_called_once()
        self.assertEqual(critical.call_args.args[1], "Invalid Step-3 settings")
        self.assertEqual(dialog._occupation_width.text(), "0")
        with self.assertRaises(RuntimeError):
            dialog.selected_settings()

    def test_confirmation_names_same_project_path_and_fixed_directives(self):
        selected_profile = profile(
            profile_id=self.context.project.server_profile_id,
            save_password=False,
            email_enabled=True,
            email_recipient="user@example.com",
        )
        dialog = Step3SubmissionConfirmationDialog(
            self.context,
            selected_profile,
            TransportConvergenceSettings(),
            temporary_password_required=True,
        )
        self.addCleanup(dialog.deleteLater)
        text = "\n".join(
            label.text() for label in dialog.findChildren(QLabel)
        )

        self.assertIn(self.context.project.remote_directory_name, text)
        self.assertIn("Step 2 — SUCCEEDED; electrodes applied", text)
        self.assertIn("Step 3 — Transport convergence", text)
        self.assertIn(
            self.context.project.remote_project_path
            + "/molecule_Au/transport",
            text,
        )
        self.assertIn("output aitranss", text)
        self.assertIn("KS_method serial", text)
        self.assertIn("restart aims.restart", text)
        self.assertEqual(
            dialog.findChild(QLabel, "step3EmailSummary").text(),
            "user@example.com",
        )
        self.assertIsNotNone(
            dialog.findChild(QLineEdit, "step3TemporaryPassword")
        )


if __name__ == "__main__":
    unittest.main()
