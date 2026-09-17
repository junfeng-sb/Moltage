"""Structured ORCA stage dialogs and one remote submission worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QRunnable, QSize, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from moltage.app.orca_submission import (
    OrcaFrequencySubmissionRequest,
    OrcaOptimizationSubmissionRequest,
    OrcaSubmissionService,
)
from moltage.app.orca_wbl import OrcaWblRequest, OrcaWblService
from moltage.domain.scheduler import scheduler_display_name
from moltage.domain.server_profile import ServerProfile
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.orca.batch import orca_memory_advisory
from moltage.orca.catalog import (
    OrcaBasis,
    OrcaCoordinateSystem,
    OrcaDispersion,
    OrcaFrequencyMode,
    OrcaMethod,
    OrcaOptimizationConvergence,
    OrcaScfConvergence,
    method_capability,
    methods_for_version,
)
from moltage.orca.settings import (
    OrcaFrequencySettings,
    OrcaOptimizationSettings,
    OrcaSettingsError,
    bases_for_structure,
)
from moltage.orca.wbl import (
    OrcaWblContactSettings,
    OrcaWblError,
    OrcaWblSettings,
    WblContactSubspaceMode,
    WblLinkerKind,
    WblParameterStatus,
    detect_wbl_contacts,
    resolve_automatic_contact_subspace,
)
from moltage.orca.wbl_defaults import load_default_wbl_ui_defaults


def _enum_combo(parent: QWidget, name: str, values) -> QComboBox:
    combo = QComboBox(parent)
    combo.setObjectName(name)
    for value in values:
        combo.addItem(value.value, value)
    return combo


def _resource_spin(
    parent: QWidget,
    name: str,
    value: int,
    *,
    maximum: int = 1_000_000,
) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setObjectName(name)
    spin.setRange(1, maximum)
    spin.setValue(value)
    return spin


class OrcaOptimizationSettingsDialog(QDialog):
    """Collect reviewed ORCA optimization choices without scientific defaults."""

    def __init__(
        self,
        structure: MolecularStructure,
        profile: ServerProfile,
        parent: QWidget | None = None,
        *,
        initial_settings: OrcaOptimizationSettings | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(structure, MolecularStructure) or not structure:
            raise ValueError("ORCA optimization requires a molecular structure")
        if not isinstance(profile, ServerProfile):
            raise TypeError("profile must be a ServerProfile")
        if profile.execution_preset is None:
            raise ValueError("Configure shared Cluster settings before ORCA optimization")
        if profile.orca_runtime is None:
            raise ValueError("Configure and validate ORCA for this server profile")
        if initial_settings is not None and not isinstance(
            initial_settings,
            OrcaOptimizationSettings,
        ):
            raise TypeError("initial_settings must be OrcaOptimizationSettings or None")
        self._structure = structure
        self._profile = profile
        self._settings: OrcaOptimizationSettings | None = None
        preset = profile.execution_preset
        family = profile.orca_runtime.version_evidence.version_family

        self.setWindowTitle("ORCA Molecule Optimization")
        self.setMinimumWidth(660)
        layout = QVBoxLayout(self)

        runtime = QLabel(
            f"{profile.name}: {profile.orca_runtime.executable_path}\n"
            f"Verified ORCA version: {profile.orca_runtime.version_evidence.version or 'unverified'}",
            self,
        )
        runtime.setObjectName("orcaOptimizationRuntime")
        runtime.setWordWrap(True)
        layout.addWidget(runtime)

        scientific = QGroupBox("ORCA input", self)
        form = QFormLayout(scientific)
        self._method = QComboBox(self)
        self._method.setObjectName("orcaOptimizationMethod")
        self._method.addItem("Select method...", None)
        for method in methods_for_version(family):
            self._method.addItem(method.value, method)
        self._basis = QComboBox(self)
        self._basis.setObjectName("orcaOptimizationBasis")
        self._basis.addItem("Select basis...", None)
        for basis in bases_for_structure(structure):
            self._basis.addItem(basis.value, basis)
        self._dispersion = _enum_combo(
            self, "orcaOptimizationDispersion", OrcaDispersion
        )
        self._optimization = _enum_combo(
            self,
            "orcaOptimizationConvergence",
            OrcaOptimizationConvergence,
        )
        self._optimization.setCurrentIndex(
            self._optimization.findData(OrcaOptimizationConvergence.OPT)
        )
        self._coordinates = _enum_combo(
            self, "orcaOptimizationCoordinates", OrcaCoordinateSystem
        )
        self._coordinates.setCurrentIndex(
            self._coordinates.findData(OrcaCoordinateSystem.REDUNDANT)
        )
        self._scf = _enum_combo(self, "orcaOptimizationScf", OrcaScfConvergence)
        self._scf.setCurrentIndex(self._scf.findData(OrcaScfConvergence.DEFAULT))
        self._charge = QSpinBox(self)
        self._charge.setObjectName("orcaOptimizationCharge")
        self._charge.setRange(-100, 100)
        self._charge.setValue(0)
        self._multiplicity = QSpinBox(self)
        self._multiplicity.setObjectName("orcaOptimizationMultiplicity")
        self._multiplicity.setRange(1, 100)
        self._multiplicity.setValue(1)
        form.addRow("Method:", self._method)
        form.addRow("Basis:", self._basis)
        form.addRow("Dispersion:", self._dispersion)
        form.addRow("Optimization:", self._optimization)
        form.addRow("Coordinates:", self._coordinates)
        form.addRow("SCF convergence:", self._scf)
        form.addRow("Charge:", self._charge)
        form.addRow("Multiplicity:", self._multiplicity)
        layout.addWidget(scientific)

        resources = QGroupBox("Job resources", self)
        resource_form = QFormLayout(resources)
        self._nodes = _resource_spin(self, "orcaOptimizationNodes", preset.nodes)
        self._processes = _resource_spin(
            self, "orcaOptimizationProcesses", preset.ntasks
        )
        self._runtime = _resource_spin(
            self, "orcaOptimizationRuntimeMinutes", preset.runtime_minutes
        )
        self._memory = _resource_spin(
            self, "orcaOptimizationSchedulerMemoryGb", preset.memory_gb
        )
        self._use_max_core = QCheckBox("Write %maxcore", self)
        self._use_max_core.setObjectName("orcaOptimizationUseMaxCore")
        self._max_core = _resource_spin(self, "orcaOptimizationMaxCoreMb", 4096)
        self._max_core.setEnabled(False)
        max_core_row = QWidget(self)
        max_core_layout = QVBoxLayout(max_core_row)
        max_core_layout.setContentsMargins(0, 0, 0, 0)
        max_core_layout.addWidget(self._use_max_core)
        max_core_layout.addWidget(self._max_core)
        resource_form.addRow("Nodes:", self._nodes)
        resource_form.addRow("ORCA processes:", self._processes)
        resource_form.addRow("Maximum runtime (minutes):", self._runtime)
        resource_form.addRow("Scheduler memory (GB):", self._memory)
        resource_form.addRow("ORCA memory per process (MB):", max_core_row)
        self._memory_note = QLabel(self)
        self._memory_note.setObjectName("orcaOptimizationMemoryAdvisory")
        self._memory_note.setWordWrap(True)
        resource_form.addRow("", self._memory_note)
        layout.addWidget(resources)

        explanation = QLabel(
            "%MaxCore is optional. If omitted, ORCA uses its own documented default; "
            "when provided, it is MB per ORCA process and is not a hard memory limit.",
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._method.currentIndexChanged.connect(self._method_changed)
        self._use_max_core.toggled.connect(self._max_core.setEnabled)
        for widget in (
            self._nodes,
            self._processes,
            self._runtime,
            self._memory,
            self._max_core,
        ):
            widget.valueChanged.connect(self._update_memory_advisory)
        self._use_max_core.toggled.connect(self._update_memory_advisory)
        if initial_settings is not None:
            self._apply_initial_settings(initial_settings)
        self._method_changed()
        self._update_memory_advisory()

    def _apply_initial_settings(self, settings: OrcaOptimizationSettings) -> None:
        """Populate a resubmission form from persisted structured settings."""

        for combo, value, label in (
            (self._method, settings.method, "method"),
            (self._basis, settings.basis, "basis"),
            (self._dispersion, settings.dispersion, "dispersion"),
            (
                self._optimization,
                settings.optimization_convergence,
                "optimization convergence",
            ),
            (self._coordinates, settings.coordinate_system, "coordinate system"),
            (self._scf, settings.scf_convergence, "SCF convergence"),
        ):
            index = combo.findData(value)
            if index < 0:
                raise ValueError(f"Persisted ORCA {label} is unavailable in this runtime")
            combo.setCurrentIndex(index)
        self._charge.setValue(settings.charge)
        self._multiplicity.setValue(settings.multiplicity)
        self._nodes.setValue(settings.scheduler_nodes)
        self._processes.setValue(settings.process_count)
        self._runtime.setValue(settings.runtime_minutes)
        self._memory.setValue(settings.scheduler_memory_gb)
        self._use_max_core.setChecked(settings.max_core_mb is not None)
        if settings.max_core_mb is not None:
            self._max_core.setValue(settings.max_core_mb)

    def selected_settings(self) -> OrcaOptimizationSettings:
        if self._settings is None:
            raise RuntimeError("ORCA optimization settings were not confirmed")
        return self._settings

    def _build_settings(self) -> OrcaOptimizationSettings:
        return OrcaOptimizationSettings(
            method=self._method.currentData(),
            basis=self._basis.currentData(),
            dispersion=self._dispersion.currentData(),
            charge=self._charge.value(),
            multiplicity=self._multiplicity.value(),
            optimization_convergence=self._optimization.currentData(),
            coordinate_system=self._coordinates.currentData(),
            scf_convergence=self._scf.currentData(),
            process_count=self._processes.value(),
            max_core_mb=(
                self._max_core.value() if self._use_max_core.isChecked() else None
            ),
            version_family=self._profile.orca_runtime.version_evidence.version_family,
            scheduler_nodes=self._nodes.value(),
            runtime_minutes=self._runtime.value(),
            scheduler_memory_gb=self._memory.value(),
        )

    @Slot()
    def _method_changed(self, *_ignored) -> None:
        method = self._method.currentData()
        if method is None:
            self._basis.setEnabled(True)
            self._dispersion.setEnabled(True)
            return
        capability = method_capability(method)
        self._basis.setEnabled(capability.requires_basis)
        self._dispersion.setEnabled(capability.permits_dispersion)
        if not capability.requires_basis:
            self._basis.setCurrentIndex(0)
        if not capability.permits_dispersion:
            self._dispersion.setCurrentIndex(
                self._dispersion.findData(OrcaDispersion.NONE)
            )

    @Slot()
    def _update_memory_advisory(self, *_ignored) -> None:
        try:
            settings = self._build_settings()
            preset = replace(
                self._profile.execution_preset,
                nodes=settings.scheduler_nodes,
                ntasks=settings.process_count,
                cpus_per_task=1,
                runtime_minutes=settings.runtime_minutes,
                memory_gb=settings.scheduler_memory_gb,
                omp_num_threads=1,
            )
            message = orca_memory_advisory(preset, settings)
        except Exception:
            message = None
        self._memory_note.setText(message or "No comparable scheduler-memory conflict detected.")

    @Slot()
    def _validate_and_accept(self) -> None:
        try:
            settings = self._build_settings()
            settings.validate_for_structure(self._structure)
        except (OrcaSettingsError, ValueError) as error:
            QMessageBox.critical(self, "Invalid ORCA settings", str(error))
            return
        self._settings = settings
        self.accept()


class OrcaFrequencySettingsDialog(QDialog):
    """Collect one optional frequency stage while keeping science inherited."""

    def __init__(
        self,
        source: OrcaOptimizationSettings,
        optimized_structure: MolecularStructure,
        profile: ServerProfile,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if source.method is None:
            raise ValueError("Frequency requires a configured ORCA optimization")
        if not isinstance(optimized_structure, MolecularStructure) or not optimized_structure:
            raise ValueError("Frequency requires a verified optimized structure")
        if profile.execution_preset is None or profile.orca_runtime is None:
            raise ValueError("Frequency requires shared cluster settings and verified ORCA")
        self._source = source
        self._profile = profile
        self._settings: OrcaFrequencySettings | None = None
        preset = profile.execution_preset
        capability = method_capability(source.method)

        self.setWindowTitle("ORCA Frequency Verification")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        inherited = QGroupBox("Inherited optimization settings (read-only)", self)
        inherited_form = QFormLayout(inherited)
        for label, value in (
            ("Method:", source.method.value),
            ("Basis:", source.basis.value if source.basis else "Built into method"),
            ("Dispersion:", source.dispersion.value),
            ("Charge:", str(source.charge)),
            ("Multiplicity:", str(source.multiplicity)),
        ):
            inherited_form.addRow(label, QLabel(value, self))
        layout.addWidget(inherited)

        form = QFormLayout()
        self._mode = QComboBox(self)
        self._mode.setObjectName("orcaFrequencyMode")
        self._mode.addItem("Select frequency mode...", None)
        analytical_index = self._mode.count()
        self._mode.addItem("FREQ — analytical Hessian", OrcaFrequencyMode.FREQ)
        self._mode.model().item(analytical_index).setEnabled(
            capability.analytical_frequency_supported
        )
        self._mode.addItem("NUMFREQ — numerical Hessian", OrcaFrequencyMode.NUMFREQ)
        self._nodes = _resource_spin(self, "orcaFrequencyNodes", preset.nodes)
        self._processes = _resource_spin(self, "orcaFrequencyProcesses", preset.ntasks)
        self._runtime = _resource_spin(
            self, "orcaFrequencyRuntimeMinutes", preset.runtime_minutes
        )
        self._memory = _resource_spin(
            self, "orcaFrequencySchedulerMemoryGb", preset.memory_gb
        )
        self._use_max_core = QCheckBox("Write %maxcore", self)
        self._use_max_core.setObjectName("orcaFrequencyUseMaxCore")
        self._max_core = _resource_spin(self, "orcaFrequencyMaxCoreMb", 4096)
        self._max_core.setEnabled(False)
        self._use_max_core.toggled.connect(self._max_core.setEnabled)
        form.addRow("Mode:", self._mode)
        form.addRow("Nodes:", self._nodes)
        form.addRow("ORCA processes:", self._processes)
        form.addRow("Maximum runtime (minutes):", self._runtime)
        form.addRow("Scheduler memory (GB):", self._memory)
        form.addRow("ORCA memory per process (MB):", self._max_core)
        form.addRow("", self._use_max_core)
        layout.addLayout(form)
        warning = QLabel(
            "NUMFREQ performs many displaced-gradient calculations and can cost "
            "substantially more than an optimization. A completed frequency stage "
            "reports only ORCA-reported imaginary modes; it does not prove a global minimum.",
            self,
        )
        warning.setObjectName("orcaFrequencyCostGuidance")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        if not capability.analytical_frequency_supported:
            unsupported = QLabel(
                "FREQ is disabled because the maintained catalog has no analytical-Hessian "
                "support evidence for this method/runtime combination.",
                self,
            )
            unsupported.setWordWrap(True)
            layout.addWidget(unsupported)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_settings(self) -> OrcaFrequencySettings:
        if self._settings is None:
            raise RuntimeError("ORCA frequency settings were not confirmed")
        return self._settings

    @Slot()
    def _validate_and_accept(self) -> None:
        mode = self._mode.currentData()
        if mode is None:
            QMessageBox.critical(self, "Invalid ORCA settings", "Select FREQ or NUMFREQ")
            return
        try:
            self._settings = OrcaFrequencySettings(
                source_optimization=self._source,
                source_optimization_sha256=self._source.scientific_identity_sha256(),
                mode=mode,
                process_count=self._processes.value(),
                max_core_mb=(
                    self._max_core.value() if self._use_max_core.isChecked() else None
                ),
                scheduler_nodes=self._nodes.value(),
                runtime_minutes=self._runtime.value(),
                scheduler_memory_gb=self._memory.value(),
            )
        except OrcaSettingsError as error:
            QMessageBox.critical(self, "Invalid ORCA settings", str(error))
            return
        self.accept()


@dataclass(slots=True)
class _WblContactControls:
    atom: QComboBox
    linker_summary: QLabel
    linker: QComboBox
    gamma0: QDoubleSpinBox
    status: QComboBox
    subspace: QComboBox
    manual_direction: QLineEdit
    manual_aos: QLineEdit
    advanced: QGroupBox
    last_atom_index: int | None = None
    detected_linker: WblLinkerKind | None = None


class _CompactDoubleSpinBox(QDoubleSpinBox):
    """Numeric-only eV editor without fixed-width trailing zeroes."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt override
        return format(value, ".10g")


class _RequiredPositiveEvSpinBox(_CompactDoubleSpinBox):
    """Required positive eV editor with a non-editable empty-state prompt."""

    _EMPTY_TEXT = "Enter value"

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override
        empty = self.value() <= self.minimum()
        if empty:
            self.setSpecialValueText("")
        super().focusInEvent(event)
        if empty:
            QTimer.singleShot(0, self._begin_empty_edit)

    def _begin_empty_edit(self) -> None:
        if not self.hasFocus() or self.value() > self.minimum():
            return
        self.clear()
        self.lineEdit().setCursorPosition(0)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        if self.value() <= self.minimum():
            self.setSpecialValueText(self._EMPTY_TEXT)


_MANUAL_CONTACT_SELECTION = "MANUAL_SELECT_IN_VIEWER"

_SUBSPACE_LABELS = {
    WblContactSubspaceMode.S_3P_DIRECTIONAL: (
        "S valence 3p — along the contact direction"
    ),
    WblContactSubspaceMode.N_2S_2P_DIRECTIONAL: (
        "N valence 2s/2p — along the lone-pair/contact direction"
    ),
    WblContactSubspaceMode.N_2P_NORMAL: (
        "N valence 2p — normal to the local atom plane"
    ),
    WblContactSubspaceMode.MANUAL_AO: "Manual AO numbers",
}


class OrcaWblSettingsDialog(QDialog):
    """Collect explicit WBL hypotheses without inventing coupling values."""

    def __init__(
        self,
        structure: MolecularStructure,
        connectivity: Connectivity,
        parent: QWidget | None = None,
        *,
        contact_atom_selector: Callable[[str], int | None] | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(structure, MolecularStructure) or not structure:
            raise ValueError("ORCA WBL requires a verified optimized structure")
        if not isinstance(connectivity, Connectivity):
            raise TypeError("ORCA WBL requires verified molecular connectivity")
        if connectivity.atom_count != len(structure):
            raise ValueError("ORCA WBL connectivity does not match the structure")
        if sum(atom.element in {"S", "N"} for atom in structure) < 2:
            raise ValueError(
                "ORCA WBL requires at least two selectable S/N contact atoms"
            )
        self._structure = structure
        self._connectivity = connectivity
        self._contact_atom_selector = contact_atom_selector
        self._detected_linkers_by_atom = self._detected_linker_map()
        self._defaults = load_default_wbl_ui_defaults()
        self._settings: OrcaWblSettings | None = None
        self._collapsed_size: QSize | None = None
        self.setWindowTitle("ORCA Wide-Band-Limit Transmission")
        self.setMinimumWidth(960)
        layout = QVBoxLayout(self)

        guidance = QLabel(
            "This stage analyzes the existing optimized ORCA wavefunction. It does "
            "not run SCF or geometry optimization. Γ₀ has no Moltage numeric "
            "default: enter a positive value. The editable Au E<sub>F</sub> starting "
            "value is a generic model hypothesis, not a universal Au surface value. "
            "Results are linker-parameterized WBL hypotheses, not explicit "
            "Au–molecule–Au DFT-NEGF.",
            self,
        )
        guidance.setTextFormat(Qt.TextFormat.RichText)
        guidance.setObjectName("orcaWblGuidance")
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        contacts = QWidget(self)
        contacts_layout = QHBoxLayout(contacts)
        contacts_layout.setContentsMargins(0, 0, 0, 0)
        self._left = self._contact_group("Left contact", "Left", contacts_layout)
        self._right = self._contact_group(
            "Right contact",
            "Right",
            contacts_layout,
            right_contact=True,
        )
        contacts_layout.setStretch(0, 1)
        contacts_layout.setStretch(1, 1)
        layout.addWidget(contacts)
        self._left.gamma0.valueChanged.connect(self._mirror_right_gamma)
        self._same_gamma.toggled.connect(self._right_gamma_link_changed)
        self._same_gamma.setChecked(True)
        self._right_gamma_link_changed(True)

        self._advanced_button = QPushButton("Advanced contact settings…", self)
        self._advanced_button.setObjectName("orcaWblAdvancedToggle")
        self._advanced_button.setCheckable(True)
        self._advanced_button.toggled.connect(self._advanced_toggled)
        layout.addWidget(self._advanced_button)

        self._prefill_detected_contacts()

        scan = QGroupBox("Energy window and sampling", self)
        scan_form = QFormLayout(scan)
        self._fermi = self._energy_number(
            "orcaWblFermiEnergy",
            self._defaults.au_fermi_energy_ev,
            minimum=-100.0,
            maximum=100.0,
        )
        self._fermi.setToolTip(self._defaults.limitations)
        self._energy_min = self._energy_number(
            "orcaWblEnergyMinimum",
            self._defaults.energy_min_relative_ev,
            minimum=-1000.0,
            maximum=1000.0,
        )
        self._energy_max = self._energy_number(
            "orcaWblEnergyMaximum",
            self._defaults.energy_max_relative_ev,
            minimum=-1000.0,
            maximum=1000.0,
        )
        self._energy_step = self._energy_number(
            "orcaWblEnergyStep",
            self._defaults.energy_step_ev,
            minimum=0.000001,
            maximum=100.0,
        )
        scan_form.addRow(self._rich_label("Au Fermi level, E<sub>F</sub>:"), self._fermi)
        scan_form.addRow(
            self._rich_label("Lower energy, E − E<sub>F</sub>:"),
            self._energy_min,
        )
        scan_form.addRow(
            self._rich_label("Upper energy, E − E<sub>F</sub>:"),
            self._energy_max,
        )
        scan_form.addRow("Sampling interval:", self._energy_step)
        layout.addWidget(scan)

        self._validation_message = QLabel(self)
        self._validation_message.setObjectName("orcaWblValidationMessage")
        self._validation_message.setWordWrap(True)
        layout.addWidget(self._validation_message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start Step 2")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._left.gamma0.valueChanged.connect(self._update_required_input_message)
        self._right.gamma0.valueChanged.connect(self._update_required_input_message)
        self._same_gamma.toggled.connect(self._update_required_input_message)
        self._update_required_input_message()
        self._advanced_toggled(False)

    def selected_settings(self) -> OrcaWblSettings:
        if self._settings is None:
            raise RuntimeError("ORCA WBL settings were not confirmed")
        return self._settings

    def _contact_group(
        self,
        title: str,
        prefix: str,
        outer_layout: QHBoxLayout,
        *,
        right_contact: bool = False,
    ) -> _WblContactControls:
        group = QGroupBox(title, self)
        form = QFormLayout(group)
        atom = QComboBox(self)
        atom.setObjectName(f"orcaWbl{prefix}Atom")
        atom.addItem("Select contact atom...", None)
        for item in self._structure:
            if item.element in {"S", "N"}:
                detected = self._detected_linkers_by_atom.get(item.index, ())
                suffix = (
                    f" · {detected[0].value} detected"
                    if len(detected) == 1
                    else ""
                )
                atom.addItem(
                    f"{item.index + 1} — {item.element}{suffix}",
                    item.index,
                )
        atom.insertSeparator(atom.count())
        atom.addItem("Manual select in viewer…", _MANUAL_CONTACT_SELECTION)
        linker_summary = QLabel("Not detected", self)
        linker_summary.setObjectName(f"orcaWbl{prefix}LinkerSummary")
        linker = QComboBox(self)
        linker.setObjectName(f"orcaWbl{prefix}Linker")
        linker.addItem("Select when automatic detection is unavailable...", None)
        for value in WblLinkerKind:
            linker.addItem(value.value, value)
        gamma0 = self._gamma_number(f"orcaWbl{prefix}Gamma0")
        status = QComboBox(self)
        status.setObjectName(f"orcaWbl{prefix}ParameterStatus")
        status.addItem(
            "Model hypothesis (user-supplied)",
            WblParameterStatus.HYPOTHESIS,
        )
        status.addItem(
            "Calibrated against external data",
            WblParameterStatus.CALIBRATED,
        )
        status.setToolTip(
            "Describes the evidence behind your Γ₀ value; it does not change the formula."
        )
        subspace = QComboBox(self)
        subspace.setObjectName(f"orcaWbl{prefix}Subspace")
        subspace.addItem("Auto — choose a detected contact", WblContactSubspaceMode.AUTO)
        for mode in (
            WblContactSubspaceMode.S_3P_DIRECTIONAL,
            WblContactSubspaceMode.N_2S_2P_DIRECTIONAL,
            WblContactSubspaceMode.N_2P_NORMAL,
            WblContactSubspaceMode.MANUAL_AO,
        ):
            subspace.addItem(_SUBSPACE_LABELS[mode], mode)
        subspace.setToolTip(
            "Chooses the contact atom-orbital projector used for Löwdin weights. "
            "Auto derives the valence subspace and direction from linker geometry."
        )
        manual_direction = QLineEdit(self)
        manual_direction.setObjectName(f"orcaWbl{prefix}Direction")
        manual_direction.setPlaceholderText("Optional unit direction: x, y, z")
        manual_direction.setToolTip(
            "Advanced override for the contact-orbital direction in molecular XYZ "
            "coordinates. Leave empty to use the displayed automatic direction model."
        )
        manual_aos = QLineEdit(self)
        manual_aos.setObjectName(f"orcaWbl{prefix}ManualAos")
        manual_aos.setPlaceholderText(
            "Required only for Manual AO numbers; 1-based, comma-separated"
        )
        for advanced_editor in (
            linker,
            status,
            subspace,
            manual_direction,
            manual_aos,
        ):
            advanced_editor.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
        form.addRow("Contact atom:", atom)
        form.addRow("Detected linker:", linker_summary)
        if right_contact:
            gamma_row = QWidget(group)
            gamma_layout = QHBoxLayout(gamma_row)
            gamma_layout.setContentsMargins(0, 0, 0, 0)
            gamma_layout.addWidget(gamma0, 1)
            self._same_gamma = QCheckBox("Same as left", gamma_row)
            self._same_gamma.setObjectName("orcaWblSameGamma")
            self._same_gamma.setToolTip(
                "Mirror the left user-entered Γ₀ value; uncheck to enter a separate value."
            )
            gamma_layout.addWidget(self._same_gamma)
            form.addRow(self._rich_label("Γ<sub>0</sub>:"), gamma_row)
        else:
            form.addRow(self._rich_label("Γ<sub>0</sub>:"), gamma0)

        advanced = QGroupBox(f"{title} — Advanced", group)
        advanced.setObjectName(f"orcaWbl{prefix}Advanced")
        advanced_form = QFormLayout(advanced)
        advanced_form.addRow("Linker override:", linker)
        advanced_form.addRow(self._rich_label("Γ<sub>0</sub> evidence:"), status)
        advanced_form.addRow("Contact orbital projection:", subspace)
        advanced_form.addRow("Projection direction override:", manual_direction)
        advanced_form.addRow("Manual AO numbers:", manual_aos)
        form.addRow(advanced)
        advanced.setVisible(False)
        outer_layout.addWidget(group)
        controls = _WblContactControls(
            atom,
            linker_summary,
            linker,
            gamma0,
            status,
            subspace,
            manual_direction,
            manual_aos,
            advanced,
        )
        atom.currentIndexChanged.connect(
            lambda _index, current=controls, side=prefix.lower(): (
                self._contact_atom_changed(current, side)
            )
        )
        linker.currentIndexChanged.connect(
            lambda _index, current=controls: self._linker_changed(current)
        )
        subspace.currentIndexChanged.connect(
            lambda _index, current=controls: self._subspace_changed(current)
        )
        self._subspace_changed(controls)
        return controls

    def _gamma_number(self, name: str) -> QDoubleSpinBox:
        edit = _RequiredPositiveEvSpinBox(self)
        edit.setObjectName(name)
        edit.setDecimals(8)
        edit.setRange(0.0, 1000.0)
        edit.setSingleStep(0.01)
        edit.setSuffix(" eV")
        edit.setSpecialValueText("Enter value")
        edit.setValue(0.0)
        edit.setToolTip("Required positive coupling in eV; letters are not accepted.")
        return edit

    def _energy_number(
        self,
        name: str,
        value: float,
        *,
        minimum: float,
        maximum: float,
    ) -> QDoubleSpinBox:
        edit = _CompactDoubleSpinBox(self)
        edit.setObjectName(name)
        edit.setDecimals(8)
        edit.setRange(minimum, maximum)
        edit.setSingleStep(0.01)
        edit.setSuffix(" eV")
        edit.setValue(value)
        return edit

    def _rich_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.RichText)
        return label

    def _detected_linker_map(self) -> dict[int, tuple[WblLinkerKind, ...]]:
        grouped: dict[int, list[WblLinkerKind]] = {}
        for item in detect_wbl_contacts(self._structure, self._connectivity):
            grouped.setdefault(item.atom_index, []).append(item.linker)
        return {
            atom_index: tuple(sorted(set(linkers), key=lambda value: value.value))
            for atom_index, linkers in grouped.items()
        }

    def _prefill_detected_contacts(self) -> None:
        if len(self._detected_linkers_by_atom) != 2 or any(
            len(linkers) != 1
            for linkers in self._detected_linkers_by_atom.values()
        ):
            return
        unambiguous = tuple(
            (atom_index, linkers[0])
            for atom_index, linkers in sorted(self._detected_linkers_by_atom.items())
        )
        for controls, (atom_index, _linker) in zip(
            (self._left, self._right),
            unambiguous,
            strict=True,
        ):
            controls.atom.setCurrentIndex(controls.atom.findData(atom_index))

    @Slot(bool)
    def _advanced_toggled(self, visible: bool) -> None:
        if visible and self.isVisible():
            self._collapsed_size = self.size()
        for controls in (self._left, self._right):
            controls.advanced.setVisible(visible)
        self._advanced_button.setText(
            "Hide advanced contact settings"
            if visible
            else "Advanced contact settings…"
        )
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        hint = self.sizeHint()
        if visible:
            self.resize(
                max(self.width(), hint.width()),
                max(self.height(), hint.height()),
            )
            return
        target = QSize(self._collapsed_size or hint)
        self._collapsed_size = None
        QTimer.singleShot(0, lambda: self._restore_collapsed_size(target))

    def _restore_collapsed_size(self, target: QSize) -> None:
        if self._advanced_button.isChecked():
            return
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.resize(
            max(target.width(), self.minimumWidth()),
            max(target.height(), self.minimumHeight()),
        )

    @Slot(float)
    def _mirror_right_gamma(self, value: float) -> None:
        if self._same_gamma.isChecked():
            self._right.gamma0.setValue(value)

    @Slot(bool)
    def _right_gamma_link_changed(self, linked: bool) -> None:
        self._right.gamma0.setEnabled(not linked)
        if linked:
            self._right.gamma0.setValue(self._left.gamma0.value())

    def _contact_atom_changed(
        self,
        controls: _WblContactControls,
        side: str,
    ) -> None:
        selected = controls.atom.currentData()
        if selected == _MANUAL_CONTACT_SELECTION:
            self._choose_contact_in_viewer(controls, side)
            return
        if not isinstance(selected, int):
            controls.last_atom_index = None
            controls.detected_linker = None
            controls.linker_summary.setText("Not selected")
            controls.linker.setCurrentIndex(0)
            self._refresh_auto_subspace(controls)
            return
        controls.last_atom_index = selected
        detected = self._detected_linkers_by_atom.get(selected, ())
        if len(detected) == 1:
            controls.detected_linker = detected[0]
            controls.linker_summary.setText(f"{detected[0].value} (automatic)")
            controls.linker.setCurrentIndex(controls.linker.findData(detected[0]))
        elif detected:
            controls.detected_linker = None
            controls.linker_summary.setText("Ambiguous — choose in Advanced")
            controls.linker.setCurrentIndex(0)
        else:
            controls.detected_linker = None
            controls.linker_summary.setText("Not recognized — choose in Advanced")
            controls.linker.setCurrentIndex(0)
        self._refresh_auto_subspace(controls)

    def _choose_contact_in_viewer(
        self,
        controls: _WblContactControls,
        side: str,
    ) -> None:
        previous = controls.last_atom_index
        if self._contact_atom_selector is None:
            QMessageBox.information(
                self,
                "Viewer selection unavailable",
                "Open these settings from a recovered ORCA Geometry workspace to "
                "select a contact atom in the viewer.",
            )
            self._restore_contact_combo(controls, previous)
            return
        self.hide()
        try:
            selected = self._contact_atom_selector(side)
        finally:
            self.show()
            self.raise_()
            self.activateWindow()
        if selected is None:
            self._restore_contact_combo(controls, previous)
            return
        if (
            isinstance(selected, bool)
            or not isinstance(selected, int)
            or selected < 0
            or selected >= len(self._structure)
            or self._structure[selected].element not in {"S", "N"}
        ):
            QMessageBox.warning(
                self,
                "Unsupported WBL contact",
                "Select an S or N atom supported by the current WBL linker models.",
            )
            self._restore_contact_combo(controls, previous)
            return
        self._restore_contact_combo(controls, selected)

    @staticmethod
    def _restore_contact_combo(
        controls: _WblContactControls,
        atom_index: int | None,
    ) -> None:
        index = controls.atom.findData(atom_index)
        controls.atom.setCurrentIndex(index if index >= 0 else 0)

    def _linker_changed(self, controls: _WblContactControls) -> None:
        raw_linker = controls.linker.currentData()
        try:
            linker = WblLinkerKind(raw_linker)
        except (TypeError, ValueError):
            linker = None
        if (
            controls.last_atom_index is not None
            and linker is not None
            and linker is not controls.detected_linker
        ):
            controls.linker_summary.setText(f"{linker.value} (Advanced override)")
        elif linker is controls.detected_linker and linker is not None:
            controls.linker_summary.setText(f"{linker.value} (automatic)")
        self._refresh_auto_subspace(controls)

    def _refresh_auto_subspace(self, controls: _WblContactControls) -> None:
        atom_index = controls.last_atom_index
        raw_linker = controls.linker.currentData()
        try:
            linker = WblLinkerKind(raw_linker)
        except (TypeError, ValueError):
            linker = None
        label = "Auto — choose a detected contact"
        tooltip = "Select a contact/linker before automatic projection can be resolved."
        if isinstance(atom_index, int) and linker is not None:
            try:
                mode, _direction = resolve_automatic_contact_subspace(
                    self._structure,
                    self._connectivity,
                    atom_index,
                    linker,
                )
            except OrcaWblError as error:
                label = "Auto — unresolved; use an Advanced override"
                tooltip = str(error)
            else:
                label = f"Auto — {_SUBSPACE_LABELS[mode]}"
                tooltip = (
                    "Resolved from the selected linker and current molecular geometry."
                )
        controls.subspace.setItemText(0, label)
        controls.subspace.setItemData(
            0,
            tooltip,
            role=Qt.ItemDataRole.ToolTipRole,
        )
        controls.subspace.setToolTip(tooltip)

    def _subspace_changed(self, controls: _WblContactControls) -> None:
        manual = (
            controls.subspace.currentData()
            == WblContactSubspaceMode.MANUAL_AO
        )
        controls.manual_aos.setEnabled(manual)
        if not manual:
            controls.manual_aos.clear()

    def _contact_settings(
        self,
        controls: _WblContactControls,
    ) -> OrcaWblContactSettings:
        atom_index = controls.atom.currentData()
        linker = controls.linker.currentData()
        if atom_index is None or linker is None:
            raise OrcaWblError("Select a contact atom and linker for both sides")
        linker = WblLinkerKind(linker)
        expected = "S" if linker in {WblLinkerKind.SH, WblLinkerKind.SME} else "N"
        if self._structure[atom_index].element != expected:
            raise OrcaWblError(
                f"{linker.value} requires a {expected} contact atom"
            )
        direction_text = controls.manual_direction.text().strip()
        direction = None
        if direction_text:
            fields = tuple(part.strip() for part in direction_text.split(","))
            if len(fields) != 3:
                raise OrcaWblError("manual direction requires exactly x, y, z")
            direction = tuple(float(value) for value in fields)
        ao_text = controls.manual_aos.text().strip()
        manual_aos = ()
        if ao_text:
            parsed = tuple(int(part.strip()) for part in ao_text.split(","))
            if any(value < 1 for value in parsed):
                raise OrcaWblError("manual AO numbers are 1-based positive integers")
            manual_aos = tuple(value - 1 for value in parsed)
        return OrcaWblContactSettings(
            atom_index=atom_index,
            linker=linker,
            gamma0_ev=controls.gamma0.value(),
            parameter_status=WblParameterStatus(controls.status.currentData()),
            subspace_mode=WblContactSubspaceMode(
                controls.subspace.currentData()
            ),
            manual_direction=direction,
            manual_ao_indices=manual_aos,
        )

    @Slot()
    def _validate_and_accept(self) -> None:
        if self._left.gamma0.value() <= 0.0:
            self._reject_missing_gamma(self._left.gamma0, "left")
            return
        if not self._same_gamma.isChecked() and self._right.gamma0.value() <= 0.0:
            self._reject_missing_gamma(self._right.gamma0, "right")
            return
        try:
            settings = OrcaWblSettings(
                self._contact_settings(self._left),
                self._contact_settings(self._right),
                self._fermi.value(),
                self._energy_min.value(),
                self._energy_max.value(),
                self._energy_step.value(),
            )
            settings.require_runnable(len(self._structure))
        except (OrcaWblError, ValueError) as error:
            self._validation_message.setText(
                f"WBL cannot start: {error}"
            )
            QMessageBox.critical(self, "Invalid ORCA WBL settings", str(error))
            return
        self._settings = settings
        self.accept()

    @Slot()
    def _update_required_input_message(self, *_ignored) -> None:
        missing = []
        if self._left.gamma0.value() <= 0.0:
            missing.append("left Γ₀")
        if not self._same_gamma.isChecked() and self._right.gamma0.value() <= 0.0:
            missing.append("right Γ₀")
        if missing:
            self._validation_message.setText(
                "Required before WBL can start: enter a positive value for "
                + " and ".join(missing)
                + ". Moltage does not assume a coupling value."
            )
            return
        self._validation_message.setText(
            "Required coupling input is complete. Start Step 2 to begin the WBL "
            "transmission analysis."
        )

    def _reject_missing_gamma(
        self,
        editor: QDoubleSpinBox,
        side: str,
    ) -> None:
        message = (
            f"Enter a positive {side} Γ₀ value in eV. Moltage does not provide "
            "a scientific default for linker coupling."
        )
        self._validation_message.setText(f"WBL cannot start: {message}")
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.selectAll()
        QMessageBox.critical(self, "Required WBL coupling", message)


class OrcaSubmissionConfirmationDialog(QDialog):
    """Final explicit authorization with deterministic input/script previews."""

    def __init__(
        self,
        profile: ServerProfile,
        project_name: str,
        stage_label: str,
        input_text: str,
        script_text: str,
        *,
        temporary_password_required: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._temporary_password_required = temporary_password_required
        self.setWindowTitle(
            f"Confirm REAL {scheduler_display_name(profile.execution_preset.scheduler_kind)} ORCA Submission"
        )
        self.setMinimumSize(760, 650)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "Submit will create or update a real remote project and make at most "
            "one scheduler submission request.",
            self,
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        form.addRow("Server:", QLabel(profile.name, self))
        form.addRow("Project:", QLabel(project_name, self))
        form.addRow("Stage:", QLabel(stage_label, self))
        form.addRow("ORCA executable:", QLabel(profile.orca_runtime.executable_path, self))
        layout.addLayout(form)
        preview = QPlainTextEdit(self)
        preview.setObjectName("orcaSubmissionPreview")
        preview.setReadOnly(True)
        preview.setPlainText(
            "----- ORCA input -----\n"
            + input_text
            + "\n----- Scheduler script -----\n"
            + script_text
        )
        layout.addWidget(preview, stretch=1)
        self._password: QLineEdit | None = None
        if temporary_password_required:
            password_form = QFormLayout()
            self._password = QLineEdit(self)
            self._password.setObjectName("orcaSubmissionTemporaryPassword")
            self._password.setEchoMode(QLineEdit.EchoMode.Password)
            password_form.addRow("Password:", self._password)
            layout.addLayout(password_form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        submit = buttons.addButton("Submit", QDialogButtonBox.ButtonRole.AcceptRole)
        submit.setObjectName("confirmRealOrcaSubmission")
        submit.clicked.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def take_temporary_password(self) -> str | None:
        if self._password is None:
            return None
        value = self._password.text()
        self._password.clear()
        return value or None

    @Slot()
    def _validate_and_accept(self) -> None:
        if self._temporary_password_required and self._password is not None and not self._password.text():
            QMessageBox.critical(self, "Password required", "Enter the password for this submission.")
            return
        self.accept()


class _OrcaSubmissionSignals(QObject):
    progress = Signal(str)
    project_updated = Signal(object)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class OrcaSubmissionWorker(QRunnable):
    """Run one explicitly authorized ORCA submission away from the GUI thread."""

    def __init__(
        self,
        service: OrcaSubmissionService,
        request: OrcaOptimizationSubmissionRequest | OrcaFrequencySubmissionRequest,
    ) -> None:
        super().__init__()
        if not isinstance(service, OrcaSubmissionService):
            raise TypeError("service must be an OrcaSubmissionService")
        if not isinstance(
            request,
            (OrcaOptimizationSubmissionRequest, OrcaFrequencySubmissionRequest),
        ):
            raise TypeError("request must be an ORCA submission request")
        self._service = service
        self._request = request
        self.signals = _OrcaSubmissionSignals()
        # The GUI retains this worker until its queued ``finished`` signal is
        # delivered.  Letting QThreadPool auto-delete the native QRunnable can
        # invalidate the PySide wrapper before that callback consumes it.
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            if isinstance(self._request, OrcaOptimizationSubmissionRequest):
                result = self._service.submit_optimization(
                    self._request, progress=self.signals.progress.emit
                )
            else:
                result = self._service.submit_frequency(
                    self._request, progress=self.signals.progress.emit
                )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit(self)


class OrcaWblWorker(QRunnable):
    """Run one explicitly authorized WBL evidence conversion and analysis."""

    def __init__(self, service: OrcaWblService, request: OrcaWblRequest) -> None:
        super().__init__()
        if not isinstance(service, OrcaWblService):
            raise TypeError("service must be an OrcaWblService")
        if not isinstance(request, OrcaWblRequest):
            raise TypeError("request must be an OrcaWblRequest")
        self._service = service
        self._request = request
        self.signals = _OrcaSubmissionSignals()
        # Keep the native QRunnable alive until the owning GUI removes the
        # worker after queued signal delivery, matching the submission workers.
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.calculate(
                self._request,
                progress=self.signals.progress.emit,
                project_updated=self.signals.project_updated.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit(self)
