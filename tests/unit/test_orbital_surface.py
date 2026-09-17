from dataclasses import replace
import unittest

from vtkmodules.vtkRenderingCore import vtkRenderer
from vtkmodules.vtkCommonTransforms import vtkTransform

from moltage.structure.cube import CubeScalarField
from moltage.visualization.orbital_surface import (
    DEFAULT_ORBITAL_AMBIENT,
    DEFAULT_ORBITAL_LIGHT_INTENSITY,
    DEFAULT_ORBITAL_SPECULAR,
    DEFAULT_ORBITAL_SHININESS,
    DEFAULT_NEGATIVE_LOBE_COLOR,
    DEFAULT_POSITIVE_LOBE_COLOR,
    MAXIMUM_ORBITAL_SURFACE_POLYGONS,
    SEMI_TRANSPARENT_ORBITAL_OPACITY,
    OrbitalSurfaceLayer,
    OrbitalSurfacePreferences,
    OrbitalSurfaceStyle,
)


def _signed_field() -> CubeScalarField:
    values = tuple(
        float(i - 1)
        for i in range(3)
        for _j in range(3)
        for _k in range(3)
    )
    return CubeScalarField(
        (3, 3, 3),
        (1.0, 2.0, 3.0),
        ((0.5, 0.0, 0.0), (0.1, 0.4, 0.0), (0.0, 0.1, 0.3)),
        values,
    )


class OrbitalSurfaceLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = OrbitalSurfaceLayer()
        self.preferences = OrbitalSurfacePreferences(isovalue=0.5)
        self.layer.set_field(_signed_field(), self.preferences)

    def test_builds_two_colored_non_pickable_cached_surfaces(self) -> None:
        self.assertGreater(self.layer.polygon_count, 0)
        self.assertEqual(self.layer.extraction_count, 1)
        self.assertTrue(self.layer.actor.GetVisibility())
        self.assertFalse(self.layer.actor.GetPickable())
        self.assertEqual(
            self.layer.actor.GetProperty().GetInterpolationAsString(), "Phong"
        )
        self.assertEqual(
            self.layer.actor.GetProperty().GetSpecular(), DEFAULT_ORBITAL_SPECULAR
        )
        self.assertEqual(
            self.layer.actor.GetProperty().GetSpecularPower(),
            DEFAULT_ORBITAL_SHININESS,
        )
        self.assertEqual(
            self.layer.actor.GetProperty().GetSpecularColor(), (1.0, 1.0, 1.0)
        )
        self.assertEqual(
            self.layer.actor.GetProperty().GetAmbient(),
            DEFAULT_ORBITAL_AMBIENT,
        )
        self.assertEqual(
            self.layer.actor.GetProperty().GetDiffuse(),
            1.0 - DEFAULT_ORBITAL_AMBIENT,
        )
        negative = self.layer._lookup_table.GetTableValue(0)
        positive = self.layer._lookup_table.GetTableValue(1)
        self.assertEqual(
            tuple(round(component * 255) for component in negative[:3]),
            tuple(
                round(component * DEFAULT_ORBITAL_LIGHT_INTENSITY)
                for component in DEFAULT_NEGATIVE_LOBE_COLOR
            ),
        )
        self.assertEqual(
            tuple(round(component * 255) for component in positive[:3]),
            tuple(
                round(component * DEFAULT_ORBITAL_LIGHT_INTENSITY)
                for component in DEFAULT_POSITIVE_LOBE_COLOR
            ),
        )

    def test_color_style_and_camera_changes_do_not_reextract_mesh(self) -> None:
        changed = replace(
            self.preferences,
            positive_color=(1, 2, 3),
            negative_color=(4, 5, 6),
            style=OrbitalSurfaceStyle.SEMI_TRANSPARENT,
            ambient=0.40,
            light_intensity=0.65,
            specular=0.55,
            shininess=64.0,
        )
        self.layer.set_preferences(changed)
        renderer = vtkRenderer()
        renderer.AddActor(self.layer.actor)
        renderer.ResetCamera()

        self.assertEqual(self.layer.extraction_count, 1)
        self.assertEqual(
            self.layer.actor.GetProperty().GetOpacity(),
            SEMI_TRANSPARENT_ORBITAL_OPACITY,
        )
        self.assertTrue(self.layer.actor.GetForceTranslucent())
        self.assertEqual(self.layer.actor.GetProperty().GetAmbient(), 0.40)
        self.assertEqual(self.layer.actor.GetProperty().GetDiffuse(), 0.60)
        self.assertEqual(self.layer.actor.GetProperty().GetSpecular(), 0.55)
        self.assertEqual(self.layer.actor.GetProperty().GetSpecularPower(), 64.0)
        self.assertEqual(
            tuple(
                round(component * 255)
                for component in self.layer._lookup_table.GetTableValue(1)[:3]
            ),
            (1, 1, 2),
        )

        self.layer.set_preferences(
            replace(changed, style=OrbitalSurfaceStyle.OPAQUE, specular=0.0)
        )
        self.assertEqual(self.layer.actor.GetProperty().GetSpecular(), 0.0)
        self.assertTrue(self.layer.actor.GetForceOpaque())
        self.assertEqual(self.layer.extraction_count, 1)

    def test_material_parameters_reject_invalid_values(self) -> None:
        for name, values in (
            ("specular", (-0.01, 1.01, float("nan"), True)),
            ("shininess", (0.0, 129.0, float("inf"), True)),
        ):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(
                    ValueError
                ):
                    OrbitalSurfacePreferences(**{name: value})

    def test_ambient_rejects_values_outside_the_vtk_fraction_range(self) -> None:
        for invalid in (-0.01, 1.01, float("nan"), True):
            with self.subTest(invalid=invalid), self.assertRaises(
                (TypeError, ValueError)
            ):
                OrbitalSurfacePreferences(ambient=invalid)

    def test_light_intensity_rejects_values_outside_the_vtk_fraction_range(self) -> None:
        for invalid in (-0.01, 1.01, float("nan"), True):
            with self.subTest(invalid=invalid), self.assertRaises(
                (TypeError, ValueError)
            ):
                OrbitalSurfacePreferences(light_intensity=invalid)

    def test_isovalue_change_reextracts_while_zero_hides_surface(self) -> None:
        self.layer.set_preferences(replace(self.preferences, isovalue=0.25))
        self.assertEqual(self.layer.extraction_count, 2)
        self.assertTrue(self.layer.actor.GetVisibility())

        self.layer.set_preferences(replace(self.preferences, isovalue=0.0))
        self.assertEqual(self.layer.extraction_count, 2)
        self.assertEqual(self.layer.polygon_count, 0)
        self.assertFalse(self.layer.actor.GetVisibility())

    def test_grid_affine_transform_preserves_origin_and_all_axis_vectors(self) -> None:
        matrix = self.layer.actor.GetUserMatrix()
        field = _signed_field()
        self.assertEqual(
            self.layer._image.GetDimensions(),
            tuple(reversed(field.dimensions)),
        )
        for row, component in enumerate(field.origin_angstrom):
            self.assertEqual(matrix.GetElement(row, 3), component)
        for column, vector in enumerate(reversed(field.axis_vectors_angstrom)):
            for row, component in enumerate(vector):
                self.assertEqual(matrix.GetElement(row, column), component)

        # A Cube (i, j, k) sample is attached to VTK (k, j, i), so applying
        # the reversed affine still gives origin + i*X + j*Y + k*Z.
        i, j, k = 2, 1, 2
        vtk_point = (k, j, i, 1.0)
        transformed = tuple(
            sum(matrix.GetElement(row, column) * vtk_point[column]
                for column in range(4))
            for row in range(3)
        )
        expected = tuple(
            field.origin_angstrom[row]
            + i * field.axis_vectors_angstrom[0][row]
            + j * field.axis_vectors_angstrom[1][row]
            + k * field.axis_vectors_angstrom[2][row]
            for row in range(3)
        )
        self.assertEqual(transformed, expected)

    def test_compact_cube_buffer_is_attached_in_file_order(self) -> None:
        field = _signed_field()
        layer = OrbitalSurfaceLayer()
        layer.set_field(field, self.preferences)
        scalars = layer._image.GetPointData().GetScalars()

        self.assertEqual(scalars.GetNumberOfValues(), len(field.values))
        self.assertEqual(
            tuple(scalars.GetValue(index) for index in range(len(field.values))),
            tuple(field.values),
        )

    def test_world_winding_matches_normals_for_both_grid_handednesses(self) -> None:
        for direction in (1.0, -1.0):
            with self.subTest(direction=direction):
                field = replace(
                    _signed_field(),
                    axis_vectors_angstrom=(
                        (0.5 * direction, 0.0, 0.0),
                        (0.1, 0.4, 0.0),
                        (0.0, 0.1, 0.3),
                    ),
                )
                layer = OrbitalSurfaceLayer()
                layer.set_field(field, self.preferences)
                layer._mapper.Update()
                mesh = layer._mapper.GetInput()
                raw = layer._flying_edges.GetOutput()
                self.assertEqual(mesh.GetNumberOfPolys(), raw.GetNumberOfPolys())
                for index in range(mesh.GetNumberOfPoints()):
                    self.assertEqual(mesh.GetPoint(index), raw.GetPoint(index))
                    self.assertEqual(
                        mesh.GetPointData().GetScalars().GetTuple1(index),
                        raw.GetPointData().GetScalars().GetTuple1(index),
                    )
                    self.assertEqual(
                        mesh.GetPointData().GetNormals().GetTuple3(index),
                        raw.GetPointData().GetNormals().GetTuple3(index),
                    )
                transform = vtkTransform()
                transform.SetMatrix(layer.actor.GetUserMatrix())
                for index in range(mesh.GetNumberOfCells()):
                    cell = mesh.GetCell(index)
                    points = [
                        transform.TransformPoint(mesh.GetPoint(cell.GetPointId(i)))
                        for i in range(3)
                    ]
                    u = tuple(b - a for a, b in zip(points[0], points[1]))
                    v = tuple(b - a for a, b in zip(points[0], points[2]))
                    face_normal = (
                        u[1] * v[2] - u[2] * v[1],
                        u[2] * v[0] - u[0] * v[2],
                        u[0] * v[1] - u[1] * v[0],
                    )
                    normal = transform.TransformNormal(
                        mesh.GetPointData().GetNormals().GetTuple3(cell.GetPointId(0))
                    )
                    self.assertGreater(sum(a * b for a, b in zip(face_normal, normal)), 0)

    def test_bounded_synthetic_dense_grid_is_reduced_to_render_budget(self) -> None:
        size = 42
        values = tuple(
            1.0 if (i + j + k) % 2 else -1.0
            for i in range(size)
            for j in range(size)
            for k in range(size)
        )
        field = CubeScalarField(
            (size, size, size),
            (0.0, 0.0, 0.0),
            ((0.1, 0.0, 0.0), (0.0, 0.1, 0.0), (0.0, 0.0, 0.1)),
            values,
        )
        layer = OrbitalSurfaceLayer()
        layer.set_field(field, OrbitalSurfacePreferences(isovalue=0.5))

        self.assertGreater(
            layer._flying_edges.GetOutput().GetNumberOfPolys(),
            MAXIMUM_ORBITAL_SURFACE_POLYGONS,
        )
        self.assertLessEqual(
            layer.polygon_count,
            MAXIMUM_ORBITAL_SURFACE_POLYGONS,
        )
        self.assertIs(
            layer._winding.GetInputConnection(0, 0).GetProducer(),
            layer._decimator,
        )


if __name__ == "__main__":
    unittest.main()
