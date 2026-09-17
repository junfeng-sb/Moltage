import math
import unittest
from dataclasses import replace

from PySide6.QtCharts import QLogValueAxis
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from moltage.aims.transport_evidence import TransportSpinMode
from moltage.aitranss.transmission import (
    TransmissionPoint,
    TransmissionResult,
)
from moltage.gui.transmission_plot import (
    DEFAULT_TRANSMISSION_MAX,
    TransmissionPlotRangeError,
    transmission_at_fermi,
    transmission_plot_defaults,
)
from moltage.gui.transmission_view import (
    AxisRangeDialog,
    TransmissionWindow,
    _format_scientific_notation,
    _format_significant_value,
)
from moltage.gui.transmission_settings import (
    PlotLineStyle,
    TickDirection,
    TransmissionSettingsDialog,
)


class TransmissionPlotMathTests(unittest.TestCase):
    def test_three_significant_digit_scientific_display_uses_superscript(self):
        self.assertEqual(
            _format_scientific_notation(0.231843),
            "2.32 × 10⁻¹",
        )
        self.assertEqual(
            _format_scientific_notation(9.96),
            "9.96 × 10⁰",
        )

    def test_energy_readout_uses_three_significant_digits(self):
        self.assertEqual(_format_significant_value(0.0554255), "0.0554")
        self.assertEqual(_format_significant_value(-0.25), "−0.250")
        self.assertEqual(_format_significant_value(0.75), "0.750")
        self.assertEqual(_format_significant_value(0.0), "0.00")
        self.assertEqual(
            _format_significant_value(1234.0),
            "1.23 × 10³",
        )

    def test_default_y_minimum_uses_only_default_x_window(self):
        result = _result(
            (-3.0, 1.0e-10),
            (-1.0, 0.002),
            (1.0, 0.1),
        )

        defaults = transmission_plot_defaults(result)

        self.assertEqual(defaults.window_point_count, 2)
        self.assertEqual(defaults.minimum_positive_transmission, 0.002)
        self.assertEqual(defaults.minimum_exponent, -3)
        self.assertEqual(defaults.transmission_min, 1.0e-3)
        self.assertEqual(defaults.transmission_max, 10.0**0.5)

    def test_exact_decade_stays_on_that_decade(self):
        defaults = transmission_plot_defaults(
            _result((-1.0, 1.0e-4), (1.0, 0.2))
        )

        self.assertEqual(defaults.minimum_exponent, -4)
        self.assertEqual(defaults.transmission_min, 1.0e-4)

    def test_value_below_decade_uses_next_lower_floor(self):
        defaults = transmission_plot_defaults(
            _result((-1.0, 9.0e-5), (1.0, 0.2))
        )

        self.assertEqual(defaults.minimum_exponent, -5)
        self.assertEqual(defaults.transmission_min, 1.0e-5)

    def test_non_positive_values_are_ignored_without_mutation(self):
        result = _result((-1.0, 0.0), (0.0, -0.2), (1.0, 0.002))

        defaults = transmission_plot_defaults(result)

        self.assertEqual(defaults.minimum_positive_transmission, 0.002)
        self.assertEqual(
            tuple(point.transmission_per_spin for point in result.points),
            (0.0, -0.2, 0.002),
        )

    def test_missing_positive_value_in_default_window_is_plot_error_only(self):
        result = _result((-3.0, 0.1), (-1.0, 0.0), (1.0, -0.1), (3.0, 0.2))

        with self.assertRaisesRegex(
            TransmissionPlotRangeError,
            "No positive finite transmission",
        ):
            transmission_plot_defaults(result)

    def test_exact_zero_sample_wins(self):
        result = _result((-1.0, 0.1), (0.0, 0.25), (1.0, 0.9))

        value = transmission_at_fermi(result)

        self.assertTrue(value.exact)
        self.assertEqual(value.value, 0.25)
        self.assertIsNone(value.interpolation_weight)
        self.assertIsNone(value.lower_point)
        self.assertIsNone(value.upper_point)

    def test_symmetric_bracket_interpolates_linearly_in_raw_transmission(self):
        value = transmission_at_fermi(
            _result((-1.0, 0.2), (1.0, 0.6))
        )

        self.assertFalse(value.exact)
        self.assertEqual(value.interpolation_weight, 0.5)
        self.assertAlmostEqual(value.value, 0.4)

    def test_asymmetric_bracket_uses_distance_weight(self):
        value = transmission_at_fermi(
            _result((-0.25, 0.2), (0.75, 0.6))
        )

        self.assertEqual(value.interpolation_weight, 0.25)
        self.assertAlmostEqual(value.value, 0.3)

    def test_no_zero_bracket_is_unavailable_without_extrapolation(self):
        positive_only = transmission_at_fermi(
            _result((0.25, 0.2), (0.75, 0.6))
        )
        negative_only = transmission_at_fermi(
            _result((-0.75, 0.2), (-0.25, 0.6))
        )

        self.assertFalse(positive_only.available)
        self.assertFalse(negative_only.available)
        self.assertIsNone(positive_only.value)
        self.assertIsNone(negative_only.value)


class AxisRangeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_apply_requires_finite_increasing_range(self):
        dialog = _axis_dialog(positive=False)
        dialog.show()

        dialog._minimum.setText("2")
        dialog._maximum.setText("1")
        QTest.mouseClick(dialog._apply_button, Qt.MouseButton.LeftButton)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("less than", dialog._error.text())

        dialog._minimum.setText("nan")
        dialog._maximum.setText("3")
        QTest.mouseClick(dialog._apply_button, Qt.MouseButton.LeftButton)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("finite", dialog._error.text())

        dialog._minimum.setText("-1.5")
        dialog._maximum.setText("1.25")
        QTest.mouseClick(dialog._apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.selected_range, (-1.5, 1.25))

    def test_log_y_requires_positive_minimum_and_accepts_scientific_notation(self):
        dialog = _axis_dialog(positive=True)
        dialog.show()

        dialog._minimum.setText("0")
        dialog._maximum.setText("1e1")
        QTest.mouseClick(dialog._apply_button, Qt.MouseButton.LeftButton)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("greater than zero", dialog._error.text())

        dialog._minimum.setText("1e-5")
        dialog._maximum.setText("1e1")
        QTest.mouseClick(dialog._apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.selected_range, (1.0e-5, 1.0e1))


class TransmissionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = TransmissionWindow(
            "Synthetic.Project",
            "12345",
            "TE.dat",
            _plot_result(),
        )
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_default_log_axes_marker_and_raw_series(self):
        self.assertIsInstance(self.window._transmission_axis, QLogValueAxis)
        self.assertEqual(self.window._transmission_axis.base(), 10.0)
        self.assertEqual(self.window._energy_axis.min(), -2.0)
        self.assertEqual(self.window._energy_axis.max(), 2.0)
        self.assertEqual(self.window._transmission_axis.min(), 1.0e-3)
        self.assertAlmostEqual(
            self.window._transmission_axis.max(),
            DEFAULT_TRANSMISSION_MAX,
        )
        self.assertEqual(self.window._series.count(), 6)
        self.assertEqual(self.window._series.at(0).x(), -3.0)
        self.assertEqual(self.window._series.at(0).y(), 1.0e-10)
        self.assertEqual(self.window._fermi_marker.count(), 1)
        self.assertEqual(self.window._fermi_marker.at(0).x(), 0.0)
        self.assertAlmostEqual(self.window._fermi_marker.at(0).y(), 0.3)
        self.assertFalse(self.window._fermi_marker.pointLabelsVisible())
        self.assertEqual(
            self.window._chart_view._fermi_readout.text(),
            "T(E<sub>F</sub>) = 3.00 × 10⁻¹",
        )
        self.assertIn(
            "T(E<sub>F</sub>) = 3.00 × 10⁻¹",
            self.window._fermi_status.text(),
        )
        self.assertEqual(self.window._reference_series.at(0).x(), 0.0)
        self.assertEqual(self.window._reference_series.at(1).x(), 0.0)

    def test_fermi_annotation_stays_beside_marker_and_avoids_curve(self):
        plot_bounds = self.window._chart_view._chart_rect_in_view(
            self.window._chart.plotArea()
        )
        annotation = QRectF(self.window._chart_view._fermi_readout.geometry())
        marker = self.window._chart_view._point_in_view(
            self.window._fermi_marker.at(0)
        )
        nearest_x = min(max(marker.x(), annotation.left()), annotation.right())
        nearest_y = min(max(marker.y(), annotation.top()), annotation.bottom())
        marker_gap = math.hypot(marker.x() - nearest_x, marker.y() - nearest_y)

        self.assertTrue(self.window._chart_view._fermi_readout.isVisible())
        self.assertTrue(plot_bounds.contains(annotation))
        self.assertFalse(annotation.contains(marker))
        self.assertLessEqual(marker_gap, 18.0)
        self.assertEqual(
            self.window._chart_view._curve_intersection_count(
                annotation.adjusted(-4.0, -4.0, 4.0, 4.0)
            ),
            0,
        )

    def test_log_plot_uses_same_raw_linear_fermi_interpolation(self):
        self.assertIsInstance(self.window._transmission_axis, QLogValueAxis)
        self.assertEqual(
            self.window.fermi_transmission.interpolation_weight,
            0.25,
        )
        self.assertAlmostEqual(self.window.fermi_transmission.value, 0.3)

    def test_curve_hover_shows_nearest_sample_marker_and_readout(self):
        target = self.window._series.at(2)
        position = self.window._chart_view._point_in_view(target).toPoint()

        _send_mouse_move(self.window._chart_view.viewport(), QPoint(2, 2))
        _send_mouse_move(self.window._chart_view.viewport(), position)
        self.application.processEvents()

        self.assertEqual(self.window._probe_marker.count(), 1)
        self.assertEqual(self.window._probe_marker.at(0), target)
        self.assertTrue(self.window._chart_view._probe_readout.isVisible())
        self.assertIn(
            "E − E<sub>F</sub> = −0.250 eV",
            self.window._chart_view._probe_readout.text(),
        )
        self.assertIn(
            "T(E) = 2.00 × 10⁻¹",
            self.window._chart_view._probe_readout.text(),
        )

        _send_mouse_move(self.window._chart_view.viewport(), QPoint(2, 2))
        self.application.processEvents()

        self.assertEqual(self.window._probe_marker.count(), 0)
        self.assertFalse(self.window._chart_view._probe_readout.isVisible())

    def test_curve_hover_accepts_the_rendered_segment_between_samples(self):
        left = self.window._chart_view._point_in_view(self.window._series.at(2))
        right = self.window._chart_view._point_in_view(self.window._series.at(3))
        segment_midpoint = QPoint(
            round((left.x() + right.x()) / 2.0),
            round((left.y() + right.y()) / 2.0),
        )

        _send_mouse_move(self.window._chart_view.viewport(), QPoint(2, 2))
        _send_mouse_move(self.window._chart_view.viewport(), segment_midpoint)
        self.application.processEvents()

        self.assertEqual(self.window._probe_marker.count(), 1)
        self.assertIn(
            self.window._probe_marker.at(0),
            (self.window._series.at(2), self.window._series.at(3)),
        )

    def test_click_pins_probe_and_arrow_keys_move_between_visible_samples(self):
        target = self.window._series.at(2)
        position = self.window._chart_view._point_in_view(target).toPoint()

        QTest.mouseClick(
            self.window._chart_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=position,
        )
        _send_mouse_move(self.window._chart_view.viewport(), QPoint(2, 2))
        self.application.processEvents()

        self.assertEqual(self.window._probe_marker.at(0), target)
        self.assertIsNotNone(self.window._chart_view._pinned_probe_index)

        QTest.keyClick(self.window._chart_view, Qt.Key.Key_Right)
        self.application.processEvents()

        self.assertEqual(self.window._probe_marker.at(0), self.window._series.at(3))
        self.assertIn(
            "E − E<sub>F</sub> = 0.750 eV",
            self.window._chart_view._probe_readout.text(),
        )

    def test_clicking_empty_plot_space_clears_a_pinned_probe(self):
        target = self.window._series.at(2)
        target_position = self.window._chart_view._point_in_view(target).toPoint()
        QTest.mouseClick(
            self.window._chart_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=target_position,
        )
        plot_bounds = self.window._chart_view._chart_rect_in_view(
            self.window._chart.plotArea()
        )
        empty_position = QPoint(
            round(plot_bounds.left() + 20.0),
            round(plot_bounds.top() + 20.0),
        )
        self.assertIsNone(
            self.window._chart_view._probe_index_at(QPointF(empty_position))
        )

        QTest.mouseClick(
            self.window._chart_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=empty_position,
        )
        self.application.processEvents()

        self.assertIsNone(self.window._chart_view._pinned_probe_index)
        self.assertEqual(self.window._probe_marker.count(), 0)
        self.assertFalse(self.window._chart_view._probe_readout.isVisible())

    def test_log_axis_uses_superscript_power_of_ten_labels(self):
        self.assertFalse(self.window._transmission_axis.labelsVisible())
        labels = [
            label.text()
            for label in self.window._chart_view._log_tick_labels
            if label.isVisible()
        ]

        self.assertEqual(labels, ["10⁻³", "10⁻²", "10⁻¹", "10⁰"])
        self.assertTrue(all("E" not in label for label in labels))

    def test_superscript_labels_follow_an_edited_log_range(self):
        self.window._transmission_axis.setRange(1.0e-5, 1.0e1)
        self.application.processEvents()
        labels = [
            label.text()
            for label in self.window._chart_view._log_tick_labels
            if label.isVisible()
        ]

        self.assertEqual(
            labels,
            ["10⁻⁵", "10⁻⁴", "10⁻³", "10⁻²", "10⁻¹", "10⁰", "10¹"],
        )

    def test_plot_has_black_four_sided_frame_and_arial_typography(self):
        self.assertTrue(self.window._chart.isPlotAreaBackgroundVisible())
        self.assertEqual(
            self.window._chart.plotAreaBackgroundPen().color(),
            QColor("#000000"),
        )
        fonts = (
            self.window.font(),
            self.window._content.font(),
            self.window._chart.titleFont(),
            self.window._chart.legend().font(),
            self.window._energy_axis.labelsFont(),
            self.window._energy_axis.titleFont(),
            self.window._transmission_axis.labelsFont(),
            self.window._transmission_axis.titleFont(),
            self.window._chart_view._fermi_readout.font(),
            self.window._chart_view._probe_readout.font(),
            self.window._chart_view._transmission_title_label.font(),
            *(
                label.font()
                for label in self.window._chart_view._log_tick_labels
            ),
        )

        self.assertTrue(fonts)
        self.assertTrue(all(font.family() == "Arial" for font in fonts))
        self.assertEqual(self.window._energy_axis.labelsFont().pointSizeF(), 11.0)
        self.assertEqual(self.window._energy_axis.titleFont().pointSizeF(), 12.0)
        self.assertEqual(
            self.window._transmission_axis.labelsFont().pointSizeF(),
            11.0,
        )
        self.assertEqual(
            self.window._transmission_axis.titleFont().pointSizeF(),
            12.0,
        )
        self.assertEqual(
            self.window._energy_axis.titleText(),
            "Energy − E<sub>F</sub> (eV)",
        )

    def test_redundant_title_and_legend_are_hidden(self):
        self.assertEqual(self.window._chart.title(), "")
        self.assertEqual(self.window._series.name(), "")
        self.assertFalse(self.window._chart.legend().isVisible())

    def test_vertical_title_is_left_of_custom_log_tick_labels(self):
        title = self.window._chart_view._transmission_title_label
        tick_labels = [
            label
            for label in self.window._chart_view._log_tick_labels
            if label.isVisible()
        ]

        self.assertTrue(title.isVisible())
        self.assertTrue(tick_labels)
        self.assertLess(
            title.geometry().right(),
            min(label.geometry().left() for label in tick_labels),
        )

    def test_unavailable_fermi_value_has_no_extrapolated_marker(self):
        window = TransmissionWindow(
            "Synthetic.Project",
            "12345",
            "TE.dat",
            _result((0.25, 0.2), (0.75, 0.6), (1.0, 0.1)),
        )
        try:
            self.assertEqual(window._fermi_marker.count(), 0)
            self.assertIn(
                "T(E<sub>F</sub>) unavailable",
                window._fermi_status.text(),
            )
            self.assertIn("no extrapolation", window._fermi_status.text())
        finally:
            window.close()
            window.deleteLater()

    def test_non_positive_raw_samples_are_not_replaced_in_chart_series(self):
        window = TransmissionWindow(
            "Synthetic.Project",
            "12345",
            "TE.dat",
            _result((-1.0, 0.0), (0.0, -0.2), (1.0, 0.002)),
        )
        try:
            self.assertEqual(
                tuple(window._series.at(index).y() for index in range(3)),
                (0.0, -0.2, 0.002),
            )
            self.assertEqual(window._fermi_marker.count(), 0)
            self.assertIn("non-positive raw value", window._fermi_status.text())
        finally:
            window.close()
            window.deleteLater()

    def test_x_axis_double_click_opens_dialog_and_applies_range(self):
        observed = self._invoke_settings_dialog(
            "x",
            lambda dialog: _apply_settings_range(
                dialog,
                axis="x",
                minimum="-1.5",
                maximum="1.25",
            ),
        )

        self.assertEqual(observed, [("Transmission View Settings", 0)])
        self.assertEqual(self.window._energy_axis.min(), -1.5)
        self.assertEqual(self.window._energy_axis.max(), 1.25)

    def test_y_axis_double_click_opens_dialog_and_applies_scientific_range(self):
        observed = self._invoke_settings_dialog(
            "y",
            lambda dialog: _apply_settings_range(
                dialog,
                axis="y",
                minimum="1e-4",
                maximum="1e1",
            ),
        )

        self.assertEqual(observed, [("Transmission View Settings", 1)])
        self.assertEqual(self.window._transmission_axis.min(), 1.0e-4)
        self.assertEqual(self.window._transmission_axis.max(), 1.0e1)

    def test_curve_double_click_opens_curve_settings(self):
        position = self.window._chart_view._point_in_view(
            self.window._series.at(2)
        ).toPoint()
        observed = self._invoke_settings_at(
            position,
            lambda dialog: dialog.cancel_button.click(),
        )

        self.assertEqual(observed, [("Transmission View Settings", 3)])

    def test_blank_plot_double_click_opens_canvas_settings(self):
        plot_bounds = self.window._chart_view._chart_rect_in_view(
            self.window._chart.plotArea()
        )
        candidates = (
            QPointF(plot_bounds.left() + 16.0, plot_bounds.top() + 16.0),
            QPointF(plot_bounds.right() - 16.0, plot_bounds.bottom() - 16.0),
        )
        position = next(
            point.toPoint()
            for point in candidates
            if self.window._chart_view._probe_index_at(point) is None
        )
        observed = self._invoke_settings_at(
            position,
            lambda dialog: dialog.cancel_button.click(),
        )

        self.assertEqual(observed, [("Transmission View Settings", 4)])

    def test_cancel_leaves_current_x_range_unchanged(self):
        self.window._energy_axis.setRange(-1.25, 1.5)

        self._invoke_settings_dialog(
            "x",
            lambda dialog: QTest.mouseClick(
                dialog.cancel_button,
                Qt.MouseButton.LeftButton,
            ),
        )

        self.assertEqual(self.window._energy_axis.min(), -1.25)
        self.assertEqual(self.window._energy_axis.max(), 1.5)

    def test_x_edit_does_not_change_custom_y_range(self):
        self.window._transmission_axis.setRange(1.0e-4, 1.0e1)

        self._invoke_settings_dialog(
            "x",
            lambda dialog: _apply_settings_range(
                dialog,
                axis="x",
                minimum="-1",
                maximum="1",
            ),
        )

        self.assertEqual(self.window._transmission_axis.min(), 1.0e-4)
        self.assertEqual(self.window._transmission_axis.max(), 1.0e1)

    def test_comprehensive_styles_apply_without_changing_raw_data(self):
        before = tuple(
            (
                self.window._series.at(index).x(),
                self.window._series.at(index).y(),
            )
            for index in range(self.window._series.count())
        )
        settings = self.window._content.visual_settings
        selected = replace(
            settings,
            x_axis=replace(
                settings.x_axis,
                minimum=-1.5,
                maximum=1.5,
                title="Relative energy (eV)",
                font_family="Courier New",
                label_point_size=13.0,
                title_point_size=14.0,
                line_color="#2040a0",
                line_width=2.0,
                grid_visible=False,
                mirror_visible=True,
            ),
            y_axis=replace(
                settings.y_axis,
                minimum=1.0e-4,
                maximum=1.0,
                line_color="#305020",
                grid_color="#a0a0a0",
                grid_width=1.5,
                grid_style=PlotLineStyle.DOT,
                mirror_visible=True,
            ),
            ticks=replace(
                settings.ticks,
                major_length=10.0,
                major_width=2.0,
                minor_visible=True,
                minor_length=4.0,
                minor_width=1.5,
                direction=TickDirection.INSIDE,
                x_major_tick_count=7,
                x_minor_tick_count=2,
                y_minor_tick_count=4,
            ),
            curve=replace(
                settings.curve,
                color="#d02070",
                width=3.5,
                line_style=PlotLineStyle.DASH,
                legend_visible=True,
                legend_label="Calculated T(E)",
            ),
            canvas=replace(
                settings.canvas,
                export_width=760,
                export_height=480,
                background_color="#f4f5f6",
                plot_background_color="#fffef8",
                frame_color="#202020",
                frame_width=2.0,
                left_margin=92,
                top_margin=24,
                right_margin=52,
                bottom_margin=30,
            ),
        )

        self.window._content._apply_visual_settings(selected)
        self.application.processEvents()

        self.assertTrue(self.window._energy_mirror_axis.isVisible())
        self.assertTrue(self.window._transmission_mirror_axis.isVisible())
        self.assertFalse(self.window._energy_axis.isGridLineVisible())
        self.assertEqual(
            self.window._transmission_axis.gridLinePen().style(),
            Qt.PenStyle.DotLine,
        )
        self.assertEqual(self.window._series.pen().color(), QColor("#d02070"))
        self.assertEqual(self.window._series.pen().widthF(), 3.5)
        self.assertEqual(
            self.window._series.pen().style(),
            Qt.PenStyle.DashLine,
        )
        self.assertTrue(self.window._chart.legend().isVisible())
        self.assertEqual(self.window._series.name(), "Calculated T(E)")
        self.assertEqual(self.window._energy_axis.tickCount(), 7)
        tick_layout = self.window._chart_view.tick_layout()
        self.assertEqual(len(tick_layout["x_major"]), 7)
        self.assertEqual(len(tick_layout["x_minor"]), 12)
        self.assertGreater(
            tick_layout["x_major"][0].y1(),
            tick_layout["x_major"][0].y2(),
        )
        self.assertIn("x_mirror_major", tick_layout)
        self.assertIn("y_mirror_major", tick_layout)
        self.assertEqual(self.window.export_base_size().width(), 760)
        self.assertEqual(self.window.export_base_size().height(), 480)
        after = tuple(
            (
                self.window._series.at(index).x(),
                self.window._series.at(index).y(),
            )
            for index in range(self.window._series.count())
        )
        self.assertEqual(after, before)

    def test_capture_uses_configured_canvas_and_scale(self):
        settings = self.window._content.visual_settings
        self.window._content._apply_visual_settings(
            replace(
                settings,
                canvas=replace(
                    settings.canvas,
                    export_width=640,
                    export_height=400,
                ),
            )
        )

        image = self.window.capture_image(2)

        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (1280, 800))

    def test_workspace_reset_restores_both_frozen_axis_defaults(self):
        self.window._energy_axis.setRange(-0.5, 0.75)
        self.window._transmission_axis.setRange(1.0e-4, 1.0e1)

        self.window.reset_view()

        self.assertEqual(self.window._energy_axis.min(), -2.0)
        self.assertEqual(self.window._energy_axis.max(), 2.0)
        self.assertEqual(self.window._transmission_axis.min(), 1.0e-3)
        self.assertAlmostEqual(
            self.window._transmission_axis.max(),
            math.sqrt(10.0),
        )

    def _invoke_settings_dialog(self, axis, interaction):
        x_region, y_region = self.window._chart_view.axis_regions()
        position = (x_region if axis == "x" else y_region).center().toPoint()
        return self._invoke_settings_at(position, interaction)

    def _invoke_settings_at(self, position, interaction):
        observed: list[tuple[str, int]] = []

        def interact() -> None:
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, TransmissionSettingsDialog):
                observed.append((dialog.windowTitle(), dialog.tabs.currentIndex()))
                interaction(dialog)

        QTimer.singleShot(20, interact)
        QTest.mouseDClick(
            self.window._chart_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=position,
        )
        self.application.processEvents()
        return observed


def _apply_settings_range(
    dialog: TransmissionSettingsDialog,
    *,
    axis: str,
    minimum: str,
    maximum: str,
) -> None:
    page = dialog.x_axis_page if axis == "x" else dialog.y_axis_page
    page.minimum.setText(minimum)
    page.maximum.setText(maximum)
    QTest.mouseClick(dialog.ok_button, Qt.MouseButton.LeftButton)


def _send_mouse_move(widget, position: QPoint) -> None:
    global_position = widget.mapToGlobal(position)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(position),
        QPointF(global_position),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _axis_dialog(*, positive: bool) -> AxisRangeDialog:
    return AxisRangeDialog(
        title="Y Axis Range" if positive else "X Axis Range",
        minimum_label="Minimum",
        maximum_label="Maximum",
        current_range=(1.0e-3, 1.0) if positive else (-2.0, 2.0),
        default_range=(1.0e-3, math.sqrt(10.0)) if positive else (-2.0, 2.0),
        require_positive_minimum=positive,
        object_name="testAxisRangeDialog",
    )


def _plot_result() -> TransmissionResult:
    return _result(
        (-3.0, 1.0e-10),
        (-1.0, 0.002),
        (-0.25, 0.2),
        (0.75, 0.6),
        (1.0, 0.1),
        (3.0, 0.2),
    )


def _result(*samples: tuple[float, float]) -> TransmissionResult:
    points = tuple(
        TransmissionPoint(
            energy_hartree=-0.4 + index * 0.001,
            energy_relative_ev=energy,
            transmission_per_spin=transmission,
        )
        for index, (energy, transmission) in enumerate(samples)
    )
    return TransmissionResult(
        bias_volts=0.0,
        fermi_energy_hartree=-0.2,
        spin_mode=TransportSpinMode.NONE,
        header_lines=("synthetic spin", "synthetic metadata", "synthetic columns"),
        points=points,
    )


if __name__ == "__main__":
    unittest.main()
