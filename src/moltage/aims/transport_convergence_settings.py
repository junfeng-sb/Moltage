"""Validated scientific settings for FHI-aims Step-3 convergence."""

from dataclasses import dataclass, field
from math import isfinite

from moltage.aims.optimization_settings import (
    Relativity,
    SpeciesAccuracy,
    SpinInitializationMode,
    SpinSettings,
    XCFunctional,
)
from moltage.aims.orbital_cube import OrbitalCubeOutputSettings


class TransportConvergenceSettingsError(ValueError):
    """Raised when the reviewed Step-3 scientific surface is invalid."""


@dataclass(frozen=True, slots=True)
class TransportConvergenceSettings:
    """Narrow Step-3 model without optimization or dispersion semantics."""

    xc: XCFunctional = XCFunctional.PBE
    spin: SpinSettings = field(default_factory=SpinSettings)
    total_charge: float = 0.0
    species_accuracy: SpeciesAccuracy = SpeciesAccuracy.TIGHT
    occupation_width: float = 0.01
    n_max_pulay: int = 10
    charge_mix_param: float = 0.2
    sc_accuracy_rho: float = 1.0e-5
    sc_accuracy_eev: float = 1.0e-3
    sc_accuracy_etot: float = 1.0e-6
    sc_iter_limit: int = 500
    orbital_cubes: OrbitalCubeOutputSettings = field(
        default_factory=OrbitalCubeOutputSettings
    )

    relativity: Relativity = field(
        default=Relativity.ATOMIC_ZORA_SCALAR,
        init=False,
    )
    occupation_type: str = field(default="gaussian", init=False)
    mixer: str = field(default="pulay", init=False)
    output: str = field(default="aitranss", init=False)
    ks_method: str = field(default="serial", init=False)
    restart_file: str = field(default="aims.restart", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.xc, XCFunctional):
            raise TransportConvergenceSettingsError(
                "Step-3 XC functional is unsupported"
            )
        if not isinstance(self.spin, SpinSettings):
            raise TransportConvergenceSettingsError(
                "Step-3 spin settings are invalid"
            )
        if self.spin.enabled:
            if (
                self.spin.initialization_mode
                is not SpinInitializationMode.UNIFORM_DEFAULT
            ):
                raise TransportConvergenceSettingsError(
                    "Step-3 spin collinear requires the accepted uniform "
                    "initial-moment mode"
                )
            if self.spin.fixed_spin_moment is not None:
                raise TransportConvergenceSettingsError(
                    "fixed_spin_moment is outside the Step-3 MVP surface"
                )
        if not isinstance(self.species_accuracy, SpeciesAccuracy):
            raise TransportConvergenceSettingsError(
                "Step-3 species accuracy is unsupported"
            )
        if not isinstance(self.orbital_cubes, OrbitalCubeOutputSettings):
            raise TransportConvergenceSettingsError(
                "Step-3 orbital Cube output settings are invalid"
            )
        if self.orbital_cubes.frontier_orbitals:
            raise TransportConvergenceSettingsError(
                "Step-3 orbital output requires absolute eigenstate numbers"
            )

        object.__setattr__(
            self,
            "total_charge",
            _finite_float(self.total_charge, "total system charge"),
        )
        for field_name, label in (
            ("occupation_width", "occupation width"),
            ("sc_accuracy_rho", "sc_accuracy_rho"),
            ("sc_accuracy_eev", "sc_accuracy_eev"),
            ("sc_accuracy_etot", "sc_accuracy_etot"),
        ):
            value = _finite_float(getattr(self, field_name), label)
            if value <= 0.0:
                raise TransportConvergenceSettingsError(
                    f"{label} must be greater than zero"
                )
            object.__setattr__(self, field_name, value)

        mix = _finite_float(
            self.charge_mix_param,
            "charge_mix_param",
        )
        if not 0.0 < mix <= 1.0:
            raise TransportConvergenceSettingsError(
                "charge_mix_param must be greater than zero and at most one"
            )
        object.__setattr__(self, "charge_mix_param", mix)

        _validate_positive_integer(self.n_max_pulay, "n_max_pulay")
        _validate_positive_integer(self.sc_iter_limit, "sc_iter_limit")


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TransportConvergenceSettingsError(f"{label} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise TransportConvergenceSettingsError(f"{label} must be finite")
    return numeric


def _validate_positive_integer(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TransportConvergenceSettingsError(f"{label} must be an integer")
    if value <= 0:
        raise TransportConvergenceSettingsError(
            f"{label} must be greater than zero"
        )
