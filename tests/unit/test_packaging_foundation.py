import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from moltage.app.package_resources import (
    application_legal_document_path,
    application_resource_path,
    application_resources_directory,
)
from moltage.structure.covalent_radii import load_covalent_radii
from moltage.structure.vdw_radii import load_vdw_radii


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.molecule_viewer_demo import _viewer_icon


class PackageResourceTests(unittest.TestCase):
    def test_source_resources_are_independent_of_working_directory(self) -> None:
        expected = PROJECT_ROOT / "resources"

        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_directory = Path.cwd()
            try:
                os.chdir(temporary_directory)
                actual = application_resources_directory()
            finally:
                os.chdir(previous_directory)

        self.assertEqual(actual, expected)
        self.assertEqual(
            application_resource_path("chemistry", "covalent_radii.toml"),
            expected / "chemistry" / "covalent_radii.toml",
        )

    def test_frozen_resources_use_the_bundle_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(sys, "_MEIPASS", temporary_directory, create=True):
                actual = application_resource_path("icons", "undo.svg")

            self.assertEqual(
                actual,
                Path(temporary_directory).resolve() / "resources" / "icons" / "undo.svg",
            )

    def test_legal_documents_resolve_in_source_and_frozen_runtimes(self) -> None:
        self.assertEqual(
            application_legal_document_path("LICENSE"),
            PROJECT_ROOT / "LICENSE",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(sys, "_MEIPASS", temporary_directory, create=True):
                actual = application_legal_document_path(
                    "THIRD_PARTY_NOTICES.md"
                )
            self.assertEqual(
                actual,
                Path(temporary_directory).resolve()
                / "resources"
                / "legal"
                / "THIRD_PARTY_NOTICES.md",
            )
        for unsafe in ("", "../LICENSE", "legal/LICENSE"):
            with self.assertRaises(ValueError):
                application_legal_document_path(unsafe)

    def test_frozen_bundle_loads_both_radius_resources_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = PROJECT_ROOT / "resources" / "chemistry"
            target = Path(temporary_directory) / "resources" / "chemistry"
            target.mkdir(parents=True)
            shutil.copy2(source / "covalent_radii.toml", target)
            shutil.copy2(source / "vdw_radii.toml", target)

            with patch.object(sys, "_MEIPASS", temporary_directory, create=True):
                covalent = load_covalent_radii(
                    application_resource_path("chemistry", "covalent_radii.toml")
                )
                vdw = load_vdw_radii(
                    application_resource_path("chemistry", "vdw_radii.toml")
                )

            self.assertEqual(len(covalent), 83)
            self.assertEqual(len(vdw), 82)
            self.assertEqual(covalent["Au"], 1.24)
            self.assertEqual(vdw["Au"], 2.253766)


class GuiEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_entry_delegates_to_the_existing_main_once(self) -> None:
        with patch("tools.molecule_viewer_demo.main") as existing_main:
            runpy.run_path(
                str(PROJECT_ROOT / "moltage_gui.py"),
                run_name="__main__",
            )

        existing_main.assert_called_once_with()

    def test_existing_viewer_icon_loads_through_packaged_resources(self) -> None:
        self.assertFalse(_viewer_icon("undo.svg").isNull())

    def test_application_icon_loads_through_packaged_resources(self) -> None:
        icon_path = application_resource_path("icons", "moltage.ico")

        self.assertTrue(icon_path.is_file())
        self.assertFalse(_viewer_icon("moltage.ico").isNull())


if __name__ == "__main__":
    unittest.main()
