"""Minimal domain model for ordered molecular structures."""

from collections.abc import Iterator
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class Atom:
    """An atom identified by its zero-based position in a structure."""

    index: int
    element: str
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("atom index must be an integer")
        if self.index < 0:
            raise ValueError("atom index must be non-negative")

        if (
            not isinstance(self.element, str)
            or not self.element
            or not self.element.isascii()
            or not self.element.isalpha()
        ):
            raise ValueError("element symbol must contain ASCII letters only")
        normalized_element = self.element[0].upper() + self.element[1:].lower()
        object.__setattr__(self, "element", normalized_element)

        for coordinate_name in ("x", "y", "z"):
            coordinate = getattr(self, coordinate_name)
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise TypeError(f"{coordinate_name} coordinate must be numeric")
            numeric_coordinate = float(coordinate)
            if not isfinite(numeric_coordinate):
                raise ValueError(f"{coordinate_name} coordinate must be finite")
            object.__setattr__(self, coordinate_name, numeric_coordinate)


@dataclass(frozen=True, slots=True)
class MolecularStructure:
    """An immutable ordered collection using zero-based atom indexes."""

    atoms: tuple[Atom, ...]
    comment: str = ""

    def __post_init__(self) -> None:
        ordered_atoms = tuple(self.atoms)
        for expected_index, atom in enumerate(ordered_atoms):
            if atom.index != expected_index:
                raise ValueError(
                    "atom index must match its zero-based position: "
                    f"expected {expected_index}, found {atom.index}"
                )
        if not isinstance(self.comment, str):
            raise TypeError("structure comment must be a string")
        object.__setattr__(self, "atoms", ordered_atoms)

    def __len__(self) -> int:
        return len(self.atoms)

    def __iter__(self) -> Iterator[Atom]:
        return iter(self.atoms)

    def __getitem__(self, index: int) -> Atom:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("atom index must be an integer")
        if index < 0:
            raise IndexError("atom index must be zero-based and non-negative")
        return self.atoms[index]
