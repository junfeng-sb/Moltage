"""Strict bounded parsers for FHI-aims Step-3 transport evidence."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
import re


AIMS_OUTPUT_HEAD_MAX_BYTES = 2 * 1024 * 1024
AIMS_OUTPUT_TAIL_MAX_BYTES = 256 * 1024
ORBITAL_HEADER_MAX_BYTES = 64 * 1024
NORMAL_TERMINATION_MARKER = "Have a nice day."
TIMEOUT_REASON = "运行时间到达设定上限"
OUT_OF_MEMORY_REASON = "任务因内存不足终止"
SLURM_TASK_OUT_OF_MEMORY = "SLURM_TASK_OUT_OF_MEMORY"


class TransportEvidenceError(ValueError):
    """Raised when bounded Step-3 evidence is malformed or insufficient."""


class TransportSpinMode(StrEnum):
    NONE = "none"
    COLLINEAR = "collinear"


@dataclass(frozen=True, slots=True)
class TransportCompletionEvidence:
    """Validated scalar evidence needed for recovery and tcontrol defaults."""

    natoms: int
    nsaos: int
    spin_mode: TransportSpinMode
    geometry_sha256: str

    def __post_init__(self) -> None:
        for name in ("natoms", "nsaos"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise TransportEvidenceError(f"{name} must be a positive integer")
        if not isinstance(self.spin_mode, TransportSpinMode):
            raise TransportEvidenceError("Step-3 spin mode is invalid")
        if _SHA256.fullmatch(self.geometry_sha256) is None:
            raise TransportEvidenceError("geometry SHA256 is invalid")

    @property
    def spin_polarized(self) -> bool:
        return self.spin_mode is TransportSpinMode.COLLINEAR

    @property
    def orbital_filenames(self) -> tuple[str, ...]:
        if self.spin_polarized:
            return ("alpha.aims", "beta.aims")
        return ("mos.aims",)


def has_exact_normal_termination(output_tail: str | bytes) -> bool:
    """Match the accepted FHI-aims marker as one complete stripped line."""

    text = _decode(output_tail, "FHI-aims output tail")
    return any(line.strip() == NORMAL_TERMINATION_MARKER for line in text.splitlines())


def parse_aims_natoms(output_head: str | bytes) -> int:
    """Parse the supported FHI-aims atom-count line."""

    text = _decode(output_head, "FHI-aims output header")
    matches = tuple(int(match.group("count")) for match in _NATOMS.finditer(text))
    if not matches:
        raise TransportEvidenceError(
            "Step-3 atom count was not found in the bounded FHI-aims output header"
        )
    if any(value <= 0 for value in matches):
        raise TransportEvidenceError("Step-3 atom count must be positive")
    if len(set(matches)) != 1:
        raise TransportEvidenceError(
            "Step-3 output contains conflicting atom-count values"
        )
    return matches[0]


def parse_aims_nsaos(orbital_head: str | bytes, *, source_name: str) -> int:
    """Parse positive NSAOS from a bounded mos/alpha/beta header."""

    text = _decode(orbital_head, source_name)
    match = _NSAOS.search(text)
    if match is None:
        raise TransportEvidenceError(
            f"Step-3 NSAOS was not found in the bounded {source_name} header"
        )
    value = int(match.group("count"))
    if value <= 0:
        raise TransportEvidenceError(f"Step-3 NSAOS in {source_name} must be positive")
    return value


def parse_transport_spin_mode(control_text: str | bytes) -> TransportSpinMode:
    """Read the single active frozen `spin` directive from control.in."""

    text = _decode(control_text, "control.in")
    values: list[str] = []
    for line in text.splitlines():
        active = line.split("#", 1)[0].strip()
        if not active:
            continue
        fields = active.split()
        if fields[0].casefold() == "spin":
            if len(fields) != 2:
                raise TransportEvidenceError("control.in spin directive is malformed")
            values.append(fields[1].casefold())
    if len(values) != 1:
        raise TransportEvidenceError(
            "control.in must contain exactly one active spin directive"
        )
    try:
        return TransportSpinMode(values[0])
    except ValueError:
        raise TransportEvidenceError(
            f"control.in uses unsupported Step-3 spin mode: {values[0]}"
        ) from None


def has_slurm_timeout_signature(output_tail: str | bytes) -> bool:
    """Recognize only the supported Slurm timeout line shape."""

    text = _decode(output_tail, "FHI-aims output tail")
    return any(_SLURM_TIMEOUT.fullmatch(line.strip()) is not None for line in text.splitlines())


def has_slurm_task_oom_signature(output_tail: str | bytes) -> bool:
    """Require both supported Slurm task-OOM line shapes."""

    text = _decode(output_tail, "FHI-aims output tail")
    lines = tuple(line.strip() for line in text.splitlines())
    return any(_SLURM_OOM_EVENT.fullmatch(line) is not None for line in lines) and any(
        _SLURM_SRUN_OOM.fullmatch(line) is not None for line in lines
    )


def parse_fortran_real(value: str, *, field_name: str) -> float:
    """Parse finite decimal/E/D notation used by AITRANSS inputs."""

    if not isinstance(value, str) or not value.strip():
        raise TransportEvidenceError(f"{field_name} must not be empty")
    try:
        number = float(value.strip().replace("D", "E").replace("d", "e"))
    except ValueError:
        raise TransportEvidenceError(f"{field_name} must be numeric") from None
    if not isfinite(number):
        raise TransportEvidenceError(f"{field_name} must be finite")
    return number


def _decode(value: str | bytes, source_name: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeError:
            raise TransportEvidenceError(f"{source_name} is not valid UTF-8") from None
    if not isinstance(value, str):
        raise TransportEvidenceError(f"{source_name} must be text or bytes")
    return value


_NATOMS = re.compile(
    r"^[ \t]*\|[ \t]*Number of atoms[ \t]*:[ \t]*(?P<count>[0-9]+)[ \t]*$",
    re.MULTILINE,
)
_NSAOS = re.compile(r"\bnsaos[ \t]*=[ \t]*(?P<count>[0-9]+)\b", re.IGNORECASE)
_SLURM_TIMEOUT = re.compile(
    r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\] error: \*\*\* "
    r"(?:STEP [0-9]+\.[0-9]+|JOB [0-9]+) ON [A-Za-z0-9._-]+ "
    r"CANCELLED AT \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} DUE TO TIME LIMIT \*\*\*"
)
_SLURM_OOM_EVENT = re.compile(
    r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\] error: Detected "
    r"[1-9][0-9]* oom_kill events? in StepId=[0-9]+\.[A-Za-z0-9_+-]+\. "
    r"Some of the step tasks have been OOM Killed\."
)
_SLURM_SRUN_OOM = re.compile(
    r"srun: error: [A-Za-z0-9._-]+: task [0-9]+: Out Of Memory"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
