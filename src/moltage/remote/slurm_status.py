"""Strict one-shot Slurm status queries using absolute command paths."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
import shlex

from moltage.remote.executor import RemoteCommandResult, RemoteExecutor
from moltage.remote.slurm_discovery import (
    validate_scheduler_command_path,
    validate_slurm_command_path,
)
from moltage.remote.lsf_environment import render_lsf_client_command


class SlurmStatusError(RuntimeError):
    """Base error for an unusable scheduler-status result."""


class SlurmStatusQueryError(SlurmStatusError):
    """Raised when a normal scheduler command result reports failure."""


class SlurmStatusParseError(SlurmStatusError):
    """Raised when parsable scheduler output is malformed or ambiguous."""


class SchedulerStatusKind(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ACCOUNTING_PENDING = "ACCOUNTING_PENDING"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class SlurmJobStatus:
    kind: SchedulerStatusKind
    scheduler_state: str | None = None
    exit_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SchedulerStatusKind):
            raise TypeError("scheduler status kind is unsupported")
        if self.scheduler_state is not None and (
            not isinstance(self.scheduler_state, str)
            or _STATE.fullmatch(self.scheduler_state) is None
        ):
            raise ValueError("scheduler state is malformed")
        if self.exit_code is not None and (
            not isinstance(self.exit_code, str)
            or _EXIT_CODE.fullmatch(self.exit_code) is None
        ):
            raise ValueError("scheduler exit code is malformed")


def validate_job_id(job_id: object) -> str:
    """Accept only the decimal identifier produced by the sbatch parser."""

    if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
        raise SlurmStatusError("Scheduler status requires a decimal job ID")
    return job_id


def build_squeue_status_command(
    squeue_path: str,
    profile_username: str,
) -> str:
    """Build the fixed parsable query for one profile user's active jobs."""

    executable = validate_slurm_command_path(squeue_path, "squeue")
    return (
        f"{shlex.quote(executable)} --noheader "
        f"--user={shlex.quote(profile_username)} "
        f"--format={shlex.quote('%i|%T')}"
    )


def build_sacct_status_command(sacct_path: str, job_id: str) -> str:
    """Build the fixed parsable accounting query."""

    executable = validate_slurm_command_path(sacct_path, "sacct")
    normalized_job_id = validate_job_id(job_id)
    return (
        f"{shlex.quote(executable)} --noheader --parsable2 "
        f"--jobs={normalized_job_id} "
        "--format=JobIDRaw,State,ExitCode"
    )


def query_slurm_job_status(
    executor: RemoteExecutor,
    *,
    squeue_path: str,
    sacct_path: str,
    job_id: str,
    profile_username: str,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> SlurmJobStatus:
    """Query the configured Slurm or LSF scheduler without fuzzy matching."""

    if PurePosixPath(squeue_path).name == "bjobs":
        return query_lsf_job_status(
            executor,
            bjobs_path=squeue_path,
            bhist_path=sacct_path,
            job_id=job_id,
            profile_username=profile_username,
            lsf_env_directory=lsf_env_directory,
            lsf_library_directory=lsf_library_directory,
            lsf_server_directory=lsf_server_directory,
        )

    normalized_job_id = validate_job_id(job_id)
    squeue = executor.execute(
        build_squeue_status_command(squeue_path, profile_username)
    )
    _require_successful_result(squeue, "squeue")
    active = parse_squeue_output(squeue.stdout, normalized_job_id)
    if active is not None:
        return active

    sacct = executor.execute(
        build_sacct_status_command(sacct_path, normalized_job_id)
    )
    _require_successful_result(sacct, "sacct")
    return parse_sacct_output(sacct.stdout, normalized_job_id)


def build_bjobs_status_command(
    bjobs_path: str,
    job_id: str,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> str:
    """Build one bounded machine-readable query for the exact LSF job."""

    executable = validate_scheduler_command_path(bjobs_path, "bjobs")
    normalized_job_id = validate_job_id(job_id)
    query_format = "jobid stat delimiter='|'"
    command = (
        f"{shlex.quote(executable)} -a -noheader "
        f"-o {shlex.quote(query_format)} {normalized_job_id}"
    )
    if any(
        value is None
        for value in (
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    ):
        raise SlurmStatusError(
            "LSF status requires the configuration, library, and server directories"
        )
    try:
        return render_lsf_client_command(
            command,
            env_directory=lsf_env_directory,
            bin_directory=str(PurePosixPath(executable).parent),
            library_directory=lsf_library_directory,
            server_directory=lsf_server_directory,
        )
    except (TypeError, ValueError) as error:
        raise SlurmStatusError(str(error)) from None


def build_bhist_status_command(
    bhist_path: str,
    job_id: str,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> str:
    """Build one bounded LSF event-history query for the requested job.

    LSF rotates ``lsb.events``.  Without ``-n``, ``bhist`` may therefore lose a
    job at the first event-log switch even though its history is still retained
    in the immediately preceding files.  Search a fixed, bounded number of
    event logs while continuing to select only the exact persisted Job ID.
    """

    executable = validate_scheduler_command_path(bhist_path, "bhist")
    normalized_job_id = validate_job_id(job_id)
    if any(
        value is None
        for value in (
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    ):
        raise SlurmStatusError(
            "LSF history requires the configuration, library, and server directories"
        )
    try:
        return render_lsf_client_command(
            f"{shlex.quote(executable)} -n {_LSF_HISTORY_LOG_LIMIT} "
            f"-l {normalized_job_id}",
            env_directory=lsf_env_directory,
            bin_directory=str(PurePosixPath(executable).parent),
            library_directory=lsf_library_directory,
            server_directory=lsf_server_directory,
        )
    except (TypeError, ValueError) as error:
        raise SlurmStatusError(str(error)) from None


def query_lsf_job_status(
    executor: RemoteExecutor,
    *,
    bjobs_path: str,
    bhist_path: str,
    job_id: str,
    profile_username: str,
    lsf_env_directory: str | None,
    lsf_library_directory: str | None,
    lsf_server_directory: str | None,
) -> SlurmJobStatus:
    """Use bjobs for live state, then bhist for an exact terminal cause."""

    normalized_job_id = validate_job_id(job_id)
    bjobs = executor.execute(
        build_bjobs_status_command(
            bjobs_path,
            normalized_job_id,
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    )
    if bjobs.exit_status != 0:
        if not _is_lsf_not_found(bjobs.stdout, bjobs.stderr):
            _require_successful_result(bjobs, "bjobs")
        current = None
    else:
        current = parse_bjobs_output(bjobs.stdout, normalized_job_id)
    if current is not None and current.kind is not SchedulerStatusKind.FAILED:
        return current

    bhist = executor.execute(
        build_bhist_status_command(
            bhist_path,
            normalized_job_id,
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    )
    if bhist.exit_status != 0:
        if _is_lsf_not_found(bhist.stdout, bhist.stderr):
            return current or SlurmJobStatus(SchedulerStatusKind.ACCOUNTING_PENDING)
        _require_successful_result(bhist, "bhist")
    historical = parse_bhist_output(bhist.stdout, normalized_job_id)
    if historical.kind is SchedulerStatusKind.ACCOUNTING_PENDING and current is not None:
        return current
    return historical


def parse_bjobs_output(output: str | bytes, job_id: str) -> SlurmJobStatus | None:
    """Parse the exact LSF JobID from current two- or legacy three-field rows."""

    normalized_job_id = validate_job_id(job_id)
    if _is_lsf_not_found(output, b""):
        return None
    lines = _nonempty_lines(output, "bjobs")
    target = None
    for line in lines:
        fields = line.split("|")
        if len(fields) not in {2, 3}:
            raise SlurmStatusParseError(
                "bjobs output is not JobID|Status"
            )
        row_job_id, state = fields[:2]
        exit_code = fields[2] if len(fields) == 3 else ""
        if _JOB_ID.fullmatch(row_job_id) is None:
            raise SlurmStatusParseError("bjobs job ID is malformed")
        _validate_state(state, "bjobs")
        if exit_code not in {"", "-"} and _LSF_EXIT_CODE.fullmatch(exit_code) is None:
            raise SlurmStatusParseError("bjobs exit code is malformed")
        if row_job_id != normalized_job_id:
            continue
        if target is not None:
            raise SlurmStatusParseError(
                "bjobs returned multiple rows for the target non-array job"
            )
        if state == "PEND":
            kind = SchedulerStatusKind.QUEUED
        elif state == "RUN":
            kind = SchedulerStatusKind.RUNNING
        elif state == "DONE":
            kind = SchedulerStatusKind.COMPLETED
        elif state == "EXIT":
            kind = SchedulerStatusKind.FAILED
        else:
            kind = SchedulerStatusKind.UNRESOLVED
        target = SlurmJobStatus(
            kind,
            state,
            (
                "0:0"
                if state == "DONE"
                else None if exit_code in {"", "-"} else exit_code
            ),
        )
    return target


def parse_bhist_output(output: str | bytes, job_id: str) -> SlurmJobStatus:
    """Parse one exact LSF history record and preserve its terminal cause."""

    normalized_job_id = validate_job_id(job_id)
    text = _decoded_text(output, "bhist")
    observed_ids = tuple(_BHIST_JOB_ID.findall(text))
    if not observed_ids:
        return SlurmJobStatus(SchedulerStatusKind.ACCOUNTING_PENDING)
    if any(item != normalized_job_id for item in observed_ids):
        raise SlurmStatusParseError(
            "bhist returned a record outside the requested job"
        )

    terminal_events = [
        (match.start(), match.group("status").lower())
        for match in _BHIST_COMPLETION.finditer(text)
    ]
    terminal_events.extend(
        (match.start(), "done") for match in _BHIST_DONE.finditer(text)
    )
    if not terminal_events:
        return SlurmJobStatus(SchedulerStatusKind.ACCOUNTING_PENDING)
    position, terminal = max(terminal_events, key=lambda item: item[0])
    if terminal == "done":
        return SlurmJobStatus(
            SchedulerStatusKind.COMPLETED,
            "DONE",
            "0:0",
        )

    terminal_text = text[position:]
    if _BHIST_RUN_LIMIT.search(terminal_text) is not None:
        state = "TIMEOUT"
    elif _BHIST_MEMORY_LIMIT.search(terminal_text) is not None:
        state = "OUT_OF_MEMORY"
    elif _BHIST_CANCELLED.search(terminal_text) is not None:
        state = "CANCELLED"
    else:
        state = "EXIT"
    exit_codes = tuple(_BHIST_EXIT_CODE.findall(text[: position + 1]))
    exit_code = f"{exit_codes[-1]}:0" if exit_codes else None
    return SlurmJobStatus(
        SchedulerStatusKind.FAILED,
        state,
        exit_code,
    )


def parse_squeue_output(
    output: str | bytes,
    job_id: str,
) -> SlurmJobStatus | None:
    """Select the exact target from strict per-user `JobID|State` rows."""

    normalized_job_id = validate_job_id(job_id)
    lines = _nonempty_lines(output, "squeue")
    target: SlurmJobStatus | None = None
    for line in lines:
        fields = line.split("|")
        if len(fields) != 2:
            raise SlurmStatusParseError("squeue output is not JobID|State")
        row_job_id, state = fields
        if _SQUEUE_JOB_ID.fullmatch(row_job_id) is None:
            raise SlurmStatusParseError("squeue job ID is malformed")
        _validate_state(state, "squeue")
        if row_job_id != normalized_job_id:
            continue
        if target is not None:
            raise SlurmStatusParseError(
                "squeue returned multiple rows for the target non-array job"
            )
        if state in _QUEUED_STATES:
            kind = SchedulerStatusKind.QUEUED
        elif state in _RUNNING_STATES:
            kind = SchedulerStatusKind.RUNNING
        else:
            kind = SchedulerStatusKind.UNRESOLVED
        target = SlurmJobStatus(kind, state)
    return target


def parse_sacct_output(output: str | bytes, job_id: str) -> SlurmJobStatus:
    """Select exactly the parent JobIDRaw row and ignore its step rows."""

    normalized_job_id = validate_job_id(job_id)
    parents: list[tuple[str, str]] = []
    for line in _nonempty_lines(output, "sacct"):
        fields = line.split("|")
        if len(fields) != 3:
            raise SlurmStatusParseError(
                "sacct output is not JobIDRaw|State|ExitCode"
        )
        row_job_id, state, exit_code = fields
        if row_job_id == normalized_job_id:
            state = _canonical_sacct_state(state)
            if _EXIT_CODE.fullmatch(exit_code) is None:
                raise SlurmStatusParseError("sacct parent exit code is malformed")
            parents.append((state, exit_code))
        elif row_job_id.startswith(normalized_job_id + "."):
            continue
        else:
            raise SlurmStatusParseError(
                "sacct returned a row outside the requested parent job"
            )

    if not parents:
        return SlurmJobStatus(SchedulerStatusKind.ACCOUNTING_PENDING)
    if len(parents) != 1:
        raise SlurmStatusParseError(
            "sacct returned multiple parent rows for one non-array job"
        )
    state, exit_code = parents[0]
    if state == "COMPLETED":
        kind = SchedulerStatusKind.COMPLETED
    elif state in _FAILED_STATES:
        kind = SchedulerStatusKind.FAILED
    else:
        kind = SchedulerStatusKind.UNRESOLVED
    return SlurmJobStatus(kind, state, exit_code)


def _require_successful_result(
    result: RemoteCommandResult,
    command_name: str,
) -> None:
    if not isinstance(result, RemoteCommandResult):
        raise SlurmStatusQueryError(
            f"{command_name} returned an invalid command result"
        )
    if result.exit_status != 0:
        raise SlurmStatusQueryError(
            f"{command_name} status query returned a nonzero status"
        )


def _nonempty_lines(output: str | bytes, source: str) -> tuple[str, ...]:
    if isinstance(output, bytes):
        try:
            output = output.decode("utf-8")
        except UnicodeError:
            raise SlurmStatusParseError(
                f"{source} output is not valid UTF-8"
            ) from None
    if not isinstance(output, str):
        raise SlurmStatusParseError(f"{source} output must be text or bytes")
    lines: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        if line != line.strip():
            raise SlurmStatusParseError(
                f"{source} parsable output contains surrounding whitespace"
            )
        lines.append(line)
    return tuple(lines)


def _decoded_text(output: str | bytes, source: str) -> str:
    if isinstance(output, bytes):
        try:
            return output.decode("utf-8")
        except UnicodeError:
            raise SlurmStatusParseError(
                f"{source} output is not valid UTF-8"
            ) from None
    if not isinstance(output, str):
        raise SlurmStatusParseError(f"{source} output must be text or bytes")
    return output


def _is_lsf_not_found(stdout: str | bytes, stderr: str | bytes) -> bool:
    text = "\n".join(
        part.decode("utf-8", errors="replace")
        if isinstance(part, bytes)
        else part
        for part in (stdout, stderr)
        if isinstance(part, (str, bytes))
    )
    return bool(
        re.search(
            r"(?:\bNo (?:unfinished |matching )?jobs? found\b|"
            r"\bJob <[0-9]+> is not found\b)",
            text,
            re.I,
        )
    )


def _validate_state(state: str, source: str) -> None:
    if _STATE.fullmatch(state) is None:
        raise SlurmStatusParseError(f"{source} scheduler state is malformed")


def _canonical_sacct_state(state: str) -> str:
    """Canonicalize only the observed Slurm cancellation decoration."""

    if _SACCT_CANCELLED_BY_UID.fullmatch(state) is not None:
        return "CANCELLED"
    _validate_state(state, "sacct")
    return state


_JOB_ID = re.compile(r"[0-9]+")
_SQUEUE_JOB_ID = re.compile(r"[^\s|]+")
_STATE = re.compile(r"[A-Z][A-Z_]*")
_SACCT_CANCELLED_BY_UID = re.compile(r"CANCELLED by [0-9]+")
_EXIT_CODE = re.compile(r"[0-9]+(?::[0-9]+)?")
_LSF_EXIT_CODE = re.compile(r"[0-9]+")
_LSF_HISTORY_LOG_LIMIT = 10
_BHIST_JOB_ID = re.compile(r"\bJob <([0-9]+)>")
_BHIST_COMPLETION = re.compile(
    r"\bCompleted <(?P<status>done|exit)>", re.I
)
_BHIST_DONE = re.compile(r"\bDone successfully\.", re.I)
_BHIST_EXIT_CODE = re.compile(r"\bExited with exit code ([0-9]+)\b", re.I)
_BHIST_RUN_LIMIT = re.compile(r"\bTERM_RUNLIMIT\b")
_BHIST_MEMORY_LIMIT = re.compile(r"\bTERM_MEMLIMIT\b")
_BHIST_CANCELLED = re.compile(r"\bTERM_(?:OWNER|ADMIN|FORCE_ADMIN)\b")
_QUEUED_STATES = frozenset(
    {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED"}
)
_RUNNING_STATES = frozenset({"RUNNING", "COMPLETING", "STAGE_OUT"})
_FAILED_STATES = frozenset(
    {
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "OUT_OF_MEMORY",
        "NODE_FAIL",
        "BOOT_FAIL",
        "DEADLINE",
        "PREEMPTED",
        "REVOKED",
    }
)
