import math
from pathlib import Path
import unittest

from moltage.domain.au_pyramid import (
    AU_PYRAMID_GEOMETRY_MODEL,
    AU_PYRAMID_SPACING_ANGSTROM,
    AuPyramidError,
    generate_au_pyramid,
)


class ElectrodeGeneratorTests(unittest.TestCase):
    def test_all_supported_sizes_have_deterministic_lattice_identity(self) -> None:
        expected = {2: 4, 3: 10, 4: 20, 5: 35, 6: 56, 7: 84, 8: 120, 9: 165, 10: 220}
        for layers, atom_count in expected.items():
            with self.subTest(layers=layers):
                first = generate_au_pyramid(layers)
                second = generate_au_pyramid(layers)
                self.assertIs(first, second)
                self.assertEqual(first.geometry_model, AU_PYRAMID_GEOMETRY_MODEL)
                self.assertEqual(len(first.structure), atom_count)
                self.assertEqual(len(first.atom_identities), atom_count)
                self.assertEqual(
                    tuple(identity.local_index for identity in first.atom_identities),
                    tuple(range(atom_count)),
                )
                self.assertEqual(
                    len({identity.lattice_key for identity in first.atom_identities}),
                    atom_count,
                )
                self.assertEqual(
                    tuple(
                        (identity.layer_index, *identity.lattice_key)
                        for identity in first.atom_identities
                    ),
                    tuple(
                        sorted(
                            (identity.layer_index, *identity.lattice_key)
                            for identity in first.atom_identities
                        )
                    ),
                )
                self.assertEqual(first.apex_lattice_key, (0, 0, 0))
                self.assertEqual(
                    first.reference_corner_lattice_keys,
                    ((layers - 1, 0, 0), (0, layers - 1, 0), (0, 0, layers - 1)),
                )
                self.assertTrue(
                    all(
                        math.isfinite(value)
                        for atom in first.structure
                        for value in (atom.x, atom.y, atom.z)
                    )
                )

    def test_connectivity_is_lattice_derived_at_the_project_spacing(self) -> None:
        for layers in range(2, 11):
            pyramid = generate_au_pyramid(layers)
            self.assertTrue(pyramid.connectivity.bonds)
            self.assertTrue(
                all(
                    math.isclose(
                        bond.distance,
                        AU_PYRAMID_SPACING_ANGSTROM,
                        rel_tol=0.0,
                        abs_tol=1.0e-10,
                    )
                    for bond in pyramid.connectivity
                )
            )
            self.assertAlmostEqual(math.hypot(*pyramid.principal_axis), 1.0)

    def test_invalid_layer_counts_are_rejected_without_coercion(self) -> None:
        for value in (1, 11, True, 6.0, "6", None):
            with self.subTest(value=value), self.assertRaises((TypeError, AuPyramidError)):
                generate_au_pyramid(value)

    def test_old_coordinate_templates_are_not_distributed_or_referenced(self) -> None:
        root = Path(__file__).resolve().parents[2]
        obsolete = {"au_6layer_variant_a.xyz", "au_6layer_variant_b.xyz"}
        resource_files = {path.name for path in (root / "resources").rglob("*") if path.is_file()}
        self.assertTrue(obsolete.isdisjoint(resource_files))
        searched = [root / "pyproject.toml", *(root / "packaging").rglob("*")]
        for path in searched:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                for name in obsolete:
                    self.assertNotIn(name, text, str(path))


if __name__ == "__main__":
    unittest.main()
