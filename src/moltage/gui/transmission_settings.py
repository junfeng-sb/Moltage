"""Session-local presentation settings for one Transmission workspace."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class PlotLineStyle(StrEnum):
    SOLID = "Solid"
    DASH = "Dash"
    DOT = "Dot"
    DASH_DOT = "Dash-dot"


class TickDirection(StrEnum):
    INSIDE = "Inside"
    OUTSIDE = "Outside"


@dataclass(frozen=True, slots=True)
class AxisVisualSettings:
    minimum: float
    maximum: float
    title: str
    font_family: str
    label_point_size: float
    title_point_size: float
    line_color: str
    line_width: float
    grid_visible: bool
    grid_color: str
    grid_width: float
    grid_style: PlotLineStyle
    mirror_visible: bool

    def __post_init__(self) -> None:
        if not isfinite(self.minimum) or not isfinite(self.maximum):
            raise ValueError("axis range must be finite")
        if self.minimum >= self.maximum:
            raise ValueError("axis minimum must be less than maximum")
        if not self.font_family.strip():
            raise ValueError("axis font family must not be empty")
        if not 6.0 <= self.label_point_size <= 36.0:
            raise ValueError("axis label size must be between 6 and 36 pt")
        if not 6.0 <= self.title_point_size <= 36.0:
            raise ValueError("axis title size must be between 6 and 36 pt")
        _validate_color(self.line_color)
        _validate_color(self.grid_color)
        if not 0.5 <= self.line_width <= 8.0:
            raise ValueError("axis line width must be between 0.5 and 8 px")
        if not 0.5 <= self.grid_width <= 8.0:
            raise ValueError("grid width must be between 0.5 and 8 px")
        if not isinstance(self.grid_style, PlotLineStyle):
            raise TypeError("grid style must be a PlotLineStyle")


@dataclass(frozen=True, slots=True)
class TickVisualSettings:
    major_length: float
    major_width: float
    minor_visible: bool
    minor_length: float
    minor_width: float
    direction: TickDirection
    x_major_tick_count: int
    x_minor_tick_count: int
    y_minor_tick_count: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.major_length <= 30.0:
            raise ValueError("major tick length must be between 0 and 30 px")
        if not 0.5 <= self.major_width <= 8.0:
            raise ValueError("major tick width must be between 0.5 and 8 px")
        if not isinstance(self.minor_visible, bool):
            raise TypeError("minor tick visibility must be boolean")
        if not 0.0 <= self.minor_length <= 30.0:
            raise ValueError("minor tick length must be between 0 and 30 px")
        if not 0.5 <= self.minor_width <= 8.0:
            raise ValueError("minor tick width must be between 0.5 and 8 px")
        if not isinstance(self.direction, TickDirection):
            raise TypeError("tick direction must be a TickDirection")
        if not 2 <= self.x_major_tick_count <= 25:
            raise ValueError("X major tick count must be between 2 and 25")
        if not 0 <= self.x_minor_tick_count <= 20:
            raise ValueError("X minor tick count must be between 0 and 20")
        if not 0 <= self.y_minor_tick_count <= 20:
            raise ValueError("Y minor tick count must be between 0 and 20")


@dataclass(frozen=True, slots=True)
class CurveVisualSettings:
    color: str
    width: float
    line_style: PlotLineStyle
    legend_visible: bool
    legend_label: str

    def __post_init__(self) -> None:
        _validate_color(self.color)
        if not 0.5 <= self.width <= 12.0:
            raise ValueError("curve width must be between 0.5 and 12 px")
        if not isinstance(self.line_style, PlotLineStyle):
            raise TypeError("curve style must be a PlotLineStyle")
        if self.legend_visible and not self.legend_label.strip():
            raise ValueError("visible curve legend requires a label")


@dataclass(frozen=True, slots=True)
class CanvasVisualSettings:
    export_width: int
    export_height: int
    background_color: str
    plot_background_color: str
    frame_color: str
    frame_width: float
    left_margin: int
    top_margin: int
    right_margin: int
    bottom_margin: int

    def __post_init__(self) -> None:
        if not 400 <= self.export_width <= 4095:
            raise ValueError("canvas width must be between 400 and 4095 px")
        if not 300 <= self.export_height <= 4095:
            raise ValueError("canvas height must be between 300 and 4095 px")
        _validate_color(self.background_color)
        _validate_color(self.plot_background_color)
        _validate_color(self.frame_color)
        if not 0.5 <= self.frame_width <= 8.0:
            raise ValueError("frame width must be between 0.5 and 8 px")
        for value in (
            self.left_margin,
            self.top_margin,
            self.right_margin,
            self.bottom_margin,
        ):
            if not 0 <= value <= 240:
                raise ValueError("canvas margins must be between 0 and 240 px")


@dataclass(frozen=True, slots=True)
class TransmissionVisualSettings:
    x_axis: AxisVisualSettings
    y_axis: AxisVisualSettings
    ticks: TickVisualSettings
    curve: CurveVisualSettings
    canvas: CanvasVisualSettings


class _ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = ""
        self.clicked.connect(self._choose_color)
        self.set_color(color)

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        normalized = _validate_color(color)
        self._color = normalized
        foreground = _contrast_text_color(QColor(normalized))
        self.setText(normalized.upper())
        self.setStyleSheet(
            "QPushButton {"
            f"background-color: {normalized}; color: {foreground};"
            "border: 1px solid #6f7780; padding: 4px 8px;"
            "}"
        )
        self.color_changed.emit(normalized)

    @Slot()
    def _choose_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._color), self)
        if selected.isValid():
            self.set_color(selected.name(QColor.NameFormat.HexRgb))


class _AxisSettingsPage(QWidget):
    def __init__(
        self,
        settings: AxisVisualSettings,
        *,
        logarithmic: bool,
        object_prefix: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._logarithmic = logarithmic
        self._object_prefix = object_prefix
        layout = QVBoxLayout(self)

        range_group = QGroupBox("Range and title", self)
        range_form = QFormLayout(range_group)
        self.minimum = QLineEdit(f"{settings.minimum:.12g}", range_group)
        self.minimum.setObjectName(f"{object_prefix}Minimum")
        self.maximum = QLineEdit(f"{settings.maximum:.12g}", range_group)
        self.maximum.setObjectName(f"{object_prefix}Maximum")
        self.title = QLineEdit(settings.title, range_group)
        self.title.setObjectName(f"{object_prefix}Title")
        range_form.addRow("Minimum:", self.minimum)
        range_form.addRow("Maximum:", self.maximum)
        range_form.addRow("Axis title:", self.title)
        layout.addWidget(range_group)

        type_group = QGroupBox("Typography", self)
        type_form = QFormLayout(type_group)
        self.font_family = QFontComboBox(type_group)
        self.font_family.setObjectName(f"{object_prefix}FontFamily")
        self.font_family.setCurrentFont(QFont(settings.font_family))
        self.label_size = _double_spin(
            settings.label_point_size,
            6.0,
            36.0,
            0.5,
            f"{object_prefix}LabelSize",
            " pt",
            type_group,
        )
        self.title_size = _double_spin(
            settings.title_point_size,
            6.0,
            36.0,
            0.5,
            f"{object_prefix}TitleSize",
            " pt",
            type_group,
        )
        type_form.addRow("Font:", self.font_family)
        type_form.addRow("Tick-label size:", self.label_size)
        type_form.addRow("Title size:", self.title_size)
        layout.addWidget(type_group)

        line_group = QGroupBox("Axis and grid", self)
        line_grid = QGridLayout(line_group)
        self.line_color = _ColorButton(settings.line_color, line_group)
        self.line_color.setObjectName(f"{object_prefix}LineColor")
        self.line_width = _double_spin(
            settings.line_width,
            0.5,
            8.0,
            0.5,
            f"{object_prefix}LineWidth",
            " px",
            line_group,
        )
        self.grid_visible = QCheckBox("Show major grid lines", line_group)
        self.grid_visible.setObjectName(f"{object_prefix}GridVisible")
        self.grid_visible.setChecked(settings.grid_visible)
        self.grid_color = _ColorButton(settings.grid_color, line_group)
        self.grid_color.setObjectName(f"{object_prefix}GridColor")
        self.grid_width = _double_spin(
            settings.grid_width,
            0.5,
            8.0,
            0.5,
            f"{object_prefix}GridWidth",
            " px",
            line_group,
        )
        self.grid_style = _line_style_combo(
            settings.grid_style,
            f"{object_prefix}GridStyle",
            line_group,
        )
        self.mirror_visible = QCheckBox(
            "Show mirrored axis on the opposite side",
            line_group,
        )
        self.mirror_visible.setObjectName(f"{object_prefix}MirrorVisible")
        self.mirror_visible.setChecked(settings.mirror_visible)
        line_grid.addWidget(QLabel("Axis color:", line_group), 0, 0)
        line_grid.addWidget(self.line_color, 0, 1)
        line_grid.addWidget(QLabel("Axis width:", line_group), 0, 2)
        line_grid.addWidget(self.line_width, 0, 3)
        line_grid.addWidget(self.grid_visible, 1, 0, 1, 2)
        line_grid.addWidget(QLabel("Grid color:", line_group), 2, 0)
        line_grid.addWidget(self.grid_color, 2, 1)
        line_grid.addWidget(QLabel("Grid width:", line_group), 2, 2)
        line_grid.addWidget(self.grid_width, 2, 3)
        line_grid.addWidget(QLabel("Grid style:", line_group), 3, 0)
        line_grid.addWidget(self.grid_style, 3, 1)
        line_grid.addWidget(self.mirror_visible, 4, 0, 1, 4)
        layout.addWidget(line_group)
        layout.addStretch(1)

    def settings(self) -> AxisVisualSettings:
        try:
            minimum = float(self.minimum.text().strip())
            maximum = float(self.maximum.text().strip())
        except ValueError as error:
            raise ValueError("Axis minimum and maximum must be numbers.") from error
        if not isfinite(minimum) or not isfinite(maximum):
            raise ValueError("Axis minimum and maximum must be finite.")
        if self._logarithmic and minimum <= 0.0:
            raise ValueError("Logarithmic-axis minimum must be greater than zero.")
        return AxisVisualSettings(
            minimum=minimum,
            maximum=maximum,
            title=self.title.text().strip(),
            font_family=self.font_family.currentFont().family(),
            label_point_size=self.label_size.value(),
            title_point_size=self.title_size.value(),
            line_color=self.line_color.color,
            line_width=self.line_width.value(),
            grid_visible=self.grid_visible.isChecked(),
            grid_color=self.grid_color.color,
            grid_width=self.grid_width.value(),
            grid_style=PlotLineStyle(self.grid_style.currentData()),
            mirror_visible=self.mirror_visible.isChecked(),
        )


class _TickSettingsPage(QWidget):
    def __init__(
        self,
        settings: TickVisualSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        style_group = QGroupBox("Tick appearance", self)
        style_grid = QGridLayout(style_group)
        self.direction = QComboBox(style_group)
        self.direction.setObjectName("transmissionTickDirection")
        for direction in TickDirection:
            self.direction.addItem(direction.value, direction.value)
        self.direction.setCurrentIndex(
            self.direction.findData(settings.direction.value)
        )
        self.major_length = _double_spin(
            settings.major_length,
            0.0,
            30.0,
            0.5,
            "transmissionMajorTickLength",
            " px",
            style_group,
        )
        self.major_width = _double_spin(
            settings.major_width,
            0.5,
            8.0,
            0.5,
            "transmissionMajorTickWidth",
            " px",
            style_group,
        )
        self.minor_visible = QCheckBox("Show minor ticks", style_group)
        self.minor_visible.setObjectName("transmissionMinorTicksVisible")
        self.minor_visible.setChecked(settings.minor_visible)
        self.minor_length = _double_spin(
            settings.minor_length,
            0.0,
            30.0,
            0.5,
            "transmissionMinorTickLength",
            " px",
            style_group,
        )
        self.minor_width = _double_spin(
            settings.minor_width,
            0.5,
            8.0,
            0.5,
            "transmissionMinorTickWidth",
            " px",
            style_group,
        )
        style_grid.addWidget(QLabel("Direction:", style_group), 0, 0)
        style_grid.addWidget(self.direction, 0, 1)
        style_grid.addWidget(QLabel("Major length:", style_group), 1, 0)
        style_grid.addWidget(self.major_length, 1, 1)
        style_grid.addWidget(QLabel("Major width:", style_group), 1, 2)
        style_grid.addWidget(self.major_width, 1, 3)
        style_grid.addWidget(self.minor_visible, 2, 0, 1, 2)
        style_grid.addWidget(QLabel("Minor length:", style_group), 3, 0)
        style_grid.addWidget(self.minor_length, 3, 1)
        style_grid.addWidget(QLabel("Minor width:", style_group), 3, 2)
        style_grid.addWidget(self.minor_width, 3, 3)
        layout.addWidget(style_group)

        interval_group = QGroupBox("Tick intervals", self)
        interval_form = QFormLayout(interval_group)
        self.x_major_tick_count = _int_spin(
            settings.x_major_tick_count,
            2,
            25,
            "transmissionXMajorTickCount",
            "",
            interval_group,
        )
        self.x_minor_tick_count = _int_spin(
            settings.x_minor_tick_count,
            0,
            20,
            "transmissionXMinorTickCount",
            "",
            interval_group,
        )
        self.y_minor_tick_count = _int_spin(
            settings.y_minor_tick_count,
            0,
            20,
            "transmissionYMinorTickCount",
            "",
            interval_group,
        )
        interval_form.addRow("X major tick count:", self.x_major_tick_count)
        interval_form.addRow(
            "X minor ticks per major interval:",
            self.x_minor_tick_count,
        )
        interval_form.addRow(
            "Y minor divisions per decade:",
            self.y_minor_tick_count,
        )
        explanation = QLabel(
            "The logarithmic Y-axis major ticks remain at base-10 decades.",
            interval_group,
        )
        explanation.setWordWrap(True)
        interval_form.addRow(explanation)
        layout.addWidget(interval_group)
        layout.addStretch(1)

        self.minor_visible.toggled.connect(self._set_minor_controls_enabled)
        self._set_minor_controls_enabled(settings.minor_visible)

    @Slot(bool)
    def _set_minor_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.minor_length,
            self.minor_width,
            self.x_minor_tick_count,
            self.y_minor_tick_count,
        ):
            widget.setEnabled(enabled)

    def settings(self) -> TickVisualSettings:
        return TickVisualSettings(
            major_length=self.major_length.value(),
            major_width=self.major_width.value(),
            minor_visible=self.minor_visible.isChecked(),
            minor_length=self.minor_length.value(),
            minor_width=self.minor_width.value(),
            direction=TickDirection(self.direction.currentData()),
            x_major_tick_count=self.x_major_tick_count.value(),
            x_minor_tick_count=self.x_minor_tick_count.value(),
            y_minor_tick_count=self.y_minor_tick_count.value(),
        )


class _CurveSettingsPage(QWidget):
    def __init__(
        self,
        settings: CurveVisualSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self.color = _ColorButton(settings.color, self)
        self.color.setObjectName("transmissionCurveColor")
        self.width = _double_spin(
            settings.width,
            0.5,
            12.0,
            0.5,
            "transmissionCurveWidth",
            " px",
            self,
        )
        self.line_style = _line_style_combo(
            settings.line_style,
            "transmissionCurveLineStyle",
            self,
        )
        self.legend_visible = QCheckBox("Show curve legend", self)
        self.legend_visible.setObjectName("transmissionCurveLegendVisible")
        self.legend_visible.setChecked(settings.legend_visible)
        self.legend_label = QLineEdit(settings.legend_label, self)
        self.legend_label.setObjectName("transmissionCurveLegendLabel")
        self.legend_label.setEnabled(settings.legend_visible)
        self.legend_visible.toggled.connect(self.legend_label.setEnabled)
        form.addRow("Curve color:", self.color)
        form.addRow("Line width:", self.width)
        form.addRow("Line style:", self.line_style)
        form.addRow(self.legend_visible)
        form.addRow("Legend label:", self.legend_label)

    def settings(self) -> CurveVisualSettings:
        return CurveVisualSettings(
            color=self.color.color,
            width=self.width.value(),
            line_style=PlotLineStyle(self.line_style.currentData()),
            legend_visible=self.legend_visible.isChecked(),
            legend_label=self.legend_label.text().strip(),
        )


class _CanvasSettingsPage(QWidget):
    def __init__(
        self,
        settings: CanvasVisualSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        size_group = QGroupBox("Export canvas size", self)
        size_form = QFormLayout(size_group)
        self.width = _int_spin(
            settings.export_width,
            400,
            4095,
            "transmissionCanvasWidth",
            " px",
            size_group,
        )
        self.height = _int_spin(
            settings.export_height,
            300,
            4095,
            "transmissionCanvasHeight",
            " px",
            size_group,
        )
        size_form.addRow("Width:", self.width)
        size_form.addRow("Height:", self.height)
        layout.addWidget(size_group)

        color_group = QGroupBox("Canvas and frame", self)
        color_form = QFormLayout(color_group)
        self.background_color = _ColorButton(
            settings.background_color,
            color_group,
        )
        self.background_color.setObjectName("transmissionCanvasColor")
        self.plot_background_color = _ColorButton(
            settings.plot_background_color,
            color_group,
        )
        self.plot_background_color.setObjectName("transmissionPlotAreaColor")
        self.frame_color = _ColorButton(settings.frame_color, color_group)
        self.frame_color.setObjectName("transmissionFrameColor")
        self.frame_width = _double_spin(
            settings.frame_width,
            0.5,
            8.0,
            0.5,
            "transmissionFrameWidth",
            " px",
            color_group,
        )
        color_form.addRow("Canvas color:", self.background_color)
        color_form.addRow("Plot-area color:", self.plot_background_color)
        color_form.addRow("Frame color:", self.frame_color)
        color_form.addRow("Frame width:", self.frame_width)
        layout.addWidget(color_group)

        margins_group = QGroupBox("Plot margins", self)
        margins_layout = QGridLayout(margins_group)
        self.left_margin = _margin_spin(
            settings.left_margin,
            "transmissionLeftMargin",
            margins_group,
        )
        self.top_margin = _margin_spin(
            settings.top_margin,
            "transmissionTopMargin",
            margins_group,
        )
        self.right_margin = _margin_spin(
            settings.right_margin,
            "transmissionRightMargin",
            margins_group,
        )
        self.bottom_margin = _margin_spin(
            settings.bottom_margin,
            "transmissionBottomMargin",
            margins_group,
        )
        margins_layout.addWidget(QLabel("Left:", margins_group), 0, 0)
        margins_layout.addWidget(self.left_margin, 0, 1)
        margins_layout.addWidget(QLabel("Top:", margins_group), 0, 2)
        margins_layout.addWidget(self.top_margin, 0, 3)
        margins_layout.addWidget(QLabel("Right:", margins_group), 1, 0)
        margins_layout.addWidget(self.right_margin, 1, 1)
        margins_layout.addWidget(QLabel("Bottom:", margins_group), 1, 2)
        margins_layout.addWidget(self.bottom_margin, 1, 3)
        layout.addWidget(margins_group)
        layout.addStretch(1)

    def settings(self) -> CanvasVisualSettings:
        return CanvasVisualSettings(
            export_width=self.width.value(),
            export_height=self.height.value(),
            background_color=self.background_color.color,
            plot_background_color=self.plot_background_color.color,
            frame_color=self.frame_color.color,
            frame_width=self.frame_width.value(),
            left_margin=self.left_margin.value(),
            top_margin=self.top_margin.value(),
            right_margin=self.right_margin.value(),
            bottom_margin=self.bottom_margin.value(),
        )


class TransmissionSettingsDialog(QDialog):
    """Edit all plot presentation controls without touching parsed data."""

    settings_applied = Signal(object)

    _PAGE_INDEX = {"x": 0, "y": 1, "ticks": 2, "curve": 3, "canvas": 4}

    def __init__(
        self,
        settings: TransmissionVisualSettings,
        parent: QWidget | None = None,
        *,
        initial_page: str = "x",
    ) -> None:
        super().__init__(parent)
        if not isinstance(settings, TransmissionVisualSettings):
            raise TypeError("Transmission settings dialog requires plot settings")
        if initial_page not in self._PAGE_INDEX:
            raise ValueError("unknown Transmission settings page")
        self._initial_settings = settings
        self._selected_settings = settings
        self.setObjectName("transmissionSettingsDialog")
        self.setWindowTitle("Transmission View Settings")
        self.setModal(True)
        self.resize(620, 650)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("transmissionSettingsTabs")
        self.x_axis_page = _AxisSettingsPage(
            settings.x_axis,
            logarithmic=False,
            object_prefix="transmissionXAxis",
            parent=self.tabs,
        )
        self.y_axis_page = _AxisSettingsPage(
            settings.y_axis,
            logarithmic=True,
            object_prefix="transmissionYAxis",
            parent=self.tabs,
        )
        self.tick_page = _TickSettingsPage(settings.ticks, self.tabs)
        self.curve_page = _CurveSettingsPage(settings.curve, self.tabs)
        self.canvas_page = _CanvasSettingsPage(settings.canvas, self.tabs)
        self.tabs.addTab(self.x_axis_page, "X Axis")
        self.tabs.addTab(self.y_axis_page, "Y Axis")
        self.tabs.addTab(self.tick_page, "Ticks")
        self.tabs.addTab(self.curve_page, "Curve")
        self.tabs.addTab(self.canvas_page, "Canvas")
        self.tabs.setCurrentIndex(self._PAGE_INDEX[initial_page])
        layout.addWidget(self.tabs, 1)

        self.error = QLabel(self)
        self.error.setObjectName("transmissionSettingsError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(self)
        buttons.setObjectName("transmissionSettingsButtons")
        self.apply_button = buttons.addButton(
            "Apply",
            QDialogButtonBox.ButtonRole.ApplyRole,
        )
        self.apply_button.setObjectName("transmissionSettingsApply")
        self.ok_button = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = buttons.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button.clicked.connect(self._apply)
        self.ok_button.clicked.connect(self._accept)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_settings(self) -> TransmissionVisualSettings:
        return self._selected_settings

    @Slot()
    def _apply(self) -> None:
        selected = self._collect()
        if selected is None:
            return
        self._selected_settings = selected
        self.settings_applied.emit(selected)

    @Slot()
    def _accept(self) -> None:
        selected = self._collect()
        if selected is None:
            return
        self._selected_settings = selected
        self.settings_applied.emit(selected)
        self.accept()

    def _collect(self) -> TransmissionVisualSettings | None:
        try:
            selected = TransmissionVisualSettings(
                x_axis=self.x_axis_page.settings(),
                y_axis=self.y_axis_page.settings(),
                ticks=self.tick_page.settings(),
                curve=self.curve_page.settings(),
                canvas=self.canvas_page.settings(),
            )
        except (TypeError, ValueError) as error:
            self.error.setText(str(error))
            self.error.show()
            return None
        self.error.clear()
        self.error.hide()
        return selected


def _line_style_combo(
    selected: PlotLineStyle,
    object_name: str,
    parent: QWidget,
) -> QComboBox:
    combo = QComboBox(parent)
    combo.setObjectName(object_name)
    for style in PlotLineStyle:
        combo.addItem(style.value, style.value)
    combo.setCurrentIndex(combo.findData(selected.value))
    return combo


def _double_spin(
    value: float,
    minimum: float,
    maximum: float,
    step: float,
    object_name: str,
    suffix: str,
    parent: QWidget,
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox(parent)
    spin.setObjectName(object_name)
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(1)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


def _int_spin(
    value: int,
    minimum: int,
    maximum: int,
    object_name: str,
    suffix: str,
    parent: QWidget,
) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setObjectName(object_name)
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


def _margin_spin(value: int, object_name: str, parent: QWidget) -> QSpinBox:
    return _int_spin(value, 0, 240, object_name, " px", parent)


def _validate_color(value: str) -> str:
    color = QColor(value)
    if not color.isValid():
        raise ValueError(f"invalid plot color: {value!r}")
    return color.name(QColor.NameFormat.HexRgb)


def _contrast_text_color(background: QColor) -> str:
    luminance = (
        0.2126 * background.redF()
        + 0.7152 * background.greenF()
        + 0.0722 * background.blueF()
    )
    return "#111111" if luminance >= 0.55 else "#ffffff"
