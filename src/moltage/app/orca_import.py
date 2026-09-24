"""Import one completed external ORCA optimization as a managed project.

The source directory is a read-only origin.  Only the four artifacts the
existing WBL pipeline consumes are copied, under the canonical managed names,
so recovery, geometry viewing and WBL all continue to read an ordinary ORCA
project without a parallel code path.  An input that reads ``*xyzfile``
coordinates is the one exception: its managed copy carries those coordinates
inline and is uploaded rather than copied.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from moltage.app.connection_service import ServerConnectionService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_submission import ORCA_OPT_INPUT, ORCA_OPT_OUTPUT
from moltage.app.orca_wbl import verify_orca_wbl_utilities
from moltage.app.project_planning import (
    ProjectPlanningError,
    create_initial_project,
    project_directory_candidates,
    validate_project_base_name,
)
from moltage.app.project_submission import allocate_remote_project_directory
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    MANAGED_METADATA_DIRECTORY,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.orca.import_evidence import (
    OrcaImportCandidate,
    OrcaImportEvidenceError,
    OrcaImportOptimizationEvidence,
    describe_missing_candidates,
    resolve_orca_xyzfile_path,
    survey_orca_import_candidates,
    validate_orca_import_optimization,
)
from moltage.orca.project_evidence import (
    OrcaImportProvenance,
    OrcaImportWavefunctionReadiness,
    OrcaOptimizationOrigin,
    OrcaOptimizationResultEvidence,
)
from moltage.orca.settings import OrcaOptimizationSettings
from moltage.remote.executor import (
    RemoteExecutorError,
    RemoteOperationStopToken,
    RemoteOperationStopped,
    RemotePathNotDirectoryError,
    RemotePathNotFoundError,
)
from moltage.remote.orca_runtime import OrcaRuntimeError, require_usable_orca_runtime
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.step_inputs import (
    StepInputTransferError,
    copy_remote_files_with_verified_digests,
    remote_file_sha256,
    upload_new_files_atomically,
)
from moltage.remote.step_inputs import discard_imported_step_inputs


ORCA_OPT_GEOMETRY = "orca_opt.xyz"
ORCA_OPT_WAVEFUNCTION = "orca_opt.gbw"
MANAGED_IMPORT_FILENAMES = (
    ORCA_OPT_INPUT,
    ORCA_OPT_OUTPUT,
    ORCA_OPT_GEOMETRY,
    ORCA_OPT_WAVEFUNCTION,
)


class OrcaImportError(RuntimeError):
    """A sanitized, user-facing external-optimization import failure."""


class OrcaImportCleanupUncertain(OrcaImportError):
    """The import failed and the claimed managed directory may still exist."""


@dataclass(frozen=True, slots=True)
class OrcaImportValidationRequest:
    profile: ServerProfile
    source_directory: str
    selected_stem: str | None = None
    supplied_password: str | None = field(default=None, repr=False, compare=False)
    stop_token: RemoteOperationStopToken | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class OrcaImportValidation:
    """User-facing validation outcome for one external ORCA directory."""

    source_directory: str
    candidate_stems: tuple[str, ...]
    selected: OrcaImportCandidate | None
    evidence: OrcaImportOptimizationEvidence | None
    source_digests: tuple[tuple[str, str], ...]
    wavefunction_readiness: OrcaImportWavefunctionReadiness
    orca_2json_path: str | None
    readiness_diagnostic: str | None
    molden_filenames: tuple[str, ...]
    blocking_reason: str | None
    selection_required: bool
    # The file an ``*xyzfile`` input reads, with the SHA256 of the exact bytes
    # that were written inline into the managed input.
    coordinate_path: str | None = None
    coordinate_sha256: str | None = None

    @property
    def importable(self) -> bool:
        return self.blocking_reason is None and self.evidence is not None

    @property
    def optimized_structure(self) -> MolecularStructure | None:
        return None if self.evidence is None else self.evidence.optimized_structure


@dataclass(frozen=True, slots=True)
class OrcaImportRequest:
    profile: ServerProfile
    validation: OrcaImportValidation
    managed_project_base_name: str
    source_molecule_name: str
    supplied_password: str | None = field(default=None, repr=False, compare=False)
    stop_token: RemoteOperationStopToken | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class OrcaImportResult:
    project: CalculationProject
    remote_project_path: str
    optimized_structure: MolecularStructure
    managed_input_hashes: tuple[tuple[str, str], ...]


class OrcaOptimizationImportService:
    """Validate one external ORCA directory, then register it read-only."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        project_id_factory: Callable[[], UUID] = uuid4,
        temporary_id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._now_factory = now_factory
        self._project_id_factory = project_id_factory
        self._temporary_id_factory = temporary_id_factory

    def validate(
        self,
        request: OrcaImportValidationRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> OrcaImportValidation:
        """Read the source directory only; never write, rename or delete."""

        if not isinstance(request, OrcaImportValidationRequest):
            raise TypeError("request must be OrcaImportValidationRequest")
        profile = _require_profile(request.profile)
        directory = _require_source_directory(request.source_directory)
        report = progress or (lambda _message: None)
        report(f"Connecting to {profile.name}...")
        stop_token = request.stop_token
        executor = self._connection_service.connect_for_remote_operation(
            profile,
            request.supplied_password,
            **({"stop_token": stop_token} if stop_token is not None else {}),
        )
        try:
            _checkpoint(stop_token)
            report("Reading the remote ORCA directory...")
            filenames = _list_regular_files(executor, directory)
            survey = survey_orca_import_candidates(filenames)
            stems = tuple(item.stem for item in survey.complete)
            if not survey.complete:
                return _blocked(
                    directory,
                    stems,
                    describe_missing_candidates(survey),
                    molden_filenames=survey.molden_filenames,
                )
            candidate = _select_candidate(survey.complete, request.selected_stem)
            if candidate is None:
                return _blocked(
                    directory,
                    stems,
                    "This directory contains several completed ORCA calculations. "
                    "Select which result to import.",
                    molden_filenames=survey.molden_filenames,
                    selection_required=True,
                )
            _checkpoint(stop_token)
            report(f"Validating ORCA result {candidate.stem}...")
            artifacts = {
                ORCA_OPT_INPUT: candidate.input_filename,
                ORCA_OPT_OUTPUT: candidate.output_filename,
                ORCA_OPT_GEOMETRY: candidate.geometry_filename,
                ORCA_OPT_WAVEFUNCTION: candidate.wavefunction_filename,
            }
            try:
                input_bytes = executor.read_bytes(
                    str(PurePosixPath(directory) / candidate.input_filename)
                )
                coordinate_path = resolve_orca_xyzfile_path(
                    candidate, input_bytes, directory
                )
                coordinate_bytes = (
                    None
                    if coordinate_path is None
                    else _read_coordinate_file(executor, candidate, coordinate_path)
                )
                evidence = validate_orca_import_optimization(
                    candidate,
                    input_bytes=input_bytes,
                    output_bytes=executor.read_bytes(
                        str(PurePosixPath(directory) / candidate.output_filename)
                    ),
                    geometry_bytes=executor.read_bytes(
                        str(PurePosixPath(directory) / candidate.geometry_filename)
                    ),
                    coordinate_bytes=coordinate_bytes,
                )
            except OrcaImportEvidenceError as error:
                return _blocked(
                    directory,
                    stems,
                    str(error),
                    selected=candidate,
                    molden_filenames=survey.molden_filenames,
                )
            except RemotePathNotFoundError as error:
                return _blocked(
                    directory,
                    stems,
                    f"A required ORCA artifact disappeared while reading it: {error}",
                    selected=candidate,
                    molden_filenames=survey.molden_filenames,
                )
            _checkpoint(stop_token)
            report("Recording source checksums...")
            digests = tuple(
                (
                    managed_name,
                    remote_file_sha256(
                        executor, str(PurePosixPath(directory) / source_name)
                    ),
                )
                for managed_name, source_name in artifacts.items()
            )
            _checkpoint(stop_token)
            report("Checking the WBL conversion utility...")
            readiness, utility_path, diagnostic = self._wavefunction_readiness(
                executor, profile
            )
            return OrcaImportValidation(
                directory,
                stems,
                candidate,
                evidence,
                digests,
                readiness,
                utility_path,
                diagnostic,
                survey.molden_filenames,
                None,
                False,
                coordinate_path=coordinate_path,
                coordinate_sha256=(
                    None
                    if coordinate_bytes is None
                    else hashlib.sha256(coordinate_bytes).hexdigest()
                ),
            )
        except (OrcaImportError, RemoteOperationStopped):
            raise
        except StepInputTransferError as error:
            raise OrcaImportError(str(error)) from None
        except RemoteExecutorError as error:
            raise OrcaImportError(str(error)) from None
        finally:
            executor.close()

    def import_optimization(
        self,
        request: OrcaImportRequest,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> OrcaImportResult:
        """Create the managed project without mutating the source directory."""

        if not isinstance(request, OrcaImportRequest):
            raise TypeError("request must be OrcaImportRequest")
        profile = _require_profile(request.profile)
        validation = request.validation
        if not isinstance(validation, OrcaImportValidation) or not validation.importable:
            raise OrcaImportError(
                "This ORCA directory has not been validated as importable"
            )
        try:
            base_name = validate_project_base_name(request.managed_project_base_name)
        except ProjectPlanningError as error:
            raise OrcaImportError(str(error)) from None
        evidence = validation.evidence
        assert evidence is not None
        directory = validation.source_directory
        expected = dict(validation.source_digests)
        sources = {
            ORCA_OPT_INPUT: evidence.candidate.input_filename,
            ORCA_OPT_OUTPUT: evidence.candidate.output_filename,
            ORCA_OPT_GEOMETRY: evidence.candidate.geometry_filename,
            ORCA_OPT_WAVEFUNCTION: evidence.candidate.wavefunction_filename,
        }
        report = progress or (lambda _message: None)
        report(f"Connecting to {profile.name}...")
        stop_token = request.stop_token
        executor = self._connection_service.connect_for_remote_operation(
            profile,
            request.supplied_password,
            **({"stop_token": stop_token} if stop_token is not None else {}),
        )
        claimed_path: str | None = None
        metadata_created = False
        try:
            _checkpoint(stop_token)
            report("Rechecking the source artifacts...")
            for managed_name, source_name in sources.items():
                current = remote_file_sha256(
                    executor, str(PurePosixPath(directory) / source_name)
                )
                if current != expected[managed_name]:
                    raise OrcaImportError(
                        f"{source_name} changed since validation; nothing was created"
                    )
            coordinate_path = validation.coordinate_path
            if coordinate_path is not None and (
                remote_file_sha256(executor, coordinate_path)
                != validation.coordinate_sha256
            ):
                raise OrcaImportError(
                    f"{coordinate_path} changed since validation; nothing was created"
                )
            _require_workspace(executor, profile.remote_project_root)
            # Last cancellation point: once the managed directory is claimed the
            # remaining copy and manifest write run to a definite outcome so no
            # half-built project is left behind.
            _checkpoint(stop_token)
            imported_at = _aware_now(self._now_factory)
            report("Creating the managed project directory...")
            directory_name, claimed_path = allocate_remote_project_directory(
                executor,
                profile.remote_project_root,
                base_name,
                imported_at.date(),
            )
            copied = {
                managed_name: str(PurePosixPath(directory) / source_name)
                for managed_name, source_name in sources.items()
                if evidence.inlined_input is None or managed_name != ORCA_OPT_INPUT
            }
            managed_hashes: tuple[tuple[str, str], ...] = ()
            if evidence.inlined_input is not None:
                report("Writing the managed ORCA input with inline coordinates...")
                managed_hashes = upload_new_files_atomically(
                    executor,
                    claimed_path,
                    {ORCA_OPT_INPUT: evidence.inlined_input},
                    temporary_id_factory=self._temporary_id_factory,
                    verify_on_server=True,
                )
            report("Copying the required ORCA artifacts...")
            managed_hashes += copy_remote_files_with_verified_digests(
                executor,
                claimed_path,
                copied,
                {name: expected[name] for name in copied},
            )
            executor.mkdir(str(PurePosixPath(claimed_path) / MANAGED_METADATA_DIRECTORY))
            metadata_created = True
            project = self._build_project(
                profile=profile,
                base_name=base_name,
                directory_name=directory_name,
                remote_path=claimed_path,
                source_molecule_name=request.source_molecule_name,
                evidence=evidence,
                validation=validation,
                managed_hashes=managed_hashes,
                imported_at=imported_at,
            )
            report("Registering the managed project...")
            RemoteProjectRepository(
                executor, temporary_id_factory=self._temporary_id_factory
            ).write_initial(project)
            self._local_index_repository.mark_seen(
                project, bound_server_profile_id=profile.profile_id
            )
            claimed_path = None
            return OrcaImportResult(
                project,
                project.remote_project_path,
                evidence.optimized_structure,
                managed_hashes,
            )
        except RemoteOperationStopped:
            raise
        except Exception as error:
            message = str(error)
            if claimed_path is not None:
                removed = discard_imported_step_inputs(
                    executor,
                    claimed_path,
                    MANAGED_IMPORT_FILENAMES,
                    metadata_directory=(
                        MANAGED_METADATA_DIRECTORY if metadata_created else None
                    ),
                )
                if not removed:
                    raise OrcaImportCleanupUncertain(
                        f"{message} The partially prepared directory "
                        f"{claimed_path} could not be proven removed; inspect it "
                        "on the server before retrying."
                    ) from None
            if isinstance(error, OrcaImportError):
                raise
            raise OrcaImportError(message) from None
        finally:
            executor.close()

    def _wavefunction_readiness(self, executor, profile):
        runtime = profile.orca_runtime
        if runtime is None:
            return (
                OrcaImportWavefunctionReadiness.CONFIGURATION_REQUIRED,
                None,
                "No ORCA installation is configured for this server. Configure and "
                "validate ORCA in this server's ORCA settings before running WBL.",
            )
        try:
            require_usable_orca_runtime(executor, runtime)
            utilities = verify_orca_wbl_utilities(executor, runtime)
        except (OrcaRuntimeError, RemoteExecutorError, RuntimeError) as error:
            return (
                OrcaImportWavefunctionReadiness.CONFIGURATION_REQUIRED,
                None,
                "The ORCA conversion utility could not be verified for this "
                f"server: {error} Configure and validate it in this server's ORCA "
                "settings before running WBL.",
            )
        return (
            OrcaImportWavefunctionReadiness.READY,
            utilities.orca_2json_path,
            None,
        )

    def _build_project(
        self,
        *,
        profile,
        base_name,
        directory_name,
        remote_path,
        source_molecule_name,
        evidence,
        validation,
        managed_hashes,
        imported_at,
    ) -> CalculationProject:
        candidate = create_initial_project(
            base_name=base_name,
            remote_directory_name=next(
                iter(project_directory_candidates(base_name, imported_at.date()))
            ),
            source_molecule_name=source_molecule_name,
            server_profile_id=profile.profile_id,
            remote_project_root=profile.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=imported_at,
            project_id=self._project_id_factory(),
        )
        candidate = replace(
            candidate,
            remote_directory_name=directory_name,
            remote_project_path=remote_path,
        )
        digests = dict(validation.source_digests)
        identity = evidence.identity
        settings = OrcaOptimizationSettings(
            method=identity.method,
            basis=identity.basis,
            dispersion=identity.dispersion,
            charge=identity.charge,
            multiplicity=identity.multiplicity,
        )
        result = OrcaOptimizationResultEvidence(
            scheduler_succeeded=False,
            normal_termination=True,
            optimization_converged=True,
            final_xyz_valid=True,
            wbl_input_ready=True,
            output_sha256=digests[ORCA_OPT_OUTPUT],
            xyz_sha256=digests[ORCA_OPT_GEOMETRY],
            gbw_sha256=digests[ORCA_OPT_WAVEFUNCTION],
            diagnostic=validation.readiness_diagnostic,
            origin=OrcaOptimizationOrigin.IMPORTED_EXTERNAL,
        )
        provenance = OrcaImportProvenance(
            source_directory=validation.source_directory,
            source_stem=evidence.candidate.stem,
            source_input_filename=evidence.candidate.input_filename,
            source_output_filename=evidence.candidate.output_filename,
            source_geometry_filename=evidence.candidate.geometry_filename,
            source_wavefunction_filename=evidence.candidate.wavefunction_filename,
            source_input_sha256=digests[ORCA_OPT_INPUT],
            source_output_sha256=digests[ORCA_OPT_OUTPUT],
            source_geometry_sha256=digests[ORCA_OPT_GEOMETRY],
            source_wavefunction_sha256=digests[ORCA_OPT_WAVEFUNCTION],
            imported_at=imported_at,
            wavefunction_readiness=validation.wavefunction_readiness,
            orca_2json_path=validation.orca_2json_path,
            readiness_diagnostic=validation.readiness_diagnostic,
            source_coordinate_path=validation.coordinate_path,
            source_coordinate_sha256=validation.coordinate_sha256,
        )
        optimization = replace(
            candidate.steps[0],
            state=ProjectStepState.SUCCEEDED,
            finished_at=imported_at,
            input_hashes=managed_hashes,
            orca_optimization_settings=settings,
            orca_runtime=profile.orca_runtime,
            orca_optimization_result=result,
            orca_submitted_elements=evidence.elements,
            orca_import_provenance=provenance,
        )
        return replace(candidate, steps=(optimization, *candidate.steps[1:]))


def _checkpoint(stop_token) -> None:
    if stop_token is not None:
        stop_token.checkpoint()


def default_managed_base_name(source_directory: str) -> str:
    """Derive one conservative editable project name from a source basename."""

    basename = PurePosixPath(source_directory).name if source_directory else ""
    converted = "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in basename
    ).strip("_")
    return converted or "imported_orca"


def _select_candidate(candidates, selected_stem):
    if selected_stem is not None:
        return next(
            (item for item in candidates if item.stem == selected_stem),
            None,
        )
    return candidates[0] if len(candidates) == 1 else None


def _blocked(
    directory,
    stems,
    reason,
    *,
    selected=None,
    molden_filenames=(),
    selection_required=False,
):
    return OrcaImportValidation(
        directory,
        stems,
        selected,
        None,
        (),
        OrcaImportWavefunctionReadiness.CONFIGURATION_REQUIRED,
        None,
        None,
        tuple(molden_filenames),
        reason,
        selection_required,
    )


def _read_coordinate_file(executor, candidate, path) -> bytes:
    try:
        return executor.read_bytes(path)
    except RemotePathNotFoundError:
        raise OrcaImportEvidenceError(
            f"{candidate.input_filename} reads its starting coordinates from "
            f"{path}, which does not exist."
        ) from None


def _list_regular_files(executor, directory) -> tuple[str, ...]:
    try:
        entries = executor.list_directory(directory)
    except RemotePathNotFoundError:
        raise OrcaImportError(
            f"The remote directory does not exist: {directory}"
        ) from None
    except RemotePathNotDirectoryError:
        raise OrcaImportError(
            f"The remote path is not a directory: {directory}"
        ) from None
    return tuple(entry.name for entry in entries if not entry.is_directory)


def _require_profile(profile) -> ServerProfile:
    if not isinstance(profile, ServerProfile):
        raise TypeError("ORCA import requires a ServerProfile")
    return profile


def _require_source_directory(value) -> str:
    if not isinstance(value, str):
        raise OrcaImportError("Enter the remote ORCA optimization directory")
    directory = value.strip()
    if not directory:
        raise OrcaImportError("Enter the remote ORCA optimization directory")
    if not directory.startswith("/"):
        raise OrcaImportError(
            "Enter an absolute remote directory such as "
            "/remote/path/to/completed/orca_calculation"
        )
    normalized = directory.rstrip("/") or "/"
    path = PurePosixPath(normalized)
    if (
        str(path) != normalized
        or ".." in path.parts
        or any(ord(character) < 32 for character in normalized)
    ):
        raise OrcaImportError("The remote directory path is not a valid POSIX path")
    return normalized


def _require_workspace(executor, path) -> None:
    try:
        item = executor.stat(path)
    except RemotePathNotFoundError:
        raise OrcaImportError(
            f"Remote project workspace does not exist: {path}"
        ) from None
    if not item.is_directory:
        raise OrcaImportError(f"Remote project workspace is not a directory: {path}")


def _aware_now(factory) -> datetime:
    value = factory()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise OrcaImportError("ORCA import timestamps must be timezone-aware")
    return value
