"""Offline tests for server-backed standalone FHI-aims input export."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.app.input_export import (
    AimsInputExportError,
    AimsInputExportRequest,
    AimsInputExportService,
)
from moltage.app.server_profiles import ServerProfileRepository
from moltage.domain.structure import Atom, MolecularStructure
from moltage.remote.executor import RemotePathNotFoundError, RemotePathStat
from phase2b1_test_support import profile
from species_test_support import install_remote_species


class ReadOnlySpeciesRemote:
    def __init__(self) -> None:
        self.files = {}
        self.stats = []
        self.reads = []
        self.commands = []
        self.created_directories = []
        self.writes = []
        self.closed = 0

    def stat(self, path):
        self.stats.append(path)
        if path not in self.files:
            raise RemotePathNotFoundError(path)
        return RemotePathStat(False, len(self.files[path]))

    def read_bytes(self, path):
        self.reads.append(path)
        if path not in self.files:
            raise RemotePathNotFoundError(path)
        return self.files[path]

    def execute(self, command):
        self.commands.append(command)
        raise AssertionError(f"standalone export must not execute: {command}")

    def mkdir(self, path):
        self.created_directories.append(path)
        raise AssertionError(f"standalone export must not create remotely: {path}")

    def write_bytes(self, path, data):
        self.writes.append((path, data))
        raise AssertionError(f"standalone export must not write remotely: {path}")

    def close(self):
        self.closed += 1


class MemoryConnection:
    def __init__(self, remote) -> None:
        self.remote = remote
        self.calls = []

    def connect_for_remote_operation(self, selected, supplied_password=None):
        self.calls.append((selected, supplied_password))
        return self.remote


class StandaloneInputExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = ServerProfileRepository(self.root / "profiles.json")
        self.profile = profile()
        self.repository.save(self.profile)
        structure = MolecularStructure(
            (
                Atom(0, "O", 0.0, 0.0, 0.0),
                Atom(1, "H", 0.8, 0.0, 0.0),
                Atom(2, "H", -0.2, 0.7, 0.0),
            )
        )
        self.plan = AimsOptimizationInputPlan(
            structure,
            AimsOptimizationSettings(),
        )
        self.remote = ReadOnlySpeciesRemote()
        install_remote_species(
            self.remote,
            self.plan.species_requirements,
            root=self.profile.execution_preset.fhi_species_defaults_path,
        )
        self.connection = MemoryConnection(self.remote)
        self.service = AimsInputExportService(
            self.connection,
            self.repository,
        )

    def request(self, destination=None):
        return AimsInputExportRequest(
            self.profile,
            self.plan,
            destination or self.root / "export",
            supplied_password="SECRET_TEST_ONLY",
        )

    def test_export_reads_saved_server_without_scheduler_or_remote_mutation(self):
        progress = []

        geometry, control = self.service.export(
            self.request(),
            progress=progress.append,
        )

        self.assertTrue(geometry.is_file())
        self.assertTrue(control.is_file())
        self.assertIn("# SYNTHETIC TEST SPECIES", control.read_text("utf-8"))
        self.assertEqual(self.remote.commands, [])
        self.assertEqual(self.remote.created_directories, [])
        self.assertEqual(self.remote.writes, [])
        self.assertEqual(self.remote.stats, self.remote.reads)
        self.assertEqual(len(self.remote.reads), len(self.plan.species_requirements))
        self.assertEqual(self.remote.closed, 1)
        self.assertTrue(any("Writing" in message for message in progress))

    def test_incomplete_acquisition_creates_no_local_destination(self):
        del self.remote.files[next(iter(self.remote.files))]
        destination = self.root / "must-not-exist"

        with self.assertRaisesRegex(RuntimeError, "species definition is missing"):
            self.service.export(self.request(destination))

        self.assertFalse(destination.exists())
        self.assertEqual(self.remote.commands, [])
        self.assertEqual(self.remote.created_directories, [])
        self.assertEqual(self.remote.writes, [])

    def test_unsaved_profile_is_rejected_before_connection(self):
        request = replace(
            self.request(),
            profile=replace(self.profile, profile_id=uuid4()),
        )

        with self.assertRaisesRegex(AimsInputExportError, "saved Server Connection"):
            self.service.export(request)

        self.assertEqual(self.connection.calls, [])

    def test_unresolved_species_root_is_rejected_before_connection(self):
        unresolved = replace(
            self.profile,
            execution_preset=replace(
                self.profile.execution_preset,
                fhi_species_defaults_path=None,
            ),
        )
        self.repository.save(unresolved)

        with self.assertRaisesRegex(AimsInputExportError, "species definitions root"):
            self.service.export(replace(self.request(), profile=unresolved))

        self.assertEqual(self.connection.calls, [])


if __name__ == "__main__":
    unittest.main()
