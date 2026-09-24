"""Shared Qt Charts presentation for validated transmission curves.

This module owns the reviewed chart presentation itself: axes, the explicit
tick model, custom logarithmic labels, curve probing, and the five-page view
settings. It knows nothing about FHI-aims/AITRANSS or ORCA result types; each
workflow view supplies already validated curves and its own explanatory text.
"""

from bisect import bisect_left
from dataclasses import dataclass, replace
from html import escape
from math import ceil, floor, isfinite, log10

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QLineSeries,
    QLogValueAxis,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import (
    QEvent,
    QLineF,
    QMargins,
    QPointF,
    QRectF,
    QSize,
    Signal,
    Slot,
    Qt,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QImage,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from moltage.gui.transmission_settings import (
    AxisVisualSettings,
    CanvasVisualSettings,
    CurveVisualSettings,
    PlotLineStyle,
    TickDirection,
    TickVisualSettings,
    TransmissionSettingsDialog,
    TransmissionVisualSettings,
)
from moltage.gui.log_axis import (
    SUPERSCRIPT_TRANSLATION as _SUPERSCRIPT_TRANSLATION,
    log_tick_exponents as _log_tick_exponents,
    power_of_ten_label as _power_of_ten_label,
)
from moltage.gui.view_export import render_widget_image


_CHART_FONT_FAMILY = "Arial"
_CURVE_HIT_RADIUS_PX = 12.0
_AXIS_LABEL_POINT_SIZE = 11.0
_AXIS_TITLE_POINT_SIZE = 12.0
_ANNOTATION_POINT_SIZE = 11.0
_PROBE_READOUT_POINT_SIZE = 10.0
class _VerticalAxisLabel(QWidget):
    """Draw one controlled vertical title outside custom Y tick labels."""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._text = text
        self._color = QColor("#000000")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        return QSize(metrics.height() + 4, metrics.horizontalAdvance(self._text) + 8)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(self._color)
        painter.translate(0.0, float(self.height()))
        painter.rotate(-90.0)
        painter.drawText(
            QRectF(0.0, 0.0, float(self.height()), float(self.width())),
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )


class AxisRangeDialog(QDialog):
    """Edit one finite axis range without adding general chart controls."""

    def __init__(
        self,
        *,
        title: str,
        minimum_label: str,
        maximum_label: str,
        current_range: tuple[float, float],
        default_range: tuple[float, float],
        require_positive_minimum: bool,
        object_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFont(chart_font(self.font()))
        self._require_positive_minimum = require_positive_minimum
        self._default_range = default_range
        self._selected_range = current_range
        self.setObjectName(object_name)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._minimum = QLineEdit(_format_axis_value(current_range[0]), self)
        self._minimum.setObjectName("axisRangeMinimum")
        self._maximum = QLineEdit(_format_axis_value(current_range[1]), self)
        self._maximum.setObjectName("axisRangeMaximum")
        form.addRow(minimum_label, self._minimum)
        form.addRow(maximum_label, self._maximum)
        layout.addLayout(form)

        self._error = QLabel("", self)
        self._error.setObjectName("axisRangeError")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(self)
        buttons.setObjectName("axisRangeButtons")
        self._apply_button = buttons.addButton(
            "Apply",
            QDialogButtonBox.ButtonRole.ApplyRole,
        )
        self._apply_button.setObjectName("axisRangeApply")
        self._apply_button.setDefault(True)
        self._reset_button = buttons.addButton(
            "Reset Default",
            QDialogButtonBox.ButtonRole.ResetRole,
        )
        self._reset_button.setObjectName("axisRangeResetDefault")
        self._cancel_button = buttons.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        self._cancel_button.setObjectName("axisRangeCancel")
        self._apply_button.clicked.connect(self._apply)
        self._reset_button.clicked.connect(self._reset_default)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._minimum.setFocus()
        self._minimum.selectAll()

    @property
    def selected_range(self) -> tuple[float, float]:
        return self._selected_range

    @Slot()
    def _apply(self) -> None:
        parsed = self._parse_range()
        if parsed is None:
            return
        self._selected_range = parsed
        self.accept()

    @Slot()
    def _reset_default(self) -> None:
        self._minimum.setText(_format_axis_value(self._default_range[0]))
        self._maximum.setText(_format_axis_value(self._default_range[1]))
        self._selected_range = self._default_range
        self.accept()

    def _parse_range(self) -> tuple[float, float] | None:
        try:
            minimum = float(self._minimum.text().strip())
            maximum = float(self._maximum.text().strip())
        except ValueError:
            self._show_error("Minimum and maximum must be finite numbers.")
            return None
        if not isfinite(minimum) or not isfinite(maximum):
            self._show_error("Minimum and maximum must be finite numbers.")
            return None
        if self._require_positive_minimum and minimum <= 0.0:
            self._show_error(
                "Minimum must be greater than zero for the logarithmic axis."
            )
            return None
        if minimum >= maximum:
            self._show_error("Minimum must be less than maximum.")
            return None
        self._error.clear()
        self._error.hide()
        return minimum, maximum

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()


class TransmissionChartView(QChartView):
    """Provide deterministic curve probing and reviewed axis interaction."""

    axisDoubleClicked = Signal(str)
    curveDoubleClicked = Signal()
    canvasDoubleClicked = Signal()

    def __init__(
        self,
        chart: QChart,
        curves: tuple[QLineSeries, ...],
        fermi_marker: QScatterSeries,
        fermi_annotation: str | None,
        probe_marker: QScatterSeries,
        energy_axis: QValueAxis,
        transmission_axis: QLogValueAxis,
        energy_mirror_axis: QValueAxis,
        transmission_mirror_axis: QLogValueAxis,
        transmission_title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(chart, parent)
        self._curves = tuple(curves)
        if not self._curves:
            raise ValueError("transmission chart view requires one curve")
        self._fermi_marker = fermi_marker
        self._probe_marker = probe_marker
        self._energy_axis = energy_axis
        self._transmission_axis = transmission_axis
        self._energy_mirror_axis = energy_mirror_axis
        self._transmission_mirror_axis = transmission_mirror_axis
        self._probe_points = tuple(
            tuple(
                (index, series.at(index))
                for index in range(series.count())
                if series.at(index).y() > 0.0
            )
            for series in self._curves
        )
        self._probe_energies = tuple(
            tuple(point.x() for _, point in points)
            for points in self._probe_points
        )
        self._active_probe_index: tuple[int, int] | None = None
        self._pinned_probe_index: tuple[int, int] | None = None
        self._log_tick_labels: list[QLabel] = []
        self._right_log_tick_labels: list[QLabel] = []
        self._y_label_font = chart_font(
            self._transmission_axis.labelsFont(),
            point_size=_AXIS_LABEL_POINT_SIZE,
        )
        self._y_overlay_color = QColor("#000000")
        self._tick_settings: TickVisualSettings | None = None
        self._x_axis_line_color = QColor("#000000")
        self._y_axis_line_color = QColor("#000000")
        self._x_axis_line_width = 1.0
        self._y_axis_line_width = 1.0

        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._probe_readout = QLabel(self.viewport())
        self._probe_readout.setObjectName("transmissionProbeReadout")
        self._probe_readout.setFont(
            chart_font(
                self._probe_readout.font(),
                point_size=_PROBE_READOUT_POINT_SIZE,
            )
        )
        self._probe_readout.setTextFormat(Qt.TextFormat.RichText)
        self._probe_readout.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._probe_readout.setStyleSheet(
            "QLabel { background: rgba(255, 255, 255, 235); color: #111111; "
            "border: 1px solid #202020; padding: 3px 5px; }"
        )
        self._probe_readout.hide()

        self._fermi_readout = QLabel(self.viewport())
        self._fermi_readout.setObjectName("transmissionFermiReadout")
        self._fermi_readout.setFont(
            chart_font(
                self._fermi_readout.font(),
                point_size=_ANNOTATION_POINT_SIZE,
            )
        )
        self._fermi_readout.setTextFormat(Qt.TextFormat.RichText)
        self._fermi_readout.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._fermi_readout.setStyleSheet(
            "QLabel { background: transparent; color: #000000; padding: 0; }"
        )
        if fermi_annotation is None:
            self._fermi_readout.hide()
        else:
            self._fermi_readout.setText(fermi_annotation)
            self._fermi_readout.adjustSize()
            self._fermi_readout.show()

        self._transmission_title_label = _VerticalAxisLabel(
            transmission_title,
            self.viewport(),
        )
        self._transmission_title_label.setObjectName(
            "transmissionVerticalAxisTitle"
        )
        self._transmission_title_label.setFont(
            chart_font(
                self._transmission_axis.titleFont(),
                point_size=_AXIS_TITLE_POINT_SIZE,
            )
        )
        self._transmission_title_label.adjustSize()
        self._transmission_title_label.show()

        chart.plotAreaChanged.connect(self._plot_area_changed)
        energy_axis.rangeChanged.connect(self._axis_range_changed)
        transmission_axis.rangeChanged.connect(self._axis_range_changed)

    def axis_regions(self) -> tuple[QRectF, QRectF]:
        """Return X and Y label/title bands in viewport coordinates."""

        chart_bounds = self._chart_rect_in_view(self.chart().boundingRect())
        plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
        x_region = QRectF(
            plot_bounds.left(),
            plot_bounds.bottom(),
            plot_bounds.width(),
            max(0.0, chart_bounds.bottom() - plot_bounds.bottom()),
        )
        y_region = QRectF(
            chart_bounds.left(),
            plot_bounds.top(),
            max(0.0, plot_bounds.left() - chart_bounds.left()),
            plot_bounds.height(),
        )
        return x_region, y_region

    def axis_at(self, position: QPointF) -> str | None:
        """Identify an axis label/title band while excluding the plot interior."""

        x_region, y_region = self.axis_regions()
        if x_region.contains(position):
            return "x"
        if y_region.contains(position):
            return "y"
        chart_bounds = self._chart_rect_in_view(self.chart().boundingRect())
        plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
        if self._energy_mirror_axis.isVisible():
            top_region = QRectF(
                plot_bounds.left(),
                chart_bounds.top(),
                plot_bounds.width(),
                max(0.0, plot_bounds.top() - chart_bounds.top()),
            )
            if top_region.contains(position):
                return "x"
        if self._transmission_mirror_axis.isVisible():
            right_region = QRectF(
                plot_bounds.right(),
                plot_bounds.top(),
                max(0.0, chart_bounds.right() - plot_bounds.right()),
                plot_bounds.height(),
            )
            if right_region.contains(position):
                return "y"
        return None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        axis = self.axis_at(event.position())
        if axis is not None:
            event.accept()
            self.axisDoubleClicked.emit(axis)
            return
        if self._probe_index_at(event.position()) is not None:
            event.accept()
            self.curveDoubleClicked.emit()
            return
        event.accept()
        self.canvasDoubleClicked.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pinned_probe_index is None:
            probe_index = self._probe_index_at(event.position())
            if probe_index is None:
                self._clear_probe()
            else:
                self._show_probe(probe_index)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            probe_index = self._probe_index_at(event.position())
            if probe_index is not None:
                self._pinned_probe_index = probe_index
                self._show_probe(probe_index)
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
                return
            plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
            if plot_bounds.contains(event.position()):
                self._pinned_probe_index = None
                self._clear_probe()
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._pinned_probe_index is not None:
            if event.key() == Qt.Key.Key_Left:
                self._move_pinned_probe(-1)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Right:
                self._move_pinned_probe(1)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                self._pinned_probe_index = None
                self._clear_probe()
                event.accept()
                return
        super().keyPressEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self._pinned_probe_index is None:
            self._clear_probe()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_overlays()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._tick_settings is None:
            return
        painter = QPainter(self.viewport())
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_custom_axes_and_ticks(painter)
        finally:
            painter.end()

    @Slot(QRectF)
    def _plot_area_changed(self, _plot_area: QRectF) -> None:
        self.refresh_overlays()

    @Slot(float, float)
    def _axis_range_changed(self, _minimum: float, _maximum: float) -> None:
        self.refresh_overlays()

    def refresh_overlays(self) -> None:
        self._update_log_tick_labels()
        self._position_transmission_title()
        self._position_fermi_readout()
        if self._active_probe_index is not None:
            self._position_probe_readout()

    def set_y_overlay_style(
        self,
        *,
        title: str,
        font_family: str,
        label_point_size: float,
        title_point_size: float,
        color: QColor,
    ) -> None:
        """Apply typography shared by custom left/right log-axis overlays."""

        self._transmission_title_label.set_text(title)
        title_font = QFont(font_family)
        title_font.setPointSizeF(title_point_size)
        self._transmission_title_label.setFont(title_font)
        self._transmission_title_label.set_color(color)
        label_font = QFont(font_family)
        label_font.setPointSizeF(label_point_size)
        self._y_label_font = label_font
        self._y_overlay_color = QColor(color)
        style = (
            "QLabel { background: transparent; "
            f"color: {color.name(QColor.NameFormat.HexRgb)}; }}"
        )
        for label in (*self._log_tick_labels, *self._right_log_tick_labels):
            label.setFont(label_font)
            label.setStyleSheet(style)
        self.refresh_overlays()

    def set_tick_style(
        self,
        settings: TickVisualSettings,
        *,
        x_axis_color: QColor,
        y_axis_color: QColor,
        x_axis_width: float,
        y_axis_width: float,
    ) -> None:
        """Apply one explicit custom tick model to the linear/log axes."""

        if not isinstance(settings, TickVisualSettings):
            raise TypeError("Transmission tick settings are invalid")
        self._tick_settings = settings
        self._x_axis_line_color = QColor(x_axis_color)
        self._y_axis_line_color = QColor(y_axis_color)
        self._x_axis_line_width = float(x_axis_width)
        self._y_axis_line_width = float(y_axis_width)
        self.viewport().update()

    def tick_layout(self) -> dict[str, tuple[QLineF, ...]]:
        """Return the current viewport-space tick geometry for rendering/tests."""

        settings = self._tick_settings
        if settings is None:
            return {}
        plot = self._chart_rect_in_view(self.chart().plotArea())
        layout: dict[str, tuple[QLineF, ...]] = {
            "x_axis": (QLineF(plot.left(), plot.bottom(), plot.right(), plot.bottom()),),
            "y_axis": (QLineF(plot.left(), plot.top(), plot.left(), plot.bottom()),),
        }
        if self._energy_mirror_axis.isVisible():
            layout["x_mirror_axis"] = (
                QLineF(plot.left(), plot.top(), plot.right(), plot.top()),
            )
        if self._transmission_mirror_axis.isVisible():
            layout["y_mirror_axis"] = (
                QLineF(plot.right(), plot.top(), plot.right(), plot.bottom()),
            )

        x_major_positions = tuple(
            plot.left()
            + plot.width() * index / (settings.x_major_tick_count - 1)
            for index in range(settings.x_major_tick_count)
        )
        layout["x_major"] = tuple(
            self._vertical_tick(
                x,
                plot.bottom(),
                settings.major_length,
                top=False,
            )
            for x in x_major_positions
        )
        if self._energy_mirror_axis.isVisible():
            layout["x_mirror_major"] = tuple(
                self._vertical_tick(
                    x,
                    plot.top(),
                    settings.major_length,
                    top=True,
                )
                for x in x_major_positions
            )

        y_major_positions = tuple(
            self._log_y_position(float(exponent), plot)
            for exponent in _log_tick_exponents(
                self._transmission_axis.min(),
                self._transmission_axis.max(),
            )
        )
        layout["y_major"] = tuple(
            self._horizontal_tick(
                plot.left(),
                y,
                settings.major_length,
                right=False,
            )
            for y in y_major_positions
        )
        if self._transmission_mirror_axis.isVisible():
            layout["y_mirror_major"] = tuple(
                self._horizontal_tick(
                    plot.right(),
                    y,
                    settings.major_length,
                    right=True,
                )
                for y in y_major_positions
            )

        if not settings.minor_visible:
            return layout
        x_minor_positions = tuple(
            plot.left()
            + plot.width()
            * (major + minor / (settings.x_minor_tick_count + 1))
            / (settings.x_major_tick_count - 1)
            for major in range(settings.x_major_tick_count - 1)
            for minor in range(1, settings.x_minor_tick_count + 1)
        )
        layout["x_minor"] = tuple(
            self._vertical_tick(
                x,
                plot.bottom(),
                settings.minor_length,
                top=False,
            )
            for x in x_minor_positions
        )
        if self._energy_mirror_axis.isVisible():
            layout["x_mirror_minor"] = tuple(
                self._vertical_tick(
                    x,
                    plot.top(),
                    settings.minor_length,
                    top=True,
                )
                for x in x_minor_positions
            )

        minimum_log = log10(self._transmission_axis.min())
        maximum_log = log10(self._transmission_axis.max())
        y_minor_logs = tuple(
            exponent + minor / (settings.y_minor_tick_count + 1)
            for exponent in range(floor(minimum_log), ceil(maximum_log))
            for minor in range(1, settings.y_minor_tick_count + 1)
            if minimum_log
            < exponent + minor / (settings.y_minor_tick_count + 1)
            < maximum_log
        )
        layout["y_minor"] = tuple(
            self._horizontal_tick(
                plot.left(),
                self._log_y_position(log_value, plot),
                settings.minor_length,
                right=False,
            )
            for log_value in y_minor_logs
        )
        if self._transmission_mirror_axis.isVisible():
            layout["y_mirror_minor"] = tuple(
                self._horizontal_tick(
                    plot.right(),
                    self._log_y_position(log_value, plot),
                    settings.minor_length,
                    right=True,
                )
                for log_value in y_minor_logs
            )
        return layout

    def _paint_custom_axes_and_ticks(self, painter: QPainter) -> None:
        settings = self._tick_settings
        if settings is None:
            return
        layout = self.tick_layout()
        groups = (
            ("x_axis", self._x_axis_line_color, self._x_axis_line_width),
            ("x_mirror_axis", self._x_axis_line_color, self._x_axis_line_width),
            ("y_axis", self._y_axis_line_color, self._y_axis_line_width),
            ("y_mirror_axis", self._y_axis_line_color, self._y_axis_line_width),
            ("x_major", self._x_axis_line_color, settings.major_width),
            ("x_mirror_major", self._x_axis_line_color, settings.major_width),
            ("y_major", self._y_axis_line_color, settings.major_width),
            ("y_mirror_major", self._y_axis_line_color, settings.major_width),
            ("x_minor", self._x_axis_line_color, settings.minor_width),
            ("x_mirror_minor", self._x_axis_line_color, settings.minor_width),
            ("y_minor", self._y_axis_line_color, settings.minor_width),
            ("y_mirror_minor", self._y_axis_line_color, settings.minor_width),
        )
        for name, color, width in groups:
            lines = layout.get(name, ())
            if not lines:
                continue
            pen = QPen(color)
            pen.setWidthF(width)
            painter.setPen(pen)
            painter.drawLines(lines)

    def _vertical_tick(
        self,
        x: float,
        baseline: float,
        length: float,
        *,
        top: bool,
    ) -> QLineF:
        settings = self._tick_settings
        assert settings is not None
        inward_sign = 1.0 if top else -1.0
        sign = (
            inward_sign
            if settings.direction is TickDirection.INSIDE
            else -inward_sign
        )
        return QLineF(x, baseline, x, baseline + sign * length)

    def _horizontal_tick(
        self,
        baseline: float,
        y: float,
        length: float,
        *,
        right: bool,
    ) -> QLineF:
        settings = self._tick_settings
        assert settings is not None
        inward_sign = -1.0 if right else 1.0
        sign = (
            inward_sign
            if settings.direction is TickDirection.INSIDE
            else -inward_sign
        )
        return QLineF(baseline, y, baseline + sign * length, y)

    def _log_y_position(self, log_value: float, plot: QRectF) -> float:
        minimum_log = log10(self._transmission_axis.min())
        maximum_log = log10(self._transmission_axis.max())
        fraction = (log_value - minimum_log) / (maximum_log - minimum_log)
        return plot.bottom() - fraction * plot.height()

    def _probe_index_at(self, position: QPointF) -> tuple[int, int] | None:
        plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
        if not plot_bounds.contains(position):
            return None

        chart_position = self.chart().mapFromScene(
            self.mapToScene(position.toPoint())
        )
        energy = self.chart().mapToValue(chart_position, self._curves[0]).x()
        best_identity: tuple[int, int] | None = None
        best_distance_sq = _CURVE_HIT_RADIUS_PX**2
        for curve_index, points in enumerate(self._probe_points):
            if not points:
                continue
            insertion = bisect_left(self._probe_energies[curve_index], energy)
            candidates = {
                index
                for index in range(insertion - 2, insertion + 2)
                if 0 <= index < len(points)
            }
            for candidate in candidates:
                point = points[candidate][1]
                if not self._point_is_in_view(point):
                    continue
                distance_sq = _distance_squared(
                    position,
                    self._point_in_view(point),
                )
                if distance_sq <= best_distance_sq:
                    best_identity = (curve_index, candidate)
                    best_distance_sq = distance_sq

            for left_index in range(insertion - 2, insertion + 1):
                if left_index < 0 or left_index + 1 >= len(points):
                    continue
                left_source, left_point = points[left_index]
                right_source, right_point = points[left_index + 1]
                if right_source != left_source + 1:
                    continue
                distance_sq, projection = _segment_distance_squared(
                    position,
                    self._point_in_view(left_point),
                    self._point_in_view(right_point),
                )
                if distance_sq > best_distance_sq:
                    continue
                preferred = left_index if projection <= 0.5 else left_index + 1
                if not self._point_is_in_view(points[preferred][1]):
                    continue
                best_identity = (curve_index, preferred)
                best_distance_sq = distance_sq
        return best_identity

    def _show_probe(self, probe_index: tuple[int, int]) -> None:
        curve_index, point_index = probe_index
        series = self._curves[curve_index]
        _, point = self._probe_points[curve_index][point_index]
        self._active_probe_index = probe_index
        self._probe_marker.setColor(series.pen().color())
        self._probe_marker.replace([point])
        name = series.name().strip()
        identification = (
            f"{escape(name)}<br>" if name and len(self._curves) > 1 else ""
        )
        self._probe_readout.setText(
            identification
            + f"E − E<sub>F</sub> = {format_significant_value(point.x())} eV<br>"
            f"T(E) = {format_scientific_notation(point.y())}"
        )
        self._probe_readout.adjustSize()
        self._probe_readout.show()
        self._probe_readout.raise_()
        self._position_probe_readout()

    def _clear_probe(self) -> None:
        self._active_probe_index = None
        self._probe_marker.clear()
        self._probe_readout.hide()

    def _move_pinned_probe(self, direction: int) -> None:
        if self._pinned_probe_index is None:
            return
        curve_index, point_index = self._pinned_probe_index
        points = self._probe_points[curve_index]
        candidate = point_index + direction
        while 0 <= candidate < len(points):
            if self._point_is_in_view(points[candidate][1]):
                self._pinned_probe_index = (curve_index, candidate)
                self._show_probe(self._pinned_probe_index)
                return
            candidate += direction

    def _point_is_in_view(self, point: QPointF) -> bool:
        return (
            self._energy_axis.min() <= point.x() <= self._energy_axis.max()
            and self._transmission_axis.min()
            <= point.y()
            <= self._transmission_axis.max()
        )

    def _point_in_view(self, point: QPointF) -> QPointF:
        chart_position = self.chart().mapToPosition(point, self._curves[0])
        return QPointF(
            self.mapFromScene(self.chart().mapToScene(chart_position))
        )

    def _position_probe_readout(self) -> None:
        if self._active_probe_index is None:
            return
        curve_index, point_index = self._active_probe_index
        point = self._probe_points[curve_index][point_index][1]
        if not self._point_is_in_view(point):
            self._probe_readout.hide()
            return
        marker = self._point_in_view(point)
        width = self._probe_readout.width()
        height = self._probe_readout.height()
        x = int(marker.x() + 12.0)
        y = int(marker.y() - height - 10.0)
        if x + width > self.viewport().width() - 4:
            x = int(marker.x() - width - 12.0)
        if y < 4:
            y = int(marker.y() + 10.0)
        x = max(4, min(x, self.viewport().width() - width - 4))
        y = max(4, min(y, self.viewport().height() - height - 4))
        self._probe_readout.move(x, y)
        self._probe_readout.show()
        self._probe_readout.raise_()

    def _position_fermi_readout(self) -> None:
        if not self._fermi_readout.text() or not self._fermi_marker.count():
            self._fermi_readout.hide()
            return
        self._fermi_readout.adjustSize()
        plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
        marker = self._point_in_view(self._fermi_marker.at(0))
        width = float(self._fermi_readout.width())
        height = float(self._fermi_readout.height())
        gap = 12.0
        candidates = (
            QRectF(marker.x() + gap, marker.y() - height - gap, width, height),
            QRectF(marker.x() - width - gap, marker.y() - height - gap, width, height),
            QRectF(marker.x() + gap, marker.y() + gap, width, height),
            QRectF(marker.x() - width - gap, marker.y() + gap, width, height),
        )
        label_bounds = plot_bounds.adjusted(5.0, 5.0, -5.0, -5.0)
        bounded_candidates = tuple(
            _bounded_rect(candidate, label_bounds) for candidate in candidates
        )
        best_index, best_rect = min(
            enumerate(bounded_candidates),
            key=lambda indexed: (
                self._curve_intersection_count(
                    indexed[1].adjusted(-4.0, -4.0, 4.0, 4.0)
                ),
                indexed[0],
            ),
        )
        del best_index
        self._fermi_readout.move(round(best_rect.x()), round(best_rect.y()))
        self._fermi_readout.show()
        self._fermi_readout.raise_()

    def _curve_intersection_count(self, rectangle: QRectF) -> int:
        count = 0
        for points in self._probe_points:
            for index in range(len(points) - 1):
                left_source, left_point = points[index]
                right_source, right_point = points[index + 1]
                if right_source != left_source + 1:
                    continue
                if _segment_intersects_rect(
                    self._point_in_view(left_point),
                    self._point_in_view(right_point),
                    rectangle,
                ):
                    count += 1
        return count

    def _position_transmission_title(self) -> None:
        if not self._transmission_title_label._text:
            self._transmission_title_label.hide()
            return
        visible_labels = [
            label for label in self._log_tick_labels if not label.isHidden()
        ]
        if not visible_labels:
            self._transmission_title_label.hide()
            return
        self._transmission_title_label.adjustSize()
        leftmost_tick = min(label.geometry().left() for label in visible_labels)
        x = max(2, leftmost_tick - self._transmission_title_label.width() - 12)
        plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
        y = round(
            plot_bounds.center().y()
            - self._transmission_title_label.height() / 2.0
        )
        self._transmission_title_label.move(x, y)
        self._transmission_title_label.show()
        self._transmission_title_label.raise_()

    def _update_log_tick_labels(self) -> None:
        exponents = _log_tick_exponents(
            self._transmission_axis.min(),
            self._transmission_axis.max(),
        )
        self._ensure_log_labels(self._log_tick_labels, len(exponents), "left")
        self._ensure_log_labels(
            self._right_log_tick_labels,
            len(exponents),
            "right",
        )
        plot_bounds = self._chart_rect_in_view(self.chart().plotArea())
        for labels, side in (
            (self._log_tick_labels, "left"),
            (self._right_log_tick_labels, "right"),
        ):
            show_side = (
                side == "left" or self._transmission_mirror_axis.isVisible()
            )
            for index, label in enumerate(labels):
                if index >= len(exponents) or not show_side:
                    label.hide()
                    continue
                exponent = exponents[index]
                label.setText(_power_of_ten_label(exponent))
                label.adjustSize()
                axis_point = self._point_in_view(
                    QPointF(self._energy_axis.min(), 10.0**exponent)
                )
                if side == "left":
                    x = int(plot_bounds.left() - label.width() - 7.0)
                else:
                    x = int(plot_bounds.right() + 7.0)
                y = int(axis_point.y() - label.height() / 2.0)
                label.move(max(2, x), y)
                label.show()
                label.raise_()

    def _ensure_log_labels(
        self,
        labels: list[QLabel],
        count: int,
        side: str,
    ) -> None:
        while len(labels) < count:
            label = QLabel(self.viewport())
            label.setObjectName(
                "transmissionLogTickLabel"
                if side == "left"
                else "transmissionRightLogTickLabel"
            )
            alignment = (
                Qt.AlignmentFlag.AlignRight
                if side == "left"
                else Qt.AlignmentFlag.AlignLeft
            )
            label.setAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
            label.setFont(self._y_label_font)
            label.setStyleSheet(
                "QLabel { background: transparent; "
                f"color: {self._y_overlay_color.name(QColor.NameFormat.HexRgb)}; }}"
            )
            labels.append(label)

    def _chart_rect_in_view(self, rectangle: QRectF) -> QRectF:
        scene_polygon = self.chart().mapToScene(rectangle)
        viewport_polygon = self.mapFromScene(scene_polygon)
        return QRectF(viewport_polygon.boundingRect())


@dataclass(frozen=True, slots=True)
class TransmissionCurveDefinition:
    """One already validated curve, its samples, and its initial style."""

    object_name: str
    legend_label: str
    points: tuple[tuple[float, float], ...]
    color: str | None = None
    width: float = 2.0
    line_style: PlotLineStyle = PlotLineStyle.SOLID
    legend_visible: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.object_name, str) or not self.object_name.strip():
            raise ValueError("transmission curve requires an object name")
        if not isinstance(self.legend_label, str) or not self.legend_label.strip():
            raise ValueError("transmission curve requires a legend label")
        if not self.points:
            raise ValueError("transmission curve requires at least one sample")
        if not all(
            isfinite(energy) and isfinite(value) for energy, value in self.points
        ):
            raise ValueError("transmission curve samples must be finite")
        if not isinstance(self.line_style, PlotLineStyle):
            raise TypeError("transmission curve style must be a PlotLineStyle")
        if not 0.5 <= self.width <= 12.0:
            raise ValueError("curve width must be between 0.5 and 12 px")


class TransmissionCanvasView(QWidget):
    """Own the reviewed transmission chart, its controls, and its export."""

    def __init__(
        self,
        curves: tuple[TransmissionCurveDefinition, ...],
        *,
        object_prefix: str,
        energy_axis_title: str,
        transmission_axis_title: str,
        energy_range: tuple[float, float],
        transmission_range: tuple[float, float],
        chart_title: str = "",
        chart_margins: tuple[int, int, int, int] = (84, 18, 18, 24),
        fermi_point: tuple[float, float] | None = None,
        fermi_annotation: str | None = None,
        fermi_curve_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        definitions = tuple(curves)
        if not definitions or not all(
            isinstance(definition, TransmissionCurveDefinition)
            for definition in definitions
        ):
            raise TypeError("transmission canvas requires curve definitions")
        if not isinstance(object_prefix, str) or not object_prefix.strip():
            raise ValueError("transmission canvas requires an object prefix")
        if not 0 <= fermi_curve_index < len(definitions):
            raise ValueError("transmission canvas Fermi curve index is unknown")
        energy_minimum, energy_maximum = _validated_range(
            energy_range,
            positive=False,
        )
        transmission_minimum, transmission_maximum = _validated_range(
            transmission_range,
            positive=True,
        )

        self._curve_definitions = definitions
        self._default_energy_range = (energy_minimum, energy_maximum)
        self._default_transmission_range = (
            transmission_minimum,
            transmission_maximum,
        )
        self._fermi_curve_index = fermi_curve_index
        self._chart_margins = tuple(int(margin) for margin in chart_margins)
        self.setFont(chart_font(self.font()))
        self._layout = QVBoxLayout(self)

        self._curve_series = tuple(
            self._create_series(definition) for definition in definitions
        )

        self._reference_series = QLineSeries(self)
        self._reference_series.setObjectName(f"{object_prefix}FermiReference")
        self._reference_series.replace(
            [
                QPointF(0.0, transmission_minimum),
                QPointF(0.0, transmission_maximum),
            ]
        )
        reference_pen = QPen(self.palette().color(QPalette.ColorRole.Mid))
        reference_pen.setStyle(Qt.PenStyle.DashLine)
        reference_pen.setWidthF(1.0)
        self._reference_series.setPen(reference_pen)

        self._fermi_marker = QScatterSeries(self)
        self._fermi_marker.setObjectName(f"{object_prefix}FermiMarker")
        self._fermi_marker.setMarkerSize(9.0)
        if fermi_point is not None and fermi_point[1] > 0.0:
            self._fermi_marker.append(fermi_point[0], fermi_point[1])

        self._probe_marker = QScatterSeries(self)
        self._probe_marker.setObjectName(f"{object_prefix}ProbeMarker")
        self._probe_marker.setMarkerSize(10.0)
        self._probe_marker.setBorderColor(QColor("#000000"))

        self._chart = QChart()
        self._chart.setObjectName(f"{object_prefix}Chart")
        self._chart.setTitle(chart_title)
        self._chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._chart.setMargins(QMargins(*self._chart_margins))
        self._chart.setTitleFont(
            chart_font(self._chart.titleFont(), point_size=_AXIS_TITLE_POINT_SIZE)
        )
        self._chart.legend().setFont(chart_font(self._chart.legend().font()))
        self._chart.setPlotAreaBackgroundVisible(True)
        plot_border = QPen(QColor("#000000"))
        plot_border.setWidthF(1.0)
        self._chart.setPlotAreaBackgroundPen(plot_border)
        for series in self._chart_series():
            self._chart.addSeries(series)

        self._energy_axis = QValueAxis(self._chart)
        self._energy_axis.setObjectName(f"{object_prefix}EnergyAxis")
        self._energy_axis.setTitleText(energy_axis_title)
        self._energy_axis.setLabelFormat("%.3g")
        self._energy_axis.setTickCount(9)
        self._energy_axis.setLabelsFont(
            chart_font(
                self._energy_axis.labelsFont(),
                point_size=_AXIS_LABEL_POINT_SIZE,
            )
        )
        self._energy_axis.setTitleFont(
            chart_font(
                self._energy_axis.titleFont(),
                point_size=_AXIS_TITLE_POINT_SIZE,
            )
        )
        self._chart.addAxis(self._energy_axis, Qt.AlignmentFlag.AlignBottom)

        self._energy_mirror_axis = QValueAxis(self._chart)
        self._energy_mirror_axis.setObjectName(
            f"{object_prefix}EnergyMirrorAxis"
        )
        self._energy_mirror_axis.setLabelFormat("%.3g")
        self._energy_mirror_axis.setTickCount(9)
        self._energy_mirror_axis.setLabelsFont(
            chart_font(
                self._energy_axis.labelsFont(),
                point_size=_AXIS_LABEL_POINT_SIZE,
            )
        )
        self._energy_mirror_axis.setTitleVisible(False)
        self._energy_mirror_axis.setGridLineVisible(False)
        self._chart.addAxis(self._energy_mirror_axis, Qt.AlignmentFlag.AlignTop)
        self._energy_mirror_axis.hide()

        self._transmission_axis = QLogValueAxis(self._chart)
        self._transmission_axis.setObjectName(f"{object_prefix}ValueAxis")
        self._transmission_axis.setTitleText(transmission_axis_title)
        self._transmission_axis.setBase(10.0)
        self._transmission_axis.setLabelsFont(
            chart_font(
                self._transmission_axis.labelsFont(),
                point_size=_AXIS_LABEL_POINT_SIZE,
            )
        )
        self._transmission_axis.setTitleFont(
            chart_font(
                self._transmission_axis.titleFont(),
                point_size=_AXIS_TITLE_POINT_SIZE,
            )
        )
        self._transmission_axis.setTitleVisible(False)
        self._transmission_axis.setLabelsVisible(False)
        self._chart.addAxis(self._transmission_axis, Qt.AlignmentFlag.AlignLeft)

        self._transmission_mirror_axis = QLogValueAxis(self._chart)
        self._transmission_mirror_axis.setObjectName(
            f"{object_prefix}ValueMirrorAxis"
        )
        self._transmission_mirror_axis.setBase(10.0)
        self._transmission_mirror_axis.setTitleVisible(False)
        self._transmission_mirror_axis.setLabelsVisible(False)
        self._transmission_mirror_axis.setGridLineVisible(False)
        self._chart.addAxis(
            self._transmission_mirror_axis,
            Qt.AlignmentFlag.AlignRight,
        )
        self._transmission_mirror_axis.hide()

        for series in self._chart_series():
            series.attachAxis(self._energy_axis)
            series.attachAxis(self._transmission_axis)
        for axis in (self._energy_axis, self._energy_mirror_axis):
            axis.setRange(energy_minimum, energy_maximum)
        for axis in (self._transmission_axis, self._transmission_mirror_axis):
            axis.setRange(transmission_minimum, transmission_maximum)
        self._energy_axis.rangeChanged.connect(self._energy_mirror_axis.setRange)
        self._transmission_axis.rangeChanged.connect(
            self._transmission_mirror_axis.setRange
        )
        self._transmission_axis.rangeChanged.connect(self._update_fermi_reference)

        marker_color = self._curve_series[fermi_curve_index].pen().color()
        self._fermi_marker.setColor(marker_color)
        self._fermi_marker.setBorderColor(marker_color)
        self._probe_marker.setColor(marker_color)
        self._chart.legend().setVisible(
            any(definition.legend_visible for definition in definitions)
        )
        for series, definition in zip(
            self._curve_series,
            definitions,
            strict=True,
        ):
            for marker in self._chart.legend().markers(series):
                marker.setVisible(definition.legend_visible)
        for auxiliary in (
            self._reference_series,
            self._fermi_marker,
            self._probe_marker,
        ):
            for marker in self._chart.legend().markers(auxiliary):
                marker.setVisible(False)

        self._chart_view = TransmissionChartView(
            self._chart,
            self._curve_series,
            self._fermi_marker,
            fermi_annotation,
            self._probe_marker,
            self._energy_axis,
            self._transmission_axis,
            self._energy_mirror_axis,
            self._transmission_mirror_axis,
            transmission_axis_title,
            self,
        )
        self._chart_view.setObjectName(f"{object_prefix}ChartView")
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._chart_view.axisDoubleClicked.connect(self._edit_axis_range)
        self._chart_view.curveDoubleClicked.connect(
            lambda: self.open_view_settings("curve")
        )
        self._chart_view.canvasDoubleClicked.connect(
            lambda: self.open_view_settings("canvas")
        )
        self._layout.addWidget(self._chart_view, stretch=1)

        self._canvas_size_custom = False
        self._visual_settings = self._create_initial_visual_settings()
        self._hide_native_axis_lines()
        self._apply_tick_settings(self._visual_settings.ticks)

    @property
    def visual_settings(self) -> TransmissionVisualSettings:
        return self._visual_settings

    def export_base_size(self) -> QSize:
        """Return the configured plot canvas size used by image export."""

        if self._canvas_size_custom:
            canvas = self._visual_settings.canvas
            return QSize(canvas.export_width, canvas.export_height)
        size = self._chart_view.size()
        return QSize(max(1, size.width()), max(1, size.height()))

    def capture_image(self, scale_factor: int = 1) -> QImage:
        """Render the styled chart canvas without the surrounding controls."""

        return render_widget_image(
            self._chart_view,
            base_size=self.export_base_size(),
            scale_factor=scale_factor,
        )

    @Slot()
    def open_view_settings(self, initial_page: str = "x") -> None:
        """Open comprehensive presentation controls at the requested page."""

        settings = replace(
            self._visual_settings,
            x_axis=replace(
                self._visual_settings.x_axis,
                minimum=self._energy_axis.min(),
                maximum=self._energy_axis.max(),
            ),
            y_axis=replace(
                self._visual_settings.y_axis,
                minimum=self._transmission_axis.min(),
                maximum=self._transmission_axis.max(),
            ),
        )
        if not self._canvas_size_custom:
            current = self.export_base_size()
            settings = replace(
                settings,
                canvas=replace(
                    settings.canvas,
                    export_width=max(400, current.width()),
                    export_height=max(300, current.height()),
                ),
            )
        dialog = TransmissionSettingsDialog(
            settings,
            self,
            initial_page=initial_page,
        )
        dialog.settings_applied.connect(self._apply_visual_settings)
        dialog.exec()
        dialog.deleteLater()

    @Slot()
    def reset_view(self) -> None:
        """Restore both axes to this result's frozen default ranges."""

        self._energy_axis.setRange(*self._default_energy_range)
        self._transmission_axis.setRange(*self._default_transmission_range)
        self._visual_settings = replace(
            self._visual_settings,
            x_axis=replace(
                self._visual_settings.x_axis,
                minimum=self._default_energy_range[0],
                maximum=self._default_energy_range[1],
            ),
            y_axis=replace(
                self._visual_settings.y_axis,
                minimum=self._default_transmission_range[0],
                maximum=self._default_transmission_range[1],
            ),
        )

    def _chart_series(self) -> tuple[QLineSeries | QScatterSeries, ...]:
        return (
            *self._curve_series,
            self._reference_series,
            self._fermi_marker,
            self._probe_marker,
        )

    def _create_series(
        self,
        definition: TransmissionCurveDefinition,
    ) -> QLineSeries:
        series = QLineSeries(self)
        series.setObjectName(definition.object_name)
        if definition.legend_visible:
            # A hidden legend keeps no redundant curve name on the chart.
            series.setName(definition.legend_label)
        series.replace(
            [QPointF(energy, value) for energy, value in definition.points]
        )
        if definition.color is not None:
            pen = QPen(QColor(definition.color))
            pen.setWidthF(definition.width)
            pen.setStyle(qt_pen_style(definition.line_style))
            series.setPen(pen)
        return series

    def _create_initial_visual_settings(self) -> TransmissionVisualSettings:
        x_grid = self._energy_axis.gridLinePen()
        y_grid = self._transmission_axis.gridLinePen()
        size = self._chart_view.size()
        left, top, right, bottom = self._chart_margins
        return TransmissionVisualSettings(
            x_axis=AxisVisualSettings(
                minimum=self._energy_axis.min(),
                maximum=self._energy_axis.max(),
                title=self._energy_axis.titleText(),
                font_family=_CHART_FONT_FAMILY,
                label_point_size=_AXIS_LABEL_POINT_SIZE,
                title_point_size=_AXIS_TITLE_POINT_SIZE,
                line_color="#000000",
                line_width=1.0,
                grid_visible=self._energy_axis.isGridLineVisible(),
                grid_color=x_grid.color().name(),
                grid_width=max(0.5, x_grid.widthF()),
                grid_style=plot_line_style(x_grid.style()),
                mirror_visible=False,
            ),
            y_axis=AxisVisualSettings(
                minimum=self._transmission_axis.min(),
                maximum=self._transmission_axis.max(),
                title=self._transmission_axis.titleText(),
                font_family=_CHART_FONT_FAMILY,
                label_point_size=_AXIS_LABEL_POINT_SIZE,
                title_point_size=_AXIS_TITLE_POINT_SIZE,
                line_color="#000000",
                line_width=1.0,
                grid_visible=self._transmission_axis.isGridLineVisible(),
                grid_color=y_grid.color().name(),
                grid_width=max(0.5, y_grid.widthF()),
                grid_style=plot_line_style(y_grid.style()),
                mirror_visible=False,
            ),
            ticks=TickVisualSettings(
                major_length=6.0,
                major_width=1.0,
                minor_visible=False,
                minor_length=3.5,
                minor_width=1.0,
                direction=TickDirection.OUTSIDE,
                x_major_tick_count=self._energy_axis.tickCount(),
                x_minor_tick_count=1,
                y_minor_tick_count=8,
            ),
            curves=tuple(
                CurveVisualSettings(
                    color=series.pen().color().name(),
                    width=max(1.0, series.pen().widthF()),
                    line_style=plot_line_style(series.pen().style()),
                    legend_visible=definition.legend_visible,
                    legend_label=definition.legend_label,
                )
                for series, definition in zip(
                    self._curve_series,
                    self._curve_definitions,
                    strict=True,
                )
            ),
            canvas=CanvasVisualSettings(
                export_width=max(400, size.width()),
                export_height=max(300, size.height()),
                background_color="#ffffff",
                plot_background_color="#ffffff",
                frame_color="#000000",
                frame_width=1.0,
                left_margin=left,
                top_margin=top,
                right_margin=right,
                bottom_margin=bottom,
            ),
        )

    @Slot(object)
    def _apply_visual_settings(
        self,
        settings: TransmissionVisualSettings,
    ) -> None:
        if not isinstance(settings, TransmissionVisualSettings):
            raise TypeError("Transmission view settings are invalid")
        self._visual_settings = settings
        self._canvas_size_custom = True
        self._apply_axis_settings(
            self._energy_axis,
            self._energy_mirror_axis,
            settings.x_axis,
            logarithmic=False,
        )
        self._apply_axis_settings(
            self._transmission_axis,
            self._transmission_mirror_axis,
            settings.y_axis,
            logarithmic=True,
        )
        self._apply_tick_settings(settings.ticks)
        self._apply_curve_settings(settings.curves)
        self._apply_canvas_settings(settings.canvas)
        self._chart_view.set_y_overlay_style(
            title=settings.y_axis.title,
            font_family=settings.y_axis.font_family,
            label_point_size=settings.y_axis.label_point_size,
            title_point_size=settings.y_axis.title_point_size,
            color=QColor(settings.y_axis.line_color),
        )
        self._chart_view.refresh_overlays()

    def _apply_axis_settings(
        self,
        axis: QValueAxis | QLogValueAxis,
        mirror: QValueAxis | QLogValueAxis,
        settings: AxisVisualSettings,
        *,
        logarithmic: bool,
    ) -> None:
        axis.setRange(settings.minimum, settings.maximum)
        mirror.setRange(settings.minimum, settings.maximum)
        axis.setTitleText(settings.title)
        labels_font = QFont(settings.font_family)
        labels_font.setPointSizeF(settings.label_point_size)
        title_font = QFont(settings.font_family)
        title_font.setPointSizeF(settings.title_point_size)
        color = QColor(settings.line_color)
        hidden_axis_pen = QPen(QColor(0, 0, 0, 0))
        hidden_axis_pen.setWidthF(settings.line_width)
        grid_pen = QPen(QColor(settings.grid_color))
        grid_pen.setWidthF(settings.grid_width)
        grid_pen.setStyle(qt_pen_style(settings.grid_style))
        for target in (axis, mirror):
            target.setLabelsFont(labels_font)
            target.setTitleFont(title_font)
            target.setLabelsBrush(QBrush(color))
            target.setTitleBrush(QBrush(color))
            # Qt Charts exposes no public tick-length/direction API. Hide its
            # inseparable native line/ticks and paint the explicit model in
            # TransmissionChartView while retaining native labels and grids.
            target.setLinePen(hidden_axis_pen)
        axis.setGridLinePen(grid_pen)
        axis.setGridLineVisible(settings.grid_visible)
        axis.setMinorGridLineVisible(False)
        mirror.setGridLineVisible(False)
        mirror.setMinorGridLineVisible(False)
        mirror.setTitleVisible(False)
        mirror.setLabelsVisible(settings.mirror_visible)
        if logarithmic:
            # Reserve native right-axis label space, then paint the same true
            # superscript notation used on the custom left logarithmic axis.
            mirror.setLabelsBrush(QBrush(QColor(0, 0, 0, 0)))
        mirror.setVisible(settings.mirror_visible)

    def _apply_tick_settings(self, settings: TickVisualSettings) -> None:
        self._energy_axis.setTickCount(settings.x_major_tick_count)
        self._energy_mirror_axis.setTickCount(settings.x_major_tick_count)
        for axis in (self._energy_axis, self._energy_mirror_axis):
            axis.setMinorTickCount(0)
        for axis in (
            self._transmission_axis,
            self._transmission_mirror_axis,
        ):
            axis.setMinorTickCount(0)
        self._chart_view.set_tick_style(
            settings,
            x_axis_color=QColor(self._visual_settings.x_axis.line_color),
            y_axis_color=QColor(self._visual_settings.y_axis.line_color),
            x_axis_width=self._visual_settings.x_axis.line_width,
            y_axis_width=self._visual_settings.y_axis.line_width,
        )

    def _hide_native_axis_lines(self) -> None:
        hidden = QPen(QColor(0, 0, 0, 0))
        for axis in (
            self._energy_axis,
            self._energy_mirror_axis,
            self._transmission_axis,
            self._transmission_mirror_axis,
        ):
            axis.setLinePen(hidden)

    def _apply_curve_settings(
        self,
        settings: tuple[CurveVisualSettings, ...],
    ) -> None:
        if len(settings) != len(self._curve_series):
            raise ValueError(
                "Transmission curve settings must match the displayed curves"
            )
        legend_visible = False
        for series, curve in zip(self._curve_series, settings, strict=True):
            pen = QPen(QColor(curve.color))
            pen.setWidthF(curve.width)
            pen.setStyle(qt_pen_style(curve.line_style))
            series.setPen(pen)
            series.setName(curve.legend_label)
            legend_visible = legend_visible or curve.legend_visible
            for marker in self._chart.legend().markers(series):
                marker.setVisible(curve.legend_visible)
        marker_color = QColor(settings[self._fermi_curve_index].color)
        self._fermi_marker.setColor(marker_color)
        self._fermi_marker.setBorderColor(marker_color)
        self._probe_marker.setColor(marker_color)
        self._chart.legend().setVisible(legend_visible)
        for auxiliary in (
            self._reference_series,
            self._fermi_marker,
            self._probe_marker,
        ):
            for marker in self._chart.legend().markers(auxiliary):
                marker.setVisible(False)

    def _apply_canvas_settings(self, settings: CanvasVisualSettings) -> None:
        self._chart.setBackgroundVisible(True)
        self._chart.setBackgroundBrush(QBrush(QColor(settings.background_color)))
        self._chart.setPlotAreaBackgroundVisible(True)
        self._chart.setPlotAreaBackgroundBrush(
            QBrush(QColor(settings.plot_background_color))
        )
        frame_pen = QPen(QColor(settings.frame_color))
        frame_pen.setWidthF(settings.frame_width)
        self._chart.setPlotAreaBackgroundPen(frame_pen)
        self._chart.setMargins(
            QMargins(
                settings.left_margin,
                settings.top_margin,
                settings.right_margin,
                settings.bottom_margin,
            )
        )

    @Slot(str)
    def _edit_axis_range(self, axis: str) -> None:
        if axis in {"x", "y"}:
            self.open_view_settings(axis)

    @Slot(float, float)
    def _update_fermi_reference(self, minimum: float, maximum: float) -> None:
        self._reference_series.replace(
            [QPointF(0.0, minimum), QPointF(0.0, maximum)]
        )


def _validated_range(
    value: tuple[float, float],
    *,
    positive: bool,
) -> tuple[float, float]:
    minimum, maximum = (float(value[0]), float(value[1]))
    if not isfinite(minimum) or not isfinite(maximum):
        raise ValueError("transmission canvas ranges must be finite")
    if minimum >= maximum:
        raise ValueError("transmission canvas minimum must be below maximum")
    if positive and minimum <= 0.0:
        raise ValueError("logarithmic transmission range must be positive")
    return minimum, maximum

def _format_axis_value(value: float) -> str:
    return f"{value:.12g}"


def chart_font(source: QFont, *, point_size: float | None = None) -> QFont:
    font = QFont(source)
    font.setFamily(_CHART_FONT_FAMILY)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    if point_size is not None:
        font.setPointSizeF(point_size)
    return font


def qt_pen_style(style: PlotLineStyle) -> Qt.PenStyle:
    return {
        PlotLineStyle.SOLID: Qt.PenStyle.SolidLine,
        PlotLineStyle.DASH: Qt.PenStyle.DashLine,
        PlotLineStyle.DOT: Qt.PenStyle.DotLine,
        PlotLineStyle.DASH_DOT: Qt.PenStyle.DashDotLine,
    }[style]


def plot_line_style(style: Qt.PenStyle) -> PlotLineStyle:
    return {
        Qt.PenStyle.DashLine: PlotLineStyle.DASH,
        Qt.PenStyle.DotLine: PlotLineStyle.DOT,
        Qt.PenStyle.DashDotLine: PlotLineStyle.DASH_DOT,
    }.get(style, PlotLineStyle.SOLID)


def format_scientific_notation(
    value: float,
    *,
    significant_digits: int = 3,
) -> str:
    if not isfinite(value):
        raise ValueError("scientific display value must be finite")
    if significant_digits < 1:
        raise ValueError("scientific display requires at least one digit")
    coefficient, exponent_text = f"{value:.{significant_digits - 1}e}".split("e")
    coefficient = coefficient.replace("-", "−")
    exponent = int(exponent_text)
    return f"{coefficient} × 10{str(exponent).translate(_SUPERSCRIPT_TRANSLATION)}"


def format_significant_value(
    value: float,
    *,
    significant_digits: int = 3,
) -> str:
    if not isfinite(value):
        raise ValueError("significant display value must be finite")
    if significant_digits < 1:
        raise ValueError("significant display requires at least one digit")
    if value == 0.0:
        return f"{0.0:.{significant_digits - 1}f}"
    exponent = floor(log10(abs(value)))
    if -3 <= exponent < significant_digits:
        decimal_places = max(0, significant_digits - exponent - 1)
        return f"{value:.{decimal_places}f}".replace("-", "−")
    return format_scientific_notation(
        value,
        significant_digits=significant_digits,
    )


def _distance_squared(first: QPointF, second: QPointF) -> float:
    return (first.x() - second.x()) ** 2 + (first.y() - second.y()) ** 2


def _segment_distance_squared(
    point: QPointF,
    start: QPointF,
    end: QPointF,
) -> tuple[float, float]:
    delta_x = end.x() - start.x()
    delta_y = end.y() - start.y()
    length_squared = delta_x**2 + delta_y**2
    if length_squared <= 0.0:
        return _distance_squared(point, start), 0.0
    projection = (
        (point.x() - start.x()) * delta_x
        + (point.y() - start.y()) * delta_y
    ) / length_squared
    projection = max(0.0, min(1.0, projection))
    nearest = QPointF(
        start.x() + projection * delta_x,
        start.y() + projection * delta_y,
    )
    return _distance_squared(point, nearest), projection


def _bounded_rect(rectangle: QRectF, bounds: QRectF) -> QRectF:
    if rectangle.width() >= bounds.width():
        x = bounds.left()
    else:
        x = min(max(rectangle.left(), bounds.left()), bounds.right() - rectangle.width())
    if rectangle.height() >= bounds.height():
        y = bounds.top()
    else:
        y = min(max(rectangle.top(), bounds.top()), bounds.bottom() - rectangle.height())
    return QRectF(x, y, rectangle.width(), rectangle.height())


def _segment_intersects_rect(
    start: QPointF,
    end: QPointF,
    rectangle: QRectF,
) -> bool:
    if rectangle.contains(start) or rectangle.contains(end):
        return True
    segment = QLineF(start, end)
    edges = (
        QLineF(rectangle.topLeft(), rectangle.topRight()),
        QLineF(rectangle.topRight(), rectangle.bottomRight()),
        QLineF(rectangle.bottomRight(), rectangle.bottomLeft()),
        QLineF(rectangle.bottomLeft(), rectangle.topLeft()),
    )
    return any(
        segment.intersects(edge)[0]
        == QLineF.IntersectionType.BoundedIntersection
        for edge in edges
    )
