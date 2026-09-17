"""Strict single-dataset Gaussian Cube input for read-only visualization."""

from array import array
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from os import PathLike
from pathlib import Path
from typing import TextIO

from moltage.aims.species_library import element_for_atomic_number
from moltage.domain.structure import Atom, MolecularStructure


BOHR_TO_ANGSTROM = 0.529177210903


class CubeFormatError(ValueError):
    """Raised when a Cube file is outside the supported MVP subset."""


class CubeCoordinateUnit(StrEnum):
    """Coordinate units stored in a Cube header."""

    BOHR = "bohr"
    ANGSTROM = "angstrom"


class CubeSourceKind(StrEnum):
    """Producer evidence available from the two free-form comment rows."""

    ORCA = "ORCA"
    FHI_AIMS = "FHI-aims"
    UNKNOWN = "Unknown"


@dataclass(frozen=True, slots=True)
class CubeUnitRecommendation:
    """A bounded coordinate-unit recommendation derived from header evidence."""

    source_kind: CubeSourceKind
    coordinate_unit: CubeCoordinateUnit
    requires_confirmation: bool


@dataclass(frozen=True, slots=True)
class CubeScalarField:
    """One scalar field sampled on a possibly non-orthogonal 3D grid."""

    dimensions: tuple[int, int, int]
    origin_angstrom: tuple[float, float, float]
    axis_vectors_angstrom: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]
    values: array
    dataset_id: int | None = None
    source_coordinate_unit: CubeCoordinateUnit = CubeCoordinateUnit.BOHR
    source_kind: CubeSourceKind = CubeSourceKind.UNKNOWN
    _minimum: float = field(init=False, repr=False)
    _maximum: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        dimensions = tuple(self.dimensions)
        if len(dimensions) != 3:
            raise ValueError("Cube grid dimensions must contain x, y, and z")
        for dimension in dimensions:
            if isinstance(dimension, bool) or not isinstance(dimension, int):
                raise TypeError("Cube grid dimensions must be integers")
            if dimension <= 0:
                raise ValueError("Cube grid dimensions must be positive")

        origin = _validated_vector(self.origin_angstrom, "Cube grid origin")
        axes = tuple(
            _validated_vector(vector, f"Cube grid axis {index + 1}")
            for index, vector in enumerate(self.axis_vectors_angstrom)
        )
        if len(axes) != 3:
            raise ValueError("Cube grid must contain exactly three axis vectors")
        axis_scale = _vector_length(axes[0]) * _vector_length(
            axes[1]
        ) * _vector_length(axes[2])
        determinant = _dot(axes[0], _cross(axes[1], axes[2]))
        if axis_scale == 0.0 or abs(determinant) <= 1.0e-12 * axis_scale:
            raise ValueError("Cube grid axis vectors must be non-zero and non-coplanar")

        values = self.values
        if isinstance(values, array) and values.typecode == "d":
            value_buffer = values
        else:
            value_buffer = array("d")
            try:
                iterator = iter(values)
            except TypeError as error:
                raise TypeError("Cube scalar values must be iterable") from error
            for value in iterator:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError("Cube scalar values must be numeric")
                numeric = float(value)
                if not isfinite(numeric):
                    raise ValueError("Cube scalar values must be finite")
                value_buffer.append(numeric)

        expected_count = dimensions[0] * dimensions[1] * dimensions[2]
        if len(value_buffer) != expected_count:
            raise ValueError(
                "Cube scalar count does not match grid dimensions: "
                f"expected {expected_count}, found {len(value_buffer)}"
            )
        minimum = float("inf")
        maximum = float("-inf")
        for value in value_buffer:
            if not isfinite(value):
                raise ValueError("Cube scalar values must be finite")
            minimum = min(minimum, value)
            maximum = max(maximum, value)

        if self.dataset_id is not None and (
            isinstance(self.dataset_id, bool)
            or not isinstance(self.dataset_id, int)
        ):
            raise TypeError("Cube dataset identifier must be an integer")
        if not isinstance(self.source_coordinate_unit, CubeCoordinateUnit):
            raise TypeError("Cube source coordinate unit is invalid")
        if not isinstance(self.source_kind, CubeSourceKind):
            raise TypeError("Cube source kind is invalid")

        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "origin_angstrom", origin)
        object.__setattr__(self, "axis_vectors_angstrom", axes)
        object.__setattr__(self, "values", value_buffer)
        object.__setattr__(self, "_minimum", minimum)
        object.__setattr__(self, "_maximum", maximum)

    @property
    def minimum(self) -> float:
        return self._minimum

    @property
    def maximum(self) -> float:
        return self._maximum

    @property
    def maximum_absolute_value(self) -> float:
        return max(abs(self.minimum), abs(self.maximum))


@dataclass(frozen=True, slots=True)
class ParsedCube:
    """The atom geometry and scalar grid carried by one Cube file."""

    structure: MolecularStructure
    scalar_field: CubeScalarField


class _CubeLineReader:
    """Track source line numbers while retaining only the current line."""

    def __init__(self, handle: TextIO, source_path: Path) -> None:
        self._handle = handle
        self.source_path = source_path
        self.line_number = 0

    def next_line(self, truncated_part: str) -> str:
        line = self._handle.readline()
        if line == "":
            raise CubeFormatError(
                f"{self.source_path.name}: Cube file is truncated {truncated_part}"
            )
        self.line_number += 1
        return line.rstrip("\r\n")

    def remaining_chunks(self, size: int = 1024 * 1024):
        while chunk := self._handle.read(size):
            yield chunk


def recommend_cube_coordinate_unit(
    path: str | PathLike[str],
) -> CubeUnitRecommendation:
    """Inspect only Cube comment rows and recommend a coordinate convention."""

    source_path = Path(path)
    with source_path.open("r", encoding="utf-8-sig", newline=None) as handle:
        first = handle.readline()
        second = handle.readline()
    if first == "" or second == "":
        raise CubeFormatError(
            f"{source_path.name}: Cube file is truncated before its comment header"
        )
    comments = f"{first}\n{second}".casefold()
    if "fhi-aims" in comments or "fhi aims" in comments:
        return CubeUnitRecommendation(
            CubeSourceKind.FHI_AIMS,
            CubeCoordinateUnit.ANGSTROM,
            True,
        )
    if "orca" in comments:
        return CubeUnitRecommendation(
            CubeSourceKind.ORCA,
            CubeCoordinateUnit.BOHR,
            False,
        )
    return CubeUnitRecommendation(
        CubeSourceKind.UNKNOWN,
        CubeCoordinateUnit.BOHR,
        True,
    )


def read_cube(
    path: str | PathLike[str],
    *,
    coordinate_unit: CubeCoordinateUnit | None = None,
) -> ParsedCube:
    """Read one scalar Cube dataset, including single-orbital Cube records."""

    source_path = Path(path)
    recommendation = recommend_cube_coordinate_unit(source_path)
    selected_unit = (
        recommendation.coordinate_unit
        if coordinate_unit is None
        else coordinate_unit
    )
    if not isinstance(selected_unit, CubeCoordinateUnit):
        raise TypeError("Cube coordinate unit must be a CubeCoordinateUnit")
    coordinate_scale = (
        BOHR_TO_ANGSTROM
        if selected_unit is CubeCoordinateUnit.BOHR
        else 1.0
    )

    with source_path.open("r", encoding="utf-8-sig", newline=None) as handle:
        reader = _CubeLineReader(handle, source_path)
        comment_lines = (
            reader.next_line("before its first comment row"),
            reader.next_line("before its second comment row"),
        )
        origin_line = reader.next_line("before its atom/origin row")
        origin_fields = origin_line.split()
        if len(origin_fields) not in {4, 5}:
            raise CubeFormatError(
                f"{source_path.name}:3: Cube atom/origin row must contain "
                "4 or 5 fields"
            )
        signed_atom_count = _parse_integer(
            origin_fields[0], source_path, 3, "atom count"
        )
        atom_count = abs(signed_atom_count)
        if atom_count == 0:
            raise CubeFormatError(
                f"{source_path.name}: Cube visualization requires at least one atom"
            )
        if len(origin_fields) == 5:
            values_per_voxel = _parse_integer(
                origin_fields[4], source_path, 3, "values-per-voxel count"
            )
            if values_per_voxel != 1:
                raise CubeFormatError(
                    f"{source_path.name}: only one Cube scalar value per voxel "
                    "is supported"
                )
        origin = tuple(
            _parse_finite_float(token, source_path, 3, "origin coordinate")
            * coordinate_scale
            for token in origin_fields[1:4]
        )

        dimensions: list[int] = []
        axes: list[tuple[float, float, float]] = []
        for axis_index in range(3):
            line_number = 4 + axis_index
            fields = reader.next_line("in its grid header").split()
            if len(fields) != 4:
                raise CubeFormatError(
                    f"{source_path.name}:{line_number}: Cube grid-axis row must "
                    "contain exactly 4 fields"
                )
            point_count = _parse_integer(
                fields[0], source_path, line_number, "grid point count"
            )
            if point_count <= 0:
                raise CubeFormatError(
                    f"{source_path.name}:{line_number}: only positive Cube grid "
                    "counts are supported"
                )
            dimensions.append(point_count)
            axes.append(
                tuple(
                    _parse_finite_float(
                        token,
                        source_path,
                        line_number,
                        "grid-axis coordinate",
                    )
                    * coordinate_scale
                    for token in fields[1:4]
                )
            )

        atoms: list[Atom] = []
        for atom_index in range(atom_count):
            line = reader.next_line("in its atom rows")
            line_number = reader.line_number
            fields = line.split()
            if len(fields) != 5:
                raise CubeFormatError(
                    f"{source_path.name}:{line_number}: Cube atom row must contain "
                    "exactly 5 fields"
                )
            atomic_number = _parse_integer(
                fields[0], source_path, line_number, "atomic number"
            )
            try:
                element = element_for_atomic_number(atomic_number)
            except ValueError as error:
                raise CubeFormatError(
                    f"{source_path.name}:{line_number}: unsupported atomic number "
                    f"{atomic_number}"
                ) from error
            _parse_finite_float(
                fields[1], source_path, line_number, "nuclear charge"
            )
            coordinates = tuple(
                _parse_finite_float(
                    token,
                    source_path,
                    line_number,
                    "atom coordinate",
                )
                * coordinate_scale
                for token in fields[2:5]
            )
            atoms.append(Atom(atom_index, element, *coordinates))

        dataset_id = None
        if signed_atom_count < 0:
            dataset_id = _read_single_dataset_id(reader)

        expected_scalar_count = dimensions[0] * dimensions[1] * dimensions[2]
        values = _read_scalar_values(reader, expected_scalar_count)

    try:
        scalar_field = CubeScalarField(
            dimensions=tuple(dimensions),
            origin_angstrom=origin,
            axis_vectors_angstrom=tuple(axes),
            values=values,
            dataset_id=dataset_id,
            source_coordinate_unit=selected_unit,
            source_kind=recommendation.source_kind,
        )
    except (TypeError, ValueError) as error:
        raise CubeFormatError(f"{source_path.name}: {error}") from error
    return ParsedCube(
        MolecularStructure(
            tuple(atoms),
            comment="\n".join(comment_lines),
        ),
        scalar_field,
    )


def _read_single_dataset_id(reader: _CubeLineReader) -> int:
    source_path = reader.source_path
    first_line = reader.next_line("before its orbital dataset record")
    first_line_number = reader.line_number
    fields = first_line.split()
    if not fields:
        raise CubeFormatError(
            f"{source_path.name}:{first_line_number}: Cube orbital dataset "
            "record is empty"
        )
    dataset_count = _parse_integer(
        fields[0], source_path, first_line_number, "orbital dataset count"
    )
    if dataset_count <= 0:
        raise CubeFormatError(
            f"{source_path.name}:{first_line_number}: Cube orbital dataset count "
            "must be positive"
        )

    identifiers: list[int] = []
    pending_fields = fields[1:]
    while True:
        if len(identifiers) + len(pending_fields) > dataset_count:
            raise CubeFormatError(
                f"{source_path.name}:{reader.line_number}: Cube orbital dataset "
                "record contains trailing fields"
            )
        identifiers.extend(
            _parse_integer(
                token,
                source_path,
                reader.line_number,
                "orbital dataset identifier",
            )
            for token in pending_fields
        )
        if len(identifiers) == dataset_count:
            break
        pending_fields = reader.next_line(
            "in its orbital dataset record"
        ).split()
        if not pending_fields:
            raise CubeFormatError(
                f"{source_path.name}:{reader.line_number}: Cube orbital dataset "
                "record is empty"
            )

    if dataset_count != 1:
        rendered_ids = ", ".join(str(identifier) for identifier in identifiers)
        raise CubeFormatError(
            f"{source_path.name}: Cube contains {dataset_count} scalar datasets "
            f"({rendered_ids}); dataset selection is not supported"
        )
    return identifiers[0]


def _read_scalar_values(
    reader: _CubeLineReader,
    expected_count: int,
) -> array:
    """Parse scalar text in bounded chunks into one contiguous float64 buffer."""

    values = array("d")
    carry = ""
    whitespace = " \t\r\n\v\f"
    for chunk in reader.remaining_chunks():
        text = carry + chunk
        if text and not text[-1].isspace():
            boundary = max(text.rfind(character) for character in whitespace)
            if boundary < 0:
                carry = text
                continue
            complete = text[: boundary + 1]
            carry = text[boundary + 1 :]
        else:
            complete = text
            carry = ""
        _extend_scalar_values(
            values,
            complete,
            reader.source_path,
            expected_count,
        )
    if carry:
        _extend_scalar_values(
            values,
            carry,
            reader.source_path,
            expected_count,
        )
    if len(values) != expected_count:
        raise CubeFormatError(
            f"{reader.source_path.name}: Cube scalar count does not match grid: "
            f"expected {expected_count}, found {len(values)}"
        )
    return values


def _extend_scalar_values(
    destination: array,
    text: str,
    source_path: Path,
    expected_count: int,
) -> None:
    normalized = text.replace("D", "E").replace("d", "e")
    tokens = normalized.split()
    if not tokens:
        return
    try:
        parsed = array("d", map(float, tokens))
    except ValueError as error:
        for offset, token in enumerate(tokens):
            try:
                float(token)
            except ValueError as token_error:
                raise CubeFormatError(
                    f"{source_path.name}: invalid Cube scalar value {token!r} "
                    f"at index {len(destination) + offset}"
                ) from token_error
        raise CubeFormatError(
            f"{source_path.name}: invalid Cube scalar data"
        ) from error
    if len(destination) + len(parsed) > expected_count:
        raise CubeFormatError(
            f"{source_path.name}: Cube scalar count does not match grid: "
            f"expected {expected_count}, found more"
        )
    destination.extend(parsed)


def _parse_integer(
    token: str,
    path: Path,
    line_number: int,
    label: str,
) -> int:
    try:
        return int(token)
    except ValueError as error:
        raise CubeFormatError(
            f"{path.name}:{line_number}: invalid Cube {label} {token!r}"
        ) from error


def _parse_finite_float(
    token: str,
    path: Path,
    line_number: int | None,
    label: str,
) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as error:
        location = path.name if line_number is None else f"{path.name}:{line_number}"
        raise CubeFormatError(
            f"{location}: invalid Cube {label} {token!r}"
        ) from error
    if not isfinite(value):
        location = path.name if line_number is None else f"{path.name}:{line_number}"
        raise CubeFormatError(f"{location}: Cube {label} must be finite")
    return value


def _validated_vector(
    value: object,
    label: str,
) -> tuple[float, float, float]:
    try:
        vector = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError(f"{label} must contain three coordinates") from error
    if len(vector) != 3:
        raise ValueError(f"{label} must contain three coordinates")
    result: list[float] = []
    for component in vector:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise TypeError(f"{label} coordinates must be numeric")
        numeric = float(component)
        if not isfinite(numeric):
            raise ValueError(f"{label} coordinates must be finite")
        result.append(numeric)
    return tuple(result)  # type: ignore[return-value]


def _vector_length(vector: tuple[float, float, float]) -> float:
    return sum(component * component for component in vector) ** 0.5


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
