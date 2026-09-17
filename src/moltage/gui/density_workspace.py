"""Dedicated density selection, one-task monitoring and read-only result workspace."""

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QComboBox, QLineEdit, QPushButton, QLabel, QCheckBox, QDoubleSpinBox, QSpinBox,
    QMessageBox, QInputDialog, QMenu, QRubberBand, QSplitter, QSlider, QFileDialog,
    QTableWidget, QTableWidgetItem, QGroupBox, QStackedWidget, QProgressBar,
    QButtonGroup, QRadioButton)

from moltage.aims.density_difference import DensitySettings, DensityElectronicState
from moltage.aims.optimization_settings import XCFunctional, SpeciesAccuracy, SpinSettings, SpinInitializationMode
from moltage.app.density_workflow import (
    DensityCancellationOutcome,
    DensityCancellationResult,
    DensityRecoveryPhase,
    DensityRecoveryProgress,
    DensityTask,
)
from moltage.app.density_results import (
    density_display_dimensions,
    export_density_result,
    interpolate_density_fields,
)
from moltage.domain.density_difference import FragmentPartition, DensityGrid, parse_atom_selection, format_atom_selection, COMPONENTS
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.server_profile import (
    runtime_hours_from_minutes,
    runtime_minutes_from_hours,
)
from moltage.structure.covalent_radii import load_default_covalent_radii
from moltage.structure.connectivity import infer_connectivity
from moltage.visualization.molecule_viewer import MoleculeViewerWidget
from moltage.visualization.view_preferences import ViewPreferences
from moltage.visualization.orbital_surface import (
    OrbitalSurfacePreferences,
    OrbitalSurfaceResolution,
)
from moltage.gui.view_settings_dialog import ViewSettingsDialog
from moltage.gui.status_refresh import StatusRefreshSession, StatusRefreshTimeout


class _Signals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)
    progress = Signal(object)


class DensityWorker(QRunnable):
    """One explicit operation. No timer polling and no automatic remote retry."""
    def __init__(self, function, *, with_progress=False):
        super().__init__()
        self.signals = _Signals()
        self.function = function
        self.with_progress = with_progress
        self.setAutoDelete(False)

    @Slot()
    def run(self):
        try:
            result = (
                self.function(self.signals.progress.emit)
                if self.with_progress
                else self.function()
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.function = None
            self.signals.finished.emit(self)


class FragmentViewer(MoleculeViewerWidget):
    rectangle_picked = Signal(object)

    def __init__(self, parent=None):
        self.rectangle_mode = False
        self.selection_structure = None
        self._rectangle_start = None
        super().__init__(parent)
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self._vtk_widget)
        self._rubber_band.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._rubber_band.setStyleSheet(
            "QRubberBand { background-color: transparent; "
            "border: 1px dashed #111111; }"
        )

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_vtk_widget", None) and self.rectangle_mode:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._clear_hover_target()
                self._rectangle_start = event.position().toPoint()
                self._rubber_band.setGeometry(QRect(self._rectangle_start, self._rectangle_start))
                self._rubber_band.show()
                return True
            if self._rectangle_start is not None and event.type() == QEvent.Type.MouseMove:
                self._rubber_band.setGeometry(QRect(self._rectangle_start, event.position().toPoint()).normalized())
                return True
            if self._rectangle_start is not None and event.type() == QEvent.Type.MouseButtonRelease:
                rectangle = QRect(self._rectangle_start, event.position().toPoint()).normalized()
                self._rectangle_start = None
                self._rubber_band.hide()
                self.rectangle_picked.emit(self.atoms_in_rectangle(rectangle))
                return True
        return super().eventFilter(watched, event)

    def atoms_in_rectangle(self, rectangle):
        if self.selection_structure is None:
            return ()
        width, height = self._vtk_widget.GetRenderWindow().GetSize()
        if not width or not height: return ()
        indexes = []
        for atom in self.selection_structure:
            self._renderer.SetWorldPoint(atom.x, atom.y, atom.z, 1)
            self._renderer.WorldToDisplay()
            x, y, z = self._renderer.GetDisplayPoint()
            if 0 <= z <= 1 and rectangle.contains(QPoint(round(x * self._vtk_widget.width() / width), round((height - 1 - y) * self._vtk_widget.height() / height))):
                indexes.append(atom.index)
        return tuple(indexes)

    def display_field(self, field, preferences):
        self._scene.set_orbital_surface(field, preferences if field is not None else None)
        self._renderer.ResetCameraClippingRange()
        self._vtk_widget.GetRenderWindow().Render()


def _real(value, *, minimum=0, maximum=1e9, decimals=8, step=0.1):
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setSingleStep(step)
    widget.setValue(value)
    widget.setKeyboardTracking(False)
    return widget


def _integer(value, maximum=200001):
    widget = QSpinBox()
    widget.setRange(1, maximum)
    widget.setValue(value)
    return widget


class DensityComponentLamp(QWidget):
    """One ordinary-size status lamp for a component in the shared job."""

    cancel_requested = Signal()

    def __init__(self, number, label, parent=None):
        super().__init__(parent)
        self.number = number
        self.label = label
        self.state = "NOT_STARTED"
        self.cancellation_enabled = False
        self.setFixedSize(18, 18)
        self.setObjectName(f"densityComponentLamp{number}")
        self._update_tooltip()

    def set_state(self, state, *, cancellation_enabled=False):
        self.state = state
        self.cancellation_enabled = cancellation_enabled
        self._update_tooltip()
        self.update()

    def _update_tooltip(self):
        suffix = (
            " Right-click to cancel the shared density scheduler job."
            if self.cancellation_enabled
            else ""
        )
        self.setToolTip(f"{self.label}: {self.state}.{suffix}")

    def context_menu(self):
        menu = QMenu(self)
        action = menu.addAction("Cancel Density Job...")
        action.setObjectName("cancelDensityJob")
        action.setEnabled(self.cancellation_enabled)
        if self.cancellation_enabled:
            action.triggered.connect(self.cancel_requested)
        return menu

    def contextMenuEvent(self, event):
        self.context_menu().exec(event.globalPos())
        event.accept()

    def paintEvent(self, _event):
        color = QColor(
            {
                "COMPLETE": "#2e7d32",
                "FAILED": "#c62828",
                "CANCELLED": "#616161",
                "NOT_STARTED": "#757575",
            }.get(self.state, "#fbc02d")
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(
            Qt.BrushStyle.NoBrush
            if self.state == "NOT_STARTED"
            else color
        )
        rectangle = self.rect().adjusted(1, 1, -1, -1)
        painter.drawEllipse(rectangle)
        painter.setPen(
            QColor("#202020")
            if self.state not in {"COMPLETE", "FAILED", "CANCELLED"}
            else QColor("#ffffff")
        )
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, str(self.number))


class FragmentSelectionEdit(QLineEdit):
    """A fragment field that makes its subset active as soon as it is used."""

    activated = Signal()

    def focusInEvent(self, event):
        self.activated.emit()
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self.activated.emit()
        super().mousePressEvent(event)


@dataclass(frozen=True, slots=True)
class DensityResultViewRequest:
    """Validated recovered result handed from task controls to presentation."""

    task: DensityTask
    result: object

    @property
    def identity(self):
        return self.task.task_id, self.task.attempt["number"]

    @property
    def display_title(self):
        return f"{self.task.name} — Density Difference"


class DensityWorkspace(QWidget):
    task_updated = Signal(object)
    result_view_requested = Signal(object)
    operation_state_changed = Signal()

    def __init__(self, structure, profiles, service, secret_store, parent=None, *, task=None,
                 view_preferences=None, orbital_preferences=None):
        super().__init__(parent)
        self.structure = structure
        self.service = service
        self.secret_store = secret_store
        self.task = task
        self.busy = False
        self._workers = set()
        self._status_refresh: StatusRefreshSession | None = None
        self._initial_fit = False
        self._selection = [set(), set()]
        self._syncing_selection_fields = False
        self._loading_task = False
        self._preserved_grid = None
        self._cancel_suppressed_attempts = set()
        self._view_preferences = view_preferences or ViewPreferences()
        layout = QVBoxLayout(self)
        split = QSplitter(self)
        layout.addWidget(split, 1)
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setMaximumWidth(440)
        split.addWidget(self.settings_tabs)
        self.viewer = FragmentViewer(self)
        self.viewer.selection_structure = structure
        radii = load_default_covalent_radii()
        self.viewer.set_molecule(structure, infer_connectivity(structure, radii), radii)
        self.viewer.set_view_preferences(self._view_preferences)
        self.viewer.atom_picked.connect(self._point_picked)
        self.viewer.rectangle_picked.connect(self.assign_atoms)
        split.addWidget(self.viewer)
        split.setStretchFactor(1, 1)
        self._build_selection()
        self._build_science()
        self._build_submission(tuple(profiles))
        row = QHBoxLayout()
        self.submit_button = QPushButton("Submit")
        self.refresh_button = QPushButton("Refresh Status")
        self.recover_button = QPushButton("Recover Results")
        self.retry_button = QPushButton("Retry Failed Components")
        for button, callback in ((self.submit_button, self._submit), (self.refresh_button, lambda: self._remote("refresh")),
                                 (self.recover_button, lambda: self._remote("recover")), (self.retry_button, lambda: self._remote("retry"))):
            row.addWidget(button)
            button.clicked.connect(callback)
            button.setAutoDefault(False)
        row.addStretch()
        component_label = QLabel("电子密度差计算")
        component_label.setObjectName("densityTaskLabel")
        row.addWidget(component_label)
        names = ("Total", "Subset 1", "Subset 2")
        self.component_lamps = []
        for number, name in enumerate(names, 1):
            lamp = DensityComponentLamp(number, name, self)
            lamp.cancel_requested.connect(self._request_cancel)
            row.addWidget(lamp)
            self.component_lamps.append(lamp)
        layout.addLayout(row)
        self.status = QLabel("Select two non-overlapping fragments covering all atoms. Source coordinates are unchanged.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.recovery_progress_panel = QWidget(self)
        self.recovery_progress_panel.setObjectName("densityRecoveryProgressPanel")
        recovery_progress_layout = QVBoxLayout(self.recovery_progress_panel)
        recovery_progress_layout.setContentsMargins(0, 0, 0, 0)
        self.recovery_progress_label = QLabel(
            "Preparing result recovery…",
            self.recovery_progress_panel,
        )
        self.recovery_progress_label.setObjectName("densityRecoveryProgressLabel")
        self.recovery_progress_label.setWordWrap(True)
        recovery_progress_layout.addWidget(self.recovery_progress_label)
        self.recovery_progress_bar = QProgressBar(self.recovery_progress_panel)
        self.recovery_progress_bar.setObjectName("densityRecoveryProgressBar")
        self.recovery_progress_bar.setRange(0, 0)
        self.recovery_progress_bar.setTextVisible(False)
        recovery_progress_layout.addWidget(self.recovery_progress_bar)
        self.recovery_progress_panel.hide()
        layout.addWidget(self.recovery_progress_panel)
        if task is not None:
            self.load_task(task)
        self._update_controls()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_fit:
            self._initial_fit = True
            # Fit once after the new tab's real viewport size is known. Selection,
            # result updates and later tab switches never reset the camera.
            QTimer.singleShot(0, self.viewer, self.viewer.reset_camera)

    def _page(self, title):
        page = QWidget()
        self.settings_tabs.addTab(page, title)
        return page

    def _build_selection(self):
        page = self._page("Fragments")
        form = QFormLayout(page)
        self.active_subset = QComboBox()
        self.active_subset.addItems(("Subset 1", "Subset 2"))
        form.addRow("Active fragment", self.active_subset)
        self.pick_mode = QComboBox()
        self.pick_mode.addItems(("Point selection", "Rectangle selection", "Rotate view"))
        self.pick_mode.currentIndexChanged.connect(lambda index: setattr(self.viewer, "rectangle_mode", index == 1))
        form.addRow("Mouse", self.pick_mode)
        self.remove = QCheckBox("Remove from active fragment")
        form.addRow(self.remove)
        self.index_fields = []
        self.count_labels = []
        for index in range(2):
            edit = FragmentSelectionEdit()
            edit.setPlaceholderText("1-20,25,31-40")
            edit.activated.connect(
                lambda n=index: self.active_subset.setCurrentIndex(n)
            )
            edit.textChanged.connect(
                lambda text, n=index: self._selection_text_changed(n, text)
            )
            form.addRow(f"Subset {index + 1}", edit)
            count = QLabel("0 atoms")
            form.addRow(count)
            self.index_fields.append(edit)
            self.count_labels.append(count)
        remaining = QPushButton("Add all remaining atoms")
        remaining.clicked.connect(lambda: self.assign_atoms(set(range(len(self.structure))) - self._selection[0] - self._selection[1], remove=False))
        form.addRow(remaining)
        hint = QLabel("Point/rectangle selection is continuous. Assigning an atom transfers it from the other fragment. Rectangle selects projected atom centers, including hidden atoms. Use right/middle drag or Rotate view to move the camera.")
        hint.setWordWrap(True)
        form.addRow(hint)

    def _build_science(self):
        self.electronic_page = self._page("States")
        form = QFormLayout(self.electronic_page)
        self.xc = QComboBox()
        for item in XCFunctional: self.xc.addItem(item.value, item)
        self.species = QComboBox()
        for item in SpeciesAccuracy: self.species.addItem(item.value, item)
        self.species.setCurrentIndex(self.species.findData(SpeciesAccuracy.TIGHT))
        form.addRow("XC (all three)", self.xc)
        form.addRow("Species (all three)", self.species)
        self.charge_fields, self.spin_fields, self.moment_fields = [], [], []
        for name in COMPONENTS:
            row = QHBoxLayout()
            charge = _real(0, minimum=-100000, decimals=4)
            charge.setToolTip("Net charge in electron-charge units; total = subset1 + subset2")
            spin = QCheckBox("Spin")
            moment = _real(1, minimum=-10000, decimals=4)
            moment.setToolTip("Initial moment per atom for collinear SCF; nonzero. Not a constrained final spin.")
            moment.setEnabled(False)
            spin.toggled.connect(moment.setEnabled)
            row.addWidget(charge)
            row.addWidget(spin)
            row.addWidget(moment)
            form.addRow(name + " charge/spin", row)
            self.charge_fields.append(charge)
            self.spin_fields.append(spin)
            self.moment_fields.append(moment)
        self.derive_charge = QCheckBox("Derive Subset 2 charge = Total − Subset 1")
        self.derive_charge.setChecked(True)
        self.derive_charge.toggled.connect(self._sync_charge)
        for field in self.charge_fields[:2]: field.valueChanged.connect(self._sync_charge)
        form.addRow(self.derive_charge)
        self._sync_charge()
        hint = QLabel("Fragment charge and spin define the independent reference. Cutting bonds may create open-shell fragments; neutral/non-spin defaults are not a physical-state determination.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.science_page = self._page("SCF & Grid")
        form = QFormLayout(self.science_page)
        self.scf_fields = {}
        defaults = DensitySettings()
        for name in ("occupation_width", "n_max_pulay", "charge_mix_param", "sc_accuracy_rho", "sc_accuracy_eev", "sc_accuracy_etot", "sc_iter_limit"):
            value = getattr(defaults, name)
            field = _integer(value) if isinstance(value, int) else _real(value, minimum=1e-10, decimals=10)
            self.scf_fields[name] = field
            form.addRow(name, field)
        self.spacing = _real(0.1, minimum=0.0001, decimals=4, step=0.01)
        self.padding = _real(14 * 0.529177210903, decimals=6)
        form.addRow("Grid spacing (Å)", self.spacing)
        form.addRow("Boundary padding (Å)", self.padding)
        self.grid_summary = QLabel()
        self.grid_summary.setWordWrap(True)
        form.addRow(self.grid_summary)
        for field in (self.spacing, self.padding):
            field.valueChanged.connect(self._grid_parameter_changed)
        self._grid_summary()

    def _build_submission(self, profiles):
        page = self._page("Server & Resources")
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.server = QComboBox()
        for profile in profiles: self.server.addItem(profile.name, profile)
        form.addRow("Server:", self.server)
        self.name = QLineEdit("Density")
        form.addRow("Task name:", self.name)
        layout.addLayout(form)

        self.scheduler_resource_pages = QStackedWidget(page)
        self.scheduler_resource_pages.setObjectName("densitySchedulerResourcePages")

        slurm_resources = QGroupBox("Slurm resources", page)
        slurm_form = QFormLayout(slurm_resources)
        self.resource_fields = {}
        labels = {
            "nodes": "Nodes:",
            "ntasks": "MPI tasks:",
            "cpus_per_task": "CPUs per task:",
            "memory_gb": "Memory limit per node:",
        }
        object_names = {
            "nodes": "densityNodes",
            "ntasks": "densityMpiTasks",
            "cpus_per_task": "densityCpusPerTask",
            "memory_gb": "densityMemoryLimitGb",
        }
        for name in ("nodes", "ntasks", "cpus_per_task", "memory_gb"):
            field = _integer(1)
            field.setObjectName(object_names[name])
            if name == "memory_gb":
                field.setSuffix(" GB")
            slurm_form.addRow(labels[name], field)
            self.resource_fields[name] = field
        self.runtime_hours = QDoubleSpinBox(slurm_resources)
        self.runtime_hours.setObjectName("densityMaximumRuntimeHours")
        self.runtime_hours.setRange(0.1, 1_000_000.0)
        self.runtime_hours.setDecimals(1)
        self.runtime_hours.setSingleStep(0.1)
        self.runtime_hours.setSuffix(" hours")
        slurm_form.insertRow(3, "Maximum runtime:", self.runtime_hours)
        self.scheduler_resource_pages.addWidget(slurm_resources)

        lsf_resources = QGroupBox("LSF resources", page)
        lsf_form = QFormLayout(lsf_resources)
        self.lsf_resource_fields = {}
        lsf_labels = {
            "nodes": "Execution hosts:",
            "ntasks": "MPI job slots / ranks:",
            "memory_gb": "Memory reservation (LSF rusage):",
        }
        lsf_object_names = {
            "nodes": "densityLsfExecutionHosts",
            "ntasks": "densityLsfMpiJobSlots",
            "memory_gb": "densityLsfMemoryReservationGb",
        }
        for name in ("nodes", "ntasks", "memory_gb"):
            field = _integer(1)
            field.setObjectName(lsf_object_names[name])
            if name == "memory_gb":
                field.setSuffix(" GB")
            lsf_form.addRow(lsf_labels[name], field)
            self.lsf_resource_fields[name] = field
        self.lsf_runtime_hours = QDoubleSpinBox(lsf_resources)
        self.lsf_runtime_hours.setObjectName("densityLsfMaximumRuntimeHours")
        self.lsf_runtime_hours.setRange(0.1, 1_000_000.0)
        self.lsf_runtime_hours.setDecimals(1)
        self.lsf_runtime_hours.setSingleStep(0.1)
        self.lsf_runtime_hours.setSuffix(" hours")
        lsf_form.insertRow(2, "Maximum runtime:", self.lsf_runtime_hours)
        lsf_model = QLabel(
            "Pure MPI: one CPU and one OpenMP thread per rank. The three SCFs "
            "run sequentially inside one LSF job.",
            lsf_resources,
        )
        lsf_model.setObjectName("densityLsfExecutionModel")
        lsf_model.setWordWrap(True)
        lsf_form.addRow("Execution model:", lsf_model)
        self.scheduler_resource_pages.addWidget(lsf_resources)
        layout.addWidget(self.scheduler_resource_pages)

        self.resource_hint = QLabel()
        self.resource_hint.setWordWrap(True)
        layout.addWidget(self.resource_hint)
        layout.addStretch(1)
        self.server.currentIndexChanged.connect(self._server_changed)
        self._server_changed()

    def _server_changed(self):
        profile = self.server.currentData()
        preset = profile.execution_preset if profile is not None else None
        scheduler_kind = (
            preset.scheduler_kind if preset is not None else SchedulerKind.SLURM
        )
        self.scheduler_resource_pages.setCurrentIndex(
            0 if scheduler_kind is SchedulerKind.SLURM else 1
        )
        if preset is not None:
            fields = (
                self.resource_fields
                if scheduler_kind is SchedulerKind.SLURM
                else self.lsf_resource_fields
            )
            for name, field in fields.items():
                field.setValue(getattr(preset, name))
            runtime = (
                self.runtime_hours
                if scheduler_kind is SchedulerKind.SLURM
                else self.lsf_runtime_hours
            )
            runtime.setValue(runtime_hours_from_minutes(preset.runtime_minutes))
            scheduler_name = scheduler_display_name(scheduler_kind)
            memory_meaning = (
                "memory is a per-node execution limit"
                if scheduler_kind is SchedulerKind.SLURM
                else (
                    "memory is an LSF rusage reservation whose scope follows "
                    "cluster configuration, not a hard limit"
                )
            )
            self.resource_hint.setText(
                f"One {scheduler_name} job; walltime covers all three sequential "
                f"SCFs. Resources are user-supplied; {memory_meaning}. "
                f"{scheduler_name} native email follows the selected server policy."
            )
        else:
            self.resource_hint.setText(
                "Configure this server's runtime and scheduler resources before submission."
            )
        self._update_resource_editability()

    def _resource_values(self):
        profile = self.server.currentData()
        preset = profile.execution_preset if profile is not None else None
        if preset is None:
            raise ValueError("Select a server with configured runtime and resources")
        if preset.scheduler_kind is SchedulerKind.LSF:
            return {
                **{
                    name: field.value()
                    for name, field in self.lsf_resource_fields.items()
                },
                "cpus_per_task": 1,
                "omp_num_threads": 1,
                "unset_slurm_export_env": False,
                "runtime_minutes": runtime_minutes_from_hours(
                    self.lsf_runtime_hours.value()
                ),
            }
        return {
            **{name: field.value() for name, field in self.resource_fields.items()},
            "runtime_minutes": runtime_minutes_from_hours(
                self.runtime_hours.value()
            ),
        }

    def _update_resource_editability(self):
        profile = self.server.currentData()
        preset = profile.execution_preset if profile is not None else None
        editable = self._parameters_editable()
        is_slurm = preset is not None and preset.scheduler_kind is SchedulerKind.SLURM
        is_lsf = preset is not None and preset.scheduler_kind is SchedulerKind.LSF
        for field in self.resource_fields.values():
            field.setEnabled(editable and is_slurm)
        self.runtime_hours.setEnabled(editable and is_slurm)
        for field in self.lsf_resource_fields.values():
            field.setEnabled(editable and is_lsf)
        self.lsf_runtime_hours.setEnabled(editable and is_lsf)

    def _sync_charge(self):
        derive = self.derive_charge.isChecked()
        self.charge_fields[2].setEnabled(not derive)
        if derive: self.charge_fields[2].setValue(self.charge_fields[0].value() - self.charge_fields[1].value())

    def _grid_summary(self):
        grid = self._preserved_grid or DensityGrid.around(
            self.structure, self.spacing.value(), self.padding.value()
        )
        prefix = "Preserved common grid" if self._preserved_grid else "Common grid"
        self.grid_summary.setText(f"{prefix}: {grid.dimensions}; {grid.point_count:,} points. Three Cube files ≈ {grid.point_count * 16 * 3 / 1e6:.1f} MB (text estimate). Grid convergence is not automatic.")

    def _grid_parameter_changed(self):
        if not self._loading_task:
            self._preserved_grid = None
        self._grid_summary()

    def _parameters_editable(self):
        return not self.busy and (
            self.task is None or self.task.state in {"FAILED", "CANCELLED"}
        )

    def assign_atoms(self, indexes, remove=None):
        if not self._parameters_editable(): return
        active = self.active_subset.currentIndex()
        selected = set(indexes)
        if any(type(i) is not int or not 0 <= i < len(self.structure) for i in selected):
            raise ValueError("Invalid selected atom")
        remove = self.remove.isChecked() if remove is None else remove
        if remove:
            self._selection[active] -= selected
        else:
            self._selection[active] |= selected
            self._selection[1 - active] -= selected
        self._sync_selection()

    def _point_picked(self, index):
        if self.pick_mode.currentIndex() != 0:
            return
        active = self.active_subset.currentIndex()
        self.assign_atoms(
            (index,),
            remove=self.remove.isChecked() or index in self._selection[active],
        )

    def _selection_text_changed(self, index, text):
        if self._syncing_selection_fields or not self._parameters_editable():
            return
        try:
            selected = set(parse_atom_selection(text, len(self.structure)))
        except ValueError as error:
            self.index_fields[index].setToolTip(str(error))
            return
        self.index_fields[index].setToolTip("")
        self._selection[index] = selected
        self._selection[1 - index] -= selected
        self._sync_selection()

    def _sync_selection(self):
        self._syncing_selection_fields = True
        try:
            for index in range(2):
                self.index_fields[index].setText(
                    format_atom_selection(self._selection[index])
                )
                self.index_fields[index].setToolTip("")
                self.count_labels[index].setText(
                    f"{len(self._selection[index])} atoms"
                )
        finally:
            self._syncing_selection_fields = False
        self.viewer.set_grouped_atom_indices(*self._selection)

    def scientific_inputs(self, partition=None):
        # Commit text fields without requiring a focus change before Submit.
        if partition is None:
            first = parse_atom_selection(
                self.index_fields[0].text(), len(self.structure)
            )
            second = parse_atom_selection(
                self.index_fields[1].text(), len(self.structure)
            )
            partition = FragmentPartition(self.structure, first, second)
        states = []
        for charge, spin, moment in zip(self.charge_fields, self.spin_fields, self.moment_fields):
            states.append(DensityElectronicState(charge.value(), SpinSettings(True, SpinInitializationMode.UNIFORM_DEFAULT, moment.value()) if spin.isChecked() else SpinSettings()))
        settings = DensitySettings(*states, xc=self.xc.currentData(), species_accuracy=self.species.currentData(),
                                   **{name: field.value() for name, field in self.scf_fields.items()})
        grid = self._preserved_grid or DensityGrid.around(
            self.structure, self.spacing.value(), self.padding.value()
        )
        return partition, settings, grid

    def _submission_partition(self):
        first = set(
            parse_atom_selection(
                self.index_fields[0].text(), len(self.structure)
            )
        )
        second = set(
            parse_atom_selection(
                self.index_fields[1].text(), len(self.structure)
            )
        )
        missing = set(range(len(self.structure))) - first - second
        if missing:
            atom_numbers = format_atom_selection(missing)
            message = (
                "The following atom numbers have not been assigned to Subset 1 "
                f"or Subset 2:\n\n{atom_numbers}\n\n"
                "Assign every atom before submission."
            )
            self.status.setText(message)
            QMessageBox.warning(self, "Atoms not assigned", message)
            return None
        return FragmentPartition(
            self.structure,
            tuple(sorted(first)),
            tuple(sorted(second)),
        )

    def _profile_password(self):
        profile = self.server.currentData()
        if profile is None or profile.execution_preset is None:
            raise ValueError("Select a server with configured runtime and resources")
        profile = replace(
            profile,
            execution_preset=replace(
                profile.execution_preset, **self._resource_values()
            ),
        )
        if profile.save_password and self.secret_store.get_password(profile.profile_id): return profile, None
        password, accepted = QInputDialog.getText(self, "Password required", f"Password for {profile.username}@{profile.host}", QLineEdit.EchoMode.Password)
        return (profile, password) if accepted and password else (None, None)

    def _submit(self):
        try:
            partition = self._submission_partition()
            if partition is None:
                return
            partition, settings, grid = self.scientific_inputs(partition)
            profile, password = self._profile_password()
            if profile is None: return
            name = self.name.text().strip()
            self._run(lambda: self.service.submit(profile, name, partition, settings, grid, password))
        except Exception as error: self._error(error)

    def _remote(self, operation):
        if operation == "refresh" and self._status_refresh is not None:
            self._status_refresh.request_stop()
            return
        if self.busy:
            return
        if self.task is None: return
        scheduler_name = self._selected_scheduler_name()
        if operation == "retry" and QMessageBox.question(
            self,
            "Retry density task",
            f"Submit one new {scheduler_name} job for unvalidated components "
            "using the original scientific input files? Earlier inputs/results "
            "are preserved. Current server resource values are used; fragment, "
            "state, SCF and grid edits are not.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            profile, password = self._profile_password()
            if profile is None: return
            path = self.task.remote_path
            if operation == "refresh":
                self._run_status_refresh(profile, path, password)
            elif operation == "recover":
                self._run(
                    lambda progress: self.service.recover(
                        profile,
                        path,
                        password,
                        progress=progress,
                    ),
                    with_progress=True,
                )
            else:
                self._run(
                    lambda: getattr(self.service, operation)(
                        profile,
                        path,
                        password,
                    )
                )
        except Exception as error: self._error(error)

    def has_active_remote_operation(self, *, include_status_refresh=True):
        return any(
            include_status_refresh or worker is not self._status_refresh
            for worker in self._workers
        )

    def stop_status_refresh(self):
        if self._status_refresh is not None:
            self._status_refresh.request_stop()

    def _run_status_refresh(self, profile, path, password):
        service = self.service
        worker = StatusRefreshSession(
            lambda stop_token, _progress: service.refresh(
                profile, path, password, stop_token=stop_token,
            ),
            self,
        )
        worker.succeeded.connect(self._completed)
        worker.failed.connect(self._error)
        worker.stopped.connect(self._status_refresh_stopped)
        worker.finished.connect(self._finished)
        self._workers.add(worker)
        self._status_refresh = worker
        self.busy = True
        self.status.setText("Refreshing density task status…")
        self.operation_state_changed.emit()
        self._update_controls()
        worker.start()

    @Slot()
    def _status_refresh_stopped(self):
        self.status.setText("Status refresh stopped. No calculation was cancelled.")

    def _request_cancel(self):
        if self.busy or self.task is None or self.task.job_id is None:
            return
        attempt_key = (self.task.task_id, self.task.job_id)
        if (
            self.task.state not in {"QUEUED", "RUNNING"}
            or attempt_key in self._cancel_suppressed_attempts
        ):
            return
        answer = QMessageBox.question(
            self,
            "Cancel density job",
            f"Cancel the exact shared {self._task_scheduler_name()} job?\n\n"
            f"Job ID: {self.task.job_id}\n\n"
            "Total, Subset 1 and Subset 2 run sequentially in this one job. "
            "Cancelling it stops the current component and prevents remaining "
            "components from starting.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            profile, password = self._profile_password()
            if profile is None:
                return
            path = self.task.remote_path
            self._run(lambda: self.service.cancel(profile, path, password))
        except Exception as error:
            self._error(error)

    def _run(self, function, *, with_progress=False):
        if self.busy: return
        self.busy = True
        self.operation_state_changed.emit()
        self._update_controls()
        if with_progress:
            self.status.setText(
                "Recovering density results in the background. Scientific "
                "validation follows the file transfer."
            )
            self.recovery_progress_label.setText(
                "Connecting and locating density result files…"
            )
            self.recovery_progress_bar.setRange(0, 0)
            self.recovery_progress_bar.setTextVisible(False)
            self.recovery_progress_panel.show()
        else:
            self.status.setText(
                "Working… SSH, file parsing and subtraction run in the background."
            )
            self.recovery_progress_panel.hide()
        worker = DensityWorker(function, with_progress=with_progress)
        self._workers.add(worker)
        worker.signals.succeeded.connect(self._completed)
        worker.signals.failed.connect(self._error)
        if with_progress:
            worker.signals.progress.connect(self._recovery_progress)
        worker.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    @staticmethod
    def _byte_count_text(value):
        value = max(0, int(value))
        amount = float(value)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if amount < 1024.0 or unit == "TiB":
                return (
                    f"{int(amount)} {unit}"
                    if unit == "B"
                    else f"{amount:.1f} {unit}"
                )
            amount /= 1024.0
        raise AssertionError("unreachable byte unit")

    @Slot(object)
    def _recovery_progress(self, event):
        if not isinstance(event, DensityRecoveryProgress):
            return
        self.recovery_progress_panel.show()
        details = []
        if event.phase is DensityRecoveryPhase.DOWNLOADING:
            if event.current_file_total_bytes is not None:
                details.append(
                    "Current file "
                    f"{self._byte_count_text(event.current_file_bytes)} / "
                    f"{self._byte_count_text(event.current_file_total_bytes)}"
                )
            elif event.current_file_bytes:
                details.append(
                    "Current file "
                    f"{self._byte_count_text(event.current_file_bytes)}"
                )
            if event.total_bytes is not None:
                details.append(
                    f"Total {self._byte_count_text(event.retrieved_bytes)} / "
                    f"{self._byte_count_text(event.total_bytes)}"
                )
                self.recovery_progress_bar.setRange(0, 1000)
                fraction = (
                    event.retrieved_bytes / event.total_bytes
                    if event.total_bytes > 0
                    else 1.0
                )
                self.recovery_progress_bar.setValue(
                    max(0, min(1000, round(fraction * 1000)))
                )
            else:
                self.recovery_progress_bar.setRange(0, 0)
        else:
            self.recovery_progress_bar.setRange(0, 0)
            if event.total_bytes is not None:
                details.append(
                    f"Downloaded {self._byte_count_text(event.retrieved_bytes)} / "
                    f"{self._byte_count_text(event.total_bytes)}"
                )
        self.recovery_progress_label.setText(
            event.message + (" — " + " · ".join(details) if details else "")
        )

    @Slot(object)
    def _completed(self, result):
        if isinstance(result, DensityCancellationResult):
            self._cancellation_completed(result)
        elif isinstance(result, DensityTask): self.load_task(result)
        elif isinstance(result, tuple) and isinstance(result[0], DensityTask):
            task, density_result = result
            self.load_task(task)
            self.result_view_requested.emit(
                DensityResultViewRequest(task, density_result)
            )

    def _cancellation_completed(self, result):
        self.load_task(result.task)
        attempt_key = (result.task.task_id, result.job_id)
        if result.outcome is DensityCancellationOutcome.ALREADY_TERMINAL:
            self._cancel_suppressed_attempts.discard(attempt_key)
            self.status.setText(result.task.message)
            return
        self._cancel_suppressed_attempts.add(attempt_key)
        if result.outcome is DensityCancellationOutcome.REQUESTED:
            self.status.setText(
                f"Cancellation requested for {self._task_scheduler_name()} "
                f"Job {result.job_id}. "
                "Use Refresh Status to retrieve its terminal state."
            )
        else:
            self.status.setText(
                f"Cancellation outcome for {self._task_scheduler_name()} "
                f"Job {result.job_id} is unknown "
                "after dispatch. Do not send another cancellation; use Refresh "
                "Status."
            )
        self._update_controls()

    @Slot(object)
    def _finished(self, worker):
        if worker not in self._workers:
            return
        self._workers.discard(worker)
        if worker is self._status_refresh:
            self._status_refresh = None
            worker.deleteLater()
        self.busy = False
        self.recovery_progress_panel.hide()
        self.recovery_progress_bar.setRange(0, 0)
        self.operation_state_changed.emit()
        self._update_controls()

    @Slot(object)
    def _error(self, error):
        if getattr(error, "density_task", None) is not None:
            self.load_task(error.density_task)
        from moltage.gui.projects_dialog import recovery_error_presentation
        presentation = recovery_error_presentation(error)
        self.status.setText(presentation.message)
        if isinstance(error, StatusRefreshTimeout):
            return
        QMessageBox.critical(self, presentation.title, presentation.message)

    def load_task(self, task):
        self.task = task
        if task.job_id is not None and task.state not in {"QUEUED", "RUNNING"}:
            self._cancel_suppressed_attempts.discard((task.task_id, task.job_id))
        self._selection = [set(task.partition.subset1), set(task.partition.subset2)]
        self._sync_selection()
        for index in range(self.server.count()):
            if str(self.server.itemData(index).profile_id) == task.data["context"]["profile_id"]:
                self.server.setCurrentIndex(index)
                break
        self.name.setText(task.name)
        settings = task.settings
        self._loading_task = True
        try:
            self.xc.setCurrentIndex(self.xc.findData(settings.xc))
            self.species.setCurrentIndex(self.species.findData(settings.species_accuracy))
            self.derive_charge.setChecked(False)
            for index, component in enumerate(COMPONENTS):
                state = getattr(settings, component)
                self.charge_fields[index].setValue(state.charge)
                self.spin_fields[index].setChecked(state.spin.enabled)
                if state.spin.enabled: self.moment_fields[index].setValue(state.spin.uniform_initial_moment)
            for name, field in self.scf_fields.items(): field.setValue(getattr(settings, name))
            self.spacing.setValue(task.grid.spacing)
            attempt_kind = SchedulerKind(
                task.attempt.get("scheduler_kind", SchedulerKind.SLURM.value)
            )
            current_profile = self.server.currentData()
            current_preset = (
                current_profile.execution_preset
                if current_profile is not None
                else None
            )
            if (
                current_preset is not None
                and current_preset.scheduler_kind is attempt_kind
            ):
                fields = (
                    self.resource_fields
                    if attempt_kind is SchedulerKind.SLURM
                    else self.lsf_resource_fields
                )
                for name, field in fields.items():
                    field.setValue(task.attempt["resources"][name])
                runtime = (
                    self.runtime_hours
                    if attempt_kind is SchedulerKind.SLURM
                    else self.lsf_runtime_hours
                )
                runtime.setValue(
                    runtime_hours_from_minutes(
                        task.attempt["resources"]["runtime_minutes"]
                    )
                )
        finally:
            self._loading_task = False
        self._preserved_grid = task.grid
        self._grid_summary()
        status = f"Job {task.job_id or 'receipt unknown'} — {task.message}\n{task.remote_path}"
        if task.state in {"FAILED", "CANCELLED"}:
            status += (
                "\nThis stopped/failed task is editable. Resubmit with Changes "
                "creates a new density task and preserves this task and its files."
            )
        self.status.setText(status)
        self.task_updated.emit(task)
        self._update_controls()

    def _update_controls(self):
        state = self.task.state if self.task else "PREPARED"
        editable = self._parameters_editable()
        component_states = (
            self.task.component_states
            if self.task is not None
            else {name: "NOT_STARTED" for name in COMPONENTS}
        )
        cancellation_enabled = (
            not self.busy
            and self.task is not None
            and self.task.job_id is not None
            and state in {"QUEUED", "RUNNING"}
            and (self.task.task_id, self.task.job_id)
            not in self._cancel_suppressed_attempts
        )
        for lamp, name in zip(self.component_lamps, COMPONENTS):
            lamp.set_state(
                component_states[name],
                cancellation_enabled=cancellation_enabled,
            )
        self.submit_button.setText(
            "Resubmit with Changes"
            if self.task is not None and state in {"FAILED", "CANCELLED"}
            else "Submit"
        )
        self.submit_button.setEnabled(editable)
        self.refresh_button.setText("Stop" if self._status_refresh is not None else "Refresh Status")
        self.refresh_button.setEnabled(self._status_refresh is not None or (not self.busy and self.task is not None))
        self.recover_button.setEnabled(
            not self.busy
            and state in {"OUTPUT_READY", "COMPLETE", "FAILED", "CANCELLED"}
        )
        self.retry_button.setEnabled(
            not self.busy and state in {"FAILED", "CANCELLED"}
        )
        self.retry_button.setToolTip(
            "Uses the original immutable scientific inputs; only current server resource values apply."
            if state in {"FAILED", "CANCELLED"}
            else ""
        )
        for index in (0, 1, 2):
            self.settings_tabs.widget(index).setEnabled(editable)
        self.server.setEnabled(editable)
        self.name.setEnabled(editable)
        self._update_resource_editability()
        self.pick_mode.setEnabled(editable)
        self.viewer.rectangle_mode = editable and self.pick_mode.currentIndex() == 1

    def _selected_scheduler_name(self):
        profile = self.server.currentData()
        preset = profile.execution_preset if profile is not None else None
        return (
            scheduler_display_name(preset.scheduler_kind)
            if preset is not None
            else "scheduler"
        )

    def _task_scheduler_name(self):
        if self.task is None:
            return self._selected_scheduler_name()
        kind = SchedulerKind(
            self.task.attempt.get("scheduler_kind", SchedulerKind.SLURM.value)
        )
        return scheduler_display_name(kind)

    def open_view_settings(self):
        before = self._view_preferences
        dialog = ViewSettingsDialog(
            self._view_preferences,
            (a.element for a in self.structure),
            self,
        )
        dialog.preview_preferences_changed.connect(self.viewer.set_view_preferences)
        if dialog.exec():
            self._view_preferences = dialog.selected_preferences()
        else:
            self._view_preferences = before
            self.viewer.set_view_preferences(self._view_preferences)


class DensityResultView(QWidget):
    """Read-only presentation created only after explicit result recovery."""

    lighting_accepted = Signal(object)

    def __init__(
        self,
        request,
        parent=None,
        *,
        view_preferences=None,
        orbital_preferences=None,
    ):
        super().__init__(parent)
        if not isinstance(request, DensityResultViewRequest):
            raise TypeError("Density result view requires a recovered result request")
        self.setObjectName("densityResultView")
        self.task = request.task
        self.result = request.result
        self.structure = self.task.partition.structure
        self.busy = False
        self._workers = set()
        self._surface_worker = None
        self._surface_revision = 0
        self._surface_pending = False
        self._displayed_field = None
        self._displayed_resolution = None
        self._initial_fit = False
        self._view_preferences = view_preferences or ViewPreferences()
        self._orbital_preferences = (
            orbital_preferences
            or OrbitalSurfacePreferences(
                resolution=OrbitalSurfaceResolution.MEDIUM
            )
        )
        self._resolution_cache = {
            OrbitalSurfaceResolution.FULL: (
                self.result.total,
                self.result.reference,
                self.result.difference,
            )
        }

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        definition = QLabel(
            "Δρ = ρtotal − ρsubset1 − ρsubset2; positive values show "
            "electron accumulation and negative values show depletion."
        )
        definition.setWordWrap(True)
        header.addWidget(definition, 1)

        resolution_box = QGroupBox("Display resolution", self)
        resolution_box.setObjectName("densityResolutionControls")
        resolution_layout = QVBoxLayout(resolution_box)
        resolution_choices = QHBoxLayout()
        self.resolution_group = QButtonGroup(self)
        self.resolution_group.setExclusive(True)
        self.resolution_buttons = {}
        for resolution in OrbitalSurfaceResolution:
            button = QRadioButton(resolution.value, resolution_box)
            button.setObjectName(f"densityResolution{resolution.value}")
            button.setToolTip(
                "Display-only grid used for isosurface extraction. "
                "Scientific Cube data, Hirshfeld values and exports remain "
                "full resolution."
            )
            self.resolution_group.addButton(button)
            self.resolution_buttons[resolution] = button
            resolution_choices.addWidget(button)
        resolution_layout.addLayout(resolution_choices)
        self.resolution_summary = QLabel(resolution_box)
        self.resolution_summary.setObjectName("densityResolutionSummary")
        resolution_layout.addWidget(self.resolution_summary)
        header.addWidget(resolution_box, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)

        self._sync_resolution_controls()
        for resolution, button in self.resolution_buttons.items():
            button.toggled.connect(
                lambda checked, selected=resolution: (
                    self._resolution_selected(selected) if checked else None
                )
            )

        split = QSplitter(self)
        layout.addWidget(split, 1)
        self.charge_table = self._build_charge_table()
        self.charge_table.setMaximumWidth(440)
        split.addWidget(self.charge_table)
        self.viewer = FragmentViewer(self)
        radii = load_default_covalent_radii()
        self.viewer.set_molecule(
            self.structure,
            infer_connectivity(self.structure, radii),
            radii,
        )
        self.viewer.set_view_preferences(self._view_preferences)
        split.addWidget(self.viewer)
        split.setStretchFactor(1, 1)

        result_bar = QHBoxLayout()
        self.display_mode = QComboBox()
        self.display_mode.setObjectName("densityDisplayMode")
        self.display_mode.addItems(
            ("Density difference", "Total/reference density")
        )
        self.display_mode.setToolTip(
            "Δρ = ρtotal − ρsubset1 − ρsubset2; positive = electron "
            "accumulation, negative = depletion. Density units: "
            "electrons/bohr³."
        )
        self.display_mode.currentIndexChanged.connect(self._request_surface)
        result_bar.addWidget(self.display_mode)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("densityInterpolation")
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(self._request_surface)
        result_bar.addWidget(self.slider, 1)
        self.interpolation_label = QLabel(
            "Density Interpolation λ=1.00 (not time)"
        )
        result_bar.addWidget(self.interpolation_label)
        self.view_button = QPushButton("View…")
        self.view_button.clicked.connect(self.open_view_settings)
        result_bar.addWidget(self.view_button)
        self.export_button = QPushButton("Export report…")
        self.export_button.clicked.connect(self._export)
        result_bar.addWidget(self.export_button)
        layout.addLayout(result_bar)
        self.status = QLabel(
            f"Recovered validated result for Job "
            f"{self.task.job_id or 'receipt unknown'} — {self.task.remote_path}"
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._surface_timer = QTimer(self)
        self._surface_timer.setSingleShot(True)
        self._surface_timer.setInterval(80)
        self._surface_timer.timeout.connect(self._start_surface)
        self._request_surface()
        self._update_controls()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_fit:
            self._initial_fit = True
            QTimer.singleShot(0, self.viewer, self.viewer.reset_camera)

    def _build_charge_table(self):
        table = QTableWidget(len(self.structure) + 2, 4)
        table.setObjectName("densityChargeTable")
        table.setHorizontalHeaderLabels(
            ("Atom / fragment", "q total", "q fragment", "Final gain (e)")
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for index, atom in enumerate(self.structure):
            values = (
                f"{index + 1} {atom.element}",
                self.result.total_charges[index],
                self.result.fragment_charges[index],
                self.result.electron_gains[index],
            )
            for column, value in enumerate(values):
                table.setItem(
                    index,
                    column,
                    QTableWidgetItem(
                        str(value) if column == 0 else f"{value:.6f}"
                    ),
                )
        for row, name in enumerate(COMPONENTS[1:], len(self.structure)):
            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(
                row,
                3,
                QTableWidgetItem(
                    f"{sum(self.result.electron_gains[i] for i in self.task.partition.indexes(name)):.6f}"
                ),
            )
        table.setToolTip(
            "Hirshfeld population changes: qfragment − qtotal. "
            "Not fixed-space integrals of Δρ."
        )
        return table

    def _request_surface(self, *_):
        self._surface_revision += 1
        self.interpolation_label.setText(
            f"Density Interpolation λ={self.slider.value() / 100:.2f} "
            "(not time)"
        )
        self._surface_timer.start()

    def _resolution_selected(self, resolution):
        if resolution is self._orbital_preferences.resolution:
            return
        self._orbital_preferences = replace(
            self._orbital_preferences,
            resolution=resolution,
        )
        self._sync_resolution_controls()
        self._request_surface()

    def _sync_resolution_controls(self):
        resolution = self._orbital_preferences.resolution
        blockers = [
            QSignalBlocker(button)
            for button in self.resolution_buttons.values()
        ]
        self.resolution_buttons[resolution].setChecked(True)
        del blockers
        source = self.result.difference.dimensions
        limit = resolution.maximum_axis_points
        displayed = (
            source if limit is None else density_display_dimensions(source, limit)
        )
        dimensions = " × ".join(str(value) for value in displayed)
        source_dimensions = " × ".join(str(value) for value in source)
        if displayed == source:
            summary = f"{dimensions} (source grid; display only)"
        else:
            summary = (
                f"{dimensions} (source {source_dimensions}; display only)"
            )
        self.resolution_summary.setText(summary)

    def _start_surface(self):
        if self._surface_worker is not None:
            self._surface_pending = True
            return
        revision = self._surface_revision
        fraction = self.slider.value() / 100
        difference = self.display_mode.currentIndex() == 0
        result = self.result
        resolution = self._orbital_preferences.resolution
        cached_basis = self._resolution_cache.get(resolution)

        def prepare_surface():
            if fraction == 0 and difference:
                return revision, resolution, cached_basis, None
            basis = cached_basis
            if basis is None:
                limit = resolution.maximum_axis_points
                if limit is None:
                    basis = (
                        result.total,
                        result.reference,
                        result.difference,
                    )
                else:
                    basis = result.display_basis(limit)
            field = interpolate_density_fields(
                *basis,
                fraction,
                difference=difference,
            )
            return revision, resolution, basis, field

        worker = DensityWorker(
            prepare_surface
        )
        self._surface_worker = worker
        self._workers.add(worker)
        worker.signals.succeeded.connect(self._surface_ready)
        worker.signals.failed.connect(self._error)
        worker.signals.finished.connect(self._surface_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _surface_ready(self, result):
        revision, resolution, basis, field = result
        if basis is not None:
            self._resolution_cache.setdefault(resolution, basis)
        if (
            revision != self._surface_revision
            or resolution is not self._orbital_preferences.resolution
        ):
            return
        try:
            self.viewer.display_field(field, self._orbital_preferences)
            self._displayed_field = field
            self._displayed_resolution = resolution
        except Exception as error:
            self.status.setText(
                "Density rendering failed; the validated calculation result "
                "is unchanged."
            )
            QMessageBox.critical(self, "Density rendering failed", str(error))

    @Slot(object)
    def _surface_finished(self, worker):
        self._workers.discard(worker)
        self._surface_worker = None
        if self._surface_pending:
            self._surface_pending = False
            self._surface_timer.start()

    def open_view_settings(self):
        before_view = self._view_preferences
        before_orbital = self._orbital_preferences
        maximum = max(
            self._orbital_preferences.isovalue,
            self.result.total.maximum_absolute_value,
            self.result.reference.maximum_absolute_value,
            self.result.difference.maximum_absolute_value,
        )
        dialog = ViewSettingsDialog(
            self._view_preferences,
            (atom.element for atom in self.structure),
            self,
            orbital_preferences=self._orbital_preferences,
            orbital_maximum_isovalue=maximum,
            orbital_resolution_control=True,
        )
        dialog.preview_preferences_changed.connect(
            self.viewer.set_view_preferences
        )
        dialog.preview_orbital_preferences_changed.connect(
            self._preview_orbital
        )
        if dialog.exec():
            self._view_preferences = dialog.selected_preferences()
            selected_orbital = dialog.selected_orbital_preferences()
            assert selected_orbital is not None
            self._orbital_preferences = selected_orbital
            self._sync_resolution_controls()
            self.lighting_accepted.emit(self._orbital_preferences)
        else:
            resolution_changed = (
                self._orbital_preferences.resolution
                is not before_orbital.resolution
            )
            self._view_preferences = before_view
            self._orbital_preferences = before_orbital
            self._sync_resolution_controls()
            self.viewer.set_view_preferences(before_view)
            if resolution_changed:
                self._request_surface()
            elif self._displayed_field is not None:
                self.viewer.set_orbital_surface_preferences(before_orbital)

    def _preview_orbital(self, preferences):
        resolution_changed = (
            preferences.resolution is not self._orbital_preferences.resolution
        )
        self._orbital_preferences = preferences
        self._sync_resolution_controls()
        if resolution_changed:
            self._request_surface()
        elif (
            self._displayed_field is not None
            and self._displayed_resolution is preferences.resolution
        ):
            self.viewer.set_orbital_surface_preferences(preferences)

    def _export(self):
        root = QFileDialog.getExistingDirectory(
            self,
            "Choose parent folder for a new result report",
        )
        if not root:
            return
        destination = Path(root) / (
            self.task.name
            + "_density_"
            + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        )
        self._run(lambda: export_density_result(self.result, destination))

    def _run(self, function):
        if self.busy:
            return
        self.busy = True
        self._update_controls()
        self.status.setText("Exporting the validated density result…")
        worker = DensityWorker(function)
        self._workers.add(worker)
        worker.signals.succeeded.connect(self._completed)
        worker.signals.failed.connect(self._error)
        worker.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _completed(self, result):
        if isinstance(result, Path):
            self.status.setText(f"Report saved: {result}")

    @Slot(object)
    def _finished(self, worker):
        self._workers.discard(worker)
        self.busy = False
        self._update_controls()

    @Slot(object)
    def _error(self, error):
        self.status.setText(str(error))
        QMessageBox.critical(self, "Density result", str(error))

    def _update_controls(self):
        for control in (
            self.slider,
            self.display_mode,
            self.view_button,
            self.export_button,
            *self.resolution_buttons.values(),
        ):
            control.setEnabled(not self.busy)
