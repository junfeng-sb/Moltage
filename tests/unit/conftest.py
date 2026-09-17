"""Fail-closed process isolation for Moltage's default offline test suite."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import tempfile
from typing import Any

import keyring
import paramiko
import pytest


_NETWORK_ERROR = (
    "Moltage default tests must not access a real network or SSH endpoint; "
    "inject a fake remote boundary instead"
)
_CREDENTIAL_ERROR = (
    "Moltage default tests must not access the native credential store; "
    "inject a memory secret store instead"
)

_temporary_directory: tempfile.TemporaryDirectory[str] | None = None
_test_appdata_root: Path | None = None
_original_appdata: str | None = None
_original_socket_functions: dict[str, Any] = {}
_original_socket_methods: dict[str, Any] = {}
_original_paramiko_connect: Any = None
_original_keyring_functions: dict[str, Any] = {}


def _deny_network_access(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(_NETWORK_ERROR)


def _deny_credential_access(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(_CREDENTIAL_ERROR)


def pytest_configure(config: pytest.Config) -> None:
    """Install isolation before pytest imports unit-test modules."""

    del config

    global _temporary_directory
    global _test_appdata_root
    global _original_appdata
    global _original_paramiko_connect

    if _temporary_directory is not None:
        return

    _original_appdata = os.environ.get("APPDATA")
    _temporary_directory = tempfile.TemporaryDirectory(
        prefix="moltage-default-tests-"
    )
    _test_appdata_root = Path(_temporary_directory.name) / "appdata"
    _test_appdata_root.mkdir()
    os.environ["APPDATA"] = str(_test_appdata_root)

    for name in ("create_connection", "getaddrinfo"):
        _original_socket_functions[name] = getattr(socket, name)
        setattr(socket, name, _deny_network_access)
    for name in ("connect", "connect_ex"):
        _original_socket_methods[name] = getattr(socket.socket, name)
        setattr(socket.socket, name, _deny_network_access)

    _original_paramiko_connect = paramiko.SSHClient.connect
    paramiko.SSHClient.connect = _deny_network_access

    for name in ("get_keyring", "get_password", "set_password", "delete_password"):
        _original_keyring_functions[name] = getattr(keyring, name)
        setattr(keyring, name, _deny_credential_access)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore process globals after the default suite finishes."""

    del config

    global _temporary_directory
    global _test_appdata_root
    global _original_appdata
    global _original_paramiko_connect

    for name, original in _original_keyring_functions.items():
        setattr(keyring, name, original)
    _original_keyring_functions.clear()

    if _original_paramiko_connect is not None:
        paramiko.SSHClient.connect = _original_paramiko_connect
        _original_paramiko_connect = None

    for name, original in _original_socket_methods.items():
        setattr(socket.socket, name, original)
    _original_socket_methods.clear()
    for name, original in _original_socket_functions.items():
        setattr(socket, name, original)
    _original_socket_functions.clear()

    if _original_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _original_appdata
    _original_appdata = None

    if _temporary_directory is not None:
        _temporary_directory.cleanup()
        _temporary_directory = None
    _test_appdata_root = None


@pytest.fixture(scope="session")
def default_test_appdata_root() -> Path:
    """Return the application-data root owned by this pytest process."""

    if _test_appdata_root is None:
        raise AssertionError("default-test application-data isolation is inactive")
    return _test_appdata_root


@pytest.fixture(scope="session")
def original_appdata_value() -> str | None:
    """Expose only the original path value so tests can prove it is not selected."""

    return _original_appdata
