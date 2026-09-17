"""Concrete runtime identities for reviewed desktop workspace kinds."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from moltage.aitranss.transmission import TransmissionResult
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.domain.calculation_project import ProjectStepKind, ProjectStepState
from moltage.orca.wbl_artifacts import OrcaWblPresentation, WBL_JSON_FILENAME


class WorkspaceKind(StrEnum):
    """Concrete work-object kinds routed by the main tabbed workspace."""

    GEOMETRY = "GEOMETRY"
    TRANSMISSION = "TRANSMISSION"
    ORCA_WBL = "ORCA_WBL"
    DENSITY = "DENSITY"
    DENSITY_RESULT = "DENSITY_RESULT"
    TIGHT_BINDING = "TIGHT_BINDING"


@dataclass(frozen=True, slots=True)
class LocalGeometryWorkspaceIdentity:
    """Identity of one local structure-backed Geometry workspace."""

    canonical_source_path: Path

    @classmethod
    def from_path(cls, source_path: Path) -> "LocalGeometryWorkspaceIdentity":
        if not isinstance(source_path, Path):
            raise TypeError("local Geometry source must be a Path")
        return cls(source_path.expanduser().resolve())


@dataclass(frozen=True, slots=True)
class ManagedGeometryWorkspaceIdentity:
    """Identity of one managed-project Geometry role/context."""

    project_id: UUID
    geometry_role: str

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID):
            raise TypeError("managed Geometry project ID must be a UUID")
        if not isinstance(self.geometry_role, str) or not self.geometry_role.strip():
            raise ValueError("managed Geometry role must not be empty")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ProjectRecoverySnapshot,
    ) -> "ManagedGeometryWorkspaceIdentity":
        if not isinstance(snapshot, ProjectRecoverySnapshot):
            raise TypeError("managed Geometry identity requires a recovery snapshot")
        return cls(
            snapshot.project.project_id,
            snapshot.active_step_kind.value,
        )


GeometryWorkspaceIdentity = (
    LocalGeometryWorkspaceIdentity | ManagedGeometryWorkspaceIdentity
)


@dataclass(frozen=True, slots=True)
class TransmissionWorkspaceIdentity:
    """Identity of one authoritative successful Step-4 result."""

    project_id: UUID
    job_id: str
    submit_script_filename: str
    slurm_output_filename: str
    input_hashes: tuple[tuple[str, str], ...]
    result_filename: str
    result_digest: str


@dataclass(frozen=True, slots=True)
class TransmissionWorkspaceRequest:
    """Validated local request emitted by Project Manager to the main window."""

    identity: TransmissionWorkspaceIdentity
    project_name: str
    job_id: str
    result_filename: str
    result: TransmissionResult

    @property
    def display_title(self) -> str:
        return f"{self.project_name} — Transmission"

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ProjectRecoverySnapshot,
    ) -> "TransmissionWorkspaceRequest":
        if not isinstance(snapshot, ProjectRecoverySnapshot):
            raise TypeError("Transmission workspace requires a recovery snapshot")
        if not snapshot.can_view_transmission:
            raise ValueError("the project has no authoritative successful transmission")
        result = snapshot.transmission_result
        result_filename = snapshot.transmission_result_filename
        step = snapshot.active_step
        if result is None or result_filename is None or step.job_id is None:
            raise ValueError("successful transmission provenance is incomplete")
        identity = TransmissionWorkspaceIdentity(
            project_id=snapshot.project.project_id,
            job_id=step.job_id,
            submit_script_filename=step.submit_script_filename or "",
            slurm_output_filename=step.slurm_output_filename or "",
            input_hashes=step.input_hashes,
            result_filename=result_filename,
            result_digest=_transmission_result_digest(result),
        )
        return cls(
            identity=identity,
            project_name=snapshot.project.remote_directory_name,
            job_id=step.job_id,
            result_filename=result_filename,
            result=result,
        )


@dataclass(frozen=True, slots=True)
class OrcaWblWorkspaceIdentity:
    """Identity of one verified persisted ORCA WBL result."""

    project_id: UUID
    result_digest: str


@dataclass(frozen=True, slots=True)
class OrcaWblWorkspaceRequest:
    """Validated request emitted by Project Manager for a WBL result tab."""

    identity: OrcaWblWorkspaceIdentity
    project_name: str
    presentation: OrcaWblPresentation

    @property
    def display_title(self) -> str:
        return f"{self.project_name} — ORCA WBL"

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ProjectRecoverySnapshot,
    ) -> "OrcaWblWorkspaceRequest":
        if not isinstance(snapshot, ProjectRecoverySnapshot):
            raise TypeError("ORCA WBL workspace requires a recovery snapshot")
        presentation = snapshot.orca_wbl_presentation
        step = next(
            (
                item
                for item in snapshot.project.steps
                if item.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
            ),
            None,
        )
        if (
            presentation is None
            or step is None
            or step.state is not ProjectStepState.SUCCEEDED
            or step.orca_wbl_result is None
        ):
            raise ValueError("the project has no verified ORCA WBL result")
        digest = dict(step.orca_wbl_result.artifact_hashes).get(WBL_JSON_FILENAME)
        if digest is None:
            raise ValueError("ORCA WBL result identity is incomplete")
        return cls(
            OrcaWblWorkspaceIdentity(snapshot.project.project_id, digest),
            snapshot.project.remote_directory_name,
            presentation,
        )


def _transmission_result_digest(result: TransmissionResult) -> str:
    """Fingerprint parsed presentation data without changing scientific values."""

    if not isinstance(result, TransmissionResult):
        raise TypeError("Transmission result digest requires a TransmissionResult")
    digest = sha256()
    fields = (
        result.bias_volts.hex(),
        result.fermi_energy_hartree.hex(),
        result.spin_mode.value,
        *result.header_lines,
    )
    for field in fields:
        digest.update(field.encode("utf-8"))
        digest.update(b"\0")
    for point in result.points:
        for value in (
            point.energy_hartree,
            point.energy_relative_ev,
            point.transmission_per_spin,
        ):
            digest.update(value.hex().encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()
