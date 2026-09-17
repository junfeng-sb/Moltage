"""Load the reviewed van der Waals radii without fallback values."""

from collections.abc import Mapping
from math import isfinite
from os import PathLike
from pathlib import Path
from types import MappingProxyType
import tomllib

from moltage.app.package_resources import application_resource_path

DEFAULT_VDW_RADII_PATH = application_resource_path(
    "chemistry",
    "vdw_radii.toml",
)

SUPPORTED_VDW_ELEMENTS = (
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


class VdwRadiiError(ValueError):
    """Raised when the configured van der Waals radii are unusable."""


def load_default_vdw_radii() -> Mapping[str, float]:
    """Load the packaged free-atom equilibrium radii."""

    return load_vdw_radii(DEFAULT_VDW_RADII_PATH)


def load_vdw_radii(path: str | PathLike[str]) -> Mapping[str, float]:
    """Load a TOML van der Waals radius table expressed in angstrom."""

    resource_path = Path(path)
    try:
        with resource_path.open("rb") as stream:
            document = tomllib.load(stream)
    except FileNotFoundError as error:
        raise VdwRadiiError(
            f"van der Waals radius resource does not exist: {resource_path}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise VdwRadiiError(
            "van der Waals radius resource is malformed TOML: "
            f"{resource_path}: {error}"
        ) from error
    except OSError as error:
        raise VdwRadiiError(
            f"unable to read van der Waals radii {resource_path}: {error}"
        ) from error

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise VdwRadiiError(
            "van der Waals radius resource is missing [metadata]"
        )
    for field in _REQUIRED_METADATA_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise VdwRadiiError(
                "van der Waals radius resource metadata."
                f"{field} must be a non-empty string"
            )
    if metadata.get("units") != "angstrom":
        raise VdwRadiiError(
            "van der Waals radius metadata.units must be 'angstrom'"
        )

    radius_table = document.get("radii")
    if not isinstance(radius_table, dict) or not radius_table:
        raise VdwRadiiError(
            "van der Waals resource must contain a non-empty [radii] table"
        )

    radii: dict[str, float] = {}
    for element, value in radius_table.items():
        if (
            not isinstance(element, str)
            or not element
            or not element.isascii()
            or not element.isalpha()
        ):
            raise VdwRadiiError(
                f"invalid element symbol in van der Waals resource: {element!r}"
            )
        normalized_element = element[0].upper() + element[1:].lower()
        if element != normalized_element:
            raise VdwRadiiError(
                "van der Waals element symbols must use canonical case: "
                f"{element!r}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise VdwRadiiError(
                f"van der Waals radius for {element} must be numeric"
            )
        numeric_value = float(value)
        if not isfinite(numeric_value) or numeric_value <= 0.0:
            raise VdwRadiiError(
                f"van der Waals radius for {element} must be finite "
                "and greater than zero"
            )
        radii[element] = numeric_value

    expected = set(SUPPORTED_VDW_ELEMENTS)
    actual = set(radii)
    if actual != expected:
        missing = [element for element in SUPPORTED_VDW_ELEMENTS if element not in actual]
        unexpected = [element for element in radii if element not in expected]
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise VdwRadiiError(
            "van der Waals radius resource must cover exactly H-Bi excluding Pm "
            f"({'; '.join(details)})"
        )

    return MappingProxyType(radii)
