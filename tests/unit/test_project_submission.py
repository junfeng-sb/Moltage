from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import Path, PurePosixPath
import re
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings, SpeciesAccuracy
from moltage.aims.species_library import species_default_filename
from moltage.aims.transport_convergence_bundle import (
    TransportConvergenceInputPlan,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.connection_service import ServerConnectionService
from moltage.app.imported_start import (
    ImportedTransportStartContext,
    imported_contact_au_context,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_planning import create_initial_project
from moltage.app.project_submission import (
    MissingClusterSettingsError,
    NewProjectSubmissionRequest,
    ProjectAllocationError,
    ProjectChecksumError,
    ProjectPreparationError,
    ProjectSubmissionService,
    SbatchRejectedError,
    SUBMISSION_LIFECYCLE_LOGGER_NAME,
    SubmissionOutcomeUnknown,
    allocate_remote_project_directory,
)
from moltage.app.server_profiles import ServerProfileRepository
from moltage.domain.calculation_project import (
    ProjectRestartProvenance,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.server_profile import SlurmCommandMode
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
    RemoteDirectoryEntry,
    RemoteExecutorError,
    RemotePathAlreadyExistsError,
    RemotePathNotFoundError,
    RemotePathStat,
)
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from moltage.remote.slurm_discovery import (
    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
    LOGIN_SHELL_DISCOVERY_COMMAND,
    SlurmConfigurationError,
    SlurmDiscoveryError,
)
from phase2b1_test_support import MemorySecretStore, profile
from species_test_support import (
    TEST_SPECIES_ROOT,
    synthetic_species_library,
    synthetic_species_text,
)
from test_imported_start import _contact_state
from synthetic_test_data import (
    SYNTHETIC_FHI_EXECUTABLE_NAME,
    SYNTHETIC_PROFILE_NAME,
    SYNTHETIC_REMOTE_ROOT,
)


CREATED_AT = datetime(
    2030,
    1,
    2,
    12,
    0,
    tzinfo=timezone(timedelta(hours=2)),
)
UPDATED_AT = datetime(
    2030,
    1,
    2,
    12,
    1,
    tzinfo=timezone(timedelta(hours=2)),
)
PROJECT_ID = UUID("1a2b3c4d-1111-4111-8111-111111111111")
PASSWORD = "SYNTHETIC_PASSWORD_FOR_OFFLINE_TESTS"


def _synthetic_remote_species(path: str) -> bytes | None:
    candidate = PurePosixPath(path)
    if str(candidate.parent.parent) != TEST_SPECIES_ROOT:
        return None
    try:
        accuracy = SpeciesAccuracy(candidate.parent.name)
    except ValueError:
        return None
    match = re.fullmatch(r"[0-9]{2}_([A-Z][a-z]?)_default", candidate.name)
    if match is None:
        return None
    element = match.group(1)
    try:
        expected = species_default_filename(element, accuracy)
    except ValueError:
        return None
    if candidate.name != expected:
        return None
    return synthetic_species_text(element, accuracy).encode("utf-8")


class MemoryRemoteExecutor:
    def __init__(self) -> None:
        self.directories = {
            "/",
            "/srv",
            "/srv/moltage-test",
            SYNTHETIC_REMOTE_ROOT,
        }
        self.files: dict[str, bytes] = {}
        self.operations: list[tuple] = []
        self.manifest_history: list[bytes] = []
        self.command_result = RemoteCommandResult(0, b"12345;clusterA\n", b"")
        self.execute_error: Exception | None = None
        self.discovery_path: str | None = "/usr/bin/sbatch"
        self.discovery_version_result = RemoteCommandResult(
            0,
            b"slurm 24.11.3\n",
            b"",
        )
        self.mkdir_errors: dict[str, Exception] = {}
        self.corrupt_read_path: str | None = None
        self.missing_species_paths: set[str] = set()
        self.connected_request = None
        self.closed = False

    def connect(self, request) -> None:
        self.connected_request = request
        self.operations.append(("connect", request.profile.profile_id))

    def close(self) -> None:
        self.closed = True
        self.operations.append(("close",))

    def stat(self, path: str) -> RemotePathStat:
        self.operations.append(("stat", path))
        if path in self.directories:
            return RemotePathStat(True)
        if path in self.files:
            return RemotePathStat(False, len(self.files[path]))
        if (
            path not in self.missing_species_paths
            and (species := _synthetic_remote_species(path)) is not None
        ):
            return RemotePathStat(False, len(species))
        raise RemotePathNotFoundError(f"missing: {path}")

    def list_directory(self, path: str) -> tuple[RemoteDirectoryEntry, ...]:
        if path not in self.directories:
            raise RemotePathNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        entries = {}
        for candidate in self.directories | set(self.files):
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix) :]
                if remainder and "/" not in remainder:
                    entries[remainder] = candidate in self.directories
        return tuple(
            RemoteDirectoryEntry(name, is_directory)
            for name, is_directory in sorted(entries.items())
        )

    def mkdir(self, path: str) -> None:
        self.operations.append(("mkdir", path))
        if path in self.mkdir_errors:
            raise self.mkdir_errors[path]
        if path in self.directories or path in self.files:
            raise RemotePathAlreadyExistsError(path)
        parent = str(PurePosixPath(path).parent)
        if parent not in self.directories:
            raise RemotePathNotFoundError(parent)
        self.directories.add(path)

    def read_bytes(self, path: str) -> bytes:
        self.operations.append(("read", path))
        if path in self.files:
            data = self.files[path]
        else:
            data = (
                None
                if path in self.missing_species_paths
                else _synthetic_remote_species(path)
            )
            if data is None:
                raise RemotePathNotFoundError(path)
        if path == self.corrupt_read_path:
            return data + b"!"
        return data

    def write_bytes(self, path: str, data: bytes) -> None:
        self.operations.append(("write", path, data))
        parent = str(PurePosixPath(path).parent)
        if parent not in self.directories:
            raise RemotePathNotFoundError(parent)
        self.files[path] = data

    def rename(self, source: str, destination: str) -> None:
        self.operations.append(("rename", source, destination))
        if source not in self.files:
            raise RemotePathNotFoundError(source)
        self.files[destination] = self.files.pop(source)
        if destination.endswith("/.moltage/project.json"):
            self.manifest_history.append(self.files[destination])

    def execute(self, command: str) -> RemoteCommandResult:
        self.operations.append(("execute", command))
        if command == CURRENT_ENVIRONMENT_DISCOVERY_COMMAND:
            if self.discovery_path is None:
                return RemoteCommandResult(1, b"", b"")
            return RemoteCommandResult(
                0,
                (self.discovery_path + "\n").encode("utf-8"),
                b"",
            )
        if command == LOGIN_SHELL_DISCOVERY_COMMAND:
            return RemoteCommandResult(1, b"", b"")
        if (
            self.discovery_path is not None
            and command == f"{self.discovery_path} --version"
        ):
            return self.discovery_version_result
        if command.endswith(" --parsable submit.sh"):
            if self.execute_error is not None:
                raise self.execute_error
            return self.command_result
        raise AssertionError(f"unexpected remote command: {command}")


class FixedConnectionService(ServerConnectionService):
    def __init__(self, executor: MemoryRemoteExecutor) -> None:
        super().__init__(MemorySecretStore(), lambda: executor)


class Clock:
    def __init__(self) -> None:
        self.values = iter((CREATED_AT, UPDATED_AT, UPDATED_AT, UPDATED_AT))

    def __call__(self) -> datetime:
        return next(self.values)


class ProjectSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.index = LocalProjectIndexRepository(
            Path(self.temporary_directory.name) / "known_projects.json"
        )
        self.executor = MemoryRemoteExecutor()
        self.structure = MolecularStructure((Atom(0, "H", 0.0, 0.0, 0.0),))
        self.input_plan = AimsOptimizationInputPlan(
            self.structure,
            AimsOptimizationSettings(),
        )
        self.bundle = self.input_plan.materialize(synthetic_species_library())

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_manual_runtime_is_used_by_actual_submission_without_input_changes(self):
        from moltage.domain.server_profile import FhiAimsRuntimeConfiguration
        from test_runtime_configuration import AIMS, MPI, NONE

        server = profile(save_password=False)
        server = replace(server, execution_preset=replace(server.execution_preset,
            fhi_runtime=FhiAimsRuntimeConfiguration(AIMS, MPI, NONE)))
        original_execute = self.executor.execute

        def execute(command):
            if "__AT_RUNTIME_AVAILABLE__" in command:
                self.executor.operations.append(("execute", command))
                return RemoteCommandResult(0, b"__AT_RUNTIME_AVAILABLE__\n", b"")
            return original_execute(command)

        with patch.object(self.executor, "execute", side_effect=execute), \
             patch.object(self.executor, "read_file_head", create=True, return_value=b"\x7fELF"):
            result = self._submit(server_profile=server)
        root = result.project.remote_project_path
        self.assertEqual(self.executor.files[root + "/geometry.in"], self.bundle.geometry_text.encode())
        self.assertEqual(self.executor.files[root + "/control.in"], self.bundle.control_text.encode())
        script = self.executor.files[root + "/submit.sh"].decode()
        self.assertEqual(script.splitlines()[-1], f"exec {MPI} -n 24 {AIMS}")
        self.assertNotIn("module ", script)
        self.assertEqual(sum("--parsable" in operation[1] for operation in self.executor.operations
                             if operation[0] == "execute"), 1)

    def test_unavailable_manual_runtime_stops_before_any_project_mutation(self):
        from moltage.domain.server_profile import FhiAimsRuntimeConfiguration
        from moltage.remote.runtime_environment import RuntimeConfigurationError
        from test_runtime_configuration import AIMS, MPI, NONE

        server = profile(save_password=False)
        server = replace(server, execution_preset=replace(server.execution_preset,
            fhi_runtime=FhiAimsRuntimeConfiguration(AIMS, MPI, NONE)))
        with patch.object(self.executor, "execute", return_value=RemoteCommandResult(3, b"", b"")):
            with self.assertRaises(RuntimeConfigurationError):
                self._submit(server_profile=server)
        self.assertFalse(any(operation[0] in {"write", "mkdir", "rename"}
                             for operation in self.executor.operations))

    def test_step1_and_step2_acquire_species_before_any_remote_mutation(self):
        species_path = str(
            PurePosixPath(TEST_SPECIES_ROOT)
            / SpeciesAccuracy.TIGHT.value
            / species_default_filename("H", SpeciesAccuracy.TIGHT)
        )
        for step in (
            ProjectStepKind.MOLECULE_OPT,
            ProjectStepKind.MOLECULE_AU_OPT,
        ):
            with self.subTest(step=step):
                executor = MemoryRemoteExecutor()
                executor.missing_species_paths.add(species_path)
                service = ProjectSubmissionService(
                    FixedConnectionService(executor),
                    self.index,
                    now_factory=Clock(),
                    project_id_factory=lambda: PROJECT_ID,
                    temporary_id_factory=_temporary_ids(),
                )

                with self.assertRaisesRegex(
                    ProjectPreparationError,
                    "element=H, accuracy=tight",
                ):
                    service.create_and_submit_project(
                        NewProjectSubmissionRequest(
                            profile(save_password=False),
                            "MoleculeA",
                            "MoleculeA.xyz",
                            step,
                            self.input_plan,
                            PASSWORD,
                        )
                    )

                self.assertFalse(
                    any(
                        operation[0] in {"mkdir", "write", "rename"}
                        for operation in executor.operations
                    )
                )
                self.assertFalse(
                    any(
                        "--parsable submit.sh" in operation[1]
                        for operation in executor.operations
                        if operation[0] == "execute"
                    )
                )

    def test_successful_species_reads_precede_project_allocation(self):
        result = self._submit()
        root = result.project.remote_project_path
        species_reads = [
            index
            for index, operation in enumerate(self.executor.operations)
            if operation[0] == "read"
            and operation[1].startswith(TEST_SPECIES_ROOT + "/")
        ]
        allocation_index = self.executor.operations.index(("mkdir", root))

        self.assertTrue(species_reads)
        self.assertLess(max(species_reads), allocation_index)

    def _submit(
        self,
        step: ProjectStepKind = ProjectStepKind.MOLECULE_OPT,
        *,
        input_plan: AimsOptimizationInputPlan | None = None,
        server_profile=None,
    ):
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )
        return service.create_and_submit_project(
            NewProjectSubmissionRequest(
                server_profile or profile(save_password=False),
                "MoleculeA",
                "MoleculeA.xyz",
                step,
                input_plan or self.input_plan,
                PASSWORD,
            )
        )

    def _restart_source(self, state: ProjectStepState):
        server = profile(
            profile_id=UUID("80000000-0000-4000-8000-000000000001"),
            save_password=False,
        )
        geometry = self.bundle.geometry_text.encode("utf-8")
        base = create_initial_project(
            base_name="Source",
            remote_directory_name="Source.20300825",
            source_molecule_name="Source.xyz",
            server_profile_id=server.profile_id,
            remote_project_root=server.remote_project_root,
            starting_step=ProjectStepKind.MOLECULE_OPT,
            now=CREATED_AT,
            project_id=UUID("80000000-0000-4000-8000-000000000002"),
        )
        step = replace(
            base.steps[0],
            state=state,
            job_id="44020",
            submitted_at=CREATED_AT,
            finished_at=(
                UPDATED_AT
                if state
                in {
                    ProjectStepState.FAILED,
                    ProjectStepState.SCHEDULER_COMPLETED,
                    ProjectStepState.SUCCEEDED,
                }
                else None
            ),
            scheduler_state=("CANCELLED" if state is ProjectStepState.FAILED else None),
            input_hashes=(("geometry.in", hashlib.sha256(geometry).hexdigest()),),
        )
        source = replace(
            base,
            revision=3,
            steps=(step, *base.steps[1:]),
        )
        root = source.remote_project_path
        metadata = root + "/.moltage"
        self.executor.directories.update((root, metadata))
        self.executor.files[metadata + "/project.json"] = (
            serialize_project_manifest(source).encode("utf-8")
        )
        self.executor.files[root + "/geometry.in"] = geometry
        provenance = ProjectRestartProvenance(
            source.project_id,
            ProjectStepKind.MOLECULE_OPT,
            "44020",
            hashlib.sha256(geometry).hexdigest(),
        )
        return server, source, provenance

    def test_step1_uses_current_profile_mail_settings_once(self) -> None:
        server = profile(
            save_password=False,
            email_enabled=True,
            email_recipient="user@example.com",
        )

        self._submit(server_profile=server)

        script = self.executor.files[
            "/srv/moltage-test/projects/MoleculeA.20300102/submit.sh"
        ].decode("utf-8")
        self.assertEqual(script.count("#SBATCH --mail-user=user@example.com"), 1)
        self.assertEqual(script.count("#SBATCH --mail-type=END,FAIL"), 1)
        self.assertEqual(script.count("--kill-on-bad-exit=1"), 1)
        self.assertEqual(
            script.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )
        self.assertNotIn("TIME_LIMIT", script)

    def test_step1_layout_manifest_transition_and_atomic_upload_are_exact(self) -> None:
        result = self._submit()
        root = "/srv/moltage-test/projects/MoleculeA.20300102"

        self.assertEqual(result.project.remote_project_path, root)
        self.assertEqual(result.project.revision, 2)
        self.assertEqual(result.step.state, ProjectStepState.QUEUED)
        self.assertEqual(result.job_id, "12345")
        self.assertEqual(result.cluster_name, "clusterA")
        self.assertNotIn(PASSWORD, repr(result))
        self.assertNotIn(self.bundle.geometry_text, repr(result))
        self.assertNotIn(self.bundle.control_text, repr(result))
        self.assertEqual(
            result.geometry_sha256,
            hashlib.sha256(self.bundle.geometry_text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            result.control_sha256,
            hashlib.sha256(self.bundle.control_text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            result.submit_sha256,
            hashlib.sha256(
                self.executor.files[f"{root}/submit.sh"]
            ).hexdigest(),
        )
        self.assertEqual(
            self._user_files(root),
            {
                f"{root}/.moltage/project.json",
                f"{root}/geometry.in",
                f"{root}/control.in",
                f"{root}/submit.sh",
            },
        )
        self.assertNotIn(f"{root}/molecule_Au", self.executor.directories)
        self.assertNotIn(f"{root}/molecule_Au/transport", self.executor.directories)
        self.assertFalse(
            any(".tmp-" in path for path in self.executor.files)
        )
        initial = parse_project_manifest(self.executor.manifest_history[0])
        final = parse_project_manifest(self.executor.manifest_history[-1])
        self.assertEqual(initial.revision, 1)
        self.assertEqual(initial.steps[0].state, ProjectStepState.NOT_STARTED)
        self.assertEqual(final.revision, 2)
        self.assertEqual(final.steps[0].state, ProjectStepState.QUEUED)
        self.assertEqual(len(self.executor.manifest_history), 2)
        self.assertTrue(
            all(
                ".tmp-" in operation[1]
                for operation in self.executor.operations
                if operation[0] == "write"
            )
        )
        self.assertEqual(
            self._execute_commands(),
            [
                "cd /srv/moltage-test/projects/MoleculeA.20300102 "
                "&& /usr/bin/sbatch --parsable submit.sh"
            ],
        )
        reference = LocalProjectIndexRepository(self.index.path).load()[0]
        self.assertEqual(reference.last_seen_revision, 2)
        self.assertFalse(reference.has_unseen_change(result.project))

        version_index = self.executor.operations.index(
            ("execute", "/usr/bin/sbatch --version")
        )
        project_mkdir_index = self.executor.operations.index(
            ("mkdir", root)
        )
        self.assertLess(version_index, project_mkdir_index)

    def test_lifecycle_diagnostics_record_milestones_without_secret_content(self) -> None:
        with self.assertLogs(
            SUBMISSION_LIFECYCLE_LOGGER_NAME,
            level="INFO",
        ) as captured:
            result = self._submit()

        self.assertEqual(result.job_id, "12345")
        text = "\n".join(captured.output)
        for event in (
            "input_preparation_started",
            "connection_established",
            "upload_started",
            "upload_completed",
            "sbatch_dispatch_started",
            "sbatch_returned_job_id",
            "manifest_persisted",
            "local_index_persisted",
            "connection_close_started",
            "connection_close_finished",
        ):
            self.assertIn("event=" + event, text)
        self.assertIn("project_id=" + str(PROJECT_ID), text)
        self.assertIn("job_id=12345", text)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(self.bundle.geometry_text, text)
        self.assertNotIn(self.bundle.control_text, text)

    def test_lifecycle_log_handler_failure_does_not_change_submission(self) -> None:
        class FailingHandler(logging.Handler):
            def emit(self, record):
                del record
                raise OSError("synthetic diagnostic log failure")

        logger = logging.getLogger(SUBMISSION_LIFECYCLE_LOGGER_NAME)
        previous_level = logger.level
        handler = FailingHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            result = self._submit()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertEqual(result.job_id, "12345")
        self.assertIs(result.step.state, ProjectStepState.QUEUED)

    def test_newly_discovered_automatic_location_is_cached_without_other_changes(self) -> None:
        repository = ServerProfileRepository(
            Path(self.temporary_directory.name) / "profiles.json"
        )
        server = profile(save_password=False)
        repository.save(server)
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            server_profile_repository=repository,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )

        service.create_and_submit_project(
            NewProjectSubmissionRequest(
                server,
                "MoleculeA",
                "MoleculeA.xyz",
                ProjectStepKind.MOLECULE_OPT,
                self.input_plan,
                PASSWORD,
            )
        )

        cached = repository.load().profiles[0]
        self.assertEqual(cached.profile_id, server.profile_id)
        self.assertEqual(cached.host, server.host)
        self.assertEqual(cached.execution_preset.nodes, server.execution_preset.nodes)
        self.assertEqual(cached.execution_preset.slurm_bin_directory, "/usr/bin")

    def test_cache_write_failure_warns_but_does_not_block_verified_submission(self) -> None:
        class FailingCacheRepository:
            def cache_automatic_slurm_directory(self, profile_id, directory, *lsf_directories):
                del profile_id, directory, lsf_directories
                raise OSError("local cache unavailable")

        progress = []
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            server_profile_repository=FailingCacheRepository(),
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )

        result = service.create_and_submit_project(
            NewProjectSubmissionRequest(
                profile(save_password=False),
                "MoleculeA",
                "MoleculeA.xyz",
                ProjectStepKind.MOLECULE_OPT,
                self.input_plan,
                PASSWORD,
            ),
            progress=progress.append,
        )

        self.assertEqual(result.job_id, "12345")
        self.assertTrue(any(message.startswith("Warning:") for message in progress))

    def test_manual_mode_verifies_and_submits_with_only_the_configured_path(self) -> None:
        self.executor.discovery_path = "/custom/slurm/bin/sbatch"
        server = profile(save_password=False)
        server = replace(
            server,
            execution_preset=replace(
                server.execution_preset,
                slurm_command_mode=SlurmCommandMode.MANUAL,
                slurm_bin_directory="/custom/slurm/bin",
            ),
        )
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )

        service.create_and_submit_project(
            NewProjectSubmissionRequest(
                server,
                "MoleculeA",
                "MoleculeA.xyz",
                ProjectStepKind.MOLECULE_OPT,
                self.input_plan,
                PASSWORD,
            )
        )

        all_commands = [
            item[1] for item in self.executor.operations if item[0] == "execute"
        ]
        self.assertEqual(
            all_commands,
            [
                "/custom/slurm/bin/sbatch --version",
                "cd /srv/moltage-test/projects/MoleculeA.20300102 && "
                "/custom/slurm/bin/sbatch --parsable submit.sh",
            ],
        )

    def test_direct_step2_layout_skips_step1(self) -> None:
        result = self._submit(ProjectStepKind.MOLECULE_AU_OPT)
        root = "/srv/moltage-test/projects/MoleculeA.20300102"
        step = f"{root}/molecule_Au"

        self.assertEqual(
            self._user_files(root),
            {
                f"{root}/.moltage/project.json",
                f"{step}/geometry.in",
                f"{step}/control.in",
                f"{step}/submit.sh",
            },
        )
        self.assertIn(step, self.executor.directories)
        self.assertNotIn(f"{step}/transport", self.executor.directories)
        self.assertEqual(result.project.steps[0].state, ProjectStepState.SKIPPED)
        self.assertEqual(result.project.steps[1].state, ProjectStepState.QUEUED)
        self.assertIs(
            result.project.starting_step,
            ProjectStepKind.MOLECULE_AU_OPT,
        )
        self.assertEqual(
            self._execute_commands(),
            [f"cd {step} && /usr/bin/sbatch --parsable submit.sh"],
        )
        step2_script = self.executor.files[f"{step}/submit.sh"].decode("utf-8")
        self.assertEqual(step2_script.count("--kill-on-bad-exit=1"), 1)
        self.assertTrue(
            step2_script.rstrip().endswith(
                "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
                "aims.synthetic.scalapack.mpi.x"
            )
        )

    def test_restart_creates_a_new_project_with_durable_source_provenance(self) -> None:
        server, source, provenance = self._restart_source(
            ProjectStepState.FAILED
        )
        species_path = str(
            PurePosixPath(server.execution_preset.fhi_species_defaults_path)
            / SpeciesAccuracy.TIGHT.value
            / species_default_filename("H", SpeciesAccuracy.TIGHT)
        )
        self.executor.files[species_path] = (
            b"# CURRENT SELECTED SERVER DEFINITION\n"
            b"species H\n"
            b"  nucleus 1\n"
        )
        edited_plan = AimsOptimizationInputPlan(
            MolecularStructure((Atom(0, "H", 0.25, 0.0, 0.0),)),
            AimsOptimizationSettings(),
        )
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )

        result = service.create_and_submit_project(
            NewProjectSubmissionRequest(
                profile=server,
                base_name="Restarted",
                source_molecule_name=source.source_molecule_name,
                starting_step=ProjectStepKind.MOLECULE_OPT,
                input_plan=edited_plan,
                supplied_password=PASSWORD,
                restart_provenance=provenance,
                restart_source_project=source,
            )
        )

        self.assertNotEqual(result.project.project_id, source.project_id)
        self.assertEqual(result.project.restart_provenance, provenance)
        self.assertEqual(
            result.project.remote_project_path,
            "/srv/moltage-test/projects/Restarted.20300102",
        )
        self.assertEqual(
            self.executor.files[
                result.project.remote_project_path + "/geometry.in"
            ],
            edited_plan.materialize(
                synthetic_species_library()
            ).geometry_text.encode("utf-8"),
        )
        self.assertIn(
            b"# CURRENT SELECTED SERVER DEFINITION",
            self.executor.files[
                result.project.remote_project_path + "/control.in"
            ],
        )
        source_manifest = parse_project_manifest(
            self.executor.files[
                source.remote_project_path + "/.moltage/project.json"
            ]
        )
        self.assertEqual(source_manifest, source)

    def test_active_restart_source_fails_before_new_project_mkdir(self) -> None:
        server, source, provenance = self._restart_source(
            ProjectStepState.RUNNING
        )
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )

        with self.assertRaisesRegex(
            ProjectPreparationError,
            "not durably terminal",
        ):
            service.create_and_submit_project(
                NewProjectSubmissionRequest(
                    profile=server,
                    base_name="Restarted",
                    source_molecule_name=source.source_molecule_name,
                    starting_step=ProjectStepKind.MOLECULE_OPT,
                    input_plan=self.input_plan,
                    supplied_password=PASSWORD,
                    restart_provenance=provenance,
                    restart_source_project=source,
                )
            )

        self.assertNotIn(
            ("mkdir", "/srv/moltage-test/projects/Restarted.20300102"),
            self.executor.operations,
        )
        self.assertFalse(
            any(
                item[0] == "write"
                and "/Restarted.20300102/" in item[1]
                for item in self.executor.operations
            )
        )

    def test_direct_step3_uses_canonical_layout_and_persists_electrodes(self) -> None:
        _, _, contact_structure, contact_connectivity, anchors = _contact_state()
        contact_context = imported_contact_au_context(
            contact_structure,
            contact_connectivity,
            anchors,
        )
        proposal = propose_electrode_placement(
            contact_structure,
            contact_connectivity,
            contact_context.contact_sites,
            contact_context.contact_sites,
        )
        applied = apply_electrode_placement(
            contact_structure,
            contact_connectivity,
            proposal,
        )
        imported_context = ImportedTransportStartContext(contact_context, applied)
        input_plan = TransportConvergenceInputPlan(
            applied.structure,
            TransportConvergenceSettings(),
        )
        requirement = input_plan.species_requirements[0]
        current_species_path = str(
            PurePosixPath(TEST_SPECIES_ROOT)
            / requirement.accuracy.value
            / species_default_filename(requirement.element, requirement.accuracy)
        )
        self.executor.files[current_species_path] = synthetic_species_text(
            requirement.element,
            requirement.accuracy,
        ).replace(
            "# SYNTHETIC TEST SPECIES",
            "# CURRENT STEP3 SERVER DEFINITION",
        ).encode(
            "utf-8"
        )
        aims_inputs = input_plan.materialize(synthetic_species_library())
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )

        result = service.create_and_submit_project(
            NewProjectSubmissionRequest(
                profile=profile(save_password=False),
                base_name="MoleculeA",
                source_molecule_name="MoleculeA.xyz",
                starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
                input_plan=input_plan,
                supplied_password=PASSWORD,
                imported_transport_context=imported_context,
            )
        )

        root = "/srv/moltage-test/projects/MoleculeA.20300102"
        transport = f"{root}/molecule_Au/transport"
        self.assertEqual(
            self._user_files(root),
            {
                f"{root}/.moltage/project.json",
                f"{transport}/geometry.in",
                f"{transport}/control.in",
                f"{transport}/submit.sh",
            },
        )
        self.assertIn(f"{root}/molecule_Au", self.executor.directories)
        self.assertIn(transport, self.executor.directories)
        self.assertEqual(
            self.executor.files[f"{transport}/geometry.in"],
            aims_inputs.geometry_text.encode("utf-8"),
        )
        self.assertIn(
            b"# CURRENT STEP3 SERVER DEFINITION",
            self.executor.files[f"{transport}/control.in"],
        )
        self.assertEqual(
            tuple(step.state for step in result.project.steps),
            (
                ProjectStepState.SKIPPED,
                ProjectStepState.SKIPPED,
                ProjectStepState.QUEUED,
                ProjectStepState.NOT_STARTED,
            ),
        )
        self.assertIs(
            result.project.starting_step,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        self.assertEqual(
            result.project.electrode_provenance,
            imported_context.electrode_provenance,
        )
        self.assertFalse(
            any(
                path.endswith("geometry.in.next_step")
                or path.endswith("aims.dft.out")
                for path in self.executor.files
            )
        )
        self.assertEqual(
            self._execute_commands(),
            [f"cd {transport} && /usr/bin/sbatch --parsable submit.sh"],
        )
        step3_script = self.executor.files[f"{transport}/submit.sh"].decode("utf-8")
        self.assertEqual(step3_script.count("--kill-on-bad-exit=1"), 1)
        self.assertTrue(
            step3_script.rstrip().endswith(
                "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
                "aims.synthetic.scalapack.mpi.x"
            )
        )

    def test_step2_directory_failure_keeps_managed_root_and_records_failed(self) -> None:
        root = "/srv/moltage-test/projects/MoleculeA.20300102"
        step = f"{root}/molecule_Au"
        self.executor.mkdir_errors[step] = RemoteExecutorError(
            "permission denied"
        )

        with self.assertRaises(ProjectPreparationError) as caught:
            self._submit(ProjectStepKind.MOLECULE_AU_OPT)

        self.assertEqual(caught.exception.remote_project_path, root)
        self.assertIn(root, self.executor.directories)
        self.assertNotIn(step, self.executor.directories)
        self.assertEqual(self._execute_commands(), [])
        final = parse_project_manifest(self.executor.manifest_history[-1])
        self.assertEqual(final.revision, 2)
        self.assertEqual(final.steps[0].state, ProjectStepState.SKIPPED)
        self.assertEqual(final.steps[1].state, ProjectStepState.FAILED)
        self.assertIsNotNone(final.steps[1].finished_at)

    def test_synthetic_bundle_bytes_are_uploaded_without_modification(self) -> None:
        structure = MolecularStructure(
            (Atom(0, "H", 0.0, 0.25, -0.5),), comment="unchanged"
        )
        original_atoms = structure.atoms
        input_plan = AimsOptimizationInputPlan(
            structure,
            AimsOptimizationSettings(),
        )
        bundle = input_plan.materialize(synthetic_species_library())

        self._submit(input_plan=input_plan)
        root = "/srv/moltage-test/projects/MoleculeA.20300102"

        self.assertEqual(
            self.executor.files[f"{root}/geometry.in"],
            bundle.geometry_text.encode("utf-8"),
        )
        self.assertEqual(
            self.executor.files[f"{root}/control.in"],
            bundle.control_text.encode("utf-8"),
        )
        self.assertIs(structure.atoms, original_atoms)

    def test_checksum_corruption_stops_before_sbatch_and_records_failed(self) -> None:
        root = "/srv/moltage-test/projects/MoleculeA.20300102"
        self.executor.corrupt_read_path = f"{root}/control.in"

        with self.assertRaises(ProjectChecksumError) as caught:
            self._submit()

        self.assertIn("control.in", str(caught.exception))
        self.assertEqual(caught.exception.remote_project_path, root)
        self.assertEqual(self._execute_commands(), [])
        self.assertIn(root, self.executor.directories)
        final = parse_project_manifest(self.executor.manifest_history[-1])
        self.assertEqual(final.steps[0].state, ProjectStepState.FAILED)
        self.assertIsNotNone(final.steps[0].finished_at)
        self.assertNotEqual(final.steps[0].state, ProjectStepState.QUEUED)

    def test_unknown_after_dispatch_is_not_retried_and_records_hashes(self) -> None:
        self.executor.execute_error = RemoteCommandOutcomeUnknown("lost")

        with self.assertRaises(SubmissionOutcomeUnknown) as caught:
            self._submit()

        self.assertEqual(len(self._execute_commands()), 1)
        self.assertIn("Inspect Slurm", str(caught.exception))
        final = parse_project_manifest(self.executor.manifest_history[-1])
        step = final.steps[0]
        self.assertEqual(step.state, ProjectStepState.UNKNOWN)
        self.assertEqual(
            step.last_error,
            "Slurm submission outcome unknown; automatic retry was not performed.",
        )
        self.assertEqual(set(dict(step.input_hashes)), self._input_names())
        self.assertIsNone(step.finished_at)
        self.assertIsNone(step.job_id)

    def test_nonzero_sbatch_is_failed_with_hashes_and_no_retry(self) -> None:
        self.executor.command_result = RemoteCommandResult(
            1,
            b"",
            b"bash: line 1: sbatch: command not found\n",
        )

        with self.assertRaises(SbatchRejectedError) as caught:
            self._submit()

        self.assertEqual(len(self._execute_commands()), 1)
        final = parse_project_manifest(self.executor.manifest_history[-1])
        step = final.steps[0]
        self.assertEqual(step.state, ProjectStepState.FAILED)
        self.assertIn("sbatch: command not found", step.last_error)
        self.assertNotIn("Password authentication failed", str(caught.exception))
        self.assertEqual(set(dict(step.input_hashes)), self._input_names())
        self.assertIsNone(step.job_id)
        self.assertIsNotNone(step.finished_at)

    def test_unsupported_success_receipt_is_unknown_without_retry(self) -> None:
        self.executor.command_result = RemoteCommandResult(
            0,
            b"Submitted batch job 12345\n",
            b"",
        )

        with self.assertRaises(SubmissionOutcomeUnknown):
            self._submit()

        self.assertEqual(len(self._execute_commands()), 1)
        final = parse_project_manifest(self.executor.manifest_history[-1])
        self.assertEqual(final.steps[0].state, ProjectStepState.UNKNOWN)

    def test_step_directory_is_shell_quoted_for_the_fixed_sbatch_command(self) -> None:
        self.executor.directories.add("/srv/moltage-test/work place")
        service = ProjectSubmissionService(
            FixedConnectionService(self.executor),
            self.index,
            now_factory=Clock(),
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=_temporary_ids(),
        )
        server = replace(
            profile(save_password=False),
            remote_project_root="/srv/moltage-test/work place",
        )

        service.create_and_submit_project(
            NewProjectSubmissionRequest(
                server,
                "MoleculeA",
                "MoleculeA.xyz",
                ProjectStepKind.MOLECULE_OPT,
                self.input_plan,
                PASSWORD,
            )
        )

        self.assertEqual(
            self._execute_commands(),
            [
                "cd '/srv/moltage-test/work place/MoleculeA.20300102' "
                "&& /usr/bin/sbatch --parsable submit.sh"
            ],
        )

    def test_password_is_absent_from_all_persistent_and_display_surfaces(self) -> None:
        self.executor.command_result = RemoteCommandResult(
            1,
            b"",
            f"sbatch: error: {PASSWORD}\n".encode(),
        )

        with self.assertRaises(SbatchRejectedError) as caught:
            self._submit()

        surfaces = [
            str(caught.exception),
            repr(
                NewProjectSubmissionRequest(
                    profile(save_password=False),
                    "MoleculeA",
                    "MoleculeA.xyz",
                    ProjectStepKind.MOLECULE_OPT,
                    self.input_plan,
                    PASSWORD,
                )
            ),
            self.index.path.read_text(encoding="utf-8"),
            *(data.decode("utf-8") for data in self.executor.files.values()),
        ]
        self.assertNotIn(PASSWORD, "\n".join(surfaces))

    def _execute_commands(self) -> list[str]:
        return [
            operation[1]
            for operation in self.executor.operations
            if operation[0] == "execute"
            and operation[1].endswith(" --parsable submit.sh")
        ]

    def _user_files(self, root: str) -> set[str]:
        return {
            path
            for path in self.executor.files
            if path.startswith(root + "/") and ".tmp-" not in path
        }

    @staticmethod
    def _input_names() -> set[str]:
        return {"geometry.in", "control.in", "submit.sh"}


class ProjectAllocationTests(unittest.TestCase):
    def test_missing_execution_preset_stops_before_connection_or_mkdir(self) -> None:
        executor = MemoryRemoteExecutor()
        with tempfile.TemporaryDirectory() as directory:
            service = ProjectSubmissionService(
                FixedConnectionService(executor),
                LocalProjectIndexRepository(Path(directory) / "index.json"),
                now_factory=Clock(),
                project_id_factory=lambda: PROJECT_ID,
                temporary_id_factory=_temporary_ids(),
            )
            with self.assertRaisesRegex(
                MissingClusterSettingsError,
                "Configure Cluster Execution Settings for ExampleCluster",
            ):
                service.create_and_submit_project(
                    NewProjectSubmissionRequest(
                        replace(profile(save_password=False), execution_preset=None),
                        "MoleculeA",
                        "MoleculeA.xyz",
                        ProjectStepKind.MOLECULE_OPT,
                        AimsOptimizationInputPlan(
                            MolecularStructure((Atom(0, "H", 0, 0, 0),)),
                            AimsOptimizationSettings(),
                        ),
                        PASSWORD,
                    )
                )

        self.assertEqual(executor.operations, [])

    def test_complete_discovery_failure_stops_before_any_project_mutation(self) -> None:
        executor = MemoryRemoteExecutor()
        executor.discovery_path = None
        with tempfile.TemporaryDirectory() as directory:
            index = LocalProjectIndexRepository(Path(directory) / "index.json")
            service = ProjectSubmissionService(
                FixedConnectionService(executor),
                index,
                now_factory=Clock(),
                project_id_factory=lambda: PROJECT_ID,
                temporary_id_factory=_temporary_ids(),
            )

            with self.assertRaises(SlurmDiscoveryError):
                service.create_and_submit_project(
                    NewProjectSubmissionRequest(
                        profile(save_password=False),
                        "MoleculeA",
                        "MoleculeA.xyz",
                        ProjectStepKind.MOLECULE_OPT,
                        AimsOptimizationInputPlan(
                            MolecularStructure((Atom(0, "H", 0, 0, 0),)),
                            AimsOptimizationSettings(),
                        ),
                        PASSWORD,
                    )
                )

            self.assertFalse(index.path.exists())
        self.assertEqual(
            [item for item in executor.operations if item[0] == "mkdir"],
            [],
        )
        self.assertEqual(executor.files, {})
        self.assertEqual(executor.manifest_history, [])
        self.assertEqual(
            [item[1] for item in executor.operations if item[0] == "execute"],
            [
                CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                LOGIN_SHELL_DISCOVERY_COMMAND,
            ],
        )

    def test_manual_verification_failure_stops_before_project_allocation(self) -> None:
        executor = MemoryRemoteExecutor()
        executor.discovery_path = "/wrong/slurm/bin/sbatch"
        executor.discovery_version_result = RemoteCommandResult(127, b"", b"missing")
        server = profile(save_password=False)
        server = replace(
            server,
            execution_preset=replace(
                server.execution_preset,
                slurm_command_mode=SlurmCommandMode.MANUAL,
                slurm_bin_directory="/wrong/slurm/bin",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            service = ProjectSubmissionService(
                FixedConnectionService(executor),
                LocalProjectIndexRepository(Path(directory) / "index.json"),
                now_factory=Clock(),
                project_id_factory=lambda: PROJECT_ID,
                temporary_id_factory=_temporary_ids(),
            )
            with self.assertRaises(SlurmConfigurationError):
                service.create_and_submit_project(
                    NewProjectSubmissionRequest(
                        server,
                        "MoleculeA",
                        "MoleculeA.xyz",
                        ProjectStepKind.MOLECULE_OPT,
                        AimsOptimizationInputPlan(
                            MolecularStructure((Atom(0, "H", 0, 0, 0),)),
                            AimsOptimizationSettings(),
                        ),
                        PASSWORD,
                    )
                )

        self.assertEqual(
            [item for item in executor.operations if item[0] == "mkdir"],
            [],
        )
        self.assertEqual(executor.manifest_history, [])

    def test_only_already_exists_advances_the_candidate(self) -> None:
        executor = MemoryRemoteExecutor()
        base = "/srv/moltage-test/projects/MoleculeA.20300102"
        executor.directories.update((base, base + "_02"))

        name, path = allocate_remote_project_directory(
            executor,
            "/srv/moltage-test/projects",
            "MoleculeA",
            CREATED_AT.date(),
        )

        self.assertEqual(name, "MoleculeA.20300102_03")
        self.assertEqual(path, base + "_03")
        attempts = [item[1] for item in executor.operations if item[0] == "mkdir"]
        self.assertEqual(attempts, [base, base + "_02", base + "_03"])

    def test_permission_or_missing_parent_does_not_advance(self) -> None:
        for error in (
            RemoteExecutorError("permission denied"),
            RemotePathNotFoundError("workspace missing"),
        ):
            with self.subTest(error=type(error).__name__):
                executor = MemoryRemoteExecutor()
                base = "/srv/moltage-test/projects/MoleculeA.20300102"
                executor.mkdir_errors[base] = error
                with self.assertRaises(ProjectAllocationError):
                    allocate_remote_project_directory(
                        executor,
                        "/srv/moltage-test/projects",
                        "MoleculeA",
                        CREATED_AT.date(),
                    )
                attempts = [
                    item for item in executor.operations if item[0] == "mkdir"
                ]
                self.assertEqual(attempts, [("mkdir", base)])

    def test_missing_workspace_stops_before_project_mkdir(self) -> None:
        executor = MemoryRemoteExecutor()
        executor.directories.remove("/srv/moltage-test/projects")
        with tempfile.TemporaryDirectory() as directory:
            service = ProjectSubmissionService(
                FixedConnectionService(executor),
                LocalProjectIndexRepository(Path(directory) / "index.json"),
                now_factory=Clock(),
                project_id_factory=lambda: PROJECT_ID,
                temporary_id_factory=_temporary_ids(),
            )
            with self.assertRaises(ProjectAllocationError):
                service.create_and_submit_project(
                    NewProjectSubmissionRequest(
                        profile(save_password=False),
                        "MoleculeA",
                        "MoleculeA.xyz",
                        ProjectStepKind.MOLECULE_OPT,
                        AimsOptimizationInputPlan(
                            MolecularStructure((Atom(0, "H", 0, 0, 0),)),
                            AimsOptimizationSettings(),
                        ),
                        PASSWORD,
                    )
                )

        self.assertEqual(
            [item for item in executor.operations if item[0] == "mkdir"],
            [],
        )


def _temporary_ids():
    values = iter(f"tmp{index}" for index in range(100))
    return lambda: next(values)


if __name__ == "__main__":
    unittest.main()
