"""Canonical, deterministic Au-pyramid geometry owned by Moltage."""

from dataclasses import dataclass
from functools import lru_cache
from math import hypot, isclose, isfinite, sqrt

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure


AU_PYRAMID_GEOMETRY_MODEL = "MoltageAuPyramidV1"
AU_PYRAMID_SPACING_ANGSTROM = 2.88372
MIN_AU_PYRAMID_LAYERS = 2
MAX_AU_PYRAMID_LAYERS = 10
DEFAULT_AU_PYRAMID_LAYERS = 6
LatticeKey = tuple[int, int, int]
Vector3 = tuple[float, float, float]


class AuPyramidError(ValueError):
    """Raised when a canonical pyramid request or identity is invalid."""


@dataclass(frozen=True, slots=True)
class AuPyramidAtomIdentity:
    """Stable local atom identity independent of Cartesian placement."""

    local_index: int
    layer_index: int
    lattice_key: LatticeKey
    standard_pyramid_member: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.local_index, bool) or not isinstance(self.local_index, int):
            raise TypeError("Au pyramid local index must be an integer")
        if self.local_index < 0:
            raise AuPyramidError("Au pyramid local index must be non-negative")
        if isinstance(self.layer_index, bool) or not isinstance(self.layer_index, int):
            raise TypeError("Au pyramid layer index must be an integer")
        if self.layer_index < 0:
            raise AuPyramidError("Au pyramid layer index must be non-negative")
        key = tuple(self.lattice_key)
        if len(key) != 3 or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in key
        ):
            raise AuPyramidError(
                "Au pyramid lattice key must contain three non-negative integers"
            )
        if sum(key) != self.layer_index:
            raise AuPyramidError("Au pyramid lattice key must identify its layer")
        if not isinstance(self.standard_pyramid_member, bool):
            raise TypeError("standard-pyramid membership must be boolean")
        object.__setattr__(self, "lattice_key", key)


@dataclass(frozen=True, slots=True)
class MoltageAuPyramid:
    """One canonical tetrahedral Au lattice and its immutable identities."""

    pyramid_layers: int
    structure: MolecularStructure
    connectivity: Connectivity
    atom_identities: tuple[AuPyramidAtomIdentity, ...]
    apex_lattice_key: LatticeKey
    reference_corner_lattice_keys: tuple[LatticeKey, LatticeKey, LatticeKey]
    nearest_neighbor_spacing_angstrom: float = AU_PYRAMID_SPACING_ANGSTROM
    geometry_model: str = AU_PYRAMID_GEOMETRY_MODEL

    def __post_init__(self) -> None:
        layers = validate_pyramid_layers(self.pyramid_layers)
        expected_count = tetrahedral_atom_count(layers)
        if self.geometry_model != AU_PYRAMID_GEOMETRY_MODEL:
            raise AuPyramidError("unsupported Au pyramid geometry model")
        spacing = self.nearest_neighbor_spacing_angstrom
        if (
            isinstance(spacing, bool)
            or not isinstance(spacing, (int, float))
            or not isfinite(float(spacing))
            or float(spacing) <= 0.0
        ):
            raise AuPyramidError("Au pyramid spacing must be positive and finite")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("Au pyramid structure is invalid")
        if len(self.structure) != expected_count or any(
            atom.element != "Au" for atom in self.structure
        ):
            raise AuPyramidError("canonical pyramid must contain the expected Au atoms")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("Au pyramid connectivity is invalid")
        if self.connectivity.atom_count != expected_count:
            raise AuPyramidError("Au pyramid connectivity atom count is inconsistent")
        identities = tuple(self.atom_identities)
        if len(identities) != expected_count or any(
            identity.local_index != index
            for index, identity in enumerate(identities)
        ):
            raise AuPyramidError("Au pyramid identities must cover local order exactly")
        keys = tuple(identity.lattice_key for identity in identities)
        if len(set(keys)) != expected_count:
            raise AuPyramidError("Au pyramid lattice identities must be unique")
        if self.apex_lattice_key != (0, 0, 0) or keys[0] != (0, 0, 0):
            raise AuPyramidError("canonical Au pyramid apex identity is invalid")
        expected_corners = canonical_reference_corner_keys(layers)
        if tuple(self.reference_corner_lattice_keys) != expected_corners:
            raise AuPyramidError("canonical Au pyramid reference corners are invalid")
        if not set(expected_corners) <= set(keys):
            raise AuPyramidError("canonical reference corners are absent")
        object.__setattr__(self, "pyramid_layers", layers)
        object.__setattr__(self, "nearest_neighbor_spacing_angstrom", float(spacing))
        object.__setattr__(self, "atom_identities", identities)
        object.__setattr__(
            self,
            "reference_corner_lattice_keys",
            expected_corners,
        )

    @property
    def apex_local_index(self) -> int:
        return 0

    @property
    def reference_corner_local_indices(self) -> tuple[int, int, int]:
        local_by_key = {
            identity.lattice_key: identity.local_index
            for identity in self.atom_identities
        }
        return tuple(local_by_key[key] for key in self.reference_corner_lattice_keys)

    @property
    def base_layer_local_indices(self) -> tuple[int, ...]:
        return tuple(
            identity.local_index
            for identity in self.atom_identities
            if identity.layer_index == self.pyramid_layers - 1
        )

    @property
    def principal_axis(self) -> Vector3:
        apex = _coordinates(self.structure[0])
        base = tuple(self.structure[index] for index in self.base_layer_local_indices)
        centroid = tuple(
            sum(_coordinates(atom)[component] for atom in base) / len(base)
            for component in range(3)
        )
        vector = tuple(
            value - origin for value, origin in zip(centroid, apex, strict=True)
        )
        length = hypot(*vector)
        if length <= 0.0:
            raise AuPyramidError("canonical pyramid principal axis is degenerate")
        return tuple(value / length for value in vector)


def validate_pyramid_layers(value: int) -> int:
    """Return a supported layer count or reject it explicitly."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("pyramid layers must be an integer")
    if value < MIN_AU_PYRAMID_LAYERS or value > MAX_AU_PYRAMID_LAYERS:
        raise AuPyramidError(
            f"pyramid layers must be in {MIN_AU_PYRAMID_LAYERS}.."
            f"{MAX_AU_PYRAMID_LAYERS}"
        )
    return value


def tetrahedral_atom_count(pyramid_layers: int) -> int:
    """Return n(n+1)(n+2)/6 for a supported canonical pyramid."""

    layers = validate_pyramid_layers(pyramid_layers)
    return layers * (layers + 1) * (layers + 2) // 6


def canonical_reference_corner_keys(
    pyramid_layers: int,
) -> tuple[LatticeKey, LatticeKey, LatticeKey]:
    """Return immutable outer-base corners in AITRANSS order."""

    base = validate_pyramid_layers(pyramid_layers) - 1
    return (base, 0, 0), (0, base, 0), (0, 0, base)


@lru_cache(maxsize=9)
def generate_au_pyramid(pyramid_layers: int) -> MoltageAuPyramid:
    """Generate one deterministic `MoltageAuPyramidV1` lattice."""

    layers = validate_pyramid_layers(pyramid_layers)
    spacing = AU_PYRAMID_SPACING_ANGSTROM
    b1 = (spacing, 0.0, 0.0)
    b2 = (spacing / 2.0, spacing * sqrt(3.0) / 2.0, 0.0)
    b3 = (
        spacing / 2.0,
        spacing * sqrt(3.0) / 6.0,
        spacing * sqrt(2.0 / 3.0),
    )
    identities: list[AuPyramidAtomIdentity] = []
    atoms: list[Atom] = []
    for layer in range(layers):
        keys = sorted(
            (i, j, layer - i - j)
            for i in range(layer + 1)
            for j in range(layer - i + 1)
        )
        for key in keys:
            local_index = len(atoms)
            coordinate = tuple(
                key[0] * b1[component]
                + key[1] * b2[component]
                + key[2] * b3[component]
                for component in range(3)
            )
            atoms.append(Atom(local_index, "Au", *coordinate))
            identities.append(AuPyramidAtomIdentity(local_index, layer, key))

    structure = MolecularStructure(tuple(atoms), comment=AU_PYRAMID_GEOMETRY_MODEL)
    bonds: list[Bond] = []
    for first_index, first in enumerate(identities):
        for second in identities[first_index + 1 :]:
            if _lattice_distance_squared(first.lattice_key, second.lattice_key) == 1:
                bonds.append(
                    Bond(
                        first_index,
                        second.local_index,
                        _distance(
                            _coordinates(structure[first_index]),
                            _coordinates(structure[second.local_index]),
                        ),
                    )
                )
    connectivity = Connectivity(len(structure), tuple(bonds))
    pyramid = MoltageAuPyramid(
        pyramid_layers=layers,
        structure=structure,
        connectivity=connectivity,
        atom_identities=tuple(identities),
        apex_lattice_key=(0, 0, 0),
        reference_corner_lattice_keys=canonical_reference_corner_keys(layers),
    )
    if any(
        not isclose(bond.distance, spacing, rel_tol=0.0, abs_tol=1.0e-10)
        for bond in connectivity
    ):
        raise AssertionError("canonical lattice produced a non-nearest-neighbor bond")
    return pyramid


def _lattice_distance_squared(first: LatticeKey, second: LatticeKey) -> int:
    di, dj, dk = (
        first[index] - second[index] for index in range(3)
    )
    return di * di + dj * dj + dk * dk + di * dj + di * dk + dj * dk


def _coordinates(atom: Atom) -> Vector3:
    return atom.x, atom.y, atom.z


def _distance(first: Vector3, second: Vector3) -> float:
    return sqrt(
        sum((a - b) ** 2 for a, b in zip(first, second, strict=True))
    )
