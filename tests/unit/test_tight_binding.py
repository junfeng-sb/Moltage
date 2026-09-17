from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from moltage.app.tight_binding import (
    TightBindingCalculator,
    TightBindingConfigurationError,
    build_hamiltonian,
    resolve_tight_binding_model,
)
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import Atom, MolecularStructure
from moltage.domain.tight_binding import TightBindingValidationError


class TightBindingCoreTests(TestCase):
    def setUp(self) -> None:
        self.structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "N", 1.2, 0.0, 0.0),
                Atom(2, "C", 2.4, 0.0, 0.0),
            )
        )
        self.connectivity = Connectivity(
            3,
            (
                Bond(0, 1, 1.2),
                Bond(1, 2, 1.2),
            ),
        )

    def _model(self, **changes):
        values = {
            "onsite_by_element": {"C": 0.0, "N": 0.2},
            "onsite_overrides": {},
            "hopping_by_element_pair": {("C", "N"): -1.0},
            "hopping_overrides": {},
            "left_contact_index": 0,
            "right_contact_index": 2,
            "gamma_left_ev": 0.6,
            "gamma_right_ev": 0.8,
            "eta_ev": 1.0e-6,
            "energy_start_ev": -2.0,
            "energy_end_ev": 2.0,
            "energy_step_ev": 0.1,
        }
        values.update(changes)
        return resolve_tight_binding_model(
            self.structure,
            self.connectivity,
            **values,
        )

    def test_type_defaults_and_exact_overrides_build_symmetric_hamiltonian(self):
        model = self._model(
            onsite_overrides={1: -0.4},
            hopping_overrides={(2, 1): 0.75},
        )

        matrix = build_hamiltonian(model)

        np.testing.assert_allclose(
            matrix,
            np.array(
                [
                    [0.0, -1.0, 0.0],
                    [-1.0, -0.4, 0.75],
                    [0.0, 0.75, 0.0],
                ]
            ),
        )

    def test_missing_values_are_reported_without_chemical_fallback(self):
        with self.assertRaisesRegex(
            TightBindingConfigurationError,
            "atom energies: 2 N",
        ):
            self._model(
                onsite_by_element={"C": 0.0},
                hopping_by_element_pair={},
                gamma_left_ev=None,
            )

    def test_contact_atoms_must_be_distinct(self):
        with self.assertRaisesRegex(
            TightBindingValidationError,
            "different atoms",
        ):
            self._model(right_contact_index=0)

    def test_start_inclusive_end_exclusive_grid(self):
        model = self._model(
            energy_start_ev=-1.0,
            energy_end_ev=1.0,
            energy_step_ev=0.5,
        )
        result = TightBindingCalculator().calculate(model)
        self.assertEqual(result.energies_ev, (-1.0, -0.5, 0.0, 0.5))

    def test_projected_green_function_matches_direct_matrix_inverse(self):
        model = self._model(
            onsite_overrides={0: -0.3, 2: 0.4},
            hopping_overrides={(0, 1): -0.9, (1, 2): 0.65},
            energy_start_ev=-1.5,
            energy_end_ev=1.5,
            energy_step_ev=0.15,
        )
        result = TightBindingCalculator().calculate(model)
        matrix = build_hamiltonian(model)
        expected = []
        sigma = np.zeros(matrix.shape, dtype=np.complex128)
        sigma[model.left_contact_index, model.left_contact_index] = (
            -0.5j * model.gamma_left_ev
        )
        sigma[model.right_contact_index, model.right_contact_index] = (
            -0.5j * model.gamma_right_ev
        )
        for energy in result.energies_ev:
            green = np.linalg.inv(
                (energy + 1j * model.eta_ev) * np.eye(model.atom_count)
                - matrix
                - sigma
            )
            expected.append(
                model.gamma_left_ev
                * model.gamma_right_ev
                * abs(
                    green[
                        model.left_contact_index,
                        model.right_contact_index,
                    ]
                )
                ** 2
            )
        np.testing.assert_allclose(result.transmissions, expected, rtol=1e-10)

    def test_eigenpairs_are_reused_when_only_lead_coupling_changes(self):
        calculator = TightBindingCalculator()
        model = self._model()
        with patch(
            "moltage.app.tight_binding.np.linalg.eigh",
            wraps=np.linalg.eigh,
        ) as eigh:
            calculator.calculate(model)
            calculator.calculate(replace(model, gamma_left_ev=0.9))
        self.assertEqual(eigh.call_count, 1)

