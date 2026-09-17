"""Read-only-first ORCA project refresh, recovery, and explicit cancellation."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
from pathlib import PurePosixPath

from moltage.app.connection_service import ServerConnectionService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_submission import (
    ORCA_FREQ_INPUT,
    ORCA_FREQ_OUTPUT,
    ORCA_OPT_INPUT,
    ORCA_OPT_OUTPUT,
)
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.orca.evidence import (
    OrcaEvidenceError,
    OrcaFrequencyCompletion,
    parse_orca_final_xyz,
    parse_orca_frequency_evidence,
    parse_orca_optimization_output,
)
from moltage.orca.input_writer import parse_rendered_orca_structure
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.remote.executor import RemotePathNotFoundError
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.slurm_cancel import request_slurm_cancellation_once
from moltage.remote.slurm_discovery import resolve_slurm_for_submission, slurm_command_path
from moltage.remote.slurm_status import SchedulerStatusKind, query_slurm_job_status


class OrcaRecoveryError(RuntimeError):
    """Raised when ORCA project evidence cannot be safely reconciled."""


@dataclass(frozen=True, slots=True)
class OrcaRecoverySnapshot:
    project: CalculationProject
    status_message: str
    active_step_kind: ProjectStepKind
    submitted_structure: MolecularStructure | None = None
    optimized_structure: MolecularStructure | None = None
    output_bytes: bytes | None = None
    trajectory_available: bool = False


class OrcaRecoveryService:
    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._now_factory = now_factory

    def refresh_project(
        self,
        profile: ServerProfile,
        remote_project_path: str,
        *,
        supplied_password: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> OrcaRecoverySnapshot:
        _validate_scope(profile, remote_project_path)
        report = progress or (lambda _message: None)
        executor = self._connection_service.connect_for_remote_operation(
            profile, supplied_password
        )
        try:
            repository = RemoteProjectRepository(executor)
            project = repository.load(remote_project_path, preserve_remote_errors=True)
            if project.workflow_kind is not CalculationWorkflowKind.ORCA:
                raise OrcaRecoveryError("Selected project is not an ORCA workflow")
            if project.server_profile_id != profile.profile_id and not self._local_index_repository.is_bound_to_profile(
                project, bound_server_profile_id=profile.profile_id
            ):
                raise OrcaRecoveryError("Confirm this server profile before refreshing the ORCA project")
            step = _active_step(project)
            if step.job_id is None:
                return _snapshot(project, "ORCA stage has not been submitted")
            report("Querying scheduler status...")
            preset = profile.execution_preset
            if preset is None:
                raise OrcaRecoveryError("Cluster Execution Settings are unavailable")
            resolved = resolve_slurm_for_submission(executor, preset)
            effective = replace(preset, slurm_bin_directory=resolved.bin_directory)
            status = query_slurm_job_status(
                executor,
                squeue_path=slurm_command_path(effective, "squeue"),
                sacct_path=slurm_command_path(effective, "sacct"),
                job_id=step.job_id,
                profile_username=profile.username,
                lsf_env_directory=resolved.lsf_env_directory,
                lsf_library_directory=resolved.lsf_library_directory,
                lsf_server_directory=resolved.lsf_server_directory,
            )
            if status.kind in {SchedulerStatusKind.QUEUED, SchedulerStatusKind.RUNNING}:
                state = (
                    ProjectStepState.QUEUED
                    if status.kind is SchedulerStatusKind.QUEUED
                    else ProjectStepState.RUNNING
                )
                project = self._persist(
                    repository,
                    project,
                    replace(step, state=state, scheduler_state=status.scheduler_state),
                )
                self._mark_seen(project, profile)
                return _snapshot(project, f"ORCA job is {state.value.lower()}")
            if status.kind is SchedulerStatusKind.FAILED:
                message = f"Scheduler reported {status.scheduler_state or 'failure'}"
                project = self._persist(
                    repository,
                    project,
                    replace(
                        step,
                        state=ProjectStepState.FAILED,
                        scheduler_state=status.scheduler_state,
                        finished_at=step.finished_at or _aware_now(self._now_factory),
                        last_error=message,
                    ),
                )
                self._mark_seen(project, profile)
                return _snapshot(project, message)
            if status.kind is not SchedulerStatusKind.COMPLETED:
                project = self._persist(
                    repository,
                    project,
                    replace(
                        step,
                        state=ProjectStepState.UNKNOWN,
                        scheduler_state=status.scheduler_state,
                        last_error="Scheduler outcome is not yet authoritative",
                    ),
                )
                self._mark_seen(project, profile)
                return _snapshot(project, "Scheduler outcome is not yet authoritative")
            report("Reading ORCA result evidence...")
            if step.kind is ProjectStepKind.ORCA_OPTIMIZATION:
                snapshot = self._recover_optimization(executor, repository, project, step)
            else:
                snapshot = self._recover_frequency(executor, repository, project, step)
            self._mark_seen(snapshot.project, profile)
            return snapshot
        finally:
            executor.close()

    def cancel_active_job(
        self,
        profile: ServerProfile,
        project: CalculationProject,
        *,
        supplied_password: str | None = None,
    ) -> CalculationProject:
        if project.workflow_kind is not CalculationWorkflowKind.ORCA:
            raise OrcaRecoveryError("Cancellation requires an ORCA project")
        executor = self._connection_service.connect_for_remote_operation(
            profile, supplied_password
        )
        try:
            repository = RemoteProjectRepository(executor)
            current = repository.load(project.remote_project_path, preserve_remote_errors=True)
            step = _active_step(current)
            if step.job_id is None or step.state not in {
                ProjectStepState.QUEUED,
                ProjectStepState.RUNNING,
                ProjectStepState.UNKNOWN,
            }:
                raise OrcaRecoveryError("The ORCA project has no cancellable active job")
            preset = profile.execution_preset
            if preset is None:
                raise OrcaRecoveryError("Cluster Execution Settings are unavailable")
            resolved = resolve_slurm_for_submission(executor, preset)
            effective = replace(preset, slurm_bin_directory=resolved.bin_directory)
            request_slurm_cancellation_once(
                executor,
                scancel_path=slurm_command_path(effective, "scancel"),
                job_id=step.job_id,
                lsf_env_directory=resolved.lsf_env_directory,
                lsf_library_directory=resolved.lsf_library_directory,
                lsf_server_directory=resolved.lsf_server_directory,
            )
            updated = self._persist(
                repository,
                current,
                replace(
                    step,
                    state=ProjectStepState.UNKNOWN,
                    scheduler_state="CANCEL_REQUESTED",
                    last_error="Cancellation requested; refresh to obtain the terminal scheduler state",
                ),
            )
            self._mark_seen(updated, profile)
            return updated
        finally:
            executor.close()

    def _recover_optimization(self, executor, repository, project, step):
        directory = project.remote_project_path
        submitted_input = _read_required(executor, str(PurePosixPath(directory) / ORCA_OPT_INPUT), ORCA_OPT_INPUT)
        _require_submitted_hash(step, ORCA_OPT_INPUT, submitted_input)
        submitted = parse_rendered_orca_structure(submitted_input)
        if tuple(atom.element for atom in submitted) != step.orca_submitted_elements:
            raise OrcaRecoveryError("Submitted ORCA atom identity differs from the manifest")
        output = _read_required(executor, str(PurePosixPath(directory) / ORCA_OPT_OUTPUT), ORCA_OPT_OUTPUT)
        parsed_output = parse_orca_optimization_output(output)
        if parsed_output.explicit_nonconvergence:
            evidence = OrcaOptimizationResultEvidence(
                True,
                parsed_output.normal_termination,
                False,
                False,
                False,
                output_sha256=hashlib.sha256(output).hexdigest(),
                diagnostic="ORCA reported optimization nonconvergence",
            )
            updated = self._persist(
                repository,
                project,
                replace(
                    step,
                    state=ProjectStepState.FAILED,
                    scheduler_state="COMPLETED",
                    finished_at=step.finished_at or _aware_now(self._now_factory),
                    last_error=evidence.diagnostic,
                    orca_optimization_result=evidence,
                ),
            )
            return _snapshot(updated, evidence.diagnostic, submitted=submitted, output=output)
        try:
            xyz = _read_required(executor, str(PurePosixPath(directory) / "orca_opt.xyz"), "orca_opt.xyz")
            optimized = parse_orca_final_xyz(xyz, submitted)
            xyz_hash = hashlib.sha256(xyz).hexdigest()
            xyz_valid = True
            xyz_diagnostic = None
        except (OrcaRecoveryError, OrcaEvidenceError) as error:
            optimized = None
            xyz_hash = None
            xyz_valid = False
            xyz_diagnostic = str(error)
        gbw_hash = _optional_hash(executor, str(PurePosixPath(directory) / "orca_opt.gbw"))
        evidence = OrcaOptimizationResultEvidence(
            scheduler_succeeded=True,
            normal_termination=parsed_output.normal_termination,
            optimization_converged=parsed_output.optimization_converged,
            final_xyz_valid=xyz_valid,
            wbl_input_ready=gbw_hash is not None,
            output_sha256=hashlib.sha256(output).hexdigest(),
            xyz_sha256=xyz_hash,
            gbw_sha256=gbw_hash,
            diagnostic=(
                None
                if parsed_output.normal_termination and parsed_output.optimization_converged and xyz_valid
                else xyz_diagnostic
                or "ORCA normal termination or optimization convergence was not confirmed"
            ),
        )
        state = ProjectStepState.SUCCEEDED if evidence.succeeded else ProjectStepState.SCHEDULER_COMPLETED
        updated = self._persist(
            repository,
            project,
            replace(
                step,
                state=state,
                scheduler_state="COMPLETED",
                finished_at=step.finished_at or _aware_now(self._now_factory),
                last_error=evidence.diagnostic,
                orca_optimization_result=evidence,
            ),
        )
        message = (
            "ORCA optimization converged and the final geometry was verified"
            if evidence.succeeded
            else evidence.diagnostic or "ORCA optimization result is unverified"
        )
        trajectory = _exists_nonempty(executor, str(PurePosixPath(directory) / "orca_opt_trj.xyz"))
        return _snapshot(
            updated,
            message,
            submitted=submitted,
            optimized=optimized,
            output=output,
            trajectory=trajectory,
        )

    def _recover_frequency(self, executor, repository, project, step):
        directory = remote_step_directory(project, ProjectStepKind.ORCA_FREQUENCY)
        submitted_input = _read_required(
            executor,
            str(PurePosixPath(directory) / ORCA_FREQ_INPUT),
            ORCA_FREQ_INPUT,
        )
        _require_submitted_hash(step, ORCA_FREQ_INPUT, submitted_input)
        output = _read_required(executor, str(PurePosixPath(directory) / ORCA_FREQ_OUTPUT), ORCA_FREQ_OUTPUT)
        try:
            hessian = executor.read_bytes(str(PurePosixPath(directory) / "orca_freq.hess"))
        except RemotePathNotFoundError:
            hessian = None
        atom_count = len(project.steps[0].orca_submitted_elements)
        evidence = parse_orca_frequency_evidence(output, hessian, atom_count=atom_count)
        state = (
            ProjectStepState.SUCCEEDED
            if evidence.completion is OrcaFrequencyCompletion.FREQUENCY_COMPLETED
            else ProjectStepState.SCHEDULER_COMPLETED
        )
        updated = self._persist(
            repository,
            project,
            replace(
                step,
                state=state,
                scheduler_state="COMPLETED",
                finished_at=step.finished_at or _aware_now(self._now_factory),
                last_error=evidence.diagnostic,
                orca_frequency_result=evidence,
            ),
        )
        message = (
            "ORCA frequency completed; "
            + evidence.imaginary_classification.value.replace("_", " ").lower()
            if state is ProjectStepState.SUCCEEDED
            else evidence.diagnostic or "ORCA frequency result is unverified"
        )
        return _snapshot(updated, message, output=output)

    def _persist(self, repository, project, changed_step):
        candidate = replace(
            project,
            steps=tuple(
                changed_step if item.kind is changed_step.kind else item
                for item in project.steps
            ),
        )
        if candidate == project:
            return project
        return repository.persist_update(candidate, updated_at=_aware_now(self._now_factory))

    def _mark_seen(self, project, profile):
        self._local_index_repository.mark_seen(
            project, bound_server_profile_id=profile.profile_id
        )


def _snapshot(project, message, *, submitted=None, optimized=None, output=None, trajectory=False):
    return OrcaRecoverySnapshot(
        project,
        message,
        _active_step(project).kind,
        submitted,
        optimized,
        output,
        trajectory,
    )


def _active_step(project):
    for step in reversed(project.steps):
        if step.state is not ProjectStepState.NOT_STARTED:
            return step
    return project.steps[0]


def _read_required(executor, path, label):
    try:
        data = executor.read_bytes(path)
    except RemotePathNotFoundError:
        raise OrcaRecoveryError(f"Missing required ORCA artifact: {label}") from None
    if not data:
        raise OrcaRecoveryError(f"Required ORCA artifact is empty: {label}")
    return data


def _require_submitted_hash(step, filename, content):
    expected = dict(step.input_hashes).get(filename)
    if expected is None:
        raise OrcaRecoveryError(f"Missing submitted SHA256 evidence for {filename}")
    if hashlib.sha256(content).hexdigest() != expected:
        raise OrcaRecoveryError(f"Submitted ORCA artifact hash mismatch: {filename}")


def _optional_hash(executor, path):
    try:
        data = executor.read_bytes(path)
    except RemotePathNotFoundError:
        return None
    return hashlib.sha256(data).hexdigest() if data else None


def _exists_nonempty(executor, path):
    try:
        stat = executor.stat(path)
    except RemotePathNotFoundError:
        return False
    return not stat.is_directory and bool(stat.size)


def _validate_scope(profile, path):
    if not isinstance(profile, ServerProfile):
        raise TypeError("ORCA recovery requires a ServerProfile")
    if PurePosixPath(path).parent != PurePosixPath(profile.remote_project_root):
        raise OrcaRecoveryError("ORCA recovery is limited to first-level managed projects")


def _aware_now(factory):
    value = factory()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ORCA recovery timestamps must be timezone-aware")
    return value
