"""Deterministic, non-mutating virtual-Au placement orchestration."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import acos, cos, dist, degrees, hypot, isclose, isfinite, radians, sqrt

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Connectivity
from moltage.domain.junction import AuPlacementProposal
from moltage.domain.structure import MolecularStructure
from moltage.junction.steric_orientation import (
    OrientationCandidate,
    StericOrientationError,
    cone_orientation_candidates,
    discrete_orientation_candidates,
    select_orientation_pair,
    select_single_orientation,
    with_nh2_local_h_scores,
)
from moltage.structure.anchor_detector import (
    PairedDicyanoGroup,
    paired_dicyano_group_for_anchor,
)


Vector3 = tuple[float, float, float]
_GEOMETRY_TOLERANCE = 1.0e-10


class AuPlacementError(ValueError):
    """Raised when a requested virtual-Au proposal cannot be constructed."""


@dataclass(frozen=True, slots=True)
class AuPlacementParameters:
    """Geometry parameters for one selected physical anchor site."""

    anchor: AnchorCandidate
    distance: float
    angle_degrees: float | None = None


@dataclass(frozen=True, slots=True)
class _PlacementConstraint:
    parameters: AuPlacementParameters
    orientations: tuple[OrientationCandidate, ...]
    remove_atom_indices: tuple[int, ...]
    dicyano_group: PairedDicyanoGroup | None = None
    dicyano_normal: Vector3 | None = None


def propose_au_placement(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchor: AnchorCandidate,
    distance: float,
    angle_degrees: float | None = None,
    *,
    vdw_radii: Mapping[str, float],
) -> AuPlacementProposal | None:
    """Return one proposal, or ``None`` when the site already contains Au."""

    _validate_common_inputs(structure, connectivity)
    _validate_anchor(anchor, structure)
    if anchor.attached_au_indices:
        return None
    return propose_au_placements(
        structure,
        connectivity,
        (
            AuPlacementParameters(
                anchor=anchor,
                distance=distance,
                angle_degrees=angle_degrees,
            ),
        ),
        vdw_radii=vdw_radii,
    )[0]


def propose_au_placements(
    structure: MolecularStructure,
    connectivity: Connectivity,
    parameters: Sequence[AuPlacementParameters],
    *,
    vdw_radii: Mapping[str, float],
) -> tuple[AuPlacementProposal, ...]:
    """Jointly propose virtual Au for exactly one or two unoccupied sites."""

    _validate_common_inputs(structure, connectivity)
    requested = tuple(parameters)
    if len(requested) not in {1, 2}:
        raise AuPlacementError(
            "Au placement requires exactly one or two selected anchor sites"
        )
    if any(not isinstance(item, AuPlacementParameters) for item in requested):
        raise TypeError("parameters must contain AuPlacementParameters instances")
    anchors = tuple(item.anchor for item in requested)
    if len(set(anchors)) != len(anchors):
        raise AuPlacementError("selected anchor sites must be unique")

    adjacency = _adjacency(connectivity)
    constraints = tuple(
        _build_constraint(
            structure,
            connectivity,
            item,
            adjacency,
            vdw_radii,
        )
        for item in requested
    )
    preferred_orientations = tuple(
        _preferred_orientations(constraint, structure)
        for constraint in constraints
    )
    try:
        if len(constraints) == 1:
            selected = (select_single_orientation(preferred_orientations[0]),)
        elif all(
            constraint.dicyano_group is not None
            for constraint in constraints
        ):
            selected = _select_opposite_dicyano_pair(
                constraints,
                structure,
                vdw_radii,
            )
        else:
            selected = select_orientation_pair(
                preferred_orientations[0],
                preferred_orientations[1],
                vdw_radii,
            )
    except StericOrientationError as error:
        raise AuPlacementError(str(error)) from error

    return tuple(
        AuPlacementProposal(
            anchor=constraint.parameters.anchor,
            x=orientation.point[0],
            y=orientation.point[1],
            z=orientation.point[2],
            remove_atom_indices=constraint.remove_atom_indices,
        )
        for constraint, orientation in zip(constraints, selected, strict=True)
    )


def angle_reference_atom_indices(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchor: AnchorCandidate,
) -> tuple[int, ...]:
    """Return the one or two atoms that define displayed anchor angles."""

    _validate_common_inputs(structure, connectivity)
    _validate_anchor(anchor, structure)
    adjacency = _adjacency(connectivity)
    if anchor.kind is AnchorKind.NCS:
        return (_one_group_neighbor(
            structure, anchor, adjacency, "C", "NCS carbon"
        ),)
    if anchor.kind is AnchorKind.SMe:
        return (_one_group_neighbor(
            structure, anchor, adjacency, "C", "SMe methyl carbon"
        ),)
    if anchor.kind is AnchorKind.NH2:
        return (_one_backbone_neighbor(structure, anchor, adjacency, "NH2"),)
    if anchor.kind is AnchorKind.SH:
        return (_one_backbone_neighbor(structure, anchor, adjacency, "SH"),)
    if anchor.kind is AnchorKind.ALKYNYL_C:
        return (_one_group_neighbor(
            structure, anchor, adjacency, "C", "adjacent alkynyl carbon"
        ),)
    if anchor.kind is AnchorKind.CYANO_N:
        return (_one_group_neighbor(
            structure, anchor, adjacency, "C", "cyano carbon"
        ),)
    if anchor.kind is AnchorKind.DICYANO_C:
        group = paired_dicyano_group_for_anchor(
            structure,
            connectivity,
            anchor,
        )
        if group is None:
            raise AuPlacementError(
                "Dicyano-C placement requires exactly two activated "
                "R-C(CN)2 groups"
            )
        return (group.reference_atom_index,)
    if anchor.kind is AnchorKind.PYRIDINE_N:
        neighbors = tuple(
            atom_index
            for atom_index in adjacency[anchor.binding_atom_index]
            if atom_index in anchor.atom_indices
            and structure[atom_index].element == "C"
        )
        if len(neighbors) != 2:
            raise AuPlacementError(
                "Pyridine-N placement requires exactly two adjacent ring carbons"
            )
        return tuple(sorted(neighbors))
    raise AuPlacementError(f"unsupported anchor kind: {anchor.kind.value}")


def angle_reference_atom_index(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchor: AnchorCandidate,
) -> int | None:
    """Return the first angle reference atom (legacy single-reference API)."""

    references = angle_reference_atom_indices(structure, connectivity, anchor)
    return references[0] if references else None


def _build_constraint(
    structure: MolecularStructure,
    connectivity: Connectivity,
    parameters: AuPlacementParameters,
    adjacency: tuple[tuple[int, ...], ...],
    vdw_radii: Mapping[str, float],
) -> _PlacementConstraint:
    anchor = parameters.anchor
    _validate_anchor(anchor, structure)
    if anchor.attached_au_indices:
        raise AuPlacementError(
            f"{anchor.kind.value} site at atom {anchor.binding_atom_index} "
            "already contains attached Au"
        )
    distance = _positive_finite(parameters.distance, "Au distance")
    angle = _validated_parameter_angle(parameters.angle_degrees, anchor.kind)
    binding = _coordinates(structure, anchor.binding_atom_index)
    required_angle = _required_angle(angle, anchor.kind)
    remove_atom_indices: tuple[int, ...] = ()
    dicyano_group = paired_dicyano_group_for_anchor(
        structure,
        connectivity,
        anchor,
    )
    if anchor.kind is AnchorKind.DICYANO_C and dicyano_group is None:
        raise AuPlacementError(
            "Dicyano-C placement requires exactly two activated R-C(CN)2 groups"
        )
    dicyano_normal = (
        _dicyano_group_normal(structure, dicyano_group)
        if dicyano_group is not None
        else None
    )

    try:
        if anchor.kind is AnchorKind.PYRIDINE_N:
            first_index, second_index = angle_reference_atom_indices(
                structure, connectivity, anchor
            )
            directions = _pyridine_mirror_directions(
                _subtract(_coordinates(structure, first_index), binding),
                _subtract(_coordinates(structure, second_index), binding),
                required_angle,
            )
            orientations = discrete_orientation_candidates(
                structure,
                connectivity,
                anchor.binding_atom_index,
                binding,
                directions,
                distance,
                vdw_radii,
            )
        else:
            reference_index = angle_reference_atom_indices(
                structure, connectivity, anchor
            )[0]
            if anchor.kind is AnchorKind.SH:
                remove_atom_indices = (_one_group_neighbor(
                    structure, anchor, adjacency, "H", "thiol hydrogen"
                ),)
            elif anchor.kind is AnchorKind.ALKYNYL_C:
                hydrogen_indices = _group_neighbors(
                    structure, anchor, adjacency, "H"
                )
                if len(hydrogen_indices) != 1:
                    raise AuPlacementError(
                        "Alkynyl-C placement requires exactly one terminal hydrogen; "
                        f"found {len(hydrogen_indices)}"
                    )
                remove_atom_indices = hydrogen_indices

            orientations = cone_orientation_candidates(
                structure,
                connectivity,
                anchor.binding_atom_index,
                binding,
                _subtract(_coordinates(structure, reference_index), binding),
                distance,
                required_angle,
                vdw_radii,
                excluded_atom_indices=remove_atom_indices,
            )
            if anchor.kind is AnchorKind.NH2:
                hydrogen_indices = _group_neighbors(
                    structure, anchor, adjacency, "H"
                )
                orientations = with_nh2_local_h_scores(
                    orientations,
                    structure,
                    hydrogen_indices,
                )
    except StericOrientationError as error:
        raise AuPlacementError(str(error)) from error

    return _PlacementConstraint(
        parameters,
        orientations,
        remove_atom_indices,
        dicyano_group,
        dicyano_normal,
    )


def _preferred_orientations(
    constraint: _PlacementConstraint,
    structure: MolecularStructure,
) -> tuple[OrientationCandidate, ...]:
    group = constraint.dicyano_group
    normal = constraint.dicyano_normal
    if group is None or normal is None:
        return constraint.orientations

    if constraint.parameters.anchor.kind is AnchorKind.DICYANO_C:
        scored = tuple(
            (
                _dicyano_center_balance(structure, group, candidate),
                candidate,
            )
            for candidate in constraint.orientations
        )
        best = min(score for score, _ in scored)
        return tuple(
            candidate
            for score, candidate in scored
            if all(
                isclose(value, best_value, abs_tol=1.0e-9)
                for value, best_value in zip(score, best, strict=True)
            )
        )

    if constraint.parameters.anchor.kind is AnchorKind.CYANO_N:
        scored = tuple(
            (abs(_dot(candidate.direction, normal)), candidate)
            for candidate in constraint.orientations
        )
        best = max(score for score, _ in scored)
        return tuple(
            candidate
            for score, candidate in scored
            if isclose(score, best, abs_tol=1.0e-9)
        )
    return constraint.orientations


def _select_opposite_dicyano_pair(
    constraints: Sequence[_PlacementConstraint],
    structure: MolecularStructure,
    vdw_radii: Mapping[str, float],
) -> tuple[OrientationCandidate, OrientationCandidate]:
    first_normal = constraints[0].dicyano_normal
    second_normal = constraints[1].dicyano_normal
    if first_normal is None or second_normal is None:
        raise StericOrientationError(
            "paired dicyano placement requires two local plane normals"
        )
    if _dot(first_normal, second_normal) < 0.0:
        second_normal = _scale(second_normal, -1.0)
    first_ranks = _dicyano_local_ranks(
        constraints[0],
        structure,
        first_normal,
    )
    second_ranks = _dicyano_local_ranks(
        constraints[1],
        structure,
        second_normal,
    )
    try:
        au_radius = _positive_finite(vdw_radii["Au"], "Au van der Waals radius")
    except KeyError as error:
        raise StericOrientationError(
            "no approved van der Waals radius for required element 'Au'"
        ) from error

    feasible: list[
        tuple[
            OrientationCandidate,
            OrientationCandidate,
            int,
            int,
            float,
        ]
    ] = []
    for first in constraints[0].orientations:
        first_side = _dot(first.direction, first_normal)
        if abs(first_side) <= _GEOMETRY_TOLERANCE:
            continue
        for second in constraints[1].orientations:
            second_side = _dot(second.direction, second_normal)
            if first_side * second_side >= -_GEOMETRY_TOLERANCE:
                continue
            au_clearance = dist(first.point, second.point) - 2.0 * au_radius
            if au_clearance >= 0.0:
                feasible.append(
                    (
                        first,
                        second,
                        first_ranks[first],
                        second_ranks[second],
                        au_clearance,
                    )
                )
    if not feasible:
        raise StericOrientationError(
            "no sterically valid opposite-side virtual-Au pair exists for "
            "the activated dicyano groups"
        )
    selected = max(
        feasible,
        key=lambda item: (
            -max(item[2], item[3]),
            -(item[2] + item[3]),
            round(item[4], 12),
            round(min(item[0].clearance, item[1].clearance), 12),
            round(-_dot(item[0].direction, item[1].direction), 12),
            -item[0].candidate_order,
            -item[1].candidate_order,
        ),
    )
    return selected[0], selected[1]


def _dicyano_local_ranks(
    constraint: _PlacementConstraint,
    structure: MolecularStructure,
    normal: Vector3,
) -> dict[OrientationCandidate, int]:
    ordered = sorted(
        constraint.orientations,
        key=lambda candidate: _dicyano_local_score(
            constraint,
            structure,
            normal,
            candidate,
        ),
        reverse=True,
    )
    return {candidate: rank for rank, candidate in enumerate(ordered)}


def _dicyano_local_score(
    constraint: _PlacementConstraint,
    structure: MolecularStructure,
    normal: Vector3,
    candidate: OrientationCandidate,
) -> tuple[float, ...]:
    group = constraint.dicyano_group
    if group is None:
        raise StericOrientationError(
            "dicyano local ranking requires an activated group"
        )
    if constraint.parameters.anchor.kind is AnchorKind.DICYANO_C:
        angle_error, distance_error = _dicyano_center_balance(
            structure,
            group,
            candidate,
        )
        return (
            -round(angle_error, 12),
            -round(distance_error, 12),
            round(abs(_dot(candidate.direction, normal)), 12),
            round(candidate.clearance, 12),
            float(-candidate.candidate_order),
        )
    return (
        round(abs(_dot(candidate.direction, normal)), 12),
        round(candidate.clearance, 12),
        float(-candidate.candidate_order),
    )


def _dicyano_center_balance(
    structure: MolecularStructure,
    group: PairedDicyanoGroup,
    candidate: OrientationCandidate,
) -> tuple[float, float]:
    center = _coordinates(structure, group.center_carbon_index)
    nitrogen_points = tuple(
        _coordinates(structure, atom_index)
        for atom_index in group.nitrogen_indices
    )
    nitrogen_rays = tuple(
        _subtract(point, center)
        for point in nitrogen_points
    )
    angle_error = abs(
        _angle_between(nitrogen_rays[0], candidate.direction)
        - _angle_between(nitrogen_rays[1], candidate.direction)
    )
    distance_error = abs(
        dist(candidate.point, nitrogen_points[0])
        - dist(candidate.point, nitrogen_points[1])
    )
    return angle_error, distance_error


def _dicyano_group_normal(
    structure: MolecularStructure,
    group: PairedDicyanoGroup,
) -> Vector3:
    center = _coordinates(structure, group.center_carbon_index)
    first, second = tuple(
        _subtract(_coordinates(structure, atom_index), center)
        for atom_index in group.cyano_carbon_indices
    )
    return _normalize(
        _cross(first, second),
        "dicyano CN-arm plane normal",
    )


def _angle_between(first: Vector3, second: Vector3) -> float:
    first_unit = _normalize(first, "dicyano angle ray")
    second_unit = _normalize(second, "dicyano Au direction")
    return degrees(acos(max(-1.0, min(1.0, _dot(first_unit, second_unit)))))


def _pyridine_mirror_directions(
    first_reference_ray: Vector3,
    second_reference_ray: Vector3,
    angle_degrees: float,
) -> tuple[Vector3, Vector3]:
    """Construct the only two directions at equal angles to two ring rays."""

    angle = _validated_angle(angle_degrees)
    first = _normalize(first_reference_ray, "first pyridine ring ray")
    second = _normalize(second_reference_ray, "second pyridine ring ray")
    bisector = _normalize(_add(first, second), "pyridine ring-ray bisector")
    normal = _normalize(_cross(first, second), "pyridine ring plane normal")
    projection = _dot(bisector, first)
    if abs(projection) <= _GEOMETRY_TOLERANCE:
        raise AuPlacementError(
            "Pyridine-N ring geometry has no usable equal-angle bisector"
        )
    alpha = cos(radians(angle)) / projection
    if abs(alpha) > 1.0 + _GEOMETRY_TOLERANCE:
        raise AuPlacementError(
            "Pyridine-N equal-angle geometry is impossible for the requested "
            f"angle ({angle:.6g} degrees)"
        )
    alpha = max(-1.0, min(1.0, alpha))
    beta = sqrt(max(0.0, 1.0 - alpha * alpha))
    directions = (
        _add(_scale(bisector, alpha), _scale(normal, beta)),
        _add(_scale(bisector, alpha), _scale(normal, -beta)),
    )
    target = cos(radians(angle))
    for direction in directions:
        if not (
            isclose(_dot(direction, first), target, abs_tol=_GEOMETRY_TOLERANCE)
            and isclose(
                _dot(direction, second),
                target,
                abs_tol=_GEOMETRY_TOLERANCE,
            )
        ):
            raise AuPlacementError(
                "Pyridine-N equal-angle construction failed numerical validation"
            )
    return directions


def _validated_parameter_angle(
    value: float | None,
    kind: AnchorKind,
) -> float | None:
    if value is None:
        raise AuPlacementError(f"{kind.value} placement requires an angle")
    return _validated_angle(value)


def _validated_angle(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Au angle must be numeric")
    numeric_value = float(value)
    if (
        not isfinite(numeric_value)
        or numeric_value <= 0.0
        or numeric_value > 180.0
    ):
        raise AuPlacementError(
            "Au angle must be finite, greater than zero, and at most 180"
        )
    return numeric_value


def _required_angle(value: float | None, kind: AnchorKind) -> float:
    if value is None:
        raise AuPlacementError(
            f"internal placement constraint for {kind.value} has no angle"
        )
    return value


def _group_neighbors(
    structure: MolecularStructure,
    anchor: AnchorCandidate,
    adjacency: tuple[tuple[int, ...], ...],
    element: str,
) -> tuple[int, ...]:
    return tuple(
        atom_index
        for atom_index in adjacency[anchor.binding_atom_index]
        if atom_index in anchor.atom_indices
        and structure[atom_index].element == element
    )


def _one_group_neighbor(
    structure: MolecularStructure,
    anchor: AnchorCandidate,
    adjacency: tuple[tuple[int, ...], ...],
    element: str,
    description: str,
) -> int:
    neighbors = _group_neighbors(structure, anchor, adjacency, element)
    if len(neighbors) != 1:
        raise AuPlacementError(
            f"{anchor.kind.value} placement requires exactly one adjacent "
            f"{description}; found {len(neighbors)}"
        )
    return neighbors[0]


def _one_backbone_neighbor(
    structure: MolecularStructure,
    anchor: AnchorCandidate,
    adjacency: tuple[tuple[int, ...], ...],
    description: str,
) -> int:
    neighbors = tuple(
        atom_index
        for atom_index in adjacency[anchor.binding_atom_index]
        if atom_index not in anchor.atom_indices
        and structure[atom_index].element not in {"H", "Au"}
    )
    if len(neighbors) != 1:
        raise AuPlacementError(
            f"{description} placement requires exactly one non-hydrogen "
            f"backbone neighbor; found {len(neighbors)}"
        )
    return neighbors[0]


def _validate_common_inputs(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("connectivity must be a Connectivity")
    if connectivity.atom_count != len(structure):
        raise AuPlacementError(
            "connectivity atom count does not match the molecular structure"
        )


def _validate_anchor(
    anchor: AnchorCandidate,
    structure: MolecularStructure,
) -> None:
    if not isinstance(anchor, AnchorCandidate):
        raise TypeError("anchor must be an AnchorCandidate")
    indexes = (
        anchor.atom_indices
        + anchor.attached_au_indices
        + (anchor.binding_atom_index,)
    )
    if any(atom_index >= len(structure) for atom_index in indexes):
        raise AuPlacementError(
            "anchor contains an atom index outside the molecular structure"
        )
    binding_element = structure[anchor.binding_atom_index].element
    required_element = {
        AnchorKind.NCS: "S",
        AnchorKind.NH2: "N",
        AnchorKind.PYRIDINE_N: "N",
        AnchorKind.SMe: "S",
        AnchorKind.SH: "S",
        AnchorKind.ALKYNYL_C: "C",
        AnchorKind.CYANO_N: "N",
        AnchorKind.DICYANO_C: "C",
    }[anchor.kind]
    if binding_element != required_element:
        raise AuPlacementError(
            f"{anchor.kind.value} binding atom must be {required_element}, "
            f"not {binding_element}"
        )


def _adjacency(connectivity: Connectivity) -> tuple[tuple[int, ...], ...]:
    neighbors: list[list[int]] = [[] for _ in range(connectivity.atom_count)]
    for bond in connectivity:
        neighbors[bond.first_index].append(bond.second_index)
        neighbors[bond.second_index].append(bond.first_index)
    return tuple(tuple(sorted(entries)) for entries in neighbors)


def _positive_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric_value = float(value)
    if not isfinite(numeric_value) or numeric_value <= 0.0:
        raise AuPlacementError(f"{name} must be finite and greater than zero")
    return numeric_value


def _coordinates(structure: MolecularStructure, atom_index: int) -> Vector3:
    atom = structure[atom_index]
    return atom.x, atom.y, atom.z


def _normalize(vector: Vector3, description: str) -> Vector3:
    length = hypot(*vector)
    if not isfinite(length) or length <= _GEOMETRY_TOLERANCE:
        raise AuPlacementError(f"{description} must have non-zero finite length")
    return _scale(vector, 1.0 / length)


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
