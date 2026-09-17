"""Centralized desktop theme definitions and application-level activation."""

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QStyleFactory

from moltage.app.package_resources import application_resource_path
from moltage.visualization.view_preferences import (
    ATOM_HIGHLIGHT_COLORS_PROPERTY,
    AtomHighlightColors,
)


DEFAULT_THEME_ID = "future_light"
THEME_ID_PROPERTY = "aimsTransportThemeId"
_SEMANTIC_COLOR_ICON_FILENAMES = frozenset(
    {
        "measure_angle.svg",
        "rotate_bond.svg",
        "delete_atom.svg",
        "replace_atom.svg",
    }
)


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """Named UI colors shared by palettes and style sheets."""

    application_background: str
    workspace_background: str
    panel_background: str
    raised_background: str
    border: str
    strong_border: str
    primary_text: str
    secondary_text: str
    accent: str
    accent_dark: str
    accent_text: str
    hover_background: str
    selected_background: str
    disabled_background: str
    disabled_text: str
    warning_text: str
    critical_text: str
    success: str
    atom_hover: str
    atom_group_primary: str
    atom_group_secondary: str


@dataclass(frozen=True, slots=True)
class ThemeMetrics:
    """Small reusable geometry tokens for desktop widgets."""

    panel_radius: int
    control_radius: int
    compact_radius: int
    control_padding_vertical: int
    control_padding_horizontal: int
    filled_active_tab: bool


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    """One complete, registrable Qt desktop theme."""

    theme_id: str
    display_name: str
    preferred_fonts: tuple[str, ...]
    qt_style: str
    is_dark: bool
    colors: ThemeColors
    metrics: ThemeMetrics
    stylesheet: str


class ThemeManager:
    """Register themes and apply one definition to a QApplication."""

    def __init__(
        self,
        themes: Iterable[ThemeDefinition],
        default_theme_id: str,
    ) -> None:
        self._themes: dict[str, ThemeDefinition] = {}
        self._default_theme_id = default_theme_id
        self._active_theme_id: str | None = None
        for theme in themes:
            self.register(theme)
        if default_theme_id not in self._themes:
            raise ValueError("default theme must be registered")

    @property
    def default_theme_id(self) -> str:
        return self._default_theme_id

    @property
    def active_theme_id(self) -> str | None:
        return self._active_theme_id

    @property
    def themes(self) -> tuple[ThemeDefinition, ...]:
        return tuple(self._themes.values())

    def register(self, theme: ThemeDefinition) -> None:
        if not isinstance(theme, ThemeDefinition):
            raise TypeError("registered theme must be a ThemeDefinition")
        if not theme.theme_id or not theme.theme_id.isidentifier():
            raise ValueError("theme ID must be a non-empty identifier")
        if theme.theme_id in self._themes:
            raise ValueError(f"theme is already registered: {theme.theme_id}")
        self._themes[theme.theme_id] = theme

    def definition(self, theme_id: str) -> ThemeDefinition:
        try:
            return self._themes[theme_id]
        except KeyError as error:
            raise ValueError(f"unknown UI theme: {theme_id}") from error

    def apply(
        self,
        application: QApplication,
        theme_id: str | None = None,
    ) -> ThemeDefinition:
        if not isinstance(application, QApplication):
            raise TypeError("UI theme requires a QApplication")
        theme = self.definition(theme_id or self._default_theme_id)
        style = QStyleFactory.create(theme.qt_style)
        if style is None:
            raise RuntimeError(f"required Qt style is unavailable: {theme.qt_style}")

        application.setProperty(
            ATOM_HIGHLIGHT_COLORS_PROPERTY,
            AtomHighlightColors(
                hover=_rgb_tuple(theme.colors.atom_hover),
                primary_group=_rgb_tuple(theme.colors.atom_group_primary),
                secondary_group=_rgb_tuple(theme.colors.atom_group_secondary),
            ),
        )
        application.setStyle(style)
        application.setPalette(_build_palette(theme.colors))
        _apply_preferred_font(application, theme.preferred_fonts)
        application.setStyleSheet(theme.stylesheet)
        application.setProperty(THEME_ID_PROPERTY, theme.theme_id)
        self._active_theme_id = theme.theme_id
        return theme


FUTURE_LIGHT_COLORS = ThemeColors(
    application_background="#F5F8FC",
    workspace_background="#FAFCFF",
    panel_background="#F0F5FA",
    raised_background="#FFFFFF",
    border="#D6E0EA",
    strong_border="#B9CAD9",
    primary_text="#243445",
    secondary_text="#60758A",
    accent="#3B91D4",
    accent_dark="#2878B5",
    accent_text="#FFFFFF",
    hover_background="#E7F2FA",
    selected_background="#D9ECF8",
    disabled_background="#EDF2F6",
    disabled_text="#91A1B0",
    warning_text="#8A5A00",
    critical_text="#9A3D2A",
    success="#2FAF73",
    atom_hover="#0077FF",
    atom_group_primary="#E600D7",
    atom_group_secondary="#009FC2",
)

FUTURE_LIGHT_METRICS = ThemeMetrics(
    panel_radius=8,
    control_radius=5,
    compact_radius=4,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=True,
)

ARCTIC_CIRCUIT_COLORS = ThemeColors(
    application_background="#F4FBFD",
    workspace_background="#FFFFFF",
    panel_background="#EAF6F8",
    raised_background="#FFFFFF",
    border="#C6DDE2",
    strong_border="#8EBEC7",
    primary_text="#17343A",
    secondary_text="#58777D",
    accent="#00A7B7",
    accent_dark="#007E8B",
    accent_text="#FFFFFF",
    hover_background="#DDF5F7",
    selected_background="#C7EEF2",
    disabled_background="#E6F0F2",
    disabled_text="#7E969B",
    warning_text="#845900",
    critical_text="#A83D42",
    success="#179B72",
    atom_hover="#FF9D00",
    atom_group_primary="#F500A4",
    atom_group_secondary="#00AFC7",
)

ARCTIC_CIRCUIT_METRICS = ThemeMetrics(
    panel_radius=3,
    control_radius=3,
    compact_radius=2,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=False,
)

PEARL_QUANTUM_COLORS = ThemeColors(
    application_background="#F8F6FB",
    workspace_background="#FEFCFF",
    panel_background="#F1EDF7",
    raised_background="#FFFFFF",
    border="#DDD5E8",
    strong_border="#BFB1D1",
    primary_text="#312B3C",
    secondary_text="#746A82",
    accent="#7457C5",
    accent_dark="#5A3EA5",
    accent_text="#FFFFFF",
    hover_background="#EFE9FA",
    selected_background="#E5DAF8",
    disabled_background="#EEEAF2",
    disabled_text="#9A91A5",
    warning_text="#825600",
    critical_text="#A33A50",
    success="#299B72",
    atom_hover="#7038F0",
    atom_group_primary="#E500B5",
    atom_group_secondary="#009DDE",
)

PEARL_QUANTUM_METRICS = ThemeMetrics(
    panel_radius=11,
    control_radius=7,
    compact_radius=7,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=True,
)

AURORA_GLASS_COLORS = ThemeColors(
    application_background="#F2FAF8",
    workspace_background="#FBFFFE",
    panel_background="#E7F4F1",
    raised_background="#FFFFFF",
    border="#CBE1DC",
    strong_border="#98C5BC",
    primary_text="#183A35",
    secondary_text="#5C7D76",
    accent="#1CA88A",
    accent_dark="#117B68",
    accent_text="#FFFFFF",
    hover_background="#DCF3ED",
    selected_background="#CDECE4",
    disabled_background="#E4EFEC",
    disabled_text="#839C96",
    warning_text="#835800",
    critical_text="#A63E43",
    success="#15996E",
    atom_hover="#E8A900",
    atom_group_primary="#F0008C",
    atom_group_secondary="#00A488",
)

AURORA_GLASS_METRICS = ThemeMetrics(
    panel_radius=13,
    control_radius=8,
    compact_radius=8,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=True,
)

SOLAR_PAPER_COLORS = ThemeColors(
    application_background="#FAF7EF",
    workspace_background="#FFFDF8",
    panel_background="#F3EEDF",
    raised_background="#FFFEFA",
    border="#DED5BF",
    strong_border="#BFAF8D",
    primary_text="#3B3528",
    secondary_text="#786E59",
    accent="#D58216",
    accent_dark="#A65E09",
    accent_text="#FFFFFF",
    hover_background="#F7EBD1",
    selected_background="#F2DFC0",
    disabled_background="#EEE9DD",
    disabled_text="#948A75",
    warning_text="#865000",
    critical_text="#9F3D35",
    success="#368D60",
    atom_hover="#F06A00",
    atom_group_primary="#D900A9",
    atom_group_secondary="#007FE8",
)

SOLAR_PAPER_METRICS = ThemeMetrics(
    panel_radius=4,
    control_radius=3,
    compact_radius=2,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=False,
)

EVENT_HORIZON_COLORS = ThemeColors(
    application_background="#0B1016",
    workspace_background="#0F151D",
    panel_background="#121B24",
    raised_background="#17222C",
    border="#263645",
    strong_border="#3A5366",
    primary_text="#E7F2FA",
    secondary_text="#8CA5B7",
    accent="#22C7E5",
    accent_dark="#15A3C0",
    accent_text="#071116",
    hover_background="#182A36",
    selected_background="#173643",
    disabled_background="#1B252E",
    disabled_text="#607789",
    warning_text="#E8AE3C",
    critical_text="#F06B66",
    success="#35C98A",
    atom_hover="#72FFE7",
    atom_group_primary="#FF4FD8",
    atom_group_secondary="#35CFFF",
)

EVENT_HORIZON_METRICS = ThemeMetrics(
    panel_radius=7,
    control_radius=5,
    compact_radius=4,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=False,
)

EMBER_LAB_COLORS = ThemeColors(
    application_background="#171311",
    workspace_background="#1C1714",
    panel_background="#241D19",
    raised_background="#2C231D",
    border="#40342C",
    strong_border="#665044",
    primary_text="#F4EEE8",
    secondary_text="#B4A194",
    accent="#F09032",
    accent_dark="#C56A1D",
    accent_text="#1A0E05",
    hover_background="#35271F",
    selected_background="#493020",
    disabled_background="#302925",
    disabled_text="#796A60",
    warning_text="#F0B040",
    critical_text="#F07468",
    success="#55C989",
    atom_hover="#FFD166",
    atom_group_primary="#FF4FA3",
    atom_group_secondary="#25D9FF",
)

EMBER_LAB_METRICS = ThemeMetrics(
    panel_radius=4,
    control_radius=3,
    compact_radius=2,
    control_padding_vertical=5,
    control_padding_horizontal=9,
    filled_active_tab=False,
)


def _build_stylesheet(
    colors: ThemeColors,
    metrics: ThemeMetrics,
    *,
    is_dark: bool,
) -> str:
    panel_radius = metrics.panel_radius
    control_radius = metrics.control_radius
    compact_radius = metrics.compact_radius
    vertical_padding = metrics.control_padding_vertical
    horizontal_padding = metrics.control_padding_horizontal
    tab_close_icon = _theme_asset_url(
        "tab_close_on_dark.svg" if is_dark else "tab_close.svg"
    )
    combo_down_icon = _theme_asset_url(
        "combo_down_on_dark.svg" if is_dark else "combo_down.svg"
    )
    selected_tab_background = (
        colors.workspace_background
        if metrics.filled_active_tab
        else "transparent"
    )
    selected_tab_border = (
        colors.border if metrics.filled_active_tab else "transparent"
    )
    return f"""
QWidget {{
    color: {colors.primary_text};
}}

QMainWindow, QDialog, QMessageBox {{
    background-color: {colors.application_background};
}}

QMenuBar {{
    background-color: {colors.application_background};
    border: 0;
    border-bottom: 1px solid {colors.border};
    padding: 3px 12px;
    spacing: 3px;
}}

QMenuBar::item {{
    background: transparent;
    color: {colors.primary_text};
    border-radius: {compact_radius}px;
    padding: 6px 10px;
}}

QMenuBar::item:selected {{
    background-color: {colors.hover_background};
    color: {colors.accent_dark};
}}

QMenuBar::item:pressed {{
    background-color: {colors.selected_background};
}}

QWidget#menuCornerControls {{
    background: transparent;
}}

QToolButton#themeButton,
QToolButton#updateLogButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {control_radius}px;
    padding: 3px;
}}

QToolButton#themeButton:hover,
QToolButton#updateLogButton:hover {{
    background-color: {colors.hover_background};
    border-color: {colors.border};
}}

QToolButton#themeButton:pressed,
QToolButton#themeButton:open,
QToolButton#updateLogButton:pressed,
QToolButton#updateLogButton:open {{
    background-color: {colors.selected_background};
    border-color: {colors.accent};
}}

QMenu {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.border};
    border-radius: {control_radius}px;
    padding: 5px;
}}

QMenu::item {{
    border-radius: {compact_radius}px;
    padding: 6px 28px 6px 10px;
}}

QMenu::item:selected {{
    background-color: {colors.hover_background};
    color: {colors.accent_dark};
}}

QMenu::item:disabled {{
    color: {colors.disabled_text};
}}

QMenu#themeMenu::item:disabled {{
    color: {colors.secondary_text};
    font-weight: 600;
}}

QWidget#themePanel {{
    background-color: {colors.raised_background};
}}

QLabel#themeLightHeader,
QLabel#themeDarkHeader {{
    color: {colors.primary_text};
    font-weight: 600;
    padding: 1px 6px 3px 6px;
}}

QFrame#themeLightHeaderDivider,
QFrame#themeDarkHeaderDivider {{
    background-color: {colors.border};
    border: 0;
    min-height: 1px;
    max-height: 1px;
}}

QFrame#themeColumnDivider {{
    background-color: {colors.border};
    border: 0;
    min-width: 1px;
    max-width: 1px;
}}

QRadioButton[themeChoice="true"] {{
    background-color: transparent;
    color: {colors.primary_text};
    border: 1px solid transparent;
    border-radius: {compact_radius}px;
    spacing: 7px;
    padding: 6px 8px;
}}

QRadioButton[themeChoice="true"]:hover {{
    background-color: {colors.hover_background};
    border-color: {colors.border};
}}

QRadioButton[themeChoice="true"]:checked {{
    background-color: {colors.selected_background};
    color: {colors.accent_dark};
}}

QRadioButton[themeChoice="true"]::indicator {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.strong_border};
    border-radius: 7px;
    width: 14px;
    height: 14px;
}}

QRadioButton[themeChoice="true"]::indicator:checked {{
    border: 1px solid {colors.accent};
    background: qradialgradient(
        cx: 0.5, cy: 0.5, radius: 0.5,
        fx: 0.5, fy: 0.5,
        stop: 0.00 {colors.accent},
        stop: 0.36 {colors.accent},
        stop: 0.38 {colors.raised_background},
        stop: 1.00 {colors.raised_background}
    );
}}

QMenu::separator {{
    background: {colors.border};
    height: 1px;
    margin: 5px 8px;
}}

QToolBar#mainToolbar {{
    background-color: {colors.application_background};
    border: 0;
    border-bottom: 1px solid {colors.border};
    spacing: 4px;
    padding: 6px 12px;
}}

QToolBar#mainToolbar QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {control_radius}px;
    margin: 0 1px;
    padding: 5px;
}}

QToolBar#mainToolbar QToolButton:hover {{
    background-color: {colors.hover_background};
    border-color: {colors.border};
}}

QToolBar#mainToolbar QToolButton:pressed {{
    background-color: {colors.selected_background};
}}

QToolBar#mainToolbar QToolButton:checked {{
    background-color: {colors.selected_background};
    border-color: {colors.accent};
}}

QToolBar#mainToolbar QToolButton:disabled {{
    background-color: transparent;
    border-color: transparent;
    color: {colors.disabled_text};
}}

QToolBar#mainToolbar::separator {{
    background-color: {colors.border};
    width: 1px;
    margin: 5px 6px;
}}

QMainWindow::separator {{
    background-color: {colors.application_background};
    width: 7px;
    height: 7px;
}}

QMainWindow::separator:hover {{
    background-color: {colors.selected_background};
}}

QDockWidget#auToolDock {{
    background-color: {colors.panel_background};
    color: {colors.primary_text};
}}

QDockWidget#auToolDock::title {{
    background-color: {colors.panel_background};
    border: 1px solid {colors.border};
    border-bottom: 0;
    border-top-left-radius: {panel_radius}px;
    border-top-right-radius: {panel_radius}px;
    font-weight: 600;
    padding: 8px 10px;
    text-align: left;
}}

QDockWidget#auToolDock::close-button,
QDockWidget#auToolDock::float-button {{
    background: transparent;
    border: 0;
    border-radius: {compact_radius}px;
    padding: 2px;
}}

QDockWidget#auToolDock::close-button:hover,
QDockWidget#auToolDock::float-button:hover {{
    background-color: {colors.hover_background};
}}

QStackedWidget#electrodeBuilderStack {{
    background-color: {colors.panel_background};
    border: 1px solid {colors.border};
    border-top: 0;
    border-bottom-left-radius: {panel_radius}px;
    border-bottom-right-radius: {panel_radius}px;
}}

QTabWidget#workspaceTabs {{
    background-color: {colors.application_background};
}}

QTabWidget#workspaceTabs::pane {{
    background-color: {colors.workspace_background};
    border: 1px solid {colors.border};
    border-radius: {panel_radius}px;
    top: -1px;
}}

QTabWidget#workspaceTabs QTabBar::tab {{
    background-color: transparent;
    color: {colors.secondary_text};
    border: 1px solid transparent;
    border-bottom: 0;
    border-top-left-radius: {control_radius}px;
    border-top-right-radius: {control_radius}px;
    min-width: 82px;
    padding: 7px 13px;
    margin-right: 2px;
}}

QTabWidget#workspaceTabs QTabBar::tab:hover {{
    background-color: {colors.hover_background};
    color: {colors.primary_text};
}}

QTabWidget#workspaceTabs QTabBar::tab:selected {{
    background-color: {selected_tab_background};
    color: {colors.primary_text};
    border-color: {selected_tab_border};
    border-bottom: 2px solid {colors.accent};
    font-weight: 600;
}}

QTabWidget#workspaceTabs QTabBar::tab:!selected {{
    margin-top: 2px;
}}

QTabWidget#workspaceTabs QTabBar::close-button {{
    background: transparent;
    border: 0;
    image: url("{tab_close_icon}");
    margin-left: 5px;
    width: 12px;
    height: 12px;
}}

QTabWidget#workspaceTabs QTabBar::close-button:hover {{
    background-color: {colors.hover_background};
    border-radius: {compact_radius}px;
}}

QWidget#geometryWorkspace,
QWidget#transmissionView {{
    background-color: {colors.workspace_background};
}}

QFrame#measurementPanel,
QFrame#sixLayerElectrodeSection,
QFrame#anchorSiteCard {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.border};
    border-radius: {control_radius}px;
}}

QWidget#statusPanel {{
    background-color: transparent;
    border-top: 1px solid {colors.border};
}}

QLabel#measurementEmptyState,
QLabel#operationOutput,
QLabel#transmissionProvenance,
QLabel#transmissionPlotNote,
QLabel#anchorSitesHeading,
QLabel[uiTone="secondary"] {{
    color: {colors.secondary_text};
}}

QGroupBox {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.border};
    border-radius: {panel_radius}px;
    font-weight: 600;
    margin-top: 13px;
    padding: 9px 8px 8px 8px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    color: {colors.primary_text};
    left: 10px;
    padding: 0 4px;
}}

QPushButton {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.border};
    border-radius: {control_radius}px;
    padding: {vertical_padding}px {horizontal_padding}px;
}}

QPushButton:hover {{
    background-color: {colors.hover_background};
    border-color: {colors.strong_border};
}}

QPushButton:pressed,
QPushButton:checked {{
    background-color: {colors.selected_background};
    border-color: {colors.accent};
}}

QPushButton:focus {{
    border-color: {colors.accent};
}}

QPushButton:disabled {{
    background-color: {colors.disabled_background};
    border-color: {colors.border};
    color: {colors.disabled_text};
}}

QPushButton:default,
QPushButton#confirmRealSubmission,
QPushButton#submitRealStep4,
QPushButton#submitStep3Retry,
QPushButton#submitStep4ExplicitRetry {{
    background-color: {colors.accent};
    border-color: {colors.accent_dark};
    color: {colors.accent_text};
    font-weight: 600;
}}

QPushButton:default:hover,
QPushButton#confirmRealSubmission:hover,
QPushButton#submitRealStep4:hover,
QPushButton#submitStep3Retry:hover,
QPushButton#submitStep4ExplicitRetry:hover {{
    background-color: {colors.accent_dark};
}}

QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QTextEdit,
QPlainTextEdit {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.border};
    border-radius: {control_radius}px;
    padding: 4px 7px;
    selection-background-color: {colors.selected_background};
    selection-color: {colors.primary_text};
}}

QLineEdit:hover,
QComboBox:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QTextEdit:hover,
QPlainTextEdit:hover {{
    border-color: {colors.strong_border};
}}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QTextEdit:focus,
QPlainTextEdit:focus {{
    border-color: {colors.accent};
}}

QLineEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled {{
    background-color: {colors.disabled_background};
    color: {colors.disabled_text};
}}

QComboBox::drop-down {{
    border: 0;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: url("{combo_down_icon}");
    width: 12px;
    height: 12px;
}}

QAbstractItemView {{
    background-color: {colors.raised_background};
    alternate-background-color: {colors.application_background};
    border: 1px solid {colors.border};
    border-radius: {control_radius}px;
    outline: 0;
    selection-background-color: {colors.selected_background};
    selection-color: {colors.primary_text};
}}

QAbstractItemView::item {{
    padding: 4px;
}}

QAbstractItemView::item:hover {{
    background-color: {colors.hover_background};
}}

QHeaderView::section {{
    background-color: {colors.panel_background};
    color: {colors.primary_text};
    border: 0;
    border-right: 1px solid {colors.border};
    border-bottom: 1px solid {colors.border};
    padding: 5px 7px;
}}

QScrollArea {{
    background: transparent;
    border: 0;
}}

QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {colors.strong_border};
    border-radius: 3px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {colors.secondary_text};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
    border: 0;
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {colors.strong_border};
    border-radius: 3px;
    min-width: 24px;
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
    border: 0;
    width: 0;
}}

QProgressBar {{
    background-color: {colors.disabled_background};
    border: 0;
    border-radius: {compact_radius - 1}px;
    min-height: 6px;
    max-height: 6px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {colors.accent};
    border-radius: {compact_radius - 1}px;
}}

QToolTip {{
    background-color: {colors.raised_background};
    color: {colors.primary_text};
    border: 1px solid {colors.border};
    padding: 4px 6px;
}}

QLabel[uiTone="warning"] {{
    color: {colors.warning_text};
}}

QLabel[uiTone="critical"] {{
    color: {colors.critical_text};
    font-weight: 600;
}}

QFrame#torsionAngleEditor {{
    background-color: {colors.raised_background};
    border: 1px solid {colors.accent};
    border-radius: {compact_radius}px;
}}

QLineEdit#torsionAngleInput {{
    background: transparent;
    border: 0;
    color: {colors.primary_text};
    font-weight: normal;
    padding: 0 3px;
}}

QLabel#torsionAngleDegreeLabel {{
    background: transparent;
    border: 0;
    color: {colors.primary_text};
    font-weight: normal;
}}
""".strip()


def _theme_asset_url(filename: str) -> str:
    path = application_resource_path("icons", filename)
    if not path.is_file():
        raise RuntimeError(f"required theme asset is missing: {path}")
    return path.as_posix()


def _theme_definition(
    theme_id: str,
    display_name: str,
    colors: ThemeColors,
    metrics: ThemeMetrics,
    *,
    is_dark: bool,
) -> ThemeDefinition:
    return ThemeDefinition(
        theme_id=theme_id,
        display_name=display_name,
        preferred_fonts=("Segoe UI Variable", "Segoe UI"),
        qt_style="Fusion",
        is_dark=is_dark,
        colors=colors,
        metrics=metrics,
        stylesheet=_build_stylesheet(colors, metrics, is_dark=is_dark),
    )


FUTURE_LIGHT_THEME = _theme_definition(
    DEFAULT_THEME_ID,
    "Future Light",
    FUTURE_LIGHT_COLORS,
    FUTURE_LIGHT_METRICS,
    is_dark=False,
)
ARCTIC_CIRCUIT_THEME = _theme_definition(
    "arctic_circuit",
    "Arctic Circuit",
    ARCTIC_CIRCUIT_COLORS,
    ARCTIC_CIRCUIT_METRICS,
    is_dark=False,
)
PEARL_QUANTUM_THEME = _theme_definition(
    "pearl_quantum",
    "Pearl Quantum",
    PEARL_QUANTUM_COLORS,
    PEARL_QUANTUM_METRICS,
    is_dark=False,
)
AURORA_GLASS_THEME = _theme_definition(
    "aurora_glass",
    "Aurora Glass",
    AURORA_GLASS_COLORS,
    AURORA_GLASS_METRICS,
    is_dark=False,
)
SOLAR_PAPER_THEME = _theme_definition(
    "solar_paper",
    "Solar Paper",
    SOLAR_PAPER_COLORS,
    SOLAR_PAPER_METRICS,
    is_dark=False,
)
EVENT_HORIZON_THEME = _theme_definition(
    "event_horizon",
    "Event Horizon",
    EVENT_HORIZON_COLORS,
    EVENT_HORIZON_METRICS,
    is_dark=True,
)
EMBER_LAB_THEME = _theme_definition(
    "ember_lab",
    "Ember Lab",
    EMBER_LAB_COLORS,
    EMBER_LAB_METRICS,
    is_dark=True,
)

REGISTERED_THEMES = (
    FUTURE_LIGHT_THEME,
    ARCTIC_CIRCUIT_THEME,
    PEARL_QUANTUM_THEME,
    AURORA_GLASS_THEME,
    SOLAR_PAPER_THEME,
    EVENT_HORIZON_THEME,
    EMBER_LAB_THEME,
)

DEFAULT_THEME_MANAGER = ThemeManager(
    REGISTERED_THEMES,
    default_theme_id=DEFAULT_THEME_ID,
)


def apply_default_theme(application: QApplication) -> ThemeDefinition:
    """Apply the backward-compatible default desktop theme."""

    return DEFAULT_THEME_MANAGER.apply(application)


def themed_icon(filename: str) -> QIcon:
    """Load one neutral-tinted or semantic-color theme resource icon."""

    active_id = (
        DEFAULT_THEME_MANAGER.active_theme_id
        or DEFAULT_THEME_MANAGER.default_theme_id
    )
    active_theme = DEFAULT_THEME_MANAGER.definition(active_id)
    preserve_semantic_colors = filename in _SEMANTIC_COLOR_ICON_FILENAMES
    resource_filename = filename
    if preserve_semantic_colors and active_theme.is_dark:
        resource_filename = filename.removesuffix(".svg") + "_on_dark.svg"

    path = application_resource_path("icons", resource_filename)
    if not path.is_file():
        raise RuntimeError(f"required theme icon is missing: {path}")
    source = QIcon(str(path))
    if source.isNull():
        raise RuntimeError(f"theme icon could not be loaded: {path}")
    if path.suffix.casefold() != ".svg" or preserve_semantic_colors:
        return source

    color = QColor(active_theme.colors.primary_text)
    tinted_icon = QIcon()
    for pixel_size in (16, 18, 20, 24, 32):
        source_pixmap = source.pixmap(pixel_size, pixel_size)
        if source_pixmap.isNull():
            continue
        tinted_icon.addPixmap(_tint_pixmap(source_pixmap, color))
    if tinted_icon.isNull():
        raise RuntimeError(f"theme icon could not be rendered: {path}")
    return tinted_icon


def _tint_pixmap(source_pixmap: QPixmap, color: QColor) -> QPixmap:
    """Tint one pixmap without changing its logical high-DPI geometry."""

    tinted_pixmap = QPixmap(source_pixmap.size())
    # QIcon may render the source SVG at the active screen's device pixel
    # ratio.  Losing that ratio here makes Qt paint only the source's logical
    # extent into a larger DPR-1 canvas, so refreshed toolbar icons appear to
    # shrink after a theme switch.
    tinted_pixmap.setDevicePixelRatio(source_pixmap.devicePixelRatio())
    tinted_pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted_pixmap)
    painter.drawPixmap(0, 0, source_pixmap)
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_SourceIn
    )
    painter.fillRect(tinted_pixmap.rect(), color)
    painter.end()
    return tinted_pixmap


def _apply_preferred_font(
    application: QApplication,
    preferred_fonts: tuple[str, ...],
) -> None:
    installed = {
        family.casefold(): family for family in QFontDatabase.families()
    }
    for preferred in preferred_fonts:
        family = installed.get(preferred.casefold())
        if family is None:
            continue
        font = application.font()
        font.setFamily(family)
        application.setFont(font)
        return


def _rgb_tuple(value: str) -> tuple[int, int, int]:
    color = QColor(value)
    if not color.isValid():
        raise ValueError(f"invalid theme color: {value!r}")
    return color.red(), color.green(), color.blue()


def _build_palette(colors: ThemeColors) -> QPalette:
    palette = QPalette()
    roles = QPalette.ColorRole
    palette.setColor(roles.Window, QColor(colors.application_background))
    palette.setColor(roles.WindowText, QColor(colors.primary_text))
    palette.setColor(roles.Base, QColor(colors.workspace_background))
    palette.setColor(roles.AlternateBase, QColor(colors.application_background))
    palette.setColor(roles.ToolTipBase, QColor(colors.raised_background))
    palette.setColor(roles.ToolTipText, QColor(colors.primary_text))
    palette.setColor(roles.Text, QColor(colors.primary_text))
    palette.setColor(roles.Button, QColor(colors.raised_background))
    palette.setColor(roles.ButtonText, QColor(colors.primary_text))
    palette.setColor(roles.BrightText, QColor(colors.critical_text))
    palette.setColor(roles.Light, QColor(colors.raised_background))
    palette.setColor(roles.Midlight, QColor(colors.panel_background))
    palette.setColor(roles.Mid, QColor(colors.border))
    palette.setColor(roles.Dark, QColor(colors.strong_border))
    palette.setColor(roles.Shadow, QColor(colors.secondary_text))
    palette.setColor(roles.Highlight, QColor(colors.selected_background))
    palette.setColor(roles.HighlightedText, QColor(colors.primary_text))
    palette.setColor(roles.Link, QColor(colors.accent_dark))
    palette.setColor(roles.PlaceholderText, QColor(colors.disabled_text))

    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, roles.WindowText, QColor(colors.disabled_text))
    palette.setColor(disabled, roles.Text, QColor(colors.disabled_text))
    palette.setColor(disabled, roles.ButtonText, QColor(colors.disabled_text))
    palette.setColor(disabled, roles.Button, QColor(colors.disabled_background))
    palette.setColor(disabled, roles.Base, QColor(colors.disabled_background))
    return palette
