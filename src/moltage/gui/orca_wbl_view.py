"""Read-only alpha/beta/total presentation for one verified ORCA WBL result."""

from __future__ import annotations

from collections.abc import Iterable
from bisect import bisect_left
from dataclasses import replace
from html import escape
from math import floor, log10
from pathlib import Path

from PySide6.QtCore import QPointF, QSize, Qt, Slot
from PySide6.QtGui import QColor, QImage, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsPolygonItem,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from moltage.gui.transmission_canvas import (
    TransmissionCanvasView,
    TransmissionCurveDefinition,
    format_scientific_notation,
)
from moltage.gui.transmission_settings import PlotLineStyle, TickDirection
from moltage.gui.view_export import render_widget_image
from moltage.orca.wbl import WblSpinTreatment
from moltage.orca.wbl_artifacts import (
    OrcaWblPresentation,
    render_wbl_text_export,
)


_ALPHA_COLOR = "#c51b29"
_BETA_COLOR = "#2166ac"
_TOTAL_COLOR = "#111111"


class _WblReportCanvas(QWidget):
    """Keep the scientific plot and its report together on screen and in export."""

    def __init__(self, chart_view, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("orcaWblReportCanvas")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("white"))
        self.setPalette(palette)
        self.chart_view = chart_view
        chart_view.setParent(self)
        self.title = QLabel(title, self)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("color: #111; background: transparent; font: 14pt Arial;")
        self.sidebar = QLabel(self)
        self.sidebar.setObjectName("orcaWblReportDetails")
        self.sidebar.setTextFormat(Qt.TextFormat.RichText)
        self.sidebar.setWordWrap(True)
        self.sidebar.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sidebar.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sidebar.setStyleSheet(
            "color: #111; background: transparent; font: 10pt Arial; "
            "border-left: 1px solid #ddd; padding-left: 14px;"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width, height = self.width(), self.height()
        sidebar_width = min(340, max(250, round(width * 0.28)))
        body_height = max(100, height - 64)
        chart_width = max(180, min(width - sidebar_width - 16, body_height + 60))
        chart_height = min(body_height, max(140, chart_width - 60))
        left = max(0, (width - chart_width - sidebar_width) // 2)
        top = 54 + max(0, (body_height - chart_height) // 2)
        self.title.setGeometry(12, 8, width - 24, 42)
        self.chart_view.setGeometry(left, top, chart_width, chart_height)
        self.sidebar.setGeometry(left + chart_width, 62, sidebar_width - 12, height - 70)


class OrcaWblTransmissionView(TransmissionCanvasView):
    """Display all persisted spin curves without relabeling the WBL hypothesis."""

    def __init__(
        self,
        project_name: str,
        presentation: OrcaWblPresentation,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(project_name, str) or not project_name.strip():
            raise ValueError("ORCA WBL view requires a project name")
        if not isinstance(presentation, OrcaWblPresentation):
            raise TypeError("ORCA WBL view requires verified presentation data")
        closed_shell = (
            presentation.spin_treatment
            is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
        )
        energies = presentation.energy_relative_ev
        curves: list[TransmissionCurveDefinition] = []
        if not closed_shell:
            curves.append(
                _curve(
                    "orcaWblAlphaSeries",
                    "Alpha",
                    energies,
                    presentation.transmission_alpha,
                    _ALPHA_COLOR,
                    2.0,
                    PlotLineStyle.SOLID,
                )
            )
            curves.append(
                _curve(
                    "orcaWblBetaSeries",
                    "Beta",
                    energies,
                    presentation.transmission_beta,
                    _BETA_COLOR,
                    2.0,
                    PlotLineStyle.SOLID,
                )
            )
        curves.append(
            _curve(
                "orcaWblTotalSeries",
                (
                    "Total transmission (spin-degenerate)"
                    if closed_shell
                    else "Spin sum (Alpha + Beta)"
                ),
                energies,
                presentation.transmission_total,
                _TOTAL_COLOR,
                2.0 if closed_shell else 1.2,
                PlotLineStyle.SOLID if closed_shell else PlotLineStyle.DASH,
            )
        )
        total_index = len(curves) - 1
        fermi_point: tuple[float, float] | None = None
        fermi_annotation: str | None = None
        if presentation.t_total_at_fermi > 0.0:
            fermi_point = (0.0, presentation.t_total_at_fermi)
            fermi_annotation = (
                "T(E<sub>F</sub>) = "
                f"{format_scientific_notation(presentation.t_total_at_fermi)}"
            )
        super().__init__(
            tuple(curves),
            object_prefix="orcaWbl",
            energy_axis_title="Energy − E<sub>F</sub> (eV)",
            transmission_axis_title="Transmission",
            energy_range=(energies[0], energies[-1]),
            transmission_range=_default_transmission_range(
                curve.points for curve in curves
            ),
            chart_title="",
            chart_margins=(72, 28, 24, 32),
            fermi_point=fermi_point,
            fermi_annotation=fermi_annotation,
            fermi_curve_index=total_index,
            parent=parent,
        )
        self._presentation = presentation
        self.setObjectName("orcaWblTransmissionView")
        self._alpha = None if closed_shell else self._curve_series[0]
        self._beta = None if closed_shell else self._curve_series[1]
        self._total = self._curve_series[total_index]
        self._layout.removeWidget(self._chart_view)
        self._report_canvas = _WblReportCanvas(
            self._chart_view,
            f"{project_name.strip()}: "
            + ("spin-degenerate" if closed_shell else "spin-resolved")
            + " all-MO transmission",
            self,
        )
        self._layout.addWidget(self._report_canvas, stretch=1)
        self._orbital_markers = []
        if presentation.report is not None:
            for orbital in presentation.report.top_orbitals:
                marker = QGraphicsPolygonItem(self._chart)
                sign = 1 if orbital.spin == "alpha" else -1
                marker.setPolygon(QPolygonF([
                    QPointF(-5, -sign * 4), QPointF(5, -sign * 4),
                    QPointF(0, sign * 5),
                ]))
                marker.setPen(QPen(QColor("white"), 0.7))
                marker.setZValue(20)
                self._orbital_markers.append((orbital, marker))
        self._chart.plotAreaChanged.connect(self._update_orbital_markers)
        self._energy_axis.rangeChanged.connect(self._update_orbital_markers)
        self._transmission_axis.rangeChanged.connect(self._update_orbital_markers)
        initial = self.visual_settings
        self._apply_visual_settings(replace(
            initial,
            x_axis=replace(initial.x_axis, grid_color="#dddddd"),
            y_axis=replace(initial.y_axis, grid_color="#dddddd"),
            ticks=replace(initial.ticks, direction=TickDirection.INSIDE, minor_visible=True),
        ))
        self._canvas_size_custom = False

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
        self._layout.insertWidget(0, provenance)

        summary_text = (
            "T(E<sub>F</sub>) = "
            f"{format_scientific_notation(presentation.t_total_at_fermi)}  ·  "
            "closed-shell, spin-degenerate"
            if closed_shell
            else (
                "T<sub>α</sub>(E<sub>F</sub>) = "
                f"{format_scientific_notation(presentation.t_alpha_at_fermi)}  ·  "
                "T<sub>β</sub>(E<sub>F</sub>) = "
                f"{format_scientific_notation(presentation.t_beta_at_fermi)}  ·  "
                "T<sub>α+β</sub>(E<sub>F</sub>) = "
                f"{format_scientific_notation(presentation.t_total_at_fermi)}"
            )
        )
        summary = QLabel(summary_text, self)
        summary.setObjectName("orcaWblFermiSummary")
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._layout.insertWidget(1, summary)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self._export_data = QPushButton("Export Transmission Data…", self)
        self._export_data.setObjectName("orcaWblExportData")
        self._export_data.setToolTip(
            "Save the complete E − E_F and transmission grid as "
            "tab-delimited text for Igor Pro."
        )
        self._export_data.clicked.connect(self.export_transmission_data)
        export_row.addWidget(self._export_data)
        self._layout.insertLayout(2, export_row)

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
        self._layout.addWidget(limitation)

    @property
    def presentation(self) -> OrcaWblPresentation:
        return self._presentation

    def export_base_size(self) -> QSize:
        if self._canvas_size_custom:
            return super().export_base_size()
        if hasattr(self, "_report_canvas"):
            return self._report_canvas.size()
        return super().export_base_size()

    def capture_image(self, scale_factor: int = 1) -> QImage:
        requested = self.export_base_size()
        # Lay out small exports at a readable natural size before downsampling,
        # so neither the sidebar nor axis labels are clipped at small dimensions.
        ratio = max(1.0, 1000 / requested.width(), 760 / requested.height())
        natural = QSize(round(requested.width() * ratio), round(requested.height() * ratio))
        image = render_widget_image(
            self._report_canvas, base_size=natural, scale_factor=scale_factor,
        )
        if ratio > 1.0:
            image = image.scaled(
                requested * scale_factor,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return image

    def _apply_visual_settings(self, settings) -> None:
        super()._apply_visual_settings(settings)
        if not hasattr(self, "_report_canvas"):
            return
        palette = self._report_canvas.palette()
        palette.setColor(self._report_canvas.backgroundRole(), QColor(settings.canvas.background_color))
        self._report_canvas.setPalette(palette)
        self._chart.legend().hide()
        self._update_report()
        self._update_orbital_markers()

    def _apply_tick_settings(self, settings) -> None:
        super()._apply_tick_settings(settings)
        for axis, count, visual in (
            (self._energy_axis, settings.x_minor_tick_count, self.visual_settings.x_axis),
            (self._transmission_axis, settings.y_minor_tick_count, self.visual_settings.y_axis),
        ):
            axis.setMinorTickCount(count if settings.minor_visible else 0)
            axis.setMinorGridLineVisible(settings.minor_visible and visual.grid_visible)
            axis.setMinorGridLinePen(QPen(QColor("#eeeeee"), 0.5))

    def _update_report(self) -> None:
        parts = []
        for curve in self.visual_settings.curves:
            if curve.legend_visible:
                parts.append(
                    f'<span style="color:{curve.color}">━━</span> '
                    f'{escape(curve.legend_label)}<br>'
                )
        parts.append("<p><b>Model settings</b><br>Diagonal WBL · HYPOTHESIS</p>")
        report = self._presentation.report
        if report is None:
            reason = self._presentation.report_unavailable_reason or "Model / orbital detail unavailable in this result."
            parts.append(f"<p>{escape(reason)}</p>")
        else:
            parts.append(
                f"E<sub>F</sub> = {report.fermi_energy_ev:.4g} eV<br>"
                f"Γ<sub>0,L</sub> = {report.gamma0_left_ev:.4g} eV "
                f"({escape(report.left_status)})<br>"
                f"Γ<sub>0,R</sub> = {report.gamma0_right_ev:.4g} eV "
                f"({escape(report.right_status)})<br>"
            )
            counts = "; ".join(f"{spin.title()}: {count:,} MOs" for spin, count in report.orbital_counts)
            parts.append(f"{counts}<br>All orbitals included in each curve.")
            if "S_ALL_P_LEGACY" in (report.left_projection, report.right_projection):
                sides = "/".join(
                    side for side, mode in (("L", report.left_projection), ("R", report.right_projection))
                    if mode == "S_ALL_P_LEGACY"
                )
                parts.append(f"<br>{sides}: legacy S all-p weights (no direction).")
            parts.append(
                "<p><b>Largest contributions at E<sub>F</sub></b><br>"
                "Ranked by individual orbital term.<br>MO numbers are 1-based.</p>"
            )
            ranks = {}
            for orbital in report.top_orbitals:
                ranks[orbital.spin] = ranks.get(orbital.spin, 0) + 1
                series = self._series_for_spin(orbital.spin)
                symbol = {"alpha": "▼ α", "beta": "▲ β", "total": "▲"}[orbital.spin]
                parts.append(
                    f'<p><b style="color:{series.pen().color().name()}">'
                    f'{symbol}{ranks[orbital.spin]}: MO {orbital.mo_number}</b><br>'
                    f'ε − E<sub>F</sub> = {orbital.energy_relative_ev:+.3f} eV<br>'
                    f'T<sub>n</sub>(E<sub>F</sub>) = '
                    f'{format_scientific_notation(orbital.transmission_at_fermi)}</p>'
                )
            parts.append(
                "<p>Markers locate MO energies on the full curve; "
                "off-window orbitals remain listed.</p>"
            )
        self._report_canvas.sidebar.setText("".join(parts))

    def _series_for_spin(self, spin):
        return {"alpha": self._alpha, "beta": self._beta, "total": self._total}[spin]

    def _update_orbital_markers(self, *_args) -> None:
        for orbital, marker in self._orbital_markers:
            energy = orbital.energy_relative_ev
            series = self._series_for_spin(orbital.spin)
            values = {
                "alpha": self._presentation.transmission_alpha,
                "beta": self._presentation.transmission_beta,
                "total": self._presentation.transmission_total,
            }[orbital.spin]
            value = _plotted_value(self._presentation.energy_relative_ev, values, energy)
            visible = (
                value is not None
                and self._energy_axis.min() <= energy <= self._energy_axis.max()
                and self._transmission_axis.min() <= value <= self._transmission_axis.max()
            )
            marker.setVisible(visible)
            marker.setBrush(series.pen().color())
            if visible:
                marker.setPos(self._chart.mapToPosition(QPointF(energy, value), series))

    @Slot()
    def export_transmission_data(self) -> None:
        """Save the complete verified grid independently of plot styling."""

        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export ORCA WBL Transmission Data",
            "orca_wbl_transmission.txt",
            "Tab-delimited text (*.txt)",
        )
        if not selected:
            return
        destination = Path(selected)
        if not destination.suffix:
            destination = destination.with_suffix(".txt")
        elif destination.suffix.lower() != ".txt":
            QMessageBox.warning(
                self,
                "Invalid transmission data file",
                "ORCA WBL transmission data must be saved as a .txt file.",
            )
            return
        try:
            destination.write_bytes(render_wbl_text_export(self._presentation))
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Transmission data export failed",
                f"Unable to save the transmission data:\n{error}",
            )
            return
        QMessageBox.information(
            self,
            "Transmission data exported",
            f"Saved tab-delimited transmission data to:\n{destination}",
        )


def _curve(
    object_name: str,
    legend_label: str,
    energies: tuple[float, ...],
    values: tuple[float, ...],
    color: str,
    width: float,
    line_style: PlotLineStyle,
) -> TransmissionCurveDefinition:
    return TransmissionCurveDefinition(
        object_name=object_name,
        legend_label=legend_label,
        points=tuple(zip(energies, values, strict=True)),
        color=color,
        width=width,
        line_style=line_style,
        legend_visible=True,
    )


def _plotted_value(energies, values, energy):
    """Locate a marker on the displayed log-linear polyline, not a new model."""

    if not energies[0] <= energy <= energies[-1]:
        return None
    index = bisect_left(energies, energy)
    if energies[index] == energy:
        return values[index] if values[index] > 0.0 else None
    left, right = values[index - 1], values[index]
    if left <= 0.0 or right <= 0.0:
        return None
    fraction = (energy - energies[index - 1]) / (energies[index] - energies[index - 1])
    return 10 ** (log10(left) + fraction * (log10(right) - log10(left)))


def _default_transmission_range(
    curve_points: Iterable[tuple[tuple[float, float], ...]],
) -> tuple[float, float]:
    """Frame every displayed positive sample without inventing a floor value."""

    positive = tuple(
        value
        for points in curve_points
        for _energy, value in points
        if value > 0.0
    )
    minimum = 10.0 ** floor(log10(min(positive))) if positive else 1.0e-12
    maximum = max(max(positive, default=0.0) * 1.05, 1.0)
    if minimum >= maximum:
        minimum = maximum / 10.0
    return minimum, maximum
