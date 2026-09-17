"""Compact Step-3 settings and real-submission confirmation dialogs."""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from moltage.aims.optimization_settings import (
    SpeciesAccuracy,
    SpinInitializationMode,
    SpinSettings,
    XCFunctional,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.aims.orbital_cube import (
    OrbitalCubeOutputSettings,
    format_eigenstate_indices_text,
    parse_eigenstate_indices_text,
)
from moltage.app.transport_convergence import TransportConvergenceContext
from moltage.domain.calculation_project import (
    ProjectStepKind,
    remote_step_directory,
)
from moltage.domain.scheduler import scheduler_display_name
from moltage.domain.server_profile import ServerProfile
from moltage.domain.structure import MolecularStructure
from moltage.gui.project_submission import (
    email_notification_summary,
    resource_summary,
)


_XC_CHOICES = (
    ("PBE", XCFunctional.PBE),
    ("PBE0", XCFunctional.PBE0),
    ("BLYP", XCFunctional.BLYP),
    ("B3LYP", XCFunctional.B3LYP),
    ("revPBE", XCFunctional.REVPBE),
    ("AM05", XCFunctional.AM05),
)

_ACCURACY_CHOICES = (
    ("Light", SpeciesAccuracy.LIGHT),
    ("Tight", SpeciesAccuracy.TIGHT),
    ("Really tight", SpeciesAccuracy.REALLY_TIGHT),
)


class TransportConvergenceSettingsDialog(QDialog):
    """Edit only the reviewed Step-3 scientific surface."""

    def __init__(
        self,
        structure: MolecularStructure,
        initial_settings: TransportConvergenceSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(structure, MolecularStructure) or not structure:
            raise ValueError("Step-3 settings require a non-empty structure")
        settings = initial_settings or TransportConvergenceSettings()
        if not isinstance(settings, TransportConvergenceSettings):
            raise TypeError(
                "initial settings must be TransportConvergenceSettings"
            )
        self._accepted_settings: TransportConvergenceSettings | None = None
        self.setObjectName("transportConvergenceSettingsDialog")
        self.setWindowTitle("Step 3 — Transport Convergence")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._xc = _enum_combo(
            _XC_CHOICES,
            settings.xc,
            "step3XcFunctional",
        )
        form.addRow("XC", self._xc)
        self._species_accuracy = _enum_combo(
            _ACCURACY_CHOICES,
            settings.species_accuracy,
            "step3SpeciesAccuracy",
        )
        form.addRow("Basis preset", self._species_accuracy)

        self._spin_mode = QComboBox(self)
        self._spin_mode.setObjectName("step3Spin")
        self._spin_mode.addItem("None", None)
        self._spin_mode.addItem(
            "Collinear — uniform initial moment",
            SpinInitializationMode.UNIFORM_DEFAULT,
        )
        selected_spin = (
            settings.spin.initialization_mode if settings.spin.enabled else None
        )
        self._spin_mode.setCurrentIndex(self._spin_mode.findData(selected_spin))
        form.addRow("Spin", self._spin_mode)
        self._uniform_moment = _line_edit(
            (
                _format_edit_real(settings.spin.uniform_initial_moment)
                if settings.spin.uniform_initial_moment is not None
                else ""
            ),
            "step3UniformInitialMoment",
        )
        form.addRow("Initial moment / atom", self._uniform_moment)

        self._charge = _line_edit(
            _format_edit_real(settings.total_charge),
            "step3TotalCharge",
        )
        form.addRow("Total charge", self._charge)
        self._occupation_width = _line_edit(
            _format_edit_real(settings.occupation_width),
            "step3OccupationWidth",
        )
        form.addRow("Gaussian width", self._occupation_width)
        self._n_max_pulay = _line_edit(
            str(settings.n_max_pulay),
            "step3NMaxPulay",
        )
        form.addRow("n_max_pulay", self._n_max_pulay)
        self._charge_mix_param = _line_edit(
            _format_edit_real(settings.charge_mix_param),
            "step3ChargeMixParam",
        )
        form.addRow("charge_mix_param", self._charge_mix_param)
        self._sc_accuracy_rho = _line_edit(
            _format_edit_real(settings.sc_accuracy_rho),
            "step3ScAccuracyRho",
        )
        form.addRow("sc_accuracy_rho", self._sc_accuracy_rho)
        self._sc_accuracy_eev = _line_edit(
            _format_edit_real(settings.sc_accuracy_eev),
            "step3ScAccuracyEev",
        )
        form.addRow("sc_accuracy_eev", self._sc_accuracy_eev)
        self._sc_accuracy_etot = _line_edit(
            _format_edit_real(settings.sc_accuracy_etot),
            "step3ScAccuracyEtot",
        )
        form.addRow("sc_accuracy_etot", self._sc_accuracy_etot)
        self._sc_iter_limit = _line_edit(
            str(settings.sc_iter_limit),
            "step3ScIterLimit",
        )
        form.addRow("sc_iter_limit", self._sc_iter_limit)
        self._orbital_eigenstates = _line_edit(
            format_eigenstate_indices_text(
                settings.orbital_cubes.eigenstate_indices
            ),
            "step3OrbitalEigenstates",
        )
        self._orbital_eigenstates.setPlaceholderText("e.g. 152, 153, 154")
        form.addRow("Orbital state numbers", self._orbital_eigenstates)
        self._orbital_grid_spacing = _line_edit(
            (
                _format_edit_real(
                    settings.orbital_cubes.grid_spacing_angstrom
                )
                if settings.orbital_cubes.grid_spacing_angstrom is not None
                else ""
            ),
            "step3OrbitalGridSpacing",
        )
        self._orbital_grid_spacing.setPlaceholderText(
            "Blank = FHI-aims default (Å)"
        )
        form.addRow("Cube grid spacing (Å)", self._orbital_grid_spacing)
        layout.addLayout(form)

        fixed = QLabel(
            "Fixed for Step 3:\n"
            "relativistic atomic_zora scalar\n"
            "occupation_type gaussian <width>\n"
            "mixer pulay\n"
            "output aitranss\n"
            "KS_method serial\n"
            "restart aims.restart\n\n"
            "Optional orbital Cubes use explicit 1-based eigenstate numbers.\n"
            "No geometry relaxation. No vdW correction.",
            self,
        )
        fixed.setObjectName("step3FixedDirectives")
        fixed.setWordWrap(True)
        layout.addWidget(fixed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._button_box = buttons
        self._spin_mode.currentIndexChanged.connect(self._update_spin_controls)
        self._update_spin_controls()

    def selected_settings(self) -> TransportConvergenceSettings:
        if self._accepted_settings is None:
            raise RuntimeError("Step-3 settings dialog was not accepted")
        return self._accepted_settings

    @Slot()
    def _update_spin_controls(self, *_ignored) -> None:
        self._uniform_moment.setEnabled(self._spin_mode.currentData() is not None)

    @Slot()
    def _validate_and_accept(self) -> None:
        try:
            spin_mode = self._spin_mode.currentData()
            spin = (
                SpinSettings()
                if spin_mode is None
                else SpinSettings(
                    enabled=True,
                    initialization_mode=spin_mode,
                    uniform_initial_moment=_required_float(
                        self._uniform_moment,
                        "initial moment per atom",
                    ),
                )
            )
            settings = TransportConvergenceSettings(
                xc=self._xc.currentData(),
                spin=spin,
                total_charge=_required_float(self._charge, "total charge"),
                species_accuracy=self._species_accuracy.currentData(),
                occupation_width=_required_float(
                    self._occupation_width,
                    "occupation width",
                ),
                n_max_pulay=_required_integer(
                    self._n_max_pulay,
                    "n_max_pulay",
                ),
                charge_mix_param=_required_float(
                    self._charge_mix_param,
                    "charge_mix_param",
                ),
                sc_accuracy_rho=_required_float(
                    self._sc_accuracy_rho,
                    "sc_accuracy_rho",
                ),
                sc_accuracy_eev=_required_float(
                    self._sc_accuracy_eev,
                    "sc_accuracy_eev",
                ),
                sc_accuracy_etot=_required_float(
                    self._sc_accuracy_etot,
                    "sc_accuracy_etot",
                ),
                sc_iter_limit=_required_integer(
                    self._sc_iter_limit,
                    "sc_iter_limit",
                ),
                orbital_cubes=OrbitalCubeOutputSettings(
                    eigenstate_indices=parse_eigenstate_indices_text(
                        self._orbital_eigenstates.text()
                    ),
                    grid_spacing_angstrom=_optional_float(
                        self._orbital_grid_spacing,
                        "Cube grid spacing",
                    ),
                ),
            )
        except (TypeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Invalid Step-3 settings",
                str(error),
            )
            return
        self._accepted_settings = settings
        self.accept()


class Step3SubmissionConfirmationDialog(QDialog):
    """Confirm one real Step-3 scheduler submission inside the managed project."""

    def __init__(
        self,
        context: TransportConvergenceContext,
        profile: ServerProfile,
        settings: TransportConvergenceSettings,
        *,
        temporary_password_required: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(context, TransportConvergenceContext):
            raise TypeError("Step-3 confirmation requires eligible context")
        if not isinstance(profile, ServerProfile):
            raise TypeError("Step-3 confirmation requires a server profile")
        if profile.execution_preset is None:
            raise ValueError("selected server has no Cluster Execution Settings")
        if not isinstance(settings, TransportConvergenceSettings):
            raise TypeError("Step-3 confirmation requires validated settings")
        scheduler_name = scheduler_display_name(
            profile.execution_preset.scheduler_kind
        )
        self._temporary_password_required = temporary_password_required
        self.setWindowTitle(f"Confirm REAL Step-3 {scheduler_name} Submission")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "This action creates molecule_Au/transport in the existing project "
            f"and sends one real {scheduler_name} submission request. It does not wait for "
            "scientific completion.",
            self,
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        project = context.project
        form.addRow("Project:", QLabel(project.remote_directory_name))
        form.addRow("Current:", QLabel("Step 2 — SUCCEEDED; electrodes applied"))
        form.addRow("Next:", QLabel("Step 3 — Transport convergence"))
        form.addRow(
            "Remote:",
            QLabel(
                remote_step_directory(
                    project,
                    ProjectStepKind.TRANSPORT_CONVERGENCE,
                )
            ),
        )
        form.addRow("Server:", QLabel(profile.name))
        form.addRow("Resources:", QLabel(resource_summary(profile.execution_preset)))
        step3_email = QLabel(email_notification_summary(profile), self)
        step3_email.setObjectName("step3EmailSummary")
        form.addRow("Email notification:", step3_email)
        form.addRow("Settings:", QLabel(transport_convergence_summary(settings)))
        form.addRow(
            "Fixed:",
            QLabel("output aitranss; KS_method serial; restart aims.restart"),
        )
        self._password: QLineEdit | None = None
        if temporary_password_required:
            self._password = QLineEdit(self)
            self._password.setObjectName("step3TemporaryPassword")
            self._password.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Password (memory only):", self._password)
        layout.addLayout(form)
        buttons = QDialogButtonBox(self)
        self._submit = buttons.addButton(
            "Submit REAL Step 3",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def take_temporary_password(self) -> str | None:
        if self._password is None:
            return None
        password = self._password.text()
        self._password.clear()
        return password or None

    @Slot()
    def _validate_and_accept(self) -> None:
        if (
            self._temporary_password_required
            and self._password is not None
            and not self._password.text()
        ):
            QMessageBox.critical(
                self,
                "Password required",
                "Enter the password for this Step-3 submission.",
            )
            return
        self._submit.setEnabled(False)
        self.accept()


def transport_convergence_summary(
    settings: TransportConvergenceSettings,
) -> str:
    if not isinstance(settings, TransportConvergenceSettings):
        raise TypeError(
            "transport summary requires TransportConvergenceSettings"
        )
    spin = "spin collinear" if settings.spin.enabled else "spin none"
    orbital_summary = (
        "none"
        if not settings.orbital_cubes.eigenstate_indices
        else ",".join(
            str(index) for index in settings.orbital_cubes.eigenstate_indices
        )
    )
    return (
        f"{settings.xc.value.upper()} / {settings.species_accuracy.value} / "
        f"{spin} / charge {settings.total_charge:g}; Gaussian "
        f"{settings.occupation_width:g}; SCF limit {settings.sc_iter_limit}; "
        f"orbital states {orbital_summary}"
    )


def _enum_combo(
    choices: tuple[tuple[str, object], ...],
    selected: object,
    object_name: str,
) -> QComboBox:
    combo = QComboBox()
    combo.setObjectName(object_name)
    for label, value in choices:
        combo.addItem(label, value)
    index = combo.findData(selected)
    if index < 0:
        raise ValueError(f"unsupported initial Step-3 choice: {selected!r}")
    combo.setCurrentIndex(index)
    return combo


def _line_edit(text: str, object_name: str) -> QLineEdit:
    field = QLineEdit(text)
    field.setObjectName(object_name)
    return field


def _required_float(field: QLineEdit, label: str) -> float:
    text = field.text().strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{label} must be numeric") from None


def _required_integer(field: QLineEdit, label: str) -> int:
    text = field.text().strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{label} must be an integer") from None


def _optional_float(field: QLineEdit, label: str) -> float | None:
    text = field.text().strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{label} must be numeric") from None


def _format_edit_real(value: float) -> str:
    numeric = float(value)
    if numeric != 0.0 and abs(numeric) < 1.0e-2:
        mantissa, exponent = f"{numeric:.15e}".split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}E{int(exponent)}"
    text = f"{numeric:.15g}"
    if "e" not in text and "E" not in text:
        return text
    mantissa, exponent = text.lower().split("e")
    return f"{mantissa}E{int(exponent)}"
