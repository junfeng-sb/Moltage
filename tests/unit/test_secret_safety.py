from pathlib import Path
import tempfile
import unittest

import paramiko

from moltage.app.local_project_index import (
    KnownProjectReference,
    LocalProjectIndexRepository,
)
from moltage.app.server_profiles import (
    ServerProfileRepository,
    ServerProfileService,
)
from moltage.remote.executor import ConnectionRequest, RemoteExecutorError
from moltage.remote.known_hosts import KnownHostStore
from moltage.remote.paramiko_executor import ParamikoRemoteExecutor
from moltage.remote.project_manifest import serialize_project_manifest
from moltage.remote.secrets import WindowsCredentialSecretStore
from phase2b1_test_support import MemorySecretStore, example_project, profile


SENTINEL = "DO_NOT_PERSIST_OR_LOG_ME_84729"


class MemoryCredentialBackend:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def get_password(self, service, key):
        return self.values.get((service, key))

    def set_password(self, service, key, password):
        self.values[(service, key)] = password

    def delete_password(self, service, key):
        self.deleted.append((service, key))
        self.values.pop((service, key), None)


class FailingClient:
    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        raise paramiko.AuthenticationException(SENTINEL)

    def close(self):
        pass


class SecretSafetyTests(unittest.TestCase):
    def test_legacy_credential_remains_readable_but_new_writes_use_moltage(self) -> None:
        profile_id = example_project().server_profile_id
        key = f"server-profile:{profile_id}"
        backend = MemoryCredentialBackend()
        backend.values[("AIMS-Transport", key)] = "legacy-password"
        store = object.__new__(WindowsCredentialSecretStore)
        store._backend = backend

        self.assertEqual(store.get_password(profile_id), "legacy-password")
        store.set_password(profile_id, "moltage-password")
        self.assertEqual(
            backend.values[("Moltage", key)],
            "moltage-password",
        )
        self.assertEqual(store.get_password(profile_id), "moltage-password")

        store.delete_password(profile_id)

        self.assertNotIn(("Moltage", key), backend.values)
        self.assertNotIn(("AIMS-Transport", key), backend.values)
        self.assertEqual(
            backend.deleted,
            [("Moltage", key), ("AIMS-Transport", key)],
        )
    def test_sentinel_is_absent_from_all_non_secret_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret_store = MemorySecretStore()
            profile_repository = ServerProfileRepository(root / "profiles.json")
            item = profile()
            ServerProfileService(profile_repository, secret_store).save(
                item,
                supplied_password=SENTINEL,
            )
            project = example_project()
            index = LocalProjectIndexRepository(root / "projects.json")
            index.save(
                KnownProjectReference(
                    project.project_id,
                    project.server_profile_id,
                    project.remote_project_path,
                    project.display_name,
                    0,
                )
            )
            request = ConnectionRequest(item, SENTINEL)
            executor = ParamikoRemoteExecutor(
                KnownHostStore(root / "known_hosts"),
                client_factory=FailingClient,
            )
            with self.assertNoLogs(level="DEBUG"):
                with self.assertRaises(RemoteExecutorError) as caught:
                    executor.connect(request)

            surfaces = (
                repr(item),
                repr(request),
                profile_repository.path.read_text(encoding="utf-8"),
                serialize_project_manifest(project),
                index.path.read_text(encoding="utf-8"),
                str(caught.exception),
                repr(caught.exception),
            )
            for surface in surfaces:
                self.assertNotIn(SENTINEL, surface)
            self.assertEqual(secret_store.get_password(item.profile_id), SENTINEL)


if __name__ == "__main__":
    unittest.main()
