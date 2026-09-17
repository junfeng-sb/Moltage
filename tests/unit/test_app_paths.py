import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moltage.app.paths import (
    ApplicationDataPathError,
    application_data_directory,
    density_results_path,
    migrate_legacy_application_data,
)


class ApplicationDataPathTests(unittest.TestCase):
    def test_density_results_path_is_per_user_and_independent_of_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            appdata = base / "appdata"
            working_directory = base / "working"
            working_directory.mkdir()
            original_directory = Path.cwd()

            try:
                os.chdir(working_directory)
                with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                    resolved = density_results_path()
            finally:
                os.chdir(original_directory)

            self.assertEqual(
                resolved,
                appdata.resolve() / "Moltage" / "density_results",
            )
            self.assertEqual(tuple(working_directory.iterdir()), ())

    def test_density_results_path_fails_without_appdata(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ApplicationDataPathError, "APPDATA"):
                density_results_path()

    def test_legacy_files_are_copied_without_overwrite_or_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            legacy = base / "AIMS-Transport"
            current = base / "Moltage"
            legacy.mkdir()
            current.mkdir()
            (legacy / "server_profiles.json").write_text(
                "legacy profiles",
                encoding="utf-8",
            )
            (legacy / "known_projects.json").write_text(
                "legacy projects",
                encoding="utf-8",
            )
            (current / "server_profiles.json").write_text(
                "current profiles",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"APPDATA": directory}):
                migrated = migrate_legacy_application_data()
                resolved = application_data_directory()

            self.assertEqual(resolved, current.resolve())
            self.assertEqual(migrated, ("known_projects.json",))
            self.assertEqual(
                (current / "server_profiles.json").read_text(encoding="utf-8"),
                "current profiles",
            )
            self.assertEqual(
                (current / "known_projects.json").read_text(encoding="utf-8"),
                "legacy projects",
            )
            self.assertEqual(
                (legacy / "known_projects.json").read_text(encoding="utf-8"),
                "legacy projects",
            )


if __name__ == "__main__":
    unittest.main()
