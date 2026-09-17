from pathlib import Path
import tomllib
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGING_ROOT = PROJECT_ROOT / "packaging"


class WindowsPackagingTests(unittest.TestCase):
    def test_distribution_version_is_consistent(self) -> None:
        expected_version = "0.2.1"
        metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))
        self.assertEqual(metadata["project"]["version"], expected_version)

        version_info = (PACKAGING_ROOT / "version_info.txt").read_text("utf-8")
        installer = (PACKAGING_ROOT / "Moltage.iss").read_text("utf-8")
        build_script = (PACKAGING_ROOT / "build_windows.ps1").read_text("utf-8")
        bundled_log = (PROJECT_ROOT / "resources" / "update_log.txt").read_text(
            "utf-8"
        )
        changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text("utf-8")
        self.assertIn(
            f"StringStruct('FileVersion', '{expected_version}')",
            version_info,
        )
        self.assertIn(
            f"StringStruct('ProductVersion', '{expected_version}')",
            version_info,
        )
        numeric_version = tuple(int(part) for part in expected_version.split("."))
        self.assertIn(
            f"filevers={numeric_version + (0,)},",
            version_info,
        )
        self.assertIn(
            f"prodvers={numeric_version + (0,)},",
            version_info,
        )
        self.assertIn(f'#define MyAppVersion "{expected_version}"', installer)
        self.assertIn(f"VersionInfoVersion={expected_version}.0", installer)
        self.assertIn(f'Moltage-Setup-{expected_version}.exe"', build_script)
        self.assertIn(f"0.1.13 to {expected_version}", bundled_log)
        self.assertIn(f"## 0.1.13 to {expected_version}", changelog)

    def test_build_dependencies_are_pinned(self) -> None:
        requirements = set(
            (PACKAGING_ROOT / "requirements-build.txt")
            .read_text("utf-8")
            .splitlines()
        )
        self.assertEqual(
            requirements,
            {
                "pyinstaller==6.22.2",
                "PySide6==6.11.2",
                "vtk==9.7.0",
                "paramiko==5.0.0",
                "keyring==25.7.0",
            },
        )

    def test_pyinstaller_is_windowed_onedir_with_only_immutable_resources(self) -> None:
        spec = (PACKAGING_ROOT / "Moltage.spec").read_text("utf-8")
        compact = "".join(spec.split())
        self.assertIn('name="Moltage"', compact)
        self.assertIn('console=False', compact)
        self.assertIn(
            '(str(PROJECT_ROOT/"resources"),"resources")',
            compact,
        )
        self.assertIn(
            'icon=str(PROJECT_ROOT/"resources"/"icons"/"moltage.ico")',
            compact,
        )
        self.assertIn("COLLECT(", spec)
        self.assertIn('str(PROJECT_ROOT / "LICENSE")', spec)
        self.assertIn('str(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md")', spec)
        self.assertIn('str(PROJECT_ROOT / "LICENSES")', spec)
        for excluded_module in (
            "PySide6.QtPdf",
            "PySide6.QtQml",
            "PySide6.QtQuick",
            "PySide6.QtVirtualKeyboard",
        ):
            self.assertIn(f'"{excluded_module}"', spec)
        for excluded_binary in (
            "PySide6/Qt6Pdf.dll",
            "PySide6/Qt6Qml.dll",
            "PySide6/Qt6Quick.dll",
            "PySide6/Qt6VirtualKeyboard.dll",
            "PySide6/plugins/imageformats/qpdf.dll",
            "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
        ):
            self.assertIn(f'"{excluded_binary}"', spec)
        self.assertIn("filtered_binaries", spec)

        for forbidden in (
            "server_profiles.json",
            "known_projects.json",
            "known_hosts",
            "submission_lifecycle.log",
            "config/server.yaml",
        ):
            self.assertNotIn(forbidden, spec)

    def test_distribution_contains_no_fhi_aims_species_definitions(self) -> None:
        species_root = PROJECT_ROOT / "resources" / "fhi_aims" / "species_defaults"

        self.assertFalse(species_root.exists())
        self.assertEqual(
            tuple((PROJECT_ROOT / "resources").rglob("*_default")),
            (),
        )

    def test_installer_is_administrative_selectable_and_does_not_manage_user_data(self) -> None:
        installer = (PACKAGING_ROOT / "Moltage.iss").read_text("utf-8")
        self.assertIn("DefaultDirName={autopf}\\{#MyAppName}", installer)
        self.assertIn("PrivilegesRequired=admin", installer)
        self.assertIn("DisableDirPage=no", installer)
        self.assertNotIn("PrivilegesRequiredOverridesAllowed", installer)
        self.assertIn(
            "AppId={{9C0D6515-2845-4213-9027-070FDED3EDA4}", installer
        )
        self.assertIn("ArchitecturesAllowed=x64compatible", installer)
        self.assertIn("{autoprograms}\\{#MyAppName}", installer)
        self.assertIn("UsePreviousAppDir=yes", installer)
        self.assertIn("UsePreviousGroup=yes", installer)
        self.assertNotIn("{userappdata}", installer)
        self.assertNotIn("[Registry]", installer)
        self.assertIn("LicenseFile=..\\LICENSE", installer)
        self.assertIn("InfoBeforeFile=..\\THIRD_PARTY_NOTICES.md", installer)
        self.assertIn('DestName: "LICENSE.txt"', installer)
        self.assertIn('DestDir: "{app}\\LICENSES"', installer)

        uninstall_delete = installer.split("[UninstallDelete]", 1)[1].split(
            "[Code]", 1
        )[0]
        self.assertIn(
            'Type: files; Name: "{autodesktop}\\{#MyAppName}.lnk"',
            uninstall_delete,
        )
        self.assertNotIn("Moltage\\", uninstall_delete)

    def test_finished_page_offers_default_checked_desktop_shortcut(self) -> None:
        installer = (PACKAGING_ROOT / "Moltage.iss").read_text("utf-8")

        self.assertIn("Parent := WizardForm.FinishedPage", installer)
        self.assertIn("Caption := 'Create a desktop shortcut'", installer)
        self.assertIn("DesktopShortcutCheckBox.Checked := True", installer)
        self.assertIn("if CurPageID <> wpFinished then", installer)
        self.assertIn("CreateShellLink(", installer)
        self.assertIn("{autodesktop}\\{#MyAppName}.lnk", installer)

    def test_installer_and_application_use_the_supplied_icon(self) -> None:
        installer = (PACKAGING_ROOT / "Moltage.iss").read_text("utf-8")
        source_png = PACKAGING_ROOT / "assets" / "moltage.png"
        application_icon = PROJECT_ROOT / "resources" / "icons" / "moltage.ico"

        self.assertTrue(source_png.is_file())
        self.assertTrue(application_icon.is_file())
        self.assertIn("SetupIconFile={#MyAppIcon}", installer)

    def test_build_script_uses_isolated_tools_and_hashes_the_installer(self) -> None:
        script = (PACKAGING_ROOT / "build_windows.ps1").read_text("utf-8")
        self.assertIn('.venv-packaging\\Scripts\\pyinstaller.exe', script)
        self.assertIn("Moltage.spec", script)
        self.assertIn("Moltage.iss", script)
        self.assertIn("$OriginalBuildPath = $env:PATH", script)
        self.assertIn('(Split-Path -Parent $PyInstallerPath)', script)
        self.assertIn('(Join-Path $env:SystemRoot "System32")', script)
        self.assertIn("} finally {\n    $env:PATH = $OriginalBuildPath", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("Refusing to remove a build target outside", script)
        self.assertIn("Refusing to overwrite an existing release artifact", script)
        self.assertNotIn(
            "Remove-OwnedBuildDirectory -Path $InstallerDistRoot",
            script,
        )

    def test_release_legal_material_and_source_manifest_are_complete(self) -> None:
        license_text = (PROJECT_ROOT / "LICENSE").read_text("utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)
        notices = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text("utf-8")
        self.assertIn("Copyright (C) 2026 Junfeng Lin", notices)
        self.assertIn("Qt Charts", notices)
        self.assertIn("FHI-aims, AITRANSS, and ORCA are **not** included", notices)
        self.assertTrue((PROJECT_ROOT / "SECURITY.md").is_file())

        manifest = tomllib.loads(
            (PACKAGING_ROOT / "third_party_sources.toml").read_text("utf-8")
        )
        self.assertEqual(manifest["release"]["version"], "0.2.1")
        entries = manifest["source"]
        filenames = [entry["filename"] for entry in entries]
        self.assertEqual(len(filenames), len(set(filenames)))
        self.assertGreaterEqual(len(entries), 20)
        for entry in entries:
            self.assertEqual(Path(entry["filename"]).name, entry["filename"])
            self.assertEqual(len(entry["sha256"]), 64)
            int(entry["sha256"], 16)
            self.assertTrue(entry["url"].startswith("https://"))

    def test_full_build_lock_matches_the_release_environment(self) -> None:
        lock = set(
            (PACKAGING_ROOT / "requirements-build-lock.txt")
            .read_text("utf-8")
            .splitlines()
        )
        for required in (
            "pyinstaller==6.22.2",
            "PySide6==6.11.2",
            "vtk==9.7.0",
            "numpy==2.5.3",
            "paramiko==5.0.0",
            "cryptography==50.0.1",
            "matplotlib==3.11.2",
            "pillow==12.3.0",
        ):
            self.assertIn(required, lock)


if __name__ == "__main__":
    unittest.main()
