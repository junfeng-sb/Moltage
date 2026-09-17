import unittest

from moltage.remote.executor import RemoteCommandResult, RemoteConnectionError
from moltage.remote.runtime_discovery import (
    AITRANSS_EXECUTABLE_COMMAND,
    FHI_AIMS_EXECUTABLE_COMMAND,
    MAX_RUNTIME_MODULE_CANDIDATES,
    RuntimeDiscoveryError,
    build_module_listing_command,
    discover_runtimes,
    launch_command_with_verified_fhi_path,
    parse_terse_module_listing,
)
from synthetic_test_data import (
    SYNTHETIC_AITRANSS_MODULE,
    SYNTHETIC_FHI_MODULE,
    SYNTHETIC_MPI_MODULE,
)


LMOD_LISTING = f"""/srv/modulefiles/Core:
{SYNTHETIC_FHI_MODULE}
chemistry/fhi-aims-alt (D)
fhi-aims/
"""
MODULES_LISTING = f"""---------------- /srv/modulefiles ----------------
chemistry/FHIaims-example(default)
{SYNTHETIC_AITRANSS_MODULE}
unrelated/1.0
"""
FHI_PATH = "/srv/moltage-test/apps/fhi-aims/bin/aims.x"
AITRANSS_PATH = "/srv/moltage-test/apps/aitranss/bin/aitranss.x"


def _marked_result(path: str) -> RemoteCommandResult:
    return RemoteCommandResult(
        0,
        (
            f"__MOLTAGE_COMMAND__={path}\n"
            f"__MOLTAGE_RESOLVED__={path}\n"
        ).encode(),
        b"",
    )


class RuntimeRemote:
    def __init__(self, *, listing_style: str = "lmod", error=None) -> None:
        self.listing_style = listing_style
        self.error = error
        self.commands: list[str] = []

    def execute(self, command: str) -> RemoteCommandResult:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        if "module -t " in command:
            return self._listing(command)
        if f"command -v {FHI_AIMS_EXECUTABLE_COMMAND}" in command:
            return self._verify_fhi(command)
        if f"command -v {AITRANSS_EXECUTABLE_COMMAND}" in command:
            return self._verify_aitranss(command)
        raise AssertionError(f"unexpected command: {command}")

    def _listing(self, command: str) -> RemoteCommandResult:
        if self.listing_style == "none":
            return RemoteCommandResult(1, b"", b"module unavailable")
        if self.listing_style == "spider":
            if "module -t avail " in command:
                return RemoteCommandResult(1, b"", b"no match")
            if "module -t spider aims" in command:
                return RemoteCommandResult(0, b"chemistry/FHIaims-example\n", b"")
            if "module -t spider aitranss" in command:
                return RemoteCommandResult(0, (SYNTHETIC_AITRANSS_MODULE + "\n").encode(), b"")
        if "module -t avail aims" in command:
            listing = LMOD_LISTING if self.listing_style == "lmod" else MODULES_LISTING
            return RemoteCommandResult(0, listing.encode(), b"")
        if "module -t avail aitranss" in command:
            if self.listing_style == "lmod":
                return RemoteCommandResult(1, b"", b"no separate match")
            return RemoteCommandResult(0, (SYNTHETIC_AITRANSS_MODULE + "\n").encode(), b"")
        return RemoteCommandResult(1, b"", b"unsupported strategy")

    def _verify_fhi(self, command: str) -> RemoteCommandResult:
        if self.listing_style == "lmod":
            if (
                f"module load {SYNTHETIC_MPI_MODULE}" in command
                and f"module load {SYNTHETIC_FHI_MODULE}" in command
            ):
                return _marked_result(FHI_PATH)
        elif "module load chemistry/FHIaims-example" in command:
            return _marked_result(FHI_PATH)
        return RemoteCommandResult(3, b"", b"")

    def _verify_aitranss(self, command: str) -> RemoteCommandResult:
        if f"module load {SYNTHETIC_AITRANSS_MODULE}" in command:
            return _marked_result(AITRANSS_PATH)
        if self.listing_style == "lmod" and (
            f"module load {SYNTHETIC_FHI_MODULE}" in command
        ):
            return _marked_result(AITRANSS_PATH)
        return RemoteCommandResult(3, b"", b"")


class ConfiguredEnvironmentRemote(RuntimeRemote):
    def __init__(self) -> None:
        super().__init__(listing_style="none")

    def _verify_fhi(self, command: str) -> RemoteCommandResult:
        if (
            "module load compiler/2025" in command
            and "module load chemistry/runtime" in command
        ):
            return _marked_result(FHI_PATH)
        return RemoteCommandResult(3, b"", b"")

    def _verify_aitranss(self, command: str) -> RemoteCommandResult:
        if (
            "module load compiler/2025" in command
            and "module load chemistry/runtime" in command
        ):
            return _marked_result(AITRANSS_PATH)
        return RemoteCommandResult(3, b"", b"")


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_lmod_listing_accepts_headers_and_decorations(self):
        self.assertEqual(
            parse_terse_module_listing(LMOD_LISTING),
            (SYNTHETIC_FHI_MODULE, "chemistry/fhi-aims-alt"),
        )

    def test_environment_modules_listing_accepts_headers_case_and_defaults(self):
        self.assertEqual(
            parse_terse_module_listing(MODULES_LISTING),
            ("chemistry/FHIaims-example", SYNTHETIC_AITRANSS_MODULE),
        )

    def test_environment_modules_presentation_slash_is_not_an_absolute_path(self):
        listing = (
            "/srv/modulefiles/aims:\n"
            "/chemistry/FHIaims-example\n"
            f"/{SYNTHETIC_AITRANSS_MODULE}\n"
        )

        self.assertEqual(
            parse_terse_module_listing(listing),
            ("chemistry/FHIaims-example", SYNTHETIC_AITRANSS_MODULE),
        )

    def test_uppercase_query_variants_are_part_of_the_bounded_strategy(self):
        command = build_module_listing_command("avail", "AIMS")

        self.assertIn("module -t avail AIMS", command)

    def test_candidate_enumeration_is_explicitly_bounded(self):
        listing = "\n".join(
            f"aims/{index}" for index in range(MAX_RUNTIME_MODULE_CANDIDATES + 1)
        )
        with self.assertRaisesRegex(RuntimeDiscoveryError, "too many"):
            parse_terse_module_listing(listing)

    def test_configured_layout_discovers_both_generic_identities(self):
        remote = RuntimeRemote(listing_style="lmod")

        result = discover_runtimes(
            remote,
            (SYNTHETIC_MPI_MODULE, SYNTHETIC_FHI_MODULE),
        )

        self.assertEqual(len(result.fhi_aims_candidates), 1)
        self.assertEqual(len(result.aitranss_candidates), 1)
        self.assertEqual(
            result.fhi_aims_candidates[0].modules,
            (SYNTHETIC_MPI_MODULE, SYNTHETIC_FHI_MODULE),
        )
        self.assertEqual(result.fhi_aims_candidates[0].executable_path, FHI_PATH)
        self.assertEqual(
            result.aitranss_candidates[0].modules,
            (SYNTHETIC_MPI_MODULE, SYNTHETIC_FHI_MODULE),
        )
        self.assertFalse(any("--version" in command for command in remote.commands))
        self.assertFalse(any("sbatch" in command for command in remote.commands))
        self.assertFalse(any("find " in command for command in remote.commands))

    def test_normal_avail_failures_continue_to_lmod_spider(self):
        remote = RuntimeRemote(listing_style="spider")

        result = discover_runtimes(remote, ())

        self.assertEqual(
            result.fhi_aims_candidates[0].module_name,
            "chemistry/FHIaims-example",
        )
        self.assertEqual(
            result.aitranss_candidates[0].module_name,
            SYNTHETIC_AITRANSS_MODULE,
        )
        self.assertTrue(
            any("module -t spider aims" in command for command in remote.commands)
        )

    def test_tcl_environment_modules_output_is_verified_not_assumed(self):
        result = discover_runtimes(
            RuntimeRemote(listing_style="modules"),
            (),
        )

        self.assertEqual(
            result.fhi_aims_candidates[0].modules,
            ("chemistry/FHIaims-example",),
        )
        self.assertEqual(
            result.aitranss_candidates[0].modules,
            (SYNTHETIC_AITRANSS_MODULE,),
        )

    def test_manually_entered_module_environment_works_without_list_output(self):
        remote = ConfiguredEnvironmentRemote()

        result = discover_runtimes(
            remote,
            ("compiler/2025", "chemistry/runtime"),
        )

        self.assertEqual(
            result.fhi_aims_candidates[0].modules,
            ("compiler/2025", "chemistry/runtime"),
        )
        self.assertEqual(
            result.aitranss_candidates[0].modules,
            ("compiler/2025", "chemistry/runtime"),
        )
        self.assertFalse(any("module -t " in command for command in remote.commands))

    def test_all_normal_listing_failures_report_module_system_not_transport(self):
        with self.assertRaisesRegex(RuntimeDiscoveryError, "module command"):
            discover_runtimes(RuntimeRemote(listing_style="none"), ())

    def test_transport_failure_propagates_without_becoming_no_runtime(self):
        error = RemoteConnectionError("transport lost")
        remote = RuntimeRemote(error=error)

        with self.assertRaises(RemoteConnectionError) as caught:
            discover_runtimes(remote, ())

        self.assertIs(caught.exception, error)
        self.assertEqual(len(remote.commands), 1)

    def test_listing_command_is_isolated_and_rejects_unsafe_modules(self):
        command = build_module_listing_command(
            "avail",
            "aims",
            ("compiler/2025",),
        )
        self.assertTrue(command.startswith("bash -lc "))
        self.assertIn("module purge", command)
        self.assertIn("module load compiler/2025", command)
        self.assertIn("LMOD_TERSE_DECORATIONS=no", command)
        self.assertIn("MODULES_AVAIL_TERSE_OUTPUT=", command)
        with self.assertRaises(Exception):
            build_module_listing_command(
                "avail",
                "aims",
                ("compiler/2025; touch bad",),
            )

    def test_launch_update_preserves_existing_srun_options_and_identity(self):
        self.assertEqual(
            launch_command_with_verified_fhi_path(
                "srun --cpu_bind=verbose " + FHI_AIMS_EXECUTABLE_COMMAND,
                FHI_PATH,
            ),
            "srun --cpu_bind=verbose " + FHI_PATH,
        )
        self.assertEqual(
            launch_command_with_verified_fhi_path("", FHI_PATH),
            "srun " + FHI_PATH,
        )


if __name__ == "__main__":
    unittest.main()
