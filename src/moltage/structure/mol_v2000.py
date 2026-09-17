"""Narrow reader for connection-table MOL V2000 geometry files."""

from dataclasses import dataclass
from math import dist
from os import PathLike
from pathlib import Path

from moltage.domain.bond_display import BondDisplayOrder
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure


class MolV2000ParseError(ValueError):
    """Raised when required MOL V2000 content is malformed."""


class UnsupportedMolVersionError(MolV2000ParseError):
    """Raised when a MOL connection table is not V2000."""


class UnsupportedMolBondTypeError(MolV2000ParseError):
    """Raised when a MOL bond cannot be shown by the MVP."""


@dataclass(frozen=True, slots=True)
class MolV2000Geometry:
    """Atoms, explicit logical graph, and display-only bond orders from MOL."""

    structure: MolecularStructure
    connectivity: Connectivity
    bond_display_orders: tuple[BondDisplayOrder, ...]


def read_mol_v2000(path: str | PathLike[str]) -> MolV2000Geometry:
    """Read one UTF-8 MOL V2000 file without interpreting SDF records."""

    return parse_mol_v2000(Path(path).read_text(encoding="utf-8"))


def parse_mol_v2000(text: str) -> MolV2000Geometry:
    """Parse the fixed-column V2000 counts, atom, and bond blocks."""

    lines = text.splitlines()
    if len(lines) < 4:
        raise MolV2000ParseError(
            "MOL V2000 file is truncated before the counts line"
        )
    counts_line = lines[3]
    upper_counts = counts_line.upper()
    if "V3000" in upper_counts:
        raise UnsupportedMolVersionError(
            "MOL V3000 is unsupported; only MOL V2000 is accepted"
        )
    version = counts_line[33:39].strip().upper() if len(counts_line) >= 39 else ""
    if version != "V2000":
        raise UnsupportedMolVersionError(
            "unsupported or missing MOL version; expected V2000"
        )
    atom_count = _fixed_integer(counts_line, 0, 3, 4, "atom count")
    bond_count = _fixed_integer(counts_line, 3, 6, 4, "bond count")
    if atom_count < 0 or bond_count < 0:
        raise MolV2000ParseError(
            "line 4: atom and bond counts must be non-negative"
        )

    atom_start = 4
    bond_start = atom_start + atom_count
    content_end = bond_start + bond_count
    if len(lines) < content_end:
        raise MolV2000ParseError(
            "MOL V2000 file is truncated inside the declared atom or bond block"
        )

    atoms = tuple(
        _parse_atom_line(lines[line_index], line_index + 1, atom_index)
        for atom_index, line_index in enumerate(range(atom_start, bond_start))
    )
    structure = MolecularStructure(atoms, comment=lines[0])

    bonds: list[Bond] = []
    display_orders: list[BondDisplayOrder] = []
    seen: dict[tuple[int, int], int] = {}
    for line_index in range(bond_start, content_end):
        line_number = line_index + 1
        record = lines[line_index]
        first_one_based = _fixed_integer(
            record, 0, 3, line_number, "first atom index"
        )
        second_one_based = _fixed_integer(
            record, 3, 6, line_number, "second atom index"
        )
        bond_type = _fixed_integer(record, 6, 9, line_number, "bond type")
        if bond_type not in {1, 2, 3}:
            raise UnsupportedMolBondTypeError(
                f"line {line_number}: unsupported MOL bond type {bond_type}; "
                "only single (1), double (2), and triple (3) are accepted"
            )
        if not (1 <= first_one_based <= atom_count):
            raise MolV2000ParseError(
                f"line {line_number}: first atom index {first_one_based} is "
                f"outside 1..{atom_count}"
            )
        if not (1 <= second_one_based <= atom_count):
            raise MolV2000ParseError(
                f"line {line_number}: second atom index {second_one_based} is "
                f"outside 1..{atom_count}"
            )
        if first_one_based == second_one_based:
            raise MolV2000ParseError(
                f"line {line_number}: a MOL bond cannot reference one atom twice"
            )
        first = first_one_based - 1
        second = second_one_based - 1
        edge = tuple(sorted((first, second)))
        if edge in seen:
            previous_type = seen[edge]
            qualifier = "contradictory " if previous_type != bond_type else ""
            raise MolV2000ParseError(
                f"line {line_number}: {qualifier}duplicate MOL bond for "
                f"atom indexes {first_one_based} and {second_one_based}"
            )
        seen[edge] = bond_type
        first_atom = structure[edge[0]]
        second_atom = structure[edge[1]]
        distance = dist(
            (first_atom.x, first_atom.y, first_atom.z),
            (second_atom.x, second_atom.y, second_atom.z),
        )
        bonds.append(Bond(edge[0], edge[1], distance))
        display_orders.append(BondDisplayOrder(edge[0], edge[1], bond_type))

    end_markers = tuple(
        line_index
        for line_index in range(content_end, len(lines))
        if lines[line_index].strip() == "M  END"
    )
    if not end_markers:
        raise MolV2000ParseError("MOL V2000 file is missing the required M  END line")
    first_end = end_markers[0]
    if any(line.strip() for line in lines[first_end + 1 :]):
        raise MolV2000ParseError(
            "unexpected content follows M  END; SDF/multiple-record input is unsupported"
        )

    connectivity = Connectivity(atom_count, tuple(bonds))
    by_edge = {record.edge: record for record in display_orders}
    ordered_display = tuple(
        by_edge[(bond.first_index, bond.second_index)] for bond in connectivity
    )
    return MolV2000Geometry(structure, connectivity, ordered_display)


def _parse_atom_line(record: str, line_number: int, index: int) -> Atom:
    if len(record) < 34:
        raise MolV2000ParseError(
            f"line {line_number}: MOL atom record is truncated"
        )
    coordinate_texts = (record[0:10], record[10:20], record[20:30])
    try:
        x, y, z = (float(value.strip()) for value in coordinate_texts)
    except ValueError as error:
        raise MolV2000ParseError(
            f"line {line_number}: MOL atom coordinates are invalid"
        ) from error
    element = record[31:34].strip()
    if not element:
        raise MolV2000ParseError(
            f"line {line_number}: MOL atom element is missing"
        )
    try:
        return Atom(index, element, x, y, z)
    except (TypeError, ValueError) as error:
        raise MolV2000ParseError(f"line {line_number}: {error}") from error


def _fixed_integer(
    record: str,
    start: int,
    stop: int,
    line_number: int,
    label: str,
) -> int:
    if len(record) < stop:
        raise MolV2000ParseError(
            f"line {line_number}: MOL {label} field is truncated"
        )
    value = record[start:stop].strip()
    try:
        return int(value)
    except ValueError as error:
        raise MolV2000ParseError(
            f"line {line_number}: MOL {label} must be an integer"
        ) from error
