"""Reusable VTK isosurface layer for one signed Cube scalar field."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkLookupTable
from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkFiltersCore import vtkDecimatePro, vtkFlyingEdges3D, vtkReverseSense
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from moltage.structure.cube import CubeScalarField
from moltage.visualization.view_preferences import RgbColor


DEFAULT_ORBITAL_ISOVALUE = 0.02
ORBITAL_ISOVALUE_STEP = 0.005
DEFAULT_ORBITAL_AMBIENT = 0.90
MINIMUM_ORBITAL_AMBIENT = 0.0
MAXIMUM_ORBITAL_AMBIENT = 1.0
ORBITAL_AMBIENT_STEP = 0.05
DEFAULT_ORBITAL_LIGHT_INTENSITY = 0.82
MINIMUM_ORBITAL_LIGHT_INTENSITY = 0.0
MAXIMUM_ORBITAL_LIGHT_INTENSITY = 1.0
ORBITAL_LIGHT_INTENSITY_STEP = 0.05
DEFAULT_ORBITAL_SPECULAR = 0.30
MINIMUM_ORBITAL_SPECULAR = 0.0
MAXIMUM_ORBITAL_SPECULAR = 1.0
ORBITAL_SPECULAR_STEP = 0.05
DEFAULT_ORBITAL_SHININESS = 32.0
MINIMUM_ORBITAL_SHININESS = 1.0
MAXIMUM_ORBITAL_SHININESS = 128.0
ORBITAL_SHININESS_STEP = 1.0
RECOMMENDED_ORBITAL_AMBIENT = 0.55
RECOMMENDED_ORBITAL_LIGHT_INTENSITY = 0.95
SEMI_TRANSPARENT_ORBITAL_OPACITY = 0.50
DEFAULT_POSITIVE_LOBE_COLOR: RgbColor = (220, 48, 48)
DEFAULT_NEGATIVE_LOBE_COLOR: RgbColor = (45, 95, 220)
MAXIMUM_ORBITAL_SURFACE_POLYGONS = 250_000


class OrbitalSurfaceStyle(StrEnum):
    """The two intentionally small MVP surface presentation choices."""

    OPAQUE = "Opaque"
    SEMI_TRANSPARENT = "Semi-transparent"


class OrbitalSurfaceResolution(StrEnum):
    """View-only scalar-grid resolution used before isosurface extraction."""

    FULL = "Full"
    MEDIUM = "Medium"
    LOW = "Low"

    @property
    def maximum_axis_points(self) -> int | None:
        return {
            OrbitalSurfaceResolution.FULL: None,
            OrbitalSurfaceResolution.MEDIUM: 120,
            OrbitalSurfaceResolution.LOW: 80,
        }[self]


@dataclass(frozen=True, slots=True)
class OrbitalSurfacePreferences:
    """Session-local display state for one Cube workspace."""

    isovalue: float = DEFAULT_ORBITAL_ISOVALUE
    positive_color: RgbColor = DEFAULT_POSITIVE_LOBE_COLOR
    negative_color: RgbColor = DEFAULT_NEGATIVE_LOBE_COLOR
    style: OrbitalSurfaceStyle = OrbitalSurfaceStyle.OPAQUE
    resolution: OrbitalSurfaceResolution = OrbitalSurfaceResolution.FULL
    ambient: float = DEFAULT_ORBITAL_AMBIENT
    light_intensity: float = DEFAULT_ORBITAL_LIGHT_INTENSITY
    specular: float = DEFAULT_ORBITAL_SPECULAR
    shininess: float = DEFAULT_ORBITAL_SHININESS

    def __post_init__(self) -> None:
        isovalue = self.isovalue
        if (
            isinstance(isovalue, bool)
            or not isinstance(isovalue, (int, float))
            or not isfinite(float(isovalue))
            or float(isovalue) < 0.0
        ):
            raise ValueError("orbital isovalue must be finite and non-negative")
        if not isinstance(self.style, OrbitalSurfaceStyle):
            raise TypeError("orbital surface style is invalid")
        if not isinstance(self.resolution, OrbitalSurfaceResolution):
            raise TypeError("orbital surface resolution is invalid")
        ambient = self.ambient
        if (
            isinstance(ambient, bool)
            or not isinstance(ambient, (int, float))
            or not isfinite(float(ambient))
            or not (
                MINIMUM_ORBITAL_AMBIENT
                <= float(ambient)
                <= MAXIMUM_ORBITAL_AMBIENT
            )
        ):
            raise ValueError("orbital ambient lighting must be between 0 and 1")
        light_intensity = self.light_intensity
        if (
            isinstance(light_intensity, bool)
            or not isinstance(light_intensity, (int, float))
            or not isfinite(float(light_intensity))
            or not (
                MINIMUM_ORBITAL_LIGHT_INTENSITY
                <= float(light_intensity)
                <= MAXIMUM_ORBITAL_LIGHT_INTENSITY
            )
        ):
            raise ValueError("orbital light intensity must be between 0 and 1")
        object.__setattr__(self, "isovalue", float(isovalue))
        object.__setattr__(self, "ambient", float(ambient))
        object.__setattr__(self, "light_intensity", float(light_intensity))
        for name, minimum, maximum in (
            ("specular", MINIMUM_ORBITAL_SPECULAR, MAXIMUM_ORBITAL_SPECULAR),
            ("shininess", MINIMUM_ORBITAL_SHININESS, MAXIMUM_ORBITAL_SHININESS),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or not minimum <= float(value) <= maximum
            ):
                raise ValueError(
                    f"orbital {name} must be between {minimum:g} and {maximum:g}"
                )
            object.__setattr__(self, name, float(value))
        object.__setattr__(
            self,
            "positive_color",
            _validated_rgb(self.positive_color),
        )
        object.__setattr__(
            self,
            "negative_color",
            _validated_rgb(self.negative_color),
        )

    @property
    def opacity(self) -> float:
        if self.style is OrbitalSurfaceStyle.OPAQUE:
            return 1.0
        return SEMI_TRANSPARENT_ORBITAL_OPACITY


def initial_orbital_surface_preferences(
    scalar_field: CubeScalarField,
) -> OrbitalSurfacePreferences:
    """Use the accepted default, bounded only for a smaller/zero data range."""

    if not isinstance(scalar_field, CubeScalarField):
        raise TypeError("initial orbital preferences require a Cube scalar field")
    return OrbitalSurfacePreferences(
        isovalue=min(
            DEFAULT_ORBITAL_ISOVALUE,
            scalar_field.maximum_absolute_value,
        )
    )


class OrbitalSurfaceLayer:
    """Cache a signed isosurface mesh so camera motion never re-extracts it."""

    def __init__(self) -> None:
        self._field: CubeScalarField | None = None
        self._preferences = OrbitalSurfacePreferences()
        self._image = vtkImageData()
        self._flying_edges = vtkFlyingEdges3D()
        self._flying_edges.SetInputData(self._image)
        self._flying_edges.ComputeNormalsOn()
        self._flying_edges.ComputeScalarsOn()
        self._flying_edges.ComputeGradientsOff()
        self._decimator = vtkDecimatePro()
        self._decimator.SetInputConnection(self._flying_edges.GetOutputPort())
        self._decimator.PreserveTopologyOff()
        self._winding = vtkReverseSense()
        self._winding.SetInputConnection(self._flying_edges.GetOutputPort())
        self._winding.ReverseNormalsOff()

        self._lookup_table = vtkLookupTable()
        self._lookup_table.SetNumberOfTableValues(2)
        self._lookup_table.Build()
        self._mapper = vtkPolyDataMapper()
        self._mapper.SetInputConnection(self._winding.GetOutputPort())
        self._mapper.SetLookupTable(self._lookup_table)
        self._mapper.SetScalarModeToUsePointData()
        self._mapper.SetColorModeToMapScalars()
        self._mapper.UseLookupTableScalarRangeOn()
        self._mapper.ScalarVisibilityOn()

        self.actor = vtkActor()
        self.actor.SetMapper(self._mapper)
        self.actor.PickableOff()
        self.actor.GetProperty().SetInterpolationToPhong()
        self.actor.GetProperty().SetSpecularColor(1.0, 1.0, 1.0)
        self.actor.SetVisibility(False)
        self._polygon_count = 0
        self._extraction_count = 0
        self._update_appearance()

    @property
    def field(self) -> CubeScalarField | None:
        return self._field

    @property
    def preferences(self) -> OrbitalSurfacePreferences:
        return self._preferences

    @property
    def polygon_count(self) -> int:
        return self._polygon_count

    @property
    def extraction_count(self) -> int:
        """Expose deterministic extraction activity for focused regressions."""

        return self._extraction_count

    def set_field(
        self,
        field: CubeScalarField | None,
        preferences: OrbitalSurfacePreferences | None = None,
    ) -> None:
        if field is not None and not isinstance(field, CubeScalarField):
            raise TypeError("orbital surface field must be a CubeScalarField")
        if preferences is not None and not isinstance(
            preferences,
            OrbitalSurfacePreferences,
        ):
            raise TypeError(
                "orbital surface preferences must be OrbitalSurfacePreferences"
            )
        if preferences is not None:
            self._preferences = preferences
        if field is None:
            # Release VTK's borrowed scalar pointer before dropping the Python
            # array that owns that memory.
            self._image.Initialize()
            self._field = None
            self._polygon_count = 0
            self.actor.SetVisibility(False)
            return
        self._populate_image(field)
        self._field = field
        self._extract_surface()

    def set_preferences(self, preferences: OrbitalSurfacePreferences) -> None:
        if not isinstance(preferences, OrbitalSurfacePreferences):
            raise TypeError(
                "orbital surface preferences must be OrbitalSurfacePreferences"
            )
        isovalue_changed = preferences.isovalue != self._preferences.isovalue
        self._preferences = preferences
        self._update_appearance()
        if self._field is not None and isovalue_changed:
            self._extract_surface()

    def _populate_image(self, field: CubeScalarField) -> None:
        nx, ny, nz = field.dimensions
        self._image.Initialize()
        # Cube stores Z fastest while vtkImageData stores its first dimension
        # fastest. Reverse both dimensions and affine columns so the compact
        # file-order buffer can be attached directly without a pointwise copy.
        self._image.SetDimensions(nz, ny, nx)
        self._image.SetOrigin(0.0, 0.0, 0.0)
        self._image.SetSpacing(1.0, 1.0, 1.0)
        scalars = vtkDoubleArray()
        scalars.SetName("orbital_value")
        scalars.SetNumberOfComponents(1)
        scalars.SetArray(field.values, nx * ny * nz, 1)
        self._image.GetPointData().SetScalars(scalars)
        self._image.Modified()

        matrix = vtkMatrix4x4()
        matrix.Identity()
        for column, vector in enumerate(reversed(field.axis_vectors_angstrom)):
            for row, component in enumerate(vector):
                matrix.SetElement(row, column, component)
        for row, component in enumerate(field.origin_angstrom):
            matrix.SetElement(row, 3, component)
        self.actor.SetUserMatrix(matrix)
        # Reversing the Cube axes can reflect the grid-to-world transform.
        # Match triangle winding to transformed normals so VTK's two-sided
        # lighting does not shade the visible surface as if it faced away.
        # Only triangle order changes; points, normals and scalar values do not.
        self._winding.SetReverseCells(matrix.Determinant() < 0.0)

    def _extract_surface(self) -> None:
        field = self._field
        if field is None:
            return
        isovalue = self._preferences.isovalue
        self._update_appearance()
        if isovalue == 0.0:
            self._polygon_count = 0
            self.actor.SetVisibility(False)
            return

        self._flying_edges.SetNumberOfContours(2)
        self._flying_edges.SetValue(0, -isovalue)
        self._flying_edges.SetValue(1, isovalue)
        self._flying_edges.Update()
        self._extraction_count += 1
        raw_polygon_count = self._flying_edges.GetOutput().GetNumberOfPolys()
        if raw_polygon_count > MAXIMUM_ORBITAL_SURFACE_POLYGONS:
            self._decimator.SetTargetReduction(
                1.0
                - MAXIMUM_ORBITAL_SURFACE_POLYGONS / raw_polygon_count
            )
            self._decimator.Update()
            self._winding.SetInputConnection(self._decimator.GetOutputPort())
            self._polygon_count = self._decimator.GetOutput().GetNumberOfPolys()
        else:
            self._winding.SetInputConnection(self._flying_edges.GetOutputPort())
            self._polygon_count = raw_polygon_count
        self.actor.SetVisibility(self._polygon_count > 0)

    def _update_appearance(self) -> None:
        self.actor.GetProperty().SetAmbient(self._preferences.ambient)
        self.actor.GetProperty().SetDiffuse(1.0 - self._preferences.ambient)
        self.actor.GetProperty().SetSpecular(self._preferences.specular)
        self.actor.GetProperty().SetSpecularPower(self._preferences.shininess)
        isovalue = max(self._preferences.isovalue, 1.0e-300)
        self._lookup_table.SetTableRange(-isovalue, isovalue)
        self._lookup_table.SetTableValue(
            0,
            *(
                component / 255.0 * self._preferences.light_intensity
                for component in self._preferences.negative_color
            ),
            1.0,
        )
        self._lookup_table.SetTableValue(
            1,
            *(
                component / 255.0 * self._preferences.light_intensity
                for component in self._preferences.positive_color
            ),
            1.0,
        )
        self._lookup_table.Modified()
        self.actor.GetProperty().SetOpacity(self._preferences.opacity)
        if self._preferences.style is OrbitalSurfaceStyle.OPAQUE:
            self.actor.ForceTranslucentOff()
            self.actor.ForceOpaqueOn()
        else:
            self.actor.ForceOpaqueOff()
            self.actor.ForceTranslucentOn()


def _validated_rgb(value: object) -> RgbColor:
    try:
        color = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError("orbital lobe color must contain three RGB integers") from error
    if len(color) != 3:
        raise ValueError("orbital lobe color must contain three RGB integers")
    for component in color:
        if isinstance(component, bool) or not isinstance(component, int):
            raise TypeError("orbital lobe color components must be integers")
        if not 0 <= component <= 255:
            raise ValueError("orbital lobe color components must be from 0 to 255")
    return color  # type: ignore[return-value]
