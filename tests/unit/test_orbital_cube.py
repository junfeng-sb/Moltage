import unittest

from moltage.aims.orbital_cube import (
    FRONTIER_ORBITAL_ORDER,
    FrontierOrbital,
    OrbitalCubeOutputSettings,
    OrbitalCubeValidationError,
    default_step1_orbital_cubes,
    format_eigenstate_indices_text,
    parse_eigenstate_indices_text,
    parse_generated_orbital_cube_directives,
    render_orbital_cube_directives,
)
from moltage.domain.structure import Atom, MolecularStructure


class OrbitalCubeTests(unittest.TestCase):
    def test_step1_defaults_request_all_six_frontier_orbitals(self) -> None:
        settings = default_step1_orbital_cubes()

        self.assertEqual(settings.frontier_orbitals, FRONTIER_ORBITAL_ORDER)
        self.assertEqual(settings.eigenstate_indices, ())
        self.assertIsNone(settings.grid_spacing_angstrom)

    def test_renderer_uses_labels_absolute_states_and_optional_spacing(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "H", 0.0, 0.0, 0.0),
                Atom(1, "H", 1.0, 2.0, 3.0),
            )
        )
        settings = OrbitalCubeOutputSettings(
            frontier_orbitals=(
                FrontierOrbital.LUMO_PLUS_1,
                FrontierOrbital.HOMO,
            ),
            eigenstate_indices=(31, 12),
            grid_spacing_angstrom=0.08,
        )

        self.assertEqual(
            render_orbital_cube_directives(settings, structure),
            (
                "output cube eigenstate homo",
                "cube filename orbital_HOMO.cube",
                "cube origin -7.408480952642 -7.408480952642 -7.408480952642",
                "cube edge 198 0.08 0.0 0.0",
                "cube edge 211 0.0 0.08 0.0",
                "cube edge 223 0.0 0.0 0.08",
                "output cube eigenstate lumo+1",
                "cube filename orbital_LUMO_plus_1.cube",
                "cube origin -7.408480952642 -7.408480952642 -7.408480952642",
                "cube edge 198 0.08 0.0 0.0",
                "cube edge 211 0.0 0.08 0.0",
                "cube edge 223 0.0 0.0 0.08",
                "output cube eigenstate 12",
                "cube filename orbital_state_12.cube",
                "cube origin -7.408480952642 -7.408480952642 -7.408480952642",
                "cube edge 198 0.08 0.0 0.0",
                "cube edge 211 0.0 0.08 0.0",
                "cube edge 223 0.0 0.0 0.08",
                "output cube eigenstate 31",
                "cube filename orbital_state_31.cube",
                "cube origin -7.408480952642 -7.408480952642 -7.408480952642",
                "cube edge 198 0.08 0.0 0.0",
                "cube edge 211 0.0 0.08 0.0",
                "cube edge 223 0.0 0.0 0.08",
            ),
        )

    def test_generated_blocks_parse_and_leave_other_directives(self) -> None:
        settings = OrbitalCubeOutputSettings(
            frontier_orbitals=(FrontierOrbital.HOMO_MINUS_2,),
            eigenstate_indices=(25,),
        )
        directives = (
            "output dipole",
            *render_orbital_cube_directives(settings),
            "restart aims.restart",
        )

        parsed, remaining = parse_generated_orbital_cube_directives(directives)

        self.assertEqual(parsed, settings)
        self.assertEqual(remaining, ("output dipole", "restart aims.restart"))

    def test_index_text_is_canonical_and_rejects_duplicates(self) -> None:
        self.assertEqual(parse_eigenstate_indices_text("31, 12  20"), (12, 20, 31))
        self.assertEqual(format_eigenstate_indices_text((31, 12)), "12, 31")
        with self.assertRaisesRegex(
            OrbitalCubeValidationError,
            "duplicate",
        ):
            parse_eigenstate_indices_text("12, 12")

    def test_spacing_without_an_orbital_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            OrbitalCubeValidationError,
            "at least one",
        ):
            OrbitalCubeOutputSettings(grid_spacing_angstrom=0.1)

    def test_explicit_spacing_requires_geometry_for_safe_grid_extents(self) -> None:
        settings = OrbitalCubeOutputSettings(
            eigenstate_indices=(1,),
            grid_spacing_angstrom=0.2,
        )
        with self.assertRaisesRegex(
            OrbitalCubeValidationError,
            "requires the molecular structure",
        ):
            render_orbital_cube_directives(settings)


if __name__ == "__main__":
    unittest.main()
