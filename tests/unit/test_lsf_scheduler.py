from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

from moltage.app.server_profiles import ServerProfileRepository
from moltage.app.density_workflow import render_density_script
from moltage.aitranss.slurm import (
    AitranssExecutionSettings,
    parse_aitranss_execution_settings,
    render_aitranss_submit_script,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    FhiAimsRuntimeConfiguration,
    LsfResourceRequirementMode,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    SlurmExecutionPreset,
    SlurmMailSettings,
)
from moltage.remote.executor import RemoteCommandResult
from moltage.remote.scheduler_discovery import (
    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
    LOGIN_SHELL_DISCOVERY_COMMAND,
    discover_scheduler,
    resolve_scheduler_for_submission,
    verify_scheduler_directory,
)
from moltage.remote.slurm import (
    build_sbatch_submission_command,
    parse_sbatch_parsable_output,
    parse_submit_script_output_filename,
    preset_with_submit_script_resources,
    render_submit_script,
)
from moltage.remote.slurm_cancel import build_scancel_command
from moltage.remote.slurm_status import (
    SchedulerStatusKind,
    build_bhist_status_command,
    build_bjobs_status_command,
    parse_bhist_output,
    query_slurm_job_status,
)
from moltage.remote.project_manifest import (
    parse_project_manifest,
    serialize_project_manifest,
)
from phase2b1_test_support import example_project, profile
from synthetic_test_data import SYNTHETIC_JOB_ID, SYNTHETIC_PROJECT_NAME


def result(status=0, stdout=b"", stderr=b""):
    return RemoteCommandResult(status, stdout, stderr)


class ScriptedExecutor:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        expected, outcome = self.outcomes.pop(0)
        if expected is not None and command != expected:
            raise AssertionError(f"expected {expected!r}, received {command!r}")
        return outcome


def lsf_preset():
    environment = RuntimeEnvironment(
        RuntimeEnvironmentMode.MODULES,
        ("mpi/example-1.0", "math/mkl-example-1.0"),
    )
    runtime = FhiAimsRuntimeConfiguration(
        "/apps/fhi-aims/bin/aims.cluster.scalapack.mpi.x",
        "/srv/moltage-test/apps/mpi/bin/mpirun",
        environment,
    )
    return SlurmExecutionPreset(
        nodes=2,
        ntasks=64,
        cpus_per_task=1,
        runtime_minutes=150,
        memory_gb=96,
        unset_slurm_export_env=False,
        omp_num_threads=1,
        modules=environment.modules,
        launch_command="mpirun -n 64 aims.cluster.scalapack.mpi.x",
        slurm_output_filename="aims.out",
        lsf_env_directory="/srv/moltage-test/lsf/conf",
        lsf_library_directory="/srv/moltage-test/lsf/current/lib",
        lsf_server_directory="/srv/moltage-test/lsf/current/etc",
        fhi_runtime=runtime,
        scheduler_kind=SchedulerKind.LSF,
        lsf_resource_requirement_mode=LsfResourceRequirementMode.SPAN_RUSAGE,
    )


class LsfSchedulerTests(unittest.TestCase):
    def test_lsf_site_selectors_apply_to_fhi_and_aitranss_only_when_configured(self):
        preset = replace(
            lsf_preset(),
            lsf_queue="normal",
            lsf_project="project-a",
        )
        project_id = UUID("11111111-1111-4111-8111-111111111111")
        fhi = render_submit_script(
            preset,
            project_id,
            ProjectStepKind.MOLECULE_OPT,
        )
        runtime = AitranssRuntimeConfiguration(
            modules=preset.fhi_runtime.environment.modules,
            executable_path="/apps/fhi-aims/scripts/aitranss.x",
            environment=preset.fhi_runtime.environment,
        )
        aitranss = render_aitranss_submit_script(
            profile_preset=preset,
            settings=AitranssExecutionSettings(),
            project_id=project_id,
            executable_path=runtime.executable_path,
            aitranss_modules=runtime.modules,
            runtime=runtime,
        )
        for script in (fhi, aitranss):
            self.assertIn("#BSUB -q normal\n", script)
            self.assertIn("#BSUB -P project-a\n", script)

        blank = render_submit_script(
            replace(preset, lsf_queue="", lsf_project=""),
            project_id,
            ProjectStepKind.MOLECULE_OPT,
        )
        self.assertNotIn("#BSUB -q", blank)
        self.assertNotIn("#BSUB -P", blank)

    def test_lsf_site_default_omits_resource_expression_and_remains_recoverable(self):
        preset = replace(
            lsf_preset(),
            nodes=7,
            ntasks=8,
            lsf_resource_requirement_mode=LsfResourceRequirementMode.SITE_DEFAULT,
        )
        project_id = UUID("11111111-1111-4111-8111-111111111111")
        fhi = render_submit_script(
            preset,
            project_id,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        self.assertNotIn("#BSUB -R", fhi)
        recovered = preset_with_submit_script_resources(preset, fhi)
        self.assertEqual((recovered.nodes, recovered.ntasks), (7, 8))
        self.assertEqual(recovered.memory_gb, preset.memory_gb)
        self.assertIs(
            recovered.lsf_resource_requirement_mode,
            LsfResourceRequirementMode.SITE_DEFAULT,
        )

        runtime = AitranssRuntimeConfiguration(
            modules=preset.fhi_runtime.environment.modules,
            executable_path="/apps/fhi-aims/scripts/aitranss.x",
            environment=preset.fhi_runtime.environment,
        )
        settings = AitranssExecutionSettings(
            cpu_threads=8,
            runtime_minutes=45,
            memory_gb=12,
        )
        aitranss = render_aitranss_submit_script(
            profile_preset=preset,
            settings=settings,
            project_id=project_id,
            executable_path=runtime.executable_path,
            aitranss_modules=runtime.modules,
            runtime=runtime,
        )
        self.assertNotIn("#BSUB -R", aitranss)
        self.assertIn("# MOLTAGE_MEMORY_GB=12", aitranss)
        self.assertEqual(parse_aitranss_execution_settings(aitranss), settings)

    def test_bounded_detection_requires_complete_lsf_command_set_without_jobs(self):
        lookup = (
            b"__MOLTAGE_SCHEDULER__=bsub|"
            b"/srv/moltage-test/lsf/current/bin/bsub|/srv/moltage-test/lsf/conf|"
            b"/srv/moltage-test/lsf/conf|/srv/moltage-test/lsf/current/bin|"
            b"/srv/moltage-test/lsf/current/lib|/srv/moltage-test/lsf/current/etc\n"
        )
        executor = ScriptedExecutor(
            (
                (CURRENT_ENVIRONMENT_DISCOVERY_COMMAND, result(stdout=lookup)),
                (
                    None,
                    result(
                        stdout=(
                            b"IBM Spectrum LSF synthetic-test\n"
                            b"__MOLTAGE_LSF_ENVDIR__=/srv/moltage-test/lsf/conf|"
                            b"/srv/moltage-test/lsf/current/lib|"
                            b"/srv/moltage-test/lsf/current/etc\n"
                        )
                    ),
                ),
            )
        )

        discovered = discover_scheduler(executor)

        self.assertIs(discovered.scheduler_kind, SchedulerKind.LSF)
        self.assertEqual(discovered.sbatch_path, "/srv/moltage-test/lsf/current/bin/bsub")
        self.assertEqual(len(executor.commands), 2)
        verification = executor.commands[1]
        for command_name in ("bsub", "bjobs", "bhist", "bkill", "lsid"):
            self.assertIn("/srv/moltage-test/lsf/current/bin/" + command_name, verification)
        self.assertIn("LSF_ENVDIR=/srv/moltage-test/lsf/conf", verification)
        self.assertIn("LSF_BINDIR=/srv/moltage-test/lsf/current/bin", verification)
        self.assertIn("LSF_LIBDIR=/srv/moltage-test/lsf/current/lib", verification)
        self.assertIn("LSF_SERVERDIR=/srv/moltage-test/lsf/current/etc", verification)
        self.assertIn("/srv/moltage-test/lsf/current/bin/lsid", verification)
        self.assertEqual(discovered.lsf_env_directory, "/srv/moltage-test/lsf/conf")
        self.assertEqual(
            discovered.lsf_server_directory, "/srv/moltage-test/lsf/current/etc"
        )
        self.assertNotIn("bjobs -a", verification)
        self.assertNotIn("jobid stat exit_code", verification)

    def test_detection_uses_standard_lsf_environment_path_hints(self):
        self.assertIn("LSF_BINDIR", CURRENT_ENVIRONMENT_DISCOVERY_COMMAND)
        self.assertIn("LSF_SERVERDIR", CURRENT_ENVIRONMENT_DISCOVERY_COMMAND)

    def test_cached_lsf_path_derives_and_validates_config_before_dispatch(self):
        preset = replace(
            lsf_preset(),
            slurm_bin_directory=(
                "/srv/moltage-test/lsf/current/linux-x86_64/bin"
            ),
            lsf_env_directory=None,
            lsf_library_directory=None,
            lsf_server_directory=None,
        )
        executor = ScriptedExecutor(
            (
                (
                    None,
                    result(
                        stdout=(
                            b"__MOLTAGE_LSF_ATTEMPT__=/srv/moltage-test/lsf/conf|"
                            b"/srv/moltage-test/lsf/current/linux-x86_64/lib|"
                            b"/srv/moltage-test/lsf/current/linux-x86_64/etc\n"
                            b"IBM Spectrum LSF synthetic-test\n"
                            b"__MOLTAGE_LSF_ENVDIR__=/srv/moltage-test/lsf/conf|"
                            b"/srv/moltage-test/lsf/current/linux-x86_64/lib|"
                            b"/srv/moltage-test/lsf/current/linux-x86_64/etc\n"
                        )
                    ),
                ),
            )
        )

        resolved = resolve_scheduler_for_submission(executor, preset)
        dispatch = build_sbatch_submission_command(
            "/remote/project",
            resolved.sbatch_path,
            lsf_env_directory=resolved.lsf_env_directory,
            lsf_library_directory=resolved.lsf_library_directory,
            lsf_server_directory=resolved.lsf_server_directory,
        )

        verification = executor.commands[0]
        self.assertEqual(resolved.version_text, "IBM Spectrum LSF synthetic-test")
        self.assertIn("/srv/moltage-test/lsf/conf/lsf.conf", verification)
        self.assertIn("/srv/moltage-test/lsf/current/linux-x86_64/bin/lsid", verification)
        self.assertNotIn("bsub -V", verification)
        self.assertEqual(
            dispatch,
            "cd /remote/project && env LSF_ENVDIR=/srv/moltage-test/lsf/conf "
            "LSF_BINDIR=/srv/moltage-test/lsf/current/linux-x86_64/bin "
            "LSF_LIBDIR=/srv/moltage-test/lsf/current/linux-x86_64/lib "
            "LSF_SERVERDIR=/srv/moltage-test/lsf/current/linux-x86_64/etc "
            '"PATH=/srv/moltage-test/lsf/current/linux-x86_64/bin:'
            '/srv/moltage-test/lsf/current/linux-x86_64/etc${PATH:+:$PATH}" '
            '"LD_LIBRARY_PATH=/srv/moltage-test/lsf/current/linux-x86_64/lib'
            '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" '
            "/srv/moltage-test/lsf/current/linux-x86_64/bin/bsub "
            "-cwd /remote/project < submit.sh",
        )

    def test_found_lsf_candidate_reports_verification_failure(self):
        lookup = (
            b"__MOLTAGE_SCHEDULER__=bsub|"
            b"/broken/lsf/bin/bsub|||||\n"
        )
        executor = ScriptedExecutor(
            (
                (CURRENT_ENVIRONMENT_DISCOVERY_COMMAND, result(stdout=lookup)),
                (None, result(status=125)),
                (LOGIN_SHELL_DISCOVERY_COMMAND, result()),
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "LSF candidate /broken/lsf/bin/bsub.*does not contain an executable",
        ):
            discover_scheduler(executor)

    def test_lsid_failure_reports_the_remote_initialization_reason(self):
        executor = ScriptedExecutor(
            (
                (
                    None,
                    result(
                        status=126,
                        stderr=(
                            b"ls_initdebug: Bad configuration environment, "
                            b"something missing in lsf.conf?\n"
                        ),
                    ),
                ),
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "lsid rejected.*Bad configuration environment",
        ):
            verify_scheduler_directory(
                executor,
                "/lsf/bin",
                SchedulerKind.LSF,
                lsf_env_directory="/lsf/conf",
                lsf_library_directory="/lsf/lib",
                lsf_server_directory="/lsf/etc",
            )

    def test_lsf_script_uses_explicit_units_and_structured_runtime(self):
        script = render_submit_script(
            lsf_preset(),
            UUID("11111111-1111-4111-8111-111111111111"),
            ProjectStepKind.MOLECULE_OPT,
            mail_settings=SlurmMailSettings("scientist@example.org"),
        )

        self.assertIn("#BSUB -n 64", script)
        self.assertIn("#BSUB -W 2:30", script)
        self.assertNotIn("#BSUB -M", script)
        self.assertIn('span[ptile=32] rusage[mem=96G]', script)
        self.assertIn("#BSUB -rn", script)
        self.assertIn('#BSUB -env "none"', script)
        self.assertIn("#BSUB -u scientist@example.org", script)
        self.assertIn("#BSUB -N", script)
        self.assertIn("#BSUB -oo aims.out.lsf.log", script)
        self.assertIn("exec > aims.out 2>&1", script)
        self.assertNotIn("SLURM_EXPORT_ENV", script)
        self.assertIn("module load mpi/example-1.0", script)
        self.assertIn("exec /srv/moltage-test/apps/mpi/bin/mpirun -n 64", script)
        self.assertEqual(parse_submit_script_output_filename(script), "aims.out")

    def test_lsf_output_is_a_filename_not_a_path(self):
        with self.assertRaises(ValueError):
            replace(
                lsf_preset(),
                slurm_output_filename="/srv/moltage-test/users/scientist/job-logs/aims.out",
            )

    def test_lsf_dispatch_and_receipt_are_strict(self):
        command = build_sbatch_submission_command(
            "/remote/work place/task",
            "/srv/moltage-test/lsf/manual/bin/bsub",
            lsf_env_directory="/srv/moltage-test/lsf/conf",
            lsf_library_directory="/srv/moltage-test/lsf/manual/lib",
            lsf_server_directory="/srv/moltage-test/lsf/manual/etc",
        )
        self.assertEqual(
            command,
            "cd '/remote/work place/task' && env "
            "LSF_ENVDIR=/srv/moltage-test/lsf/conf "
            "LSF_BINDIR=/srv/moltage-test/lsf/manual/bin "
            "LSF_LIBDIR=/srv/moltage-test/lsf/manual/lib "
            "LSF_SERVERDIR=/srv/moltage-test/lsf/manual/etc "
            '"PATH=/srv/moltage-test/lsf/manual/bin:/srv/moltage-test/lsf/manual/etc${PATH:+:$PATH}" '
            '"LD_LIBRARY_PATH=/srv/moltage-test/lsf/manual/lib'
            '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" '
            "/srv/moltage-test/lsf/manual/bin/bsub -cwd '/remote/work place/task' < submit.sh",
        )
        receipt = parse_sbatch_parsable_output(
            "Job <12345> is submitted to queue <normal>.\n"
        )
        self.assertEqual(receipt.job_id, "12345")
        self.assertIsNone(receipt.cluster_name)

    def test_density_and_aitranss_use_their_lsf_execution_models(self):
        preset = lsf_preset()
        task_id = UUID("11111111-1111-4111-8111-111111111111")
        density = render_density_script(
            preset,
            task_id,
            {
                "total": "/remote/density/total",
                "subset1": "/remote/density/subset1",
                "subset2": "/remote/density/subset2",
            },
        )
        self.assertIn("#BSUB -n 64", density)
        self.assertIn("#BSUB -oo aims.out.lsf.log", density)
        self.assertIn("exec > aims.out 2>&1", density)
        self.assertEqual(density.count("exec /srv/moltage-test/apps/mpi/bin/mpirun -n 64"), 3)
        self.assertNotIn("#SBATCH", density)

        environment = preset.fhi_runtime.environment
        aitranss_runtime = AitranssRuntimeConfiguration(
            modules=environment.modules,
            executable_path="/apps/fhi-aims/scripts/aitranss.x",
            environment=environment,
        )
        transmission = render_aitranss_submit_script(
            profile_preset=preset,
            settings=AitranssExecutionSettings(
                cpu_threads=8,
                runtime_minutes=45,
                memory_gb=12,
            ),
            project_id=task_id,
            executable_path=aitranss_runtime.executable_path,
            aitranss_modules=aitranss_runtime.modules,
            runtime=aitranss_runtime,
        )
        self.assertIn("#BSUB -n 8", transmission)
        self.assertIn('span[hosts=1] rusage[mem=12G]', transmission)
        self.assertIn("export OMP_NUM_THREADS=8", transmission)
        self.assertIn("#BSUB -oo aitranss.out.lsf.log", transmission)
        self.assertIn("exec > aitranss.out 2>&1", transmission)
        self.assertIn("exec /apps/fhi-aims/scripts/aitranss.x", transmission)
        self.assertNotIn("mpirun", transmission)

    def test_lsf_status_uses_bjobs_then_bhist_terminal_record(self):
        bjobs = build_bjobs_status_command(
            "/lsf/bin/bjobs", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        bhist = build_bhist_status_command(
            "/lsf/bin/bhist", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        executor = ScriptedExecutor(
            (
                (bjobs, result(stdout=b"No job found\n")),
                (
                    bhist,
                    result(
                        stdout=(
                            b"Job <12345>, User <scientist>, Queue <normal>\n"
                            b"Tue Sep  8 12:00:00: Done successfully.\n"
                        )
                    ),
                ),
            )
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/lsf/bin/bjobs",
            sacct_path="/lsf/bin/bhist",
            job_id="12345",
            profile_username="scientist",
            lsf_env_directory="/lsf/conf",
            lsf_library_directory="/lsf/lib",
            lsf_server_directory="/lsf/etc",
        )

        self.assertIs(status.kind, SchedulerStatusKind.COMPLETED)
        self.assertEqual(status.scheduler_state, "DONE")
        self.assertEqual(status.exit_code, "0:0")
        self.assertNotIn("scientist", bjobs)
        self.assertTrue(bjobs.endswith(" 12345"))
        self.assertIn("/lsf/bin/bhist -n 10 -l 12345", bhist)

    def test_lsf_rotated_history_preserves_success_and_timeout_results(self):
        cases = (
            (
                b"Job <12345>, User <scientist>\n"
                b"Wed Sep  9 16:46:29: Done successfully.\n",
                SchedulerStatusKind.COMPLETED,
                "DONE",
                "0:0",
            ),
            (
                b"Job <12345>, User <scientist>\n"
                b"Tue Sep  8 22:45:17: Exited with exit code 140.\n"
                b"Tue Sep  8 22:45:17: Completed <exit>; "
                b"TERM_RUNLIMIT: job killed after reaching LSF run time limit;\n",
                SchedulerStatusKind.FAILED,
                "TIMEOUT",
                "140:0",
            ),
        )
        for history, expected_kind, expected_state, expected_exit in cases:
            with self.subTest(expected_state=expected_state):
                bjobs = build_bjobs_status_command(
                    "/lsf/bin/bjobs",
                    "12345",
                    "/lsf/conf",
                    "/lsf/lib",
                    "/lsf/etc",
                )
                bhist = build_bhist_status_command(
                    "/lsf/bin/bhist",
                    "12345",
                    "/lsf/conf",
                    "/lsf/lib",
                    "/lsf/etc",
                )
                executor = ScriptedExecutor(
                    (
                        (
                            bjobs,
                            result(
                                status=0,
                                stderr=b"Job <12345> is not found\n",
                            ),
                        ),
                        (bhist, result(stdout=history)),
                    )
                )

                status = query_slurm_job_status(
                    executor,
                    squeue_path="/lsf/bin/bjobs",
                    sacct_path="/lsf/bin/bhist",
                    job_id="12345",
                    profile_username="scientist",
                    lsf_env_directory="/lsf/conf",
                    lsf_library_directory="/lsf/lib",
                    lsf_server_directory="/lsf/etc",
                )

                self.assertIs(status.kind, expected_kind)
                self.assertEqual(status.scheduler_state, expected_state)
                self.assertEqual(status.exit_code, expected_exit)
                self.assertIn(" -n 10 -l 12345", executor.commands[1])

    def test_lsf_no_active_job_falls_back_to_history(self):
        bjobs = build_bjobs_status_command(
            "/lsf/bin/bjobs", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        bhist = build_bhist_status_command(
            "/lsf/bin/bhist", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        executor = ScriptedExecutor(
            (
                (
                    bjobs,
                    result(status=255, stderr=b"No unfinished job found\n"),
                ),
                (
                    bhist,
                    result(
                        stdout=(
                            b"Job <12345>, User <scientist>, Queue <normal>\n"
                            b"Tue Sep  8 12:00:00: Completed <done>.\n"
                        )
                    ),
                ),
            )
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/lsf/bin/bjobs",
            sacct_path="/lsf/bin/bhist",
            job_id="12345",
            profile_username="scientist",
            lsf_env_directory="/lsf/conf",
            lsf_library_directory="/lsf/lib",
            lsf_server_directory="/lsf/etc",
        )

        self.assertIs(status.kind, SchedulerStatusKind.COMPLETED)
        self.assertEqual(status.exit_code, "0:0")

    def test_lsf_bjobs_exit_is_enriched_with_bhist_cause(self):
        bjobs = build_bjobs_status_command(
            "/lsf/bin/bjobs", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        bhist = build_bhist_status_command(
            "/lsf/bin/bhist", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        executor = ScriptedExecutor(
            (
                (bjobs, result(stdout=b"12345|EXIT\n")),
                (
                    bhist,
                    result(
                        stdout=(
                            b"Job <12345>, User <scientist>\n"
                            b"Exited with exit code 140.\n"
                            b"Completed <exit>; TERM_RUNLIMIT: run limit reached;\n"
                        )
                    ),
                ),
            )
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/lsf/bin/bjobs",
            sacct_path="/lsf/bin/bhist",
            job_id="12345",
            profile_username="scientist",
            lsf_env_directory="/lsf/conf",
            lsf_library_directory="/lsf/lib",
            lsf_server_directory="/lsf/etc",
        )

        self.assertIs(status.kind, SchedulerStatusKind.FAILED)
        self.assertEqual(status.scheduler_state, "TIMEOUT")
        self.assertEqual(executor.commands, [bjobs, bhist])

    def test_lsf_exact_active_job_query_uses_basic_two_field_output(self):
        bjobs = build_bjobs_status_command(
            "/lsf/bin/bjobs", "12345", "/lsf/conf", "/lsf/lib", "/lsf/etc"
        )
        executor = ScriptedExecutor(
            ((bjobs, result(stdout=b"12345|RUN\n")),)
        )

        status = query_slurm_job_status(
            executor,
            squeue_path="/lsf/bin/bjobs",
            sacct_path="/lsf/bin/bhist",
            job_id="12345",
            profile_username="scientist",
            lsf_env_directory="/lsf/conf",
            lsf_library_directory="/lsf/lib",
            lsf_server_directory="/lsf/etc",
        )

        self.assertIs(status.kind, SchedulerStatusKind.RUNNING)
        self.assertEqual(status.scheduler_state, "RUN")
        self.assertIsNone(status.exit_code)
        self.assertEqual(len(executor.commands), 1)

    def test_lsf_bhist_run_limit_is_reported_as_timeout(self):
        output = (
            f"Job <{SYNTHETIC_JOB_ID}>, Job Name <{SYNTHETIC_PROJECT_NAME}>, "
            "User <synthetic-user>, Project <synthetic-project>, "
            "Queue <synthetic-queue>\n"
            "Wed Jan  2 03:04:05 2030: Exited with exit code 140.\n"
            "Wed Jan  2 03:04:05 2030: Completed <exit>; "
            "TERM_RUNLIMIT: job killed after reaching LSF run time limit;\n"
        )

        status = parse_bhist_output(output, SYNTHETIC_JOB_ID)

        self.assertIs(status.kind, SchedulerStatusKind.FAILED)
        self.assertEqual(status.scheduler_state, "TIMEOUT")
        self.assertEqual(status.exit_code, "140:0")

    def test_lsf_bhist_preserves_other_explicit_terminal_causes(self):
        cases = (
            ("TERM_MEMLIMIT: memory limit reached", "OUT_OF_MEMORY"),
            ("TERM_OWNER: job killed by owner", "CANCELLED"),
            ("Exited for an application error", "EXIT"),
        )
        for reason, expected_state in cases:
            with self.subTest(reason=reason):
                status = parse_bhist_output(
                    "Job <12345>, User <scientist>\n"
                    "Exited with exit code 1.\n"
                    f"Completed <exit>; {reason};\n",
                    "12345",
                )
                self.assertIs(status.kind, SchedulerStatusKind.FAILED)
                self.assertEqual(status.scheduler_state, expected_state)
                self.assertEqual(status.exit_code, "1:0")

    def test_lsf_cancel_targets_one_decimal_job(self):
        self.assertEqual(
            build_scancel_command(
                "/lsf/bin/bkill",
                "12345",
                "/lsf/conf",
                "/lsf/lib",
                "/lsf/etc",
            ),
            "env LSF_ENVDIR=/lsf/conf LSF_BINDIR=/lsf/bin "
            "LSF_LIBDIR=/lsf/lib LSF_SERVERDIR=/lsf/etc "
            '"PATH=/lsf/bin:/lsf/etc${PATH:+:$PATH}" '
            '"LD_LIBRARY_PATH=/lsf/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" '
            "/lsf/bin/bkill 12345",
        )

    def test_lsf_cpus_per_task_is_not_silently_reinterpreted(self):
        with self.assertRaisesRegex(ValueError, "CPUs per task must be 1"):
            replace(lsf_preset(), cpus_per_task=2)
        with self.assertRaisesRegex(ValueError, "OpenMP threads must be 1"):
            replace(lsf_preset(), omp_num_threads=2)
        with self.assertRaisesRegex(ValueError, "divide evenly"):
            replace(lsf_preset(), nodes=3)

    def test_lsf_scheduler_kind_persists_in_profile_schema(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            repository = ServerProfileRepository(path)
            repository.save(
                replace(profile(), execution_preset=lsf_preset())
            )

            loaded = repository.load().profiles[0]
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertIs(
            loaded.execution_preset.scheduler_kind, SchedulerKind.LSF
        )
        self.assertEqual(raw["schema_version"], 12)
        self.assertEqual(
            raw["profiles"][0]["execution_preset"]["scheduler_kind"], "LSF"
        )
        self.assertEqual(
            raw["profiles"][0]["execution_preset"]["lsf_env_directory"],
            "/srv/moltage-test/lsf/conf",
        )
        self.assertEqual(
            raw["profiles"][0]["execution_preset"]["lsf_server_directory"],
            "/srv/moltage-test/lsf/current/etc",
        )

    def test_schema_7_lsf_profile_loads_for_bounded_environment_rediscovery(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            repository = ServerProfileRepository(path)
            repository.save(replace(profile(), execution_preset=lsf_preset()))
            legacy = json.loads(path.read_text(encoding="utf-8"))
            legacy["schema_version"] = 7
            del legacy["profiles"][0]["execution_preset"][
                "lsf_env_directory"
            ]
            del legacy["profiles"][0]["execution_preset"][
                "lsf_library_directory"
            ]
            del legacy["profiles"][0]["execution_preset"][
                "lsf_server_directory"
            ]
            path.write_text(json.dumps(legacy), encoding="utf-8")

            loaded = repository.load().profiles[0]
            repository.save(loaded)
            migrated = json.loads(path.read_text(encoding="utf-8"))

        self.assertIsNone(loaded.execution_preset.lsf_env_directory)
        self.assertEqual(migrated["schema_version"], 12)
        self.assertIsNone(
            migrated["profiles"][0]["execution_preset"][
                "lsf_env_directory"
            ]
        )

    def test_schema_8_lsf_profile_keeps_config_and_rediscovers_machine_dirs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            repository = ServerProfileRepository(path)
            repository.save(replace(profile(), execution_preset=lsf_preset()))
            legacy = json.loads(path.read_text(encoding="utf-8"))
            legacy["schema_version"] = 8
            del legacy["profiles"][0]["execution_preset"][
                "lsf_library_directory"
            ]
            del legacy["profiles"][0]["execution_preset"][
                "lsf_server_directory"
            ]
            path.write_text(json.dumps(legacy), encoding="utf-8")

            loaded = repository.load().profiles[0]
            repository.save(loaded)
            migrated = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            loaded.execution_preset.lsf_env_directory,
            "/srv/moltage-test/lsf/conf",
        )
        self.assertIsNone(loaded.execution_preset.lsf_library_directory)
        self.assertIsNone(loaded.execution_preset.lsf_server_directory)
        self.assertEqual(migrated["schema_version"], 12)

    def test_automatic_lsf_cache_persists_command_and_config_directories(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            repository = ServerProfileRepository(path)
            item = replace(
                profile(),
                execution_preset=replace(
                    lsf_preset(),
                    slurm_bin_directory=None,
                    lsf_env_directory=None,
                    lsf_library_directory=None,
                    lsf_server_directory=None,
                ),
            )
            repository.save(item)

            repository.cache_automatic_slurm_directory(
                item.profile_id,
                "/srv/moltage-test/lsf/current/bin",
                "/srv/moltage-test/lsf/conf",
                "/srv/moltage-test/lsf/current/lib",
                "/srv/moltage-test/lsf/current/etc",
            )
            loaded = repository.load().profiles[0].execution_preset

        self.assertEqual(
            loaded.slurm_bin_directory, "/srv/moltage-test/lsf/current/bin"
        )
        self.assertEqual(
            loaded.lsf_env_directory, "/srv/moltage-test/lsf/conf"
        )
        self.assertEqual(
            loaded.lsf_library_directory, "/srv/moltage-test/lsf/current/lib"
        )
        self.assertEqual(
            loaded.lsf_server_directory, "/srv/moltage-test/lsf/current/etc"
        )

    def test_project_job_keeps_its_scheduler_binding(self):
        project = example_project()
        active = project.steps[1]
        project = replace(
            project,
            steps=(
                project.steps[0],
                replace(
                    active,
                    state=ProjectStepState.QUEUED,
                    job_id="12345",
                    submitted_at=project.created_at,
                    scheduler_kind=SchedulerKind.LSF,
                ),
                *project.steps[2:],
            ),
        )

        parsed = parse_project_manifest(serialize_project_manifest(project))

        self.assertIs(parsed.steps[1].scheduler_kind, SchedulerKind.LSF)


if __name__ == "__main__":
    unittest.main()
