"""Batched VTK scene construction for molecular structures and connectivity."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import (
    acos,
    atan2,
    ceil,
    cos,
    degrees,
    dist,
    hypot,
    isfinite,
    pi,
    radians,
    sin,
)
from types import MappingProxyType

from vtkmodules.vtkCommonCore import (
    vtkFloatArray,
    vtkIdTypeArray,
    vtkPoints,
    vtkStringArray,
    vtkUnsignedCharArray,
)
from vtkmodules.vtkCommonDataModel import (
    vtkCellArray,
    vtkPolyData,
)
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkSphereSource
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkActor2D,
    vtkBillboardTextActor3D,
    vtkCellPicker,
    vtkGlyph3DMapper,
    vtkPolyDataMapper,
    vtkRenderer,
)
from vtkmodules.vtkRenderingLabel import vtkLabeledDataMapper

from moltage.domain.bond_display import (
    BondDisplayOrder,
    normalized_bond_display_orders,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.structure.cube import CubeScalarField
from moltage.visualization.orbital_surface import (
    OrbitalSurfaceLayer,
    OrbitalSurfacePreferences,
)
from moltage.visualization.view_preferences import (
    DEFAULT_ATOM_HIGHLIGHT_COLORS,
    AtomHighlightColors,
    ViewPreferences,
)


ATOM_RADIUS_SCALE = 0.45
MINIMUM_ATOM_PICK_RADIUS_PIXELS = 7.0
SPHERE_RESOLUTION = 18
BOND_TUBE_RADIUS = 0.10
BOND_TUBE_SIDES = 12
MULTIPLE_BOND_STRAND_SEPARATION = BOND_TUBE_RADIUS * 2.4
HIGHLIGHT_SHELL_SIZE_MULTIPLIER = 1.35
HIGHLIGHT_SHELL_OPACITY = 0.32
PREVIEW_ATOM_OPACITY = 0.62
LATTICE_GUIDE_PLANE_RINGS = 3
LATTICE_GUIDE_PLANE_CENTER_ALPHA = 54
LATTICE_GUIDE_GRID_CENTER_ALPHA = 148
LATTICE_GUIDE_GRID_LINE_WIDTH = 1.35
LATTICE_GUIDE_BOND_LINE_WIDTH = 2.4
LATTICE_GUIDE_BOND_OPACITY = 0.88
PRIMARY_HIGHLIGHT_COLOR_RGB = (255, 0, 255)
SECONDARY_HIGHLIGHT_COLOR_RGB = (0, 220, 255)
LEFT_SURFACE_COLOR_RGB = (0, 170, 70)
RIGHT_SURFACE_COLOR_RGB = (235, 72, 28)
SURFACE_SHELL_SIZE_MULTIPLIER = 1.58
SURFACE_SHELL_OPACITY = 0.46
SURFACE_LABEL_OFFSET_PIXELS = (0, 24)
MEASUREMENT_HIGHLIGHT_COLOR_RGB = (255, 156, 0)
MEASUREMENT_HIGHLIGHT_SHELL_SIZE_MULTIPLIER = 1.50
ATOM_GROUP_RING_SIZE_MULTIPLIER = 1.20
ATOM_HOVER_RING_SIZE_MULTIPLIER = 1.28
ATOM_HIGHLIGHT_RING_SEGMENTS = 40
ATOM_HIGHLIGHT_RING_LINE_WIDTH = 3.0
ATOM_INDEX_LABEL_OFFSET_MULTIPLIER = 1.55
ATOM_INDEX_LABEL_FONT_SIZE = 16
MEASUREMENT_PICK_LABEL_OFFSET_PIXELS = (28, 28)
ANNOTATION_DASH_LENGTH = 0.16
ANNOTATION_GAP_LENGTH = 0.10
ANNOTATION_ARC_RADIUS = 0.72
ANNOTATION_ARC_STEP_DEGREES = 5.0
DISTANCE_LABEL_OFFSET_PIXELS = 18
DISTANCE_LABEL_MIN_PROJECTED_LENGTH_PIXELS = 4.0
MAX_ANNOTATION_TEXT_ACTORS = 6
MAX_MEASUREMENT_PICK_COUNT = 3
ELEMENT_LABEL_MINIMUM_OFFSET_PIXELS = 16
ELEMENT_LABEL_MARGIN_PIXELS = 10
TORSION_SELECTED_BOND_COLOR_RGB = (255, 132, 24)
TORSION_CIRCLE_COLOR_RGB = (50, 112, 150)
TORSION_RAY_COLOR_RGB = (15, 15, 15)
TORSION_ANGLE_COLOR_RGB = (181, 121, 12)
TORSION_SIDE_COLOR_RGB = (182, 82, 20)
TORSION_GIZMO_MIN_RADIUS = 0.65
TORSION_PICK_TOLERANCE = 0.012
TORSION_LABEL_PICK_HALF_WIDTH_PIXELS = 34.0
TORSION_LABEL_PICK_HALF_HEIGHT_PIXELS = 14.0
TORSION_HANDLE_RADIUS_SCALE = 0.14
TORSION_HANDLE_MIN_RADIUS = 0.11

ELEMENT_COLORS_RGB: Mapping[str, tuple[int, int, int]] = MappingProxyType(
    {
        "H": (210, 210, 210),
        "He": (217, 255, 255),
        "Li": (204, 128, 255),
        "Be": (194, 255, 0),
        "B": (255, 181, 181),
        "C": (55, 55, 55),
        "N": (48, 80, 248),
        "O": (255, 13, 13),
        "F": (144, 224, 80),
        "Ne": (179, 227, 245),
        "Na": (171, 92, 242),
        "Mg": (138, 255, 0),
        "Al": (191, 166, 166),
        "Si": (240, 200, 160),
        "P": (255, 128, 0),
        "S": (255, 255, 48),
        "Cl": (31, 240, 31),
        "Ar": (128, 209, 227),
        "K": (143, 64, 212),
        "Ca": (61, 255, 0),
        "Sc": (230, 230, 230),
        "Ti": (191, 194, 199),
        "V": (166, 166, 171),
        "Cr": (138, 153, 199),
        "Mn": (156, 122, 199),
        "Fe": (224, 102, 51),
        "Co": (240, 144, 160),
        "Ni": (80, 208, 80),
        "Cu": (200, 128, 51),
        "Zn": (125, 128, 176),
        "Ga": (194, 143, 143),
        "Ge": (102, 143, 143),
        "As": (189, 128, 227),
        "Se": (255, 161, 0),
        "Br": (166, 41, 41),
        "Kr": (92, 184, 209),
        "Rb": (112, 46, 176),
        "Sr": (0, 255, 0),
        "Y": (148, 255, 255),
        "Zr": (148, 224, 224),
        "Nb": (115, 194, 201),
        "Mo": (84, 181, 181),
        "Tc": (59, 158, 158),
        "Ru": (36, 143, 143),
        "Rh": (10, 125, 140),
        "Pd": (0, 105, 133),
        "Ag": (192, 192, 192),
        "Cd": (255, 217, 143),
        "In": (166, 117, 115),
        "Sn": (102, 128, 128),
        "Sb": (158, 99, 181),
        "Te": (212, 122, 0),
        "I": (148, 0, 148),
        "Xe": (66, 158, 176),
        "Cs": (87, 23, 143),
        "Ba": (0, 201, 0),
        "La": (112, 212, 255),
        "Ce": (255, 255, 199),
        "Pr": (217, 255, 199),
        "Nd": (199, 255, 199),
        "Pm": (163, 255, 199),
        "Sm": (143, 255, 199),
        "Eu": (97, 255, 199),
        "Gd": (69, 255, 199),
        "Tb": (48, 255, 199),
        "Dy": (31, 255, 199),
        "Ho": (0, 255, 156),
        "Er": (0, 230, 117),
        "Tm": (0, 212, 82),
        "Yb": (0, 191, 56),
        "Lu": (0, 171, 36),
        "Hf": (77, 194, 255),
        "Ta": (77, 166, 255),
        "W": (33, 148, 214),
        "Re": (38, 125, 171),
        "Os": (38, 102, 150),
        "Ir": (23, 84, 135),
        "Pt": (208, 208, 224),
        "Au": (255, 209, 35),
        "Hg": (184, 184, 208),
        "Tl": (166, 84, 77),
        "Pb": (87, 89, 97),
        "Bi": (158, 79, 181),
    }
)


class VisualizationError(ValueError):
    """Raised when molecular data cannot be represented by the viewer."""


@dataclass(frozen=True, slots=True)
class PreviewAtom:
    """Generic visual-only atom data with no molecular-structure identity."""

    coordinates: tuple[float, float, float]
    display_radius: float
    display_color_rgb: tuple[int, int, int]
    element: str | None = None

    def __post_init__(self) -> None:
        coordinates = tuple(self.coordinates)
        if len(coordinates) != 3:
            raise ValueError("preview atom coordinates must contain x, y, and z")
        numeric_coordinates: list[float] = []
        for coordinate in coordinates:
            if isinstance(coordinate, bool) or not isinstance(
                coordinate,
                (int, float),
            ):
                raise TypeError("preview atom coordinates must be numeric")
            numeric_coordinate = float(coordinate)
            if not isfinite(numeric_coordinate):
                raise ValueError("preview atom coordinates must be finite")
            numeric_coordinates.append(numeric_coordinate)

        radius = self.display_radius
        if isinstance(radius, bool) or not isinstance(radius, (int, float)):
            raise TypeError("preview atom display radius must be numeric")
        numeric_radius = float(radius)
        if not isfinite(numeric_radius) or numeric_radius <= 0.0:
            raise ValueError(
                "preview atom display radius must be finite and greater than zero"
            )

        display_color = tuple(self.display_color_rgb)
        if len(display_color) != 3:
            raise ValueError("preview atom display color must contain RGB values")
        for component in display_color:
            if isinstance(component, bool) or not isinstance(component, int):
                raise TypeError("preview atom RGB values must be integers")
            if component < 0 or component > 255:
                raise ValueError("preview atom RGB values must be from 0 to 255")

        element = self.element
        if element is not None:
            if (
                not isinstance(element, str)
                or not element
                or not element.isascii()
                or not element.isalpha()
            ):
                raise ValueError(
                    "preview atom element must contain ASCII letters only"
                )
            element = element[0].upper() + element[1:].lower()

        object.__setattr__(self, "coordinates", tuple(numeric_coordinates))
        object.__setattr__(self, "display_radius", numeric_radius)
        object.__setattr__(self, "display_color_rgb", display_color)
        object.__setattr__(self, "element", element)


@dataclass(frozen=True, slots=True)
class PreviewPickTarget:
    """Screen-pickable visual position carrying an opaque stable identity."""

    token: object
    coordinates: tuple[float, float, float]
    display_radius: float

    def __post_init__(self) -> None:
        if self.token is None:
            raise ValueError("preview pick target token must not be None")
        coordinates = tuple(self.coordinates)
        if len(coordinates) != 3:
            raise ValueError(
                "preview pick target coordinates must contain x, y, and z"
            )
        numeric_coordinates: list[float] = []
        for coordinate in coordinates:
            if isinstance(coordinate, bool) or not isinstance(
                coordinate,
                (int, float),
            ):
                raise TypeError("preview pick target coordinates must be numeric")
            numeric_coordinate = float(coordinate)
            if not isfinite(numeric_coordinate):
                raise ValueError("preview pick target coordinates must be finite")
            numeric_coordinates.append(numeric_coordinate)

        radius = self.display_radius
        if isinstance(radius, bool) or not isinstance(radius, (int, float)):
            raise TypeError("preview pick target display radius must be numeric")
        numeric_radius = float(radius)
        if not isfinite(numeric_radius) or numeric_radius <= 0.0:
            raise ValueError(
                "preview pick target display radius must be finite and greater "
                "than zero"
            )

        object.__setattr__(self, "coordinates", tuple(numeric_coordinates))
        object.__setattr__(self, "display_radius", numeric_radius)


@dataclass(frozen=True, slots=True)
class LatticeExtensionPreviewGuide:
    """Visual-only predicted bonds and current Au(111) layer orientation."""

    center: tuple[float, float, float]
    bond_targets: tuple[tuple[float, float, float], ...]
    layer_basis_u: tuple[float, float, float]
    layer_basis_v: tuple[float, float, float]
    display_color_rgb: tuple[int, int, int]

    def __post_init__(self) -> None:
        center = _validated_point(self.center, "lattice guide center")
        targets = tuple(
            _validated_point(target, "lattice guide bond target")
            for target in self.bond_targets
        )
        if not targets:
            raise ValueError("lattice guide requires at least one bond target")
        if any(dist(center, target) <= 1.0e-12 for target in targets):
            raise ValueError("lattice guide bond targets must differ from center")
        basis_u = _validated_point(
            self.layer_basis_u,
            "lattice guide layer basis u",
        )
        basis_v = _validated_point(
            self.layer_basis_v,
            "lattice guide layer basis v",
        )
        if (
            dist((0.0, 0.0, 0.0), basis_u) <= 1.0e-12
            or dist((0.0, 0.0, 0.0), basis_v) <= 1.0e-12
            or dist((0.0, 0.0, 0.0), _cross(basis_u, basis_v)) <= 1.0e-12
        ):
            raise ValueError(
                "lattice guide layer basis vectors must be non-zero and independent"
            )
        color = tuple(self.display_color_rgb)
        if len(color) != 3:
            raise ValueError("lattice guide display color must contain RGB values")
        if any(
            isinstance(component, bool)
            or not isinstance(component, int)
            or component < 0
            or component > 255
            for component in color
        ):
            raise ValueError(
                "lattice guide RGB values must be integers from 0 to 255"
            )
        object.__setattr__(self, "center", center)
        object.__setattr__(self, "bond_targets", targets)
        object.__setattr__(self, "layer_basis_u", basis_u)
        object.__setattr__(self, "layer_basis_v", basis_v)
        object.__setattr__(self, "display_color_rgb", color)


@dataclass(frozen=True, slots=True)
class DistanceAnnotation:
    """A generic measured 3D segment rendered as dashes plus a label."""

    start: tuple[float, float, float]
    end: tuple[float, float, float]

    def __post_init__(self) -> None:
        start = _validated_point(self.start, "distance annotation start")
        end = _validated_point(self.end, "distance annotation end")
        if dist(start, end) <= 1.0e-12:
            raise ValueError("distance annotation endpoints must differ")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class AngleAnnotation:
    """A generic measured 3D angle centered at one vertex."""

    vertex: tuple[float, float, float]
    reference_point: tuple[float, float, float]
    target_point: tuple[float, float, float]

    def __post_init__(self) -> None:
        vertex = _validated_point(self.vertex, "angle annotation vertex")
        reference = _validated_point(
            self.reference_point,
            "angle annotation reference point",
        )
        target = _validated_point(
            self.target_point,
            "angle annotation target point",
        )
        _normalized(_subtract(reference, vertex), "angle reference ray")
        _normalized(_subtract(target, vertex), "angle target ray")
        object.__setattr__(self, "vertex", vertex)
        object.__setattr__(self, "reference_point", reference)
        object.__setattr__(self, "target_point", target)


@dataclass(frozen=True, slots=True)
class MeasurementOverlayAnnotation:
    """Bind one session measurement ID to reusable annotation geometry."""

    measurement_id: int
    annotation: DistanceAnnotation | AngleAnnotation

    def __post_init__(self) -> None:
        if isinstance(self.measurement_id, bool) or not isinstance(
            self.measurement_id,
            int,
        ):
            raise TypeError("measurement annotation ID must be an integer")
        if self.measurement_id <= 0:
            raise ValueError("measurement annotation ID must be positive")
        if not isinstance(self.annotation, (DistanceAnnotation, AngleAnnotation)):
            raise TypeError(
                "measurement annotation must wrap distance or angle geometry"
            )


@dataclass(frozen=True, slots=True)
class TorsionGizmo:
    """Visual state for one selected connectivity edge and relative rotation."""

    fixed_atom_index: int
    rotating_atom_index: int
    reference_direction: tuple[float, float, float]
    angle_degrees: float

    def __post_init__(self) -> None:
        for name in ("fixed_atom_index", "rotating_atom_index"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"torsion {name} must be an integer")
            if value < 0:
                raise ValueError(f"torsion {name} must be non-negative")
        if self.fixed_atom_index == self.rotating_atom_index:
            raise ValueError("torsion endpoints must be distinct")
        direction = _validated_point(
            self.reference_direction,
            "torsion reference direction",
        )
        _normalized(direction, "torsion reference direction")
        angle = self.angle_degrees
        if isinstance(angle, bool) or not isinstance(angle, (int, float)):
            raise TypeError("torsion angle must be numeric")
        numeric_angle = float(angle)
        if not isfinite(numeric_angle):
            raise ValueError("torsion angle must be finite")
        object.__setattr__(self, "reference_direction", direction)
        object.__setattr__(self, "angle_degrees", numeric_angle)


@dataclass(frozen=True, slots=True)
class _AnnotationTextSpec:
    text: str
    position: tuple[float, float, float]
    projected_segment: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
    ] | None = None


@dataclass(frozen=True, slots=True)
class _DistanceLabelScreenLayout:
    orientation_degrees: float
    display_offset: tuple[int, int]


class MoleculeScene:
    """Own fixed batched pipelines for molecule and visual overlays."""

    def __init__(self, renderer: vtkRenderer) -> None:
        self._renderer = renderer
        self._view_preferences = ViewPreferences()
        self._orbital_surface = OrbitalSurfaceLayer()
        self._atom_polydata = vtkPolyData()
        self._bond_polydata = vtkPolyData()
        self._highlight_polydata = vtkPolyData()
        self._atom_highlight_ring_polydata = vtkPolyData()
        self._primary_group_label_polydata = vtkPolyData()
        self._secondary_group_label_polydata = vtkPolyData()
        self._hover_atom_label_polydata = vtkPolyData()
        self._surface_polydata = vtkPolyData()
        self._preview_polydata = vtkPolyData()
        self._lattice_guide_bond_polydata = vtkPolyData()
        self._lattice_guide_plane_polydata = vtkPolyData()
        self._lattice_guide_grid_polydata = vtkPolyData()
        self._dash_polydata = vtkPolyData()
        self._arc_polydata = vtkPolyData()
        self._selected_bond_polydata = vtkPolyData()
        self._torsion_circle_polydata = vtkPolyData()
        self._torsion_fixed_ray_polydata = vtkPolyData()
        self._torsion_moving_ray_polydata = vtkPolyData()
        self._torsion_angle_polydata = vtkPolyData()
        self._torsion_side_polydata = vtkPolyData()
        self._parameter_atom_label_polydata = vtkPolyData()
        self._parameter_bond_label_polydata = vtkPolyData()
        self._visual_radii: tuple[float, ...] = ()
        self._atom_elements: tuple[str, ...] = ()
        self._atom_coordinates: tuple[tuple[float, float, float], ...] = ()
        self._bond_edges: tuple[tuple[int, int], ...] = ()
        self._bond_display_orders: tuple[BondDisplayOrder, ...] = ()
        self._primary_highlight_indices: tuple[int, ...] = ()
        self._secondary_highlight_indices: tuple[int, ...] = ()
        self._primary_group_indices: tuple[int, ...] = ()
        self._secondary_group_indices: tuple[int, ...] = ()
        self._atom_highlight_colors = DEFAULT_ATOM_HIGHLIGHT_COLORS
        self._atom_highlight_camera_signature: tuple[float, ...] | None = None
        self._hover_atom_index: int | None = None
        self._hover_bond_edge: tuple[int, int] | None = None
        self._left_surface_indices: tuple[int, ...] = ()
        self._right_surface_indices: tuple[int, ...] = ()
        self._surface_labels: tuple[str, ...] = ()
        self._measurement_pick_indices: tuple[int, ...] = ()
        self._measurement_pick_labels: tuple[str, ...] = ()
        self._preview_atoms: tuple[PreviewAtom, ...] = ()
        self._preview_hidden_atom_indices: tuple[int, ...] = ()
        self._lattice_extension_preview_guide: (
            LatticeExtensionPreviewGuide | None
        ) = None
        self._distance_annotations: tuple[DistanceAnnotation, ...] = ()
        self._angle_annotations: tuple[AngleAnnotation, ...] = ()
        self._measurement_annotations: tuple[
            MeasurementOverlayAnnotation, ...
        ] = ()
        self._rendered_measurement_ids: tuple[int, ...] = ()
        self._annotation_texts: tuple[str, ...] = ()
        self._element_label_actors: tuple[vtkBillboardTextActor3D, ...] = ()
        self._element_label_bindings: tuple[
            tuple[
                vtkBillboardTextActor3D,
                tuple[float, float, float],
                float,
            ],
            ...,
        ] = ()
        self._distance_label_bindings: tuple[
            tuple[
                vtkBillboardTextActor3D,
                tuple[float, float, float],
                tuple[float, float, float],
            ],
            ...,
        ] = ()
        self._torsion_gizmo: TorsionGizmo | None = None
        self._torsion_center: tuple[float, float, float] | None = None
        self._torsion_label_position: tuple[float, float, float] | None = None
        self._torsion_handle_position: tuple[float, float, float] | None = None
        self._torsion_angle_text_requested_visible = True
        self._parameter_atom_labels: tuple[tuple[int, str], ...] = ()
        self._parameter_bond_labels: tuple[
            tuple[tuple[int, int], str], ...
        ] = ()

        self._sphere_source = vtkSphereSource()
        self._sphere_source.SetRadius(1.0)
        self._sphere_source.SetThetaResolution(SPHERE_RESOLUTION)
        self._sphere_source.SetPhiResolution(SPHERE_RESOLUTION)

        self._atom_mapper = vtkGlyph3DMapper()
        self._atom_mapper.SetInputData(self._atom_polydata)
        self._atom_mapper.SetSourceConnection(self._sphere_source.GetOutputPort())
        self._atom_mapper.SetScaleArray("visual_radius")
        self._atom_mapper.SetScaleModeToScaleByMagnitude()
        self._atom_mapper.SetScaleFactor(1.0)
        self._atom_mapper.SetScalarModeToUsePointFieldData()
        self._atom_mapper.SelectColorArray("display_color")
        self._atom_mapper.SetColorModeToDirectScalars()
        self._atom_mapper.SetSelectionIdArray("atom_index")
        self._atom_mapper.SetUseSelectionIds(True)
        self._atom_mapper.ScalarVisibilityOn()

        self._atom_actor = vtkActor()
        self._atom_actor.SetMapper(self._atom_mapper)
        self._atom_actor.GetProperty().SetAmbient(0.18)
        self._atom_actor.GetProperty().SetDiffuse(0.82)
        self._atom_actor.GetProperty().SetSpecular(0.25)
        self._atom_actor.GetProperty().SetSpecularPower(24.0)
        self._atom_actor.PickableOn()

        self._highlight_mapper = vtkGlyph3DMapper()
        self._highlight_mapper.SetInputData(self._highlight_polydata)
        self._highlight_mapper.SetSourceConnection(
            self._sphere_source.GetOutputPort()
        )
        self._highlight_mapper.SetScaleArray("shell_radius")
        self._highlight_mapper.SetScaleModeToScaleByMagnitude()
        self._highlight_mapper.SetScaleFactor(1.0)
        self._highlight_mapper.SetScalarModeToUsePointFieldData()
        self._highlight_mapper.SelectColorArray("shell_color")
        self._highlight_mapper.SetColorModeToDirectScalars()
        self._highlight_mapper.ScalarVisibilityOn()

        self._highlight_actor = vtkActor()
        self._highlight_actor.SetMapper(self._highlight_mapper)
        self._highlight_actor.GetProperty().SetOpacity(HIGHLIGHT_SHELL_OPACITY)
        self._highlight_actor.GetProperty().SetAmbient(0.35)
        self._highlight_actor.GetProperty().SetDiffuse(0.65)
        self._highlight_actor.ForceTranslucentOn()
        self._highlight_actor.PickableOff()

        self._atom_highlight_ring_mapper = vtkPolyDataMapper()
        self._atom_highlight_ring_mapper.SetInputData(
            self._atom_highlight_ring_polydata
        )
        self._atom_highlight_ring_mapper.SetScalarModeToUseCellData()
        self._atom_highlight_ring_mapper.SetColorModeToDirectScalars()
        self._atom_highlight_ring_mapper.ScalarVisibilityOn()
        self._atom_highlight_ring_actor = vtkActor()
        self._atom_highlight_ring_actor.SetMapper(
            self._atom_highlight_ring_mapper
        )
        ring_property = self._atom_highlight_ring_actor.GetProperty()
        ring_property.SetLineWidth(ATOM_HIGHLIGHT_RING_LINE_WIDTH)
        ring_property.SetAmbient(1.0)
        ring_property.SetDiffuse(0.0)
        ring_property.RenderLinesAsTubesOn()
        self._atom_highlight_ring_actor.PickableOff()

        self._primary_group_label_mapper = _new_atom_index_label_mapper(
            self._primary_group_label_polydata,
            self._atom_highlight_colors.primary_group,
        )
        self._primary_group_label_actor = vtkActor2D()
        self._primary_group_label_actor.SetMapper(
            self._primary_group_label_mapper
        )
        self._primary_group_label_actor.PickableOff()
        self._secondary_group_label_mapper = _new_atom_index_label_mapper(
            self._secondary_group_label_polydata,
            self._atom_highlight_colors.secondary_group,
        )
        self._secondary_group_label_actor = vtkActor2D()
        self._secondary_group_label_actor.SetMapper(
            self._secondary_group_label_mapper
        )
        self._secondary_group_label_actor.PickableOff()
        self._hover_atom_label_mapper = _new_atom_index_label_mapper(
            self._hover_atom_label_polydata,
            self._atom_highlight_colors.hover,
        )
        self._hover_atom_label_actor = vtkActor2D()
        self._hover_atom_label_actor.SetMapper(
            self._hover_atom_label_mapper
        )
        self._hover_atom_label_actor.PickableOff()

        self._surface_mapper = vtkGlyph3DMapper()
        self._surface_mapper.SetInputData(self._surface_polydata)
        self._surface_mapper.SetSourceConnection(self._sphere_source.GetOutputPort())
        self._surface_mapper.SetScaleArray("surface_shell_radius")
        self._surface_mapper.SetScaleModeToScaleByMagnitude()
        self._surface_mapper.SetScaleFactor(1.0)
        self._surface_mapper.SetScalarModeToUsePointFieldData()
        self._surface_mapper.SelectColorArray("surface_shell_color")
        self._surface_mapper.SetColorModeToDirectScalars()
        self._surface_mapper.ScalarVisibilityOn()
        self._surface_actor = vtkActor()
        self._surface_actor.SetMapper(self._surface_mapper)
        self._surface_actor.GetProperty().SetOpacity(SURFACE_SHELL_OPACITY)
        self._surface_actor.GetProperty().SetAmbient(0.45)
        self._surface_actor.GetProperty().SetDiffuse(0.55)
        self._surface_actor.ForceTranslucentOn()
        self._surface_actor.PickableOff()

        self._preview_mapper = vtkGlyph3DMapper()
        self._preview_mapper.SetInputData(self._preview_polydata)
        self._preview_mapper.SetSourceConnection(
            self._sphere_source.GetOutputPort()
        )
        self._preview_mapper.SetScaleArray("preview_radius")
        self._preview_mapper.SetScaleModeToScaleByMagnitude()
        self._preview_mapper.SetScaleFactor(1.0)
        self._preview_mapper.SetScalarModeToUsePointFieldData()
        self._preview_mapper.SelectColorArray("preview_color")
        self._preview_mapper.SetColorModeToDirectScalars()
        self._preview_mapper.ScalarVisibilityOn()

        self._preview_actor = vtkActor()
        self._preview_actor.SetMapper(self._preview_mapper)
        self._preview_actor.GetProperty().SetOpacity(PREVIEW_ATOM_OPACITY)
        self._preview_actor.GetProperty().SetAmbient(0.18)
        self._preview_actor.GetProperty().SetDiffuse(0.82)
        self._preview_actor.GetProperty().SetSpecular(0.25)
        self._preview_actor.GetProperty().SetSpecularPower(24.0)
        self._preview_actor.ForceTranslucentOn()
        self._preview_actor.PickableOff()

        self._lattice_guide_plane_mapper = vtkPolyDataMapper()
        self._lattice_guide_plane_mapper.SetInputData(
            self._lattice_guide_plane_polydata
        )
        self._lattice_guide_plane_mapper.SetScalarModeToUsePointData()
        self._lattice_guide_plane_mapper.SetColorModeToDirectScalars()
        self._lattice_guide_plane_mapper.InterpolateScalarsBeforeMappingOn()
        self._lattice_guide_plane_mapper.ScalarVisibilityOn()
        self._lattice_guide_plane_actor = vtkActor()
        self._lattice_guide_plane_actor.SetMapper(
            self._lattice_guide_plane_mapper
        )
        plane_property = self._lattice_guide_plane_actor.GetProperty()
        plane_property.SetAmbient(1.0)
        plane_property.SetDiffuse(0.0)
        plane_property.SetSpecular(0.0)
        self._lattice_guide_plane_actor.ForceTranslucentOn()
        self._lattice_guide_plane_actor.PickableOff()

        self._lattice_guide_grid_mapper = vtkPolyDataMapper()
        self._lattice_guide_grid_mapper.SetInputData(
            self._lattice_guide_grid_polydata
        )
        self._lattice_guide_grid_mapper.SetScalarModeToUsePointData()
        self._lattice_guide_grid_mapper.SetColorModeToDirectScalars()
        self._lattice_guide_grid_mapper.InterpolateScalarsBeforeMappingOn()
        self._lattice_guide_grid_mapper.ScalarVisibilityOn()
        self._lattice_guide_grid_actor = vtkActor()
        self._lattice_guide_grid_actor.SetMapper(
            self._lattice_guide_grid_mapper
        )
        grid_property = self._lattice_guide_grid_actor.GetProperty()
        grid_property.SetAmbient(1.0)
        grid_property.SetDiffuse(0.0)
        grid_property.SetSpecular(0.0)
        grid_property.SetLineWidth(LATTICE_GUIDE_GRID_LINE_WIDTH)
        grid_property.RenderLinesAsTubesOn()
        self._lattice_guide_grid_actor.ForceTranslucentOn()
        self._lattice_guide_grid_actor.PickableOff()

        self._lattice_guide_bond_mapper = vtkPolyDataMapper()
        self._lattice_guide_bond_mapper.SetInputData(
            self._lattice_guide_bond_polydata
        )
        self._lattice_guide_bond_mapper.ScalarVisibilityOff()
        self._lattice_guide_bond_actor = vtkActor()
        self._lattice_guide_bond_actor.SetMapper(
            self._lattice_guide_bond_mapper
        )
        bond_guide_property = self._lattice_guide_bond_actor.GetProperty()
        bond_guide_property.SetLineWidth(LATTICE_GUIDE_BOND_LINE_WIDTH)
        bond_guide_property.SetOpacity(LATTICE_GUIDE_BOND_OPACITY)
        bond_guide_property.SetAmbient(1.0)
        bond_guide_property.SetDiffuse(0.0)
        bond_guide_property.RenderLinesAsTubesOn()
        self._lattice_guide_bond_actor.ForceTranslucentOn()
        self._lattice_guide_bond_actor.PickableOff()

        self._tube_filter = vtkTubeFilter()
        self._tube_filter.SetInputData(self._bond_polydata)
        self._tube_filter.SetRadius(BOND_TUBE_RADIUS)
        self._tube_filter.SetNumberOfSides(BOND_TUBE_SIDES)
        self._tube_filter.CappingOn()

        self._bond_mapper = vtkPolyDataMapper()
        self._bond_mapper.SetInputConnection(self._tube_filter.GetOutputPort())
        self._bond_mapper.ScalarVisibilityOff()

        self._bond_actor = vtkActor()
        self._bond_actor.SetMapper(self._bond_mapper)
        self._bond_actor.GetProperty().SetColor(0.45, 0.47, 0.50)
        self._bond_actor.GetProperty().SetAmbient(0.15)
        self._bond_actor.GetProperty().SetDiffuse(0.85)
        self._bond_actor.PickableOn()

        self._selected_bond_tube_filter = vtkTubeFilter()
        self._selected_bond_tube_filter.SetInputData(
            self._selected_bond_polydata
        )
        self._selected_bond_tube_filter.SetRadius(BOND_TUBE_RADIUS * 1.55)
        self._selected_bond_tube_filter.SetNumberOfSides(BOND_TUBE_SIDES)
        self._selected_bond_tube_filter.CappingOn()
        self._selected_bond_mapper = vtkPolyDataMapper()
        self._selected_bond_mapper.SetInputConnection(
            self._selected_bond_tube_filter.GetOutputPort()
        )
        self._selected_bond_mapper.ScalarVisibilityOff()
        self._selected_bond_actor = vtkActor()
        self._selected_bond_actor.SetMapper(self._selected_bond_mapper)
        self._selected_bond_actor.GetProperty().SetColor(
            *(component / 255.0 for component in TORSION_SELECTED_BOND_COLOR_RGB)
        )
        self._selected_bond_actor.GetProperty().SetOpacity(0.52)
        self._selected_bond_actor.ForceTranslucentOn()
        self._selected_bond_actor.PickableOn()

        self._torsion_circle_actor = _new_line_actor(
            self._torsion_circle_polydata,
            TORSION_CIRCLE_COLOR_RGB,
            4.0,
            pickable=True,
        )
        self._torsion_fixed_ray_actor = _new_line_actor(
            self._torsion_fixed_ray_polydata,
            TORSION_RAY_COLOR_RGB,
            2.5,
        )
        self._torsion_moving_ray_actor = _new_line_actor(
            self._torsion_moving_ray_polydata,
            TORSION_RAY_COLOR_RGB,
            5.0,
            pickable=True,
        )
        self._torsion_handle_source = vtkSphereSource()
        self._torsion_handle_source.SetRadius(1.0)
        self._torsion_handle_source.SetThetaResolution(SPHERE_RESOLUTION)
        self._torsion_handle_source.SetPhiResolution(SPHERE_RESOLUTION)
        self._torsion_handle_mapper = vtkPolyDataMapper()
        self._torsion_handle_mapper.SetInputConnection(
            self._torsion_handle_source.GetOutputPort()
        )
        self._torsion_handle_actor = vtkActor()
        self._torsion_handle_actor.SetMapper(self._torsion_handle_mapper)
        self._torsion_handle_actor.GetProperty().SetColor(
            *(component / 255.0 for component in TORSION_RAY_COLOR_RGB)
        )
        self._torsion_handle_actor.GetProperty().SetAmbient(0.25)
        self._torsion_handle_actor.GetProperty().SetDiffuse(0.75)
        self._torsion_handle_actor.PickableOn()
        self._torsion_handle_actor.SetVisibility(False)
        self._torsion_angle_actor = _new_line_actor(
            self._torsion_angle_polydata,
            TORSION_ANGLE_COLOR_RGB,
            3.0,
        )
        self._torsion_side_actor = _new_line_actor(
            self._torsion_side_polydata,
            TORSION_SIDE_COLOR_RGB,
            7.0,
            pickable=True,
        )
        self._torsion_angle_text_actor = _new_torsion_text_actor()

        self._dash_mapper = vtkPolyDataMapper()
        self._dash_mapper.SetInputData(self._dash_polydata)
        self._dash_mapper.ScalarVisibilityOff()
        self._dash_actor = vtkActor()
        self._dash_actor.SetMapper(self._dash_mapper)
        self._dash_actor.GetProperty().SetColor(0.82, 0.58, 0.05)
        self._dash_actor.GetProperty().SetLineWidth(3.0)
        self._dash_actor.PickableOff()

        self._arc_mapper = vtkPolyDataMapper()
        self._arc_mapper.SetInputData(self._arc_polydata)
        self._arc_mapper.ScalarVisibilityOff()
        self._arc_actor = vtkActor()
        self._arc_actor.SetMapper(self._arc_mapper)
        self._arc_actor.GetProperty().SetColor(0.18, 0.18, 0.18)
        self._arc_actor.GetProperty().SetLineWidth(3.0)
        self._arc_actor.PickableOff()

        annotation_text_actors: list[vtkBillboardTextActor3D] = []
        for _ in range(MAX_ANNOTATION_TEXT_ACTORS):
            annotation_text_actors.append(_new_annotation_text_actor())
        self._annotation_text_actors = tuple(annotation_text_actors)
        self._measurement_pick_text_actors = tuple(
            _new_measurement_pick_text_actor()
            for _ in range(MAX_MEASUREMENT_PICK_COUNT)
        )
        self._surface_text_actors = tuple(
            _new_surface_text_actor() for _ in range(6)
        )
        self._parameter_atom_label_mapper = _new_parameter_label_mapper(
            self._parameter_atom_label_polydata,
            color=(0.05, 0.19, 0.36),
        )
        self._parameter_atom_label_actor = vtkActor2D()
        self._parameter_atom_label_actor.SetMapper(
            self._parameter_atom_label_mapper
        )
        self._parameter_atom_label_actor.PickableOff()
        self._parameter_bond_label_mapper = _new_parameter_label_mapper(
            self._parameter_bond_label_polydata,
            color=(0.43, 0.20, 0.05),
        )
        self._parameter_bond_label_actor = vtkActor2D()
        self._parameter_bond_label_actor.SetMapper(
            self._parameter_bond_label_mapper
        )
        self._parameter_bond_label_actor.PickableOff()

        self._renderer.SetBackground(1.0, 1.0, 1.0)
        self._renderer.AddActor(self._orbital_surface.actor)
        self._renderer.AddActor(self._lattice_guide_plane_actor)
        self._renderer.AddActor(self._lattice_guide_grid_actor)
        self._renderer.AddActor(self._bond_actor)
        self._renderer.AddActor(self._atom_actor)
        self._renderer.AddActor(self._highlight_actor)
        self._renderer.AddActor(self._atom_highlight_ring_actor)
        self._renderer.AddActor(self._surface_actor)
        self._renderer.AddActor(self._selected_bond_actor)
        self._renderer.AddActor(self._torsion_circle_actor)
        self._renderer.AddActor(self._torsion_fixed_ray_actor)
        self._renderer.AddActor(self._torsion_moving_ray_actor)
        self._renderer.AddActor(self._torsion_handle_actor)
        self._renderer.AddActor(self._torsion_angle_actor)
        self._renderer.AddActor(self._torsion_side_actor)
        self._renderer.AddActor(self._dash_actor)
        self._renderer.AddActor(self._arc_actor)
        self._renderer.AddActor(self._lattice_guide_bond_actor)
        self._renderer.AddActor(self._preview_actor)
        for actor in self._annotation_text_actors:
            self._renderer.AddActor(actor)
        for actor in self._measurement_pick_text_actors:
            self._renderer.AddActor(actor)
        for actor in self._surface_text_actors:
            self._renderer.AddActor(actor)
        self._renderer.AddActor(self._torsion_angle_text_actor)
        self._renderer.AddViewProp(self._parameter_atom_label_actor)
        self._renderer.AddViewProp(self._parameter_bond_label_actor)
        self._renderer.AddViewProp(self._primary_group_label_actor)
        self._renderer.AddViewProp(self._secondary_group_label_actor)
        self._renderer.AddViewProp(self._hover_atom_label_actor)
        self._renderer_start_observer_id = self._renderer.AddObserver(
            "StartEvent",
            self._renderer_started,
        )
        self.clear()

    def detach_renderer_observer(self) -> None:
        """Release the scene-owned callback without rendering during teardown."""

        if self._renderer_start_observer_id is not None:
            self._renderer.RemoveObserver(self._renderer_start_observer_id)
            self._renderer_start_observer_id = None

    def set_molecule(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        covalent_radii: Mapping[str, float],
        bond_display_orders: Iterable[BondDisplayOrder] | None = None,
    ) -> None:
        """Rebuild render data from unchanged molecular coordinates and edges."""

        if connectivity.atom_count != len(structure):
            raise VisualizationError(
                "connectivity atom count does not match the molecular structure: "
                f"{connectivity.atom_count} != {len(structure)}"
            )

        visual_radii: list[float] = []
        for atom in structure:
            try:
                ELEMENT_COLORS_RGB[atom.element]
            except KeyError as error:
                raise VisualizationError(
                    f"unsupported visualization element {atom.element!r}: "
                    "no display style is configured"
                ) from error
            visual_radii.append(
                _visual_radius_for(atom.element, covalent_radii)
            )
        self._visual_radii = tuple(visual_radii)
        self._atom_elements = tuple(atom.element for atom in structure)
        self._atom_coordinates = tuple(
            (atom.x, atom.y, atom.z) for atom in structure
        )
        self._preview_hidden_atom_indices = ()
        self._rebuild_atom_geometry()

        self._bond_edges = tuple(
            (bond.first_index, bond.second_index) for bond in connectivity
        )
        self._bond_display_orders = normalized_bond_display_orders(
            connectivity,
            bond_display_orders,
        )
        self._rebuild_bond_geometry()
        self._hover_atom_index = None
        self._hover_bond_edge = None
        self.clear_torsion_gizmo()
        self._measurement_pick_indices = ()
        self._update_measurement_pick_text_actors()
        self.set_highlighted_atom_indices((), ())
        self.set_grouped_atom_indices((), ())
        self.set_surface_verification((), ())
        self.set_preview_atoms(())
        self.set_lattice_extension_preview_guide(None)
        self._measurement_annotations = ()
        self._rendered_measurement_ids = ()
        self.set_annotations((), ())
        self.set_parameter_labels({}, {})
        self._rebuild_element_labels()

    def update_molecule_coordinates(self, structure: MolecularStructure) -> None:
        """Update only atom centers while preserving ordering and rendered edges."""

        if not isinstance(structure, MolecularStructure):
            raise TypeError("coordinate updates require a MolecularStructure")
        if len(structure) != len(self._atom_elements):
            raise VisualizationError(
                "coordinate update atom count does not match the displayed molecule"
            )
        if tuple(atom.element for atom in structure) != self._atom_elements:
            raise VisualizationError(
                "coordinate updates cannot change atom ordering or elements"
            )
        if len(self._atom_coordinates) != len(structure):
            raise VisualizationError("displayed atom geometry is unavailable")
        coordinates: list[tuple[float, float, float]] = []
        for expected_index, atom in enumerate(structure):
            if atom.index != expected_index:
                raise VisualizationError(
                    "coordinate updates require preserved zero-based atom indexes"
                )
            coordinates.append((atom.x, atom.y, atom.z))
        self._atom_coordinates = tuple(coordinates)
        self.set_lattice_extension_preview_guide(None)
        self._rebuild_atom_geometry()
        self._rebuild_bond_geometry()
        self._rebuild_highlight_geometry()
        self._rebuild_atom_highlight_rings()
        self._rebuild_surface_geometry()
        self._update_measurement_pick_text_actors()
        self._rebuild_element_labels()
        self._rebuild_parameter_labels()
        if self._torsion_gizmo is not None:
            self._rebuild_torsion_geometry()
        else:
            self._rebuild_hover_bond_geometry()

    def update_connectivity(
        self,
        connectivity: Connectivity,
        bond_display_orders: Iterable[BondDisplayOrder] | None = None,
    ) -> None:
        """Replace rendered edges without disturbing coordinates or overlays."""

        if not isinstance(connectivity, Connectivity):
            raise TypeError("connectivity updates require Connectivity")
        if connectivity.atom_count != len(self._atom_elements):
            raise VisualizationError(
                "connectivity atom count does not match the displayed molecule: "
                f"{connectivity.atom_count} != {len(self._atom_elements)}"
            )
        bond_edges = tuple(
            (bond.first_index, bond.second_index) for bond in connectivity
        )
        self._bond_edges = bond_edges
        self._bond_display_orders = normalized_bond_display_orders(
            connectivity,
            bond_display_orders,
        )
        self._rebuild_bond_geometry()
        available_edges = set(bond_edges)
        if self._hover_bond_edge not in available_edges:
            self._hover_bond_edge = None
        if self._torsion_gizmo is not None:
            selected_edge = tuple(
                sorted(
                    (
                        self._torsion_gizmo.fixed_atom_index,
                        self._torsion_gizmo.rotating_atom_index,
                    )
                )
            )
            if selected_edge not in available_edges:
                self.clear_torsion_gizmo()
        else:
            self._rebuild_hover_bond_geometry()

    def set_view_preferences(self, preferences: ViewPreferences) -> None:
        """Apply presentation-only preferences without touching molecular data."""

        if not isinstance(preferences, ViewPreferences):
            raise TypeError("molecular view preferences must be ViewPreferences")
        self._view_preferences = preferences
        self._tube_filter.SetRadius(
            BOND_TUBE_RADIUS * preferences.bond_thickness_scale
        )
        self._selected_bond_tube_filter.SetRadius(
            BOND_TUBE_RADIUS * 1.55 * preferences.bond_thickness_scale
        )
        self._rebuild_atom_geometry()
        self._rebuild_bond_geometry()
        self._rebuild_highlight_geometry()
        self._rebuild_atom_highlight_rings()
        self._rebuild_surface_geometry()
        self._update_measurement_pick_text_actors()
        self._rebuild_preview_geometry()
        self._rebuild_element_labels()
        if self._torsion_gizmo is not None:
            self._rebuild_torsion_geometry()
        else:
            self._rebuild_hover_bond_geometry()

    def set_orbital_surface(
        self,
        scalar_field: CubeScalarField | None,
        preferences: OrbitalSurfacePreferences | None = None,
    ) -> None:
        """Replace the optional signed scalar surface without touching atoms."""

        self._orbital_surface.set_field(scalar_field, preferences)

    def set_orbital_surface_preferences(
        self,
        preferences: OrbitalSurfacePreferences,
    ) -> None:
        """Update Cube presentation, re-extracting only for a new isovalue."""

        self._orbital_surface.set_preferences(preferences)

    def _rebuild_atom_geometry(self) -> None:
        points = vtkPoints()
        points.SetDataTypeToDouble()
        scale_array = vtkFloatArray()
        scale_array.SetName("visual_radius")
        scale_array.SetNumberOfComponents(1)
        color_array = vtkUnsignedCharArray()
        color_array.SetName("display_color")
        color_array.SetNumberOfComponents(3)
        atom_index_array = vtkIdTypeArray()
        atom_index_array.SetName("atom_index")
        atom_index_array.SetNumberOfComponents(1)

        for atom_index in self._visible_atom_indices():
            element = self._atom_elements[atom_index]
            default_color = ELEMENT_COLORS_RGB[element]
            display_color = self._view_preferences.display_color(
                element,
                default_color,
            )
            points.InsertNextPoint(*self._atom_coordinates[atom_index])
            scale_array.InsertNextValue(self._visual_radii[atom_index])
            color_array.InsertNextTuple3(*display_color)
            atom_index_array.InsertNextValue(atom_index)

        self._atom_polydata.SetPoints(points)
        self._atom_polydata.GetPointData().Initialize()
        self._atom_polydata.GetPointData().AddArray(scale_array)
        self._atom_polydata.GetPointData().SetScalars(color_array)
        self._atom_polydata.GetPointData().AddArray(atom_index_array)
        self._atom_polydata.Modified()

    def _visible_atom_indices(self) -> tuple[int, ...]:
        return tuple(
            atom_index
            for atom_index, element in enumerate(self._atom_elements)
            if not (
                self._view_preferences.hide_hydrogen and element == "H"
            )
            and atom_index not in self._preview_hidden_atom_indices
        )

    def _is_atom_visible(self, atom_index: int) -> bool:
        return not (
            self._view_preferences.hide_hydrogen
            and self._atom_elements[atom_index] == "H"
        ) and atom_index not in self._preview_hidden_atom_indices

    def _rebuild_bond_geometry(self) -> None:
        """Rebuild all visual strands while retaining one logical edge identity."""

        points = vtkPoints()
        points.SetDataTypeToDouble()
        for atom_index in range(len(self._atom_elements)):
            points.InsertNextPoint(*self._point_for_atom(atom_index))
        lines = vtkCellArray()
        bond_index_array = vtkIdTypeArray()
        bond_index_array.SetName("bond_index")
        bond_index_array.SetNumberOfComponents(1)
        order_by_edge = {
            record.edge: record.order for record in self._bond_display_orders
        }
        for bond_index, edge in enumerate(self._bond_edges):
            first_index, second_index = edge
            if not self._is_atom_visible(first_index) or not self._is_atom_visible(
                second_index
            ):
                continue
            display_order = order_by_edge[edge]
            segments = bond_strand_segments(
                self._point_for_atom(first_index),
                self._point_for_atom(second_index),
                display_order,
            )
            for start, end in segments:
                if display_order == 1:
                    start_id = first_index
                    end_id = second_index
                else:
                    start_id = points.InsertNextPoint(*start)
                    end_id = points.InsertNextPoint(*end)
                lines.InsertNextCell(2)
                lines.InsertCellPoint(start_id)
                lines.InsertCellPoint(end_id)
                bond_index_array.InsertNextValue(bond_index)

        self._bond_polydata.SetPoints(points)
        self._bond_polydata.SetLines(lines)
        self._bond_polydata.GetCellData().Initialize()
        self._bond_polydata.GetCellData().AddArray(bond_index_array)
        self._bond_polydata.Modified()

    def reference_direction_for_bond(
        self,
        fixed_atom_index: int,
        rotating_atom_index: int,
    ) -> tuple[float, float, float]:
        """Choose a deterministic camera-aware visible radial reference."""

        fixed = self._point_for_atom(fixed_atom_index)
        rotating = self._point_for_atom(rotating_atom_index)
        axis = _normalized(_subtract(rotating, fixed), "selected bond axis")
        camera = self._renderer.GetActiveCamera()
        candidates = (
            tuple(float(value) for value in camera.GetViewUp()),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        for candidate in candidates:
            projected = _subtract(candidate, _scale(axis, _dot(candidate, axis)))
            if hypot(*projected) > 1.0e-10:
                return _normalized(projected, "torsion radial reference")
        raise VisualizationError("no visible torsion reference direction is available")

    def set_torsion_gizmo(self, gizmo: TorsionGizmo | None) -> None:
        """Set or clear the selected-bond highlight and torsion interaction visual."""

        if gizmo is None:
            self.clear_torsion_gizmo()
            return
        if not isinstance(gizmo, TorsionGizmo):
            raise TypeError("torsion visual state must be TorsionGizmo or None")
        fixed = self._validated_displayed_atom_index(
            gizmo.fixed_atom_index,
            "fixed torsion endpoint",
        )
        rotating = self._validated_displayed_atom_index(
            gizmo.rotating_atom_index,
            "rotating torsion endpoint",
        )
        if tuple(sorted((fixed, rotating))) not in self._bond_edges:
            raise VisualizationError(
                "torsion endpoints must identify an existing rendered bond"
            )
        fixed_point = self._point_for_atom(fixed)
        rotating_point = self._point_for_atom(rotating)
        axis = _normalized(
            _subtract(rotating_point, fixed_point),
            "selected bond axis",
        )
        projected_reference = _subtract(
            gizmo.reference_direction,
            _scale(axis, _dot(gizmo.reference_direction, axis)),
        )
        reference = _normalized(
            projected_reference,
            "torsion radial reference",
        )
        self._torsion_gizmo = TorsionGizmo(
            fixed,
            rotating,
            reference,
            gizmo.angle_degrees,
        )
        self._rebuild_torsion_geometry()

    def clear_torsion_gizmo(self) -> None:
        """Clear every selected-bond and torsion actor without touching overlays."""

        self._torsion_gizmo = None
        self._torsion_center = None
        self._torsion_label_position = None
        self._torsion_handle_position = None
        for polydata in (
            self._selected_bond_polydata,
            self._torsion_circle_polydata,
            self._torsion_fixed_ray_polydata,
            self._torsion_moving_ray_polydata,
            self._torsion_angle_polydata,
            self._torsion_side_polydata,
        ):
            _set_line_segments(polydata, ())
        self._torsion_angle_text_actor.SetInput("")
        self._torsion_angle_text_actor.SetVisibility(False)
        self._torsion_handle_actor.SetVisibility(False)
        self._rebuild_hover_bond_geometry()

    def set_torsion_angle_text_visible(self, visible: bool) -> None:
        """Choose whether the VTK-owned torsion value is rendered."""

        if not isinstance(visible, bool):
            raise TypeError("torsion angle text visibility must be boolean")
        self._torsion_angle_text_requested_visible = visible
        self._torsion_angle_text_actor.SetVisibility(
            visible
            and self._torsion_gizmo is not None
            and self._torsion_label_position is not None
        )

    def torsion_label_display_position(self) -> tuple[float, float] | None:
        """Return the exact displayed location shared by text and its editor."""

        if self._torsion_label_position is None:
            return None
        display = _display_point(self._renderer, self._torsion_label_position)
        if display is None:
            return None
        offset_x, offset_y = self._torsion_angle_text_actor.GetDisplayOffset()
        return display[0] + offset_x, display[1] + offset_y

    def pick_bond_edge(
        self,
        display_x: int,
        display_y: int,
    ) -> tuple[int, int] | None:
        """Map one rendered bond cell back to its authoritative edge identity."""

        x, y = _validated_display_position(display_x, display_y)
        if not self._bond_edges:
            return None
        self._tube_filter.Update()
        picker = vtkCellPicker()
        picker.SetTolerance(TORSION_PICK_TOLERANCE)
        picker.PickFromListOn()
        picker.AddPickList(self._bond_actor)
        if not picker.Pick(x, y, 0.0, self._renderer):
            return None
        cell_id = picker.GetCellId()
        if cell_id < 0:
            return None
        bond_ids = self._tube_filter.GetOutput().GetCellData().GetArray(
            "bond_index"
        )
        if bond_ids is None or cell_id >= bond_ids.GetNumberOfTuples():
            raise VisualizationError(
                "rendered bond selection is missing its Connectivity edge identity"
            )
        bond_index = int(bond_ids.GetValue(cell_id))
        if bond_index < 0 or bond_index >= len(self._bond_edges):
            raise VisualizationError("rendered bond selection ID is out of range")
        return self._bond_edges[bond_index]

    def pick_torsion_target(
        self,
        display_x: int,
        display_y: int,
    ) -> str | None:
        """Resolve a selected-bond gizmo target without consulting atom proximity."""

        x, y = _validated_display_position(display_x, display_y)
        if self._torsion_gizmo is None:
            return None
        label_display = self.torsion_label_display_position()
        label_hit = (
            label_display is not None
            and abs(x - label_display[0]) <= TORSION_LABEL_PICK_HALF_WIDTH_PIXELS
            and abs(y - label_display[1]) <= TORSION_LABEL_PICK_HALF_HEIGHT_PIXELS
        )
        handle_picker = vtkCellPicker()
        handle_picker.SetTolerance(TORSION_PICK_TOLERANCE)
        handle_picker.PickFromListOn()
        handle_picker.AddPickList(self._torsion_handle_actor)
        if handle_picker.Pick(x, y, 0.0, self._renderer):
            if label_hit and label_display is not None:
                handle_display = (
                    None
                    if self._torsion_handle_position is None
                    else _display_point(
                        self._renderer,
                        self._torsion_handle_position,
                    )
                )
                if handle_display is not None and hypot(
                    x - label_display[0],
                    y - label_display[1],
                ) <= hypot(
                    x - handle_display[0],
                    y - handle_display[1],
                ):
                    return "angle_label"
            return "rotation_handle"
        if label_hit:
            return "angle_label"

        picker = vtkCellPicker()
        picker.SetTolerance(TORSION_PICK_TOLERANCE)
        picker.PickFromListOn()
        actor_targets = (
            (self._torsion_side_actor, "side_arrow"),
            (self._torsion_moving_ray_actor, "moving_ray"),
            (self._torsion_circle_actor, "circle"),
            (self._selected_bond_actor, "selected_bond"),
        )
        for actor, _target in actor_targets:
            picker.AddPickList(actor)
        if picker.Pick(x, y, 0.0, self._renderer):
            actor = picker.GetActor()
            for candidate, target in actor_targets:
                if actor is candidate:
                    return target
        return None

    def torsion_drag_direction(
        self,
        target: str,
        display_x: int,
        display_y: int,
    ) -> tuple[float, float]:
        """Capture one stable VTK-display drag direction at mouse-down."""

        x, y = _validated_display_position(display_x, display_y)
        gizmo = self._torsion_gizmo
        if gizmo is None or self._torsion_center is None:
            raise VisualizationError("no torsion gizmo is active")
        if target in {"circle", "moving_ray"}:
            center = _display_point(self._renderer, self._torsion_center)
            if center is not None:
                radial_x = x - center[0]
                radial_y = y - center[1]
                tangent = (-radial_y, radial_x)
                if hypot(*tangent) > 1.0e-8:
                    length = hypot(*tangent)
                    return tangent[0] / length, tangent[1] / length
        fixed = _display_point(
            self._renderer,
            self._point_for_atom(gizmo.fixed_atom_index),
        )
        rotating = _display_point(
            self._renderer,
            self._point_for_atom(gizmo.rotating_atom_index),
        )
        if fixed is not None and rotating is not None:
            projected_axis = (
                rotating[0] - fixed[0],
                rotating[1] - fixed[1],
            )
            perpendicular = (-projected_axis[1], projected_axis[0])
            if hypot(*perpendicular) > 1.0e-8:
                length = hypot(*perpendicular)
                return perpendicular[0] / length, perpendicular[1] / length
        return 1.0, 0.0

    def torsion_pointer_angle_degrees(
        self,
        display_x: int,
        display_y: int,
    ) -> float:
        """Map one display pointer to its signed angle around the torsion axis."""

        x, y = _validated_display_position(display_x, display_y)
        gizmo = self._torsion_gizmo
        center = self._torsion_center
        if gizmo is None or center is None:
            raise VisualizationError("no torsion gizmo is active")
        fixed = self._point_for_atom(gizmo.fixed_atom_index)
        rotating = self._point_for_atom(gizmo.rotating_atom_index)
        axis = _normalized(_subtract(rotating, fixed), "selected bond axis")
        reference = _normalized(
            _subtract(
                gizmo.reference_direction,
                _scale(axis, _dot(gizmo.reference_direction, axis)),
            ),
            "torsion radial reference",
        )

        near = _world_point_at_display_depth(self._renderer, x, y, 0.0)
        far = _world_point_at_display_depth(self._renderer, x, y, 1.0)
        if near is not None and far is not None:
            ray = _subtract(far, near)
            denominator = _dot(ray, axis)
            if abs(denominator) > 1.0e-10 * max(1.0, hypot(*ray)):
                distance_along_ray = _dot(_subtract(center, near), axis) / (
                    denominator
                )
                plane_point = _add(
                    near,
                    _scale(ray, distance_along_ray),
                )
                radial = _subtract(plane_point, center)
                radial = _subtract(radial, _scale(axis, _dot(radial, axis)))
                if hypot(*radial) > 1.0e-10:
                    return _signed_torsion_angle_degrees(
                        reference,
                        radial,
                        axis,
                    )

        # A view exactly edge-on to the torsion plane has no unique 3D
        # ray/plane intersection. Preserve direct pointer control through the
        # projected radial direction rather than reverting to pixel gain.
        center_display = _display_point(self._renderer, center)
        reference_display = _display_point(
            self._renderer,
            _add(center, reference),
        )
        if center_display is None or reference_display is None:
            raise VisualizationError("torsion pointer cannot be projected")
        pointer = (x - center_display[0], y - center_display[1])
        projected_reference = (
            reference_display[0] - center_display[0],
            reference_display[1] - center_display[1],
        )
        if hypot(*pointer) <= 1.0e-8 or hypot(*projected_reference) <= 1.0e-8:
            raise VisualizationError("torsion pointer is too close to the axis")
        return degrees(
            atan2(
                projected_reference[0] * pointer[1]
                - projected_reference[1] * pointer[0],
                projected_reference[0] * pointer[0]
                + projected_reference[1] * pointer[1],
            )
        )

    def _rebuild_torsion_geometry(self) -> None:
        gizmo = self._torsion_gizmo
        if gizmo is None:
            return
        if not self._is_atom_visible(
            gizmo.fixed_atom_index
        ) or not self._is_atom_visible(gizmo.rotating_atom_index):
            for polydata in (
                self._selected_bond_polydata,
                self._torsion_circle_polydata,
                self._torsion_fixed_ray_polydata,
                self._torsion_moving_ray_polydata,
                self._torsion_angle_polydata,
                self._torsion_side_polydata,
            ):
                _set_line_segments(polydata, ())
            self._torsion_angle_text_actor.SetInput("")
            self._torsion_angle_text_actor.SetVisibility(False)
            self._torsion_handle_actor.SetVisibility(False)
            self._torsion_center = None
            self._torsion_label_position = None
            self._torsion_handle_position = None
            return
        fixed = self._point_for_atom(gizmo.fixed_atom_index)
        rotating = self._point_for_atom(gizmo.rotating_atom_index)
        axis = _normalized(_subtract(rotating, fixed), "selected bond axis")
        center = _midpoint(fixed, rotating)
        reference = _normalized(
            _subtract(
                gizmo.reference_direction,
                _scale(axis, _dot(gizmo.reference_direction, axis)),
            ),
            "torsion radial reference",
        )
        tangent = _normalized(_cross(axis, reference), "torsion tangent")
        current = _rotate_vector_about_axis(
            reference,
            axis,
            radians(gizmo.angle_degrees),
        )
        bond_length = dist(fixed, rotating)
        radius = max(TORSION_GIZMO_MIN_RADIUS, min(1.40, bond_length * 0.58))

        selected_edge = tuple(
            sorted((gizmo.fixed_atom_index, gizmo.rotating_atom_index))
        )
        display_order = next(
            record.order
            for record in self._bond_display_orders
            if record.edge == selected_edge
        )
        _set_line_segments(
            self._selected_bond_polydata,
            bond_strand_segments(fixed, rotating, display_order),
        )

        circle_points = tuple(
            _add(
                center,
                _add(
                    _scale(reference, radius * cos(angle)),
                    _scale(tangent, radius * sin(angle)),
                ),
            )
            for angle in (
                index * (5.0 * pi / 3.0) / 40.0 for index in range(41)
            )
        )
        circle_segments = list(zip(circle_points, circle_points[1:]))
        circle_end = circle_points[-1]
        circle_tangent = _normalized(
            _add(
                _scale(reference, -sin(5.0 * pi / 3.0)),
                _scale(tangent, cos(5.0 * pi / 3.0)),
            ),
            "torsion circle tangent",
        )
        circle_radial = _normalized(
            _subtract(circle_end, center),
            "torsion circle radius",
        )
        arrow_back = _subtract(circle_end, _scale(circle_tangent, radius * 0.22))
        circle_segments.extend(
            (
                (
                    circle_end,
                    _add(arrow_back, _scale(circle_radial, radius * 0.11)),
                ),
                (
                    circle_end,
                    _subtract(arrow_back, _scale(circle_radial, radius * 0.11)),
                ),
            )
        )
        _set_line_segments(self._torsion_circle_polydata, circle_segments)

        fixed_ray_end = _add(center, _scale(reference, radius))
        moving_ray_end = _add(center, _scale(current, radius))
        _set_line_segments(
            self._torsion_fixed_ray_polydata,
            ((center, fixed_ray_end),),
        )
        _set_line_segments(
            self._torsion_moving_ray_polydata,
            ((center, moving_ray_end),),
        )
        handle_radius = max(
            TORSION_HANDLE_MIN_RADIUS,
            radius * TORSION_HANDLE_RADIUS_SCALE,
        )
        self._torsion_handle_actor.SetPosition(*moving_ray_end)
        self._torsion_handle_actor.SetScale(
            handle_radius,
            handle_radius,
            handle_radius,
        )
        self._torsion_handle_actor.SetVisibility(True)
        self._torsion_handle_position = moving_ray_end

        angle_radians = radians(gizmo.angle_degrees)
        angle_steps = max(1, int(ceil(abs(gizmo.angle_degrees) / 7.5)))
        if abs(angle_radians) <= 1.0e-12:
            angle_segments: tuple[
                tuple[
                    tuple[float, float, float],
                    tuple[float, float, float],
                ],
                ...,
            ] = ()
        else:
            angle_points = tuple(
                _add(
                    center,
                    _scale(
                        _rotate_vector_about_axis(
                            reference,
                            axis,
                            angle_radians * index / angle_steps,
                        ),
                        radius * 0.72,
                    ),
                )
                for index in range(angle_steps + 1)
            )
            angle_segments = tuple(
                zip(angle_points, angle_points[1:])
            )
        _set_line_segments(self._torsion_angle_polydata, angle_segments)

        side_offset = _scale(reference, radius * 0.36)
        side_start = _add(
            _subtract(center, _scale(axis, radius * 0.48)),
            side_offset,
        )
        side_end = _add(
            _add(center, _scale(axis, radius * 1.18)),
            side_offset,
        )
        side_back = _subtract(side_end, _scale(axis, radius * 0.34))
        side_segments = (
            (side_start, side_end),
            (side_end, _add(side_back, _scale(reference, radius * 0.18))),
            (side_end, _subtract(side_back, _scale(reference, radius * 0.18))),
        )
        _set_line_segments(self._torsion_side_polydata, side_segments)

        label_direction = _rotate_vector_about_axis(
            reference,
            axis,
            angle_radians / 2.0,
        )
        label_position = _add(center, _scale(label_direction, radius * 0.88))
        angle_text = (
            "0.0°"
            if abs(gizmo.angle_degrees) <= 5.0e-13
            else f"{gizmo.angle_degrees:+.1f}°"
        )
        self._torsion_angle_text_actor.SetInput(angle_text)
        self._torsion_angle_text_actor.SetPosition(*label_position)
        self._torsion_center = center
        self._torsion_label_position = label_position
        self._torsion_angle_text_actor.SetVisibility(
            self._torsion_angle_text_requested_visible
        )

    def _validated_displayed_atom_index(self, value: int, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be an integer")
        if value < 0 or value >= len(self._atom_elements):
            raise VisualizationError(
                f"{label} is outside the displayed atom range: {value}"
            )
        return value

    def _point_for_atom(self, atom_index: int) -> tuple[float, float, float]:
        index = self._validated_displayed_atom_index(atom_index, "atom index")
        return self._atom_coordinates[index]

    def set_highlighted_atom_indices(
        self,
        primary_indices: Iterable[int],
        secondary_indices: Iterable[int] = (),
    ) -> None:
        """Render primary and secondary atom highlights as translucent shells."""

        primary = _validated_highlight_indices(
            primary_indices,
            len(self._visual_radii),
            "primary",
        )
        secondary = _validated_highlight_indices(
            secondary_indices,
            len(self._visual_radii),
            "secondary",
        )
        overlap = set(primary) & set(secondary)
        if overlap:
            raise VisualizationError(
                "primary and secondary highlight indexes must not overlap: "
                f"{tuple(sorted(overlap))}"
            )

        self._primary_highlight_indices = primary
        self._secondary_highlight_indices = secondary
        self._rebuild_highlight_geometry()

    def set_grouped_atom_indices(
        self,
        primary_indices: Iterable[int],
        secondary_indices: Iterable[int] = (),
    ) -> None:
        """Render fragment membership as open rings with 1-based labels."""

        primary = _validated_highlight_indices(
            primary_indices,
            len(self._visual_radii),
            "primary group",
        )
        secondary = _validated_highlight_indices(
            secondary_indices,
            len(self._visual_radii),
            "secondary group",
        )
        overlap = set(primary) & set(secondary)
        if overlap:
            raise VisualizationError(
                "primary and secondary group indexes must not overlap: "
                f"{tuple(sorted(overlap))}"
            )
        self._primary_group_indices = primary
        self._secondary_group_indices = secondary
        self._rebuild_atom_highlight_rings()

    @property
    def grouped_atom_indices(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        return self._primary_group_indices, self._secondary_group_indices

    def set_atom_highlight_colors(self, colors: AtomHighlightColors) -> None:
        """Apply one theme's hover and fragment-ring colors."""

        if not isinstance(colors, AtomHighlightColors):
            raise TypeError("atom highlight colors must be AtomHighlightColors")
        self._atom_highlight_colors = colors
        for mapper, rgb in (
            (self._primary_group_label_mapper, colors.primary_group),
            (self._secondary_group_label_mapper, colors.secondary_group),
            (self._hover_atom_label_mapper, colors.hover),
        ):
            mapper.GetLabelTextProperty().SetColor(
                *(component / 255.0 for component in rgb)
            )
        self._rebuild_atom_highlight_rings()

    @property
    def atom_highlight_colors(self) -> AtomHighlightColors:
        return self._atom_highlight_colors

    def set_hover_highlight(
        self,
        *,
        atom_index: int | None = None,
        bond_edge: tuple[int, int] | None = None,
    ) -> None:
        """Highlight at most one transient atom or bond hover target."""

        if atom_index is not None and bond_edge is not None:
            raise VisualizationError(
                "atom and bond hover highlights are mutually exclusive"
            )
        validated_atom = (
            None
            if atom_index is None
            else self._validated_displayed_atom_index(
                atom_index,
                "hover atom index",
            )
        )
        validated_edge: tuple[int, int] | None = None
        if bond_edge is not None:
            if not isinstance(bond_edge, tuple) or len(bond_edge) != 2:
                raise VisualizationError(
                    "hover bond edge must contain two atom indexes"
                )
            first = self._validated_displayed_atom_index(
                bond_edge[0],
                "hover bond atom index",
            )
            second = self._validated_displayed_atom_index(
                bond_edge[1],
                "hover bond atom index",
            )
            validated_edge = tuple(sorted((first, second)))
            if validated_edge not in set(self._bond_edges):
                raise VisualizationError(
                    f"hover edge {first + 1}-{second + 1} is not displayed"
                )
        self._hover_atom_index = validated_atom
        self._hover_bond_edge = validated_edge
        self._rebuild_atom_highlight_rings()
        if self._torsion_gizmo is None:
            self._rebuild_hover_bond_geometry()

    @property
    def hover_highlight(
        self,
    ) -> tuple[int | None, tuple[int, int] | None]:
        return self._hover_atom_index, self._hover_bond_edge

    def set_measurement_pick_feedback(
        self,
        atom_indices: Iterable[int],
    ) -> None:
        """Show ordered, temporary measurement picks without replacing highlights."""

        self._measurement_pick_indices = _validated_measurement_pick_indices(
            atom_indices,
            len(self._visual_radii),
        )
        self._rebuild_highlight_geometry()
        self._update_measurement_pick_text_actors()

    def set_surface_verification(
        self,
        left_indices: Iterable[int],
        right_indices: Iterable[int],
    ) -> None:
        """Set the independent logical Left/Right AITRANSS surface overlay."""

        left = _validated_highlight_indices(
            left_indices,
            len(self._visual_radii),
            "Left surface",
        )
        right = _validated_highlight_indices(
            right_indices,
            len(self._visual_radii),
            "Right surface",
        )
        if left and len(left) != 3:
            raise VisualizationError("Left surface overlay requires exactly three atoms")
        if right and len(right) != 3:
            raise VisualizationError("Right surface overlay requires exactly three atoms")
        if bool(left) != bool(right):
            raise VisualizationError("Left and Right surface overlays must be set together")
        if set(left) & set(right):
            raise VisualizationError("Left and Right surface overlays must not overlap")
        self._left_surface_indices = left
        self._right_surface_indices = right
        self._surface_labels = tuple(f"L {index + 1}" for index in left) + tuple(
            f"R {index + 1}" for index in right
        )
        self._rebuild_surface_geometry()

    @property
    def surface_verification_indices(
        self,
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        return self._left_surface_indices, self._right_surface_indices

    @property
    def surface_verification_labels(self) -> tuple[str, ...]:
        return self._surface_labels

    def set_parameter_labels(
        self,
        atom_labels: Mapping[int, str],
        bond_labels: Mapping[tuple[int, int], str],
    ) -> None:
        """Show batched atom/bond parameter text without changing the model."""

        validated_atoms: list[tuple[int, str]] = []
        for atom_index, text in atom_labels.items():
            index = self._validated_displayed_atom_index(
                atom_index,
                "parameter-label atom index",
            )
            validated_atoms.append((index, _parameter_label_text(text)))

        available_edges = set(self._bond_edges)
        validated_bonds: list[tuple[tuple[int, int], str]] = []
        for raw_edge, text in bond_labels.items():
            if not isinstance(raw_edge, tuple) or len(raw_edge) != 2:
                raise VisualizationError(
                    "parameter-label bond keys must contain two atom indexes"
                )
            first = self._validated_displayed_atom_index(
                raw_edge[0],
                "parameter-label bond atom index",
            )
            second = self._validated_displayed_atom_index(
                raw_edge[1],
                "parameter-label bond atom index",
            )
            edge = tuple(sorted((first, second)))
            if edge not in available_edges:
                raise VisualizationError(
                    f"parameter label edge {first + 1}-{second + 1} is not displayed"
                )
            validated_bonds.append((edge, _parameter_label_text(text)))

        self._parameter_atom_labels = tuple(sorted(validated_atoms))
        self._parameter_bond_labels = tuple(sorted(validated_bonds))
        self._rebuild_parameter_labels()

    @property
    def parameter_label_texts(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(text for _index, text in self._parameter_atom_labels),
            tuple(text for _edge, text in self._parameter_bond_labels),
        )

    def _rebuild_parameter_labels(self) -> None:
        atom_points = vtkPoints()
        atom_points.SetDataTypeToDouble()
        atom_text = vtkStringArray()
        atom_text.SetName("parameter_label")
        for atom_index, text in self._parameter_atom_labels:
            atom_points.InsertNextPoint(*self._atom_coordinates[atom_index])
            atom_text.InsertNextValue(text)
        _replace_labeled_points(
            self._parameter_atom_label_polydata,
            atom_points,
            atom_text,
        )
        self._parameter_atom_label_actor.SetVisibility(
            bool(self._parameter_atom_labels)
        )

        bond_points = vtkPoints()
        bond_points.SetDataTypeToDouble()
        bond_text = vtkStringArray()
        bond_text.SetName("parameter_label")
        for (first, second), text in self._parameter_bond_labels:
            bond_points.InsertNextPoint(
                *_midpoint(
                    self._atom_coordinates[first],
                    self._atom_coordinates[second],
                )
            )
            bond_text.InsertNextValue(text)
        _replace_labeled_points(
            self._parameter_bond_label_polydata,
            bond_points,
            bond_text,
        )
        self._parameter_bond_label_actor.SetVisibility(
            bool(self._parameter_bond_labels)
        )

    def _rebuild_surface_geometry(self) -> None:
        points = vtkPoints()
        points.SetDataTypeToDouble()
        radii = vtkFloatArray()
        radii.SetName("surface_shell_radius")
        radii.SetNumberOfComponents(1)
        colors = vtkUnsignedCharArray()
        colors.SetName("surface_shell_color")
        colors.SetNumberOfComponents(3)
        entries = tuple(
            (index, LEFT_SURFACE_COLOR_RGB, f"L {index + 1}")
            for index in self._left_surface_indices
            if self._is_atom_visible(index)
        ) + tuple(
            (index, RIGHT_SURFACE_COLOR_RGB, f"R {index + 1}")
            for index in self._right_surface_indices
            if self._is_atom_visible(index)
        )
        for index, color, _label in entries:
            points.InsertNextPoint(self._point_for_atom(index))
            radii.InsertNextValue(
                self._visual_radii[index] * SURFACE_SHELL_SIZE_MULTIPLIER
            )
            colors.InsertNextTuple3(*color)
        self._surface_polydata.SetPoints(points)
        self._surface_polydata.GetPointData().Initialize()
        self._surface_polydata.GetPointData().AddArray(radii)
        self._surface_polydata.GetPointData().SetScalars(colors)
        self._surface_polydata.Modified()
        for actor_index, actor in enumerate(self._surface_text_actors):
            if actor_index < len(entries):
                atom_index, color, label = entries[actor_index]
                actor.SetInput(label)
                actor.SetPosition(*self._point_for_atom(atom_index))
                actor.GetTextProperty().SetColor(
                    *(component / 255.0 for component in color)
                )
                actor.SetVisibility(True)
            else:
                actor.SetInput("")
                actor.SetVisibility(False)

    def _rebuild_highlight_geometry(self) -> None:
        points = vtkPoints()
        points.SetDataTypeToDouble()
        radius_array = vtkFloatArray()
        radius_array.SetName("shell_radius")
        radius_array.SetNumberOfComponents(1)
        color_array = vtkUnsignedCharArray()
        color_array.SetName("shell_color")
        color_array.SetNumberOfComponents(3)
        atom_index_array = vtkIdTypeArray()
        atom_index_array.SetName("atom_index")
        atom_index_array.SetNumberOfComponents(1)

        measurement_indices = set(self._measurement_pick_indices)
        for atom_index, color, size_multiplier in (
            *(
                (
                    index,
                    PRIMARY_HIGHLIGHT_COLOR_RGB,
                    HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
                )
                for index in self._primary_highlight_indices
                if index not in measurement_indices
            ),
            *(
                (
                    index,
                    SECONDARY_HIGHLIGHT_COLOR_RGB,
                    HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
                )
                for index in self._secondary_highlight_indices
                if index not in measurement_indices
            ),
            *(
                (
                    index,
                    MEASUREMENT_HIGHLIGHT_COLOR_RGB,
                    MEASUREMENT_HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
                )
                for index in self._measurement_pick_indices
            ),
        ):
            if not self._is_atom_visible(atom_index):
                continue
            points.InsertNextPoint(self._point_for_atom(atom_index))
            radius_array.InsertNextValue(
                self._visual_radii[atom_index] * size_multiplier
            )
            color_array.InsertNextTuple3(*color)
            atom_index_array.InsertNextValue(atom_index)

        self._highlight_polydata.SetPoints(points)
        self._highlight_polydata.GetPointData().Initialize()
        self._highlight_polydata.GetPointData().AddArray(radius_array)
        self._highlight_polydata.GetPointData().SetScalars(color_array)
        self._highlight_polydata.GetPointData().AddArray(atom_index_array)
        self._highlight_polydata.Modified()

    def _rebuild_atom_highlight_rings(self) -> None:
        """Rebuild camera-facing open rings and their 1-based atom labels."""

        camera = self._renderer.GetActiveCamera()
        raw_view_direction = tuple(
            float(value) for value in camera.GetDirectionOfProjection()
        )
        raw_view_up = tuple(float(value) for value in camera.GetViewUp())
        view_direction = _normalized(
            raw_view_direction,
            "camera projection direction",
        )
        requested_up = _normalized(
            raw_view_up,
            "camera view-up direction",
        )
        view_right = _normalized(
            _cross(view_direction, requested_up),
            "camera view-right direction",
        )
        view_up = _normalized(
            _cross(view_right, view_direction),
            "orthogonal camera view-up direction",
        )
        self._atom_highlight_camera_signature = (
            *raw_view_direction,
            *raw_view_up,
        )

        hover = self._hover_atom_index
        entries: list[tuple[int, tuple[int, int, int], float, str]] = []
        entries.extend(
            (
                index,
                self._atom_highlight_colors.primary_group,
                ATOM_GROUP_RING_SIZE_MULTIPLIER,
                "primary",
            )
            for index in self._primary_group_indices
            if index != hover and self._is_atom_visible(index)
        )
        entries.extend(
            (
                index,
                self._atom_highlight_colors.secondary_group,
                ATOM_GROUP_RING_SIZE_MULTIPLIER,
                "secondary",
            )
            for index in self._secondary_group_indices
            if index != hover and self._is_atom_visible(index)
        )
        if hover is not None and self._is_atom_visible(hover):
            entries.append(
                (
                    hover,
                    self._atom_highlight_colors.hover,
                    ATOM_HOVER_RING_SIZE_MULTIPLIER,
                    "hover",
                )
            )

        points = vtkPoints()
        points.SetDataTypeToDouble()
        lines = vtkCellArray()
        colors = vtkUnsignedCharArray()
        colors.SetName("atom_highlight_ring_color")
        colors.SetNumberOfComponents(3)
        label_entries: dict[
            str,
            list[tuple[tuple[float, float, float], str]],
        ] = {"primary": [], "secondary": [], "hover": []}
        for atom_index, color, radius_multiplier, channel in entries:
            center = self._point_for_atom(atom_index)
            visual_radius = self._visual_radii[atom_index]
            ring_radius = visual_radius * radius_multiplier
            first_point = points.GetNumberOfPoints()
            for segment in range(ATOM_HIGHLIGHT_RING_SEGMENTS):
                angle = 2.0 * pi * segment / ATOM_HIGHLIGHT_RING_SEGMENTS
                offset = _add(
                    _scale(view_right, ring_radius * cos(angle)),
                    _scale(view_up, ring_radius * sin(angle)),
                )
                points.InsertNextPoint(_add(center, offset))
            lines.InsertNextCell(ATOM_HIGHLIGHT_RING_SEGMENTS + 1)
            for segment in range(ATOM_HIGHLIGHT_RING_SEGMENTS):
                lines.InsertCellPoint(first_point + segment)
            lines.InsertCellPoint(first_point)
            colors.InsertNextTuple3(*color)
            label_entries[channel].append(
                (
                    _add(
                        center,
                        _scale(
                            view_up,
                            visual_radius * ATOM_INDEX_LABEL_OFFSET_MULTIPLIER,
                        ),
                    ),
                    str(atom_index + 1),
                )
            )

        self._atom_highlight_ring_polydata.Initialize()
        self._atom_highlight_ring_polydata.SetPoints(points)
        self._atom_highlight_ring_polydata.SetLines(lines)
        self._atom_highlight_ring_polydata.GetCellData().SetScalars(colors)
        self._atom_highlight_ring_polydata.Modified()
        self._atom_highlight_ring_actor.SetVisibility(bool(entries))

        for channel, polydata, actor in (
            (
                "primary",
                self._primary_group_label_polydata,
                self._primary_group_label_actor,
            ),
            (
                "secondary",
                self._secondary_group_label_polydata,
                self._secondary_group_label_actor,
            ),
            (
                "hover",
                self._hover_atom_label_polydata,
                self._hover_atom_label_actor,
            ),
        ):
            _replace_atom_index_labels(polydata, label_entries[channel])
            actor.SetVisibility(bool(label_entries[channel]))

    def _rebuild_hover_bond_geometry(self) -> None:
        edge = self._hover_bond_edge
        if edge is None or self._torsion_gizmo is not None:
            _set_line_segments(self._selected_bond_polydata, ())
            return
        if not self._is_atom_visible(edge[0]) or not self._is_atom_visible(edge[1]):
            _set_line_segments(self._selected_bond_polydata, ())
            return
        display_order = next(
            record.order for record in self._bond_display_orders if record.edge == edge
        )
        _set_line_segments(
            self._selected_bond_polydata,
            bond_strand_segments(
                self._point_for_atom(edge[0]),
                self._point_for_atom(edge[1]),
                display_order,
            ),
        )

    def _update_measurement_pick_text_actors(self) -> None:
        labels = tuple(
            str(order)
            for order in range(1, len(self._measurement_pick_indices) + 1)
        )
        visible_entries = tuple(
            (atom_index, labels[index])
            for index, atom_index in enumerate(self._measurement_pick_indices)
            if self._is_atom_visible(atom_index)
        )
        for actor_index, actor in enumerate(self._measurement_pick_text_actors):
            if actor_index < len(visible_entries):
                atom_index, label = visible_entries[actor_index]
                actor.SetInput(label)
                actor.SetPosition(*self._point_for_atom(atom_index))
                actor.SetDisplayOffset(*MEASUREMENT_PICK_LABEL_OFFSET_PIXELS)
                actor.SetVisibility(True)
            else:
                actor.SetInput("")
                actor.SetVisibility(False)
        self._measurement_pick_labels = labels

    def set_preview_atoms(
        self,
        preview_atoms: Iterable[PreviewAtom],
        *,
        hidden_atom_indices: Iterable[int] = (),
    ) -> None:
        """Render visual-only atoms and transiently hide replaced base atoms."""

        atoms = tuple(preview_atoms)
        if any(not isinstance(atom, PreviewAtom) for atom in atoms):
            raise TypeError("preview atoms must contain PreviewAtom instances")
        hidden = _validated_preview_hidden_indices(
            hidden_atom_indices,
            len(self._atom_elements),
        )
        if hidden and not atoms:
            raise VisualizationError(
                "preview-hidden atoms require at least one preview atom"
            )
        hidden_changed = hidden != self._preview_hidden_atom_indices
        self._preview_atoms = atoms
        self._preview_hidden_atom_indices = hidden
        if hidden_changed:
            self._rebuild_atom_geometry()
            self._rebuild_bond_geometry()
            self._rebuild_highlight_geometry()
            self._rebuild_surface_geometry()
            self._update_measurement_pick_text_actors()
            if self._torsion_gizmo is not None:
                self._rebuild_torsion_geometry()
        self._rebuild_preview_geometry()
        self._rebuild_element_labels()

    def set_lattice_extension_preview_guide(
        self,
        guide: LatticeExtensionPreviewGuide | None,
    ) -> None:
        """Render one visual-only canonical bond and Au(111) layer guide."""

        if guide is not None and not isinstance(
            guide,
            LatticeExtensionPreviewGuide,
        ):
            raise TypeError(
                "lattice extension preview guide must be a "
                "LatticeExtensionPreviewGuide or None"
            )
        self._lattice_extension_preview_guide = guide
        self._rebuild_lattice_extension_preview_guide()

    def _rebuild_lattice_extension_preview_guide(self) -> None:
        """Refresh the dedicated unpickable hover-guide pipelines."""

        guide = self._lattice_extension_preview_guide
        if guide is None:
            empty_points = vtkPoints()
            empty_points.SetDataTypeToDouble()
            self._lattice_guide_bond_polydata.Initialize()
            self._lattice_guide_bond_polydata.SetPoints(empty_points)
            self._lattice_guide_bond_polydata.SetLines(vtkCellArray())
            self._lattice_guide_plane_polydata.Initialize()
            self._lattice_guide_plane_polydata.SetPoints(empty_points)
            self._lattice_guide_plane_polydata.SetPolys(vtkCellArray())
            self._lattice_guide_grid_polydata.Initialize()
            self._lattice_guide_grid_polydata.SetPoints(empty_points)
            self._lattice_guide_grid_polydata.SetLines(vtkCellArray())
            self._lattice_guide_bond_actor.SetVisibility(False)
            self._lattice_guide_plane_actor.SetVisibility(False)
            self._lattice_guide_grid_actor.SetVisibility(False)
            self._lattice_guide_bond_polydata.Modified()
            self._lattice_guide_plane_polydata.Modified()
            self._lattice_guide_grid_polydata.Modified()
            return

        color = guide.display_color_rgb
        self._lattice_guide_bond_actor.GetProperty().SetColor(
            *(component / 255.0 for component in color)
        )
        bond_points = vtkPoints()
        bond_points.SetDataTypeToDouble()
        bond_lines = vtkCellArray()
        for target in guide.bond_targets:
            _append_dashed_segment(
                bond_points,
                bond_lines,
                guide.center,
                target,
            )
        self._lattice_guide_bond_polydata.SetPoints(bond_points)
        self._lattice_guide_bond_polydata.SetLines(bond_lines)
        self._lattice_guide_bond_polydata.Modified()

        axial_keys = tuple(
            (q, r)
            for q in range(
                -LATTICE_GUIDE_PLANE_RINGS,
                LATTICE_GUIDE_PLANE_RINGS + 1,
            )
            for r in range(
                -LATTICE_GUIDE_PLANE_RINGS,
                LATTICE_GUIDE_PLANE_RINGS + 1,
            )
            if max(abs(q), abs(r), abs(q + r))
            <= LATTICE_GUIDE_PLANE_RINGS
        )
        plane_points = vtkPoints()
        plane_points.SetDataTypeToDouble()
        point_ids = {}
        point_coordinates = {}
        for key in axial_keys:
            q, r = key
            coordinate = _add(
                guide.center,
                _add(
                    _scale(guide.layer_basis_u, q),
                    _scale(guide.layer_basis_v, r),
                ),
            )
            point_ids[key] = plane_points.InsertNextPoint(*coordinate)
            point_coordinates[key] = coordinate

        max_radius = max(
            dist(guide.center, coordinate)
            for coordinate in point_coordinates.values()
        )
        plane_colors = vtkUnsignedCharArray()
        plane_colors.SetName("lattice_guide_plane_rgba")
        plane_colors.SetNumberOfComponents(4)
        grid_colors = vtkUnsignedCharArray()
        grid_colors.SetName("lattice_guide_grid_rgba")
        grid_colors.SetNumberOfComponents(4)
        for key in axial_keys:
            radial_fraction = min(
                1.0,
                dist(guide.center, point_coordinates[key]) / max_radius,
            )
            fade = 1.0 - radial_fraction
            plane_colors.InsertNextTuple4(
                *color,
                round(LATTICE_GUIDE_PLANE_CENTER_ALPHA * fade * fade),
            )
            grid_colors.InsertNextTuple4(
                *color,
                round(LATTICE_GUIDE_GRID_CENTER_ALPHA * fade),
            )

        plane_polygons = vtkCellArray()
        for q, r in axial_keys:
            for triangle in (
                ((q, r), (q + 1, r), (q, r + 1)),
                ((q + 1, r), (q + 1, r + 1), (q, r + 1)),
            ):
                if all(key in point_ids for key in triangle):
                    plane_polygons.InsertNextCell(3)
                    for key in triangle:
                        plane_polygons.InsertCellPoint(point_ids[key])

        grid_lines = vtkCellArray()
        for q, r in axial_keys:
            for neighbor in ((q + 1, r), (q, r + 1), (q + 1, r - 1)):
                if neighbor not in point_ids:
                    continue
                grid_lines.InsertNextCell(2)
                grid_lines.InsertCellPoint(point_ids[(q, r)])
                grid_lines.InsertCellPoint(point_ids[neighbor])

        self._lattice_guide_plane_polydata.SetPoints(plane_points)
        self._lattice_guide_plane_polydata.SetPolys(plane_polygons)
        self._lattice_guide_plane_polydata.GetPointData().Initialize()
        self._lattice_guide_plane_polydata.GetPointData().SetScalars(
            plane_colors
        )
        self._lattice_guide_plane_polydata.Modified()
        self._lattice_guide_grid_polydata.SetPoints(plane_points)
        self._lattice_guide_grid_polydata.SetLines(grid_lines)
        self._lattice_guide_grid_polydata.GetPointData().Initialize()
        self._lattice_guide_grid_polydata.GetPointData().SetScalars(
            grid_colors
        )
        self._lattice_guide_grid_polydata.Modified()
        self._lattice_guide_bond_actor.SetVisibility(True)
        self._lattice_guide_plane_actor.SetVisibility(True)
        self._lattice_guide_grid_actor.SetVisibility(True)

    def _rebuild_preview_geometry(self) -> None:
        """Refresh preview glyph presentation from retained visual-only atoms."""

        points = vtkPoints()
        points.SetDataTypeToDouble()
        radius_array = vtkFloatArray()
        radius_array.SetName("preview_radius")
        radius_array.SetNumberOfComponents(1)
        color_array = vtkUnsignedCharArray()
        color_array.SetName("preview_color")
        color_array.SetNumberOfComponents(3)

        for atom in self._preview_atoms:
            if self._view_preferences.hide_hydrogen and atom.element == "H":
                continue
            points.InsertNextPoint(*atom.coordinates)
            radius_array.InsertNextValue(atom.display_radius)
            display_color = self._view_preferences.element_color_overrides.get(
                atom.element,
                atom.display_color_rgb,
            )
            color_array.InsertNextTuple3(*display_color)

        self._preview_polydata.SetPoints(points)
        self._preview_polydata.GetPointData().Initialize()
        self._preview_polydata.GetPointData().AddArray(radius_array)
        self._preview_polydata.GetPointData().SetScalars(color_array)
        self._preview_polydata.Modified()

    def _rebuild_element_labels(self) -> None:
        """Recreate camera-facing, unpickable labels for visible atoms only."""

        for actor in self._element_label_actors:
            self._renderer.RemoveActor(actor)
        self._element_label_actors = ()
        self._element_label_bindings = ()
        if not self._view_preferences.show_element_labels:
            return

        label_entries = [
            (
                self._atom_elements[index],
                self._point_for_atom(index),
                self._visual_radii[index],
            )
            for index in self._visible_atom_indices()
        ]
        label_entries.extend(
            (atom.element, atom.coordinates, atom.display_radius)
            for atom in self._preview_atoms
            if atom.element is not None
            and not (
                self._view_preferences.hide_hydrogen and atom.element == "H"
            )
        )
        actors: list[vtkBillboardTextActor3D] = []
        bindings = []
        for element, coordinates, radius in label_entries:
            actor = _new_element_label_actor()
            actor.SetInput(element)
            actor.SetPosition(*coordinates)
            actor.SetVisibility(True)
            self._renderer.AddActor(actor)
            actors.append(actor)
            bindings.append((actor, coordinates, radius))
        self._element_label_actors = tuple(actors)
        self._element_label_bindings = tuple(bindings)
        self._update_element_label_offsets()

    def set_annotations(
        self,
        distance_annotations: Iterable[DistanceAnnotation],
        angle_annotations: Iterable[AngleAnnotation] = (),
    ) -> None:
        """Set temporary scientific-preview annotations without topology edits."""

        distances = tuple(distance_annotations)
        angles = tuple(angle_annotations)
        if any(not isinstance(item, DistanceAnnotation) for item in distances):
            raise TypeError(
                "distance annotations must contain DistanceAnnotation instances"
            )
        if any(not isinstance(item, AngleAnnotation) for item in angles):
            raise TypeError(
                "angle annotations must contain AngleAnnotation instances"
            )
        if len(distances) + len(angles) > MAX_ANNOTATION_TEXT_ACTORS:
            raise VisualizationError(
                "at most six simultaneous measured annotation labels are "
                "supported"
            )

        self._distance_annotations = distances
        self._angle_annotations = angles
        self._rebuild_annotation_geometry()

    def set_measurement_annotations(
        self,
        annotations: Iterable[MeasurementOverlayAnnotation],
    ) -> None:
        """Set independently owned persistent manual-measurement overlays."""

        measurement_annotations = tuple(annotations)
        if any(
            not isinstance(item, MeasurementOverlayAnnotation)
            for item in measurement_annotations
        ):
            raise TypeError(
                "measurement annotations must contain "
                "MeasurementOverlayAnnotation instances"
            )
        identifiers = tuple(
            item.measurement_id for item in measurement_annotations
        )
        if len(set(identifiers)) != len(identifiers):
            raise VisualizationError("measurement annotation IDs must be unique")
        self._measurement_annotations = measurement_annotations
        self._rendered_measurement_ids = identifiers
        self._rebuild_annotation_geometry()

    def _rebuild_annotation_geometry(self) -> None:
        """Batch temporary and manual annotations through the accepted actors."""

        manual_distances = tuple(
            item.annotation
            for item in self._measurement_annotations
            if isinstance(item.annotation, DistanceAnnotation)
        )
        manual_angles = tuple(
            item.annotation
            for item in self._measurement_annotations
            if isinstance(item.annotation, AngleAnnotation)
        )
        distances = self._distance_annotations + manual_distances
        angles = self._angle_annotations + manual_angles

        dash_points = vtkPoints()
        dash_points.SetDataTypeToDouble()
        dash_lines = vtkCellArray()
        labels: list[_AnnotationTextSpec] = []
        for annotation in distances:
            _append_dashed_segment(
                dash_points,
                dash_lines,
                annotation.start,
                annotation.end,
            )
            measured_distance = dist(annotation.start, annotation.end)
            labels.append(
                _AnnotationTextSpec(
                    f"{measured_distance:.2f} Å",
                    _midpoint(annotation.start, annotation.end),
                    (annotation.start, annotation.end),
                )
            )

        arc_points = vtkPoints()
        arc_points.SetDataTypeToDouble()
        arc_lines = vtkCellArray()
        scientific_angle_count = len(self._angle_annotations)
        for angle_index, annotation in enumerate(angles):
            if angle_index >= scientific_angle_count:
                _append_dashed_segment(
                    dash_points,
                    dash_lines,
                    annotation.reference_point,
                    annotation.vertex,
                )
                _append_dashed_segment(
                    dash_points,
                    dash_lines,
                    annotation.vertex,
                    annotation.target_point,
                )
            points, angle_radians = _angle_arc_points(annotation)
            for first, second in zip(points, points[1:]):
                first_id = arc_points.InsertNextPoint(*first)
                second_id = arc_points.InsertNextPoint(*second)
                arc_lines.InsertNextCell(2)
                arc_lines.InsertCellPoint(first_id)
                arc_lines.InsertCellPoint(second_id)
            labels.append(
                _AnnotationTextSpec(
                    f"{degrees(angle_radians):.1f}°",
                    points[len(points) // 2],
                )
            )

        self._dash_polydata.SetPoints(dash_points)
        self._dash_polydata.SetLines(dash_lines)
        self._dash_polydata.Modified()
        self._arc_polydata.SetPoints(arc_points)
        self._arc_polydata.SetLines(arc_lines)
        self._arc_polydata.Modified()
        self._ensure_annotation_text_actor_count(len(labels))
        distance_label_bindings = []
        for actor_index, actor in enumerate(self._annotation_text_actors):
            if actor_index < len(labels):
                label = labels[actor_index]
                actor.SetInput(label.text)
                actor.SetPosition(*label.position)
                actor.SetDisplayOffset(0, 0)
                actor.GetTextProperty().SetOrientation(0.0)
                actor.SetVisibility(True)
                if label.projected_segment is not None:
                    distance_label_bindings.append(
                        (actor, *label.projected_segment)
                    )
            else:
                actor.SetInput("")
                actor.SetVisibility(False)

        self._distance_label_bindings = tuple(distance_label_bindings)
        self._update_camera_dependent_annotation_text()
        self._annotation_texts = tuple(label.text for label in labels)

    def _ensure_annotation_text_actor_count(self, required_count: int) -> None:
        if required_count <= len(self._annotation_text_actors):
            return
        actors = list(self._annotation_text_actors)
        for _ in range(required_count - len(actors)):
            actor = _new_annotation_text_actor()
            self._renderer.AddActor(actor)
            actors.append(actor)
        self._annotation_text_actors = tuple(actors)

    def _renderer_started(self, _caller, _event_name) -> None:
        camera = self._renderer.GetActiveCamera()
        signature = (
            *tuple(float(value) for value in camera.GetDirectionOfProjection()),
            *tuple(float(value) for value in camera.GetViewUp()),
        )
        if signature != self._atom_highlight_camera_signature:
            self._rebuild_atom_highlight_rings()
        self._update_camera_dependent_annotation_text()
        self._update_element_label_offsets()

    def _update_camera_dependent_annotation_text(self) -> None:
        for actor, start, end in self._distance_label_bindings:
            projected_start = _display_point(self._renderer, start)
            projected_end = _display_point(self._renderer, end)
            if projected_start is None or projected_end is None:
                projected_start = (0.0, 0.0)
                projected_end = (0.0, 0.0)
            layout = _distance_label_screen_layout(
                projected_start,
                projected_end,
            )
            actor.SetDisplayOffset(*layout.display_offset)
            actor.GetTextProperty().SetOrientation(
                layout.orientation_degrees
            )

    def _update_element_label_offsets(self) -> None:
        if not self._element_label_bindings:
            return
        camera = self._renderer.GetActiveCamera()
        view_up = _normalized(
            tuple(float(value) for value in camera.GetViewUp()),
            "camera view-up direction",
        )
        for actor, coordinates, radius in self._element_label_bindings:
            center = _display_point(self._renderer, coordinates)
            surface = _display_point(
                self._renderer,
                _add(coordinates, _scale(view_up, radius)),
            )
            if center is None or surface is None:
                offset = ELEMENT_LABEL_MINIMUM_OFFSET_PIXELS
            else:
                projected_radius = hypot(
                    surface[0] - center[0],
                    surface[1] - center[1],
                )
                offset = max(
                    ELEMENT_LABEL_MINIMUM_OFFSET_PIXELS,
                    round(projected_radius + ELEMENT_LABEL_MARGIN_PIXELS),
                )
            actor.SetDisplayOffset(0, offset)

    def pick_atom_index(
        self,
        display_x: int,
        display_y: int,
        *,
        minimum_radius_pixels: float = MINIMUM_ATOM_PICK_RADIUS_PIXELS,
    ) -> int | None:
        """Return the deterministic visible atom nearest one screen position."""

        if self._atom_polydata.GetNumberOfPoints() == 0:
            return None
        for name, value in (("display x", display_x), ("display y", display_y)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if isinstance(minimum_radius_pixels, bool) or not isinstance(
            minimum_radius_pixels,
            (int, float),
        ):
            raise TypeError("minimum atom pick radius must be numeric")
        minimum_radius = float(minimum_radius_pixels)
        if not isfinite(minimum_radius) or minimum_radius < 0.0:
            raise ValueError(
                "minimum atom pick radius must be finite and non-negative"
            )

        render_window = self._renderer.GetRenderWindow()
        if render_window is None or not self._renderer.IsActiveCameraCreated():
            return None
        width, height = render_window.GetSize()
        if width <= 0 or height <= 0:
            return None

        camera = self._renderer.GetActiveCamera()
        view_up = _normalized(
            tuple(float(value) for value in camera.GetViewUp()),
            "camera view-up direction",
        )
        view_direction = _normalized(
            tuple(float(value) for value in camera.GetDirectionOfProjection()),
            "camera projection direction",
        )
        exact_candidates: list[tuple[float, float, int]] = []
        tolerance_candidates: list[tuple[float, float, int]] = []
        for atom_index in self._visible_atom_indices():
            coordinates = self._atom_coordinates[atom_index]
            visual_radius = self._visual_radii[atom_index]
            center = _display_point_with_depth(self._renderer, coordinates)
            radius_point = _display_point_with_depth(
                self._renderer,
                _add(coordinates, _scale(view_up, visual_radius)),
            )
            if center is None or radius_point is None:
                continue
            if center[2] < 0.0 or center[2] > 1.0:
                continue
            projected_radius = hypot(
                radius_point[0] - center[0],
                radius_point[1] - center[1],
            )
            screen_distance = hypot(
                float(display_x) - center[0],
                float(display_y) - center[1],
            )
            front = _display_point_with_depth(
                self._renderer,
                _subtract(
                    coordinates,
                    _scale(view_direction, visual_radius),
                ),
            )
            front_depth = center[2] if front is None else front[2]
            if screen_distance <= projected_radius:
                exact_candidates.append(
                    (front_depth, screen_distance, atom_index)
                )
            elif screen_distance <= minimum_radius:
                tolerance_candidates.append(
                    (screen_distance, front_depth, atom_index)
                )

        if exact_candidates:
            return min(exact_candidates)[2]
        if tolerance_candidates:
            return min(tolerance_candidates)[2]
        return None

    def pick_preview_target(
        self,
        display_x: int,
        display_y: int,
        targets: Iterable[PreviewPickTarget],
        *,
        minimum_radius_pixels: float = MINIMUM_ATOM_PICK_RADIUS_PIXELS,
    ) -> object | None:
        """Return the opaque identity nearest one projected preview position."""

        for name, value in (("display x", display_x), ("display y", display_y)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if isinstance(minimum_radius_pixels, bool) or not isinstance(
            minimum_radius_pixels,
            (int, float),
        ):
            raise TypeError("minimum preview pick radius must be numeric")
        minimum_radius = float(minimum_radius_pixels)
        if not isfinite(minimum_radius) or minimum_radius < 0.0:
            raise ValueError(
                "minimum preview pick radius must be finite and non-negative"
            )
        candidates = tuple(targets)
        for candidate in candidates:
            if not isinstance(candidate, PreviewPickTarget):
                raise TypeError(
                    "preview pick targets must contain PreviewPickTarget values"
                )
        if not candidates:
            return None

        render_window = self._renderer.GetRenderWindow()
        if render_window is None or not self._renderer.IsActiveCameraCreated():
            return None
        width, height = render_window.GetSize()
        if width <= 0 or height <= 0:
            return None

        camera = self._renderer.GetActiveCamera()
        view_up = _normalized(
            tuple(float(value) for value in camera.GetViewUp()),
            "camera view-up direction",
        )
        view_direction = _normalized(
            tuple(float(value) for value in camera.GetDirectionOfProjection()),
            "camera projection direction",
        )
        exact_matches: list[tuple[float, float, int, object]] = []
        tolerance_matches: list[tuple[float, float, int, object]] = []
        for order, candidate in enumerate(candidates):
            coordinates = candidate.coordinates
            visual_radius = candidate.display_radius
            center = _display_point_with_depth(self._renderer, coordinates)
            radius_point = _display_point_with_depth(
                self._renderer,
                _add(coordinates, _scale(view_up, visual_radius)),
            )
            if center is None or radius_point is None:
                continue
            if center[2] < 0.0 or center[2] > 1.0:
                continue
            projected_radius = hypot(
                radius_point[0] - center[0],
                radius_point[1] - center[1],
            )
            screen_distance = hypot(
                float(display_x) - center[0],
                float(display_y) - center[1],
            )
            front = _display_point_with_depth(
                self._renderer,
                _subtract(
                    coordinates,
                    _scale(view_direction, visual_radius),
                ),
            )
            front_depth = center[2] if front is None else front[2]
            if screen_distance <= projected_radius:
                exact_matches.append(
                    (front_depth, screen_distance, order, candidate.token)
                )
            elif screen_distance <= minimum_radius:
                tolerance_matches.append(
                    (screen_distance, front_depth, order, candidate.token)
                )

        if exact_matches:
            return min(exact_matches, key=lambda item: item[:3])[3]
        if tolerance_matches:
            return min(tolerance_matches, key=lambda item: item[:3])[3]
        return None

    def clear(self) -> None:
        """Remove render data while retaining all fixed VTK pipelines."""

        self._orbital_surface.set_field(None)
        self._visual_radii = ()
        self._atom_elements = ()
        self._atom_coordinates = ()
        self._bond_edges = ()
        self._bond_display_orders = ()
        self._primary_highlight_indices = ()
        self._secondary_highlight_indices = ()
        self._primary_group_indices = ()
        self._secondary_group_indices = ()
        self._atom_highlight_camera_signature = None
        self._hover_atom_index = None
        self._hover_bond_edge = None
        self._left_surface_indices = ()
        self._right_surface_indices = ()
        self._surface_labels = ()
        self._measurement_pick_indices = ()
        self._measurement_pick_labels = ()
        self._preview_atoms = ()
        self._preview_hidden_atom_indices = ()
        self._lattice_extension_preview_guide = None
        self._distance_annotations = ()
        self._angle_annotations = ()
        self._measurement_annotations = ()
        self._rendered_measurement_ids = ()
        self._annotation_texts = ()
        self._distance_label_bindings = ()
        self._parameter_atom_labels = ()
        self._parameter_bond_labels = ()
        for actor in self._element_label_actors:
            self._renderer.RemoveActor(actor)
        self._element_label_actors = ()
        self._element_label_bindings = ()
        self.clear_torsion_gizmo()
        empty_points = vtkPoints()
        empty_points.SetDataTypeToDouble()
        self._atom_polydata.Initialize()
        self._atom_polydata.SetPoints(empty_points)
        self._bond_polydata.Initialize()
        self._bond_polydata.SetPoints(empty_points)
        self._bond_polydata.SetLines(vtkCellArray())
        self._bond_polydata.GetCellData().Initialize()
        self._highlight_polydata.Initialize()
        self._highlight_polydata.SetPoints(empty_points)
        self._atom_highlight_ring_polydata.Initialize()
        self._atom_highlight_ring_polydata.SetPoints(empty_points)
        self._atom_highlight_ring_polydata.SetLines(vtkCellArray())
        self._primary_group_label_polydata.Initialize()
        self._primary_group_label_polydata.SetPoints(empty_points)
        self._secondary_group_label_polydata.Initialize()
        self._secondary_group_label_polydata.SetPoints(empty_points)
        self._hover_atom_label_polydata.Initialize()
        self._hover_atom_label_polydata.SetPoints(empty_points)
        self._surface_polydata.Initialize()
        self._surface_polydata.SetPoints(empty_points)
        self._preview_polydata.Initialize()
        self._preview_polydata.SetPoints(empty_points)
        self._lattice_guide_bond_polydata.Initialize()
        self._lattice_guide_bond_polydata.SetPoints(empty_points)
        self._lattice_guide_bond_polydata.SetLines(vtkCellArray())
        self._lattice_guide_plane_polydata.Initialize()
        self._lattice_guide_plane_polydata.SetPoints(empty_points)
        self._lattice_guide_plane_polydata.SetPolys(vtkCellArray())
        self._lattice_guide_grid_polydata.Initialize()
        self._lattice_guide_grid_polydata.SetPoints(empty_points)
        self._lattice_guide_grid_polydata.SetLines(vtkCellArray())
        self._dash_polydata.Initialize()
        self._dash_polydata.SetPoints(empty_points)
        self._dash_polydata.SetLines(vtkCellArray())
        self._arc_polydata.Initialize()
        self._arc_polydata.SetPoints(empty_points)
        self._arc_polydata.SetLines(vtkCellArray())
        self._parameter_atom_label_polydata.Initialize()
        self._parameter_atom_label_polydata.SetPoints(empty_points)
        self._parameter_bond_label_polydata.Initialize()
        self._parameter_bond_label_polydata.SetPoints(empty_points)
        self._parameter_atom_label_actor.SetVisibility(False)
        self._parameter_bond_label_actor.SetVisibility(False)
        self._atom_highlight_ring_actor.SetVisibility(False)
        self._primary_group_label_actor.SetVisibility(False)
        self._secondary_group_label_actor.SetVisibility(False)
        self._hover_atom_label_actor.SetVisibility(False)
        self._lattice_guide_bond_actor.SetVisibility(False)
        self._lattice_guide_plane_actor.SetVisibility(False)
        self._lattice_guide_grid_actor.SetVisibility(False)
        for actor in self._annotation_text_actors:
            actor.SetInput("")
            actor.SetVisibility(False)
        for actor in self._measurement_pick_text_actors:
            actor.SetInput("")
            actor.SetVisibility(False)
        for actor in self._surface_text_actors:
            actor.SetInput("")
            actor.SetVisibility(False)
        self._atom_polydata.Modified()
        self._bond_polydata.Modified()
        self._highlight_polydata.Modified()
        self._atom_highlight_ring_polydata.Modified()
        self._primary_group_label_polydata.Modified()
        self._secondary_group_label_polydata.Modified()
        self._hover_atom_label_polydata.Modified()
        self._surface_polydata.Modified()
        self._preview_polydata.Modified()
        self._lattice_guide_bond_polydata.Modified()
        self._lattice_guide_plane_polydata.Modified()
        self._lattice_guide_grid_polydata.Modified()
        self._dash_polydata.Modified()
        self._arc_polydata.Modified()
        self._parameter_atom_label_polydata.Modified()
        self._parameter_bond_label_polydata.Modified()


def _new_atom_index_label_mapper(
    polydata: vtkPolyData,
    color_rgb: tuple[int, int, int],
) -> vtkLabeledDataMapper:
    mapper = vtkLabeledDataMapper()
    mapper.SetInputData(polydata)
    mapper.SetLabelModeToLabelFieldData()
    mapper.SetFieldDataName("atom_index_label")
    text = mapper.GetLabelTextProperty()
    text.SetFontFamilyToArial()
    text.SetFontSize(ATOM_INDEX_LABEL_FONT_SIZE)
    text.BoldOn()
    text.SetItalic(False)
    text.ShadowOn()
    text.SetShadowOffset(1, -1)
    text.SetColor(*(component / 255.0 for component in color_rgb))
    text.SetJustificationToCentered()
    text.SetVerticalJustificationToCentered()
    return mapper


def _replace_atom_index_labels(
    polydata: vtkPolyData,
    entries: Iterable[tuple[tuple[float, float, float], str]],
) -> None:
    points = vtkPoints()
    points.SetDataTypeToDouble()
    labels = vtkStringArray()
    labels.SetName("atom_index_label")
    for position, label in entries:
        points.InsertNextPoint(position)
        labels.InsertNextValue(label)
    _replace_labeled_points(polydata, points, labels)


def _new_parameter_label_mapper(
    polydata: vtkPolyData,
    *,
    color: tuple[float, float, float],
) -> vtkLabeledDataMapper:
    mapper = vtkLabeledDataMapper()
    mapper.SetInputData(polydata)
    mapper.SetLabelModeToLabelFieldData()
    mapper.SetFieldDataName("parameter_label")
    text = mapper.GetLabelTextProperty()
    text.SetFontFamilyToArial()
    text.SetFontSize(12)
    text.SetBold(False)
    text.SetItalic(False)
    text.SetShadow(False)
    text.SetColor(*color)
    text.SetBackgroundColor(1.0, 1.0, 1.0)
    text.SetBackgroundOpacity(0.72)
    text.SetFrame(True)
    text.SetFrameColor(0.72, 0.76, 0.82)
    return mapper


def _replace_labeled_points(
    polydata: vtkPolyData,
    points: vtkPoints,
    labels: vtkStringArray,
) -> None:
    polydata.Initialize()
    polydata.SetPoints(points)
    polydata.GetPointData().AddArray(labels)
    polydata.Modified()


def _parameter_label_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("parameter label text must be a string")
    text = value.strip()
    if not text:
        raise VisualizationError("parameter label text must not be empty")
    if len(text) > 48:
        raise VisualizationError(
            "parameter label text must contain at most 48 characters"
        )
    return text


def _new_line_actor(
    polydata: vtkPolyData,
    color_rgb: tuple[int, int, int],
    width: float,
    *,
    pickable: bool = False,
) -> vtkActor:
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(polydata)
    mapper.ScalarVisibilityOff()
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(
        *(component / 255.0 for component in color_rgb)
    )
    actor.GetProperty().SetLineWidth(width)
    if pickable:
        actor.PickableOn()
    else:
        actor.PickableOff()
    return actor


def _new_torsion_text_actor() -> vtkBillboardTextActor3D:
    actor = vtkBillboardTextActor3D()
    actor.SetInput("")
    text_property = actor.GetTextProperty()
    text_property.SetFontSize(18)
    text_property.SetColor(
        *(component / 255.0 for component in TORSION_ANGLE_COLOR_RGB)
    )
    text_property.SetBold(True)
    text_property.SetJustificationToCentered()
    text_property.SetVerticalJustificationToCentered()
    actor.SetDisplayOffset(0, 30)
    actor.SetVisibility(False)
    actor.PickableOff()
    return actor


def _set_line_segments(
    polydata: vtkPolyData,
    segments: Iterable[
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
        ]
    ],
) -> None:
    points = vtkPoints()
    points.SetDataTypeToDouble()
    lines = vtkCellArray()
    for start, end in segments:
        first_id = points.InsertNextPoint(*start)
        second_id = points.InsertNextPoint(*end)
        lines.InsertNextCell(2)
        lines.InsertCellPoint(first_id)
        lines.InsertCellPoint(second_id)
    polydata.SetPoints(points)
    polydata.SetLines(lines)
    polydata.Modified()


def _validated_display_position(
    display_x: int,
    display_y: int,
) -> tuple[int, int]:
    for name, value in (("display x", display_x), ("display y", display_y)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    return display_x, display_y


def _new_annotation_text_actor() -> vtkBillboardTextActor3D:
    actor = vtkBillboardTextActor3D()
    actor.SetInput("")
    actor.SetVisibility(False)
    actor.PickableOff()
    text_property = actor.GetTextProperty()
    text_property.SetColor(0.08, 0.08, 0.08)
    text_property.SetFontSize(16)
    text_property.BoldOn()
    text_property.SetJustificationToCentered()
    text_property.SetVerticalJustificationToCentered()
    return actor


def _new_element_label_actor() -> vtkBillboardTextActor3D:
    actor = vtkBillboardTextActor3D()
    actor.SetInput("")
    actor.SetVisibility(False)
    actor.SetDisplayOffset(0, ELEMENT_LABEL_MINIMUM_OFFSET_PIXELS)
    actor.PickableOff()
    text_property = actor.GetTextProperty()
    text_property.SetColor(0.05, 0.05, 0.05)
    text_property.SetFontSize(15)
    text_property.BoldOn()
    text_property.ShadowOn()
    text_property.SetJustificationToCentered()
    text_property.SetVerticalJustificationToCentered()
    return actor


def _new_measurement_pick_text_actor() -> vtkBillboardTextActor3D:
    actor = vtkBillboardTextActor3D()
    actor.SetInput("")
    actor.SetVisibility(False)
    actor.SetDisplayOffset(*MEASUREMENT_PICK_LABEL_OFFSET_PIXELS)
    actor.PickableOff()
    text_property = actor.GetTextProperty()
    text_property.SetColor(0.08, 0.08, 0.08)
    text_property.SetBackgroundColor(1.0, 0.67, 0.08)
    text_property.SetBackgroundOpacity(0.88)
    text_property.SetFrameColor(0.82, 0.31, 0.0)
    text_property.SetFrameWidth(1)
    text_property.FrameOn()
    text_property.SetFontSize(18)
    text_property.BoldOn()
    text_property.SetJustificationToCentered()
    text_property.SetVerticalJustificationToCentered()
    return actor


def _new_surface_text_actor() -> vtkBillboardTextActor3D:
    actor = vtkBillboardTextActor3D()
    actor.SetInput("")
    actor.SetVisibility(False)
    actor.SetDisplayOffset(*SURFACE_LABEL_OFFSET_PIXELS)
    actor.PickableOff()
    text_property = actor.GetTextProperty()
    text_property.SetBackgroundColor(1.0, 1.0, 1.0)
    text_property.SetBackgroundOpacity(0.86)
    text_property.SetFrameColor(0.15, 0.15, 0.15)
    text_property.SetFrameWidth(1)
    text_property.FrameOn()
    text_property.SetFontSize(17)
    text_property.BoldOn()
    text_property.SetJustificationToCentered()
    text_property.SetVerticalJustificationToCentered()
    return actor


def _append_dashed_segment(
    points: vtkPoints,
    lines: vtkCellArray,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> None:
    for segment_start, segment_end in _dash_segments(start, end):
        first_id = points.InsertNextPoint(*segment_start)
        second_id = points.InsertNextPoint(*segment_end)
        lines.InsertNextCell(2)
        lines.InsertCellPoint(first_id)
        lines.InsertCellPoint(second_id)


def _display_point(
    renderer: vtkRenderer,
    world_point: tuple[float, float, float],
) -> tuple[float, float] | None:
    display_point = _display_point_with_depth(renderer, world_point)
    if display_point is None:
        return None
    return display_point[0], display_point[1]


def _display_point_with_depth(
    renderer: vtkRenderer,
    world_point: tuple[float, float, float],
) -> tuple[float, float, float] | None:
    render_window = renderer.GetRenderWindow()
    if render_window is None:
        return None
    if not renderer.IsActiveCameraCreated():
        return None
    width, height = render_window.GetSize()
    if width <= 0 or height <= 0:
        return None
    renderer.SetWorldPoint(*world_point, 1.0)
    renderer.WorldToDisplay()
    display_x, display_y, display_z = renderer.GetDisplayPoint()
    if not all(isfinite(value) for value in (display_x, display_y, display_z)):
        return None
    return float(display_x), float(display_y), float(display_z)


def _world_point_at_display_depth(
    renderer: vtkRenderer,
    display_x: int,
    display_y: int,
    display_depth: float,
) -> tuple[float, float, float] | None:
    render_window = renderer.GetRenderWindow()
    if render_window is None or not renderer.IsActiveCameraCreated():
        return None
    width, height = render_window.GetSize()
    if width <= 0 or height <= 0:
        return None
    renderer.SetDisplayPoint(
        float(display_x),
        float(display_y),
        float(display_depth),
    )
    renderer.DisplayToWorld()
    world_x, world_y, world_z, world_w = renderer.GetWorldPoint()
    if (
        not all(isfinite(value) for value in (world_x, world_y, world_z, world_w))
        or abs(world_w) <= 1.0e-12
    ):
        return None
    return (
        float(world_x / world_w),
        float(world_y / world_w),
        float(world_z / world_w),
    )


def _distance_label_screen_layout(
    projected_start: tuple[float, float],
    projected_end: tuple[float, float],
) -> _DistanceLabelScreenLayout:
    delta_x = projected_end[0] - projected_start[0]
    delta_y = projected_end[1] - projected_start[1]
    projected_length = hypot(delta_x, delta_y)
    if (
        not isfinite(projected_length)
        or projected_length < DISTANCE_LABEL_MIN_PROJECTED_LENGTH_PIXELS
    ):
        direction_x, direction_y = 1.0, 0.0
    else:
        direction_x = delta_x / projected_length
        direction_y = delta_y / projected_length
        if direction_x < -1.0e-12 or (
            abs(direction_x) <= 1.0e-12 and direction_y < 0.0
        ):
            direction_x = -direction_x
            direction_y = -direction_y

    offset_x = round(-direction_y * DISTANCE_LABEL_OFFSET_PIXELS)
    offset_y = round(direction_x * DISTANCE_LABEL_OFFSET_PIXELS)
    if offset_x == 0 and offset_y == 0:
        offset_y = DISTANCE_LABEL_OFFSET_PIXELS
    return _DistanceLabelScreenLayout(
        degrees(atan2(direction_y, direction_x)),
        (offset_x, offset_y),
    )


def _dash_segments(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> tuple[
    tuple[tuple[float, float, float], tuple[float, float, float]],
    ...,
]:
    length = dist(start, end)
    direction = tuple(
        (end_component - start_component) / length
        for start_component, end_component in zip(start, end, strict=True)
    )
    segments = []
    offset = 0.0
    while length - offset > 1.0e-12:
        segment_end_offset = min(offset + ANNOTATION_DASH_LENGTH, length)
        segments.append(
            (
                _point_along(start, direction, offset),
                _point_along(start, direction, segment_end_offset),
            )
        )
        offset += ANNOTATION_DASH_LENGTH + ANNOTATION_GAP_LENGTH
    if dist(segments[-1][1], end) > 1.0e-12:
        segments.append(
            (
                _point_along(
                    start,
                    direction,
                    max(0.0, length - ANNOTATION_DASH_LENGTH),
                ),
                end,
            )
        )
    return tuple(segments)


def _angle_arc_points(
    annotation: AngleAnnotation,
) -> tuple[tuple[tuple[float, float, float], ...], float]:
    reference_direction = _normalized(
        _subtract(annotation.reference_point, annotation.vertex),
        "angle reference ray",
    )
    target_direction = _normalized(
        _subtract(annotation.target_point, annotation.vertex),
        "angle target ray",
    )
    direction_dot = max(
        -1.0,
        min(1.0, _dot(reference_direction, target_direction)),
    )
    angle_radians = acos(direction_dot)
    if angle_radians <= 1.0e-12:
        return (
            _add(
                annotation.vertex,
                _scale(reference_direction, ANNOTATION_ARC_RADIUS),
            ),
        ), angle_radians
    perpendicular_raw = _subtract(
        target_direction,
        _scale(reference_direction, direction_dot),
    )
    if hypot(*perpendicular_raw) <= 1.0e-12:
        perpendicular = _deterministic_perpendicular(reference_direction)
    else:
        perpendicular = _normalized(
            perpendicular_raw,
            "angle annotation plane",
        )
    segment_count = max(
        2,
        ceil(degrees(angle_radians) / ANNOTATION_ARC_STEP_DEGREES),
    )
    points = tuple(
        _add(
            annotation.vertex,
            _scale(
                _add(
                    _scale(reference_direction, cos(angle_radians * step / segment_count)),
                    _scale(perpendicular, sin(angle_radians * step / segment_count)),
                ),
                ANNOTATION_ARC_RADIUS,
            ),
        )
        for step in range(segment_count + 1)
    )
    return points, angle_radians


def _deterministic_perpendicular(
    direction: tuple[float, float, float],
) -> tuple[float, float, float]:
    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    helper = min(
        enumerate(axes),
        key=lambda item: (abs(_dot(direction, item[1])), item[0]),
    )[1]
    return _normalized(
        _subtract(helper, _scale(direction, _dot(helper, direction))),
        "angle annotation perpendicular",
    )


def bond_strand_segments(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    display_order: int,
) -> tuple[
    tuple[tuple[float, float, float], tuple[float, float, float]], ...
]:
    """Return camera-independent parallel strands for one logical bond."""

    first = _validated_point(start, "bond strand start")
    second = _validated_point(end, "bond strand end")
    if isinstance(display_order, bool) or not isinstance(display_order, int):
        raise TypeError("bond display order must be an integer")
    if display_order not in {1, 2, 3}:
        raise ValueError("bond display order must be 1, 2, or 3")
    direction = _normalized(_subtract(second, first), "bond strand axis")
    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    helper = min(
        enumerate(axes),
        key=lambda item: (abs(_dot(direction, item[1])), item[0]),
    )[1]
    perpendicular = _normalized(
        _cross(direction, helper),
        "bond strand perpendicular",
    )
    offset_units = {
        1: (0.0,),
        2: (-0.5, 0.5),
        3: (-1.0, 0.0, 1.0),
    }[display_order]
    return tuple(
        (
            _add(
                first,
                _scale(
                    perpendicular,
                    offset * MULTIPLE_BOND_STRAND_SEPARATION,
                ),
            ),
            _add(
                second,
                _scale(
                    perpendicular,
                    offset * MULTIPLE_BOND_STRAND_SEPARATION,
                ),
            ),
        )
        for offset in offset_units
    )


def _validated_point(
    point: tuple[float, float, float],
    name: str,
) -> tuple[float, float, float]:
    components = tuple(point)
    if len(components) != 3:
        raise ValueError(f"{name} must contain three components")
    numeric_components = []
    for component in components:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise TypeError(f"{name} components must be numeric")
        numeric_component = float(component)
        if not isfinite(numeric_component):
            raise ValueError(f"{name} components must be finite")
        numeric_components.append(numeric_component)
    return (
        numeric_components[0],
        numeric_components[1],
        numeric_components[2],
    )


def _normalized(
    vector: tuple[float, float, float],
    name: str,
) -> tuple[float, float, float]:
    length = hypot(*vector)
    if not isfinite(length) or length <= 1.0e-12:
        raise VisualizationError(f"{name} must have non-zero finite length")
    return _scale(vector, 1.0 / length)


def _point_along(
    start: tuple[float, float, float],
    direction: tuple[float, float, float],
    distance_value: float,
) -> tuple[float, float, float]:
    return _add(start, _scale(direction, distance_value))


def _midpoint(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        (first_component + second_component) / 2.0
        for first_component, second_component in zip(first, second, strict=True)
    )


def _add(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        first_component + second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _subtract(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        first_component - second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _scale(
    vector: tuple[float, float, float],
    factor: float,
) -> tuple[float, float, float]:
    return tuple(component * factor for component in vector)


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(
        first_component * second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _signed_torsion_angle_degrees(
    reference: tuple[float, float, float],
    radial: tuple[float, float, float],
    axis: tuple[float, float, float],
) -> float:
    normalized_reference = _normalized(reference, "torsion angle reference")
    normalized_radial = _normalized(radial, "torsion pointer radial")
    normalized_axis = _normalized(axis, "torsion angle axis")
    return degrees(
        atan2(
            _dot(
                normalized_axis,
                _cross(normalized_reference, normalized_radial),
            ),
            _dot(normalized_reference, normalized_radial),
        )
    )


def _rotate_vector_about_axis(
    vector: tuple[float, float, float],
    axis: tuple[float, float, float],
    angle_radians: float,
) -> tuple[float, float, float]:
    cosine = cos(angle_radians)
    sine = sin(angle_radians)
    return _add(
        _add(
            _scale(vector, cosine),
            _scale(_cross(axis, vector), sine),
        ),
        _scale(axis, _dot(axis, vector) * (1.0 - cosine)),
    )


def _validated_highlight_indices(
    indices: Iterable[int],
    atom_count: int,
    name: str,
) -> tuple[int, ...]:
    validated_indices: set[int] = set()
    for atom_index in indices:
        if isinstance(atom_index, bool) or not isinstance(atom_index, int):
            raise TypeError(f"{name} highlight indexes must be integers")
        if atom_index < 0:
            raise ValueError(f"{name} highlight indexes must be non-negative")
        if atom_index >= atom_count:
            raise VisualizationError(
                f"{name} highlight index is outside the displayed molecule: "
                f"{atom_index} >= {atom_count}"
            )
        validated_indices.add(atom_index)
    return tuple(sorted(validated_indices))


def _validated_preview_hidden_indices(
    indices: Iterable[int],
    atom_count: int,
) -> tuple[int, ...]:
    validated_indices: set[int] = set()
    for atom_index in indices:
        if isinstance(atom_index, bool) or not isinstance(atom_index, int):
            raise TypeError("preview-hidden atom indexes must be integers")
        if atom_index < 0:
            raise ValueError(
                "preview-hidden atom indexes must be non-negative"
            )
        if atom_index >= atom_count:
            raise VisualizationError(
                "preview-hidden atom index is outside the displayed molecule: "
                f"{atom_index} >= {atom_count}"
            )
        validated_indices.add(atom_index)
    return tuple(sorted(validated_indices))


def _validated_measurement_pick_indices(
    indices: Iterable[int],
    atom_count: int,
) -> tuple[int, ...]:
    validated_indices = []
    seen_indices = set()
    for atom_index in indices:
        if isinstance(atom_index, bool) or not isinstance(atom_index, int):
            raise TypeError("measurement pick indexes must be integers")
        if atom_index < 0:
            raise ValueError("measurement pick indexes must be non-negative")
        if atom_index >= atom_count:
            raise VisualizationError(
                "measurement pick index is outside the displayed molecule: "
                f"{atom_index} >= {atom_count}"
            )
        if atom_index in seen_indices:
            raise VisualizationError("measurement pick indexes must be distinct")
        validated_indices.append(atom_index)
        seen_indices.add(atom_index)
        if len(validated_indices) > MAX_MEASUREMENT_PICK_COUNT:
            raise VisualizationError(
                "at most three temporary measurement picks are supported"
            )
    return tuple(validated_indices)


def _visual_radius_for(
    element: str,
    covalent_radii: Mapping[str, float],
) -> float:
    try:
        radius = covalent_radii[element]
    except KeyError as error:
        raise VisualizationError(
            f"cannot render element {element!r}: no covalent radius is configured"
        ) from error

    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        raise VisualizationError(
            f"cannot render element {element!r}: covalent radius must be numeric"
        )
    numeric_radius = float(radius)
    if not isfinite(numeric_radius) or numeric_radius <= 0.0:
        raise VisualizationError(
            f"cannot render element {element!r}: "
            "covalent radius must be finite and greater than zero"
        )
    return numeric_radius * ATOM_RADIUS_SCALE
