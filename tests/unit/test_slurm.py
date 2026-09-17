import unittest
from dataclasses import replace
from uuid import UUID

from moltage.aitranss.slurm import (
    AitranssExecutionSettings,
    AitranssSlurmError,
    render_aitranss_submit_script,
)
from moltage.domain.calculation_project import ProjectStepKind
from moltage.domain.server_profile import (
    SlurmAitranssLaunchMode,
    SlurmExecutionPreset,
    SlurmMailSettings,
)
from moltage.remote.slurm import (
    FHI_AIMS_KILL_ON_BAD_EXIT_OPTION,
    SLURM_MAIL_TYPES,
    SlurmSubmissionError,
    build_sbatch_submission_command,
    controlled_slurm_job_name,
    parse_sbatch_parsable_output,
    parse_submit_script_output_filename,
    preset_with_submit_script_resources,
    render_submit_script,
    render_slurm_mail_directives,
)
from phase2b1_test_support import synthetic_slurm_preset
from synthetic_test_data import SYNTHETIC_AITRANSS_EXECUTABLE


PROJECT_ID = UUID("1a2b3c4d-1111-4111-8111-111111111111")


class SlurmScriptTests(unittest.TestCase):
    def test_aitranss_uses_only_explicit_direct_or_absolute_srun_launch(self):
        configured = replace(
            synthetic_slurm_preset(),
            slurm_account="account-a",
        )
        srun_script = render_aitranss_submit_script(
            profile_preset=configured,
            settings=AitranssExecutionSettings(),
            project_id=PROJECT_ID,
            executable_path=SYNTHETIC_AITRANSS_EXECUTABLE,
            aitranss_modules=("chemistry/aitranss-example",),
        )
        self.assertIn("#SBATCH --account=account-a", srun_script)
        self.assertIn(
            "exec /usr/bin/srun --ntasks=1 " + SYNTHETIC_AITRANSS_EXECUTABLE,
            srun_script,
        )
        self.assertNotIn("\nsrun --ntasks=1", srun_script)

        direct = replace(
            configured,
            slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.DIRECT,
            slurm_aitranss_srun_path=None,
        )
        direct_script = render_aitranss_submit_script(
            profile_preset=direct,
            settings=AitranssExecutionSettings(),
            project_id=PROJECT_ID,
            executable_path=SYNTHETIC_AITRANSS_EXECUTABLE,
            aitranss_modules=("chemistry/aitranss-example",),
        )
        self.assertTrue(
            direct_script.endswith(
                "exec " + SYNTHETIC_AITRANSS_EXECUTABLE + "\n"
            )
        )
        self.assertNotIn("srun", direct_script)

        incomplete = replace(
            configured,
            slurm_aitranss_srun_path=None,
        )
        with self.assertRaisesRegex(AitranssSlurmError, "verified absolute srun"):
            render_aitranss_submit_script(
                profile_preset=incomplete,
                settings=AitranssExecutionSettings(),
                project_id=PROJECT_ID,
                executable_path=SYNTHETIC_AITRANSS_EXECUTABLE,
                aitranss_modules=("chemistry/aitranss-example",),
            )

    def test_site_selectors_render_only_when_configured(self) -> None:
        configured = replace(
            synthetic_slurm_preset(),
            slurm_account="account-a",
            slurm_partition="partition-a",
            slurm_qos="qos-a",
        )

        script = render_submit_script(
            configured,
            PROJECT_ID,
            ProjectStepKind.MOLECULE_OPT,
        )

        self.assertIn("#SBATCH --account=account-a\n", script)
        self.assertIn("#SBATCH --partition=partition-a\n", script)
        self.assertIn("#SBATCH --qos=qos-a\n", script)
        blank = render_submit_script(
            replace(
                configured,
                slurm_account="",
                slurm_partition="",
                slurm_qos="",
            ),
            PROJECT_ID,
            ProjectStepKind.MOLECULE_OPT,
        )
        self.assertNotIn("--account", blank)
        self.assertNotIn("--partition", blank)
        self.assertNotIn("--qos", blank)

    def test_synthetic_script_is_complete_deterministic_lf_text(self) -> None:
        expected = (
            "#!/bin/bash\n"
            "\n"
            "#SBATCH --job-name=AT-1A2B3C4D-S1\n"
            "#SBATCH --output=aims.dft.out\n"
            "#SBATCH --nodes=1\n"
            "#SBATCH --ntasks=24\n"
            "#SBATCH --cpus-per-task=1\n"
            "#SBATCH --time=2160\n"
            "#SBATCH --mem=128G\n"
            "#SBATCH --no-requeue\n"
            "#SBATCH --export=NONE\n"
            "\n"
            "unset SLURM_EXPORT_ENV\n"
            "\n"
            "export OMP_NUM_THREADS=1\n"
            "\n"
            "module purge\n"
            "module load mpi/example-1.0\n"
            "module load chemistry/fhi-aims-example\n"
            "\n"
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x\n"
        )

        first = render_submit_script(
            synthetic_slurm_preset(), PROJECT_ID, ProjectStepKind.MOLECULE_OPT
        )
        second = render_submit_script(
            synthetic_slurm_preset(), PROJECT_ID, ProjectStepKind.MOLECULE_OPT
        )

        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertNotIn("\r", first)
        self.assertTrue(first.endswith("\n"))
        self.assertEqual(first.count(FHI_AIMS_KILL_ON_BAD_EXIT_OPTION), 1)
        self.assertEqual(
            first.splitlines()[-1],
            "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
            "aims.synthetic.scalapack.mpi.x",
        )
        for scientific_text in (
            "xc pbe",
            "relax_geometry",
            "vdw_correction_hirshfeld",
            "atom 0.0",
        ):
            self.assertNotIn(scientific_text, first)

    def test_false_flags_are_omitted_but_modules_keep_order(self) -> None:
        preset = SlurmExecutionPreset(
            nodes=2,
            ntasks=8,
            cpus_per_task=3,
            runtime_minutes=17,
            memory_gb=9,
            no_requeue=False,
            export_none=False,
            unset_slurm_export_env=False,
            omp_num_threads=4,
            module_purge=False,
            modules=("second/2", "first/1"),
            launch_command="srun aims.x",
            slurm_output_filename="run.out",
        )

        script = render_submit_script(
            preset, PROJECT_ID, ProjectStepKind.MOLECULE_AU_OPT
        )

        self.assertIn("#SBATCH --job-name=AT-1A2B3C4D-S2\n", script)
        self.assertIn("#SBATCH --time=17\n", script)
        self.assertIn("#SBATCH --mem=9G\n", script)
        self.assertNotIn("#SBATCH --no-requeue", script)
        self.assertNotIn("#SBATCH --export=NONE", script)
        self.assertNotIn("unset SLURM_EXPORT_ENV", script)
        self.assertNotIn("module purge", script)
        self.assertLess(
            script.index("module load second/2"),
            script.index("module load first/1"),
        )
        self.assertEqual(script.splitlines()[-1], "srun --kill-on-bad-exit=1 aims.x")

    def test_scheduler_output_is_a_filename_not_a_path(self) -> None:
        with self.assertRaises(ValueError):
            replace(
                synthetic_slurm_preset(),
                slurm_output_filename="/srv/moltage-test/users/scientist/job-logs/aims.out",
            )

    def test_empty_job_output_file_can_be_stored_but_not_submitted(self) -> None:
        preset = replace(synthetic_slurm_preset(), slurm_output_filename="")

        with self.assertRaisesRegex(
            SlurmSubmissionError,
            "Configure a valid Output file name before submitting",
        ):
            render_submit_script(
                preset,
                PROJECT_ID,
                ProjectStepKind.MOLECULE_OPT,
            )

    def test_fail_fast_option_is_idempotent_and_conflicts_fail_closed(self) -> None:
        canonical = replace(
            synthetic_slurm_preset(),
            launch_command=(
                "srun --kill-on-bad-exit=1 --cpu_bind=verbose "
                "aims.synthetic.scalapack.mpi.x"
            ),
        )

        script = render_submit_script(
            canonical, PROJECT_ID, ProjectStepKind.TRANSPORT_CONVERGENCE
        )

        self.assertEqual(script.count(FHI_AIMS_KILL_ON_BAD_EXIT_OPTION), 1)
        for launch_command in (
            "srun --kill-on-bad-exit=0 aims.x",
            "srun --kill-on-bad-exit aims.x",
            "srun -K aims.x",
            "srun -K0 aims.x",
            "srun -K2 aims.x",
            "srun --kill-on-bad-exit=1 --kill-on-bad-exit=1 aims.x",
            "mpirun aims.x",
        ):
            with self.subTest(launch_command=launch_command), self.assertRaises(
                SlurmSubmissionError
            ):
                render_submit_script(
                    replace(canonical, launch_command=launch_command),
                    PROJECT_ID,
                    ProjectStepKind.TRANSPORT_CONVERGENCE,
                )

    def test_job_name_supports_all_four_workflow_steps(self) -> None:
        self.assertEqual(
            controlled_slurm_job_name(
                PROJECT_ID, ProjectStepKind.MOLECULE_OPT
            ),
            "AT-1A2B3C4D-S1",
        )
        self.assertEqual(
            controlled_slurm_job_name(
                PROJECT_ID,
                ProjectStepKind.TRANSPORT_CONVERGENCE,
            ),
            "AT-1A2B3C4D-S3",
        )
        self.assertEqual(
            controlled_slurm_job_name(PROJECT_ID, ProjectStepKind.TRANSMISSION),
            "AT-1A2B3C4D-S4",
        )

    def test_enabled_mail_is_exactly_once_with_frozen_terminal_policy(self) -> None:
        settings = SlurmMailSettings("user@example.com")

        script = render_submit_script(
            synthetic_slurm_preset(),
            PROJECT_ID,
            ProjectStepKind.MOLECULE_OPT,
            mail_settings=settings,
        )

        self.assertEqual(SLURM_MAIL_TYPES, "END,FAIL")
        self.assertEqual(
            render_slurm_mail_directives(settings),
            (
                "#SBATCH --mail-user=user@example.com",
                "#SBATCH --mail-type=END,FAIL",
            ),
        )
        self.assertEqual(
            script.count("#SBATCH --mail-user=user@example.com"),
            1,
        )
        self.assertEqual(script.count("#SBATCH --mail-type=END,FAIL"), 1)
        for unsupported in (
            "TIME_LIMIT",
            "TIME_LIMIT_50",
            "TIME_LIMIT_80",
            "TIME_LIMIT_90",
            "--mail-type=ALL",
        ):
            self.assertNotIn(unsupported, script)

    def test_disabled_mail_keeps_directives_absent_even_with_time_limit(self) -> None:
        script = render_submit_script(
            synthetic_slurm_preset(),
            PROJECT_ID,
            ProjectStepKind.MOLECULE_OPT,
        )

        self.assertIn("#SBATCH --time=2160", script)
        self.assertNotIn("--mail-user", script)
        self.assertNotIn("--mail-type", script)
        self.assertNotIn("TIME_LIMIT", script)

    def test_attempt_resources_overlay_current_fixed_policy(self) -> None:
        submitted = render_submit_script(
            replace(
                synthetic_slurm_preset(),
                nodes=2,
                ntasks=48,
                cpus_per_task=3,
                runtime_minutes=720,
                memory_gb=128,
                omp_num_threads=6,
            ),
            PROJECT_ID,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        current = replace(
            synthetic_slurm_preset(),
            nodes=9,
            ntasks=9,
            cpus_per_task=9,
            runtime_minutes=9,
            memory_gb=64,
            omp_num_threads=9,
            modules=("current/module",),
            launch_command="srun current-aims.x",
            slurm_account="current-account",
            slurm_partition="current-partition",
            slurm_qos="current-qos",
        )

        recovered = preset_with_submit_script_resources(current, submitted)

        self.assertEqual(
            (
                recovered.nodes,
                recovered.ntasks,
                recovered.cpus_per_task,
                recovered.runtime_minutes,
                recovered.memory_gb,
                recovered.omp_num_threads,
            ),
            (2, 48, 3, 720, 128, 6),
        )
        self.assertEqual(recovered.modules, ("current/module",))
        self.assertEqual(recovered.launch_command, "srun current-aims.x")
        self.assertEqual(recovered.slurm_account, current.slurm_account)
        self.assertEqual(recovered.slurm_partition, current.slurm_partition)
        self.assertEqual(recovered.slurm_qos, current.slurm_qos)
        self.assertEqual(
            recovered.slurm_aitranss_launch_mode,
            current.slurm_aitranss_launch_mode,
        )
        self.assertEqual(
            recovered.slurm_aitranss_srun_path,
            current.slurm_aitranss_srun_path,
        )

    def test_attempt_resource_parser_rejects_missing_duplicate_or_malformed(self) -> None:
        accepted = render_submit_script(
            synthetic_slurm_preset(),
            PROJECT_ID,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
        invalid = (
            accepted.replace("#SBATCH --mem=128G\n", ""),
            accepted.replace(
                "#SBATCH --mem=128G\n",
                "#SBATCH --mem=128G\n#SBATCH --mem=192G\n",
            ),
            accepted.replace("#SBATCH --mem=128G", "#SBATCH --mem=128"),
            accepted.replace("#SBATCH --time=2160", "#SBATCH --time=0"),
            accepted.replace("export OMP_NUM_THREADS=1", "export OMP_NUM_THREADS=x"),
        )
        for script in invalid:
            with self.subTest(script=script), self.assertRaises(
                SlurmSubmissionError
            ):
                preset_with_submit_script_resources(synthetic_slurm_preset(), script)


class SbatchParserTests(unittest.TestCase):
    def test_submission_command_quotes_step_and_verified_absolute_sbatch(self) -> None:
        command = build_sbatch_submission_command(
            "/remote/work place/Test.20300102",
            "/opt/slurm tools/bin/sbatch",
        )

        self.assertEqual(
            command,
            "cd '/remote/work place/Test.20300102' && "
            "'/opt/slurm tools/bin/sbatch' --parsable submit.sh",
        )
        self.assertNotIn("&& sbatch --parsable", command)

    def test_supported_receipts_are_parsed_exactly(self) -> None:
        plain = parse_sbatch_parsable_output("12345\n")
        clustered = parse_sbatch_parsable_output(b"12345;clusterA\n")

        self.assertEqual((plain.job_id, plain.cluster_name), ("12345", None))
        self.assertEqual(
            (clustered.job_id, clustered.cluster_name),
            ("12345", "clusterA"),
        )

    def test_arbitrary_scheduler_text_is_rejected(self) -> None:
        for output in (
            "",
            "Submitted batch job 12345",
            "abc",
            "12345 extra",
            "12;cluster;extra",
        ):
            with self.subTest(output=output), self.assertRaises(
                SlurmSubmissionError
            ):
                parse_sbatch_parsable_output(output)


if __name__ == "__main__":
    unittest.main()
