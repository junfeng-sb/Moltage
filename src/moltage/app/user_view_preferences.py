"""Persist approved local lighting, theme and bond-detection preferences."""

from dataclasses import dataclass
import json
import os
from math import isfinite
from pathlib import Path

from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER

from moltage.visualization.orbital_surface import (
    DEFAULT_ORBITAL_AMBIENT,
    DEFAULT_ORBITAL_LIGHT_INTENSITY,
    DEFAULT_ORBITAL_SPECULAR,
    DEFAULT_ORBITAL_SHININESS,
    OrbitalSurfacePreferences,
)


_SCHEMA_VERSION = 3
_LEGACY_SCHEMA_VERSION = 1


class UserViewPreferencesError(RuntimeError):
    """Raised when the per-user view preference file is invalid or unwritable."""


@dataclass(frozen=True, slots=True)
class PersistedOrbitalLighting:
    """Cube lighting and material values approved for restart persistence."""

    ambient: float = DEFAULT_ORBITAL_AMBIENT
    light_intensity: float = DEFAULT_ORBITAL_LIGHT_INTENSITY
    specular: float = DEFAULT_ORBITAL_SPECULAR
    shininess: float = DEFAULT_ORBITAL_SHININESS

    def __post_init__(self) -> None:
        validated = OrbitalSurfacePreferences(
            ambient=self.ambient,
            light_intensity=self.light_intensity,
            specular=self.specular,
            shininess=self.shininess,
        )
        object.__setattr__(self, "ambient", validated.ambient)
        object.__setattr__(
            self,
            "light_intensity",
            validated.light_intensity,
        )
        object.__setattr__(self, "specular", validated.specular)
        object.__setattr__(self, "shininess", validated.shininess)


class UserViewPreferencesRepository:
    """Read and atomically replace the approved per-user preference document."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> PersistedOrbitalLighting:
        lighting, _theme_id, _factor = self._load_values()
        return lighting

    def load_theme_id(self) -> str | None:
        """Return the saved registered-theme ID, if this file has one."""

        _lighting, theme_id, _factor = self._load_values()
        return theme_id

    def load_bond_threshold_factor(self) -> float:
        """Restore the accepted factor; older files retain the original default."""
        _lighting, _theme_id, factor = self._load_values()
        return factor

    def _load_values(
        self,
    ) -> tuple[PersistedOrbitalLighting, str | None, float]:
        if not self.path.exists():
            return PersistedOrbitalLighting(), None, DEFAULT_CONNECTIVITY_MULTIPLIER
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("view-preference root must be an object")
            schema_version = raw.get("schema_version")
            if schema_version not in {
                _LEGACY_SCHEMA_VERSION,
                2,
                _SCHEMA_VERSION,
            }:
                raise ValueError("unsupported view-preference schema version")
            lighting = raw["orbital_lighting"]
            if not isinstance(lighting, dict):
                raise TypeError("orbital lighting must be an object")
            persisted_lighting = PersistedOrbitalLighting(
                ambient=lighting["ambient"],
                light_intensity=lighting["light_intensity"],
                # Older schema-1 files contain only the two lighting values.
                specular=lighting.get("specular", DEFAULT_ORBITAL_SPECULAR),
                shininess=lighting.get("shininess", DEFAULT_ORBITAL_SHININESS),
            )
            theme_id = (
                None
                if schema_version == _LEGACY_SCHEMA_VERSION
                else raw.get("theme_id")
            )
            if theme_id is not None and (
                not isinstance(theme_id, str)
                or not theme_id
                or not theme_id.isidentifier()
                or len(theme_id) > 64
            ):
                raise ValueError("theme ID must be a valid identifier")
            factor = (
                _validated_bond_factor(raw["bond_threshold_factor"])
                if schema_version == _SCHEMA_VERSION
                else DEFAULT_CONNECTIVITY_MULTIPLIER
            )
            return persisted_lighting, theme_id, factor
        except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
            raise UserViewPreferencesError(
                f"view preference file is unreadable or malformed: {error}"
            ) from None

    def save(
        self,
        preferences: PersistedOrbitalLighting,
    ) -> None:
        if not isinstance(preferences, PersistedOrbitalLighting):
            raise TypeError(
                "view preference persistence requires orbital lighting values"
            )
        _current_lighting, theme_id, factor = self._load_values()
        self._save_values(preferences, theme_id, factor)

    def save_theme_id(self, theme_id: str) -> None:
        """Persist one validated theme ID without changing Cube lighting."""

        if (
            not isinstance(theme_id, str)
            or not theme_id
            or not theme_id.isidentifier()
            or len(theme_id) > 64
        ):
            raise TypeError("theme preference requires a valid theme ID")
        lighting, _current_theme_id, factor = self._load_values()
        self._save_values(lighting, theme_id, factor)

    def save_bond_threshold_factor(self, factor: float) -> None:
        """Save an accepted factor without changing the theme or lighting."""
        checked = _validated_bond_factor(factor)
        lighting, theme_id, _factor = self._load_values()
        self._save_values(lighting, theme_id, checked)

    def _save_values(
        self,
        lighting: PersistedOrbitalLighting,
        theme_id: str | None,
        factor: float,
    ) -> None:
        document: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "bond_threshold_factor": factor,
            "orbital_lighting": {
                "ambient": lighting.ambient,
                "light_intensity": lighting.light_intensity,
                "specular": lighting.specular,
                "shininess": lighting.shininess,
            },
        }
        if theme_id is not None:
            document["theme_id"] = theme_id
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(text, encoding="utf-8", newline="\n")
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise UserViewPreferencesError(
                "view preferences could not be persisted"
            ) from None


def _validated_bond_factor(value: object) -> float:
    # Match the existing Bond Detection control, without importing GUI code.
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.01 <= value <= 10.0
        or not isfinite(value)
    ):
        raise ValueError("bond threshold factor must be between 0.01 and 10.00")
    return float(value)
