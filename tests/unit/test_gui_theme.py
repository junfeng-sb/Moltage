import sys
import tempfile
from pathlib import Path
import unittest
from xml.etree import ElementTree

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from moltage.app.user_view_preferences import (
    PersistedOrbitalLighting,
    UserViewPreferencesRepository,
)
from moltage.gui.theme import (
    DEFAULT_THEME_ID,
    DEFAULT_THEME_MANAGER,
    FUTURE_LIGHT_THEME,
    REGISTERED_THEMES,
    THEME_ID_PROPERTY,
    _tint_pixmap,
    themed_icon,
)
from moltage.visualization.view_preferences import (
    ATOM_HIGHLIGHT_COLORS_PROPERTY,
    AtomHighlightColors,
)


class GuiThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        DEFAULT_THEME_MANAGER.apply(self.application, DEFAULT_THEME_ID)

    def test_selected_themes_are_registered_with_future_light_default(self) -> None:
        self.assertEqual(DEFAULT_THEME_ID, "future_light")
        self.assertEqual(DEFAULT_THEME_MANAGER.default_theme_id, DEFAULT_THEME_ID)
        self.assertEqual(DEFAULT_THEME_MANAGER.themes, REGISTERED_THEMES)
        self.assertEqual(
            tuple(theme.theme_id for theme in REGISTERED_THEMES),
            (
                "future_light",
                "arctic_circuit",
                "pearl_quantum",
                "aurora_glass",
                "solar_paper",
                "event_horizon",
                "ember_lab",
            ),
        )
        self.assertEqual(
            tuple(theme.display_name for theme in REGISTERED_THEMES),
            (
                "Future Light",
                "Arctic Circuit",
                "Pearl Quantum",
                "Aurora Glass",
                "Solar Paper",
                "Event Horizon",
                "Ember Lab",
            ),
        )
        self.assertEqual(
            tuple(theme.is_dark for theme in REGISTERED_THEMES),
            (False, False, False, False, False, True, True),
        )
        self.assertEqual(FUTURE_LIGHT_THEME.display_name, "Future Light")

    def test_application_theme_centralizes_primary_surfaces_and_states(self) -> None:
        applied = DEFAULT_THEME_MANAGER.apply(self.application)

        self.assertIs(applied, FUTURE_LIGHT_THEME)
        self.assertEqual(
            self.application.property(THEME_ID_PROPERTY),
            DEFAULT_THEME_ID,
        )
        self.assertEqual(
            self.application.palette().color(QPalette.ColorRole.Window).name(),
            "#f5f8fc",
        )
        stylesheet = self.application.styleSheet()
        for selector in (
            "QToolBar#mainToolbar",
            "QDockWidget#auToolDock",
            "QTabWidget#workspaceTabs::pane",
            "QProgressBar::chunk",
            'QLabel[uiTone="warning"]',
            "QFrame#torsionAngleEditor",
        ):
            self.assertIn(selector, stylesheet)
        self.assertIn("min-height: 6px", stylesheet)
        self.assertIn("max-height: 6px", stylesheet)
        self.assertIn(
            'QRadioButton[themeChoice="true"]::indicator:checked',
            stylesheet,
        )
        self.assertIn("background: qradialgradient(", stylesheet)
        self.assertIn("border-radius: 7px", stylesheet)

    def test_every_registered_theme_applies_complete_valid_colors(self) -> None:
        for theme in REGISTERED_THEMES:
            with self.subTest(theme=theme.theme_id):
                applied = DEFAULT_THEME_MANAGER.apply(
                    self.application,
                    theme.theme_id,
                )
                self.assertIs(applied, theme)
                self.assertEqual(
                    self.application.property(THEME_ID_PROPERTY),
                    theme.theme_id,
                )
                self.assertEqual(
                    self.application.palette()
                    .color(QPalette.ColorRole.Window)
                    .name(),
                    QColor(theme.colors.application_background).name(),
                )
                self.assertIn(theme.colors.accent, theme.stylesheet)
                self.assertIn("min-height: 6px", theme.stylesheet)
                self.assertIn("QComboBox::down-arrow", theme.stylesheet)
                arrow_name = (
                    "combo_down_on_dark.svg"
                    if theme.is_dark
                    else "combo_down.svg"
                )
                self.assertIn(arrow_name, theme.stylesheet)
                self.assertNotIn("QToolButton::menu-indicator", theme.stylesheet)
                self.assertFalse(themed_icon("theme.svg").isNull())
                highlight_colors = self.application.property(
                    ATOM_HIGHLIGHT_COLORS_PROPERTY
                )
                self.assertIsInstance(highlight_colors, AtomHighlightColors)
                self.assertEqual(
                    highlight_colors,
                    AtomHighlightColors(
                        hover=self._rgb(theme.colors.atom_hover),
                        primary_group=self._rgb(
                            theme.colors.atom_group_primary
                        ),
                        secondary_group=self._rgb(
                            theme.colors.atom_group_secondary
                        ),
                    ),
                )
                for value in (
                    theme.colors.atom_hover,
                    theme.colors.atom_group_primary,
                    theme.colors.atom_group_secondary,
                ):
                    self.assertGreaterEqual(QColor(value).hsvSaturation(), 140)

    def test_tinted_icon_preserves_high_dpi_visible_geometry(self) -> None:
        source = QIcon(
            str(PROJECT_ROOT / "resources" / "icons" / "undo.svg")
        )
        source_pixmap = source.pixmap(QSize(20, 20), 2.0)
        self.assertEqual(source_pixmap.devicePixelRatio(), 2.0)

        tinted_pixmap = _tint_pixmap(source_pixmap, QColor("#F4EEE8"))

        self.assertEqual(tinted_pixmap.size(), source_pixmap.size())
        self.assertEqual(
            tinted_pixmap.devicePixelRatio(),
            source_pixmap.devicePixelRatio(),
        )
        self.assertEqual(
            tinted_pixmap.deviceIndependentSize(),
            source_pixmap.deviceIndependentSize(),
        )
        self.assertEqual(
            self._visible_pixel_bounds(tinted_pixmap),
            self._visible_pixel_bounds(source_pixmap),
        )

    def test_semantic_operation_icons_keep_color_in_light_and_dark(self) -> None:
        filenames = (
            "measure_angle.svg",
            "rotate_bond.svg",
            "delete_atom.svg",
            "replace_atom.svg",
        )
        for theme_id in ("future_light", "event_horizon"):
            DEFAULT_THEME_MANAGER.apply(self.application, theme_id)
            for filename in filenames:
                with self.subTest(theme=theme_id, filename=filename):
                    pixmap = themed_icon(filename).pixmap(QSize(24, 24))
                    colors = {
                        image.pixelColor(x, y).rgb()
                        for image in (pixmap.toImage(),)
                        for y in range(image.height())
                        for x in range(image.width())
                        if image.pixelColor(x, y).alpha() > 96
                    }
                    self.assertGreater(len(colors), 1)
                    left, top, right, bottom = self._visible_pixel_bounds(pixmap)
                    self.assertGreater(left, 0)
                    self.assertGreater(top, 0)
                    self.assertLess(right, pixmap.width() - 1)
                    self.assertLess(bottom, pixmap.height() - 1)

    def test_operation_svg_geometry_preserves_requested_visual_cues(self) -> None:
        icon_root = PROJECT_ROOT / "resources" / "icons"
        ruler = ElementTree.parse(icon_root / "measure_distance.svg")
        angle = ElementTree.parse(icon_root / "measure_angle.svg")
        delete = ElementTree.parse(icon_root / "delete_atom.svg")
        replace = ElementTree.parse(icon_root / "replace_atom.svg")

        self.assertIsNotNone(ruler.find(".//*[@id='rulerTicks']"))
        angle_arc = angle.find(".//*[@id='angleArc']")
        self.assertIsNotNone(angle_arc)
        self.assertEqual(angle_arc.attrib["stroke-width"], "2")
        hollow_atom = delete.find(".//*[@id='hollowAtom']")
        self.assertIsNotNone(hollow_atom)
        self.assertEqual(hollow_atom.attrib["fill"], "none")
        source_atom = replace.find(".//*[@id='sourceAtom']")
        target_atom = replace.find(".//*[@id='targetAtom']")
        self.assertIsNotNone(source_atom)
        self.assertIsNotNone(target_atom)
        self.assertNotEqual(source_atom.attrib["r"], target_atom.attrib["r"])
        self.assertNotEqual(source_atom.attrib["fill"], target_atom.attrib["fill"])

    def test_ruler_icon_renders_without_touching_the_canvas_edge(self) -> None:
        for theme_id in ("future_light", "event_horizon"):
            DEFAULT_THEME_MANAGER.apply(self.application, theme_id)
            pixmap = themed_icon("measure_distance.svg").pixmap(QSize(24, 24))
            left, top, right, bottom = self._visible_pixel_bounds(pixmap)
            with self.subTest(theme=theme_id):
                self.assertGreater(left, 0)
                self.assertGreater(top, 0)
                self.assertLess(right, pixmap.width() - 1)
                self.assertLess(bottom, pixmap.height() - 1)

    @staticmethod
    def _rgb(value: str) -> tuple[int, int, int]:
        color = QColor(value)
        return color.red(), color.green(), color.blue()

    @staticmethod
    def _visible_pixel_bounds(pixmap: QPixmap) -> tuple[int, int, int, int]:
        image = pixmap.toImage()
        visible = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        ]
        if not visible:
            raise AssertionError("test icon contains no visible pixels")
        xs, ys = zip(*visible, strict=True)
        return min(xs), min(ys), max(xs), max(ys)

    def test_theme_preference_round_trip_preserves_orbital_lighting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = UserViewPreferencesRepository(
                Path(directory) / "view_preferences.json"
            )
            lighting = PersistedOrbitalLighting(0.65, 0.35, 0.55, 48.0)
            repository.save(lighting)
            repository.save_theme_id("event_horizon")

            self.assertEqual(repository.load(), lighting)
            self.assertEqual(repository.load_theme_id(), "event_horizon")

            changed = PersistedOrbitalLighting(0.75, 0.45, 0.30, 32.0)
            repository.save(changed)
            self.assertEqual(repository.load(), changed)
            self.assertEqual(repository.load_theme_id(), "event_horizon")


if __name__ == "__main__":
    unittest.main()
