"""Strict recovery of molecular FHI-aims optimization evidence."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
import re

from moltage.aims.species_library import (
    SpeciesLibraryError,
    element_for_atomic_number,
)
from moltage.domain.structure import Atom, MolecularStructure


NORMAL_TERMINATION_MARKER = "Have a nice day."
GEOMETRY_CONVERGENCE_MARKER = "Present geometry is converged."
OUTPUT_TAIL_MAX_BYTES = 256 * 1024


def _direct_element_species() -> dict[str, str]:
    mapping: dict[str, str] = {}
    atomic_number = 1
    while True:
        try:
            element = element_for_atomic_number(atomic_number)
        except SpeciesLibraryError:
            return mapping
        mapping[element] = element
        atomic_number += 1


_DIRECT_ELEMENT_SPECIES = _direct_element_species()


class AimsRecoveryError(ValueError):
    """Raised when submitted or optimized FHI-aims evidence is ambiguous."""


class AimsOutputAssessmentKind(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NORMAL_TERMINATION_MISSING = "NORMAL_TERMINATION_MISSING"


@dataclass(frozen=True, slots=True)
class AimsOutputAssessment:
    kind: AimsOutputAssessmentKind
    message: str | None = None
    geometry_convergence_detected: bool = False

    @property
    def succeeded(self) -> bool:
        return self.kind is AimsOutputAssessmentKind.SUCCEEDED


def assess_geometry_optimization_output(
    output_tail: str | bytes,
) -> AimsOutputAssessment:
    """Require normal termination and retain convergence-marker information."""

    text = _decode_text(output_tail, "FHI-aims output tail")
    geometry_convergence_detected = GEOMETRY_CONVERGENCE_MARKER in text
    if NORMAL_TERMINATION_MARKER not in text:
        return AimsOutputAssessment(
            AimsOutputAssessmentKind.NORMAL_TERMINATION_MISSING,
            "FHI-aims normal termination was not confirmed.",
            geometry_convergence_detected,
        )
    return AimsOutputAssessment(
        AimsOutputAssessmentKind.SUCCEEDED,
        geometry_convergence_detected=geometry_convergence_detected,
    )


def parse_control_species_elements(
    control_text: str | bytes,
) -> dict[str, str]:
    """Map generated species declarations to elements through their nucleus Z."""

    text = _decode_text(control_text, "control.in")
    blocks: list[tuple[str, list[int]]] = []
    current_name: str | None = None
    current_nuclei: list[int] = []

    def finish_block() -> None:
        nonlocal current_name, current_nuclei
        if current_name is None:
            return
        if len(current_nuclei) != 1:
            raise AimsRecoveryError(
                f"control.in species {current_name!r} must contain exactly one "
                "usable nucleus value"
            )
        blocks.append((current_name, list(current_nuclei)))

    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        species_match = _SPECIES_DECLARATION.fullmatch(line)
        if species_match is not None:
            finish_block()
            current_name = species_match.group("name")
            current_nuclei = []
            continue
        if stripped.startswith("species"):
            raise AimsRecoveryError(
                "control.in contains a malformed active species declaration"
            )
        nucleus_match = _NUCLEUS_DECLARATION.fullmatch(line)
        if nucleus_match is not None:
            if current_name is None:
                raise AimsRecoveryError(
                    "control.in contains nucleus outside a species block"
                )
            current_nuclei.append(int(nucleus_match.group("atomic_number")))
            continue
        if stripped.startswith("nucleus"):
            raise AimsRecoveryError(
                "control.in contains a malformed active nucleus declaration"
            )
    finish_block()

    if not blocks:
        raise AimsRecoveryError("control.in contains no active species blocks")
    mapping: dict[str, str] = {}
    for species_name, nuclei in blocks:
        try:
            element = element_for_atomic_number(nuclei[0])
        except SpeciesLibraryError as error:
            raise AimsRecoveryError(
                f"control.in species {species_name!r} has unsupported nucleus "
                f"{nuclei[0]}: {error}"
            ) from None
        previous = mapping.get(species_name)
        if previous is not None:
            if previous != element:
                raise AimsRecoveryError(
                    f"control.in species alias {species_name!r} maps to "
                    "conflicting elements"
                )
            raise AimsRecoveryError(
                f"control.in contains duplicate species alias {species_name!r}"
            )
        mapping[species_name] = element
    return mapping


def parse_molecular_geometry(
    geometry_text: str | bytes,
    species_elements: dict[str, str],
    *,
    source_name: str = "geometry.in",
) -> MolecularStructure:
    """Parse only the accepted atom/annotation molecular geometry subset."""

    if not isinstance(species_elements, dict) or not species_elements:
        raise AimsRecoveryError("geometry recovery requires a species mapping")
    text = _decode_text(geometry_text, source_name)
    atoms: list[Atom] = []
    last_record_was_atom_or_annotation = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        fields = stripped.split()
        directive = fields[0]
        if directive == "trust_radius" and atoms:
            break
        if directive == "atom":
            if len(fields) != 5:
                raise AimsRecoveryError(
                    f"{source_name}:{line_number} atom requires exactly three "
                    "coordinates and one species name"
                )
            coordinates = tuple(
                _finite_float(value, source_name, line_number)
                for value in fields[1:4]
            )
            species_name = fields[4]
            if _SPECIES_NAME.fullmatch(species_name) is None:
                raise AimsRecoveryError(
                    f"{source_name}:{line_number} species name is malformed"
                )
            try:
                element = species_elements[species_name]
            except KeyError:
                raise AimsRecoveryError(
                    f"{source_name}:{line_number} species {species_name!r} is "
                    "not established by control.in"
                ) from None
            atoms.append(Atom(len(atoms), element, *coordinates))
            last_record_was_atom_or_annotation = True
            continue
        if directive in {"initial_moment", "initial_charge"}:
            if (
                not atoms
                or not last_record_was_atom_or_annotation
                or len(fields) != 2
            ):
                raise AimsRecoveryError(
                    f"{source_name}:{line_number} contains a misplaced or "
                    f"malformed {directive} annotation"
                )
            _finite_float(fields[1], source_name, line_number)
            last_record_was_atom_or_annotation = True
            continue
        if directive in {"lattice_vector", "atom_frac"}:
            raise AimsRecoveryError(
                f"{source_name}:{line_number} uses unsupported periodic geometry "
                f"directive {directive!r}"
            )
        raise AimsRecoveryError(
            f"{source_name}:{line_number} uses unsupported active geometry "
            f"directive {directive!r}"
        )
    if not atoms:
        raise AimsRecoveryError(f"{source_name} contains no atoms")
    return MolecularStructure(tuple(atoms), comment=f"Recovered from {source_name}")


def recover_optimized_structure(
    *,
    control_text: str | bytes | None = None,
    original_geometry_text: str | bytes,
    next_geometry_text: str | bytes,
) -> MolecularStructure:
    """Resolve aliases and require identical ordered chemistry across geometries."""

    species_elements = _recovery_species_elements(control_text)
    original = parse_molecular_geometry(
        original_geometry_text,
        species_elements,
        source_name="geometry.in",
    )
    optimized = parse_molecular_geometry(
        next_geometry_text,
        species_elements,
        source_name="geometry.in.next_step",
    )
    if len(original) != len(optimized):
        raise AimsRecoveryError(
            "geometry.in.next_step is inconsistent with the submitted structure: "
            "atom count changed"
        )
    original_elements = tuple(atom.element for atom in original)
    optimized_elements = tuple(atom.element for atom in optimized)
    if original_elements != optimized_elements:
        raise AimsRecoveryError(
            "geometry.in.next_step is inconsistent with the submitted structure: "
            "ordered chemical-element sequence changed"
        )
    return optimized


def recover_submitted_structure(
    *,
    control_text: str | bytes | None = None,
    geometry_text: str | bytes,
    source_name: str = "geometry.in",
) -> MolecularStructure:
    """Recover one submitted geometry through the server-view parsing path."""

    return parse_molecular_geometry(
        geometry_text,
        _recovery_species_elements(control_text),
        source_name=source_name,
    )


def _recovery_species_elements(
    control_text: str | bytes | None,
) -> dict[str, str]:
    if control_text is None:
        return _DIRECT_ELEMENT_SPECIES
    return parse_control_species_elements(control_text)


def _decode_text(value: str | bytes, source_name: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeError:
            raise AimsRecoveryError(f"{source_name} is not valid UTF-8") from None
    if not isinstance(value, str):
        raise AimsRecoveryError(f"{source_name} must be text or bytes")
    return value


def _finite_float(value: str, source_name: str, line_number: int) -> float:
    try:
        numeric = float(value)
    except ValueError:
        raise AimsRecoveryError(
            f"{source_name}:{line_number} coordinate/annotation is not numeric"
        ) from None
    if not isfinite(numeric):
        raise AimsRecoveryError(
            f"{source_name}:{line_number} coordinate/annotation must be finite"
        )
    return numeric


_SPECIES_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_SPECIES_DECLARATION = re.compile(
    r"[ \t]*species[ \t]+(?P<name>[A-Za-z][A-Za-z0-9_]*)"
    r"[ \t]*(?:#.*)?"
)
_NUCLEUS_DECLARATION = re.compile(
    r"[ \t]*nucleus[ \t]+(?P<atomic_number>[0-9]+)[ \t]*(?:#.*)?"
)
