"""Read-only alpha/beta/total presentation for one verified ORCA WBL result."""

from __future__ import annotations

from math import floor, log10

from PySide6.QtCharts import (
    QChart,
    QLineSeries,
    QLogValueAxis,
    QValueAxis,
)
from PySide6.QtCore import QMargins, QPointF, QSize, Qt, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from moltage.gui.log_axis import PowerOfTenLogChartView
from moltage.gui.view_export import render_widget_image
from moltage.orca.wbl import WblSpinTreatment
from moltage.orca.wbl_artifacts import OrcaWblPresentation


class OrcaWblTransmissionView(QWidget):
    """Display all persisted spin curves without relabeling the WBL hypothesis."""

    def __init__(
        self,
        project_name: str,
        presentation: OrcaWblPresentation,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(project_name, str) or not project_name.strip():
            raise ValueError("ORCA WBL view requires a project name")
        if not isinstance(presentation, OrcaWblPresentation):
            raise TypeError("ORCA WBL view requires verified presentation data")
        self._presentation = presentation
        self.setObjectName("orcaWblTransmissionView")
        font = QFont("Arial")
        self.setFont(font)
        layout = QVBoxLayout(self)

        provenance = QLabel(
            f"Project: {project_name.strip()}  ·  {presentation.model_id}  ·  "
            f"{presentation.model_classification}  ·  "
            f"{len(presentation.energy_relative_ev):,} points",
            self,
        )
        provenance.setObjectName("orcaWblProvenance")
        provenance.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(provenance)
        closed_shell = (
            presentation.spin_treatment
            is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
        )
        summary_text = (
            "T(E<sub>F</sub>) = "
            f"{presentation.t_total_at_fermi:.6g}  ·  "
            "closed-shell, spin-degenerate"
            if closed_shell
            else (
                "T<sub>α</sub>(E<sub>F</sub>) = "
                f"{presentation.t_alpha_at_fermi:.6g}  ·  "
                "T<sub>β</sub>(E<sub>F</sub>) = "
                f"{presentation.t_beta_at_fermi:.6g}  ·  "
                "T<sub>α+β</sub>(E<sub>F</sub>) = "
                f"{presentation.t_total_at_fermi:.6g}"
            )
        )
        summary = QLabel(summary_text, self)
        summary.setObjectName("orcaWblFermiSummary")
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(summary)

        self._chart = QChart()
        self._chart.setObjectName("orcaWblChart")
        self._chart.setTitle(
            "ORCA linker-parameterized WBL transmission — HYPOTHESIS"
        )
        self._chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._chart.setMargins(QMargins(72, 28, 24, 32))
        self._chart.setTitleFont(QFont("Arial", 12))
        self._chart.legend().setFont(QFont("Arial", 10))
        self._chart.setPlotAreaBackgroundVisible(True)
        border = QPen(QColor("#000000"))
        border.setWidthF(1.0)
        self._chart.setPlotAreaBackgroundPen(border)

        self._alpha = None
        self._beta = None
        if not closed_shell:
            self._alpha = self._series(
                "Alpha",
                presentation.transmission_alpha,
                QColor("#1976d2"),
            )
            self._beta = self._series(
                "Beta",
                presentation.transmission_beta,
                QColor("#d32f2f"),
                dashed=True,
            )
        self._total = self._series(
            (
                "Total transmission (spin-degenerate)"
                if closed_shell
                else "Spin sum (Alpha + Beta)"
            ),
            presentation.transmission_total,
            QColor("#111111"),
            width=2.5,
        )
        self._fermi_reference = QLineSeries(self)
        self._fermi_reference.setName("E_F")
        reference_pen = QPen(QColor("#777777"))
        reference_pen.setStyle(Qt.PenStyle.DashLine)
        reference_pen.setWidthF(1.0)
        self._fermi_reference.setPen(reference_pen)
        self._data_series = tuple(
            series
            for series in (self._alpha, self._beta, self._total)
            if series is not None
        )
        for series in (*self._data_series, self._fermi_reference):
            self._chart.addSeries(series)

        self._x_axis = QValueAxis(self._chart)
        self._x_axis.setTitleText("Energy − E<sub>F</sub> (eV)")
        self._x_axis.setLabelFormat("%.3g")
        self._x_axis.setTickCount(9)
        self._x_axis.setLabelsFont(QFont("Arial", 10))
        self._x_axis.setTitleFont(QFont("Arial", 11))
        self._y_axis = QLogValueAxis(self._chart)
        self._y_axis.setTitleText("Transmission")
        self._y_axis.setBase(10.0)
        self._y_axis.setLabelsFont(QFont("Arial", 10))
        self._y_axis.setTitleFont(QFont("Arial", 11))
        self._chart.addAxis(self._x_axis, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(self._y_axis, Qt.AlignmentFlag.AlignLeft)
        for series in (*self._data_series, self._fermi_reference):
            series.attachAxis(self._x_axis)
            series.attachAxis(self._y_axis)

        self._default_x = (
            presentation.energy_relative_ev[0],
            presentation.energy_relative_ev[-1],
        )
        displayed = tuple(
            value
            for series_values in (
                presentation.transmission_total,
                *(
                    (
                        presentation.transmission_alpha,
                        presentation.transmission_beta,
                    )
                    if not closed_shell
                    else ()
                ),
            )
            for value in series_values
        )
        positive = tuple(value for value in displayed if value > 0.0)
        minimum = (
            10.0 ** floor(log10(min(positive))) if positive else 1.0e-12
        )
        maximum = max(max(positive, default=0.0) * 1.05, 1.0)
        if minimum >= maximum:
            minimum = maximum / 10.0
        self._default_y = (minimum, maximum)
        self._fermi_reference.replace(
            [QPointF(0.0, self._default_y[0]), QPointF(0.0, self._default_y[1])]
        )
        self.reset_view()

        self._chart_view = PowerOfTenLogChartView(
            self._chart,
            self._y_axis,
            self,
        )
        self._chart_view.setObjectName("orcaWblChartView")
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        layout.addWidget(self._chart_view, stretch=1)

        if closed_shell:
            limitation_text = (
                "Base-10 logarithmic display of unchanged raw values; non-positive "
                "samples are not replaced by an artificial floor. This restricted "
                "multiplicity-1 result was calculated once from the spatial orbitals "
                "and uses the conventional spin-degenerate G₀ = 2e²/h normalization; "
                "one orbital channel is therefore bounded by T = 1 rather than being "
                "duplicated as Alpha and Beta."
            )
        else:
            limitation_text = (
                "Base-10 logarithmic display of unchanged raw values; non-positive "
                "samples are not replaced by an artificial floor. Alpha and Beta are "
                "per-spin transmissions. The black curve is their raw spin sum "
                "Tα + Tβ in the e²/h-per-spin convention; when conductance is reported "
                "in G₀ = 2e²/h, G/G₀ = (Tα + Tβ)/2."
            )
        limitation = QLabel(
            limitation_text
            + " This independent-resonance linker-parameterized result is not an "
            "explicit Au–molecule–Au DFT-NEGF result.",
            self,
        )
        limitation.setObjectName("orcaWblLimitation")
        limitation.setWordWrap(True)
        layout.addWidget(limitation)

    @property
    def presentation(self) -> OrcaWblPresentation:
        return self._presentation

    def _series(
        self,
        name: str,
        values: tuple[float, ...],
        color: QColor,
        *,
        dashed: bool = False,
        width: float = 2.0,
    ) -> QLineSeries:
        series = QLineSeries(self)
        series.setName(name)
        series.replace(
            [
                QPointF(energy, value)
                for energy, value in zip(
                    self._presentation.energy_relative_ev,
                    values,
                    strict=True,
                )
            ]
        )
        pen = QPen(color)
        pen.setWidthF(width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        series.setPen(pen)
        return series

    def export_base_size(self) -> QSize:
        size = self._chart_view.size()
        return QSize(max(1, size.width()), max(1, size.height()))

    def capture_image(self, scale_factor: int = 1) -> QImage:
        return render_widget_image(
            self._chart_view,
            base_size=self.export_base_size(),
            scale_factor=scale_factor,
        )

    @Slot()
    def reset_view(self) -> None:
        self._x_axis.setRange(*self._default_x)
        self._y_axis.setRange(*self._default_y)

    @Slot()
    def open_view_settings(self) -> None:
        QMessageBox.information(
            self,
            "ORCA WBL View",
            "Multi-curve ORCA WBL styling is fixed in this version. Use Reset View "
            "to restore the persisted energy and transmission ranges.",
        )
