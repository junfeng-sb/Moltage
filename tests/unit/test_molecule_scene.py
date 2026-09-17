import math
import unittest

from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow

from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.junction import AuPlacementProposal
from moltage.domain.structure import Atom, MolecularStructure
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.visualization.molecule_scene import (
    ANNOTATION_ARC_RADIUS,
    ATOM_RADIUS_SCALE,
    AngleAnnotation,
    DistanceAnnotation,
    ELEMENT_COLORS_RGB,
    HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
    LATTICE_GUIDE_GRID_CENTER_ALPHA,
    LATTICE_GUIDE_PLANE_CENTER_ALPHA,
    LEFT_SURFACE_COLOR_RGB,
    MEASUREMENT_HIGHLIGHT_COLOR_RGB,
    MEASUREMENT_HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
    MINIMUM_ATOM_PICK_RADIUS_PIXELS,
    MeasurementOverlayAnnotation,
    MoleculeScene,
    PREVIEW_ATOM_OPACITY,
    LatticeExtensionPreviewGuide,
    PreviewAtom,
    PreviewPickTarget,
    PRIMARY_HIGHLIGHT_COLOR_RGB,
    RIGHT_SURFACE_COLOR_RGB,
    SECONDARY_HIGHLIGHT_COLOR_RGB,
    TorsionGizmo,
    _add,
    _dash_segments,
    _display_point,
    _distance_label_screen_layout,
    _normalized,
    _rotate_vector_about_axis,
    _subtract,
    _visual_radius_for,
)
from moltage.visualization.view_preferences import (
    AtomHighlightColors,
    ViewPreferences,
)


def displayed_colors(scene: MoleculeScene) -> tuple[tuple[int, int, int], ...]:
    colors = scene._atom_polydata.GetPointData().GetArray("display_color")
    return tuple(
        tuple(int(component) for component in colors.GetTuple3(index))
        for index in range(colors.GetNumberOfTuples())
    )


class MoleculeSceneHighlightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = MolecularStructure(
            atoms=(
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "N", 1.0, 0.0, 0.0),
                Atom(2, "S", 2.0, 0.0, 0.0),
                Atom(3, "Au", 3.0, 0.0, 0.0),
            )
        )
        self.connectivity = Connectivity(
            atom_count=4,
            bonds=(Bond(0, 1, 1.0), Bond(1, 2, 1.0), Bond(2, 3, 1.0)),
        )
        self.radii = {"C": 0.76, "N": 0.71, "S": 1.05, "Au": 1.36}
        self.renderer = vtkRenderer()
        self.scene = MoleculeScene(self.renderer)
        self.scene.set_molecule(
            self.structure,
            self.connectivity,
            self.radii,
        )

    def test_packaged_covalent_radius_drives_display_scale(self) -> None:
        radii = load_default_covalent_radii()

        self.assertAlmostEqual(
            _visual_radius_for("Au", radii),
            1.24 * ATOM_RADIUS_SCALE,
        )

    def test_highlights_use_shells_without_changing_base_atom_colors(self) -> None:
        original_points = tuple(
            self.scene._atom_polydata.GetPoint(index)
            for index in range(len(self.structure))
        )
        original_bonds = self.connectivity.bonds
        original_colors = displayed_colors(self.scene)

        self.scene.set_highlighted_atom_indices((2,), (3,))

        self.assertEqual(displayed_colors(self.scene), original_colors)
        self.assertEqual(
            original_colors,
            tuple(ELEMENT_COLORS_RGB[atom.element] for atom in self.structure),
        )
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 2)
        self.assertEqual(
            tuple(
                self.scene._highlight_polydata.GetPoint(index)
                for index in range(2)
            ),
            (original_points[2], original_points[3]),
        )
        shell_colors = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_color"
        )
        self.assertEqual(
            tuple(int(value) for value in shell_colors.GetTuple3(0)),
            PRIMARY_HIGHLIGHT_COLOR_RGB,
        )
        self.assertEqual(
            tuple(int(value) for value in shell_colors.GetTuple3(1)),
            SECONDARY_HIGHLIGHT_COLOR_RGB,
        )
        shell_radii = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_radius"
        )
        self.assertAlmostEqual(
            shell_radii.GetValue(0),
            self.scene._visual_radii[2] * HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
        )
        self.assertAlmostEqual(
            shell_radii.GetValue(1),
            self.scene._visual_radii[3] * HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
        )
        self.assertEqual(
            tuple(
                self.scene._atom_polydata.GetPoint(index)
                for index in range(len(self.structure))
            ),
            original_points,
        )
        self.assertEqual(self.connectivity.bonds, original_bonds)

        self.scene.set_highlighted_atom_indices((), ())

        self.assertEqual(displayed_colors(self.scene), original_colors)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

    def test_actor_count_does_not_scale_with_highlight_count(self) -> None:
        self.scene.set_highlighted_atom_indices((0, 1, 2), (3,))

        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 4)
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

    def test_hover_uses_an_open_ring_and_label_without_replacing_shells(self) -> None:
        self.scene.set_highlighted_atom_indices((1,), ())

        self.scene.set_hover_highlight(atom_index=1)

        self.assertEqual(self.scene.hover_highlight, (1, None))
        shell_colors = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_color"
        )
        self.assertEqual(
            tuple(map(int, shell_colors.GetTuple3(0))),
            PRIMARY_HIGHLIGHT_COLOR_RGB,
        )
        self.assertEqual(
            self.scene._atom_highlight_ring_polydata.GetNumberOfLines(),
            1,
        )
        ring_colors = (
            self.scene._atom_highlight_ring_polydata.GetCellData().GetScalars()
        )
        self.assertEqual(
            tuple(map(int, ring_colors.GetTuple3(0))),
            self.scene.atom_highlight_colors.hover,
        )
        hover_labels = (
            self.scene._hover_atom_label_polydata.GetPointData().GetAbstractArray(
                "atom_index_label"
            )
        )
        self.assertEqual(hover_labels.GetValue(0), "2")

        self.scene.set_hover_highlight(bond_edge=(0, 1))

        self.assertEqual(self.scene.hover_highlight, (None, (0, 1)))
        self.assertEqual(
            self.scene._atom_highlight_ring_polydata.GetNumberOfLines(),
            0,
        )
        self.assertGreater(self.scene._selected_bond_polydata.GetNumberOfLines(), 0)

        self.scene.set_hover_highlight()

        self.assertEqual(self.scene.hover_highlight, (None, None))
        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 0)
        restored = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_color"
        )
        self.assertEqual(
            tuple(map(int, restored.GetTuple3(0))),
            PRIMARY_HIGHLIGHT_COLOR_RGB,
        )

    def test_fragment_groups_use_batched_theme_colored_rings_and_labels(self) -> None:
        colors = AtomHighlightColors(
            hover=(255, 220, 0),
            primary_group=(255, 0, 180),
            secondary_group=(0, 220, 255),
        )
        original_colors = displayed_colors(self.scene)
        self.scene.set_atom_highlight_colors(colors)

        self.scene.set_grouped_atom_indices((0, 2), (1, 3))

        self.assertEqual(self.scene.grouped_atom_indices, ((0, 2), (1, 3)))
        self.assertEqual(displayed_colors(self.scene), original_colors)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(
            self.scene._atom_highlight_ring_polydata.GetNumberOfLines(),
            4,
        )
        ring_colors = (
            self.scene._atom_highlight_ring_polydata.GetCellData().GetScalars()
        )
        self.assertEqual(
            tuple(
                tuple(map(int, ring_colors.GetTuple3(index)))
                for index in range(4)
            ),
            (
                colors.primary_group,
                colors.primary_group,
                colors.secondary_group,
                colors.secondary_group,
            ),
        )
        primary_labels = (
            self.scene._primary_group_label_polydata.GetPointData().GetAbstractArray(
                "atom_index_label"
            )
        )
        secondary_labels = (
            self.scene._secondary_group_label_polydata.GetPointData().GetAbstractArray(
                "atom_index_label"
            )
        )
        self.assertEqual(
            tuple(primary_labels.GetValue(index) for index in range(2)),
            ("1", "3"),
        )
        self.assertEqual(
            tuple(secondary_labels.GetValue(index) for index in range(2)),
            ("2", "4"),
        )

    def test_surface_overlay_has_distinct_colors_1_based_labels_and_independent_lifecycle(self) -> None:
        structure = MolecularStructure(
            tuple(
                Atom(index, "Au", float(index % 3), float(index // 3), 0.0)
                for index in range(6)
            )
        )
        connectivity = Connectivity(6, ())
        self.scene.set_molecule(structure, connectivity, {"Au": 1.36})
        original_points = tuple(
            self.scene._atom_polydata.GetPoint(index) for index in range(6)
        )
        self.scene.set_highlighted_atom_indices((0,), ())

        self.scene.set_surface_verification((0, 1, 2), (3, 4, 5))

        self.assertEqual(
            self.scene.surface_verification_indices,
            ((0, 1, 2), (3, 4, 5)),
        )
        self.assertEqual(
            self.scene.surface_verification_labels,
            ("L 1", "L 2", "L 3", "R 4", "R 5", "R 6"),
        )
        colors = self.scene._surface_polydata.GetPointData().GetArray(
            "surface_shell_color"
        )
        self.assertEqual(tuple(map(int, colors.GetTuple3(0))), LEFT_SURFACE_COLOR_RGB)
        self.assertEqual(tuple(map(int, colors.GetTuple3(3))), RIGHT_SURFACE_COLOR_RGB)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 1)
        self.assertEqual(
            tuple(self.scene._atom_polydata.GetPoint(index) for index in range(6)),
            original_points,
        )

        self.scene.set_molecule(structure, connectivity, {"Au": 1.36})
        self.assertEqual(self.scene.surface_verification_indices, ((), ()))
        self.assertEqual(self.scene.surface_verification_labels, ())
        self.assertEqual(self.scene._surface_polydata.GetNumberOfPoints(), 0)

    def test_measurement_picks_use_dedicated_batched_style_and_order_labels(
        self,
    ) -> None:
        self.scene.set_highlighted_atom_indices((2,), (3,))

        self.scene.set_measurement_pick_feedback((2, 0))

        atom_indices = self.scene._highlight_polydata.GetPointData().GetArray(
            "atom_index"
        )
        colors = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_color"
        )
        radii = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_radius"
        )
        styles = {
            int(atom_indices.GetValue(index)): (
                tuple(int(value) for value in colors.GetTuple3(index)),
                radii.GetValue(index),
            )
            for index in range(atom_indices.GetNumberOfTuples())
        }
        self.assertEqual(self.scene._measurement_pick_indices, (2, 0))
        self.assertEqual(self.scene._measurement_pick_labels, ("1", "2"))
        self.assertEqual(self.scene._primary_highlight_indices, (2,))
        self.assertEqual(self.scene._secondary_highlight_indices, (3,))
        self.assertNotEqual(
            MEASUREMENT_HIGHLIGHT_COLOR_RGB,
            PRIMARY_HIGHLIGHT_COLOR_RGB,
        )
        self.assertNotEqual(
            MEASUREMENT_HIGHLIGHT_COLOR_RGB,
            SECONDARY_HIGHLIGHT_COLOR_RGB,
        )
        for atom_index in (2, 0):
            self.assertEqual(
                styles[atom_index][0],
                MEASUREMENT_HIGHLIGHT_COLOR_RGB,
            )
            self.assertAlmostEqual(
                styles[atom_index][1],
                self.scene._visual_radii[atom_index]
                * MEASUREMENT_HIGHLIGHT_SHELL_SIZE_MULTIPLIER,
            )
        self.assertEqual(
            tuple(
                actor.GetInput()
                for actor in self.scene._measurement_pick_text_actors
                if actor.GetVisibility()
            ),
            ("1", "2"),
        )
        self.assertEqual(len(self.scene._measurement_pick_text_actors), 3)

        self.scene.set_measurement_pick_feedback(())

        self.assertEqual(self.scene._measurement_pick_indices, ())
        self.assertEqual(self.scene._measurement_pick_labels, ())
        self.assertTrue(
            all(
                not actor.GetVisibility()
                for actor in self.scene._measurement_pick_text_actors
            )
        )
        restored_colors = self.scene._highlight_polydata.GetPointData().GetArray(
            "shell_color"
        )
        self.assertEqual(
            tuple(int(value) for value in restored_colors.GetTuple3(0)),
            PRIMARY_HIGHLIGHT_COLOR_RGB,
        )
        self.assertEqual(
            tuple(int(value) for value in restored_colors.GetTuple3(1)),
            SECONDARY_HIGHLIGHT_COLOR_RGB,
        )

    def test_loading_and_clearing_reset_highlight_state(self) -> None:
        self.scene.set_highlighted_atom_indices((2,), (3,))
        self.scene.set_measurement_pick_feedback((2, 0))
        self.scene.set_preview_atoms(
            (PreviewAtom((8.0, 9.0, 10.0), 0.5, (1, 2, 3)),),
            hidden_atom_indices=(2,),
        )
        guide = LatticeExtensionPreviewGuide(
            (8.0, 9.0, 10.0),
            ((10.0, 9.0, 10.0),),
            (2.0, 0.0, 0.0),
            (1.0, math.sqrt(3.0), 0.0),
            (12, 180, 240),
        )
        self.scene.set_lattice_extension_preview_guide(guide)
        replacement = MolecularStructure(
            atoms=(Atom(0, "C", 4.0, 5.0, 6.0),)
        )

        self.scene.set_molecule(
            replacement,
            Connectivity(atom_count=1, bonds=()),
            self.radii,
        )

        self.assertEqual(displayed_colors(self.scene), (ELEMENT_COLORS_RGB["C"],))
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._primary_highlight_indices, ())
        self.assertEqual(self.scene._secondary_highlight_indices, ())
        self.assertEqual(self.scene._measurement_pick_indices, ())
        self.assertEqual(self.scene._measurement_pick_labels, ())
        self.assertEqual(self.scene._preview_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._preview_atoms, ())
        self.assertEqual(self.scene._preview_hidden_atom_indices, ())
        self.assertIsNone(self.scene._lattice_extension_preview_guide)

        self.scene.set_lattice_extension_preview_guide(guide)
        self.scene.clear()

        self.assertEqual(self.scene._atom_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._primary_highlight_indices, ())
        self.assertEqual(self.scene._secondary_highlight_indices, ())
        self.assertEqual(self.scene._measurement_pick_indices, ())
        self.assertEqual(self.scene._measurement_pick_labels, ())
        self.assertEqual(self.scene._preview_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._preview_atoms, ())
        self.assertEqual(self.scene._preview_hidden_atom_indices, ())
        self.assertIsNone(self.scene._lattice_extension_preview_guide)
        self.assertEqual(
            self.scene._lattice_guide_bond_polydata.GetNumberOfLines(),
            0,
        )
        self.assertEqual(
            self.scene._lattice_guide_plane_polydata.GetNumberOfPolys(),
            0,
        )
        self.assertEqual(
            self.scene._lattice_guide_grid_polydata.GetNumberOfLines(),
            0,
        )
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

    def test_preview_atoms_preserve_molecule_and_use_one_gold_pipeline(self) -> None:
        original_points = tuple(
            self.scene._atom_polydata.GetPoint(index)
            for index in range(len(self.structure))
        )
        original_colors = displayed_colors(self.scene)
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.NCS, 2, (0, 1, 2)),
            4.5,
            1.25,
            -0.5,
        )
        gold_radius = self.radii["Au"] * ATOM_RADIUS_SCALE
        first_preview = PreviewAtom(
            (proposal.x, proposal.y, proposal.z),
            gold_radius,
            ELEMENT_COLORS_RGB["Au"],
        )

        self.scene.set_preview_atoms((first_preview,))

        self.assertEqual(self.scene._preview_polydata.GetNumberOfPoints(), 1)
        self.assertEqual(
            self.scene._preview_polydata.GetPoint(0),
            (proposal.x, proposal.y, proposal.z),
        )
        preview_radii = self.scene._preview_polydata.GetPointData().GetArray(
            "preview_radius"
        )
        self.assertAlmostEqual(preview_radii.GetValue(0), gold_radius)
        preview_colors = self.scene._preview_polydata.GetPointData().GetArray(
            "preview_color"
        )
        self.assertEqual(
            tuple(int(value) for value in preview_colors.GetTuple3(0)),
            ELEMENT_COLORS_RGB["Au"],
        )
        self.assertEqual(displayed_colors(self.scene), original_colors)
        self.assertEqual(
            tuple(
                self.scene._atom_polydata.GetPoint(index)
                for index in range(len(self.structure))
            ),
            original_points,
        )
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

        self.scene.set_preview_atoms(
            (
                first_preview,
                PreviewAtom(
                    (-4.5, -1.25, 0.5),
                    gold_radius,
                    ELEMENT_COLORS_RGB["Au"],
                ),
            )
        )

        self.assertEqual(self.scene._preview_polydata.GetNumberOfPoints(), 2)
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

        self.scene.set_preview_atoms(())

        self.assertEqual(self.scene._preview_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._preview_atoms, ())
        self.assertEqual(displayed_colors(self.scene), original_colors)
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

    def test_lattice_extension_guide_uses_dedicated_fading_unpickable_pipelines(
        self,
    ) -> None:
        original_atom_points = tuple(
            self.scene._atom_polydata.GetPoint(index)
            for index in range(len(self.structure))
        )
        original_bonds = self.connectivity.bonds
        center = (0.5, 0.25, 0.75)
        basis_u = (2.0, 0.0, 0.0)
        basis_v = (1.0, math.sqrt(3.0), 0.0)
        targets = ((2.5, 0.25, 0.75), (-0.5, -1.4820508075688772, 0.75))
        guide = LatticeExtensionPreviewGuide(
            center,
            targets,
            basis_u,
            basis_v,
            (12, 180, 240),
        )

        self.scene.set_lattice_extension_preview_guide(guide)

        self.assertIs(self.scene._lattice_extension_preview_guide, guide)
        self.assertGreater(
            self.scene._lattice_guide_bond_polydata.GetNumberOfLines(),
            len(targets),
        )
        bond_points = tuple(
            self.scene._lattice_guide_bond_polydata.GetPoint(index)
            for index in range(
                self.scene._lattice_guide_bond_polydata.GetNumberOfPoints()
            )
        )
        for expected in (center, *targets):
            self.assertTrue(
                any(math.dist(actual, expected) < 1.0e-12 for actual in bond_points)
            )
        self.assertEqual(
            self.scene._lattice_guide_plane_polydata.GetNumberOfPoints(),
            37,
        )
        self.assertGreater(
            self.scene._lattice_guide_plane_polydata.GetNumberOfPolys(),
            0,
        )
        self.assertGreater(
            self.scene._lattice_guide_grid_polydata.GetNumberOfLines(),
            0,
        )
        plane_colors = (
            self.scene._lattice_guide_plane_polydata.GetPointData().GetScalars()
        )
        grid_colors = (
            self.scene._lattice_guide_grid_polydata.GetPointData().GetScalars()
        )
        plane_points = self.scene._lattice_guide_plane_polydata.GetPoints()
        samples = sorted(
            (
                math.dist(center, plane_points.GetPoint(index)),
                int(plane_colors.GetTuple4(index)[3]),
                int(grid_colors.GetTuple4(index)[3]),
            )
            for index in range(plane_points.GetNumberOfPoints())
        )
        self.assertEqual(samples[0][1], LATTICE_GUIDE_PLANE_CENTER_ALPHA)
        self.assertEqual(samples[0][2], LATTICE_GUIDE_GRID_CENTER_ALPHA)
        for previous, current in zip(samples, samples[1:], strict=False):
            if current[0] > previous[0] + 1.0e-12:
                self.assertLessEqual(current[1], previous[1])
                self.assertLessEqual(current[2], previous[2])
        allowed_grid_directions = (basis_u, basis_v, _subtract(basis_u, basis_v))
        grid = self.scene._lattice_guide_grid_polydata
        for cell_index in range(grid.GetNumberOfCells()):
            cell = grid.GetCell(cell_index)
            first = grid.GetPoint(cell.GetPointId(0))
            second = grid.GetPoint(cell.GetPointId(1))
            segment = _subtract(second, first)
            self.assertTrue(
                any(
                    math.dist(segment, direction) < 1.0e-12
                    or math.dist(segment, tuple(-value for value in direction))
                    < 1.0e-12
                    for direction in allowed_grid_directions
                )
            )
        for actor in (
            self.scene._lattice_guide_bond_actor,
            self.scene._lattice_guide_plane_actor,
            self.scene._lattice_guide_grid_actor,
        ):
            self.assertTrue(actor.GetVisibility())
            self.assertFalse(actor.GetPickable())
        self.assertEqual(
            tuple(
                self.scene._atom_polydata.GetPoint(index)
                for index in range(len(self.structure))
            ),
            original_atom_points,
        )
        self.assertEqual(self.connectivity.bonds, original_bonds)

        replacement = LatticeExtensionPreviewGuide(
            (1.0, 1.0, 1.0),
            ((3.0, 1.0, 1.0),),
            basis_u,
            basis_v,
            (255, 48, 64),
        )
        self.scene.set_lattice_extension_preview_guide(replacement)
        self.assertIs(self.scene._lattice_extension_preview_guide, replacement)
        self.assertEqual(
            self.scene._lattice_guide_bond_actor.GetProperty().GetColor(),
            (1.0, 48 / 255.0, 64 / 255.0),
        )
        self.assertEqual(
            self.scene._lattice_guide_plane_polydata.GetNumberOfPoints(),
            37,
        )

        self.scene.set_lattice_extension_preview_guide(None)
        self.assertIsNone(self.scene._lattice_extension_preview_guide)
        self.assertEqual(
            self.scene._lattice_guide_bond_polydata.GetNumberOfLines(),
            0,
        )
        self.assertEqual(
            self.scene._lattice_guide_plane_polydata.GetNumberOfPolys(),
            0,
        )
        self.assertEqual(
            self.scene._lattice_guide_grid_polydata.GetNumberOfLines(),
            0,
        )
        self.assertTrue(
            all(
                not actor.GetVisibility()
                for actor in (
                    self.scene._lattice_guide_bond_actor,
                    self.scene._lattice_guide_plane_actor,
                    self.scene._lattice_guide_grid_actor,
                )
            )
        )

    def test_replacement_preview_hides_only_scheduled_atom_and_restores_it(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "C", 1.0, 0.0, 0.0),
                Atom(1, "S", 0.0, 0.0, 0.0),
                Atom(2, "H", -0.5, 1.0, 0.0),
            )
        )
        connectivity = Connectivity(
            3,
            (Bond(0, 1, 1.0), Bond(1, 2, 5.0**0.5 / 2.0)),
        )
        radii = {"C": 0.76, "S": 1.05, "H": 0.31, "Au": 1.36}
        self.scene.set_molecule(structure, connectivity, radii)
        self.scene.set_view_preferences(ViewPreferences(show_element_labels=True))
        self.scene.set_highlighted_atom_indices((1, 2), ())
        self.scene.set_measurement_pick_feedback((2,))
        preview = PreviewAtom(
            (-2.3, 0.0, 0.0),
            radii["Au"] * ATOM_RADIUS_SCALE,
            ELEMENT_COLORS_RGB["Au"],
            "Au",
        )
        source_atoms = structure.atoms
        source_bonds = connectivity.bonds

        self.scene.set_preview_atoms((preview,), hidden_atom_indices=(2,))

        self.assertIs(structure.atoms, source_atoms)
        self.assertIs(connectivity.bonds, source_bonds)
        self.assertEqual(self.scene._preview_hidden_atom_indices, (2,))
        self.assertEqual(self.scene._atom_polydata.GetNumberOfPoints(), 2)
        atom_indices = self.scene._atom_polydata.GetPointData().GetArray(
            "atom_index"
        )
        self.assertEqual(
            tuple(
                int(atom_indices.GetValue(index))
                for index in range(atom_indices.GetNumberOfTuples())
            ),
            (0, 1),
        )
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 1)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 1)
        self.assertTrue(
            all(
                not actor.GetVisibility()
                for actor in self.scene._measurement_pick_text_actors
            )
        )
        self.assertEqual(
            tuple(actor.GetInput() for actor in self.scene._element_label_actors),
            ("C", "S", "Au"),
        )

        self.scene.set_preview_atoms(())

        self.assertEqual(self.scene._preview_hidden_atom_indices, ())
        self.assertEqual(self.scene._atom_polydata.GetNumberOfPoints(), 3)
        self.assertEqual(self.scene._bond_polydata.GetNumberOfLines(), 2)
        self.assertEqual(self.scene._highlight_polydata.GetNumberOfPoints(), 2)
        self.assertEqual(
            tuple(actor.GetInput() for actor in self.scene._element_label_actors),
            ("C", "S", "H"),
        )
        self.assertEqual(structure.atoms, source_atoms)
        self.assertEqual(connectivity.bonds, source_bonds)

    def test_annotations_use_actual_geometry_and_two_batched_actors(self) -> None:
        distance_annotation = DistanceAnnotation(
            (0.0, 0.0, 0.0),
            (2.34, 0.0, 0.0),
        )
        angle_annotation = AngleAnnotation(
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 2.0, 0.0),
        )

        self.scene.set_annotations(
            (distance_annotation,),
            (angle_annotation,),
        )

        self.assertGreater(self.scene._dash_polydata.GetNumberOfLines(), 1)
        self.assertEqual(
            self.scene._dash_polydata.GetPoint(0),
            distance_annotation.start,
        )
        distance_dash_count = len(
            _dash_segments(distance_annotation.start, distance_annotation.end)
        )
        self.assertEqual(
            self.scene._dash_polydata.GetPoint(
                distance_dash_count * 2 - 1
            ),
            distance_annotation.end,
        )
        self.assertEqual(
            self.scene._dash_polydata.GetNumberOfLines(),
            distance_dash_count,
        )
        self.assertGreater(self.scene._arc_polydata.GetNumberOfLines(), 1)
        self.assertEqual(
            self.scene._arc_polydata.GetPoint(0),
            (ANNOTATION_ARC_RADIUS, 0.0, 0.0),
        )
        arc_end = self.scene._arc_polydata.GetPoint(
            self.scene._arc_polydata.GetNumberOfPoints() - 1
        )
        self.assertAlmostEqual(arc_end[0], 0.0, places=12)
        self.assertAlmostEqual(arc_end[1], ANNOTATION_ARC_RADIUS, places=12)
        self.assertAlmostEqual(arc_end[2], 0.0, places=12)
        self.assertEqual(self.scene._annotation_texts, ("2.34 Å", "90.0°"))
        self.assertIs(self.scene._dash_actor.GetMapper(), self.scene._dash_mapper)
        self.assertIs(self.scene._arc_actor.GetMapper(), self.scene._arc_mapper)

    def test_distance_label_is_camera_aware_offset_and_has_safe_fallback(
        self,
    ) -> None:
        renderer = vtkRenderer()
        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(800, 600)
        render_window.AddRenderer(renderer)
        scene = MoleculeScene(renderer)
        annotation = DistanceAnnotation((0.0, 0.0, 0.0), (2.0, 1.0, 1.0))
        scene.set_annotations((annotation,), ())
        actor = scene._annotation_text_actors[0]
        camera = renderer.GetActiveCamera()
        observed_layouts = []

        for position, view_up in (
            ((0.0, 0.0, 10.0), (0.0, 1.0, 0.0)),
            ((10.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        ):
            camera.SetPosition(*position)
            camera.SetFocalPoint(0.0, 0.0, 0.0)
            camera.SetViewUp(*view_up)
            renderer.InvokeEvent("StartEvent")
            projected_start = _display_point(renderer, annotation.start)
            projected_end = _display_point(renderer, annotation.end)
            self.assertIsNotNone(projected_start)
            self.assertIsNotNone(projected_end)
            expected = _distance_label_screen_layout(
                projected_start,
                projected_end,
            )
            observed = (
                actor.GetTextProperty().GetOrientation(),
                actor.GetDisplayOffset(),
            )
            observed_layouts.append(observed)
            self.assertAlmostEqual(observed[0], expected.orientation_degrees)
            self.assertEqual(observed[1], expected.display_offset)
            self.assertNotEqual(observed[1], (0, 0))
            delta_x = projected_end[0] - projected_start[0]
            delta_y = projected_end[1] - projected_start[1]
            perpendicular_distance = abs(
                delta_x * observed[1][1] - delta_y * observed[1][0]
            ) / math.hypot(delta_x, delta_y)
            self.assertGreater(perpendicular_distance, 12.0)

        self.assertNotEqual(observed_layouts[0], observed_layouts[1])
        self.assertEqual(actor.GetPosition(), (1.0, 0.5, 0.5))
        fallback = _distance_label_screen_layout((20.0, 30.0), (20.1, 30.1))
        self.assertEqual(fallback.orientation_degrees, 0.0)
        self.assertEqual(fallback.display_offset, (0, 18))
        self.assertTrue(math.isfinite(fallback.orientation_degrees))

    def test_annotations_clear_without_rebuilding_pipelines(self) -> None:
        self.scene.set_annotations(
            (DistanceAnnotation((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),),
            (
                AngleAnnotation(
                    (0.0, 0.0, 0.0),
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                ),
            ),
        )

        self.scene.set_annotations((), ())

        self.assertEqual(self.scene._dash_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._arc_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._annotation_texts, ())
        self.assertTrue(
            all(
                not actor.GetVisibility()
                for actor in self.scene._annotation_text_actors
            )
        )
        self.assertEqual(self.renderer.GetActors().GetNumberOfItems(), 19)

    def test_two_pyridine_previews_fit_six_bounded_annotation_labels(self) -> None:
        distances = (
            DistanceAnnotation((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
            DistanceAnnotation((10.0, 0.0, 0.0), (8.0, 0.0, 0.0)),
        )
        angles = tuple(
            AngleAnnotation(
                (float(origin), 0.0, 0.0),
                (float(origin), 1.0, 0.0),
                (float(origin), 0.0, 1.0),
            )
            for origin in range(4)
        )

        self.scene.set_annotations(distances, angles)

        self.assertEqual(len(self.scene._annotation_texts), 6)
        self.assertEqual(len(self.scene._angle_annotations), 4)

    def test_manual_overlay_has_stable_ids_and_survives_preview_cleanup(self) -> None:
        preview_distance = DistanceAnnotation(
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        )
        measurements = (
            MeasurementOverlayAnnotation(
                11,
                DistanceAnnotation((0.0, 0.0, 0.0), (0.0, 2.0, 0.0)),
            ),
            MeasurementOverlayAnnotation(
                12,
                AngleAnnotation(
                    (0.0, 0.0, 0.0),
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                ),
            ),
        )
        self.scene.set_annotations((preview_distance,), ())
        self.scene.set_measurement_annotations(measurements)

        self.assertEqual(self.scene._rendered_measurement_ids, (11, 12))
        self.assertEqual(self.scene._measurement_annotations, measurements)
        self.assertEqual(len(self.scene._annotation_texts), 3)

        self.scene.set_annotations((), ())

        self.assertEqual(self.scene._rendered_measurement_ids, (11, 12))
        self.assertGreater(self.scene._dash_polydata.GetNumberOfLines(), 1)
        self.assertGreater(self.scene._arc_polydata.GetNumberOfLines(), 1)
        self.assertEqual(self.scene._annotation_texts, ("2.00 Å", "90.0°"))

        self.scene.set_measurement_annotations(())

        self.assertEqual(self.scene._rendered_measurement_ids, ())
        self.assertEqual(self.scene._dash_polydata.GetNumberOfPoints(), 0)
        self.assertEqual(self.scene._arc_polydata.GetNumberOfPoints(), 0)

    def test_removing_one_angle_id_removes_its_guides_arc_and_label(self) -> None:
        first_distance = MeasurementOverlayAnnotation(
            21,
            DistanceAnnotation((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        )
        angle = MeasurementOverlayAnnotation(
            22,
            AngleAnnotation(
                (0.0, 0.0, 0.0),
                (0.0, 2.0, 0.0),
                (0.0, 0.0, 3.0),
            ),
        )
        second_distance = MeasurementOverlayAnnotation(
            23,
            DistanceAnnotation((4.0, 0.0, 0.0), (4.0, 2.0, 0.0)),
        )
        self.scene.set_measurement_annotations(
            (first_distance, angle, second_distance)
        )
        angle_guide_count = len(
            _dash_segments(
                angle.annotation.reference_point,
                angle.annotation.vertex,
            )
        ) + len(
            _dash_segments(
                angle.annotation.vertex,
                angle.annotation.target_point,
            )
        )
        full_dash_count = self.scene._dash_polydata.GetNumberOfLines()

        self.scene.set_measurement_annotations(
            (first_distance, second_distance)
        )

        self.assertEqual(self.scene._rendered_measurement_ids, (21, 23))
        self.assertEqual(
            self.scene._dash_polydata.GetNumberOfLines(),
            full_dash_count - angle_guide_count,
        )
        self.assertEqual(self.scene._arc_polydata.GetNumberOfLines(), 0)
        self.assertEqual(self.scene._annotation_texts, ("1.00 Å", "2.00 Å"))

    def test_measurement_labels_grow_and_structure_replacement_clears_layer(self) -> None:
        measurements = tuple(
            MeasurementOverlayAnnotation(
                index + 1,
                DistanceAnnotation(
                    (float(index), 0.0, 0.0),
                    (float(index), 1.0, 0.0),
                ),
            )
            for index in range(7)
        )

        self.scene.set_measurement_annotations(measurements)

        self.assertEqual(self.scene._rendered_measurement_ids, tuple(range(1, 8)))
        self.assertGreaterEqual(len(self.scene._annotation_text_actors), 7)
        self.assertEqual(len(self.scene._annotation_texts), 7)

        replacement = MolecularStructure((Atom(0, "C", 4.0, 5.0, 6.0),))
        self.scene.set_molecule(
            replacement,
            Connectivity(atom_count=1, bonds=()),
            self.radii,
        )

        self.assertEqual(self.scene._measurement_annotations, ())
        self.assertEqual(self.scene._rendered_measurement_ids, ())
        self.assertEqual(self.scene._annotation_texts, ())

    def test_rendered_bond_cells_map_back_to_connectivity_edges(self) -> None:
        bond_ids = self.scene._bond_polydata.GetCellData().GetArray("bond_index")
        self.assertEqual(
            tuple(
                int(bond_ids.GetValue(index))
                for index in range(bond_ids.GetNumberOfTuples())
            ),
            (0, 1, 2),
        )
        self.assertEqual(
            self.scene._bond_edges,
            tuple(
                (bond.first_index, bond.second_index)
                for bond in self.connectivity
            ),
        )

        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(600, 400)
        render_window.AddRenderer(self.renderer)
        self.renderer.ResetCamera()
        render_window.Render()
        midpoint = _display_point(self.renderer, (0.5, 0.0, 0.0))
        self.assertIsNotNone(midpoint)

        selected = self.scene.pick_bond_edge(
            round(midpoint[0]),
            round(midpoint[1]),
        )

        self.assertEqual(selected, (0, 1))

    def test_atom_pick_has_minimum_screen_radius_for_small_atoms(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "H", 0.0, 0.0, 0.0),
                Atom(1, "C", 12.0, 0.0, 0.0),
            )
        )
        self.scene.set_molecule(
            structure,
            Connectivity(atom_count=2, bonds=()),
            {"H": 0.31, "C": 0.76},
        )
        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(400, 400)
        render_window.AddRenderer(self.renderer)
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(0.0, 0.0, 50.0)
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetViewUp(0.0, 1.0, 0.0)
        camera.ParallelProjectionOn()
        camera.SetParallelScale(20.0)
        self.renderer.ResetCameraClippingRange()
        render_window.Render()
        center = _display_point(self.renderer, (0.0, 0.0, 0.0))
        surface = _display_point(
            self.renderer,
            (0.0, 0.31 * ATOM_RADIUS_SCALE, 0.0),
        )
        self.assertIsNotNone(center)
        self.assertIsNotNone(surface)
        projected_radius = math.hypot(
            surface[0] - center[0],
            surface[1] - center[1],
        )
        click_x = round(center[0]) + 5
        click_y = round(center[1])
        self.assertLess(projected_radius, 5.0)
        self.assertLess(5.0, MINIMUM_ATOM_PICK_RADIUS_PIXELS)

        self.assertIsNone(
            self.scene.pick_atom_index(
                click_x,
                click_y,
                minimum_radius_pixels=0.0,
            )
        )
        self.assertEqual(self.scene.pick_atom_index(click_x, click_y), 0)
        render_window.RemoveRenderer(self.renderer)

    def test_overlapping_atom_pick_prefers_frontmost_visible_atom(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "C", 0.0, 0.0, 4.0),
            )
        )
        self.scene.set_molecule(
            structure,
            Connectivity(atom_count=2, bonds=()),
            {"C": 0.76},
        )
        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(400, 400)
        render_window.AddRenderer(self.renderer)
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(0.0, 0.0, 20.0)
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetViewUp(0.0, 1.0, 0.0)
        self.renderer.ResetCameraClippingRange()
        render_window.Render()
        center = _display_point(self.renderer, (0.0, 0.0, 4.0))
        self.assertIsNotNone(center)

        selected = self.scene.pick_atom_index(round(center[0]), round(center[1]))

        self.assertEqual(selected, 1)
        render_window.RemoveRenderer(self.renderer)

    def test_preview_pick_returns_opaque_token_without_scene_mutation(self) -> None:
        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(400, 400)
        render_window.AddRenderer(self.renderer)
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(0.0, 0.0, 20.0)
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetViewUp(0.0, 1.0, 0.0)
        self.renderer.ResetCameraClippingRange()
        render_window.Render()
        token = ("LEFT", 0, 2, -1)
        target = PreviewPickTarget(token, (0.0, 0.0, 0.0), 0.6)
        center = _display_point(self.renderer, target.coordinates)
        self.assertIsNotNone(center)
        original_atom_points = self.scene._atom_polydata.GetNumberOfPoints()

        selected = self.scene.pick_preview_target(
            round(center[0]),
            round(center[1]),
            (target,),
        )

        self.assertIs(selected, token)
        self.assertEqual(
            self.scene._atom_polydata.GetNumberOfPoints(),
            original_atom_points,
        )
        render_window.RemoveRenderer(self.renderer)

    def test_overlapping_preview_pick_prefers_frontmost_then_input_order(
        self,
    ) -> None:
        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(400, 400)
        render_window.AddRenderer(self.renderer)
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(0.0, 0.0, 20.0)
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetViewUp(0.0, 1.0, 0.0)
        self.renderer.ResetCameraClippingRange()
        camera.SetClippingRange(0.1, 100.0)
        render_window.Render()
        rear_token = object()
        front_token = object()
        targets = (
            PreviewPickTarget(rear_token, (0.0, 0.0, 0.0), 0.6),
            PreviewPickTarget(front_token, (0.0, 0.0, 4.0), 0.6),
        )
        center = _display_point(self.renderer, (0.0, 0.0, 4.0))
        self.assertIsNotNone(center)

        selected = self.scene.pick_preview_target(
            round(center[0]),
            round(center[1]),
            targets,
        )

        self.assertIs(selected, front_token)
        self.assertIsNone(
            self.scene.pick_preview_target(0, 0, targets, minimum_radius_pixels=0)
        )
        render_window.RemoveRenderer(self.renderer)

    def test_torsion_gizmo_has_selected_bond_rays_arc_value_and_side_arrow(
        self,
    ) -> None:
        reference = self.scene.reference_direction_for_bond(1, 2)

        self.scene.set_torsion_gizmo(TorsionGizmo(1, 2, reference, 30.0))

        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 1)
        self.assertGreater(self.scene._torsion_circle_polydata.GetNumberOfLines(), 40)
        self.assertEqual(
            self.scene._torsion_fixed_ray_polydata.GetNumberOfLines(),
            1,
        )
        self.assertEqual(
            self.scene._torsion_moving_ray_polydata.GetNumberOfLines(),
            1,
        )
        self.assertGreater(self.scene._torsion_angle_polydata.GetNumberOfLines(), 0)
        self.assertEqual(self.scene._torsion_side_polydata.GetNumberOfLines(), 3)
        self.assertEqual(self.scene._torsion_angle_text_actor.GetInput(), "+30.0°")
        self.assertTrue(self.scene._torsion_angle_text_actor.GetVisibility())
        self.assertTrue(self.scene._torsion_handle_actor.GetVisibility())
        self.assertEqual(
            tuple(self.scene._torsion_handle_actor.GetPosition()),
            self.scene._torsion_handle_position,
        )
        self.assertEqual(
            tuple(self.scene._torsion_moving_ray_polydata.GetPoint(1)),
            self.scene._torsion_handle_position,
        )

        self.scene.set_torsion_angle_text_visible(False)
        self.assertFalse(self.scene._torsion_angle_text_actor.GetVisibility())
        self.scene.set_torsion_angle_text_visible(True)
        self.assertTrue(self.scene._torsion_angle_text_actor.GetVisibility())
        self.assertAlmostEqual(
            self.scene._selected_bond_actor.GetProperty().GetOpacity(),
            0.52,
        )

        self.scene.set_torsion_gizmo(TorsionGizmo(2, 1, reference, 0.0))

        self.assertEqual(self.scene._torsion_angle_polydata.GetNumberOfLines(), 0)
        self.assertEqual(self.scene._torsion_angle_text_actor.GetInput(), "0.0°")

    def test_torsion_pointer_angle_maps_a_tilted_camera_ray_to_axis_plane(self):
        reference = self.scene.reference_direction_for_bond(1, 2)
        self.scene.set_torsion_gizmo(TorsionGizmo(1, 2, reference, 0.0))
        center = self.scene._torsion_center
        fixed = self.scene._point_for_atom(1)
        rotating = self.scene._point_for_atom(2)
        axis = _normalized(_subtract(rotating, fixed), "test torsion axis")
        radial = _subtract(self.scene._torsion_handle_position, center)
        target = _add(
            center,
            _rotate_vector_about_axis(radial, axis, math.radians(73.0)),
        )

        render_window = vtkRenderWindow()
        render_window.SetOffScreenRendering(True)
        render_window.SetSize(700, 500)
        render_window.AddRenderer(self.renderer)
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(center[0] + 7.0, center[1] + 4.0, center[2] + 6.0)
        camera.SetFocalPoint(*center)
        camera.SetViewUp(0.0, 1.0, 0.0)
        self.renderer.ResetCameraClippingRange()
        render_window.Render()
        target_display = _display_point(self.renderer, target)
        self.assertIsNotNone(target_display)

        angle = self.scene.torsion_pointer_angle_degrees(
            round(target_display[0]),
            round(target_display[1]),
        )

        self.assertAlmostEqual(angle, 73.0, delta=0.75)

    def test_coordinate_update_preserves_edge_identity_and_refreshes_gizmo(self):
        original_edges = self.scene._bond_edges
        reference = self.scene.reference_direction_for_bond(1, 2)
        self.scene.set_torsion_gizmo(TorsionGizmo(1, 2, reference, -45.0))
        replacement = MolecularStructure(
            (
                Atom(0, "C", 0.0, 1.0, 0.0),
                Atom(1, "N", 1.0, 1.0, 0.0),
                Atom(2, "S", 2.0, 1.0, 0.0),
                Atom(3, "Au", 3.0, 1.0, 0.0),
            )
        )

        self.scene.update_molecule_coordinates(replacement)

        self.assertEqual(self.scene._bond_edges, original_edges)
        self.assertEqual(self.scene._atom_polydata.GetPoint(3), (3.0, 1.0, 0.0))
        self.assertEqual(self.scene._bond_polydata.GetPoint(3), (3.0, 1.0, 0.0))
        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 1)
        self.assertEqual(self.scene._torsion_angle_text_actor.GetInput(), "-45.0°")

        self.scene.clear_torsion_gizmo()

        self.assertEqual(self.scene._selected_bond_polydata.GetNumberOfLines(), 0)
        self.assertEqual(self.scene._torsion_circle_polydata.GetNumberOfLines(), 0)
        self.assertFalse(self.scene._torsion_angle_text_actor.GetVisibility())
        self.assertFalse(self.scene._torsion_handle_actor.GetVisibility())

    def test_base_atoms_preserve_selection_ids_and_preview_is_not_pickable(self) -> None:
        selection_ids = self.scene._atom_polydata.GetPointData().GetArray(
            "atom_index"
        )

        self.assertEqual(
            tuple(
                int(selection_ids.GetValue(index))
                for index in range(selection_ids.GetNumberOfTuples())
            ),
            tuple(range(len(self.structure))),
        )
        self.assertTrue(self.scene._atom_mapper.GetUseSelectionIds())
        self.assertTrue(self.scene._atom_actor.GetPickable())
        self.assertFalse(self.scene._preview_actor.GetPickable())
        self.assertEqual(self.scene._atom_actor.GetProperty().GetOpacity(), 1.0)
        self.assertEqual(
            self.scene._preview_actor.GetProperty().GetOpacity(),
            PREVIEW_ATOM_OPACITY,
        )


if __name__ == "__main__":
    unittest.main()
