"""Offline scenario tests for ORCA submission, recovery, and cancellation."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_recovery import OrcaRecoveryService
from moltage.app.orca_submission import (
    OrcaFrequencySubmissionRequest,
    OrcaOptimizationSubmissionRequest,
    OrcaSubmissionError,
    OrcaSubmissionOutcomeUnknown,
    OrcaSubmissionService,
)
from moltage.app.project_geometry import (
    ProjectGeometryViewKind,
    ProjectGeometryViewRequest,
    ProjectGeometryViewService,
)
from moltage.app.project_presentation import (
    StepIndicatorKind,
    project_presentation_record,
)
from moltage.app.project_recovery import ProjectRecoveryService
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
    append_orca_wbl_step,
    remote_step_directory,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    LsfResourceRequirementMode,
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    SlurmCommandMode,
    SlurmExecutionPreset,
)
from moltage.domain.structure import Atom, MolecularStructure
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaVersionEvidence,
    OrcaVersionFamily,
)
from moltage.orca.evidence import (
    OrcaFrequencyCompletion,
    OrcaImaginaryModeClassification,
)
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings
from moltage.orca.wbl import (
    WBL_MODEL_CLASSIFICATION,
    WBL_MODEL_ID,
    OrcaWblContactSettings,
    OrcaWblResultEvidence,
    OrcaWblSettings,
    WblContactSubspaceMode,
    WblLinkerKind,
    WblParameterStatus,
)
from moltage.remote.executor import RemoteCommandOutcomeUnknown, RemoteCommandResult
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from phase2b1_test_support import profile
from test_project_submission import FixedConnectionService, MemoryRemoteExecutor


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROFILE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ORCA = "/apps/example/orca-6.1/orca"
LSF_BIN = "/apps/example/lsf/bin"
LSF_CONF = "/apps/example/lsf/conf"
LSF_LIB = "/apps/example/lsf/lib"
LSF_SERVER = "/apps/example/lsf/etc"


def water() -> MolecularStructure:
    return MolecularStructure(
        (
            Atom(0, "O", 0.0, 0.0, 0.0),
            Atom(1, "H", 0.757, 0.586, 0.0),
            Atom(2, "H", -0.757, 0.586, 0.0),
        ),
        "synthetic water",
    )


def optimized_water() -> MolecularStructure:
    return MolecularStructure(
        (
            Atom(0, "O", 0.0, 0.0, 0.02),
            Atom(1, "H", 0.75, 0.58, 0.0),
            Atom(2, "H", -0.75, 0.58, 0.0),
        ),
        "synthetic optimized water",
    )


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


def settings() -> OrcaOptimizationSettings:
    return OrcaOptimizationSettings(
        method=OrcaMethod.PBE0,
        basis=OrcaBasis.DEF2_TZVP,
        process_count=4,
        version_family=OrcaVersionFamily.V6_1,
        scheduler_nodes=1,
        runtime_minutes=90,
        scheduler_memory_gb=8,
    )


def configured_profile(kind: SchedulerKind = SchedulerKind.SLURM):
    if kind is SchedulerKind.SLURM:
        preset = SlurmExecutionPreset(
            nodes=1,
            ntasks=4,
            cpus_per_task=1,
            runtime_minutes=90,
            memory_gb=8,
            slurm_command_mode=SlurmCommandMode.MANUAL,
            slurm_bin_directory="/usr/bin",
            slurm_output_filename="",
        )
    else:
        preset = SlurmExecutionPreset(
            nodes=1,
            ntasks=4,
            cpus_per_task=1,
            runtime_minutes=90,
            memory_gb=8,
            unset_slurm_export_env=False,
            scheduler_kind=SchedulerKind.LSF,
            slurm_command_mode=SlurmCommandMode.MANUAL,
            slurm_bin_directory=LSF_BIN,
            lsf_env_directory=LSF_CONF,
            lsf_library_directory=LSF_LIB,
            lsf_server_directory=LSF_SERVER,
            lsf_resource_requirement_mode=LsfResourceRequirementMode.SITE_DEFAULT,
            slurm_output_filename="",
        )
    return replace(
        profile(profile_id=PROFILE_ID),
        execution_preset=preset,
        orca_runtime=runtime(),
    )


class OrcaRemoteExecutor(MemoryRemoteExecutor):
    """One deterministic in-memory SSH/scheduler/remote-filesystem boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.submit_count = 0
        self.submit_outcome_unknown = False
        self.submit_receipt_is_ambiguous = False
        self.orca_version = "6.1.2"
        self.orca_resolved_path = ORCA
        self.squeue_stdout = b""
        self.sacct_stdout = b""

    def execute(self, command: str) -> RemoteCommandResult:
        self.operations.append(("execute", command))
        if "__MOLTAGE_ORCA_CONFIGURED__=" in command:
            return RemoteCommandResult(
                0,
                (
                    f"__MOLTAGE_ORCA_CONFIGURED__={ORCA}\n"
                    f"__MOLTAGE_ORCA_PATH__={self.orca_resolved_path}\n"
                ).encode(),
                b"",
            )
        if ORCA in command and "--version" in command:
            return RemoteCommandResult(
                0,
                f"Program Version {self.orca_version}\n".encode(),
                b"",
            )
        if command == "/usr/bin/sbatch --version":
            return RemoteCommandResult(0, b"slurm 24.11.3\n", b"")
        if command == (
            "test -x /usr/bin/squeue && test -x /usr/bin/sacct && "
            "test -x /usr/bin/scancel"
        ):
            return RemoteCommandResult(0, b"", b"")
        if all(name in command for name in ("bsub", "bjobs", "bhist", "bkill", "lsid")):
            return RemoteCommandResult(
                0,
                (
                    "IBM Spectrum LSF synthetic-test\n"
                    f"__MOLTAGE_LSF_ENVDIR__={LSF_CONF}|{LSF_LIB}|{LSF_SERVER}\n"
                ).encode(),
                b"",
            )
        if "--parsable submit.orca" in command or "< submit.orca" in command:
            self.submit_count += 1
            if self.submit_outcome_unknown:
                raise RemoteCommandOutcomeUnknown("synthetic transport ambiguity")
            if self.submit_receipt_is_ambiguous:
                return RemoteCommandResult(0, b"not-a-job-id\n", b"")
            job_id = str(90000 + self.submit_count)
            receipt = (
                f"Job <{job_id}> is submitted to queue <normal>.\n"
                if "< submit.orca" in command
                else f"{job_id};synthetic-cluster\n"
            )
            return RemoteCommandResult(0, receipt.encode(), b"")
        if command.startswith("/usr/bin/squeue "):
            return RemoteCommandResult(0, self.squeue_stdout, b"")
        if command.startswith("/usr/bin/sacct "):
            return RemoteCommandResult(0, self.sacct_stdout, b"")
        if command.startswith("/usr/bin/scancel "):
            return RemoteCommandResult(0, b"", b"")
        if f"{LSF_BIN}/bjobs -a " in command:
            return RemoteCommandResult(0, self.squeue_stdout, b"")
        if f"{LSF_BIN}/bhist -n " in command:
            return RemoteCommandResult(0, b"", b"")
        if f"{LSF_BIN}/bkill " in command:
            return RemoteCommandResult(0, b"Job <90001> is being terminated\n", b"")
        raise AssertionError(f"unexpected remote command: {command}")


def install_optimization_results(
    remote: OrcaRemoteExecutor,
    project,
    *,
    gbw: bool = True,
    converged: bool = True,
    ordered_elements: bool = True,
) -> None:
    root = project.remote_project_path
    output = (
        "THE OPTIMIZATION HAS CONVERGED\nORCA TERMINATED NORMALLY\n"
        if converged
        else "MAXIMUM NUMBER OF OPTIMIZATION CYCLES REACHED\nORCA TERMINATED NORMALLY\n"
    )
    structure = optimized_water()
    records = [
        f"{atom.element} {atom.x} {atom.y} {atom.z}" for atom in structure
    ]
    if not ordered_elements:
        records[0] = records[0].replace("O ", "H ", 1)
    remote.files[f"{root}/orca_opt.out"] = output.encode()
    remote.files[f"{root}/orca_opt.xyz"] = (
        "3\nsynthetic optimized geometry\n" + "\n".join(records) + "\n"
    ).encode()
    remote.files[f"{root}/orca_opt_trj.xyz"] = b"synthetic trajectory evidence\n"
    if gbw:
        remote.files[f"{root}/orca_opt.gbw"] = b"synthetic binary wavefunction evidence"


class OrcaSubmissionRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.index = LocalProjectIndexRepository(
            Path(self.temporary.name) / "known_projects.json"
        )
        self.remote = OrcaRemoteExecutor()

    def submission_service(self) -> OrcaSubmissionService:
        return OrcaSubmissionService(
            FixedConnectionService(self.remote),
            self.index,
            now_factory=lambda: NOW,
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=lambda: "synthetic-temp",
        )

    def submit_optimization(self, kind=SchedulerKind.SLURM):
        return self.submission_service().submit_optimization(
            OrcaOptimizationSubmissionRequest(
                configured_profile(kind),
                "SyntheticOrca",
                "synthetic.xyz",
                water(),
                settings(),
                supplied_password="synthetic-password",
                project_id=PROJECT_ID,
            )
        )

    def recovery_service(self) -> ProjectRecoveryService:
        return ProjectRecoveryService(
            FixedConnectionService(self.remote),
            self.index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-refresh",
            covalent_radii_loader=lambda: {"O": 0.66, "H": 0.31},
        )

    def test_slurm_submission_is_atomic_direct_and_persists_one_job(self):
        request_profile = replace(
            configured_profile(),
            execution_preset=replace(
                configured_profile().execution_preset,
                slurm_output_filename="aims.dft.out",
            ),
        )
        result = self.submission_service().submit_optimization(
            OrcaOptimizationSubmissionRequest(
                request_profile,
                "SyntheticOrca",
                "synthetic.xyz",
                water(),
                settings(),
                supplied_password="synthetic-password",
                project_id=PROJECT_ID,
            )
        )

        self.assertEqual(result.job_id, "90001")
        self.assertIs(result.project.workflow_kind, CalculationWorkflowKind.ORCA)
        self.assertEqual(result.step.state, ProjectStepState.QUEUED)
        self.assertEqual(self.remote.submit_count, 1)
        script = self.remote.files[
            f"{result.project.remote_project_path}/submit.orca.sh"
        ].decode()
        self.assertIn("#SBATCH --output=orca_opt.scheduler.out", script)
        self.assertIn(f"exec {ORCA} orca_opt.inp > orca_opt.out 2>&1", script)
        self.assertNotIn("aims.dft.out", script)
        self.assertNotIn('dirname -- "$0"', script)
        self.assertNotIn("srun ", script)
        self.assertNotIn("mpirun", script)
        self.assertEqual(result.step.slurm_output_filename, "orca_opt.scheduler.out")
        submit_commands = tuple(
            operation[1]
            for operation in self.remote.operations
            if operation[0] == "execute" and "--parsable submit.orca.sh" in operation[1]
        )
        self.assertEqual(len(submit_commands), 1)
        self.assertTrue(
            submit_commands[0].startswith(
                f"cd {result.project.remote_project_path} && /usr/bin/sbatch "
            )
        )
        self.assertEqual(
            dict(result.step.input_hashes).keys(),
            {"orca_opt.inp", "submit.orca.sh"},
        )
        references = self.index.load()
        self.assertEqual(len(references), 1)
        self.assertIs(references[0].workflow_kind, CalculationWorkflowKind.ORCA)

    def test_lsf_submission_uses_manual_scheduler_and_direct_driver(self):
        result = self.submit_optimization(SchedulerKind.LSF)

        self.assertEqual(result.job_id, "90001")
        self.assertIs(result.step.scheduler_kind, SchedulerKind.LSF)
        self.assertEqual(result.step.slurm_output_filename, "orca_opt.scheduler.out")
        script = self.remote.files[
            f"{result.project.remote_project_path}/submit.orca.sh"
        ].decode()
        self.assertIn("#BSUB", script)
        self.assertIn(f"exec {ORCA} orca_opt.inp > orca_opt.out 2>&1", script)
        self.assertEqual(self.remote.submit_count, 1)

    def test_running_orca_geometry_and_recovery_never_read_fhi_aims_inputs(self):
        submitted = self.submit_optimization().project
        self.remote.squeue_stdout = b"90001|RUNNING\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        result = ProjectGeometryViewService(
            FixedConnectionService(self.remote)
        ).load(
            ProjectGeometryViewRequest(
                configured_profile(),
                snapshot.project,
                ProjectStepKind.ORCA_OPTIMIZATION,
                ProjectGeometryViewKind.INPUT,
                supplied_password="synthetic-password",
            )
        )

        self.assertEqual(snapshot.submitted_structure.atoms, water().atoms)
        self.assertEqual(result.source_filename, "orca_opt.inp")
        self.assertEqual(result.structure.atoms, water().atoms)
        self.assertEqual(result.recovery_snapshot.project, snapshot.project)
        self.assertEqual(result.recovery_profile, configured_profile())
        read_paths = tuple(
            item[1]
            for item in self.remote.operations
            if item[0] == "read"
        )
        self.assertTrue(any(path.endswith("/orca_opt.inp") for path in read_paths))
        self.assertFalse(any(path.endswith("/control.in") for path in read_paths))
        self.assertFalse(any(path.endswith("/geometry.in") for path in read_paths))

    def test_resubmission_cancels_exact_active_job_before_new_submission(self):
        first = self.submit_optimization()
        self.remote.squeue_stdout = b"90001|RUNNING\n"
        retry_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

        retried = self.submission_service().submit_optimization(
            OrcaOptimizationSubmissionRequest(
                configured_profile(),
                "SyntheticOrcaRetry",
                "synthetic.xyz",
                water(),
                replace(settings(), runtime_minutes=120),
                supplied_password="synthetic-password",
                project_id=retry_id,
                resubmission_source_project=first.project,
            )
        )

        self.assertEqual(retried.job_id, "90002")
        self.assertEqual(retried.project.project_id, retry_id)
        commands = tuple(
            item[1]
            for item in self.remote.operations
            if item[0] == "execute"
        )
        cancel_index = commands.index("/usr/bin/scancel 90001")
        second_submit_index = next(
            index
            for index, command in enumerate(commands)
            if index > cancel_index and "--parsable submit.orca.sh" in command
        )
        self.assertLess(cancel_index, second_submit_index)
        source_manifest = parse_project_manifest(
            self.remote.files[
                f"{first.project.remote_project_path}/.moltage/project.json"
            ]
        )
        self.assertIs(source_manifest.steps[0].state, ProjectStepState.UNKNOWN)
        self.assertEqual(
            source_manifest.steps[0].scheduler_state,
            "CANCEL_REQUESTED",
        )

    def test_resubmission_does_not_submit_when_prior_job_is_unresolved(self):
        first = self.submit_optimization()

        with self.assertRaisesRegex(
            OrcaSubmissionError,
            "not authoritative",
        ):
            self.submission_service().submit_optimization(
                OrcaOptimizationSubmissionRequest(
                    configured_profile(),
                    "SyntheticOrcaRetry",
                    "synthetic.xyz",
                    water(),
                    settings(),
                    supplied_password="synthetic-password",
                    project_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
                    resubmission_source_project=first.project,
                )
            )

        self.assertEqual(self.remote.submit_count, 1)

    def test_lsf_resubmission_cancels_with_configured_client_environment(self):
        first = self.submit_optimization(SchedulerKind.LSF)
        self.remote.squeue_stdout = b"90001|RUN\n"

        result = self.submission_service().submit_optimization(
            OrcaOptimizationSubmissionRequest(
                configured_profile(SchedulerKind.LSF),
                "SyntheticOrcaLsfRetry",
                "synthetic.xyz",
                water(),
                settings(),
                supplied_password="synthetic-password",
                project_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
                resubmission_source_project=first.project,
            )
        )

        self.assertEqual(result.job_id, "90002")
        commands = [item[1] for item in self.remote.operations if item[0] == "execute"]
        cancel = next(command for command in commands if f"{LSF_BIN}/bkill 90001" in command)
        self.assertIn(LSF_CONF, cancel)
        self.assertIn(LSF_LIB, cancel)
        self.assertIn(LSF_SERVER, cancel)
        self.assertLess(
            commands.index(cancel),
            next(
                index
                for index, command in enumerate(commands)
                if index > commands.index(cancel) and "< submit.orca.sh" in command
            ),
        )

    def test_unknown_cancellation_outcome_blocks_replacement_submission(self):
        class UnknownCancellationExecutor(OrcaRemoteExecutor):
            def execute(self, command):
                if command.startswith("/usr/bin/scancel "):
                    self.operations.append(("execute", command))
                    raise RemoteCommandOutcomeUnknown("synthetic cancellation ambiguity")
                return super().execute(command)

        self.remote = UnknownCancellationExecutor()
        first = self.submit_optimization()
        self.remote.squeue_stdout = b"90001|RUNNING\n"

        with self.assertRaisesRegex(OrcaSubmissionOutcomeUnknown, "new job was not submitted"):
            self.submission_service().submit_optimization(
                OrcaOptimizationSubmissionRequest(
                    configured_profile(),
                    "SyntheticOrcaUnknownRetry",
                    "synthetic.xyz",
                    water(),
                    settings(),
                    supplied_password="synthetic-password",
                    project_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
                    resubmission_source_project=first.project,
                )
            )

        self.assertEqual(self.remote.submit_count, 1)

    def test_stale_runtime_fails_before_any_remote_mutation(self):
        self.remote.orca_version = "6.1.3"

        with self.assertRaisesRegex(OrcaSubmissionError, "version evidence changed"):
            self.submit_optimization()

        mutations = tuple(
            item for item in self.remote.operations if item[0] in {"mkdir", "write", "rename"}
        )
        self.assertEqual(mutations, ())
        self.assertEqual(self.remote.submit_count, 0)

    def test_version_unverified_runtime_uses_only_version_common_settings(self):
        unverified_runtime = replace(
            runtime(),
            version_evidence=OrcaVersionEvidence(
                "synthetic unparseable version output",
                None,
                None,
                "synthetic validation",
            ),
        )
        request_profile = replace(
            configured_profile(),
            orca_runtime=unverified_runtime,
        )
        request_settings = replace(settings(), version_family=None)
        self.remote.orca_version = ""

        result = self.submission_service().submit_optimization(
            OrcaOptimizationSubmissionRequest(
                request_profile,
                "SyntheticOrca",
                "synthetic.xyz",
                water(),
                request_settings,
                supplied_password="synthetic-password",
                project_id=PROJECT_ID,
            )
        )

        self.assertEqual(result.job_id, "90001")
        self.assertIsNone(result.step.orca_runtime.version_evidence.version_family)

    def test_ambiguous_submission_is_unknown_and_never_retried(self):
        self.remote.submit_receipt_is_ambiguous = True

        with self.assertRaises(OrcaSubmissionOutcomeUnknown):
            self.submit_optimization()

        self.assertEqual(self.remote.submit_count, 1)
        project_root = next(
            path for path in self.remote.directories if path.endswith("SyntheticOrca.20300102")
        )
        manifest_path = f"{project_root}/.moltage/project.json"
        from moltage.remote.project_manifest import parse_project_manifest

        persisted = parse_project_manifest(self.remote.files[manifest_path])
        self.assertIs(persisted.steps[0].state, ProjectStepState.UNKNOWN)
        self.assertIsNone(persisted.steps[0].job_id)

    def test_optimization_recovery_separates_success_geometry_and_gbw_readiness(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted, gbw=False)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )

        step = snapshot.active_step
        self.assertIs(step.state, ProjectStepState.SUCCEEDED)
        self.assertTrue(step.orca_optimization_result.succeeded)
        self.assertFalse(step.orca_optimization_result.wbl_input_ready)
        self.assertEqual(snapshot.optimized_structure.atoms, optimized_water().atoms)
        self.assertTrue(snapshot.orca_trajectory_available)
        self.assertIn("not ready", snapshot.status_message)

    def test_optimization_recovery_rejects_changed_submitted_input_bytes(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        input_path = f"{submitted.remote_project_path}/orca_opt.inp"
        self.remote.files[input_path] += b"\n"
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SCHEDULER_COMPLETED)
        self.assertIn("hash mismatch", snapshot.active_step.last_error)
        self.assertIsNone(snapshot.optimized_structure)

    def test_incomplete_terminal_result_keeps_input_geometry_available(self):
        submitted = self.submit_optimization().project
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.SCHEDULER_COMPLETED)
        self.assertIsNone(snapshot.optimized_structure)
        self.assertEqual(snapshot.submitted_structure.atoms, water().atoms)
        self.assertIsNotNone(snapshot.connectivity)

    def test_optimized_geometry_view_uses_only_orca_hash_bound_artifacts(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        operations_before = len(self.remote.operations)

        result = ProjectGeometryViewService(
            FixedConnectionService(self.remote)
        ).load(
            ProjectGeometryViewRequest(
                configured_profile(),
                snapshot.project,
                ProjectStepKind.ORCA_OPTIMIZATION,
                ProjectGeometryViewKind.OUTPUT,
                supplied_password="synthetic-password",
            )
        )

        self.assertEqual(result.source_filename, "orca_opt.xyz")
        self.assertEqual(result.structure.atoms, optimized_water().atoms)
        self.assertEqual(
            result.recovery_snapshot.optimized_structure,
            result.structure,
        )
        reads = tuple(
            item[1]
            for item in self.remote.operations[operations_before:]
            if item[0] == "read"
        )
        self.assertTrue(any(path.endswith("/orca_opt.inp") for path in reads))
        self.assertTrue(any(path.endswith("/orca_opt.xyz") for path in reads))
        self.assertFalse(any(path.endswith("/control.in") for path in reads))
        self.assertFalse(any(path.endswith("/geometry.in") for path in reads))
        self.assertFalse(
            any(
                item[0] in {"write", "rename", "execute"}
                for item in self.remote.operations[operations_before:]
            )
        )

    def test_nonconvergence_and_element_mismatch_do_not_report_success(self):
        for converged, ordered_elements, expected_state in (
            (False, True, ProjectStepState.FAILED),
            (True, False, ProjectStepState.SCHEDULER_COMPLETED),
        ):
            with self.subTest(
                converged=converged,
                ordered_elements=ordered_elements,
            ):
                temporary = TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                self.index = LocalProjectIndexRepository(
                    Path(temporary.name) / "known_projects.json"
                )
                self.remote = OrcaRemoteExecutor()
                submitted = self.submit_optimization().project
                install_optimization_results(
                    self.remote,
                    submitted,
                    converged=converged,
                    ordered_elements=ordered_elements,
                )
                self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"

                snapshot = self.recovery_service().refresh_project(
                    configured_profile(),
                    submitted.remote_project_path,
                    supplied_password="synthetic-password",
                )

                self.assertIs(snapshot.active_step.state, expected_state)
                self.assertFalse(snapshot.active_step.orca_optimization_result.succeeded)

    def test_frequency_is_independent_and_preserves_optimization_success(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
        optimized_snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        frequency_settings = OrcaFrequencySettings(
            settings(),
            settings().scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
            process_count=2,
            scheduler_nodes=1,
            runtime_minutes=120,
            scheduler_memory_gb=8,
        )

        result = self.submission_service().submit_frequency(
            OrcaFrequencySubmissionRequest(
                configured_profile(),
                optimized_snapshot.project,
                optimized_snapshot.optimized_structure,
                frequency_settings,
                supplied_password="synthetic-password",
            )
        )

        self.assertEqual(result.job_id, "90002")
        self.assertIs(result.step.kind, ProjectStepKind.ORCA_FREQUENCY)
        self.assertEqual(result.step.slurm_output_filename, "orca_freq.scheduler.out")
        frequency_root = remote_step_directory(
            result.project, ProjectStepKind.ORCA_FREQUENCY
        )
        self.remote.files[f"{frequency_root}/orca_freq.out"] = (
            b"VIBRATIONAL FREQUENCIES\n"
            b"0: 0.00 cm-1\n"
            b"1: -12.30 cm-1 ***imaginary mode***\n"
            b"ORCA TERMINATED NORMALLY\n"
        )
        self.remote.files[f"{frequency_root}/orca_freq.hess"] = (
            b"$hessian\n9\n0 1\n0 1.0\n"
            b"$vibrational_frequencies\n2\n0 0.0\n1 -12.3\n"
        )
        self.remote.sacct_stdout = b"90002|COMPLETED|0:0\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            result.project.remote_project_path,
            supplied_password="synthetic-password",
        )

        optimization, frequency = snapshot.project.steps
        self.assertIs(optimization.state, ProjectStepState.SUCCEEDED)
        self.assertTrue(optimization.orca_optimization_result.succeeded)
        self.assertIs(frequency.state, ProjectStepState.SUCCEEDED)
        self.assertIs(
            frequency.orca_frequency_result.completion,
            OrcaFrequencyCompletion.FREQUENCY_COMPLETED,
        )
        self.assertIs(
            frequency.orca_frequency_result.imaginary_classification,
            OrcaImaginaryModeClassification.IMAGINARY_MODES_REPORTED,
        )
        self.assertNotIn("WBL", tuple(step.kind.value for step in snapshot.project.steps))
        presentation = project_presentation_record(
            snapshot,
            server_profile_id=PROFILE_ID,
        )
        self.assertEqual(len(presentation.indicators), 2)
        self.assertIs(
            presentation.indicators[0].step_kind,
            ProjectStepKind.ORCA_OPTIMIZATION,
        )
        self.assertIs(
            presentation.indicators[1].step_kind,
            ProjectStepKind.ORCA_WBL_TRANSMISSION,
        )
        self.assertIs(
            presentation.indicators[1].kind,
            StepIndicatorKind.NOT_STARTED,
        )

    def test_frequency_recovery_keeps_unreadable_persisted_wbl_success(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
        optimized = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        contact = lambda atom_index: OrcaWblContactSettings(
            atom_index,
            WblLinkerKind.SH,
            0.2,
            WblParameterStatus.HYPOTHESIS,
            WblContactSubspaceMode.MANUAL_AO,
            manual_ao_indices=(atom_index,),
        )
        wbl_settings = OrcaWblSettings(
            contact(0),
            contact(2),
            -5.0,
            -2.0,
            2.0,
            0.2,
        )
        gbw_hash = optimized.project.steps[0].orca_optimization_result.gbw_sha256
        self.assertIsNotNone(gbw_hash)
        with_wbl = append_orca_wbl_step(
            optimized.project,
            settings=wbl_settings,
            result=OrcaWblResultEvidence(
                WBL_MODEL_ID,
                WBL_MODEL_CLASSIFICATION,
                gbw_hash,
                "a" * 64,
                (
                    ("orca_wbl_result.json", "b" * 64),
                    ("orca_wbl_transmission.csv", "c" * 64),
                ),
                "/apps/example/orca-6.1/orca_2json",
                0.01,
                0.02,
                0.03,
                ((1, 0.01),),
                ((1, 0.02),),
            ),
            input_hashes=(),
            finished_at=NOW,
        )
        manifest_path = (
            f"{with_wbl.remote_project_path}/.moltage/project.json"
        )
        self.remote.files[manifest_path] = serialize_project_manifest(
            with_wbl
        ).encode("utf-8")
        frequency_settings = OrcaFrequencySettings(
            settings(),
            settings().scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
        )
        submitted_frequency = self.submission_service().submit_frequency(
            OrcaFrequencySubmissionRequest(
                configured_profile(),
                with_wbl,
                optimized.optimized_structure,
                frequency_settings,
                supplied_password="synthetic-password",
            )
        )
        frequency_root = remote_step_directory(
            submitted_frequency.project,
            ProjectStepKind.ORCA_FREQUENCY,
        )
        self.remote.files[f"{frequency_root}/orca_freq.out"] = (
            b"VIBRATIONAL FREQUENCIES\n0: 12.30 cm-1\n"
            b"ORCA TERMINATED NORMALLY\n"
        )
        self.remote.files[f"{frequency_root}/orca_freq.hess"] = (
            b"$hessian\n9\n0 1\n0 1.0\n"
            b"$vibrational_frequencies\n1\n0 12.3\n"
        )
        self.remote.sacct_stdout = b"90002|COMPLETED|0:0\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted_frequency.project.remote_project_path,
            supplied_password="synthetic-password",
        )

        self.assertIs(
            snapshot.active_step_kind,
            ProjectStepKind.ORCA_FREQUENCY,
        )
        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertIs(snapshot.project.steps[1].state, ProjectStepState.SUCCEEDED)
        self.assertIsNone(snapshot.orca_wbl_presentation)
        self.assertFalse(snapshot.can_view_orca_wbl)
        self.assertIn("WBL presentation unavailable", snapshot.status_message)
        self.assertIn(
            "Missing required ORCA WBL artifact",
            snapshot.status_message,
        )
        presentation = project_presentation_record(
            snapshot,
            server_profile_id=PROFILE_ID,
        )
        self.assertIs(
            presentation.indicators[1].kind,
            StepIndicatorKind.SUCCEEDED,
        )

    def test_frequency_rejects_source_setting_mismatch_and_stale_runtime(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
        optimized_snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        different_source = replace(settings(), method=OrcaMethod.PBE)
        mismatched = OrcaFrequencySettings(
            different_source,
            different_source.scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
        )
        with self.assertRaisesRegex(OrcaSubmissionError, "differ from the source"):
            self.submission_service().submit_frequency(
                OrcaFrequencySubmissionRequest(
                    configured_profile(),
                    optimized_snapshot.project,
                    optimized_snapshot.optimized_structure,
                    mismatched,
                    supplied_password="synthetic-password",
                )
            )

        valid = OrcaFrequencySettings(
            settings(),
            settings().scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
        )
        self.remote.orca_version = "6.1.3"
        with self.assertRaisesRegex(OrcaSubmissionError, "version evidence changed"):
            self.submission_service().submit_frequency(
                OrcaFrequencySubmissionRequest(
                    configured_profile(),
                    optimized_snapshot.project,
                    optimized_snapshot.optimized_structure,
                    valid,
                    supplied_password="synthetic-password",
                )
            )
        self.assertNotIn(
            f"{optimized_snapshot.project.remote_project_path}/frequency",
            self.remote.directories,
        )

    def test_frequency_scheduler_timeout_preserves_optimization_success(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
        optimized_snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        frequency_settings = OrcaFrequencySettings(
            settings(),
            settings().scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
        )
        submitted_frequency = self.submission_service().submit_frequency(
            OrcaFrequencySubmissionRequest(
                configured_profile(),
                optimized_snapshot.project,
                optimized_snapshot.optimized_structure,
                frequency_settings,
                supplied_password="synthetic-password",
            )
        )
        self.remote.sacct_stdout = b"90002|TIMEOUT|0:15\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted_frequency.project.remote_project_path,
            supplied_password="synthetic-password",
        )

        optimization, frequency = snapshot.project.steps
        self.assertIs(optimization.state, ProjectStepState.SUCCEEDED)
        self.assertTrue(optimization.orca_optimization_result.succeeded)
        self.assertIs(frequency.state, ProjectStepState.FAILED)
        self.assertEqual(frequency.scheduler_state, "TIMEOUT")

    def test_frequency_program_evidence_stays_unverified_after_scheduler_success(self):
        submitted = self.submit_optimization().project
        install_optimization_results(self.remote, submitted)
        self.remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
        optimized_snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        frequency_settings = OrcaFrequencySettings(
            settings(),
            settings().scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
        )
        submitted_frequency = self.submission_service().submit_frequency(
            OrcaFrequencySubmissionRequest(
                configured_profile(),
                optimized_snapshot.project,
                optimized_snapshot.optimized_structure,
                frequency_settings,
                supplied_password="synthetic-password",
            )
        )
        frequency_root = remote_step_directory(
            submitted_frequency.project,
            ProjectStepKind.ORCA_FREQUENCY,
        )
        self.remote.files[f"{frequency_root}/orca_freq.out"] = (
            b"VIBRATIONAL FREQUENCIES\n0: 12.30 cm-1\n"
        )
        self.remote.files[f"{frequency_root}/orca_freq.hess"] = (
            b"$hessian\n9\n0 1\n0 1.0\n"
            b"$vibrational_frequencies\n1\n0 12.3\n"
        )
        self.remote.sacct_stdout = b"90002|COMPLETED|0:0\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted_frequency.project.remote_project_path,
            supplied_password="synthetic-password",
        )

        optimization, frequency = snapshot.project.steps
        self.assertIs(optimization.state, ProjectStepState.SUCCEEDED)
        self.assertIs(frequency.state, ProjectStepState.SCHEDULER_COMPLETED)
        self.assertIs(
            frequency.orca_frequency_result.completion,
            OrcaFrequencyCompletion.UNVERIFIED,
        )
        self.assertIn("normal termination", frequency.last_error)

    def test_cancel_then_cancelled_refresh_has_distinct_presentation(self):
        submitted = self.submit_optimization().project
        cancellation = OrcaRecoveryService(
            FixedConnectionService(self.remote),
            self.index,
            now_factory=lambda: NOW,
        ).cancel_active_job(
            configured_profile(),
            submitted,
            supplied_password="synthetic-password",
        )
        self.assertIs(cancellation.steps[0].state, ProjectStepState.UNKNOWN)
        self.assertEqual(cancellation.steps[0].scheduler_state, "CANCEL_REQUESTED")
        self.remote.sacct_stdout = b"90001|CANCELLED|0:15\n"

        snapshot = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        record = project_presentation_record(snapshot, server_profile_id=PROFILE_ID)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.scheduler_state, "CANCELLED")
        self.assertIs(record.indicators[0].kind, StepIndicatorKind.CANCELLED)
        self.assertIn("scheduler CANCELLED", record.indicators[0].tooltip)

    def test_running_and_timeout_scheduler_states_remain_explicit(self):
        submitted = self.submit_optimization().project
        self.remote.squeue_stdout = b"90001|RUNNING\n"
        running = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        self.assertIs(running.active_step.state, ProjectStepState.RUNNING)
        self.assertEqual(running.active_step.scheduler_state, "RUNNING")

        self.remote.squeue_stdout = b""
        self.remote.sacct_stdout = b"90001|TIMEOUT|0:15\n"
        timed_out = self.recovery_service().refresh_project(
            configured_profile(),
            submitted.remote_project_path,
            supplied_password="synthetic-password",
        )
        self.assertIs(timed_out.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(timed_out.active_step.scheduler_state, "TIMEOUT")
        self.assertIn("TIMEOUT", timed_out.status_message)


if __name__ == "__main__":
    unittest.main()
