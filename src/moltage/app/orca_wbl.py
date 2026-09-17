"""Explicit post-optimization ORCA WBL evidence extraction and persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
import shlex
from uuid import uuid4

from moltage.app.connection_service import ServerConnectionService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
    begin_orca_wbl_step,
    complete_orca_wbl_step,
    fail_orca_wbl_step,
)
from moltage.domain.server_profile import ServerProfile
from moltage.orca.catalog import OrcaBasis
from moltage.orca.evidence import parse_orca_final_xyz
from moltage.orca.input_writer import parse_rendered_orca_structure
from moltage.orca.wbl import (
    WBL_MODEL_CLASSIFICATION,
    WBL_MODEL_ID,
    OrcaWblResult,
    OrcaWblResultEvidence,
    OrcaWblSettings,
    calculate_orca_wbl,
)
from moltage.orca.wbl_artifacts import render_wbl_artifacts
from moltage.orca.wavefunction import (
    parse_orca_wavefunction_json,
    render_orca_2json_configuration,
)
from moltage.remote.orca_runtime import require_usable_orca_runtime
from moltage.remote.project_repository import RemoteProjectRepository
from moltage.remote.runtime_environment import runtime_environment_commands
from moltage.remote.step_inputs import upload_new_files_atomically
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii


ORCA_WBL_WAVEFUNCTION_JSON = "orca_wavefunction.json"
ORCA_WBL_MOLDEN = "orca_wavefunction.molden.input"
ORCA_WBL_CONFIG = "orca_2json.conf"


class OrcaWblServiceError(RuntimeError):
    """Raised when WBL evidence extraction or persistence cannot be verified."""


@dataclass(frozen=True, slots=True)
class OrcaWblRequest:
    profile: ServerProfile
    remote_project_path: str
    settings: OrcaWblSettings
    supplied_password: str | None = None


@dataclass(frozen=True, slots=True)
class OrcaWblServiceResult:
    project: CalculationProject
    analysis: OrcaWblResult
    remote_wbl_directory: str
    artifact_hashes: tuple[tuple[str, str], ...]
    molden_generated: bool


class OrcaWblService:
    """Run only ORCA conversion utilities, then calculate WBL locally."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        local_index_repository: LocalProjectIndexRepository,
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        temporary_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._connection_service = connection_service
        self._local_index_repository = local_index_repository
        self._now_factory = now_factory
        self._temporary_id_factory = temporary_id_factory

    def calculate(
        self,
        request: OrcaWblRequest,
        *,
        progress: Callable[[str], None] | None = None,
        project_updated: Callable[[CalculationProject], None] | None = None,
    ) -> OrcaWblServiceResult:
        if not isinstance(request, OrcaWblRequest):
            raise TypeError("ORCA WBL calculation requires an OrcaWblRequest")
        report = progress or (lambda _message: None)
        profile = request.profile
        if not isinstance(profile, ServerProfile):
            raise OrcaWblServiceError("ORCA WBL calculation requires a server profile")
        if profile.orca_runtime is None:
            raise OrcaWblServiceError("Configure and validate ORCA for this server profile")
        executor = self._connection_service.connect_for_remote_operation(
            profile, request.supplied_password
        )
        temporary_directory = None
        repository = None
        project = None
        wbl_started = False
        try:
            repository = RemoteProjectRepository(executor)
            project = repository.load(
                request.remote_project_path, preserve_remote_errors=True
            )
            self._validate_project(project, profile, request.settings)
            project = repository.persist_update(
                begin_orca_wbl_step(
                    project,
                    settings=request.settings,
                    started_at=_aware_now(self._now_factory),
                ),
                updated_at=_aware_now(self._now_factory),
                preserve_remote_errors=True,
            )
            wbl_started = True
            if project_updated is not None:
                project_updated(project)
            report("ORCA WBL transmission is running...")
            self._local_index_repository.mark_seen(
                project, bound_server_profile_id=profile.profile_id
            )
            optimization = project.steps[0]
            runtime = optimization.orca_runtime or profile.orca_runtime
            report("Validating the recorded ORCA runtime and WBL utility...")
            require_usable_orca_runtime(executor, runtime)
            orca_2json, orca_2mkl = _utility_paths(runtime.executable_path)
            molden_available = _verify_utilities(
                executor,
                runtime.environment,
                orca_2json,
                orca_2mkl,
            )
            project_root = project.remote_project_path
            gbw_path = str(PurePosixPath(project_root) / "orca_opt.gbw")
            expected_gbw = optimization.orca_optimization_result.gbw_sha256
            actual_gbw = _remote_sha256(executor, gbw_path)
            if actual_gbw != expected_gbw:
                raise OrcaWblServiceError(
                    "orca_opt.gbw no longer matches the verified optimization evidence"
                )
            submitted = parse_rendered_orca_structure(
                executor.read_bytes(str(PurePosixPath(project_root) / "orca_opt.inp"))
            )
            optimized = parse_orca_final_xyz(
                executor.read_bytes(str(PurePosixPath(project_root) / "orca_opt.xyz")),
                submitted,
            )
            token = _safe_temporary_id(self._temporary_id_factory())
            temporary_directory = f"/tmp/moltage-orca-wbl-{token}"
            executor.mkdir(temporary_directory)
            config = render_orca_2json_configuration()
            executor.write_bytes(
                str(PurePosixPath(temporary_directory) / "orca_wbl.json.conf"),
                config,
            )
            report("Extracting MO, basis, and overlap evidence from the existing GBW...")
            _run_conversion(
                executor,
                runtime.environment,
                orca_2json,
                orca_2mkl if molden_available else None,
                gbw_path,
                temporary_directory,
            )
            wavefunction_json = executor.read_bytes(
                str(PurePosixPath(temporary_directory) / "orca_wbl.json")
            )
            if not wavefunction_json:
                raise OrcaWblServiceError("orca_2json produced an empty JSON artifact")
            molden = None
            if molden_available:
                molden = executor.read_bytes(
                    str(PurePosixPath(temporary_directory) / "orca_wbl.molden.input")
                )
                if not molden:
                    raise OrcaWblServiceError("orca_2mkl produced an empty Molden artifact")
            wavefunction = parse_orca_wavefunction_json(wavefunction_json)
            connectivity = infer_connectivity(optimized, load_default_covalent_radii())
            optimization_settings = optimization.orca_optimization_settings
            if optimization_settings is None:
                raise OrcaWblServiceError(
                    "verified ORCA optimization settings are unavailable"
                )
            if (
                wavefunction.charge != optimization_settings.charge
                or wavefunction.multiplicity != optimization_settings.multiplicity
            ):
                raise OrcaWblServiceError(
                    "ORCA wavefunction charge/multiplicity differs from the "
                    "submitted optimization settings"
                )
            basis = optimization_settings.basis
            report(
                "Calculating closed-shell or spin-resolved all-MO WBL "
                "transmission from verified ORCA evidence..."
            )
            analysis = calculate_orca_wbl(
                wavefunction,
                optimized,
                connectivity,
                request.settings,
                basis=basis,
            )
            source_hashes = {
                "orca_opt.gbw": actual_gbw,
                ORCA_WBL_WAVEFUNCTION_JSON: sha256(wavefunction_json).hexdigest(),
            }
            tool_evidence = {
                "orca_executable": runtime.executable_path,
                "orca_version": runtime.version_evidence.version or "UNVERIFIED",
                "orca_2json_path": orca_2json,
                "orca_2mkl_path": orca_2mkl if molden_available else "UNAVAILABLE",
            }
            rendered, _ = render_wbl_artifacts(
                analysis,
                source_hashes=source_hashes,
                tool_evidence=tool_evidence,
            )
            files = {
                ORCA_WBL_CONFIG: config,
                ORCA_WBL_WAVEFUNCTION_JSON: wavefunction_json,
                **({ORCA_WBL_MOLDEN: molden} if molden is not None else {}),
                **rendered,
            }
            final_directory = str(PurePosixPath(project_root) / "wbl")
            staging_directory = str(
                PurePosixPath(project_root) / f".wbl.tmp-{token}"
            )
            _require_absent(executor, final_directory)
            executor.mkdir(staging_directory)
            report("Uploading and verifying WBL provenance artifacts...")
            artifact_hashes = upload_new_files_atomically(
                executor,
                staging_directory,
                files,
                temporary_id_factory=self._temporary_id_factory,
            )
            executor.rename(staging_directory, final_directory)
            evidence = OrcaWblResultEvidence(
                WBL_MODEL_ID,
                WBL_MODEL_CLASSIFICATION,
                actual_gbw,
                source_hashes[ORCA_WBL_WAVEFUNCTION_JSON],
                artifact_hashes,
                orca_2json,
                analysis.t_alpha_at_fermi,
                analysis.t_beta_at_fermi,
                analysis.t_total_at_fermi,
                tuple(
                    (item.mo_number, item.transmission_at_fermi)
                    for item in analysis.top_alpha
                ),
                tuple(
                    (item.mo_number, item.transmission_at_fermi)
                    for item in analysis.top_beta
                ),
                analysis.spin_treatment,
                tuple(
                    (item.mo_number, item.transmission_at_fermi)
                    for item in analysis.top_total
                ),
            )
            updated_candidate = complete_orca_wbl_step(
                project,
                result=evidence,
                input_hashes=(
                    ("orca_opt.gbw", actual_gbw),
                    (
                        ORCA_WBL_WAVEFUNCTION_JSON,
                        source_hashes[ORCA_WBL_WAVEFUNCTION_JSON],
                    ),
                ),
                finished_at=_aware_now(self._now_factory),
            )
            updated = repository.persist_update(
                updated_candidate,
                updated_at=_aware_now(self._now_factory),
                preserve_remote_errors=True,
            )
            project = updated
            if project_updated is not None:
                project_updated(updated)
            self._local_index_repository.mark_seen(
                updated, bound_server_profile_id=profile.profile_id
            )
            return OrcaWblServiceResult(
                updated,
                analysis,
                final_directory,
                artifact_hashes,
                molden is not None,
            )
        except Exception as error:
            diagnostic = str(error)
            if wbl_started and repository is not None and project is not None and any(
                step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
                and step.state is ProjectStepState.RUNNING
                for step in project.steps
            ):
                try:
                    project = repository.persist_update(
                        fail_orca_wbl_step(
                            project,
                            diagnostic=diagnostic,
                            finished_at=_aware_now(self._now_factory),
                        ),
                        updated_at=_aware_now(self._now_factory),
                        preserve_remote_errors=True,
                    )
                    self._local_index_repository.mark_seen(
                        project, bound_server_profile_id=profile.profile_id
                    )
                    if project_updated is not None:
                        project_updated(project)
                except Exception as persistence_error:
                    diagnostic += (
                        "; the WBL failure state could not be persisted: "
                        f"{persistence_error}"
                    )
            raise OrcaWblServiceError(diagnostic) from None
        finally:
            if temporary_directory is not None:
                try:
                    executor.execute(
                        "rm -rf -- " + shlex.quote(temporary_directory)
                    )
                except Exception:
                    pass
            executor.close()

    def _validate_project(self, project, profile, settings):
        if project.workflow_kind is not CalculationWorkflowKind.ORCA:
            raise OrcaWblServiceError("Selected project is not an ORCA workflow")
        if (
            project.server_profile_id != profile.profile_id
            and not self._local_index_repository.is_bound_to_profile(
                project,
                bound_server_profile_id=profile.profile_id,
            )
        ):
            raise OrcaWblServiceError(
                "Confirm the server profile associated with this ORCA project"
            )
        if any(
            step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
            for step in project.steps
        ):
            raise OrcaWblServiceError("This project already contains an ORCA WBL stage")
        optimization = project.steps[0]
        evidence = optimization.orca_optimization_result
        if evidence is None or not evidence.succeeded or not evidence.wbl_input_ready:
            raise OrcaWblServiceError(
                "Verified optimization and GBW evidence are required before WBL"
            )
        settings.require_runnable(len(optimization.orca_submitted_elements))


def _utility_paths(orca_path):
    parent = PurePosixPath(orca_path).parent
    return str(parent / "orca_2json"), str(parent / "orca_2mkl")


def _verify_utilities(executor, environment, orca_2json, orca_2mkl):
    setup = runtime_environment_commands(environment)
    json_q = shlex.quote(orca_2json)
    mkl_q = shlex.quote(orca_2mkl)
    result = executor.execute(
        "bash -lc "
        + shlex.quote(
            "; ".join(
                (
                    *setup,
                    f"test -f {json_q} && test -r {json_q} && test -x {json_q} || exit 61",
                    f"test \"$(readlink -f -- {json_q})\" = {json_q} || exit 62",
                    f"if test -f {mkl_q} && test -r {mkl_q} && test -x {mkl_q}; then printf '__MOLTAGE_ORCA_2MKL__=AVAILABLE\\n'; else printf '__MOLTAGE_ORCA_2MKL__=UNAVAILABLE\\n'; fi",
                )
            )
        )
    )
    if result.exit_status == 61:
        raise OrcaWblServiceError(
            "The configured ORCA installation does not provide executable orca_2json"
        )
    if result.exit_status != 0:
        raise OrcaWblServiceError("ORCA WBL utility identity could not be verified")
    return b"__MOLTAGE_ORCA_2MKL__=AVAILABLE" in result.stdout.splitlines()


def _run_conversion(executor, environment, orca_2json, orca_2mkl, gbw_path, temp):
    setup = runtime_environment_commands(environment)
    temp_q = shlex.quote(temp)
    gbw_q = shlex.quote(gbw_path)
    json_q = shlex.quote(orca_2json)
    commands = [
        *setup,
        f"cd {temp_q} || exit 71",
        f"ln -s -- {gbw_q} orca_wbl.gbw || exit 72",
        f"{json_q} orca_wbl.gbw -json || exit 73",
        "test -s orca_wbl.json || exit 74",
    ]
    if orca_2mkl is not None:
        commands.extend(
            (
                f"{shlex.quote(orca_2mkl)} orca_wbl -molden || exit 75",
                "test -s orca_wbl.molden.input || exit 76",
            )
        )
    result = executor.execute("bash -lc " + shlex.quote("; ".join(commands)))
    if result.exit_status != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise OrcaWblServiceError(
            "ORCA wavefunction evidence conversion failed"
            + (f": {detail.splitlines()[0][:300]}" if detail else "")
        )


def _remote_sha256(executor, path):
    result = executor.execute("sha256sum -- " + shlex.quote(path))
    if result.exit_status != 0:
        raise OrcaWblServiceError("Unable to verify the remote GBW SHA256")
    try:
        digest = result.stdout.decode("ascii").split()[0]
    except (UnicodeDecodeError, IndexError):
        raise OrcaWblServiceError("Remote GBW SHA256 evidence is malformed") from None
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise OrcaWblServiceError("Remote GBW SHA256 evidence is malformed")
    return digest


def _require_absent(executor, path):
    from moltage.remote.executor import RemotePathNotFoundError

    try:
        executor.stat(path)
    except RemotePathNotFoundError:
        return
    raise OrcaWblServiceError(
        "Remote WBL directory already exists without matching manifest evidence; "
        "no files were overwritten"
    )


def _safe_temporary_id(value):
    if (
        not isinstance(value, str)
        or not value
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value)
    ):
        raise OrcaWblServiceError("WBL temporary identity is unsafe")
    return value


def _aware_now(factory):
    value = factory()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OrcaWblServiceError("ORCA WBL timestamps must be timezone-aware")
    return value
