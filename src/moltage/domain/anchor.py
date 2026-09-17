"""Immutable domain objects for supported molecular anchor candidates."""

from dataclasses import dataclass
from enum import StrEnum


class AnchorKind(StrEnum):
    """The explicitly supported molecular anchor groups."""

    NCS = "NCS"
    NH2 = "NH2"
    PYRIDINE_N = "Pyridine-N"
    SMe = "SMe"
    SH = "SH"
    ALKYNYL_C = "Alkynyl-C"
    CYANO_N = "Cyano-N"
    DICYANO_C = "Dicyano-C"


@dataclass(frozen=True, slots=True)
class AnchorCandidate:
    """A detected anchor group and its future electrode-binding atom."""

    kind: AnchorKind
    binding_atom_index: int
    atom_indices: tuple[int, ...]
    attached_au_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AnchorKind):
            raise TypeError("anchor kind must be an AnchorKind")
        _validate_atom_index(self.binding_atom_index, "binding atom index")

        atom_indices = tuple(self.atom_indices)
        for atom_index in atom_indices:
            _validate_atom_index(atom_index, "anchor atom index")
        if len(set(atom_indices)) != len(atom_indices):
            raise ValueError("anchor atom indexes must be unique")
        if self.binding_atom_index not in atom_indices:
            raise ValueError("binding atom index must belong to anchor atom indexes")

        attached_au_indices = tuple(self.attached_au_indices)
        for atom_index in attached_au_indices:
            _validate_atom_index(atom_index, "attached Au index")
        if len(set(attached_au_indices)) != len(attached_au_indices):
            raise ValueError("attached Au indexes must be unique")
        if set(atom_indices) & set(attached_au_indices):
            raise ValueError("attached Au indexes must not overlap anchor atom indexes")

        object.__setattr__(self, "atom_indices", tuple(sorted(atom_indices)))
        object.__setattr__(
            self,
            "attached_au_indices",
            tuple(sorted(attached_au_indices)),
        )


def _validate_atom_index(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
