"""Validated settings for the supported FHI-aims optimization profile."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite

from moltage.aims.orbital_cube import (
    OrbitalCubeOutputSettings,
    default_step1_orbital_cubes,
)


class AimsSettingsValidationError(ValueError):
    """Raised when an optimization setting combination is not supported."""


class SpeciesAccuracy(Enum):
    """Approved FHI-aims defaults_2020 accuracy families."""

    LIGHT = "light"
    TIGHT = "tight"
    REALLY_TIGHT = "really_tight"


class XCFunctional(Enum):
    """Curated exchange-correlation choices for the Phase 2A MVP."""

    PBE = "pbe"
    PBE0 = "pbe0"
    BLYP = "blyp"
    B3LYP = "b3lyp"
    REVPBE = "revpbe"
    AM05 = "am05"


class VdwMethod(Enum):
    """Supported dispersion-correction methods."""

    NONE = "none"
    TS_HIRSHFELD = "ts_hirshfeld"
    TS_LIBMBD = "ts_libmbd"


class Relativity(Enum):
    """Supported scalar-relativistic treatments."""

    ATOMIC_ZORA_SCALAR = "atomic_zora_scalar"
    NONE = "none"


class SpinInitializationMode(Enum):
    """How the initial spin density is supplied for a collinear calculation."""

    PER_ATOM = "per_atom"
    UNIFORM_DEFAULT = "uniform_default"


@dataclass(frozen=True, slots=True)
class SpinSettings:
    """Spin polarization, initialization, and optional total-spin constraint."""

    enabled: bool = False
    initialization_mode: SpinInitializationMode | None = None
    uniform_initial_moment: float | None = None
    fixed_spin_moment: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise AimsSettingsValidationError("spin enabled must be a boolean")
        if self.initialization_mode is not None and not isinstance(
            self.initialization_mode, SpinInitializationMode
        ):
            raise AimsSettingsValidationError(
                "spin initialization mode is unsupported"
            )
        _set_optional_finite_float(
            self,
            "uniform_initial_moment",
            self.uniform_initial_moment,
        )
        _set_optional_finite_float(
            self,
            "fixed_spin_moment",
            self.fixed_spin_moment,
        )

        if not self.enabled:
            if self.initialization_mode is not None:
                raise AimsSettingsValidationError(
                    "spin initialization mode requires spin collinear"
                )
            if self.uniform_initial_moment is not None:
                raise AimsSettingsValidationError(
                    "uniform initial moment requires spin collinear"
                )
            if self.fixed_spin_moment is not None:
                raise AimsSettingsValidationError(
                    "fixed_spin_moment requires spin collinear"
                )
            return

        if self.initialization_mode is None:
            raise AimsSettingsValidationError(
                "spin collinear requires an initial spin-density mode"
            )
        if self.initialization_mode is SpinInitializationMode.PER_ATOM:
            if self.uniform_initial_moment is not None:
                raise AimsSettingsValidationError(
                    "per-atom spin initialization cannot set a uniform default"
                )
        elif self.initialization_mode is SpinInitializationMode.UNIFORM_DEFAULT:
            if self.uniform_initial_moment is None:
                raise AimsSettingsValidationError(
                    "uniform spin initialization requires a moment per atom"
                )
            if self.uniform_initial_moment == 0.0:
                raise AimsSettingsValidationError(
                    "uniform initial moment must be nonzero for spin collinear"
                )


@dataclass(frozen=True, slots=True)
class AtomAimsSettings:
    """Calculation-only settings for one zero-based structure atom."""

    atom_index: int
    species_accuracy: SpeciesAccuracy | None = None
    initial_moment: float | None = None
    initial_charge: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.atom_index, bool) or not isinstance(self.atom_index, int):
            raise AimsSettingsValidationError("atom override index must be an integer")
        if self.atom_index < 0:
            raise AimsSettingsValidationError(
                "atom override index must be zero-based and non-negative"
            )
        if self.species_accuracy is not None and not isinstance(
            self.species_accuracy, SpeciesAccuracy
        ):
            raise AimsSettingsValidationError(
                "atom species accuracy override is unsupported"
            )
        _set_optional_finite_float(self, "initial_moment", self.initial_moment)
        _set_optional_finite_float(self, "initial_charge", self.initial_charge)
        if (
            self.species_accuracy is None
            and self.initial_moment is None
            and self.initial_charge is None
        ):
            raise AimsSettingsValidationError(
                "atom override must set accuracy, initial moment, or initial charge"
            )


@dataclass(frozen=True, slots=True)
class AimsOptimizationSettings:
    """Complete supported settings for one molecular geometry optimization."""

    xc: XCFunctional = XCFunctional.PBE
    vdw: VdwMethod = VdwMethod.TS_HIRSHFELD
    relativity: Relativity = Relativity.ATOMIC_ZORA_SCALAR
    species_accuracy: SpeciesAccuracy = SpeciesAccuracy.TIGHT
    force_threshold: float = 1.0e-2
    output_dipole: bool = True
    orbital_cubes: OrbitalCubeOutputSettings = field(
        default_factory=default_step1_orbital_cubes
    )
    total_charge: float = 0.0
    spin: SpinSettings = field(default_factory=SpinSettings)
    atom_settings: tuple[AtomAimsSettings, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.xc, XCFunctional):
            raise AimsSettingsValidationError("XC functional is unsupported")
        if not isinstance(self.vdw, VdwMethod):
            raise AimsSettingsValidationError("vdW method is unsupported")
        if not isinstance(self.relativity, Relativity):
            raise AimsSettingsValidationError("relativity setting is unsupported")
        if not isinstance(self.species_accuracy, SpeciesAccuracy):
            raise AimsSettingsValidationError("species accuracy is unsupported")

        force_threshold = _finite_float(
            self.force_threshold,
            "force threshold",
        )
        if force_threshold <= 0.0:
            raise AimsSettingsValidationError(
                "force threshold must be greater than zero"
            )
        object.__setattr__(self, "force_threshold", force_threshold)
        if not isinstance(self.output_dipole, bool):
            raise AimsSettingsValidationError("output dipole must be a boolean")
        if not isinstance(self.orbital_cubes, OrbitalCubeOutputSettings):
            raise AimsSettingsValidationError(
                "orbital Cube output settings are invalid"
            )
        object.__setattr__(
            self,
            "total_charge",
            _finite_float(self.total_charge, "total system charge"),
        )
        if not isinstance(self.spin, SpinSettings):
            raise AimsSettingsValidationError("spin settings are invalid")

        atom_settings = tuple(self.atom_settings)
        if any(not isinstance(item, AtomAimsSettings) for item in atom_settings):
            raise AimsSettingsValidationError(
                "atom settings must contain AtomAimsSettings records"
            )
        indices = tuple(item.atom_index for item in atom_settings)
        if len(set(indices)) != len(indices):
            raise AimsSettingsValidationError(
                "duplicate atom override records are not allowed"
            )
        object.__setattr__(self, "atom_settings", atom_settings)

        moments = tuple(
            item.initial_moment
            for item in atom_settings
            if item.initial_moment is not None
        )
        if not self.spin.enabled:
            if moments:
                raise AimsSettingsValidationError(
                    "per-atom initial moments require spin collinear"
                )
        elif self.spin.initialization_mode is SpinInitializationMode.PER_ATOM:
            if not any(moment != 0.0 for moment in moments):
                raise AimsSettingsValidationError(
                    "per-atom spin initialization requires at least one "
                    "explicit nonzero initial moment"
                )


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AimsSettingsValidationError(f"{label} must be numeric")
    numeric_value = float(value)
    if not isfinite(numeric_value):
        raise AimsSettingsValidationError(f"{label} must be finite")
    return numeric_value


def _set_optional_finite_float(
    instance: object,
    field_name: str,
    value: object,
) -> None:
    if value is None:
        return
    object.__setattr__(
        instance,
        field_name,
        _finite_float(value, field_name.replace("_", " ")),
    )
