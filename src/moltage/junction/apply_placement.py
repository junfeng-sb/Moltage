"""Apply accepted virtual-Au proposals without recalculating their geometry."""

from collections.abc import Sequence
from math import dist

from moltage.domain.anchor import AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.junction import AppliedAuPlacement, AuPlacementProposal
from moltage.domain.structure import Atom, MolecularStructure


class AuPlacementApplicationError(ValueError):
    """Raised when accepted proposals cannot form one atomic transformation."""


def apply_au_placements(
    structure: MolecularStructure,
    connectivity: Connectivity,
    proposals: Sequence[AuPlacementProposal],
) -> AppliedAuPlacement:
    """Apply one or two proposals simultaneously using their exact coordinates."""

    _validate_source(structure, connectivity)
    requested = tuple(proposals)
    if len(requested) not in {1, 2}:
        raise AuPlacementApplicationError(
            "Au application requires exactly one or two proposals"
        )
    if any(not isinstance(item, AuPlacementProposal) for item in requested):
        raise TypeError("proposals must contain AuPlacementProposal instances")

    ordered = tuple(
        sorted(
            requested,
            key=lambda proposal: (
                proposal.anchor.binding_atom_index,
                proposal.anchor.kind.value,
            ),
        )
    )
    binding_indices = tuple(
        proposal.anchor.binding_atom_index for proposal in ordered
    )
    if len(set(binding_indices)) != len(binding_indices):
        raise AuPlacementApplicationError(
            "proposals must target unique binding atom indexes"
        )

    for proposal in ordered:
        _validate_proposal_indices(proposal, len(structure))
        if proposal.anchor.attached_au_indices:
            raise AuPlacementApplicationError(
                f"{proposal.anchor.kind.value} binding atom "
                f"{proposal.anchor.binding_atom_index} already has attached Au"
            )
        _validate_terminal_hydrogen_removal(
            structure,
            connectivity,
            proposal,
        )
    _validate_cross_proposal_removals(ordered)

    removed = tuple(
        sorted(
            {
                atom_index
                for proposal in ordered
                for atom_index in proposal.remove_atom_indices
            }
        )
    )
    removed_set = set(removed)
    mapping: list[int | None] = []
    transformed_atoms: list[Atom] = []
    for source_atom in structure:
        if source_atom.index in removed_set:
            mapping.append(None)
            continue
        new_index = len(transformed_atoms)
        mapping.append(new_index)
        transformed_atoms.append(
            Atom(
                new_index,
                source_atom.element,
                source_atom.x,
                source_atom.y,
                source_atom.z,
            )
        )

    added_au_indices: list[int] = []
    for proposal in ordered:
        new_index = len(transformed_atoms)
        added_au_indices.append(new_index)
        transformed_atoms.append(
            Atom(new_index, "Au", proposal.x, proposal.y, proposal.z)
        )

    transformed_structure = MolecularStructure(
        tuple(transformed_atoms),
        comment=structure.comment,
    )
    transformed_bonds: list[Bond] = []
    for bond in connectivity:
        first_new = mapping[bond.first_index]
        second_new = mapping[bond.second_index]
        if first_new is None or second_new is None:
            continue
        transformed_bonds.append(Bond(first_new, second_new, bond.distance))

    for proposal, au_index in zip(ordered, added_au_indices, strict=True):
        binding_index = mapping[proposal.anchor.binding_atom_index]
        if binding_index is None:
            raise AuPlacementApplicationError(
                "selected binding atom was removed during application"
            )
        binding_atom = transformed_structure[binding_index]
        au_atom = transformed_structure[au_index]
        transformed_bonds.append(
            Bond(
                binding_index,
                au_index,
                dist(
                    (binding_atom.x, binding_atom.y, binding_atom.z),
                    (au_atom.x, au_atom.y, au_atom.z),
                ),
            )
        )

    transformed_connectivity = Connectivity(
        len(transformed_structure),
        tuple(transformed_bonds),
    )
    return AppliedAuPlacement(
        structure=transformed_structure,
        connectivity=transformed_connectivity,
        old_to_new_indices=tuple(mapping),
        removed_atom_indices=removed,
        added_au_indices=tuple(added_au_indices),
    )


def _validate_source(
    structure: MolecularStructure,
    connectivity: Connectivity,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("connectivity must be a Connectivity")
    if connectivity.atom_count != len(structure):
        raise AuPlacementApplicationError(
            "connectivity atom count does not match the source structure"
        )


def _validate_proposal_indices(
    proposal: AuPlacementProposal,
    source_atom_count: int,
) -> None:
    indexes = (
        (proposal.anchor.binding_atom_index,)
        + proposal.anchor.atom_indices
        + proposal.anchor.attached_au_indices
        + proposal.remove_atom_indices
    )
    if any(atom_index >= source_atom_count for atom_index in indexes):
        raise AuPlacementApplicationError(
            "proposal contains an atom index outside the source structure"
        )


def _validate_terminal_hydrogen_removal(
    structure: MolecularStructure,
    connectivity: Connectivity,
    proposal: AuPlacementProposal,
) -> None:
    if proposal.anchor.kind not in {
        AnchorKind.SH,
        AnchorKind.ALKYNYL_C,
    }:
        return
    removals = proposal.remove_atom_indices
    if len(removals) != 1:
        raise AuPlacementApplicationError(
            f"{proposal.anchor.kind.value} application requires exactly one "
            f"scheduled terminal H removal; found {len(removals)}"
        )
    hydrogen_index = removals[0]
    if (
        hydrogen_index not in proposal.anchor.atom_indices
        or structure[hydrogen_index].element != "H"
    ):
        raise AuPlacementApplicationError(
            f"{proposal.anchor.kind.value} scheduled removal must identify "
            "its explicit terminal H"
        )
    binding_index = proposal.anchor.binding_atom_index
    if not any(
        {bond.first_index, bond.second_index}
        == {binding_index, hydrogen_index}
        for bond in connectivity
    ):
        raise AuPlacementApplicationError(
            f"{proposal.anchor.kind.value} scheduled terminal H must be "
            "directly connected to its binding atom"
        )


def _validate_cross_proposal_removals(
    proposals: tuple[AuPlacementProposal, ...],
) -> None:
    selected_bindings = {
        proposal.anchor.binding_atom_index for proposal in proposals
    }
    removal_union = {
        atom_index
        for proposal in proposals
        for atom_index in proposal.remove_atom_indices
    }
    binding_conflicts = removal_union & selected_bindings
    if binding_conflicts:
        raise AuPlacementApplicationError(
            "scheduled removals contain selected binding atoms: "
            f"{tuple(sorted(binding_conflicts))}"
        )

    for first_index, first in enumerate(proposals):
        first_removals = set(first.remove_atom_indices)
        for second in proposals[first_index + 1:]:
            second_removals = set(second.remove_atom_indices)
            duplicate_removals = first_removals & second_removals
            if duplicate_removals:
                raise AuPlacementApplicationError(
                    "proposals schedule the same source atoms for removal: "
                    f"{tuple(sorted(duplicate_removals))}"
                )
            first_conflicts = first_removals & (
                set(second.anchor.atom_indices)
                | set(second.anchor.attached_au_indices)
            )
            second_conflicts = second_removals & (
                set(first.anchor.atom_indices)
                | set(first.anchor.attached_au_indices)
            )
            conflicts = first_conflicts | second_conflicts
            if conflicts:
                raise AuPlacementApplicationError(
                    "scheduled removals conflict with another selected anchor: "
                    f"{tuple(sorted(conflicts))}"
                )
