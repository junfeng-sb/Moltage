from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from moltage.app.local_project_index import (
    KnownProjectReference,
    LocalProjectIndexError,
    LocalProjectIndexRepository,
    RecycledProjectReference,
)
from phase2b1_test_support import example_project


PROFILE_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SUBMITTED_AT = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
RECYCLED_AT = datetime(2030, 8, 31, 15, 0, tzinfo=timezone.utc)


class LocalProjectIndexTests(unittest.TestCase):
    def test_last_seen_revision_is_local_and_does_not_mutate_remote_project(self) -> None:
        remote_project = replace(example_project(), revision=5)
        reference = KnownProjectReference(
            project_id=remote_project.project_id,
            server_profile_id=remote_project.server_profile_id,
            remote_project_path=remote_project.remote_project_path,
            display_name=remote_project.display_name,
            last_seen_revision=3,
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            repository.save(reference)
            loaded = repository.load()[0]
            self.assertTrue(loaded.has_unseen_change(remote_project))

            marked = repository.mark_seen(remote_project)

            self.assertEqual(marked.last_seen_revision, 5)
            self.assertFalse(marked.has_unseen_change(remote_project))
            self.assertEqual(remote_project.revision, 5)
            serialized = repository.path.read_text(encoding="utf-8")
            self.assertNotIn("DO_NOT_PERSIST_OR_LOG_ME_84729", serialized)
            self.assertNotIn("password", serialized.casefold())

    def test_profile_binding_requires_exact_project_id_profile_and_remote_path(self) -> None:
        project = example_project()
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            repository.mark_seen(
                project,
                bound_server_profile_id=PROFILE_A,
            )

            self.assertTrue(
                repository.is_bound_to_profile(
                    project,
                    bound_server_profile_id=PROFILE_A,
                )
            )
            self.assertFalse(
                repository.is_bound_to_profile(
                    project,
                    bound_server_profile_id=PROFILE_B,
                )
            )
            self.assertFalse(
                repository.is_bound_to_profile(
                    replace(
                        project,
                        remote_project_path=(
                            "/other/workspace/" + project.remote_directory_name
                        ),
                    ),
                    bound_server_profile_id=PROFILE_A,
                )
            )

    def test_schema1_loads_and_next_write_migrates_to_schema2(self) -> None:
        project = example_project()
        legacy = {
            "schema_version": 1,
            "projects": [
                {
                    "project_id": str(project.project_id),
                    "server_profile_id": str(PROFILE_A),
                    "remote_project_path": project.remote_project_path,
                    "display_name": project.display_name,
                    "last_seen_revision": 1,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_projects.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            repository = LocalProjectIndexRepository(path)

            self.assertEqual(len(repository.load()), 1)
            self.assertEqual(repository.load_recycled(), ())
            repository.save(repository.load()[0])

            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], 3)
            self.assertEqual(migrated["recycled_projects"], [])
            self.assertEqual(
                migrated["projects"][0]["project_id"],
                str(project.project_id),
            )

    def test_recycle_persists_across_repository_restart_without_secrets(self) -> None:
        project = replace(example_project(), revision=7)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_projects.json"
            repository = LocalProjectIndexRepository(path)

            stored = repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=SUBMITTED_AT,
                recycled_at=RECYCLED_AT,
            )
            reopened = LocalProjectIndexRepository(path)

            self.assertEqual(reopened.load_recycled(), (stored,))
            self.assertTrue(reopened.is_recycled(PROFILE_A, project.project_id))
            self.assertFalse(reopened.is_recycled(PROFILE_B, project.project_id))
            known = reopened.load()[0]
            self.assertEqual(known.server_profile_id, PROFILE_A)
            self.assertEqual(known.last_seen_revision, 7)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("password", raw.casefold())
            self.assertNotIn("DO_NOT_PERSIST_OR_LOG_ME_84729", raw)
            self.assertEqual(json.loads(raw)["schema_version"], 3)

    def test_recycle_identity_is_profile_uuid_plus_project_uuid(self) -> None:
        project = example_project()
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            entry_a = repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=SUBMITTED_AT,
                recycled_at=RECYCLED_AT,
            )
            entry_b = repository.recycle(
                project,
                bound_server_profile_id=PROFILE_B,
                submitted_at=None,
                recycled_at=RECYCLED_AT,
            )

            self.assertEqual(repository.load_recycled(server_profile_id=PROFILE_A), (entry_a,))
            self.assertEqual(repository.load_recycled(server_profile_id=PROFILE_B), (entry_b,))
            self.assertEqual(len(repository.load_recycled()), 2)

            self.assertEqual(repository.restore(PROFILE_A, project.project_id), entry_a)
            self.assertFalse(repository.is_recycled(PROFILE_A, project.project_id))
            self.assertTrue(repository.is_recycled(PROFILE_B, project.project_id))

    def test_recycling_same_identity_replaces_metadata_without_duplicates(self) -> None:
        project = example_project()
        later = datetime(2030, 9, 1, 9, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=SUBMITTED_AT,
                recycled_at=RECYCLED_AT,
            )
            replacement = repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=None,
                recycled_at=later,
            )

            self.assertEqual(repository.load_recycled(), (replacement,))
            self.assertIsNone(replacement.submitted_at)
            self.assertEqual(replacement.recycled_at, later)

    def test_normal_index_update_preserves_recycle_tombstones(self) -> None:
        project = example_project()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_projects.json"
            repository = LocalProjectIndexRepository(path)
            recycled = repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=SUBMITTED_AT,
                recycled_at=RECYCLED_AT,
            )

            repository.mark_seen(
                replace(project, revision=9),
                bound_server_profile_id=PROFILE_A,
            )

            self.assertEqual(LocalProjectIndexRepository(path).load_recycled(), (recycled,))
            self.assertEqual(repository.load()[0].last_seen_revision, 9)

    def test_restore_is_local_and_missing_identity_fails_explicitly(self) -> None:
        project = example_project()
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=SUBMITTED_AT,
                recycled_at=RECYCLED_AT,
            )

            restored = repository.restore(PROFILE_A, project.project_id)

            self.assertEqual(restored.project_id, project.project_id)
            self.assertEqual(repository.load_recycled(), ())
            self.assertEqual(len(repository.load()), 1)
            with self.assertRaisesRegex(
                LocalProjectIndexError,
                "recycled project entry is unavailable",
            ):
                repository.restore(PROFILE_A, project.project_id)

    def test_forget_removes_only_the_exact_bound_identity(self) -> None:
        project = example_project()
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            repository.recycle(
                project,
                bound_server_profile_id=PROFILE_A,
                submitted_at=SUBMITTED_AT,
                recycled_at=RECYCLED_AT,
            )

            repository.forget(PROFILE_A, project.project_id)

            self.assertEqual(repository.load(), ())
            self.assertEqual(repository.load_recycled(), ())

    def test_recycle_timestamp_fields_require_timezone_awareness(self) -> None:
        project = example_project()
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectIndexRepository(
                Path(directory) / "known_projects.json"
            )
            with self.assertRaisesRegex(LocalProjectIndexError, "timezone-aware"):
                repository.recycle(
                    project,
                    bound_server_profile_id=PROFILE_A,
                    submitted_at=datetime(2030, 1, 2, 12, 0),
                    recycled_at=RECYCLED_AT,
                )
            with self.assertRaisesRegex(LocalProjectIndexError, "timezone-aware"):
                RecycledProjectReference(
                    project_id=project.project_id,
                    server_profile_id=PROFILE_A,
                    remote_project_path=project.remote_project_path,
                    display_name=project.display_name,
                    submitted_at=SUBMITTED_AT,
                    recycled_at=datetime(2030, 8, 31, 15, 0),
                )


if __name__ == "__main__":
    unittest.main()
