"""Local recycle metadata and narrowly verified permanent project deletion."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import PurePosixPath
from uuid import UUID

from moltage.app.connection_service import ServerConnectionService
from moltage.app.density_workflow import (
    MANIFEST as DENSITY_MANIFEST,
    DensityTask,
    density_task_from_manifest,
    serialize_density_task_manifest,
)
from moltage.app.local_project_index import (
    LocalProjectIndexRepository,
    RecycledProjectReference,
)
from moltage.app.project_presentation import project_submission_timestamp
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectStepState,
)
from moltage.domain.server_profile import ServerProfile
from moltage.remote.project_delete import (
    delete_remote_project_once,
    verify_remote_project_root_for_deletion,
)
from moltage.remote.project_repository import (
    RemoteProjectRepository,
    resolve_existing_managed_metadata_file,
)
from moltage.remote.slurm_status import SchedulerStatusKind


class ProjectManagementError(RuntimeError):
    """Raised when a project-management action cannot proceed safely."""


ACTIVE_OR_UNRESOLVED_DELETE_MESSAGE = (
    "This project still has active or unresolved work. "
    "Wait for it to reach a terminal state before deleting it."
)


@dataclass(frozen=True, slots=True)
class PermanentProjectDeletionRequest:
    profile: ServerProfile
    project: CalculationProject
    supplied_password: str | None = field(default=None, repr=False, compare=False)
    scheduler_status_kind: SchedulerStatusKind | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("permanent deletion requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("permanent deletion requires a CalculationProject")
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str) or not self.supplied_password
        ):
            raise ValueError("temporary password must be nonempty text or None")
        if self.scheduler_status_kind is not None and not isinstance(
            self.scheduler_status_kind,
            SchedulerStatusKind,
        ):
            raise TypeError("scheduler status kind is unsupported")
        _validate_selected_project(self.profile, self.project)
        _require_deletion_safe(
            self.project,
            scheduler_status_kind=self.scheduler_status_kind,
        )


@dataclass(frozen=True, slots=True)
class PermanentDensityTaskDeletionRequest:
    profile: ServerProfile
    task: DensityTask
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("permanent density deletion requires a ServerProfile")
        if not isinstance(self.task, DensityTask):
            raise TypeError("permanent density deletion requires a DensityTask")
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str) or not self.supplied_password
        ):
            raise ValueError("temporary password must be nonempty text or None")
        validated = density_task_from_manifest(
            self.profile,
            self.task.remote_path,
            self.task.data,
        )
        if validated != self.task:
            raise ProjectManagementError("The selected density task is invalid.")
        _validate_selected_path(self.profile, self.task.remote_path)
        _require_density_deletion_safe(self.task)


@dataclass(frozen=True, slots=True)
class PermanentProjectDeletionResult:
    project_id: UUID
    remote_project_path: str


class ProjectManagementService:
    """Keep local tombstones and one destructive remote action out of Qt."""

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

    def recycled_projects(self) -> tuple[RecycledProjectReference, ...]:
        return self._local_index_repository.load_recycled()

    def move_to_recycle(
        self,
        profile: ServerProfile,
        snapshot: ProjectRecoverySnapshot,
    ) -> RecycledProjectReference:
        if not isinstance(profile, ServerProfile):
            raise TypeError("project recycle requires a ServerProfile")
        if not isinstance(snapshot, ProjectRecoverySnapshot):
            raise TypeError("project recycle requires a recovery snapshot")
        _require_deletion_safe(
            snapshot.project,
            scheduler_status_kind=snapshot.scheduler_status_kind,
        )
        _validate_selected_path(profile, snapshot.project.remote_project_path)
        return self._local_index_repository.recycle(
            snapshot.project,
            bound_server_profile_id=profile.profile_id,
            submitted_at=project_submission_timestamp(snapshot),
            recycled_at=_aware_now(self._now_factory),
        )

    def move_density_task_to_recycle(
        self,
        profile: ServerProfile,
        task: DensityTask,
    ) -> RecycledProjectReference:
        if not isinstance(profile, ServerProfile):
            raise TypeError("density task recycle requires a ServerProfile")
        if not isinstance(task, DensityTask):
            raise TypeError("density task recycle requires a DensityTask")
        validated = density_task_from_manifest(profile, task.remote_path, task.data)
        if validated != task:
            raise ProjectManagementError("The selected density task is invalid.")
        _validate_selected_path(profile, task.remote_path)
        _require_density_deletion_safe(task)
        return self._local_index_repository.recycle_reference(
            project_id=task.task_id,
            bound_server_profile_id=profile.profile_id,
            remote_project_path=task.remote_path,
            display_name=task.name,
            submitted_at=task.created_at,
            recycled_at=_aware_now(self._now_factory),
        )

    def restore(
        self,
        entry: RecycledProjectReference,
    ) -> RecycledProjectReference:
        if not isinstance(entry, RecycledProjectReference):
            raise TypeError("project restore requires a recycled-project reference")
        return self._local_index_repository.restore(
            entry.server_profile_id,
            entry.project_id,
        )

    def permanently_delete(
        self,
        request: PermanentProjectDeletionRequest | PermanentDensityTaskDeletionRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> PermanentProjectDeletionResult:
        if not isinstance(
            request,
            (PermanentProjectDeletionRequest, PermanentDensityTaskDeletionRequest),
        ):
            raise TypeError(
                "permanently_delete requires a supported permanent-deletion request"
            )
        report = _progress_reporter(progress)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            report("Revalidating the exact managed project...")
            remote_path = (
                request.project.remote_project_path
                if isinstance(request, PermanentProjectDeletionRequest)
                else request.task.remote_path
            )
            verify_remote_project_root_for_deletion(executor, remote_path)
            if isinstance(request, PermanentProjectDeletionRequest):
                current = RemoteProjectRepository(executor).load(
                    remote_path,
                    expected_server_profile_id=request.profile.profile_id,
                    preserve_remote_errors=True,
                )
                if current != request.project:
                    raise ProjectManagementError(
                        "The selected project changed before deletion. Refresh first."
                    )
                _validate_selected_project(request.profile, current)
                _require_deletion_safe(
                    current,
                    scheduler_status_kind=request.scheduler_status_kind,
                )
                current_id = current.project_id
                current_path = current.remote_project_path
            else:
                manifest_path, manifest_stat = (
                    resolve_existing_managed_metadata_file(
                        executor,
                        remote_path,
                        DENSITY_MANIFEST,
                    )
                )
                if manifest_stat.is_directory:
                    raise ProjectManagementError(
                        "The selected density manifest path is a directory."
                    )
                data = json.loads(executor.read_bytes(manifest_path))
                current_task = density_task_from_manifest(
                    request.profile,
                    remote_path,
                    data,
                )
                if (
                    current_task.remote_path != request.task.remote_path
                    or serialize_density_task_manifest(current_task)
                    != serialize_density_task_manifest(request.task)
                ):
                    raise ProjectManagementError(
                        "The selected density task changed before deletion. Refresh first."
                    )
                _validate_selected_path(request.profile, current_task.remote_path)
                _require_density_deletion_safe(current_task)
                current_id = current_task.task_id
                current_path = current_task.remote_path
            report("Permanently deleting the exact server project directory...")
            delete_remote_project_once(executor, current_path)
        except Exception:
            _close_without_replacing_outcome(executor)
            raise
        _close_without_replacing_outcome(executor)
        self._local_index_repository.forget(
            request.profile.profile_id,
            current_id,
        )
        return PermanentProjectDeletionResult(
            current_id,
            current_path,
        )


def _validate_selected_project(
    profile: ServerProfile,
    project: CalculationProject,
) -> None:
    if project.server_profile_id != profile.profile_id:
        raise ProjectManagementError(
            "The project server-profile identity does not match the selected server."
        )
    _validate_selected_path(profile, project.remote_project_path)
    if PurePosixPath(project.remote_project_path).name != project.remote_directory_name:
        raise ProjectManagementError(
            "The project path does not match its authoritative directory name."
        )


def _validate_selected_path(profile: ServerProfile, remote_project_path: str) -> None:
    if (
        not isinstance(remote_project_path, str)
        or not remote_project_path.startswith("/")
    ):
        raise ProjectManagementError(
            "Permanent deletion is limited to one exact first-level managed project."
        )
    root = PurePosixPath(profile.remote_project_root)
    path = PurePosixPath(remote_project_path)
    if (
        str(path) != remote_project_path
        or remote_project_path == "/"
        or path == root
        or path.parent != root
        or not path.name
        or ".." in path.parts
    ):
        raise ProjectManagementError(
            "Permanent deletion is limited to one exact first-level managed project."
        )


def project_deletion_block_reason(
    project: CalculationProject,
    *,
    scheduler_status_kind: SchedulerStatusKind | None = None,
) -> str | None:
    """Return the shared nonterminal-operation deletion guard, if any."""

    if not isinstance(project, CalculationProject):
        raise TypeError("project deletion safety requires a CalculationProject")
    if scheduler_status_kind is not None and not isinstance(
        scheduler_status_kind,
        SchedulerStatusKind,
    ):
        raise TypeError("scheduler status kind is unsupported")
    unsafe_states = {
        ProjectStepState.QUEUED,
        ProjectStepState.RUNNING,
        ProjectStepState.UNKNOWN,
    }
    unresolved_statuses = {
        SchedulerStatusKind.ACCOUNTING_PENDING,
        SchedulerStatusKind.UNRESOLVED,
    }
    if (
        any(step.state in unsafe_states for step in project.steps)
        or scheduler_status_kind in unresolved_statuses
    ):
        return ACTIVE_OR_UNRESOLVED_DELETE_MESSAGE
    return None


def density_task_deletion_block_reason(task: DensityTask) -> str | None:
    """Apply the ordinary active/unresolved guard to a density task."""

    if not isinstance(task, DensityTask):
        raise TypeError("density deletion safety requires a DensityTask")
    if task.state in {"UNKNOWN", "QUEUED", "RUNNING", "UNRESOLVED"}:
        return ACTIVE_OR_UNRESOLVED_DELETE_MESSAGE
    return None


def _require_deletion_safe(
    project: CalculationProject,
    *,
    scheduler_status_kind: SchedulerStatusKind | None = None,
) -> None:
    reason = project_deletion_block_reason(
        project,
        scheduler_status_kind=scheduler_status_kind,
    )
    if reason is not None:
        raise ProjectManagementError(reason)


def _require_density_deletion_safe(task: DensityTask) -> None:
    reason = density_task_deletion_block_reason(task)
    if reason is not None:
        raise ProjectManagementError(reason)


def _aware_now(now_factory: Callable[[], datetime]) -> datetime:
    value = now_factory()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("project recycle timestamps must be timezone-aware")
    return value


def _close_without_replacing_outcome(executor: object) -> None:
    try:
        executor.close()
    except Exception:
        pass


def _progress_reporter(
    progress: Callable[[str], None] | None,
) -> Callable[[str], None]:
    def report(message: str) -> None:
        if progress is None:
            return
        try:
            progress(message)
        except Exception:
            pass

    return report
