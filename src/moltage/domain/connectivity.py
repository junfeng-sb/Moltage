"""Immutable domain objects for connected/not-connected molecular graphs."""

from collections.abc import Iterator
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class Bond:
    """An undirected connection between two zero-based atom indexes."""

    first_index: int
    second_index: int
    distance: float

    def __post_init__(self) -> None:
        for name in ("first_index", "second_index"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

        if self.first_index == self.second_index:
            raise ValueError("a bond must connect two different atom indexes")
        if self.first_index > self.second_index:
            first_index = self.second_index
            second_index = self.first_index
            object.__setattr__(self, "first_index", first_index)
            object.__setattr__(self, "second_index", second_index)

        distance = _finite_number(self.distance, "bond distance")
        if distance < 0.0:
            raise ValueError("bond distance must be non-negative")
        object.__setattr__(self, "distance", distance)


@dataclass(frozen=True, slots=True)
class Connectivity:
    """A deterministic collection of undirected bonds for one structure."""

    atom_count: int
    bonds: tuple[Bond, ...]

    def __post_init__(self) -> None:
        if isinstance(self.atom_count, bool) or not isinstance(self.atom_count, int):
            raise TypeError("connectivity atom count must be an integer")
        if self.atom_count < 0:
            raise ValueError("connectivity atom count must be non-negative")

        bonds = tuple(self.bonds)
        seen_pairs: set[tuple[int, int]] = set()
        for bond in bonds:
            if not isinstance(bond, Bond):
                raise TypeError("connectivity bonds must contain Bond instances")
            if bond.second_index >= self.atom_count:
                raise ValueError(
                    "bond index is outside the connectivity atom range: "
                    f"{bond.second_index} >= {self.atom_count}"
                )
            pair = (bond.first_index, bond.second_index)
            if pair in seen_pairs:
                raise ValueError(f"duplicate bond for atom indexes {pair}")
            seen_pairs.add(pair)

        object.__setattr__(
            self,
            "bonds",
            tuple(
                sorted(
                    bonds,
                    key=lambda bond: (bond.first_index, bond.second_index),
                )
            ),
        )

    def __len__(self) -> int:
        return len(self.bonds)

    def __iter__(self) -> Iterator[Bond]:
        return iter(self.bonds)


def _finite_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric_value = float(value)
    if not isfinite(numeric_value):
        raise ValueError(f"{name} must be finite")
    return numeric_value
