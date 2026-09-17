"""Read one authoritative managed-project geometry for local 3D inspection."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
from pathlib import PurePosixPath
from uuid import UUID

from moltage.aims.recovery import (
    AimsRecoveryError,
    recover_optimized_structure,
    recover_submitted_structure,
)
from moltage.app.connection_service import ServerConnectionService
from moltage.app.project_orbital_cube import (
    ProjectOrbitalCubeBinding,
    discover_step1_orbital_cubes,
)
from moltage.app.project_recovery import (
    ProjectProfileRebindRequired,
    ProjectRecoverySnapshot,
)
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.remote.executor import RemoteExecutor, RemotePathNotFoundError
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.app.orca_submission import ORCA_OPT_INPUT
from moltage.orca.evidence import OrcaEvidenceError, parse_orca_final_xyz
from moltage.orca.input_writer import parse_rendered_orca_structure
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii


class ProjectGeometryViewError(RuntimeError):
    """Raised when a requested project geometry is unavailable or invalid."""


class ProjectGeometryViewKind(StrEnum):
    """The three user-visible geometry choices exposed by project indicators."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    TRANSPORT = "TRANSPORT"


@dataclass(frozen=True, slots=True)
class ProjectGeometryViewRequest:
    """One explicit read-only geometry request from a loaded project snapshot."""

    profile: ServerProfile
    project: CalculationProject
    step_kind: ProjectStepKind
    view_kind: ProjectGeometryViewKind
    profile_rebind_confirmed: bool = False
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("project geometry view requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("project geometry view requires a CalculationProject")
        if not isinstance(self.step_kind, ProjectStepKind):
            raise TypeError("project geometry view requires a supported step")
        if not isinstance(self.view_kind, ProjectGeometryViewKind):
            raise TypeError("project geometry view kind is unsupported")
        if not isinstance(self.profile_rebind_confirmed, bool):
            raise TypeError("profile rebind confirmation must be boolean")
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("temporary password must be nonempty text or None")
        _validate_selected_root(self.profile, self.project.remote_project_path)
        if self.view_kind not in project_geometry_view_kinds(
            self.project,
            self.step_kind,
        ):
            raise ProjectGeometryViewError(
                "The selected project step does not have that geometry available."
            )


@dataclass(frozen=True, slots=True)
class ProjectGeometryViewResult:
    """Parsed structure and deterministic read-only workspace identity metadata."""

    project_id: UUID
    project_name: str
    step_kind: ProjectStepKind
    view_kind: ProjectGeometryViewKind
    source_filename: str
    structure: MolecularStructure
    connectivity: Connectivity
    orbital_binding: ProjectOrbitalCubeBinding | None = None
    recovery_snapshot: ProjectRecoverySnapshot | None = None
    recovery_profile: ServerProfile | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID):
            raise TypeError("project geometry result requires a project UUID")
        if not isinstance(self.project_name, str) or not self.project_name.strip():
            raise ValueError("project geometry result requires a project name")
        if not isinstance(self.step_kind, ProjectStepKind):
            raise TypeError("project geometry result step is unsupported")
        if not isinstance(self.view_kind, ProjectGeometryViewKind):
            raise TypeError("project geometry result kind is unsupported")
        if self.source_filename not in {
            "geometry.in",
            "geometry.in.next_step",
            ORCA_OPT_INPUT,
            "orca_opt.xyz",
        }:
            raise ValueError("project geometry source filename is unsupported")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("project geometry result requires a molecular structure")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("project geometry result requires connectivity")
        if self.recovery_snapshot is not None or self.recovery_profile is not None:
            if (
                not isinstance(self.recovery_snapshot, ProjectRecoverySnapshot)
                or not isinstance(self.recovery_profile, ServerProfile)
                or self.step_kind is not ProjectStepKind.ORCA_OPTIMIZATION
                or self.recovery_snapshot.project.project_id != self.project_id
                or self.recovery_snapshot.project.workflow_kind
                is not CalculationWorkflowKind.ORCA
            ):
                raise ValueError("ORCA geometry recovery context is invalid")
        if self.orbital_binding is not None:
            binding = self.orbital_binding
            if not isinstance(binding, ProjectOrbitalCubeBinding):
                raise TypeError("project geometry orbital binding is invalid")
            if (
                self.step_kind is not ProjectStepKind.MOLECULE_OPT
                or self.view_kind is not ProjectGeometryViewKind.OUTPUT
            ):
                raise ValueError(
                    "orbital Cubes may be bound only to Step-1 output geometry"
                )
            if binding.project.project_id != self.project_id:
                raise ValueError("orbital Cube binding project identity differs")
            if binding.expected_structure != self.structure:
                raise ValueError("orbital Cube binding structure differs")

    @property
    def workspace_role(self) -> str:
        if self.step_kind is ProjectStepKind.ORCA_OPTIMIZATION:
            return f"ORCA_OPTIMIZATION_{self.view_kind.value}_GEOMETRY"
        if self.view_kind is ProjectGeometryViewKind.TRANSPORT:
            return "STEP_3_4_TRANSPORT_GEOMETRY"
        step_number = _step_number(self.step_kind)
        return f"STEP_{step_number}_{self.view_kind.value}_GEOMETRY"

    @property
    def display_title(self) -> str:
        if self.step_kind is ProjectStepKind.ORCA_OPTIMIZATION:
            suffix = f"ORCA Optimization {self.view_kind.value.title()} Geometry"
        elif self.view_kind is ProjectGeometryViewKind.TRANSPORT:
            suffix = "Step 3/4 Transport Geometry"
        else:
            suffix = (
                f"Step {_step_number(self.step_kind)} "
                f"{self.view_kind.value.title()} Geometry"
            )
        return f"{self.project_name} — {suffix}"


def project_geometry_view_kinds(
    project: CalculationProject,
    step_kind: ProjectStepKind,
) -> tuple[ProjectGeometryViewKind, ...]:
    """Return only geometry choices justified by authoritative workflow state."""

    if not isinstance(project, CalculationProject):
        raise TypeError("project geometry availability requires a project")
    if not isinstance(step_kind, ProjectStepKind):
        raise TypeError("project geometry availability requires a supported step")
    if project.workflow_kind is CalculationWorkflowKind.ORCA:
        if step_kind is not ProjectStepKind.ORCA_OPTIMIZATION:
            return ()
        step = _project_step(project, step_kind)
        if step.state in {
            ProjectStepState.NOT_STARTED,
            ProjectStepState.SKIPPED,
        }:
            return ()
        choices = [ProjectGeometryViewKind.INPUT]
        evidence = step.orca_optimization_result
        if evidence is not None and evidence.final_xyz_valid:
            choices.append(ProjectGeometryViewKind.OUTPUT)
        return tuple(choices)
    if step_kind in {
        ProjectStepKind.TRANSPORT_CONVERGENCE,
        ProjectStepKind.TRANSMISSION,
    }:
        transport = _project_step(project, ProjectStepKind.TRANSPORT_CONVERGENCE)
        if transport.state in {
            ProjectStepState.NOT_STARTED,
            ProjectStepState.SKIPPED,
        }:
            return ()
        return (ProjectGeometryViewKind.TRANSPORT,)

    step = _project_step(project, step_kind)
    if step.state in {
        ProjectStepState.NOT_STARTED,
        ProjectStepState.SKIPPED,
    }:
        return ()
    choices = [ProjectGeometryViewKind.INPUT]
    if step.state is ProjectStepState.SUCCEEDED:
        choices.append(ProjectGeometryViewKind.OUTPUT)
    return tuple(choices)


class ProjectGeometryViewService:
    """Load one geometry over a short-lived read-only SSH/SFTP connection."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        *,
        covalent_radii_loader: Callable[[], Mapping[str, float]] = (
            load_default_covalent_radii
        ),
    ) -> None:
        self._connection_service = connection_service
        self._covalent_radii_loader = covalent_radii_loader

    def load(
        self,
        request: ProjectGeometryViewRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> ProjectGeometryViewResult:
        """Reload identity, read exact files, and parse without workflow mutation."""

        if not isinstance(request, ProjectGeometryViewRequest):
            raise TypeError("project geometry view requires a validated request")
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            report("Validating managed project identity...")
            project = RemoteProjectRepository(executor).load(
                request.project.remote_project_path,
                preserve_remote_errors=True,
            )
            if project.project_id != request.project.project_id:
                raise ProjectGeometryViewError(
                    "The managed project identity changed before geometry viewing."
                )
            if (
                project.server_profile_id != request.profile.profile_id
                and not request.profile_rebind_confirmed
            ):
                raise ProjectProfileRebindRequired(project, request.profile)
            if request.view_kind not in project_geometry_view_kinds(
                project,
                request.step_kind,
            ):
                raise ProjectGeometryViewError(
                    "The selected geometry is no longer available; Refresh the project."
                )

            report("Reading project geometry...")
            if project.workflow_kind is CalculationWorkflowKind.ORCA:
                return self._load_orca_geometry(executor, request, project)
            source_step = (
                ProjectStepKind.TRANSPORT_CONVERGENCE
                if request.view_kind is ProjectGeometryViewKind.TRANSPORT
                else request.step_kind
            )
            directory = PurePosixPath(remote_step_directory(project, source_step))
            control_text = _read_required_file(
                executor,
                str(directory / "control.in"),
                "control.in",
            )
            original_geometry = _read_required_file(
                executor,
                str(directory / "geometry.in"),
                "geometry.in",
            )
            try:
                if request.view_kind is ProjectGeometryViewKind.OUTPUT:
                    structure = recover_optimized_structure(
                        control_text=control_text,
                        original_geometry_text=original_geometry,
                        next_geometry_text=_read_required_file(
                            executor,
                            str(directory / "geometry.in.next_step"),
                            "geometry.in.next_step",
                        ),
                    )
                    source_filename = "geometry.in.next_step"
                else:
                    structure = recover_submitted_structure(
                        control_text=control_text,
                        geometry_text=original_geometry,
                        source_name="geometry.in",
                    )
                    source_filename = "geometry.in"
            except AimsRecoveryError as error:
                raise ProjectGeometryViewError(str(error)) from None
            connectivity = infer_connectivity(
                structure,
                self._covalent_radii_loader(),
            )
            orbital_binding = None
            if (
                request.step_kind is ProjectStepKind.MOLECULE_OPT
                and request.view_kind is ProjectGeometryViewKind.OUTPUT
            ):
                orbital_catalog = discover_step1_orbital_cubes(
                    executor,
                    str(directory),
                    control_text,
                )
                orbital_binding = ProjectOrbitalCubeBinding(
                    profile=request.profile,
                    project=project,
                    expected_structure=structure,
                    artifacts=orbital_catalog.available,
                    diagnostic=orbital_catalog.diagnostic,
                    profile_rebind_confirmed=request.profile_rebind_confirmed,
                )
            return ProjectGeometryViewResult(
                project.project_id,
                project.remote_directory_name,
                request.step_kind,
                request.view_kind,
                source_filename,
                structure,
                connectivity,
                orbital_binding,
            )
        finally:
            executor.close()

    def _load_orca_geometry(
        self,
        executor: RemoteExecutor,
        request: ProjectGeometryViewRequest,
        project: CalculationProject,
    ) -> ProjectGeometryViewResult:
        """Read only ORCA-native artifacts; never enter the FHI-aims path."""

        if request.step_kind is not ProjectStepKind.ORCA_OPTIMIZATION:
            raise ProjectGeometryViewError(
                "Only the ORCA optimization stage exposes molecular geometry."
            )
        root = PurePosixPath(project.remote_project_path)
        step = _project_step(project, ProjectStepKind.ORCA_OPTIMIZATION)
        input_bytes = _read_required_file(
            executor,
            str(root / ORCA_OPT_INPUT),
            ORCA_OPT_INPUT,
        )
        expected_input_hash = dict(step.input_hashes).get(ORCA_OPT_INPUT)
        if (
            expected_input_hash is None
            or hashlib.sha256(input_bytes).hexdigest() != expected_input_hash
        ):
            raise ProjectGeometryViewError(
                "The current orca_opt.inp does not match accepted project evidence."
            )
        try:
            submitted = parse_rendered_orca_structure(input_bytes)
        except ValueError as error:
            raise ProjectGeometryViewError(str(error)) from None
        if tuple(atom.element for atom in submitted) != step.orca_submitted_elements:
            raise ProjectGeometryViewError(
                "Submitted ORCA atom identity differs from the project manifest."
            )

        if request.view_kind is ProjectGeometryViewKind.OUTPUT:
            xyz = _read_required_file(
                executor,
                str(root / "orca_opt.xyz"),
                "orca_opt.xyz",
            )
            evidence = step.orca_optimization_result
            if (
                evidence is None
                or evidence.xyz_sha256 is None
                or hashlib.sha256(xyz).hexdigest() != evidence.xyz_sha256
            ):
                raise ProjectGeometryViewError(
                    "The current orca_opt.xyz does not match verified project evidence."
                )
            try:
                structure = parse_orca_final_xyz(xyz, submitted)
            except OrcaEvidenceError as error:
                raise ProjectGeometryViewError(str(error)) from None
            source_filename = "orca_opt.xyz"
        else:
            structure = submitted
            source_filename = ORCA_OPT_INPUT

        connectivity = infer_connectivity(
            structure,
            self._covalent_radii_loader(),
        )
        return ProjectGeometryViewResult(
            project.project_id,
            project.remote_directory_name,
            request.step_kind,
            request.view_kind,
            source_filename,
            structure,
            connectivity,
            recovery_snapshot=ProjectRecoverySnapshot(
                project,
                ProjectStepKind.ORCA_OPTIMIZATION,
                f"ORCA geometry verified from {source_filename}.",
                submitted_structure=submitted,
                optimized_structure=(
                    structure
                    if request.view_kind is ProjectGeometryViewKind.OUTPUT
                    else None
                ),
                connectivity=connectivity,
                profile_rebind_confirmed=request.profile_rebind_confirmed,
            ),
            recovery_profile=request.profile,
        )


def _project_step(project: CalculationProject, kind: ProjectStepKind):
    return next(step for step in project.steps if step.kind is kind)


def _read_required_file(
    executor: RemoteExecutor,
    path: str,
    filename: str,
) -> bytes:
    try:
        return executor.read_bytes(path)
    except RemotePathNotFoundError:
        raise ProjectGeometryViewError(
            f"The requested project geometry is missing required {filename}."
        ) from None


def _step_number(step_kind: ProjectStepKind) -> int:
    if step_kind is ProjectStepKind.ORCA_OPTIMIZATION:
        return 1
    return tuple(ProjectStepKind).index(step_kind) + 1


def _validate_selected_root(profile: ServerProfile, remote_project_path: str) -> None:
    if (
        not isinstance(remote_project_path, str)
        or PurePosixPath(remote_project_path).parent
        != PurePosixPath(profile.remote_project_root)
    ):
        raise ProjectGeometryViewError(
            "Project geometry viewing is limited to first-level managed projects "
            "in the selected server workspace."
        )
