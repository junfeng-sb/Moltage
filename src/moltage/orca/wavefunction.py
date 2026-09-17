"""Strict reader for the ORCA JSON evidence required by WBL analysis."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite

import numpy as np

from moltage.aims.species_library import element_for_atomic_number
from moltage.domain.structure import Atom, MolecularStructure


HARTREE_TO_EV = 27.211386245988


class OrcaWavefunctionError(ValueError):
    """Raised when ORCA JSON cannot prove the required wavefunction data."""


@dataclass(frozen=True, slots=True)
class OrcaAoFunction:
    ao_index: int
    atom_index: int
    shell: str
    shell_ordinal: int
    component: str


@dataclass(frozen=True, slots=True)
class OrcaSpinOrbitals:
    energies_hartree: np.ndarray
    occupancies: np.ndarray
    coefficients: np.ndarray


@dataclass(frozen=True, slots=True)
class OrcaWavefunction:
    atoms: MolecularStructure
    charge: int
    multiplicity: int
    hf_type: str
    ao_functions: tuple[OrcaAoFunction, ...]
    overlap: np.ndarray
    alpha: OrcaSpinOrbitals
    beta: OrcaSpinOrbitals
    restricted: bool
    orca_version_text: str | None = None

    @property
    def ao_count(self) -> int:
        return len(self.ao_functions)


_SHELL_COMPONENTS = {
    "s": ("s",),
    # ORCA's documented real-solid-harmonic ordering is pz, px, py.
    "p": ("z", "x", "y"),
    "d": ("0", "+1", "-1", "+2", "-2"),
    "f": ("0", "+1", "-1", "+2", "-2", "+3", "-3"),
    "g": ("0", "+1", "-1", "+2", "-2", "+3", "-3", "+4", "-4"),
}


def parse_orca_wavefunction_json(data: str | bytes) -> OrcaWavefunction:
    """Parse tested ORCA 6 JSON layouts without guessing missing evidence."""

    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        raw = json.loads(data)
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise OrcaWavefunctionError(f"ORCA wavefunction JSON is malformed: {error}") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("Molecule"), dict):
        raise OrcaWavefunctionError("ORCA JSON is missing the Molecule object")
    molecule = raw["Molecule"]
    atoms_raw = molecule.get("Atoms")
    if not isinstance(atoms_raw, list) or not atoms_raw:
        raise OrcaWavefunctionError("ORCA JSON contains no atom/basis evidence")
    atoms = []
    ao_functions: list[OrcaAoFunction] = []
    for expected_index, atom_raw in enumerate(atoms_raw):
        if not isinstance(atom_raw, dict):
            raise OrcaWavefunctionError("ORCA atom evidence must be an object")
        index = atom_raw.get("Idx", expected_index)
        if index != expected_index:
            raise OrcaWavefunctionError("ORCA atom indexes are not ordered contiguously")
        label = atom_raw.get("ElementLabel")
        if label is None:
            try:
                label = element_for_atomic_number(int(atom_raw["ElementNumber"]))
            except (KeyError, TypeError, ValueError):
                raise OrcaWavefunctionError("ORCA atom element evidence is missing") from None
        coords = atom_raw.get("Coords")
        if not isinstance(coords, list) or len(coords) != 3:
            raise OrcaWavefunctionError("ORCA atom coordinates are incomplete")
        atoms.append(Atom(index, str(label), *coords))
        basis = atom_raw.get("Basis")
        if not isinstance(basis, list) or not basis:
            raise OrcaWavefunctionError("ORCA JSON is missing per-atom basis evidence")
        ordinals: dict[str, int] = {}
        for shell_raw in basis:
            if not isinstance(shell_raw, dict):
                raise OrcaWavefunctionError("ORCA basis shell must be an object")
            shell = str(shell_raw.get("Shell", "")).lower()
            if shell not in _SHELL_COMPONENTS:
                raise OrcaWavefunctionError(
                    f"ORCA basis shell {shell!r} is unsupported by WBL v1"
                )
            ordinal = ordinals.get(shell, 0)
            ordinals[shell] = ordinal + 1
            for component in _SHELL_COMPONENTS[shell]:
                ao_functions.append(
                    OrcaAoFunction(
                        len(ao_functions), expected_index, shell, ordinal, component
                    )
                )
    coordinate_units = str(molecule.get("CoordinateUnits", "")).lower()
    if coordinate_units not in {"angs", "angstrom", "angstroms"}:
        raise OrcaWavefunctionError(
            "ORCA WBL v1 requires Angstrom coordinates in JSON evidence"
        )
    overlap = _matrix(molecule.get("S-Matrix"), "S-Matrix")
    ao_count = len(ao_functions)
    if overlap.shape != (ao_count, ao_count):
        raise OrcaWavefunctionError(
            f"S-Matrix dimension {overlap.shape} does not match {ao_count} AO functions"
        )
    alpha, beta, restricted = _parse_spin_orbitals(
        molecule, ao_count, str(molecule.get("HFTyp", ""))
    )
    try:
        charge = int(molecule["Charge"])
        multiplicity = int(molecule["Multiplicity"])
    except (KeyError, TypeError, ValueError):
        raise OrcaWavefunctionError("ORCA charge/multiplicity evidence is missing") from None
    if multiplicity < 1:
        raise OrcaWavefunctionError("ORCA multiplicity must be positive")
    header = raw.get("ORCA Header")
    version = header.get("Version") if isinstance(header, dict) else None
    return OrcaWavefunction(
        MolecularStructure(tuple(atoms), "ORCA JSON wavefunction geometry"),
        charge,
        multiplicity,
        str(molecule.get("HFTyp", "")),
        tuple(ao_functions),
        overlap,
        alpha,
        beta,
        restricted,
        str(version) if version is not None else None,
    )


def render_orca_2json_configuration() -> bytes:
    document = {
        "MOCoefficients": True,
        "Basisset": True,
        "MullikenCharge": False,
        "LoewdinCharge": False,
        "1elIntegrals": ["S"],
        "JSONFormats": ["json"],
    }
    return (json.dumps(document, indent=2) + "\n").encode("ascii")


def _parse_spin_orbitals(molecule, ao_count, hf_type):
    container = molecule.get("MolecularOrbitals")
    alpha_raw = beta_raw = None
    if isinstance(container, dict):
        if isinstance(container.get("Alpha"), dict) and isinstance(container.get("Beta"), dict):
            alpha_raw, beta_raw = container["Alpha"], container["Beta"]
        elif "MOsAlpha" in container and "MOsBeta" in container:
            alpha_raw = {"EnergyUnit": container.get("EnergyUnit"), "MOs": container["MOsAlpha"]}
            beta_raw = {"EnergyUnit": container.get("EnergyUnit"), "MOs": container["MOsBeta"]}
        elif "AlphaMOs" in container and "BetaMOs" in container:
            alpha_raw = {"EnergyUnit": container.get("EnergyUnit"), "MOs": container["AlphaMOs"]}
            beta_raw = {"EnergyUnit": container.get("EnergyUnit"), "MOs": container["BetaMOs"]}
        elif isinstance(container.get("MOs"), list):
            spin_groups = {"ALPHA": [], "BETA": []}
            has_spin = False
            for item in container["MOs"]:
                if isinstance(item, dict) and ("Spin" in item or "SpinLabel" in item):
                    has_spin = True
                    spin = str(item.get("Spin", item.get("SpinLabel"))).upper()
                    if spin in {"A", "ALPHA", "0"}:
                        spin_groups["ALPHA"].append(item)
                    elif spin in {"B", "BETA", "1"}:
                        spin_groups["BETA"].append(item)
                    else:
                        raise OrcaWavefunctionError(f"unsupported ORCA MO spin label: {spin}")
            if has_spin:
                alpha_raw = {"EnergyUnit": container.get("EnergyUnit"), "MOs": spin_groups["ALPHA"]}
                beta_raw = {"EnergyUnit": container.get("EnergyUnit"), "MOs": spin_groups["BETA"]}
            else:
                restricted = _parse_orbital_set(container, ao_count, "restricted")
                if hf_type.upper().startswith("U"):
                    raise OrcaWavefunctionError(
                        "unrestricted ORCA JSON does not identify alpha and beta orbitals"
                    )
                return restricted, restricted, True
    if alpha_raw is None or beta_raw is None:
        alpha_container = molecule.get("MolecularOrbitalsAlpha")
        beta_container = molecule.get("MolecularOrbitalsBeta")
        if isinstance(alpha_container, dict) and isinstance(beta_container, dict):
            alpha_raw, beta_raw = alpha_container, beta_container
    if alpha_raw is None or beta_raw is None:
        raise OrcaWavefunctionError(
            "ORCA JSON is missing a supported spin-resolved MolecularOrbitals layout"
        )
    return (
        _parse_orbital_set(alpha_raw, ao_count, "alpha"),
        _parse_orbital_set(beta_raw, ao_count, "beta"),
        False,
    )


def _parse_orbital_set(raw, ao_count, label):
    if not isinstance(raw, dict) or not isinstance(raw.get("MOs"), list) or not raw["MOs"]:
        raise OrcaWavefunctionError(f"ORCA {label} MO evidence is missing")
    unit = str(raw.get("EnergyUnit", "Eh"))
    if unit not in {"Eh", "Hartree", "hartree"}:
        raise OrcaWavefunctionError(f"unsupported ORCA MO energy unit: {unit}")
    energies = []
    occupancies = []
    columns = []
    for mo in raw["MOs"]:
        if not isinstance(mo, dict):
            raise OrcaWavefunctionError(f"ORCA {label} MO record must be an object")
        try:
            energy = float(mo["OrbitalEnergy"])
            occupancy = float(mo["Occupancy"])
            coefficients = np.asarray(mo["MOCoefficients"], dtype=float)
        except (KeyError, TypeError, ValueError):
            raise OrcaWavefunctionError(f"ORCA {label} MO record is incomplete") from None
        if not isfinite(energy) or not isfinite(occupancy):
            raise OrcaWavefunctionError(f"ORCA {label} MO values must be finite")
        if coefficients.shape != (ao_count,) or not np.all(np.isfinite(coefficients)):
            raise OrcaWavefunctionError(
                f"ORCA {label} MO coefficient dimension does not match the AO basis"
            )
        energies.append(energy)
        occupancies.append(occupancy)
        columns.append(coefficients)
    return OrcaSpinOrbitals(
        np.asarray(energies, dtype=float),
        np.asarray(occupancies, dtype=float),
        np.column_stack(columns),
    )


def _matrix(value, label):
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        raise OrcaWavefunctionError(f"ORCA JSON is missing a numeric {label}") from None
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise OrcaWavefunctionError(f"ORCA {label} must be a finite matrix")
    return matrix
