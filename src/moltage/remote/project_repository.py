"""Managed remote project manifests, atomic updates, and shallow discovery."""

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable
from uuid import UUID, uuid4

from moltage.domain.calculation_project import (
    CalculationProject,
    LEGACY_MANAGED_METADATA_DIRECTORIES,
    MANAGED_METADATA_DIRECTORY,
)
from moltage.remote.executor import (
    RemoteExecutor,
    RemoteExecutorError,
    RemotePathNotFoundError,
    RemoteRenameError,
)
from moltage.remote.project_manifest import (
    ManagedProjectManifestError,
    parse_project_manifest,
    serialize_project_manifest,
)


class ManagedMetadataLocationError(RuntimeError):
    """Raised when current and legacy metadata locations are ambiguous."""


class RemoteProjectRepositoryError(ManagedMetadataLocationError):
    """Raised when a managed remote manifest cannot be safely persisted."""


@dataclass(frozen=True, slots=True)
class ManagedProjectProblem:
    remote_project_path: str
    message: str


@dataclass(frozen=True, slots=True)
class ManagedProjectDiscovery:
    projects: tuple[CalculationProject, ...]
    problems: tuple[ManagedProjectProblem, ...]


class RemoteProjectRepository:
    """Treat remote project.json as authoritative workflow state."""

    def __init__(
        self,
        executor: RemoteExecutor,
        *,
        temporary_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._executor = executor
        self._temporary_id_factory = temporary_id_factory

    def write_initial(self, project: CalculationProject) -> None:
        if project.revision != 1:
            raise RemoteProjectRepositoryError(
                "an initial project manifest must begin at revision 1"
            )
        self._atomic_write(project)

    def persist_update(
        self,
        project: CalculationProject,
        *,
        updated_at: datetime,
        preserve_remote_errors: bool = False,
    ) -> CalculationProject:
        try:
            manifest_path, _ = resolve_existing_managed_metadata_file(
                self._executor,
                project.remote_project_path,
                "project.json",
            )
            current = self._load_manifest(
                project.remote_project_path,
                manifest_path,
                preserve_remote_errors=preserve_remote_errors,
            )
        except RemoteExecutorError as error:
            if preserve_remote_errors:
                raise
            raise RemoteProjectRepositoryError(str(error)) from None
        if current.project_id != project.project_id:
            raise RemoteProjectRepositoryError(
                "remote project identity changed before update"
            )
        if current.revision != project.revision:
            raise RemoteProjectRepositoryError(
                "remote project revision changed before update"
            )
        updated = replace(
            project,
            revision=project.revision + 1,
            updated_at=updated_at,
        )
        self._atomic_write(
            updated,
            destination=manifest_path,
            preserve_remote_errors=preserve_remote_errors,
        )
        return updated

    def load(
        self,
        remote_project_path: str,
        *,
        expected_server_profile_id: UUID | None = None,
        preserve_remote_errors: bool = False,
    ) -> CalculationProject:
        try:
            manifest_path, _ = resolve_existing_managed_metadata_file(
                self._executor,
                remote_project_path,
                "project.json",
            )
            project = self._load_manifest(
                remote_project_path,
                manifest_path,
                expected_server_profile_id=expected_server_profile_id,
                preserve_remote_errors=preserve_remote_errors,
            )
        except RemoteExecutorError as error:
            if preserve_remote_errors:
                raise
            raise RemoteProjectRepositoryError(str(error)) from None
        return project

    def _load_manifest(
        self,
        remote_project_path: str,
        manifest_path: str,
        *,
        expected_server_profile_id: UUID | None = None,
        preserve_remote_errors: bool = False,
    ) -> CalculationProject:
        try:
            project = parse_project_manifest(self._executor.read_bytes(manifest_path))
        except RemoteExecutorError:
            if preserve_remote_errors:
                raise
            raise
        if project.remote_project_path != remote_project_path:
            raise RemoteProjectRepositoryError(
                "manifest remote project path does not match its containing directory"
            )
        if (
            expected_server_profile_id is not None
            and project.server_profile_id != expected_server_profile_id
        ):
            raise RemoteProjectRepositoryError(
                "manifest server profile identity does not match the selected server"
            )
        return project

    def discover(
        self,
        remote_root: str,
        *,
        expected_server_profile_id: UUID | None = None,
        preserve_remote_errors: bool = False,
    ) -> ManagedProjectDiscovery:
        projects: list[CalculationProject] = []
        problems: list[ManagedProjectProblem] = []
        for entry in self._executor.list_directory(remote_root):
            if not entry.is_directory:
                continue
            project_path = str(PurePosixPath(remote_root) / entry.name)
            try:
                manifest_path, manifest_stat = (
                    resolve_existing_managed_metadata_file(
                        self._executor,
                        project_path,
                        "project.json",
                    )
                )
            except RemotePathNotFoundError:
                continue
            except RemoteExecutorError as error:
                if preserve_remote_errors:
                    raise
                problems.append(ManagedProjectProblem(project_path, str(error)))
                continue
            except ManagedMetadataLocationError as error:
                problems.append(ManagedProjectProblem(project_path, str(error)))
                continue
            if manifest_stat.is_directory:
                problems.append(
                    ManagedProjectProblem(
                        project_path,
                        f"{PurePosixPath(manifest_path).parent.name}/project.json "
                        "is a directory",
                    )
                )
                continue
            try:
                projects.append(
                    self._load_manifest(
                        project_path,
                        manifest_path,
                        expected_server_profile_id=expected_server_profile_id,
                        preserve_remote_errors=preserve_remote_errors,
                    )
                )
            except RemoteExecutorError:
                raise
            except (ManagedProjectManifestError, RemoteProjectRepositoryError) as error:
                problems.append(ManagedProjectProblem(project_path, str(error)))
        return ManagedProjectDiscovery(
            tuple(sorted(projects, key=lambda item: item.remote_project_path)),
            tuple(sorted(problems, key=lambda item: item.remote_project_path)),
        )

    def _atomic_write(
        self,
        project: CalculationProject,
        *,
        destination: str | None = None,
        preserve_remote_errors: bool = False,
    ) -> None:
        if destination is None:
            destination = _manifest_path(project.remote_project_path)
        temporary_id = self._temporary_id_factory()
        if (
            not isinstance(temporary_id, str)
            or not temporary_id
            or "/" in temporary_id
            or "\\" in temporary_id
        ):
            raise RemoteProjectRepositoryError(
                "manifest temporary identity must be one nonempty path component"
            )
        temporary = destination + ".tmp-" + temporary_id
        data = serialize_project_manifest(project).encode("utf-8")
        try:
            self._executor.write_bytes(temporary, data)
            self._executor.rename(temporary, destination)
        except RemoteRenameError as error:
            if preserve_remote_errors:
                raise
            raise RemoteProjectRepositoryError(
                f"project manifest temporary write succeeded but atomic rename failed: {error}"
            ) from None
        except RemoteExecutorError as error:
            if preserve_remote_errors:
                raise
            raise RemoteProjectRepositoryError(str(error)) from None


def _manifest_path(remote_project_path: str) -> str:
    return managed_metadata_file_path(remote_project_path, "project.json")


def managed_metadata_directory_path(remote_path: str) -> str:
    return str(PurePosixPath(remote_path) / MANAGED_METADATA_DIRECTORY)


def managed_metadata_file_path(remote_path: str, filename: str) -> str:
    return str(PurePosixPath(remote_path) / MANAGED_METADATA_DIRECTORY / filename)


def resolve_existing_managed_metadata_file(
    executor: RemoteExecutor,
    remote_path: str,
    filename: str,
):
    """Resolve exactly one current or legacy managed-metadata file."""

    candidates = (
        managed_metadata_file_path(remote_path, filename),
        *(
            str(PurePosixPath(remote_path) / directory / filename)
            for directory in LEGACY_MANAGED_METADATA_DIRECTORIES
        ),
    )
    found = []
    for candidate in candidates:
        try:
            item = executor.stat(candidate)
        except RemotePathNotFoundError:
            continue
        found.append((candidate, item))
    if not found:
        raise RemotePathNotFoundError(
            f"managed metadata file does not exist: {remote_path}/{filename}"
        )
    if len(found) > 1:
        locations = ", ".join(path for path, _ in found)
        raise ManagedMetadataLocationError(
            "both current and legacy managed metadata exist; refusing to choose "
            f"between: {locations}"
        )
    return found[0]
