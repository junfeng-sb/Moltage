"""Shared base-10 logarithmic-axis presentation for Moltage charts."""

from __future__ import annotations

from math import ceil, floor, isfinite, log10

from PySide6.QtCharts import QChart, QChartView, QLogValueAxis
from PySide6.QtCore import QRectF, Qt, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QLabel, QWidget


SUPERSCRIPT_TRANSLATION = str.maketrans(
    {
        "-": "⁻",
        "0": "⁰",
        "1": "¹",
        "2": "²",
        "3": "³",
        "4": "⁴",
        "5": "⁵",
        "6": "⁶",
        "7": "⁷",
        "8": "⁸",
        "9": "⁹",
    }
)


def power_of_ten_label(exponent: int) -> str:
    """Return the project-wide decade-label form; never return E notation."""

    return f"10{str(exponent).translate(SUPERSCRIPT_TRANSLATION)}"


def log_tick_exponents(minimum: float, maximum: float) -> tuple[int, ...]:
    """Return a bounded set of integral decades inside one positive range."""

    if (
        not isfinite(minimum)
        or not isfinite(maximum)
        or minimum <= 0.0
        or minimum >= maximum
    ):
        return ()
    first = ceil(log10(minimum) - 1.0e-12)
    last = floor(log10(maximum) + 1.0e-12)
    if first > last:
        return ()
    count = last - first + 1
    stride = max(1, ceil(count / 16))
    exponents = list(range(first, last + 1, stride))
    if exponents and exponents[-1] != last:
        exponents.append(last)
    return tuple(exponents)


class PowerOfTenLogChartView(QChartView):
    """Replace Qt's ``1E-03`` log ticks with deterministic ``10⁻³`` labels."""

    def __init__(
        self,
        chart: QChart,
        y_axis: QLogValueAxis,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(chart, parent)
        self._power_y_axis = y_axis
        self._power_tick_labels: list[QLabel] = []
        self._power_label_font = y_axis.labelsFont()
        self._power_label_color = y_axis.labelsBrush().color()
        # Preserve Qt Charts' layout reservation while replacing only the text.
        y_axis.setLabelsBrush(QBrush(QColor(0, 0, 0, 0)))
        chart.plotAreaChanged.connect(self._refresh_power_tick_labels)
        y_axis.rangeChanged.connect(self._axis_range_changed)

    @property
    def power_tick_labels(self) -> tuple[QLabel, ...]:
        return tuple(label for label in self._power_tick_labels if label.isVisible())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_power_tick_labels()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_power_tick_labels()

    @Slot(float, float)
    def _axis_range_changed(self, _minimum: float, _maximum: float) -> None:
        self._refresh_power_tick_labels()

    @Slot()
    @Slot(QRectF)
    def _refresh_power_tick_labels(self, _plot_area: QRectF | None = None) -> None:
        exponents = log_tick_exponents(
            self._power_y_axis.min(),
            self._power_y_axis.max(),
        )
        while len(self._power_tick_labels) < len(exponents):
            label = QLabel(self.viewport())
            label.setObjectName("powerOfTenLogTickLabel")
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
            label.setFont(self._power_label_font)
            label.setStyleSheet(
                "QLabel { background: transparent; "
                f"color: {self._power_label_color.name(QColor.NameFormat.HexRgb)}; }}"
            )
            self._power_tick_labels.append(label)
        plot = self._chart_rect_in_view(self.chart().plotArea())
        log_min = log10(self._power_y_axis.min())
        log_max = log10(self._power_y_axis.max())
        span = log_max - log_min
        for index, label in enumerate(self._power_tick_labels):
            if index >= len(exponents) or span <= 0.0 or plot.height() <= 0.0:
                label.hide()
                continue
            exponent = exponents[index]
            label.setText(power_of_ten_label(exponent))
            label.adjustSize()
            y = plot.bottom() - (exponent - log_min) / span * plot.height()
            label.move(
                max(2, round(plot.left() - label.width() - 7.0)),
                round(y - label.height() / 2.0),
            )
            label.show()
            label.raise_()

    def _chart_rect_in_view(self, rectangle: QRectF) -> QRectF:
        scene_polygon = self.chart().mapToScene(rectangle)
        viewport_polygon = self.mapFromScene(scene_polygon)
        return QRectF(viewport_polygon.boundingRect())
