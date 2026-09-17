"""Narrow, evidence-based classification of terminal AITRANSS output."""

from enum import StrEnum
import re


AITRANSS_OUTPUT_TAIL_MAX_BYTES = 256 * 1024
_SAFE_RESULT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class AitranssFailureCode(StrEnum):
    """Reviewed fatal conditions established from terminal AITRANSS output."""

    ELECTRODE_INTERFACE_OVERLAP = "AITRANSS_ELECTRODE_INTERFACE_OVERLAP"
    SELF_ENERGY_FILE_FORMAT_ERROR = "AITRANSS_SELF_ENERGY_FILE_FORMAT_ERROR"


AITRANSS_ELECTRODE_INTERFACE_OVERLAP_REASON = (
    "AITRANSS 电极界面区域识别重叠"
)
AITRANSS_ELECTRODE_INTERFACE_OVERLAP_DETAIL = (
    "AITRANSS 在构造 self-energy 时将左右电极的界面区域"
    "判定为包含相同原子。"
)
AITRANSS_SELF_ENERGY_FILE_FORMAT_REASON = (
    "AITRANSS 无法读取显式 self-energy 文件"
)
AITRANSS_SELF_ENERGY_FILE_FORMAT_DETAIL = (
    "self.energy 输入文件格式与当前 AITRANSS 版本不兼容。"
)


_OVERLAP_SEQUENCE = (
    re.compile(r"^build up a self-energy(?:\s*>+)?$"),
    re.compile(r"^left electrode,\s*surface atoms:\s*\S+$"),
    re.compile(r"^right electrode,\s*surface atoms:\s*\S+$"),
    re.compile(r"^your left and right electrodes share the same atoms:\s*$"),
    re.compile(r"^please,\s*check your <tcontrol> file \.\.\.$"),
    re.compile(r"^stop\s*:\s*transport module will terminate now!$"),
)

_SELF_ENERGY_FORMAT_SEQUENCE = (
    re.compile(
        r"^=== reading file <self\.energy[a-z0-9._-]*> ===$"
    ),
    re.compile(
        r"^stop \[sub\. get_self_energy_data\]: "
        r"wrong format of the self-energy file$"
    ),
)


def classify_aitranss_fatal_output(
    output: bytes | str,
) -> AitranssFailureCode | None:
    """Return a typed fatal only for the reviewed ordered semantic sequence."""

    if isinstance(output, bytes):
        text = output.decode("utf-8", errors="replace")
    elif isinstance(output, str):
        text = output
    else:
        raise TypeError("AITRANSS output must be bytes or text")

    normalized_lines = tuple(
        normalized
        for raw_line in text.splitlines()
        if (normalized := " ".join(raw_line.strip().casefold().split()))
    )
    if _contains_ordered_sequence(
        normalized_lines,
        _SELF_ENERGY_FORMAT_SEQUENCE,
    ):
        return AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR
    if _contains_ordered_sequence(normalized_lines, _OVERLAP_SEQUENCE):
        return AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP
    return None


def has_aitranss_transmission_success(
    output: bytes | str,
    *,
    expected_output_filename: str,
) -> bool:
    """Require both supported positive completion markers in order."""

    if (
        not isinstance(expected_output_filename, str)
        or _SAFE_RESULT_FILENAME.fullmatch(expected_output_filename) is None
    ):
        raise ValueError("expected AITRANSS result filename is unsafe")
    if classify_aitranss_fatal_output(output) is not None:
        return False
    if isinstance(output, bytes):
        text = output.decode("utf-8", errors="replace")
    elif isinstance(output, str):
        text = output
    else:
        raise TypeError("AITRANSS output must be bytes or text")
    lines = tuple(
        normalized
        for raw_line in text.splitlines()
        if (normalized := " ".join(raw_line.strip().split()))
    )
    written = f'transmission is written to a file "{expected_output_filename}"'
    done = "** aitranss : all done **"
    try:
        written_index = lines.index(written)
    except ValueError:
        return False
    return done in lines[written_index + 1 :]


def aitranss_failure_reason(
    code: AitranssFailureCode | str | None,
) -> str | None:
    """Map a persisted typed code to its concise Windows-GUI reason."""

    if code == AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP:
        return AITRANSS_ELECTRODE_INTERFACE_OVERLAP_REASON
    if code == AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR:
        return AITRANSS_SELF_ENERGY_FILE_FORMAT_REASON
    return None


def aitranss_failure_detail(
    code: AitranssFailureCode | str | None,
) -> str | None:
    """Map a persisted typed code to its reviewed explanatory detail."""

    if code == AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP:
        return AITRANSS_ELECTRODE_INTERFACE_OVERLAP_DETAIL
    if code == AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR:
        return AITRANSS_SELF_ENERGY_FILE_FORMAT_DETAIL
    return None


def _contains_ordered_sequence(
    lines: tuple[str, ...],
    sequence: tuple[re.Pattern[str], ...],
) -> bool:
    expected_index = 0
    for line in lines:
        if sequence[expected_index].fullmatch(line):
            expected_index += 1
            if expected_index == len(sequence):
                return True
    return False
