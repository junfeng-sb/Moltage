from dataclasses import replace
import threading
import unittest

from moltage.app.connection_service import (
    AuthenticationError,
    AutoConnectUnavailableError,
    ConnectionTestError,
    PasswordRequiredError,
    ServerConnectionService,
)
from moltage.remote.executor import (
    RemoteAuthenticationError,
    RemoteDirectoryEntry,
    RemoteExecutorError,
    RemoteOperationStopped,
    RemoteOperationStopToken,
    RemotePathStat,
)
from phase2b1_test_support import MemorySecretStore, profile


class InspectingExecutor:
    def __init__(self, is_directory=True) -> None:
        self.connected = None
        self.is_directory = is_directory
        self.operations = []
        self.closed = False

    def connect(self, request):
        self.connected = request

    def close(self):
        self.closed = True

    def stat(self, path):
        self.operations.append(("stat", path))
        return RemotePathStat(self.is_directory)

    def list_directory(self, path):
        self.operations.append(("list", path))
        return (RemoteDirectoryEntry("existing", True),)

    def mkdir(self, path):
        self.operations.append(("mkdir", path))

    def read_bytes(self, path):
        raise AssertionError

    def write_bytes(self, path, data):
        self.operations.append(("write", path))

    def rename(self, source, destination):
        raise AssertionError

    def execute(self, command):
        raise AssertionError


class UnexpectedFailureExecutor(InspectingExecutor):
    def connect(self, request):
        raise ValueError("DO_NOT_PERSIST_OR_LOG_ME_84729")


class LeakyRemoteFailureExecutor(InspectingExecutor):
    def connect(self, request):
        raise RemoteExecutorError(
            "remote rejected " + request.password + "\nsecond detail"
        )


class AuthenticationFailureExecutor(InspectingExecutor):
    def connect(self, request):
        raise RemoteAuthenticationError(
            f"Password authentication failed for {request.profile.name}"
        )


class StopAwareExecutor(InspectingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.closed_event = threading.Event()

    def close(self):
        super().close()
        self.closed_event.set()


class RemoteOperationStopTokenTests(unittest.TestCase):
    def test_stop_does_not_wait_for_a_blocked_executor_close(self):
        started, release = threading.Event(), threading.Event()

        class BlockingCloseExecutor(InspectingExecutor):
            def close(self):
                started.set()
                release.wait(timeout=5)

        self.addCleanup(release.set)
        token = RemoteOperationStopToken()
        token.bind_executor(BlockingCloseExecutor())
        token.request_stop()
        self.assertTrue(token.is_requested)
        self.assertFalse(release.is_set())
        self.assertTrue(started.wait(timeout=1))
        with self.assertRaises(RemoteOperationStopped):
            token.checkpoint()

    def test_stop_request_closes_bound_executor_and_sets_checkpoint(self):
        executor = StopAwareExecutor()
        token = RemoteOperationStopToken()
        token.bind_executor(executor)

        token.request_stop()

        self.assertTrue(executor.closed_event.wait(timeout=1))
        self.assertTrue(token.is_requested)
        with self.assertRaises(RemoteOperationStopped):
            token.checkpoint()

    def test_stop_before_bind_rejects_and_closes_late_session(self):
        executor = StopAwareExecutor()
        token = RemoteOperationStopToken()
        token.request_stop()

        with self.assertRaises(RemoteOperationStopped):
            token.bind_executor(executor)

        self.assertTrue(executor.closed)


class ConnectionServiceTests(unittest.TestCase):
    def test_refresh_can_interrupt_connection_before_handshake_completes(self):
        started, release = threading.Event(), threading.Event()

        class ConnectingExecutor(StopAwareExecutor):
            def connect(self, request):
                started.set()
                release.wait(timeout=5)

            def close(self):
                super().close()
                release.set()

        executor = ConnectingExecutor()
        service = ServerConnectionService(MemorySecretStore(), lambda: executor)
        token = RemoteOperationStopToken()
        errors = []

        def connect():
            try:
                service.connect_for_remote_operation(profile(), "temporary-secret", stop_token=token)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=connect, daemon=True)
        worker.start()
        self.addCleanup(release.set)
        self.assertTrue(started.wait(timeout=1))
        token.request_stop()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RemoteOperationStopped)
        self.assertTrue(executor.closed)

    def test_connection_checks_existing_directory_by_sftp_without_writes(self) -> None:
        secrets = MemorySecretStore()
        item = profile()
        secrets.set_password(item.profile_id, "secret")
        executor = InspectingExecutor()
        service = ServerConnectionService(secrets, lambda: executor)

        result = service.test_connection(item)

        self.assertEqual(result.message, "Connected to ExampleCluster\nRoot: /srv/moltage-test/projects")
        self.assertEqual(
            executor.operations,
            [("stat", "/srv/moltage-test/projects"), ("list", "/srv/moltage-test/projects")],
        )
        self.assertTrue(executor.closed)
        self.assertNotIn("secret", repr(executor.connected))

    def test_auto_connect_is_on_demand_and_requires_policy_plus_saved_secret(self) -> None:
        secrets = MemorySecretStore()
        disabled = profile(auto=False)
        service = ServerConnectionService(secrets, InspectingExecutor)
        with self.assertRaises(AutoConnectUnavailableError):
            service.connect_automatically(disabled)

        enabled = profile(auto=True)
        with self.assertRaises(PasswordRequiredError):
            service.connect_automatically(enabled)
        secrets.set_password(enabled.profile_id, "secret")
        executor = service.connect_automatically(enabled)
        self.assertIsNotNone(executor.connected)
        executor.close()

    def test_profile_without_stored_password_remains_valid_but_prompts_on_use(self) -> None:
        service = ServerConnectionService(MemorySecretStore(), InspectingExecutor)
        item = profile(save_password=False)
        with self.assertRaisesRegex(PasswordRequiredError, "password is required"):
            service.test_connection(item)

    def test_connection_test_does_not_require_cluster_execution_settings(self) -> None:
        item = replace(profile(save_password=False), execution_preset=None)
        executor = InspectingExecutor()
        service = ServerConnectionService(MemorySecretStore(), lambda: executor)

        result = service.test_connection(item, supplied_password="secret")

        self.assertEqual(result.profile_name, "ExampleCluster")
        self.assertEqual(
            executor.operations,
            [("stat", "/srv/moltage-test/projects"), ("list", "/srv/moltage-test/projects")],
        )

    def test_explicit_remote_operation_uses_saved_password_without_auto_policy(self) -> None:
        secrets = MemorySecretStore()
        item = profile(auto=False, save_password=True)
        secrets.set_password(item.profile_id, "saved-secret")
        executor = InspectingExecutor()
        service = ServerConnectionService(secrets, lambda: executor)

        connected = service.connect_for_remote_operation(item)

        self.assertIs(connected, executor)
        self.assertEqual(executor.connected.password, "saved-secret")
        self.assertNotIn("saved-secret", repr(executor.connected))
        connected.close()

    def test_unexpected_connection_failure_does_not_expose_secret_text(self) -> None:
        item = profile(save_password=False)
        service = ServerConnectionService(
            MemorySecretStore(),
            UnexpectedFailureExecutor,
        )
        with self.assertRaises(ConnectionTestError) as caught:
            service.test_connection(
                item,
                supplied_password="DO_NOT_PERSIST_OR_LOG_ME_84729",
            )
        self.assertNotIn("DO_NOT_PERSIST_OR_LOG_ME_84729", str(caught.exception))

    def test_remote_adapter_failure_is_redacted_and_single_line(self) -> None:
        item = profile(save_password=False)
        service = ServerConnectionService(
            MemorySecretStore(),
            LeakyRemoteFailureExecutor,
        )
        with self.assertRaises(ConnectionTestError) as caught:
            service.connect_for_remote_operation(
                item,
                supplied_password="DO_NOT_PERSIST_OR_LOG_ME_84729",
            )
        self.assertNotIn("DO_NOT_PERSIST_OR_LOG_ME_84729", str(caught.exception))
        self.assertNotIn("second detail", str(caught.exception))

    def test_confirmed_password_rejection_preserves_authentication_type(self) -> None:
        item = profile(save_password=False)
        service = ServerConnectionService(
            MemorySecretStore(),
            AuthenticationFailureExecutor,
        )

        with self.assertRaises(AuthenticationError) as caught:
            service.connect_for_remote_operation(
                item,
                supplied_password="wrong-secret",
            )

        self.assertIn("Password authentication failed", str(caught.exception))
        self.assertNotIn("wrong-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
