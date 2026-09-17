"""Interactive local tight-binding Hamiltonian and transmission workspace."""

from math import floor, isfinite, log10

from PySide6.QtCharts import QChart, QLineSeries, QLogValueAxis, QValueAxis
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPointF,
    QRunnable,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from moltage.app.tight_binding import (
    TightBindingCalculator,
    TightBindingConfigurationError,
    normalized_element_pair,
    resolve_tight_binding_model,
)
from moltage.domain.anchor import AnchorCandidate
from moltage.domain.bond_display import (
    BondDisplayOrder,
    single_bond_display_orders,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.domain.tight_binding import (
    TightBindingModel,
    TightBindingTransmissionResult,
    TightBindingValidationError,
)
from moltage.gui.log_axis import PowerOfTenLogChartView
from moltage.visualization.molecule_viewer import MoleculeViewerWidget
from moltage.visualization.view_preferences import ViewPreferences


class _CalculationSignals(QObject):
    succeeded = Signal(int, object)
    failed = Signal(int, object)
    finished = Signal(object)


class _CalculationWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        calculator: TightBindingCalculator,
        model: TightBindingModel,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.calculator = calculator
        self.model = model
        self.signals = _CalculationSignals()
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self.calculator.calculate(self.model)
        except Exception as error:
            self.signals.failed.emit(self.generation, error)
        else:
            self.signals.succeeded.emit(self.generation, result)
        finally:
            self.signals.finished.emit(self)


class TightBindingMatrixModel(QAbstractTableModel):
    """Virtualized dense presentation of a sparse parameterized Hamiltonian."""

    def __init__(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._structure = structure
        self._edges = {
            (bond.first_index, bond.second_index) for bond in connectivity
        }
        self._onsite: tuple[float | None, ...] = (None,) * len(structure)
        self._hoppings: dict[tuple[int, int], float | None] = {
            edge: None for edge in self._edges
        }

    def set_values(
        self,
        onsite: tuple[float | None, ...],
        hoppings: dict[tuple[int, int], float | None],
    ) -> None:
        self.beginResetModel()
        self._onsite = onsite
        self._hoppings = dict(hoppings)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._structure)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._structure)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        edge = tuple(sorted((row, column)))
        value: float | None
        required = False
        if row == column:
            value = self._onsite[row]
            required = True
        elif edge in self._edges:
            value = self._hoppings.get(edge)
            required = True
        else:
            value = 0.0
        if role == Qt.ItemDataRole.DisplayRole:
            return "—" if value is None else _format_value(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.BackgroundRole and required and value is None:
            return QColor("#fff2f0")
        if role == Qt.ItemDataRole.ToolTipRole:
            if row == column:
                return f"H[{row + 1},{column + 1}] = onsite energy"
            if edge in self._edges:
                return (
                    f"H[{row + 1},{column + 1}] = symmetric bond coupling"
                )
            return "Non-bonded matrix element = 0"
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        atom = self._structure[section]
        return f"{atom.index + 1} {atom.element}"


class TightBindingPlot(QWidget):
    """Mutable logarithmic view of raw local-model transmission samples."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._series = QLineSeries(self)
        self._series.setName("Local-model T(E)")
        self._zero = QLineSeries(self)
        pen = QPen(QColor("#8a96a3"))
        pen.setStyle(Qt.PenStyle.DashLine)
        self._zero.setPen(pen)
        self._chart = QChart()
        self._chart.setTitle("Local Tight-Binding Transmission")
        self._chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._chart.addSeries(self._series)
        self._chart.addSeries(self._zero)
        self._x_axis = QValueAxis(self._chart)
        self._x_axis.setTitleText("Energy − EF (eV)")
        self._x_axis.setLabelFormat("%.3g")
        self._y_axis = QLogValueAxis(self._chart)
        self._y_axis.setTitleText("Transmission T(E)")
        self._y_axis.setBase(10.0)
        self._chart.addAxis(self._x_axis, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(self._y_axis, Qt.AlignmentFlag.AlignLeft)
        for series in (self._series, self._zero):
            series.attachAxis(self._x_axis)
            series.attachAxis(self._y_axis)
        for marker in self._chart.legend().markers(self._zero):
            marker.setVisible(False)
        self._chart_view = PowerOfTenLogChartView(
            self._chart,
            self._y_axis,
            self,
        )
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        layout.addWidget(self._chart_view, 1)
        self._note = QLabel(
            "No result yet. T(E) is a local orthogonal one-orbital, spinless, "
            "wide-band model—not an AITRANSS result.",
            self,
        )
        self._note.setWordWrap(True)
        layout.addWidget(self._note)
        self._default_ranges = ((-1.0, 1.0), (1.0e-6, 1.0))
        self.reset_view()

    def set_result(self, result: TightBindingTransmissionResult) -> None:
        if not isinstance(result, TightBindingTransmissionResult):
            raise TypeError("tight-binding plot requires a transmission result")
        self._series.replace(
            [
                QPointF(energy, value)
                for energy, value in zip(
                    result.energies_ev,
                    result.transmissions,
                )
            ]
        )
        positive = [value for value in result.transmissions if value > 0.0]
        y_min = 1.0e-12 if not positive else 10.0 ** floor(log10(min(positive)))
        y_max = max(1.0, max(positive, default=0.0) * 1.10)
        if y_min >= y_max:
            y_min = y_max / 10.0
        x_min, x_max = result.energies_ev[0], result.energies_ev[-1]
        self._default_ranges = ((x_min, x_max), (y_min, y_max))
        self._zero.replace([QPointF(0.0, y_min), QPointF(0.0, y_max)])
        self._note.setText(
            f"{len(result.energies_ev):,} raw points; base-10 logarithmic display. "
            "Non-positive values are not replaced by a scientific-data floor."
        )
        self.reset_view()

    @Slot()
    def reset_view(self) -> None:
        self._x_axis.setRange(*self._default_ranges[0])
        self._y_axis.setRange(*self._default_ranges[1])


class TightBindingResultWindow(QMainWindow):
    """Separate on-demand presentation of one workspace's local result."""

    def __init__(
        self,
        source_title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("tightBindingResultWindow")
        self.setWindowTitle(f"Local Tight-Binding Transmission — {source_title}")
        self.plot = TightBindingPlot(self)
        self.setCentralWidget(self.plot)
        self.resize(900, 620)


class TightBindingWorkspace(QMainWindow):
    """One session-local parameter model created from a Geometry snapshot."""

    def __init__(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        anchors: tuple[AnchorCandidate, ...],
        covalent_radii,
        *,
        source_title: str,
        bond_display_orders: tuple[BondDisplayOrder, ...] | None = None,
        view_preferences: ViewPreferences | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if connectivity.atom_count != len(structure):
            raise TightBindingConfigurationError(
                "tight-binding source connectivity does not match its structure"
            )
        self.setObjectName("tightBindingWorkspace")
        self.structure = structure
        self.connectivity = connectivity
        self.anchors = tuple(anchors)
        self._onsite_overrides: dict[int, str] = {}
        self._hopping_overrides: dict[tuple[int, int], str] = {}
        self._selected_atom: int | None = None
        self._selected_bond: tuple[int, int] | None = None
        self._hovered_atom: int | None = None
        self._hovered_bond: tuple[int, int] | None = None
        self._contact_pick_target: str | None = None
        self._syncing_override = False
        self._calculation_started = False
        self._generation = 0
        self._running_worker: _CalculationWorker | None = None
        self._pending_calculation: tuple[int, TightBindingModel] | None = None
        self._workers: set[_CalculationWorker] = set()
        self._closed = False
        self._calculator = TightBindingCalculator()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._recalculate_timer = QTimer(self)
        self._recalculate_timer.setSingleShot(True)
        self._recalculate_timer.setInterval(200)
        self._recalculate_timer.timeout.connect(self._recalculate_latest)
        self._latest_model: TightBindingModel | None = None
        self._source_title = source_title
        self.result_window: TightBindingResultWindow | None = None

        central = QWidget(self)
        root = QVBoxLayout(central)
        description = QLabel(
            f"Source snapshot: {source_title}. Orthogonal one-orbital, real-bond, "
            "spinless coherent model; all energies are relative to EF = 0 eV.",
            central,
        )
        description.setObjectName("tightBindingModelDescription")
        description.setWordWrap(True)
        root.addWidget(description)

        outer_splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.settings_tabs = QTabWidget(outer_splitter)
        self.settings_tabs.setObjectName("tightBindingSettingsTabs")
        self.settings_tabs.setMaximumWidth(430)
        self.settings_tabs.currentChanged.connect(self._settings_tab_changed)
        outer_splitter.addWidget(self.settings_tabs)

        self.viewer = MoleculeViewerWidget(outer_splitter)
        self.viewer.set_molecule(
            structure,
            connectivity,
            covalent_radii,
            (
                bond_display_orders
                if bond_display_orders is not None
                else single_bond_display_orders(connectivity)
            ),
        )
        self.viewer.set_view_preferences(view_preferences or ViewPreferences())
        self.viewer.atom_picked.connect(self._atom_picked)
        self.viewer.bond_picked.connect(self._bond_picked)
        self.viewer.atom_hovered.connect(self._atom_hovered)
        self.viewer.bond_hovered.connect(self._bond_hovered)
        self.viewer.hover_cleared.connect(self._hover_cleared)
        self.viewer.set_hover_picking_enabled(True)
        outer_splitter.addWidget(self.viewer)
        outer_splitter.setStretchFactor(1, 1)
        root.addWidget(outer_splitter, 1)

        bottom = QHBoxLayout()
        self.calculate_button = QPushButton("Calculate", central)
        self.calculate_button.setObjectName("tightBindingCalculate")
        self.calculate_button.setEnabled(False)
        self.calculate_button.clicked.connect(self._calculate_clicked)
        bottom.addWidget(self.calculate_button)
        self.status = QLabel("Define every required model parameter.", central)
        self.status.setObjectName("tightBindingStatus")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        root.addLayout(bottom)
        self.setCentralWidget(central)

        self._build_contacts_page()
        self._build_onsite_page()
        self._build_hopping_page()
        self._build_energy_page()
        self._build_matrix_dock()
        self._parameters_changed()
        QTimer.singleShot(0, self.viewer, self.viewer.reset_camera)

    def _page(self, title: str) -> QWidget:
        page = QWidget(self.settings_tabs)
        self.settings_tabs.addTab(page, title)
        return page

    def _build_contacts_page(self) -> None:
        page = self._page("Contacts")
        form = QFormLayout(page)
        candidate_map: dict[int, list[str]] = {}
        for anchor in self.anchors:
            candidate_map.setdefault(anchor.binding_atom_index, []).append(
                anchor.kind.value
            )
        self._candidate_indices = tuple(sorted(candidate_map))
        self.candidate_combo = QComboBox(page)
        if self._candidate_indices:
            for atom_index in self._candidate_indices:
                atom = self.structure[atom_index]
                kinds = ", ".join(sorted(set(candidate_map[atom_index])))
                self.candidate_combo.addItem(
                    f"{atom_index + 1} {atom.element} — {kinds}",
                    atom_index,
                )
        else:
            self.candidate_combo.addItem("No linker candidates", None)
        form.addRow("Linker candidate", self.candidate_combo)
        candidate_buttons = QHBoxLayout()
        use_left = QPushButton("Use as Left", page)
        use_right = QPushButton("Use as Right", page)
        use_left.clicked.connect(lambda: self._use_candidate("left"))
        use_right.clicked.connect(lambda: self._use_candidate("right"))
        candidate_buttons.addWidget(use_left)
        candidate_buttons.addWidget(use_right)
        form.addRow(candidate_buttons)

        self.left_contact = _contact_spinbox(len(self.structure), page)
        self.right_contact = _contact_spinbox(len(self.structure), page)
        self.left_contact.setObjectName("tightBindingLeftContact")
        self.right_contact.setObjectName("tightBindingRightContact")
        self.left_contact.valueChanged.connect(self._parameters_changed)
        self.right_contact.valueChanged.connect(self._parameters_changed)
        form.addRow("Left atom (1-based)", self.left_contact)
        form.addRow("Right atom (1-based)", self.right_contact)
        pick_row = QHBoxLayout()
        self.pick_left = QPushButton("Pick Left", page)
        self.pick_right = QPushButton("Pick Right", page)
        self.pick_left.setCheckable(True)
        self.pick_right.setCheckable(True)
        self.pick_left.clicked.connect(lambda checked: self._set_contact_pick("left", checked))
        self.pick_right.clicked.connect(lambda checked: self._set_contact_pick("right", checked))
        pick_row.addWidget(self.pick_left)
        pick_row.addWidget(self.pick_right)
        form.addRow(pick_row)

        self.gamma_left = _number_edit("Required, > 0", page)
        self.gamma_right = _number_edit("Required, > 0", page)
        self.gamma_left.setObjectName("tightBindingGammaLeft")
        self.gamma_right.setObjectName("tightBindingGammaRight")
        self.gamma_left.textChanged.connect(self._parameters_changed)
        self.gamma_right.textChanged.connect(self._parameters_changed)
        form.addRow("Γ Left (eV)", self.gamma_left)
        form.addRow("Γ Right (eV)", self.gamma_right)
        hint = QLabel(
            "Linker detection supplies candidates only. For a structure containing "
            "explicit electrodes, select the actual lead-coupled atom manually.",
            page,
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        if len(self._candidate_indices) == 2:
            self.left_contact.setValue(self._candidate_indices[0] + 1)
            self.right_contact.setValue(self._candidate_indices[1] + 1)

    def _build_onsite_page(self) -> None:
        page = self._page("Atom Energies")
        layout = QVBoxLayout(page)
        elements = tuple(sorted({atom.element for atom in self.structure}))
        self._onsite_type_edits: dict[str, QLineEdit] = {}
        table = QTableWidget(len(elements), 2, page)
        table.setObjectName("tightBindingOnsiteTypes")
        table.setHorizontalHeaderLabels(("Element", "ε (eV)"))
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, element in enumerate(elements):
            item = QTableWidgetItem(element)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, item)
            edit = _number_edit("Required", table)
            edit.textChanged.connect(self._parameters_changed)
            table.setCellWidget(row, 1, edit)
            self._onsite_type_edits[element] = edit
        table.setMaximumHeight(min(260, 62 + 34 * len(elements)))
        layout.addWidget(table)

        selected = QFrame(page)
        form = QFormLayout(selected)
        self.selected_atom_label = QLabel("Click an atom to select it.", selected)
        self.atom_override = _number_edit("Blank = inherit element value", selected)
        self.atom_override.setEnabled(False)
        self.atom_override.textChanged.connect(self._atom_override_changed)
        self.atom_effective = QLabel("—", selected)
        form.addRow("Selected atom", self.selected_atom_label)
        form.addRow("Exact override (eV)", self.atom_override)
        form.addRow("Effective ε", self.atom_effective)
        layout.addWidget(selected)
        hint = QLabel(
            "An exact atom override takes precedence over its element value. "
            "Clear the override field to inherit again.",
            page,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

    def _build_hopping_page(self) -> None:
        page = self._page("Bond Couplings")
        layout = QVBoxLayout(page)
        pairs = tuple(
            sorted(
                {
                    normalized_element_pair(
                        self.structure[bond.first_index].element,
                        self.structure[bond.second_index].element,
                    )
                    for bond in self.connectivity
                }
            )
        )
        self._hopping_type_edits: dict[tuple[str, str], QLineEdit] = {}
        table = QTableWidget(len(pairs), 2, page)
        table.setObjectName("tightBindingHoppingTypes")
        table.setHorizontalHeaderLabels(("Bond type", "t (eV)"))
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, pair in enumerate(pairs):
            item = QTableWidgetItem(f"{pair[0]}–{pair[1]}")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, item)
            edit = _number_edit("Required", table)
            edit.textChanged.connect(self._parameters_changed)
            table.setCellWidget(row, 1, edit)
            self._hopping_type_edits[pair] = edit
        table.setMaximumHeight(min(260, 62 + 34 * len(pairs)))
        layout.addWidget(table)

        selected = QFrame(page)
        form = QFormLayout(selected)
        self.selected_bond_label = QLabel("Click a displayed bond to select it.", selected)
        self.bond_override = _number_edit("Blank = inherit bond-type value", selected)
        self.bond_override.setEnabled(False)
        self.bond_override.textChanged.connect(self._bond_override_changed)
        self.bond_effective = QLabel("—", selected)
        form.addRow("Selected bond", self.selected_bond_label)
        form.addRow("Exact override (eV)", self.bond_override)
        form.addRow("Effective t", self.bond_effective)
        layout.addWidget(selected)
        hint = QLabel(
            "Couplings are grouped by unordered element pair. MOL bond order is "
            "visual reference only and never generates a coupling automatically.",
            page,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

    def _build_energy_page(self) -> None:
        page = self._page("Energy Grid")
        form = QFormLayout(page)
        self.energy_start = _number_edit("Required", page)
        self.energy_end = _number_edit("Required", page)
        self.energy_step = _number_edit("Required, > 0", page)
        self.eta = _number_edit("Required, > 0", page)
        for edit in (
            self.energy_start,
            self.energy_end,
            self.energy_step,
            self.eta,
        ):
            edit.textChanged.connect(self._parameters_changed)
        self.energy_start.setObjectName("tightBindingEnergyStart")
        self.energy_end.setObjectName("tightBindingEnergyEnd")
        self.energy_step.setObjectName("tightBindingEnergyStep")
        self.eta.setObjectName("tightBindingEta")
        form.addRow("Start (eV, included)", self.energy_start)
        form.addRow("End (eV, excluded)", self.energy_end)
        form.addRow("Step (eV)", self.energy_step)
        form.addRow("Numerical η (eV)", self.eta)
        hint = QLabel(
            "No chemistry-dependent values are filled automatically. η is an "
            "explicit numerical broadening and changes the calculated curve.",
            page,
        )
        hint.setWordWrap(True)
        form.addRow(hint)

    def _build_matrix_dock(self) -> None:
        self.matrix_model = TightBindingMatrixModel(
            self.structure,
            self.connectivity,
            self,
        )
        self.matrix_view = QTableView(self)
        self.matrix_view.setObjectName("tightBindingHamiltonian")
        self.matrix_view.setModel(self.matrix_model)
        self.matrix_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix_view.horizontalHeader().setDefaultSectionSize(72)
        self.matrix_view.verticalHeader().setDefaultSectionSize(28)
        self.matrix_dock = QDockWidget("Hamiltonian H (eV)", self)
        self.matrix_dock.setObjectName("tightBindingMatrixDock")
        self.matrix_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.matrix_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.matrix_dock.setWidget(self.matrix_view)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.matrix_dock)
        toolbar = QToolBar("Tight-Binding View", self)
        toolbar.setObjectName("tightBindingToolbar")
        action = self.matrix_dock.toggleViewAction()
        action.setText("Matrix")
        action.setToolTip("Show/Hide Hamiltonian matrix")
        toolbar.addAction(action)
        self.addToolBar(toolbar)

    @Slot(int)
    def _settings_tab_changed(self, index: int) -> None:
        hopping_tab = self.settings_tabs.tabText(index) == "Bond Couplings"
        self.viewer.set_bond_picking_enabled(hopping_tab)

    @Slot(int)
    def _atom_picked(self, atom_index: int) -> None:
        if self._contact_pick_target == "left":
            self.left_contact.setValue(atom_index + 1)
            self._finish_contact_pick()
            return
        if self._contact_pick_target == "right":
            self.right_contact.setValue(atom_index + 1)
            self._finish_contact_pick()
            return
        if self.settings_tabs.tabText(self.settings_tabs.currentIndex()) != "Atom Energies":
            return
        self._selected_atom = atom_index
        atom = self.structure[atom_index]
        self.selected_atom_label.setText(f"{atom_index + 1} {atom.element}")
        self.atom_override.setEnabled(True)
        self._syncing_override = True
        blocker = QSignalBlocker(self.atom_override)
        self.atom_override.setText(self._onsite_overrides.get(atom_index, ""))
        del blocker
        self._syncing_override = False
        self._refresh_selected_effective_values()

    @Slot(int, int)
    def _bond_picked(self, first: int, second: int) -> None:
        if self.settings_tabs.tabText(self.settings_tabs.currentIndex()) != "Bond Couplings":
            return
        edge = tuple(sorted((first, second)))
        self._selected_bond = edge
        first_atom, second_atom = self.structure[edge[0]], self.structure[edge[1]]
        self.selected_bond_label.setText(
            f"{edge[0] + 1} {first_atom.element} — "
            f"{edge[1] + 1} {second_atom.element}"
        )
        self.bond_override.setEnabled(True)
        self._syncing_override = True
        blocker = QSignalBlocker(self.bond_override)
        self.bond_override.setText(self._hopping_overrides.get(edge, ""))
        del blocker
        self._syncing_override = False
        self._refresh_selected_effective_values()

    def _use_candidate(self, side: str) -> None:
        atom_index = self.candidate_combo.currentData()
        if atom_index is None:
            return
        target = self.left_contact if side == "left" else self.right_contact
        target.setValue(atom_index + 1)

    def _set_contact_pick(self, side: str, checked: bool) -> None:
        self._contact_pick_target = side if checked else None
        other = self.pick_right if side == "left" else self.pick_left
        blocker = QSignalBlocker(other)
        other.setChecked(False)
        del blocker
        if checked:
            self.status.setText(f"Click one atom to use as the {side} contact.")

    def _finish_contact_pick(self) -> None:
        self._contact_pick_target = None
        for button in (self.pick_left, self.pick_right):
            blocker = QSignalBlocker(button)
            button.setChecked(False)
            del blocker

    @Slot(str)
    def _atom_override_changed(self, text: str) -> None:
        if self._syncing_override or self._selected_atom is None:
            return
        if text.strip():
            self._onsite_overrides[self._selected_atom] = text
        else:
            self._onsite_overrides.pop(self._selected_atom, None)
        self._parameters_changed()

    @Slot(str)
    def _bond_override_changed(self, text: str) -> None:
        if self._syncing_override or self._selected_bond is None:
            return
        if text.strip():
            self._hopping_overrides[self._selected_bond] = text
        else:
            self._hopping_overrides.pop(self._selected_bond, None)
        self._parameters_changed()

    @Slot()
    def _parameters_changed(self, *_unused) -> None:
        if not hasattr(self, "matrix_model"):
            return
        self._generation += 1
        self._latest_model = None
        self._pending_calculation = None
        onsite, hoppings = self._partial_effective_values()
        self.matrix_model.set_values(onsite, hoppings)
        self._refresh_hover_label(onsite, hoppings)
        self._update_contact_highlights()
        self._refresh_selected_effective_values(onsite, hoppings)
        try:
            model = self._resolved_model()
        except (TightBindingValidationError, TightBindingConfigurationError, TypeError) as error:
            self.calculate_button.setEnabled(False)
            self.status.setText(str(error))
            return
        self._latest_model = model
        self.calculate_button.setEnabled(True)
        self.status.setText(
            f"Ready: {model.atom_count} orbitals, {len(model.hoppings)} couplings, "
            f"{model.energy_point_count:,} energy points."
        )
        if self._calculation_started:
            self._recalculate_timer.start()

    def _partial_effective_values(
        self,
    ) -> tuple[
        tuple[float | None, ...],
        dict[tuple[int, int], float | None],
    ]:
        onsite_defaults = {
            element: _optional_float(edit.text())
            for element, edit in self._onsite_type_edits.items()
        }
        onsite = []
        for atom in self.structure:
            override = self._onsite_overrides.get(atom.index)
            onsite.append(
                _optional_float(override)
                if override is not None
                else onsite_defaults.get(atom.element)
            )
        hopping_defaults = {
            pair: _optional_float(edit.text())
            for pair, edit in self._hopping_type_edits.items()
        }
        hoppings: dict[tuple[int, int], float | None] = {}
        for bond in self.connectivity:
            edge = (bond.first_index, bond.second_index)
            override = self._hopping_overrides.get(edge)
            pair = normalized_element_pair(
                self.structure[edge[0]].element,
                self.structure[edge[1]].element,
            )
            hoppings[edge] = (
                _optional_float(override)
                if override is not None
                else hopping_defaults.get(pair)
            )
        return tuple(onsite), hoppings

    def _resolved_model(self) -> TightBindingModel:
        onsite_defaults = {
            element: value
            for element, edit in self._onsite_type_edits.items()
            if (value := _required_optional_float(edit.text(), f"{element} onsite energy"))
            is not None
        }
        onsite_overrides = {
            atom_index: _required_float(text, f"atom {atom_index + 1} override")
            for atom_index, text in self._onsite_overrides.items()
        }
        hopping_defaults = {
            pair: value
            for pair, edit in self._hopping_type_edits.items()
            if (value := _required_optional_float(edit.text(), f"{pair[0]}-{pair[1]} coupling"))
            is not None
        }
        hopping_overrides = {
            edge: _required_float(text, f"bond {edge[0] + 1}-{edge[1] + 1} override")
            for edge, text in self._hopping_overrides.items()
        }
        return resolve_tight_binding_model(
            self.structure,
            self.connectivity,
            onsite_by_element=onsite_defaults,
            onsite_overrides=onsite_overrides,
            hopping_by_element_pair=hopping_defaults,
            hopping_overrides=hopping_overrides,
            left_contact_index=(
                self.left_contact.value() - 1
                if self.left_contact.value()
                else None
            ),
            right_contact_index=(
                self.right_contact.value() - 1
                if self.right_contact.value()
                else None
            ),
            gamma_left_ev=_required_optional_float(
                self.gamma_left.text(),
                "Gamma L",
            ),
            gamma_right_ev=_required_optional_float(
                self.gamma_right.text(),
                "Gamma R",
            ),
            eta_ev=_required_optional_float(self.eta.text(), "eta"),
            energy_start_ev=_required_optional_float(
                self.energy_start.text(),
                "energy start",
            ),
            energy_end_ev=_required_optional_float(
                self.energy_end.text(),
                "energy end",
            ),
            energy_step_ev=_required_optional_float(
                self.energy_step.text(),
                "energy step",
            ),
        )

    def _refresh_hover_label(
        self,
        onsite: tuple[float | None, ...],
        hoppings: dict[tuple[int, int], float | None],
    ) -> None:
        if self._hovered_atom is not None:
            atom_index = self._hovered_atom
            atom = self.structure[atom_index]
            self.viewer.set_parameter_labels(
                {
                    atom_index: (
                        f"{atom_index + 1} {atom.element}: "
                        f"ε={_format_optional(onsite[atom_index])} eV"
                    )
                },
                {},
            )
        elif self._hovered_bond is not None:
            edge = self._hovered_bond
            self.viewer.set_parameter_labels(
                {},
                {
                    edge: (
                        f"{edge[0] + 1}–{edge[1] + 1}: "
                        f"t={_format_optional(hoppings[edge])} eV"
                    )
                },
            )

    @Slot(int)
    def _atom_hovered(self, atom_index: int) -> None:
        self._hovered_atom = atom_index
        self._hovered_bond = None
        self._refresh_hover_label(*self._partial_effective_values())

    @Slot(int, int)
    def _bond_hovered(self, first: int, second: int) -> None:
        self._hovered_atom = None
        self._hovered_bond = tuple(sorted((first, second)))
        self._refresh_hover_label(*self._partial_effective_values())

    @Slot()
    def _hover_cleared(self) -> None:
        self._hovered_atom = None
        self._hovered_bond = None
        self.viewer.set_parameter_labels({}, {})

    def _update_contact_highlights(self) -> None:
        left = self.left_contact.value() - 1 if self.left_contact.value() else None
        right = self.right_contact.value() - 1 if self.right_contact.value() else None
        if left is None and right is None:
            self.viewer.set_highlighted_atom_indices(self._candidate_indices, ())
            return
        self.viewer.set_highlighted_atom_indices(
            () if left is None else (left,),
            () if right is None or right == left else (right,),
        )

    def _refresh_selected_effective_values(
        self,
        onsite: tuple[float | None, ...] | None = None,
        hoppings: dict[tuple[int, int], float | None] | None = None,
    ) -> None:
        if onsite is None or hoppings is None:
            onsite, hoppings = self._partial_effective_values()
        if self._selected_atom is not None:
            self.atom_effective.setText(
                _format_optional(onsite[self._selected_atom]) + " eV"
            )
        if self._selected_bond is not None:
            self.bond_effective.setText(
                _format_optional(hoppings[self._selected_bond]) + " eV"
            )

    @Slot()
    def _calculate_clicked(self) -> None:
        try:
            model = self._resolved_model()
        except (TightBindingValidationError, TightBindingConfigurationError, TypeError) as error:
            self.status.setText(str(error))
            return
        self._calculation_started = True
        self._latest_model = model
        self._show_result_window()
        self._start_or_queue_calculation(self._generation, model)

    def _show_result_window(self) -> None:
        if self.result_window is None:
            self.result_window = TightBindingResultWindow(
                self._source_title,
                self,
            )
        self.result_window.show()
        self.result_window.raise_()
        self.result_window.activateWindow()

    @Slot()
    def _recalculate_latest(self) -> None:
        if self._latest_model is not None:
            self._start_or_queue_calculation(
                self._generation,
                self._latest_model,
            )

    def _start_or_queue_calculation(
        self,
        generation: int,
        model: TightBindingModel,
    ) -> None:
        if self._closed:
            return
        if self._running_worker is not None:
            self._pending_calculation = (generation, model)
            self.status.setText("Parameters changed; the latest calculation is queued.")
            return
        worker = _CalculationWorker(generation, self._calculator, model)
        worker.signals.succeeded.connect(self._calculation_succeeded)
        worker.signals.failed.connect(self._calculation_failed)
        worker.signals.finished.connect(self._calculation_finished)
        self._running_worker = worker
        self._workers.add(worker)
        self.status.setText("Calculating local transmission…")
        self._thread_pool.start(worker)

    @Slot(int, object)
    def _calculation_succeeded(self, generation: int, result: object) -> None:
        if self._closed or generation != self._generation:
            return
        if not isinstance(result, TightBindingTransmissionResult):
            self.status.setText("Local calculation returned an invalid result type.")
            return
        if self.result_window is None:
            self.result_window = TightBindingResultWindow(
                self._source_title,
                self,
            )
        self.result_window.plot.set_result(result)
        self.status.setText(
            f"Calculated {len(result.energies_ev):,} energy points from the current model."
        )

    @Slot(int, object)
    def _calculation_failed(self, generation: int, error: object) -> None:
        if self._closed or generation != self._generation:
            return
        self.status.setText(f"Local calculation failed: {error}")

    @Slot(object)
    def _calculation_finished(self, worker: object) -> None:
        if isinstance(worker, _CalculationWorker):
            self._workers.discard(worker)
            if self._running_worker is worker:
                self._running_worker = None
        if self._closed:
            return
        pending = self._pending_calculation
        self._pending_calculation = None
        if pending is not None and pending[0] == self._generation:
            self._start_or_queue_calculation(*pending)

    @Slot()
    def reset_view(self) -> None:
        self.viewer.reset_camera()
        if self.result_window is not None:
            self.result_window.plot.reset_view()

    def closeEvent(self, event) -> None:
        self._closed = True
        self._recalculate_timer.stop()
        self._pending_calculation = None
        if self.result_window is not None:
            self.result_window.close()
        owned_viewers = tuple(self.findChildren(MoleculeViewerWidget))
        for viewer in owned_viewers:
            viewer.prepare_for_owner_teardown()
        super().closeEvent(event)


def _contact_spinbox(atom_count: int, parent: QWidget) -> QSpinBox:
    field = QSpinBox(parent)
    field.setRange(0, atom_count)
    field.setSpecialValueText("Not selected")
    return field


def _number_edit(placeholder: str, parent: QWidget) -> QLineEdit:
    field = QLineEdit(parent)
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    return field


def _optional_float(text: str | None) -> float | None:
    if text is None or not text.strip():
        return None
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if isfinite(value) else None


def _required_optional_float(text: str, label: str) -> float | None:
    if not text.strip():
        return None
    return _required_float(text, label)


def _required_float(text: str, label: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as error:
        raise TightBindingConfigurationError(
            f"{label} must be a finite number"
        ) from error
    if not isfinite(value):
        raise TightBindingConfigurationError(f"{label} must be finite")
    return value


def _format_value(value: float) -> str:
    return f"{value:.6g}"


def _format_optional(value: float | None) -> str:
    return "—" if value is None else _format_value(value)
