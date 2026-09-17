"""Synthetic ORCA Slurm/LSF rendering tests."""

import unittest
from uuid import UUID

from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import (
    LsfResourceRequirementMode,
    OrcaRuntimeConfiguration,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    SlurmExecutionPreset,
)
from moltage.orca.batch import orca_memory_advisory, render_orca_submit_script
from moltage.orca.catalog import OrcaBasis, OrcaMethod, OrcaVersionEvidence, OrcaVersionFamily
from moltage.orca.settings import OrcaOptimizationSettings


def runtime(environment=None):
    return OrcaRuntimeConfiguration(
        "/opt/example/orca/orca",
        environment or RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
        OrcaVersionEvidence("Program Version 6.1.0", "6.1.0", OrcaVersionFamily.V6_1, "synthetic"),
    )


def settings():
    return OrcaOptimizationSettings(
        OrcaMethod.PBE,
        OrcaBasis.DEF2_SVP,
        process_count=4,
        version_family=OrcaVersionFamily.V6_1,
    )


def preset(kind=SchedulerKind.SLURM, **changes):
    values = dict(
        nodes=1,
        ntasks=4,
        cpus_per_task=1,
        runtime_minutes=90,
        memory_gb=8,
        unset_slurm_export_env=kind is SchedulerKind.SLURM,
        scheduler_kind=kind,
        slurm_output_filename="",
    )
    if kind is SchedulerKind.LSF:
        values.update(
            lsf_resource_requirement_mode=LsfResourceRequirementMode.SITE_DEFAULT,
            lsf_env_directory="/opt/example/lsf/conf",
            lsf_library_directory="/opt/example/lsf/lib",
            lsf_server_directory="/opt/example/lsf/conf",
        )
    values.update(changes)
    return SlurmExecutionPreset(**values)


class OrcaBatchTests(unittest.TestCase):
    def test_slurm_direct_invocation_and_blank_site_fields(self):
        script = render_orca_submit_script(
            preset(), runtime(), UUID("11111111-1111-4111-8111-111111111111"), settings()
        )
        self.assertIn("#SBATCH --ntasks=4", script)
        self.assertIn("#SBATCH --output=orca_opt.scheduler.out", script)
        self.assertIn("exec /opt/example/orca/orca orca_opt.inp > orca_opt.out 2>&1", script)
        self.assertNotIn('dirname -- "$0"', script)
        self.assertNotIn("--account", script)
        self.assertNotIn("srun ", script)
        self.assertNotIn("mpirun", script)
        self.assertNotIn("which ", script)

    def test_orca_scheduler_log_does_not_reuse_fhi_output_name(self):
        script = render_orca_submit_script(
            preset(slurm_output_filename="aims.dft.out"),
            runtime(),
            UUID("11111111-1111-4111-8111-111111111111"),
            settings(),
        )

        self.assertIn("#SBATCH --output=orca_opt.scheduler.out", script)
        self.assertNotIn("aims.dft.out", script)

        frequency_script = render_orca_submit_script(
            preset(slurm_output_filename="aims.dft.out"),
            runtime(),
            UUID("11111111-1111-4111-8111-111111111111"),
            settings(),
            frequency=True,
        )
        self.assertIn("#SBATCH --output=orca_freq.scheduler.out", frequency_script)
        self.assertIn("orca_freq.inp > orca_freq.out 2>&1", frequency_script)
        self.assertNotIn("aims.dft.out", frequency_script)

    def test_slurm_site_fields_and_module_environment(self):
        environment = RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, ("chemistry/orca-6.1",))
        script = render_orca_submit_script(
            preset(slurm_account="acct", slurm_partition="cpu", slurm_qos="normal"),
            runtime(environment),
            UUID("11111111-1111-4111-8111-111111111111"),
            settings(),
        )
        self.assertIn("#SBATCH --account=acct", script)
        self.assertIn("module load chemistry/orca-6.1", script)

    def test_lsf_site_default_and_span_rusage(self):
        direct = render_orca_submit_script(
            preset(
                SchedulerKind.LSF,
                lsf_queue="normal",
                lsf_project="example",
                slurm_output_filename="aims.dft.out",
            ),
            runtime(),
            UUID("11111111-1111-4111-8111-111111111111"),
            settings(),
        )
        self.assertIn("#BSUB -q normal", direct)
        self.assertIn("#BSUB -oo orca_opt.scheduler.out.lsf.log", direct)
        self.assertNotIn("aims.dft.out", direct)
        self.assertNotIn("#BSUB -R", direct)
        structured = render_orca_submit_script(
            preset(SchedulerKind.LSF, lsf_resource_requirement_mode=LsfResourceRequirementMode.SPAN_RUSAGE),
            runtime(),
            UUID("11111111-1111-4111-8111-111111111111"),
            settings(),
        )
        self.assertIn('#BSUB -R "span[hosts=1] rusage[mem=8G]"', structured)

    def test_maxcore_warning_is_advisory_and_lsf_scope_is_not_total(self):
        configured = OrcaOptimizationSettings(
            OrcaMethod.PBE,
            OrcaBasis.DEF2_SVP,
            process_count=4,
            max_core_mb=4096,
            version_family=OrcaVersionFamily.V6_1,
        )
        self.assertIn("above", orca_memory_advisory(preset(), configured))
        self.assertIn("site-defined", orca_memory_advisory(preset(SchedulerKind.LSF), configured))


if __name__ == "__main__":
    unittest.main()
