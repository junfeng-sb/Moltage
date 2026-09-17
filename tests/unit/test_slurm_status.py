from pathlib import Path
import unittest

from moltage.remote.executor import (
    RemoteCommandResult,
    RemoteConnectionError,
)
from moltage.remote.slurm_status import (
    SchedulerStatusKind,
    SlurmStatusParseError,
    SlurmStatusQueryError,
    build_sacct_status_command,
    build_squeue_status_command,
    parse_sacct_output,
    parse_squeue_output,
    query_slurm_job_status,
    validate_job_id,
)


class ScriptedExecutor:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        expected, outcome = self.outcomes.pop(0)
        if command != expected:
            raise AssertionError(f"expected {expected!r}, received {command!r}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def result(status=0, stdout=b"", stderr=b""):
    return RemoteCommandResult(status, stdout, stderr)


SYNTHETIC_CANCELLED_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "remote_r3_r2"
    / "sacct_cancelled_synthetic.psv"
)


class SlurmStatusTests(unittest.TestCase):
    def test_commands_are_absolute_fixed_and_job_id_is_decimal_only(self):
        self.assertEqual(
            build_squeue_status_command(
                "/opt/slurm/bin/squeue",
                "scientist",
            ),
            "/opt/slurm/bin/squeue --noheader --user=scientist "
            "--format='%i|%T'",
        )
        self.assertEqual(
            build_sacct_status_command("/opt/slurm/bin/sacct", "12345"),
            "/opt/slurm/bin/sacct --noheader --parsable2 --jobs=12345 "
            "--format=JobIDRaw,State,ExitCode",
        )
        for unsafe in ("", "12 34", "123;rm", "job123", "123\n456"):
            with self.subTest(unsafe=unsafe), self.assertRaises(Exception):
                validate_job_id(unsafe)

    def test_squeue_profile_username_is_shell_quoted(self):
        self.assertEqual(
            build_squeue_status_command(
                "/opt/slurm/bin/squeue",
                "science user;group",
            ),
            "/opt/slurm/bin/squeue --noheader "
            "--user='science user;group' --format='%i|%T'",
        )

    def test_squeue_queued_states_map_conservatively(self):
        for state in (
            "PENDING",
            "CONFIGURING",
            "REQUEUED",
            "REQUEUE_HOLD",
            "REQUEUE_FED",
        ):
            with self.subTest(state=state):
                parsed = parse_squeue_output(f"12345|{state}\n", "12345")
                self.assertIs(parsed.kind, SchedulerStatusKind.QUEUED)

    def test_squeue_running_states_map_conservatively(self):
        for state in ("RUNNING", "COMPLETING", "STAGE_OUT"):
            with self.subTest(state=state):
                parsed = parse_squeue_output(f"12345|{state}\n", "12345")
                self.assertIs(parsed.kind, SchedulerStatusKind.RUNNING)

    def test_squeue_selects_exact_target_among_multiple_user_jobs(self):
        parsed = parse_squeue_output(
            "11111|RUNNING\n12345|PENDING\n22222|RUNNING\n",
            "12345",
        )

        self.assertIs(parsed.kind, SchedulerStatusKind.QUEUED)
        self.assertEqual(parsed.scheduler_state, "PENDING")

    def test_squeue_does_not_fuzzy_match_target_job_id(self):
        parsed = parse_squeue_output(
            "123456|RUNNING\n12345_1|RUNNING\n12345.batch|RUNNING\n",
            "12345",
        )

        self.assertIsNone(parsed)

    def test_multiple_user_jobs_select_target_without_querying_sacct(self):
        squeue_command = build_squeue_status_command(
            "/usr/bin/squeue",
            "scientist",
        )
        executor = ScriptedExecutor(
            (
                (
                    squeue_command,
                    result(
                        stdout=(
                            b"11111|RUNNING\n"
                            b"12345|PENDING\n"
                            b"22222|RUNNING\n"
                        )
                    ),
                ),
            )
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/usr/bin/squeue",
            sacct_path="/usr/bin/sacct",
            job_id="12345",
            profile_username="scientist",
        )

        self.assertIs(status.kind, SchedulerStatusKind.QUEUED)
        self.assertEqual(executor.commands, [squeue_command])

    def test_running_target_does_not_query_sacct(self):
        squeue_command = build_squeue_status_command(
            "/usr/bin/squeue",
            "scientist",
        )
        executor = ScriptedExecutor(
            ((squeue_command, result(stdout=b"12345|RUNNING\n")),)
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/usr/bin/squeue",
            sacct_path="/usr/bin/sacct",
            job_id="12345",
            profile_username="scientist",
        )

        self.assertIs(status.kind, SchedulerStatusKind.RUNNING)
        self.assertEqual(executor.commands, [squeue_command])

    def test_empty_squeue_queries_sacct_and_selects_only_parent(self):
        squeue_command = build_squeue_status_command(
            "/usr/bin/squeue",
            "scientist",
        )
        sacct_command = build_sacct_status_command("/usr/bin/sacct", "12345")
        executor = ScriptedExecutor(
            (
                (squeue_command, result()),
                (
                    sacct_command,
                    result(
                        stdout=(
                            b"12345|COMPLETED|0:0\n"
                            b"12345.batch|COMPLETED|0:0\n"
                            b"12345.extern|COMPLETED|0:0\n"
                        )
                    ),
                ),
            )
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/usr/bin/squeue",
            sacct_path="/usr/bin/sacct",
            job_id="12345",
            profile_username="scientist",
        )

        self.assertIs(status.kind, SchedulerStatusKind.COMPLETED)
        self.assertEqual(status.exit_code, "0:0")
        self.assertEqual(executor.commands, [squeue_command, sacct_command])

    def test_sacct_terminal_failures_are_distinct_from_completed(self):
        for state, exit_code in (("TIMEOUT", "0:0"), ("OUT_OF_MEMORY", "0:125")):
            with self.subTest(state=state):
                parsed = parse_sacct_output(
                    f"12345|{state}|{exit_code}\n",
                    "12345",
                )
                self.assertIs(parsed.kind, SchedulerStatusKind.FAILED)
                self.assertEqual(parsed.scheduler_state, state)
                self.assertEqual(parsed.exit_code, exit_code)

    def test_synthetic_cancelled_by_uid_parent_is_canonical_cancelled(self):
        output = SYNTHETIC_CANCELLED_FIXTURE.read_bytes()

        parsed = parse_sacct_output(output, "41003")

        self.assertIs(parsed.kind, SchedulerStatusKind.FAILED)
        self.assertEqual(parsed.scheduler_state, "CANCELLED")
        self.assertEqual(parsed.exit_code, "0:0")

    def test_synthetic_cancelled_hierarchy_flows_through_production_query(self):
        squeue_command = build_squeue_status_command(
            "/opt/slurm/bin/squeue",
            "scientist",
        )
        sacct_command = build_sacct_status_command(
            "/opt/slurm/bin/sacct",
            "41003",
        )
        executor = ScriptedExecutor(
            (
                (squeue_command, result()),
                (
                    sacct_command,
                    result(stdout=SYNTHETIC_CANCELLED_FIXTURE.read_bytes()),
                ),
            )
        )

        parsed = query_slurm_job_status(
            executor,
            squeue_path="/opt/slurm/bin/squeue",
            sacct_path="/opt/slurm/bin/sacct",
            job_id="41003",
            profile_username="scientist",
        )

        self.assertIs(parsed.kind, SchedulerStatusKind.FAILED)
        self.assertEqual(parsed.scheduler_state, "CANCELLED")
        self.assertEqual(parsed.exit_code, "0:0")
        self.assertEqual(executor.commands, [squeue_command, sacct_command])

    def test_parent_selection_is_independent_of_child_row_order(self):
        lines = SYNTHETIC_CANCELLED_FIXTURE.read_bytes().splitlines()
        reordered = b"\n".join((*lines[1:], lines[0])) + b"\n"

        parsed = parse_sacct_output(reordered, "41003")

        self.assertIs(parsed.kind, SchedulerStatusKind.FAILED)
        self.assertEqual(parsed.scheduler_state, "CANCELLED")
        self.assertEqual(parsed.exit_code, "0:0")

    def test_cancelled_decoration_remains_exact_and_fail_closed(self):
        invalid_states = (
            "",
            "CANCELLED+",
            "CANCELLED by ",
            "CANCELLED by user",
            "CANCELLED by -1",
            "CANCELLED by 200001 extra",
            "CANCELLED by 200001 ",
            "cancelled by 200001",
        )
        for state in invalid_states:
            with self.subTest(state=state), self.assertRaisesRegex(
                SlurmStatusParseError,
                "sacct scheduler state is malformed",
            ):
                parse_sacct_output(
                    f"41003|{state}|0:0\n",
                    "41003",
                )

    def test_squeue_does_not_accept_sacct_cancellation_decoration(self):
        with self.assertRaisesRegex(
            SlurmStatusParseError,
            "squeue scheduler state is malformed",
        ):
            parse_squeue_output(
                "41003|CANCELLED by 200001\n",
                "41003",
            )

    def test_no_parent_accounting_row_is_transient(self):
        parsed = parse_sacct_output(
            "12345.batch|COMPLETED|0:0\n12345.extern|COMPLETED|0:0\n",
            "12345",
        )
        self.assertIs(parsed.kind, SchedulerStatusKind.ACCOUNTING_PENDING)

    def test_unknown_well_formed_state_is_unresolved_not_failed(self):
        active = parse_squeue_output("12345|FUTURE_STATE\n", "12345")
        terminal = parse_sacct_output("12345|FUTURE_STATE|0:0\n", "12345")
        self.assertIs(active.kind, SchedulerStatusKind.UNRESOLVED)
        self.assertIs(terminal.kind, SchedulerStatusKind.UNRESOLVED)

    def test_malformed_wrong_or_ambiguous_rows_are_rejected(self):
        invalid_squeue = (
            "JOBID STATE\n12345 RUNNING\n",
            "12345|RUNNING\n12345|PENDING\n",
            " 12345|RUNNING\n",
            "other job|RUNNING\n",
        )
        for output in invalid_squeue:
            with self.subTest(output=output), self.assertRaises(
                SlurmStatusParseError
            ):
                parse_squeue_output(output, "12345")
        invalid_sacct = (
            "99999|COMPLETED|0:0\n",
            "12345|COMPLETED|0:0\n12345|FAILED|1:0\n",
            "12345 COMPLETED 0:0\n",
            "12345|COMPLETED|zero\n",
        )
        for output in invalid_sacct:
            with self.subTest(output=output), self.assertRaises(
                SlurmStatusParseError
            ):
                parse_sacct_output(output, "12345")

    def test_normal_nonzero_query_is_explicit_and_does_not_fall_through(self):
        command = build_squeue_status_command("/usr/bin/squeue", "scientist")
        executor = ScriptedExecutor(((command, result(1)),))
        with self.assertRaises(SlurmStatusQueryError):
            query_slurm_job_status(
                executor,
                squeue_path="/usr/bin/squeue",
                sacct_path="/usr/bin/sacct",
                job_id="12345",
                profile_username="scientist",
            )
        self.assertEqual(executor.commands, [command])

    def test_transport_failure_propagates_without_accounting_fallback(self):
        command = build_squeue_status_command("/usr/bin/squeue", "scientist")
        transport = RemoteConnectionError("SSH transport lost")
        executor = ScriptedExecutor(((command, transport),))
        with self.assertRaises(RemoteConnectionError) as caught:
            query_slurm_job_status(
                executor,
                squeue_path="/usr/bin/squeue",
                sacct_path="/usr/bin/sacct",
                job_id="12345",
                profile_username="scientist",
            )
        self.assertIs(caught.exception, transport)
        self.assertEqual(executor.commands, [command])


if __name__ == "__main__":
    unittest.main()
