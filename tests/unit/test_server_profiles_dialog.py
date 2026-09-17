from dataclasses import replace
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread, QTimer
from qt_test_support import wait_until
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QSpinBox,
)

from moltage.app.connection_service import (
    ConnectionTestError,
    ConnectionTestSuccess,
)
from moltage.app.server_profiles import (
    ServerProfileRepository,
    ServerProfileService,
)
from moltage.gui.server_profiles_dialog import (
    ServerProfilesDialog,
    _RemoteDirectoryWorker,
)
from moltage.remote.executor import RemoteDirectoryEntry
from moltage.remote.known_hosts import HostKeyInfo, UnknownHostKey
from phase2b1_test_support import MemorySecretStore, profile


SENTINEL = "DO_NOT_PERSIST_OR_LOG_ME_84729"


class FakeConnectionService:
    def __init__(self, outcomes=None) -> None:
        self.outcomes = list(outcomes or ())
        self.calls = []
        self.worker_threads = []

    def test_connection(self, server_profile, supplied_password=None):
        self.calls.append((server_profile, supplied_password))
        self.worker_threads.append(QThread.currentThread())
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return ConnectionTestSuccess(
            server_profile.name,
            server_profile.remote_project_root,
        )


class FakeKnownHosts:
    def __init__(self) -> None:
        self.trusted = []

    def trust(self, info):
        self.trusted.append(info)


class FakeDirectoryExecutor:
    def __init__(self) -> None:
        self.list_directory_calls = []
        self.close_calls = 0

    def list_directory(self, path):
        self.list_directory_calls.append(path)
        if path == "/srv/moltage-test/projects":
            return (
                RemoteDirectoryEntry("logs", True),
                RemoteDirectoryEntry("existing.txt", False),
            )
        if path == "/srv/moltage-test/projects/logs":
            return ()
        raise AssertionError(f"unexpected directory: {path}")

    def close(self):
        self.close_calls += 1


class FakeDirectoryConnection(FakeConnectionService):
    def __init__(self, executor) -> None:
        super().__init__()
        self.executor = executor
        self.remote_calls = []

    def connect_for_remote_operation(self, server_profile, supplied_password=None):
        self.remote_calls.append((server_profile, supplied_password))
        return self.executor


class ServerProfilesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.secrets = MemorySecretStore()
        self.repository = ServerProfileRepository(
            Path(self.temporary.name) / "profiles.json"
        )
        self.service = ServerProfileService(self.repository, self.secrets)
        self.saved_profile = profile()
        self.service.save(
            self.saved_profile,
            supplied_password=SENTINEL,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dialog_is_connection_only_with_clear_workspace_wording(self) -> None:
        connection = FakeConnectionService()
        dialog = ServerProfilesDialog(
            self.service,
            connection,
            FakeKnownHosts(),
        )
        labels = tuple(label.text() for label in dialog.findChildren(QLabel))

        self.assertEqual(dialog.windowTitle(), "Server Connections")
        self.assertEqual(dialog._profile_selector.currentText(), "ExampleCluster")
        self.assertEqual(dialog._host.text(), "cluster.example.org")
        self.assertEqual(dialog._remote_root.text(), "/srv/moltage-test/projects")
        self.assertIn("Remote project workspace:", labels)
        self.assertIn(
            "All calculation project folders will be created under this working "
            "directory.",
            labels,
        )
        self.assertEqual(
            dialog._browse_workspace_button.accessibleName(),
            "Browse remote project workspace",
        )
        self.assertEqual(dialog.findChildren(QSpinBox), [])
        self.assertEqual(dialog.findChildren(QDoubleSpinBox), [])
        self.assertEqual(dialog.findChildren(QListWidget), [])
        for removed_attribute in (
            "_nodes",
            "_ntasks",
            "_cpus_per_task",
            "_wall_time",
            "_runtime_hours",
            "_memory",
            "_memory_gb",
            "_omp_num_threads",
            "_modules",
            "_launch_command",
        ):
            self.assertFalse(hasattr(dialog, removed_attribute))
        self.assertEqual(dialog._cluster_button.text(), "Cluster Settings...")
        self.assertEqual(connection.calls, [])
        dialog.close()

    def test_remote_workspace_worker_lists_directories_only(self) -> None:
        executor = FakeDirectoryExecutor()
        connection = FakeDirectoryConnection(executor)
        worker = _RemoteDirectoryWorker(
            connection,
            self.saved_profile,
            "/srv/moltage-test/projects",
            None,
        )
        succeeded = []
        worker.signals.succeeded.connect(succeeded.append)

        worker.run()

        self.assertEqual(succeeded, [("/srv/moltage-test/projects", ("logs",))])
        self.assertEqual(executor.list_directory_calls, ["/srv/moltage-test/projects"])
        self.assertEqual(executor.close_calls, 1)

    def test_remote_workspace_browser_selects_only_an_existing_folder(self) -> None:
        executor = FakeDirectoryExecutor()
        connection = FakeDirectoryConnection(executor)
        dialog = ServerProfilesDialog(
            self.service,
            connection,
            FakeKnownHosts(),
        )

        def choose_when_loaded():
            picker = dialog._remote_directory_dialog
            if picker is None:
                QTimer.singleShot(10, choose_when_loaded)
                return
            if picker._path.text() == "/srv/moltage-test/projects" and picker._directories.count():
                picker._directories.itemDoubleClicked.emit(
                    picker._directories.item(0)
                )
                QTimer.singleShot(10, choose_when_loaded)
                return
            if (
                picker._path.text() == "/srv/moltage-test/projects/logs"
                and picker._select_button.isEnabled()
            ):
                self.assertIsNone(picker.findChild(QLineEdit, "remoteOutputFilename"))
                picker._select_button.click()
                return
            QTimer.singleShot(10, choose_when_loaded)

        QTimer.singleShot(0, choose_when_loaded)
        dialog._browse_workspace_button.click()

        self.assertEqual(dialog._remote_root.text(), "/srv/moltage-test/projects/logs")
        self.assertEqual(
            executor.list_directory_calls,
            ["/srv/moltage-test/projects", "/srv/moltage-test/projects/logs"],
        )
        self.assertEqual(len(connection.remote_calls), 2)
        dialog.close()

    def test_password_toggle_is_visual_only_and_placeholder_is_not_plaintext(self) -> None:
        dialog = ServerProfilesDialog(
            self.service,
            FakeConnectionService(),
            FakeKnownHosts(),
        )
        stored_before = dict(self.secrets.passwords)
        profile_json_before = self.repository.path.read_bytes()

        self.assertEqual(dialog._password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog._password.text(), "")
        self.assertEqual(
            dialog._password.placeholderText(),
            "Saved password available",
        )
        dialog._password_visibility_button.click()
        self.assertEqual(dialog._password.echoMode(), QLineEdit.EchoMode.Normal)
        self.assertEqual(dialog._password.text(), "")
        dialog._password_visibility_button.click()
        self.assertEqual(dialog._password.echoMode(), QLineEdit.EchoMode.Password)

        dialog._password.setText(SENTINEL)
        dialog._password_visibility_button.click()
        self.assertEqual(dialog._password.echoMode(), QLineEdit.EchoMode.Normal)
        self.assertEqual(dialog._password.text(), SENTINEL)
        dialog._password_visibility_button.click()
        self.assertEqual(dialog._password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(self.secrets.passwords, stored_before)
        self.assertEqual(self.repository.path.read_bytes(), profile_json_before)
        self.assertNotIn(SENTINEL, dialog._status.text())
        self.assertNotIn(SENTINEL, dialog._password_visibility_button.toolTip())
        dialog.close()

    def test_cluster_button_opens_independent_dialog(self) -> None:
        opened = []

        class RejectedClusterDialog:
            def __init__(inner_self, server_profile, parent=None, **_kwargs):
                opened.append((server_profile, parent))

            def exec(inner_self):
                return QDialog.DialogCode.Rejected

        dialog = ServerProfilesDialog(
            self.service,
            FakeConnectionService(),
            FakeKnownHosts(),
        )
        with patch(
            "moltage.gui.server_profiles_dialog."
            "ClusterExecutionSettingsDialog",
            RejectedClusterDialog,
        ):
            dialog._cluster_button.click()

        self.assertEqual(opened, [(self.saved_profile, dialog)])
        dialog.close()

    def test_accepted_cluster_dialog_persists_only_the_new_preset(self) -> None:
        updated_preset = replace(
            self.saved_profile.execution_preset,
            runtime_minutes=60,
            memory_gb=64,
        )

        class AcceptedClusterDialog:
            def __init__(inner_self, server_profile, parent=None, **_kwargs):
                inner_self.profile = server_profile

            def exec(inner_self):
                return QDialog.DialogCode.Accepted

            def selected_preset(inner_self):
                return updated_preset

            def selected_aitranss_runtime(inner_self):
                return inner_self.profile.aitranss_runtime

            def selected_runtime_hints(inner_self):
                return inner_self.profile.runtime_hints

        dialog = ServerProfilesDialog(
            self.service,
            FakeConnectionService(),
            FakeKnownHosts(),
        )
        with patch(
            "moltage.gui.server_profiles_dialog."
            "ClusterExecutionSettingsDialog",
            AcceptedClusterDialog,
        ):
            dialog._cluster_button.click()

        reloaded = self.repository.load().profiles[0]
        self.assertEqual(reloaded.execution_preset, updated_preset)
        self.assertEqual(
            reloaded.aitranss_runtime,
            self.saved_profile.aitranss_runtime,
        )
        self.assertEqual(reloaded.host, self.saved_profile.host)
        self.assertEqual(self.secrets.get_password(reloaded.profile_id), SENTINEL)
        dialog.close()

    def test_connection_save_preserves_cluster_settings(self) -> None:
        dialog = ServerProfilesDialog(
            self.service,
            FakeConnectionService(),
            FakeKnownHosts(),
        )
        dialog._name.setText("cluster_1")
        with patch.object(QMessageBox, "critical"):
            dialog._save_button.click()

        reloaded = self.repository.load().profiles[0]
        self.assertEqual(reloaded.name, "cluster_1")
        self.assertEqual(reloaded.execution_preset.runtime_minutes, 2160)
        self.assertEqual(reloaded.execution_preset.memory_gb, 128)
        self.assertEqual(reloaded.execution_preset.ntasks, 24)
        self.assertEqual(
            reloaded.aitranss_runtime,
            self.saved_profile.aitranss_runtime,
        )
        dialog.close()

    def test_connection_save_preserves_notification_settings(self) -> None:
        enabled = replace(
            self.saved_profile,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        self.service.save(enabled)
        dialog = ServerProfilesDialog(
            self.service,
            FakeConnectionService(),
            FakeKnownHosts(),
        )
        dialog._host.setText("renamed.example.org")

        with patch.object(QMessageBox, "critical"):
            dialog._save_button.click()

        reloaded = self.repository.load().profiles[0]
        self.assertTrue(reloaded.email_notification_enabled)
        self.assertEqual(
            reloaded.email_notification_recipient,
            "user@example.com",
        )
        self.assertEqual(reloaded.host, "renamed.example.org")
        dialog.close()

    def test_save_persists_auto_connect_and_disabling_save_deletes_secret(self) -> None:
        dialog = ServerProfilesDialog(
            self.service,
            FakeConnectionService(),
            FakeKnownHosts(),
        )
        dialog._auto_connect.setChecked(False)
        dialog._save_password.setChecked(False)
        dialog._host.setText("renamed.example.org")
        with patch.object(QMessageBox, "critical"):
            dialog._save_button.click()

        reloaded = self.repository.load().profiles[0]
        self.assertFalse(reloaded.auto_connect)
        self.assertFalse(reloaded.save_password)
        self.assertEqual(reloaded.host, "renamed.example.org")
        self.assertNotIn(reloaded.profile_id, self.secrets.passwords)
        dialog.close()

    def test_connection_runs_off_gui_thread_without_execution_preset(self) -> None:
        connection = FakeConnectionService()
        dialog = ServerProfilesDialog(
            self.service,
            connection,
            FakeKnownHosts(),
        )
        dialog._editing_execution_preset = None
        with patch.object(QMessageBox, "information") as information:
            dialog._test_button.click()
            self._wait_until(lambda: len(connection.calls) == 1)
            self._wait_until(lambda: dialog._test_button.isEnabled())

        tested_profile = connection.calls[0][0]
        self.assertIsNone(tested_profile.execution_preset)
        self.assertIsNot(connection.worker_threads[0], self.application.thread())
        self.assertEqual(
            dialog._status.text(),
            "Connected to ExampleCluster\nRoot: /srv/moltage-test/projects",
        )
        information.assert_called_once()
        dialog.close()

    def test_connection_test_does_not_disable_cluster_settings(self) -> None:
        entered = Event()
        release = Event()
        opened = []

        class BlockingConnectionService(FakeConnectionService):
            def test_connection(self, server_profile, supplied_password=None):
                entered.set()
                if not release.wait(timeout=3.0):
                    raise RuntimeError("connection-test release timed out")
                return super().test_connection(server_profile, supplied_password)

        class RejectedClusterDialog:
            def __init__(inner_self, server_profile, parent=None, **_kwargs):
                opened.append((server_profile, parent))

            def exec(inner_self):
                return QDialog.DialogCode.Rejected

        connection = BlockingConnectionService()
        dialog = ServerProfilesDialog(
            self.service,
            connection,
            FakeKnownHosts(),
        )
        try:
            with (
                patch.object(QMessageBox, "information"),
                patch(
                    "moltage.gui.server_profiles_dialog."
                    "ClusterExecutionSettingsDialog",
                    RejectedClusterDialog,
                ),
            ):
                dialog._test_button.click()
                self._wait_until(entered.is_set)

                self.assertFalse(dialog._test_button.isEnabled())
                self.assertTrue(dialog._cluster_button.isEnabled())
                dialog._cluster_button.click()
                self.assertEqual(opened, [(self.saved_profile, dialog)])

                release.set()
                self._wait_until(lambda: dialog._test_button.isEnabled())
                self.assertTrue(dialog._cluster_button.isEnabled())
        finally:
            release.set()
            self._wait_until(lambda: not dialog._workers)
            dialog.close()

    def test_unknown_host_can_be_trusted_once_then_retried(self) -> None:
        info = HostKeyInfo(
            "cluster.example.org",
            22,
            "ssh-ed25519",
            "SHA256:synthetic",
            "synthetic-public-material",
        )
        connection = FakeConnectionService(
            (
                UnknownHostKey(info),
                ConnectionTestSuccess("ExampleCluster", "/srv/moltage-test/projects"),
            )
        )
        known_hosts = FakeKnownHosts()
        dialog = ServerProfilesDialog(self.service, connection, known_hosts)
        with (
            patch.object(dialog, "_confirm_unknown_host", return_value=True),
            patch.object(QMessageBox, "information"),
        ):
            dialog._test_button.click()
            self._wait_until(lambda: len(connection.calls) == 2)
            self._wait_until(lambda: dialog._test_button.isEnabled())

        self.assertEqual(known_hosts.trusted, [info])
        self.assertEqual(
            dialog._status.text(),
            "Connected to ExampleCluster\nRoot: /srv/moltage-test/projects",
        )
        dialog.close()

    def test_failure_is_sanitized_into_status_and_message(self) -> None:
        connection = FakeConnectionService((ConnectionTestError("clean failure"),))
        dialog = ServerProfilesDialog(
            self.service,
            connection,
            FakeKnownHosts(),
        )
        with patch.object(QMessageBox, "critical") as critical:
            dialog._test_button.click()
            self._wait_until(lambda: dialog._status.text() == "clean failure")
            self._wait_until(lambda: dialog._test_button.isEnabled())
        critical.assert_called_once_with(dialog, "Connection failed", "clean failure")
        dialog.close()

    def _wait_until(self, condition, timeout=3.0) -> None:
        wait_until(condition, timeout, message="timed out waiting for Qt worker result")


if __name__ == "__main__":
    unittest.main()
