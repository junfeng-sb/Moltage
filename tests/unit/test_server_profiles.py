import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from moltage.app.server_profiles import (
    ServerProfileRepository,
    ServerProfileRepositoryError,
    ServerProfileService,
)
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    LsfResourceRequirementMode,
    RuntimeDiscoveryHints,
    ServerProfileValidationError,
    SlurmCommandMode,
    SlurmAitranssLaunchMode,
    SlurmExecutionPreset,
    runtime_hours_from_minutes,
    runtime_minutes_from_hours,
    validate_fhi_species_defaults_path,
)
from moltage.domain.scheduler import SchedulerKind
from phase2b1_test_support import MemorySecretStore, profile, synthetic_slurm_preset


class ServerProfileTests(unittest.TestCase):
    def test_synthetic_reference_preset_is_represented_without_loss(self) -> None:
        preset = synthetic_slurm_preset()

        self.assertEqual(
            (
                preset.nodes,
                preset.ntasks,
                preset.cpus_per_task,
                preset.runtime_minutes,
                preset.memory_gb,
                preset.no_requeue,
                preset.export_none,
                preset.unset_slurm_export_env,
                preset.omp_num_threads,
                preset.module_purge,
                preset.modules,
                preset.launch_command,
                preset.slurm_output_filename,
                preset.slurm_command_mode,
                preset.slurm_bin_directory,
            ),
            (
                1,
                24,
                1,
                2160,
                128,
                True,
                True,
                True,
                1,
                True,
                ("mpi/example-1.0", "chemistry/fhi-aims-example"),
                "srun --cpu_bind=verbose aims.synthetic.scalapack.mpi.x",
                "aims.dft.out",
                SlurmCommandMode.AUTOMATIC,
                None,
            ),
        )

    def test_execution_preset_rejects_invalid_operational_fields(self) -> None:
        valid = synthetic_slurm_preset()
        for field_name, value in (
            ("nodes", 0),
            ("ntasks", -1),
            ("cpus_per_task", 0),
            ("omp_num_threads", 0),
            ("runtime_minutes", 0),
            ("runtime_minutes", "2160"),
            ("memory_gb", 0),
            ("memory_gb", -1),
            ("memory_gb", "128"),
            ("memory_gb", "128G"),
            ("modules", ("safe", "bad\nmodule")),
            ("modules", ("safe", "bad;module")),
            ("launch_command", "srun aims\nrm"),
            ("slurm_output_filename", "../aims.out"),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ServerProfileValidationError):
                    replace(valid, **{field_name: value})

    def test_runtime_hour_conversion_and_round_trip_are_numeric(self) -> None:
        for hours, minutes in (
            (0.1, 6),
            (1.0, 60),
            (1.5, 90),
            (36.0, 2160),
        ):
            with self.subTest(hours=hours):
                self.assertEqual(runtime_minutes_from_hours(hours), minutes)
        self.assertEqual(runtime_hours_from_minutes(2160), 36.0)
        with self.assertRaises(ServerProfileValidationError):
            runtime_minutes_from_hours("36.0")

    def test_new_execution_preset_uses_safety_defaults(self) -> None:
        preset = SlurmExecutionPreset(
            nodes=2,
            ntasks=7,
            cpus_per_task=3,
            runtime_minutes=42,
            memory_gb=9,
            launch_command="srun aims.x",
            slurm_output_filename="aims.out",
        )

        self.assertTrue(preset.no_requeue)
        self.assertTrue(preset.export_none)
        self.assertTrue(preset.unset_slurm_export_env)
        self.assertTrue(preset.module_purge)
        self.assertEqual(preset.omp_num_threads, 1)
        self.assertIs(preset.slurm_command_mode, SlurmCommandMode.AUTOMATIC)
        self.assertIsNone(preset.slurm_bin_directory)
        self.assertIsNone(preset.slurm_account)
        self.assertIsNone(preset.slurm_partition)
        self.assertIsNone(preset.slurm_qos)
        self.assertIsNone(preset.slurm_aitranss_launch_mode)
        self.assertIsNone(preset.slurm_aitranss_srun_path)

    def test_scheduler_site_fields_are_normalized_and_isolated(self) -> None:
        slurm = replace(
            synthetic_slurm_preset(),
            slurm_account=" account-a ",
            slurm_partition="partition-a",
            slurm_qos="qos-a",
        )
        self.assertEqual(slurm.slurm_account, "account-a")
        self.assertEqual(slurm.slurm_partition, "partition-a")
        self.assertEqual(slurm.slurm_qos, "qos-a")
        self.assertIsNone(replace(slurm, slurm_account=" ").slurm_account)

        lsf = SlurmExecutionPreset(
            nodes=2,
            ntasks=8,
            cpus_per_task=1,
            runtime_minutes=60,
            memory_gb=16,
            launch_command="mpirun aims.x",
            unset_slurm_export_env=False,
            omp_num_threads=1,
            scheduler_kind=SchedulerKind.LSF,
            lsf_queue=" normal ",
            lsf_project="project-a",
            lsf_resource_requirement_mode=(
                LsfResourceRequirementMode.SPAN_RUSAGE
            ),
        )
        self.assertEqual(lsf.lsf_queue, "normal")
        self.assertEqual(lsf.lsf_project, "project-a")

        for changes in (
            {"lsf_queue": "normal"},
            {"lsf_project": "project-a"},
            {"lsf_resource_requirement_mode": LsfResourceRequirementMode.SITE_DEFAULT},
        ):
            with self.subTest(changes=changes), self.assertRaises(
                ServerProfileValidationError
            ):
                replace(slurm, **changes)
        for changes in (
            {"slurm_account": "account-a"},
            {"slurm_partition": "partition-a"},
            {"slurm_qos": "qos-a"},
            {"slurm_aitranss_launch_mode": SlurmAitranssLaunchMode.DIRECT},
        ):
            with self.subTest(changes=changes), self.assertRaises(
                ServerProfileValidationError
            ):
                replace(lsf, **changes)

    def test_scheduler_site_fields_reject_directive_syntax(self) -> None:
        valid = synthetic_slurm_preset()
        for field_name in ("slurm_account", "slurm_partition", "slurm_qos"):
            for value in (
                "two values",
                "safe\n#SBATCH --nodes=9",
                "#SBATCH",
                "$(command)",
                "'quoted'",
            ):
                with self.subTest(field_name=field_name, value=value), self.assertRaises(
                    ServerProfileValidationError
                ):
                    replace(valid, **{field_name: value})

    def test_slurm_aitranss_launch_mode_validates_srun_path(self) -> None:
        valid = synthetic_slurm_preset()
        direct = replace(
            valid,
            slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.DIRECT,
            slurm_aitranss_srun_path=None,
        )
        self.assertIs(direct.slurm_aitranss_launch_mode, SlurmAitranssLaunchMode.DIRECT)
        unresolved = replace(
            valid,
            slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.SRUN,
            slurm_aitranss_srun_path=None,
        )
        self.assertIsNone(unresolved.slurm_aitranss_srun_path)
        with self.assertRaises(ServerProfileValidationError):
            replace(direct, slurm_aitranss_srun_path="/usr/bin/srun")
        for path in ("srun", "/usr/bin/mpirun", "/usr/../bin/srun", "/usr/bin/srun;id"):
            with self.subTest(path=path), self.assertRaises(ServerProfileValidationError):
                replace(valid, slurm_aitranss_srun_path=path)

    def test_output_file_name_can_be_empty_but_must_not_be_a_path(self) -> None:
        valid = synthetic_slurm_preset()

        self.assertEqual(
            replace(valid, slurm_output_filename="").slurm_output_filename,
            "",
        )
        self.assertEqual(
            replace(valid, slurm_output_filename=" aims.out ").slurm_output_filename,
            "aims.out",
        )
        for value in (
            "/srv/moltage-test/users/scientist/job-logs/aims.out",
            "/srv/moltage-test/users/scientist/../aims.out",
            "logs/aims.out",
            "logs\\aims.out",
        ):
            with self.subTest(value=value), self.assertRaises(
                ServerProfileValidationError
            ):
                replace(valid, slurm_output_filename=value)

    def test_empty_output_file_name_round_trips_in_profile_json(self) -> None:
        output_file = ""
        item = profile(save_password=False)
        item = replace(
            item,
            execution_preset=replace(
                item.execution_preset,
                slurm_output_filename=output_file,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(
                Path(directory) / "profiles.json"
            )

            repository.save(item)
            reloaded = repository.load().profiles[0]

        self.assertEqual(
            reloaded.execution_preset.slurm_output_filename,
            output_file,
        )

    def test_scheduler_site_configuration_round_trips_in_schema_11(self) -> None:
        original = profile(save_password=False)
        configured = replace(
            original,
            execution_preset=replace(
                original.execution_preset,
                slurm_account="account-a",
                slurm_partition="partition-a",
                slurm_qos="qos-a",
                slurm_aitranss_launch_mode=SlurmAitranssLaunchMode.SRUN,
                slurm_aitranss_srun_path="/usr/bin/srun",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(configured)
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            reloaded = repository.load().profiles[0]

        self.assertEqual(document["schema_version"], 12)
        self.assertEqual(reloaded, configured)
        persisted = document["profiles"][0]["execution_preset"]
        self.assertEqual(persisted["slurm_account"], "account-a")
        self.assertEqual(persisted["slurm_partition"], "partition-a")
        self.assertEqual(persisted["slurm_qos"], "qos-a")
        self.assertIsNone(persisted["lsf_queue"])
        self.assertIsNone(persisted["lsf_project"])
        self.assertIsNone(persisted["lsf_resource_requirement_mode"])
        self.assertEqual(persisted["slurm_aitranss_launch_mode"], "SRUN")
        self.assertEqual(persisted["slurm_aitranss_srun_path"], "/usr/bin/srun")

    def test_schema_10_slurm_migration_reuses_only_existing_srun_evidence(self) -> None:
        item = profile(save_password=False)
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(item)
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            document["schema_version"] = 10
            preset = document["profiles"][0]["execution_preset"]
            for field_name in (
                "slurm_account",
                "slurm_partition",
                "slurm_qos",
                "lsf_queue",
                "lsf_project",
                "lsf_resource_requirement_mode",
                "slurm_aitranss_launch_mode",
                "slurm_aitranss_srun_path",
            ):
                preset.pop(field_name, None)
            preset["fhi_runtime"] = {
                "executable_path": "/opt/fhi-aims/bin/aims.x",
                "launcher_path": "/opt/slurm/bin/srun",
                "environment": {
                    "mode": "NONE",
                    "modules": [],
                    "setup_script": None,
                },
            }
            repository.path.write_text(json.dumps(document), encoding="utf-8")
            before = repository.path.read_bytes()

            migrated = repository.load().profiles[0].execution_preset

            self.assertIs(
                migrated.slurm_aitranss_launch_mode,
                SlurmAitranssLaunchMode.SRUN,
            )
            self.assertEqual(
                migrated.slurm_aitranss_srun_path,
                "/opt/slurm/bin/srun",
            )
            self.assertIsNone(migrated.slurm_account)
            self.assertIsNone(migrated.slurm_partition)
            self.assertIsNone(migrated.slurm_qos)
            self.assertEqual(repository.path.read_bytes(), before)

            preset["fhi_runtime"]["launcher_path"] = "/opt/mpi/bin/mpirun"
            repository.path.write_text(json.dumps(document), encoding="utf-8")
            unresolved = repository.load().profiles[0].execution_preset
            self.assertIs(
                unresolved.slurm_aitranss_launch_mode,
                SlurmAitranssLaunchMode.SRUN,
            )
            self.assertIsNone(unresolved.slurm_aitranss_srun_path)

    def test_schema_10_lsf_migration_preserves_span_rusage(self) -> None:
        original = profile(save_password=False)
        lsf_preset = SlurmExecutionPreset(
            nodes=2,
            ntasks=8,
            cpus_per_task=1,
            runtime_minutes=60,
            memory_gb=16,
            launch_command="mpirun aims.x",
            unset_slurm_export_env=False,
            omp_num_threads=1,
            scheduler_kind=SchedulerKind.LSF,
            lsf_queue="normal",
            lsf_project="project-a",
            lsf_resource_requirement_mode=LsfResourceRequirementMode.SPAN_RUSAGE,
        )
        item = replace(original, execution_preset=lsf_preset)
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(item)
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            document["schema_version"] = 10
            preset = document["profiles"][0]["execution_preset"]
            for field_name in (
                "slurm_account",
                "slurm_partition",
                "slurm_qos",
                "lsf_queue",
                "lsf_project",
                "lsf_resource_requirement_mode",
                "slurm_aitranss_launch_mode",
                "slurm_aitranss_srun_path",
            ):
                preset.pop(field_name, None)
            repository.path.write_text(json.dumps(document), encoding="utf-8")
            before = repository.path.read_bytes()

            migrated = repository.load().profiles[0].execution_preset
            self.assertIs(
                migrated.lsf_resource_requirement_mode,
                LsfResourceRequirementMode.SPAN_RUSAGE,
            )
            self.assertIsNone(migrated.lsf_queue)
            self.assertIsNone(migrated.lsf_project)
            self.assertEqual(repository.path.read_bytes(), before)

    def test_legacy_schema_loads_and_next_save_writes_only_canonical_units(self) -> None:
        profile_id = UUID("22222222-2222-4222-8222-222222222222")
        legacy_document = {
            "schema_version": 1,
            "last_selected_profile_id": str(profile_id),
            "profiles": [
                {
                    "profile_id": str(profile_id),
                    "name": "ExampleCluster",
                    "host": "cluster.example.org",
                    "port": 22,
                    "username": "scientist",
                    "remote_project_root": "/srv/moltage-test/projects",
                    "save_password": True,
                    "auto_connect": True,
                    "execution_preset": {
                        "nodes": 1,
                        "ntasks": 24,
                        "cpus_per_task": 1,
                        "wall_time": "36:00:00",
                        "memory": "128G",
                        "no_requeue": True,
                        "export_none": True,
                        "unset_slurm_export_env": True,
                        "omp_num_threads": 1,
                        "module_purge": True,
                        "modules": [
                            "mpi/example-1.0",
                            "chemistry/fhi-aims-example",
                        ],
                        "launch_command": "srun aims.x",
                        "slurm_output_filename": "aims.out",
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "profiles.json"
            known_hosts = root / "known_hosts"
            known_hosts.write_text("trusted-host-key\n", encoding="utf-8")
            original_known_hosts = known_hosts.read_bytes()
            path.write_text(json.dumps(legacy_document), encoding="utf-8")
            repository = ServerProfileRepository(path)
            secrets = MemorySecretStore()
            secrets.set_password(profile_id, "saved-secret")

            collection = repository.load()
            migrated = collection.profiles[0]
            self.assertEqual(migrated.profile_id, profile_id)
            self.assertEqual(collection.last_selected_profile_id, profile_id)
            self.assertEqual(migrated.name, "ExampleCluster")
            self.assertEqual(migrated.host, "cluster.example.org")
            self.assertEqual(migrated.port, 22)
            self.assertEqual(migrated.username, "scientist")
            self.assertEqual(migrated.remote_project_root, "/srv/moltage-test/projects")
            self.assertEqual(migrated.execution_preset.runtime_minutes, 2160)
            self.assertEqual(migrated.execution_preset.memory_gb, 128)

            ServerProfileService(repository, secrets).save(migrated)
            canonical = json.loads(path.read_text(encoding="utf-8"))
            canonical_preset = canonical["profiles"][0]["execution_preset"]
            self.assertEqual(canonical["schema_version"], 12)
            self.assertEqual(canonical_preset["runtime_minutes"], 2160)
            self.assertEqual(canonical_preset["memory_gb"], 128)
            self.assertNotIn("wall_time", canonical_preset)
            self.assertNotIn("memory", canonical_preset)
            self.assertEqual(
                canonical_preset["slurm_command_mode"],
                "AUTOMATIC",
            )
            self.assertIsNone(canonical_preset["slurm_bin_directory"])
            self.assertEqual(
                canonical_preset["slurm_aitranss_launch_mode"],
                "SRUN",
            )
            self.assertIsNone(canonical_preset["slurm_aitranss_srun_path"])
            self.assertEqual(secrets.get_password(profile_id), "saved-secret")
            self.assertEqual(known_hosts.read_bytes(), original_known_hosts)

    def test_unsafe_legacy_units_fail_with_reentry_guidance(self) -> None:
        legacy = {
            "schema_version": 1,
            "last_selected_profile_id": None,
            "profiles": [],
        }
        item = profile()
        legacy_profile = {
            "profile_id": str(item.profile_id),
            "name": item.name,
            "host": item.host,
            "port": item.port,
            "username": item.username,
            "remote_project_root": item.remote_project_root,
            "save_password": item.save_password,
            "auto_connect": item.auto_connect,
            "execution_preset": {
                "nodes": 1,
                "ntasks": 24,
                "cpus_per_task": 1,
                "wall_time": "36:00:30",
                "memory": "128M",
                "no_requeue": True,
                "export_none": True,
                "unset_slurm_export_env": True,
                "omp_num_threads": 1,
                "module_purge": True,
                "modules": [],
                "launch_command": "srun aims.x",
                "slurm_output_filename": "aims.out",
            },
        }
        legacy["profiles"].append(legacy_profile)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with self.assertRaisesRegex(
                ServerProfileRepositoryError,
                "re-enter Cluster Execution Settings",
            ):
                ServerProfileRepository(path).load()

    def test_profile_save_reload_rename_save_as_and_last_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            secrets = MemorySecretStore()
            repository = ServerProfileRepository(path)
            service = ServerProfileService(repository, secrets)
            original = profile()
            service.save(original, supplied_password="stored-secret")

            reloaded = ServerProfileRepository(path).load()
            self.assertEqual(reloaded.profiles, (original,))
            self.assertEqual(reloaded.last_selected_profile_id, original.profile_id)
            self.assertEqual(
                UUID(json.loads(path.read_text(encoding="utf-8"))["profiles"][0]["profile_id"]),
                original.profile_id,
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"],
                12,
            )
            runtime_document = json.loads(
                path.read_text(encoding="utf-8")
            )["profiles"][0]["aitranss_runtime"]
            self.assertEqual(
                runtime_document,
                {
                    "modules": list(original.aitranss_runtime.modules),
                    "executable_path": original.aitranss_runtime.executable_path,
                    "environment": None,
                },
            )

            renamed = replace(original, name="Vienna")
            service.save(renamed)
            self.assertEqual(repository.load().profiles[0].profile_id, original.profile_id)
            copied = service.save_as(renamed, "cluster_1")
            self.assertNotEqual(copied.profile_id, original.profile_id)
            self.assertEqual(repository.load().last_selected_profile_id, copied.profile_id)

    def test_schema_3_profile_loads_without_aitranss_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(profile())
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            document["schema_version"] = 3
            document["profiles"][0].pop("aitranss_runtime")
            repository.path.write_text(json.dumps(document), encoding="utf-8")

            migrated = repository.load().profiles[0]

            self.assertIsNone(migrated.aitranss_runtime)

    def test_aitranss_runtime_rejects_unsafe_or_relative_paths(self) -> None:
        for path in (
            "aitranss.synthetic.x",
            "/opt/../aitranss.synthetic.x",
            "/opt/aitranss.synthetic.x;rm",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ServerProfileValidationError):
                    AitranssRuntimeConfiguration(
                        modules=("chemistry/aitranss-example",),
                        executable_path=path,
                    )

    def test_notification_settings_round_trip_without_changing_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(
                Path(directory) / "profiles.json"
            )
            original = profile(save_password=False, auto=False)
            enabled = replace(
                original,
                email_notification_enabled=True,
                email_notification_recipient="  user@example.com  ",
            )

            repository.save(enabled)
            reloaded = repository.load().profiles[0]
            document = json.loads(repository.path.read_text(encoding="utf-8"))

            self.assertEqual(reloaded, enabled)
            self.assertEqual(
                reloaded.email_notification_recipient,
                "user@example.com",
            )
            self.assertTrue(reloaded.email_notification_enabled)
            self.assertEqual(reloaded.host, original.host)
            self.assertEqual(reloaded.execution_preset, original.execution_preset)
            self.assertFalse(reloaded.save_password)
            self.assertFalse(reloaded.auto_connect)
            self.assertTrue(
                document["profiles"][0]["email_notification_enabled"]
            )
            self.assertEqual(
                document["profiles"][0]["email_notification_recipient"],
                "user@example.com",
            )

    def test_legacy_profile_without_notification_fields_loads_disabled(self) -> None:
        item = profile()
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(
                Path(directory) / "profiles.json"
            )
            repository.save(item)
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            document["profiles"][0].pop("email_notification_enabled")
            document["profiles"][0].pop("email_notification_recipient")
            repository.path.write_text(json.dumps(document), encoding="utf-8")

            reloaded = repository.load().profiles[0]

            self.assertFalse(reloaded.email_notification_enabled)
            self.assertIsNone(reloaded.email_notification_recipient)
            self.assertIsNone(reloaded.slurm_mail_settings)

    def test_invalid_enabled_persisted_recipient_fails_during_local_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(
                Path(directory) / "profiles.json"
            )
            repository.save(profile())
            document = json.loads(repository.path.read_text(encoding="utf-8"))
            document["profiles"][0]["email_notification_enabled"] = True
            document["profiles"][0]["email_notification_recipient"] = (
                "one@example.com,two@example.com"
            )
            repository.path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(
                ServerProfileRepositoryError,
                "email notification recipient",
            ):
                repository.load()

    def test_notification_address_validation_blocks_unsafe_enabled_profiles(self) -> None:
        normalized = replace(
            profile(),
            email_notification_enabled=True,
            email_notification_recipient="  user@example.com  ",
        )
        self.assertEqual(
            normalized.slurm_mail_settings.recipient,
            "user@example.com",
        )
        disabled = replace(
            profile(),
            email_notification_enabled=False,
            email_notification_recipient="   ",
        )
        self.assertIsNone(disabled.email_notification_recipient)

        for recipient in (
            "",
            "no-at-sign",
            "user@example.com\n#SBATCH --mail-type=ALL",
            "user@example.com\rmalicious",
            "user@example.com\x00",
            "user@example.com\x1f",
            "one@example.com,two@example.com",
            "user@example.com #SBATCH --mail-type=ALL",
        ):
            with self.subTest(recipient=repr(recipient)):
                with self.assertRaises(ServerProfileValidationError):
                    replace(
                        profile(),
                        email_notification_enabled=True,
                        email_notification_recipient=recipient,
                    )

    def test_names_are_unique_case_insensitively_without_silent_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ServerProfileService(
                ServerProfileRepository(Path(directory) / "profiles.json"),
                MemorySecretStore(),
            )
            service.save(profile("ExampleCluster"))
            with self.assertRaisesRegex(
                ServerProfileRepositoryError,
                "already exists",
            ):
                service.save(profile("examplecluster"))

    def test_remote_root_trailing_slash_is_normalized(self) -> None:
        normalized = replace(profile(), remote_project_root="/srv/moltage-test/projects/")
        self.assertEqual(normalized.remote_project_root, "/srv/moltage-test/projects")

    def test_password_save_disable_and_delete_semantics_never_touch_remote_data(self) -> None:
        sentinel = "DO_NOT_PERSIST_OR_LOG_ME_84729"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote_project = root / "unrelated-remote-project"
            remote_project.mkdir()
            repository = ServerProfileRepository(root / "profiles.json")
            secrets = MemorySecretStore()
            service = ServerProfileService(repository, secrets)
            item = profile()

            service.save(item, supplied_password=sentinel)
            self.assertEqual(secrets.passwords[item.profile_id], sentinel)
            serialized = repository.path.read_text(encoding="utf-8")
            self.assertNotIn(sentinel, serialized)
            serialized_profile = json.loads(serialized)["profiles"][0]
            self.assertNotIn("password", serialized_profile)
            self.assertNotIn(sentinel, repr(item))

            disabled = replace(item, save_password=False)
            service.save(disabled)
            self.assertNotIn(item.profile_id, secrets.passwords)

            service.save(item, supplied_password=sentinel)
            service.delete(item.profile_id)
            self.assertNotIn(item.profile_id, secrets.passwords)
            self.assertTrue(remote_project.is_dir())

    def test_schema_2_migrates_to_automatic_and_discovery_cache_is_narrow(self) -> None:
        item = profile()
        preset = item.execution_preset
        schema_2 = {
            "schema_version": 2,
            "last_selected_profile_id": str(item.profile_id),
            "profiles": [
                {
                    "profile_id": str(item.profile_id),
                    "name": item.name,
                    "host": item.host,
                    "port": item.port,
                    "username": item.username,
                    "remote_project_root": item.remote_project_root,
                    "save_password": item.save_password,
                    "auto_connect": item.auto_connect,
                    "execution_preset": {
                        "nodes": preset.nodes,
                        "ntasks": preset.ntasks,
                        "cpus_per_task": preset.cpus_per_task,
                        "runtime_minutes": preset.runtime_minutes,
                        "memory_gb": preset.memory_gb,
                        "no_requeue": preset.no_requeue,
                        "export_none": preset.export_none,
                        "unset_slurm_export_env": preset.unset_slurm_export_env,
                        "omp_num_threads": preset.omp_num_threads,
                        "module_purge": preset.module_purge,
                        "modules": list(preset.modules),
                        "launch_command": preset.launch_command,
                        "slurm_output_filename": preset.slurm_output_filename,
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "profiles.json"
            known_hosts = root / "known_hosts"
            known_hosts.write_bytes(b"unchanged-known-host\n")
            path.write_text(json.dumps(schema_2), encoding="utf-8")
            secrets = MemorySecretStore()
            secrets.set_password(item.profile_id, "saved-secret")
            repository = ServerProfileRepository(path)

            migrated = repository.load().profiles[0]
            self.assertEqual(migrated.profile_id, item.profile_id)
            self.assertEqual(migrated.name, item.name)
            self.assertEqual(migrated.host, item.host)
            self.assertEqual(migrated.execution_preset.nodes, preset.nodes)
            self.assertIs(
                migrated.execution_preset.slurm_command_mode,
                SlurmCommandMode.AUTOMATIC,
            )
            self.assertIsNone(migrated.execution_preset.slurm_bin_directory)

            repository.cache_automatic_slurm_directory(
                item.profile_id,
                "/opt/slurm_26-05-2-1/bin",
            )
            reloaded = repository.load().profiles[0]

            self.assertEqual(reloaded.profile_id, item.profile_id)
            self.assertEqual(
                reloaded.execution_preset.slurm_bin_directory,
                "/opt/slurm_26-05-2-1/bin",
            )
            self.assertEqual(secrets.get_password(item.profile_id), "saved-secret")
            self.assertEqual(known_hosts.read_bytes(), b"unchanged-known-host\n")
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"],
                12,
            )

    def test_species_root_is_validated_and_round_trips_per_profile(self) -> None:
        root_a = "/opt/fhi-aims/species_defaults"
        root_b = "/shared/aims/2025/species_defaults"
        first = replace(
            profile("Alpha"),
            execution_preset=replace(
                profile("Alpha preset").execution_preset,
                fhi_species_defaults_path=root_a,
            ),
            runtime_hints=RuntimeDiscoveryHints(
                fhi_species_defaults_path=root_a,
            ),
        )
        second = replace(
            profile("Beta"),
            execution_preset=replace(
                profile("Beta preset").execution_preset,
                fhi_species_defaults_path=root_b,
            ),
            runtime_hints=RuntimeDiscoveryHints(
                fhi_species_defaults_path=root_b,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(
                Path(directory) / "profiles.json"
            )
            repository.save(first)
            repository.save(second)
            reloaded = {
                item.name: item for item in repository.load().profiles
            }

        self.assertEqual(
            reloaded["Alpha"].execution_preset.fhi_species_defaults_path,
            root_a,
        )
        self.assertEqual(
            reloaded["Alpha"].runtime_hints.fhi_species_defaults_path,
            root_a,
        )
        self.assertEqual(
            reloaded["Beta"].execution_preset.fhi_species_defaults_path,
            root_b,
        )
        self.assertEqual(
            reloaded["Beta"].runtime_hints.fhi_species_defaults_path,
            root_b,
        )

    def test_species_root_rejects_missing_relative_or_noncanonical_paths(self) -> None:
        for value in (
            "",
            "species_defaults",
            "/",
            "/opt//species_defaults",
            "/opt/../species_defaults",
            "/opt/species_defaults/.",
            "/opt/species_defaults;rm",
        ):
            with self.subTest(value=value), self.assertRaises(
                ServerProfileValidationError
            ):
                validate_fhi_species_defaults_path(value)

    def test_schema_9_loads_species_root_as_unresolved_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ServerProfileRepository(
                Path(directory) / "profiles.json"
            )
            repository.save(profile())
            document = json.loads(
                repository.path.read_text(encoding="utf-8")
            )
            document["schema_version"] = 9
            preset = document["profiles"][0]["execution_preset"]
            preset.pop("fhi_species_defaults_path")
            hints = document["profiles"][0]["runtime_hints"]
            if hints is not None:
                hints.pop("fhi_species_defaults_path")
            repository.path.write_text(
                json.dumps(document, sort_keys=True),
                encoding="utf-8",
            )
            before = repository.path.read_bytes()

            loaded = repository.load().profiles[0]

            self.assertIsNone(
                loaded.execution_preset.fhi_species_defaults_path
            )
            self.assertIsNone(
                loaded.runtime_hints
                and loaded.runtime_hints.fhi_species_defaults_path
            )
            self.assertEqual(repository.path.read_bytes(), before)

    def test_manual_mode_requires_a_safe_directory_not_an_sbatch_path(self) -> None:
        with self.assertRaisesRegex(
            ServerProfileValidationError,
            "requires the directory containing sbatch",
        ):
            replace(
                synthetic_slurm_preset(),
                slurm_command_mode=SlurmCommandMode.MANUAL,
                slurm_bin_directory=None,
            )
        with self.assertRaisesRegex(
            ServerProfileValidationError,
            "directory containing sbatch",
        ):
            replace(
                synthetic_slurm_preset(),
                slurm_command_mode=SlurmCommandMode.MANUAL,
                slurm_bin_directory="/opt/slurm/bin/sbatch",
            )


if __name__ == "__main__":
    unittest.main()
