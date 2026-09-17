"""Session-local, presentation-only molecular viewer preferences."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType


MINIMUM_BOND_THICKNESS_SCALE = 0.50
MAXIMUM_BOND_THICKNESS_SCALE = 2.00
DEFAULT_BOND_THICKNESS_SCALE = 1.00

RgbColor = tuple[int, int, int]
ATOM_HIGHLIGHT_COLORS_PROPERTY = "aimsTransportAtomHighlightColors"


@dataclass(frozen=True, slots=True)
class AtomHighlightColors:
    """Theme-owned colors for atom hover and fragment-selection feedback."""

    hover: RgbColor = (0, 119, 255)
    primary_group: RgbColor = (230, 0, 215)
    secondary_group: RgbColor = (0, 159, 194)

    def __post_init__(self) -> None:
        for name, color in (
            ("hover", self.hover),
            ("primary group", self.primary_group),
            ("secondary group", self.secondary_group),
        ):
            if (
                not isinstance(color, tuple)
                or len(color) != 3
                or any(
                    isinstance(component, bool)
                    or not isinstance(component, int)
                    or component < 0
                    or component > 255
                    for component in color
                )
            ):
                raise ValueError(
                    f"{name} atom-highlight color must be an RGB integer tuple"
                )


DEFAULT_ATOM_HIGHLIGHT_COLORS = AtomHighlightColors()


@dataclass(frozen=True, slots=True)
class ViewPreferences:
    """Immutable visual settings shared by every Geometry workspace."""

    bond_thickness_scale: float = DEFAULT_BOND_THICKNESS_SCALE
    element_color_overrides: Mapping[str, RgbColor] = MappingProxyType({})
    show_element_labels: bool = False
    hide_hydrogen: bool = False

    def __post_init__(self) -> None:
        scale = self.bond_thickness_scale
        if (
            isinstance(scale, bool)
            or not isinstance(scale, (int, float))
            or not isfinite(scale)
            or not MINIMUM_BOND_THICKNESS_SCALE
            <= float(scale)
            <= MAXIMUM_BOND_THICKNESS_SCALE
        ):
            raise ValueError(
                "bond thickness scale must be between "
                f"{MINIMUM_BOND_THICKNESS_SCALE:.2f} and "
                f"{MAXIMUM_BOND_THICKNESS_SCALE:.2f}"
            )

        if not isinstance(self.show_element_labels, bool):
            raise TypeError("show element labels preference must be boolean")
        if not isinstance(self.hide_hydrogen, bool):
            raise TypeError("hide hydrogen preference must be boolean")
        if not isinstance(self.element_color_overrides, Mapping):
            raise TypeError("element color overrides must be a mapping")

        normalized_overrides: dict[str, RgbColor] = {}
        for element, color in self.element_color_overrides.items():
            normalized_element = _normalized_element_symbol(element)
            if normalized_element in normalized_overrides:
                raise ValueError(
                    f"duplicate element color override for {normalized_element}"
                )
            normalized_overrides[normalized_element] = _validated_rgb(color)

        object.__setattr__(self, "bond_thickness_scale", float(scale))
        object.__setattr__(
            self,
            "element_color_overrides",
            MappingProxyType(dict(sorted(normalized_overrides.items()))),
        )

    def display_color(
        self,
        element: str,
        default_color: RgbColor,
    ) -> RgbColor:
        """Return an override when present, otherwise the frozen default color."""

        return self.element_color_overrides.get(
            _normalized_element_symbol(element),
            _validated_rgb(default_color),
        )


def _normalized_element_symbol(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or not value.isalpha()
    ):
        raise ValueError("element symbol must contain ASCII letters only")
    return value[0].upper() + value[1:].lower()


def _validated_rgb(value: object) -> RgbColor:
    try:
        color = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError("element color must contain three RGB integers") from error
    if len(color) != 3:
        raise ValueError("element color must contain three RGB integers")
    for component in color:
        if isinstance(component, bool) or not isinstance(component, int):
            raise TypeError("element color components must be integers")
        if component < 0 or component > 255:
            raise ValueError("element color components must be from 0 to 255")
    return color  # type: ignore[return-value]
