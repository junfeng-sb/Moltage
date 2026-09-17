"""Build and write deterministic FHI-aims molecular optimization inputs."""

from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from moltage.aims.control_writer import render_control_in
from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AimsSettingsValidationError,
    SpeciesAccuracy,
)
from moltage.aims.species_library import (
    SpeciesDefinitionProvider,
    SpeciesDefaultBlock,
    SpeciesRequirement,
)
from moltage.domain.structure import MolecularStructure


class AimsInputGenerationError(ValueError):
    """Raised when a coherent geometry.in/control.in pair cannot be built."""


class AimsInputWriteError(OSError):
    """Raised when an input bundle cannot be written without ambiguity."""


class ExistingAimsInputError(AimsInputWriteError):
    """Raised when writing would overwrite an existing AIMS input file."""


@dataclass(frozen=True, slots=True)
class AimsInputBundle:
    """Byte-stable text for the two mandatory FHI-aims input files."""

    geometry_text: str
    control_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.geometry_text, str):
            raise TypeError("geometry text must be a string")
        if not isinstance(self.control_text, str):
            raise TypeError("control text must be a string")


@dataclass(frozen=True, slots=True)
class AimsOptimizationInputPlan:
    """Immutable structure/settings plan materialized after species acquisition."""

    structure: MolecularStructure
    settings: AimsOptimizationSettings

    def __post_init__(self) -> None:
        _validate_optimization_plan(self.structure, self.settings)

    @property
    def species_requirements(self) -> tuple[SpeciesRequirement, ...]:
        requirements = []
        settings_by_index = {
            item.atom_index: item for item in self.settings.atom_settings
        }
        for atom in self.structure:
            atom_setting = settings_by_index.get(atom.index)
            accuracy = (
                self.settings.species_accuracy
                if atom_setting is None
                or atom_setting.species_accuracy is None
                else atom_setting.species_accuracy
            )
            requirement = SpeciesRequirement(atom.element, accuracy)
            if requirement not in requirements:
                requirements.append(requirement)
        return tuple(requirements)

    def materialize(
        self,
        species_library: SpeciesDefinitionProvider,
    ) -> AimsInputBundle:
        return build_aims_optimization_inputs(
            self.structure,
            self.settings,
            species_library,
        )


def build_aims_optimization_inputs(
    structure: MolecularStructure,
    settings: AimsOptimizationSettings,
    species_library: SpeciesDefinitionProvider,
) -> AimsInputBundle:
    """Validate, resolve species, and render one deterministic input pair."""

    _validate_optimization_plan(structure, settings)
    if not isinstance(species_library, SpeciesDefinitionProvider):
        raise TypeError(
            "species_library must implement SpeciesDefinitionProvider"
        )

    settings_by_index = {
        atom_setting.atom_index: atom_setting
        for atom_setting in settings.atom_settings
    }
    geometry_species_names: list[str] = []
    required_species: list[tuple[str, str, SpeciesAccuracy]] = []
    seen_species_names: set[str] = set()
    for atom in structure:
        atom_setting = settings_by_index.get(atom.index)
        accuracy = (
            settings.species_accuracy
            if atom_setting is None or atom_setting.species_accuracy is None
            else atom_setting.species_accuracy
        )
        species_name = _species_name(
            atom.element,
            accuracy,
            settings.species_accuracy,
        )
        geometry_species_names.append(species_name)
        if species_name not in seen_species_names:
            required_species.append((species_name, atom.element, accuracy))
            seen_species_names.add(species_name)

    species_blocks = tuple(
        species_library.load(
            element,
            accuracy,
            species_name=species_name,
        )
        for species_name, element, accuracy in required_species
    )
    _validate_species_coverage(geometry_species_names, species_blocks)
    geometry_text = render_geometry_in(
        structure,
        species_names=geometry_species_names,
        atom_settings=settings.atom_settings,
    )
    control_text = render_control_in(settings, species_blocks, structure)
    return AimsInputBundle(geometry_text, control_text)


def _validate_optimization_plan(
    structure: MolecularStructure,
    settings: AimsOptimizationSettings,
) -> None:
    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    if not structure:
        raise AimsSettingsValidationError(
            "an optimization input requires at least one atom"
        )
    if not isinstance(settings, AimsOptimizationSettings):
        raise TypeError("settings must be AimsOptimizationSettings")
    for atom_setting in settings.atom_settings:
        if atom_setting.atom_index >= len(structure):
            raise AimsSettingsValidationError(
                f"atom override index does not exist: {atom_setting.atom_index}"
            )


def write_aims_optimization_inputs(
    bundle: AimsInputBundle,
    destination: str | PathLike[str],
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Write geometry.in and control.in, rejecting implicit overwrites."""

    if not isinstance(bundle, AimsInputBundle):
        raise TypeError("bundle must be an AimsInputBundle")
    if not isinstance(overwrite, bool):
        raise TypeError("overwrite must be a boolean")
    destination_path = Path(destination)
    if destination_path.exists() and not destination_path.is_dir():
        raise AimsInputWriteError(
            f"AIMS input destination is not a directory: {destination_path}"
        )
    geometry_path = destination_path / "geometry.in"
    control_path = destination_path / "control.in"
    existing = tuple(
        path for path in (geometry_path, control_path) if path.exists()
    )
    if existing and not overwrite:
        raise ExistingAimsInputError(
            "refusing to overwrite existing FHI-aims input files: "
            + ", ".join(str(path) for path in existing)
        )
    try:
        destination_path.mkdir(parents=True, exist_ok=True)
        _write_text(geometry_path, bundle.geometry_text)
        _write_text(control_path, bundle.control_text)
    except OSError as error:
        raise AimsInputWriteError(
            f"unable to write FHI-aims inputs in {destination_path}: {error}"
        ) from error
    return geometry_path, control_path


def _species_name(
    element: str,
    accuracy: SpeciesAccuracy,
    global_accuracy: SpeciesAccuracy,
) -> str:
    return (
        element
        if accuracy is global_accuracy
        else f"{element}_{accuracy.value}"
    )


def _validate_species_coverage(
    geometry_species_names: list[str],
    species_blocks: tuple[SpeciesDefaultBlock, ...],
) -> None:
    block_names = tuple(block.species_name for block in species_blocks)
    if len(set(block_names)) != len(block_names):
        raise AimsInputGenerationError(
            "generated control.in would contain duplicate species blocks"
        )
    referenced_names = tuple(dict.fromkeys(geometry_species_names))
    if block_names != referenced_names:
        raise AimsInputGenerationError(
            "geometry/control species mismatch: "
            f"geometry={referenced_names}, control={block_names}"
        )


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output_file:
        output_file.write(text)
