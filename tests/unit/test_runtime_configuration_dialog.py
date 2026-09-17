"""Minimal Qt regressions for manual input, constrained discovery and Save/Cancel."""

from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QToolButton

from moltage.app.server_profiles import ServerProfileRepository, ServerProfileService
from moltage.domain.server_profile import (
    RuntimeDiscoveryHints, RuntimeEnvironment, RuntimeEnvironmentMode,
    RuntimeLocation, RuntimeLocationKind, SlurmAitranssLaunchMode,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.gui.cluster_execution_dialog import ClusterExecutionSettingsDialog, _RuntimeWorker
from moltage.gui.runtime_configuration_dialog import RuntimeConfigurationDialog
from moltage.gui.server_profiles_dialog import ServerProfilesDialog
from moltage.remote.runtime_discovery import RuntimeCandidate, RuntimeDiscoveryError, RuntimeDiscoveryResult
from moltage.remote.executor import RemoteCommandResult, RemoteExecutorError
from phase2b1_test_support import MemorySecretStore, profile
from test_runtime_configuration import AIMS, AITRANSS, MPI, NONE, SPECIES_ROOT


def manual_hints():
    return RuntimeDiscoveryHints(
        RuntimeLocation(AIMS, NONE, RuntimeLocationKind.EXECUTABLE), MPI,
        RuntimeLocation(AITRANSS, NONE, RuntimeLocationKind.EXECUTABLE),
        SPECIES_ROOT)


class RuntimeConfigurationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_compact_tabs_help_conditional_rows_and_enter_keeps_dialog_open(self):
        dialog = RuntimeConfigurationDialog(manual_hints())
        dialog.show()
        self.app.processEvents()
        self.assertEqual([dialog.tabs.tabText(i) for i in range(dialog.tabs.count())], ["FHI-aims", "AITRANSS"])
        helpers = [button for button in dialog.findChildren(QToolButton) if button.objectName().endswith("Help")]
        self.assertEqual(len(helpers), 14)
        self.assertTrue(all(not button.icon().isNull() and button.toolTip() for button in helpers))
        self.assertTrue(dialog.fhi._modules_row.isHidden())
        dialog.fhi.environment.setCurrentIndex(dialog.fhi.environment.findData(RuntimeEnvironmentMode.MODULES))
        self.assertFalse(dialog.fhi._modules_row.isHidden())
        dialog.fhi.modules.setText("mpi/1 aims/1")
        self.assertEqual(dialog.hints().fhi_aims.environment.modules, ("mpi/1", "aims/1"))
        dialog.fhi.location.setFocus()
        QTest.keyClick(dialog.fhi.location, Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.reject()

    def test_slurm_aitranss_launch_controls_do_not_guess_a_mode(self):
        dialog = RuntimeConfigurationDialog(manual_hints())

        self.assertIsNone(dialog.aitranss.aitranss_launch_mode.currentData())
        self.assertFalse(
            dialog.aitranss._form.isRowVisible(
                dialog.aitranss._aitranss_srun_row
            )
        )
        candidate = RuntimeCandidate(
            "NONE",
            (),
            AIMS,
            AIMS,
            NONE,
            launcher_candidates=("/opt/slurm/bin/srun", MPI),
            species_root_path=SPECIES_ROOT,
        )
        dialog.receive_result(RuntimeDiscoveryResult((candidate,), ()))

        self.assertIsNone(dialog.aitranss.aitranss_launch_mode.currentData())
        self.assertEqual(dialog.slurm_aitranss_srun_path(), None)
        dialog.aitranss.aitranss_launch_mode.setCurrentIndex(
            dialog.aitranss.aitranss_launch_mode.findData(
                SlurmAitranssLaunchMode.SRUN
            )
        )
        self.assertTrue(
            dialog.aitranss._form.isRowVisible(
                dialog.aitranss._aitranss_srun_row
            )
        )
        self.assertEqual(
            dialog.slurm_aitranss_srun_path(),
            "/opt/slurm/bin/srun",
        )
        self.assertIn("Verified", dialog.aitranss.aitranss_srun_status.text())
        dialog._apply()
        self.assertIs(
            dialog.selected_slurm_aitranss_launch_mode,
            SlurmAitranssLaunchMode.SRUN,
        )
        self.assertEqual(
            dialog.selected_slurm_aitranss_srun_path,
            "/opt/slurm/bin/srun",
        )

    def test_lsf_runtime_page_has_no_slurm_launch_controls(self):
        dialog = RuntimeConfigurationDialog(
            manual_hints(),
            scheduler_kind=SchedulerKind.LSF,
        )

        self.assertIsNone(dialog.aitranss.aitranss_launch_mode)
        self.assertIsNone(dialog.aitranss.aitranss_srun_path)
        dialog._apply()
        self.assertIsNone(dialog.selected_slurm_aitranss_launch_mode)
        self.assertIsNone(dialog.selected_slurm_aitranss_srun_path)

    def test_runtime_worker_verifies_only_the_exact_srun_candidate(self):
        class Executor:
            def __init__(self):
                self.commands = []
                self.closed = False

            def execute(self, command):
                self.commands.append(command)
                return RemoteCommandResult(0, b"", b"")

            def close(self):
                self.closed = True

        executor = Executor()

        class Connection:
            def connect_for_remote_operation(self, *_args):
                return executor

        worker = _RuntimeWorker(
            Connection(),
            profile(),
            (),
            None,
            manual_hints(),
            False,
            "/opt/slurm/bin/srun",
        )
        results = []
        worker.signals.succeeded.connect(results.append)
        with patch(
            "moltage.gui.cluster_execution_dialog.discover_runtime_configuration",
            return_value=RuntimeDiscoveryResult((), ()),
        ):
            worker.run()

        self.assertTrue(executor.closed)
        self.assertEqual(
            executor.commands,
            [
                "test -f /opt/slurm/bin/srun && test -r "
                "/opt/slurm/bin/srun && test -x /opt/slurm/bin/srun"
            ],
        )
        self.assertEqual(results[0].verified_srun_paths, ("/opt/slurm/bin/srun",))

    def test_full_manual_configuration_applies_without_discovery_and_cancels_locally(self):
        outer = ClusterExecutionSettingsDialog(profile())
        opened = []

        def reject_open_dialog():
            # Do not monkeypatch a virtual Qt ``exec`` method while constructing
            # that same Python Qt subclass.  PySide/Shiboken can enter an invalid
            # native dispatch path.  Drive the real modal lifecycle instead.
            dialog = QApplication.activeModalWidget()
            self.assertIsInstance(dialog, RuntimeConfigurationDialog)
            opened.append(dialog)
            dialog.reject()

        QTimer.singleShot(0, reject_open_dialog)
        outer._manual_runtime_button.click()
        self.assertEqual(len(opened), 1)
        dialog = RuntimeConfigurationDialog(manual_hints(), outer, discovery_available=False)
        self.assertFalse(dialog.find_button.isEnabled())
        dialog._apply()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.selected_fhi_runtime.executable_path, AIMS)
        self.assertEqual(dialog.selected_aitranss_runtime.environment, NONE)
        original = outer._profile
        outer._apply_manual_runtime(dialog)
        self.assertTrue(outer._launch_command.isReadOnly())
        outer._ntasks.setValue(6)
        self.assertEqual(outer._launch_command.text(), f"{MPI} -n 6 {AIMS}")
        self.assertEqual(original, outer._profile)
        with patch.object(
            outer,
            "_prompt_unsaved_changes",
            return_value="discard",
        ):
            outer.reject()
        self.assertEqual(original.execution_preset.fhi_runtime, None)

    def test_declining_setup_execution_does_not_start_ssh(self):
        outer = ClusterExecutionSettingsDialog(profile(), connection_service=object())
        hints = replace(manual_hints(), fhi_aims=RuntimeLocation(AIMS,
            RuntimeEnvironment(RuntimeEnvironmentMode.SCRIPT, setup_script="/opt/env/setup.sh"),
            RuntimeLocationKind.EXECUTABLE))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
             patch.object(outer, "_start_runtime_worker") as start:
            outer._begin_runtime_discovery(hints)
        start.assert_not_called()
        self.assertFalse(outer._allow_setup_scripts)
        outer.reject()

    def test_partial_search_fills_blanks_not_directory_launcher_or_explicit_environment(self):
        hints = RuntimeDiscoveryHints(
            RuntimeLocation("/opt/fhi-aims", NONE),
            MPI,
            fhi_species_defaults_path=SPECIES_ROOT,
        )
        dialog = RuntimeConfigurationDialog(hints)
        candidate = RuntimeCandidate(
            "NONE",
            (),
            AIMS,
            AIMS,
            NONE,
            "/usr/bin/srun",
            species_root_path=SPECIES_ROOT,
            species_root_candidates=(SPECIES_ROOT,),
        )
        dialog.receive_result(RuntimeDiscoveryResult((candidate,), (), ("AITRANSS not found",)))
        self.assertEqual(dialog.fhi.location.text(), "/opt/fhi-aims")
        self.assertEqual(dialog.fhi.launcher.text(), MPI)
        self.assertEqual(dialog.hints().fhi_aims.environment, NONE)
        self.assertEqual(dialog.aitranss.location.text(), "")
        dialog._apply()
        self.assertEqual(dialog.selected_fhi_runtime.executable_path, AIMS)
        self.assertIsNone(dialog.selected_aitranss_runtime)

    def test_edit_after_directory_resolution_does_not_apply_stale_binary(self):
        dialog = RuntimeConfigurationDialog(RuntimeDiscoveryHints(RuntimeLocation("/opt/fhi-aims", NONE), MPI))
        candidate = RuntimeCandidate("NONE", (), AIMS, AIMS, NONE, MPI)
        dialog.receive_result(RuntimeDiscoveryResult((candidate,), ()))
        dialog.fhi.location.setText("/another/install")
        with patch.object(QMessageBox, "warning") as warning:
            dialog._apply()
        warning.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(dialog.selected_hints)

    def test_unresolved_library_environment_is_not_auto_applied(self):
        dialog = RuntimeConfigurationDialog(RuntimeDiscoveryHints())
        candidate = RuntimeCandidate("NONE", (), AIMS, AIMS, NONE, MPI,
                                     ("Direct libraries not located: libmpi.so",), False)
        missing = (
            "Missing FHI-aims > Environment. Enter compatible modules manually.",
        )
        dialog.receive_result(RuntimeDiscoveryResult((candidate,), (), (), missing))
        self.assertEqual(dialog.fhi.location.text(), "")
        self.assertEqual(dialog.fhi.environment.currentData(), RuntimeEnvironmentMode.AUTO)
        self.assertIn("partial candidate was not auto-applied", dialog.status.toPlainText())
        self.assertIn(missing[0], dialog.status.toPlainText())
        with self.assertRaisesRegex(ValueError, "incomplete discovery candidate"):
            dialog.fhi.apply_candidate(candidate)

    def test_discovered_launcher_choices_fill_only_an_empty_field(self):
        dialog = RuntimeConfigurationDialog(RuntimeDiscoveryHints())
        candidate = RuntimeCandidate("NONE", (), AIMS, AIMS, NONE,
                                     launcher_candidates=("/usr/bin/srun", MPI),
                                     species_root_path=SPECIES_ROOT,
                                     species_root_candidates=(SPECIES_ROOT,))
        with patch.object(QInputDialog, "getItem", return_value=(MPI, True)) as choose:
            dialog.receive_result(RuntimeDiscoveryResult((candidate,), ()))
            dialog.receive_result(RuntimeDiscoveryResult((candidate,), ()))
        choose.assert_called_once()
        self.assertEqual(dialog.fhi.launcher.text(), MPI)

    def test_species_root_is_shared_by_discovery_manual_input_and_browse_signal(self):
        dialog = RuntimeConfigurationDialog(
            RuntimeDiscoveryHints(),
            discovery_available=True,
        )
        requested = []
        dialog.species_directory_requested.connect(requested.append)

        dialog.fhi.species_browse_button.click()
        self.assertEqual(requested, ["/"])

        candidate = RuntimeCandidate(
            "NONE",
            (),
            AIMS,
            AIMS,
            NONE,
            MPI,
            species_root_path=SPECIES_ROOT,
            species_root_candidates=(SPECIES_ROOT,),
        )
        dialog.receive_result(RuntimeDiscoveryResult((candidate,), ()))

        self.assertEqual(dialog.fhi.species_root.text(), SPECIES_ROOT)
        self.assertEqual(
            dialog.hints().fhi_species_defaults_path,
            SPECIES_ROOT,
        )

    def test_invalid_species_root_does_not_apply_or_lose_other_fields(self):
        dialog = RuntimeConfigurationDialog(manual_hints())
        original_launcher = dialog.fhi.launcher.text()
        original_aitranss = dialog.aitranss.location.text()
        dialog.fhi.species_root.setText("relative/species_defaults")

        with patch.object(QMessageBox, "warning") as warning:
            dialog._apply()

        warning.assert_called_once()
        self.assertIsNone(dialog.selected_hints)
        self.assertIsNone(dialog.selected_fhi_runtime)
        self.assertEqual(dialog.fhi.launcher.text(), original_launcher)
        self.assertEqual(dialog.aitranss.location.text(), original_aitranss)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_find_missing_uses_existing_off_thread_worker_and_transport_presentation(self):
        class Executor:
            def close(self):
                self.closed = True
        executor = Executor()
        class Connection:
            def connect_for_remote_operation(inner, *_args):
                inner.thread = QThread.currentThread()
                return executor
        connection = Connection()
        outer = ClusterExecutionSettingsDialog(profile(), connection_service=connection)
        child = RuntimeConfigurationDialog(manual_hints(), outer)
        outer._manual_dialog = child
        child.find_requested.connect(outer._find_manual_missing)
        failure = RemoteExecutorError("transport lost")
        with patch("moltage.gui.cluster_execution_dialog.discover_runtime_configuration", side_effect=failure) as discover, \
             patch("moltage.gui.cluster_execution_dialog.discover_server_runtimes") as automatic, \
             patch.object(QMessageBox, "critical") as critical:
            child.find_button.click()
            self.assertFalse(child.apply_button.isEnabled())
            deadline = time.monotonic() + 3
            while outer._runtime_workers and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)
            self.assertFalse(outer._runtime_workers)
        self.assertIsNot(connection.thread, self.app.thread())
        self.assertTrue(executor.closed)
        self.assertEqual(discover.call_args.args[1], manual_hints())
        automatic.assert_not_called()
        self.assertEqual(critical.call_args.args[1], "Connection failed")
        self.assertTrue(child.apply_button.isEnabled())
        self.assertEqual(child.hints(), manual_hints())
        outer._manual_dialog = None
        child.reject()
        outer.reject()

    def test_automatic_search_keeps_one_budget_and_never_retries_connection_failure(self):
        class Executor:
            def close(self):
                pass
        executor = Executor()
        class Connection:
            def connect_for_remote_operation(self, *_args):
                return executor
        for error in (RuntimeDiscoveryError("search exhausted"), RemoteExecutorError("connection lost")):
            worker = _RuntimeWorker(Connection(), profile(), (), None)
            errors = []
            worker.signals.failed.connect(errors.append)
            with patch("moltage.gui.cluster_execution_dialog.discover_server_runtimes", side_effect=error) as automatic, \
                 patch("moltage.gui.cluster_execution_dialog.discover_runtime_configuration",
                       return_value=RuntimeDiscoveryResult((), ())) as fallback:
                worker.run()
            automatic.assert_called_once()
            fallback.assert_not_called()
            self.assertEqual(errors, [error])

    def test_outer_save_and_connection_edit_preserve_new_runtime_fields(self):
        server = profile()
        outer = ClusterExecutionSettingsDialog(server)
        child = RuntimeConfigurationDialog(manual_hints(), outer)
        child._apply()
        outer._apply_manual_runtime(child)
        outer._save()
        configured = replace(server, execution_preset=outer.selected_preset(),
            aitranss_runtime=outer.selected_aitranss_runtime(), runtime_hints=outer.selected_runtime_hints())
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            service = ServerProfileService(repository, MemorySecretStore())
            service.save(configured)
            dialog = ServerProfilesDialog(service, None, None)
            dialog._name.setText("Renamed")
            edited = dialog._collect_profile()
            self.assertEqual(edited.runtime_hints, manual_hints())
            self.assertEqual(edited.execution_preset.fhi_runtime, configured.execution_preset.fhi_runtime)
            self.assertEqual(edited.aitranss_runtime, configured.aitranss_runtime)
            dialog.reject()


if __name__ == "__main__":
    unittest.main()
