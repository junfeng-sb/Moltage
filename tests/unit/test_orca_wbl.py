import json

import numpy as np
import pytest

from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.orca.catalog import OrcaBasis
from moltage.orca.wavefunction import (
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
    detect_wbl_contacts,
    resolve_contact_projector,
)
from moltage.orca.wbl_defaults import load_default_wbl_ui_defaults
from moltage.orca.wbl_artifacts import (
    OrcaWblArtifactError,
    parse_wbl_presentation,
    render_wbl_artifacts,
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
    assert result_json["schema"] == "moltage.orca-wbl-result.v2"
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


def test_unrestricted_singlet_is_not_silently_treated_as_fully_paired():
    document = json.loads(_sh_wavefunction_json(unrestricted=True))
    document["Molecule"]["Multiplicity"] = 1
    wavefunction = parse_orca_wavefunction_json(json.dumps(document))
    structure, connectivity = _structure_and_connectivity(wavefunction)
    with pytest.raises(OrcaWblError, match="unrestricted"):
        calculate_orca_wbl(
            wavefunction,
            structure,
            connectivity,
            _settings(),
            basis=OrcaBasis.DEF2_SVP,
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
