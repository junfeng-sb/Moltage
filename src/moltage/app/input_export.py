"""Export newly generated FHI-aims inputs using one saved remote runtime."""

from dataclasses import dataclass, field
from pathlib import Path

from moltage.aims.input_bundle import (
    AimsOptimizationInputPlan,
    write_aims_optimization_inputs,
)
from moltage.app.server_profiles import ServerProfileRepository
from moltage.app.species_acquisition import acquire_species_library
from moltage.domain.server_profile import ServerProfile


class AimsInputExportError(RuntimeError):
    """Raised when a local export cannot use an eligible saved server."""


@dataclass(frozen=True, slots=True)
class AimsInputExportRequest:
    """One confirmed local export whose definitions come from a saved server."""

    profile: ServerProfile
    input_plan: AimsOptimizationInputPlan
    destination: Path
    overwrite: bool = False
    supplied_password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ServerProfile):
            raise TypeError("input export requires a ServerProfile")
        if not isinstance(self.input_plan, AimsOptimizationInputPlan):
            raise TypeError("input export requires an AimsOptimizationInputPlan")
        object.__setattr__(self, "destination", Path(self.destination))
        if not isinstance(self.overwrite, bool):
            raise TypeError("overwrite must be a boolean")
        if self.supplied_password is not None and (
            not isinstance(self.supplied_password, str)
            or not self.supplied_password
        ):
            raise ValueError("supplied password must be a non-empty string")


class AimsInputExportService:
    """Acquire remote definitions fully before creating any local output."""

    def __init__(
        self,
        connection_service,
        profile_repository: ServerProfileRepository,
    ) -> None:
        self._connection_service = connection_service
        self._profile_repository = profile_repository

    def export(
        self,
        request: AimsInputExportRequest,
        *,
        progress=None,
    ) -> tuple[Path, Path]:
        if not isinstance(request, AimsInputExportRequest):
            raise TypeError("request must be an AimsInputExportRequest")
        report = progress if progress is not None else lambda _message: None
        profile = self._saved_profile(request.profile)
        preset = profile.execution_preset
        if preset is None or preset.fhi_species_defaults_path is None:
            raise AimsInputExportError(
                "Configure the selected server's FHI-aims runtime and species "
                "definitions root before generating new control.in files."
            )

        report(f"Connecting to {profile.name} for species definitions...")
        executor = self._connection_service.connect_for_remote_operation(
            profile,
            request.supplied_password,
        )
        try:
            report("Reading and validating required FHI-aims species definitions...")
            library = acquire_species_library(
                executor,
                preset.fhi_species_defaults_path,
                request.input_plan.species_requirements,
            )
            bundle = request.input_plan.materialize(library)
        finally:
            _close_without_replacing_outcome(executor)

        report("Writing validated FHI-aims inputs locally...")
        return write_aims_optimization_inputs(
            bundle,
            request.destination,
            overwrite=request.overwrite,
        )

    def _saved_profile(self, selected: ServerProfile) -> ServerProfile:
        collection = self._profile_repository.load()
        saved = next(
            (
                profile
                for profile in collection.profiles
                if profile.profile_id == selected.profile_id
            ),
            None,
        )
        if saved is None:
            raise AimsInputExportError(
                "Select a saved Server Connection before generating a new "
                "control.in file."
            )
        return saved


def _close_without_replacing_outcome(executor) -> None:
    try:
        executor.close()
    except Exception:
        pass
