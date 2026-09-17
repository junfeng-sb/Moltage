"""Editable tcontrol and explicit one-process Step-4 submission dialog."""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from moltage.aitranss.slurm import AitranssExecutionSettings
from moltage.aitranss.runtime import AITRANSS_EXECUTABLE_COMMAND
from moltage.aitranss.nlayers import (
    NlayersValueSource,
    missing_nlayers_guidance,
)
from moltage.aitranss.tcontrol import (
    OnOff,
    TControlProposal,
    TControlSettings,
    render_tcontrol_preserving_self_energy,
)
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.domain.server_profile import (
    LsfResourceRequirementMode,
    ServerProfile,
    runtime_hours_from_minutes,
    runtime_minutes_from_hours,
)
from moltage.gui.project_submission import email_notification_summary


class AitranssStep4Dialog(QDialog):
    """Keep automatic evidence immutable while editing a final tcontrol copy."""

    def __init__(
        self,
        snapshot: ProjectRecoverySnapshot,
        profile: ServerProfile,
        *,
        verified_aitranss_path: str | None,
        executable_preflight_error: str | None = None,
        initial_settings: TControlSettings | None = None,
        initial_execution_settings: AitranssExecutionSettings | None = None,
        self_energy_filename: str | None = None,
        restart_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(restart_mode, bool):
            raise TypeError("Step-4 restart mode must be boolean")
        if not restart_mode and not snapshot.can_continue_step4:
            raise ValueError("project is not eligible for Step 4")
        if restart_mode and (
            snapshot.optimized_structure is None
            or snapshot.transport_evidence is None
            or snapshot.surface_proposal is None
        ):
            raise ValueError("Step-4 restart requires recovered Step-3 evidence")
        if profile.execution_preset is None:
            raise ValueError("Cluster Execution Settings are unavailable")
        self._snapshot = snapshot
        self._profile = profile
        self._verified_aitranss_path = verified_aitranss_path
        self._preflight_error = executable_preflight_error
        self._self_energy_filename = self_energy_filename
        self._restart_mode = restart_mode
        self._proposal = TControlProposal.from_evidence(
            snapshot.transport_evidence,
            snapshot.surface_proposal,
        )
        initial = initial_settings or _editable_initial_settings(self._proposal)
        if not isinstance(initial, TControlSettings):
            raise TypeError("initial tcontrol settings are invalid")
        initial_nlayers = (
            initial.nlayers if initial_settings is not None else self._proposal.nlayers
        )
        self._nlayers_source = (
            NlayersValueSource.USER_SPECIFIED
            if initial_settings is not None
            else self._proposal.nlayers_source
        )
        self._selected_settings: TControlSettings | None = None
        self._selected_execution: AitranssExecutionSettings | None = None
        self.setWindowTitle(
            "Restart — Step 4 / AITRANSS"
            if restart_mode
            else "Continue — Step 4 / AITRANSS"
        )
        self.setMinimumWidth(900)
        self.resize(960, 680)
        layout = QVBoxLayout(self)

        convention = QLabel(
            "Logical Left and Right follow the two persisted electrode sides. "
            "Surface numbers are 1-based geometry.in/tcontrol indices.\n"
            f"Left: {', '.join(map(str, self._proposal.left_surface))}    "
            f"Right: {', '.join(map(str, self._proposal.right_surface))}",
            self,
        )
        convention.setObjectName("surfaceIndexConvention")
        convention.setWordWrap(True)
        layout.addWidget(convention)

        columns = QHBoxLayout()
        left_column = QVBoxLayout()
        right_column = QVBoxLayout()
        columns.addLayout(left_column, 1)
        columns.addLayout(right_column, 1)
        layout.addLayout(columns, 1)

        system_surface = QGroupBox("System and surface", self)
        system_surface.setObjectName("step4SystemSurfaceGroup")
        system_surface_form = QFormLayout(system_surface)
        self._integer_fields: dict[str, QSpinBox] = {}
        integer_values = {
            "natoms": initial.natoms,
            "nsaos": initial.nsaos,
            "lsurc": initial.lsurc,
            "lsurx": initial.lsurx,
            "lsury": initial.lsury,
            "rsurc": initial.rsurc,
            "rsurx": initial.rsurx,
            "rsury": initial.rsury,
        }
        for name, value in integer_values.items():
            widget = QSpinBox(system_surface)
            widget.setObjectName("tcontrol_" + name)
            widget.setRange(1, 2_000_000_000)
            widget.setValue(value)
            widget.valueChanged.connect(self._settings_changed)
            self._integer_fields[name] = widget
            system_surface_form.addRow(f"${name}:", widget)
        nlayers = QSpinBox(system_surface)
        nlayers.setObjectName("tcontrol_nlayers")
        nlayers.setRange(0, 2_000_000_000)
        nlayers.setSpecialValueText("Not configured")
        nlayers.setValue(initial_nlayers or 0)
        nlayers.valueChanged.connect(self._nlayers_changed)
        self._integer_fields["nlayers"] = nlayers
        system_surface_form.addRow("$nlayers:", nlayers)
        self._nlayers_source_label = QLabel(system_surface)
        self._nlayers_source_label.setObjectName("tcontrolNlayersSource")
        self._nlayers_source_label.setWordWrap(True)
        system_surface_form.addRow("Source:", self._nlayers_source_label)
        left_column.addWidget(system_surface)

        transport_output = QGroupBox("Transport and output", self)
        transport_output.setObjectName("step4TransportOutputGroup")
        transport_output_form = QFormLayout(transport_output)
        self._text_fields: dict[str, QLineEdit] = {}
        for name, value in (
            ("s1i", initial.s1i),
            ("s2i", initial.s2i),
            ("s3i", initial.s3i),
            ("ener", initial.ener),
            ("estep", initial.estep),
            ("eend", initial.eend),
            ("output_filename", initial.output_filename),
        ):
            widget = QLineEdit(value, transport_output)
            widget.setObjectName("tcontrol_" + name)
            widget.textChanged.connect(self._settings_changed)
            self._text_fields[name] = widget
            transport_output_form.addRow(
                "$output file:" if name == "output_filename" else f"${name}:",
                widget,
            )
        self._testing = _on_off_combo(initial.testing, transport_output)
        self._testing.setObjectName("tcontrol_testing")
        self._ecp = _on_off_combo(initial.ecp, transport_output)
        self._ecp.setObjectName("tcontrol_ecp")
        self._testing.currentIndexChanged.connect(self._settings_changed)
        self._ecp.currentIndexChanged.connect(self._settings_changed)
        transport_output_form.addRow("$testing:", self._testing)
        transport_output_form.addRow("$ecp:", self._ecp)
        right_column.addWidget(transport_output)

        scheduler_kind = profile.execution_preset.scheduler_kind
        scheduler_name = scheduler_display_name(scheduler_kind)
        resources = QGroupBox(f"Step-4 {scheduler_name} resources", self)
        resources.setObjectName("step4ResourcesGroup")
        resource_form = QFormLayout(resources)
        defaults = initial_execution_settings or AitranssExecutionSettings()
        if not isinstance(defaults, AitranssExecutionSettings):
            raise TypeError("initial AITRANSS execution settings are invalid")
        self._threads = QSpinBox(resources)
        self._threads.setObjectName("aitranssCpuThreads")
        self._threads.setRange(1, 2_000_000_000)
        self._threads.setValue(defaults.cpu_threads)
        self._runtime = QDoubleSpinBox(resources)
        self._runtime.setObjectName("aitranssRuntimeHours")
        self._runtime.setRange(0.1, 1_000_000.0)
        self._runtime.setDecimals(1)
        self._runtime.setSingleStep(0.5)
        self._runtime.setSuffix(" hours")
        self._runtime.setValue(runtime_hours_from_minutes(defaults.runtime_minutes))
        self._memory = QSpinBox(resources)
        self._memory.setObjectName("aitranssMemoryGb")
        self._memory.setRange(1, 2_000_000_000)
        self._memory.setSuffix(" GB")
        self._memory.setValue(defaults.memory_gb)
        thread_label = (
            "AITRANSS CPU threads:"
            if scheduler_kind is SchedulerKind.SLURM
            else "LSF job slots / AITRANSS threads:"
        )
        memory_label = (
            "Memory limit per node:"
            if scheduler_kind is SchedulerKind.SLURM
            else (
                "Memory reservation (LSF rusage):"
                if profile.execution_preset.lsf_resource_requirement_mode
                is LsfResourceRequirementMode.SPAN_RUSAGE
                else "Memory reservation (not submitted):"
            )
        )
        resource_form.addRow(thread_label, self._threads)
        resource_form.addRow("Maximum runtime:", self._runtime)
        resource_form.addRow(memory_label, self._memory)
        if scheduler_kind is SchedulerKind.LSF:
            uses_structured_requirement = (
                profile.execution_preset.lsf_resource_requirement_mode
                is LsfResourceRequirementMode.SPAN_RUSAGE
            )
            self._memory.setEnabled(uses_structured_requirement)
            lsf_model = QLabel(
                (
                    "AITRANSS runs once on one LSF host. Requested LSF slots are "
                    "used as the OpenMP thread count; span/rusage is submitted."
                    if uses_structured_requirement
                    else "AITRANSS runs once with the requested LSF slots as its "
                    "OpenMP thread count. No #BSUB -R request is submitted; host "
                    "placement and memory use scheduler/site defaults."
                ),
                resources,
            )
            lsf_model.setObjectName("step4LsfExecutionModel")
            lsf_model.setWordWrap(True)
            resource_form.addRow("Execution model:", lsf_model)
        step4_email = QLabel(email_notification_summary(profile), resources)
        step4_email.setObjectName("step4EmailSummary")
        resource_form.addRow("Email notification:", step4_email)
        left_column.addWidget(resources)
        left_column.addStretch(1)

        self._validation = QLabel(self)
        self._validation.setObjectName("step4ValidationStatus")
        self._validation.setWordWrap(True)
        right_column.addWidget(self._validation)
        preview_group = QGroupBox("Generated tcontrol preview", self)
        preview_group.setObjectName("step4PreviewGroup")
        preview_layout = QVBoxLayout(preview_group)
        self._preview = QPlainTextEdit(preview_group)
        self._preview.setObjectName("tcontrolPreview")
        self._preview.setReadOnly(True)
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        preview_layout.addWidget(self._preview)
        right_column.addWidget(preview_group, stretch=1)

        buttons = QDialogButtonBox(self)
        self._submit = buttons.addButton(
            "Submit REAL Step-4 Restart" if restart_mode else "Submit REAL Step 4",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._submit.setObjectName("submitRealStep4")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_submission)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_nlayers_source_label()
        self._settings_changed()

    @property
    def automatic_proposal(self) -> TControlProposal:
        return self._proposal

    @property
    def verified_aitranss_path(self) -> str | None:
        return self._verified_aitranss_path

    def selected_settings(self) -> TControlSettings:
        if self._selected_settings is None:
            raise RuntimeError("Step-4 tcontrol settings were not accepted")
        return self._selected_settings

    def selected_execution_settings(self) -> AitranssExecutionSettings:
        if self._selected_execution is None:
            raise RuntimeError("Step-4 execution settings were not accepted")
        return self._selected_execution

    @Slot()
    def _nlayers_changed(self, value: int) -> None:
        self._nlayers_source = (
            NlayersValueSource.USER_SPECIFIED if value > 0 else None
        )
        self._update_nlayers_source_label()
        self._settings_changed()

    def _update_nlayers_source_label(self) -> None:
        source = self._nlayers_source
        if source is NlayersValueSource.AIMS_RECOMMENDED:
            detail = self._proposal.nlayers_evidence or "Reviewed AITRANSS header evidence."
            self._nlayers_source_label.setText(
                "AIMS recommended (editable). " + detail
            )
        elif source is NlayersValueSource.USER_SPECIFIED:
            self._nlayers_source_label.setText("User specified (editable).")
        else:
            self._nlayers_source_label.setText(
                missing_nlayers_guidance(self._snapshot.surface_proposal.pyramid_layers)
            )

    @Slot()
    def _settings_changed(self, *_args) -> None:
        try:
            settings = self._collect_settings()
            rendered = render_tcontrol_preserving_self_energy(
                settings,
                self._snapshot.optimized_structure,
                self._snapshot.transport_evidence.spin_mode,
                self._self_energy_filename,
            )
        except Exception as error:
            self._preview.clear()
            self._validation.setText(f"Invalid tcontrol: {error}")
            self._submit.setEnabled(False)
            return
        self._preview.setPlainText(rendered)
        if self._verified_aitranss_path is None:
            self._validation.setText(
                self._preflight_error
                or "AITRANSS executable is unavailable: a verified "
                f"{AITRANSS_EXECUTABLE_COMMAND} path is required before real "
                "Step-4 submission."
            )
            self._submit.setEnabled(False)
            return
        self._validation.setText(
            "Valid tcontrol. Step 4 will run one AITRANSS process using "
            + self._verified_aitranss_path
        )
        self._submit.setEnabled(True)

    def _collect_settings(self) -> TControlSettings:
        values = {name: widget.value() for name, widget in self._integer_fields.items()}
        if values["nlayers"] <= 0:
            raise ValueError(
                missing_nlayers_guidance(
                    self._snapshot.surface_proposal.pyramid_layers
                )
            )
        return TControlSettings(
            **values,
            s1i=self._text_fields["s1i"].text(),
            s2i=self._text_fields["s2i"].text(),
            s3i=self._text_fields["s3i"].text(),
            ener=self._text_fields["ener"].text(),
            estep=self._text_fields["estep"].text(),
            eend=self._text_fields["eend"].text(),
            output_filename=self._text_fields["output_filename"].text(),
            testing=self._testing.currentData(),
            ecp=self._ecp.currentData(),
        )

    @Slot()
    def _accept_submission(self) -> None:
        if self._verified_aitranss_path is None:
            return
        self._selected_settings = self._collect_settings()
        render_tcontrol_preserving_self_energy(
            self._selected_settings,
            self._snapshot.optimized_structure,
            self._snapshot.transport_evidence.spin_mode,
            self._self_energy_filename,
        )
        self._selected_execution = AitranssExecutionSettings(
            cpu_threads=self._threads.value(),
            runtime_minutes=runtime_minutes_from_hours(self._runtime.value()),
            memory_gb=self._memory.value(),
        )
        self.accept()


def _on_off_combo(value: OnOff, parent: QWidget) -> QComboBox:
    widget = QComboBox(parent)
    widget.addItem("on", OnOff.ON)
    widget.addItem("off", OnOff.OFF)
    widget.setCurrentIndex(0 if value is OnOff.ON else 1)
    return widget


def _editable_initial_settings(proposal: TControlProposal) -> TControlSettings:
    """Build field defaults while allowing the editor to represent missing nlayers."""

    return TControlSettings(
        natoms=proposal.natoms,
        nsaos=proposal.nsaos,
        lsurc=proposal.left_surface[0],
        lsurx=proposal.left_surface[1],
        lsury=proposal.left_surface[2],
        rsurc=proposal.right_surface[0],
        rsurx=proposal.right_surface[1],
        rsury=proposal.right_surface[2],
        nlayers=proposal.nlayers or 1,
    )
