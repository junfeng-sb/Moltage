"""Compact editor for session-local molecular view preferences."""

from collections.abc import Iterable
from math import isfinite

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from moltage.visualization.molecule_scene import ELEMENT_COLORS_RGB
from moltage.visualization.view_preferences import (
    MAXIMUM_BOND_THICKNESS_SCALE,
    MINIMUM_BOND_THICKNESS_SCALE,
    RgbColor,
    ViewPreferences,
)
from moltage.visualization.orbital_surface import (
    DEFAULT_ORBITAL_SPECULAR,
    DEFAULT_ORBITAL_SHININESS,
    MAXIMUM_ORBITAL_AMBIENT,
    MAXIMUM_ORBITAL_LIGHT_INTENSITY,
    MAXIMUM_ORBITAL_SPECULAR,
    MAXIMUM_ORBITAL_SHININESS,
    MINIMUM_ORBITAL_AMBIENT,
    MINIMUM_ORBITAL_LIGHT_INTENSITY,
    MINIMUM_ORBITAL_SPECULAR,
    MINIMUM_ORBITAL_SHININESS,
    ORBITAL_AMBIENT_STEP,
    ORBITAL_ISOVALUE_STEP,
    ORBITAL_LIGHT_INTENSITY_STEP,
    ORBITAL_SPECULAR_STEP,
    ORBITAL_SHININESS_STEP,
    RECOMMENDED_ORBITAL_AMBIENT,
    RECOMMENDED_ORBITAL_LIGHT_INTENSITY,
    OrbitalSurfacePreferences,
    OrbitalSurfaceResolution,
    OrbitalSurfaceStyle,
)


class ViewSettingsDialog(QDialog):
    """Edit and preview one copy of application-session viewer preferences."""

    preview_preferences_changed = Signal(object)
    preview_orbital_preferences_changed = Signal(object)

    def __init__(
        self,
        preferences: ViewPreferences,
        displayed_elements: Iterable[str],
        parent: QWidget | None = None,
        *,
        orbital_preferences: OrbitalSurfacePreferences | None = None,
        orbital_maximum_isovalue: float | None = None,
        orbital_resolution_control: bool = False,
    ) -> None:
        super().__init__(parent)
        if not isinstance(preferences, ViewPreferences):
            raise TypeError("view settings require ViewPreferences")
        self._working_overrides = dict(preferences.element_color_overrides)
        requested_elements = set(displayed_elements) | set(
            self._working_overrides
        )
        unsupported = requested_elements - set(ELEMENT_COLORS_RGB)
        if unsupported:
            raise ValueError(
                "view settings have no default color for: "
                + ", ".join(sorted(unsupported))
            )
        self._elements = tuple(
            element for element in ELEMENT_COLORS_RGB if element in requested_elements
        )
        self._color_buttons: dict[str, QPushButton] = {}
        self._reset_buttons: dict[str, QPushButton] = {}
        self._orbital_preferences_available = orbital_preferences is not None
        self._orbital_style: QComboBox | None = None
        self._orbital_resolution: QComboBox | None = None
        self._orbital_resolution_value = (
            orbital_preferences.resolution
            if orbital_preferences is not None
            else OrbitalSurfaceResolution.FULL
        )
        self._orbital_isovalue: QDoubleSpinBox | None = None
        self._orbital_ambient: QDoubleSpinBox | None = None
        self._orbital_light_intensity: QDoubleSpinBox | None = None
        self._orbital_specular: QDoubleSpinBox | None = None
        self._orbital_shininess: QDoubleSpinBox | None = None
        self._nonclosing_numeric_targets: dict[QObject, QDoubleSpinBox] = {}
        self._orbital_color_buttons: dict[str, QPushButton] = {}
        if orbital_preferences is None:
            if orbital_maximum_isovalue is not None:
                raise ValueError(
                    "orbital maximum requires orbital surface preferences"
                )
            self._working_positive_lobe_color: RgbColor | None = None
            self._working_negative_lobe_color: RgbColor | None = None
        else:
            if not isinstance(orbital_preferences, OrbitalSurfacePreferences):
                raise TypeError(
                    "orbital settings require OrbitalSurfacePreferences"
                )
            if (
                isinstance(orbital_maximum_isovalue, bool)
                or not isinstance(orbital_maximum_isovalue, (int, float))
                or not isfinite(float(orbital_maximum_isovalue))
                or float(orbital_maximum_isovalue) < 0.0
            ):
                raise ValueError(
                    "orbital maximum isovalue must be finite and non-negative"
                )
            if orbital_preferences.isovalue > float(
                orbital_maximum_isovalue
            ):
                raise ValueError(
                    "orbital isovalue exceeds the scalar field data range"
                )
            self._working_positive_lobe_color = (
                orbital_preferences.positive_color
            )
            self._working_negative_lobe_color = (
                orbital_preferences.negative_color
            )

        self.setWindowTitle("View Settings")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        tabs = QTabWidget(self)
        tabs.setObjectName("viewSettingsTabs")
        molecule_tab = QWidget(tabs)
        molecule_tab.setObjectName("molecularViewTab")
        molecule_layout = QVBoxLayout(molecule_tab)

        appearance = QGroupBox("Molecular View", molecule_tab)
        appearance_layout = QGridLayout(appearance)
        appearance_layout.addWidget(QLabel("Bond thickness:", appearance), 0, 0)
        self._bond_thickness = QDoubleSpinBox(appearance)
        self._bond_thickness.setObjectName("bondThicknessScale")
        self._bond_thickness.setAccessibleName("Bond thickness")
        self._bond_thickness.setDecimals(2)
        self._bond_thickness.setRange(
            MINIMUM_BOND_THICKNESS_SCALE,
            MAXIMUM_BOND_THICKNESS_SCALE,
        )
        self._bond_thickness.setSingleStep(0.05)
        self._bond_thickness.setSuffix("×")
        self._bond_thickness.setKeyboardTracking(True)
        self._bond_thickness.setValue(preferences.bond_thickness_scale)
        appearance_layout.addWidget(self._bond_thickness, 0, 1)

        self._show_element_labels = QCheckBox(
            "Show element symbols on atoms",
            appearance,
        )
        self._show_element_labels.setObjectName("showElementSymbols")
        self._show_element_labels.setChecked(preferences.show_element_labels)
        appearance_layout.addWidget(self._show_element_labels, 1, 0, 1, 2)

        self._hide_hydrogen = QCheckBox(
            "Hide hydrogen atoms (visual only)",
            appearance,
        )
        self._hide_hydrogen.setObjectName("hideHydrogenAtoms")
        self._hide_hydrogen.setChecked(preferences.hide_hydrogen)
        appearance_layout.addWidget(self._hide_hydrogen, 2, 0, 1, 2)
        molecule_layout.addWidget(appearance)

        colors = QGroupBox("Atom Colors", molecule_tab)
        colors_layout = QVBoxLayout(colors)
        if self._elements:
            scroll = QScrollArea(colors)
            scroll.setObjectName("atomColorsScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            rows = QWidget(scroll)
            rows_layout = QGridLayout(rows)
            rows_layout.setContentsMargins(0, 0, 0, 0)
            rows_layout.setHorizontalSpacing(8)
            rows_layout.setVerticalSpacing(4)
            rows_layout.addWidget(QLabel("Element", rows), 0, 0)
            rows_layout.addWidget(QLabel("Color", rows), 0, 1)
            for row, element in enumerate(self._elements, start=1):
                element_label = QLabel(element, rows)
                element_label.setObjectName(f"elementColorLabel_{element}")
                rows_layout.addWidget(element_label, row, 0)
                color_button = QPushButton("", rows)
                color_button.setObjectName(f"elementColor_{element}")
                color_button.setAccessibleName(f"Change {element} color")
                color_button.setToolTip(f"Change {element} display color")
                color_button.setFixedWidth(92)
                color_button.clicked.connect(
                    lambda _checked=False, symbol=element: (
                        self._choose_element_color(symbol)
                    )
                )
                rows_layout.addWidget(color_button, row, 1)
                reset_button = QPushButton("Reset to Default", rows)
                reset_button.setObjectName(f"resetElementColor_{element}")
                reset_button.clicked.connect(
                    lambda _checked=False, symbol=element: (
                        self._reset_element_color(symbol)
                    )
                )
                rows_layout.addWidget(reset_button, row, 2)
                self._color_buttons[element] = color_button
                self._reset_buttons[element] = reset_button
            rows_layout.setColumnStretch(1, 1)
            scroll.setWidget(rows)
            scroll.setMaximumHeight(230)
            colors_layout.addWidget(scroll)
        else:
            empty = QLabel(
                "Open a Geometry workspace to customize element colors.",
                colors,
            )
            empty.setObjectName("atomColorsEmptyState")
            empty.setWordWrap(True)
            colors_layout.addWidget(empty)

        self._reset_all_colors = QPushButton("Reset All Colors", colors)
        self._reset_all_colors.setObjectName("resetAllElementColors")
        self._reset_all_colors.clicked.connect(self._reset_all_element_colors)
        colors_layout.addWidget(
            self._reset_all_colors,
            alignment=Qt.AlignmentFlag.AlignRight,
        )
        molecule_layout.addWidget(colors)
        tabs.addTab(molecule_tab, "Molecule")

        if orbital_preferences is not None:
            self.setMinimumWidth(640)
            orbital = QWidget(tabs)
            orbital.setObjectName("orbitalSurfaceTab")
            orbital_layout = QGridLayout(orbital)
            surface_group = QGroupBox("Surface", orbital)
            surface_group.setObjectName("orbitalSurfaceGroup")
            surface_layout = QGridLayout(surface_group)
            lighting_group = QGroupBox("Lighting and Material", orbital)
            lighting_group.setObjectName("orbitalMaterialGroup")
            lighting_layout = QGridLayout(lighting_group)
            orbital_layout.addWidget(surface_group, 0, 0, Qt.AlignmentFlag.AlignTop)
            orbital_layout.addWidget(lighting_group, 0, 1, Qt.AlignmentFlag.AlignTop)
            surface_layout.addWidget(QLabel("Display:", orbital), 0, 0)
            self._orbital_style = QComboBox(orbital)
            self._orbital_style.setObjectName("orbitalSurfaceStyle")
            self._orbital_style.addItem(
                OrbitalSurfaceStyle.OPAQUE.value,
                OrbitalSurfaceStyle.OPAQUE,
            )
            self._orbital_style.addItem(
                "Semi-transparent (50%)",
                OrbitalSurfaceStyle.SEMI_TRANSPARENT,
            )
            self._orbital_style.setCurrentIndex(
                self._orbital_style.findData(orbital_preferences.style)
            )
            surface_layout.addWidget(self._orbital_style, 0, 1)

            if orbital_resolution_control:
                surface_layout.addWidget(
                    QLabel("Display resolution:", orbital), 1, 0
                )
                self._orbital_resolution = QComboBox(orbital)
                self._orbital_resolution.setObjectName(
                    "orbitalSurfaceResolution"
                )
                for resolution in OrbitalSurfaceResolution:
                    suffix = {
                        OrbitalSurfaceResolution.FULL: "source grid",
                        OrbitalSurfaceResolution.MEDIUM: "max 120 × 120 × 120",
                        OrbitalSurfaceResolution.LOW: "max 80 × 80 × 80",
                    }[resolution]
                    self._orbital_resolution.addItem(
                        f"{resolution.value} ({suffix})",
                        resolution,
                    )
                self._orbital_resolution.setCurrentIndex(
                    self._orbital_resolution.findData(
                        orbital_preferences.resolution
                    )
                )
                self._orbital_resolution.setToolTip(
                    "Display only. Scientific Cube data, Hirshfeld values and "
                    "exports remain full resolution."
                )
                surface_layout.addWidget(self._orbital_resolution, 1, 1)

            surface_layout.addWidget(QLabel("Isovalue:", orbital), 2, 0)
            self._orbital_isovalue = QDoubleSpinBox(orbital)
            self._orbital_isovalue.setObjectName("orbitalIsovalue")
            self._orbital_isovalue.setAccessibleName("Orbital isovalue")
            self._orbital_isovalue.setDecimals(6)
            self._orbital_isovalue.setRange(
                0.0,
                float(orbital_maximum_isovalue),
            )
            self._orbital_isovalue.setSingleStep(ORBITAL_ISOVALUE_STEP)
            self._orbital_isovalue.setKeyboardTracking(False)
            self._orbital_isovalue.setValue(orbital_preferences.isovalue)
            surface_layout.addWidget(self._orbital_isovalue, 2, 1)

            surface_layout.addWidget(QLabel("Positive lobe:", orbital), 3, 0)
            positive_button = self._new_orbital_color_button(
                "positive",
                "positiveLobeColor",
                orbital,
            )
            surface_layout.addWidget(positive_button, 3, 1)
            surface_layout.addWidget(QLabel("Negative lobe:", orbital), 4, 0)
            negative_button = self._new_orbital_color_button(
                "negative",
                "negativeLobeColor",
                orbital,
            )
            surface_layout.addWidget(negative_button, 4, 1)

            lighting_layout.addWidget(QLabel("Ambient light:", orbital), 0, 0)
            self._orbital_ambient = QDoubleSpinBox(orbital)
            self._orbital_ambient.setObjectName("orbitalAmbientLight")
            self._orbital_ambient.setAccessibleName(
                "Orbital ambient lighting"
            )
            self._orbital_ambient.setDecimals(2)
            self._orbital_ambient.setRange(
                MINIMUM_ORBITAL_AMBIENT,
                MAXIMUM_ORBITAL_AMBIENT,
            )
            self._orbital_ambient.setSingleStep(ORBITAL_AMBIENT_STEP)
            self._orbital_ambient.setKeyboardTracking(True)
            self._orbital_ambient.setValue(orbital_preferences.ambient)
            self._orbital_ambient.setToolTip(
                "Higher values retain more lobe color in shadowed regions."
            )
            lighting_layout.addWidget(self._orbital_ambient, 0, 1)

            lighting_layout.addWidget(
                QLabel("Light intensity:", orbital),
                1,
                0,
            )
            self._orbital_light_intensity = QDoubleSpinBox(orbital)
            self._orbital_light_intensity.setObjectName(
                "orbitalLightIntensity"
            )
            self._orbital_light_intensity.setAccessibleName(
                "Orbital light intensity"
            )
            self._orbital_light_intensity.setDecimals(2)
            self._orbital_light_intensity.setRange(
                MINIMUM_ORBITAL_LIGHT_INTENSITY,
                MAXIMUM_ORBITAL_LIGHT_INTENSITY,
            )
            self._orbital_light_intensity.setSingleStep(
                ORBITAL_LIGHT_INTENSITY_STEP
            )
            self._orbital_light_intensity.setKeyboardTracking(True)
            self._orbital_light_intensity.setValue(
                orbital_preferences.light_intensity
            )
            self._orbital_light_intensity.setToolTip(
                "Controls orbital-lobe brightness; atoms and bonds are "
                "unchanged."
            )
            lighting_layout.addWidget(self._orbital_light_intensity, 1, 1)

            lighting_layout.addWidget(QLabel("Specular:", orbital), 2, 0)
            self._orbital_specular = QDoubleSpinBox(orbital)
            self._orbital_specular.setObjectName("orbitalSpecular")
            self._orbital_specular.setAccessibleName("Orbital highlight strength")
            self._orbital_specular.setDecimals(2)
            self._orbital_specular.setRange(
                MINIMUM_ORBITAL_SPECULAR, MAXIMUM_ORBITAL_SPECULAR
            )
            self._orbital_specular.setSingleStep(ORBITAL_SPECULAR_STEP)
            self._orbital_specular.setKeyboardTracking(True)
            self._orbital_specular.setValue(orbital_preferences.specular)
            self._orbital_specular.setToolTip(
                "White highlight strength; 0 turns highlights off."
            )
            lighting_layout.addWidget(self._orbital_specular, 2, 1)

            lighting_layout.addWidget(QLabel("Shininess:", orbital), 3, 0)
            self._orbital_shininess = QDoubleSpinBox(orbital)
            self._orbital_shininess.setObjectName("orbitalShininess")
            self._orbital_shininess.setAccessibleName("Orbital highlight focus")
            self._orbital_shininess.setDecimals(0)
            self._orbital_shininess.setRange(
                MINIMUM_ORBITAL_SHININESS, MAXIMUM_ORBITAL_SHININESS
            )
            self._orbital_shininess.setSingleStep(ORBITAL_SHININESS_STEP)
            self._orbital_shininess.setKeyboardTracking(True)
            self._orbital_shininess.setValue(orbital_preferences.shininess)
            self._orbital_shininess.setToolTip(
                "Higher values make highlights smaller and more focused."
            )
            lighting_layout.addWidget(self._orbital_shininess, 3, 1)

            recommended = QPushButton("Apply Recommended Material", orbital)
            recommended.setObjectName("recommendedOrbitalMaterial")
            recommended.setAutoDefault(False)
            recommended.setToolTip(
                "Preview balanced shading and highlights without changing "
                "isovalue, lobe colors or transparency."
            )
            recommended.clicked.connect(self._apply_recommended_orbital_material)
            lighting_layout.addWidget(recommended, 4, 0, 1, 2)
            surface_layout.setColumnStretch(1, 1)
            lighting_layout.setColumnStretch(1, 1)
            orbital_layout.setColumnStretch(0, 1)
            orbital_layout.setColumnStretch(1, 1)
            orbital_layout.setRowStretch(1, 1)
            tabs.addTab(orbital, "Isosurface")

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_color_controls()
        self._bond_thickness.valueChanged.connect(
            self._emit_preview_preferences
        )
        self._show_element_labels.toggled.connect(
            self._emit_preview_preferences
        )
        self._hide_hydrogen.toggled.connect(
            self._emit_preview_preferences
        )
        if self._orbital_style is not None:
            self._orbital_style.currentIndexChanged.connect(
                self._emit_preview_orbital_preferences
            )
        if self._orbital_resolution is not None:
            self._orbital_resolution.currentIndexChanged.connect(
                self._emit_preview_orbital_preferences
            )
        if self._orbital_isovalue is not None:
            self._orbital_isovalue.valueChanged.connect(
                self._emit_preview_orbital_preferences
            )
        if self._orbital_ambient is not None:
            self._orbital_ambient.valueChanged.connect(
                self._emit_preview_orbital_preferences
            )
        if self._orbital_light_intensity is not None:
            self._orbital_light_intensity.valueChanged.connect(
                self._emit_preview_orbital_preferences
            )
        for spin_box in (self._orbital_specular, self._orbital_shininess):
            if spin_box is not None:
                spin_box.valueChanged.connect(self._emit_preview_orbital_preferences)
        for spin_box in (
            self._bond_thickness,
            self._orbital_isovalue,
            self._orbital_ambient,
            self._orbital_light_intensity,
            self._orbital_specular,
            self._orbital_shininess,
        ):
            if spin_box is not None:
                editor = spin_box.lineEdit()
                for event_target in (spin_box, editor):
                    self._nonclosing_numeric_targets[event_target] = spin_box
                    event_target.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Commit numeric edits on Enter without accepting the dialog."""

        spin_box = self._nonclosing_numeric_targets.get(watched)
        if (
            spin_box is not None
            and event.type() == QEvent.Type.KeyPress
            and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
        ):
            spin_box.interpretText()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def selected_preferences(self) -> ViewPreferences:
        """Return the current dialog values as one immutable preference state."""

        return ViewPreferences(
            bond_thickness_scale=self._bond_thickness.value(),
            element_color_overrides=self._working_overrides,
            show_element_labels=self._show_element_labels.isChecked(),
            hide_hydrogen=self._hide_hydrogen.isChecked(),
        )

    def selected_orbital_preferences(
        self,
    ) -> OrbitalSurfacePreferences | None:
        """Return Cube-only presentation state when this dialog has one."""

        if not self._orbital_preferences_available:
            return None
        assert self._orbital_style is not None
        assert self._orbital_isovalue is not None
        assert self._orbital_ambient is not None
        assert self._orbital_light_intensity is not None
        assert self._orbital_specular is not None
        assert self._orbital_shininess is not None
        assert self._working_positive_lobe_color is not None
        assert self._working_negative_lobe_color is not None
        style = self._orbital_style.currentData()
        try:
            style = OrbitalSurfaceStyle(style)
        except (TypeError, ValueError) as error:
            raise TypeError("selected orbital surface style is invalid") from error
        resolution = (
            self._orbital_resolution.currentData()
            if self._orbital_resolution is not None
            else self._orbital_resolution_value
        )
        try:
            resolution = OrbitalSurfaceResolution(resolution)
        except (TypeError, ValueError) as error:
            raise TypeError(
                "selected orbital surface resolution is invalid"
            ) from error
        return OrbitalSurfacePreferences(
            isovalue=self._orbital_isovalue.value(),
            positive_color=self._working_positive_lobe_color,
            negative_color=self._working_negative_lobe_color,
            style=style,
            resolution=resolution,
            ambient=self._orbital_ambient.value(),
            light_intensity=self._orbital_light_intensity.value(),
            specular=self._orbital_specular.value(),
            shininess=self._orbital_shininess.value(),
        )

    def _apply_recommended_orbital_material(self) -> None:
        controls = (
            (self._orbital_ambient, RECOMMENDED_ORBITAL_AMBIENT),
            (self._orbital_light_intensity, RECOMMENDED_ORBITAL_LIGHT_INTENSITY),
            (self._orbital_specular, DEFAULT_ORBITAL_SPECULAR),
            (self._orbital_shininess, DEFAULT_ORBITAL_SHININESS),
        )
        blockers = [QSignalBlocker(control) for control, _ in controls]
        for control, value in controls:
            assert control is not None
            control.setValue(value)
        del blockers
        self._emit_preview_orbital_preferences()

    def _new_orbital_color_button(
        self,
        lobe: str,
        object_name: str,
        parent: QWidget,
    ) -> QPushButton:
        button = QPushButton("", parent)
        button.setObjectName(object_name)
        button.setAccessibleName(f"Change {lobe} orbital lobe color")
        button.setFixedWidth(112)
        button.clicked.connect(
            lambda _checked=False, selected_lobe=lobe: (
                self._choose_orbital_color(selected_lobe)
            )
        )
        self._orbital_color_buttons[lobe] = button
        self._refresh_orbital_color_controls()
        return button

    def _choose_orbital_color(self, lobe: str) -> None:
        current = self._orbital_color(lobe)
        chooser = QColorDialog(QColor(*current), self)
        chooser.setWindowTitle(f"{lobe.title()} Orbital Lobe Color")
        chooser.currentColorChanged.connect(
            lambda color, selected_lobe=lobe: self._preview_orbital_qcolor(
                selected_lobe,
                color,
            )
        )
        if chooser.exec() == QDialog.DialogCode.Accepted:
            self._preview_orbital_qcolor(lobe, chooser.selectedColor())
            return
        self._set_orbital_color(lobe, current)

    def _preview_orbital_qcolor(self, lobe: str, color: QColor) -> None:
        if not color.isValid():
            return
        self._set_orbital_color(
            lobe,
            (color.red(), color.green(), color.blue()),
        )

    def _set_orbital_color(self, lobe: str, color: RgbColor) -> None:
        """Set one lobe color; kept narrow for non-modal GUI tests."""

        validated = OrbitalSurfacePreferences(
            positive_color=color,
            negative_color=color,
        ).positive_color
        if lobe == "positive":
            if self._working_positive_lobe_color == validated:
                return
            self._working_positive_lobe_color = validated
        elif lobe == "negative":
            if self._working_negative_lobe_color == validated:
                return
            self._working_negative_lobe_color = validated
        else:
            raise ValueError(f"unknown orbital lobe {lobe!r}")
        self._refresh_orbital_color_controls()
        self._emit_preview_orbital_preferences()

    def _orbital_color(self, lobe: str) -> RgbColor:
        if lobe == "positive" and self._working_positive_lobe_color is not None:
            return self._working_positive_lobe_color
        if lobe == "negative" and self._working_negative_lobe_color is not None:
            return self._working_negative_lobe_color
        raise ValueError(f"unknown or unavailable orbital lobe {lobe!r}")

    def _refresh_orbital_color_controls(self) -> None:
        for lobe, button in self._orbital_color_buttons.items():
            red, green, blue = self._orbital_color(lobe)
            text_color = "#000000" if red + green + blue >= 382 else "#ffffff"
            button.setText(f"#{red:02X}{green:02X}{blue:02X}")
            button.setStyleSheet(
                "QPushButton {"
                f"background-color: rgb({red}, {green}, {blue});"
                f"color: {text_color};"
                "}"
            )

    def _choose_element_color(self, element: str) -> None:
        current = self._display_color(element)
        had_override = element in self._working_overrides
        original_override = self._working_overrides.get(element)
        chooser = QColorDialog(QColor(*current), self)
        chooser.setWindowTitle(f"{element} Atom Color")
        chooser.currentColorChanged.connect(
            lambda color, symbol=element: self._preview_qcolor(symbol, color)
        )
        if chooser.exec() == QDialog.DialogCode.Accepted:
            self._preview_qcolor(element, chooser.selectedColor())
            return
        if had_override:
            assert original_override is not None
            self._working_overrides[element] = original_override
        else:
            self._working_overrides.pop(element, None)
        self._refresh_color_controls()
        self._emit_preview_preferences()

    def _preview_qcolor(self, element: str, color: QColor) -> None:
        if not color.isValid():
            return
        self._set_element_color(
            element,
            (color.red(), color.green(), color.blue()),
        )

    def _set_element_color(self, element: str, color: RgbColor) -> None:
        """Set one working color; kept narrow so GUI tests need no modal chooser."""

        validated = ViewPreferences(
            element_color_overrides={element: color}
        ).element_color_overrides[element]
        if self._working_overrides.get(element) == validated:
            return
        self._working_overrides[element] = validated
        self._refresh_color_controls()
        self._emit_preview_preferences()

    def _reset_element_color(self, element: str) -> None:
        if self._working_overrides.pop(element, None) is None:
            return
        self._refresh_color_controls()
        self._emit_preview_preferences()

    def _reset_all_element_colors(self) -> None:
        if not self._working_overrides:
            return
        self._working_overrides.clear()
        self._refresh_color_controls()
        self._emit_preview_preferences()

    def _display_color(self, element: str) -> RgbColor:
        return self._working_overrides.get(element, ELEMENT_COLORS_RGB[element])

    def _refresh_color_controls(self) -> None:
        for element, button in self._color_buttons.items():
            red, green, blue = self._display_color(element)
            text_color = "#000000" if red + green + blue >= 382 else "#ffffff"
            button.setText(f"#{red:02X}{green:02X}{blue:02X}")
            button.setStyleSheet(
                "QPushButton {"
                f"background-color: rgb({red}, {green}, {blue});"
                f"color: {text_color};"
                "}"
            )
            self._reset_buttons[element].setEnabled(
                element in self._working_overrides
            )
        self._reset_all_colors.setEnabled(bool(self._working_overrides))

    def _emit_preview_preferences(self, *_unused: object) -> None:
        self.preview_preferences_changed.emit(self.selected_preferences())

    def _emit_preview_orbital_preferences(self, *_unused: object) -> None:
        preferences = self.selected_orbital_preferences()
        if preferences is not None:
            self.preview_orbital_preferences_changed.emit(preferences)
