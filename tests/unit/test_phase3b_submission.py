from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from moltage.aims.transport_evidence import (
    TransportCompletionEvidence,
    TransportSpinMode,
)
from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.recovery import (
    parse_control_species_elements,
    parse_molecular_geometry,
)
from moltage.aitranss.slurm import (
    AitranssExecutionSettings,
    render_aitranss_submit_script,
    render_step3_retry_script,
)
from moltage.aitranss.output import AitranssFailureCode
from moltage.aitranss.self_energy import (
    build_partitioned_self_energy_plan,
    parse_self_energy,
    render_self_energy,
    validate_self_energy_round_trip,
)
from moltage.aitranss.tcontrol import (
    TControlSettings,
    parse_tcontrol,
    render_tcontrol,
    render_tcontrol_replacing_self_energy,
    render_tcontrol_with_self_energy,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.app.project_planning import create_initial_project
from moltage.app.project_submission import SubmissionOutcomeUnknown
from moltage.app.transport_submission import (
    STEP4_OUTPUT_FILENAME,
    STEP4_SCRIPT_FILENAME,
    STEP4_ATTEMPT01_TCONTROL_FILENAME,
    STEP4_ATTEMPT02_TCONTROL_FILENAME,
    STEP4_RETRY_OUTPUT_FILENAME,
    STEP4_RETRY03_OUTPUT_FILENAME,
    STEP4_RETRY03_SCRIPT_FILENAME,
    STEP4_RETRY03_SELF_ENERGY_FILENAME,
    STEP4_RETRY_SCRIPT_FILENAME,
    STEP4_RETRY_SELF_ENERGY_FILENAME,
    STEP4_TCONTROL_FILENAME,
    AitranssExecutableUnavailableError,
    Step3RetryConflictError,
    Step3RetryRequest,
    Step4SubmissionConflictError,
    Step4SubmissionRequest,
    Step4SettingsRetryRequest,
    Step4ExplicitRetryConflictError,
    Step4ExplicitRetryRequest,
    Step4ReaderCompatibleRetryRequest,
    TransportWorkflowSubmissionService,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepAttempt,
    ProjectStepState,
)
from moltage.domain.server_profile import SlurmMailSettings
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
    RemotePathNotFoundError,
    RemotePathStat,
)
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from moltage.remote.slurm import render_submit_script
from moltage.remote.slurm_discovery import CURRENT_ENVIRONMENT_DISCOVERY_COMMAND
from moltage.remote.runtime_environment import RuntimeConfigurationError
from moltage.remote.aitranss_discovery import (
    AITRANSS_MARKER,
    AITRANSS_RESOLVED_MARKER,
)
from moltage.junction.electrode_surface import propose_electrode_surfaces
from phase2b1_test_support import profile
from synthetic_structure_test_support import (
    synthetic_extended_electrode_placement,
    synthetic_junction_geometry_bytes,
    synthetic_junction_provenance,
    synthetic_junction_structure,
)


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
PROFILE = profile(profile_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"))
AITRANSS_PATH = PROFILE.aitranss_runtime.executable_path


class MemoryRemote:
    def __init__(self):
        self.directories = {"/", "/nfs", "/srv/moltage-test/projects"}
        self.files = {}
        self.commands = []
        self.operations = []
        self.sbatch_result = RemoteCommandResult(0, b"90002\n", b"")
        self.sbatch_error = None
        self.srun_available = True
        self.closed = False

    def close(self):
        self.closed = True

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

    def read_file_head(self, path, max_bytes):
        return self.read_bytes(path)[:max_bytes]

    def write_bytes(self, path, data):
        self.operations.append(("write", path))
        self.files[path] = data

    def rename(self, source, destination):
        self.operations.append(("rename", source, destination))
        self.files[destination] = self.files.pop(source)

    def execute(self, command):
        self.commands.append(command)
        if command == CURRENT_ENVIRONMENT_DISCOVERY_COMMAND:
            return RemoteCommandResult(0, b"/usr/bin/sbatch\n", b"")
        if command == "/usr/bin/sbatch --version":
            return RemoteCommandResult(0, b"slurm 24.11.3\n", b"")
        if command == f"test -f {AITRANSS_PATH} && test -x {AITRANSS_PATH}":
            return RemoteCommandResult(0, b"", b"")
        if command == "test -f /usr/bin/srun && test -r /usr/bin/srun && test -x /usr/bin/srun":
            return RemoteCommandResult(0 if self.srun_available else 3, b"", b"")
        if command.startswith("bash -lc ") and AITRANSS_MARKER in command:
            return RemoteCommandResult(
                0,
                (
                    f"{AITRANSS_MARKER}{AITRANSS_PATH}\n"
                    f"{AITRANSS_RESOLVED_MARKER}{AITRANSS_PATH}\n"
                ).encode(),
                b"",
            )
        if "--parsable" in command:
            if self.sbatch_error is not None:
                raise self.sbatch_error
            return self.sbatch_result
        raise AssertionError(command)


class FixedConnection:
    def __init__(self, remote):
        self.remote = remote

    def connect_for_remote_operation(self, profile, password=None):
        del profile, password
        return self.remote


def _base_project():
    return create_initial_project(
        base_name="Phase3B",
        remote_directory_name="Phase3B.20300829",
        source_molecule_name="Phase3B.xyz",
        server_profile_id=PROFILE.profile_id,
        remote_project_root=PROFILE.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        now=NOW,
        project_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
    )


def _retry_project():
    base = _base_project()
    geometry = b"atom 0 0 0 Au\n"
    control = b"spin none\n"
    script = render_submit_script(
        PROFILE.execution_preset,
        base.project_id,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
    ).encode()
    step2 = replace(base.steps[1], state=ProjectStepState.SUCCEEDED)
    step3 = replace(
        base.steps[2],
        state=ProjectStepState.FAILED,
        job_id="41001",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=tuple(
            (name, hashlib.sha256(data).hexdigest())
            for name, data in (
                ("geometry.in", geometry),
                ("control.in", control),
                ("submit.sh", script),
            )
        ),
        last_error="运行时间到达设定上限",
        scheduler_state="TIMEOUT",
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    project = replace(
        base,
        revision=8,
        steps=(base.steps[0], step2, step3, base.steps[3]),
        legacy_electrode_recovery_allowed=True,
    )
    return project, geometry, control, script


def _extended_retry_project():
    project, _geometry, control, script = _retry_project()
    applied = synthetic_extended_electrode_placement()
    geometry = render_geometry_in(applied.structure).encode("utf-8")
    step3 = replace(
        project.steps[2],
        input_hashes=tuple(
            (name, hashlib.sha256(geometry).hexdigest() if name == "geometry.in" else digest)
            for name, digest in project.steps[2].input_hashes
        ),
    )
    project = replace(
        project,
        steps=(project.steps[0], project.steps[1], step3, project.steps[3]),
        electrode_provenance=provenance_from_applied_electrodes(applied),
        legacy_electrode_recovery_allowed=False,
    )
    return project, geometry, control, script


def _step4_project():
    base = _base_project()
    structure = synthetic_junction_structure()
    geometry = synthetic_junction_geometry_bytes()
    digest = hashlib.sha256(geometry).hexdigest()
    step2 = replace(base.steps[1], state=ProjectStepState.SUCCEEDED)
    step3 = replace(
        base.steps[2],
        state=ProjectStepState.SUCCEEDED,
        job_id="90001",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=(
            ("geometry.in", digest),
            ("control.in", "1" * 64),
            ("submit.sh", "2" * 64),
        ),
        scheduler_state="COMPLETED",
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    provenance = synthetic_junction_provenance()
    project = replace(
        base,
        revision=9,
        steps=(base.steps[0], step2, step3, base.steps[3]),
        electrode_provenance=provenance,
    )
    evidence = TransportCompletionEvidence(
        len(structure), 512, TransportSpinMode.NONE, digest
    )
    surface = propose_electrode_surfaces(structure, provenance)
    settings = TControlSettings(
        natoms=len(structure),
        nsaos=512,
        lsurc=surface.left_one_based[0],
        lsurx=surface.left_one_based[1],
        lsury=surface.left_one_based[2],
        rsurc=surface.right_one_based[0],
        rsurx=surface.right_one_based[1],
        rsury=surface.right_one_based[2],
    )
    return project, structure, geometry, evidence, settings


def _install(remote, project, files):
    root = project.remote_project_path
    directory = root + "/molecule_Au/transport"
    metadata = root + "/.moltage"
    remote.directories.update((root, root + "/molecule_Au", directory, metadata))
    remote.files[metadata + "/project.json"] = serialize_project_manifest(project).encode()
    for name, data in files.items():
        remote.files[directory + "/" + name] = data
    return directory, metadata + "/project.json"


def _explicit_retry_project(*, extended: bool = False):
    base = _base_project()
    if extended:
        applied = synthetic_extended_electrode_placement()
        geometry = render_geometry_in(applied.structure).encode("utf-8")
        provenance = provenance_from_applied_electrodes(applied)
    else:
        geometry = synthetic_junction_geometry_bytes()
        provenance = synthetic_junction_provenance()
    control = (
        b"spin none\n"
        b"species H\n  nucleus 1\n"
        b"species C\n  nucleus 6\n"
        b"species N\n  nucleus 7\n"
        b"species S\n  nucleus 16\n"
        b"species Au\n  nucleus 79\n"
    )
    structure = parse_molecular_geometry(
        geometry,
        parse_control_species_elements(control),
        source_name="geometry.in",
    )
    surface = propose_electrode_surfaces(structure, provenance)
    settings = TControlSettings(
        natoms=len(structure),
        nsaos=512,
        lsurc=surface.left_one_based[0],
        lsurx=surface.left_one_based[1],
        lsury=surface.left_one_based[2],
        rsurc=surface.right_one_based[0],
        rsurx=surface.right_one_based[1],
        rsury=surface.right_one_based[2],
    )
    attempt01 = render_tcontrol(
        settings,
        structure,
        TransportSpinMode.NONE,
    ).encode("utf-8")
    parsed_tcontrol = parse_tcontrol(attempt01, structure)
    plan = build_partitioned_self_energy_plan(
        structure,
        surface,
        parsed_tcontrol.settings,
    )
    step3_script = b"frozen Step-3 script\n"
    attempt01_script = render_aitranss_submit_script(
        profile_preset=PROFILE.execution_preset,
        settings=AitranssExecutionSettings(),
        project_id=base.project_id,
        executable_path=AITRANSS_PATH,
        aitranss_modules=PROFILE.aitranss_runtime.modules,
        mail_settings=SlurmMailSettings("scientist@example.org"),
    ).encode()
    step2 = replace(base.steps[1], state=ProjectStepState.SUCCEEDED)
    step3 = replace(
        base.steps[2],
        state=ProjectStepState.SUCCEEDED,
        job_id="41002",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=tuple(
            (name, hashlib.sha256(data).hexdigest())
            for name, data in (
                ("geometry.in", geometry),
                ("control.in", control),
                ("submit.retry02.sh", step3_script),
            )
        ),
        scheduler_state="COMPLETED",
        submit_script_filename="submit.retry02.sh",
        slurm_output_filename="aims.dft.retry02.out",
    )
    step4 = replace(
        base.steps[3],
        state=ProjectStepState.FAILED,
        job_id="42001",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=(
            ("tcontrol", hashlib.sha256(attempt01).hexdigest()),
            ("submit.aitranss.sh", hashlib.sha256(attempt01_script).hexdigest()),
        ),
        last_error="AITRANSS_ELECTRODE_INTERFACE_OVERLAP",
        scheduler_state="COMPLETED",
        submit_script_filename="submit.aitranss.sh",
        slurm_output_filename="aitranss.out",
    )
    project = replace(
        base,
        revision=12,
        steps=(base.steps[0], step2, step3, step4),
        electrode_provenance=provenance,
    )
    evidence = TransportCompletionEvidence(
        len(structure),
        512,
        TransportSpinMode.NONE,
        hashlib.sha256(geometry).hexdigest(),
    )
    files = {
        "geometry.in": geometry,
        "control.in": control,
        "submit.retry02.sh": step3_script,
        "aims.dft.retry02.out": b"Have a nice day.\n",
        "basis-indices.out": b"basis immutable\n",
        "omat.aims": b"omat immutable\n",
        "mos.aims": b"$scfmo.aims\n nsaos=512\nimmutable\n",
        "tcontrol": attempt01,
        "submit.aitranss.sh": attempt01_script,
        "aitranss.out": b"reviewed overlap fatal\n",
    }
    return project, structure, surface, plan, evidence, attempt01, files


def _reader_compatible_retry_project():
    project, structure, surface, plan, evidence, attempt01, files = (
        _explicit_retry_project()
    )
    attempt01_script = files[STEP4_SCRIPT_FILENAME]
    attempt02_tcontrol = render_tcontrol_with_self_energy(
        attempt01,
        STEP4_RETRY_SELF_ENERGY_FILENAME,
        structure,
    )
    failed_attempt02_self_energy = render_self_energy(plan).replace(
        b"empty",
        b"     ",
    )
    attempt02_script = render_aitranss_submit_script(
        profile_preset=PROFILE.execution_preset,
        settings=AitranssExecutionSettings(),
        project_id=project.project_id,
        executable_path=AITRANSS_PATH,
        aitranss_modules=PROFILE.aitranss_runtime.modules,
        output_filename=STEP4_RETRY_OUTPUT_FILENAME,
        mail_settings=SlurmMailSettings("scientist@example.org"),
    ).encode("utf-8")
    attempt01_record = ProjectStepAttempt(
        job_id="42001",
        submitted_at=NOW,
        finished_at=NOW,
        terminal_scheduler_state="COMPLETED",
        failure_reason=AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP.value,
        submit_script_filename=STEP4_SCRIPT_FILENAME,
        slurm_output_filename="aitranss.out",
        input_hashes=(
            (
                STEP4_ATTEMPT01_TCONTROL_FILENAME,
                hashlib.sha256(attempt01).hexdigest(),
            ),
            (
                STEP4_SCRIPT_FILENAME,
                hashlib.sha256(attempt01_script).hexdigest(),
            ),
        ),
    )
    step4 = replace(
        project.steps[3],
        state=ProjectStepState.FAILED,
        job_id="42002",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=(
            (
                STEP4_TCONTROL_FILENAME,
                hashlib.sha256(attempt02_tcontrol).hexdigest(),
            ),
            (
                STEP4_RETRY_SELF_ENERGY_FILENAME,
                hashlib.sha256(failed_attempt02_self_energy).hexdigest(),
            ),
            (
                STEP4_RETRY_SCRIPT_FILENAME,
                hashlib.sha256(attempt02_script).hexdigest(),
            ),
        ),
        last_error=AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR.value,
        scheduler_state="COMPLETED",
        submit_script_filename=STEP4_RETRY_SCRIPT_FILENAME,
        slurm_output_filename=STEP4_RETRY_OUTPUT_FILENAME,
        attempts=(attempt01_record,),
    )
    project = replace(
        project,
        revision=15,
        steps=(*project.steps[:3], step4),
    )
    files.update(
        {
            STEP4_ATTEMPT01_TCONTROL_FILENAME: attempt01,
            STEP4_TCONTROL_FILENAME: attempt02_tcontrol,
            STEP4_RETRY_SELF_ENERGY_FILENAME: failed_attempt02_self_energy,
            STEP4_RETRY_SCRIPT_FILENAME: attempt02_script,
            STEP4_RETRY_OUTPUT_FILENAME: (
                Path(__file__).parents[1]
                / "fixtures"
                / "phase4b"
                / "aitranss_self_energy_file_format_error.out"
            ).read_bytes(),
        }
    )
    return (
        project,
        structure,
        surface,
        plan,
        evidence,
        attempt02_tcontrol,
        files,
    )


class Phase3BSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.remote = MemoryRemote()
        self.index = LocalProjectIndexRepository(Path(self.temp.name) / "projects.json")
        self.service = TransportWorkflowSubmissionService(
            FixedConnection(self.remote),
            self.index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "phase3b",
        )

    def test_missing_profile_runtime_fails_before_connection_without_fallback(self):
        unconfigured = replace(PROFILE, aitranss_runtime=None)

        with self.assertRaisesRegex(
            AitranssExecutableUnavailableError,
            "run server runtime discovery",
        ):
            self.service.preflight_aitranss(unconfigured)

        self.assertEqual(self.remote.commands, [])
        self.assertFalse(self.remote.closed)

    def test_step4_unavailable_configured_srun_fails_before_remote_writes(self):
        project, structure, geometry, evidence, settings = _step4_project()
        directory, _ = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": b"frozen",
                "submit.sh": b"frozen",
                "basis-indices.out": b"basis",
                "omat.aims": b"omat",
                "mos.aims": b"mos",
            },
        )
        before_files = dict(self.remote.files)
        before_operations = len(self.remote.operations)
        self.remote.srun_available = False

        with self.assertRaisesRegex(
            RuntimeConfigurationError,
            "Configured AITRANSS srun executable is unavailable",
        ):
            self.service.submit_step4(
                Step4SubmissionRequest(
                    PROFILE,
                    project,
                    structure,
                    evidence,
                    settings,
                    AitranssExecutionSettings(),
                    AITRANSS_PATH,
                )
            )

        self.assertEqual(self.remote.files, before_files)
        self.assertEqual(
            self.remote.operations[before_operations:],
            [],
            f"unexpected mutation under {directory}",
        )
        self.assertFalse(any("--parsable" in command for command in self.remote.commands))

    def test_step3_retry_uses_configured_launcher_and_preserves_scientific_files(self):
        from moltage.domain.server_profile import FhiAimsRuntimeConfiguration
        from test_runtime_configuration import AIMS, MPI, NONE

        project, geometry, control, script = _retry_project()
        directory, _ = _install(self.remote, project, {
            "geometry.in": geometry, "control.in": control, "submit.sh": script,
            "aims.dft.out": b"old timeout log\n", "aims.restart": b"restart-state"})
        configured = replace(PROFILE, execution_preset=replace(PROFILE.execution_preset,
            fhi_runtime=FhiAimsRuntimeConfiguration(AIMS, MPI, NONE)))
        self.remote.files[AIMS] = b"\x7fELFsynthetic"
        original_execute = self.remote.execute

        def execute(command):
            if "__AT_RUNTIME_AVAILABLE__" in command:
                self.remote.commands.append(command)
                return RemoteCommandResult(0, b"__AT_RUNTIME_AVAILABLE__\n", b"")
            return original_execute(command)

        with patch.object(self.remote, "execute", side_effect=execute):
            self.service.retry_step3(Step3RetryRequest(configured, project,
                replace(configured.execution_preset, ntasks=6)))
        self.assertEqual(self.remote.files[directory + "/geometry.in"], geometry)
        self.assertEqual(self.remote.files[directory + "/control.in"], control)
        self.assertEqual(self.remote.files[directory + "/aims.restart"], b"restart-state")
        retry = self.remote.files[directory + "/submit.retry02.sh"].decode()
        self.assertEqual(retry.splitlines()[-1], f"exec {MPI} -n 6 {AIMS}")
        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)

    def test_step4_manual_nonmodule_runtime_reaches_real_submission_path(self):
        from moltage.domain.server_profile import AitranssRuntimeConfiguration
        from test_runtime_configuration import NONE

        executable = "/custom/bin/aitranss.x"
        project, structure, geometry, evidence, settings = _step4_project()
        frozen = {"geometry.in": geometry, "control.in": b"frozen", "submit.sh": b"frozen",
                  "basis-indices.out": b"basis", "omat.aims": b"omat", "mos.aims": b"mos"}
        directory, _ = _install(self.remote, project, frozen)
        configured = replace(PROFILE, aitranss_runtime=AitranssRuntimeConfiguration((), executable, NONE))
        original_execute = self.remote.execute

        def execute(command):
            if command == f"test -f {executable} && test -x {executable}":
                self.remote.commands.append(command)
                return RemoteCommandResult(0, b"", b"")
            return original_execute(command)

        with patch.object(self.remote, "execute", side_effect=execute):
            self.service.submit_step4(Step4SubmissionRequest(configured, project, structure,
                evidence, settings, AitranssExecutionSettings(), executable))
        for filename, data in frozen.items():
            self.assertEqual(self.remote.files[directory + "/" + filename], data)
        script = self.remote.files[directory + "/" + STEP4_SCRIPT_FILENAME].decode()
        self.assertNotIn("module ", script)
        self.assertEqual(script.splitlines()[-1], "exec /usr/bin/srun --ntasks=1 " + executable)
        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)

    def test_aitranss_renderer_uses_configured_modules_not_a_source_default(self):
        script = render_aitranss_submit_script(
            profile_preset=PROFILE.execution_preset,
            settings=AitranssExecutionSettings(),
            project_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            executable_path=AITRANSS_PATH,
            aitranss_modules=("site/verified-aitranss",),
        )

        self.assertIn("module load site/verified-aitranss\n", script)
        self.assertNotIn("module load chemistry/aitranss-example\n", script)

    def test_retry_preserves_science_restart_and_old_attempt_then_submits_once(self):
        project, geometry, control, script = _retry_project()
        directory, manifest_path = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": control,
                "submit.sh": script,
                "aims.dft.out": b"old timeout log\n",
                "aims.restart": b"restart-state",
            },
        )
        retry_preset = replace(PROFILE.execution_preset, runtime_minutes=4320)

        result = self.service.retry_step3(
            Step3RetryRequest(PROFILE, project, retry_preset)
        )

        self.assertEqual(result.job_id, "90002")
        self.assertEqual(self.remote.files[directory + "/geometry.in"], geometry)
        self.assertEqual(self.remote.files[directory + "/control.in"], control)
        self.assertEqual(self.remote.files[directory + "/aims.restart"], b"restart-state")
        self.assertEqual(self.remote.files[directory + "/aims.dft.out"], b"old timeout log\n")
        retry_script = self.remote.files[directory + "/submit.retry02.sh"].decode()
        self.assertIn("#SBATCH --time=4320", retry_script)
        self.assertIn("#SBATCH --output=aims.dft.retry02.out", retry_script)
        self.assertEqual(retry_script.count("--kill-on-bad-exit=1"), 1)
        self.assertEqual(
            retry_script.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )
        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        step = persisted.steps[2]
        self.assertIs(step.state, ProjectStepState.QUEUED)
        self.assertEqual(step.job_id, "90002")
        self.assertEqual(step.submit_script_filename, "submit.retry02.sh")
        self.assertEqual(step.slurm_output_filename, "aims.dft.retry02.out")
        self.assertEqual(len(step.attempts), 1)
        self.assertEqual(step.attempts[0].job_id, "41001")
        self.assertEqual(step.attempts[0].terminal_scheduler_state, "TIMEOUT")
        self.assertEqual(
            dict(step.attempts[0].input_hashes),
            {
                "geometry.in": hashlib.sha256(geometry).hexdigest(),
                "control.in": hashlib.sha256(control).hexdigest(),
                "submit.sh": hashlib.sha256(script).hexdigest(),
            },
        )

    def test_step3_resource_retry_preserves_schema_eight_extensions(self):
        project, geometry, control, script = _extended_retry_project()
        directory, manifest_path = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": control,
                "submit.sh": script,
                "aims.dft.out": b"old timeout log\n",
            },
        )

        result = self.service.retry_step3(
            Step3RetryRequest(
                PROFILE,
                project,
                replace(PROFILE.execution_preset, runtime_minutes=4320),
            )
        )
        persisted = parse_project_manifest(self.remote.files[manifest_path])

        self.assertEqual(result.project.electrode_provenance, project.electrode_provenance)
        self.assertEqual(persisted.electrode_provenance, project.electrode_provenance)
        self.assertEqual(
            tuple(
                len(record.lattice_extensions)
                for record in persisted.electrode_provenance
            ),
            (2, 1),
        )
        self.assertEqual(self.remote.files[directory + "/geometry.in"], geometry)

    def test_oom_memory_retry_uses_current_fail_fast_mail_and_preserves_attempt(self):
        project, geometry, control, current_script = _retry_project()
        legacy_script = current_script.replace(
            b"srun --kill-on-bad-exit=1 ",
            b"srun ",
        )
        old_step = replace(
            project.steps[2],
            last_error="SLURM_TASK_OUT_OF_MEMORY",
            scheduler_state="CANCELLED",
            input_hashes=(
                ("geometry.in", hashlib.sha256(geometry).hexdigest()),
                ("control.in", hashlib.sha256(control).hexdigest()),
                ("submit.sh", hashlib.sha256(legacy_script).hexdigest()),
            ),
        )
        project = replace(
            project,
            steps=(*project.steps[:2], old_step, project.steps[3]),
        )
        directory, manifest_path = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": control,
                "submit.sh": legacy_script,
                "aims.dft.out": b"reviewed OOM evidence\n",
            },
        )
        mail_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        retry_preset = replace(PROFILE.execution_preset, memory_gb=192)

        self.service.retry_step3(
            Step3RetryRequest(mail_profile, project, retry_preset)
        )

        retry_script = self.remote.files[
            directory + "/submit.retry02.sh"
        ].decode("utf-8")
        self.assertIn("#SBATCH --mem=192G", retry_script)
        self.assertNotIn("#SBATCH --mem=128G", retry_script)
        self.assertEqual(retry_script.count("--kill-on-bad-exit=1"), 1)
        self.assertEqual(
            retry_script.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )
        self.assertEqual(
            retry_script.count("#SBATCH --mail-user=user@example.com"),
            1,
        )
        self.assertEqual(retry_script.count("#SBATCH --mail-type=END,FAIL"), 1)
        self.assertNotIn("TIME_LIMIT", retry_script)
        self.assertEqual(self.remote.files[directory + "/geometry.in"], geometry)
        self.assertEqual(self.remote.files[directory + "/control.in"], control)
        self.assertEqual(self.remote.files[directory + "/submit.sh"], legacy_script)
        self.assertEqual(sum("--parsable" in item for item in self.remote.commands), 1)
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        attempt = persisted.steps[2].attempts[0]
        self.assertEqual(attempt.job_id, "41001")
        self.assertEqual(attempt.terminal_scheduler_state, "CANCELLED")
        self.assertEqual(attempt.failure_reason, "SLURM_TASK_OUT_OF_MEMORY")
        self.assertEqual(attempt.submit_script_filename, "submit.sh")
        self.assertEqual(attempt.slurm_output_filename, "aims.dft.out")
        self.assertEqual(
            dict(attempt.input_hashes)["submit.sh"],
            hashlib.sha256(legacy_script).hexdigest(),
        )

    def test_retry03_uses_current_mail_and_preserves_historical_retry02(self):
        project, geometry, control, initial_script = _retry_project()
        old_retry_script = render_step3_retry_script(
            PROFILE.execution_preset,
            project.project_id,
            "aims.dft.retry02.out",
            mail_settings=SlurmMailSettings("old@example.com"),
        ).encode("utf-8")
        current_step = replace(
            project.steps[2],
            job_id="41004",
            submit_script_filename="submit.retry02.sh",
            slurm_output_filename="aims.dft.retry02.out",
            attempts=(
                ProjectStepAttempt(
                    "41001",
                    NOW,
                    NOW,
                    "TIMEOUT",
                    "运行时间到达设定上限",
                    "submit.sh",
                    "aims.dft.out",
                ),
            ),
        )
        project = replace(
            project,
            steps=(
                project.steps[0],
                project.steps[1],
                current_step,
                project.steps[3],
            ),
        )
        directory, manifest_path = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": control,
                "submit.sh": initial_script,
                "submit.retry02.sh": old_retry_script,
                "aims.dft.retry02.out": b"historical retry output\n",
                "aims.restart": b"restart-state",
            },
        )
        current_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="new@example.com",
        )

        self.service.retry_step3(
            Step3RetryRequest(
                current_profile,
                project,
                PROFILE.execution_preset,
            )
        )

        self.assertEqual(
            self.remote.files[directory + "/submit.retry02.sh"],
            old_retry_script,
        )
        retry03 = self.remote.files[
            directory + "/submit.retry03.sh"
        ].decode("utf-8")
        self.assertIn("#SBATCH --mail-user=new@example.com", retry03)
        self.assertIn("#SBATCH --mail-type=END,FAIL", retry03)
        self.assertNotIn("old@example.com", retry03)
        self.assertNotIn("TIME_LIMIT", retry03)
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        step = persisted.steps[2]
        self.assertEqual(step.submit_script_filename, "submit.retry03.sh")
        self.assertEqual(step.slurm_output_filename, "aims.dft.retry03.out")
        self.assertEqual(
            self.remote.files[directory + "/aims.dft.retry02.out"],
            b"historical retry output\n",
        )

    def test_retry_hash_conflict_stops_before_upload_and_sbatch(self):
        project, geometry, control, script = _retry_project()
        directory, _ = _install(
            self.remote,
            project,
            {"geometry.in": geometry + b"changed", "control.in": control, "submit.sh": script},
        )
        with self.assertRaises(Step3RetryConflictError):
            self.service.retry_step3(
                Step3RetryRequest(PROFILE, project, PROFILE.execution_preset)
            )
        self.assertNotIn(directory + "/submit.retry02.sh", self.remote.files)
        self.assertFalse(any("--parsable" in command for command in self.remote.commands))

    def test_retry_unknown_command_outcome_is_persisted_and_never_retried(self):
        project, geometry, control, script = _retry_project()
        _, manifest_path = _install(
            self.remote,
            project,
            {"geometry.in": geometry, "control.in": control, "submit.sh": script},
        )
        self.remote.sbatch_error = RemoteCommandOutcomeUnknown("lost after dispatch")
        with self.assertRaises(SubmissionOutcomeUnknown):
            self.service.retry_step3(
                Step3RetryRequest(PROFILE, project, PROFILE.execution_preset)
            )
        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)
        step = parse_project_manifest(self.remote.files[manifest_path]).steps[2]
        self.assertIs(step.state, ProjectStepState.UNKNOWN)
        self.assertIsNone(step.job_id)
        self.assertEqual(step.submit_script_filename, "submit.retry02.sh")
        self.assertEqual(len(step.attempts), 1)

    def test_step4_uploads_only_new_files_and_queues_one_process_job(self):
        project, structure, geometry, evidence, settings = _step4_project()
        directory, manifest_path = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": b"frozen",
                "submit.sh": b"frozen-step3",
                "basis-indices.out": b"basis",
                "omat.aims": b"omat",
                "mos.aims": b"mos",
            },
        )
        before = {name: self.remote.files[directory + "/" + name] for name in ("geometry.in", "control.in", "submit.sh", "basis-indices.out", "omat.aims", "mos.aims")}
        request = Step4SubmissionRequest(
            PROFILE,
            project,
            structure,
            evidence,
            settings,
            AitranssExecutionSettings(),
            AITRANSS_PATH,
        )

        result = self.service.submit_step4(request)

        self.assertEqual(result.job_id, "90002")
        self.assertIn(directory + "/" + STEP4_TCONTROL_FILENAME, self.remote.files)
        script_text = self.remote.files[directory + "/" + STEP4_SCRIPT_FILENAME].decode()
        self.assertIn("#SBATCH --nodes=1", script_text)
        self.assertIn("#SBATCH --ntasks=1", script_text)
        self.assertIn("#SBATCH --cpus-per-task=1", script_text)
        self.assertIn("#SBATCH --time=600", script_text)
        self.assertIn("#SBATCH --mem=100G", script_text)
        self.assertIn("export OMP_NUM_THREADS=1", script_text)
        self.assertIn(
            f"module load {PROFILE.aitranss_runtime.modules[0]}",
            script_text,
        )
        self.assertNotIn("module load mpi/example-1.0\n", script_text)
        self.assertEqual(
            script_text.count(f"exec /usr/bin/srun --ntasks=1 {AITRANSS_PATH}"),
            1,
        )
        self.assertNotIn("--kill-on-bad-exit", script_text)
        self.assertEqual(
            script_text.splitlines()[-1],
            f"exec /usr/bin/srun --ntasks=1 {AITRANSS_PATH}",
        )
        self.assertNotIn("mpirun", script_text)
        for name, data in before.items():
            self.assertEqual(self.remote.files[directory + "/" + name], data)
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        self.assertIs(persisted.steps[2].state, ProjectStepState.SUCCEEDED)
        self.assertIs(persisted.steps[3].state, ProjectStepState.QUEUED)
        self.assertEqual(persisted.steps[3].job_id, "90002")
        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)

    def test_step4_uses_current_mail_without_changing_one_process_contract(self):
        project, structure, geometry, evidence, settings = _step4_project()
        directory, _ = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "control.in": b"frozen",
                "submit.sh": b"frozen",
                "basis-indices.out": b"basis",
                "omat.aims": b"omat",
                "mos.aims": b"mos",
            },
        )
        enabled_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )

        self.service.submit_step4(
            Step4SubmissionRequest(
                enabled_profile,
                project,
                structure,
                evidence,
                settings,
                AitranssExecutionSettings(),
                AITRANSS_PATH,
            )
        )

        script = self.remote.files[
            directory + "/" + STEP4_SCRIPT_FILENAME
        ].decode("utf-8")
        self.assertEqual(script.count("#SBATCH --mail-user=user@example.com"), 1)
        self.assertEqual(script.count("#SBATCH --mail-type=END,FAIL"), 1)
        self.assertEqual(script.count("#SBATCH --ntasks=1"), 1)
        self.assertEqual(script.count(f"exec /usr/bin/srun --ntasks=1 {AITRANSS_PATH}"), 1)
        self.assertNotIn("--kill-on-bad-exit", script)
        self.assertNotIn("TIME_LIMIT", script)

    def test_step4_preexisting_file_conflict_stops_before_any_overwrite_or_sbatch(self):
        project, structure, geometry, evidence, settings = _step4_project()
        directory, _ = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "basis-indices.out": b"basis",
                "omat.aims": b"omat",
                "mos.aims": b"mos",
                "tcontrol": b"preexisting",
            },
        )
        request = Step4SubmissionRequest(
            PROFILE,
            project,
            structure,
            evidence,
            settings,
            AitranssExecutionSettings(),
            AITRANSS_PATH,
        )
        with self.assertRaises(Step4SubmissionConflictError):
            self.service.submit_step4(request)
        self.assertEqual(self.remote.files[directory + "/tcontrol"], b"preexisting")
        self.assertNotIn(directory + "/submit.aitranss.sh", self.remote.files)
        self.assertFalse(any("--parsable" in command for command in self.remote.commands))

    def test_step4_preexisting_script_conflict_stops_before_tcontrol_or_sbatch(self):
        project, structure, geometry, evidence, settings = _step4_project()
        directory, _ = _install(
            self.remote,
            project,
            {
                "geometry.in": geometry,
                "basis-indices.out": b"basis",
                "omat.aims": b"omat",
                "mos.aims": b"mos",
                "submit.aitranss.sh": b"preexisting",
            },
        )
        request = Step4SubmissionRequest(
            PROFILE,
            project,
            structure,
            evidence,
            settings,
            AitranssExecutionSettings(),
            AITRANSS_PATH,
        )
        with self.assertRaises(Step4SubmissionConflictError):
            self.service.submit_step4(request)
        self.assertEqual(
            self.remote.files[directory + "/submit.aitranss.sh"],
            b"preexisting",
        )
        self.assertNotIn(directory + "/tcontrol", self.remote.files)
        self.assertFalse(any("--parsable" in command for command in self.remote.commands))

    def test_cancelled_step4_settings_retry_preserves_attempt_and_science(self):
        (
            project,
            structure,
            _surface,
            _plan,
            evidence,
            attempt01,
            files,
        ) = _explicit_retry_project()
        current = replace(
            project.steps[3],
            state=ProjectStepState.FAILED,
            scheduler_state="CANCELLED",
            last_error="CANCELLED",
        )
        project = replace(
            project,
            revision=13,
            steps=(*project.steps[:3], current),
        )
        directory, manifest_path = _install(self.remote, project, files)
        parsed = parse_tcontrol(attempt01, structure)
        edited = replace(parsed.settings, ener="-0.3000", eend="-0.05")
        notified = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        immutable = {
            name: self.remote.files[directory + "/" + name]
            for name in (
                "geometry.in",
                "control.in",
                "submit.retry02.sh",
                "basis-indices.out",
                "omat.aims",
                "mos.aims",
            )
        }

        result = self.service.retry_cancelled_step4_with_settings(
            Step4SettingsRetryRequest(
                notified,
                project,
                structure,
                evidence,
                edited,
                AitranssExecutionSettings(
                    cpu_threads=4,
                    runtime_minutes=720,
                    memory_gb=120,
                ),
                None,
                AITRANSS_PATH,
            )
        )

        self.assertEqual(result.job_id, "90002")
        self.assertEqual(
            sum("--parsable" in command for command in self.remote.commands),
            1,
        )
        for name, data in immutable.items():
            self.assertEqual(self.remote.files[directory + "/" + name], data)
        self.assertEqual(
            self.remote.files[directory + "/tcontrol.attempt01"],
            attempt01,
        )
        self.assertIn(b"$ener   -0.3000\n", self.remote.files[directory + "/tcontrol"])
        script = self.remote.files[
            directory + "/submit.aitranss.retry02.sh"
        ].decode("utf-8")
        self.assertIn("#SBATCH --cpus-per-task=4", script)
        self.assertIn("#SBATCH --time=720", script)
        self.assertIn("#SBATCH --mem=120G", script)
        self.assertEqual(script.count("#SBATCH --mail-type=END,FAIL"), 1)
        self.assertNotIn("--kill-on-bad-exit", script)
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        active = persisted.steps[3]
        self.assertIs(active.state, ProjectStepState.QUEUED)
        self.assertEqual(active.job_id, "90002")
        self.assertEqual(active.submit_script_filename, "submit.aitranss.retry02.sh")
        self.assertEqual(active.slurm_output_filename, "aitranss.retry02.out")
        self.assertEqual(len(active.attempts), 1)
        self.assertEqual(active.attempts[0].job_id, "42001")
        self.assertEqual(
            active.attempts[0].terminal_scheduler_state,
            "CANCELLED",
        )

    def test_cancelled_step4_settings_retry_unknown_dispatch_is_recorded_once(self):
        (
            project,
            structure,
            _surface,
            _plan,
            evidence,
            attempt01,
            files,
        ) = _explicit_retry_project()
        current = replace(
            project.steps[3],
            state=ProjectStepState.FAILED,
            scheduler_state="CANCELLED",
            last_error="CANCELLED",
        )
        project = replace(
            project,
            revision=13,
            steps=(*project.steps[:3], current),
        )
        directory, manifest_path = _install(self.remote, project, files)
        parsed = parse_tcontrol(attempt01, structure)
        self.remote.sbatch_error = RemoteCommandOutcomeUnknown(
            "lost after dispatch"
        )

        with self.assertRaises(SubmissionOutcomeUnknown):
            self.service.retry_cancelled_step4_with_settings(
                Step4SettingsRetryRequest(
                    PROFILE,
                    project,
                    structure,
                    evidence,
                    parsed.settings,
                    AitranssExecutionSettings(),
                    None,
                    AITRANSS_PATH,
                )
            )

        self.assertEqual(
            sum("--parsable" in command for command in self.remote.commands),
            1,
        )
        self.assertIn(directory + "/tcontrol.attempt01", self.remote.files)
        active = parse_project_manifest(self.remote.files[manifest_path]).steps[3]
        self.assertIs(active.state, ProjectStepState.UNKNOWN)
        self.assertIsNone(active.job_id)
        self.assertEqual(active.submit_script_filename, "submit.aitranss.retry02.sh")
        self.assertEqual(active.slurm_output_filename, "aitranss.retry02.out")
        self.assertEqual(len(active.attempts), 1)
        self.assertEqual(active.attempts[0].job_id, "42001")

    def test_step4_explicit_retry_preserves_attempt_and_science_then_submits_once(self):
        (
            project,
            structure,
            surface,
            plan,
            evidence,
            attempt01,
            files,
        ) = _explicit_retry_project()
        directory, manifest_path = _install(self.remote, project, files)
        immutable_names = (
            "geometry.in",
            "control.in",
            "basis-indices.out",
            "omat.aims",
            "mos.aims",
            "aims.dft.retry02.out",
        )
        before = {name: self.remote.files[directory + "/" + name] for name in immutable_names}
        notified_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="scientist@example.org",
        )

        result = self.service.retry_step4_with_explicit_self_energy(
            Step4ExplicitRetryRequest(
                notified_profile,
                project,
                structure,
                evidence,
                surface,
                plan,
                attempt01,
            )
        )

        self.assertEqual(result.job_id, "90002")
        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)
        for name, data in before.items():
            self.assertEqual(self.remote.files[directory + "/" + name], data)
        self.assertEqual(
            self.remote.files[directory + "/" + STEP4_ATTEMPT01_TCONTROL_FILENAME],
            attempt01,
        )
        active_tcontrol = self.remote.files[directory + "/tcontrol"].decode()
        self.assertEqual(
            active_tcontrol.count(
                "$self_energy file=self.energy.retry02.in"
            ),
            1,
        )
        self.assertTrue(active_tcontrol.endswith("$end\n"))
        parsed_self_energy = parse_self_energy(
            self.remote.files[directory + "/" + STEP4_RETRY_SELF_ENERGY_FILENAME],
            structure,
        )
        self.assertEqual(len(parsed_self_energy.rows), 128)
        self.assertEqual(sum(row.reservoir == "left" for row in parsed_self_energy.rows), 52)
        self.assertEqual(sum(row.reservoir == "right" for row in parsed_self_energy.rows), 52)
        retry_script = self.remote.files[
            directory + "/" + STEP4_RETRY_SCRIPT_FILENAME
        ].decode()
        self.assertIn(f"#SBATCH --output={STEP4_RETRY_OUTPUT_FILENAME}", retry_script)
        self.assertIn("#SBATCH --mail-user=scientist@example.org", retry_script)
        self.assertIn("#SBATCH --mail-type=END,FAIL", retry_script)
        self.assertNotIn("TIME_LIMIT", retry_script)
        self.assertEqual(retry_script.count(f"exec /usr/bin/srun --ntasks=1 {AITRANSS_PATH}"), 1)

        persisted = parse_project_manifest(self.remote.files[manifest_path])
        self.assertEqual(
            persisted.electrode_provenance,
            project.electrode_provenance,
        )
        step4 = persisted.steps[3]
        self.assertIs(step4.state, ProjectStepState.QUEUED)
        self.assertEqual(step4.job_id, "90002")
        self.assertEqual(step4.submit_script_filename, STEP4_RETRY_SCRIPT_FILENAME)
        self.assertEqual(step4.slurm_output_filename, STEP4_RETRY_OUTPUT_FILENAME)
        self.assertEqual(len(step4.attempts), 1)
        attempt = step4.attempts[0]
        self.assertEqual(attempt.job_id, "42001")
        self.assertEqual(attempt.terminal_scheduler_state, "COMPLETED")
        self.assertEqual(
            attempt.failure_reason,
            "AITRANSS_ELECTRODE_INTERFACE_OVERLAP",
        )
        self.assertEqual(attempt.submit_script_filename, "submit.aitranss.sh")
        self.assertEqual(attempt.slurm_output_filename, "aitranss.out")
        self.assertIn(STEP4_ATTEMPT01_TCONTROL_FILENAME, dict(attempt.input_hashes))
        self.assertEqual(
            set(dict(step4.input_hashes)),
            {"tcontrol", STEP4_RETRY_SELF_ENERGY_FILENAME, STEP4_RETRY_SCRIPT_FILENAME},
        )

    def test_step4_self_energy_retry_preserves_extensions_and_nlayers(self):
        (
            project,
            structure,
            surface,
            plan,
            evidence,
            attempt01,
            files,
        ) = _explicit_retry_project(extended=True)
        directory, manifest_path = _install(self.remote, project, files)

        self.service.retry_step4_with_explicit_self_energy(
            Step4ExplicitRetryRequest(
                PROFILE,
                project,
                structure,
                evidence,
                surface,
                plan,
                attempt01,
            )
        )
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        parsed_tcontrol = parse_tcontrol(
            self.remote.files[directory + "/tcontrol"],
            structure,
        )
        parsed_self_energy = parse_self_energy(
            self.remote.files[
                directory + "/" + STEP4_RETRY_SELF_ENERGY_FILENAME
            ],
            structure,
        )

        self.assertEqual(
            persisted.electrode_provenance,
            project.electrode_provenance,
        )
        self.assertEqual(
            tuple(
                len(record.lattice_extensions)
                for record in persisted.electrode_provenance
            ),
            (2, 1),
        )
        self.assertEqual(parsed_tcontrol.settings.nlayers, 4)
        self.assertEqual(len(parsed_self_energy.rows), len(structure))

    def test_step4_explicit_retry_stops_before_writes_when_te_dat_exists(self):
        project, structure, surface, plan, evidence, attempt01, files = (
            _explicit_retry_project()
        )
        files["TE.dat"] = b"ambiguous existing transmission\n"
        directory, _manifest_path = _install(self.remote, project, files)

        with self.assertRaises(Step4ExplicitRetryConflictError):
            self.service.retry_step4_with_explicit_self_energy(
                Step4ExplicitRetryRequest(
                    PROFILE,
                    project,
                    structure,
                    evidence,
                    surface,
                    plan,
                    attempt01,
                )
            )

        self.assertFalse(any(operation[0] == "write" for operation in self.remote.operations))
        self.assertNotIn(directory + "/tcontrol.attempt01", self.remote.files)
        self.assertFalse(any("--parsable" in command for command in self.remote.commands))

    def test_step4_explicit_retry_unknown_dispatch_is_recorded_without_retry(self):
        project, structure, surface, plan, evidence, attempt01, files = (
            _explicit_retry_project()
        )
        _directory, manifest_path = _install(self.remote, project, files)
        self.remote.sbatch_error = RemoteCommandOutcomeUnknown("lost after dispatch")

        with self.assertRaises(SubmissionOutcomeUnknown):
            self.service.retry_step4_with_explicit_self_energy(
                Step4ExplicitRetryRequest(
                    PROFILE,
                    project,
                    structure,
                    evidence,
                    surface,
                    plan,
                    attempt01,
                )
            )

        self.assertEqual(sum("--parsable" in command for command in self.remote.commands), 1)
        step4 = parse_project_manifest(self.remote.files[manifest_path]).steps[3]
        self.assertIs(step4.state, ProjectStepState.UNKNOWN)
        self.assertIsNone(step4.job_id)
        self.assertEqual(len(step4.attempts), 1)
        self.assertEqual(step4.attempts[0].job_id, "42001")

    def test_step4_retry03_preserves_history_and_changes_serialization_only(self):
        (
            project,
            structure,
            surface,
            plan,
            evidence,
            attempt02_tcontrol,
            files,
        ) = _reader_compatible_retry_project()
        directory, manifest_path = _install(self.remote, project, files)
        historical_names = (
            "geometry.in",
            "control.in",
            "basis-indices.out",
            "omat.aims",
            "mos.aims",
            "aims.dft.retry02.out",
            STEP4_ATTEMPT01_TCONTROL_FILENAME,
            STEP4_SCRIPT_FILENAME,
            STEP4_OUTPUT_FILENAME,
            STEP4_RETRY_SELF_ENERGY_FILENAME,
            STEP4_RETRY_SCRIPT_FILENAME,
            STEP4_RETRY_OUTPUT_FILENAME,
        )
        before = {
            name: self.remote.files[directory + "/" + name]
            for name in historical_names
        }
        notified_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="scientist@example.org",
        )

        result = self.service.retry_step4_with_reader_compatible_self_energy(
            Step4ReaderCompatibleRetryRequest(
                notified_profile,
                project,
                structure,
                evidence,
                surface,
                plan,
                attempt02_tcontrol,
            )
        )

        self.assertEqual(result.job_id, "90002")
        self.assertEqual(sum("--parsable" in item for item in self.remote.commands), 1)
        for name, data in before.items():
            self.assertEqual(self.remote.files[directory + "/" + name], data)
        self.assertEqual(
            self.remote.files[
                directory + "/" + STEP4_ATTEMPT02_TCONTROL_FILENAME
            ],
            attempt02_tcontrol,
        )
        active_tcontrol = self.remote.files[
            directory + "/" + STEP4_TCONTROL_FILENAME
        ]
        self.assertEqual(
            active_tcontrol,
            render_tcontrol_replacing_self_energy(
                attempt02_tcontrol,
                STEP4_RETRY_SELF_ENERGY_FILENAME,
                STEP4_RETRY03_SELF_ENERGY_FILENAME,
                structure,
            ),
        )
        parsed_tcontrol = parse_tcontrol(active_tcontrol, structure)
        self.assertEqual(
            parsed_tcontrol.self_energy_filename,
            STEP4_RETRY03_SELF_ENERGY_FILENAME,
        )

        retry03_self_energy = self.remote.files[
            directory + "/" + STEP4_RETRY03_SELF_ENERGY_FILENAME
        ]
        compatibility = validate_self_energy_round_trip(
            retry03_self_energy,
            plan,
        )
        self.assertEqual(
            (
                compatibility.row_count,
                compatibility.left_count,
                compatibility.right_count,
                compatibility.empty_count,
            ),
            (128, 52, 52, 24),
        )
        atom_lines = retry03_self_energy.decode("ascii").splitlines()[1:-1]
        self.assertTrue(all(len(line.split()) == 7 for line in atom_lines))
        self.assertEqual(atom_lines[0].split()[5], "empty")
        self.assertEqual(atom_lines[37].split()[5], "left")
        self.assertEqual(atom_lines[110].split()[5], "right")
        self.assertEqual(retry03_self_energy, render_self_energy(plan))
        self.assertEqual((len(retry03_self_energy), len(atom_lines) + 2), (11692, 130))

        retry03_script = self.remote.files[
            directory + "/" + STEP4_RETRY03_SCRIPT_FILENAME
        ].decode("utf-8")
        persisted = parse_project_manifest(self.remote.files[manifest_path])
        self.assertEqual(
            persisted.electrode_provenance,
            project.electrode_provenance,
        )
        self.assertIn(
            f"#SBATCH --output={STEP4_RETRY03_OUTPUT_FILENAME}",
            retry03_script,
        )
        self.assertIn("#SBATCH --nodes=1", retry03_script)
        self.assertIn("#SBATCH --ntasks=1", retry03_script)
        self.assertIn("#SBATCH --cpus-per-task=1", retry03_script)
        self.assertIn("#SBATCH --time=600", retry03_script)
        self.assertIn("#SBATCH --mem=100G", retry03_script)
        self.assertIn("export OMP_NUM_THREADS=1", retry03_script)
        self.assertIn("#SBATCH --mail-user=scientist@example.org", retry03_script)
        self.assertIn("#SBATCH --mail-type=END,FAIL", retry03_script)
        self.assertNotIn("TIME_LIMIT", retry03_script)
        self.assertEqual(
            retry03_script.count(f"exec /usr/bin/srun --ntasks=1 {AITRANSS_PATH}"),
            1,
        )

        persisted = parse_project_manifest(self.remote.files[manifest_path])
        step4 = persisted.steps[3]
        self.assertIs(step4.state, ProjectStepState.QUEUED)
        self.assertEqual(step4.job_id, "90002")
        self.assertEqual(step4.submit_script_filename, STEP4_RETRY03_SCRIPT_FILENAME)
        self.assertEqual(step4.slurm_output_filename, STEP4_RETRY03_OUTPUT_FILENAME)
        self.assertEqual(len(step4.attempts), 2)
        self.assertEqual(step4.attempts[0], project.steps[3].attempts[0])
        attempt02 = step4.attempts[1]
        self.assertEqual(attempt02.job_id, "42002")
        self.assertEqual(
            attempt02.failure_reason,
            AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR.value,
        )
        self.assertEqual(
            set(dict(attempt02.input_hashes)),
            {
                STEP4_ATTEMPT02_TCONTROL_FILENAME,
                STEP4_RETRY_SELF_ENERGY_FILENAME,
                STEP4_RETRY_SCRIPT_FILENAME,
            },
        )
        self.assertEqual(
            set(dict(step4.input_hashes)),
            {
                STEP4_TCONTROL_FILENAME,
                STEP4_RETRY03_SELF_ENERGY_FILENAME,
                STEP4_RETRY03_SCRIPT_FILENAME,
            },
        )

    def test_step4_retry03_te_dat_conflict_stops_before_writes_or_sbatch(self):
        (
            project,
            structure,
            surface,
            plan,
            evidence,
            attempt02_tcontrol,
            files,
        ) = _reader_compatible_retry_project()
        files["TE.dat"] = b"ambiguous existing transmission\n"
        directory, _manifest_path = _install(self.remote, project, files)

        with self.assertRaises(Step4ExplicitRetryConflictError):
            self.service.retry_step4_with_reader_compatible_self_energy(
                Step4ReaderCompatibleRetryRequest(
                    PROFILE,
                    project,
                    structure,
                    evidence,
                    surface,
                    plan,
                    attempt02_tcontrol,
                )
            )

        self.assertFalse(
            any(operation[0] == "write" for operation in self.remote.operations)
        )
        self.assertNotIn(
            directory + "/" + STEP4_ATTEMPT02_TCONTROL_FILENAME,
            self.remote.files,
        )
        self.assertFalse(any("--parsable" in item for item in self.remote.commands))

    def test_step4_retry03_unknown_dispatch_is_recorded_and_not_retried(self):
        (
            project,
            structure,
            surface,
            plan,
            evidence,
            attempt02_tcontrol,
            files,
        ) = _reader_compatible_retry_project()
        _directory, manifest_path = _install(self.remote, project, files)
        self.remote.sbatch_error = RemoteCommandOutcomeUnknown("lost after dispatch")

        with self.assertRaises(SubmissionOutcomeUnknown):
            self.service.retry_step4_with_reader_compatible_self_energy(
                Step4ReaderCompatibleRetryRequest(
                    PROFILE,
                    project,
                    structure,
                    evidence,
                    surface,
                    plan,
                    attempt02_tcontrol,
                )
            )

        self.assertEqual(sum("--parsable" in item for item in self.remote.commands), 1)
        step4 = parse_project_manifest(self.remote.files[manifest_path]).steps[3]
        self.assertIs(step4.state, ProjectStepState.UNKNOWN)
        self.assertIsNone(step4.job_id)
        self.assertEqual(len(step4.attempts), 2)
        self.assertEqual(step4.attempts[1].job_id, "42002")
        self.assertEqual(step4.submit_script_filename, STEP4_RETRY03_SCRIPT_FILENAME)


if __name__ == "__main__":
    unittest.main()
