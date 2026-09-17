"""Synthetic tests for bounded ORCA runtime validation and discovery."""

from dataclasses import replace
import unittest

from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    RuntimeLocation,
)
from moltage.remote.executor import RemoteCommandResult
from moltage.remote.orca_runtime import (
    OrcaRuntimeError,
    discover_orca_runtimes,
    require_usable_orca_runtime,
    validate_orca_runtime,
)


ORCA_61 = "/apps/example/orca-6.1/orca"
ORCA_50 = "/apps/example/orca-5.0/orca"


class SyntheticOrcaExecutor:
    def __init__(self, *, path=ORCA_61, version="6.1.2") -> None:
        self.path = path
        self.version = version
        self.commands = []
        self.validation_status = 0
        self.resolved_override = None
        self.module_catalog = b"orca/5.0\norca/6.1(default)\nchemistry/not-related\n"

    def execute(self, command):
        self.commands.append(command)
        if "module -t avail orca" in command:
            return RemoteCommandResult(0, b"", self.module_catalog)
        if "__MOLTAGE_ORCA_CANONICAL__=" in command:
            path = self._path_for(command)
            return RemoteCommandResult(
                0,
                f"__MOLTAGE_ORCA_CANONICAL__={path}\n".encode(),
                b"",
            )
        if "__MOLTAGE_ORCA_CONFIGURED__=" in command:
            if self.validation_status:
                return RemoteCommandResult(self.validation_status, b"", b"")
            configured = self._path_for(command)
            resolved = self.resolved_override or configured
            return RemoteCommandResult(
                0,
                (
                    f"__MOLTAGE_ORCA_CONFIGURED__={configured}\n"
                    f"__MOLTAGE_ORCA_PATH__={resolved}\n"
                ).encode(),
                b"",
            )
        if "--version" in command:
            version = "5.0.4" if "orca-5.0" in command else self.version
            output = (
                f"Program Version {version}\n" if version else "synthetic unparseable output\n"
            )
            return RemoteCommandResult(0, output.encode(), b"")
        raise AssertionError(f"unexpected ORCA command: {command}")

    def _path_for(self, command):
        if "orca/5.0" in command or "orca-5.0" in command:
            return ORCA_50
        if "orca/6.1" in command or "orca-6.1" in command:
            return ORCA_61
        return self.path


class OrcaRuntimeTests(unittest.TestCase):
    def test_manual_validation_binds_exact_canonical_path_environment_and_version(self):
        executor = SyntheticOrcaExecutor()
        environment = RuntimeEnvironment(
            RuntimeEnvironmentMode.MODULES,
            ("orca/6.1",),
        )

        runtime = validate_orca_runtime(executor, ORCA_61, environment)

        self.assertEqual(runtime.executable_path, ORCA_61)
        self.assertEqual(runtime.environment, environment)
        self.assertEqual(runtime.version_evidence.version, "6.1.2")
        self.assertEqual(runtime.version_evidence.version_family.value, "6.1")
        self.assertFalse(any("find " in command for command in executor.commands))

    def test_manual_validation_rejects_non_executable_and_environment_mismatch(self):
        executor = SyntheticOrcaExecutor()
        executor.validation_status = 31
        with self.assertRaisesRegex(OrcaRuntimeError, "readable regular executable"):
            validate_orca_runtime(
                executor,
                ORCA_61,
                RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
            )

        executor = SyntheticOrcaExecutor()
        executor.resolved_override = ORCA_50
        with self.assertRaisesRegex(OrcaRuntimeError, "different executable"):
            validate_orca_runtime(
                executor,
                ORCA_61,
                RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
            )

    def test_slurm_discovery_keeps_distinct_candidates_without_newest_selection(self):
        executor = SyntheticOrcaExecutor(path="/apps/example/login/orca")
        configured = RuntimeLocation(
            ORCA_50,
            RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, ("orca/5.0",)),
        )

        result = discover_orca_runtimes(
            executor,
            scheduler_kind=SchedulerKind.SLURM,
            configured_location=configured,
        )

        identities = tuple(
            (item.source_label, item.runtime.executable_path)
            for item in result.candidates
        )
        self.assertIn(("login PATH", "/apps/example/login/orca"), identities)
        self.assertIn(("configured environment", ORCA_50), identities)
        self.assertIn(("module orca/6.1", ORCA_61), identities)
        self.assertGreaterEqual(len(identities), 3)
        self.assertFalse(any("/opt" in command or "find " in command for command in executor.commands))

    def test_discovery_retains_unparseable_version_as_unverified_evidence(self):
        executor = SyntheticOrcaExecutor(version="")
        executor.module_catalog = b""

        result = discover_orca_runtimes(
            executor,
            scheduler_kind=SchedulerKind.SLURM,
        )

        self.assertEqual(len(result.candidates), 1)
        evidence = result.candidates[0].runtime.version_evidence
        self.assertIsNone(evidence.version)
        self.assertIsNone(evidence.version_family)

    def test_lsf_auto_discovery_is_rejected_without_remote_commands(self):
        executor = SyntheticOrcaExecutor()
        with self.assertRaisesRegex(OrcaRuntimeError, "only for Slurm"):
            discover_orca_runtimes(executor, scheduler_kind=SchedulerKind.LSF)
        self.assertEqual(executor.commands, [])

    def test_submission_preflight_rejects_stale_or_unsupported_version(self):
        executor = SyntheticOrcaExecutor()
        runtime = validate_orca_runtime(
            executor,
            ORCA_61,
            RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
        )
        executor.version = "6.1.3"
        with self.assertRaisesRegex(OrcaRuntimeError, "changed"):
            require_usable_orca_runtime(executor, runtime)

        unverified = replace(
            runtime,
            version_evidence=replace(
                runtime.version_evidence,
                version=None,
                version_family=None,
            ),
        )
        executor.version = ""
        require_usable_orca_runtime(executor, unverified)

        unsupported = replace(
            runtime,
            version_evidence=replace(
                runtime.version_evidence,
                version="7.0.0",
                version_family=None,
            ),
        )
        executor.version = "7.0.0"
        with self.assertRaisesRegex(OrcaRuntimeError, "outside the reviewed"):
            require_usable_orca_runtime(executor, unsupported)


if __name__ == "__main__":
    unittest.main()
