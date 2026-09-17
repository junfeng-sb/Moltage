import unittest
from pathlib import Path
import tempfile

from moltage.app.imported_start import (
    ImportedContactAuEligibilityError,
    ImportedTransportStartContext,
    applied_contact_au_context,
    imported_contact_au_context,
)
from moltage.domain.anchor import AnchorCandidate
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.junction.apply_placement import apply_au_placements
from moltage.junction.au_placement import (
    AuPlacementParameters,
    propose_au_placements,
)
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from moltage.junction.electrode_lattice_extension import (
    add_lattice_extension,
    enumerate_lattice_extension_candidates,
)
from moltage.junction.placement_defaults import (
    load_default_au_placement_defaults,
)
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.connectivity import (
    DEFAULT_CONNECTIVITY_MULTIPLIER,
    infer_connectivity,
)
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.geometry_loader import load_geometry
from moltage.structure.vdw_radii import load_default_vdw_radii
from moltage.structure.xyz import read_xyz


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_XYZ = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


def _contact_state():
    source, connectivity, applied, occupied = _applied_contact_state()
    return source, connectivity, applied.structure, applied.connectivity, occupied


def _applied_contact_state():
    source = read_xyz(REFERENCE_XYZ)
    connectivity = infer_connectivity(source, load_default_covalent_radii())
    anchors = detect_anchors(source, connectivity)
    defaults = load_default_au_placement_defaults()
    proposals = propose_au_placements(
        source,
        connectivity,
        tuple(
            AuPlacementParameters(
                anchor,
                defaults[anchor.kind].distance_angstrom,
                defaults[anchor.kind].angle_degrees,
            )
            for anchor in anchors
        ),
        vdw_radii=load_default_vdw_radii(),
    )
    applied = apply_au_placements(source, connectivity, proposals)
    occupied = detect_anchors(applied.structure, applied.connectivity)
    return source, connectivity, applied, occupied


class ImportedStartTests(unittest.TestCase):
    def test_viewer_applied_two_contacts_are_valid_step2_input(self) -> None:
        _, _, applied, anchors = _applied_contact_state()

        context = applied_contact_au_context(
            applied.structure,
            applied.connectivity,
            anchors,
            applied,
        )

        self.assertEqual(
            frozenset(site.contact_au_index for site in context.contact_sites),
            frozenset(applied.added_au_indices),
        )

        edited_atoms = list(applied.structure.atoms)
        edited_atom = edited_atoms[0]
        edited_atoms[0] = Atom(
            edited_atom.index,
            edited_atom.element,
            edited_atom.x + 0.1,
            edited_atom.y,
            edited_atom.z,
        )
        edited = MolecularStructure(tuple(edited_atoms))
        edited_context = applied_contact_au_context(
            edited,
            applied.connectivity,
            anchors,
            applied,
        )
        self.assertIs(edited_context.structure, edited)

        changed_topology = Connectivity(
            len(applied.structure),
            applied.connectivity.bonds[:-1],
        )
        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "topology",
        ):
            applied_contact_au_context(
                applied.structure,
                changed_topology,
                anchors,
                applied,
            )

    def test_viewer_applied_single_contact_is_not_valid_step2_input(self) -> None:
        source = read_xyz(REFERENCE_XYZ)
        connectivity = infer_connectivity(source, load_default_covalent_radii())
        anchor = detect_anchors(source, connectivity)[0]
        defaults = load_default_au_placement_defaults()[anchor.kind]
        proposal = propose_au_placements(
            source,
            connectivity,
            (
                AuPlacementParameters(
                    anchor,
                    defaults.distance_angstrom,
                    defaults.angle_degrees,
                ),
            ),
            vdw_radii=load_default_vdw_radii(),
        )
        applied = apply_au_placements(source, connectivity, proposal)

        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "exactly one attached contact Au",
        ):
            applied_contact_au_context(
                applied.structure,
                applied.connectivity,
                detect_anchors(applied.structure, applied.connectivity),
                applied,
            )

    def test_exact_two_recognized_contact_sites_are_required(self) -> None:
        source, source_connectivity, structure, connectivity, anchors = _contact_state()

        context = imported_contact_au_context(structure, connectivity, anchors)

        self.assertEqual(len(context.anchors), 2)
        self.assertEqual(
            tuple(site.contact_au_index for site in context.contact_sites),
            (16, 17),
        )
        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "exactly one attached contact Au",
        ):
            imported_contact_au_context(
                source,
                source_connectivity,
                detect_anchors(source, source_connectivity),
            )

        missing_contact = AnchorCandidate(
            anchors[1].kind,
            anchors[1].binding_atom_index,
            anchors[1].atom_indices,
            (),
        )
        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "exactly one attached contact Au",
        ):
            imported_contact_au_context(
                structure,
                connectivity,
                (anchors[0], missing_contact),
            )

    def test_extra_unrelated_au_and_full_clusters_are_ineligible(self) -> None:
        _, _, structure, connectivity, anchors = _contact_state()
        extra = MolecularStructure(
            (*structure.atoms, Atom(len(structure), "Au", 100.0, 0.0, 0.0))
        )
        extra_connectivity = Connectivity(len(extra), connectivity.bonds)
        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "only the two recognized contact Au",
        ):
            imported_contact_au_context(extra, extra_connectivity, anchors)

        contact_context = imported_contact_au_context(
            structure,
            connectivity,
            anchors,
        )
        proposal = propose_electrode_placement(
            structure,
            connectivity,
            contact_context.contact_sites,
            contact_context.contact_sites,
        )
        applied = apply_electrode_placement(structure, connectivity, proposal)
        full_anchors = detect_anchors(applied.structure, applied.connectivity)
        with self.assertRaises(ImportedContactAuEligibilityError):
            imported_contact_au_context(
                applied.structure,
                applied.connectivity,
                full_anchors,
            )

    def test_ambiguous_multiple_contacts_on_one_terminus_are_ineligible(self) -> None:
        _, _, structure, connectivity, anchors = _contact_state()
        ambiguous = AnchorCandidate(
            anchors[0].kind,
            anchors[0].binding_atom_index,
            anchors[0].atom_indices,
            (anchors[0].attached_au_indices[0], anchors[1].attached_au_indices[0]),
        )

        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "exactly one attached contact Au",
        ):
            imported_contact_au_context(
                structure,
                connectivity,
                (ambiguous, anchors[1]),
            )

    def test_transport_context_preserves_exact_phase2d_mapping(self) -> None:
        _, _, structure, connectivity, anchors = _contact_state()
        contact_context = imported_contact_au_context(
            structure,
            connectivity,
            anchors,
        )
        proposal = propose_electrode_placement(
            structure,
            connectivity,
            contact_context.contact_sites,
            contact_context.contact_sites,
        )
        applied = apply_electrode_placement(structure, connectivity, proposal)

        context = ImportedTransportStartContext(contact_context, applied)
        provenance = context.electrode_provenance

        self.assertIs(context.working_structure, applied.structure)
        self.assertEqual(tuple(item.side for item in provenance), ("LEFT", "RIGHT"))
        self.assertTrue(
            all(item.geometry_model == "MoltageAuPyramidV1" for item in provenance)
        )
        self.assertTrue(all(item.pyramid_layers == 6 for item in provenance))
        self.assertEqual(
            tuple(item.contact_au_index for item in provenance),
            tuple(cluster.apex_atom_index for cluster in proposal.clusters),
        )
        self.assertEqual(
            tuple(item.roll_degrees for item in provenance),
            tuple(cluster.roll_degrees for cluster in proposal.clusters),
        )
        self.assertEqual(
            tuple(item.local_to_global_indices for item in provenance),
            tuple(cluster.local_to_global_indices for cluster in proposal.clusters),
        )
        self.assertEqual(len(applied.structure), len(structure) + 110)

    def test_direct_step3_preserves_extended_electrode_provenance(self) -> None:
        _, _, structure, connectivity, anchors = _contact_state()
        contact_context = imported_contact_au_context(
            structure,
            connectivity,
            anchors,
        )
        proposal = propose_electrode_placement(
            structure,
            connectivity,
            contact_context.contact_sites,
            contact_context.contact_sites,
        )
        applied = apply_electrode_placement(structure, connectivity, proposal)
        for side, layer_index in (("LEFT", 0), ("RIGHT", 5)):
            candidate = next(
                item
                for item in enumerate_lattice_extension_candidates(applied)
                if item.side == side and item.layer_index == layer_index
            )
            applied = add_lattice_extension(applied, candidate).applied

        context = ImportedTransportStartContext(contact_context, applied)

        self.assertEqual(
            tuple(
                len(record.lattice_extensions)
                for record in context.electrode_provenance
            ),
            (1, 1),
        )
        self.assertEqual(context.working_structure, applied.structure)

    def test_transport_context_separates_coordinate_edits_from_provenance(
        self,
    ) -> None:
        _, _, structure, connectivity, anchors = _contact_state()
        contact_context = imported_contact_au_context(
            structure,
            connectivity,
            anchors,
        )
        proposal = propose_electrode_placement(
            structure,
            connectivity,
            contact_context.contact_sites,
            contact_context.contact_sites,
        )
        applied = apply_electrode_placement(structure, connectivity, proposal)
        edited_atoms = list(applied.structure.atoms)
        atom = edited_atoms[-1]
        edited_atoms[-1] = Atom(
            atom.index,
            atom.element,
            atom.x + 0.125,
            atom.y,
            atom.z,
        )
        edited = MolecularStructure(tuple(edited_atoms))

        context = ImportedTransportStartContext(
            contact_context,
            applied,
            edited,
        )

        self.assertIs(context.working_structure, edited)
        self.assertIs(context.applied_electrodes, applied)
        self.assertEqual(
            context.electrode_provenance,
            ImportedTransportStartContext(contact_context, applied).electrode_provenance,
        )

        incompatible_atoms = list(edited.atoms)
        incompatible = incompatible_atoms[-1]
        incompatible_atoms[-1] = Atom(
            incompatible.index,
            "Ag",
            incompatible.x,
            incompatible.y,
            incompatible.z,
        )
        with self.assertRaisesRegex(
            ImportedContactAuEligibilityError,
            "coordinates only",
        ):
            ImportedTransportStartContext(
                contact_context,
                applied,
                MolecularStructure(tuple(incompatible_atoms)),
            )

    def test_xyz_and_mol_bond_orders_have_identical_eligibility(self) -> None:
        _, _, structure, connectivity, _ = _contact_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xyz_path = root / "contacts.xyz"
            mol_path = root / "contacts.mol"
            xyz_path.write_text(_xyz_text(structure), encoding="utf-8")
            mol_path.write_text(
                _mol_text(structure, connectivity),
                encoding="utf-8",
            )
            radii = load_default_covalent_radii()
            xyz = load_geometry(
                xyz_path,
                radii,
                multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
            )
            mol = load_geometry(
                mol_path,
                radii,
                multiplier=DEFAULT_CONNECTIVITY_MULTIPLIER,
            )

        xyz_context = imported_contact_au_context(
            xyz.structure,
            xyz.connectivity,
            detect_anchors(xyz.structure, xyz.connectivity),
        )
        mol_context = imported_contact_au_context(
            mol.structure,
            mol.connectivity,
            detect_anchors(mol.structure, mol.connectivity),
        )
        self.assertEqual(
            tuple(site.contact_au_index for site in xyz_context.contact_sites),
            tuple(site.contact_au_index for site in mol_context.contact_sites),
        )
        self.assertEqual(
            tuple((bond.first_index, bond.second_index) for bond in xyz.connectivity),
            tuple((bond.first_index, bond.second_index) for bond in mol.connectivity),
        )
        self.assertTrue(any(order.order > 1 for order in mol.bond_display_orders))


def _xyz_text(structure: MolecularStructure) -> str:
    rows = [str(len(structure)), "imported contact-Au parity fixture"]
    rows.extend(
        f"{atom.element} {atom.x:.8f} {atom.y:.8f} {atom.z:.8f}"
        for atom in structure
    )
    return "\n".join(rows) + "\n"


def _mol_text(structure: MolecularStructure, connectivity: Connectivity) -> str:
    rows = [
        "Imported contacts",
        "  Moltage",
        "",
        f"{len(structure):>3}{len(connectivity):>3}  0  0  0  0            999 V2000",
    ]
    rows.extend(
        f"{atom.x:10.4f}{atom.y:10.4f}{atom.z:10.4f} "
        f"{atom.element:<3} 0  0  0  0  0  0  0  0  0  0  0  0"
        for atom in structure
    )
    rows.extend(
        f"{bond.first_index + 1:>3}{bond.second_index + 1:>3}"
        f"{(2 if index % 2 == 0 else 3):>3}  0  0  0  0"
        for index, bond in enumerate(connectivity)
    )
    rows.append("M  END")
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    unittest.main()
