"""Render ordered molecular structures as FHI-aims geometry.in text."""

from collections.abc import Iterable, Sequence
from os import PathLike
from pathlib import Path
import re

from moltage.aims.optimization_settings import AtomAimsSettings
from moltage.domain.structure import MolecularStructure


def render_geometry_in(
    structure: MolecularStructure,
    *,
    species_names: Sequence[str] | None = None,
    atom_settings: Iterable[AtomAimsSettings] = (),
) -> str:
    """Render atoms and optional calculation-only annotations in source order."""

    if not isinstance(structure, MolecularStructure):
        raise TypeError("structure must be a MolecularStructure")
    resolved_species_names = (
        tuple(atom.element for atom in structure)
        if species_names is None
        else tuple(species_names)
    )
    if len(resolved_species_names) != len(structure):
        raise ValueError("geometry species-name count must match the atom count")
    for species_name in resolved_species_names:
        if (
            not isinstance(species_name, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", species_name) is None
        ):
            raise ValueError(f"invalid FHI-aims geometry species name: {species_name!r}")

    annotations: dict[int, AtomAimsSettings] = {}
    for annotation in atom_settings:
        if not isinstance(annotation, AtomAimsSettings):
            raise TypeError("atom settings must contain AtomAimsSettings records")
        if annotation.atom_index >= len(structure):
            raise ValueError(
                f"atom override index does not exist: {annotation.atom_index}"
            )
        if annotation.atom_index in annotations:
            raise ValueError(
                f"duplicate atom override index: {annotation.atom_index}"
            )
        annotations[annotation.atom_index] = annotation

    records: list[str] = []
    for atom, species_name in zip(
        structure,
        resolved_species_names,
        strict=True,
    ):
        records.append(
            f"atom {atom.x!r} {atom.y!r} {atom.z!r} {species_name}"
        )
        annotation = annotations.get(atom.index)
        if annotation is None:
            continue
        if annotation.initial_moment is not None:
            records.append(f"initial_moment {annotation.initial_moment!r}")
        if annotation.initial_charge is not None:
            records.append(f"initial_charge {annotation.initial_charge!r}")
    return "\n".join(records) + ("\n" if records else "")


def write_geometry_in(
    structure: MolecularStructure,
    path: str | PathLike[str],
    *,
    species_names: Sequence[str] | None = None,
    atom_settings: Iterable[AtomAimsSettings] = (),
) -> None:
    """Write rendered geometry.in text to the requested UTF-8 path."""

    output_path = Path(path)
    with output_path.open("w", encoding="utf-8", newline="\n") as output_file:
        output_file.write(
            render_geometry_in(
                structure,
                species_names=species_names,
                atom_settings=atom_settings,
            )
        )
