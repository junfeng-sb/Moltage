"""Strict ORCA optimization and frequency evidence parsing."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
import re

from moltage.domain.structure import Atom, MolecularStructure


class OrcaEvidenceError(ValueError):
    """Raised when a required ORCA result artifact is malformed or inconsistent."""


NORMAL_TERMINATION_MARKER = "ORCA TERMINATED NORMALLY"
OPTIMIZATION_CONVERGED_MARKER = "THE OPTIMIZATION HAS CONVERGED"
OPTIMIZATION_MAX_CYCLES_MARKERS = (
    "THE OPTIMIZATION DID NOT CONVERGE",
    "MAXIMUM NUMBER OF OPTIMIZATION CYCLES REACHED",
)
FREQUENCY_SECTION_MARKER = "VIBRATIONAL FREQUENCIES"
IMAGINARY_MODE_MARKER = "***imaginary mode***"


class OrcaFrequencyCompletion(StrEnum):
    FREQUENCY_COMPLETED = "FREQUENCY_COMPLETED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


class OrcaImaginaryModeClassification(StrEnum):
    NO_IMAGINARY_MODES_REPORTED = "NO_IMAGINARY_MODES_REPORTED"
    IMAGINARY_MODES_REPORTED = "IMAGINARY_MODES_REPORTED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class OrcaOptimizationOutputEvidence:
    normal_termination: bool
    optimization_converged: bool
    explicit_nonconvergence: bool


@dataclass(frozen=True, slots=True)
class OrcaFrequencyModeEvidence:
    mode_index: int
    frequency_cm1: float
    orca_reported_imaginary: bool


@dataclass(frozen=True, slots=True)
class OrcaFrequencyEvidence:
    completion: OrcaFrequencyCompletion
    imaginary_classification: OrcaImaginaryModeClassification
    modes: tuple[OrcaFrequencyModeEvidence, ...]
    hessian_dimension: int | None
    diagnostic: str | None = None


def parse_orca_optimization_output(output: str | bytes) -> OrcaOptimizationOutputEvidence:
    text = _text(output, "ORCA optimization output")
    upper = text.upper()
    return OrcaOptimizationOutputEvidence(
        normal_termination=NORMAL_TERMINATION_MARKER in upper,
        optimization_converged=OPTIMIZATION_CONVERGED_MARKER in upper,
        explicit_nonconvergence=any(marker in upper for marker in OPTIMIZATION_MAX_CYCLES_MARKERS),
    )


def parse_orca_final_xyz(
    xyz: str | bytes,
    submitted_structure: MolecularStructure,
) -> MolecularStructure:
    text = _text(xyz, "orca_opt.xyz")
    lines = text.splitlines()
    if len(lines) < 2:
        raise OrcaEvidenceError("orca_opt.xyz is incomplete")
    try:
        atom_count = int(lines[0].strip())
    except ValueError:
        raise OrcaEvidenceError("orca_opt.xyz atom count is invalid") from None
    if atom_count != len(submitted_structure):
        raise OrcaEvidenceError(
            f"orca_opt.xyz contains {atom_count} atoms; expected {len(submitted_structure)}"
        )
    records = [line for line in lines[2:] if line.strip()]
    if len(records) != atom_count:
        raise OrcaEvidenceError("orca_opt.xyz must contain exactly one complete XYZ frame")
    atoms: list[Atom] = []
    for index, (line, submitted) in enumerate(zip(records, submitted_structure, strict=True)):
        fields = line.split()
        if len(fields) != 4:
            raise OrcaEvidenceError(f"orca_opt.xyz atom {index + 1} is malformed")
        element = fields[0][0].upper() + fields[0][1:].lower()
        if element != submitted.element:
            raise OrcaEvidenceError(
                f"orca_opt.xyz atom {index + 1} is {element}; expected {submitted.element}"
            )
        try:
            coordinates = tuple(float(value) for value in fields[1:])
        except ValueError:
            raise OrcaEvidenceError(f"orca_opt.xyz atom {index + 1} has invalid coordinates") from None
        if not all(isfinite(value) for value in coordinates):
            raise OrcaEvidenceError(f"orca_opt.xyz atom {index + 1} has non-finite coordinates")
        atoms.append(Atom(index, element, *coordinates))
    return MolecularStructure(tuple(atoms), comment=lines[1])


_FREQUENCY_LINE = re.compile(
    r"^\s*(?P<index>[0-9]+)\s*[:)]\s*"
    r"(?P<value>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?)\s*"
    r"cm(?:\*\*)?\s*-\s*1(?P<suffix>.*)$",
    re.IGNORECASE,
)


def parse_orca_frequency_evidence(
    output: str | bytes,
    hessian: str | bytes | None,
    *,
    atom_count: int,
    scheduler_succeeded: bool = True,
) -> OrcaFrequencyEvidence:
    text = _text(output, "ORCA frequency output")
    if not scheduler_succeeded:
        return OrcaFrequencyEvidence(
            OrcaFrequencyCompletion.FAILED,
            OrcaImaginaryModeClassification.UNVERIFIED,
            (),
            None,
            "Scheduler did not report successful completion",
        )
    upper = text.upper()
    if NORMAL_TERMINATION_MARKER not in upper:
        return _unverified("ORCA normal termination was not confirmed")
    if FREQUENCY_SECTION_MARKER not in upper:
        return _unverified("VIBRATIONAL FREQUENCIES section was not found")
    modes = _parse_output_modes(text)
    if not modes:
        return _unverified("No parseable vibrational frequencies were found")
    if hessian is None:
        return _unverified("orca_freq.hess is missing", modes=modes)
    try:
        dimension, hessian_mode_count = parse_orca_hessian(hessian)
    except OrcaEvidenceError as error:
        return _unverified(str(error), modes=modes)
    expected_dimension = 3 * atom_count
    if dimension != expected_dimension:
        return _unverified(
            f"Hessian dimension is {dimension}; expected 3N = {expected_dimension}",
            modes=modes,
            dimension=dimension,
        )
    if hessian_mode_count != len(modes):
        return _unverified(
            f"Frequency mode count differs between output ({len(modes)}) and Hessian ({hessian_mode_count})",
            modes=modes,
            dimension=dimension,
        )
    classification = (
        OrcaImaginaryModeClassification.IMAGINARY_MODES_REPORTED
        if any(mode.orca_reported_imaginary for mode in modes)
        else OrcaImaginaryModeClassification.NO_IMAGINARY_MODES_REPORTED
    )
    return OrcaFrequencyEvidence(
        OrcaFrequencyCompletion.FREQUENCY_COMPLETED,
        classification,
        modes,
        dimension,
    )


def parse_orca_hessian(hessian: str | bytes) -> tuple[int, int]:
    text = _text(hessian, "orca_freq.hess")
    lines = text.splitlines()
    dimension = _section_count(lines, "$hessian", "Hessian dimension")
    mode_count = _section_count(lines, "$vibrational_frequencies", "frequency mode count")
    if dimension <= 0 or mode_count <= 0:
        raise OrcaEvidenceError("orca_freq.hess dimensions must be positive")
    hessian_start = _section_index(lines, "$hessian") + 2
    end = next((i for i in range(hessian_start, len(lines)) if lines[i].lstrip().startswith("$")), len(lines))
    numeric_values = []
    for line in lines[hessian_start:end]:
        fields = line.split()
        for field in fields:
            try:
                numeric_values.append(float(field))
            except ValueError:
                continue
    if not numeric_values:
        raise OrcaEvidenceError("orca_freq.hess contains no parseable Hessian values")
    return dimension, mode_count


def _parse_output_modes(text: str) -> tuple[OrcaFrequencyModeEvidence, ...]:
    section = text.upper().find(FREQUENCY_SECTION_MARKER)
    modes = []
    for line in text[section:].splitlines()[1:]:
        match = _FREQUENCY_LINE.match(line)
        if match is None:
            continue
        modes.append(
            OrcaFrequencyModeEvidence(
                int(match.group("index")),
                float(match.group("value")),
                IMAGINARY_MODE_MARKER in match.group("suffix").lower(),
            )
        )
    return tuple(modes)


def _section_count(lines: list[str], marker: str, label: str) -> int:
    index = _section_index(lines, marker)
    try:
        return int(lines[index + 1].strip())
    except (IndexError, ValueError):
        raise OrcaEvidenceError(f"orca_freq.hess {label} is invalid") from None


def _section_index(lines: list[str], marker: str) -> int:
    for index, line in enumerate(lines):
        if line.strip().lower() == marker:
            return index
    raise OrcaEvidenceError(f"orca_freq.hess is missing {marker}")


def _unverified(
    diagnostic: str,
    *,
    modes: tuple[OrcaFrequencyModeEvidence, ...] = (),
    dimension: int | None = None,
) -> OrcaFrequencyEvidence:
    return OrcaFrequencyEvidence(
        OrcaFrequencyCompletion.UNVERIFIED,
        OrcaImaginaryModeClassification.UNVERIFIED,
        modes,
        dimension,
        diagnostic,
    )


def _text(value: str | bytes, label: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            raise OrcaEvidenceError(f"{label} is not valid UTF-8") from None
    if not isinstance(value, str) or not value.strip():
        raise OrcaEvidenceError(f"{label} is empty")
    return value
