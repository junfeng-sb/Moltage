"""Deterministic electrode-partitioned AITRANSS self-energy preparation."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, sqrt

from moltage.aims.transport_evidence import (
    TransportEvidenceError,
    parse_fortran_real,
)
from moltage.aitranss.tcontrol import TControlSettings
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_surface import ElectrodeSurfaceProposal


AITRANSS_LAYER_TOLERANCE_ANGSTROM = 0.03
SELF_ENERGY_HEADER = "$self.energy: imaginary piece per atom"
SELF_ENERGY_END = "$end"


class SelfEnergyError(ValueError):
    """Raised when an explicit AITRANSS interface cannot be proven safe."""


class ElectrodeOwner(StrEnum):
    """Internal scientific ownership, independent of external file tokens."""

    NONE = "NONE"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


_EXTERNAL_OWNER_TOKENS = {
    ElectrodeOwner.NONE: "empty",
    ElectrodeOwner.LEFT: "left",
    ElectrodeOwner.RIGHT: "right",
}


@dataclass(frozen=True, slots=True)
class AtomSelfEnergyAssignment:
    """Internal per-atom owner, selected plane, and leakage assignment."""

    atom_index_zero_based: int
    owner: ElectrodeOwner
    plane_number: int | None
    leakage_text: str | None
    leakage: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.atom_index_zero_based, bool)
            or not isinstance(self.atom_index_zero_based, int)
            or self.atom_index_zero_based < 0
        ):
            raise SelfEnergyError("assignment atom index must be nonnegative")
        if not isinstance(self.owner, ElectrodeOwner):
            raise SelfEnergyError("assignment owner is unsupported")
        if self.owner is ElectrodeOwner.NONE:
            if (
                self.plane_number is not None
                or self.leakage_text is not None
                or self.leakage != 0.0
            ):
                raise SelfEnergyError(
                    "unassigned atoms cannot carry a plane or leakage"
                )
            return
        if (
            isinstance(self.plane_number, bool)
            or not isinstance(self.plane_number, int)
            or self.plane_number <= 0
        ):
            raise SelfEnergyError("assigned atoms require a positive plane number")
        if not isinstance(self.leakage_text, str) or not self.leakage_text.strip():
            raise SelfEnergyError("assigned atoms require leakage source text")
        if not isfinite(self.leakage) or self.leakage <= 0.0:
            raise SelfEnergyError("assigned atoms require positive leakage")

    @property
    def atom_index_one_based(self) -> int:
        return self.atom_index_zero_based + 1


@dataclass(frozen=True, slots=True)
class InterfaceLayer:
    """One selected absolute-distance plane and its atom membership."""

    plane_number: int
    distance_angstrom: float
    global_zero_based: tuple[int, ...]
    template_local_indices: tuple[int | None, ...]
    leakage_text: str
    leakage: float

    def __post_init__(self) -> None:
        if self.plane_number <= 0:
            raise SelfEnergyError("interface plane number must be positive")
        if not isfinite(self.distance_angstrom) or self.distance_angstrom < 0.0:
            raise SelfEnergyError("interface plane distance must be finite and nonnegative")
        if not self.global_zero_based:
            raise SelfEnergyError("an interface plane must contain at least one atom")
        if len(self.global_zero_based) != len(self.template_local_indices):
            raise SelfEnergyError("global and template-local memberships disagree")
        if tuple(sorted(self.global_zero_based)) != self.global_zero_based:
            raise SelfEnergyError("interface membership must follow geometry order")
        if not isfinite(self.leakage) or self.leakage <= 0.0:
            raise SelfEnergyError("selected interface leakage must be positive")

    @property
    def global_one_based(self) -> tuple[int, ...]:
        return tuple(index + 1 for index in self.global_zero_based)


@dataclass(frozen=True, slots=True)
class ElectrodeInterfaceSelection:
    """Selected layers for one explicitly owned electrode population."""

    side: str
    candidate_global_zero_based: tuple[int, ...]
    layers: tuple[InterfaceLayer, ...]

    def __post_init__(self) -> None:
        if self.side not in {"left", "right", "legacy"}:
            raise SelfEnergyError("interface side is unsupported")
        candidates = tuple(self.candidate_global_zero_based)
        if len(set(candidates)) != len(candidates):
            raise SelfEnergyError("electrode candidate membership contains duplicates")
        selected = self.selected_global_zero_based
        if not set(selected) <= set(candidates):
            raise SelfEnergyError("selected atoms are outside the electrode candidates")
        if len(set(selected)) != len(selected):
            raise SelfEnergyError("an electrode atom was selected more than once")

    @property
    def selected_global_zero_based(self) -> tuple[int, ...]:
        return tuple(index for layer in self.layers for index in layer.global_zero_based)

    @property
    def selected_count(self) -> int:
        return sum(len(layer.global_zero_based) for layer in self.layers)


@dataclass(frozen=True, slots=True)
class PartitionedSelfEnergyPlan:
    """Disjoint atom-specific left/right reservoir assignment."""

    structure: MolecularStructure
    left: ElectrodeInterfaceSelection
    right: ElectrodeInterfaceSelection
    tolerance_angstrom: float = AITRANSS_LAYER_TOLERANCE_ANGSTROM

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("self-energy plan requires a MolecularStructure")
        if self.left.side != "left" or self.right.side != "right":
            raise SelfEnergyError("partitioned plan requires left and right selections")
        if set(self.left.candidate_global_zero_based) & set(
            self.right.candidate_global_zero_based
        ):
            raise SelfEnergyError("left/right electrode provenance overlaps")
        if set(self.left.selected_global_zero_based) & set(
            self.right.selected_global_zero_based
        ):
            raise SelfEnergyError("left/right selected interfaces overlap")
        if (
            not isfinite(self.tolerance_angstrom)
            or self.tolerance_angstrom <= 0.0
        ):
            raise SelfEnergyError("layer-plane tolerance must be positive")

    @property
    def unassigned_count(self) -> int:
        return (
            len(self.structure)
            - self.left.selected_count
            - self.right.selected_count
        )

    @property
    def atom_assignments(self) -> tuple[AtomSelfEnergyAssignment, ...]:
        """Return complete internal ownership without exposing file tokens."""

        selected: dict[int, AtomSelfEnergyAssignment] = {}
        for selection, owner in (
            (self.left, ElectrodeOwner.LEFT),
            (self.right, ElectrodeOwner.RIGHT),
        ):
            for layer in selection.layers:
                for index in layer.global_zero_based:
                    if index in selected:
                        raise SelfEnergyError(
                            "an atom has more than one reservoir assignment"
                        )
                    selected[index] = AtomSelfEnergyAssignment(
                        atom_index_zero_based=index,
                        owner=owner,
                        plane_number=layer.plane_number,
                        leakage_text=layer.leakage_text,
                        leakage=layer.leakage,
                    )
        return tuple(
            selected.get(
                atom.index,
                AtomSelfEnergyAssignment(
                    atom_index_zero_based=atom.index,
                    owner=ElectrodeOwner.NONE,
                    plane_number=None,
                    leakage_text=None,
                    leakage=0.0,
                ),
            )
            for atom in self.structure
        )


@dataclass(frozen=True, slots=True)
class SelfEnergyRow:
    atom_index_one_based: int
    x: float
    y: float
    z: float
    element: str
    reservoir: str | None
    leakage: float


@dataclass(frozen=True, slots=True)
class ParsedSelfEnergy:
    rows: tuple[SelfEnergyRow, ...]


@dataclass(frozen=True, slots=True)
class SelfEnergyCompatibilityReport:
    """Independent token/round-trip evidence for one rendered input."""

    row_count: int
    empty_count: int
    left_count: int
    right_count: int


def select_interface_layers(
    structure: MolecularStructure,
    candidate_global_zero_based: tuple[int, ...],
    surface_zero_based: tuple[int, int, int],
    *,
    nlayers: int,
    leakage_texts: tuple[str, str, str],
    side: str = "legacy",
    local_to_global_zero_based: tuple[int, ...] | None = None,
    tolerance_angstrom: float = AITRANSS_LAYER_TOLERANCE_ANGSTROM,
) -> ElectrodeInterfaceSelection:
    """Reproduce absolute-distance plane grouping for one bounded candidate set."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("layer selection requires a MolecularStructure")
    candidates = tuple(candidate_global_zero_based)
    if not candidates or len(set(candidates)) != len(candidates):
        raise SelfEnergyError("candidate atoms must be a nonempty unique sequence")
    if tuple(sorted(candidates)) != candidates:
        raise SelfEnergyError("candidate atoms must follow geometry order")
    if any(index < 0 or index >= len(structure) for index in candidates):
        raise SelfEnergyError("candidate atom is outside geometry")
    if any(structure[index].element != "Au" for index in candidates):
        raise SelfEnergyError("electrode candidates must all be Au")
    if len(set(surface_zero_based)) != 3 or not set(surface_zero_based) <= set(candidates):
        raise SelfEnergyError("surface triplet must be three distinct candidate atoms")
    if isinstance(nlayers, bool) or not isinstance(nlayers, int) or nlayers <= 0:
        raise SelfEnergyError("nlayers must be a positive integer")
    if not isfinite(tolerance_angstrom) or tolerance_angstrom <= 0.0:
        raise SelfEnergyError("layer-plane tolerance must be positive")
    if len(leakage_texts) != 3:
        raise SelfEnergyError("s1i/s2i/s3i are required")
    leakages = tuple(
        parse_fortran_real(value, field_name=name)
        for value, name in zip(leakage_texts, ("s1i", "s2i", "s3i"), strict=True)
    )
    if any(value <= 0.0 for value in leakages):
        raise SelfEnergyError("s1i/s2i/s3i must be positive")

    normal = _unit_plane_normal(structure, surface_zero_based)
    origin = structure[surface_zero_based[0]]
    distances = {
        index: abs(
            (structure[index].x - origin.x) * normal[0]
            + (structure[index].y - origin.y) * normal[1]
            + (structure[index].z - origin.z) * normal[2]
        )
        for index in candidates
    }
    remaining = set(candidates)
    groups: list[tuple[float, tuple[int, ...]]] = []
    while remaining and len(groups) < nlayers:
        representative = min(distances[index] for index in remaining)
        members = tuple(
            index
            for index in candidates
            if index in remaining
            and abs(distances[index] - representative) <= tolerance_angstrom
        )
        if not members:
            raise AssertionError("plane grouping failed to consume a candidate")
        groups.append((representative, members))
        remaining.difference_update(members)
    if len(groups) != nlayers:
        raise SelfEnergyError(
            f"{side} electrode contains only {len(groups)} distinct planes; "
            f"nlayers={nlayers}"
        )

    local_by_global = (
        {global_index: local for local, global_index in enumerate(local_to_global_zero_based)}
        if local_to_global_zero_based is not None
        else {}
    )
    layers = tuple(
        InterfaceLayer(
            plane_number=plane_number,
            distance_angstrom=distance,
            global_zero_based=members,
            template_local_indices=tuple(local_by_global.get(index) for index in members),
            leakage_text=(
                leakage_texts[0]
                if plane_number == 1
                else leakage_texts[1]
                if plane_number == 2
                else leakage_texts[2]
            ),
            leakage=(
                leakages[0]
                if plane_number == 1
                else leakages[1]
                if plane_number == 2
                else leakages[2]
            ),
        )
        for plane_number, (distance, members) in enumerate(groups, start=1)
    )
    return ElectrodeInterfaceSelection(side, candidates, layers)


def build_partitioned_self_energy_plan(
    structure: MolecularStructure,
    surface: ElectrodeSurfaceProposal,
    settings: TControlSettings,
    *,
    tolerance_angstrom: float = AITRANSS_LAYER_TOLERANCE_ANGSTROM,
) -> PartitionedSelfEnergyPlan:
    """Select the same planes after partitioning candidates by Phase-2D provenance."""

    if surface.structure != structure:
        raise SelfEnergyError("surface proposal does not belong to this geometry")
    leakage_texts = (settings.s1i, settings.s2i, settings.s3i)
    left_candidates = tuple(sorted(surface.left_electrode_zero_based))
    right_candidates = tuple(sorted(surface.right_electrode_zero_based))
    left = select_interface_layers(
        structure,
        left_candidates,
        surface.left_zero_based,
        nlayers=settings.nlayers,
        leakage_texts=leakage_texts,
        side="left",
        local_to_global_zero_based=surface.left_local_to_global_zero_based,
        tolerance_angstrom=tolerance_angstrom,
    )
    right = select_interface_layers(
        structure,
        right_candidates,
        surface.right_zero_based,
        nlayers=settings.nlayers,
        leakage_texts=leakage_texts,
        side="right",
        local_to_global_zero_based=surface.right_local_to_global_zero_based,
        tolerance_angstrom=tolerance_angstrom,
    )
    return PartitionedSelfEnergyPlan(structure, left, right, tolerance_angstrom)


def render_self_energy(plan: PartitionedSelfEnergyPlan) -> bytes:
    """Render the installed-reader-compatible seven-token atom table."""

    if not isinstance(plan, PartitionedSelfEnergyPlan):
        raise TypeError("self-energy rendering requires a partitioned plan")
    lines = [SELF_ENERGY_HEADER]
    for atom, assignment in zip(
        plan.structure,
        plan.atom_assignments,
        strict=True,
    ):
        owner_token = _EXTERNAL_OWNER_TOKENS[assignment.owner]
        coordinate_fields = "".join(
            f"{coordinate:15.7f}" for coordinate in (atom.x, atom.y, atom.z)
        )
        leakage_field = f"{assignment.leakage:20.14E}".replace("E", "D")
        line = (
            f"{atom.index + 1:5d} {coordinate_fields}"
            f"    {atom.element.casefold():>2}    {owner_token:>5}    "
            f"{leakage_field}"
        )
        if len(line) != 90 or len(line.split()) != 7:
            raise SelfEnergyError(
                "self-energy row does not match the installed seven-token format"
            )
        lines.append(line)
    lines.append(SELF_ENERGY_END)
    rendered = ("\n".join(lines) + "\n").encode("ascii")
    validate_self_energy_round_trip(rendered, plan)
    return rendered


def parse_self_energy(
    text: bytes | str,
    structure: MolecularStructure | None = None,
) -> ParsedSelfEnergy:
    """Parse the deterministic subset accepted by the installed reader."""

    if isinstance(text, bytes):
        try:
            decoded = text.decode("ascii")
        except UnicodeError:
            raise SelfEnergyError("self-energy file must be ASCII") from None
    elif isinstance(text, str):
        decoded = text
    else:
        raise TypeError("self-energy input must be bytes or text")
    if not decoded.endswith("\n"):
        raise SelfEnergyError("self-energy file must end with a newline")
    lines = decoded.splitlines()
    if (
        len(lines) < 3
        or lines[0] != SELF_ENERGY_HEADER
        or lines[-1] != SELF_ENERGY_END
    ):
        raise SelfEnergyError("self-energy header or final $end is invalid")
    rows: list[SelfEnergyRow] = []
    for expected_index, line in enumerate(lines[1:-1], start=1):
        if len(line) > 120:
            raise SelfEnergyError("self-energy atom rows may not exceed 120 characters")
        fields = line.split()
        if len(fields) != 7:
            raise SelfEnergyError(
                "self-energy atom rows require exactly seven tokens, including "
                "empty/left/right ownership"
            )
        try:
            atom_index = int(fields[0])
            coordinates = tuple(
                parse_fortran_real(value, field_name="self-energy coordinate")
                for value in fields[1:4]
            )
            leakage = parse_fortran_real(
                fields[6],
                field_name="self-energy leakage",
            )
        except (TransportEvidenceError, ValueError, TypeError):
            raise SelfEnergyError("self-energy row contains invalid numeric text") from None
        if atom_index != expected_index:
            raise SelfEnergyError("self-energy atom indices must be exact and ordered")
        element = fields[4]
        owner_token = fields[5]
        if owner_token not in {"empty", "left", "right"}:
            raise SelfEnergyError("self-energy ownership token is invalid")
        reservoir = None if owner_token == "empty" else owner_token
        if not isfinite(leakage) or leakage < 0.0:
            raise SelfEnergyError("self-energy leakage must be finite and nonnegative")
        if reservoir is None and leakage != 0.0:
            raise SelfEnergyError("unassigned atoms must have zero leakage")
        if reservoir is not None and leakage <= 0.0:
            raise SelfEnergyError("reservoir-assigned atoms must have positive leakage")
        row = SelfEnergyRow(atom_index, *coordinates, element, reservoir, leakage)
        if structure is not None:
            if len(lines) - 2 != len(structure):
                raise SelfEnergyError("self-energy row count does not match geometry")
            atom = structure[expected_index - 1]
            if element.casefold() != atom.element.casefold() or any(
                parsed != round(actual, 7)
                for parsed, actual in zip(coordinates, (atom.x, atom.y, atom.z), strict=True)
            ):
                raise SelfEnergyError(
                    "self-energy atom coordinates/elements do not match geometry"
                )
        rows.append(row)
    return ParsedSelfEnergy(tuple(rows))


def validate_self_energy_round_trip(
    text: bytes | str,
    plan: PartitionedSelfEnergyPlan,
) -> SelfEnergyCompatibilityReport:
    """Prove seven-token grammar and exact internal owner/eta preservation."""

    if not isinstance(plan, PartitionedSelfEnergyPlan):
        raise TypeError("self-energy validation requires a partitioned plan")
    if isinstance(text, bytes):
        try:
            decoded = text.decode("ascii")
        except UnicodeError:
            raise SelfEnergyError("self-energy file must be ASCII") from None
    elif isinstance(text, str):
        decoded = text
    else:
        raise TypeError("self-energy input must be bytes or text")
    if "\r" in decoded or not decoded.endswith("\n"):
        raise SelfEnergyError("self-energy file must use LF and end with a newline")
    lines = decoded.splitlines()
    if (
        len(lines) < 3
        or lines[0] != SELF_ENERGY_HEADER
        or lines[-1] != SELF_ENERGY_END
    ):
        raise SelfEnergyError("self-energy header or final $end is invalid")
    atom_lines = lines[1:-1]
    if len(atom_lines) != len(plan.structure):
        raise SelfEnergyError("self-energy row count does not match geometry")

    token_rows: list[tuple[str, ...]] = []
    atom_indices: list[int] = []
    owner_counts = {"empty": 0, "left": 0, "right": 0}
    for line in atom_lines:
        fields = tuple(line.split())
        if len(fields) != 7:
            raise SelfEnergyError(
                "self-energy atom rows require exactly seven tokens, including "
                "empty/left/right ownership"
            )
        try:
            atom_index = int(fields[0])
            numeric = tuple(
                parse_fortran_real(value, field_name="self-energy compatibility")
                for value in (*fields[1:4], fields[6])
            )
        except (TransportEvidenceError, ValueError, TypeError):
            raise SelfEnergyError("self-energy row contains invalid numeric text") from None
        if not all(isfinite(value) for value in numeric):
            raise SelfEnergyError("self-energy row contains non-finite numeric text")
        owner = fields[5]
        if owner not in owner_counts:
            raise SelfEnergyError("self-energy ownership token is invalid")
        atom_indices.append(atom_index)
        owner_counts[owner] += 1
        token_rows.append(fields)
    if atom_indices != list(range(1, len(plan.structure) + 1)):
        raise SelfEnergyError(
            "self-energy atom indices must be unique and exactly 1..N"
        )

    parsed = parse_self_energy(decoded, plan.structure)
    assignments = plan.atom_assignments
    for fields, row, assignment in zip(
        token_rows,
        parsed.rows,
        assignments,
        strict=True,
    ):
        expected_owner = _EXTERNAL_OWNER_TOKENS[assignment.owner]
        expected_reservoir = None if expected_owner == "empty" else expected_owner
        atom = plan.structure[assignment.atom_index_zero_based]
        if (
            row.atom_index_one_based != assignment.atom_index_one_based
            or fields[5] != expected_owner
            or row.reservoir != expected_reservoir
            or row.leakage != assignment.leakage
            or row.element.casefold() != atom.element.casefold()
            or (row.x, row.y, row.z)
            != tuple(round(value, 7) for value in (atom.x, atom.y, atom.z))
        ):
            raise SelfEnergyError(
                "self-energy round trip changed atom, ownership, or leakage data"
            )

    report = SelfEnergyCompatibilityReport(
        row_count=len(token_rows),
        empty_count=owner_counts["empty"],
        left_count=owner_counts["left"],
        right_count=owner_counts["right"],
    )
    if (
        report.empty_count != plan.unassigned_count
        or report.left_count != plan.left.selected_count
        or report.right_count != plan.right.selected_count
    ):
        raise SelfEnergyError(
            "self-energy external owner counts disagree with the internal plan"
        )
    return report


def _unit_plane_normal(
    structure: MolecularStructure,
    surface: tuple[int, int, int],
) -> tuple[float, float, float]:
    c, x, y = (structure[index] for index in surface)
    cx = (x.x - c.x, x.y - c.y, x.z - c.z)
    cy = (y.x - c.x, y.y - c.y, y.z - c.z)
    cross = (
        cx[1] * cy[2] - cx[2] * cy[1],
        cx[2] * cy[0] - cx[0] * cy[2],
        cx[0] * cy[1] - cx[1] * cy[0],
    )
    norm = sqrt(sum(value * value for value in cross))
    if norm <= 1.0e-12:
        raise SelfEnergyError("surface triplet is collinear")
    return tuple(value / norm for value in cross)
