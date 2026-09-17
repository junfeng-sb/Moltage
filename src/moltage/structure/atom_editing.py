"""Deterministic single-atom edits for an ordered molecular graph."""

from dataclasses import dataclass

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure


class AtomEditingError(ValueError):
    """Raised when a requested atom edit cannot target the current structure."""


@dataclass(frozen=True, slots=True)
class AtomEditResult:
    """One edited structure, graph, and complete source-index mapping."""

    structure: MolecularStructure
    connectivity: Connectivity
    old_to_new_indices: tuple[int | None, ...]


def delete_atom(
    structure: MolecularStructure,
    connectivity: Connectivity,
    atom_index: int,
) -> AtomEditResult:
    """Delete one atom, its incident edges, and compact later atom indexes."""

    _validate_source(structure, connectivity)
    target = _validated_atom_index(atom_index, len(structure))

    mapping: list[int | None] = []
    atoms: list[Atom] = []
    for atom in structure:
        if atom.index == target:
            mapping.append(None)
            continue
        new_index = len(atoms)
        mapping.append(new_index)
        atoms.append(Atom(new_index, atom.element, atom.x, atom.y, atom.z))

    bonds: list[Bond] = []
    for bond in connectivity:
        first = mapping[bond.first_index]
        second = mapping[bond.second_index]
        if first is None or second is None:
            continue
        bonds.append(Bond(first, second, bond.distance))

    edited_structure = MolecularStructure(tuple(atoms), comment=structure.comment)
    return AtomEditResult(
        edited_structure,
        Connectivity(len(edited_structure), tuple(bonds)),
        tuple(mapping),
    )


def replace_atom(
    structure: MolecularStructure,
    connectivity: Connectivity,
    atom_index: int,
    element: str,
) -> AtomEditResult:
    """Replace one element while preserving atom identity, position, and edges."""

    _validate_source(structure, connectivity)
    target = _validated_atom_index(atom_index, len(structure))
    source_atom = structure[target]
    replacement = Atom(
        target,
        element,
        source_atom.x,
        source_atom.y,
        source_atom.z,
    )
    if replacement == source_atom:
        edited_structure = structure
    else:
        atoms = list(structure.atoms)
        atoms[target] = replacement
        edited_structure = MolecularStructure(tuple(atoms), comment=structure.comment)
    return AtomEditResult(
        edited_structure,
        connectivity,
        tuple(range(len(structure))),
    )


def _validate_source(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("atom editing requires a MolecularStructure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("atom editing requires Connectivity")
    if connectivity.atom_count != len(structure):
        raise AtomEditingError(
            "connectivity atom count does not match the edited structure"
        )


def _validated_atom_index(atom_index: int, atom_count: int) -> int:
    if isinstance(atom_index, bool) or not isinstance(atom_index, int):
        raise TypeError("edited atom index must be an integer")
    if atom_index < 0 or atom_index >= atom_count:
        raise AtomEditingError(
            f"edited atom index is outside the current structure: "
            f"{atom_index} not in [0, {atom_count})"
        )
    return atom_index
