"""Persistent strict SSH host-key trust using a Paramiko known-hosts file."""

from dataclasses import dataclass
import base64
from hashlib import sha256
import os
from pathlib import Path

import paramiko


class KnownHostStoreError(RuntimeError):
    """Raised when the application-owned known-host store is unusable."""


@dataclass(frozen=True, slots=True)
class HostKeyInfo:
    hostname: str
    port: int
    algorithm: str
    sha256_fingerprint: str
    public_key_base64: str


class UnknownHostKey(RuntimeError):
    """Structured first-contact condition requiring explicit user trust."""

    def __init__(self, info: HostKeyInfo) -> None:
        self.info = info
        super().__init__(
            f"Unknown SSH host key for {info.hostname}:{info.port} "
            f"({info.algorithm}, {info.sha256_fingerprint})"
        )


class HostKeyMismatch(RuntimeError):
    """Hard failure for a known identity presenting a different key."""

    def __init__(
        self,
        hostname: str,
        port: int,
        stored_fingerprint: str,
        received_fingerprint: str,
    ) -> None:
        self.hostname = hostname
        self.port = port
        self.stored_fingerprint = stored_fingerprint
        self.received_fingerprint = received_fingerprint
        super().__init__(
            f"SSH host key mismatch for {hostname}:{port}; stored "
            f"{stored_fingerprint}, received {received_fingerprint}"
        )


class KnownHostStore:
    """Verify and explicitly persist exact host keys without auto-add policy."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def verify(
        self,
        hostname: str,
        port: int,
        key: paramiko.PKey,
    ) -> None:
        identity = host_key_identity(hostname, port)
        host_keys = self._load()
        known = host_keys.lookup(identity)
        received = host_key_info(hostname, port, key)
        if known is None:
            raise UnknownHostKey(received)
        matching_algorithm = known.get(key.get_name())
        if matching_algorithm is not None and (
            matching_algorithm.asbytes() == key.asbytes()
        ):
            return
        stored_key = matching_algorithm or known[sorted(known)[0]]
        raise HostKeyMismatch(
            hostname,
            port,
            _fingerprint(stored_key),
            received.sha256_fingerprint,
        )

    def trust(self, info: HostKeyInfo) -> None:
        """Persist an unknown exact key; never replace a mismatched known key."""

        key = _key_from_info(info)
        try:
            self.verify(info.hostname, info.port, key)
            return
        except UnknownHostKey:
            pass
        identity = host_key_identity(info.hostname, info.port)
        host_keys = self._load()
        host_keys.add(identity, info.algorithm, key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            host_keys.save(str(temporary))
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise KnownHostStoreError(
                "the trusted SSH host key could not be persisted"
            ) from None

    def _load(self) -> paramiko.HostKeys:
        host_keys = paramiko.HostKeys()
        if not self.path.exists():
            return host_keys
        try:
            host_keys.load(str(self.path))
        except (OSError, ValueError, paramiko.SSHException):
            raise KnownHostStoreError(
                "the application known-hosts file is unreadable or malformed"
            ) from None
        return host_keys


def host_key_identity(hostname: str, port: int) -> str:
    """Use OpenSSH/Paramiko naming and isolate nondefault ports."""

    return hostname if port == 22 else f"[{hostname}]:{port}"


def host_key_info(
    hostname: str,
    port: int,
    key: paramiko.PKey,
) -> HostKeyInfo:
    return HostKeyInfo(
        hostname=hostname,
        port=port,
        algorithm=key.get_name(),
        sha256_fingerprint=_fingerprint(key),
        public_key_base64=key.get_base64(),
    )


def _fingerprint(key: paramiko.PKey) -> str:
    encoded = base64.b64encode(sha256(key.asbytes()).digest()).decode("ascii")
    return "SHA256:" + encoded.rstrip("=")


def _key_from_info(info: HostKeyInfo) -> paramiko.PKey:
    try:
        key_bytes = base64.b64decode(info.public_key_base64, validate=True)
        key = paramiko.PKey.from_type_string(info.algorithm, key_bytes)
    except (ValueError, TypeError, paramiko.SSHException):
        raise KnownHostStoreError("trusted SSH host-key material is invalid") from None
    if host_key_info(info.hostname, info.port, key) != info:
        raise KnownHostStoreError(
            "trusted SSH host-key metadata does not match its public key"
        )
    return key
