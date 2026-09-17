"""Reusable PySide6 editor for the supported optimization settings model."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AimsSettingsValidationError,
    AtomAimsSettings,
    Relativity,
    SpeciesAccuracy,
    SpinInitializationMode,
    SpinSettings,
    VdwMethod,
    XCFunctional,
)
from moltage.aims.orbital_cube import (
    FRONTIER_ORBITAL_ORDER,
    FrontierOrbital,
    OrbitalCubeOutputSettings,
    OrbitalCubeValidationError,
    format_eigenstate_indices_text,
    parse_eigenstate_indices_text,
)
from moltage.domain.structure import MolecularStructure


_XC_CHOICES = (
    ("PBE", XCFunctional.PBE),
    ("PBE0", XCFunctional.PBE0),
    ("BLYP", XCFunctional.BLYP),
    ("B3LYP", XCFunctional.B3LYP),
    ("revPBE", XCFunctional.REVPBE),
    ("AM05", XCFunctional.AM05),
)

_VDW_CHOICES = (
    ("None", VdwMethod.NONE),
    ("TS (Hirshfeld)", VdwMethod.TS_HIRSHFELD),
    ("TS (libmbd)", VdwMethod.TS_LIBMBD),
)

_RELATIVITY_CHOICES = (
    ("Atomic ZORA scalar", Relativity.ATOMIC_ZORA_SCALAR),
    ("None", Relativity.NONE),
)

_ACCURACY_CHOICES = (
    ("Light", SpeciesAccuracy.LIGHT),
    ("Tight", SpeciesAccuracy.TIGHT),
    ("Really tight", SpeciesAccuracy.REALLY_TIGHT),
)

_FRONTIER_LABELS = {
    FrontierOrbital.HOMO_MINUS_2: "HOMO-2",
    FrontierOrbital.HOMO_MINUS_1: "HOMO-1",
    FrontierOrbital.HOMO: "HOMO",
    FrontierOrbital.LUMO: "LUMO",
    FrontierOrbital.LUMO_PLUS_1: "LUMO+1",
    FrontierOrbital.LUMO_PLUS_2: "LUMO+2",
}


class AimsOptimizationSettingsDialog(QDialog):
    """Edit settings while delegating all scientific validation to the model."""

    def __init__(
        self,
        structure: MolecularStructure,
        initial_settings: AimsOptimizationSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(structure, MolecularStructure) or not structure:
            raise ValueError("optimization settings require a non-empty structure")
        settings = initial_settings or AimsOptimizationSettings()
        if not isinstance(settings, AimsOptimizationSettings):
            raise TypeError("initial settings must be AimsOptimizationSettings")

        self._structure = structure
        self._atom_settings = {
            item.atom_index: item for item in settings.atom_settings
        }
        self._accepted_settings: AimsOptimizationSettings | None = None
        self.setWindowTitle("FHI-aims Optimization Settings")
        self.setMinimumWidth(900)
        self.resize(960, 720)

        layout = QVBoxLayout(self)
        columns = QHBoxLayout()
        left_column = QVBoxLayout()
        right_column = QVBoxLayout()
        columns.addLayout(left_column, 1)
        columns.addLayout(right_column, 1)
        layout.addLayout(columns, 1)

        general_group = QGroupBox("General", self)
        general_group.setObjectName("optimizationGeneralGroup")
        main_form = QFormLayout(general_group)

        self._xc = _enum_combo(_XC_CHOICES, settings.xc, "xcFunctional")
        main_form.addRow("Functional", self._xc)
        self._vdw = _enum_combo(_VDW_CHOICES, settings.vdw, "vdwMethod")
        main_form.addRow("vdW", self._vdw)
        self._relativity = _enum_combo(
            _RELATIVITY_CHOICES,
            settings.relativity,
            "relativity",
        )
        main_form.addRow("Relativity", self._relativity)
        self._species_accuracy = _enum_combo(
            _ACCURACY_CHOICES,
            settings.species_accuracy,
            "speciesAccuracy",
        )
        main_form.addRow("Species accuracy", self._species_accuracy)

        self._force_threshold = QDoubleSpinBox(self)
        self._force_threshold.setObjectName("forceThreshold")
        self._force_threshold.setDecimals(8)
        self._force_threshold.setRange(1.0e-8, 100.0)
        self._force_threshold.setValue(settings.force_threshold)
        self._force_threshold.setSuffix(" eV/Å")
        self._force_threshold.setKeyboardTracking(False)
        main_form.addRow("Force threshold", self._force_threshold)
        left_column.addWidget(general_group)

        self._build_spin_group(settings, right_column)
        self._build_charge_group(settings, left_column)

        output_group = QGroupBox("Output", self)
        output_layout = QVBoxLayout(output_group)
        self._output_dipole = QCheckBox("Dipole", output_group)
        self._output_dipole.setObjectName("outputDipole")
        self._output_dipole.setChecked(settings.output_dipole)
        output_layout.addWidget(self._output_dipole)

        orbital_label = QLabel("Molecular-orbital Cube files", output_group)
        output_layout.addWidget(orbital_label)
        orbital_grid = QGridLayout()
        self._frontier_orbital_checks: dict[FrontierOrbital, QCheckBox] = {}
        selected_frontier = frozenset(
            settings.orbital_cubes.frontier_orbitals
        )
        for position, orbital in enumerate(FRONTIER_ORBITAL_ORDER):
            checkbox = QCheckBox(_FRONTIER_LABELS[orbital], output_group)
            checkbox.setObjectName(
                "outputOrbital" + _FRONTIER_LABELS[orbital]
                .replace("-", "Minus")
                .replace("+", "Plus")
            )
            checkbox.setChecked(orbital in selected_frontier)
            orbital_grid.addWidget(checkbox, position // 3, position % 3)
            self._frontier_orbital_checks[orbital] = checkbox
        output_layout.addLayout(orbital_grid)

        custom_row = QHBoxLayout()
        self._additional_orbitals_enabled = QCheckBox(
            "Additional state numbers",
            output_group,
        )
        self._additional_orbitals_enabled.setObjectName(
            "outputAdditionalOrbitalStates"
        )
        self._additional_orbitals_enabled.setChecked(
            bool(settings.orbital_cubes.eigenstate_indices)
        )
        custom_row.addWidget(self._additional_orbitals_enabled)
        self._orbital_eigenstates = QLineEdit(
            format_eigenstate_indices_text(
                settings.orbital_cubes.eigenstate_indices
            ),
            output_group,
        )
        self._orbital_eigenstates.setObjectName("outputOrbitalEigenstates")
        self._orbital_eigenstates.setPlaceholderText("e.g. 23, 24, 27")
        self._orbital_eigenstates.setEnabled(
            self._additional_orbitals_enabled.isChecked()
        )
        custom_row.addWidget(self._orbital_eigenstates, 1)
        output_layout.addLayout(custom_row)

        spacing_row = QHBoxLayout()
        spacing_row.addWidget(QLabel("Cube grid spacing", output_group))
        self._orbital_grid_spacing = QLineEdit(output_group)
        self._orbital_grid_spacing.setObjectName("outputOrbitalGridSpacing")
        self._orbital_grid_spacing.setPlaceholderText(
            "Blank = FHI-aims default"
        )
        if settings.orbital_cubes.grid_spacing_angstrom is not None:
            self._orbital_grid_spacing.setText(
                _format_edit_real(
                    settings.orbital_cubes.grid_spacing_angstrom
                )
            )
        spacing_row.addWidget(self._orbital_grid_spacing, 1)
        spacing_row.addWidget(QLabel("Å", output_group))
        output_layout.addLayout(spacing_row)
        left_column.addWidget(output_group)
        left_column.addStretch(1)

        self._build_atom_overrides_group(right_column)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._button_box = buttons

        self._spin_enabled.toggled.connect(self._update_spin_controls)
        self._uniform_spin.toggled.connect(self._update_spin_controls)
        self._fixed_spin.toggled.connect(self._update_spin_controls)
        self._charged_system.toggled.connect(self._update_charge_controls)
        self._additional_orbitals_enabled.toggled.connect(
            self._orbital_eigenstates.setEnabled
        )
        self._update_spin_controls()
        self._update_charge_controls()
        self._refresh_override_list()

    def selected_settings(self) -> AimsOptimizationSettings:
        """Return the validated model after the dialog has been accepted."""

        if self._accepted_settings is None:
            raise RuntimeError("optimization settings dialog was not accepted")
        return self._accepted_settings

    def _build_spin_group(
        self,
        settings: AimsOptimizationSettings,
        parent_layout: QVBoxLayout,
    ) -> None:
        group = QGroupBox("Spin", self)
        layout = QVBoxLayout(group)
        self._spin_enabled = QCheckBox("Enable spin polarization", group)
        self._spin_enabled.setObjectName("spinEnabled")
        self._spin_enabled.setChecked(settings.spin.enabled)
        layout.addWidget(self._spin_enabled)
        self._spin_state = QLabel(group)
        self._spin_state.setObjectName("spinState")
        layout.addWidget(self._spin_state)

        self._spin_options = QWidget(group)
        options_layout = QVBoxLayout(self._spin_options)
        options_layout.setContentsMargins(18, 0, 0, 0)
        options_layout.addWidget(QLabel("Initial spin density:"))
        self._per_atom_spin = QRadioButton(
            "Per-atom initial moments",
            self._spin_options,
        )
        self._per_atom_spin.setObjectName("perAtomSpin")
        self._uniform_spin = QRadioButton(
            "Uniform default initial moment / atom",
            self._spin_options,
        )
        self._uniform_spin.setObjectName("uniformSpin")
        if (
            settings.spin.initialization_mode
            is SpinInitializationMode.UNIFORM_DEFAULT
        ):
            self._uniform_spin.setChecked(True)
        else:
            self._per_atom_spin.setChecked(True)
        options_layout.addWidget(self._per_atom_spin)
        options_layout.addWidget(self._uniform_spin)

        uniform_row = QHBoxLayout()
        uniform_row.addSpacing(24)
        uniform_row.addWidget(QLabel("Moment / atom"))
        self._uniform_moment = _real_spin_box("uniformInitialMoment")
        self._uniform_moment.setValue(
            settings.spin.uniform_initial_moment
            if settings.spin.uniform_initial_moment is not None
            else 0.0
        )
        uniform_row.addWidget(self._uniform_moment)
        uniform_row.addStretch(1)
        options_layout.addLayout(uniform_row)
        warning = QLabel(
            "Advanced: applies to every atom without an explicit initial_moment; "
            "use deliberately.",
            self._spin_options,
        )
        warning.setWordWrap(True)
        warning.setProperty("uiTone", "warning")
        options_layout.addWidget(warning)

        fixed_row = QHBoxLayout()
        self._fixed_spin = QCheckBox(
            "Fix total spin moment (2S = Nup - Ndown)",
            self._spin_options,
        )
        self._fixed_spin.setObjectName("fixedSpinEnabled")
        self._fixed_spin.setChecked(settings.spin.fixed_spin_moment is not None)
        fixed_row.addWidget(self._fixed_spin)
        self._fixed_spin_value = _real_spin_box("fixedSpinMoment")
        self._fixed_spin_value.setValue(
            settings.spin.fixed_spin_moment
            if settings.spin.fixed_spin_moment is not None
            else 0.0
        )
        fixed_row.addWidget(self._fixed_spin_value)
        options_layout.addLayout(fixed_row)
        layout.addWidget(self._spin_options)
        parent_layout.addWidget(group)

    def _build_charge_group(
        self,
        settings: AimsOptimizationSettings,
        parent_layout: QVBoxLayout,
    ) -> None:
        group = QGroupBox("Charge", self)
        layout = QVBoxLayout(group)
        self._charged_system = QCheckBox("Charged system", group)
        self._charged_system.setObjectName("chargedSystem")
        self._charged_system.setChecked(settings.total_charge != 0.0)
        layout.addWidget(self._charged_system)
        row = QHBoxLayout()
        row.addSpacing(18)
        row.addWidget(QLabel("Total charge"))
        self._total_charge = _real_spin_box("totalCharge")
        self._total_charge.setValue(settings.total_charge)
        row.addWidget(self._total_charge)
        row.addStretch(1)
        layout.addLayout(row)
        parent_layout.addWidget(group)

    def _build_atom_overrides_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("Atom overrides", self)
        layout = QVBoxLayout(group)
        explanation = QLabel(
            "Only atoms with explicit calculation settings are listed. "
            "Initial charge guess is independent of total system charge.",
            group,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self._override_list = QListWidget(group)
        self._override_list.setObjectName("atomOverrideList")
        layout.addWidget(self._override_list)
        button_row = QHBoxLayout()
        self._add_override_button = QPushButton("Add atom override", group)
        self._edit_override_button = QPushButton("Edit", group)
        self._remove_override_button = QPushButton("Remove", group)
        button_row.addWidget(self._add_override_button)
        button_row.addWidget(self._edit_override_button)
        button_row.addWidget(self._remove_override_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        self._add_override_button.clicked.connect(self._add_override)
        self._edit_override_button.clicked.connect(self._edit_override)
        self._remove_override_button.clicked.connect(self._remove_override)
        self._override_list.itemDoubleClicked.connect(
            lambda _item: self._edit_override()
        )
        parent_layout.addWidget(group)

    def _update_spin_controls(self, _checked: bool | None = None) -> None:
        enabled = self._spin_enabled.isChecked()
        self._spin_state.setText("spin collinear" if enabled else "spin none")
        self._spin_options.setEnabled(enabled)
        self._uniform_moment.setEnabled(enabled and self._uniform_spin.isChecked())
        self._fixed_spin_value.setEnabled(enabled and self._fixed_spin.isChecked())

    def _update_charge_controls(self, _checked: bool | None = None) -> None:
        self._total_charge.setEnabled(self._charged_system.isChecked())

    def _add_override(self) -> None:
        editor = AtomOverrideDialog(
            self._structure,
            allow_initial_moment=self._spin_enabled.isChecked(),
            parent=self,
        )
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        atom_setting = editor.selected_atom_settings()
        if atom_setting.atom_index in self._atom_settings:
            QMessageBox.warning(
                self,
                "Duplicate atom override",
                f"An override for atom {atom_setting.atom_index} already exists.",
            )
            return
        self._atom_settings[atom_setting.atom_index] = atom_setting
        self._refresh_override_list(atom_setting.atom_index)

    def _edit_override(self) -> None:
        item = self._override_list.currentItem()
        if item is None:
            QMessageBox.information(
                self,
                "Edit atom override",
                "Select an atom override to edit.",
            )
            return
        original_index = int(item.data(Qt.ItemDataRole.UserRole))
        editor = AtomOverrideDialog(
            self._structure,
            existing=self._atom_settings[original_index],
            allow_initial_moment=self._spin_enabled.isChecked(),
            parent=self,
        )
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        atom_setting = editor.selected_atom_settings()
        if (
            atom_setting.atom_index != original_index
            and atom_setting.atom_index in self._atom_settings
        ):
            QMessageBox.warning(
                self,
                "Duplicate atom override",
                f"An override for atom {atom_setting.atom_index} already exists.",
            )
            return
        del self._atom_settings[original_index]
        self._atom_settings[atom_setting.atom_index] = atom_setting
        self._refresh_override_list(atom_setting.atom_index)

    def _remove_override(self) -> None:
        item = self._override_list.currentItem()
        if item is None:
            return
        del self._atom_settings[int(item.data(Qt.ItemDataRole.UserRole))]
        self._refresh_override_list()

    def _refresh_override_list(self, select_index: int | None = None) -> None:
        self._override_list.clear()
        for atom_index in sorted(self._atom_settings):
            atom_setting = self._atom_settings[atom_index]
            atom = self._structure[atom_index]
            parts = []
            if atom_setting.species_accuracy is not None:
                parts.append(f"accuracy={atom_setting.species_accuracy.value}")
            if atom_setting.initial_moment is not None:
                parts.append(f"moment={atom_setting.initial_moment!r}")
            if atom_setting.initial_charge is not None:
                parts.append(
                    f"initial_charge={atom_setting.initial_charge!r}"
                )
            self._override_list.addItem(
                f"{atom.element}{atom_index}    " + ", ".join(parts)
            )
            item = self._override_list.item(self._override_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, atom_index)
            if atom_index == select_index:
                self._override_list.setCurrentItem(item)

    def _collect_settings(self) -> AimsOptimizationSettings:
        spin_enabled = self._spin_enabled.isChecked()
        if spin_enabled:
            initialization_mode = (
                SpinInitializationMode.UNIFORM_DEFAULT
                if self._uniform_spin.isChecked()
                else SpinInitializationMode.PER_ATOM
            )
            uniform_initial_moment = (
                self._uniform_moment.value()
                if initialization_mode
                is SpinInitializationMode.UNIFORM_DEFAULT
                else None
            )
            fixed_spin_moment = (
                self._fixed_spin_value.value()
                if self._fixed_spin.isChecked()
                else None
            )
        else:
            initialization_mode = None
            uniform_initial_moment = None
            fixed_spin_moment = None
        return AimsOptimizationSettings(
            xc=self._xc.currentData(),
            vdw=self._vdw.currentData(),
            relativity=self._relativity.currentData(),
            species_accuracy=self._species_accuracy.currentData(),
            force_threshold=self._force_threshold.value(),
            output_dipole=self._output_dipole.isChecked(),
            orbital_cubes=OrbitalCubeOutputSettings(
                frontier_orbitals=tuple(
                    orbital
                    for orbital in FRONTIER_ORBITAL_ORDER
                    if self._frontier_orbital_checks[orbital].isChecked()
                ),
                eigenstate_indices=parse_eigenstate_indices_text(
                    self._orbital_eigenstates.text()
                    if self._additional_orbitals_enabled.isChecked()
                    else ""
                ),
                grid_spacing_angstrom=_optional_float(
                    self._orbital_grid_spacing,
                    "Cube grid spacing",
                ),
            ),
            total_charge=(
                self._total_charge.value()
                if self._charged_system.isChecked()
                else 0.0
            ),
            spin=SpinSettings(
                enabled=spin_enabled,
                initialization_mode=initialization_mode,
                uniform_initial_moment=uniform_initial_moment,
                fixed_spin_moment=fixed_spin_moment,
            ),
            atom_settings=tuple(
                self._atom_settings[index]
                for index in sorted(self._atom_settings)
            ),
        )

    def _validate_and_accept(self) -> None:
        try:
            self._accepted_settings = self._collect_settings()
        except (AimsSettingsValidationError, OrbitalCubeValidationError) as error:
            QMessageBox.critical(self, "Invalid FHI-aims settings", str(error))
            return
        self.accept()


class AtomOverrideDialog(QDialog):
    """Edit one compact zero-based atom override record."""

    def __init__(
        self,
        structure: MolecularStructure,
        *,
        existing: AtomAimsSettings | None = None,
        allow_initial_moment: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._selected: AtomAimsSettings | None = None
        self.setWindowTitle("Atom override")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._atom = QComboBox(self)
        self._atom.setObjectName("overrideAtom")
        for atom in structure:
            self._atom.addItem(f"{atom.element}{atom.index}", atom.index)
        if existing is not None:
            self._atom.setCurrentIndex(self._atom.findData(existing.atom_index))
        form.addRow("Atom", self._atom)

        self._accuracy = QComboBox(self)
        self._accuracy.setObjectName("overrideAccuracy")
        self._accuracy.addItem("Inherit", None)
        for label, value in _ACCURACY_CHOICES:
            self._accuracy.addItem(label, value)
        if existing is not None and existing.species_accuracy is not None:
            self._accuracy.setCurrentIndex(
                self._accuracy.findData(existing.species_accuracy)
            )
        form.addRow("Species accuracy", self._accuracy)

        moment_widget = QWidget(self)
        moment_layout = QHBoxLayout(moment_widget)
        moment_layout.setContentsMargins(0, 0, 0, 0)
        self._moment_enabled = QCheckBox("Set", moment_widget)
        self._moment_enabled.setObjectName("overrideMomentEnabled")
        self._moment = _real_spin_box("overrideInitialMoment")
        if existing is not None and existing.initial_moment is not None:
            self._moment_enabled.setChecked(True)
            self._moment.setValue(existing.initial_moment)
        self._moment_enabled.setEnabled(allow_initial_moment)
        self._moment.setEnabled(
            allow_initial_moment and self._moment_enabled.isChecked()
        )
        if not allow_initial_moment:
            moment_widget.setToolTip(
                "Enable spin polarization before setting initial moments."
            )
        self._moment_enabled.toggled.connect(
            lambda checked: self._moment.setEnabled(
                allow_initial_moment and checked
            )
        )
        moment_layout.addWidget(self._moment_enabled)
        moment_layout.addWidget(self._moment)
        form.addRow("Initial moment", moment_widget)

        charge_widget = QWidget(self)
        charge_layout = QHBoxLayout(charge_widget)
        charge_layout.setContentsMargins(0, 0, 0, 0)
        self._charge_enabled = QCheckBox("Set", charge_widget)
        self._charge_enabled.setObjectName("overrideChargeEnabled")
        self._charge = _real_spin_box("overrideInitialCharge")
        if existing is not None and existing.initial_charge is not None:
            self._charge_enabled.setChecked(True)
            self._charge.setValue(existing.initial_charge)
        self._charge.setEnabled(self._charge_enabled.isChecked())
        self._charge_enabled.toggled.connect(self._charge.setEnabled)
        charge_layout.addWidget(self._charge_enabled)
        charge_layout.addWidget(self._charge)
        form.addRow("Initial charge guess", charge_widget)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_atom_settings(self) -> AtomAimsSettings:
        if self._selected is None:
            raise RuntimeError("atom override dialog was not accepted")
        return self._selected

    def _validate_and_accept(self) -> None:
        try:
            self._selected = AtomAimsSettings(
                atom_index=self._atom.currentData(),
                species_accuracy=self._accuracy.currentData(),
                initial_moment=(
                    self._moment.value()
                    if self._moment_enabled.isChecked()
                    else None
                ),
                initial_charge=(
                    self._charge.value()
                    if self._charge_enabled.isChecked()
                    else None
                ),
            )
        except AimsSettingsValidationError as error:
            QMessageBox.critical(self, "Invalid atom override", str(error))
            return
        self.accept()


def _enum_combo(
    choices: tuple[tuple[str, object], ...],
    selected: object,
    object_name: str,
) -> QComboBox:
    combo = QComboBox()
    combo.setObjectName(object_name)
    for label, value in choices:
        combo.addItem(label, value)
    selected_index = combo.findData(selected)
    if selected_index < 0:
        raise ValueError(f"unsupported initial choice for {object_name}")
    combo.setCurrentIndex(selected_index)
    return combo


def _real_spin_box(object_name: str) -> QDoubleSpinBox:
    spin_box = QDoubleSpinBox()
    spin_box.setObjectName(object_name)
    spin_box.setDecimals(6)
    spin_box.setRange(-1000.0, 1000.0)
    spin_box.setKeyboardTracking(False)
    return spin_box


def _optional_float(field: QLineEdit, label: str) -> float | None:
    text = field.text().strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise OrbitalCubeValidationError(f"{label} must be numeric") from None


def _format_edit_real(value: float) -> str:
    text = f"{float(value):.15g}"
    if "e" not in text and "E" not in text:
        return text
    mantissa, exponent = text.lower().split("e")
    return f"{mantissa}E{int(exponent)}"
