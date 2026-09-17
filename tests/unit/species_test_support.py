"""Synthetic FHI-aims species definitions for offline tests only."""

from functools import lru_cache
from pathlib import PurePosixPath

from moltage.aims.optimization_settings import SpeciesAccuracy
from moltage.aims.species_library import (
    InMemorySpeciesLibrary,
    SpeciesDefaultBlock,
    SpeciesRequirement,
    element_for_atomic_number,
    species_default_filename,
)
from synthetic_test_data import SYNTHETIC_SPECIES_ROOT


TEST_SPECIES_ROOT = SYNTHETIC_SPECIES_ROOT


def synthetic_species_text(
    element: str,
    accuracy: SpeciesAccuracy,
) -> str:
    """Return the smallest useful, provenance-labelled test definition."""

    atomic_number = int(species_default_filename(element, accuracy).split("_", 1)[0])
    return (
        f"# SYNTHETIC TEST SPECIES {accuracy.value}\n"
        f"species {element}\n"
        f"  nucleus {atomic_number}\n"
    )


def synthetic_species_block(
    element: str,
    accuracy: SpeciesAccuracy,
    *,
    root: str = TEST_SPECIES_ROOT,
) -> SpeciesDefaultBlock:
    path = (
        PurePosixPath(root)
        / accuracy.value
        / species_default_filename(element, accuracy)
    )
    return SpeciesDefaultBlock(
        element,
        element,
        accuracy,
        path,
        synthetic_species_text(element, accuracy),
    )


@lru_cache(maxsize=None)
def _cached_library(
    elements: tuple[str, ...],
    accuracies: tuple[SpeciesAccuracy, ...],
) -> InMemorySpeciesLibrary:
    return InMemorySpeciesLibrary(
        synthetic_species_block(element, accuracy)
        for element in elements
        for accuracy in accuracies
    )


def synthetic_species_library(
    elements: tuple[str, ...] | None = None,
    accuracies: tuple[SpeciesAccuracy, ...] = tuple(SpeciesAccuracy),
) -> InMemorySpeciesLibrary:
    """Return a reusable in-memory library with no packaged-file dependency."""

    if elements is None:
        resolved = []
        atomic_number = 1
        while True:
            try:
                resolved.append(element_for_atomic_number(atomic_number))
            except ValueError:
                break
            atomic_number += 1
        elements = tuple(resolved)
    return _cached_library(tuple(elements), tuple(accuracies))


def install_remote_species(
    remote,
    requirements: tuple[SpeciesRequirement, ...],
    *,
    root: str = TEST_SPECIES_ROOT,
) -> None:
    """Install exact synthetic definition bytes in a memory remote fixture."""

    for requirement in requirements:
        path = str(
            PurePosixPath(root)
            / requirement.accuracy.value
            / species_default_filename(requirement.element, requirement.accuracy)
        )
        remote.files[path] = synthetic_species_text(
            requirement.element,
            requirement.accuracy,
        ).encode("utf-8")
