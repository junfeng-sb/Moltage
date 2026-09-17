"""Load the approved starting parameters for every supported anchor kind."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from os import PathLike
from pathlib import Path
from types import MappingProxyType
import tomllib

from moltage.app.package_resources import application_resource_path
from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.structure.anchor_detector import paired_dicyano_group_for_anchor


DEFAULT_AU_PLACEMENT_DEFAULTS_PATH = application_resource_path(
    "anchors",
    "au_placement_defaults.toml",
)

_SECTION_BY_KIND: Mapping[AnchorKind, str] = MappingProxyType(
    {
        AnchorKind.NCS: "NCS",
        AnchorKind.SMe: "SMe",
        AnchorKind.PYRIDINE_N: "PYRIDINE_N",
        AnchorKind.NH2: "NH2",
        AnchorKind.SH: "SH",
        AnchorKind.ALKYNYL_C: "ALKYNYL_C",
        AnchorKind.CYANO_N: "CYANO_N",
        AnchorKind.DICYANO_C: "DICYANO_C",
    }
)
_DICYANO_CYANO_N_SECTION = "DICYANO_CYANO_N"


class PlacementDefaultsError(ValueError):
    """Raised when the approved placement-default resource is unusable."""


@dataclass(frozen=True, slots=True)
class AnchorPlacementDefaults:
    """Approved project starting distance and angle for one anchor kind."""

    distance_angstrom: float
    angle_degrees: float


NCSPlacementDefaults = AnchorPlacementDefaults


def load_default_au_placement_defaults(
) -> Mapping[AnchorKind, AnchorPlacementDefaults]:
    """Load the complete checked-in project-owned starting-parameter map."""

    return load_au_placement_defaults(DEFAULT_AU_PLACEMENT_DEFAULTS_PATH)


def load_default_dicyano_cyano_n_placement_defaults(
) -> AnchorPlacementDefaults:
    """Load the contextual Cyano-N defaults for an activated dicyano pair."""

    return load_dicyano_cyano_n_placement_defaults(
        DEFAULT_AU_PLACEMENT_DEFAULTS_PATH
    )


def load_au_placement_defaults(
    path: str | PathLike[str],
) -> Mapping[AnchorKind, AnchorPlacementDefaults]:
    """Load exactly one approved distance/angle pair for every anchor kind."""

    defaults, _ = _load_au_placement_default_catalog(path)
    return defaults


def load_dicyano_cyano_n_placement_defaults(
    path: str | PathLike[str],
) -> AnchorPlacementDefaults:
    """Load the contextual Cyano-N pair from a complete approved resource."""

    _, contextual_defaults = _load_au_placement_default_catalog(path)
    return contextual_defaults


def placement_defaults_for_anchor(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchor: AnchorCandidate,
    defaults: Mapping[AnchorKind, AnchorPlacementDefaults],
    dicyano_cyano_n_defaults: AnchorPlacementDefaults,
) -> AnchorPlacementDefaults:
    """Resolve candidate-specific defaults without placing chemistry in the GUI."""

    if not isinstance(anchor, AnchorCandidate):
        raise TypeError("anchor must be an AnchorCandidate")
    if (
        anchor.kind is AnchorKind.CYANO_N
        and paired_dicyano_group_for_anchor(
            structure,
            connectivity,
            anchor,
        )
        is not None
    ):
        return dicyano_cyano_n_defaults
    try:
        return defaults[anchor.kind]
    except KeyError as error:
        raise PlacementDefaultsError(
            f"no approved placement defaults for {anchor.kind.value}"
        ) from error


def _load_au_placement_default_catalog(
    path: str | PathLike[str],
) -> tuple[
    Mapping[AnchorKind, AnchorPlacementDefaults],
    AnchorPlacementDefaults,
]:

    resource_path = Path(path)
    try:
        with resource_path.open("rb") as stream:
            document = tomllib.load(stream)
    except FileNotFoundError as error:
        raise PlacementDefaultsError(
            f"Au-placement default resource does not exist: {resource_path}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise PlacementDefaultsError(
            "Au-placement default resource is malformed TOML: "
            f"{resource_path}: {error}"
        ) from error
    except OSError as error:
        raise PlacementDefaultsError(
            f"unable to read Au-placement defaults {resource_path}: {error}"
        ) from error

    required_sections = {
        "metadata",
        *_SECTION_BY_KIND.values(),
        _DICYANO_CYANO_N_SECTION,
    }
    if set(document) != required_sections:
        raise PlacementDefaultsError(
            "Au-placement defaults must contain exactly [metadata] and the "
            "eight approved anchor sections plus [DICYANO_CYANO_N]"
        )
    metadata = document["metadata"]
    if not isinstance(metadata, dict):
        raise PlacementDefaultsError(
            "Au-placement defaults [metadata] must be a table"
        )
    if metadata.get("units_distance") != "angstrom":
        raise PlacementDefaultsError(
            "Au-placement defaults distance unit must be angstrom"
        )
    if metadata.get("units_angle") != "degree":
        raise PlacementDefaultsError(
            "Au-placement defaults angle unit must be degree"
        )

    defaults: dict[AnchorKind, AnchorPlacementDefaults] = {}
    for kind, section_name in _SECTION_BY_KIND.items():
        section = document[section_name]
        angle_field = (
            "equal_angle_degrees"
            if kind is AnchorKind.PYRIDINE_N
            else "angle_degrees"
        )
        defaults[kind] = _placement_defaults_from_section(
            section,
            section_name,
            angle_field,
        )
    contextual_section = document[_DICYANO_CYANO_N_SECTION]
    contextual_defaults = _placement_defaults_from_section(
        contextual_section,
        _DICYANO_CYANO_N_SECTION,
        "angle_degrees",
    )
    return MappingProxyType(defaults), contextual_defaults


def _placement_defaults_from_section(
    section: object,
    section_name: str,
    angle_field: str,
) -> AnchorPlacementDefaults:
    if not isinstance(section, dict) or set(section) != {
        "distance_angstrom",
        angle_field,
    }:
        raise PlacementDefaultsError(
            f"[{section_name}] must contain distance_angstrom and "
            f"{angle_field} only"
        )
    return AnchorPlacementDefaults(
        _positive_finite(
            section["distance_angstrom"],
            f"{section_name} distance_angstrom",
        ),
        _angle(section[angle_field], f"{section_name} {angle_field}"),
    )


def load_default_ncs_placement_defaults() -> NCSPlacementDefaults:
    """Return the NCS entry while preserving the accepted narrow API."""

    return load_default_au_placement_defaults()[AnchorKind.NCS]


def load_ncs_placement_defaults(
    path: str | PathLike[str],
) -> NCSPlacementDefaults:
    """Return the NCS entry from a complete approved-default resource."""

    return load_au_placement_defaults(path)[AnchorKind.NCS]


def _positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlacementDefaultsError(f"{name} must be numeric")
    numeric_value = float(value)
    if not isfinite(numeric_value) or numeric_value <= 0.0:
        raise PlacementDefaultsError(
            f"{name} must be finite and greater than zero"
        )
    return numeric_value


def _angle(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlacementDefaultsError(f"{name} must be numeric")
    numeric_value = float(value)
    if (
        not isfinite(numeric_value)
        or numeric_value <= 0.0
        or numeric_value > 180.0
    ):
        raise PlacementDefaultsError(
            f"{name} must be finite, greater than zero, and at most 180"
        )
    return numeric_value
