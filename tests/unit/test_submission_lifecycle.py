import gc
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.app.project_planning import create_initial_project
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.app.project_submission import (
    ExistingProjectStepSubmissionRequest,
    NewProjectSubmissionRequest,
    ProjectSubmissionResult,
    ProjectSubmissionService,
    SbatchRejectedError,
    SUBMISSION_LIFECYCLE_LOGGER_NAME,
    record_submission_lifecycle_event,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.xyz import read_xyz
from moltage.gui.project_submission import ProjectSubmissionWorker
from phase2b1_test_support import profile
from tools.molecule_viewer_demo import (
    MoleculeViewerDemo,
    _ProjectSubmissionDependencies,
    _SubmissionBackendOutcome,
    _SubmissionTerminalPresentation,
    _configure_submission_lifecycle_logging,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)
NOW = datetime(2030, 8, 27, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("00000000-0000-4000-8000-000000000042")


def _input_plan() -> AimsOptimizationInputPlan:
    return AimsOptimizationInputPlan(
        read_xyz(REFERENCE_XYZ_PATH),
        AimsOptimizationSettings(),
    )


class _LifecycleService(ProjectSubmissionService):
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def _run(self, progress):
        self.calls += 1
        progress("Connecting...")
        progress("Uploading input files...")
        progress("Recording project state...")
        if self.error is not None:
            raise self.error
        return self.result

    def create_and_submit_project(self, request, *, progress=None):
        del request
        return self._run(progress)

    def continue_project_with_step2(self, request, *, progress=None):
        del request
        return self._run(progress)


def _dependencies(service):
    return _ProjectSubmissionDependencies(
        Mock(),
        Mock(),
        Mock(),
        Mock(),
        service,
    )


def _step1_submission():
    selected = profile(save_password=False)
    project = create_initial_project(
        base_name="MoleculeA",
        remote_directory_name="MoleculeA.20300827",
        source_molecule_name="MoleculeA.xyz",
        server_profile_id=selected.profile_id,
        remote_project_root=selected.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_OPT,
        now=NOW,
        project_id=PROJECT_ID,
    )
    queued = replace(
        project.steps[0],
        state=ProjectStepState.QUEUED,
        job_id="43002",
        submitted_at=NOW,
        input_hashes=(
            ("geometry.in", "geometry-hash"),
            ("control.in", "control-hash"),
            ("submit.sh", "submit-hash"),
        ),
    )
    updated = replace(
        project,
        revision=2,
        steps=(queued, *project.steps[1:]),
    )
    request = NewProjectSubmissionRequest(
        selected,
        "MoleculeA",
        "MoleculeA.xyz",
        ProjectStepKind.MOLECULE_OPT,
        _input_plan(),
        "temporary-secret",
    )
    result = ProjectSubmissionResult(
        updated,
        queued,
        updated.remote_project_path,
        "43002",
        None,
        "geometry-hash",
        "control-hash",
        "submit-hash",
    )
    return request, result


def _step2_submission():
    selected = profile(save_password=False)
    project = create_initial_project(
        base_name="MoleculeA",
        remote_directory_name="MoleculeA.20300827",
        source_molecule_name="MoleculeA.xyz",
        server_profile_id=selected.profile_id,
        remote_project_root=selected.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_OPT,
        now=NOW,
        project_id=PROJECT_ID,
    )
    step1 = replace(
        project.steps[0],
        state=ProjectStepState.SUCCEEDED,
        job_id="11111",
        submitted_at=NOW,
        finished_at=NOW,
    )
    project = replace(
        project,
        revision=3,
        steps=(step1, *project.steps[1:]),
    )
    step2 = replace(
        project.steps[1],
        state=ProjectStepState.QUEUED,
        job_id="43002",
        submitted_at=NOW,
        input_hashes=(
            ("geometry.in", "geometry-hash"),
            ("control.in", "control-hash"),
            ("submit.sh", "submit-hash"),
        ),
    )
    updated = replace(
        project,
        revision=4,
        steps=(project.steps[0], step2, *project.steps[2:]),
    )
    request = ExistingProjectStepSubmissionRequest(
        selected,
        project,
        _input_plan(),
        "temporary-secret",
    )
    result = ProjectSubmissionResult(
        updated,
        step2,
        updated.remote_project_path + "/molecule_Au",
        "43002",
        None,
        "geometry-hash",
        "control-hash",
        "submit-hash",
    )
    structure = read_xyz(REFERENCE_XYZ_PATH)
    connectivity = infer_connectivity(
        structure,
        load_default_covalent_radii(),
    )
    snapshot = ProjectRecoverySnapshot(
        project,
        ProjectStepKind.MOLECULE_OPT,
        "Step 1 completed successfully.",
        optimized_structure=structure,
        connectivity=connectivity,
    )
    return request, result, snapshot, selected


class SubmissionLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.window = MoleculeViewerDemo()
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()
        cls.window = None
        gc.collect()

    def setUp(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        self.application.processEvents()

    def _prepare_step2(self, snapshot, selected) -> None:
        self.window._load_recovered_snapshot(snapshot, selected)
        self.window._confirmed = True
        self.window._applied_result = object()
        self.window._update_continuation_control()
        self.assertTrue(self.window._continue_step2_action.isEnabled())

    def _wait_for_finish(self) -> None:
        for _ in range(300):
            self.application.processEvents()
            if not self.window._submission_running:
                break
            QTest.qWait(5)
        self.assertFalse(self.window._submission_running)
        self.assertEqual(self.window._submission_workers, set())
        self.assertTrue(self.window._projects_action.isEnabled())

    def test_normal_step2_success_presents_job_state_and_cleans_up(self) -> None:
        request, result, snapshot, selected = _step2_submission()
        self._prepare_step2(snapshot, selected)
        service = _LifecycleService(result=result)

        with patch.object(QMessageBox, "information") as information, patch.object(
            QMessageBox, "critical"
        ) as critical:
            self.window._start_submission(_dependencies(service), request)
            self._wait_for_finish()

        self.assertEqual(service.calls, 1)
        self.assertIs(
            self.window._submission_backend_outcome,
            _SubmissionBackendOutcome.SUCCESS,
        )
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.SUCCESS,
        )
        self.assertNotIn("Connecting", self.window._operation_label.text())
        self.assertIn("43002", self.window._operation_label.text())
        critical.assert_not_called()
        information.assert_called_once()
        text = information.call_args.args[2]
        self.assertIn("Job ID: 43002", text)
        self.assertIn("State: QUEUED", text)
        self.assertIs(
            self.window._recovery_snapshot.project.steps[1].state,
            ProjectStepState.QUEUED,
        )
        self.assertFalse(self.window._continue_step2_action.isEnabled())

    def test_normal_failure_replaces_progress_and_reenables_controls(self) -> None:
        request, _result, snapshot, selected = _step2_submission()
        self._prepare_step2(snapshot, selected)
        service = _LifecycleService(
            error=SbatchRejectedError("synthetic scheduler rejection")
        )

        with patch.object(QMessageBox, "critical") as critical:
            self.window._start_submission(_dependencies(service), request)
            self._wait_for_finish()

        self.assertEqual(service.calls, 1)
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.FAILURE,
        )
        self.assertNotIn("Connecting", self.window._operation_label.text())
        self.assertIn("Slurm submission failed", self.window._operation_label.text())
        self.assertTrue(self.window._continue_step2_action.isEnabled())
        critical.assert_called_once()

    def test_success_presentation_exception_uses_durable_success_fallback(self) -> None:
        request, result, snapshot, selected = _step2_submission()
        self._prepare_step2(snapshot, selected)
        service = _LifecycleService(result=result)

        with patch.object(
            QMessageBox,
            "information",
            side_effect=RuntimeError("synthetic local presentation failure"),
        ):
            self.window._start_submission(_dependencies(service), request)
            self._wait_for_finish()

        self.assertEqual(service.calls, 1)
        self.assertIs(self.window._submission_backend_result, result)
        self.assertIs(result.step.state, ProjectStepState.QUEUED)
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.INTERNAL_PRESENTATION_FAILURE,
        )
        label = self.window._operation_label.text()
        self.assertNotIn("Connecting", label)
        self.assertIn("Submission succeeded", label)
        self.assertIn("43002", label)
        self.assertIn("Project Manager", label)
        self.assertIn("Refresh Status", label)
        self.assertFalse(self.window._continue_step2_action.isEnabled())

    def test_failure_presentation_exception_uses_internal_fallback(self) -> None:
        request, _result = _step1_submission()
        service = _LifecycleService(
            error=SbatchRejectedError("synthetic scheduler rejection")
        )

        with patch.object(
            QMessageBox,
            "critical",
            side_effect=RuntimeError("synthetic local presentation failure"),
        ):
            self.window._start_submission(_dependencies(service), request)
            self._wait_for_finish()

        self.assertEqual(service.calls, 1)
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.INTERNAL_PRESENTATION_FAILURE,
        )
        label = self.window._operation_label.text()
        self.assertNotIn("Connecting", label)
        self.assertIn("Submission failed", label)
        self.assertIn("detailed error", label)
        self.assertTrue(self.window._submit_aims_action.isEnabled())

    def test_finished_without_presented_outcome_replaces_stale_progress(self) -> None:
        request, result = _step1_submission()
        service = _LifecycleService(result=result)
        dependencies = _dependencies(service)
        worker = ProjectSubmissionWorker(service, request)
        self.window._reset_submission_lifecycle_tracking()
        self.window._submission_running = True
        self.window._pending_submission_dependencies = dependencies
        self.window._pending_submission_request = request
        self.window._submission_workers.add(worker)
        self.window._operation_label.setText("Connecting...")

        self.window._submission_worker_finished(worker)

        self.assertFalse(self.window._submission_running)
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.INTERNAL_PRESENTATION_FAILURE,
        )
        label = self.window._operation_label.text()
        self.assertNotIn("Connecting", label)
        self.assertIn("without a displayable result", label)
        self.assertIn("Project Manager", label)

    def test_step2_snapshot_failure_preserves_success_and_requires_refresh(self) -> None:
        request, result, snapshot, selected = _step2_submission()
        self._prepare_step2(snapshot, selected)
        service = _LifecycleService(result=result)

        with patch.object(
            self.window,
            "_apply_successful_step2_result",
            side_effect=RuntimeError("synthetic snapshot update failure"),
        ), patch.object(QMessageBox, "warning") as warning, patch.object(
            QMessageBox, "critical"
        ) as critical:
            self.window._start_submission(_dependencies(service), request)
            self._wait_for_finish()

        self.assertEqual(service.calls, 1)
        self.assertIs(self.window._submission_backend_result, result)
        self.assertIs(result.step.state, ProjectStepState.QUEUED)
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.SUCCESS,
        )
        self.assertTrue(self.window._submission_step2_refresh_required)
        self.assertFalse(self.window._continue_step2_action.isEnabled())
        self.assertNotIn("Connecting", self.window._operation_label.text())
        self.assertIn("submitted successfully", self.window._operation_label.text())
        self.assertIn("local project view", self.window._operation_label.text())
        critical.assert_not_called()
        warning.assert_called_once()
        warning_text = warning.call_args.args[2]
        self.assertIn("Job ID: 43002", warning_text)
        self.assertIn("State: QUEUED", warning_text)

    def test_step1_success_requires_no_output_file_and_keeps_existing_flow(self) -> None:
        request, result = _step1_submission()
        service = _LifecycleService(result=result)

        with patch.object(QMessageBox, "information") as information:
            self.window._start_submission(_dependencies(service), request)
            self._wait_for_finish()

        self.assertEqual(service.calls, 1)
        self.assertIs(
            self.window._submission_terminal_presentation,
            _SubmissionTerminalPresentation.SUCCESS,
        )
        self.assertIn("43002", self.window._operation_label.text())
        self.assertIn("QUEUED", self.window._operation_label.text())
        self.assertNotIn("aims.dft.out", information.call_args.args[2])
        self.assertTrue(self.window._submit_aims_action.isEnabled())

    def test_rotating_lifecycle_log_is_local_bounded_and_secret_free(self) -> None:
        import logging

        logger = logging.getLogger(SUBMISSION_LIFECYCLE_LOGGER_NAME)
        original_handlers = tuple(logger.handlers)
        original_level = logger.level
        original_propagate = logger.propagate
        for handler in original_handlers:
            logger.removeHandler(handler)
        try:
            with tempfile.TemporaryDirectory() as directory, patch(
                "tools.molecule_viewer_demo.submission_lifecycle_log_path",
                return_value=Path(directory) / "submission_lifecycle.log",
            ):
                self.assertTrue(_configure_submission_lifecycle_logging())
                handler = logger.handlers[0]
                self.assertEqual(handler.maxBytes, 512 * 1024)
                self.assertEqual(handler.backupCount, 2)
                record_submission_lifecycle_event(
                    "worker_started",
                    project_id=PROJECT_ID,
                    step_kind=ProjectStepKind.MOLECULE_AU_OPT,
                    job_id="43002",
                )
                handler.flush()
                text = (Path(directory) / "submission_lifecycle.log").read_text(
                    encoding="utf-8"
                )
                self.assertIn("event=worker_started", text)
                self.assertIn("project_id=" + str(PROJECT_ID), text)
                self.assertIn("job_id=43002", text)
                self.assertNotIn("temporary-secret", text)
                logger.removeHandler(handler)
                handler.close()
        finally:
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.setLevel(original_level)
            logger.propagate = original_propagate


if __name__ == "__main__":
    unittest.main()
