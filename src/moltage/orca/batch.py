"""Deterministic Slurm and LSF scripts for direct ORCA driver execution."""

from pathlib import PurePosixPath
import re
import shlex
from uuid import UUID

from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    SlurmExecutionPreset,
    SlurmMailSettings,
)
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings
from moltage.remote.runtime_environment import runtime_environment_commands
from moltage.remote.slurm import (
    SlurmSubmissionError,
    lsf_scheduler_log_filename,
    render_lsf_mail_directives,
    render_lsf_resource_directives,
    render_lsf_site_directives,
    render_slurm_mail_directives,
    render_slurm_site_directives,
)


def render_orca_submit_script(
    preset: SlurmExecutionPreset,
    runtime: OrcaRuntimeConfiguration,
    project_id: UUID,
    settings: OrcaOptimizationSettings | OrcaFrequencySettings,
    *,
    frequency: bool = False,
    mail_settings: SlurmMailSettings | None = None,
) -> str:
    """Render one scheduler script that invokes only the verified ORCA path."""

    if not isinstance(preset, SlurmExecutionPreset):
        raise SlurmSubmissionError("ORCA submission requires Cluster Execution Settings")
    if not isinstance(runtime, OrcaRuntimeConfiguration):
        raise SlurmSubmissionError("ORCA submission requires a verified ORCA runtime")
    if not isinstance(project_id, UUID):
        raise SlurmSubmissionError("ORCA scheduler job naming requires a project UUID")
    if not isinstance(settings, (OrcaOptimizationSettings, OrcaFrequencySettings)):
        raise SlurmSubmissionError("ORCA submission settings are invalid")
    if preset.cpus_per_task != 1:
        raise SlurmSubmissionError("ORCA submission currently requires one CPU per scheduler task")
    if preset.ntasks != settings.process_count:
        raise SlurmSubmissionError(
            "Scheduler task slots must equal ORCA %pal nprocs before submission"
        )
    input_name = "orca_freq.inp" if frequency else "orca_opt.inp"
    output_name = "orca_freq.out" if frequency else "orca_opt.out"
    job_name = f"MT-{project_id.hex[:8].upper()}-{'FREQ' if frequency else 'OPT'}"
    scheduler_output = (
        "orca_freq.scheduler.out" if frequency else "orca_opt.scheduler.out"
    )
    launch = (
        f"exec {shlex.quote(runtime.executable_path)} {input_name} "
        f"> {output_name} 2>&1"
    )
    if preset.scheduler_kind is SchedulerKind.SLURM:
        lines = [
            "#!/bin/bash -l",
            "",
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --output={scheduler_output}",
            *render_slurm_site_directives(preset),
            f"#SBATCH --nodes={preset.nodes}",
            f"#SBATCH --ntasks={preset.ntasks}",
            "#SBATCH --cpus-per-task=1",
            f"#SBATCH --time={preset.runtime_minutes}",
            f"#SBATCH --mem={preset.memory_gb}G",
        ]
        if preset.no_requeue:
            lines.append("#SBATCH --no-requeue")
        if preset.export_none:
            lines.append("#SBATCH --export=NONE")
        lines.extend(render_slurm_mail_directives(mail_settings))
        lines.append("")
        if preset.unset_slurm_export_env:
            lines.extend(("unset SLURM_EXPORT_ENV", ""))
    else:
        hours, minutes = divmod(preset.runtime_minutes, 60)
        lines = [
            "#!/bin/bash -l",
            "",
            f"#BSUB -J {job_name}",
            f"#BSUB -oo {lsf_scheduler_log_filename(scheduler_output)}",
            *render_lsf_site_directives(preset),
            f"#BSUB -n {preset.ntasks}",
            f"#BSUB -W {hours}:{minutes:02d}",
            *render_lsf_resource_directives(
                preset,
                nodes=preset.nodes,
                ntasks=preset.ntasks,
                memory_gb=preset.memory_gb,
            ),
        ]
        if preset.no_requeue:
            lines.append("#BSUB -rn")
        if preset.export_none:
            lines.append('#BSUB -env "none"')
        lines.extend(render_lsf_mail_directives(mail_settings))
        lines.append("")
    lines.extend(runtime_environment_commands(runtime.environment))
    lines.extend(
        (
            "export OMP_NUM_THREADS=1",
            "",
            launch,
        )
    )
    script = "\n".join(lines) + "\n"
    _validate_rendered_script(script, runtime.executable_path, input_name, output_name)
    return script


def orca_memory_advisory(
    preset: SlurmExecutionPreset,
    settings: OrcaOptimizationSettings | OrcaFrequencySettings,
) -> str | None:
    """Return a transparent capacity warning without modifying either value."""

    configured = (
        None
        if settings.max_core_mb is None
        else settings.process_count * settings.max_core_mb
    )
    if configured is None:
        return None
    if preset.scheduler_kind is SchedulerKind.LSF:
        return (
            f"ORCA is configured for approximately {configured} MB across processes; "
            "LSF rusage memory scope is site-defined and is not treated as an exact total."
        )
    capacity_mb = preset.nodes * preset.memory_gb * 1024
    if configured > capacity_mb:
        return (
            f"ORCA nprocs × %MaxCore is {configured} MB, above the requested "
            f"Slurm node-memory capacity of {capacity_mb} MB. %MaxCore is not a hard limit."
        )
    return None


def _validate_rendered_script(
    script: str,
    executable_path: str,
    input_name: str,
    output_name: str,
) -> None:
    expected = (
        f"exec {shlex.quote(executable_path)} {input_name} > {output_name} 2>&1"
    )
    if expected not in script:
        raise SlurmSubmissionError("ORCA script does not contain the controlled direct invocation")
    command_lines = tuple(
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.startswith("#")
    )
    if any(re.match(r"^(?:srun|mpirun|which|orca)(?:\s|$)", line) for line in command_lines):
        raise SlurmSubmissionError("ORCA script contains an unsupported launcher or PATH lookup")
    if PurePosixPath(executable_path).name != "orca":
        raise SlurmSubmissionError("ORCA executable path must name orca")
