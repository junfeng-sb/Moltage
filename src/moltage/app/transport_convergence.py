"""Step-3 eligibility and complete local submission preflight."""

from dataclasses import dataclass, field
import hashlib
from uuid import UUID

from moltage.aims.transport_convergence_bundle import (
    TransportConvergenceAimsInputs,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectElectrodeClusterProvenance,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.electrode import AppliedElectrodePlacement
from moltage.domain.server_profile import SlurmExecutionPreset, SlurmMailSettings
from moltage.domain.structure import MolecularStructure
from moltage.remote.slurm import render_submit_script


class TransportConvergenceEligibilityError(ValueError):
    """Raised when current authoritative/session state cannot enter Step 3."""


@dataclass(frozen=True, slots=True)
class TransportConvergenceContext:
    """Proof that the current accepted viewer structure is eligible for Step 3."""

    snapshot: ProjectRecoverySnapshot
    source_structure: MolecularStructure
    working_structure: MolecularStructure
    applied_electrodes: AppliedElectrodePlacement

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ProjectRecoverySnapshot):
            raise TransportConvergenceEligibilityError(
                "Step 3 requires a managed recovered-project session"
            )
        if self.snapshot.requires_profile_rebind:
            raise TransportConvergenceEligibilityError(
                "confirm the local server-profile binding before Step 3"
            )
        if self.snapshot.active_step_kind is not ProjectStepKind.MOLECULE_AU_OPT:
            raise TransportConvergenceEligibilityError(
                "Step 3 requires a recovered Step-2 source"
            )
        if self.snapshot.active_step.state is not ProjectStepState.SUCCEEDED:
            raise TransportConvergenceEligibilityError(
                "Step 2 must be authoritatively SUCCEEDED before Step 3"
            )
        recovered = self.snapshot.optimized_structure
        recovered_connectivity = self.snapshot.connectivity
        if recovered is None or recovered_connectivity is None:
            raise TransportConvergenceEligibilityError(
                "Step 3 requires the validated optimized Step-2 structure"
            )
        if not isinstance(self.source_structure, MolecularStructure):
            raise TransportConvergenceEligibilityError(
                "the viewer source is not a molecular structure"
            )
        if self.source_structure is not recovered:
            raise TransportConvergenceEligibilityError(
                "the viewer source is not the recovered optimized Step-2 structure"
            )
        if not isinstance(self.applied_electrodes, AppliedElectrodePlacement):
            raise TransportConvergenceEligibilityError(
                "apply both Au-pyramid electrode clusters with Done before Step 3"
            )
        proposal = self.applied_electrodes.proposal
        if len(proposal.clusters) != 2:
            raise TransportConvergenceEligibilityError(
                "Step 3 requires both applied electrode clusters"
            )
        if proposal.source_structure is not recovered:
            raise TransportConvergenceEligibilityError(
                "the applied electrodes do not originate from recovered Step 2"
            )
        if proposal.source_connectivity is not recovered_connectivity:
            raise TransportConvergenceEligibilityError(
                "the applied electrodes do not preserve recovered Step-2 topology"
            )
        if not isinstance(self.working_structure, MolecularStructure):
            raise TransportConvergenceEligibilityError(
                "the current working structure is unavailable"
            )
        if _atom_identity(self.working_structure) != _atom_identity(
            self.applied_electrodes.structure
        ):
            raise TransportConvergenceEligibilityError(
                "the current working structure may change coordinates only after "
                "the accepted electrode result"
            )

        step3 = _project_step(
            self.snapshot.project,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        if (
            step3.state is not ProjectStepState.NOT_STARTED
            or step3.job_id is not None
            or step3.cluster_name is not None
            or step3.submitted_at is not None
            or step3.started_at is not None
            or step3.finished_at is not None
            or step3.input_hashes
            or step3.last_error is not None
            or step3.scheduler_state is not None
            or step3.submit_script_filename is not None
            or step3.slurm_output_filename is not None
            or step3.attempts
        ):
            raise TransportConvergenceEligibilityError(
                "Step 3 already has authoritative submission state"
            )

    @property
    def project(self) -> CalculationProject:
        return self.snapshot.project

    @property
    def electrode_provenance(
        self,
    ) -> tuple[ProjectElectrodeClusterProvenance, ...]:
        """Return the exact two-side metadata accepted by Au Tool Done."""

        return provenance_from_applied_electrodes(self.applied_electrodes)


@dataclass(frozen=True, slots=True)
class TransportConvergenceSubmissionBundle:
    """Immutable three-file Step-3 bundle with local SHA256 metadata."""

    project_id: UUID
    structure: MolecularStructure
    settings: TransportConvergenceSettings
    geometry_bytes: bytes = field(repr=False)
    control_bytes: bytes = field(repr=False)
    submit_bytes: bytes = field(repr=False)
    input_hashes: tuple[tuple[str, str], ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID):
            raise TypeError("Step-3 submission bundle requires a project UUID")
        if not isinstance(self.structure, MolecularStructure) or not self.structure:
            raise TypeError("Step-3 submission bundle requires a structure")
        if not isinstance(self.settings, TransportConvergenceSettings):
            raise TypeError(
                "Step-3 submission bundle requires validated settings"
            )
        files = self.files()
        if any(not isinstance(data, bytes) or not data for data in files.values()):
            raise TypeError("Step-3 submission files must be non-empty bytes")
        object.__setattr__(
            self,
            "input_hashes",
            tuple(
                (filename, hashlib.sha256(data).hexdigest())
                for filename, data in files.items()
            ),
        )

    def files(self) -> dict[str, bytes]:
        """Return the accepted upload order as a fresh mapping."""

        return {
            "geometry.in": self.geometry_bytes,
            "control.in": self.control_bytes,
            "submit.sh": self.submit_bytes,
        }


def prepare_transport_convergence_submission_bundle(
    aims_inputs: TransportConvergenceAimsInputs,
    preset: SlurmExecutionPreset,
    project_id: UUID,
    *,
    mail_settings: SlurmMailSettings | None = None,
) -> TransportConvergenceSubmissionBundle:
    """Render submit.sh and hash all files before any remote operation."""

    if not isinstance(aims_inputs, TransportConvergenceAimsInputs):
        raise TypeError(
            "Step-3 submission preflight requires transport-convergence inputs"
        )
    submit_text = render_submit_script(
        preset,
        project_id,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
        mail_settings=mail_settings,
    )
    return TransportConvergenceSubmissionBundle(
        project_id,
        aims_inputs.structure,
        aims_inputs.settings,
        aims_inputs.geometry_text.encode("utf-8"),
        aims_inputs.control_text.encode("utf-8"),
        submit_text.encode("utf-8"),
    )


def _project_step(
    project: CalculationProject,
    step_kind: ProjectStepKind,
):
    return next(step for step in project.steps if step.kind is step_kind)


def _atom_identity(
    structure: MolecularStructure,
) -> tuple[tuple[int, str], ...]:
    return tuple((atom.index, atom.element) for atom in structure)
