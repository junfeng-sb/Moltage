"""Qt presentation tests for verified ORCA WBL curves."""

from dataclasses import replace
from time import monotonic
from unittest.mock import patch

import pytest
from PySide6.QtCharts import QLogValueAxis
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QWidget,
)

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_wbl import OrcaWblRequest, OrcaWblService
from moltage.app.project_planning import create_initial_project
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
    begin_orca_wbl_step,
    append_orca_wbl_step,
)
from moltage.gui.orca_wbl_view import OrcaWblTransmissionView
from moltage.gui.projects_dialog import CalculationProjectsDialog
from moltage.app.project_presentation import StepIndicatorKind
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.orca.wbl import OrcaWblResultEvidence, WblSpinTreatment
from moltage.orca.wbl_artifacts import OrcaWblPresentation
from phase2b1_test_support import MemorySecretStore
from test_orca_submission_recovery import NOW, configured_profile
from test_orca_wbl_workflow import WblRemoteExecutor, _optimized_project, _settings
from test_project_submission import FixedConnectionService
from test_project_recovery import _project


@pytest.fixture
def wbl_context(tmp_path):
    profile = configured_profile()
    index = LocalProjectIndexRepository(tmp_path / "known_projects.json")
    remote = WblRemoteExecutor()
    optimized, _ = _optimized_project(remote, index)
    service = OrcaWblService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: optimized.project.updated_at,
        temporary_id_factory=lambda: "synthetic-wbl-gui",
    )
    return profile, optimized, service


def _completed_wbl(context):
    profile, optimized, service = context
    return service.calculate(
        OrcaWblRequest(
            profile, optimized.project.remote_project_path, _settings(),
            "synthetic-password",
        )
    )


def _projects_dialog(profile, snapshot, service=None):
    dialog = CalculationProjectsDialog(
        (profile,), profile.profile_id, object(), MemorySecretStore(), object(),
        orca_wbl_service=service,
    )
    dialog._snapshots = (snapshot,)
    dialog._render_snapshots()
    return dialog


def test_wbl_worker_completion_updates_second_light_in_all_project_views(wbl_context):
    application = QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    dialog = _projects_dialog(profile, optimized, service)
    updates = []
    requests = []
    operation_statuses = []
    dialog.project_snapshot_updated.connect(updates.append)
    dialog.orca_wbl_workspace_requested.connect(requests.append)
    dialog.orca_wbl_operation_status.connect(
        lambda project_id, message: operation_statuses.append((project_id, message))
    )
    try:
        with (
            patch("moltage.gui.projects_dialog.OrcaWblSettingsDialog") as settings_dialog,
            patch.object(
                QMessageBox,
                "question",
                side_effect=AssertionError("WBL must not show a second confirmation"),
            ),
            patch.object(QMessageBox, "information") as started_message,
            patch.object(dialog, "_password_for", return_value=(True, "synthetic-password")),
        ):
            settings_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            settings_dialog.return_value.selected_settings.return_value = _settings()
            dialog.calculate_orca_wbl(optimized, profile)
            deadline = monotonic() + 5.0
            while dialog._workers and monotonic() < deadline:
                QTest.qWait(10)
            assert not dialog._workers, "Synthetic WBL worker did not finish"
        started_message.assert_called_once()
        assert started_message.call_args.args[1] == "ORCA Step 2 started"
        application.processEvents()

        assert any(snapshot.active_step.state is ProjectStepState.RUNNING for snapshot in updates)
        assert updates[-1].active_step.state is ProjectStepState.SUCCEEDED
        assert updates[-1].orca_wbl_presentation is not None
        assert updates[-1].can_view_orca_wbl
        assert dialog._view_orca_wbl.isEnabled()
        for index in range(dialog._view.count()):
            dialog._view.setCurrentIndex(index)
            record = dialog._records[0]
            assert len(record.indicators) == 2
            assert record.indicators[1].kind is StepIndicatorKind.SUCCEEDED
            assert "ORCA WBL transmission" in dialog._project_list.item(0).text()
        assert len(requests) == 1
        assert requests[0].identity.project_id == optimized.project.project_id
        assert operation_statuses
        assert all(
            project_id == optimized.project.project_id
            for project_id, _message in operation_statuses
        )
        assert any("Preparing" in message for _project_id, message in operation_statuses)
        assert any("running" in message for _project_id, message in operation_statuses)
        assert any("completed" in message for _project_id, message in operation_statuses)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_hidden_project_manager_uses_visible_main_window_for_wbl_prompts(
    wbl_context,
):
    application = QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    main_window = QWidget()
    main_window.show()
    dialog = CalculationProjectsDialog(
        (profile,),
        profile.profile_id,
        object(),
        MemorySecretStore(),
        object(),
        main_window,
        orca_wbl_service=service,
    )
    dialog._snapshots = (optimized,)
    dialog._render_snapshots()
    assert not dialog.isVisible()
    assert main_window.isVisible()

    try:
        with (
            patch("moltage.gui.projects_dialog.OrcaWblSettingsDialog") as settings_dialog,
            patch.object(
                QMessageBox,
                "question",
                side_effect=AssertionError("WBL must not show a second confirmation"),
            ),
            patch.object(QMessageBox, "information") as started_message,
            patch.object(dialog, "_password_for", return_value=(True, "synthetic-password")),
            patch.object(dialog._thread_pool, "start") as start_worker,
        ):
            settings_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            settings_dialog.return_value.selected_settings.return_value = _settings()

            dialog.calculate_orca_wbl(optimized, profile)

        assert settings_dialog.call_args.args[2] is main_window
        assert started_message.call_args.args[0] is main_window
        assert start_worker.call_count == 1
        assert len(dialog._workers) == 1
        dialog._workers.clear()
        dialog._busy = False

        with patch.object(
            QInputDialog,
            "getText",
            return_value=("", False),
        ) as password_prompt:
            assert dialog._password_for(replace(profile, save_password=False)) == (
                False,
                None,
            )
        assert password_prompt.call_args.args[0] is main_window
    finally:
        dialog.close()
        dialog.deleteLater()
        main_window.close()
        main_window.deleteLater()


def test_wbl_failure_publishes_geometry_workspace_feedback(wbl_context):
    profile, optimized, _service = wbl_context
    dialog = _projects_dialog(profile, optimized)
    dialog._pending_orca_wbl_snapshot = optimized
    statuses = []
    dialog.orca_wbl_operation_status.connect(
        lambda project_id, message: statuses.append((project_id, message))
    )
    try:
        with patch.object(dialog, "_show_error") as show_error:
            dialog._orca_wbl_failed(RuntimeError("synthetic WBL failure"))

        show_error.assert_called_once()
        assert statuses == [
            (
                optimized.project.project_id,
                "Project recovery failed: synthetic WBL failure",
            )
        ]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_verified_wbl_success_lights_second_indicator_even_if_presentation_fails(wbl_context):
    application = QApplication.instance() or QApplication([])
    profile, optimized, _ = wbl_context
    result = _completed_wbl(wbl_context)
    dialog = _projects_dialog(profile, optimized)
    dialog._pending_orca_wbl_snapshot = optimized
    dialog._pending_orca_wbl_profile = profile
    requests = []
    dialog.orca_wbl_workspace_requested.connect(requests.append)
    try:
        with (
            patch(
                "moltage.gui.projects_dialog.wbl_presentation_from_result",
                side_effect=ValueError("synthetic presentation failure"),
            ),
            patch.object(dialog, "_show_error") as show_error,
        ):
            dialog._orca_wbl_succeeded(result)
        application.processEvents()

        assert dialog._records[0].indicators[1].kind is StepIndicatorKind.SUCCEEDED
        assert dialog._current_snapshot().project == result.project
        assert dialog._pending_orca_wbl_snapshot.project == result.project
        assert not dialog._current_snapshot().can_view_orca_wbl
        assert not dialog._view_orca_wbl.isEnabled()
        assert not requests
        assert show_error.call_count == 1
        assert "completed" in str(show_error.call_args.args[0]).lower()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_orca_footer_hides_fhi_actions_and_restores_them_for_fhi_project(wbl_context):
    application = QApplication.instance() or QApplication([])
    profile, optimized, _ = wbl_context
    completed = _completed_wbl(wbl_context)
    running = replace(
        optimized,
        project=begin_orca_wbl_step(
            optimized.project, settings=_settings(), started_at=optimized.project.updated_at,
        ),
        active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
    )
    succeeded = replace(
        optimized, project=completed.project,
        active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
    )
    dialog = _projects_dialog(profile, optimized)
    try:
        for snapshot in (optimized, running, succeeded):
            dialog._snapshots = (snapshot,)
            dialog._render_snapshots()
            for button in (
                dialog._retry_step3, dialog._retry_step4,
                dialog._kill_step3, dialog._view_transmission,
            ):
                assert button.isHidden(), button.text()
            assert not dialog._open.isHidden()
            assert not dialog._refresh_status.isHidden()
            assert not dialog._cancel_orca.isHidden()
            assert not snapshot.can_view_orca_wbl
            assert not dialog._view_orca_wbl.isEnabled()

        dialog._snapshots = (
            ProjectRecoverySnapshot(
                _project(ProjectStepState.SUCCEEDED),
                ProjectStepKind.MOLECULE_OPT,
                "Synthetic FHI optimization complete.",
            ),
        )
        dialog._render_snapshots()
        application.processEvents()
        assert not dialog._retry_step3.isHidden()
        assert not dialog._retry_step4.isHidden()
        assert not dialog._view_transmission.isHidden()
        assert dialog._cancel_orca.isHidden()
    finally:
        dialog.close()
        dialog.deleteLater()


def _presentation() -> OrcaWblPresentation:
    return OrcaWblPresentation(
        "MOLtage_ORCA_LINKER_WBL_V1",
        "HYPOTHESIS",
        (-1.0, 0.0, 1.0),
        (-6.0, -5.0, -4.0),
        (0.01, 0.02, 0.03),
        (0.02, 0.01, 0.04),
        (0.03, 0.03, 0.07),
        0.02,
        0.01,
        0.03,
        ((2, 0.012), (1, 0.008)),
        ((1, 0.006), (2, 0.004)),
        (("orca_opt.gbw", "a" * 64),),
    )


def _closed_shell_presentation() -> OrcaWblPresentation:
    return OrcaWblPresentation(
        "MOLtage_ORCA_LINKER_WBL_V1",
        "HYPOTHESIS",
        (-1.0, 0.0, 1.0),
        (-6.0, -5.0, -4.0),
        (),
        (),
        (0.01, 1.0, 0.03),
        None,
        None,
        1.0,
        (),
        (),
        (("orca_opt.gbw", "a" * 64),),
        WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE,
        ((2, 1.0), (1, 0.01)),
    )


def test_wbl_view_displays_three_spin_curves_and_exports_current_canvas():
    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("SyntheticWbl.20300103", _presentation())
    view.resize(900, 620)
    view.show()
    application.processEvents()

    assert view._alpha.count() == 3
    assert view._beta.count() == 3
    assert view._total.count() == 3
    assert view._alpha.name() == "Alpha"
    assert view._beta.name() == "Beta"
    assert view._total.name() == "Spin sum (Alpha + Beta)"
    assert isinstance(view._y_axis, QLogValueAxis)
    assert view._y_axis.base() == 10.0
    assert view._y_axis.min() > 0.0
    application.processEvents()
    tick_texts = tuple(
        label.text() for label in view._chart_view.power_tick_labels
    )
    assert tick_texts
    assert all(text.startswith("10") and "E" not in text for text in tick_texts)
    assert "G/G₀ = (Tα + Tβ)/2" in view.findChild(
        QLabel, "orcaWblLimitation"
    ).text()
    image = view.capture_image(1)
    assert not image.isNull()
    assert image.width() > 0
    assert image.height() > 0

    view.close()
    view.deleteLater()


def test_closed_shell_wbl_view_displays_only_spin_degenerate_total():
    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView(
        "SyntheticClosedShell.20300103",
        _closed_shell_presentation(),
    )
    view.resize(900, 620)
    view.show()
    application.processEvents()

    assert view._alpha is None
    assert view._beta is None
    assert view._total.count() == 3
    assert view._total.name() == "Total transmission (spin-degenerate)"
    summary = view.findChild(QLabel, "orcaWblFermiSummary").text()
    assert "closed-shell, spin-degenerate" in summary
    assert "T<sub>α</sub>" not in summary
    limitation = view.findChild(QLabel, "orcaWblLimitation").text()
    assert "calculated once from the spatial orbitals" in limitation
    assert "bounded by T = 1" in limitation

    view.close()
    view.deleteLater()


def test_second_orca_indicator_opens_verified_transmission_not_geometry():
    application = QApplication.instance() or QApplication([])
    profile = configured_profile()
    project = create_initial_project(
        base_name="SyntheticWblView",
        remote_directory_name="SyntheticWblView.20300102",
        source_molecule_name="synthetic.xyz",
        server_profile_id=profile.profile_id,
        remote_project_root=profile.remote_project_root,
        starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
        workflow_kind=CalculationWorkflowKind.ORCA,
        now=NOW,
    )
    project = replace(
        project,
        steps=(
            replace(
                project.steps[0],
                state=ProjectStepState.SUCCEEDED,
                orca_optimization_result=OrcaOptimizationResultEvidence(
                    True, True, True, True, True, gbw_sha256="a" * 64,
                ),
            ),
        ),
    )
    presentation = _presentation()
    project = append_orca_wbl_step(
        project,
        settings=_settings(),
        result=OrcaWblResultEvidence(
            presentation.model_id,
            presentation.model_classification,
            "a" * 64,
            "b" * 64,
            (("orca_wbl_result.json", "c" * 64),),
            "/apps/example/orca-6.1/orca_2json",
            presentation.t_alpha_at_fermi,
            presentation.t_beta_at_fermi,
            presentation.t_total_at_fermi,
            presentation.top_alpha,
            presentation.top_beta,
        ),
        input_hashes=(),
        finished_at=NOW,
    )
    snapshot = ProjectRecoverySnapshot(
        project,
        ProjectStepKind.ORCA_WBL_TRANSMISSION,
        "Synthetic WBL complete.",
        orca_wbl_presentation=presentation,
    )
    assert snapshot.can_view_orca_wbl
    dialog = CalculationProjectsDialog(
        (profile,), profile.profile_id, object(), MemorySecretStore(), object(),
    )
    requests = []
    dialog.orca_wbl_workspace_requested.connect(requests.append)
    dialog._snapshots = (snapshot,)
    dialog._render_snapshots()

    assert not dialog._view_orca_wbl.isHidden()
    assert dialog._view_orca_wbl.isEnabled()
    dialog._view_orca_wbl.click()
    application.processEvents()
    assert len(requests) == 1

    menu = dialog._step_geometry_menu(snapshot, ProjectStepKind.ORCA_WBL_TRANSMISSION)
    actions = menu.actions()
    assert len(actions) == 1
    assert actions[0].text() == "View Transmission"
    assert actions[0].isEnabled()
    actions[0].trigger()
    application.processEvents()

    assert len(requests) == 2
    assert all(request.presentation == presentation for request in requests)
    assert all(request.identity.project_id == project.project_id for request in requests)
    dialog.close()
    dialog.deleteLater()
