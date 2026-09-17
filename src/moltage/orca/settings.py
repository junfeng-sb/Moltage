"""Immutable structured settings for ORCA optimization and frequency stages."""

from dataclasses import dataclass
from hashlib import sha256
import json

from moltage.aims.species_library import element_for_atomic_number
from moltage.domain.structure import MolecularStructure
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaCatalogError,
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaOptimizationConvergence,
    OrcaScfConvergence,
    OrcaVersionFamily,
    require_supported_method,
    DEF2_MAX_ATOMIC_NUMBER,
    DEF2_MIN_ATOMIC_NUMBER,
)


class OrcaSettingsError(ValueError):
    """Raised when structured ORCA settings are missing or inconsistent."""


_ATOMIC_NUMBER = {
    element_for_atomic_number(number): number for number in range(1, 102)
}


@dataclass(frozen=True, slots=True)
class OrcaOptimizationSettings:
    method: OrcaMethod | None = None
    basis: OrcaBasis | None = None
    dispersion: OrcaDispersion = OrcaDispersion.NONE
    charge: int = 0
    multiplicity: int = 1
    optimization_convergence: OrcaOptimizationConvergence = OrcaOptimizationConvergence.OPT
    coordinate_system: OrcaCoordinateSystem = OrcaCoordinateSystem.REDUNDANT
    scf_convergence: OrcaScfConvergence = OrcaScfConvergence.DEFAULT
    process_count: int = 1
    max_core_mb: int | None = None
    version_family: OrcaVersionFamily | None = None
    scheduler_nodes: int = 1
    runtime_minutes: int = 60
    scheduler_memory_gb: int = 1

    def __post_init__(self) -> None:
        if self.method is not None:
            object.__setattr__(self, "method", OrcaMethod(self.method))
        if self.basis is not None:
            object.__setattr__(self, "basis", OrcaBasis(self.basis))
        object.__setattr__(self, "dispersion", OrcaDispersion(self.dispersion))
        object.__setattr__(self, "optimization_convergence", OrcaOptimizationConvergence(self.optimization_convergence))
        object.__setattr__(self, "coordinate_system", OrcaCoordinateSystem(self.coordinate_system))
        object.__setattr__(self, "scf_convergence", OrcaScfConvergence(self.scf_convergence))
        if self.version_family is not None:
            object.__setattr__(self, "version_family", OrcaVersionFamily(self.version_family))
        _integer(self.charge, "charge", positive=False)
        _integer(self.multiplicity, "multiplicity")
        _integer(self.process_count, "ORCA process count")
        _integer(self.scheduler_nodes, "scheduler node count")
        _integer(self.runtime_minutes, "scheduler runtime minutes")
        _integer(self.scheduler_memory_gb, "scheduler memory")
        if self.max_core_mb is not None:
            _integer(self.max_core_mb, "%MaxCore")

    @property
    def configured_memory_mb(self) -> int | None:
        return None if self.max_core_mb is None else self.process_count * self.max_core_mb

    def validate_for_structure(self, structure: MolecularStructure) -> None:
        if not isinstance(structure, MolecularStructure) or not structure:
            raise OrcaSettingsError("ORCA optimization requires a non-empty molecular structure")
        if self.method is None:
            raise OrcaSettingsError("Select an ORCA method")
        try:
            capability = require_supported_method(self.method, self.version_family)
        except OrcaCatalogError as error:
            raise OrcaSettingsError(str(error)) from None
        if capability.requires_basis and self.basis is None:
            raise OrcaSettingsError(f"Select an orbital basis for {self.method.value}")
        if not capability.requires_basis and self.basis is not None:
            raise OrcaSettingsError(f"{self.method.value} is a composite method and must not add a separate basis")
        if not capability.permits_dispersion and self.dispersion is not OrcaDispersion.NONE:
            raise OrcaSettingsError(f"{self.method.value} already defines its dispersion treatment")
        electron_count = 0
        unsupported_basis_elements: list[str] = []
        for atom in structure:
            try:
                atomic_number = _ATOMIC_NUMBER[atom.element]
            except KeyError:
                raise OrcaSettingsError(
                    f"Atomic number is unavailable for element {atom.element}"
                ) from None
            electron_count += atomic_number
            if (
                capability.requires_basis
                and self.basis is not None
                and not DEF2_MIN_ATOMIC_NUMBER <= atomic_number <= DEF2_MAX_ATOMIC_NUMBER
                and atom.element not in unsupported_basis_elements
            ):
                unsupported_basis_elements.append(atom.element)
        if unsupported_basis_elements:
            raise OrcaSettingsError(
                f"{self.basis.value} is documented only for elements H through Rn; "
                "unsupported element(s): " + ", ".join(unsupported_basis_elements)
            )
        electron_count -= self.charge
        if electron_count <= 0:
            raise OrcaSettingsError("Charge leaves no electrons in the molecular system")
        if (electron_count % 2 == 0) != (self.multiplicity % 2 == 1):
            raise OrcaSettingsError(
                f"Charge {self.charge} gives {electron_count} electrons, which is incompatible with multiplicity {self.multiplicity}"
            )

    def scientific_identity_sha256(self) -> str:
        payload = {
            "method": self.method.value if self.method else None,
            "basis": self.basis.value if self.basis else None,
            "dispersion": self.dispersion.value,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bases_for_structure(structure: MolecularStructure) -> tuple[OrcaBasis, ...]:
    """Return reviewed orbital bases whose documented range covers all atoms."""

    if not isinstance(structure, MolecularStructure) or not structure:
        return ()
    try:
        atomic_numbers = tuple(_ATOMIC_NUMBER[atom.element] for atom in structure)
    except KeyError:
        return ()
    if all(
        DEF2_MIN_ATOMIC_NUMBER <= number <= DEF2_MAX_ATOMIC_NUMBER
        for number in atomic_numbers
    ):
        return tuple(OrcaBasis)
    return ()


@dataclass(frozen=True, slots=True)
class OrcaFrequencySettings:
    source_optimization: OrcaOptimizationSettings
    source_optimization_sha256: str
    mode: OrcaFrequencyMode
    process_count: int = 1
    max_core_mb: int | None = None
    scheduler_nodes: int = 1
    runtime_minutes: int = 60
    scheduler_memory_gb: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.source_optimization, OrcaOptimizationSettings):
            raise OrcaSettingsError("Frequency settings require ORCA optimization settings")
        object.__setattr__(self, "mode", OrcaFrequencyMode(self.mode))
        if self.source_optimization.method is None:
            raise OrcaSettingsError("Frequency settings require a configured source method")
        if self.source_optimization_sha256 != self.source_optimization.scientific_identity_sha256():
            raise OrcaSettingsError("Frequency scientific settings do not match the source optimization")
        _integer(self.process_count, "ORCA process count")
        _integer(self.scheduler_nodes, "scheduler node count")
        _integer(self.runtime_minutes, "scheduler runtime minutes")
        _integer(self.scheduler_memory_gb, "scheduler memory")
        if self.max_core_mb is not None:
            _integer(self.max_core_mb, "%MaxCore")
        capability = require_supported_method(
            self.source_optimization.method,
            self.source_optimization.version_family,
        )
        if self.mode is OrcaFrequencyMode.FREQ and not capability.analytical_frequency_supported:
            raise OrcaSettingsError(
                f"Analytical frequency is not supported for {capability.method.value}"
            )


def _integer(value: object, label: str, *, positive: bool = True) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OrcaSettingsError(f"{label} must be an integer")
    if positive and value <= 0:
        raise OrcaSettingsError(f"{label} must be positive")
