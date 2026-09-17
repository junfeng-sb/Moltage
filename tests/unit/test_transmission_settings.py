import unittest
from dataclasses import replace

from PySide6.QtWidgets import QApplication

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


class TransmissionSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_dialog_routes_to_requested_page_and_collects_all_controls(self):
        dialog = TransmissionSettingsDialog(_settings(), initial_page="curve")
        dialog.curve_page.width.setValue(4.0)
        dialog.curve_page.line_style.setCurrentIndex(
            dialog.curve_page.line_style.findData(PlotLineStyle.DOT.value)
        )
        dialog.canvas_page.width.setValue(1600)
        dialog.canvas_page.height.setValue(900)

        selected = dialog._collect()

        self.assertEqual(dialog.tabs.currentIndex(), 3)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.curve.width, 4.0)
        self.assertEqual(selected.curve.line_style, PlotLineStyle.DOT)
        self.assertEqual(selected.canvas.export_width, 1600)
        self.assertEqual(selected.canvas.export_height, 900)
        dialog.deleteLater()

    def test_tick_page_collects_lengths_widths_direction_and_intervals(self):
        dialog = TransmissionSettingsDialog(_settings(), initial_page="ticks")
        page = dialog.tick_page
        page.major_length.setValue(9.0)
        page.major_width.setValue(2.0)
        page.minor_visible.setChecked(True)
        page.minor_length.setValue(4.0)
        page.minor_width.setValue(1.5)
        page.direction.setCurrentIndex(
            page.direction.findData(TickDirection.INSIDE.value)
        )
        page.x_major_tick_count.setValue(7)
        page.x_minor_tick_count.setValue(3)
        page.y_minor_tick_count.setValue(8)

        selected = dialog._collect()

        self.assertEqual(dialog.tabs.currentIndex(), 2)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.ticks.major_length, 9.0)
        self.assertEqual(selected.ticks.major_width, 2.0)
        self.assertTrue(selected.ticks.minor_visible)
        self.assertEqual(selected.ticks.minor_length, 4.0)
        self.assertEqual(selected.ticks.minor_width, 1.5)
        self.assertEqual(selected.ticks.direction, TickDirection.INSIDE)
        self.assertEqual(selected.ticks.x_major_tick_count, 7)
        self.assertEqual(selected.ticks.x_minor_tick_count, 3)
        self.assertEqual(selected.ticks.y_minor_tick_count, 8)
        dialog.deleteLater()

    def test_log_axis_rejects_nonpositive_minimum_without_emitting(self):
        dialog = TransmissionSettingsDialog(_settings(), initial_page="y")
        dialog.y_axis_page.minimum.setText("0")

        selected = dialog._collect()

        self.assertIsNone(selected)
        self.assertTrue(dialog.error.isVisible() or bool(dialog.error.text()))
        self.assertIn("greater than zero", dialog.error.text())
        dialog.deleteLater()

    def test_invalid_ranges_colors_and_canvas_sizes_fail_explicitly(self):
        base = _settings()
        with self.assertRaisesRegex(ValueError, "minimum"):
            replace(_axis(), minimum=2.0, maximum=1.0)
        with self.assertRaisesRegex(ValueError, "invalid plot color"):
            CurveVisualSettings(
                "not-a-color",
                2.0,
                PlotLineStyle.SOLID,
                False,
                "T(E)",
            )
        with self.assertRaisesRegex(ValueError, "canvas width"):
            CanvasVisualSettings(
                399,
                base.canvas.export_height,
                base.canvas.background_color,
                base.canvas.plot_background_color,
                base.canvas.frame_color,
                base.canvas.frame_width,
                base.canvas.left_margin,
                base.canvas.top_margin,
                base.canvas.right_margin,
                base.canvas.bottom_margin,
            )
        with self.assertRaisesRegex(ValueError, "major tick length"):
            replace(base.ticks, major_length=31.0)


def _axis() -> AxisVisualSettings:
    return AxisVisualSettings(
        minimum=-2.0,
        maximum=2.0,
        title="Energy",
        font_family="Arial",
        label_point_size=11.0,
        title_point_size=12.0,
        line_color="#000000",
        line_width=1.0,
        grid_visible=True,
        grid_color="#d0d0d0",
        grid_width=1.0,
        grid_style=PlotLineStyle.SOLID,
        mirror_visible=False,
    )


def _settings() -> TransmissionVisualSettings:
    return TransmissionVisualSettings(
        x_axis=_axis(),
        y_axis=replace(
            _axis(),
            minimum=1.0e-4,
            maximum=1.0,
            title="Transmission T(E)",
        ),
        ticks=TickVisualSettings(
            major_length=6.0,
            major_width=1.0,
            minor_visible=False,
            minor_length=3.5,
            minor_width=1.0,
            direction=TickDirection.OUTSIDE,
            x_major_tick_count=9,
            x_minor_tick_count=1,
            y_minor_tick_count=8,
        ),
        curve=CurveVisualSettings(
            "#2080c0",
            2.0,
            PlotLineStyle.SOLID,
            False,
            "T(E)",
        ),
        canvas=CanvasVisualSettings(
            1200,
            720,
            "#ffffff",
            "#ffffff",
            "#000000",
            1.0,
            84,
            18,
            18,
            24,
        ),
    )


if __name__ == "__main__":
    unittest.main()
