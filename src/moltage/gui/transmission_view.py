"""AITRANSS transmission result view built on the shared chart presentation."""

from PySide6.QtCore import QSize, Qt, Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from moltage.aitranss.transmission import TransmissionResult
from moltage.gui.transmission_canvas import (
    TransmissionCanvasView,
    TransmissionCurveDefinition,
    chart_font,
    format_scientific_notation,
)
from moltage.gui.transmission_plot import (
    FermiTransmission,
    TransmissionPlotDefaults,
    transmission_at_fermi,
    transmission_plot_defaults,
)


class TransmissionView(TransmissionCanvasView):
    """Display one unchanged raw T(E) trace with the reviewed MVP presentation."""

    def __init__(
        self,
        project_name: str,
        job_id: str,
        result_filename: str,
        result: TransmissionResult,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(project_name, str) or not project_name.strip():
            raise ValueError("transmission view requires a project name")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("transmission view requires a Job ID")
        if not isinstance(result_filename, str) or not result_filename.strip():
            raise ValueError("transmission view requires a result filename")
        if not isinstance(result, TransmissionResult):
            raise TypeError("transmission view requires a TransmissionResult")

        defaults = transmission_plot_defaults(result)
        fermi = transmission_at_fermi(result)
        fermi_point: tuple[float, float] | None = None
        fermi_annotation: str | None = None
        if fermi.value is not None and fermi.value > 0.0:
            fermi_point = (0.0, fermi.value)
            fermi_annotation = (
                "T(E<sub>F</sub>) = "
                f"{format_scientific_notation(fermi.value)}"
            )
        super().__init__(
            (
                TransmissionCurveDefinition(
                    object_name="transmissionSeries",
                    legend_label="T(E)",
                    points=tuple(
                        (point.energy_relative_ev, point.transmission_per_spin)
                        for point in result.points
                    ),
                ),
            ),
            object_prefix="transmission",
            energy_axis_title="Energy − E<sub>F</sub> (eV)",
            transmission_axis_title="Transmission T(E)",
            energy_range=(defaults.energy_min_ev, defaults.energy_max_ev),
            transmission_range=(
                defaults.transmission_min,
                defaults.transmission_max,
            ),
            fermi_point=fermi_point,
            fermi_annotation=fermi_annotation,
            parent=parent,
        )
        self._result = result
        self._defaults = defaults
        self._fermi = fermi
        self._series = self._curve_series[0]
        self.setObjectName("transmissionView")

        provenance = QLabel(
            f"Project: {project_name.strip()}  ·  Job {job_id.strip()}  ·  "
            f"{result_filename.strip()}  ·  {len(result.points):,} points",
            self,
        )
        provenance.setObjectName("transmissionProvenance")
        provenance.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._layout.insertWidget(0, provenance)

        self._fermi_status = QLabel(_fermi_status_text(self._fermi), self)
        self._fermi_status.setObjectName("transmissionFermiStatus")
        self._fermi_status.setTextFormat(Qt.TextFormat.RichText)
        self._fermi_status.setWordWrap(True)
        self._layout.addWidget(self._fermi_status)

        note = QLabel(
            "Base-10 logarithmic display of unchanged raw T(E) values. "
            "Non-positive samples are not replaced with an artificial floor.",
            self,
        )
        note.setObjectName("transmissionPlotNote")
        note.setWordWrap(True)
        self._layout.addWidget(note)

    @property
    def result(self) -> TransmissionResult:
        return self._result

    @property
    def plot_defaults(self) -> TransmissionPlotDefaults:
        return self._defaults

    @property
    def fermi_transmission(self) -> FermiTransmission:
        return self._fermi


class TransmissionWindow(QDialog):
    """Compatibility shell around the canonical reusable Transmission view."""

    def __init__(
        self,
        project_name: str,
        job_id: str,
        result_filename: str,
        result: TransmissionResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setFont(chart_font(self.font()))
        self.setObjectName("transmissionWindow")
        self.setWindowTitle(f"Transmission — {project_name.strip()}")
        self.setModal(False)
        self.setMinimumSize(760, 520)
        self.resize(900, 620)

        layout = QVBoxLayout(self)
        self._content = TransmissionView(
            project_name,
            job_id,
            result_filename,
            result,
            self,
        )
        layout.addWidget(self._content, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.setObjectName("transmissionButtons")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Keep the reviewed test/diagnostic handles on this thin legacy shell.
        for name in (
            "_series",
            "_reference_series",
            "_fermi_marker",
            "_probe_marker",
            "_chart",
            "_energy_axis",
            "_transmission_axis",
            "_energy_mirror_axis",
            "_transmission_mirror_axis",
            "_chart_view",
            "_fermi_status",
        ):
            setattr(self, name, getattr(self._content, name))

    @property
    def result(self) -> TransmissionResult:
        return self._content.result

    @property
    def plot_defaults(self) -> TransmissionPlotDefaults:
        return self._content.plot_defaults

    @property
    def fermi_transmission(self) -> FermiTransmission:
        return self._content.fermi_transmission

    @Slot()
    def reset_view(self) -> None:
        self._content.reset_view()

    def open_view_settings(self, initial_page: str = "x") -> None:
        self._content.open_view_settings(initial_page)

    def export_base_size(self) -> QSize:
        return self._content.export_base_size()

    def capture_image(self, scale_factor: int = 1) -> QImage:
        return self._content.capture_image(scale_factor)


def _fermi_status_text(value: FermiTransmission) -> str:
    if value.value is None:
        return (
            "T(E<sub>F</sub>) unavailable: the parsed Energy − E<sub>F</sub> "
            "samples do not bracket "
            "0 eV; no extrapolation is applied."
        )
    annotation = format_scientific_notation(value.value)
    if value.value <= 0.0:
        return (
            f"T(E<sub>F</sub>) = {annotation}; this non-positive raw value "
            "cannot be marked on a logarithmic Y axis."
        )
    if value.exact:
        return (
            f"T(E<sub>F</sub>) = {annotation} from the exact "
            "Energy − E<sub>F</sub> = 0 sample."
        )
    return (
        f"T(E<sub>F</sub>) = {annotation}, linearly interpolated in raw T(E) "
        "between the nearest bracketing samples."
    )
