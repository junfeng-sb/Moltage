import re
import unittest

from moltage.aims.input_bundle import build_aims_optimization_inputs
from moltage.aims.input_parser import (
    AimsInputParsingError,
    parse_generated_optimization_inputs,
    parse_generated_transport_convergence_inputs,
)
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AtomAimsSettings,
    Relativity,
    SpeciesAccuracy,
    SpinInitializationMode,
    SpinSettings,
    VdwMethod,
    XCFunctional,
)
from moltage.aims.transport_convergence_bundle import (
    build_transport_convergence_aims_inputs,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.aims.orbital_cube import (
    FrontierOrbital,
    OrbitalCubeOutputSettings,
)
from moltage.domain.structure import Atom, MolecularStructure
from species_test_support import synthetic_species_library


class GeneratedInputParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = synthetic_species_library()
        cls.structure = MolecularStructure(
            (
                Atom(0, "C", 0.125, -0.25, 0.5),
                Atom(1, "H", 1.25, 0.0, -0.5),
                Atom(2, "Au", 2.0, 0.25, 0.0),
            )
        )

    def test_optimization_pair_recovers_every_editable_setting_exactly(self) -> None:
        settings = AimsOptimizationSettings(
            xc=XCFunctional.B3LYP,
            vdw=VdwMethod.TS_LIBMBD,
            relativity=Relativity.NONE,
            species_accuracy=SpeciesAccuracy.TIGHT,
            force_threshold=0.025,
            output_dipole=False,
            total_charge=-1.25,
            spin=SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.PER_ATOM,
                fixed_spin_moment=2.0,
            ),
            atom_settings=(
                AtomAimsSettings(
                    0,
                    species_accuracy=SpeciesAccuracy.LIGHT,
                    initial_moment=1.5,
                    initial_charge=-0.25,
                ),
                AtomAimsSettings(2, initial_moment=0.5),
            ),
            orbital_cubes=OrbitalCubeOutputSettings(
                frontier_orbitals=(
                    FrontierOrbital.HOMO_MINUS_1,
                    FrontierOrbital.LUMO_PLUS_1,
                ),
                eigenstate_indices=(31, 12),
                grid_spacing_angstrom=0.08,
            ),
        )
        bundle = build_aims_optimization_inputs(
            self.structure,
            settings,
            self.library,
        )

        parsed = parse_generated_optimization_inputs(
            bundle.geometry_text.encode("utf-8"),
            bundle.control_text.encode("utf-8"),
        )

        self.assertEqual(parsed.structure.atoms, self.structure.atoms)
        self.assertEqual(parsed.settings, settings)

    def test_step1_pair_recovers_without_external_species_source(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "N", 1.2, 0.0, 0.0),
            )
        )
        settings = AimsOptimizationSettings(
            species_accuracy=SpeciesAccuracy.LIGHT,
        )
        bundle = build_aims_optimization_inputs(
            structure,
            settings,
            self.library,
        )

        parsed = parse_generated_optimization_inputs(
            bundle.geometry_text,
            bundle.control_text,
        )

        self.assertEqual(parsed.structure.atoms, structure.atoms)
        self.assertEqual(parsed.settings, settings)

    def test_historical_step2_pair_recovers_mixed_per_atom_accuracy(self) -> None:
        settings = AimsOptimizationSettings(
            species_accuracy=SpeciesAccuracy.TIGHT,
            atom_settings=(
                AtomAimsSettings(
                    0,
                    species_accuracy=SpeciesAccuracy.LIGHT,
                ),
            ),
        )
        bundle = build_aims_optimization_inputs(
            self.structure,
            settings,
            self.library,
        )
        historical_control = _as_historical_control(bundle.control_text)

        parsed = parse_generated_optimization_inputs(
            bundle.geometry_text,
            historical_control,
        )

        self.assertEqual(parsed.settings, settings)

    def test_step3_pair_recovers_every_editable_setting_exactly(self) -> None:
        settings = TransportConvergenceSettings(
            xc=XCFunctional.PBE0,
            spin=SpinSettings(
                enabled=True,
                initialization_mode=SpinInitializationMode.UNIFORM_DEFAULT,
                uniform_initial_moment=1.25,
            ),
            total_charge=2.0,
            species_accuracy=SpeciesAccuracy.REALLY_TIGHT,
            occupation_width=0.02,
            n_max_pulay=12,
            charge_mix_param=0.35,
            sc_accuracy_rho=2.0e-5,
            sc_accuracy_eev=2.0e-3,
            sc_accuracy_etot=2.0e-6,
            sc_iter_limit=600,
            orbital_cubes=OrbitalCubeOutputSettings(
                eigenstate_indices=(152, 154),
                grid_spacing_angstrom=0.12,
            ),
        )
        bundle = build_transport_convergence_aims_inputs(
            self.structure,
            settings,
            self.library,
        )

        parsed = parse_generated_transport_convergence_inputs(
            bundle.geometry_text.encode("utf-8"),
            bundle.control_text.encode("utf-8"),
        )

        self.assertEqual(parsed.structure.atoms, self.structure.atoms)
        self.assertEqual(parsed.settings, settings)

    def test_historical_step3_pair_uses_embedded_accuracy_evidence(self) -> None:
        settings = TransportConvergenceSettings(
            species_accuracy=SpeciesAccuracy.REALLY_TIGHT,
        )
        bundle = build_transport_convergence_aims_inputs(
            self.structure,
            settings,
            self.library,
        )

        parsed = parse_generated_transport_convergence_inputs(
            bundle.geometry_text,
            _as_historical_control(bundle.control_text),
        )

        self.assertEqual(parsed.settings, settings)

    def test_historical_input_without_accuracy_evidence_is_ambiguous(self) -> None:
        bundle = build_aims_optimization_inputs(
            self.structure,
            AimsOptimizationSettings(),
            self.library,
        )
        historical_control = re.sub(
            r"# Moltage species accuracy: [a-z_]+\n",
            "",
            bundle.control_text,
            count=1,
        )

        with self.assertRaisesRegex(AimsInputParsingError, "not uniquely"):
            parse_generated_optimization_inputs(
                bundle.geometry_text,
                historical_control,
            )

    def test_conflicting_accuracy_evidence_inside_one_block_is_rejected(self) -> None:
        bundle = build_aims_optimization_inputs(
            self.structure,
            AimsOptimizationSettings(species_accuracy=SpeciesAccuracy.LIGHT),
            self.library,
        )
        historical_control = _as_historical_control(bundle.control_text).replace(
            '# Suggested "light" defaults\n',
            '# Suggested "light" defaults\n# Suggested "tight" defaults\n',
            1,
        )

        with self.assertRaisesRegex(AimsInputParsingError, "not uniquely"):
            parse_generated_optimization_inputs(
                bundle.geometry_text,
                historical_control,
            )

    def test_duplicate_embedded_species_declaration_is_rejected(self) -> None:
        bundle = build_aims_optimization_inputs(
            self.structure,
            AimsOptimizationSettings(),
            self.library,
        )
        malformed = bundle.control_text.replace(
            "species C\n",
            "species C\n  nucleus 6\nspecies C_duplicate\n",
            1,
        )

        with self.assertRaisesRegex(AimsInputParsingError, "species"):
            parse_generated_optimization_inputs(
                bundle.geometry_text,
                malformed,
            )

    def test_malformed_embedded_species_block_is_rejected(self) -> None:
        bundle = build_transport_convergence_aims_inputs(
            self.structure,
            TransportConvergenceSettings(),
            self.library,
        )
        malformed = bundle.control_text.replace("  nucleus 6\n", "", 1)

        with self.assertRaisesRegex(AimsInputParsingError, "nucleus"):
            parse_generated_transport_convergence_inputs(
                bundle.geometry_text,
                malformed,
            )

    def test_semantically_equivalent_but_unmodified_writer_bytes_are_required(self) -> None:
        bundle = build_aims_optimization_inputs(
            self.structure,
            AimsOptimizationSettings(),
            self.library,
        )
        modified = bundle.control_text.replace("charge 0.\n", "charge 0.0\n")

        with self.assertRaisesRegex(AimsInputParsingError, "round-trip"):
            parse_generated_optimization_inputs(
                bundle.geometry_text,
                modified,
            )

    def test_unknown_directive_fails_without_guessing_settings(self) -> None:
        bundle = build_transport_convergence_aims_inputs(
            self.structure,
            TransportConvergenceSettings(),
            self.library,
        )
        modified = bundle.control_text.replace(
            "restart aims.restart\n",
            "restart aims.restart\nunknown_restart_option yes\n",
        )

        with self.assertRaisesRegex(AimsInputParsingError, "round-trip"):
            parse_generated_transport_convergence_inputs(
                bundle.geometry_text,
                modified,
            )


def _as_historical_control(control_text: str) -> str:
    accuracy_names = {
        "light": "light",
        "tight": "tight",
        "really_tight": "safe",
    }
    without_metadata = re.sub(
        r"# Moltage species accuracy: [a-z_]+\n",
        "",
        control_text,
        count=1,
    )

    def add_evidence(match: re.Match[str]) -> str:
        accuracy = match.group("accuracy")
        return (
            f'# Suggested "{accuracy_names[accuracy]}" defaults\n'
            + match.group(0)
        )

    return re.sub(
        r"# SYNTHETIC TEST SPECIES (?P<accuracy>light|tight|really_tight)\n",
        add_evidence,
        without_metadata,
    )


if __name__ == "__main__":
    unittest.main()
