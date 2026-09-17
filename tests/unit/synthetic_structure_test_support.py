"""Deterministic synthetic molecular and junction fixtures for offline tests."""

from functools import lru_cache
from pathlib import Path

from moltage.aims.geometry_writer import render_geometry_in
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.junction.apply_placement import apply_au_placements
from moltage.junction.au_placement import AuPlacementParameters, propose_au_placements
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    eligible_electrode_contact_sites,
    propose_electrode_placement,
)
from moltage.junction.electrode_lattice_extension import (
    add_lattice_extension,
    enumerate_lattice_extension_candidates,
)
from moltage.junction.placement_defaults import load_default_au_placement_defaults
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.vdw_radii import load_default_vdw_radii
from moltage.structure.xyz import read_xyz


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SYNTHETIC_MOLECULE = (
    _PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "phase1b"
    / "synthetic_dual_ncs.xyz"
)


@lru_cache(maxsize=1)
def synthetic_step2_state():
    """Return a synthetic molecule with exactly two attached contact Au atoms."""

    source = read_xyz(_SYNTHETIC_MOLECULE)
    connectivity = infer_connectivity(source, load_default_covalent_radii())
    anchors = detect_anchors(source, connectivity)
    defaults = load_default_au_placement_defaults()
    contact_proposals = propose_au_placements(
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
    applied = apply_au_placements(source, connectivity, contact_proposals)
    occupied_anchors = detect_anchors(applied.structure, applied.connectivity)
    sites = eligible_electrode_contact_sites(
        applied.structure,
        applied.connectivity,
        occupied_anchors,
    )
    return applied.structure, applied.connectivity, occupied_anchors, sites


@lru_cache(maxsize=1)
def synthetic_electrode_placement():
    """Construct the accepted synthetic two-electrode placement once."""

    structure, connectivity, _anchors, sites = synthetic_step2_state()
    proposal = propose_electrode_placement(
        structure,
        connectivity,
        sites,
        sites,
    )
    return apply_electrode_placement(structure, connectivity, proposal)


def synthetic_junction_structure():
    """Return the deterministic synthetic two-electrode junction structure."""

    return synthetic_electrode_placement().structure


def synthetic_junction_provenance():
    """Return schema-8 identity for the synthetic generated electrodes."""

    return provenance_from_applied_electrodes(synthetic_electrode_placement())


def synthetic_extended_electrode_placement(
    additions=(
        ("LEFT", 0),
        ("RIGHT", 5),
        ("LEFT", 0),
    ),
):
    """Return an extended junction using deterministic backend candidates."""

    applied = synthetic_electrode_placement()
    for side, layer_index in tuple(additions):
        candidate = next(
            item
            for item in enumerate_lattice_extension_candidates(applied)
            if item.side == side and item.layer_index == layer_index
        )
        applied = add_lattice_extension(applied, candidate).applied
    return applied


def synthetic_junction_geometry_bytes() -> bytes:
    """Render the synthetic junction using the production geometry grammar."""

    return render_geometry_in(synthetic_junction_structure()).encode("utf-8")
