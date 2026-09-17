"""Local image export for the active scientific presentation canvas."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPoint, QRect, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (
    QImage,
    QImageWriter,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


_RASTER_FORMATS = {
    ".png": b"png",
    ".jpg": b"jpeg",
    ".jpeg": b"jpeg",
}
_SUPPORTED_SUFFIXES = (*_RASTER_FORMATS, ".pdf")
_FORMAT_FILTERS = (
    "PNG image (*.png);;JPEG image — JPG (*.jpg);;"
    "JPEG image — JPEG (*.jpeg);;PDF document (*.pdf)"
)
_FORMAT_CHOICES = (
    ("PNG image (.png)", ".png"),
    ("JPEG image (.jpg)", ".jpg"),
    ("JPEG image (.jpeg)", ".jpeg"),
    ("PDF document (.pdf)", ".pdf"),
)
_BASE_DPI = 96


class ViewExportError(RuntimeError):
    """Raised when a requested local image cannot be rendered or written."""


@dataclass(frozen=True, slots=True)
class ViewExportRequest:
    """Validated destination and integer screen-resolution multiplier."""

    destination: Path
    scale_factor: int

    def __post_init__(self) -> None:
        if not self.destination.is_absolute():
            raise ValueError("view export destination must be absolute")
        if self.destination.suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ValueError("view export destination has an unsupported suffix")
        if not 1 <= self.scale_factor <= 8:
            raise ValueError("view export scale must be between 1 and 8")


class ViewExportDialog(QDialog):
    """Collect one explicit output path and a 1x through 8x pixel scale."""

    def __init__(
        self,
        base_size: QSize,
        default_basename: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if base_size.width() <= 0 or base_size.height() <= 0:
            raise ValueError("view export requires a positive base size")
        self._base_size = QSize(base_size)
        self._request: ViewExportRequest | None = None
        self.setObjectName("viewExportDialog")
        self.setWindowTitle("Export Current View")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        description = QLabel(
            "Exports the active molecular/Cube canvas or Transmission plot "
            "with its current camera and display settings. Interface controls "
            "are not included.",
            self,
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        destination_row = QWidget(self)
        destination_layout = QHBoxLayout(destination_row)
        destination_layout.setContentsMargins(0, 0, 0, 0)
        destination_layout.setSpacing(6)
        self._destination = QLineEdit(self)
        self._destination.setObjectName("viewExportDestination")
        self._destination.setText(f"{default_basename}.png")
        destination_layout.addWidget(self._destination, 1)
        self._browse = QPushButton("Browse…", destination_row)
        self._browse.setObjectName("viewExportBrowse")
        self._browse.clicked.connect(self._browse_destination)
        destination_layout.addWidget(self._browse)
        form.addRow("Output file:", destination_row)

        self._format = QComboBox(self)
        self._format.setObjectName("viewExportFormat")
        for label, suffix in _FORMAT_CHOICES:
            self._format.addItem(label, suffix)
        self._format.currentIndexChanged.connect(self._format_changed)
        form.addRow("Format:", self._format)

        self._scale = QComboBox(self)
        self._scale.setObjectName("viewExportScale")
        for scale in (1, 2, 4, 8):
            self._scale.addItem(f"{scale}x", scale)
        self._scale.currentIndexChanged.connect(self._update_summary)
        form.addRow("Resolution:", self._scale)
        layout.addLayout(form)

        self._summary = QLabel(self)
        self._summary.setObjectName("viewExportPixelSummary")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._error = QLabel(self)
        self._error.setObjectName("viewExportError")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.setObjectName("viewExportButtons")
        self._save = buttons.button(QDialogButtonBox.StandardButton.Save)
        self._save.setObjectName("viewExportSave")
        buttons.accepted.connect(self._accept_request)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_summary()

    @property
    def selected_request(self) -> ViewExportRequest:
        if self._request is None:
            raise RuntimeError("view export dialog has no accepted request")
        return self._request

    def _browse_destination(self) -> None:
        destination, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Current View",
            self._destination.text().strip(),
            _FORMAT_FILTERS,
        )
        if not destination:
            return
        self._destination.setText(destination)
        selected_suffix = Path(destination).suffix.lower()
        for index, (_label, suffix) in enumerate(_FORMAT_CHOICES):
            if suffix == selected_suffix:
                self._format.setCurrentIndex(index)
                break

    def _format_changed(self) -> None:
        text = self._destination.text().strip()
        if not text:
            return
        path = Path(text)
        if path.suffix.lower() in _SUPPORTED_SUFFIXES:
            self._destination.setText(
                str(path.with_suffix(str(self._format.currentData())))
            )

    def _update_summary(self) -> None:
        scale = int(self._scale.currentData())
        width = self._base_size.width() * scale
        height = self._base_size.height() * scale
        memory_mib = width * height * 4 / (1024 * 1024)
        summary = f"Output size: {width:,} × {height:,} pixels"
        if memory_mib >= 512.0:
            summary += f" (approximately {memory_mib:,.0f} MiB while rendering)"
        self._summary.setText(summary)

    def _accept_request(self) -> None:
        try:
            destination = self._normalized_destination()
        except ValueError as error:
            self._show_error(str(error))
            return
        if destination.exists() and not destination.is_file():
            self._show_error("The selected output path is not a file.")
            return
        if destination.exists():
            answer = QMessageBox.question(
                self,
                "Replace existing image?",
                f"{destination.name} already exists. Replace it?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._request = ViewExportRequest(
            destination,
            int(self._scale.currentData()),
        )
        self.accept()

    def _normalized_destination(self) -> Path:
        text = self._destination.text().strip()
        if not text:
            raise ValueError("Choose an output file.")
        destination = Path(text).expanduser()
        if not destination.suffix:
            destination = destination.with_suffix(
                str(self._format.currentData())
            )
        if destination.suffix.lower() not in _SUPPORTED_SUFFIXES:
            names = ", ".join(_SUPPORTED_SUFFIXES)
            raise ValueError(f"Output file must use one of: {names}.")
        destination = destination.resolve(strict=False)
        if not destination.parent.is_dir():
            raise ValueError("The selected output folder does not exist.")
        return destination

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()


def render_widget_image(
    widget: QWidget,
    *,
    base_size: QSize | None = None,
    scale_factor: int = 1,
) -> QImage:
    """Render a QWidget and its child overlays at an explicit pixel scale."""

    if not isinstance(widget, QWidget):
        raise TypeError("widget image export requires a QWidget")
    if not 1 <= scale_factor <= 8:
        raise ValueError("widget image scale must be between 1 and 8")
    requested_size = QSize(base_size or widget.size())
    if requested_size.width() <= 0 or requested_size.height() <= 0:
        raise ViewExportError("The active view has no renderable size.")

    original_minimum = widget.minimumSize()
    original_maximum = widget.maximumSize()
    original_size = widget.size()
    top_level = widget.window()
    preserve_top_level = not (
        top_level.isMaximized()
        or top_level.isMinimized()
        or top_level.isFullScreen()
    )
    top_level_geometry = QRect(top_level.geometry())
    try:
        if requested_size != original_size:
            widget.setMinimumSize(requested_size)
            widget.setMaximumSize(requested_size)
            widget.resize(requested_size)
        output_size = QSize(
            requested_size.width() * scale_factor,
            requested_size.height() * scale_factor,
        )
        image = QImage(output_size, QImage.Format.Format_ARGB32_Premultiplied)
        if image.isNull():
            raise ViewExportError("The requested image is too large to allocate.")
        image.fill(Qt.GlobalColor.transparent)
        dots_per_meter = round(_BASE_DPI * scale_factor / 0.0254)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        painter = QPainter(image)
        if not painter.isActive():
            raise ViewExportError("Unable to start the image renderer.")
        try:
            painter.scale(scale_factor, scale_factor)
            # Call QWidget's implementation explicitly because QGraphicsView
            # (the base of QChartView) shadows ``render`` with a scene-only
            # overload. The QWidget path also includes our child annotations.
            QWidget.render(widget, painter, QPoint())
        finally:
            painter.end()
        return image
    finally:
        widget.setMinimumSize(original_minimum)
        widget.setMaximumSize(original_maximum)
        if widget.size() != original_size:
            widget.resize(original_size)
        if preserve_top_level:
            top_level.setGeometry(top_level_geometry)


def save_view_image(image: QImage, destination: Path) -> None:
    """Write one rendered image using its explicit supported suffix."""

    if not isinstance(image, QImage) or image.isNull():
        raise ViewExportError("The rendered image is empty.")
    path = Path(destination)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        _write_pdf(image, path)
        return
    image_format = _RASTER_FORMATS.get(suffix)
    if image_format is None:
        raise ViewExportError(f"Unsupported image format: {suffix or '(none)'}")
    writer = QImageWriter(str(path), image_format)
    if image_format == b"jpeg":
        writer.setQuality(95)
    if not writer.write(image):
        raise ViewExportError(
            writer.errorString() or f"Unable to write image: {path}"
        )


def _write_pdf(image: QImage, destination: Path) -> None:
    dots_per_meter = max(1, image.dotsPerMeterX())
    dpi = max(1, round(dots_per_meter * 0.0254))
    width_mm = image.width() / dpi * 25.4
    height_mm = image.height() / dpi * 25.4
    page_size = QPageSize(
        QSizeF(width_mm, height_mm),
        QPageSize.Unit.Millimeter,
        "Exported view",
        QPageSize.SizeMatchPolicy.ExactMatch,
    )
    orientation = (
        QPageLayout.Orientation.Landscape
        if width_mm > height_mm
        else QPageLayout.Orientation.Portrait
    )
    writer = QPdfWriter(str(destination))
    writer.setResolution(dpi)
    writer.setPageLayout(
        QPageLayout(
            page_size,
            orientation,
            QMarginsF(0.0, 0.0, 0.0, 0.0),
            QPageLayout.Unit.Millimeter,
        )
    )
    painter = QPainter(writer)
    if not painter.isActive():
        raise ViewExportError("Unable to start the PDF renderer.")
    try:
        target = QRectF(writer.pageLayout().paintRectPixels(dpi))
        painter.drawImage(target, image)
    finally:
        painter.end()
    try:
        if not destination.is_file() or destination.stat().st_size <= 0:
            raise ViewExportError("The PDF writer produced no output.")
    except OSError as error:
        raise ViewExportError(f"Unable to verify PDF output: {error}") from error
