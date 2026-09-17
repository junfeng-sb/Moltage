"""Synthetic profile and project persistence tests for the ORCA workflow."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

from moltage.app.project_planning import create_initial_project
from moltage.app.server_profiles import ServerProfileRepository
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
    append_orca_frequency_step,
    append_orca_wbl_step,
)
from moltage.domain.server_profile import (
    OrcaRuntimeConfiguration,
    RuntimeDiscoveryHints,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    RuntimeLocation,
)
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaVersionEvidence,
    OrcaVersionFamily,
)
from moltage.orca.evidence import (
    OrcaFrequencyCompletion,
    OrcaFrequencyEvidence,
    OrcaImaginaryModeClassification,
)
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.orca.settings import OrcaFrequencySettings, OrcaOptimizationSettings
from moltage.orca.wbl import (
    WBL_MODEL_CLASSIFICATION,
    WBL_MODEL_ID,
    OrcaWblContactSettings,
    OrcaWblResultEvidence,
    OrcaWblSettings,
    WblLinkerKind,
    WblParameterStatus,
    WblSpinTreatment,
)
from moltage.remote.project_manifest import parse_project_manifest, serialize_project_manifest
from phase2b1_test_support import profile


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROFILE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def runtime():
    return OrcaRuntimeConfiguration(
        "/apps/example/orca-6.1/orca",
        RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, ("orca/6.1",)),
        OrcaVersionEvidence(
            "Program Version 6.1.2",
            "6.1.2",
            OrcaVersionFamily.V6_1,
            "synthetic validation",
        ),
    )


def optimization_settings():
    return OrcaOptimizationSettings(
        method=OrcaMethod.PBE0,
        basis=OrcaBasis.DEF2_TZVP,
        process_count=8,
        max_core_mb=1500,
        version_family=OrcaVersionFamily.V6_1,
        scheduler_nodes=2,
        runtime_minutes=120,
        scheduler_memory_gb=16,
    )


class OrcaPersistenceTests(unittest.TestCase):
    def test_profile_schema_12_round_trip_keeps_runtime_and_unsuccessful_hint(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            base = profile(profile_id=PROFILE_ID)
            hints = base.runtime_hints or RuntimeDiscoveryHints()
            configured = replace(
                base,
                orca_runtime=runtime(),
                runtime_hints=replace(
                    hints,
                    orca=RuntimeLocation(
                        "/apps/example/unverified/orca",
                        RuntimeEnvironment(RuntimeEnvironmentMode.NONE),
                    ),
                ),
            )
            repository = ServerProfileRepository(path)
            repository.save(configured)
            loaded = repository.load().profiles[0]
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], 12)
        self.assertEqual(loaded.orca_runtime, configured.orca_runtime)
        self.assertEqual(loaded.runtime_hints.orca, configured.runtime_hints.orca)
        self.assertEqual(loaded.execution_preset, configured.execution_preset)

    def test_profile_schemas_3_through_11_load_with_orca_unresolved(self):
        for schema_version in range(3, 12):
            with self.subTest(schema_version=schema_version), TemporaryDirectory() as directory:
                path = Path(directory) / "profiles.json"
                repository = ServerProfileRepository(path)
                configured = replace(profile(profile_id=PROFILE_ID), orca_runtime=runtime())
                repository.save(configured)
                document = json.loads(path.read_text(encoding="utf-8"))
                document["schema_version"] = schema_version
                document["profiles"][0].pop("orca_runtime", None)
                hints = document["profiles"][0].get("runtime_hints")
                if isinstance(hints, dict):
                    hints.pop("orca", None)
                path.write_text(json.dumps(document), encoding="utf-8")

                loaded = repository.load().profiles[0]

                self.assertIsNone(loaded.orca_runtime)
                self.assertEqual(loaded.profile_id, PROFILE_ID)
                self.assertEqual(loaded.remote_project_root, configured.remote_project_root)

    def test_orca_optimization_and_frequency_manifest_round_trip(self):
        project = create_initial_project(
            base_name="SyntheticOrca",
            remote_directory_name="SyntheticOrca.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=PROFILE_ID,
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=NOW,
            project_id=PROJECT_ID,
        )
        settings = optimization_settings()
        result = OrcaOptimizationResultEvidence(
            True,
            True,
            True,
            True,
            True,
            output_sha256="1" * 64,
            xyz_sha256="2" * 64,
            gbw_sha256="3" * 64,
        )
        optimization = replace(
            project.steps[0],
            state=ProjectStepState.SUCCEEDED,
            job_id="90001",
            scheduler_kind="SLURM",
            orca_optimization_settings=settings,
            orca_runtime=runtime(),
            orca_optimization_result=result,
            orca_submitted_elements=("O", "H", "H"),
        )
        project = replace(project, steps=(optimization,))
        project = append_orca_frequency_step(project)
        frequency_settings = OrcaFrequencySettings(
            settings,
            settings.scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
            process_count=4,
        )
        frequency_result = OrcaFrequencyEvidence(
            OrcaFrequencyCompletion.FREQUENCY_COMPLETED,
            OrcaImaginaryModeClassification.NO_IMAGINARY_MODES_REPORTED,
            (),
            9,
        )
        frequency = replace(
            project.steps[-1],
            state=ProjectStepState.SUCCEEDED,
            orca_frequency_settings=frequency_settings,
            orca_runtime=runtime(),
            orca_frequency_result=frequency_result,
        )
        project = replace(project, steps=(project.steps[0], frequency))

        encoded = serialize_project_manifest(project)
        decoded = parse_project_manifest(encoded)

        self.assertEqual(decoded, project)
        raw = json.loads(encoded)
        self.assertEqual(raw["schema_version"], 10)
        self.assertEqual(raw["workflow_kind"], "ORCA")
        self.assertNotIn("WBL", encoded)

    def test_orca_wbl_stage_round_trip_and_precedes_optional_frequency(self):
        project = create_initial_project(
            base_name="SyntheticWbl",
            remote_directory_name="SyntheticWbl.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=PROFILE_ID,
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=NOW,
            project_id=PROJECT_ID,
        )
        optimization = replace(
            project.steps[0],
            state=ProjectStepState.SUCCEEDED,
            job_id="90002",
            scheduler_kind="SLURM",
            input_hashes=(("orca_opt.inp", "0" * 64),),
            orca_optimization_settings=optimization_settings(),
            orca_runtime=runtime(),
            orca_optimization_result=OrcaOptimizationResultEvidence(
                True,
                True,
                True,
                True,
                True,
                output_sha256="1" * 64,
                xyz_sha256="2" * 64,
                gbw_sha256="3" * 64,
            ),
            orca_submitted_elements=("S", "C", "S"),
        )
        project = replace(project, steps=(optimization,))
        wbl_settings = OrcaWblSettings(
            OrcaWblContactSettings(
                0,
                WblLinkerKind.SH,
                0.2,
                WblParameterStatus.HYPOTHESIS,
            ),
            OrcaWblContactSettings(
                2,
                WblLinkerKind.SH,
                0.3,
                WblParameterStatus.CALIBRATED,
            ),
            -5.0,
            -2.0,
            2.0,
            0.1,
        )
        wbl_result = OrcaWblResultEvidence(
            WBL_MODEL_ID,
            WBL_MODEL_CLASSIFICATION,
            "3" * 64,
            "4" * 64,
            (
                ("orca_wbl_result.json", "5" * 64),
                ("orca_wbl_transmission.csv", "6" * 64),
            ),
            "/apps/example/orca-6.1/orca_2json",
            0.1,
            0.2,
            0.3,
            ((1, 0.1),),
            ((2, 0.2),),
        )
        project = append_orca_wbl_step(
            project,
            settings=wbl_settings,
            result=wbl_result,
            input_hashes=(("orca_opt.gbw", "3" * 64),),
            finished_at=NOW,
        )
        project = append_orca_frequency_step(project)

        encoded = serialize_project_manifest(project)
        decoded = parse_project_manifest(encoded)

        self.assertEqual(decoded, project)
        self.assertEqual(
            tuple(step.kind for step in decoded.steps),
            (
                ProjectStepKind.ORCA_OPTIMIZATION,
                ProjectStepKind.ORCA_WBL_TRANSMISSION,
                ProjectStepKind.ORCA_FREQUENCY,
            ),
        )
        self.assertEqual(decoded.steps[1].orca_wbl_settings, wbl_settings)
        self.assertEqual(decoded.steps[1].orca_wbl_result, wbl_result)

        legacy = json.loads(encoded)
        legacy_result = legacy["steps"][1]["orca_wbl_result"]
        legacy_result.pop("spin_treatment")
        legacy_result.pop("top_total")
        migrated = parse_project_manifest(json.dumps(legacy))
        self.assertIs(
            migrated.steps[1].orca_wbl_result.spin_treatment,
            WblSpinTreatment.SPIN_RESOLVED,
        )
        self.assertEqual(migrated.steps[1].orca_wbl_result.top_total, ())


if __name__ == "__main__":
    unittest.main()
