"""Persistent typed evidence attached to ORCA project stages."""

from dataclasses import dataclass


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
        return all(
            (
                self.scheduler_succeeded,
                self.normal_termination,
                self.optimization_converged,
                self.final_xyz_valid,
            )
        )
