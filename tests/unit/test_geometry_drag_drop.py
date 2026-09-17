import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, Qt, QUrl
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from moltage.domain.structure import Atom, MolecularStructure
from tools.molecule_viewer_demo import MoleculeViewerDemo, load_geometry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "ui_r5" / "double.mol"


class _FakeDropEvent:
    def __init__(self, mime_data: QMimeData) -> None:
        self._mime_data = mime_data
        self.drop_action = None
        self.accepted = False
        self.ignored = False

    def mimeData(self) -> QMimeData:
        return self._mime_data

    def setDropAction(self, action) -> None:
        self.drop_action = action

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


def _url_mime_data(*entries: str | Path) -> QMimeData:
    mime_data = QMimeData()
    urls = []
    for entry in entries:
        if isinstance(entry, Path):
            urls.append(QUrl.fromLocalFile(str(entry)))
        else:
            urls.append(QUrl(entry))
    mime_data.setUrls(urls)
    return mime_data


class GeometryDragDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.window = MoleculeViewerDemo()
        self.window.resize(900, 650)
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def _write_xyz(self, name: str = "molecule.xyz") -> Path:
        path = self.root / name
        path.write_text(
            "2\nDropped structure\nC 0.0 0.0 0.0\nO 1.2 0.0 0.0\n",
            encoding="utf-8",
        )
        return path

    def _write_aims_pair(self) -> tuple[Path, Path]:
        control = self.root / "control.in"
        geometry = self.root / "geometry.in"
        next_step = self.root / "geometry.in.next_step"
        control.write_text(
            "species C_alias\n nucleus 6\n"
            "species N_alias\n nucleus 7\n",
            encoding="utf-8",
        )
        geometry.write_text(
            "atom 0 0 0 C_alias\natom 1.2 0 0 N_alias\n",
            encoding="utf-8",
        )
        next_step.write_text(
            "atom 0.3 0 0 C_alias\natom 1.5 0 0 N_alias\n",
            encoding="utf-8",
        )
        return geometry, next_step

    def test_main_window_accepts_url_drags_as_copy_and_ignores_plain_text(self) -> None:
        self.assertTrue(self.window.acceptDrops())
        url_event = _FakeDropEvent(_url_mime_data(self._write_xyz()))

        self.window.dragEnterEvent(url_event)

        self.assertTrue(url_event.accepted)
        self.assertFalse(url_event.ignored)
        self.assertIs(url_event.drop_action, Qt.DropAction.CopyAction)

        text_mime = QMimeData()
        text_mime.setText("not a file")
        text_event = _FakeDropEvent(text_mime)

        self.window.dragEnterEvent(text_event)

        self.assertFalse(text_event.accepted)
        self.assertTrue(text_event.ignored)

    def test_classification_is_case_insensitive_and_rejects_non_files_and_urls(self) -> None:
        xyz_path = self._write_xyz("structure.XYZ")
        mol_path = self.root / "structure.MOL"
        mol_path.write_bytes(MOL_FIXTURE.read_bytes())
        geometry, next_step = self._write_aims_pair()
        unsupported = self.root / "structure.sdf"
        unsupported.write_text("not inspected", encoding="utf-8")
        directory = self.root / "folder.xyz"
        directory.mkdir()
        mime_data = _url_mime_data(
            xyz_path,
            mol_path,
            geometry,
            next_step,
            unsupported,
            directory,
            "https://example.invalid/remote.xyz",
        )

        accepted, rejected = self.window._classify_geometry_drop(mime_data)

        self.assertEqual(accepted, (xyz_path, mol_path, geometry, next_step))
        self.assertEqual(len(rejected), 3)
        self.assertIn("unsupported file type", rejected[0])
        self.assertIn("not a regular file", rejected[1])
        self.assertIn("not a local file", rejected[2])

    def test_file_chooser_manually_loads_input_and_next_step_through_recovery(self) -> None:
        geometry, next_step = self._write_aims_pair()

        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(geometry), "IN (*.in)"),
        ):
            self.window._new_geometry_action.trigger()
            self.application.processEvents()

        self.assertEqual(self.window._source_path, geometry)
        self.assertEqual(tuple(atom.element for atom in self.window._structure), ("C", "N"))
        self.assertEqual(self.window._structure[0].x, 0.0)

        geometry.unlink()
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(next_step), "NEXT STEP (*.next_step)"),
        ):
            self.window._new_geometry_action.trigger()
            self.application.processEvents()

        self.assertEqual(self.window._source_path, next_step)
        self.assertEqual(self.window._structure[0].x, 0.3)
        self.assertEqual(self.window._workspace_tabs.count(), 2)

    def test_drop_geometry_in_with_element_species_needs_no_control_file(self) -> None:
        geometry = self.root / "geometry.in"
        geometry.write_text(
            "atom 0 0 0 C\natom 1.2 0 0 N\n",
            encoding="utf-8",
        )

        with patch.object(QMessageBox, "critical") as critical:
            self.window.dropEvent(_FakeDropEvent(_url_mime_data(geometry)))
            self.application.processEvents()

        critical.assert_not_called()
        self.assertEqual(self.window._source_path, geometry)
        self.assertEqual(tuple(atom.element for atom in self.window._structure), ("C", "N"))

    def test_multiple_supported_files_open_in_drop_order_through_existing_loader(self) -> None:
        xyz_path = self._write_xyz("路径 with space.XYZ")
        mol_path = self.root / "explicit.MOL"
        mol_path.write_bytes(MOL_FIXTURE.read_bytes())
        event = _FakeDropEvent(_url_mime_data(xyz_path, mol_path))

        self.window.dropEvent(event)
        self.application.processEvents()

        self.assertTrue(event.accepted)
        self.assertIs(event.drop_action, Qt.DropAction.CopyAction)
        self.assertEqual(self.window._workspace_tabs.count(), 2)
        self.assertEqual(self.window._source_path, mol_path)
        self.assertEqual(
            tuple(
                workspace.identity.canonical_source_path
                for workspace in self.window._geometry_workspaces_by_identity.values()
            ),
            (xyz_path.resolve(), mol_path.resolve()),
        )
        self.assertEqual(
            tuple(item.order for item in self.window._bond_display_orders),
            (2,),
        )

    def test_duplicate_drop_focuses_existing_workspace_without_reread_or_edit_loss(self) -> None:
        path = self._write_xyz()
        with patch(
            "tools.molecule_viewer_demo.load_geometry",
            wraps=load_geometry,
        ) as loader:
            self.window.dropEvent(_FakeDropEvent(_url_mime_data(path)))
            workspace = self.window._active_geometry_workspace()
            edited = MolecularStructure(
                tuple(
                    Atom(atom.index, atom.element, atom.x, atom.y, atom.z + 0.4)
                    for atom in workspace.structure
                ),
                comment=workspace.structure.comment,
            )
            self.window._commit_working_structure(edited)

            self.window.dropEvent(
                _FakeDropEvent(
                    _url_mime_data(path.parent / "." / path.name)
                )
            )

        self.assertEqual(loader.call_count, 1)
        self.assertIs(self.window._active_geometry_workspace(), workspace)
        self.assertIs(workspace.structure, edited)
        self.assertIs(self.window._structure, edited)
        self.assertEqual(self.window._workspace_tabs.count(), 1)

    def test_mixed_drop_opens_supported_files_and_reports_rejections_once(self) -> None:
        valid = self._write_xyz()
        unsupported = self.root / "structure.pdb"
        unsupported.write_text("not inspected", encoding="utf-8")
        event = _FakeDropEvent(_url_mime_data(valid, unsupported))

        with patch.object(
            self.window,
            "_open_local_geometry",
        ) as opener, patch.object(QMessageBox, "warning") as warning:
            self.window.dropEvent(event)

        opener.assert_called_once_with(valid)
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "Some files were not opened")
        self.assertIn(unsupported.name, warning.call_args.args[2])
        self.assertIn("unsupported file type", warning.call_args.args[2])

    def test_all_invalid_drop_changes_no_workspace_and_reports_explicitly(self) -> None:
        unsupported = self.root / "structure.pdb"
        unsupported.write_text("not inspected", encoding="utf-8")
        event = _FakeDropEvent(
            _url_mime_data(unsupported, "https://example.invalid/remote.mol")
        )

        with patch.object(
            self.window,
            "_open_local_geometry",
        ) as opener, patch.object(QMessageBox, "warning") as warning:
            self.window.dropEvent(event)

        opener.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "No structure files were opened")
        self.assertEqual(self.window._workspace_tabs.count(), 1)
        self.assertIsNone(self.window._source_path)

    def test_malformed_supported_drop_uses_existing_error_and_leaves_no_orphan_tab(self) -> None:
        first = self._write_xyz("valid.xyz")
        malformed = self.root / "malformed.xyz"
        malformed.write_text("not xyz\n", encoding="utf-8")
        self.window.dropEvent(_FakeDropEvent(_url_mime_data(first)))
        count_before = self.window._workspace_tabs.count()

        with patch.object(QMessageBox, "critical") as critical:
            self.window.dropEvent(_FakeDropEvent(_url_mime_data(malformed)))

        critical.assert_called_once()
        self.assertEqual(critical.call_args.args[1], "Unable to display molecule")
        self.assertEqual(self.window._workspace_tabs.count(), count_before)
        self.assertEqual(self.window._source_path, first)


if __name__ == "__main__":
    unittest.main()
