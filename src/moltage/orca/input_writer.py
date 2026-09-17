"""Deterministic ORCA input rendering from reviewed structured settings."""

from moltage.domain.structure import MolecularStructure
from moltage.domain.structure import Atom
from moltage.orca.catalog import (
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaScfConvergence,
    method_capability,
)
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings


def render_orca_optimization_input(
    structure: MolecularStructure,
    settings: OrcaOptimizationSettings,
) -> str:
    if not isinstance(settings, OrcaOptimizationSettings):
        raise TypeError("settings must be OrcaOptimizationSettings")
    settings.validate_for_structure(structure)
    tokens = _scientific_tokens(settings)
    convergence = settings.optimization_convergence.value
    if settings.coordinate_system is OrcaCoordinateSystem.CARTESIAN:
        tokens.extend(([convergence] if convergence != "OPT" else []) + ["COPT"])
    else:
        tokens.append(convergence)
    if settings.scf_convergence is not OrcaScfConvergence.DEFAULT:
        tokens.append(settings.scf_convergence.value)
    return _render(structure, settings.charge, settings.multiplicity, tokens, settings.process_count, settings.max_core_mb)


def render_orca_frequency_input(
    structure: MolecularStructure,
    settings: OrcaFrequencySettings,
) -> str:
    if not isinstance(settings, OrcaFrequencySettings):
        raise TypeError("settings must be OrcaFrequencySettings")
    source = settings.source_optimization
    source.validate_for_structure(structure)
    tokens = _scientific_tokens(source)
    tokens.append(settings.mode.value)
    if source.scf_convergence is not OrcaScfConvergence.DEFAULT:
        tokens.append(source.scf_convergence.value)
    return _render(structure, source.charge, source.multiplicity, tokens, settings.process_count, settings.max_core_mb)


def parse_rendered_orca_structure(text: str | bytes) -> MolecularStructure:
    """Recover coordinates only from Moltage's explicit ``* xyz`` block."""

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("ORCA input is not valid UTF-8") from None
    if not isinstance(text, str):
        raise TypeError("ORCA input must be text or bytes")
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip().lower().startswith("* xyz ")),
        None,
    )
    if start is None:
        raise ValueError("ORCA input has no explicit xyz coordinate block")
    atoms = []
    for line in lines[start + 1:]:
        if line.strip() == "*":
            break
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("ORCA xyz coordinate record is malformed")
        try:
            atoms.append(Atom(len(atoms), fields[0], *(float(value) for value in fields[1:])))
        except (TypeError, ValueError):
            raise ValueError("ORCA xyz coordinate record is malformed") from None
    else:
        raise ValueError("ORCA xyz coordinate block is unterminated")
    if not atoms:
        raise ValueError("ORCA xyz coordinate block is empty")
    return MolecularStructure(tuple(atoms), comment="Recovered from submitted ORCA input")


def _scientific_tokens(settings: OrcaOptimizationSettings) -> list[str]:
    if settings.method is None:
        raise ValueError("Select an ORCA method")
    capability = method_capability(settings.method)
    tokens = [settings.method.value]
    if capability.requires_basis:
        if settings.basis is None:
            raise ValueError(f"Select an orbital basis for {settings.method.value}")
        tokens.append(settings.basis.value)
    if settings.dispersion is not OrcaDispersion.NONE:
        tokens.append(settings.dispersion.value)
    return tokens


def _render(
    structure: MolecularStructure,
    charge: int,
    multiplicity: int,
    tokens: list[str],
    process_count: int,
    max_core_mb: int | None,
) -> str:
    lines = ["! " + " ".join(tokens), "%pal", f"  nprocs {process_count}", "end"]
    if max_core_mb is not None:
        lines.append(f"%maxcore {max_core_mb}")
    lines.append(f"* xyz {charge} {multiplicity}")
    for atom in structure:
        lines.append(f"  {atom.element} {atom.x!r} {atom.y!r} {atom.z!r}")
    lines.extend(("*", ""))
    return "\n".join(lines)
