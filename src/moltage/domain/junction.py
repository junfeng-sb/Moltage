"""Immutable domain results for proposed and applied junction contacts."""

from dataclasses import dataclass
from math import isfinite

from moltage.domain.anchor import AnchorCandidate
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure


@dataclass(frozen=True, slots=True)
class AuPlacementProposal:
    """One virtual Au position for an existing detected anchor candidate."""

    anchor: AnchorCandidate
    x: float
    y: float
    z: float
    remove_atom_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, AnchorCandidate):
            raise TypeError("proposal anchor must be an AnchorCandidate")

        for coordinate_name in ("x", "y", "z"):
            coordinate = getattr(self, coordinate_name)
            if isinstance(coordinate, bool) or not isinstance(
                coordinate,
                (int, float),
            ):
                raise TypeError(f"proposal {coordinate_name} must be numeric")
            numeric_coordinate = float(coordinate)
            if not isfinite(numeric_coordinate):
                raise ValueError(
                    f"proposal {coordinate_name} must be finite"
                )
            object.__setattr__(self, coordinate_name, numeric_coordinate)

        remove_atom_indices = tuple(self.remove_atom_indices)
        for atom_index in remove_atom_indices:
            if isinstance(atom_index, bool) or not isinstance(atom_index, int):
                raise TypeError("remove atom indexes must be integers")
            if atom_index < 0:
                raise ValueError("remove atom indexes must be non-negative")
        if len(set(remove_atom_indices)) != len(remove_atom_indices):
            raise ValueError("remove atom indexes must be unique")
        if self.anchor.binding_atom_index in remove_atom_indices:
            raise ValueError("binding atom must not be scheduled for removal")
        overlap = set(remove_atom_indices) & set(
            self.anchor.attached_au_indices
        )
        if overlap:
            raise ValueError(
                "attached Au atoms must not be scheduled for removal: "
                f"{tuple(sorted(overlap))}"
            )
        object.__setattr__(
            self,
            "remove_atom_indices",
            tuple(sorted(remove_atom_indices)),
        )


@dataclass(frozen=True, slots=True)
class AppliedAuPlacement:
    """A transformed structure plus explicit source-to-result traceability."""

    structure: MolecularStructure
    connectivity: Connectivity
    old_to_new_indices: tuple[int | None, ...]
    removed_atom_indices: tuple[int, ...]
    added_au_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("applied structure must be a MolecularStructure")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("applied connectivity must be a Connectivity")
        if self.connectivity.atom_count != len(self.structure):
            raise ValueError(
                "applied connectivity atom count must match the structure"
            )

        mapping = tuple(self.old_to_new_indices)
        mapped_indices: list[int] = []
        for value in mapping:
            if value is None:
                continue
            _validate_result_index(value, len(self.structure), "mapped index")
            mapped_indices.append(value)
        if len(set(mapped_indices)) != len(mapped_indices):
            raise ValueError("surviving mapped indexes must be unique")

        validated_removed = _validated_unique_indices(
            self.removed_atom_indices,
            len(mapping),
            "removed source index",
        )
        removed = tuple(sorted(validated_removed))
        none_indices = tuple(
            source_index
            for source_index, value in enumerate(mapping)
            if value is None
        )
        if removed != none_indices:
            raise ValueError(
                "removed source indexes must correspond exactly to None "
                "mapping entries"
            )

        added = _validated_unique_indices(
            self.added_au_indices,
            len(self.structure),
            "added Au index",
        )
        if set(mapped_indices) & set(added):
            raise ValueError(
                "added Au indexes must not overlap surviving mapped indexes"
            )
        if set(mapped_indices) | set(added) != set(range(len(self.structure))):
            raise ValueError(
                "mapped survivors and added Au indexes must cover the result"
            )
        if any(
            self.structure[atom_index].element != "Au"
            for atom_index in added
        ):
            raise ValueError("added Au indexes must identify Au atoms")

        object.__setattr__(self, "old_to_new_indices", mapping)
        object.__setattr__(self, "removed_atom_indices", removed)
        object.__setattr__(self, "added_au_indices", added)


def _validated_unique_indices(
    values: tuple[int, ...],
    upper_bound: int,
    name: str,
) -> tuple[int, ...]:
    indices = tuple(values)
    for value in indices:
        _validate_result_index(value, upper_bound, name)
    if len(set(indices)) != len(indices):
        raise ValueError(f"{name} values must be unique")
    return indices


def _validate_result_index(value: int, upper_bound: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or value >= upper_bound:
        raise ValueError(f"{name} is outside its atom range")
