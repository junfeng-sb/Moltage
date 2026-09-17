"""Deterministic Slurm script rendering and strict sbatch result parsing."""

from dataclasses import dataclass, replace
import re
import shlex
from pathlib import PurePosixPath
from uuid import UUID

from moltage.domain.calculation_project import ProjectStepKind
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    LsfResourceRequirementMode,
    SlurmAitranssLaunchMode,
    SlurmExecutionPreset,
    SlurmMailSettings,
    ServerProfileValidationError,
    normalize_scheduler_output_filename,
    runtime_launcher_kind,
)
from moltage.remote.slurm_discovery import (
    validate_sbatch_path,
    validate_scheduler_command_path,
)
from moltage.remote.lsf_environment import render_lsf_client_command
from moltage.remote.runtime_environment import (
    render_configured_fhi_launch,
    runtime_environment_commands,
)


class SlurmSubmissionError(ValueError):
    """Raised when a supported script or scheduler receipt cannot be produced."""


@dataclass(frozen=True, slots=True)
class SlurmSubmissionReceipt:
    job_id: str
    cluster_name: str | None


SLURM_MAIL_TYPES = "END,FAIL"
FHI_AIMS_KILL_ON_BAD_EXIT_OPTION = "--kill-on-bad-exit=1"


def render_slurm_site_directives(
    preset: SlurmExecutionPreset,
) -> tuple[str, ...]:
    """Render only validated, scheduler-qualified Slurm site selectors."""

    if preset.scheduler_kind is not SchedulerKind.SLURM:
        raise SlurmSubmissionError("Slurm site directives require a Slurm preset")
    values = (
        ("account", preset.slurm_account),
        ("partition", preset.slurm_partition),
        ("qos", preset.slurm_qos),
    )
    return tuple(
        f"#SBATCH --{name}={value}"
        for name, value in values
        if value is not None
    )


def render_lsf_site_directives(
    preset: SlurmExecutionPreset,
) -> tuple[str, ...]:
    """Render only validated, scheduler-qualified LSF site selectors."""

    if preset.scheduler_kind is not SchedulerKind.LSF:
        raise SlurmSubmissionError("LSF site directives require an LSF preset")
    values = (
        ("-q", preset.lsf_queue),
        ("-P", preset.lsf_project),
    )
    return tuple(
        f"#BSUB {flag} {value}"
        for flag, value in values
        if value is not None
    )


def render_lsf_resource_directives(
    preset: SlurmExecutionPreset,
    *,
    nodes: int,
    ntasks: int,
    memory_gb: int,
) -> tuple[str, ...]:
    """Render either no LSF ``-R`` or the one approved structured policy."""

    if preset.scheduler_kind is not SchedulerKind.LSF:
        raise SlurmSubmissionError("LSF resources require an LSF preset")
    if preset.lsf_resource_requirement_mode is LsfResourceRequirementMode.SITE_DEFAULT:
        return ()
    if preset.lsf_resource_requirement_mode is not LsfResourceRequirementMode.SPAN_RUSAGE:
        raise SlurmSubmissionError("LSF resource requirement mode is unavailable")
    for label, value in (
        ("execution hosts", nodes),
        ("MPI tasks", ntasks),
        ("memory reservation", memory_gb),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SlurmSubmissionError(f"LSF {label} must be a positive integer")
    if nodes > ntasks or ntasks % nodes != 0:
        raise SlurmSubmissionError(
            "For LSF, MPI tasks must divide evenly across the requested nodes"
        )
    span = "span[hosts=1]" if nodes == 1 else f"span[ptile={ntasks // nodes}]"
    return (f'#BSUB -R "{span} rusage[mem={memory_gb}G]"',)


def render_slurm_aitranss_launch_command(
    preset: SlurmExecutionPreset,
    executable_path: str,
) -> str:
    """Render the explicit Step-4 direct or absolute-srun launch policy."""

    if preset.scheduler_kind is not SchedulerKind.SLURM:
        raise SlurmSubmissionError("Slurm AITRANSS launch requires a Slurm preset")
    executable = shlex.quote(executable_path)
    mode = preset.slurm_aitranss_launch_mode
    if mode is SlurmAitranssLaunchMode.DIRECT:
        return f"exec {executable}"
    if mode is SlurmAitranssLaunchMode.SRUN:
        if preset.slurm_aitranss_srun_path is None:
            raise SlurmSubmissionError(
                "Slurm Step 4 srun mode requires a verified absolute srun executable"
            )
        return (
            f"exec {shlex.quote(preset.slurm_aitranss_srun_path)} "
            f"--ntasks=1 {executable}"
        )
    raise SlurmSubmissionError(
        "Select direct or srun for Slurm Step 4 before submitting"
    )


def render_slurm_mail_directives(
    mail_settings: SlurmMailSettings | None,
) -> tuple[str, ...]:
    """Render the one experimentally accepted Slurm-native mail policy."""

    if mail_settings is None:
        return ()
    if not isinstance(mail_settings, SlurmMailSettings):
        raise SlurmSubmissionError(
            "Slurm mail rendering requires validated SlurmMailSettings"
        )
    return (
        f"#SBATCH --mail-user={mail_settings.recipient}",
        f"#SBATCH --mail-type={SLURM_MAIL_TYPES}",
    )


def render_lsf_mail_directives(
    mail_settings: SlurmMailSettings | None,
) -> tuple[str, ...]:
    """Render the LSF-native equivalent of one terminal notification."""

    if mail_settings is None:
        return ()
    if not isinstance(mail_settings, SlurmMailSettings):
        raise SlurmSubmissionError(
            "LSF mail rendering requires validated SlurmMailSettings"
        )
    return (
        f"#BSUB -u {mail_settings.recipient}",
        "#BSUB -N",
    )


def build_sbatch_submission_command(
    step_directory: str,
    sbatch_path: str,
    script_filename: str = "submit.sh",
    *,
    lsf_env_directory: str | None = None,
    lsf_library_directory: str | None = None,
    lsf_server_directory: str | None = None,
) -> str:
    """Build the one quoted dispatch command from a verified absolute sbatch path."""

    if (
        not isinstance(step_directory, str)
        or not step_directory
        or "\x00" in step_directory
        or "\n" in step_directory
        or "\r" in step_directory
    ):
        raise SlurmSubmissionError("remote step directory must be safe POSIX text")
    command_name = PurePosixPath(sbatch_path).name
    if command_name == "sbatch":
        verified_path = validate_sbatch_path(sbatch_path)
    elif command_name == "bsub":
        verified_path = validate_scheduler_command_path(sbatch_path, "bsub")
    else:
        raise SlurmSubmissionError(
            "scheduler submit command must end in /sbatch or /bsub"
        )
    if _SAFE_FILENAME.fullmatch(script_filename) is None:
        raise SlurmSubmissionError("submit script filename must be one safe filename")
    if command_name == "bsub":
        if any(
            value is None
            for value in (
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            )
        ):
            raise SlurmSubmissionError(
                "LSF submission requires the configuration, library, and "
                "server directories"
            )
        try:
            dispatch = render_lsf_client_command(
                f"{shlex.quote(verified_path)} "
                f"-cwd {shlex.quote(step_directory)} "
                f"< {shlex.quote(script_filename)}",
                env_directory=lsf_env_directory,
                bin_directory=str(PurePosixPath(verified_path).parent),
                library_directory=lsf_library_directory,
                server_directory=lsf_server_directory,
            )
        except (TypeError, ValueError) as error:
            raise SlurmSubmissionError(str(error)) from None
        return (
            f"cd {shlex.quote(step_directory)} && "
            f"{dispatch}"
        )
    if any(
        value is not None
        for value in (
            lsf_env_directory,
            lsf_library_directory,
            lsf_server_directory,
        )
    ):
        raise SlurmSubmissionError(
            "LSF client directories cannot be applied to sbatch"
        )
    return (
        f"cd {shlex.quote(step_directory)} && "
        f"{shlex.quote(verified_path)} --parsable {shlex.quote(script_filename)}"
    )


def controlled_slurm_job_name(
    project_id: UUID,
    step_kind: ProjectStepKind,
) -> str:
    """Return an ASCII-only scheduler label derived from stable identities."""

    if not isinstance(project_id, UUID):
        raise SlurmSubmissionError("Scheduler job naming requires a project UUID")
    suffix = _step_suffix(step_kind)
    return f"AT-{project_id.hex[:8].upper()}-{suffix}"


def render_submit_script(
    preset: SlurmExecutionPreset,
    project_id: UUID,
    step_kind: ProjectStepKind,
    *,
    mail_settings: SlurmMailSettings | None = None,
) -> str:
    """Render the accepted execution preset with LF endings and final newline."""

    if not isinstance(preset, SlurmExecutionPreset):
        raise SlurmSubmissionError(
            "submit.sh rendering requires configured Cluster Execution Settings"
        )
    job_name = controlled_slurm_job_name(project_id, step_kind)
    lines, launch = render_fhi_batch_parts(preset, job_name, mail_settings)
    return "\n".join((*lines, launch)) + "\n"


def render_fhi_batch_parts(preset, job_name, mail_settings=None):
    """Shared allocation/environment; the caller controls foreground invocations."""
    if not re.fullmatch(r"AT-[A-Z0-9-]+", job_name):
        raise SlurmSubmissionError("Invalid controlled job name")
    if preset.scheduler_kind is SchedulerKind.LSF:
        return _render_lsf_fhi_batch_parts(preset, job_name, mail_settings)
    output_filename = _required_output_filename(preset)
    lines = [
        "#!/bin/bash -l" if preset.fhi_runtime is not None else "#!/bin/bash",
        "",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --output={output_filename}",
        *render_slurm_site_directives(preset),
        f"#SBATCH --nodes={preset.nodes}",
        f"#SBATCH --ntasks={preset.ntasks}",
        f"#SBATCH --cpus-per-task={preset.cpus_per_task}",
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
    if preset.fhi_runtime is not None:
        lines.extend(runtime_environment_commands(preset.fhi_runtime.environment))
        lines.extend((f"export OMP_NUM_THREADS={preset.omp_num_threads}", ""))
        launch = render_configured_fhi_launch(preset.fhi_runtime, preset.ntasks)
    else:
        lines.extend((f"export OMP_NUM_THREADS={preset.omp_num_threads}", ""))
        if preset.module_purge:
            lines.append("module purge")
        lines.extend(f"module load {module}" for module in preset.modules)
        if preset.module_purge or preset.modules:
            lines.append("")
        launch = render_fhi_aims_launch_command(preset.launch_command)
    return lines, launch


def _render_lsf_fhi_batch_parts(preset, job_name, mail_settings=None):
    """Render the reviewed LSF resource model using explicit units."""

    if preset.fhi_runtime is None:
        raise SlurmSubmissionError(
            "LSF submission requires a resolved FHI-aims executable, MPI launcher, "
            "and runtime environment"
        )
    if runtime_launcher_kind(preset.fhi_runtime.launcher_path) != "mpirun":
        raise SlurmSubmissionError(
            "LSF submission requires an explicitly verified mpirun launcher"
        )
    if preset.cpus_per_task != 1:
        raise SlurmSubmissionError(
            "LSF submission currently requires CPUs per task to be 1"
        )
    hours, minutes = divmod(preset.runtime_minutes, 60)
    output_filename = _required_output_filename(preset)
    lines = [
        "#!/bin/bash -l",
        "",
        f"#BSUB -J {job_name}",
        f"#BSUB -oo {lsf_scheduler_log_filename(output_filename)}",
        *render_lsf_site_directives(preset),
        f"#BSUB -n {preset.ntasks}",
        f"#BSUB -W {hours}:{minutes:02d}",
        *render_lsf_resource_directives(
            preset,
            nodes=preset.nodes,
            ntasks=preset.ntasks,
            memory_gb=preset.memory_gb,
        ),
        f"# MOLTAGE_NODES={preset.nodes}",
        "# MOLTAGE_CPUS_PER_TASK=1",
    ]
    if preset.no_requeue:
        lines.append("#BSUB -rn")
    if preset.export_none:
        lines.append('#BSUB -env "none"')
    lines.extend(render_lsf_mail_directives(mail_settings))
    lines.extend(("", f"exec > {output_filename} 2>&1", ""))
    lines.extend(runtime_environment_commands(preset.fhi_runtime.environment))
    lines.extend((f"export OMP_NUM_THREADS={preset.omp_num_threads}", ""))
    return lines, render_configured_fhi_launch(
        preset.fhi_runtime, preset.ntasks
    )


def render_fhi_aims_launch_command(launch_command: str) -> str:
    """Add the one fixed fail-fast option to a configured FHI-aims srun line."""

    try:
        tokens = shlex.split(launch_command, posix=True)
    except (TypeError, ValueError):
        raise SlurmSubmissionError(
            "FHI-aims launch command must be a valid srun command"
        ) from None
    if not tokens or tokens[0] != "srun":
        raise SlurmSubmissionError(
            "FHI-aims launch command must begin with srun"
        )

    kill_options = tuple(
        token
        for token in tokens[1:]
        if token == "--kill-on-bad-exit"
        or token.startswith("--kill-on-bad-exit=")
        or token.startswith("-K")
    )
    if kill_options:
        if kill_options == (FHI_AIMS_KILL_ON_BAD_EXIT_OPTION,):
            return launch_command
        raise SlurmSubmissionError(
            "FHI-aims launch command must contain exactly one "
            f"{FHI_AIMS_KILL_ON_BAD_EXIT_OPTION} option"
        )

    match = _LEADING_SRUN.fullmatch(launch_command)
    if match is None:
        raise SlurmSubmissionError(
            "FHI-aims launch command must begin with canonical srun text"
        )
    return (
        "srun "
        f"{FHI_AIMS_KILL_ON_BAD_EXIT_OPTION}"
        f"{match.group('remainder')}"
    )


def parse_sbatch_parsable_output(output: str | bytes) -> SlurmSubmissionReceipt:
    """Accept only the canonical Slurm or LSF submission receipt."""

    if isinstance(output, bytes):
        try:
            output = output.decode("utf-8")
        except UnicodeError:
            raise SlurmSubmissionError(
                "sbatch --parsable output is not valid UTF-8"
            ) from None
    if not isinstance(output, str):
        raise SlurmSubmissionError("sbatch --parsable output must be text or bytes")
    normalized = output.strip()
    match = _SBATCH_RECEIPT.fullmatch(normalized)
    if match is not None:
        return SlurmSubmissionReceipt(
            job_id=match.group("job_id"),
            cluster_name=match.group("cluster_name"),
        )
    lsf_match = _BSUB_RECEIPT.fullmatch(normalized)
    if lsf_match is None:
        raise SlurmSubmissionError(
            "scheduler returned an unsupported submission receipt"
        )
    return SlurmSubmissionReceipt(
        job_id=lsf_match.group("job_id"),
        cluster_name=None,
    )


def parse_submit_script_output_filename(script: str | bytes) -> str:
    """Recover the one safe output filename emitted by the accepted builder."""

    if isinstance(script, bytes):
        try:
            script = script.decode("utf-8")
        except UnicodeError:
            raise SlurmSubmissionError("submitted submit.sh is not valid UTF-8") from None
    if not isinstance(script, str):
        raise SlurmSubmissionError("submitted submit.sh must be text or bytes")
    is_lsf = any(line.startswith("#BSUB") for line in script.splitlines())
    pattern = _LSF_OUTPUT_DIRECTIVE if is_lsf else _OUTPUT_DIRECTIVE
    matches = tuple(
        match.group("filename")
        for line in script.splitlines()
        if (match := pattern.fullmatch(line)) is not None
    )
    active_output_lines = tuple(
        line
        for line in script.splitlines()
        if (
            (
                is_lsf
                and line.startswith("#BSUB")
                and re.match(r"#BSUB\s+-oo?(?:\s|$)", line)
            )
            or (not is_lsf and line.startswith("#SBATCH") and "--output" in line)
        )
    )
    if len(matches) != 1 or len(active_output_lines) != 1:
        raise SlurmSubmissionError(
            "submitted submit.sh must contain exactly one valid scheduler output directive"
        )
    output_filename = matches[0]
    if is_lsf:
        redirect_matches = tuple(
            match.group("filename")
            for line in script.splitlines()
            if (match := _LSF_APPLICATION_OUTPUT_REDIRECT.fullmatch(line)) is not None
        )
        active_redirects = tuple(
            line
            for line in script.splitlines()
            if re.match(r"exec[ \t]+>", line) is not None
        )
        if active_redirects:
            if len(active_redirects) != 1 or len(redirect_matches) != 1:
                raise SlurmSubmissionError(
                    "submitted LSF script must contain exactly one valid application "
                    "output redirect"
                )
            output_filename = redirect_matches[0]
            if matches[0] != lsf_scheduler_log_filename(output_filename):
                raise SlurmSubmissionError(
                    "submitted LSF scheduler log does not match its application "
                    "output filename"
                )
    try:
        return normalize_scheduler_output_filename(output_filename)
    except ServerProfileValidationError as error:
        raise SlurmSubmissionError(
            "submitted scheduler output filename is invalid: " + str(error)
        ) from None


def lsf_scheduler_log_filename(output_filename: str) -> str:
    """Derive the separate LSF report log from one application output name."""

    try:
        normalized = normalize_scheduler_output_filename(output_filename)
        return normalize_scheduler_output_filename(normalized + ".lsf.log")
    except ServerProfileValidationError as error:
        raise SlurmSubmissionError(
            "LSF application output filename is invalid: " + str(error)
        ) from None


def _required_output_filename(preset: SlurmExecutionPreset) -> str:
    try:
        return normalize_scheduler_output_filename(
            preset.slurm_output_filename,
        )
    except ServerProfileValidationError as error:
        raise SlurmSubmissionError(
            "Configure a valid Output file name before submitting: " + str(error)
        ) from None


def preset_with_submit_script_resources(
    base_preset: SlurmExecutionPreset,
    script: str | bytes,
) -> SlurmExecutionPreset:
    """Overlay one rendered attempt's editable resources on current fixed policy."""

    if not isinstance(base_preset, SlurmExecutionPreset):
        raise SlurmSubmissionError(
            "submitted resource recovery requires a SlurmExecutionPreset"
        )
    if isinstance(script, bytes):
        try:
            script = script.decode("utf-8")
        except UnicodeError:
            raise SlurmSubmissionError(
                "submitted submit script is not valid UTF-8"
            ) from None
    if not isinstance(script, str):
        raise SlurmSubmissionError(
            "submitted submit script must be text or bytes"
        )

    lines = script.splitlines()
    if any(line.startswith("#BSUB") for line in lines):
        return _preset_with_lsf_submit_script_resources(base_preset, lines)
    values: dict[str, int] = {}
    for field_name, prefix, pattern in _RESOURCE_DIRECTIVES:
        candidates = tuple(line for line in lines if line.startswith(prefix))
        matches = tuple(
            match
            for line in candidates
            if (match := pattern.fullmatch(line)) is not None
        )
        if len(candidates) != 1 or len(matches) != 1:
            raise SlurmSubmissionError(
                "submitted submit script must contain exactly one valid "
                f"{prefix} resource setting"
            )
        values[field_name] = int(matches[0].group("value"))

    return replace(base_preset, **values)


def _preset_with_lsf_submit_script_resources(base_preset, lines):
    if base_preset.scheduler_kind is not SchedulerKind.LSF:
        raise SlurmSubmissionError(
            "submitted LSF resources require the current LSF profile"
        )
    values = {}

    def one(pattern, label):
        matches = tuple(
            match for line in lines if (match := pattern.fullmatch(line)) is not None
        )
        if len(matches) != 1:
            raise SlurmSubmissionError(
                f"submitted LSF script requires exactly one valid {label}"
            )
        return matches[0]

    values["nodes"] = int(
        one(re.compile(r"# MOLTAGE_NODES=(?P<value>[1-9][0-9]*)"), "node record").group("value")
    )
    values["cpus_per_task"] = int(
        one(re.compile(r"# MOLTAGE_CPUS_PER_TASK=(?P<value>[1-9][0-9]*)"), "CPU-per-task record").group("value")
    )
    values["ntasks"] = int(
        one(re.compile(r"#BSUB -n (?P<value>[1-9][0-9]*)"), "task directive").group("value")
    )
    wall = one(
        re.compile(r"#BSUB -W (?P<hours>[0-9]+):(?P<minutes>[0-5][0-9])"),
        "runtime directive",
    )
    values["runtime_minutes"] = int(wall.group("hours")) * 60 + int(wall.group("minutes"))
    nodes = values["nodes"]
    tasks = values["ntasks"]
    resource_lines = tuple(line for line in lines if line.startswith("#BSUB -R"))
    if (
        base_preset.lsf_resource_requirement_mode
        is LsfResourceRequirementMode.SPAN_RUSAGE
    ):
        resource = one(
            re.compile(
                r'#BSUB -R "(?P<span>span\[(?:hosts=1|ptile=[1-9][0-9]*)\]) '
                r'rusage\[mem=(?P<value>[1-9][0-9]*)G(?:/host)?\]"'
            ),
            "host placement and memory-reservation directive",
        )
        values["memory_gb"] = int(resource.group("value"))
        if nodes > tasks or tasks % nodes != 0:
            raise SlurmSubmissionError(
                "submitted LSF script has incompatible host and MPI task records"
            )
        expected_span = (
            "span[hosts=1]"
            if nodes == 1
            else f"span[ptile={tasks // nodes}]"
        )
        if resource.group("span") != expected_span:
            raise SlurmSubmissionError(
                "submitted LSF script host placement does not match its recorded resources"
            )
    elif resource_lines:
        raise SlurmSubmissionError(
            "submitted LSF site-default script must not contain #BSUB -R"
        )
    values["omp_num_threads"] = int(
        one(re.compile(r"export OMP_NUM_THREADS=(?P<value>[1-9][0-9]*)"), "OMP thread export").group("value")
    )
    return replace(base_preset, **values)


def _step_suffix(step_kind: ProjectStepKind) -> str:
    if step_kind is ProjectStepKind.MOLECULE_OPT:
        return "S1"
    if step_kind is ProjectStepKind.MOLECULE_AU_OPT:
        return "S2"
    if step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE:
        return "S3"
    if step_kind is ProjectStepKind.TRANSMISSION:
        return "S4"
    raise SlurmSubmissionError(
        "submit.sh is supported only for Step 1, Step 2, or Step 3"
    )


_SBATCH_RECEIPT = re.compile(
    r"(?P<job_id>[0-9]+)(?:;(?P<cluster_name>[A-Za-z0-9][A-Za-z0-9._-]*))?"
)
_BSUB_RECEIPT = re.compile(
    r"Job <(?P<job_id>[0-9]+)> is submitted to (?:default )?queue <[A-Za-z0-9][A-Za-z0-9._-]*>\."
)
_OUTPUT_DIRECTIVE = re.compile(
    r"#SBATCH --output=(?P<filename>[A-Za-z0-9][A-Za-z0-9._-]*)"
)
_LSF_OUTPUT_DIRECTIVE = re.compile(
    r"#BSUB -oo? (?P<filename>[A-Za-z0-9][A-Za-z0-9._-]*)"
)
_LSF_APPLICATION_OUTPUT_REDIRECT = re.compile(
    r"exec > (?P<filename>[A-Za-z0-9][A-Za-z0-9._-]*) 2>&1"
)
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LEADING_SRUN = re.compile(r"srun(?P<remainder>[ \t]+.+)")
_POSITIVE_INTEGER = r"(?P<value>[1-9][0-9]*)"
_RESOURCE_DIRECTIVES = (
    (
        "nodes",
        "#SBATCH --nodes",
        re.compile(rf"#SBATCH --nodes={_POSITIVE_INTEGER}"),
    ),
    (
        "ntasks",
        "#SBATCH --ntasks",
        re.compile(rf"#SBATCH --ntasks={_POSITIVE_INTEGER}"),
    ),
    (
        "cpus_per_task",
        "#SBATCH --cpus-per-task",
        re.compile(rf"#SBATCH --cpus-per-task={_POSITIVE_INTEGER}"),
    ),
    (
        "runtime_minutes",
        "#SBATCH --time",
        re.compile(rf"#SBATCH --time={_POSITIVE_INTEGER}"),
    ),
    (
        "memory_gb",
        "#SBATCH --mem",
        re.compile(rf"#SBATCH --mem={_POSITIVE_INTEGER}G"),
    ),
    (
        "omp_num_threads",
        "export OMP_NUM_THREADS",
        re.compile(rf"export OMP_NUM_THREADS={_POSITIVE_INTEGER}"),
    ),
)
