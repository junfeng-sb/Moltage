"""Small manual runtime editor; remote discovery is owned by the parent worker."""

from dataclasses import replace
from pathlib import PurePosixPath

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QStyle, QTabWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration, FhiAimsRuntimeConfiguration, RuntimeDiscoveryHints,
    RuntimeEnvironment, RuntimeEnvironmentMode, RuntimeLocation, RuntimeLocationKind,
    SlurmAitranssLaunchMode, validate_srun_launcher_path,
)
from moltage.domain.scheduler import SchedulerKind
from moltage.gui.theme import themed_icon
from moltage.remote.runtime_discovery import RuntimeCandidate, RuntimeDiscoveryResult


def _help_row(parent, widget, title, explanation):
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget, 1)
    help_button = QToolButton(row)
    help_button.setObjectName(widget.objectName() + "Help")
    help_button.setIcon(themed_icon("update_log.svg"))
    help_button.setAccessibleName("Help: " + title)
    help_button.setToolTip(explanation)
    help_button.clicked.connect(lambda: QMessageBox.information(parent, title, explanation))
    layout.addWidget(help_button)
    return row


class _RuntimePage(QWidget):
    species_directory_requested = Signal(str)

    def __init__(
        self,
        title,
        request,
        parent,
        *,
        launcher=None,
        configured=None,
        species_root=None,
        species_browse_available=False,
        slurm_aitranss_launch_mode=None,
        slurm_aitranss_srun_path=None,
        show_slurm_aitranss_launch=False,
    ):
        super().__init__(parent)
        self.title = title
        self._resolved = None
        form = QFormLayout(self)
        self.kind = QComboBox(self)
        self.kind.setObjectName(title + "LocationKind")
        self.kind.addItem("Installation directory", RuntimeLocationKind.DIRECTORY)
        self.kind.addItem("Executable file", RuntimeLocationKind.EXECUTABLE)
        self.kind.setCurrentIndex(self.kind.findData(request.kind))
        form.addRow("Location type:", _help_row(self, self.kind, "Location type",
            "Choose a directory to search, or an exact executable to configure manually. "
            "Only one path is needed; do not enter both."))
        self.location = QLineEdit(request.location, self)
        self.location.setObjectName(title + "Location")
        example = ("/opt/fhi-aims or /opt/fhi-aims/bin/aims.scalapack.mpi.x"
                   if title == "FHI-aims" else "/opt/aitranss or /opt/aitranss/bin/aitranss.x")
        self.location.setPlaceholderText(example)
        form.addRow("Remote path:", _help_row(self, self.location, title + " location",
            "Enter an absolute Linux path on the selected server, not a Windows path. "
            "A directory limits Find Missing to that installation. An executable must be the "
            "actual calculation binary, not subtask.slurm.sh or run_aims.mpi. Examples: " + example))
        self.launcher = None
        if launcher is not None:
            self.launcher = QLineEdit(launcher, self)
            self.launcher.setObjectName("mpiLauncher")
            self.launcher.setPlaceholderText("Optional for discovery; e.g. /usr/bin/mpirun")
            form.addRow("MPI launcher:", _help_row(self, self.launcher, "MPI launcher",
                "Enter the absolute path of the srun or mpirun executable compatible with this "
                "FHI-aims build. Leave blank for Find Missing. Examples: /usr/bin/srun or "
                "/opt/openmpi/bin/mpirun. Do not enter arguments or a submission script. "
                "MPI task count comes from Cluster Execution Settings."))
        self.species_root = None
        self.species_browse_button = None
        self.species_status = None
        if title == "FHI-aims":
            self.species_root = QLineEdit(species_root or "", self)
            self.species_root.setObjectName("fhiSpeciesDefinitionsRoot")
            self.species_root.setPlaceholderText(
                "/opt/fhi-aims/species_defaults/defaults_2020"
            )
            species_row = QWidget(self)
            species_layout = QHBoxLayout(species_row)
            species_layout.setContentsMargins(0, 0, 0, 0)
            species_layout.addWidget(self.species_root, 1)
            self.species_browse_button = QToolButton(species_row)
            self.species_browse_button.setObjectName(
                "browseFhiSpeciesDefinitionsRoot"
            )
            self.species_browse_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
            )
            self.species_browse_button.setEnabled(species_browse_available)
            self.species_browse_button.setToolTip(
                "Choose the species definitions root on this server"
            )
            self.species_browse_button.setAccessibleName(
                "Browse FHI-aims species definitions root"
            )
            self.species_browse_button.clicked.connect(
                self._request_species_directory
            )
            species_layout.addWidget(self.species_browse_button)
            form.addRow(
                "Species definitions root:",
                _help_row(
                    self,
                    species_row,
                    "Species definitions root",
                    "Enter or choose a canonical absolute directory on the selected "
                    "server. Its immediate children must include light, tight, and "
                    "really_tight. Moltage reads the exact required element files "
                    "from this root when preparing new control.in files.",
                ),
            )
            self.species_status = QLabel(
                "Configured" if species_root else "Not configured",
                self,
            )
            self.species_status.setObjectName("fhiSpeciesDefinitionsStatus")
            self.species_status.setWordWrap(True)
            form.addRow("Species root status:", self.species_status)
        self.environment = QComboBox(self)
        self.environment.setObjectName(title + "Environment")
        for label, mode in (("Auto", RuntimeEnvironmentMode.AUTO),
                            ("No setup", RuntimeEnvironmentMode.NONE),
                            ("Modules", RuntimeEnvironmentMode.MODULES),
                            ("Setup script", RuntimeEnvironmentMode.SCRIPT)):
            self.environment.addItem(label, mode)
        form.addRow("Environment:", _help_row(self, self.environment, "Runtime environment",
            "Auto searches for an environment; it is not a runnable saved environment. "
            "No setup uses the server login environment without module commands. "
            "Modules loads the names below. Setup script sources only the script you supply. "
            "FHI-aims and AITRANSS may use different environments."))
        self.modules = QLineEdit(" ".join(request.environment.modules), self)
        self.modules.setObjectName(title + "Modules")
        self.modules.setPlaceholderText("mpi/module chemistry/fhi-aims")
        self._modules_row = _help_row(self, self.modules, "Environment modules",
            "Enter module names in load order, separated by spaces; do not include 'module load'. "
            "Example: mpi/module chemistry/fhi-aims. Use the names installed on your server.")
        form.addRow("Modules:", self._modules_row)
        self.script = QLineEdit(request.environment.setup_script or "", self)
        self.script.setObjectName(title + "SetupScript")
        self.script.setPlaceholderText("/opt/compiler/env/setup.sh")
        self._script_row = _help_row(self, self.script, "Environment setup script",
            "Enter an absolute path to a trusted environment-only shell script, for example "
            "/opt/compiler/env/setup.sh. It will be sourced, not just read. Do not use job "
            "submission or calculation scripts. Find Missing asks before executing it.")
        form.addRow("Setup script:", self._script_row)
        self.resolved_label = QLabel("Not searched", self)
        self.resolved_label.setWordWrap(True)
        form.addRow("Resolved executable:", self.resolved_label)
        self._form = form
        self.environment.setCurrentIndex(self.environment.findData(request.environment.mode))
        self.environment.currentIndexChanged.connect(self._environment_changed)
        self._environment_changed()
        if configured is not None:
            env = configured.environment or RuntimeEnvironment(
                RuntimeEnvironmentMode.MODULES, configured.modules)
            self._resolved = (request, configured.executable_path, env)
            self.resolved_label.setText(configured.executable_path)
        self.location.textChanged.connect(self._invalidate_label)
        self.kind.currentIndexChanged.connect(self._invalidate_label)
        self.environment.currentIndexChanged.connect(self._invalidate_label)
        self.modules.textChanged.connect(self._invalidate_label)
        self.script.textChanged.connect(self._invalidate_label)
        if self.species_root is not None:
            self.species_root.textChanged.connect(
                self._species_root_changed
            )
        self.aitranss_launch_mode = None
        self.aitranss_srun_path = None
        self.aitranss_srun_status = None
        self._aitranss_srun_row = None
        if show_slurm_aitranss_launch:
            self.aitranss_launch_mode = QComboBox(self)
            self.aitranss_launch_mode.setObjectName("slurmAitranssLaunchMode")
            self.aitranss_launch_mode.addItem(
                "Select before Step 4",
                None,
            )
            self.aitranss_launch_mode.addItem(
                "Direct executable",
                SlurmAitranssLaunchMode.DIRECT,
            )
            self.aitranss_launch_mode.addItem(
                "srun (verified absolute path)",
                SlurmAitranssLaunchMode.SRUN,
            )
            self.aitranss_launch_mode.setCurrentIndex(
                self.aitranss_launch_mode.findData(slurm_aitranss_launch_mode)
            )
            form.addRow(
                "Slurm Step-4 launch:",
                _help_row(
                    self,
                    self.aitranss_launch_mode,
                    "Slurm Step-4 launch",
                    "Choose Direct to execute AITRANSS itself, or srun to launch "
                    "exactly one task through a remotely verified absolute srun path. "
                    "Moltage never falls back to a bare PATH-resolved srun.",
                ),
            )
            self.aitranss_srun_path = QLineEdit(
                slurm_aitranss_srun_path or "",
                self,
            )
            self.aitranss_srun_path.setObjectName("slurmAitranssSrunPath")
            self.aitranss_srun_path.setPlaceholderText("/path/to/slurm/bin/srun")
            self._aitranss_srun_row = _help_row(
                self,
                self.aitranss_srun_path,
                "AITRANSS srun executable",
                "Enter one canonical absolute path ending in /srun. Find Missing "
                "checks only this exact path or the configured scheduler-bin sibling; "
                "it does not scan the filesystem.",
            )
            form.addRow("srun executable:", self._aitranss_srun_row)
            self.aitranss_srun_status = QLabel(self)
            self.aitranss_srun_status.setObjectName("slurmAitranssSrunStatus")
            self.aitranss_srun_status.setWordWrap(True)
            form.addRow("srun status:", self.aitranss_srun_status)
            self.aitranss_launch_mode.currentIndexChanged.connect(
                self._aitranss_launch_changed
            )
            self.aitranss_srun_path.textChanged.connect(
                self._aitranss_srun_changed
            )
            self._verified_srun_paths = set()
            self._aitranss_launch_changed()

    def _request_species_directory(self):
        if self.species_root is None:
            return
        self.species_directory_requested.emit(
            self.species_root.text().strip() or "/"
        )

    def _species_root_changed(self, text):
        if self.species_status is not None:
            self.species_status.setText(
                "Entered; validation occurs during discovery and input preparation"
                if text.strip()
                else "Not configured"
            )

    def _environment_changed(self):
        mode = RuntimeEnvironmentMode(self.environment.currentData())
        self._form.setRowVisible(self._modules_row, mode is RuntimeEnvironmentMode.MODULES)
        self._form.setRowVisible(self._script_row, mode is RuntimeEnvironmentMode.SCRIPT)

    def _invalidate_label(self):
        self.resolved_label.setText("Changed; Find Missing can verify the new configuration.")

    def _aitranss_launch_changed(self):
        if self.aitranss_launch_mode is None:
            return
        raw_mode = self.aitranss_launch_mode.currentData()
        mode = None if raw_mode is None else SlurmAitranssLaunchMode(raw_mode)
        is_srun = mode is SlurmAitranssLaunchMode.SRUN
        self._form.setRowVisible(self._aitranss_srun_row, is_srun)
        self._form.setRowVisible(self.aitranss_srun_status, is_srun)
        if is_srun and not self.aitranss_srun_path.text().strip():
            if self._verified_srun_paths:
                self.aitranss_srun_path.setText(sorted(self._verified_srun_paths)[0])
        self._update_aitranss_srun_status()

    def _aitranss_srun_changed(self):
        self._update_aitranss_srun_status()

    def _update_aitranss_srun_status(self):
        if self.aitranss_srun_status is None:
            return
        path = self.aitranss_srun_path.text().strip()
        if not path:
            self.aitranss_srun_status.setText(
                "Not configured; Step 4 will remain unavailable in srun mode."
            )
        elif path in self._verified_srun_paths:
            self.aitranss_srun_status.setText("Verified during bounded remote discovery")
        else:
            self.aitranss_srun_status.setText(
                "Entered; remote executable verification is required before submission."
            )

    def register_verified_srun_paths(self, paths):
        if self.aitranss_launch_mode is None:
            return
        for path in paths:
            if PurePosixPath(path).name == "srun":
                self._verified_srun_paths.add(path)
        self._aitranss_launch_changed()

    def request(self):
        mode = RuntimeEnvironmentMode(self.environment.currentData())
        env = RuntimeEnvironment(mode,
            tuple(self.modules.text().split()) if mode is RuntimeEnvironmentMode.MODULES else (),
            self.script.text().strip() if mode is RuntimeEnvironmentMode.SCRIPT else None)
        return RuntimeLocation(self.location.text(), env, self.kind.currentData())

    def apply_candidate(self, candidate, *, species_root_path=None):
        if not candidate.environment_resolved:
            raise ValueError(
                f"{self.title}: an incomplete discovery candidate cannot be auto-applied."
            )
        if not self.location.text().strip():
            self.kind.setCurrentIndex(self.kind.findData(RuntimeLocationKind.EXECUTABLE))
            self.location.setText(candidate.executable_path)
        env = candidate.environment or RuntimeEnvironment(RuntimeEnvironmentMode.MODULES, candidate.modules)
        if self.environment.currentData() == RuntimeEnvironmentMode.AUTO and candidate.environment_resolved:
            self.modules.setText(" ".join(env.modules))
            self.script.setText(env.setup_script or "")
            self.environment.setCurrentIndex(self.environment.findData(env.mode))
        if self.launcher is not None and not self.launcher.text().strip() and candidate.launcher_path:
            self.launcher.setText(candidate.launcher_path)
        selected_species_root = species_root_path or candidate.species_root_path
        if (
            self.species_root is not None
            and not self.species_root.text().strip()
            and selected_species_root
        ):
            self.species_root.setText(selected_species_root)
            if self.species_status is not None:
                self.species_status.setText("Verified during runtime discovery")
        self._resolved = (self.request(), candidate.executable_path,
                          env if candidate.environment_resolved else RuntimeEnvironment())
        self.resolved_label.setText(candidate.executable_path)

    def resolved_values(self):
        request = self.request()
        if not request.location:
            return None
        current = self._resolved if self._resolved and self._resolved[0] == request else None
        if request.kind is RuntimeLocationKind.EXECUTABLE:
            executable = request.location
        elif current:
            executable = current[1]
        else:
            raise ValueError(f"{self.title}: use Find Missing to resolve the directory, "
                             "or choose Executable file and enter its exact path.")
        environment = request.environment
        if environment.mode is RuntimeEnvironmentMode.AUTO:
            if not current:
                raise ValueError(f"{self.title}: choose an environment or use Find Missing.")
            environment = current[2]
            if environment.mode is RuntimeEnvironmentMode.AUTO:
                raise ValueError(f"{self.title}: environment dependencies are unresolved. "
                                 "Supply the required modules/setup script or explicitly select No setup.")
        return executable, environment


class RuntimeConfigurationDialog(QDialog):
    find_requested = Signal(object)
    species_directory_requested = Signal(str)

    def __init__(self, hints, parent=None, *, fhi_runtime=None, aitranss_runtime=None,
                 discovery_available=True,
                 scheduler_kind=SchedulerKind.SLURM,
                 slurm_aitranss_launch_mode=None,
                 slurm_aitranss_srun_path=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Runtime Configuration")
        self.setMinimumWidth(680)
        self._busy = False
        self._discovery_available = discovery_available
        self.selected_hints = None
        self.selected_fhi_runtime = None
        self.selected_aitranss_runtime = None
        self.selected_slurm_aitranss_launch_mode = None
        self.selected_slurm_aitranss_srun_path = None
        self._orca_hint = hints.orca
        self._scheduler_kind = SchedulerKind(scheduler_kind)
        layout = QVBoxLayout(self)
        note = QLabel("Paths refer to this server. Enter what you know, then Find Missing; "
                      "filled fields are kept. Exact executable paths can also be applied manually.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget(self)
        self.fhi = _RuntimePage("FHI-aims", hints.fhi_aims, self.tabs,
                               launcher=hints.mpi_launcher, configured=fhi_runtime,
                               species_root=hints.fhi_species_defaults_path,
                               species_browse_available=discovery_available)
        self.fhi.species_directory_requested.connect(
            self.species_directory_requested.emit
        )
        self.aitranss = _RuntimePage(
            "AITRANSS",
            hints.aitranss,
            self.tabs,
            configured=aitranss_runtime,
            slurm_aitranss_launch_mode=slurm_aitranss_launch_mode,
            slurm_aitranss_srun_path=slurm_aitranss_srun_path,
            show_slurm_aitranss_launch=(
                self._scheduler_kind is SchedulerKind.SLURM
            ),
        )
        self.tabs.addTab(self.fhi, "FHI-aims")
        self.tabs.addTab(self.aitranss, "AITRANSS")
        layout.addWidget(self.tabs)
        self.status = QPlainTextEdit(self)
        self.status.setObjectName("runtimeSearchDetails")
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(130)
        self.status.setPlainText("Apply changes the working copy only; Save in Cluster Execution Settings "
                                 "persists it. File checks do not prove MPI compatibility or calculation success.")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.find_button = QPushButton("Find Missing", self)
        self.find_button.setEnabled(discovery_available)
        self.apply_button = QPushButton("Apply", self)
        self.cancel_button = QPushButton("Cancel", self)
        for button in (self.find_button, self.apply_button, self.cancel_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.find_button.clicked.connect(self._find)
        self.apply_button.clicked.connect(self._apply)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.find_button)
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def hints(self):
        species_root = (
            self.fhi.species_root.text().strip()
            if self.fhi.species_root is not None
            else ""
        )
        return RuntimeDiscoveryHints(
            self.fhi.request(),
            self.fhi.launcher.text(),
            self.aitranss.request(),
            species_root or None,
            self._orca_hint,
        )

    def set_species_directory(self, directory):
        if self.fhi.species_root is None:
            return
        self.fhi.species_root.setText(directory)
        if self.fhi.species_status is not None:
            self.fhi.species_status.setText("Selected remote directory")

    def slurm_aitranss_srun_path(self):
        if self.aitranss.aitranss_srun_path is None:
            return None
        return self.aitranss.aitranss_srun_path.text().strip() or None

    def _find(self):
        try:
            hints = self.hints()
        except ValueError as error:
            QMessageBox.warning(self, "Invalid runtime configuration", str(error))
            return
        self.find_requested.emit(hints)

    def set_busy(self, busy):
        self._busy = busy
        self.tabs.setEnabled(not busy)
        self.find_button.setEnabled(not busy and self._discovery_available)
        self.apply_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        if busy:
            self.status.setPlainText("Searching the configured scope...")

    def receive_result(self, result: RuntimeDiscoveryResult):
        details = list(result.diagnostics)
        verified_srun_paths = list(result.verified_srun_paths)
        for candidate in result.fhi_aims_candidates:
            for path in (
                candidate.launcher_path,
                *candidate.launcher_candidates,
            ):
                if path and PurePosixPath(path).name == "srun":
                    verified_srun_paths.append(path)
        self.aitranss.register_verified_srun_paths(
            tuple(dict.fromkeys(verified_srun_paths))
        )
        for page, candidates in ((self.fhi, result.fhi_aims_candidates),
                                 (self.aitranss, result.aitranss_candidates)):
            if not candidates:
                details.append(page.title + ": no candidate found; existing fields were kept.")
                continue
            usable = tuple(
                item for item in candidates
                if item.environment_resolved and (
                    page.launcher is None
                    or bool(page.launcher.text().strip())
                    or item.launcher_path is not None
                    or bool(item.launcher_candidates)
                )
                and (
                    page is not self.fhi
                    or bool(self.fhi.species_root.text().strip())
                    or item.species_root_path is not None
                    or bool(item.species_root_candidates)
                )
            )
            partial = tuple(item for item in candidates if item not in usable)
            for item in partial:
                details.append(
                    page.title + ": partial candidate was not auto-applied: "
                    + item.executable_path
                )
                details.extend(item.notes)
            if not usable:
                details.append(page.title + ": existing fields were kept for manual completion.")
                continue
            candidate = usable[0]
            if len(usable) > 1:
                labels = [f"{item.module_name} — {item.executable_path}" for item in usable]
                label, accepted = QInputDialog.getItem(self, "Select " + page.title,
                    "Multiple candidates; choose explicitly:", labels, 0, False)
                if not accepted:
                    details.append(page.title + ": selection cancelled; existing fields were kept.")
                    continue
                candidate = usable[labels.index(label)]
            if (page.launcher is not None and not page.launcher.text().strip()
                    and candidate.launcher_path is None and candidate.launcher_candidates):
                launcher, accepted = QInputDialog.getItem(self, "Select MPI launcher",
                    "Choose the launcher compatible with this FHI-aims build:",
                    list(candidate.launcher_candidates), 0, False)
                if not accepted:
                    details.append(
                        page.title + ": launcher selection cancelled; existing fields were kept."
                    )
                    continue
                page.launcher.setText(launcher)
            species_root_path = None
            if (
                page is self.fhi
                and not self.fhi.species_root.text().strip()
                and candidate.species_root_path is None
                and candidate.species_root_candidates
            ):
                species_root_path, accepted = QInputDialog.getItem(
                    self,
                    "Select species definitions root",
                    "Choose the root belonging to this FHI-aims installation:",
                    list(candidate.species_root_candidates),
                    0,
                    False,
                )
                if not accepted:
                    details.append(
                        "FHI-aims: species-root selection cancelled; existing "
                        "fields were kept."
                    )
                    continue
                candidate = replace(
                    candidate,
                    species_root_path=species_root_path,
                )
            page.apply_candidate(
                candidate,
                species_root_path=species_root_path,
            )
            details.append(page.title + ": " + candidate.executable_path)
            details.extend(candidate.notes)
        details.extend(result.missing_requirements)
        self.status.setPlainText("\n".join(details))

    def _apply(self):
        try:
            hints = self.hints()
            fhi = self.fhi.resolved_values()
            aitranss = self.aitranss.resolved_values()
            fhi_runtime = None if fhi is None else FhiAimsRuntimeConfiguration(
                fhi[0], hints.mpi_launcher, fhi[1])
            aitranss_runtime = None if aitranss is None else AitranssRuntimeConfiguration(
                aitranss[1].modules, aitranss[0], aitranss[1])
            launch_mode = None
            srun_path = None
            if self.aitranss.aitranss_launch_mode is not None:
                raw_launch_mode = self.aitranss.aitranss_launch_mode.currentData()
                launch_mode = (
                    None
                    if raw_launch_mode is None
                    else SlurmAitranssLaunchMode(raw_launch_mode)
                )
                entered_srun = self.slurm_aitranss_srun_path()
                if launch_mode is SlurmAitranssLaunchMode.SRUN:
                    srun_path = (
                        validate_srun_launcher_path(entered_srun)
                        if entered_srun is not None
                        else None
                    )
                elif launch_mode is None and entered_srun is not None:
                    raise ValueError(
                        "Select srun before configuring an AITRANSS srun executable."
                    )
        except ValueError as error:
            QMessageBox.warning(self, "Incomplete runtime configuration", str(error))
            return
        self.selected_hints = hints
        self.selected_fhi_runtime = fhi_runtime
        self.selected_aitranss_runtime = aitranss_runtime
        self.selected_slurm_aitranss_launch_mode = launch_mode
        self.selected_slurm_aitranss_srun_path = srun_path
        self.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def accept(self):
        if not self._busy:
            super().accept()

    def reject(self):
        if not self._busy:
            super().reject()
