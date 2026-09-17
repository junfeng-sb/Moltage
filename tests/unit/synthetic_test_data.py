"""Central synthetic identities shared by offline tests.

These values are intentionally fictional and are not acceptance evidence for an
external server or scientific calculation.
"""

from datetime import datetime, timezone


SYNTHETIC_PROFILE_NAME = "ExampleCluster"
SYNTHETIC_HOST = "cluster.example.org"
SYNTHETIC_NODE = "compute001"
SYNTHETIC_USERNAME = "scientist"
SYNTHETIC_EMAIL = "scientist@example.org"
SYNTHETIC_REMOTE_ROOT = "/srv/moltage-test/projects"
SYNTHETIC_PROJECT_NAME = "ExampleMolecule.20300102_01"

SYNTHETIC_MPI_MODULE = "mpi/example-1.0"
SYNTHETIC_FHI_MODULE = "chemistry/fhi-aims-example"
SYNTHETIC_AITRANSS_MODULE = "chemistry/aitranss-example"
SYNTHETIC_FHI_EXECUTABLE_NAME = "aims.synthetic.scalapack.mpi.x"
SYNTHETIC_AITRANSS_EXECUTABLE_NAME = "aitranss.synthetic.x"
SYNTHETIC_FHI_EXECUTABLE = (
    "/srv/moltage-test/apps/fhi-aims/bin/" + SYNTHETIC_FHI_EXECUTABLE_NAME
)
SYNTHETIC_AITRANSS_EXECUTABLE = (
    "/srv/moltage-test/apps/aitranss/bin/" + SYNTHETIC_AITRANSS_EXECUTABLE_NAME
)
SYNTHETIC_SPECIES_ROOT = "/srv/moltage-test/apps/fhi-aims/species_defaults"
SYNTHETIC_SRUN_EXECUTABLE = "/usr/bin/srun"

SYNTHETIC_JOB_ID = "41001"
SYNTHETIC_RETRY_JOB_ID = "41002"
SYNTHETIC_CANCELLED_JOB_ID = "41003"
SYNTHETIC_STEP4_JOB_ID = "42001"
SYNTHETIC_STEP4_RETRY_JOB_ID = "42002"
SYNTHETIC_STEP4_FINAL_JOB_ID = "42003"
SYNTHETIC_UID = "200001"
SYNTHETIC_TIMESTAMP = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
