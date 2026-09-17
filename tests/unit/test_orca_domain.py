"""Synthetic ORCA catalog, settings, input, and evidence tests."""

import unittest

from moltage.domain.structure import Atom, MolecularStructure
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaOptimizationConvergence,
    OrcaScfConvergence,
    OrcaVersionFamily,
    methods_for_version,
    parse_orca_version_evidence,
)
from moltage.orca.evidence import (
    OrcaEvidenceError,
    OrcaFrequencyCompletion,
    OrcaImaginaryModeClassification,
    parse_orca_final_xyz,
    parse_orca_frequency_evidence,
    parse_orca_optimization_output,
)
from moltage.orca.input_writer import (
    parse_rendered_orca_structure,
    render_orca_frequency_input,
    render_orca_optimization_input,
)
from moltage.orca.settings import (
    OrcaFrequencySettings,
    OrcaOptimizationSettings,
    OrcaSettingsError,
    bases_for_structure,
)


def water():
    return MolecularStructure(
        (
            Atom(0, "O", 0.0, 0.0, 0.0),
            Atom(1, "H", 0.757, 0.586, 0.0),
            Atom(2, "H", -0.757, 0.586, 0.0),
        ),
        "synthetic water",
    )


def settings(**changes):
    values = dict(
        method=OrcaMethod.PBE0,
        basis=OrcaBasis.DEF2_TZVP,
        dispersion=OrcaDispersion.D4,
        charge=0,
        multiplicity=1,
        process_count=8,
        version_family=OrcaVersionFamily.V6_1,
    )
    values.update(changes)
    return OrcaOptimizationSettings(**values)


class OrcaDomainTests(unittest.TestCase):
    def test_version_evidence_and_catalog_filtering_are_literal(self):
        evidence = parse_orca_version_evidence(
            "Program Version 6.1.2 - synthetic", detection_source="test"
        )
        self.assertEqual(evidence.version, "6.1.2")
        self.assertIs(evidence.version_family, OrcaVersionFamily.V6_1)
        self.assertIn(OrcaMethod.WB97M_D4REV, methods_for_version(evidence.version_family))
        self.assertNotIn(OrcaMethod.WB97M_D4REV, methods_for_version(OrcaVersionFamily.V5_0))
        unknown = parse_orca_version_evidence("unparseable", detection_source="test")
        self.assertIsNone(unknown.version)
        unsupported = parse_orca_version_evidence("ORCA VERSION 7.0.0", detection_source="test")
        self.assertEqual(unsupported.version, "7.0.0")
        self.assertIsNone(unsupported.version_family)

    def test_required_choices_composite_locking_and_parity_fail_closed(self):
        with self.assertRaisesRegex(OrcaSettingsError, "Select an ORCA method"):
            OrcaOptimizationSettings().validate_for_structure(water())
        with self.assertRaisesRegex(OrcaSettingsError, "Select an orbital basis"):
            OrcaOptimizationSettings(
                method=OrcaMethod.PBE,
                version_family=OrcaVersionFamily.V6_0,
            ).validate_for_structure(water())
        with self.assertRaisesRegex(OrcaSettingsError, "composite method"):
            settings(method=OrcaMethod.B97_3C, basis=OrcaBasis.DEF2_SVP).validate_for_structure(water())
        with self.assertRaisesRegex(OrcaSettingsError, "incompatible with multiplicity"):
            settings(multiplicity=2).validate_for_structure(water())
        with self.assertRaises(ValueError):
            OrcaOptimizationSettings(method="PBE ! PAL8")
        self.assertIsNone(settings().max_core_mb)

    def test_reviewed_def2_bases_are_not_offered_beyond_radon(self):
        actinium = MolecularStructure((Atom(0, "Ac", 0.0, 0.0, 0.0),), "synthetic")
        self.assertEqual(bases_for_structure(actinium), ())
        with self.assertRaisesRegex(OrcaSettingsError, "documented only for elements H through Rn"):
            settings(charge=0, multiplicity=2).validate_for_structure(actinium)

    def test_deterministic_optimization_input_uses_reviewed_tokens_and_precision(self):
        configured = settings(
            optimization_convergence=OrcaOptimizationConvergence.TIGHTOPT,
            coordinate_system=OrcaCoordinateSystem.CARTESIAN,
            scf_convergence=OrcaScfConvergence.VERYTIGHTSCF,
            max_core_mb=1200,
        )
        expected = (
            "! PBE0 DEF2-TZVP D4 TIGHTOPT COPT VERYTIGHTSCF\n"
            "%pal\n  nprocs 8\nend\n"
            "%maxcore 1200\n"
            "* xyz 0 1\n"
            "  O 0.0 0.0 0.0\n"
            "  H 0.757 0.586 0.0\n"
            "  H -0.757 0.586 0.0\n"
            "*\n"
        )
        rendered = render_orca_optimization_input(water(), configured)
        self.assertEqual(rendered, expected)
        self.assertEqual(parse_rendered_orca_structure(rendered).atoms, water().atoms)

    def test_composite_and_wb97m_rendering_lock_dependent_tokens(self):
        composite = settings(
            method=OrcaMethod.B97_3C,
            basis=None,
            dispersion=OrcaDispersion.NONE,
        )
        self.assertTrue(render_orca_optimization_input(water(), composite).startswith("! B97-3C OPT\n"))
        wb97m = settings(
            method=OrcaMethod.WB97M_D4REV,
            dispersion=OrcaDispersion.NONE,
        )
        line = render_orca_optimization_input(water(), wb97m).splitlines()[0]
        self.assertEqual(line, "! WB97M-D4REV DEF2-TZVP OPT")

    def test_frequency_input_inherits_scientific_identity(self):
        source = settings(max_core_mb=900)
        frequency = OrcaFrequencySettings(
            source,
            source.scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
            process_count=4,
            max_core_mb=None,
        )
        rendered = render_orca_frequency_input(water(), frequency)
        self.assertEqual(rendered.splitlines()[0], "! PBE0 DEF2-TZVP D4 NUMFREQ")
        self.assertIn("  nprocs 4\n", rendered)
        self.assertNotIn("%maxcore", rendered)

    def test_optimization_evidence_keeps_termination_and_convergence_separate(self):
        successful = parse_orca_optimization_output(
            "THE OPTIMIZATION HAS CONVERGED\nORCA TERMINATED NORMALLY"
        )
        self.assertTrue(successful.normal_termination)
        self.assertTrue(successful.optimization_converged)
        not_converged = parse_orca_optimization_output(
            "MAXIMUM NUMBER OF OPTIMIZATION CYCLES REACHED\nORCA TERMINATED NORMALLY"
        )
        self.assertTrue(not_converged.normal_termination)
        self.assertFalse(not_converged.optimization_converged)
        self.assertTrue(not_converged.explicit_nonconvergence)

    def test_final_xyz_requires_exact_order_and_finite_coordinates(self):
        xyz = "3\noptimized\nO 0 0 0.1\nH 0.7 0.5 0\nH -0.7 0.5 0\n"
        parsed = parse_orca_final_xyz(xyz, water())
        self.assertEqual(tuple(atom.element for atom in parsed), ("O", "H", "H"))
        with self.assertRaisesRegex(OrcaEvidenceError, "expected O"):
            parse_orca_final_xyz(xyz.replace("O 0 0", "H 0 0", 1), water())
        with self.assertRaisesRegex(OrcaEvidenceError, "non-finite"):
            parse_orca_final_xyz(xyz.replace("0.1", "nan"), water())

    def test_frequency_evidence_uses_orca_annotation_and_hessian_3n(self):
        output = (
            "VIBRATIONAL FREQUENCIES\n"
            "0: 0.00 cm-1\n"
            "1: -12.30 cm-1 ***imaginary mode***\n"
            "ORCA TERMINATED NORMALLY\n"
        )
        hessian = "$hessian\n9\n0 1\n0 1.0\n$vibrational_frequencies\n2\n0 0.0\n1 -12.3\n"
        evidence = parse_orca_frequency_evidence(output, hessian, atom_count=3)
        self.assertIs(evidence.completion, OrcaFrequencyCompletion.FREQUENCY_COMPLETED)
        self.assertIs(
            evidence.imaginary_classification,
            OrcaImaginaryModeClassification.IMAGINARY_MODES_REPORTED,
        )
        unverified = parse_orca_frequency_evidence(output, hessian.replace("\n9\n", "\n6\n", 1), atom_count=3)
        self.assertIs(unverified.completion, OrcaFrequencyCompletion.UNVERIFIED)

    def test_frequency_evidence_reports_no_annotation_and_incomplete_artifacts(self):
        output = (
            "VIBRATIONAL FREQUENCIES\n"
            "0: -0.01 cm**-1\n"
            "1: 12.30 cm**-1\n"
            "ORCA TERMINATED NORMALLY\n"
        )
        hessian = "$hessian\n9\n0 1\n0 1.0\n$vibrational_frequencies\n2\n0 -0.01\n1 12.3\n"
        complete = parse_orca_frequency_evidence(output, hessian, atom_count=3)
        self.assertIs(complete.completion, OrcaFrequencyCompletion.FREQUENCY_COMPLETED)
        self.assertIs(
            complete.imaginary_classification,
            OrcaImaginaryModeClassification.NO_IMAGINARY_MODES_REPORTED,
        )
        missing = parse_orca_frequency_evidence(output, None, atom_count=3)
        self.assertIs(missing.completion, OrcaFrequencyCompletion.UNVERIFIED)
        self.assertIn("missing", missing.diagnostic)
        conflicting = parse_orca_frequency_evidence(
            output,
            hessian.replace("$vibrational_frequencies\n2", "$vibrational_frequencies\n3"),
            atom_count=3,
        )
        self.assertIs(conflicting.completion, OrcaFrequencyCompletion.UNVERIFIED)
        self.assertIn("mode count differs", conflicting.diagnostic)


if __name__ == "__main__":
    unittest.main()
