"""Infer undirected molecular connectivity from interatomic distances."""

from collections.abc import Mapping
from math import dist, isfinite

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import MolecularStructure


DEFAULT_CONNECTIVITY_MULTIPLIER = 1.10


class UnsupportedElementError(ValueError):
    """Raised when connectivity inference has no radius for an element."""


def infer_connectivity(
    structure: MolecularStructure,
    covalent_radii: Mapping[str, float],
    multiplier: float = DEFAULT_CONNECTIVITY_MULTIPLIER,
) -> Connectivity:
    """Infer all pairs satisfying d(i, j) <= m * (r_i + r_j)."""

    numeric_multiplier = _validated_multiplier(multiplier)
    atom_radii = tuple(
        _radius_for(atom.element, covalent_radii) for atom in structure
    )
    bonds: list[Bond] = []

    for first_index, first_atom in enumerate(structure):
        first_coordinates = (first_atom.x, first_atom.y, first_atom.z)
        for second_index in range(first_index + 1, len(structure)):
            second_atom = structure[second_index]
            distance = dist(
                first_coordinates,
                (second_atom.x, second_atom.y, second_atom.z),
            )
            cutoff = numeric_multiplier * (
                atom_radii[first_index] + atom_radii[second_index]
            )
            if distance <= cutoff:
                bonds.append(
                    Bond(
                        first_index=first_index,
                        second_index=second_index,
                        distance=distance,
                    )
                )

    return Connectivity(atom_count=len(structure), bonds=tuple(bonds))


def _validated_multiplier(multiplier: float) -> float:
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        raise TypeError("connectivity multiplier must be numeric")
    numeric_multiplier = float(multiplier)
    if not isfinite(numeric_multiplier) or numeric_multiplier <= 0.0:
        raise ValueError(
            "connectivity multiplier must be finite and greater than zero"
        )
    return numeric_multiplier


def _radius_for(element: str, covalent_radii: Mapping[str, float]) -> float:
    try:
        radius = covalent_radii[element]
    except KeyError as error:
        raise UnsupportedElementError(
            f"unsupported element {element!r}: no covalent radius is configured"
        ) from error

    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        raise ValueError(f"covalent radius for {element} must be numeric")
    numeric_radius = float(radius)
    if not isfinite(numeric_radius) or numeric_radius <= 0.0:
        raise ValueError(
            f"covalent radius for {element} must be finite and greater than zero"
        )
    return numeric_radius
