"""Secret-store boundary and native Windows Credential Manager adapter."""

from typing import Protocol
from uuid import UUID

import keyring
from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import KeyringError, PasswordDeleteError


_SERVICE_NAME = "Moltage"
_LEGACY_SERVICE_NAME = "AIMS-Transport"


class SecretStoreError(RuntimeError):
    """Raised when the native credential store cannot complete an operation."""


class SecretStore(Protocol):
    """Minimal password store keyed by stable server-profile UUID."""

    def get_password(self, profile_id: UUID) -> str | None: ...

    def set_password(self, profile_id: UUID, password: str) -> None: ...

    def delete_password(self, profile_id: UUID) -> None: ...


class WindowsCredentialSecretStore:
    """Store profile passwords only in native Windows Credential Manager."""

    def __init__(self) -> None:
        backend = keyring.get_keyring()
        if not isinstance(backend, WinVaultKeyring) or backend.priority <= 0:
            raise SecretStoreError(
                "keyring is not using the native Windows Credential Manager backend"
            )
        self._backend = backend

    def get_password(self, profile_id: UUID) -> str | None:
        key = _credential_key(profile_id)
        try:
            password = self._backend.get_password(_SERVICE_NAME, key)
            if password is not None:
                return password
            return self._backend.get_password(_LEGACY_SERVICE_NAME, key)
        except KeyringError:
            raise SecretStoreError(
                "Windows Credential Manager could not read the saved password"
            ) from None

    def set_password(self, profile_id: UUID, password: str) -> None:
        if not isinstance(password, str) or not password:
            raise SecretStoreError("a nonempty password is required for secure saving")
        key = _credential_key(profile_id)
        try:
            self._backend.set_password(_SERVICE_NAME, key, password)
        except KeyringError:
            raise SecretStoreError(
                "Windows Credential Manager could not save the password"
            ) from None

    def delete_password(self, profile_id: UUID) -> None:
        key = _credential_key(profile_id)
        for service_name in (_SERVICE_NAME, _LEGACY_SERVICE_NAME):
            try:
                self._backend.delete_password(service_name, key)
            except PasswordDeleteError:
                continue
            except KeyringError:
                raise SecretStoreError(
                    "Windows Credential Manager could not delete the password"
                ) from None


def _credential_key(profile_id: UUID) -> str:
    if not isinstance(profile_id, UUID):
        raise SecretStoreError("credential identity requires a profile UUID")
    return f"server-profile:{profile_id}"
