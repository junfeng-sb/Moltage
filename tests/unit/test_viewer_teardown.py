"""Native Qt/VTK teardown regressions without suppressing VTK diagnostics."""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QApplication

from moltage.visualization.molecule_viewer import MoleculeViewerWidget


class ViewerTeardownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _dispose(self, viewer):
        viewer.close()
        viewer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def test_closes_interaction_timers_and_observer_before_finalize_once(self):
        viewer = MoleculeViewerWidget()
        viewer.show()
        self.application.processEvents()
        style = viewer._interactor.GetInteractorStyle()
        style.UseTimersOn()
        style.StartRotate()
        viewer._initial_background_timer.start(1000)
        viewer._hover_pick_timer.start()
        observer_id = viewer._scene._renderer_start_observer_id
        self.assertTrue(viewer._vtk_widget._Timer.isActive())
        self.assertEqual(viewer._interactor.GetEnabled(), 1)
        finalize = viewer._vtk_widget.Finalize

        with patch.object(
            viewer._vtk_widget,
            "Finalize",
            side_effect=finalize,
        ) as close:
            viewer.close()
            viewer.close()
            close.assert_called_once_with()
            self.assertFalse(viewer._initial_background_timer.isActive())
            self.assertFalse(viewer._hover_pick_timer.isActive())
            self.assertFalse(viewer._vtk_widget._Timer.isActive())
            self.assertEqual(style.GetState(), 0)
            self.assertEqual(style.GetEnabled(), 0)
            self.assertEqual(viewer._interactor.GetEnabled(), 0)
            self.assertIsNone(viewer._scene._renderer_start_observer_id)
            self.assertFalse(viewer._renderer.GetCommand(observer_id))

            viewer.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.application.processEvents()
            close.assert_called_once_with()

    def test_late_pick_and_hover_callbacks_do_not_render_or_emit_after_close(self):
        viewer = MoleculeViewerWidget()
        self.addCleanup(self._dispose, viewer)
        viewer.show()
        self.application.processEvents()
        render_window = viewer._vtk_widget.GetRenderWindow()
        renders = []
        observer = render_window.AddObserver("StartEvent", lambda *_: renders.append(True))
        viewer.close()
        renders.clear()  # StopState may render once before finalization.
        viewer._hover_pick_position = QPoint(20, 20)
        with patch.object(viewer, "_atom_index_at_display") as pick:
            viewer._pick_at(QPoint(20, 20))
            viewer._pick_hover_target()
            viewer._render_initial_background()
            viewer._position_torsion_angle_editor()
            pick.assert_not_called()
        self.assertEqual(renders, [])
        render_window.RemoveObserver(observer)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_native_active_rotation_and_multi_workspace_owner_teardown(self):
        script = textwrap.dedent("""\
            import gc
            from PySide6.QtCore import QCoreApplication, QEvent
            from PySide6.QtWidgets import QApplication
            from moltage.domain.connectivity import Bond, Connectivity
            from moltage.domain.structure import Atom, MolecularStructure
            from moltage.structure.covalent_radii import load_default_covalent_radii
            from moltage.gui.tight_binding_workspace import TightBindingWorkspace
            from moltage.visualization.molecule_viewer import MoleculeViewerWidget
            from tools.molecule_viewer_demo import MoleculeViewerDemo

            app = QApplication([])
            structure = MolecularStructure((Atom(0, 'N', 0, 0, 0), Atom(1, 'C', 1.4, 0, 0)))
            connectivity = Connectivity(2, (Bond(0, 1, 1.4),))
            radii = load_default_covalent_radii()
            for attempt in range(3):
                viewer = MoleculeViewerWidget()
                viewer.set_molecule(structure, connectivity, radii)
                viewer.show()
                app.processEvents()
                style = viewer._interactor.GetInteractorStyle()
                style.UseTimersOn()
                style.StartRotate()
                assert viewer._vtk_widget._Timer.isActive()
                viewer.close()
                assert not viewer._vtk_widget._Timer.isActive()
                assert viewer._interactor.GetEnabled() == 0
                assert style.GetState() == 0
                app.processEvents()
                viewer.close()
                viewer.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
                del style, viewer
                gc.collect()

            workspace = TightBindingWorkspace(structure, connectivity, (), radii, source_title='synthetic.xyz')
            workspace.show()
            app.processEvents()
            workspace.close()
            assert workspace.viewer._render_resources_released
            workspace.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

            main = MoleculeViewerDemo()
            main.show()
            main._load(__import__('pathlib').Path('tests/fixtures/phase1b/synthetic_dual_ncs.xyz'))
            main._create_geometry_workspace(identity=None, display_title='Synthetic', select=True)
            app.processEvents()
            viewers = main.findChildren(MoleculeViewerWidget)
            assert len(viewers) >= 2
            main.close()
            assert all(viewer._render_resources_released for viewer in viewers)
            main.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            del viewers, main, workspace
            gc.collect()

            # Continue exercising Qt after VTK owners are gone.  The original
            # regression corrupted the native heap during viewer teardown and
            # was only detected by a later, unrelated dialog/GC cycle.
            from PySide6.QtWidgets import QDialog
            for _ in range(3):
                dialog = QDialog()
                dialog.show()
                app.processEvents()
                dialog.close()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
                del dialog
                gc.collect()
            print('native teardown complete', flush=True)
        """)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=20,
        )
        # Capture for assertions, but keep every diagnostic available to pytest.
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        evidence = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, evidence)
        self.assertIn("native teardown complete", completed.stdout)
        for error in (
            "wglMakeCurrent failed", "failed to get valid pixel format",
            "Failed to initialize OpenGL functions",
        ):
            self.assertNotIn(error, evidence)
