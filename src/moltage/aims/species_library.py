"""Validate and provide acquired FHI-aims species definitions in memory."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Protocol, runtime_checkable

from moltage.aims.optimization_settings import SpeciesAccuracy


class SpeciesLibraryError(ValueError):
    """Raised when a requested species definition cannot be used."""


class MissingSpeciesDefaultError(SpeciesLibraryError):
    """Raised when an exact requested element/accuracy file is absent."""


class MalformedSpeciesDefaultError(SpeciesLibraryError):
    """Raised when a species-default file has no unique expected declaration."""


@dataclass(frozen=True, slots=True)
class SpeciesDefaultBlock:
    """One resolved source block and its generated geometry species name."""

    species_name: str
    element: str
    accuracy: SpeciesAccuracy
    source_path: PurePosixPath
    text: str


@dataclass(frozen=True, slots=True)
class SpeciesRequirement:
    """One exact element/accuracy pair required by an input plan."""

    element: str
    accuracy: SpeciesAccuracy

    def __post_init__(self) -> None:
        object.__setattr__(self, "element", _canonical_element(self.element))
        if not isinstance(self.accuracy, SpeciesAccuracy):
            raise SpeciesLibraryError(
                f"unsupported species accuracy: {self.accuracy!r}"
            )


@runtime_checkable
class SpeciesDefinitionProvider(Protocol):
    """Pure input-builder boundary for validated species-definition blocks."""

    def load(
        self,
        element: str,
        accuracy: SpeciesAccuracy,
        *,
        species_name: str | None = None,
    ) -> SpeciesDefaultBlock: ...


class InMemorySpeciesLibrary:
    """Validated species blocks acquired before deterministic input rendering."""

    def __init__(self, blocks: Iterable[SpeciesDefaultBlock]) -> None:
        by_key: dict[tuple[str, SpeciesAccuracy], SpeciesDefaultBlock] = {}
        for block in blocks:
            if not isinstance(block, SpeciesDefaultBlock):
                raise TypeError(
                    "in-memory species library requires SpeciesDefaultBlock records"
                )
            key = (block.element, block.accuracy)
            if key in by_key:
                raise SpeciesLibraryError(
                    "duplicate in-memory species definition: "
                    f"element={block.element}, accuracy={block.accuracy.value}"
                )
            validated = species_default_block_from_text(
                block.element,
                block.accuracy,
                block.source_path,
                block.text,
            )
            by_key[key] = validated
        self._blocks = by_key

    def load(
        self,
        element: str,
        accuracy: SpeciesAccuracy,
        *,
        species_name: str | None = None,
    ) -> SpeciesDefaultBlock:
        normalized_element = _canonical_element(element)
        if not isinstance(accuracy, SpeciesAccuracy):
            raise SpeciesLibraryError(
                f"unsupported species accuracy: {accuracy!r}"
            )
        try:
            source = self._blocks[(normalized_element, accuracy)]
        except KeyError as error:
            raise MissingSpeciesDefaultError(
                "missing in-memory FHI-aims species default: "
                f"element={normalized_element}, accuracy={accuracy.value}"
            ) from error
        return species_default_block_from_text(
            normalized_element,
            accuracy,
            source.source_path,
            source.text,
            species_name=species_name,
        )


def species_default_filename(
    element: str,
    accuracy: SpeciesAccuracy,
) -> str:
    """Return the exact FHI-aims default filename for one element."""

    normalized_element = _canonical_element(element)
    if not isinstance(accuracy, SpeciesAccuracy):
        raise SpeciesLibraryError(
            f"unsupported species accuracy: {accuracy!r}"
        )
    atomic_number = _ATOMIC_NUMBER_BY_ELEMENT[normalized_element]
    return f"{atomic_number:02d}_{normalized_element}_default"


def species_default_block_from_text(
    element: str,
    accuracy: SpeciesAccuracy,
    source_path: PurePosixPath,
    text: str,
    *,
    species_name: str | None = None,
) -> SpeciesDefaultBlock:
    """Validate one exact source block and optionally alias its declaration."""

    normalized_element = _canonical_element(element)
    if not isinstance(accuracy, SpeciesAccuracy):
        raise SpeciesLibraryError(
            f"unsupported species accuracy: {accuracy!r}"
        )
    if not isinstance(source_path, PurePosixPath):
        raise TypeError("species source path must be a remote POSIX identity")
    if not isinstance(text, str):
        raise TypeError("species source text must be a string")
    output_species_name = (
        normalized_element
        if species_name is None
        else _validate_species_name(species_name)
    )
    normalized_text = _normalize_line_endings(text)
    lines = normalized_text.splitlines(keepends=True)
    declaration_matches = tuple(
        (line_index, match)
        for line_index, line in enumerate(lines)
        if (
            match := _SPECIES_DECLARATION.fullmatch(line.rstrip("\n"))
        )
        is not None
    )
    if len(declaration_matches) != 1:
        raise MalformedSpeciesDefaultError(
            "species default must contain exactly one active species "
            f"declaration for {normalized_element}: {source_path}"
        )
    declaration_line_index, declaration = declaration_matches[0]
    if declaration.group("name") != normalized_element:
        raise MalformedSpeciesDefaultError(
            "species default declaration does not match requested element "
            f"{normalized_element}: found {declaration.group('name')!r} in "
            f"{source_path}"
        )
    if output_species_name != normalized_element:
        replacement = (
            declaration.group("prefix")
            + output_species_name
            + declaration.group("suffix")
        )
        lines[declaration_line_index] = replacement + "\n"
        normalized_text = "".join(lines)
    return SpeciesDefaultBlock(
        species_name=output_species_name,
        element=normalized_element,
        accuracy=accuracy,
        source_path=source_path,
        text=normalized_text,
    )


def element_for_atomic_number(atomic_number: object) -> str:
    """Resolve the supported Z=1-102 element table without guessing."""

    if (
        isinstance(atomic_number, bool)
        or not isinstance(atomic_number, int)
        or not 1 <= atomic_number <= len(_ELEMENTS_Z_1_TO_102)
    ):
        raise SpeciesLibraryError(
            "atomic number is outside the supported Z=1-102 element table"
        )
    return _ELEMENTS_Z_1_TO_102[atomic_number - 1]


_SPECIES_DECLARATION = re.compile(
    r"(?P<prefix>[ \t]*species[ \t]+)(?P<name>[^\s#]+)"
    r"(?P<suffix>[ \t]*(?:#.*)?)"
)


def _canonical_element(element: object) -> str:
    if (
        not isinstance(element, str)
        or not element
        or not element.isascii()
        or not element.isalpha()
    ):
        raise SpeciesLibraryError(
            f"element symbol must contain ASCII letters only: {element!r}"
        )
    normalized = element[0].upper() + element[1:].lower()
    if normalized not in _ATOMIC_NUMBER_BY_ELEMENT:
        raise SpeciesLibraryError(
            f"element is outside the supported Z=1-102 element table: {normalized}"
        )
    return normalized


def _validate_species_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value) is None
    ):
        raise SpeciesLibraryError(f"invalid FHI-aims species name: {value!r}")
    return value


def _normalize_line_endings(text: str) -> str:
    lines = text.splitlines()
    return "\n".join(lines) + ("\n" if lines else "")


_ELEMENTS_Z_1_TO_102 = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md",
)
_ATOMIC_NUMBER_BY_ELEMENT = {
    element: atomic_number
    for atomic_number, element in enumerate(_ELEMENTS_Z_1_TO_102, start=1)
}
