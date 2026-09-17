from pathlib import Path
import tempfile
import unittest

import paramiko

from moltage.remote.known_hosts import (
    HostKeyMismatch,
    KnownHostStore,
    UnknownHostKey,
    host_key_info,
    host_key_identity,
)


class KnownHostStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.first_key = paramiko.RSAKey.generate(1024)
        cls.second_key = paramiko.RSAKey.generate(1024)

    def test_unknown_trust_persist_and_matching_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            store = KnownHostStore(path)

            with self.assertRaises(UnknownHostKey) as caught:
                store.verify("cluster.example.org", 22, self.first_key)
            info = caught.exception.info
            self.assertEqual(info.hostname, "cluster.example.org")
            self.assertEqual(info.port, 22)
            self.assertEqual(info.algorithm, self.first_key.get_name())
            self.assertTrue(info.sha256_fingerprint.startswith("SHA256:"))
            self.assertEqual(info.public_key_base64, self.first_key.get_base64())

            store.trust(info)
            self.assertTrue(path.is_file())
            KnownHostStore(path).verify("cluster.example.org", 22, self.first_key)

    def test_mismatch_is_hard_failure_with_both_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = KnownHostStore(Path(directory) / "known_hosts")
            try:
                store.verify("cluster.example.org", 22, self.first_key)
            except UnknownHostKey as unknown:
                store.trust(unknown.info)

            with self.assertRaises(HostKeyMismatch) as caught:
                store.verify("cluster.example.org", 22, self.second_key)
            self.assertIn("stored SHA256:", str(caught.exception))
            self.assertIn("received SHA256:", str(caught.exception))
            with self.assertRaises(HostKeyMismatch):
                store.trust(
                    host_key_info("cluster.example.org", 22, self.second_key)
                )

    def test_default_and_nondefault_ports_have_distinct_identities(self) -> None:
        self.assertEqual(host_key_identity("cluster", 22), "cluster")
        self.assertEqual(host_key_identity("cluster", 2222), "[cluster]:2222")
        with tempfile.TemporaryDirectory() as directory:
            store = KnownHostStore(Path(directory) / "known_hosts")
            info_22 = _unknown_info(store, "cluster", 22, self.first_key)
            store.trust(info_22)
            with self.assertRaises(UnknownHostKey) as nondefault:
                store.verify("cluster", 2222, self.first_key)
            store.trust(nondefault.exception.info)
            store.verify("cluster", 22, self.first_key)
            store.verify("cluster", 2222, self.first_key)


def _unknown_info(store, hostname, port, key):
    try:
        store.verify(hostname, port, key)
    except UnknownHostKey as error:
        return error.info
    raise AssertionError("expected unknown host key")


if __name__ == "__main__":
    unittest.main()
