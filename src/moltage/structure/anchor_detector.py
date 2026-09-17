"""Detect supported anchor groups from existing connectivity."""

from collections.abc import Sequence
from dataclasses import dataclass

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure


@dataclass(frozen=True, slots=True)
class PairedDicyanoGroup:
    """One activated R-C(CN)2 topology with its two explicit cyano arms."""

    center_carbon_index: int
    reference_atom_index: int
    cyano_pairs: tuple[tuple[int, int], tuple[int, int]]

    @property
    def cyano_carbon_indices(self) -> tuple[int, int]:
        return tuple(pair[0] for pair in self.cyano_pairs)

    @property
    def nitrogen_indices(self) -> tuple[int, int]:
        return tuple(pair[1] for pair in self.cyano_pairs)

    @property
    def anchor_atom_indices(self) -> tuple[int, ...]:
        return (
            self.center_carbon_index,
            *(index for pair in self.cyano_pairs for index in pair),
        )


def detect_anchors(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> tuple[AnchorCandidate, ...]:
    """Return supported anchor candidates in deterministic order."""

    if connectivity.atom_count != len(structure):
        raise ValueError(
            "connectivity atom count does not match the molecular structure: "
            f"{connectivity.atom_count} != {len(structure)}"
        )

    adjacency = _build_adjacency(connectivity)
    elements = tuple(atom.element for atom in structure)
    ncs_candidates = _detect_ncs(elements, adjacency)
    paired_dicyano_groups = _paired_dicyano_groups(elements, adjacency)
    candidates = [
        *ncs_candidates,
        *_detect_nh2(elements, adjacency),
        *_detect_pyridine_n(elements, adjacency),
        *_detect_sme(elements, adjacency),
        *_detect_alkynyl_c(elements, adjacency),
        *_detect_cyano_n(elements, adjacency),
        *_dicyano_center_candidates(
            elements,
            adjacency,
            paired_dicyano_groups,
        ),
        *_detect_sh(
            elements,
            adjacency,
            {candidate.binding_atom_index for candidate in ncs_candidates},
        ),
    ]
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.binding_atom_index,
                candidate.kind.value,
            ),
        )
    )


def paired_dicyano_groups(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> tuple[PairedDicyanoGroup, ...]:
    """Return the two activated dicyano groups, or none unless exactly two exist."""

    if connectivity.atom_count != len(structure):
        raise ValueError(
            "connectivity atom count does not match the molecular structure: "
            f"{connectivity.atom_count} != {len(structure)}"
        )
    return _paired_dicyano_groups(
        tuple(atom.element for atom in structure),
        _build_adjacency(connectivity),
    )


def paired_dicyano_group_for_anchor(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchor: AnchorCandidate,
) -> PairedDicyanoGroup | None:
    """Return an activated group containing a dicyano C or Cyano-N site."""

    for group in paired_dicyano_groups(structure, connectivity):
        if (
            anchor.kind is AnchorKind.DICYANO_C
            and anchor.binding_atom_index == group.center_carbon_index
        ) or (
            anchor.kind is AnchorKind.CYANO_N
            and anchor.binding_atom_index in group.nitrogen_indices
        ):
            return group
    return None


def _build_adjacency(connectivity: Connectivity) -> tuple[tuple[int, ...], ...]:
    neighbors: list[list[int]] = [[] for _ in range(connectivity.atom_count)]
    for bond in connectivity:
        neighbors[bond.first_index].append(bond.second_index)
        neighbors[bond.second_index].append(bond.first_index)
    return tuple(tuple(sorted(atom_neighbors)) for atom_neighbors in neighbors)


def _binding_neighbors(
    binding_atom_index: int,
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    attached_au_indices = tuple(
        atom_index
        for atom_index in adjacency[binding_atom_index]
        if elements[atom_index] == "Au"
    )
    structural_neighbors = tuple(
        atom_index
        for atom_index in adjacency[binding_atom_index]
        if elements[atom_index] != "Au"
    )
    return structural_neighbors, attached_au_indices


def _detect_ncs(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for sulfur_index, element in enumerate(elements):
        if element != "S":
            continue
        sulfur_neighbors, attached_au_indices = _binding_neighbors(
            sulfur_index,
            elements,
            adjacency,
        )
        if len(sulfur_neighbors) != 1:
            continue

        carbon_index = sulfur_neighbors[0]
        carbon_neighbors = adjacency[carbon_index]
        if elements[carbon_index] != "C" or len(carbon_neighbors) != 2:
            continue

        nitrogen_neighbors = tuple(
            atom_index
            for atom_index in carbon_neighbors
            if atom_index != sulfur_index and elements[atom_index] == "N"
        )
        if len(nitrogen_neighbors) != 1:
            continue

        nitrogen_index = nitrogen_neighbors[0]
        nitrogen_adjacency = adjacency[nitrogen_index]
        if len(nitrogen_adjacency) != 2 or carbon_index not in nitrogen_adjacency:
            continue

        backbone_index = next(
            atom_index
            for atom_index in nitrogen_adjacency
            if atom_index != carbon_index
        )
        if (
            backbone_index in {nitrogen_index, carbon_index, sulfur_index}
            or elements[backbone_index] == "H"
        ):
            continue

        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.NCS,
                binding_atom_index=sulfur_index,
                atom_indices=(nitrogen_index, carbon_index, sulfur_index),
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _detect_nh2(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for nitrogen_index, element in enumerate(elements):
        if element != "N":
            continue
        neighbors, attached_au_indices = _binding_neighbors(
            nitrogen_index,
            elements,
            adjacency,
        )
        if len(neighbors) != 3:
            continue

        hydrogen_indices = tuple(
            atom_index for atom_index in neighbors if elements[atom_index] == "H"
        )
        non_hydrogen_indices = tuple(
            atom_index for atom_index in neighbors if elements[atom_index] != "H"
        )
        if len(hydrogen_indices) != 2 or len(non_hydrogen_indices) != 1:
            continue

        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.NH2,
                binding_atom_index=nitrogen_index,
                atom_indices=(nitrogen_index, *hydrogen_indices),
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _detect_pyridine_n(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for nitrogen_index, element in enumerate(elements):
        if element != "N":
            continue
        neighbors, attached_au_indices = _binding_neighbors(
            nitrogen_index,
            elements,
            adjacency,
        )
        if (
            len(neighbors) != 2
            or any(elements[atom_index] != "C" for atom_index in neighbors)
        ):
            continue

        ring_indices = _pyridine_ring_indices(
            nitrogen_index,
            neighbors,
            elements,
            adjacency,
        )
        if ring_indices is None:
            continue

        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.PYRIDINE_N,
                binding_atom_index=nitrogen_index,
                atom_indices=ring_indices,
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _pyridine_ring_indices(
    nitrogen_index: int,
    nitrogen_neighbors: tuple[int, ...],
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> tuple[int, ...] | None:
    first_carbon_index, second_carbon_index = nitrogen_neighbors
    paths = _four_edge_carbon_paths(
        first_carbon_index,
        second_carbon_index,
        elements,
        adjacency,
    )
    if not paths:
        return None
    return min(
        tuple(sorted((nitrogen_index, *path)))
        for path in paths
    )


def _four_edge_carbon_paths(
    start_index: int,
    end_index: int,
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> tuple[tuple[int, ...], ...]:
    paths: list[tuple[int, ...]] = []

    def visit(current_index: int, path: tuple[int, ...]) -> None:
        if len(path) == 5:
            if current_index == end_index:
                paths.append(path)
            return
        if current_index == end_index:
            return

        for neighbor_index in adjacency[current_index]:
            if neighbor_index in path or elements[neighbor_index] != "C":
                continue
            visit(neighbor_index, (*path, neighbor_index))

    visit(start_index, (start_index,))
    return tuple(paths)


def _detect_sme(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for sulfur_index, element in enumerate(elements):
        if element != "S":
            continue
        neighbors, attached_au_indices = _binding_neighbors(
            sulfur_index,
            elements,
            adjacency,
        )
        if len(neighbors) != 2:
            continue

        methyl_indices = tuple(
            atom_index
            for atom_index in neighbors
            if _is_explicit_methyl_carbon(
                atom_index,
                sulfur_index,
                elements,
                adjacency,
            )
        )
        if len(methyl_indices) != 1:
            continue

        methyl_index = methyl_indices[0]
        backbone_index = next(
            atom_index for atom_index in neighbors if atom_index != methyl_index
        )
        if elements[backbone_index] == "H":
            continue
        hydrogen_indices = tuple(
            atom_index
            for atom_index in adjacency[methyl_index]
            if elements[atom_index] == "H"
        )
        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.SMe,
                binding_atom_index=sulfur_index,
                atom_indices=(sulfur_index, methyl_index, *hydrogen_indices),
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _is_explicit_methyl_carbon(
    carbon_index: int,
    sulfur_index: int,
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> bool:
    neighbors = adjacency[carbon_index]
    if elements[carbon_index] != "C" or len(neighbors) != 4:
        return False
    if sulfur_index not in neighbors:
        return False
    return all(
        elements[atom_index] == "H"
        for atom_index in neighbors
        if atom_index != sulfur_index
    )


def _detect_sh(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
    ncs_binding_atom_indices: set[int],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for sulfur_index, element in enumerate(elements):
        if element != "S":
            continue
        neighbors, attached_au_indices = _binding_neighbors(
            sulfur_index,
            elements,
            adjacency,
        )

        if len(neighbors) == 2:
            hydrogen_indices = tuple(
                atom_index
                for atom_index in neighbors
                if elements[atom_index] == "H"
            )
            non_hydrogen_indices = tuple(
                atom_index
                for atom_index in neighbors
                if elements[atom_index] != "H"
            )
            if len(hydrogen_indices) != 1 or len(non_hydrogen_indices) != 1:
                continue
            atom_indices = (sulfur_index, hydrogen_indices[0])
        elif (
            attached_au_indices
            and len(neighbors) == 1
            and elements[neighbors[0]] != "H"
            and sulfur_index not in ncs_binding_atom_indices
        ):
            atom_indices = (sulfur_index,)
        else:
            continue

        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.SH,
                binding_atom_index=sulfur_index,
                atom_indices=atom_indices,
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _detect_alkynyl_c(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for binding_index, element in enumerate(elements):
        if element != "C":
            continue
        neighbors, attached_au_indices = _binding_neighbors(
            binding_index,
            elements,
            adjacency,
        )
        carbon_indices = tuple(
            atom_index
            for atom_index in neighbors
            if elements[atom_index] == "C"
        )
        hydrogen_indices = tuple(
            atom_index
            for atom_index in neighbors
            if elements[atom_index] == "H"
        )

        if not attached_au_indices:
            if (
                len(neighbors) != 2
                or len(carbon_indices) != 1
                or len(hydrogen_indices) != 1
            ):
                continue
            atom_indices = (
                binding_index,
                carbon_indices[0],
                hydrogen_indices[0],
            )
        else:
            if (
                len(neighbors) != 1
                or len(carbon_indices) != 1
                or hydrogen_indices
            ):
                continue
            atom_indices = (binding_index, carbon_indices[0])

        adjacent_carbon_index = carbon_indices[0]
        linker_neighbors = tuple(
            atom_index
            for atom_index in adjacency[adjacent_carbon_index]
            if atom_index != binding_index
            and elements[atom_index] not in {"H", "Au"}
        )
        if not linker_neighbors:
            continue

        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.ALKYNYL_C,
                binding_atom_index=binding_index,
                atom_indices=atom_indices,
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _detect_cyano_n(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for nitrogen_index, element in enumerate(elements):
        if element != "N":
            continue
        neighbors, attached_au_indices = _binding_neighbors(
            nitrogen_index,
            elements,
            adjacency,
        )
        if len(neighbors) != 1 or elements[neighbors[0]] != "C":
            continue

        cyano_carbon_index = neighbors[0]
        carbon_neighbors = tuple(
            atom_index
            for atom_index in adjacency[cyano_carbon_index]
            if elements[atom_index] != "Au"
        )
        backbone_indices = tuple(
            atom_index
            for atom_index in carbon_neighbors
            if atom_index != nitrogen_index
            and elements[atom_index] not in {"H", "Au"}
        )
        if len(carbon_neighbors) != 2 or len(backbone_indices) != 1:
            continue

        candidates.append(
            AnchorCandidate(
                kind=AnchorKind.CYANO_N,
                binding_atom_index=nitrogen_index,
                atom_indices=(nitrogen_index, cyano_carbon_index),
                attached_au_indices=attached_au_indices,
            )
        )
    return candidates


def _paired_dicyano_groups(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> tuple[PairedDicyanoGroup, ...]:
    groups: list[PairedDicyanoGroup] = []
    for center_index, element in enumerate(elements):
        if element != "C":
            continue
        structural_neighbors, _ = _binding_neighbors(
            center_index,
            elements,
            adjacency,
        )
        if len(structural_neighbors) != 3:
            continue

        cyano_pairs = tuple(
            pair
            for neighbor_index in structural_neighbors
            if (
                pair := _cyano_pair_from_center(
                    center_index,
                    neighbor_index,
                    elements,
                    adjacency,
                )
            )
            is not None
        )
        if len(cyano_pairs) != 2:
            continue
        cyano_carbon_indices = {pair[0] for pair in cyano_pairs}
        reference_indices = tuple(
            atom_index
            for atom_index in structural_neighbors
            if atom_index not in cyano_carbon_indices
        )
        if (
            len(reference_indices) != 1
            or elements[reference_indices[0]] == "H"
        ):
            continue
        groups.append(
            PairedDicyanoGroup(
                center_carbon_index=center_index,
                reference_atom_index=reference_indices[0],
                cyano_pairs=tuple(sorted(cyano_pairs)),
            )
        )

    if len(groups) != 2:
        return ()
    return tuple(sorted(groups, key=lambda group: group.center_carbon_index))


def _cyano_pair_from_center(
    center_index: int,
    neighbor_index: int,
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
) -> tuple[int, int] | None:
    if elements[neighbor_index] != "C":
        return None
    carbon_neighbors = tuple(
        atom_index
        for atom_index in adjacency[neighbor_index]
        if elements[atom_index] != "Au"
    )
    if len(carbon_neighbors) != 2 or center_index not in carbon_neighbors:
        return None
    nitrogen_indices = tuple(
        atom_index
        for atom_index in carbon_neighbors
        if atom_index != center_index and elements[atom_index] == "N"
    )
    if len(nitrogen_indices) != 1:
        return None
    nitrogen_index = nitrogen_indices[0]
    nitrogen_neighbors, _ = _binding_neighbors(
        nitrogen_index,
        elements,
        adjacency,
    )
    if nitrogen_neighbors != (neighbor_index,):
        return None
    return neighbor_index, nitrogen_index


def _dicyano_center_candidates(
    elements: Sequence[str],
    adjacency: Sequence[tuple[int, ...]],
    groups: Sequence[PairedDicyanoGroup],
) -> tuple[AnchorCandidate, ...]:
    return tuple(
        AnchorCandidate(
            kind=AnchorKind.DICYANO_C,
            binding_atom_index=group.center_carbon_index,
            atom_indices=group.anchor_atom_indices,
            attached_au_indices=_binding_neighbors(
                group.center_carbon_index,
                elements,
                adjacency,
            )[1],
        )
        for group in groups
    )
