"""Deterministic ORCA input rendering from reviewed structured settings."""

from dataclasses import dataclass

from moltage.domain.structure import MolecularStructure
from moltage.domain.structure import Atom
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaMethod,
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


@dataclass(frozen=True, slots=True)
class OrcaInputScientificIdentity:
    """Charge, multiplicity and unambiguously recognized catalog choices."""

    charge: int
    multiplicity: int
    method: OrcaMethod | None
    basis: OrcaBasis | None
    dispersion: OrcaDispersion


def parse_rendered_orca_scientific_identity(
    text: str | bytes,
) -> OrcaInputScientificIdentity:
    """Read back charge/multiplicity and only unambiguous catalog keywords.

    The coordinate-block header and the leading ``!`` keyword line are the same
    grammar ``_render`` writes, so this reads Moltage's own representation back
    rather than interpreting ORCA output.  A keyword line that does not resolve
    to exactly one reviewed method, at most one reviewed basis and at most one
    reviewed dispersion leaves all three unset instead of guessing.
    """

    lines = _input_lines(text)
    header = next(
        (
            line
            for line in lines
            if line.strip().lower().startswith("* xyz ")
        ),
        None,
    )
    if header is None:
        raise ValueError("ORCA input has no explicit xyz coordinate block")
    fields = header.split()
    if len(fields) != 4:
        raise ValueError("ORCA xyz coordinate block header is malformed")
    try:
        charge = int(fields[2])
        multiplicity = int(fields[3])
    except ValueError:
        raise ValueError(
            "ORCA xyz coordinate block header has no integer charge and multiplicity"
        ) from None
    if multiplicity < 1:
        raise ValueError("ORCA multiplicity must be positive")
    method, basis, dispersion = _recognized_keyword_choices(lines)
    return OrcaInputScientificIdentity(charge, multiplicity, method, basis, dispersion)


def _recognized_keyword_choices(lines):
    tokens = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("!"):
            tokens.extend(stripped[1:].split())
    methods = _unique_catalog_matches(tokens, OrcaMethod)
    bases = _unique_catalog_matches(tokens, OrcaBasis)
    dispersions = _unique_catalog_matches(tokens, OrcaDispersion)
    if len(methods) != 1 or len(bases) > 1 or len(dispersions) > 1:
        return None, None, OrcaDispersion.NONE
    return (
        methods[0],
        bases[0] if bases else None,
        dispersions[0] if dispersions else OrcaDispersion.NONE,
    )


def _unique_catalog_matches(tokens, catalog):
    matches = []
    for member in catalog:
        if member is OrcaDispersion.NONE:
            continue
        if any(token.upper() == member.value.upper() for token in tokens):
            matches.append(member)
    return matches


def _input_lines(text: str | bytes) -> list[str]:
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("ORCA input is not valid UTF-8") from None
    if not isinstance(text, str):
        raise TypeError("ORCA input must be text or bytes")
    return text.splitlines()


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
    lines.extend(render_orca_xyz_block(structure, charge, multiplicity))
    lines.append("")
    return "\n".join(lines)


def render_orca_xyz_block(
    structure: MolecularStructure,
    charge: int,
    multiplicity: int,
) -> list[str]:
    """Render the explicit ``* xyz`` block that ``parse_rendered_orca_structure`` reads."""

    lines = [f"* xyz {charge} {multiplicity}"]
    for atom in structure:
        lines.append(f"  {atom.element} {atom.x!r} {atom.y!r} {atom.z!r}")
    lines.append("*")
    return lines
