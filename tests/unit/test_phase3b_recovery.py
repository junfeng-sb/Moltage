from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from uuid import UUID

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_planning import create_initial_project
from moltage.app.project_recovery import (
    ACTIVE_TASK_OOM_DETAIL,
    ProjectRecoveryService,
    StepRuntimeEvidence,
    slurm_failure_reason,
)
from moltage.aims.transport_evidence import SLURM_TASK_OUT_OF_MEMORY
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)
from moltage.remote.project_manifest import serialize_project_manifest
from moltage.remote.slurm import render_submit_script
from phase2b1_test_support import profile
from test_project_recovery import FixedConnectionService, RecoveryRemoteExecutor
from synthetic_structure_test_support import (
    synthetic_junction_provenance,
    synthetic_junction_structure,
)


NOW = datetime(2030, 1, 2, 10, 0, tzinfo=timezone.utc)
PROFILE = profile(profile_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
REVIEWED_TASK_OOM_OUTPUT = (
    b"[2030-01-02T03:04:05.006] error: Detected 1 oom_kill event in "
    b"StepId=41003.0. Some of the step tasks have been OOM Killed.\n"
    b"srun: error: compute001: task 7: Out Of Memory\n"
)
SYNTHETIC_DECORATED_CANCELLED_SACCT = (
    b"41003|CANCELLED by 200001|0:0\n"
    b"41003.batch|CANCELLED|0:15\n"
    b"41003.extern|COMPLETED|0:0\n"
    b"41003.0|OUT_OF_MEMORY|0:125\n"
)


def _structure():
    return synthetic_junction_structure()


def _geometry_bytes(structure):
    return (
        "\n".join(
            f"atom {atom.x:.10f} {atom.y:.10f} {atom.z:.10f} {atom.element}"
            for atom in structure
        )
        + "\n"
    ).encode()


def _project_and_files(state=ProjectStepState.RUNNING, *, spin="none"):
    structure = _structure()
    geometry = _geometry_bytes(structure)
    control = (
        f"spin {spin}\n"
        "species H\n  nucleus 1\n"
        "species C\n  nucleus 6\n"
        "species N\n  nucleus 7\n"
        "species S\n  nucleus 16\n"
        "species Au\n  nucleus 79\n"
    ).encode()
    base = create_initial_project(
        base_name="Phase3B",
        remote_directory_name="Phase3B.20300829",
        source_molecule_name="Phase3B.xyz",
        server_profile_id=PROFILE.profile_id,
        remote_project_root=PROFILE.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        now=NOW,
        project_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        electrode_provenance=synthetic_junction_provenance(),
    )
    step2 = replace(base.steps[1], state=ProjectStepState.SUCCEEDED)
    script = render_submit_script(
        PROFILE.execution_preset,
        base.project_id,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
    ).encode()
    step3 = replace(
        base.steps[2],
        state=state,
        job_id="41001",
        submitted_at=NOW,
        input_hashes=(
            ("geometry.in", hashlib.sha256(geometry).hexdigest()),
            ("control.in", hashlib.sha256(control).hexdigest()),
            ("submit.sh", hashlib.sha256(script).hexdigest()),
        ),
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    project = replace(
        base,
        revision=7,
        steps=(base.steps[0], step2, step3, base.steps[3]),
    )
    files = {
        "geometry.in": geometry,
        "control.in": control,
        "submit.sh": script,
        "aims.dft.out": (
            f"  | Number of atoms                   :      {len(structure)}\n".encode()
            + b"SCF details\nHave a nice day.\n"
        ),
        "basis-indices.out": b"basis\n",
        "omat.aims": b"large-placeholder\n",
    }
    if spin == "none":
        files["mos.aims"] = b"$scfmo.aims\n 1 a nsaos= 512\n"
    else:
        files["alpha.aims"] = b"$uhfmo_alpha\n nsaos=512\n"
        files["beta.aims"] = b"$uhfmo_beta\n nsaos=512\n"
    return project, files


def _synthetic_cancelled_project_and_files():
    project, files = _project_and_files()
    step3 = replace(project.steps[2], job_id="41003")
    return (
        replace(
            project,
            steps=(*project.steps[:2], step3, project.steps[3]),
        ),
        files,
    )


def _install(remote, project, files):
    root = project.remote_project_path
    metadata = root + "/.moltage"
    directory = remote_step_directory(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
    remote.directories.update((root, metadata, root + "/molecule_Au", directory))
    remote.files[metadata + "/project.json"] = serialize_project_manifest(project).encode()
    for name, data in files.items():
        remote.files[str(PurePosixPath(directory) / name)] = data


class Phase3BRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.remote = RecoveryRemoteExecutor()
        self.service = ProjectRecoveryService(
            FixedConnectionService(self.remote),
            LocalProjectIndexRepository(Path(self.temp.name) / "projects.json"),
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "phase3b",
        )

    def test_completed_nonspin_step3_requires_all_bounded_evidence_and_no_next_step(self):
        project, files = _project_and_files()
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step_kind, ProjectStepKind.TRANSPORT_CONVERGENCE)
        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)
        self.assertEqual(snapshot.transport_evidence.natoms, 128)
        self.assertEqual(snapshot.transport_evidence.nsaos, 512)
        self.assertEqual(snapshot.surface_proposal.left_one_based, (73, 58, 53))
        self.assertEqual(snapshot.surface_proposal.right_one_based, (128, 113, 108))
        self.assertFalse(any("geometry.in.next_step" in str(item) for item in self.remote.operations))
        self.assertTrue(any(item[0] == "head" and item[1].endswith("mos.aims") for item in self.remote.operations))
        self.assertFalse(any(item[0] == "read" and item[1].endswith("omat.aims") for item in self.remote.operations))

    def test_completed_without_normal_marker_is_exact_unknown(self):
        project, files = _project_and_files()
        files["aims.dft.out"] = b"| Number of atoms : 128\n"
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.last_error, "unknown")

    def test_completed_missing_mos_has_concrete_reason(self):
        project, files = _project_and_files()
        del files["mos.aims"]
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertEqual(
            snapshot.active_step.last_error,
            "Missing required Step-3 output: mos.aims",
        )

    def test_output_natoms_must_match_geometry_atom_records(self):
        project, files = _project_and_files()
        files["aims.dft.out"] = (
            b"  | Number of atoms                   :      127\n"
            b"SCF details\nHave a nice day.\n"
        )
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|COMPLETED|0:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(
            snapshot.active_step.last_error,
            "Step-3 atom count mismatch: aims.dft.out reports 127, but "
            "geometry.in contains 128 atom records",
        )
        self.assertIsNone(snapshot.transport_evidence)

    def test_parent_timeout_has_exact_primary_reason_without_science_assessment(self):
        project, files = _project_and_files()
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|TIMEOUT|0:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.last_error, "运行时间到达设定上限")
        self.assertEqual(snapshot.active_step.scheduler_state, "TIMEOUT")
        self.assertFalse(any(item[0] in {"head", "tail"} for item in self.remote.operations))

    def test_parent_out_of_memory_is_terminal_with_memory_reason(self):
        project, files = _project_and_files()
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|OUT_OF_MEMORY|0:125\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.job_id, "41001")
        self.assertEqual(snapshot.active_step.scheduler_state, "OUT_OF_MEMORY")
        self.assertEqual(snapshot.active_step.last_error, "OUT_OF_MEMORY")
        self.assertEqual(snapshot.status_message, "任务因内存不足终止")
        self.assertEqual(
            slurm_failure_reason(snapshot.active_step.last_error),
            "任务因内存不足终止",
        )
        self.assertIsNotNone(snapshot.active_step.finished_at)
        self.assertFalse(any(item[0] == "tail" for item in self.remote.operations))

    def test_parent_failed_with_exact_manifest_output_gets_typed_task_oom(self):
        project, files = _project_and_files()
        step3 = replace(
            project.steps[2],
            slurm_output_filename="aims.dft.retry02.out",
        )
        project = replace(project, steps=(*project.steps[:2], step3, project.steps[3]))
        files["aims.dft.out"] = b"decoy ordinary application failure\n"
        files["aims.dft.retry02.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|FAILED|1:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.scheduler_state, "FAILED")
        self.assertEqual(
            snapshot.active_step.last_error,
            SLURM_TASK_OUT_OF_MEMORY,
        )
        self.assertEqual(snapshot.status_message, "任务因内存不足终止")
        tails = [item[1] for item in self.remote.operations if item[0] == "tail"]
        self.assertEqual(
            tails,
            [
                project.remote_project_path
                + "/molecule_Au/transport/aims.dft.retry02.out"
            ],
        )

    def test_running_parent_reports_derived_oom_without_changing_scheduler_state(self):
        project, files = _project_and_files()
        files["aims.dft.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(self.remote, project, files)
        self.remote.squeue_stdout = b"41001|RUNNING\n"
        manifest = project.remote_project_path + "/.moltage/project.json"
        before = self.remote.files[manifest]

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertIsNone(snapshot.active_step.last_error)
        self.assertIs(
            snapshot.runtime_evidence,
            StepRuntimeEvidence.TASK_OOM_DETECTED,
        )
        self.assertEqual(snapshot.status_message, ACTIVE_TASK_OOM_DETAIL)
        self.assertTrue(snapshot.can_kill_step3_oom)
        self.assertFalse(snapshot.can_retry_step3)
        self.assertEqual(self.remote.files[manifest], before)
        self.assertEqual(
            [item[1] for item in self.remote.operations if item[0] == "tail"],
            [
                project.remote_project_path
                + "/molecule_Au/transport/aims.dft.out"
            ],
        )
        self.assertFalse(any("sacct" in command for command in self.remote.commands))

    def test_running_parent_without_exact_pair_remains_ordinary_running(self):
        project, files = _project_and_files()
        files["aims.dft.out"] = (
            b"memory usage high; possible OOM risk; allocation memory warning\n"
        )
        _install(self.remote, project, files)
        self.remote.squeue_stdout = b"41001|RUNNING\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.RUNNING)
        self.assertIsNone(snapshot.runtime_evidence)
        self.assertEqual(snapshot.status_message, "Slurm reports RUNNING.")
        self.assertFalse(snapshot.can_kill_step3_oom)

    def test_running_oom_reads_only_manifest_selected_current_attempt_output(self):
        project, files = _project_and_files()
        step3 = replace(
            project.steps[2],
            slurm_output_filename="aims.dft.retry02.out",
        )
        project = replace(project, steps=(*project.steps[:2], step3, project.steps[3]))
        files["aims.dft.out"] = b"decoy ordinary output\n"
        files["aims.dft.retry02.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(self.remote, project, files)
        self.remote.squeue_stdout = b"41001|RUNNING\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(
            snapshot.runtime_evidence,
            StepRuntimeEvidence.TASK_OOM_DETECTED,
        )
        self.assertEqual(
            [item[1] for item in self.remote.operations if item[0] == "tail"],
            [
                project.remote_project_path
                + "/molecule_Au/transport/aims.dft.retry02.out"
            ],
        )

    def test_cancelled_parent_with_exact_current_output_preserves_both_facts(self):
        project, files = _project_and_files()
        files["aims.dft.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|CANCELLED|0:15\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.scheduler_state, "CANCELLED")
        self.assertEqual(snapshot.active_step.last_error, SLURM_TASK_OUT_OF_MEMORY)
        self.assertEqual(snapshot.status_message, "任务因内存不足终止")
        self.assertTrue(snapshot.can_retry_step3)
        self.assertIsNotNone(snapshot.step3_retry_preset)

    def test_synthetic_decorated_cancelled_parent_with_oom_enables_retry(self):
        project, files = _synthetic_cancelled_project_and_files()
        files["aims.dft.out"] = REVIEWED_TASK_OOM_OUTPUT
        _install(self.remote, project, files)
        self.remote.sacct_stdout = SYNTHETIC_DECORATED_CANCELLED_SACCT

        snapshot = self.service.refresh_project(
            PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.job_id, "41003")
        self.assertEqual(snapshot.active_step.scheduler_state, "CANCELLED")
        self.assertEqual(
            snapshot.active_step.last_error,
            SLURM_TASK_OUT_OF_MEMORY,
        )
        self.assertEqual(snapshot.status_message, "任务因内存不足终止")
        self.assertFalse(snapshot.can_kill_step3_oom)
        self.assertTrue(snapshot.can_retry_step3)
        self.assertIsNotNone(snapshot.step3_retry_preset)
        self.assertEqual(snapshot.step3_retry_preset.memory_gb, 128)

    def test_synthetic_decorated_cancelled_without_oom_is_not_retryable(self):
        project, files = _synthetic_cancelled_project_and_files()
        _install(self.remote, project, files)
        self.remote.sacct_stdout = SYNTHETIC_DECORATED_CANCELLED_SACCT

        snapshot = self.service.refresh_project(
            PROFILE,
            project.remote_project_path,
        )

        self.assertIs(snapshot.active_step.state, ProjectStepState.FAILED)
        self.assertEqual(snapshot.active_step.scheduler_state, "CANCELLED")
        self.assertEqual(snapshot.active_step.last_error, "CANCELLED")
        self.assertEqual(snapshot.status_message, "CANCELLED")
        self.assertIsNone(snapshot.runtime_evidence)
        self.assertFalse(snapshot.can_kill_step3_oom)
        self.assertFalse(snapshot.can_retry_step3)

    def test_retry_resources_come_from_hash_bound_attempt_script_not_profile(self):
        project, files = _project_and_files()
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|OUT_OF_MEMORY|0:125\n"
        drifted_profile = replace(
            PROFILE,
            execution_preset=replace(PROFILE.execution_preset, memory_gb=64),
        )

        snapshot = self.service.refresh_project(
            drifted_profile,
            project.remote_project_path,
        )

        self.assertEqual(snapshot.step3_retry_preset.memory_gb, 128)
        self.assertEqual(
            snapshot.step3_retry_preset.launch_command,
            drifted_profile.execution_preset.launch_command,
        )

    def test_retry_resource_hash_mismatch_has_no_profile_fallback(self):
        project, files = _project_and_files()
        hashes = dict(project.steps[2].input_hashes)
        hashes["submit.sh"] = "0" * 64
        step3 = replace(project.steps[2], input_hashes=tuple(hashes.items()))
        project = replace(project, steps=(*project.steps[:2], step3, project.steps[3]))
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|OUT_OF_MEMORY|0:125\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertTrue(snapshot.can_retry_step3)
        self.assertIsNone(snapshot.step3_retry_preset)
        self.assertIn("submit script changed", snapshot.step3_retry_preparation_error)

    def test_other_terminal_failure_is_exact_unknown(self):
        project, files = _project_and_files()
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|FAILED|1:0\n"

        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)

        self.assertEqual(snapshot.active_step.last_error, "unknown")
        self.assertEqual(snapshot.active_step.scheduler_state, "FAILED")
        self.assertTrue(any(item[0] == "tail" for item in self.remote.operations))

    def test_spin_success_requires_matching_alpha_beta_headers(self):
        project, files = _project_and_files(spin="collinear")
        _install(self.remote, project, files)
        self.remote.sacct_stdout = b"41001|COMPLETED|0:0\n"
        snapshot = self.service.refresh_project(PROFILE, project.remote_project_path)
        self.assertIs(snapshot.active_step.state, ProjectStepState.SUCCEEDED)

        project2, files2 = _project_and_files(spin="collinear")
        files2["beta.aims"] = b"nsaos=6819\n"
        project2 = replace(project2, revision=20)
        self.remote = RecoveryRemoteExecutor()
        _install(self.remote, project2, files2)
        self.remote.sacct_stdout = b"41001|COMPLETED|0:0\n"
        service = ProjectRecoveryService(
            FixedConnectionService(self.remote),
            LocalProjectIndexRepository(Path(self.temp.name) / "projects2.json"),
            now_factory=lambda: NOW,
        )
        mismatch = service.refresh_project(PROFILE, project2.remote_project_path)
        self.assertIn("alpha/beta NSAOS", mismatch.active_step.last_error)


if __name__ == "__main__":
    unittest.main()
