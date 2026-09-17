import unittest

from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
    RemoteConnectionError,
)
from moltage.remote.slurm_cancel import (
    SlurmCancellationError,
    SlurmCancellationRejected,
    build_scancel_command,
    request_slurm_cancellation_once,
)
from moltage.remote.slurm_discovery import SlurmVerificationError
from moltage.remote.slurm_status import SlurmStatusError


class FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SlurmCancellationTests(unittest.TestCase):
    def test_builder_targets_one_exact_decimal_job(self) -> None:
        self.assertEqual(
            build_scancel_command("/verified/slurm/bin/scancel", "41005"),
            "/verified/slurm/bin/scancel 41005",
        )
        for job_id in ("", "41005.batch", "41005*", "--user=user"):
            with self.subTest(job_id=job_id), self.assertRaises(SlurmStatusError):
                build_scancel_command("/verified/slurm/bin/scancel", job_id)
        with self.assertRaises(SlurmVerificationError):
            build_scancel_command("scancel", "41005")

    def test_success_and_known_rejection_each_dispatch_once(self) -> None:
        success = FakeExecutor(RemoteCommandResult(0, b"", b""))
        request_slurm_cancellation_once(
            success,
            scancel_path="/verified/slurm/bin/scancel",
            job_id="41005",
        )
        self.assertEqual(
            success.commands,
            ["/verified/slurm/bin/scancel 41005"],
        )

        rejected = FakeExecutor(RemoteCommandResult(1, b"", b"denied"))
        with self.assertRaises(SlurmCancellationRejected):
            request_slurm_cancellation_once(
                rejected,
                scancel_path="/verified/slurm/bin/scancel",
                job_id="41005",
            )
        self.assertEqual(len(rejected.commands), 1)

    def test_transport_outcomes_propagate_without_retry(self) -> None:
        for error in (
            RemoteCommandOutcomeUnknown("unknown after dispatch"),
            RemoteConnectionError("not dispatched"),
        ):
            executor = FakeExecutor(error)
            with self.subTest(error=type(error).__name__), self.assertRaises(
                type(error)
            ):
                request_slurm_cancellation_once(
                    executor,
                    scancel_path="/verified/slurm/bin/scancel",
                    job_id="41005",
                )
            self.assertEqual(len(executor.commands), 1)

    def test_invalid_known_result_fails_typed_without_second_dispatch(self) -> None:
        executor = FakeExecutor(object())
        with self.assertRaisesRegex(SlurmCancellationError, "invalid command result"):
            request_slurm_cancellation_once(
                executor,
                scancel_path="/verified/slurm/bin/scancel",
                job_id="41005",
            )
        self.assertEqual(executor.commands, ["/verified/slurm/bin/scancel 41005"])


if __name__ == "__main__":
    unittest.main()
