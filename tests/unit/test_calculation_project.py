from dataclasses import replace
from datetime import date, datetime, timezone
import itertools
import unittest
from uuid import uuid4

from moltage.app.project_planning import (
    ProjectPlanningError,
    StartStepAdvice,
    create_initial_project,
    plan_project_directory_name,
    project_directory_candidates,
    recommend_start_step,
    validate_project_base_name,
)
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    CalculationProjectValidationError,
    ProjectElectrodeLatticeExtension,
    ProjectRestartProvenance,
    ProjectStepKind,
    ProjectStepRecord,
    ProjectStepState,
    STEP_RELATIVE_FOLDERS,
    initial_step_records,
    required_initial_directories,
    workflow_step_kinds,
)
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.structure.anchor_detector import detect_anchors
from electrode_test_support import synthetic_project_electrode_provenance


class CalculationProjectTests(unittest.TestCase):
    def test_exact_four_steps_states_and_fixed_folders(self) -> None:
        self.assertEqual(
            workflow_step_kinds(CalculationWorkflowKind.FHI_AIMS_AITRANSS),
            (
                ProjectStepKind.MOLECULE_OPT,
                ProjectStepKind.MOLECULE_AU_OPT,
                ProjectStepKind.TRANSPORT_CONVERGENCE,
                ProjectStepKind.TRANSMISSION,
            ),
        )
        self.assertEqual(
            STEP_RELATIVE_FOLDERS,
            {
                ProjectStepKind.MOLECULE_OPT: ".",
                ProjectStepKind.MOLECULE_AU_OPT: "molecule_Au",
                ProjectStepKind.TRANSPORT_CONVERGENCE: "molecule_Au/transport",
                ProjectStepKind.TRANSMISSION: "molecule_Au/transport",
                ProjectStepKind.ORCA_OPTIMIZATION: ".",
                ProjectStepKind.ORCA_WBL_TRANSMISSION: "wbl",
                ProjectStepKind.ORCA_FREQUENCY: "frequency",
            },
        )
        self.assertEqual(
            workflow_step_kinds(CalculationWorkflowKind.ORCA),
            (ProjectStepKind.ORCA_OPTIMIZATION,),
        )
        self.assertEqual(
            workflow_step_kinds(
                CalculationWorkflowKind.ORCA,
                include_optional_frequency=True,
            ),
            (
                ProjectStepKind.ORCA_OPTIMIZATION,
                ProjectStepKind.ORCA_FREQUENCY,
            ),
        )
        self.assertEqual(
            workflow_step_kinds(
                CalculationWorkflowKind.ORCA,
                include_optional_frequency=True,
                include_optional_wbl=True,
            ),
            (
                ProjectStepKind.ORCA_OPTIMIZATION,
                ProjectStepKind.ORCA_WBL_TRANSMISSION,
                ProjectStepKind.ORCA_FREQUENCY,
            ),
        )
        self.assertEqual(
            tuple(ProjectStepState),
            (
                ProjectStepState.NOT_STARTED,
                ProjectStepState.SKIPPED,
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
                ProjectStepState.SCHEDULER_COMPLETED,
                ProjectStepState.SUCCEEDED,
                ProjectStepState.FAILED,
                ProjectStepState.UNKNOWN,
            ),
        )
        with self.assertRaises(CalculationProjectValidationError):
            ProjectStepRecord(
                ProjectStepKind.MOLECULE_AU_OPT,
                ProjectStepState.NOT_STARTED,
                "step2",
            )

    def test_step_record_requires_a_scheduler_output_filename(self) -> None:
        output_file = "aims.out"
        step = ProjectStepRecord(
            ProjectStepKind.MOLECULE_OPT,
            ProjectStepState.QUEUED,
            ".",
            job_id="12345",
            slurm_output_filename=output_file,
        )

        self.assertEqual(step.slurm_output_filename, output_file)
        for path in (
            "/srv/moltage-test/users/scientist/job-logs/aims.out",
            "job-logs/aims.out",
        ):
            with self.subTest(path=path), self.assertRaises(
                CalculationProjectValidationError
            ):
                ProjectStepRecord(
                    ProjectStepKind.MOLECULE_OPT,
                    ProjectStepState.QUEUED,
                    ".",
                    job_id="12345",
                    slurm_output_filename=path,
                )

    def test_initial_states_and_lazy_directories_for_step1_through_step3(self) -> None:
        step1 = initial_step_records(ProjectStepKind.MOLECULE_OPT)
        step2 = initial_step_records(ProjectStepKind.MOLECULE_AU_OPT)
        step3 = initial_step_records(ProjectStepKind.TRANSPORT_CONVERGENCE)
        self.assertEqual(
            tuple(item.state for item in step1),
            (ProjectStepState.NOT_STARTED,) * 4,
        )
        self.assertEqual(
            tuple(item.state for item in step2),
            (
                ProjectStepState.SKIPPED,
                ProjectStepState.NOT_STARTED,
                ProjectStepState.NOT_STARTED,
                ProjectStepState.NOT_STARTED,
            ),
        )
        self.assertEqual(
            tuple(item.state for item in step3),
            (
                ProjectStepState.SKIPPED,
                ProjectStepState.SKIPPED,
                ProjectStepState.NOT_STARTED,
                ProjectStepState.NOT_STARTED,
            ),
        )
        self.assertEqual(
            required_initial_directories(ProjectStepKind.MOLECULE_OPT),
            (".moltage",),
        )
        self.assertEqual(
            required_initial_directories(ProjectStepKind.MOLECULE_AU_OPT),
            (".moltage", "molecule_Au"),
        )
        self.assertEqual(
            required_initial_directories(ProjectStepKind.TRANSPORT_CONVERGENCE),
            (".moltage", "molecule_Au", "molecule_Au/transport"),
        )

    def test_project_naming_sequence_unicode_and_unsafe_rejection(self) -> None:
        fixed_date = date(2030, 1, 2)
        self.assertEqual(
            tuple(itertools.islice(project_directory_candidates("MoleculeA", fixed_date), 3)),
            (
                "MoleculeA.20300102",
                "MoleculeA.20300102_02",
                "MoleculeA.20300102_03",
            ),
        )
        self.assertEqual(
            plan_project_directory_name(
                "MoleculeA",
                fixed_date,
                {"MoleculeA.20300102", "MoleculeA.20300102_02"},
            ),
            "MoleculeA.20300102_03",
        )
        self.assertEqual(
            next(iter(project_directory_candidates("单分子A", fixed_date))),
            "单分子A.20300102",
        )
        for unsafe in ("", " ../x", "a/b", "a\\b", "a..b", "a;b", "a\nb"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ProjectPlanningError):
                    validate_project_base_name(unsafe)

    def test_start_step_advice_reuses_detected_attached_au_indices(self) -> None:
        no_au_structure = MolecularStructure(
            (
                Atom(0, "C", 0, 0, 0),
                Atom(1, "S", 1, 0, 0),
                Atom(2, "H", 2, 0, 0),
            )
        )
        no_au_connectivity = Connectivity(
            3,
            (Bond(0, 1, 1), Bond(1, 2, 1)),
        )
        no_au = recommend_start_step(
            no_au_structure,
            detect_anchors(no_au_structure, no_au_connectivity),
        )
        self.assertIs(no_au.advice, StartStepAdvice.STEP1)
        self.assertIs(no_au.recommended_step, ProjectStepKind.MOLECULE_OPT)
        self.assertTrue(no_au.confirmation_required)

        two_au_structure = MolecularStructure(
            (
                Atom(0, "C", 0, 0, 0),
                Atom(1, "S", 1, 0, 0),
                Atom(2, "Au", 2, 0, 0),
                Atom(3, "C", 10, 0, 0),
                Atom(4, "S", 11, 0, 0),
                Atom(5, "Au", 12, 0, 0),
            )
        )
        two_au_connectivity = Connectivity(
            6,
            (
                Bond(0, 1, 1),
                Bond(1, 2, 1),
                Bond(3, 4, 1),
                Bond(4, 5, 1),
            ),
        )
        anchors = detect_anchors(two_au_structure, two_au_connectivity)
        self.assertEqual(
            tuple(anchor.attached_au_indices for anchor in anchors),
            ((2,), (5,)),
        )
        two_au = recommend_start_step(two_au_structure, anchors)
        self.assertIs(two_au.advice, StartStepAdvice.STEP2)
        self.assertIs(two_au.recommended_step, ProjectStepKind.MOLECULE_AU_OPT)

        extra_au_structure = MolecularStructure(
            (*two_au_structure.atoms, Atom(6, "Au", 20, 0, 0))
        )
        extra_connectivity = Connectivity(7, two_au_connectivity.bonds)
        ambiguous = recommend_start_step(
            extra_au_structure,
            detect_anchors(extra_au_structure, extra_connectivity),
        )
        self.assertIs(ambiguous.advice, StartStepAdvice.AMBIGUOUS)
        self.assertIsNone(ambiguous.recommended_step)

    def test_initial_project_revision_identity_and_path(self) -> None:
        project_id = uuid4()
        profile_id = uuid4()
        now = datetime(2030, 1, 2, 10, 30, tzinfo=timezone.utc)
        project = create_initial_project(
            base_name="单分子A",
            remote_directory_name="单分子A.20300102",
            source_molecule_name="单分子A.xyz",
            server_profile_id=profile_id,
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.MOLECULE_AU_OPT,
            now=now,
            project_id=project_id,
        )
        self.assertEqual(project.project_id, project_id)
        self.assertEqual(project.server_profile_id, profile_id)
        self.assertEqual(project.revision, 1)
        self.assertEqual(project.remote_project_path, "/srv/moltage-test/projects/单分子A.20300102")
        self.assertIs(project.steps[0].state, ProjectStepState.SKIPPED)
        self.assertIs(project.starting_step, ProjectStepKind.MOLECULE_AU_OPT)

        with self.assertRaises(CalculationProjectValidationError):
            create_initial_project(
                base_name="MoleculeA",
                remote_directory_name="unsafe;name",
                source_molecule_name="MoleculeA.xyz",
                server_profile_id=profile_id,
                remote_project_root="/srv/moltage-test/projects",
                starting_step=ProjectStepKind.MOLECULE_OPT,
                now=now,
            )

    def test_direct_step3_requires_exact_two_cluster_provenance(self) -> None:
        first, second = synthetic_project_electrode_provenance(
            rolls=(12, 24)
        )
        project = create_initial_project(
            base_name="Imported",
            remote_directory_name="Imported.20300830",
            source_molecule_name="Imported.xyz",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 1, 6, 10, 30, tzinfo=timezone.utc),
            electrode_provenance=(first, second),
        )

        self.assertIs(project.starting_step, ProjectStepKind.TRANSPORT_CONVERGENCE)
        self.assertEqual(project.electrode_provenance, (first, second))
        self.assertEqual(
            tuple(step.state for step in project.steps),
            (
                ProjectStepState.SKIPPED,
                ProjectStepState.SKIPPED,
                ProjectStepState.NOT_STARTED,
                ProjectStepState.NOT_STARTED,
            ),
        )
        with self.assertRaisesRegex(
            CalculationProjectValidationError,
            "require persisted electrode provenance",
        ):
            create_initial_project(
                base_name="Imported",
                remote_directory_name="Imported.20300830",
                source_molecule_name="Imported.xyz",
                server_profile_id=uuid4(),
                remote_project_root="/srv/moltage-test/projects",
                starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
                now=datetime(2030, 1, 6, 10, 30, tzinfo=timezone.utc),
            )

    def test_extension_provenance_is_separate_and_may_be_asymmetric(self) -> None:
        first, second = synthetic_project_electrode_provenance()
        left_extensions = (
            ProjectElectrodeLatticeExtension(
                "LATTICE_EXTENSION",
                0,
                (-1, 0, 1),
                112,
            ),
            ProjectElectrodeLatticeExtension(
                "LATTICE_EXTENSION",
                0,
                (-1, 1, 0),
                114,
            ),
        )
        right_extensions = (
            ProjectElectrodeLatticeExtension(
                "LATTICE_EXTENSION",
                5,
                (-1, 0, 6),
                113,
            ),
        )
        project = create_initial_project(
            base_name="Extended",
            remote_directory_name="Extended.20300905",
            source_molecule_name="source.xyz",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 9, 5, tzinfo=timezone.utc),
            electrode_provenance=(
                replace(first, lattice_extensions=left_extensions),
                replace(second, lattice_extensions=right_extensions),
            ),
        )

        self.assertEqual(
            tuple(
                len(record.lattice_extensions)
                for record in project.electrode_provenance
            ),
            (2, 1),
        )
        self.assertEqual(
            len(project.electrode_provenance[0].atom_identities),
            56,
        )

    def test_extension_provenance_rejects_invalid_identity_and_mappings(self) -> None:
        first, second = synthetic_project_electrode_provenance()
        extension = ProjectElectrodeLatticeExtension(
            "LATTICE_EXTENSION",
            0,
            (-1, 0, 1),
            112,
        )
        with self.assertRaisesRegex(
            CalculationProjectValidationError,
            "standard core",
        ):
            replace(
                first,
                lattice_extensions=(
                    ProjectElectrodeLatticeExtension(
                        "LATTICE_EXTENSION",
                        0,
                        (0, 0, 0),
                        112,
                    ),
                ),
            )
        with self.assertRaisesRegex(
            CalculationProjectValidationError,
            "must not overlap",
        ):
            replace(
                first,
                lattice_extensions=(replace(extension, global_atom_index=2),),
            )
        left = replace(first, lattice_extensions=(extension,))
        right = replace(
            second,
            lattice_extensions=(replace(extension, lattice_key=(1, -1, 0)),),
        )
        with self.assertRaisesRegex(
            CalculationProjectValidationError,
            "mappings must not overlap",
        ):
            create_initial_project(
                base_name="Overlap",
                remote_directory_name="Overlap.20300905",
                source_molecule_name="source.xyz",
                server_profile_id=uuid4(),
                remote_project_root="/srv/moltage-test/projects",
                starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
                now=datetime(2030, 9, 5, tzinfo=timezone.utc),
                electrode_provenance=(left, right),
            )

    def test_restart_provenance_requires_the_scientifically_required_start(self) -> None:
        source_id = uuid4()
        provenance = ProjectRestartProvenance(
            source_id,
            ProjectStepKind.MOLECULE_AU_OPT,
            "44002",
            "b" * 64,
        )
        project = create_initial_project(
            base_name="RestartStep2",
            remote_directory_name="RestartStep2.20300901",
            source_molecule_name="source.geometry.in",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.MOLECULE_AU_OPT,
            now=datetime(2030, 9, 1, tzinfo=timezone.utc),
            restart_provenance=provenance,
        )

        self.assertEqual(project.restart_provenance, provenance)
        with self.assertRaisesRegex(
            CalculationProjectValidationError,
            "starting step does not match",
        ):
            create_initial_project(
                base_name="WrongRestart",
                remote_directory_name="WrongRestart.20300901",
                source_molecule_name="source.geometry.in",
                server_profile_id=uuid4(),
                remote_project_root="/srv/moltage-test/projects",
                starting_step=ProjectStepKind.MOLECULE_OPT,
                now=datetime(2030, 9, 1, tzinfo=timezone.utc),
                restart_provenance=provenance,
            )

        with self.assertRaisesRegex(
            CalculationProjectValidationError,
            "lowercase hex",
        ):
            ProjectRestartProvenance(
                source_id,
                ProjectStepKind.MOLECULE_OPT,
                "44003",
                "B" * 64,
            )


if __name__ == "__main__":
    unittest.main()
