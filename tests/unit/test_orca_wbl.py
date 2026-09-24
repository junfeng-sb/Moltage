from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.orca.catalog import OrcaBasis
from moltage.orca.wavefunction import (
    HARTREE_TO_EV,
    OrcaSpinOrbitals,
    OrcaWavefunctionError,
    parse_orca_wavefunction_json,
    render_orca_2json_configuration,
)
from moltage.orca.wbl import (
    OrcaWblContactSettings,
    OrcaWblError,
    OrcaWblSettings,
    WblContactSubspaceMode,
    WblLinkerKind,
    WblParameterStatus,
    WblSpinTreatment,
    calculate_orca_wbl,
    contact_element_for_linker,
    detect_wbl_contacts,
    manual_contact_direction,
    resolve_automatic_contact_subspace,
    resolve_contact_projector,
    resolve_contact_projection_geometry,
)
from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER
from moltage.orca.wbl_defaults import load_default_wbl_ui_defaults
from moltage.orca.wbl_artifacts import (
    OrcaWblArtifactError,
    parse_wbl_presentation,
    render_wbl_artifacts,
    wbl_presentation_from_result,
)


def _basis(shells):
    return [
        {"Shell": shell, "Coefficients": [1.0], "Exponents": [1.0]}
        for shell in shells
    ]


def _generic_wavefunction_json(atoms):
    """Build one minimal restricted synthetic ORCA document for projector tests."""

    ao_count = sum(
        sum(1 if shell == "s" else 3 for shell in shells)
        for _element, _coords, shells in atoms
    )
    coefficients = [0.0] * ao_count
    coefficients[0] = 1.0
    return json.dumps(
        {
            "Molecule": {
                "Atoms": [
                    {
                        "Idx": index,
                        "ElementLabel": element,
                        "Coords": list(coords),
                        "Basis": _basis(shells),
                    }
                    for index, (element, coords, shells) in enumerate(atoms)
                ],
                "CoordinateUnits": "Angs",
                "Charge": 0,
                "Multiplicity": 1,
                "HFTyp": "RHF",
                "MolecularOrbitals": {
                    "EnergyUnit": "Eh",
                    "MOs": [
                        {
                            "MOCoefficients": coefficients,
                            "Occupancy": 2.0,
                            "OrbitalEnergy": -0.1,
                        }
                    ],
                },
                "S-Matrix": np.eye(ao_count).tolist(),
            }
        }
    )


def _star_connectivity(atom_count):
    return Connectivity(
        atom_count,
        tuple(Bond(0, index, 1.0) for index in range(1, atom_count)),
    )


def _sh_wavefunction_json(*, unrestricted=False):
    atoms = [
        ("H", (-2.0, 0.0, 0.0), ["s"]),
        ("S", (-1.0, 0.0, 0.0), ["s"] * 4 + ["p"] * 3),
        ("C", (0.0, 0.0, 0.0), ["s"]),
        ("C", (1.0, 0.0, 0.0), ["s"]),
        ("S", (2.0, 0.0, 0.0), ["s"] * 4 + ["p"] * 3),
        ("H", (3.0, 0.0, 0.0), ["s"]),
    ]
    ao_count = sum(sum(1 if shell == "s" else 3 for shell in shells) for _, _, shells in atoms)
    # Both synthetic orbitals have evidence at both contacts so every MO must
    # contribute to the final curve.
    first = np.zeros(ao_count)
    second = np.zeros(ao_count)
    first[8] = first[23] = 0.5
    second[11] = second[26] = 0.5
    mos = [
        {"MOCoefficients": first.tolist(), "Occupancy": 2.0, "OrbitalEnergy": -0.1},
        {"MOCoefficients": second.tolist(), "Occupancy": 0.0, "OrbitalEnergy": 0.1},
    ]
    if unrestricted:
        orbitals = {
            "Alpha": {"EnergyUnit": "Eh", "MOs": mos},
            "Beta": {
                "EnergyUnit": "Eh",
                "MOs": [dict(item, OrbitalEnergy=item["OrbitalEnergy"] + 0.02) for item in mos],
            },
        }
        hf_type = "UHF"
    else:
        orbitals = {"EnergyUnit": "Eh", "MOs": mos}
        hf_type = "RHF"
    return json.dumps(
        {
            "Molecule": {
                "Atoms": [
                    {
                        "Idx": index,
                        "ElementLabel": element,
                        "Coords": list(coords),
                        "Basis": _basis(shells),
                    }
                    for index, (element, coords, shells) in enumerate(atoms)
                ],
                "CoordinateUnits": "Angs",
                "Charge": 0,
                "Multiplicity": 1 if not unrestricted else 2,
                "HFTyp": hf_type,
                "MolecularOrbitals": orbitals,
                "S-Matrix": np.eye(ao_count).tolist(),
            },
            "ORCA Header": {"Version": "Program Version 6.1.0"},
        }
    )


def _structure_and_connectivity(wavefunction):
    structure = wavefunction.atoms
    return structure, Connectivity(
        len(structure),
        tuple(Bond(index, index + 1, 1.0) for index in range(len(structure) - 1)),
    )


def _settings():
    return OrcaWblSettings(
        OrcaWblContactSettings(
            1,
            WblLinkerKind.SH,
            0.4,
            WblParameterStatus.HYPOTHESIS,
        ),
        OrcaWblContactSettings(
            4,
            WblLinkerKind.SH,
            0.5,
            WblParameterStatus.CALIBRATED,
        ),
        0.0,
        -4.0,
        4.0,
        0.5,
        DEFAULT_CONNECTIVITY_MULTIPLIER,
    )


def test_orca_2json_configuration_requires_overlap_and_mos():
    config = json.loads(render_orca_2json_configuration())
    assert config["MOCoefficients"] is True
    assert config["Basisset"] is True
    assert config["1elIntegrals"] == ["S"]


def test_versioned_wbl_ui_defaults_are_explicit_hypothesis_inputs():
    defaults = load_default_wbl_ui_defaults()

    assert defaults.model_id == "MoltageOrcaWblUiDefaultsV1"
    assert defaults.au_fermi_energy_ev == -5.1
    assert defaults.energy_min_relative_ev == -5.0
    assert defaults.energy_max_relative_ev == 5.0
    assert defaults.energy_step_ev == 0.01
    assert defaults.classification == "HYPOTHESIS"
    assert "not a universal" in defaults.limitations


def test_wbl_contacts_reuse_shared_linker_detection():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)

    detected = detect_wbl_contacts(structure, connectivity)

    assert tuple((item.atom_index, item.linker) for item in detected) == (
        (1, WblLinkerKind.SH),
        (4, WblLinkerKind.SH),
    )


def test_wavefunction_reader_rejects_molden_equivalent_without_overlap():
    raw = json.loads(_sh_wavefunction_json())
    del raw["Molecule"]["S-Matrix"]
    with pytest.raises(OrcaWavefunctionError, match="S-Matrix"):
        parse_orca_wavefunction_json(json.dumps(raw))


def test_restricted_orbitals_are_explicitly_represented_for_both_spins():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    assert wavefunction.restricted is True
    assert wavefunction.alpha is wavefunction.beta
    assert wavefunction.alpha.coefficients.shape == (30, 2)


def test_unrestricted_alpha_and_beta_remain_separate():
    wavefunction = parse_orca_wavefunction_json(
        _sh_wavefunction_json(unrestricted=True)
    )
    assert wavefunction.restricted is False
    assert not np.array_equal(
        wavefunction.alpha.energies_hartree,
        wavefunction.beta.energies_hartree,
    )


def test_reviewed_s_projection_excludes_inner_p_shell():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    projector = resolve_contact_projector(
        wavefunction,
        structure,
        connectivity,
        _settings().left,
        basis=OrcaBasis.DEF2_SVP,
    )
    atom_p = [
        function
        for function in wavefunction.ao_functions
        if function.atom_index == 1 and function.shell == "p"
    ]
    excluded = {function.ao_index for function in atom_p if function.shell_ordinal == 0}
    assert excluded
    assert excluded.isdisjoint(projector.ao_indices)
    assert {function.shell_ordinal for function in atom_p if function.ao_index in projector.ao_indices} == {1, 2}


def test_sme_uses_the_outward_bisector_and_directional_valence_3p():
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(
            (
                ("S", (0.0, 0.0, 0.0), ["s"] * 4 + ["p"] * 3),
                ("C", (-1.0, 1.0, 0.0), ["s"]),
                ("C", (-1.0, -1.0, 0.0), ["s"]),
            )
        )
    )
    projector = resolve_contact_projector(
        wavefunction,
        wavefunction.atoms,
        _star_connectivity(3),
        OrcaWblContactSettings(0, WblLinkerKind.SME),
        basis=OrcaBasis.DEF2_SVP,
    )
    assert projector.resolved_mode is WblContactSubspaceMode.S_3P_DIRECTIONAL
    assert np.allclose(projector.direction, (1.0, 0.0, 0.0))
    assert len(projector.vectors) == 2


def test_pyridine_uses_the_outward_in_plane_lone_pair_direction():
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(
            (
                ("N", (0.0, 0.0, 0.0), ["s"] * 3 + ["p"] * 2),
                ("C", (-1.0, 1.0, 0.0), ["s"]),
                ("C", (-1.0, -1.0, 0.0), ["s"]),
            )
        )
    )
    projector = resolve_contact_projector(
        wavefunction,
        wavefunction.atoms,
        _star_connectivity(3),
        OrcaWblContactSettings(0, WblLinkerKind.PYRIDINE),
        basis=OrcaBasis.DEF2_SVP,
    )
    assert projector.resolved_mode is WblContactSubspaceMode.N_2S_2P_DIRECTIONAL
    assert np.allclose(projector.direction, (1.0, 0.0, 0.0))
    assert len(projector.vectors) == 4


def test_pyramidal_nh2_uses_the_opposite_neighbor_sum():
    root_three_over_two = float(np.sqrt(3.0) / 2.0)
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(
            (
                ("N", (0.0, 0.0, 0.0), ["s"] * 3 + ["p"] * 2),
                ("C", (1.0, 0.0, -0.5), ["s"]),
                ("H", (-0.5, root_three_over_two, -0.5), ["s"]),
                ("H", (-0.5, -root_three_over_two, -0.5), ["s"]),
            )
        )
    )
    projector = resolve_contact_projector(
        wavefunction,
        wavefunction.atoms,
        _star_connectivity(4),
        OrcaWblContactSettings(0, WblLinkerKind.NH2),
        basis=OrcaBasis.DEF2_SVP,
    )
    assert projector.resolved_mode is WblContactSubspaceMode.N_2S_2P_DIRECTIONAL
    assert np.allclose(projector.direction, (0.0, 0.0, 1.0))
    assert len(projector.vectors) == 4


def test_numerically_planar_nh2_reduces_to_the_directional_p_normal():
    root_three_over_two = float(np.sqrt(3.0) / 2.0)
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(
            (
                ("N", (0.0, 0.0, 0.0), ["s"] * 3 + ["p"] * 2),
                ("C", (1.0, 0.0, 0.0), ["s"]),
                ("H", (-0.5, root_three_over_two, 0.0), ["s"]),
                ("H", (-0.5, -root_three_over_two, 0.0), ["s"]),
            )
        )
    )
    projector = resolve_contact_projector(
        wavefunction,
        wavefunction.atoms,
        _star_connectivity(4),
        OrcaWblContactSettings(0, WblLinkerKind.NH2),
        basis=OrcaBasis.DEF2_SVP,
    )
    assert projector.resolved_mode is WblContactSubspaceMode.N_2P_NORMAL
    assert np.allclose(projector.direction, (0.0, 0.0, 1.0))
    assert len(projector.vectors) == 2


def test_restricted_singlet_uses_every_spatial_mo_once_and_emits_total_only():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        _settings(),
        basis=OrcaBasis.DEF2_SVP,
    )
    assert result.spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
    assert result.transmission_alpha == ()
    assert result.transmission_beta == ()
    assert len(result.total_contributions_at_fermi) == 2
    assert result.t_alpha_at_fermi is None
    assert result.t_beta_at_fermi is None
    assert result.t_total_at_fermi == pytest.approx(
        sum(
            item.transmission_at_fermi
            for item in result.total_contributions_at_fermi
        )
    )
    assert all(
        item.transmission_at_fermi <= 1.0 + 1.0e-12
        for item in result.total_contributions_at_fermi
    )
    artifacts, hashes = render_wbl_artifacts(
        result,
        source_hashes={"orca_opt.gbw": "a" * 64, "orca_opt.json": "b" * 64},
        tool_evidence={"orca_2json_path": "/opt/orca/orca_2json"},
    )
    assert set(artifacts) == {
        "orca_wbl_transmission.csv",
        "orca_wbl_result.json",
        "orca_wbl_transmission.svg",
    }
    assert all(len(value) == 64 for value in hashes.values())
    result_json = json.loads(artifacts["orca_wbl_result.json"])
    assert result_json["schema"] == "moltage.orca-wbl-result.v3"
    assert result_json["model"]["classification"] == "HYPOTHESIS"
    assert (
        result_json["model"]["spin_treatment"]
        == "CLOSED_SHELL_SPIN_DEGENERATE"
    )
    assert (
        result_json["model"]["transmission_convention"]
        == "G_OVER_G0_WITH_G0_2E2_OVER_H"
    )
    assert "t_alpha_at_fermi" not in result_json["summary"]
    assert artifacts["orca_wbl_transmission.csv"].splitlines()[0] == (
        b"energy_relative_ev,energy_absolute_ev,transmission_total"
    )
    svg_text = artifacts["orca_wbl_transmission.svg"].decode("utf-8")
    assert "10⁻" in svg_text
    assert "1E-" not in svg_text
    assert '<tspan font-size="12" baseline-shift="sub">F</tspan>' in svg_text
    assert "E_F" not in svg_text
    assert "not explicit Au-molecule-Au DFT-NEGF" in result_json["model"]["description"]
    presentation = parse_wbl_presentation(
        artifacts["orca_wbl_result.json"],
        artifacts["orca_wbl_transmission.csv"],
    )
    assert presentation.transmission_alpha == result.transmission_alpha
    assert presentation.transmission_beta == result.transmission_beta
    assert presentation.transmission_total == result.transmission_total
    assert presentation.spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
    assert presentation.top_total == tuple(
        (item.mo_number, item.transmission_at_fermi) for item in result.top_total
    )


def test_open_shell_keeps_alpha_beta_and_raw_spin_sum():
    wavefunction = parse_orca_wavefunction_json(
        _sh_wavefunction_json(unrestricted=True)
    )
    structure, connectivity = _structure_and_connectivity(wavefunction)
    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        _settings(),
        basis=OrcaBasis.DEF2_SVP,
    )
    assert result.spin_treatment is WblSpinTreatment.SPIN_RESOLVED
    assert len(result.alpha_contributions_at_fermi) == 2
    assert len(result.beta_contributions_at_fermi) == 2
    assert result.total_contributions_at_fermi == ()
    assert np.allclose(
        result.transmission_total,
        np.asarray(result.transmission_alpha) + np.asarray(result.transmission_beta),
    )


def test_unrestricted_singlet_keeps_both_spin_channels_instead_of_assuming_pairing():
    document = json.loads(_sh_wavefunction_json(unrestricted=True))
    document["Molecule"]["Multiplicity"] = 1
    wavefunction = parse_orca_wavefunction_json(json.dumps(document))
    structure, connectivity = _structure_and_connectivity(wavefunction)

    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        _settings(),
        basis=OrcaBasis.DEF2_SVP,
    )

    assert result.spin_treatment is WblSpinTreatment.SPIN_RESOLVED
    assert len(result.alpha_contributions_at_fermi) == 2
    assert len(result.beta_contributions_at_fermi) == 2
    # Beta keeps its own orbitals rather than being replaced by alpha.
    assert [
        item.orbital_energy_ev for item in result.beta_contributions_at_fermi
    ] != [item.orbital_energy_ev for item in result.alpha_contributions_at_fermi]
    assert result.total_contributions_at_fermi == ()
    np.testing.assert_allclose(
        result.transmission_total,
        np.asarray(result.transmission_alpha) + np.asarray(result.transmission_beta),
    )


def test_wbl_presentation_rejects_a_total_curve_mismatch():
    wavefunction = parse_orca_wavefunction_json(
        _sh_wavefunction_json(unrestricted=True)
    )
    structure, connectivity = _structure_and_connectivity(wavefunction)
    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        _settings(),
        basis=OrcaBasis.DEF2_SVP,
    )
    artifacts, _ = render_wbl_artifacts(
        result,
        source_hashes={"orca_opt.gbw": "a" * 64},
        tool_evidence={"orca_2json_path": "/opt/orca/orca_2json"},
    )
    csv_text = artifacts["orca_wbl_transmission.csv"].decode("ascii")
    rows = csv_text.splitlines()
    fields = rows[1].split(",")
    fields[-1] = "99"
    rows[1] = ",".join(fields)
    with pytest.raises(OrcaWblArtifactError, match="alpha plus beta"):
        parse_wbl_presentation(
            artifacts["orca_wbl_result.json"],
            ("\n".join(rows) + "\n").encode("ascii"),
        )


def test_v1_spin_resolved_artifacts_remain_readable():
    wavefunction = parse_orca_wavefunction_json(
        _sh_wavefunction_json(unrestricted=True)
    )
    structure, connectivity = _structure_and_connectivity(wavefunction)
    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        _settings(),
        basis=OrcaBasis.DEF2_SVP,
    )
    artifacts, _ = render_wbl_artifacts(
        result,
        source_hashes={"orca_opt.gbw": "a" * 64},
        tool_evidence={"orca_2json_path": "/opt/orca/orca_2json"},
    )
    legacy = json.loads(artifacts["orca_wbl_result.json"])
    legacy["schema"] = "moltage.orca-wbl-result.v1"
    legacy["model"].pop("spin_treatment")

    presentation = parse_wbl_presentation(
        (json.dumps(legacy) + "\n").encode("ascii"),
        artifacts["orca_wbl_transmission.csv"],
    )

    assert presentation.spin_treatment is WblSpinTreatment.SPIN_RESOLVED
    assert presentation.transmission_alpha == result.transmission_alpha


def test_missing_gamma_remains_data_needed_and_cannot_run():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    incomplete = OrcaWblSettings(
        OrcaWblContactSettings(1, WblLinkerKind.SH),
        _settings().right,
        0.0,
        -1.0,
        1.0,
        0.1,
        DEFAULT_CONNECTIVITY_MULTIPLIER,
    )
    with pytest.raises(OrcaWblError, match="DATA_NEEDED"):
        calculate_orca_wbl(
            wavefunction,
            structure,
            connectivity,
            incomplete,
            basis=OrcaBasis.DEF2_SVP,
        )


def test_composite_or_unknown_basis_requires_manual_subspace():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    with pytest.raises(OrcaWblError, match="manual AO"):
        resolve_contact_projector(
            wavefunction,
            structure,
            connectivity,
            _settings().left,
            basis=None,
        )


def test_manual_ao_mode_is_explicit_and_atom_scoped():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    contact = OrcaWblContactSettings(
        1,
        WblLinkerKind.SH,
        0.2,
        WblParameterStatus.HYPOTHESIS,
        WblContactSubspaceMode.MANUAL_AO,
        manual_ao_indices=(8, 9),
    )
    projector = resolve_contact_projector(
        wavefunction, structure, connectivity, contact, basis=None
    )
    assert projector.ao_indices == (8, 9)
    assert projector.basis_mapping == "USER_EXPLICIT_AO_INDICES"


def _connectivity_without_contact_hydrogens(structure):
    """Connectivity a too-small bond threshold factor produces for the dithiol."""

    return Connectivity(
        len(structure),
        tuple(
            Bond(index, index + 1, 1.0)
            for index in range(1, len(structure) - 2)
        ),
    )


def test_unresolvable_bonds_still_block_the_automatic_contact_projection():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure = wavefunction.atoms
    connectivity = _connectivity_without_contact_hydrogens(structure)

    with pytest.raises(OrcaWblError, match="one S-H bond"):
        resolve_contact_projector(
            wavefunction,
            structure,
            connectivity,
            _settings().left,
            basis=OrcaBasis.DEF2_SVP,
        )


def test_explicit_projection_and_direction_run_without_detectable_bonds():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure = wavefunction.atoms
    connectivity = _connectivity_without_contact_hydrogens(structure)
    contact = OrcaWblContactSettings(
        1,
        WblLinkerKind.SH,
        0.4,
        WblParameterStatus.HYPOTHESIS,
        WblContactSubspaceMode.S_3P_DIRECTIONAL,
        manual_direction=(-1.0, 0.0, 0.0),
    )

    projector = resolve_contact_projector(
        wavefunction,
        structure,
        connectivity,
        contact,
        basis=OrcaBasis.DEF2_SVP,
    )

    assert projector.resolved_mode is WblContactSubspaceMode.S_3P_DIRECTIONAL
    assert projector.direction == (-1.0, 0.0, 0.0)
    assert projector.ao_indices


def test_manual_contact_direction_follows_the_molecular_geometry():
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure = wavefunction.atoms

    toward = manual_contact_direction(structure, 1, 0, pointing_away=False)
    away = manual_contact_direction(structure, 1, 0, pointing_away=True)

    assert toward == (-1.0, 0.0, 0.0)
    assert away == (1.0, 0.0, 0.0)
    with pytest.raises(OrcaWblError, match="two different atoms"):
        manual_contact_direction(structure, 1, 1, pointing_away=False)
    with pytest.raises(OrcaWblError, match="inside the structure"):
        manual_contact_direction(structure, 1, len(structure), pointing_away=False)


def test_bond_threshold_factor_is_required_and_recorded():
    with pytest.raises(OrcaWblError, match="bond threshold factor"):
        OrcaWblSettings(
            _settings().left,
            _settings().right,
            0.0,
            -4.0,
            4.0,
            0.5,
            0.0,
        )

    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    reviewed = replace(_settings(), connectivity_multiplier=1.35)
    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        reviewed,
        basis=OrcaBasis.DEF2_SVP,
    )
    artifacts, _hashes = render_wbl_artifacts(
        result,
        source_hashes={"orca_opt.gbw": "a" * 64},
        tool_evidence={"orca_2json_path": "/opt/orca/orca_2json"},
    )
    document = json.loads(artifacts["orca_wbl_result.json"])

    assert document["settings"]["connectivity_multiplier"] == 1.35


def _ncs_atoms(*, with_gold=False):
    """One synthetic R-N=C=S arm whose terminal sulfur binds the electrode."""

    atoms = [
        ("S", (0.0, 0.0, 0.0), ["s"] * 4 + ["p"] * 3),
        ("C", (1.6, 0.0, 0.0), ["s"]),
        ("N", (2.8, 0.0, 0.0), ["s"] * 3 + ["p"] * 3),
        ("C", (4.2, 0.0, 0.0), ["s"]),
    ]
    if with_gold:
        atoms.append(("Au", (-2.3, 0.0, 0.0), ["s"]))
    return atoms


def _ncs_connectivity(atom_count, *, with_gold=False):
    bonds = [Bond(0, 1, 1.6), Bond(1, 2, 1.2), Bond(2, 3, 1.4)]
    if with_gold:
        bonds.append(Bond(0, 4, 2.3))
    return Connectivity(atom_count, tuple(bonds))


def test_ncs_is_detected_and_resolves_to_legacy_all_p_without_direction():
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(_ncs_atoms())
    )
    structure = wavefunction.atoms
    connectivity = _ncs_connectivity(len(structure))

    detected = detect_wbl_contacts(structure, connectivity)
    mode, direction = resolve_automatic_contact_subspace(
        structure, connectivity, 0, WblLinkerKind.NCS
    )

    assert tuple((item.atom_index, item.linker) for item in detected) == (
        (0, WblLinkerKind.NCS),
    )
    assert contact_element_for_linker(WblLinkerKind.NCS) == "S"
    assert mode is WblContactSubspaceMode.S_ALL_P_LEGACY
    assert direction is None


def test_ncs_projector_uses_all_sulfur_p_shells_and_components():
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(_ncs_atoms())
    )
    structure = wavefunction.atoms
    connectivity = _ncs_connectivity(len(structure))
    contact = OrcaWblContactSettings(
        0,
        WblLinkerKind.NCS,
        0.3,
        WblParameterStatus.HYPOTHESIS,
    )

    projector = resolve_contact_projector(
        wavefunction,
        structure,
        connectivity,
        contact,
        basis=OrcaBasis.DEF2_SVP,
    )

    assert projector.resolved_mode is WblContactSubspaceMode.S_ALL_P_LEGACY
    assert projector.direction is None
    assert len(projector.ao_indices) == 9
    assert projector.vectors == tuple(((index, 1.0),) for index in projector.ao_indices)
    assert projector.ao_indices
    assert all(
        wavefunction.ao_functions[index].atom_index == 0
        for index in projector.ao_indices
    )
    assert all(
        wavefunction.ao_functions[index].shell == "p"
        for index in projector.ao_indices
    )


def test_ncs_all_p_is_direction_independent_and_rejects_a_nitrogen_contact():
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(_ncs_atoms(with_gold=True))
    )
    structure = wavefunction.atoms
    connectivity = _ncs_connectivity(len(structure), with_gold=True)

    mode, direction = resolve_automatic_contact_subspace(
        structure, connectivity, 0, WblLinkerKind.NCS
    )

    assert mode is WblContactSubspaceMode.S_ALL_P_LEGACY
    assert direction is None
    with pytest.raises(OrcaWblError, match="NCS contact atom must be S"):
        resolve_contact_projector(
            wavefunction,
            structure,
            connectivity,
            OrcaWblContactSettings(
                2,
                WblLinkerKind.NCS,
                0.3,
                WblParameterStatus.HYPOTHESIS,
            ),
            basis=OrcaBasis.DEF2_SVP,
        )


def test_ncs_legacy_needs_no_direction_but_explicit_directional_mode_still_does():
    wavefunction = parse_orca_wavefunction_json(
        _generic_wavefunction_json(_ncs_atoms())
    )
    structure = wavefunction.atoms
    isolated = Connectivity(len(structure), (Bond(1, 2, 1.2), Bond(2, 3, 1.4)))

    assert resolve_automatic_contact_subspace(structure, isolated, 0, WblLinkerKind.NCS) == (
        WblContactSubspaceMode.S_ALL_P_LEGACY, None,
    )
    contact = OrcaWblContactSettings(0, WblLinkerKind.NCS)
    with pytest.raises(OrcaWblError, match="one terminal S-C bond"):
        resolve_contact_projector(
            wavefunction, structure, isolated,
            replace(contact, subspace_mode=WblContactSubspaceMode.S_3P_DIRECTIONAL),
            basis=OrcaBasis.DEF2_SVP,
        )
    explicit = resolve_contact_projector(
        wavefunction, structure, isolated,
        replace(contact, subspace_mode=WblContactSubspaceMode.S_3P_DIRECTIONAL,
                manual_direction=(0, 1, 0)), basis=OrcaBasis.DEF2_SVP,
    )
    assert explicit.direction == (0, 1, 0)
    assert len(explicit.ao_indices) == 6
    # Old saved settings still load; a new calculation rejects the conflict.
    conflict = replace(contact, manual_direction=(1, 0, 0))
    with pytest.raises(OrcaWblError, match="select an explicit directional"):
        resolve_contact_projection_geometry(structure, isolated, conflict)


def test_ncs_all_p_matches_legacy_svd_for_complete_s_orthonormal_mos():
    """Compare the two routes where complete C.T @ S @ C = I holds.

    This does not assert equivalence for rounded/non-orthonormal coefficients;
    production keeps the existing overlap-based Löwdin implementation.
    """
    atoms = [
        ("S", (0, 0, 0), ["s"] + ["p"] * 5),
        ("C", (1.6, 0, 0), ["s"]),
        ("N", (2.8, 0, 0), ["s", "p"]),
        ("C", (4.2, 0, 0), ["s"]),
        ("C", (5.6, 0, 0), ["s"]),
        ("N", (7, 0, 0), ["s", "p"]),
        ("C", (8.2, 0, 0), ["s"]),
        ("S", (9.8, 0, 0), ["s"] + ["p"] * 5),
    ]
    wavefunction = parse_orca_wavefunction_json(_generic_wavefunction_json(atoms))
    n = wavefunction.ao_count
    rng = np.random.default_rng(17)
    transform = rng.normal(size=(n, n)) / n
    overlap = np.eye(n) + transform.T @ transform
    eigenvalues, eigenvectors = np.linalg.eigh(overlap)
    inverse_sqrt = (eigenvectors * eigenvalues**-0.5) @ eigenvectors.T
    qa, _ = np.linalg.qr(rng.normal(size=(n, n)))
    qb, _ = np.linalg.qr(rng.normal(size=(n, n)))
    ca, cb = inverse_sqrt @ qa, inverse_sqrt @ qb
    eps_a = np.linspace(-8, 3, n)
    eps_b = eps_a + 0.13
    wavefunction = replace(
        wavefunction, overlap=overlap, restricted=False, multiplicity=2,
        alpha=OrcaSpinOrbitals(eps_a / HARTREE_TO_EV, np.zeros(n), ca),
        beta=OrcaSpinOrbitals(eps_b / HARTREE_TO_EV, np.zeros(n), cb),
    )
    contacts = [OrcaWblContactSettings(i, WblLinkerKind.NCS, gamma, WblParameterStatus.HYPOTHESIS)
                for i, gamma in ((0, 0.4), (7, 0.5))]
    settings = replace(_settings(), left=contacts[0], right=contacts[1], fermi_energy_ev=-5.1)
    result = calculate_orca_wbl(
        wavefunction, wavefunction.atoms, Connectivity(len(atoms), ()),
        settings, basis=None,
    )
    u, singular, vt = np.linalg.svd(ca, full_matrices=False)
    legacy = (u @ vt, (u * (1 / singular)) @ u.T @ cb)
    p_rows = [[f.ao_index for f in wavefunction.ao_functions if f.atom_index == i and f.shell == "p"]
              for i in (0, 7)]
    assert tuple(map(len, p_rows)) == (15, 15)
    for lowdin, eps, actual, contributions in zip(
        legacy, (eps_a, eps_b), (result.transmission_alpha, result.transmission_beta),
        (result.alpha_contributions_at_fermi, result.beta_contributions_at_fermi), strict=True,
    ):
        wl, wr = (np.sum(lowdin[rows] ** 2, axis=0) for rows in p_rows)
        np.testing.assert_allclose([c.left_weight for c in contributions], wl, atol=1e-13)
        np.testing.assert_allclose([c.right_weight for c in contributions], wr, atol=1e-13)
        gl, gr = 0.4 * wl, 0.5 * wr
        expected = np.sum(
            gl * gr / ((np.asarray(result.energy_absolute_ev)[:, None] - eps) ** 2 + ((gl + gr) / 2) ** 2), axis=1,
        )
        np.testing.assert_allclose(actual, expected, rtol=1e-11, atol=1e-13)
    artifacts, _ = render_wbl_artifacts(result, source_hashes={"orca_opt.gbw": "a" * 64}, tool_evidence={})
    document = json.loads(artifacts["orca_wbl_result.json"])
    assert document["contacts"]["left"]["resolved_mode"] == "S_ALL_P_LEGACY"
    assert document["contacts"]["left"]["basis_mapping"] == "LEGACY_SELECTED_S_ALL_P_NO_RADIAL_OR_DIRECTION_FILTER"
    assert document["contacts"]["left"]["direction"] is None
    restored = parse_wbl_presentation(artifacts["orca_wbl_result.json"], artifacts["orca_wbl_transmission.csv"])
    assert restored.report == wbl_presentation_from_result(result, source_hashes={}).report
    assert restored.report.orbital_counts == (("alpha", n), ("beta", n))


@pytest.mark.parametrize("missing", ["empty", "component"])
def test_ncs_legacy_rejects_incomplete_p_ao_evidence(missing):
    wavefunction = parse_orca_wavefunction_json(_generic_wavefunction_json(_ncs_atoms()))
    functions = tuple(
        f for f in wavefunction.ao_functions
        if not (f.atom_index == 0 and f.shell == "p" and (missing == "empty" or f.ao_index == 4))
    )
    wavefunction = replace(wavefunction, ao_functions=functions)
    with pytest.raises(OrcaWblError, match="p AO evidence|component evidence"):
        resolve_contact_projector(
            wavefunction, wavefunction.atoms, _ncs_connectivity(len(wavefunction.atoms)),
            OrcaWblContactSettings(0, WblLinkerKind.NCS), basis=None,
        )


@pytest.mark.parametrize("missing_section", ["settings", "contacts", "orbital_contributions_at_fermi"])
def test_current_report_rejects_inconsistent_or_missing_evidence(missing_section):
    wavefunction = parse_orca_wavefunction_json(_sh_wavefunction_json())
    structure, connectivity = _structure_and_connectivity(wavefunction)
    result = calculate_orca_wbl(wavefunction, structure, connectivity, _settings(), basis=OrcaBasis.DEF2_SVP)
    artifacts, _ = render_wbl_artifacts(result, source_hashes={}, tool_evidence={})
    document = json.loads(artifacts["orca_wbl_result.json"])
    document["settings"]["fermi_energy_ev"] += 0.5
    with pytest.raises(OrcaWblArtifactError, match="Fermi energy"):
        parse_wbl_presentation(json.dumps(document).encode(), artifacts["orca_wbl_transmission.csv"])
    document = json.loads(artifacts["orca_wbl_result.json"])
    document["summary"]["top_total"][0]["orbital_energy_ev"] += 1.0
    with pytest.raises(OrcaWblArtifactError, match="leading MO"):
        parse_wbl_presentation(json.dumps(document).encode(), artifacts["orca_wbl_transmission.csv"])
    document = json.loads(artifacts["orca_wbl_result.json"])
    del document[missing_section]
    with pytest.raises(OrcaWblArtifactError, match="report sections missing"):
        parse_wbl_presentation(json.dumps(document).encode(), artifacts["orca_wbl_transmission.csv"])


@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_prechange_serializer_fixture_remains_readable_with_optional_legacy_details(schema):
    folder = Path(__file__).parents[1] / "fixtures" / "orca_wbl_legacy_v2"
    raw = (folder / "synthetic.json").read_bytes()
    curve = (folder / "synthetic.csv").read_bytes()
    document = json.loads(raw)
    if schema == "v1":
        # Synthetic v1 compatibility variation, not a purported real capture.
        document["schema"] = "moltage.orca-wbl-result.v1"
        del document["model"]["spin_treatment"]
    result = parse_wbl_presentation(json.dumps(document).encode(), curve)
    assert result.report is not None
    assert result.report.gamma0_left_ev == 0.4
    assert result.report.orbital_counts == (("alpha", 2), ("beta", 2))
    # The frozen synthetic MOs each have 0.25 population at each contact.
    expected = sum(
        (0.4 * 0.25) * (0.5 * 0.25)
        / ((energy * HARTREE_TO_EV) ** 2 + ((0.4 * 0.25 + 0.5 * 0.25) / 2) ** 2)
        for energy in (-0.1, 0.1, -0.08, 0.12)
    )
    assert result.t_total_at_fermi == pytest.approx(expected)
    # Older accepted curve/summary evidence must not be rejected solely because
    # an optional annotation section is incomplete or uses a different shape.
    del document["contacts"]["left"]["resolved_mode"]
    partial = parse_wbl_presentation(json.dumps(document).encode(), curve)
    assert partial.transmission_total == result.transmission_total
    assert partial.report is None
    assert "Legacy report detail unavailable" in partial.report_unavailable_reason
    del document["settings"]
    minimal = parse_wbl_presentation(json.dumps(document).encode(), curve)
    assert minimal.transmission_total == result.transmission_total
    assert minimal.report is None
    assert "report sections missing: settings" in minimal.report_unavailable_reason


def test_legacy_population_keeps_tiny_positive_weights_and_saved_settings_load():
    from moltage.orca.wbl import _projector_weights
    from moltage.remote.project_manifest import _wbl_contact_from_dict, _wbl_contact_to_dict

    w = parse_orca_wavefunction_json(_generic_wavefunction_json(_ncs_atoms()))
    contact = OrcaWblContactSettings(0, WblLinkerKind.NCS, subspace_mode=WblContactSubspaceMode.S_ALL_P_LEGACY)
    p = resolve_contact_projector(w, w.atoms, _ncs_connectivity(len(w.atoms)), contact, basis=None)
    lowdin = np.zeros((w.ao_count, 1))
    lowdin[p.ao_indices[0], 0] = 1e-8
    assert _projector_weights(lowdin, p)[0] == pytest.approx(1e-16, rel=1e-12, abs=0)
    assert _wbl_contact_from_dict(_wbl_contact_to_dict(contact)) == contact
    old = replace(contact, subspace_mode=WblContactSubspaceMode.AUTO, manual_direction=(1, 0, 0))
    assert _wbl_contact_from_dict(_wbl_contact_to_dict(old)) == old


def test_report_svg_keeps_full_viewbox_at_small_requested_dimensions():
    from moltage.orca.wbl_artifacts import render_wbl_svg
    from xml.etree import ElementTree

    w = parse_orca_wavefunction_json(_flat_unrestricted_json((1, 1, 1, 0, 0), (1, 1, 0, 0, 0), 2))
    s, c = _structure_and_connectivity(w)
    result = calculate_orca_wbl(w, s, c, _settings(), basis=OrcaBasis.DEF2_SVP)
    svg = render_wbl_svg(result, width=600, height=420).decode()
    element = ElementTree.fromstring(svg)
    assert element.attrib["viewBox"] == "0 0 1200 840"
    assert element.attrib["width"] == "600" and element.attrib["height"] == "420"
    assert "#c51b29" in svg and "#2166ac" in svg
    assert "Model settings" in svg and "Largest contributions" in svg
    ns = {"svg": "http://www.w3.org/2000/svg"}
    paths = element.findall(".//svg:path", ns)
    assert len(paths) == 3 and all(path.attrib["d"].startswith("M ") for path in paths)
    markers = element.findall(".//svg:polygon", ns)
    assert markers
    for marker in markers:
        for point in marker.attrib["points"].split():
            x, y = map(float, point.split(","))
            assert 0 <= x <= 1200 and 0 <= y <= 840
    assert "× 10" in svg
    assert "Eꜰ" not in svg


def _flat_unrestricted_json(
    alpha_occupancies,
    beta_occupancies,
    multiplicity,
    *,
    beta_energy_shift=0.02,
    interleave=False,
):
    """ORCA 6.1 orca_2json UHF layout: one unlabelled MO list, alpha then beta."""

    document = json.loads(_sh_wavefunction_json())
    molecule = document["Molecule"]
    ao_count = len(molecule["S-Matrix"])

    def block(occupancies, shift):
        orbitals = []
        for index, occupancy in enumerate(occupancies):
            coefficients = [0.0] * ao_count
            # S p_x of the valence shells both SH contacts project onto.
            coefficients[9] = coefficients[24] = 0.5
            coefficients[index] += 0.1
            orbitals.append(
                {
                    "MOCoefficients": coefficients,
                    "Occupancy": float(occupancy),
                    "OrbitalEnergy": -0.3 + 0.1 * index + shift,
                    "OrbitalSymLabel": "A",
                    "OrbitalSymmetry": 0,
                }
            )
        return orbitals

    alpha = block(alpha_occupancies, 0.0)
    beta = block(beta_occupancies, beta_energy_shift)
    if interleave:
        orbitals = [item for pair in zip(alpha, beta) for item in pair]
    else:
        orbitals = alpha + beta
    molecule["HFTyp"] = "UHF"
    molecule["Multiplicity"] = multiplicity
    molecule["MolecularOrbitals"] = {
        "EnergyUnit": "Eh",
        "MOs": orbitals,
        "OrbitalLabels": ["0H   1s"] * len(orbitals),
    }
    return json.dumps(document)


def test_orca_6_unlabelled_unrestricted_orbitals_split_alpha_then_beta():
    wavefunction = parse_orca_wavefunction_json(
        _flat_unrestricted_json((1, 1, 1, 0), (1, 1, 0, 0), 2)
    )

    assert not wavefunction.restricted
    assert wavefunction.alpha.occupancies.tolist() == [1.0, 1.0, 1.0, 0.0]
    assert wavefunction.beta.occupancies.tolist() == [1.0, 1.0, 0.0, 0.0]
    np.testing.assert_allclose(
        wavefunction.beta.energies_hartree - wavefunction.alpha.energies_hartree,
        0.02,
    )


def test_unlabelled_unrestricted_orbitals_must_reproduce_the_multiplicity():
    # Interleaving would put the occupied orbitals of both spins in one half.
    with pytest.raises(OrcaWavefunctionError, match="hold 4 and 1 electrons"):
        parse_orca_wavefunction_json(
            _flat_unrestricted_json(
                (1, 1, 1, 0), (1, 1, 0, 0), 2, interleave=True
            )
        )
    odd = json.loads(_flat_unrestricted_json((1, 1, 1, 0), (1, 1, 0, 0), 2))
    odd["Molecule"]["MolecularOrbitals"]["MOs"].pop()
    with pytest.raises(OrcaWavefunctionError, match="odd number"):
        parse_orca_wavefunction_json(json.dumps(odd))
    doubled = json.loads(_flat_unrestricted_json((1, 1, 0, 0), (1, 1, 0, 0), 1))
    doubled["Molecule"]["MolecularOrbitals"]["MOs"][0]["Occupancy"] = 2.0
    with pytest.raises(OrcaWavefunctionError, match="between 0 and 1"):
        parse_orca_wavefunction_json(json.dumps(doubled))


def test_closed_shell_uks_singlet_reports_equal_alpha_and_beta_transmission():
    wavefunction = parse_orca_wavefunction_json(
        _flat_unrestricted_json(
            (1, 1, 0, 0), (1, 1, 0, 0), 1, beta_energy_shift=0.0
        )
    )
    structure, connectivity = _structure_and_connectivity(wavefunction)

    result = calculate_orca_wbl(
        wavefunction,
        structure,
        connectivity,
        _settings(),
        basis=OrcaBasis.DEF2_SVP,
    )

    assert result.spin_treatment is WblSpinTreatment.SPIN_RESOLVED
    assert max(result.transmission_alpha) > 0.0
    np.testing.assert_allclose(result.transmission_alpha, result.transmission_beta)
    np.testing.assert_allclose(
        result.transmission_total,
        2.0 * np.asarray(result.transmission_alpha),
    )
