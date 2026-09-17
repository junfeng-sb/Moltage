import gc
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from qt_test_support import wait_until
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.app.project_submission import ProjectSubmissionService
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from moltage.visualization.bond_torsion import (
    BondTorsionError,
    BondTorsionSession,
    structure_coordinates,
)
from phase2b1_test_support import MemorySecretStore, profile
from synthetic_structure_test_support import synthetic_step2_state
from test_project_recovery import NOW, _project
from test_project_submission import (
    FixedConnectionService,
    MemoryRemoteExecutor,
    _temporary_ids,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.molecule_viewer_demo import (
    MoleculeViewerDemo,
    _ProjectSubmissionDependencies,
)


class _AcceptedStep3SettingsDialog:
    observed_structure = None

    def __init__(self, structure, parent=None):
        del parent
        type(self).observed_structure = structure

    def exec(self):
        return QDialog.DialogCode.Accepted

    def selected_settings(self):
        return TransportConvergenceSettings()


class _AcceptedStep3ConfirmationDialog:
    observed_context = None

    def __init__(
        self,
        context,
        profile,
        settings,
        *,
        temporary_password_required,
        parent=None,
    ):
        del profile, settings, temporary_password_required, parent
        type(self).observed_context = context

    def exec(self):
        return QDialog.DialogCode.Accepted

    def take_temporary_password(self):
        return "temporary-secret"


class _RejectedStep3SettingsDialog(_AcceptedStep3SettingsDialog):
    def exec(self):
        return QDialog.DialogCode.Rejected


class _GatedStep3SubmissionService(ProjectSubmissionService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def continue_project_with_step3(self, request, *, progress=None):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return super().continue_project_with_step3(
            request,
            progress=progress,
        )


class TransportConvergenceViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.source, cls.connectivity, _anchors, _sites = synthetic_step2_state()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.window = MoleculeViewerDemo()
        self.window.show()
        self.application.processEvents()
        self.project = _project(
            ProjectStepState.SUCCEEDED,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        self.selected_profile = profile(
            profile_id=self.project.server_profile_id,
            save_password=False,
        )
        snapshot = ProjectRecoverySnapshot(
            self.project,
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=self.source,
            connectivity=self.connectivity,
        )
        self.window._load_recovered_snapshot(snapshot, self.selected_profile)
        controls = tuple(self.window._electrode_site_controls.values())
        self.assertEqual(len(controls), 2)
        controls[0].setChecked(True)
        controls[1].setChecked(True)
        self.application.processEvents()
        self.window._confirm_electrode_proposal()
        self.application.processEvents()
        self.assertTrue(self.window._continue_step3_action.isEnabled())
        self.executor = MemoryRemoteExecutor()
        root = self.project.remote_project_path
        metadata = root + "/.moltage"
        self.executor.directories.update((root, metadata, root + "/molecule_Au"))
        self.executor.files[metadata + "/project.json"] = (
            serialize_project_manifest(self.project).encode("utf-8")
        )
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.release.set()
        self._wait_until(
            lambda: not self.window._has_active_remote_operation(),
            fail_on_timeout=False,
        )
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        del self.window
        gc.collect()

    def _wait_until(self, predicate, timeout=5.0, *, fail_on_timeout=True):
        return wait_until(
            predicate, timeout, fail_on_timeout=fail_on_timeout,
            message="asynchronous Step-3 operation timed out",
        )

    def _dependencies(self, *, gated):
        service_type = (
            _GatedStep3SubmissionService if gated else ProjectSubmissionService
        )
        service = service_type(
            FixedConnectionService(self.executor),
            LocalProjectIndexRepository(
                Path(self.temporary.name) / "known_projects.json"
            ),
            now_factory=lambda: NOW,
            temporary_id_factory=_temporary_ids(),
        )
        if gated:
            self.services.append(service)
        return _ProjectSubmissionDependencies(
            Mock(),
            Mock(),
            MemorySecretStore(),
            Mock(),
            service,
        ), service

    def _start_through_step3_dialogs(self, dependencies):
        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.TransportConvergenceSettingsDialog",
            _AcceptedStep3SettingsDialog,
        ), patch(
            "tools.molecule_viewer_demo.Step3SubmissionConfirmationDialog",
            _AcceptedStep3ConfirmationDialog,
        ):
            self.window._continue_project_step3()

    def _dispatches(self):
        return tuple(
            operation[1]
            for operation in self.executor.operations
            if operation[0] == "execute"
            and operation[1].endswith("--parsable submit.sh")
        )

    def _rotate_working_geometry(self, angle_degrees):
        original = self.window._structure
        for bond in self.window._connectivity:
            edge = (bond.first_index, bond.second_index)
            try:
                session = BondTorsionSession(
                    original,
                    self.window._connectivity,
                    *edge,
                )
                candidate = session.structure_at(angle_degrees)
            except BondTorsionError:
                continue
            if structure_coordinates(candidate) == structure_coordinates(original):
                continue
            self.window._rotate_bond_button.click()
            self.window._bond_rotation_edge_picked(*edge)
            self.window._torsion_numeric_input.setText(str(angle_degrees))
            self.window._commit_torsion_numeric_edit()
            self.application.processEvents()
            return original, self.window._structure
        self.fail("the applied-electrode fixture has no effective rotatable edge")

    def test_cancelled_or_invalid_settings_stops_before_dependencies_or_worker(self):
        with patch(
            "tools.molecule_viewer_demo.TransportConvergenceSettingsDialog",
            _RejectedStep3SettingsDialog,
        ), patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies"
        ) as dependencies:
            self.window._continue_project_step3()

        dependencies.assert_not_called()
        self.assertFalse(self.window._submission_running)
        self.assertEqual(self.executor.operations, [])

    def test_manual_measurements_do_not_change_step3_eligibility_or_state(self):
        structure = self.window._structure
        connectivity = self.window._connectivity
        applied = self.window._applied_electrode_result
        project = self.window._recovery_snapshot.project
        context_before = self.window._current_transport_convergence_context()

        self.window._distance_measure_button.click()
        self.window._report_picked_atom(0)
        self.window._report_picked_atom(1)
        self.application.processEvents()

        context_after = self.window._current_transport_convergence_context()
        self.assertEqual(self.window._measurement_table.rowCount(), 1)
        self.assertTrue(self.window._continue_step3_action.isEnabled())
        self.assertIs(self.window._structure, structure)
        self.assertIs(self.window._connectivity, connectivity)
        self.assertIs(self.window._applied_electrode_result, applied)
        self.assertIs(self.window._recovery_snapshot.project, project)
        self.assertIs(context_before.working_structure, structure)
        self.assertIs(context_after.working_structure, structure)
        self.assertIs(context_after.applied_electrodes, applied)

    def test_step3_worker_is_async_terminal_and_main_close_guarded(self):
        dependencies, service = self._dependencies(gated=True)
        with patch.object(QMessageBox, "warning") as warning, patch.object(
            QMessageBox,
            "information",
        ) as information, patch.object(QMessageBox, "critical") as critical:
            self._start_through_step3_dialogs(dependencies)
            self._wait_until(service.started.is_set)

            self.assertTrue(self.window._submission_running)
            self.assertEqual(service.calls, 1)
            self.assertFalse(self.window._continue_step3_action.isEnabled())
            self.assertFalse(self.window.close())
            self.application.processEvents()
            warning.assert_called_once()
            self.assertIn(
                "Wait for it to finish before closing the application",
                warning.call_args.args[2],
            )

            service.release.set()
            self._wait_until(lambda: not self.window._submission_running)

        critical.assert_not_called()
        information.assert_called_once()
        self.assertEqual(information.call_args.args[1], "Step 3 submitted")
        success = information.call_args.args[2]
        self.assertIn("Step 3 submitted", success)
        self.assertIn("Job ID: 12345", success)
        self.assertIn("molecule_Au/transport", success)
        self.assertEqual(len(self._dispatches()), 1)
        self.assertIs(
            self.window._recovery_snapshot.project.steps[2].state,
            ProjectStepState.QUEUED,
        )
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.assertEqual(
            _AcceptedStep3SettingsDialog.observed_structure,
            self.window._structure,
        )
        self.assertEqual(
            _AcceptedStep3ConfirmationDialog.observed_context.working_structure,
            self.window._structure,
        )

    def test_rotated_applied_geometry_is_the_exact_uploaded_geometry_in(self):
        applied_structure, rotated = self._rotate_working_geometry(37.0)
        self.assertIs(
            self.window._active_geometry_workspace().structure,
            rotated,
        )
        self.assertTrue(self.window._continue_step3_action.isEnabled())
        expected = render_geometry_in(rotated).encode("utf-8")
        self.assertNotEqual(
            expected,
            render_geometry_in(applied_structure).encode("utf-8"),
        )
        dependencies, _service = self._dependencies(gated=False)

        with patch.object(QMessageBox, "information"), patch.object(
            QMessageBox,
            "critical",
        ) as critical:
            self._start_through_step3_dialogs(dependencies)
            self._wait_until(lambda: not self.window._submission_running)

        critical.assert_not_called()
        uploaded_path = (
            self.project.remote_project_path
            + "/molecule_Au/transport/geometry.in"
        )
        self.assertEqual(self.executor.files[uploaded_path], expected)
        self.assertEqual(len(self._dispatches()), 1)

    def test_presentation_failure_preserves_durable_queued_result_without_resubmit(self):
        dependencies, _service = self._dependencies(gated=False)
        with patch.object(
            QMessageBox,
            "information",
            side_effect=RuntimeError("synthetic presentation failure"),
        ), patch.object(QMessageBox, "critical"):
            self._start_through_step3_dialogs(dependencies)
            self._wait_until(lambda: not self.window._submission_running)

        self.assertEqual(len(self._dispatches()), 1)
        manifest = parse_project_manifest(
            self.executor.files[
                self.project.remote_project_path
                + "/.moltage/project.json"
            ]
        )
        self.assertIs(manifest.steps[2].state, ProjectStepState.QUEUED)
        self.assertEqual(manifest.steps[2].job_id, "12345")
        self.assertIs(
            self.window._submission_backend_result.step.state,
            ProjectStepState.QUEUED,
        )
        self.assertIn(
            "Submission succeeded as job 12345",
            self.window._operation_label.text(),
        )
        self.assertFalse(self.window._continue_step3_action.isEnabled())


if __name__ == "__main__":
    unittest.main()
