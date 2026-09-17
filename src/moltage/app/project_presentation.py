"""Read-only Project Manager presentation derived from recovery snapshots."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
import re
from uuid import UUID

from moltage.app.project_recovery import (
    ProjectRecoverySnapshot,
    StepRuntimeEvidence,
)
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
)


class ProjectSortCriterion(StrEnum):
    FILE_NAME = "File name"
    SUBMISSION_DATE = "Submission date"
    STEP_TYPE = "Step type"


class ProjectViewMode(StrEnum):
    DETAILS = "Details"
    COMPACT = "Compact"
    TILES = "Tiles"


class StepIndicatorKind(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NOT_STARTED = "NOT_STARTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProjectStepIndicator:
    step_number: int
    kind: StepIndicatorKind
    tooltip: str
    step_kind: ProjectStepKind | None = None


@dataclass(frozen=True, slots=True)
class ProjectPresentationRecord:
    """One adapter shared by every Project Manager view and sort mode."""

    snapshot: ProjectRecoverySnapshot
    server_profile_id: UUID
    submitted_at: datetime | None
    indicators: tuple[ProjectStepIndicator, ...]

    @property
    def project_id(self) -> UUID:
        return self.snapshot.project.project_id

    @property
    def display_name(self) -> str:
        return self.snapshot.project.display_name

    @property
    def remote_directory_basename(self) -> str:
        return PurePosixPath(self.snapshot.project.remote_project_path).name

    @property
    def remote_path(self) -> str:
        return self.snapshot.project.remote_project_path

    @property
    def current_step(self) -> ProjectStepKind:
        return self.snapshot.active_step_kind


def project_presentation_record(
    snapshot: ProjectRecoverySnapshot,
    *,
    server_profile_id: UUID,
) -> ProjectPresentationRecord:
    if not isinstance(snapshot, ProjectRecoverySnapshot):
        raise TypeError("project presentation requires a recovery snapshot")
    if not isinstance(server_profile_id, UUID):
        raise TypeError("project presentation requires a server-profile UUID")
    if snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA:
        optimization = next(
            step
            for step in snapshot.project.steps
            if step.kind is ProjectStepKind.ORCA_OPTIMIZATION
        )
        wbl = next(
            (
                step
                for step in snapshot.project.steps
                if step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
            ),
            None,
        )
        indicators = (
            _step_indicator(snapshot, 1, optimization.state, optimization.kind),
            (
                _step_indicator(snapshot, 2, wbl.state, wbl.kind)
                if wbl is not None
                else ProjectStepIndicator(
                    2,
                    StepIndicatorKind.NOT_STARTED,
                    "ORCA WBL transmission — NOT_STARTED",
                    ProjectStepKind.ORCA_WBL_TRANSMISSION,
                )
            ),
        )
    else:
        indicators = tuple(
            _step_indicator(snapshot, index, step.state, step.kind)
            for index, step in enumerate(snapshot.project.steps, start=1)
        )
    return ProjectPresentationRecord(
        snapshot=snapshot,
        server_profile_id=server_profile_id,
        submitted_at=project_submission_timestamp(snapshot),
        indicators=indicators,
    )


def project_submission_timestamp(
    snapshot: ProjectRecoverySnapshot,
) -> datetime | None:
    """Return the earliest authoritative submission across steps and attempts."""

    timestamps = [
        timestamp
        for step in snapshot.project.steps
        for timestamp in (
            step.submitted_at,
            *(attempt.submitted_at for attempt in step.attempts),
        )
        if timestamp is not None
    ]
    return min(timestamps) if timestamps else None


def format_project_submission_timestamp(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("project submission timestamp must be timezone-aware")
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def sort_project_presentations(
    records: tuple[ProjectPresentationRecord, ...],
    criterion: ProjectSortCriterion,
    *,
    reversed_order: bool = False,
) -> tuple[ProjectPresentationRecord, ...]:
    """Apply one deterministic total order, optionally reversing it in full."""

    try:
        checked_criterion = ProjectSortCriterion(criterion)
    except (TypeError, ValueError):
        raise ValueError("unsupported Project Manager sort criterion") from None
    checked = tuple(records)
    if any(not isinstance(item, ProjectPresentationRecord) for item in checked):
        raise TypeError("project sort requires presentation records")
    key = {
        ProjectSortCriterion.FILE_NAME: _file_name_key,
        ProjectSortCriterion.SUBMISSION_DATE: _submission_date_key,
        ProjectSortCriterion.STEP_TYPE: _step_type_key,
    }[checked_criterion]
    ordered = tuple(sorted(checked, key=key))
    return tuple(reversed(ordered)) if reversed_order else ordered


def _file_name_key(record: ProjectPresentationRecord) -> tuple[object, ...]:
    return (
        _natural_key(record.remote_directory_basename),
        *_descending_timestamp_key(record.submitted_at),
        str(record.project_id),
    )


def _submission_date_key(record: ProjectPresentationRecord) -> tuple[object, ...]:
    return (
        *_descending_timestamp_key(record.submitted_at),
        _natural_key(record.remote_directory_basename),
        str(record.project_id),
    )


def _step_type_key(record: ProjectPresentationRecord) -> tuple[object, ...]:
    step_order = {
        ProjectStepKind.MOLECULE_OPT: 0,
        ProjectStepKind.MOLECULE_AU_OPT: 1,
        ProjectStepKind.TRANSPORT_CONVERGENCE: 2,
        ProjectStepKind.TRANSMISSION: 3,
        ProjectStepKind.ORCA_OPTIMIZATION: 4,
        ProjectStepKind.ORCA_WBL_TRANSMISSION: 5,
        ProjectStepKind.ORCA_FREQUENCY: 6,
    }
    return (
        step_order[record.current_step],
        *_descending_timestamp_key(record.submitted_at),
        _natural_key(record.remote_directory_basename),
        str(record.project_id),
    )


def _descending_timestamp_key(value: datetime | None) -> tuple[int, float]:
    return (1, 0.0) if value is None else (0, -value.timestamp())


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if index % 2 else part.casefold()
        for index, part in enumerate(re.split(r"(\d+)", value))
    )


def _step_indicator(
    snapshot: ProjectRecoverySnapshot,
    step_number: int,
    state: ProjectStepState,
    step_kind: ProjectStepKind,
) -> ProjectStepIndicator:
    indicator_kind = {
        ProjectStepState.SUCCEEDED: StepIndicatorKind.SUCCEEDED,
        ProjectStepState.SKIPPED: StepIndicatorKind.SKIPPED,
        ProjectStepState.QUEUED: StepIndicatorKind.ACTIVE,
        ProjectStepState.RUNNING: StepIndicatorKind.ACTIVE,
        ProjectStepState.FAILED: StepIndicatorKind.FAILED,
        ProjectStepState.NOT_STARTED: StepIndicatorKind.NOT_STARTED,
        ProjectStepState.SCHEDULER_COMPLETED: StepIndicatorKind.UNKNOWN,
        ProjectStepState.UNKNOWN: StepIndicatorKind.UNKNOWN,
    }[state]
    if snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA:
        step = next(item for item in snapshot.project.steps if item.kind is step_kind)
        stage_kind = step_kind
        label = {
            ProjectStepKind.ORCA_OPTIMIZATION: "ORCA optimization",
            ProjectStepKind.ORCA_WBL_TRANSMISSION: "ORCA WBL transmission",
            ProjectStepKind.ORCA_FREQUENCY: "ORCA frequency",
        }[stage_kind]
        tooltip = f"{label} — {state.value}"
        if step.scheduler_state is not None:
            tooltip += f" — scheduler {step.scheduler_state}"
        if state is ProjectStepState.FAILED and step.scheduler_state == "CANCELLED":
            indicator_kind = StepIndicatorKind.CANCELLED
    else:
        tooltip = f"Step {step_number} — {state.value}"
    if (
        step_number == 3
        and state is ProjectStepState.RUNNING
        and snapshot.runtime_evidence is StepRuntimeEvidence.TASK_OOM_DETECTED
    ):
        tooltip += " — OOM detected"
    return ProjectStepIndicator(step_number, indicator_kind, tooltip, step_kind)
