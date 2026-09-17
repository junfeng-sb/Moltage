"""Shared deterministic fixtures for focused server-profile tests."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from moltage.app.project_planning import create_initial_project
from moltage.domain.calculation_project import ProjectStepKind
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    ServerProfile,
    SlurmAitranssLaunchMode,
    SlurmExecutionPreset,
)
from synthetic_test_data import (
    SYNTHETIC_AITRANSS_EXECUTABLE,
    SYNTHETIC_AITRANSS_MODULE,
    SYNTHETIC_FHI_EXECUTABLE_NAME,
    SYNTHETIC_FHI_MODULE,
    SYNTHETIC_HOST,
    SYNTHETIC_MPI_MODULE,
    SYNTHETIC_PROFILE_NAME,
    SYNTHETIC_REMOTE_ROOT,
    SYNTHETIC_SPECIES_ROOT,
    SYNTHETIC_SRUN_EXECUTABLE,
    SYNTHETIC_USERNAME,
)


TEST_AITRANSS_MODULES = (SYNTHETIC_AITRANSS_MODULE,)
TEST_AITRANSS_PATH = SYNTHETIC_AITRANSS_EXECUTABLE
TEST_SPECIES_ROOT = SYNTHETIC_SPECIES_ROOT


class MemorySecretStore:
    def __init__(self) -> None:
        self.passwords = {}

    def get_password(self, profile_id):
        return self.passwords.get(profile_id)

    def set_password(self, profile_id, password):
        self.passwords[profile_id] = password

    def delete_password(self, profile_id):
        self.passwords.pop(profile_id, None)


def synthetic_slurm_preset() -> SlurmExecutionPreset:
    return SlurmExecutionPreset(
        nodes=1,
        ntasks=24,
        cpus_per_task=1,
        runtime_minutes=2160,
        memory_gb=128,
        no_requeue=True,
        export_none=True,
        unset_slurm_export_env=True,
        omp_num_threads=1,
        module_purge=True,
        modules=(SYNTHETIC_MPI_MODULE, SYNTHETIC_FHI_MODULE),
        launch_command=(
            "srun --cpu_bind=verbose " + SYNTHETIC_FHI_EXECUTABLE_NAME
        ),
        slurm_output_filename="aims.dft.out",
        fhi_species_defaults_path=TEST_SPECIES_ROOT,
        slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.SRUN,
        slurm_aitranss_srun_path=SYNTHETIC_SRUN_EXECUTABLE,
    )


def profile(
    name=SYNTHETIC_PROFILE_NAME,
    profile_id=None,
    save_password=True,
    auto=True,
    email_enabled=False,
    email_recipient=None,
):
    return ServerProfile(
        profile_id=profile_id or uuid4(),
        name=name,
        host=SYNTHETIC_HOST,
        port=22,
        username=SYNTHETIC_USERNAME,
        remote_project_root=SYNTHETIC_REMOTE_ROOT,
        save_password=save_password,
        auto_connect=auto,
        execution_preset=synthetic_slurm_preset(),
        aitranss_runtime=AitranssRuntimeConfiguration(
            modules=TEST_AITRANSS_MODULES,
            executable_path=TEST_AITRANSS_PATH,
        ),
        email_notification_enabled=email_enabled,
        email_notification_recipient=email_recipient,
    )


def example_project():
    return create_initial_project(
        base_name="ExampleMolecule",
        remote_directory_name="ExampleMolecule.20300102",
        source_molecule_name="ExampleMolecule.xyz",
        server_profile_id=UUID("22222222-2222-4222-8222-222222222222"),
        remote_project_root=SYNTHETIC_REMOTE_ROOT,
        starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        now=datetime(
            2030,
            1,
            2,
            3,
            4,
            5,
            tzinfo=timezone.utc,
        ),
        project_id=UUID("11111111-1111-4111-8111-111111111111"),
    )
