"""Qt presentation tests for verified ORCA WBL curves."""

from dataclasses import replace
from time import monotonic
from unittest.mock import patch

import pytest
from PySide6.QtCharts import QLogValueAxis
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
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
from moltage.gui.transmission_settings import TransmissionSettingsDialog
from moltage.gui.projects_dialog import CalculationProjectsDialog
from moltage.app.project_presentation import StepIndicatorKind
from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.orca.wbl import OrcaWblResultEvidence, WblSpinTreatment
from moltage.orca.wbl_artifacts import (
    OrcaWblPresentation,
    WblReportDetails,
    WblReportOrbital,
    render_wbl_text_export,
)
from phase2b1_test_support import MemorySecretStore
from qt_test_support import wait_until
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
            dialog.calculate_orca_wbl(
                optimized,
                profile,
                connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
            )
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

            dialog.calculate_orca_wbl(
                optimized,
                profile,
                connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
            )

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
    assert isinstance(view._transmission_axis, QLogValueAxis)
    assert view._transmission_axis.base() == 10.0
    assert view._transmission_axis.min() > 0.0
    application.processEvents()
    tick_texts = tuple(
        label.text()
        for label in view._chart_view._log_tick_labels
        if label.isVisible()
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


def test_wbl_text_export_uses_two_or_four_igor_friendly_columns():
    closed_lines = render_wbl_text_export(
        _closed_shell_presentation()
    ).decode("ascii").splitlines()
    spin_lines = render_wbl_text_export(
        _presentation()
    ).decode("ascii").splitlines()

    assert closed_lines == [
        "energy_minus_EF_eV\ttransmission",
        "-1\t0.01",
        "0\t1",
        "1\t0.029999999999999999",
    ]
    assert spin_lines[0].split("\t") == [
        "energy_minus_EF_eV",
        "transmission_alpha",
        "transmission_beta",
        "transmission_total",
    ]
    assert [float(value) for value in spin_lines[1].split("\t")] == [
        -1.0,
        0.01,
        0.02,
        0.03,
    ]
    assert all(len(line.split("\t")) == 4 for line in spin_lines[1:])


def test_wbl_export_button_writes_the_complete_spin_grid(tmp_path):
    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("SyntheticWbl.20300103", _presentation())
    destination = tmp_path / "synthetic-wbl.txt"
    button = view.findChild(QPushButton, "orcaWblExportData")

    with (
        patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(destination), "Tab-delimited text (*.txt)"),
        ),
        patch.object(QMessageBox, "information") as information,
    ):
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        application.processEvents()

    assert destination.read_bytes() == render_wbl_text_export(_presentation())
    assert information.call_count == 1
    assert "tab-delimited transmission data" in information.call_args.args[2]

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


def test_wbl_contact_detection_uses_the_session_bond_threshold_factor(wbl_context):
    """The session factor, not the inference default, builds the shown bonds."""

    QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    dialog = _projects_dialog(profile, optimized, service)

    try:
        with (
            patch(
                "moltage.gui.projects_dialog.OrcaWblSettingsDialog"
            ) as settings_dialog,
            patch.object(QMessageBox, "information"),
            patch.object(
                dialog, "_password_for", return_value=(True, "synthetic-password")
            ),
            patch.object(dialog._thread_pool, "start"),
        ):
            settings_dialog.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            dialog.calculate_orca_wbl(
                optimized,
                profile,
                connectivity_multiplier=0.80,
            )

        assert settings_dialog.call_args.kwargs["connectivity_multiplier"] == 0.80
        connectivity = settings_dialog.call_args.args[1]
        # The synthetic S-C-S geometry only bonds both sulfurs to each other at
        # the 1.10 inference default.
        assert {
            (bond.first_index, bond.second_index) for bond in connectivity
        } == {(0, 1), (1, 2)}
    finally:
        dialog.close()
        dialog.deleteLater()


def test_step_2_is_offered_again_after_a_failed_wbl_stage(wbl_context):
    from tools.molecule_viewer_demo import _orca_wbl_calculation_eligible

    from moltage.domain.calculation_project import fail_orca_wbl_step
    from moltage.gui.projects_dialog import _orca_wbl_eligible

    _profile, optimized, _service = wbl_context
    running = begin_orca_wbl_step(
        optimized.project, settings=_settings(), started_at=NOW
    )
    failed = replace(
        optimized,
        project=fail_orca_wbl_step(
            running, diagnostic="synthetic conversion failure", finished_at=NOW
        ),
    )
    still_running = replace(optimized, project=running)

    assert _orca_wbl_eligible(failed)
    assert _orca_wbl_calculation_eligible(failed)
    assert not _orca_wbl_eligible(still_running)
    assert not _orca_wbl_calculation_eligible(still_running)


def test_rerunning_a_failed_step_2_starts_from_its_previous_settings(wbl_context):
    from moltage.domain.calculation_project import fail_orca_wbl_step

    QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    previous = _settings()
    failed = replace(
        optimized,
        project=fail_orca_wbl_step(
            begin_orca_wbl_step(
                optimized.project, settings=previous, started_at=NOW
            ),
            diagnostic="synthetic conversion failure",
            finished_at=NOW,
        ),
        active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
    )
    dialog = _projects_dialog(profile, failed, service)

    try:
        with patch(
            "moltage.gui.projects_dialog.OrcaWblSettingsDialog"
        ) as settings_dialog:
            settings_dialog.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            dialog.calculate_orca_wbl(
                failed,
                profile,
                connectivity_multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
            )

        assert settings_dialog.call_args.kwargs["initial_settings"] == previous
    finally:
        dialog.close()
        dialog.deleteLater()


def test_successful_step_2_prefills_authorized_rerun_and_updates_curves(wbl_context):
    from tools.molecule_viewer_demo import _orca_wbl_calculation_eligible

    application = QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    first = _completed_wbl(wbl_context)
    snapshot = replace(
        optimized, project=first.project,
        active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
    )
    assert _orca_wbl_calculation_eligible(snapshot)
    dialog = _projects_dialog(profile, snapshot, service)
    requests = []
    dialog.orca_wbl_workspace_requested.connect(requests.append)
    new_settings = replace(_settings(), energy_step_ev=0.1, fermi_energy_ev=-4.8)
    try:
        assert dialog._resubmit_orca.isEnabled()
        with (
            patch("moltage.gui.projects_dialog.OrcaWblSettingsDialog") as settings_dialog,
            patch.object(dialog, "_password_for", return_value=(True, "synthetic-password")),
            patch.object(QMessageBox, "information"),
            patch.object(QMessageBox, "critical") as error,
        ):
            settings_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            settings_dialog.return_value.selected_settings.return_value = new_settings
            dialog.calculate_orca_wbl(snapshot, profile, connectivity_multiplier=1.1)
            wait_until(lambda: not dialog._workers, timeout=5.0)
        error.assert_not_called()
        assert settings_dialog.call_args.kwargs["initial_settings"] == _settings()
        assert settings_dialog.call_args.kwargs["replacing_result"] is True
        assert dialog._current_snapshot().active_step.orca_wbl_settings == new_settings
        assert dialog._records[0].indicators[1].kind is StepIndicatorKind.SUCCEEDED
        assert len(requests) == 1
        assert len(requests[0].presentation.energy_relative_ev) == 41
        assert requests[0].presentation.report.fermi_energy_ev == -4.8
        assert dialog._resubmit_orca.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_cancel_successful_step_2_rerun_never_starts_worker(wbl_context):
    QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    first = _completed_wbl(wbl_context)
    snapshot = replace(optimized, project=first.project, active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION)
    dialog = _projects_dialog(profile, snapshot, service)
    try:
        with (
            patch("moltage.gui.projects_dialog.OrcaWblSettingsDialog") as settings_dialog,
            patch.object(dialog._thread_pool, "start") as start,
            patch.object(dialog, "_password_for") as password,
        ):
            settings_dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            dialog.calculate_orca_wbl(snapshot, profile, connectivity_multiplier=1.1)
        assert settings_dialog.call_args.kwargs["replacing_result"] is True
        start.assert_not_called()
        password.assert_not_called()
        assert dialog._current_snapshot() == snapshot
    finally:
        dialog.close()
        dialog.deleteLater()


def test_failed_replacement_presentation_cannot_relabel_the_previous_curve(wbl_context):
    QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    first = _completed_wbl(wbl_context)
    snapshot = replace(
        optimized, project=first.project,
        active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
        orca_wbl_presentation=_presentation(),
    )
    dialog = _projects_dialog(profile, snapshot, service)
    dialog._pending_orca_wbl_snapshot = snapshot
    try:
        request = OrcaWblRequest(
            profile, first.project.remote_project_path,
            replace(_settings(), energy_step_ev=0.1), "synthetic-password",
            replace_existing_result=first.project.steps[1].orca_wbl_result,
        )
        result = service.calculate(request)
        with (
            patch("moltage.gui.projects_dialog.wbl_presentation_from_result", side_effect=ValueError("synthetic display failure")),
            patch.object(dialog, "_show_error") as show_error,
        ):
            dialog._orca_wbl_succeeded(result)
        current = dialog._current_snapshot()
        assert current.project == result.project
        assert current.orca_wbl_presentation is None
        assert not current.can_view_orca_wbl
        assert not dialog._view_orca_wbl.isEnabled()
        assert dialog._records[0].indicators[1].kind is StepIndicatorKind.SUCCEEDED
        show_error.assert_called_once()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_verified_rollback_restores_previous_view_and_truthful_status(wbl_context):
    QApplication.instance() or QApplication([])
    profile, optimized, service = wbl_context
    first = _completed_wbl(wbl_context)
    old_presentation = _presentation()
    snapshot = replace(
        optimized, project=first.project,
        active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
        orca_wbl_presentation=old_presentation,
    )
    dialog = _projects_dialog(profile, snapshot, service)
    dialog._pending_orca_wbl_snapshot = snapshot
    dialog._previous_orca_wbl_snapshot = snapshot
    try:
        running = begin_orca_wbl_step(first.project, settings=_settings(), started_at=NOW)
        dialog._orca_wbl_project_updated(running)
        assert dialog._current_snapshot().orca_wbl_presentation is None
        restored = replace(first.project, revision=first.project.revision + 2)
        dialog._orca_wbl_project_updated(restored)
        current = dialog._current_snapshot()
        assert current.orca_wbl_presentation is old_presentation
        assert current.can_view_orca_wbl
        assert dialog._view_orca_wbl.isEnabled()
        assert "restored" in current.status_message
        assert "completed" not in current.status_message
    finally:
        dialog.close()
        dialog.deleteLater()


def test_new_result_replaces_open_project_chart_and_export_data(wbl_context):
    from moltage.gui.workspace_tabs import OrcaWblWorkspaceIdentity, OrcaWblWorkspaceRequest
    from tools.molecule_viewer_demo import MoleculeViewerDemo

    application = QApplication.instance() or QApplication([])
    _, optimized, _ = wbl_context
    project_id = optimized.project.project_id
    request = OrcaWblWorkspaceRequest(
        OrcaWblWorkspaceIdentity(project_id, "a" * 64), "SyntheticWbl", _presentation(),
    )
    window = MoleculeViewerDemo()
    try:
        old = window._open_orca_wbl_workspace(request)
        new_request = replace(
            request,
            identity=OrcaWblWorkspaceIdentity(project_id, "b" * 64),
            presentation=replace(_presentation(), transmission_total=(0.06, 0.06, 0.14)),
        )
        new = window._open_orca_wbl_workspace(new_request)
        assert new is not old
        assert window._workspace_tabs.indexOf(old.content) == -1
        assert tuple(window._orca_wbl_workspaces_by_identity) == (new_request.identity,)
        assert new.content.presentation == new_request.presentation
        assert new.content._total.at(2).y() == 0.14
        assert window._open_orca_wbl_workspace(new_request) is new
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()


def test_wbl_view_opens_the_shared_five_page_settings_and_edits_axes():
    """WBL reuses the AITRANSS presentation controls without touching data."""

    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("SyntheticWbl.20300103", _presentation())
    view.resize(900, 620)
    view.show()
    application.processEvents()
    observed: list[tuple[str, int, int]] = []

    def interact() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, TransmissionSettingsDialog)
        observed.append(
            (dialog.windowTitle(), dialog.tabs.count(), dialog.tabs.currentIndex())
        )
        dialog.x_axis_page.minimum.setText("-0.5")
        dialog.x_axis_page.maximum.setText("0.5")
        dialog.y_axis_page.minimum.setText("1e-4")
        dialog.y_axis_page.maximum.setText("1e0")
        QTest.mouseClick(dialog.ok_button, Qt.MouseButton.LeftButton)

    QTimer.singleShot(20, interact)
    view.open_view_settings("y")
    application.processEvents()

    assert observed == [("Transmission View Settings", 5, 1)]
    assert view._energy_axis.min() == -0.5
    assert view._energy_axis.max() == 0.5
    assert view._transmission_axis.min() == 1.0e-4
    assert view._transmission_axis.max() == 1.0
    assert view.presentation.transmission_total == (0.03, 0.03, 0.07)
    assert tuple(
        view._total.at(index).y() for index in range(view._total.count())
    ) == (0.03, 0.03, 0.07)

    view.reset_view()

    assert view._energy_axis.min() == -1.0
    assert view._energy_axis.max() == 1.0
    assert view._transmission_axis.min() == 0.01

    view.close()
    view.deleteLater()


def test_wbl_curve_page_styles_each_spin_curve_independently():
    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("SyntheticWbl.20300103", _presentation())
    view.resize(900, 620)
    view.show()
    application.processEvents()

    dialog = TransmissionSettingsDialog(
        view.visual_settings,
        view,
        initial_page="curve",
    )
    try:
        assert tuple(
            curve.legend_label for curve in dialog.curve_page.settings()
        ) == ("Alpha", "Beta", "Spin sum (Alpha + Beta)")
        beta_color = dialog.curve_page.findChild(
            QPushButton, "transmissionCurveColor2"
        )
        beta_width = dialog.curve_page.findChild(
            QDoubleSpinBox, "transmissionCurveWidth2"
        )
        beta_color.set_color("#00aa00")
        beta_width.setValue(4.0)
        dialog.settings_applied.connect(view._apply_visual_settings)
        dialog._apply()
    finally:
        dialog.deleteLater()

    assert view._beta.pen().color().name() == "#00aa00"
    assert view._beta.pen().widthF() == 4.0
    assert view._alpha.pen().color().name() == "#c51b29"
    assert view._total.pen().widthF() == 1.2

    view.close()
    view.deleteLater()


def test_wbl_legend_omits_the_fermi_reference_and_annotates_with_subscript():
    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("SyntheticWbl.20300103", _presentation())
    view.resize(900, 620)
    view.show()
    application.processEvents()

    visible_labels = tuple(
        marker.label()
        for marker in view._chart.legend().markers()
        if marker.isVisible()
    )

    assert not view._chart.legend().isVisible()
    sidebar = view.findChild(QLabel, "orcaWblReportDetails").text()
    for name in ("Alpha", "Beta", "Spin sum (Alpha + Beta)"):
        assert name in sidebar
    assert "E_F" not in "".join(visible_labels)
    assert view._fermi_marker.count() == 1
    assert view._fermi_marker.at(0).x() == 0.0
    assert (
        view._chart_view._fermi_readout.text()
        == "T(E<sub>F</sub>) = 3.00 × 10⁻²"
    )

    view.close()
    view.deleteLater()


def test_wbl_probe_identifies_the_nearest_spin_curve_by_name():
    application = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("SyntheticWbl.20300103", _presentation())
    view.resize(900, 620)
    view.show()
    application.processEvents()

    target = view._alpha.at(1)
    identity = view._chart_view._probe_index_at(
        view._chart_view._point_in_view(target)
    )

    assert identity is not None
    assert identity[0] == 0

    view._chart_view._show_probe(identity)
    readout = view._chart_view._probe_readout.text()

    assert readout.startswith("Alpha<br>")
    assert "E − E<sub>F</sub> = 0.00 eV" in readout
    assert "T(E) = 2.00 × 10⁻²" in readout
    assert view._probe_marker.at(0) == target

    view.close()
    view.deleteLater()


def _report_presentation():
    return replace(_presentation(), report=WblReportDetails(
        -5.0, 0.25, 0.4, "HYPOTHESIS", "CALIBRATED",
        "S_ALL_P_LEGACY", "S_ALL_P_LEGACY", (("alpha", 2), ("beta", 2)),
        (
            WblReportOrbital("alpha", 2, 0.0, 0.012),
            WblReportOrbital("alpha", 1, -0.6, 0.008),
            WblReportOrbital("beta", 1, 0.5, 0.006),
            WblReportOrbital("beta", 2, 1.5, 0.004),
        ),
    ))


def test_wbl_report_uses_current_parameters_markers_and_resizes_export():
    app = QApplication.instance() or QApplication([])
    presentation = _report_presentation()
    view = OrcaWblTransmissionView("Synthetic report", presentation)
    view.resize(1200, 860)
    view.show()
    app.processEvents()
    try:
        sidebar = view._report_canvas.sidebar
        text = sidebar.text()
        assert "0.25 eV" in text and "0.4 eV" in text
        assert "CALIBRATED" in text and "HYPOTHESIS" in text
        assert "1-based" in text and "legacy S all-p" in text
        assert "ε − E<sub>F</sub> = +1.500 eV" in text
        assert "1.20 × 10⁻²" in text
        assert view._alpha.pen().color().name() == "#c51b29"
        assert view._beta.pen().color().name() == "#2166ac"
        assert view._total.pen().style() == Qt.PenStyle.DashLine
        assert [marker.isVisible() for _mo, marker in view._orbital_markers] == [True, True, True, False]
        mo, marker = view._orbital_markers[0]
        assert marker.pos() == view._chart.mapToPosition(view._alpha.at(1), view._alpha)
        view._energy_axis.setRange(-1.0, -0.1)
        app.processEvents()
        assert [marker.isVisible() for _mo, marker in view._orbital_markers] == [False, True, False, False]
        assert "+1.500" in sidebar.text()
        view.reset_view()
        view._apply_visual_settings(replace(
            view.visual_settings,
            canvas=replace(view.visual_settings.canvas, export_width=1100, export_height=800),
            curves=(replace(view.visual_settings.curves[0], color="#ff00ff"), *view.visual_settings.curves[1:]),
        ))
        assert marker.brush().color().name() == "#ff00ff"
        assert "#ff00ff" in sidebar.text()
        size_before = view.size()
        image = view.capture_image()
        assert (image.width(), image.height()) == (1100, 800)
        assert view.size() == size_before
        assert view.presentation == presentation
        assert sidebar.parent() is view._report_canvas
        area = view._chart.plotArea()
        assert 0.8 < area.width() / area.height() < 1.3
    finally:
        view.close()
        view.deleteLater()


def test_wbl_missing_report_is_explicit_and_closed_shell_markers_stay_total_only():
    app = QApplication.instance() or QApplication([])
    view = OrcaWblTransmissionView("Synthetic minimal", _presentation())
    assert "unavailable" in view._report_canvas.sidebar.text()
    assert view._orbital_markers == []
    view.close()
    view.deleteLater()
    report = replace(
        _report_presentation().report,
        orbital_counts=(("total", 2),),
        top_orbitals=(WblReportOrbital("total", 2, 0.0, 1.0),),
    )
    view = OrcaWblTransmissionView("Synthetic closed shell", replace(_closed_shell_presentation(), report=report))
    view.resize(1200, 860)
    view.show()
    app.processEvents()
    assert len(view._orbital_markers) == 1
    assert view._orbital_markers[0][1].isVisible()
    assert view._alpha is view._beta is None
    assert "Alpha" not in view._report_canvas.sidebar.text()
    view.close()
    view.deleteLater()
