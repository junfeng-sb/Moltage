"""Persistent four-step calculation-project domain model."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from pathlib import PurePosixPath
from uuid import UUID
import re

from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import OrcaRuntimeConfiguration
from moltage.orca.evidence import OrcaFrequencyEvidence
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings
from moltage.orca.wbl import OrcaWblResultEvidence, OrcaWblSettings
from moltage.domain.au_pyramid import (
    AU_PYRAMID_GEOMETRY_MODEL,
    AU_PYRAMID_SPACING_ANGSTROM,
    canonical_reference_corner_keys,
    tetrahedral_atom_count,
)


PROJECT_SCHEMA_VERSION = 10
LEGACY_PROJECT_SCHEMA_VERSIONS = (1, 2, 3, 4, 5, 6, 7, 8, 9)
MANAGED_METADATA_DIRECTORY = ".moltage"
LEGACY_MANAGED_METADATA_DIRECTORIES = (".aims_transport",)


class CalculationProjectValidationError(ValueError):
    """Raised when persistent project state violates a workflow invariant."""


class ProjectStepKind(StrEnum):
    """The four stable calculation stages in workflow order."""

    MOLECULE_OPT = "MOLECULE_OPT"
    MOLECULE_AU_OPT = "MOLECULE_AU_OPT"
    TRANSPORT_CONVERGENCE = "TRANSPORT_CONVERGENCE"
    TRANSMISSION = "TRANSMISSION"
    ORCA_OPTIMIZATION = "ORCA_OPTIMIZATION"
    ORCA_WBL_TRANSMISSION = "ORCA_WBL_TRANSMISSION"
    ORCA_FREQUENCY = "ORCA_FREQUENCY"


class CalculationWorkflowKind(StrEnum):
    """Stable workflow identity used for step validation and GUI dispatch."""

    FHI_AIMS_AITRANSS = "FHI_AIMS_AITRANSS"
    ORCA = "ORCA"


class ProjectStepState(StrEnum):
    """Persistent scheduler/scientific states without collapsing meanings."""

    NOT_STARTED = "NOT_STARTED"
    SKIPPED = "SKIPPED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SCHEDULER_COMPLETED = "SCHEDULER_COMPLETED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProjectElectrodeAtomIdentity:
    """Persisted local identity for a standard or bounded legacy Au atom."""

    local_index: int
    layer_index: int | None
    lattice_key: tuple[int, int, int] | None
    standard_pyramid_member: bool

    def __post_init__(self) -> None:
        _validate_nonnegative_integer(self.local_index, "electrode local index")
        if not isinstance(self.standard_pyramid_member, bool):
            raise CalculationProjectValidationError(
                "standard-pyramid membership must be boolean"
            )
        if self.standard_pyramid_member:
            if self.layer_index is None or self.lattice_key is None:
                raise CalculationProjectValidationError(
                    "standard electrode atoms require layer and lattice identities"
                )
            _validate_nonnegative_integer(self.layer_index, "electrode layer index")
            key = tuple(self.lattice_key)
            if len(key) != 3 or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in key
            ):
                raise CalculationProjectValidationError(
                    "electrode lattice key must contain three non-negative integers"
                )
            if sum(key) != self.layer_index:
                raise CalculationProjectValidationError(
                    "electrode lattice key must identify its recorded layer"
                )
            object.__setattr__(self, "lattice_key", key)
        elif self.layer_index is not None or self.lattice_key is not None:
            raise CalculationProjectValidationError(
                "nonstandard legacy atoms must not claim lattice identities"
            )


@dataclass(frozen=True, slots=True)
class ProjectElectrodeLatticeExtension:
    """Persisted identity and mapping for one generated extension Au atom."""

    origin: str
    layer_index: int
    lattice_key: tuple[int, int, int]
    global_atom_index: int

    def __post_init__(self) -> None:
        if self.origin != "LATTICE_EXTENSION":
            raise CalculationProjectValidationError(
                "electrode lattice extension origin must be LATTICE_EXTENSION"
            )
        _validate_nonnegative_integer(
            self.layer_index,
            "electrode lattice extension layer index",
        )
        key = tuple(self.lattice_key)
        if len(key) != 3 or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in key
        ):
            raise CalculationProjectValidationError(
                "electrode extension lattice key must contain three signed integers"
            )
        if sum(key) != self.layer_index:
            raise CalculationProjectValidationError(
                "electrode extension lattice key must identify its recorded layer"
            )
        _validate_nonnegative_integer(
            self.global_atom_index,
            "electrode lattice extension atom index",
        )
        object.__setattr__(self, "lattice_key", key)


@dataclass(frozen=True, slots=True)
class ProjectElectrodeClusterProvenance:
    """Persistent side-specific generated or bounded-legacy electrode identity."""

    side: str
    geometry_model: str
    pyramid_layers: int
    nearest_neighbor_spacing_angstrom: float
    roll_degrees: int | None
    atom_identities: tuple[ProjectElectrodeAtomIdentity, ...]
    local_to_global_indices: tuple[int, ...]
    apex_lattice_key: tuple[int, int, int]
    reference_corner_lattice_keys: tuple[
        tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]
    ]
    lattice_extensions: tuple[ProjectElectrodeLatticeExtension, ...] = ()

    def __post_init__(self) -> None:
        normalized_side = str(self.side).upper()
        if normalized_side not in {"LEFT", "RIGHT"}:
            raise CalculationProjectValidationError(
                "electrode provenance side must be LEFT or RIGHT"
            )
        _require_text(self.geometry_model, "electrode geometry model")
        if (
            isinstance(self.pyramid_layers, bool)
            or not isinstance(self.pyramid_layers, int)
            or self.pyramid_layers < 2
            or self.pyramid_layers > 10
        ):
            raise CalculationProjectValidationError(
                "electrode pyramid layers must be an integer in 2..10"
            )
        spacing = self.nearest_neighbor_spacing_angstrom
        if (
            isinstance(spacing, bool)
            or not isinstance(spacing, (int, float))
            or not isfinite(float(spacing))
            or float(spacing) <= 0.0
        ):
            raise CalculationProjectValidationError(
                "electrode nearest-neighbor spacing must be positive and finite"
            )
        if self.roll_degrees is not None and (
            isinstance(self.roll_degrees, bool)
            or not isinstance(self.roll_degrees, int)
            or self.roll_degrees < 0
            or self.roll_degrees >= 360
        ):
            raise CalculationProjectValidationError(
                "electrode roll angle must be None or an integer in [0, 360)"
            )
        identities = tuple(self.atom_identities)
        if not identities or any(
            not isinstance(item, ProjectElectrodeAtomIdentity) for item in identities
        ):
            raise CalculationProjectValidationError(
                "electrode provenance requires ordered atom identities"
            )
        if any(item.local_index != index for index, item in enumerate(identities)):
            raise CalculationProjectValidationError(
                "electrode atom identities must cover local order exactly"
            )
        standard_keys = tuple(
            item.lattice_key for item in identities if item.standard_pyramid_member
        )
        if len(set(standard_keys)) != len(standard_keys):
            raise CalculationProjectValidationError(
                "standard electrode lattice identities must be unique"
            )
        mapping = tuple(self.local_to_global_indices)
        if len(mapping) != len(identities):
            raise CalculationProjectValidationError(
                "electrode mapping must match its atom identity count"
            )
        for atom_index in mapping:
            _validate_nonnegative_integer(atom_index, "electrode atom index")
        if len(set(mapping)) != len(mapping):
            raise CalculationProjectValidationError(
                "electrode provenance atom indexes must be unique"
            )
        apex_key = tuple(self.apex_lattice_key)
        corners = tuple(tuple(key) for key in self.reference_corner_lattice_keys)
        if len(apex_key) != 3 or len(corners) != 3 or any(len(key) != 3 for key in corners):
            raise CalculationProjectValidationError(
                "electrode apex and reference corners must be lattice triplets"
            )
        local_by_key = {
            item.lattice_key: item.local_index
            for item in identities
            if item.standard_pyramid_member
        }
        if apex_key not in local_by_key:
            raise CalculationProjectValidationError(
                "electrode apex lattice identity is absent"
            )
        if len(set(corners)) != 3 or not set(corners) <= set(local_by_key):
            raise CalculationProjectValidationError(
                "electrode reference-corner lattice identities are invalid"
            )
        extensions = tuple(self.lattice_extensions)
        if any(
            not isinstance(item, ProjectElectrodeLatticeExtension)
            for item in extensions
        ):
            raise CalculationProjectValidationError(
                "electrode lattice extensions must contain extension records"
            )
        if extensions and self.geometry_model != AU_PYRAMID_GEOMETRY_MODEL:
            raise CalculationProjectValidationError(
                "bounded legacy electrodes cannot claim lattice extensions"
            )
        if any(item.layer_index >= self.pyramid_layers for item in extensions):
            raise CalculationProjectValidationError(
                "electrode lattice extension layer is outside the standard pyramid"
            )
        extension_keys = tuple(
            (item.layer_index, item.lattice_key) for item in extensions
        )
        if len(set(extension_keys)) != len(extension_keys):
            raise CalculationProjectValidationError(
                "electrode lattice extension identities must be unique"
            )
        standard_layer_keys = {
            (item.layer_index, item.lattice_key)
            for item in identities
            if item.standard_pyramid_member
        }
        if standard_layer_keys & set(extension_keys):
            raise CalculationProjectValidationError(
                "electrode lattice extension collides with the standard core"
            )
        extension_indices = tuple(item.global_atom_index for item in extensions)
        if extension_indices != tuple(sorted(extension_indices)):
            raise CalculationProjectValidationError(
                "electrode lattice extensions must preserve per-side append order"
            )
        if len(set(extension_indices)) != len(extension_indices):
            raise CalculationProjectValidationError(
                "electrode lattice extension atom indexes must be unique"
            )
        if set(mapping) & set(extension_indices):
            raise CalculationProjectValidationError(
                "electrode standard and extension mappings must not overlap"
            )
        if self.geometry_model == AU_PYRAMID_GEOMETRY_MODEL:
            expected_count = tetrahedral_atom_count(self.pyramid_layers)
            if len(identities) != expected_count or not all(
                item.standard_pyramid_member for item in identities
            ):
                raise CalculationProjectValidationError(
                    "canonical electrode provenance must contain every standard "
                    "pyramid atom"
                )
            expected_identity_order = tuple(
                (layer, key)
                for layer in range(self.pyramid_layers)
                for key in sorted(
                    (i, j, layer - i - j)
                    for i in range(layer + 1)
                    for j in range(layer - i + 1)
                )
            )
            actual_identity_order = tuple(
                (item.layer_index, item.lattice_key) for item in identities
            )
            if actual_identity_order != expected_identity_order:
                raise CalculationProjectValidationError(
                    "canonical electrode lattice identities are incomplete or "
                    "out of order"
                )
            if apex_key != (0, 0, 0) or corners != canonical_reference_corner_keys(
                self.pyramid_layers
            ):
                raise CalculationProjectValidationError(
                    "canonical electrode apex or reference corners are invalid"
                )
            if not isclose(
                float(spacing),
                AU_PYRAMID_SPACING_ANGSTROM,
                rel_tol=0.0,
                abs_tol=1.0e-10,
            ):
                raise CalculationProjectValidationError(
                    "canonical electrode spacing does not match its geometry model"
                )
        object.__setattr__(self, "side", normalized_side)
        object.__setattr__(self, "nearest_neighbor_spacing_angstrom", float(spacing))
        object.__setattr__(self, "atom_identities", identities)
        object.__setattr__(self, "local_to_global_indices", mapping)
        object.__setattr__(self, "apex_lattice_key", apex_key)
        object.__setattr__(self, "reference_corner_lattice_keys", corners)
        object.__setattr__(self, "lattice_extensions", extensions)

    @property
    def contact_au_index(self) -> int:
        local_by_key = {
            item.lattice_key: item.local_index
            for item in self.atom_identities
            if item.standard_pyramid_member
        }
        return self.local_to_global_indices[local_by_key[self.apex_lattice_key]]


@dataclass(frozen=True, slots=True)
class ProjectRestartProvenance:
    """Trace one user-edited restart project to its immutable source input."""

    source_project_id: UUID
    source_step: ProjectStepKind
    source_job_id: str
    source_geometry_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_project_id, UUID):
            raise CalculationProjectValidationError(
                "restart source project ID must be a UUID"
            )
        if not isinstance(self.source_step, ProjectStepKind):
            raise CalculationProjectValidationError(
                "restart source step is unsupported"
            )
        _require_text(self.source_job_id, "restart source Job ID")
        if re.fullmatch(r"[0-9]+", self.source_job_id) is None:
            raise CalculationProjectValidationError(
                "restart source Job ID must contain decimal digits only"
            )
        if (
            not isinstance(self.source_geometry_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_geometry_sha256) is None
        ):
            raise CalculationProjectValidationError(
                "restart source geometry SHA256 must be 64 lowercase hex digits"
            )


STEP_RELATIVE_FOLDERS = {
    ProjectStepKind.MOLECULE_OPT: ".",
    ProjectStepKind.MOLECULE_AU_OPT: "molecule_Au",
    ProjectStepKind.TRANSPORT_CONVERGENCE: "molecule_Au/transport",
    ProjectStepKind.TRANSMISSION: "molecule_Au/transport",
    ProjectStepKind.ORCA_OPTIMIZATION: ".",
    ProjectStepKind.ORCA_WBL_TRANSMISSION: "wbl",
    ProjectStepKind.ORCA_FREQUENCY: "frequency",
}

_FHI_STEP_KINDS = (
    ProjectStepKind.MOLECULE_OPT,
    ProjectStepKind.MOLECULE_AU_OPT,
    ProjectStepKind.TRANSPORT_CONVERGENCE,
    ProjectStepKind.TRANSMISSION,
)
_ORCA_REQUIRED_STEP_KINDS = (ProjectStepKind.ORCA_OPTIMIZATION,)


def workflow_step_kinds(
    workflow_kind: CalculationWorkflowKind,
    *,
    include_optional_frequency: bool = False,
    include_optional_wbl: bool = False,
) -> tuple[ProjectStepKind, ...]:
    workflow_kind = CalculationWorkflowKind(workflow_kind)
    if workflow_kind is CalculationWorkflowKind.FHI_AIMS_AITRANSS:
        return _FHI_STEP_KINDS
    return (
        *_ORCA_REQUIRED_STEP_KINDS,
        *((ProjectStepKind.ORCA_WBL_TRANSMISSION,) if include_optional_wbl else ()),
        *((ProjectStepKind.ORCA_FREQUENCY,) if include_optional_frequency else ()),
    )


@dataclass(frozen=True, slots=True)
class ProjectStepAttempt:
    """Minimal immutable provenance for one historical Step-3/Step-4 attempt."""

    job_id: str
    submitted_at: datetime | None
    finished_at: datetime | None
    terminal_scheduler_state: str | None
    failure_reason: str | None
    submit_script_filename: str
    slurm_output_filename: str
    input_hashes: tuple[tuple[str, str], ...] = ()
    scheduler_kind: SchedulerKind = SchedulerKind.SLURM

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "scheduler_kind", SchedulerKind(self.scheduler_kind))
        except (TypeError, ValueError):
            raise CalculationProjectValidationError(
                "attempt scheduler type must be SLURM or LSF"
            ) from None
        _require_text(self.job_id, "attempt job ID")
        for field_name in ("submitted_at", "finished_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                _validate_timestamp(timestamp, field_name)
        for field_name in ("terminal_scheduler_state", "failure_reason"):
            _validate_optional_text(getattr(self, field_name), field_name)
        _validate_filename(
            self.submit_script_filename,
            "attempt submit script filename",
        )
        _validate_filename(
            self.slurm_output_filename,
            "attempt Slurm output filename",
        )
        object.__setattr__(
            self,
            "input_hashes",
            _normalize_input_hashes(self.input_hashes),
        )


@dataclass(frozen=True, slots=True)
class ProjectStepRecord:
    """Small persistent record for one logical workflow step."""

    kind: ProjectStepKind
    state: ProjectStepState
    relative_folder: str
    job_id: str | None = None
    cluster_name: str | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_hashes: tuple[tuple[str, str], ...] = ()
    last_error: str | None = None
    scheduler_state: str | None = None
    submit_script_filename: str | None = None
    slurm_output_filename: str | None = None
    attempts: tuple[ProjectStepAttempt, ...] = ()
    scheduler_kind: SchedulerKind | None = None
    orca_optimization_settings: OrcaOptimizationSettings | None = None
    orca_frequency_settings: OrcaFrequencySettings | None = None
    orca_runtime: OrcaRuntimeConfiguration | None = None
    orca_optimization_result: OrcaOptimizationResultEvidence | None = None
    orca_frequency_result: OrcaFrequencyEvidence | None = None
    orca_wbl_settings: OrcaWblSettings | None = None
    orca_wbl_result: OrcaWblResultEvidence | None = None
    orca_submitted_elements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.scheduler_kind is not None:
            try:
                object.__setattr__(
                    self, "scheduler_kind", SchedulerKind(self.scheduler_kind)
                )
            except (TypeError, ValueError):
                raise CalculationProjectValidationError(
                    "step scheduler type must be SLURM or LSF"
                ) from None
        elif self.job_id is not None:
            # In-memory compatibility for legacy callers and schema migrations.
            object.__setattr__(self, "scheduler_kind", SchedulerKind.SLURM)
        if not isinstance(self.kind, ProjectStepKind):
            raise CalculationProjectValidationError("step kind is unsupported")
        if not isinstance(self.state, ProjectStepState):
            raise CalculationProjectValidationError("step state is unsupported")
        expected_folder = STEP_RELATIVE_FOLDERS[self.kind]
        if self.relative_folder != expected_folder:
            raise CalculationProjectValidationError(
                f"{self.kind.value} folder must be {expected_folder!r}"
            )
        for field_name in (
            "job_id",
            "cluster_name",
            "last_error",
            "scheduler_state",
        ):
            _validate_optional_text(getattr(self, field_name), field_name)
        for field_name in (
            "submit_script_filename",
            "slurm_output_filename",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_filename(value, field_name)
        for field_name in ("submitted_at", "started_at", "finished_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                _validate_timestamp(timestamp, field_name)

        object.__setattr__(
            self,
            "input_hashes",
            _normalize_input_hashes(self.input_hashes),
        )

        attempts = tuple(self.attempts)
        if any(not isinstance(item, ProjectStepAttempt) for item in attempts):
            raise CalculationProjectValidationError(
                "step attempts must contain ProjectStepAttempt records"
            )
        if len({item.job_id for item in attempts}) != len(attempts):
            raise CalculationProjectValidationError(
                "step attempt Job IDs must be unique"
            )
        if attempts and self.kind not in {
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            ProjectStepKind.TRANSMISSION,
        }:
            raise CalculationProjectValidationError(
                "attempt history is supported only for Steps 3 and 4"
            )
        object.__setattr__(self, "attempts", attempts)
        orca_values = (
            self.orca_optimization_settings,
            self.orca_frequency_settings,
            self.orca_runtime,
            self.orca_optimization_result,
            self.orca_frequency_result,
            self.orca_wbl_settings,
            self.orca_wbl_result,
        )
        if self.kind not in {
            ProjectStepKind.ORCA_OPTIMIZATION,
            ProjectStepKind.ORCA_WBL_TRANSMISSION,
            ProjectStepKind.ORCA_FREQUENCY,
        } and (
            any(value is not None for value in orca_values)
            or bool(self.orca_submitted_elements)
        ):
            raise CalculationProjectValidationError(
                "ORCA evidence is valid only for ORCA project stages"
            )
        if self.kind is ProjectStepKind.ORCA_OPTIMIZATION:
            if (
                self.orca_frequency_settings is not None
                or self.orca_frequency_result is not None
                or self.orca_wbl_settings is not None
                or self.orca_wbl_result is not None
            ):
                raise CalculationProjectValidationError(
                    "frequency/WBL evidence is invalid on the ORCA optimization stage"
                )
            elements = tuple(self.orca_submitted_elements)
            if any(
                not isinstance(element, str)
                or not element
                or not element.isascii()
                or not element.isalpha()
                for element in elements
            ):
                raise CalculationProjectValidationError(
                    "ORCA submitted elements must be ordered element symbols"
                )
            object.__setattr__(self, "orca_submitted_elements", elements)
        if self.kind is ProjectStepKind.ORCA_FREQUENCY:
            if (
                self.orca_optimization_settings is not None
                or self.orca_optimization_result is not None
                or self.orca_wbl_settings is not None
                or self.orca_wbl_result is not None
            ):
                raise CalculationProjectValidationError(
                    "optimization evidence is valid only for the ORCA optimization stage"
                )
            if self.orca_submitted_elements:
                raise CalculationProjectValidationError(
                    "submitted element identity is stored on ORCA optimization"
                )
        if self.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION:
            if any(
                value is not None
                for value in (
                    self.orca_optimization_settings,
                    self.orca_optimization_result,
                    self.orca_frequency_settings,
                    self.orca_frequency_result,
                )
            ):
                raise CalculationProjectValidationError(
                    "optimization/frequency evidence is invalid on the ORCA WBL stage"
                )
            if self.orca_submitted_elements:
                raise CalculationProjectValidationError(
                    "submitted element identity is stored on ORCA optimization"
                )
            if self.job_id is not None or self.scheduler_kind is not None:
                raise CalculationProjectValidationError(
                    "ORCA WBL post-processing is not a scheduler job"
                )
            if self.state is ProjectStepState.SUCCEEDED and (
                self.orca_wbl_settings is None or self.orca_wbl_result is None
            ):
                raise CalculationProjectValidationError(
                    "successful ORCA WBL stage requires settings and result evidence"
                )


@dataclass(frozen=True, slots=True)
class CalculationProject:
    """Durable identity and workflow truth for one calculation campaign."""

    project_id: UUID
    display_name: str
    remote_directory_name: str
    source_molecule_name: str
    server_profile_id: UUID
    remote_project_path: str
    created_at: datetime
    updated_at: datetime
    revision: int
    steps: tuple[ProjectStepRecord, ...]
    starting_step: ProjectStepKind | None = None
    electrode_provenance: tuple[ProjectElectrodeClusterProvenance, ...] = ()
    legacy_electrode_recovery_allowed: bool = False
    restart_provenance: ProjectRestartProvenance | None = None
    workflow_kind: CalculationWorkflowKind = CalculationWorkflowKind.FHI_AIMS_AITRANSS
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_SCHEMA_VERSION:
            raise CalculationProjectValidationError(
                f"schema version must be {PROJECT_SCHEMA_VERSION}"
            )
        if not isinstance(self.project_id, UUID):
            raise CalculationProjectValidationError("project ID must be a UUID")
        if not isinstance(self.server_profile_id, UUID):
            raise CalculationProjectValidationError(
                "server profile ID must be a UUID"
            )
        _require_text(self.display_name, "display name")
        _validate_filename(self.remote_directory_name, "remote directory name")
        _require_text(self.source_molecule_name, "source molecule name")
        _validate_timestamp(self.created_at, "created at")
        _validate_timestamp(self.updated_at, "updated at")
        if self.updated_at < self.created_at:
            raise CalculationProjectValidationError(
                "updated timestamp must not precede created timestamp"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise CalculationProjectValidationError("revision must be an integer")
        if self.revision < 1:
            raise CalculationProjectValidationError("revision must be positive")

        remote_path = PurePosixPath(self.remote_project_path)
        if (
            not self.remote_project_path.startswith("/")
            or str(remote_path) != self.remote_project_path
            or ".." in remote_path.parts
            or remote_path.name != self.remote_directory_name
        ):
            raise CalculationProjectValidationError(
                "remote project path must be an absolute POSIX path ending in "
                "the remote directory name"
            )

        try:
            workflow_kind = CalculationWorkflowKind(self.workflow_kind)
        except (TypeError, ValueError):
            raise CalculationProjectValidationError("project workflow is unsupported") from None
        object.__setattr__(self, "workflow_kind", workflow_kind)
        steps = tuple(self.steps)
        expected_kinds = workflow_step_kinds(
            workflow_kind,
            include_optional_frequency=(
                workflow_kind is CalculationWorkflowKind.ORCA
                and any(step.kind is ProjectStepKind.ORCA_FREQUENCY for step in steps)
            ),
            include_optional_wbl=(
                workflow_kind is CalculationWorkflowKind.ORCA
                and any(
                    step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
                    for step in steps
                )
            ),
        )
        if tuple(step.kind for step in steps) != expected_kinds:
            raise CalculationProjectValidationError(
                "project steps do not match the selected workflow"
            )
        object.__setattr__(self, "steps", steps)

        starting_step = self.starting_step
        if starting_step is None:
            starting_step = infer_starting_step(steps)
            object.__setattr__(self, "starting_step", starting_step)
        allowed_starts = (
            {
                ProjectStepKind.MOLECULE_OPT,
                ProjectStepKind.MOLECULE_AU_OPT,
                ProjectStepKind.TRANSPORT_CONVERGENCE,
            }
            if workflow_kind is CalculationWorkflowKind.FHI_AIMS_AITRANSS
            else {ProjectStepKind.ORCA_OPTIMIZATION}
        )
        if starting_step not in allowed_starts:
            raise CalculationProjectValidationError("project starting stage does not match its workflow")
        starting_index = expected_kinds.index(starting_step)
        if any(
            step.state is not ProjectStepState.SKIPPED
            for step in steps[:starting_index]
        ):
            raise CalculationProjectValidationError(
                "steps before the recorded starting step must be SKIPPED"
            )
        if any(
            step.state is ProjectStepState.SKIPPED
            for step in steps[starting_index:]
        ):
            raise CalculationProjectValidationError(
                "the starting step and later steps must not be SKIPPED"
            )

        electrode_provenance = tuple(self.electrode_provenance)
        if workflow_kind is CalculationWorkflowKind.ORCA and electrode_provenance:
            raise CalculationProjectValidationError("ORCA optimization projects must not contain electrode provenance")
        if any(
            not isinstance(item, ProjectElectrodeClusterProvenance)
            for item in electrode_provenance
        ):
            raise CalculationProjectValidationError(
                "electrode provenance must contain cluster records"
            )
        if electrode_provenance:
            if len(electrode_provenance) != 2:
                raise CalculationProjectValidationError(
                    "electrode provenance requires exactly two electrode clusters"
                )
            if tuple(item.side for item in electrode_provenance) != (
                "LEFT",
                "RIGHT",
            ):
                raise CalculationProjectValidationError(
                    "electrode provenance must use LEFT then RIGHT side order"
                )
            if len({item.geometry_model for item in electrode_provenance}) != 1:
                raise CalculationProjectValidationError(
                    "both electrode sides must use the same geometry model"
                )
            if len({item.pyramid_layers for item in electrode_provenance}) != 1:
                raise CalculationProjectValidationError(
                    "both electrode sides must use the same pyramid layer count"
                )
            if len(
                {
                    item.nearest_neighbor_spacing_angstrom
                    for item in electrode_provenance
                }
            ) != 1:
                raise CalculationProjectValidationError(
                    "both electrode sides must use the same lattice spacing"
                )
            contact_indices = tuple(
                item.contact_au_index for item in electrode_provenance
            )
            if len(set(contact_indices)) != 2:
                raise CalculationProjectValidationError(
                    "electrode contacts must be distinct"
                )
            mapped_sets = tuple(
                set(item.local_to_global_indices)
                | {
                    extension.global_atom_index
                    for extension in item.lattice_extensions
                }
                for item in electrode_provenance
            )
            if mapped_sets[0] & mapped_sets[1]:
                raise CalculationProjectValidationError(
                    "electrode mappings must not overlap"
                )
        elif starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
            raise CalculationProjectValidationError(
                "direct Step-3 starts require persisted electrode provenance"
            )
        object.__setattr__(self, "electrode_provenance", electrode_provenance)

        if not isinstance(self.legacy_electrode_recovery_allowed, bool):
            raise CalculationProjectValidationError(
                "legacy electrode recovery flag must be boolean"
            )
        if electrode_provenance and self.legacy_electrode_recovery_allowed:
            raise CalculationProjectValidationError(
                "persisted provenance and legacy recovery mode are mutually exclusive"
            )
        transport_started = workflow_kind is CalculationWorkflowKind.FHI_AIMS_AITRANSS and any(
            step.kind
            in {
                ProjectStepKind.TRANSPORT_CONVERGENCE,
                ProjectStepKind.TRANSMISSION,
            }
            and step.state
            not in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}
            for step in steps
        )
        if (
            transport_started
            and not electrode_provenance
            and not self.legacy_electrode_recovery_allowed
        ):
            raise CalculationProjectValidationError(
                "started transport workflow requires electrode provenance"
            )

        if self.restart_provenance is not None and not isinstance(
            self.restart_provenance,
            ProjectRestartProvenance,
        ):
            raise CalculationProjectValidationError(
                "restart provenance must be a ProjectRestartProvenance record"
            )
        if self.restart_provenance is not None:
            if workflow_kind is CalculationWorkflowKind.ORCA:
                raise CalculationProjectValidationError("ORCA workflow does not support FHI restart provenance")
            expected_start = {
                ProjectStepKind.MOLECULE_OPT: ProjectStepKind.MOLECULE_OPT,
                ProjectStepKind.MOLECULE_AU_OPT: ProjectStepKind.MOLECULE_AU_OPT,
                ProjectStepKind.TRANSPORT_CONVERGENCE: (
                    ProjectStepKind.TRANSPORT_CONVERGENCE
                ),
                ProjectStepKind.TRANSMISSION: (
                    ProjectStepKind.TRANSPORT_CONVERGENCE
                ),
            }[self.restart_provenance.source_step]
            if starting_step is not expected_start:
                raise CalculationProjectValidationError(
                    "restart project starting step does not match its source step"
                )


def initial_step_records(
    starting_step: ProjectStepKind,
    workflow_kind: CalculationWorkflowKind = CalculationWorkflowKind.FHI_AIMS_AITRANSS,
) -> tuple[ProjectStepRecord, ...]:
    """Build initial state for a project whose first calculation is Step 1-3."""

    workflow_kind = CalculationWorkflowKind(workflow_kind)
    if workflow_kind is CalculationWorkflowKind.ORCA:
        if starting_step is not ProjectStepKind.ORCA_OPTIMIZATION:
            raise CalculationProjectValidationError("new ORCA projects must start with optimization")
        return (
            ProjectStepRecord(
                kind=ProjectStepKind.ORCA_OPTIMIZATION,
                state=ProjectStepState.NOT_STARTED,
                relative_folder=STEP_RELATIVE_FOLDERS[ProjectStepKind.ORCA_OPTIMIZATION],
            ),
        )
    if starting_step not in {
        ProjectStepKind.MOLECULE_OPT,
        ProjectStepKind.MOLECULE_AU_OPT,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
    }:
        raise CalculationProjectValidationError(
            "new projects may start only at Step 1, Step 2, or Step 3"
        )
    starting_index = _FHI_STEP_KINDS.index(starting_step)
    return tuple(
        ProjectStepRecord(
            kind=kind,
            state=(
                ProjectStepState.SKIPPED
                if _FHI_STEP_KINDS.index(kind) < starting_index
                else ProjectStepState.NOT_STARTED
            ),
            relative_folder=STEP_RELATIVE_FOLDERS[kind],
        )
        for kind in _FHI_STEP_KINDS
    )


def append_orca_frequency_step(project: CalculationProject) -> CalculationProject:
    """Create the optional stage only after explicit post-optimization action."""

    if project.workflow_kind is not CalculationWorkflowKind.ORCA:
        raise CalculationProjectValidationError("frequency can be added only to an ORCA project")
    if any(step.kind is ProjectStepKind.ORCA_FREQUENCY for step in project.steps):
        raise CalculationProjectValidationError("ORCA frequency stage already exists")
    optimization = project.steps[0]
    if (
        optimization.state is not ProjectStepState.SUCCEEDED
        or optimization.orca_optimization_result is None
        or not optimization.orca_optimization_result.succeeded
    ):
        raise CalculationProjectValidationError(
            "verified ORCA optimization success is required before adding frequency"
        )
    return replace(
        project,
        steps=(
            *project.steps,
            ProjectStepRecord(
                ProjectStepKind.ORCA_FREQUENCY,
                ProjectStepState.NOT_STARTED,
                STEP_RELATIVE_FOLDERS[ProjectStepKind.ORCA_FREQUENCY],
            ),
        ),
    )


def append_orca_wbl_step(
    project: CalculationProject,
    *,
    settings: OrcaWblSettings,
    result: OrcaWblResultEvidence,
    input_hashes: tuple[tuple[str, str], ...],
    finished_at: datetime,
) -> CalculationProject:
    """Persist one completed user-authorized WBL post-processing stage."""

    if project.workflow_kind is not CalculationWorkflowKind.ORCA:
        raise CalculationProjectValidationError("WBL can be added only to an ORCA project")
    if any(step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION for step in project.steps):
        raise CalculationProjectValidationError("ORCA WBL stage already exists")
    optimization = project.steps[0]
    if (
        optimization.state is not ProjectStepState.SUCCEEDED
        or optimization.orca_optimization_result is None
        or not optimization.orca_optimization_result.succeeded
        or not optimization.orca_optimization_result.wbl_input_ready
        or optimization.orca_optimization_result.gbw_sha256 is None
    ):
        raise CalculationProjectValidationError(
            "verified optimization and GBW evidence are required before adding WBL"
        )
    _validate_timestamp(finished_at, "WBL finished at")
    stage = ProjectStepRecord(
        ProjectStepKind.ORCA_WBL_TRANSMISSION,
        ProjectStepState.SUCCEEDED,
        STEP_RELATIVE_FOLDERS[ProjectStepKind.ORCA_WBL_TRANSMISSION],
        input_hashes=input_hashes,
        finished_at=finished_at,
        orca_runtime=optimization.orca_runtime,
        orca_wbl_settings=settings,
        orca_wbl_result=result,
    )
    steps = list(project.steps)
    steps.insert(1, stage)
    return replace(project, steps=tuple(steps))


def begin_orca_wbl_step(
    project: CalculationProject,
    *,
    settings: OrcaWblSettings,
    started_at: datetime,
) -> CalculationProject:
    """Persist one user-authorized WBL stage before post-processing starts."""

    if project.workflow_kind is not CalculationWorkflowKind.ORCA:
        raise CalculationProjectValidationError("WBL can be added only to an ORCA project")
    if any(step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION for step in project.steps):
        raise CalculationProjectValidationError("ORCA WBL stage already exists")
    optimization = project.steps[0]
    if (
        optimization.state is not ProjectStepState.SUCCEEDED
        or optimization.orca_optimization_result is None
        or not optimization.orca_optimization_result.succeeded
        or not optimization.orca_optimization_result.wbl_input_ready
        or optimization.orca_optimization_result.gbw_sha256 is None
    ):
        raise CalculationProjectValidationError(
            "verified optimization and GBW evidence are required before adding WBL"
        )
    _validate_timestamp(started_at, "WBL started at")
    stage = ProjectStepRecord(
        ProjectStepKind.ORCA_WBL_TRANSMISSION,
        ProjectStepState.RUNNING,
        STEP_RELATIVE_FOLDERS[ProjectStepKind.ORCA_WBL_TRANSMISSION],
        started_at=started_at,
        orca_runtime=optimization.orca_runtime,
        orca_wbl_settings=settings,
    )
    steps = list(project.steps)
    steps.insert(1, stage)
    return replace(project, steps=tuple(steps))


def complete_orca_wbl_step(
    project: CalculationProject,
    *,
    result: OrcaWblResultEvidence,
    input_hashes: tuple[tuple[str, str], ...],
    finished_at: datetime,
) -> CalculationProject:
    """Complete the existing local WBL post-processing stage."""

    _validate_timestamp(finished_at, "WBL finished at")
    step = next(
        (
            item
            for item in project.steps
            if item.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
        ),
        None,
    )
    if step is None or step.state is not ProjectStepState.RUNNING:
        raise CalculationProjectValidationError("running ORCA WBL stage is required")
    changed = replace(
        step,
        state=ProjectStepState.SUCCEEDED,
        input_hashes=input_hashes,
        finished_at=finished_at,
        last_error=None,
        orca_wbl_result=result,
    )
    return _replace_project_step(project, changed)


def fail_orca_wbl_step(
    project: CalculationProject,
    *,
    diagnostic: str,
    finished_at: datetime,
) -> CalculationProject:
    """Record an explicit failure for a started local WBL stage."""

    _require_text(diagnostic, "WBL failure diagnostic")
    _validate_timestamp(finished_at, "WBL finished at")
    step = next(
        (
            item
            for item in project.steps
            if item.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
        ),
        None,
    )
    if step is None or step.state is not ProjectStepState.RUNNING:
        raise CalculationProjectValidationError("running ORCA WBL stage is required")
    changed = replace(
        step,
        state=ProjectStepState.FAILED,
        finished_at=finished_at,
        last_error=diagnostic,
    )
    return _replace_project_step(project, changed)


def _replace_project_step(
    project: CalculationProject,
    changed: ProjectStepRecord,
) -> CalculationProject:
    return replace(
        project,
        steps=tuple(
            changed if item.kind is changed.kind else item
            for item in project.steps
        ),
    )


def required_initial_directories(
    starting_step: ProjectStepKind,
) -> tuple[str, ...]:
    """Return only the relative directories needed at initial creation."""

    if starting_step is ProjectStepKind.ORCA_OPTIMIZATION:
        return (MANAGED_METADATA_DIRECTORY,)
    if starting_step is ProjectStepKind.MOLECULE_OPT:
        return (MANAGED_METADATA_DIRECTORY,)
    if starting_step is ProjectStepKind.MOLECULE_AU_OPT:
        return (MANAGED_METADATA_DIRECTORY, "molecule_Au")
    if starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
        return (
            MANAGED_METADATA_DIRECTORY,
            "molecule_Au",
            "molecule_Au/transport",
        )
    raise CalculationProjectValidationError(
        "new projects may start only at Step 1, Step 2, or Step 3"
    )


def infer_starting_step(
    steps: tuple[ProjectStepRecord, ...],
) -> ProjectStepKind:
    """Infer legacy project starts solely from their leading SKIPPED records."""

    checked = tuple(steps)
    if checked and checked[0].kind is ProjectStepKind.ORCA_OPTIMIZATION:
        return ProjectStepKind.ORCA_OPTIMIZATION
    if len(checked) >= 2 and all(
        item.state is ProjectStepState.SKIPPED for item in checked[:2]
    ):
        return ProjectStepKind.TRANSPORT_CONVERGENCE
    if checked and checked[0].state is ProjectStepState.SKIPPED:
        return ProjectStepKind.MOLECULE_AU_OPT
    return ProjectStepKind.MOLECULE_OPT


def remote_step_directory(
    project: CalculationProject,
    step_kind: ProjectStepKind,
) -> str:
    """Return the canonical absolute directory for one project step."""

    if not isinstance(project, CalculationProject):
        raise CalculationProjectValidationError(
            "remote step directory requires a CalculationProject"
        )
    if not isinstance(step_kind, ProjectStepKind):
        raise CalculationProjectValidationError("step kind is unsupported")
    relative_folder = STEP_RELATIVE_FOLDERS[step_kind]
    if relative_folder == ".":
        return project.remote_project_path
    return str(PurePosixPath(project.remote_project_path) / relative_folder)


def _validate_timestamp(value: object, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CalculationProjectValidationError(
            f"{field_name.replace('_', ' ')} must be timezone-aware"
        )
    if value.utcoffset() is None:
        raise CalculationProjectValidationError(
            f"{field_name.replace('_', ' ')} must have a UTC offset"
        )


def _validate_nonnegative_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CalculationProjectValidationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CalculationProjectValidationError(
            f"{field_name.replace('_', ' ')} must not be empty"
        )
    if "\x00" in value or "\n" in value or "\r" in value:
        raise CalculationProjectValidationError(
            f"{field_name.replace('_', ' ')} must be one NUL-free line"
        )


def _validate_optional_text(value: object, field_name: str) -> None:
    if value is not None:
        _require_text(value, field_name)


def _validate_filename(value: object, field_name: str) -> None:
    _require_text(value, field_name)
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise CalculationProjectValidationError(
            f"{field_name.replace('_', ' ')} must be one safe path component"
        )
    if field_name == "remote directory name" and (
        ".." in value
        or value.endswith(".")
        or not all(character.isalnum() or character in "_-." for character in value)
    ):
        raise CalculationProjectValidationError(
            "remote directory name contains unsafe characters"
        )


def _normalize_input_hashes(
    hashes: object,
) -> tuple[tuple[str, str], ...]:
    try:
        checked = tuple(hashes)
    except TypeError:
        raise CalculationProjectValidationError(
            "input hashes must be filename/hash pairs"
        ) from None
    seen_names: set[str] = set()
    normalized: list[tuple[str, str]] = []
    for item in checked:
        if not isinstance(item, tuple) or len(item) != 2:
            raise CalculationProjectValidationError(
                "input hashes must be filename/hash pairs"
            )
        filename, digest = item
        _validate_filename(filename, "input hash filename")
        _require_text(digest, "input hash digest")
        if filename in seen_names:
            raise CalculationProjectValidationError(
                f"duplicate input hash filename: {filename}"
            )
        seen_names.add(filename)
        normalized.append((filename, digest))
    return tuple(sorted(normalized))
