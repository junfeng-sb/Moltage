from dataclasses import replace
from datetime import datetime, timedelta, timezone
import posixpath
import stat
import unittest
from uuid import UUID

from moltage.remote.executor import (
    RemoteDirectoryEntry,
    RemotePathNotFoundError,
    RemotePathStat,
    RemoteRenameError,
)
from moltage.remote.project_manifest import serialize_project_manifest
from moltage.remote.project_repository import (
    RemoteProjectRepository,
    RemoteProjectRepositoryError,
)
from phase2b1_test_support import example_project


class MemoryRemoteExecutor:
    def __init__(self) -> None:
        self.files = {}
        self.directories = {"/", "/remote", "/remote/work"}
        self.operations = []
        self.fail_rename = False

    def connect(self, request):
        self.operations.append(("connect",))

    def close(self):
        self.operations.append(("close",))

    def stat(self, path):
        self.operations.append(("stat", path))
        if path in self.directories:
            return RemotePathStat(True)
        if path in self.files:
            return RemotePathStat(False, len(self.files[path]))
        raise RemotePathNotFoundError(f"remote path does not exist: {path}")

    def list_directory(self, path):
        self.operations.append(("list", path))
        if path not in self.directories:
            raise RemotePathNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        names = {}
        for directory in self.directories:
            if directory.startswith(prefix):
                remainder = directory[len(prefix):]
                if remainder and "/" not in remainder:
                    names[remainder] = True
        for file_path in self.files:
            if file_path.startswith(prefix):
                remainder = file_path[len(prefix):]
                if remainder and "/" not in remainder:
                    names[remainder] = False
        return tuple(
            RemoteDirectoryEntry(name, is_directory)
            for name, is_directory in sorted(names.items())
        )

    def mkdir(self, path):
        self.operations.append(("mkdir", path))
        self.directories.add(path)

    def read_bytes(self, path):
        self.operations.append(("read", path))
        try:
            return self.files[path]
        except KeyError:
            raise RemotePathNotFoundError(path) from None

    def write_bytes(self, path, data):
        self.operations.append(("write", path))
        self.files[path] = data

    def rename(self, source, destination):
        self.operations.append(("rename", source, destination))
        if self.fail_rename:
            raise RemoteRenameError("synthetic rename failure")
        self.files[destination] = self.files.pop(source)

    def execute(self, command):
        raise AssertionError("commands are outside this test")

    def add_project(self, project, *, metadata_directory=".moltage"):
        root = project.remote_project_path
        metadata = posixpath.join(root, metadata_directory)
        self.directories.update((root, metadata))
        self.files[posixpath.join(metadata, "project.json")] = (
            serialize_project_manifest(project).encode("utf-8")
        )


class RemoteProjectRepositoryTests(unittest.TestCase):
    def test_manifest_write_is_temporary_then_atomic_rename(self) -> None:
        remote = MemoryRemoteExecutor()
        project = example_project()
        remote.directories.update(
            (project.remote_project_path, project.remote_project_path + "/.moltage")
        )
        repository = RemoteProjectRepository(
            remote,
            temporary_id_factory=lambda: "fixed",
        )

        repository.write_initial(project)

        destination = project.remote_project_path + "/.moltage/project.json"
        self.assertEqual(
            remote.operations[-2:],
            [
                ("write", destination + ".tmp-fixed"),
                ("rename", destination + ".tmp-fixed", destination),
            ],
        )
        self.assertIn(destination, remote.files)

    def test_rename_failure_is_explicit_and_never_directly_truncates_manifest(self) -> None:
        remote = MemoryRemoteExecutor()
        project = example_project()
        remote.directories.update(
            (project.remote_project_path, project.remote_project_path + "/.moltage")
        )
        remote.fail_rename = True
        repository = RemoteProjectRepository(
            remote,
            temporary_id_factory=lambda: "failure",
        )

        with self.assertRaisesRegex(
            RemoteProjectRepositoryError,
            "atomic rename failed",
        ):
            repository.write_initial(project)
        writes = tuple(item[1] for item in remote.operations if item[0] == "write")
        self.assertEqual(
            writes,
            (project.remote_project_path + "/.moltage/project.json.tmp-failure",),
        )

    def test_revision_increments_exactly_once_after_successful_update(self) -> None:
        remote = MemoryRemoteExecutor()
        project = example_project()
        remote.add_project(project)
        repository = RemoteProjectRepository(remote, temporary_id_factory=lambda: "update")
        updated_at = project.updated_at + timedelta(minutes=1)

        updated = repository.persist_update(project, updated_at=updated_at)

        self.assertEqual(updated.revision, 2)
        self.assertEqual(repository.load(project.remote_project_path), updated)
        self.assertEqual(project.revision, 1)

    def test_legacy_manifest_is_loaded_and_updated_in_place(self) -> None:
        remote = MemoryRemoteExecutor()
        project = example_project()
        remote.add_project(project, metadata_directory=".aims_transport")
        repository = RemoteProjectRepository(
            remote,
            temporary_id_factory=lambda: "legacy-update",
        )

        loaded = repository.load(project.remote_project_path)
        updated = repository.persist_update(
            loaded,
            updated_at=loaded.updated_at + timedelta(minutes=1),
        )

        legacy_path = (
            project.remote_project_path + "/.aims_transport/project.json"
        )
        self.assertEqual(updated.revision, 2)
        self.assertIn(legacy_path, remote.files)
        self.assertNotIn(
            project.remote_project_path + "/.moltage/project.json",
            remote.files,
        )
        self.assertEqual(repository.load(project.remote_project_path), updated)

    def test_current_and_legacy_manifests_are_reported_as_ambiguous(self) -> None:
        remote = MemoryRemoteExecutor()
        project = example_project()
        remote.directories.add("/srv/moltage-test/projects")
        remote.add_project(project)
        remote.add_project(project, metadata_directory=".aims_transport")

        result = RemoteProjectRepository(remote).discover(
            "/srv/moltage-test/projects"
        )

        self.assertFalse(result.projects)
        self.assertEqual(len(result.problems), 1)
        self.assertIn("both current and legacy", result.problems[0].message)

    def test_discovery_returns_only_managed_projects_and_reports_bad_manifest(self) -> None:
        remote = MemoryRemoteExecutor()
        first = replace(
            example_project(),
            display_name="projectA",
            remote_directory_name="projectA",
            remote_project_path="/remote/work/projectA",
        )
        second = replace(
            example_project(),
            project_id=UUID("33333333-3333-4333-8333-333333333333"),
            display_name="projectB",
            remote_directory_name="projectB",
            remote_project_path="/remote/work/projectB",
        )
        remote.add_project(first)
        remote.add_project(second)
        remote.directories.add("/remote/work/unrelated_old_work")
        remote.files["/remote/work/unrelated_old_work/geometry.in"] = b"geometry"
        remote.directories.update(
            (
                "/remote/work/broken",
                "/remote/work/broken/.moltage",
            )
        )
        remote.files["/remote/work/broken/.moltage/project.json"] = b"{bad"

        result = RemoteProjectRepository(remote).discover("/remote/work")

        self.assertEqual(
            tuple(project.display_name for project in result.projects),
            ("projectA", "projectB"),
        )
        self.assertEqual(len(result.problems), 1)
        self.assertEqual(result.problems[0].remote_project_path, "/remote/work/broken")
        self.assertIn("malformed", result.problems[0].message)


if __name__ == "__main__":
    unittest.main()
