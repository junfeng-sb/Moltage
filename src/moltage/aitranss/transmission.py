"""Immutable, evidence-based parsing for the reviewed AITRANSS ``TE.dat``."""

from dataclasses import dataclass
from math import isclose, isfinite
import re

from moltage.aims.transport_evidence import TransportSpinMode


TRANSMISSION_RESULT_MAX_BYTES = 8 * 1024 * 1024


class TransmissionDataError(ValueError):
    """Raised when authoritative transmission input or output is unsupported."""


@dataclass(frozen=True, slots=True)
class TransmissionPoint:
    """One non-spin transmission sample from ``TE.dat``."""

    energy_hartree: float
    energy_relative_ev: float
    transmission_per_spin: float

    def __post_init__(self) -> None:
        for name in (
            "energy_hartree",
            "energy_relative_ev",
            "transmission_per_spin",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TransmissionDataError(f"{name} must be numeric")
            if not isfinite(float(value)):
                raise TransmissionDataError(f"{name} must be finite")
            object.__setattr__(self, name, float(value))


@dataclass(frozen=True, slots=True)
class TransmissionResult:
    """Parsed non-spin transmission data and supported header metadata."""

    bias_volts: float
    fermi_energy_hartree: float
    spin_mode: TransportSpinMode
    header_lines: tuple[str, str, str]
    points: tuple[TransmissionPoint, ...]

    def __post_init__(self) -> None:
        for name in ("bias_volts", "fermi_energy_hartree"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TransmissionDataError(f"{name} must be numeric")
            if not isfinite(float(value)):
                raise TransmissionDataError(f"{name} must be finite")
            object.__setattr__(self, name, float(value))
        if self.spin_mode is not TransportSpinMode.NONE:
            raise TransmissionDataError(
                "only the reviewed non-spin TE.dat format is supported"
            )
        headers = tuple(self.header_lines)
        if len(headers) != 3 or any(
            not isinstance(line, str) or not line for line in headers
        ):
            raise TransmissionDataError(
                "TE.dat must retain its three reviewed header lines"
            )
        object.__setattr__(self, "header_lines", headers)
        points = tuple(self.points)
        if not points:
            raise TransmissionDataError("TE.dat contains no transmission points")
        if any(not isinstance(point, TransmissionPoint) for point in points):
            raise TransmissionDataError(
                "transmission points must be TransmissionPoint records"
            )
        for previous, current in zip(points, points[1:]):
            if current.energy_hartree <= previous.energy_hartree:
                raise TransmissionDataError(
                    "TE.dat Hartree energies must be strictly increasing"
                )
            if current.energy_relative_ev <= previous.energy_relative_ev:
                raise TransmissionDataError(
                    "TE.dat relative energies must be strictly increasing"
                )
        object.__setattr__(self, "points", points)


@dataclass(frozen=True, slots=True)
class TransmissionRequest:
    """Attempt-authoritative result filename, grid, and spin mode from tcontrol."""

    output_filename: str
    energy_start_hartree: float
    energy_step_hartree: float
    energy_end_hartree: float
    spin_mode: TransportSpinMode
    reported_fermi_energy_hartree: float | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.output_filename, str)
            or _SAFE_FILENAME.fullmatch(self.output_filename) is None
        ):
            raise TransmissionDataError(
                "tcontrol transmission output filename is unsafe"
            )
        for name in (
            "energy_start_hartree",
            "energy_step_hartree",
            "energy_end_hartree",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TransmissionDataError(f"{name} must be numeric")
            if not isfinite(float(value)):
                raise TransmissionDataError(f"{name} must be finite")
            object.__setattr__(self, name, float(value))
        if self.energy_step_hartree <= 0.0:
            raise TransmissionDataError("tcontrol estep must be greater than zero")
        if self.energy_start_hartree >= self.energy_end_hartree:
            raise TransmissionDataError("tcontrol ener must be less than eend")
        if not isinstance(self.spin_mode, TransportSpinMode):
            raise TransmissionDataError("tcontrol spin mode is unsupported")
        if self.reported_fermi_energy_hartree is not None:
            value = self.reported_fermi_energy_hartree
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TransmissionDataError(
                    "reported tcontrol Fermi energy must be numeric"
                )
            if not isfinite(float(value)):
                raise TransmissionDataError(
                    "reported tcontrol Fermi energy must be finite"
                )
            object.__setattr__(
                self,
                "reported_fermi_energy_hartree",
                float(value),
            )


def parse_te_dat(
    text: bytes | str,
    *,
    source_name: str = "TE.dat",
) -> TransmissionResult:
    """Parse the supported non-spin transmission-result format."""

    decoded = _decode_lf_text(text, source_name)
    numbered = tuple(
        (line_number, line.strip())
        for line_number, line in enumerate(decoded.splitlines(), start=1)
        if line.strip()
    )
    if len(numbered) < 5:
        raise TransmissionDataError(
            f"{source_name} is truncated or contains no numeric result"
        )

    first_number, first = numbered[0]
    metadata_number, metadata = numbered[1]
    columns_number, columns = numbered[2]
    end_number, end = numbered[-1]
    if first.casefold() != "#non-spin-polarized calculation":
        raise TransmissionDataError(
            f"{source_name} line {first_number} has an unsupported spin header"
        )
    metadata_match = _METADATA_HEADER.fullmatch(metadata)
    if metadata_match is None:
        raise TransmissionDataError(
            f"{source_name} line {metadata_number} has an unsupported metadata header"
        )
    if _normalize_header(columns) != "# e [hartree] e-ef [ev] t(e) per spin":
        raise TransmissionDataError(
            f"{source_name} line {columns_number} has unsupported columns"
        )
    if end.casefold() != "#end":
        raise TransmissionDataError(
            f"{source_name} is truncated: the final nonblank line must be #end"
        )

    bias = _parse_finite(
        metadata_match.group("bias"),
        source_name,
        metadata_number,
        "bias",
    )
    fermi = _parse_finite(
        metadata_match.group("fermi"),
        source_name,
        metadata_number,
        "efermi",
    )
    points: list[TransmissionPoint] = []
    for line_number, line in numbered[3:-1]:
        if line.startswith("#"):
            raise TransmissionDataError(
                f"{source_name} line {line_number} contains an unsupported comment"
            )
        fields = line.split()
        if len(fields) != 3:
            raise TransmissionDataError(
                f"{source_name} line {line_number} must contain exactly three columns"
            )
        points.append(
            TransmissionPoint(
                _parse_finite(
                    fields[0], source_name, line_number, "energy in Hartree"
                ),
                _parse_finite(
                    fields[1], source_name, line_number, "energy relative to EF"
                ),
                _parse_finite(
                    fields[2], source_name, line_number, "transmission"
                ),
            )
        )

    return TransmissionResult(
        bias_volts=bias,
        fermi_energy_hartree=fermi,
        spin_mode=TransportSpinMode.NONE,
        header_lines=(first, metadata, columns),
        points=tuple(points),
    )


def parse_transmission_request(
    text: bytes | str,
    *,
    source_name: str = "tcontrol",
) -> TransmissionRequest:
    """Read only result authority needed from the accepted attempt tcontrol."""

    decoded = _decode_lf_text(text, source_name)
    lines = decoded.splitlines()
    if not lines or lines[0] != '#input data for the "aitranss" module':
        raise TransmissionDataError(f"{source_name} header is unsupported")
    if lines[-1] != "$end":
        raise TransmissionDataError(f"{source_name} must end with $end")
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:], start=2):
        if not line or not line.startswith("$"):
            raise TransmissionDataError(
                f"{source_name} line {line_number} is unsupported"
            )
        parts = line.split(maxsplit=1)
        tag = parts[0][1:]
        value = parts[1].strip() if len(parts) == 2 else ""
        if tag not in _TCONTROL_ALLOWED_TAGS:
            raise TransmissionDataError(
                f"{source_name} contains unsupported tag ${tag}"
            )
        if tag in values:
            raise TransmissionDataError(
                f"{source_name} contains duplicate tag ${tag}"
            )
        values[tag] = value
    missing = {"ener", "estep", "eend", "output", "end"} - values.keys()
    if missing:
        raise TransmissionDataError(
            f"{source_name} is missing result tags: " + ", ".join(sorted(missing))
        )
    if values["end"]:
        raise TransmissionDataError(f"{source_name} $end must not have a value")

    spin_tags = {"scfmo", "uhfmo_alpha", "uhfmo_beta"} & values.keys()
    if spin_tags == {"scfmo"} and values["scfmo"] == "file=mos.aims":
        spin_mode = TransportSpinMode.NONE
    elif spin_tags == {"uhfmo_alpha", "uhfmo_beta"} and (
        values["uhfmo_alpha"] == "file=alpha.aims"
        and values["uhfmo_beta"] == "file=beta.aims"
    ):
        spin_mode = TransportSpinMode.COLLINEAR
    else:
        raise TransmissionDataError(f"{source_name} orbital-file wiring is unsupported")

    output_value = values["output"]
    if not output_value.startswith("file="):
        raise TransmissionDataError(f"{source_name} $output must use file=<name>")
    output_filename = output_value[5:]
    runtime_tags = {"valence_electrons", "efermi"} & values.keys()
    if runtime_tags == {"valence_electrons"}:
        raise TransmissionDataError(
            f"{source_name} has an incomplete AITRANSS result annotation"
        )
    reported_fermi = None
    if "valence_electrons" in runtime_tags:
        try:
            valence_electrons = int(values["valence_electrons"])
        except ValueError:
            raise TransmissionDataError(
                f"{source_name} has invalid $valence_electrons"
            ) from None
        if valence_electrons <= 0:
            raise TransmissionDataError(
                f"{source_name} has invalid $valence_electrons"
            )
    if "efermi" in runtime_tags:
        reported_fermi = _parse_finite(
            values["efermi"], source_name, 0, "$efermi"
        )
    return TransmissionRequest(
        output_filename=output_filename,
        energy_start_hartree=_parse_finite(
            values["ener"], source_name, 0, "$ener"
        ),
        energy_step_hartree=_parse_finite(
            values["estep"], source_name, 0, "$estep"
        ),
        energy_end_hartree=_parse_finite(
            values["eend"], source_name, 0, "$eend"
        ),
        spin_mode=spin_mode,
        reported_fermi_energy_hartree=reported_fermi,
    )


def submitted_tcontrol_bytes(text: bytes | str) -> bytes:
    """Remove only the observed AITRANSS-added result annotations."""

    decoded = _decode_lf_text(text, "tcontrol")
    lines = decoded.splitlines()
    annotation_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith(("$valence_electrons", "$efermi"))
    ]
    if not annotation_indexes:
        return decoded.encode("utf-8")
    annotation_start: int
    if (
        len(lines) >= 3
        and annotation_indexes == [len(lines) - 3, len(lines) - 2]
        and lines[-3].startswith("$valence_electrons ")
        and lines[-2].startswith("$efermi ")
        and lines[-1] == "$end"
    ):
        annotation_start = len(lines) - 3
    elif (
        len(lines) >= 2
        and annotation_indexes == [len(lines) - 2]
        and lines[-2].startswith("$efermi ")
        and lines[-1] == "$end"
    ):
        annotation_start = len(lines) - 2
    else:
        raise TransmissionDataError(
            "AITRANSS result annotations in tcontrol have unsupported placement"
        )
    if annotation_start == len(lines) - 3:
        try:
            valence_electrons = int(lines[-3].split(maxsplit=1)[1])
        except (IndexError, ValueError):
            raise TransmissionDataError(
                "AITRANSS $valence_electrons annotation is invalid"
            ) from None
        if valence_electrons <= 0:
            raise TransmissionDataError(
                "AITRANSS $valence_electrons annotation is invalid"
            )
    try:
        efermi = lines[-2].split(maxsplit=1)[1]
    except IndexError:
        raise TransmissionDataError(
            "AITRANSS $efermi annotation is invalid"
        ) from None
    _parse_finite(efermi, "tcontrol", 0, "$efermi")
    return ("\n".join((*lines[:annotation_start], "$end")) + "\n").encode(
        "utf-8"
    )


def validate_transmission_grid(
    result: TransmissionResult,
    request: TransmissionRequest,
) -> None:
    """Require a complete start-inclusive grid under either supported endpoint rule.

    Supported AITRANSS builds agree on ``ener`` and ``estep`` but may differ at the
    upper bound: one stops at the final grid point below ``eend`` while another
    includes an on-grid ``eend`` row.  Both are complete results.  Rows beyond
    ``eend`` and results ending before the final permitted point remain invalid.
    """

    if not isinstance(result, TransmissionResult):
        raise TypeError("grid validation requires a TransmissionResult")
    if not isinstance(request, TransmissionRequest):
        raise TypeError("grid validation requires a TransmissionRequest")
    if result.spin_mode is not request.spin_mode:
        raise TransmissionDataError(
            "TE.dat spin header does not match the authoritative tcontrol"
        )
    if (
        request.reported_fermi_energy_hartree is not None
        and not isclose(
            result.fermi_energy_hartree,
            request.reported_fermi_energy_hartree,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
    ):
        raise TransmissionDataError(
            "TE.dat Fermi energy does not match the AITRANSS-annotated tcontrol"
        )
    tolerance = max(abs(request.energy_step_hartree) * 1.0e-7, 1.0e-10)
    for index, point in enumerate(result.points):
        expected = (
            request.energy_start_hartree
            + index * request.energy_step_hartree
        )
        if not isclose(
            point.energy_hartree,
            expected,
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            raise TransmissionDataError(
                "TE.dat energy rows do not reproduce the requested tcontrol grid"
            )
    final_energy = result.points[-1].energy_hartree
    if isclose(
        final_energy,
        request.energy_end_hartree,
        rel_tol=0.0,
        abs_tol=tolerance,
    ):
        return
    if final_energy > request.energy_end_hartree:
        raise TransmissionDataError(
            "TE.dat extends beyond the requested tcontrol endpoint"
        )
    if final_energy + request.energy_step_hartree < (
        request.energy_end_hartree - tolerance
    ):
        raise TransmissionDataError(
            "TE.dat is truncated before the requested tcontrol endpoint"
        )


def _decode_lf_text(value: bytes | str, source_name: str) -> str:
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8")
        except UnicodeError:
            raise TransmissionDataError(
                f"{source_name} must be valid UTF-8"
            ) from None
    elif isinstance(value, str):
        decoded = value
    else:
        raise TypeError(f"{source_name} input must be bytes or text")
    if "\r" in decoded or not decoded.endswith("\n"):
        raise TransmissionDataError(
            f"{source_name} must use LF and end with a newline"
        )
    return decoded


def _parse_finite(
    value: str,
    source_name: str,
    line_number: int,
    field_name: str,
) -> float:
    try:
        parsed = float(value.replace("D", "E").replace("d", "e"))
    except ValueError:
        location = f" line {line_number}" if line_number else ""
        raise TransmissionDataError(
            f"{source_name}{location} has invalid {field_name}"
        ) from None
    if not isfinite(parsed):
        location = f" line {line_number}" if line_number else ""
        raise TransmissionDataError(
            f"{source_name}{location} has non-finite {field_name}"
        )
    return parsed


def _normalize_header(value: str) -> str:
    return " ".join(value.casefold().split())


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"
_METADATA_HEADER = re.compile(
    rf"#bias\s*=\s*(?P<bias>{_NUMBER})\s+V\s+"
    rf"efermi\s*=\s*(?P<fermi>{_NUMBER})\s+H",
    re.IGNORECASE,
)
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TCONTROL_ALLOWED_TAGS = frozenset(
    {
        "aims_input",
        "landauer",
        "coord",
        "natoms",
        "basis",
        "read_omat",
        "scfmo",
        "uhfmo_alpha",
        "uhfmo_beta",
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
        "self_energy",
        "valence_electrons",
        "efermi",
        "end",
    }
)
