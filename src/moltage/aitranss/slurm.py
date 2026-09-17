"""Deterministic retry and one-process AITRANSS Slurm scripts."""

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
import re
import shlex

from moltage.aitranss.runtime import (
    AITRANSS_DEFAULT_CPU_THREADS,
    AITRANSS_DEFAULT_MEMORY_GB,
    AITRANSS_DEFAULT_RUNTIME_MINUTES,
    is_aitranss_executable_name,
)
from moltage.domain.calculation_project import ProjectStepKind
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    SlurmExecutionPreset,
    SlurmMailSettings,
    normalize_module_name,
    validate_remote_executable_path,
)
from moltage.remote.runtime_environment import runtime_environment_commands
from moltage.remote.slurm import (
    SlurmSubmissionError,
    controlled_slurm_job_name,
    lsf_scheduler_log_filename,
    render_lsf_resource_directives,
    render_lsf_site_directives,
    render_lsf_mail_directives,
    render_slurm_aitranss_launch_command,
    render_slurm_mail_directives,
    render_slurm_site_directives,
    render_submit_script,
)


class AitranssSlurmError(ValueError):
    """Raised when a transport Slurm script cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class AitranssExecutionSettings:
    """Step-4-only resources; never written back to the FHI-aims preset."""

    cpu_threads: int = AITRANSS_DEFAULT_CPU_THREADS
    runtime_minutes: int = AITRANSS_DEFAULT_RUNTIME_MINUTES
    memory_gb: int = AITRANSS_DEFAULT_MEMORY_GB

    def __post_init__(self) -> None:
        for name in ("cpu_threads", "runtime_minutes", "memory_gb"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise AitranssSlurmError(f"{name} must be a positive integer")


def parse_aitranss_execution_settings(
    script: str | bytes,
) -> AitranssExecutionSettings:
    """Recover the three editable resources from one generated Step-4 script."""

    if isinstance(script, bytes):
        try:
            script = script.decode("utf-8")
        except UnicodeError:
            raise AitranssSlurmError(
                "AITRANSS submit script is not valid UTF-8"
            ) from None
    if not isinstance(script, str):
        raise TypeError("AITRANSS submit script must be text or bytes")
    lines = script.splitlines()

    def one(pattern: re.Pattern[str], label: str) -> re.Match[str]:
        matches = tuple(
            match for line in lines if (match := pattern.fullmatch(line)) is not None
        )
        if len(matches) != 1:
            raise AitranssSlurmError(
                f"AITRANSS submit script requires exactly one valid {label}"
            )
        return matches[0]

    is_lsf = any(line.startswith("#BSUB") for line in lines)
    cpus = int(one(
        re.compile(
            r"#BSUB -n (?P<value>[1-9][0-9]*)"
            if is_lsf
            else r"#SBATCH --cpus-per-task=(?P<value>[1-9][0-9]*)"
        ),
        "LSF slot directive" if is_lsf else "cpus-per-task directive",
    ).group("value"))
    omp = int(one(
        re.compile(r"export OMP_NUM_THREADS=(?P<value>[1-9][0-9]*)"),
        "OMP_NUM_THREADS export",
    ).group("value"))
    if cpus != omp:
        raise AitranssSlurmError(
            "AITRANSS CPU and OMP thread resources do not agree"
        )
    if is_lsf:
        wall = one(
            re.compile(r"#BSUB -W (?P<hours>[0-9]+):(?P<minutes>[0-5][0-9])"),
            "runtime directive",
        )
        runtime_minutes = int(wall.group("hours")) * 60 + int(wall.group("minutes"))
        resource_pattern = re.compile(
            r'#BSUB -R "span\[hosts=1\] '
            r'rusage\[mem=(?P<value>[1-9][0-9]*)G(?:/host)?\]"'
        )
        resource_lines = tuple(
            line for line in lines if line.startswith("#BSUB -R")
        )
        if resource_lines:
            memory_gb = one(
                resource_pattern,
                "LSF memory-reservation directive",
            ).group("value")
        else:
            memory_gb = one(
                re.compile(r"# MOLTAGE_MEMORY_GB=(?P<value>[1-9][0-9]*)"),
                "recorded memory value",
            ).group("value")
    else:
        runtime_minutes = one(
            re.compile(r"#SBATCH --time=(?P<value>[1-9][0-9]*)"),
            "runtime directive",
        ).group("value")
        memory_gb = one(
            re.compile(r"#SBATCH --mem=(?P<value>[1-9][0-9]*)G"),
            "memory directive",
        ).group("value")
    return AitranssExecutionSettings(
        cpu_threads=cpus,
        runtime_minutes=int(runtime_minutes),
        memory_gb=int(memory_gb),
    )


def render_step3_retry_script(
    preset: SlurmExecutionPreset,
    project_id,
    output_filename: str,
    *,
    mail_settings: SlurmMailSettings | None = None,
) -> str:
    """Reuse frozen FHI-aims launch semantics with only retry resources/output."""

    retry_preset = replace(preset, slurm_output_filename=output_filename)
    return render_submit_script(
        retry_preset,
        project_id,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
        mail_settings=mail_settings,
    )


def render_aitranss_submit_script(
    *,
    profile_preset: SlurmExecutionPreset,
    settings: AitranssExecutionSettings,
    project_id,
    executable_path: str,
    aitranss_modules: tuple[str, ...],
    output_filename: str = "aitranss.out",
    mail_settings: SlurmMailSettings | None = None,
    runtime: AitranssRuntimeConfiguration | None = None,
) -> str:
    """Render one Slurm task invoking exactly one verified AITRANSS process."""

    if not isinstance(profile_preset, SlurmExecutionPreset):
        raise TypeError("AITRANSS script requires a saved execution preset")
    if not isinstance(settings, AitranssExecutionSettings):
        raise TypeError("AITRANSS script requires AitranssExecutionSettings")
    modules = tuple(normalize_module_name(module) for module in aitranss_modules)
    environment = None if runtime is None else runtime.environment
    if runtime is not None and (runtime.executable_path != executable_path or runtime.modules != modules):
        raise AitranssSlurmError("AITRANSS script and runtime configuration disagree")
    if not modules and environment is None:
        raise AitranssSlurmError(
            "AITRANSS script requires a configured environment module"
        )
    executable = PurePosixPath(executable_path)
    if (
        not isinstance(executable_path, str)
        or not executable_path.startswith("/")
        or str(executable) != executable_path
        or not is_aitranss_executable_name(executable.name)
    ):
        raise AitranssSlurmError(
            "AITRANSS executable must be a verified absolute path with a "
            "supported AITRANSS executable name"
        )
    validate_remote_executable_path(executable_path, "AITRANSS executable")
    if (
        not output_filename
        or "/" in output_filename
        or "\\" in output_filename
        or output_filename in {".", ".."}
    ):
        raise AitranssSlurmError("AITRANSS Slurm output filename is unsafe")

    if profile_preset.scheduler_kind is SchedulerKind.LSF:
        return _render_lsf_aitranss_script(
            profile_preset=profile_preset,
            settings=settings,
            project_id=project_id,
            executable_path=executable_path,
            output_filename=output_filename,
            environment=environment,
            modules=modules,
            mail_settings=mail_settings,
        )

    lines = [
        "#!/bin/bash -l" if environment is not None else "#!/bin/bash",
        "",
        "#SBATCH --job-name="
        + controlled_slurm_job_name(project_id, ProjectStepKind.TRANSMISSION),
        f"#SBATCH --output={output_filename}",
        *render_slurm_site_directives(profile_preset),
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks=1",
        f"#SBATCH --cpus-per-task={settings.cpu_threads}",
        f"#SBATCH --time={settings.runtime_minutes}",
        f"#SBATCH --mem={settings.memory_gb}G",
    ]
    if profile_preset.no_requeue:
        lines.append("#SBATCH --no-requeue")
    if profile_preset.export_none:
        lines.append("#SBATCH --export=NONE")
    lines.extend(render_slurm_mail_directives(mail_settings))
    lines.append("")
    if profile_preset.unset_slurm_export_env:
        lines.extend(("unset SLURM_EXPORT_ENV", ""))
    if environment is None:
        lines.extend((f"export OMP_NUM_THREADS={settings.cpu_threads}", ""))
        lines.append("module purge")
        lines.extend(f"module load {module}" for module in modules)
    else:
        lines.extend(runtime_environment_commands(environment))
        lines.append(f"export OMP_NUM_THREADS={settings.cpu_threads}")
    lines.append("")
    try:
        launch = render_slurm_aitranss_launch_command(
            profile_preset,
            executable_path,
        )
    except SlurmSubmissionError as error:
        raise AitranssSlurmError(str(error)) from None
    lines.append(launch)
    return "\n".join(lines) + "\n"


def _render_lsf_aitranss_script(
    *,
    profile_preset,
    settings,
    project_id,
    executable_path,
    output_filename,
    environment,
    modules,
    mail_settings,
):
    hours, minutes = divmod(settings.runtime_minutes, 60)
    lines = [
        "#!/bin/bash -l" if environment is not None else "#!/bin/bash",
        "",
        "#BSUB -J "
        + controlled_slurm_job_name(project_id, ProjectStepKind.TRANSMISSION),
        f"#BSUB -oo {lsf_scheduler_log_filename(output_filename)}",
        *render_lsf_site_directives(profile_preset),
        f"#BSUB -n {settings.cpu_threads}",
        f"#BSUB -W {hours}:{minutes:02d}",
        f"# MOLTAGE_MEMORY_GB={settings.memory_gb}",
        *render_lsf_resource_directives(
            profile_preset,
            nodes=1,
            ntasks=settings.cpu_threads,
            memory_gb=settings.memory_gb,
        ),
    ]
    if profile_preset.no_requeue:
        lines.append("#BSUB -rn")
    if profile_preset.export_none:
        lines.append('#BSUB -env "none"')
    lines.extend(render_lsf_mail_directives(mail_settings))
    lines.extend(("", f"exec > {output_filename} 2>&1", ""))
    if environment is None:
        lines.append("module purge")
        lines.extend(f"module load {module}" for module in modules)
    else:
        lines.extend(runtime_environment_commands(environment))
    lines.extend(
        (
            f"export OMP_NUM_THREADS={settings.cpu_threads}",
            "",
            "exec " + shlex.quote(executable_path),
        )
    )
    return "\n".join(lines) + "\n"
