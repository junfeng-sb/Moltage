from dataclasses import replace
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from moltage.gui.density_workspace import (
    DensityResultView,
    DensityResultViewRequest,
    DensityWorkspace,
)
from moltage.gui.theme import DEFAULT_THEME_ID, DEFAULT_THEME_MANAGER
from moltage.gui.status_refresh import StatusRefreshSession
from moltage.gui.projects_dialog import (
    CalculationProjectsDialog,
    DeleteProjectDialog,
)
from moltage.gui.project_list_view import (
    PROJECT_PRESENTATION_ROLE,
    ProjectItemDelegate,
    density_component_indicators,
)
from moltage.app.project_presentation import (
    ProjectViewMode,
    StepIndicatorKind,
)
from moltage.app.density_workflow import (
    DensityRecoveryPhase,
    DensityRecoveryProgress,
    DensityTask,
    DensityWorkflowService,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_management import ProjectManagementService
from moltage.app.project_management import (
    PermanentDensityTaskDeletionRequest,
    PermanentProjectDeletionResult,
)
from moltage.aims.density_difference import DensitySettings
from moltage.domain.density_difference import DensityGrid
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import LsfResourceRequirementMode
from test_density_workflow import MemoryRemote, MemoryConnection
from test_density_difference import partition
from test_density_difference import component_files
from moltage.app.density_results import build_density_result
from moltage.visualization.orbital_surface import (
    OrbitalSurfaceResolution,
)
from moltage.visualization.view_preferences import (
    ATOM_HIGHLIGHT_COLORS_PROPERTY,
)
from phase2b1_test_support import profile, MemorySecretStore


class DensityWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.profile = profile()
        self.remote = MemoryRemote()
        self.service = DensityWorkflowService(MemoryConnection(self.remote), self.temporary.name)
        self.widget = DensityWorkspace(partition().structure, (self.profile,), self.service, MemorySecretStore())
        self.widget.resize(1000, 800)
        self.widget.setWindowOpacity(0)
        self.widget.show()
        self.application.processEvents()

    def tearDown(self):
        self.widget.stop_status_refresh()
        self.widget.viewer.close()
        self.widget.close()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary.cleanup()

    def test_density_refresh_stop_is_immediate_and_does_not_cancel_job(self):
        task = self.service.submit(
            self.profile, "SyntheticRefresh", partition(), DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        self.widget.load_task(task)
        self.widget._profile_password = lambda: (self.profile, None)
        started, release, returned = (threading.Event() for _ in range(3))
        self.addCleanup(release.set)

        def refresh(*_args, **_kwargs):
            started.set()
            release.wait(timeout=5)
            returned.set()
            return task

        commands = tuple(self.remote.commands)
        with patch.object(self.service, "refresh", side_effect=refresh):
            self.widget._remote("refresh")
            self.assertTrue(started.wait(timeout=1))
            self.assertTrue(self.widget.busy)
            self.assertEqual(self.widget.refresh_button.text(), "Stop")
            self.assertFalse(self.widget.has_active_remote_operation(include_status_refresh=False))
            self.widget._remote("refresh")
            self.assertFalse(self.widget.busy)
            self.assertEqual(self.widget.refresh_button.text(), "Refresh Status")
            self.assertTrue(self.widget.refresh_button.isEnabled())
            self.assertFalse(returned.is_set())
            self.assertEqual(self.widget.task, task)
            release.set()
            self.assertTrue(returned.wait(timeout=1))
            self.application.processEvents()
        self.assertEqual(tuple(self.remote.commands), commands)
        self.assertIn("No calculation was cancelled", self.widget.status.text())

    def test_density_refresh_timeout_retains_task_and_unblocks_controls(self):
        task = self.service.submit(
            self.profile, "SyntheticTimeout", partition(), DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        self.widget.load_task(task)
        self.widget._profile_password = lambda: (self.profile, None)
        release = threading.Event()
        self.addCleanup(release.set)

        def refresh(*_args, **_kwargs):
            release.wait(timeout=5)
            return task

        with patch.object(self.service, "refresh", side_effect=refresh), patch(
            "moltage.gui.density_workspace.StatusRefreshSession",
            side_effect=lambda operation, parent: StatusRefreshSession(operation, parent, timeout_ms=50),
        ), patch("moltage.gui.density_workspace.QMessageBox.critical") as critical:
            self.widget._remote("refresh")
            for _index in range(200):
                if not self.widget.busy:
                    break
                QTest.qWait(10)
                self.application.processEvents()
            self.assertFalse(self.widget.busy)
            self.assertIn("Status refresh timed out", self.widget.status.text())
            self.assertEqual(self.widget.task, task)
            self.assertEqual(self.widget.refresh_button.text(), "Refresh Status")
            self.assertFalse(release.is_set())
            critical.assert_not_called()

    def test_continuous_pick_transfer_remove_and_no_camera_reset(self):
        camera = self.widget.viewer._renderer.GetActiveCamera()
        before = (camera.GetPosition(), camera.GetFocalPoint(), camera.GetViewUp())
        self.widget.assign_atoms((0, 2))
        self.widget.active_subset.setCurrentIndex(1)
        self.widget.assign_atoms((1, 2))
        self.assertEqual(self.widget._selection, [{0}, {1, 2}])
        self.widget.assign_atoms((2,), remove=True)
        self.assertEqual(self.widget._selection, [{0}, {1}])
        self.assertEqual(
            self.widget.viewer._scene.grouped_atom_indices,
            ((0,), (1,)),
        )
        self.assertEqual(
            self.widget.viewer._scene._atom_highlight_ring_polydata.GetNumberOfLines(),
            2,
        )
        self.assertEqual(
            self.widget.viewer._scene._highlight_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertEqual(before, (camera.GetPosition(), camera.GetFocalPoint(), camera.GetViewUp()))

    def test_point_pick_toggles_the_active_subset(self):
        self.widget._point_picked(0)
        self.assertEqual(self.widget._selection, [{0}, set()])
        self.widget._point_picked(0)
        self.assertEqual(self.widget._selection, [set(), set()])
        self.widget.active_subset.setCurrentIndex(1)
        self.widget._point_picked(0)
        self.assertEqual(self.widget._selection, [set(), {0}])

    def test_lsf_uses_a_distinct_resource_page_and_fixed_pure_mpi_values(self):
        lsf_profile = replace(
            self.profile,
            execution_preset=replace(
                self.profile.execution_preset,
                scheduler_kind=SchedulerKind.LSF,
                unset_slurm_export_env=False,
                lsf_resource_requirement_mode=(
                    LsfResourceRequirementMode.SPAN_RUSAGE
                ),
                slurm_aitranss_launch_mode=None,
                slurm_aitranss_srun_path=None,
            ),
        )
        self.widget.server.setItemData(0, lsf_profile)
        self.widget._server_changed()

        self.assertEqual(
            self.widget.scheduler_resource_pages.currentWidget().title(),
            "LSF resources",
        )
        self.widget.lsf_resource_fields["nodes"].setValue(2)
        self.widget.lsf_resource_fields["ntasks"].setValue(24)
        values = self.widget._resource_values()
        self.assertEqual((values["nodes"], values["ntasks"]), (2, 24))
        self.assertEqual(values["cpus_per_task"], 1)
        self.assertEqual(values["omp_num_threads"], 1)
        self.assertFalse(values["unset_slurm_export_env"])

    def test_enter_applies_selection_without_submit_and_snapshot_is_current(self):
        with patch.object(self.widget, "_run") as run:
            field = self.widget.index_fields[0]
            field.setText("1,3")
            self.assertEqual(self.widget._selection[0], {0, 2})
            QTest.keyClick(field, Qt.Key.Key_Return)
            self.assertTrue(self.widget.isVisible())
            self.assertEqual(self.widget._selection[0], {0, 2})
            run.assert_not_called()
        self.widget.index_fields[1].setText("2")
        part, settings, grid = self.widget.scientific_inputs()
        self.assertIs(part.structure, self.widget.structure)
        self.assertEqual(part.subset1, (0, 2))

    def test_fragment_fields_have_no_apply_button_and_activate_on_click(self):
        self.assertNotIn(
            "Apply",
            [button.text() for button in self.widget.findChildren(QPushButton)],
        )
        QTest.mouseClick(
            self.widget.index_fields[1],
            Qt.MouseButton.LeftButton,
        )
        self.assertEqual(self.widget.active_subset.currentIndex(), 1)

    def test_submit_lists_unassigned_atom_numbers_before_remote_work(self):
        self.widget.index_fields[0].setText("1")
        self.widget.index_fields[1].setText("2")
        with patch(
            "moltage.gui.density_workspace.QMessageBox.warning"
        ) as warning, patch.object(self.widget, "_profile_password") as password:
            self.widget._submit()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "Atoms not assigned")
        self.assertIn("3", warning.call_args.args[2])
        password.assert_not_called()

    def test_rectangle_selects_projected_atoms(self):
        picked = self.widget.viewer.atoms_in_rectangle(QRect(0, 0, self.widget.viewer.width(), self.widget.viewer.height()))
        self.assertEqual(set(picked), {0, 1, 2})
        style = self.widget.viewer._rubber_band.styleSheet()
        self.assertIn("background-color: transparent", style)
        self.assertIn("border: 1px dashed #111111", style)

    def test_fragment_viewer_defaults_to_atom_only_hover(self):
        viewer = self.widget.viewer
        self.assertTrue(viewer._hover_picking_enabled)
        self.assertFalse(viewer._hover_bond_picking_enabled)
        self.assertTrue(viewer._vtk_widget.hasMouseTracking())

        viewer._set_hover_target(("atom", 1))

        self.assertEqual(viewer._scene.hover_highlight, (1, None))
        self.assertEqual(
            viewer._scene._atom_highlight_ring_polydata.GetNumberOfLines(),
            1,
        )
        labels = (
            viewer._scene._hover_atom_label_polydata.GetPointData().GetAbstractArray(
                "atom_index_label"
            )
        )
        self.assertEqual(labels.GetValue(0), "2")

    def test_live_theme_change_updates_fragment_and_hover_colors(self):
        try:
            DEFAULT_THEME_MANAGER.apply(self.application, "event_horizon")
            self.application.processEvents()

            self.assertEqual(
                self.widget.viewer._scene.atom_highlight_colors,
                self.application.property(ATOM_HIGHLIGHT_COLORS_PROPERTY),
            )
        finally:
            DEFAULT_THEME_MANAGER.apply(self.application, DEFAULT_THEME_ID)
            self.application.processEvents()

    def test_three_lamps_and_unknown_dispatch_lock_submission(self):
        self.remote.dispatch_error = __import__('moltage.remote.executor', fromlist=['RemoteCommandOutcomeUnknown']).RemoteCommandOutcomeUnknown("Synthetic loss")
        try:
            self.service.submit(self.profile, "Example", partition(), DensitySettings(), DensityGrid((0, 0, 0), (2, 2, 2), .1))
        except Exception as error:
            with patch("moltage.gui.density_workspace.QMessageBox.critical"):
                self.widget._error(error)
        self.assertEqual(self.widget.task.state, "UNKNOWN")
        self.assertFalse(self.widget.submit_button.isEnabled())
        self.assertFalse(self.widget.retry_button.isEnabled())
        self.assertEqual(len(self.widget.component_lamps), 3)
        self.assertEqual(
            [lamp.state for lamp in self.widget.component_lamps],
            ["UNKNOWN", "UNKNOWN", "UNKNOWN"],
        )
        self.assertTrue(
            all(lamp.size().width() == 18 for lamp in self.widget.component_lamps)
        )

    def test_stopped_task_parameters_submit_as_new_preserved_task(self):
        original_grid = DensityGrid.around(
            partition().structure, spacing=0.1, padding=3.0
        )
        task = self.service.submit(
            self.profile,
            "Original",
            partition(),
            DensitySettings(),
            original_grid,
        )
        self.widget.load_task(task)
        self.assertFalse(self.widget.submit_button.isEnabled())
        self.assertTrue(
            all(
                not self.widget.settings_tabs.widget(index).isEnabled()
                for index in range(3)
            )
        )

        self.remote.scheduler_state = "CANCELLED"
        stopped = self.service.refresh(self.profile, task.remote_path)
        self.assertEqual(stopped.state, "CANCELLED")
        historical = {
            path: contents
            for path, contents in self.remote.files.items()
            if path.startswith(task.remote_path + "/")
        }
        self.widget.load_task(stopped)

        self.assertEqual(
            [lamp.state for lamp in self.widget.component_lamps],
            ["CANCELLED", "NOT_STARTED", "NOT_STARTED"],
        )
        indicators = density_component_indicators(stopped)
        self.assertIs(indicators[0].kind, StepIndicatorKind.CANCELLED)
        self.assertIn("CANCELLED", indicators[0].tooltip)
        self.assertEqual(
            self.widget.submit_button.text(), "Resubmit with Changes"
        )
        self.assertTrue(self.widget.submit_button.isEnabled())
        self.assertTrue(
            all(
                self.widget.settings_tabs.widget(index).isEnabled()
                for index in range(3)
            )
        )
        self.assertTrue(self.widget.name.isEnabled())
        self.assertTrue(self.widget.server.isEnabled())
        self.assertTrue(self.widget.index_fields[0].isEnabled())
        self.assertTrue(self.widget.scf_fields["sc_iter_limit"].isEnabled())
        self.assertTrue(self.widget.padding.isEnabled())
        self.assertTrue(self.widget.runtime_hours.isEnabled())

        self.widget.name.setText("Edited")
        self.widget.index_fields[0].setText("1-2")
        self.widget.index_fields[1].setText("3")
        self.widget.scf_fields["sc_iter_limit"].setValue(777)
        self.widget.spacing.setValue(0.2)
        self.widget.padding.setValue(2.0)
        self.widget.resource_fields["memory_gb"].setValue(96)
        self.widget.runtime_hours.setValue(2.5)
        self.widget.secret_store.set_password(
            self.profile.profile_id, "SECRET_TEST_ONLY"
        )
        submitted = []
        with patch.object(
            self.widget,
            "_run",
            side_effect=lambda operation: submitted.append(operation()),
        ):
            self.widget._submit()

        self.assertEqual(len(submitted), 1)
        replacement = submitted[0]
        self.assertNotEqual(replacement.task_id, stopped.task_id)
        self.assertNotEqual(replacement.remote_path, stopped.remote_path)
        self.assertEqual(replacement.name, "Edited")
        self.assertEqual(replacement.partition.subset1, (0, 1))
        self.assertEqual(replacement.partition.subset2, (2,))
        self.assertEqual(replacement.settings.sc_iter_limit, 777)
        self.assertEqual(
            replacement.grid,
            DensityGrid.around(
                partition().structure, spacing=0.2, padding=2.0
            ),
        )
        self.assertEqual(replacement.attempt["resources"]["memory_gb"], 96)
        self.assertEqual(replacement.attempt["resources"]["runtime_minutes"], 150)
        self.assertEqual(
            {
                path: contents
                for path, contents in self.remote.files.items()
                if path.startswith(task.remote_path + "/")
            },
            historical,
        )

    def test_projects_item_has_three_right_lamps_and_no_transport_snapshot(self):
        task = self.service.submit(self.profile, "Example", partition(), DensitySettings(), DensityGrid((0, 0, 0), (2, 2, 2), .1))
        dialog = CalculationProjectsDialog((self.profile,), self.profile.profile_id, Mock(), MemorySecretStore(), Mock(), density_service=self.service)
        try:
            dialog.update_density_task(task)
            self.assertEqual(dialog._project_list.count(), 1)
            self.assertIsNone(dialog._current_snapshot())
            self.assertEqual(dialog._current_density_task().task_id, task.task_id)
            item = dialog._project_list.item(0)
            self.assertTrue(item.icon().isNull())
            self.assertIs(item.data(PROJECT_PRESENTATION_ROLE), task)
            rectangles = ProjectItemDelegate.density_indicator_rects(
                QRect(0, 0, 600, 50), ProjectViewMode.DETAILS
            )
            self.assertEqual(len(rectangles), 3)
            self.assertTrue(all(rectangle.width() == 18 for rectangle in rectangles))
            self.assertGreater(rectangles[0].left(), 500)
            with patch.object(dialog, "_request_density_cancel") as cancel:
                action = dialog._density_task_menu(task).actions()[0]
                self.assertTrue(action.isEnabled())
                action.trigger()
                cancel.assert_called_once_with(task)
            callback = Mock()
            dialog.density_workspace_requested.connect(callback)
            dialog._open_selected()
            callback.assert_called_once()
            self.assertFalse(dialog._retry_step3.isEnabled())
            self.assertFalse(dialog._retry_step4.isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_density_task_delete_reuses_project_recycle_and_restore_rules(self):
        queued = self.service.submit(
            self.profile,
            "Delete_Example",
            partition(),
            DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        attempt = queued.attempt | {
            "state": "FAILED",
            "component_states": {
                name: "FAILED" for name in queued.attempt["components"]
            },
        }
        failed = DensityTask(
            queued.remote_path,
            queued.data | {"attempts": [attempt]},
        )
        connection = Mock()
        index = LocalProjectIndexRepository(
            Path(self.temporary.name) / "project-index.json"
        )
        management = ProjectManagementService(connection, index)
        dialog = CalculationProjectsDialog(
            (self.profile,),
            self.profile.profile_id,
            Mock(),
            MemorySecretStore(),
            Mock(),
            project_management_service=management,
            project_workspace_open=lambda task_id: task_id == failed.task_id,
            density_service=self.service,
        )
        try:
            dialog.update_density_task(queued)
            self.assertFalse(dialog._delete_project.isEnabled())
            dialog.update_density_task(failed)
            self.assertTrue(dialog._delete_project.isEnabled())
            self.assertEqual(dialog._delete_project.text(), "Delete Task...")
            self.assertIn(
                "calculation and result workspace tabs",
                dialog._permanent_delete_block_reason(failed),
            )
            dialog._password_for = Mock(
                side_effect=AssertionError("local recycle requested credentials")
            )
            with patch.object(
                DeleteProjectDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ):
                dialog._delete_selected_project()

            self.assertEqual(dialog._project_list.count(), 0)
            self.assertEqual(index.load(), ())
            self.assertEqual(index.load_recycled()[0].project_id, failed.task_id)
            connection.connect_for_remote_operation.assert_not_called()
            dialog.update_density_task(failed)
            self.assertEqual(dialog._project_list.count(), 0)

            dialog._show_recycle_bin()
            self.assertEqual(dialog._project_list.count(), 1)
            dialog._restore_selected_project()
            dialog._show_projects()
            dialog.update_density_task(failed)
            self.assertEqual(dialog._project_list.count(), 1)
            self.assertEqual(
                dialog._current_density_task().task_id,
                failed.task_id,
            )
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_density_task_permanent_delete_routes_the_density_request(self):
        queued = self.service.submit(
            self.profile,
            "Permanent_Delete",
            partition(),
            DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        failed = DensityTask(
            queued.remote_path,
            queued.data
            | {
                "attempts": [
                    queued.attempt
                    | {
                        "state": "FAILED",
                        "component_states": {
                            name: "FAILED"
                            for name in queued.attempt["components"]
                        },
                    }
                ]
            },
        )
        management = Mock(spec=ProjectManagementService)
        management.recycled_projects.return_value = ()
        management.permanently_delete.side_effect = (
            lambda request, progress: PermanentProjectDeletionResult(
                request.task.task_id,
                request.task.remote_path,
            )
        )
        dialog = CalculationProjectsDialog(
            (self.profile,),
            self.profile.profile_id,
            Mock(),
            MemorySecretStore(),
            Mock(),
            project_management_service=management,
            project_workspace_open=lambda _task_id: False,
            density_service=self.service,
        )
        dialog._password_for = Mock(return_value=(True, "temporary-secret"))

        def accept_permanent(delete_dialog):
            delete_dialog._permanent.setChecked(True)
            return QDialog.DialogCode.Accepted

        try:
            dialog.update_density_task(failed)
            with patch.object(
                DeleteProjectDialog,
                "exec",
                accept_permanent,
            ):
                dialog._delete_selected_project()
            for _ in range(300):
                if not dialog._busy:
                    break
                self.application.processEvents()
                QTest.qWait(10)
            self.assertFalse(dialog._busy)
            management.permanently_delete.assert_called_once()
            request = management.permanently_delete.call_args.args[0]
            self.assertIsInstance(request, PermanentDensityTaskDeletionRequest)
            self.assertEqual(request.task.task_id, failed.task_id)
            self.assertEqual(dialog._project_list.count(), 0)
            self.assertIn("server density task", dialog._status.text())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_density_runtime_is_displayed_in_hours_and_saved_as_minutes(self):
        preset = self.profile.execution_preset
        self.assertEqual(
            self.widget.runtime_hours.value(), preset.runtime_minutes / 60
        )
        self.assertEqual(self.widget.runtime_hours.suffix(), " hours")
        self.assertEqual(self.widget.resource_fields["memory_gb"].suffix(), " GB")
        self.widget.runtime_hours.setValue(1.5)
        self.assertEqual(self.widget._resource_values()["runtime_minutes"], 90)

    def test_recovery_progress_shows_current_file_and_aggregate_bytes(self):
        self.widget._recovery_progress(
            DensityRecoveryProgress(
                DensityRecoveryPhase.DOWNLOADING,
                "Downloading Subset 2 / density.cube",
                current_file_bytes=512,
                current_file_total_bytes=1024,
                retrieved_bytes=1536,
                total_bytes=4096,
            )
        )

        self.assertTrue(self.widget.recovery_progress_panel.isVisible())
        self.assertIn(
            "Current file 512 B / 1.0 KiB",
            self.widget.recovery_progress_label.text(),
        )
        self.assertIn(
            "Total 1.5 KiB / 4.0 KiB",
            self.widget.recovery_progress_label.text(),
        )
        self.assertEqual(self.widget.recovery_progress_bar.maximum(), 1000)
        self.assertEqual(self.widget.recovery_progress_bar.value(), 375)

        self.widget._recovery_progress(
            DensityRecoveryProgress(
                DensityRecoveryPhase.PROCESSING,
                "Validating Total (1/3)…",
                retrieved_bytes=4096,
                total_bytes=4096,
            )
        )
        self.assertEqual(self.widget.recovery_progress_bar.maximum(), 0)
        self.assertIn("Downloaded 4.0 KiB / 4.0 KiB", self.widget.recovery_progress_label.text())

    def test_recover_operation_passes_the_worker_progress_callback(self):
        task = self.service.submit(
            self.profile,
            "Example",
            partition(),
            DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        self.widget.load_task(task)
        progress = Mock()
        with patch.object(
            self.widget,
            "_profile_password",
            return_value=(self.profile, "SECRET_TEST_ONLY"),
        ), patch.object(self.widget, "_run") as run, patch.object(
            self.service,
            "recover",
            return_value="recovered",
        ) as recover:
            self.widget._remote("recover")
            operation = run.call_args.args[0]
            self.assertTrue(run.call_args.kwargs["with_progress"])
            self.assertEqual(operation(progress), "recovered")

        recover.assert_called_once_with(
            self.profile,
            task.remote_path,
            "SECRET_TEST_ONLY",
            progress=progress,
        )

    def test_workspace_lamp_context_menu_cancels_the_shared_job(self):
        task = self.service.submit(
            self.profile,
            "Example",
            partition(),
            DensitySettings(),
            DensityGrid((0, 0, 0), (2, 2, 2), .1),
        )
        self.widget.load_task(task)
        for lamp in self.widget.component_lamps:
            action = lamp.context_menu().actions()[0]
            self.assertTrue(action.isEnabled())
            self.assertEqual(action.objectName(), "cancelDensityJob")

    def test_surface_endpoints_keep_camera_and_lighting_does_not_rebuild_density(self):
        grid = DensityGrid((0, 0, 0), (2, 2, 2), .1)
        directories = component_files(self.temporary.name, partition(), grid)
        result = build_density_result(partition(), grid, directories)
        task = self.service.submit(
            self.profile,
            "Example",
            partition(),
            DensitySettings(),
            grid,
        )
        result_view = DensityResultView(
            DensityResultViewRequest(task, result)
        )
        result_view._surface_timer.stop()
        result_view.resize(1000, 800)
        result_view.setWindowOpacity(0)
        result_view.show()
        self.application.processEvents()
        camera = result_view.viewer._renderer.GetActiveCamera()
        before = (camera.GetPosition(), camera.GetFocalPoint(), camera.GetViewUp())
        resolution = result_view._orbital_preferences.resolution
        basis = result_view.result.display_basis(
            resolution.maximum_axis_points
        )
        result_view._surface_ready(
            (
                result_view._surface_revision,
                resolution,
                basis,
                basis[2],
            )
        )
        self.assertIs(result_view._displayed_field, result.difference)
        from dataclasses import replace
        with patch.object(result_view, "_request_surface") as extraction:
            result_view._preview_orbital(replace(result_view._orbital_preferences, ambient=.75))
            extraction.assert_not_called()
        result_view._surface_ready(
            (
                result_view._surface_revision,
                resolution,
                basis,
                None,
            )
        )
        self.assertIsNone(result_view._displayed_field)
        self.assertEqual(before, (camera.GetPosition(), camera.GetFocalPoint(), camera.GetViewUp()))
        result_view.viewer.close()
        result_view.close()
        result_view.deleteLater()

    def test_density_result_defaults_to_medium_and_top_controls_are_exclusive(self):
        grid = DensityGrid((0, 0, 0), (2, 2, 2), .1)
        directories = component_files(self.temporary.name, partition(), grid)
        result = build_density_result(partition(), grid, directories)
        task = self.service.submit(
            self.profile,
            "Example",
            partition(),
            DensitySettings(),
            grid,
        )
        result_view = DensityResultView(DensityResultViewRequest(task, result))
        result_view._surface_timer.stop()
        try:
            self.assertIs(
                result_view._orbital_preferences.resolution,
                OrbitalSurfaceResolution.MEDIUM,
            )
            self.assertTrue(
                result_view.resolution_buttons[
                    OrbitalSurfaceResolution.MEDIUM
                ].isChecked()
            )
            self.assertEqual(
                sum(button.isChecked() for button in result_view.resolution_buttons.values()),
                1,
            )
            with patch.object(result_view, "_request_surface") as extraction:
                result_view.resolution_buttons[
                    OrbitalSurfaceResolution.LOW
                ].click()
                extraction.assert_called_once()
            self.assertIs(
                result_view._orbital_preferences.resolution,
                OrbitalSurfaceResolution.LOW,
            )
            with patch.object(result_view, "_request_surface") as extraction:
                result_view._preview_orbital(
                    replace(
                        result_view._orbital_preferences,
                        resolution=OrbitalSurfaceResolution.FULL,
                    )
                )
                extraction.assert_called_once()
            self.assertTrue(
                result_view.resolution_buttons[
                    OrbitalSurfaceResolution.FULL
                ].isChecked()
            )
            self.assertIn("display only", result_view.resolution_summary.text())
        finally:
            result_view.viewer.close()
            result_view.close()
            result_view.deleteLater()

    def test_recovery_requests_a_separate_result_view(self):
        grid = DensityGrid((0, 0, 0), (2, 2, 2), .1)
        directories = component_files(self.temporary.name, partition(), grid)
        result = build_density_result(partition(), grid, directories)
        task = self.service.submit(
            self.profile,
            "Example",
            partition(),
            DensitySettings(),
            grid,
        )
        request = Mock()
        self.widget.result_view_requested.connect(request)
        self.widget._completed((task, result))
        request.assert_called_once()
        emitted = request.call_args.args[0]
        self.assertIsInstance(emitted, DensityResultViewRequest)
        self.assertIs(emitted.result, result)
        self.assertFalse(hasattr(self.widget, "display_mode"))
        self.assertFalse(hasattr(self.widget, "slider"))

    def test_calculation_entry_snapshots_geometry_even_when_read_only(self):
        from tools.molecule_viewer_demo import MoleculeViewerDemo, _DensityWorkspace
        window = MoleculeViewerDemo()
        window.setWindowOpacity(0)
        window.show()
        self.application.processEvents()
        path = Path(self.temporary.name) / "current.xyz"
        path.write_text("2\ncurrent\nAu 0 0 0\nH 1.25 2.5 -1\n")
        try:
            source = window._open_local_geometry(path)
            from dataclasses import replace
            from moltage.domain.structure import MolecularStructure
            edited = MolecularStructure((source.structure.atoms[0], replace(source.structure.atoms[1], x=3.75, z=-2.0)))
            window._structure = edited
            source.read_only = True
            window._route_active_workspace()
            self.assertTrue(window._density_action.isEnabled())
            dependencies = SimpleNamespace(connection_service=MemoryConnection(self.remote), secret_store=MemorySecretStore(),
                profile_repository=SimpleNamespace(load=lambda: SimpleNamespace(profiles=(self.profile,))))
            with patch("tools.molecule_viewer_demo._create_project_submission_dependencies", return_value=dependencies):
                window._new_density_workspace()
            density = window._active_workspace()
            self.assertIsInstance(density, _DensityWorkspace)
            self.assertEqual(density.content.structure, edited)
            self.assertEqual(density.content.structure, source.structure)
            self.assertFalse(window._submit_aims_action.isEnabled())
            self.assertTrue(
                window._has_open_managed_project_workspace(density.identity)
            )
            density.content.busy = True
            self.assertTrue(window._project_has_external_operation(density.identity))
            density.content.busy = False
            window._close_workspace_tab(window._workspace_tabs.currentIndex())
            self.assertIs(window._active_geometry_workspace(), source)
        finally:
            window.close()
            window.deleteLater()

    def test_main_window_opens_recovered_density_in_dedicated_view(self):
        from tools.molecule_viewer_demo import (
            MoleculeViewerDemo,
            _DensityResultWorkspace,
        )

        grid = DensityGrid((0, 0, 0), (2, 2, 2), .1)
        directories = component_files(self.temporary.name, partition(), grid)
        result = build_density_result(partition(), grid, directories)
        task = self.service.submit(
            self.profile,
            "Example",
            partition(),
            DensitySettings(),
            grid,
        )
        window = MoleculeViewerDemo()
        window.setWindowOpacity(0)
        window.show()
        try:
            workspace = window._open_density_result(
                DensityResultViewRequest(task, result)
            )
            self.assertIsInstance(workspace, _DensityResultWorkspace)
            self.assertIs(window._active_workspace(), workspace)
            self.assertEqual(
                workspace.display_title,
                "Example — Density Difference",
            )
            self.assertIs(
                workspace.content._orbital_preferences.resolution,
                OrbitalSurfaceResolution.MEDIUM,
            )
            self.assertTrue(
                window._has_open_managed_project_workspace(task.task_id)
            )
            workspace.content._surface_timer.stop()
        finally:
            window.close()
            window.deleteLater()
