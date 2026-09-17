"""Load versioned editable defaults for the ORCA WBL settings dialog."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from os import PathLike
from pathlib import Path
import tomllib

from moltage.app.package_resources import application_resource_path


DEFAULT_ORCA_WBL_UI_DEFAULTS_PATH = application_resource_path(
    "orca",
    "wbl_ui_defaults.toml",
)


class WblUiDefaultsError(ValueError):
    """Raised when the versioned WBL UI-default resource is unusable."""


@dataclass(frozen=True, slots=True)
class WblUiDefaults:
    model_id: str
    au_fermi_energy_ev: float
    energy_min_relative_ev: float
    energy_max_relative_ev: float
    energy_step_ev: float
    classification: str
    limitations: str


def load_default_wbl_ui_defaults() -> WblUiDefaults:
    """Load the packaged, versioned WBL UI starting values."""

    return load_wbl_ui_defaults(DEFAULT_ORCA_WBL_UI_DEFAULTS_PATH)


def load_wbl_ui_defaults(path: str | PathLike[str]) -> WblUiDefaults:
    """Load one complete WBL UI-default resource without silent fallback."""

    source = Path(path)
    try:
        with source.open("rb") as stream:
            document = tomllib.load(stream)
    except OSError as error:
        raise WblUiDefaultsError(
            f"WBL UI defaults could not be read: {source}: {error}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise WblUiDefaultsError(
            f"WBL UI defaults are not valid TOML: {source}: {error}"
        ) from error

    if document.get("schema_version") != 1:
        raise WblUiDefaultsError("WBL UI defaults require schema_version = 1")
    model_id = document.get("model_id")
    provenance = document.get("provenance")
    if not isinstance(model_id, str) or not model_id.strip():
        raise WblUiDefaultsError("WBL UI defaults require a model_id")
    if not isinstance(provenance, dict):
        raise WblUiDefaultsError("WBL UI defaults require provenance metadata")
    classification = provenance.get("classification")
    limitations = provenance.get("limitations")
    if classification != "HYPOTHESIS":
        raise WblUiDefaultsError(
            "the editable Au Fermi starting value must be classified HYPOTHESIS"
        )
    if not isinstance(limitations, str) or not limitations.strip():
        raise WblUiDefaultsError("WBL UI defaults require an explicit limitations note")

    fermi = _finite(document.get("au_fermi_energy_ev"), "Au Fermi energy")
    minimum = _finite(
        document.get("energy_min_relative_ev"),
        "relative energy minimum",
    )
    maximum = _finite(
        document.get("energy_max_relative_ev"),
        "relative energy maximum",
    )
    step = _finite(document.get("energy_step_ev"), "energy step")
    if minimum >= maximum:
        raise WblUiDefaultsError(
            "WBL UI relative energy minimum must be lower than its maximum"
        )
    if step <= 0.0:
        raise WblUiDefaultsError("WBL UI energy step must be greater than zero")
    intervals = (maximum - minimum) / step
    if abs(intervals - round(intervals)) > 1.0e-9:
        raise WblUiDefaultsError(
            "WBL UI energy range must contain an integer number of steps"
        )
    return WblUiDefaults(
        model_id.strip(),
        fermi,
        minimum,
        maximum,
        step,
        classification,
        limitations.strip(),
    )


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WblUiDefaultsError(f"{label} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise WblUiDefaultsError(f"{label} must be finite")
    return result
