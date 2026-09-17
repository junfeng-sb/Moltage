"""Discover and load Step-1 orbital Cubes as presentation-only data."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from moltage.aims.orbital_cube import (
    FRONTIER_ORBITAL_ORDER,
    FrontierOrbital,
    OrbitalCubeValidationError,
    orbital_cube_filename,
    parse_generated_orbital_cube_directives,
)
from moltage.aims.recovery import (
    AimsRecoveryError,
    recover_optimized_structure,
)
from moltage.app.connection_service import ServerConnectionService
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.remote.executor import (
    RemoteExecutor,
    RemoteExecutorError,
    RemotePathNotFoundError,
)
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.structure.cube import (
    CubeCoordinateUnit,
    CubeFormatError,
    CubeScalarField,
    read_cube,
)


class ProjectOrbitalCubeError(RuntimeError):
    """Raised when an advertised project orbital cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class ProjectOrbitalCubeArtifact:
    """One non-empty canonical Cube found in the completed Step-1 directory."""

    state: FrontierOrbital | int
    filename: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.state, (FrontierOrbital, int)) or isinstance(
            self.state,
            bool,
        ):
            raise TypeError("orbital Cube state is unsupported")
        if isinstance(self.state, int) and self.state <= 0:
            raise ValueError("orbital Cube state number must be positive")
        if self.filename != orbital_cube_filename(self.state):
            raise ValueError("orbital Cube filename does not match its state")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise TypeError("orbital Cube size must be an integer")
        if self.size_bytes <= 0:
            raise ValueError("orbital Cube file must be non-empty")

    @property
    def display_label(self) -> str:
        if isinstance(self.state, int):
            return str(self.state)
        return {
            FrontierOrbital.HOMO_MINUS_2: "HOMO-2",
            FrontierOrbital.HOMO_MINUS_1: "HOMO-1",
            FrontierOrbital.HOMO: "HOMO",
            FrontierOrbital.LUMO: "LUMO",
            FrontierOrbital.LUMO_PLUS_1: "LUMO+1",
            FrontierOrbital.LUMO_PLUS_2: "LUMO+2",
        }[self.state]


@dataclass(frozen=True, slots=True)
class ProjectOrbitalCubeCatalog:
    """Bounded Step-1 directory result, including visible missing evidence."""

    available: tuple[ProjectOrbitalCubeArtifact, ...] = ()
    missing_filenames: tuple[str, ...] = ()
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, ProjectOrbitalCubeArtifact)
            for item in self.available
        ):
            raise TypeError("orbital Cube catalog contains an invalid artifact")
        if any(
            not isinstance(name, str) or not name
            for name in self.missing_filenames
        ):
            raise TypeError("missing orbital Cube filenames must be nonempty text")
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("orbital Cube diagnostic must be nonempty text")


@dataclass(frozen=True, slots=True)
class ProjectOrbitalCubeBinding:
    """Immutable Step-1 source context shared by editable and read-only views."""

    profile: ServerProfile
    project: CalculationProject
    expected_structure: MolecularStructure
    artifacts: tuple[ProjectOrbitalCubeArtifact, ...] = ()
    diagnostic: str | None = None
    profile_rebind_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("orbital Cube binding requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("orbital Cube binding requires a calculation project")
        if not isinstance(self.expected_structure, MolecularStructure):
            raise TypeError("orbital Cube binding requires the recovered structure")
        artifacts = tuple(self.artifacts)
        if any(
            not isinstance(item, ProjectOrbitalCubeArtifact) for item in artifacts
        ):
            raise TypeError("orbital Cube binding contains an invalid artifact")
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("orbital Cube binding diagnostic must be nonempty text")
        if not isinstance(self.profile_rebind_confirmed, bool):
            raise TypeError("profile rebind confirmation must be boolean")
        _validate_selected_root(self.profile, self.project.remote_project_path)
        object.__setattr__(self, "artifacts", artifacts)


@dataclass(frozen=True, slots=True)
class ProjectOrbitalCubeLoadRequest:
    """One explicit, on-demand load for a catalogued Step-1 Cube."""

    profile: ServerProfile
    project: CalculationProject
    artifact: ProjectOrbitalCubeArtifact
    expected_structure: MolecularStructure
    profile_rebind_confirmed: bool = False
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("orbital Cube loading requires a ServerProfile")
        if not isinstance(self.project, CalculationProject):
            raise TypeError("orbital Cube loading requires a calculation project")
        if not isinstance(self.artifact, ProjectOrbitalCubeArtifact):
            raise TypeError("orbital Cube loading requires a catalogued artifact")
        if not isinstance(self.expected_structure, MolecularStructure):
            raise TypeError("orbital Cube loading requires the recovered structure")
        if not isinstance(self.profile_rebind_confirmed, bool):
            raise TypeError("profile rebind confirmation must be boolean")
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("temporary password must be nonempty text or None")
        _validate_selected_root(self.profile, self.project.remote_project_path)


@dataclass(frozen=True, slots=True)
class ProjectOrbitalCubeLoadResult:
    """A validated scalar field that does not carry workflow structure state."""

    artifact: ProjectOrbitalCubeArtifact
    scalar_field: CubeScalarField

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ProjectOrbitalCubeArtifact):
            raise TypeError("orbital Cube result requires its artifact")
        if not isinstance(self.scalar_field, CubeScalarField):
            raise TypeError("orbital Cube result requires a scalar field")


def discover_step1_orbital_cubes(
    executor: RemoteExecutor,
    step_directory: str,
    control_text: bytes,
) -> ProjectOrbitalCubeCatalog:
    """List one directory and retain only requested, canonical, non-empty Cubes."""

    try:
        states = _requested_orbital_states(control_text)
    except (UnicodeError, OrbitalCubeValidationError, ValueError) as error:
        return ProjectOrbitalCubeCatalog(
            diagnostic=f"Orbital Cube detection could not parse control.in: {error}"
        )
    if not states:
        return ProjectOrbitalCubeCatalog()
    try:
        entries = executor.list_directory(step_directory)
    except RemoteExecutorError as error:
        return ProjectOrbitalCubeCatalog(
            diagnostic=f"Orbital Cube detection could not list Step 1: {error}"
        )

    regular_names = {
        entry.name for entry in entries if not entry.is_directory
    }
    available: list[ProjectOrbitalCubeArtifact] = []
    missing: list[str] = []
    problems: list[str] = []
    for state in states:
        filename = orbital_cube_filename(state)
        if filename not in regular_names:
            missing.append(filename)
            continue
        path = str(PurePosixPath(step_directory) / filename)
        try:
            stat = executor.stat(path)
        except RemoteExecutorError as error:
            problems.append(f"{filename}: {error}")
            continue
        if stat.is_directory or stat.size is None or stat.size <= 0:
            missing.append(filename)
            continue
        available.append(ProjectOrbitalCubeArtifact(state, filename, stat.size))

    diagnostic_parts: list[str] = []
    if missing:
        diagnostic_parts.append(
            "Requested orbital Cube file(s) not available: "
            + ", ".join(missing)
        )
    if problems:
        diagnostic_parts.append(
            "Orbital Cube metadata could not be read: " + "; ".join(problems)
        )
    return ProjectOrbitalCubeCatalog(
        tuple(available),
        tuple(missing),
        " ".join(diagnostic_parts) or None,
    )


class ProjectOrbitalCubeService:
    """Download one selected Cube over a short-lived read-only connection."""

    def __init__(self, connection_service: ServerConnectionService) -> None:
        self._connection_service = connection_service

    def load(
        self,
        request: ProjectOrbitalCubeLoadRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> ProjectOrbitalCubeLoadResult:
        if not isinstance(request, ProjectOrbitalCubeLoadRequest):
            raise TypeError("orbital Cube loading requires a validated request")
        report = progress or (lambda _message: None)
        report(f"Connecting to {request.profile.name}...")
        executor = self._connection_service.connect_for_remote_operation(
            request.profile,
            request.supplied_password,
        )
        try:
            report("Validating managed project and Step-1 Cube metadata...")
            project = RemoteProjectRepository(executor).load(
                request.project.remote_project_path,
                preserve_remote_errors=True,
            )
            if project.project_id != request.project.project_id:
                raise ProjectOrbitalCubeError(
                    "The managed project identity changed; Refresh before loading "
                    "an orbital Cube."
                )
            if (
                project.server_profile_id != request.profile.profile_id
                and not request.profile_rebind_confirmed
            ):
                raise ProjectOrbitalCubeError(
                    "The managed project is not bound to the selected server profile."
                )
            step = next(
                item
                for item in project.steps
                if item.kind is ProjectStepKind.MOLECULE_OPT
            )
            if step.state is not ProjectStepState.SUCCEEDED:
                raise ProjectOrbitalCubeError(
                    "Step 1 is no longer marked successful; Refresh the project."
                )
            directory = PurePosixPath(
                remote_step_directory(project, ProjectStepKind.MOLECULE_OPT)
            )
            control_text = _read_required(
                executor,
                str(directory / "control.in"),
                "control.in",
            )
            catalog = discover_step1_orbital_cubes(
                executor,
                str(directory),
                control_text,
            )
            current = next(
                (
                    item
                    for item in catalog.available
                    if item.state == request.artifact.state
                    and item.filename == request.artifact.filename
                ),
                None,
            )
            if current is None:
                detail = f" {catalog.diagnostic}" if catalog.diagnostic else ""
                raise ProjectOrbitalCubeError(
                    "The selected orbital Cube is no longer available; Refresh "
                    f"the project.{detail}"
                )
            if current.size_bytes != request.artifact.size_bytes:
                raise ProjectOrbitalCubeError(
                    "The selected orbital Cube changed after recovery; Refresh "
                    "the project before displaying it."
                )

            recovered = _recover_step1_structure(executor, directory, control_text)
            if not _structures_match(recovered, request.expected_structure):
                raise ProjectOrbitalCubeError(
                    "The recovered Step-1 structure changed after this workspace "
                    "was opened; Refresh before displaying an orbital Cube."
                )

            with TemporaryDirectory(prefix="moltage_orbital_") as temporary:
                local_path = Path(temporary) / current.filename
                last_percent = -1

                def transfer_progress(current_bytes: int, total_bytes: int) -> None:
                    nonlocal last_percent
                    if total_bytes <= 0:
                        return
                    percent = min(100, max(0, current_bytes * 100 // total_bytes))
                    if percent == last_percent or (
                        percent % 5 != 0 and current_bytes != total_bytes
                    ):
                        return
                    last_percent = percent
                    report(
                        f"Downloading {current.display_label} Cube: "
                        f"{_format_bytes(current_bytes)} / {_format_bytes(total_bytes)}"
                    )

                executor.download_file(
                    str(directory / current.filename),
                    str(local_path),
                    transfer_progress,
                )
                report(f"Validating {current.display_label} Cube...")
                try:
                    parsed = read_cube(
                        local_path,
                        coordinate_unit=CubeCoordinateUnit.BOHR,
                    )
                except (OSError, UnicodeError, CubeFormatError) as error:
                    raise ProjectOrbitalCubeError(str(error)) from None
            if not _structures_match(parsed.structure, recovered):
                raise ProjectOrbitalCubeError(
                    "The Cube atom list or coordinates do not match the recovered "
                    "Step-1 molecule. The file was not displayed."
                )
            return ProjectOrbitalCubeLoadResult(current, parsed.scalar_field)
        finally:
            executor.close()


def _requested_orbital_states(
    control_text: bytes,
) -> tuple[FrontierOrbital | int, ...]:
    try:
        control = control_text.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnicodeError("control.in is not valid UTF-8 text") from error
    prefix_lines: list[str] = []
    for line in control.splitlines():
        stripped = line.strip()
        if stripped.startswith("species ") or stripped == "# Species":
            break
        if stripped and not stripped.startswith("#"):
            prefix_lines.append(stripped)
    settings, _remaining = parse_generated_orbital_cube_directives(
        tuple(prefix_lines)
    )
    selected = frozenset(settings.frontier_orbitals)
    return (
        *(item for item in FRONTIER_ORBITAL_ORDER if item in selected),
        *settings.eigenstate_indices,
    )


def _recover_step1_structure(
    executor: RemoteExecutor,
    directory: PurePosixPath,
    control_text: bytes,
) -> MolecularStructure:
    try:
        return recover_optimized_structure(
            control_text=control_text,
            original_geometry_text=_read_required(
                executor,
                str(directory / "geometry.in"),
                "geometry.in",
            ),
            next_geometry_text=_read_required(
                executor,
                str(directory / "geometry.in.next_step"),
                "geometry.in.next_step",
            ),
        )
    except AimsRecoveryError as error:
        raise ProjectOrbitalCubeError(str(error)) from None


def _read_required(
    executor: RemoteExecutor,
    path: str,
    filename: str,
) -> bytes:
    try:
        return executor.read_bytes(path)
    except RemotePathNotFoundError:
        raise ProjectOrbitalCubeError(
            f"The Step-1 result is missing required {filename}."
        ) from None


def _structures_match(
    first: MolecularStructure,
    second: MolecularStructure,
    *,
    tolerance_angstrom: float = 1.0e-4,
) -> bool:
    if len(first) != len(second):
        return False
    return all(
        left.element == right.element
        and abs(left.x - right.x) <= tolerance_angstrom
        and abs(left.y - right.y) <= tolerance_angstrom
        and abs(left.z - right.z) <= tolerance_angstrom
        for left, right in zip(first, second, strict=True)
    )


def _format_bytes(value: int) -> str:
    numeric = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if numeric < 1024.0 or unit == "GiB":
            return f"{numeric:.0f} {unit}" if unit == "B" else f"{numeric:.1f} {unit}"
        numeric /= 1024.0
    raise AssertionError("unreachable byte unit")


def _validate_selected_root(
    profile: ServerProfile,
    remote_project_path: str,
) -> None:
    if (
        not isinstance(remote_project_path, str)
        or PurePosixPath(remote_project_path).parent
        != PurePosixPath(profile.remote_project_root)
    ):
        raise ProjectOrbitalCubeError(
            "Orbital Cube loading is limited to first-level managed projects in "
            "the selected server workspace."
        )
