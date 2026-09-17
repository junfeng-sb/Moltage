import gc
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from moltage.visualization.molecule_scene import PreviewPickTarget
from moltage.visualization.molecule_viewer import MoleculeViewerWidget


class PreviewTargetViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.viewer = MoleculeViewerWidget()
        cls.viewer.resize(480, 360)
        cls.hovered = []
        cls.clicked = []
        cls.cleared = []
        cls.viewer.preview_target_hovered.connect(cls.hovered.append)
        cls.viewer.preview_target_clicked.connect(cls.clicked.append)
        cls.viewer.preview_target_cleared.connect(lambda: cls.cleared.append(True))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.viewer.close()
        cls.viewer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()
        cls.viewer = None
        gc.collect()

    def setUp(self) -> None:
        self.hovered.clear()
        self.clicked.clear()
        self.cleared.clear()
        self.viewer.set_preview_target_picking_enabled(False)
        self.viewer.set_preview_pick_targets(())

    def test_hover_and_click_emit_the_same_opaque_token(self) -> None:
        token = ("LEFT", 2, (-1, 2, 1))
        target = PreviewPickTarget(token, (1.0, 2.0, 3.0), 0.5)
        self.viewer.set_preview_pick_targets((target,))
        self.viewer.set_preview_target_picking_enabled(True)

        with (
            patch.object(
                self.viewer,
                "_display_position",
                return_value=(120, 90),
            ),
            patch.object(
                self.viewer._scene,
                "pick_preview_target",
                return_value=token,
            ) as pick_target,
        ):
            self.viewer._hover_pick_position = QPoint(10, 20)
            self.viewer._pick_hover_target()
            self.viewer._pick_at(QPoint(10, 20))

        self.assertEqual(self.hovered, [token])
        self.assertEqual(self.clicked, [token])
        self.assertTrue(
            all(call.args[2] == (target,) for call in pick_target.call_args_list)
        )

    def test_no_hit_drag_leave_and_mode_exit_clear_safely(self) -> None:
        token = object()
        target = PreviewPickTarget(token, (0.0, 0.0, 0.0), 0.5)
        self.viewer.set_preview_pick_targets((target,))
        self.viewer.set_preview_target_picking_enabled(True)
        self.viewer._set_hover_target(("preview", token))

        drag = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(4.0, 5.0),
            QPointF(4.0, 5.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.viewer.eventFilter(self.viewer._vtk_widget, drag)
        self.assertIsNone(self.viewer._hover_target)

        self.viewer._set_hover_target(("preview", token))
        self.viewer.eventFilter(
            self.viewer._vtk_widget,
            QEvent(QEvent.Type.Leave),
        )
        self.assertIsNone(self.viewer._hover_target)

        self.viewer._set_hover_target(("preview", token))
        self.viewer.set_preview_target_picking_enabled(False)
        self.assertIsNone(self.viewer._hover_target)
        self.assertGreaterEqual(len(self.cleared), 3)

        self.viewer.set_preview_target_picking_enabled(True)
        with (
            patch.object(
                self.viewer,
                "_display_position",
                return_value=(120, 90),
            ),
            patch.object(
                self.viewer._scene,
                "pick_preview_target",
                return_value=None,
            ),
        ):
            self.viewer._hover_pick_position = QPoint(10, 20)
            self.viewer._pick_hover_target()
            self.viewer._pick_at(QPoint(10, 20))
        self.assertEqual(self.clicked, [])


if __name__ == "__main__":
    unittest.main()
