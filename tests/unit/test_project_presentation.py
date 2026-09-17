from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID

from moltage.app.project_planning import create_initial_project
from moltage.app.project_presentation import (
    ProjectSortCriterion,
    ProjectViewMode,
    StepIndicatorKind,
    format_project_submission_timestamp,
    project_presentation_record,
    project_submission_timestamp,
    sort_project_presentations,
)
from moltage.app.project_recovery import (
    ProjectRecoverySnapshot,
    StepRuntimeEvidence,
)
from moltage.domain.calculation_project import (
    ProjectStepAttempt,
    ProjectStepKind,
    ProjectStepState,
)
from electrode_test_support import synthetic_project_electrode_provenance


PROFILE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
UTC = timezone.utc


def _snapshot(
    *,
    sequence: int,
    remote_name: str,
    active_step: ProjectStepKind,
    states: tuple[ProjectStepState, ...],
    submitted_at: datetime | None = None,
    step_timestamps: tuple[datetime | None, ...] | None = None,
    attempts: tuple[tuple[ProjectStepAttempt, ...], ...] | None = None,
    runtime_evidence: StepRuntimeEvidence | None = None,
) -> ProjectRecoverySnapshot:
    if len(states) != 4:
        raise AssertionError("presentation fixture requires four states")
    if states[:2] == (ProjectStepState.SKIPPED, ProjectStepState.SKIPPED):
        starting_step = ProjectStepKind.TRANSPORT_CONVERGENCE
        electrode_provenance = synthetic_project_electrode_provenance()
    elif states[0] is ProjectStepState.SKIPPED:
        starting_step = ProjectStepKind.MOLECULE_AU_OPT
        electrode_provenance = ()
    else:
        starting_step = ProjectStepKind.MOLECULE_OPT
        electrode_provenance = ()
    if any(
        state not in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}
        for state in states[2:]
    ):
        electrode_provenance = synthetic_project_electrode_provenance()
    created_at = datetime(2030, 8, 1, tzinfo=UTC)
    project = create_initial_project(
        base_name=f"Display{sequence}",
        remote_directory_name=remote_name,
        source_molecule_name=f"Molecule{sequence}.xyz",
        server_profile_id=PROFILE_ID,
        remote_project_root="/workspace",
        starting_step=starting_step,
        now=created_at,
        project_id=UUID(int=sequence),
        electrode_provenance=electrode_provenance,
    )
    timestamps = (
        step_timestamps
        if step_timestamps is not None
        else tuple(submitted_at for _ in range(4))
    )
    histories = attempts or ((), (), (), ())
    if len(timestamps) != 4 or len(histories) != 4:
        raise AssertionError("presentation fixture requires four timestamp histories")
    steps = tuple(
        replace(
            step,
            state=state,
            submitted_at=timestamp,
            attempts=history,
        )
        for step, state, timestamp, history in zip(
            project.steps,
            states,
            timestamps,
            histories,
            strict=True,
        )
    )
    project = replace(project, steps=steps)
    return ProjectRecoverySnapshot(
        project=project,
        active_step_kind=active_step,
        status_message=f"fixture {sequence}",
        runtime_evidence=runtime_evidence,
    )


def _record(snapshot: ProjectRecoverySnapshot):
    return project_presentation_record(snapshot, server_profile_id=PROFILE_ID)


class ProjectPresentationTests(unittest.TestCase):
    def test_supported_sort_and_view_choices_are_exact(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ProjectSortCriterion),
            ("File name", "Submission date", "Step type"),
        )
        self.assertEqual(
            tuple(item.value for item in ProjectViewMode),
            ("Details", "Compact", "Tiles"),
        )

    def test_submission_timestamp_is_earliest_authoritative_step_or_attempt(self) -> None:
        first_submission = datetime(2030, 8, 20, 10, tzinfo=UTC)
        historical_attempt = datetime(2030, 8, 25, 10, tzinfo=UTC)
        current_retry = datetime(2030, 8, 30, 10, tzinfo=UTC)
        attempt = ProjectStepAttempt(
            job_id="123",
            submitted_at=historical_attempt,
            finished_at=datetime(2030, 8, 25, 11, tzinfo=UTC),
            terminal_scheduler_state="TIMEOUT",
            failure_reason="TIMEOUT",
            submit_script_filename="submit.sh",
            slurm_output_filename="aims.dft.out",
        )
        snapshot = _snapshot(
            sequence=1,
            remote_name="old-project.20300820",
            active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            states=(
                ProjectStepState.SUCCEEDED,
                ProjectStepState.SUCCEEDED,
                ProjectStepState.RUNNING,
                ProjectStepState.NOT_STARTED,
            ),
            step_timestamps=(first_submission, None, current_retry, None),
            attempts=((), (), (attempt,), ()),
        )

        self.assertEqual(project_submission_timestamp(snapshot), first_submission)
        self.assertEqual(_record(snapshot).submitted_at, first_submission)

    def test_direct_start_uses_first_real_submission_and_not_retry_date(self) -> None:
        original = datetime(2030, 8, 22, 9, tzinfo=UTC)
        retry = datetime(2030, 8, 31, 9, tzinfo=UTC)
        first_attempt = ProjectStepAttempt(
            job_id="201",
            submitted_at=original,
            finished_at=datetime(2030, 8, 22, 12, tzinfo=UTC),
            terminal_scheduler_state="TIMEOUT",
            failure_reason="TIMEOUT",
            submit_script_filename="submit.sh",
            slurm_output_filename="aims.dft.out",
        )
        snapshot = _snapshot(
            sequence=2,
            remote_name="direct-step3.20300822",
            active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            states=(
                ProjectStepState.SKIPPED,
                ProjectStepState.SKIPPED,
                ProjectStepState.RUNNING,
                ProjectStepState.NOT_STARTED,
            ),
            step_timestamps=(None, None, retry, None),
            attempts=((), (), (first_attempt,), ()),
        )

        self.assertEqual(project_submission_timestamp(snapshot), original)
        self.assertIs(_record(snapshot).current_step, ProjectStepKind.TRANSPORT_CONVERGENCE)

    def test_submission_date_format_is_compact_and_rejects_naive_time(self) -> None:
        value = datetime(2030, 1, 6, 12, 34, tzinfo=timezone(timedelta(hours=2)))
        rendered = format_project_submission_timestamp(value)

        self.assertEqual(rendered, value.astimezone().strftime("%Y-%m-%d %H:%M"))
        self.assertRegex(rendered, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(format_project_submission_timestamp(None), "—")
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            format_project_submission_timestamp(datetime(2030, 1, 6, 12, 34))

    def test_submission_date_sort_is_newest_first_missing_last_and_fully_reversible(self) -> None:
        states = (
            ProjectStepState.RUNNING,
            ProjectStepState.NOT_STARTED,
            ProjectStepState.NOT_STARTED,
            ProjectStepState.NOT_STARTED,
        )
        records = tuple(
            _record(snapshot)
            for snapshot in (
                _snapshot(
                    sequence=1,
                    remote_name="old",
                    active_step=ProjectStepKind.MOLECULE_OPT,
                    states=states,
                    submitted_at=datetime(2030, 8, 28, tzinfo=UTC),
                ),
                _snapshot(
                    sequence=2,
                    remote_name="missing",
                    active_step=ProjectStepKind.MOLECULE_OPT,
                    states=states,
                ),
                _snapshot(
                    sequence=3,
                    remote_name="new",
                    active_step=ProjectStepKind.MOLECULE_OPT,
                    states=states,
                    submitted_at=datetime(2030, 8, 31, tzinfo=UTC),
                ),
            )
        )

        normal = sort_project_presentations(
            records,
            ProjectSortCriterion.SUBMISSION_DATE,
        )
        reversed_records = sort_project_presentations(
            records,
            ProjectSortCriterion.SUBMISSION_DATE,
            reversed_order=True,
        )

        self.assertEqual(
            tuple(item.remote_directory_basename for item in normal),
            ("new", "old", "missing"),
        )
        self.assertEqual(reversed_records, tuple(reversed(normal)))

    def test_file_name_sort_is_case_insensitive_natural_and_deterministic(self) -> None:
        states = (
            ProjectStepState.RUNNING,
            ProjectStepState.NOT_STARTED,
            ProjectStepState.NOT_STARTED,
            ProjectStepState.NOT_STARTED,
        )
        records = tuple(
            _record(
                _snapshot(
                    sequence=sequence,
                    remote_name=name,
                    active_step=ProjectStepKind.MOLECULE_OPT,
                    states=states,
                    submitted_at=datetime(2030, 8, day, tzinfo=UTC),
                )
            )
            for sequence, name, day in (
                (10, "project10", 30),
                (11, "Project11", 29),
                (2, "project2", 28),
            )
        )

        ordered = sort_project_presentations(
            records,
            ProjectSortCriterion.FILE_NAME,
        )
        repeated = sort_project_presentations(
            tuple(reversed(records)),
            ProjectSortCriterion.FILE_NAME,
        )

        self.assertEqual(
            tuple(item.remote_directory_basename for item in ordered),
            ("project2", "project10", "Project11"),
        )
        self.assertEqual(repeated, ordered)
        self.assertEqual(
            sort_project_presentations(
                records,
                ProjectSortCriterion.FILE_NAME,
                reversed_order=True,
            ),
            tuple(reversed(ordered)),
        )

    def test_step_type_sort_groups_one_to_four_newest_first_and_reverses_all(self) -> None:
        records = []
        fhi_steps = (
            ProjectStepKind.MOLECULE_OPT,
            ProjectStepKind.MOLECULE_AU_OPT,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            ProjectStepKind.TRANSMISSION,
        )
        for step_number, active_step in enumerate(fhi_steps, start=1):
            states = tuple(
                ProjectStepState.SUCCEEDED
                if index < step_number
                else (
                    ProjectStepState.RUNNING
                    if index == step_number
                    else ProjectStepState.NOT_STARTED
                )
                for index in range(1, 5)
            )
            for age, day in (("old", 28), ("new", 31)):
                sequence = step_number * 10 + (1 if age == "old" else 2)
                records.append(
                    _record(
                        _snapshot(
                            sequence=sequence,
                            remote_name=f"step{step_number}-{age}",
                            active_step=active_step,
                            states=states,
                            submitted_at=datetime(2030, 8, day, tzinfo=UTC),
                        )
                    )
                )

        ordered = sort_project_presentations(
            tuple(reversed(records)),
            ProjectSortCriterion.STEP_TYPE,
        )

        self.assertEqual(
            tuple(item.remote_directory_basename for item in ordered),
            tuple(
                name
                for step_number in range(1, 5)
                for name in (f"step{step_number}-new", f"step{step_number}-old")
            ),
        )
        self.assertEqual(
            sort_project_presentations(
                tuple(records),
                ProjectSortCriterion.STEP_TYPE,
                reversed_order=True,
            ),
            tuple(reversed(ordered)),
        )

    def test_indicator_matrix_uses_only_the_reviewed_semantics(self) -> None:
        direct = _record(
            _snapshot(
                sequence=31,
                remote_name="direct",
                active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
                states=(
                    ProjectStepState.SKIPPED,
                    ProjectStepState.SKIPPED,
                    ProjectStepState.RUNNING,
                    ProjectStepState.NOT_STARTED,
                ),
            )
        )
        other = _record(
            _snapshot(
                sequence=32,
                remote_name="other",
                active_step=ProjectStepKind.TRANSMISSION,
                states=(
                    ProjectStepState.SUCCEEDED,
                    ProjectStepState.QUEUED,
                    ProjectStepState.FAILED,
                    ProjectStepState.UNKNOWN,
                ),
            )
        )
        scheduler_only = _record(
            _snapshot(
                sequence=33,
                remote_name="scheduler-only",
                active_step=ProjectStepKind.MOLECULE_OPT,
                states=(
                    ProjectStepState.SCHEDULER_COMPLETED,
                    ProjectStepState.NOT_STARTED,
                    ProjectStepState.NOT_STARTED,
                    ProjectStepState.NOT_STARTED,
                ),
            )
        )

        self.assertEqual(
            tuple(item.kind for item in direct.indicators),
            (
                StepIndicatorKind.SKIPPED,
                StepIndicatorKind.SKIPPED,
                StepIndicatorKind.ACTIVE,
                StepIndicatorKind.NOT_STARTED,
            ),
        )
        self.assertEqual(
            tuple(item.kind for item in other.indicators),
            (
                StepIndicatorKind.SUCCEEDED,
                StepIndicatorKind.ACTIVE,
                StepIndicatorKind.FAILED,
                StepIndicatorKind.UNKNOWN,
            ),
        )
        self.assertEqual(
            scheduler_only.indicators[0].kind,
            StepIndicatorKind.UNKNOWN,
        )
        self.assertEqual(
            tuple(item.tooltip for item in direct.indicators),
            (
                "Step 1 — SKIPPED",
                "Step 2 — SKIPPED",
                "Step 3 — RUNNING",
                "Step 4 — NOT_STARTED",
            ),
        )
        self.assertEqual(other.indicators[1].tooltip, "Step 2 — QUEUED")
        self.assertEqual(other.indicators[3].tooltip, "Step 4 — UNKNOWN")
        self.assertEqual(
            scheduler_only.indicators[0].tooltip,
            "Step 1 — SCHEDULER_COMPLETED",
        )

    def test_running_step3_oom_is_only_a_tooltip_refinement(self) -> None:
        record = _record(
            _snapshot(
                sequence=34,
                remote_name="oom",
                active_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
                states=(
                    ProjectStepState.SUCCEEDED,
                    ProjectStepState.SUCCEEDED,
                    ProjectStepState.RUNNING,
                    ProjectStepState.NOT_STARTED,
                ),
                runtime_evidence=StepRuntimeEvidence.TASK_OOM_DETECTED,
            )
        )

        self.assertIs(record.indicators[2].kind, StepIndicatorKind.ACTIVE)
        self.assertEqual(
            record.indicators[2].tooltip,
            "Step 3 — RUNNING — OOM detected",
        )

    def test_equal_primary_keys_have_uuid_tie_breaker(self) -> None:
        timestamp = datetime(2030, 8, 31, tzinfo=UTC)
        states = (
            ProjectStepState.RUNNING,
            ProjectStepState.NOT_STARTED,
            ProjectStepState.NOT_STARTED,
            ProjectStepState.NOT_STARTED,
        )
        records = tuple(
            _record(
                _snapshot(
                    sequence=sequence,
                    remote_name="same-name",
                    active_step=ProjectStepKind.MOLECULE_OPT,
                    states=states,
                    submitted_at=timestamp,
                )
            )
            for sequence in (3, 1, 2)
        )

        ordered = sort_project_presentations(
            records,
            ProjectSortCriterion.SUBMISSION_DATE,
        )

        self.assertEqual(
            tuple(item.project_id for item in ordered),
            tuple(sorted((item.project_id for item in records), key=str)),
        )


if __name__ == "__main__":
    unittest.main()
