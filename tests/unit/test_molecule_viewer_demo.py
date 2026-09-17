import gc
from array import array
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QThreadPool, QTimer, Qt
from PySide6.QtTest import QTest
from qt_test_support import wait_until
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
)

from moltage.aims.geometry_writer import render_geometry_in, write_geometry_in
from moltage.aims.input_bundle import AimsOptimizationInputPlan
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.orbital_cube import FrontierOrbital
from moltage.app.connection_service import AuthenticationError
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.app.input_export import AimsInputExportService
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_submission import OrcaOptimizationSubmissionRequest
from moltage.app.project_geometry import (
    ProjectGeometryViewKind,
    ProjectGeometryViewResult,
)
from moltage.app.project_orbital_cube import (
    ProjectOrbitalCubeArtifact,
    ProjectOrbitalCubeBinding,
    ProjectOrbitalCubeLoadResult,
)
from moltage.app.project_planning import (
    StartStepAdvice,
    StartStepRecommendation,
    create_initial_project,
)
from moltage.app.project_recovery import (
    ProjectDiscoveryResult,
    ProjectRecoverySnapshot,
)
from moltage.app.project_submission import (
    ExistingProjectStepSubmissionRequest,
    NewProjectSubmissionRequest,
    ProjectSubmissionService,
    SbatchRejectedError,
)
from moltage.app.task_restart import ProjectTaskRestartDraft
from moltage.app.server_profiles import (
    ServerProfileRepository,
    ServerProfileService,
)
from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
)
from electrode_test_support import synthetic_project_electrode_provenance
from moltage.domain.structure import Atom, MolecularStructure
from moltage.domain.junction import AppliedAuPlacement
from moltage.junction.electrode_lattice_interaction import (
    LatticeExtensionAvailability,
    interaction_candidates_for_working_geometry,
)
from moltage.junction.apply_placement import AuPlacementApplicationError
from moltage.orca.project_evidence import OrcaOptimizationResultEvidence
from moltage.gui.project_submission import (
    NewCalculationProjectDialog,
    NewProjectSelection,
)
from moltage.gui.orca_dialogs import OrcaWblSettingsDialog
from moltage.gui.projects_dialog import CalculationProjectsDialog
from moltage.gui.workspace_tabs import (
    LocalGeometryWorkspaceIdentity,
    ManagedGeometryWorkspaceIdentity,
    TransmissionWorkspaceIdentity,
)
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.cube import CubeScalarField
from moltage.structure.vdw_radii import load_default_vdw_radii
from moltage.structure.xyz import read_xyz
from moltage.visualization.molecule_scene import (
    ELEMENT_COLORS_RGB,
    VisualizationError,
)
from moltage.remote.slurm_discovery import (
    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.molecule_viewer_demo import (
    AVAILABLE_AU_EXTENSION_GUIDE_COLOR_RGB,
    BLOCKED_AU_EXTENSION_COLOR_RGB,
    MoleculeViewerDemo,
    _ProjectSubmissionDependencies,
    _ViewerPickMode,
    _structure_retains_recovered_prefix,
)

from phase2b1_test_support import MemorySecretStore, profile
from species_test_support import synthetic_species_library
from test_project_submission import (
    Clock,
    FixedConnectionService,
    MemoryRemoteExecutor,
    PASSWORD,
    PROJECT_ID,
    _temporary_ids,
)
from synthetic_structure_test_support import (
    synthetic_electrode_placement,
    synthetic_step2_state,
)
from test_project_recovery import TEST_PROFILE, _project
from test_project_continuation import _successful_step1_project
from test_orca_submission_recovery import (
    NOW as ORCA_NOW,
    configured_profile as configured_orca_profile,
    settings as orca_settings,
    water as synthetic_orca_structure,
)


REFERENCE_XYZ_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "phase1b" / "synthetic_dual_ncs.xyz"
)


def _minimal_input_plan() -> AimsOptimizationInputPlan:
    return AimsOptimizationInputPlan(
        MolecularStructure((Atom(0, "H", 0.0, 0.0, 0.0),)),
        AimsOptimizationSettings(),
    )


class _RecordingThreadPool(QThreadPool):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.wait_for_done_calls = 0
        self.setMaxThreadCount(1)

    def waitForDone(self, *args, **kwargs):
        self.wait_for_done_calls += 1
        return super().waitForDone(*args, **kwargs)


class _BlockingRecoveryService:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.cancel_calls = 0

    def discover_and_refresh(
        self,
        profile,
        supplied_password=None,
        *,
        progress=None,
        stop_token=None,
    ):
        del profile, supplied_password, progress, stop_token
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return ProjectDiscoveryResult((), ())

    def cancel(self):
        self.cancel_calls += 1
        self.release.set()


class _BlockingSubmissionService(ProjectSubmissionService):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.cancel_calls = 0

    def create_and_submit_project(self, request, *, progress=None):
        del request, progress
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return None

    def cancel(self):
        self.cancel_calls += 1
        self.release.set()


class MoleculeViewerCompositionTests(unittest.TestCase):
    def test_density_service_uses_per_user_cache_without_touching_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            appdata = base / "appdata"
            working_directory = base / "working"
            working_directory.mkdir()
            connection_service = object()
            dependencies = SimpleNamespace(connection_service=connection_service)
            original_directory = Path.cwd()

            try:
                os.chdir(working_directory)
                with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                    service = MoleculeViewerDemo._density_service(
                        object(),
                        dependencies,
                    )
            finally:
                os.chdir(original_directory)

            self.assertIs(service.connection_service, connection_service)
            self.assertEqual(
                service.cache_directory,
                appdata.resolve() / "Moltage" / "density_results",
            )
            self.assertEqual(tuple(working_directory.iterdir()), ())


class MoleculeViewerDemoSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.window = MoleculeViewerDemo()
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()
        cls.window = None
        gc.collect()

    def _simple_restart_draft(
        self,
        *,
        step_kind=ProjectStepKind.MOLECULE_OPT,
        terminal=False,
    ):
        if step_kind is ProjectStepKind.TRANSMISSION:
            project = _project(
                ProjectStepState.SUCCEEDED,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            )
            step3 = replace(
                project.steps[2],
                state=ProjectStepState.SUCCEEDED,
                job_id="44029",
                submitted_at=project.updated_at,
                finished_at=project.updated_at,
                scheduler_state="COMPLETED",
                submit_script_filename="submit.sh",
                slurm_output_filename="aims.dft.out",
            )
            step4 = replace(
                project.steps[3],
                state=ProjectStepState.RUNNING,
                job_id="44030",
                submitted_at=project.updated_at,
                submit_script_filename="submit.aitranss.sh",
                slurm_output_filename="aitranss.out",
            )
            project = replace(
                project,
                steps=(*project.steps[:2], step3, step4),
                electrode_provenance=synthetic_project_electrode_provenance(
                    rolls=(None, None)
                ),
            )
        else:
            project = _project(ProjectStepState.RUNNING, step_kind=step_kind)
        if terminal:
            active = next(step for step in project.steps if step.kind is step_kind)
            active = replace(
                active,
                state=ProjectStepState.FAILED,
                finished_at=project.updated_at,
                scheduler_state="CANCELLED",
                last_error="CANCELLED",
            )
            project = replace(
                project,
                revision=project.revision + 1,
                steps=tuple(
                    active if step.kind is step_kind else step
                    for step in project.steps
                ),
            )
        structure = MolecularStructure((Atom(0, "C", 0.0, 0.0, 0.0),))
        return ProjectTaskRestartDraft(
            profile=TEST_PROFILE,
            source_project=project,
            source_step=step_kind,
            source_job_id=next(
                step.job_id for step in project.steps if step.kind is step_kind
            ),
            source_structure=structure,
            connectivity=Connectivity(1, ()),
            source_geometry_sha256="a" * 64,
            optimization_settings=(
                AimsOptimizationSettings()
                if step_kind
                in {
                    ProjectStepKind.MOLECULE_OPT,
                    ProjectStepKind.MOLECULE_AU_OPT,
                }
                else None
            ),
            source_terminal_confirmed=terminal,
        )

    def test_orca_actions_are_grouped_by_workflow_stage(self) -> None:
        self.assertEqual(
            tuple(
                action.text()
                for action in self.window._orca_calculation_menu.actions()
                if not action.isSeparator()
            ),
            (
                "Transmission",
                "Step 1 — Optimization...",
                "Step 2 — WBL Transmission...",
                "Other Analysis",
                "Run Frequency...",
            ),
        )
        self.assertFalse(hasattr(self.window, "_orca_resubmit_action"))
        self.assertFalse(hasattr(self.window, "_orca_view_wbl_action"))

    def test_program_specific_step1_dialog_locks_selected_engine(self) -> None:
        recommendation = StartStepRecommendation(
            StartStepAdvice.STEP1,
            ProjectStepKind.MOLECULE_OPT,
            False,
            "Synthetic molecule without contact Au.",
        )
        profile = configured_orca_profile()
        dialogs = tuple(
            NewCalculationProjectDialog(
                (profile,),
                profile.profile_id,
                "Synthetic",
                recommendation,
                preview_date=ORCA_NOW.date(),
                preselected_engine=engine,
            )
            for engine in (
                CalculationWorkflowKind.FHI_AIMS_AITRANSS,
                CalculationWorkflowKind.ORCA,
            )
        )
        try:
            self.assertEqual(
                tuple(dialog._engine.currentData() for dialog in dialogs),
                (
                    CalculationWorkflowKind.FHI_AIMS_AITRANSS,
                    CalculationWorkflowKind.ORCA,
                ),
            )
            self.assertTrue(all(not dialog._engine.isEnabled() for dialog in dialogs))
            self.assertIs(
                dialogs[1]._current_step(),
                ProjectStepKind.ORCA_OPTIMIZATION,
            )
        finally:
            for dialog in dialogs:
                dialog.deleteLater()

    def test_program_specific_step1_actions_share_the_normal_new_project_flow(
        self,
    ) -> None:
        with patch.object(
            self.window,
            "_submit_new_optimization_project",
        ) as submit:
            self.window._submit_aims_optimization()
            self.window._submit_orca_optimization()

        self.assertEqual(
            tuple(
                call.kwargs["preselected_engine"]
                for call in submit.call_args_list
            ),
            (
                CalculationWorkflowKind.FHI_AIMS_AITRANSS,
                CalculationWorkflowKind.ORCA,
            ),
        )

    def test_project_scoped_orca_resubmit_keeps_identity_and_provenance(self) -> None:
        structure = synthetic_orca_structure()
        server = configured_orca_profile()
        source_project = create_initial_project(
            base_name="SyntheticOrca",
            remote_directory_name="SyntheticOrca.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=server.profile_id,
            remote_project_root=server.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=ORCA_NOW,
        )
        source_project = replace(
            source_project,
            steps=(
                replace(
                    source_project.steps[0],
                    state=ProjectStepState.FAILED,
                    last_error="Synthetic cancellation",
                    orca_optimization_settings=orca_settings(),
                    orca_submitted_elements=tuple(
                        atom.element for atom in structure
                    ),
                ),
            ),
        )
        snapshot = ProjectRecoverySnapshot(
            source_project,
            ProjectStepKind.ORCA_OPTIMIZATION,
            "Synthetic ORCA optimization was cancelled.",
            submitted_structure=structure,
            profile_rebind_confirmed=True,
        )
        generated_project_id = uuid4()
        dependencies = SimpleNamespace(
            orca_submission_service=object(),
            secret_store=MemorySecretStore(),
        )

        class AcceptedSettingsDialog:
            def __init__(inner_self, *args, initial_settings=None, **kwargs):
                del args, kwargs
                self.assertEqual(initial_settings, orca_settings())

            def exec(inner_self):
                return QDialog.DialogCode.Accepted

            def selected_settings(inner_self):
                return orca_settings()

        class AcceptedConfirmationDialog:
            def __init__(inner_self, *args, **kwargs):
                del args, kwargs

            def exec(inner_self):
                return QDialog.DialogCode.Accepted

            def take_temporary_password(inner_self):
                return "synthetic-password"

        with (
            patch(
                "tools.molecule_viewer_demo._create_project_submission_dependencies",
                return_value=dependencies,
            ),
            patch(
                "tools.molecule_viewer_demo.OrcaOptimizationSettingsDialog",
                AcceptedSettingsDialog,
            ),
            patch(
                "tools.molecule_viewer_demo.OrcaSubmissionConfirmationDialog",
                AcceptedConfirmationDialog,
            ),
            patch(
                "tools.molecule_viewer_demo.uuid4",
                return_value=generated_project_id,
            ),
            patch.object(self.window, "_start_submission") as start,
        ):
            self.window._resubmit_orca_optimization(
                (snapshot, server, structure)
            )

        start.assert_called_once()
        called_dependencies, request = start.call_args.args
        self.assertIs(called_dependencies, dependencies)
        self.assertIsInstance(request, OrcaOptimizationSubmissionRequest)
        self.assertEqual(request.base_name, "SyntheticOrca_resubmit")
        self.assertEqual(request.project_id, generated_project_id)
        self.assertEqual(request.resubmission_source_project, source_project)
        self.assertTrue(request.profile_rebind_confirmed)
        self.assertEqual(request.structure, structure)
        self.assertEqual(request.source_molecule_name, "synthetic.xyz")

    def test_orca_input_indicator_view_retains_resubmission_context(self) -> None:
        original_workspace = self.window._active_workspace()
        structure = synthetic_orca_structure()
        profile = configured_orca_profile()
        project = create_initial_project(
            base_name="SyntheticOrcaInput",
            remote_directory_name="SyntheticOrcaInput.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=profile.profile_id,
            remote_project_root=profile.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=ORCA_NOW,
        )
        project = replace(
            project,
            steps=(
                replace(
                    project.steps[0],
                    state=ProjectStepState.RUNNING,
                    job_id="90001",
                    orca_optimization_settings=orca_settings(),
                    orca_submitted_elements=("O", "H", "H"),
                ),
            ),
        )
        connectivity = infer_connectivity(structure, load_default_covalent_radii())
        snapshot = ProjectRecoverySnapshot(
            project,
            ProjectStepKind.ORCA_OPTIMIZATION,
            "Synthetic ORCA optimization is running.",
            submitted_structure=structure,
            connectivity=connectivity,
        )
        result = ProjectGeometryViewResult(
            project.project_id,
            project.remote_directory_name,
            ProjectStepKind.ORCA_OPTIMIZATION,
            ProjectGeometryViewKind.INPUT,
            "orca_opt.inp",
            structure,
            connectivity,
            recovery_snapshot=snapshot,
            recovery_profile=profile,
        )

        workspace = self.window._open_project_geometry_workspace(result)
        self.application.processEvents()

        def restore_workspace() -> None:
            index = self.window._workspace_tabs.indexOf(workspace.content)
            if index >= 0:
                self.window._close_workspace_tab(index)
            if (
                original_workspace is not None
                and original_workspace.content in self.window._workspaces_by_widget
            ):
                self.window._focus_workspace(original_workspace)
            self.application.processEvents()

        self.addCleanup(restore_workspace)
        self.assertTrue(workspace.read_only)
        self.assertEqual(workspace.recovery_snapshot, snapshot)
        self.assertFalse(self.window._submit_orca_action.isEnabled())
        self.assertFalse(self.window._orca_wbl_action.isEnabled())
        self.assertFalse(self.window._orca_frequency_action.isEnabled())
        self.assertFalse(self.window._generate_aims_action.isEnabled())
        self.window._reload()
        self.application.processEvents()
        self.assertIsNone(workspace.recovery_snapshot.optimized_structure)
        self.assertEqual(workspace.recovery_snapshot.submitted_structure, structure)

        updated = replace(snapshot, project=replace(project, revision=2))
        self.window._project_snapshot_updated(updated)
        self.window._store_bound_geometry_workspace(capture_builder_visibility=False)
        self.assertEqual(workspace.recovery_snapshot.project.revision, 2)
        self.assertEqual(self.window._recovery_snapshot.project.revision, 2)

    def _load_accepted_synthetic_electrodes(self):
        structure, connectivity, _, _ = synthetic_step2_state()
        snapshot = ProjectRecoverySnapshot(
            _project(
                ProjectStepState.SUCCEEDED,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            ),
            ProjectStepKind.MOLECULE_AU_OPT,
            "Synthetic Step 2 completed successfully.",
            optimized_structure=structure,
            connectivity=connectivity,
        )
        self.window._load_recovered_snapshot(snapshot, TEST_PROFILE)
        self.application.processEvents()
        for checkbox in self.window._electrode_site_controls.values():
            checkbox.setChecked(True)
        self.application.processEvents()
        self.window._electrode_done_button.click()
        self.application.processEvents()
        self.assertIsNotNone(self.window._applied_electrode_result)
        return self.window._active_geometry_workspace()

    def test_verified_orca_result_loads_read_only_without_rewriting_project_evidence(self):
        now = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
        submitted = MolecularStructure(
            (
                Atom(0, "O", 0.0, 0.0, 0.0),
                Atom(1, "H", 0.75, 0.58, 0.0),
                Atom(2, "H", -0.75, 0.58, 0.0),
            ),
            "synthetic submitted geometry",
        )
        optimized = MolecularStructure(
            (
                Atom(0, "O", 0.0, 0.0, 0.02),
                Atom(1, "H", 0.74, 0.57, 0.0),
                Atom(2, "H", -0.74, 0.57, 0.0),
            ),
            "synthetic verified ORCA geometry",
        )
        project = create_initial_project(
            base_name="SyntheticOrca",
            remote_directory_name="SyntheticOrca.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=TEST_PROFILE.profile_id,
            remote_project_root=TEST_PROFILE.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=now,
        )
        result_evidence = OrcaOptimizationResultEvidence(
            scheduler_succeeded=True,
            normal_termination=True,
            optimization_converged=True,
            final_xyz_valid=True,
            wbl_input_ready=False,
        )
        completed_step = replace(
            project.steps[0],
            state=ProjectStepState.SUCCEEDED,
            job_id="90001",
            submitted_at=now,
            finished_at=now,
            scheduler_state="COMPLETED",
            orca_optimization_result=result_evidence,
            orca_submitted_elements=("O", "H", "H"),
        )
        project = replace(project, steps=(completed_step,))
        snapshot = ProjectRecoverySnapshot(
            project,
            ProjectStepKind.ORCA_OPTIMIZATION,
            "ORCA optimization converged and the final geometry was verified.",
            optimized_structure=optimized,
            connectivity=infer_connectivity(optimized, load_default_covalent_radii()),
            submitted_structure=submitted,
        )

        self.window._load_recovered_snapshot(snapshot, TEST_PROFILE)
        self.application.processEvents()

        workspace = self.window._active_geometry_workspace()
        self.assertTrue(workspace.read_only)
        self.assertEqual(self.window._structure, optimized)
        self.assertEqual(self.window._recovery_snapshot.submitted_structure, submitted)
        self.assertIs(
            self.window._recovery_snapshot.project.steps[0].orca_optimization_result,
            result_evidence,
        )
        self.assertFalse(self.window._generate_aims_action.isEnabled())

    def test_recovered_verified_orca_result_enables_wbl_action(self) -> None:
        original_workspace = self.window._active_workspace()
        original_projects_dialog = self.window._projects_dialog
        structure = synthetic_orca_structure()
        profile = configured_orca_profile()
        project = create_initial_project(
            base_name="SyntheticOrcaWblReady",
            remote_directory_name="SyntheticOrcaWblReady.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=profile.profile_id,
            remote_project_root=profile.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=ORCA_NOW,
        )
        result_evidence = OrcaOptimizationResultEvidence(
            scheduler_succeeded=True,
            normal_termination=True,
            optimization_converged=True,
            final_xyz_valid=True,
            wbl_input_ready=True,
            gbw_sha256="a" * 64,
        )
        completed_step = replace(
            project.steps[0],
            state=ProjectStepState.SUCCEEDED,
            job_id="90002",
            submitted_at=ORCA_NOW,
            finished_at=ORCA_NOW,
            scheduler_state="COMPLETED",
            orca_optimization_result=result_evidence,
            orca_submitted_elements=tuple(atom.element for atom in structure),
        )
        project = replace(project, steps=(completed_step,))
        snapshot = ProjectRecoverySnapshot(
            project,
            ProjectStepKind.ORCA_OPTIMIZATION,
            "Synthetic ORCA optimization and GBW evidence verified.",
            optimized_structure=structure,
            connectivity=infer_connectivity(
                structure,
                load_default_covalent_radii(),
            ),
            submitted_structure=structure,
        )
        self.window._projects_dialog = MagicMock()
        workspace = None
        try:
            workspace = self.window._open_recovered_geometry(snapshot, profile)
            self.application.processEvents()

            self.assertTrue(workspace.read_only)
            self.assertTrue(self.window._orca_wbl_action.isEnabled())
        finally:
            if workspace is not None:
                index = self.window._workspace_tabs.indexOf(workspace.content)
                if index >= 0:
                    self.window._close_workspace_tab(index)
            self.window._projects_dialog = original_projects_dialog
            if (
                original_workspace is not None
                and original_workspace.content in self.window._workspaces_by_widget
            ):
                self.window._focus_workspace(original_workspace)
            self.application.processEvents()

    def test_orca_wbl_contact_picker_accepts_numbered_contact_in_read_only_view(self) -> None:
        original_workspace = self.window._active_workspace()
        original_projects_dialog = self.window._projects_dialog
        profile = configured_orca_profile()
        structure = MolecularStructure(
            (
                Atom(0, "H", -2.0, 0.0, 0.0),
                Atom(1, "S", -1.0, 0.0, 0.0),
                Atom(2, "C", 0.0, 0.0, 0.0),
                Atom(3, "S", 1.0, 0.0, 0.0),
                Atom(4, "H", 2.0, 0.0, 0.0),
            ),
            "synthetic dithiol for viewer contact selection",
        )
        connectivity = Connectivity(
            5,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 1.0),
                Bond(2, 3, 1.0),
                Bond(3, 4, 1.0),
            ),
        )
        project = create_initial_project(
            base_name="SyntheticOrcaContact",
            remote_directory_name="SyntheticOrcaContact.20300102",
            source_molecule_name="synthetic.xyz",
            server_profile_id=profile.profile_id,
            remote_project_root=profile.remote_project_root,
            starting_step=ProjectStepKind.ORCA_OPTIMIZATION,
            workflow_kind=CalculationWorkflowKind.ORCA,
            now=ORCA_NOW,
        )
        evidence = OrcaOptimizationResultEvidence(
            scheduler_succeeded=True,
            normal_termination=True,
            optimization_converged=True,
            final_xyz_valid=True,
            wbl_input_ready=True,
            gbw_sha256="a" * 64,
        )
        optimization = replace(
            project.steps[0],
            state=ProjectStepState.SUCCEEDED,
            job_id="90003",
            submitted_at=ORCA_NOW,
            finished_at=ORCA_NOW,
            scheduler_state="COMPLETED",
            orca_optimization_result=evidence,
            orca_submitted_elements=tuple(atom.element for atom in structure),
        )
        snapshot = ProjectRecoverySnapshot(
            replace(project, steps=(optimization,)),
            ProjectStepKind.ORCA_OPTIMIZATION,
            "Synthetic ORCA optimization and GBW evidence verified.",
            optimized_structure=structure,
            submitted_structure=structure,
            connectivity=connectivity,
        )
        self.window._projects_dialog = MagicMock()
        workspace = None
        try:
            workspace = self.window._open_recovered_geometry(snapshot, profile)
            self.application.processEvents()
            self.assertTrue(workspace.read_only)

            QTimer.singleShot(0, lambda: self.window._report_picked_atom(3))
            selected = self.window._select_orca_wbl_contact_atom("right")

            self.assertEqual(selected, 3)
            self.assertIs(self.window._pick_mode, _ViewerPickMode.NORMAL)
            self.assertIn("S3", self.window._picked_atom_label.text())

            settings = OrcaWblSettingsDialog(
                structure,
                connectivity,
                self.window,
                contact_atom_selector=self.window._select_orca_wbl_contact_atom,
            )

            def choose_from_modal_dialog() -> None:
                QTimer.singleShot(0, lambda: self.window._report_picked_atom(1))
                settings._left.atom.setCurrentIndex(
                    settings._left.atom.findData("MANUAL_SELECT_IN_VIEWER")
                )
                QTimer.singleShot(0, settings.reject)

            QTimer.singleShot(0, choose_from_modal_dialog)
            settings.exec()
            self.assertEqual(settings._left.atom.currentData(), 1)
            self.assertIs(self.window._pick_mode, _ViewerPickMode.NORMAL)
            settings.close()

            self.window._orca_wbl_operation_status_changed(
                snapshot.project.project_id,
                "Synthetic ORCA WBL processing is running.",
            )
            self.assertEqual(
                workspace.operation_label.text(),
                "Synthetic ORCA WBL processing is running.",
            )
        finally:
            if workspace is not None:
                index = self.window._workspace_tabs.indexOf(workspace.content)
                if index >= 0:
                    self.window._close_workspace_tab(index)
            self.window._projects_dialog = original_projects_dialog
            if (
                original_workspace is not None
                and original_workspace.content in self.window._workspaces_by_widget
            ):
                self.window._focus_workspace(original_workspace)
            self.application.processEvents()

    def test_au_lattice_extension_hover_uses_one_cached_snapshot(self) -> None:
        workspace = self._load_accepted_synthetic_electrodes()
        self.assertIsNotNone(workspace)
        self.assertTrue(workspace.electrode_extension_button.isEnabled())
        original_structure = self.window._structure
        original_connectivity = self.window._connectivity

        with patch(
            "tools.molecule_viewer_demo."
            "interaction_candidates_for_working_geometry",
            wraps=interaction_candidates_for_working_geometry,
        ) as evaluate:
            workspace.electrode_extension_button.click()
            self.application.processEvents()
            self.assertEqual(evaluate.call_count, 1)
            self.assertIs(
                self.window._pick_mode,
                _ViewerPickMode.AU_LATTICE_EXTENSION,
            )
            self.assertTrue(workspace.lattice_extension_cache_valid)
            available = next(
                candidate
                for candidate in workspace.lattice_extension_candidates
                if candidate.availability
                is LatticeExtensionAvailability.AVAILABLE
            )
            blocked = next(
                candidate
                for candidate in workspace.lattice_extension_candidates
                if candidate.availability
                is LatticeExtensionAvailability.BLOCKED
            )
            self.assertEqual(
                {candidate.identity.side for candidate in workspace.lattice_extension_candidates},
                {"LEFT", "RIGHT"},
            )
            self.assertGreater(
                len(
                    {
                        candidate.identity.layer_index
                        for candidate in workspace.lattice_extension_candidates
                    }
                ),
                1,
            )

            self.window._lattice_extension_target_hovered(available.identity)
            self.window._lattice_extension_target_hovered(available.identity)
            self.window._viewer._renderer.GetActiveCamera().Azimuth(5.0)
            self.window._viewer._preview_target_at_display(0, 0)
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(
                self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
                1,
            )
            colors = self.window._viewer._scene._preview_polydata.GetPointData().GetArray(
                "preview_color"
            )
            self.assertEqual(
                tuple(int(value) for value in colors.GetTuple3(0)),
                ELEMENT_COLORS_RGB["Au"],
            )
            available_guide = (
                self.window._viewer._scene._lattice_extension_preview_guide
            )
            self.assertEqual(available_guide.center, available.coordinates)
            self.assertEqual(
                available_guide.bond_targets,
                available.predicted_bond_coordinates,
            )
            self.assertEqual(
                available_guide.layer_basis_u,
                available.layer_basis_u,
            )
            self.assertEqual(
                available_guide.layer_basis_v,
                available.layer_basis_v,
            )
            self.assertEqual(
                available_guide.display_color_rgb,
                AVAILABLE_AU_EXTENSION_GUIDE_COLOR_RGB,
            )
            self.assertGreater(
                self.window._viewer._scene._lattice_guide_bond_polydata.GetNumberOfLines(),
                0,
            )
            self.assertGreater(
                self.window._viewer._scene._lattice_guide_plane_polydata.GetNumberOfPolys(),
                0,
            )
            self.assertGreater(
                self.window._viewer._scene._lattice_guide_grid_polydata.GetNumberOfLines(),
                0,
            )

            self.window._lattice_extension_target_hovered(blocked.identity)
            self.assertEqual(evaluate.call_count, 1)
            colors = self.window._viewer._scene._preview_polydata.GetPointData().GetArray(
                "preview_color"
            )
            self.assertEqual(
                tuple(int(value) for value in colors.GetTuple3(0)),
                BLOCKED_AU_EXTENSION_COLOR_RGB,
            )
            blocked_guide = (
                self.window._viewer._scene._lattice_extension_preview_guide
            )
            self.assertEqual(blocked_guide.center, blocked.coordinates)
            self.assertEqual(
                blocked_guide.bond_targets,
                blocked.predicted_bond_coordinates,
            )
            self.assertEqual(
                blocked_guide.display_color_rgb,
                BLOCKED_AU_EXTENSION_COLOR_RGB,
            )
            self.window._viewer.preview_target_cleared.emit()
            self.application.processEvents()
            self.assertIsNone(
                self.window._viewer._scene._lattice_extension_preview_guide
            )
            self.window._lattice_extension_target_hovered(blocked.identity)

        history_count = len(self.window._geometry_undo_history)
        with patch(
            "moltage.junction.electrode_lattice_interaction."
            "add_lattice_extension"
        ) as phase2_add:
            self.window._lattice_extension_target_clicked(blocked.identity)
        phase2_add.assert_not_called()
        self.assertIs(self.window._structure, original_structure)
        self.assertIs(self.window._connectivity, original_connectivity)
        self.assertEqual(len(self.window._geometry_undo_history), history_count)
        self.assertIn("blocked", self.window._operation_label.text().lower())

        self.window._clear_lattice_extension_hover()
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertIsNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )
        self.assertEqual(
            self.window._viewer._scene._lattice_guide_bond_polydata.GetNumberOfLines(),
            0,
        )
        self.assertEqual(
            self.window._viewer._scene._lattice_guide_plane_polydata.GetNumberOfPolys(),
            0,
        )
        self.assertEqual(
            self.window._viewer._scene._lattice_guide_grid_polydata.GetNumberOfLines(),
            0,
        )
        self.window._lattice_extension_target_hovered(available.identity)
        self.assertIsNotNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )
        workspace.electrode_extension_button.click()
        self.application.processEvents()
        self.assertIs(self.window._pick_mode, _ViewerPickMode.NORMAL)
        self.assertFalse(self.window._viewer._preview_target_picking_enabled)
        self.assertIsNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )
        self.window._load(REFERENCE_XYZ_PATH)

    def test_au_lattice_extension_add_undo_redo_is_consistent(self) -> None:
        workspace = self._load_accepted_synthetic_electrodes()
        workspace.electrode_extension_button.click()
        self.application.processEvents()
        candidate = next(
            item
            for item in workspace.lattice_extension_candidates
            if item.availability is LatticeExtensionAvailability.AVAILABLE
            and item.identity.side == "LEFT"
        )
        before_structure = self.window._structure
        before_connectivity = self.window._connectivity
        before_applied = self.window._applied_electrode_result
        before_candidate_identities = tuple(
            item.identity for item in workspace.lattice_extension_candidates
        )
        self.window._lattice_extension_target_hovered(candidate.identity)
        self.assertIsNotNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )

        self.window._lattice_extension_target_clicked(candidate.identity)
        self.application.processEvents()

        added_structure = self.window._structure
        added_connectivity = self.window._connectivity
        added_applied = self.window._applied_electrode_result
        self.assertEqual(len(added_structure), len(before_structure) + 1)
        self.assertGreater(len(added_connectivity), len(before_connectivity))
        self.assertEqual(added_applied.lattice_extensions[-1].identity, candidate.identity)
        self.assertEqual(added_applied.proposal, before_applied.proposal)
        self.assertTrue(workspace.lattice_extension_cache_valid)
        self.assertNotEqual(
            tuple(item.identity for item in workspace.lattice_extension_candidates),
            before_candidate_identities,
        )
        self.assertEqual(len(self.window._geometry_undo_history), 1)
        self.assertIsNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )
        self.assertTrue(self.window._continue_step3_action.isEnabled())
        context = self.window._current_transport_convergence_context()
        self.assertIs(context.working_structure, added_structure)
        self.assertIs(context.applied_electrodes, added_applied)

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "geometry.in"
            self.window._save_geometry_to_path(output_path)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                render_geometry_in(added_structure),
            )

        self.window._undo_geometry()
        self.application.processEvents()
        self.assertEqual(self.window._structure, before_structure)
        self.assertEqual(self.window._connectivity, before_connectivity)
        self.assertEqual(self.window._applied_electrode_result, before_applied)
        self.assertTrue(workspace.lattice_extension_cache_valid)
        self.assertEqual(
            tuple(item.identity for item in workspace.lattice_extension_candidates),
            before_candidate_identities,
        )
        undo_candidate = workspace.lattice_extension_candidates[0]
        self.window._lattice_extension_target_hovered(undo_candidate.identity)
        self.assertIsNotNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )

        self.window._redo_geometry()
        self.application.processEvents()
        self.assertEqual(self.window._structure, added_structure)
        self.assertEqual(self.window._connectivity, added_connectivity)
        self.assertEqual(self.window._applied_electrode_result, added_applied)
        self.assertTrue(workspace.lattice_extension_cache_valid)
        self.assertIsNone(workspace.lattice_extension_hover_identity)
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertIsNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )
        history_count = len(self.window._geometry_undo_history)
        self.window._lattice_extension_target_clicked(candidate.identity)
        self.assertEqual(self.window._structure, added_structure)
        self.assertEqual(len(self.window._geometry_undo_history), history_count)
        self.assertIn("stale", self.window._operation_label.text().lower())
        self.window._load(REFERENCE_XYZ_PATH)

    def test_au_lattice_extension_presentation_failure_is_atomic(self) -> None:
        workspace = self._load_accepted_synthetic_electrodes()
        workspace.electrode_extension_button.click()
        candidate = next(
            item
            for item in workspace.lattice_extension_candidates
            if item.availability is LatticeExtensionAvailability.AVAILABLE
        )
        structure = self.window._structure
        connectivity = self.window._connectivity
        applied = self.window._applied_electrode_result
        history_count = len(self.window._geometry_undo_history)

        with patch.object(
            self.window._viewer,
            "replace_molecule_preserving_view",
            side_effect=VisualizationError("synthetic presentation failure"),
        ):
            self.window._lattice_extension_target_clicked(candidate.identity)

        self.assertIs(self.window._structure, structure)
        self.assertIs(self.window._connectivity, connectivity)
        self.assertIs(self.window._applied_electrode_result, applied)
        self.assertEqual(len(self.window._geometry_undo_history), history_count)
        self.assertIn("presentation failure", self.window._operation_label.text())
        self.window._load(REFERENCE_XYZ_PATH)

    def test_added_extension_rotates_with_its_electrode_and_refreshes_cache(
        self,
    ) -> None:
        workspace = self._load_accepted_synthetic_electrodes()
        workspace.electrode_extension_button.click()
        candidate = next(
            item
            for item in workspace.lattice_extension_candidates
            if item.availability is LatticeExtensionAvailability.AVAILABLE
            and item.identity.side == "LEFT"
            and item.identity.layer_index == 5
        )
        self.window._lattice_extension_target_clicked(candidate.identity)
        applied = self.window._applied_electrode_result
        extension = applied.lattice_extensions[-1]
        extension_index = extension.global_atom_index
        left_cluster = next(
            cluster for cluster in applied.proposal.clusters if cluster.side == "LEFT"
        )
        right_cluster = next(
            cluster for cluster in applied.proposal.clusters if cluster.side == "RIGHT"
        )
        left_corner_index = left_cluster.local_to_global_indices[-1]
        right_corner_index = right_cluster.local_to_global_indices[-1]
        extension_before = self.window._structure[extension_index]
        left_corner_before = self.window._structure[left_corner_index]
        right_corner_before = self.window._structure[right_corner_index]
        remaining = next(
            item
            for item in workspace.lattice_extension_candidates
            if item.identity.side == "LEFT"
        )
        remaining_coordinate_before = remaining.coordinates
        self.window._lattice_extension_target_hovered(remaining.identity)
        self.assertIsNotNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )

        self.window._set_pick_mode(_ViewerPickMode.BOND_ROTATION)
        self.assertIsNone(
            self.window._viewer._scene._lattice_extension_preview_guide
        )
        self.window._bond_rotation_edge_picked(
            left_cluster.site.anchor.binding_atom_index,
            left_cluster.site.contact_au_index,
        )
        self.assertIsNotNone(self.window._torsion_session)
        if extension_index not in self.window._torsion_session.rotating_indices:
            self.window._switch_torsion_side()
        self.assertIn(extension_index, self.window._torsion_session.rotating_indices)
        self.window._torsion_drag_started()
        self.window._torsion_drag_changed(18.0)
        self.window._torsion_drag_finished()

        self.assertNotEqual(self.window._structure[extension_index], extension_before)
        self.assertNotEqual(self.window._structure[left_corner_index], left_corner_before)
        self.assertEqual(self.window._structure[right_corner_index], right_corner_before)
        self.assertEqual(
            self.window._applied_electrode_result.lattice_extensions[-1],
            extension,
        )
        self.assertFalse(workspace.lattice_extension_cache_valid)

        self.window._set_pick_mode(_ViewerPickMode.AU_LATTICE_EXTENSION)
        refreshed = next(
            item
            for item in workspace.lattice_extension_candidates
            if item.identity == remaining.identity
        )
        self.assertNotEqual(refreshed.coordinates, remaining_coordinate_before)
        self.assertEqual(refreshed.identity, remaining.identity)
        self.window._load(REFERENCE_XYZ_PATH)

    def test_au_lattice_extension_editability_and_workspace_boundaries(self) -> None:
        workspace = self._load_accepted_synthetic_electrodes()
        self.assertTrue(workspace.electrode_extension_button.isEnabled())

        workspace.coordinate_only = True
        self.window._route_active_workspace()
        self.assertFalse(workspace.electrode_extension_button.isEnabled())
        workspace.coordinate_only = False
        workspace.read_only = True
        self.window._route_active_workspace()
        self.assertFalse(workspace.electrode_extension_button.isEnabled())
        workspace.read_only = False
        workspace.lattice_extension_submitted_immutable = True
        self.window._route_active_workspace()
        self.assertFalse(workspace.electrode_extension_button.isEnabled())
        workspace.lattice_extension_submitted_immutable = False
        workspace.builder_visible = True
        self.window._route_active_workspace()
        self.assertTrue(workspace.electrode_extension_button.isEnabled())

        workspace.electrode_extension_button.click()
        self.assertTrue(workspace.lattice_extension_cache_valid)
        self.window._lattice_extension_target_hovered(
            workspace.lattice_extension_candidates[0].identity
        )
        other = self.window._create_geometry_workspace(
            identity=None,
            display_title="Synthetic Empty Geometry",
            select=True,
        )
        self.application.processEvents()
        self.assertFalse(workspace.lattice_extension_cache_valid)
        self.assertIsNone(workspace.lattice_extension_hover_identity)
        self.assertFalse(workspace.viewer._preview_target_picking_enabled)
        self.assertIsNone(
            workspace.viewer._scene._lattice_extension_preview_guide
        )
        self.assertIs(workspace.pick_mode, _ViewerPickMode.NORMAL)
        self.assertFalse(other.electrode_extension_button.isEnabled())

        self.window._focus_workspace(workspace)
        self.application.processEvents()
        self.assertFalse(workspace.lattice_extension_cache_valid)
        self.assertFalse(workspace.electrode_extension_button.isChecked())
        workspace.electrode_extension_button.click()
        self.assertTrue(workspace.lattice_extension_cache_valid)
        index = self.window._workspace_tabs.indexOf(other.content)
        self.window._close_workspace_tab(index)
        self.window._load(REFERENCE_XYZ_PATH)

    def test_imported_direct_step3_electrodes_enable_lattice_extension(self) -> None:
        structure, _, _, _ = synthetic_step2_state()
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "synthetic_contact_geometry.in"
            write_geometry_in(structure, source_path)
            self.window._load(source_path)
            self.application.processEvents()
            controls = tuple(self.window._electrode_site_controls.values())
            self.assertEqual(len(controls), 2)
            for checkbox in controls:
                checkbox.setChecked(True)
            self.application.processEvents()
            self.window._electrode_done_button.click()
            self.application.processEvents()

            workspace = self.window._active_geometry_workspace()
            self.assertIsNone(self.window._recovery_snapshot)
            self.assertIsNotNone(self.window._applied_electrode_result)
            self.assertTrue(workspace.electrode_extension_button.isEnabled())
            workspace.electrode_extension_button.click()
            self.assertTrue(workspace.lattice_extension_cache_valid)
            context = self.window._current_imported_transport_context()
            self.assertIs(context.working_structure, self.window._structure)
            self.assertIs(
                context.applied_electrodes,
                self.window._applied_electrode_result,
            )
        self.window._load(REFERENCE_XYZ_PATH)

    def test_repeated_asymmetric_extension_adds_preserve_schema8_provenance(
        self,
    ) -> None:
        workspace = self._load_accepted_synthetic_electrodes()
        workspace.electrode_extension_button.click()
        selected_identities = []
        for side, layer_index in (("LEFT", 1), ("RIGHT", 5)):
            candidate = next(
                item
                for item in workspace.lattice_extension_candidates
                if item.availability is LatticeExtensionAvailability.AVAILABLE
                and item.identity.side == side
                and item.identity.layer_index == layer_index
            )
            selected_identities.append(candidate.identity)
            self.window._lattice_extension_target_clicked(candidate.identity)
            self.application.processEvents()

        applied = self.window._applied_electrode_result
        self.assertEqual(
            tuple(item.identity for item in applied.lattice_extensions[-2:]),
            tuple(selected_identities),
        )
        provenance = provenance_from_applied_electrodes(applied)
        extensions_by_side = {
            record.side: tuple(
                (item.layer_index, item.lattice_key, item.global_atom_index)
                for item in record.lattice_extensions
            )
            for record in provenance
        }
        self.assertEqual(
            extensions_by_side["LEFT"],
            ((1, selected_identities[0].lattice_key, len(self.window._structure) - 2),),
        )
        self.assertEqual(
            extensions_by_side["RIGHT"],
            ((5, selected_identities[1].lattice_key, len(self.window._structure) - 1),),
        )
        self.assertEqual(
            tuple(record.pyramid_layers for record in provenance),
            (6, 6),
        )
        self.window._load(REFERENCE_XYZ_PATH)

    def test_restart_draft_is_editable_but_submit_unlocks_only_after_refresh(self):
        draft = self._simple_restart_draft()
        workspace = self.window._open_project_restart_workspace(draft)
        self.assertIsNotNone(workspace)
        self.application.processEvents()

        self.assertFalse(workspace.read_only)
        self.assertIs(workspace.restart_draft, draft)
        self.assertEqual(
            self.window._submit_aims_action.text(),
            "Submit Restart Draft...",
        )
        self.assertFalse(self.window._submit_aims_action.isEnabled())
        self.assertTrue(self.window._generate_aims_action.isEnabled())

        active = next(
            step
            for step in draft.source_project.steps
            if step.kind is draft.source_step
        )
        terminal_step = replace(
            active,
            state=ProjectStepState.FAILED,
            finished_at=draft.source_project.updated_at,
            scheduler_state="CANCELLED",
            last_error="CANCELLED",
        )
        terminal_project = replace(
            draft.source_project,
            revision=draft.source_project.revision + 1,
            steps=tuple(
                terminal_step if step.kind is draft.source_step else step
                for step in draft.source_project.steps
            ),
        )
        self.window._project_snapshot_updated(
            ProjectRecoverySnapshot(
                terminal_project,
                draft.source_step,
                "Source task is terminal.",
            )
        )
        self.application.processEvents()

        self.assertTrue(workspace.restart_draft.source_terminal_confirmed)
        self.assertTrue(self.window._submit_aims_action.isEnabled())

        index = self.window._workspace_tabs.indexOf(workspace.content)
        self.window._close_workspace_tab(index)
        self.application.processEvents()

    def test_changed_step4_geometry_routes_to_new_step3_project(self):
        draft = self._simple_restart_draft(
            step_kind=ProjectStepKind.TRANSMISSION,
            terminal=True,
        )
        mappings = synthetic_project_electrode_provenance(
            rolls=(None, None)
        )
        draft = replace(draft, electrode_provenance=mappings)
        workspace = self.window._open_project_restart_workspace(draft)
        self.assertIsNotNone(workspace)
        self.application.processEvents()
        self.assertTrue(workspace.coordinate_only)
        self.assertFalse(self.window._electrode_builder_action.isEnabled())
        surface = SimpleNamespace(
            left_local_to_global_zero_based=mappings[0].local_to_global_indices,
            right_local_to_global_zero_based=mappings[1].local_to_global_indices,
        )

        with (
            patch(
                "tools.molecule_viewer_demo.propose_electrode_surfaces",
                return_value=surface,
            ),
            patch.object(self.window, "_submit_restart_step4") as same_project,
            patch.object(
                self.window,
                "_submit_restart_as_new_project",
            ) as new_project,
        ):
            self.window._submit_restart_draft()
            same_project.assert_called_once_with(draft)
            new_project.assert_not_called()

            self.window._structure = MolecularStructure(
                (Atom(0, "C", 0.25, 0.0, 0.0),)
            )
            self.window._submit_restart_draft()
            new_project.assert_called_once_with(
                draft,
                ProjectStepKind.TRANSPORT_CONVERGENCE,
            )

        index = self.window._workspace_tabs.indexOf(workspace.content)
        self.window._close_workspace_tab(index)
        self.application.processEvents()

    def test_permanent_delete_workspace_guard_matches_managed_project_uuid_only(self):
        target_id = uuid4()
        other_id = uuid4()
        local = LocalGeometryWorkspaceIdentity.from_path(REFERENCE_XYZ_PATH)
        other_geometry = ManagedGeometryWorkspaceIdentity(other_id, "MOLECULE_OPT")
        target_geometry = ManagedGeometryWorkspaceIdentity(target_id, "CONTACT_OPT")

        def transmission(project_id):
            return TransmissionWorkspaceIdentity(
                project_id=project_id,
                job_id="123",
                submit_script_filename="submit.aitranss.sh",
                slurm_output_filename="aitranss.out",
                input_hashes=(),
                result_filename="TE.dat",
                result_digest=project_id.hex,
            )

        geometry = self.window._geometry_workspaces_by_identity
        transmissions = self.window._transmission_workspaces_by_identity
        with patch.dict(
            geometry,
            {local: object(), other_geometry: object()},
            clear=True,
        ), patch.dict(
            transmissions,
            {transmission(other_id): object()},
            clear=True,
        ):
            self.assertFalse(
                self.window._has_open_managed_project_workspace(target_id)
            )

            geometry[target_geometry] = object()
            self.assertTrue(
                self.window._has_open_managed_project_workspace(target_id)
            )
            del geometry[target_geometry]
            self.assertFalse(
                self.window._has_open_managed_project_workspace(target_id)
            )

            target_transmission = transmission(target_id)
            transmissions[target_transmission] = object()
            self.assertTrue(
                self.window._has_open_managed_project_workspace(target_id)
            )
            del transmissions[target_transmission]
            self.assertFalse(
                self.window._has_open_managed_project_workspace(target_id)
            )

    def test_project_geometry_views_are_read_only_and_transport_is_deduplicated(self):
        window = self.window
        original_workspace = window._active_workspace()
        original_widgets = set(window._workspaces_by_widget)
        window._create_geometry_workspace(
            identity=None,
            display_title="Geometry inspection test",
            select=True,
        )
        try:
            project_id = uuid4()
            structure = MolecularStructure(
                (
                    Atom(0, "C", 0.0, 0.0, 0.0),
                    Atom(1, "N", 1.2, 0.0, 0.0),
                )
            )
            connectivity = Connectivity(2, (Bond(0, 1, 1.2),))
            step3 = ProjectGeometryViewResult(
                project_id,
                "readonly-project",
                ProjectStepKind.TRANSPORT_CONVERGENCE,
                ProjectGeometryViewKind.TRANSPORT,
                "geometry.in",
                structure,
                connectivity,
            )
            transport_workspace = window._open_project_geometry_workspace(step3)
            self.assertIsNotNone(transport_workspace)
            assert transport_workspace is not None
            self.assertTrue(transport_workspace.read_only)
            self.assertEqual(
                transport_workspace.display_title,
                "readonly-project — Step 3/4 Transport Geometry",
            )
            self.assertFalse(window._rotate_bond_action.isEnabled())
            self.assertFalse(window._geometry_undo_action.isEnabled())
            self.assertFalse(window._geometry_redo_action.isEnabled())
            self.assertFalse(window._electrode_builder_action.isEnabled())
            self.assertFalse(window._generate_aims_action.isEnabled())
            self.assertFalse(window._submit_aims_action.isEnabled())
            self.assertFalse(window._continue_step2_action.isEnabled())
            self.assertFalse(window._continue_step3_action.isEnabled())
            self.assertFalse(window._continue_step4_action.isEnabled())
            self.assertFalse(transport_workspace.save_geometry_button.isEnabled())
            self.assertTrue(window._distance_measure_action.isEnabled())
            self.assertTrue(window._angle_measure_action.isEnabled())

            tab_count = window._workspace_tabs.count()
            step4 = replace(
                step3,
                step_kind=ProjectStepKind.TRANSMISSION,
            )
            same_transport = window._open_project_geometry_workspace(step4)
            self.assertIs(same_transport, transport_workspace)
            self.assertEqual(window._workspace_tabs.count(), tab_count)

            input_result = ProjectGeometryViewResult(
                project_id,
                "readonly-project",
                ProjectStepKind.MOLECULE_OPT,
                ProjectGeometryViewKind.INPUT,
                "geometry.in",
                structure,
                connectivity,
            )
            output_result = replace(
                input_result,
                view_kind=ProjectGeometryViewKind.OUTPUT,
                source_filename="geometry.in.next_step",
            )
            input_workspace = window._open_project_geometry_workspace(input_result)
            output_workspace = window._open_project_geometry_workspace(output_result)
            self.assertIsNot(input_workspace, output_workspace)
            self.assertEqual(window._workspace_tabs.count(), tab_count + 2)
            self.assertTrue(input_workspace.read_only)
            self.assertTrue(output_workspace.read_only)
        finally:
            for widget in tuple(
                set(window._workspaces_by_widget) - original_widgets
            ):
                index = window._workspace_tabs.indexOf(widget)
                if index >= 0:
                    window._close_workspace_tab(index)
            if (
                original_workspace is not None
                and original_workspace.content in window._workspaces_by_widget
            ):
                window._focus_workspace(original_workspace)
            self.application.processEvents()

    def test_step1_output_indicator_workspace_keeps_orbital_controls(self) -> None:
        original_workspace = self.window._active_workspace()
        project = _project(ProjectStepState.SUCCEEDED)
        structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "N", 1.2, 0.0, 0.0),
            )
        )
        artifact = ProjectOrbitalCubeArtifact(
            FrontierOrbital.HOMO,
            "orbital_HOMO.cube",
            2048,
        )
        binding = ProjectOrbitalCubeBinding(
            TEST_PROFILE,
            project,
            structure,
            (artifact,),
        )
        result = ProjectGeometryViewResult(
            project.project_id,
            project.remote_directory_name,
            ProjectStepKind.MOLECULE_OPT,
            ProjectGeometryViewKind.OUTPUT,
            "geometry.in.next_step",
            structure,
            Connectivity(2, (Bond(0, 1, 1.2),)),
            binding,
        )

        workspace = self.window._open_project_geometry_workspace(result)

        self.assertIsNotNone(workspace)
        assert workspace is not None

        def restore_workspace() -> None:
            index = self.window._workspace_tabs.indexOf(workspace.content)
            if index >= 0:
                self.window._close_workspace_tab(index)
            if (
                original_workspace is not None
                and original_workspace.content in self.window._workspaces_by_widget
            ):
                self.window._focus_workspace(original_workspace)
            self.application.processEvents()

        self.addCleanup(restore_workspace)
        self.assertTrue(workspace.read_only)
        self.assertIs(workspace.orbital_binding, binding)
        self.assertTrue(workspace.orbital_controls.isVisible())
        self.assertEqual(
            tuple(button.text() for button in workspace.orbital_buttons.values()),
            ("HOMO",),
        )
        self.assertIn("upper right", workspace.operation_label.text())

        orbital_worker = MagicMock()
        dependencies = SimpleNamespace(project_orbital_cube_service=object())
        with (
            patch(
                "tools.molecule_viewer_demo._create_project_submission_dependencies",
                return_value=dependencies,
            ),
            patch.object(
                self.window,
                "_temporary_transport_password",
                return_value=None,
            ),
            patch(
                "tools.molecule_viewer_demo.ProjectOrbitalCubeWorker",
                return_value=orbital_worker,
            ) as worker_type,
            patch.object(self.window._orbital_thread_pool, "start") as start,
        ):
            self.window._request_project_orbital_cube(
                workspace.runtime_id,
                artifact,
            )

        request = worker_type.call_args.args[1]
        self.assertIs(request.expected_structure, structure)
        self.assertIs(request.project, project)
        start.assert_called_once_with(orbital_worker)
        self.window._orbital_workers.discard(orbital_worker)
        self.window._orbital_worker_origins.pop(orbital_worker, None)
        workspace.orbital_load_running = False

    def test_traceable_au_application_may_remove_terminal_h_for_orbital_view(self):
        recovered = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "S", 1.8, 0.0, 0.0),
                Atom(2, "H", 3.1, 0.0, 0.0),
            )
        )
        applied = AppliedAuPlacement(
            MolecularStructure(
                (
                    Atom(0, "C", 0.0, 0.0, 0.0),
                    Atom(1, "S", 1.8, 0.0, 0.0),
                    Atom(2, "Au", 3.9, 0.0, 0.0),
                )
            ),
            Connectivity(
                3,
                (
                    Bond(0, 1, 1.8),
                    Bond(1, 2, 2.1),
                ),
            ),
            (0, 1, None),
            (2,),
            (2,),
        )

        self.assertTrue(
            _structure_retains_recovered_prefix(
                applied.structure,
                recovered,
                applied,
            )
        )
        edited = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "S", 1.9, 0.0, 0.0),
                Atom(2, "Au", 3.9, 0.0, 0.0),
            )
        )
        self.assertFalse(
            _structure_retains_recovered_prefix(edited, recovered, applied)
        )

    def test_ncs_preview_done_save_and_reload_working_state(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        self.application.processEvents()
        self.assertTrue(self.window._au_tool_dock.isVisible())
        self.assertTrue(
            self.window._au_tool_dock.toggleViewAction().isChecked()
        )
        self.assertEqual(
            self.window.dockWidgetArea(self.window._au_tool_dock),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.assertEqual(
            self.window._status_panel.findChildren(QLineEdit),
            [],
        )
        self.assertNotIn(
            "Preview Au",
            tuple(
                button.text()
                for button in self.window.findChildren(QPushButton)
            ),
        )

        controls = tuple(self.window._site_controls.values())
        self.assertEqual(len(controls), 2)
        first = controls[0]
        self.assertEqual(first.distance_input.text(), "2.34")
        self.assertEqual(first.angle_input.text(), "170.0")
        self.assertFalse(first.parameter_widget.isVisible())
        original_structure = self.window._structure
        original_connectivity = self.window._connectivity
        original_source_bytes = REFERENCE_XYZ_PATH.read_bytes()
        camera = self.window._viewer._renderer.GetActiveCamera()
        camera_before = (
            camera.GetPosition(),
            camera.GetFocalPoint(),
            camera.GetViewUp(),
        )

        first.checkbox.setChecked(True)
        self.application.processEvents()

        self.assertTrue(first.parameter_widget.isVisible())
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            1,
        )
        self.assertGreater(
            self.window._viewer._scene._dash_polydata.GetNumberOfLines(),
            1,
        )
        self.assertGreater(
            self.window._viewer._scene._arc_polydata.GetNumberOfLines(),
            1,
        )
        self.assertIn("Au-S=2.34 Å", self.window._operation_label.text())
        self.assertIn("C-S-Au=170.0°", self.window._operation_label.text())
        self.assertEqual(
            (
                camera.GetPosition(),
                camera.GetFocalPoint(),
                camera.GetViewUp(),
            ),
            camera_before,
        )
        single_site_first_proposal = self.window._current_proposals[0]

        second = controls[1]
        second.checkbox.setChecked(True)
        self.application.processEvents()
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            2,
        )
        self.assertIsNot(
            self.window._current_proposals[0],
            single_site_first_proposal,
        )
        accepted_coordinates = tuple(
            (proposal.x, proposal.y, proposal.z)
            for proposal in self.window._current_proposals
        )

        preview_count = (
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints()
        )
        self.window._au_tool_dock.toggleViewAction().trigger()
        self.application.processEvents()
        self.assertFalse(self.window._au_tool_dock.isVisible())
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            preview_count,
        )
        self.window._au_tool_dock.toggleViewAction().trigger()

        with patch(
            "tools.molecule_viewer_demo.propose_au_placements",
            side_effect=AssertionError("Done must not recalculate proposals"),
        ):
            self.window._done_button.click()
            self.application.processEvents()
        self.assertTrue(self.window._confirmed)
        self.assertIs(self.window._source_structure, original_structure)
        self.assertIs(self.window._source_connectivity, original_connectivity)
        self.assertIsNot(self.window._structure, original_structure)
        self.assertIsNot(self.window._connectivity, original_connectivity)
        self.assertEqual(len(original_structure), 16)
        self.assertEqual(len(self.window._structure), 18)
        self.assertEqual(
            tuple(
                (
                    self.window._structure[index].x,
                    self.window._structure[index].y,
                    self.window._structure[index].z,
                )
                for index in (16, 17)
            ),
            accepted_coordinates,
        )
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertEqual(
            self.window._viewer._scene._dash_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertEqual(
            self.window._viewer._scene._arc_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertEqual(
            self.window._viewer._scene._atom_polydata.GetNumberOfPoints(),
            18,
        )
        self.assertEqual(
            self.window._viewer._scene._atom_actor.GetProperty().GetOpacity(),
            1.0,
        )
        self.assertEqual(
            self.window._viewer._scene._secondary_highlight_indices,
            (16, 17),
        )
        self.assertTrue(
            all(
                not controls.checkbox.isEnabled()
                for controls in self.window._site_controls.values()
            )
        )
        self.assertTrue(self.window._save_geometry_button.isEnabled())
        self.assertIn("Applied 2 Au contacts", self.window._operation_label.text())

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "geometry.in"
            with patch.object(
                QFileDialog,
                "getSaveFileName",
                return_value=(str(output_path), ""),
            ):
                self.window._save_geometry_button.click()
                self.application.processEvents()
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                render_geometry_in(self.window._structure),
            )
            self.assertIn(str(output_path), self.window._operation_label.text())
        self.assertEqual(REFERENCE_XYZ_PATH.read_bytes(), original_source_bytes)

        self.window._reload()
        self.application.processEvents()
        self.assertEqual(len(self.window._structure), 16)
        self.assertNotIn("Au", tuple(atom.element for atom in self.window._structure))
        self.assertFalse(self.window._save_geometry_button.isEnabled())
        self.assertIs(self.window._structure, self.window._source_structure)
        self.assertIs(
            self.window._connectivity,
            self.window._source_connectivity,
        )

    def test_recovered_step1_is_new_source_and_never_auto_adds_gold(self) -> None:
        window = self.window
        structure = read_xyz(REFERENCE_XYZ_PATH)
        covalent_radii = load_default_covalent_radii()
        connectivity = infer_connectivity(structure, covalent_radii)
        snapshot = ProjectRecoverySnapshot(
            _project(ProjectStepState.SUCCEEDED),
            ProjectStepKind.MOLECULE_OPT,
            "Step 1 completed successfully.",
            optimized_structure=structure,
            connectivity=connectivity,
            orbital_cubes=(
                ProjectOrbitalCubeArtifact(
                    FrontierOrbital.HOMO,
                    "orbital_HOMO.cube",
                    2048,
                ),
            ),
        )

        window._load_recovered_snapshot(snapshot, TEST_PROFILE)
        self.application.processEvents()

        action_texts = {
            action.text()
            for action in window._fhi_calculation_menu.actions()
            if not action.isSeparator()
        }
        self.assertIn("Step 2 — Molecule–Au Optimization...", action_texts)
        self.assertNotIn("Continue Step 3", action_texts)
        self.assertIsNone(window._source_path)
        self.assertEqual(window._source_structure, structure)
        self.assertEqual(window._structure, structure)
        self.assertIsNone(window._applied_result)
        self.assertFalse(any(atom.element == "Au" for atom in window._structure))
        self.assertFalse(window._submit_aims_action.isEnabled())
        self.assertFalse(window._continue_step2_action.isEnabled())
        self.assertFalse(window._continue_step3_action.isEnabled())
        self.assertFalse(window._electrode_section.isVisible())
        workspace = window._active_geometry_workspace()
        self.assertIsNotNone(workspace)
        self.assertFalse(workspace.read_only)
        self.assertTrue(workspace.orbital_controls.isVisible())
        self.assertEqual(
            tuple(button.text() for button in workspace.orbital_buttons.values()),
            ("HOMO",),
        )

        scalar_field = CubeScalarField(
            dimensions=(2, 2, 2),
            origin_angstrom=(-1.0, -1.0, -1.0),
            axis_vectors_angstrom=(
                (2.0, 0.0, 0.0),
                (0.0, 2.0, 0.0),
                (0.0, 0.0, 2.0),
            ),
            values=array("d", (-0.1, 0.1, 0.1, -0.1, 0.1, -0.1, -0.1, 0.1)),
        )
        worker_marker = object()
        window._orbital_worker_origins[worker_marker] = workspace.runtime_id
        window._project_orbital_cube_succeeded(
            worker_marker,
            ProjectOrbitalCubeLoadResult(
                snapshot.orbital_cubes[0],
                scalar_field,
            ),
        )
        window._orbital_worker_origins.pop(worker_marker)
        self.application.processEvents()

        self.assertEqual(window._structure, structure)
        self.assertIs(workspace.scalar_field, scalar_field)
        self.assertIs(
            workspace.viewer._scene._orbital_surface.field,
            scalar_field,
        )
        self.assertTrue(workspace.orbital_buttons["orbital_HOMO.cube"].isChecked())
        self.assertFalse(workspace.read_only)

        controls = tuple(window._site_controls.values())
        self.assertEqual(len(controls), 2)
        for site in controls:
            site.checkbox.setChecked(True)
        self.application.processEvents()
        window._confirm_current_proposals()

        self.assertTrue(window._continue_step2_action.isEnabled())
        self.assertEqual(
            sum(atom.element == "Au" for atom in window._structure),
            2,
        )
        self.assertIs(
            workspace.viewer._scene._orbital_surface.field,
            scalar_field,
        )

        orbital_service = object()
        orbital_worker = MagicMock()
        dependencies = SimpleNamespace(
            project_orbital_cube_service=orbital_service,
        )
        with (
            patch(
                "tools.molecule_viewer_demo._create_project_submission_dependencies",
                return_value=dependencies,
            ),
            patch.object(
                window,
                "_temporary_transport_password",
                return_value=None,
            ),
            patch(
                "tools.molecule_viewer_demo.ProjectOrbitalCubeWorker",
                return_value=orbital_worker,
            ) as worker_type,
            patch.object(window._orbital_thread_pool, "start") as start,
        ):
            window._request_project_orbital_cube(
                workspace.runtime_id,
                snapshot.orbital_cubes[0],
            )

        request = worker_type.call_args.args[1]
        self.assertIs(request.expected_structure, structure)
        self.assertIs(request.project, snapshot.project)
        start.assert_called_once_with(orbital_worker)
        window._orbital_workers.discard(orbital_worker)
        window._orbital_worker_origins.pop(orbital_worker, None)
        workspace.orbital_load_running = False

        window._reload()
        self.application.processEvents()
        self.assertEqual(window._source_structure, structure)
        self.assertEqual(window._structure, structure)
        self.assertFalse(any(atom.element == "Au" for atom in window._structure))
        self.assertFalse(window._continue_step2_action.isEnabled())
        window._load(REFERENCE_XYZ_PATH)

    def test_recovered_step2_electrode_preview_done_and_reload_lifecycle(self) -> None:
        window = self.window
        structure, connectivity, _, _ = synthetic_step2_state()
        snapshot = ProjectRecoverySnapshot(
            _project(
                ProjectStepState.SUCCEEDED,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            ),
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=structure,
            connectivity=connectivity,
        )

        window._load_recovered_snapshot(snapshot, TEST_PROFILE)
        self.application.processEvents()
        window._electrode_layers_input.setValue(6)
        self.application.processEvents()

        self.assertTrue(window._electrode_section.isVisible())
        self.assertEqual(
            window._electrode_section.findChildren(QSpinBox),
            [window._electrode_layers_input],
        )
        self.assertEqual(window._electrode_section.findChildren(QDoubleSpinBox), [])
        self.assertEqual(window._electrode_section.findChildren(QComboBox), [])
        self.assertEqual(window._electrode_section.findChildren(QSlider), [])
        self.assertEqual(
            window._electrode_section.findChild(
                QLabel,
                "sixLayerElectrodeHeading",
            ).text(),
            "Canonical Au pyramids",
        )
        controls = tuple(window._electrode_site_controls.values())
        self.assertEqual(len(controls), 2)
        self.assertTrue(all(checkbox.isEnabled() for checkbox in controls))
        self.assertIn("Site 1", controls[0].text())
        self.assertIn("left", controls[0].text().lower())
        self.assertIn("Site 2", controls[1].text())
        self.assertIn("right", controls[1].text().lower())
        self.assertFalse(window._electrode_done_button.isEnabled())
        self.assertFalse(window._continue_step3_action.isEnabled())
        actor_count = window._viewer._renderer.GetActors().GetNumberOfItems()

        controls[0].setChecked(True)
        self.application.processEvents()
        single_proposal = window._electrode_current_proposal
        self.assertIsNotNone(single_proposal)
        self.assertEqual(len(single_proposal.clusters), 1)
        self.assertEqual(single_proposal.clusters[0].roll_degrees, 0)
        single_coordinates = single_proposal.clusters[0].transformed_coordinates
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            55,
        )
        self.assertFalse(window._electrode_done_button.isEnabled())
        self.assertFalse(window._continue_step3_action.isEnabled())
        self.assertEqual(
            window._viewer._renderer.GetActors().GetNumberOfItems(),
            actor_count,
        )

        controls[1].setChecked(True)
        self.application.processEvents()
        joint_proposal = window._electrode_current_proposal
        self.assertIsNotNone(joint_proposal)
        self.assertEqual(len(joint_proposal.clusters), 2)
        self.assertNotEqual(
            joint_proposal.clusters[0].transformed_coordinates,
            single_coordinates,
        )
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            110,
        )
        self.assertTrue(window._electrode_done_button.isEnabled())
        self.assertFalse(window._continue_step3_action.isEnabled())

        controls[1].setChecked(False)
        self.application.processEvents()
        restored_single = window._electrode_current_proposal
        self.assertEqual(restored_single.clusters[0].roll_degrees, 0)
        self.assertEqual(
            restored_single.clusters[0].transformed_coordinates,
            single_coordinates,
        )
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            55,
        )
        self.assertFalse(window._electrode_done_button.isEnabled())

        controls[1].setChecked(True)
        self.application.processEvents()
        accepted = window._electrode_current_proposal
        self.assertEqual(accepted, joint_proposal)
        accepted_preview = accepted.preview_structure
        source_before = window._source_structure

        with patch(
            "tools.molecule_viewer_demo.propose_electrode_placement",
            side_effect=AssertionError("electrode Done must not recalculate geometry"),
        ):
            window._electrode_done_button.click()
            self.application.processEvents()

        self.assertIs(window._source_structure, source_before)
        self.assertIs(window._structure, accepted_preview)
        self.assertIs(window._applied_electrode_result.proposal, accepted)
        self.assertEqual(len(window._structure), 128)
        self.assertEqual(sum(atom.element == "Au" for atom in window._structure), 112)
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertEqual(
            window._viewer._renderer.GetActors().GetNumberOfItems(),
            actor_count,
        )
        self.assertTrue(all(not checkbox.isEnabled() for checkbox in controls))
        self.assertFalse(window._electrode_done_button.isEnabled())
        self.assertTrue(window._continue_step3_action.isEnabled())
        self.assertTrue(window._save_geometry_button.isEnabled())

        window._confirm_electrode_proposal()
        self.assertIn("already applied", window._operation_label.text())

        window._reload()
        self.application.processEvents()
        self.assertIsNone(window._applied_electrode_result)
        self.assertEqual(window._structure, structure)
        self.assertEqual(len(window._structure), 18)
        self.assertEqual(sum(atom.element == "Au" for atom in window._structure), 2)
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        reloaded_controls = tuple(window._electrode_site_controls.values())
        self.assertEqual(len(reloaded_controls), 2)
        self.assertTrue(all(checkbox.isEnabled() for checkbox in reloaded_controls))
        self.assertTrue(all(not checkbox.isChecked() for checkbox in reloaded_controls))
        self.assertFalse(window._electrode_done_button.isEnabled())
        self.assertFalse(window._continue_step3_action.isEnabled())
        self.assertFalse(window._save_geometry_button.isEnabled())
        window._load(REFERENCE_XYZ_PATH)

    def test_pyramid_layer_selector_warns_and_invalidates_stale_preview(self) -> None:
        window = self.window
        structure, connectivity, _, _ = synthetic_step2_state()
        snapshot = ProjectRecoverySnapshot(
            _project(
                ProjectStepState.SUCCEEDED,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            ),
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=structure,
            connectivity=connectivity,
        )
        window._load_recovered_snapshot(snapshot, TEST_PROFILE)
        self.application.processEvents()

        selector = window._electrode_layers_input
        selector.setValue(6)
        self.application.processEvents()
        self.assertEqual((selector.minimum(), selector.maximum(), selector.value()), (2, 10, 6))
        for recommended in (2, 6):
            selector.setValue(recommended)
            self.application.processEvents()
            self.assertFalse(window._electrode_cost_warning.isVisible())
        selector.setValue(7)
        self.application.processEvents()
        self.assertTrue(window._electrode_cost_warning.isVisible())
        self.assertIn("usually not recommended", window._electrode_cost_warning.text())

        selector.setValue(6)
        controls = tuple(window._electrode_site_controls.values())
        for checkbox in controls:
            checkbox.setChecked(True)
        self.application.processEvents()
        old_proposal = window._electrode_current_proposal
        self.assertEqual(tuple(cluster.pyramid_layers for cluster in old_proposal.clusters), (6, 6))

        selector.setValue(4)
        self.application.processEvents()
        current = window._electrode_current_proposal
        self.assertIsNot(current, old_proposal)
        self.assertEqual(tuple(cluster.pyramid_layers for cluster in current.clusters), (4, 4))
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            38,
        )
        self.assertTrue(window._electrode_done_button.isEnabled())

        selector.setValue(7)
        self.application.processEvents()
        current = window._electrode_current_proposal
        self.assertEqual(tuple(cluster.pyramid_layers for cluster in current.clusters), (7, 7))
        self.assertEqual(
            window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            166,
        )
        self.assertTrue(window._electrode_cost_warning.isVisible())
        self.assertTrue(window._electrode_done_button.isEnabled())
        selector.setValue(6)
        window._load(REFERENCE_XYZ_PATH)

    def test_recovered_step2_with_wrong_contact_count_disables_electrode_done(self) -> None:
        structure = read_xyz(REFERENCE_XYZ_PATH)
        connectivity = infer_connectivity(
            structure,
            load_default_covalent_radii(),
        )
        snapshot = ProjectRecoverySnapshot(
            _project(
                ProjectStepState.SUCCEEDED,
                step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            ),
            ProjectStepKind.MOLECULE_AU_OPT,
            "Step 2 completed successfully.",
            optimized_structure=structure,
            connectivity=connectivity,
        )

        self.window._load_recovered_snapshot(snapshot, TEST_PROFILE)
        self.application.processEvents()

        self.assertTrue(self.window._electrode_section.isVisible())
        self.assertEqual(self.window._electrode_site_controls, {})
        self.assertIn("exactly two", self.window._electrode_message_label.text())
        self.assertIn("found 0", self.window._electrode_message_label.text())
        self.assertFalse(self.window._electrode_done_button.isEnabled())
        self.window._load(REFERENCE_XYZ_PATH)

    def test_servers_access_point_opens_real_dialog_factory(self) -> None:
        class AcceptedDialog:
            def exec(self):
                return QDialog.DialogCode.Accepted

        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profiles.json"
            with patch(
                "tools.molecule_viewer_demo.create_server_profiles_dialog",
                return_value=AcceptedDialog(),
            ) as factory, patch(
                "tools.molecule_viewer_demo.server_profiles_path",
                return_value=profile_path,
            ):
                self.window._servers_action.trigger()
                self.application.processEvents()

        factory.assert_called_once_with(self.window)

    def test_manage_servers_reload_updates_runtime_for_same_process_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profiles.json"
            repository = ServerProfileRepository(profile_path)
            configured = profile()
            original = replace(configured, aitranss_runtime=None)
            updated = replace(
                original,
                aitranss_runtime=configured.aitranss_runtime,
            )
            repository.save(original)

            class AcceptedDialog:
                def exec(inner_self):
                    repository.save(updated)
                    return QDialog.DialogCode.Accepted

            class ProjectsStub:
                def __init__(inner_self):
                    inner_self.updates = []

                def update_profile(inner_self, selected):
                    inner_self.updates.append(selected)

            workspace = self.window._bound_geometry_workspace
            self.assertIsNotNone(workspace)
            projects = ProjectsStub()
            prior_recovery = self.window._recovery_profile
            prior_workspace_profile = workspace.recovery_profile
            prior_projects = self.window._projects_dialog
            self.window._recovery_profile = original
            workspace.recovery_profile = original
            self.window._projects_dialog = projects
            try:
                with patch(
                    "tools.molecule_viewer_demo.create_server_profiles_dialog",
                    return_value=AcceptedDialog(),
                ), patch(
                    "tools.molecule_viewer_demo.server_profiles_path",
                    return_value=profile_path,
                ):
                    self.window._open_server_profiles()

                self.assertEqual(self.window._recovery_profile, updated)
                self.assertEqual(workspace.recovery_profile, updated)
                self.assertEqual(projects.updates, [updated])
                self.assertIsNotNone(
                    self.window._recovery_profile.aitranss_runtime
                )
            finally:
                self.window._recovery_profile = prior_recovery
                workspace.recovery_profile = prior_workspace_profile
                self.window._projects_dialog = prior_projects

    def test_submission_cluster_settings_persists_discovered_runtime(self) -> None:
        original = replace(profile(), aitranss_runtime=None)
        configured = profile(profile_id=original.profile_id)
        selected_preset = replace(
            original.execution_preset,
            runtime_minutes=90,
        )
        profile_service = unittest.mock.Mock()
        dependencies = SimpleNamespace(
            connection_service=unittest.mock.Mock(),
            known_hosts=unittest.mock.Mock(),
            profile_service=profile_service,
        )

        class AcceptedClusterDialog:
            def __init__(inner_self, selected, parent, **kwargs):
                inner_self.profile = selected

            def exec(inner_self):
                return QDialog.DialogCode.Accepted

            def selected_preset(inner_self):
                return selected_preset

            def selected_aitranss_runtime(inner_self):
                return configured.aitranss_runtime

        with patch(
            "tools.molecule_viewer_demo.ClusterExecutionSettingsDialog",
            AcceptedClusterDialog,
        ):
            updated = self.window._configure_submission_cluster_settings(
                dependencies,
                original,
            )

        self.assertEqual(updated.execution_preset, selected_preset)
        self.assertEqual(updated.aitranss_runtime, configured.aitranss_runtime)
        profile_service.save.assert_called_once_with(updated)

    def test_email_notifications_menu_saves_current_profile_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profiles.json"
            repository = ServerProfileRepository(profile_path)
            server = profile()
            repository.save(server)
            updated = replace(
                server,
                email_notification_enabled=True,
                email_notification_recipient="user@example.com",
            )
            opened = []

            class AcceptedEmailDialog:
                def __init__(inner_self, selected, selected_repository, parent):
                    opened.append((selected, parent))
                    inner_self.repository = selected_repository

                def exec(inner_self):
                    inner_self.repository.save(updated)
                    return QDialog.DialogCode.Accepted

                def saved_profile(inner_self):
                    return updated

            class ProjectsStub:
                def __init__(inner_self):
                    inner_self.updates = []

                def update_profile(inner_self, selected):
                    inner_self.updates.append(selected)

            projects = ProjectsStub()
            prior_recovery = self.window._recovery_profile
            prior_projects = self.window._projects_dialog
            self.window._recovery_profile = server
            self.window._projects_dialog = projects
            try:
                with patch(
                    "tools.molecule_viewer_demo.server_profiles_path",
                    return_value=profile_path,
                ), patch(
                    "tools.molecule_viewer_demo.EmailNotificationsDialog",
                    AcceptedEmailDialog,
                ), patch(
                    "tools.molecule_viewer_demo._create_project_submission_dependencies"
                ) as remote_dependencies:
                    self.window._open_email_notifications()
            finally:
                self.window._recovery_profile = prior_recovery
                self.window._projects_dialog = prior_projects

            self.assertEqual(opened, [(server, self.window)])
            self.assertEqual(repository.load().profiles[0], updated)
            self.assertEqual(projects.updates, [updated])
            remote_dependencies.assert_not_called()

    def test_projects_window_is_persistent_modeless_and_reused(self) -> None:
        repository = unittest.mock.Mock()
        repository.load.return_value = unittest.mock.Mock(
            profiles=(TEST_PROFILE,),
            last_selected_profile_id=TEST_PROFILE.profile_id,
        )
        dependencies = _ProjectSubmissionDependencies(
            repository,
            unittest.mock.Mock(),
            MemorySecretStore(),
            unittest.mock.Mock(),
            unittest.mock.Mock(),
            recovery_service=unittest.mock.Mock(),
        )

        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies",
            return_value=dependencies,
        ) as create_dependencies, patch.object(
            CalculationProjectsDialog,
            "exec",
            side_effect=AssertionError("Projects must not use a nested modal loop"),
        ) as nested_exec:
            self.window._projects_action.trigger()
            self.application.processEvents()
            first = self.window._projects_dialog

            self.assertIsInstance(first, CalculationProjectsDialog)
            self.assertIs(first.parent(), self.window)
            self.assertTrue(first.isVisible())
            self.assertFalse(first.isModal())
            self.assertTrue(self.window.isEnabled())
            self.assertEqual(
                first._project_has_external_operation,
                self.window._project_has_external_operation,
            )
            nested_exec.assert_not_called()

            first.reject()
            self.application.processEvents()
            self.assertFalse(first.isVisible())

            self.window._open_projects()
            self.application.processEvents()
            self.assertIs(self.window._projects_dialog, first)
            self.assertTrue(first.isVisible())
            self.assertEqual(create_dependencies.call_count, 1)
            nested_exec.assert_not_called()

        first.hide()
        first.setParent(None)
        first.deleteLater()
        self.window._projects_dialog = None
        self.window._projects_profile_repository = None
        self.application.processEvents()

    def test_project_manager_external_operation_lookup_covers_live_submissions(self):
        target = _successful_step1_project()
        unrelated_id = uuid4()
        selected_profile = profile(profile_id=target.server_profile_id)
        continuation = ExistingProjectStepSubmissionRequest(
            selected_profile,
            target,
            _minimal_input_plan(),
        )
        new_project = NewProjectSubmissionRequest(
            selected_profile,
            "MoleculeB",
            "MoleculeB.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _minimal_input_plan(),
        )
        prior = (
            self.window._submission_running,
            self.window._pending_submission_request,
            self.window._transport_operation_running,
            self.window._pending_transport_project_id,
        )
        try:
            self.window._submission_running = True
            self.window._pending_submission_request = continuation
            self.assertTrue(
                self.window._project_has_external_operation(target.project_id)
            )
            self.assertFalse(
                self.window._project_has_external_operation(unrelated_id)
            )

            self.window._pending_submission_request = new_project
            self.assertTrue(
                self.window._project_has_external_operation(target.project_id)
            )
            self.assertTrue(
                self.window._project_has_external_operation(unrelated_id)
            )

            self.window._submission_running = False
            self.window._pending_submission_request = None
            self.window._transport_operation_running = True
            self.window._pending_transport_project_id = target.project_id
            self.assertTrue(
                self.window._project_has_external_operation(target.project_id)
            )
            self.assertFalse(
                self.window._project_has_external_operation(unrelated_id)
            )
        finally:
            (
                self.window._submission_running,
                self.window._pending_submission_request,
                self.window._transport_operation_running,
                self.window._pending_transport_project_id,
            ) = prior

    def test_submit_action_exists_and_no_structure_never_prepares_network(self) -> None:
        self.assertEqual(
            self.window._submit_aims_action.text(),
            "Step 1 — Molecule Optimization...",
        )
        self.assertEqual(
            self.window._submit_aims_action.objectName(),
            "submitAimsOptimization",
        )
        self.assertIsNot(
            self.window._submit_aims_action,
            self.window._generate_aims_action,
        )
        self.window._structure = None
        self.window._submit_aims_action.setEnabled(False)
        with patch(
            "tools.molecule_viewer_demo._create_project_submission_dependencies"
        ) as dependencies, patch.object(QMessageBox, "critical"):
            self.window._submit_aims_optimization()
        dependencies.assert_not_called()
        self.assertIn("No molecular structure", self.window._operation_label.text())
        self.window._load(REFERENCE_XYZ_PATH)

    def test_cancel_project_or_phase2a_or_final_confirmation_never_starts_worker(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        server = profile(save_password=True)
        selection = NewProjectSelection(
            server,
            REFERENCE_XYZ_PATH.stem,
            ProjectStepKind.MOLECULE_OPT,
            f"{REFERENCE_XYZ_PATH.stem}.20300102",
        )

        class ProjectDialog:
            result = QDialog.DialogCode.Rejected
            default_names = []

            def __init__(inner_self, *args, **kwargs):
                ProjectDialog.default_names.append(args[2])
                del kwargs

            def exec(inner_self):
                return inner_self.result

            def selected_project(inner_self):
                return selection

        class SettingsDialog:
            result = QDialog.DialogCode.Rejected
            init_count = 0

            def __init__(inner_self, *args, **kwargs):
                SettingsDialog.init_count += 1
                del args, kwargs

            def exec(inner_self):
                return inner_self.result

            def selected_settings(inner_self):
                return AimsOptimizationSettings()

        class ConfirmationDialog:
            result = QDialog.DialogCode.Rejected

            def __init__(inner_self, *args, **kwargs):
                del args, kwargs

            def exec(inner_self):
                return inner_self.result

            def take_temporary_password(inner_self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            secrets = MemorySecretStore()
            secrets.set_password(server.profile_id, "saved-secret")
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(server)
            submission_service = unittest.mock.Mock()
            dependencies = _ProjectSubmissionDependencies(
                repository,
                ServerProfileService(repository, secrets),
                secrets,
                unittest.mock.Mock(),
                submission_service,
            )

            with patch(
                "tools.molecule_viewer_demo._create_project_submission_dependencies",
                return_value=dependencies,
            ), patch(
                "tools.molecule_viewer_demo.NewCalculationProjectDialog",
                ProjectDialog,
            ), patch(
                "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
                SettingsDialog,
            ), patch.object(
                self.window, "_start_submission"
            ) as start:
                ProjectDialog.result = QDialog.DialogCode.Rejected
                self.window._submit_aims_action.trigger()
                self.assertEqual(SettingsDialog.init_count, 0)
                self.assertEqual(
                    ProjectDialog.default_names[-1],
                    REFERENCE_XYZ_PATH.stem,
                )
                start.assert_not_called()

                ProjectDialog.result = QDialog.DialogCode.Accepted
                SettingsDialog.result = QDialog.DialogCode.Rejected
                self.window._submit_aims_optimization()
                start.assert_not_called()

            with patch(
                "tools.molecule_viewer_demo._create_project_submission_dependencies",
                return_value=dependencies,
            ), patch(
                "tools.molecule_viewer_demo.NewCalculationProjectDialog",
                ProjectDialog,
            ), patch(
                "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
                SettingsDialog,
            ), patch(
                "tools.molecule_viewer_demo.SubmissionConfirmationDialog",
                ConfirmationDialog,
            ), patch.object(
                self.window, "_start_submission"
            ) as start:
                SettingsDialog.result = QDialog.DialogCode.Accepted
                ConfirmationDialog.result = QDialog.DialogCode.Rejected
                self.window._submit_aims_optimization()
                start.assert_not_called()

        self.assertFalse(self.window._submission_running)

    def test_final_submit_runs_one_worker_and_success_shows_path_and_job(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        executor = MemoryRemoteExecutor()
        selected_profile = profile(save_password=False)
        request = NewProjectSubmissionRequest(
            selected_profile,
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _minimal_input_plan(),
            PASSWORD,
        )
        with tempfile.TemporaryDirectory() as directory:
            service = ProjectSubmissionService(
                FixedConnectionService(executor),
                LocalProjectIndexRepository(Path(directory) / "index.json"),
                now_factory=Clock(),
                project_id_factory=lambda: PROJECT_ID,
                temporary_id_factory=_temporary_ids(),
            )
            dependencies = _ProjectSubmissionDependencies(
                unittest.mock.Mock(),
                unittest.mock.Mock(),
                unittest.mock.Mock(),
                unittest.mock.Mock(),
                service,
            )
            with patch.object(QMessageBox, "information") as information, patch.object(
                QMessageBox, "critical"
            ) as critical:
                self.window._start_submission(dependencies, request)
                self.window._start_submission(dependencies, request)
                self.assertTrue(self.window._submission_running)
                self.assertFalse(self.window._submit_aims_action.isEnabled())
                for _ in range(200):
                    self.application.processEvents()
                    if not self.window._submission_running:
                        break
                    QTest.qWait(10)

        self.assertFalse(self.window._submission_running)
        self.assertTrue(self.window._submit_aims_action.isEnabled())
        self.assertEqual(
            [item for item in executor.operations if item[0] == "execute"],
            [
                (
                    "execute",
                    CURRENT_ENVIRONMENT_DISCOVERY_COMMAND,
                ),
                (
                    "execute",
                    "/usr/bin/sbatch --version",
                ),
                (
                    "execute",
                    "cd /srv/moltage-test/projects/MoleculeA.20300102 "
                    "&& /usr/bin/sbatch --parsable submit.sh",
                )
            ],
        )
        critical.assert_not_called()
        information.assert_called_once()
        success_text = information.call_args.args[2]
        self.assertIn("Job ID: 12345", success_text)
        self.assertIn(
            "/srv/moltage-test/projects/MoleculeA.20300102",
            success_text,
        )

    def test_real_scheduler_error_routes_to_slurm_failure_dialog(self) -> None:
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _minimal_input_plan(),
            PASSWORD,
        )
        self.window._pending_submission_request = request
        error = SbatchRejectedError(
            "Slurm rejected the submission: bash: line 1: sbatch: command not found",
            remote_project_path="/srv/moltage-test/projects/MoleculeA.20300102",
            step_kind=ProjectStepKind.MOLECULE_OPT,
        )

        with patch.object(QMessageBox, "critical") as critical:
            self.window._show_submission_error(error)

        self.assertEqual(critical.call_args.args[1], "Slurm submission failed")
        self.assertIn("sbatch: command not found", critical.call_args.args[2])
        self.assertNotIn("Password authentication failed", critical.call_args.args[2])
        self.window._pending_submission_request = None

    def test_real_authentication_error_keeps_authentication_dialog(self) -> None:
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _minimal_input_plan(),
            PASSWORD,
        )
        self.window._pending_submission_request = request

        with patch.object(QMessageBox, "critical") as critical:
            self.window._show_submission_error(
                AuthenticationError("Password authentication failed for ExampleCluster")
            )

        self.assertEqual(critical.call_args.args[1], "Authentication failed")
        self.assertNotIn("Slurm submission failed", critical.call_args.args[2])
        self.window._pending_submission_request = None

    def test_third_site_is_rejected_and_occupied_site_is_disabled(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "S", 0.0, 0.0, 0.0),
                Atom(1, "H", 1.0, 0.0, 0.0),
                Atom(2, "S", 8.0, 0.0, 0.0),
                Atom(3, "H", 9.0, 0.0, 0.0),
                Atom(4, "S", 16.0, 0.0, 0.0),
                Atom(5, "H", 17.0, 0.0, 0.0),
                Atom(6, "S", 24.0, 0.0, 0.0),
                Atom(7, "H", 25.0, 0.0, 0.0),
                Atom(8, "Au", 22.0, 0.0, 0.0),
            )
        )
        connectivity = Connectivity(
            9,
            (
                Bond(0, 1, 1.0),
                Bond(2, 3, 1.0),
                Bond(4, 5, 1.0),
                Bond(6, 7, 1.0),
                Bond(6, 8, 2.0),
            ),
        )
        anchors = (
            AnchorCandidate(AnchorKind.SH, 0, (0, 1)),
            AnchorCandidate(AnchorKind.SH, 2, (2, 3)),
            AnchorCandidate(AnchorKind.SH, 4, (4, 5)),
            AnchorCandidate(AnchorKind.SH, 6, (6, 7), (8,)),
        )
        covalent_radii = load_default_covalent_radii()
        self.window._structure = structure
        self.window._connectivity = connectivity
        self.window._covalent_radii = covalent_radii
        self.window._vdw_radii = load_default_vdw_radii()
        self.window._anchors = anchors
        self.window._viewer.set_molecule(
            structure,
            connectivity,
            covalent_radii,
        )
        self.window._rebuild_anchor_site_controls()
        self.application.processEvents()

        controls = tuple(self.window._site_controls.values())
        for controls_index in (0, 1, 2):
            controls[controls_index].distance_input.setText("2.30")
        controls[0].checkbox.setChecked(True)
        controls[1].checkbox.setChecked(True)
        controls[2].checkbox.setChecked(True)
        self.application.processEvents()

        self.assertTrue(controls[0].checkbox.isChecked())
        self.assertTrue(controls[1].checkbox.isChecked())
        self.assertFalse(controls[2].checkbox.isChecked())
        self.assertIn("At most two", self.window._operation_label.text())
        occupied = controls[3]
        self.assertFalse(occupied.checkbox.isEnabled())
        self.assertIsNone(occupied.parameter_widget)
        self.assertIn("existing Au: Au8", occupied.checkbox.text())

    def test_bond_detection_setting_reuses_connectivity_update_path(self) -> None:
        self.window._connectivity_multiplier = 1.10
        self.window._load(REFERENCE_XYZ_PATH)
        source_structure = self.window._source_structure
        original_structure = self.window._structure

        class AcceptedBondDetectionDialog:
            def __init__(inner_self, current_factor, parent=None):
                self.assertEqual(current_factor, 1.10)
                self.assertIs(parent, self.window)
                inner_self._preview_callback = None
                inner_self.preview_factor_changed = inner_self

            def connect(inner_self, callback):
                inner_self._preview_callback = callback

            def exec(inner_self):
                inner_self._preview_callback(1.20)
                return QDialog.DialogCode.Accepted

            def selected_factor(inner_self):
                return 1.20

        with patch(
            "tools.molecule_viewer_demo.BondDetectionDialog",
            AcceptedBondDetectionDialog,
        ), patch(
            "tools.molecule_viewer_demo.infer_connectivity",
            wraps=infer_connectivity,
        ) as mocked_inference:
            self.window._bond_detection_action.trigger()
            self.application.processEvents()
            self.assertEqual(mocked_inference.call_count, 1)
            self.assertAlmostEqual(
                mocked_inference.call_args.kwargs["multiplier"],
                1.20,
            )
            self.assertIs(self.window._structure, original_structure)
            self.assertIs(self.window._source_structure, source_structure)
        self.assertEqual(self.window._connectivity_multiplier, 1.20)
        self.window._connectivity_multiplier = 1.10
        self.window._reload()

    def test_sh_done_removes_h_and_promotes_preview_au_to_real_atom(self) -> None:
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
        anchors = detect_anchors(structure, connectivity)
        radii = load_default_covalent_radii()
        self.window._source_structure = structure
        self.window._source_connectivity = connectivity
        self.window._structure = structure
        self.window._connectivity = connectivity
        self.window._covalent_radii = radii
        self.window._vdw_radii = load_default_vdw_radii()
        self.window._anchors = anchors
        self.window._applied_result = None
        self.window._save_geometry_button.setEnabled(False)
        self.window._viewer.set_molecule(structure, connectivity, radii)
        self.window._rebuild_anchor_site_controls()

        controls = self.window._site_controls[anchors[0]]
        controls.checkbox.setChecked(True)
        self.application.processEvents()
        proposal = self.window._current_proposals[0]
        self.assertEqual(proposal.remove_atom_indices, (2,))
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            1,
        )
        self.assertEqual(
            self.window._viewer._scene._preview_hidden_atom_indices,
            (2,),
        )
        self.assertEqual(
            self.window._viewer._scene._atom_polydata.GetNumberOfPoints(),
            2,
        )
        self.assertEqual(tuple(atom.element for atom in self.window._structure), ("C", "S", "H"))
        self.assertIn(
            "preview replacement (removed on Done): H2",
            self.window._operation_label.text(),
        )

        self.window._done_button.click()
        self.application.processEvents()

        self.assertEqual(tuple(atom.element for atom in self.window._structure), ("C", "S", "Au"))
        self.assertEqual(
            (
                self.window._structure[2].x,
                self.window._structure[2].y,
                self.window._structure[2].z,
            ),
            (proposal.x, proposal.y, proposal.z),
        )
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            0,
        )
        self.assertEqual(
            self.window._viewer._scene._preview_hidden_atom_indices,
            (),
        )
        self.assertEqual(
            self.window._viewer._scene._secondary_highlight_indices,
            (2,),
        )
        applied_controls = next(iter(self.window._site_controls.values()))
        self.assertFalse(applied_controls.checkbox.isEnabled())
        self.assertIn("Removed source atoms: source H2", self.window._operation_label.text())
        self.assertIs(
            self.window._bound_geometry_workspace.structure,
            self.window._structure,
        )
        self.assertIs(
            self.window._bound_geometry_workspace.applied_result,
            self.window._applied_result,
        )

    def test_sh_and_alkynyl_preview_restore_apply_and_generate_exact_geometry(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "C", 1.0, 0.0, 0.0),
                Atom(1, "S", 0.0, 0.0, 0.0),
                Atom(2, "H", -0.5, 1.0, 0.0),
                Atom(3, "C", 20.0, 0.0, 0.0),
                Atom(4, "C", 21.0, 0.0, 0.0),
                Atom(5, "C", 22.0, 0.0, 0.0),
                Atom(6, "H", 23.0, 0.0, 0.0),
            )
        )
        connectivity = Connectivity(
            7,
            (
                Bond(0, 1, 1.0),
                Bond(1, 2, 5.0**0.5 / 2.0),
                Bond(3, 4, 1.0),
                Bond(4, 5, 1.0),
                Bond(5, 6, 1.0),
            ),
        )
        anchors = detect_anchors(structure, connectivity)
        self.assertEqual(
            {anchor.kind for anchor in anchors},
            {AnchorKind.SH, AnchorKind.ALKYNYL_C},
        )
        radii = load_default_covalent_radii()
        try:
            self.window._source_path = None
            self.window._source_structure = structure
            self.window._source_connectivity = connectivity
            self.window._structure = structure
            self.window._connectivity = connectivity
            self.window._covalent_radii = radii
            self.window._vdw_radii = load_default_vdw_radii()
            self.window._anchors = anchors
            self.window._applied_result = None
            self.window._save_geometry_button.setEnabled(False)
            self.window._viewer.set_molecule(structure, connectivity, radii)
            self.window._rebuild_anchor_site_controls()

            controls_by_kind = {
                anchor.kind: self.window._site_controls[anchor]
                for anchor in anchors
            }
            controls_by_kind[AnchorKind.SH].checkbox.setChecked(True)
            controls_by_kind[AnchorKind.ALKYNYL_C].checkbox.setChecked(True)
            self.application.processEvents()

            self.assertEqual(
                tuple(
                    proposal.remove_atom_indices
                    for proposal in self.window._current_proposals
                ),
                ((2,), (6,)),
            )
            self.assertEqual(
                self.window._viewer._scene._preview_hidden_atom_indices,
                (2, 6),
            )
            self.assertEqual(
                self.window._viewer._scene._atom_polydata.GetNumberOfPoints(),
                5,
            )
            self.assertEqual(
                self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
                2,
            )
            self.assertEqual(
                tuple(atom.element for atom in self.window._structure).count("H"),
                2,
            )

            controls_by_kind[AnchorKind.ALKYNYL_C].checkbox.setChecked(False)
            controls_by_kind[AnchorKind.SH].checkbox.setChecked(False)
            self.application.processEvents()
            self.assertEqual(
                self.window._viewer._scene._preview_hidden_atom_indices,
                (),
            )
            self.assertEqual(
                self.window._viewer._scene._atom_polydata.GetNumberOfPoints(),
                7,
            )

            controls_by_kind[AnchorKind.SH].checkbox.setChecked(True)
            controls_by_kind[AnchorKind.ALKYNYL_C].checkbox.setChecked(True)
            self.application.processEvents()
            proposals = self.window._current_proposals
            self.window._done_button.click()
            self.application.processEvents()

            self.assertEqual(
                self.window._applied_result.removed_atom_indices,
                (2, 6),
            )
            self.assertEqual(
                tuple(atom.element for atom in self.window._structure),
                ("C", "S", "C", "C", "C", "Au", "Au"),
            )
            self.assertEqual(
                tuple(
                    (
                        self.window._structure[index].x,
                        self.window._structure[index].y,
                        self.window._structure[index].z,
                    )
                    for index in self.window._applied_result.added_au_indices
                ),
                tuple(
                    (proposal.x, proposal.y, proposal.z)
                    for proposal in proposals
                ),
            )
            self.assertEqual(
                self.window._viewer._scene._preview_hidden_atom_indices,
                (),
            )
            self.assertIs(
                self.window._bound_geometry_workspace.structure,
                self.window._structure,
            )
            self.assertIs(
                self.window._bound_geometry_workspace.applied_result,
                self.window._applied_result,
            )

            geometry_text = AimsOptimizationInputPlan(
                self.window._structure,
                AimsOptimizationSettings(),
            ).materialize(synthetic_species_library()).geometry_text
            self.assertEqual(
                geometry_text,
                render_geometry_in(self.window._structure),
            )
            self.assertNotIn(" H\n", geometry_text)
            self.assertEqual(
                sum(
                    line.endswith(" Au")
                    for line in geometry_text.splitlines()
                ),
                2,
            )
        finally:
            self.window._load(REFERENCE_XYZ_PATH)
            self.application.processEvents()

    def test_application_failure_keeps_working_molecule_and_preview(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        controls = next(iter(self.window._site_controls.values()))
        controls.checkbox.setChecked(True)
        self.application.processEvents()
        structure_before = self.window._structure
        connectivity_before = self.window._connectivity
        proposals_before = self.window._current_proposals
        preview_before = (
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints()
        )

        with patch(
            "tools.molecule_viewer_demo.apply_au_placements",
            side_effect=AuPlacementApplicationError("synthetic failure"),
        ):
            self.window._done_button.click()
            self.application.processEvents()

        self.assertIs(self.window._structure, structure_before)
        self.assertIs(self.window._connectivity, connectivity_before)
        self.assertIs(self.window._current_proposals, proposals_before)
        self.assertEqual(
            self.window._viewer._scene._preview_polydata.GetNumberOfPoints(),
            preview_before,
        )
        self.assertIn("synthetic failure", self.window._operation_label.text())
        self.assertFalse(self.window._save_geometry_button.isEnabled())

    def test_save_failure_keeps_applied_working_structure(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        controls = next(iter(self.window._site_controls.values()))
        controls.checkbox.setChecked(True)
        self.application.processEvents()
        self.window._done_button.click()
        self.application.processEvents()
        structure_before = self.window._structure
        connectivity_before = self.window._connectivity

        with (
            patch.object(
                QFileDialog,
                "getSaveFileName",
                return_value=("unused-geometry.in", ""),
            ),
            patch(
                "tools.molecule_viewer_demo.write_geometry_in",
                side_effect=OSError("synthetic disk failure"),
            ),
        ):
            self.window._save_geometry_button.click()
            self.application.processEvents()

        self.assertIs(self.window._structure, structure_before)
        self.assertIs(self.window._connectivity, connectivity_before)
        self.assertIn("synthetic disk failure", self.window._operation_label.text())
        self.assertTrue(self.window._save_geometry_button.isEnabled())

    def test_generate_aims_action_uses_current_working_structure(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "H", 0.0, 0.0, 0.0),
                Atom(1, "H", 0.75, 0.0, 0.0),
            )
        )
        self.window._structure = structure
        self.window._generate_aims_action.setEnabled(True)
        captured = {}

        class AcceptedSettingsDialog:
            def __init__(inner_self, dialog_structure, parent=None):
                captured["structure"] = dialog_structure
                captured["parent"] = parent

            def exec(inner_self):
                return QDialog.DialogCode.Accepted

            def selected_settings(inner_self):
                return AimsOptimizationSettings()

        with tempfile.TemporaryDirectory() as directory:
            selected_profile = profile(save_password=False)
            secrets = MemorySecretStore()
            secrets.set_password(selected_profile.profile_id, "saved-secret")
            repository = ServerProfileRepository(Path(directory) / "profiles.json")
            repository.save(selected_profile)
            executor = MemoryRemoteExecutor()
            export_service = AimsInputExportService(
                FixedConnectionService(executor),
                repository,
            )
            dependencies = _ProjectSubmissionDependencies(
                repository,
                ServerProfileService(repository, secrets),
                secrets,
                unittest.mock.Mock(),
                unittest.mock.Mock(),
                input_export_service=export_service,
            )
            with patch(
                "tools.molecule_viewer_demo._create_project_submission_dependencies",
                return_value=dependencies,
            ), patch.object(
                self.window,
                "_temporary_transport_password",
                return_value="saved-secret",
            ), patch(
                "tools.molecule_viewer_demo.AimsOptimizationSettingsDialog",
                AcceptedSettingsDialog,
            ), patch.object(
                QInputDialog,
                "getItem",
                return_value=(selected_profile.name, True),
            ), patch.object(
                QFileDialog,
                "getExistingDirectory",
                return_value=directory,
            ), patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ), patch.object(
                QMessageBox,
                "information",
            ) as information, patch.object(
                QMessageBox,
                "critical",
            ) as critical:
                self.window._generate_aims_action.trigger()
                for _ in range(500):
                    self.application.processEvents()
                    if not self.window._input_export_workers:
                        break
                    QTest.qWait(10)
                self.assertFalse(self.window._input_export_workers)

            geometry_path = Path(directory) / "geometry.in"
            control_path = Path(directory) / "control.in"
            self.assertEqual(
                geometry_path.read_text(encoding="utf-8"),
                "atom 0.0 0.0 0.0 H\natom 0.75 0.0 0.0 H\n",
            )
            self.assertIn("xc pbe", control_path.read_text(encoding="utf-8"))
            self.assertFalse(
                any(
                    operation[0] in {"execute", "mkdir", "write"}
                    for operation in executor.operations
                )
            )
            information.assert_called_once()
            critical.assert_not_called()
        self.assertIs(captured["structure"], structure)
        self.assertIs(captured["parent"], self.window)
        self.assertIn(
            "Generated FHI-aims optimization inputs",
            self.window._operation_label.text(),
        )
        self.window._load(REFERENCE_XYZ_PATH)

    def test_atom_pick_reports_preserved_index_and_preview_is_not_real(self) -> None:
        self.window._load(REFERENCE_XYZ_PATH)
        self.application.processEvents()
        structure = self.window._structure
        renderer = self.window._viewer._renderer
        render_window = self.window._viewer._vtk_widget.GetRenderWindow()
        render_window.Render()
        original_atoms = structure.atoms
        original_highlights = (
            self.window._viewer._scene._primary_highlight_indices,
            self.window._viewer._scene._secondary_highlight_indices,
        )
        camera = renderer.GetActiveCamera()
        original_camera = (
            camera.GetPosition(),
            camera.GetFocalPoint(),
            camera.GetViewUp(),
        )
        atom = structure[0]
        renderer.SetWorldPoint(atom.x, atom.y, atom.z, 1.0)
        renderer.WorldToDisplay()
        display_x, display_y, _ = renderer.GetDisplayPoint()
        picked = self.window._viewer._scene.pick_atom_index(
            round(display_x),
            round(display_y),
        )
        self.assertEqual(picked, 0)

        emitted = []
        self.window._viewer.atom_picked.connect(emitted.append)
        render_width, render_height = render_window.GetSize()
        widget = self.window._viewer._vtk_widget
        qt_point = QPoint(
            round(display_x * widget.width() / render_width),
            round(
                (render_height - 1 - display_y)
                * widget.height()
                / render_height
            ),
        )
        self.window._viewer._pick_at(qt_point)
        self.assertEqual(emitted, [0])
        self.assertEqual(self.window._picked_atom_label.text(), "Selected atom: C0")

        first_controls = next(iter(self.window._site_controls.values()))
        first_controls.checkbox.setChecked(True)
        self.application.processEvents()
        preview_point = self.window._viewer._scene._preview_polydata.GetPoint(0)
        renderer.SetWorldPoint(*preview_point, 1.0)
        renderer.WorldToDisplay()
        preview_x, preview_y, _ = renderer.GetDisplayPoint()
        self.assertIsNone(
            self.window._viewer._scene.pick_atom_index(
                round(preview_x),
                round(preview_y),
            )
        )
        self.assertIs(structure.atoms, original_atoms)
        self.assertEqual(
            (
                self.window._viewer._scene._primary_highlight_indices,
                self.window._viewer._scene._secondary_highlight_indices,
            ),
            original_highlights,
        )
        self.assertEqual(
            (
                camera.GetPosition(),
                camera.GetFocalPoint(),
                camera.GetViewUp(),
            ),
            original_camera,
        )

    def test_all_anchor_kinds_have_complete_defaults_and_explicit_labels(self) -> None:
        kinds = tuple(AnchorKind)
        elements = ("S", "N", "N", "S", "S", "C", "N", "C")
        structure = MolecularStructure(
            tuple(
                Atom(index, element, float(index) * 5.0, 0.0, 0.0)
                for index, element in enumerate(elements)
            )
        )
        self.window._structure = structure
        self.window._connectivity = Connectivity(len(structure), ())
        self.window._anchors = tuple(
            AnchorCandidate(kind, index, (index,))
            for index, kind in enumerate(kinds)
        )
        self.window._rebuild_anchor_site_controls()
        self.application.processEvents()
        expected = {
            AnchorKind.NCS: ("2.34", "170.0", "C-S-Au (°)"),
            AnchorKind.SMe: ("2.40", "100.0", "C(Me)-S-Au (°)"),
            AnchorKind.PYRIDINE_N: (
                "2.15",
                "119.0",
                "Au-N-C1/C2 (equal) (°)",
            ),
            AnchorKind.NH2: ("2.42", "120.0", "R-N-Au (°)"),
            AnchorKind.SH: ("2.35", "105.0", "R-S-Au (°)"),
            AnchorKind.ALKYNYL_C: (
                "2.05",
                "179.0",
                "C(adj)-C(bind)-Au (°)",
            ),
            AnchorKind.CYANO_N: ("2.20", "179.0", "C-N-Au (°)"),
            AnchorKind.DICYANO_C: ("2.10", "111.0", "R-C-Au (°)"),
        }

        self.assertEqual(set(self.window._site_controls), set(self.window._anchors))
        for anchor, controls in self.window._site_controls.items():
            with self.subTest(kind=anchor.kind):
                distance, angle, angle_label = expected[anchor.kind]
                self.assertEqual(controls.distance_input.text(), distance)
                self.assertEqual(controls.angle_input.text(), angle)
                labels = tuple(
                    label.text()
                    for label in controls.parameter_widget.findChildren(QLabel)
                )
                self.assertIn(angle_label, labels)
                self.assertFalse(controls.parameter_widget.isVisible())

    def test_paired_dicyano_cyano_n_controls_use_contextual_defaults(self) -> None:
        atoms = (
            ("S", -12.0, 0.0, 0.0),
            ("C", -10.0, 0.0, 0.0),
            ("C", -9.0, 0.7, 0.0),
            ("N", -8.0, 1.4, 0.0),
            ("C", -9.0, -0.7, 0.0),
            ("N", -8.0, -1.4, 0.0),
            ("O", 12.0, 0.0, 0.0),
            ("C", 10.0, 0.0, 0.0),
            ("C", 9.0, 0.7, 0.0),
            ("N", 8.0, 1.4, 0.0),
            ("C", 9.0, -0.7, 0.0),
            ("N", 8.0, -1.4, 0.0),
        )
        structure = MolecularStructure(
            tuple(
                Atom(index, element, x, y, z)
                for index, (element, x, y, z) in enumerate(atoms)
            )
        )
        connectivity = Connectivity(
            len(structure),
            tuple(
                Bond(first, second, 1.0)
                for first, second in (
                    (0, 1), (1, 2), (2, 3), (1, 4), (4, 5),
                    (6, 7), (7, 8), (8, 9), (7, 10), (10, 11),
                )
            ),
        )
        self.window._structure = structure
        self.window._connectivity = connectivity
        self.window._anchors = detect_anchors(structure, connectivity)

        self.window._rebuild_anchor_site_controls()

        for anchor, controls in self.window._site_controls.items():
            if anchor.kind is AnchorKind.CYANO_N:
                self.assertEqual(controls.distance_input.text(), "2.00")
                self.assertEqual(controls.angle_input.text(), "121.0")
            elif anchor.kind is AnchorKind.DICYANO_C:
                self.assertEqual(controls.distance_input.text(), "2.10")
                self.assertEqual(controls.angle_input.text(), "111.0")

    def test_pyridine_preview_renders_both_equal_angle_arcs(self) -> None:
        # Keep this synthetic preview fixture away from the packaged steric
        # cutoff; it is not a scientific geometry default.
        ring_side = 1.4
        half_height = ring_side * 3.0**0.5 / 2.0
        structure = MolecularStructure(
            (
                Atom(0, "N", ring_side, 0.0, 0.0),
                Atom(1, "C", 0.5 * ring_side, half_height, 0.0),
                Atom(2, "C", -0.5 * ring_side, half_height, 0.0),
                Atom(3, "C", -ring_side, 0.0, 0.0),
                Atom(4, "C", -0.5 * ring_side, -half_height, 0.0),
                Atom(5, "C", 0.5 * ring_side, -half_height, 0.0),
            )
        )
        connectivity = Connectivity(
            6,
            tuple(
                Bond(first, second, ring_side)
                for first, second in (
                    (0, 1),
                    (1, 2),
                    (2, 3),
                    (3, 4),
                    (4, 5),
                    (0, 5),
                )
            ),
        )
        anchor = AnchorCandidate(AnchorKind.PYRIDINE_N, 0, range(6))
        radii = load_default_covalent_radii()
        self.window._structure = structure
        self.window._connectivity = connectivity
        self.window._covalent_radii = radii
        self.window._vdw_radii = load_default_vdw_radii()
        self.window._anchors = (anchor,)
        self.window._viewer.set_molecule(structure, connectivity, radii)
        self.window._rebuild_anchor_site_controls()

        controls = self.window._site_controls[anchor]
        controls.checkbox.setChecked(True)
        self.application.processEvents()

        self.assertEqual(len(self.window._viewer._scene._distance_annotations), 1)
        self.assertEqual(len(self.window._viewer._scene._angle_annotations), 2)
        self.assertIn(
            "Au-N-C1/C2 (equal)=119.0°/119.0°",
            self.window._operation_label.text(),
        )

    def test_occupied_new_anchor_kinds_are_disabled(self) -> None:
        structure = MolecularStructure(
            (
                Atom(0, "C", 0.0, 0.0, 0.0),
                Atom(1, "Au", 1.0, 0.0, 0.0),
                Atom(2, "N", 5.0, 0.0, 0.0),
                Atom(3, "Au", 6.0, 0.0, 0.0),
            )
        )
        anchors = (
            AnchorCandidate(AnchorKind.ALKYNYL_C, 0, (0,), (1,)),
            AnchorCandidate(AnchorKind.CYANO_N, 2, (2,), (3,)),
        )
        self.window._structure = structure
        self.window._anchors = anchors
        self.window._rebuild_anchor_site_controls()

        for controls in self.window._site_controls.values():
            self.assertFalse(controls.checkbox.isEnabled())
            self.assertIsNone(controls.parameter_widget)


class MoleculeViewerShutdownGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.warning_patcher = patch.object(QMessageBox, "warning")
        self.critical_patcher = patch.object(QMessageBox, "critical")
        self.warning = self.warning_patcher.start()
        self.critical = self.critical_patcher.start()
        self.addCleanup(self.warning_patcher.stop)
        self.addCleanup(self.critical_patcher.stop)
        self.window = MoleculeViewerDemo()
        self.window.show()
        self.application.processEvents()
        self._release_events = []

    def tearDown(self) -> None:
        for release in self._release_events:
            release.set()
        self._wait_until(
            lambda: not self.window._has_active_remote_operation(),
            fail_on_timeout=False,
        )
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        del self.window
        gc.collect()

    def _wait_until(self, predicate, timeout=5.0, *, fail_on_timeout=True):
        return wait_until(
            predicate, timeout, fail_on_timeout=fail_on_timeout,
            message="asynchronous GUI operation timed out",
        )

    def _assert_shutdown_warning(self) -> None:
        self.warning.assert_called_once()
        self.assertEqual(
            self.warning.call_args.args[1],
            "Remote operation still running",
        )
        self.assertIn(
            "Wait for it to finish before closing the application",
            self.warning.call_args.args[2],
        )

    def test_hidden_projects_refresh_allows_main_close_without_waiting(self) -> None:
        service = _BlockingRecoveryService()
        self._release_events.append(service.release)
        dialog = CalculationProjectsDialog(
            (TEST_PROFILE,),
            TEST_PROFILE.profile_id,
            service,
            MemorySecretStore(),
            unittest.mock.Mock(),
            self.window,
        )
        pool = _RecordingThreadPool(dialog)
        dialog._thread_pool = pool
        dialog._password_for = lambda _profile: (True, "temporary-secret")
        self.window._projects_dialog = dialog
        dialog.show()
        dialog._discover()
        self._wait_until(service.started.is_set)
        self.assertTrue(dialog.has_active_remote_operation())
        self.assertFalse(dialog.has_active_remote_operation(include_status_refresh=False))
        self.assertEqual(pool.activeThreadCount(), 0)
        dialog.reject()
        self.application.processEvents()

        self.assertFalse(dialog.isVisible())
        self.assertTrue(self.window.close())
        self.application.processEvents()

        self.assertFalse(self.window.isVisible())
        self.assertFalse(dialog.has_active_remote_operation())
        self.assertEqual(pool.activeThreadCount(), 0)
        self.assertEqual(pool.wait_for_done_calls, 0)
        self.assertEqual(service.cancel_calls, 0)
        self.assertFalse(service.release.is_set())
        self.warning.assert_not_called()

        service.release.set()
        self._wait_until(lambda: not dialog.has_active_remote_operation())
        self._wait_until(lambda: pool.activeThreadCount() == 0)
        self.assertTrue(self.window.close())
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(pool.wait_for_done_calls, 0)

    def test_submission_worker_refuses_main_close_without_retry_or_cancel(self) -> None:
        service = _BlockingSubmissionService()
        self._release_events.append(service.release)
        pool = _RecordingThreadPool(self.window)
        self.window._submission_thread_pool = pool
        request = NewProjectSubmissionRequest(
            profile(save_password=False),
            "MoleculeA",
            "MoleculeA.xyz",
            ProjectStepKind.MOLECULE_OPT,
            _minimal_input_plan(),
            PASSWORD,
        )
        dependencies = _ProjectSubmissionDependencies(
            unittest.mock.Mock(),
            unittest.mock.Mock(),
            unittest.mock.Mock(),
            unittest.mock.Mock(),
            service,
        )

        self.window._start_submission(dependencies, request)
        self.window._start_submission(dependencies, request)
        self._wait_until(service.started.is_set)
        self.assertFalse(self.window.close())
        self.application.processEvents()

        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._submission_running)
        self.assertFalse(self.window._projects_action.isEnabled())
        self.assertEqual(len(self.window._submission_workers), 1)
        self.assertEqual(service.calls, 1)
        self.assertEqual(service.cancel_calls, 0)
        self.assertFalse(service.release.is_set())
        self.assertEqual(pool.wait_for_done_calls, 0)
        self._assert_shutdown_warning()

        service.release.set()
        self._wait_until(lambda: not self.window._submission_running)
        self._wait_until(lambda: pool.activeThreadCount() == 0)
        self.assertEqual(service.calls, 1)
        self.assertTrue(self.window._projects_action.isEnabled())
        self.assertTrue(self.window.close())
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(pool.wait_for_done_calls, 0)

    def test_normal_main_close_has_no_guard_or_delay(self) -> None:
        self.assertTrue(self.window.close())
        self.application.processEvents()

        self.assertFalse(self.window.isVisible())
        self.warning.assert_not_called()

    def test_process_exits_with_permanently_blocked_refresh(self) -> None:
        code = """
import threading
from unittest.mock import Mock
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from moltage.app.project_recovery import ProjectDiscoveryResult
from moltage.gui.projects_dialog import CalculationProjectsDialog
from phase2b1_test_support import MemorySecretStore
from test_project_recovery import TEST_PROFILE
from tools.molecule_viewer_demo import MoleculeViewerDemo

class BlockedRefresh:
    def __init__(self):
        self.started = threading.Event()
    def discover_and_refresh(self, *args, **kwargs):
        self.started.set()
        threading.Event().wait()

app = QApplication([])
window = MoleculeViewerDemo()
service = BlockedRefresh()
dialog = CalculationProjectsDialog(
    (TEST_PROFILE,), TEST_PROFILE.profile_id, service,
    MemorySecretStore(), Mock(), window,
)
dialog._password_for = lambda profile: (True, "synthetic-secret")
window._projects_dialog = dialog
window.show()
dialog.show()
dialog._discover()
assert service.started.wait(timeout=2)
assert window.close()
assert not dialog.has_active_remote_operation()
window.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
print("CLOSED_WITHOUT_WAIT")
"""
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CLOSED_WITHOUT_WAIT", result.stdout)


if __name__ == "__main__":
    unittest.main()
