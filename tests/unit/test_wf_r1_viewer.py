from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.project_submission import NewProjectSubmissionRequest
from moltage.domain.calculation_project import ProjectStepKind
from moltage.gui.project_submission import NewProjectSelection
from phase2b1_test_support import profile
from test_imported_start import _contact_state, _mol_text, _xyz_text
from tools.molecule_viewer_demo import MoleculeViewerDemo
from moltage.visualization.view_preferences import ViewPreferences
from moltage.visualization.bond_torsion import (
    BondTorsionError,
    BondTorsionSession,
    structure_coordinates,
)


class WfR1ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        _, _, structure, connectivity, _ = _contact_state()
        self.xyz_path = root / "imported_contacts.xyz"
        self.mol_path = root / "imported_contacts.mol"
        self.xyz_path.write_text(_xyz_text(structure), encoding="utf-8")
        self.mol_path.write_text(
            _mol_text(structure, connectivity),
            encoding="utf-8",
        )
        self.ordinary_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "phase1b"
            / "synthetic_dual_ncs.xyz"
        )
        self.window = MoleculeViewerDemo()
        self.window.resize(900, 650)
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def test_xyz_mol_and_active_workspace_receive_exact_same_eligibility(self) -> None:
        xyz = self.window._open_local_geometry(self.xyz_path)
        self.assertTrue(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._electrode_section.isHidden())
        self.assertEqual(len(self.window._electrode_sites), 2)

        ordinary = self.window._open_local_geometry(self.ordinary_path)
        self.assertFalse(self.window._continue_step2_action.isEnabled())
        self.assertTrue(self.window._electrode_section.isHidden())

        mol = self.window._open_local_geometry(self.mol_path)
        self.assertTrue(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._electrode_section.isHidden())
        self.assertEqual(len(self.window._electrode_sites), 2)
        self.assertTrue(any(order.order > 1 for order in mol.bond_display_orders))

        self.window._focus_workspace(ordinary)
        self.assertFalse(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.window._focus_workspace(xyz)
        self.assertTrue(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())

    def test_extreme_view_preferences_do_not_change_imported_start_context(self) -> None:
        workspace = self.window._open_local_geometry(self.xyz_path)
        baseline_context = self.window._current_imported_contact_context()
        structure = workspace.structure
        connectivity = workspace.connectivity
        sites = self.window._electrode_sites

        self.window._apply_view_preferences(
            ViewPreferences(
                bond_thickness_scale=2.0,
                element_color_overrides={"Au": (1, 2, 3)},
                show_element_labels=True,
                hide_hydrogen=True,
            )
        )

        self.assertEqual(
            self.window._current_imported_contact_context(),
            baseline_context,
        )
        self.assertIs(workspace.structure, structure)
        self.assertIs(workspace.connectivity, connectivity)
        self.assertEqual(self.window._electrode_sites, sites)
        self.assertTrue(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())

    def test_two_contacts_added_in_viewer_enable_direct_step2_only(self) -> None:
        self.window._open_local_geometry(self.ordinary_path)
        self.assertFalse(self.window._continue_step2_action.isEnabled())

        available_controls = tuple(
            controls
            for controls in self.window._site_controls.values()
            if controls.checkbox.isEnabled()
        )
        self.assertEqual(len(available_controls), 2)
        available_controls[0].checkbox.setChecked(True)
        self.application.processEvents()
        self.assertEqual(len(self.window._current_proposals), 1)
        self.window._confirm_current_proposals()
        self.assertFalse(self.window._continue_step2_action.isEnabled())

        remaining_controls = tuple(
            controls
            for controls in self.window._site_controls.values()
            if controls.checkbox.isEnabled()
        )
        self.assertEqual(len(remaining_controls), 1)
        remaining_controls[0].checkbox.setChecked(True)
        self.application.processEvents()
        self.window._confirm_current_proposals()

        applied = self.window._applied_result
        self.assertIsNotNone(applied)
        self.assertEqual(len(applied.added_au_indices), 1)
        self.assertEqual(
            sum(atom.element == "Au" for atom in self.window._structure),
            2,
        )
        self.assertTrue(self.window._continue_step2_action.isEnabled())
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.assertTrue(self.window._electrode_section.isHidden())

        with patch.object(
            self.window,
            "_submit_new_optimization_project",
        ) as submit:
            self.window._continue_project_step2()
        submit.assert_called_once_with(
            fixed_starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        )

    def test_phase2d_apply_reuses_contacts_and_enables_only_active_direct_step3(self) -> None:
        imported = self.window._open_local_geometry(self.xyz_path)
        self.window._apply_view_preferences(
            ViewPreferences(
                bond_thickness_scale=0.5,
                element_color_overrides={"Au": (25, 50, 75)},
                show_element_labels=True,
                hide_hydrogen=True,
            )
        )
        source = imported.structure
        contact_indices = tuple(
            site.contact_au_index for site in self.window._electrode_sites
        )
        for checkbox in self.window._electrode_site_controls.values():
            checkbox.setChecked(True)
        self.application.processEvents()
        preview = self.window._electrode_current_proposal
        self.assertIsNotNone(preview)
        self.assertEqual(len(preview.clusters), 2)

        self.window._confirm_electrode_proposal()

        applied = self.window._applied_electrode_result
        self.assertIsNotNone(applied)
        self.assertEqual(applied.structure, preview.preview_structure)
        self.assertEqual(applied.structure.atoms[: len(source)], source.atoms)
        self.assertEqual(len(applied.added_au_indices), 110)
        self.assertEqual(
            tuple(cluster.apex_atom_index for cluster in preview.clusters),
            contact_indices,
        )
        self.assertTrue(self.window._continue_step3_action.isEnabled())
        self.assertEqual(
            self.window._current_imported_transport_context().applied_electrodes,
            applied,
        )

        ordinary = self.window._open_local_geometry(self.ordinary_path)
        self.assertFalse(self.window._continue_step3_action.isEnabled())
        self.window._focus_workspace(imported)
        self.assertTrue(self.window._continue_step3_action.isEnabled())

    def test_direct_step2_uses_fixed_normal_project_path_and_cancel_is_local(self) -> None:
        self.window._open_local_geometry(self.xyz_path)
        selected_profile = profile(save_password=False)
        selection = NewProjectSelection(
            selected_profile,
            "Imported",
            ProjectStepKind.MOLECULE_AU_OPT,
            "Imported.20300830",
        )
        dependencies = _dependencies(selected_profile)
        project_dialog = _accepted_dialog(selection)
        settings_dialog = _accepted_dialog(AimsOptimizationSettings())
        confirmation = _accepted_confirmation("temporary")

        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.NewCalculationProjectDialog",
            return_value=project_dialog,
        ) as dialog_type, patch(
            "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
            return_value=confirmation,
        ), patch.object(
            self.window,
            "_start_submission",
        ) as start_submission:
            self.window._continue_project_step2()

        request = start_submission.call_args.args[1]
        self.assertIsInstance(request, NewProjectSubmissionRequest)
        self.assertIs(request.starting_step, ProjectStepKind.MOLECULE_AU_OPT)
        self.assertIs(request.input_plan.structure, self.window._structure)
        self.assertIs(
            dialog_type.call_args.kwargs["fixed_starting_step"],
            ProjectStepKind.MOLECULE_AU_OPT,
        )

        confirmation.exec.return_value = QDialog.DialogCode.Rejected
        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.NewCalculationProjectDialog",
            return_value=project_dialog,
        ), patch(
            "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
            return_value=confirmation,
        ), patch.object(self.window, "_start_submission") as cancelled:
            self.window._continue_project_step2()
        cancelled.assert_not_called()

    def test_direct_step3_after_electrode_rotation_uses_exact_working_geometry(
        self,
    ) -> None:
        self.window._open_local_geometry(self.xyz_path)
        for checkbox in self.window._electrode_site_controls.values():
            checkbox.setChecked(True)
        self.application.processEvents()
        self.window._confirm_electrode_proposal()
        applied = self.window._applied_electrode_result
        original_geometry = render_geometry_in(applied.structure)
        selected_edge = None
        for bond in self.window._connectivity:
            edge = (bond.first_index, bond.second_index)
            try:
                session = BondTorsionSession(
                    self.window._structure,
                    self.window._connectivity,
                    *edge,
                )
                candidate = session.structure_at(37.0)
            except BondTorsionError:
                continue
            if structure_coordinates(candidate) != structure_coordinates(
                self.window._structure
            ):
                selected_edge = edge
                break
        self.assertIsNotNone(selected_edge)
        self.window._rotate_bond_button.click()
        self.window._bond_rotation_edge_picked(*selected_edge)
        self.window._torsion_numeric_input.setText("37")
        self.window._commit_torsion_numeric_edit()
        self.application.processEvents()
        rotated = self.window._structure
        rotated_geometry = render_geometry_in(rotated)
        self.assertNotEqual(rotated_geometry, original_geometry)
        self.assertIs(
            self.window._active_geometry_workspace().structure,
            rotated,
        )
        self.assertIs(
            self.window._current_imported_transport_context().working_structure,
            rotated,
        )
        selected_profile = profile(save_password=False)
        selection = NewProjectSelection(
            selected_profile,
            "Imported",
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            "Imported.20300830",
        )
        dependencies = _dependencies(selected_profile)
        project_dialog = _accepted_dialog(selection)
        settings = TransportConvergenceSettings()
        settings_dialog = _accepted_dialog(settings)
        confirmation = _accepted_confirmation("temporary")
        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.NewCalculationProjectDialog",
            return_value=project_dialog,
        ) as dialog_type, patch(
            "tools.molecule_viewer_demo.TransportConvergenceSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
            return_value=confirmation,
        ), patch.object(
            self.window,
            "_start_submission",
        ) as start_submission:
            self.window._continue_project_step3()

        request = start_submission.call_args.args[1]
        self.assertIs(request.starting_step, ProjectStepKind.TRANSPORT_CONVERGENCE)
        self.assertIs(request.input_plan.structure, rotated)
        self.assertNotEqual(render_geometry_in(request.input_plan.structure), original_geometry)
        self.assertIs(
            request.imported_transport_context.applied_electrodes,
            applied,
        )
        self.assertIs(
            request.imported_transport_context.working_structure,
            rotated,
        )
        self.assertIs(
            dialog_type.call_args.kwargs["fixed_starting_step"],
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )

        confirmation.exec.return_value = QDialog.DialogCode.Rejected
        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ), patch(
            "tools.molecule_viewer_demo.NewCalculationProjectDialog",
            return_value=project_dialog,
        ), patch(
            "tools.molecule_viewer_demo.TransportConvergenceSettingsDialog",
            return_value=settings_dialog,
        ), patch(
            "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
            return_value=confirmation,
        ), patch.object(self.window, "_start_submission") as cancelled:
            self.window._continue_project_step3()
        cancelled.assert_not_called()


def _dependencies(selected_profile):
    repository = MagicMock()
    repository.load.return_value = SimpleNamespace(
        profiles=(selected_profile,),
        last_selected_profile_id=selected_profile.profile_id,
    )
    return SimpleNamespace(
        profile_repository=repository,
        secret_store=MagicMock(get_password=MagicMock(return_value=None)),
    )


def _accepted_dialog(value):
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    if isinstance(value, NewProjectSelection):
        dialog.selected_project.return_value = value
    else:
        dialog.selected_settings.return_value = value
    return dialog


def _accepted_confirmation(password):
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.take_temporary_password.return_value = password
    return dialog


if __name__ == "__main__":
    unittest.main()
