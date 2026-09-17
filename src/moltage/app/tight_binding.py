"""Resolve and calculate the independent local tight-binding model."""

from collections.abc import Mapping
from math import isfinite
from threading import RLock

import numpy as np

from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.domain.tight_binding import (
    TightBindingHopping,
    TightBindingModel,
    TightBindingTransmissionResult,
    TightBindingValidationError,
)


class TightBindingConfigurationError(TightBindingValidationError):
    """Editable tight-binding parameters cannot yet resolve a complete model."""


class TightBindingCalculationError(RuntimeError):
    """The resolved local model could not be evaluated numerically."""


def normalized_element_pair(first: str, second: str) -> tuple[str, str]:
    """Return one deterministic unordered element-pair key."""

    if not isinstance(first, str) or not isinstance(second, str):
        raise TypeError("element-pair members must be strings")
    return tuple(sorted((first, second)))


def resolve_tight_binding_model(
    structure: MolecularStructure,
    connectivity: Connectivity,
    *,
    onsite_by_element: Mapping[str, float],
    onsite_overrides: Mapping[int, float],
    hopping_by_element_pair: Mapping[tuple[str, str], float],
    hopping_overrides: Mapping[tuple[int, int], float],
    left_contact_index: int | None,
    right_contact_index: int | None,
    gamma_left_ev: float | None,
    gamma_right_ev: float | None,
    eta_ev: float | None,
    energy_start_ev: float | None,
    energy_end_ev: float | None,
    energy_step_ev: float | None,
) -> TightBindingModel:
    """Resolve type defaults and exact overrides without inventing parameters."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("tight-binding resolution requires a MolecularStructure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("tight-binding resolution requires Connectivity")
    if connectivity.atom_count != len(structure):
        raise TightBindingConfigurationError(
            "connectivity atom count does not match the molecular structure"
        )

    onsite_defaults = {
        _normalized_element(element): _finite_parameter(value, "onsite energy")
        for element, value in onsite_by_element.items()
    }
    onsite_exact: dict[int, float] = {}
    for index, value in onsite_overrides.items():
        _require_atom_index(index, len(structure), "onsite override")
        onsite_exact[index] = _finite_parameter(value, "onsite override")

    onsite_values: list[float] = []
    missing_atoms: list[str] = []
    for atom in structure:
        if atom.index in onsite_exact:
            onsite_values.append(onsite_exact[atom.index])
        elif atom.element in onsite_defaults:
            onsite_values.append(onsite_defaults[atom.element])
        else:
            missing_atoms.append(f"{atom.index + 1} {atom.element}")

    hopping_defaults: dict[tuple[str, str], float] = {}
    for raw_pair, value in hopping_by_element_pair.items():
        if not isinstance(raw_pair, tuple) or len(raw_pair) != 2:
            raise TightBindingConfigurationError(
                "bond-type keys must contain exactly two elements"
            )
        pair = normalized_element_pair(
            _normalized_element(raw_pair[0]),
            _normalized_element(raw_pair[1]),
        )
        hopping_defaults[pair] = _finite_parameter(value, "bond-type coupling")

    connectivity_edges = {
        (bond.first_index, bond.second_index) for bond in connectivity
    }
    hopping_exact: dict[tuple[int, int], float] = {}
    for raw_edge, value in hopping_overrides.items():
        edge = _normalized_edge(raw_edge, len(structure))
        if edge not in connectivity_edges:
            raise TightBindingConfigurationError(
                f"hopping override {edge[0] + 1}-{edge[1] + 1} is not a molecular bond"
            )
        hopping_exact[edge] = _finite_parameter(value, "bond override")

    hoppings: list[TightBindingHopping] = []
    missing_bonds: list[str] = []
    for bond in connectivity:
        edge = (bond.first_index, bond.second_index)
        pair = normalized_element_pair(
            structure[bond.first_index].element,
            structure[bond.second_index].element,
        )
        if edge in hopping_exact:
            value = hopping_exact[edge]
        elif pair in hopping_defaults:
            value = hopping_defaults[pair]
        else:
            missing_bonds.append(
                f"{bond.first_index + 1}-{bond.second_index + 1} "
                f"({pair[0]}-{pair[1]})"
            )
            continue
        hoppings.append(TightBindingHopping(*edge, value))

    missing: list[str] = []
    if missing_atoms:
        missing.append("atom energies: " + ", ".join(missing_atoms[:12]))
    if missing_bonds:
        missing.append("bond couplings: " + ", ".join(missing_bonds[:12]))
    if left_contact_index is None:
        missing.append("left contact atom")
    if right_contact_index is None:
        missing.append("right contact atom")
    for value, label in (
        (gamma_left_ev, "Gamma L"),
        (gamma_right_ev, "Gamma R"),
        (eta_ev, "eta"),
        (energy_start_ev, "energy start"),
        (energy_end_ev, "energy end"),
        (energy_step_ev, "energy step"),
    ):
        if value is None:
            missing.append(label)
    if missing:
        raise TightBindingConfigurationError(
            "Missing required tight-binding parameters — " + "; ".join(missing)
        )

    return TightBindingModel(
        onsite_energies_ev=tuple(onsite_values),
        hoppings=tuple(hoppings),
        left_contact_index=left_contact_index,
        right_contact_index=right_contact_index,
        gamma_left_ev=gamma_left_ev,
        gamma_right_ev=gamma_right_ev,
        eta_ev=eta_ev,
        energy_start_ev=energy_start_ev,
        energy_end_ev=energy_end_ev,
        energy_step_ev=energy_step_ev,
    )


def build_hamiltonian(model: TightBindingModel) -> np.ndarray:
    """Build the exact real symmetric Hamiltonian represented by the model."""

    if not isinstance(model, TightBindingModel):
        raise TypeError("Hamiltonian construction requires a TightBindingModel")
    hamiltonian = np.zeros((model.atom_count, model.atom_count), dtype=np.float64)
    np.fill_diagonal(hamiltonian, model.onsite_energies_ev)
    for hopping in model.hoppings:
        hamiltonian[hopping.first_index, hopping.second_index] = hopping.value_ev
        hamiltonian[hopping.second_index, hopping.first_index] = hopping.value_ev
    return hamiltonian


class TightBindingCalculator:
    """Evaluate single-site wide-band transmission with cached eigenpairs."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._signature: tuple[object, ...] | None = None
        self._eigenvalues: np.ndarray | None = None
        self._eigenvectors: np.ndarray | None = None

    def calculate(
        self,
        model: TightBindingModel,
    ) -> TightBindingTransmissionResult:
        if not isinstance(model, TightBindingModel):
            raise TypeError("tight-binding calculation requires a TightBindingModel")
        with self._lock:
            eigenvalues, eigenvectors = self._eigenpairs(model)
            return _transmission_from_eigenpairs(model, eigenvalues, eigenvectors)

    def _eigenpairs(
        self,
        model: TightBindingModel,
    ) -> tuple[np.ndarray, np.ndarray]:
        signature = model.hamiltonian_signature
        if (
            signature == self._signature
            and self._eigenvalues is not None
            and self._eigenvectors is not None
        ):
            return self._eigenvalues, self._eigenvectors
        try:
            eigenvalues, eigenvectors = np.linalg.eigh(build_hamiltonian(model))
        except np.linalg.LinAlgError as error:
            raise TightBindingCalculationError(
                "the Hamiltonian eigendecomposition did not converge"
            ) from error
        self._signature = signature
        self._eigenvalues = eigenvalues
        self._eigenvectors = eigenvectors
        return eigenvalues, eigenvectors


def _transmission_from_eigenpairs(
    model: TightBindingModel,
    eigenvalues: np.ndarray,
    eigenvectors: np.ndarray,
) -> TightBindingTransmissionResult:
    energies = model.energy_start_ev + model.energy_step_ev * np.arange(
        model.energy_point_count,
        dtype=np.float64,
    )
    left_row = eigenvectors[model.left_contact_index, :]
    right_row = eigenvectors[model.right_contact_index, :]
    weight_ll = left_row * left_row
    weight_rr = right_row * right_row
    weight_lr = left_row * right_row
    sigma_left = -0.5j * model.gamma_left_ev
    sigma_right = -0.5j * model.gamma_right_ev
    values = np.empty(energies.shape, dtype=np.float64)

    chunk_size = 2048
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        for start in range(0, len(energies), chunk_size):
            stop = min(len(energies), start + chunk_size)
            inverse_denominators = 1.0 / (
                energies[start:stop, None]
                + 1j * model.eta_ev
                - eigenvalues[None, :]
            )
            g_ll = inverse_denominators @ weight_ll
            g_rr = inverse_denominators @ weight_rr
            g_lr = inverse_denominators @ weight_lr
            determinant = (
                (1.0 - g_ll * sigma_left)
                * (1.0 - g_rr * sigma_right)
                - g_lr * g_lr * sigma_left * sigma_right
            )
            dressed_lr = g_lr / determinant
            values[start:stop] = (
                model.gamma_left_ev
                * model.gamma_right_ev
                * np.abs(dressed_lr) ** 2
            )

    if not np.all(np.isfinite(values)):
        raise TightBindingCalculationError(
            "the Green-function evaluation produced non-finite transmission"
        )
    if np.any(values < 0.0):
        raise TightBindingCalculationError(
            "the Green-function evaluation produced negative transmission"
        )
    return TightBindingTransmissionResult(
        energies_ev=tuple(float(value) for value in energies),
        transmissions=tuple(float(value) for value in values),
    )


def _normalized_element(value: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or not value.isalpha():
        raise TightBindingConfigurationError(
            "element parameters require ASCII element symbols"
        )
    return value[0].upper() + value[1:].lower()


def _normalized_edge(raw_edge: tuple[int, int], atom_count: int) -> tuple[int, int]:
    if not isinstance(raw_edge, tuple) or len(raw_edge) != 2:
        raise TightBindingConfigurationError(
            "bond override keys must contain exactly two atom indexes"
        )
    first, second = raw_edge
    _require_atom_index(first, atom_count, "bond override")
    _require_atom_index(second, atom_count, "bond override")
    if first == second:
        raise TightBindingConfigurationError(
            "a bond override must connect two different atoms"
        )
    return tuple(sorted((first, second)))


def _require_atom_index(value: int, atom_count: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TightBindingConfigurationError(f"{label} atom index must be an integer")
    if value < 0 or value >= atom_count:
        raise TightBindingConfigurationError(
            f"{label} atom index is outside the molecular structure"
        )


def _finite_parameter(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TightBindingConfigurationError(f"{label} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise TightBindingConfigurationError(f"{label} must be finite")
    return numeric
