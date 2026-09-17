from dataclasses import replace
from math import dist
import unittest
from unittest.mock import patch

from moltage.domain.au_lattice_extension import (
    SAME_LAYER_NEIGHBOR_DELTAS,
    AuLatticeExtensionCandidate,
    AuLatticeExtensionError,
    AuLatticeExtensionSite,
    AuLatticeFrame,
    has_au_lattice_clearance,
    lattice_distance_squared,
    raw_extension_candidate_keys,
    same_layer_neighbor_keys,
)
from moltage.domain.au_pyramid import generate_au_pyramid
from moltage.domain.connectivity import Connectivity
from moltage.domain.electrode import AppliedElectrodePlacement
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from moltage.junction.electrode_lattice_extension import (
    ElectrodeLatticeExtensionError,
    add_lattice_extension,
    enumerate_lattice_extension_candidates,
)
from synthetic_structure_test_support import synthetic_step2_state


class SignedAuLatticeDomainTests(unittest.TestCase):
    def test_signed_identity_and_fixed_neighbors_preserve_layer_sum(self) -> None:
        site = AuLatticeExtensionSite("left", 0, (1, -1, 0))
        self.assertEqual(site.side, "LEFT")
        self.assertEqual(
            SAME_LAYER_NEIGHBOR_DELTAS,
            (
                (1, -1, 0),
                (-1, 1, 0),
                (1, 0, -1),
                (-1, 0, 1),
                (0, 1, -1),
                (0, -1, 1),
            ),
        )
        self.assertEqual(len(same_layer_neighbor_keys(site.lattice_key)), 6)
        self.assertTrue(
            all(sum(key) == site.layer_index for key in same_layer_neighbor_keys(site.lattice_key))
        )
        with self.assertRaisesRegex(AuLatticeExtensionError, "recorded layer"):
            AuLatticeExtensionSite("LEFT", 1, (1, -1, 0))

    def test_identity_anchored_frame_supports_independent_rigid_transforms(self) -> None:
        pyramid = generate_au_pyramid(4)
        transforms = {
            "LEFT": lambda point: (
                -point[1] - 12.0,
                point[0] + 2.0,
                point[2] - 1.0,
            ),
            "RIGHT": lambda point: (
                point[2] + 14.0,
                point[1] - 3.0,
                -point[0] + 4.0,
            ),
        }
        frames = {}
        for side, transform in transforms.items():
            entries = tuple(
                (
                    identity.lattice_key,
                    transform(_coordinates(pyramid.structure[identity.local_index])),
                )
                for identity in pyramid.atom_identities
            )
            frames[side] = AuLatticeFrame.from_standard_mapping(
                side=side,
                pyramid_layers=4,
                spacing_angstrom=pyramid.nearest_neighbor_spacing_angstrom,
                coordinates_by_key=entries,
            )
        signed_key = (-2, 3, 1)
        self.assertNotEqual(
            frames["LEFT"].coordinate(signed_key),
            frames["RIGHT"].coordinate(signed_key),
        )
        self.assertAlmostEqual(
            dist(
                frames["LEFT"].coordinate(signed_key),
                frames["LEFT"].coordinate((0, 2, 0)),
            ),
            dist(
                frames["RIGHT"].coordinate(signed_key),
                frames["RIGHT"].coordinate((0, 2, 0)),
            ),
            places=10,
        )

    def test_frame_rejects_ambiguous_and_non_rigid_standard_mapping(self) -> None:
        pyramid = generate_au_pyramid(3)
        entries = [
            (identity.lattice_key, _coordinates(pyramid.structure[identity.local_index]))
            for identity in pyramid.atom_identities
        ]
        with self.assertRaisesRegex(AuLatticeExtensionError, "ambiguous"):
            AuLatticeFrame.from_standard_mapping(
                side="LEFT",
                pyramid_layers=3,
                spacing_angstrom=pyramid.nearest_neighbor_spacing_angstrom,
                coordinates_by_key=(*entries, entries[0]),
            )
        shifted = list(entries)
        key, point = shifted[-1]
        shifted[-1] = (key, (point[0] + 0.01, point[1], point[2]))
        with self.assertRaisesRegex(AuLatticeExtensionError, "not rigid"):
            AuLatticeFrame.from_standard_mapping(
                side="LEFT",
                pyramid_layers=3,
                spacing_angstrom=pyramid.nearest_neighbor_spacing_angstrom,
                coordinates_by_key=shifted,
            )

    def test_seed_and_boundary_generation_are_finite_and_deterministic(self) -> None:
        apex = (0, 0, 0)
        first = (1, -1, 0)
        third = (1, 0, -1)
        apex_candidates = raw_extension_candidate_keys({apex}, layer_index=0)
        self.assertEqual(len(apex_candidates), 6)
        self.assertEqual(apex_candidates, tuple(sorted(apex_candidates)))
        self.assertEqual(
            raw_extension_candidate_keys({apex, first}, layer_index=0),
            ((0, -1, 1), (1, 0, -1)),
        )
        with self.assertRaisesRegex(AuLatticeExtensionError, "adjacent"):
            raw_extension_candidate_keys({apex, (2, -2, 0)}, layer_index=0)
        boundary = raw_extension_candidate_keys(
            {apex, first, third},
            layer_index=0,
        )
        self.assertEqual(
            boundary,
            ((0, -1, 1), (0, 1, -1), (2, -1, -1)),
        )
        self.assertEqual(len(boundary), len(set(boundary)))

    def test_every_supported_standard_layer_produces_candidates(self) -> None:
        for pyramid_layers in range(2, 11):
            pyramid = generate_au_pyramid(pyramid_layers)
            for layer_index in range(pyramid_layers):
                with self.subTest(
                    pyramid_layers=pyramid_layers,
                    layer_index=layer_index,
                ):
                    occupied = {
                        identity.lattice_key
                        for identity in pyramid.atom_identities
                        if identity.layer_index == layer_index
                    }
                    candidates = raw_extension_candidate_keys(
                        occupied,
                        layer_index=layer_index,
                    )
                    self.assertTrue(candidates)
                    self.assertTrue(occupied.isdisjoint(candidates))

    def test_clearance_uses_only_spacing_and_numerical_tolerance(self) -> None:
        spacing = generate_au_pyramid(2).nearest_neighbor_spacing_angstrom
        self.assertTrue(
            has_au_lattice_clearance(
                (0.0, 0.0, 0.0),
                ((spacing, 0.0, 0.0),),
                spacing_angstrom=spacing,
            )
        )
        self.assertFalse(
            has_au_lattice_clearance(
                (0.0, 0.0, 0.0),
                ((spacing - 1.0e-4, 0.0, 0.0),),
                spacing_angstrom=spacing,
            )
        )
        self.assertFalse(
            has_au_lattice_clearance(
                (0.0, 0.0, 0.0),
                ((0.0, 0.0, 0.0),),
                spacing_angstrom=spacing,
            )
        )


class AppliedAuLatticeExtensionTests(unittest.TestCase):
    def test_zero_extension_state_preserves_the_exact_standard_preview(self) -> None:
        applied = _applied_electrodes()
        self.assertEqual(applied.lattice_extensions, ())
        self.assertIs(applied.structure, applied.proposal.preview_structure)
        self.assertIs(applied.connectivity, applied.proposal.preview_connectivity)

    def test_add_revalidates_appends_and_connects_only_same_side_neighbors(self) -> None:
        applied = _applied_electrodes()
        candidates = enumerate_lattice_extension_candidates(applied)
        selected = next(
            item
            for item in candidates
            if item.side == "LEFT" and item.layer_index == 0
        )
        result = add_lattice_extension(applied, selected)
        updated = result.applied
        extension = updated.lattice_extensions[-1]
        self.assertEqual(extension.identity, selected.identity)
        self.assertEqual(extension.global_atom_index, len(applied.structure))
        self.assertEqual(updated.added_au_indices[-1], extension.global_atom_index)
        self.assertEqual(updated.structure.atoms[:-1], applied.structure.atoms)
        self.assertEqual(
            set(applied.connectivity.bonds),
            {
                bond
                for bond in updated.connectivity.bonds
                if extension.global_atom_index
                not in {bond.first_index, bond.second_index}
            },
        )
        cluster = applied.proposal.clusters[0]
        identity_by_global = {
            cluster.local_to_global_indices[identity.local_index]: identity.lattice_key
            for identity in cluster.pyramid.atom_identities
        }
        expected_neighbors = {
            global_index
            for global_index, key in identity_by_global.items()
            if lattice_distance_squared(key, selected.lattice_key) == 1
        }
        actual_neighbors = {
            bond.first_index
            if bond.second_index == extension.global_atom_index
            else bond.second_index
            for bond in updated.connectivity.bonds
            if extension.global_atom_index
            in {bond.first_index, bond.second_index}
        }
        self.assertEqual(actual_neighbors, expected_neighbors)
        neighbor_layers = {
            identity.layer_index
            for identity in cluster.pyramid.atom_identities
            if cluster.local_to_global_indices[identity.local_index]
            in actual_neighbors
        }
        self.assertLessEqual({0, 1}, neighbor_layers)
        self.assertEqual(
            len(
                [
                    item
                    for item in result.candidates
                    if item.side == "LEFT" and item.layer_index == 0
                ]
            ),
            2,
        )

    def test_repeated_add_supports_boundary_growth_layers_and_asymmetric_sides(self) -> None:
        applied = _applied_electrodes()
        requests = (("LEFT", 0), ("LEFT", 0), ("RIGHT", 1), ("LEFT", 5))
        for side, layer_index in requests:
            candidate = next(
                item
                for item in enumerate_lattice_extension_candidates(applied)
                if item.side == side and item.layer_index == layer_index
            )
            applied = add_lattice_extension(applied, candidate).applied
        self.assertEqual(
            tuple(extension.side for extension in applied.lattice_extensions),
            ("LEFT", "LEFT", "RIGHT", "LEFT"),
        )
        self.assertEqual(
            tuple(extension.layer_index for extension in applied.lattice_extensions),
            (0, 0, 1, 5),
        )
        self.assertEqual(applied.proposal.pyramid_layers, 6)

        one_side = _applied_electrodes(selected_side="LEFT")
        for _ in range(24):
            candidate = next(
                item
                for item in enumerate_lattice_extension_candidates(one_side)
                if item.layer_index == 5
            )
            one_side = add_lattice_extension(one_side, candidate).applied
        self.assertEqual(len(one_side.lattice_extensions), 24)

    def test_forged_occupied_side_mismatch_and_stale_additions_are_atomic(self) -> None:
        applied = _applied_electrodes()
        selected = enumerate_lattice_extension_candidates(applied)[0]
        invalid = (
            replace(
                selected,
                coordinates=(
                    selected.coordinates[0] + 0.01,
                    selected.coordinates[1],
                    selected.coordinates[2],
                ),
            ),
            replace(selected, side="RIGHT" if selected.side == "LEFT" else "LEFT"),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                before = applied
                with self.assertRaisesRegex(
                    ElectrodeLatticeExtensionError,
                    "invalid, forged, occupied, or stale",
                ):
                    add_lattice_extension(applied, candidate)
                self.assertIs(applied, before)

        updated = add_lattice_extension(applied, selected).applied
        before = updated
        with self.assertRaisesRegex(ElectrodeLatticeExtensionError, "stale"):
            add_lattice_extension(updated, selected)
        self.assertIs(updated, before)

    def test_applied_record_rejects_changed_standard_atoms_mapping_or_bonds(self) -> None:
        applied = _applied_electrodes()
        atoms = list(applied.structure.atoms)
        atom = atoms[-1]
        atoms[-1] = Atom(atom.index, atom.element, atom.x + 0.01, atom.y, atom.z)
        with self.assertRaisesRegex(ValueError, "standard atoms"):
            AppliedElectrodePlacement(
                MolecularStructure(tuple(atoms), comment=applied.structure.comment),
                applied.connectivity,
                applied.proposal,
                applied.added_au_indices,
            )
        with self.assertRaisesRegex(ValueError, "connectivity"):
            AppliedElectrodePlacement(
                applied.structure,
                Connectivity(
                    applied.connectivity.atom_count,
                    applied.connectivity.bonds[1:],
                ),
                applied.proposal,
                applied.added_au_indices,
            )
        cluster = applied.proposal.clusters[0]
        mapping = list(cluster.local_to_global_indices)
        mapping[1], mapping[2] = mapping[2], mapping[1]
        changed_cluster = replace(cluster, local_to_global_indices=tuple(mapping))
        with self.assertRaises(ValueError):
            replace(
                applied.proposal,
                clusters=(changed_cluster, *applied.proposal.clusters[1:]),
            )

    def test_existing_au_overlap_is_filtered_without_radius_resources(self) -> None:
        base = _applied_electrodes(selected_side="LEFT")
        blocked = next(
            item
            for item in enumerate_lattice_extension_candidates(base)
            if item.layer_index == 0
        )
        structure, connectivity, _anchors, sites = synthetic_step2_state()
        blocker = Atom(len(structure), "Au", *blocked.coordinates)
        blocked_source = MolecularStructure(
            structure.atoms + (blocker,),
            comment=structure.comment,
        )
        blocked_connectivity = Connectivity(len(blocked_source), connectivity.bonds)
        proposal = propose_electrode_placement(
            blocked_source,
            blocked_connectivity,
            sites,
            (sites[0],),
        )
        applied = apply_electrode_placement(
            blocked_source,
            blocked_connectivity,
            proposal,
        )
        identities = {
            (item.side, item.layer_index, item.lattice_key)
            for item in enumerate_lattice_extension_candidates(applied)
        }
        self.assertNotIn(
            (blocked.side, blocked.layer_index, blocked.lattice_key),
            identities,
        )
        self.assertNotIn("radius", enumerate_lattice_extension_candidates.__doc__.casefold())

    def test_opposite_side_au_collision_is_filtered(self) -> None:
        applied = _applied_electrodes()
        blocked = next(
            item
            for item in enumerate_lattice_extension_candidates(applied)
            if item.side == "LEFT" and item.layer_index == 0
        )
        proposal = applied.proposal
        right = proposal.clusters[1]
        old_apex = right.transformed_coordinates[0]
        translation = tuple(
            blocked.coordinates[index] - old_apex[index] for index in range(3)
        )
        moved_right_coordinates = tuple(
            tuple(point[index] + translation[index] for index in range(3))
            for point in right.transformed_coordinates
        )
        moved_right = replace(
            right,
            transformed_coordinates=moved_right_coordinates,
        )
        source_atoms = list(proposal.source_structure.atoms)
        contact = source_atoms[right.apex_atom_index]
        source_atoms[right.apex_atom_index] = Atom(
            contact.index,
            contact.element,
            *blocked.coordinates,
        )
        moved_source = MolecularStructure(
            tuple(source_atoms),
            comment=proposal.source_structure.comment,
        )
        preview_atoms = list(proposal.preview_structure.atoms)
        for local_index, global_index in enumerate(right.local_to_global_indices):
            atom = preview_atoms[global_index]
            preview_atoms[global_index] = Atom(
                atom.index,
                atom.element,
                *moved_right_coordinates[local_index],
            )
        moved_proposal = replace(
            proposal,
            source_structure=moved_source,
            clusters=(proposal.clusters[0], moved_right),
            preview_structure=MolecularStructure(
                tuple(preview_atoms),
                comment=proposal.preview_structure.comment,
            ),
        )
        moved_applied = AppliedElectrodePlacement(
            moved_proposal.preview_structure,
            moved_proposal.preview_connectivity,
            moved_proposal,
            moved_proposal.added_au_indices,
        )

        identities = {
            item.identity
            for item in enumerate_lattice_extension_candidates(moved_applied)
        }
        self.assertNotIn(blocked.identity, identities)

    def test_candidate_pipeline_does_not_load_scientific_radius_or_anchor_data(self) -> None:
        applied = _applied_electrodes()
        with (
            patch(
                "moltage.structure.covalent_radii.load_default_covalent_radii",
                side_effect=AssertionError("covalent radii consulted"),
            ),
            patch(
                "moltage.structure.vdw_radii.load_default_vdw_radii",
                side_effect=AssertionError("vdW radii consulted"),
            ),
            patch(
                "moltage.junction.placement_defaults.load_default_au_placement_defaults",
                side_effect=AssertionError("anchor defaults consulted"),
            ),
        ):
            candidates = enumerate_lattice_extension_candidates(applied)
        self.assertTrue(candidates)


def _applied_electrodes(*, selected_side: str | None = None):
    structure, connectivity, _anchors, sites = synthetic_step2_state()
    selected = sites
    if selected_side == "LEFT":
        selected = (sites[0],)
    elif selected_side == "RIGHT":
        selected = (sites[1],)
    proposal = propose_electrode_placement(
        structure,
        connectivity,
        sites,
        selected,
    )
    return apply_electrode_placement(structure, connectivity, proposal)


def _coordinates(atom: Atom) -> tuple[float, float, float]:
    return atom.x, atom.y, atom.z


if __name__ == "__main__":
    unittest.main()
