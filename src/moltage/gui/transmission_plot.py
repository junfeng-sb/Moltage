"""Deterministic presentation math for one validated transmission result."""

from dataclasses import dataclass
from math import floor, isfinite, log10

from moltage.aitranss.transmission import (
    TransmissionPoint,
    TransmissionResult,
)


DEFAULT_ENERGY_MIN_EV = -2.0
DEFAULT_ENERGY_MAX_EV = 2.0
DEFAULT_TRANSMISSION_MAX = 10.0**0.5


class TransmissionPlotRangeError(ValueError):
    """Raised when the reviewed logarithmic default viewport cannot be formed."""


@dataclass(frozen=True, slots=True)
class TransmissionPlotDefaults:
    """Reviewed default viewport and its auditable window-floor evidence."""

    energy_min_ev: float
    energy_max_ev: float
    transmission_min: float
    transmission_max: float
    window_point_count: int
    minimum_positive_transmission: float
    minimum_exponent: int


@dataclass(frozen=True, slots=True)
class FermiTransmission:
    """Exact or raw-linearly interpolated transmission at ``E - EF = 0``."""

    value: float | None
    exact: bool
    lower_point: TransmissionPoint | None
    upper_point: TransmissionPoint | None
    interpolation_weight: float | None

    @property
    def available(self) -> bool:
        return self.value is not None


def transmission_plot_defaults(
    result: TransmissionResult,
) -> TransmissionPlotDefaults:
    """Compute the fixed MVP viewport without filtering or changing raw points."""

    _require_result(result)
    window_points = tuple(
        point
        for point in result.points
        if DEFAULT_ENERGY_MIN_EV
        <= point.energy_relative_ev
        <= DEFAULT_ENERGY_MAX_EV
    )
    positive = tuple(
        point.transmission_per_spin
        for point in window_points
        if isfinite(point.transmission_per_spin)
        and point.transmission_per_spin > 0.0
    )
    if not positive:
        raise TransmissionPlotRangeError(
            "No positive finite transmission is available inside the default "
            "Energy - EF window [-2, +2] eV; a logarithmic Y range cannot be "
            "established."
        )

    minimum = min(positive)
    exponent = floor(log10(minimum))
    transmission_min = 10.0**exponent
    if (
        not isfinite(transmission_min)
        or transmission_min <= 0.0
        or transmission_min >= DEFAULT_TRANSMISSION_MAX
    ):
        raise TransmissionPlotRangeError(
            "The reviewed logarithmic Y defaults do not form a valid positive "
            "range for this result."
        )
    return TransmissionPlotDefaults(
        energy_min_ev=DEFAULT_ENERGY_MIN_EV,
        energy_max_ev=DEFAULT_ENERGY_MAX_EV,
        transmission_min=transmission_min,
        transmission_max=DEFAULT_TRANSMISSION_MAX,
        window_point_count=len(window_points),
        minimum_positive_transmission=minimum,
        minimum_exponent=exponent,
    )


def transmission_at_fermi(result: TransmissionResult) -> FermiTransmission:
    """Return exact-zero T or nearest-bracket linear interpolation in raw T."""

    _require_result(result)
    lower: TransmissionPoint | None = None
    upper: TransmissionPoint | None = None
    for point in result.points:
        energy = point.energy_relative_ev
        if energy == 0.0:
            return FermiTransmission(
                value=point.transmission_per_spin,
                exact=True,
                lower_point=None,
                upper_point=None,
                interpolation_weight=None,
            )
        if energy < 0.0:
            lower = point
            continue
        upper = point
        break

    if lower is None or upper is None:
        return FermiTransmission(
            value=None,
            exact=False,
            lower_point=lower,
            upper_point=upper,
            interpolation_weight=None,
        )

    span = upper.energy_relative_ev - lower.energy_relative_ev
    weight = -lower.energy_relative_ev / span
    value = lower.transmission_per_spin + weight * (
        upper.transmission_per_spin - lower.transmission_per_spin
    )
    if not isfinite(value):
        raise TransmissionPlotRangeError(
            "T(EF) interpolation produced a non-finite presentation value."
        )
    return FermiTransmission(
        value=value,
        exact=False,
        lower_point=lower,
        upper_point=upper,
        interpolation_weight=weight,
    )


def format_transmission(value: float) -> str:
    """Format a transmission value as concise, deterministic scientific notation."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("transmission annotation requires a numeric value")
    if not isfinite(float(value)):
        raise ValueError("transmission annotation requires a finite value")
    return f"{float(value):.6e}"


def _require_result(result: TransmissionResult) -> None:
    if not isinstance(result, TransmissionResult):
        raise TypeError("transmission plot math requires a TransmissionResult")
