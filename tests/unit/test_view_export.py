import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel

from moltage.gui.view_export import (
    ViewExportDialog,
    ViewExportRequest,
    render_widget_image,
    save_view_image,
)


class ViewExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_widget_render_uses_requested_screen_multiplier(self):
        label = QLabel("Visible scientific canvas")
        label.setStyleSheet("background: #204060; color: #ffffff;")
        label.resize(320, 180)
        label.show()
        self.application.processEvents()

        image = render_widget_image(
            label,
            base_size=QSize(400, 240),
            scale_factor=3,
        )

        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (1200, 720))
        self.assertEqual((label.width(), label.height()), (320, 180))
        self.assertNotEqual(image.pixelColor(10, 10), QColor("#000000"))
        label.close()

    def test_png_jpeg_and_pdf_are_written(self):
        image = QImage(240, 160, QImage.Format.Format_RGB32)
        image.fill(QColor("#2d77aa"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in (".png", ".jpg", ".jpeg", ".pdf"):
                with self.subTest(suffix=suffix):
                    destination = root / f"view{suffix}"
                    save_view_image(image, destination)
                    self.assertTrue(destination.is_file())
                    self.assertGreater(destination.stat().st_size, 100)
                    if suffix != ".pdf":
                        loaded = QImage(str(destination))
                        self.assertEqual(
                            (loaded.width(), loaded.height()),
                            (240, 160),
                        )

    def test_dialog_exposes_named_formats_and_discrete_scale_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = ViewExportDialog(QSize(640, 480), "molecule_view")
            try:
                self.assertEqual(
                    tuple(
                        dialog._format.itemData(index)
                        for index in range(dialog._format.count())
                    ),
                    (".png", ".jpg", ".jpeg", ".pdf"),
                )
                self.assertEqual(
                    tuple(
                        (
                            dialog._scale.itemText(index),
                            dialog._scale.itemData(index),
                        )
                        for index in range(dialog._scale.count())
                    ),
                    (("1x", 1), ("2x", 2), ("4x", 4), ("8x", 8)),
                )
                self.assertEqual(dialog._scale.currentData(), 1)
                dialog._scale.setCurrentIndex(dialog._scale.findData(8))
                self.assertIn("5,120 × 3,840", dialog._summary.text())

                destination = Path(directory) / "synthetic-view.png"
                dialog._destination.setText(str(destination))
                dialog._accept_request()

                self.assertEqual(dialog.selected_request.destination, destination)
                self.assertEqual(dialog.selected_request.scale_factor, 8)
            finally:
                dialog.deleteLater()

    def test_request_rejects_relative_unsupported_and_overscale_paths(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            ViewExportRequest(Path("view.png"), 1)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            ViewExportRequest(Path.cwd() / "view.svg", 1)
        with self.assertRaisesRegex(ValueError, "between 1 and 8"):
            ViewExportRequest(Path.cwd() / "view.png", 9)


if __name__ == "__main__":
    unittest.main()
