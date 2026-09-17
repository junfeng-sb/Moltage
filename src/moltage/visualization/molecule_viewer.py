"""Reusable PySide6 widget embedding the VTK molecular scene."""

from collections.abc import Iterable, Mapping

import numpy as np

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QDoubleValidator,
    QFocusEvent,
    QImage,
    QKeyEvent,
    QPalette,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from vtkmodules import vtkRenderingOpenGL2  # noqa: F401
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.util.numpy_support import vtk_to_numpy
from vtkmodules.vtkRenderingCore import vtkRenderer, vtkWindowToImageFilter

from moltage.domain.connectivity import Connectivity
from moltage.domain.bond_display import BondDisplayOrder
from moltage.domain.structure import MolecularStructure
from moltage.structure.cube import CubeScalarField
from moltage.visualization.bond_torsion import (
    drag_angle_from_total_displacement,
    normalize_signed_degrees,
)
from moltage.visualization.molecule_scene import (
    AngleAnnotation,
    DistanceAnnotation,
    MeasurementOverlayAnnotation,
    MINIMUM_ATOM_PICK_RADIUS_PIXELS,
    LatticeExtensionPreviewGuide,
    MoleculeScene,
    PreviewAtom,
    PreviewPickTarget,
    TorsionGizmo,
    VisualizationError,
)
from moltage.visualization.view_preferences import (
    ATOM_HIGHLIGHT_COLORS_PROPERTY,
    DEFAULT_ATOM_HIGHLIGHT_COLORS,
    AtomHighlightColors,
    ViewPreferences,
)
from moltage.visualization.orbital_surface import (
    OrbitalSurfacePreferences,
)


class _TorsionAngleInput(QLineEdit):
    """Keep direct torsion editing local to the on-canvas editor."""

    cancel_requested = Signal()
    edit_requested = Signal()

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self.edit_requested.emit()

    def event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and event.key() == Qt.Key.Key_Escape
        ):
            self.cancel_requested.emit()
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MoleculeViewerWidget(QWidget):
    """Display molecular atoms and bonds using reusable batched VTK pipelines."""

    atom_picked = Signal(int)
    bond_picked = Signal(int, int)
    atom_hovered = Signal(int)
    bond_hovered = Signal(int, int)
    hover_cleared = Signal()
    preview_target_hovered = Signal(object)
    preview_target_clicked = Signal(object)
    preview_target_cleared = Signal()
    torsion_drag_started = Signal()
    torsion_drag_changed = Signal(float)
    torsion_drag_finished = Signal()
    torsion_side_switch_requested = Signal()
    torsion_numeric_edit_requested = Signal()
    torsion_numeric_commit_requested = Signal()
    torsion_numeric_cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._render_resources_released = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._vtk_widget = QVTKRenderWindowInteractor(self)
        layout.addWidget(self._vtk_widget)

        self._torsion_angle_editor = QFrame(self._vtk_widget)
        self._torsion_angle_editor.setObjectName("torsionAngleEditor")
        self._torsion_angle_editor.setFrameShape(QFrame.Shape.StyledPanel)
        angle_layout = QHBoxLayout(self._torsion_angle_editor)
        angle_layout.setContentsMargins(4, 1, 3, 1)
        angle_layout.setSpacing(1)
        self._torsion_angle_input = _TorsionAngleInput(
            self._torsion_angle_editor
        )
        self._torsion_angle_input.setObjectName("torsionAngleInput")
        self._torsion_angle_input.setAccessibleName("Relative torsion angle")
        self._torsion_angle_input.setToolTip(
            "Enter the signed relative torsion angle in degrees"
        )
        self._torsion_angle_input.setValidator(
            QDoubleValidator(
                -1.0e9,
                1.0e9,
                6,
                self._torsion_angle_input,
            )
        )
        self._torsion_angle_input.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._torsion_angle_input.setFixedWidth(68)
        angle_layout.addWidget(self._torsion_angle_input)
        degree_label = QLabel("°", self._torsion_angle_editor)
        degree_label.setObjectName("torsionAngleDegreeLabel")
        angle_layout.addWidget(degree_label)
        self._torsion_angle_editor.setFixedHeight(28)
        self._torsion_angle_editor.adjustSize()
        self._torsion_angle_editor.hide()
        self._torsion_angle_input.edit_requested.connect(
            self.torsion_numeric_edit_requested.emit
        )
        self._torsion_angle_input.returnPressed.connect(
            self.torsion_numeric_commit_requested.emit
        )
        self._torsion_angle_input.cancel_requested.connect(
            self.torsion_numeric_cancel_requested.emit
        )

        self._renderer = vtkRenderer()
        render_window = self._vtk_widget.GetRenderWindow()
        render_window.SetMultiSamples(4)
        render_window.AddRenderer(self._renderer)

        self._interactor = render_window.GetInteractor()
        self._interactor.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self._scene = MoleculeScene(self._renderer)
        self._apply_palette_background()
        self._apply_atom_highlight_colors()
        self._interactor.Initialize()
        self._initial_background_rendered = False
        self._initial_background_timer = QTimer(self)
        self._initial_background_timer.setSingleShot(True)
        self._initial_background_timer.timeout.connect(
            self._render_initial_background
        )
        self._left_press_position: QPoint | None = None
        self._bond_rotation_enabled = False
        self._bond_picking_enabled = False
        # Every molecular canvas exposes atom hover feedback. Workspaces that
        # explicitly opt in through set_hover_picking_enabled also get the
        # pre-existing bond-hover fallback used by the tight-binding editor.
        self._hover_picking_enabled = True
        self._hover_bond_picking_enabled = False
        self._hover_pick_position: QPoint | None = None
        self._hover_target: tuple[str, object] | None = None
        self._preview_pick_targets: tuple[PreviewPickTarget, ...] = ()
        self._preview_target_picking_enabled = False
        self._hover_pick_timer = QTimer(self)
        self._hover_pick_timer.setSingleShot(True)
        self._hover_pick_timer.setInterval(40)
        self._hover_pick_timer.timeout.connect(self._pick_hover_target)
        self._torsion_press_position: QPoint | None = None
        self._torsion_press_target: str | None = None
        self._torsion_press_edge: tuple[int, int] | None = None
        self._torsion_drag_direction: tuple[float, float] | None = None
        self._torsion_handle_pointer_angle: float | None = None
        self._torsion_handle_drag_delta = 0.0
        self._torsion_drag_moved = False
        self._selected_torsion_edge: tuple[int, int] | None = None
        self._torsion_angle_editing = False
        self._vtk_widget.installEventFilter(self)
        self._vtk_widget.setMouseTracking(True)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._initial_background_rendered:
            self._initial_background_rendered = True
            self._initial_background_timer.start(0)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        } and hasattr(self, "_renderer"):
            self._apply_palette_background()
            self._apply_atom_highlight_colors()

    def _apply_palette_background(self) -> None:
        color = self.palette().color(QPalette.ColorRole.Base)
        self._renderer.SetBackground(color.redF(), color.greenF(), color.blueF())

    def _apply_atom_highlight_colors(self) -> None:
        application = QApplication.instance()
        colors = (
            None
            if application is None
            else application.property(ATOM_HIGHLIGHT_COLORS_PROPERTY)
        )
        if colors is None:
            colors = DEFAULT_ATOM_HIGHLIGHT_COLORS
        if not isinstance(colors, AtomHighlightColors):
            raise RuntimeError(
                "application atom-highlight colors have an invalid type"
            )
        self._scene.set_atom_highlight_colors(colors)

    def _render_initial_background(self) -> None:
        if not self._render_resources_released and self.isVisible():
            self._render_if_active()

    def _render_if_active(self) -> None:
        """Render only while the native VTK window remains owned and valid."""

        if self._render_resources_released:
            return
        render_window = self._vtk_widget.GetRenderWindow()
        if render_window is not None:
            render_window.Render()

    @property
    def torsion_angle_input(self) -> QLineEdit:
        """Return the directly editable on-canvas torsion angle field."""

        return self._torsion_angle_input

    def set_molecule(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        covalent_radii: Mapping[str, float],
        bond_display_orders: Iterable[BondDisplayOrder] | None = None,
        *,
        scalar_field: CubeScalarField | None = None,
        orbital_preferences: OrbitalSurfacePreferences | None = None,
    ) -> None:
        """Replace the displayed molecule and fit it in the current viewport."""

        self._scene.set_molecule(
            structure,
            connectivity,
            covalent_radii,
            bond_display_orders,
        )
        self._scene.set_orbital_surface(
            scalar_field,
            orbital_preferences,
        )
        self._reset_hover_tracking()
        self._selected_torsion_edge = None
        self._cancel_torsion_pointer_state()
        self._torsion_angle_editing = False
        self._scene.set_torsion_angle_text_visible(True)
        self._torsion_angle_editor.hide()
        self.reset_camera()

    def replace_molecule_preserving_view(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        covalent_radii: Mapping[str, float],
        bond_display_orders: Iterable[BondDisplayOrder] | None = None,
        *,
        scalar_field: CubeScalarField | None = None,
        orbital_preferences: OrbitalSurfacePreferences | None = None,
    ) -> None:
        """Replace atom identity/topology without moving the current camera."""

        self._scene.set_molecule(
            structure,
            connectivity,
            covalent_radii,
            bond_display_orders,
        )
        self._scene.set_orbital_surface(
            scalar_field,
            orbital_preferences,
        )
        self._reset_hover_tracking()
        self._selected_torsion_edge = None
        self._cancel_torsion_pointer_state()
        self._torsion_angle_editing = False
        self._scene.set_torsion_angle_text_visible(True)
        self._torsion_angle_editor.hide()
        self._renderer.ResetCameraClippingRange()
        self._render_if_active()

    def clear(self) -> None:
        """Remove the displayed molecule without rebuilding rendering pipelines."""

        self._scene.clear()
        self._reset_hover_tracking()
        self._selected_torsion_edge = None
        self._cancel_torsion_pointer_state()
        self._torsion_angle_editing = False
        self._scene.set_torsion_angle_text_visible(True)
        self._torsion_angle_editor.hide()
        self._render_if_active()

    def update_molecule_coordinates(self, structure: MolecularStructure) -> None:
        """Redraw coordinate-only edits without resetting topology or camera."""

        self._scene.update_molecule_coordinates(structure)
        self._renderer.ResetCameraClippingRange()
        self._render_if_active()
        self._position_torsion_angle_editor()

    def update_connectivity(
        self,
        connectivity: Connectivity,
        bond_display_orders: Iterable[BondDisplayOrder] | None = None,
    ) -> None:
        """Redraw logical bonds without resetting camera or viewer overlays."""

        self._scene.update_connectivity(connectivity, bond_display_orders)
        edges = {
            (bond.first_index, bond.second_index) for bond in connectivity
        }
        if (
            self._selected_torsion_edge is not None
            and self._selected_torsion_edge not in edges
        ):
            self._selected_torsion_edge = None
            self._cancel_torsion_pointer_state()
        self._render_if_active()
        self._position_torsion_angle_editor()

    def set_view_preferences(self, preferences: ViewPreferences) -> None:
        """Refresh visual preferences while preserving camera and model state."""

        self._scene.set_view_preferences(preferences)
        self._renderer.ResetCameraClippingRange()
        self._render_if_active()
        self._position_torsion_angle_editor()

    def set_orbital_surface_preferences(
        self,
        preferences: OrbitalSurfacePreferences,
    ) -> None:
        """Refresh the active Cube surface while preserving camera state."""

        self._scene.set_orbital_surface_preferences(preferences)
        self._renderer.ResetCameraClippingRange()
        self._render_if_active()

    def set_orbital_surface(
        self,
        scalar_field: CubeScalarField | None,
        preferences: OrbitalSurfacePreferences | None = None,
    ) -> None:
        """Replace only the display field while preserving molecule and camera."""

        self._scene.set_orbital_surface(scalar_field, preferences)
        self._renderer.ResetCameraClippingRange()
        self._render_if_active()

    def set_bond_rotation_enabled(self, enabled: bool) -> None:
        """Give or release exclusive left-mouse ownership for bond rotation."""

        if not isinstance(enabled, bool):
            raise TypeError("bond rotation enabled state must be boolean")
        self._bond_rotation_enabled = enabled
        self._left_press_position = None
        self._cancel_torsion_pointer_state()
        if enabled:
            self._clear_hover_target()
        if not enabled:
            self.clear_torsion_gizmo()

    def set_bond_picking_enabled(self, enabled: bool) -> None:
        """Enable ordinary click selection of displayed connectivity edges."""

        if not isinstance(enabled, bool):
            raise TypeError("bond picking enabled state must be boolean")
        self._bond_picking_enabled = enabled
        self._left_press_position = None

    def set_hover_picking_enabled(self, enabled: bool) -> None:
        """Enable throttled pointer hover feedback for displayed atoms and bonds."""

        if not isinstance(enabled, bool):
            raise TypeError("hover picking enabled state must be boolean")
        self._hover_picking_enabled = enabled
        self._hover_bond_picking_enabled = enabled
        self._vtk_widget.setMouseTracking(
            enabled or self._preview_target_picking_enabled
        )
        if not enabled:
            self._clear_hover_target()

    def set_preview_target_picking_enabled(self, enabled: bool) -> None:
        """Give one preview tool exclusive hover and click target selection."""

        if not isinstance(enabled, bool):
            raise TypeError("preview target picking enabled state must be boolean")
        self._preview_target_picking_enabled = enabled
        self._left_press_position = None
        self._vtk_widget.setMouseTracking(enabled or self._hover_picking_enabled)
        self._clear_hover_target()

    def set_torsion_gizmo(self, gizmo: TorsionGizmo) -> None:
        """Show one selected connectivity edge and its editing references."""

        self._scene.set_torsion_gizmo(gizmo)
        self._selected_torsion_edge = tuple(
            sorted((gizmo.fixed_atom_index, gizmo.rotating_atom_index))
        )
        self._scene.set_torsion_angle_text_visible(
            not self._torsion_angle_editing
        )
        self._torsion_angle_input.setText(
            "0.0"
            if abs(gizmo.angle_degrees) <= 5.0e-13
            else f"{gizmo.angle_degrees:+.1f}"
        )
        self._render_if_active()
        self._position_torsion_angle_editor()

    def begin_torsion_angle_edit(self) -> None:
        """Replace the VTK display value with one opaque Qt editor."""

        if self._selected_torsion_edge is None:
            return
        self._torsion_angle_editing = True
        self._scene.set_torsion_angle_text_visible(False)
        self._render_if_active()
        self._position_torsion_angle_editor()

    def end_torsion_angle_edit(self) -> None:
        """Return from the Qt editor to the VTK-rendered display value."""

        self._torsion_angle_editing = False
        self._torsion_angle_editor.hide()
        self._scene.set_torsion_angle_text_visible(
            self._selected_torsion_edge is not None
        )
        self._render_if_active()

    def clear_torsion_gizmo(self) -> None:
        """Clear selected-bond interaction visuals only."""

        self._scene.clear_torsion_gizmo()
        self._selected_torsion_edge = None
        self._cancel_torsion_pointer_state()
        self._torsion_angle_editing = False
        self._scene.set_torsion_angle_text_visible(True)
        self._torsion_angle_editor.hide()
        self._render_if_active()

    def torsion_reference_direction(
        self,
        fixed_atom_index: int,
        rotating_atom_index: int,
    ) -> tuple[float, float, float]:
        return self._scene.reference_direction_for_bond(
            fixed_atom_index,
            rotating_atom_index,
        )

    def set_highlighted_atom_indices(
        self,
        primary_indices: Iterable[int],
        secondary_indices: Iterable[int] = (),
    ) -> None:
        """Apply generic shell highlights without changing molecular data."""

        self._scene.set_highlighted_atom_indices(
            primary_indices,
            secondary_indices,
        )
        self._render_if_active()

    def set_grouped_atom_indices(
        self,
        primary_indices: Iterable[int],
        secondary_indices: Iterable[int] = (),
    ) -> None:
        """Show fragment membership with open rings and 1-based labels."""

        self._scene.set_grouped_atom_indices(
            primary_indices,
            secondary_indices,
        )
        self._render_if_active()

    def set_preview_atoms(
        self,
        preview_atoms: Iterable[PreviewAtom],
        *,
        hidden_atom_indices: Iterable[int] = (),
    ) -> None:
        """Apply a visual-only replacement preview without molecular edits."""

        self._scene.set_preview_atoms(
            preview_atoms,
            hidden_atom_indices=hidden_atom_indices,
        )
        self._render_if_active()

    def set_lattice_extension_preview_guide(
        self,
        guide: LatticeExtensionPreviewGuide | None,
    ) -> None:
        """Show or clear the visual-only Au lattice hover guide."""

        self._scene.set_lattice_extension_preview_guide(guide)
        self._render_if_active()

    def set_preview_pick_targets(
        self,
        targets: Iterable[PreviewPickTarget],
    ) -> None:
        """Replace the cached screen-pickable positions used by preview tools."""

        validated = tuple(targets)
        for target in validated:
            if not isinstance(target, PreviewPickTarget):
                raise TypeError(
                    "preview pick targets must contain PreviewPickTarget values"
                )
        self._preview_pick_targets = validated
        if self._hover_target is not None and self._hover_target[0] == "preview":
            self._clear_hover_target()

    def set_annotations(
        self,
        distance_annotations: Iterable[DistanceAnnotation],
        angle_annotations: Iterable[AngleAnnotation] = (),
    ) -> None:
        """Apply generic measured 3D annotations without topology changes."""

        self._scene.set_annotations(distance_annotations, angle_annotations)
        self._render_if_active()

    def set_measurement_annotations(
        self,
        annotations: Iterable[MeasurementOverlayAnnotation],
        *,
        pick_feedback: Iterable[int] | None = None,
    ) -> None:
        """Apply session-local overlays independently of scientific previews."""

        self._scene.set_measurement_annotations(annotations)
        if pick_feedback is not None:
            self._scene.set_measurement_pick_feedback(pick_feedback)
        self._render_if_active()

    def set_measurement_pick_feedback(
        self,
        atom_indices: Iterable[int],
    ) -> None:
        """Apply temporary ordered measurement picks as an independent layer."""

        self._scene.set_measurement_pick_feedback(atom_indices)
        self._render_if_active()

    def set_surface_verification(
        self,
        left_indices: Iterable[int],
        right_indices: Iterable[int],
    ) -> None:
        """Show independent logical surface shells and 1-based L/R labels."""

        self._scene.set_surface_verification(left_indices, right_indices)
        self._render_if_active()

    def set_parameter_labels(
        self,
        atom_labels: Mapping[int, str],
        bond_labels: Mapping[tuple[int, int], str],
    ) -> None:
        """Show batched parameter labels anchored to atoms and bond midpoints."""

        self._scene.set_parameter_labels(atom_labels, bond_labels)
        self._render_if_active()

    def reset_camera(self) -> None:
        """Fit the current molecule using VTK's camera implementation."""

        self._renderer.ResetCamera()
        self._renderer.ResetCameraClippingRange()
        self._render_if_active()
        self._position_torsion_angle_editor()

    def export_pixel_size(self) -> QSize:
        """Return the current VTK framebuffer size used as the 1x export base."""

        width, height = self._vtk_widget.GetRenderWindow().GetSize()
        return QSize(max(1, int(width)), max(1, int(height)))

    def capture_image(self, scale_factor: int = 1) -> QImage:
        """Capture the unchanged current VTK scene at an integer pixel scale."""

        if not isinstance(scale_factor, int) or not 1 <= scale_factor <= 8:
            raise ValueError("molecular view export scale must be between 1 and 8")
        render_window = self._vtk_widget.GetRenderWindow()
        width, height = render_window.GetSize()
        if width <= 0 or height <= 0:
            raise VisualizationError("The molecular view has no renderable size.")
        render_window.Render()
        capture = vtkWindowToImageFilter()
        capture.SetInput(render_window)
        capture.SetInputBufferTypeToRGBA()
        capture.ReadFrontBufferOff()
        capture.SetScale(scale_factor)
        capture.Update()
        output = capture.GetOutput()
        output_width, output_height, _depth = output.GetDimensions()
        scalars = output.GetPointData().GetScalars()
        if scalars is None or scalars.GetNumberOfComponents() != 4:
            raise VisualizationError("VTK did not produce an RGBA image.")
        pixels = vtk_to_numpy(scalars).reshape(
            output_height,
            output_width,
            4,
        )
        pixels = np.ascontiguousarray(np.flipud(pixels))
        # VTK's off-screen alpha channel can remain zero even though RGB has
        # already been composited against the renderer background. Export an
        # opaque final presentation so PNG and JPEG match the visible canvas.
        pixels[:, :, 3] = 255
        image = QImage(
            pixels.data,
            output_width,
            output_height,
            output_width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        if image.isNull():
            raise VisualizationError("VTK produced an empty image.")
        dots_per_meter = round(96 * scale_factor / 0.0254)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        return image

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Distinguish an atom-inspection click from trackball dragging."""

        if watched is self._vtk_widget:
            if self._render_resources_released:
                return super().eventFilter(watched, event)
            if event.type() in {
                QEvent.Type.Resize,
                QEvent.Type.Wheel,
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease,
            }:
                QTimer.singleShot(0, self._position_torsion_angle_editor)
            if (
                self._hover_picking_enabled
                or self._preview_target_picking_enabled
            ):
                if (
                    event.type() == QEvent.Type.MouseMove
                    and event.buttons() == Qt.MouseButton.NoButton
                    and not self._bond_rotation_enabled
                ):
                    self._hover_pick_position = event.position().toPoint()
                    if not self._hover_pick_timer.isActive():
                        self._hover_pick_timer.start()
                elif event.type() in {
                    QEvent.Type.Leave,
                    QEvent.Type.Resize,
                    QEvent.Type.Wheel,
                } or (
                    event.type() == QEvent.Type.MouseMove
                    and event.buttons() != Qt.MouseButton.NoButton
                ):
                    self._clear_hover_target()
            if self._bond_rotation_enabled and self._handle_torsion_event(event):
                return True
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._left_press_position = event.position().toPoint()
            elif (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                release_position = event.position().toPoint()
                press_position = self._left_press_position
                self._left_press_position = None
                if (
                    press_position is not None
                    and (
                        release_position - press_position
                    ).manhattanLength()
                    <= QApplication.startDragDistance()
                ):
                    QTimer.singleShot(
                        0,
                        lambda point=release_position: self._pick_at(point),
                    )
        return super().eventFilter(watched, event)

    def _handle_torsion_event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = event.position().toPoint()
            display_position = self._display_position(point)
            if display_position is None:
                return False
            display_x, display_y = display_position
            target = self._scene.pick_torsion_target(display_x, display_y)
            edge = self._scene.pick_bond_edge(display_x, display_y)
            if (
                target is None
                and edge is not None
                and edge == self._selected_torsion_edge
            ):
                target = "selected_bond"
            if target is None and edge is None:
                return False
            self._torsion_press_position = point
            self._torsion_press_target = target
            self._torsion_press_edge = edge
            self._torsion_drag_moved = False
            if target in {
                "selected_bond",
                "circle",
                "moving_ray",
            }:
                vtk_direction = self._scene.torsion_drag_direction(
                    target,
                    display_x,
                    display_y,
                )
                self._torsion_drag_direction = (
                    vtk_direction[0],
                    -vtk_direction[1],
                )
                self.torsion_drag_started.emit()
            elif target == "rotation_handle":
                try:
                    self._torsion_handle_pointer_angle = (
                        self._scene.torsion_pointer_angle_degrees(
                            display_x,
                            display_y,
                        )
                    )
                except VisualizationError:
                    self._cancel_torsion_pointer_state()
                    return True
                self._torsion_handle_drag_delta = 0.0
                self.torsion_drag_started.emit()
            return True
        if event.type() == QEvent.Type.MouseMove:
            press = self._torsion_press_position
            target = self._torsion_press_target
            if target == "rotation_handle":
                previous_angle = self._torsion_handle_pointer_angle
                if press is None or previous_angle is None:
                    return True
                point = event.position().toPoint()
                displacement = point - press
                if displacement.manhattanLength() > 3:
                    display_position = self._display_position(point)
                    if display_position is None:
                        return True
                    try:
                        pointer_angle = (
                            self._scene.torsion_pointer_angle_degrees(
                                *display_position
                            )
                        )
                    except VisualizationError:
                        return True
                    self._torsion_drag_moved = True
                    self._torsion_handle_drag_delta += normalize_signed_degrees(
                        pointer_angle - previous_angle
                    )
                    self._torsion_handle_pointer_angle = pointer_angle
                    self.torsion_drag_changed.emit(
                        self._torsion_handle_drag_delta
                    )
                return True
            direction = self._torsion_drag_direction
            if (
                press is None
                or direction is None
                or target not in {
                    "selected_bond",
                    "circle",
                    "moving_ray",
                }
            ):
                return self._torsion_press_edge is not None or target is not None
            point = event.position().toPoint()
            displacement = point - press
            if displacement.manhattanLength() > 3:
                self._torsion_drag_moved = True
                angle_delta = drag_angle_from_total_displacement(
                    float(displacement.x()),
                    float(displacement.y()),
                    direction,
                )
                self.torsion_drag_changed.emit(angle_delta)
            return True
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if self._torsion_press_position is None:
                return False
            press = self._torsion_press_position
            target = self._torsion_press_target
            edge = self._torsion_press_edge
            moved = self._torsion_drag_moved
            release = event.position().toPoint()
            click_without_drag = (release - press).manhattanLength() <= 3
            was_drag_target = target in {
                "selected_bond",
                "circle",
                "moving_ray",
                "rotation_handle",
            }
            self._cancel_torsion_pointer_state()
            if was_drag_target:
                self.torsion_drag_finished.emit()
                if target == "moving_ray" and click_without_drag and not moved:
                    self.torsion_numeric_edit_requested.emit()
                return True
            if click_without_drag and target == "side_arrow":
                self.torsion_side_switch_requested.emit()
            elif click_without_drag and target == "angle_label":
                self.torsion_numeric_edit_requested.emit()
            elif click_without_drag and edge is not None:
                self.bond_picked.emit(*edge)
            return True
        return False

    def _cancel_torsion_pointer_state(self) -> None:
        self._torsion_press_position = None
        self._torsion_press_target = None
        self._torsion_press_edge = None
        self._torsion_drag_direction = None
        self._torsion_handle_pointer_angle = None
        self._torsion_handle_drag_delta = 0.0
        self._torsion_drag_moved = False

    def _pick_at(self, point: QPoint) -> None:
        if self._render_resources_released:
            return
        display_position = self._display_position(point)
        if display_position is None:
            return
        display_x, display_y = display_position
        if self._preview_target_picking_enabled:
            target = self._preview_target_at_display(display_x, display_y)
            if target is not None:
                self.preview_target_clicked.emit(target)
            return
        if self._bond_picking_enabled:
            edge = self._scene.pick_bond_edge(display_x, display_y)
            if edge is not None:
                self.bond_picked.emit(*edge)
                return
        atom_index = self._atom_index_at_display(display_x, display_y)
        if atom_index is not None:
            self.atom_picked.emit(atom_index)

    @Slot()
    def _pick_hover_target(self) -> None:
        if self._render_resources_released:
            return
        point = self._hover_pick_position
        if (
            not self._hover_picking_enabled
            and not self._preview_target_picking_enabled
        ) or point is None:
            return
        display_position = self._display_position(point)
        if display_position is None:
            self._set_hover_target(None)
            return
        display_x, display_y = display_position
        if self._preview_target_picking_enabled:
            target = self._preview_target_at_display(display_x, display_y)
            self._set_hover_target(
                None if target is None else ("preview", target)
            )
            return
        atom_index = self._atom_index_at_display(display_x, display_y)
        if atom_index is not None:
            self._set_hover_target(("atom", atom_index))
            return
        if not self._hover_bond_picking_enabled:
            self._set_hover_target(None)
            return
        edge = self._scene.pick_bond_edge(display_x, display_y)
        self._set_hover_target(
            None if edge is None else ("bond", tuple(sorted(edge)))
        )

    def _atom_index_at_display(
        self,
        display_x: int,
        display_y: int,
    ) -> int | None:
        render_width, render_height = self._vtk_widget.GetRenderWindow().GetSize()
        widget_width = max(1, self._vtk_widget.width())
        widget_height = max(1, self._vtk_widget.height())
        render_scale = max(
            render_width / widget_width,
            render_height / widget_height,
        )
        return self._scene.pick_atom_index(
            display_x,
            display_y,
            minimum_radius_pixels=(
                MINIMUM_ATOM_PICK_RADIUS_PIXELS * render_scale
            ),
        )

    def _preview_target_at_display(
        self,
        display_x: int,
        display_y: int,
    ) -> object | None:
        render_width, render_height = self._vtk_widget.GetRenderWindow().GetSize()
        widget_width = max(1, self._vtk_widget.width())
        widget_height = max(1, self._vtk_widget.height())
        render_scale = max(
            render_width / widget_width,
            render_height / widget_height,
        )
        return self._scene.pick_preview_target(
            display_x,
            display_y,
            self._preview_pick_targets,
            minimum_radius_pixels=(
                MINIMUM_ATOM_PICK_RADIUS_PIXELS * render_scale
            ),
        )

    def _set_hover_target(self, target: tuple[str, object] | None) -> None:
        if target == self._hover_target:
            return
        previous = self._hover_target
        self._hover_target = target
        if previous is not None and previous[0] == "preview":
            self.preview_target_cleared.emit()
        if target is None:
            self._scene.set_hover_highlight()
            self.hover_cleared.emit()
        elif target[0] == "atom":
            atom_index = int(target[1])
            self._scene.set_hover_highlight(atom_index=atom_index)
            self.atom_hovered.emit(atom_index)
        elif target[0] == "bond":
            first, second = target[1]
            self._scene.set_hover_highlight(bond_edge=(first, second))
            self.bond_hovered.emit(first, second)
        else:
            self._scene.set_hover_highlight()
            self.preview_target_hovered.emit(target[1])
        self._render_if_active()

    def _clear_hover_target(self) -> None:
        self._hover_pick_timer.stop()
        self._hover_pick_position = None
        self._set_hover_target(None)

    def _reset_hover_tracking(self) -> None:
        self._hover_pick_timer.stop()
        self._hover_pick_position = None
        if self._hover_target is not None and self._hover_target[0] == "preview":
            self.preview_target_cleared.emit()
        self._hover_target = None
        self._scene.set_hover_highlight()

    def _display_position(self, point: QPoint) -> tuple[int, int] | None:
        render_window = self._vtk_widget.GetRenderWindow()
        render_width, render_height = render_window.GetSize()
        if render_width <= 0 or render_height <= 0:
            return None
        widget_width = max(1, self._vtk_widget.width())
        widget_height = max(1, self._vtk_widget.height())
        display_x = min(
            render_width - 1,
            max(0, round(point.x() * render_width / widget_width)),
        )
        display_y = min(
            render_height - 1,
            max(
                0,
                render_height
                - 1
                - round(point.y() * render_height / widget_height),
            ),
        )
        return display_x, display_y

    def _position_torsion_angle_editor(self) -> None:
        if self._render_resources_released:
            return
        try:
            self._torsion_angle_editor.objectName()
        except RuntimeError:
            # A queued reposition may outlive native Qt teardown.
            return
        if not self._torsion_angle_editing:
            self._torsion_angle_editor.hide()
            return
        if self._selected_torsion_edge is None:
            self._torsion_angle_editor.hide()
            return
        display = self._scene.torsion_label_display_position()
        if display is None:
            self._torsion_angle_editor.hide()
            return
        render_width, render_height = self._vtk_widget.GetRenderWindow().GetSize()
        if render_width <= 0 or render_height <= 0:
            return
        widget_width = max(1, self._vtk_widget.width())
        widget_height = max(1, self._vtk_widget.height())
        center_x = round(display[0] * widget_width / render_width)
        center_y = round(
            (render_height - 1 - display[1]) * widget_height / render_height
        )
        editor_width = self._torsion_angle_editor.width()
        editor_height = self._torsion_angle_editor.height()
        left = min(
            max(0, center_x - editor_width // 2),
            max(0, widget_width - editor_width),
        )
        top = min(
            max(0, center_y - editor_height // 2),
            max(0, widget_height - editor_height),
        )
        self._torsion_angle_editor.show()
        self._torsion_angle_editor.move(left, top)
        self._torsion_angle_editor.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Quiesce interaction before Qt performs native child teardown."""

        self.prepare_for_owner_teardown()
        super().closeEvent(event)

    def prepare_for_owner_teardown(self) -> None:
        """Release VTK while its Qt native window handle is still valid.

        QVTK connects its parent's ``destroyed`` signal to a late Finalize.
        Windows may have invalidated the WGL handle by then.  Finalize here,
        disconnect that fallback, and break VTK's native reference chain so
        later Python/Qt destruction cannot try to clean the same window again.
        """

        if not self._render_resources_released:
            self._render_resources_released = True
            self._initial_background_timer.stop()
            self._hover_pick_timer.stop()
            self._hover_pick_position = None
            style = self._interactor.GetInteractorStyle()
            if style is not None:
                # End interaction while its native surface is still usable.
                style.StopState()
                style.SetEnabled(False)
            self._vtk_widget.DestroyTimer(self._interactor, "DestroyTimerEvent")
            self._interactor.Disable()
            self._scene.detach_renderer_observer()
            self.destroyed.disconnect(self._vtk_widget.close)
            render_window = self._vtk_widget.GetRenderWindow()
            self._vtk_widget.Finalize()
            self._interactor.SetRenderWindow(None)
            render_window.RemoveRenderer(self._renderer)
            self._vtk_widget._RenderWindow = None
