"""Offline tests for importing one completed external ORCA optimization."""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
import shlex
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_import import (
    MANAGED_IMPORT_FILENAMES,
    OrcaImportError,
    OrcaImportRequest,
    OrcaImportValidationRequest,
    OrcaOptimizationImportService,
    default_managed_base_name,
)
from moltage.app.project_recovery import ProjectRecoveryService
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
)
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaMethod,
    OrcaVersionEvidence,
    OrcaVersionFamily,
)
from moltage.orca.import_evidence import (
    OrcaImportEvidenceError,
    OrcaImportCandidate,
    resolve_orca_xyzfile_path,
    survey_orca_import_candidates,
    validate_orca_import_optimization,
)
from moltage.orca.project_evidence import (
    OrcaImportWavefunctionReadiness,
    OrcaOptimizationOrigin,
)
from moltage.remote.executor import (
    RemoteCommandResult,
    RemoteDirectoryEntry,
    RemotePathAlreadyExistsError,
    RemotePathNotFoundError,
    RemotePathStat,
)
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from phase2b1_test_support import MemorySecretStore, profile
from synthetic_test_data import SYNTHETIC_REMOTE_ROOT
from test_project_submission import FixedConnectionService


NOW = datetime(2030, 5, 4, 9, 30, tzinfo=timezone.utc)
PROJECT_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
PROFILE_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
SOURCE = "/home/scientist/legacy runs/benzenedithiol opt"
ORCA = "/apps/example/orca-6.1/orca"
ORCA_2JSON = "/apps/example/orca-6.1/orca_2json"
ORCA_2MKL = "/apps/example/orca-6.1/orca_2mkl"


def orca_input(charge: int = 0, multiplicity: int = 1, keywords: str = "PBE0 DEF2-TZVP OPT") -> bytes:
    return (
        f"! {keywords}\n"
        "%pal\n  nprocs 8\nend\n"
        f"* xyz {charge} {multiplicity}\n"
        "  S 0.0 0.0 0.0\n"
        "  C 1.8 0.0 0.0\n"
        "  H 2.4 0.9 0.0\n"
        "*\n"
    ).encode("utf-8")


def orca_output(converged: bool = True, terminated: bool = True) -> bytes:
    lines = ["synthetic ORCA optimization log"]
    if converged:
        lines.append("THE OPTIMIZATION HAS CONVERGED")
    else:
        lines.append("MAXIMUM NUMBER OF OPTIMIZATION CYCLES REACHED")
    if terminated:
        lines.append("ORCA TERMINATED NORMALLY")
    return ("\n".join(lines) + "\n").encode("utf-8")


def orca_geometry(elements: tuple[str, ...] = ("S", "C", "H")) -> bytes:
    rows = "\n".join(
        f"{element} {index}.0 0.0 0.0" for index, element in enumerate(elements)
    )
    return f"{len(elements)}\nsynthetic optimized geometry\n{rows}\n".encode("utf-8")


def orca_xyzfile_input(path: str, newline: str = "\n") -> bytes:
    return newline.join(
        (
            "! PBE0 DEF2-TZVP OPT",
            "%pal",
            "  nprocs 8",
            "end",
            f"*xyzfile 0 2 {path}",
            "",
        )
    ).encode("utf-8")


def start_geometry(elements: tuple[str, ...] = ("S", "C", "H")) -> bytes:
    rows = "\n".join(
        f"{element} {index}.25 0.5 -0.125" for index, element in enumerate(elements)
    )
    return f"{len(elements)}\nsynthetic starting geometry\n{rows}\n".encode("utf-8")


INLINED_INPUT = (
    b"! PBE0 DEF2-TZVP OPT\n%pal\n  nprocs 8\nend\n"
    b"* xyz 0 2\n  S 0.25 0.5 -0.125\n  C 1.25 0.5 -0.125\n  H 2.25 0.5 -0.125\n*\n"
)


GBW = b"synthetic binary wavefunction payload"


def runtime() -> OrcaRuntimeConfiguration:
    return OrcaRuntimeConfiguration(
        ORCA,
        RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
        OrcaVersionEvidence(
            "Program Version 6.1.2",
            "6.1.2",
            OrcaVersionFamily.V6_1,
            "synthetic validation",
        ),
    )


class FakeRemoteFilesystem:
    """One in-memory server that records every mutation for assertions."""

    def __init__(self) -> None:
        self.directories = {
            "/",
            "/home",
            "/home/scientist",
            "/home/scientist/legacy runs",
            SOURCE,
            "/srv",
            "/srv/moltage-test",
            SYNTHETIC_REMOTE_ROOT,
        }
        self.files: dict[str, bytes] = {}
        self.orca_2json_available = True
        self.orca_2mkl_available = True
        self.copy_corrupts: set[str] = set()
        self.mkdir_errors: dict[str, Exception] = {}
        self.write_errors: dict[str, Exception] = {}
        self.commands: list[str] = []
        self.closed = False

    # Convenience -----------------------------------------------------

    def add_result(self, stem: str, *, input_bytes=None, output_bytes=None, geometry_bytes=None, gbw=GBW):
        base = PurePosixPath(SOURCE)
        if input_bytes is not None:
            self.files[str(base / f"{stem}.inp")] = input_bytes
        if output_bytes is not None:
            self.files[str(base / f"{stem}.out")] = output_bytes
        if geometry_bytes is not None:
            self.files[str(base / f"{stem}.xyz")] = geometry_bytes
        if gbw is not None:
            self.files[str(base / f"{stem}.gbw")] = gbw

    def snapshot_source(self) -> dict[str, bytes]:
        prefix = SOURCE + "/"
        return {
            path: data
            for path, data in self.files.items()
            if path.startswith(prefix)
        }

    # RemoteExecutor protocol ----------------------------------------

    def connect(self, request) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def stat(self, path: str) -> RemotePathStat:
        if path in self.directories:
            return RemotePathStat(True)
        if path in self.files:
            return RemotePathStat(False, len(self.files[path]))
        raise RemotePathNotFoundError(path)

    def list_directory(self, path: str) -> tuple[RemoteDirectoryEntry, ...]:
        if path not in self.directories:
            raise RemotePathNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        entries: dict[str, bool] = {}
        for candidate in self.directories | set(self.files):
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix):]
                if remainder and "/" not in remainder:
                    entries[remainder] = candidate in self.directories
        return tuple(
            RemoteDirectoryEntry(name, is_directory)
            for name, is_directory in sorted(entries.items())
        )

    def mkdir(self, path: str) -> None:
        if path in self.mkdir_errors:
            raise self.mkdir_errors[path]
        if path in self.directories or path in self.files:
            raise RemotePathAlreadyExistsError(path)
        parent = str(PurePosixPath(path).parent)
        if parent not in self.directories:
            raise RemotePathNotFoundError(parent)
        self.directories.add(path)

    def read_bytes(self, path: str) -> bytes:
        if path not in self.files:
            raise RemotePathNotFoundError(path)
        return self.files[path]

    def write_bytes(self, path: str, data: bytes) -> None:
        if path in self.write_errors:
            raise self.write_errors[path]
        parent = str(PurePosixPath(path).parent)
        if parent not in self.directories:
            raise RemotePathNotFoundError(parent)
        self.files[path] = data

    def rename(self, source: str, destination: str) -> None:
        if source not in self.files:
            raise RemotePathNotFoundError(source)
        self.files[destination] = self.files.pop(source)

    def download_file(self, path, destination, progress=None) -> None:
        raise AssertionError("import must not download files")

    def read_file_head(self, path, max_bytes):
        return self.read_bytes(path)[:max_bytes]

    def read_file_tail(self, path, max_bytes):
        return self.read_bytes(path)[-max_bytes:]

    def execute(self, command: str) -> RemoteCommandResult:
        self.commands.append(command)
        if "__MOLTAGE_ORCA_CONFIGURED__=" in command:
            return RemoteCommandResult(
                0,
                (
                    f"__MOLTAGE_ORCA_CONFIGURED__={ORCA}\n"
                    f"__MOLTAGE_ORCA_PATH__={ORCA}\n"
                ).encode(),
                b"",
            )
        if ORCA in command and "--version" in command:
            return RemoteCommandResult(0, b"Program Version 6.1.2\n", b"")
        if "__MOLTAGE_ORCA_2MKL__" in command:
            if not self.orca_2json_available:
                return RemoteCommandResult(61, b"", b"")
            marker = (
                b"__MOLTAGE_ORCA_2MKL__=AVAILABLE\n"
                if self.orca_2mkl_available
                else b"__MOLTAGE_ORCA_2MKL__=UNAVAILABLE\n"
            )
            return RemoteCommandResult(0, marker, b"")
        if command.startswith("sha256sum -- "):
            path = shlex.split(command)[2]
            if path not in self.files:
                return RemoteCommandResult(1, b"", b"missing\n")
            digest = sha256(self.files[path]).hexdigest()
            return RemoteCommandResult(0, f"{digest}  {path}\n".encode(), b"")
        if command.startswith("cp -- "):
            _, _, source, destination = shlex.split(command)
            if source not in self.files:
                return RemoteCommandResult(1, b"", b"missing source\n")
            parent = str(PurePosixPath(destination).parent)
            if parent not in self.directories:
                return RemoteCommandResult(1, b"", b"missing destination\n")
            payload = self.files[source]
            if PurePosixPath(destination).name in self.copy_corrupts:
                payload = payload + b"corrupted"
            self.files[destination] = payload
            return RemoteCommandResult(0, b"", b"")
        if command.startswith("rm -f -- ") or "; rmdir -- " in command:
            return self._cleanup(command)
        raise AssertionError(f"unexpected remote command: {command}")

    def _cleanup(self, command: str) -> RemoteCommandResult:
        status = 0
        for part in command.split("; "):
            tokens = shlex.split(part)
            if tokens[0] == "rm":
                for target in tokens[2:]:
                    self.files.pop(target, None)
            elif tokens[0] == "rmdir":
                target = tokens[2]
                prefix = target.rstrip("/") + "/"
                occupied = any(
                    item.startswith(prefix)
                    for item in self.directories | set(self.files)
                    if item != target
                )
                if occupied or target not in self.directories:
                    status = 1
                else:
                    self.directories.discard(target)
        return RemoteCommandResult(status, b"", b"")


class OrcaImportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.index = LocalProjectIndexRepository(
            Path(self.temporary_directory.name) / "known_projects.json"
        )
        self.executor = FakeRemoteFilesystem()
        self.executor.add_result(
            "benzenedithiol",
            input_bytes=orca_input(),
            output_bytes=orca_output(),
            geometry_bytes=orca_geometry(),
        )
        self.profile = replace(
            profile(profile_id=PROFILE_ID),
            orca_runtime=runtime(),
        )
        self.service = OrcaOptimizationImportService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=lambda: NOW,
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=lambda: "synthetictempid",
        )

    def validate(self, *, stem=None, source=SOURCE):
        return self.service.validate(
            OrcaImportValidationRequest(self.profile, source, stem, "secret")
        )

    def import_result(self, validation, *, name="benzenedithiol"):
        return self.service.import_optimization(
            OrcaImportRequest(self.profile, validation, name, "legacy", "secret")
        )


class CandidateSurveyTests(unittest.TestCase):
    def test_complete_group_requires_all_four_artifacts(self) -> None:
        survey = survey_orca_import_candidates(
            (
                "run.inp",
                "run.out",
                "run.xyz",
                "run.gbw",
                "run_trj.xyz",
                "run.property.txt",
                "other.inp",
                "other.out",
            )
        )

        self.assertEqual([item.stem for item in survey.complete], ["run"])
        partial = {item.stem: item.missing_extensions for item in survey.partial}
        self.assertEqual(partial["other"], (".xyz", ".gbw"))
        self.assertEqual(partial["run_trj"], (".inp", ".out", ".gbw"))

    def test_molden_files_are_listed_but_never_a_wavefunction_source(self) -> None:
        survey = survey_orca_import_candidates(
            ("run.inp", "run.out", "run.xyz", "run.gbw", "run.molden.input")
        )

        self.assertEqual(survey.molden_filenames, ("run.molden.input",))
        self.assertEqual(
            survey.complete[0].wavefunction_filename,
            "run.gbw",
        )


class ImportEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = OrcaImportCandidate("run")

    def evidence(self, **overrides):
        payload = {
            "input_bytes": orca_input(),
            "output_bytes": orca_output(),
            "geometry_bytes": orca_geometry(),
        }
        payload.update(overrides)
        return validate_orca_import_optimization(self.candidate, **payload)

    def test_valid_result_reports_structure_charge_and_multiplicity(self) -> None:
        evidence = self.evidence(input_bytes=orca_input(charge=-1, multiplicity=2))

        self.assertEqual(evidence.elements, ("S", "C", "H"))
        self.assertEqual(len(evidence.optimized_structure), 3)
        self.assertEqual(evidence.identity.charge, -1)
        self.assertEqual(evidence.identity.multiplicity, 2)
        self.assertIs(evidence.identity.method, OrcaMethod.PBE0)
        self.assertIs(evidence.identity.basis, OrcaBasis.DEF2_TZVP)

    def test_missing_normal_termination_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "normal termination"):
            self.evidence(output_bytes=orca_output(terminated=False))

    def test_nonconvergence_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "did not converge"):
            self.evidence(output_bytes=orca_output(converged=False))

    def test_geometry_from_another_calculation_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "final geometry"):
            self.evidence(geometry_bytes=orca_geometry(("S", "C", "H", "H")))

    def test_input_without_any_coordinate_header_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "coordinate block"):
            self.evidence(input_bytes=b"! PBE0 DEF2-TZVP OPT\n%pal\n  nprocs 8\nend\n")

    def test_xyzfile_coordinates_are_written_inline_into_the_managed_input(
        self,
    ) -> None:
        evidence = self.evidence(
            input_bytes=orca_xyzfile_input("start.xyz"),
            coordinate_bytes=start_geometry(),
        )

        self.assertEqual(evidence.inlined_input, INLINED_INPUT)
        self.assertEqual(evidence.identity.charge, 0)
        self.assertEqual(evidence.identity.multiplicity, 2)
        self.assertIs(evidence.identity.method, OrcaMethod.PBE0)
        self.assertEqual(
            [(atom.element, atom.x) for atom in evidence.submitted_structure],
            [("S", 0.25), ("C", 1.25), ("H", 2.25)],
        )

    def test_inlining_keeps_the_input_line_endings(self) -> None:
        evidence = self.evidence(
            input_bytes=orca_xyzfile_input("start.xyz", newline="\r\n"),
            coordinate_bytes=start_geometry(),
        )

        self.assertEqual(evidence.inlined_input, INLINED_INPUT.replace(b"\n", b"\r\n"))

    def test_inline_input_is_copied_unchanged(self) -> None:
        self.assertIsNone(self.evidence().inlined_input)

    def test_xyzfile_input_requires_its_coordinate_file(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "that file is required"):
            self.evidence(input_bytes=orca_xyzfile_input("start.xyz"))

    def test_xyzfile_coordinates_must_match_the_final_geometry(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "final geometry"):
            self.evidence(
                input_bytes=orca_xyzfile_input("start.xyz"),
                coordinate_bytes=start_geometry(("S", "C", "C")),
            )

    def test_unreadable_xyzfile_coordinates_are_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "not a readable XYZ"):
            self.evidence(
                input_bytes=orca_xyzfile_input("start.xyz"),
                coordinate_bytes=b"3\ncomment\nS 0.0 0.0\n",
            )

    def test_several_xyzfile_headers_are_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "more than one"):
            self.evidence(
                input_bytes=b"*xyzfile 0 2 a.xyz\n*xyzfile 0 2 b.xyz\n",
                coordinate_bytes=start_geometry(),
            )

    def test_xyzfile_path_resolution(self) -> None:
        directory = "/data/runs/Cr3"
        cases = {
            "start.xyz": "/data/runs/Cr3/start.xyz",
            "/data/runs/Cr3/Cr3_start.xyz": "/data/runs/Cr3/Cr3_start.xyz",
            "/elsewhere/start.xyz": "/elsewhere/start.xyz",
        }
        for written, resolved in cases.items():
            with self.subTest(written=written):
                self.assertEqual(
                    resolve_orca_xyzfile_path(
                        self.candidate, orca_xyzfile_input(written), directory
                    ),
                    resolved,
                )
        self.assertIsNone(
            resolve_orca_xyzfile_path(self.candidate, orca_input(), directory)
        )

    def test_xyzfile_path_through_parent_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "normalized path"):
            resolve_orca_xyzfile_path(
                self.candidate, orca_xyzfile_input("../start.xyz"), "/data/runs/Cr3"
            )

    def test_xyzfile_reading_the_final_geometry_file_is_rejected(self) -> None:
        with self.assertRaisesRegex(OrcaImportEvidenceError, "cannot be recovered"):
            resolve_orca_xyzfile_path(
                self.candidate, orca_xyzfile_input("run.xyz"), "/data/runs/Cr3"
            )

    def test_ambiguous_keyword_line_leaves_method_and_basis_unset(self) -> None:
        evidence = self.evidence(
            input_bytes=orca_input(keywords="B3LYP PBE0 DEF2-SVP OPT")
        )

        self.assertIsNone(evidence.identity.method)
        self.assertIsNone(evidence.identity.basis)
        self.assertEqual(evidence.identity.charge, 0)


class ValidationServiceTests(OrcaImportTestCase):
    def test_single_candidate_is_selected_and_reports_ready(self) -> None:
        validation = self.validate()

        self.assertTrue(validation.importable)
        self.assertEqual(validation.candidate_stems, ("benzenedithiol",))
        self.assertEqual(validation.selected.stem, "benzenedithiol")
        self.assertIs(
            validation.wavefunction_readiness,
            OrcaImportWavefunctionReadiness.READY,
        )
        self.assertEqual(validation.orca_2json_path, ORCA_2JSON)
        self.assertEqual(
            dict(validation.source_digests)["orca_opt.gbw"],
            sha256(GBW).hexdigest(),
        )

    def test_validation_never_writes_to_the_source_directory(self) -> None:
        before = self.executor.snapshot_source()

        self.validate()

        self.assertEqual(self.executor.snapshot_source(), before)

    def test_several_candidates_require_explicit_selection(self) -> None:
        self.executor.add_result(
            "other",
            input_bytes=orca_input(),
            output_bytes=orca_output(),
            geometry_bytes=orca_geometry(),
        )

        validation = self.validate()

        self.assertFalse(validation.importable)
        self.assertTrue(validation.selection_required)
        self.assertEqual(
            sorted(validation.candidate_stems),
            ["benzenedithiol", "other"],
        )

        chosen = self.validate(stem="other")

        self.assertTrue(chosen.importable)
        self.assertEqual(chosen.selected.stem, "other")

    def test_missing_wavefunction_blocks_import(self) -> None:
        del self.executor.files[str(PurePosixPath(SOURCE) / "benzenedithiol.gbw")]

        validation = self.validate()

        self.assertFalse(validation.importable)
        self.assertIn(".gbw", validation.blocking_reason)

    def test_unverified_conversion_utility_still_allows_import(self) -> None:
        self.executor.orca_2json_available = False

        validation = self.validate()

        self.assertTrue(validation.importable)
        self.assertIs(
            validation.wavefunction_readiness,
            OrcaImportWavefunctionReadiness.CONFIGURATION_REQUIRED,
        )
        self.assertIn("ORCA settings", validation.readiness_diagnostic)

    def test_missing_directory_reports_an_explicit_reason(self) -> None:
        with self.assertRaisesRegex(OrcaImportError, "does not exist"):
            self.validate(source="/home/scientist/absent")

    def test_relative_path_is_rejected_before_connecting(self) -> None:
        with self.assertRaisesRegex(OrcaImportError, "absolute remote directory"):
            self.validate(source="relative/path")

    def test_default_managed_name_is_derived_from_the_source_basename(self) -> None:
        self.assertEqual(
            default_managed_base_name(SOURCE),
            "benzenedithiol_opt",
        )


class ImportServiceTests(OrcaImportTestCase):
    def test_import_creates_a_managed_project_without_touching_the_source(self) -> None:
        before = self.executor.snapshot_source()

        result = self.import_result(self.validate())

        self.assertEqual(self.executor.snapshot_source(), before)
        self.assertEqual(
            result.remote_project_path,
            f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504",
        )
        copied = sorted(
            PurePosixPath(path).name
            for path in self.executor.files
            if path.startswith(result.remote_project_path + "/")
            and "/.moltage/" not in path
        )
        self.assertEqual(copied, sorted(MANAGED_IMPORT_FILENAMES))

    def test_only_required_artifacts_are_copied(self) -> None:
        base = PurePosixPath(SOURCE)
        self.executor.files[str(base / "benzenedithiol_trj.xyz")] = b"trajectory\n"
        self.executor.files[str(base / "benzenedithiol.molden.input")] = b"molden\n"

        result = self.import_result(self.validate())

        managed = {
            PurePosixPath(path).name
            for path in self.executor.files
            if path.startswith(result.remote_project_path + "/")
        }
        self.assertNotIn("benzenedithiol_trj.xyz", managed)
        self.assertNotIn("benzenedithiol.molden.input", managed)

    def test_imported_step_is_succeeded_with_no_scheduler_identity(self) -> None:
        result = self.import_result(self.validate())
        step = result.project.steps[0]

        self.assertIs(result.project.workflow_kind, CalculationWorkflowKind.ORCA)
        self.assertIs(step.kind, ProjectStepKind.ORCA_OPTIMIZATION)
        self.assertIs(step.state, ProjectStepState.SUCCEEDED)
        self.assertIsNone(step.job_id)
        self.assertIsNone(step.scheduler_kind)
        self.assertIsNone(step.scheduler_state)
        self.assertIsNone(step.submit_script_filename)
        self.assertIsNone(step.slurm_output_filename)
        self.assertIs(
            step.orca_optimization_result.origin,
            OrcaOptimizationOrigin.IMPORTED_EXTERNAL,
        )
        self.assertFalse(step.orca_optimization_result.scheduler_succeeded)
        self.assertTrue(step.orca_optimization_result.succeeded)
        self.assertEqual(len(result.project.steps), 1)

    def test_import_records_source_and_managed_provenance_separately(self) -> None:
        result = self.import_result(self.validate())
        step = result.project.steps[0]
        provenance = step.orca_import_provenance

        self.assertEqual(provenance.source_directory, SOURCE)
        self.assertEqual(provenance.source_stem, "benzenedithiol")
        self.assertEqual(
            provenance.source_wavefunction_filename,
            "benzenedithiol.gbw",
        )
        self.assertEqual(provenance.imported_at, NOW)
        self.assertEqual(provenance.orca_2json_path, ORCA_2JSON)
        self.assertEqual(
            dict(step.input_hashes)["orca_opt.gbw"],
            sha256(GBW).hexdigest(),
        )
        self.assertEqual(
            sorted(dict(step.input_hashes)),
            sorted(MANAGED_IMPORT_FILENAMES),
        )

    def test_charge_multiplicity_and_basis_reach_the_wbl_settings(self) -> None:
        self.executor.add_result(
            "benzenedithiol",
            input_bytes=orca_input(charge=-2, multiplicity=1),
        )

        result = self.import_result(self.validate())
        settings = result.project.steps[0].orca_optimization_settings

        self.assertEqual(settings.charge, -2)
        self.assertEqual(settings.multiplicity, 1)
        self.assertIs(settings.basis, OrcaBasis.DEF2_TZVP)

    def test_destination_collision_claims_the_next_candidate(self) -> None:
        self.executor.directories.add(
            f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504"
        )
        existing = f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504/keep.txt"
        self.executor.files[existing] = b"existing project\n"

        result = self.import_result(self.validate())

        self.assertEqual(
            result.remote_project_path,
            f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504_02",
        )
        self.assertEqual(self.executor.files[existing], b"existing project\n")

    def test_changed_source_aborts_before_any_directory_is_created(self) -> None:
        validation = self.validate()
        self.executor.files[str(PurePosixPath(SOURCE) / "benzenedithiol.gbw")] = (
            GBW + b"changed"
        )

        with self.assertRaisesRegex(OrcaImportError, "changed since validation"):
            self.import_result(validation)

        self.assertFalse(
            [
                item
                for item in self.executor.directories
                if item.startswith(SYNTHETIC_REMOTE_ROOT + "/")
            ]
        )

    def test_corrupt_copy_fails_closed_and_removes_the_claimed_directory(self) -> None:
        self.executor.copy_corrupts.add("orca_opt.gbw")

        with self.assertRaisesRegex(OrcaImportError, "SHA256 verification failed"):
            self.import_result(self.validate())

        self.assertIn(
            "cp -- ",
            "\n".join(self.executor.commands),
        )
        self.assertNotIn(
            f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504",
            self.executor.directories,
        )
        self.assertFalse(
            [
                path
                for path in self.executor.files
                if path.startswith(SYNTHETIC_REMOTE_ROOT + "/")
            ]
        )

    def test_metadata_directory_is_removed_when_the_manifest_cannot_be_written(
        self,
    ) -> None:
        root = f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504"
        self.executor.write_errors[
            f"{root}/.moltage/project.json.tmp-synthetictempid"
        ] = RemotePathNotFoundError("synthetic manifest write failure")
        validation = self.validate()
        before = self.executor.snapshot_source()

        with self.assertRaises(OrcaImportError):
            self.import_result(validation)

        self.assertNotIn(f"{root}/.moltage", self.executor.directories)
        self.assertNotIn(root, self.executor.directories)
        self.assertEqual(self.executor.snapshot_source(), before)

    def test_manifest_write_failure_leaves_no_managed_project(self) -> None:
        root = f"{SYNTHETIC_REMOTE_ROOT}/benzenedithiol.20300504"
        self.executor.mkdir_errors[f"{root}/.moltage"] = RemotePathNotFoundError(root)
        validation = self.validate()
        before = self.executor.snapshot_source()

        with self.assertRaises(OrcaImportError):
            self.import_result(validation)

        self.assertNotIn(root, self.executor.directories)
        self.assertEqual(self.executor.snapshot_source(), before)
        self.assertFalse(
            [
                path
                for path in self.executor.files
                if path.startswith(SYNTHETIC_REMOTE_ROOT + "/")
            ]
        )

    def test_unvalidated_request_is_refused(self) -> None:
        self.executor.orca_2json_available = True
        del self.executor.files[str(PurePosixPath(SOURCE) / "benzenedithiol.gbw")]
        blocked = self.validate()

        with self.assertRaisesRegex(OrcaImportError, "not been validated"):
            self.import_result(blocked)

    def test_unsafe_managed_name_is_refused(self) -> None:
        with self.assertRaises(OrcaImportError):
            self.import_result(self.validate(), name="bad name/../escape")

    def test_import_creates_no_wbl_stage_and_starts_no_analysis(self) -> None:
        result = self.import_result(self.validate())

        self.assertTrue(
            all(
                step.kind is not ProjectStepKind.ORCA_WBL_TRANSMISSION
                for step in result.project.steps
            )
        )
        self.assertFalse(
            [command for command in self.executor.commands if "-json" in command]
        )


class ImportedPersistenceTests(OrcaImportTestCase):
    def test_manifest_round_trips_imported_origin_and_provenance(self) -> None:
        result = self.import_result(self.validate())

        encoded = serialize_project_manifest(result.project)
        decoded = parse_project_manifest(encoded)

        self.assertEqual(decoded, result.project)
        self.assertIs(
            decoded.steps[0].orca_optimization_result.origin,
            OrcaOptimizationOrigin.IMPORTED_EXTERNAL,
        )
        self.assertEqual(
            decoded.steps[0].orca_import_provenance.source_directory,
            SOURCE,
        )
        self.assertNotIn("secret", encoded)

    def test_manifest_written_to_the_managed_workspace_is_readable(self) -> None:
        result = self.import_result(self.validate())
        manifest = self.executor.files[
            f"{result.remote_project_path}/.moltage/project.json"
        ]

        reloaded = parse_project_manifest(manifest)

        self.assertEqual(reloaded.project_id, PROJECT_ID)
        self.assertIs(reloaded.steps[0].state, ProjectStepState.SUCCEEDED)

    def test_refresh_preserves_imported_success_and_adds_no_scheduler_state(self) -> None:
        self.import_result(self.validate())
        before = self.executor.snapshot_source()
        recovery = ProjectRecoveryService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=lambda: NOW,
        )

        discovered = recovery.discover_and_refresh(self.profile, "secret")

        snapshot = next(
            item
            for item in discovered.snapshots
            if item.project.project_id == PROJECT_ID
        )
        step = snapshot.project.steps[0]
        self.assertIs(step.state, ProjectStepState.SUCCEEDED)
        self.assertIs(
            step.orca_optimization_result.origin,
            OrcaOptimizationOrigin.IMPORTED_EXTERNAL,
        )
        self.assertFalse(step.orca_optimization_result.scheduler_succeeded)
        self.assertIsNone(step.scheduler_state)
        self.assertIsNone(step.job_id)
        self.assertIsNotNone(snapshot.optimized_structure)
        self.assertEqual(self.executor.snapshot_source(), before)

    def test_provenance_recorded_without_coordinate_keys_remains_readable(
        self,
    ) -> None:
        import json

        result = self.import_result(self.validate())
        raw = json.loads(serialize_project_manifest(result.project))
        for entry in raw["steps"]:
            entry["orca_import_provenance"].pop("source_coordinate_path")
            entry["orca_import_provenance"].pop("source_coordinate_sha256")

        self.assertEqual(parse_project_manifest(json.dumps(raw)), result.project)


class XyzfileImportTests(OrcaImportTestCase):
    START = "benzenedithiol_start.xyz"

    def setUp(self) -> None:
        super().setUp()
        self.coordinate_path = str(PurePosixPath(SOURCE) / self.START)
        self.executor.files[self.coordinate_path] = start_geometry()
        self.executor.add_result(
            "benzenedithiol", input_bytes=orca_xyzfile_input(self.START)
        )

    def test_validation_records_the_coordinate_file(self) -> None:
        validation = self.validate()

        self.assertTrue(validation.importable, validation.blocking_reason)
        self.assertEqual(validation.coordinate_path, self.coordinate_path)
        self.assertEqual(
            validation.coordinate_sha256,
            sha256(start_geometry()).hexdigest(),
        )

    def test_missing_coordinate_file_blocks_validation(self) -> None:
        del self.executor.files[self.coordinate_path]

        validation = self.validate()

        self.assertFalse(validation.importable)
        self.assertIn("does not exist", validation.blocking_reason)

    def test_import_writes_the_inlined_input_and_records_both_sources(self) -> None:
        before = self.executor.snapshot_source()

        result = self.import_result(self.validate())

        step = result.project.steps[0]
        provenance = step.orca_import_provenance
        self.assertEqual(self.executor.snapshot_source(), before)
        self.assertEqual(
            self.executor.files[f"{result.remote_project_path}/orca_opt.inp"],
            INLINED_INPUT,
        )
        self.assertEqual(
            dict(step.input_hashes)["orca_opt.inp"],
            sha256(INLINED_INPUT).hexdigest(),
        )
        self.assertEqual(
            sorted(dict(step.input_hashes)),
            sorted(MANAGED_IMPORT_FILENAMES),
        )
        self.assertEqual(
            provenance.source_input_sha256,
            sha256(orca_xyzfile_input(self.START)).hexdigest(),
        )
        self.assertEqual(provenance.source_coordinate_path, self.coordinate_path)
        self.assertEqual(
            provenance.source_coordinate_sha256,
            sha256(start_geometry()).hexdigest(),
        )
        self.assertEqual(
            parse_project_manifest(serialize_project_manifest(result.project)),
            result.project,
        )

    def test_changed_coordinate_file_aborts_before_any_directory_is_created(
        self,
    ) -> None:
        validation = self.validate()
        self.executor.files[self.coordinate_path] = start_geometry(("S", "C", "C"))

        with self.assertRaisesRegex(OrcaImportError, "changed since validation"):
            self.import_result(validation)

        self.assertFalse(
            [
                item
                for item in self.executor.directories
                if item.startswith(SYNTHETIC_REMOTE_ROOT + "/")
            ]
        )

    def test_refresh_reads_the_inlined_input_and_keeps_success(self) -> None:
        result = self.import_result(self.validate())
        recovery = ProjectRecoveryService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=lambda: NOW,
            covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75, "H": 0.32},
        )

        snapshot = recovery.refresh_project(
            self.profile,
            result.remote_project_path,
            supplied_password="secret",
        )

        self.assertIs(snapshot.project.steps[0].state, ProjectStepState.SUCCEEDED)
        self.assertIsNotNone(snapshot.optimized_structure)


if __name__ == "__main__":
    unittest.main()


class LegacySchemaTests(unittest.TestCase):
    def test_schema_ten_manifest_keeps_submitted_origin_without_import_metadata(
        self,
    ) -> None:
        import json

        from moltage.app.project_planning import create_initial_project
        from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
        from moltage.orca.settings import OrcaOptimizationSettings

        project = create_initial_project(
            base_name="LegacyOrca",
            remote_directory_name="LegacyOrca.20300102",
            source_molecule_name="legacy.xyz",
            server_profile_id=PROFILE_ID,
            remote_project_root=SYNTHETIC_REMOTE_ROOT,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=NOW,
            project_id=PROJECT_ID,
        )
        step = replace(
            project.steps[0],
            state=ProjectStepState.SUCCEEDED,
            job_id="90001",
            scheduler_kind="SLURM",
            orca_optimization_settings=OrcaOptimizationSettings(
                method=OrcaMethod.PBE0,
                basis=OrcaBasis.DEF2_TZVP,
            ),
            orca_optimization_result=OrcaOptimizationResultEvidence(
                True, True, True, True, True
            ),
            orca_submitted_elements=("S", "C", "H"),
        )
        project = replace(project, steps=(step,))
        raw = json.loads(serialize_project_manifest(project))
        raw["schema_version"] = 10
        for entry in raw["steps"]:
            entry.pop("orca_import_provenance", None)
            entry["orca_optimization_result"].pop("origin", None)

        migrated = parse_project_manifest(json.dumps(raw))

        self.assertEqual(migrated.schema_version, 12)
        self.assertIs(
            migrated.steps[0].orca_optimization_result.origin,
            OrcaOptimizationOrigin.MOLTAGE_SUBMITTED,
        )
        self.assertIsNone(migrated.steps[0].orca_import_provenance)
        self.assertTrue(migrated.steps[0].orca_optimization_result.succeeded)


class ResubmitGatingTests(OrcaImportTestCase):
    def test_imported_projects_do_not_offer_optimization_resubmission(self) -> None:
        from moltage.gui.projects_dialog import _orca_resubmit_eligible

        result = self.import_result(self.validate())
        recovery = ProjectRecoveryService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=lambda: NOW,
            covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75, "H": 0.32},
        )
        snapshot = recovery.refresh_project(
            self.profile,
            result.remote_project_path,
            supplied_password="secret",
        )

        self.assertIsNotNone(snapshot.optimized_structure)
        self.assertIsNotNone(snapshot.project.steps[0].orca_optimization_settings)
        self.assertFalse(_orca_resubmit_eligible(snapshot))


class CancellationTests(OrcaImportTestCase):
    def test_a_stopped_validation_reads_nothing_further(self) -> None:
        from moltage.remote.executor import (
            RemoteOperationStopToken,
            RemoteOperationStopped,
        )

        token = RemoteOperationStopToken()
        token.request_stop()

        with self.assertRaises(RemoteOperationStopped):
            self.service.validate(
                OrcaImportValidationRequest(self.profile, SOURCE, None, "secret", token)
            )

    def test_a_stopped_import_creates_no_managed_directory(self) -> None:
        from moltage.remote.executor import (
            RemoteOperationStopToken,
            RemoteOperationStopped,
        )

        validation = self.validate()
        token = RemoteOperationStopToken()
        token.request_stop()
        before = self.executor.snapshot_source()

        with self.assertRaises(RemoteOperationStopped):
            self.service.import_optimization(
                OrcaImportRequest(
                    self.profile, validation, "benzenedithiol", "legacy", "secret", token
                )
            )

        self.assertEqual(self.executor.snapshot_source(), before)
        self.assertFalse(
            [
                item
                for item in self.executor.directories
                if item.startswith(SYNTHETIC_REMOTE_ROOT + "/")
            ]
        )
