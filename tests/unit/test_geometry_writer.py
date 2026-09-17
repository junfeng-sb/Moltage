import tempfile
import unittest
from pathlib import Path

from moltage.aims.geometry_writer import (
    render_geometry_in,
    write_geometry_in,
)
from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.junction import AuPlacementProposal
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.apply_placement import apply_au_placements
from moltage.structure.xyz import read_xyz


FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures" / "phase1b"


def _parse_geometry_records(text: str) -> list[tuple[str, float, float, float]]:
    records = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        fields = line.split()
        if len(fields) != 5 or fields[0] != "atom":
            raise AssertionError(f"invalid reference atom record on line {line_number}")
        _, x_text, y_text, z_text, element = fields
        records.append((element, float(x_text), float(y_text), float(z_text)))
    return records


class GeometryWriterTests(unittest.TestCase):
    def test_renders_minimal_structure_in_atom_order(self) -> None:
        structure = MolecularStructure(
            atoms=(
                Atom(index=0, element="Fe", x=1.25, y=-2, z=0),
                Atom(index=1, element="H", x=0, y=3.5, z=-4.75),
            )
        )

        rendered = render_geometry_in(structure)

        self.assertEqual(
            rendered,
            "atom 1.25 -2.0 0.0 Fe\n"
            "atom 0.0 3.5 -4.75 H\n",
        )

    def test_user_reference_is_semantically_equivalent(self) -> None:
        structure = read_xyz(FIXTURE_DIRECTORY / "synthetic_dual_ncs.xyz")
        generated_records = _parse_geometry_records(render_geometry_in(structure))
        reference_records = _parse_geometry_records(
            (FIXTURE_DIRECTORY / "geometry.in").read_text(encoding="utf-8")
        )

        self.assertEqual(len(generated_records), 16)
        self.assertEqual(
            [record[0] for record in generated_records],
            [record[0] for record in reference_records],
        )
        self.assertEqual(
            [record[1:] for record in generated_records],
            [record[1:] for record in reference_records],
        )

    def test_writes_rendered_text_to_requested_path(self) -> None:
        structure = MolecularStructure(
            atoms=(Atom(index=0, element="N", x=1, y=2, z=3),)
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "geometry.in"

            write_geometry_in(structure, output_path)

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                render_geometry_in(structure),
            )

    def test_writes_applied_structure_order_without_removed_hydrogen(self) -> None:
        source = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "S", 1.0, 0.0, 0.0),
                Atom(2, "H", 1.0, 1.0, 0.0),
            )
        )
        connectivity = Connectivity(
            3,
            (Bond(0, 1, 1.0), Bond(1, 2, 1.0)),
        )
        proposal = AuPlacementProposal(
            AnchorCandidate(AnchorKind.SH, 1, (1, 2)),
            2.2345678901234567,
            -0.75,
            0.125,
            remove_atom_indices=(2,),
        )
        applied = apply_au_placements(source, connectivity, (proposal,))

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "geometry.in"
            write_geometry_in(applied.structure, output_path)
            records = _parse_geometry_records(
                output_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            records,
            [
                (atom.element, atom.x, atom.y, atom.z)
                for atom in applied.structure
            ],
        )
        self.assertNotIn("H", tuple(record[0] for record in records))
        self.assertEqual(records[-1][0], "Au")
        self.assertEqual(records[-1][1:], (proposal.x, proposal.y, proposal.z))


if __name__ == "__main__":
    unittest.main()
