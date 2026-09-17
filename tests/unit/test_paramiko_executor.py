from collections import deque
import errno
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import paramiko

from moltage.domain.server_profile import ServerProfile, SlurmExecutionPreset
from moltage.remote.executor import (
    ConnectionRequest,
    RemoteAuthenticationError,
    RemoteCommandOutcomeUnknown,
    RemoteExecutorError,
    RemotePathAlreadyExistsError,
    RemotePathNotFoundError,
)
from moltage.remote.known_hosts import HostKeyMismatch, KnownHostStore, UnknownHostKey
from moltage.remote.paramiko_executor import ParamikoRemoteExecutor


SENTINEL = "DO_NOT_PERSIST_OR_LOG_ME_84729"


def profile():
    return ServerProfile(
        uuid4(),
        "Test",
        "cluster.example.org",
        2222,
        "user",
        "/work",
        False,
        False,
        SlurmExecutionPreset(
            nodes=1,
            ntasks=1,
            cpus_per_task=1,
            runtime_minutes=60,
            memory_gb=1,
            modules=("chemistry/fhi-aims-example",),
            launch_command="srun aims.x",
            slurm_output_filename="aims.out",
        ),
    )


class AuthenticationFailingClient:
    def __init__(self) -> None:
        self.kwargs = None
        self.policy = None
        self.closed = False

    def load_host_keys(self, path):
        self.loaded_path = path

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.kwargs = kwargs
        raise paramiko.AuthenticationException(SENTINEL)

    def close(self):
        self.closed = True


class ExistingPathSftp:
    def __init__(self, error=None):
        self.error = error or OSError(errno.EEXIST, "exists")
        self.stat_calls = []

    def mkdir(self, path):
        raise self.error

    def stat(self, path):
        self.stat_calls.append(path)
        return object()


class MissingPathSftp(ExistingPathSftp):
    def stat(self, path):
        self.stat_calls.append(path)
        raise OSError(errno.ENOENT, "missing")


class DispatchLosingClient:
    def __init__(self):
        self.calls = []

    def exec_command(self, command):
        self.calls.append(command)
        raise EOFError("connection disappeared")


class _CommandStream:
    def __init__(self, channel):
        self.channel = channel

    def read(self, *_args, **_kwargs):
        raise AssertionError("execute must drain the underlying channel")


class ScriptedCommandClient:
    def __init__(self, channel):
        self.channel = channel
        self.calls = []
        self.stdout = _CommandStream(channel)
        self.stderr = _CommandStream(channel)

    def exec_command(self, command):
        self.calls.append(command)
        return object(), self.stdout, self.stderr


class ScriptedCommandChannel:
    def __init__(
        self,
        events=(),
        *,
        exit_status=0,
        exit_ready_while_buffered=False,
        receive_failure=None,
        exit_status_failure=None,
        close_failure=None,
    ):
        self._events = deque((stream, bytes(data)) for stream, data in events)
        self._exit_status = exit_status
        self._exit_ready_while_buffered = exit_ready_while_buffered
        self._receive_failure = receive_failure
        self._exit_status_failure = exit_status_failure
        self._close_failure = close_failure
        self.eof_received = not self._events
        self.closed = False
        self.received_stdout = 0
        self.received_stderr = 0
        self.received_at_exit_status = None
        self.receive_order = []
        self.exit_ready_observed_with_buffered_output = False
        self.exit_status_calls = 0
        self.close_calls = 0

    def recv_ready(self):
        return bool(self._events and self._events[0][0] == "stdout")

    def recv_stderr_ready(self):
        return bool(self._events and self._events[0][0] == "stderr")

    def recv(self, nbytes):
        if self._receive_failure == "stdout":
            raise EOFError("stdout transport lost")
        chunk = self._receive("stdout", nbytes)
        self.received_stdout += len(chunk)
        return chunk

    def recv_stderr(self, nbytes):
        if self._receive_failure == "stderr":
            raise EOFError("stderr transport lost")
        chunk = self._receive("stderr", nbytes)
        self.received_stderr += len(chunk)
        return chunk

    def _receive(self, expected_stream, nbytes):
        if not self._events or self._events[0][0] != expected_stream:
            raise AssertionError(f"{expected_stream} was not ready")
        stream, data = self._events[0]
        chunk = data[:nbytes]
        remaining = data[nbytes:]
        if remaining:
            self._events[0] = (stream, remaining)
        else:
            self._events.popleft()
            if not self._events:
                self.eof_received = True
        self.receive_order.append(expected_stream)
        return chunk

    def exit_status_ready(self):
        if self._exit_ready_while_buffered and self._events:
            self.exit_ready_observed_with_buffered_output = True
        return self._exit_ready_while_buffered or not self._events

    def recv_exit_status(self):
        self.exit_status_calls += 1
        self.received_at_exit_status = self.received_stdout + self.received_stderr
        if self._exit_status_failure is not None:
            raise self._exit_status_failure
        return self._exit_status

    def close(self):
        self.close_calls += 1
        self.closed = True
        if self._close_failure is not None:
            raise self._close_failure


class ReadinessFailingCommandChannel(ScriptedCommandChannel):
    def recv_ready(self):
        raise EOFError("channel readiness check failed")


class LateOutputCommandChannel(ScriptedCommandChannel):
    def __init__(self):
        super().__init__(
            (
                ("stdout", b"late-out\n"),
                ("stderr", b"late-err\n"),
            ),
            exit_ready_while_buffered=True,
        )
        self._readiness_checks = 0
        self.empty_readiness_observations = 0

    def _readiness_suppressed(self):
        self._readiness_checks += 1
        if self._readiness_checks <= 4:
            self.empty_readiness_observations += 1
            return True
        return False

    def recv_ready(self):
        if self._readiness_suppressed():
            return False
        return super().recv_ready()

    def recv_stderr_ready(self):
        if self._readiness_suppressed():
            return False
        return super().recv_stderr_ready()


class _TailFile:
    def __init__(self, data):
        self._data = data
        self.position = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def seek(self, position):
        self.position = position

    def read(self, size):
        return self._data[self.position : self.position + size]


class TailSftp:
    def __init__(self, data=b"0123456789"):
        self.data = data
        self.opened = []
        self.file = _TailFile(data)

    def stat(self, path):
        return type("Stat", (), {"st_size": len(self.data)})()

    def open(self, path, mode):
        self.opened.append((path, mode))
        return self.file


class MissingTailSftp(TailSftp):
    def stat(self, path):
        raise OSError(errno.ENOENT, "missing")


class DisconnectedTailSftp(TailSftp):
    def stat(self, path):
        raise EOFError("transport ended")


class DownloadingSftp:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def get(self, path, destination, callback=None):
        self.calls.append((path, destination))
        midpoint = len(self.data) // 2
        Path(destination).write_bytes(self.data)
        if callback is not None:
            callback(midpoint, len(self.data))
            callback(len(self.data), len(self.data))


class CloseRecorder:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def close(self):
        self.calls += 1
        if self.error is not None:
            raise self.error


class ParamikoExecutorTests(unittest.TestCase):
    def test_closed_channel_without_exit_status_is_unknown_without_polling(self):
        class ClosedChannel(ScriptedCommandChannel):
            def __init__(self):
                super().__init__()
                self.closed = True
                self.polls = 0

            def exit_status_ready(self):
                self.polls += 1
                if self.polls > 10:
                    raise AssertionError("must not keep polling a closed channel")
                return False

        channel = ClosedChannel()
        client = ScriptedCommandClient(channel)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(KnownHostStore(Path(directory) / "known_hosts"))
            executor._client = client
            with self.assertRaises(RemoteCommandOutcomeUnknown):
                executor.execute("synthetic status query")
        self.assertEqual(channel.polls, 1)
        self.assertEqual(client.calls, ["synthetic status query"])
        self.assertEqual(channel.exit_status_calls, 0)

    def _execute_scripted_channel(self, channel, command="test-command"):
        client = ScriptedCommandClient(channel)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._client = client
            result = executor.execute(command)
        return result, client

    def test_execute_preserves_small_stdout_exactly(self) -> None:
        channel = ScriptedCommandChannel((("stdout", b"hello\n"),))

        result, client = self._execute_scripted_channel(channel)

        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.stdout, b"hello\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(client.calls, ["test-command"])
        self.assertEqual(channel.close_calls, 1)

    def test_execute_preserves_small_stderr_and_nonzero_status(self) -> None:
        channel = ScriptedCommandChannel(
            (("stderr", b"not found\n"),),
            exit_status=127,
        )

        result, _client = self._execute_scripted_channel(channel)

        self.assertEqual(result.exit_status, 127)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"not found\n")
        self.assertEqual(channel.close_calls, 1)

    def test_execute_returns_known_success_when_channel_close_fails(self) -> None:
        channel = ScriptedCommandChannel(
            (("stdout", b"job 123\n"),),
            close_failure=EOFError("cleanup transport lost"),
        )

        result, client = self._execute_scripted_channel(
            channel,
            "sbatch --parsable submit.sh",
        )

        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.stdout, b"job 123\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(client.calls, ["sbatch --parsable submit.sh"])
        self.assertEqual(channel.exit_status_calls, 1)
        self.assertEqual(channel.close_calls, 1)

    def test_execute_returns_known_nonzero_when_channel_close_fails(self) -> None:
        channel = ScriptedCommandChannel(
            (("stderr", b"invalid option\n"),),
            exit_status=2,
            close_failure=EOFError("cleanup transport lost"),
        )

        result, client = self._execute_scripted_channel(channel, "bad-command")

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"invalid option\n")
        self.assertEqual(client.calls, ["bad-command"])
        self.assertEqual(channel.exit_status_calls, 1)
        self.assertEqual(channel.close_calls, 1)

    def test_execute_drains_interleaved_stdout_and_stderr_without_starvation(self) -> None:
        channel = ScriptedCommandChannel(
            (
                ("stdout", b"out-1\n"),
                ("stderr", b"err-1\n"),
                ("stdout", b"out-2\n"),
                ("stderr", b"err-2\n"),
            )
        )

        result, _client = self._execute_scripted_channel(channel)

        self.assertEqual(result.stdout, b"out-1\nout-2\n")
        self.assertEqual(result.stderr, b"err-1\nerr-2\n")
        self.assertEqual(
            channel.receive_order,
            ["stdout", "stderr", "stdout", "stderr"],
        )
        self.assertEqual(channel.close_calls, 1)

    def test_execute_drains_output_larger_than_two_mib_before_exit_status(self) -> None:
        large_stdout = b"L" * (2 * 1024 * 1024 + 137)
        channel = ScriptedCommandChannel((("stdout", large_stdout),))

        result, _client = self._execute_scripted_channel(channel)

        self.assertEqual(result.stdout, large_stdout)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(channel.received_at_exit_status, len(large_stdout))
        self.assertGreater(len(channel.receive_order), 1)
        self.assertEqual(channel.close_calls, 1)

    def test_execute_drains_final_bytes_when_exit_is_already_ready(self) -> None:
        channel = ScriptedCommandChannel(
            (
                ("stdout", b"first-"),
                ("stderr", b"warning-"),
                ("stdout", b"final"),
                ("stderr", b"final"),
            ),
            exit_ready_while_buffered=True,
        )

        result, _client = self._execute_scripted_channel(channel)

        self.assertTrue(channel.exit_ready_observed_with_buffered_output)
        self.assertEqual(result.stdout, b"first-final")
        self.assertEqual(result.stderr, b"warning-final")
        self.assertEqual(channel.received_at_exit_status, 24)
        self.assertEqual(channel.close_calls, 1)

    def test_execute_waits_through_empty_poll_until_eof_and_late_output(self) -> None:
        channel = LateOutputCommandChannel()

        result, client = self._execute_scripted_channel(channel)

        self.assertEqual(channel.empty_readiness_observations, 4)
        self.assertTrue(channel.exit_ready_observed_with_buffered_output)
        self.assertTrue(channel.eof_received)
        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.stdout, b"late-out\n")
        self.assertEqual(result.stderr, b"late-err\n")
        self.assertEqual(channel.received_at_exit_status, 18)
        self.assertEqual(client.calls, ["test-command"])
        self.assertEqual(channel.close_calls, 1)

    def test_execute_handles_zero_output(self) -> None:
        channel = ScriptedCommandChannel(exit_status=0)

        result, _client = self._execute_scripted_channel(channel)

        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(channel.exit_status_calls, 1)
        self.assertEqual(channel.close_calls, 1)

    def test_execute_receive_failure_is_unknown_closed_and_not_retried(self) -> None:
        channel = ScriptedCommandChannel(
            (("stdout", b"partial"),),
            receive_failure="stdout",
        )
        client = ScriptedCommandClient(channel)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._client = client

            with self.assertRaises(RemoteCommandOutcomeUnknown):
                executor.execute("sbatch --parsable submit.sh")

        self.assertEqual(client.calls, ["sbatch --parsable submit.sh"])
        self.assertEqual(channel.close_calls, 1)

    def test_execute_readiness_failure_is_unknown_closed_and_not_retried(self) -> None:
        channel = ReadinessFailingCommandChannel()
        client = ScriptedCommandClient(channel)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._client = client

            with self.assertRaises(RemoteCommandOutcomeUnknown):
                executor.execute("squeue")

        self.assertEqual(client.calls, ["squeue"])
        self.assertEqual(channel.close_calls, 1)

    def test_execute_exit_status_failure_is_unknown_closed_and_not_retried(self) -> None:
        channel = ScriptedCommandChannel(
            exit_status_failure=EOFError("exit status transport lost")
        )
        client = ScriptedCommandClient(channel)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._client = client

            with self.assertRaises(RemoteCommandOutcomeUnknown):
                executor.execute("sacct")

        self.assertEqual(client.calls, ["sacct"])
        self.assertEqual(channel.exit_status_calls, 1)
        self.assertEqual(channel.close_calls, 1)

    def test_close_attempts_client_and_clears_state_after_sftp_failure(self) -> None:
        sftp_error = RemoteExecutorError("synthetic SFTP close failure")
        sftp = CloseRecorder(sftp_error)
        client = CloseRecorder()
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            executor._client = client

            with self.assertRaises(RemoteExecutorError) as caught:
                executor.close()

            self.assertIs(caught.exception, sftp_error)
            self.assertEqual(sftp.calls, 1)
            self.assertEqual(client.calls, 1)
            self.assertIsNone(executor._sftp)
            self.assertIsNone(executor._client)
            executor.close()
            self.assertEqual(sftp.calls, 1)
            self.assertEqual(client.calls, 1)

    def test_close_clears_state_after_client_failure(self) -> None:
        client_error = RemoteExecutorError("synthetic SSH client close failure")
        sftp = CloseRecorder()
        client = CloseRecorder(client_error)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            executor._client = client

            with self.assertRaises(RemoteExecutorError) as caught:
                executor.close()

            self.assertIs(caught.exception, client_error)
            self.assertEqual(sftp.calls, 1)
            self.assertEqual(client.calls, 1)
            self.assertIsNone(executor._sftp)
            self.assertIsNone(executor._client)

    def test_close_preserves_first_failure_when_both_resources_fail(self) -> None:
        sftp_error = RemoteExecutorError("first cleanup failure")
        client_error = RemoteExecutorError("second cleanup failure")
        sftp = CloseRecorder(sftp_error)
        client = CloseRecorder(client_error)
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            executor._client = client

            with self.assertRaises(RemoteExecutorError) as caught:
                executor.close()

            self.assertIs(caught.exception, sftp_error)
            self.assertEqual(sftp.calls, 1)
            self.assertEqual(client.calls, 1)
            self.assertIsNone(executor._sftp)
            self.assertIsNone(executor._client)

    def test_close_success_remains_orderly_and_idempotent(self) -> None:
        order = []

        class OrderedCloseRecorder(CloseRecorder):
            def __init__(self, name):
                super().__init__()
                self.name = name

            def close(self):
                super().close()
                order.append(self.name)

        sftp = OrderedCloseRecorder("sftp")
        client = OrderedCloseRecorder("client")
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            executor._client = client

            executor.close()
            executor.close()

            self.assertEqual(order, ["sftp", "client"])
            self.assertEqual(sftp.calls, 1)
            self.assertEqual(client.calls, 1)
            self.assertIsNone(executor._sftp)
            self.assertIsNone(executor._client)

    def test_read_file_tail_seeks_and_bounds_the_sftp_read(self) -> None:
        sftp = TailSftp(b"prefix-important-tail")
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            result = executor.read_file_tail("/work/aims.out", 14)

        self.assertEqual(result, b"important-tail")
        self.assertEqual(sftp.file.position, len(sftp.data) - 14)
        self.assertEqual(sftp.opened, [("/work/aims.out", "rb")])

    def test_read_file_head_bounds_the_sftp_read_without_stat_or_tail_seek(self) -> None:
        sftp = TailSftp(b"important-header-and-large-body")
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            result = executor.read_file_head("/work/mos.aims", 16)

        self.assertEqual(result, b"important-header")
        self.assertEqual(sftp.file.position, 0)
        self.assertEqual(sftp.opened, [("/work/mos.aims", "rb")])

    def test_download_file_streams_to_disk_and_reports_byte_progress(self) -> None:
        sftp = DownloadingSftp(b"0123456789")
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            destination = Path(directory) / "density.cube.tmp"
            executor.download_file(
                "/work/density.cube",
                str(destination),
                lambda current, total: progress.append((current, total)),
            )
            self.assertEqual(destination.read_bytes(), sftp.data)

        self.assertEqual(progress, [(5, 10), (10, 10)])
        self.assertEqual(sftp.calls[0][0], "/work/density.cube")

    def test_download_progress_failure_does_not_change_transfer(self) -> None:
        sftp = DownloadingSftp(b"result")
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            destination = Path(directory) / "result.tmp"
            executor.download_file(
                "/work/result",
                str(destination),
                lambda _current, _total: (_ for _ in ()).throw(RuntimeError()),
            )
            self.assertEqual(destination.read_bytes(), b"result")

    def test_read_file_tail_preserves_missing_path_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = MissingTailSftp()
            with self.assertRaises(RemotePathNotFoundError):
                executor.read_file_tail("/work/aims.out", 256 * 1024)

    def test_read_file_tail_normalizes_transport_loss_as_remote_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = DisconnectedTailSftp()
            with self.assertRaises(RemoteExecutorError) as caught:
                executor.read_file_tail("/work/aims.out", 256 * 1024)
        self.assertNotIsInstance(caught.exception, RemotePathNotFoundError)

    def test_password_only_flags_and_failure_text_are_secret_safe(self) -> None:
        client = AuthenticationFailingClient()
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts"),
                client_factory=lambda: client,
            )
            request = ConnectionRequest(profile(), SENTINEL)
            self.assertNotIn(SENTINEL, repr(request))
            with self.assertRaises(RemoteExecutorError) as caught:
                executor.connect(request)

        self.assertFalse(client.kwargs["allow_agent"])
        self.assertFalse(client.kwargs["look_for_keys"])
        self.assertEqual(client.kwargs["password"], SENTINEL)
        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertIsInstance(caught.exception, RemoteAuthenticationError)
        self.assertNotIsInstance(client.policy, paramiko.AutoAddPolicy)
        self.assertTrue(client.closed)

    def test_missing_key_policy_distinguishes_unknown_from_known_mismatch(self) -> None:
        first_key = paramiko.RSAKey.generate(1024)
        second_key = paramiko.RSAKey.generate(1024)
        client = AuthenticationFailingClient()
        with tempfile.TemporaryDirectory() as directory:
            store = KnownHostStore(Path(directory) / "known_hosts")
            executor = ParamikoRemoteExecutor(store, client_factory=lambda: client)
            with self.assertRaises(RemoteExecutorError):
                executor.connect(ConnectionRequest(profile(), SENTINEL))

            with self.assertRaises(UnknownHostKey) as unknown:
                client.policy.missing_host_key(None, "ignored", first_key)
            store.trust(unknown.exception.info)
            with self.assertRaises(HostKeyMismatch):
                client.policy.missing_host_key(None, "ignored", second_key)

    def test_mkdir_normalizes_only_eexist_as_a_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = ExistingPathSftp()
            with self.assertRaises(RemotePathAlreadyExistsError):
                executor.mkdir("/work/MoleculeA.20300102")

    def test_generic_sftp_failure_uses_only_post_mkdir_stat_to_confirm_collision(self) -> None:
        sftp = ExistingPathSftp(OSError("Failure"))
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            with self.assertRaises(RemotePathAlreadyExistsError):
                executor.mkdir("/work/MoleculeA.20300102")
        self.assertEqual(sftp.stat_calls, ["/work/MoleculeA.20300102"])

    def test_generic_sftp_failure_without_existing_path_is_not_a_collision(self) -> None:
        sftp = MissingPathSftp(OSError("Failure"))
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._sftp = sftp
            with self.assertRaises(RemoteExecutorError) as caught:
                executor.mkdir("/work/MoleculeA.20300102")
        self.assertNotIsInstance(caught.exception, RemotePathAlreadyExistsError)

    def test_any_loss_after_execute_dispatch_is_unknown_and_not_retried(self) -> None:
        client = DispatchLosingClient()
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            executor._client = client
            with self.assertRaises(RemoteCommandOutcomeUnknown):
                executor.execute("sbatch --parsable submit.sh")
        self.assertEqual(client.calls, ["sbatch --parsable submit.sh"])

    def test_execute_without_a_connected_client_fails_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executor = ParamikoRemoteExecutor(
                KnownHostStore(Path(directory) / "known_hosts")
            )
            with self.assertRaises(RemoteExecutorError) as caught:
                executor.execute("sbatch --parsable submit.sh")
        self.assertNotIsInstance(caught.exception, RemoteCommandOutcomeUnknown)


if __name__ == "__main__":
    unittest.main()
