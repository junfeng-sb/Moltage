import unittest

from moltage.domain.server_profile import AitranssRuntimeConfiguration
from moltage.remote.aitranss_discovery import (
    AITRANSS_MARKER,
    AITRANSS_RESOLVED_MARKER,
    AitranssDiscoveryError,
    discover_aitranss_executable,
)
from moltage.remote.executor import RemoteCommandResult, RemoteConnectionError


COMMAND_PATH = "/srv/moltage-test/modules/bin/aitranss.synthetic.x"
RESOLVED_PATH = "/srv/moltage-test/apps/aitranss/bin/aitranss.synthetic.x"
RUNTIME = AitranssRuntimeConfiguration(
    modules=("chemistry/aitranss-example",),
    executable_path=RESOLVED_PATH,
)


class DiscoveryRemote:
    def __init__(self, lookup_result, *, error=None):
        self.lookup_result = lookup_result
        self.error = error
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        if command.startswith("bash -lc "):
            return self.lookup_result
        if command == f"test -f {RESOLVED_PATH} && test -x {RESOLVED_PATH}":
            return RemoteCommandResult(0, b"", b"")
        raise AssertionError(command)


class AitranssDiscoveryTests(unittest.TestCase):
    def test_v3_lookup_resolves_regular_executable_but_never_runs_it(self):
        remote = DiscoveryRemote(
            RemoteCommandResult(
                0,
                (
                    AITRANSS_MARKER
                    + COMMAND_PATH
                    + "\n"
                    + AITRANSS_RESOLVED_MARKER
                    + RESOLVED_PATH
                    + "\n"
                ).encode(),
                b"",
            )
        )

        result = discover_aitranss_executable(remote, RUNTIME)

        self.assertEqual(result.command_v_path, COMMAND_PATH)
        self.assertEqual(result.executable_path, RESOLVED_PATH)
        self.assertIsNone(result.tcontrol_utility_path)
        self.assertEqual(len(remote.commands), 2)
        lookup = remote.commands[0]
        self.assertIn("module purge", lookup)
        self.assertIn(f"module load {RUNTIME.modules[0]}", lookup)
        self.assertIn("command -v aitranss.synthetic.x", lookup)
        self.assertIn("readlink -f", lookup)
        self.assertEqual(
            remote.commands[-1],
            f"test -f {RESOLVED_PATH} && test -x {RESOLVED_PATH}",
        )
        self.assertFalse(any("--version" in command for command in remote.commands))

    def test_empty_command_and_realpath_markers_are_unavailable(self):
        remote = DiscoveryRemote(
            RemoteCommandResult(
                0,
                f"{AITRANSS_MARKER}\n{AITRANSS_RESOLVED_MARKER}\n".encode(),
                b"",
            )
        )
        with self.assertRaisesRegex(AitranssDiscoveryError, "no resolvable path"):
            discover_aitranss_executable(remote, RUNTIME)
        self.assertEqual(len(remote.commands), 1)

    def test_transport_failure_propagates_without_becoming_unavailable(self):
        error = RemoteConnectionError("transport lost")
        remote = DiscoveryRemote(None, error=error)
        with self.assertRaises(RemoteConnectionError) as caught:
            discover_aitranss_executable(remote, RUNTIME)
        self.assertIs(caught.exception, error)

    def test_resolved_path_must_match_the_saved_verified_runtime(self):
        remote = DiscoveryRemote(
            RemoteCommandResult(
                0,
                (
                    AITRANSS_MARKER
                    + COMMAND_PATH
                    + "\n"
                    + AITRANSS_RESOLVED_MARKER
                    + RESOLVED_PATH
                    + "\n"
                ).encode(),
                b"",
            )
        )
        stale = AitranssRuntimeConfiguration(
            modules=RUNTIME.modules,
            executable_path="/srv/moltage-test/old/bin/aitranss.synthetic.x",
        )

        with self.assertRaisesRegex(AitranssDiscoveryError, "path is stale"):
            discover_aitranss_executable(remote, stale)

        self.assertEqual(len(remote.commands), 1)


if __name__ == "__main__":
    unittest.main()
