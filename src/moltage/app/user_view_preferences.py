"""Persist approved per-user orbital lighting and registered UI theme."""

from dataclasses import dataclass
import json
import os
from pathlib import Path

from moltage.visualization.orbital_surface import (
    DEFAULT_ORBITAL_AMBIENT,
    DEFAULT_ORBITAL_LIGHT_INTENSITY,
    DEFAULT_ORBITAL_SPECULAR,
    DEFAULT_ORBITAL_SHININESS,
    OrbitalSurfacePreferences,
)


_SCHEMA_VERSION = 2
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
    """Read and atomically replace one small, non-scientific UI document."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> PersistedOrbitalLighting:
        lighting, _theme_id = self._load_values()
        return lighting

    def load_theme_id(self) -> str | None:
        """Return the saved registered-theme ID, if this file has one."""

        _lighting, theme_id = self._load_values()
        return theme_id

    def _load_values(
        self,
    ) -> tuple[PersistedOrbitalLighting, str | None]:
        if not self.path.exists():
            return PersistedOrbitalLighting(), None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("view-preference root must be an object")
            schema_version = raw.get("schema_version")
            if schema_version not in {
                _LEGACY_SCHEMA_VERSION,
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
            return persisted_lighting, theme_id
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
        _current_lighting, theme_id = self._load_values()
        self._save_values(preferences, theme_id)

    def save_theme_id(self, theme_id: str) -> None:
        """Persist one validated theme ID without changing Cube lighting."""

        if (
            not isinstance(theme_id, str)
            or not theme_id
            or not theme_id.isidentifier()
            or len(theme_id) > 64
        ):
            raise TypeError("theme preference requires a valid theme ID")
        lighting, _current_theme_id = self._load_values()
        self._save_values(lighting, theme_id)

    def _save_values(
        self,
        lighting: PersistedOrbitalLighting,
        theme_id: str | None,
    ) -> None:
        document: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
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
