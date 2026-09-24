"""Persistent typed evidence attached to ORCA project stages."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath


class OrcaOptimizationOrigin(StrEnum):
    """Whether Moltage submitted an optimization or imported a finished one."""

    MOLTAGE_SUBMITTED = "MOLTAGE_SUBMITTED"
    IMPORTED_EXTERNAL = "IMPORTED_EXTERNAL"


class OrcaImportWavefunctionReadiness(StrEnum):
    """Whether the verified WBL conversion utility is available for a source."""

    READY = "READY"
    CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"


def _require_sha256(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA256")


def _require_source_filename(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "\x00", "\n", "\r"))
    ):
        raise ValueError(f"{field_name} must be a plain remote file name")


@dataclass(frozen=True, slots=True)
class OrcaOptimizationResultEvidence:
    scheduler_succeeded: bool
    normal_termination: bool
    optimization_converged: bool
    final_xyz_valid: bool
    wbl_input_ready: bool
    output_sha256: str | None = None
    xyz_sha256: str | None = None
    gbw_sha256: str | None = None
    diagnostic: str | None = None
    origin: OrcaOptimizationOrigin = OrcaOptimizationOrigin.MOLTAGE_SUBMITTED

    def __post_init__(self) -> None:
        for field_name in (
            "scheduler_succeeded",
            "normal_termination",
            "optimization_converged",
            "final_xyz_valid",
            "wbl_input_ready",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")
        try:
            origin = OrcaOptimizationOrigin(self.origin)
        except (TypeError, ValueError):
            raise ValueError("ORCA optimization origin is unsupported") from None
        object.__setattr__(self, "origin", origin)
        if origin is OrcaOptimizationOrigin.IMPORTED_EXTERNAL and self.scheduler_succeeded:
            raise ValueError(
                "imported ORCA optimization evidence must not claim a scheduler outcome"
            )
        for field_name in ("output_sha256", "xyz_sha256", "gbw_sha256"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA256 or None")
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("ORCA optimization diagnostic must be nonempty text or None")

    @property
    def succeeded(self) -> bool:
        program_evidence = (
            self.normal_termination,
            self.optimization_converged,
            self.final_xyz_valid,
        )
        if self.origin is OrcaOptimizationOrigin.IMPORTED_EXTERNAL:
            # Moltage observed no scheduler outcome for an imported directory, so
            # success rests entirely on ORCA's own reviewed output evidence.
            return all(program_evidence)
        return self.scheduler_succeeded and all(program_evidence)


@dataclass(frozen=True, slots=True)
class OrcaImportProvenance:
    """Immutable record of the external directory an optimization came from."""

    source_directory: str
    source_stem: str
    source_input_filename: str
    source_output_filename: str
    source_geometry_filename: str
    source_wavefunction_filename: str
    source_input_sha256: str
    source_output_sha256: str
    source_geometry_sha256: str
    source_wavefunction_sha256: str
    imported_at: datetime
    wavefunction_readiness: OrcaImportWavefunctionReadiness
    orca_2json_path: str | None = None
    readiness_diagnostic: str | None = None
    # Set only when the source input read its starting coordinates through
    # ``*xyzfile``; the managed ``orca_opt.inp`` then carries them inline.
    source_coordinate_path: str | None = None
    source_coordinate_sha256: str | None = None

    def __post_init__(self) -> None:
        directory = self.source_directory
        if not isinstance(directory, str) or not directory.startswith("/"):
            raise ValueError("imported source directory must be an absolute POSIX path")
        path = PurePosixPath(directory)
        if str(path) != directory or ".." in path.parts:
            raise ValueError("imported source directory must be a normalized POSIX path")
        if (self.source_coordinate_path is None) != (self.source_coordinate_sha256 is None):
            raise ValueError(
                "an imported coordinate file requires both its path and its SHA256"
            )
        if self.source_coordinate_path is not None:
            coordinate = self.source_coordinate_path
            if (
                not isinstance(coordinate, str)
                or not coordinate.startswith("/")
                or str(PurePosixPath(coordinate)) != coordinate
                or ".." in PurePosixPath(coordinate).parts
            ):
                raise ValueError(
                    "imported coordinate file must be an absolute normalized POSIX path"
                )
            _require_sha256(self.source_coordinate_sha256, "source_coordinate_sha256")
        if not isinstance(self.source_stem, str) or not self.source_stem:
            raise ValueError("imported source stem must be nonempty text")
        for field_name in (
            "source_input_filename",
            "source_output_filename",
            "source_geometry_filename",
            "source_wavefunction_filename",
        ):
            _require_source_filename(getattr(self, field_name), field_name)
        for field_name in (
            "source_input_sha256",
            "source_output_sha256",
            "source_geometry_sha256",
            "source_wavefunction_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        timestamp = self.imported_at
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ValueError("import timestamp must be timezone-aware")
        try:
            readiness = OrcaImportWavefunctionReadiness(self.wavefunction_readiness)
        except (TypeError, ValueError):
            raise ValueError("imported WBL readiness is unsupported") from None
        object.__setattr__(self, "wavefunction_readiness", readiness)
        if self.orca_2json_path is not None and (
            not isinstance(self.orca_2json_path, str)
            or not self.orca_2json_path.startswith("/")
        ):
            raise ValueError("verified orca_2json path must be absolute or None")
        if readiness is OrcaImportWavefunctionReadiness.READY:
            if self.orca_2json_path is None:
                raise ValueError("ready WBL input requires the verified utility path")
        elif self.readiness_diagnostic is None:
            raise ValueError(
                "WBL input that requires configuration must record its reason"
            )
        if self.readiness_diagnostic is not None and (
            not isinstance(self.readiness_diagnostic, str)
            or not self.readiness_diagnostic.strip()
        ):
            raise ValueError("WBL readiness diagnostic must be nonempty text or None")

    @property
    def source_input_path(self) -> str:
        return str(PurePosixPath(self.source_directory) / self.source_input_filename)

    @property
    def source_output_path(self) -> str:
        return str(PurePosixPath(self.source_directory) / self.source_output_filename)

    @property
    def source_geometry_path(self) -> str:
        return str(PurePosixPath(self.source_directory) / self.source_geometry_filename)

    @property
    def source_wavefunction_path(self) -> str:
        return str(
            PurePosixPath(self.source_directory) / self.source_wavefunction_filename
        )
