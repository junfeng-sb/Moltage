"""Authoritative AITRANSS surface mapping from project electrode provenance."""

from dataclasses import dataclass
from math import dist, hypot

from moltage.domain.au_pyramid import (
    AU_PYRAMID_SPACING_ANGSTROM,
    AU_PYRAMID_GEOMETRY_MODEL,
    generate_au_pyramid,
)
from moltage.domain.au_lattice_extension import (
    AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM,
)
from moltage.domain.calculation_project import (
    CalculationProject,
    ProjectElectrodeAtomIdentity,
    ProjectElectrodeClusterProvenance,
)
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_lattice_extension import (
    ElectrodeLatticeExtensionError,
    validate_persisted_lattice_extensions,
)


LEGACY_AU59_MODEL = "LegacyAu59V1"
GENERATED_RIGID_TOLERANCE_ANGSTROM = (
    AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM
)
LEGACY_RIGID_TOLERANCE_ANGSTROM = 5.0e-3


class ElectrodeSurfaceError(ValueError):
    """Raised when current geometry and persisted electrode identity disagree."""


@dataclass(frozen=True, slots=True)
class ElectrodeSurfaceProposal:
    """Reference planes and complete validated two-side electrode membership."""

    structure: MolecularStructure
    source_atom_count: int
    pyramid_layers: int
    left_zero_based: tuple[int, int, int]
    right_zero_based: tuple[int, int, int]
    left_provenance: ProjectElectrodeClusterProvenance
    right_provenance: ProjectElectrodeClusterProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("surface proposal requires a MolecularStructure")
        if self.source_atom_count <= 0 or self.source_atom_count >= len(self.structure):
            raise ElectrodeSurfaceError("surface proposal source atom count is invalid")
        if self.pyramid_layers < 2 or self.pyramid_layers > 10:
            raise ElectrodeSurfaceError("surface proposal pyramid layer count is invalid")
        if self.left_provenance.side != "LEFT" or self.right_provenance.side != "RIGHT":
            raise ElectrodeSurfaceError("surface proposal requires LEFT and RIGHT provenance")
        all_surface = self.left_zero_based + self.right_zero_based
        if len(set(all_surface)) != 6:
            raise ElectrodeSurfaceError("Left and Right surface atoms must be distinct")
        if any(index < 0 or index >= len(self.structure) for index in all_surface):
            raise ElectrodeSurfaceError("surface atom index is outside geometry.in")
        if any(self.structure[index].element != "Au" for index in all_surface):
            raise ElectrodeSurfaceError("every proposed surface atom must be Au")
        _require_non_collinear(self.structure, self.left_zero_based, "Left")
        _require_non_collinear(self.structure, self.right_zero_based, "Right")

    @property
    def left_one_based(self) -> tuple[int, int, int]:
        return tuple(index + 1 for index in self.left_zero_based)

    @property
    def right_one_based(self) -> tuple[int, int, int]:
        return tuple(index + 1 for index in self.right_zero_based)

    @property
    def labels(self) -> tuple[tuple[int, str], ...]:
        return tuple((index, f"L {index + 1}") for index in self.left_zero_based) + tuple(
            (index, f"R {index + 1}") for index in self.right_zero_based
        )

    @property
    def left_local_to_global_zero_based(self) -> tuple[int, ...]:
        return self.left_provenance.local_to_global_indices

    @property
    def right_local_to_global_zero_based(self) -> tuple[int, ...]:
        return self.right_provenance.local_to_global_indices

    @property
    def left_electrode_zero_based(self) -> tuple[int, ...]:
        return _electrode_global_indices(self.left_provenance)

    @property
    def right_electrode_zero_based(self) -> tuple[int, ...]:
        return _electrode_global_indices(self.right_provenance)

    @property
    def left_atom_identities(self) -> tuple[ProjectElectrodeAtomIdentity, ...]:
        return self.left_provenance.atom_identities

    @property
    def right_atom_identities(self) -> tuple[ProjectElectrodeAtomIdentity, ...]:
        return self.right_provenance.atom_identities


def resolve_project_electrode_surfaces(
    project: CalculationProject,
    structure: MolecularStructure,
) -> ElectrodeSurfaceProposal:
    """Use authoritative schema-8 metadata or a bounded legacy recovery adapter."""

    if not isinstance(project, CalculationProject):
        raise TypeError("surface resolution requires a CalculationProject")
    if project.electrode_provenance:
        provenance = project.electrode_provenance
    elif project.legacy_electrode_recovery_allowed:
        provenance = recover_legacy_normal_electrode_provenance(structure)
    else:
        raise ElectrodeSurfaceError(
            "project has no authoritative electrode provenance; rebuild the electrode "
            "geometry before a mapping-dependent operation"
        )
    return propose_electrode_surfaces(structure, provenance)


def propose_electrode_surfaces(
    structure: MolecularStructure,
    provenance: tuple[
        ProjectElectrodeClusterProvenance, ProjectElectrodeClusterProvenance
    ]
    | tuple[ProjectElectrodeClusterProvenance, ...],
) -> ElectrodeSurfaceProposal:
    """Validate explicit side mappings and resolve immutable corner identities."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("surface mapping requires a MolecularStructure")
    records = tuple(provenance)
    if len(records) != 2 or any(
        not isinstance(item, ProjectElectrodeClusterProvenance) for item in records
    ):
        raise ElectrodeSurfaceError("surface mapping requires exactly two provenance records")
    if tuple(item.side for item in records) != ("LEFT", "RIGHT"):
        raise ElectrodeSurfaceError("electrode provenance must use LEFT then RIGHT order")
    if len({item.pyramid_layers for item in records}) != 1:
        raise ElectrodeSurfaceError("left/right pyramid layer counts disagree")
    if len({item.geometry_model for item in records}) != 1:
        raise ElectrodeSurfaceError("left/right electrode geometry models disagree")

    non_apex_count = sum(
        len(item.local_to_global_indices) - 1 + len(item.lattice_extensions)
        for item in records
    )
    source_count = len(structure) - non_apex_count
    if source_count <= 0:
        raise ElectrodeSurfaceError("geometry is too small for persisted electrode mappings")
    expected_appended = set(range(source_count, len(structure)))
    mapped_appended: set[int] = set()
    for record in records:
        _validate_record_against_structure(structure, source_count, record)
        apex_local = _local_index_for_key(record, record.apex_lattice_key)
        mapped_appended.update(
            global_index
            for local_index, global_index in enumerate(record.local_to_global_indices)
            if local_index != apex_local
        )
        mapped_appended.update(
            extension.global_atom_index
            for extension in record.lattice_extensions
        )
    if mapped_appended != expected_appended:
        raise ElectrodeSurfaceError(
            "electrode mapping does not exactly cover the generated geometry suffix"
        )
    membership_sets = tuple(
        set(record.local_to_global_indices)
        | {
            extension.global_atom_index
            for extension in record.lattice_extensions
        }
        for record in records
    )
    if membership_sets[0] & membership_sets[1]:
        raise ElectrodeSurfaceError("Left and Right electrode provenance overlaps")
    try:
        validate_persisted_lattice_extensions(structure, records)
    except ElectrodeLatticeExtensionError as error:
        raise ElectrodeSurfaceError(str(error)) from error

    left = _corner_global_indices(records[0])
    right = _corner_global_indices(records[1])
    return ElectrodeSurfaceProposal(
        structure=structure,
        source_atom_count=source_count,
        pyramid_layers=records[0].pyramid_layers,
        left_zero_based=left,
        right_zero_based=right,
        left_provenance=records[0],
        right_provenance=records[1],
    )


def recover_legacy_normal_electrode_provenance(
    structure: MolecularStructure,
) -> tuple[ProjectElectrodeClusterProvenance, ProjectElectrodeClusterProvenance]:
    """Recover historical 58+58 ordering only when both apex matches are unique."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("legacy recovery requires a MolecularStructure")
    source_count = len(structure) - 116
    if source_count <= 0:
        raise ElectrodeSurfaceError(
            "legacy electrode mapping cannot be recovered: historical 58+58 suffix "
            "is absent"
        )
    canonical = generate_au_pyramid(6)
    records: list[ProjectElectrodeClusterProvenance] = []
    used_apexes: set[int] = set()
    for side, offset in (("LEFT", source_count), ("RIGHT", source_count + 58)):
        appended = tuple(range(offset, offset + 58))
        if any(structure[index].element != "Au" for index in appended):
            raise ElectrodeSurfaceError(
                "legacy electrode mapping cannot be recovered: suffix contains non-Au atoms"
            )
        _validate_ordered_standard_geometry(
            structure,
            (None, *appended[:55]),
            canonical,
            tolerance=LEGACY_RIGID_TOLERANCE_ANGSTROM,
            compare_apex=False,
        )
        candidates = tuple(
            index
            for index in range(source_count)
            if structure[index].element == "Au"
            and index not in used_apexes
            and _candidate_matches_legacy_apex(
                structure,
                index,
                appended[:55],
                canonical,
            )
        )
        if len(candidates) != 1:
            raise ElectrodeSurfaceError(
                f"legacy {side.casefold()} contact/apex mapping is not unique; "
                "mapping-dependent operation is unavailable"
            )
        apex = candidates[0]
        used_apexes.add(apex)
        records.append(
            _legacy_record(side, (apex, *appended))
        )
    return records[0], records[1]


def _legacy_record(
    side: str,
    mapping: tuple[int, ...],
) -> ProjectElectrodeClusterProvenance:
    canonical = generate_au_pyramid(6)
    identities = tuple(
        ProjectElectrodeAtomIdentity(
            local_index=index,
            layer_index=(canonical.atom_identities[index].layer_index if index < 56 else None),
            lattice_key=(canonical.atom_identities[index].lattice_key if index < 56 else None),
            standard_pyramid_member=index < 56,
        )
        for index in range(59)
    )
    return ProjectElectrodeClusterProvenance(
        side=side,
        geometry_model=LEGACY_AU59_MODEL,
        pyramid_layers=6,
        nearest_neighbor_spacing_angstrom=AU_PYRAMID_SPACING_ANGSTROM,
        roll_degrees=None,
        atom_identities=identities,
        local_to_global_indices=mapping,
        apex_lattice_key=(0, 0, 0),
        reference_corner_lattice_keys=((0, 0, 5), (0, 5, 0), (5, 0, 0)),
    )


def _validate_record_against_structure(
    structure: MolecularStructure,
    source_count: int,
    record: ProjectElectrodeClusterProvenance,
) -> None:
    mapping = record.local_to_global_indices
    if any(index < 0 or index >= len(structure) for index in mapping):
        raise ElectrodeSurfaceError(f"{record.side} electrode mapping is outside geometry.in")
    if len(set(mapping)) != len(mapping):
        raise ElectrodeSurfaceError(f"{record.side} electrode mapping contains duplicates")
    if any(structure[index].element != "Au" for index in mapping):
        raise ElectrodeSurfaceError(f"{record.side} electrode mapping contains a non-Au atom")
    extension_indices = tuple(
        extension.global_atom_index for extension in record.lattice_extensions
    )
    if any(index < 0 or index >= len(structure) for index in extension_indices):
        raise ElectrodeSurfaceError(
            f"{record.side} lattice extension mapping is outside geometry.in"
        )
    if any(structure[index].element != "Au" for index in extension_indices):
        raise ElectrodeSurfaceError(
            f"{record.side} lattice extension mapping contains a non-Au atom"
        )
    apex_local = _local_index_for_key(record, record.apex_lattice_key)
    if mapping[apex_local] >= source_count:
        raise ElectrodeSurfaceError(
            f"{record.side} contact/apex Au does not belong to the source structure"
        )
    if record.geometry_model not in {AU_PYRAMID_GEOMETRY_MODEL, LEGACY_AU59_MODEL}:
        raise ElectrodeSurfaceError(
            f"{record.side} electrode geometry model is unsupported"
        )
    canonical = generate_au_pyramid(record.pyramid_layers)
    standard = tuple(
        (identity, mapping[identity.local_index])
        for identity in record.atom_identities
        if identity.standard_pyramid_member
    )
    canonical_by_key = {
        identity.lattice_key: identity.local_index
        for identity in canonical.atom_identities
    }
    if set(identity.lattice_key for identity, _ in standard) != set(canonical_by_key):
        raise ElectrodeSurfaceError(
            f"{record.side} standard pyramid lattice identities are incomplete"
        )
    tolerance = (
        GENERATED_RIGID_TOLERANCE_ANGSTROM
        if record.geometry_model == AU_PYRAMID_GEOMETRY_MODEL
        else LEGACY_RIGID_TOLERANCE_ANGSTROM
    )
    for offset, (first_identity, first_global) in enumerate(standard):
        first_reference = canonical.structure[canonical_by_key[first_identity.lattice_key]]
        for second_identity, second_global in standard[offset + 1 :]:
            second_reference = canonical.structure[
                canonical_by_key[second_identity.lattice_key]
            ]
            actual = dist(_coordinates(structure[first_global]), _coordinates(structure[second_global]))
            expected = dist(_coordinates(first_reference), _coordinates(second_reference))
            if abs(actual - expected) > tolerance:
                raise ElectrodeSurfaceError(
                    f"{record.side} electrode atom order/geometry conflicts with "
                    "persisted lattice provenance"
                )
    _corner_global_indices(record)


def _validate_ordered_standard_geometry(
    structure: MolecularStructure,
    mapping: tuple[int | None, ...],
    canonical: object,
    *,
    tolerance: float,
    compare_apex: bool,
) -> None:
    start = 0 if compare_apex else 1
    for first in range(start, 56):
        if mapping[first] is None:
            continue
        for second in range(first + 1, 56):
            if mapping[second] is None:
                continue
            actual = dist(
                _coordinates(structure[mapping[first]]),
                _coordinates(structure[mapping[second]]),
            )
            expected = dist(
                _coordinates(canonical.structure[first]),
                _coordinates(canonical.structure[second]),
            )
            if abs(actual - expected) > tolerance:
                raise ElectrodeSurfaceError(
                    "legacy electrode ordering/geometry does not match the bounded Au56 core"
                )


def _candidate_matches_legacy_apex(
    structure: MolecularStructure,
    candidate: int,
    appended_standard: tuple[int, ...],
    canonical: object,
) -> bool:
    return all(
        abs(
            dist(_coordinates(structure[candidate]), _coordinates(structure[global_index]))
            - dist(_coordinates(canonical.structure[0]), _coordinates(canonical.structure[local_index]))
        )
        <= LEGACY_RIGID_TOLERANCE_ANGSTROM
        for local_index, global_index in enumerate(appended_standard, start=1)
    )


def _local_index_for_key(
    record: ProjectElectrodeClusterProvenance,
    key: tuple[int, int, int],
) -> int:
    matches = tuple(
        identity.local_index
        for identity in record.atom_identities
        if identity.standard_pyramid_member and identity.lattice_key == key
    )
    if len(matches) != 1:
        raise ElectrodeSurfaceError(
            f"{record.side} electrode lattice identity {key!r} is not unique"
        )
    return matches[0]


def _corner_global_indices(
    record: ProjectElectrodeClusterProvenance,
) -> tuple[int, int, int]:
    return tuple(
        record.local_to_global_indices[_local_index_for_key(record, key)]
        for key in record.reference_corner_lattice_keys
    )


def _electrode_global_indices(
    record: ProjectElectrodeClusterProvenance,
) -> tuple[int, ...]:
    standard = tuple(
        record.local_to_global_indices[identity.local_index]
        for identity in record.atom_identities
        if identity.standard_pyramid_member
    )
    if record.geometry_model == LEGACY_AU59_MODEL:
        return standard
    return standard + tuple(
        extension.global_atom_index for extension in record.lattice_extensions
    )


def _require_non_collinear(
    structure: MolecularStructure,
    indices: tuple[int, int, int],
    side: str,
) -> None:
    first, second, third = (_coordinates(structure[index]) for index in indices)
    ab = tuple(b - a for a, b in zip(first, second, strict=True))
    ac = tuple(c - a for a, c in zip(first, third, strict=True))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    if hypot(*cross) <= 1.0e-10:
        raise ElectrodeSurfaceError(f"{side} surface triplet is collinear")


def _coordinates(atom: object) -> tuple[float, float, float]:
    return atom.x, atom.y, atom.z
