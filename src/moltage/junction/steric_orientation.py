"""Deterministic hard-steric screening and finite-candidate selection."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from math import cos, dist, hypot, inf, isfinite, radians, sin

from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure


AZIMUTH_STEP_DEGREES = 1
_NUMERICAL_TOLERANCE = 1.0e-12

Vector3 = tuple[float, float, float]


class StericOrientationError(ValueError):
    """Raised when no hard-sterically-valid orientation can be selected."""


@dataclass(frozen=True, slots=True)
class StericClearance:
    """Minimum Au-to-existing-atom vdW clearance for one point."""

    minimum: float
    closest_atom_index: int | None


@dataclass(frozen=True, slots=True)
class OrientationCandidate:
    """One feasible orientation with optional NH2-local ranking metadata."""

    point: Vector3
    direction: Vector3
    clearance: float
    azimuth_degrees: int | None
    candidate_order: int = 0
    nh2_min_h_distance: float | None = None
    nh2_total_h_distance: float | None = None


def cone_orientation_candidates(
    structure: MolecularStructure,
    connectivity: Connectivity,
    binding_atom_index: int,
    origin: Vector3,
    axis: Vector3,
    distance: float,
    angle_degrees: float,
    vdw_radii: Mapping[str, float],
    *,
    excluded_atom_indices: Iterable[int] = (),
) -> tuple[OrientationCandidate, ...]:
    """Enumerate and hard-screen a complete one-degree cone."""

    unit_axis = _normalize(axis, "cone axis")
    numeric_distance = _positive_finite(distance, "Au distance")
    numeric_angle = _validated_angle(angle_degrees)
    first_perpendicular, second_perpendicular = _orthonormal_pair(unit_axis)
    angle_radians = radians(numeric_angle)

    if numeric_angle == 180.0:
        samples = ((0, 0, _scale(unit_axis, -1.0)),)
    else:
        samples = tuple(
            (
                azimuth_degrees,
                azimuth_degrees,
                _normalize(
                    _add(
                        _scale(unit_axis, cos(angle_radians)),
                        _scale(
                            _add(
                                _scale(
                                    first_perpendicular,
                                    cos(radians(azimuth_degrees)),
                                ),
                                _scale(
                                    second_perpendicular,
                                    sin(radians(azimuth_degrees)),
                                ),
                            ),
                            sin(angle_radians),
                        ),
                    ),
                    "cone direction",
                ),
            )
            for azimuth_degrees in range(0, 360, AZIMUTH_STEP_DEGREES)
        )

    return _screen_samples(
        structure,
        connectivity,
        binding_atom_index,
        origin,
        numeric_distance,
        samples,
        vdw_radii,
        excluded_atom_indices,
    )


def discrete_orientation_candidates(
    structure: MolecularStructure,
    connectivity: Connectivity,
    binding_atom_index: int,
    origin: Vector3,
    directions: Sequence[Vector3],
    distance: float,
    vdw_radii: Mapping[str, float],
    *,
    excluded_atom_indices: Iterable[int] = (),
) -> tuple[OrientationCandidate, ...]:
    """Hard-screen a finite, deterministically ordered direction set."""

    checked_directions = tuple(directions)
    if not checked_directions:
        raise StericOrientationError(
            "discrete orientation directions must not be empty"
        )
    return _screen_samples(
        structure,
        connectivity,
        binding_atom_index,
        origin,
        _positive_finite(distance, "Au distance"),
        tuple(
            (None, order, _normalize(direction, "discrete Au direction"))
            for order, direction in enumerate(checked_directions)
        ),
        vdw_radii,
        excluded_atom_indices,
    )


def fixed_orientation_candidates(
    structure: MolecularStructure,
    connectivity: Connectivity,
    binding_atom_index: int,
    origin: Vector3,
    direction: Vector3,
    distance: float,
    vdw_radii: Mapping[str, float],
    *,
    excluded_atom_indices: Iterable[int] = (),
) -> tuple[OrientationCandidate, ...]:
    """Hard-screen one fixed direction using the same steric rule."""

    return discrete_orientation_candidates(
        structure,
        connectivity,
        binding_atom_index,
        origin,
        (direction,),
        distance,
        vdw_radii,
        excluded_atom_indices=excluded_atom_indices,
    )


def steric_clearance_for_point(
    structure: MolecularStructure,
    connectivity: Connectivity,
    binding_atom_index: int,
    point: Vector3,
    vdw_radii: Mapping[str, float],
    *,
    excluded_atom_indices: Iterable[int] = (),
) -> StericClearance:
    """Return clearance after local and scheduled-removal exclusions."""

    _validate_inputs(structure, connectivity, binding_atom_index, vdw_radii)
    numeric_point = _finite_vector(point, "proposed Au point")
    explicit_exclusions = _validated_excluded_indices(
        excluded_atom_indices,
        len(structure),
        binding_atom_index,
    )
    eligible_indices = _remote_atom_indices(
        connectivity,
        binding_atom_index,
        explicit_exclusions,
    )
    au_radius = _vdw_radius("Au", vdw_radii)
    minimum = inf
    closest_atom_index: int | None = None
    for atom_index in eligible_indices:
        atom = structure[atom_index]
        atom_radius = _vdw_radius(atom.element, vdw_radii)
        clearance = dist(
            numeric_point,
            (atom.x, atom.y, atom.z),
        ) - (au_radius + atom_radius)
        if abs(clearance) <= _NUMERICAL_TOLERANCE:
            clearance = 0.0
        if clearance < minimum:
            minimum = clearance
            closest_atom_index = atom_index
    return StericClearance(minimum, closest_atom_index)


def non_au_steric_clearance_for_point(
    structure: MolecularStructure,
    point: Vector3,
    vdw_radii: Mapping[str, float],
) -> StericClearance:
    """Return Au-point clearance against every non-Au structure atom."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not isinstance(vdw_radii, Mapping):
        raise TypeError("vdw_radii must be a mapping")
    numeric_point = _finite_vector(point, "proposed Au point")
    au_radius = _vdw_radius("Au", vdw_radii)
    minimum = inf
    closest_atom_index: int | None = None
    for atom in structure:
        if atom.element == "Au":
            continue
        clearance = dist(
            numeric_point,
            (atom.x, atom.y, atom.z),
        ) - (au_radius + _vdw_radius(atom.element, vdw_radii))
        if abs(clearance) <= _NUMERICAL_TOLERANCE:
            clearance = 0.0
        if clearance < minimum:
            minimum = clearance
            closest_atom_index = atom.index
    return StericClearance(minimum, closest_atom_index)


def with_nh2_local_h_scores(
    candidates: Sequence[OrientationCandidate],
    structure: MolecularStructure,
    hydrogen_atom_indices: Sequence[int],
) -> tuple[OrientationCandidate, ...]:
    """Attach maximin and total distances to the two explicit NH2 H atoms."""

    checked = _validated_candidates(candidates, "NH2")
    hydrogen_indices = tuple(hydrogen_atom_indices)
    if len(hydrogen_indices) != 2 or len(set(hydrogen_indices)) != 2:
        raise StericOrientationError(
            "NH2 local ranking requires exactly two unique hydrogen atoms"
        )
    for atom_index in hydrogen_indices:
        if (
            isinstance(atom_index, bool)
            or not isinstance(atom_index, int)
            or atom_index < 0
            or atom_index >= len(structure)
        ):
            raise StericOrientationError(
                "NH2 hydrogen index is outside the molecular structure"
            )
        if structure[atom_index].element != "H":
            raise StericOrientationError(
                f"NH2 local-ranking atom {atom_index} is not hydrogen"
            )

    hydrogen_points = tuple(
        (
            structure[atom_index].x,
            structure[atom_index].y,
            structure[atom_index].z,
        )
        for atom_index in hydrogen_indices
    )
    scored = []
    for candidate in checked:
        distances = tuple(
            dist(candidate.point, hydrogen_point)
            for hydrogen_point in hydrogen_points
        )
        scored.append(
            replace(
                candidate,
                nh2_min_h_distance=min(distances),
                nh2_total_h_distance=sum(distances),
            )
        )
    return tuple(scored)


def select_single_orientation(
    candidates: Sequence[OrientationCandidate],
) -> OrientationCandidate:
    """Apply NH2-local ranking when present, otherwise maximize clearance."""

    checked = _validated_candidates(candidates, "single-anchor")
    if _candidate_set_has_nh2_scores(checked):
        return max(
            checked,
            key=lambda candidate: (
                _objective_float(_nh2_minimum(candidate)),
                _objective_float(_nh2_total(candidate)),
                _objective_float(candidate.clearance),
                -candidate.candidate_order,
            ),
        )
    return max(
        checked,
        key=lambda candidate: (
            _objective_float(candidate.clearance),
            -candidate.candidate_order,
        ),
    )


def select_orientation_pair(
    first_candidates: Sequence[OrientationCandidate],
    second_candidates: Sequence[OrientationCandidate],
    vdw_radii: Mapping[str, float],
) -> tuple[OrientationCandidate, OrientationCandidate]:
    """Select a non-overlapping pair with the approved lexicographic goal."""

    first = _validated_candidates(first_candidates, "first-anchor")
    second = _validated_candidates(second_candidates, "second-anchor")
    first_is_nh2 = _candidate_set_has_nh2_scores(first)
    second_is_nh2 = _candidate_set_has_nh2_scores(second)
    au_radius = _vdw_radius("Au", vdw_radii)
    feasible_pairs: list[
        tuple[OrientationCandidate, OrientationCandidate, float]
    ] = []
    best_au_clearance = -inf
    for first_candidate in first:
        for second_candidate in second:
            au_clearance = (
                dist(first_candidate.point, second_candidate.point)
                - 2.0 * au_radius
            )
            if abs(au_clearance) <= _NUMERICAL_TOLERANCE:
                au_clearance = 0.0
            best_au_clearance = max(best_au_clearance, au_clearance)
            if au_clearance >= 0.0:
                feasible_pairs.append(
                    (first_candidate, second_candidate, au_clearance)
                )

    if not feasible_pairs:
        raise StericOrientationError(
            "no non-overlapping virtual-Au pair exists; best Au-Au clearance "
            f"is {best_au_clearance:.6f} Å"
        )

    has_nh2 = first_is_nh2 or second_is_nh2
    selected = max(
        feasible_pairs,
        key=(
            _nh2_pair_objective
            if has_nh2
            else _standard_pair_objective
        ),
    )
    return selected[0], selected[1]


def _screen_samples(
    structure: MolecularStructure,
    connectivity: Connectivity,
    binding_atom_index: int,
    origin: Vector3,
    distance_value: float,
    samples: Sequence[tuple[int | None, int, Vector3]],
    vdw_radii: Mapping[str, float],
    excluded_atom_indices: Iterable[int],
) -> tuple[OrientationCandidate, ...]:
    _validate_inputs(structure, connectivity, binding_atom_index, vdw_radii)
    numeric_origin = _finite_vector(origin, "binding-atom origin")
    exclusions = _validated_excluded_indices(
        excluded_atom_indices,
        len(structure),
        binding_atom_index,
    )
    feasible: list[OrientationCandidate] = []
    best_clearance = -inf
    best_conflict_index: int | None = None
    for azimuth_degrees, candidate_order, direction in samples:
        point = _add(numeric_origin, _scale(direction, distance_value))
        clearance = steric_clearance_for_point(
            structure,
            connectivity,
            binding_atom_index,
            point,
            vdw_radii,
            excluded_atom_indices=exclusions,
        )
        if clearance.minimum > best_clearance:
            best_clearance = clearance.minimum
            best_conflict_index = clearance.closest_atom_index
        if clearance.minimum >= 0.0:
            feasible.append(
                OrientationCandidate(
                    point=point,
                    direction=direction,
                    clearance=clearance.minimum,
                    azimuth_degrees=azimuth_degrees,
                    candidate_order=candidate_order,
                )
            )

    if feasible:
        return tuple(feasible)

    conflict = ""
    if best_conflict_index is not None:
        conflict_atom = structure[best_conflict_index]
        conflict = (
            f"; closest conflict is {conflict_atom.element}"
            f"{best_conflict_index}"
        )
    raise StericOrientationError(
        "no sterically valid Au orientation exists for the current "
        "distance/angle; best clearance is "
        f"{best_clearance:.6f} Å{conflict}"
    )


def _standard_pair_objective(
    item: tuple[OrientationCandidate, OrientationCandidate, float],
) -> tuple[float, float, float, float, int, int]:
    first, second, au_clearance = item
    return (
        _objective_float(min(first.clearance, second.clearance)),
        _objective_float(-_dot(first.direction, second.direction)),
        _objective_float(first.clearance + second.clearance),
        _objective_float(au_clearance),
        -first.candidate_order,
        -second.candidate_order,
    )


def _nh2_pair_objective(
    item: tuple[OrientationCandidate, OrientationCandidate, float],
) -> tuple[float, float, float, float, float, float, int, int]:
    first, second, au_clearance = item
    nh2_candidates = tuple(
        candidate
        for candidate in (first, second)
        if candidate.nh2_min_h_distance is not None
    )
    return (
        _objective_float(
            min(_nh2_minimum(candidate) for candidate in nh2_candidates)
        ),
        _objective_float(min(first.clearance, second.clearance)),
        _objective_float(-_dot(first.direction, second.direction)),
        _objective_float(
            sum(_nh2_total(candidate) for candidate in nh2_candidates)
        ),
        _objective_float(first.clearance + second.clearance),
        _objective_float(au_clearance),
        -first.candidate_order,
        -second.candidate_order,
    )


def _remote_atom_indices(
    connectivity: Connectivity,
    binding_atom_index: int,
    explicit_exclusions: frozenset[int],
) -> tuple[int, ...]:
    adjacency: list[list[int]] = [
        [] for _ in range(connectivity.atom_count)
    ]
    for bond in connectivity:
        adjacency[bond.first_index].append(bond.second_index)
        adjacency[bond.second_index].append(bond.first_index)

    excluded = {binding_atom_index}
    frontier = {binding_atom_index}
    for _ in range(2):
        next_frontier = {
            neighbor
            for atom_index in frontier
            for neighbor in adjacency[atom_index]
            if neighbor not in excluded
        }
        excluded.update(next_frontier)
        frontier = next_frontier
    excluded.update(explicit_exclusions)
    return tuple(
        atom_index
        for atom_index in range(connectivity.atom_count)
        if atom_index not in excluded
    )


def _validated_excluded_indices(
    indices: Iterable[int],
    atom_count: int,
    binding_atom_index: int,
) -> frozenset[int]:
    checked: set[int] = set()
    for atom_index in indices:
        if isinstance(atom_index, bool) or not isinstance(atom_index, int):
            raise TypeError("excluded atom indexes must be integers")
        if atom_index < 0 or atom_index >= atom_count:
            raise StericOrientationError(
                "excluded atom index is outside the molecular structure"
            )
        if atom_index == binding_atom_index:
            raise StericOrientationError(
                "binding atom cannot be excluded from steric evaluation"
            )
        checked.add(atom_index)
    return frozenset(checked)


def _validate_inputs(
    structure: MolecularStructure,
    connectivity: Connectivity,
    binding_atom_index: int,
    vdw_radii: Mapping[str, float],
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("connectivity must be a Connectivity")
    if connectivity.atom_count != len(structure):
        raise StericOrientationError(
            "connectivity atom count does not match the molecular structure"
        )
    if isinstance(binding_atom_index, bool) or not isinstance(
        binding_atom_index,
        int,
    ):
        raise TypeError("binding atom index must be an integer")
    if binding_atom_index < 0 or binding_atom_index >= len(structure):
        raise StericOrientationError(
            "binding atom index is outside the molecular structure"
        )
    if not isinstance(vdw_radii, Mapping):
        raise TypeError("vdw_radii must be a mapping")


def _validated_candidates(
    candidates: Sequence[OrientationCandidate],
    name: str,
) -> tuple[OrientationCandidate, ...]:
    checked = tuple(candidates)
    if not checked:
        raise StericOrientationError(f"{name} candidate set must not be empty")
    if any(not isinstance(candidate, OrientationCandidate) for candidate in checked):
        raise TypeError(
            f"{name} candidate set must contain OrientationCandidate instances"
        )
    return checked


def _candidate_set_has_nh2_scores(
    candidates: Sequence[OrientationCandidate],
) -> bool:
    scored = tuple(
        candidate.nh2_min_h_distance is not None
        or candidate.nh2_total_h_distance is not None
        for candidate in candidates
    )
    if any(scored) and not all(scored):
        raise StericOrientationError(
            "NH2 candidate scores must be present for the complete candidate set"
        )
    if all(scored):
        for candidate in candidates:
            _nh2_minimum(candidate)
            _nh2_total(candidate)
        return True
    return False


def _nh2_minimum(candidate: OrientationCandidate) -> float:
    value = candidate.nh2_min_h_distance
    if value is None or not isfinite(value) or value < 0.0:
        raise StericOrientationError(
            "NH2 minimum H distance must be finite and non-negative"
        )
    return value


def _nh2_total(candidate: OrientationCandidate) -> float:
    value = candidate.nh2_total_h_distance
    if value is None or not isfinite(value) or value < 0.0:
        raise StericOrientationError(
            "NH2 total H distance must be finite and non-negative"
        )
    return value


def _vdw_radius(element: str, radii: Mapping[str, float]) -> float:
    try:
        value = radii[element]
    except KeyError as error:
        raise StericOrientationError(
            f"no approved van der Waals radius for required element {element!r}"
        ) from error
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StericOrientationError(
            f"van der Waals radius for {element} must be numeric"
        )
    numeric_value = float(value)
    if not isfinite(numeric_value) or numeric_value <= 0.0:
        raise StericOrientationError(
            f"van der Waals radius for {element} must be finite and positive"
        )
    return numeric_value


def _positive_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric_value = float(value)
    if not isfinite(numeric_value) or numeric_value <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return numeric_value


def _validated_angle(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("cone angle must be numeric")
    numeric_value = float(value)
    if (
        not isfinite(numeric_value)
        or numeric_value <= 0.0
        or numeric_value > 180.0
    ):
        raise ValueError(
            "cone angle must be finite, greater than zero, and at most 180"
        )
    return numeric_value


def _orthonormal_pair(axis: Vector3) -> tuple[Vector3, Vector3]:
    cartesian_axes: tuple[Vector3, ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    helper = min(
        enumerate(cartesian_axes),
        key=lambda item: (abs(_dot(axis, item[1])), item[0]),
    )[1]
    first = _normalize(_cross(axis, helper), "cone perpendicular basis")
    second = _normalize(_cross(axis, first), "cone perpendicular basis")
    return first, second


def _objective_float(value: float) -> float:
    if value in {inf, -inf}:
        return value
    return round(value, 12)


def _finite_vector(vector: Vector3, name: str) -> Vector3:
    components = tuple(vector)
    if len(components) != 3:
        raise ValueError(f"{name} must contain three components")
    numeric: list[float] = []
    for component in components:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise TypeError(f"{name} components must be numeric")
        numeric_component = float(component)
        if not isfinite(numeric_component):
            raise ValueError(f"{name} components must be finite")
        numeric.append(numeric_component)
    return numeric[0], numeric[1], numeric[2]


def _normalize(vector: Vector3, name: str) -> Vector3:
    numeric_vector = _finite_vector(vector, name)
    length = hypot(*numeric_vector)
    if not isfinite(length) or length <= _NUMERICAL_TOLERANCE:
        raise StericOrientationError(f"{name} must have non-zero finite length")
    return _scale(numeric_vector, 1.0 / length)


def _add(first: Vector3, second: Vector3) -> Vector3:
    return tuple(
        first_component + second_component
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
