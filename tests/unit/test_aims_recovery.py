from pathlib import Path
import unittest

from moltage.aims.input_bundle import build_aims_optimization_inputs
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AtomAimsSettings,
    SpeciesAccuracy,
)
from moltage.aims.recovery import (
    GEOMETRY_CONVERGENCE_MARKER,
    NORMAL_TERMINATION_MARKER,
    AimsOutputAssessmentKind,
    AimsRecoveryError,
    assess_geometry_optimization_output,
    parse_control_species_elements,
    parse_molecular_geometry,
    recover_optimized_structure,
    recover_submitted_structure,
)
from species_test_support import synthetic_species_library
from moltage.domain.structure import Atom, MolecularStructure
from moltage.remote.slurm import (
    SlurmSubmissionError,
    parse_submit_script_output_filename,
    render_submit_script,
)
from moltage.domain.calculation_project import ProjectStepKind
from phase2b1_test_support import synthetic_slurm_preset
from test_slurm import PROJECT_ID


class AimsRecoveryTests(unittest.TestCase):
    def test_output_requires_normal_termination_and_tracks_convergence_marker(self):
        success = assess_geometry_optimization_output(
            f"{GEOMETRY_CONVERGENCE_MARKER}\n...\n{NORMAL_TERMINATION_MARKER}\n"
        )
        normal_without_convergence = assess_geometry_optimization_output(
            f"SCF converged\n{NORMAL_TERMINATION_MARKER}\n"
        )
        abnormal = assess_geometry_optimization_output(
            f"{GEOMETRY_CONVERGENCE_MARKER}\nfinished\n"
        )

        self.assertIs(success.kind, AimsOutputAssessmentKind.SUCCEEDED)
        self.assertIs(
            normal_without_convergence.kind,
            AimsOutputAssessmentKind.SUCCEEDED,
        )
        self.assertTrue(success.geometry_convergence_detected)
        self.assertFalse(
            normal_without_convergence.geometry_convergence_detected
        )
        self.assertIs(
            abnormal.kind,
            AimsOutputAssessmentKind.NORMAL_TERMINATION_MISSING,
        )
        self.assertTrue(abnormal.geometry_convergence_detected)

    def test_synthetic_control_recovers_light_carbon_and_heavy_au_aliases(self):
        structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "Au", 2.0, 0.0, 0.0),
            )
        )
        settings = AimsOptimizationSettings(
            atom_settings=(
                AtomAimsSettings(0, species_accuracy=SpeciesAccuracy.LIGHT),
                AtomAimsSettings(
                    1,
                    species_accuracy=SpeciesAccuracy.REALLY_TIGHT,
                ),
            )
        )
        bundle = build_aims_optimization_inputs(
            structure,
            settings,
            synthetic_species_library(),
        )

        mapping = parse_control_species_elements(bundle.control_text)

        self.assertEqual(mapping["C_light"], "C")
        self.assertEqual(mapping["Au_really_tight"], "Au")
        self.assertNotIn("light", mapping.values())

    def test_geometry_comments_annotations_and_trailing_hessian_comments_are_safe(self):
        text = (
            "#\n"
            "# This is the geometry file for the current relaxation step.\n"
            "atom 0.0 0.1 0.2 C_alias\n"
            "initial_moment 1.0  # collinear seed\n"
            "initial_charge -0.25 # atom annotation\n"
            "atom 2.0 3.0 4.0 Au_alias\n"
            "# Hessian comments follow\n"
            "# 1.0 2.0 3.0\n"
        )

        structure = parse_molecular_geometry(
            text,
            {"C_alias": "C", "Au_alias": "Au"},
            source_name="geometry.in.next_step",
        )

        self.assertEqual(tuple(atom.index for atom in structure), (0, 1))
        self.assertEqual(tuple(atom.element for atom in structure), ("C", "Au"))
        self.assertEqual(
            (structure[0].x, structure[0].y, structure[0].z),
            (0.0, 0.1, 0.2),
        )
        self.assertEqual(
            (structure[1].x, structure[1].y, structure[1].z),
            (2.0, 3.0, 4.0),
        )

    def test_synthetic_next_step_stops_after_sixteen_atoms(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "phase2c"
            / "synthetic_dual_ncs.geometry.in.next_step"
        )

        structure = parse_molecular_geometry(
            fixture.read_bytes(),
            {"C": "C", "N": "N", "S": "S", "H": "H"},
            source_name="geometry.in.next_step",
        )

        self.assertEqual(len(structure), 16)
        self.assertEqual(
            tuple(atom.element for atom in structure),
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

    def test_trust_radius_stops_before_all_future_metadata(self):
        text = (
            "atom 0 0 0 C_alias\n"
            "atom 1 0 0 N_alias\n"
            "trust_radius 0.4\n"
            "completely_unknown_future_metadata xyz\n"
            "another_unknown_directive\n"
        )

        structure = parse_molecular_geometry(
            text,
            {"C_alias": "C", "N_alias": "N"},
            source_name="geometry.in.next_step",
        )

        self.assertEqual(tuple(atom.element for atom in structure), ("C", "N"))

    def test_unknown_directive_before_trust_radius_remains_an_error(self):
        text = (
            "atom 0 0 0 C_alias\n"
            "imaginary_directive 123\n"
            "trust_radius 0.4\n"
        )

        with self.assertRaisesRegex(AimsRecoveryError, "imaginary_directive"):
            parse_molecular_geometry(text, {"C_alias": "C"})

    def test_trust_radius_before_any_atom_remains_an_error(self):
        with self.assertRaisesRegex(AimsRecoveryError, "trust_radius"):
            parse_molecular_geometry(
                "trust_radius 0.4\natom 0 0 0 C_alias\n",
                {"C_alias": "C"},
            )

    def test_geometry_rejects_periodic_unknown_nonfinite_and_unmapped_records(self):
        cases = (
            "lattice_vector 1 0 0\n",
            "atom_frac 0 0 0 C_alias\n",
            "velocity 0 0 0\n",
            "atom nan 0 0 C_alias\n",
            "atom 0 0 0 Unknown_alias\n",
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(AimsRecoveryError):
                parse_molecular_geometry(text, {"C_alias": "C"})

    def test_control_parser_rejects_ambiguous_or_unsupported_species_evidence(self):
        cases = (
            "species C_alias\n",
            "species C_alias\n nucleus 6\n nucleus 7\n",
            "species C_alias\n nucleus 0\n",
            "species C_alias\n nucleus 6\nspecies C_alias\n nucleus 7\n",
            "nucleus 6\nspecies C_alias\n nucleus 6\n",
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(AimsRecoveryError):
                parse_control_species_elements(text)

    def test_optimized_coordinates_may_change_but_ordered_chemistry_may_not(self):
        control = "species C_alias\n nucleus 6\nspecies N_alias\n nucleus 7\n"
        original = "atom 0 0 0 C_alias\natom 1 0 0 N_alias\n"
        optimized = "atom 0.25 0 0 C_alias\natom 1.25 0 0 N_alias\n"

        recovered = recover_optimized_structure(
            control_text=control,
            original_geometry_text=original,
            next_geometry_text=optimized,
        )

        self.assertEqual(tuple(atom.element for atom in recovered), ("C", "N"))
        self.assertEqual((recovered[0].x, recovered[1].x), (0.25, 1.25))

    def test_submitted_geometry_helper_uses_the_same_control_alias_evidence(self):
        recovered = recover_submitted_structure(
            control_text=(
                "species C_alias\n nucleus 6\n"
                "species N_alias\n nucleus 7\n"
            ),
            geometry_text=(
                "atom 0 0 0 C_alias\n"
                "atom 1 0 0 N_alias\n"
            ),
            source_name="manual.in",
        )

        self.assertEqual(tuple(atom.element for atom in recovered), ("C", "N"))
        self.assertEqual(recovered.comment, "Recovered from manual.in")

    def test_submitted_geometry_without_control_accepts_only_element_species(self):
        recovered = recover_submitted_structure(
            geometry_text="atom 0 0 0 C\natom 1 0 0 Au\n",
        )

        self.assertEqual(tuple(atom.element for atom in recovered), ("C", "Au"))
        with self.assertRaisesRegex(AimsRecoveryError, "C_alias"):
            recover_submitted_structure(
                geometry_text="atom 0 0 0 C_alias\n",
            )

    def test_missing_changed_or_reordered_atoms_are_rejected(self):
        control = "species C_alias\n nucleus 6\nspecies N_alias\n nucleus 7\n"
        original = "atom 0 0 0 C_alias\natom 1 0 0 N_alias\n"
        invalid = (
            "atom 0 0 0 C_alias\n",
            "atom 0 0 0 C_alias\natom 1 0 0 C_alias\n",
            "atom 0 0 0 N_alias\natom 1 0 0 C_alias\n",
        )
        for optimized in invalid:
            with self.subTest(optimized=optimized), self.assertRaisesRegex(
                AimsRecoveryError,
                "inconsistent",
            ):
                recover_optimized_structure(
                    control_text=control,
                    original_geometry_text=original,
                    next_geometry_text=optimized,
                )

    def test_submit_script_output_filename_is_historical_and_generic(self):
        accepted = render_submit_script(
            synthetic_slurm_preset(),
            PROJECT_ID,
            ProjectStepKind.MOLECULE_OPT,
        )
        synthetic = "#!/bin/bash\n#SBATCH --output=calculation.out\nsrun aims.x\n"
        legacy_lsf = "#!/bin/bash\n#BSUB -oo legacy.out\nmpirun aims.x\n"
        current_lsf = (
            "#!/bin/bash\n#BSUB -oo calculation.out.lsf.log\n"
            "exec > calculation.out 2>&1\nexec mpirun aims.x\n"
        )

        self.assertEqual(
            parse_submit_script_output_filename(accepted),
            "aims.dft.out",
        )
        self.assertEqual(
            parse_submit_script_output_filename(synthetic),
            "calculation.out",
        )
        self.assertEqual(
            parse_submit_script_output_filename(legacy_lsf),
            "legacy.out",
        )
        self.assertEqual(
            parse_submit_script_output_filename(current_lsf),
            "calculation.out",
        )

    def test_submit_script_output_filename_rejects_missing_duplicate_and_unsafe(self):
        invalid = (
            "#!/bin/bash\nsrun aims.x\n",
            "#SBATCH --output=one.out\n#SBATCH --output=two.out\n",
            "#SBATCH --output=../aims.out\n",
            "#SBATCH --output=$(touch_bad)\n",
            "#SBATCH --output=has space.out\n",
            (
                "#BSUB -oo unrelated.lsf.log\n"
                "exec > aims.out 2>&1\n"
                "exec mpirun aims.x\n"
            ),
        )
        for script in invalid:
            with self.subTest(script=script), self.assertRaises(
                SlurmSubmissionError
            ):
                parse_submit_script_output_filename(script)


if __name__ == "__main__":
    unittest.main()
