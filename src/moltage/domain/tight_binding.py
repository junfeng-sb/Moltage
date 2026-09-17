"""Immutable scientific records for the local tight-binding model."""

from dataclasses import dataclass
from math import ceil, isfinite


MAX_TIGHT_BINDING_ENERGY_POINTS = 20_001


class TightBindingValidationError(ValueError):
    """A local tight-binding model is incomplete or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class TightBindingHopping:
    """One real symmetric Hamiltonian coupling between two atom orbitals."""

    first_index: int
    second_index: int
    value_ev: float

    def __post_init__(self) -> None:
        first = _atom_index(self.first_index, "hopping first atom index")
        second = _atom_index(self.second_index, "hopping second atom index")
        if first == second:
            raise TightBindingValidationError(
                "a hopping must connect two different atoms"
            )
        if first > second:
            first, second = second, first
        object.__setattr__(self, "first_index", first)
        object.__setattr__(self, "second_index", second)
        object.__setattr__(
            self,
            "value_ev",
            _finite_float(self.value_ev, "hopping value"),
        )


@dataclass(frozen=True, slots=True)
class TightBindingModel:
    """A resolved orthogonal one-orbital, two-single-site-lead model."""

    onsite_energies_ev: tuple[float, ...]
    hoppings: tuple[TightBindingHopping, ...]
    left_contact_index: int
    right_contact_index: int
    gamma_left_ev: float
    gamma_right_ev: float
    eta_ev: float
    energy_start_ev: float
    energy_end_ev: float
    energy_step_ev: float

    def __post_init__(self) -> None:
        onsite = tuple(
            _finite_float(value, f"onsite energy for atom {index + 1}")
            for index, value in enumerate(self.onsite_energies_ev)
        )
        if not onsite:
            raise TightBindingValidationError(
                "the tight-binding model requires at least one atom"
            )
        object.__setattr__(self, "onsite_energies_ev", onsite)

        left = _atom_index(self.left_contact_index, "left contact atom index")
        right = _atom_index(self.right_contact_index, "right contact atom index")
        if left >= len(onsite) or right >= len(onsite):
            raise TightBindingValidationError(
                "a contact atom index is outside the Hamiltonian"
            )
        if left == right:
            raise TightBindingValidationError(
                "left and right contacts must use different atoms"
            )
        object.__setattr__(self, "left_contact_index", left)
        object.__setattr__(self, "right_contact_index", right)

        hoppings = tuple(self.hoppings)
        seen: set[tuple[int, int]] = set()
        for hopping in hoppings:
            if not isinstance(hopping, TightBindingHopping):
                raise TypeError(
                    "tight-binding hoppings must contain TightBindingHopping records"
                )
            if hopping.second_index >= len(onsite):
                raise TightBindingValidationError(
                    "a hopping atom index is outside the Hamiltonian"
                )
            edge = (hopping.first_index, hopping.second_index)
            if edge in seen:
                raise TightBindingValidationError(
                    f"duplicate hopping for atoms {edge[0] + 1}-{edge[1] + 1}"
                )
            seen.add(edge)
        object.__setattr__(
            self,
            "hoppings",
            tuple(
                sorted(
                    hoppings,
                    key=lambda item: (item.first_index, item.second_index),
                )
            ),
        )

        for name in ("gamma_left_ev", "gamma_right_ev", "eta_ev"):
            value = _finite_float(getattr(self, name), name)
            if value <= 0.0:
                raise TightBindingValidationError(f"{name} must be positive")
            object.__setattr__(self, name, value)

        start = _finite_float(self.energy_start_ev, "energy start")
        end = _finite_float(self.energy_end_ev, "energy end")
        step = _finite_float(self.energy_step_ev, "energy step")
        if end <= start:
            raise TightBindingValidationError(
                "energy end must be greater than energy start"
            )
        if step <= 0.0:
            raise TightBindingValidationError("energy step must be positive")
        object.__setattr__(self, "energy_start_ev", start)
        object.__setattr__(self, "energy_end_ev", end)
        object.__setattr__(self, "energy_step_ev", step)
        count = _energy_point_count(start, end, step)
        if count < 2:
            raise TightBindingValidationError(
                "the start-inclusive/end-exclusive energy grid needs at least two points"
            )
        if count > MAX_TIGHT_BINDING_ENERGY_POINTS:
            raise TightBindingValidationError(
                "the energy grid exceeds the local interactive limit of "
                f"{MAX_TIGHT_BINDING_ENERGY_POINTS:,} points"
            )

    @property
    def atom_count(self) -> int:
        return len(self.onsite_energies_ev)

    @property
    def energy_point_count(self) -> int:
        return _energy_point_count(
            self.energy_start_ev,
            self.energy_end_ev,
            self.energy_step_ev,
        )

    @property
    def hamiltonian_signature(self) -> tuple[object, ...]:
        """Return the exact inputs that require a new eigendecomposition."""

        return (
            self.onsite_energies_ev,
            tuple(
                (item.first_index, item.second_index, item.value_ev)
                for item in self.hoppings
            ),
        )


@dataclass(frozen=True, slots=True)
class TightBindingTransmissionResult:
    """Raw local-model energy and transmission samples."""

    energies_ev: tuple[float, ...]
    transmissions: tuple[float, ...]

    def __post_init__(self) -> None:
        energies = tuple(
            _finite_float(value, "transmission energy")
            for value in self.energies_ev
        )
        transmissions = tuple(
            _finite_float(value, "transmission value")
            for value in self.transmissions
        )
        if len(energies) != len(transmissions) or not energies:
            raise TightBindingValidationError(
                "transmission energies and values must have the same non-zero length"
            )
        if any(value < 0.0 for value in transmissions):
            raise TightBindingValidationError(
                "transmission values must be non-negative"
            )
        if any(second <= first for first, second in zip(energies, energies[1:])):
            raise TightBindingValidationError(
                "transmission energies must be strictly increasing"
            )
        object.__setattr__(self, "energies_ev", energies)
        object.__setattr__(self, "transmissions", transmissions)


def _energy_point_count(start: float, end: float, step: float) -> int:
    ratio = (end - start) / step
    return max(1, int(ceil(ratio - max(1.0, abs(ratio)) * 1.0e-12)))


def _atom_index(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise TightBindingValidationError(f"{name} must be non-negative")
    return value


def _finite_float(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise TightBindingValidationError(f"{name} must be finite")
    return numeric
