"""Deterministic project naming, initialization, and start-step advice."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from moltage.domain.anchor import AnchorCandidate
from moltage.domain.calculation_project import (
    CalculationProject,
    CalculationWorkflowKind,
    ProjectElectrodeClusterProvenance,
    ProjectRestartProvenance,
    ProjectStepKind,
    initial_step_records,
)
from moltage.domain.structure import MolecularStructure


class ProjectPlanningError(ValueError):
    """Raised when a safe deterministic project plan cannot be produced."""


class StartStepAdvice(StrEnum):
    STEP1 = "STEP1"
    STEP2 = "STEP2"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class StartStepRecommendation:
    advice: StartStepAdvice
    recommended_step: ProjectStepKind | None
    confirmation_required: bool
    reason: str


def validate_project_base_name(base_name: str) -> str:
    """Validate a Unicode-safe conservative remote directory base name."""

    if not isinstance(base_name, str):
        raise ProjectPlanningError("project base name must be text")
    if base_name != base_name.strip() or not base_name:
        raise ProjectPlanningError(
            "project base name must be nonempty without surrounding whitespace"
        )
    if ".." in base_name:
        raise ProjectPlanningError("project base name must not contain '..'")
    if not all(character.isalnum() or character in "_-" for character in base_name):
        raise ProjectPlanningError(
            "project base name may contain only Unicode letters/numbers, '_' and '-'"
        )
    return base_name


def project_directory_candidates(
    base_name: str,
    local_date: date,
) -> Iterable[str]:
    """Yield the unsuffixed candidate followed by _02, _03, and so on."""

    normalized = validate_project_base_name(base_name)
    if not isinstance(local_date, date):
        raise ProjectPlanningError("project date must be a date")
    dated = f"{normalized}.{local_date:%Y%m%d}"
    yield dated
    suffix = 2
    while True:
        yield f"{dated}_{suffix:02d}"
        suffix += 1


def plan_project_directory_name(
    base_name: str,
    local_date: date,
    existing_names: Iterable[str],
) -> str:
    existing = frozenset(existing_names)
    return next(
        candidate
        for candidate in project_directory_candidates(base_name, local_date)
        if candidate not in existing
    )


def recommend_start_step(
    structure: MolecularStructure,
    anchors: Iterable[AnchorCandidate],
) -> StartStepRecommendation:
    """Reuse accepted anchor results; never infer Au attachment geometrically."""

    if not isinstance(structure, MolecularStructure):
        raise ProjectPlanningError("start-step advice requires a molecular structure")
    recognized = tuple(anchors)
    if any(not isinstance(anchor, AnchorCandidate) for anchor in recognized):
        raise ProjectPlanningError("anchors must contain AnchorCandidate records")
    if not recognized:
        return StartStepRecommendation(
            StartStepAdvice.AMBIGUOUS,
            None,
            True,
            "No supported linker sites were recognized; choose Step 1 or Step 2.",
        )

    attached_by_site = tuple(
        anchor for anchor in recognized if anchor.attached_au_indices
    )
    attached_indices = frozenset(
        atom_index
        for anchor in attached_by_site
        for atom_index in anchor.attached_au_indices
    )
    all_au_indices = frozenset(
        atom.index for atom in structure if atom.element == "Au"
    )
    extra_au_indices = all_au_indices - attached_indices

    if not attached_indices:
        return StartStepRecommendation(
            StartStepAdvice.STEP1,
            ProjectStepKind.MOLECULE_OPT,
            True,
            "Recognized linkers have no directly attached contact Au.",
        )
    if extra_au_indices:
        return StartStepRecommendation(
            StartStepAdvice.AMBIGUOUS,
            None,
            True,
            "Linker-bound Au and additional Au atoms were detected; choose Step 1 "
            "or Step 2 explicitly.",
        )
    if len(attached_by_site) == 2 and len(attached_indices) == 2:
        return StartStepRecommendation(
            StartStepAdvice.STEP2,
            ProjectStepKind.MOLECULE_AU_OPT,
            True,
            "Two linker-bound contact Au atoms were detected; Step 1 will be skipped.",
        )
    return StartStepRecommendation(
        StartStepAdvice.AMBIGUOUS,
        None,
        True,
        "The linker-bound Au pattern is not the supported two-contact case; choose "
        "Step 1 or Step 2 explicitly.",
    )


def create_initial_project(
    *,
    base_name: str,
    remote_directory_name: str,
    source_molecule_name: str,
    server_profile_id: UUID,
    remote_project_root: str,
    starting_step: ProjectStepKind,
    now: datetime,
    project_id: UUID | None = None,
    electrode_provenance: tuple[ProjectElectrodeClusterProvenance, ...] = (),
    restart_provenance: ProjectRestartProvenance | None = None,
    workflow_kind: CalculationWorkflowKind = CalculationWorkflowKind.FHI_AIMS_AITRANSS,
) -> CalculationProject:
    """Create revision 1 after the name/collision plan has been confirmed."""

    display_name = validate_project_base_name(base_name)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ProjectPlanningError("project creation time must be timezone-aware")
    remote_path = str(PurePosixPath(remote_project_root) / remote_directory_name)
    return CalculationProject(
        project_id=project_id or uuid4(),
        display_name=display_name,
        remote_directory_name=remote_directory_name,
        source_molecule_name=source_molecule_name,
        server_profile_id=server_profile_id,
        remote_project_path=remote_path,
        created_at=now,
        updated_at=now,
        revision=1,
        steps=initial_step_records(starting_step, workflow_kind),
        starting_step=starting_step,
        electrode_provenance=electrode_provenance,
        restart_provenance=restart_provenance,
        workflow_kind=workflow_kind,
    )
