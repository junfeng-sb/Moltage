from dataclasses import replace
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QThread, Qt
from PySide6.QtTest import QTest
from qt_test_support import wait_until
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QSpinBox,
    QTabWidget,
)

from moltage.app.server_profiles import (
    ServerProfileRepository,
    ServerProfileService,
)
from moltage.app.connection_service import PasswordRequiredError
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    LsfResourceRequirementMode,
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    SlurmAitranssLaunchMode,
    SlurmCommandMode,
)
from moltage.orca.catalog import (
    OrcaVersionEvidence,
    OrcaVersionFamily,
)
from moltage.gui.cluster_execution_dialog import (
    ClusterExecutionSettingsDialog,
)
from moltage.gui.runtime_configuration_dialog import RuntimeConfigurationDialog
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
)
from moltage.remote.runtime_discovery import (
    RuntimeCandidate,
    RuntimeDiscoveryError,
    RuntimeDiscoveryResult,
)
from moltage.remote.scheduler_discovery import (
    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
    LOGIN_SHELL_DISCOVERY_COMMAND,
)
from phase2b1_test_support import MemorySecretStore, profile
from synthetic_test_data import (
    SYNTHETIC_AITRANSS_EXECUTABLE,
    SYNTHETIC_FHI_EXECUTABLE,
    SYNTHETIC_SPECIES_ROOT,
)


SPECIES_ROOT = SYNTHETIC_SPECIES_ROOT


class FakeSchedulerExecutor:
    def __init__(self) -> None:
        self.commands = []
        self.closed = False
        self.mkdir_calls = []

    def execute(self, command):
        self.commands.append(command)
        if command == CURRENT_ENVIRONMENT_DISCOVERY_COMMAND:
            return RemoteCommandResult(
                0,
                b"__MOLTAGE_SCHEDULER__=sbatch|/usr/bin/sbatch|||||\n",
                b"",
            )
        if command == "/usr/bin/sbatch --version":
            return RemoteCommandResult(0, b"slurm 24.11.3\n", b"")
        if command == "/custom/slurm/bin/sbatch --version":
            return RemoteCommandResult(0, b"slurm 23.02.7\n", b"")
        if command in {
            "test -x /usr/bin/squeue && test -x /usr/bin/sacct && test -x /usr/bin/scancel",
            "test -x /custom/slurm/bin/squeue && test -x /custom/slurm/bin/sacct && test -x /custom/slurm/bin/scancel",
            "test -f /usr/bin/srun && test -r /usr/bin/srun && test -x /usr/bin/srun",
            "test -f /custom/slurm/bin/srun && test -r /custom/slurm/bin/srun && test -x /custom/slurm/bin/srun",
        }:
            return RemoteCommandResult(0, b"", b"")
        raise AssertionError(f"unexpected command: {command}")

    def mkdir(self, path):
        self.mkdir_calls.append(path)

    def close(self):
        self.closed = True


class TransportFailingSchedulerExecutor(FakeSchedulerExecutor):
    def execute(self, command):
        self.commands.append(command)
        raise RemoteCommandOutcomeUnknown(
            "SSH transport failed during Slurm discovery"
        )


class FakeLsfSchedulerExecutor(FakeSchedulerExecutor):
    def execute(self, command):
        self.commands.append(command)
        if command == CURRENT_ENVIRONMENT_DISCOVERY_COMMAND:
            return RemoteCommandResult(
                0,
                b"__MOLTAGE_SCHEDULER__=bsub|/srv/moltage-test/lsf/current/bin/bsub|"
                b"/srv/moltage-test/lsf/conf|/srv/moltage-test/lsf/conf|/srv/moltage-test/lsf/current/bin|/srv/moltage-test/lsf/current/lib|"
                b"/srv/moltage-test/lsf/current/etc\n",
                b"",
            )
        if all(
            name in command
            for name in ("bsub", "bjobs", "bhist", "bkill", "lsid")
        ):
            return RemoteCommandResult(
                0,
                b"IBM Spectrum LSF synthetic-test\n"
                b"__MOLTAGE_LSF_ENVDIR__=/srv/moltage-test/lsf/conf|"
                b"/srv/moltage-test/lsf/current/lib|/srv/moltage-test/lsf/current/etc\n",
                b"",
            )
        raise AssertionError(f"unexpected command: {command}")


class FakeSchedulerConnection:
    def __init__(self, executor) -> None:
        self.executor = executor
        self.calls = []
        self.worker_threads = []

    def connect_for_remote_operation(self, server_profile, supplied_password=None):
        self.calls.append((server_profile, supplied_password))
        self.worker_threads.append(QThread.currentThread())
        return self.executor


class PasswordPromptingSchedulerConnection(FakeSchedulerConnection):
    def connect_for_remote_operation(self, server_profile, supplied_password=None):
        self.calls.append((server_profile, supplied_password))
        self.worker_threads.append(QThread.currentThread())
        if supplied_password is None:
            raise PasswordRequiredError("temporary password required")
        return self.executor


class ClusterExecutionSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _dispose_added_dialogs(self, *dialogs: QDialog) -> None:
        for dialog in dialogs:
            dialog.close()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        gc.collect()

    def test_examplecluster_values_use_numeric_unit_controls_and_clear_labels(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        labels = tuple(label.text() for label in dialog.findChildren(QLabel))

        self.assertEqual(dialog.windowTitle(), "Cluster Execution Settings")
        self.assertEqual(dialog._server_name.text(), "ExampleCluster")
        self.assertEqual(dialog._scheduler_type.text(), "Slurm")
        self.assertTrue(dialog._automatic_mode.isChecked())
        self.assertFalse(dialog._manual_mode.isChecked())
        self.assertFalse(dialog._slurm_bin_directory.isEnabled())
        self.assertEqual(dialog._scheduler_status.text(), "Not detected")
        self.assertEqual(
            dialog._runtime_discovery_button.text(),
            "Discover Runtime...",
        )
        self.assertEqual(
            dialog._runtime_discovery_button.objectName(),
            "discoverRuntime",
        )
        self.assertIsInstance(dialog._nodes, QSpinBox)
        self.assertIsInstance(dialog._ntasks, QSpinBox)
        self.assertIsInstance(dialog._cpus_per_task, QSpinBox)
        self.assertIsInstance(dialog._runtime_hours, QDoubleSpinBox)
        self.assertIsInstance(dialog._memory_gb, QSpinBox)
        self.assertIsInstance(dialog._omp_num_threads, QSpinBox)
        self.assertEqual(dialog._nodes.value(), 1)
        self.assertEqual(dialog._ntasks.value(), 24)
        self.assertEqual(dialog._cpus_per_task.value(), 1)
        self.assertEqual(dialog._runtime_hours.value(), 36.0)
        self.assertEqual(dialog._runtime_hours.decimals(), 1)
        self.assertEqual(dialog._runtime_hours.singleStep(), 0.1)
        self.assertEqual(dialog._runtime_hours.suffix(), " hours")
        self.assertEqual(dialog._memory_gb.value(), 128)
        self.assertEqual(dialog._memory_gb.suffix(), " GB")
        self.assertEqual(dialog._omp_num_threads.value(), 1)
        self.assertEqual(
            dialog._no_requeue.text(),
            "Do not automatically requeue the job",
        )
        self.assertEqual(
            dialog._export_none.text(),
            "Do not export the submission environment",
        )
        self.assertEqual(
            dialog._unset_slurm_export_env.text(),
            "Clear inherited Slurm environment",
        )
        self.assertEqual(
            dialog._module_purge.text(),
            "Clear previously loaded environment modules",
        )
        for expected in (
            "Nodes:",
            "MPI tasks:",
            "CPUs per task:",
            "Maximum runtime:",
            "Memory limit per node:",
            "OpenMP threads:",
            "FHI-aims launch command:",
            "Output file name:",
        ):
            self.assertIn(expected, labels)
        self.assertIn(
            "Command executed after environment modules are loaded.",
            labels,
        )
        self.assertEqual(dialog.findChildren(QListWidget), [dialog._modules])
        self.assertIn("LSF creates it when the job starts", dialog._slurm_output.toolTip())
        self.assertEqual(
            tuple(
                dialog._modules.item(index).text()
                for index in range(dialog._modules.count())
            ),
            ("mpi/example-1.0", "chemistry/fhi-aims-example"),
        )
        self.assertEqual(dialog.findChild(QLineEdit, "serverPassword"), None)
        self.assertFalse(hasattr(dialog, "_browse_output_button"))
        self.assertNotIn("Host:", labels)
        self.assertNotIn("Username:", labels)
        self.assertNotIn("Password:", labels)
        dialog.close()

    def test_save_is_primary_and_initial_editable_state_is_clean(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())

        self.assertTrue(dialog._save_button.isDefault())
        self.assertTrue(dialog._save_button.autoDefault())
        self.assertFalse(dialog._cancel_button.autoDefault())
        self.assertEqual(dialog._changed_field_labels(), ())

        dialog._slurm_account.setText("synthetic-account")
        self.assertIn("Slurm account", dialog._changed_field_labels())
        dialog._working_species_root = "/apps/example/species/defaults_2020"
        self.assertIn("Species definitions root", dialog._changed_field_labels())
        dialog.close()

    def test_unchanged_visible_close_does_not_prompt(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog.show()
        self.application.processEvents()

        with patch.object(dialog, "_prompt_unsaved_changes") as prompt:
            dialog.close()
            self.application.processEvents()

        prompt.assert_not_called()
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()

    def test_unsaved_prompt_lists_fields_and_exact_three_actions(self) -> None:
        captured = {}

        class FakeMessageBox:
            Icon = QMessageBox.Icon
            ButtonRole = QMessageBox.ButtonRole

            def __init__(inner_self, parent):
                captured["parent"] = parent
                captured["buttons"] = []
                inner_self._clicked = None

            def setIcon(inner_self, icon):
                captured["icon"] = icon

            def setWindowTitle(inner_self, title):
                captured["title"] = title

            def setText(inner_self, text):
                captured["text"] = text

            def setInformativeText(inner_self, text):
                captured["informative"] = text

            def addButton(inner_self, text, role):
                button = object()
                captured["buttons"].append((text, role, button))
                return button

            def setDefaultButton(inner_self, button):
                captured["default"] = button

            def setEscapeButton(inner_self, button):
                captured["escape"] = button

            def exec(inner_self):
                inner_self._clicked = captured["buttons"][2][2]

            def clickedButton(inner_self):
                return inner_self._clicked

        dialog = ClusterExecutionSettingsDialog(profile())
        with patch(
            "moltage.gui.cluster_execution_dialog.QMessageBox",
            FakeMessageBox,
        ):
            choice = dialog._prompt_unsaved_changes(
                ("Slurm partition", "ORCA executable")
            )

        self.assertEqual(choice, "cancel")
        self.assertEqual(captured["text"], "You have unsaved changes.")
        self.assertIn("Slurm partition", captured["informative"])
        self.assertIn("ORCA executable", captured["informative"])
        self.assertEqual(
            tuple(text for text, _role, _button in captured["buttons"]),
            ("Save", "Discard", "Cancel"),
        )
        self.assertIs(captured["default"], captured["buttons"][0][2])
        self.assertIs(captured["escape"], captured["buttons"][2][2])
        dialog.close()

    def test_unsaved_prompt_covers_cancel_escape_and_window_close(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog.show()
        dialog._slurm_partition.setText("synthetic-partition")
        self.application.processEvents()

        with patch.object(
            dialog,
            "_prompt_unsaved_changes",
            return_value="cancel",
        ) as prompt:
            dialog._cancel_button.click()
            self.assertTrue(dialog.isVisible())
            self.assertIn("Slurm partition", prompt.call_args.args[0])

        with patch.object(
            dialog,
            "_prompt_unsaved_changes",
            return_value="cancel",
        ) as prompt:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.application.processEvents()
            prompt.assert_called_once()
            self.assertTrue(dialog.isVisible())

        with patch.object(
            dialog,
            "_prompt_unsaved_changes",
            return_value="discard",
        ) as prompt:
            dialog.close()
            self.application.processEvents()
            prompt.assert_called_once()
            self.assertFalse(dialog.isVisible())
        dialog.deleteLater()

    def test_prompt_save_validation_failure_keeps_edits_open(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog.show()
        dialog._manual_mode.setChecked(True)
        dialog._slurm_bin_directory.clear()
        self.application.processEvents()

        with (
            patch.object(dialog, "_prompt_unsaved_changes", return_value="save"),
            patch.object(dialog, "_show_validation_error") as validation_error,
        ):
            dialog.reject()

        validation_error.assert_called_once()
        self.assertTrue(dialog.isVisible())
        self.assertIn("Command location", dialog._changed_field_labels())
        dialog._reject_without_prompt()
        dialog.deleteLater()

    def test_prompt_save_reuses_existing_successful_save(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog.show()
        dialog._slurm_qos.setText("synthetic-qos")
        self.application.processEvents()

        def successful_save():
            dialog.accept()
            return True

        with (
            patch.object(dialog, "_prompt_unsaved_changes", return_value="save"),
            patch.object(dialog, "_save", side_effect=successful_save) as save,
        ):
            dialog.reject()

        save.assert_called_once_with()
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()

    def test_active_worker_guard_precedes_unsaved_prompt(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog._slurm_qos.setText("synthetic-qos")
        worker = object()
        dialog._scheduler_workers.add(worker)

        with patch.object(dialog, "_prompt_unsaved_changes") as prompt:
            dialog.reject()

        prompt.assert_not_called()
        self.assertIn("finish before closing", dialog._scheduler_status.text())
        dialog._scheduler_workers.remove(worker)
        dialog._reject_without_prompt()

    def test_species_root_stays_isolated_when_switching_profile_dialogs(self) -> None:
        first_root = "/srv/alpha/species_defaults/defaults_2020"
        second_root = "/srv/beta/species_defaults/defaults_2020"
        first_profile = replace(
            profile("Alpha"),
            execution_preset=replace(
                profile("Alpha preset").execution_preset,
                fhi_species_defaults_path=first_root,
            ),
        )
        second_profile = replace(
            profile("Beta"),
            execution_preset=replace(
                profile("Beta preset").execution_preset,
                fhi_species_defaults_path=second_root,
            ),
        )

        first = ClusterExecutionSettingsDialog(first_profile)
        second = ClusterExecutionSettingsDialog(second_profile)

        self.assertEqual(first._working_species_root, first_root)
        self.assertEqual(first._fhi_species_root.text(), first_root)
        self.assertEqual(second._working_species_root, second_root)
        self.assertEqual(second._fhi_species_root.text(), second_root)
        self._dispose_added_dialogs(first, second)

    def test_species_folder_selection_changes_only_the_manual_species_field(self) -> None:
        original = profile()
        selected_root = "/srv/selected/species_defaults/defaults_2020"
        dialog = ClusterExecutionSettingsDialog(
            original,
            connection_service=object(),
        )
        manual = RuntimeConfigurationDialog(dialog._manual_hints(), dialog)
        dialog._manual_dialog = manual

        with patch(
            "moltage.gui.cluster_execution_dialog.RemoteDirectoryDialog"
        ) as directory_dialog:
            picker = directory_dialog.return_value
            picker.exec.return_value = QDialog.DialogCode.Accepted
            picker.selected_directory = selected_root
            dialog._browse_species_directory(SPECIES_ROOT)

        self.assertEqual(manual.fhi.species_root.text(), selected_root)
        self.assertEqual(
            dialog._profile.remote_project_root,
            original.remote_project_root,
        )
        self.assertEqual(
            dialog._working_species_root,
            original.execution_preset.fhi_species_defaults_path,
        )
        directory_dialog.assert_called_once()
        explanation = directory_dialog.call_args.kwargs["explanation"]
        self.assertIn("light, tight, and really_tight", explanation)
        self.assertIn("Remote Project Workspace is not changed", explanation)
        self._dispose_added_dialogs(manual, dialog)

    def test_cancelled_species_folder_selection_keeps_existing_value(self) -> None:
        original = profile()
        dialog = ClusterExecutionSettingsDialog(
            original,
            connection_service=object(),
        )
        manual = RuntimeConfigurationDialog(dialog._manual_hints(), dialog)
        dialog._manual_dialog = manual

        with patch(
            "moltage.gui.cluster_execution_dialog.RemoteDirectoryDialog"
        ) as directory_dialog:
            directory_dialog.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            dialog._browse_species_directory(SPECIES_ROOT)

        expected = original.execution_preset.fhi_species_defaults_path
        self.assertEqual(manual.fhi.species_root.text(), expected)
        self.assertEqual(dialog._working_species_root, expected)
        self._dispose_added_dialogs(manual, dialog)

    def test_settings_are_grouped_by_cluster_and_program_hierarchy(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())

        pages = dialog.findChild(QTabWidget, "clusterProgramPages")
        self.assertIsNotNone(pages)
        self.assertEqual(
            tuple(pages.tabText(index) for index in range(pages.count())),
            ("General / Cluster", "FHI-aims", "ORCA"),
        )
        toolchain = dialog.findChild(QTabWidget, "fhiAimsToolchainPages")
        self.assertIsNotNone(toolchain)
        self.assertEqual(
            tuple(toolchain.tabText(index) for index in range(toolchain.count())),
            ("FHI-aims", "AITRANSS"),
        )
        dialog.close()

    def test_orca_runtime_is_independent_and_field_edits_invalidate_evidence(self):
        runtime = OrcaRuntimeConfiguration(
            "/apps/example/orca-6.1/orca",
            RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
            OrcaVersionEvidence(
                "Program Version 6.1.2",
                "6.1.2",
                OrcaVersionFamily.V6_1,
                "synthetic validation",
            ),
        )
        original = replace(profile(), orca_runtime=runtime)
        dialog = ClusterExecutionSettingsDialog(
            original,
            connection_service=object(),
        )

        self.assertEqual(dialog._orca_executable.text(), runtime.executable_path)
        self.assertIn("Verified supported", dialog._orca_status.text())
        dialog._orca_executable.setText("/apps/example/orca-6.0/orca")

        self.assertIn("changed", dialog._orca_status.text())
        dialog._save_button.click()
        self.assertIsNone(dialog.selected_orca_runtime())
        self.assertEqual(
            dialog.selected_runtime_hints().orca.location,
            "/apps/example/orca-6.0/orca",
        )
        self.assertEqual(
            dialog.selected_aitranss_runtime(),
            original.aitranss_runtime,
        )

    def test_missing_unrelated_runtime_does_not_block_profile_settings_save(self):
        orca = OrcaRuntimeConfiguration(
            "/apps/example/orca-6.1/orca",
            RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
            OrcaVersionEvidence(
                "Program Version 6.1.2",
                "6.1.2",
                OrcaVersionFamily.V6_1,
                "synthetic validation",
            ),
        )
        independent = replace(
            profile(),
            aitranss_runtime=None,
            orca_runtime=orca,
        )
        dialog = ClusterExecutionSettingsDialog(independent)

        dialog._save_button.click()

        self.assertEqual(dialog.selected_orca_runtime(), orca)
        self.assertIsNone(dialog.selected_aitranss_runtime())

    def test_lsf_orca_page_disables_discovery_but_keeps_manual_validation(self):
        dialog = ClusterExecutionSettingsDialog(
            profile(),
            connection_service=object(),
        )
        dialog._manual_scheduler_kind.setCurrentIndex(
            dialog._manual_scheduler_kind.findData(SchedulerKind.LSF.value)
        )

        self.assertFalse(dialog._orca_discover_button.isEnabled())
        self.assertTrue(dialog._orca_validate_button.isEnabled())
        self.assertIn("not supported for LSF", dialog._orca_discover_button.toolTip())
        dialog.close()

    def test_scheduler_site_fields_are_optional_and_stay_scheduler_specific(self):
        slurm_dialog = ClusterExecutionSettingsDialog(profile())
        slurm_dialog._slurm_account.setText(" account-a ")
        slurm_dialog._slurm_partition.setText("partition-a")
        slurm_dialog._slurm_qos.setText(" ")
        slurm_dialog._save_button.click()
        slurm = slurm_dialog.selected_preset()

        self.assertEqual(slurm.slurm_account, "account-a")
        self.assertEqual(slurm.slurm_partition, "partition-a")
        self.assertIsNone(slurm.slurm_qos)
        self.assertIsNone(slurm.lsf_queue)
        self.assertIsNone(slurm.lsf_project)

        lsf_dialog = ClusterExecutionSettingsDialog(profile())
        lsf_dialog._manual_scheduler_kind.setCurrentIndex(
            lsf_dialog._manual_scheduler_kind.findData(SchedulerKind.LSF.value)
        )
        lsf_dialog._lsf_queue.setText(" normal ")
        lsf_dialog._lsf_project.setText("project-a")
        lsf_dialog._save_button.click()
        lsf = lsf_dialog.selected_preset()

        self.assertEqual(lsf.lsf_queue, "normal")
        self.assertEqual(lsf.lsf_project, "project-a")
        self.assertIsNone(lsf.slurm_account)
        self.assertIsNone(lsf.slurm_partition)
        self.assertIsNone(lsf.slurm_qos)
        self.assertIsNone(lsf.slurm_aitranss_launch_mode)
        self.assertIsNone(lsf.slurm_aitranss_srun_path)

    def test_lsf_resource_mode_marks_unsubmitted_controls(self):
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog._manual_scheduler_kind.setCurrentIndex(
            dialog._manual_scheduler_kind.findData(SchedulerKind.LSF.value)
        )

        self.assertIs(
            LsfResourceRequirementMode(dialog._lsf_resource_mode.currentData()),
            LsfResourceRequirementMode.SITE_DEFAULT,
        )
        self.assertFalse(dialog._lsf_hosts.isEnabled())
        self.assertFalse(dialog._lsf_memory_gb.isEnabled())
        self.assertIn("not submitted", dialog._lsf_hosts_label.text())
        self.assertIn("No #BSUB -R", dialog._lsf_resource_note.text())

        dialog._lsf_resource_mode.setCurrentIndex(
            dialog._lsf_resource_mode.findData(
                LsfResourceRequirementMode.SPAN_RUSAGE
            )
        )
        self.assertTrue(dialog._lsf_hosts.isEnabled())
        self.assertTrue(dialog._lsf_memory_gb.isEnabled())
        self.assertIn("span/rusage", dialog._lsf_resource_note.text())
        dialog._save_button.click()
        self.assertIs(
            dialog.selected_preset().lsf_resource_requirement_mode,
            LsfResourceRequirementMode.SPAN_RUSAGE,
        )

    def test_scheduler_mode_controls_manual_directory_only_when_selected(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())

        self.assertFalse(dialog._slurm_bin_directory.isEnabled())
        dialog._manual_mode.setChecked(True)
        self.application.processEvents()
        self.assertTrue(dialog._slurm_bin_directory.isEnabled())
        self.assertEqual(dialog._scheduler_check_button.text(), "Verify")
        dialog._automatic_mode.setChecked(True)
        self.application.processEvents()
        self.assertFalse(dialog._slurm_bin_directory.isEnabled())
        self.assertEqual(dialog._scheduler_check_button.text(), "Detect Scheduler")
        dialog.close()

    def test_automatic_detection_runs_off_thread_and_displays_verified_result(self) -> None:
        executor = FakeSchedulerExecutor()
        connection = FakeSchedulerConnection(executor)
        dialog = ClusterExecutionSettingsDialog(
            profile(),
            connection_service=connection,
        )

        dialog._scheduler_check_button.click()
        self._wait_until(lambda: len(connection.calls) == 1)
        self._wait_until(lambda: dialog._scheduler_check_button.isEnabled())

        self.assertIsNot(connection.worker_threads[0], self.application.thread())
        self.assertEqual(dialog._scheduler_status.text(), "✓ Slurm detected")
        self.assertEqual(dialog._sbatch_path.text(), "/usr/bin/sbatch")
        self.assertEqual(dialog._slurm_version.text(), "slurm 24.11.3")
        self.assertEqual(dialog._detection_method.text(), "Remote environment")
        self.assertEqual(executor.mkdir_calls, [])
        self.assertFalse(
            any("--parsable" in command for command in executor.commands)
        )

        dialog._save_button.click()
        selected = dialog.selected_preset()
        self.assertIs(selected.slurm_command_mode, SlurmCommandMode.AUTOMATIC)
        self.assertEqual(selected.slurm_bin_directory, "/usr/bin")

    def test_manual_verify_uses_only_the_configured_absolute_candidate(self) -> None:
        executor = FakeSchedulerExecutor()
        connection = FakeSchedulerConnection(executor)
        dialog = ClusterExecutionSettingsDialog(
            profile(),
            connection_service=connection,
        )
        dialog._manual_mode.setChecked(True)
        dialog._slurm_bin_directory.setText("/custom/slurm/bin")

        dialog._scheduler_check_button.click()
        self._wait_until(lambda: dialog._scheduler_check_button.isEnabled())

        self.assertEqual(
            executor.commands,
            [
                "/custom/slurm/bin/sbatch --version",
                "test -x /custom/slurm/bin/squeue && "
                "test -x /custom/slurm/bin/sacct && "
                "test -x /custom/slurm/bin/scancel",
            ],
        )
        self.assertEqual(dialog._sbatch_path.text(), "/custom/slurm/bin/sbatch")
        self.assertEqual(dialog._detection_method.text(), "Manual configuration")
        dialog._save_button.click()
        selected = dialog.selected_preset()
        self.assertIs(selected.slurm_command_mode, SlurmCommandMode.MANUAL)
        self.assertEqual(selected.slurm_bin_directory, "/custom/slurm/bin")

    def test_automatic_lsf_detection_updates_the_saved_scheduler_type(self) -> None:
        executor = FakeLsfSchedulerExecutor()
        dialog = ClusterExecutionSettingsDialog(
            profile(),
            connection_service=FakeSchedulerConnection(executor),
        )

        dialog._scheduler_check_button.click()
        self._wait_until(lambda: dialog._scheduler_check_button.isEnabled())

        self.assertEqual(dialog._scheduler_status.text(), "✓ LSF detected")
        self.assertEqual(dialog._scheduler_type.text(), "LSF")
        self.assertEqual(dialog._sbatch_path.text(), "/srv/moltage-test/lsf/current/bin/bsub")
        self.assertEqual(dialog._lsf_env_directory.text(), "/srv/moltage-test/lsf/conf")
        self.assertEqual(
            dialog._lsf_library_directory.text(), "/srv/moltage-test/lsf/current/lib"
        )
        self.assertEqual(
            dialog._lsf_server_directory.text(), "/srv/moltage-test/lsf/current/etc"
        )
        self.assertEqual(
            dialog._resource_stack.currentWidget().title(), "LSF resources"
        )
        self.assertFalse(dialog._cpus_per_task.isEnabled())
        dialog._lsf_hosts.setValue(2)
        dialog._lsf_ntasks.setValue(64)
        dialog._save_button.click()
        selected = dialog.selected_preset()
        self.assertIs(selected.scheduler_kind, SchedulerKind.LSF)
        self.assertEqual((selected.nodes, selected.ntasks), (2, 64))
        self.assertEqual(selected.cpus_per_task, 1)
        self.assertEqual(selected.omp_num_threads, 1)
        self.assertFalse(selected.unset_slurm_export_env)
        self.assertEqual(selected.lsf_env_directory, "/srv/moltage-test/lsf/conf")
        self.assertEqual(selected.lsf_library_directory, "/srv/moltage-test/lsf/current/lib")
        self.assertEqual(selected.lsf_server_directory, "/srv/moltage-test/lsf/current/etc")

    def test_manual_lsf_requires_and_persists_its_configuration_directory(self):
        executor = FakeLsfSchedulerExecutor()
        dialog = ClusterExecutionSettingsDialog(
            profile(),
            connection_service=FakeSchedulerConnection(executor),
        )
        dialog._manual_mode.setChecked(True)
        dialog._manual_scheduler_kind.setCurrentIndex(
            dialog._manual_scheduler_kind.findData(SchedulerKind.LSF.value)
        )
        dialog._slurm_bin_directory.setText("/srv/moltage-test/lsf/current/bin")
        dialog._lsf_env_directory.setText("/srv/moltage-test/lsf/conf")
        dialog._lsf_library_directory.setText("/srv/moltage-test/lsf/current/lib")
        dialog._lsf_server_directory.setText("/srv/moltage-test/lsf/current/etc")

        dialog._scheduler_check_button.click()
        self._wait_until(lambda: dialog._scheduler_check_button.isEnabled())

        self.assertEqual(dialog._scheduler_status.text(), "✓ LSF detected")
        self.assertIn("LSF_ENVDIR=/srv/moltage-test/lsf/conf", executor.commands[-1])
        self.assertIn("LSF_SERVERDIR=/srv/moltage-test/lsf/current/etc", executor.commands[-1])
        dialog._lsf_hosts.setValue(1)
        dialog._lsf_ntasks.setValue(1)
        dialog._save_button.click()
        selected = dialog.selected_preset()
        self.assertIs(selected.scheduler_kind, SchedulerKind.LSF)
        self.assertEqual(selected.lsf_env_directory, "/srv/moltage-test/lsf/conf")
        self.assertEqual(selected.lsf_library_directory, "/srv/moltage-test/lsf/current/lib")
        self.assertEqual(selected.lsf_server_directory, "/srv/moltage-test/lsf/current/etc")

    def test_transport_failure_uses_connection_presentation_in_both_modes(self):
        cases = (
            (
                SlurmCommandMode.AUTOMATIC,
                CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
            ),
            (
                SlurmCommandMode.MANUAL,
                "/custom/slurm/bin/sbatch --version",
            ),
        )
        for mode, expected_command in cases:
            with self.subTest(mode=mode):
                executor = TransportFailingSchedulerExecutor()
                dialog = ClusterExecutionSettingsDialog(
                    profile(),
                    connection_service=FakeSchedulerConnection(executor),
                )
                if mode is SlurmCommandMode.MANUAL:
                    dialog._manual_mode.setChecked(True)
                    dialog._slurm_bin_directory.setText("/custom/slurm/bin")

                with patch.object(QMessageBox, "critical") as critical:
                    dialog._scheduler_check_button.click()
                    self._wait_until(
                        lambda: dialog._scheduler_check_button.isEnabled()
                    )

                critical.assert_called_once()
                self.assertEqual(critical.call_args.args[1], "Connection failed")
                self.assertNotEqual(
                    critical.call_args.args[1],
                    "Scheduler not detected",
                )
                self.assertNotEqual(
                    critical.call_args.args[1],
                    "Scheduler configuration invalid",
                )
                self.assertEqual(executor.commands, [expected_command])
                self.assertNotIn(LOGIN_SHELL_DISCOVERY_COMMAND, executor.commands)
                dialog.close()

    def test_detection_can_retry_with_one_masked_temporary_password(self) -> None:
        executor = FakeSchedulerExecutor()
        connection = PasswordPromptingSchedulerConnection(executor)
        dialog = ClusterExecutionSettingsDialog(
            profile(save_password=False),
            connection_service=connection,
        )

        with patch.object(
            QInputDialog,
            "getText",
            return_value=("temporary-secret", True),
        ):
            dialog._scheduler_check_button.click()
            self._wait_until(lambda: len(connection.calls) == 2)
            self._wait_until(lambda: dialog._scheduler_check_button.isEnabled())

        self.assertEqual(connection.calls[0][1], None)
        self.assertEqual(connection.calls[1][1], "temporary-secret")
        self.assertEqual(dialog._sbatch_path.text(), "/usr/bin/sbatch")
        self.assertNotIn("temporary-secret", dialog._scheduler_status.text())
        dialog.close()

    def test_server_runtime_discovery_updates_only_the_working_runtime(self) -> None:
        executor = FakeSchedulerExecutor()
        connection = FakeSchedulerConnection(executor)
        dialog = ClusterExecutionSettingsDialog(
            profile(),
            connection_service=connection,
        )
        fhi_path = SYNTHETIC_FHI_EXECUTABLE
        aitranss_path = SYNTHETIC_AITRANSS_EXECUTABLE
        result = RuntimeDiscoveryResult(
            fhi_aims_candidates=(
                RuntimeCandidate(
                    module_name="chemistry/fhi-aims-example",
                    modules=("mpi/example-1.0", "chemistry/fhi-aims-example"),
                    command_v_path=fhi_path,
                    executable_path=fhi_path,
                    species_root_path=SPECIES_ROOT,
                    species_root_candidates=(SPECIES_ROOT,),
                ),
            ),
            aitranss_candidates=(
                RuntimeCandidate(
                    module_name="chemistry/aitranss-example",
                    modules=("chemistry/aitranss-example",),
                    command_v_path=aitranss_path,
                    executable_path=aitranss_path,
                ),
            ),
        )

        with patch(
            "moltage.gui.cluster_execution_dialog."
            "discover_server_runtimes",
            return_value=result,
        ) as discover:
            dialog._runtime_discovery_button.click()
            self._wait_until(lambda: dialog._runtime_discovery_button.isEnabled())

        discover.assert_called_once_with(
            executor,
            ("mpi/example-1.0", "chemistry/fhi-aims-example"),
        )
        self.assertEqual(dialog._fhi_runtime_path.text(), fhi_path)
        self.assertEqual(dialog._aitranss_runtime_path.text(), aitranss_path)
        self.assertEqual(dialog._runtime_status.text(), "✓ Server runtimes verified")
        self.assertEqual(
            dialog._launch_command.text(),
            "srun --cpu_bind=verbose " + fhi_path,
        )
        self.assertEqual(executor.mkdir_calls, [])

        dialog._save_button.click()

        self.assertEqual(
            dialog.selected_aitranss_runtime().executable_path,
            aitranss_path,
        )
        self.assertEqual(
            dialog.selected_aitranss_runtime().modules,
            ("chemistry/aitranss-example",),
        )

    def test_runtime_discovery_error_is_server_generic(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())

        with patch.object(QMessageBox, "critical") as critical:
            dialog._show_runtime_error(
                RuntimeDiscoveryError("No verified runtime module was found")
            )

        critical.assert_called_once_with(
            dialog,
            "Server runtime not detected",
            "No verified runtime module was found",
        )
        dialog.close()

    def test_multiple_verified_runtime_candidates_require_explicit_selection(self):
        dialog = ClusterExecutionSettingsDialog(profile())
        original_modules = tuple(
            dialog._modules.item(index).text()
            for index in range(dialog._modules.count())
        )
        original_launch = dialog._launch_command.text()
        executable = SYNTHETIC_FHI_EXECUTABLE
        result = RuntimeDiscoveryResult(
            fhi_aims_candidates=(
                RuntimeCandidate(
                    "fhi-aims/one",
                    ("mpi/example-1.0", "fhi-aims/one"),
                    executable,
                    executable,
                    species_root_path=SPECIES_ROOT,
                    species_root_candidates=(SPECIES_ROOT,),
                ),
                RuntimeCandidate(
                    "fhi-aims/two",
                    ("mpi/example-1.0", "fhi-aims/two"),
                    executable,
                    executable,
                    species_root_path=SPECIES_ROOT,
                    species_root_candidates=(SPECIES_ROOT,),
                ),
            ),
            aitranss_candidates=(
                RuntimeCandidate(
                    "fhi-aims/v3",
                    ("fhi-aims/v3",),
                    SYNTHETIC_AITRANSS_EXECUTABLE,
                    SYNTHETIC_AITRANSS_EXECUTABLE,
                ),
            ),
        )

        with patch.object(QInputDialog, "getItem", return_value=("", False)) as choose:
            dialog._runtime_succeeded(result)

        choose.assert_called_once()
        self.assertEqual(
            tuple(
                dialog._modules.item(index).text()
                for index in range(dialog._modules.count())
            ),
            original_modules,
        )
        self.assertEqual(dialog._launch_command.text(), original_launch)
        self.assertIn("not changed", dialog._runtime_status.text())
        dialog.close()

    def test_save_normalizes_runtime_and_memory_to_integers(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog._runtime_hours.setValue(1.5)
        dialog._memory_gb.setValue(64)
        dialog._save_button.click()

        selected = dialog.selected_preset()
        self.assertEqual(selected.runtime_minutes, 90)
        self.assertEqual(selected.memory_gb, 64)
        self.assertIsInstance(selected.runtime_minutes, int)
        self.assertIsInstance(selected.memory_gb, int)

    def test_empty_job_output_file_requires_confirmation_but_can_be_saved(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        dialog._slurm_output.clear()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            dialog._save_button.click()

        question.assert_called_once()
        self.assertEqual(
            question.call_args.args[2],
            "Output file name remains empty. Save anyway?",
        )
        with self.assertRaises(RuntimeError):
            dialog.selected_preset()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog._save_button.click()

        self.assertEqual(dialog.selected_preset().slurm_output_filename, "")

    def test_new_dialog_uses_safety_defaults_without_examplecluster_resource_defaults(self) -> None:
        unconfigured = replace(profile(), execution_preset=None)
        dialog = ClusterExecutionSettingsDialog(unconfigured)

        self.assertTrue(dialog._no_requeue.isChecked())
        self.assertTrue(dialog._export_none.isChecked())
        self.assertTrue(dialog._unset_slurm_export_env.isChecked())
        self.assertTrue(dialog._module_purge.isChecked())
        self.assertEqual(dialog._omp_num_threads.value(), 1)
        self.assertNotEqual(dialog._ntasks.value(), 24)
        self.assertNotEqual(dialog._runtime_hours.value(), 36.0)
        self.assertNotEqual(dialog._memory_gb.value(), 128)
        dialog.close()

    def test_module_add_edit_remove_preserve_order_only_in_working_copy(self) -> None:
        original = profile()
        dialog = ClusterExecutionSettingsDialog(original)
        with patch(
            "moltage.gui.cluster_execution_dialog.QInputDialog.getText",
            return_value=("intel/2024", True),
        ):
            dialog._add_module_button.click()
        dialog._modules.setCurrentRow(0)
        with patch(
            "moltage.gui.cluster_execution_dialog.QInputDialog.getText",
            return_value=("openmpi/5.0", True),
        ):
            dialog._edit_module_button.click()
        dialog._modules.setCurrentRow(1)
        dialog._remove_module_button.click()

        self.assertEqual(
            tuple(
                dialog._modules.item(index).text()
                for index in range(dialog._modules.count())
            ),
            ("openmpi/5.0", "intel/2024"),
        )
        self.assertEqual(
            original.execution_preset.modules,
            ("mpi/example-1.0", "chemistry/fhi-aims-example"),
        )
        dialog._save_button.click()
        self.assertEqual(
            dialog.selected_preset().modules,
            ("openmpi/5.0", "intel/2024"),
        )

    def test_unsafe_module_entry_is_rejected(self) -> None:
        dialog = ClusterExecutionSettingsDialog(profile())
        original_count = dialog._modules.count()
        with (
            patch(
                "moltage.gui.cluster_execution_dialog."
                "QInputDialog.getText",
                return_value=("mpi/example-1.0; rm -rf", True),
            ),
            patch.object(QMessageBox, "critical") as critical,
        ):
            dialog._add_module_button.click()

        self.assertEqual(dialog._modules.count(), original_count)
        critical.assert_called_once()
        dialog.close()

    def test_cancel_does_not_persist_working_runtime_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            service = ServerProfileService(repository, MemorySecretStore())
            saved = profile(save_password=False)
            service.save(saved)
            dialog = ClusterExecutionSettingsDialog(saved)
            dialog._runtime_hours.setValue(1.0)

            with patch.object(
                dialog,
                "_prompt_unsaved_changes",
                return_value="discard",
            ):
                dialog._cancel_button.click()

            reloaded = repository.load().profiles[0]
            self.assertEqual(reloaded.execution_preset.runtime_minutes, 2160)

    def _wait_until(self, condition, timeout=3.0) -> None:
        wait_until(condition, timeout, message="timed out waiting for scheduler worker result")


if __name__ == "__main__":
    unittest.main()
