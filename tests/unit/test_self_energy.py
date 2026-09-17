from math import cos, sin
from pathlib import Path
import unittest

from moltage.aitranss.self_energy import (
    AITRANSS_LAYER_TOLERANCE_ANGSTROM,
    ElectrodeOwner,
    SelfEnergyError,
    build_partitioned_self_energy_plan,
    parse_self_energy,
    render_self_energy,
    select_interface_layers,
    validate_self_energy_round_trip,
)
from moltage.aitranss.tcontrol import TControlSettings
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_surface import (
    ElectrodeSurfaceProposal,
    propose_electrode_surfaces,
)
from synthetic_structure_test_support import synthetic_junction_structure
from synthetic_structure_test_support import synthetic_junction_provenance
from synthetic_structure_test_support import synthetic_extended_electrode_placement
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from test_electrode_surface import accepted_shape


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "phase4b"


def _current_structure() -> MolecularStructure:
    return synthetic_junction_structure()


def _current_plan():
    structure = _current_structure()
    surface = propose_electrode_surfaces(
        structure,
        synthetic_junction_provenance(),
    )
    settings = TControlSettings(
        natoms=len(structure),
        nsaos=512,
        lsurc=surface.left_one_based[0],
        lsurx=surface.left_one_based[1],
        lsury=surface.left_one_based[2],
        rsurc=surface.right_one_based[0],
        rsurx=surface.right_one_based[1],
        rsury=surface.right_one_based[2],
    )
    return structure, surface, settings, build_partitioned_self_energy_plan(
        structure, surface, settings
    )


class SelfEnergyLayerSelectionTests(unittest.TestCase):
    def test_extensions_join_membership_without_changing_surfaces_or_nlayers(self):
        standard_structure, standard_provenance = accepted_shape()
        standard_surface = propose_electrode_surfaces(
            standard_structure,
            standard_provenance,
        )
        applied = synthetic_extended_electrode_placement()
        surface = propose_electrode_surfaces(
            applied.structure,
            provenance_from_applied_electrodes(applied),
        )
        settings = TControlSettings(
            natoms=len(applied.structure),
            nsaos=512,
            lsurc=surface.left_one_based[0],
            lsurx=surface.left_one_based[1],
            lsury=surface.left_one_based[2],
            rsurc=surface.right_one_based[0],
            rsurx=surface.right_one_based[1],
            rsury=surface.right_one_based[2],
            nlayers=4,
        )

        plan = build_partitioned_self_energy_plan(
            applied.structure,
            surface,
            settings,
        )

        self.assertEqual(surface.left_zero_based, standard_surface.left_zero_based)
        self.assertEqual(surface.right_zero_based, standard_surface.right_zero_based)
        self.assertEqual(settings.nlayers, 4)
        self.assertEqual(len(surface.left_electrode_zero_based), 58)
        self.assertEqual(len(surface.right_electrode_zero_based), 57)
        self.assertEqual(plan.left.candidate_global_zero_based[-2:], (128, 130))
        self.assertEqual(plan.right.candidate_global_zero_based[-1:], (129,))
        self.assertIn(129, plan.right.selected_global_zero_based)

    def test_multiple_pyramid_sizes_use_only_explicit_standard_members(self):
        for pyramid_layers, nlayers, atom_count in (
            (4, 2, 20),
            (5, 3, 35),
            (6, 4, 56),
        ):
            with self.subTest(pyramid_layers=pyramid_layers):
                structure, provenance = accepted_shape(pyramid_layers)
                surface = propose_electrode_surfaces(structure, provenance)
                settings = TControlSettings(
                    natoms=len(structure),
                    nsaos=512,
                    lsurc=surface.left_one_based[0],
                    lsurx=surface.left_one_based[1],
                    lsury=surface.left_one_based[2],
                    rsurc=surface.right_one_based[0],
                    rsurx=surface.right_one_based[1],
                    rsury=surface.right_one_based[2],
                    nlayers=nlayers,
                )
                plan = build_partitioned_self_energy_plan(
                    structure,
                    surface,
                    settings,
                )
                self.assertEqual(len(plan.left.candidate_global_zero_based), atom_count)
                self.assertEqual(len(plan.right.candidate_global_zero_based), atom_count)
                self.assertTrue(
                    all(
                        local is not None
                        for layer in (*plan.left.layers, *plan.right.layers)
                        for local in layer.template_local_indices
                    )
                )

    def test_synthetic_junction_and_control_preserve_selection_invariants(self):
        structure, surface, settings, _plan = _current_plan()

        self.assertEqual(len(structure), 128)
        self.assertEqual(sum(atom.element == "Au" for atom in structure), 112)
        self.assertEqual(settings.natoms, 128)
        self.assertEqual(settings.nsaos, 512)
        self.assertEqual(surface.left_one_based, (73, 58, 53))
        self.assertEqual(surface.right_one_based, (128, 113, 108))

    def test_global_population_is_deterministic_for_synthetic_geometry(self):
        structure, surface, settings, plan = _current_plan()
        selection = select_interface_layers(
            structure,
            tuple(atom.index for atom in structure if atom.element == "Au"),
            surface.left_zero_based,
            nlayers=settings.nlayers,
            leakage_texts=(settings.s1i, settings.s2i, settings.s3i),
        )
        self.assertEqual(
            tuple(layer.global_one_based for layer in selection.layers),
            tuple(layer.global_one_based for layer in plan.left.layers),
        )
        self.assertEqual(
            tuple(len(layer.global_zero_based) for layer in selection.layers),
            (21, 15, 10, 6),
        )

    def test_partitioned_standard_pyramid_memberships_are_deterministic(self):
        _structure, surface, _settings, plan = _current_plan()
        self.assertEqual(
            tuple(layer.global_one_based for layer in plan.left.layers),
            (
                tuple(range(53, 74)),
                tuple(range(38, 53)),
                tuple(range(28, 38)),
                tuple(range(22, 28)),
            ),
        )
        self.assertEqual(
            tuple(layer.template_local_indices for layer in plan.left.layers),
            (
                tuple(range(35, 56)),
                tuple(range(20, 35)),
                tuple(range(10, 20)),
                tuple(range(4, 10)),
            ),
        )
        self.assertEqual(
            tuple(layer.global_one_based for layer in plan.right.layers),
            (
                tuple(range(108, 129)),
                tuple(range(93, 108)),
                tuple(range(83, 93)),
                tuple(range(77, 83)),
            ),
        )
        self.assertEqual(
            tuple(layer.template_local_indices for layer in plan.right.layers),
            (
                tuple(range(35, 56)),
                tuple(range(20, 35)),
                tuple(range(10, 20)),
                tuple(range(4, 10)),
            ),
        )
        self.assertEqual(
            tuple(len(layer.global_zero_based) for layer in plan.left.layers),
            (21, 15, 10, 6),
        )
        self.assertEqual(
            tuple(len(layer.global_zero_based) for layer in plan.right.layers),
            (21, 15, 10, 6),
        )
        self.assertEqual((plan.left.selected_count, plan.right.selected_count), (52, 52))
        self.assertFalse(
            set(plan.left.selected_global_zero_based)
            & set(plan.right.selected_global_zero_based)
        )
        self.assertLessEqual(
            set(plan.left.selected_global_zero_based),
            set(surface.left_electrode_zero_based),
        )
        self.assertLessEqual(
            set(plan.right.selected_global_zero_based),
            set(surface.right_electrode_zero_based),
        )

    def test_complete_electrode_rigid_rotations_preserve_local_layers(self):
        structure, surface, settings, original = _current_plan()
        left_set = set(surface.left_electrode_zero_based)
        right_set = set(surface.right_electrode_zero_based)
        atoms = []
        for atom in structure:
            angle = 0.71 if atom.index in left_set else -0.49 if atom.index in right_set else 0.0
            if angle:
                x = cos(angle) * atom.x - sin(angle) * atom.y
                y = sin(angle) * atom.x + cos(angle) * atom.y
                z = atom.z
            else:
                x, y, z = atom.x, atom.y, atom.z
            atoms.append(Atom(atom.index, atom.element, x, y, z))
        rotated_structure = MolecularStructure(tuple(atoms))
        rotated_surface = propose_electrode_surfaces(
            rotated_structure,
            (surface.left_provenance, surface.right_provenance),
        )
        rotated = build_partitioned_self_energy_plan(
            rotated_structure, rotated_surface, settings
        )
        for old, new in ((original.left, rotated.left), (original.right, rotated.right)):
            self.assertEqual(
                tuple(layer.template_local_indices for layer in old.layers),
                tuple(layer.template_local_indices for layer in new.layers),
            )
        self.assertEqual(
            AITRANSS_LAYER_TOLERANCE_ANGSTROM,
            rotated.tolerance_angstrom,
        )


class SelfEnergyRenderingTests(unittest.TestCase):
    def test_render_parse_round_trip_has_exact_atoms_assignments_and_reader_format(self):
        structure, _surface, _settings, plan = _current_plan()
        rendered = render_self_energy(plan)
        parsed = parse_self_energy(rendered, structure)
        compatibility = validate_self_energy_round_trip(rendered, plan)
        self.assertTrue(rendered.endswith(b"$end\n"))
        self.assertNotIn(b"\r", rendered)
        self.assertEqual(len(parsed.rows), 128)
        self.assertEqual(
            sum(row.reservoir == "left" for row in parsed.rows), 52
        )
        self.assertEqual(
            sum(row.reservoir == "right" for row in parsed.rows), 52
        )
        self.assertEqual(
            sum(row.reservoir is None and row.leakage == 0.0 for row in parsed.rows),
            24,
        )
        lines = rendered.decode("ascii").splitlines()
        self.assertTrue(all(len(line) == 90 for line in lines[1:-1]))
        self.assertTrue(all(len(line.split()) == 7 for line in lines[1:-1]))
        self.assertEqual(lines[1][0:5], "    1")
        self.assertEqual(lines[1][55:57], " c")
        self.assertEqual(lines[1][61:66], "empty")
        self.assertEqual(lines[17][61:66], "empty")  # contact apex is not selected
        self.assertEqual(lines[53][61:66], " left")
        self.assertEqual(lines[111][61:66], "right")
        self.assertIn("D-01", lines[53][70:90])
        self.assertEqual(rendered, render_self_energy(plan))
        self.assertEqual(
            (
                compatibility.row_count,
                compatibility.left_count,
                compatibility.right_count,
                compatibility.empty_count,
            ),
            (128, 52, 52, 24),
        )

        assignment_by_index = {
            item.atom_index_one_based: item for item in plan.atom_assignments
        }
        for row in parsed.rows:
            assignment = assignment_by_index[row.atom_index_one_based]
            expected = {
                ElectrodeOwner.NONE: None,
                ElectrodeOwner.LEFT: "left",
                ElectrodeOwner.RIGHT: "right",
            }[assignment.owner]
            self.assertEqual(row.reservoir, expected)
            self.assertEqual(row.leakage, assignment.leakage)

    def test_internal_ownership_and_planes_are_independent_of_file_tokens(self):
        _structure, _surface, _settings, plan = _current_plan()
        assignments = plan.atom_assignments

        self.assertEqual(len(assignments), 128)
        self.assertEqual(
            sum(item.owner is ElectrodeOwner.LEFT for item in assignments),
            52,
        )
        self.assertEqual(
            sum(item.owner is ElectrodeOwner.RIGHT for item in assignments),
            52,
        )
        self.assertEqual(
            sum(item.owner is ElectrodeOwner.NONE for item in assignments),
            24,
        )
        self.assertEqual(assignments[0].owner, ElectrodeOwner.NONE)
        self.assertIsNone(assignments[0].plane_number)
        self.assertEqual(assignments[37].owner, ElectrodeOwner.LEFT)
        self.assertEqual(assignments[37].plane_number, 2)
        self.assertEqual(assignments[37].leakage, 0.05)
        self.assertEqual(assignments[110].owner, ElectrodeOwner.RIGHT)
        self.assertEqual(assignments[110].plane_number, 1)
        self.assertEqual(assignments[110].leakage, 0.1)

        external = render_self_energy(plan).decode("ascii").splitlines()
        self.assertEqual(external[1].split()[5], "empty")
        self.assertEqual(external[38].split()[5], "left")
        self.assertEqual(external[111].split()[5], "right")

    def test_minimal_reference_grammar_is_accepted(self):
        structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "Au", 1.0, 0.0, 0.0),
                Atom(2, "Au", 2.0, 0.0, 0.0),
            )
        )
        reference = (
            FIXTURE_DIRECTORY / "self_energy_reader_compatible_minimal.in"
        ).read_bytes()

        parsed = parse_self_energy(reference, structure)

        self.assertEqual(
            tuple(row.reservoir for row in parsed.rows),
            (None, "left", "right"),
        )
        self.assertTrue(
            all(len(line.split()) == 7 for line in reference.splitlines()[1:-1])
        )

    def test_blank_unassigned_owner_reproduces_retry02_six_token_mismatch(self):
        failed = (
            b"$self.energy: imaginary piece per atom\n"
            b"    1        1.1072984      0.9076341      0.0328575"
            b"     C             0.00000000000000D+00\n"
            b"$end\n"
        )

        with self.assertRaisesRegex(SelfEnergyError, "exactly seven tokens"):
            parse_self_energy(failed)

        _structure, _surface, _settings, plan = _current_plan()
        invalid = render_self_energy(plan).replace(b"empty", b"     ", 1)
        with self.assertRaisesRegex(SelfEnergyError, "exactly seven tokens"):
            validate_self_energy_round_trip(invalid, plan)

    def test_leakage_follows_plane_number(self):
        _structure, _surface, _settings, plan = _current_plan()
        self.assertEqual(
            tuple(layer.leakage for layer in plan.left.layers),
            (0.1, 0.05, 0.025, 0.025),
        )
        self.assertEqual(
            tuple(layer.leakage for layer in plan.right.layers),
            (0.1, 0.05, 0.025, 0.025),
        )


if __name__ == "__main__":
    unittest.main()
