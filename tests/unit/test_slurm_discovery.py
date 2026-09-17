from dataclasses import replace
import unittest

from moltage.domain.server_profile import (
    ServerProfileValidationError,
    SlurmCommandMode,
    validate_slurm_bin_directory,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
    RemoteConnectionError,
    RemoteExecutorError,
)
from moltage.remote.slurm_discovery import (
    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
    LOGIN_SHELL_DISCOVERY_COMMAND,
    SlurmConfigurationError,
    SlurmDiscoveryError,
    SlurmDiscoverySource,
    discover_slurm,
    resolve_slurm_for_submission,
    slurm_command_path,
    validate_sbatch_path,
)
from phase2b1_test_support import synthetic_slurm_preset


class ScriptedExecutor:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        if not self.outcomes:
            raise AssertionError(f"unexpected remote command: {command}")
        expected, outcome = self.outcomes.pop(0)
        if command != expected:
            raise AssertionError(f"expected {expected!r}, received {command!r}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def command_result(status=0, stdout=b"", stderr=b""):
    return RemoteCommandResult(status, stdout, stderr)


class SlurmDiscoveryTests(unittest.TestCase):
    def test_current_environment_candidate_is_verified_without_login_shell(self):
        executor = ScriptedExecutor(
            (
                (
                    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                    command_result(stdout=b"/usr/bin/sbatch\n"),
                ),
                (
                    "/usr/bin/sbatch --version",
                    command_result(stdout=b"slurm 24.11.3\n"),
                ),
            )
        )

        result = discover_slurm(executor)

        self.assertEqual(result.sbatch_path, "/usr/bin/sbatch")
        self.assertEqual(result.bin_directory, "/usr/bin")
        self.assertEqual(result.version_text, "slurm 24.11.3")
        self.assertIs(
            result.discovery_source,
            SlurmDiscoverySource.CURRENT_ENVIRONMENT,
        )
        self.assertNotIn(LOGIN_SHELL_DISCOVERY_COMMAND, executor.commands)
        self.assertEqual(executor.outcomes, [])

    def test_current_exit_one_falls_back_to_marked_login_shell_then_verifies(self):
        executor = ScriptedExecutor(
            (
                (
                    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                    command_result(1),
                ),
                (
                    LOGIN_SHELL_DISCOVERY_COMMAND,
                    command_result(
                        stdout=(
                            b"harmless cluster banner\n"
                            b"__MOLTAGE_SBATCH__="
                            b"/apps/slurm/current/bin/sbatch\n"
                        )
                    ),
                ),
                (
                    "/apps/slurm/current/bin/sbatch --version",
                    command_result(stdout=b"slurm 25.05.1\n"),
                ),
            )
        )

        result = discover_slurm(executor)

        self.assertEqual(
            result.sbatch_path,
            "/apps/slurm/current/bin/sbatch",
        )
        self.assertEqual(result.bin_directory, "/apps/slurm/current/bin")
        self.assertIs(result.discovery_source, SlurmDiscoverySource.LOGIN_SHELL)

    def test_current_discovery_transport_failure_propagates_without_fallback(self):
        transport_error = RemoteConnectionError(
            "SSH transport failed during Slurm discovery"
        )
        executor = ScriptedExecutor(
            ((CURRENT_ENVIRONMENT_DISCOVERY_COMMAND, transport_error),)
        )

        with self.assertRaises(RemoteConnectionError) as caught:
            discover_slurm(executor)

        self.assertIs(caught.exception, transport_error)
        self.assertEqual(
            executor.commands,
            [CURRENT_ENVIRONMENT_DISCOVERY_COMMAND],
        )
        self.assertNotIn(LOGIN_SHELL_DISCOVERY_COMMAND, executor.commands)

    def test_current_candidate_verification_transport_failure_propagates(self):
        transport_error = RemoteCommandOutcomeUnknown(
            "SSH transport failed during sbatch verification"
        )
        executor = ScriptedExecutor(
            (
                (
                    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                    command_result(stdout=b"/usr/bin/sbatch\n"),
                ),
                ("/usr/bin/sbatch --version", transport_error),
            )
        )

        with self.assertRaises(RemoteCommandOutcomeUnknown) as caught:
            discover_slurm(executor)

        self.assertIs(caught.exception, transport_error)
        self.assertEqual(
            executor.commands,
            [
                CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                "/usr/bin/sbatch --version",
            ],
        )
        self.assertNotIn(LOGIN_SHELL_DISCOVERY_COMMAND, executor.commands)

    def test_unmarked_or_ambiguous_discovery_output_is_never_guessed(self):
        invalid_current_outputs = (
            b"sbatch\n",
            b"sbatch is /usr/bin/sbatch\n",
            b"alias sbatch=/usr/bin/sbatch\n",
            b"/usr/bin/sbatch\n/another/sbatch\n",
        )
        for output in invalid_current_outputs:
            with self.subTest(output=output):
                executor = ScriptedExecutor(
                    (
                        (
                            CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                            command_result(stdout=output),
                        ),
                        (
                            LOGIN_SHELL_DISCOVERY_COMMAND,
                            command_result(1),
                        ),
                    )
                )
                with self.assertRaises(SlurmDiscoveryError):
                    discover_slurm(executor)
                self.assertFalse(
                    any(command.endswith(" --version") for command in executor.commands)
                )

        ambiguous_marker = (
            b"__MOLTAGE_SBATCH__=/usr/bin/sbatch\n"
            b"__MOLTAGE_SBATCH__=/another/sbatch\n"
        )
        executor = ScriptedExecutor(
            (
                (CURRENT_ENVIRONMENT_DISCOVERY_COMMAND, command_result(1)),
                (
                    LOGIN_SHELL_DISCOVERY_COMMAND,
                    command_result(stdout=ambiguous_marker),
                ),
            )
        )
        with self.assertRaises(SlurmDiscoveryError):
            discover_slurm(executor)

    def test_nonzero_current_candidate_verification_allows_login_fallback(self):
        executor = ScriptedExecutor(
            (
                (
                    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                    command_result(stdout=b"/usr/bin/sbatch\n"),
                ),
                (
                    "/usr/bin/sbatch --version",
                    command_result(1, stderr=b"unusable\n"),
                ),
                (
                    LOGIN_SHELL_DISCOVERY_COMMAND,
                    command_result(
                        stdout=b"__MOLTAGE_SBATCH__=/software/slurm/bin/sbatch\n"
                    ),
                ),
                (
                    "/software/slurm/bin/sbatch --version",
                    command_result(stdout=b"Slurm 23.02.7\n"),
                ),
            )
        )

        result = discover_slurm(executor)

        self.assertEqual(result.bin_directory, "/software/slurm/bin")
        self.assertIs(result.discovery_source, SlurmDiscoverySource.LOGIN_SHELL)

    def test_stale_cached_automatic_path_is_rediscovered(self):
        preset = replace(
            synthetic_slurm_preset(),
            slurm_bin_directory="/old/slurm/bin",
        )
        executor = ScriptedExecutor(
            (
                (
                    "/old/slurm/bin/sbatch --version",
                    command_result(1),
                ),
                (
                    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                    command_result(stdout=b"/new/slurm/bin/sbatch\n"),
                ),
                (
                    "/new/slurm/bin/sbatch --version",
                    command_result(stdout=b"slurm 24.11.3\n"),
                ),
            )
        )

        result = resolve_slurm_for_submission(executor, preset)

        self.assertEqual(result.sbatch_path, "/new/slurm/bin/sbatch")
        self.assertNotEqual(result.sbatch_path, "/old/slurm/bin/sbatch")

    def test_manual_mode_verifies_exact_path_without_automatic_discovery(self):
        preset = replace(
            synthetic_slurm_preset(),
            slurm_command_mode=SlurmCommandMode.MANUAL,
            slurm_bin_directory="/custom/slurm/bin",
        )
        executor = ScriptedExecutor(
            (
                (
                    "/custom/slurm/bin/sbatch --version",
                    command_result(stdout=b"slurm 22.05.9\n"),
                ),
            )
        )

        result = resolve_slurm_for_submission(executor, preset)

        self.assertEqual(result.sbatch_path, "/custom/slurm/bin/sbatch")
        self.assertIs(result.discovery_source, SlurmDiscoverySource.MANUAL)
        self.assertEqual(
            executor.commands,
            ["/custom/slurm/bin/sbatch --version"],
        )

    def test_manual_verification_failure_does_not_switch_to_automatic(self):
        preset = replace(
            synthetic_slurm_preset(),
            slurm_command_mode=SlurmCommandMode.MANUAL,
            slurm_bin_directory="/wrong/slurm/bin",
        )
        executor = ScriptedExecutor(
            (
                (
                    "/wrong/slurm/bin/sbatch --version",
                    command_result(127),
                ),
            )
        )

        with self.assertRaises(SlurmConfigurationError) as caught:
            resolve_slurm_for_submission(executor, preset)

        self.assertEqual(caught.exception.bin_directory, "/wrong/slurm/bin")
        self.assertNotIn(CURRENT_ENVIRONMENT_DISCOVERY_COMMAND, executor.commands)
        self.assertNotIn(LOGIN_SHELL_DISCOVERY_COMMAND, executor.commands)

    def test_manual_verification_transport_failure_is_not_configuration_error(self):
        preset = replace(
            synthetic_slurm_preset(),
            slurm_command_mode=SlurmCommandMode.MANUAL,
            slurm_bin_directory="/custom/slurm/bin",
        )
        transport_error = RemoteExecutorError(
            "SSH transport failed during manual sbatch verification"
        )
        executor = ScriptedExecutor(
            (("/custom/slurm/bin/sbatch --version", transport_error),)
        )

        with self.assertRaises(RemoteExecutorError) as caught:
            resolve_slurm_for_submission(executor, preset)

        self.assertIs(caught.exception, transport_error)
        self.assertNotIsInstance(caught.exception, SlurmConfigurationError)
        self.assertEqual(
            executor.commands,
            ["/custom/slurm/bin/sbatch --version"],
        )

    def test_path_validation_accepts_safe_generic_paths_and_rejects_unsafe_paths(self):
        for directory in (
            "/opt/slurm/bin",
            "/usr/bin",
            "/apps/slurm/24.11/bin",
        ):
            with self.subTest(directory=directory):
                self.assertEqual(validate_slurm_bin_directory(directory), directory)
                self.assertEqual(
                    validate_sbatch_path(directory + "/sbatch"),
                    directory + "/sbatch",
                )

        for directory in (
            "opt/slurm/bin",
            "/",
            "/opt/slurm/bin/sbatch",
            "/opt/slurm/bin;rm",
            "/opt/slurm/bin\n",
            "/opt/slurm/bin\nnext",
            "/opt/slurm/bin\x00next",
        ):
            with self.subTest(directory=directory):
                with self.assertRaises(ServerProfileValidationError):
                    validate_slurm_bin_directory(directory)

    def test_only_required_phase2c_scheduler_commands_can_be_derived(self):
        preset = replace(
            synthetic_slurm_preset(),
            slurm_bin_directory="/opt/slurm/bin",
        )
        self.assertEqual(
            slurm_command_path(preset, "squeue"),
            "/opt/slurm/bin/squeue",
        )
        self.assertEqual(
            slurm_command_path(preset, "sacct"),
            "/opt/slurm/bin/sacct",
        )
        self.assertEqual(
            slurm_command_path(preset, "scancel"),
            "/opt/slurm/bin/scancel",
        )
        with self.assertRaises(ValueError):
            slurm_command_path(preset, "scontrol")

    def test_discovery_commands_are_bounded_and_contain_no_server_hard_code(self):
        commands = (
            CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
            LOGIN_SHELL_DISCOVERY_COMMAND,
        )
        joined = "\n".join(commands).casefold()
        self.assertIn("command -v sbatch", joined)
        self.assertIn("bash -lc", joined)
        self.assertIn("__moltage_sbatch__=", joined)
        self.assertNotIn("find /", joined)
        self.assertNotIn("source ", joined)
        self.assertNotIn("examplecluster", joined)
        self.assertNotIn("slurm_26-05-2-1", joined)


if __name__ == "__main__":
    unittest.main()
