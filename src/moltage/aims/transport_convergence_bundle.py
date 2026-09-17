"""Build immutable FHI-aims Step-3 transport-convergence inputs."""

from dataclasses import dataclass, field

from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.species_library import (
    SpeciesDefinitionProvider,
    SpeciesDefaultBlock,
    SpeciesRequirement,
)
from moltage.aims.transport_convergence_control import (
    render_transport_convergence_control_in,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
    TransportConvergenceSettingsError,
)
from moltage.domain.structure import MolecularStructure


@dataclass(frozen=True, slots=True)
class TransportConvergenceAimsInputs:
    """Exact geometry/control text and provenance for one Step-3 preflight."""

    structure: MolecularStructure
    settings: TransportConvergenceSettings
    geometry_text: str = field(repr=False)
    control_text: str = field(repr=False)
    species_blocks: tuple[SpeciesDefaultBlock, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure) or not self.structure:
            raise TypeError("Step-3 inputs require a non-empty MolecularStructure")
        if not isinstance(self.settings, TransportConvergenceSettings):
            raise TypeError(
                "Step-3 inputs require TransportConvergenceSettings"
            )
        if not isinstance(self.geometry_text, str):
            raise TypeError("Step-3 geometry text must be a string")
        if not isinstance(self.control_text, str):
            raise TypeError("Step-3 control text must be a string")
        blocks = tuple(self.species_blocks)
        if not blocks or any(
            not isinstance(block, SpeciesDefaultBlock) for block in blocks
        ):
            raise TypeError(
                "Step-3 species blocks must contain resolved species records"
            )
        object.__setattr__(self, "species_blocks", blocks)


@dataclass(frozen=True, slots=True)
class TransportConvergenceInputPlan:
    """Immutable Step-3 plan materialized after remote species acquisition."""

    structure: MolecularStructure
    settings: TransportConvergenceSettings

    def __post_init__(self) -> None:
        _validate_transport_plan(self.structure, self.settings)

    @property
    def species_requirements(self) -> tuple[SpeciesRequirement, ...]:
        return tuple(
            SpeciesRequirement(element, self.settings.species_accuracy)
            for element in dict.fromkeys(
                atom.element for atom in self.structure
            )
        )

    def materialize(
        self,
        species_library: SpeciesDefinitionProvider,
    ) -> TransportConvergenceAimsInputs:
        return build_transport_convergence_aims_inputs(
            self.structure,
            self.settings,
            species_library,
        )


def build_transport_convergence_aims_inputs(
    structure: MolecularStructure,
    settings: TransportConvergenceSettings,
    species_library: SpeciesDefinitionProvider,
) -> TransportConvergenceAimsInputs:
    """Resolve only used species and render exact accepted working geometry."""

    _validate_transport_plan(structure, settings)
    if not isinstance(species_library, SpeciesDefinitionProvider):
        raise TypeError(
            "species_library must implement SpeciesDefinitionProvider"
        )

    elements = tuple(dict.fromkeys(atom.element for atom in structure))
    blocks = tuple(
        species_library.load(element, settings.species_accuracy)
        for element in elements
    )
    geometry_text = render_geometry_in(structure)
    control_text = render_transport_convergence_control_in(
        settings,
        blocks,
        structure,
    )
    return TransportConvergenceAimsInputs(
        structure,
        settings,
        geometry_text,
        control_text,
        blocks,
    )


def _validate_transport_plan(
    structure: MolecularStructure,
    settings: TransportConvergenceSettings,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not structure:
        raise TransportConvergenceSettingsError(
            "Step-3 transport convergence requires at least one atom"
        )
    if not isinstance(settings, TransportConvergenceSettings):
        raise TypeError("settings must be TransportConvergenceSettings")
