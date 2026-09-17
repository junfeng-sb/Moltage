"""Current-geometry interaction state for canonical Au lattice extensions."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import dist

from moltage.domain.au_lattice_extension import (
    AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM,
    AuLatticeExtensionCandidate,
    AuLatticeExtensionError,
    AuLatticeExtensionSite,
    AuLatticeFrame,
    has_au_lattice_clearance,
    lattice_distance_squared,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.electrode import AppliedElectrodePlacement
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.electrode_lattice_extension import (
    ElectrodeLatticeExtensionError,
    add_lattice_extension,
    enumerate_lattice_extension_candidates,
)
from moltage.junction.steric_orientation import (
    StericOrientationError,
    non_au_steric_clearance_for_point,
)


Vector3 = tuple[float, float, float]


class ElectrodeLatticeInteractionError(ValueError):
    """Raised when current geometry cannot support lattice interaction."""


class LatticeExtensionAvailability(StrEnum):
    """Transient addability of one Phase-2 lattice candidate."""

    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class LatticeExtensionInteractionCandidate:
    """One canonical identity mapped into the current working geometry."""

    identity: AuLatticeExtensionSite
    coordinates: Vector3
    availability: LatticeExtensionAvailability
    predicted_bond_atom_indices: tuple[int, ...]
    predicted_bond_coordinates: tuple[Vector3, ...]
    layer_basis_u: Vector3
    layer_basis_v: Vector3
    blocking_atom_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AuLatticeExtensionSite):
            raise TypeError("interaction candidate requires a lattice identity")
        coordinates = _finite_vector(self.coordinates, "interaction coordinates")
        if not isinstance(self.availability, LatticeExtensionAvailability):
            raise TypeError("interaction candidate requires an availability state")
        blocker = self.blocking_atom_index
        if blocker is not None and (
            isinstance(blocker, bool) or not isinstance(blocker, int) or blocker < 0
        ):
            raise ValueError("blocking atom index must be non-negative")
        if self.availability is LatticeExtensionAvailability.AVAILABLE:
            if blocker is not None:
                raise ValueError("available candidate cannot have a blocking atom")
        neighbor_indices = tuple(self.predicted_bond_atom_indices)
        if not neighbor_indices or any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in neighbor_indices
        ):
            raise ValueError(
                "predicted bond atom indices must contain non-negative integers"
            )
        if len(set(neighbor_indices)) != len(neighbor_indices):
            raise ValueError("predicted bond atom indices must be unique")
        neighbor_coordinates = tuple(
            _finite_vector(point, "predicted bond coordinate")
            for point in self.predicted_bond_coordinates
        )
        if len(neighbor_coordinates) != len(neighbor_indices):
            raise ValueError(
                "predicted bond indices and coordinates must have equal length"
            )
        basis_u = _finite_vector(self.layer_basis_u, "layer basis u")
        basis_v = _finite_vector(self.layer_basis_v, "layer basis v")
        if _squared_length(basis_u) == 0.0 or _squared_length(basis_v) == 0.0:
            raise ValueError("layer basis vectors must be non-zero")
        if _squared_length(_cross(basis_u, basis_v)) == 0.0:
            raise ValueError("layer basis vectors must be independent")
        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "predicted_bond_atom_indices", neighbor_indices)
        object.__setattr__(self, "predicted_bond_coordinates", neighbor_coordinates)
        object.__setattr__(self, "layer_basis_u", basis_u)
        object.__setattr__(self, "layer_basis_v", basis_v)

    @property
    def sort_key(self) -> tuple[int, int, int, int, int]:
        return self.identity.sort_key


@dataclass(frozen=True, slots=True)
class CurrentLatticeExtensionAddResult:
    """Prepared canonical provenance and current-world geometry after one add."""

    applied: AppliedElectrodePlacement
    working_structure: MolecularStructure
    connectivity: Connectivity
    identity: AuLatticeExtensionSite


def interaction_candidates_for_working_geometry(
    applied: AppliedElectrodePlacement,
    working_structure: MolecularStructure,
    vdw_radii: Mapping[str, float],
) -> tuple[LatticeExtensionInteractionCandidate, ...]:
    """Map and classify one cached Phase-2 candidate snapshot."""

    _validate_working_identity(applied, working_structure)
    frames = _current_frames(applied, working_structure)
    _validate_current_extensions(applied, working_structure, frames)
    identity_by_global = _identity_by_global_atom_index(applied)
    canonical_candidates = enumerate_lattice_extension_candidates(applied)
    return tuple(
        _interaction_candidate(
            candidate,
            frames[candidate.side].coordinate(candidate.lattice_key),
            working_structure,
            frames[candidate.side],
            vdw_radii,
            _predicted_neighbor_indices(candidate, identity_by_global),
        )
        for candidate in canonical_candidates
    )


def add_lattice_extension_to_working_geometry(
    applied: AppliedElectrodePlacement,
    working_structure: MolecularStructure,
    identity: AuLatticeExtensionSite,
    vdw_radii: Mapping[str, float],
) -> CurrentLatticeExtensionAddResult:
    """Freshly revalidate one identity and prepare one current-world add."""

    if not isinstance(identity, AuLatticeExtensionSite):
        raise TypeError("working-geometry add requires a lattice identity")
    snapshot = interaction_candidates_for_working_geometry(
        applied,
        working_structure,
        vdw_radii,
    )
    matches = tuple(item for item in snapshot if item.identity == identity)
    if len(matches) != 1:
        raise ElectrodeLatticeInteractionError(
            "Au lattice extension identity is invalid, occupied, or stale"
        )
    current = matches[0]
    if current.availability is LatticeExtensionAvailability.BLOCKED:
        raise ElectrodeLatticeInteractionError(
            "Au lattice extension is blocked by the current working geometry"
        )
    canonical_matches = tuple(
        candidate
        for candidate in enumerate_lattice_extension_candidates(applied)
        if candidate.identity == identity
    )
    if len(canonical_matches) != 1:
        raise ElectrodeLatticeInteractionError(
            "Au lattice extension identity became stale before addition"
        )
    try:
        canonical_result = add_lattice_extension(applied, canonical_matches[0])
    except ElectrodeLatticeExtensionError as error:
        raise ElectrodeLatticeInteractionError(str(error)) from error
    new_index = len(working_structure)
    extension = canonical_result.applied.lattice_extensions[-1]
    if extension.global_atom_index != new_index or extension.identity != identity:
        raise ElectrodeLatticeInteractionError(
            "Phase-2 addition returned an inconsistent extension mapping"
        )
    next_structure = MolecularStructure(
        working_structure.atoms
        + (Atom(new_index, "Au", *current.coordinates),),
        comment=working_structure.comment,
    )
    return CurrentLatticeExtensionAddResult(
        canonical_result.applied,
        next_structure,
        canonical_result.applied.connectivity,
        identity,
    )


def _interaction_candidate(
    candidate: AuLatticeExtensionCandidate,
    coordinates: Vector3,
    working_structure: MolecularStructure,
    frame: AuLatticeFrame,
    vdw_radii: Mapping[str, float],
    predicted_neighbor_indices: tuple[int, ...],
) -> LatticeExtensionInteractionCandidate:
    predicted_neighbor_coordinates = tuple(
        _coordinates(working_structure[index])
        for index in predicted_neighbor_indices
    )
    layer_basis_u = _subtract(frame.basis_i, frame.basis_j)
    layer_basis_v = _subtract(frame.basis_i, frame.basis_k)
    all_au_coordinates = tuple(
        _coordinates(atom) for atom in working_structure if atom.element == "Au"
    )
    if not has_au_lattice_clearance(
        coordinates,
        all_au_coordinates,
        spacing_angstrom=frame.spacing_angstrom,
    ):
        blocker = _closest_atom_index(
            coordinates,
            working_structure,
            element="Au",
        )
        return LatticeExtensionInteractionCandidate(
            candidate.identity,
            coordinates,
            LatticeExtensionAvailability.BLOCKED,
            predicted_neighbor_indices,
            predicted_neighbor_coordinates,
            layer_basis_u,
            layer_basis_v,
            blocker,
        )
    try:
        clearance = non_au_steric_clearance_for_point(
            working_structure,
            coordinates,
            vdw_radii,
        )
    except StericOrientationError as error:
        raise ElectrodeLatticeInteractionError(str(error)) from error
    if clearance.minimum < 0.0:
        return LatticeExtensionInteractionCandidate(
            candidate.identity,
            coordinates,
            LatticeExtensionAvailability.BLOCKED,
            predicted_neighbor_indices,
            predicted_neighbor_coordinates,
            layer_basis_u,
            layer_basis_v,
            clearance.closest_atom_index,
        )
    return LatticeExtensionInteractionCandidate(
        candidate.identity,
        coordinates,
        LatticeExtensionAvailability.AVAILABLE,
        predicted_neighbor_indices,
        predicted_neighbor_coordinates,
        layer_basis_u,
        layer_basis_v,
    )


def _identity_by_global_atom_index(
    applied: AppliedElectrodePlacement,
) -> dict[int, AuLatticeExtensionSite]:
    identity_by_global: dict[int, AuLatticeExtensionSite] = {}
    for cluster in applied.proposal.clusters:
        for identity in cluster.pyramid.atom_identities:
            global_index = cluster.local_to_global_indices[identity.local_index]
            identity_by_global[global_index] = AuLatticeExtensionSite(
                cluster.side,
                identity.layer_index,
                identity.lattice_key,
            )
    for extension in applied.lattice_extensions:
        identity_by_global[extension.global_atom_index] = extension.identity
    return identity_by_global


def _predicted_neighbor_indices(
    candidate: AuLatticeExtensionCandidate,
    identity_by_global: Mapping[int, AuLatticeExtensionSite],
) -> tuple[int, ...]:
    neighbors = tuple(
        global_index
        for global_index, identity in sorted(identity_by_global.items())
        if identity.side == candidate.side
        and lattice_distance_squared(identity.lattice_key, candidate.lattice_key)
        == 1
    )
    if not neighbors:
        raise ElectrodeLatticeInteractionError(
            "Au lattice extension candidate has no canonical lattice neighbor"
        )
    return neighbors


def _current_frames(
    applied: AppliedElectrodePlacement,
    working_structure: MolecularStructure,
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
                            working_structure[
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
    except (AuLatticeExtensionError, IndexError) as error:
        raise ElectrodeLatticeInteractionError(str(error)) from error


def _validate_current_extensions(
    applied: AppliedElectrodePlacement,
    working_structure: MolecularStructure,
    frames: Mapping[str, AuLatticeFrame],
) -> None:
    for extension in applied.lattice_extensions:
        expected = frames[extension.side].coordinate(extension.lattice_key)
        actual = _coordinates(working_structure[extension.global_atom_index])
        if dist(actual, expected) > AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM:
            raise ElectrodeLatticeInteractionError(
                f"{extension.side} extension geometry is not a rigid lattice mapping"
            )


def _validate_working_identity(
    applied: AppliedElectrodePlacement,
    working_structure: MolecularStructure,
) -> None:
    if not isinstance(applied, AppliedElectrodePlacement):
        raise TypeError("interaction requires an applied electrode placement")
    if not isinstance(working_structure, MolecularStructure):
        raise TypeError("interaction requires a working molecular structure")
    canonical_identity = tuple(
        (atom.index, atom.element) for atom in applied.structure
    )
    working_identity = tuple(
        (atom.index, atom.element) for atom in working_structure
    )
    if working_identity != canonical_identity:
        raise ElectrodeLatticeInteractionError(
            "working geometry atom identity conflicts with applied electrodes"
        )


def _closest_atom_index(
    coordinates: Vector3,
    structure: MolecularStructure,
    *,
    element: str,
) -> int | None:
    matches = tuple(atom for atom in structure if atom.element == element)
    if not matches:
        return None
    return min(
        matches,
        key=lambda atom: (dist(coordinates, _coordinates(atom)), atom.index),
    ).index


def _finite_vector(value: Vector3, name: str) -> Vector3:
    try:
        candidate = Atom(0, "Au", *tuple(value))
    except (TypeError, ValueError) as error:
        raise type(error)(f"{name} must contain three finite numbers") from error
    return candidate.x, candidate.y, candidate.z


def _coordinates(atom: Atom) -> Vector3:
    return atom.x, atom.y, atom.z


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return tuple(first[index] - second[index] for index in range(3))


def _cross(first: Vector3, second: Vector3) -> Vector3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _squared_length(vector: Vector3) -> float:
    return sum(component * component for component in vector)
