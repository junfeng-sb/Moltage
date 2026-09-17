"""Strict reader for the four-column XYZ format."""

from os import PathLike
from pathlib import Path

from moltage.domain.structure import Atom, MolecularStructure


class XYZParseError(ValueError):
    """Raised when required XYZ content is malformed."""


def read_xyz(path: str | PathLike[str]) -> MolecularStructure:
    """Read a UTF-8 XYZ file and return its molecular structure."""

    return parse_xyz(Path(path).read_text(encoding="utf-8"))


def parse_xyz(text: str) -> MolecularStructure:
    """Parse atom count, comment, symbols, and coordinates from XYZ text."""

    lines = text.splitlines()
    if not lines:
        raise XYZParseError("line 1: missing atom count")

    atom_count_text = lines[0].strip()
    try:
        atom_count = int(atom_count_text)
    except ValueError as error:
        raise XYZParseError(
            f"line 1: atom count must be an integer, found {atom_count_text!r}"
        ) from error

    if atom_count < 0:
        raise XYZParseError(
            f"line 1: atom count must be non-negative, found {atom_count}"
        )
    if len(lines) < 2:
        raise XYZParseError("line 2: missing XYZ comment line")

    atom_records = lines[2:]
    if len(atom_records) != atom_count:
        raise XYZParseError(
            f"declared {atom_count} atoms but found {len(atom_records)} atom records"
        )

    atoms = tuple(
        _parse_atom_record(record, index + 3, index)
        for index, record in enumerate(atom_records)
    )
    return MolecularStructure(atoms=atoms, comment=lines[1])


def _parse_atom_record(record: str, line_number: int, index: int) -> Atom:
    fields = record.split()
    if len(fields) != 4:
        raise XYZParseError(
            f"line {line_number}: atom record must contain "
            "an element and exactly three coordinates"
        )

    element, x_text, y_text, z_text = fields
    try:
        x, y, z = (float(value) for value in (x_text, y_text, z_text))
    except ValueError as error:
        raise XYZParseError(
            f"line {line_number}: coordinates must be floating-point numbers"
        ) from error

    try:
        return Atom(index=index, element=element, x=x, y=y, z=z)
    except (TypeError, ValueError) as error:
        raise XYZParseError(f"line {line_number}: {error}") from error
