from dataclasses import replace
from functools import lru_cache
import unittest

from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.app.project_planning import create_initial_project
from moltage.domain.calculation_project import ProjectStepKind
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from moltage.junction.electrode_surface import (
    ElectrodeSurfaceError,
    propose_electrode_surfaces,
    recover_legacy_normal_electrode_provenance,
    resolve_project_electrode_surfaces,
)
from datetime import datetime, timezone
from uuid import UUID
from synthetic_structure_test_support import (
    synthetic_electrode_placement,
    synthetic_extended_electrode_placement,
    synthetic_step2_state,
)
from electrode_test_support import synthetic_legacy_au59_structure


@lru_cache(maxsize=9)
def accepted_shape(pyramid_layers=6):
    structure, connectivity, _anchors, sites = synthetic_step2_state()
    proposal = propose_electrode_placement(
        structure,
        connectivity,
        sites,
        sites,
        pyramid_layers=pyramid_layers,
    )
    applied = apply_electrode_placement(structure, connectivity, proposal)
    return applied.structure, provenance_from_applied_electrodes(applied)


class ElectrodeSurfaceTests(unittest.TestCase):
    def test_generated_mapping_resolves_dynamic_corners_to_one_based_indices(self):
        structure, provenance = accepted_shape()
        proposal = propose_electrode_surfaces(structure, provenance)

        self.assertEqual(len(structure), 128)
        self.assertEqual(proposal.source_atom_count, 18)
        self.assertEqual(proposal.pyramid_layers, 6)
        self.assertEqual(proposal.left_zero_based, (72, 57, 52))
        self.assertEqual(proposal.right_zero_based, (127, 112, 107))
        self.assertEqual(proposal.left_one_based, (73, 58, 53))
        self.assertEqual(proposal.right_one_based, (128, 113, 108))
        self.assertEqual(proposal.left_electrode_zero_based[0], 16)
        self.assertEqual(proposal.right_electrode_zero_based[0], 17)
        self.assertEqual(len(proposal.left_electrode_zero_based), 56)
        self.assertEqual(len(proposal.right_electrode_zero_based), 56)

    def test_multiple_sizes_use_persisted_reference_identities(self):
        for layers, atom_count in ((2, 4), (6, 56), (10, 220)):
            with self.subTest(layers=layers):
                structure, provenance = accepted_shape(layers)
                surface = propose_electrode_surfaces(structure, provenance)
                self.assertEqual(surface.pyramid_layers, layers)
                self.assertEqual(len(surface.left_electrode_zero_based), atom_count)
                self.assertEqual(len(surface.right_electrode_zero_based), atom_count)
                self.assertEqual(len(set(surface.left_zero_based)), 3)
                self.assertEqual(len(set(surface.right_zero_based)), 3)

    def test_extensions_expand_membership_without_changing_reference_corners(self):
        standard = synthetic_electrode_placement()
        extended = synthetic_extended_electrode_placement()
        standard_surface = propose_electrode_surfaces(
            standard.structure,
            provenance_from_applied_electrodes(standard),
        )
        provenance = provenance_from_applied_electrodes(extended)
        surface = propose_electrode_surfaces(extended.structure, provenance)

        self.assertEqual(surface.source_atom_count, 18)
        self.assertEqual(surface.left_zero_based, standard_surface.left_zero_based)
        self.assertEqual(surface.right_zero_based, standard_surface.right_zero_based)
        self.assertEqual(len(surface.left_electrode_zero_based), 58)
        self.assertEqual(len(surface.right_electrode_zero_based), 57)
        self.assertEqual(
            surface.left_electrode_zero_based[-2:],
            (128, 130),
        )
        self.assertEqual(surface.right_electrode_zero_based[-1:], (129,))

    def test_extension_coordinate_mapping_and_replay_fail_explicitly(self):
        extended = synthetic_extended_electrode_placement()
        provenance = provenance_from_applied_electrodes(extended)

        atoms = list(extended.structure.atoms)
        atom = atoms[128]
        atoms[128] = Atom(atom.index, "C", atom.x, atom.y, atom.z)
        with self.assertRaisesRegex(ElectrodeSurfaceError, "non-Au"):
            propose_electrode_surfaces(MolecularStructure(tuple(atoms)), provenance)

        atoms = list(extended.structure.atoms)
        atom = atoms[128]
        atoms[128] = Atom(atom.index, "Au", atom.x + 0.01, atom.y, atom.z)
        with self.assertRaisesRegex(ElectrodeSurfaceError, "coordinate"):
            propose_electrode_surfaces(MolecularStructure(tuple(atoms)), provenance)

        first_extension = provenance[0].lattice_extensions[0]
        invalid_extension = replace(
            first_extension,
            lattice_key=(10, -10, 0),
        )
        invalid_left = replace(
            provenance[0],
            lattice_extensions=(
                invalid_extension,
                *provenance[0].lattice_extensions[1:],
            ),
        )
        with self.assertRaisesRegex(ElectrodeSurfaceError, "replayed"):
            propose_electrode_surfaces(
                extended.structure,
                (invalid_left, provenance[1]),
            )

    def test_altered_or_cross_side_extension_mapping_is_rejected(self):
        extended = synthetic_extended_electrode_placement()
        provenance = provenance_from_applied_electrodes(extended)
        changed_extension = replace(
            provenance[0].lattice_extensions[0],
            global_atom_index=129,
        )
        changed_left = replace(
            provenance[0],
            lattice_extensions=(
                changed_extension,
                *provenance[0].lattice_extensions[1:],
            ),
        )
        with self.assertRaisesRegex(
            ElectrodeSurfaceError,
            "mapping|overlaps",
        ):
            propose_electrode_surfaces(
                extended.structure,
                (changed_left, provenance[1]),
            )

    def test_reordered_or_mismatched_structure_is_rejected(self):
        structure, provenance = accepted_shape()
        atoms = list(structure.atoms)
        atom = atoms[20]
        atoms[20] = Atom(20, "Au", atom.x + 1.0, atom.y, atom.z)
        with self.assertRaisesRegex(ElectrodeSurfaceError, "provenance"):
            propose_electrode_surfaces(MolecularStructure(tuple(atoms)), provenance)

        malformed = replace(
            provenance[0],
            local_to_global_indices=(
                provenance[0].local_to_global_indices[1],
                provenance[0].local_to_global_indices[0],
                *provenance[0].local_to_global_indices[2:],
            ),
        )
        with self.assertRaises(ElectrodeSurfaceError):
            propose_electrode_surfaces(structure, (malformed, provenance[1]))

    def test_legacy_normal_mapping_is_recovered_only_from_unique_evidence(self):
        structure = synthetic_legacy_au59_structure()

        provenance = recover_legacy_normal_electrode_provenance(structure)
        surface = propose_electrode_surfaces(structure, provenance)

        self.assertEqual(
            tuple(item.geometry_model for item in provenance),
            ("LegacyAu59V1", "LegacyAu59V1"),
        )
        self.assertEqual(tuple(item.contact_au_index for item in provenance), (1, 2))
        self.assertEqual(tuple(len(item.local_to_global_indices) for item in provenance), (59, 59))
        self.assertEqual(len(surface.left_electrode_zero_based), 56)
        self.assertEqual(len(surface.right_electrode_zero_based), 56)

        project = create_initial_project(
            base_name="LegacyNormal",
            remote_directory_name="LegacyNormal.20300101",
            source_molecule_name="source.xyz",
            server_profile_id=UUID("10000000-0000-4000-8000-000000000001"),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.MOLECULE_AU_OPT,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        project = replace(project, legacy_electrode_recovery_allowed=True)
        self.assertEqual(resolve_project_electrode_surfaces(project, structure), surface)

    def test_legacy_normal_mapping_rejects_ambiguous_or_changed_evidence(self):
        ambiguous = synthetic_legacy_au59_structure(duplicate_left_apex=True)
        with self.assertRaisesRegex(ElectrodeSurfaceError, "not unique"):
            recover_legacy_normal_electrode_provenance(ambiguous)

        structure = synthetic_legacy_au59_structure()
        atoms = list(structure.atoms)
        first = atoms[3]
        atoms[3] = Atom(first.index, "Au", first.x + 0.1, first.y, first.z)
        with self.assertRaisesRegex(ElectrodeSurfaceError, "ordering/geometry"):
            recover_legacy_normal_electrode_provenance(
                MolecularStructure(tuple(atoms))
            )


if __name__ == "__main__":
    unittest.main()
