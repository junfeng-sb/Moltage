"""Qt behavior for the Import Existing ORCA Optimization dialog."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QDialog

from moltage.app.orca_import import OrcaImportError, OrcaImportValidation
from moltage.app.project_planning import project_directory_candidates
from moltage.gui.orca_import_dialog import (
    DIALOG_EXPLANATION,
    DIALOG_TITLE,
    ImportExistingOrcaOptimizationDialog,
)
from moltage.orca.catalog import OrcaBasis, OrcaDispersion, OrcaMethod
from moltage.orca.import_evidence import OrcaImportCandidate
from moltage.orca.input_writer import OrcaInputScientificIdentity
from moltage.orca.project_evidence import OrcaImportWavefunctionReadiness
from phase2b1_test_support import MemorySecretStore, profile
from qt_test_support import wait_until
from synthetic_test_data import SYNTHETIC_REMOTE_ROOT


PROFILE_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
SOURCE = "/home/scientist/legacy/benzenedithiol"


class FakeStructure(tuple):
    """Only the length is presented, so a tuple of atoms is sufficient."""


def identity(method=OrcaMethod.PBE0, basis=OrcaBasis.DEF2_TZVP):
    return OrcaInputScientificIdentity(0, 1, method, basis, OrcaDispersion.NONE)


def evidence(stem="benzenedithiol", **overrides):
    candidate = OrcaImportCandidate(stem)
    payload = {
        "candidate": candidate,
        "submitted_structure": FakeStructure(("S", "C", "H")),
        "optimized_structure": FakeStructure(("S", "C", "H")),
        "identity": identity(),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def validation(
    *,
    stems=("benzenedithiol",),
    selected_stem="benzenedithiol",
    blocking_reason=None,
    selection_required=False,
    readiness=OrcaImportWavefunctionReadiness.READY,
    readiness_diagnostic=None,
    molden=(),
    identity_override=None,
    coordinate_path=None,
):
    item = evidence(selected_stem) if selected_stem is not None else None
    if item is not None and identity_override is not None:
        item.identity = identity_override
    return OrcaImportValidation(
        SOURCE,
        stems,
        item.candidate if item is not None else None,
        None if blocking_reason is not None else item,
        (("orca_opt.gbw", "a" * 64),),
        readiness,
        "/apps/orca/orca_2json" if readiness is OrcaImportWavefunctionReadiness.READY else None,
        readiness_diagnostic,
        molden,
        blocking_reason,
        selection_required,
        coordinate_path=coordinate_path,
        coordinate_sha256=None if coordinate_path is None else "b" * 64,
    )


class RecordingImportService:
    def __init__(self, validation_result=None, import_result=None) -> None:
        self.validation_result = validation_result
        self.import_result = import_result
        self.validation_requests = []
        self.import_requests = []
        self.validation_error: Exception | None = None
        self.import_error: Exception | None = None

    def validate(self, request):
        self.validation_requests.append(request)
        if self.validation_error is not None:
            raise self.validation_error
        return self.validation_result

    def import_optimization(self, request):
        self.import_requests.append(request)
        if self.import_error is not None:
            raise self.import_error
        return self.import_result


class OrcaImportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.secret_store = MemorySecretStore()
        self.profile = replace(profile(profile_id=PROFILE_ID), save_password=True)
        self.secret_store.set_password(PROFILE_ID, "synthetic-password")
        self.pool = QThreadPool()
        self.service = RecordingImportService()

    def build(self, profiles=None):
        dialog = ImportExistingOrcaOptimizationDialog(
            (self.profile,) if profiles is None else profiles,
            PROFILE_ID,
            self.service,
            connection_service=None,
            secret_store=self.secret_store,
            parent=None,
            thread_pool=self.pool,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    # Presentation ----------------------------------------------------

    def test_title_and_explanation_state_that_nothing_is_rerun(self) -> None:
        dialog = self.build()

        self.assertEqual(dialog.windowTitle(), DIALOG_TITLE)
        self.assertIn("will not rerun ORCA", DIALOG_EXPLANATION)
        self.assertIn("modify the source directory", DIALOG_EXPLANATION)
        self.assertEqual(dialog._path.placeholderText(), "/remote/path/to/completed/orca_calculation")

    def test_import_is_disabled_before_validation(self) -> None:
        dialog = self.build()

        self.assertFalse(dialog._import.isEnabled())

    def test_without_a_server_profile_the_dialog_explains_the_next_step(self) -> None:
        dialog = self.build(profiles=())

        self.assertIn("Server", dialog._results.text())
        self.assertFalse(dialog._validate.isEnabled())
        self.assertFalse(dialog._import.isEnabled())
        self.assertTrue(dialog._server_settings.isEnabled())

    def test_server_settings_entry_emits_a_request(self) -> None:
        dialog = self.build(profiles=())
        seen = []
        dialog.server_settings_requested.connect(lambda: seen.append(True))

        dialog._server_settings.click()

        self.assertEqual(seen, [True])

    # Source selection ------------------------------------------------

    def test_manual_path_entry_derives_a_default_project_name(self) -> None:
        dialog = self.build()

        dialog.set_source_directory(SOURCE)

        self.assertEqual(dialog._project_name.text(), "benzenedithiol")

    def test_destination_preview_uses_the_profile_workspace(self) -> None:
        dialog = self.build()

        dialog.set_source_directory(SOURCE)

        expected = next(
            iter(project_directory_candidates("benzenedithiol", date.today()))
        )
        self.assertIn(f"{SYNTHETIC_REMOTE_ROOT}/{expected}", dialog._destination.text())
        self.assertIn("source directory is not modified", dialog._destination.text())

    def test_browse_result_is_written_back_into_the_path_field(self) -> None:
        dialog = self.build()

        with patch(
            "moltage.gui.orca_import_dialog.RemoteDirectoryDialog"
        ) as remote_dialog:
            instance = remote_dialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.selected_directory = SOURCE
            dialog._browse.click()

        self.assertEqual(dialog._path.text(), SOURCE)

    # Validation ------------------------------------------------------

    def _validate(self, dialog, result):
        self.service.validation_result = result
        dialog.set_source_directory(SOURCE)
        dialog._validate.click()
        wait_until(lambda: dialog.validation is not None or not dialog._busy)
        wait_until(lambda: not dialog._busy)

    def test_successful_validation_enables_import_and_lists_the_facts(self) -> None:
        dialog = self.build()

        self._validate(dialog, validation())

        text = dialog._results.text()
        self.assertIn("ORCA output: detected", text)
        self.assertIn("Normal termination: verified", text)
        self.assertIn("Geometry optimization: converged", text)
        self.assertIn("Wavefunction source: benzenedithiol.gbw", text)
        self.assertIn("Optimized geometry: available", text)
        self.assertIn("WBL readiness: ready", text)
        self.assertNotIn("Starting coordinates", text)
        self.assertTrue(dialog._import.isEnabled())

    def test_xyzfile_coordinates_are_disclosed_as_inlined(self) -> None:
        dialog = self.build()

        self._validate(dialog, validation(coordinate_path=f"{SOURCE}/start.xyz"))

        text = dialog._results.text()
        self.assertIn(f"Starting coordinates: {SOURCE}/start.xyz", text)
        self.assertIn("written inline into the managed orca_opt.inp", text)
        self.assertTrue(dialog._import.isEnabled())

    def test_failed_validation_keeps_import_disabled(self) -> None:
        dialog = self.build()

        self._validate(
            dialog,
            validation(
                blocking_reason=(
                    "benzenedithiol.out does not confirm ORCA normal termination."
                )
            ),
        )

        self.assertIn("normal termination", dialog._results.text())
        self.assertFalse(dialog._import.isEnabled())

    def test_several_candidates_require_an_explicit_choice(self) -> None:
        dialog = self.build()

        self._validate(
            dialog,
            validation(
                stems=("first", "second"),
                blocking_reason="This directory contains several completed ORCA calculations.",
                selection_required=True,
            ),
        )

        self.assertTrue(dialog._candidate_choice_active)
        self.assertEqual(dialog._candidates.count(), 2)
        self.assertFalse(dialog._import.isEnabled())

        # Choosing a candidate revalidates that exact result.
        self.service.validation_result = validation(
            stems=("first", "second"), selected_stem="second"
        )
        dialog._candidates.setCurrentIndex(1)
        wait_until(lambda: not dialog._busy)

        self.assertTrue(dialog._import.isEnabled())
        self.assertEqual(
            self.service.validation_requests[-1].selected_stem, "second"
        )

    def test_configuration_required_readiness_is_reported_but_still_importable(
        self,
    ) -> None:
        dialog = self.build()

        self._validate(
            dialog,
            validation(
                readiness=OrcaImportWavefunctionReadiness.CONFIGURATION_REQUIRED,
                readiness_diagnostic="Configure ORCA in this server's ORCA settings.",
            ),
        )

        self.assertIn("WBL readiness: configuration required", dialog._results.text())
        self.assertIn("ORCA settings", dialog._results.text())
        self.assertTrue(dialog._import.isEnabled())

    def test_unrecognized_method_is_reported_as_manual_ao_requirement(self) -> None:
        dialog = self.build()

        self._validate(
            dialog,
            validation(identity_override=identity(method=None, basis=None)),
        )

        self.assertIn("manually specified contact AO indices", dialog._results.text())
        self.assertTrue(dialog._import.isEnabled())

    def test_existing_molden_file_is_reported_as_unused(self) -> None:
        dialog = self.build()

        self._validate(dialog, validation(molden=("benzenedithiol.molden.input",)))

        self.assertIn("not used", dialog._results.text())
        self.assertIn(".gbw", dialog._results.text())

    def test_validation_failure_is_shown_without_internal_details(self) -> None:
        dialog = self.build()
        self.service.validation_error = OrcaImportError(
            "The remote directory does not exist: /absent"
        )
        dialog.set_source_directory("/absent")

        dialog._validate.click()
        wait_until(lambda: not dialog._busy)

        self.assertIn("does not exist", dialog._results.text())
        self.assertNotIn("Traceback", dialog._results.text())
        self.assertFalse(dialog._import.isEnabled())

    def test_changing_the_source_discards_a_previous_validation(self) -> None:
        dialog = self.build()
        self._validate(dialog, validation())
        self.assertTrue(dialog._import.isEnabled())

        dialog.set_source_directory("/home/scientist/other")

        self.assertIsNone(dialog.validation)
        self.assertFalse(dialog._import.isEnabled())

    # Import ----------------------------------------------------------

    def test_import_sends_the_validated_evidence_and_managed_name(self) -> None:
        dialog = self.build()
        self._validate(dialog, validation())
        self.service.import_result = SimpleNamespace(
            project=SimpleNamespace(server_profile_id=PROFILE_ID),
            remote_project_path=f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504",
        )

        with patch("moltage.gui.orca_import_dialog.QMessageBox.information"):
            dialog._import.click()
            wait_until(lambda: bool(self.service.import_requests))
            wait_until(lambda: not dialog._busy)

        request = self.service.import_requests[0]
        self.assertEqual(request.managed_project_base_name, "benzenedithiol")
        self.assertIs(request.validation, dialog.validation)
        self.assertIsNotNone(dialog.imported_project)

    def test_a_running_import_disables_repeat_activation(self) -> None:
        dialog = self.build()
        self._validate(dialog, validation())
        dialog._set_busy(True, "Creating the managed ORCA project...")

        self.assertFalse(dialog._import.isEnabled())
        self.assertFalse(dialog._validate.isEnabled())
        self.assertFalse(dialog._browse.isEnabled())
        self.assertFalse(dialog._close.isEnabled())

        dialog._start_import()

        self.assertEqual(self.service.import_requests, [])
        dialog._set_busy(False, "")

    def test_busy_dialog_refuses_to_close(self) -> None:
        dialog = self.build()
        dialog.show()
        self.addCleanup(dialog.hide)
        dialog._set_busy(True, "Validating...")

        dialog.reject()

        self.assertTrue(dialog.isVisible())

        dialog._set_busy(False, "")
        dialog.reject()

        self.assertFalse(dialog.isVisible())

    def test_invalid_managed_name_blocks_import(self) -> None:
        dialog = self.build()
        self._validate(dialog, validation())

        dialog.set_managed_project_name("bad name")

        self.assertFalse(dialog._import.isEnabled())

    # Cancellation ----------------------------------------------------

    def test_stop_is_only_available_while_an_operation_runs(self) -> None:
        dialog = self.build()

        self.assertFalse(dialog._stop.isEnabled())

        self._validate(dialog, validation())

        self.assertFalse(dialog._stop.isEnabled())

    def test_stop_requests_cancellation_of_the_running_operation(self) -> None:
        dialog = self.build()
        dialog.set_source_directory(SOURCE)
        from moltage.remote.executor import RemoteOperationStopToken

        dialog._stop_token = RemoteOperationStopToken()
        dialog._set_busy(True, "Validating...")

        self.assertTrue(dialog._stop.isEnabled())
        dialog._stop.click()

        self.assertTrue(dialog._stop_token.is_requested)
        dialog._set_busy(False, "")

    def test_a_stopped_operation_is_reported_without_creating_a_project(self) -> None:
        from moltage.remote.executor import RemoteOperationStopped

        dialog = self.build()
        self.service.validation_error = RemoteOperationStopped("stopped")
        dialog.set_source_directory(SOURCE)

        dialog._validate.click()
        wait_until(lambda: not dialog._busy)

        self.assertIn("was stopped", dialog._results.text())
        self.assertIsNone(dialog.imported_project)
        self.assertFalse(dialog._import.isEnabled())



class ImportMenuEntryTests(unittest.TestCase):
    """The Projects entry stays reachable without any loaded structure."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        from tools.molecule_viewer_demo import MoleculeViewerDemo

        cls.window = MoleculeViewerDemo()
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        cls.application.processEvents()
        cls.window = None

    def test_entry_is_enabled_with_no_molecule_loaded(self) -> None:
        action = self.window._import_orca_optimization_action

        self.assertIsNone(self.window._active_workspace().structure)
        self.assertTrue(action.isEnabled())
        self.assertEqual(action.text(), "ORCA Optimization...")

    def test_entry_lives_under_the_projects_menu_not_server_settings(self) -> None:
        projects = self.window._projects_menu
        submenu = self.window._import_calculation_menu

        self.assertEqual(submenu.title(), "Import Existing Calculation...")
        self.assertIn(
            submenu.menuAction(),
            projects.actions(),
        )
        self.assertNotIn(
            self.window._import_orca_optimization_action,
            self.window._server_menu.actions(),
        )
        self.assertNotIn(
            self.window._import_orca_optimization_action,
            self.window._settings_menu.actions(),
        )


if __name__ == "__main__":
    unittest.main()
