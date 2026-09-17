from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from PySide6.QtCore import QEvent, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from qt_test_support import wait_until
from moltage.gui.status_refresh import StatusRefreshSession
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyleOptionViewItem,
    QToolTip,
)

from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_evidence import SLURM_TASK_OUT_OF_MEMORY
from moltage.aitranss.output import AitranssFailureCode
from moltage.aitranss.tcontrol import parse_tcontrol
from moltage.aitranss.transmission import TransmissionPoint, parse_te_dat
from moltage.app.local_project_index import (
    LocalProjectIndexRepository,
    RecycledProjectReference,
)
from moltage.app.project_management import (
    PermanentProjectDeletionResult,
    ProjectManagementError,
)
from moltage.app.project_planning import create_initial_project
from moltage.app.project_geometry import (
    ProjectGeometryViewKind,
    ProjectGeometryViewResult,
)
from moltage.app.project_presentation import (
    ProjectPresentationRecord,
    ProjectSortCriterion,
    ProjectViewMode,
    StepIndicatorKind,
)
from moltage.app.project_recovery import (
    ACTIVE_TASK_OOM_DETAIL,
    ProjectDiscoveryResult,
    ProjectRecoveryError,
    ProjectRecoveryService,
    ProjectRecoverySnapshot,
    Step3OomCancellationOutcome,
    Step3OomCancellationResult,
    StepRuntimeEvidence,
)
from moltage.app.task_restart import (
    ProjectTaskCancellationOutcome,
    ProjectTaskRestartDraft,
    ProjectTaskRestartResult,
)
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.server_profile import runtime_hours_from_minutes
from moltage.domain.structure import Atom, MolecularStructure
from moltage.gui.projects_dialog import (
    CalculationProjectsDialog,
    DeleteProjectDialog,
    Step2ContinuationConfirmationDialog,
    Step3RetryDialog,
    Step4ExplicitRetryDialog,
    project_list_text,
    recovery_error_presentation,
)
from moltage.gui.project_list_view import (
    PROJECT_PRESENTATION_ROLE,
    PROJECT_UUID_ROLE,
    TILE_GRID_SIZE,
    TILE_ITEM_SIZE,
    _INDICATOR_COLORS,
    _UNFILLED_INDICATOR_KINDS,
)
from moltage.gui.transmission_view import TransmissionView
from moltage.gui.workspace_tabs import TransmissionWorkspaceRequest
from moltage.remote.executor import RemoteConnectionError
from moltage.remote.project_delete import (
    RemoteProjectDeletionOutcomeUnknown,
)
from moltage.remote.slurm_status import SchedulerStatusKind
from phase2b1_test_support import MemorySecretStore
from electrode_test_support import synthetic_project_electrode_provenance
from test_phase3b_recovery import (
    NOW as PHASE3B_NOW,
    PROFILE as PHASE3B_PROFILE,
    SYNTHETIC_DECORATED_CANCELLED_SACCT,
    REVIEWED_TASK_OOM_OUTPUT,
    _install as _install_phase3b_project,
    _synthetic_cancelled_project_and_files,
)
from test_project_recovery import (
    NOW,
    TEST_PROFILE,
    FixedConnectionService,
    RecoveryRemoteExecutor,
    _project,
    _step4_project,
)
from test_orca_submission_recovery import (
    NOW as ORCA_NOW,
    configured_profile as configured_orca_profile,
    settings as orca_settings,
    water as synthetic_orca_structure,
)
from test_phase3b_submission import _explicit_retry_project


class FakeKnownHosts:
    def __init__(self):
        self.trusted = []

    def trust(self, info):
        self.trusted.append(info)


class FakeRecoveryService:
    def __init__(
        self,
        discovery,
        refreshed=None,
        *,
        block=False,
        progress_messages=(),
        failure=None,
        cancellation_result=None,
        honor_stop=True,
    ):
        self.discovery = discovery
        self.refreshed = refreshed
        self.progress_messages = tuple(progress_messages)
        self.failure = failure
        self.cancellation_result = cancellation_result
        self.honor_stop = honor_stop
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        if not block:
            self.release.set()

    def _complete(self, progress, stop_token=None):
        if not self.honor_stop:
            stop_token = None
        for message in self.progress_messages:
            progress(message)
        self.started.set()
        for _index in range(300):
            if self.release.wait(timeout=0.01):
                break
            if stop_token is not None:
                stop_token.checkpoint()
        if stop_token is not None:
            stop_token.checkpoint()
        if self.failure is not None:
            raise self.failure

    def discover_and_refresh(
        self,
        profile,
        supplied_password=None,
        *,
        progress=lambda _message: None,
        stop_token=None,
    ):
        self.calls.append(("discover", profile, supplied_password))
        self._complete(progress, stop_token)
        return self.discovery

    def refresh_project(
        self,
        profile,
        path,
        *,
        profile_rebind_confirmed=False,
        supplied_password=None,
        progress=lambda _message: None,
        stop_token=None,
    ):
        self.calls.append(
            (
                "refresh",
                profile,
                path,
                profile_rebind_confirmed,
                supplied_password,
            )
        )
        self._complete(progress, stop_token)
        return self.refreshed

    def cancel_active_step3_oom_job(
        self,
        request,
        *,
        progress=lambda _message: None,
    ):
        self.calls.append(("cancel", request))
        self._complete(progress)
        return self.cancellation_result


class FakeProjectGeometryService:
    def __init__(self, result=None, *, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def load(self, request, *, progress=lambda _message: None):
        self.calls.append(request)
        progress("Reading project geometry...")
        if self.failure is not None:
            raise self.failure
        return self.result


class FakeTaskRestartService:
    def __init__(self, result=None, *, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def abort_and_prepare(self, request, *, progress=lambda _message: None):
        self.calls.append(request)
        progress("Preparing exact task restart...")
        if self.failure is not None:
            raise self.failure
        return self.result


class FakeProjectManagementService:
    """Local fake that records whether a GUI path crossed the remote boundary."""

    def __init__(self, *, permanent_failure=None):
        self.entries = []
        self.move_calls = []
        self.restore_calls = []
        self.permanent_calls = []
        self.permanent_failure = permanent_failure

    def recycled_projects(self):
        return tuple(self.entries)

    def move_to_recycle(self, profile, snapshot):
        self.move_calls.append((profile, snapshot))
        entry = RecycledProjectReference(
            project_id=snapshot.project.project_id,
            server_profile_id=profile.profile_id,
            remote_project_path=snapshot.project.remote_project_path,
            display_name=snapshot.project.display_name,
            submitted_at=snapshot.active_step.submitted_at,
            recycled_at=datetime(2030, 9, 1, 12, tzinfo=timezone.utc),
        )
        self.entries = [
            item for item in self.entries if item.identity != entry.identity
        ]
        self.entries.append(entry)
        return entry

    def restore(self, entry):
        self.restore_calls.append(entry)
        self.entries = [item for item in self.entries if item != entry]
        return entry

    def permanently_delete(self, request, *, progress=lambda _message: None):
        self.permanent_calls.append(request)
        progress("Deleting test project...")
        if self.permanent_failure is not None:
            raise self.permanent_failure
        return PermanentProjectDeletionResult(
            request.project.project_id,
            request.project.remote_project_path,
        )


def _snapshot(state=ProjectStepState.RUNNING, *, optimized=False):
    project = _project(state)
    structure = None
    connectivity = None
    if optimized:
        structure = MolecularStructure((Atom(0, "C", 0.1, 0.2, 0.3),))
        connectivity = Connectivity(1, ())
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.MOLECULE_OPT,
        f"Step 1 is {state.value}.",
        optimized_structure=structure,
        connectivity=connectivity,
    )


def _restart_draft(snapshot: ProjectRecoverySnapshot) -> ProjectTaskRestartDraft:
    structure = MolecularStructure((Atom(0, "C", 0.1, 0.2, 0.3),))
    return ProjectTaskRestartDraft(
        profile=TEST_PROFILE,
        source_project=snapshot.project,
        source_step=snapshot.active_step_kind,
        source_job_id=snapshot.active_step.job_id,
        source_structure=structure,
        connectivity=Connectivity(1, ()),
        source_geometry_sha256="a" * 64,
        optimization_settings=AimsOptimizationSettings(),
    )


def _managed_snapshot(
    sequence,
    remote_name,
    submitted_at,
    *,
    active_step=ProjectStepKind.MOLECULE_OPT,
    active_state=ProjectStepState.RUNNING,
):
    """Build a unique, domain-valid snapshot for presentation-only Qt tests."""

    snapshot = _snapshot(ProjectStepState.RUNNING)
    active_index = tuple(ProjectStepKind).index(active_step)
    steps = []
    for index, step in enumerate(snapshot.project.steps):
        if index < active_index:
            steps.append(
                replace(
                    step,
                    state=ProjectStepState.SUCCEEDED,
                    submitted_at=submitted_at - timedelta(minutes=10),
                    finished_at=submitted_at - timedelta(minutes=5),
                )
            )
        elif index == active_index:
            steps.append(
                replace(
                    step,
                    state=active_state,
                    job_id=(
                        None
                        if active_state
                        in {
                            ProjectStepState.NOT_STARTED,
                            ProjectStepState.UNKNOWN,
                        }
                        else str(70000 + sequence)
                    ),
                    submitted_at=(
                        None
                        if active_state is ProjectStepState.NOT_STARTED
                        else submitted_at
                    ),
                    finished_at=(
                        submitted_at + timedelta(minutes=5)
                        if active_state
                        in {
                            ProjectStepState.SUCCEEDED,
                            ProjectStepState.FAILED,
                            ProjectStepState.SCHEDULER_COMPLETED,
                        }
                        else None
                    ),
                )
            )
        else:
            steps.append(step)
    project = replace(
        snapshot.project,
        project_id=UUID(int=sequence),
        display_name=f"Display {sequence}",
        remote_directory_name=remote_name,
        remote_project_path=f"{TEST_PROFILE.remote_project_root}/{remote_name}",
        steps=tuple(steps),
        electrode_provenance=(
            synthetic_project_electrode_provenance()
            if active_index >= 2
            else ()
        ),
    )
    return replace(
        snapshot,
        project=project,
        active_step_kind=active_step,
        status_message=f"Fixture {sequence}",
    )


def _step3_failed_snapshot():
    project = _project(ProjectStepState.RUNNING)
    step2 = replace(
        project.steps[1],
        state=ProjectStepState.SUCCEEDED,
    )
    step3 = replace(
        project.steps[2],
        state=ProjectStepState.FAILED,
        job_id="41001",
        submitted_at=NOW,
        finished_at=NOW,
        last_error="运行时间到达设定上限",
        scheduler_state="TIMEOUT",
    )
    project = replace(
        project,
        steps=(project.steps[0], step2, step3, project.steps[3]),
        electrode_provenance=synthetic_project_electrode_provenance(),
    )
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
        "运行时间到达设定上限",
        step3_retry_preset=TEST_PROFILE.execution_preset,
    )


def _step3_oom_snapshot():
    snapshot = _step3_failed_snapshot()
    step3 = replace(
        snapshot.project.steps[2],
        last_error=SLURM_TASK_OUT_OF_MEMORY,
        scheduler_state="FAILED",
    )
    project = replace(
        snapshot.project,
        steps=(*snapshot.project.steps[:2], step3, snapshot.project.steps[3]),
    )
    return replace(
        snapshot,
        project=project,
        status_message="任务因内存不足终止",
    )


def _step3_running_oom_snapshot():
    snapshot = _step3_failed_snapshot()
    step3 = replace(
        snapshot.project.steps[2],
        state=ProjectStepState.RUNNING,
        finished_at=None,
        last_error=None,
        scheduler_state=None,
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    project = replace(
        snapshot.project,
        steps=(*snapshot.project.steps[:2], step3, snapshot.project.steps[3]),
    )
    return replace(
        snapshot,
        project=project,
        status_message=ACTIVE_TASK_OOM_DETAIL,
        runtime_evidence=StepRuntimeEvidence.TASK_OOM_DETECTED,
        step3_retry_preset=None,
    )


def _step4_overlap_snapshot():
    project = _step4_project(ProjectStepState.FAILED)
    step4 = replace(
        project.steps[3],
        last_error=AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value,
        scheduler_state="COMPLETED",
        finished_at=NOW,
    )
    project = replace(
        project,
        steps=(*project.steps[:3], step4),
    )
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.TRANSMISSION,
        "AITRANSS 电极界面区域识别重叠。",
    )


def _step4_self_energy_format_snapshot():
    project = _step4_project(ProjectStepState.FAILED)
    step4 = replace(
        project.steps[3],
        job_id="42002",
        submit_script_filename="submit.aitranss.retry02.sh",
        slurm_output_filename="aitranss.retry02.out",
        last_error=AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR.value,
        scheduler_state="COMPLETED",
        finished_at=NOW,
    )
    project = replace(project, steps=(*project.steps[:3], step4))
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.TRANSMISSION,
        "AITRANSS 无法读取显式 self-energy 文件。",
    )


def _step4_retry_ready_snapshot():
    project, structure, surface, plan, evidence, attempt01, _files = (
        _explicit_retry_project()
    )
    settings = parse_tcontrol(attempt01, structure).settings
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.TRANSMISSION,
        "Reviewed explicit retry is available.",
        optimized_structure=structure,
        transport_evidence=evidence,
        surface_proposal=surface,
        step4_attempt01_tcontrol=attempt01,
        step4_tcontrol_settings=settings,
        step4_self_energy_plan=plan,
    )


def _step4_success_snapshot():
    project = _step4_project(ProjectStepState.SUCCEEDED)
    result = parse_te_dat(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "phase4c"
            / "TE.synthetic-nonspin.dat"
        ).read_bytes()
    )
    result = replace(
        result,
        points=(
            TransmissionPoint(-0.20, -1.00, 0.02),
            TransmissionPoint(-0.19, -0.25, 0.20),
            TransmissionPoint(-0.18, 0.75, 0.60),
            TransmissionPoint(-0.17, 1.00, 0.10),
        ),
    )
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.TRANSMISSION,
        "Step 4 completed successfully.",
        transmission_result=result,
        transmission_result_filename="TE.dat",
    )


class CalculationProjectsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _dialog(
        self,
        service,
        *,
        geometry_service=None,
        task_restart_service=None,
        management_service=None,
        workspace_open=None,
        external_operation=None,
    ):
        dialog = CalculationProjectsDialog(
            (TEST_PROFILE,),
            TEST_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            project_geometry_service=geometry_service,
            project_task_restart_service=task_restart_service,
            project_management_service=management_service,
            project_workspace_open=workspace_open,
            project_has_external_operation=external_operation,
        )
        dialog._password_for = lambda _profile: (True, "temporary-secret")
        return dialog

    def _wait_until(self, predicate, timeout=3.0):
        wait_until(predicate, timeout, message="asynchronous GUI operation timed out")

    def test_minimal_controls_exist_without_periodic_timer(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)

        texts = {button.text() for button in dialog.findChildren(QPushButton)}
        self.assertTrue(
            {"Refresh", "Open / Recover", "Refresh Status", "Close"}
            <= texts
        )
        self.assertEqual(dialog._close.text(), "Close")
        self.assertFalse(dialog.has_active_remote_operation())
        self.assertEqual(dialog.findChildren(QTimer), [])
        self.assertEqual(service.calls, [])
        self.assertIn("Resubmit Optimization...", texts)
        self.assertIn("View WBL Transmission", texts)
        self.assertTrue(dialog._resubmit_orca.isHidden())
        self.assertTrue(dialog._view_orca_wbl.isHidden())

        primary_actions = dialog.findChild(
            QHBoxLayout,
            "projectsPrimaryActions",
        )
        workflow_actions = dialog.findChild(
            QHBoxLayout,
            "projectsWorkflowActions",
        )
        self.assertIsNotNone(primary_actions)
        self.assertIsNotNone(workflow_actions)
        primary_widgets = {
            primary_actions.itemAt(index).widget()
            for index in range(primary_actions.count())
            if primary_actions.itemAt(index).widget() is not None
        }
        workflow_widgets = {
            workflow_actions.itemAt(index).widget()
            for index in range(workflow_actions.count())
            if workflow_actions.itemAt(index).widget() is not None
        }
        self.assertEqual(
            primary_widgets,
            {
                dialog._open,
                dialog._refresh_status,
                dialog._view_transmission,
                dialog._view_orca_wbl,
                dialog._delete_project,
                dialog._restore_project,
            },
        )
        self.assertEqual(
            workflow_widgets,
            {
                dialog._kill_step3,
                dialog._cancel_orca,
                dialog._resubmit_orca,
                dialog._retry_step3,
                dialog._retry_step4,
                dialog._close,
            },
        )
        dialog.reject()

    def test_orca_resubmit_is_project_scoped_and_emits_recovered_context(self):
        profile = configured_orca_profile()
        project = create_initial_project(
            base_name="SyntheticOrcaResubmit",
            remote_directory_name="SyntheticOrcaResubmit.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=profile.profile_id,
            remote_project_root=profile.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=ORCA_NOW,
        )
        project = replace(
            project,
            steps=(
                replace(
                    project.steps[0],
                    state=ProjectStepState.FAILED,
                    scheduler_state="CANCELLED",
                    last_error="Synthetic cancellation",
                    orca_optimization_settings=orca_settings(),
                    orca_submitted_elements=("O", "H", "H"),
                ),
            ),
        )
        structure = synthetic_orca_structure()
        snapshot = ProjectRecoverySnapshot(
            project,
            ProjectStepKind.ORCA_OPTIMIZATION,
            "Synthetic ORCA optimization was cancelled.",
            submitted_structure=structure,
        )
        dialog = CalculationProjectsDialog(
            (profile,),
            profile.profile_id,
            FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ())),
            MemorySecretStore(),
            FakeKnownHosts(),
        )
        requests = []
        dialog.orca_optimization_resubmit_requested.connect(requests.append)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        self.assertFalse(dialog._resubmit_orca.isHidden())
        self.assertTrue(dialog._resubmit_orca.isEnabled())
        self.assertFalse(dialog._view_orca_wbl.isEnabled())
        dialog._resubmit_orca.click()

        self.assertEqual(requests, [(snapshot, profile, structure)])
        dialog.reject()

    def test_pm_r1_default_controls_and_date_order_are_exact(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)
        snapshots = (
            _managed_snapshot(
                2,
                "project2.20300828",
                datetime(2030, 8, 28, 10, tzinfo=timezone.utc),
            ),
            _managed_snapshot(
                10,
                "project10.20300831",
                datetime(2030, 8, 31, 10, tzinfo=timezone.utc),
            ),
            _managed_snapshot(
                11,
                "Project11.20300830",
                datetime(2030, 8, 30, 10, tzinfo=timezone.utc),
            ),
        )
        dialog._snapshots = snapshots
        dialog._render_snapshots()

        self.assertIs(
            dialog._sort_criterion,
            ProjectSortCriterion.SUBMISSION_DATE,
        )
        self.assertFalse(dialog._sort_reversed)
        self.assertEqual(dialog._sort.text(), "Sort: Submission date ↓")
        self.assertEqual(
            tuple(action.text() for action in dialog._sort.menu().actions()),
            ("File name", "Submission date", "Step type"),
        )
        self.assertIs(dialog._view_mode, ProjectViewMode.DETAILS)
        self.assertEqual(
            tuple(dialog._view.itemText(index) for index in range(dialog._view.count())),
            ("Details", "Compact", "Tiles"),
        )
        self.assertEqual(
            tuple(record.project_id for record in dialog._records),
            (UUID(int=10), UUID(int=11), UUID(int=2)),
        )
        self.assertEqual(service.calls, [])
        dialog.reject()

    def test_pm_r1_sort_criteria_full_reversal_preserve_uuid_without_refresh(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)
        snapshots = (
            _managed_snapshot(
                10,
                "project10",
                datetime(2030, 8, 31, tzinfo=timezone.utc),
                active_step=ProjectStepKind.MOLECULE_AU_OPT,
            ),
            _managed_snapshot(
                11,
                "Project11",
                datetime(2030, 1, 6, tzinfo=timezone.utc),
                active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            ),
            _managed_snapshot(
                2,
                "project2",
                datetime(2030, 8, 28, tzinfo=timezone.utc),
                active_step=ProjectStepKind.MOLECULE_OPT,
            ),
        )
        dialog._snapshots = snapshots
        dialog._render_snapshots()
        self.assertTrue(dialog._select_project_id(UUID(int=10)))

        file_action = next(
            action
            for action in dialog._sort.menu().actions()
            if action.text() == "File name"
        )
        file_action.trigger()
        self.application.processEvents()
        file_order = tuple(record.project_id for record in dialog._records)
        self.assertEqual(file_order, (UUID(int=2), UUID(int=10), UUID(int=11)))
        self.assertEqual(
            dialog._current_snapshot().project.project_id,
            UUID(int=10),
        )
        QTest.mouseDClick(dialog._sort, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertEqual(
            tuple(record.project_id for record in dialog._records),
            tuple(reversed(file_order)),
        )
        self.assertEqual(dialog._sort.text(), "Sort: File name ↓")

        step_action = next(
            action
            for action in dialog._sort.menu().actions()
            if action.text() == "Step type"
        )
        step_action.trigger()
        self.application.processEvents()
        step_order = tuple(record.project_id for record in dialog._records)
        self.assertEqual(step_order, (UUID(int=2), UUID(int=10), UUID(int=11)))
        self.assertFalse(dialog._sort_reversed)
        self.assertEqual(dialog._sort.text(), "Sort: Step type ↑")
        QTest.mouseDClick(dialog._sort, Qt.MouseButton.LeftButton)
        self.application.processEvents()
        self.assertEqual(
            tuple(record.project_id for record in dialog._records),
            tuple(reversed(step_order)),
        )
        self.assertEqual(
            dialog._current_snapshot().project.project_id,
            UUID(int=10),
        )
        self.assertEqual(service.calls, [])
        dialog.reject()

    def test_pm_r1_three_views_share_records_sort_selection_and_action_identity(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)
        snapshots = tuple(
            _managed_snapshot(
                sequence,
                f"project{sequence}",
                datetime(2030, 8, sequence, tzinfo=timezone.utc),
            )
            for sequence in (28, 30, 31)
        )
        dialog._snapshots = snapshots
        dialog._render_snapshots()
        expected_ids = tuple(record.project_id for record in dialog._records)
        selected_id = UUID(int=30)
        self.assertTrue(dialog._select_project_id(selected_id))
        self.assertIn("Step 1", dialog._project_list.currentItem().text())

        for mode in (ProjectViewMode.COMPACT, ProjectViewMode.TILES):
            with self.subTest(mode=mode):
                dialog._view.setCurrentIndex(dialog._view.findText(mode.value))
                self.application.processEvents()
                self.assertIs(dialog._view_mode, mode)
                self.assertEqual(
                    tuple(record.project_id for record in dialog._records),
                    expected_ids,
                )
                self.assertEqual(
                    tuple(
                        dialog._project_list.item(row).data(PROJECT_UUID_ROLE)
                        for row in range(dialog._project_list.count())
                    ),
                    tuple(str(project_id) for project_id in expected_ids),
                )
                self.assertEqual(
                    dialog._current_snapshot().project.project_id,
                    selected_id,
                )
                self.assertIsInstance(
                    dialog._project_list.currentItem().data(
                        PROJECT_PRESENTATION_ROLE
                    ),
                    ProjectPresentationRecord,
                )
        self.assertEqual(
            dialog._project_list.viewMode(),
            QListView.ViewMode.IconMode,
        )
        self.assertEqual(service.calls, [])
        dialog.reject()

    def test_pm_r1_tile_grid_is_fixed_reflows_and_has_no_horizontal_overflow(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)
        dialog._snapshots = tuple(
            _managed_snapshot(
                sequence,
                f"project{sequence}",
                datetime(2030, 8, 1, tzinfo=timezone.utc)
                + timedelta(minutes=sequence),
            )
            for sequence in range(1, 13)
        )
        dialog._render_snapshots()
        dialog._view.setCurrentIndex(dialog._view.findText("Tiles"))
        dialog.show()
        self.application.processEvents()

        view = dialog._project_list
        self.assertIs(dialog._view_mode, ProjectViewMode.TILES)
        self.assertEqual(view.viewMode(), QListView.ViewMode.IconMode)
        self.assertEqual(view.gridSize(), TILE_GRID_SIZE)
        self.assertTrue(view.isWrapping())
        self.assertEqual(
            view.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, TILE_GRID_SIZE.width(), TILE_GRID_SIZE.height())
        self.assertEqual(
            dialog._project_delegate.sizeHint(option, view.model().index(0, 0)),
            TILE_ITEM_SIZE,
        )

        def first_row_columns():
            view.doItemsLayout()
            self.application.processEvents()
            rectangles = [
                view.visualItemRect(view.item(row)) for row in range(view.count())
            ]
            first_top = min(rect.top() for rect in rectangles)
            return sum(rect.top() == first_top for rect in rectangles)

        initial_columns = first_row_columns()
        self.assertIn(initial_columns, (4, 5))
        initial_grid = view.gridSize()
        view.setFixedWidth(1080)
        wider_columns = first_row_columns()
        view.setFixedWidth(440)
        narrower_columns = first_row_columns()

        self.assertGreater(wider_columns, initial_columns)
        self.assertLess(narrower_columns, initial_columns)
        self.assertEqual(view.gridSize(), initial_grid)
        self.assertEqual(view.horizontalScrollBar().maximum(), 0)
        dialog.close()

    def test_pm_r1_delegate_exposes_four_indicator_tooltips_and_exact_colors(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)
        snapshot = _managed_snapshot(
            33,
            "oom-project",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
            active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        snapshot = replace(
            snapshot,
            runtime_evidence=StepRuntimeEvidence.TASK_OOM_DETECTED,
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        dialog._view.setCurrentIndex(dialog._view.findText("Compact"))
        self.application.processEvents()

        record = dialog._project_list.item(0).data(PROJECT_PRESENTATION_ROLE)
        self.assertIsInstance(record, ProjectPresentationRecord)
        self.assertEqual(len(record.indicators), 4)
        self.assertEqual(
            tuple(indicator.step_number for indicator in record.indicators),
            (1, 2, 3, 4),
        )
        self.assertEqual(
            record.indicators[2].tooltip,
            "Step 3 — RUNNING — OOM detected",
        )
        self.assertEqual(
            {
                kind: _INDICATOR_COLORS[kind].name()
                for kind in (
                    StepIndicatorKind.SUCCEEDED,
                    StepIndicatorKind.SKIPPED,
                    StepIndicatorKind.ACTIVE,
                    StepIndicatorKind.FAILED,
                    StepIndicatorKind.UNKNOWN,
                )
            },
            {
                StepIndicatorKind.SUCCEEDED: "#2e7d32",
                StepIndicatorKind.SKIPPED: "#757575",
                StepIndicatorKind.ACTIVE: "#fbc02d",
                StepIndicatorKind.FAILED: "#c62828",
                StepIndicatorKind.UNKNOWN: "#fbc02d",
            },
        )
        self.assertEqual(
            _UNFILLED_INDICATOR_KINDS,
            frozenset({StepIndicatorKind.NOT_STARTED}),
        )
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 700, 34)
        indicator_rects = dialog._project_delegate.indicator_rects(
            option.rect,
            ProjectViewMode.COMPACT,
        )
        self.assertEqual(len(indicator_rects), 4)
        event = QHelpEvent(
            QEvent.Type.ToolTip,
            indicator_rects[2].center(),
            QPoint(100, 100),
        )
        with patch.object(QToolTip, "showText") as show_text:
            handled = dialog._project_delegate.helpEvent(
                event,
                dialog._project_list,
                option,
                dialog._project_list.model().index(0, 0),
            )
        self.assertTrue(handled)
        self.assertEqual(
            show_text.call_args.args[1],
            "Step 3 — RUNNING — OOM detected",
        )
        dialog.reject()

    def test_pm_r1_r1_unresolved_lights_and_accessibility_are_exact_in_both_views(self):
        dialog = self._dialog(FakeRecoveryService(ProjectDiscoveryResult((), ())))
        snapshots = (
            _managed_snapshot(
                34,
                "not-started",
                datetime(2030, 8, 29, tzinfo=timezone.utc),
                active_step=ProjectStepKind.TRANSMISSION,
                active_state=ProjectStepState.NOT_STARTED,
            ),
            _managed_snapshot(
                35,
                "scheduler-completed",
                datetime(2030, 1, 6, tzinfo=timezone.utc),
                active_step=ProjectStepKind.TRANSMISSION,
                active_state=ProjectStepState.SCHEDULER_COMPLETED,
            ),
            _managed_snapshot(
                36,
                "unknown",
                datetime(2030, 8, 31, tzinfo=timezone.utc),
                active_step=ProjectStepKind.TRANSMISSION,
                active_state=ProjectStepState.UNKNOWN,
            ),
        )
        dialog._snapshots = snapshots
        dialog._render_snapshots()

        by_name = {
            record.remote_directory_basename: record
            for record in dialog._records
        }
        self.assertIs(
            by_name["not-started"].indicators[3].kind,
            StepIndicatorKind.NOT_STARTED,
        )
        for name, exact_state in (
            ("scheduler-completed", "SCHEDULER_COMPLETED"),
            ("unknown", "UNKNOWN"),
        ):
            indicator = by_name[name].indicators[3]
            self.assertIs(indicator.kind, StepIndicatorKind.UNKNOWN)
            self.assertEqual(indicator.tooltip, f"Step 4 — {exact_state}")
        self.assertEqual(
            _INDICATOR_COLORS[StepIndicatorKind.UNKNOWN].name(),
            "#fbc02d",
        )
        self.assertNotIn(
            StepIndicatorKind.UNKNOWN,
            _UNFILLED_INDICATOR_KINDS,
        )
        self.assertIn(
            StepIndicatorKind.NOT_STARTED,
            _UNFILLED_INDICATOR_KINDS,
        )

        for mode in (ProjectViewMode.COMPACT, ProjectViewMode.TILES):
            with self.subTest(mode=mode):
                dialog._view.setCurrentIndex(dialog._view.findText(mode.value))
                self.application.processEvents()
                option = QStyleOptionViewItem()
                option.rect = (
                    QRect(0, 0, 700, 34)
                    if mode is ProjectViewMode.COMPACT
                    else QRect(0, 0, TILE_GRID_SIZE.width(), TILE_GRID_SIZE.height())
                )
                for row in range(dialog._project_list.count()):
                    item = dialog._project_list.item(row)
                    record = item.data(PROJECT_PRESENTATION_ROLE)
                    expected_description = "; ".join(
                        indicator.tooltip for indicator in record.indicators
                    )
                    self.assertEqual(
                        item.data(Qt.ItemDataRole.AccessibleTextRole),
                        record.remote_directory_basename,
                    )
                    self.assertEqual(
                        item.data(Qt.ItemDataRole.AccessibleDescriptionRole),
                        expected_description,
                    )
                    if record.remote_directory_basename == "not-started":
                        continue
                    indicator_rect = dialog._project_delegate.indicator_rects(
                        option.rect,
                        mode,
                    )[3]
                    event = QHelpEvent(
                        QEvent.Type.ToolTip,
                        indicator_rect.center(),
                        QPoint(100, 100),
                    )
                    with patch.object(QToolTip, "showText") as show_text:
                        handled = dialog._project_delegate.helpEvent(
                            event,
                            dialog._project_list,
                            option,
                            dialog._project_list.model().index(row, 0),
                        )
                    self.assertTrue(handled)
                    self.assertEqual(
                        show_text.call_args.args[1],
                        record.indicators[3].tooltip,
                    )
        dialog.reject()

    def test_pm_r1_delete_dialog_resets_unchecked_and_rejects_active_entirely(self):
        terminal = _managed_snapshot(
            40,
            "terminal-project",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
            active_state=ProjectStepState.SUCCEEDED,
        )
        first = DeleteProjectDialog(
            terminal,
            permanent_block_reason=None,
        )
        first_checkbox = first.findChild(
            QCheckBox,
            "deleteServerFilesPermanently",
        )
        self.assertFalse(first_checkbox.isChecked())
        first_checkbox.setChecked(True)
        self.assertEqual(first._confirm.text(), "Delete Permanently")
        self.assertIn("cannot be restored", first._explanation.text())
        first.reject()

        second = DeleteProjectDialog(
            terminal,
            permanent_block_reason=None,
        )
        second_checkbox = second.findChild(
            QCheckBox,
            "deleteServerFilesPermanently",
        )
        self.assertFalse(second_checkbox.isChecked())
        self.assertEqual(second._confirm.text(), "Move to Recycle Bin")
        self.assertIn(
            "Server files and running jobs will not be changed",
            second._explanation.text(),
        )
        second.reject()

        active = _managed_snapshot(
            41,
            "active-project",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(
            ProjectManagementError,
            "active or unresolved work",
        ):
            DeleteProjectDialog(
                active,
                permanent_block_reason="Active job blocks permanent deletion.",
            )

    def test_pm_r1_r1_active_and_scheduler_unresolved_delete_is_rejected(self):
        cases = tuple(
            (state.value, state, None)
            for state in (
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
                ProjectStepState.UNKNOWN,
            )
        ) + tuple(
            (status.value, ProjectStepState.SUCCEEDED, status)
            for status in (
                SchedulerStatusKind.ACCOUNTING_PENDING,
                SchedulerStatusKind.UNRESOLVED,
            )
        )
        for sequence, (label, state, scheduler_status) in enumerate(cases, start=70):
            with self.subTest(case=label):
                recovery = FakeRecoveryService(ProjectDiscoveryResult((), ()))
                management = FakeProjectManagementService()
                dialog = self._dialog(
                    recovery,
                    management_service=management,
                    workspace_open=lambda _project_id: False,
                )
                snapshot = _managed_snapshot(
                    sequence,
                    f"blocked-{sequence}",
                    datetime(2030, 8, 31, tzinfo=timezone.utc),
                    active_state=state,
                )
                snapshot = replace(
                    snapshot,
                    scheduler_status_kind=scheduler_status,
                )
                dialog._snapshots = (snapshot,)
                dialog._render_snapshots()
                password = Mock(
                    side_effect=AssertionError("credentials requested")
                )
                dialog._password_for = password

                self.assertFalse(dialog._delete_project.isEnabled())
                dialog._delete_selected_project()

                self.assertEqual(dialog._project_list.count(), 1)
                self.assertEqual(len(dialog._records), 1)
                self.assertEqual(management.move_calls, [])
                self.assertEqual(management.permanent_calls, [])
                self.assertEqual(management.entries, [])
                self.assertEqual(recovery.calls, [])
                password.assert_not_called()
                self.assertIn("active or unresolved work", dialog._status.text())
                dialog.reject()

    def test_pm_r1_r1_external_submission_guard_is_project_aware_and_fail_closed(self):
        recovery = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        management = FakeProjectManagementService()
        active_ids = set()

        def external_operation(project_id):
            return project_id in active_ids

        dialog = self._dialog(
            recovery,
            management_service=management,
            workspace_open=lambda _project_id: False,
            external_operation=external_operation,
        )
        target = _managed_snapshot(
            80,
            "submission-target",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
            active_state=ProjectStepState.SUCCEEDED,
        )
        unrelated = _managed_snapshot(
            81,
            "terminal-unrelated",
            datetime(2030, 1, 6, tzinfo=timezone.utc),
            active_state=ProjectStepState.SUCCEEDED,
        )
        dialog._snapshots = (target, unrelated)
        dialog._render_snapshots()
        self.assertTrue(dialog._select_project_id(target.project.project_id))
        self.assertTrue(dialog._delete_project.isEnabled())

        active_ids.add(target.project.project_id)
        dialog.external_operation_state_changed()
        self.assertFalse(dialog._delete_project.isEnabled())
        dialog._delete_selected_project()
        self.assertEqual(management.move_calls, [])
        self.assertEqual(management.permanent_calls, [])
        self.assertEqual(dialog._project_list.count(), 2)

        self.assertTrue(dialog._select_project_id(unrelated.project.project_id))
        self.assertTrue(dialog._delete_project.isEnabled())

        dialog._project_has_external_operation = lambda _project_id: (_ for _ in ()).throw(
            RuntimeError("operation state unavailable")
        )
        dialog.external_operation_state_changed()
        self.assertFalse(dialog._delete_project.isEnabled())
        dialog.reject()

    def test_pm_r1_r1_scheduler_completed_is_delete_eligible_but_unresolved(self):
        dialog = self._dialog(
            FakeRecoveryService(ProjectDiscoveryResult((), ())),
            management_service=FakeProjectManagementService(),
            workspace_open=lambda _project_id: False,
        )
        snapshot = _managed_snapshot(
            82,
            "scheduler-completed",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
            active_state=ProjectStepState.SCHEDULER_COMPLETED,
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        self.assertTrue(dialog._delete_project.isEnabled())
        self.assertIsNone(dialog._project_delete_block_reason(snapshot))
        self.assertIsNone(dialog._permanent_delete_block_reason(snapshot))
        record = dialog._project_list.item(0).data(PROJECT_PRESENTATION_ROLE)
        self.assertIs(record.indicators[0].kind, StepIndicatorKind.UNKNOWN)
        self.assertEqual(
            record.indicators[0].tooltip,
            "Step 1 — SCHEDULER_COMPLETED",
        )
        dialog.reject()

    def test_pm_r1_local_recycle_and_restore_use_no_credentials_or_remote_path(self):
        recovery = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        management = FakeProjectManagementService()
        dialog = self._dialog(
            recovery,
            management_service=management,
            workspace_open=lambda _project_id: False,
        )
        snapshots = tuple(
            _managed_snapshot(
                sequence,
                f"project{sequence}",
                datetime(2030, 8, sequence, tzinfo=timezone.utc),
                active_state=ProjectStepState.SUCCEEDED,
            )
            for sequence in (28, 30, 31)
        )
        dialog._snapshots = snapshots
        dialog._render_snapshots()
        selected_id = UUID(int=30)
        self.assertTrue(dialog._select_project_id(selected_id))
        credential_request = Mock(side_effect=AssertionError("credentials requested"))
        dialog._password_for = credential_request

        def accept_local(delete_dialog):
            self.assertFalse(delete_dialog.delete_server_files_permanently())
            return QDialog.DialogCode.Accepted

        with patch.object(DeleteProjectDialog, "exec", accept_local):
            dialog._delete_selected_project()

        self.assertEqual(len(management.move_calls), 1)
        self.assertEqual(management.permanent_calls, [])
        self.assertEqual(recovery.calls, [])
        credential_request.assert_not_called()
        self.assertEqual(
            {record.project_id for record in dialog._records},
            {UUID(int=28), UUID(int=31)},
        )
        self.assertIn("Server files and running jobs were not changed", dialog._status.text())

        dialog._show_recycle_bin()
        self.assertTrue(dialog._in_recycle_bin)
        self.assertEqual(dialog._project_list.count(), 1)
        self.assertIs(
            dialog._project_list.itemDelegate(),
            dialog._recycle_delegate,
        )
        self.assertIn("Display 30", dialog._project_list.item(0).text())
        self.assertTrue(dialog._restore_project.isEnabled())
        dialog._restore_selected_project()

        self.assertEqual(len(management.restore_calls), 1)
        self.assertEqual(management.entries, [])
        self.assertEqual(management.permanent_calls, [])
        self.assertEqual(recovery.calls, [])
        credential_request.assert_not_called()
        self.assertIn("restored locally", dialog._status.text())
        self.assertIn("Refresh", dialog._status.text())
        dialog._show_projects()
        self.assertFalse(dialog._in_recycle_bin)
        self.assertTrue(dialog._back_to_projects.isHidden())
        self.assertFalse(dialog._sort.isHidden())
        self.assertFalse(dialog._view.isHidden())
        self.assertEqual(dialog._project_list.count(), 2)
        self.assertEqual(recovery.calls, [])
        dialog.reject()

    def test_pm_r1_workspace_guard_blocks_only_the_selected_project(self):
        recovery = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        target = _managed_snapshot(
            50,
            "target",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
            active_state=ProjectStepState.SUCCEEDED,
        )
        seen = []

        def workspace_open(project_id):
            seen.append(project_id)
            return project_id == target.project.project_id

        dialog = self._dialog(
            recovery,
            management_service=FakeProjectManagementService(),
            workspace_open=workspace_open,
        )
        reason = dialog._permanent_delete_block_reason(target)
        self.assertIn("Close this project's open Geometry and Transmission", reason)
        self.assertEqual(seen, [target.project.project_id])

        dialog._project_workspace_open = lambda _project_id: False
        self.assertIsNone(dialog._permanent_delete_block_reason(target))
        dialog.reject()

    def test_pm_r1_permanent_delete_ui_routes_success_definite_and_unknown_once(self):
        terminal = _managed_snapshot(
            60,
            "terminal-delete",
            datetime(2030, 8, 31, tzinfo=timezone.utc),
            active_state=ProjectStepState.SUCCEEDED,
        )

        def accept_permanent(delete_dialog):
            checkbox = delete_dialog.findChild(
                QCheckBox,
                "deleteServerFilesPermanently",
            )
            checkbox.setChecked(True)
            return QDialog.DialogCode.Accepted

        cases = (
            (None, 0, "Permanently deleted server project"),
            (
                ProjectManagementError("synthetic definite refusal"),
                1,
                "Project deletion failed: synthetic definite refusal",
            ),
            (
                RemoteProjectDeletionOutcomeUnknown("ambiguous loss"),
                1,
                "Deletion outcome unknown. Reconnect and Refresh.",
            ),
        )
        for failure, expected_rows, expected_status in cases:
            with self.subTest(failure=type(failure).__name__ if failure else "success"):
                recovery = FakeRecoveryService(ProjectDiscoveryResult((), ()))
                management = FakeProjectManagementService(
                    permanent_failure=failure,
                )
                dialog = self._dialog(
                    recovery,
                    management_service=management,
                    workspace_open=lambda _project_id: False,
                )
                dialog._snapshots = (terminal,)
                dialog._render_snapshots()
                password = Mock(return_value=(True, "temporary-secret"))
                dialog._password_for = password

                with patch.object(DeleteProjectDialog, "exec", accept_permanent):
                    dialog._delete_selected_project()
                    self._wait_until(lambda: not dialog._busy)

                self.assertEqual(len(management.permanent_calls), 1)
                self.assertEqual(management.move_calls, [])
                self.assertEqual(recovery.calls, [])
                password.assert_called_once_with(TEST_PROFILE)
                self.assertEqual(dialog._project_list.count(), expected_rows)
                self.assertIn(expected_status, dialog._status.text())
                dialog.reject()

    def test_saved_profile_update_is_used_for_future_dialog_operations(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = self._dialog(service)
        dialog._selected_profile = TEST_PROFILE
        updated = replace(
            TEST_PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="new@example.com",
        )

        dialog.update_profile(updated)

        self.assertEqual(dialog._server.currentData(), updated)
        self.assertEqual(dialog._selected_profile, updated)
        dialog.reject()

    def test_terminal_step3_timeout_exposes_explicit_retry_and_reason(self):
        service = FakeRecoveryService(ProjectDiscoveryResult((), ()))
        dialog = CalculationProjectsDialog(
            (TEST_PROFILE,),
            TEST_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            transport_submission_service=object(),
        )
        snapshot = _step3_failed_snapshot()
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        self.assertTrue(dialog._retry_step3.isEnabled())
        self.assertEqual(dialog._retry_step3.text(), "Resubmit Step 3...")
        self.assertEqual(dialog._status.text(), "运行时间到达设定上限")
        self.assertIn("Step 3 — FAILED", dialog._project_list.item(0).text())
        self.assertIn("Job 41001", dialog._project_list.item(0).text())
        dialog.reject()
        self.assertEqual(service.calls, [])

    def test_terminal_step3_task_oom_shows_chinese_reason_without_raw_code(self):
        snapshot = _step3_oom_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        text = dialog._project_list.item(0).text()
        self.assertIn("Step 3 — FAILED", text)
        self.assertIn("Job 41001", text)
        self.assertIn("Reason: 任务因内存不足终止", text)
        self.assertIn("Slurm: FAILED", text)
        self.assertNotIn(SLURM_TASK_OUT_OF_MEMORY, text)
        self.assertEqual(dialog._status.text(), "任务因内存不足终止")
        self.assertTrue(snapshot.can_retry_step3)
        dialog.reject()

        retry_dialog = Step3RetryDialog(
            snapshot,
            TEST_PROFILE,
            snapshot.step3_retry_preset,
        )
        label_text = "\n".join(
            label.text() for label in retry_dialog.findChildren(QLabel)
        )
        self.assertIn("任务因内存不足终止", label_text)
        self.assertNotIn(SLURM_TASK_OUT_OF_MEMORY, label_text)
        self.assertEqual(
            retry_dialog.findChild(QSpinBox, "step3RetryMemoryGb").value(),
            128,
        )
        retry_dialog.reject()

    def test_oom_retry_memory_starts_from_attempt_not_drifted_profile(self):
        snapshot = _step3_oom_snapshot()
        drifted_profile = replace(
            TEST_PROFILE,
            execution_preset=replace(TEST_PROFILE.execution_preset, memory_gb=64),
        )
        dialog = Step3RetryDialog(
            snapshot,
            drifted_profile,
            snapshot.step3_retry_preset,
        )
        memory = dialog.findChild(QSpinBox, "step3RetryMemoryGb")

        self.assertEqual(memory.value(), 128)
        memory.setValue(192)
        dialog._accept_settings()
        self.assertEqual(dialog.selected_preset().memory_gb, 192)
        self.assertEqual(
            dialog.selected_preset().launch_command,
            snapshot.step3_retry_preset.launch_command,
        )

    def test_active_step3_oom_is_derived_running_with_only_kill_enabled(self):
        snapshot = _step3_running_oom_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = CalculationProjectsDialog(
            (TEST_PROFILE,),
            TEST_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            transport_submission_service=object(),
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        text = dialog._project_list.item(0).text()
        self.assertIn("Step 3 — RUNNING — OOM detected", text)
        self.assertIn("Job 41001", text)
        self.assertEqual(dialog._status.text(), ACTIVE_TASK_OOM_DETAIL)
        self.assertTrue(dialog._kill_step3.isEnabled())
        self.assertFalse(dialog._kill_step3.isHidden())
        self.assertFalse(dialog._retry_step3.isEnabled())
        dialog.reject()

    def test_kill_confirmation_cancel_starts_no_worker_or_remote_operation(self):
        snapshot = _step3_running_oom_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        with patch.object(dialog, "_confirm_step3_oom_kill", return_value=False):
            dialog._kill_selected_step3()

        self.assertEqual(service.calls, [])
        self.assertEqual(dialog._workers, set())
        self.assertEqual(dialog._status.text(), "Job cancellation was cancelled.")
        dialog.reject()

    def test_kill_confirmation_text_contains_exact_job_and_named_buttons(self):
        snapshot = _step3_running_oom_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        captured = {}

        def capture(box):
            captured["text"] = box.text()
            captured["buttons"] = {button.text() for button in box.buttons()}
            return 0

        with patch.object(QMessageBox, "exec", capture):
            accepted = dialog._confirm_step3_oom_kill("41005")

        self.assertFalse(accepted)
        self.assertIn("Kill Slurm Job 41005?", captured["text"])
        self.assertEqual(captured["buttons"], {"Kill Job", "Cancel"})
        dialog.reject()

    def test_confirmed_kill_is_async_once_and_disables_redispatch_until_refresh(self):
        snapshot = _step3_running_oom_snapshot()
        outcome = Step3OomCancellationResult(
            Step3OomCancellationOutcome.REQUESTED,
            snapshot.project.project_id,
            snapshot.active_step.job_id,
        )
        service = FakeRecoveryService(
            ProjectDiscoveryResult((snapshot,), ()),
            cancellation_result=outcome,
        )
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        dialog.show()

        with patch.object(dialog, "_confirm_step3_oom_kill", return_value=True):
            QTest.mouseClick(dialog._kill_step3, Qt.MouseButton.LeftButton)
            self._wait_until(lambda: not dialog._busy)

        self.assertEqual([call[0] for call in service.calls], ["cancel"])
        self.assertIn("Cancellation requested for Slurm Job 41001", dialog._status.text())
        self.assertTrue(dialog._kill_step3.isHidden())
        dialog._kill_selected_step3()
        self.assertEqual([call[0] for call in service.calls], ["cancel"])
        dialog.close()

    def test_ambiguous_kill_outcome_requires_refresh_and_suppresses_repeat(self):
        snapshot = _step3_running_oom_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        result = Step3OomCancellationResult(
            Step3OomCancellationOutcome.UNKNOWN,
            snapshot.project.project_id,
            snapshot.active_step.job_id,
        )

        dialog._kill_succeeded(result)

        self.assertIn("outcome", dialog._status.text())
        self.assertIn("unknown", dialog._status.text())
        self.assertIn("Refresh Status", dialog._status.text())
        self.assertTrue(dialog._kill_step3.isHidden())
        dialog.reject()

    def test_terminal_cancelled_oom_hides_kill_enables_resource_retry(self):
        snapshot = _step3_oom_snapshot()
        step3 = replace(snapshot.active_step, scheduler_state="CANCELLED")
        project = replace(
            snapshot.project,
            steps=(*snapshot.project.steps[:2], step3, snapshot.project.steps[3]),
        )
        snapshot = replace(snapshot, project=project)
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = CalculationProjectsDialog(
            (TEST_PROFILE,),
            TEST_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            transport_submission_service=object(),
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        self.assertTrue(dialog._kill_step3.isHidden())
        self.assertTrue(dialog._retry_step3.isEnabled())
        self.assertIn("Slurm: CANCELLED", dialog._project_list.item(0).text())
        dialog.reject()

    def test_synthetic_decorated_cancelled_oom_opens_ready_retry_then_cancel(self):
        with tempfile.TemporaryDirectory() as temporary:
            remote = RecoveryRemoteExecutor()
            project, files = _synthetic_cancelled_project_and_files()
            files["aims.dft.out"] = REVIEWED_TASK_OOM_OUTPUT
            _install_phase3b_project(remote, project, files)
            remote.sacct_stdout = SYNTHETIC_DECORATED_CANCELLED_SACCT
            recovery = ProjectRecoveryService(
                FixedConnectionService(remote),
                LocalProjectIndexRepository(
                    Path(temporary) / "known_projects.json"
                ),
                now_factory=lambda: PHASE3B_NOW,
            )

            snapshot = recovery.refresh_project(
                PHASE3B_PROFILE,
                project.remote_project_path,
            )

        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = CalculationProjectsDialog(
            (PHASE3B_PROFILE,),
            PHASE3B_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            transport_submission_service=object(),
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        self.assertEqual(dialog._project_list.count(), 1)
        row = dialog._project_list.item(0).text()
        self.assertIn("Step 3 — FAILED", row)
        self.assertIn("Job 41003", row)
        self.assertIn("Reason: 任务因内存不足终止", row)
        self.assertIn("Slurm: CANCELLED", row)
        self.assertTrue(dialog._kill_step3.isHidden())
        self.assertTrue(dialog._retry_step3.isEnabled())

        inspected = {}

        def inspect_and_reject(retry_dialog):
            preset = snapshot.step3_retry_preset
            self.assertIsNotNone(preset)
            memory = retry_dialog.findChild(QSpinBox, "step3RetryMemoryGb")
            submit = retry_dialog.findChild(QPushButton, "submitStep3Retry")
            self.assertIsNotNone(memory)
            self.assertIsNotNone(submit)
            self.assertEqual(memory.value(), 128)
            self.assertIs(retry_dialog.focusWidget(), memory)
            self.assertTrue(memory.lineEdit().selectedText())
            self.assertEqual(retry_dialog._nodes.value(), preset.nodes)
            self.assertEqual(retry_dialog._ntasks.value(), preset.ntasks)
            self.assertEqual(retry_dialog._cpus.value(), preset.cpus_per_task)
            self.assertEqual(
                retry_dialog._runtime.value(),
                runtime_hours_from_minutes(preset.runtime_minutes),
            )
            self.assertEqual(retry_dialog._memory.value(), preset.memory_gb)
            self.assertEqual(
                retry_dialog._omp.value(),
                preset.omp_num_threads,
            )
            self.assertTrue(submit.isEnabled())
            inspected["ready"] = True
            retry_dialog.reject()
            return QDialog.DialogCode.Rejected

        with patch.object(Step3RetryDialog, "exec", new=inspect_and_reject):
            dialog._retry_selected_step3()

        self.assertEqual(inspected, {"ready": True})
        self.assertEqual(service.calls, [])
        self.assertEqual(dialog._workers, set())
        self.assertEqual(dialog._status.text(), "Step-3 retry was cancelled.")
        dialog.reject()

    def test_ordinary_cancelled_step3_is_not_an_oom_retry_candidate(self):
        snapshot = _step3_oom_snapshot()
        step3 = replace(
            snapshot.active_step,
            scheduler_state="CANCELLED",
            last_error="CANCELLED",
        )
        project = replace(
            snapshot.project,
            steps=(*snapshot.project.steps[:2], step3, snapshot.project.steps[3]),
        )
        snapshot = replace(snapshot, project=project, status_message="CANCELLED")

        self.assertFalse(snapshot.can_retry_step3)

    def test_retry_dialog_cancel_creates_no_worker_or_submission(self):
        snapshot = _step3_oom_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = CalculationProjectsDialog(
            (TEST_PROFILE,),
            TEST_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            transport_submission_service=object(),
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        with patch.object(
            Step3RetryDialog,
            "exec",
            return_value=QDialog.DialogCode.Rejected,
        ):
            dialog._retry_selected_step3()

        self.assertEqual(service.calls, [])
        self.assertEqual(dialog._workers, set())
        self.assertEqual(dialog._status.text(), "Step-3 retry was cancelled.")
        dialog.reject()

    def test_step4_overlap_failure_shows_scientifically_honest_reason(self):
        snapshot = _step4_overlap_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        text = dialog._project_list.item(0).text()
        self.assertIn("Step 4 — FAILED", text)
        self.assertIn("Job 42001", text)
        self.assertIn("Reason: AITRANSS 电极界面区域识别重叠", text)
        self.assertNotIn("左右电极编号设置错误", text)
        self.assertNotIn("左右电极实际重叠", text)
        self.assertEqual(dialog._status.text(), snapshot.status_message)
        dialog.reject()

    def test_step4_self_energy_format_failure_has_distinct_gui_routing(self):
        snapshot = _step4_self_energy_format_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()

        text = dialog._project_list.item(0).text()
        self.assertIn("Step 4 — FAILED", text)
        self.assertIn("Job 42002", text)
        self.assertIn(
            "Reason: AITRANSS 无法读取显式 self-energy 文件",
            text,
        )
        self.assertNotIn("电极界面区域识别重叠", text)
        self.assertFalse(dialog._retry_step4.isEnabled())
        self.assertEqual(dialog._status.text(), snapshot.status_message)
        dialog.reject()

    def test_step4_explicit_retry_control_and_read_only_summary(self):
        snapshot = _step4_retry_ready_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        profile = replace(
            TEST_PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="scientist@example.org",
        )
        projects = CalculationProjectsDialog(
            (profile,),
            profile.profile_id,
            service,
            MemorySecretStore(),
            FakeKnownHosts(),
            transport_submission_service=object(),
        )
        projects._snapshots = (snapshot,)
        projects._render_snapshots()

        self.assertTrue(projects._retry_step4.isEnabled())
        self.assertEqual(
            projects._retry_step4.text(),
            "Retry with explicit self-energy...",
        )
        dialog = Step4ExplicitRetryDialog(snapshot, profile)
        label_text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("opposite electrode", label_text)
        self.assertIn("52", label_text)
        self.assertIn("4", label_text)
        self.assertIn("0.1d0 / 0.05d0 / 0.025d0", label_text)
        self.assertIn("Geometry unchanged — Step 3 results reused", label_text)
        self.assertIn("scientist@example.org", label_text)
        submit = dialog.findChild(QPushButton, "submitStep4ExplicitRetry")
        self.assertEqual(submit.text(), "Submit REAL Step-4 Retry")
        dialog.reject()
        projects.reject()

    def test_refresh_is_async_and_displays_running_job(self):
        snapshot = _snapshot()
        service = FakeRecoveryService(
            ProjectDiscoveryResult((snapshot,), ()),
            block=True,
        )
        dialog = self._dialog(service)
        dialog.show()

        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self.assertTrue(service.started.wait(timeout=1))
        self.assertTrue(dialog._busy)
        self.assertTrue(dialog._refresh.isEnabled())
        self.assertEqual(dialog._refresh.text(), "Stop")
        service.release.set()
        self._wait_until(lambda: not dialog._busy)

        self.assertEqual(dialog._project_list.count(), 1)
        text = dialog._project_list.item(0).text()
        self.assertIn("Step 1 — RUNNING", text)
        self.assertIn("Job 12345", text)
        dialog.close()

    def test_slow_refresh_shows_progress_keeps_close_and_qt_responsive(self):
        snapshot = _snapshot()
        service = FakeRecoveryService(
            ProjectDiscoveryResult((snapshot,), ()),
            block=True,
            progress_messages=(
                "Connecting to ExampleCluster...",
                "Opening remote project workspace...",
            ),
        )
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        dialog.show()

        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self._wait_until(service.started.is_set)
        self._wait_until(
            lambda: dialog._progress_text.text()
            == "Opening remote project workspace..."
        )

        self.assertTrue(dialog._busy_area.isVisible())
        self.assertTrue(dialog._busy_indicator.isVisible())
        self.assertEqual(dialog._busy_indicator.minimum(), 0)
        self.assertEqual(dialog._busy_indicator.maximum(), 0)
        self.assertTrue(dialog._refresh.isEnabled())
        self.assertEqual(dialog._refresh.text(), "Stop")
        self.assertFalse(dialog._refresh_status.isEnabled())
        self.assertFalse(dialog._open.isEnabled())
        self.assertTrue(dialog._close.isEnabled())
        self.assertEqual(dialog._close.text(), "Close (operation continues)")
        self.assertTrue(dialog.has_active_remote_operation())

        heartbeat = threading.Event()
        QTimer.singleShot(0, heartbeat.set)
        self._wait_until(heartbeat.is_set)
        self.assertTrue(dialog.isVisible())

        service.release.set()
        self._wait_until(lambda: not dialog._busy)
        dialog.close()

    def test_server_refresh_button_stops_only_its_status_operation(self):
        listed = _snapshot()
        service = FakeRecoveryService(
            ProjectDiscoveryResult((_snapshot(ProjectStepState.SUCCEEDED),), ()),
            block=True,
            progress_messages=("Reading managed projects...",),
        )
        dialog = self._dialog(service)
        dialog._snapshots = (listed,)
        dialog._render_snapshots()
        dialog.show()

        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self._wait_until(service.started.is_set)
        self.assertEqual(dialog._refresh.text(), "Stop")
        self.assertTrue(dialog._refresh.isEnabled())

        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self.assertFalse(dialog._busy)

        self.assertEqual(dialog._refresh.text(), "Refresh")
        self.assertTrue(dialog._refresh.isEnabled())
        self.assertIn("Server refresh stopped", dialog._status.text())
        self.assertEqual(dialog._project_list.count(), 1)
        self.assertEqual(len(service.calls), 1)
        dialog.close()

    def test_close_while_busy_hides_without_destroying_worker_or_pool(self):
        """Hiding Projects keeps a bounded refresh; it never owns a pool thread."""
        service = FakeRecoveryService(
            ProjectDiscoveryResult((_snapshot(),), ()),
            block=True,
            progress_messages=("Connecting to ExampleCluster...",),
        )
        dialog = self._dialog(service)
        dialog.show()
        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self._wait_until(service.started.is_set)
        worker = next(iter(dialog._workers))
        pool = dialog._thread_pool

        QTest.mouseClick(dialog._close, Qt.MouseButton.LeftButton)
        self.application.processEvents()

        self.assertFalse(dialog.isVisible())
        self.assertTrue(dialog._busy)
        self.assertTrue(dialog.has_active_remote_operation())
        self.assertIn(worker, dialog._workers)
        self.assertIs(dialog._thread_pool, pool)
        self.assertEqual(pool.activeThreadCount(), 0)
        self.assertFalse(service.release.is_set())
        self.assertEqual(dialog._close.text(), "Close (operation continues)")

        service.release.set()
        self._wait_until(lambda: not dialog._busy)
        self.assertNotIn(worker, dialog._workers)
        self._wait_until(lambda: pool.activeThreadCount() == 0)
        self.assertFalse(dialog.has_active_remote_operation())
        self.assertEqual(dialog._close.text(), "Close")

    def test_stop_unblocks_controls_before_network_returns_and_ignores_old_refresh(self):
        listed = _snapshot()
        old_service = FakeRecoveryService(
            ProjectDiscoveryResult((_snapshot(ProjectStepState.SUCCEEDED),), ()),
            block=True, honor_stop=False,
            progress_messages=("Old refresh progress",),
        )
        new_service = FakeRecoveryService(ProjectDiscoveryResult((listed,), ()), block=True)
        dialog = self._dialog(old_service)
        dialog.show()
        try:
            dialog._discover()
            self.assertTrue(old_service.started.wait(timeout=1))
            dialog._refresh_or_stop()
            self.assertFalse(dialog._busy)
            self.assertEqual(dialog._refresh.text(), "Refresh")
            self.assertTrue(dialog._server.isEnabled())
            self.assertFalse(old_service.release.is_set())
            dialog._recovery_service = new_service
            dialog._discover()
            self._wait_until(new_service.started.is_set)
            old_service.release.set()
            QTest.qWait(50)
            self.application.processEvents()
            self.assertTrue(dialog._busy)
            self.assertEqual(dialog._refresh.text(), "Stop")
            self.assertEqual(dialog._project_list.count(), 0)
            self.assertNotEqual(dialog._progress_text.text(), "Old refresh progress")
            new_service.release.set()
            self._wait_until(lambda: not dialog._busy)
            self.assertEqual(dialog._snapshots, (listed,))
        finally:
            old_service.release.set()
            new_service.release.set()
            dialog.stop_status_refresh()
            dialog.close()

    def test_full_and_selected_refresh_deadlines_retain_existing_task_state(self):
        listed = _snapshot()
        for selected in (False, True):
            with self.subTest(selected=selected):
                service = FakeRecoveryService(
                    ProjectDiscoveryResult((), ()),
                    refreshed=_snapshot(ProjectStepState.SUCCEEDED),
                    block=True, honor_stop=False,
                )
                dialog = self._dialog(service)
                dialog._snapshots = (listed,)
                dialog._render_snapshots()
                dialog._select_project_id(listed.project.project_id)
                dialog.show()
                try:
                    with patch(
                        "moltage.gui.projects_dialog.StatusRefreshSession",
                        side_effect=lambda operation, parent: StatusRefreshSession(operation, parent, timeout_ms=50),
                    ), patch.object(QMessageBox, "critical") as critical:
                        if selected:
                            dialog._refresh_selected()
                        else:
                            dialog._discover()
                        self._wait_until(service.started.is_set)
                    self._wait_until(lambda: not dialog._busy)
                    critical.assert_not_called()
                    self.assertIn("Status refresh timed out", dialog._status.text())
                    self.assertEqual(dialog._snapshots, (listed,))
                    self.assertEqual(dialog._refresh.text(), "Refresh")
                    self.assertTrue(dialog._refresh_status.isEnabled())
                    self.assertFalse(service.release.is_set())
                finally:
                    service.release.set()
                    dialog.stop_status_refresh()
                    dialog.close()

    def test_reopen_while_busy_retains_same_worker_and_rejects_second_refresh(self):
        service = FakeRecoveryService(
            ProjectDiscoveryResult((_snapshot(),), ()),
            block=True,
            progress_messages=("Checking Slurm status...",),
        )
        dialog = self._dialog(service)
        dialog.show()
        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self._wait_until(service.started.is_set)
        self._wait_until(
            lambda: dialog._progress_text.text() == "Checking Slurm status..."
        )
        worker = next(iter(dialog._workers))

        dialog.reject()
        self.application.processEvents()
        same_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.application.processEvents()
        dialog._discover()

        self.assertIs(dialog, same_dialog)
        self.assertEqual(dialog._workers, {worker})
        self.assertEqual(len(service.calls), 1)
        self.assertTrue(dialog._busy_area.isVisible())
        self.assertEqual(
            dialog._progress_text.text(),
            "Checking Slurm status...",
        )

        service.release.set()
        self._wait_until(lambda: not dialog._busy)
        dialog.close()

    def test_hidden_success_is_retained_without_popup(self):
        snapshot = _snapshot()
        service = FakeRecoveryService(
            ProjectDiscoveryResult((snapshot,), ()),
            block=True,
        )
        dialog = self._dialog(service)
        dialog.show()

        with patch.object(QMessageBox, "critical") as critical:
            QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
            self._wait_until(service.started.is_set)
            dialog.reject()
            self.application.processEvents()
            service.release.set()
            self._wait_until(lambda: not dialog._busy)

        critical.assert_not_called()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog._project_list.count(), 1)
        self.assertIn("Discovered 1 managed project", dialog._status.text())
        self.assertFalse(dialog._busy_area.isVisible())
        self.assertTrue(dialog._refresh.isEnabled())

        dialog.show()
        self.application.processEvents()
        self.assertTrue(dialog.isVisible())
        self.assertIn("Step 1 — RUNNING", dialog._project_list.item(0).text())
        dialog.close()

    def test_hidden_failure_is_retained_without_modal_error(self):
        service = FakeRecoveryService(
            ProjectDiscoveryResult((), ()),
            block=True,
            failure=ProjectRecoveryError("synthetic recovery failure"),
        )
        dialog = self._dialog(service)
        dialog.show()

        with patch.object(QMessageBox, "critical") as critical:
            QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
            self._wait_until(service.started.is_set)
            dialog.reject()
            self.application.processEvents()
            service.release.set()
            self._wait_until(lambda: not dialog._busy)

        critical.assert_not_called()
        self.assertFalse(dialog.isVisible())
        self.assertIn("Project recovery failed", dialog._status.text())
        self.assertIn("synthetic recovery failure", dialog._status.text())
        self.assertFalse(dialog._busy_area.isVisible())
        self.assertTrue(dialog._refresh.isEnabled())

        dialog.show()
        self.application.processEvents()
        self.assertIn("synthetic recovery failure", dialog._status.text())
        dialog.close()

    def test_visible_success_clears_stale_progress_and_restores_controls(self):
        snapshot = _snapshot()
        service = FakeRecoveryService(
            ProjectDiscoveryResult((snapshot,), ()),
            block=True,
            progress_messages=(
                "Connecting to ExampleCluster...",
                "Opening remote project workspace...",
                "Checking Slurm status...",
            ),
        )
        dialog = self._dialog(service)
        dialog.show()
        QTest.mouseClick(dialog._refresh, Qt.MouseButton.LeftButton)
        self._wait_until(service.started.is_set)
        self._wait_until(
            lambda: dialog._progress_text.text() == "Checking Slurm status..."
        )

        service.release.set()
        self._wait_until(lambda: not dialog._busy)

        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog._project_list.count(), 1)
        self.assertIn("Discovered 1 managed project", dialog._status.text())
        self.assertFalse(dialog._busy_area.isVisible())
        self.assertEqual(dialog._progress_text.text(), "")
        self.assertTrue(dialog._refresh.isEnabled())
        self.assertTrue(dialog._close.isEnabled())
        self.assertEqual(dialog._close.text(), "Close")
        dialog.close()

    def test_open_refreshes_authoritative_project_then_returns_structure(self):
        listed = _snapshot()
        recovered = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        service = FakeRecoveryService(
            ProjectDiscoveryResult((listed,), ()),
            refreshed=recovered,
        )
        dialog = self._dialog(service)
        dialog._snapshots = (listed,)
        dialog._render_snapshots()
        dialog.show()

        QTest.mouseClick(dialog._open, Qt.MouseButton.LeftButton)
        self._wait_until(lambda: not dialog._busy)

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        selected, selected_profile = dialog.selected_recovery()
        self.assertIs(selected, recovered)
        self.assertIs(selected_profile, TEST_PROFILE)
        self.assertEqual(service.calls[0][0], "refresh")

    def test_status_indicator_geometry_menu_matches_step_state_exactly(self):
        successful = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        service = FakeRecoveryService(ProjectDiscoveryResult((successful,), ()))
        dialog = self._dialog(service)

        step1 = dialog._step_geometry_menu(
            successful,
            ProjectStepKind.MOLECULE_OPT,
        )
        self.assertEqual(
            [action.text() for action in step1.actions()],
            ["View Input Geometry", "View Output Geometry"],
        )
        self.assertEqual(
            [ProjectGeometryViewKind(action.data()) for action in step1.actions()],
            [ProjectGeometryViewKind.INPUT, ProjectGeometryViewKind.OUTPUT],
        )

        incomplete = _snapshot(ProjectStepState.RUNNING)
        step1_running = dialog._step_geometry_menu(
            incomplete,
            ProjectStepKind.MOLECULE_OPT,
        )
        self.assertEqual(
            [action.text() for action in step1_running.actions()],
            ["View Input Geometry"],
        )
        step2_not_started = dialog._step_geometry_menu(
            incomplete,
            ProjectStepKind.MOLECULE_AU_OPT,
        )
        self.assertEqual(
            [action.text() for action in step2_not_started.actions()],
            ["No geometry available"],
        )
        self.assertFalse(step2_not_started.actions()[0].isEnabled())
        dialog.close()

    def test_step3_and_step4_status_lights_offer_only_view_geometry(self):
        snapshot = _managed_snapshot(
            91,
            "transport-project",
            NOW,
            active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            active_state=ProjectStepState.RUNNING,
        )
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        for step_kind in (
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            ProjectStepKind.TRANSMISSION,
        ):
            menu = dialog._step_geometry_menu(snapshot, step_kind)
            self.assertEqual(
                [action.text() for action in menu.actions()],
                ["View Geometry"],
            )
            self.assertIs(
                ProjectGeometryViewKind(menu.actions()[0].data()),
                ProjectGeometryViewKind.TRANSPORT,
            )
        dialog.close()

    def test_indicator_click_routes_exact_step_in_all_three_views(self):
        snapshot = _snapshot(ProjectStepState.RUNNING)
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        dialog.resize(830, 500)
        dialog.show()
        self.application.processEvents()

        with patch.object(dialog, "_show_step_geometry_menu") as popup:
            for mode in ProjectViewMode:
                dialog._view_mode = mode
                dialog._configure_project_view()
                self.application.processEvents()
                item = dialog._project_list.item(0)
                rectangle = dialog._project_list.visualItemRect(item)
                step2_rect = dialog._project_delegate.indicator_rects(
                    rectangle,
                    mode,
                )[1]
                QTest.mouseClick(
                    dialog._project_list.viewport(),
                    Qt.MouseButton.LeftButton,
                    pos=step2_rect.center(),
                )
                self.application.processEvents()
                self.assertIs(
                    popup.call_args.args[1],
                    ProjectStepKind.MOLECULE_AU_OPT,
                )
        self.assertEqual(popup.call_count, 3)
        dialog.close()

    def test_right_click_routes_exact_task_light_without_changing_left_click(self):
        snapshot = _snapshot(ProjectStepState.RUNNING)
        task_service = FakeTaskRestartService()
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(
            service,
            task_restart_service=task_service,
        )
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        dialog.resize(830, 500)
        dialog.show()
        self.application.processEvents()

        with (
            patch.object(dialog, "_show_step_task_menu") as task_popup,
            patch.object(dialog, "_show_step_geometry_menu") as geometry_popup,
        ):
            item = dialog._project_list.item(0)
            rectangle = dialog._project_list.visualItemRect(item)
            step1_rect = dialog._project_delegate.indicator_rects(
                rectangle,
                dialog._view_mode,
            )[0]
            QTest.mouseClick(
                dialog._project_list.viewport(),
                Qt.MouseButton.RightButton,
                pos=step1_rect.center(),
            )
            self.application.processEvents()
            task_popup.assert_called_once()
            self.assertIs(
                task_popup.call_args.args[1],
                ProjectStepKind.MOLECULE_OPT,
            )
            geometry_popup.assert_not_called()

            QTest.mouseClick(
                dialog._project_list.viewport(),
                Qt.MouseButton.LeftButton,
                pos=step1_rect.center(),
            )
            self.application.processEvents()
            geometry_popup.assert_called_once()
        dialog.close()

    def test_task_menu_enables_only_exact_current_queued_or_running_step(self):
        running = _snapshot(ProjectStepState.RUNNING)
        service = FakeRecoveryService(ProjectDiscoveryResult((running,), ()))
        dialog = self._dialog(
            service,
            task_restart_service=FakeTaskRestartService(),
        )

        current = dialog._step_task_menu(
            running,
            ProjectStepKind.MOLECULE_OPT,
        ).actions()[0]
        future = dialog._step_task_menu(
            running,
            ProjectStepKind.MOLECULE_AU_OPT,
        ).actions()[0]
        completed = dialog._step_task_menu(
            _snapshot(ProjectStepState.SUCCEEDED),
            ProjectStepKind.MOLECULE_OPT,
        ).actions()[0]
        queued = dialog._step_task_menu(
            _snapshot(ProjectStepState.QUEUED),
            ProjectStepKind.MOLECULE_OPT,
        ).actions()[0]

        self.assertEqual(current.text(), "Abort Task and Create Restart Draft...")
        self.assertTrue(current.isEnabled())
        self.assertFalse(future.isEnabled())
        self.assertFalse(completed.isEnabled())
        self.assertTrue(queued.isEnabled())
        dialog.close()

    def test_cancel_confirmation_starts_no_credentials_or_remote_worker(self):
        snapshot = _snapshot(ProjectStepState.RUNNING)
        task_service = FakeTaskRestartService()
        dialog = self._dialog(
            FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ())),
            task_restart_service=task_service,
        )
        with (
            patch.object(dialog, "_confirm_task_restart", return_value=False),
            patch.object(dialog, "_password_for") as password,
        ):
            dialog._request_task_restart(
                snapshot,
                ProjectStepKind.MOLECULE_OPT,
            )

        password.assert_not_called()
        self.assertEqual(task_service.calls, [])
        self.assertFalse(dialog._workers)
        dialog.close()

    def test_known_cancellation_emits_locked_draft_but_unknown_never_does(self):
        snapshot = _snapshot(ProjectStepState.RUNNING)
        draft = _restart_draft(snapshot)
        dialog = self._dialog(
            FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ())),
            task_restart_service=FakeTaskRestartService(),
        )
        emitted = []
        dialog.restart_draft_requested.connect(emitted.append)

        dialog._task_restart_succeeded(
            ProjectTaskRestartResult(
                ProjectTaskCancellationOutcome.REQUESTED,
                snapshot.project.project_id,
                snapshot.active_step_kind,
                snapshot.active_step.job_id,
                draft,
            )
        )

        self.assertEqual(emitted, [draft])
        self.assertFalse(emitted[0].source_terminal_confirmed)
        self.assertIn("submission remains locked", dialog._status.text())

        dialog._task_restart_succeeded(
            ProjectTaskRestartResult(
                ProjectTaskCancellationOutcome.UNKNOWN,
                snapshot.project.project_id,
                snapshot.active_step_kind,
                snapshot.active_step.job_id,
            )
        )
        self.assertEqual(emitted, [draft])
        self.assertIn("No restart draft was opened", dialog._status.text())
        dialog.close()

    def test_geometry_view_runs_async_and_emits_read_only_workspace_result(self):
        snapshot = _snapshot(ProjectStepState.RUNNING)
        structure = MolecularStructure((Atom(0, "C", 0.0, 0.0, 0.0),))
        result = ProjectGeometryViewResult(
            snapshot.project.project_id,
            snapshot.project.remote_directory_name,
            ProjectStepKind.MOLECULE_OPT,
            ProjectGeometryViewKind.INPUT,
            "geometry.in",
            structure,
            Connectivity(1, ()),
        )
        geometry_service = FakeProjectGeometryService(result)
        service = FakeRecoveryService(ProjectDiscoveryResult((snapshot,), ()))
        dialog = self._dialog(service, geometry_service=geometry_service)
        dialog._snapshots = (snapshot,)
        dialog._render_snapshots()
        dialog.show()
        emitted = []
        dialog.geometry_workspace_requested.connect(emitted.append)

        dialog._request_step_geometry(
            snapshot,
            ProjectStepKind.MOLECULE_OPT,
            ProjectGeometryViewKind.INPUT,
        )
        self._wait_until(lambda: not dialog._busy)

        self.assertEqual(emitted, [result])
        self.assertEqual(len(geometry_service.calls), 1)
        request = geometry_service.calls[0]
        self.assertIs(request.view_kind, ProjectGeometryViewKind.INPUT)
        self.assertEqual(request.supplied_password, "temporary-secret")
        self.assertIn("Opened read-only geometry.in", dialog._status.text())
        self.assertEqual(dialog.result(), 0)
        dialog.close()

    def test_view_transmission_emits_contextual_workspace_request(self):
        running = _snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((running,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (running,)
        dialog._render_snapshots()
        dialog.show()
        self.application.processEvents()
        self.assertFalse(dialog._view_transmission.isEnabled())

        successful = _step4_success_snapshot()
        dialog._snapshots = (successful,)
        dialog._render_snapshots()
        self.application.processEvents()
        self.assertTrue(dialog._view_transmission.isEnabled())
        requests = []
        dialog.transmission_workspace_requested.connect(requests.append)

        QTest.mouseClick(
            dialog._view_transmission,
            Qt.MouseButton.LeftButton,
        )
        self.application.processEvents()
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertIsInstance(request, TransmissionWorkspaceRequest)
        self.assertEqual(request.identity.project_id, successful.project.project_id)
        self.assertEqual(request.job_id, successful.active_step.job_id)
        self.assertEqual(request.result_filename, "TE.dat")
        self.assertIs(request.result, successful.transmission_result)
        self.assertEqual(
            request.display_title,
            f"{successful.project.remote_directory_name} — Transmission",
        )

        view = TransmissionView(
            request.project_name,
            request.job_id,
            request.result_filename,
            request.result,
        )
        self.assertEqual(
            view._energy_axis.titleText(),
            "Energy − E<sub>F</sub> (eV)",
        )
        self.assertEqual(
            view._transmission_axis.titleText(),
            "Transmission T(E)",
        )
        self.assertEqual(view._series.count(), 4)

        QTest.mouseClick(
            dialog._view_transmission,
            Qt.MouseButton.LeftButton,
        )
        self.application.processEvents()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        view.close()
        view.deleteLater()
        dialog.close()

    def test_plot_only_failure_does_not_downgrade_scientific_success(self):
        successful = _step4_success_snapshot()
        service = FakeRecoveryService(ProjectDiscoveryResult((successful,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (successful,)
        dialog._render_snapshots()
        dialog.show()

        with patch.object(QMessageBox, "critical") as critical:
            dialog.show_transmission_view_error(
                RuntimeError("synthetic chart failure")
            )

        self.assertIs(
            successful.active_step.state,
            ProjectStepState.SUCCEEDED,
        )
        self.assertIn("Transmission view failed", dialog._status.text())
        self.assertIn("synthetic chart failure", dialog._status.text())
        critical.assert_called_once_with(
            dialog,
            "Transmission view failed",
            "synthetic chart failure",
        )
        dialog.close()

    def test_missing_log_range_is_a_presentation_failure_only(self):
        successful = _step4_success_snapshot()
        assert successful.transmission_result is not None
        result = replace(
            successful.transmission_result,
            points=(
                TransmissionPoint(-0.20, -3.0, 0.10),
                TransmissionPoint(-0.19, -1.0, 0.00),
                TransmissionPoint(-0.18, 1.0, -0.10),
                TransmissionPoint(-0.17, 3.0, 0.20),
            ),
        )
        successful = replace(successful, transmission_result=result)
        service = FakeRecoveryService(ProjectDiscoveryResult((successful,), ()))
        dialog = self._dialog(service)
        dialog._snapshots = (successful,)
        dialog._render_snapshots()
        dialog.show()

        def open_requested_view(request):
            try:
                TransmissionView(
                    request.project_name,
                    request.job_id,
                    request.result_filename,
                    request.result,
                )
            except Exception as error:
                dialog.show_transmission_view_error(error)

        dialog.transmission_workspace_requested.connect(open_requested_view)

        with patch.object(QMessageBox, "critical") as critical:
            QTest.mouseClick(
                dialog._view_transmission,
                Qt.MouseButton.LeftButton,
            )
            self.application.processEvents()

        self.assertIs(
            successful.active_step.state,
            ProjectStepState.SUCCEEDED,
        )
        self.assertIn("Transmission view failed", dialog._status.text())
        self.assertIn("No positive finite transmission", dialog._status.text())
        critical.assert_called_once()
        dialog.close()

    def test_rebind_confirmation_is_explicit_and_local_only(self):
        old_identity = replace(
            _snapshot(),
            requires_profile_rebind=True,
        )
        refreshed = replace(
            old_identity,
            requires_profile_rebind=False,
            profile_rebind_confirmed=True,
        )
        service = FakeRecoveryService(
            ProjectDiscoveryResult((old_identity,), ()),
            refreshed=refreshed,
        )
        dialog = self._dialog(service)
        dialog._snapshots = (old_identity,)
        dialog._render_snapshots()
        historical_id = old_identity.project.server_profile_id

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog._refresh_selected()
            self._wait_until(lambda: not dialog._busy)

        self.assertTrue(service.calls[0][3])
        self.assertEqual(old_identity.project.server_profile_id, historical_id)

        with patch.object(QMessageBox, "question") as question:
            dialog._refresh_selected()
            self._wait_until(lambda: not dialog._busy)

        question.assert_not_called()
        self.assertTrue(service.calls[1][3])

    def test_error_routing_keeps_transport_distinct_from_slurm_absence(self):
        presentation = recovery_error_presentation(
            RemoteConnectionError("transport lost")
        )
        self.assertEqual(presentation.title, "Connection failed")
        self.assertNotIn("Slurm not detected", presentation.message)
        self.assertNotIn("Slurm configuration invalid", presentation.message)

    def test_list_text_for_succeeded_step2_exposes_no_step3_action(self):
        project = _project(
            ProjectStepState.SUCCEEDED,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        snapshot = ProjectRecoverySnapshot(
            project,
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
        )
        text = project_list_text(snapshot)
        self.assertIn("Step 2 — SUCCEEDED", text)
        self.assertNotIn("Step 3", text)

    def test_step2_confirmation_keeps_identity_and_password_memory_only(self):
        snapshot = _snapshot(ProjectStepState.SUCCEEDED, optimized=True)
        enabled_profile = replace(
            TEST_PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        dialog = Step2ContinuationConfirmationDialog(
            snapshot,
            enabled_profile,
            AimsOptimizationSettings(),
            temporary_password_required=True,
        )

        self.assertIn(
            snapshot.project.remote_directory_name,
            tuple(label.text() for label in dialog.findChildren(QLabel)),
        )
        self.assertTrue(
            any(
                label.text().endswith("/molecule_Au")
                for label in dialog.findChildren(QLabel)
            )
        )
        self.assertEqual(
            dialog._password.echoMode(),
            dialog._password.EchoMode.Password,
        )
        dialog._password.setText("temporary-secret")
        self.assertEqual(dialog.take_temporary_password(), "temporary-secret")
        self.assertEqual(dialog._password.text(), "")
        self.assertEqual(
            dialog.findChild(QLabel, "step2EmailSummary").text(),
            "user@example.com",
        )


if __name__ == "__main__":
    unittest.main()
