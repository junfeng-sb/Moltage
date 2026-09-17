import unittest

from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.optimization_settings import (
    Relativity,
    SpeciesAccuracy,
    XCFunctional,
)
from moltage.aims.species_library import (
    SpeciesLibraryError,
)
from moltage.aims.transport_convergence_bundle import (
    build_transport_convergence_aims_inputs,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
    TransportConvergenceSettingsError,
)
from moltage.aims.orbital_cube import OrbitalCubeOutputSettings
from moltage.domain.structure import Atom, MolecularStructure
from species_test_support import synthetic_species_library
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from synthetic_structure_test_support import synthetic_step2_state


def _structure(*elements):
    return MolecularStructure(
        tuple(
            Atom(index, element, index + 0.125, -index - 0.25, index * 0.5)
            for index, element in enumerate(elements)
        )
    )


def _active_global_lines(control_text):
    active = []
    for line in control_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("species "):
            break
        if stripped and not stripped.startswith("#"):
            active.append(" ".join(stripped.split()))
    return tuple(active)


def _active_lines(control_text):
    return tuple(
        " ".join(line.split())
        for line in control_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


class TransportConvergenceInputTests(unittest.TestCase):
    def test_default_settings_are_the_reviewed_step3_surface(self):
        settings = TransportConvergenceSettings()

        self.assertIs(settings.xc, XCFunctional.PBE)
        self.assertFalse(settings.spin.enabled)
        self.assertEqual(settings.total_charge, 0.0)
        self.assertIs(settings.relativity, Relativity.ATOMIC_ZORA_SCALAR)
        self.assertEqual(settings.occupation_type, "gaussian")
        self.assertEqual(settings.occupation_width, 0.01)
        self.assertEqual(settings.mixer, "pulay")
        self.assertEqual(settings.n_max_pulay, 10)
        self.assertEqual(settings.charge_mix_param, 0.2)
        self.assertEqual(settings.sc_accuracy_rho, 1.0e-5)
        self.assertEqual(settings.sc_accuracy_eev, 1.0e-3)
        self.assertEqual(settings.sc_accuracy_etot, 1.0e-6)
        self.assertEqual(settings.sc_iter_limit, 500)
        self.assertIs(settings.species_accuracy, SpeciesAccuracy.TIGHT)
        self.assertEqual(settings.output, "aitranss")
        self.assertEqual(settings.ks_method, "serial")
        self.assertEqual(settings.restart_file, "aims.restart")
        self.assertEqual(settings.orbital_cubes, OrbitalCubeOutputSettings())

    def test_invalid_scf_values_fail_explicitly(self):
        invalid = (
            {"occupation_width": 0.0},
            {"n_max_pulay": 0},
            {"n_max_pulay": 1.5},
            {"charge_mix_param": 0.0},
            {"charge_mix_param": 1.01},
            {"sc_accuracy_rho": 0.0},
            {"sc_accuracy_eev": -1.0},
            {"sc_accuracy_etot": float("inf")},
            {"sc_iter_limit": 0},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(TransportConvergenceSettingsError):
                    TransportConvergenceSettings(**values)

    def test_default_control_has_exact_global_order_and_values(self):
        bundle = build_transport_convergence_aims_inputs(
            _structure("C", "H"),
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )

        self.assertEqual(
            _active_global_lines(bundle.control_text),
            (
                "xc pbe",
                "spin none",
                "relativistic atomic_zora scalar",
                "charge 0.",
                "occupation_type gaussian 0.01",
                "mixer pulay",
                "n_max_pulay 10",
                "charge_mix_param 0.2",
                "sc_accuracy_rho 1E-5",
                "sc_accuracy_eev 1E-3",
                "sc_accuracy_etot 1E-6",
                "sc_iter_limit 500",
                "output aitranss",
                "KS_method serial",
                "restart aims.restart",
            ),
        )

    def test_default_control_has_no_optimization_or_dispersion_directive(self):
        bundle = build_transport_convergence_aims_inputs(
            _structure("Au"),
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )
        active = _active_lines(bundle.control_text)
        forbidden_prefixes = (
            "relax_geometry",
            "sc_accuracy_forces",
            "output dipole",
            "output hirshfeld",
            "many_body_dispersion",
            "many_body_dispersion_nl",
            "vdw_correction_hirshfeld",
            "vdw_ts",
        )

        for forbidden in forbidden_prefixes:
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(line.startswith(forbidden) for line in active),
                    forbidden,
                )

    def test_requested_absolute_orbitals_and_grid_spacing_are_rendered(self):
        settings = TransportConvergenceSettings(
            orbital_cubes=OrbitalCubeOutputSettings(
                eigenstate_indices=(154, 152),
                grid_spacing_angstrom=0.12,
            )
        )

        bundle = build_transport_convergence_aims_inputs(
            _structure("C", "H"),
            settings,
            synthetic_species_library(),
        )

        self.assertIn("output cube eigenstate 152\n", bundle.control_text)
        self.assertIn("cube filename orbital_state_152.cube\n", bundle.control_text)
        self.assertIn("output cube eigenstate 154\n", bundle.control_text)
        self.assertEqual(bundle.control_text.count("cube origin "), 2)
        self.assertEqual(bundle.control_text.count("cube edge "), 6)
        self.assertNotIn("cube edge_density", bundle.control_text)

    def test_only_first_used_structure_species_are_emitted_in_tight_order(self):
        bundle = build_transport_convergence_aims_inputs(
            _structure("C", "H", "N", "S", "Au", "C"),
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )
        declarations = tuple(
            line.split()[1]
            for line in _active_lines(bundle.control_text)
            if line.startswith("species ")
        )

        self.assertEqual(declarations, ("C", "H", "N", "S", "Au"))
        self.assertEqual(
            tuple(block.element for block in bundle.species_blocks),
            declarations,
        )
        self.assertTrue(
            all(
                block.accuracy is SpeciesAccuracy.TIGHT
                and block.source_path.parent.name == "tight"
                for block in bundle.species_blocks
            )
        )
        for historical_only in ("Ru", "Ge", "Mo", "Cl", "P", "Si"):
            self.assertNotIn(historical_only, declarations)

    def test_unresolvable_species_uses_existing_typed_library_failure(self):
        with self.assertRaises(SpeciesLibraryError):
            build_transport_convergence_aims_inputs(
                _structure("Xx"),
                TransportConvergenceSettings(),
                synthetic_species_library(),
            )

    def test_electrode_applied_geometry_is_written_exactly_without_rebuilding(self):
        step2_structure, connectivity, _anchors, sites = synthetic_step2_state()
        proposal = propose_electrode_placement(
            step2_structure,
            connectivity,
            sites,
            sites,
        )
        applied = apply_electrode_placement(
            step2_structure,
            connectivity,
            proposal,
        )

        bundle = build_transport_convergence_aims_inputs(
            applied.structure,
            TransportConvergenceSettings(),
            synthetic_species_library(),
        )

        self.assertEqual(
            bundle.geometry_text,
            render_geometry_in(applied.structure),
        )
        atom_lines = bundle.geometry_text.splitlines()
        self.assertEqual(len(atom_lines), len(applied.structure))
        for atom, line in zip(applied.structure, atom_lines, strict=True):
            self.assertEqual(
                line,
                f"atom {atom.x!r} {atom.y!r} {atom.z!r} {atom.element}",
            )


if __name__ == "__main__":
    unittest.main()
