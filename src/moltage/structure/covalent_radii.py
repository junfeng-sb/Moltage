"""Load the reviewed covalent-radius resource without fallback values."""

from collections.abc import Mapping
from math import isfinite
from os import PathLike
from pathlib import Path
from types import MappingProxyType
import tomllib

from moltage.app.package_resources import application_resource_path

DEFAULT_COVALENT_RADII_PATH = application_resource_path(
    "chemistry",
    "covalent_radii.toml",
)

SUPPORTED_COVALENT_ELEMENTS = (
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
)

_REQUIRED_METADATA_FIELDS = (
    "citation",
    "source_url",
    "source_version",
    "license",
    "license_url",
    "units",
    "original_units",
    "radius_definition",
    "coverage",
    "curation_note",
)


class CovalentRadiiError(ValueError):
    """Raised when the configured covalent-radius resource is unusable."""


def load_default_covalent_radii() -> Mapping[str, float]:
    """Load the packaged single-bond radius table for elements Z=1-83."""

    return load_covalent_radii(DEFAULT_COVALENT_RADII_PATH)


def load_covalent_radii(path: str | PathLike[str]) -> Mapping[str, float]:
    """Load a TOML radius table expressed in angstrom."""

    resource_path = Path(path)
    try:
        with resource_path.open("rb") as stream:
            document = tomllib.load(stream)
    except FileNotFoundError as error:
        raise CovalentRadiiError(
            f"covalent-radius resource does not exist: {resource_path}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise CovalentRadiiError(
            f"covalent-radius resource is malformed TOML: {resource_path}: {error}"
        ) from error
    except OSError as error:
        raise CovalentRadiiError(
            f"unable to read covalent-radius resource {resource_path}: {error}"
        ) from error

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise CovalentRadiiError("covalent-radius resource is missing [metadata]")
    for field in _REQUIRED_METADATA_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CovalentRadiiError(
                "covalent-radius resource metadata."
                f"{field} must be a non-empty string"
            )
    if metadata.get("units") != "angstrom":
        raise CovalentRadiiError(
            "covalent-radius resource metadata.units must be 'angstrom'"
        )

    radius_table = document.get("radii")
    if not isinstance(radius_table, dict) or not radius_table:
        raise CovalentRadiiError(
            "covalent-radius resource must contain a non-empty [radii] table"
        )

    radii: dict[str, float] = {}
    for element, value in radius_table.items():
        if (
            not isinstance(element, str)
            or not element
            or not element.isascii()
            or not element.isalpha()
        ):
            raise CovalentRadiiError(
                f"invalid element symbol in covalent-radius resource: {element!r}"
            )
        normalized_element = element[0].upper() + element[1:].lower()
        if element != normalized_element:
            raise CovalentRadiiError(
                "element symbols in covalent-radius resource must use canonical case: "
                f"{element!r}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CovalentRadiiError(
                f"covalent radius for {element} must be numeric"
            )
        numeric_value = float(value)
        if not isfinite(numeric_value) or numeric_value <= 0.0:
            raise CovalentRadiiError(
                f"covalent radius for {element} must be finite and greater than zero"
            )
        radii[element] = numeric_value

    expected = set(SUPPORTED_COVALENT_ELEMENTS)
    actual = set(radii)
    if actual != expected:
        missing = [element for element in SUPPORTED_COVALENT_ELEMENTS if element not in actual]
        unexpected = [element for element in radii if element not in expected]
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise CovalentRadiiError(
            "covalent-radius resource must cover exactly elements H-Bi "
            f"({'; '.join(details)})"
        )

    return MappingProxyType(radii)
