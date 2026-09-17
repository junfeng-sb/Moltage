from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.app.project_planning import StartStepAdvice
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.gui.project_submission import NewProjectSelection
from moltage.gui.workspace_tabs import (
    LocalGeometryWorkspaceIdentity,
    ManagedGeometryWorkspaceIdentity,
    TransmissionWorkspaceRequest,
)
from phase2b1_test_support import profile
from test_project_recovery import TEST_PROFILE
from test_projects_dialog import _snapshot, _step4_success_snapshot
from test_aitranss_dialog import (
    PROFILE as STEP4_PROFILE,
    _snapshot as _step4_ready_snapshot,
)
from tools.molecule_viewer_demo import MoleculeViewerDemo


class UiR4R1CalculationContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.local_path = root / "local.xyz"
        self.same_name_a = root / "one" / "molecule.xyz"
        self.same_name_b = root / "two" / "molecule.xyz"
        self.same_name_a.parent.mkdir()
        self.same_name_b.parent.mkdir()
        self.local_path.write_text(_xyz(0.0, "local"), encoding="utf-8")
        self.same_name_a.write_text(_xyz(0.0, "same A"), encoding="utf-8")
        self.same_name_b.write_text(_xyz(7.0, "same B"), encoding="utf-8")
        self.window = MoleculeViewerDemo()
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window._submission_running = False
        self.window._submission_workers.clear()
        self.window._transport_operation_running = False
        self.window._transport_workers.clear()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def test_case_a_only_local_geometry_routes_step1_and_rejects_steps_2_to_4(self):
        local = self.window._open_local_geometry(self.local_path)

        self.assertIsInstance(local.identity, LocalGeometryWorkspaceIdentity)
        structure, request = self._capture_step1(local)
        self.assertIs(structure, local.structure)
        self.assertIs(request.input_plan.structure, local.structure)
        self.assertFalse(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.assertFalse(self.window._continue_step4_action.isEnabled())

        with patch.object(
            self.window,
            "_show_local_submission_error",
        ) as show_error, patch.object(
            self.window,
            "_start_submission",
        ) as start_submission, patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies"
        ) as create_dependencies:
            self.window._continue_project_step2()
            self.window._continue_project_step3()
            self.window._continue_project_step4()

        self.assertEqual(show_error.call_count, 3)
        start_submission.assert_not_called()
        create_dependencies.assert_not_called()

    def test_case_b_local_never_falls_back_to_hidden_managed_project(self):
        local = self.window._open_local_geometry(self.local_path)
        snapshot_b = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        managed_b = self.window._open_recovered_geometry(snapshot_b, TEST_PROFILE)

        self.window._focus_workspace(local)
        structure, _request = self._capture_step1(local)
        self.assertIs(structure, local.structure)
        self.assertIsNone(self.window._recovery_snapshot)
        self.assertFalse(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.assertFalse(self.window._continue_step4_action.isEnabled())

        request_b = self._capture_step2(managed_b)
        self.assertEqual(request_b.project.project_id, snapshot_b.project.project_id)
        self.assertIs(self.window._recovery_snapshot, snapshot_b)

        self.window._focus_workspace(local)
        self.assertIsNone(self.window._recovery_snapshot)
        self.assertIsNone(self.window._recovery_profile)
        self.assertFalse(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.assertFalse(self.window._continue_step4_action.isEnabled())

    def test_case_c_two_managed_projects_supply_the_active_uuid(self):
        snapshot_a = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        snapshot_b = replace(
            snapshot_a,
            project=replace(snapshot_a.project, project_id=uuid4()),
        )
        managed_a = self.window._open_recovered_geometry(snapshot_a, TEST_PROFILE)
        managed_b = self.window._open_recovered_geometry(snapshot_b, TEST_PROFILE)

        self.assertEqual(managed_a.display_title, managed_b.display_title)
        self.assertIsInstance(managed_a.identity, ManagedGeometryWorkspaceIdentity)
        self.assertIsInstance(managed_b.identity, ManagedGeometryWorkspaceIdentity)

        project_manager = MagicMock()
        project_manager.selected_recovery.return_value = (snapshot_b, TEST_PROFILE)
        previous_project_manager = self.window._projects_dialog
        self.window._projects_dialog = project_manager
        try:
            request_a = self._capture_step2(managed_a)
            request_b = self._capture_step2(managed_b)
        finally:
            self.window._projects_dialog = previous_project_manager

        self.assertEqual(request_a.project.project_id, snapshot_a.project.project_id)
        self.assertEqual(request_b.project.project_id, snapshot_b.project.project_id)
        self.assertNotEqual(request_a.project.project_id, request_b.project.project_id)
        project_manager.selected_recovery.assert_not_called()

    def test_case_d_transmission_never_falls_back_to_hidden_geometry(self):
        snapshot_a = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        managed_a = self.window._open_recovered_geometry(snapshot_a, TEST_PROFILE)
        transmission_snapshot = _step4_success_snapshot()
        transmission_snapshot = replace(
            transmission_snapshot,
            project=replace(
                transmission_snapshot.project,
                project_id=snapshot_a.project.project_id,
                remote_directory_name=snapshot_a.project.remote_directory_name,
            ),
        )
        transmission = self.window._open_transmission_workspace(
            TransmissionWorkspaceRequest.from_snapshot(transmission_snapshot)
        )

        self.assertIs(self.window._active_workspace(), transmission)
        for action in (
            self.window._submit_aims_action,
            self.window._continue_step2_action,
            self.window._continue_step3_action,
            self.window._continue_step4_action,
        ):
            self.assertFalse(action.isEnabled())

        with patch.object(
            self.window,
            "_start_submission",
        ) as start_submission, patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies"
        ) as create_dependencies:
            self.window._submit_aims_optimization()
            self.window._continue_project_step2()
            self.window._continue_project_step3()
            self.window._continue_project_step4()

        start_submission.assert_not_called()
        create_dependencies.assert_not_called()
        self.window._focus_workspace(managed_a)
        self.assertIs(self.window._recovery_snapshot, snapshot_a)

    def test_case_e_same_basename_uses_only_active_canonical_identity(self):
        local_a = self.window._open_local_geometry(self.same_name_a)
        local_b = self.window._open_local_geometry(self.same_name_b)

        self.assertEqual(local_a.display_title, local_b.display_title)
        self.assertNotEqual(local_a.identity, local_b.identity)
        structure_a, request_a = self._capture_step1(local_a)
        structure_b, request_b = self._capture_step1(local_b)

        self.assertIs(structure_a, local_a.structure)
        self.assertIs(structure_b, local_b.structure)
        self.assertIsNot(structure_a, structure_b)
        self.assertEqual(request_a.source_molecule_name, "molecule.xyz")
        self.assertEqual(request_b.source_molecule_name, "molecule.xyz")
        self.assertEqual(
            local_a.identity.canonical_source_path,
            self.same_name_a.resolve(),
        )
        self.assertEqual(
            local_b.identity.canonical_source_path,
            self.same_name_b.resolve(),
        )

    def test_step3_context_constructor_receives_only_active_workspace_state(self):
        snapshot_a = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        snapshot_b = replace(
            snapshot_a,
            project=replace(snapshot_a.project, project_id=uuid4()),
        )
        managed_a = self.window._open_recovered_geometry(snapshot_a, TEST_PROFILE)
        managed_b = self.window._open_recovered_geometry(snapshot_b, TEST_PROFILE)
        captured = []

        def capture(snapshot, source, working, applied):
            captured.append((snapshot, source, working, applied))
            return snapshot.project.project_id

        with patch(
            "tools.molecule_viewer_demo.TransportConvergenceContext",
            side_effect=capture,
        ):
            self.window._focus_workspace(managed_a)
            project_a = self.window._current_transport_convergence_context()
            self.window._focus_workspace(managed_b)
            project_b = self.window._current_transport_convergence_context()

        self.assertEqual(project_a, snapshot_a.project.project_id)
        self.assertEqual(project_b, snapshot_b.project.project_id)
        self.assertIs(captured[0][0], snapshot_a)
        self.assertIs(captured[-1][0], snapshot_b)
        self.assertIs(captured[0][1], managed_a.source_structure)
        self.assertIs(captured[-1][1], managed_b.source_structure)

    def test_step4_preflight_origin_and_callback_context_use_active_project(self):
        snapshot_a = _step4_ready_snapshot()
        snapshot_b = replace(
            snapshot_a,
            project=replace(snapshot_a.project, project_id=uuid4()),
        )
        managed_a = self.window._open_recovered_geometry(snapshot_a, STEP4_PROFILE)
        managed_b = self.window._open_recovered_geometry(snapshot_b, STEP4_PROFILE)

        project_a = self._capture_step4_preflight_context(managed_a)
        project_b = self._capture_step4_preflight_context(managed_b)

        self.assertEqual(project_a, snapshot_a.project.project_id)
        self.assertEqual(project_b, snapshot_b.project.project_id)
        self.assertNotEqual(project_a, project_b)

    def _capture_step1(self, workspace):
        self.window._focus_workspace(workspace)
        selected_profile = profile()
        selection = NewProjectSelection(
            selected_profile,
            "context_test",
            ProjectStepKind.MOLECULE_OPT,
            "context_test.20300830",
        )
        repository = MagicMock()
        repository.load.return_value = SimpleNamespace(
            profiles=(selected_profile,),
            last_selected_profile_id=selected_profile.profile_id,
        )
        dependencies = SimpleNamespace(
            profile_repository=repository,
            secret_store=MagicMock(get_password=MagicMock(return_value=None)),
        )
        project_dialog = MagicMock()
        project_dialog.exec.return_value = QDialog.DialogCode.Accepted
        project_dialog.selected_project.return_value = selection
        settings_dialog = MagicMock()
        settings_dialog.exec.return_value = QDialog.DialogCode.Accepted
        settings_dialog.selected_settings.return_value = AimsOptimizationSettings()
        confirmation = MagicMock()
        confirmation.exec.return_value = QDialog.DialogCode.Accepted
        confirmation.take_temporary_password.return_value = "temporary"

        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.NewCalculationProjectDialog",
            return_value=project_dialog,
        ), patch(
            "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
            return_value=confirmation,
        ), patch.object(
            self.window,
            "_start_submission",
        ) as start_submission:
            self.window._submit_aims_optimization()

        request = start_submission.call_args.args[1]
        return request.input_plan.structure, request

    def _capture_step2(self, workspace):
        self.window._focus_workspace(workspace)
        self.window._confirmed = True
        self.window._applied_result = object()
        settings_dialog = MagicMock()
        settings_dialog.exec.return_value = QDialog.DialogCode.Accepted
        settings_dialog.selected_settings.return_value = AimsOptimizationSettings()
        confirmation = MagicMock()
        confirmation.exec.return_value = QDialog.DialogCode.Accepted
        confirmation.take_temporary_password.return_value = "temporary"
        dependencies = SimpleNamespace(
            secret_store=MagicMock(get_password=MagicMock(return_value=None)),
        )
        recommendation = SimpleNamespace(
            advice=StartStepAdvice.STEP2,
            reason="accepted Step-2 structure",
        )

        with patch(
            "tools.molecule_viewer_demo.recommend_start_step",
            return_value=recommendation,
        ), patch(
            "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.Step2ContinuationConfirmationDialog",
            return_value=confirmation,
        ), patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch.object(
            self.window,
            "_start_submission",
        ) as start_submission:
            self.window._continue_project_step2()

        return start_submission.call_args.args[1]

    def _capture_step4_preflight_context(self, workspace):
        self.window._focus_workspace(workspace)
        self.assertTrue(self.window._continue_step4_action.isEnabled())
        worker = MagicMock()
        worker.signals = SimpleNamespace(
            succeeded=MagicMock(),
            failed=MagicMock(),
            finished=MagicMock(),
        )
        service = object()
        dependencies = SimpleNamespace(transport_submission_service=service)

        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch.object(
            self.window,
            "_temporary_transport_password",
            return_value=None,
        ), patch(
            "tools.molecule_viewer_demo.AitranssPreflightWorker",
            return_value=worker,
        ), patch.object(
            self.window._submission_thread_pool,
            "start",
        ) as start_worker:
            self.window._continue_project_step4()

        start_worker.assert_called_once_with(worker)
        self.assertEqual(self.window._transport_origin_workspace_id, workspace.runtime_id)
        self.assertIs(self.window._pending_transport_profile, workspace.recovery_profile)
        with self.window._geometry_callback_context(workspace.runtime_id):
            project_id = self.window._recovery_snapshot.project.project_id

        self.window._transport_workers.clear()
        self.window._transport_operation_running = False
        self.window._transport_origin_workspace_id = None
        self.window._pending_transport_dependencies = None
        self.window._pending_transport_profile = None
        self.window._route_active_workspace()
        return project_id


def _xyz(offset: float, comment: str) -> str:
    return (
        "2\n"
        f"{comment}\n"
        f"C {offset:.1f} 0.0 0.0\n"
        f"H {offset + 1.0:.1f} 0.0 0.0\n"
    )


if __name__ == "__main__":
    unittest.main()
