"""Read exact FHI-aims species definitions through one connected executor."""

from pathlib import PurePosixPath

from moltage.aims.species_library import (
    InMemorySpeciesLibrary,
    SpeciesDefaultBlock,
    SpeciesRequirement,
    species_default_block_from_text,
    species_default_filename,
)
from moltage.domain.server_profile import (
    ServerProfileValidationError,
    validate_fhi_species_defaults_path,
)
from moltage.remote.executor import (
    RemoteExecutor,
    RemoteExecutorError,
    RemotePathNotFoundError,
    RemotePathStat,
)


MAX_SPECIES_DEFAULT_BYTES = 2 * 1024 * 1024


class SpeciesAcquisitionError(RuntimeError):
    """Raised before remote mutation when required definitions cannot be read."""


class MissingSpeciesRootConfigurationError(SpeciesAcquisitionError):
    """Raised when a selected profile has no configured species root."""


def acquire_species_library(
    executor: RemoteExecutor,
    species_root: str | None,
    requirements: tuple[SpeciesRequirement, ...],
) -> InMemorySpeciesLibrary:
    """Fetch and validate each exact requirement once without fallback."""

    if species_root is None:
        raise MissingSpeciesRootConfigurationError(
            "FHI-aims species definitions root is not configured for the "
            "selected server. Open Cluster Execution Settings > Manual "
            "Configuration and enter the remote root whose immediate children "
            "include light, tight, and really_tight."
        )
    try:
        root = validate_fhi_species_defaults_path(species_root)
    except ServerProfileValidationError as error:
        raise MissingSpeciesRootConfigurationError(str(error)) from None
    unique_requirements = []
    for requirement in requirements:
        if not isinstance(requirement, SpeciesRequirement):
            raise TypeError(
                "species requirements must contain SpeciesRequirement records"
            )
        if requirement not in unique_requirements:
            unique_requirements.append(requirement)
    if not unique_requirements:
        raise SpeciesAcquisitionError(
            "Input preparation requires at least one species definition"
        )

    blocks = tuple(
        _read_species_block(executor, root, requirement)
        for requirement in unique_requirements
    )
    return InMemorySpeciesLibrary(blocks)


def _read_species_block(
    executor: RemoteExecutor,
    root: str,
    requirement: SpeciesRequirement,
) -> SpeciesDefaultBlock:
    path = PurePosixPath(root) / requirement.accuracy.value / (
        species_default_filename(requirement.element, requirement.accuracy)
    )
    path_text = str(path)
    context = (
        f"element={requirement.element}, "
        f"accuracy={requirement.accuracy.value}, remote_path={path_text}"
    )
    try:
        path_stat = executor.stat(path_text)
    except RemotePathNotFoundError:
        raise SpeciesAcquisitionError(
            f"Required FHI-aims species definition is missing: {context}"
        ) from None
    except RemoteExecutorError as error:
        raise SpeciesAcquisitionError(
            "Unable to inspect required FHI-aims species definition: "
            f"{context}: {error}"
        ) from None
    _validate_species_stat(path_stat, context)
    try:
        raw = executor.read_bytes(path_text)
    except RemotePathNotFoundError:
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition disappeared during input "
            f"preparation: {context}"
        ) from None
    except RemoteExecutorError as error:
        raise SpeciesAcquisitionError(
            "Unable to read required FHI-aims species definition: "
            f"{context}: {error}"
        ) from None
    if len(raw) > MAX_SPECIES_DEFAULT_BYTES:
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition exceeds the read limit: "
            f"{context}, size={len(raw)} bytes"
        )
    if path_stat.size != len(raw):
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition changed while being read: "
            f"{context}, expected_size={path_stat.size}, actual_size={len(raw)}"
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition is not valid UTF-8: "
            f"{context}: {error}"
        ) from None
    try:
        return species_default_block_from_text(
            requirement.element,
            requirement.accuracy,
            path,
            text,
        )
    except (TypeError, ValueError) as error:
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition is invalid: "
            f"{context}: {error}"
        ) from None


def _validate_species_stat(path_stat: object, context: str) -> None:
    if not isinstance(path_stat, RemotePathStat):
        raise SpeciesAcquisitionError(
            "Remote species-definition stat returned an invalid result: "
            + context
        )
    if path_stat.is_directory:
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition is a directory: " + context
        )
    if (
        isinstance(path_stat.size, bool)
        or not isinstance(path_stat.size, int)
        or path_stat.size < 0
    ):
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition has no bounded file size: "
            + context
        )
    if path_stat.size > MAX_SPECIES_DEFAULT_BYTES:
        raise SpeciesAcquisitionError(
            "Required FHI-aims species definition exceeds the read limit: "
            f"{context}, size={path_stat.size} bytes"
        )
