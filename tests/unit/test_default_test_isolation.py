"""Regression tests for the default suite's external-environment boundary."""

from __future__ import annotations

import os
from pathlib import Path
import socket
from uuid import uuid4

import keyring
import paramiko
import pytest

from moltage.app.paths import application_data_directory
from moltage.remote.executor import ConnectionRequest
from moltage.remote.known_hosts import KnownHostStore
from moltage.remote.paramiko_executor import ParamikoRemoteExecutor
from moltage.remote.secrets import WindowsCredentialSecretStore
from phase2b1_test_support import profile


class _FakeSftp:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSshClient:
    def __init__(self) -> None:
        self.connect_kwargs: dict[str, object] | None = None
        self.policy: object | None = None
        self.sftp = _FakeSftp()
        self.closed = False

    def load_host_keys(self, _path: str) -> None:
        raise AssertionError("the synthetic known-hosts path must not exist")

    def set_missing_host_key_policy(self, policy: object) -> None:
        self.policy = policy

    def connect(self, **kwargs: object) -> None:
        self.connect_kwargs = kwargs

    def open_sftp(self) -> _FakeSftp:
        return self.sftp

    def close(self) -> None:
        self.closed = True


class _MemoryCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.values.get((service, key))

    def set_password(self, service: str, key: str, password: str) -> None:
        self.values[(service, key)] = password

    def delete_password(self, service: str, key: str) -> None:
        self.values.pop((service, key), None)


def _connect_socket() -> None:
    candidate = socket.socket()
    try:
        candidate.connect(("127.0.0.1", 9))
    finally:
        candidate.close()


@pytest.mark.parametrize(
    "operation",
    (
        lambda: socket.create_connection(("127.0.0.1", 9)),
        lambda: socket.getaddrinfo("example.invalid", 22),
        _connect_socket,
    ),
)
def test_real_network_entry_points_fail_closed(operation) -> None:
    with pytest.raises(AssertionError, match="must not access a real network"):
        operation()


def test_native_credential_store_fails_closed() -> None:
    with pytest.raises(AssertionError, match="must not access the native credential"):
        keyring.get_keyring()
    with pytest.raises(AssertionError, match="must not access the native credential"):
        WindowsCredentialSecretStore()


def test_application_state_uses_test_owned_appdata(
    default_test_appdata_root: Path,
    original_appdata_value: str | None,
    tmp_path: Path,
) -> None:
    simulated_user_root = tmp_path / "simulated-user-appdata"
    simulated_user_root.mkdir()
    sentinel = simulated_user_root / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    selected_root = Path(os.environ["APPDATA"]).resolve()
    application_root = application_data_directory()
    application_root.mkdir(parents=True)
    (application_root / "synthetic-state.json").write_text("{}", encoding="utf-8")

    assert selected_root == default_test_appdata_root.resolve()
    assert application_root.is_relative_to(selected_root)
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert tuple(simulated_user_root.iterdir()) == (sentinel,)
    if original_appdata_value is not None:
        assert selected_root != Path(original_appdata_value).expanduser().resolve()


def test_injected_fake_ssh_client_remains_usable(tmp_path: Path) -> None:
    client = _FakeSshClient()
    executor = ParamikoRemoteExecutor(
        KnownHostStore(tmp_path / "known_hosts"),
        client_factory=lambda: client,
    )

    executor.connect(ConnectionRequest(profile(), "synthetic-password"))
    executor.close()

    assert client.connect_kwargs is not None
    assert client.connect_kwargs["password"] == "synthetic-password"
    assert client.sftp.closed
    assert client.closed


def test_injected_memory_credential_backend_remains_usable() -> None:
    profile_id = uuid4()
    store = object.__new__(WindowsCredentialSecretStore)
    store._backend = _MemoryCredentialBackend()

    store.set_password(profile_id, "synthetic-password")
    assert store.get_password(profile_id) == "synthetic-password"
    store.delete_password(profile_id)
    assert store.get_password(profile_id) is None


def test_pyproject_declares_pytest_test_extra() -> None:
    import tomllib

    project_root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "pytest" in metadata["project"]["optional-dependencies"]["test"]
