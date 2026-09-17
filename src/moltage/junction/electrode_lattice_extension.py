"""Pure enumeration and immutable addition for Au(111) lattice extensions."""

from dataclasses import dataclass
from math import dist

from moltage.domain.au_lattice_extension import (
    AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM,
    AuLatticeExtension,
    AuLatticeExtensionCandidate,
    AuLatticeExtensionError,
    AuLatticeFrame,
    AuLatticeExtensionSite,
    has_au_lattice_clearance,
    lattice_distance_squared,
    raw_extension_candidate_keys,
)
from moltage.domain.calculation_project import ProjectElectrodeClusterProvenance
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.electrode import AppliedElectrodePlacement
from moltage.domain.structure import Atom, MolecularStructure


class ElectrodeLatticeExtensionError(ValueError):
    """Raised when an Au lattice extension cannot be proven or applied."""


@dataclass(frozen=True, slots=True)
class LatticeExtensionAddResult:
    """A new immutable electrode state and its recomputed legal candidates."""

    applied: AppliedElectrodePlacement
    candidates: tuple[AuLatticeExtensionCandidate, ...]


def enumerate_lattice_extension_candidates(
    applied: AppliedElectrodePlacement,
) -> tuple[AuLatticeExtensionCandidate, ...]:
    """Return all currently legal finite candidates in deterministic order."""

    if not isinstance(applied, AppliedElectrodePlacement):
        raise TypeError("candidate enumeration requires an applied electrode placement")
    frames = _frames_from_applied(applied)
    occupied, _identity_by_global = _identity_state_from_applied(applied)
    all_au_coordinates = tuple(
        _coordinates(atom) for atom in applied.structure if atom.element == "Au"
    )
    candidates: list[AuLatticeExtensionCandidate] = []
    for cluster in applied.proposal.clusters:
        frame = frames[cluster.side]
        for layer_index in range(cluster.pyramid_layers):
            layer_occupancy = occupied[(cluster.side, layer_index)]
            try:
                raw_keys = raw_extension_candidate_keys(
                    layer_occupancy,
                    layer_index=layer_index,
                )
            except AuLatticeExtensionError as error:
                raise ElectrodeLatticeExtensionError(str(error)) from error
            for lattice_key in raw_keys:
                candidate = AuLatticeExtensionCandidate(
                    cluster.side,
                    layer_index,
                    lattice_key,
                    frame.coordinate(lattice_key),
                )
                if has_au_lattice_clearance(
                    candidate.coordinates,
                    all_au_coordinates,
                    spacing_angstrom=frame.spacing_angstrom,
                ):
                    candidates.append(candidate)
    return tuple(sorted(set(candidates), key=lambda item: item.sort_key))


def add_lattice_extension(
    applied: AppliedElectrodePlacement,
    candidate: AuLatticeExtensionCandidate,
) -> LatticeExtensionAddResult:
    """Revalidate and append exactly one Au without mutating the current state."""

    if not isinstance(applied, AppliedElectrodePlacement):
        raise TypeError("lattice extension requires an applied electrode placement")
    if not isinstance(candidate, AuLatticeExtensionCandidate):
        raise TypeError("lattice extension requires a typed candidate")
    available = enumerate_lattice_extension_candidates(applied)
    if candidate not in available:
        raise ElectrodeLatticeExtensionError(
            "Au lattice extension candidate is invalid, forged, occupied, or stale"
        )

    _occupied, identity_by_global = _identity_state_from_applied(applied)
    new_index = len(applied.structure)
    neighbor_indices = tuple(
        global_index
        for global_index, identity in sorted(identity_by_global.items())
        if identity.side == candidate.side
        and lattice_distance_squared(identity.lattice_key, candidate.lattice_key)
        == 1
    )
    if not neighbor_indices:
        raise ElectrodeLatticeExtensionError(
            "Au lattice extension candidate has no same-side lattice neighbor"
        )
    new_atom = Atom(new_index, "Au", *candidate.coordinates)
    structure = MolecularStructure(
        applied.structure.atoms + (new_atom,),
        comment=applied.structure.comment,
    )
    new_bonds = tuple(
        Bond(
            atom_index,
            new_index,
            dist(_coordinates(structure[atom_index]), candidate.coordinates),
        )
        for atom_index in neighbor_indices
    )
    connectivity = Connectivity(
        len(structure),
        applied.connectivity.bonds + new_bonds,
    )
    extension = AuLatticeExtension(
        candidate.side,
        candidate.layer_index,
        candidate.lattice_key,
        new_index,
    )
    updated = AppliedElectrodePlacement(
        structure=structure,
        connectivity=connectivity,
        proposal=applied.proposal,
        added_au_indices=applied.added_au_indices + (new_index,),
        lattice_extensions=applied.lattice_extensions + (extension,),
    )
    return LatticeExtensionAddResult(
        updated,
        enumerate_lattice_extension_candidates(updated),
    )


def validate_persisted_lattice_extensions(
    structure: MolecularStructure,
    provenance: tuple[ProjectElectrodeClusterProvenance, ...],
) -> None:
    """Replay persisted additions by global append order against current geometry."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("extension replay requires a MolecularStructure")
    records = tuple(provenance)
    frames = _frames_from_provenance(structure, records)
    extensions = tuple(
        (record.side, extension)
        for record in records
        for extension in record.lattice_extensions
    )
    if not extensions:
        return
    ordered = tuple(sorted(extensions, key=lambda item: item[1].global_atom_index))
    extension_indices = tuple(item[1].global_atom_index for item in ordered)
    first_extension_index = len(structure) - len(ordered)
    if extension_indices != tuple(range(first_extension_index, len(structure))):
        raise ElectrodeLatticeExtensionError(
            "lattice extension mappings must form the global geometry suffix"
        )

    occupied: dict[tuple[str, int], set[tuple[int, int, int]]] = {}
    for record in records:
        for layer_index in range(record.pyramid_layers):
            occupied[(record.side, layer_index)] = {
                identity.lattice_key
                for identity in record.atom_identities
                if identity.standard_pyramid_member
                and identity.layer_index == layer_index
            }
    existing_au = [
        _coordinates(atom)
        for atom in structure.atoms[:first_extension_index]
        if atom.element == "Au"
    ]
    for side, extension in ordered:
        frame = frames[side]
        key = extension.lattice_key
        layer_occupancy = occupied[(side, extension.layer_index)]
        try:
            raw_keys = raw_extension_candidate_keys(
                layer_occupancy,
                layer_index=extension.layer_index,
            )
        except AuLatticeExtensionError as error:
            raise ElectrodeLatticeExtensionError(str(error)) from error
        if key not in raw_keys:
            raise ElectrodeLatticeExtensionError(
                f"{side} lattice extension cannot be replayed in append order"
            )
        candidate = AuLatticeExtensionCandidate(
            side,
            extension.layer_index,
            key,
            frame.coordinate(key),
        )
        atom = structure[extension.global_atom_index]
        if atom.element != "Au":
            raise ElectrodeLatticeExtensionError(
                f"{side} lattice extension mapping contains a non-Au atom"
            )
        if dist(_coordinates(atom), candidate.coordinates) > (
            AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM
        ):
            raise ElectrodeLatticeExtensionError(
                f"{side} lattice extension coordinate conflicts with its identity"
            )
        if not has_au_lattice_clearance(
            candidate.coordinates,
            tuple(existing_au),
            spacing_angstrom=frame.spacing_angstrom,
        ):
            raise ElectrodeLatticeExtensionError(
                f"{side} lattice extension overlaps existing Au geometry"
            )
        layer_occupancy.add(key)
        existing_au.append(candidate.coordinates)


def _frames_from_applied(
    applied: AppliedElectrodePlacement,
) -> dict[str, AuLatticeFrame]:
    try:
        return {
            cluster.side: AuLatticeFrame.from_standard_mapping(
                side=cluster.side,
                pyramid_layers=cluster.pyramid_layers,
                spacing_angstrom=(
                    cluster.pyramid.nearest_neighbor_spacing_angstrom
                ),
                coordinates_by_key=tuple(
                    (
                        identity.lattice_key,
                        _coordinates(
                            applied.structure[
                                cluster.local_to_global_indices[
                                    identity.local_index
                                ]
                            ]
                        ),
                    )
                    for identity in cluster.pyramid.atom_identities
                ),
            )
            for cluster in applied.proposal.clusters
        }
    except AuLatticeExtensionError as error:
        raise ElectrodeLatticeExtensionError(str(error)) from error


def _frames_from_provenance(
    structure: MolecularStructure,
    records: tuple[ProjectElectrodeClusterProvenance, ...],
) -> dict[str, AuLatticeFrame]:
    try:
        return {
            record.side: AuLatticeFrame.from_standard_mapping(
                side=record.side,
                pyramid_layers=record.pyramid_layers,
                spacing_angstrom=record.nearest_neighbor_spacing_angstrom,
                coordinates_by_key=tuple(
                    (
                        identity.lattice_key,
                        _coordinates(
                            structure[
                                record.local_to_global_indices[
                                    identity.local_index
                                ]
                            ]
                        ),
                    )
                    for identity in record.atom_identities
                    if identity.standard_pyramid_member
                ),
            )
            for record in records
            if record.lattice_extensions
        }
    except (AuLatticeExtensionError, IndexError) as error:
        raise ElectrodeLatticeExtensionError(str(error)) from error


def _identity_state_from_applied(
    applied: AppliedElectrodePlacement,
) -> tuple[
    dict[tuple[str, int], set[tuple[int, int, int]]],
    dict[int, AuLatticeExtensionSite],
]:
    occupied: dict[tuple[str, int], set[tuple[int, int, int]]] = {}
    identity_by_global: dict[int, AuLatticeExtensionSite] = {}
    for cluster in applied.proposal.clusters:
        for layer_index in range(cluster.pyramid_layers):
            occupied[(cluster.side, layer_index)] = set()
        for identity in cluster.pyramid.atom_identities:
            site = AuLatticeExtensionSite(
                cluster.side,
                identity.layer_index,
                identity.lattice_key,
            )
            global_index = cluster.local_to_global_indices[identity.local_index]
            occupied[(site.side, site.layer_index)].add(site.lattice_key)
            identity_by_global[global_index] = site
    for extension in applied.lattice_extensions:
        site = extension.identity
        occupied[(site.side, site.layer_index)].add(site.lattice_key)
        identity_by_global[extension.global_atom_index] = site
    return occupied, identity_by_global


def _coordinates(atom: Atom) -> tuple[float, float, float]:
    return atom.x, atom.y, atom.z
