"""Synthetic generated-electrode provenance for offline unit tests."""

from moltage.domain.au_pyramid import generate_au_pyramid
from moltage.domain.calculation_project import (
    ProjectElectrodeAtomIdentity,
    ProjectElectrodeClusterProvenance,
)
from moltage.domain.structure import Atom, MolecularStructure


def synthetic_project_electrode_provenance(
    *,
    source_atom_count: int = 2,
    pyramid_layers: int = 6,
    rolls: tuple[int | None, int | None] = (0, 18),
) -> tuple[ProjectElectrodeClusterProvenance, ProjectElectrodeClusterProvenance]:
    pyramid = generate_au_pyramid(pyramid_layers)
    new_count = len(pyramid.structure) - 1
    mappings = (
        (0, *range(source_atom_count, source_atom_count + new_count)),
        (
            1,
            *range(
                source_atom_count + new_count,
                source_atom_count + 2 * new_count,
            ),
        ),
    )
    identities = tuple(
        ProjectElectrodeAtomIdentity(
            identity.local_index,
            identity.layer_index,
            identity.lattice_key,
            identity.standard_pyramid_member,
        )
        for identity in pyramid.atom_identities
    )
    return tuple(
        ProjectElectrodeClusterProvenance(
            side=side,
            geometry_model=pyramid.geometry_model,
            pyramid_layers=pyramid_layers,
            nearest_neighbor_spacing_angstrom=(
                pyramid.nearest_neighbor_spacing_angstrom
            ),
            roll_degrees=roll,
            atom_identities=identities,
            local_to_global_indices=mapping,
            apex_lattice_key=pyramid.apex_lattice_key,
            reference_corner_lattice_keys=pyramid.reference_corner_lattice_keys,
        )
        for side, roll, mapping in zip(
            ("LEFT", "RIGHT"), rolls, mappings, strict=True
        )
    )


def synthetic_legacy_au59_structure(*, duplicate_left_apex=False):
    """Build old 58+58 ordering from Moltage geometry, not third-party files."""

    pyramid = generate_au_pyramid(6)
    atoms = [Atom(0, "C", 0.0, 0.0, 0.0)]
    left_apex = (-30.0, 0.0, 0.0)
    right_apex = (30.0, 0.0, 0.0)
    atoms.append(Atom(len(atoms), "Au", *left_apex))
    atoms.append(Atom(len(atoms), "Au", *right_apex))
    if duplicate_left_apex:
        atoms.append(Atom(len(atoms), "Au", *left_apex))
    for origin in (left_apex, right_apex):
        for atom in pyramid.structure.atoms[1:]:
            atoms.append(
                Atom(
                    len(atoms),
                    "Au",
                    atom.x + origin[0],
                    atom.y + origin[1],
                    atom.z + origin[2],
                )
            )
        for offset in range(3):
            atoms.append(
                Atom(
                    len(atoms),
                    "Au",
                    origin[0] + 20.0 + offset,
                    origin[1] + 20.0,
                    origin[2] + 20.0,
                )
            )
    return MolecularStructure(tuple(atoms))
