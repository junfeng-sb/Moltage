from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import PurePosixPath
import unittest
from uuid import UUID

from moltage.aims.input_bundle import build_aims_optimization_inputs
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AtomAimsSettings,
    SpeciesAccuracy,
)
from species_test_support import synthetic_species_library
from moltage.aims.transport_convergence_bundle import (
    build_transport_convergence_aims_inputs,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.aims.transport_evidence import TransportSpinMode
from moltage.aitranss.slurm import (
    AitranssExecutionSettings,
    render_aitranss_submit_script,
)
from moltage.aitranss.tcontrol import TControlSettings, render_tcontrol
from moltage.app.project_planning import create_initial_project
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.app.task_restart import (
    ProjectTaskCancellationOutcome,
    ProjectTaskRestartError,
    ProjectTaskRestartRequest,
    ProjectTaskRestartService,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
)
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_surface import propose_electrode_surfaces
from moltage.junction.electrode_surface import recover_legacy_normal_electrode_provenance
from moltage.remote.executor import (
    RemoteCommandOutcomeUnknown,
    RemoteCommandResult,
)
from moltage.remote.project_manifest import serialize_project_manifest
from moltage.remote.slurm import render_submit_script
from moltage.remote.slurm_cancel import SlurmCancellationRejected
from phase2b1_test_support import profile
from test_electrode_surface import accepted_shape
from electrode_test_support import synthetic_legacy_au59_structure
from test_project_recovery import FixedConnectionService, RecoveryRemoteExecutor
from synthetic_structure_test_support import synthetic_extended_electrode_placement


NOW = datetime(2030, 9, 1, 9, 0, tzinfo=timezone.utc)
PROFILE = profile(
    profile_id=UUID("70000000-0000-4000-8000-000000000001")
)


class TaskRestartRemote(RecoveryRemoteExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.scancel_result = RemoteCommandResult(0, b"", b"")
        self.scancel_error: Exception | None = None

    def execute(self, command):
        if command.startswith("/usr/bin/scancel "):
            self.commands.append(command)
            self.operations.append(("execute", command))
            if self.scancel_error is not None:
                raise self.scancel_error
            return self.scancel_result
        return super().execute(command)


def _running_step1_project(
    state: ProjectStepState = ProjectStepState.RUNNING,
):
    structure = MolecularStructure(
        (
            Atom(0, "C", 0.0, 0.0, 0.0),
            Atom(1, "N", 1.25, 0.0, 0.0),
        )
    )
    settings = AimsOptimizationSettings(
        atom_settings=(
            AtomAimsSettings(0, species_accuracy=SpeciesAccuracy.LIGHT),
        )
    )
    bundle = build_aims_optimization_inputs(
        structure,
        settings,
        synthetic_species_library(),
    )
    base = create_initial_project(
        base_name="RestartSource",
        remote_directory_name="RestartSource.20300901",
        source_molecule_name="source.xyz",
        server_profile_id=PROFILE.profile_id,
        remote_project_root=PROFILE.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_OPT,
        now=NOW,
        project_id=UUID("70000000-0000-4000-8000-000000000002"),
    )
    script = render_submit_script(
        PROFILE.execution_preset,
        base.project_id,
        ProjectStepKind.MOLECULE_OPT,
    ).encode("utf-8")
    files = {
        "geometry.in": bundle.geometry_text.encode("utf-8"),
        "control.in": bundle.control_text.encode("utf-8"),
        "submit.sh": script,
    }
    step1 = replace(
        base.steps[0],
        state=state,
        job_id="44010",
        submitted_at=NOW,
        input_hashes=tuple(
            (name, hashlib.sha256(data).hexdigest())
            for name, data in files.items()
        ),
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    project = replace(
        base,
        revision=3,
        steps=(step1, *base.steps[1:]),
    )
    return project, files, structure, settings


def _install(remote: TaskRestartRemote, project, files) -> None:
    root = project.remote_project_path
    metadata = str(PurePosixPath(root) / ".moltage")
    remote.directories.update((root, metadata))
    remote.files[str(PurePosixPath(metadata) / "project.json")] = (
        serialize_project_manifest(project).encode("utf-8")
    )
    for name, data in files.items():
        remote.files[str(PurePosixPath(root) / name)] = data


def _running_step2_project():
    structure = MolecularStructure(
        (
            Atom(0, "C", 0.0, 0.0, 0.0),
            Atom(1, "Au", 1.9, 0.0, 0.0),
        )
    )
    settings = AimsOptimizationSettings(force_threshold=0.02)
    bundle = build_aims_optimization_inputs(
        structure,
        settings,
        synthetic_species_library(),
    )
    base = create_initial_project(
        base_name="Step2RestartSource",
        remote_directory_name="Step2RestartSource.20300901",
        source_molecule_name="source.xyz",
        server_profile_id=PROFILE.profile_id,
        remote_project_root=PROFILE.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        now=NOW,
        project_id=UUID("70000000-0000-4000-8000-000000000003"),
    )
    script = render_submit_script(
        PROFILE.execution_preset,
        base.project_id,
        ProjectStepKind.MOLECULE_AU_OPT,
    ).encode("utf-8")
    files = {
        "geometry.in": bundle.geometry_text.encode("utf-8"),
        "control.in": bundle.control_text.encode("utf-8"),
        "submit.sh": script,
    }
    step2 = replace(
        base.steps[1],
        state=ProjectStepState.RUNNING,
        job_id="44013",
        submitted_at=NOW,
        input_hashes=tuple(
            (name, hashlib.sha256(data).hexdigest())
            for name, data in files.items()
        ),
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    return (
        replace(base, revision=3, steps=(base.steps[0], step2, *base.steps[2:])),
        files,
        structure,
        settings,
    )


def _install_step2_files(remote: TaskRestartRemote, project, files) -> None:
    root = project.remote_project_path
    metadata = str(PurePosixPath(root) / ".moltage")
    step2 = str(PurePosixPath(root) / "molecule_Au")
    remote.directories.update((root, metadata, step2))
    remote.files[str(PurePosixPath(metadata) / "project.json")] = (
        serialize_project_manifest(project).encode("utf-8")
    )
    for name, data in files.items():
        remote.files[str(PurePosixPath(step2) / name)] = data


def _running_step4_project(*, legacy=False, extended=False):
    if legacy and extended:
        raise ValueError("legacy and extended restart fixtures are mutually exclusive")
    if legacy:
        structure = synthetic_legacy_au59_structure()
        electrode_provenance = recover_legacy_normal_electrode_provenance(
            structure
        )
    elif extended:
        applied = synthetic_extended_electrode_placement()
        structure = applied.structure
        electrode_provenance = provenance_from_applied_electrodes(applied)
    else:
        structure, electrode_provenance = accepted_shape()
    transport_settings = TransportConvergenceSettings()
    bundle = build_transport_convergence_aims_inputs(
        structure,
        transport_settings,
        synthetic_species_library(),
    )
    base = create_initial_project(
        base_name="Step4RestartSource",
        remote_directory_name="Step4RestartSource.20300901",
        source_molecule_name="source.geometry.in",
        server_profile_id=PROFILE.profile_id,
        remote_project_root=PROFILE.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        now=NOW,
        project_id=UUID("70000000-0000-4000-8000-000000000004"),
    )
    step3_script = render_submit_script(
        PROFILE.execution_preset,
        base.project_id,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
    ).encode("utf-8")
    step3_files = {
        "geometry.in": bundle.geometry_text.encode("utf-8"),
        "control.in": bundle.control_text.encode("utf-8"),
        "submit.sh": step3_script,
    }
    step2 = replace(base.steps[1], state=ProjectStepState.SUCCEEDED)
    step3 = replace(
        base.steps[2],
        state=ProjectStepState.SUCCEEDED,
        job_id="44011",
        submitted_at=NOW,
        finished_at=NOW,
        scheduler_state="COMPLETED",
        input_hashes=tuple(
            (name, hashlib.sha256(data).hexdigest())
            for name, data in step3_files.items()
        ),
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    surface = propose_electrode_surfaces(structure, electrode_provenance)
    tcontrol_settings = TControlSettings(
        natoms=len(structure),
        nsaos=512,
        lsurc=surface.left_one_based[0],
        lsurx=surface.left_one_based[1],
        lsury=surface.left_one_based[2],
        rsurc=surface.right_one_based[0],
        rsurx=surface.right_one_based[1],
        rsury=surface.right_one_based[2],
    )
    tcontrol = render_tcontrol(
        tcontrol_settings,
        structure,
        TransportSpinMode.NONE,
    ).encode("utf-8")
    execution = AitranssExecutionSettings(
        cpu_threads=6,
        runtime_minutes=90,
        memory_gb=24,
    )
    step4_script = render_aitranss_submit_script(
        profile_preset=PROFILE.execution_preset,
        settings=execution,
        project_id=base.project_id,
        executable_path="/opt/aitranss/aitranss.synthetic.x",
        aitranss_modules=PROFILE.aitranss_runtime.modules,
    ).encode("utf-8")
    step4_files = {
        "tcontrol": tcontrol,
        "submit.aitranss.sh": step4_script,
    }
    step4 = replace(
        base.steps[3],
        state=ProjectStepState.RUNNING,
        job_id="44012",
        submitted_at=NOW,
        input_hashes=tuple(
            (name, hashlib.sha256(data).hexdigest())
            for name, data in step4_files.items()
        ),
        submit_script_filename="submit.aitranss.sh",
        slurm_output_filename="aitranss.out",
    )
    project = replace(
        base,
        revision=8,
        steps=(base.steps[0], step2, step3, step4),
        electrode_provenance=(() if legacy else electrode_provenance),
        legacy_electrode_recovery_allowed=legacy,
    )
    return (
        project,
        {**step3_files, **step4_files},
        structure,
        transport_settings,
        tcontrol_settings,
        execution,
    )


def _install_transport_files(remote: TaskRestartRemote, project, files) -> None:
    root = project.remote_project_path
    metadata = str(PurePosixPath(root) / ".moltage")
    transport = str(PurePosixPath(root) / "molecule_Au" / "transport")
    remote.directories.update(
        (root, metadata, str(PurePosixPath(root) / "molecule_Au"), transport)
    )
    remote.files[str(PurePosixPath(metadata) / "project.json")] = (
        serialize_project_manifest(project).encode("utf-8")
    )
    for name, data in files.items():
        remote.files[str(PurePosixPath(transport) / name)] = data


class ProjectTaskRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.remote = TaskRestartRemote()
        self.project, files, self.structure, self.settings = (
            _running_step1_project()
        )
        _install(self.remote, self.project, files)
        self.remote.squeue_stdout = b"44010|RUNNING\n"
        self.service = ProjectTaskRestartService(
            FixedConnectionService(self.remote)
        )

    def _request(self) -> ProjectTaskRestartRequest:
        return ProjectTaskRestartRequest(
            PROFILE,
            self.project,
            ProjectStepKind.MOLECULE_OPT,
        )

    def _scancel_commands(self) -> list[str]:
        return [command for command in self.remote.commands if "scancel" in command]

    def test_running_task_reads_exact_inputs_then_cancels_exact_job_once(self) -> None:
        manifest_path = (
            self.project.remote_project_path
            + "/.moltage/project.json"
        )
        before = self.remote.files[manifest_path]

        result = self.service.abort_and_prepare(self._request())

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.REQUESTED)
        self.assertEqual(self._scancel_commands(), ["/usr/bin/scancel 44010"])
        self.assertIsNotNone(result.draft)
        self.assertEqual(result.draft.source_structure.atoms, self.structure.atoms)
        self.assertEqual(result.draft.optimization_settings, self.settings)
        self.assertEqual(result.draft.source_job_id, "44010")
        self.assertFalse(result.draft.source_terminal_confirmed)
        self.assertEqual(self.remote.files[manifest_path], before)
        reads = [item[1] for item in self.remote.operations if item[0] == "read"]
        self.assertIn(self.project.remote_project_path + "/geometry.in", reads)
        self.assertIn(self.project.remote_project_path + "/control.in", reads)
        self.assertIn(self.project.remote_project_path + "/submit.sh", reads)

    def test_legacy_profile_without_species_root_recovers_embedded_inputs(self) -> None:
        legacy_profile = replace(
            PROFILE,
            execution_preset=replace(
                PROFILE.execution_preset,
                fhi_species_defaults_path=None,
            ),
        )

        result = self.service.abort_and_prepare(
            ProjectTaskRestartRequest(
                legacy_profile,
                self.project,
                ProjectStepKind.MOLECULE_OPT,
            )
        )

        self.assertEqual(result.draft.source_structure.atoms, self.structure.atoms)
        self.assertEqual(result.draft.optimization_settings, self.settings)
        read_paths = tuple(
            item[1] for item in self.remote.operations if item[0] == "read"
        )
        self.assertFalse(any(path.endswith("_default") for path in read_paths))

    def test_queued_task_is_equally_eligible(self) -> None:
        project, files, _structure, _settings = _running_step1_project(
            ProjectStepState.QUEUED
        )
        remote = TaskRestartRemote()
        _install(remote, project, files)
        remote.squeue_stdout = b"44010|PENDING\n"
        service = ProjectTaskRestartService(FixedConnectionService(remote))

        result = service.abort_and_prepare(
            ProjectTaskRestartRequest(
                PROFILE,
                project,
                ProjectStepKind.MOLECULE_OPT,
            )
        )

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.REQUESTED)
        self.assertEqual(
            [command for command in remote.commands if "scancel" in command],
            ["/usr/bin/scancel 44010"],
        )

    def test_step2_uses_its_exact_directory_and_recovers_optimization_settings(self) -> None:
        project, files, structure, settings = _running_step2_project()
        remote = TaskRestartRemote()
        _install_step2_files(remote, project, files)
        remote.squeue_stdout = b"44013|RUNNING\n"
        service = ProjectTaskRestartService(FixedConnectionService(remote))

        result = service.abort_and_prepare(
            ProjectTaskRestartRequest(
                PROFILE,
                project,
                ProjectStepKind.MOLECULE_AU_OPT,
            )
        )

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.REQUESTED)
        self.assertEqual(result.draft.source_structure.atoms, structure.atoms)
        self.assertEqual(result.draft.optimization_settings, settings)
        self.assertEqual(
            [command for command in remote.commands if "scancel" in command],
            ["/usr/bin/scancel 44013"],
        )
        reads = [item[1] for item in remote.operations if item[0] == "read"]
        self.assertIn(
            project.remote_project_path + "/molecule_Au/geometry.in",
            reads,
        )

    def test_step3_recovers_fixed_geometry_settings_without_step4_surface(self) -> None:
        (
            project,
            files,
            structure,
            transport_settings,
            _tcontrol_settings,
            _execution,
        ) = _running_step4_project()
        step3 = replace(
            project.steps[2],
            state=ProjectStepState.RUNNING,
            finished_at=None,
            scheduler_state=None,
        )
        step4 = ProjectStepRecord(
            ProjectStepKind.TRANSMISSION,
            ProjectStepState.NOT_STARTED,
            "molecule_Au/transport",
        )
        project = replace(
            project,
            revision=7,
            steps=(*project.steps[:2], step3, step4),
        )
        remote = TaskRestartRemote()
        _install_transport_files(remote, project, files)
        remote.squeue_stdout = b"44011|RUNNING\n"
        service = ProjectTaskRestartService(FixedConnectionService(remote))

        result = service.abort_and_prepare(
            ProjectTaskRestartRequest(
                PROFILE,
                project,
                ProjectStepKind.TRANSPORT_CONVERGENCE,
            )
        )

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.REQUESTED)
        self.assertTrue(result.draft.coordinate_only)
        self.assertEqual(result.draft.source_structure.atoms, structure.atoms)
        self.assertEqual(result.draft.transport_settings, transport_settings)
        self.assertIsNone(result.draft.tcontrol_settings)
        self.assertIsNone(result.draft.step4_execution_settings)
        self.assertEqual(len(result.draft.electrode_provenance), 2)
        self.assertEqual(
            [command for command in remote.commands if "scancel" in command],
            ["/usr/bin/scancel 44011"],
        )

    def test_ambiguous_scancel_opens_no_draft_and_is_never_retried(self) -> None:
        self.remote.scancel_error = RemoteCommandOutcomeUnknown(
            "transport lost after dispatch"
        )

        result = self.service.abort_and_prepare(self._request())

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.UNKNOWN)
        self.assertIsNone(result.draft)
        self.assertEqual(self._scancel_commands(), ["/usr/bin/scancel 44010"])

    def test_definite_scancel_rejection_opens_no_draft_and_is_not_retried(self) -> None:
        self.remote.scancel_result = RemoteCommandResult(1, b"", b"denied")

        with self.assertRaises(SlurmCancellationRejected):
            self.service.abort_and_prepare(self._request())

        self.assertEqual(self._scancel_commands(), ["/usr/bin/scancel 44010"])

    def test_terminal_race_returns_locked_draft_without_scancel(self) -> None:
        self.remote.squeue_stdout = b""
        self.remote.sacct_stdout = b"44010|CANCELLED|0:15\n"

        result = self.service.abort_and_prepare(self._request())

        self.assertIs(
            result.outcome,
            ProjectTaskCancellationOutcome.ALREADY_TERMINAL,
        )
        self.assertEqual(self._scancel_commands(), [])
        self.assertIsNotNone(result.draft)
        self.assertFalse(result.draft.source_terminal_confirmed)

    def test_changed_revision_fails_closed_before_read_or_scancel(self) -> None:
        changed = replace(self.project, revision=self.project.revision + 1)
        path = self.project.remote_project_path + "/.moltage/project.json"
        self.remote.files[path] = serialize_project_manifest(changed).encode("utf-8")

        with self.assertRaisesRegex(ProjectTaskRestartError, "revision changed"):
            self.service.abort_and_prepare(self._request())

        self.assertEqual(self._scancel_commands(), [])
        self.assertFalse(
            any(
                item[0] == "read" and item[1].endswith("/geometry.in")
                for item in self.remote.operations
            )
        )

    def test_input_hash_or_grammar_failure_occurs_before_scancel(self) -> None:
        path = self.project.remote_project_path + "/control.in"
        self.remote.files[path] = self.remote.files[path].replace(
            b"charge 0.\n",
            b"charge 0.0\n",
        )
        changed_hash = hashlib.sha256(self.remote.files[path]).hexdigest()
        hashes = dict(self.project.steps[0].input_hashes)
        hashes["control.in"] = changed_hash
        changed_step = replace(
            self.project.steps[0],
            input_hashes=tuple(hashes.items()),
        )
        changed_project = replace(
            self.project,
            revision=self.project.revision + 1,
            steps=(changed_step, *self.project.steps[1:]),
        )
        manifest = self.project.remote_project_path + "/.moltage/project.json"
        self.remote.files[manifest] = serialize_project_manifest(
            changed_project
        ).encode("utf-8")
        request = ProjectTaskRestartRequest(
            PROFILE,
            changed_project,
            ProjectStepKind.MOLECULE_OPT,
        )

        with self.assertRaisesRegex(
            ProjectTaskRestartError,
            "cannot be recovered exactly",
        ):
            self.service.abort_and_prepare(request)

        self.assertEqual(self._scancel_commands(), [])

    def test_step4_restart_recovers_step3_geometry_and_both_setting_surfaces(self) -> None:
        (
            project,
            files,
            structure,
            transport_settings,
            tcontrol_settings,
            execution,
        ) = _running_step4_project()
        remote = TaskRestartRemote()
        _install_transport_files(remote, project, files)
        remote.squeue_stdout = b"44012|RUNNING\n"
        service = ProjectTaskRestartService(FixedConnectionService(remote))

        result = service.abort_and_prepare(
            ProjectTaskRestartRequest(
                PROFILE,
                project,
                ProjectStepKind.TRANSMISSION,
            )
        )

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.REQUESTED)
        draft = result.draft
        self.assertIsNotNone(draft)
        self.assertTrue(draft.coordinate_only)
        self.assertEqual(draft.source_structure.atoms, structure.atoms)
        self.assertEqual(draft.transport_settings, transport_settings)
        self.assertEqual(draft.tcontrol_settings, tcontrol_settings)
        self.assertEqual(draft.step4_execution_settings, execution)
        self.assertIsNone(draft.tcontrol_self_energy_filename)
        self.assertEqual(len(draft.electrode_provenance), 2)
        self.assertEqual(
            tuple(item.roll_degrees for item in draft.electrode_provenance),
            tuple(item.roll_degrees for item in project.electrode_provenance),
        )
        self.assertEqual(
            [command for command in remote.commands if "scancel" in command],
            ["/usr/bin/scancel 44012"],
        )

    def test_step4_restart_recovers_provable_legacy_normal_mapping(self) -> None:
        project, files, structure, *_rest = _running_step4_project(legacy=True)
        remote = TaskRestartRemote()
        _install_transport_files(remote, project, files)
        remote.squeue_stdout = b"44012|RUNNING\n"
        service = ProjectTaskRestartService(FixedConnectionService(remote))

        result = service.abort_and_prepare(
            ProjectTaskRestartRequest(
                PROFILE,
                project,
                ProjectStepKind.TRANSMISSION,
            )
        )

        self.assertIs(result.outcome, ProjectTaskCancellationOutcome.REQUESTED)
        self.assertEqual(result.draft.source_structure.atoms, structure.atoms)
        self.assertEqual(
            tuple(
                item.geometry_model
                for item in result.draft.electrode_provenance
            ),
            ("LegacyAu59V1", "LegacyAu59V1"),
        )
        self.assertEqual(project.electrode_provenance, ())
        self.assertTrue(project.legacy_electrode_recovery_allowed)

    def test_step4_restart_preserves_schema_eight_lattice_extensions(self) -> None:
        project, files, structure, *_rest = _running_step4_project(extended=True)
        remote = TaskRestartRemote()
        _install_transport_files(remote, project, files)
        remote.squeue_stdout = b"44012|RUNNING\n"
        service = ProjectTaskRestartService(FixedConnectionService(remote))

        result = service.abort_and_prepare(
            ProjectTaskRestartRequest(
                PROFILE,
                project,
                ProjectStepKind.TRANSMISSION,
            )
        )

        self.assertEqual(result.draft.source_structure.atoms, structure.atoms)
        self.assertEqual(
            result.draft.electrode_provenance,
            project.electrode_provenance,
        )
        self.assertEqual(
            tuple(
                len(record.lattice_extensions)
                for record in result.draft.electrode_provenance
            ),
            (2, 1),
        )
        self.assertEqual(result.draft.tcontrol_settings.nlayers, 4)


if __name__ == "__main__":
    unittest.main()
