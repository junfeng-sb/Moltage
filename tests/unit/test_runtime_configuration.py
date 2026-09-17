"""Focused offline tests for bounded search and explicit runtime configuration."""

from dataclasses import replace
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from moltage.app.server_profiles import ServerProfileRepository
from moltage.aitranss.slurm import AitranssExecutionSettings, render_aitranss_submit_script
from moltage.domain.calculation_project import ProjectStepKind
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration, FhiAimsRuntimeConfiguration, RuntimeDiscoveryHints,
    RuntimeEnvironment, RuntimeEnvironmentMode, RuntimeLocation, RuntimeLocationKind,
)
from moltage.remote.aitranss_discovery import discover_aitranss_executable
from moltage.remote.executor import RemoteCommandResult, RemoteExecutorError, RemotePathNotFoundError
from moltage.remote.runtime_discovery import (
    FHI_AIMS_EXECUTABLE_COMMAND, RuntimeCandidate, RuntimeDiscoveryError,
)
from moltage.remote.runtime_environment import RuntimeConfigurationError, verify_configured_runtime
from moltage.remote.runtime_search import (
    MAX_CANDIDATES, _DependencyReport, _Search,
    _compatible_launcher_candidates, _rank_support_modules,
    _retain_best_candidates,
    discover_runtime_configuration, discover_server_runtimes,
)
from moltage.remote.slurm import preset_with_submit_script_resources, render_submit_script
from phase2b1_test_support import profile


NONE = RuntimeEnvironment(RuntimeEnvironmentMode.NONE)
AIMS = "/opt/fhi-aims/bin/aims.scalapack.mpi.x"
AITRANSS = "/opt/fhi-aims/bin/aitranss.synthetic.x"
MPI = "/opt/mpi/bin/mpirun"
SPECIES_ROOT = "/opt/fhi-aims/species_defaults"
WRAPPER = "/opt/fhi-aims/scripts/run_aims.mpi"
# Minimal synthetic wrapper grammar used only by offline tests.
WRAPPER_BYTES = b"""#!/bin/bash
psd=$(dirname $(readlink -f $0))
aims='aims.scalapack.mpi.x'
case "$1" in
  -u) aims='aims.serial.heaparrays.x' ;;
esac
mpirun -n $cpus ${psd}/../bin/${aims} < /dev/null | tee aims.dft.out | format
"""


def _bash_executable() -> Path | None:
    path = shutil.which("bash")
    return Path(path) if path is not None else None


class SearchRemote:
    def __init__(self):
        self.files = {AIMS: b"\x7fELFsynthetic", MPI: b"launcher", WRAPPER: WRAPPER_BYTES}
        self.paths = {"run_aims.mpi": WRAPPER, "mpirun": MPI}
        self.directories = {}
        self.aliases = {}
        self.commands = []
        self.reads = []
        self.needed = ()
        self.library_locations = {}
        self.needed_by_modules = {}
        self.runpaths_by_modules = {}
        self.library_locations_by_modules = {}
        self.family_paths = {}
        self.advertised_paths = {"FHI-aims": (), "AITRANSS": ()}
        self.index_available = False
        self.indexed_paths = {"FHI-aims": (), "AITRANSS": ()}
        self.species_roots = (SPECIES_ROOT,)

    def execute(self, command):
        self.commands.append(command)
        body = shlex.split(command)[2]
        if "# MOLTAGE_RUNTIME_PATH\n" in body:
            names = shlex.split(re.search(r"for name in (.*?); do", body).group(1))
            return self.result("".join(f"{name}\t{path}\n" for name in names
                for path in ((self.paths[name],) if isinstance(self.paths.get(name), str)
                             else self.paths.get(name, ()))))
        if "# MOLTAGE_RUNTIME_FAMILY\n" in body:
            label = "AITRANSS" if "aitranss*.x" in body else "FHI-aims"
            return self.result("__AT_CANDIDATES_BEGIN__\n" +
                               "".join(path + "\n" for path in self.family_paths.get(label, ())) +
                               "__AT_CANDIDATES_END__\n")
        if "# MOLTAGE_RUNTIME_ADVERTISED_LAYOUT\n" in body:
            label = "AITRANSS" if "aitranss*.x" in body else "FHI-aims"
            return self.result(
                "__AT_CANDIDATES_BEGIN__\n"
                + "".join(path + "\n" for path in self.advertised_paths[label])
                + "__AT_CANDIDATES_END__\n"
            )
        if "# MOLTAGE_RUNTIME_INDEX\n" in body:
            if not self.index_available:
                return self.result(
                    "__AT_CANDIDATES_BEGIN__\n__AT_INDEX_UNAVAILABLE__\n"
                    "__AT_CANDIDATES_END__\n"
                )
            return self.result(
                "__AT_CANDIDATES_BEGIN__\n"
                + "".join(
                    f"{label}\t{path}\n"
                    for label, paths in self.indexed_paths.items()
                    for path in paths
                )
                + "__AT_CANDIDATES_END__\n"
            )
        if "# MOLTAGE_RUNTIME_LAYOUT\n" in body:
            label = "AITRANSS" if "aitranss*.x" in body else "FHI-aims"
            roots = shlex.split(re.search(r"for root in (.*?); do", body).group(1))
            directories = {
                directory
                for root in roots
                for directory in (root, root + "/bin", root + "/scripts", root + "/libexec")
            }
            paths = []
            for directory in directories:
                for path in self.directories.get(directory, ()):
                    name = Path(path).name.casefold()
                    matches = (
                        label == "FHI-aims" and name.startswith("aims") and name.endswith(".x")
                    ) or (
                        label == "AITRANSS" and (
                            name == "aitranss"
                            or name.startswith("aitranss") and name.endswith(".x")
                        )
                    )
                    if matches and path not in paths:
                        paths.append(path)
            return self.result(
                "__AT_CANDIDATES_BEGIN__\n"
                + "".join(path + "\n" for path in paths)
                + "__AT_CANDIDATES_END__\n"
            )
        if "# MOLTAGE_RUNTIME_LOCATION\n" in body:
            tokens = shlex.split(body.split("\n", 1)[1])
            kind, path = tokens[1:3]
            if kind == "-d" and path in self.directories:
                values = ("__AT_DIRECTORY__", *self.directories[path])
            elif kind == "-f" and path in self.files:
                values = (path,)
            else:
                return self.result("", 3)
            return self.result("Login banner\n__AT_CANDIDATES_BEGIN__\n" + "\n".join(values) +
                               "\n__AT_CANDIDATES_END__\n")
        if "# MOLTAGE_RUNTIME_FILE\n" in body:
            tokens = shlex.split(body.split("\n", 1)[1])
            path = tokens[2]
            resolved = self.aliases.get(path, path)
            return self.result("__AT_REALPATH__=" + resolved + "\n") if resolved in self.files else self.result("", 3)
        if "# MOLTAGE_SPECIES_ROOTS\n" in body:
            return self.result(
                "__AT_SPECIES_BEGIN__\n"
                + "".join(path + "\n" for path in self.species_roots)
                + "__AT_SPECIES_END__\n"
            )
        if "# MOLTAGE_RUNTIME_DEPENDENCIES\n" in body:
            modules = tuple(re.findall(r"module load ([\w./+-]+)", body))
            needed = self.needed_by_modules.get(modules, self.needed)
            runpaths = self.runpaths_by_modules.get(modules, ())
            return self.result(
                "".join(
                    f" 0x0 (NEEDED) Shared library: [{name}]\n"
                    for name in needed
                )
                + "".join(
                    f" 0x0 (RUNPATH) Library runpath: [{path}]\n"
                    for path in runpaths
                )
            )
        if "# MOLTAGE_RUNTIME_LIBRARY_CHECK\n" in body:
            modules = tuple(re.findall(r"module load ([\w./+-]+)", body))
            needed = self.needed_by_modules.get(modules, self.needed)
            locations = self.library_locations_by_modules.get(modules, self.library_locations)
            return self.result("".join(f"__AT_LIBRARY__={name}|{locations.get(name, '')}\n"
                                       for name in needed))
        if "__AT_RUNTIME_AVAILABLE__" in body:
            return self.result("__AT_RUNTIME_AVAILABLE__\n")
        if "module -t " in body:
            return self.result("")  # Valid module command, but no matching modules.
        raise AssertionError(command)

    def read_file_head(self, path, max_bytes):
        self.reads.append((path, max_bytes))
        path = self.aliases.get(path, path)
        if path not in self.files:
            raise RemotePathNotFoundError(path)
        return self.files[path][:max_bytes]

    @staticmethod
    def result(text, status=0):
        return RemoteCommandResult(status, text.encode(), b"")


class ModuleSearchRemote(SearchRemote):
    """Only add module-exposure records to the existing offline executor."""

    def __init__(self):
        super().__init__()
        self.paths = {}
        self.environments = {}
        self.listings = {}
        self.files[AITRANSS] = b"\x7fELFsynthetic"
        self.files["/usr/bin/srun"] = b"launcher"

    def execute(self, command):
        body = shlex.split(command)[2]
        if "MOLTAGE_MODULE_CATALOG" in body:
            self.commands.append(command)
            return self.result("\n".join(self.listings.values()))
        if "module -t " in body:
            self.commands.append(command)
            for query, listing in self.listings.items():
                if query in body:
                    return self.result(listing)
            return self.result("", 1)
        if "# MOLTAGE_RUNTIME_PATH\n" in body:
            modules = tuple(re.findall(r"module load ([\w./+-]+)", body))
            self.paths = self.environments.get(modules, {})
        return super().execute(command)


class RuntimeConfigurationTests(unittest.TestCase):
    def test_optional_bash_discovery_reports_unavailable_path(self):
        with patch.object(shutil, "which", return_value=None):
            self.assertIsNone(_bash_executable())

    def test_optional_bash_discovery_uses_path_result(self):
        with patch.object(shutil, "which", return_value="C:/tools/bash.exe"):
            self.assertEqual(_bash_executable(), Path("C:/tools/bash.exe"))

    def test_species_root_discovery_accepts_one_executable_derived_root(self):
        remote = SearchRemote()

        result = discover_runtime_configuration(
            remote,
            RuntimeDiscoveryHints(),
        )

        candidate = result.fhi_aims_candidates[0]
        self.assertEqual(candidate.species_root_path, SPECIES_ROOT)
        self.assertEqual(candidate.species_root_candidates, (SPECIES_ROOT,))

    def test_species_root_manual_hint_is_the_only_inspected_root(self):
        remote = SearchRemote()
        manual_root = "/shared/aims/species_defaults/defaults_2020"
        remote.species_roots = (manual_root,)

        result = discover_runtime_configuration(
            remote,
            RuntimeDiscoveryHints(
                fhi_species_defaults_path=manual_root,
            ),
        )

        candidate = result.fhi_aims_candidates[0]
        self.assertEqual(candidate.species_root_path, manual_root)
        species_command = next(
            shlex.split(command)[2]
            for command in remote.commands
            if "MOLTAGE_SPECIES_ROOTS" in command
        )
        self.assertIn(manual_root, species_command)
        self.assertNotIn("find ", species_command)
        self.assertNotIn("locate", species_command)

    def test_missing_species_root_keeps_fhi_candidate_incomplete(self):
        remote = SearchRemote()
        remote.species_roots = ()

        result = discover_runtime_configuration(
            remote,
            RuntimeDiscoveryHints(),
        )

        candidate = result.fhi_aims_candidates[0]
        self.assertIsNone(candidate.species_root_path)
        self.assertIn(
            "Missing FHI-aims > Species definitions root",
            "\n".join(result.missing_requirements),
        )

    def test_ambiguous_species_roots_require_explicit_selection(self):
        remote = SearchRemote()
        roots = (
            "/opt/fhi-aims/species_defaults/defaults_2010",
            "/opt/fhi-aims/species_defaults/defaults_2020",
        )
        remote.species_roots = roots

        result = discover_runtime_configuration(
            remote,
            RuntimeDiscoveryHints(),
        )

        candidate = result.fhi_aims_candidates[0]
        self.assertIsNone(candidate.species_root_path)
        self.assertEqual(candidate.species_root_candidates, roots)
        message = "\n".join(result.missing_requirements)
        self.assertIn("Species definitions root selection", message)
        self.assertIn(roots[0], message)
        self.assertIn(roots[1], message)
        species_commands = tuple(
            command
            for command in remote.commands
            if "MOLTAGE_SPECIES_ROOTS" in command
        )
        forbidden = ("find ", "locate", "--version", "sbatch", "source ")
        self.assertFalse(
            any(token in command for command in species_commands for token in forbidden)
        )

    def test_first_successful_module_tier_is_shared_and_stops_before_spider(self):
        remote = ModuleSearchRemote()
        remote.listings = {"module -t avail aims": "chem/aims\n"}
        remote.environments[("chem/aims",)] = {"aims.x": AIMS, "aitranss": AITRANSS, "mpirun": MPI}
        result = discover_server_runtimes(remote)
        self.assertTrue(result.fhi_aims_candidates and result.aitranss_candidates)
        listings = [cmd for cmd in remote.commands if "module -t" in cmd]
        self.assertEqual(len(listings), 1)
        self.assertIn("module -t avail aims", listings[0])
        self.assertIn("module -t avail aitranss", listings[0])
        self.assertEqual(sum("RUNTIME_PATH" in cmd for cmd in remote.commands), 2)  # Login + module.
        self.assertFalse(any("spider" in cmd or "avail AIMS" in cmd for cmd in remote.commands))

    def test_failed_avail_tiers_still_reach_spider_once_for_both_programs(self):
        remote = ModuleSearchRemote()
        remote.listings = {"module -t spider aims": "chem/aims\n"}
        remote.environments[("chem/aims",)] = {"aims.x": AIMS, "aitranss": AITRANSS, "mpirun": MPI}
        result = discover_server_runtimes(remote)
        self.assertTrue(result.fhi_aims_candidates and result.aitranss_candidates)
        self.assertEqual(sum("module -t" in cmd for cmd in remote.commands), 3)
        self.assertFalse(any("spider AIMS" in cmd for cmd in remote.commands))

    def test_multiple_modules_in_successful_tier_remain_explicit_candidates(self):
        remote = ModuleSearchRemote()
        remote.listings = {"module -t avail aims": "chem/aims1\nchem/aims2\n"}
        for version in (1, 2):
            aims = f"/opt/build{version}/aims.x"
            remote.files[aims] = b"\x7fELFsynthetic"
            remote.environments[(f"chem/aims{version}",)] = {
                "aims.x": aims, "aitranss": AITRANSS, "mpirun": MPI}
        result = discover_server_runtimes(remote)
        self.assertEqual(len(result.fhi_aims_candidates), 2)
        self.assertEqual(sum("module -t" in cmd for cmd in remote.commands), 1)

    def test_missing_dependencies_build_only_observably_improving_module_chain(self):
        remote = ModuleSearchRemote()
        mpi_module = "mpi/example-1.0"
        mkl_module = "math/mkl-example-1.0"
        wrong_mpi = "mpi/unrelated-old-build"
        modules = (mpi_module, mkl_module)
        remote.listings = {
            "module -t avail mpi": wrong_mpi + "\n" + mpi_module + "\n",
            "module -t avail mkl": mkl_module + "\n",
        }
        remote.environments[()] = {"aims.x": AIMS, "aitranss": AITRANSS}
        remote.environments[(mpi_module,)] = {"mpirun": MPI}
        remote.environments[(mkl_module,)] = {}
        remote.environments[modules] = {
            "mpirun": MPI,
        }
        remote.needed = ("libmpi.so.12", "libmkl_core.so.1")
        remote.library_locations_by_modules[(mpi_module,)] = {
            "libmpi.so.12": "/mpi/lib/libmpi.so.12",
        }
        remote.library_locations_by_modules[(mkl_module,)] = {
            "libmkl_core.so.1": "/mkl/lib/libmkl_core.so.1",
        }
        remote.library_locations_by_modules[modules] = {
            "libmpi.so.12": "/mpi/lib/libmpi.so.12",
            "libmkl_core.so.1": "/mkl/lib/libmkl_core.so.1",
        }

        result = discover_server_runtimes(remote)

        self.assertEqual(result.fhi_aims_candidates[0].modules, modules)
        self.assertEqual(result.fhi_aims_candidates[0].launcher_path, MPI)
        self.assertEqual(result.aitranss_candidates[0].modules, modules)
        self.assertTrue(
            any("MOLTAGE_MODULE_CATALOG" in command for command in remote.commands)
        )
        self.assertFalse(any(
            mpi_module + " " + mkl_module in command
            for command in remote.commands
            if "module -t" in command
        ))

    def test_target_style_mkl_then_mpi_chain_reuses_environment_for_aitranss(self):
        remote = ModuleSearchRemote()
        aims = "/srv/moltage-test/users/scientist/.local/bin/aims.cluster.scalapack.mpi.x"
        aitranss = "/srv/moltage-test/users/scientist/apps/fhi-aims/scripts/aitranss.x"
        mkl_module = "math/mkl-example-1.0"
        mpi_module = "mpi/example-1.0"
        modules = (mkl_module, mpi_module)
        libraries = (
            "libmkl_scalapack_lp64.so.1",
            "libmkl_intel_lp64.so.1",
            "libmkl_sequential.so.1",
            "libmkl_core.so.1",
            "libmkl_blacs_intelmpi_lp64.so.1",
        )
        located = {name: "/srv/moltage-test/apps/mkl/lib/" + name for name in libraries}
        remote.files.update({
            aims: b"\x7fELFsynthetic",
            aitranss: b"\x7fELFsynthetic",
        })
        remote.environments[()] = {
            "aims.cluster.scalapack.mpi.x": aims,
            "mpirun": MPI,
        }
        remote.environments[(mkl_module,)] = {}
        remote.environments[modules] = {
            "aims.cluster.scalapack.mpi.x": aims,
            "aitranss.x": aitranss,
            "mpirun": MPI,
        }
        remote.listings = {
            "module -t avail mkl": "/" + mkl_module + "\n",
            "module -t avail mpi": "/" + mpi_module + "\n",
        }
        remote.needed_by_modules.update({
            (): libraries,
            (mkl_module,): libraries,
            modules: libraries,
        })
        remote.library_locations_by_modules.update({
            (mkl_module,): located,
            modules: located,
        })

        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())

        self.assertEqual(len(result.fhi_aims_candidates), 1)
        self.assertEqual(result.fhi_aims_candidates[0].environment.modules, modules)
        self.assertTrue(result.fhi_aims_candidates[0].environment_resolved)
        self.assertEqual(result.fhi_aims_candidates[0].launcher_path, MPI)
        self.assertEqual(result.aitranss_candidates[0].executable_path, aitranss)
        self.assertEqual(result.aitranss_candidates[0].environment.modules, modules)
        self.assertFalse(any(
            "Runtime search limit reached" in note for note in result.diagnostics
        ))
        self.assertLess(len(remote.commands), 30)
        self.assertFalse(any(
            "find " in shlex.split(command)[2] for command in remote.commands
        ))

    def test_namespaced_catalog_uses_elf_version_evidence_before_old_modules(self):
        base = RuntimeCandidate(
            "NONE",
            (),
            AIMS,
            AIMS,
            NONE,
            environment_resolved=False,
            missing_dependencies=("libmkl_core.so.1",),
            dependency_search_paths=(
                "/srv/moltage-test/apps/toolchain-example-1.0/lib",
            ),
        )
        modules = (
            "math/mkl-legacy-a",
            "math/mkl-legacy-b",
            "math/mkl-example-1.0",
        )

        self.assertEqual(
            _rank_support_modules(modules, base)[0],
            "math/mkl-example-1.0",
        )

    def test_mpirun_must_own_the_elf_resolved_mpi_libraries(self):
        old = "/srv/moltage-test/apps/mpi-legacy/bin/mpirun"
        matching = "/srv/moltage-test/apps/mpi-example-1.0/bin/mpirun"
        report = _DependencyReport(
            ("checked",),
            needed=("libmpi.so.12",),
            path_hints=("/srv/moltage-test/apps/mpi-example-1.0/lib",),
            inspected=True,
            located=((
                "libmpi.so.12",
                "/srv/moltage-test/apps/mpi-example-1.0/lib/libmpi.so.12",
            ),),
        )

        self.assertEqual(
            _compatible_launcher_candidates((old, matching), report),
            (matching,),
        )

    def test_path_advertised_shallow_layout_finds_unexported_aitranss(self):
        remote = SearchRemote()
        aims = "/srv/moltage-test/users/scientist/.local/bin/aims.cluster.scalapack.mpi.x"
        aitranss = "/srv/moltage-test/users/scientist/work/opt/fhi-aims/scripts/aitranss.synthetic.x"
        remote.files.update({aims: b"\x7fELFsynthetic", aitranss: b"\x7fELFsynthetic"})
        remote.paths = {"aims.cluster.scalapack.mpi.x": aims, "mpirun": MPI}
        remote.advertised_paths["AITRANSS"] = (aitranss,)

        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())

        self.assertEqual(result.fhi_aims_candidates[0].executable_path, aims)
        self.assertEqual(result.aitranss_candidates[0].executable_path, aitranss)
        self.assertTrue(any(
            "RUNTIME_ADVERTISED_LAYOUT" in command for command in remote.commands
        ))
        self.assertFalse(any(
            "find " in shlex.split(command)[2] for command in remote.commands
        ))

    def test_target_shape_catalog_finds_minimal_program_environments(self):
        remote = ModuleSearchRemote()
        aims = "/srv/moltage-test/users/scientist/.local/bin/aims.cluster.scalapack.mpi.x"
        aitranss = "/srv/moltage-test/users/scientist/work/opt/fhi-aims/scripts/aitranss.synthetic.x"
        mkl = "math/mkl-example-1.0"
        mpi = "mpi/example-1.0"
        full = (mkl, mpi)
        old_mpi = "mpi/legacy-build"
        needed = ("libmkl_core.so.1", "libmpi.so.12")
        mpi_library = "/srv/moltage-test/apps/mpi-example-1.0/lib/libmpi.so.12"
        mkl_library = "/srv/moltage-test/apps/mkl-example-1.0/lib/libmkl_core.so.1"
        launcher = "/srv/moltage-test/apps/mpi-example-1.0/bin/mpirun"
        remote.files.update({
            aims: b"\x7fELFsynthetic",
            aitranss: b"\x7fELFsynthetic",
            launcher: b"launcher",
        })
        remote.environments[()] = {"aims.cluster.scalapack.mpi.x": aims}
        remote.environments[(mkl,)] = {}
        remote.environments[full] = {
            "mpirun": launcher,
        }
        remote.advertised_paths["AITRANSS"] = (aitranss,)
        remote.listings = {
            "catalog": "\n".join((
                "math/mkl-legacy-a",
                mkl,
                old_mpi,
                mpi,
            )) + "\n",
        }
        remote.needed = needed
        remote.needed_by_modules.update({(): needed, (mkl,): needed, full: needed})
        remote.runpaths_by_modules.update({
            (): ("/srv/moltage-test/apps/mpi-example-1.0/lib",),
            (mkl,): ("/srv/moltage-test/apps/mpi-example-1.0/lib",),
            full: ("/srv/moltage-test/apps/mpi-example-1.0/lib",),
        })
        remote.library_locations = {"libmpi.so.12": mpi_library}
        remote.library_locations_by_modules[(mkl,)] = {
            "libmpi.so.12": mpi_library,
            "libmkl_core.so.1": mkl_library,
        }
        remote.library_locations_by_modules[full] = {
            "libmpi.so.12": mpi_library,
            "libmkl_core.so.1": mkl_library,
        }

        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())

        self.assertEqual(result.fhi_aims_candidates[0].modules, full)
        self.assertEqual(result.aitranss_candidates[0].modules, (mkl,))
        self.assertEqual(result.missing_requirements, ())
        self.assertFalse(any("search limit" in note.casefold() for note in result.diagnostics))
        self.assertFalse(any("module load " + old_mpi in command for command in remote.commands))

    def test_equivalent_partial_environments_are_collapsed_for_presentation(self):
        missing = ("libmkl_core.so.1",)
        first = RuntimeCandidate(
            "mkl/one",
            ("mkl/one",),
            AIMS,
            AIMS,
            RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, ("mkl/one",)),
            MPI,
            environment_resolved=False,
            missing_dependencies=missing,
        )
        second = RuntimeCandidate(
            "compiler/one / mkl/two",
            ("compiler/one", "mkl/two"),
            AIMS,
            AIMS,
            RuntimeEnvironment(
                RuntimeEnvironmentMode.MODULES,
                ("compiler/one", "mkl/two"),
            ),
            MPI,
            environment_resolved=False,
            missing_dependencies=missing,
        )

        retained = _retain_best_candidates("FHI-aims", (second, first))

        self.assertEqual(retained, (first,))

    def test_module_results_keep_legacy_launch_options_path(self):
        remote = ModuleSearchRemote()
        canonical = "/opt/fhi/bin/" + FHI_AIMS_EXECUTABLE_COMMAND
        remote.files[canonical] = b"\x7fELFsynthetic"
        configured = ("mpi/example-1.0", "chemistry/fhi-aims-example")
        remote.environments[configured] = {FHI_AIMS_EXECUTABLE_COMMAND: canonical, "srun": "/usr/bin/srun"}
        remote.environments[("chemistry/aitranss-example",)] = {"aitranss.x": AITRANSS}
        remote.listings = {"module -t avail aims": "chemistry/aitranss-example\n"}
        result = discover_server_runtimes(remote, configured)
        self.assertEqual(result.fhi_aims_candidates[0].modules, configured)
        self.assertIsNone(result.fhi_aims_candidates[0].environment)
        self.assertIsNone(result.aitranss_candidates[0].environment)
        self.assertEqual(sum("module -t" in cmd for cmd in remote.commands), 1)

    def test_synthetic_wrapper_layout_uses_one_path_inventory_and_no_module_listing(self):
        remote = SearchRemote()
        # The paths and library records below are intentionally synthetic.
        root = "/srv/moltage-test/apps/fhi-aims-wrapper-layout"
        aims = root + "/bin/aims.scalapack.mpi.x"
        aitranss = root + "/bin/aitranss.synthetic.x"
        wrapper = root + "/scripts/run_aims.mpi"
        launcher = "/srv/moltage-test/apps/mpi-example-1.0/bin/mpirun"
        remote.files = {aims: b"\x7fELFsynthetic", aitranss: b"\x7fELFsynthetic",
                        wrapper: WRAPPER_BYTES, launcher: b"launcher", "/usr/bin/mpirun": b"launcher"}
        remote.paths = {"aims.scalapack.mpi.x": aims, "run_aims.mpi": wrapper,
                        "mpirun": (launcher, "/usr/bin/mpirun")}
        remote.directories[root + "/bin"] = [aitranss]
        remote.needed = ("libmpi.so.12",)
        remote.library_locations = {"libmpi.so.12": "/synthetic/lib/libmpi.so.12"}
        result = discover_server_runtimes(remote)
        self.assertEqual(result.fhi_aims_candidates[0].executable_path, aims)
        self.assertEqual(result.aitranss_candidates[0].executable_path, aitranss)
        self.assertIsNone(result.fhi_aims_candidates[0].launcher_path)
        self.assertEqual(result.fhi_aims_candidates[0].launcher_candidates, (launcher, "/usr/bin/mpirun"))
        self.assertTrue(any("compatibility" in note for note in result.aitranss_candidates[0].notes))
        self.assertEqual(sum("# MOLTAGE_RUNTIME_PATH\n" in shlex.split(cmd)[2]
                             for cmd in remote.commands), 1)
        self.assertFalse(any("module -t" in cmd or "RUNTIME_FAMILY" in cmd for cmd in remote.commands))
        self.assertEqual(remote.reads.count((aims, 4)), 1)
        self.assertLessEqual(len(remote.commands), 11)

    def test_resolved_symlink_root_finds_sibling_runtime_without_scanning(self):
        remote = SearchRemote()
        callable_aims = "/srv/moltage-test/users/scientist/.local/bin/aims.cluster.scalapack.mpi.x"
        root = "/share/apps/fhi-aims"
        aims = root + "/bin/aims.cluster.scalapack.mpi.x"
        aitranss = root + "/scripts/aitranss.synthetic.x"
        remote.paths = {"aims.x": callable_aims, "mpirun": MPI}
        remote.aliases[callable_aims] = aims
        remote.files.update({
            aims: b"\x7fELFsynthetic",
            aitranss: b"\x7fELFsynthetic",
        })
        remote.directories[root + "/scripts"] = (aitranss,)

        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())

        self.assertEqual(result.fhi_aims_candidates[0].executable_path, aims)
        self.assertEqual(result.aitranss_candidates[0].executable_path, aitranss)
        self.assertFalse(any("RUNTIME_INDEX" in command for command in remote.commands))
        self.assertFalse(any("find " in shlex.split(command)[2] for command in remote.commands))

    def test_filename_index_is_queried_once_and_candidates_are_verified(self):
        remote = SearchRemote()
        indexed_aims = "/srv/software/fhi/bin/aims.cluster.scalapack.mpi.x"
        indexed_aitranss = "/srv/software/fhi/scripts/aitranss.synthetic.x"
        remote.paths = {"mpirun": MPI}
        remote.files.update({
            indexed_aims: b"\x7fELFsynthetic",
            indexed_aitranss: b"\x7fELFsynthetic",
        })
        remote.index_available = True
        remote.indexed_paths = {
            "FHI-aims": (indexed_aims,),
            "AITRANSS": (indexed_aitranss,),
        }

        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())

        self.assertEqual(result.fhi_aims_candidates[0].executable_path, indexed_aims)
        self.assertEqual(result.aitranss_candidates[0].executable_path, indexed_aitranss)
        self.assertEqual(sum("RUNTIME_INDEX" in command for command in remote.commands), 1)
        self.assertFalse(any("find " in shlex.split(command)[2] for command in remote.commands))
        self.assertFalse(any("module -t" in command for command in remote.commands))

    def test_unadvertised_missing_paths_have_actionable_manual_prompts(self):
        remote = SearchRemote()
        remote.files = {MPI: b"launcher"}
        remote.paths = {"mpirun": MPI}

        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())

        message = "\n".join(result.missing_requirements)
        self.assertIn("Missing FHI-aims > Remote path", message)
        self.assertIn("aims.cluster.scalapack.mpi.x", message)
        self.assertIn("Missing FHI-aims > MPI launcher selection", message)
        self.assertIn(MPI, message)
        self.assertIn("found independently", message)
        self.assertIn("Missing AITRANSS > Remote path", message)
        self.assertIn("aitranss*.x", message)
        self.assertIn("Manual Configuration", message)
        self.assertIn("plocate/locate", message)
        self.assertFalse(any("find " in shlex.split(command)[2] for command in remote.commands))

    def test_mpirun_aliases_are_deduplicated_without_changing_callable_name(self):
        remote = SearchRemote()
        realpath = "/opt/mpi/libexec/launcher.hydra"
        remote.paths["mpirun"] = (MPI, "/usr/bin/mpirun")
        remote.files[realpath] = b"launcher"
        remote.aliases.update({MPI: realpath, "/usr/bin/mpirun": realpath})
        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints(
            aitranss=RuntimeLocation(environment=NONE)))
        self.assertEqual(result.fhi_aims_candidates[0].launcher_candidates, (MPI,))
        self.assertEqual(result.fhi_aims_candidates[0].launcher_path, MPI)

    def test_noncanonical_path_candidate_does_not_abort_valid_runtime_discovery(self):
        remote = SearchRemote()
        remote.paths["mpirun"] = ("/opt/mpi/../mpi/bin/mpirun", MPI)

        search = _Search(remote)
        paths = search.paths_on_path(("mpirun",), NONE)

        self.assertEqual(paths, {"mpirun": (MPI,)})
        self.assertEqual(
            search.diagnostics,
            ["Ignored 1 unsafe or non-canonical PATH runtime candidate(s)"],
        )

    def test_shell_inventory_finds_same_name_paths_without_running_wrappers(self):
        bash = _bash_executable()
        if bash is None:
            self.skipTest("bash is not available on PATH for shell-query validation")
        remote = SearchRemote()
        search = _Search(remote)
        search.paths_on_path(("mpirun",), NONE)
        search.path_family("AITRANSS", NONE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("first", "second"):
                folder = root / name
                folder.mkdir()
                for filename in ("mpirun", "aitranss.synthetic.x"):
                    target = folder / filename
                    target.write_text("#!/bin/sh\nprintf 'WRAPPER_WAS_EXECUTED'\nexit 9\n", encoding="utf-8")
                    target.chmod(0o755)
            # Convert this known local drive path for the bundled Git Bash.
            unix_root = "/" + root.drive[0].lower() + root.as_posix()[2:]
            for command in remote.commands:
                body = 'PATH=' + shlex.quote(
                    unix_root + "/first/../first/:" + unix_root + "/second"
                ) + "; " + shlex.split(command)[2]
                result = subprocess.run([str(bash), "--noprofile", "--norc", "-c", body],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("WRAPPER_WAS_EXECUTED", result.stdout)
                suffix = "mpirun" if "RUNTIME_PATH" in command else "aitranss.synthetic.x"
                self.assertIn(unix_root + "/first/" + suffix, result.stdout)
                self.assertIn(unix_root + "/second/" + suffix, result.stdout)

    def test_versioned_path_fallback_is_only_used_after_same_bin_and_is_bounded(self):
        remote = SearchRemote()
        alternate = "/other/bin/aitranss.synthetic.x"
        remote.family_paths["AITRANSS"] = (alternate,)
        remote.files[alternate] = b"\x7fELFsynthetic"
        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())
        self.assertEqual(result.aitranss_candidates[0].executable_path, alternate)
        family_index = next(i for i, cmd in enumerate(remote.commands) if "RUNTIME_FAMILY" in cmd)
        self.assertTrue(any("RUNTIME_LAYOUT" in cmd for cmd in remote.commands[:family_index]))
        body = shlex.split(remote.commands[family_index])[2]
        self.assertIn('"$checked" -gt 32', body)
        self.assertNotIn("find ", body)
        self.assertFalse(any("module -t" in cmd for cmd in remote.commands))

    def test_bad_named_aitranss_does_not_hide_valid_same_bin_candidate(self):
        remote = SearchRemote()
        remote.paths["aitranss"] = WRAPPER  # Executable script, not the binary.
        remote.files[AITRANSS] = b"\x7fELFsynthetic"
        remote.directories["/opt/fhi-aims/bin"] = (AITRANSS,)
        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())
        self.assertEqual(result.aitranss_candidates[0].executable_path, AITRANSS)
        self.assertFalse(any("module -t" in cmd for cmd in remote.commands))

    def test_budget_stop_retains_found_program_without_starting_another_search(self):
        remote = SearchRemote()
        with patch("moltage.remote.runtime_search.MAX_COMMANDS", 6):
            result = discover_server_runtimes(remote)
        self.assertEqual(result.fhi_aims_candidates[0].executable_path, AIMS)
        self.assertEqual(len(remote.commands), 6)
        self.assertTrue(any("partial results retained" in note for note in result.diagnostics))

    def test_transport_error_during_module_fallback_is_not_a_budget_or_absence(self):
        remote = SearchRemote()
        execute = remote.execute
        failure = RemoteExecutorError("connection lost")

        def fail_in_modules(command):
            if "module -t" in command:
                raise failure
            return execute(command)

        with patch.object(remote, "execute", side_effect=fail_in_modules):
            with self.assertRaises(RemoteExecutorError) as caught:
                discover_server_runtimes(remote)
        self.assertIs(caught.exception, failure)

    def test_six_rank_scripts_preserve_resources_and_retry_readback(self):
        server = profile()
        preset = replace(server.execution_preset, ntasks=6, nodes=1, cpus_per_task=1,
                         fhi_runtime=FhiAimsRuntimeConfiguration(AIMS, MPI, NONE))
        for step in (ProjectStepKind.MOLECULE_OPT, ProjectStepKind.MOLECULE_AU_OPT,
                     ProjectStepKind.TRANSPORT_CONVERGENCE):
            script = render_submit_script(preset, server.profile_id, step)
            self.assertIn("#SBATCH --ntasks=6\n", script)
            self.assertIn("#SBATCH --cpus-per-task=1\n", script)
            self.assertEqual(script.splitlines()[-1], f"exec {MPI} -n 6 {AIMS}")
            self.assertNotIn("module ", script)
            self.assertNotIn("salloc", script)
            self.assertNotIn("tee ", script)
            self.assertEqual(preset_with_submit_script_resources(preset, script), preset)

    def test_empty_modules_reach_reviewed_wrapper_without_executing_it(self):
        remote = SearchRemote()
        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())
        self.assertEqual(len(result.fhi_aims_candidates), 1)
        candidate = result.fhi_aims_candidates[0]
        self.assertEqual((candidate.executable_path, candidate.launcher_path), (AIMS, MPI))
        self.assertEqual(candidate.environment, NONE)
        self.assertTrue(candidate.environment_resolved)
        self.assertEqual(result.aitranss_candidates, ())
        self.assertTrue(any("AITRANSS" in note for note in result.diagnostics))
        self.assertIn((WRAPPER, 65537), remote.reads)
        self.assertFalse(any(word in command for command in remote.commands
                             for word in ("salloc", "sbatch", "tee aims", "ldd ", "source ")))

    def test_directory_seed_preserves_constraint_and_returns_multiple_candidates(self):
        remote = SearchRemote()
        alternative = "/custom install/bin/aims.alternative.x"
        other = "/custom install/bin/aims.x"
        remote.files[alternative] = b"\x7fELFsynthetic"
        remote.files[other] = b"\x7fELFsynthetic"
        remote.directories["/custom install"] = [alternative, other]
        hints = RuntimeDiscoveryHints(RuntimeLocation("/custom install", NONE), MPI,
                                      RuntimeLocation(environment=NONE))
        result = discover_runtime_configuration(remote, hints)
        self.assertEqual(len(result.fhi_aims_candidates), 2)
        self.assertEqual(hints.fhi_aims.location, "/custom install")
        self.assertTrue(all(item.launcher_path == MPI for item in result.fhi_aims_candidates))
        bodies = [shlex.split(command)[2] for command in remote.commands]
        self.assertTrue(any("find -H '/custom install' -maxdepth 3" in body for body in bodies))
        self.assertFalse(any("module -t" in body for body in bodies))

    def test_explicit_bad_file_never_substitutes_another_installation(self):
        remote = SearchRemote()
        remote.paths["aims.x"] = AIMS
        hints = RuntimeDiscoveryHints(
            RuntimeLocation("/missing/aims.x", NONE, RuntimeLocationKind.EXECUTABLE), MPI,
            RuntimeLocation(environment=NONE))
        result = discover_runtime_configuration(remote, hints)
        self.assertEqual(result.fhi_aims_candidates, ())
        self.assertNotIn((AIMS, 4), remote.reads)

    def test_multiple_launchers_are_reported_for_explicit_choice_not_guessed(self):
        remote = SearchRemote()
        remote.paths["srun"] = "/usr/bin/srun"
        remote.files["/usr/bin/srun"] = b"launcher"
        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints(
            RuntimeLocation(AIMS, NONE, RuntimeLocationKind.EXECUTABLE),
            aitranss=RuntimeLocation(environment=NONE)))
        candidate = result.fhi_aims_candidates[0]
        self.assertIsNone(candidate.launcher_path)
        self.assertEqual(candidate.launcher_candidates, ("/usr/bin/srun", MPI))

    def test_transport_errors_propagate_without_hidden_fallback(self):
        remote = SearchRemote()
        failure = RemoteExecutorError("connection lost")
        with patch.object(remote, "execute", side_effect=failure) as execute:
            with self.assertRaises(RemoteExecutorError) as caught:
                discover_runtime_configuration(remote, RuntimeDiscoveryHints())
        self.assertIs(caught.exception, failure)
        execute.assert_called_once()

    def test_missing_library_does_not_resolve_auto_environment(self):
        remote = SearchRemote()
        remote.needed = ("libmpi.so.40",)
        result = discover_runtime_configuration(remote, RuntimeDiscoveryHints())
        candidate = result.fhi_aims_candidates[0]
        self.assertFalse(candidate.environment_resolved)
        self.assertIn("libmpi.so.40", candidate.notes[0])
        self.assertTrue(any("module -t" in command for command in remote.commands))
        message = "\n".join(result.missing_requirements)
        self.assertIn("Missing FHI-aims > Environment", message)
        self.assertIn("libmpi.so.40", message)
        self.assertIn("choose Modules", message)

    def test_script_setup_is_only_used_after_explicit_authorization(self):
        remote = SearchRemote()
        environment = RuntimeEnvironment(RuntimeEnvironmentMode.SCRIPT, setup_script="/opt/env/setup.sh")
        hints = RuntimeDiscoveryHints(RuntimeLocation(AIMS, environment, RuntimeLocationKind.EXECUTABLE), MPI,
                                      RuntimeLocation(environment=NONE))
        with self.assertRaisesRegex(RuntimeDiscoveryError, "explicit permission"):
            discover_runtime_configuration(remote, hints)
        self.assertEqual(remote.commands, [])
        result = discover_runtime_configuration(remote, hints, allow_setup_scripts=True)
        self.assertEqual(result.fhi_aims_candidates[0].environment, environment)
        self.assertIn('source /opt/env/setup.sh || exit "$?"', shlex.split(remote.commands[0])[2])

    def test_limits_stop_search_and_unsupported_wrapper_is_not_guessed(self):
        remote = SearchRemote()
        remote.directories["/wide"] = [f"/wide/aims.{number}.x" for number in range(MAX_CANDIDATES + 1)]
        with self.assertRaisesRegex(RuntimeDiscoveryError, "Too many"):
            discover_runtime_configuration(remote, RuntimeDiscoveryHints(RuntimeLocation("/wide", NONE)))
        remote.files[WRAPPER] = b"#!/bin/bash\nsource /private/unknown.sh\n"
        search = _Search(remote)
        self.assertIsNone(search.wrapper_target(WRAPPER, NONE))
        with patch("moltage.remote.runtime_search.MAX_COMMANDS", 0):
            with self.assertRaisesRegex(RuntimeDiscoveryError, "limit"):
                _Search(remote).paths_on_path(("aims.x",), NONE)

    def test_synthetic_shell_queries_are_valid_bash_without_executing_them(self):
        bash = _bash_executable()
        if bash is None:
            self.skipTest("bash is not available on PATH for syntax-only validation")
        remote = SearchRemote()
        remote.needed = ("libmpi.so.40",)
        remote.directories["/custom install"] = [AIMS]
        discover_runtime_configuration(remote, RuntimeDiscoveryHints(RuntimeLocation("/custom install")))
        _Search(remote).wrapper_target(WRAPPER, NONE)
        for command in remote.commands:
            check = subprocess.run([str(bash), "--noprofile", "--norc", "-n"],
                                   input=shlex.split(command)[2], text=True, capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_runtime_profile_round_trip_and_schema4_compatibility(self):
        server = profile()
        hints = RuntimeDiscoveryHints(RuntimeLocation("/opt/fhi-aims", NONE), MPI,
                                      RuntimeLocation(AITRANSS, NONE, RuntimeLocationKind.EXECUTABLE))
        configured = replace(server, runtime_hints=hints,
            execution_preset=replace(server.execution_preset,
                fhi_runtime=FhiAimsRuntimeConfiguration(AIMS, MPI, NONE)),
            aitranss_runtime=AitranssRuntimeConfiguration((), AITRANSS, NONE))
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(configured)
            self.assertEqual(repository.load().profiles, (configured,))
            repository.save(server)
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            document["schema_version"] = 4
            item = document["profiles"][0]
            item.pop("runtime_hints")
            item["execution_preset"].pop("fhi_runtime")
            item["aitranss_runtime"].pop("environment")
            repository.path.write_text(json.dumps(document), encoding="utf-8")
            expected_legacy = replace(
                server,
                execution_preset=replace(
                    server.execution_preset,
                    slurm_aitranss_srun_path=None,
                ),
            )
            self.assertEqual(repository.load().profiles, (expected_legacy,))

    def test_configured_files_feed_step_scripts_without_changing_mail_or_resources(self):
        server = profile(email_enabled=True, email_recipient="user@example.org")
        for environment in (NONE, RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, ("compiler/1",)),
                            RuntimeEnvironment(RuntimeEnvironmentMode.SCRIPT, setup_script="/opt/env/setup.sh")):
            for launcher in (MPI, "/usr/bin/srun"):
                preset = replace(server.execution_preset,
                    fhi_runtime=FhiAimsRuntimeConfiguration(AIMS, launcher, environment))
                script = render_submit_script(preset, server.profile_id, ProjectStepKind.MOLECULE_OPT,
                                              mail_settings=server.slurm_mail_settings)
                self.assertTrue(script.startswith("#!/bin/bash -l\n"))
                self.assertIn("#SBATCH --mail-type=END,FAIL", script)
                self.assertIn("#SBATCH --ntasks=24", script)
                expected = f"exec {MPI} -n 24 {AIMS}" if launcher == MPI else f"exec /usr/bin/srun --kill-on-bad-exit=1 {AIMS}"
                self.assertEqual(script.splitlines()[-1], expected)
                if environment == NONE:
                    self.assertNotIn("module ", script)
                runtime = AitranssRuntimeConfiguration(environment.modules, AITRANSS, environment)
                ait = render_aitranss_submit_script(profile_preset=preset,
                    settings=AitranssExecutionSettings(), project_id=server.profile_id,
                    executable_path=AITRANSS, aitranss_modules=runtime.modules, runtime=runtime,
                    mail_settings=server.slurm_mail_settings)
                self.assertIn("exec /usr/bin/srun --ntasks=1 " + AITRANSS, ait)
                self.assertNotIn("exec " + MPI, ait)
                if environment == NONE:
                    self.assertNotIn("module ", ait)

    def test_preflight_never_executes_binary_and_keeps_transport_errors_typed(self):
        remote = SearchRemote()
        verify_configured_runtime(remote, AIMS, NONE, MPI)
        self.assertEqual(remote.reads, [(AIMS, 4)])
        remote.files[AITRANSS] = b"\x7fELFsynthetic"
        runtime = AitranssRuntimeConfiguration((), AITRANSS, NONE)
        self.assertEqual(discover_aitranss_executable(remote, runtime).executable_path, AITRANSS)
        with patch.object(remote, "execute", return_value=SearchRemote.result("", 3)):
            with self.assertRaises(RuntimeConfigurationError):
                verify_configured_runtime(remote, AIMS, NONE, MPI)
        with patch.object(remote, "execute", side_effect=RemoteExecutorError("transport lost")):
            with self.assertRaises(RemoteExecutorError):
                discover_aitranss_executable(remote, runtime)
        with self.assertRaisesRegex(RuntimeConfigurationError, "startup script"):
            verify_configured_runtime(remote, WRAPPER, NONE, MPI)


if __name__ == "__main__":
    unittest.main()
