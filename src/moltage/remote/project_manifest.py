"""Deterministic project JSON with explicit legacy-schema migration."""

from datetime import datetime
import json
from uuid import UUID

from moltage.domain.calculation_project import (
    PROJECT_SCHEMA_VERSION,
    LEGACY_PROJECT_SCHEMA_VERSIONS,
    CalculationProject,
    CalculationProjectValidationError,
    CalculationWorkflowKind,
    ProjectElectrodeAtomIdentity,
    ProjectElectrodeClusterProvenance,
    ProjectElectrodeLatticeExtension,
    ProjectRestartProvenance,
    ProjectStepKind,
    ProjectStepAttempt,
    ProjectStepRecord,
    ProjectStepState,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
)
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaOptimizationConvergence,
    OrcaScfConvergence,
    OrcaVersionEvidence,
    OrcaVersionFamily,
)
from moltage.orca.evidence import (
    OrcaFrequencyCompletion,
    OrcaFrequencyEvidence,
    OrcaFrequencyModeEvidence,
    OrcaImaginaryModeClassification,
)
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings
from moltage.orca.wbl import (
    WblContactSubspaceMode,
    WblLinkerKind,
    WblParameterStatus,
    WblSpinTreatment,
    OrcaWblContactSettings,
    OrcaWblResultEvidence,
    OrcaWblSettings,
)


class ManagedProjectManifestError(ValueError):
    """Raised when managed remote project metadata is unsupported or malformed."""


def serialize_project_manifest(project: CalculationProject) -> str:
    """Return deterministic UTF-8-ready JSON with explicit null optional fields."""

    if not isinstance(project, CalculationProject):
        raise TypeError("project must be a CalculationProject")
    document = {
        "schema_version": project.schema_version,
        "project_id": str(project.project_id),
        "display_name": project.display_name,
        "remote_directory_name": project.remote_directory_name,
        "source_molecule_name": project.source_molecule_name,
        "server_profile_id": str(project.server_profile_id),
        "remote_project_path": project.remote_project_path,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "revision": project.revision,
        "workflow_kind": project.workflow_kind.value,
        "starting_step": project.starting_step.value,
        "electrode_provenance": [
            {
                "side": cluster.side,
                "geometry_model": cluster.geometry_model,
                "pyramid_layers": cluster.pyramid_layers,
                "nearest_neighbor_spacing_angstrom": (
                    cluster.nearest_neighbor_spacing_angstrom
                ),
                "roll_degrees": cluster.roll_degrees,
                "atom_identities": [
                    {
                        "local_index": identity.local_index,
                        "layer_index": identity.layer_index,
                        "lattice_key": (
                            list(identity.lattice_key)
                            if identity.lattice_key is not None
                            else None
                        ),
                        "standard_pyramid_member": (
                            identity.standard_pyramid_member
                        ),
                    }
                    for identity in cluster.atom_identities
                ],
                "local_to_global_atom_indices": list(
                    cluster.local_to_global_indices
                ),
                "apex_lattice_key": list(cluster.apex_lattice_key),
                "reference_corner_lattice_keys": [
                    list(key) for key in cluster.reference_corner_lattice_keys
                ],
                "lattice_extensions": [
                    {
                        "origin": extension.origin,
                        "layer_index": extension.layer_index,
                        "lattice_key": list(extension.lattice_key),
                        "global_atom_index": extension.global_atom_index,
                    }
                    for extension in cluster.lattice_extensions
                ],
            }
            for cluster in project.electrode_provenance
        ],
        "legacy_electrode_recovery_allowed": (
            project.legacy_electrode_recovery_allowed
        ),
        "restart_provenance": (
            {
                "source_project_id": str(project.restart_provenance.source_project_id),
                "source_step": project.restart_provenance.source_step.value,
                "source_job_id": project.restart_provenance.source_job_id,
                "source_geometry_sha256": (
                    project.restart_provenance.source_geometry_sha256
                ),
            }
            if project.restart_provenance is not None
            else None
        ),
        "steps": [
            {
                "kind": step.kind.value,
                "state": step.state.value,
                "relative_folder": step.relative_folder,
                "job_id": step.job_id,
                "cluster_name": step.cluster_name,
                "submitted_at": _format_optional_timestamp(step.submitted_at),
                "started_at": _format_optional_timestamp(step.started_at),
                "finished_at": _format_optional_timestamp(step.finished_at),
                "input_hashes": dict(step.input_hashes),
                "last_error": step.last_error,
                "scheduler_state": step.scheduler_state,
                "scheduler_kind": (
                    step.scheduler_kind.value
                    if step.scheduler_kind is not None
                    else None
                ),
                "submit_script_filename": step.submit_script_filename,
                "slurm_output_filename": step.slurm_output_filename,
                "attempts": [
                    {
                        "job_id": attempt.job_id,
                        "submitted_at": _format_optional_timestamp(
                            attempt.submitted_at
                        ),
                        "finished_at": _format_optional_timestamp(
                            attempt.finished_at
                        ),
                        "terminal_scheduler_state": (
                            attempt.terminal_scheduler_state
                        ),
                        "failure_reason": attempt.failure_reason,
                        "submit_script_filename": (
                            attempt.submit_script_filename
                        ),
                        "slurm_output_filename": (
                            attempt.slurm_output_filename
                        ),
                        "input_hashes": dict(attempt.input_hashes),
                        "scheduler_kind": attempt.scheduler_kind.value,
                    }
                    for attempt in step.attempts
                ],
                "orca_optimization_settings": _optimization_settings_to_dict(
                    step.orca_optimization_settings
                ),
                "orca_frequency_settings": _frequency_settings_to_dict(
                    step.orca_frequency_settings
                ),
                "orca_runtime": _orca_runtime_to_dict(step.orca_runtime),
                "orca_optimization_result": _optimization_result_to_dict(
                    step.orca_optimization_result
                ),
                "orca_frequency_result": _frequency_result_to_dict(
                    step.orca_frequency_result
                ),
                "orca_wbl_settings": _wbl_settings_to_dict(step.orca_wbl_settings),
                "orca_wbl_result": _wbl_result_to_dict(step.orca_wbl_result),
                "orca_submitted_elements": list(step.orca_submitted_elements),
            }
            for step in project.steps
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def parse_project_manifest(text: str | bytes) -> CalculationProject:
    """Parse the current schema, migrating supported legacy manifests in memory."""

    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        if not isinstance(text, str):
            raise TypeError("manifest input must be text or bytes")
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise TypeError("manifest root must be an object")
        schema_version = raw["schema_version"]
        if schema_version not in (
            PROJECT_SCHEMA_VERSION,
            *LEGACY_PROJECT_SCHEMA_VERSIONS,
        ):
            raise ManagedProjectManifestError(
                f"unsupported project manifest schema version: {schema_version!r}"
            )
        raw_steps = raw["steps"]
        if not isinstance(raw_steps, list):
            raise TypeError("steps must be an array")
        steps = tuple(
            _parse_step(item, schema_version=schema_version)
            for item in raw_steps
        )
        workflow_kind = (
            CalculationWorkflowKind(raw["workflow_kind"])
            if schema_version >= 9
            else CalculationWorkflowKind.FHI_AIMS_AITRANSS
        )
        if schema_version >= 4:
            starting_step = ProjectStepKind(raw["starting_step"])
            raw_electrodes = raw["electrode_provenance"]
            if not isinstance(raw_electrodes, list):
                raise TypeError("electrode_provenance must be an array")
            electrode_provenance = tuple(
                (
                    _parse_electrode_provenance(
                        item,
                        include_extensions=schema_version >= 8,
                    )
                    if schema_version >= 7
                    else _parse_legacy_electrode_provenance(item)
                )
                for item in raw_electrodes
            )
        else:
            starting_step = None
            electrode_provenance = ()
        legacy_electrode_recovery_allowed = (
            raw["legacy_electrode_recovery_allowed"]
            if schema_version >= 7
            else not electrode_provenance
            and any(
                step.kind
                in {
                    ProjectStepKind.TRANSPORT_CONVERGENCE,
                    ProjectStepKind.TRANSMISSION,
                }
                and step.state
                not in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}
                for step in steps
            )
        )
        restart_provenance = (
            _parse_restart_provenance(raw["restart_provenance"])
            if schema_version >= 5 and raw["restart_provenance"] is not None
            else None
        )
        return CalculationProject(
            schema_version=PROJECT_SCHEMA_VERSION,
            project_id=UUID(raw["project_id"]),
            display_name=raw["display_name"],
            remote_directory_name=raw["remote_directory_name"],
            source_molecule_name=raw["source_molecule_name"],
            server_profile_id=UUID(raw["server_profile_id"]),
            remote_project_path=raw["remote_project_path"],
            created_at=_parse_timestamp(raw["created_at"], "created_at"),
            updated_at=_parse_timestamp(raw["updated_at"], "updated_at"),
            revision=raw["revision"],
            workflow_kind=workflow_kind,
            steps=steps,
            starting_step=starting_step,
            electrode_provenance=electrode_provenance,
            legacy_electrode_recovery_allowed=legacy_electrode_recovery_allowed,
            restart_provenance=restart_provenance,
        )
    except ManagedProjectManifestError:
        raise
    except (
        CalculationProjectValidationError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as error:
        raise ManagedProjectManifestError(
            f"managed project manifest is malformed: {error}"
        ) from None


def _parse_step(raw: object, *, schema_version: int) -> ProjectStepRecord:
    if not isinstance(raw, dict):
        raise TypeError("step record must be an object")
    input_hashes = raw["input_hashes"]
    if not isinstance(input_hashes, dict):
        raise TypeError("input_hashes must be an object")
    attempts = ()
    if schema_version >= 2:
        raw_attempts = raw["attempts"]
        if not isinstance(raw_attempts, list):
            raise TypeError("attempts must be an array")
        attempts = tuple(
            _parse_attempt(item, schema_version=schema_version)
            for item in raw_attempts
        )
    return ProjectStepRecord(
        kind=ProjectStepKind(raw["kind"]),
        state=ProjectStepState(raw["state"]),
        relative_folder=raw["relative_folder"],
        job_id=raw["job_id"],
        cluster_name=raw["cluster_name"],
        submitted_at=_parse_optional_timestamp(raw["submitted_at"], "submitted_at"),
        started_at=_parse_optional_timestamp(raw["started_at"], "started_at"),
        finished_at=_parse_optional_timestamp(raw["finished_at"], "finished_at"),
        input_hashes=tuple(input_hashes.items()),
        last_error=raw["last_error"],
        scheduler_state=(raw["scheduler_state"] if schema_version >= 2 else None),
        scheduler_kind=(
            SchedulerKind(raw["scheduler_kind"])
            if schema_version >= 6 and raw["scheduler_kind"] is not None
            else SchedulerKind.SLURM
            if raw["job_id"] is not None
            else None
        ),
        submit_script_filename=(
            raw["submit_script_filename"] if schema_version >= 2 else None
        ),
        slurm_output_filename=(
            raw["slurm_output_filename"] if schema_version >= 2 else None
        ),
        attempts=attempts,
        orca_optimization_settings=(
            _optimization_settings_from_dict(raw.get("orca_optimization_settings"))
            if schema_version >= 9
            else None
        ),
        orca_frequency_settings=(
            _frequency_settings_from_dict(raw.get("orca_frequency_settings"))
            if schema_version >= 9
            else None
        ),
        orca_runtime=(
            _orca_runtime_from_dict(raw.get("orca_runtime"))
            if schema_version >= 9
            else None
        ),
        orca_optimization_result=(
            _optimization_result_from_dict(raw.get("orca_optimization_result"))
            if schema_version >= 9
            else None
        ),
        orca_frequency_result=(
            _frequency_result_from_dict(raw.get("orca_frequency_result"))
            if schema_version >= 9
            else None
        ),
        orca_wbl_settings=(
            _wbl_settings_from_dict(raw.get("orca_wbl_settings"))
            if schema_version >= 10
            else None
        ),
        orca_wbl_result=(
            _wbl_result_from_dict(raw.get("orca_wbl_result"))
            if schema_version >= 10
            else None
        ),
        orca_submitted_elements=(
            tuple(raw.get("orca_submitted_elements", ()))
            if schema_version >= 9
            else ()
        ),
    )


def _optimization_settings_to_dict(settings):
    if settings is None:
        return None
    return {
        "method": settings.method.value if settings.method is not None else None,
        "basis": settings.basis.value if settings.basis is not None else None,
        "dispersion": settings.dispersion.value,
        "charge": settings.charge,
        "multiplicity": settings.multiplicity,
        "optimization_convergence": settings.optimization_convergence.value,
        "coordinate_system": settings.coordinate_system.value,
        "scf_convergence": settings.scf_convergence.value,
        "process_count": settings.process_count,
        "max_core_mb": settings.max_core_mb,
        "scheduler_nodes": settings.scheduler_nodes,
        "runtime_minutes": settings.runtime_minutes,
        "scheduler_memory_gb": settings.scheduler_memory_gb,
        "version_family": (
            settings.version_family.value if settings.version_family is not None else None
        ),
    }


def _optimization_settings_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("ORCA optimization settings must be an object")
    family = raw["version_family"]
    return OrcaOptimizationSettings(
        method=OrcaMethod(raw["method"]) if raw["method"] is not None else None,
        basis=OrcaBasis(raw["basis"]) if raw["basis"] is not None else None,
        dispersion=OrcaDispersion(raw["dispersion"]),
        charge=raw["charge"],
        multiplicity=raw["multiplicity"],
        optimization_convergence=OrcaOptimizationConvergence(raw["optimization_convergence"]),
        coordinate_system=OrcaCoordinateSystem(raw["coordinate_system"]),
        scf_convergence=OrcaScfConvergence(raw["scf_convergence"]),
        process_count=raw["process_count"],
        max_core_mb=raw["max_core_mb"],
        scheduler_nodes=raw["scheduler_nodes"],
        runtime_minutes=raw["runtime_minutes"],
        scheduler_memory_gb=raw["scheduler_memory_gb"],
        version_family=OrcaVersionFamily(family) if family is not None else None,
    )


def _frequency_settings_to_dict(settings):
    if settings is None:
        return None
    return {
        "source_optimization": _optimization_settings_to_dict(settings.source_optimization),
        "source_optimization_sha256": settings.source_optimization_sha256,
        "mode": settings.mode.value,
        "process_count": settings.process_count,
        "max_core_mb": settings.max_core_mb,
        "scheduler_nodes": settings.scheduler_nodes,
        "runtime_minutes": settings.runtime_minutes,
        "scheduler_memory_gb": settings.scheduler_memory_gb,
    }


def _frequency_settings_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("ORCA frequency settings must be an object")
    source = _optimization_settings_from_dict(raw["source_optimization"])
    if source is None:
        raise TypeError("ORCA frequency source settings are missing")
    return OrcaFrequencySettings(
        source,
        raw["source_optimization_sha256"],
        OrcaFrequencyMode(raw["mode"]),
        raw["process_count"],
        raw["max_core_mb"],
        raw["scheduler_nodes"],
        raw["runtime_minutes"],
        raw["scheduler_memory_gb"],
    )


def _environment_to_dict(environment):
    return {
        "mode": environment.mode.value,
        "modules": list(environment.modules),
        "setup_script": environment.setup_script,
    }


def _environment_from_dict(raw):
    if not isinstance(raw, dict):
        raise TypeError("ORCA runtime environment must be an object")
    return RuntimeEnvironment(
        RuntimeEnvironmentMode(raw["mode"]),
        tuple(raw["modules"]),
        raw["setup_script"],
    )


def _orca_runtime_to_dict(runtime):
    if runtime is None:
        return None
    evidence = runtime.version_evidence
    return {
        "executable_path": runtime.executable_path,
        "environment": _environment_to_dict(runtime.environment),
        "version_evidence": {
            "version_text": evidence.version_text,
            "version": evidence.version,
            "version_family": evidence.version_family.value if evidence.version_family else None,
            "detection_source": evidence.detection_source,
        },
    }


def _orca_runtime_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("version_evidence"), dict):
        raise TypeError("ORCA runtime must be an object")
    evidence_raw = raw["version_evidence"]
    family = evidence_raw["version_family"]
    return OrcaRuntimeConfiguration(
        raw["executable_path"],
        _environment_from_dict(raw["environment"]),
        OrcaVersionEvidence(
            evidence_raw["version_text"],
            evidence_raw["version"],
            OrcaVersionFamily(family) if family is not None else None,
            evidence_raw["detection_source"],
        ),
    )


def _optimization_result_to_dict(result):
    if result is None:
        return None
    return {field: getattr(result, field) for field in result.__dataclass_fields__}


def _optimization_result_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("ORCA optimization result must be an object")
    return OrcaOptimizationResultEvidence(**raw)


def _frequency_result_to_dict(result):
    if result is None:
        return None
    return {
        "completion": result.completion.value,
        "imaginary_classification": result.imaginary_classification.value,
        "modes": [
            {
                "mode_index": mode.mode_index,
                "frequency_cm1": mode.frequency_cm1,
                "orca_reported_imaginary": mode.orca_reported_imaginary,
            }
            for mode in result.modes
        ],
        "hessian_dimension": result.hessian_dimension,
        "diagnostic": result.diagnostic,
    }


def _frequency_result_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("ORCA frequency result must be an object")
    return OrcaFrequencyEvidence(
        OrcaFrequencyCompletion(raw["completion"]),
        OrcaImaginaryModeClassification(raw["imaginary_classification"]),
        tuple(
            OrcaFrequencyModeEvidence(
                item["mode_index"],
                item["frequency_cm1"],
                item["orca_reported_imaginary"],
            )
            for item in raw["modes"]
        ),
        raw["hessian_dimension"],
        raw["diagnostic"],
    )


def _wbl_contact_to_dict(contact):
    if contact is None:
        return None
    return {
        "atom_index": contact.atom_index,
        "linker": contact.linker.value,
        "gamma0_ev": contact.gamma0_ev,
        "parameter_status": contact.parameter_status.value,
        "subspace_mode": contact.subspace_mode.value,
        "manual_direction": (
            list(contact.manual_direction)
            if contact.manual_direction is not None
            else None
        ),
        "manual_ao_indices": list(contact.manual_ao_indices),
    }


def _wbl_contact_from_dict(raw):
    if not isinstance(raw, dict):
        raise TypeError("ORCA WBL contact settings must be an object")
    direction = raw["manual_direction"]
    return OrcaWblContactSettings(
        atom_index=raw["atom_index"],
        linker=WblLinkerKind(raw["linker"]),
        gamma0_ev=raw["gamma0_ev"],
        parameter_status=WblParameterStatus(raw["parameter_status"]),
        subspace_mode=WblContactSubspaceMode(raw["subspace_mode"]),
        manual_direction=tuple(direction) if direction is not None else None,
        manual_ao_indices=tuple(raw["manual_ao_indices"]),
    )


def _wbl_settings_to_dict(settings):
    if settings is None:
        return None
    return {
        "left": _wbl_contact_to_dict(settings.left),
        "right": _wbl_contact_to_dict(settings.right),
        "fermi_energy_ev": settings.fermi_energy_ev,
        "energy_min_relative_ev": settings.energy_min_relative_ev,
        "energy_max_relative_ev": settings.energy_max_relative_ev,
        "energy_step_ev": settings.energy_step_ev,
    }


def _wbl_settings_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("ORCA WBL settings must be an object")
    return OrcaWblSettings(
        _wbl_contact_from_dict(raw["left"]),
        _wbl_contact_from_dict(raw["right"]),
        raw["fermi_energy_ev"],
        raw["energy_min_relative_ev"],
        raw["energy_max_relative_ev"],
        raw["energy_step_ev"],
    )


def _wbl_result_to_dict(result):
    if result is None:
        return None
    return {
        "model_id": result.model_id,
        "model_classification": result.model_classification,
        "source_gbw_sha256": result.source_gbw_sha256,
        "wavefunction_json_sha256": result.wavefunction_json_sha256,
        "artifact_hashes": dict(result.artifact_hashes),
        "orca_2json_path": result.orca_2json_path,
        "t_alpha_at_fermi": result.t_alpha_at_fermi,
        "t_beta_at_fermi": result.t_beta_at_fermi,
        "t_total_at_fermi": result.t_total_at_fermi,
        "top_alpha": [list(item) for item in result.top_alpha],
        "top_beta": [list(item) for item in result.top_beta],
        "spin_treatment": result.spin_treatment.value,
        "top_total": [list(item) for item in result.top_total],
    }


def _wbl_result_from_dict(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("artifact_hashes"), dict):
        raise TypeError("ORCA WBL result evidence must be an object")
    return OrcaWblResultEvidence(
        raw["model_id"],
        raw["model_classification"],
        raw["source_gbw_sha256"],
        raw["wavefunction_json_sha256"],
        tuple(raw["artifact_hashes"].items()),
        raw["orca_2json_path"],
        raw["t_alpha_at_fermi"],
        raw["t_beta_at_fermi"],
        raw["t_total_at_fermi"],
        tuple(tuple(item) for item in raw["top_alpha"]),
        tuple(tuple(item) for item in raw["top_beta"]),
        WblSpinTreatment(
            raw.get("spin_treatment", WblSpinTreatment.SPIN_RESOLVED.value)
        ),
        tuple(tuple(item) for item in raw.get("top_total", ())),
    )


def _parse_attempt(raw: object, *, schema_version: int) -> ProjectStepAttempt:
    if not isinstance(raw, dict):
        raise TypeError("attempt record must be an object")
    return ProjectStepAttempt(
        job_id=raw["job_id"],
        submitted_at=_parse_optional_timestamp(
            raw["submitted_at"],
            "attempt submitted_at",
        ),
        finished_at=_parse_optional_timestamp(
            raw["finished_at"],
            "attempt finished_at",
        ),
        terminal_scheduler_state=raw["terminal_scheduler_state"],
        failure_reason=raw["failure_reason"],
        submit_script_filename=raw["submit_script_filename"],
        slurm_output_filename=raw["slurm_output_filename"],
        input_hashes=(
            tuple(_require_hash_object(raw["input_hashes"]).items())
            if schema_version >= 3
            else ()
        ),
        scheduler_kind=(
            SchedulerKind(raw["scheduler_kind"])
            if schema_version >= 6
            else SchedulerKind.SLURM
        ),
    )


def _parse_electrode_provenance(
    raw: object,
    *,
    include_extensions: bool,
) -> ProjectElectrodeClusterProvenance:
    if not isinstance(raw, dict):
        raise TypeError("electrode provenance record must be an object")
    mapping = raw["local_to_global_atom_indices"]
    if not isinstance(mapping, list):
        raise TypeError("electrode local-to-global mapping must be an array")
    raw_identities = raw["atom_identities"]
    if not isinstance(raw_identities, list):
        raise TypeError("electrode atom_identities must be an array")
    raw_corners = raw["reference_corner_lattice_keys"]
    if not isinstance(raw_corners, list):
        raise TypeError("electrode reference corners must be an array")
    raw_extensions = raw["lattice_extensions"] if include_extensions else []
    if not isinstance(raw_extensions, list):
        raise TypeError("electrode lattice_extensions must be an array")
    return ProjectElectrodeClusterProvenance(
        side=raw["side"],
        geometry_model=raw["geometry_model"],
        pyramid_layers=raw["pyramid_layers"],
        nearest_neighbor_spacing_angstrom=raw[
            "nearest_neighbor_spacing_angstrom"
        ],
        roll_degrees=raw["roll_degrees"],
        atom_identities=tuple(
            _parse_electrode_atom_identity(item) for item in raw_identities
        ),
        local_to_global_indices=tuple(mapping),
        apex_lattice_key=tuple(raw["apex_lattice_key"]),
        reference_corner_lattice_keys=tuple(tuple(key) for key in raw_corners),
        lattice_extensions=tuple(
            _parse_electrode_lattice_extension(item) for item in raw_extensions
        ),
    )


def _parse_electrode_atom_identity(raw: object) -> ProjectElectrodeAtomIdentity:
    if not isinstance(raw, dict):
        raise TypeError("electrode atom identity must be an object")
    lattice_key = raw["lattice_key"]
    if lattice_key is not None and not isinstance(lattice_key, list):
        raise TypeError("electrode lattice_key must be an array or null")
    return ProjectElectrodeAtomIdentity(
        local_index=raw["local_index"],
        layer_index=raw["layer_index"],
        lattice_key=(tuple(lattice_key) if lattice_key is not None else None),
        standard_pyramid_member=raw["standard_pyramid_member"],
    )


def _parse_electrode_lattice_extension(
    raw: object,
) -> ProjectElectrodeLatticeExtension:
    if not isinstance(raw, dict):
        raise TypeError("electrode lattice extension must be an object")
    lattice_key = raw["lattice_key"]
    if not isinstance(lattice_key, list):
        raise TypeError("electrode extension lattice_key must be an array")
    return ProjectElectrodeLatticeExtension(
        origin=raw["origin"],
        layer_index=raw["layer_index"],
        lattice_key=tuple(lattice_key),
        global_atom_index=raw["global_atom_index"],
    )


def _parse_legacy_electrode_provenance(
    raw: object,
) -> ProjectElectrodeClusterProvenance:
    """Decode schema 4-6 Au59 mappings without coordinate resources."""

    if not isinstance(raw, dict):
        raise TypeError("legacy electrode provenance record must be an object")
    mapping = raw["local_to_global_indices"]
    if not isinstance(mapping, list) or len(mapping) != 59:
        raise TypeError("legacy electrode mapping must contain 59 indexes")
    contact_au_index = raw["contact_au_index"]
    if (
        isinstance(contact_au_index, bool)
        or not isinstance(contact_au_index, int)
        or contact_au_index != mapping[0]
    ):
        raise ValueError(
            "legacy electrode contact index must equal the mapped apex index"
        )
    variant = raw["template_variant"]
    if variant not in {"A", "B"}:
        raise ValueError("legacy electrode template variant must be A or B")
    identities = tuple(
        ProjectElectrodeAtomIdentity(
            local_index=index,
            layer_index=_legacy_layer_index(index) if index < 56 else None,
            lattice_key=_legacy_lattice_key(index) if index < 56 else None,
            standard_pyramid_member=index < 56,
        )
        for index in range(59)
    )
    return ProjectElectrodeClusterProvenance(
        side="LEFT" if variant == "A" else "RIGHT",
        geometry_model="LegacyAu59V1",
        pyramid_layers=6,
        nearest_neighbor_spacing_angstrom=2.88372,
        roll_degrees=raw["roll_degrees"],
        atom_identities=identities,
        local_to_global_indices=tuple(mapping),
        apex_lattice_key=(0, 0, 0),
        reference_corner_lattice_keys=((0, 0, 5), (0, 5, 0), (5, 0, 0)),
    )


def _legacy_layer_index(local_index: int) -> int:
    boundaries = (1, 4, 10, 20, 35, 56)
    return next(layer for layer, boundary in enumerate(boundaries) if local_index < boundary)


def _legacy_lattice_key(local_index: int) -> tuple[int, int, int]:
    layer = _legacy_layer_index(local_index)
    offset = (0, 1, 4, 10, 20, 35)[layer]
    keys = sorted(
        (i, j, layer - i - j)
        for i in range(layer + 1)
        for j in range(layer - i + 1)
    )
    return keys[local_index - offset]


def _parse_restart_provenance(raw: object) -> ProjectRestartProvenance:
    if not isinstance(raw, dict):
        raise TypeError("restart_provenance must be an object or null")
    return ProjectRestartProvenance(
        source_project_id=UUID(raw["source_project_id"]),
        source_step=ProjectStepKind(raw["source_step"]),
        source_job_id=raw["source_job_id"],
        source_geometry_sha256=raw["source_geometry_sha256"],
    )


def _require_hash_object(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("attempt input_hashes must be an object")
    return value


def _format_optional_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_optional_timestamp(value: object, field_name: str) -> datetime | None:
    return None if value is None else _parse_timestamp(value, field_name)


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed
