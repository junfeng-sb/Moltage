"""Validated immutable settings and deterministic AITRANSS tcontrol rendering."""

from dataclasses import dataclass
from enum import StrEnum
from math import hypot
import re

from moltage.aitranss.nlayers import (
    NlayersValueSource,
    nlayers_initial_value_for_pyramid,
)
from moltage.aims.transport_evidence import (
    TransportCompletionEvidence,
    TransportEvidenceError,
    TransportSpinMode,
    parse_fortran_real,
)
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_surface import ElectrodeSurfaceProposal


class TControlError(ValueError):
    """Raised when tcontrol values are unsafe or scientifically inconsistent."""


class OnOff(StrEnum):
    ON = "on"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class ParsedTControl:
    """Reviewed existing tcontrol values plus optional explicit self-energy."""

    settings: "TControlSettings"
    spin_mode: TransportSpinMode
    self_energy_filename: str | None = None


@dataclass(frozen=True, slots=True)
class TControlProposal:
    """Unedited automatic values retained independently of final settings."""

    natoms: int
    nsaos: int
    left_surface: tuple[int, int, int]
    right_surface: tuple[int, int, int]
    nlayers: int | None
    nlayers_source: NlayersValueSource | None
    nlayers_evidence: str | None
    spin_mode: TransportSpinMode

    @classmethod
    def from_evidence(
        cls,
        evidence: TransportCompletionEvidence,
        surface: ElectrodeSurfaceProposal,
    ) -> "TControlProposal":
        if not isinstance(evidence, TransportCompletionEvidence):
            raise TypeError("tcontrol proposal requires Step-3 evidence")
        if not isinstance(surface, ElectrodeSurfaceProposal):
            raise TypeError("tcontrol proposal requires an electrode surface proposal")
        if evidence.natoms != len(surface.structure):
            raise TControlError(
                "Step-3 atom count does not match the recovered geometry.in atom count"
            )
        initial_nlayers = nlayers_initial_value_for_pyramid(surface.pyramid_layers)
        return cls(
            evidence.natoms,
            evidence.nsaos,
            surface.left_one_based,
            surface.right_one_based,
            initial_nlayers.value if initial_nlayers is not None else None,
            initial_nlayers.source if initial_nlayers is not None else None,
            initial_nlayers.evidence if initial_nlayers is not None else None,
            evidence.spin_mode,
        )


@dataclass(frozen=True, slots=True)
class TControlSettings:
    """Small user-editable tcontrol surface, separate from automatic evidence."""

    natoms: int
    nsaos: int
    lsurc: int
    lsurx: int
    lsury: int
    rsurc: int
    rsurx: int
    rsury: int
    nlayers: int = 4
    s1i: str = "0.1d0"
    s2i: str = "0.05d0"
    s3i: str = "0.025d0"
    ener: str = "-0.3200"
    estep: str = "0.0002"
    eend: str = "-0.07"
    output_filename: str = "TE.dat"
    testing: OnOff = OnOff.OFF
    ecp: OnOff = OnOff.ON

    def __post_init__(self) -> None:
        for name in (
            "natoms",
            "nsaos",
            "lsurc",
            "lsurx",
            "lsury",
            "rsurc",
            "rsurx",
            "rsury",
            "nlayers",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TControlError(f"{name} must be an integer")
            if value <= 0:
                raise TControlError(f"{name} must be greater than zero")
        for name in ("s1i", "s2i", "s3i", "ener", "estep", "eend"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TControlError(f"{name} must be numeric text")
            normalized = value.strip()
            parse_fortran_real(normalized, field_name=name)
            object.__setattr__(self, name, normalized)
        if _SAFE_FILENAME.fullmatch(self.output_filename) is None:
            raise TControlError("output filename must be one safe plain filename")
        for name in ("testing", "ecp"):
            try:
                object.__setattr__(self, name, OnOff(getattr(self, name)))
            except (TypeError, ValueError):
                raise TControlError(f"{name} must be on or off") from None

    @classmethod
    def from_proposal(cls, proposal: TControlProposal) -> "TControlSettings":
        if not isinstance(proposal, TControlProposal):
            raise TypeError("settings require a TControlProposal")
        if proposal.nlayers is None:
            raise TControlError(
                "$nlayers is not configured for this pyramid size; enter a "
                "reviewed positive integer before generating a new tcontrol"
            )
        return cls(
            natoms=proposal.natoms,
            nsaos=proposal.nsaos,
            lsurc=proposal.left_surface[0],
            lsurx=proposal.left_surface[1],
            lsury=proposal.left_surface[2],
            rsurc=proposal.right_surface[0],
            rsurx=proposal.right_surface[1],
            rsury=proposal.right_surface[2],
            nlayers=proposal.nlayers,
        )

    @property
    def left_surface(self) -> tuple[int, int, int]:
        return self.lsurc, self.lsurx, self.lsury

    @property
    def right_surface(self) -> tuple[int, int, int]:
        return self.rsurc, self.rsurx, self.rsury


def validate_tcontrol_settings(
    settings: TControlSettings,
    structure: MolecularStructure,
) -> None:
    """Validate cross-field and geometry-dependent transport constraints."""

    if not isinstance(settings, TControlSettings):
        raise TypeError("tcontrol validation requires TControlSettings")
    if not isinstance(structure, MolecularStructure):
        raise TypeError("tcontrol validation requires a MolecularStructure")
    left = settings.left_surface
    right = settings.right_surface
    if len(set(left)) != 3:
        raise TControlError("Left surface indices must be distinct")
    if len(set(right)) != 3:
        raise TControlError("Right surface indices must be distinct")
    if set(left) & set(right):
        raise TControlError("Left and Right surface indices must not overlap")
    for index in left + right:
        if index > settings.natoms:
            raise TControlError("surface index must be in the range 1..natoms")
        if index > len(structure):
            raise TControlError("surface index is outside recovered geometry.in")
        if structure[index - 1].element != "Au":
            raise TControlError(f"surface atom {index} is not Au")
    _validate_non_collinear(structure, left, "Left")
    _validate_non_collinear(structure, right, "Right")
    for name in ("s1i", "s2i", "s3i"):
        if parse_fortran_real(getattr(settings, name), field_name=name) <= 0.0:
            raise TControlError(f"{name} must be greater than zero")
    ener = parse_fortran_real(settings.ener, field_name="ener")
    estep = parse_fortran_real(settings.estep, field_name="estep")
    eend = parse_fortran_real(settings.eend, field_name="eend")
    if estep <= 0.0:
        raise TControlError("estep must be greater than zero")
    if ener >= eend:
        raise TControlError("ener must be less than eend")


def render_tcontrol(
    settings: TControlSettings,
    structure: MolecularStructure,
    spin_mode: TransportSpinMode,
) -> str:
    """Render stable UTF-8/LF tcontrol text with the accepted line order."""

    validate_tcontrol_settings(settings, structure)
    if not isinstance(spin_mode, TransportSpinMode):
        raise TControlError("tcontrol spin mode is invalid")
    lines = [
        '#input data for the "aitranss" module',
        "$aims_input on",
        "$landauer on",
        "$coord   file=geometry.in",
        f"$natoms  {settings.natoms}",
        "$basis   file=basis-indices.out",
        "$read_omat file=omat.aims",
    ]
    if spin_mode is TransportSpinMode.COLLINEAR:
        lines.extend(
            (
                "$uhfmo_alpha file=alpha.aims",
                "$uhfmo_beta  file=beta.aims",
            )
        )
    else:
        lines.append("$scfmo   file=mos.aims")
    lines.extend(
        (
            f"$nsaos   {settings.nsaos}",
            f"$lsurc   {settings.lsurc}",
            f"$lsurx   {settings.lsurx}",
            f"$lsury   {settings.lsury}",
            f"$rsurc   {settings.rsurc}",
            f"$rsurx   {settings.rsurx}",
            f"$rsury   {settings.rsury}",
            f"$nlayers {settings.nlayers}",
            f"$s1i     {settings.s1i}",
            f"$s2i     {settings.s2i}",
            f"$s3i     {settings.s3i}",
            f"$ener   {settings.ener}",
            f"$estep   {settings.estep}",
            f"$eend    {settings.eend}",
            f"$output  file={settings.output_filename}",
            f"$testing {settings.testing.value}",
            f"$ecp {settings.ecp.value}",
            "$end",
        )
    )
    return "\n".join(lines) + "\n"


def render_tcontrol_preserving_self_energy(
    settings: TControlSettings,
    structure: MolecularStructure,
    spin_mode: TransportSpinMode,
    self_energy_filename: str | None,
) -> str:
    """Render edited settings while retaining only an established file directive."""

    rendered = render_tcontrol(settings, structure, spin_mode)
    if self_energy_filename is None:
        return rendered
    if _SAFE_FILENAME.fullmatch(self_energy_filename) is None:
        raise TControlError("self-energy filename must be one safe plain filename")
    lines = rendered.splitlines()
    if not lines or lines[-1] != "$end":
        raise AssertionError("canonical tcontrol writer did not end with $end")
    lines.insert(-1, f"$self_energy file={self_energy_filename}")
    return "\n".join(lines) + "\n"


def parse_tcontrol(
    text: bytes | str,
    structure: MolecularStructure,
) -> ParsedTControl:
    """Parse only the deterministic reviewed AITRANSS tcontrol surface."""

    if isinstance(text, bytes):
        try:
            decoded = text.decode("utf-8")
        except UnicodeError:
            raise TControlError("tcontrol must be valid UTF-8") from None
    elif isinstance(text, str):
        decoded = text
    else:
        raise TypeError("tcontrol input must be bytes or text")
    if "\r" in decoded or not decoded.endswith("\n"):
        raise TControlError("tcontrol must use LF and end with a newline")
    lines = decoded.splitlines()
    if not lines or lines[0] != '#input data for the "aitranss" module':
        raise TControlError("tcontrol header is unsupported")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if not line or not line.startswith("$"):
            raise TControlError("tcontrol contains an unsupported line")
        parts = line.split(maxsplit=1)
        tag = parts[0][1:]
        value = parts[1].strip() if len(parts) == 2 else ""
        if tag in values:
            raise TControlError(f"tcontrol contains duplicate ${tag}")
        values[tag] = value

    required = {
        "aims_input",
        "landauer",
        "coord",
        "natoms",
        "basis",
        "read_omat",
        "nsaos",
        "lsurc",
        "lsurx",
        "lsury",
        "rsurc",
        "rsurx",
        "rsury",
        "nlayers",
        "s1i",
        "s2i",
        "s3i",
        "ener",
        "estep",
        "eend",
        "output",
        "testing",
        "ecp",
        "end",
    }
    spin_tags = {"scfmo", "uhfmo_alpha", "uhfmo_beta"} & values.keys()
    allowed = required | {
        "scfmo",
        "uhfmo_alpha",
        "uhfmo_beta",
        "self_energy",
    }
    missing = required - values.keys()
    unknown = values.keys() - allowed
    if missing:
        raise TControlError(
            "tcontrol is missing required tags: " + ", ".join(sorted(missing))
        )
    if unknown:
        raise TControlError(
            "tcontrol contains unsupported tags: " + ", ".join(sorted(unknown))
        )
    if values["end"]:
        raise TControlError("$end must not have a value")
    fixed = {
        "aims_input": "on",
        "landauer": "on",
        "coord": "file=geometry.in",
        "basis": "file=basis-indices.out",
        "read_omat": "file=omat.aims",
    }
    for tag, expected in fixed.items():
        if values[tag] != expected:
            raise TControlError(f"tcontrol ${tag} value is unsupported")

    if spin_tags == {"scfmo"} and values["scfmo"] == "file=mos.aims":
        spin_mode = TransportSpinMode.NONE
    elif spin_tags == {"uhfmo_alpha", "uhfmo_beta"} and (
        values["uhfmo_alpha"] == "file=alpha.aims"
        and values["uhfmo_beta"] == "file=beta.aims"
    ):
        spin_mode = TransportSpinMode.COLLINEAR
    else:
        raise TControlError("tcontrol orbital-file wiring is unsupported")

    output_filename = _file_value(values["output"], "$output")
    self_energy_filename = (
        _file_value(values["self_energy"], "$self_energy")
        if "self_energy" in values
        else None
    )
    try:
        settings = TControlSettings(
            natoms=int(values["natoms"]),
            nsaos=int(values["nsaos"]),
            lsurc=int(values["lsurc"]),
            lsurx=int(values["lsurx"]),
            lsury=int(values["lsury"]),
            rsurc=int(values["rsurc"]),
            rsurx=int(values["rsurx"]),
            rsury=int(values["rsury"]),
            nlayers=int(values["nlayers"]),
            s1i=values["s1i"],
            s2i=values["s2i"],
            s3i=values["s3i"],
            ener=values["ener"],
            estep=values["estep"],
            eend=values["eend"],
            output_filename=output_filename,
            testing=OnOff(values["testing"]),
            ecp=OnOff(values["ecp"]),
        )
    except (TypeError, ValueError):
        raise TControlError("tcontrol contains an invalid reviewed value") from None
    if settings.natoms != len(structure):
        raise TControlError("tcontrol natoms does not match geometry.in")
    validate_tcontrol_settings(settings, structure)
    return ParsedTControl(settings, spin_mode, self_energy_filename)


def render_tcontrol_with_self_energy(
    attempt01: bytes | str,
    self_energy_filename: str,
    structure: MolecularStructure,
) -> bytes:
    """Preserve attempt-1 tcontrol bytes and insert one explicit file tag."""

    parsed = parse_tcontrol(attempt01, structure)
    if parsed.self_energy_filename is not None:
        raise TControlError("attempt-1 tcontrol already references self-energy")
    if _SAFE_FILENAME.fullmatch(self_energy_filename) is None:
        raise TControlError("self-energy filename must be one safe plain filename")
    decoded = attempt01.decode("utf-8") if isinstance(attempt01, bytes) else attempt01
    lines = decoded.splitlines()
    end_indexes = [index for index, line in enumerate(lines) if line == "$end"]
    if len(end_indexes) != 1 or end_indexes[0] != len(lines) - 1:
        raise TControlError("tcontrol must contain one final $end")
    lines.insert(end_indexes[0], f"$self_energy file={self_energy_filename}")
    rendered = ("\n".join(lines) + "\n").encode("utf-8")
    reparsed = parse_tcontrol(rendered, structure)
    if reparsed.settings != parsed.settings or reparsed.spin_mode is not parsed.spin_mode:
        raise AssertionError("explicit self-energy insertion changed tcontrol science")
    if reparsed.self_energy_filename != self_energy_filename:
        raise AssertionError("explicit self-energy insertion was not retained")
    return rendered


def render_tcontrol_replacing_self_energy(
    current: bytes | str,
    expected_current_filename: str,
    replacement_filename: str,
    structure: MolecularStructure,
) -> bytes:
    """Replace only one already-active explicit self-energy filename."""

    parsed = parse_tcontrol(current, structure)
    if parsed.self_energy_filename != expected_current_filename:
        raise TControlError(
            "active tcontrol does not reference the expected self-energy file"
        )
    for filename in (expected_current_filename, replacement_filename):
        if _SAFE_FILENAME.fullmatch(filename) is None:
            raise TControlError(
                "self-energy filename must be one safe plain filename"
            )
    decoded = current.decode("utf-8") if isinstance(current, bytes) else current
    lines = decoded.splitlines()
    expected_line = f"$self_energy file={expected_current_filename}"
    indexes = [index for index, line in enumerate(lines) if line == expected_line]
    if len(indexes) != 1:
        raise TControlError(
            "active tcontrol must contain exactly one expected self-energy line"
        )
    lines[indexes[0]] = f"$self_energy file={replacement_filename}"
    rendered = ("\n".join(lines) + "\n").encode("utf-8")
    reparsed = parse_tcontrol(rendered, structure)
    if reparsed.settings != parsed.settings or reparsed.spin_mode is not parsed.spin_mode:
        raise AssertionError("self-energy filename replacement changed tcontrol science")
    if reparsed.self_energy_filename != replacement_filename:
        raise AssertionError("replacement self-energy filename was not retained")
    return rendered


def _validate_non_collinear(
    structure: MolecularStructure,
    indices: tuple[int, int, int],
    side: str,
) -> None:
    points = tuple(
        (structure[index - 1].x, structure[index - 1].y, structure[index - 1].z)
        for index in indices
    )
    first, second, third = points
    ab = tuple(b - a for a, b in zip(first, second, strict=True))
    ac = tuple(c - a for a, c in zip(first, third, strict=True))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    if hypot(*cross) <= 1.0e-10:
        raise TControlError(f"{side} surface triplet must be non-collinear")


def _file_value(value: str, tag: str) -> str:
    if not value.startswith("file="):
        raise TControlError(f"{tag} must use file=<safe-filename>")
    filename = value[5:]
    if _SAFE_FILENAME.fullmatch(filename) is None:
        raise TControlError(f"{tag} filename is unsafe")
    return filename


_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
