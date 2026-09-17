"""Per-user application-data paths for non-secret persistent state."""

import os
from pathlib import Path
import shutil


class ApplicationDataPathError(RuntimeError):
    """Raised when a safe per-user application-data root is unavailable."""


_APPLICATION_DIRECTORY_NAME = "Moltage"
_LEGACY_APPLICATION_DIRECTORY_NAME = "AIMS-Transport"
_MIGRATED_FILENAMES = (
    "server_profiles.json",
    "known_hosts",
    "known_projects.json",
    "view_preferences.json",
    "submission_lifecycle.log",
    "submission_lifecycle.log.1",
    "submission_lifecycle.log.2",
)


def _application_data_base() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ApplicationDataPathError(
            "APPDATA is unavailable; a per-user Moltage data path "
            "cannot be established"
        )
    base = Path(appdata).expanduser().resolve()
    if base.anchor == str(base):
        raise ApplicationDataPathError(
            "refusing to use a filesystem root as application data storage"
        )
    return base


def application_data_directory() -> Path:
    """Return the Windows roaming application-data directory for this app."""

    return _application_data_base() / _APPLICATION_DIRECTORY_NAME


def density_results_path() -> Path:
    """Return the per-user cache root for density workflow results."""

    return application_data_directory() / "density_results"


def migrate_legacy_application_data() -> tuple[str, ...]:
    """Copy missing legacy state files into Moltage's per-user directory.

    Existing Moltage files are authoritative and are never overwritten.  The
    legacy directory remains untouched so an interrupted migration cannot make
    an older installation unusable.
    """

    base = _application_data_base()
    legacy = base / _LEGACY_APPLICATION_DIRECTORY_NAME
    destination = base / _APPLICATION_DIRECTORY_NAME
    if not legacy.exists():
        return ()
    if legacy.is_symlink() or not legacy.is_dir():
        raise ApplicationDataPathError(
            "legacy application data exists but is not a safe directory"
        )
    destination.mkdir(parents=True, exist_ok=True)
    migrated: list[str] = []
    for filename in _MIGRATED_FILENAMES:
        source = legacy / filename
        target = destination / filename
        if not source.exists() or target.exists():
            continue
        if source.is_symlink() or not source.is_file():
            raise ApplicationDataPathError(
                f"legacy application data entry is not a safe file: {filename}"
            )
        try:
            with source.open("rb") as source_handle, target.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
        except FileExistsError:
            continue
        except OSError as error:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise ApplicationDataPathError(
                f"could not migrate legacy application data file: {filename}"
            ) from error
        migrated.append(filename)
    return tuple(migrated)


def server_profiles_path() -> Path:
    return application_data_directory() / "server_profiles.json"


def known_hosts_path() -> Path:
    return application_data_directory() / "known_hosts"


def local_project_index_path() -> Path:
    return application_data_directory() / "known_projects.json"


def user_view_preferences_path() -> Path:
    return application_data_directory() / "view_preferences.json"


def submission_lifecycle_log_path() -> Path:
    return application_data_directory() / "submission_lifecycle.log"
