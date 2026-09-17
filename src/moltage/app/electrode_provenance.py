"""Conversion between accepted Au placement and durable project provenance."""

from moltage.domain.calculation_project import (
    ProjectElectrodeAtomIdentity,
    ProjectElectrodeClusterProvenance,
    ProjectElectrodeLatticeExtension,
)
from moltage.domain.electrode import AppliedElectrodePlacement


def provenance_from_applied_electrodes(
    applied: AppliedElectrodePlacement,
) -> tuple[ProjectElectrodeClusterProvenance, ProjectElectrodeClusterProvenance]:
    """Persist the exact identities and mapping accepted by Au Tool Done."""

    if not isinstance(applied, AppliedElectrodePlacement):
        raise TypeError("electrode provenance requires an applied placement")
    clusters = applied.proposal.clusters
    if len(clusters) != 2:
        raise ValueError("electrode provenance requires both accepted sides")
    return tuple(
        ProjectElectrodeClusterProvenance(
            side=cluster.side,
            geometry_model=cluster.pyramid.geometry_model,
            pyramid_layers=cluster.pyramid.pyramid_layers,
            nearest_neighbor_spacing_angstrom=(
                cluster.pyramid.nearest_neighbor_spacing_angstrom
            ),
            roll_degrees=cluster.roll_degrees,
            atom_identities=tuple(
                ProjectElectrodeAtomIdentity(
                    local_index=identity.local_index,
                    layer_index=identity.layer_index,
                    lattice_key=identity.lattice_key,
                    standard_pyramid_member=identity.standard_pyramid_member,
                )
                for identity in cluster.pyramid.atom_identities
            ),
            local_to_global_indices=cluster.local_to_global_indices,
            apex_lattice_key=cluster.pyramid.apex_lattice_key,
            reference_corner_lattice_keys=(
                cluster.pyramid.reference_corner_lattice_keys
            ),
            lattice_extensions=tuple(
                ProjectElectrodeLatticeExtension(
                    origin=extension.origin,
                    layer_index=extension.layer_index,
                    lattice_key=extension.lattice_key,
                    global_atom_index=extension.global_atom_index,
                )
                for extension in applied.lattice_extensions
                if extension.side == cluster.side
            ),
        )
        for cluster in clusters
    )
