from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest
from uuid import uuid4

from moltage.app.project_planning import create_initial_project
from moltage.domain.calculation_project import (
    ProjectRestartProvenance,
    ProjectStepAttempt,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.remote.project_manifest import (
    ManagedProjectManifestError,
    parse_project_manifest,
    serialize_project_manifest,
)
from phase2b1_test_support import example_project
from electrode_test_support import synthetic_project_electrode_provenance
from synthetic_structure_test_support import (
    synthetic_extended_electrode_placement,
)
from moltage.app.electrode_provenance import provenance_from_applied_electrodes


class ProjectManifestTests(unittest.TestCase):
    def test_deterministic_unicode_round_trip_with_all_four_steps(self) -> None:
        project = replace(example_project(), display_name="示例分子A")
        first = serialize_project_manifest(project)
        second = serialize_project_manifest(project)
        self.assertEqual(first, second)
        self.assertIn("示例分子A", first)
        self.assertNotIn("\\u5355", first)
        self.assertEqual(parse_project_manifest(first), project)
        self.assertEqual(len(json.loads(first)["steps"]), 4)

    def test_initial_manifest_has_revision_one_and_no_inputs_or_secrets(self) -> None:
        text = serialize_project_manifest(example_project())
        raw = json.loads(text)
        self.assertEqual(raw["schema_version"], 10)
        self.assertEqual(raw["starting_step"], "MOLECULE_AU_OPT")
        self.assertEqual(raw["electrode_provenance"], [])
        self.assertIsNone(raw["restart_provenance"])
        self.assertEqual(raw["revision"], 1)
        self.assertEqual(
            tuple(step["state"] for step in raw["steps"]),
            (
                ProjectStepState.SKIPPED.value,
                ProjectStepState.NOT_STARTED.value,
                ProjectStepState.NOT_STARTED.value,
                ProjectStepState.NOT_STARTED.value,
            ),
        )
        self.assertNotIn("password", text.casefold())
        self.assertNotIn("geometry.in", text)
        self.assertNotIn("control.in", text)

    def test_newer_schema_and_changed_step_folder_fail_explicitly(self) -> None:
        raw = json.loads(serialize_project_manifest(example_project()))
        raw["schema_version"] = 11
        with self.assertRaisesRegex(
            ManagedProjectManifestError,
            "unsupported project manifest schema",
        ):
            parse_project_manifest(json.dumps(raw))

        raw = json.loads(serialize_project_manifest(example_project()))
        raw["steps"][1]["relative_folder"] = "step2"
        with self.assertRaisesRegex(
            ManagedProjectManifestError,
            "MOLECULE_AU_OPT folder",
        ):
            parse_project_manifest(json.dumps(raw))

    def test_naive_timestamp_is_rejected(self) -> None:
        raw = json.loads(serialize_project_manifest(example_project()))
        raw["updated_at"] = "2030-08-26T12:34:56"
        with self.assertRaisesRegex(ManagedProjectManifestError, "timezone"):
            parse_project_manifest(json.dumps(raw))

    def test_legacy_schema_one_is_migrated_in_memory_without_losing_state(self) -> None:
        project = example_project()
        raw = json.loads(serialize_project_manifest(project))
        raw["schema_version"] = 1
        del raw["starting_step"]
        del raw["electrode_provenance"]
        del raw["restart_provenance"]
        for step in raw["steps"]:
            for name in (
                "scheduler_state",
                "submit_script_filename",
                "slurm_output_filename",
                "attempts",
            ):
                del step[name]

        migrated = parse_project_manifest(json.dumps(raw))

        self.assertEqual(migrated, project)
        self.assertEqual(migrated.schema_version, 10)
        self.assertIsNone(migrated.restart_provenance)

    def test_step3_attempt_provenance_round_trips(self) -> None:
        project = example_project()
        step3 = project.steps[2]
        attempt = ProjectStepAttempt(
            job_id="41001",
            submitted_at=project.created_at,
            finished_at=project.updated_at,
            terminal_scheduler_state="TIMEOUT",
            failure_reason="运行时间到达设定上限",
            submit_script_filename="submit.sh",
            slurm_output_filename="aims.dft.out",
            input_hashes=(("submit.sh", "a" * 64),),
        )
        changed = replace(
            step3,
            attempts=(attempt,),
            submit_script_filename="submit.retry02.sh",
            slurm_output_filename="aims.dft.retry02.out",
        )
        project = replace(
            project,
            steps=tuple(changed if item is step3 else item for item in project.steps),
        )

        self.assertEqual(parse_project_manifest(serialize_project_manifest(project)), project)

    def test_schema_two_attempts_migrate_with_empty_historical_hashes(self) -> None:
        project = example_project()
        attempt = ProjectStepAttempt(
            "42001",
            project.created_at,
            project.updated_at,
            "COMPLETED",
            "AITRANSS_ELECTRODE_INTERFACE_OVERLAP",
            "submit.aitranss.sh",
            "aitranss.out",
            (("tcontrol.attempt01", "b" * 64),),
        )
        step4 = replace(project.steps[3], attempts=(attempt,))
        project = replace(project, steps=(*project.steps[:3], step4))
        raw = json.loads(serialize_project_manifest(project))
        raw["schema_version"] = 2
        del raw["starting_step"]
        del raw["electrode_provenance"]
        del raw["restart_provenance"]
        for step in raw["steps"]:
            for historical in step["attempts"]:
                del historical["input_hashes"]

        migrated = parse_project_manifest(json.dumps(raw))

        self.assertEqual(migrated.schema_version, 10)
        self.assertEqual(migrated.steps[3].attempts[0].input_hashes, ())

    def test_direct_step3_provenance_round_trips_without_fake_success(self) -> None:
        electrodes = synthetic_project_electrode_provenance()
        project = create_initial_project(
            base_name="Imported",
            remote_directory_name="Imported.20300830",
            source_molecule_name="Imported.mol",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 1, 6, tzinfo=timezone.utc),
            electrode_provenance=electrodes,
        )

        raw = json.loads(serialize_project_manifest(project))
        recovered = parse_project_manifest(json.dumps(raw))

        self.assertEqual(recovered, project)
        self.assertEqual(raw["starting_step"], "TRANSPORT_CONVERGENCE")
        self.assertEqual(len(raw["electrode_provenance"]), 2)
        self.assertEqual(
            tuple(step.state for step in recovered.steps[:2]),
            (ProjectStepState.SKIPPED, ProjectStepState.SKIPPED),
        )
        self.assertNotIn(ProjectStepState.SUCCEEDED, tuple(step.state for step in recovered.steps))

    def test_restart_provenance_and_unknown_electrode_roll_round_trip(self) -> None:
        electrodes = synthetic_project_electrode_provenance(
            rolls=(None, None)
        )
        provenance = ProjectRestartProvenance(
            source_project_id=uuid4(),
            source_step=ProjectStepKind.TRANSMISSION,
            source_job_id="44001",
            source_geometry_sha256="a" * 64,
        )
        project = create_initial_project(
            base_name="Step4Restart",
            remote_directory_name="Step4Restart.20300901",
            source_molecule_name="source.geometry.in",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 9, 1, tzinfo=timezone.utc),
            electrode_provenance=electrodes,
            restart_provenance=provenance,
        )

        text = serialize_project_manifest(project)
        raw = json.loads(text)

        self.assertEqual(raw["schema_version"], 10)
        self.assertEqual(raw["restart_provenance"]["source_job_id"], "44001")
        self.assertEqual(
            tuple(item["roll_degrees"] for item in raw["electrode_provenance"]),
            (None, None),
        )
        self.assertEqual(parse_project_manifest(text), project)

    def test_schema_six_direct_mapping_uses_bounded_legacy_identity(self) -> None:
        raw = self._schema_six_direct_manifest()

        migrated = parse_project_manifest(json.dumps(raw))

        self.assertEqual(migrated.schema_version, 10)
        self.assertEqual(
            tuple(item.geometry_model for item in migrated.electrode_provenance),
            ("LegacyAu59V1", "LegacyAu59V1"),
        )
        self.assertEqual(
            tuple(item.side for item in migrated.electrode_provenance),
            ("LEFT", "RIGHT"),
        )
        left = migrated.electrode_provenance[0]
        self.assertEqual(left.contact_au_index, 0)
        self.assertEqual(len(left.local_to_global_indices), 59)
        self.assertEqual(
            sum(item.standard_pyramid_member for item in left.atom_identities),
            56,
        )
        self.assertEqual(
            left.reference_corner_lattice_keys,
            ((0, 0, 5), (0, 5, 0), (5, 0, 0)),
        )

    def test_schema_six_direct_mapping_rejects_incomplete_or_overlapping_data(
        self,
    ) -> None:
        for mutation in (
            "incomplete",
            "overlap",
            "invalid_variant",
            "invalid_contact",
        ):
            with self.subTest(mutation=mutation):
                raw = self._schema_six_direct_manifest()
                if mutation == "incomplete":
                    raw["electrode_provenance"][0][
                        "local_to_global_indices"
                    ].pop()
                elif mutation == "overlap":
                    raw["electrode_provenance"][1][
                        "local_to_global_indices"
                    ][0] = 0
                elif mutation == "invalid_variant":
                    raw["electrode_provenance"][1]["template_variant"] = "C"
                else:
                    raw["electrode_provenance"][0]["contact_au_index"] = 7
                with self.assertRaises(ManagedProjectManifestError):
                    parse_project_manifest(json.dumps(raw))

    def test_schema_eight_rejects_incomplete_canonical_identity(self) -> None:
        project = create_initial_project(
            base_name="Generated",
            remote_directory_name="Generated.20300831",
            source_molecule_name="Generated.mol",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 1, 6, tzinfo=timezone.utc),
            electrode_provenance=synthetic_project_electrode_provenance(),
        )
        raw = json.loads(serialize_project_manifest(project))
        raw["electrode_provenance"][0]["atom_identities"].pop()
        raw["electrode_provenance"][0][
            "local_to_global_atom_indices"
        ].pop()

        with self.assertRaises(ManagedProjectManifestError):
            parse_project_manifest(json.dumps(raw))

    def test_schema_eight_round_trips_ordered_interleaved_extensions(self) -> None:
        applied = synthetic_extended_electrode_placement()
        electrodes = provenance_from_applied_electrodes(applied)
        project = create_initial_project(
            base_name="Extended",
            remote_directory_name="Extended.20300902",
            source_molecule_name="Extended.mol",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 9, 2, tzinfo=timezone.utc),
            electrode_provenance=electrodes,
        )

        text = serialize_project_manifest(project)
        raw = json.loads(text)
        left, right = raw["electrode_provenance"]

        self.assertEqual(raw["schema_version"], 10)
        self.assertEqual(
            tuple(item["origin"] for item in left["lattice_extensions"]),
            ("LATTICE_EXTENSION", "LATTICE_EXTENSION"),
        )
        self.assertEqual(
            tuple(
                item["global_atom_index"]
                for item in left["lattice_extensions"]
            ),
            (128, 130),
        )
        self.assertEqual(
            tuple(
                item["global_atom_index"]
                for item in right["lattice_extensions"]
            ),
            (129,),
        )
        self.assertEqual(parse_project_manifest(text), project)

    def test_schema_seven_migrates_with_empty_extension_collections(self) -> None:
        electrodes = synthetic_project_electrode_provenance()
        project = create_initial_project(
            base_name="SchemaSeven",
            remote_directory_name="SchemaSeven.20300903",
            source_molecule_name="source.mol",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 9, 3, tzinfo=timezone.utc),
            electrode_provenance=electrodes,
        )
        raw = json.loads(serialize_project_manifest(project))
        raw["schema_version"] = 7
        for record in raw["electrode_provenance"]:
            del record["lattice_extensions"]

        migrated = parse_project_manifest(json.dumps(raw))

        self.assertEqual(migrated.schema_version, 10)
        self.assertEqual(
            tuple(record.lattice_extensions for record in migrated.electrode_provenance),
            ((), ()),
        )
        self.assertEqual(
            tuple(record.atom_identities for record in migrated.electrode_provenance),
            tuple(record.atom_identities for record in electrodes),
        )
        self.assertEqual(
            tuple(
                record.local_to_global_indices
                for record in migrated.electrode_provenance
            ),
            tuple(record.local_to_global_indices for record in electrodes),
        )

    def test_schema_eight_rejects_malformed_extension_records(self) -> None:
        applied = synthetic_extended_electrode_placement()
        project = create_initial_project(
            base_name="MalformedExtension",
            remote_directory_name="MalformedExtension.20300904",
            source_molecule_name="source.mol",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 9, 4, tzinfo=timezone.utc),
            electrode_provenance=provenance_from_applied_electrodes(applied),
        )
        for mutation in (
            "missing_field",
            "duplicate_key",
            "standard_collision",
            "invalid_sum",
            "invalid_layer",
            "duplicate_global",
            "cross_side_global",
        ):
            with self.subTest(mutation=mutation):
                raw = json.loads(serialize_project_manifest(project))
                left = raw["electrode_provenance"][0]["lattice_extensions"]
                right = raw["electrode_provenance"][1]["lattice_extensions"]
                if mutation == "missing_field":
                    del left[0]["origin"]
                elif mutation == "duplicate_key":
                    duplicate = dict(left[0])
                    duplicate["global_atom_index"] = 999
                    left.append(duplicate)
                elif mutation == "standard_collision":
                    left[0]["layer_index"] = 0
                    left[0]["lattice_key"] = [0, 0, 0]
                elif mutation == "invalid_sum":
                    left[0]["lattice_key"] = [4, 4, 4]
                elif mutation == "invalid_layer":
                    left[0]["layer_index"] = 6
                    left[0]["lattice_key"] = [6, 0, 0]
                elif mutation == "duplicate_global":
                    left[1]["global_atom_index"] = left[0]["global_atom_index"]
                else:
                    right[0]["global_atom_index"] = left[0]["global_atom_index"]
                with self.assertRaises(ManagedProjectManifestError):
                    parse_project_manifest(json.dumps(raw))

    def test_schema_six_normal_transport_marks_mapping_as_proof_required(
        self,
    ) -> None:
        project = example_project()
        step3 = replace(
            project.steps[2],
            state=ProjectStepState.QUEUED,
            job_id="41001",
            submitted_at=project.updated_at,
        )
        current = replace(
            project,
            steps=(*project.steps[:2], step3, project.steps[3]),
            electrode_provenance=synthetic_project_electrode_provenance(),
        )
        raw = json.loads(serialize_project_manifest(current))
        raw["schema_version"] = 6
        raw["electrode_provenance"] = []
        raw.pop("legacy_electrode_recovery_allowed")

        migrated = parse_project_manifest(json.dumps(raw))

        self.assertEqual(migrated.electrode_provenance, ())
        self.assertTrue(migrated.legacy_electrode_recovery_allowed)
        self.assertIs(migrated.steps[2].state, ProjectStepState.QUEUED)

    @staticmethod
    def _schema_six_direct_manifest() -> dict:
        project = create_initial_project(
            base_name="LegacyDirect",
            remote_directory_name="LegacyDirect.20300830",
            source_molecule_name="LegacyDirect.mol",
            server_profile_id=uuid4(),
            remote_project_root="/srv/moltage-test/projects",
            starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            now=datetime(2030, 1, 6, tzinfo=timezone.utc),
            electrode_provenance=synthetic_project_electrode_provenance(),
        )
        raw = json.loads(serialize_project_manifest(project))
        raw["schema_version"] = 6
        raw.pop("legacy_electrode_recovery_allowed")
        raw["electrode_provenance"] = [
            {
                "contact_au_index": 0,
                "template_variant": "A",
                "roll_degrees": 12,
                "local_to_global_indices": [0, *range(2, 60)],
            },
            {
                "contact_au_index": 1,
                "template_variant": "B",
                "roll_degrees": 348,
                "local_to_global_indices": [1, *range(60, 118)],
            },
        ]
        return raw


if __name__ == "__main__":
    unittest.main()
