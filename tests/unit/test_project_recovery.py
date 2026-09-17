from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import hashlib
import tempfile
import unittest
from moltage.app.project_recovery import _progress_reporter
from moltage.remote.executor import RemoteOperationStopped, RemoteOperationStopToken
from uuid import UUID

from moltage.aims.input_bundle import build_aims_optimization_inputs
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AtomAimsSettings,
    SpeciesAccuracy,
)
from moltage.aims.recovery import (
    GEOMETRY_CONVERGENCE_MARKER,
    NORMAL_TERMINATION_MARKER,
)
from moltage.aitranss.output import AitranssFailureCode
from moltage.aitranss.tcontrol import TControlSettings, render_tcontrol
from moltage.aims.transport_evidence import TransportSpinMode
from species_test_support import synthetic_species_library
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.app.project_planning import create_initial_project
from moltage.app.project_recovery import (
    ProjectProfileRebindRequired,
    ProjectRecoveryService,
)
from moltage.domain.calculation_project import (
    ProjectStepAttempt,
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)
from electrode_test_support import synthetic_project_electrode_provenance
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_surface import propose_electrode_surfaces
from moltage.remote.executor import (
    RemoteCommandResult,
    RemoteConnectionError,
    RemoteDirectoryEntry,
    RemotePathNotFoundError,
    RemotePathStat,
)
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from moltage.remote.slurm import render_submit_script
from moltage.remote.slurm_discovery import (
    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
)
from moltage.remote.slurm_status import (
    SchedulerStatusKind,
    SlurmStatusQueryError,
)
from phase2b1_test_support import profile
from synthetic_structure_test_support import (
    synthetic_extended_electrode_placement,
    synthetic_junction_geometry_bytes,
    synthetic_junction_provenance,
    synthetic_junction_structure,
)


NOW = datetime(2030, 1, 2, 14, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("44444444-4444-4444-8444-444444444444")
SYNTHETIC_NEXT_STEP_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase2c"
    / "synthetic_dual_ncs.geometry.in.next_step"
)
SYNTHETIC_STEP4_FATAL_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase4a"
    / "aitranss_electrode_interface_overlap.out"
)
PHASE4B_FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "fixtures" / "phase4b"
)
SYNTHETIC_STEP4_SELF_ENERGY_FORMAT_FATAL_FIXTURE = (
    PHASE4B_FIXTURE_DIRECTORY
    / "aitranss_self_energy_file_format_error.out"
)
PHASE4C_FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "fixtures" / "phase4c"
)
SYNTHETIC_STEP4_SUCCESS_FIXTURE = (
    PHASE4C_FIXTURE_DIRECTORY / "aitranss-success.minimal.out"
)
SYNTHETIC_TE_DAT_FIXTURE = (
    PHASE4C_FIXTURE_DIRECTORY / "TE.synthetic-nonspin.dat"
)


class RecoveryRemoteExecutor:
    def __init__(self) -> None:
        self.directories = {"/", "/nfs", "/srv/moltage-test/projects"}
        self.files = {}
        self.commands = []
        self.operations = []
        self.squeue_stdout = b""
        self.sacct_stdout = b""
        self.squeue_status = 0
        self.sacct_status = 0
        self.tail_error = None
        self.closed = False

    def close(self):
        self.closed = True
        self.operations.append(("close",))

    def list_directory(self, path):
        self.operations.append(("list", path))
        if path not in self.directories:
            raise RemotePathNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        entries = {}
        for candidate in self.directories:
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix) :]
                if remainder and "/" not in remainder:
                    entries[remainder] = True
        for candidate in self.files:
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix) :]
                if remainder and "/" not in remainder:
                    entries[remainder] = False
        return tuple(
            RemoteDirectoryEntry(name, is_directory)
            for name, is_directory in sorted(entries.items())
        )

    def stat(self, path):
        self.operations.append(("stat", path))
        if path in self.directories:
            return RemotePathStat(True)
        if path in self.files:
            return RemotePathStat(False, len(self.files[path]))
        raise RemotePathNotFoundError(path)

    def read_bytes(self, path):
        self.operations.append(("read", path))
        try:
            return self.files[path]
        except KeyError:
            raise RemotePathNotFoundError(path) from None

    def read_file_tail(self, path, max_bytes):
        self.operations.append(("tail", path, max_bytes))
        if self.tail_error is not None:
            raise self.tail_error
        try:
            return self.files[path][-max_bytes:]
        except KeyError:
            raise RemotePathNotFoundError(path) from None

    def read_file_head(self, path, max_bytes):
        self.operations.append(("head", path, max_bytes))
        try:
            return self.files[path][:max_bytes]
        except KeyError:
            raise RemotePathNotFoundError(path) from None

    def write_bytes(self, path, data):
        self.operations.append(("write", path))
        self.files[path] = data

    def rename(self, source, destination):
        self.operations.append(("rename", source, destination))
        self.files[destination] = self.files.pop(source)

    def mkdir(self, path):
        self.operations.append(("mkdir", path))
        self.directories.add(path)

    def execute(self, command):
        self.commands.append(command)
        self.operations.append(("execute", command))
        if command == CURRENT_ENVIRONMENT_DISCOVERY_COMMAND:
            return RemoteCommandResult(0, b"/usr/bin/sbatch\n", b"")
        if command == "/usr/bin/sbatch --version":
            return RemoteCommandResult(0, b"slurm 24.11.3\n", b"")
        if command.startswith("/usr/bin/squeue "):
            return RemoteCommandResult(
                self.squeue_status,
                self.squeue_stdout,
                b"",
            )
        if command.startswith("/usr/bin/sacct "):
            return RemoteCommandResult(
                self.sacct_status,
                self.sacct_stdout,
                b"",
            )
        raise AssertionError(f"unexpected command: {command}")

    def add_project(self, project, bundle, *, include_output=True):
        root = project.remote_project_path
        metadata = str(PurePosixPath(root) / ".moltage")
        step = _active_step(project)
        step_directory = remote_step_directory(project, step.kind)
        self.directories.update((root, metadata))
        if step_directory != root:
            self.directories.add(step_directory)
        self.files[str(PurePosixPath(metadata) / "project.json")] = (
            serialize_project_manifest(project).encode("utf-8")
        )
        self.files[str(PurePosixPath(step_directory) / "geometry.in")] = (
            bundle.geometry_text.encode("utf-8")
        )
        self.files[str(PurePosixPath(step_directory) / "control.in")] = (
            bundle.control_text.encode("utf-8")
        )
        self.files[str(PurePosixPath(step_directory) / "submit.sh")] = (
            render_submit_script(
                TEST_PROFILE.execution_preset,
                project.project_id,
                step.kind,
            ).encode("utf-8")
        )
        self.files[
            str(PurePosixPath(step_directory) / "geometry.in.next_step")
        ] = _optimized_geometry(bundle.geometry_text).encode("utf-8")
        if include_output:
            self.files[str(PurePosixPath(step_directory) / "aims.dft.out")] = (
                f"{GEOMETRY_CONVERGENCE_MARKER}\n...\n"
                f"{NORMAL_TERMINATION_MARKER}\n"
            ).encode("utf-8")


class FixedConnectionService:
    def __init__(self, executor):
        self.executor = executor

    def connect_for_remote_operation(self, server_profile, supplied_password=None, *, stop_token=None):
        del server_profile, supplied_password
        if stop_token is not None:
            stop_token.bind_executor(self.executor)
        return self.executor


TEST_PROFILE = profile(
    profile_id=UUID("55555555-5555-4555-8555-555555555555")
)


def _bundle():
    structure = MolecularStructure(
        (
            Atom(0, "C", 0.0, 0.0, 0.0),
            Atom(1, "N", 1.2, 0.0, 0.0),
        )
    )
    settings = AimsOptimizationSettings(
        atom_settings=(
            AtomAimsSettings(0, species_accuracy=SpeciesAccuracy.LIGHT),
        )
    )
    return build_aims_optimization_inputs(
        structure,
        settings,
        synthetic_species_library(),
    )


def _project(
    state=ProjectStepState.QUEUED,
    *,
    step_kind=ProjectStepKind.MOLECULE_OPT,
    profile_id=None,
    revision=2,
    job_id="12345",
    last_error=None,
):
    project = create_initial_project(
        base_name="MoleculeA",
        remote_directory_name="MoleculeA.20300102",
        source_molecule_name="MoleculeA.xyz",
        server_profile_id=profile_id or TEST_PROFILE.profile_id,
        remote_project_root=TEST_PROFILE.remote_project_root,
        starting_step=step_kind,
        now=NOW,
        project_id=PROJECT_ID,
    )
    active = next(item for item in project.steps if item.kind is step_kind)
    changed = replace(
        active,
        state=state,
        job_id=(None if state is ProjectStepState.UNKNOWN else job_id),
        submitted_at=NOW,
        input_hashes=(("geometry.in", "a"), ("control.in", "b"), ("submit.sh", "c")),
        last_error=last_error,
    )
    return replace(
        project,
        revision=revision,
        steps=tuple(changed if item.kind is step_kind else item for item in project.steps),
    )


def _direct_step3_project(
    state=ProjectStepState.QUEUED,
    *,
    revision=2,
    job_id="12345",
    extended=False,
):
    electrodes = (
        provenance_from_applied_electrodes(
            synthetic_extended_electrode_placement()
        )
        if extended
        else synthetic_project_electrode_provenance(rolls=(12, 348))
    )
    project = create_initial_project(
        base_name="ImportedJunction",
        remote_directory_name="ImportedJunction.20300102",
        source_molecule_name="ImportedJunction.xyz",
        server_profile_id=TEST_PROFILE.profile_id,
        remote_project_root=TEST_PROFILE.remote_project_root,
        starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
        now=NOW,
        project_id=PROJECT_ID,
        electrode_provenance=electrodes,
    )
    if state is ProjectStepState.NOT_STARTED:
        return replace(project, revision=revision)
    step3 = replace(
        project.steps[2],
        state=state,
        job_id=job_id,
        submitted_at=NOW,
        input_hashes=(("geometry.in", "a"), ("control.in", "b"), ("submit.sh", "c")),
    )
    return replace(
        project,
        revision=revision,
        steps=(*project.steps[:2], step3, project.steps[3]),
    )


def _active_step(project):
    return next(
        (
            item
            for item in reversed(project.steps)
            if item.state
            not in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}
        ),
        next(item for item in project.steps if item.kind is project.starting_step),
    )


def _step4_project(
    state=ProjectStepState.QUEUED,
    *,
    output_filename="aitranss.out",
    revision=12,
):
    base = _project(
        ProjectStepState.SUCCEEDED,
        step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        revision=revision,
    )
    step3 = replace(
        base.steps[2],
        state=ProjectStepState.SUCCEEDED,
        job_id="41002",
        submitted_at=NOW,
        finished_at=NOW,
        scheduler_state="COMPLETED",
        submit_script_filename="submit.retry02.sh",
        slurm_output_filename="aims.dft.retry02.out",
    )
    step4 = replace(
        base.steps[3],
        state=state,
        job_id="42001",
        submitted_at=NOW,
        input_hashes=(("submit.aitranss.sh", "c"), ("tcontrol", "d")),
        submit_script_filename="submit.aitranss.sh",
        slurm_output_filename=output_filename,
    )
    return replace(
        base,
        steps=(base.steps[0], base.steps[1], step3, step4),
        electrode_provenance=synthetic_junction_provenance(),
    )


def _step4_synthetic_evidence_project():
    project = _step4_project()
    structure = synthetic_junction_structure()
    geometry = synthetic_junction_geometry_bytes()
    surface = propose_electrode_surfaces(
        structure,
        project.electrode_provenance,
    )
    tcontrol = render_tcontrol(
        TControlSettings(
            natoms=len(structure),
            nsaos=512,
            lsurc=surface.left_one_based[0],
            lsurx=surface.left_one_based[1],
            lsury=surface.left_one_based[2],
            rsurc=surface.right_one_based[0],
            rsurx=surface.right_one_based[1],
            rsury=surface.right_one_based[2],
        ),
        structure,
        TransportSpinMode.NONE,
    ).encode("utf-8")
    control = (
        b"spin none\n"
        b"species H\n  nucleus 1\n"
        b"species C\n  nucleus 6\n"
        b"species N\n  nucleus 7\n"
        b"species S\n  nucleus 16\n"
        b"species Au\n  nucleus 79\n"
    )
    attempt_script = b"frozen attempt-1 AITRANSS script\n"
    step3 = replace(
        project.steps[2],
        input_hashes=(
            ("geometry.in", hashlib.sha256(geometry).hexdigest()),
            ("control.in", hashlib.sha256(control).hexdigest()),
            ("submit.retry02.sh", "a" * 64),
        ),
    )
    step4 = replace(
        project.steps[3],
        input_hashes=(
            ("tcontrol", hashlib.sha256(tcontrol).hexdigest()),
            ("submit.aitranss.sh", hashlib.sha256(attempt_script).hexdigest()),
        ),
    )
    project = replace(
        project,
        steps=(*project.steps[:2], step3, step4),
    )
    files = {
        "geometry.in": geometry,
        "control.in": control,
        "basis-indices.out": b"basis\n",
        "omat.aims": b"omat\n",
        "mos.aims": b"$scfmo.aims\n nsaos=512\n",
        "aims.dft.retry02.out": (
            f"  | Number of atoms                   :      {len(structure)}\n".encode()
            + b"Have a nice day.\n"
        ),
        "tcontrol": tcontrol,
        "submit.aitranss.sh": attempt_script,
        "aitranss.out": SYNTHETIC_STEP4_FATAL_FIXTURE.read_bytes(),
    }
    return project, files


def _install_step4_success_files(
    remote,
    project,
    *,
    runtime_annotations=False,
    ecp_on=True,
    inclusive_endpoint=False,
):
    directory = project.remote_project_path + "/molecule_Au/transport"
    submitted_text = _phase4c_tcontrol()
    if inclusive_endpoint:
        submitted_text = submitted_text.replace(
            "$eend 0.1000\n",
            "$eend 0.0000\n",
        )
    if not ecp_on:
        submitted_text = submitted_text.replace("$ecp on\n", "$ecp off\n")
    submitted_tcontrol = submitted_text.encode("utf-8")
    tcontrol = submitted_tcontrol
    if runtime_annotations:
        tcontrol = tcontrol.replace(
            b"$end\n",
            b"$valence_electrons  256\n"
            b"$efermi   -0.200000000000\n"
            b"$end\n",
        )
    step4 = replace(
        project.steps[3],
        input_hashes=(
            ("submit.aitranss.sh", "c"),
            ("tcontrol", hashlib.sha256(submitted_tcontrol).hexdigest()),
        ),
    )
    project = replace(project, steps=(*project.steps[:3], step4))
    remote.add_project(project, _bundle(), include_output=False)
    remote.files[directory + "/tcontrol"] = tcontrol
    remote.files[directory + "/aitranss.out"] = SYNTHETIC_STEP4_SUCCESS_FIXTURE.read_bytes()
    remote.files[directory + "/TE.dat"] = SYNTHETIC_TE_DAT_FIXTURE.read_bytes()
    return project


def _phase4c_tcontrol():
    return (
        '#input data for the "aitranss" module\n'
        "$aims_input on\n"
        "$landauer on\n"
        "$coord file=geometry.in\n"
        "$natoms 1\n"
        "$basis file=basis-indices.out\n"
        "$read_omat file=omat.aims\n"
        "$scfmo file=mos.aims\n"
        "$nsaos 1\n"
        "$lsurc 1\n"
        "$lsurx 2\n"
        "$lsury 3\n"
        "$rsurc 4\n"
        "$rsurx 5\n"
        "$rsury 6\n"
        "$nlayers 4\n"
        "$s1i 0.1d0\n"
        "$s2i 0.05d0\n"
        "$s3i 0.025d0\n"
        "$ener -0.3000\n"
        "$estep 0.1000\n"
        "$eend 0.1000\n"
        "$output file=TE.dat\n"
        "$testing off\n"
        "$ecp on\n"
        "$end\n"
    )


def _optimized_geometry(original):
    lines = []
    for line in original.splitlines():
        if line.startswith("atom "):
            fields = line.split()
            fields[1] = repr(float(fields[1]) + 0.25)
            line = " ".join(fields)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _install_real_format_geometry(remote, project):
    next_step = SYNTHETIC_NEXT_STEP_FIXTURE.read_bytes()
    original = b"\n".join(
        line
        for line in next_step.splitlines()
        if line.lstrip().startswith(b"atom ")
    ) + b"\n"
    control = (
        b"species C\n nucleus 6\n"
        b"species N\n nucleus 7\n"
        b"species S\n nucleus 16\n"
        b"species H\n nucleus 1\n"
    )
    root = project.remote_project_path
    remote.files[root + "/control.in"] = control
    remote.files[root + "/geometry.in"] = original
    remote.files[root + "/geometry.in.next_step"] = next_step


class ProjectRecoveryTests(unittest.TestCase):
    def test_refresh_progress_does_not_swallow_a_stop_checkpoint(self):
        token = RemoteOperationStopToken()

        def progress(_message):
            token.request_stop()
            token.checkpoint()

        with self.assertRaises(RemoteOperationStopped):
            _progress_reporter(progress, token)("synthetic refresh stage")

    def test_non_cancellation_progress_failure_remains_non_authoritative(self):
        def progress(_message):
            raise ValueError("synthetic display failure")

        _progress_reporter(progress, RemoteOperationStopToken())("synthetic stage")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.index = LocalProjectIndexRepository(
            Path(self.temporary.name) / "known_projects.json"
        )
        self.remote = RecoveryRemoteExecutor()
        self.service = ProjectRecoveryService(
            FixedConnectionService(self.remote),
            self.index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "refresh",
        )

    def test_running_project_is_recovered_without_scientific_reads_or_step2_creation(self):
        project = _project()
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertEqual(snapshot.project.revision, project.revision + 1)
        self.assertFalse(any(item[0] == "tail" for item in self.remote.operations))
        self.assertFalse(
            any("geometry.in.next_step" in item[1] for item in self.remote.operations if item[0] == "read")
        )
        self.assertFalse(any(item[0] == "mkdir" for item in self.remote.operations))

    def test_running_to_running_refresh_does_not_bump_revision(self):
        project = _project(ProjectStepState.RUNNING, revision=3)
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"
        manifest_path = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest_path]

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertEqual(snapshot.project.revision, 3)
        self.assertEqual(self.remote.files[manifest_path], before)

    def test_scheduler_completed_never_regresses_to_running(self):
        project = _project(ProjectStepState.SCHEDULER_COMPLETED, revision=4)
        self.remote.add_project(project, _bundle(), include_output=False)
        self.remote.squeue_stdout = b"12345|RUNNING\n"
        manifest_path = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest_path]

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(
            snapshot.active_step.state,
            ProjectStepState.SCHEDULER_COMPLETED,
        )
        self.assertEqual(snapshot.project.revision, project.revision)
        self.assertEqual(self.remote.files[manifest_path], before)
        self.assertFalse(any("squeue" in command for command in self.remote.commands))
        self.assertFalse(any("sacct" in command for command in self.remote.commands))
        self.assertFalse(any(item[0] == "rename" for item in self.remote.operations))

    def test_completed_scheduler_and_all_scientific_evidence_succeed_once(self):
        project = _project(ProjectStepState.RUNNING, revision=3)
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"11111|RUNNING\n22222|PENDING\n"
        self.remote.sacct_stdout = (
            b"12345|COMPLETED|0:0\n"
            b"12345.batch|COMPLETED|0:0\n"
            b"12345.extern|COMPLETED|0:0\n"
        )

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(snapshot.project.revision, 4)
        self.assertEqual(tuple(atom.element for atom in snapshot.optimized_structure), ("C", "N"))
        self.assertEqual(snapshot.optimized_structure[0].x, 0.25)
        self.assertIsNotNone(snapshot.connectivity)
        self.assertNotIn(
            project.remote_project_path + "/molecule_Au",
            self.remote.directories,
        )
        self.assertEqual(
            len([item for item in self.remote.operations if item[0] == "rename"]),
            1,
        )
        self.assertIn(
            "/usr/bin/squeue --noheader --user=scientist "
            "--format='%i|%T'",
            self.remote.commands,
        )
        self.assertIn(
            "/usr/bin/sacct --noheader --parsable2 --jobs=12345 "
            "--format=JobIDRaw,State,ExitCode",
            self.remote.commands,
        )

    def test_progress_reports_existing_coarse_recovery_boundaries(self):
        project = _project(ProjectStepState.RUNNING, revision=3)
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b""
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        progress = []

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
            progress=progress.append,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(
            progress,
            [
                f"Connecting to {TEST_PROFILE.name}...",
                "Opening remote project workspace...",
                "Reading managed projects...",
                "Checking Slurm status...",
                "Reading completed calculation results...",
                "Updating project list...",
            ],
        )

    def test_progress_presentation_failure_cannot_change_recovery(self):
        project = _project(ProjectStepState.RUNNING, revision=3)
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"

        def broken_progress(_message):
            raise RuntimeError("synthetic presentation failure")

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
            progress=broken_progress,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)

    def test_historical_false_failed_project_reconciles_to_succeeded_once(self):
        project = _project(
            ProjectStepState.FAILED,
            revision=3,
            job_id="43001",
            last_error="historical molecular recovery failure",
        )
        original_step = _active_step(project)
        self.remote.add_project(project, _bundle())
        _install_real_format_geometry(self.remote, project)
        self.remote.files[project.remote_project_path + "/aims.dft.out"] = (
            f"{NORMAL_TERMINATION_MARKER}\n".encode()
        )
        self.remote.sacct_stdout = b"43001|COMPLETED|0:0\n"
        manifest_path = project.remote_project_path + "/.moltage/project.json"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(snapshot.project.revision, 4)
        self.assertIsNone(snapshot.active_step.last_error)
        self.assertEqual(snapshot.active_step.job_id, original_step.job_id)
        self.assertEqual(
            snapshot.active_step.submitted_at,
            original_step.submitted_at,
        )
        self.assertEqual(
            snapshot.active_step.input_hashes,
            original_step.input_hashes,
        )
        self.assertEqual(len(snapshot.optimized_structure), 16)
        self.assertEqual(
            tuple(atom.element for atom in snapshot.optimized_structure),
            (
                "C",
                "C",
                "C",
                "C",
                "C",
                "C",
                "N",
                "C",
                "S",
                "N",
                "C",
                "S",
                "H",
                "H",
                "H",
                "H",
            ),
        )
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        self.assertEqual(persisted.revision, 4)
        self.assertIs(
            _active_step(persisted).state,
            ProjectStepState.SUCCEEDED,
        )
        self.assertFalse(
            any("--parsable submit.sh" in command for command in self.remote.commands)
        )
        self.assertFalse(any(item[0] == "mkdir" for item in self.remote.operations))
        self.assertEqual(
            [item[1] for item in self.remote.operations if item[0] == "write"],
            [manifest_path + ".tmp-refresh"],
        )
        self.assertNotIn(
            project.remote_project_path + "/molecule_Au",
            self.remote.directories,
        )

    def test_stored_failed_scheduler_failure_remains_failed_without_science_reads(self):
        project = _project(
            ProjectStepState.FAILED,
            revision=3,
            last_error="historical molecular recovery failure",
        )
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|TIMEOUT|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            "Slurm job ended with TIMEOUT (exit code 0:0)",
        )
        self.assertFalse(any(item[0] == "tail" for item in self.remote.operations))
        self.assertFalse(
            any(
                item[0] == "read" and "geometry.in.next_step" in item[1]
                for item in self.remote.operations
            )
        )

    def test_squeue_infrastructure_failure_preserves_manifest(self):
        project = _project(ProjectStepState.RUNNING, revision=3)
        self.remote.add_project(project, _bundle())
        self.remote.squeue_status = 1
        manifest_path = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest_path]

        with self.assertRaises(SlurmStatusQueryError):
            self.service.refresh_project(
                TEST_PROFILE,
                project.remote_project_path,
            )

        self.assertEqual(self.remote.files[manifest_path], before)
        self.assertFalse(any("sacct" in command for command in self.remote.commands))
        self.assertFalse(any(item[0] == "tail" for item in self.remote.operations))
        self.assertFalse(any(item[0] == "rename" for item in self.remote.operations))

    def test_normal_termination_and_valid_structure_succeed_without_convergence_marker(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        output_path = project.remote_project_path + "/aims.dft.out"
        self.remote.files[output_path] = f"{NORMAL_TERMINATION_MARKER}\n".encode()

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(snapshot.active_step.job_id, "12345")
        self.assertIsNone(snapshot.active_step.last_error)
        self.assertIsNotNone(snapshot.optimized_structure)
        self.assertIn("terminated normally", snapshot.status_message)
        self.assertNotIn("marker was detected", snapshot.status_message)

    def test_missing_normal_termination_marker_remains_scientific_failure(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        output_path = project.remote_project_path + "/aims.dft.out"
        self.remote.files[output_path] = (
            f"{GEOMETRY_CONVERGENCE_MARKER}\n".encode()
        )

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            "FHI-aims normal termination was not confirmed.",
        )
        self.assertIsNone(snapshot.optimized_structure)
        self.assertFalse(
            any(
                item[0] == "read" and "geometry.in.next_step" in item[1]
                for item in self.remote.operations
            )
        )

    def test_missing_next_step_remains_scientific_failure(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        del self.remote.files[
            project.remote_project_path + "/geometry.in.next_step"
        ]

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertIn("Required optimization recovery file is missing", snapshot.status_message)

    def test_malformed_next_step_remains_scientific_failure(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        self.remote.files[
            project.remote_project_path + "/geometry.in.next_step"
        ] = b"atom 0 0 C_light\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertIn("atom requires exactly", snapshot.status_message)

    def test_inconsistent_next_step_chemistry_remains_scientific_failure(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        self.remote.files[
            project.remote_project_path + "/geometry.in.next_step"
        ] = b"atom 0.25 0 0 C_light\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertIn("atom count changed", snapshot.status_message)

    def test_missing_output_persists_scheduler_completed_not_failed(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle(), include_output=False)
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(
            snapshot.active_step.state,
            ProjectStepState.SCHEDULER_COMPLETED,
        )
        self.assertIn("not yet available", snapshot.status_message)

    def test_transport_error_reading_output_propagates_without_manifest_mutation(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"
        transport = RemoteConnectionError("SSH transport lost during output read")
        self.remote.tail_error = transport
        manifest_path = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest_path]

        with self.assertRaises(RemoteConnectionError) as caught:
            self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(caught.exception, transport)
        self.assertEqual(self.remote.files[manifest_path], before)
        self.assertFalse(any(item[0] == "rename" for item in self.remote.operations))

    def test_accounting_lag_keeps_previous_persistent_state(self):
        project = _project(ProjectStepState.RUNNING, revision=4)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345.batch|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertEqual(snapshot.project.revision, 4)
        self.assertIs(
            snapshot.scheduler_status_kind,
            SchedulerStatusKind.ACCOUNTING_PENDING,
        )
        self.assertIn("accounting", snapshot.status_message)
        self.assertFalse(any(item[0] == "rename" for item in self.remote.operations))

    def test_unrecognized_scheduler_state_is_typed_without_manifest_mutation(self):
        project = _project(ProjectStepState.RUNNING, revision=4)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|FUTURE_STATE|0:0\n"

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertEqual(snapshot.project.revision, 4)
        self.assertIs(
            snapshot.scheduler_status_kind,
            SchedulerStatusKind.UNRESOLVED,
        )
        self.assertIn("FUTURE_STATE", snapshot.status_message)
        self.assertFalse(any(item[0] == "rename" for item in self.remote.operations))

    def test_scheduler_terminal_failure_skips_scientific_assessment(self):
        project = _project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle())
        self.remote.sacct_stdout = b"12345|TIMEOUT|0:0\n"

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            "Slurm job ended with TIMEOUT (exit code 0:0)",
        )
        self.assertFalse(any(item[0] == "tail" for item in self.remote.operations))

    def test_step4_completed_zero_with_exact_fatal_becomes_typed_failed(self):
        project = _step4_project()
        self.remote.add_project(project, _bundle(), include_output=False)
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/aitranss.out"] = (
            SYNTHETIC_STEP4_FATAL_FIXTURE.read_bytes()
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step_kind, ProjectStepKind.TRANSMISSION)
        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value,
        )
        self.assertEqual(snapshot.active_step.scheduler_state, "COMPLETED")
        self.assertIn("电极界面区域识别重叠", snapshot.status_message)
        self.assertIn("42001", snapshot.status_message)
        self.assertIn("aitranss.out", snapshot.status_message)

    def test_step4_retry02_completed_zero_with_format_stop_becomes_typed_failed(self):
        project = _step4_project(output_filename="aitranss.retry02.out")
        step4 = replace(
            project.steps[3],
            job_id="42002",
            submit_script_filename="submit.aitranss.retry02.sh",
            input_hashes=(
                ("self.energy.retry02.in", "a" * 64),
                ("submit.aitranss.retry02.sh", "b" * 64),
                ("tcontrol", "c" * 64),
            ),
            attempts=(
                ProjectStepAttempt(
                    job_id="42001",
                    submitted_at=NOW,
                    finished_at=NOW,
                    terminal_scheduler_state="COMPLETED",
                    failure_reason=(
                        AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value
                    ),
                    submit_script_filename="submit.aitranss.sh",
                    slurm_output_filename="aitranss.out",
                    input_hashes=(("submit.aitranss.sh", "d" * 64),),
                ),
            ),
        )
        project = replace(project, steps=(*project.steps[:3], step4))
        self.remote.add_project(project, _bundle(), include_output=False)
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/aitranss.retry02.out"] = (
            SYNTHETIC_STEP4_SELF_ENERGY_FORMAT_FATAL_FIXTURE.read_bytes()
        )
        self.remote.sacct_stdout = b"42002|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step_kind, ProjectStepKind.TRANSMISSION)
        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR.value,
        )
        self.assertEqual(snapshot.active_step.scheduler_state, "COMPLETED")
        self.assertIn("AITRANSS 无法读取显式 self-energy 文件", snapshot.status_message)
        self.assertIn("42002", snapshot.status_message)
        self.assertIn("aitranss.retry02.out", snapshot.status_message)
        self.assertFalse(snapshot.can_retry_step4_explicit)
        persisted = parse_project_manifest(
            self.remote.files[
                project.remote_project_path + "/.moltage/project.json"
            ]
        )
        self.assertIs(persisted.steps[3].state, ProjectStepState.FAILED)
        self.assertEqual(
            persisted.steps[3].last_error,
            AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR.value,
        )
        self.assertEqual(persisted.steps[3].attempts, step4.attempts)
        self.assertFalse(
            any(
                operation[0] == "read" and operation[1].endswith("/tcontrol")
                for operation in self.remote.operations
            )
        )

    def test_step4_typed_overlap_prepares_partitioned_retry_without_submitting(self):
        project, files = _step4_synthetic_evidence_project()
        root = project.remote_project_path
        directory = root + "/molecule_Au/transport"
        metadata = root + "/.moltage"
        self.remote.directories.update((root, root + "/molecule_Au", directory, metadata))
        self.remote.files[metadata + "/project.json"] = (
            serialize_project_manifest(project).encode()
        )
        for name, data in files.items():
            self.remote.files[directory + "/" + name] = data
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value,
        )
        self.assertTrue(snapshot.can_retry_step4_explicit)
        self.assertEqual(snapshot.step4_self_energy_plan.left.selected_count, 52)
        self.assertEqual(snapshot.step4_self_energy_plan.right.selected_count, 52)
        self.assertEqual(snapshot.step4_tcontrol_settings.nlayers, 4)
        self.assertEqual(
            snapshot.step4_attempt01_tcontrol,
            files["tcontrol"],
        )
        self.assertFalse(any("&& /usr/bin/sbatch" in command for command in self.remote.commands))

    def test_step4_active_states_do_not_read_partial_output(self):
        for scheduler_line, expected in (
            (b"42001|PENDING\n", ProjectStepState.QUEUED),
            (b"42001|RUNNING\n", ProjectStepState.RUNNING),
        ):
            with self.subTest(scheduler_line=scheduler_line):
                self.remote = RecoveryRemoteExecutor()
                self.service = ProjectRecoveryService(
                    FixedConnectionService(self.remote),
                    self.index,
                    now_factory=lambda: NOW,
                    temporary_id_factory=lambda: "refresh",
                )
                project = _step4_project()
                self.remote.add_project(project, _bundle(), include_output=False)
                self.remote.squeue_stdout = scheduler_line
                directory = project.remote_project_path + "/molecule_Au/transport"
                self.remote.files[directory + "/aitranss.out"] = (
                    SYNTHETIC_STEP4_FATAL_FIXTURE.read_bytes()
                )

                snapshot = self.service.refresh_project(
                    TEST_PROFILE,
                    project.remote_project_path,
                )

                self.assertIs(snapshot.active_step.state, expected)
                self.assertFalse(
                    any(item[0] == "tail" for item in self.remote.operations)
                )

    def test_step4_completed_without_known_success_or_fatal_stays_unassessed(self):
        project = _step4_project()
        self.remote.add_project(project, _bundle(), include_output=False)
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/aitranss.out"] = (
            b"AITRANSS exited without a reviewed terminal success marker\n"
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(
            snapshot.active_step.state,
            ProjectStepState.SCHEDULER_COMPLETED,
        )
        self.assertIsNone(snapshot.active_step.last_error)
        self.assertIn("success remains unassessed", snapshot.status_message)

    def test_step4_completed_zero_positive_markers_and_valid_te_succeeds(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.RUNNING),
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(snapshot.project.revision, project.revision + 1)
        self.assertTrue(snapshot.can_view_transmission)
        self.assertEqual(snapshot.transmission_result_filename, "TE.dat")
        self.assertEqual(len(snapshot.transmission_result.points), 4)
        self.assertIn("4 points", snapshot.status_message)
        persisted = parse_project_manifest(
            self.remote.files[
                project.remote_project_path + "/.moltage/project.json"
            ]
        )
        self.assertIs(persisted.steps[3].state, ProjectStepState.SUCCEEDED)

    def test_step4_accepts_supported_endpoint_inclusive_grid(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.SCHEDULER_COMPLETED),
            inclusive_endpoint=True,
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertTrue(snapshot.can_view_transmission)
        self.assertEqual(snapshot.transmission_result_filename, "TE.dat")
        self.assertEqual(
            snapshot.transmission_result.points[-1].energy_hartree,
            0.0,
        )
        self.assertIsNone(snapshot.transmission_result_error)

    def test_step4_accepts_supported_runtime_tcontrol_annotations(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.SCHEDULER_COMPLETED),
            runtime_annotations=True,
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(
            snapshot.transmission_result.fermi_energy_hartree,
            -0.2,
        )
        self.assertNotIn("SHA256", snapshot.status_message)

    def test_step4_ecp_off_accepts_efermi_only_runtime_annotation(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.SCHEDULER_COMPLETED),
            runtime_annotations=True,
            ecp_on=False,
        )
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/tcontrol"] = self.remote.files[
            directory + "/tcontrol"
        ].replace(b"$valence_electrons  256\n", b"")
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(
            snapshot.transmission_result.fermi_energy_hartree,
            -0.2,
        )
        self.assertNotIn("SHA256", snapshot.status_message)
        self.assertIsNone(snapshot.transmission_result_error)

    def test_step4_tcontrol_hash_mismatch_does_not_block_semantic_success(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.SCHEDULER_COMPLETED),
            runtime_annotations=True,
        )
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/tcontrol"] = self.remote.files[
            directory + "/tcontrol"
        ].replace(
            b"$coord file=geometry.in\n",
            b"$coord    file=geometry.in\n",
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(snapshot.transmission_result_filename, "TE.dat")
        self.assertEqual(len(snapshot.transmission_result.points), 4)
        self.assertIn(
            "tcontrol differs byte-for-byte",
            snapshot.status_message,
        )
        self.assertIsNone(snapshot.transmission_result_error)

    def test_step4_success_marker_without_valid_te_remains_scheduler_completed(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.RUNNING),
        )
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/TE.dat"] = b"malformed\n"
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(
            snapshot.active_step.state,
            ProjectStepState.SCHEDULER_COMPLETED,
        )
        self.assertIsNone(snapshot.transmission_result)
        self.assertIsNotNone(snapshot.transmission_result_error)
        self.assertIn("success remains unassessed", snapshot.status_message)

    def test_step4_persisted_scheduler_completed_rechecks_accounting_before_success(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.SCHEDULER_COMPLETED),
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertTrue(any("sacct" in command for command in self.remote.commands))

    def test_recorded_step4_success_reloads_result_without_manifest_mutation(self):
        project = _install_step4_success_files(
            self.remote,
            _step4_project(ProjectStepState.SUCCEEDED),
        )
        manifest_path = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest_path]

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertTrue(snapshot.can_view_transmission)
        self.assertEqual(self.remote.files[manifest_path], before)
        self.assertEqual(self.remote.commands, [])
        self.assertFalse(any(item[0] == "rename" for item in self.remote.operations))

    def test_step4_reads_only_current_attempt_output_filename(self):
        project = _step4_project(output_filename="aitranss.retry02.out")
        self.remote.add_project(project, _bundle(), include_output=False)
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/aitranss.out"] = (
            SYNTHETIC_STEP4_FATAL_FIXTURE.read_bytes()
        )
        self.remote.files[directory + "/aitranss.retry02.out"] = (
            b"No reviewed fatal and no reviewed success evidence\n"
        )
        self.remote.sacct_stdout = b"42001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(
            snapshot.active_step.state,
            ProjectStepState.SCHEDULER_COMPLETED,
        )
        tails = [item[1] for item in self.remote.operations if item[0] == "tail"]
        self.assertEqual(tails, [directory + "/aitranss.retry02.out"])

    def test_step4_scheduler_failure_prefers_known_aitranss_fatal(self):
        project = _step4_project(ProjectStepState.RUNNING)
        self.remote.add_project(project, _bundle(), include_output=False)
        directory = project.remote_project_path + "/molecule_Au/transport"
        self.remote.files[directory + "/aitranss.out"] = (
            SYNTHETIC_STEP4_FATAL_FIXTURE.read_bytes()
        )
        self.remote.sacct_stdout = b"42001|FAILED|1:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value,
        )
        self.assertEqual(snapshot.active_step.scheduler_state, "FAILED")

    def test_unknown_submission_without_job_id_never_searches_by_project_name(self):
        project = _project(ProjectStepState.UNKNOWN)
        self.remote.add_project(project, _bundle())

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.UNKNOWN)
        self.assertIn("Manual scheduler inspection", snapshot.status_message)
        self.assertEqual(self.remote.commands, [])

    def test_profile_rebind_is_explicit_local_only_and_preserves_manifest_identity(self):
        historical_id = UUID("66666666-6666-4666-8666-666666666666")
        project = _project(
            ProjectStepState.FAILED,
            profile_id=historical_id,
            revision=4,
        )
        self.remote.add_project(project, _bundle())
        manifest_path = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest_path]

        with self.assertRaises(ProjectProfileRebindRequired):
            self.service.refresh_project(TEST_PROFILE, project.remote_project_path)
        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
            profile_rebind_confirmed=True,
        )

        refreshed_without_another_confirmation = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )
        discovered = self.service.discover_and_refresh(TEST_PROFILE)

        reference = self.index.load()[0]
        self.assertEqual(reference.server_profile_id, TEST_PROFILE.profile_id)
        self.assertTrue(snapshot.profile_rebind_confirmed)
        self.assertTrue(
            refreshed_without_another_confirmation.profile_rebind_confirmed
        )
        self.assertEqual(len(discovered.snapshots), 1)
        self.assertFalse(discovered.snapshots[0].requires_profile_rebind)
        self.assertTrue(discovered.snapshots[0].profile_rebind_confirmed)
        self.assertEqual(snapshot.project.server_profile_id, historical_id)
        self.assertEqual(self.remote.files[manifest_path], before)

    def test_direct_step2_running_recovery_uses_molecule_au(self):
        project = _project(
            ProjectStepState.QUEUED,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"

        snapshot = self.service.refresh_project(TEST_PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step_kind, ProjectStepKind.MOLECULE_AU_OPT)
        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertIn(project.remote_project_path + "/molecule_Au", self.remote.directories)

    def test_direct_step2_scientific_success_recovers_from_molecule_au(self):
        project = _project(
            ProjectStepState.RUNNING,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b""
        self.remote.sacct_stdout = b"12345|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step_kind, ProjectStepKind.MOLECULE_AU_OPT)
        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertIsNotNone(snapshot.optimized_structure)
        self.assertIn("Step 2", snapshot.status_message)
        self.assertFalse(
            any(
                item[0] == "mkdir" and item[1].endswith("/transport")
                for item in self.remote.operations
            )
        )

    def test_direct_step3_running_recovery_preserves_skips_and_electrodes(self):
        project = _direct_step3_project()
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertIs(
            snapshot.active_step_kind,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertEqual(
            tuple(item.state for item in snapshot.project.steps[:2]),
            (ProjectStepState.SKIPPED, ProjectStepState.SKIPPED),
        )
        self.assertEqual(
            snapshot.project.electrode_provenance,
            project.electrode_provenance,
        )

    def test_direct_step3_recovery_preserves_lattice_extensions(self):
        project = _direct_step3_project(extended=True)
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"

        snapshot = self.service.refresh_project(
            TEST_PROFILE,
            project.remote_project_path,
        )

        self.assertEqual(
            snapshot.project.electrode_provenance,
            project.electrode_provenance,
        )
        self.assertEqual(
            tuple(
                len(record.lattice_extensions)
                for record in snapshot.project.electrode_provenance
            ),
            (2, 1),
        )

    def test_pristine_direct_step3_restart_uses_recorded_starting_step(self):
        project = _direct_step3_project(
            ProjectStepState.NOT_STARTED,
            revision=1,
            job_id=None,
        )
        root = project.remote_project_path
        metadata = root + "/.moltage"
        self.remote.directories.update(
            (root, metadata, root + "/molecule_Au", root + "/molecule_Au/transport")
        )
        self.remote.files[metadata + "/project.json"] = (
            serialize_project_manifest(project).encode("utf-8")
        )

        snapshot = self.service.refresh_project(TEST_PROFILE, root)

        self.assertIs(
            snapshot.active_step_kind,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        self.assertIs(snapshot.active_step.state, ProjectStepState.NOT_STARTED)
        self.assertEqual(
            snapshot.project.electrode_provenance,
            project.electrode_provenance,
        )
        self.assertFalse(any(item[0] == "execute" for item in self.remote.operations))

    def test_discovery_uses_only_manifests_and_refreshes_matching_projects(self):
        project = _project()
        self.remote.add_project(project, _bundle())
        unrelated = TEST_PROFILE.remote_project_root + "/unrelated"
        self.remote.directories.add(unrelated)
        self.remote.files[unrelated + "/geometry.in"] = b"atom 0 0 0 H\n"
        self.remote.squeue_stdout = b"12345|RUNNING\n"

        discovery = self.service.discover_and_refresh(TEST_PROFILE)

        self.assertEqual(len(discovery.snapshots), 1)
        self.assertIs(
            discovery.snapshots[0].active_step.state,
            ProjectStepState.RUNNING,
        )
        self.assertEqual(discovery.problems, ())
        self.assertEqual(
            parse_project_manifest(
                self.remote.files[
                    project.remote_project_path + "/.moltage/project.json"
                ]
            ).revision,
            3,
        )

    def test_recycled_project_is_suppressed_before_scheduler_reconciliation(self):
        project = _project()
        self.remote.add_project(project, _bundle())
        self.remote.squeue_stdout = b"12345|RUNNING\n"
        self.index.recycle(
            project,
            bound_server_profile_id=TEST_PROFILE.profile_id,
            submitted_at=project.steps[0].submitted_at,
            recycled_at=NOW,
        )

        discovery = self.service.discover_and_refresh(TEST_PROFILE)

        self.assertEqual(discovery.snapshots, ())
        self.assertEqual(discovery.problems, ())
        manifest_path = (
            project.remote_project_path + "/.moltage/project.json"
        )
        self.assertIn(("read", manifest_path), self.remote.operations)
        self.assertFalse(
            any(item[0] == "execute" for item in self.remote.operations)
        )
        self.assertFalse(
            any(item[0] in {"head", "tail", "write", "rename"}
                for item in self.remote.operations)
        )


if __name__ == "__main__":
    unittest.main()
