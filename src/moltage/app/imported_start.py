"""Validated imported contact-Au entry contexts for Steps 2 and 3."""

from dataclasses import dataclass
from typing import Sequence

from moltage.domain.anchor import AnchorCandidate
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.domain.calculation_project import ProjectElectrodeClusterProvenance
from moltage.domain.connectivity import Connectivity
from moltage.domain.electrode import (
    AppliedElectrodePlacement,
    ElectrodeContactSite,
)
from moltage.domain.junction import AppliedAuPlacement
from moltage.domain.structure import MolecularStructure
from moltage.junction.electrode_builder import (
    ElectrodeBuilderError,
    eligible_electrode_contact_sites,
)


class ImportedContactAuEligibilityError(ValueError):
    """Raised when a local structure is not the exact imported two-contact case."""


@dataclass(frozen=True, slots=True)
class ImportedContactAuContext:
    """Proof derived only from frozen anchor/contact recognition and topology."""

    structure: MolecularStructure
    connectivity: Connectivity
    anchors: tuple[AnchorCandidate, ...]
    contact_sites: tuple[ElectrodeContactSite, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("imported contact-Au context requires a structure")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("imported contact-Au context requires connectivity")
        if self.connectivity.atom_count != len(self.structure):
            raise ImportedContactAuEligibilityError(
                "connectivity does not match the imported structure"
            )
        anchors = tuple(self.anchors)
        sites = tuple(self.contact_sites)
        if len(anchors) != 2 or len(sites) != 2:
            raise ImportedContactAuEligibilityError(
                "the imported start requires exactly two recognized linker termini"
            )
        if any(not isinstance(item, AnchorCandidate) for item in anchors):
            raise TypeError("imported anchors must contain AnchorCandidate records")
        if any(not isinstance(item, ElectrodeContactSite) for item in sites):
            raise TypeError(
                "imported contact sites must contain ElectrodeContactSite records"
            )
        if {site.anchor for site in sites} != set(anchors):
            raise ImportedContactAuEligibilityError(
                "each recognized linker terminus must own one contact Au"
            )
        contact_indices = frozenset(site.contact_au_index for site in sites)
        all_au_indices = frozenset(
            atom.index for atom in self.structure if atom.element == "Au"
        )
        if all_au_indices != contact_indices:
            raise ImportedContactAuEligibilityError(
                "the imported start must contain only the two recognized contact Au atoms"
            )
        object.__setattr__(self, "anchors", anchors)
        object.__setattr__(self, "contact_sites", sites)


@dataclass(frozen=True, slots=True)
class ImportedTransportStartContext:
    """Imported pre-optimized contact geometry plus the accepted Phase-2D result."""

    contact_context: ImportedContactAuContext
    applied_electrodes: AppliedElectrodePlacement
    current_structure: MolecularStructure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.contact_context, ImportedContactAuContext):
            raise TypeError("direct Step 3 requires imported contact-Au proof")
        if not isinstance(self.applied_electrodes, AppliedElectrodePlacement):
            raise TypeError("direct Step 3 requires an applied electrode result")
        proposal = self.applied_electrodes.proposal
        if len(proposal.clusters) != 2:
            raise ImportedContactAuEligibilityError(
                "direct Step 3 requires both accepted electrode clusters"
            )
        if proposal.source_structure is not self.contact_context.structure:
            raise ImportedContactAuEligibilityError(
                "the applied electrodes do not originate from the imported structure"
            )
        if proposal.source_connectivity is not self.contact_context.connectivity:
            raise ImportedContactAuEligibilityError(
                "the applied electrodes do not preserve imported contact topology"
            )
        proposal_sites = tuple(cluster.site for cluster in proposal.clusters)
        if proposal_sites != self.contact_context.contact_sites:
            raise ImportedContactAuEligibilityError(
                "the applied electrodes do not match the recognized contact sites"
            )
        current = self.current_structure
        if current is not None:
            if not isinstance(current, MolecularStructure):
                raise TypeError("direct Step 3 working geometry must be a structure")
            if _atom_identity(current) != _atom_identity(
                self.applied_electrodes.structure
            ):
                raise ImportedContactAuEligibilityError(
                    "direct Step 3 working geometry may change coordinates only"
                )

    @property
    def working_structure(self) -> MolecularStructure:
        if self.current_structure is not None:
            return self.current_structure
        return self.applied_electrodes.structure

    @property
    def electrode_provenance(
        self,
    ) -> tuple[ProjectElectrodeClusterProvenance, ...]:
        return provenance_from_applied_electrodes(self.applied_electrodes)


def imported_contact_au_context(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchors: Sequence[AnchorCandidate],
) -> ImportedContactAuContext:
    """Require exactly two recognized occupied termini and no unrelated Au."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("imported contact-Au eligibility requires a structure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("imported contact-Au eligibility requires connectivity")
    recognized = tuple(anchors)
    if len(recognized) != 2:
        raise ImportedContactAuEligibilityError(
            "the imported start requires exactly two recognized linker termini"
        )
    if any(not isinstance(anchor, AnchorCandidate) for anchor in recognized):
        raise TypeError("anchors must contain AnchorCandidate records")
    if any(len(anchor.attached_au_indices) != 1 for anchor in recognized):
        raise ImportedContactAuEligibilityError(
            "each recognized linker terminus must have exactly one attached contact Au"
        )
    try:
        sites = eligible_electrode_contact_sites(
            structure,
            connectivity,
            recognized,
        )
    except ElectrodeBuilderError as error:
        raise ImportedContactAuEligibilityError(str(error)) from None
    return ImportedContactAuContext(
        structure,
        connectivity,
        recognized,
        sites,
    )


def applied_contact_au_context(
    structure: MolecularStructure,
    connectivity: Connectivity,
    anchors: Sequence[AnchorCandidate],
    applied_placement: AppliedAuPlacement,
) -> ImportedContactAuContext:
    """Validate the exact two-contact state after accepted viewer placement.

    One or two sites may have been applied in the latest operation; this permits
    users to add the two contacts together or sequentially.  The current working
    geometry may subsequently change coordinates, but it must retain the same
    atoms and bond topology, contain exactly two recognized contact Au atoms,
    and use every Au from the latest accepted placement as one of those contacts.
    """

    if not isinstance(structure, MolecularStructure):
        raise TypeError("applied contact-Au eligibility requires a structure")
    if not isinstance(connectivity, Connectivity):
        raise TypeError("applied contact-Au eligibility requires connectivity")
    if not isinstance(applied_placement, AppliedAuPlacement):
        raise TypeError("direct Step 2 requires an accepted Au placement")
    if not applied_placement.added_au_indices:
        raise ImportedContactAuEligibilityError(
            "direct Step 2 requires an Au atom added by the viewer"
        )
    if _atom_identity(structure) != _atom_identity(applied_placement.structure):
        raise ImportedContactAuEligibilityError(
            "the applied contact-Au working geometry may change coordinates only"
        )
    if _bond_identity(connectivity) != _bond_identity(
        applied_placement.connectivity
    ):
        raise ImportedContactAuEligibilityError(
            "the applied contact-Au working topology no longer matches the "
            "accepted placement"
        )

    context = imported_contact_au_context(structure, connectivity, anchors)
    contact_indices = frozenset(
        site.contact_au_index for site in context.contact_sites
    )
    if not frozenset(applied_placement.added_au_indices).issubset(
        contact_indices
    ):
        raise ImportedContactAuEligibilityError(
            "the latest viewer-added Au atoms are not recognized contacts"
        )
    return context


def _atom_identity(
    structure: MolecularStructure,
) -> tuple[tuple[int, str], ...]:
    return tuple((atom.index, atom.element) for atom in structure)


def _bond_identity(
    connectivity: Connectivity,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (bond.first_index, bond.second_index) for bond in connectivity
    )
