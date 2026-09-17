"""Connectivity-only bond-axis rotation for the session-local molecule viewer."""

from dataclasses import dataclass
from math import cos, fmod, hypot, isfinite, radians, sin

from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import Atom, MolecularStructure


TORSION_DRAG_DEGREES_PER_PIXEL = 0.30
_AXIS_TOLERANCE = 1.0e-12


class BondTorsionError(ValueError):
    """Raised when a requested connectivity-only torsion is not well defined."""


class NonRotatableEdgeError(BondTorsionError):
    """Raised when a connectivity edge has an alternate endpoint path."""


@dataclass(frozen=True, slots=True)
class RotatableEdgeAnalysis:
    """Graph result for one existing Connectivity edge."""

    atom_a: int
    atom_b: int
    group_a: tuple[int, ...]
    group_b: tuple[int, ...]
    component_count_before: int
    component_count_after: int
    is_rotatable: bool

    def __post_init__(self) -> None:
        if self.atom_a == self.atom_b:
            raise ValueError("a torsion edge must have two distinct endpoints")
        if self.component_count_before < 1 or self.component_count_after < 1:
            raise ValueError("component counts must be positive")
        if self.is_rotatable:
            if set(self.group_a) & set(self.group_b):
                raise ValueError("rotatable endpoint groups must be disjoint")
            if self.component_count_after != self.component_count_before + 1:
                raise ValueError(
                    "a rotatable edge must increase the component count by one"
                )


@dataclass(frozen=True, slots=True)
class TorsionGroupAssignment:
    """Deterministic fixed and rotating endpoint components."""

    fixed_indices: tuple[int, ...]
    rotating_indices: tuple[int, ...]
    fixed_endpoint: int
    rotating_endpoint: int


class BondTorsionSession:
    """Own one selected bridge edge and its current relative rotation baseline."""

    __slots__ = (
        "_analysis",
        "_baseline_structure",
        "_fixed_endpoint",
        "_fixed_indices",
        "_relative_angle_degrees",
        "_rotating_endpoint",
        "_rotating_indices",
        "_structure_signature",
    )

    def __init__(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        atom_a: int,
        atom_b: int,
    ) -> None:
        if not isinstance(structure, MolecularStructure):
            raise TypeError("torsion editing requires a MolecularStructure")
        if not isinstance(connectivity, Connectivity):
            raise TypeError("torsion editing requires Connectivity")
        if connectivity.atom_count != len(structure):
            raise BondTorsionError(
                "connectivity atom count does not match the molecular structure"
            )
        analysis = analyze_rotatable_edge(connectivity, atom_a, atom_b)
        if not analysis.is_rotatable:
            raise NonRotatableEdgeError(
                "This bond cannot be rotated independently because its two "
                "sides remain connected through another path."
            )
        assignment = default_group_assignment(analysis)
        self._analysis = analysis
        self._baseline_structure = structure
        self._structure_signature = structure_signature(structure)
        self._fixed_indices = assignment.fixed_indices
        self._rotating_indices = assignment.rotating_indices
        self._fixed_endpoint = assignment.fixed_endpoint
        self._rotating_endpoint = assignment.rotating_endpoint
        self._relative_angle_degrees = 0.0

    @property
    def analysis(self) -> RotatableEdgeAnalysis:
        return self._analysis

    @property
    def selected_edge(self) -> tuple[int, int]:
        return tuple(sorted((self._analysis.atom_a, self._analysis.atom_b)))

    @property
    def fixed_indices(self) -> tuple[int, ...]:
        return self._fixed_indices

    @property
    def rotating_indices(self) -> tuple[int, ...]:
        return self._rotating_indices

    @property
    def fixed_endpoint(self) -> int:
        return self._fixed_endpoint

    @property
    def rotating_endpoint(self) -> int:
        return self._rotating_endpoint

    @property
    def relative_angle_degrees(self) -> float:
        return self._relative_angle_degrees

    def structure_at(self, angle_degrees: float) -> MolecularStructure:
        """Derive an absolute relative angle from the unchanged session baseline."""

        angle = normalize_signed_degrees(angle_degrees)
        structure = rotate_structure_about_bond(
            self._baseline_structure,
            self._fixed_endpoint,
            self._rotating_endpoint,
            self._rotating_indices,
            angle,
        )
        self._relative_angle_degrees = angle
        return structure

    def switch_rotating_side(self, current_structure: MolecularStructure) -> None:
        """Swap endpoint roles and rebase without changing current coordinates."""

        _require_matching_structure(
            current_structure,
            self._structure_signature,
            "torsion side switch",
        )
        self._fixed_indices, self._rotating_indices = (
            self._rotating_indices,
            self._fixed_indices,
        )
        self._fixed_endpoint, self._rotating_endpoint = (
            self._rotating_endpoint,
            self._fixed_endpoint,
        )
        self._baseline_structure = current_structure
        self._relative_angle_degrees = 0.0


@dataclass(frozen=True, slots=True)
class _CoordinateSnapshot:
    signature: tuple[tuple[int, str], ...]
    coordinates: tuple[tuple[float, float, float], ...]


class GeometryUndoHistory:
    """Bounded linear coordinate history guarded by structure identity."""

    __slots__ = ("_maximum_depth", "_redo_snapshots", "_undo_snapshots")

    def __init__(self, maximum_depth: int = 5) -> None:
        if isinstance(maximum_depth, bool) or not isinstance(maximum_depth, int):
            raise TypeError("geometry Undo depth must be an integer")
        if maximum_depth <= 0:
            raise ValueError("geometry Undo depth must be positive")
        self._maximum_depth = maximum_depth
        self._undo_snapshots: list[_CoordinateSnapshot] = []
        self._redo_snapshots: list[_CoordinateSnapshot] = []

    def __len__(self) -> int:
        return len(self._undo_snapshots)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_snapshots)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_snapshots)

    def clear(self) -> None:
        self._undo_snapshots.clear()
        self._redo_snapshots.clear()

    def push(self, structure: MolecularStructure) -> None:
        if not isinstance(structure, MolecularStructure):
            raise TypeError("geometry Undo requires a MolecularStructure")
        self._undo_snapshots.append(_coordinate_snapshot(structure))
        self._trim_to_maximum_depth(self._undo_snapshots)
        self._redo_snapshots.clear()

    def undo(self, current_structure: MolecularStructure) -> MolecularStructure | None:
        if not isinstance(current_structure, MolecularStructure):
            raise TypeError("geometry Undo requires a MolecularStructure")
        return self._restore(
            current_structure,
            source=self._undo_snapshots,
            destination=self._redo_snapshots,
            operation="geometry Undo",
        )

    def redo(self, current_structure: MolecularStructure) -> MolecularStructure | None:
        if not isinstance(current_structure, MolecularStructure):
            raise TypeError("geometry Redo requires a MolecularStructure")
        return self._restore(
            current_structure,
            source=self._redo_snapshots,
            destination=self._undo_snapshots,
            operation="geometry Redo",
        )

    def _restore(
        self,
        current_structure: MolecularStructure,
        *,
        source: list[_CoordinateSnapshot],
        destination: list[_CoordinateSnapshot],
        operation: str,
    ) -> MolecularStructure | None:
        if not source:
            return None
        snapshot = source[-1]
        try:
            _require_matching_structure(
                current_structure,
                snapshot.signature,
                operation,
            )
            restored = structure_with_coordinates(
                current_structure,
                snapshot.coordinates,
            )
        except BondTorsionError:
            self.clear()
            raise
        source.pop()
        destination.append(_coordinate_snapshot(current_structure))
        self._trim_to_maximum_depth(destination)
        return restored

    def _trim_to_maximum_depth(
        self,
        snapshots: list[_CoordinateSnapshot],
    ) -> None:
        if len(snapshots) > self._maximum_depth:
            del snapshots[: len(snapshots) - self._maximum_depth]


def _coordinate_snapshot(structure: MolecularStructure) -> _CoordinateSnapshot:
    return _CoordinateSnapshot(
        structure_signature(structure),
        structure_coordinates(structure),
    )


def analyze_rotatable_edge(
    connectivity: Connectivity,
    atom_a: int,
    atom_b: int,
) -> RotatableEdgeAnalysis:
    """Analyze one existing edge using connectivity alone and no chemistry."""

    if not isinstance(connectivity, Connectivity):
        raise TypeError("rotatable-edge analysis requires Connectivity")
    first = _validated_atom_index(atom_a, connectivity.atom_count, "edge atom A")
    second = _validated_atom_index(atom_b, connectivity.atom_count, "edge atom B")
    if first == second:
        raise BondTorsionError("a torsion edge must have two distinct endpoints")
    edge = tuple(sorted((first, second)))
    edges = tuple(
        (bond.first_index, bond.second_index) for bond in connectivity.bonds
    )
    if edge not in edges:
        raise BondTorsionError(
            f"atom indexes {edge[0]} and {edge[1]} are not a Connectivity edge"
        )

    adjacency_before = _adjacency(connectivity.atom_count, edges)
    remaining_edges = tuple(candidate for candidate in edges if candidate != edge)
    adjacency_after = _adjacency(connectivity.atom_count, remaining_edges)
    components_before = _connected_components(adjacency_before)
    components_after = _connected_components(adjacency_after)
    group_a = _component_containing(components_after, first)
    group_b = _component_containing(components_after, second)
    is_rotatable = group_a != group_b
    return RotatableEdgeAnalysis(
        atom_a=first,
        atom_b=second,
        group_a=group_a,
        group_b=group_b,
        component_count_before=len(components_before),
        component_count_after=len(components_after),
        is_rotatable=is_rotatable,
    )


def default_group_assignment(
    analysis: RotatableEdgeAnalysis,
) -> TorsionGroupAssignment:
    """Fix the larger endpoint group, with a minimum-index tie break."""

    if not isinstance(analysis, RotatableEdgeAnalysis):
        raise TypeError("group assignment requires RotatableEdgeAnalysis")
    if not analysis.is_rotatable:
        raise NonRotatableEdgeError(
            "the endpoint groups are not independent after removing the edge"
        )
    group_a = analysis.group_a
    group_b = analysis.group_b
    if len(group_a) > len(group_b):
        fixed_is_a = True
    elif len(group_b) > len(group_a):
        fixed_is_a = False
    else:
        fixed_is_a = min(group_a) < min(group_b)
    if fixed_is_a:
        return TorsionGroupAssignment(
            group_a,
            group_b,
            analysis.atom_a,
            analysis.atom_b,
        )
    return TorsionGroupAssignment(
        group_b,
        group_a,
        analysis.atom_b,
        analysis.atom_a,
    )


def rotate_structure_about_bond(
    structure: MolecularStructure,
    fixed_endpoint: int,
    rotating_endpoint: int,
    rotating_indices: tuple[int, ...],
    angle_degrees: float,
) -> MolecularStructure:
    """Rigidly rotate one endpoint component around the oriented bond axis."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("bond rotation requires a MolecularStructure")
    fixed_index = _validated_atom_index(
        fixed_endpoint,
        len(structure),
        "fixed endpoint",
    )
    rotating_index = _validated_atom_index(
        rotating_endpoint,
        len(structure),
        "rotating endpoint",
    )
    if fixed_index == rotating_index:
        raise BondTorsionError("bond rotation endpoints must be distinct")
    indices = _validated_rotation_indices(rotating_indices, len(structure))
    if rotating_index not in indices:
        raise BondTorsionError(
            "the rotating endpoint must belong to the rotating component"
        )
    if fixed_index in indices:
        raise BondTorsionError(
            "the fixed endpoint cannot belong to the rotating component"
        )
    angle = normalize_signed_degrees(angle_degrees)
    if abs(angle) <= _AXIS_TOLERANCE:
        return structure

    origin = _atom_point(structure[fixed_index])
    endpoint = _atom_point(structure[rotating_index])
    axis = _normalized(_subtract(endpoint, origin), "selected bond axis")
    angle_radians = radians(angle)
    cosine = cos(angle_radians)
    sine = sin(angle_radians)
    rotating_set = set(indices)
    transformed_atoms: list[Atom] = []
    for atom in structure:
        if atom.index not in rotating_set or atom.index == rotating_index:
            transformed_atoms.append(atom)
            continue
        relative = _subtract(_atom_point(atom), origin)
        rotated = _add(
            _add(
                _scale(relative, cosine),
                _scale(_cross(axis, relative), sine),
            ),
            _scale(axis, _dot(axis, relative) * (1.0 - cosine)),
        )
        coordinates = _add(origin, rotated)
        transformed_atoms.append(
            Atom(atom.index, atom.element, *coordinates)
        )
    return MolecularStructure(tuple(transformed_atoms), comment=structure.comment)


def normalize_signed_degrees(angle_degrees: float) -> float:
    """Normalize a finite angle to -180 < theta <= 180 degrees."""

    angle = _finite_float(angle_degrees, "torsion angle")
    normalized = fmod(angle, 360.0)
    if normalized <= -180.0:
        normalized += 360.0
    elif normalized > 180.0:
        normalized -= 360.0
    if abs(normalized) <= _AXIS_TOLERANCE:
        return 0.0
    return normalized


def drag_angle_from_total_displacement(
    delta_x: float,
    delta_y: float,
    drag_direction: tuple[float, float],
    *,
    sensitivity: float = TORSION_DRAG_DEGREES_PER_PIXEL,
) -> float:
    """Map total screen displacement linearly to a signed angular delta."""

    x = _finite_float(delta_x, "drag x displacement")
    y = _finite_float(delta_y, "drag y displacement")
    direction = tuple(drag_direction)
    if len(direction) != 2:
        raise ValueError("drag direction must contain x and y")
    direction_x = _finite_float(direction[0], "drag direction x")
    direction_y = _finite_float(direction[1], "drag direction y")
    length = hypot(direction_x, direction_y)
    if length <= _AXIS_TOLERANCE:
        raise BondTorsionError("drag direction must have non-zero length")
    gain = _finite_float(sensitivity, "torsion drag sensitivity")
    if gain <= 0.0:
        raise BondTorsionError("torsion drag sensitivity must be positive")
    return gain * (
        x * direction_x / length + y * direction_y / length
    )


def structure_signature(
    structure: MolecularStructure,
) -> tuple[tuple[int, str], ...]:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure signature requires a MolecularStructure")
    return tuple((atom.index, atom.element) for atom in structure)


def structure_coordinates(
    structure: MolecularStructure,
) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure coordinates require a MolecularStructure")
    return tuple(_atom_point(atom) for atom in structure)


def structure_with_coordinates(
    structure: MolecularStructure,
    coordinates: tuple[tuple[float, float, float], ...],
) -> MolecularStructure:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("coordinate replacement requires a MolecularStructure")
    points = tuple(coordinates)
    if len(points) != len(structure):
        raise BondTorsionError(
            "coordinate snapshot atom count does not match the current structure"
        )
    atoms = tuple(
        Atom(atom.index, atom.element, *_validated_point(point, "atom coordinates"))
        for atom, point in zip(structure, points, strict=True)
    )
    return MolecularStructure(atoms, comment=structure.comment)


def _adjacency(
    atom_count: int,
    edges: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, ...], ...]:
    neighbors: list[set[int]] = [set() for _ in range(atom_count)]
    for first, second in edges:
        neighbors[first].add(second)
        neighbors[second].add(first)
    return tuple(tuple(sorted(values)) for values in neighbors)


def _connected_components(
    adjacency: tuple[tuple[int, ...], ...],
) -> tuple[tuple[int, ...], ...]:
    unvisited = set(range(len(adjacency)))
    components: list[tuple[int, ...]] = []
    while unvisited:
        start = min(unvisited)
        stack = [start]
        component: set[int] = set()
        while stack:
            atom_index = stack.pop()
            if atom_index in component:
                continue
            component.add(atom_index)
            unvisited.discard(atom_index)
            stack.extend(
                neighbor
                for neighbor in reversed(adjacency[atom_index])
                if neighbor not in component
            )
        components.append(tuple(sorted(component)))
    return tuple(components)


def _component_containing(
    components: tuple[tuple[int, ...], ...],
    atom_index: int,
) -> tuple[int, ...]:
    for component in components:
        if atom_index in component:
            return component
    raise RuntimeError("validated atom index is absent from graph components")


def _validated_atom_index(value: object, atom_count: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0 or value >= atom_count:
        raise BondTorsionError(
            f"{label} is outside the current structure: {value} not in "
            f"[0, {atom_count})"
        )
    return value


def _validated_rotation_indices(
    indices: tuple[int, ...],
    atom_count: int,
) -> tuple[int, ...]:
    values = tuple(indices)
    if len(set(values)) != len(values):
        raise BondTorsionError("rotating atom indexes must be unique")
    validated = tuple(
        _validated_atom_index(value, atom_count, "rotating atom index")
        for value in values
    )
    return tuple(sorted(validated))


def _require_matching_structure(
    structure: MolecularStructure,
    expected_signature: tuple[tuple[int, str], ...],
    operation: str,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError(f"{operation} requires a MolecularStructure")
    if structure_signature(structure) != expected_signature:
        raise BondTorsionError(
            f"{operation} cannot cross a structure replacement or atom reindexing"
        )


def _validated_point(
    point: tuple[float, float, float],
    label: str,
) -> tuple[float, float, float]:
    values = tuple(point)
    if len(values) != 3:
        raise ValueError(f"{label} must contain x, y, and z")
    return tuple(_finite_float(value, label) for value in values)


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise BondTorsionError(f"{label} must be finite")
    return numeric


def _atom_point(atom: Atom) -> tuple[float, float, float]:
    return atom.x, atom.y, atom.z


def _normalized(
    vector: tuple[float, float, float],
    label: str,
) -> tuple[float, float, float]:
    length = hypot(*vector)
    if not isfinite(length) or length <= _AXIS_TOLERANCE:
        raise BondTorsionError(f"{label} must have non-zero finite length")
    return _scale(vector, 1.0 / length)


def _add(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        first_component + second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _subtract(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        first_component - second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _scale(
    vector: tuple[float, float, float],
    factor: float,
) -> tuple[float, float, float]:
    return tuple(component * factor for component in vector)


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(
        first_component * second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
