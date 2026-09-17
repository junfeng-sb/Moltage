"""Linker-parameterized ORCA wide-band-limit analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
import re
from typing import Iterable

import numpy as np

from moltage.domain.anchor import AnchorKind
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.orca.catalog import OrcaBasis
from moltage.structure.anchor_detector import detect_anchors
from moltage.orca.wavefunction import (
    HARTREE_TO_EV,
    OrcaAoFunction,
    OrcaSpinOrbitals,
    OrcaWavefunction,
)


WBL_MODEL_ID = "MOLtage_ORCA_LINKER_WBL_V1"
WBL_MODEL_CLASSIFICATION = "HYPOTHESIS"


class OrcaWblError(ValueError):
    """Raised when WBL settings or scientific evidence are incomplete."""


class WblLinkerKind(StrEnum):
    SH = "SH"
    SME = "SMe"
    NH2 = "NH2"
    PYRIDINE = "Pyridine"


class WblParameterStatus(StrEnum):
    DATA_NEEDED = "DATA_NEEDED"
    HYPOTHESIS = "HYPOTHESIS"
    CALIBRATED = "CALIBRATED"


class WblContactSubspaceMode(StrEnum):
    AUTO = "AUTO"
    S_3P_DIRECTIONAL = "S_3P_DIRECTIONAL"
    N_2S_2P_DIRECTIONAL = "N_2S_2P_DIRECTIONAL"
    N_2P_NORMAL = "N_2P_NORMAL"
    MANUAL_AO = "MANUAL_AO"


class WblSpinTreatment(StrEnum):
    """Scientific convention used for the persisted transmission result."""

    CLOSED_SHELL_SPIN_DEGENERATE = "CLOSED_SHELL_SPIN_DEGENERATE"
    SPIN_RESOLVED = "SPIN_RESOLVED"


@dataclass(frozen=True, slots=True)
class WblDetectedContact:
    """One supported linker/contact detected by the shared structure logic."""

    atom_index: int
    linker: WblLinkerKind


@dataclass(frozen=True, slots=True)
class OrcaWblContactSettings:
    atom_index: int
    linker: WblLinkerKind
    gamma0_ev: float | None = None
    parameter_status: WblParameterStatus = WblParameterStatus.DATA_NEEDED
    subspace_mode: WblContactSubspaceMode = WblContactSubspaceMode.AUTO
    manual_direction: tuple[float, float, float] | None = None
    manual_ao_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.atom_index, bool) or not isinstance(self.atom_index, int):
            raise OrcaWblError("contact atom index must be an integer")
        if self.atom_index < 0:
            raise OrcaWblError("contact atom index must be non-negative")
        object.__setattr__(self, "linker", WblLinkerKind(self.linker))
        object.__setattr__(
            self, "parameter_status", WblParameterStatus(self.parameter_status)
        )
        object.__setattr__(
            self, "subspace_mode", WblContactSubspaceMode(self.subspace_mode)
        )
        if self.gamma0_ev is not None:
            gamma = _positive_finite(self.gamma0_ev, "Gamma_0")
            object.__setattr__(self, "gamma0_ev", gamma)
            if self.parameter_status is WblParameterStatus.DATA_NEEDED:
                raise OrcaWblError(
                    "a numeric Gamma_0 must be marked HYPOTHESIS or CALIBRATED"
                )
        elif self.parameter_status is not WblParameterStatus.DATA_NEEDED:
            raise OrcaWblError(
                "HYPOTHESIS or CALIBRATED Gamma_0 requires a numeric value"
            )
        if self.manual_direction is not None:
            object.__setattr__(
                self,
                "manual_direction",
                _unit_vector(self.manual_direction, "manual contact direction"),
            )
        indices = tuple(self.manual_ao_indices)
        if any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in indices
        ):
            raise OrcaWblError("manual AO indices must be non-negative integers")
        if len(set(indices)) != len(indices):
            raise OrcaWblError("manual AO indices must be unique")
        if self.subspace_mode is WblContactSubspaceMode.MANUAL_AO and not indices:
            raise OrcaWblError("manual AO mode requires at least one AO index")
        if self.subspace_mode is not WblContactSubspaceMode.MANUAL_AO and indices:
            raise OrcaWblError("manual AO indices are valid only in MANUAL_AO mode")
        object.__setattr__(self, "manual_ao_indices", indices)

    def require_runnable(self) -> None:
        if self.gamma0_ev is None:
            raise OrcaWblError(
                f"{self.linker.value} contact Gamma_0 is DATA_NEEDED; enter a value"
            )


@dataclass(frozen=True, slots=True)
class OrcaWblSettings:
    left: OrcaWblContactSettings
    right: OrcaWblContactSettings
    fermi_energy_ev: float
    energy_min_relative_ev: float
    energy_max_relative_ev: float
    energy_step_ev: float

    def __post_init__(self) -> None:
        if not isinstance(self.left, OrcaWblContactSettings) or not isinstance(
            self.right, OrcaWblContactSettings
        ):
            raise OrcaWblError("WBL settings require left and right contacts")
        if self.left.atom_index == self.right.atom_index:
            raise OrcaWblError("left and right contact atoms must be distinct")
        for name in (
            "fermi_energy_ev",
            "energy_min_relative_ev",
            "energy_max_relative_ev",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        object.__setattr__(
            self,
            "energy_step_ev",
            _positive_finite(self.energy_step_ev, "energy step"),
        )
        if self.energy_min_relative_ev >= self.energy_max_relative_ev:
            raise OrcaWblError("energy minimum must be lower than energy maximum")
        intervals = (
            self.energy_max_relative_ev - self.energy_min_relative_ev
        ) / self.energy_step_ev
        rounded = round(intervals)
        if rounded < 1 or not np.isclose(intervals, rounded, rtol=0.0, atol=1.0e-9):
            raise OrcaWblError(
                "energy range must contain an integer number of requested steps"
            )
        if rounded > 200_000:
            raise OrcaWblError("energy grid exceeds the 200000-point safety limit")

    @property
    def relative_energy_grid_ev(self) -> tuple[float, ...]:
        count = round(
            (self.energy_max_relative_ev - self.energy_min_relative_ev)
            / self.energy_step_ev
        )
        return tuple(
            self.energy_min_relative_ev + index * self.energy_step_ev
            for index in range(count + 1)
        )

    def require_runnable(self, atom_count: int) -> None:
        self.left.require_runnable()
        self.right.require_runnable()
        if max(self.left.atom_index, self.right.atom_index) >= atom_count:
            raise OrcaWblError("contact atom index is outside the optimized structure")


@dataclass(frozen=True, slots=True)
class OrcaWblProjector:
    atom_index: int
    linker: WblLinkerKind
    resolved_mode: WblContactSubspaceMode
    direction: tuple[float, float, float] | None
    vectors: tuple[tuple[tuple[int, float], ...], ...]
    ao_indices: tuple[int, ...]
    basis_mapping: str


@dataclass(frozen=True, slots=True)
class OrcaWblOrbitalContribution:
    spin: str
    mo_number: int
    orbital_energy_ev: float
    left_weight: float
    right_weight: float
    gamma_left_ev: float
    gamma_right_ev: float
    transmission_at_fermi: float


@dataclass(frozen=True, slots=True)
class OrcaWblResult:
    model_id: str
    model_classification: str
    settings: OrcaWblSettings
    left_projector: OrcaWblProjector
    right_projector: OrcaWblProjector
    energy_relative_ev: tuple[float, ...]
    energy_absolute_ev: tuple[float, ...]
    transmission_alpha: tuple[float, ...]
    transmission_beta: tuple[float, ...]
    transmission_total: tuple[float, ...]
    alpha_contributions_at_fermi: tuple[OrcaWblOrbitalContribution, ...]
    beta_contributions_at_fermi: tuple[OrcaWblOrbitalContribution, ...]
    top_alpha: tuple[OrcaWblOrbitalContribution, ...]
    top_beta: tuple[OrcaWblOrbitalContribution, ...]
    t_alpha_at_fermi: float | None
    t_beta_at_fermi: float | None
    t_total_at_fermi: float
    spin_treatment: WblSpinTreatment = WblSpinTreatment.SPIN_RESOLVED
    total_contributions_at_fermi: tuple[OrcaWblOrbitalContribution, ...] = ()
    top_total: tuple[OrcaWblOrbitalContribution, ...] = ()


@dataclass(frozen=True, slots=True)
class OrcaWblResultEvidence:
    """Small manifest-safe summary while numerical arrays remain artifacts."""

    model_id: str
    model_classification: str
    source_gbw_sha256: str
    wavefunction_json_sha256: str
    artifact_hashes: tuple[tuple[str, str], ...]
    orca_2json_path: str
    t_alpha_at_fermi: float | None
    t_beta_at_fermi: float | None
    t_total_at_fermi: float
    top_alpha: tuple[tuple[int, float], ...]
    top_beta: tuple[tuple[int, float], ...]
    spin_treatment: WblSpinTreatment = WblSpinTreatment.SPIN_RESOLVED
    top_total: tuple[tuple[int, float], ...] = ()

    def __post_init__(self) -> None:
        if self.model_id != WBL_MODEL_ID or self.model_classification != WBL_MODEL_CLASSIFICATION:
            raise OrcaWblError("WBL result evidence uses an unsupported model identity")
        object.__setattr__(
            self,
            "spin_treatment",
            WblSpinTreatment(self.spin_treatment),
        )
        for value, label in (
            (self.source_gbw_sha256, "source GBW SHA256"),
            (self.wavefunction_json_sha256, "wavefunction JSON SHA256"),
        ):
            _require_sha256(value, label)
        hashes = tuple(self.artifact_hashes)
        if not hashes:
            raise OrcaWblError("WBL result evidence requires artifact hashes")
        for filename, digest in hashes:
            if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
                raise OrcaWblError("WBL artifact hash names must be plain filenames")
            _require_sha256(digest, f"{filename} SHA256")
        if len({name for name, _ in hashes}) != len(hashes):
            raise OrcaWblError("WBL artifact hash names must be unique")
        if not isinstance(self.orca_2json_path, str) or not self.orca_2json_path.startswith("/"):
            raise OrcaWblError("orca_2json evidence path must be absolute")
        if _finite(self.t_total_at_fermi, "T_total(E_F)") < 0.0:
            raise OrcaWblError("T_total(E_F) must be non-negative")
        if self.spin_treatment is WblSpinTreatment.SPIN_RESOLVED:
            if self.t_alpha_at_fermi is None or self.t_beta_at_fermi is None:
                raise OrcaWblError(
                    "spin-resolved WBL evidence requires alpha and beta transmissions"
                )
            alpha_fermi = _finite(self.t_alpha_at_fermi, "T_alpha(E_F)")
            beta_fermi = _finite(self.t_beta_at_fermi, "T_beta(E_F)")
            if alpha_fermi < 0.0 or beta_fermi < 0.0:
                raise OrcaWblError(
                    "spin-resolved WBL transmissions must be non-negative"
                )
            if not np.isclose(
                self.t_total_at_fermi,
                alpha_fermi + beta_fermi,
                rtol=1.0e-12,
                atol=1.0e-15,
            ):
                raise OrcaWblError("WBL total transmission must equal alpha plus beta")
            if self.top_total:
                raise OrcaWblError(
                    "spin-resolved WBL evidence cannot contain closed-shell top orbitals"
                )
        else:
            if self.t_alpha_at_fermi is not None or self.t_beta_at_fermi is not None:
                raise OrcaWblError(
                    "closed-shell WBL evidence must not contain separate spin transmissions"
                )
            if self.top_alpha or self.top_beta:
                raise OrcaWblError(
                    "closed-shell WBL evidence must not contain separate spin summaries"
                )
        for entries, label in (
            (self.top_alpha, "alpha"),
            (self.top_beta, "beta"),
            (self.top_total, "total"),
        ):
            checked = tuple(entries)
            if len(checked) > 2:
                raise OrcaWblError(f"WBL {label} summary stores at most two orbitals")
            for number, contribution in checked:
                if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                    raise OrcaWblError("WBL MO numbers must be positive integers")
                if _finite(contribution, "WBL orbital contribution") < 0.0:
                    raise OrcaWblError("WBL orbital contribution must be non-negative")
            object.__setattr__(self, f"top_{label}", checked)
        object.__setattr__(self, "artifact_hashes", tuple(sorted(hashes)))


_VALENCE_ZETA_COUNT: dict[OrcaBasis, int] = {
    OrcaBasis.DEF2_SVP: 2,
    OrcaBasis.DEF2_TZVP: 3,
    OrcaBasis.DEF2_TZVPP: 3,
    OrcaBasis.DEF2_QZVP: 4,
}

_WBL_LINKER_BY_ANCHOR_KIND = {
    AnchorKind.SH: WblLinkerKind.SH,
    AnchorKind.SMe: WblLinkerKind.SME,
    AnchorKind.NH2: WblLinkerKind.NH2,
    AnchorKind.PYRIDINE_N: WblLinkerKind.PYRIDINE,
}


def detect_wbl_contacts(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> tuple[WblDetectedContact, ...]:
    """Return supported WBL contacts from the canonical linker detector."""

    detected = []
    for anchor in detect_anchors(structure, connectivity):
        linker = _WBL_LINKER_BY_ANCHOR_KIND.get(anchor.kind)
        if linker is not None:
            detected.append(WblDetectedContact(anchor.binding_atom_index, linker))
    return tuple(
        sorted(detected, key=lambda item: (item.atom_index, item.linker.value))
    )


def resolve_automatic_contact_subspace(
    structure: MolecularStructure,
    connectivity: Connectivity,
    atom_index: int,
    linker: WblLinkerKind,
) -> tuple[WblContactSubspaceMode, tuple[float, float, float]]:
    """Expose the domain-owned automatic projection decision for UI explanation."""

    contact = OrcaWblContactSettings(
        atom_index=atom_index,
        linker=linker,
    )
    return _resolved_mode_and_direction(structure, connectivity, contact)


def calculate_orca_wbl(
    wavefunction: OrcaWavefunction,
    structure: MolecularStructure,
    connectivity: Connectivity,
    settings: OrcaWblSettings,
    *,
    basis: OrcaBasis | None,
) -> OrcaWblResult:
    """Calculate the approved all-MO independent-resonance WBL hypothesis."""

    if len(structure) != len(wavefunction.atoms):
        raise OrcaWblError("wavefunction and optimized structure atom counts differ")
    if tuple(atom.element for atom in structure) != tuple(
        atom.element for atom in wavefunction.atoms
    ):
        raise OrcaWblError("wavefunction and optimized structure element order differs")
    if connectivity.atom_count != len(structure):
        raise OrcaWblError("connectivity does not match the optimized structure")
    settings.require_runnable(len(structure))
    left = resolve_contact_projector(
        wavefunction, structure, connectivity, settings.left, basis=basis
    )
    right = resolve_contact_projector(
        wavefunction, structure, connectivity, settings.right, basis=basis
    )
    sqrt_overlap = _symmetric_positive_sqrt(wavefunction.overlap)
    relative = np.asarray(settings.relative_energy_grid_ev, dtype=float)
    absolute = relative + settings.fermi_energy_ev
    if wavefunction.multiplicity == 1:
        if not wavefunction.restricted:
            raise OrcaWblError(
                "multiplicity-1 ORCA wavefunction is unrestricted; paired-electron "
                "WBL treatment requires restricted orbital evidence"
            )
        total, total_contributions = _spin_transmission(
            wavefunction.alpha,
            sqrt_overlap,
            left,
            right,
            float(settings.left.gamma0_ev),
            float(settings.right.gamma0_ev),
            absolute,
            settings.fermi_energy_ev,
            "SPIN_DEGENERATE",
        )
        total_ef = float(
            sum(item.transmission_at_fermi for item in total_contributions)
        )
        alpha_curve = beta_curve = np.asarray((), dtype=float)
        alpha_contributions = beta_contributions = ()
        top_alpha = top_beta = ()
        alpha_ef = beta_ef = None
        spin_treatment = WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
        top_total = _top_contributions(total_contributions)
    else:
        if wavefunction.restricted:
            raise OrcaWblError(
                "open-shell ORCA wavefunction does not provide separate alpha and "
                "beta orbital evidence"
            )
        alpha_curve, alpha_contributions = _spin_transmission(
            wavefunction.alpha,
            sqrt_overlap,
            left,
            right,
            float(settings.left.gamma0_ev),
            float(settings.right.gamma0_ev),
            absolute,
            settings.fermi_energy_ev,
            "ALPHA",
        )
        beta_curve, beta_contributions = _spin_transmission(
            wavefunction.beta,
            sqrt_overlap,
            left,
            right,
            float(settings.left.gamma0_ev),
            float(settings.right.gamma0_ev),
            absolute,
            settings.fermi_energy_ev,
            "BETA",
        )
        total = alpha_curve + beta_curve
        alpha_ef = float(
            sum(item.transmission_at_fermi for item in alpha_contributions)
        )
        beta_ef = float(
            sum(item.transmission_at_fermi for item in beta_contributions)
        )
        total_ef = alpha_ef + beta_ef
        top_alpha = _top_contributions(alpha_contributions)
        top_beta = _top_contributions(beta_contributions)
        spin_treatment = WblSpinTreatment.SPIN_RESOLVED
        total_contributions = top_total = ()
    return OrcaWblResult(
        WBL_MODEL_ID,
        WBL_MODEL_CLASSIFICATION,
        settings,
        left,
        right,
        tuple(float(value) for value in relative),
        tuple(float(value) for value in absolute),
        tuple(float(value) for value in alpha_curve),
        tuple(float(value) for value in beta_curve),
        tuple(float(value) for value in total),
        alpha_contributions,
        beta_contributions,
        top_alpha,
        top_beta,
        alpha_ef,
        beta_ef,
        total_ef,
        spin_treatment,
        total_contributions,
        top_total,
    )


def _top_contributions(
    contributions: tuple[OrcaWblOrbitalContribution, ...],
) -> tuple[OrcaWblOrbitalContribution, ...]:
    return tuple(
        sorted(
            contributions,
            key=lambda item: (-item.transmission_at_fermi, item.mo_number),
        )[:2]
    )


def resolve_contact_projector(
    wavefunction: OrcaWavefunction,
    structure: MolecularStructure,
    connectivity: Connectivity,
    contact: OrcaWblContactSettings,
    *,
    basis: OrcaBasis | None,
) -> OrcaWblProjector:
    if contact.atom_index >= len(structure):
        raise OrcaWblError("contact atom index is outside the optimized structure")
    expected_element = "S" if contact.linker in {WblLinkerKind.SH, WblLinkerKind.SME} else "N"
    if structure[contact.atom_index].element != expected_element:
        raise OrcaWblError(
            f"{contact.linker.value} contact atom must be {expected_element}"
        )
    if contact.subspace_mode is WblContactSubspaceMode.MANUAL_AO:
        if max(contact.manual_ao_indices) >= wavefunction.ao_count:
            raise OrcaWblError("manual AO index is outside the wavefunction")
        wrong_atom = tuple(
            index
            for index in contact.manual_ao_indices
            if wavefunction.ao_functions[index].atom_index != contact.atom_index
        )
        if wrong_atom:
            raise OrcaWblError("manual AO subspace contains another atom's functions")
        vectors = tuple(((index, 1.0),) for index in contact.manual_ao_indices)
        return OrcaWblProjector(
            contact.atom_index,
            contact.linker,
            WblContactSubspaceMode.MANUAL_AO,
            contact.manual_direction,
            vectors,
            contact.manual_ao_indices,
            "USER_EXPLICIT_AO_INDICES",
        )
    if basis is None:
        raise OrcaWblError(
            "automatic contact projection requires a reviewed explicit def2 basis; "
            "use manual AO mode for composite/custom basis evidence"
        )
    try:
        basis = OrcaBasis(basis)
        zeta_count = _VALENCE_ZETA_COUNT[basis]
    except (ValueError, KeyError):
        raise OrcaWblError(
            "automatic contact projection does not support this basis; use manual AO mode"
        ) from None
    derived_mode, direction = _resolved_mode_and_direction(
        structure, connectivity, contact
    )
    if contact.subspace_mode is not WblContactSubspaceMode.AUTO:
        derived_mode = contact.subspace_mode
        if derived_mode is WblContactSubspaceMode.N_2P_NORMAL and contact.manual_direction is None:
            direction = _neighbor_plane_normal(structure, connectivity, contact.atom_index)
    if contact.manual_direction is not None:
        direction = contact.manual_direction
    functions = tuple(
        function
        for function in wavefunction.ao_functions
        if function.atom_index == contact.atom_index
    )
    vectors: list[tuple[tuple[int, float], ...]] = []
    if derived_mode in {
        WblContactSubspaceMode.N_2S_2P_DIRECTIONAL,
    }:
        s_shells = _last_shells(functions, "s", zeta_count)
        vectors.extend(((function.ao_index, 1.0),) for function in s_shells)
    if derived_mode not in {
        WblContactSubspaceMode.S_3P_DIRECTIONAL,
        WblContactSubspaceMode.N_2S_2P_DIRECTIONAL,
        WblContactSubspaceMode.N_2P_NORMAL,
    }:
        raise OrcaWblError(f"unsupported automatic contact mode: {derived_mode.value}")
    p_shells = _last_p_shell_groups(functions, zeta_count)
    ux, uy, uz = direction
    for shell in p_shells:
        components = {function.component: function for function in shell}
        if set(components) != {"z", "x", "y"}:
            raise OrcaWblError("ORCA p-shell component evidence is incomplete")
        vectors.append(
            (
                (components["z"].ao_index, uz),
                (components["x"].ao_index, ux),
                (components["y"].ao_index, uy),
            )
        )
    ao_indices = tuple(sorted({index for vector in vectors for index, _ in vector}))
    return OrcaWblProjector(
        contact.atom_index,
        contact.linker,
        derived_mode,
        direction,
        tuple(vectors),
        ao_indices,
        f"REVIEWED_{basis.value}_VALENCE_ZETA_{zeta_count}_ORCA_P_ORDER_ZXY",
    )


def _spin_transmission(
    spin: OrcaSpinOrbitals,
    sqrt_overlap: np.ndarray,
    left: OrcaWblProjector,
    right: OrcaWblProjector,
    gamma0_left: float,
    gamma0_right: float,
    energy_ev: np.ndarray,
    fermi_ev: float,
    spin_name: str,
) -> tuple[np.ndarray, tuple[OrcaWblOrbitalContribution, ...]]:
    lowdin = sqrt_overlap @ spin.coefficients
    left_weights = _projector_weights(lowdin, left)
    right_weights = _projector_weights(lowdin, right)
    gamma_left = gamma0_left * left_weights
    gamma_right = gamma0_right * right_weights
    orbital_energy_ev = spin.energies_hartree * HARTREE_TO_EV
    curve = np.zeros(energy_ev.shape, dtype=float)
    contributions: list[OrcaWblOrbitalContribution] = []
    for index, epsilon in enumerate(orbital_energy_ev):
        numerator = gamma_left[index] * gamma_right[index]
        width = 0.5 * (gamma_left[index] + gamma_right[index])
        denominator = (energy_ev - epsilon) ** 2 + width**2
        orbital_curve = np.divide(
            numerator,
            denominator,
            out=np.zeros_like(denominator),
            where=denominator > 0.0,
        )
        curve += orbital_curve
        ef_denominator = (fermi_ev - epsilon) ** 2 + width**2
        ef_value = numerator / ef_denominator if ef_denominator > 0.0 else 0.0
        contributions.append(
            OrcaWblOrbitalContribution(
                spin_name,
                index + 1,
                float(epsilon),
                float(left_weights[index]),
                float(right_weights[index]),
                float(gamma_left[index]),
                float(gamma_right[index]),
                float(ef_value),
            )
        )
    if not np.all(np.isfinite(curve)):
        raise OrcaWblError("WBL calculation produced non-finite transmission")
    return curve, tuple(contributions)


def _projector_weights(
    lowdin_coefficients: np.ndarray,
    projector: OrcaWblProjector,
) -> np.ndarray:
    result = np.zeros(lowdin_coefficients.shape[1], dtype=float)
    for vector in projector.vectors:
        amplitude = sum(
            coefficient * lowdin_coefficients[index, :]
            for index, coefficient in vector
        )
        result += amplitude * amplitude
    result[np.abs(result) < 1.0e-14] = 0.0
    if np.any(result < -1.0e-12) or not np.all(np.isfinite(result)):
        raise OrcaWblError("contact projection produced invalid weights")
    return result


def _symmetric_positive_sqrt(overlap: np.ndarray) -> np.ndarray:
    if overlap.ndim != 2 or overlap.shape[0] != overlap.shape[1]:
        raise OrcaWblError("AO overlap matrix must be square")
    if not np.allclose(overlap, overlap.T, rtol=0.0, atol=1.0e-10):
        raise OrcaWblError("AO overlap matrix must be symmetric")
    eigenvalues, eigenvectors = np.linalg.eigh(overlap)
    if eigenvalues.size == 0 or float(eigenvalues.min()) <= 1.0e-10:
        raise OrcaWblError(
            "AO overlap matrix is not positive definite at the supported tolerance"
        )
    return (eigenvectors * np.sqrt(eigenvalues)) @ eigenvectors.T


def _resolved_mode_and_direction(structure, connectivity, contact):
    attached_au = _neighbors(structure, connectivity, contact.atom_index, element="Au")
    if len(attached_au) > 1:
        raise OrcaWblError("multiple Au neighbors make the automatic contact direction ambiguous")
    if attached_au:
        direction = _direction_between(structure, contact.atom_index, attached_au[0])
        mode = (
            WblContactSubspaceMode.S_3P_DIRECTIONAL
            if contact.linker in {WblLinkerKind.SH, WblLinkerKind.SME}
            else WblContactSubspaceMode.N_2S_2P_DIRECTIONAL
        )
        return mode, direction
    non_au = _neighbors(structure, connectivity, contact.atom_index, exclude_element="Au")
    if contact.linker is WblLinkerKind.SH:
        hydrogens = tuple(index for index in non_au if structure[index].element == "H")
        if len(hydrogens) != 1:
            raise OrcaWblError("SH automatic direction requires exactly one S-H bond or one Au neighbor")
        return WblContactSubspaceMode.S_3P_DIRECTIONAL, _direction_between(
            structure, contact.atom_index, hydrogens[0]
        )
    if contact.linker is WblLinkerKind.SME:
        carbons = tuple(index for index in non_au if structure[index].element == "C")
        if len(carbons) != 2:
            raise OrcaWblError("SMe automatic direction requires exactly two S-C bonds")
        return WblContactSubspaceMode.S_3P_DIRECTIONAL, _opposite_bisector(
            structure, contact.atom_index, carbons
        )
    if contact.linker is WblLinkerKind.PYRIDINE:
        carbons = tuple(index for index in non_au if structure[index].element == "C")
        if len(carbons) != 2:
            raise OrcaWblError("Pyridine automatic direction requires two adjacent ring carbons")
        return WblContactSubspaceMode.N_2S_2P_DIRECTIONAL, _opposite_bisector(
            structure, contact.atom_index, carbons
        )
    if contact.linker is WblLinkerKind.NH2:
        if len(non_au) != 3:
            raise OrcaWblError("NH2 automatic direction requires exactly three non-Au neighbors")
        summed = np.sum(
            [_direction_between(structure, contact.atom_index, index) for index in non_au],
            axis=0,
        )
        numerical_tolerance = 64.0 * np.finfo(float).eps
        if float(np.linalg.norm(summed)) > numerical_tolerance:
            return WblContactSubspaceMode.N_2S_2P_DIRECTIONAL, _unit_vector(
                -summed, "NH2 lone-pair direction"
            )
        return WblContactSubspaceMode.N_2P_NORMAL, _neighbor_plane_normal(
            structure, connectivity, contact.atom_index
        )
    raise OrcaWblError(f"unsupported linker: {contact.linker.value}")


def _neighbor_plane_normal(structure, connectivity, atom_index):
    neighbors = _neighbors(structure, connectivity, atom_index, exclude_element="Au")
    if len(neighbors) < 3:
        raise OrcaWblError("local plane normal requires three non-Au neighbors")
    origin = _coords(structure[neighbors[0]])
    first = _coords(structure[neighbors[1]]) - origin
    second = _coords(structure[neighbors[2]]) - origin
    normal = np.cross(first, second)
    unit = np.asarray(_unit_vector(normal, "local neighbor-plane normal"))
    for value in unit:
        if abs(value) > 1.0e-14:
            if value < 0.0:
                unit = -unit
            break
    return tuple(float(value) for value in unit)


def _last_shells(functions: Iterable[OrcaAoFunction], shell: str, count: int):
    matching = tuple(function for function in functions if function.shell == shell)
    if len(matching) < count:
        raise OrcaWblError(f"basis evidence has fewer than {count} {shell} shells")
    return matching[-count:]


def _last_p_shell_groups(functions: Iterable[OrcaAoFunction], count: int):
    grouped: dict[int, list[OrcaAoFunction]] = {}
    for function in functions:
        if function.shell == "p":
            grouped.setdefault(function.shell_ordinal, []).append(function)
    ordered = tuple(tuple(grouped[key]) for key in sorted(grouped))
    if len(ordered) < count:
        raise OrcaWblError(f"basis evidence has fewer than {count} p shells")
    return ordered[-count:]


def _neighbors(structure, connectivity, atom_index, *, element=None, exclude_element=None):
    result = []
    for bond in connectivity:
        if bond.first_index == atom_index:
            candidate = bond.second_index
        elif bond.second_index == atom_index:
            candidate = bond.first_index
        else:
            continue
        if element is not None and structure[candidate].element != element:
            continue
        if exclude_element is not None and structure[candidate].element == exclude_element:
            continue
        result.append(candidate)
    return tuple(sorted(result))


def _opposite_bisector(structure, center_index, neighbor_indices):
    summed = np.sum(
        [_direction_between(structure, center_index, index) for index in neighbor_indices],
        axis=0,
    )
    return _unit_vector(-summed, "outward contact bisector")


def _direction_between(structure, first_index, second_index):
    return _unit_vector(
        _coords(structure[second_index]) - _coords(structure[first_index]),
        "contact bond direction",
    )


def _coords(atom):
    return np.asarray((atom.x, atom.y, atom.z), dtype=float)


def _unit_vector(values, label):
    array = np.asarray(tuple(values), dtype=float)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise OrcaWblError(f"{label} must contain three finite values")
    norm = float(np.linalg.norm(array))
    if norm <= 1.0e-12:
        raise OrcaWblError(f"{label} is geometrically degenerate")
    return tuple(float(value) for value in array / norm)


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrcaWblError(f"{label} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise OrcaWblError(f"{label} must be finite")
    return result


def _positive_finite(value, label):
    result = _finite(value, label)
    if result <= 0.0:
        raise OrcaWblError(f"{label} must be greater than zero")
    return result


def _require_sha256(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise OrcaWblError(f"{label} must be 64 lowercase hexadecimal characters")
