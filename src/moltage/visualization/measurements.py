"""Session-local manual molecular measurements with no structure mutation."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import acos, degrees, dist, hypot, isfinite

from moltage.domain.structure import MolecularStructure


class MeasurementError(ValueError):
    """Raised when a manual measurement cannot be defined."""


@dataclass(frozen=True, slots=True)
class DistanceMeasurement:
    """One immutable interatomic distance in explicit Cartesian coordinates."""

    measurement_id: int
    atom_a: int
    atom_b: int
    value_angstrom: float

    def __post_init__(self) -> None:
        _validate_measurement_id(self.measurement_id)
        _validate_atom_index(self.atom_a, "distance atom A")
        _validate_atom_index(self.atom_b, "distance atom B")
        if self.atom_a == self.atom_b:
            raise MeasurementError("distance atoms must be distinct")
        value = _finite_float(self.value_angstrom, "distance")
        if value <= 1.0e-12:
            raise MeasurementError("distance must have non-zero length")
        object.__setattr__(self, "value_angstrom", value)


@dataclass(frozen=True, slots=True)
class AngleMeasurement:
    """One immutable A-B-C angle, with atom B as the vertex."""

    measurement_id: int
    atom_a: int
    atom_b: int
    atom_c: int
    value_degrees: float

    def __post_init__(self) -> None:
        _validate_measurement_id(self.measurement_id)
        for name in ("atom_a", "atom_b", "atom_c"):
            _validate_atom_index(getattr(self, name), f"angle {name}")
        if len({self.atom_a, self.atom_b, self.atom_c}) != 3:
            raise MeasurementError("angle atoms A, B, and C must be distinct")
        value = _finite_float(self.value_degrees, "angle")
        if not 0.0 <= value <= 180.0:
            raise MeasurementError("angle must be between 0 and 180 degrees")
        object.__setattr__(self, "value_degrees", value)


MeasurementRecord = DistanceMeasurement | AngleMeasurement


@dataclass(frozen=True, slots=True)
class MeasurementSessionSnapshot:
    """Exact restorable measurement records and monotonic ID allocation."""

    measurements: tuple[MeasurementRecord, ...]
    next_id: int

    def __post_init__(self) -> None:
        measurements = tuple(self.measurements)
        if any(
            not isinstance(item, (DistanceMeasurement, AngleMeasurement))
            for item in measurements
        ):
            raise TypeError("measurement snapshot contains an invalid record")
        identifiers = tuple(item.measurement_id for item in measurements)
        if len(set(identifiers)) != len(identifiers):
            raise MeasurementError("measurement snapshot IDs must be unique")
        _validate_measurement_id(self.next_id)
        if identifiers and self.next_id <= max(identifiers):
            raise MeasurementError(
                "measurement snapshot next ID must exceed every existing ID"
            )
        object.__setattr__(self, "measurements", measurements)


class MeasurementSession:
    """Own creation-ordered records and stable IDs for one viewer session."""

    __slots__ = ("_measurements", "_next_id")

    def __init__(self) -> None:
        self._measurements: list[MeasurementRecord] = []
        self._next_id = 1

    @property
    def measurements(self) -> tuple[MeasurementRecord, ...]:
        return tuple(self._measurements)

    def __len__(self) -> int:
        return len(self._measurements)

    def add_distance(
        self,
        structure: MolecularStructure,
        atom_a: int,
        atom_b: int,
    ) -> DistanceMeasurement:
        """Measure any two distinct atoms without consulting connectivity."""

        first = _atom_coordinates(structure, atom_a, "distance atom A")
        second = _atom_coordinates(structure, atom_b, "distance atom B")
        measurement = DistanceMeasurement(
            self._next_id,
            atom_a,
            atom_b,
            dist(first, second),
        )
        self._measurements.append(measurement)
        self._next_id += 1
        return measurement

    def add_angle(
        self,
        structure: MolecularStructure,
        atom_a: int,
        atom_b: int,
        atom_c: int,
    ) -> AngleMeasurement:
        """Measure A-B-C using atom B as the vertex."""

        first = _atom_coordinates(structure, atom_a, "angle atom A")
        vertex = _atom_coordinates(structure, atom_b, "angle atom B")
        third = _atom_coordinates(structure, atom_c, "angle atom C")
        if len({atom_a, atom_b, atom_c}) != 3:
            raise MeasurementError("angle atoms A, B, and C must be distinct")
        ba = _subtract(first, vertex)
        bc = _subtract(third, vertex)
        denominator = hypot(*ba) * hypot(*bc)
        if not isfinite(denominator) or denominator <= 1.0e-12:
            raise MeasurementError("angle rays must have non-zero finite length")
        cosine = _dot(ba, bc) / denominator
        measurement = AngleMeasurement(
            self._next_id,
            atom_a,
            atom_b,
            atom_c,
            angle_degrees_from_cosine(cosine),
        )
        self._measurements.append(measurement)
        self._next_id += 1
        return measurement

    def delete(self, measurement_id: int) -> MeasurementRecord:
        """Delete exactly one record by stable ID and return it."""

        _validate_measurement_id(measurement_id)
        for index, measurement in enumerate(self._measurements):
            if measurement.measurement_id == measurement_id:
                return self._measurements.pop(index)
        raise MeasurementError(f"unknown measurement ID: {measurement_id}")

    def clear(self) -> None:
        """Discard records while retaining monotonic session ID allocation."""

        self._measurements.clear()

    def snapshot(self) -> MeasurementSessionSnapshot:
        """Capture exact records for one workspace-local Undo state."""

        return MeasurementSessionSnapshot(self.measurements, self._next_id)

    def restore(self, snapshot: MeasurementSessionSnapshot) -> None:
        """Restore exact records and stable-ID allocation from one snapshot."""

        if not isinstance(snapshot, MeasurementSessionSnapshot):
            raise TypeError("measurement restore requires a session snapshot")
        self._measurements = list(snapshot.measurements)
        self._next_id = snapshot.next_id

    def remap_atom_indices(
        self,
        old_to_new_indices: Sequence[int | None],
    ) -> tuple[MeasurementRecord, ...]:
        """Drop records touching removed atoms and remap all surviving records."""

        mapping = tuple(old_to_new_indices)
        for value in mapping:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("measurement atom mapping values must be integers or None")
            if value < 0:
                raise MeasurementError(
                    "measurement atom mapping values must be non-negative"
                )

        removed: list[MeasurementRecord] = []
        remapped: list[MeasurementRecord] = []
        for measurement in self._measurements:
            atom_indices = _measurement_atom_indices(measurement)
            if any(atom_index >= len(mapping) for atom_index in atom_indices):
                raise MeasurementError(
                    "measurement atom index is outside the supplied atom mapping"
                )
            mapped = tuple(mapping[atom_index] for atom_index in atom_indices)
            if any(atom_index is None for atom_index in mapped):
                removed.append(measurement)
                continue
            if isinstance(measurement, DistanceMeasurement):
                remapped.append(
                    DistanceMeasurement(
                        measurement.measurement_id,
                        mapped[0],
                        mapped[1],
                        measurement.value_angstrom,
                    )
                )
            else:
                remapped.append(
                    AngleMeasurement(
                        measurement.measurement_id,
                        mapped[0],
                        mapped[1],
                        mapped[2],
                        measurement.value_degrees,
                    )
                )
        self._measurements = remapped
        return tuple(removed)


def angle_degrees_from_cosine(cosine: float) -> float:
    """Clamp a finite computed cosine before converting it to degrees."""

    numeric = _finite_float(cosine, "angle cosine")
    return degrees(acos(max(-1.0, min(1.0, numeric))))


def _measurement_atom_indices(
    measurement: MeasurementRecord,
) -> tuple[int, ...]:
    if isinstance(measurement, DistanceMeasurement):
        return measurement.atom_a, measurement.atom_b
    if isinstance(measurement, AngleMeasurement):
        return measurement.atom_a, measurement.atom_b, measurement.atom_c
    raise TypeError("unsupported measurement record")


def _atom_coordinates(
    structure: MolecularStructure,
    atom_index: int,
    label: str,
) -> tuple[float, float, float]:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("measurements require a MolecularStructure")
    _validate_atom_index(atom_index, label)
    if atom_index >= len(structure):
        raise MeasurementError(
            f"{label} index is outside the current structure: "
            f"{atom_index} >= {len(structure)}"
        )
    atom = structure[atom_index]
    return atom.x, atom.y, atom.z


def _validate_measurement_id(measurement_id: object) -> None:
    if isinstance(measurement_id, bool) or not isinstance(measurement_id, int):
        raise TypeError("measurement ID must be an integer")
    if measurement_id <= 0:
        raise MeasurementError("measurement ID must be positive")


def _validate_atom_index(atom_index: object, label: str) -> None:
    if isinstance(atom_index, bool) or not isinstance(atom_index, int):
        raise TypeError(f"{label} index must be an integer")
    if atom_index < 0:
        raise MeasurementError(f"{label} index must be non-negative")


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise MeasurementError(f"{label} must be finite")
    return numeric


def _subtract(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        first_component - second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(
        first_component * second_component
        for first_component, second_component in zip(first, second, strict=True)
    )
