from dataclasses import replace
from datetime import date
import unittest

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.connection_service import AuthenticationError
from moltage.app.project_planning import (
    StartStepAdvice,
    StartStepRecommendation,
)
from moltage.app.project_submission import (
    NewProjectSubmissionRequest,
    SbatchRejectedError,
)
from moltage.domain.calculation_project import CalculationWorkflowKind, ProjectStepKind
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
)
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.project_submission import (
    NewCalculationProjectDialog,
    NewProjectSelection,
    SubmissionConfirmationDialog,
    submission_error_presentation,
)
from moltage.remote.executor import RemoteCommandOutcomeUnknown
from moltage.remote.slurm_discovery import SlurmDiscoveryError
from moltage.orca.catalog import OrcaVersionEvidence, OrcaVersionFamily
from phase2b1_test_support import profile, synthetic_slurm_preset


STEP1_RECOMMENDATION = StartStepRecommendation(
    StartStepAdvice.STEP1,
    ProjectStepKind.MOLECULE_OPT,
    True,
    "Recognized linkers have no directly attached contact Au.",
)
STEP2_RECOMMENDATION = StartStepRecommendation(
    StartStepAdvice.STEP2,
    ProjectStepKind.MOLECULE_AU_OPT,
    True,
    "Two linker-bound contact Au atoms were detected; Step 1 will be skipped.",
)
AMBIGUOUS_RECOMMENDATION = StartStepRecommendation(
    StartStepAdvice.AMBIGUOUS,
    None,
    True,
    "The linker-bound Au pattern is ambiguous; choose explicitly.",
)


def _input_plan() -> AimsOptimizationInputPlan:
    return AimsOptimizationInputPlan(
        MolecularStructure((Atom(0, "H", 0.0, 0.0, 0.0),)),
        AimsOptimizationSettings(),
    )


class ProjectSubmissionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_project_dialog_defaults_to_last_server_and_loaded_filename_stem(self) -> None:
        first = profile(name="Alpha")
        last = profile(
            name="ExampleCluster",
            email_enabled=True,
            email_recipient="user@example.com",
        )
        dialog = NewCalculationProjectDialog(
            (first, last),
            last.profile_id,
            "ExampleMolecule",
            STEP1_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
        )

        self.assertEqual(dialog._server.currentData(), last)
        self.assertEqual(dialog._base_name.text(), "ExampleMolecule")
        self.assertIs(
            dialog._current_step(),
            ProjectStepKind.MOLECULE_OPT,
        )
        self.assertIn("no directly attached", dialog._detected.text())
        self.assertEqual(
            dialog._remote_preview.text(),
            "/srv/moltage-test/projects/ExampleMolecule.20300102",
        )
        self.assertIn("24 MPI tasks", dialog._cluster_summary.text())
        self.assertEqual(dialog._email_summary.text(), "user@example.com")
        self.assertTrue(dialog._continue_button.isEnabled())
        self.assertEqual(
            [
                field
                for field in dialog.findChildren(QLineEdit)
                if field.echoMode() is QLineEdit.EchoMode.Password
            ],
            [],
        )

    def test_direct_step2_displays_explicit_skipped_warning(self) -> None:
        server = profile()
        dialog = NewCalculationProjectDialog(
            (server,),
            server.profile_id,
            "MoleculeA",
            STEP2_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
        )

        self.assertIs(
            dialog._current_step(),
            ProjectStepKind.MOLECULE_AU_OPT,
        )
        self.assertEqual(
            dialog._step_warning.text(),
            "Step 1 was not performed by this project and will be marked "
            "SKIPPED — imported contact-Au geometry.",
        )

    def test_orca_engine_is_independent_and_requires_only_its_runtime(self) -> None:
        incomplete = profile()
        dialog = NewCalculationProjectDialog(
            (incomplete,),
            incomplete.profile_id,
            "ExampleMolecule",
            STEP1_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
        )
        dialog._engine.setCurrentIndex(
            dialog._engine.findData(CalculationWorkflowKind.ORCA)
        )
        self.application.processEvents()

        self.assertIs(dialog._current_step(), ProjectStepKind.ORCA_OPTIMIZATION)
        self.assertFalse(dialog._continue_button.isEnabled())
        self.assertIn("Configure and validate ORCA", dialog._cluster_status.text())

        configured = replace(
            incomplete,
            orca_runtime=OrcaRuntimeConfiguration(
                "/apps/example/orca/orca",
                RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
                OrcaVersionEvidence(
                    "Program Version 6.1.2",
                    "6.1.2",
                    OrcaVersionFamily.V6_1,
                    "synthetic validation",
                ),
            ),
        )
        configured_dialog = NewCalculationProjectDialog(
            (configured,),
            configured.profile_id,
            "ExampleMolecule",
            STEP1_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
        )
        configured_dialog._engine.setCurrentIndex(
            configured_dialog._engine.findData(CalculationWorkflowKind.ORCA)
        )
        self.application.processEvents()

        self.assertTrue(configured_dialog._continue_button.isEnabled())
        self.assertEqual(configured_dialog._cluster_status.text(), "")

    def test_direct_step3_is_fixed_and_requires_preoptimized_confirmation(self) -> None:
        server = profile(save_password=False)
        planning = NewCalculationProjectDialog(
            (server,),
            server.profile_id,
            "Imported",
            STEP2_RECOMMENDATION,
            preview_date=date(2030, 1, 6),
            fixed_starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        selection = NewProjectSelection(
            server,
            "Imported",
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            "Imported.20300830",
        )
        confirmation = SubmissionConfirmationDialog(
            selection,
            TransportConvergenceSettings(),
            temporary_password_required=True,
        )

        self.assertFalse(planning._starting_step.isEnabled())
        self.assertIs(
            planning._current_step(),
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        self.assertIn("Steps 1 and 2 were not performed", planning._step_warning.text())
        warning = confirmation.findChild(QLabel, "confirmationStepWarning").text()
        self.assertIn("appropriately optimized", warning)
        self.assertIn("marked SKIPPED", warning)
        self.assertIn("electrode clusters", warning)

    def test_missing_source_stem_requires_an_explicit_valid_project_name(self) -> None:
        server = profile()
        dialog = NewCalculationProjectDialog(
            (server,),
            server.profile_id,
            "",
            STEP1_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
        )

        self.assertFalse(dialog._continue_button.isEnabled())
        self.assertEqual(
            dialog._remote_preview.text(),
            "Enter a valid project name.",
        )
        dialog._base_name.setText("ExplicitName")
        self.application.processEvents()
        self.assertTrue(dialog._continue_button.isEnabled())

    def test_ambiguous_recommendation_requires_an_explicit_step(self) -> None:
        server = profile()
        dialog = NewCalculationProjectDialog(
            (server,),
            server.profile_id,
            "MoleculeA",
            AMBIGUOUS_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
        )

        self.assertIsNone(dialog._current_step())
        self.assertFalse(dialog._continue_button.isEnabled())
        dialog._starting_step.setCurrentIndex(
            dialog._starting_step.findData(ProjectStepKind.MOLECULE_OPT.value)
        )
        self.application.processEvents()
        self.assertTrue(dialog._continue_button.isEnabled())

    def test_missing_preset_blocks_and_cluster_callback_can_supply_it(self) -> None:
        incomplete = replace(profile(), execution_preset=None)
        callback_calls = []

        def configure(selected):
            callback_calls.append(selected)
            return replace(selected, execution_preset=synthetic_slurm_preset())

        dialog = NewCalculationProjectDialog(
            (incomplete,),
            incomplete.profile_id,
            "MoleculeA",
            STEP1_RECOMMENDATION,
            preview_date=date(2030, 1, 2),
            cluster_settings_callback=configure,
        )

        self.assertFalse(dialog._continue_button.isEnabled())
        self.assertIn(
            "Configure Cluster Execution Settings for ExampleCluster",
            dialog._cluster_status.text(),
        )
        dialog._cluster_settings.click()
        self.application.processEvents()
        self.assertEqual(callback_calls, [incomplete])
        self.assertTrue(dialog._continue_button.isEnabled())

    def test_confirmation_has_masked_memory_only_password_and_full_summary(self) -> None:
        server = profile(
            save_password=False,
            email_enabled=True,
            email_recipient="user@example.com",
        )
        selection = NewProjectSelection(
            server,
            "MoleculeA",
            ProjectStepKind.MOLECULE_AU_OPT,
            "MoleculeA.20300102",
        )
        dialog = SubmissionConfirmationDialog(
            selection,
            AimsOptimizationSettings(),
            temporary_password_required=True,
        )

        self.assertIsNotNone(dialog._password)
        self.assertIs(
            dialog._password.echoMode(),
            QLineEdit.EchoMode.Password,
        )
        self.assertIn("REAL", dialog.windowTitle())
        self.assertEqual(
            dialog.findChild(
                type(dialog._password), "submissionTemporaryPassword"
            ),
            dialog._password,
        )
        self.assertEqual(
            dialog.findChild(QLabel, "confirmationStepWarning").text(),
            "The current geometry already contains the recognized linker contact "
            "Au atoms. Step 1 was not performed and will be marked SKIPPED.",
        )
        summary_text = "\n".join(
            label.text() for label in dialog.findChildren(QLabel)
        )
        self.assertIn("36.0 hours maximum", summary_text)
        self.assertIn("128 GB/node", summary_text)
        self.assertIn("PBE / tight", summary_text)
        self.assertEqual(
            dialog.findChild(QLabel, "confirmationEmailSummary").text(),
            "user@example.com",
        )
        dialog._password.setText("temporary-secret")
        self.assertEqual(dialog.take_temporary_password(), "temporary-secret")
        self.assertEqual(dialog._password.text(), "")

    def test_scheduler_rejection_is_never_presented_as_authentication_failure(self) -> None:
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _input_plan(),
            "temporary-secret",
        )
        error = SbatchRejectedError(
            "Slurm rejected the submission: bash: line 1: sbatch: command not found",
            remote_project_path="/srv/moltage-test/projects/MoleculeA.20300102",
            step_kind=ProjectStepKind.MOLECULE_OPT,
        )

        presentation = submission_error_presentation(error, request)

        self.assertEqual(presentation.title, "Slurm submission failed")
        self.assertIn("verified input files were created", presentation.message)
        self.assertIn("sbatch: command not found", presentation.message)
        self.assertNotIn("Password authentication failed", presentation.message)
        self.assertNotIn("Authentication or connection failed", presentation.message)

    def test_actual_authentication_failure_keeps_authentication_presentation(self) -> None:
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _input_plan(),
            "wrong-secret",
        )

        presentation = submission_error_presentation(
            AuthenticationError("Password authentication failed for ExampleCluster"),
            request,
        )

        self.assertEqual(presentation.title, "Authentication failed")
        self.assertIn("authentication failed", presentation.message.casefold())
        self.assertNotIn("Slurm submission failed", presentation.message)

    def test_discovery_failure_explicitly_says_no_project_was_created(self) -> None:
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _input_plan(),
            "temporary-secret",
        )

        presentation = submission_error_presentation(
            SlurmDiscoveryError("not found"),
            request,
        )

        self.assertEqual(presentation.title, "Slurm not detected")
        self.assertIn("No remote calculation project was created", presentation.message)
        self.assertIn("manually", presentation.message)

    def test_discovery_transport_failure_uses_connection_presentation(self) -> None:
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _input_plan(),
            "temporary-secret",
        )

        presentation = submission_error_presentation(
            RemoteCommandOutcomeUnknown(
                "SSH transport failed during Slurm discovery"
            ),
            request,
        )

        self.assertEqual(presentation.title, "Connection failed")
        self.assertNotEqual(presentation.title, "Slurm not detected")
        self.assertNotEqual(presentation.title, "Slurm configuration invalid")


if __name__ == "__main__":
    unittest.main()
