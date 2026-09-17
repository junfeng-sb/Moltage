from dataclasses import replace
from math import dist
import unittest

from moltage.domain.au_lattice_extension import AuLatticeExtensionSite
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_lattice_extension import (
    add_lattice_extension,
    enumerate_lattice_extension_candidates,
)
from moltage.junction.electrode_lattice_interaction import (
    ElectrodeLatticeInteractionError,
    LatticeExtensionAvailability,
    add_lattice_extension_to_working_geometry,
    interaction_candidates_for_working_geometry,
)
from moltage.junction.steric_orientation import (
    non_au_steric_clearance_for_point,
)
from moltage.structure.vdw_radii import load_default_vdw_radii
from synthetic_structure_test_support import synthetic_electrode_placement


class AuLatticeInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.applied = synthetic_electrode_placement()
        self.radii = load_default_vdw_radii()

    def test_snapshot_retains_every_phase2_identity_and_marks_blocked(self) -> None:
        canonical = enumerate_lattice_extension_candidates(self.applied)
        snapshot = interaction_candidates_for_working_geometry(
            self.applied,
            self.applied.structure,
            self.radii,
        )
        self.assertEqual(
            tuple(item.identity for item in snapshot),
            tuple(item.identity for item in canonical),
        )
        self.assertTrue(
            any(
                item.availability is LatticeExtensionAvailability.AVAILABLE
                for item in snapshot
            )
        )
        self.assertTrue(
            any(
                item.availability is LatticeExtensionAvailability.BLOCKED
                for item in snapshot
            )
        )
        canonical_by_identity = {
            candidate.identity: candidate for candidate in canonical
        }
        representative_by_side_layer = {}
        for item in snapshot:
            representative_by_side_layer.setdefault(
                (item.identity.side, item.identity.layer_index),
                item,
            )
        self.assertEqual(
            {side for side, _layer in representative_by_side_layer},
            {"LEFT", "RIGHT"},
        )
        self.assertGreater(
            len({layer for _side, layer in representative_by_side_layer}),
            1,
        )
        for item in representative_by_side_layer.values():
            phase2_result = add_lattice_extension(
                self.applied,
                canonical_by_identity[item.identity],
            )
            new_index = len(self.applied.structure)
            actual_neighbors = tuple(
                bond.first_index
                if bond.second_index == new_index
                else bond.second_index
                for bond in phase2_result.applied.connectivity
                if new_index in {bond.first_index, bond.second_index}
            )
            self.assertEqual(
                item.predicted_bond_atom_indices,
                actual_neighbors,
            )
            self.assertEqual(
                item.predicted_bond_coordinates,
                tuple(
                    _coordinates(self.applied.structure[index])
                    for index in actual_neighbors
                ),
            )
            spacing = next(
                cluster.pyramid.nearest_neighbor_spacing_angstrom
                for cluster in phase2_result.applied.proposal.clusters
                if cluster.side == item.identity.side
            )
            self.assertAlmostEqual(
                dist((0.0, 0.0, 0.0), item.layer_basis_u),
                spacing,
                places=12,
            )
            self.assertAlmostEqual(
                dist((0.0, 0.0, 0.0), item.layer_basis_v),
                spacing,
                places=12,
            )
            self.assertAlmostEqual(
                dist(item.layer_basis_u, item.layer_basis_v),
                spacing,
                places=12,
            )

    def test_independent_rigid_side_transforms_map_signed_candidates(self) -> None:
        original = interaction_candidates_for_working_geometry(
            self.applied,
            self.applied.structure,
            self.radii,
        )
        transform = lambda point: (
            -point[1] + 11.0,
            point[0] - 4.0,
            point[2] + 2.5,
        )
        working = _transform_side(
            self.applied,
            self.applied.structure,
            "LEFT",
            transform,
        )
        transformed = interaction_candidates_for_working_geometry(
            self.applied,
            working,
            self.radii,
        )
        original_by_identity = {item.identity: item for item in original}
        transformed_by_identity = {item.identity: item for item in transformed}
        self.assertEqual(original_by_identity.keys(), transformed_by_identity.keys())
        signed = next(
            identity
            for identity in original_by_identity
            if identity.side == "LEFT" and min(identity.lattice_key) < 0
        )
        self.assertAlmostEqual(
            dist(
                transformed_by_identity[signed].coordinates,
                transform(original_by_identity[signed].coordinates),
            ),
            0.0,
            places=12,
        )
        right = next(
            identity for identity in original_by_identity if identity.side == "RIGHT"
        )
        self.assertEqual(
            transformed_by_identity[right].coordinates,
            original_by_identity[right].coordinates,
        )
        transformed_signed = transformed_by_identity[signed]
        self.assertEqual(
            transformed_signed.predicted_bond_coordinates,
            tuple(
                _coordinates(working[index])
                for index in transformed_signed.predicted_bond_atom_indices
            ),
        )
        original_signed = original_by_identity[signed]
        for original_basis, transformed_basis in (
            (original_signed.layer_basis_u, transformed_signed.layer_basis_u),
            (original_signed.layer_basis_v, transformed_signed.layer_basis_v),
        ):
            expected_endpoint = transform(
                tuple(
                    original_signed.coordinates[index] + original_basis[index]
                    for index in range(3)
                )
            )
            expected_basis = tuple(
                expected_endpoint[index] - transformed_signed.coordinates[index]
                for index in range(3)
            )
            self.assertAlmostEqual(
                dist(transformed_basis, expected_basis),
                0.0,
                places=12,
            )

    def test_non_au_overlap_and_exact_radius_sum_boundary(self) -> None:
        snapshot = interaction_candidates_for_working_geometry(
            self.applied,
            self.applied.structure,
            self.radii,
        )
        selected = next(
            item
            for item in snapshot
            if item.availability is LatticeExtensionAvailability.AVAILABLE
        )
        atom = next(atom for atom in self.applied.structure if atom.element != "Au")
        overlapped = _move_atom(
            self.applied.structure,
            atom.index,
            selected.coordinates,
        )
        overlapped_by_identity = {
            item.identity: item
            for item in interaction_candidates_for_working_geometry(
                self.applied,
                overlapped,
                self.radii,
            )
        }
        self.assertIs(
            overlapped_by_identity[selected.identity].availability,
            LatticeExtensionAvailability.BLOCKED,
        )
        self.assertEqual(
            overlapped_by_identity[selected.identity].blocking_atom_index,
            atom.index,
        )

        boundary_distance = self.radii["Au"] + self.radii[atom.element]
        boundary = _move_atom(
            self.applied.structure,
            atom.index,
            (
                selected.coordinates[0] + boundary_distance,
                selected.coordinates[1],
                selected.coordinates[2],
            ),
        )
        clearance = non_au_steric_clearance_for_point(
            boundary,
            selected.coordinates,
            self.radii,
        )
        self.assertEqual(clearance.minimum, 0.0)
        boundary_by_identity = {
            item.identity: item
            for item in interaction_candidates_for_working_geometry(
                self.applied,
                boundary,
                self.radii,
            )
        }
        self.assertIs(
            boundary_by_identity[selected.identity].availability,
            LatticeExtensionAvailability.AVAILABLE,
        )

    def test_current_au_collision_blocks_without_removing_identity(self) -> None:
        original = interaction_candidates_for_working_geometry(
            self.applied,
            self.applied.structure,
            self.radii,
        )
        selected = next(item for item in original if item.identity.side == "LEFT")
        right_cluster = next(
            cluster
            for cluster in self.applied.proposal.clusters
            if cluster.side == "RIGHT"
        )
        target = _coordinates(
            self.applied.structure[right_cluster.local_to_global_indices[0]]
        )
        translation = tuple(
            target[index] - selected.coordinates[index] for index in range(3)
        )
        moved = _transform_side(
            self.applied,
            self.applied.structure,
            "LEFT",
            lambda point: tuple(
                point[index] + translation[index] for index in range(3)
            ),
        )
        moved_by_identity = {
            item.identity: item
            for item in interaction_candidates_for_working_geometry(
                self.applied,
                moved,
                self.radii,
            )
        }
        self.assertIn(selected.identity, moved_by_identity)
        self.assertIs(
            moved_by_identity[selected.identity].availability,
            LatticeExtensionAvailability.BLOCKED,
        )

    def test_missing_radius_and_non_rigid_mapping_fail_explicitly(self) -> None:
        missing = dict(self.radii)
        element = next(
            atom.element for atom in self.applied.structure if atom.element != "Au"
        )
        del missing[element]
        with self.assertRaisesRegex(
            ElectrodeLatticeInteractionError,
            "no approved van der Waals radius",
        ):
            interaction_candidates_for_working_geometry(
                self.applied,
                self.applied.structure,
                missing,
            )

        cluster = self.applied.proposal.clusters[0]
        changed_index = cluster.local_to_global_indices[-1]
        atom = self.applied.structure[changed_index]
        non_rigid = _move_atom(
            self.applied.structure,
            changed_index,
            (atom.x + 0.01, atom.y, atom.z),
        )
        with self.assertRaisesRegex(
            ElectrodeLatticeInteractionError,
            "not rigid",
        ):
            interaction_candidates_for_working_geometry(
                self.applied,
                non_rigid,
                self.radii,
            )

    def test_current_world_add_revalidates_and_preserves_invariants(self) -> None:
        transform = lambda point: (
            point[0] + 8.0,
            -point[2] + 3.0,
            point[1] - 2.0,
        )
        working = _transform_side(
            self.applied,
            self.applied.structure,
            "RIGHT",
            transform,
        )
        snapshot = interaction_candidates_for_working_geometry(
            self.applied,
            working,
            self.radii,
        )
        selected = next(
            item
            for item in snapshot
            if item.identity.side == "RIGHT"
            and item.availability is LatticeExtensionAvailability.AVAILABLE
        )
        reference_corners = tuple(
            cluster.pyramid.reference_corner_lattice_keys
            for cluster in self.applied.proposal.clusters
        )
        result = add_lattice_extension_to_working_geometry(
            self.applied,
            working,
            selected.identity,
            self.radii,
        )
        self.assertEqual(len(result.working_structure), len(working) + 1)
        self.assertEqual(
            _coordinates(result.working_structure[len(result.working_structure) - 1]),
            selected.coordinates,
        )
        self.assertEqual(result.applied.lattice_extensions[-1].identity, selected.identity)
        self.assertEqual(result.connectivity.atom_count, len(result.working_structure))
        self.assertEqual(result.applied.proposal.pyramid_layers, 6)
        self.assertEqual(
            tuple(
                cluster.pyramid.reference_corner_lattice_keys
                for cluster in result.applied.proposal.clusters
            ),
            reference_corners,
        )

        with self.assertRaisesRegex(
            ElectrodeLatticeInteractionError,
            "invalid, occupied, or stale",
        ):
            add_lattice_extension_to_working_geometry(
                result.applied,
                result.working_structure,
                selected.identity,
                self.radii,
            )

    def test_blocked_add_is_an_atomic_no_op(self) -> None:
        snapshot = interaction_candidates_for_working_geometry(
            self.applied,
            self.applied.structure,
            self.radii,
        )
        blocked = next(
            item
            for item in snapshot
            if item.availability is LatticeExtensionAvailability.BLOCKED
        )
        before_applied = self.applied
        before_structure = self.applied.structure
        with self.assertRaisesRegex(
            ElectrodeLatticeInteractionError,
            "blocked by the current working geometry",
        ):
            add_lattice_extension_to_working_geometry(
                self.applied,
                self.applied.structure,
                blocked.identity,
                self.radii,
            )
        self.assertIs(self.applied, before_applied)
        self.assertIs(self.applied.structure, before_structure)


def _transform_side(applied, structure, side, transform):
    cluster = next(item for item in applied.proposal.clusters if item.side == side)
    indices = set(cluster.local_to_global_indices)
    indices.update(
        extension.global_atom_index
        for extension in applied.lattice_extensions
        if extension.side == side
    )
    atoms = list(structure.atoms)
    for atom_index in indices:
        atom = atoms[atom_index]
        atoms[atom_index] = Atom(
            atom.index,
            atom.element,
            *transform(_coordinates(atom)),
        )
    return MolecularStructure(tuple(atoms), comment=structure.comment)


def _move_atom(structure, atom_index, coordinates):
    atoms = list(structure.atoms)
    atom = atoms[atom_index]
    atoms[atom_index] = Atom(atom.index, atom.element, *coordinates)
    return MolecularStructure(tuple(atoms), comment=structure.comment)


def _coordinates(atom):
    return atom.x, atom.y, atom.z


if __name__ == "__main__":
    unittest.main()
