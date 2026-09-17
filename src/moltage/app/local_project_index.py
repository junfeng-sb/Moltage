"""Non-authoritative local cache of known remote project identities."""

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from uuid import UUID

from moltage.domain.calculation_project import CalculationProject, CalculationWorkflowKind


_INDEX_SCHEMA_VERSION = 3
_LEGACY_INDEX_SCHEMA_VERSIONS = (1, 2)


class LocalProjectIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KnownProjectReference:
    project_id: UUID
    server_profile_id: UUID
    remote_project_path: str
    display_name: str
    last_seen_revision: int
    workflow_kind: CalculationWorkflowKind = CalculationWorkflowKind.FHI_AIMS_AITRANSS

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID):
            raise LocalProjectIndexError("project reference ID must be a UUID")
        if not isinstance(self.server_profile_id, UUID):
            raise LocalProjectIndexError("server profile reference must be a UUID")
        if (
            not isinstance(self.remote_project_path, str)
            or not self.remote_project_path.startswith("/")
        ):
            raise LocalProjectIndexError(
                "project reference path must be an absolute POSIX path"
            )
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise LocalProjectIndexError("project display name must not be empty")
        if (
            isinstance(self.last_seen_revision, bool)
            or not isinstance(self.last_seen_revision, int)
            or self.last_seen_revision < 0
        ):
            raise LocalProjectIndexError(
                "last-seen revision must be a non-negative integer"
            )
        try:
            object.__setattr__(self, "workflow_kind", CalculationWorkflowKind(self.workflow_kind))
        except (TypeError, ValueError):
            raise LocalProjectIndexError("project workflow is unsupported") from None

    def has_unseen_change(self, remote_project: CalculationProject) -> bool:
        if remote_project.project_id != self.project_id:
            raise LocalProjectIndexError("remote project ID does not match reference")
        return remote_project.revision > self.last_seen_revision


@dataclass(frozen=True, slots=True)
class RecycledProjectReference:
    """Persistent local tombstone; remote project data remains untouched."""

    project_id: UUID
    server_profile_id: UUID
    remote_project_path: str
    display_name: str
    submitted_at: datetime | None
    recycled_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID):
            raise LocalProjectIndexError("recycled project ID must be a UUID")
        if not isinstance(self.server_profile_id, UUID):
            raise LocalProjectIndexError(
                "recycled server profile reference must be a UUID"
            )
        if (
            not isinstance(self.remote_project_path, str)
            or not self.remote_project_path.startswith("/")
        ):
            raise LocalProjectIndexError(
                "recycled project path must be an absolute POSIX path"
            )
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise LocalProjectIndexError("recycled project display name must not be empty")
        _validate_optional_timestamp(self.submitted_at, "submission timestamp")
        _validate_timestamp(self.recycled_at, "recycled timestamp")

    @property
    def identity(self) -> tuple[UUID, UUID]:
        return self.server_profile_id, self.project_id


class LocalProjectIndexRepository:
    """Deterministic JSON cache; remote manifests remain authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[KnownProjectReference, ...]:
        references, _recycled = self._load_state()
        return references

    def is_bound_to_profile(
        self,
        project: CalculationProject,
        *,
        bound_server_profile_id: UUID,
    ) -> bool:
        """Return whether this exact remote project was approved for a profile.

        The remote manifest keeps its historical profile UUID.  This local
        binding is deliberately stricter than a project-ID-only lookup so a
        copied manifest at another path cannot inherit an earlier approval.
        """

        if not isinstance(project, CalculationProject):
            raise LocalProjectIndexError(
                "local profile binding requires a CalculationProject"
            )
        if not isinstance(bound_server_profile_id, UUID):
            raise LocalProjectIndexError(
                "bound server profile reference must be a UUID"
            )
        return any(
            reference.project_id == project.project_id
            and reference.server_profile_id == bound_server_profile_id
            and reference.remote_project_path == project.remote_project_path
            for reference in self.load()
        )

    def load_recycled(
        self,
        *,
        server_profile_id: UUID | None = None,
    ) -> tuple[RecycledProjectReference, ...]:
        if server_profile_id is not None and not isinstance(server_profile_id, UUID):
            raise LocalProjectIndexError("recycle filter must be a server-profile UUID")
        _references, recycled = self._load_state()
        if server_profile_id is None:
            return recycled
        return tuple(
            item for item in recycled if item.server_profile_id == server_profile_id
        )

    def is_recycled(self, server_profile_id: UUID, project_id: UUID) -> bool:
        if not isinstance(server_profile_id, UUID) or not isinstance(project_id, UUID):
            raise LocalProjectIndexError("recycle identity requires two UUIDs")
        return any(
            item.identity == (server_profile_id, project_id)
            for item in self.load_recycled()
        )

    def _load_state(
        self,
    ) -> tuple[
        tuple[KnownProjectReference, ...],
        tuple[RecycledProjectReference, ...],
    ]:
        if not self.path.exists():
            return (), ()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            schema_version = raw["schema_version"]
            if schema_version not in (
                _INDEX_SCHEMA_VERSION,
                *_LEGACY_INDEX_SCHEMA_VERSIONS,
            ):
                raise ValueError("unsupported local-project-index schema")
            references = tuple(
                KnownProjectReference(
                    project_id=UUID(item["project_id"]),
                    server_profile_id=UUID(item["server_profile_id"]),
                    remote_project_path=item["remote_project_path"],
                    display_name=item["display_name"],
                    last_seen_revision=item["last_seen_revision"],
                    workflow_kind=(
                        CalculationWorkflowKind(item["workflow_kind"])
                        if schema_version >= 3
                        else CalculationWorkflowKind.FHI_AIMS_AITRANSS
                    ),
                )
                for item in raw["projects"]
            )
            recycled = (
                tuple(
                    RecycledProjectReference(
                        project_id=UUID(item["project_id"]),
                        server_profile_id=UUID(item["server_profile_id"]),
                        remote_project_path=item["remote_project_path"],
                        display_name=item["display_name"],
                        submitted_at=_parse_optional_timestamp(item["submitted_at"]),
                        recycled_at=_parse_timestamp(item["recycled_at"]),
                    )
                    for item in raw["recycled_projects"]
                )
                if schema_version >= 2
                else ()
            )
        except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
            raise LocalProjectIndexError(
                f"local project index is unreadable or malformed: {error}"
            ) from None
        if len({item.project_id for item in references}) != len(references):
            raise LocalProjectIndexError("local project index has duplicate project IDs")
        if len({item.identity for item in recycled}) != len(recycled):
            raise LocalProjectIndexError("local project index has duplicate recycle IDs")
        return references, recycled

    def save(self, reference: KnownProjectReference) -> tuple[KnownProjectReference, ...]:
        current, recycled = self._load_state()
        updated = tuple(
            reference if item.project_id == reference.project_id else item
            for item in current
        )
        if reference.project_id not in {item.project_id for item in current}:
            updated = (*updated, reference)
        updated = tuple(sorted(updated, key=lambda item: str(item.project_id)))
        self._write(updated, recycled)
        return updated

    def mark_seen(
        self,
        project: CalculationProject,
        *,
        bound_server_profile_id: UUID | None = None,
    ) -> KnownProjectReference:
        profile_id = (
            project.server_profile_id
            if bound_server_profile_id is None
            else bound_server_profile_id
        )
        if not isinstance(profile_id, UUID):
            raise LocalProjectIndexError(
                "bound server profile reference must be a UUID"
            )
        reference = KnownProjectReference(
            project_id=project.project_id,
            server_profile_id=profile_id,
            remote_project_path=project.remote_project_path,
            display_name=project.display_name,
            last_seen_revision=project.revision,
            workflow_kind=project.workflow_kind,
        )
        self.save(reference)
        return reference

    def recycle(
        self,
        project: CalculationProject,
        *,
        bound_server_profile_id: UUID,
        submitted_at: datetime | None,
        recycled_at: datetime,
    ) -> RecycledProjectReference:
        if not isinstance(project, CalculationProject):
            raise LocalProjectIndexError("recycle requires a CalculationProject")
        if not isinstance(bound_server_profile_id, UUID):
            raise LocalProjectIndexError(
                "recycle requires the selected server-profile UUID"
            )
        entry = RecycledProjectReference(
            project_id=project.project_id,
            server_profile_id=bound_server_profile_id,
            remote_project_path=project.remote_project_path,
            display_name=project.display_name,
            submitted_at=submitted_at,
            recycled_at=recycled_at,
        )
        reference = KnownProjectReference(
            project_id=project.project_id,
            server_profile_id=bound_server_profile_id,
            remote_project_path=project.remote_project_path,
            display_name=project.display_name,
            last_seen_revision=project.revision,
            workflow_kind=project.workflow_kind,
        )
        references, recycled = self._load_state()
        references = _replace_or_append_reference(references, reference)
        recycled = _replace_or_append_recycled(recycled, entry)
        self._write(references, recycled)
        return entry

    def recycle_reference(
        self,
        *,
        project_id: UUID,
        bound_server_profile_id: UUID,
        remote_project_path: str,
        display_name: str,
        submitted_at: datetime | None,
        recycled_at: datetime,
    ) -> RecycledProjectReference:
        """Persist a tombstone for another managed manifest kind.

        This deliberately does not invent a ``KnownProjectReference``: only
        calculation-project manifests carry its authoritative revision field.
        """

        entry = RecycledProjectReference(
            project_id=project_id,
            server_profile_id=bound_server_profile_id,
            remote_project_path=remote_project_path,
            display_name=display_name,
            submitted_at=submitted_at,
            recycled_at=recycled_at,
        )
        references, recycled = self._load_state()
        recycled = _replace_or_append_recycled(recycled, entry)
        self._write(references, recycled)
        return entry
    def restore(
        self,
        server_profile_id: UUID,
        project_id: UUID,
    ) -> RecycledProjectReference:
        references, recycled = self._load_state()
        identity = _recycle_identity(server_profile_id, project_id)
        restored = next((item for item in recycled if item.identity == identity), None)
        if restored is None:
            raise LocalProjectIndexError("recycled project entry is unavailable")
        self._write(
            references,
            tuple(item for item in recycled if item.identity != identity),
        )
        return restored

    def forget(self, server_profile_id: UUID, project_id: UUID) -> None:
        references, recycled = self._load_state()
        identity = _recycle_identity(server_profile_id, project_id)
        self._write(
            tuple(
                item
                for item in references
                if not (
                    item.project_id == project_id
                    and item.server_profile_id == server_profile_id
                )
            ),
            tuple(item for item in recycled if item.identity != identity),
        )

    def _write(
        self,
        references: tuple[KnownProjectReference, ...],
        recycled: tuple[RecycledProjectReference, ...],
    ) -> None:
        references = tuple(sorted(references, key=lambda item: str(item.project_id)))
        recycled = tuple(
            sorted(
                recycled,
                key=lambda item: (str(item.server_profile_id), str(item.project_id)),
            )
        )
        document = {
            "schema_version": _INDEX_SCHEMA_VERSION,
            "projects": [
                {
                    "project_id": str(item.project_id),
                    "server_profile_id": str(item.server_profile_id),
                    "remote_project_path": item.remote_project_path,
                    "display_name": item.display_name,
                    "last_seen_revision": item.last_seen_revision,
                    "workflow_kind": item.workflow_kind.value,
                }
                for item in references
            ],
            "recycled_projects": [
                {
                    "project_id": str(item.project_id),
                    "server_profile_id": str(item.server_profile_id),
                    "remote_project_path": item.remote_project_path,
                    "display_name": item.display_name,
                    "submitted_at": _format_optional_timestamp(item.submitted_at),
                    "recycled_at": item.recycled_at.isoformat(),
                }
                for item in recycled
            ],
        }
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(text, encoding="utf-8", newline="\n")
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise LocalProjectIndexError(
                "local project index could not be persisted"
            ) from None


def _replace_or_append_reference(
    references: tuple[KnownProjectReference, ...],
    replacement: KnownProjectReference,
) -> tuple[KnownProjectReference, ...]:
    updated = tuple(
        replacement if item.project_id == replacement.project_id else item
        for item in references
    )
    if replacement.project_id not in {item.project_id for item in references}:
        updated = (*updated, replacement)
    return tuple(sorted(updated, key=lambda item: str(item.project_id)))


def _replace_or_append_recycled(
    recycled: tuple[RecycledProjectReference, ...],
    entry: RecycledProjectReference,
) -> tuple[RecycledProjectReference, ...]:
    updated = tuple(
        entry if item.identity == entry.identity else item for item in recycled
    )
    if entry.identity not in {item.identity for item in recycled}:
        updated = (*updated, entry)
    return updated


def _recycle_identity(
    server_profile_id: UUID,
    project_id: UUID,
) -> tuple[UUID, UUID]:
    if not isinstance(server_profile_id, UUID) or not isinstance(project_id, UUID):
        raise LocalProjectIndexError("recycle identity requires two UUIDs")
    return server_profile_id, project_id


def _validate_optional_timestamp(value: datetime | None, field_name: str) -> None:
    if value is not None:
        _validate_timestamp(value, field_name)


def _validate_timestamp(value: object, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise LocalProjectIndexError(f"{field_name} must be timezone-aware")


def _parse_optional_timestamp(value: object) -> datetime | None:
    return None if value is None else _parse_timestamp(value)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("local project timestamp must be ISO 8601 text")
    parsed = datetime.fromisoformat(value)
    _validate_timestamp(parsed, "local project timestamp")
    return parsed


def _format_optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
