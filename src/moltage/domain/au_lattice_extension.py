"""Signed Au(111) lattice identities and deterministic candidate geometry."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import dist, isfinite

from moltage.domain.au_pyramid import (
    AU_PYRAMID_SPACING_ANGSTROM,
    LatticeKey,
    generate_au_pyramid,
    validate_pyramid_layers,
)


AU_LATTICE_EXTENSION_ORIGIN = "LATTICE_EXTENSION"
AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM = 1.0e-6
SAME_LAYER_NEIGHBOR_DELTAS: tuple[LatticeKey, ...] = (
    (1, -1, 0),
    (-1, 1, 0),
    (1, 0, -1),
    (-1, 0, 1),
    (0, 1, -1),
    (0, -1, 1),
)
Vector3 = tuple[float, float, float]


class AuLatticeExtensionError(ValueError):
    """Raised when signed lattice identity or geometry is inconsistent."""


@dataclass(frozen=True, slots=True)
class AuLatticeExtensionSite:
    """One side-specific signed lattice identity outside or inside the core."""

    side: str
    layer_index: int
    lattice_key: LatticeKey

    def __post_init__(self) -> None:
        side = str(self.side).upper()
        if side not in {"LEFT", "RIGHT"}:
            raise AuLatticeExtensionError("Au lattice side must be LEFT or RIGHT")
        _validate_layer_key(self.layer_index, self.lattice_key)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "lattice_key", tuple(self.lattice_key))

    @property
    def sort_key(self) -> tuple[int, int, int, int, int]:
        return (
            0 if self.side == "LEFT" else 1,
            self.layer_index,
            *self.lattice_key,
        )


@dataclass(frozen=True, slots=True)
class AuLatticeExtensionCandidate:
    """A currently legal extension site with its identity-derived coordinate."""

    side: str
    layer_index: int
    lattice_key: LatticeKey
    coordinates: Vector3

    def __post_init__(self) -> None:
        site = AuLatticeExtensionSite(
            self.side,
            self.layer_index,
            self.lattice_key,
        )
        coordinates = _finite_vector(self.coordinates, "candidate coordinate")
        object.__setattr__(self, "side", site.side)
        object.__setattr__(self, "lattice_key", site.lattice_key)
        object.__setattr__(self, "coordinates", coordinates)

    @property
    def identity(self) -> AuLatticeExtensionSite:
        return AuLatticeExtensionSite(
            self.side,
            self.layer_index,
            self.lattice_key,
        )

    @property
    def sort_key(self) -> tuple[int, int, int, int, int]:
        return self.identity.sort_key


@dataclass(frozen=True, slots=True)
class AuLatticeExtension:
    """One accepted Au atom appended from a signed lattice candidate."""

    side: str
    layer_index: int
    lattice_key: LatticeKey
    global_atom_index: int
    origin: str = AU_LATTICE_EXTENSION_ORIGIN

    def __post_init__(self) -> None:
        site = AuLatticeExtensionSite(
            self.side,
            self.layer_index,
            self.lattice_key,
        )
        if (
            isinstance(self.global_atom_index, bool)
            or not isinstance(self.global_atom_index, int)
            or self.global_atom_index < 0
        ):
            raise AuLatticeExtensionError(
                "extension global atom index must be a non-negative integer"
            )
        if self.origin != AU_LATTICE_EXTENSION_ORIGIN:
            raise AuLatticeExtensionError(
                f"extension origin must be {AU_LATTICE_EXTENSION_ORIGIN}"
            )
        object.__setattr__(self, "side", site.side)
        object.__setattr__(self, "lattice_key", site.lattice_key)

    @property
    def identity(self) -> AuLatticeExtensionSite:
        return AuLatticeExtensionSite(
            self.side,
            self.layer_index,
            self.lattice_key,
        )


@dataclass(frozen=True, slots=True)
class AuLatticeFrame:
    """An identity-anchored affine frame proven against a standard pyramid."""

    side: str
    pyramid_layers: int
    spacing_angstrom: float
    origin: Vector3
    basis_i: Vector3
    basis_j: Vector3
    basis_k: Vector3

    @classmethod
    def from_standard_mapping(
        cls,
        *,
        side: str,
        pyramid_layers: int,
        spacing_angstrom: float,
        coordinates_by_key: Iterable[tuple[LatticeKey, Vector3]],
        tolerance_angstrom: float = AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM,
    ) -> "AuLatticeFrame":
        """Build a frame from exact standard identities, rejecting fitting."""

        normalized_side = AuLatticeExtensionSite(side, 0, (0, 0, 0)).side
        layers = validate_pyramid_layers(pyramid_layers)
        spacing = _positive_finite(spacing_angstrom, "Au lattice spacing")
        tolerance = _positive_finite(
            tolerance_angstrom,
            "Au lattice geometry tolerance",
        )
        supplied: dict[LatticeKey, Vector3] = {}
        for raw_key, raw_coordinates in tuple(coordinates_by_key):
            key = tuple(raw_key)
            if key in supplied:
                raise AuLatticeExtensionError(
                    f"{normalized_side} standard lattice mapping is ambiguous"
                )
            if len(key) != 3 or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in key
            ):
                raise AuLatticeExtensionError(
                    "standard lattice keys must contain three non-negative integers"
                )
            supplied[key] = _finite_vector(
                raw_coordinates,
                "standard lattice coordinate",
            )

        canonical = generate_au_pyramid(layers)
        expected_keys = tuple(
            identity.lattice_key for identity in canonical.atom_identities
        )
        if set(supplied) != set(expected_keys):
            raise AuLatticeExtensionError(
                f"{normalized_side} standard lattice mapping is incomplete"
            )
        if abs(spacing - AU_PYRAMID_SPACING_ANGSTROM) > tolerance:
            raise AuLatticeExtensionError(
                "Au lattice spacing conflicts with MoltageAuPyramidV1"
            )

        origin = supplied[(0, 0, 0)]
        basis_i = _subtract(supplied[(1, 0, 0)], origin)
        basis_j = _subtract(supplied[(0, 1, 0)], origin)
        basis_k = _subtract(supplied[(0, 0, 1)], origin)
        frame = cls(
            normalized_side,
            layers,
            spacing,
            origin,
            basis_i,
            basis_j,
            basis_k,
        )

        anchor_vectors = (basis_i, basis_j, basis_k)
        if any(
            abs(dist((0.0, 0.0, 0.0), vector) - spacing) > tolerance
            for vector in anchor_vectors
        ) or any(
            abs(dist(anchor_vectors[first], anchor_vectors[second]) - spacing)
            > tolerance
            for first in range(3)
            for second in range(first + 1, 3)
        ):
            raise AuLatticeExtensionError(
                f"{normalized_side} standard lattice frame is not rigid"
            )
        if any(
            dist(frame.coordinate(key), supplied[key]) > tolerance
            for key in expected_keys
        ):
            raise AuLatticeExtensionError(
                f"{normalized_side} standard lattice mapping is not rigid"
            )
        return frame

    def coordinate(self, lattice_key: LatticeKey) -> Vector3:
        """Map any signed lattice key into the side's current world frame."""

        key = tuple(lattice_key)
        if len(key) != 3 or any(
            isinstance(value, bool) or not isinstance(value, int) for value in key
        ):
            raise AuLatticeExtensionError(
                "Au lattice key must contain exactly three integers"
            )
        return tuple(
            self.origin[axis]
            + key[0] * self.basis_i[axis]
            + key[1] * self.basis_j[axis]
            + key[2] * self.basis_k[axis]
            for axis in range(3)
        )


def same_layer_neighbor_keys(lattice_key: LatticeKey) -> tuple[LatticeKey, ...]:
    """Return the six deterministic triangular-lattice nearest neighbors."""

    key = tuple(lattice_key)
    if len(key) != 3 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in key
    ):
        raise AuLatticeExtensionError(
            "Au lattice key must contain exactly three integers"
        )
    return tuple(
        tuple(key[index] + delta[index] for index in range(3))
        for delta in SAME_LAYER_NEIGHBOR_DELTAS
    )


def lattice_distance_squared(first: LatticeKey, second: LatticeKey) -> int:
    """Return the integer squared distance in the canonical tetrahedral basis."""

    first_key = tuple(first)
    second_key = tuple(second)
    if len(first_key) != 3 or len(second_key) != 3 or any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (*first_key, *second_key)
    ):
        raise AuLatticeExtensionError(
            "Au lattice distance requires two integer triplets"
        )
    di, dj, dk = tuple(
        first_key[index] - second_key[index] for index in range(3)
    )
    return di * di + dj * dj + dk * dk + di * dj + di * dk + dj * dk


def raw_extension_candidate_keys(
    occupied_keys: Iterable[LatticeKey],
    *,
    layer_index: int,
) -> tuple[LatticeKey, ...]:
    """Generate finite seed or boundary candidates from one layer occupancy."""

    occupied = {tuple(key) for key in occupied_keys}
    if not occupied:
        raise AuLatticeExtensionError("Au lattice layer occupancy must not be empty")
    for key in occupied:
        _validate_layer_key(layer_index, key)

    if len(occupied) == 1:
        only = next(iter(occupied))
        if layer_index != 0 or only != (0, 0, 0):
            raise AuLatticeExtensionError(
                "a one-site extension seed is valid only for the canonical apex"
            )
        return tuple(sorted(same_layer_neighbor_keys(only)))

    if len(occupied) == 2:
        first, second = sorted(occupied)
        if lattice_distance_squared(first, second) != 1:
            raise AuLatticeExtensionError(
                "a two-site extension seed requires adjacent occupied sites"
            )
        common = set(same_layer_neighbor_keys(first)) & set(
            same_layer_neighbor_keys(second)
        )
        if len(common) != 2:
            raise AssertionError("triangular lattice edge must have two third sites")
        return tuple(sorted(common))

    candidates: set[LatticeKey] = set()
    for first in sorted(occupied):
        for second in same_layer_neighbor_keys(first):
            if second not in occupied or second <= first:
                continue
            third_sites = set(same_layer_neighbor_keys(first)) & set(
                same_layer_neighbor_keys(second)
            )
            occupied_thirds = third_sites & occupied
            if len(occupied_thirds) == 1:
                candidates.update(third_sites - occupied)
    return tuple(sorted(candidates))


def has_au_lattice_clearance(
    candidate_coordinates: Vector3,
    existing_au_coordinates: Iterable[Vector3],
    *,
    spacing_angstrom: float,
    tolerance_angstrom: float = AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM,
) -> bool:
    """Reject coordinate coincidence and sub-spacing Au-Au separation."""

    candidate = _finite_vector(candidate_coordinates, "candidate coordinate")
    spacing = _positive_finite(spacing_angstrom, "Au lattice spacing")
    tolerance = _positive_finite(
        tolerance_angstrom,
        "Au lattice geometry tolerance",
    )
    for existing in tuple(existing_au_coordinates):
        separation = dist(candidate, _finite_vector(existing, "existing Au coordinate"))
        if separation <= tolerance or separation < spacing - tolerance:
            return False
    return True


def _validate_layer_key(layer_index: int, lattice_key: LatticeKey) -> None:
    if (
        isinstance(layer_index, bool)
        or not isinstance(layer_index, int)
        or layer_index < 0
    ):
        raise AuLatticeExtensionError(
            "Au lattice layer index must be a non-negative integer"
        )
    key = tuple(lattice_key)
    if len(key) != 3 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in key
    ):
        raise AuLatticeExtensionError(
            "Au extension lattice key must contain exactly three signed integers"
        )
    if sum(key) != layer_index:
        raise AuLatticeExtensionError(
            "Au extension lattice key must identify its recorded layer"
        )


def _positive_finite(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise AuLatticeExtensionError(f"{name} must be positive and finite")
    return float(value)


def _finite_vector(value: Iterable[float], name: str) -> Vector3:
    coordinates = tuple(value)
    if len(coordinates) != 3 or any(
        isinstance(component, bool)
        or not isinstance(component, (int, float))
        or not isfinite(float(component))
        for component in coordinates
    ):
        raise AuLatticeExtensionError(f"{name} must contain three finite numbers")
    return tuple(float(component) for component in coordinates)


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return tuple(
        first[index] - second[index]
        for index in range(3)
    )
