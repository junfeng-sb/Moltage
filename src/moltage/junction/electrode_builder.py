"""Deterministic placement of canonical Moltage Au pyramids."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import cos, dist, hypot, inf, isfinite, radians, sin, sqrt

import numpy as np

from moltage.domain.anchor import AnchorCandidate
from moltage.domain.au_pyramid import (
    DEFAULT_AU_PYRAMID_LAYERS,
    MoltageAuPyramid,
    generate_au_pyramid,
    validate_pyramid_layers,
)
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.electrode import (
    AppliedElectrodePlacement,
    ElectrodeClusterProposal,
    ElectrodeContactSite,
    ElectrodePlacementProposal,
    Vector3,
)
from moltage.domain.structure import Atom, MolecularStructure


COARSE_ROLL_STEP_DEGREES = 10
REFINE_ROLL_RADIUS_DEGREES = 10
NUMERICAL_TOLERANCE = 1.0e-12


class ElectrodeBuilderError(ValueError):
    """Raised when eligible contacts cannot form an exact electrode preview."""


@dataclass(frozen=True, slots=True)
class JointRollSelection:
    """The deterministic coarse-to-fine two-cluster roll result."""

    first_degrees: int
    second_degrees: int
    minimum_distance: float


def eligible_electrode_contact_sites(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchors: Sequence[AnchorCandidate],
) -> tuple[ElectrodeContactSite, ...]:
    """Return recognized anchors having exactly one connectivity-recorded Au."""

    _validate_source(structure, connectivity)
    bond_pairs = {
        (bond.first_index, bond.second_index) for bond in connectivity
    }
    sites: list[ElectrodeContactSite] = []
    for anchor in tuple(anchors):
        if not isinstance(anchor, AnchorCandidate):
            raise TypeError("anchors must contain AnchorCandidate instances")
        indexes = anchor.atom_indices + anchor.attached_au_indices
        if any(atom_index >= len(structure) for atom_index in indexes):
            raise ElectrodeBuilderError(
                "detected anchor contains an atom index outside the source structure"
            )
        if len(anchor.attached_au_indices) != 1:
            continue
        contact_index = anchor.attached_au_indices[0]
        if structure[contact_index].element != "Au":
            raise ElectrodeBuilderError(
                "detected attached contact index does not identify an Au atom"
            )
        binding_index = anchor.binding_atom_index
        pair = tuple(sorted((binding_index, contact_index)))
        if pair not in bond_pairs:
            raise ElectrodeBuilderError(
                "detected attached contact Au is absent from supplied connectivity"
            )
        sites.append(ElectrodeContactSite(anchor, contact_index))

    ordered = tuple(sorted(sites, key=lambda site: site.sort_key))
    if len({site.sort_key for site in ordered}) != len(ordered):
        raise ElectrodeBuilderError(
            "detected occupied anchor/contact sites are not uniquely identified"
        )
    if len({site.contact_au_index for site in ordered}) != len(ordered):
        raise ElectrodeBuilderError(
            "one contact Au cannot serve multiple electrode anchor sites"
        )
    return ordered


def propose_electrode_placement(
    structure: MolecularStructure,
    connectivity: Connectivity,
    eligible_sites: Sequence[ElectrodeContactSite],
    selected_sites: Sequence[ElectrodeContactSite],
    *,
    pyramid_layers: int = DEFAULT_AU_PYRAMID_LAYERS,
) -> ElectrodePlacementProposal:
    """Build an exact one- or two-cluster preview in deterministic site order."""

    _validate_source(structure, connectivity)
    all_sites = _validated_sites(eligible_sites, "eligible")
    if len(all_sites) != 2:
        raise ElectrodeBuilderError(
            "Au pyramids require exactly two eligible occupied "
            f"anchor/contact sites; found {len(all_sites)}"
        )
    selected = _validated_sites(selected_sites, "selected")
    if len(selected) not in {1, 2}:
        raise ElectrodeBuilderError(
            "electrode preview requires one or both eligible contact sites"
        )
    all_by_key = {site.sort_key: site for site in all_sites}
    if any(site.sort_key not in all_by_key for site in selected):
        raise ElectrodeBuilderError(
            "selected electrode sites must belong to the eligible two-site set"
        )
    selected = tuple(all_by_key[site.sort_key] for site in selected)
    selected = tuple(sorted(selected, key=lambda site: site.sort_key))

    layers = validate_pyramid_layers(pyramid_layers)
    pyramid = generate_au_pyramid(layers)
    side_by_site = {
        all_sites[0].sort_key: "LEFT",
        all_sites[1].sort_key: "RIGHT",
    }
    aligned: list[
        tuple[ElectrodeContactSite, str, Vector3, tuple[Vector3, ...]]
    ] = []
    for site in selected:
        side = side_by_site[site.sort_key]
        target_axis = target_axis_for_site(structure, site)
        coordinates = align_electrode_pyramid(
            pyramid,
            _atom_coordinates(structure, site.anchor.binding_atom_index),
            _atom_coordinates(structure, site.contact_au_index),
            roll_degrees=0,
        )
        aligned.append((site, side, target_axis, coordinates))

    minimum_distance: float | None = None
    if len(aligned) == 2:
        roll_selection = optimize_joint_roll(
            aligned[0][3],
            aligned[0][2],
            aligned[1][3],
            aligned[1][2],
        )
        roll_angles = (
            roll_selection.first_degrees,
            roll_selection.second_degrees,
        )
        minimum_distance = roll_selection.minimum_distance
    else:
        roll_angles = (0,)

    transformed_sets = tuple(
        roll_electrode_coordinates(base, axis, roll)
        for (_, _, axis, base), roll in zip(
            aligned,
            roll_angles,
            strict=True,
        )
    )
    next_index = len(structure)
    new_atoms: list[Atom] = []
    clusters: list[ElectrodeClusterProposal] = []
    for (site, side, _, _), roll, transformed in zip(
        aligned,
        roll_angles,
        transformed_sets,
        strict=True,
    ):
        mapping = (
            site.contact_au_index,
            *range(next_index, next_index + len(pyramid.structure) - 1),
        )
        for local_index, coordinates in enumerate(transformed[1:], start=1):
            global_index = mapping[local_index]
            new_atoms.append(Atom(global_index, "Au", *coordinates))
        mapped_bonds = tuple(
            Bond(
                mapping[bond.first_index],
                mapping[bond.second_index],
                dist(
                    transformed[bond.first_index],
                    transformed[bond.second_index],
                ),
            )
            for bond in pyramid.connectivity
        )
        clusters.append(
            ElectrodeClusterProposal(
                site,
                side,
                pyramid,
                roll,
                transformed,
                mapping,
                mapped_bonds,
            )
        )
        next_index += len(pyramid.structure) - 1

    preview_structure = MolecularStructure(
        structure.atoms + tuple(new_atoms),
        comment=structure.comment,
    )
    preview_connectivity = Connectivity(
        len(preview_structure),
        connectivity.bonds
        + tuple(
            bond
            for cluster in clusters
            for bond in cluster.mapped_bonds
        ),
    )
    return ElectrodePlacementProposal(
        structure,
        connectivity,
        tuple(clusters),
        preview_structure,
        preview_connectivity,
        minimum_distance,
    )


def apply_electrode_placement(
    structure: MolecularStructure,
    connectivity: Connectivity,
    proposal: ElectrodePlacementProposal,
) -> AppliedElectrodePlacement:
    """Promote the exact accepted preview without any geometry recalculation."""

    _validate_source(structure, connectivity)
    if not isinstance(proposal, ElectrodePlacementProposal):
        raise TypeError("electrode application requires an ElectrodePlacementProposal")
    if structure != proposal.source_structure:
        raise ElectrodeBuilderError(
            "working structure changed after the electrode preview was generated"
        )
    if connectivity != proposal.source_connectivity:
        raise ElectrodeBuilderError(
            "working connectivity changed after the electrode preview was generated"
        )
    return AppliedElectrodePlacement(
        proposal.preview_structure,
        proposal.preview_connectivity,
        proposal,
        proposal.added_au_indices,
    )


def target_axis_for_site(
    structure: MolecularStructure,
    site: ElectrodeContactSite,
) -> Vector3:
    """Return normalize(contact Au - binding atom), pointing away from molecule."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not isinstance(site, ElectrodeContactSite):
        raise TypeError("site must be an ElectrodeContactSite")
    if max(site.sort_key) >= len(structure):
        raise ElectrodeBuilderError(
            "electrode contact site is outside the molecular structure"
        )
    binding = _atom_coordinates(structure, site.anchor.binding_atom_index)
    apex = _atom_coordinates(structure, site.contact_au_index)
    try:
        return _normalize(_subtract(apex, binding), "target electrode axis")
    except ValueError as error:
        raise ElectrodeBuilderError(str(error)) from error


def align_electrode_pyramid(
    pyramid: MoltageAuPyramid,
    binding_coordinates: Vector3,
    apex_coordinates: Vector3,
    *,
    roll_degrees: int | float = 0,
) -> tuple[Vector3, ...]:
    """Map canonical apex/axis to an exact contact position and outward axis."""

    if not isinstance(pyramid, MoltageAuPyramid):
        raise TypeError("pyramid must be a MoltageAuPyramid")
    binding = _finite_vector(binding_coordinates, "binding atom coordinates")
    apex = _finite_vector(apex_coordinates, "contact Au coordinates")
    target_axis = _normalize(_subtract(apex, binding), "target electrode axis")
    canonical_apex = _atom_coordinates(pyramid.structure, pyramid.apex_local_index)
    aligned: list[Vector3] = []
    for local_index, atom in enumerate(pyramid.structure):
        if local_index == pyramid.apex_local_index:
            aligned.append(apex)
            continue
        relative = _subtract((atom.x, atom.y, atom.z), canonical_apex)
        aligned.append(
            _add(
                apex,
                _rotate_from_to(
                    relative,
                    pyramid.principal_axis,
                    target_axis,
                ),
            )
        )
    return roll_electrode_coordinates(tuple(aligned), target_axis, roll_degrees)


def roll_electrode_coordinates(
    aligned_coordinates: Sequence[Vector3],
    target_axis: Vector3,
    roll_degrees: int | float,
) -> tuple[Vector3, ...]:
    """Rotate a base-aligned canonical pyramid about its fixed apex and axis."""

    coordinates = _validated_cluster_coordinates(aligned_coordinates)
    axis = _normalize(target_axis, "target electrode axis")
    angle = _finite_number(roll_degrees, "roll angle") % 360.0
    apex = coordinates[0]
    if angle == 0.0:
        return coordinates
    angle_radians = radians(angle)
    rotated = [apex]
    for point in coordinates[1:]:
        rotated.append(
            _add(
                apex,
                _rotate_about_axis(
                    _subtract(point, apex),
                    axis,
                    cos(angle_radians),
                    sin(angle_radians),
                ),
            )
        )
    return tuple(rotated)


def optimize_joint_roll(
    first_aligned_coordinates: Sequence[Vector3],
    first_target_axis: Vector3,
    second_aligned_coordinates: Sequence[Vector3],
    second_target_axis: Vector3,
) -> JointRollSelection:
    """Maximize opposite-cluster minimum distance via fixed coarse/refine grids."""

    first = _validated_cluster_coordinates(first_aligned_coordinates)
    second = _validated_cluster_coordinates(second_aligned_coordinates)
    first_axis = _normalize(first_target_axis, "first target electrode axis")
    second_axis = _normalize(second_target_axis, "second target electrode axis")
    coarse_angles = tuple(range(0, 360, COARSE_ROLL_STEP_DEGREES))
    coarse_first = {
        angle: roll_electrode_coordinates(first, first_axis, angle)
        for angle in coarse_angles
    }
    coarse_second = {
        angle: roll_electrode_coordinates(second, second_axis, angle)
        for angle in coarse_angles
    }
    coarse_pair, coarse_score = _best_roll_pair(
        coarse_first,
        coarse_second,
    )

    first_refine_angles = tuple(
        sorted(
            {
                (coarse_pair[0] + offset) % 360
                for offset in range(
                    -REFINE_ROLL_RADIUS_DEGREES,
                    REFINE_ROLL_RADIUS_DEGREES + 1,
                )
            }
        )
    )
    second_refine_angles = tuple(
        sorted(
            {
                (coarse_pair[1] + offset) % 360
                for offset in range(
                    -REFINE_ROLL_RADIUS_DEGREES,
                    REFINE_ROLL_RADIUS_DEGREES + 1,
                )
            }
        )
    )
    refined_first = {
        angle: roll_electrode_coordinates(first, first_axis, angle)
        for angle in first_refine_angles
    }
    refined_second = {
        angle: roll_electrode_coordinates(second, second_axis, angle)
        for angle in second_refine_angles
    }
    refined_pair, refined_score = _best_roll_pair(
        refined_first,
        refined_second,
        initial_pair=coarse_pair,
        initial_score=coarse_score,
    )
    return JointRollSelection(
        refined_pair[0],
        refined_pair[1],
        sqrt(max(0.0, refined_score)),
    )


def minimum_cross_cluster_distance_squared(
    first_coordinates: Sequence[Vector3],
    second_coordinates: Sequence[Vector3],
) -> float:
    """Return the cross-cluster minimum squared distance, excluding apex/apex."""

    return _minimum_cross_cluster_distance_squared(
        _validated_cluster_coordinates(first_coordinates),
        _validated_cluster_coordinates(second_coordinates),
    )


def _best_roll_pair(
    first_coordinates_by_angle: Mapping[int, tuple[Vector3, ...]],
    second_coordinates_by_angle: Mapping[int, tuple[Vector3, ...]],
    *,
    initial_pair: tuple[int, int] | None = None,
    initial_score: float = -inf,
) -> tuple[tuple[int, int], float]:
    best_pair = initial_pair
    best_score = initial_score
    for first_angle in sorted(first_coordinates_by_angle):
        first = first_coordinates_by_angle[first_angle]
        for second_angle in sorted(second_coordinates_by_angle):
            pair = first_angle, second_angle
            score = _minimum_cross_cluster_distance_squared(
                first,
                second_coordinates_by_angle[second_angle],
                best_score=(best_score if best_pair is not None else None),
            )
            if (
                best_pair is None
                or score > best_score + NUMERICAL_TOLERANCE
                or (
                    abs(score - best_score) <= NUMERICAL_TOLERANCE
                    and pair < best_pair
                )
            ):
                best_pair = pair
                best_score = score
    if best_pair is None or not isfinite(best_score):
        raise ElectrodeBuilderError("joint electrode roll search found no candidate")
    return best_pair, best_score


def _minimum_cross_cluster_distance_squared(
    first: tuple[Vector3, ...],
    second: tuple[Vector3, ...],
    *,
    best_score: float | None = None,
) -> float:
    first_array = np.asarray(first, dtype=float)
    second_array = np.asarray(second, dtype=float)
    differences = first_array[:, np.newaxis, :] - second_array[np.newaxis, :, :]
    squared = np.einsum("ijk,ijk->ij", differences, differences)
    squared[0, 0] = np.inf
    minimum = float(np.min(squared))
    if best_score is not None and minimum < best_score - NUMERICAL_TOLERANCE:
        return minimum
    return minimum


def _validated_sites(
    sites: Sequence[ElectrodeContactSite],
    description: str,
) -> tuple[ElectrodeContactSite, ...]:
    checked = tuple(sites)
    if any(not isinstance(site, ElectrodeContactSite) for site in checked):
        raise TypeError(
            f"{description} sites must contain ElectrodeContactSite instances"
        )
    if len({site.sort_key for site in checked}) != len(checked):
        raise ElectrodeBuilderError(f"{description} electrode sites must be unique")
    return tuple(sorted(checked, key=lambda site: site.sort_key))


def _validate_source(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("connectivity must be a Connectivity")
    if connectivity.atom_count != len(structure):
        raise ElectrodeBuilderError(
            "connectivity atom count does not match the source structure"
        )


def _validated_cluster_coordinates(
    coordinates: Sequence[Vector3],
) -> tuple[Vector3, ...]:
    checked = tuple(
        _finite_vector(point, "electrode cluster coordinate")
        for point in coordinates
    )
    if len(checked) < 2:
        raise ElectrodeBuilderError(
            "electrode cluster coordinate set must contain at least two atoms"
        )
    return checked


def _rotate_from_to(
    vector: Vector3,
    source_axis: Vector3,
    target_axis: Vector3,
) -> Vector3:
    source = _normalize(source_axis, "source rotation axis")
    target = _normalize(target_axis, "target rotation axis")
    cosine = max(-1.0, min(1.0, _dot(source, target)))
    cross = _cross(source, target)
    sine = hypot(*cross)
    if sine <= NUMERICAL_TOLERANCE:
        if cosine >= 0.0:
            return vector
        helper = min(
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            key=lambda axis: (abs(_dot(source, axis)), axis),
        )
        rotation_axis = _normalize(
            _cross(source, helper),
            "anti-parallel rotation axis",
        )
        return _rotate_about_axis(vector, rotation_axis, -1.0, 0.0)
    rotation_axis = _scale(cross, 1.0 / sine)
    return _rotate_about_axis(vector, rotation_axis, cosine, sine)


def _rotate_about_axis(
    vector: Vector3,
    axis: Vector3,
    cosine: float,
    sine: float,
) -> Vector3:
    return _add(
        _add(
            _scale(vector, cosine),
            _scale(_cross(axis, vector), sine),
        ),
        _scale(axis, _dot(axis, vector) * (1.0 - cosine)),
    )


def _atom_coordinates(
    structure: MolecularStructure,
    atom_index: int,
) -> Vector3:
    atom = structure[atom_index]
    return atom.x, atom.y, atom.z


def _finite_vector(value: Sequence[float], name: str) -> Vector3:
    components = tuple(value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three components")
    numeric = tuple(_finite_number(component, name) for component in components)
    return numeric[0], numeric[1], numeric[2]


def _normalize(vector: Sequence[float], name: str) -> Vector3:
    numeric = _finite_vector(vector, name)
    length = hypot(*numeric)
    if length <= NUMERICAL_TOLERANCE:
        raise ValueError(f"{name} must have non-zero finite length")
    return _scale(numeric, 1.0 / length)


def _finite_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _add(first: Vector3, second: Vector3) -> Vector3:
    return tuple(
        first_component + second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return tuple(
        first_component - second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _scale(vector: Vector3, factor: float) -> Vector3:
    return tuple(component * factor for component in vector)


def _dot(first: Vector3, second: Vector3) -> float:
    return sum(
        first_component * second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _cross(first: Vector3, second: Vector3) -> Vector3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
