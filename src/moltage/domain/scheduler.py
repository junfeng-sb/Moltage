"""Scheduler identities shared by profiles and persisted task provenance."""

from enum import StrEnum


class SchedulerKind(StrEnum):
    """The two batch schedulers supported by the application."""

    SLURM = "SLURM"
    LSF = "LSF"


def scheduler_display_name(kind: SchedulerKind) -> str:
    """Return the stable user-facing scheduler name."""

    normalized = SchedulerKind(kind)
    return "Slurm" if normalized is SchedulerKind.SLURM else "LSF"
