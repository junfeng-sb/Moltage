"""Desktop application for molecular viewing and Moltage workflows."""

import math
import logging
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import (
    QEventLoop,
    QMimeData,
    QRect,
    QSize,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QSpinBox,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from moltage.aims.geometry_writer import write_geometry_in
from moltage.aims.input_bundle import (
    AimsOptimizationInputPlan,
)
from moltage.aims.optimization_settings import AimsOptimizationSettings
from moltage.aims.transport_convergence_bundle import (
    TransportConvergenceInputPlan,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.app.connection_service import (
    ServerConnectionService,
)
from moltage.app.input_export import (
    AimsInputExportRequest,
    AimsInputExportService,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.package_resources import (
    application_legal_document_path,
    application_resource_path,
)
from moltage.app.project_management import ProjectManagementService
from moltage.app.project_geometry import (
    ProjectGeometryViewResult,
    ProjectGeometryViewService,
)
from moltage.app.project_orbital_cube import (
    ProjectOrbitalCubeArtifact,
    ProjectOrbitalCubeBinding,
    ProjectOrbitalCubeLoadRequest,
    ProjectOrbitalCubeLoadResult,
    ProjectOrbitalCubeService,
)
from moltage.app.imported_start import (
    ImportedContactAuContext,
    ImportedContactAuEligibilityError,
    ImportedTransportStartContext,
    applied_contact_au_context,
    imported_contact_au_context,
)
from moltage.app.paths import (
    ApplicationDataPathError,
    density_results_path,
    known_hosts_path,
    local_project_index_path,
    migrate_legacy_application_data,
    server_profiles_path,
    submission_lifecycle_log_path,
    user_view_preferences_path,
)
from moltage.app.project_planning import (
    StartStepAdvice,
    recommend_start_step,
)
from moltage.app.project_recovery import (
    ProjectRecoveryService,
    ProjectRecoverySnapshot,
)
from moltage.app.project_submission import (
    ExistingProjectStepSubmissionRequest,
    NewProjectSubmissionRequest,
    ProjectSubmissionResult,
    ProjectSubmissionError,
    ProjectSubmissionService,
    SUBMISSION_LIFECYCLE_LOGGER_NAME,
    TransportConvergenceSubmissionRequest,
    record_submission_lifecycle_event,
)
from moltage.app.orca_recovery import OrcaRecoveryService
from moltage.app.orca_wbl import OrcaWblService
from moltage.app.orca_submission import (
    OrcaFrequencySubmissionRequest,
    OrcaOptimizationSubmissionRequest,
    OrcaSubmissionResult,
    OrcaSubmissionService,
)
from moltage.app.task_restart import (
    ProjectTaskRestartDraft,
    ProjectTaskRestartService,
)
from moltage.app.server_profiles import (
    ServerProfileRepository,
    ServerProfileService,
)
from moltage.app.user_view_preferences import (
    PersistedOrbitalLighting,
    UserViewPreferencesError,
    UserViewPreferencesRepository,
)
from moltage.app.transport_convergence import (
    TransportConvergenceContext,
    TransportConvergenceEligibilityError,
)
from moltage.app.transport_submission import (
    Step4SettingsRetryRequest,
    Step4SubmissionRequest,
    TransportSubmissionResult,
    TransportWorkflowSubmissionService,
)
from moltage.domain.anchor import AnchorCandidate, AnchorKind
from moltage.domain.au_pyramid import DEFAULT_AU_PYRAMID_LAYERS
from moltage.domain.au_lattice_extension import AuLatticeExtensionSite
from moltage.domain.bond_display import (
    BondDisplayOrder,
    ConnectivitySource,
    extend_bond_display_orders,
    remap_bond_display_orders,
    single_bond_display_orders,
    validate_bond_display_orders,
)
from moltage.domain.calculation_project import (
    CalculationWorkflowKind,
    ProjectStepKind,
    ProjectStepState,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.electrode import (
    AppliedElectrodePlacement,
    ElectrodeContactSite,
    ElectrodePlacementProposal,
)
from moltage.domain.junction import AppliedAuPlacement, AuPlacementProposal
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.gui.cluster_execution_dialog import (
    ClusterExecutionSettingsDialog,
)
from moltage.gui.geometry_history import (
    GeometryEditHistory,
    GeometryEditState,
)
from moltage.gui.aitranss_dialog import AitranssStep4Dialog
from moltage.gui.bond_detection_dialog import (
    MAXIMUM_BOND_THRESHOLD_FACTOR,
    MINIMUM_BOND_THRESHOLD_FACTOR,
    BondDetectionDialog,
)
from moltage.gui.email_notifications_dialog import (
    EmailNotificationsDialog,
)
from moltage.gui.optimization_dialog import (
    AimsOptimizationSettingsDialog,
)
from moltage.gui.orca_dialogs import (
    OrcaFrequencySettingsDialog,
    OrcaOptimizationSettingsDialog,
    OrcaSubmissionConfirmationDialog,
    OrcaSubmissionWorker,
)
from moltage.gui.orca_wbl_view import OrcaWblTransmissionView
from moltage.gui.project_submission import (
    NewCalculationProjectDialog,
    ProjectSubmissionWorker,
    SubmissionConfirmationDialog,
    submission_error_presentation,
    step_label,
)
from moltage.gui.input_export import AimsInputExportWorker
from moltage.gui.project_orbital_cube import ProjectOrbitalCubeWorker
from moltage.gui.projects_dialog import (
    CalculationProjectsDialog,
    Step2ContinuationConfirmationDialog,
)
from moltage.gui.server_profiles_dialog import (
    create_server_profiles_dialog,
)
from moltage.gui.theme import (
    DEFAULT_THEME_MANAGER,
    apply_default_theme,
    themed_icon,
)
from moltage.gui.transport_convergence_dialog import (
    Step3SubmissionConfirmationDialog,
    TransportConvergenceSettingsDialog,
)
from moltage.gui.transport_submission import (
    AitranssPreflightWorker,
    Step4SettingsRetryWorker,
    Step4SubmissionWorker,
)
from moltage.gui.view_settings_dialog import ViewSettingsDialog
from moltage.gui.periodic_table_dialog import PeriodicTableDialog
from moltage.gui.tight_binding_workspace import TightBindingWorkspace
from moltage.gui.transmission_view import TransmissionView
from moltage.gui.view_export import (
    ViewExportDialog,
    ViewExportError,
    save_view_image,
)
from moltage.gui.workspace_tabs import (
    GeometryWorkspaceIdentity,
    LocalGeometryWorkspaceIdentity,
    ManagedGeometryWorkspaceIdentity,
    OrcaWblWorkspaceIdentity,
    OrcaWblWorkspaceRequest,
    TransmissionWorkspaceIdentity,
    TransmissionWorkspaceRequest,
    WorkspaceKind,
)
from moltage.orca.batch import render_orca_submit_script
from moltage.orca.input_writer import (
    render_orca_frequency_input,
    render_orca_optimization_input,
)
from moltage.junction.apply_placement import (
    AuPlacementApplicationError,
    apply_au_placements,
)
from moltage.junction.au_placement import (
    AuPlacementParameters,
    angle_reference_atom_indices,
    propose_au_placements,
)
from moltage.junction.electrode_builder import (
    ElectrodeBuilderError,
    apply_electrode_placement,
    eligible_electrode_contact_sites,
    propose_electrode_placement,
)
from moltage.junction.electrode_lattice_interaction import (
    ElectrodeLatticeInteractionError,
    LatticeExtensionAvailability,
    LatticeExtensionInteractionCandidate,
    add_lattice_extension_to_working_geometry,
    interaction_candidates_for_working_geometry,
)
from moltage.junction.electrode_surface import propose_electrode_surfaces
from moltage.junction.placement_defaults import (
    AnchorPlacementDefaults,
    PlacementDefaultsError,
    load_default_au_placement_defaults,
    load_default_dicyano_cyano_n_placement_defaults,
    placement_defaults_for_anchor,
)
from moltage.remote.known_hosts import (
    HostKeyInfo,
    KnownHostStore,
    UnknownHostKey,
)
from moltage.remote.aitranss_discovery import (
    AitranssDiscoveryError,
    AitranssDiscoveryResult,
)
from moltage.remote.paramiko_executor import ParamikoRemoteExecutor
from moltage.remote.secrets import WindowsCredentialSecretStore
from moltage.structure.anchor_detector import detect_anchors
from moltage.structure.atom_editing import (
    AtomEditingError,
    delete_atom,
    replace_atom,
)
from moltage.structure.connectivity import (
    DEFAULT_CONNECTIVITY_MULTIPLIER,
    UnsupportedElementError,
    infer_connectivity,
)
from moltage.structure.covalent_radii import (
    CovalentRadiiError,
    load_default_covalent_radii,
)
from moltage.structure.cube import (
    CubeCoordinateUnit,
    CubeScalarField,
    CubeSourceKind,
    recommend_cube_coordinate_unit,
)
from moltage.structure.geometry_loader import (
    SUPPORTED_GEOMETRY_SUFFIXES,
    has_supported_geometry_extension,
    load_geometry,
)
from moltage.structure.vdw_radii import (
    VdwRadiiError,
    load_default_vdw_radii,
)
from moltage.visualization.molecule_scene import (
    ATOM_RADIUS_SCALE,
    ELEMENT_COLORS_RGB,
    AngleAnnotation,
    DistanceAnnotation,
    LatticeExtensionPreviewGuide,
    MeasurementOverlayAnnotation,
    PreviewAtom,
    PreviewPickTarget,
    TorsionGizmo,
    VisualizationError,
)
from moltage.visualization.molecule_viewer import MoleculeViewerWidget
from moltage.visualization.orbital_surface import (
    OrbitalSurfacePreferences,
    OrbitalSurfaceResolution,
    initial_orbital_surface_preferences,
)
from moltage.visualization.bond_torsion import (
    BondTorsionError,
    BondTorsionSession,
    NonRotatableEdgeError,
    normalize_signed_degrees,
    structure_coordinates,
)
from moltage.visualization.measurements import (
    AngleMeasurement,
    DistanceMeasurement,
    MeasurementError,
    MeasurementRecord,
    MeasurementSession,
)
from moltage.visualization.view_preferences import ViewPreferences


_SUPPORTED_GEOMETRY_PATTERNS = tuple(
    f"*{suffix}" for suffix in SUPPORTED_GEOMETRY_SUFFIXES
)
_GEOMETRY_FILE_DIALOG_FILTER = (
    f"Supported Geometry ({' '.join(_SUPPORTED_GEOMETRY_PATTERNS)});;"
    + ";;".join(
        f"{suffix[1:].replace('_', ' ').upper()} ({pattern})"
        for suffix, pattern in zip(
            SUPPORTED_GEOMETRY_SUFFIXES,
            _SUPPORTED_GEOMETRY_PATTERNS,
            strict=True,
        )
    )
)

BLOCKED_AU_EXTENSION_COLOR_RGB = (255, 48, 64)
AVAILABLE_AU_EXTENSION_GUIDE_COLOR_RGB = (0, 120, 215)


DISTANCE_PARAMETER_LABELS = {
    AnchorKind.NCS: "Au-S",
    AnchorKind.SMe: "Au-S",
    AnchorKind.PYRIDINE_N: "Au-N",
    AnchorKind.NH2: "Au-N",
    AnchorKind.SH: "Au-S",
    AnchorKind.ALKYNYL_C: "Au-C",
    AnchorKind.CYANO_N: "Au-N",
    AnchorKind.DICYANO_C: "Au-C",
}

ANGLE_PARAMETER_LABELS = {
    AnchorKind.NCS: "C-S-Au",
    AnchorKind.SMe: "C(Me)-S-Au",
    AnchorKind.PYRIDINE_N: "Au-N-C1/C2 (equal)",
    AnchorKind.NH2: "R-N-Au",
    AnchorKind.SH: "R-S-Au",
    AnchorKind.ALKYNYL_C: "C(adj)-C(bind)-Au",
    AnchorKind.CYANO_N: "C-N-Au",
    AnchorKind.DICYANO_C: "R-C-Au",
}


class _SubmissionBackendOutcome(StrEnum):
    NONE = "NONE"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class _SubmissionTerminalPresentation(StrEnum):
    NONE = "NONE"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    INTERNAL_PRESENTATION_FAILURE = "INTERNAL_PRESENTATION_FAILURE"


class _ViewerPickMode(StrEnum):
    NORMAL = "NORMAL"
    DISTANCE = "DISTANCE"
    ANGLE = "ANGLE"
    BOND_ROTATION = "BOND_ROTATION"
    DELETE_ATOM = "DELETE_ATOM"
    REPLACE_ATOM = "REPLACE_ATOM"
    AU_PLACEMENT = "AU_PLACEMENT"
    AU_LATTICE_EXTENSION = "AU_LATTICE_EXTENSION"
    ORCA_WBL_CONTACT = "ORCA_WBL_CONTACT"


@dataclass(slots=True)
class _AnchorSiteControls:
    anchor: AnchorCandidate
    checkbox: QCheckBox
    parameter_widget: QWidget | None
    distance_input: QLineEdit | None
    angle_input: QLineEdit | None


@dataclass(slots=True)
class _ProjectSubmissionDependencies:
    profile_repository: ServerProfileRepository
    profile_service: ServerProfileService
    secret_store: WindowsCredentialSecretStore
    known_hosts: KnownHostStore
    submission_service: ProjectSubmissionService
    recovery_service: ProjectRecoveryService | None = None
    connection_service: ServerConnectionService | None = None
    transport_submission_service: TransportWorkflowSubmissionService | None = None
    project_management_service: ProjectManagementService | None = None
    project_geometry_service: ProjectGeometryViewService | None = None
    project_task_restart_service: ProjectTaskRestartService | None = None
    project_orbital_cube_service: ProjectOrbitalCubeService | None = None
    input_export_service: AimsInputExportService | None = None
    orca_submission_service: OrcaSubmissionService | None = None
    orca_recovery_service: OrcaRecoveryService | None = None
    orca_wbl_service: OrcaWblService | None = None


@dataclass(slots=True)
class _GeometryWorkspace:
    """One live Geometry presentation and all of its session-local state."""

    runtime_id: UUID
    identity: GeometryWorkspaceIdentity | None
    display_title: str
    content: QWidget
    viewer_stack: QStackedWidget
    viewer: MoleculeViewerWidget
    empty_state_widget: QWidget
    open_geometry_button: QPushButton
    orbital_controls: QFrame
    orbital_buttons_layout: QHBoxLayout
    measurement_panel: QFrame
    measurement_heading: QLabel
    measurement_empty_state: QLabel
    measurement_table: QTableWidget
    status_panel: QWidget
    picked_atom_label: QLabel
    operation_label: QLabel
    anchor_highlight_legend: QWidget
    builder_widget: QWidget
    anchor_sites_layout: QVBoxLayout
    done_button: QPushButton
    save_geometry_button: QPushButton
    electrode_section: QFrame
    electrode_message_label: QLabel
    electrode_sites_widget: QWidget
    electrode_sites_layout: QVBoxLayout
    electrode_layers_input: QSpinBox
    electrode_cost_warning: QLabel
    electrode_done_button: QPushButton
    electrode_extension_button: QPushButton
    source_path: Path | None = None
    source_structure: MolecularStructure | None = None
    source_connectivity: Connectivity | None = None
    source_connectivity_source: ConnectivitySource = ConnectivitySource.INFERRED
    source_bond_display_orders: tuple[BondDisplayOrder, ...] = ()
    structure: MolecularStructure | None = None
    connectivity: Connectivity | None = None
    connectivity_source: ConnectivitySource = ConnectivitySource.INFERRED
    bond_display_orders: tuple[BondDisplayOrder, ...] = ()
    covalent_radii: Mapping[str, float] | None = None
    vdw_radii: Mapping[str, float] | None = None
    anchors: tuple[AnchorCandidate, ...] = ()
    site_controls: dict[AnchorCandidate, _AnchorSiteControls] | None = None
    current_proposals: tuple[AuPlacementProposal, ...] = ()
    applied_result: AppliedAuPlacement | None = None
    electrode_sites: tuple[ElectrodeContactSite, ...] = ()
    electrode_site_controls: dict[ElectrodeContactSite, QCheckBox] | None = None
    electrode_current_proposal: ElectrodePlacementProposal | None = None
    applied_electrode_result: AppliedElectrodePlacement | None = None
    confirmed: bool = False
    recovery_snapshot: ProjectRecoverySnapshot | None = None
    recovery_profile: ServerProfile | None = None
    measurement_session: MeasurementSession | None = None
    measurement_selection: tuple[int, ...] = ()
    pick_mode: _ViewerPickMode = _ViewerPickMode.NORMAL
    torsion_session: BondTorsionSession | None = None
    torsion_reference_direction: tuple[float, float, float] | None = None
    torsion_drag_start_structure: MolecularStructure | None = None
    torsion_drag_start_angle: float = 0.0
    geometry_undo_history: GeometryEditHistory | None = None
    replacement_element: str | None = None
    submission_step2_refresh_required: bool = False
    submission_step3_refresh_required: bool = False
    builder_visible: bool = True
    read_only: bool = False
    coordinate_only: bool = False
    restart_draft: ProjectTaskRestartDraft | None = None
    scalar_field: CubeScalarField | None = None
    orbital_preferences: OrbitalSurfacePreferences | None = None
    orbital_binding: ProjectOrbitalCubeBinding | None = None
    orbital_cube_artifacts: tuple[ProjectOrbitalCubeArtifact, ...] = ()
    orbital_buttons: dict[str, QPushButton] | None = None
    active_orbital_filename: str | None = None
    orbital_load_running: bool = False
    lattice_extension_candidates: tuple[
        LatticeExtensionInteractionCandidate, ...
    ] = ()
    lattice_extension_cache_valid: bool = False
    lattice_extension_hover_identity: AuLatticeExtensionSite | None = None
    lattice_extension_submitted_immutable: bool = False

    @property
    def kind(self) -> WorkspaceKind:
        return WorkspaceKind.GEOMETRY


@dataclass(slots=True)
class _TransmissionWorkspace:
    """One live Transmission presentation for one authoritative result."""

    runtime_id: UUID
    identity: TransmissionWorkspaceIdentity
    display_title: str
    content: TransmissionView

    @property
    def kind(self) -> WorkspaceKind:
        return WorkspaceKind.TRANSMISSION


@dataclass(slots=True)
class _OrcaWblWorkspace:
    """One live view of a verified persisted ORCA WBL result."""

    runtime_id: UUID
    identity: OrcaWblWorkspaceIdentity
    display_title: str
    content: OrcaWblTransmissionView

    @property
    def kind(self) -> WorkspaceKind:
        return WorkspaceKind.ORCA_WBL


@dataclass(slots=True)
class _DensityWorkspace:
    runtime_id: UUID
    identity: UUID
    display_title: str
    content: QWidget

    @property
    def kind(self):
        return WorkspaceKind.DENSITY


@dataclass(slots=True)
class _DensityResultWorkspace:
    runtime_id: UUID
    identity: tuple[UUID, int]
    display_title: str
    content: QWidget

    @property
    def kind(self):
        return WorkspaceKind.DENSITY_RESULT


@dataclass(slots=True)
class _TightBindingWorkspace:
    runtime_id: UUID
    identity: UUID
    display_title: str
    content: TightBindingWorkspace

    @property
    def kind(self):
        return WorkspaceKind.TIGHT_BINDING


_Workspace = (
    _GeometryWorkspace
    | _TransmissionWorkspace
    | _OrcaWblWorkspace
    | _DensityWorkspace
    | _DensityResultWorkspace
    | _TightBindingWorkspace
)


class _UpdateLogDialog(QDialog):
    """Read-only presentation of the bundled user-facing update log."""

    def __init__(self, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("updateLogDialog")
        self.setWindowTitle("Update Log")
        self.resize(620, 420)

        layout = QVBoxLayout(self)
        log_view = QPlainTextEdit(self)
        log_view.setObjectName("updateLogText")
        log_view.setReadOnly(True)
        log_view.setPlainText(content)
        layout.addWidget(log_view, stretch=1)

        close_button = QPushButton("Close", self)
        close_button.setObjectName("closeUpdateLog")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)


@dataclass(frozen=True, slots=True)
class _InferredConnectivitySnapshot:
    """Exact per-workspace graph state captured for one modal preview."""

    runtime_id: UUID
    source_connectivity: Connectivity
    source_bond_display_orders: tuple[BondDisplayOrder, ...]
    connectivity: Connectivity
    bond_display_orders: tuple[BondDisplayOrder, ...]
    anchors: tuple[AnchorCandidate, ...]


class MoleculeViewerDemo(QMainWindow):
    """Coordinate loading, Au preview/application, export, and inspection."""

    def __init__(
        self,
        user_view_preferences_repository: (
            UserViewPreferencesRepository | None
        ) = None,
    ) -> None:
        super().__init__()
        self._user_view_preferences_repository = (
            user_view_preferences_repository
        )
        self._persisted_orbital_lighting = (
            user_view_preferences_repository.load()
            if user_view_preferences_repository is not None
            else PersistedOrbitalLighting()
        )
        self._placement_defaults: Mapping[
            AnchorKind, AnchorPlacementDefaults
        ] = (
            load_default_au_placement_defaults()
        )
        self._dicyano_cyano_n_defaults = (
            load_default_dicyano_cyano_n_placement_defaults()
        )
        self._submission_thread_pool = QThreadPool(self)
        self._submission_thread_pool.setMaxThreadCount(1)
        self._orbital_thread_pool = QThreadPool(self)
        self._orbital_thread_pool.setMaxThreadCount(1)
        self._orbital_workers: set[ProjectOrbitalCubeWorker] = set()
        self._orbital_worker_origins: dict[ProjectOrbitalCubeWorker, UUID] = {}
        self._submission_workers: set[ProjectSubmissionWorker | OrcaSubmissionWorker] = set()
        self._input_export_workers: set[AimsInputExportWorker] = set()
        self._input_export_worker_origins: dict[AimsInputExportWorker, UUID] = {}
        self._input_export_running = False
        self._transport_workers: set[object] = set()
        self._transport_operation_running = False
        self._pending_transport_dependencies: (
            _ProjectSubmissionDependencies | None
        ) = None
        self._pending_transport_profile: ServerProfile | None = None
        self._pending_transport_project_id: UUID | None = None
        self._submission_running = False
        self._submission_retry_pending = False
        self._submission_trust_retry_used = False
        self._submission_backend_outcome = _SubmissionBackendOutcome.NONE
        self._submission_backend_result: ProjectSubmissionResult | OrcaSubmissionResult | None = None
        self._submission_terminal_presentation = (
            _SubmissionTerminalPresentation.NONE
        )
        self._submission_terminal_callback_active = False
        self._submission_finished_pending = False
        self._pending_submission_request: (
            NewProjectSubmissionRequest
            | ExistingProjectStepSubmissionRequest
            | TransportConvergenceSubmissionRequest
            | OrcaOptimizationSubmissionRequest
            | OrcaFrequencySubmissionRequest
            | None
        ) = None
        self._pending_submission_dependencies: (
            _ProjectSubmissionDependencies | None
        ) = None
        self._submission_origin_workspace_id: UUID | None = None
        self._transport_origin_workspace_id: UUID | None = None
        self._projects_dialog: CalculationProjectsDialog | None = None
        self._projects_profile_repository: ServerProfileRepository | None = None
        self._connectivity_multiplier = DEFAULT_CONNECTIVITY_MULTIPLIER
        self._view_preferences = ViewPreferences()
        self._workspaces_by_widget: dict[QWidget, _Workspace] = {}
        self._geometry_workspaces_by_identity: dict[
            GeometryWorkspaceIdentity, _GeometryWorkspace
        ] = {}
        self._transmission_workspaces_by_identity: dict[
            TransmissionWorkspaceIdentity, _TransmissionWorkspace
        ] = {}
        self._orca_wbl_workspaces_by_identity: dict[
            OrcaWblWorkspaceIdentity, _OrcaWblWorkspace
        ] = {}
        self._density_result_workspaces_by_identity: dict[
            tuple[UUID, int], _DensityResultWorkspace
        ] = {}
        self._retired_geometry_workspaces: dict[UUID, _GeometryWorkspace] = {}
        self._bound_geometry_workspace: _GeometryWorkspace | None = None
        self._presented_workspace: _Workspace | None = None
        self._workspace_switch_in_progress = False
        self._orca_wbl_contact_selection_loop: QEventLoop | None = None
        self._orca_wbl_contact_selection_result: int | None = None

        self.setWindowTitle("Moltage")
        self.resize(1280, 900)
        self.setMinimumSize(900, 650)
        self.setAcceptDrops(True)

        self._workspace_tabs = QTabWidget(self)
        self._workspace_tabs.setObjectName("workspaceTabs")
        self._workspace_tabs.setTabsClosable(True)
        self._workspace_tabs.setMovable(False)
        self._workspace_tabs.setDocumentMode(True)
        self._workspace_tabs.currentChanged.connect(
            self._active_workspace_changed
        )
        self._workspace_tabs.tabCloseRequested.connect(self._close_workspace_tab)
        self.setCentralWidget(self._workspace_tabs)

        self._build_au_tool_dock()
        initial_workspace = self._create_geometry_workspace(
            identity=None,
            display_title="Geometry",
            select=True,
        )
        self._bind_geometry_workspace(initial_workspace)
        self._build_top_toolbar()
        self._measurement_escape_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape),
            self,
        )
        self._measurement_escape_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._measurement_escape_shortcut.activated.connect(
            self._handle_viewer_escape
        )
        self._route_active_workspace()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept URL drops so unsupported local entries can fail explicitly."""

        mime_data = event.mimeData()
        if mime_data.hasUrls() and mime_data.urls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """Open supported local structure files through the canonical path."""

        mime_data = event.mimeData()
        if not mime_data.hasUrls() or not mime_data.urls():
            event.ignore()
            return
        accepted_paths, rejected_entries = self._classify_geometry_drop(
            mime_data
        )
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        for source_path in accepted_paths:
            self._open_local_geometry(source_path)
        if rejected_entries:
            self._show_rejected_geometry_drop(
                rejected_entries,
                opened_count=len(accepted_paths),
            )

    @staticmethod
    def _classify_geometry_drop(
        mime_data: QMimeData,
    ) -> tuple[tuple[Path, ...], tuple[str, ...]]:
        """Separate supported local files from explicitly rejected entries."""

        accepted_paths: list[Path] = []
        rejected_entries: list[str] = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                rejected_entries.append(f"{url.toString()} — not a local file")
                continue
            local_text = url.toLocalFile()
            source_path = Path(local_text)
            if not source_path.is_file():
                rejected_entries.append(f"{local_text} — not a regular file")
                continue
            if not has_supported_geometry_extension(source_path):
                rejected_entries.append(f"{local_text} — unsupported file type")
                continue
            accepted_paths.append(source_path)
        return tuple(accepted_paths), tuple(rejected_entries)

    def _show_rejected_geometry_drop(
        self,
        rejected_entries: tuple[str, ...],
        *,
        opened_count: int,
    ) -> None:
        """Report one bounded warning instead of silently skipping entries."""

        visible_entries = rejected_entries[:10]
        rejected_text = "\n".join(f"- {entry}" for entry in visible_entries)
        hidden_count = len(rejected_entries) - len(visible_entries)
        if hidden_count:
            rejected_text += f"\n- ... and {hidden_count} more"
        title = (
            "Some files were not opened"
            if opened_count
            else "No structure files were opened"
        )
        QMessageBox.warning(
            self,
            title,
            "Moltage accepts local XYZ (.xyz), MOL V2000 (.mol), "
            "FHI-aims geometry (.in or .next_step), and Cube "
            "(.cube or .cub) files."
            f"\n\nRejected:\n{rejected_text}",
        )

    def _create_measurement_table(
        self,
        parent: QWidget,
        central_layout: QVBoxLayout,
        runtime_id: UUID,
    ) -> tuple[QFrame, QLabel, QLabel, QTableWidget]:
        panel = QFrame(parent)
        panel.setObjectName("measurementPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(4, 3, 4, 3)
        panel_layout.setSpacing(2)
        heading = QLabel("Measurements", panel)
        heading_layout = QHBoxLayout()
        heading_layout.addWidget(heading)
        heading_layout.addStretch(1)
        clear_button = QPushButton("Clear", panel)
        clear_button.setObjectName("clearMeasurements")
        clear_button.setToolTip("Clear all measurements in this Geometry tab")
        clear_button.clicked.connect(
            lambda _checked=False, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._clear_measurements,
                )
            )
        )
        heading_layout.addWidget(clear_button)
        panel_layout.addLayout(heading_layout)

        empty_state = QLabel("No measurements", panel)
        empty_state.setObjectName("measurementEmptyState")
        panel_layout.addWidget(empty_state)

        table = QTableWidget(0, 4, panel)
        table.setObjectName("measurementTable")
        table.setAccessibleName("Measurements")
        table.setHorizontalHeaderLabels(("Type", "Atoms", "Value", "Unit"))
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(False)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            self._show_measurement_context_menu
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setMinimumHeight(72)
        table.setMaximumHeight(142)
        panel_layout.addWidget(table)
        central_layout.addWidget(panel)
        # Keep the table footprint stable after its first result opens the panel.
        empty_state.setVisible(False)
        panel.setMinimumHeight(panel.sizeHint().height())
        table.setVisible(False)
        empty_state.setVisible(True)
        panel.setVisible(False)
        table.customContextMenuRequested.disconnect()
        table.customContextMenuRequested.connect(
            lambda position, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._show_measurement_context_menu,
                    position,
                )
            )
        )
        return panel, heading, empty_state, table

    def _create_geometry_workspace(
        self,
        *,
        identity: GeometryWorkspaceIdentity | None,
        display_title: str,
        select: bool,
    ) -> _GeometryWorkspace:
        runtime_id = uuid4()
        content = QWidget(self._workspace_tabs)
        content.setObjectName("geometryWorkspace")
        central_layout = QVBoxLayout(content)
        central_layout.setContentsMargins(10, 10, 10, 8)
        central_layout.setSpacing(8)

        viewer_stack = QStackedWidget(content)
        viewer_stack.setObjectName("geometryViewerStack")
        viewer = MoleculeViewerWidget(viewer_stack)
        viewer.set_view_preferences(self._view_preferences)
        viewer.atom_picked.connect(
            lambda atom_index, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._report_picked_atom,
                    atom_index,
                )
            )
        )
        viewer.bond_picked.connect(
            lambda atom_a, atom_b, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._bond_rotation_edge_picked,
                    atom_a,
                    atom_b,
                )
            )
        )
        viewer.preview_target_hovered.connect(
            lambda identity, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._lattice_extension_target_hovered,
                    identity,
                )
            )
        )
        viewer.preview_target_clicked.connect(
            lambda identity, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._lattice_extension_target_clicked,
                    identity,
                )
            )
        )
        viewer.preview_target_cleared.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._clear_lattice_extension_hover,
            )
        )
        viewer.torsion_drag_started.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._torsion_drag_started,
            )
        )
        viewer.torsion_drag_changed.connect(
            lambda angle, workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._torsion_drag_changed,
                angle,
            )
        )
        viewer.torsion_drag_finished.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._torsion_drag_finished,
            )
        )
        viewer.torsion_side_switch_requested.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._switch_torsion_side,
            )
        )
        viewer.torsion_numeric_edit_requested.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._start_torsion_numeric_edit,
            )
        )
        viewer.torsion_numeric_commit_requested.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._commit_torsion_numeric_edit,
            )
        )
        viewer.torsion_numeric_cancel_requested.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._cancel_torsion_numeric_edit,
            )
        )
        viewer_stack.addWidget(viewer)

        empty_state_widget = QWidget(viewer_stack)
        empty_state_widget.setObjectName("emptyGeometryWorkspace")
        empty_state_layout = QVBoxLayout(empty_state_widget)
        empty_state_layout.setContentsMargins(24, 24, 24, 24)
        empty_state_layout.addStretch(1)
        empty_state_message = QLabel(
            "Open a geometry file (.xyz, .mol, .in, .next_step) or Cube "
            "file (.cube, .cub) to begin.",
            empty_state_widget,
        )
        empty_state_message.setObjectName("emptyGeometryMessage")
        empty_state_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_state_message.setWordWrap(True)
        empty_state_layout.addWidget(empty_state_message)
        open_geometry_button = QPushButton("Open...", empty_state_widget)
        open_geometry_button.setObjectName("emptyGeometryOpenButton")
        open_geometry_button.clicked.connect(
            lambda _checked=False: self._open_xyz()
        )
        empty_state_layout.addWidget(
            open_geometry_button,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        empty_state_layout.addStretch(1)
        viewer_stack.addWidget(empty_state_widget)
        viewer_stack.setCurrentWidget(empty_state_widget)

        orbital_controls = QFrame(content)
        orbital_controls.setObjectName("step1OrbitalCubeControls")
        orbital_controls_layout = QHBoxLayout(orbital_controls)
        orbital_controls_layout.setContentsMargins(4, 0, 4, 0)
        orbital_controls_layout.setSpacing(4)
        orbital_controls_layout.addStretch(1)
        orbital_label = QLabel("Step 1 orbitals:", orbital_controls)
        orbital_label.setObjectName("step1OrbitalCubeLabel")
        orbital_controls_layout.addWidget(orbital_label)
        orbital_buttons_widget = QWidget(orbital_controls)
        orbital_buttons_widget.setObjectName("step1OrbitalCubeButtons")
        orbital_buttons_layout = QHBoxLayout(orbital_buttons_widget)
        orbital_buttons_layout.setContentsMargins(0, 0, 0, 0)
        orbital_buttons_layout.setSpacing(3)
        orbital_controls_layout.addWidget(orbital_buttons_widget)
        orbital_controls.setVisible(False)
        central_layout.addWidget(orbital_controls)
        central_layout.addWidget(viewer_stack, stretch=1)

        (
            measurement_panel,
            measurement_heading,
            measurement_empty_state,
            measurement_table,
        ) = self._create_measurement_table(content, central_layout, runtime_id)

        status_panel = QWidget(content)
        status_panel.setObjectName("statusPanel")
        status_panel.setFixedHeight(76)
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(8, 5, 8, 3)
        status_layout.setSpacing(3)
        status_row = QHBoxLayout()
        picked_atom_label = QLabel("Selected atom: none", status_panel)
        status_row.addWidget(picked_atom_label)
        status_row.addStretch(1)
        anchor_highlight_legend = QWidget(status_panel)
        anchor_highlight_legend.setObjectName("electrodeHighlightLegend")
        anchor_highlight_layout = QHBoxLayout(anchor_highlight_legend)
        anchor_highlight_layout.setContentsMargins(0, 0, 0, 0)
        anchor_highlight_layout.setSpacing(4)
        _add_swatch_legend(
            anchor_highlight_layout,
            (255, 0, 255),
            "linker binding atom",
        )
        _add_swatch_legend(
            anchor_highlight_layout,
            (0, 220, 255),
            "existing Au attached to linker",
        )
        _add_swatch_legend(
            anchor_highlight_layout,
            ELEMENT_COLORS_RGB["Au"],
            "proposed virtual Au",
        )
        anchor_highlight_legend.setVisible(False)
        status_row.addWidget(anchor_highlight_legend)
        status_layout.addLayout(status_row)
        operation_label = QLabel("", status_panel)
        operation_label.setObjectName("operationOutput")
        operation_label.setWordWrap(True)
        operation_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        status_layout.addWidget(operation_label)
        status_panel.setVisible(False)
        central_layout.addWidget(status_panel)

        (
            builder_widget,
            anchor_sites_layout,
            done_button,
            save_geometry_button,
            electrode_section,
            electrode_message_label,
            electrode_sites_widget,
            electrode_sites_layout,
            electrode_layers_input,
            electrode_cost_warning,
            electrode_done_button,
            electrode_extension_button,
        ) = self._create_electrode_builder_content(runtime_id)

        workspace = _GeometryWorkspace(
            runtime_id=runtime_id,
            identity=identity,
            display_title=display_title,
            content=content,
            viewer_stack=viewer_stack,
            viewer=viewer,
            empty_state_widget=empty_state_widget,
            open_geometry_button=open_geometry_button,
            orbital_controls=orbital_controls,
            orbital_buttons_layout=orbital_buttons_layout,
            measurement_panel=measurement_panel,
            measurement_heading=measurement_heading,
            measurement_empty_state=measurement_empty_state,
            measurement_table=measurement_table,
            status_panel=status_panel,
            picked_atom_label=picked_atom_label,
            operation_label=operation_label,
            anchor_highlight_legend=anchor_highlight_legend,
            builder_widget=builder_widget,
            anchor_sites_layout=anchor_sites_layout,
            done_button=done_button,
            save_geometry_button=save_geometry_button,
            electrode_section=electrode_section,
            electrode_message_label=electrode_message_label,
            electrode_sites_widget=electrode_sites_widget,
            electrode_sites_layout=electrode_sites_layout,
            electrode_layers_input=electrode_layers_input,
            electrode_cost_warning=electrode_cost_warning,
            electrode_done_button=electrode_done_button,
            electrode_extension_button=electrode_extension_button,
            site_controls={},
            electrode_site_controls={},
            measurement_session=MeasurementSession(),
            geometry_undo_history=GeometryEditHistory(5),
            orbital_buttons={},
        )
        self._workspaces_by_widget[content] = workspace
        if identity is not None:
            self._geometry_workspaces_by_identity[identity] = workspace
        self._electrode_builder_stack.addWidget(builder_widget)
        blocker = QSignalBlocker(self._workspace_tabs)
        self._workspace_tabs.addTab(content, display_title)
        del blocker
        self._set_workspace_tab_tooltip(workspace)
        if select:
            self._focus_workspace(workspace)
        return workspace

    def _create_electrode_builder_content(
        self,
        runtime_id: UUID,
    ) -> tuple[
        QWidget,
        QVBoxLayout,
        QPushButton,
        QPushButton,
        QFrame,
        QLabel,
        QWidget,
        QVBoxLayout,
        QSpinBox,
        QLabel,
        QPushButton,
        QPushButton,
    ]:
        dock_contents = QWidget(self._electrode_builder_stack)
        dock_layout = QVBoxLayout(dock_contents)
        dock_layout.setContentsMargins(10, 10, 10, 10)
        dock_layout.setSpacing(8)

        heading = QLabel("Detected anchor sites (select at most two)")
        heading.setObjectName("anchorSitesHeading")
        heading.setWordWrap(True)
        dock_layout.addWidget(heading)
        scroll_area = QScrollArea(dock_contents)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        sites_widget = QWidget(scroll_area)
        anchor_sites_layout = QVBoxLayout(sites_widget)
        anchor_sites_layout.setContentsMargins(0, 0, 0, 0)
        anchor_sites_layout.setSpacing(5)
        anchor_sites_layout.addWidget(QLabel("Load a geometry file first."))
        anchor_sites_layout.addStretch(1)
        scroll_area.setWidget(sites_widget)
        dock_layout.addWidget(scroll_area, stretch=1)

        done_button = QPushButton("Done")
        done_button.setObjectName("auDoneButton")
        done_button.clicked.connect(
            lambda _checked=False, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._confirm_current_proposals,
                )
            )
        )
        dock_layout.addWidget(done_button)
        save_geometry_button = QPushButton("Save geometry.in")
        save_geometry_button.setObjectName("saveGeometryButton")
        save_geometry_button.setEnabled(False)
        save_geometry_button.clicked.connect(
            lambda _checked=False, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._save_geometry_in,
                )
            )
        )
        dock_layout.addWidget(save_geometry_button)

        electrode_section = QFrame(dock_contents)
        electrode_section.setObjectName("sixLayerElectrodeSection")
        electrode_section.setFrameShape(QFrame.Shape.StyledPanel)
        electrode_layout = QVBoxLayout(electrode_section)
        electrode_layout.setContentsMargins(5, 5, 5, 5)
        electrode_layout.setSpacing(4)
        electrode_heading = QLabel("Canonical Au pyramids")
        electrode_heading.setObjectName("sixLayerElectrodeHeading")
        electrode_layout.addWidget(electrode_heading)
        layer_row = QWidget(electrode_section)
        layer_layout = QHBoxLayout(layer_row)
        layer_layout.setContentsMargins(0, 0, 0, 0)
        layer_layout.addWidget(QLabel("Pyramid layers:", layer_row))
        electrode_layers_input = QSpinBox(layer_row)
        electrode_layers_input.setObjectName("pyramidLayersInput")
        electrode_layers_input.setRange(2, 10)
        electrode_layers_input.setValue(DEFAULT_AU_PYRAMID_LAYERS)
        electrode_layers_input.valueChanged.connect(
            lambda value, workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._electrode_layers_changed,
                value,
            )
        )
        layer_layout.addWidget(electrode_layers_input)
        layer_layout.addStretch(1)
        electrode_layout.addWidget(layer_row)
        electrode_cost_warning = QLabel("", electrode_section)
        electrode_cost_warning.setObjectName("pyramidLayerCostWarning")
        electrode_cost_warning.setWordWrap(True)
        electrode_cost_warning.setVisible(False)
        electrode_layout.addWidget(electrode_cost_warning)
        electrode_message_label = QLabel("")
        electrode_message_label.setObjectName("sixLayerElectrodeMessage")
        electrode_message_label.setWordWrap(True)
        electrode_layout.addWidget(electrode_message_label)
        electrode_sites_widget = QWidget(electrode_section)
        electrode_sites_layout = QVBoxLayout(electrode_sites_widget)
        electrode_sites_layout.setContentsMargins(0, 0, 0, 0)
        electrode_sites_layout.setSpacing(3)
        electrode_layout.addWidget(electrode_sites_widget)
        electrode_done_button = QPushButton("Done")
        electrode_done_button.setObjectName("sixLayerElectrodeDoneButton")
        electrode_done_button.setEnabled(False)
        electrode_done_button.clicked.connect(
            lambda _checked=False, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._confirm_electrode_proposal,
                )
            )
        )
        electrode_layout.addWidget(electrode_done_button)
        electrode_extension_button = QPushButton("Extend Au(111) lattice")
        electrode_extension_button.setObjectName("extendAu111LatticeButton")
        electrode_extension_button.setCheckable(True)
        electrode_extension_button.setEnabled(False)
        electrode_extension_button.setToolTip(
            "Preview and add canonical Au(111) sites to the accepted electrodes"
        )
        electrode_extension_button.toggled.connect(
            lambda checked, workspace_id=runtime_id: (
                self._dispatch_geometry_event(
                    workspace_id,
                    self._lattice_extension_toggled,
                    checked,
                )
            )
        )
        electrode_layout.addWidget(electrode_extension_button)
        electrode_section.setVisible(False)
        dock_layout.addWidget(electrode_section)
        return (
            dock_contents,
            anchor_sites_layout,
            done_button,
            save_geometry_button,
            electrode_section,
            electrode_message_label,
            electrode_sites_widget,
            electrode_sites_layout,
            electrode_layers_input,
            electrode_cost_warning,
            electrode_done_button,
            electrode_extension_button,
        )

    def _bind_geometry_workspace(self, workspace: _GeometryWorkspace) -> None:
        """Bind legacy main-window handlers to exactly one Geometry workspace."""

        self._bound_geometry_workspace = workspace
        self._viewer = workspace.viewer
        self._torsion_numeric_input = workspace.viewer.torsion_angle_input
        self._measurement_panel = workspace.measurement_panel
        self._measurement_heading = workspace.measurement_heading
        self._measurement_empty_state = workspace.measurement_empty_state
        self._measurement_table = workspace.measurement_table
        self._status_panel = workspace.status_panel
        self._picked_atom_label = workspace.picked_atom_label
        self._operation_label = workspace.operation_label
        self._anchor_sites_layout = workspace.anchor_sites_layout
        self._done_button = workspace.done_button
        self._save_geometry_button = workspace.save_geometry_button
        self._electrode_section = workspace.electrode_section
        self._electrode_message_label = workspace.electrode_message_label
        self._electrode_sites_widget = workspace.electrode_sites_widget
        self._electrode_sites_layout = workspace.electrode_sites_layout
        self._electrode_layers_input = workspace.electrode_layers_input
        self._electrode_cost_warning = workspace.electrode_cost_warning
        self._electrode_done_button = workspace.electrode_done_button
        self._electrode_extension_button = workspace.electrode_extension_button
        self._source_path = workspace.source_path
        self._source_structure = workspace.source_structure
        self._source_connectivity = workspace.source_connectivity
        self._source_connectivity_source = workspace.source_connectivity_source
        self._source_bond_display_orders = workspace.source_bond_display_orders
        self._structure = workspace.structure
        self._connectivity = workspace.connectivity
        self._connectivity_source = workspace.connectivity_source
        self._bond_display_orders = workspace.bond_display_orders
        self._covalent_radii = workspace.covalent_radii
        self._vdw_radii = workspace.vdw_radii
        self._anchors = workspace.anchors
        self._site_controls = (
            workspace.site_controls
            if workspace.site_controls is not None
            else {}
        )
        self._current_proposals = workspace.current_proposals
        self._applied_result = workspace.applied_result
        self._electrode_sites = workspace.electrode_sites
        self._electrode_site_controls = (
            workspace.electrode_site_controls
            if workspace.electrode_site_controls is not None
            else {}
        )
        self._electrode_current_proposal = workspace.electrode_current_proposal
        self._applied_electrode_result = workspace.applied_electrode_result
        self._confirmed = workspace.confirmed
        self._recovery_snapshot = workspace.recovery_snapshot
        self._recovery_profile = workspace.recovery_profile
        self._measurement_session = (
            workspace.measurement_session
            if workspace.measurement_session is not None
            else MeasurementSession()
        )
        self._measurement_selection = workspace.measurement_selection
        self._pick_mode = workspace.pick_mode
        self._torsion_session = workspace.torsion_session
        self._torsion_reference_direction = workspace.torsion_reference_direction
        self._torsion_drag_start_structure = workspace.torsion_drag_start_structure
        self._torsion_drag_start_angle = workspace.torsion_drag_start_angle
        self._geometry_undo_history = (
            workspace.geometry_undo_history
            if workspace.geometry_undo_history is not None
            else GeometryEditHistory(5)
        )
        self._replacement_element = workspace.replacement_element
        self._submission_step2_refresh_required = (
            workspace.submission_step2_refresh_required
        )
        self._submission_step3_refresh_required = (
            workspace.submission_step3_refresh_required
        )
        self._scalar_field = workspace.scalar_field
        self._orbital_preferences = workspace.orbital_preferences
        self._sync_geometry_workspace_presentation(workspace)
        self._update_measurement_empty_state()

    def _store_bound_geometry_workspace(
        self,
        *,
        capture_builder_visibility: bool,
    ) -> None:
        workspace = self._bound_geometry_workspace
        if workspace is None:
            return
        workspace.source_path = self._source_path
        workspace.source_structure = self._source_structure
        workspace.source_connectivity = self._source_connectivity
        workspace.source_connectivity_source = self._source_connectivity_source
        workspace.source_bond_display_orders = self._source_bond_display_orders
        workspace.structure = self._structure
        workspace.connectivity = self._connectivity
        workspace.connectivity_source = self._connectivity_source
        workspace.bond_display_orders = self._bond_display_orders
        workspace.covalent_radii = self._covalent_radii
        workspace.vdw_radii = self._vdw_radii
        workspace.anchors = self._anchors
        workspace.site_controls = self._site_controls
        workspace.current_proposals = self._current_proposals
        workspace.applied_result = self._applied_result
        workspace.electrode_sites = self._electrode_sites
        workspace.electrode_site_controls = self._electrode_site_controls
        workspace.electrode_current_proposal = self._electrode_current_proposal
        workspace.applied_electrode_result = self._applied_electrode_result
        workspace.confirmed = self._confirmed
        workspace.recovery_snapshot = self._recovery_snapshot
        workspace.recovery_profile = self._recovery_profile
        workspace.measurement_session = self._measurement_session
        workspace.measurement_selection = self._measurement_selection
        workspace.pick_mode = self._pick_mode
        workspace.torsion_session = self._torsion_session
        workspace.torsion_reference_direction = self._torsion_reference_direction
        workspace.torsion_drag_start_structure = self._torsion_drag_start_structure
        workspace.torsion_drag_start_angle = self._torsion_drag_start_angle
        workspace.geometry_undo_history = self._geometry_undo_history
        workspace.replacement_element = self._replacement_element
        workspace.submission_step2_refresh_required = (
            self._submission_step2_refresh_required
        )
        workspace.submission_step3_refresh_required = (
            self._submission_step3_refresh_required
        )
        workspace.scalar_field = self._scalar_field
        workspace.orbital_preferences = self._orbital_preferences
        if capture_builder_visibility:
            workspace.builder_visible = not self._au_tool_dock.isHidden()
        self._sync_geometry_workspace_presentation(workspace)

    @staticmethod
    def _sync_geometry_workspace_presentation(
        workspace: _GeometryWorkspace,
    ) -> None:
        """Show the empty prompt only before a structure has been loaded."""

        has_structure = workspace.structure is not None
        workspace.viewer_stack.setCurrentWidget(
            workspace.viewer if has_structure else workspace.empty_state_widget
        )
        workspace.status_panel.setVisible(has_structure)

    def _active_workspace(self) -> _Workspace | None:
        return self._workspaces_by_widget.get(self._workspace_tabs.currentWidget())

    def _active_geometry_workspace(self) -> _GeometryWorkspace | None:
        workspace = self._active_workspace()
        return workspace if isinstance(workspace, _GeometryWorkspace) else None

    def _focus_workspace(self, workspace: _Workspace) -> None:
        index = self._workspace_tabs.indexOf(workspace.content)
        if index < 0:
            raise RuntimeError("workspace is not open")
        blocker = QSignalBlocker(self._workspace_tabs)
        self._workspace_tabs.setCurrentIndex(index)
        del blocker
        self._active_workspace_changed(index)

    @Slot(int)
    def _active_workspace_changed(self, _index: int) -> None:
        if self._workspace_switch_in_progress:
            return
        self._workspace_switch_in_progress = True
        try:
            self._cancel_orca_wbl_contact_selection()
            active = self._active_workspace()
            outgoing = self._bound_geometry_workspace
            if outgoing is not None:
                self._deactivate_lattice_extension_workspace(outgoing)
                self._store_bound_geometry_workspace(
                    capture_builder_visibility=(
                        self._presented_workspace is outgoing
                    ),
                )
            if hasattr(self, "_torsion_numeric_input"):
                self._cancel_torsion_numeric_edit()
            if isinstance(active, _GeometryWorkspace):
                self._bind_geometry_workspace(active)
            self._presented_workspace = active
            self._route_active_workspace()
        finally:
            self._workspace_switch_in_progress = False

    def _route_active_workspace(self) -> None:
        """Synchronize the single action/dock surface with the active tab."""

        if not hasattr(self, "_reset_view_action"):
            return
        workspace = self._active_workspace()
        is_geometry = isinstance(workspace, _GeometryWorkspace)
        geometry_ready = is_geometry and workspace.structure is not None
        orca_snapshot = (
            workspace.recovery_snapshot
            if is_geometry
            and workspace.recovery_snapshot is not None
            and workspace.recovery_snapshot.project.workflow_kind
            is CalculationWorkflowKind.ORCA
            else None
        )
        if hasattr(self, "_orca_wbl_action"):
            orca_context_ready = bool(
                geometry_ready
                and orca_snapshot is not None
                and workspace.recovery_profile is not None
            )
            self._orca_wbl_action.setEnabled(
                bool(
                    orca_context_ready
                    and _orca_wbl_calculation_eligible(orca_snapshot)
                    and self._projects_dialog is not None
                )
            )
            self._orca_frequency_action.setEnabled(
                bool(
                    orca_context_ready
                    and _orca_frequency_calculation_eligible(orca_snapshot)
                    and not self._submission_running
                )
            )
        if hasattr(self, "_export_current_view_action"):
            self._export_current_view_action.setEnabled(
                self._active_export_target() is not None
            )
        if hasattr(self, "_density_action"):
            self._density_action.setEnabled(bool(geometry_ready and len(workspace.structure) >= 2))
        if hasattr(self, "_tight_binding_action"):
            self._tight_binding_action.setEnabled(
                bool(
                    geometry_ready
                    and workspace.connectivity is not None
                    and len(workspace.structure) >= 2
                )
            )
        is_restart = bool(is_geometry and workspace.restart_draft is not None)
        self._submit_aims_action.setText(
            "Submit Restart Draft..."
            if is_restart
            else "Step 1 — Molecule Optimization..."
        )
        self._submit_aims_action.setToolTip(
            "Submit Restart Draft..."
            if is_restart
            else "Step 1 — Molecule Optimization..."
        )
        self._element_labels_action.setEnabled(is_geometry)
        for action in (
            self._distance_measure_action,
            self._angle_measure_action,
            self._rotate_bond_action,
            self._delete_atom_action,
            self._replace_atom_action,
        ):
            blocker = QSignalBlocker(action)
            action.setChecked(
                is_geometry
                and (
                    (
                        action is self._distance_measure_action
                        and workspace.pick_mode is _ViewerPickMode.DISTANCE
                    )
                    or (
                        action is self._angle_measure_action
                        and workspace.pick_mode is _ViewerPickMode.ANGLE
                    )
                    or (
                        action is self._rotate_bond_action
                        and workspace.pick_mode is _ViewerPickMode.BOND_ROTATION
                    )
                    or (
                        action is self._delete_atom_action
                        and workspace.pick_mode is _ViewerPickMode.DELETE_ATOM
                    )
                    or (
                        action is self._replace_atom_action
                        and workspace.pick_mode is _ViewerPickMode.REPLACE_ATOM
                    )
                )
            )
            action.setEnabled(
                bool(
                    geometry_ready
                    and (
                        action
                        not in {
                            self._rotate_bond_action,
                            self._delete_atom_action,
                            self._replace_atom_action,
                        }
                        or (
                            not workspace.read_only
                            and (
                                action is self._rotate_bond_action
                                or not workspace.coordinate_only
                            )
                        )
                    )
                )
            )
            del blocker

        if is_geometry:
            self._electrode_builder_stack.setCurrentWidget(workspace.builder_widget)
            self._electrode_builder_action.setEnabled(
                not workspace.read_only and not workspace.coordinate_only
            )
            extension_enabled = self._lattice_extension_editable(workspace)
            if (
                not extension_enabled
                and workspace is self._bound_geometry_workspace
                and self._pick_mode is _ViewerPickMode.AU_LATTICE_EXTENSION
            ):
                self._set_pick_mode(
                    _ViewerPickMode.NORMAL,
                    announce=False,
                )
            extension_blocker = QSignalBlocker(
                workspace.electrode_extension_button
            )
            workspace.electrode_extension_button.setEnabled(extension_enabled)
            workspace.electrode_extension_button.setChecked(
                extension_enabled
                and workspace.pick_mode
                is _ViewerPickMode.AU_LATTICE_EXTENSION
            )
            del extension_blocker
            self._au_tool_dock.setVisible(
                workspace.builder_visible
                and not workspace.read_only
                and not workspace.coordinate_only
            )
            self._geometry_undo_action.setEnabled(
                bool(
                    not workspace.read_only
                    and workspace.geometry_undo_history
                    and workspace.geometry_undo_history.can_undo
                )
            )
            self._geometry_redo_action.setEnabled(
                bool(
                    not workspace.read_only
                    and workspace.geometry_undo_history
                    and workspace.geometry_undo_history.can_redo
                )
            )
            self._reset_view_action.setEnabled(bool(geometry_ready))
            self._generate_aims_action.setEnabled(
                bool(
                    geometry_ready
                    and not workspace.read_only
                    and not self._input_export_running
                )
            )
            self._submit_aims_action.setEnabled(
                bool(
                    geometry_ready
                    and not workspace.read_only
                    and workspace.restart_draft is None
                    and workspace.recovery_snapshot is None
                    and not self._submission_running
                )
            )
            self._submit_orca_action.setEnabled(
                bool(
                    geometry_ready
                    and not workspace.read_only
                    and workspace.restart_draft is None
                    and workspace.recovery_snapshot is None
                    and not self._submission_running
                )
            )
            self._measurement_escape_shortcut.setEnabled(True)
            if workspace.restart_draft is not None:
                self._continue_step2_action.setEnabled(False)
                self._continue_step3_action.setEnabled(False)
                self._continue_step4_action.setEnabled(False)
                self._submit_aims_action.setEnabled(
                    bool(
                        geometry_ready
                        and workspace.restart_draft.source_terminal_confirmed
                        and not self._submission_running
                        and not self._transport_operation_running
                    )
                )
                self._submit_aims_action.setToolTip(
                    "Submit Restart Draft..."
                    if workspace.restart_draft.source_terminal_confirmed
                    else "Refresh Status until the source Job is terminal before submitting."
                )
                return
            if workspace.read_only:
                self._continue_step2_action.setEnabled(False)
                self._continue_step3_action.setEnabled(False)
                self._continue_step4_action.setEnabled(False)
            else:
                self._update_continuation_control()
            return

        self._au_tool_dock.hide()
        self._electrode_builder_action.setEnabled(False)
        self._geometry_undo_action.setEnabled(False)
        self._geometry_redo_action.setEnabled(False)
        self._reset_view_action.setEnabled(
            isinstance(
                workspace,
                (
                    _TransmissionWorkspace,
                    _OrcaWblWorkspace,
                    _DensityWorkspace,
                    _DensityResultWorkspace,
                    _TightBindingWorkspace,
                ),
            )
        )
        self._generate_aims_action.setEnabled(False)
        self._submit_aims_action.setEnabled(False)
        self._submit_orca_action.setEnabled(False)
        self._continue_step2_action.setEnabled(False)
        self._continue_step3_action.setEnabled(False)
        self._continue_step4_action.setEnabled(False)
        self._measurement_escape_shortcut.setEnabled(False)

    def _dispatch_geometry_event(self, runtime_id: UUID, callback, *args) -> None:
        workspace = self._active_geometry_workspace()
        if workspace is None or workspace.runtime_id != runtime_id:
            return
        if self._bound_geometry_workspace is not workspace:
            self._bind_geometry_workspace(workspace)
        callback(*args)

    def _reset_active_view(self) -> None:
        workspace = self._active_workspace()
        if isinstance(workspace, _GeometryWorkspace):
            workspace.viewer.reset_camera()
        elif isinstance(workspace, _TransmissionWorkspace):
            workspace.content.reset_view()
        elif isinstance(workspace, _OrcaWblWorkspace):
            workspace.content.reset_view()
        elif isinstance(workspace, _DensityWorkspace):
            workspace.content.viewer.reset_camera()
        elif isinstance(workspace, _DensityResultWorkspace):
            workspace.content.viewer.reset_camera()
        elif isinstance(workspace, _TightBindingWorkspace):
            workspace.content.reset_view()

    def _set_workspace_tab_tooltip(self, workspace: _Workspace) -> None:
        index = self._workspace_tabs.indexOf(workspace.content)
        if index < 0:
            return
        if isinstance(workspace, _GeometryWorkspace):
            identity = workspace.identity
            if isinstance(identity, LocalGeometryWorkspaceIdentity):
                tooltip = str(identity.canonical_source_path)
            elif isinstance(identity, ManagedGeometryWorkspaceIdentity):
                tooltip = (
                    f"Project {identity.project_id} · {identity.geometry_role}"
                )
            else:
                tooltip = "Open a geometry to begin"
        elif isinstance(workspace, _DensityWorkspace):
            tooltip = (
                "Electron Density Difference — selection, submission and status"
            )
        elif isinstance(workspace, _DensityResultWorkspace):
            tooltip = (
                f"Electron Density Difference result · Task "
                f"{workspace.identity[0]} · Attempt {workspace.identity[1]:02d}"
            )
        elif isinstance(workspace, _TightBindingWorkspace):
            tooltip = f"Session-local tight-binding model · {workspace.display_title}"
        elif isinstance(workspace, _OrcaWblWorkspace):
            tooltip = (
                f"Project {workspace.identity.project_id} · ORCA WBL · "
                f"{workspace.identity.result_digest[:12]}"
            )
        else:
            tooltip = (
                f"Project {workspace.identity.project_id} · "
                f"Job {workspace.identity.job_id} · "
                f"{workspace.identity.result_filename}"
            )
        self._workspace_tabs.setTabToolTip(index, tooltip)

    def _set_geometry_workspace_identity(
        self,
        workspace: _GeometryWorkspace,
        identity: GeometryWorkspaceIdentity,
        display_title: str,
    ) -> None:
        previous = workspace.identity
        if previous is not None:
            self._geometry_workspaces_by_identity.pop(previous, None)
        workspace.identity = identity
        workspace.display_title = display_title
        self._geometry_workspaces_by_identity[identity] = workspace
        index = self._workspace_tabs.indexOf(workspace.content)
        if index >= 0:
            self._workspace_tabs.setTabText(index, display_title)
        self._set_workspace_tab_tooltip(workspace)

    @Slot(int)
    def _close_workspace_tab(self, index: int) -> None:
        widget = self._workspace_tabs.widget(index)
        workspace = self._workspaces_by_widget.get(widget)
        if workspace is None:
            return
        if isinstance(
            workspace,
            (_DensityWorkspace, _DensityResultWorkspace),
        ) and workspace.content._workers:
            QMessageBox.information(self, "Density operation running", "Wait for the current operation before closing this tab.")
            return
        if (
            isinstance(workspace, _GeometryWorkspace)
            and workspace.orbital_load_running
        ):
            QMessageBox.information(
                self,
                "Orbital Cube download running",
                "Wait for the selected orbital Cube to finish loading before "
                "closing this tab.",
            )
            return
        if workspace is self._bound_geometry_workspace:
            self._store_bound_geometry_workspace(
                capture_builder_visibility=(self._presented_workspace is workspace),
            )
        blocker = QSignalBlocker(self._workspace_tabs)
        self._workspace_tabs.removeTab(index)
        del blocker
        self._workspaces_by_widget.pop(widget, None)
        if self._presented_workspace is workspace:
            self._presented_workspace = None
        owned_viewers = tuple(widget.findChildren(MoleculeViewerWidget))
        retire_geometry_workspace = bool(
            isinstance(workspace, _GeometryWorkspace)
            and workspace.runtime_id
            in {
                self._submission_origin_workspace_id,
                self._transport_origin_workspace_id,
            }
        )
        if isinstance(workspace, (_DensityWorkspace, _DensityResultWorkspace)):
            if isinstance(workspace, _DensityResultWorkspace):
                self._density_result_workspaces_by_identity.pop(
                    workspace.identity,
                    None,
                )
                workspace.content._surface_timer.stop()
            workspace.content.close()
            workspace.content.deleteLater()
        elif isinstance(workspace, _TightBindingWorkspace):
            workspace.content.close()
            workspace.content.deleteLater()
        elif isinstance(workspace, _TransmissionWorkspace):
            self._transmission_workspaces_by_identity.pop(workspace.identity, None)
            workspace.content.close()
            workspace.content.deleteLater()
        elif isinstance(workspace, _OrcaWblWorkspace):
            self._orca_wbl_workspaces_by_identity.pop(workspace.identity, None)
            workspace.content.close()
            workspace.content.deleteLater()
        else:
            if workspace.identity is not None:
                self._geometry_workspaces_by_identity.pop(workspace.identity, None)
            self._electrode_builder_stack.removeWidget(workspace.builder_widget)
            workspace.content.hide()
            if retire_geometry_workspace:
                workspace.content.hide()
                workspace.builder_widget.hide()
                self._retired_geometry_workspaces[workspace.runtime_id] = workspace
            else:
                workspace.content.deleteLater()
                workspace.builder_widget.deleteLater()
        if self._bound_geometry_workspace is workspace:
            self._bound_geometry_workspace = None
        self._active_workspace_changed(self._workspace_tabs.currentIndex())
        for viewer in owned_viewers:
            viewer.prepare_for_owner_teardown()

    def _workspace_by_runtime_id(
        self,
        runtime_id: UUID | None,
    ) -> _GeometryWorkspace | None:
        if runtime_id is None:
            return None
        for workspace in self._workspaces_by_widget.values():
            if (
                isinstance(workspace, _GeometryWorkspace)
                and workspace.runtime_id == runtime_id
            ):
                return workspace
        return self._retired_geometry_workspaces.get(runtime_id)

    @contextmanager
    def _geometry_callback_context(self, runtime_id: UUID | None):
        """Temporarily route an async callback to its originating Geometry."""

        target = self._workspace_by_runtime_id(runtime_id)
        visible = self._active_geometry_workspace()
        previous = self._bound_geometry_workspace
        if target is not None and target is not previous:
            if previous is not None:
                self._store_bound_geometry_workspace(
                    capture_builder_visibility=False,
                )
            self._bind_geometry_workspace(target)
        try:
            yield
        finally:
            if target is not None:
                self._store_bound_geometry_workspace(
                    capture_builder_visibility=False,
                )
            restore = visible or previous
            if restore is not None and restore is not target:
                self._bind_geometry_workspace(restore)
            self._route_active_workspace()

    def _release_retired_workspace(self, runtime_id: UUID | None) -> None:
        if runtime_id is None:
            return
        if runtime_id in {
            self._submission_origin_workspace_id,
            self._transport_origin_workspace_id,
        }:
            return
        workspace = self._retired_geometry_workspaces.pop(runtime_id, None)
        if workspace is not None:
            if self._bound_geometry_workspace is workspace:
                self._bound_geometry_workspace = None
            workspace.content.deleteLater()
            workspace.builder_widget.deleteLater()

    def _has_active_remote_operation(self, *, include_status_refresh: bool = True) -> bool:
        projects_dialog = self._projects_dialog
        return (
            self._submission_running
            or bool(self._submission_workers)
            or bool(self._input_export_workers)
            or self._transport_operation_running
            or bool(self._transport_workers)
            or bool(self._orbital_workers)
            or (
                projects_dialog is not None
                and projects_dialog.has_active_remote_operation(include_status_refresh=include_status_refresh)
            )
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cancel_orca_wbl_contact_selection()
        if any(
            (
                isinstance(w, _DensityWorkspace)
                and w.content.has_active_remote_operation(include_status_refresh=False)
            )
            or (
                isinstance(w, _DensityResultWorkspace)
                and w.content._workers
            )
            for w in self._workspaces_by_widget.values()
        ):
            QMessageBox.information(self, "Density operation running", "Wait for the current density operation before closing the application.")
            event.ignore()
            return
        if self._has_active_remote_operation(include_status_refresh=False):
            QMessageBox.warning(
                self,
                "Remote operation still running",
                "A remote operation is still running.\n\n"
                "Wait for it to finish before closing the application.",
            )
            event.ignore()
            return
        if self._projects_dialog is not None:
            self._projects_dialog.stop_status_refresh()
        owned_viewers = tuple(self.findChildren(MoleculeViewerWidget))
        retired_viewers = tuple(
            workspace.viewer
            for workspace in self._retired_geometry_workspaces.values()
        )
        for viewer in (*owned_viewers, *retired_viewers):
            viewer.prepare_for_owner_teardown()
        for workspace in self._workspaces_by_widget.values():
            if isinstance(workspace, _DensityWorkspace):
                workspace.content.stop_status_refresh()
            if isinstance(workspace, (_DensityWorkspace, _DensityResultWorkspace, _TightBindingWorkspace)):
                if isinstance(workspace, _DensityResultWorkspace):
                    workspace.content._surface_timer.stop()
                workspace.content.close()
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        active_geometry = self._active_geometry_workspace()
        if (
            active_geometry is not None
            and active_geometry is self._bound_geometry_workspace
            and event.key() == Qt.Key.Key_Escape
            and self._pick_mode
            in {
                _ViewerPickMode.DISTANCE,
                _ViewerPickMode.ANGLE,
                _ViewerPickMode.DELETE_ATOM,
                _ViewerPickMode.REPLACE_ATOM,
                _ViewerPickMode.AU_LATTICE_EXTENSION,
                _ViewerPickMode.ORCA_WBL_CONTACT,
            }
        ):
            self._handle_viewer_escape()
            event.accept()
            return
        super().keyPressEvent(event)

    def _handle_viewer_escape(self) -> None:
        if self._pick_mode in {
            _ViewerPickMode.DELETE_ATOM,
            _ViewerPickMode.REPLACE_ATOM,
            _ViewerPickMode.AU_LATTICE_EXTENSION,
            _ViewerPickMode.ORCA_WBL_CONTACT,
        }:
            if self._pick_mode is _ViewerPickMode.ORCA_WBL_CONTACT:
                self._cancel_orca_wbl_contact_selection()
            else:
                self._set_pick_mode(_ViewerPickMode.NORMAL)
            return
        self._clear_incomplete_measurement()

    def _clear_incomplete_measurement(self) -> None:
        if self._active_geometry_workspace() is not self._bound_geometry_workspace:
            return
        if self._pick_mode not in {
            _ViewerPickMode.DISTANCE,
            _ViewerPickMode.ANGLE,
        }:
            return
        self._measurement_selection = ()
        self._restore_current_highlights()
        self._set_operation(
            "Incomplete measurement selection cleared; measurement mode "
            "remains active."
        )

    def _build_au_tool_dock(self) -> None:
        self._au_tool_dock = QDockWidget("Electrode Builder", self)
        self._au_tool_dock.setObjectName("auToolDock")
        self._electrode_builder_dock = self._au_tool_dock
        self._au_tool_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self._au_tool_dock.setMinimumWidth(260)
        self._electrode_builder_stack = QStackedWidget(self._au_tool_dock)
        self._electrode_builder_stack.setObjectName("electrodeBuilderStack")
        self._au_tool_dock.setWidget(self._electrode_builder_stack)
        self.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea,
            self._au_tool_dock,
        )
        self._au_tool_dock.show()

    def _build_top_toolbar(self) -> None:
        self._build_main_menus()
        self._build_geometry_history_actions()

        toolbar = QToolBar("Main controls", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._main_toolbar = toolbar

        toolbar.addAction(self._geometry_undo_action)
        toolbar.addAction(self._geometry_redo_action)
        self._geometry_undo_button = self._toolbar_button_for_action(
            toolbar,
            self._geometry_undo_action,
            "geometryUndoButton",
            "Undo",
        )
        self._geometry_redo_button = self._toolbar_button_for_action(
            toolbar,
            self._geometry_redo_action,
            "geometryRedoButton",
            "Redo",
        )
        toolbar.addSeparator()

        self._reset_view_action = QAction(
            _viewer_icon("reset_view.svg"),
            "Reset View",
            self,
        )
        self._reset_view_action.setObjectName("resetView")
        self._reset_view_action.setToolTip("Reset View")
        self._reset_view_action.triggered.connect(self._reset_active_view)
        toolbar.addAction(self._reset_view_action)
        self._reset_view_button = self._toolbar_button_for_action(
            toolbar,
            self._reset_view_action,
            "resetViewTool",
            "Reset View",
        )

        self._element_labels_action = QAction(
            _viewer_icon("element_labels.svg"),
            "Element Labels",
            self,
        )
        self._element_labels_action.setObjectName("elementLabels")
        self._element_labels_action.setCheckable(True)
        self._element_labels_action.setChecked(
            self._view_preferences.show_element_labels
        )
        self._element_labels_action.setToolTip("Show/Hide Element Labels")
        self._element_labels_action.toggled.connect(
            self._element_labels_toggled
        )
        toolbar.addAction(self._element_labels_action)
        self._element_labels_button = self._toolbar_button_for_action(
            toolbar,
            self._element_labels_action,
            "elementLabelsTool",
            "Show/Hide Element Labels",
        )

        toolbar.addSeparator()

        self._distance_measure_action = QAction(
            _viewer_icon("measure_distance.svg"),
            "Distance",
            self,
        )
        self._distance_measure_action.setObjectName("measureDistance")
        self._distance_measure_action.setCheckable(True)
        self._distance_measure_action.setEnabled(False)
        self._distance_measure_action.setToolTip("Measure Distance")
        self._distance_measure_action.toggled.connect(
            self._distance_measurement_toggled
        )
        toolbar.addAction(self._distance_measure_action)
        self._distance_measure_button = self._toolbar_button_for_action(
            toolbar,
            self._distance_measure_action,
            "measureDistanceTool",
            "Measure Distance",
        )

        self._angle_measure_action = QAction(
            _viewer_icon("measure_angle.svg"),
            "Angle",
            self,
        )
        self._angle_measure_action.setObjectName("measureAngle")
        self._angle_measure_action.setCheckable(True)
        self._angle_measure_action.setEnabled(False)
        self._angle_measure_action.setToolTip("Measure Angle")
        self._angle_measure_action.toggled.connect(self._angle_measurement_toggled)
        toolbar.addAction(self._angle_measure_action)
        self._angle_measure_button = self._toolbar_button_for_action(
            toolbar,
            self._angle_measure_action,
            "measureAngleTool",
            "Measure Angle",
        )

        self._rotate_bond_action = QAction(
            _viewer_icon("rotate_bond.svg"),
            "Rotate Bond",
            self,
        )
        self._rotate_bond_action.setObjectName("rotateBond")
        self._rotate_bond_action.setCheckable(True)
        self._rotate_bond_action.setEnabled(False)
        self._rotate_bond_action.setToolTip("Rotate Bond")
        self._rotate_bond_action.toggled.connect(self._bond_rotation_toggled)
        toolbar.addAction(self._rotate_bond_action)
        self._rotate_bond_button = self._toolbar_button_for_action(
            toolbar,
            self._rotate_bond_action,
            "rotateBondTool",
            "Rotate Bond",
        )

        self._delete_atom_action = QAction(
            _viewer_icon("delete_atom.svg"),
            "Delete Atom",
            self,
        )
        self._delete_atom_action.setObjectName("deleteAtom")
        self._delete_atom_action.setCheckable(True)
        self._delete_atom_action.setEnabled(False)
        self._delete_atom_action.setToolTip("Delete Atom")
        self._delete_atom_action.toggled.connect(self._delete_atom_toggled)
        toolbar.addAction(self._delete_atom_action)
        self._delete_atom_button = self._toolbar_button_for_action(
            toolbar,
            self._delete_atom_action,
            "deleteAtomTool",
            "Delete Atom",
        )

        self._replace_atom_action = QAction(
            _viewer_icon("replace_atom.svg"),
            "Replace Atom",
            self,
        )
        self._replace_atom_action.setObjectName("replaceAtom")
        self._replace_atom_action.setCheckable(True)
        self._replace_atom_action.setEnabled(False)
        self._replace_atom_action.setToolTip("Replace Atom")
        self._replace_atom_action.toggled.connect(self._replace_atom_toggled)
        toolbar.addAction(self._replace_atom_action)
        self._replace_atom_button = self._toolbar_button_for_action(
            toolbar,
            self._replace_atom_action,
            "replaceAtomTool",
            "Replace Atom",
        )

        toolbar.addSeparator()

        toggle_action = self._au_tool_dock.toggleViewAction()
        toggle_action.setObjectName("electrodeBuilderToggle")
        toggle_action.setText("Electrode Builder")
        toggle_action.setToolTip("Show/Hide Electrode Builder")
        toggle_action.setIcon(_viewer_icon("electrode_builder.svg"))
        toolbar.addAction(toggle_action)
        self._electrode_builder_action = toggle_action
        self._electrode_builder_toggle = self._toolbar_button_for_action(
            toolbar,
            toggle_action,
            "electrodeBuilderTool",
            "Show/Hide Electrode Builder",
        )
        self._au_tool_toggle = self._electrode_builder_toggle

        toggle_action.toggled.connect(self._au_tool_visibility_toggled)

    @staticmethod
    def _toolbar_button_for_action(
        toolbar: QToolBar,
        action: QAction,
        object_name: str,
        accessible_name: str,
    ) -> QToolButton:
        button = toolbar.widgetForAction(action)
        if not isinstance(button, QToolButton):
            raise RuntimeError(f"toolbar action has no tool button: {action.text()}")
        button.setObjectName(object_name)
        button.setAccessibleName(accessible_name)
        button.setToolTip(action.toolTip())
        button.setAutoRaise(True)
        return button

    def _build_main_menus(self) -> None:
        """Create one canonical QAction for each desktop command."""

        menu_bar = self.menuBar()

        self._file_menu = menu_bar.addMenu("File")
        self._file_menu.setObjectName("fileMenu")
        self._new_geometry_action = QAction("New Geometry...", self)
        self._new_geometry_action.setObjectName("newGeometry")
        self._new_geometry_action.setToolTip("New Geometry...")
        self._new_geometry_action.triggered.connect(self._open_xyz)
        self._file_menu.addAction(self._new_geometry_action)
        self._file_menu.addSeparator()
        self._export_current_view_action = QAction(
            "Export Current View...",
            self,
        )
        self._export_current_view_action.setObjectName("exportCurrentView")
        self._export_current_view_action.setToolTip(
            "Export Current View..."
        )
        self._export_current_view_action.setEnabled(False)
        self._export_current_view_action.triggered.connect(
            self._export_current_view
        )
        self._file_menu.addAction(self._export_current_view_action)

        self._projects_menu = menu_bar.addMenu("Projects")
        self._projects_menu.setObjectName("projectsMenu")
        self._projects_action = QAction("Project Manager...", self)
        self._projects_action.setObjectName("calculationProjects")
        self._projects_action.setToolTip("Project Manager...")
        self._projects_action.triggered.connect(self._open_projects)
        self._projects_menu.addAction(self._projects_action)

        self._calculation_menu = menu_bar.addMenu("Calculation")
        self._calculation_menu.setObjectName("calculationMenu")
        self._calculation_menu.menuAction().setObjectName("calculationMenuAction")

        def add_section_heading(menu: QMenu, text: str, object_name: str) -> QAction:
            action = menu.addAction(text)
            action.setObjectName(object_name)
            action.setEnabled(False)
            return action

        add_section_heading(
            self._calculation_menu,
            "External Programs",
            "externalProgramsHeading",
        )
        self._fhi_calculation_menu = self._calculation_menu.addMenu("FHI-aims")
        self._fhi_calculation_menu.setObjectName("fhiAimsCalculationMenu")
        add_section_heading(
            self._fhi_calculation_menu,
            "Transmission",
            "fhiTransmissionHeading",
        )

        self._generate_aims_action = QAction(
            "Generate AIMS Optimization...",
            self,
        )
        self._generate_aims_action.setObjectName("generateAimsOptimization")
        self._generate_aims_action.setToolTip("Generate AIMS Optimization...")
        self._generate_aims_action.setEnabled(False)
        self._generate_aims_action.triggered.connect(
            self._generate_aims_optimization
        )

        self._submit_aims_action = QAction(
            "Step 1 — Molecule Optimization...",
            self,
        )
        self._submit_aims_action.setObjectName("submitAimsOptimization")
        self._submit_aims_action.setToolTip(
            "Step 1 — Molecule Optimization..."
        )
        self._submit_aims_action.setEnabled(False)
        self._submit_aims_action.triggered.connect(
            self._submit_aims_optimization
        )
        self._fhi_calculation_menu.addAction(self._submit_aims_action)

        self._continue_step2_action = QAction(
            "Step 2 — Molecule–Au Optimization...",
            self,
        )
        self._continue_step2_action.setObjectName("continueProjectStep2")
        self._continue_step2_action.setToolTip(
            "Step 2 — Molecule–Au Optimization..."
        )
        self._continue_step2_action.setEnabled(False)
        self._continue_step2_action.triggered.connect(
            self._continue_project_step2
        )
        self._fhi_calculation_menu.addAction(self._continue_step2_action)

        self._continue_step3_action = QAction(
            "Step 3 — Transport Convergence...",
            self,
        )
        self._continue_step3_action.setObjectName("continueProjectStep3")
        self._continue_step3_action.setToolTip(
            "Step 3 — Transport Convergence..."
        )
        self._continue_step3_action.setEnabled(False)
        self._continue_step3_action.triggered.connect(
            self._continue_project_step3
        )
        self._fhi_calculation_menu.addAction(self._continue_step3_action)

        self._continue_step4_action = QAction(
            "Step 4 — Transmission...",
            self,
        )
        self._continue_step4_action.setObjectName("continueProjectStep4")
        self._continue_step4_action.setToolTip("Step 4 — Transmission...")
        self._continue_step4_action.setEnabled(False)
        self._continue_step4_action.triggered.connect(
            self._continue_project_step4
        )
        self._fhi_calculation_menu.addAction(self._continue_step4_action)

        self._fhi_calculation_menu.addSeparator()
        add_section_heading(
            self._fhi_calculation_menu,
            "Other Analysis",
            "fhiOtherAnalysisHeading",
        )
        self._density_action = self._fhi_calculation_menu.addAction(
            "Electron Density Difference..."
        )
        self._density_action.setObjectName("electronDensityDifferenceAction")
        self._density_action.triggered.connect(self._new_density_workspace)

        self._orca_calculation_menu = self._calculation_menu.addMenu("ORCA")
        self._orca_calculation_menu.setObjectName("orcaCalculationMenu")
        add_section_heading(
            self._orca_calculation_menu,
            "Transmission",
            "orcaTransmissionHeading",
        )
        self._submit_orca_action = self._orca_calculation_menu.addAction(
            "Step 1 — Optimization..."
        )
        self._submit_orca_action.setObjectName("submitOrcaOptimization")
        self._submit_orca_action.setEnabled(False)
        self._submit_orca_action.triggered.connect(self._submit_orca_optimization)
        self._orca_wbl_action = self._orca_calculation_menu.addAction(
            "Step 2 — WBL Transmission..."
        )
        self._orca_wbl_action.setObjectName("orcaCalculateWbl")
        self._orca_wbl_action.setEnabled(False)
        self._orca_wbl_action.triggered.connect(self._calculate_active_orca_wbl)
        self._orca_calculation_menu.addSeparator()
        add_section_heading(
            self._orca_calculation_menu,
            "Other Analysis",
            "orcaOtherAnalysisHeading",
        )
        self._orca_frequency_action = self._orca_calculation_menu.addAction("Run Frequency...")
        self._orca_frequency_action.setObjectName("orcaRunFrequency")
        self._orca_frequency_action.setEnabled(False)
        self._orca_frequency_action.triggered.connect(
            self._run_active_orca_frequency
        )
        self._calculation_menu.addSeparator()
        add_section_heading(
            self._calculation_menu,
            "Local Analysis",
            "localAnalysisHeading",
        )
        self._tight_binding_action = self._calculation_menu.addAction(
            "Local Tight-Binding Transmission..."
        )
        self._tight_binding_action.setObjectName("tightBindingTransmissionAction")
        self._tight_binding_action.setEnabled(False)
        self._tight_binding_action.triggered.connect(
            self._new_tight_binding_workspace
        )

        self._server_menu = menu_bar.addMenu("Server")
        self._server_menu.setObjectName("serverMenu")
        self._servers_action = QAction("Manage Servers...", self)
        self._servers_action.setObjectName("serverProfiles")
        self._servers_action.setToolTip("Manage Servers...")
        self._servers_action.triggered.connect(self._open_server_profiles)
        self._server_menu.addAction(self._servers_action)

        self._settings_menu = menu_bar.addMenu("Settings")
        self._settings_menu.setObjectName("settingsMenu")
        self._view_settings_action = QAction("View...", self)
        self._view_settings_action.setObjectName("viewSettings")
        self._view_settings_action.setToolTip("View...")
        self._view_settings_action.triggered.connect(self._open_view_settings)
        self._settings_menu.addAction(self._view_settings_action)

        self._bond_detection_action = QAction("Bond Detection...", self)
        self._bond_detection_action.setObjectName("bondDetection")
        self._bond_detection_action.setToolTip("Bond Detection...")
        self._bond_detection_action.triggered.connect(
            self._open_bond_detection
        )
        self._settings_menu.addAction(self._bond_detection_action)

        self._email_notifications_action = QAction(
            "Email Notifications...",
            self,
        )
        self._email_notifications_action.setObjectName("emailNotifications")
        self._email_notifications_action.setToolTip("Email Notifications...")
        self._email_notifications_action.triggered.connect(
            self._open_email_notifications
        )
        self._settings_menu.addAction(self._email_notifications_action)

        self._help_menu = menu_bar.addMenu("Help")
        self._help_menu.setObjectName("helpMenu")
        self._license_notices_action = self._help_menu.addAction(
            "License and Third-Party Notices..."
        )
        self._license_notices_action.setObjectName("openLicenseNotices")
        self._license_notices_action.triggered.connect(
            self._open_license_notices
        )
        self._about_action = self._help_menu.addAction("About Moltage")
        self._about_action.setObjectName("aboutMoltage")
        self._about_action.triggered.connect(self._open_about_moltage)

        self._theme_menu = QMenu(self)
        self._theme_menu.setObjectName("themeMenu")
        self._theme_action_group = QActionGroup(self)
        self._theme_action_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        active_theme_id = (
            DEFAULT_THEME_MANAGER.active_theme_id
            or DEFAULT_THEME_MANAGER.default_theme_id
        )
        for definition in DEFAULT_THEME_MANAGER.themes:
            action = QAction(definition.display_name, self)
            action.setObjectName(f"selectTheme_{definition.theme_id}")
            action.setData(definition.theme_id)
            action.setCheckable(True)
            action.setChecked(definition.theme_id == active_theme_id)
            action.triggered.connect(
                lambda _checked=False, theme_id=definition.theme_id: (
                    self._select_theme(theme_id)
                )
            )
            self._theme_action_group.addAction(action)
            self._theme_actions[definition.theme_id] = action

        self._theme_panel = QWidget(self._theme_menu)
        self._theme_panel.setObjectName("themePanel")
        theme_panel_layout = QHBoxLayout(self._theme_panel)
        theme_panel_layout.setContentsMargins(6, 6, 6, 6)
        theme_panel_layout.setSpacing(10)
        self._theme_choice_group = QButtonGroup(self._theme_panel)
        self._theme_choice_group.setExclusive(True)
        self._theme_choice_buttons: dict[str, QRadioButton] = {}

        for column_index, (is_dark, section_title) in enumerate(
            ((False, "Light"), (True, "Dark"))
        ):
            if column_index:
                column_divider = QFrame(self._theme_panel)
                column_divider.setObjectName("themeColumnDivider")
                column_divider.setFrameShape(QFrame.Shape.VLine)
                column_divider.setFrameShadow(QFrame.Shadow.Plain)
                theme_panel_layout.addWidget(column_divider)

            column = QWidget(self._theme_panel)
            column.setObjectName(f"theme{section_title}Column")
            column.setMinimumWidth(150)
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(3)

            header = QLabel(section_title, column)
            header.setObjectName(f"theme{section_title}Header")
            column_layout.addWidget(header)
            header_divider = QFrame(column)
            header_divider.setObjectName(
                f"theme{section_title}HeaderDivider"
            )
            header_divider.setFrameShape(QFrame.Shape.HLine)
            header_divider.setFrameShadow(QFrame.Shadow.Plain)
            column_layout.addWidget(header_divider)

            for definition in DEFAULT_THEME_MANAGER.themes:
                if definition.is_dark is not is_dark:
                    continue
                action = self._theme_actions[definition.theme_id]
                choice = QRadioButton(definition.display_name, column)
                choice.setObjectName(f"themeChoice_{definition.theme_id}")
                choice.setProperty("themeChoice", True)
                choice.setChecked(definition.theme_id == active_theme_id)
                choice.clicked.connect(
                    lambda _checked=False, selected_action=action: (
                        selected_action.trigger(),
                        self._theme_menu.close(),
                    )
                )
                action.toggled.connect(choice.setChecked)
                self._theme_choice_group.addButton(choice)
                self._theme_choice_buttons[definition.theme_id] = choice
                column_layout.addWidget(choice)
            column_layout.addStretch(1)
            theme_panel_layout.addWidget(column)

        self._theme_widget_action = QWidgetAction(self._theme_menu)
        self._theme_widget_action.setDefaultWidget(self._theme_panel)
        self._theme_menu.addAction(self._theme_widget_action)

        self._theme_button = QToolButton(menu_bar)
        self._theme_button.setObjectName("themeButton")
        self._theme_button.setAccessibleName("Themes")
        self._theme_button.setIcon(themed_icon("theme.svg"))
        self._theme_button.setIconSize(QSize(18, 18))
        self._theme_button.setAutoRaise(True)
        self._theme_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._theme_button.setMenu(self._theme_menu)
        self._update_theme_button_text(active_theme_id)

        self._update_log_menu = QMenu(self)
        self._update_log_menu.setObjectName("updateLogMenu")
        self._update_log_action = self._update_log_menu.addAction(
            "Update Log..."
        )
        self._update_log_action.setObjectName("openUpdateLog")
        self._update_log_action.triggered.connect(self._open_update_log)

        self._update_log_button = QToolButton(menu_bar)
        self._update_log_button.setObjectName("updateLogButton")
        self._update_log_button.setAccessibleName("Updates")
        self._update_log_button.setToolTip("Updates")
        self._update_log_button.setIcon(_viewer_icon("update_log.svg"))
        self._update_log_button.setIconSize(QSize(18, 18))
        self._update_log_button.setAutoRaise(True)
        self._update_log_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._update_log_button.setMenu(self._update_log_menu)

        self._menu_corner_controls = QWidget(menu_bar)
        self._menu_corner_controls.setObjectName("menuCornerControls")
        corner_layout = QHBoxLayout(self._menu_corner_controls)
        corner_layout.setContentsMargins(0, 0, 8, 0)
        corner_layout.setSpacing(2)
        corner_layout.addWidget(self._theme_button)
        corner_layout.addWidget(self._update_log_button)
        menu_bar.setCornerWidget(
            self._menu_corner_controls,
            Qt.Corner.TopRightCorner,
        )

    def _select_theme(self, theme_id: str) -> None:
        """Apply and persist one explicitly selected registered UI theme."""

        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("theme switching requires a QApplication")
        preserved_geometry = QRect(self.geometry())
        preserve_normal_geometry = not (
            self.isMaximized() or self.isMinimized() or self.isFullScreen()
        )
        definition = DEFAULT_THEME_MANAGER.apply(application, theme_id)
        self._refresh_theme_icons()
        self._update_theme_button_text(definition.theme_id)
        for registered_id, action in self._theme_actions.items():
            action.setChecked(registered_id == definition.theme_id)
        if preserve_normal_geometry:
            self.setGeometry(preserved_geometry)
            QTimer.singleShot(
                0,
                lambda geometry=QRect(preserved_geometry): self._restore_theme_geometry(
                    geometry
                ),
            )

        repository = self._user_view_preferences_repository
        if repository is None:
            return
        try:
            repository.save_theme_id(definition.theme_id)
        except UserViewPreferencesError as error:
            QMessageBox.critical(
                self,
                "Unable to save Theme setting",
                str(error),
            )

    def _restore_theme_geometry(self, geometry: QRect) -> None:
        """Keep a normal top-level window stable after deferred repolishing."""

        if self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return
        self.setGeometry(geometry)

    def _update_theme_button_text(self, theme_id: str) -> None:
        definition = DEFAULT_THEME_MANAGER.definition(theme_id)
        label = f"Theme — {definition.display_name}"
        self._theme_button.setToolTip(label)
        self._theme_button.setAccessibleDescription(label)

    def _refresh_theme_icons(self) -> None:
        """Retint persistent SVG actions after an application theme change."""

        self._theme_button.setIcon(themed_icon("theme.svg"))
        self._update_log_button.setIcon(themed_icon("update_log.svg"))
        for action, filename in (
            (self._geometry_undo_action, "undo.svg"),
            (self._geometry_redo_action, "redo.svg"),
            (self._reset_view_action, "reset_view.svg"),
            (self._element_labels_action, "element_labels.svg"),
            (self._distance_measure_action, "measure_distance.svg"),
            (self._angle_measure_action, "measure_angle.svg"),
            (self._rotate_bond_action, "rotate_bond.svg"),
            (self._delete_atom_action, "delete_atom.svg"),
            (self._replace_atom_action, "replace_atom.svg"),
            (self._electrode_builder_action, "electrode_builder.svg"),
        ):
            action.setIcon(themed_icon(filename))

    def _open_update_log(self) -> None:
        path = application_resource_path("update_log.txt")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            QMessageBox.critical(
                self,
                "Unable to open update log",
                f"Unable to read {path}: {error}",
            )
            return
        dialog = _UpdateLogDialog(content, self)
        dialog.exec()
        dialog.deleteLater()

    def _open_license_notices(self) -> None:
        documents: list[str] = []
        for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            path = application_legal_document_path(filename)
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                QMessageBox.critical(
                    self,
                    "Unable to open legal notices",
                    f"Unable to read {path}: {error}",
                )
                return
            documents.append(f"===== {filename} =====\n\n{content}")
        dialog = _UpdateLogDialog("\n\n".join(documents), self)
        dialog.setObjectName("licenseNoticesDialog")
        dialog.setWindowTitle("License and Third-Party Notices")
        dialog.exec()
        dialog.deleteLater()

    def _open_about_moltage(self) -> None:
        QMessageBox.about(
            self,
            "About Moltage",
            "<b>Moltage 0.2.1</b><br>"
            "Single-Molecule Quantum Transport &amp; Analysis Workbench"
            "<br><br>Copyright &copy; 2026 Junfeng Lin."
            "<br>Licensed under GNU GPL version 3 only."
            "<br><br>See Help &gt; License and Third-Party Notices for "
            "redistribution terms.",
        )

    def _build_geometry_history_actions(self) -> None:
        self._geometry_undo_action = QAction(
            _viewer_icon("undo.svg"),
            "Undo",
            self,
        )
        self._geometry_undo_action.setObjectName("geometryUndo")
        self._geometry_undo_action.setToolTip("Undo")
        self._geometry_undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._geometry_undo_action.setShortcutContext(
            Qt.ShortcutContext.WindowShortcut
        )
        self._geometry_undo_action.setEnabled(False)
        self._geometry_undo_action.triggered.connect(self._undo_geometry)

        self._geometry_redo_action = QAction(
            _viewer_icon("redo.svg"),
            "Redo",
            self,
        )
        self._geometry_redo_action.setObjectName("geometryRedo")
        self._geometry_redo_action.setToolTip("Redo")
        self._geometry_redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self._geometry_redo_action.setShortcutContext(
            Qt.ShortcutContext.WindowShortcut
        )
        self._geometry_redo_action.setEnabled(False)
        self._geometry_redo_action.triggered.connect(self._redo_geometry)

    def _distance_measurement_toggled(self, checked: bool) -> None:
        if checked:
            self._set_pick_mode(_ViewerPickMode.DISTANCE)
        elif self._pick_mode is _ViewerPickMode.DISTANCE:
            self._set_pick_mode(_ViewerPickMode.NORMAL)

    def _angle_measurement_toggled(self, checked: bool) -> None:
        if checked:
            self._set_pick_mode(_ViewerPickMode.ANGLE)
        elif self._pick_mode is _ViewerPickMode.ANGLE:
            self._set_pick_mode(_ViewerPickMode.NORMAL)

    def _bond_rotation_toggled(self, checked: bool) -> None:
        if checked:
            self._set_pick_mode(_ViewerPickMode.BOND_ROTATION)
        elif self._pick_mode is _ViewerPickMode.BOND_ROTATION:
            self._set_pick_mode(_ViewerPickMode.NORMAL)

    def _delete_atom_toggled(self, checked: bool) -> None:
        if checked:
            self._set_pick_mode(_ViewerPickMode.DELETE_ATOM)
        elif self._pick_mode is _ViewerPickMode.DELETE_ATOM:
            self._set_pick_mode(_ViewerPickMode.NORMAL)

    def _replace_atom_toggled(self, checked: bool) -> None:
        if not checked:
            if self._pick_mode is _ViewerPickMode.REPLACE_ATOM:
                self._set_pick_mode(_ViewerPickMode.NORMAL)
            return
        active = self._active_geometry_workspace()
        if (
            self._structure is None
            or active is None
            or active.read_only
            or active.coordinate_only
        ):
            blocker = QSignalBlocker(self._replace_atom_action)
            self._replace_atom_action.setChecked(False)
            del blocker
            return
        dialog = PeriodicTableDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            blocker = QSignalBlocker(self._replace_atom_action)
            self._replace_atom_action.setChecked(False)
            del blocker
            return
        element = dialog.selected_element
        if element is None:
            blocker = QSignalBlocker(self._replace_atom_action)
            self._replace_atom_action.setChecked(False)
            del blocker
            return
        self._replacement_element = element
        active.replacement_element = element
        self._set_pick_mode(_ViewerPickMode.REPLACE_ATOM)

    def _set_pick_mode(
        self,
        mode: _ViewerPickMode,
        *,
        announce: bool = True,
    ) -> None:
        if not isinstance(mode, _ViewerPickMode):
            raise TypeError("viewer pick mode must be _ViewerPickMode")
        if mode in {
            _ViewerPickMode.DISTANCE,
            _ViewerPickMode.ANGLE,
            _ViewerPickMode.BOND_ROTATION,
            _ViewerPickMode.DELETE_ATOM,
            _ViewerPickMode.REPLACE_ATOM,
            _ViewerPickMode.AU_LATTICE_EXTENSION,
            _ViewerPickMode.ORCA_WBL_CONTACT,
        }:
            if self._structure is None:
                self._set_operation(
                    "Load or recover a structure before using viewer tools."
                )
                mode = _ViewerPickMode.NORMAL
        active = self._active_geometry_workspace()
        if (
            mode
            in {
                _ViewerPickMode.BOND_ROTATION,
                _ViewerPickMode.DELETE_ATOM,
                _ViewerPickMode.REPLACE_ATOM,
                _ViewerPickMode.AU_LATTICE_EXTENSION,
            }
            and active is not None
            and (
                active.read_only
                or (
                    mode
                    in {
                        _ViewerPickMode.DELETE_ATOM,
                        _ViewerPickMode.REPLACE_ATOM,
                        _ViewerPickMode.AU_LATTICE_EXTENSION,
                    }
                    and active.coordinate_only
                )
            )
        ):
            mode = _ViewerPickMode.NORMAL
        if (
            mode is _ViewerPickMode.REPLACE_ATOM
            and self._replacement_element is None
        ):
            mode = _ViewerPickMode.NORMAL
        if (
            mode is _ViewerPickMode.AU_LATTICE_EXTENSION
            and (
                active is None
                or not self._lattice_extension_editable(active)
            )
        ):
            mode = _ViewerPickMode.NORMAL

        previous_mode = self._pick_mode
        self._measurement_selection = ()
        self._pick_mode = mode
        if active is not None:
            active.pick_mode = mode
        distance_blocker = QSignalBlocker(self._distance_measure_action)
        angle_blocker = QSignalBlocker(self._angle_measure_action)
        rotation_blocker = QSignalBlocker(self._rotate_bond_action)
        deletion_blocker = QSignalBlocker(self._delete_atom_action)
        replacement_blocker = QSignalBlocker(self._replace_atom_action)
        extension_blocker = QSignalBlocker(self._electrode_extension_button)
        self._distance_measure_action.setChecked(
            mode is _ViewerPickMode.DISTANCE
        )
        self._angle_measure_action.setChecked(mode is _ViewerPickMode.ANGLE)
        self._rotate_bond_action.setChecked(
            mode is _ViewerPickMode.BOND_ROTATION
        )
        self._delete_atom_action.setChecked(mode is _ViewerPickMode.DELETE_ATOM)
        self._replace_atom_action.setChecked(mode is _ViewerPickMode.REPLACE_ATOM)
        self._electrode_extension_button.setChecked(
            mode is _ViewerPickMode.AU_LATTICE_EXTENSION
        )
        del (
            distance_blocker,
            angle_blocker,
            rotation_blocker,
            deletion_blocker,
            replacement_blocker,
            extension_blocker,
        )
        self._viewer.set_bond_rotation_enabled(
            mode is _ViewerPickMode.BOND_ROTATION
        )
        if (
            previous_mode is _ViewerPickMode.BOND_ROTATION
            and mode is not _ViewerPickMode.BOND_ROTATION
        ):
            self._clear_torsion_selection()
        if mode is _ViewerPickMode.AU_LATTICE_EXTENSION:
            self._viewer.set_preview_target_picking_enabled(True)
            if not self._refresh_lattice_extension_candidates():
                self._set_pick_mode(
                    _ViewerPickMode.NORMAL,
                    announce=False,
                )
                return
        else:
            self._viewer.set_preview_target_picking_enabled(False)
            self._viewer.set_preview_pick_targets(())
            if previous_mode is _ViewerPickMode.AU_LATTICE_EXTENSION:
                self._clear_lattice_extension_hover()
        self._restore_current_highlights()
        self._update_measurement_empty_state()

        if not announce:
            return
        if mode is _ViewerPickMode.DISTANCE:
            self._set_operation(
                "Distance measurement active. Select the first atom."
            )
        elif mode is _ViewerPickMode.ANGLE:
            self._set_operation(
                "Angle measurement active. Select A, then vertex B, then C."
            )
        elif mode is _ViewerPickMode.BOND_ROTATION:
            self._set_operation(
                "Bond Rotation active. Select an existing acyclic bridge edge."
            )
        elif mode is _ViewerPickMode.DELETE_ATOM:
            self._set_operation(
                "Delete Atom active. Click atoms to delete them; the mode remains active."
            )
        elif mode is _ViewerPickMode.REPLACE_ATOM:
            self._set_operation(
                f"Replace Atom active ({self._replacement_element}). Click atoms "
                "to replace them; the mode remains active."
            )
        elif mode is _ViewerPickMode.AU_LATTICE_EXTENSION:
            self._set_operation(
                "Au(111) lattice extension active. Hover a site to preview; "
                "red sites are blocked by the current geometry."
            )
        elif mode is _ViewerPickMode.ORCA_WBL_CONTACT:
            self._set_operation(
                "ORCA WBL contact selection active. Click a numbered S or N atom; "
                "press Esc to cancel."
            )
        elif mode is _ViewerPickMode.NORMAL:
            self._set_operation("Viewer editing tools are off.")

    def _enter_au_placement_mode(self) -> None:
        self._set_pick_mode(_ViewerPickMode.AU_PLACEMENT, announce=False)

    def _lattice_extension_toggled(self, checked: bool) -> None:
        if checked:
            self._set_pick_mode(_ViewerPickMode.AU_LATTICE_EXTENSION)
        elif self._pick_mode is _ViewerPickMode.AU_LATTICE_EXTENSION:
            self._set_pick_mode(_ViewerPickMode.NORMAL)

    def _lattice_extension_editable(
        self,
        workspace: _GeometryWorkspace,
    ) -> bool:
        is_bound = workspace is self._bound_geometry_workspace
        structure = self._structure if is_bound else workspace.structure
        connectivity = self._connectivity if is_bound else workspace.connectivity
        applied = (
            self._applied_electrode_result
            if is_bound
            else workspace.applied_electrode_result
        )
        covalent_radii = (
            self._covalent_radii if is_bound else workspace.covalent_radii
        )
        vdw_radii = self._vdw_radii if is_bound else workspace.vdw_radii
        step3_refresh_required = (
            self._submission_step3_refresh_required
            if is_bound
            else workspace.submission_step3_refresh_required
        )
        snapshot = self._recovery_snapshot if is_bound else workspace.recovery_snapshot
        transport_started = bool(
            snapshot is not None
            and any(
                step.kind is ProjectStepKind.TRANSPORT_CONVERGENCE
                and step.state
                not in {ProjectStepState.NOT_STARTED, ProjectStepState.SKIPPED}
                for step in snapshot.project.steps
            )
        )
        return bool(
            structure is not None
            and connectivity is not None
            and applied is not None
            and covalent_radii is not None
            and vdw_radii is not None
            and not workspace.read_only
            and not workspace.coordinate_only
            and not workspace.lattice_extension_submitted_immutable
            and not step3_refresh_required
            and not transport_started
            and not self._submission_running
            and not self._transport_operation_running
        )

    def _invalidate_lattice_extension_cache(
        self,
        workspace: _GeometryWorkspace | None = None,
    ) -> None:
        target = workspace or self._bound_geometry_workspace
        if target is None:
            return
        had_preview = target.lattice_extension_hover_identity is not None
        target.lattice_extension_candidates = ()
        target.lattice_extension_cache_valid = False
        target.lattice_extension_hover_identity = None
        target.viewer.set_preview_pick_targets(())
        target.viewer.set_lattice_extension_preview_guide(None)
        if had_preview:
            target.viewer.set_preview_atoms(())

    def _deactivate_lattice_extension_workspace(
        self,
        workspace: _GeometryWorkspace,
    ) -> None:
        """Clear transient extension interaction before workspace ownership moves."""

        if workspace.pick_mode is _ViewerPickMode.AU_LATTICE_EXTENSION:
            workspace.pick_mode = _ViewerPickMode.NORMAL
        if workspace is self._bound_geometry_workspace:
            self._pick_mode = workspace.pick_mode
        blocker = QSignalBlocker(workspace.electrode_extension_button)
        workspace.electrode_extension_button.setChecked(False)
        del blocker
        workspace.viewer.set_preview_target_picking_enabled(False)
        self._invalidate_lattice_extension_cache(workspace)

    def _refresh_lattice_extension_candidates(self) -> bool:
        workspace = self._bound_geometry_workspace
        if workspace is None or not self._lattice_extension_editable(workspace):
            self._set_operation(
                "Au(111) lattice extension requires editable accepted electrodes."
            )
            return False
        if (
            self._structure is None
            or self._applied_electrode_result is None
            or self._covalent_radii is None
            or self._vdw_radii is None
        ):
            self._set_operation(
                "Au(111) lattice extension configuration is incomplete."
            )
            return False
        try:
            if not workspace.lattice_extension_cache_valid:
                workspace.lattice_extension_candidates = (
                    interaction_candidates_for_working_geometry(
                        self._applied_electrode_result,
                        self._structure,
                        self._vdw_radii,
                    )
                )
                workspace.lattice_extension_cache_valid = True
            gold_radius = self._covalent_radii["Au"] * ATOM_RADIUS_SCALE
            targets = tuple(
                PreviewPickTarget(
                    candidate.identity,
                    candidate.coordinates,
                    gold_radius,
                )
                for candidate in workspace.lattice_extension_candidates
            )
            self._viewer.set_preview_pick_targets(targets)
        except (
            ElectrodeLatticeInteractionError,
            KeyError,
            TypeError,
            ValueError,
            VisualizationError,
        ) as error:
            self._invalidate_lattice_extension_cache(workspace)
            self._set_operation(
                f"Au(111) lattice extension is unavailable: {error}"
            )
            return False
        return True

    def _lattice_extension_target_hovered(self, identity: object) -> None:
        if self._pick_mode is not _ViewerPickMode.AU_LATTICE_EXTENSION:
            return
        if not isinstance(identity, AuLatticeExtensionSite):
            self._clear_lattice_extension_hover()
            self._set_operation("Ignored an invalid Au lattice candidate identity.")
            return
        workspace = self._bound_geometry_workspace
        if workspace is None:
            return
        matches = tuple(
            candidate
            for candidate in workspace.lattice_extension_candidates
            if candidate.identity == identity
        )
        if len(matches) != 1 or self._covalent_radii is None:
            self._clear_lattice_extension_hover()
            self._set_operation(
                "The Au lattice candidate is stale; refresh the extension mode."
            )
            return
        candidate = matches[0]
        color = (
            ELEMENT_COLORS_RGB["Au"]
            if candidate.availability is LatticeExtensionAvailability.AVAILABLE
            else BLOCKED_AU_EXTENSION_COLOR_RGB
        )
        guide_color = (
            AVAILABLE_AU_EXTENSION_GUIDE_COLOR_RGB
            if candidate.availability is LatticeExtensionAvailability.AVAILABLE
            else BLOCKED_AU_EXTENSION_COLOR_RGB
        )
        try:
            self._viewer.set_preview_atoms(
                (
                    PreviewAtom(
                        candidate.coordinates,
                        self._covalent_radii["Au"] * ATOM_RADIUS_SCALE,
                        color,
                        "Au",
                    ),
                )
            )
            self._viewer.set_lattice_extension_preview_guide(
                LatticeExtensionPreviewGuide(
                    candidate.coordinates,
                    candidate.predicted_bond_coordinates,
                    candidate.layer_basis_u,
                    candidate.layer_basis_v,
                    guide_color,
                )
            )
        except (KeyError, TypeError, ValueError, VisualizationError) as error:
            self._clear_lattice_extension_hover()
            self._set_operation(f"Au lattice preview failed: {error}")
            return
        workspace.lattice_extension_hover_identity = identity
        state = candidate.availability.value
        self._set_operation(
            f"{identity.side} layer {identity.layer_index + 1} site "
            f"{identity.lattice_key}: {state}."
        )

    def _clear_lattice_extension_hover(self) -> None:
        workspace = self._bound_geometry_workspace
        if workspace is None:
            return
        workspace.lattice_extension_hover_identity = None
        self._viewer.set_preview_atoms(())
        self._viewer.set_lattice_extension_preview_guide(None)

    def _lattice_extension_target_clicked(self, identity: object) -> None:
        workspace = self._bound_geometry_workspace
        if (
            self._pick_mode is not _ViewerPickMode.AU_LATTICE_EXTENSION
            or workspace is None
        ):
            return
        if not isinstance(identity, AuLatticeExtensionSite):
            self._set_operation("Rejected an invalid Au lattice candidate identity.")
            return
        if not self._lattice_extension_editable(workspace):
            self._set_pick_mode(_ViewerPickMode.NORMAL, announce=False)
            self._set_operation(
                "Au lattice extension is disabled because this geometry is immutable."
            )
            return
        if (
            self._structure is None
            or self._connectivity is None
            or self._applied_electrode_result is None
            or self._covalent_radii is None
            or self._vdw_radii is None
        ):
            self._set_operation("Au lattice extension state is incomplete.")
            return

        previous = self._capture_geometry_edit_state()
        try:
            result = add_lattice_extension_to_working_geometry(
                self._applied_electrode_result,
                self._structure,
                identity,
                self._vdw_radii,
            )
            display_orders = extend_bond_display_orders(
                self._connectivity,
                self._working_bond_display_orders(),
                result.connectivity,
            )
            anchors = detect_anchors(
                result.working_structure,
                result.connectivity,
            )
            self._viewer.replace_molecule_preserving_view(
                result.working_structure,
                result.connectivity,
                self._covalent_radii,
                display_orders,
            )
        except (
            ElectrodeLatticeInteractionError,
            KeyError,
            TypeError,
            ValueError,
            VisualizationError,
        ) as error:
            self._set_operation(f"Au lattice extension was not added: {error}")
            return

        self._structure = result.working_structure
        self._connectivity = result.connectivity
        self._bond_display_orders = display_orders
        self._anchors = anchors
        self._applied_electrode_result = result.applied
        self._confirmed = True
        self._measurement_selection = ()
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())
        self._geometry_undo_history.push(previous)
        self._update_geometry_history_actions()
        self._save_geometry_button.setEnabled(True)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_electrode_controls()
        self._invalidate_lattice_extension_cache(workspace)
        refreshed = self._refresh_lattice_extension_candidates()
        self._update_continuation_control()
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        if refreshed:
            self._set_operation(
                f"Added one {identity.side} Au extension at layer "
                f"{identity.layer_index + 1}, lattice key {identity.lattice_key}; "
                f"working geometry now has {len(self._structure)} atoms."
            )
        else:
            self._set_pick_mode(
                _ViewerPickMode.NORMAL,
                announce=False,
            )

    def _au_tool_visibility_toggled(self, visible: bool) -> None:
        workspace = self._active_geometry_workspace()
        if workspace is None:
            return
        workspace.builder_visible = visible
        if visible:
            self._enter_au_placement_mode()
        self._sync_anchor_highlights(workspace)

    def _restore_current_highlights(self) -> None:
        self._viewer.set_measurement_pick_feedback(())
        workspace = self._bound_geometry_workspace
        if workspace is None:
            return
        self._sync_anchor_highlights(workspace)

    def _sync_anchor_highlights(self, workspace: _GeometryWorkspace) -> None:
        """Show detection shells only while the compatible Au Tool is open."""

        is_bound = workspace is self._bound_geometry_workspace
        anchors = self._anchors if is_bound else workspace.anchors
        structure = self._structure if is_bound else workspace.structure
        enabled = bool(
            structure is not None
            and workspace.builder_visible
            and not workspace.read_only
            and not workspace.coordinate_only
        )
        workspace.viewer.set_highlighted_atom_indices(
            (
                candidate.binding_atom_index
                for candidate in anchors
                if enabled
            ),
            (
                atom_index
                for candidate in anchors
                if enabled
                for atom_index in candidate.attached_au_indices
            ),
        )
        workspace.anchor_highlight_legend.setVisible(enabled)

    def _show_measurement_selection(self) -> None:
        if self._structure is None:
            return
        self._viewer.set_measurement_pick_feedback(self._measurement_selection)

    def _bond_rotation_edge_picked(self, atom_a: int, atom_b: int) -> None:
        if self._pick_mode is not _ViewerPickMode.BOND_ROTATION:
            return
        structure = self._structure
        connectivity = self._connectivity
        if structure is None or connectivity is None:
            return
        try:
            session = BondTorsionSession(
                structure,
                connectivity,
                atom_a,
                atom_b,
            )
            reference = self._viewer.torsion_reference_direction(
                session.fixed_endpoint,
                session.rotating_endpoint,
            )
        except NonRotatableEdgeError as error:
            self._clear_torsion_selection()
            self._set_operation(str(error))
            return
        except (BondTorsionError, VisualizationError, TypeError, ValueError) as error:
            self._clear_torsion_selection()
            self._set_operation(f"Bond rotation selection failed: {error}")
            return
        self._torsion_session = session
        self._torsion_reference_direction = reference
        self._torsion_drag_start_structure = None
        self._show_torsion_gizmo()
        self._set_operation(
            f"Selected Connectivity edge {atom_a}-{atom_b}: fixed group "
            f"{len(session.fixed_indices)} atoms, rotating group "
            f"{len(session.rotating_indices)} atoms. Relative angle 0.0°."
        )

    def _show_torsion_gizmo(self) -> None:
        session = self._torsion_session
        reference = self._torsion_reference_direction
        if session is None or reference is None:
            self._viewer.clear_torsion_gizmo()
            return
        self._viewer.set_torsion_gizmo(
            TorsionGizmo(
                session.fixed_endpoint,
                session.rotating_endpoint,
                reference,
                session.relative_angle_degrees,
            )
        )

    def _clear_torsion_selection(self) -> None:
        self._torsion_session = None
        self._torsion_reference_direction = None
        self._torsion_drag_start_structure = None
        self._torsion_drag_start_angle = 0.0
        if hasattr(self, "_torsion_numeric_input"):
            self._cancel_torsion_numeric_edit()
        self._viewer.clear_torsion_gizmo()

    def _torsion_drag_started(self) -> None:
        session = self._torsion_session
        structure = self._structure
        if session is None or structure is None:
            self._torsion_drag_start_structure = None
            return
        self._torsion_drag_start_structure = structure
        self._torsion_drag_start_angle = session.relative_angle_degrees

    def _torsion_drag_changed(self, angle_delta: float) -> None:
        session = self._torsion_session
        start_structure = self._torsion_drag_start_structure
        current_structure = self._structure
        if (
            session is None
            or start_structure is None
            or current_structure is None
        ):
            return
        try:
            target_angle = normalize_signed_degrees(
                self._torsion_drag_start_angle + angle_delta
            )
            candidate = session.structure_at(target_angle)
            if structure_coordinates(candidate) == structure_coordinates(
                current_structure
            ):
                self._show_torsion_gizmo()
                return
            self._invalidate_coordinate_dependent_state()
            self._apply_torsion_structure(candidate)
        except (BondTorsionError, VisualizationError, TypeError, ValueError) as error:
            self._set_operation(f"Bond rotation failed: {error}")

    def _torsion_drag_finished(self) -> None:
        start_structure = self._torsion_drag_start_structure
        current_structure = self._structure
        self._torsion_drag_start_structure = None
        if start_structure is None or current_structure is None:
            return
        if structure_coordinates(start_structure) == structure_coordinates(
            current_structure
        ):
            return
        self._geometry_undo_history.push(
            self._capture_geometry_edit_state(structure=start_structure)
        )
        self._update_geometry_history_actions()
        session = self._torsion_session
        if session is not None:
            self._set_operation(
                f"Bond rotation set to {session.relative_angle_degrees:+.1f}°."
            )

    def _switch_torsion_side(self) -> None:
        session = self._torsion_session
        structure = self._structure
        if session is None or structure is None:
            return
        before = structure_coordinates(structure)
        try:
            session.switch_rotating_side(structure)
            if structure_coordinates(structure) != before:
                raise RuntimeError("torsion side switch changed coordinates")
            self._show_torsion_gizmo()
        except (BondTorsionError, VisualizationError, TypeError, ValueError) as error:
            self._set_operation(f"Rotating-side switch failed: {error}")
            return
        self._set_operation(
            "Rotating side switched without moving atoms; current geometry is "
            "the new 0.0° baseline."
        )

    def _start_torsion_numeric_edit(self) -> None:
        session = self._torsion_session
        if session is None:
            return
        self._torsion_numeric_input.setText(
            f"{session.relative_angle_degrees:.6g}"
        )
        self._measurement_escape_shortcut.setEnabled(False)
        self._viewer.begin_torsion_angle_edit()
        self._torsion_numeric_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._torsion_numeric_input.selectAll()

    def _cancel_torsion_numeric_edit(self) -> None:
        session = self._torsion_session
        if session is not None:
            self._torsion_numeric_input.setText(
                f"{session.relative_angle_degrees:.6g}"
            )
        if hasattr(self, "_measurement_escape_shortcut"):
            self._measurement_escape_shortcut.setEnabled(True)
        self._torsion_numeric_input.clearFocus()
        self._viewer.end_torsion_angle_edit()

    def _commit_torsion_numeric_edit(self) -> None:
        session = self._torsion_session
        structure = self._structure
        if session is None or structure is None:
            self._cancel_torsion_numeric_edit()
            return
        try:
            target_angle = normalize_signed_degrees(
                float(self._torsion_numeric_input.text().strip())
            )
            candidate = session.structure_at(target_angle)
        except (BondTorsionError, TypeError, ValueError) as error:
            self._set_operation(f"Invalid torsion angle: {error}")
            self._torsion_numeric_input.setFocus()
            self._torsion_numeric_input.selectAll()
            return
        if structure_coordinates(candidate) == structure_coordinates(structure):
            self._cancel_torsion_numeric_edit()
            self._show_torsion_gizmo()
            self._set_operation("Torsion angle is unchanged; no Undo state added.")
            return
        try:
            self._invalidate_coordinate_dependent_state()
            self._apply_torsion_structure(candidate)
        except (VisualizationError, TypeError, ValueError) as error:
            self._set_operation(f"Bond rotation failed: {error}")
            return
        self._geometry_undo_history.push(
            self._capture_geometry_edit_state(structure=structure)
        )
        self._update_geometry_history_actions()
        self._cancel_torsion_numeric_edit()
        self._set_operation(
            f"Bond rotation set exactly to {session.relative_angle_degrees:+.1f}°."
        )

    def _apply_torsion_structure(self, structure: MolecularStructure) -> None:
        self._commit_working_structure(structure)
        self._show_torsion_gizmo()
        self._update_continuation_control()

    def _commit_working_structure(self, structure: MolecularStructure) -> None:
        """Make one coordinate edit authoritative for this workspace session."""

        if not isinstance(structure, MolecularStructure):
            raise TypeError("the edited working geometry must be a structure")
        self._viewer.update_molecule_coordinates(structure)
        self._structure = structure
        workspace = self._bound_geometry_workspace
        if workspace is not None:
            workspace.structure = structure
            self._invalidate_lattice_extension_cache(workspace)

    def _capture_geometry_edit_state(
        self,
        *,
        structure: MolecularStructure | None = None,
    ) -> GeometryEditState:
        captured_structure = self._structure if structure is None else structure
        if captured_structure is None or self._connectivity is None:
            raise ValueError("no working geometry is available for Undo history")
        return GeometryEditState(
            structure=captured_structure,
            connectivity=self._connectivity,
            bond_display_orders=self._working_bond_display_orders(),
            measurements=self._measurement_session.snapshot(),
            applied_result=self._applied_result,
            applied_electrode_result=self._applied_electrode_result,
            confirmed=self._confirmed,
        )

    def _restore_geometry_edit_state(self, state: GeometryEditState) -> None:
        if not isinstance(state, GeometryEditState):
            raise TypeError("Geometry restore requires GeometryEditState")
        if self._covalent_radii is None:
            raise ValueError("covalent radii are not loaded")
        anchors = detect_anchors(state.structure, state.connectivity)
        self._viewer.replace_molecule_preserving_view(
            state.structure,
            state.connectivity,
            self._covalent_radii,
            state.bond_display_orders,
        )
        self._structure = state.structure
        self._connectivity = state.connectivity
        self._bond_display_orders = state.bond_display_orders
        self._anchors = anchors
        self._measurement_session.restore(state.measurements)
        self._measurement_selection = ()
        self._torsion_session = None
        self._torsion_reference_direction = None
        self._torsion_drag_start_structure = None
        self._torsion_drag_start_angle = 0.0
        self._current_proposals = ()
        self._applied_result = state.applied_result
        self._electrode_sites = ()
        self._electrode_site_controls = {}
        self._electrode_current_proposal = None
        self._applied_electrode_result = state.applied_electrode_result
        self._confirmed = state.confirmed
        workspace = self._bound_geometry_workspace
        self._invalidate_lattice_extension_cache(workspace)
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())
        self._restore_current_highlights()
        self._sync_measurement_view()
        self._picked_atom_label.setText("Selected atom: none")
        self._save_geometry_button.setEnabled(
            self._applied_result is not None
            or self._applied_electrode_result is not None
        )
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        if self._pick_mode is _ViewerPickMode.AU_LATTICE_EXTENSION:
            if not self._refresh_lattice_extension_candidates():
                self._set_pick_mode(
                    _ViewerPickMode.NORMAL,
                    announce=False,
                )
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        self._route_active_workspace()

    def _delete_picked_atom(self, atom_index: int) -> None:
        structure = self._structure
        connectivity = self._connectivity
        if structure is None or connectivity is None:
            return
        previous = self._capture_geometry_edit_state()
        atom_label = _atom_reference(structure, atom_index)
        try:
            result = delete_atom(structure, connectivity, atom_index)
            display_orders = remap_bond_display_orders(
                connectivity,
                self._working_bond_display_orders(),
                result.connectivity,
                result.old_to_new_indices,
            )
            removed_measurements = self._measurement_session.remap_atom_indices(
                result.old_to_new_indices
            )
            self._apply_atom_identity_edit(
                result.structure,
                result.connectivity,
                display_orders,
            )
        except (
            AtomEditingError,
            MeasurementError,
            VisualizationError,
            TypeError,
            ValueError,
        ) as error:
            self._measurement_session.restore(previous.measurements)
            self._set_operation(f"Atom deletion failed: {error}")
            return
        self._geometry_undo_history.push(previous)
        self._update_geometry_history_actions()
        self._set_operation(
            f"Deleted {atom_label}; {len(result.structure)} atoms remain; "
            f"removed {len(removed_measurements)} related measurement(s). "
            "Delete Atom remains active."
        )

    def _replace_picked_atom(self, atom_index: int) -> None:
        structure = self._structure
        connectivity = self._connectivity
        element = self._replacement_element
        if structure is None or connectivity is None or element is None:
            return
        source_atom = structure[atom_index]
        if source_atom.element == element:
            self._set_operation(
                f"{_atom_reference(structure, atom_index)} is already {element}; "
                "no edit or Undo state was added. Replace Atom remains active."
            )
            return
        previous = self._capture_geometry_edit_state()
        try:
            if (
                element not in ELEMENT_COLORS_RGB
                or self._covalent_radii is None
                or element not in self._covalent_radii
            ):
                raise ValueError(
                    f"element {element} is not supported by the current viewer"
                )
            result = replace_atom(structure, connectivity, atom_index, element)
            display_orders = self._working_bond_display_orders()
            self._apply_atom_identity_edit(
                result.structure,
                result.connectivity,
                display_orders,
            )
        except (
            AtomEditingError,
            VisualizationError,
            TypeError,
            ValueError,
        ) as error:
            self._set_operation(f"Atom replacement failed: {error}")
            return
        self._geometry_undo_history.push(previous)
        self._update_geometry_history_actions()
        self._set_operation(
            f"Replaced {source_atom.element}{atom_index} with {element}{atom_index}; "
            "measurements were preserved. Replace Atom remains active."
        )

    def _apply_atom_identity_edit(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        bond_display_orders: tuple[BondDisplayOrder, ...],
    ) -> None:
        if self._covalent_radii is None:
            raise ValueError("covalent radii are not loaded")
        anchors = detect_anchors(structure, connectivity)
        self._viewer.replace_molecule_preserving_view(
            structure,
            connectivity,
            self._covalent_radii,
            bond_display_orders,
        )
        self._structure = structure
        self._connectivity = connectivity
        self._bond_display_orders = bond_display_orders
        self._anchors = anchors
        self._measurement_selection = ()
        self._torsion_session = None
        self._torsion_reference_direction = None
        self._torsion_drag_start_structure = None
        self._torsion_drag_start_angle = 0.0
        self._current_proposals = ()
        self._applied_result = None
        self._reset_electrode_session_state()
        self._confirmed = False
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())
        self._restore_current_highlights()
        self._sync_measurement_view()
        self._picked_atom_label.setText("Selected atom: none")
        self._save_geometry_button.setEnabled(False)
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        self._update_continuation_control()
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        self._route_active_workspace()

    def _invalidate_coordinate_dependent_state(self) -> None:
        self._invalidate_lattice_extension_cache()
        self._measurement_selection = ()
        self._measurement_session.clear()
        self._sync_measurement_view()
        self._viewer.set_measurement_pick_feedback(())

        anchor_blockers = [
            QSignalBlocker(controls.checkbox)
            for controls in self._site_controls.values()
        ]
        for controls in self._site_controls.values():
            controls.checkbox.setChecked(False)
            if controls.parameter_widget is not None:
                controls.parameter_widget.setVisible(False)
        electrode_blockers = [
            QSignalBlocker(checkbox)
            for checkbox in self._electrode_site_controls.values()
        ]
        for checkbox in self._electrode_site_controls.values():
            checkbox.setChecked(False)
        del anchor_blockers, electrode_blockers
        self._current_proposals = ()
        self._electrode_current_proposal = None
        self._electrode_done_button.setEnabled(False)
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())
        self._restore_current_highlights()

    def _undo_geometry(self) -> None:
        self._restore_geometry_from_history(redo=False)

    def _redo_geometry(self) -> None:
        self._restore_geometry_from_history(redo=True)

    def _restore_geometry_from_history(self, *, redo: bool) -> None:
        active = self._active_geometry_workspace()
        if active is not None and active.read_only:
            return
        if self._structure is None or self._connectivity is None:
            return
        operation = "Redo" if redo else "Undo"
        restore = (
            self._geometry_undo_history.redo
            if redo
            else self._geometry_undo_history.undo
        )
        try:
            current = self._capture_geometry_edit_state()
            restored = restore(current)
        except (BondTorsionError, TypeError, ValueError) as error:
            self._clear_torsion_selection()
            self._update_geometry_history_actions()
            self._set_operation(f"Geometry {operation} failed: {error}")
            return
        if restored is None:
            return
        try:
            self._restore_geometry_edit_state(restored)
        except (
            MeasurementError,
            VisualizationError,
            TypeError,
            ValueError,
        ) as error:
            self._geometry_undo_history.clear()
            self._update_geometry_history_actions()
            self._set_operation(f"Geometry {operation} failed: {error}")
            return
        self._clear_torsion_selection()
        self._update_geometry_history_actions()
        self._update_continuation_control()
        direction = "next" if redo else "previous"
        self._set_operation(
            f"Restored the {direction} Geometry edit state."
        )

    def _update_geometry_history_actions(self) -> None:
        active = self._active_geometry_workspace()
        owns_actions = (
            active is not None
            and active is self._bound_geometry_workspace
            and not active.read_only
        )
        undo_enabled = owns_actions and self._geometry_undo_history.can_undo
        redo_enabled = owns_actions and self._geometry_undo_history.can_redo
        self._geometry_undo_action.setEnabled(undo_enabled)
        self._geometry_redo_action.setEnabled(redo_enabled)

    def _measurement_atom_picked(self, atom_index: int) -> None:
        structure = self._structure
        if structure is None or atom_index >= len(structure):
            return
        if self._pick_mode is _ViewerPickMode.DISTANCE:
            self._distance_atom_picked(atom_index)
        elif self._pick_mode is _ViewerPickMode.ANGLE:
            self._angle_atom_picked(atom_index)

    def _distance_atom_picked(self, atom_index: int) -> None:
        structure = self._structure
        if structure is None:
            return
        if not self._measurement_selection:
            self._measurement_selection = (atom_index,)
            self._show_measurement_selection()
            self._set_operation(
                f"Distance A = {_atom_reference(structure, atom_index)}. "
                "Select the second atom."
            )
            return
        atom_a = self._measurement_selection[0]
        if atom_index == atom_a:
            self._set_operation("Select a different second atom.")
            return
        try:
            measurement = self._measurement_session.add_distance(
                structure,
                atom_a,
                atom_index,
            )
        except (MeasurementError, TypeError) as error:
            self._measurement_selection = ()
            self._viewer.set_measurement_pick_feedback(())
            self._set_operation(f"Distance measurement not created: {error}")
            return
        self._measurement_selection = ()
        self._sync_measurement_view(clear_pick_feedback=True)
        self._set_operation(
            f"Distance M{measurement.measurement_id}: "
            f"{_atom_reference(structure, measurement.atom_a)} – "
            f"{_atom_reference(structure, measurement.atom_b)} = "
            f"{measurement.value_angstrom:.3f} Å. "
            "Select the first atom for another distance."
        )

    def _angle_atom_picked(self, atom_index: int) -> None:
        structure = self._structure
        if structure is None:
            return
        selected = self._measurement_selection
        if atom_index in selected:
            self._set_operation(
                "Select a different atom; A, B, and C must be distinct."
            )
            return
        if len(selected) < 2:
            self._measurement_selection = (*selected, atom_index)
            self._show_measurement_selection()
            if len(self._measurement_selection) == 1:
                message = (
                    f"Angle A = {_atom_reference(structure, atom_index)}. "
                    "Select vertex B."
                )
            else:
                message = (
                    f"Angle B = {_atom_reference(structure, atom_index)} "
                    "(vertex). Select C."
                )
            self._set_operation(message)
            return
        atom_a, atom_b = selected
        try:
            measurement = self._measurement_session.add_angle(
                structure,
                atom_a,
                atom_b,
                atom_index,
            )
        except (MeasurementError, TypeError) as error:
            self._measurement_selection = ()
            self._viewer.set_measurement_pick_feedback(())
            self._set_operation(f"Angle measurement not created: {error}")
            return
        self._measurement_selection = ()
        self._sync_measurement_view(clear_pick_feedback=True)
        self._set_operation(
            f"Angle M{measurement.measurement_id}: "
            f"{_atom_reference(structure, measurement.atom_a)} – "
            f"{_atom_reference(structure, measurement.atom_b)} – "
            f"{_atom_reference(structure, measurement.atom_c)} = "
            f"{measurement.value_degrees:.1f}°. "
            "Select A for another angle."
        )

    def _sync_measurement_view(self, *, clear_pick_feedback: bool = False) -> None:
        structure = self._structure
        measurements = self._measurement_session.measurements
        if structure is None:
            if measurements:
                raise RuntimeError(
                    "manual measurements cannot outlive the displayed structure"
                )
            annotations: tuple[MeasurementOverlayAnnotation, ...] = ()
        else:
            annotations = tuple(
                _measurement_overlay(structure, measurement)
                for measurement in measurements
            )
        self._viewer.set_measurement_annotations(
            annotations,
            pick_feedback=() if clear_pick_feedback else None,
        )
        self._refresh_measurement_table()

    def _refresh_measurement_table(self) -> None:
        table = self._measurement_table
        table.setRowCount(0)
        structure = self._structure
        if structure is None:
            self._update_measurement_empty_state()
            return
        for measurement in self._measurement_session.measurements:
            row = table.rowCount()
            table.insertRow(row)
            if isinstance(measurement, DistanceMeasurement):
                values = (
                    "Distance",
                    f"{_atom_reference(structure, measurement.atom_a)} – "
                    f"{_atom_reference(structure, measurement.atom_b)}",
                    f"{measurement.value_angstrom:.3f}",
                    "Å",
                )
            else:
                values = (
                    "Angle",
                    f"{_atom_reference(structure, measurement.atom_a)} – "
                    f"{_atom_reference(structure, measurement.atom_b)} – "
                    f"{_atom_reference(structure, measurement.atom_c)}",
                    f"{measurement.value_degrees:.1f}",
                    "°",
                )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    measurement.measurement_id,
                )
                table.setItem(row, column, item)
        self._update_measurement_empty_state()

    def _update_measurement_empty_state(self) -> None:
        has_measurements = self._measurement_table.rowCount() > 0
        self._measurement_panel.setVisible(has_measurements)
        self._measurement_empty_state.setVisible(False)
        self._measurement_table.setVisible(has_measurements)

    def _clear_measurements(self) -> None:
        """Clear this workspace's records and picks without leaving its tool."""

        self._measurement_selection = ()
        self._measurement_session.clear()
        self._sync_measurement_view(clear_pick_feedback=True)
        self._set_operation("All measurements cleared.")

    def _show_measurement_context_menu(self, position) -> None:
        item = self._measurement_table.itemAt(position)
        if item is None:
            return
        row = item.row()
        menu = QMenu(self._measurement_table)
        delete_action = menu.addAction("Delete measurement")
        selected = menu.exec(
            self._measurement_table.viewport().mapToGlobal(position)
        )
        if selected is delete_action:
            self._delete_measurement_at_table_row(row)

    def _delete_measurement_at_table_row(self, row: int) -> None:
        if isinstance(row, bool) or not isinstance(row, int):
            raise TypeError("measurement table row must be an integer")
        item = self._measurement_table.item(row, 0)
        if item is None:
            raise MeasurementError(f"measurement table row does not exist: {row}")
        measurement_id = item.data(Qt.ItemDataRole.UserRole)
        deleted = self._measurement_session.delete(measurement_id)
        self._sync_measurement_view()
        self._set_operation(f"Deleted measurement M{deleted.measurement_id}.")

    def _prepare_for_structure_replacement(self) -> None:
        workspace = self._bound_geometry_workspace
        self._invalidate_lattice_extension_cache(workspace)
        self._measurement_selection = ()
        self._measurement_session.clear()
        self._geometry_undo_history.clear()
        self._torsion_session = None
        self._torsion_reference_direction = None
        self._torsion_drag_start_structure = None
        self._torsion_drag_start_angle = 0.0
        self._replacement_element = None
        self._pick_mode = _ViewerPickMode.NORMAL
        if hasattr(self, "_distance_measure_action"):
            distance_blocker = QSignalBlocker(self._distance_measure_action)
            angle_blocker = QSignalBlocker(self._angle_measure_action)
            rotation_blocker = QSignalBlocker(self._rotate_bond_action)
            deletion_blocker = QSignalBlocker(self._delete_atom_action)
            replacement_blocker = QSignalBlocker(self._replace_atom_action)
            extension_blocker = QSignalBlocker(
                self._electrode_extension_button
            )
            self._distance_measure_action.setChecked(False)
            self._angle_measure_action.setChecked(False)
            self._rotate_bond_action.setChecked(False)
            self._delete_atom_action.setChecked(False)
            self._replace_atom_action.setChecked(False)
            self._electrode_extension_button.setChecked(False)
            del (
                distance_blocker,
                angle_blocker,
                rotation_blocker,
                deletion_blocker,
                replacement_blocker,
                extension_blocker,
            )
            self._cancel_torsion_numeric_edit()
            self._update_geometry_history_actions()
        self._viewer.set_bond_rotation_enabled(False)
        self._viewer.set_preview_target_picking_enabled(False)
        self._viewer.set_preview_pick_targets(())
        self._viewer.set_measurement_pick_feedback(())
        self._viewer.set_measurement_annotations(())
        self._viewer.set_surface_verification((), ())
        self._measurement_table.setRowCount(0)
        self._update_measurement_empty_state()

    def _working_bond_display_orders(self) -> tuple[BondDisplayOrder, ...]:
        """Return exact current metadata, explicitly invalidating stale metadata."""

        if self._connectivity is None:
            return ()
        if not self._bond_display_orders:
            return single_bond_display_orders(self._connectivity)
        try:
            return validate_bond_display_orders(
                self._connectivity,
                self._bond_display_orders,
            )
        except (TypeError, ValueError):
            return single_bond_display_orders(self._connectivity)

    def _set_measurement_tools_enabled(self, enabled: bool) -> None:
        active = self._active_geometry_workspace()
        routed_enabled = (
            enabled
            and active is not None
            and active is self._bound_geometry_workspace
        )
        self._distance_measure_action.setEnabled(routed_enabled)
        self._angle_measure_action.setEnabled(routed_enabled)
        self._rotate_bond_action.setEnabled(
            routed_enabled and active is not None and not active.read_only
        )
        identity_editing_enabled = bool(
            routed_enabled
            and active is not None
            and not active.read_only
            and not active.coordinate_only
        )
        self._delete_atom_action.setEnabled(identity_editing_enabled)
        self._replace_atom_action.setEnabled(identity_editing_enabled)

    def _active_export_target(self):
        """Return the active scientific canvas, excluding surrounding UI."""

        workspace = self._active_workspace()
        if isinstance(workspace, _TransmissionWorkspace):
            return workspace.content
        if isinstance(workspace, _OrcaWblWorkspace):
            return workspace.content
        if isinstance(workspace, _GeometryWorkspace):
            return workspace.viewer if workspace.structure is not None else None
        if isinstance(
            workspace,
            (
                _DensityWorkspace,
                _DensityResultWorkspace,
                _TightBindingWorkspace,
            ),
        ):
            viewer = getattr(workspace.content, "viewer", None)
            return viewer if isinstance(viewer, MoleculeViewerWidget) else None
        return None

    def _export_current_view(self) -> None:
        """Export the active molecular/Cube or Transmission presentation."""

        target = self._active_export_target()
        workspace = self._active_workspace()
        if target is None or workspace is None:
            QMessageBox.information(
                self,
                "No view to export",
                "Open a molecular, Cube, or Transmission view first.",
            )
            return
        if isinstance(target, (TransmissionView, OrcaWblTransmissionView)):
            base_size = target.export_base_size()
        else:
            base_size = target.export_pixel_size()
        dialog = ViewExportDialog(
            base_size,
            _image_export_basename(workspace.display_title),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        request = dialog.selected_request
        dialog.deleteLater()
        try:
            image = target.capture_image(request.scale_factor)
            save_view_image(image, request.destination)
        except (
            MemoryError,
            OSError,
            TypeError,
            ValueError,
            ViewExportError,
            VisualizationError,
        ) as error:
            QMessageBox.critical(
                self,
                "Unable to export current view",
                str(error),
            )
            return
        self.statusBar().showMessage(
            f"View image saved: {request.destination}",
            5000,
        )

    def _open_view_settings(self) -> None:
        workspace = self._active_workspace()
        if isinstance(workspace, _TransmissionWorkspace):
            workspace.content.open_view_settings()
            return
        if isinstance(workspace, _OrcaWblWorkspace):
            workspace.content.open_view_settings()
            return
        if isinstance(
            workspace,
            (_DensityWorkspace, _DensityResultWorkspace),
        ):
            workspace.content.open_view_settings()
            return
        self._store_bound_geometry_workspace(
            capture_builder_visibility=False,
        )
        committed_preferences = self._view_preferences
        active = self._active_geometry_workspace()
        committed_orbital_preferences = (
            active.orbital_preferences if active is not None else None
        )
        elements = {
            atom.element
            for workspace in self._workspaces_by_widget.values()
            if isinstance(workspace, _GeometryWorkspace)
            and workspace.structure is not None
            for atom in workspace.structure
        }
        if active is not None and active.scalar_field is not None:
            if active.orbital_preferences is None:
                raise RuntimeError("Cube workspace has no orbital display state")
            dialog = ViewSettingsDialog(
                self._view_preferences,
                elements,
                self,
                orbital_preferences=active.orbital_preferences,
                orbital_maximum_isovalue=(
                    active.scalar_field.maximum_absolute_value
                ),
            )
            dialog.preview_orbital_preferences_changed.connect(
                lambda preferences, runtime_id=active.runtime_id: (
                    self._apply_orbital_surface_preferences(
                        runtime_id,
                        preferences,
                    )
                )
            )
        else:
            dialog = ViewSettingsDialog(
                self._view_preferences,
                elements,
                self,
            )
        dialog.preview_preferences_changed.connect(
            self._apply_view_preferences
        )
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self._apply_view_preferences(dialog.selected_preferences())
            if active is not None and active.scalar_field is not None:
                selected_orbital_preferences = (
                    dialog.selected_orbital_preferences()
                )
                assert selected_orbital_preferences is not None
                self._apply_orbital_surface_preferences(
                    active.runtime_id,
                    selected_orbital_preferences,
                )
                self._persist_orbital_lighting(selected_orbital_preferences)
        else:
            self._apply_view_preferences(committed_preferences)
            if active is not None and committed_orbital_preferences is not None:
                self._apply_orbital_surface_preferences(
                    active.runtime_id,
                    committed_orbital_preferences,
                )

    def _element_labels_toggled(self, checked: bool) -> None:
        if checked == self._view_preferences.show_element_labels:
            return
        self._apply_view_preferences(
            replace(
                self._view_preferences,
                show_element_labels=checked,
            )
        )

    def _apply_view_preferences(self, preferences: ViewPreferences) -> None:
        if not isinstance(preferences, ViewPreferences):
            raise TypeError("view preferences must be ViewPreferences")
        self._view_preferences = preferences
        if hasattr(self, "_element_labels_action"):
            blocker = QSignalBlocker(self._element_labels_action)
            self._element_labels_action.setChecked(
                preferences.show_element_labels
            )
            del blocker
        for workspace in self._workspaces_by_widget.values():
            if isinstance(workspace, _GeometryWorkspace):
                workspace.viewer.set_view_preferences(preferences)

    def _apply_orbital_surface_preferences(
        self,
        runtime_id: UUID,
        preferences: OrbitalSurfacePreferences,
    ) -> None:
        """Apply Cube-only live preview to exactly its owning workspace."""

        if not isinstance(preferences, OrbitalSurfacePreferences):
            raise TypeError(
                "orbital surface preferences must be OrbitalSurfacePreferences"
            )
        workspace = self._workspace_by_runtime_id(runtime_id)
        if workspace is None or workspace.scalar_field is None:
            return
        if preferences.isovalue > workspace.scalar_field.maximum_absolute_value:
            raise ValueError("orbital isovalue exceeds the scalar field data range")
        workspace.orbital_preferences = preferences
        if workspace is self._bound_geometry_workspace:
            self._orbital_preferences = preferences
        workspace.viewer.set_orbital_surface_preferences(preferences)

    def _persist_orbital_lighting(
        self,
        preferences: OrbitalSurfacePreferences,
    ) -> None:
        """Remember only accepted Cube lighting across future app launches."""

        lighting = PersistedOrbitalLighting(
            ambient=preferences.ambient,
            light_intensity=preferences.light_intensity,
            specular=preferences.specular,
            shininess=preferences.shininess,
        )
        repository = self._user_view_preferences_repository
        if repository is not None and lighting != self._persisted_orbital_lighting:
            try:
                repository.save(lighting)
            except UserViewPreferencesError as error:
                QMessageBox.critical(
                    self,
                    "Unable to save View settings",
                    str(error),
                )
                return
        self._persisted_orbital_lighting = lighting

    def _open_bond_detection(self) -> None:
        self._store_bound_geometry_workspace(
            capture_builder_visibility=False,
        )
        committed_factor = self._connectivity_multiplier
        snapshots = self._capture_inferred_connectivity_snapshots()
        dialog = BondDetectionDialog(self._connectivity_multiplier, self)
        dialog.preview_factor_changed.connect(
            lambda factor: self._preview_bond_detection_factor(
                factor,
                snapshots,
            )
        )
        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted:
            self._restore_inferred_connectivity_preview(
                committed_factor,
                snapshots,
            )
            return
        selected_factor = dialog.selected_factor()
        self._preview_bond_detection_factor(
            selected_factor,
            snapshots,
        )
        self._finalize_inferred_connectivity_preview(snapshots)
        active = self._active_geometry_workspace()
        if active is not None:
            self._set_operation(
                f"Bond threshold factor set to {selected_factor:.2f}; "
                f"updated {len(snapshots)} inferred Geometry workspace(s); "
                "explicit MOL connectivity was preserved."
            )

    def _capture_inferred_connectivity_snapshots(
        self,
    ) -> tuple[_InferredConnectivitySnapshot, ...]:
        affected = tuple(
            workspace
            for workspace in self._workspaces_by_widget.values()
            if isinstance(workspace, _GeometryWorkspace)
            and workspace.source_structure is not None
            and workspace.source_connectivity_source
            is ConnectivitySource.INFERRED
        )
        snapshots: list[_InferredConnectivitySnapshot] = []
        for workspace in affected:
            if (
                workspace.source_connectivity is None
                or workspace.connectivity is None
            ):
                raise RuntimeError(
                    "an inferred Geometry workspace has no connectivity state"
                )
            snapshots.append(
                _InferredConnectivitySnapshot(
                    runtime_id=workspace.runtime_id,
                    source_connectivity=workspace.source_connectivity,
                    source_bond_display_orders=(
                        workspace.source_bond_display_orders
                    ),
                    connectivity=workspace.connectivity,
                    bond_display_orders=workspace.bond_display_orders,
                    anchors=workspace.anchors,
                )
            )
        return tuple(snapshots)

    def _preview_bond_detection_factor(
        self,
        factor: float,
        snapshots: tuple[_InferredConnectivitySnapshot, ...],
    ) -> None:
        if (
            isinstance(factor, bool)
            or not isinstance(factor, (int, float))
            or not math.isfinite(factor)
            or not (
                MINIMUM_BOND_THRESHOLD_FACTOR
                <= float(factor)
                <= MAXIMUM_BOND_THRESHOLD_FACTOR
            )
        ):
            raise ValueError(
                "bond threshold factor must be between 0.01 and 10.00"
            )
        preview_factor = float(factor)
        if preview_factor == self._connectivity_multiplier:
            return

        updates: list[
            tuple[
                _GeometryWorkspace,
                Connectivity,
                tuple[BondDisplayOrder, ...],
                Connectivity,
                tuple[BondDisplayOrder, ...],
                tuple[AnchorCandidate, ...],
            ]
        ] = []
        for snapshot in snapshots:
            workspace = self._workspace_by_runtime_id(snapshot.runtime_id)
            if (
                workspace is None
                or workspace.source_structure is None
                or workspace.structure is None
                or workspace.covalent_radii is None
            ):
                raise RuntimeError(
                    "an inferred Geometry workspace became unavailable during preview"
                )
            source_connectivity = infer_connectivity(
                workspace.source_structure,
                workspace.covalent_radii,
                multiplier=preview_factor,
            )
            source_orders = single_bond_display_orders(source_connectivity)
            if workspace.structure is workspace.source_structure:
                connectivity = source_connectivity
                display_orders = source_orders
            else:
                connectivity = infer_connectivity(
                    workspace.structure,
                    workspace.covalent_radii,
                    multiplier=preview_factor,
                )
                display_orders = single_bond_display_orders(connectivity)
            anchors = detect_anchors(workspace.structure, connectivity)
            updates.append(
                (
                    workspace,
                    source_connectivity,
                    source_orders,
                    connectivity,
                    display_orders,
                    anchors,
                )
            )

        self._connectivity_multiplier = preview_factor
        for update in updates:
            self._set_inferred_connectivity_state(*update)

    def _restore_inferred_connectivity_preview(
        self,
        committed_factor: float,
        snapshots: tuple[_InferredConnectivitySnapshot, ...],
    ) -> None:
        self._connectivity_multiplier = committed_factor
        for snapshot in snapshots:
            workspace = self._workspace_by_runtime_id(snapshot.runtime_id)
            if workspace is None:
                continue
            self._set_inferred_connectivity_state(
                workspace,
                snapshot.source_connectivity,
                snapshot.source_bond_display_orders,
                snapshot.connectivity,
                snapshot.bond_display_orders,
                snapshot.anchors,
            )

    def _set_inferred_connectivity_state(
        self,
        workspace: _GeometryWorkspace,
        source_connectivity: Connectivity,
        source_bond_display_orders: tuple[BondDisplayOrder, ...],
        connectivity: Connectivity,
        bond_display_orders: tuple[BondDisplayOrder, ...],
        anchors: tuple[AnchorCandidate, ...],
    ) -> None:
        workspace.source_connectivity = source_connectivity
        workspace.source_bond_display_orders = source_bond_display_orders
        workspace.connectivity = connectivity
        workspace.bond_display_orders = bond_display_orders
        workspace.anchors = anchors
        if workspace is self._bound_geometry_workspace:
            self._source_connectivity = source_connectivity
            self._source_bond_display_orders = source_bond_display_orders
            self._connectivity = connectivity
            self._bond_display_orders = bond_display_orders
            self._anchors = anchors

        workspace.viewer.update_connectivity(connectivity, bond_display_orders)
        self._sync_anchor_highlights(workspace)
        torsion_session = workspace.torsion_session
        reference = workspace.torsion_reference_direction
        edges = {
            (bond.first_index, bond.second_index) for bond in connectivity
        }
        if (
            torsion_session is not None
            and reference is not None
            and torsion_session.selected_edge in edges
        ):
            workspace.viewer.set_torsion_gizmo(
                TorsionGizmo(
                    torsion_session.fixed_endpoint,
                    torsion_session.rotating_endpoint,
                    reference,
                    torsion_session.relative_angle_degrees,
                )
            )

    def _finalize_inferred_connectivity_preview(
        self,
        snapshots: tuple[_InferredConnectivitySnapshot, ...],
    ) -> None:
        for snapshot in snapshots:
            workspace = self._workspace_by_runtime_id(snapshot.runtime_id)
            if workspace is None or workspace.connectivity is None:
                continue
            edges = {
                (bond.first_index, bond.second_index)
                for bond in workspace.connectivity
            }
            torsion_invalid = (
                workspace.torsion_session is not None
                and workspace.torsion_session.selected_edge not in edges
            )
            anchors_changed = workspace.anchors != snapshot.anchors
            if not torsion_invalid and not anchors_changed:
                continue
            with self._geometry_callback_context(workspace.runtime_id):
                if torsion_invalid:
                    self._clear_torsion_selection()
                if anchors_changed:
                    self._current_proposals = ()
                    self._viewer.set_preview_atoms(())
                    self._viewer.set_annotations((), ())
                    self._rebuild_anchor_site_controls()
                    self._rebuild_electrode_controls()
                    self._update_continuation_control()

    def _open_server_profiles(self) -> None:
        try:
            dialog = create_server_profiles_dialog(self)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Unable to open Server Connections",
                str(error),
            )
            self._set_operation(f"Unable to open Server Connections: {error}")
            return
        dialog.exec()
        try:
            collection = ServerProfileRepository(server_profiles_path()).load()
        except Exception as error:
            QMessageBox.critical(
                self,
                "Unable to reload Server Connections",
                str(error),
            )
            self._set_operation(
                f"Unable to reload saved Server Connections: {error}"
            )
            return
        self._use_saved_server_profiles(collection.profiles)

    def _use_saved_server_profiles(
        self,
        profiles: tuple[ServerProfile, ...],
    ) -> None:
        """Replace same-process profile copies after Manage Servers closes."""

        profiles_by_id = {profile.profile_id: profile for profile in profiles}
        if self._recovery_profile is not None:
            self._recovery_profile = profiles_by_id.get(
                self._recovery_profile.profile_id,
                self._recovery_profile,
            )
        for workspace in self._workspaces_by_widget.values():
            if not isinstance(workspace, _GeometryWorkspace):
                continue
            if workspace.recovery_profile is not None:
                workspace.recovery_profile = profiles_by_id.get(
                    workspace.recovery_profile.profile_id,
                    workspace.recovery_profile,
                )
            if workspace.restart_draft is not None:
                updated = profiles_by_id.get(
                    workspace.restart_draft.profile.profile_id
                )
                if updated is not None:
                    workspace.restart_draft = replace(
                        workspace.restart_draft,
                        profile=updated,
                    )
        if self._projects_dialog is not None:
            for profile in profiles:
                self._projects_dialog.update_profile(profile)

    def _open_email_notifications(self) -> None:
        """Edit the selected profile locally without opening a connection."""

        try:
            repository = ServerProfileRepository(server_profiles_path())
            collection = repository.load()
        except Exception as error:
            QMessageBox.critical(
                self,
                "Unable to open Email Notifications",
                str(error),
            )
            self._set_operation(f"Unable to open Email Notifications: {error}")
            return
        if not collection.profiles:
            QMessageBox.critical(
                self,
                "No saved server",
                "Create and save a Server Connection before configuring email notifications.",
            )
            self._set_operation(
                "Email notifications require a saved Server Connection."
            )
            return

        selected_id = collection.last_selected_profile_id
        active_geometry = self._active_geometry_workspace()
        active_profile = (
            self._recovery_profile
            if active_geometry is self._bound_geometry_workspace
            else None
        )
        if active_profile is not None:
            selected_id = active_profile.profile_id
        profile = next(
            (
                item
                for item in collection.profiles
                if item.profile_id == selected_id
            ),
            collection.profiles[0],
        )
        dialog = EmailNotificationsDialog(profile, repository, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.saved_profile()
        if (
            active_profile is not None
            and active_profile.profile_id == updated.profile_id
        ):
            self._recovery_profile = updated
            self._store_bound_geometry_workspace(
                capture_builder_visibility=False,
            )
        if self._projects_dialog is not None:
            self._projects_dialog.update_profile(updated)
        self._set_operation(
            "Email notifications saved for "
            f"{updated.name}: "
            + (
                updated.email_notification_recipient
                if updated.email_notification_enabled
                else "Off"
            )
        )

    def _open_projects(self) -> None:
        if self._projects_dialog is not None:
            self._show_projects_dialog()
            return
        if self._submission_running:
            self._set_operation(
                "Wait for the current Slurm submission before recovering a project."
            )
            return
        try:
            dependencies = _create_project_submission_dependencies()
            collection = dependencies.profile_repository.load()
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare project recovery",
                error,
            )
            return
        if not collection.profiles:
            QMessageBox.critical(
                self,
                "No saved server",
                "Create and save a Server Connection before recovering projects.",
            )
            self._set_operation(
                "Project recovery requires a saved Server Connection."
            )
            return
        if dependencies.recovery_service is None:
            self._show_local_submission_error(
                "Unable to prepare project recovery",
                RuntimeError("project recovery service is unavailable"),
            )
            return
        dialog = CalculationProjectsDialog(
            collection.profiles,
            collection.last_selected_profile_id,
            dependencies.recovery_service,
            dependencies.secret_store,
            dependencies.known_hosts,
            self,
            project_geometry_service=dependencies.project_geometry_service,
            project_task_restart_service=(
                dependencies.project_task_restart_service
            ),
            transport_submission_service=(
                dependencies.transport_submission_service
            ),
            project_management_service=dependencies.project_management_service,
            density_service=self._density_service(dependencies),
            orca_recovery_service=dependencies.orca_recovery_service,
            orca_wbl_service=dependencies.orca_wbl_service,
            project_workspace_open=self._has_open_managed_project_workspace,
            project_has_external_operation=(
                self._project_has_external_operation
            ),
        )
        dialog.setModal(False)
        dialog.accepted.connect(self._project_recovery_accepted)
        dialog.geometry_workspace_requested.connect(
            self._open_project_geometry_workspace
        )
        dialog.restart_draft_requested.connect(
            self._open_project_restart_workspace
        )
        dialog.project_snapshot_updated.connect(
            self._project_snapshot_updated
        )
        dialog.orca_wbl_operation_status.connect(
            self._orca_wbl_operation_status_changed
        )
        dialog.transmission_workspace_requested.connect(
            self._open_transmission_workspace
        )
        dialog.density_workspace_requested.connect(self._open_density_task)
        dialog.orca_wbl_workspace_requested.connect(
            self._open_orca_wbl_workspace
        )
        dialog.orca_optimization_resubmit_requested.connect(
            self._resubmit_orca_optimization
        )
        self._projects_dialog = dialog
        self._projects_profile_repository = dependencies.profile_repository
        self._show_projects_dialog()

    @Slot()
    def _calculate_active_orca_wbl(self) -> None:
        workspace = self._active_orca_geometry_workspace()
        dialog = self._projects_dialog
        if workspace is None or dialog is None:
            return
        snapshot = workspace.recovery_snapshot
        profile = workspace.recovery_profile
        if snapshot is None or profile is None or not _orca_wbl_calculation_eligible(
            snapshot
        ):
            return
        dialog.calculate_orca_wbl(
            snapshot,
            profile,
            contact_atom_selector=self._select_orca_wbl_contact_atom,
        )

    def _select_orca_wbl_contact_atom(self, side: str) -> int | None:
        """Return one viewer-picked S/N atom without mutating recovered geometry."""

        workspace = self._active_orca_geometry_workspace()
        if (
            workspace is None
            or workspace is not self._bound_geometry_workspace
            or self._structure is None
            or self._orca_wbl_contact_selection_loop is not None
        ):
            return None
        self._orca_wbl_contact_selection_result = None
        self._set_pick_mode(_ViewerPickMode.ORCA_WBL_CONTACT, announce=False)
        self._viewer.set_parameter_labels(
            {index: str(index + 1) for index in range(len(self._structure))},
            {},
        )
        selection_viewer = self._viewer
        self._set_operation(
            f"Select the {side} ORCA WBL contact: click a numbered S or N atom; "
            "press Esc to cancel."
        )
        self.show()
        self.raise_()
        self.activateWindow()
        loop = QEventLoop(self)
        self._orca_wbl_contact_selection_loop = loop
        try:
            loop.exec()
            return self._orca_wbl_contact_selection_result
        finally:
            self._orca_wbl_contact_selection_loop = None
            self._orca_wbl_contact_selection_result = None
            if self._pick_mode is _ViewerPickMode.ORCA_WBL_CONTACT:
                self._set_pick_mode(_ViewerPickMode.NORMAL, announce=False)
            selection_viewer.set_parameter_labels({}, {})
            if selection_viewer is self._viewer:
                self._sync_measurement_view()

    def _cancel_orca_wbl_contact_selection(self) -> None:
        loop = self._orca_wbl_contact_selection_loop
        if loop is None:
            return
        self._orca_wbl_contact_selection_result = None
        if self._pick_mode is _ViewerPickMode.ORCA_WBL_CONTACT:
            self._set_pick_mode(_ViewerPickMode.NORMAL, announce=False)
        loop.quit()

    @Slot()
    def _run_active_orca_frequency(self) -> None:
        workspace = self._active_orca_geometry_workspace()
        if workspace is None:
            return
        snapshot = workspace.recovery_snapshot
        profile = workspace.recovery_profile
        if (
            snapshot is None
            or profile is None
            or not _orca_frequency_calculation_eligible(snapshot)
        ):
            return
        self._prepare_orca_frequency_submission((snapshot, profile))

    def _active_orca_geometry_workspace(self) -> _GeometryWorkspace | None:
        workspace = self._active_geometry_workspace()
        if (
            workspace is None
            or workspace.recovery_snapshot is None
            or workspace.recovery_snapshot.project.workflow_kind
            is not CalculationWorkflowKind.ORCA
        ):
            return None
        return workspace

    @Slot()
    def _resubmit_active_orca_optimization(self) -> None:
        workspace = self._active_orca_geometry_workspace()
        if workspace is None or workspace.structure is None:
            return
        snapshot = workspace.recovery_snapshot
        profile = workspace.recovery_profile
        if snapshot is None or profile is None:
            return
        self._resubmit_orca_optimization((snapshot, profile, workspace.structure))

    @Slot(object)
    def _resubmit_orca_optimization(self, request_data: object) -> None:
        """Create a new ORCA project while preserving resubmission semantics."""

        try:
            snapshot, profile, structure = request_data
            if not isinstance(snapshot, ProjectRecoverySnapshot):
                raise TypeError("ORCA resubmission snapshot is invalid")
            if not isinstance(profile, ServerProfile):
                raise TypeError("ORCA resubmission server profile is invalid")
            if not isinstance(structure, MolecularStructure):
                raise TypeError("ORCA resubmission structure is invalid")
            if snapshot.project.workflow_kind is not CalculationWorkflowKind.ORCA:
                raise ValueError("ORCA resubmission requires an ORCA project")
        except Exception as error:
            self._show_local_submission_error(
                "ORCA resubmission unavailable",
                error,
            )
            return
        if self._submission_running:
            return
        source_settings = snapshot.project.steps[0].orca_optimization_settings
        if source_settings is None:
            self._show_local_submission_error(
                "ORCA resubmission unavailable",
                ValueError("Persisted ORCA optimization settings are unavailable."),
            )
            return
        try:
            dependencies = _create_project_submission_dependencies()
            if dependencies.orca_submission_service is None:
                raise RuntimeError("The ORCA submission service is unavailable")
            settings_dialog = OrcaOptimizationSettingsDialog(
                structure,
                profile,
                self,
                initial_settings=source_settings,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Cannot configure ORCA resubmission",
                error,
            )
            return
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("ORCA optimization resubmission was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        project_id = uuid4()
        base_name = f"{snapshot.project.display_name}_resubmit"
        try:
            input_text = render_orca_optimization_input(structure, settings)
            preset = replace(
                profile.execution_preset,
                nodes=settings.scheduler_nodes,
                ntasks=settings.process_count,
                cpus_per_task=1,
                runtime_minutes=settings.runtime_minutes,
                memory_gb=settings.scheduler_memory_gb,
                omp_num_threads=1,
            )
            script_text = render_orca_submit_script(
                preset,
                profile.orca_runtime,
                project_id,
                settings,
            )
            saved_password_exists = (
                profile.save_password
                and dependencies.secret_store.get_password(profile.profile_id)
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "ORCA resubmission input generation failed",
                error,
            )
            return
        confirmation = OrcaSubmissionConfirmationDialog(
            profile,
            base_name,
            (
                "ORCA molecule optimization resubmission; any authoritatively "
                "active source Job will be cancelled first"
            ),
            input_text,
            script_text,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("ORCA optimization resubmission was cancelled.")
            return
        request = OrcaOptimizationSubmissionRequest(
            profile=profile,
            base_name=base_name,
            source_molecule_name=snapshot.project.source_molecule_name,
            structure=structure,
            settings=settings,
            supplied_password=confirmation.take_temporary_password(),
            project_id=project_id,
            resubmission_source_project=snapshot.project,
            profile_rebind_confirmed=snapshot.profile_rebind_confirmed,
        )
        self._start_submission(dependencies, request)

    @Slot(object)
    def _prepare_orca_frequency_submission(self, request_data: object) -> None:
        """Collect a separate user-authorized frequency job for verified ORCA opt."""

        try:
            snapshot, profile = request_data
            if not isinstance(snapshot, ProjectRecoverySnapshot) or not isinstance(
                profile, ServerProfile
            ):
                raise TypeError("ORCA frequency request is invalid")
            optimization = snapshot.project.steps[0]
            source = optimization.orca_optimization_settings
            if (
                snapshot.project.workflow_kind is not CalculationWorkflowKind.ORCA
                or optimization.state is not ProjectStepState.SUCCEEDED
                or optimization.orca_optimization_result is None
                or not optimization.orca_optimization_result.succeeded
                or source is None
                or snapshot.optimized_structure is None
            ):
                raise ValueError(
                    "ORCA frequency requires a verified successful optimization and final geometry"
                )
            dependencies = _create_project_submission_dependencies()
            if dependencies.orca_submission_service is None:
                raise RuntimeError("The ORCA submission service is unavailable")
            dialog = OrcaFrequencySettingsDialog(
                source,
                snapshot.optimized_structure,
                profile,
                self,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Cannot configure ORCA frequency",
                error,
            )
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("ORCA frequency submission was cancelled.")
            return
        settings = dialog.selected_settings()
        try:
            input_text = render_orca_frequency_input(
                snapshot.optimized_structure,
                settings,
            )
            preset = replace(
                profile.execution_preset,
                nodes=settings.scheduler_nodes,
                ntasks=settings.process_count,
                cpus_per_task=1,
                runtime_minutes=settings.runtime_minutes,
                memory_gb=settings.scheduler_memory_gb,
                omp_num_threads=1,
            )
            script_text = render_orca_submit_script(
                preset,
                profile.orca_runtime,
                snapshot.project.project_id,
                settings,
                frequency=True,
            )
            saved_password_exists = (
                profile.save_password
                and dependencies.secret_store.get_password(profile.profile_id)
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "ORCA frequency input generation failed",
                error,
            )
            return
        confirmation = OrcaSubmissionConfirmationDialog(
            profile,
            snapshot.project.remote_directory_name,
            "ORCA frequency verification",
            input_text,
            script_text,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("ORCA frequency submission was cancelled.")
            return
        request = OrcaFrequencySubmissionRequest(
            profile=profile,
            project=snapshot.project,
            optimized_structure=snapshot.optimized_structure,
            settings=settings,
            supplied_password=confirmation.take_temporary_password(),
        )
        self._start_submission(dependencies, request)

    def _density_service(self, dependencies):
        from moltage.app.density_workflow import DensityWorkflowService

        return DensityWorkflowService(
            dependencies.connection_service,
            density_results_path(),
        )

    def _new_density_workspace(self):
        source = self._active_geometry_workspace()
        if source is None:
            return
        if source is self._bound_geometry_workspace:
            self._store_bound_geometry_workspace(capture_builder_visibility=True)
        if source.structure is None or len(source.structure) < 2:
            return
        self._create_density_workspace(source.structure, source.display_title)

    @Slot()
    def _new_tight_binding_workspace(self) -> _TightBindingWorkspace | None:
        source = self._active_geometry_workspace()
        if source is None:
            return None
        if source is self._bound_geometry_workspace:
            self._store_bound_geometry_workspace(
                capture_builder_visibility=True,
            )
        if (
            source.structure is None
            or source.connectivity is None
            or len(source.structure) < 2
        ):
            return None
        try:
            content = TightBindingWorkspace(
                source.structure,
                source.connectivity,
                source.anchors,
                source.covalent_radii or load_default_covalent_radii(),
                source_title=source.display_title,
                bond_display_orders=source.bond_display_orders,
                view_preferences=self._view_preferences,
                parent=self._workspace_tabs,
            )
            workspace = _TightBindingWorkspace(
                runtime_id=uuid4(),
                identity=uuid4(),
                display_title=f"{source.display_title} — Tight Binding",
                content=content,
            )
            self._workspaces_by_widget[content] = workspace
            blocker = QSignalBlocker(self._workspace_tabs)
            self._workspace_tabs.addTab(content, workspace.display_title)
            del blocker
            self._set_workspace_tab_tooltip(workspace)
            self._focus_workspace(workspace)
            return workspace
        except Exception as error:
            QMessageBox.critical(
                self,
                "Local Tight-Binding Transmission",
                str(error),
            )
            return None

    def _open_density_task(self, request):
        task, profile = request
        for workspace in self._workspaces_by_widget.values():
            if isinstance(workspace, _DensityWorkspace) and workspace.identity == task.task_id:
                if not workspace.content.busy:
                    workspace.content.load_task(task)
                self._focus_workspace(workspace)
                return
        self._create_density_workspace(task.partition.structure, task.name, task=task, profile=profile)

    def _create_density_workspace(self, structure, title, *, task=None, profile=None):
        from moltage.gui.density_workspace import DensityWorkspace
        try:
            dependencies = _create_project_submission_dependencies()
            collection = dependencies.profile_repository.load()
            content = DensityWorkspace(structure, (profile,) if profile else collection.profiles,
                self._density_service(dependencies), dependencies.secret_store, self._workspace_tabs,
                task=task, view_preferences=self._view_preferences)
            identity = task.task_id if task else uuid4()
            workspace = _DensityWorkspace(
                uuid4(),
                identity,
                title + " — Density Calculation",
                content,
            )
            content.task_updated.connect(lambda updated: self._density_task_updated(workspace, updated))
            content.result_view_requested.connect(self._open_density_result)
            content.operation_state_changed.connect(
                self._notify_project_manager_external_operation_changed
            )
            self._workspaces_by_widget[content] = workspace
            blocker = QSignalBlocker(self._workspace_tabs)
            self._workspace_tabs.addTab(content, workspace.display_title)
            del blocker
            self._set_workspace_tab_tooltip(workspace)
            self._focus_workspace(workspace)
            return workspace
        except Exception as error:
            QMessageBox.critical(self, "Electron Density Difference", str(error))
            return None

    @Slot(object)
    def _open_density_result(self, request):
        from moltage.gui.density_workspace import (
            DensityResultView,
            DensityResultViewRequest,
        )

        if not isinstance(request, DensityResultViewRequest):
            QMessageBox.critical(
                self,
                "Electron Density Difference",
                "The recovered density result request is invalid.",
            )
            return None
        existing = self._density_result_workspaces_by_identity.get(
            request.identity
        )
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        try:
            content = DensityResultView(
                request,
                self._workspace_tabs,
                view_preferences=self._view_preferences,
                orbital_preferences=OrbitalSurfacePreferences(
                    resolution=OrbitalSurfaceResolution.MEDIUM,
                    ambient=self._persisted_orbital_lighting.ambient,
                    light_intensity=(
                        self._persisted_orbital_lighting.light_intensity
                    ),
                    specular=self._persisted_orbital_lighting.specular,
                    shininess=self._persisted_orbital_lighting.shininess,
                ),
            )
            content.lighting_accepted.connect(self._persist_orbital_lighting)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Electron Density Difference",
                str(error),
            )
            return None
        workspace = _DensityResultWorkspace(
            runtime_id=uuid4(),
            identity=request.identity,
            display_title=request.display_title,
            content=content,
        )
        self._workspaces_by_widget[content] = workspace
        self._density_result_workspaces_by_identity[request.identity] = workspace
        blocker = QSignalBlocker(self._workspace_tabs)
        self._workspace_tabs.addTab(content, request.display_title)
        del blocker
        self._set_workspace_tab_tooltip(workspace)
        self._focus_workspace(workspace)
        return workspace

    def _density_task_updated(self, workspace, task):
        workspace.identity = task.task_id
        if self._projects_dialog is not None:
            self._projects_dialog.update_density_task(task)

    def _has_open_managed_project_workspace(self, project_id: UUID) -> bool:
        """Fail-closed deletion guard keyed only by authoritative project UUID."""

        if not isinstance(project_id, UUID):
            raise TypeError("managed workspace lookup requires a project UUID")
        if any(
            isinstance(identity, ManagedGeometryWorkspaceIdentity)
            and identity.project_id == project_id
            for identity in self._geometry_workspaces_by_identity
        ):
            return True
        if any(
            identity.project_id == project_id
            for identity in self._transmission_workspaces_by_identity
        ):
            return True
        if any(
            identity.project_id == project_id
            for identity in self._orca_wbl_workspaces_by_identity
        ):
            return True
        return any(
            (
                isinstance(workspace, _DensityWorkspace)
                and workspace.identity == project_id
            )
            or (
                isinstance(workspace, _DensityResultWorkspace)
                and workspace.identity[0] == project_id
            )
            for workspace in self._workspaces_by_widget.values()
        )

    def _project_has_external_operation(self, project_id: UUID) -> bool:
        """Report live submission work not owned by Project Manager itself."""

        if not isinstance(project_id, UUID):
            raise TypeError("project operation lookup requires a project UUID")
        if self._submission_running:
            request = self._pending_submission_request
            if isinstance(request, NewProjectSubmissionRequest):
                return True
            submission_project_id, _step_kind = _submission_request_context(request)
            if submission_project_id == project_id:
                return True
        if any(
            isinstance(workspace, _DensityWorkspace)
            and workspace.identity == project_id
            and workspace.content.busy
            for workspace in self._workspaces_by_widget.values()
        ):
            return True
        return (
            self._transport_operation_running
            and self._pending_transport_project_id == project_id
        )

    def _notify_project_manager_external_operation_changed(self) -> None:
        dialog = self._projects_dialog
        if dialog is not None:
            dialog.external_operation_state_changed()

    def _show_projects_dialog(self) -> None:
        dialog = self._projects_dialog
        if dialog is None:
            return
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @Slot()
    def _project_recovery_accepted(self) -> None:
        dialog = self._projects_dialog
        profile_repository = self._projects_profile_repository
        if dialog is None or profile_repository is None:
            self._show_local_submission_error(
                "Unable to open recovered project",
                RuntimeError("project recovery window is unavailable"),
            )
            return
        try:
            snapshot, profile = dialog.selected_recovery()
            profile_repository.set_last_selected(profile.profile_id)
            self._open_recovered_geometry(snapshot, profile)
        except Exception as error:
            self._show_local_submission_error(
                "Unable to open recovered project",
                error,
            )

    def _workspace_for_new_geometry(
        self,
        display_title: str,
    ) -> tuple[_GeometryWorkspace, bool]:
        active = self._active_geometry_workspace()
        if (
            active is not None
            and active.identity is None
            and active.structure is None
        ):
            return active, False
        for candidate in self._workspaces_by_widget.values():
            if (
                isinstance(candidate, _GeometryWorkspace)
                and candidate.identity is None
                and candidate.structure is None
            ):
                self._focus_workspace(candidate)
                return candidate, False
        workspace = self._create_geometry_workspace(
            identity=None,
            display_title=display_title,
            select=True,
        )
        return workspace, True

    def _open_recovered_geometry(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
    ) -> _GeometryWorkspace:
        identity = ManagedGeometryWorkspaceIdentity.from_snapshot(snapshot)
        existing = self._geometry_workspaces_by_identity.get(identity)
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        title = f"{snapshot.project.remote_directory_name} — Geometry"
        workspace, created = self._workspace_for_new_geometry(title)
        try:
            self._load_recovered_snapshot(snapshot, profile)
        except Exception:
            if created:
                index = self._workspace_tabs.indexOf(workspace.content)
                if index >= 0:
                    self._close_workspace_tab(index)
            raise
        self._set_geometry_workspace_identity(workspace, identity, title)
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        self._route_active_workspace()
        return workspace

    def _configure_step1_orbital_controls(
        self,
        workspace: _GeometryWorkspace,
        binding: ProjectOrbitalCubeBinding | None,
    ) -> None:
        """Expose only the Cubes verified for this exact Step-1 structure."""

        while workspace.orbital_buttons_layout.count():
            item = workspace.orbital_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        workspace.orbital_binding = binding
        artifacts = binding.artifacts if binding is not None else ()
        workspace.orbital_cube_artifacts = artifacts
        workspace.orbital_buttons = {}
        workspace.active_orbital_filename = None
        workspace.orbital_load_running = False
        for artifact in artifacts:
            button = QPushButton(artifact.display_label, workspace.orbital_controls)
            button.setObjectName(
                "step1OrbitalCube_"
                + artifact.filename.removesuffix(".cube")
            )
            button.setCheckable(True)
            button.setToolTip(
                f"Download and display {artifact.filename} "
                f"({_format_file_size(artifact.size_bytes)}). "
                "Presentation only; molecular geometry and workflow inputs are "
                "unchanged."
            )
            button.clicked.connect(
                lambda _checked=False, workspace_id=workspace.runtime_id,
                selected=artifact: self._request_project_orbital_cube(
                    workspace_id,
                    selected,
                )
            )
            workspace.orbital_buttons[artifact.filename] = button
            workspace.orbital_buttons_layout.addWidget(button)
        workspace.orbital_controls.setVisible(bool(artifacts))

    def _request_project_orbital_cube(
        self,
        runtime_id: UUID,
        artifact: ProjectOrbitalCubeArtifact,
    ) -> None:
        workspace = self._workspace_by_runtime_id(runtime_id)
        if workspace is None or workspace.orbital_load_running:
            return
        binding = workspace.orbital_binding
        if (
            binding is None
            or artifact not in workspace.orbital_cube_artifacts
        ):
            self._set_workspace_operation(
                workspace,
                "This orbital Cube is not bound to the selected Step-1 "
                "result. Refresh the project.",
            )
            self._restore_orbital_button_checks(workspace)
            return
        if not _structure_retains_recovered_prefix(
            workspace.structure,
            binding.expected_structure,
            workspace.applied_result,
            workspace.applied_electrode_result,
        ):
            self._set_workspace_operation(
                workspace,
                "The recovered Step-1 atoms were edited, so its orbital Cube "
                "cannot be overlaid on this working geometry. Reload the "
                "recovered result first.",
            )
            self._restore_orbital_button_checks(workspace)
            return
        try:
            dependencies = _create_project_submission_dependencies()
            service = dependencies.project_orbital_cube_service
            if service is None:
                raise RuntimeError("project orbital Cube service is unavailable")
            password = self._temporary_transport_password(
                dependencies,
                binding.profile,
            )
            if password is False:
                self._set_workspace_operation(
                    workspace,
                    "Orbital Cube download was cancelled.",
                )
                self._restore_orbital_button_checks(workspace)
                return
            request = ProjectOrbitalCubeLoadRequest(
                profile=binding.profile,
                project=binding.project,
                artifact=artifact,
                expected_structure=binding.expected_structure,
                profile_rebind_confirmed=binding.profile_rebind_confirmed,
                supplied_password=(password if isinstance(password, str) else None),
            )
        except Exception as error:
            self._show_workspace_error(
                workspace,
                "Unable to prepare orbital Cube loading",
                error,
            )
            self._restore_orbital_button_checks(workspace)
            return

        worker = ProjectOrbitalCubeWorker(service, request)
        worker.signals.progress.connect(
            lambda message, active_worker=worker: (
                self._project_orbital_cube_progress(active_worker, message)
            )
        )
        worker.signals.succeeded.connect(
            lambda result, active_worker=worker: (
                self._project_orbital_cube_succeeded(active_worker, result)
            )
        )
        worker.signals.failed.connect(
            lambda error, active_worker=worker: (
                self._project_orbital_cube_failed(active_worker, error)
            )
        )
        worker.signals.finished.connect(self._project_orbital_cube_finished)
        self._orbital_workers.add(worker)
        self._orbital_worker_origins[worker] = runtime_id
        workspace.orbital_load_running = True
        self._set_orbital_buttons_enabled(workspace, False)
        self._restore_orbital_button_checks(workspace)
        self._set_workspace_operation(
            workspace,
            f"Loading {artifact.display_label} orbital Cube from Step 1...",
        )
        self._orbital_thread_pool.start(worker)

    def _project_orbital_cube_progress(
        self,
        worker: ProjectOrbitalCubeWorker,
        message: str,
    ) -> None:
        workspace = self._workspace_by_runtime_id(
            self._orbital_worker_origins.get(worker)
        )
        if workspace is not None:
            self._set_workspace_operation(workspace, message)

    def _project_orbital_cube_succeeded(
        self,
        worker: ProjectOrbitalCubeWorker,
        result: object,
    ) -> None:
        workspace = self._workspace_by_runtime_id(
            self._orbital_worker_origins.get(worker)
        )
        if workspace is None:
            return
        if not isinstance(result, ProjectOrbitalCubeLoadResult):
            self._project_orbital_cube_failed(
                worker,
                RuntimeError("orbital Cube loading returned invalid data"),
            )
            return
        binding = workspace.orbital_binding
        if (
            binding is None
            or not _structure_retains_recovered_prefix(
                workspace.structure,
                binding.expected_structure,
                workspace.applied_result,
                workspace.applied_electrode_result,
            )
        ):
            self._project_orbital_cube_failed(
                worker,
                RuntimeError(
                    "The working molecule changed relative to the recovered "
                    "Step-1 coordinates while the Cube was loading. The field "
                    "was not displayed."
                ),
            )
            return
        previous = workspace.orbital_preferences
        if previous is None:
            preferences = replace(
                initial_orbital_surface_preferences(result.scalar_field),
                ambient=self._persisted_orbital_lighting.ambient,
                light_intensity=self._persisted_orbital_lighting.light_intensity,
                specular=self._persisted_orbital_lighting.specular,
                shininess=self._persisted_orbital_lighting.shininess,
            )
        else:
            preferences = replace(
                previous,
                isovalue=min(
                    previous.isovalue,
                    result.scalar_field.maximum_absolute_value,
                ),
            )
        workspace.scalar_field = result.scalar_field
        workspace.orbital_preferences = preferences
        workspace.active_orbital_filename = result.artifact.filename
        if workspace is self._bound_geometry_workspace:
            self._scalar_field = result.scalar_field
            self._orbital_preferences = preferences
        workspace.viewer.set_orbital_surface(result.scalar_field, preferences)
        self._restore_orbital_button_checks(workspace)
        nx, ny, nz = result.scalar_field.dimensions
        self._set_workspace_operation(
            workspace,
            f"Displayed Step-1 {result.artifact.display_label} orbital "
            f"({nx}×{ny}×{nz}) on the current molecular skeleton. This is a "
            "presentation-only overlay; Au placement and calculation inputs are "
            "unchanged. Isosurface settings remain available under View.",
        )

    def _project_orbital_cube_failed(
        self,
        worker: ProjectOrbitalCubeWorker,
        error: object,
    ) -> None:
        workspace = self._workspace_by_runtime_id(
            self._orbital_worker_origins.get(worker)
        )
        if workspace is None:
            return
        self._restore_orbital_button_checks(workspace)
        self._show_workspace_error(
            workspace,
            "Unable to display orbital Cube",
            error,
        )

    @Slot(object)
    def _project_orbital_cube_finished(self, worker: object) -> None:
        if not isinstance(worker, ProjectOrbitalCubeWorker):
            return
        runtime_id = self._orbital_worker_origins.pop(worker, None)
        self._orbital_workers.discard(worker)
        workspace = self._workspace_by_runtime_id(runtime_id)
        if workspace is not None:
            workspace.orbital_load_running = False
            self._set_orbital_buttons_enabled(workspace, True)
            self._restore_orbital_button_checks(workspace)

    @staticmethod
    def _set_orbital_buttons_enabled(
        workspace: _GeometryWorkspace,
        enabled: bool,
    ) -> None:
        for button in (workspace.orbital_buttons or {}).values():
            button.setEnabled(enabled)

    @staticmethod
    def _restore_orbital_button_checks(workspace: _GeometryWorkspace) -> None:
        for filename, button in (workspace.orbital_buttons or {}).items():
            blocker = QSignalBlocker(button)
            button.setChecked(filename == workspace.active_orbital_filename)
            del blocker

    @staticmethod
    def _set_workspace_operation(
        workspace: _GeometryWorkspace,
        message: str,
    ) -> None:
        workspace.operation_label.setText(message)
        workspace.status_panel.setVisible(workspace.structure is not None)

    def _show_workspace_error(
        self,
        workspace: _GeometryWorkspace,
        title: str,
        error: object,
    ) -> None:
        message = str(error) if isinstance(error, Exception) else "Operation failed"
        self._set_workspace_operation(workspace, f"{title}: {message}")
        QMessageBox.critical(self, title, message)

    @Slot(object)
    def _open_project_geometry_workspace(
        self,
        result: object,
    ) -> _GeometryWorkspace | None:
        """Open or focus one immutable project-file geometry inspection tab."""

        if not isinstance(result, ProjectGeometryViewResult):
            error = TypeError("Project geometry workspace request is invalid")
            if self._projects_dialog is not None:
                self._projects_dialog.show_geometry_view_error(error)
            return None
        identity = ManagedGeometryWorkspaceIdentity(
            result.project_id,
            result.workspace_role,
        )
        existing = self._geometry_workspaces_by_identity.get(identity)
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        workspace, created = self._workspace_for_new_geometry(
            result.display_title
        )
        try:
            self._load_read_only_project_geometry(result)
        except Exception as error:
            if created:
                index = self._workspace_tabs.indexOf(workspace.content)
                if index >= 0:
                    self._close_workspace_tab(index)
            if self._projects_dialog is not None:
                self._projects_dialog.show_geometry_view_error(error)
            else:
                QMessageBox.critical(self, "Geometry view failed", str(error))
            return None
        self._set_geometry_workspace_identity(
            workspace,
            identity,
            result.display_title,
        )
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        self._route_active_workspace()
        return workspace

    @Slot(object)
    def _open_project_restart_workspace(
        self,
        draft: object,
    ) -> _GeometryWorkspace | None:
        """Open one editable restart draft without impersonating recovered state."""

        if not isinstance(draft, ProjectTaskRestartDraft):
            error = TypeError("Project restart draft is invalid")
            if self._projects_dialog is not None:
                self._projects_dialog.show_geometry_view_error(error)
            return None
        number = tuple(ProjectStepKind).index(draft.source_step) + 1
        identity = ManagedGeometryWorkspaceIdentity(
            draft.source_project.project_id,
            f"STEP_{number}_RESTART_DRAFT_{draft.source_job_id}",
        )
        existing = self._geometry_workspaces_by_identity.get(identity)
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        workspace, created = self._workspace_for_new_geometry(draft.display_title)
        try:
            self._load_restart_draft(draft)
        except Exception as error:
            if created:
                index = self._workspace_tabs.indexOf(workspace.content)
                if index >= 0:
                    self._close_workspace_tab(index)
            if self._projects_dialog is not None:
                self._projects_dialog.show_geometry_view_error(error)
            else:
                QMessageBox.critical(self, "Restart draft failed", str(error))
            return None
        self._set_geometry_workspace_identity(
            workspace,
            identity,
            draft.display_title,
        )
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        self._route_active_workspace()
        return workspace

    def _load_restart_draft(self, draft: ProjectTaskRestartDraft) -> None:
        number = tuple(ProjectStepKind).index(draft.source_step) + 1
        if self._bound_geometry_workspace is None:
            workspace, _created = self._workspace_for_new_geometry(
                draft.display_title
            )
            self._focus_workspace(workspace)
        workspace = self._bound_geometry_workspace
        assert workspace is not None
        self._configure_step1_orbital_controls(workspace, None)
        self._scalar_field = None
        self._orbital_preferences = None
        structure = draft.source_structure
        connectivity = draft.connectivity
        covalent_radii = load_default_covalent_radii()
        vdw_radii = load_default_vdw_radii()
        bond_display_orders = single_bond_display_orders(connectivity)
        anchors = detect_anchors(structure, connectivity)
        self._prepare_for_structure_replacement()
        self._viewer.set_molecule(
            structure,
            connectivity,
            covalent_radii,
            bond_display_orders,
        )
        self._viewer.set_surface_verification((), ())
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())

        workspace.read_only = False
        workspace.coordinate_only = draft.coordinate_only
        workspace.restart_draft = draft
        workspace.builder_visible = not draft.coordinate_only
        self._source_path = None
        self._source_structure = structure
        self._source_connectivity = connectivity
        self._source_connectivity_source = ConnectivitySource.INFERRED
        self._source_bond_display_orders = bond_display_orders
        self._structure = structure
        self._connectivity = connectivity
        self._connectivity_source = ConnectivitySource.INFERRED
        self._bond_display_orders = bond_display_orders
        self._covalent_radii = covalent_radii
        self._vdw_radii = vdw_radii
        self._anchors = anchors
        self._current_proposals = ()
        self._applied_result = None
        self._reset_electrode_session_state()
        self._confirmed = False
        self._recovery_snapshot = None
        self._recovery_profile = None
        self._submission_step2_refresh_required = False
        self._submission_step3_refresh_required = False
        self._sync_anchor_highlights(workspace)
        self._save_geometry_button.setEnabled(False)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        self._set_measurement_tools_enabled(True)
        self._generate_aims_action.setEnabled(not self._input_export_running)
        self._submit_aims_action.setEnabled(False)
        self._set_operation(
            f"Editable Step {number} restart draft loaded from the exact pre-run "
            "geometry.in. Submit remains locked until Refresh Status confirms "
            f"Slurm Job {draft.source_job_id} is terminal."
        )

    @Slot(object)
    def _project_snapshot_updated(self, snapshot: object) -> None:
        """Refresh workspace workflow context without replacing displayed geometry."""

        if not isinstance(snapshot, ProjectRecoverySnapshot):
            return
        changed = False
        for candidate in self._workspaces_by_widget.values():
            if not isinstance(candidate, _GeometryWorkspace):
                continue
            if (
                candidate.recovery_snapshot is not None
                and candidate.recovery_snapshot.project.project_id
                == snapshot.project.project_id
            ):
                candidate.recovery_snapshot = snapshot
                if candidate is self._bound_geometry_workspace:
                    self._recovery_snapshot = snapshot
                if (
                    snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA
                    and snapshot.active_step_kind
                    is ProjectStepKind.ORCA_WBL_TRANSMISSION
                    and snapshot.status_message
                ):
                    candidate.operation_label.setText(snapshot.status_message)
                changed = True
            draft = candidate.restart_draft
            if (
                draft is None
                or draft.source_project.project_id
                != snapshot.project.project_id
            ):
                continue
            step = next(
                item
                for item in snapshot.project.steps
                if item.kind is draft.source_step
            )
            terminal = (
                step.job_id == draft.source_job_id
                and step.state
                in {
                    ProjectStepState.FAILED,
                    ProjectStepState.SCHEDULER_COMPLETED,
                    ProjectStepState.SUCCEEDED,
                }
            )
            candidate.restart_draft = replace(
                draft,
                source_project=snapshot.project,
                source_terminal_confirmed=terminal,
            )
            if terminal:
                candidate.operation_label.setText(
                    f"Source Slurm Job {draft.source_job_id} is durably terminal. "
                    "The restart draft may now be submitted explicitly."
                )
            changed = True
        if changed:
            self._route_active_workspace()

    @Slot(object, str)
    def _orca_wbl_operation_status_changed(
        self,
        project_id: object,
        message: str,
    ) -> None:
        if not isinstance(project_id, UUID) or not message:
            return
        for candidate in self._workspaces_by_widget.values():
            if not isinstance(candidate, _GeometryWorkspace):
                continue
            snapshot = candidate.recovery_snapshot
            if snapshot is None or snapshot.project.project_id != project_id:
                continue
            candidate.operation_label.setText(message)
            if candidate is self._bound_geometry_workspace:
                self._set_operation(message)

    @Slot(object)
    def _open_transmission_workspace(
        self,
        request: object,
    ) -> _TransmissionWorkspace | None:
        if not isinstance(request, TransmissionWorkspaceRequest):
            error = TypeError(
                "Transmission workspace request has an unsupported type"
            )
            if self._projects_dialog is not None:
                self._projects_dialog.show_transmission_view_error(error)
            return None
        existing = self._transmission_workspaces_by_identity.get(request.identity)
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        try:
            content = TransmissionView(
                request.project_name,
                request.job_id,
                request.result_filename,
                request.result,
                self._workspace_tabs,
            )
        except Exception as error:
            if self._projects_dialog is not None:
                self._projects_dialog.show_transmission_view_error(error)
            else:
                QMessageBox.critical(
                    self,
                    "Transmission view failed",
                    str(error),
                )
            return None
        workspace = _TransmissionWorkspace(
            runtime_id=uuid4(),
            identity=request.identity,
            display_title=request.display_title,
            content=content,
        )
        self._workspaces_by_widget[content] = workspace
        self._transmission_workspaces_by_identity[request.identity] = workspace
        blocker = QSignalBlocker(self._workspace_tabs)
        self._workspace_tabs.addTab(content, request.display_title)
        del blocker
        self._set_workspace_tab_tooltip(workspace)
        self._focus_workspace(workspace)
        return workspace

    @Slot(object)
    def _open_orca_wbl_workspace(
        self,
        request: object,
    ) -> _OrcaWblWorkspace | None:
        if not isinstance(request, OrcaWblWorkspaceRequest):
            QMessageBox.critical(
                self,
                "ORCA WBL view failed",
                "ORCA WBL workspace request has an unsupported type",
            )
            return None
        existing = self._orca_wbl_workspaces_by_identity.get(request.identity)
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        try:
            content = OrcaWblTransmissionView(
                request.project_name,
                request.presentation,
                self._workspace_tabs,
            )
        except Exception as error:
            QMessageBox.critical(self, "ORCA WBL view failed", str(error))
            return None
        workspace = _OrcaWblWorkspace(
            runtime_id=uuid4(),
            identity=request.identity,
            display_title=request.display_title,
            content=content,
        )
        self._workspaces_by_widget[content] = workspace
        self._orca_wbl_workspaces_by_identity[request.identity] = workspace
        blocker = QSignalBlocker(self._workspace_tabs)
        self._workspace_tabs.addTab(content, request.display_title)
        del blocker
        self._set_workspace_tab_tooltip(workspace)
        self._focus_workspace(workspace)
        return workspace

    def _load_recovered_snapshot(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
    ) -> None:
        if self._bound_geometry_workspace is None:
            workspace, _created = self._workspace_for_new_geometry("Geometry")
            self._focus_workspace(workspace)
        workspace = self._bound_geometry_workspace
        assert workspace is not None
        orca_workflow = (
            snapshot.project.workflow_kind is CalculationWorkflowKind.ORCA
        )
        structure = snapshot.optimized_structure or (
            snapshot.submitted_structure if orca_workflow else None
        )
        connectivity = snapshot.connectivity
        if structure is None or connectivity is None:
            raise ValueError(
                "the selected project has no validated structure available"
            )
        covalent_radii = load_default_covalent_radii()
        vdw_radii = load_default_vdw_radii()
        bond_display_orders = single_bond_display_orders(connectivity)
        anchors = detect_anchors(structure, connectivity)
        orbital_binding = (
            ProjectOrbitalCubeBinding(
                profile=profile,
                project=snapshot.project,
                expected_structure=structure,
                artifacts=snapshot.orbital_cubes,
                diagnostic=snapshot.orbital_cube_diagnostic,
                profile_rebind_confirmed=snapshot.profile_rebind_confirmed,
            )
            if snapshot.active_step_kind is ProjectStepKind.MOLECULE_OPT
            else None
        )
        self._configure_step1_orbital_controls(workspace, orbital_binding)
        self._scalar_field = None
        self._orbital_preferences = None
        self._prepare_for_structure_replacement()
        self._viewer.set_molecule(
            structure,
            connectivity,
            covalent_radii,
            bond_display_orders,
        )
        if snapshot.surface_proposal is None:
            self._viewer.set_surface_verification((), ())
        else:
            self._viewer.set_surface_verification(
                snapshot.surface_proposal.left_zero_based,
                snapshot.surface_proposal.right_zero_based,
            )
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())

        self._source_path = None
        self._source_structure = structure
        self._source_connectivity = connectivity
        self._source_connectivity_source = ConnectivitySource.INFERRED
        self._source_bond_display_orders = bond_display_orders
        self._structure = structure
        self._connectivity = connectivity
        self._connectivity_source = ConnectivitySource.INFERRED
        self._bond_display_orders = bond_display_orders
        self._covalent_radii = covalent_radii
        self._vdw_radii = vdw_radii
        self._anchors = anchors
        self._current_proposals = ()
        self._applied_result = None
        self._reset_electrode_session_state()
        self._confirmed = False
        self._recovery_snapshot = snapshot
        self._recovery_profile = profile
        workspace.read_only = orca_workflow
        workspace.coordinate_only = False
        self._submission_step2_refresh_required = False
        self._submission_step3_refresh_required = False
        self._restore_current_highlights()
        self._save_geometry_button.setEnabled(False)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        self._set_measurement_tools_enabled(True)
        self._generate_aims_action.setEnabled(
            not orca_workflow and not self._input_export_running
        )
        self._submit_aims_action.setEnabled(False)
        self._update_continuation_control()
        step_number = {
            ProjectStepKind.MOLECULE_OPT: 1,
            ProjectStepKind.MOLECULE_AU_OPT: 2,
            ProjectStepKind.TRANSPORT_CONVERGENCE: 3,
            ProjectStepKind.TRANSMISSION: 4,
            ProjectStepKind.ORCA_OPTIMIZATION: 1,
            ProjectStepKind.ORCA_WBL_TRANSMISSION: 2,
            ProjectStepKind.ORCA_FREQUENCY: 3,
        }[snapshot.active_step_kind]
        if orca_workflow:
            stage = {
                ProjectStepKind.ORCA_OPTIMIZATION: "optimization",
                ProjectStepKind.ORCA_WBL_TRANSMISSION: "WBL transmission",
                ProjectStepKind.ORCA_FREQUENCY: "frequency verification",
            }[snapshot.active_step_kind]
            geometry_kind = (
                "verified optimized"
                if _orca_verified_optimization(snapshot) is not None
                else (
                    "validated output"
                    if snapshot.optimized_structure is not None
                    else "submitted input"
                )
            )
            message = (
                f"ORCA {geometry_kind} geometry loaded read-only for {stage}. "
                "Use Calculation > ORCA to resubmit optimization or, after a "
                "verified optimization, start WBL/frequency analysis. "
                + snapshot.status_message
            )
        elif step_number == 3 and snapshot.surface_proposal is not None:
            left = ", ".join(map(str, snapshot.surface_proposal.left_one_based))
            right = ", ".join(map(str, snapshot.surface_proposal.right_one_based))
            message = (
                "Step 3 completed successfully. Exact fixed geometry.in was "
                "recovered. Logical Left: "
                f"{left}; logical Right: {right}. "
                "Numbers are 1-based geometry.in/tcontrol indices."
            )
        else:
            message = (
                f"Step {step_number} completed successfully. "
                "FHI-aims optimized structure loaded from geometry.in.next_step."
                + (
                    " Apply both Au-pyramid electrodes to prepare Step 3."
                    if step_number == 2
                    else ""
                )
            )
        if snapshot.orbital_cubes:
            message += (
                f" {len(snapshot.orbital_cubes)} Step-1 orbital Cube file(s) "
                "are available from the controls at the upper right."
            )
        if snapshot.orbital_cube_diagnostic:
            message += f" {snapshot.orbital_cube_diagnostic}"
        self._set_operation(message)
        self._store_bound_geometry_workspace(capture_builder_visibility=False)

    def _load_read_only_project_geometry(
        self,
        result: ProjectGeometryViewResult,
    ) -> None:
        """Install immutable geometry and retain any ORCA action context."""

        if self._bound_geometry_workspace is None:
            workspace, _created = self._workspace_for_new_geometry(
                result.display_title
            )
            self._focus_workspace(workspace)
        workspace = self._bound_geometry_workspace
        assert workspace is not None
        self._configure_step1_orbital_controls(workspace, result.orbital_binding)
        self._scalar_field = None
        self._orbital_preferences = None
        structure = result.structure
        connectivity = result.connectivity
        covalent_radii = load_default_covalent_radii()
        vdw_radii = load_default_vdw_radii()
        bond_display_orders = single_bond_display_orders(connectivity)
        anchors = detect_anchors(structure, connectivity)
        self._prepare_for_structure_replacement()
        self._viewer.set_molecule(
            structure,
            connectivity,
            covalent_radii,
            bond_display_orders,
        )
        self._viewer.set_surface_verification((), ())
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())

        workspace.read_only = True
        self._source_path = None
        self._source_structure = structure
        self._source_connectivity = connectivity
        self._source_connectivity_source = ConnectivitySource.INFERRED
        self._source_bond_display_orders = bond_display_orders
        self._structure = structure
        self._connectivity = connectivity
        self._connectivity_source = ConnectivitySource.INFERRED
        self._bond_display_orders = bond_display_orders
        self._covalent_radii = covalent_radii
        self._vdw_radii = vdw_radii
        self._anchors = anchors
        self._current_proposals = ()
        self._applied_result = None
        self._reset_electrode_session_state()
        self._confirmed = False
        self._recovery_snapshot = result.recovery_snapshot
        self._recovery_profile = result.recovery_profile
        self._submission_step2_refresh_required = False
        self._submission_step3_refresh_required = False
        self._sync_anchor_highlights(workspace)
        self._save_geometry_button.setEnabled(False)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        self._set_measurement_tools_enabled(True)
        self._generate_aims_action.setEnabled(False)
        self._submit_aims_action.setEnabled(False)
        self._continue_step2_action.setEnabled(False)
        self._continue_step3_action.setEnabled(False)
        self._continue_step4_action.setEnabled(False)
        message = (
            f"Read-only project geometry loaded from {result.source_filename}: "
            f"{len(structure)} atoms, {len(connectivity)} inferred bonds."
        )
        if result.recovery_snapshot is not None:
            message += " Use Calculation > ORCA for the available ORCA actions."
        if result.orbital_binding is not None:
            if result.orbital_binding.artifacts:
                message += (
                    f" {len(result.orbital_binding.artifacts)} Step-1 orbital "
                    "Cube file(s) are available from the controls at the upper "
                    "right."
                )
            if result.orbital_binding.diagnostic:
                message += f" {result.orbital_binding.diagnostic}"
        self._set_operation(message)
        self._store_bound_geometry_workspace(capture_builder_visibility=False)

    def _open_xyz(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "New Geometry",
            "",
            _GEOMETRY_FILE_DIALOG_FILTER,
        )
        if selected_path:
            self._open_local_geometry(Path(selected_path))

    def _open_local_geometry(self, source_path: Path) -> _GeometryWorkspace | None:
        identity = LocalGeometryWorkspaceIdentity.from_path(source_path)
        existing = self._geometry_workspaces_by_identity.get(identity)
        if existing is not None:
            self._focus_workspace(existing)
            return existing
        workspace, created = self._workspace_for_new_geometry(
            identity.canonical_source_path.name
        )
        if not self._load(identity.canonical_source_path):
            if created:
                index = self._workspace_tabs.indexOf(workspace.content)
                if index >= 0:
                    self._close_workspace_tab(index)
            return None
        self._set_geometry_workspace_identity(
            workspace,
            identity,
            identity.canonical_source_path.name,
        )
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        return workspace

    def _reload(self) -> None:
        if self._source_path is not None:
            self._load(self._source_path)
            return
        if (
            self._source_structure is not None
            and self._recovery_snapshot is not None
            and self._recovery_profile is not None
        ):
            try:
                connectivity = infer_connectivity(
                    self._source_structure,
                    load_default_covalent_radii(),
                    multiplier=self._connectivity_multiplier,
                )
                orca_workflow = (
                    self._recovery_snapshot.project.workflow_kind
                    is CalculationWorkflowKind.ORCA
                )
                input_only = (
                    orca_workflow
                    and self._recovery_snapshot.optimized_structure is None
                )
                snapshot = replace(
                    self._recovery_snapshot,
                    optimized_structure=(
                        None if input_only else self._source_structure
                    ),
                    submitted_structure=(
                        self._source_structure
                        if input_only
                        else self._recovery_snapshot.submitted_structure
                    ),
                    connectivity=connectivity,
                )
                self._load_recovered_snapshot(snapshot, self._recovery_profile)
                self._set_operation(
                    "Updated connectivity for the recovered ORCA structure."
                    if orca_workflow
                    else "Updated connectivity for the recovered source structure; "
                    "unsubmitted working Au changes were discarded."
                )
            except Exception as error:
                QMessageBox.critical(
                    self,
                    "Unable to update recovered structure",
                    str(error),
                )
            return
        else:
            self._set_operation("No source structure is available to update.")

    def _load(self, source_path: Path) -> bool:
        if self._bound_geometry_workspace is None:
            workspace, _created = self._workspace_for_new_geometry(
                source_path.name
            )
            self._focus_workspace(workspace)
        try:
            multiplier = self._connectivity_multiplier
            covalent_radii = load_default_covalent_radii()
            vdw_radii = load_default_vdw_radii()
            cube_coordinate_unit = None
            if source_path.suffix.casefold() in {".cube", ".cub"}:
                if (
                    self._source_path == source_path
                    and self._scalar_field is not None
                ):
                    cube_coordinate_unit = (
                        self._scalar_field.source_coordinate_unit
                    )
                else:
                    cube_coordinate_unit = self._select_cube_coordinate_unit(
                        source_path
                    )
                    if cube_coordinate_unit is None:
                        return False
            loaded = load_geometry(
                source_path,
                covalent_radii,
                multiplier=multiplier,
                cube_coordinate_unit=cube_coordinate_unit,
            )
            structure = loaded.structure
            connectivity = loaded.connectivity
            connectivity_source = loaded.connectivity_source
            bond_display_orders = loaded.bond_display_orders
            scalar_field = loaded.scalar_field
            orbital_preferences = (
                replace(
                    initial_orbital_surface_preferences(scalar_field),
                    ambient=self._persisted_orbital_lighting.ambient,
                    light_intensity=(
                        self._persisted_orbital_lighting.light_intensity
                    ),
                    specular=self._persisted_orbital_lighting.specular,
                    shininess=self._persisted_orbital_lighting.shininess,
                )
                if scalar_field is not None
                else None
            )
            anchors = detect_anchors(structure, connectivity)
            self._prepare_for_structure_replacement()
            self._viewer.set_molecule(
                structure,
                connectivity,
                covalent_radii,
                bond_display_orders,
                scalar_field=scalar_field,
                orbital_preferences=orbital_preferences,
            )
        except (
            OSError,
            UnicodeError,
            CovalentRadiiError,
            VdwRadiiError,
            UnsupportedElementError,
            VisualizationError,
            TypeError,
            ValueError,
        ) as error:
            QMessageBox.critical(
                self,
                "Unable to display molecule",
                str(error),
            )
            return False

        self._source_path = source_path
        self._source_structure = structure
        self._source_connectivity = connectivity
        self._source_connectivity_source = connectivity_source
        self._source_bond_display_orders = bond_display_orders
        self._structure = structure
        self._connectivity = connectivity
        self._connectivity_source = connectivity_source
        self._bond_display_orders = bond_display_orders
        self._scalar_field = scalar_field
        self._orbital_preferences = orbital_preferences
        self._covalent_radii = covalent_radii
        self._vdw_radii = vdw_radii
        self._anchors = anchors
        self._current_proposals = ()
        self._applied_result = None
        self._reset_electrode_session_state()
        self._confirmed = False
        self._recovery_snapshot = None
        self._recovery_profile = None
        self._submission_step2_refresh_required = False
        self._submission_step3_refresh_required = False
        workspace = self._bound_geometry_workspace
        assert workspace is not None
        self._configure_step1_orbital_controls(workspace, None)
        workspace.read_only = scalar_field is not None
        workspace.coordinate_only = False
        if scalar_field is not None:
            workspace.builder_visible = False
        self._sync_anchor_highlights(workspace)
        self._save_geometry_button.setEnabled(False)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        self._set_measurement_tools_enabled(True)
        self._generate_aims_action.setEnabled(
            scalar_field is None and not self._input_export_running
        )
        self._submit_aims_action.setEnabled(
            scalar_field is None and not self._submission_running
        )
        self._update_continuation_control()
        if scalar_field is None:
            self._set_operation(
                f"Loaded {source_path.name}: {len(structure)} atoms, "
                f"{len(connectivity)} bonds ({connectivity_source.value}), "
                f"{len(anchors)} detected anchors."
            )
        else:
            nx, ny, nz = scalar_field.dimensions
            dataset = (
                f", dataset {scalar_field.dataset_id}"
                if scalar_field.dataset_id is not None
                else ""
            )
            self._set_operation(
                f"Loaded read-only Cube {source_path.name}: {len(structure)} "
                f"atoms, {nx}×{ny}×{nz} scalar grid{dataset}, "
                f"coordinates interpreted as "
                f"{scalar_field.source_coordinate_unit.value}, isovalue "
                f"{orbital_preferences.isovalue:.6f}."
            )
        self._store_bound_geometry_workspace(capture_builder_visibility=False)
        self._route_active_workspace()
        return True

    def _select_cube_coordinate_unit(
        self,
        source_path: Path,
    ) -> CubeCoordinateUnit | None:
        recommendation = recommend_cube_coordinate_unit(source_path)
        if not recommendation.requires_confirmation:
            return recommendation.coordinate_unit

        if recommendation.source_kind is CubeSourceKind.FHI_AIMS:
            prompt = (
                "This file identifies FHI-aims. FHI-aims Cube variants may "
                "store header coordinates in different units. Choose the "
                "coordinate unit used by this file:"
            )
            choices = (
                "Angstrom (FHI-aims default)",
                "Bohr (atomic units)",
            )
            units = (
                CubeCoordinateUnit.ANGSTROM,
                CubeCoordinateUnit.BOHR,
            )
        else:
            prompt = (
                "This Cube file does not identify a recognized producer. "
                "Choose the coordinate unit used by its header:"
            )
            choices = (
                "Bohr (standard Cube)",
                "Angstrom",
            )
            units = (
                CubeCoordinateUnit.BOHR,
                CubeCoordinateUnit.ANGSTROM,
            )
        selected, accepted = QInputDialog.getItem(
            self,
            "Cube coordinate units",
            prompt,
            choices,
            0,
            False,
        )
        if not accepted:
            return None
        return units[choices.index(selected)]

    def _rebuild_anchor_site_controls(self) -> None:
        _clear_layout(self._anchor_sites_layout)
        self._site_controls = {}
        if self._structure is None:
            self._anchor_sites_layout.addWidget(QLabel("Load a geometry file first."))
            self._anchor_sites_layout.addStretch(1)
            return
        if not self._anchors:
            self._anchor_sites_layout.addWidget(
                QLabel("No supported anchors detected.")
            )
            self._anchor_sites_layout.addStretch(1)
            return

        for anchor in self._anchors:
            site_frame = QFrame()
            site_frame.setObjectName("anchorSiteCard")
            site_frame.setFrameShape(QFrame.Shape.StyledPanel)
            site_layout = QVBoxLayout(site_frame)
            site_layout.setContentsMargins(5, 4, 5, 4)
            site_layout.setSpacing(3)

            checkbox = QCheckBox(_anchor_site_label(self._structure, anchor))
            checkbox.setObjectName(
                f"anchorSite_{anchor.binding_atom_index}"
            )
            site_layout.addWidget(checkbox)

            if anchor.attached_au_indices:
                checkbox.setEnabled(False)
                parameter_widget = None
                distance_input = None
                angle_input = None
            else:
                parameter_widget, distance_input, angle_input = (
                    self._build_site_parameter_widget(anchor)
                )
                parameter_widget.setVisible(False)
                site_layout.addWidget(parameter_widget)

            controls = _AnchorSiteControls(
                anchor=anchor,
                checkbox=checkbox,
                parameter_widget=parameter_widget,
                distance_input=distance_input,
                angle_input=angle_input,
            )
            self._site_controls[anchor] = controls
            if not anchor.attached_au_indices:
                checkbox.toggled.connect(
                    lambda checked, site_anchor=anchor, workspace_id=(
                        self._bound_geometry_workspace.runtime_id
                    ): (
                        self._dispatch_geometry_event(
                            workspace_id,
                            self._anchor_selection_changed,
                            site_anchor,
                            checked,
                        )
                    )
                )
            self._anchor_sites_layout.addWidget(site_frame)
        self._anchor_sites_layout.addStretch(1)

    def _reset_electrode_session_state(self) -> None:
        self._invalidate_lattice_extension_cache()
        workspace = self._bound_geometry_workspace
        if workspace is not None:
            workspace.lattice_extension_submitted_immutable = False
        self._electrode_sites = ()
        self._electrode_site_controls = {}
        self._electrode_current_proposal = None
        self._applied_electrode_result = None
        if hasattr(self, "_electrode_layers_input"):
            self._electrode_layers_input.setEnabled(True)
        if hasattr(self, "_electrode_extension_button"):
            blocker = QSignalBlocker(self._electrode_extension_button)
            self._electrode_extension_button.setChecked(False)
            self._electrode_extension_button.setEnabled(False)
            del blocker

    def _is_recovered_successful_step2_source(self) -> bool:
        snapshot = self._recovery_snapshot
        return (
            snapshot is not None
            and snapshot.active_step_kind is ProjectStepKind.MOLECULE_AU_OPT
            and snapshot.active_step.state is ProjectStepState.SUCCEEDED
            and snapshot.optimized_structure is not None
            and self._source_structure == snapshot.optimized_structure
        )

    def _imported_source_contact_context(self) -> ImportedContactAuContext:
        if (
            self._recovery_snapshot is not None
            or self._source_path is None
            or self._source_structure is None
            or self._source_connectivity is None
        ):
            raise ImportedContactAuEligibilityError(
                "the active workspace is not a local imported geometry"
            )
        source_anchors = detect_anchors(
            self._source_structure,
            self._source_connectivity,
        )
        return imported_contact_au_context(
            self._source_structure,
            self._source_connectivity,
            source_anchors,
        )

    def _current_imported_contact_context(self) -> ImportedContactAuContext:
        # The source proof prevents contact Au added later in the application
        # from being mislabeled as imported provenance. The current proof keeps
        # eligibility synchronized with session-local geometry edits.
        self._imported_source_contact_context()
        if self._structure is None or self._connectivity is None:
            raise ImportedContactAuEligibilityError(
                "the active imported geometry is unavailable"
            )
        return imported_contact_au_context(
            self._structure,
            self._connectivity,
            self._anchors,
        )

    def _current_step2_contact_context(self) -> ImportedContactAuContext:
        """Validate either imported or viewer-applied two-contact Step-2 input."""

        if (
            self._recovery_snapshot is not None
            or self._source_path is None
            or self._source_structure is None
            or self._source_connectivity is None
        ):
            raise ImportedContactAuEligibilityError(
                "the active workspace is not a local imported geometry"
            )
        if self._structure is None or self._connectivity is None:
            raise ImportedContactAuEligibilityError(
                "the active local geometry is unavailable"
            )
        if self._applied_result is not None:
            if not self._confirmed:
                raise ImportedContactAuEligibilityError(
                    "confirm the two viewer-added contact Au atoms before Step 2"
                )
            return applied_contact_au_context(
                self._structure,
                self._connectivity,
                self._anchors,
                self._applied_result,
            )
        return self._current_imported_contact_context()

    def _current_imported_transport_context(
        self,
    ) -> ImportedTransportStartContext:
        self._imported_source_contact_context()
        applied = self._applied_electrode_result
        if applied is None:
            raise ImportedContactAuEligibilityError(
                "apply both Au-pyramid electrodes before direct Step 3"
            )
        proposal = applied.proposal
        proposal_anchors = detect_anchors(
            proposal.source_structure,
            proposal.source_connectivity,
        )
        contact_context = imported_contact_au_context(
            proposal.source_structure,
            proposal.source_connectivity,
            proposal_anchors,
        )
        return ImportedTransportStartContext(
            contact_context,
            applied,
            self._structure,
        )

    def _electrode_source_description(self) -> str:
        return (
            "recovered optimized Step-2 source"
            if self._is_recovered_successful_step2_source()
            else "imported contact-Au source"
        )

    def _rebuild_electrode_controls(self) -> None:
        _clear_layout(self._electrode_sites_layout)
        self._electrode_site_controls = {}
        self._electrode_current_proposal = None
        self._electrode_done_button.setEnabled(False)
        extension_blocker = QSignalBlocker(self._electrode_extension_button)
        self._electrode_extension_button.setEnabled(False)
        self._electrode_extension_button.setChecked(
            self._pick_mode is _ViewerPickMode.AU_LATTICE_EXTENSION
        )
        del extension_blocker
        imported_context: ImportedContactAuContext | None = None
        recovered_source = self._is_recovered_successful_step2_source()
        if self._applied_electrode_result is not None:
            if not recovered_source:
                try:
                    self._current_imported_transport_context()
                except (ImportedContactAuEligibilityError, TypeError, ValueError):
                    self._electrode_sites = ()
                    self._electrode_message_label.setText("")
                    self._electrode_section.setVisible(False)
                    return
            self._electrode_section.setVisible(True)
            self._electrode_layers_input.setEnabled(False)
            workspace = self._bound_geometry_workspace
            self._electrode_extension_button.setEnabled(
                workspace is not None
                and self._lattice_extension_editable(workspace)
            )
            self._electrode_message_label.setText(
                f"Both {self._electrode_layers_input.value()}-layer Au pyramids "
                "are applied. The "
                f"{self._electrode_source_description()} remains unchanged."
            )
            return
        if not recovered_source:
            try:
                imported_context = self._current_imported_contact_context()
            except (ImportedContactAuEligibilityError, TypeError, ValueError):
                self._electrode_sites = ()
                self._electrode_message_label.setText("")
                self._electrode_section.setVisible(False)
                return

        self._electrode_section.setVisible(True)
        if self._structure is None or self._connectivity is None:
            self._electrode_sites = ()
            self._electrode_message_label.setText(
                "No recovered Step-2 molecular structure is available."
            )
            return

        try:
            sites = (
                imported_context.contact_sites
                if imported_context is not None
                else eligible_electrode_contact_sites(
                    self._structure,
                    self._connectivity,
                    self._anchors,
                )
            )
        except (ElectrodeBuilderError, TypeError, ValueError) as error:
            self._electrode_sites = ()
            self._electrode_message_label.setText(
                f"Electrode contact sites are invalid: {error}"
            )
            return
        self._electrode_sites = sites
        if len(sites) != 2:
            self._electrode_message_label.setText(
                "Final electrode application requires exactly two "
                "recognized occupied anchor/contact sites; "
                f"found {len(sites)}."
            )
            return

        self._electrode_message_label.setText(
            "Select either site to preview; select both to enable Done. "
            "Both sides use the selected canonical pyramid size."
        )
        for site_number, site in enumerate(sites, start=1):
            side = "Left" if site_number == 1 else "Right"
            checkbox = QCheckBox(
                f"Site {site_number} | {site.anchor.kind.value} "
                f"{_atom_reference(self._structure, site.anchor.binding_atom_index)} "
                f"-> {_atom_reference(self._structure, site.contact_au_index)} "
                f"| {side} electrode"
            )
            checkbox.setObjectName(f"sixLayerElectrodeSite_{site_number - 1}")
            checkbox.toggled.connect(
                lambda checked, workspace_id=(
                    self._bound_geometry_workspace.runtime_id
                ): self._dispatch_geometry_event(
                    workspace_id,
                    self._electrode_selection_changed,
                    checked,
                )
            )
            self._electrode_site_controls[site] = checkbox
            self._electrode_sites_layout.addWidget(checkbox)

    def _selected_electrode_sites(self) -> tuple[ElectrodeContactSite, ...]:
        return tuple(
            site
            for site, checkbox in self._electrode_site_controls.items()
            if checkbox.isChecked() and checkbox.isEnabled()
        )

    def _electrode_selection_changed(self, _checked: bool) -> None:
        self._enter_au_placement_mode()
        if self._applied_electrode_result is not None:
            return
        self._recompute_electrode_preview()

    def _electrode_layers_changed(self, value: int) -> None:
        """Invalidate stale preview metadata and regenerate for the selected size."""

        if self._applied_electrode_result is not None:
            return
        self._electrode_current_proposal = None
        self._electrode_done_button.setEnabled(False)
        self._viewer.set_preview_atoms(())
        self._viewer.set_annotations((), ())
        if value >= 7:
            self._electrode_cost_warning.setText(
                "High-cost electrode: 7–10 layers can substantially increase "
                "calculation cost and are usually not recommended above 6 layers."
            )
            self._electrode_cost_warning.setVisible(True)
        else:
            self._electrode_cost_warning.clear()
            self._electrode_cost_warning.setVisible(False)
        if self._selected_electrode_sites():
            self._recompute_electrode_preview()
        else:
            self._electrode_message_label.setText(
                f"{value}-layer canonical Au pyramid selected. Select either site "
                "to preview; select both to enable Done."
            )

    def _recompute_electrode_preview(self) -> bool:
        self._electrode_done_button.setEnabled(False)
        if self._structure is None or self._connectivity is None:
            self._electrode_current_proposal = None
            self._electrode_message_label.setText(
                "No eligible contact-Au source structure is available."
            )
            return False
        selected = self._selected_electrode_sites()
        if not selected:
            self._viewer.set_preview_atoms(())
            self._viewer.set_annotations((), ())
            self._electrode_current_proposal = None
            self._electrode_message_label.setText(
                "Select either site to preview; select both to enable Done. "
                "Both sides use the selected canonical pyramid size."
            )
            self._set_operation("No Au electrode sites are selected.")
            return True

        try:
            proposal = propose_electrode_placement(
                self._structure,
                self._connectivity,
                self._electrode_sites,
                selected,
                pyramid_layers=self._electrode_layers_input.value(),
            )
            if self._covalent_radii is None:
                raise ValueError("covalent radii are not loaded")
            gold_radius = self._covalent_radii["Au"] * ATOM_RADIUS_SCALE
            preview_atoms = tuple(
                PreviewAtom(
                    coordinates,
                    gold_radius,
                    ELEMENT_COLORS_RGB["Au"],
                    "Au",
                )
                for cluster in proposal.clusters
                for coordinates in cluster.transformed_coordinates[1:]
            )
            self._viewer.set_preview_atoms(preview_atoms)
            self._viewer.set_annotations((), ())
        except (
            ElectrodeBuilderError,
            KeyError,
            TypeError,
            ValueError,
            VisualizationError,
        ) as error:
            self._viewer.set_preview_atoms(())
            self._electrode_current_proposal = None
            message = f"Au-pyramid electrode preview not updated: {error}"
            self._electrode_message_label.setText(message)
            self._set_operation(message)
            return False

        self._electrode_current_proposal = proposal
        both_selected = len(selected) == 2 and len(proposal.clusters) == 2
        self._electrode_done_button.setEnabled(both_selected)
        rolls = ", ".join(
            f"{cluster.side.casefold()} roll {cluster.roll_degrees}°"
            for cluster in proposal.clusters
        )
        new_count = sum(len(cluster.new_atom_indices) for cluster in proposal.clusters)
        if proposal.minimum_intercluster_distance is None:
            detail = f"{rolls}; {new_count} virtual Au; existing apex reused."
        else:
            detail = (
                f"{rolls}; {new_count} virtual Au; minimum opposite-cluster Au-Au "
                f"distance {proposal.minimum_intercluster_distance:.3f} Å "
                "(fixed apex pair excluded)."
            )
        self._electrode_message_label.setText(detail)
        self._set_operation(
            f"{proposal.pyramid_layers}-layer Au-pyramid preview: {detail}"
        )
        return True

    def _build_site_parameter_widget(
        self,
        anchor: AnchorCandidate,
    ) -> tuple[QWidget, QLineEdit, QLineEdit | None]:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(18, 0, 0, 2)
        form.setSpacing(3)

        distance_input = QLineEdit()
        distance_input.setObjectName(
            f"distance_{anchor.binding_atom_index}"
        )
        if self._structure is None or self._connectivity is None:
            raise ValueError("anchor controls require loaded connectivity")
        defaults = placement_defaults_for_anchor(
            self._structure,
            self._connectivity,
            anchor,
            self._placement_defaults,
            self._dicyano_cyano_n_defaults,
        )
        distance_input.setText(f"{defaults.distance_angstrom:.2f}")
        form.addRow(
            f"{DISTANCE_PARAMETER_LABELS[anchor.kind]} (Å)",
            distance_input,
        )
        runtime_id = self._bound_geometry_workspace.runtime_id
        distance_input.editingFinished.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._parameter_edit_finished,
            )
        )

        angle_input = QLineEdit()
        angle_input.setObjectName(f"angle_{anchor.binding_atom_index}")
        angle_input.setText(f"{defaults.angle_degrees:.1f}")
        form.addRow(
            f"{ANGLE_PARAMETER_LABELS[anchor.kind]} (°)",
            angle_input,
        )
        angle_input.editingFinished.connect(
            lambda workspace_id=runtime_id: self._dispatch_geometry_event(
                workspace_id,
                self._parameter_edit_finished,
            )
        )
        return widget, distance_input, angle_input

    def _anchor_selection_changed(
        self,
        anchor: AnchorCandidate,
        checked: bool,
    ) -> None:
        self._enter_au_placement_mode()
        controls = self._site_controls[anchor]
        if checked and len(self._selected_controls()) > 2:
            controls.checkbox.blockSignals(True)
            controls.checkbox.setChecked(False)
            controls.checkbox.blockSignals(False)
            self._set_operation(
                "At most two unoccupied anchor sites can be selected."
            )
            return
        if controls.parameter_widget is not None:
            controls.parameter_widget.setVisible(checked)
        self._confirmed = False
        self._recompute_previews()

    def _parameter_edit_finished(self) -> None:
        self._enter_au_placement_mode()
        self._confirmed = False
        self._recompute_previews()

    def _selected_controls(self) -> tuple[_AnchorSiteControls, ...]:
        return tuple(
            controls
            for controls in self._site_controls.values()
            if controls.checkbox.isChecked() and controls.checkbox.isEnabled()
        )

    def _recompute_previews(self) -> bool:
        if (
            self._structure is None
            or self._connectivity is None
            or self._covalent_radii is None
            or self._vdw_radii is None
        ):
            self._set_operation("Open a valid geometry file before placing Au.")
            return False

        selected = self._selected_controls()
        if not selected:
            self._viewer.set_preview_atoms(())
            self._viewer.set_annotations((), ())
            self._current_proposals = ()
            self._set_operation("No Au-placement sites are selected.")
            return True

        try:
            parameters = tuple(
                self._read_site_parameters(controls)
                for controls in selected
            )
            proposals = propose_au_placements(
                self._structure,
                self._connectivity,
                parameters,
                vdw_radii=self._vdw_radii,
            )
            gold_radius = self._covalent_radii["Au"] * ATOM_RADIUS_SCALE
            preview_atoms = tuple(
                PreviewAtom(
                    (proposal.x, proposal.y, proposal.z),
                    gold_radius,
                    ELEMENT_COLORS_RGB["Au"],
                    "Au",
                )
                for proposal in proposals
            )
            distances = tuple(
                DistanceAnnotation(
                    _atom_coordinates(
                        self._structure,
                        proposal.anchor.binding_atom_index,
                    ),
                    (proposal.x, proposal.y, proposal.z),
                )
                for proposal in proposals
            )
            angles = tuple(
                annotation
                for proposal, parameter in zip(
                    proposals,
                    parameters,
                    strict=True,
                )
                for annotation in self._angle_annotations(
                    proposal,
                    parameter,
                )
            )
            self._viewer.set_preview_atoms(
                preview_atoms,
                hidden_atom_indices=(
                    atom_index
                    for proposal in proposals
                    for atom_index in proposal.remove_atom_indices
                ),
            )
            self._viewer.set_annotations(distances, angles)
        except (
            KeyError,
            TypeError,
            ValueError,
            VisualizationError,
        ) as error:
            self._set_operation(f"Au preview not updated: {error}")
            return False

        self._current_proposals = proposals
        self._set_operation(
            _format_proposals(
                self._structure,
                self._connectivity,
                proposals,
            )
        )
        return True

    def _read_site_parameters(
        self,
        controls: _AnchorSiteControls,
    ) -> AuPlacementParameters:
        if controls.distance_input is None:
            raise ValueError("occupied anchor sites cannot receive virtual Au")
        distance = _required_float(
            controls.distance_input,
            DISTANCE_PARAMETER_LABELS[controls.anchor.kind],
        )
        angle = None
        if controls.angle_input is not None:
            angle = _required_float(
                controls.angle_input,
                ANGLE_PARAMETER_LABELS[controls.anchor.kind],
            )
        return AuPlacementParameters(controls.anchor, distance, angle)

    def _angle_annotations(
        self,
        proposal: AuPlacementProposal,
        parameters: AuPlacementParameters,
    ) -> tuple[AngleAnnotation, ...]:
        if parameters.angle_degrees is None:
            return ()
        if self._structure is None or self._connectivity is None:
            raise ValueError("molecular geometry is not loaded")
        reference_indices = angle_reference_atom_indices(
            self._structure,
            self._connectivity,
            proposal.anchor,
        )
        return tuple(
            AngleAnnotation(
                _atom_coordinates(
                    self._structure,
                    proposal.anchor.binding_atom_index,
                ),
                _atom_coordinates(self._structure, reference_index),
                (proposal.x, proposal.y, proposal.z),
            )
            for reference_index in reference_indices
        )

    def _confirm_current_proposals(self) -> None:
        selected = self._selected_controls()
        if not selected:
            self._set_operation("Select one or two unoccupied anchor sites first.")
            return
        if self._structure is None or self._connectivity is None:
            self._set_operation("Open a valid geometry file before applying Au.")
            return
        selected_anchors = {controls.anchor for controls in selected}
        proposal_anchors = {
            proposal.anchor for proposal in self._current_proposals
        }
        if (
            len(self._current_proposals) != len(selected)
            or proposal_anchors != selected_anchors
        ):
            self._set_operation("No complete Au proposal is available to confirm.")
            return

        source_before_application = self._structure
        try:
            source_display_orders = self._working_bond_display_orders()
            result = apply_au_placements(
                self._structure,
                self._connectivity,
                self._current_proposals,
            )
            result_display_orders = remap_bond_display_orders(
                self._connectivity,
                source_display_orders,
                result.connectivity,
                result.old_to_new_indices,
            )
            anchors = detect_anchors(result.structure, result.connectivity)
            if self._covalent_radii is None:
                raise ValueError("covalent radii are not loaded")
            self._prepare_for_structure_replacement()
            self._viewer.set_molecule(
                result.structure,
                result.connectivity,
                self._covalent_radii,
                result_display_orders,
                scalar_field=self._scalar_field,
                orbital_preferences=self._orbital_preferences,
            )
            self._viewer.set_preview_atoms(())
            self._viewer.set_annotations((), ())
        except (
            AuPlacementApplicationError,
            KeyError,
            TypeError,
            ValueError,
            VisualizationError,
        ) as error:
            self._set_operation(f"Au application failed: {error}")
            return

        self._structure = result.structure
        self._connectivity = result.connectivity
        self._bond_display_orders = result_display_orders
        self._anchors = anchors
        self._current_proposals = ()
        self._applied_result = result
        self._confirmed = True
        workspace = self._bound_geometry_workspace
        assert workspace is not None
        self._sync_anchor_highlights(workspace)
        self._save_geometry_button.setEnabled(True)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_anchor_site_controls()
        self._rebuild_electrode_controls()
        self._update_continuation_control()
        self._set_operation(
            _format_applied_result(source_before_application, result)
        )
        self._store_bound_geometry_workspace(
            capture_builder_visibility=False,
        )

    def _confirm_electrode_proposal(self) -> None:
        if self._applied_electrode_result is not None:
            self._set_operation(
                "Au-pyramid electrodes are already applied; reopen the "
                "contact-Au source before rebuilding."
            )
            return
        selected = self._selected_electrode_sites()
        proposal = self._electrode_current_proposal
        if len(selected) != 2:
            self._set_operation(
                "Select both eligible electrode sites before Done."
            )
            return
        if (
            proposal is None
            or len(proposal.clusters) != 2
            or {cluster.site for cluster in proposal.clusters} != set(selected)
        ):
            self._set_operation(
                "No complete two-electrode preview is available to apply."
            )
            return
        if self._structure is None or self._connectivity is None:
            self._set_operation(
                "No eligible contact-Au source structure is available."
            )
            return

        source_before_application = self._structure
        try:
            source_display_orders = self._working_bond_display_orders()
            result = apply_electrode_placement(
                self._structure,
                self._connectivity,
                proposal,
            )
            result_display_orders = extend_bond_display_orders(
                self._connectivity,
                source_display_orders,
                result.connectivity,
            )
            anchors = detect_anchors(result.structure, result.connectivity)
            if self._covalent_radii is None:
                raise ValueError("covalent radii are not loaded")
            self._prepare_for_structure_replacement()
            self._viewer.set_molecule(
                result.structure,
                result.connectivity,
                self._covalent_radii,
                result_display_orders,
                scalar_field=self._scalar_field,
                orbital_preferences=self._orbital_preferences,
            )
            self._viewer.set_preview_atoms(())
            self._viewer.set_annotations((), ())
        except (
            ElectrodeBuilderError,
            KeyError,
            TypeError,
            ValueError,
            VisualizationError,
        ) as error:
            self._set_operation(f"Au-pyramid electrode application failed: {error}")
            return

        self._structure = result.structure
        self._connectivity = result.connectivity
        self._bond_display_orders = result_display_orders
        self._anchors = anchors
        self._electrode_current_proposal = None
        self._applied_electrode_result = result
        self._confirmed = True
        workspace = self._bound_geometry_workspace
        assert workspace is not None
        self._sync_anchor_highlights(workspace)
        self._save_geometry_button.setEnabled(True)
        self._picked_atom_label.setText("Selected atom: none")
        self._rebuild_anchor_site_controls()
        for checkbox in self._electrode_site_controls.values():
            checkbox.setEnabled(False)
        self._electrode_layers_input.setEnabled(False)
        self._electrode_done_button.setEnabled(False)
        self._electrode_extension_button.setEnabled(
            self._lattice_extension_editable(workspace)
        )
        self._electrode_message_label.setText(
            f"Both {result.proposal.pyramid_layers}-layer Au pyramids are applied. The "
            f"{self._electrode_source_description()} remains unchanged."
        )
        self._update_continuation_control()
        self._set_operation(
            f"Applied two {result.proposal.pyramid_layers}-layer Au pyramids from "
            "the exact preview: "
            f"preserved {len(source_before_application)} source atoms, appended "
            f"{len(result.added_au_indices)} Au, working atoms "
            f"{len(result.structure)}."
        )

    def _save_geometry_in(self) -> None:
        if (
            self._applied_result is None
            and self._applied_electrode_result is None
        ) or self._structure is None:
            self._set_operation(
                "Apply contact Au or both electrode clusters before saving geometry.in."
            )
            return
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save geometry.in",
            "geometry.in",
            "FHI-aims geometry.in (geometry.in);;All files (*.*)",
        )
        if not selected_path:
            return
        try:
            self._save_geometry_to_path(Path(selected_path))
        except (OSError, TypeError, ValueError) as error:
            self._set_operation(f"Unable to save geometry.in: {error}")
            return
        self._set_operation(f"Saved working geometry.in: {selected_path}")

    def _save_geometry_to_path(self, output_path: Path) -> None:
        """Write the current applied working structure beneath the dialog."""

        if (
            self._applied_result is None
            and self._applied_electrode_result is None
        ) or self._structure is None:
            raise ValueError("no applied working structure is available to save")
        write_geometry_in(self._structure, output_path)

    def _generate_aims_optimization(self) -> None:
        active = self._active_geometry_workspace()
        if (
            active is not self._bound_geometry_workspace
            or active is None
            or active.read_only
        ):
            return
        if self._structure is None:
            QMessageBox.critical(
                self,
                "Cannot generate FHI-aims inputs",
                "Load a molecular structure before generating optimization inputs.",
            )
            self._set_operation(
                "No molecular structure is loaded for FHI-aims input generation."
            )
            return

        try:
            dependencies = _create_project_submission_dependencies()
            export_service = dependencies.input_export_service
            if export_service is None:
                raise RuntimeError("FHI-aims input export service is unavailable")
            collection = dependencies.profile_repository.load()
            if not collection.profiles:
                raise ValueError(
                    "Create and save a Server Connection before generating a "
                    "new control.in file."
                )
            labels = tuple(profile.name for profile in collection.profiles)
            selected_index = next(
                (
                    index
                    for index, profile in enumerate(collection.profiles)
                    if profile.profile_id == collection.last_selected_profile_id
                ),
                0,
            )
            selected_label, accepted = QInputDialog.getItem(
                self,
                "Select FHI-aims server",
                "Saved server supplying species definitions:",
                labels,
                selected_index,
                False,
            )
            if not accepted:
                self._set_operation("FHI-aims optimization generation was cancelled.")
                return
            profile = collection.profiles[labels.index(selected_label)]
            if (
                profile.execution_preset is None
                or profile.execution_preset.fhi_species_defaults_path is None
            ):
                raise ValueError(
                    f"Configure the FHI-aims species definitions root for "
                    f"{profile.name} in Server Connections > Cluster Settings "
                    "before generating a new control.in file."
                )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Cannot generate FHI-aims inputs",
                str(error),
            )
            self._set_operation(f"FHI-aims input generation is unavailable: {error}")
            return

        dialog = AimsOptimizationSettingsDialog(self._structure, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("FHI-aims optimization generation was cancelled.")
            return
        settings = dialog.selected_settings()
        selected_directory = QFileDialog.getExistingDirectory(
            self,
            "Select FHI-aims optimization directory",
            "",
        )
        if not selected_directory:
            self._set_operation("FHI-aims optimization generation was cancelled.")
            return

        destination = Path(selected_directory)
        existing = tuple(
            path
            for path in (
                destination / "geometry.in",
                destination / "control.in",
            )
            if path.exists()
        )
        overwrite = False
        if existing:
            answer = QMessageBox.question(
                self,
                "Overwrite existing FHI-aims inputs?",
                "The selected directory already contains "
                + ", ".join(path.name for path in existing)
                + ". Overwrite these files?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_operation(
                    "FHI-aims optimization generation was cancelled; "
                    "existing inputs were preserved."
                )
                return
            overwrite = True

        try:
            confirmation = QMessageBox.question(
                self,
                "Read species definitions from server?",
                f"Moltage will connect to {profile.name} and read the required "
                "FHI-aims species definitions. No remote files or directories "
                "will be created. Continue?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                self._set_operation("FHI-aims optimization generation was cancelled.")
                return
            password = self._temporary_transport_password(dependencies, profile)
            if password is False:
                self._set_operation("FHI-aims optimization generation was cancelled.")
                return
            request = AimsInputExportRequest(
                profile=profile,
                input_plan=AimsOptimizationInputPlan(self._structure, settings),
                destination=destination,
                overwrite=overwrite,
                supplied_password=(
                    password if isinstance(password, str) else None
                ),
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Unable to generate FHI-aims inputs",
                str(error),
            )
            self._set_operation(f"FHI-aims input generation failed: {error}")
            return

        worker = AimsInputExportWorker(export_service, request)
        worker.signals.progress.connect(
            lambda message, active_worker=worker: (
                self._aims_input_export_progress(active_worker, message)
            )
        )
        worker.signals.succeeded.connect(
            lambda result, active_worker=worker: (
                self._aims_input_export_succeeded(active_worker, result)
            )
        )
        worker.signals.failed.connect(
            lambda error, active_worker=worker: (
                self._aims_input_export_failed(active_worker, error)
            )
        )
        worker.signals.finished.connect(
            lambda active_worker=worker: (
                self._aims_input_export_finished(active_worker)
            )
        )
        self._input_export_workers.add(worker)
        self._input_export_worker_origins[worker] = active.runtime_id
        self._input_export_running = True
        self._generate_aims_action.setEnabled(False)
        self._set_operation(
            f"Preparing FHI-aims inputs with definitions from {profile.name}..."
        )
        self._submission_thread_pool.start(worker)

    def _aims_input_export_progress(self, worker, message: str) -> None:
        origin = self._input_export_worker_origins.get(worker)
        with self._geometry_callback_context(origin):
            self._set_operation(message)

    def _aims_input_export_succeeded(self, worker, result: object) -> None:
        if worker not in self._input_export_workers:
            return
        origin = self._input_export_worker_origins.get(worker)
        with self._geometry_callback_context(origin):
            geometry_path, control_path = result
            message = (
                f"Generated FHI-aims optimization inputs: {geometry_path} and "
                f"{control_path}"
            )
            self._set_operation(message)
            QMessageBox.information(self, "FHI-aims inputs generated", message)

    def _aims_input_export_failed(self, worker, error: object) -> None:
        if worker not in self._input_export_workers:
            return
        origin = self._input_export_worker_origins.get(worker)
        with self._geometry_callback_context(origin):
            QMessageBox.critical(
                self,
                "Unable to generate FHI-aims inputs",
                str(error),
            )
            self._set_operation(f"FHI-aims input generation failed: {error}")

    def _aims_input_export_finished(self, worker) -> None:
        self._input_export_workers.discard(worker)
        self._input_export_worker_origins.pop(worker, None)
        self._input_export_running = bool(self._input_export_workers)
        active = self._active_geometry_workspace()
        self._generate_aims_action.setEnabled(
            bool(
                active is not None
                and active is self._bound_geometry_workspace
                and active.structure is not None
                and not active.read_only
                and not self._input_export_running
            )
        )

    def _update_continuation_control(self) -> None:
        active = self._active_geometry_workspace()
        if (
            active is None
            or active is not self._bound_geometry_workspace
            or active.read_only
        ):
            self._continue_step2_action.setEnabled(False)
            self._continue_step3_action.setEnabled(False)
            self._continue_step4_action.setEnabled(False)
            return
        snapshot = self._recovery_snapshot
        recovered_step2_eligible = (
            snapshot is not None
            and snapshot.can_continue_step2
            and self._recovery_profile is not None
            and self._confirmed
            and self._applied_result is not None
            and not self._submission_running
            and not self._submission_step2_refresh_required
        )
        imported_step2_eligible = False
        if (
            snapshot is None
            and not self._submission_running
            and not self._submission_step2_refresh_required
            and self._applied_electrode_result is None
        ):
            try:
                self._current_step2_contact_context()
            except (ImportedContactAuEligibilityError, TypeError, ValueError):
                pass
            else:
                imported_step2_eligible = True
        self._continue_step2_action.setEnabled(
            recovered_step2_eligible or imported_step2_eligible
        )

        step3_eligible = False
        if (
            self._recovery_profile is not None
            and not self._submission_running
            and not self._submission_step3_refresh_required
        ):
            try:
                self._current_transport_convergence_context()
            except (TransportConvergenceEligibilityError, TypeError, ValueError):
                pass
            else:
                step3_eligible = True
        elif (
            snapshot is None
            and not self._submission_running
            and not self._submission_step3_refresh_required
        ):
            try:
                self._current_imported_transport_context()
            except (ImportedContactAuEligibilityError, TypeError, ValueError):
                pass
            else:
                step3_eligible = True
        self._continue_step3_action.setEnabled(step3_eligible)
        step4_eligible = (
            snapshot is not None
            and snapshot.can_continue_step4
            and self._recovery_profile is not None
            and self._structure is snapshot.optimized_structure
            and not self._submission_running
            and not self._transport_operation_running
        )
        self._continue_step4_action.setEnabled(step4_eligible)

    def _current_transport_convergence_context(
        self,
    ) -> TransportConvergenceContext:
        return TransportConvergenceContext(
            self._recovery_snapshot,
            self._source_structure,
            self._structure,
            self._applied_electrode_result,
        )

    def _continue_project_step2(self) -> None:
        if self._active_geometry_workspace() is not self._bound_geometry_workspace:
            return
        if self._submission_running:
            self._set_operation("A Slurm submission is already in progress.")
            return
        if self._recovery_snapshot is None:
            self._submit_imported_step2()
            return
        snapshot = self._recovery_snapshot
        profile = self._recovery_profile
        if snapshot is None or profile is None or not snapshot.can_continue_step2:
            self._show_local_submission_error(
                "Cannot continue to Step 2",
                ValueError(
                    "open a successfully recovered Step-1 project before continuing"
                ),
            )
            return
        if (
            not self._confirmed
            or self._applied_result is None
            or self._structure is None
        ):
            self._show_local_submission_error(
                "Cannot continue to Step 2",
                ValueError(
                    "inspect the recovered structure, apply contact Au with the "
                    "Electrode Builder, and press Done first"
                ),
            )
            return
        try:
            recommendation = recommend_start_step(
                self._structure,
                self._anchors,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to validate Step-2 structure",
                error,
            )
            return
        if recommendation.advice is StartStepAdvice.STEP1:
            self._show_local_submission_error(
                "Cannot continue to Step 2",
                ValueError(recommendation.reason),
            )
            return
        if recommendation.advice is StartStepAdvice.AMBIGUOUS:
            answer = QMessageBox.question(
                self,
                "Confirm ambiguous Step-2 structure",
                recommendation.reason
                + "\n\nContinue this existing project explicitly as Step 2?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_operation("Step-2 continuation was cancelled.")
                return

        settings_dialog = AimsOptimizationSettingsDialog(
            self._structure,
            parent=self,
        )
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Step-2 continuation was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        try:
            input_plan = AimsOptimizationInputPlan(
                self._structure,
                settings,
            )
            dependencies = _create_project_submission_dependencies()
            saved_password_exists = (
                profile.save_password
                and dependencies.secret_store.get_password(profile.profile_id)
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare Step-2 continuation",
                error,
            )
            return

        confirmation = Step2ContinuationConfirmationDialog(
            snapshot,
            profile,
            settings,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("Step-2 continuation was cancelled.")
            return
        temporary_password = confirmation.take_temporary_password()
        try:
            request = ExistingProjectStepSubmissionRequest(
                profile=profile,
                project=snapshot.project,
                input_plan=input_plan,
                supplied_password=temporary_password,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Invalid Step-2 continuation",
                error,
            )
            return
        self._start_submission(dependencies, request)

    def _continue_project_step3(self) -> None:
        if self._active_geometry_workspace() is not self._bound_geometry_workspace:
            return
        if self._submission_running:
            self._set_operation("A Slurm submission is already in progress.")
            return
        if self._recovery_snapshot is None:
            self._submit_imported_step3()
            return
        profile = self._recovery_profile
        if profile is None:
            self._show_local_submission_error(
                "Cannot continue to Step 3",
                ValueError("open a managed recovered Step-2 project first"),
            )
            return
        try:
            context = self._current_transport_convergence_context()
        except (TransportConvergenceEligibilityError, TypeError, ValueError) as error:
            self._show_local_submission_error(
                "Cannot continue to Step 3",
                error,
            )
            return
        preset = profile.execution_preset
        if preset is None:
            self._show_local_submission_error(
                "Cannot continue to Step 3",
                ValueError(
                    f"Configure Cluster Execution Settings for {profile.name} "
                    "before submitting."
                ),
            )
            return

        settings_dialog = TransportConvergenceSettingsDialog(
            context.working_structure,
            parent=self,
        )
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Step-3 submission was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        try:
            input_plan = TransportConvergenceInputPlan(
                context.working_structure,
                settings,
            )
            dependencies = _create_project_submission_dependencies()
            saved_password_exists = (
                profile.save_password
                and dependencies.secret_store.get_password(profile.profile_id)
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare Step-3 submission",
                error,
            )
            return

        confirmation = Step3SubmissionConfirmationDialog(
            context,
            profile,
            settings,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("Step-3 submission was cancelled.")
            return
        temporary_password = confirmation.take_temporary_password()
        try:
            request = TransportConvergenceSubmissionRequest(
                profile,
                context,
                input_plan,
                temporary_password,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Invalid Step-3 submission",
                error,
            )
            return
        self._start_submission(dependencies, request)

    def _submit_imported_step3(self) -> None:
        try:
            context = self._current_imported_transport_context()
        except (ImportedContactAuEligibilityError, TypeError, ValueError) as error:
            self._show_local_submission_error("Cannot start direct Step 3", error)
            return
        try:
            dependencies = _create_project_submission_dependencies()
            profile_collection = dependencies.profile_repository.load()
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare secure submission",
                error,
            )
            return
        if not profile_collection.profiles:
            self._show_local_submission_error(
                "No saved server",
                ValueError("Create and save a Server Connection before submitting."),
            )
            return

        recommendation = recommend_start_step(
            context.contact_context.structure,
            context.contact_context.anchors,
        )
        default_base_name = self._source_path.stem if self._source_path is not None else ""
        project_dialog = NewCalculationProjectDialog(
            profile_collection.profiles,
            profile_collection.last_selected_profile_id,
            default_base_name,
            recommendation,
            preview_date=datetime.now().astimezone().date(),
            cluster_settings_callback=lambda selected: (
                self._configure_submission_cluster_settings(
                    dependencies,
                    selected,
                )
            ),
            fixed_starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
            parent=self,
        )
        if project_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Direct Step-3 project creation was cancelled.")
            return
        selection = project_dialog.selected_project()
        try:
            dependencies.profile_repository.set_last_selected(
                selection.profile.profile_id
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to save selected server",
                error,
            )
            return

        settings_dialog = TransportConvergenceSettingsDialog(
            context.working_structure,
            parent=self,
        )
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Direct Step-3 submission was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        try:
            input_plan = TransportConvergenceInputPlan(
                context.working_structure,
                settings,
            )
            saved_password_exists = (
                selection.profile.save_password
                and dependencies.secret_store.get_password(
                    selection.profile.profile_id
                )
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare direct Step-3 submission",
                error,
            )
            return

        confirmation = SubmissionConfirmationDialog(
            selection,
            settings,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("Direct Step-3 submission was cancelled.")
            return
        temporary_password = confirmation.take_temporary_password()
        source_name = (
            self._source_path.name
            if self._source_path is not None
            else selection.base_name
        )
        try:
            request = NewProjectSubmissionRequest(
                profile=selection.profile,
                base_name=selection.base_name,
                source_molecule_name=source_name,
                starting_step=ProjectStepKind.TRANSPORT_CONVERGENCE,
                input_plan=input_plan,
                supplied_password=temporary_password,
                imported_transport_context=context,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Invalid direct Step-3 submission",
                error,
            )
            return
        self._start_submission(dependencies, request)

    def _continue_project_step4(self) -> None:
        if self._active_geometry_workspace() is not self._bound_geometry_workspace:
            return
        if self._transport_operation_running or self._submission_running:
            self._set_operation("A remote submission operation is already in progress.")
            return
        snapshot = self._recovery_snapshot
        profile = self._recovery_profile
        if snapshot is None or profile is None or not snapshot.can_continue_step4:
            self._show_local_submission_error(
                "Cannot continue to Step 4",
                ValueError(
                    "open a successfully recovered Step-3 project with a valid "
                    "surface proposal first"
                ),
            )
            return
        try:
            dependencies = _create_project_submission_dependencies()
            service = dependencies.transport_submission_service
            if service is None:
                raise RuntimeError("transport submission service is unavailable")
            password = self._temporary_transport_password(dependencies, profile)
        except Exception as error:
            self._show_local_submission_error(
                "Unable to check AITRANSS",
                error,
            )
            return
        if password is False:
            self._set_operation("Step-4 executable preflight was cancelled.")
            return
        worker = AitranssPreflightWorker(
            service,
            profile,
            password if isinstance(password, str) else None,
        )
        worker.signals.succeeded.connect(self._routed_step4_preflight_succeeded)
        worker.signals.failed.connect(self._routed_step4_preflight_failed)
        worker.signals.finished.connect(self._routed_transport_worker_finished)
        self._transport_workers.add(worker)
        self._transport_operation_running = True
        self._pending_transport_project_id = snapshot.project.project_id
        self._transport_origin_workspace_id = (
            self._active_geometry_workspace().runtime_id
        )
        self._pending_transport_dependencies = dependencies
        self._pending_transport_profile = profile
        self._notify_project_manager_external_operation_changed()
        self._continue_step4_action.setEnabled(False)
        self._projects_action.setEnabled(False)
        self._set_operation(
            "Checking the accepted V3 module environment for "
            "the configured AITRANSS executable..."
        )
        self._submission_thread_pool.start(worker)

    def _submit_restart_step4(self, draft: ProjectTaskRestartDraft) -> None:
        """Preflight one unchanged-geometry edited Step-4 attempt."""

        if self._transport_operation_running or self._submission_running:
            return
        if (
            draft.tcontrol_settings is None
            or draft.step4_execution_settings is None
            or draft.transport_evidence is None
            or draft.surface_proposal is None
        ):
            self._show_local_submission_error(
                "Cannot restart Step 4",
                ValueError("The original Step-4 settings/evidence are unavailable."),
            )
            return
        try:
            dependencies = _create_project_submission_dependencies()
            service = dependencies.transport_submission_service
            if service is None:
                raise RuntimeError("transport submission service is unavailable")
            password = self._temporary_transport_password(
                dependencies,
                draft.profile,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to check AITRANSS",
                error,
            )
            return
        if password is False:
            self._set_operation("Step-4 restart preflight was cancelled.")
            return
        worker = AitranssPreflightWorker(
            service,
            draft.profile,
            password if isinstance(password, str) else None,
        )
        worker.signals.succeeded.connect(self._routed_step4_preflight_succeeded)
        worker.signals.failed.connect(self._routed_step4_preflight_failed)
        worker.signals.finished.connect(self._routed_transport_worker_finished)
        self._transport_workers.add(worker)
        self._transport_operation_running = True
        self._pending_transport_project_id = draft.source_project.project_id
        active = self._active_geometry_workspace()
        assert active is not None
        self._transport_origin_workspace_id = active.runtime_id
        self._pending_transport_dependencies = dependencies
        self._pending_transport_profile = draft.profile
        self._notify_project_manager_external_operation_changed()
        self._submit_aims_action.setEnabled(False)
        self._projects_action.setEnabled(False)
        self._set_operation(
            "Checking the accepted V3 module environment for the Step-4 restart..."
        )
        self._submission_thread_pool.start(worker)

    def _temporary_transport_password(
        self,
        dependencies: _ProjectSubmissionDependencies,
        profile: ServerProfile,
    ) -> str | None | bool:
        if (
            profile.save_password
            and dependencies.secret_store.get_password(profile.profile_id)
        ):
            return None
        password, accepted = QInputDialog.getText(
            self,
            "Password required",
            f"Password for {profile.username}@{profile.host}:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return False
        if not password:
            raise ValueError("enter a password for the remote operation")
        return password

    @Slot(str)
    def _routed_transport_progress(self, message: str) -> None:
        with self._geometry_callback_context(
            self._transport_origin_workspace_id
        ):
            self._set_operation(message)

    @Slot(object)
    def _routed_step4_preflight_succeeded(self, result: object) -> None:
        with self._geometry_callback_context(
            self._transport_origin_workspace_id
        ):
            self._step4_preflight_succeeded(result)

    @Slot(object)
    def _routed_step4_preflight_failed(self, error: object) -> None:
        with self._geometry_callback_context(
            self._transport_origin_workspace_id
        ):
            self._step4_preflight_failed(error)

    @Slot(object)
    def _routed_step4_submission_succeeded(self, result: object) -> None:
        with self._geometry_callback_context(
            self._transport_origin_workspace_id
        ):
            self._step4_submission_succeeded(result)

    @Slot(object)
    def _routed_step4_submission_failed(self, error: object) -> None:
        with self._geometry_callback_context(
            self._transport_origin_workspace_id
        ):
            self._step4_submission_failed(error)

    @Slot(object)
    def _routed_transport_worker_finished(self, worker: object) -> None:
        origin = self._transport_origin_workspace_id
        with self._geometry_callback_context(origin):
            self._transport_worker_finished(worker)
        if not self._transport_workers:
            self._transport_origin_workspace_id = None
            self._release_retired_workspace(origin)

    @Slot(object)
    def _step4_preflight_succeeded(self, result: object) -> None:
        if not isinstance(result, AitranssDiscoveryResult):
            self._show_local_submission_error(
                "AITRANSS preflight failed",
                RuntimeError("AITRANSS preflight returned invalid data"),
            )
            return
        active = self._active_geometry_workspace()
        if active is not None and active.restart_draft is not None:
            self._open_step4_restart_dialog(result.executable_path, None)
            return
        self._open_step4_dialog(result.executable_path, None)

    @Slot(object)
    def _step4_preflight_failed(self, error: object) -> None:
        if isinstance(error, AitranssDiscoveryError):
            # Preparation remains available, but real Submit is visibly disabled.
            active = self._active_geometry_workspace()
            if active is not None and active.restart_draft is not None:
                self._open_step4_restart_dialog(None, str(error))
                return
            self._open_step4_dialog(None, str(error))
            return
        self._show_submission_error(error)

    def _open_step4_dialog(
        self,
        executable_path: str | None,
        executable_error: str | None,
    ) -> None:
        snapshot = self._recovery_snapshot
        profile = self._pending_transport_profile
        dependencies = self._pending_transport_dependencies
        if snapshot is None or profile is None or dependencies is None:
            self._show_local_submission_error(
                "Unable to prepare Step 4",
                RuntimeError("Step-4 project context is unavailable"),
            )
            return
        try:
            dialog = AitranssStep4Dialog(
                snapshot,
                profile,
                verified_aitranss_path=executable_path,
                executable_preflight_error=executable_error,
                parent=self,
            )
        except Exception as error:
            self._show_local_submission_error("Unable to prepare Step 4", error)
            return
        if executable_path is None:
            self._set_operation(
                executable_error
                or "AITRANSS executable is unavailable; Step-4 Submit is disabled."
            )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation(
                "Step-4 submission was not sent."
                if executable_path is not None
                else (
                    "tcontrol was previewed, but Step-4 submission remains "
                    "disabled because the configured AITRANSS executable was not verified."
                )
            )
            return
        try:
            password = self._temporary_transport_password(dependencies, profile)
            if password is False:
                self._set_operation("Step-4 submission was cancelled.")
                return
            request = Step4SubmissionRequest(
                profile=profile,
                project=snapshot.project,
                structure=snapshot.optimized_structure,
                evidence=snapshot.transport_evidence,
                tcontrol_settings=dialog.selected_settings(),
                execution_settings=dialog.selected_execution_settings(),
                verified_aitranss_path=executable_path,
                supplied_password=(password if isinstance(password, str) else None),
            )
            service = dependencies.transport_submission_service
            if service is None:
                raise RuntimeError("transport submission service is unavailable")
        except Exception as error:
            self._show_local_submission_error("Invalid Step-4 submission", error)
            return
        worker = Step4SubmissionWorker(service, request)
        worker.signals.progress.connect(self._routed_transport_progress)
        worker.signals.succeeded.connect(self._routed_step4_submission_succeeded)
        worker.signals.failed.connect(self._routed_step4_submission_failed)
        worker.signals.finished.connect(self._routed_transport_worker_finished)
        self._transport_workers.add(worker)
        self._set_operation("Preparing explicit Step-4 submission...")
        self._submission_thread_pool.start(worker)

    def _open_step4_restart_dialog(
        self,
        executable_path: str | None,
        executable_error: str | None,
    ) -> None:
        active = self._active_geometry_workspace()
        profile = self._pending_transport_profile
        dependencies = self._pending_transport_dependencies
        draft = active.restart_draft if active is not None else None
        if (
            active is None
            or draft is None
            or profile is None
            or dependencies is None
            or draft.transport_evidence is None
            or draft.surface_proposal is None
            or draft.tcontrol_settings is None
            or draft.step4_execution_settings is None
        ):
            self._show_local_submission_error(
                "Unable to prepare Step-4 restart",
                RuntimeError("Step-4 restart context is unavailable"),
            )
            return
        snapshot = ProjectRecoverySnapshot(
            project=draft.source_project,
            active_step_kind=ProjectStepKind.TRANSMISSION,
            status_message="Step-4 restart draft",
            optimized_structure=draft.source_structure,
            connectivity=draft.connectivity,
            transport_evidence=draft.transport_evidence,
            surface_proposal=draft.surface_proposal,
        )
        try:
            dialog = AitranssStep4Dialog(
                snapshot,
                profile,
                verified_aitranss_path=executable_path,
                executable_preflight_error=executable_error,
                initial_settings=draft.tcontrol_settings,
                initial_execution_settings=draft.step4_execution_settings,
                self_energy_filename=draft.tcontrol_self_energy_filename,
                restart_mode=True,
                parent=self,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare Step-4 restart",
                error,
            )
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Step-4 restart was not submitted.")
            return
        try:
            password = self._temporary_transport_password(dependencies, profile)
            if password is False:
                self._set_operation("Step-4 restart was cancelled.")
                return
            request = Step4SettingsRetryRequest(
                profile=profile,
                project=draft.source_project,
                structure=draft.source_structure,
                evidence=draft.transport_evidence,
                tcontrol_settings=dialog.selected_settings(),
                execution_settings=dialog.selected_execution_settings(),
                self_energy_filename=draft.tcontrol_self_energy_filename,
                verified_aitranss_path=executable_path or "",
                supplied_password=(password if isinstance(password, str) else None),
            )
            service = dependencies.transport_submission_service
            if service is None:
                raise RuntimeError("transport submission service is unavailable")
        except Exception as error:
            self._show_local_submission_error(
                "Invalid Step-4 restart",
                error,
            )
            return
        worker = Step4SettingsRetryWorker(service, request)
        worker.signals.progress.connect(self._routed_transport_progress)
        worker.signals.succeeded.connect(
            self._routed_step4_restart_submission_succeeded
        )
        worker.signals.failed.connect(self._routed_step4_submission_failed)
        worker.signals.finished.connect(self._routed_transport_worker_finished)
        self._transport_workers.add(worker)
        self._set_operation("Preparing explicit edited Step-4 restart...")
        self._submission_thread_pool.start(worker)

    @Slot(object)
    def _routed_step4_restart_submission_succeeded(self, result: object) -> None:
        with self._geometry_callback_context(
            self._transport_origin_workspace_id
        ):
            self._step4_restart_submission_succeeded(result)

    def _step4_restart_submission_succeeded(self, result: object) -> None:
        if not isinstance(result, TransportSubmissionResult):
            self._show_local_submission_error(
                "Step-4 restart failed",
                RuntimeError("Step-4 restart returned invalid data"),
            )
            return
        active = self._active_geometry_workspace()
        if active is not None and active.restart_draft is not None:
            active.restart_draft = replace(
                active.restart_draft,
                source_project=result.project,
                source_job_id=result.job_id,
                source_terminal_confirmed=False,
            )
        self._set_operation(
            f"Step-4 restart submitted as job {result.job_id} ({result.step.state.value})."
        )
        QMessageBox.information(
            self,
            "Step-4 restart submitted",
            "AITRANSS Step-4 restart submitted\n\n"
            f"Job ID: {result.job_id}\n"
            f"Script: {result.step.submit_script_filename}\n"
            f"Slurm output: {result.step.slurm_output_filename}",
        )

    @Slot(object)
    def _step4_submission_succeeded(self, result: object) -> None:
        if not isinstance(result, TransportSubmissionResult):
            self._show_local_submission_error(
                "Step-4 submission failed",
                RuntimeError("Step-4 submission returned invalid data"),
            )
            return
        if self._recovery_snapshot is None:
            self._show_local_submission_error(
                "Step-4 submission state unavailable",
                RuntimeError(
                    f"Slurm accepted job {result.job_id}; refresh Projects to "
                    "reload the durable manifest"
                ),
            )
            return
        self._recovery_snapshot = replace(
            self._recovery_snapshot,
            project=result.project,
            active_step_kind=ProjectStepKind.TRANSMISSION,
            status_message=(
                f"Step 4 submitted as Slurm job {result.job_id}. "
                "Result assessment is deferred."
            ),
        )
        self._set_operation(
            f"Step 4 submitted as job {result.job_id} (QUEUED); "
            "Step-4 result assessment is not implemented yet."
        )
        QMessageBox.information(
            self,
            "Step 4 submitted",
            "AITRANSS Step 4 submitted\n\n"
            f"Job ID: {result.job_id}\n"
            "Script: submit.aitranss.sh\n"
            "Slurm output: aitranss.out",
        )

    @Slot(object)
    def _step4_submission_failed(self, error: object) -> None:
        self._show_submission_error(error)

    @Slot(object)
    def _transport_worker_finished(self, worker: object) -> None:
        self._transport_workers.discard(worker)
        if self._transport_workers:
            return
        self._transport_operation_running = False
        self._pending_transport_dependencies = None
        self._pending_transport_profile = None
        self._pending_transport_project_id = None
        self._notify_project_manager_external_operation_changed()
        self._projects_action.setEnabled(True)
        self._update_continuation_control()
        self._route_active_workspace()

    def _submit_aims_optimization(self) -> None:
        workspace = self._active_geometry_workspace()
        if workspace is not None and workspace.restart_draft is not None:
            self._submit_restart_draft()
            return
        self._submit_new_optimization_project(
            preselected_engine=CalculationWorkflowKind.FHI_AIMS_AITRANSS,
        )

    def _submit_orca_optimization(self) -> None:
        self._submit_new_optimization_project(
            preselected_engine=CalculationWorkflowKind.ORCA,
        )

    def _submit_restart_draft(self) -> None:
        workspace = self._active_geometry_workspace()
        if (
            workspace is None
            or workspace is not self._bound_geometry_workspace
            or workspace.restart_draft is None
            or self._structure is None
        ):
            return
        draft = workspace.restart_draft
        if not draft.source_terminal_confirmed:
            self._show_local_submission_error(
                "Restart submission locked",
                ValueError(
                    "Use Project Manager Refresh Status until the exact source "
                    "Job is durably terminal."
                ),
            )
            return
        if self._submission_running or self._transport_operation_running:
            self._set_operation("A remote submission operation is already in progress.")
            return

        geometry_changed = self._structure.atoms != draft.source_structure.atoms
        if draft.coordinate_only:
            if tuple(atom.element for atom in self._structure) != tuple(
                atom.element for atom in draft.source_structure
            ):
                self._show_local_submission_error(
                    "Invalid restart geometry",
                    ValueError(
                        "Step-3/4 restart geometry must preserve atom count, order, "
                        "and elements."
                    ),
                )
                return
            try:
                surface = propose_electrode_surfaces(
                    self._structure,
                    draft.electrode_provenance,
                )
            except Exception as error:
                self._show_local_submission_error(
                    "Invalid restart geometry",
                    error,
                )
                return
            expected_mappings = tuple(
                item.local_to_global_indices for item in draft.electrode_provenance
            )
            if expected_mappings != (
                surface.left_local_to_global_zero_based,
                surface.right_local_to_global_zero_based,
            ):
                self._show_local_submission_error(
                    "Invalid restart geometry",
                    ValueError(
                        "Step-3/4 edits changed the accepted electrode provenance."
                    ),
                )
                return

        if (
            draft.source_step is ProjectStepKind.TRANSMISSION
            and not geometry_changed
            and next(
                step
                for step in draft.source_project.steps
                if step.kind is ProjectStepKind.TRANSMISSION
            ).scheduler_state
            == "CANCELLED"
        ):
            self._submit_restart_step4(draft)
            return
        starting_step = (
            ProjectStepKind.TRANSPORT_CONVERGENCE
            if draft.source_step
            in {
                ProjectStepKind.TRANSPORT_CONVERGENCE,
                ProjectStepKind.TRANSMISSION,
            }
            else draft.source_step
        )
        self._submit_restart_as_new_project(draft, starting_step)

    def _submit_restart_as_new_project(
        self,
        draft: ProjectTaskRestartDraft,
        starting_step: ProjectStepKind,
    ) -> None:
        if self._structure is None:
            return
        try:
            dependencies = _create_project_submission_dependencies()
            recommendation = recommend_start_step(
                self._structure,
                self._anchors,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare restart submission",
                error,
            )
            return
        profile = draft.profile
        project_dialog = NewCalculationProjectDialog(
            (profile,),
            profile.profile_id,
            draft.source_project.display_name + "_restart",
            recommendation,
            preview_date=datetime.now().astimezone().date(),
            cluster_settings_callback=lambda selected: (
                self._configure_restart_cluster_settings(selected)
            ),
            fixed_starting_step=starting_step,
            parent=self,
        )
        if project_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Restart project creation was cancelled.")
            return
        selection = project_dialog.selected_project()

        if starting_step in {
            ProjectStepKind.MOLECULE_OPT,
            ProjectStepKind.MOLECULE_AU_OPT,
        }:
            initial = draft.optimization_settings
            if initial is None:
                self._show_local_submission_error(
                    "Restart settings unavailable",
                    ValueError("Original optimization settings were not recovered."),
                )
                return
            settings_dialog = AimsOptimizationSettingsDialog(
                self._structure,
                initial_settings=initial,
                parent=self,
            )
        else:
            initial = draft.transport_settings
            if initial is None:
                self._show_local_submission_error(
                    "Restart settings unavailable",
                    ValueError("Original Step-3 settings were not recovered."),
                )
                return
            settings_dialog = TransportConvergenceSettingsDialog(
                self._structure,
                initial_settings=initial,
                parent=self,
            )
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Restart submission was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        try:
            if starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE:
                input_plan = TransportConvergenceInputPlan(
                    self._structure,
                    settings,
                )
            else:
                input_plan = AimsOptimizationInputPlan(
                    self._structure,
                    settings,
                )
            saved_password_exists = (
                selection.profile.save_password
                and dependencies.secret_store.get_password(
                    selection.profile.profile_id
                )
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to generate restart inputs",
                error,
            )
            return
        confirmation = SubmissionConfirmationDialog(
            selection,
            settings,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("Restart submission was cancelled.")
            return
        password = confirmation.take_temporary_password()
        try:
            request = NewProjectSubmissionRequest(
                profile=selection.profile,
                base_name=selection.base_name,
                source_molecule_name=draft.source_project.source_molecule_name,
                starting_step=starting_step,
                input_plan=input_plan,
                supplied_password=password,
                restart_provenance=draft.restart_provenance,
                restart_source_project=draft.source_project,
                restart_electrode_provenance=(
                    draft.electrode_provenance
                    if starting_step is ProjectStepKind.TRANSPORT_CONVERGENCE
                    else ()
                ),
                restart_profile_rebind_confirmed=(
                    draft.profile_rebind_confirmed
                ),
            )
        except Exception as error:
            self._show_local_submission_error(
                "Invalid restart submission",
                error,
            )
            return
        self._start_submission(dependencies, request)

    def _configure_restart_cluster_settings(
        self,
        profile: ServerProfile,
    ) -> ServerProfile | None:
        """Edit a temporary retry preset without changing the saved profile."""

        dialog = ClusterExecutionSettingsDialog(profile, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return replace(profile, execution_preset=dialog.selected_preset())

    def _submit_imported_step2(self) -> None:
        try:
            self._current_step2_contact_context()
        except (ImportedContactAuEligibilityError, TypeError, ValueError) as error:
            self._show_local_submission_error("Cannot start direct Step 2", error)
            return
        self._submit_new_optimization_project(
            fixed_starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        )

    def _submit_new_optimization_project(
        self,
        *,
        fixed_starting_step: ProjectStepKind | None = None,
        preselected_engine: CalculationWorkflowKind | None = None,
    ) -> None:
        """Collect all local choices before starting one real remote worker."""

        active = self._active_geometry_workspace()
        if (
            active is not self._bound_geometry_workspace
            or active is None
            or active.read_only
        ):
            return
        if self._submission_running:
            self._set_operation("A Slurm submission is already in progress.")
            return
        if self._structure is None:
            QMessageBox.critical(
                self,
                "Cannot submit FHI-aims optimization",
                "Load a molecular structure before submitting optimization inputs.",
            )
            self._set_operation(
                "No molecular structure is loaded for remote submission."
            )
            return

        try:
            dependencies = _create_project_submission_dependencies()
            profile_collection = dependencies.profile_repository.load()
        except Exception as error:
            self._show_local_submission_error(
                "Unable to prepare secure submission",
                error,
            )
            return
        if not profile_collection.profiles:
            QMessageBox.critical(
                self,
                "No saved server",
                "Create and save a Server Connection before submitting.",
            )
            self._set_operation(
                "Remote submission requires a saved Server Connection."
            )
            return

        try:
            recommendation = recommend_start_step(
                self._structure,
                self._anchors,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to plan calculation project",
                error,
            )
            return

        default_base_name = (
            self._source_path.stem if self._source_path is not None else ""
        )
        project_dialog_options = {
            "preview_date": datetime.now().astimezone().date(),
            "cluster_settings_callback": lambda selected: (
                self._configure_submission_cluster_settings(
                    dependencies,
                    selected,
                )
            ),
            "parent": self,
        }
        if fixed_starting_step is not None:
            project_dialog_options["fixed_starting_step"] = fixed_starting_step
        if preselected_engine is not None:
            project_dialog_options["preselected_engine"] = preselected_engine
        project_dialog = NewCalculationProjectDialog(
            profile_collection.profiles,
            profile_collection.last_selected_profile_id,
            default_base_name,
            recommendation,
            **project_dialog_options,
        )
        if project_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Remote project submission was cancelled.")
            return
        selection = project_dialog.selected_project()
        try:
            dependencies.profile_repository.set_last_selected(
                selection.profile.profile_id
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to save selected server",
                error,
            )
            return

        if selection.workflow_kind is CalculationWorkflowKind.ORCA:
            self._prepare_orca_optimization_submission(
                dependencies,
                selection,
            )
            return

        settings_dialog = AimsOptimizationSettingsDialog(
            self._structure,
            parent=self,
        )
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("Remote project submission was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        try:
            input_plan = AimsOptimizationInputPlan(
                self._structure,
                settings,
            )
        except Exception as error:
            self._show_local_submission_error(
                "FHI-aims input generation failed",
                error,
            )
            return

        try:
            saved_password_exists = (
                selection.profile.save_password
                and dependencies.secret_store.get_password(
                    selection.profile.profile_id
                )
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "Unable to read saved credential",
                error,
            )
            return

        confirmation = SubmissionConfirmationDialog(
            selection,
            settings,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("Remote project submission was cancelled.")
            return
        temporary_password = confirmation.take_temporary_password()
        source_name = (
            self._source_path.name
            if self._source_path is not None
            else selection.base_name
        )
        try:
            request = NewProjectSubmissionRequest(
                profile=selection.profile,
                base_name=selection.base_name,
                source_molecule_name=source_name,
                starting_step=selection.starting_step,
                input_plan=input_plan,
                supplied_password=temporary_password,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Invalid project submission",
                error,
            )
            return
        self._start_submission(dependencies, request)

    def _prepare_orca_optimization_submission(
        self,
        dependencies: _ProjectSubmissionDependencies,
        selection,
    ) -> None:
        """Collect and preview one ORCA optimization before remote mutation."""

        if self._structure is None:
            return
        if dependencies.orca_submission_service is None:
            self._show_local_submission_error(
                "ORCA submission unavailable",
                RuntimeError("The ORCA submission service is unavailable."),
            )
            return
        try:
            settings_dialog = OrcaOptimizationSettingsDialog(
                self._structure,
                selection.profile,
                self,
            )
        except Exception as error:
            self._show_local_submission_error(
                "Cannot configure ORCA optimization",
                error,
            )
            return
        if settings_dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_operation("ORCA optimization submission was cancelled.")
            return
        settings = settings_dialog.selected_settings()
        project_id = uuid4()
        try:
            input_text = render_orca_optimization_input(self._structure, settings)
            preset = replace(
                selection.profile.execution_preset,
                nodes=settings.scheduler_nodes,
                ntasks=settings.process_count,
                cpus_per_task=1,
                runtime_minutes=settings.runtime_minutes,
                memory_gb=settings.scheduler_memory_gb,
                omp_num_threads=1,
            )
            script_text = render_orca_submit_script(
                preset,
                selection.profile.orca_runtime,
                project_id,
                settings,
            )
            saved_password_exists = (
                selection.profile.save_password
                and dependencies.secret_store.get_password(
                    selection.profile.profile_id
                )
                is not None
            )
        except Exception as error:
            self._show_local_submission_error(
                "ORCA input generation failed",
                error,
            )
            return
        confirmation = OrcaSubmissionConfirmationDialog(
            selection.profile,
            selection.nominal_directory_name,
            "ORCA molecule optimization",
            input_text,
            script_text,
            temporary_password_required=not saved_password_exists,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            confirmation.take_temporary_password()
            self._set_operation("ORCA optimization submission was cancelled.")
            return
        password = confirmation.take_temporary_password()
        source_name = (
            self._source_path.name
            if self._source_path is not None
            else selection.base_name
        )
        request = OrcaOptimizationSubmissionRequest(
            profile=selection.profile,
            base_name=selection.base_name,
            source_molecule_name=source_name,
            structure=self._structure,
            settings=settings,
            supplied_password=password,
            project_id=project_id,
        )
        self._start_submission(dependencies, request)

    def _configure_submission_cluster_settings(
        self,
        dependencies: _ProjectSubmissionDependencies,
        profile,
    ):
        dialog = ClusterExecutionSettingsDialog(
            profile,
            self,
            connection_service=dependencies.connection_service,
            known_hosts=dependencies.known_hosts,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        updated = replace(
            profile,
            execution_preset=dialog.selected_preset(),
            aitranss_runtime=dialog.selected_aitranss_runtime(),
            runtime_hints=getattr(
                dialog,
                "selected_runtime_hints",
                lambda: profile.runtime_hints,
            )(),
            orca_runtime=getattr(
                dialog,
                "selected_orca_runtime",
                lambda: profile.orca_runtime,
            )(),
        )
        dependencies.profile_service.save(updated)
        return updated

    def _start_submission(
        self,
        dependencies: _ProjectSubmissionDependencies,
        request: (
            NewProjectSubmissionRequest
            | ExistingProjectStepSubmissionRequest
            | TransportConvergenceSubmissionRequest
            | OrcaOptimizationSubmissionRequest
            | OrcaFrequencySubmissionRequest
        ),
    ) -> None:
        if self._submission_running:
            return
        active_workspace = self._active_geometry_workspace()
        frequency_only = isinstance(request, OrcaFrequencySubmissionRequest)
        if active_workspace is None and not frequency_only:
            return
        if (
            active_workspace is not None
            and self._pick_mode is _ViewerPickMode.AU_LATTICE_EXTENSION
        ):
            self._set_pick_mode(_ViewerPickMode.NORMAL, announce=False)
        self._submission_origin_workspace_id = (
            active_workspace.runtime_id if active_workspace is not None else None
        )
        self._reset_submission_lifecycle_tracking()
        self._submission_running = True
        if active_workspace is not None:
            active_workspace.electrode_extension_button.setEnabled(False)
        self._submission_retry_pending = False
        self._submission_trust_retry_used = False
        self._pending_submission_dependencies = dependencies
        self._pending_submission_request = request
        self._notify_project_manager_external_operation_changed()
        self._submit_aims_action.setEnabled(False)
        self._continue_step2_action.setEnabled(False)
        self._continue_step3_action.setEnabled(False)
        self._continue_step4_action.setEnabled(False)
        self._projects_action.setEnabled(False)
        self._launch_pending_submission_worker()

    def _launch_pending_submission_worker(self) -> None:
        dependencies = self._pending_submission_dependencies
        request = self._pending_submission_request
        if dependencies is None or request is None:
            self._show_submission_error(
                RuntimeError("submission state is unavailable")
            )
            self._finish_submission_action()
            return
        if isinstance(
            request,
            (OrcaOptimizationSubmissionRequest, OrcaFrequencySubmissionRequest),
        ):
            if dependencies.orca_submission_service is None:
                self._show_submission_error(
                    RuntimeError("The ORCA submission service is unavailable.")
                )
                self._finish_submission_action()
                return
            worker = OrcaSubmissionWorker(
                dependencies.orca_submission_service,
                request,
            )
        else:
            worker = ProjectSubmissionWorker(
                dependencies.submission_service,
                request,
            )
        worker.signals.progress.connect(self._routed_submission_progress)
        worker.signals.succeeded.connect(self._routed_submission_succeeded)
        worker.signals.failed.connect(self._routed_submission_failed)
        worker.signals.finished.connect(self._routed_submission_worker_finished)
        self._submission_workers.add(worker)
        self._submission_thread_pool.start(worker)

    @Slot(str)
    def _routed_submission_progress(self, message: str) -> None:
        with self._geometry_callback_context(
            self._submission_origin_workspace_id
        ):
            self._submission_progress(message)

    @Slot(object)
    def _routed_submission_succeeded(self, result: object) -> None:
        with self._geometry_callback_context(
            self._submission_origin_workspace_id
        ):
            self._submission_succeeded(result)

    @Slot(object)
    def _routed_submission_failed(self, error: object) -> None:
        with self._geometry_callback_context(
            self._submission_origin_workspace_id
        ):
            self._submission_failed(error)

    @Slot(object)
    def _routed_submission_worker_finished(self, worker: object) -> None:
        origin = self._submission_origin_workspace_id
        with self._geometry_callback_context(origin):
            self._submission_worker_finished(worker)
        if not self._submission_running and not self._submission_workers:
            self._submission_origin_workspace_id = None
            self._release_retired_workspace(origin)

    @Slot(str)
    def _submission_progress(self, message: str) -> None:
        self._set_operation(message)

    @Slot(object)
    def _submission_succeeded(self, result: object) -> None:
        self._submission_retry_pending = False
        request = self._pending_submission_request
        if isinstance(result, (ProjectSubmissionResult, OrcaSubmissionResult)):
            self._submission_backend_outcome = (
                _SubmissionBackendOutcome.SUCCESS
            )
            self._submission_backend_result = result
            if result.step.kind is ProjectStepKind.MOLECULE_AU_OPT:
                self._submission_step2_refresh_required = True
            elif result.step.kind is ProjectStepKind.TRANSPORT_CONVERGENCE:
                self._submission_step3_refresh_required = True
                workspace = self._bound_geometry_workspace
                if workspace is not None:
                    workspace.lattice_extension_submitted_immutable = True
                    workspace.electrode_extension_button.setEnabled(False)
        project_id, step_kind = _submission_request_context(request)
        if isinstance(result, (ProjectSubmissionResult, OrcaSubmissionResult)):
            project_id = result.project.project_id
            step_kind = result.step.kind
        record_submission_lifecycle_event(
            "success_callback_entered",
            project_id=project_id,
            step_kind=step_kind,
            job_id=(
                result.job_id
                if isinstance(result, (ProjectSubmissionResult, OrcaSubmissionResult))
                else None
            ),
        )
        self._submission_terminal_callback_active = True
        try:
            if not isinstance(result, (ProjectSubmissionResult, OrcaSubmissionResult)):
                raise RuntimeError("submission returned an invalid result")
            if request is None:
                raise RuntimeError("submission request identity is unavailable")
            if isinstance(result, OrcaSubmissionResult):
                self._present_orca_submission_success(request, result)
            else:
                self._present_submission_success(request, result)
        except Exception as error:
            record_submission_lifecycle_event(
                "success_callback_presentation_failed",
                project_id=project_id,
                step_kind=step_kind,
                job_id=(
                    result.job_id
                    if isinstance(result, (ProjectSubmissionResult, OrcaSubmissionResult))
                    else None
                ),
                exception_type=type(error).__name__,
            )
        else:
            self._submission_terminal_presentation = (
                _SubmissionTerminalPresentation.SUCCESS
            )
            record_submission_lifecycle_event(
                "success_callback_completed",
                project_id=project_id,
                step_kind=step_kind,
                job_id=result.job_id,
            )
        finally:
            self._submission_terminal_callback_active = False
            self._complete_deferred_submission_finish()

    def _present_orca_submission_success(
        self,
        request: OrcaOptimizationSubmissionRequest | OrcaFrequencySubmissionRequest,
        result: OrcaSubmissionResult,
    ) -> None:
        stage = (
            "ORCA optimization"
            if result.step.kind is ProjectStepKind.ORCA_OPTIMIZATION
            else "ORCA frequency"
        )
        self._set_operation(
            f"{stage} submitted as job {result.job_id} "
            f"({result.step.state.value}): {result.remote_step_directory}"
        )
        QMessageBox.information(
            self,
            f"{stage} submitted",
            f"Server: {request.profile.name}\n"
            f"Project: {result.project.remote_directory_name}\n"
            f"Stage: {stage}\n"
            f"Job ID: {result.job_id}\n"
            f"State: {result.step.state.value}\n"
            f"Remote: {result.remote_step_directory}",
        )

    def _present_submission_success(
        self,
        request: (
            NewProjectSubmissionRequest
            | ExistingProjectStepSubmissionRequest
            | TransportConvergenceSubmissionRequest
        ),
        result: ProjectSubmissionResult,
    ) -> None:
        if isinstance(request, TransportConvergenceSubmissionRequest):
            try:
                self._apply_successful_step3_result(result)
            except Exception as error:
                self._submission_step3_refresh_required = True
                record_submission_lifecycle_event(
                    "step3_snapshot_update_failed",
                    project_id=result.project.project_id,
                    step_kind=result.step.kind,
                    job_id=result.job_id,
                    exception_type=type(error).__name__,
                )
                self._set_operation(
                    f"Step 3 submitted as job {result.job_id} "
                    f"({result.step.state.value}), but the local project view "
                    "could not be updated. Use Project Manager... to inspect the "
                    "durable manifest state."
                )
                QMessageBox.warning(
                    self,
                    "Step 3 submitted; refresh required",
                    "Step 3 submitted successfully, but the local project view "
                    "could not be updated.\n\n"
                    f"Job ID: {result.job_id}\n"
                    "Remote folder: molecule_Au/transport\n\n"
                    "Use Project Manager... to inspect the durable remote state.",
                )
                return
            self._set_operation(
                f"Step 3 submitted as job {result.job_id} "
                f"({result.step.state.value}) in molecule_Au/transport."
            )
            QMessageBox.information(
                self,
                "Step 3 submitted",
                "Step 3 submitted\n\n"
                f"Job ID: {result.job_id}\n\n"
                "Remote folder:\n"
                "molecule_Au/transport",
            )
            return

        continued = isinstance(request, ExistingProjectStepSubmissionRequest)
        message = (
            ("Step 2 submitted successfully\n\n" if continued else "Submitted successfully\n\n")
            + f"Server: {request.profile.name}\n"
            f"Project: {result.project.remote_directory_name}\n"
            f"Step: {step_label(result.step.kind)}\n"
            f"Job ID: {result.job_id}\n"
            f"State: {result.step.state.value}\n"
            f"Remote: {result.project.remote_project_path}\n"
            f"Step directory: {result.remote_step_directory}"
        )
        if continued:
            try:
                self._apply_successful_step2_result(result)
            except Exception as error:
                self._submission_step2_refresh_required = True
                record_submission_lifecycle_event(
                    "step2_snapshot_update_failed",
                    project_id=result.project.project_id,
                    step_kind=result.step.kind,
                    job_id=result.job_id,
                    exception_type=type(error).__name__,
                )
                refresh_message = (
                    "Step 2 submitted successfully, but the local project view "
                    "could not be updated.\n\n"
                    f"Project: {result.project.remote_directory_name}\n"
                    f"Job ID: {result.job_id}\n"
                    f"State: {result.step.state.value}\n\n"
                    "Use Project Manager... and Refresh Status to load the durable "
                    "remote project state."
                )
                self._set_operation(
                    f"Step 2 submitted successfully as job {result.job_id} "
                    f"({result.step.state.value}), but the local project view "
                    "could not be updated. Use Project Manager... and Refresh Status."
                )
                QMessageBox.warning(
                    self,
                    "Step 2 submitted; refresh required",
                    refresh_message,
                )
                return
        self._set_operation(
            f"Submitted job {result.job_id} successfully "
            f"({result.step.state.value}): {result.project.remote_project_path}"
        )
        QMessageBox.information(
            self,
            "Step 2 submitted successfully" if continued else "Submitted successfully",
            message,
        )

    def _apply_successful_step2_result(
        self,
        result: ProjectSubmissionResult,
    ) -> None:
        if self._recovery_snapshot is None:
            raise RuntimeError("the recovered project snapshot is unavailable")
        self._recovery_snapshot = replace(
            self._recovery_snapshot,
            project=result.project,
            active_step_kind=ProjectStepKind.MOLECULE_AU_OPT,
            status_message=(
                f"Step 2 submitted to Slurm as job {result.job_id}."
            ),
            optimized_structure=None,
            connectivity=None,
        )
        self._submission_step2_refresh_required = False

    def _apply_successful_step3_result(
        self,
        result: ProjectSubmissionResult,
    ) -> None:
        if self._recovery_snapshot is None:
            raise RuntimeError("the recovered project snapshot is unavailable")
        if result.step.kind is not ProjectStepKind.TRANSPORT_CONVERGENCE:
            raise RuntimeError("the submission result is not Step 3")
        self._recovery_snapshot = replace(
            self._recovery_snapshot,
            project=result.project,
            status_message=(
                f"Step 3 submitted to Slurm as job {result.job_id}."
            ),
        )
        self._submission_step3_refresh_required = False

    @Slot(object)
    def _submission_failed(self, error: object) -> None:
        self._submission_backend_outcome = _SubmissionBackendOutcome.FAILURE
        self._submission_backend_result = None
        if (
            isinstance(error, ProjectSubmissionError)
            and error.step_kind is ProjectStepKind.TRANSPORT_CONVERGENCE
        ):
            # A Step-3 directory or sbatch dispatch may now exist. Phase 3A has
            # no interrupted-creation recovery, so this session must not offer
            # another submission attempt from stale local state.
            self._submission_step3_refresh_required = True
        request = self._pending_submission_request
        project_id, step_kind = _submission_request_context(request)
        job_id = getattr(error, "job_id", None)
        record_submission_lifecycle_event(
            "failure_callback_entered",
            project_id=project_id,
            step_kind=step_kind,
            job_id=job_id if isinstance(job_id, str) else None,
            exception_type=type(error).__name__,
        )
        self._submission_terminal_callback_active = True
        retry_scheduled = False
        try:
            retry_scheduled = self._present_submission_failure(error)
        except Exception as presentation_error:
            self._submission_retry_pending = False
            record_submission_lifecycle_event(
                "failure_callback_presentation_failed",
                project_id=project_id,
                step_kind=step_kind,
                job_id=job_id if isinstance(job_id, str) else None,
                exception_type=type(presentation_error).__name__,
            )
        else:
            if retry_scheduled:
                self._submission_backend_outcome = (
                    _SubmissionBackendOutcome.NONE
                )
                record_submission_lifecycle_event(
                    "host_trust_retry_scheduled",
                    project_id=project_id,
                    step_kind=step_kind,
                )
            else:
                self._submission_terminal_presentation = (
                    _SubmissionTerminalPresentation.FAILURE
                )
                record_submission_lifecycle_event(
                    "failure_callback_completed",
                    project_id=project_id,
                    step_kind=step_kind,
                    job_id=job_id if isinstance(job_id, str) else None,
                )
        finally:
            self._submission_terminal_callback_active = False
            self._complete_deferred_submission_finish()

    def _present_submission_failure(self, error: object) -> bool:
        dependencies = self._pending_submission_dependencies
        if (
            isinstance(error, UnknownHostKey)
            and dependencies is not None
            and not self._submission_trust_retry_used
        ):
            if self._confirm_submission_unknown_host(error.info):
                try:
                    dependencies.known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_submission_error(trust_error)
                    return False
                self._submission_trust_retry_used = True
                self._submission_retry_pending = True
                self._set_operation(
                    "Host key trusted; retrying the connection once."
                )
                return True
            self._set_operation(
                "Submission cancelled; the unknown host key was not trusted."
            )
            return False
        self._submission_retry_pending = False
        self._show_submission_error(error)
        return False

    @Slot(object)
    def _submission_worker_finished(self, worker: object) -> None:
        if not isinstance(worker, (ProjectSubmissionWorker, OrcaSubmissionWorker)):
            return
        project_id, step_kind = _submission_request_context(
            self._pending_submission_request
        )
        if self._submission_backend_result is not None:
            project_id = self._submission_backend_result.project.project_id
            step_kind = self._submission_backend_result.step.kind
        record_submission_lifecycle_event(
            "finished_callback_entered",
            project_id=project_id,
            step_kind=step_kind,
            job_id=(
                self._submission_backend_result.job_id
                if self._submission_backend_result is not None
                else None
            ),
        )
        self._submission_workers.discard(worker)
        if self._submission_workers:
            return
        if self._submission_terminal_callback_active:
            self._submission_finished_pending = True
            record_submission_lifecycle_event(
                "finished_callback_deferred",
                project_id=project_id,
                step_kind=step_kind,
            )
            return
        self._complete_submission_worker_finish()

    def _complete_deferred_submission_finish(self) -> None:
        if (
            self._submission_finished_pending
            and not self._submission_terminal_callback_active
        ):
            self._submission_finished_pending = False
            self._complete_submission_worker_finish()

    def _complete_submission_worker_finish(self) -> None:
        if self._submission_retry_pending:
            self._submission_retry_pending = False
            self._reset_submission_lifecycle_tracking(for_retry=True)
            self._launch_pending_submission_worker()
            return
        self._finish_submission_action()
        if (
            self._submission_terminal_presentation
            is _SubmissionTerminalPresentation.NONE
        ):
            self._present_submission_finished_fallback()
        result = self._submission_backend_result
        record_submission_lifecycle_event(
            "finished_callback_completed",
            project_id=(result.project.project_id if result is not None else None),
            step_kind=(result.step.kind if result is not None else None),
            job_id=(result.job_id if result is not None else None),
        )

    def _present_submission_finished_fallback(self) -> None:
        result = self._submission_backend_result
        if (
            self._submission_backend_outcome
            is _SubmissionBackendOutcome.SUCCESS
            and result is not None
        ):
            message = (
                f"Submission succeeded as job {result.job_id} "
                f"({result.step.state.value}), but the interface could not "
                "display the result. Use Project Manager... and Refresh Status to "
                "verify the durable project state."
            )
            event = "finished_success_fallback_presented"
        elif self._submission_backend_outcome is _SubmissionBackendOutcome.FAILURE:
            message = (
                "Submission failed, but the interface could not display the "
                "detailed error. Use Project Manager... and Refresh Status to verify "
                "the remote project state."
            )
            event = "finished_failure_fallback_presented"
        else:
            message = (
                "Submission finished without a displayable result. Use "
                "Project Manager... and Refresh Status to verify the remote project state."
            )
            event = "finished_unknown_fallback_presented"
        self._operation_label.setText(message)
        self._submission_terminal_presentation = (
            _SubmissionTerminalPresentation.INTERNAL_PRESENTATION_FAILURE
        )
        record_submission_lifecycle_event(
            event,
            project_id=(result.project.project_id if result is not None else None),
            step_kind=(result.step.kind if result is not None else None),
            job_id=(result.job_id if result is not None else None),
        )

    def _reset_submission_lifecycle_tracking(
        self,
        *,
        for_retry: bool = False,
    ) -> None:
        self._submission_backend_outcome = _SubmissionBackendOutcome.NONE
        self._submission_backend_result = None
        self._submission_terminal_presentation = (
            _SubmissionTerminalPresentation.NONE
        )
        self._submission_terminal_callback_active = False
        self._submission_finished_pending = False
        if not for_retry:
            self._submission_step2_refresh_required = False

    def _finish_submission_action(self) -> None:
        self._pending_submission_request = None
        self._pending_submission_dependencies = None
        self._submission_running = False
        self._submission_retry_pending = False
        self._notify_project_manager_external_operation_changed()
        active = self._active_geometry_workspace()
        self._submit_aims_action.setEnabled(
            active is not None
            and not active.read_only
            and active.restart_draft is None
            and self._structure is not None
            and self._recovery_snapshot is None
        )
        self._projects_action.setEnabled(True)
        self._update_continuation_control()
        self._route_active_workspace()

    def _confirm_submission_unknown_host(self, info: HostKeyInfo) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unknown SSH host key")
        box.setText("Trust this host key for the real submission?")
        box.setInformativeText(
            f"Host: {info.hostname}:{info.port}\n"
            f"Algorithm: {info.algorithm}\n"
            f"SHA256: {info.sha256_fingerprint}"
        )
        trust_button = box.addButton(
            "Trust and Save",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is trust_button

    def _show_local_submission_error(self, title: str, error: object) -> None:
        message = str(error) if isinstance(error, Exception) else "Operation failed"
        self._set_operation(f"{title}: {message}")
        QMessageBox.critical(self, title, message)

    def _show_submission_error(self, error: object) -> None:
        request = self._pending_submission_request
        presentation = submission_error_presentation(error, request)
        self._set_operation(
            f"{presentation.title}: {presentation.message}"
        )
        QMessageBox.critical(
            self,
            presentation.title,
            presentation.message,
        )

    def _report_picked_atom(self, atom_index: int) -> None:
        if self._structure is None or atom_index >= len(self._structure):
            return
        self._picked_atom_label.setText(
            f"Selected atom: {_atom_reference(self._structure, atom_index)}"
        )
        if self._pick_mode is _ViewerPickMode.DELETE_ATOM:
            self._delete_picked_atom(atom_index)
            return
        if self._pick_mode is _ViewerPickMode.REPLACE_ATOM:
            self._replace_picked_atom(atom_index)
            return
        if self._pick_mode is _ViewerPickMode.ORCA_WBL_CONTACT:
            if self._structure[atom_index].element not in {"S", "N"}:
                self._set_operation(
                    "ORCA WBL contacts currently support S or N. Select another "
                    "numbered atom or press Esc to cancel."
                )
                return
            self._orca_wbl_contact_selection_result = atom_index
            loop = self._orca_wbl_contact_selection_loop
            if loop is not None:
                self._set_pick_mode(_ViewerPickMode.NORMAL, announce=False)
                loop.quit()
            return
        self._measurement_atom_picked(atom_index)

    def _set_operation(self, message: str) -> None:
        self._operation_label.setText(message)


def _orca_verified_optimization(
    snapshot: ProjectRecoverySnapshot | None,
):
    if (
        snapshot is None
        or snapshot.project.workflow_kind is not CalculationWorkflowKind.ORCA
        or snapshot.optimized_structure is None
    ):
        return None
    optimization = snapshot.project.steps[0]
    evidence = optimization.orca_optimization_result
    if (
        optimization.kind is not ProjectStepKind.ORCA_OPTIMIZATION
        or optimization.state is not ProjectStepState.SUCCEEDED
        or evidence is None
        or not evidence.succeeded
    ):
        return None
    return optimization


def _orca_wbl_calculation_eligible(
    snapshot: ProjectRecoverySnapshot | None,
) -> bool:
    optimization = _orca_verified_optimization(snapshot)
    return bool(
        optimization is not None
        and optimization.orca_optimization_result is not None
        and optimization.orca_optimization_result.wbl_input_ready
        and optimization.orca_optimization_result.gbw_sha256 is not None
        and not any(
            step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
            for step in snapshot.project.steps
        )
    )


def _orca_frequency_calculation_eligible(
    snapshot: ProjectRecoverySnapshot | None,
) -> bool:
    return bool(
        _orca_verified_optimization(snapshot) is not None
        and not any(
            step.kind is ProjectStepKind.ORCA_FREQUENCY
            for step in snapshot.project.steps
        )
    )


def _submission_request_context(
    request: (
        NewProjectSubmissionRequest
        | ExistingProjectStepSubmissionRequest
        | TransportConvergenceSubmissionRequest
        | OrcaOptimizationSubmissionRequest
        | OrcaFrequencySubmissionRequest
        | None
    ),
) -> tuple[UUID | None, ProjectStepKind | None]:
    if isinstance(request, OrcaFrequencySubmissionRequest):
        return request.project.project_id, ProjectStepKind.ORCA_FREQUENCY
    if isinstance(request, OrcaOptimizationSubmissionRequest):
        return request.project_id, ProjectStepKind.ORCA_OPTIMIZATION
    if isinstance(request, ExistingProjectStepSubmissionRequest):
        return request.project.project_id, ProjectStepKind.MOLECULE_AU_OPT
    if isinstance(request, TransportConvergenceSubmissionRequest):
        return (
            request.project.project_id,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
        )
    if isinstance(request, NewProjectSubmissionRequest):
        return None, request.starting_step
    return None, None


def _configure_submission_lifecycle_logging() -> bool:
    """Install one small rotating, secret-free lifecycle log if possible."""

    logger = logging.getLogger(SUBMISSION_LIFECYCLE_LOGGER_NAME)
    if any(
        getattr(handler, "_moltage_submission_lifecycle", False)
        for handler in logger.handlers
    ):
        return True
    try:
        path = submission_lifecycle_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=512 * 1024,
            backupCount=2,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        handler._moltage_submission_lifecycle = True
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    except Exception:
        return False
    return True


def _create_project_submission_dependencies() -> _ProjectSubmissionDependencies:
    """Build local/secure adapters without opening a network connection."""

    secret_store = WindowsCredentialSecretStore()
    profile_repository = ServerProfileRepository(server_profiles_path())
    profile_service = ServerProfileService(profile_repository, secret_store)
    known_hosts = KnownHostStore(known_hosts_path())
    connection_service = ServerConnectionService(
        secret_store,
        lambda: ParamikoRemoteExecutor(known_hosts),
    )
    local_index_repository = LocalProjectIndexRepository(
        local_project_index_path()
    )
    submission_service = ProjectSubmissionService(
        connection_service,
        local_index_repository,
        server_profile_repository=profile_repository,
    )
    recovery_service = ProjectRecoveryService(
        connection_service,
        local_index_repository,
    )
    transport_submission_service = TransportWorkflowSubmissionService(
        connection_service,
        local_index_repository,
    )
    project_management_service = ProjectManagementService(
        connection_service,
        local_index_repository,
    )
    project_geometry_service = ProjectGeometryViewService(connection_service)
    project_orbital_cube_service = ProjectOrbitalCubeService(connection_service)
    project_task_restart_service = ProjectTaskRestartService(connection_service)
    input_export_service = AimsInputExportService(
        connection_service,
        profile_repository,
    )
    orca_submission_service = OrcaSubmissionService(
        connection_service,
        local_index_repository,
    )
    orca_recovery_service = OrcaRecoveryService(
        connection_service,
        local_index_repository,
    )
    orca_wbl_service = OrcaWblService(
        connection_service,
        local_index_repository,
    )
    return _ProjectSubmissionDependencies(
        profile_repository,
        profile_service,
        secret_store,
        known_hosts,
        submission_service,
        recovery_service,
        connection_service,
        transport_submission_service,
        project_management_service,
        project_geometry_service,
        project_task_restart_service,
        project_orbital_cube_service,
        input_export_service,
        orca_submission_service,
        orca_recovery_service,
        orca_wbl_service,
    )


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _format_file_size(value: int) -> str:
    numeric = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if numeric < 1024.0 or unit == "GiB":
            return (
                f"{numeric:.0f} {unit}"
                if unit == "B"
                else f"{numeric:.1f} {unit}"
            )
        numeric /= 1024.0
    raise AssertionError("unreachable file-size unit")


def _structure_retains_recovered_prefix(
    current: MolecularStructure | None,
    recovered: MolecularStructure,
    applied_contact: AppliedAuPlacement | None = None,
    applied_electrodes: AppliedElectrodePlacement | None = None,
) -> bool:
    """Accept only the recovered molecule or its traceable Au-only derivatives."""

    if current is None:
        return False
    if current == recovered:
        return True

    contact_structure = None
    if (
        applied_contact is not None
        and _applied_contact_retains_recovered_atoms(applied_contact, recovered)
    ):
        contact_structure = applied_contact.structure
        if current == contact_structure:
            return True

    if applied_electrodes is None or current != applied_electrodes.structure:
        return False
    electrode_source = applied_electrodes.proposal.source_structure
    return electrode_source == recovered or (
        contact_structure is not None and electrode_source == contact_structure
    )


def _applied_contact_retains_recovered_atoms(
    applied: AppliedAuPlacement,
    recovered: MolecularStructure,
) -> bool:
    """Verify the explicit source-to-result map, including terminal-H removal."""

    if len(applied.old_to_new_indices) != len(recovered):
        return False
    for source_index, result_index in enumerate(applied.old_to_new_indices):
        source_atom = recovered[source_index]
        if result_index is None:
            if source_atom.element != "H":
                return False
            continue
        result_atom = applied.structure[result_index]
        if (
            source_atom.element != result_atom.element
            or source_atom.x != result_atom.x
            or source_atom.y != result_atom.y
            or source_atom.z != result_atom.z
        ):
            return False
    return True


def _add_swatch_legend(
    layout: QHBoxLayout,
    color: tuple[int, int, int],
    text: str,
) -> None:
    swatch = QFrame()
    swatch.setFixedSize(12, 12)
    swatch.setStyleSheet(
        f"background-color: rgb({color[0]}, {color[1]}, {color[2]}); "
        "border: 1px solid #555;"
    )
    layout.addWidget(swatch)
    layout.addWidget(QLabel(text))


def _anchor_site_label(
    structure: MolecularStructure,
    anchor: AnchorCandidate,
) -> str:
    linker = _atom_reference(structure, anchor.binding_atom_index)
    group = " ".join(
        _atom_reference(structure, atom_index)
        for atom_index in anchor.atom_indices
    )
    if anchor.attached_au_indices:
        state = "existing Au: " + " ".join(
            _atom_reference(structure, atom_index)
            for atom_index in anchor.attached_au_indices
        )
    else:
        state = "available"
    return f"{anchor.kind.value} | linker {linker}\ngroup: {group} | {state}"


def _format_proposals(
    structure: MolecularStructure | None,
    connectivity: Connectivity | None,
    proposals: tuple[AuPlacementProposal, ...],
) -> str:
    if structure is None or connectivity is None:
        raise ValueError("molecular geometry is not loaded")
    sections = []
    for proposal in proposals:
        anchor = proposal.anchor
        binding = _atom_coordinates(structure, anchor.binding_atom_index)
        proposed = (proposal.x, proposal.y, proposal.z)
        geometry = (
            f"{_distance_report_label(anchor.kind)}="
            f"{math.dist(binding, proposed):.2f} Å"
        )
        reference_indices = angle_reference_atom_indices(
            structure,
            connectivity,
            anchor,
        )
        if reference_indices:
            angles = tuple(
                _angle_degrees(
                    _subtract(
                        _atom_coordinates(structure, reference_index),
                        binding,
                    ),
                    _subtract(proposed, binding),
                )
                for reference_index in reference_indices
            )
            geometry += (
                f" | {ANGLE_PARAMETER_LABELS[anchor.kind]}="
                + "/".join(f"{angle:.1f}°" for angle in angles)
            )
        removal = ""
        if proposal.remove_atom_indices:
            removal = " | preview replacement (removed on Done): " + ", ".join(
                _atom_reference(structure, atom_index)
                for atom_index in proposal.remove_atom_indices
            )
        sections.append(
            f"{anchor.kind.value} | linker "
            f"{_atom_reference(structure, anchor.binding_atom_index)} | "
            f"Au=({proposal.x:.6f}, {proposal.y:.6f}, {proposal.z:.6f}) | "
            f"{geometry}{removal}"
        )
    return " ; ".join(sections)


def _format_applied_result(
    source: MolecularStructure,
    result: AppliedAuPlacement,
) -> str:
    removed = ", ".join(
        f"source {_atom_reference(source, atom_index)}"
        for atom_index in result.removed_atom_indices
    ) or "none"
    added = ", ".join(
        _atom_reference(result.structure, atom_index)
        for atom_index in result.added_au_indices
    )
    return (
        f"Applied {len(result.added_au_indices)} Au contacts | "
        f"Removed source atoms: {removed} | Added Au atoms: {added} | "
        f"Working atoms: {len(result.structure)}"
    )


def _distance_report_label(anchor_kind: AnchorKind) -> str:
    if anchor_kind in {AnchorKind.ALKYNYL_C, AnchorKind.DICYANO_C}:
        return "Au-C"
    if anchor_kind in {
        AnchorKind.NH2,
        AnchorKind.PYRIDINE_N,
        AnchorKind.CYANO_N,
    }:
        return "Au-N"
    return "Au-S"


def _required_float(field: QLineEdit, label: str) -> float:
    text = field.text().strip()
    if not text:
        raise ValueError(f"{label} is required for this selected site")
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"{label} must be numeric") from error


def _atom_coordinates(
    structure: MolecularStructure,
    atom_index: int,
) -> tuple[float, float, float]:
    atom = structure[atom_index]
    return atom.x, atom.y, atom.z


def _atom_reference(structure: MolecularStructure, atom_index: int) -> str:
    return f"{structure[atom_index].element}{atom_index}"


def _angle_degrees(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    denominator = math.hypot(*first) * math.hypot(*second)
    if denominator <= 1.0e-12:
        raise ValueError("cannot report an angle from a zero-length ray")
    cosine = sum(
        first_component * second_component
        for first_component, second_component in zip(first, second, strict=True)
    ) / denominator
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _subtract(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        first_component - second_component
        for first_component, second_component in zip(first, second, strict=True)
    )


def _measurement_overlay(
    structure: MolecularStructure,
    measurement: MeasurementRecord,
) -> MeasurementOverlayAnnotation:
    if isinstance(measurement, DistanceMeasurement):
        annotation: DistanceAnnotation | AngleAnnotation = DistanceAnnotation(
            _atom_coordinates(structure, measurement.atom_a),
            _atom_coordinates(structure, measurement.atom_b),
        )
    elif isinstance(measurement, AngleMeasurement):
        annotation = AngleAnnotation(
            _atom_coordinates(structure, measurement.atom_b),
            _atom_coordinates(structure, measurement.atom_a),
            _atom_coordinates(structure, measurement.atom_c),
        )
    else:
        raise TypeError("unsupported manual measurement record")
    return MeasurementOverlayAnnotation(
        measurement.measurement_id,
        annotation,
    )


def _viewer_icon(filename: str) -> QIcon:
    return themed_icon(filename)


def _image_export_basename(display_title: str) -> str:
    """Return a Windows-safe, human-readable default image basename."""

    safe = "".join(
        character
        if character.isalnum() or character in {"-", "_", "."}
        else "_"
        for character in display_title.strip()
    ).strip(" ._")
    return f"{safe or 'current_view'}_view"


def main() -> None:
    try:
        migrate_legacy_application_data()
    except ApplicationDataPathError as error:
        application = QApplication(sys.argv)
        QMessageBox.critical(None, "Unable to migrate Moltage settings", str(error))
        raise SystemExit(1) from error
    _configure_submission_lifecycle_logging()
    application = QApplication(sys.argv)
    apply_default_theme(application)
    application.setWindowIcon(_viewer_icon("moltage.ico"))
    try:
        view_preferences_repository = UserViewPreferencesRepository(
            user_view_preferences_path()
        )
        saved_theme_id = view_preferences_repository.load_theme_id()
        if saved_theme_id is not None:
            try:
                DEFAULT_THEME_MANAGER.apply(application, saved_theme_id)
            except ValueError as error:
                raise UserViewPreferencesError(
                    f"saved UI theme is unavailable: {saved_theme_id}"
                ) from error
        window = MoleculeViewerDemo(view_preferences_repository)
    except (ApplicationDataPathError, UserViewPreferencesError) as error:
        QMessageBox.critical(
            None,
            "Unable to load View settings",
            str(error),
        )
        raise SystemExit(1) from error
    except PlacementDefaultsError as error:
        QMessageBox.critical(
            None,
            "Unable to start Electrode Builder",
            str(error),
        )
        raise SystemExit(1) from error
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
