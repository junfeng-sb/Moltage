from dataclasses import replace
import hashlib
import unittest

from species_test_support import synthetic_species_library
from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.transport_convergence_bundle import (
    build_transport_convergence_aims_inputs,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.app.transport_convergence import (
    TransportConvergenceContext,
    TransportConvergenceEligibilityError,
    prepare_transport_convergence_submission_bundle,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.server_profile import SlurmMailSettings
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from phase2b1_test_support import synthetic_slurm_preset
from synthetic_structure_test_support import (
    synthetic_extended_electrode_placement,
    synthetic_step2_state,
)
from test_project_recovery import _project


class TransportConvergenceContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        structure, connectivity, _anchors, sites = synthetic_step2_state()
        proposal = propose_electrode_placement(
            structure,
            connectivity,
            sites,
            sites,
        )
        cls.source = structure
        cls.connectivity = connectivity
        cls.applied = apply_electrode_placement(
            structure,
            connectivity,
            proposal,
        )
        cls.project = _project(
            ProjectStepState.SUCCEEDED,
            step_kind=ProjectStepKind.MOLECULE_AU_OPT,
        )

    def _snapshot(self, project=None):
        return ProjectRecoverySnapshot(
            project or self.project,
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=self.source,
            connectivity=self.connectivity,
        )

    def test_exact_recovered_step2_and_applied_pair_are_eligible(self):
        context = TransportConvergenceContext(
            self._snapshot(),
            self.source,
            self.applied.structure,
            self.applied,
        )

        self.assertEqual(context.project, self.project)
        self.assertEqual(context.working_structure, self.applied.structure)
        self.assertEqual(len(context.applied_electrodes.proposal.clusters), 2)

    def test_extended_electrode_provenance_enters_normal_step3_unchanged(self):
        applied = synthetic_extended_electrode_placement()
        context = TransportConvergenceContext(
            self._snapshot(),
            self.source,
            applied.structure,
            applied,
        )

        provenance = context.electrode_provenance

        self.assertEqual(
            tuple(len(record.lattice_extensions) for record in provenance),
            (2, 1),
        )
        self.assertEqual(
            tuple(
                extension.global_atom_index
                for record in provenance
                for extension in record.lattice_extensions
            ),
            (128, 130, 129),
        )
        self.assertEqual(context.working_structure, applied.structure)

    def test_coordinate_edited_electrode_structure_is_exact_step3_geometry(self):
        atoms = list(self.applied.structure.atoms)
        atom = atoms[-1]
        atoms[-1] = Atom(
            atom.index,
            atom.element,
            atom.x,
            atom.y + 0.25,
            atom.z,
        )
        edited = MolecularStructure(tuple(atoms))
        context = TransportConvergenceContext(
            self._snapshot(),
            self.source,
            edited,
            self.applied,
        )

        aims_inputs = build_transport_convergence_aims_inputs(
            context.working_structure,
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )

        self.assertIs(context.working_structure, edited)
        self.assertIs(context.applied_electrodes, self.applied)
        self.assertEqual(aims_inputs.geometry_text, render_geometry_in(edited))
        self.assertNotEqual(
            aims_inputs.geometry_text,
            render_geometry_in(self.applied.structure),
        )

    def test_coordinate_edit_cannot_change_electrode_atom_identity(self):
        atoms = list(self.applied.structure.atoms)
        atom = atoms[-1]
        atoms[-1] = Atom(atom.index, "Ag", atom.x, atom.y, atom.z)

        with self.assertRaisesRegex(
            TransportConvergenceEligibilityError,
            "coordinates only",
        ):
            TransportConvergenceContext(
                self._snapshot(),
                self.source,
                MolecularStructure(tuple(atoms)),
                self.applied,
            )

    def test_ordinary_or_step1_source_is_ineligible(self):
        step1 = _project(ProjectStepState.SUCCEEDED)
        snapshot = ProjectRecoverySnapshot(
            step1,
            ProjectStepKind.MOLECULE_OPT,
            "Step 1 completed successfully.",
            optimized_structure=self.source,
            connectivity=self.connectivity,
        )

        with self.assertRaisesRegex(
            TransportConvergenceEligibilityError,
            "recovered Step-2",
        ):
            TransportConvergenceContext(
                snapshot,
                self.source,
                self.applied.structure,
                self.applied,
            )

    def test_recovered_step2_without_done_applied_electrodes_is_ineligible(self):
        for scenario in (
            "before electrode selection",
            "one electrode previewed",
            "both electrodes previewed without Done",
        ):
            with self.subTest(scenario=scenario):
                with self.assertRaisesRegex(
                    TransportConvergenceEligibilityError,
                    "Done",
                ):
                    TransportConvergenceContext(
                        self._snapshot(),
                        self.source,
                        self.source,
                        None,
                    )

    def test_unrelated_viewer_source_is_ineligible(self):
        first = self.source[0]
        unrelated = MolecularStructure(
            (
                Atom(first.index, first.element, first.x + 0.5, first.y, first.z),
                *self.source.atoms[1:],
            )
        )

        with self.assertRaisesRegex(
            TransportConvergenceEligibilityError,
            "viewer source",
        ):
            TransportConvergenceContext(
                self._snapshot(),
                unrelated,
                self.applied.structure,
                self.applied,
            )

    def test_existing_step3_submission_state_is_ineligible(self):
        step3 = self.project.steps[2]
        queued = replace(
            step3,
            state=ProjectStepState.QUEUED,
            job_id="33333",
            submitted_at=self.project.updated_at,
        )
        project = replace(
            self.project,
            steps=(
                self.project.steps[0],
                self.project.steps[1],
                queued,
                self.project.steps[3],
            ),
            electrode_provenance=provenance_from_applied_electrodes(
                self.applied
            ),
        )

        with self.assertRaisesRegex(
            TransportConvergenceEligibilityError,
            "already has",
        ):
            TransportConvergenceContext(
                self._snapshot(project),
                self.source,
                self.applied.structure,
                self.applied,
            )

    def test_complete_submission_bundle_contains_three_hashed_files(self):
        context = TransportConvergenceContext(
            self._snapshot(),
            self.source,
            self.applied.structure,
            self.applied,
        )
        aims_inputs = build_transport_convergence_aims_inputs(
            context.working_structure,
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )

        bundle = prepare_transport_convergence_submission_bundle(
            aims_inputs,
            synthetic_slurm_preset(),
            context.project.project_id,
        )

        self.assertEqual(
            tuple(bundle.files()),
            ("geometry.in", "control.in", "submit.sh"),
        )
        self.assertEqual(
            dict(bundle.input_hashes),
            {
                filename: hashlib.sha256(data).hexdigest()
                for filename, data in bundle.files().items()
            },
        )
        submit = bundle.submit_bytes.decode("utf-8")
        self.assertIn("#SBATCH --job-name=AT-", submit)
        self.assertIn("-S3\n", submit)
        self.assertIn("#SBATCH --output=aims.dft.out", submit)
        self.assertEqual(submit.count("--kill-on-bad-exit=1"), 1)
        self.assertEqual(
            submit.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )
        self.assertNotIn("aims.restart", bundle.files())

    def test_mail_changes_only_step3_scheduler_metadata(self):
        context = TransportConvergenceContext(
            self._snapshot(),
            self.source,
            self.applied.structure,
            self.applied,
        )
        aims_inputs = build_transport_convergence_aims_inputs(
            context.working_structure,
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )
        disabled = prepare_transport_convergence_submission_bundle(
            aims_inputs,
            synthetic_slurm_preset(),
            context.project.project_id,
        )
        enabled = prepare_transport_convergence_submission_bundle(
            aims_inputs,
            synthetic_slurm_preset(),
            context.project.project_id,
            mail_settings=SlurmMailSettings("user@example.com"),
        )

        self.assertEqual(enabled.geometry_bytes, disabled.geometry_bytes)
        self.assertEqual(enabled.control_bytes, disabled.control_bytes)
        self.assertEqual(
            dict(enabled.input_hashes)["geometry.in"],
            dict(disabled.input_hashes)["geometry.in"],
        )
        self.assertEqual(
            dict(enabled.input_hashes)["control.in"],
            dict(disabled.input_hashes)["control.in"],
        )
        enabled_submit = enabled.submit_bytes.decode("utf-8")
        disabled_submit = disabled.submit_bytes.decode("utf-8")
        self.assertIn("#SBATCH --mail-user=user@example.com", enabled_submit)
        self.assertIn("#SBATCH --mail-type=END,FAIL", enabled_submit)
        self.assertNotIn("--mail-user", disabled_submit)
        self.assertNotIn("--mail-type", disabled_submit)


if __name__ == "__main__":
    unittest.main()
