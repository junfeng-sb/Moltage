"""Independent cluster preset editor with bounded Slurm/LSF discovery."""

from dataclasses import replace
from pathlib import PurePosixPath
import shlex

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from moltage.app.connection_service import (
    AuthenticationError,
    ConnectionTestError,
    PasswordRequiredError,
    ServerConnectionService,
)
from moltage.domain.server_profile import (
    AitranssRuntimeConfiguration,
    LsfResourceRequirementMode,
    OrcaRuntimeConfiguration,
    RuntimeDiscoveryHints,
    RuntimeEnvironment,
    RuntimeEnvironmentMode,
    RuntimeLocation,
    RuntimeLocationKind,
    ServerProfile,
    SlurmCommandMode,
    SlurmAitranssLaunchMode,
    SlurmExecutionPreset,
    normalize_module_name,
    runtime_hours_from_minutes,
    runtime_minutes_from_hours,
    validate_lsf_env_directory,
    validate_lsf_library_directory,
    validate_lsf_server_directory,
    validate_fhi_species_defaults_path,
    validate_slurm_bin_directory,
)
from moltage.domain.scheduler import SchedulerKind, scheduler_display_name
from moltage.remote.executor import RemoteExecutorError
from moltage.gui.runtime_configuration_dialog import RuntimeConfigurationDialog
from moltage.gui.remote_directory_dialog import (
    RemoteDirectoryDialog,
    RemoteDirectoryWorker,
    normalize_remote_directory,
)
from moltage.remote.runtime_environment import (
    RuntimeConfigurationError,
    render_configured_fhi_launch,
    verify_srun_launcher,
)
from moltage.remote.runtime_search import discover_runtime_configuration, discover_server_runtimes
from moltage.remote.runtime_discovery import (
    RuntimeCandidate,
    RuntimeDiscoveryResult,
    RuntimeDiscoveryError,
    launch_command_with_verified_fhi_path,
)
from moltage.remote.known_hosts import (
    HostKeyInfo,
    HostKeyMismatch,
    KnownHostStore,
    UnknownHostKey,
)
from moltage.remote.slurm_discovery import (
    SlurmDiscoveryResult,
    SlurmDiscoverySource,
)
from moltage.remote.scheduler_discovery import (
    discover_scheduler,
    verify_scheduler_directory,
)
from moltage.remote.orca_runtime import (
    OrcaDiscoveryResult,
    OrcaRuntimeCandidate,
    discover_orca_runtimes,
    validate_orca_runtime,
)


class _SchedulerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class _SchedulerWorker(QRunnable):
    """Connect, discover/verify, and close without touching project storage."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        profile: ServerProfile,
        mode: SlurmCommandMode,
        bin_directory: str | None,
        scheduler_kind: SchedulerKind,
        lsf_env_directory: str | None,
        lsf_library_directory: str | None,
        lsf_server_directory: str | None,
        password: str | None,
    ) -> None:
        super().__init__()
        self.signals = _SchedulerSignals()
        self._connection_service = connection_service
        self._profile = profile
        self._mode = mode
        self._bin_directory = bin_directory
        self._scheduler_kind = scheduler_kind
        self._lsf_env_directory = lsf_env_directory
        self._lsf_library_directory = lsf_library_directory
        self._lsf_server_directory = lsf_server_directory
        self._password = password
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        executor = None
        try:
            executor = self._connection_service.connect_for_remote_operation(
                self._profile,
                self._password,
            )
            if self._mode is SlurmCommandMode.AUTOMATIC:
                result = discover_scheduler(
                    executor,
                    lsf_env_directory=self._lsf_env_directory,
                    lsf_library_directory=self._lsf_library_directory,
                    lsf_server_directory=self._lsf_server_directory,
                )
            else:
                directory = validate_slurm_bin_directory(self._bin_directory)
                result = verify_scheduler_directory(
                    executor,
                    directory,
                    self._scheduler_kind,
                    lsf_env_directory=self._lsf_env_directory,
                    lsf_library_directory=self._lsf_library_directory,
                    lsf_server_directory=self._lsf_server_directory,
                )
            executor.close()
            executor = None
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            if executor is not None:
                try:
                    executor.close()
                except Exception:
                    pass
            self._password = None
            self.signals.finished.emit(self)


class _RuntimeSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class _RuntimeWorker(QRunnable):
    """Connect and run only bounded server-runtime discovery."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        profile: ServerProfile,
        current_modules: tuple[str, ...],
        password: str | None,
        hints: RuntimeDiscoveryHints | None = None,
        allow_setup_scripts: bool = False,
        step4_srun_candidate: str | None = None,
    ) -> None:
        super().__init__()
        self.signals = _RuntimeSignals()
        self._connection_service = connection_service
        self._profile = profile
        self._current_modules = current_modules
        self._password = password
        self._hints = hints
        self._allow_setup_scripts = allow_setup_scripts
        self._step4_srun_candidate = step4_srun_candidate
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        executor = None
        try:
            executor = self._connection_service.connect_for_remote_operation(
                self._profile,
                self._password,
            )
            if self._hints is None:
                result = discover_server_runtimes(executor, self._current_modules)
            else:
                result = discover_runtime_configuration(
                    executor, self._hints, self._current_modules,
                    allow_setup_scripts=self._allow_setup_scripts)
            if self._step4_srun_candidate is not None:
                try:
                    verified_srun = verify_srun_launcher(
                        executor,
                        self._step4_srun_candidate,
                    )
                except RuntimeConfigurationError as error:
                    result = replace(
                        result,
                        diagnostics=(
                            *result.diagnostics,
                            f"Slurm Step 4 srun: {error}",
                        ),
                    )
                else:
                    result = replace(
                        result,
                        verified_srun_paths=tuple(
                            dict.fromkeys(
                                (*result.verified_srun_paths, verified_srun)
                            )
                        ),
                    )
            executor.close()
            executor = None
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            if executor is not None:
                try:
                    executor.close()
                except Exception:
                    pass
            self._password = None
            self.signals.finished.emit(self)


class _OrcaRuntimeWorker(QRunnable):
    """Run one user-triggered ORCA discovery or exact manual validation."""

    def __init__(
        self,
        connection_service: ServerConnectionService,
        profile: ServerProfile,
        *,
        scheduler_kind: SchedulerKind,
        location: RuntimeLocation,
        discover: bool,
        password: str | None,
    ) -> None:
        super().__init__()
        self.signals = _RuntimeSignals()
        self._connection_service = connection_service
        self._profile = profile
        self._scheduler_kind = scheduler_kind
        self._location = location
        self._discover = discover
        self._password = password
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        executor = None
        try:
            executor = self._connection_service.connect_for_remote_operation(
                self._profile,
                self._password,
            )
            if self._discover:
                result = discover_orca_runtimes(
                    executor,
                    scheduler_kind=self._scheduler_kind,
                    configured_location=self._location,
                )
            else:
                result = validate_orca_runtime(
                    executor,
                    self._location.location,
                    self._location.environment,
                )
            executor.close()
            executor = None
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            if executor is not None:
                try:
                    executor.close()
                except Exception:
                    pass
            self._password = None
            self.signals.finished.emit(self)


class ClusterExecutionSettingsDialog(QDialog):
    """Edit a working copy and expose a validated preset only after Save."""

    def __init__(
        self,
        profile: ServerProfile,
        parent: QWidget | None = None,
        *,
        connection_service: ServerConnectionService | None = None,
        known_hosts: KnownHostStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._connection_service = connection_service
        self._known_hosts = known_hosts
        self._selected_preset: SlurmExecutionPreset | None = None
        self._selected_aitranss_runtime: AitranssRuntimeConfiguration | None = None
        self._selected_orca_runtime: OrcaRuntimeConfiguration | None = None
        self._working_aitranss_runtime = profile.aitranss_runtime
        self._working_orca_runtime = profile.orca_runtime
        self._working_fhi_runtime = (
            profile.execution_preset.fhi_runtime if profile.execution_preset else None)
        self._working_runtime_hints = profile.runtime_hints
        self._working_slurm_aitranss_launch_mode = (
            profile.execution_preset.slurm_aitranss_launch_mode
            if profile.execution_preset is not None
            else None
        )
        self._working_slurm_aitranss_srun_path = (
            profile.execution_preset.slurm_aitranss_srun_path
            if profile.execution_preset is not None
            else None
        )
        self._working_species_root = (
            profile.execution_preset.fhi_species_defaults_path
            if profile.execution_preset is not None
            else None
        )
        if (
            self._working_species_root is None
            and profile.runtime_hints is not None
        ):
            self._working_species_root = (
                profile.runtime_hints.fhi_species_defaults_path
            )
        self._manual_dialog: RuntimeConfigurationDialog | None = None
        self._discovery_hints: RuntimeDiscoveryHints | None = None
        self._allow_setup_scripts = False
        self._verified_scheduler: SlurmDiscoveryResult | None = None
        self._automatic_bin_directory: str | None = None
        self._manual_bin_directory = ""
        self._last_scheduler_mode = SlurmCommandMode.AUTOMATIC
        self._configured_scheduler_kind = (
            profile.execution_preset.scheduler_kind
            if profile.execution_preset is not None
            else SchedulerKind.SLURM
        )
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._scheduler_workers: set[_SchedulerWorker] = set()
        self._scheduler_running = False
        self._scheduler_retry_pending = False
        self._scheduler_retry_password: str | None = None
        self._scheduler_trust_retry_used = False
        self._scheduler_password_retry_used = False
        self._runtime_workers: set[_RuntimeWorker] = set()
        self._runtime_running = False
        self._runtime_retry_pending = False
        self._runtime_retry_password: str | None = None
        self._runtime_trust_retry_used = False
        self._runtime_password_retry_used = False
        self._orca_workers: set[_OrcaRuntimeWorker] = set()
        self._orca_running = False
        self._orca_operation_is_discovery = False
        self._orca_retry_pending = False
        self._orca_retry_password: str | None = None
        self._orca_trust_retry_used = False
        self._orca_password_retry_used = False
        self._loading_orca_fields = False
        self._species_directory_dialog: RemoteDirectoryDialog | None = None
        self._species_directory_workers: set[RemoteDirectoryWorker] = set()
        self._species_directory_requested_path: str | None = None
        self._species_directory_password: str | None = None
        self._species_directory_retry_pending = False
        self._species_directory_trust_retry_used = False
        self._species_directory_password_retry_used = False

        self.setWindowTitle("Cluster Execution Settings")
        self.setMinimumWidth(900)
        layout = QVBoxLayout(self)

        server_row = QFormLayout()
        self._server_name = QLabel(profile.name, self)
        server_row.addRow("Server:", self._server_name)
        server_row.addRow(
            "Remote project workspace:",
            QLabel(profile.remote_project_root, self),
        )
        layout.addLayout(server_row)

        self._program_tabs = QTabWidget(self)
        self._program_tabs.setObjectName("clusterProgramPages")
        general_page = QWidget(self._program_tabs)
        general_page.setObjectName("generalClusterPage")
        general_layout = QVBoxLayout(general_page)
        self._program_tabs.addTab(general_page, "General / Cluster")
        fhi_page = QWidget(self._program_tabs)
        fhi_page.setObjectName("fhiAimsProgramPage")
        fhi_page_layout = QVBoxLayout(fhi_page)
        self._program_tabs.addTab(fhi_page, "FHI-aims")
        orca_page = QWidget(self._program_tabs)
        orca_page.setObjectName("orcaProgramPage")
        orca_page_layout = QVBoxLayout(orca_page)
        self._program_tabs.addTab(orca_page, "ORCA")
        layout.addWidget(self._program_tabs, 1)

        settings_columns = QHBoxLayout()
        left_column = QVBoxLayout()
        right_column = QVBoxLayout()
        settings_columns.addLayout(left_column, 1)
        settings_columns.addLayout(right_column, 1)
        general_layout.addLayout(settings_columns, 1)

        scheduler = QGroupBox("Scheduler", self)
        scheduler_form = QFormLayout(scheduler)
        self._scheduler_type = QLabel(
            scheduler_display_name(self._configured_scheduler_kind), scheduler
        )
        self._scheduler_type.setObjectName("schedulerType")
        scheduler_form.addRow("Scheduler:", self._scheduler_type)

        self._manual_scheduler_kind = QComboBox(scheduler)
        self._manual_scheduler_kind.setObjectName("manualSchedulerKind")
        self._manual_scheduler_kind.addItem("Slurm", SchedulerKind.SLURM.value)
        self._manual_scheduler_kind.addItem("LSF", SchedulerKind.LSF.value)
        self._manual_scheduler_kind.currentIndexChanged.connect(
            self._scheduler_kind_changed
        )
        scheduler_form.addRow("Manual scheduler type:", self._manual_scheduler_kind)

        mode_container = QWidget(scheduler)
        mode_layout = QVBoxLayout(mode_container)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        self._automatic_mode = QRadioButton(
            "Automatic detection (recommended)",
            mode_container,
        )
        self._automatic_mode.setObjectName("automaticSlurmDetection")
        self._manual_mode = QRadioButton("Manual", mode_container)
        self._manual_mode.setObjectName("manualSlurmLocation")
        mode_layout.addWidget(self._automatic_mode)
        mode_layout.addWidget(self._manual_mode)
        scheduler_form.addRow("Command location:", mode_container)

        self._slurm_bin_directory = QLineEdit(scheduler)
        self._slurm_bin_directory.setObjectName("slurmCommandDirectory")
        self._slurm_bin_directory.setPlaceholderText(
            "/path/to/slurm/bin or /path/to/lsf/bin"
        )
        scheduler_form.addRow(
            "Scheduler command directory:",
            self._slurm_bin_directory,
        )

        self._lsf_env_directory = QLineEdit(scheduler)
        self._lsf_env_directory.setObjectName("lsfEnvironmentDirectory")
        self._lsf_env_directory.setPlaceholderText(
            "/path/to/lsf/conf (directory containing lsf.conf)"
        )
        self._lsf_env_directory.setToolTip(
            "Directory containing the LSF client configuration file lsf.conf. "
            "For example: /opt/lsf/conf"
        )
        self._lsf_env_label = QLabel("LSF configuration directory:", scheduler)
        scheduler_form.addRow(self._lsf_env_label, self._lsf_env_directory)

        self._lsf_library_directory = QLineEdit(scheduler)
        self._lsf_library_directory.setObjectName("lsfLibraryDirectory")
        self._lsf_library_directory.setPlaceholderText("/path/to/lsf/platform/lib")
        self._lsf_library_directory.setToolTip(
            "Directory exported as LSF_LIBDIR. It normally sits beside the "
            "LSF command and server directories."
        )
        self._lsf_library_label = QLabel("LSF library directory:", scheduler)
        scheduler_form.addRow(
            self._lsf_library_label, self._lsf_library_directory
        )

        self._lsf_server_directory = QLineEdit(scheduler)
        self._lsf_server_directory.setObjectName("lsfServerDirectory")
        self._lsf_server_directory.setPlaceholderText("/path/to/lsf/platform/etc")
        self._lsf_server_directory.setToolTip(
            "Directory exported as LSF_SERVERDIR. LSF clients use it to find "
            "server-side helpers such as authentication and submission hooks."
        )
        self._lsf_server_label = QLabel("LSF server directory:", scheduler)
        scheduler_form.addRow(
            self._lsf_server_label, self._lsf_server_directory
        )

        self._scheduler_status = QLabel("Not detected", scheduler)
        self._scheduler_status.setObjectName("slurmDetectionStatus")
        self._scheduler_status.setWordWrap(True)
        self._sbatch_path = QLabel("—", scheduler)
        self._sbatch_path.setObjectName("detectedSbatchPath")
        self._sbatch_path.setWordWrap(True)
        self._slurm_version = QLabel("—", scheduler)
        self._slurm_version.setObjectName("detectedSlurmVersion")
        self._slurm_version.setWordWrap(True)
        self._detection_method = QLabel("—", scheduler)
        self._detection_method.setObjectName("slurmDetectionMethod")
        self._detection_method.setWordWrap(True)
        scheduler_form.addRow("Status:", self._scheduler_status)
        scheduler_form.addRow("Submit command:", self._sbatch_path)
        scheduler_form.addRow("Version:", self._slurm_version)
        scheduler_form.addRow("Detection method:", self._detection_method)

        self._scheduler_check_button = QPushButton("Detect Scheduler", scheduler)
        self._scheduler_check_button.setObjectName("detectSlurm")
        self._scheduler_check_button.clicked.connect(self._check_scheduler)
        scheduler_form.addRow("", self._scheduler_check_button)
        self._automatic_mode.toggled.connect(self._scheduler_mode_changed)
        self._manual_mode.toggled.connect(self._scheduler_mode_changed)
        self._slurm_bin_directory.textChanged.connect(
            self._scheduler_directory_edited
        )
        self._lsf_env_directory.textChanged.connect(
            self._lsf_environment_directory_edited
        )
        self._lsf_library_directory.textChanged.connect(
            self._lsf_environment_directory_edited
        )
        self._lsf_server_directory.textChanged.connect(
            self._lsf_environment_directory_edited
        )
        left_column.addWidget(scheduler)

        self._resource_stack = QStackedWidget(self)
        self._resource_stack.setObjectName("schedulerResourcePages")

        self._slurm_resources = QGroupBox("Slurm resources", self)
        resources_form = QFormLayout(self._slurm_resources)
        self._nodes = _positive_spin_box(self._slurm_resources)
        self._ntasks = _positive_spin_box(self._slurm_resources)
        self._cpus_per_task = _positive_spin_box(self._slurm_resources)
        self._runtime_hours = QDoubleSpinBox(self._slurm_resources)
        self._runtime_hours.setObjectName("maximumRuntimeHours")
        self._runtime_hours.setRange(0.1, 1_000_000.0)
        self._runtime_hours.setDecimals(1)
        self._runtime_hours.setSingleStep(0.1)
        self._runtime_hours.setSuffix(" hours")
        self._memory_gb = _positive_spin_box(self._slurm_resources)
        self._memory_gb.setObjectName("memoryLimitGb")
        self._memory_gb.setSuffix(" GB")
        self._omp_num_threads = _positive_spin_box(self._slurm_resources)
        self._slurm_account = QLineEdit(self._slurm_resources)
        self._slurm_account.setObjectName("slurmAccount")
        self._slurm_account.setPlaceholderText("Blank uses the site default")
        self._slurm_partition = QLineEdit(self._slurm_resources)
        self._slurm_partition.setObjectName("slurmPartition")
        self._slurm_partition.setPlaceholderText("Blank uses the site default")
        self._slurm_qos = QLineEdit(self._slurm_resources)
        self._slurm_qos.setObjectName("slurmQos")
        self._slurm_qos.setPlaceholderText("Blank uses the site default")
        resources_form.addRow("Account (optional):", self._slurm_account)
        resources_form.addRow("Partition (optional):", self._slurm_partition)
        resources_form.addRow("QoS (optional):", self._slurm_qos)
        resources_form.addRow("Nodes:", self._nodes)
        resources_form.addRow("MPI tasks:", self._ntasks)
        resources_form.addRow("CPUs per task:", self._cpus_per_task)
        resources_form.addRow("Maximum runtime:", self._runtime_hours)
        resources_form.addRow("Memory limit per node:", self._memory_gb)
        resources_form.addRow("OpenMP threads:", self._omp_num_threads)
        self._resource_stack.addWidget(self._slurm_resources)

        self._lsf_resources = QGroupBox("LSF resources", self)
        lsf_resources_form = QFormLayout(self._lsf_resources)
        self._lsf_hosts = _positive_spin_box(self._lsf_resources)
        self._lsf_hosts.setObjectName("lsfExecutionHosts")
        self._lsf_ntasks = _positive_spin_box(self._lsf_resources)
        self._lsf_ntasks.setObjectName("lsfMpiJobSlots")
        self._lsf_runtime_hours = QDoubleSpinBox(self._lsf_resources)
        self._lsf_runtime_hours.setObjectName("lsfMaximumRuntimeHours")
        self._lsf_runtime_hours.setRange(0.1, 1_000_000.0)
        self._lsf_runtime_hours.setDecimals(1)
        self._lsf_runtime_hours.setSingleStep(0.1)
        self._lsf_runtime_hours.setSuffix(" hours")
        self._lsf_memory_gb = _positive_spin_box(self._lsf_resources)
        self._lsf_memory_gb.setObjectName("lsfMemoryReservationGb")
        self._lsf_memory_gb.setSuffix(" GB")
        self._lsf_queue = QLineEdit(self._lsf_resources)
        self._lsf_queue.setObjectName("lsfQueue")
        self._lsf_queue.setPlaceholderText("Blank uses the site default")
        self._lsf_project = QLineEdit(self._lsf_resources)
        self._lsf_project.setObjectName("lsfProject")
        self._lsf_project.setPlaceholderText("Blank uses the site default")
        self._lsf_resource_mode = QComboBox(self._lsf_resources)
        self._lsf_resource_mode.setObjectName("lsfResourceRequirementMode")
        self._lsf_resource_mode.addItem(
            "Scheduler/site default",
            LsfResourceRequirementMode.SITE_DEFAULT,
        )
        self._lsf_resource_mode.addItem(
            "Structured span + rusage",
            LsfResourceRequirementMode.SPAN_RUSAGE,
        )
        self._lsf_resource_mode.currentIndexChanged.connect(
            self._lsf_resource_mode_changed
        )
        lsf_resources_form.addRow("Queue (optional):", self._lsf_queue)
        lsf_resources_form.addRow("Project (optional):", self._lsf_project)
        lsf_resources_form.addRow("Resource requirement:", self._lsf_resource_mode)
        self._lsf_hosts_label = QLabel("Execution hosts:", self._lsf_resources)
        lsf_resources_form.addRow(self._lsf_hosts_label, self._lsf_hosts)
        lsf_resources_form.addRow("MPI job slots / ranks:", self._lsf_ntasks)
        lsf_resources_form.addRow("Maximum runtime:", self._lsf_runtime_hours)
        self._lsf_memory_label = QLabel(
            "Memory reservation (LSF rusage):",
            self._lsf_resources,
        )
        lsf_resources_form.addRow(
            self._lsf_memory_label, self._lsf_memory_gb
        )
        self._lsf_resource_note = QLabel(self._lsf_resources)
        self._lsf_resource_note.setObjectName("lsfResourceRequirementExplanation")
        self._lsf_resource_note.setWordWrap(True)
        lsf_resources_form.addRow("Submitted placement:", self._lsf_resource_note)
        lsf_model = QLabel(
            "Pure MPI: one CPU and one OpenMP thread per rank. Host placement "
            "is generated from the host and rank counts; the rank count must "
            "divide evenly across hosts. Memory reservation scope follows the "
            "cluster's LSF configuration and is not a hard execution limit.",
            self._lsf_resources,
        )
        lsf_model.setObjectName("lsfExecutionModel")
        lsf_model.setWordWrap(True)
        lsf_resources_form.addRow("Execution model:", lsf_model)
        self._resource_stack.addWidget(self._lsf_resources)
        left_column.addWidget(self._resource_stack)

        self._behavior_stack = QStackedWidget(self)
        self._behavior_stack.setObjectName("schedulerBehaviorPages")
        slurm_behavior = QGroupBox("Slurm behavior", self)
        behavior_layout = QVBoxLayout(slurm_behavior)
        self._no_requeue = QCheckBox(
            "Do not automatically requeue the job",
            slurm_behavior,
        )
        self._no_requeue.setToolTip("Future directive: #SBATCH --no-requeue")
        self._export_none = QCheckBox(
            "Do not export the submission environment",
            slurm_behavior,
        )
        self._export_none.setToolTip("Future directive: #SBATCH --export=NONE")
        self._unset_slurm_export_env = QCheckBox(
            "Clear inherited Slurm environment",
            slurm_behavior,
        )
        self._unset_slurm_export_env.setToolTip(
            "Future command: unset SLURM_EXPORT_ENV"
        )
        behavior_layout.addWidget(self._no_requeue)
        behavior_layout.addWidget(self._export_none)
        behavior_layout.addWidget(self._unset_slurm_export_env)
        self._behavior_stack.addWidget(slurm_behavior)

        lsf_behavior = QGroupBox("LSF behavior", self)
        lsf_behavior_layout = QVBoxLayout(lsf_behavior)
        self._lsf_no_requeue = QCheckBox(
            "Mark the job as non-rerunnable",
            lsf_behavior,
        )
        self._lsf_no_requeue.setObjectName("lsfNoRerun")
        self._lsf_no_requeue.setToolTip("Directive: #BSUB -rn")
        self._lsf_export_none = QCheckBox(
            "Do not propagate the submission environment",
            lsf_behavior,
        )
        self._lsf_export_none.setObjectName("lsfEnvironmentNone")
        self._lsf_export_none.setToolTip('Directive: #BSUB -env "none"')
        lsf_behavior_layout.addWidget(self._lsf_no_requeue)
        lsf_behavior_layout.addWidget(self._lsf_export_none)
        lsf_note = QLabel(
            "LSF has no SLURM_EXPORT_ENV setting; no substitute directive is generated.",
            lsf_behavior,
        )
        lsf_note.setObjectName("lsfBehaviorBoundary")
        lsf_note.setWordWrap(True)
        lsf_behavior_layout.addWidget(lsf_note)
        self._behavior_stack.addWidget(lsf_behavior)
        right_column.addWidget(self._behavior_stack)

        self._fhi_toolchain_tabs = QTabWidget(fhi_page)
        self._fhi_toolchain_tabs.setObjectName("fhiAimsToolchainPages")
        fhi_runtime_page = QWidget(self._fhi_toolchain_tabs)
        fhi_runtime_page.setObjectName("fhiAimsRuntimePage")
        fhi_runtime_layout = QVBoxLayout(fhi_runtime_page)
        self._fhi_toolchain_tabs.addTab(fhi_runtime_page, "FHI-aims")
        aitranss_page = QWidget(self._fhi_toolchain_tabs)
        aitranss_page.setObjectName("aitranssRuntimePage")
        aitranss_layout = QVBoxLayout(aitranss_page)
        self._fhi_toolchain_tabs.addTab(aitranss_page, "AITRANSS")
        fhi_page_layout.addWidget(self._fhi_toolchain_tabs)

        environment = QGroupBox("FHI-aims environment and runtime", fhi_runtime_page)
        environment_layout = QVBoxLayout(environment)
        self._module_purge = QCheckBox(
            "Clear previously loaded environment modules",
            environment,
        )
        self._module_purge.setToolTip("Future command: module purge")
        environment_layout.addWidget(self._module_purge)
        environment_layout.addWidget(QLabel("Environment modules:", environment))
        self._modules = QListWidget(environment)
        self._modules.setObjectName("environmentModules")
        environment_layout.addWidget(self._modules)
        module_buttons = QHBoxLayout()
        self._add_module_button = QPushButton("Add module", environment)
        self._edit_module_button = QPushButton("Edit", environment)
        self._remove_module_button = QPushButton("Remove", environment)
        self._add_module_button.clicked.connect(self._add_module)
        self._edit_module_button.clicked.connect(self._edit_module)
        self._remove_module_button.clicked.connect(self._remove_module)
        module_buttons.addWidget(self._add_module_button)
        module_buttons.addWidget(self._edit_module_button)
        module_buttons.addWidget(self._remove_module_button)
        module_buttons.addStretch(1)
        environment_layout.addLayout(module_buttons)

        runtime_form = QFormLayout()
        self._fhi_runtime_path = QLabel("—", environment)
        self._fhi_runtime_path.setObjectName("discoveredFhiAimsPath")
        self._fhi_runtime_path.setWordWrap(True)
        self._fhi_species_root = QLabel("—", environment)
        self._fhi_species_root.setObjectName(
            "configuredFhiSpeciesDefinitionsRoot"
        )
        self._fhi_species_root.setWordWrap(True)
        self._runtime_status = QLabel("Not discovered", environment)
        self._runtime_status.setObjectName("runtimeDiscoveryStatus")
        self._runtime_status.setWordWrap(True)
        runtime_form.addRow("FHI-aims executable:", self._fhi_runtime_path)
        runtime_form.addRow(
            "Species definitions root:",
            self._fhi_species_root,
        )
        runtime_form.addRow("FHI-aims status:", self._runtime_status)
        environment_layout.addLayout(runtime_form)
        self._runtime_discovery_button = QPushButton(
            "Discover Runtime...",
            environment,
        )
        self._runtime_discovery_button.setObjectName("discoverRuntime")
        self._runtime_discovery_button.clicked.connect(
            self._discover_runtime
        )
        runtime_buttons = QHBoxLayout()
        runtime_buttons.addWidget(self._runtime_discovery_button)
        self._manual_runtime_button = QPushButton("Manual Configuration...", environment)
        self._manual_runtime_button.setObjectName("manualRuntimeConfiguration")
        self._manual_runtime_button.clicked.connect(self._open_manual_runtime)
        runtime_buttons.addWidget(self._manual_runtime_button)
        environment_layout.addLayout(runtime_buttons)
        fhi_runtime_layout.addWidget(environment)

        command_form = QFormLayout()
        self._launch_command = QLineEdit(self)
        self._launch_command.setObjectName("fhiAimsLaunchCommand")
        command_container = QWidget(self)
        command_layout = QVBoxLayout(command_container)
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.addWidget(self._launch_command)
        launch_helper = QLabel(
            "Command executed after environment modules are loaded.",
            command_container,
        )
        launch_helper.setWordWrap(True)
        command_layout.addWidget(launch_helper)
        self._slurm_output = QLineEdit(self)
        self._slurm_output.setObjectName("slurmStandardOutputFile")
        self._slurm_output.setPlaceholderText("e.g. aims.out")
        self._slurm_output.setToolTip(
            "Application output filename in the job working directory. "
            "Slurm writes this file directly. LSF creates it when the job starts "
            "and writes its separate scheduler report to <name>.lsf.log."
        )
        command_form.addRow("FHI-aims launch command:", command_container)
        command_form.addRow("Output file name:", self._slurm_output)
        fhi_runtime_layout.addLayout(command_form)
        fhi_runtime_layout.addStretch(1)

        aitranss_form = QFormLayout()
        self._aitranss_runtime_path = QLabel("—", aitranss_page)
        self._aitranss_runtime_path.setObjectName("discoveredAitranssPath")
        self._aitranss_runtime_path.setWordWrap(True)
        self._aitranss_runtime_status = QLabel("Not discovered", aitranss_page)
        self._aitranss_runtime_status.setObjectName("aitranssRuntimeStatus")
        self._aitranss_runtime_status.setWordWrap(True)
        aitranss_form.addRow("AITRANSS executable:", self._aitranss_runtime_path)
        aitranss_form.addRow("AITRANSS status:", self._aitranss_runtime_status)
        aitranss_layout.addLayout(aitranss_form)
        aitranss_buttons = QHBoxLayout()
        self._aitranss_discovery_button = QPushButton(
            "Discover Runtime...", aitranss_page
        )
        self._aitranss_discovery_button.setObjectName("discoverAitranssRuntime")
        self._aitranss_discovery_button.clicked.connect(self._discover_runtime)
        aitranss_buttons.addWidget(self._aitranss_discovery_button)
        self._aitranss_manual_runtime_button = QPushButton(
            "Manual Configuration...", aitranss_page
        )
        self._aitranss_manual_runtime_button.setObjectName(
            "manualAitranssRuntimeConfiguration"
        )
        self._aitranss_manual_runtime_button.clicked.connect(
            self._open_manual_runtime
        )
        aitranss_buttons.addWidget(self._aitranss_manual_runtime_button)
        aitranss_layout.addLayout(aitranss_buttons)
        aitranss_layout.addStretch(1)

        orca_form = QFormLayout()
        self._orca_executable = QLineEdit(orca_page)
        self._orca_executable.setObjectName("orcaExecutablePath")
        self._orca_executable.setPlaceholderText("/absolute/path/to/orca")
        self._orca_environment_mode = QComboBox(orca_page)
        self._orca_environment_mode.setObjectName("orcaEnvironmentMode")
        self._orca_environment_mode.addItem(
            "Auto (discovery only)", RuntimeEnvironmentMode.AUTO
        )
        self._orca_environment_mode.addItem(
            "No setup (login environment)", RuntimeEnvironmentMode.NONE
        )
        self._orca_environment_mode.addItem(
            "Environment modules", RuntimeEnvironmentMode.MODULES
        )
        self._orca_environment_mode.addItem(
            "Setup script", RuntimeEnvironmentMode.SCRIPT
        )
        self._orca_modules = QLineEdit(orca_page)
        self._orca_modules.setObjectName("orcaEnvironmentModules")
        self._orca_modules.setPlaceholderText("Comma-separated, in load order")
        self._orca_setup_script = QLineEdit(orca_page)
        self._orca_setup_script.setObjectName("orcaEnvironmentSetupScript")
        self._orca_setup_script.setPlaceholderText("/absolute/trusted/environment.sh")
        self._orca_version = QLabel("—", orca_page)
        self._orca_version.setObjectName("orcaRuntimeVersion")
        self._orca_version.setWordWrap(True)
        self._orca_status = QLabel("ORCA runtime is not configured", orca_page)
        self._orca_status.setObjectName("orcaRuntimeStatus")
        self._orca_status.setWordWrap(True)
        orca_form.addRow("ORCA executable:", self._orca_executable)
        orca_form.addRow("Environment:", self._orca_environment_mode)
        orca_form.addRow("Modules:", self._orca_modules)
        orca_form.addRow("Setup script:", self._orca_setup_script)
        orca_form.addRow("Version evidence:", self._orca_version)
        orca_form.addRow("Status:", self._orca_status)
        orca_page_layout.addLayout(orca_form)
        orca_buttons = QHBoxLayout()
        self._orca_discover_button = QPushButton("Discover ORCA", orca_page)
        self._orca_discover_button.setObjectName("discoverOrcaRuntime")
        self._orca_discover_button.clicked.connect(self._discover_orca)
        orca_buttons.addWidget(self._orca_discover_button)
        self._orca_validate_button = QPushButton("Validate Manual Path", orca_page)
        self._orca_validate_button.setObjectName("validateOrcaRuntime")
        self._orca_validate_button.clicked.connect(self._validate_orca)
        orca_buttons.addWidget(self._orca_validate_button)
        orca_page_layout.addLayout(orca_buttons)
        orca_note = QLabel(
            "Automatic discovery is bounded and available only for Slurm. "
            "LSF uses the same exact manual path and environment validation. "
            "Configuring ORCA is independent of FHI-aims/AITRANSS.",
            orca_page,
        )
        orca_note.setWordWrap(True)
        orca_page_layout.addWidget(orca_note)
        orca_page_layout.addStretch(1)
        self._orca_environment_mode.currentIndexChanged.connect(
            self._orca_environment_changed
        )
        self._orca_executable.textChanged.connect(self._orca_fields_changed)
        self._orca_modules.textChanged.connect(self._orca_fields_changed)
        self._orca_setup_script.textChanged.connect(self._orca_fields_changed)
        left_column.addStretch(1)
        right_column.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._cancel_button = QPushButton("Cancel", self)
        self._save_button = QPushButton("Save", self)
        self._cancel_button.setAutoDefault(False)
        self._save_button.setAutoDefault(True)
        self._save_button.setDefault(True)
        self._cancel_button.clicked.connect(self.reject)
        self._save_button.clicked.connect(self._save)
        buttons.addWidget(self._cancel_button)
        buttons.addWidget(self._save_button)
        layout.addLayout(buttons)

        self._load_working_copy(profile.execution_preset)
        self._show_saved_runtime()
        self._load_orca_runtime()
        self._ntasks.valueChanged.connect(self._sync_configured_runtime)
        self._lsf_ntasks.valueChanged.connect(self._sync_configured_runtime)
        self._sync_configured_runtime()
        self._dismiss_bypass = False
        self._editable_baseline = self._editable_state()

    def selected_runtime_hints(self) -> RuntimeDiscoveryHints | None:
        if self.result() != QDialog.DialogCode.Accepted:
            raise RuntimeError("Cluster Execution Settings were not saved")
        return self._working_runtime_hints

    def selected_orca_runtime(self) -> OrcaRuntimeConfiguration | None:
        if self.result() != QDialog.DialogCode.Accepted:
            raise RuntimeError("Cluster Execution Settings were not saved")
        return self._selected_orca_runtime

    def _orca_environment(self) -> RuntimeEnvironment:
        mode = RuntimeEnvironmentMode(self._orca_environment_mode.currentData())
        if mode is RuntimeEnvironmentMode.MODULES:
            modules = tuple(
                item.strip()
                for item in self._orca_modules.text().split(",")
                if item.strip()
            )
            return RuntimeEnvironment(mode, modules)
        if mode is RuntimeEnvironmentMode.SCRIPT:
            script = self._orca_setup_script.text().strip() or None
            return RuntimeEnvironment(mode, setup_script=script)
        return RuntimeEnvironment(mode)

    def _orca_location(self) -> RuntimeLocation:
        location = self._orca_executable.text().strip()
        environment = self._orca_environment()
        if not location and environment.mode is RuntimeEnvironmentMode.AUTO:
            return (
                self._working_runtime_hints.orca
                if self._working_runtime_hints is not None
                else RuntimeLocation()
            )
        return RuntimeLocation(
            location,
            environment,
            RuntimeLocationKind.EXECUTABLE,
        )

    def _load_orca_runtime(self) -> None:
        runtime = self._working_orca_runtime
        hint = (
            self._working_runtime_hints.orca
            if self._working_runtime_hints is not None
            else RuntimeLocation()
        )
        location = (
            RuntimeLocation(
                runtime.executable_path,
                runtime.environment,
                RuntimeLocationKind.EXECUTABLE,
            )
            if runtime is not None
            else hint
        )
        self._set_orca_fields(location)
        self._show_orca_runtime()
        self._orca_environment_changed()

    def _set_orca_fields(self, location: RuntimeLocation) -> None:
        self._loading_orca_fields = True
        try:
            self._orca_executable.setText(location.location)
            index = self._orca_environment_mode.findData(location.environment.mode)
            self._orca_environment_mode.setCurrentIndex(max(index, 0))
            self._orca_modules.setText(", ".join(location.environment.modules))
            self._orca_setup_script.setText(location.environment.setup_script or "")
        finally:
            self._loading_orca_fields = False

    def _show_orca_runtime(self) -> None:
        runtime = self._working_orca_runtime
        if runtime is None:
            self._orca_version.setText("—")
            self._orca_status.setText(
                "ORCA runtime is not configured; use discovery or validate an exact path."
            )
        else:
            evidence = runtime.version_evidence
            self._orca_version.setText(
                evidence.version or "Path verified; version not parsed"
            )
            self._orca_status.setText(
                "Verified supported ORCA runtime"
                if evidence.supported
                else "Executable verified; version support remains unverified"
            )
        self._update_orca_controls()

    @Slot()
    def _orca_environment_changed(self, *_args) -> None:
        mode = RuntimeEnvironmentMode(self._orca_environment_mode.currentData())
        self._orca_modules.setVisible(mode is RuntimeEnvironmentMode.MODULES)
        self._orca_setup_script.setVisible(mode is RuntimeEnvironmentMode.SCRIPT)
        self._orca_fields_changed()
        self._update_orca_controls()

    @Slot()
    def _orca_fields_changed(self, *_args) -> None:
        """Invalidate evidence when the path or environment no longer matches it."""

        if self._loading_orca_fields or self._working_orca_runtime is None:
            return
        try:
            current = self._orca_location()
        except Exception:
            current = None
        verified = RuntimeLocation(
            self._working_orca_runtime.executable_path,
            self._working_orca_runtime.environment,
            RuntimeLocationKind.EXECUTABLE,
        )
        if current == verified:
            return
        self._working_orca_runtime = None
        self._orca_version.setText("—")
        self._orca_status.setText(
            "ORCA path or environment changed; validate this configuration before use."
        )

    def _update_orca_controls(self) -> None:
        available = self._connection_service is not None and not self._orca_running
        is_slurm = self._current_scheduler_kind() is SchedulerKind.SLURM
        self._orca_discover_button.setEnabled(available and is_slurm)
        self._orca_discover_button.setToolTip(
            "Bounded login-PATH and ORCA-module discovery"
            if is_slurm
            else "Automatic ORCA discovery is not supported for LSF in this release"
        )
        self._orca_validate_button.setEnabled(available)
        for widget in (
            self._orca_executable,
            self._orca_environment_mode,
            self._orca_modules,
            self._orca_setup_script,
        ):
            widget.setEnabled(not self._orca_running)

    def _manual_hints(self) -> RuntimeDiscoveryHints:
        if self._working_runtime_hints is not None:
            return replace(
                self._working_runtime_hints,
                fhi_species_defaults_path=self._working_species_root,
            )
        fhi = self._working_fhi_runtime
        if fhi is not None:
            fhi_location = RuntimeLocation(fhi.executable_path, fhi.environment,
                                           RuntimeLocationKind.EXECUTABLE)
            launcher = fhi.launcher_path
        else:
            fhi_location = RuntimeLocation()
            launcher = ""
        aitranss = self._working_aitranss_runtime
        ait_location = RuntimeLocation()
        if aitranss is not None:
            env = aitranss.environment or RuntimeEnvironment(
                RuntimeEnvironmentMode.MODULES, aitranss.modules)
            ait_location = RuntimeLocation(aitranss.executable_path, env,
                                           RuntimeLocationKind.EXECUTABLE)
        return RuntimeDiscoveryHints(
            fhi_location,
            launcher,
            ait_location,
            self._working_species_root,
            (
                self._working_runtime_hints.orca
                if self._working_runtime_hints is not None
                else RuntimeLocation()
            ),
        )

    @Slot()
    def _open_manual_runtime(self, result=None) -> None:
        if self._scheduler_workers or (self._runtime_workers and result is None):
            return
        dialog = RuntimeConfigurationDialog(self._manual_hints(), self,
            fhi_runtime=self._working_fhi_runtime,
            aitranss_runtime=self._working_aitranss_runtime,
            discovery_available=self._connection_service is not None,
            scheduler_kind=self._current_scheduler_kind(),
            slurm_aitranss_launch_mode=(
                self._working_slurm_aitranss_launch_mode
            ),
            slurm_aitranss_srun_path=(
                self._working_slurm_aitranss_srun_path
            ))
        self._manual_dialog = dialog
        dialog.find_requested.connect(self._find_manual_missing)
        dialog.species_directory_requested.connect(
            self._browse_species_directory
        )
        dialog.set_busy(bool(self._runtime_workers))
        if isinstance(result, RuntimeDiscoveryResult):
            dialog.receive_result(result)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._apply_manual_runtime(dialog)
        finally:
            self._manual_dialog = None

    def _apply_manual_runtime(self, dialog) -> None:
        self._working_runtime_hints = dialog.selected_hints
        self._working_species_root = (
            dialog.selected_hints.fhi_species_defaults_path
        )
        fhi = dialog.selected_fhi_runtime
        if fhi is not None or self._working_fhi_runtime is not None:
            self._working_fhi_runtime = fhi
            if fhi is None:
                self._launch_command.clear()
        self._working_aitranss_runtime = dialog.selected_aitranss_runtime
        self._working_slurm_aitranss_launch_mode = (
            dialog.selected_slurm_aitranss_launch_mode
        )
        self._working_slurm_aitranss_srun_path = (
            dialog.selected_slurm_aitranss_srun_path
        )
        self._sync_configured_runtime()
        self._show_saved_runtime()
        self._runtime_status.setText("Runtime working copy updated; Save to persist. Startup not tested.")

    @Slot(str)
    def _browse_species_directory(self, initial_directory: str) -> None:
        if (
            self._connection_service is None
            or self._species_directory_workers
            or self._runtime_workers
            or self._scheduler_workers
        ):
            return
        try:
            initial = normalize_remote_directory(initial_directory or "/")
        except ValueError as error:
            self._show_runtime_error(error)
            return
        dialog = RemoteDirectoryDialog(
            initial,
            self._manual_dialog or self,
            window_title="Choose FHI-aims Species Definitions Root",
            explanation=(
                "Choose the directory whose immediate children include light, "
                "tight, and really_tight. Only folders on the selected server "
                "are shown; Remote Project Workspace is not changed."
            ),
            selection_label="FHI-aims species definitions root",
        )
        self._species_directory_dialog = dialog
        self._species_directory_requested_path = None
        self._species_directory_password = None
        self._species_directory_retry_pending = False
        self._species_directory_trust_retry_used = False
        self._species_directory_password_retry_used = False
        dialog.directory_requested.connect(
            self._request_species_directory_listing
        )
        dialog.request_current_directory()
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = validate_fhi_species_defaults_path(
                    dialog.selected_directory
                )
                if self._manual_dialog is not None:
                    self._manual_dialog.fhi.species_root.setText(selected)
                    self._manual_dialog.fhi.species_status.setText(
                        "Selected remote directory; contents are validated "
                        "during discovery and input preparation"
                    )
        except ValueError as error:
            self._show_runtime_error(error)
        finally:
            self._species_directory_dialog = None
            self._species_directory_requested_path = None
            self._species_directory_password = None
            self._species_directory_retry_pending = False

    @Slot(str)
    def _request_species_directory_listing(self, directory: str) -> None:
        if (
            self._species_directory_dialog is None
            or self._species_directory_workers
        ):
            return
        self._species_directory_requested_path = directory
        self._species_directory_retry_pending = False
        self._start_species_directory_worker(
            directory,
            self._species_directory_password,
        )

    def _start_species_directory_worker(
        self,
        directory: str,
        password: str | None,
    ) -> None:
        dialog = self._species_directory_dialog
        if dialog is None or self._connection_service is None:
            return
        dialog.set_busy(True)
        worker = RemoteDirectoryWorker(
            self._connection_service,
            self._profile,
            directory,
            password,
        )
        worker.signals.succeeded.connect(
            self._species_directory_succeeded
        )
        worker.signals.failed.connect(self._species_directory_failed)
        worker.signals.finished.connect(
            self._species_directory_worker_finished
        )
        self._species_directory_workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(object)
    def _species_directory_succeeded(self, result: object) -> None:
        self._species_directory_retry_pending = False
        dialog = self._species_directory_dialog
        if (
            dialog is None
            or not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], str)
            or not isinstance(result[1], tuple)
            or any(not isinstance(item, str) for item in result[1])
        ):
            self._show_species_directory_error(
                RuntimeError("Remote directory listing returned an invalid result")
            )
            return
        directory, names = result
        dialog.show_directory(directory, names)

    @Slot(object)
    def _species_directory_failed(self, error: object) -> None:
        dialog = self._species_directory_dialog
        if dialog is None:
            return
        if (
            isinstance(error, PasswordRequiredError)
            and not self._species_directory_password_retry_used
        ):
            password, accepted = QInputDialog.getText(
                dialog,
                "Password required",
                f"Password for {self._profile.name}:",
                QLineEdit.EchoMode.Password,
            )
            if accepted and password:
                self._species_directory_password_retry_used = True
                self._species_directory_password = password
                self._species_directory_retry_pending = True
                dialog.show_error(
                    "Password received; retrying remote directory listing."
                )
                return
            dialog.show_error("Remote directory browsing cancelled.")
            return
        if (
            isinstance(error, UnknownHostKey)
            and self._known_hosts is not None
            and not self._species_directory_trust_retry_used
        ):
            if self._confirm_unknown_host(
                error.info,
                "species-directory browsing",
            ):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_species_directory_error(trust_error)
                    return
                self._species_directory_trust_retry_used = True
                self._species_directory_retry_pending = True
                dialog.show_error(
                    "Host key trusted; retrying remote directory listing."
                )
                return
            dialog.show_error(
                "Remote directory browsing cancelled; host key was not trusted."
            )
            return
        self._species_directory_retry_pending = False
        self._show_species_directory_error(error)

    @Slot(object)
    def _species_directory_worker_finished(self, worker: object) -> None:
        if not isinstance(worker, RemoteDirectoryWorker):
            return
        self._species_directory_workers.discard(worker)
        if self._species_directory_workers:
            return
        if (
            self._species_directory_retry_pending
            and self._species_directory_requested_path is not None
        ):
            self._species_directory_retry_pending = False
            self._start_species_directory_worker(
                self._species_directory_requested_path,
                self._species_directory_password,
            )
            return
        if self._species_directory_dialog is not None:
            self._species_directory_dialog.set_busy(False)

    def _show_species_directory_error(self, error: object) -> None:
        message = (
            str(error)
            if isinstance(error, Exception)
            else "Remote directory browsing failed"
        )
        dialog = self._species_directory_dialog
        if dialog is not None:
            dialog.show_error(message)
        QMessageBox.critical(
            dialog or self,
            "Species definitions root unavailable",
            message,
        )


    def _sync_configured_runtime(self) -> None:
        runtime = self._working_fhi_runtime
        if runtime is not None:
            self._modules.clear()
            self._modules.addItems(runtime.environment.modules)
            self._module_purge.setChecked(runtime.environment.mode is RuntimeEnvironmentMode.MODULES)
            self._launch_command.setText(
                render_configured_fhi_launch(
                    runtime,
                    self._active_mpi_tasks(),
                ).removeprefix("exec ")
            )
        editable = runtime is None and not self._runtime_running
        self._launch_command.setReadOnly(runtime is not None)
        task_term = (
            "MPI tasks"
            if self._current_scheduler_kind() is SchedulerKind.SLURM
            else "LSF MPI job slots / ranks"
        )
        self._launch_command.setToolTip(
            f"Configured through Manual Configuration; task count follows {task_term}."
            if runtime
            else ""
        )
        for widget in (self._modules, self._module_purge, self._add_module_button,
                       self._edit_module_button, self._remove_module_button):
            widget.setEnabled(editable)

    @Slot(object)
    def _find_manual_missing(self, hints) -> None:
        self._begin_runtime_discovery(hints)

    def _step4_srun_candidate(self) -> str | None:
        if self._current_scheduler_kind() is not SchedulerKind.SLURM:
            return None
        if self._manual_dialog is not None:
            entered = self._manual_dialog.slurm_aitranss_srun_path()
            if entered:
                return entered
        if self._working_slurm_aitranss_srun_path:
            return self._working_slurm_aitranss_srun_path
        directory = (
            self._verified_scheduler.bin_directory
            if self._verified_scheduler is not None
            and self._verified_scheduler.scheduler_kind is SchedulerKind.SLURM
            else self._slurm_bin_directory.text().strip()
        )
        return str(PurePosixPath(directory) / "srun") if directory else None

    def _begin_runtime_discovery(self, hints) -> None:
        if (
            self._connection_service is None
            or self._runtime_workers
            or self._scheduler_workers
            or self._species_directory_workers
        ):
            return
        self._allow_setup_scripts = False
        if hints is not None:
            scripts = tuple(dict.fromkeys(item.environment.setup_script for item in
                (hints.fhi_aims, hints.aitranss)
                if item.environment.mode is RuntimeEnvironmentMode.SCRIPT))
            if scripts:
                answer = QMessageBox.question(self._manual_dialog or self,
                    "Execute selected environment setup?",
                    "Find Missing will source the following user-selected scripts on the server. "
                    "This executes their contents; it is not a read-only file inspection. "
                    "Use trusted environment-only scripts, never submission scripts.\n\n" + "\n".join(scripts),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self._allow_setup_scripts = True
        self._discovery_hints = hints
        self._runtime_retry_pending = False
        self._runtime_retry_password = None
        self._runtime_trust_retry_used = False
        self._runtime_password_retry_used = False
        self._start_runtime_worker(None)

    def selected_preset(self) -> SlurmExecutionPreset:
        if self._selected_preset is None:
            raise RuntimeError("Cluster Execution Settings were not saved")
        return self._selected_preset

    def selected_aitranss_runtime(
        self,
    ) -> AitranssRuntimeConfiguration | None:
        if self.result() != QDialog.DialogCode.Accepted:
            raise RuntimeError("Cluster Execution Settings were not saved")
        return self._selected_aitranss_runtime

    def _load_working_copy(
        self,
        preset: SlurmExecutionPreset | None,
    ) -> None:
        self._automatic_mode.blockSignals(True)
        self._manual_mode.blockSignals(True)
        if preset is None:
            mode = SlurmCommandMode.AUTOMATIC
            self._automatic_bin_directory = None
            self._manual_bin_directory = ""
            self._lsf_env_directory.clear()
            self._lsf_library_directory.clear()
            self._lsf_server_directory.clear()
            self._slurm_account.clear()
            self._slurm_partition.clear()
            self._slurm_qos.clear()
            self._lsf_queue.clear()
            self._lsf_project.clear()
            lsf_resource_mode = LsfResourceRequirementMode.SITE_DEFAULT
        else:
            mode = preset.slurm_command_mode
            self._configured_scheduler_kind = preset.scheduler_kind
            if mode is SlurmCommandMode.AUTOMATIC:
                self._automatic_bin_directory = preset.slurm_bin_directory
                self._manual_bin_directory = ""
            else:
                self._automatic_bin_directory = None
                self._manual_bin_directory = preset.slurm_bin_directory or ""
            self._lsf_env_directory.setText(preset.lsf_env_directory or "")
            self._lsf_library_directory.setText(
                preset.lsf_library_directory or ""
            )
            self._lsf_server_directory.setText(
                preset.lsf_server_directory or ""
            )
            self._slurm_account.setText(preset.slurm_account or "")
            self._slurm_partition.setText(preset.slurm_partition or "")
            self._slurm_qos.setText(preset.slurm_qos or "")
            self._lsf_queue.setText(preset.lsf_queue or "")
            self._lsf_project.setText(preset.lsf_project or "")
            lsf_resource_mode = (
                preset.lsf_resource_requirement_mode
                or LsfResourceRequirementMode.SITE_DEFAULT
            )
        self._automatic_mode.setChecked(mode is SlurmCommandMode.AUTOMATIC)
        self._manual_mode.setChecked(mode is SlurmCommandMode.MANUAL)
        self._automatic_mode.blockSignals(False)
        self._manual_mode.blockSignals(False)
        self._set_scheduler_kind_selector(self._configured_scheduler_kind)
        self._last_scheduler_mode = mode
        self._show_configured_scheduler_state(mode)

        self._nodes.setValue(1)
        self._ntasks.setValue(1)
        self._cpus_per_task.setValue(1)
        self._runtime_hours.setValue(0.1)
        self._memory_gb.setValue(1)
        self._omp_num_threads.setValue(1)
        self._no_requeue.setChecked(True)
        self._export_none.setChecked(True)
        self._unset_slurm_export_env.setChecked(True)
        self._lsf_hosts.setValue(1)
        self._lsf_ntasks.setValue(1)
        self._lsf_runtime_hours.setValue(0.1)
        self._lsf_memory_gb.setValue(1)
        self._lsf_no_requeue.setChecked(True)
        self._lsf_export_none.setChecked(True)
        self._lsf_resource_mode.setCurrentIndex(
            self._lsf_resource_mode.findData(lsf_resource_mode)
        )
        self._module_purge.setChecked(True)

        if preset is not None:
            if preset.scheduler_kind is SchedulerKind.SLURM:
                self._nodes.setValue(preset.nodes)
                self._ntasks.setValue(preset.ntasks)
                self._cpus_per_task.setValue(preset.cpus_per_task)
                self._runtime_hours.setValue(
                    runtime_hours_from_minutes(preset.runtime_minutes)
                )
                self._memory_gb.setValue(preset.memory_gb)
                self._omp_num_threads.setValue(preset.omp_num_threads)
                self._no_requeue.setChecked(preset.no_requeue)
                self._export_none.setChecked(preset.export_none)
                self._unset_slurm_export_env.setChecked(
                    preset.unset_slurm_export_env
                )
            else:
                self._lsf_hosts.setValue(preset.nodes)
                self._lsf_ntasks.setValue(preset.ntasks)
                self._lsf_runtime_hours.setValue(
                    runtime_hours_from_minutes(preset.runtime_minutes)
                )
                self._lsf_memory_gb.setValue(preset.memory_gb)
                self._lsf_no_requeue.setChecked(preset.no_requeue)
                self._lsf_export_none.setChecked(preset.export_none)
            self._module_purge.setChecked(preset.module_purge)
            self._modules.addItems(preset.modules)
            self._launch_command.setText(preset.launch_command)
            self._slurm_output.setText(preset.slurm_output_filename)
        self._apply_scheduler_kind_ui()
        self._lsf_resource_mode_changed()

    def _current_scheduler_kind(self) -> SchedulerKind:
        if (
            self._current_scheduler_mode() is SlurmCommandMode.AUTOMATIC
            and self._verified_scheduler is not None
        ):
            return self._verified_scheduler.scheduler_kind
        data = self._manual_scheduler_kind.currentData()
        return SchedulerKind(data)

    def _set_scheduler_kind_selector(self, kind: SchedulerKind) -> None:
        index = self._manual_scheduler_kind.findData(SchedulerKind(kind).value)
        if index >= 0:
            self._manual_scheduler_kind.blockSignals(True)
            self._manual_scheduler_kind.setCurrentIndex(index)
            self._manual_scheduler_kind.blockSignals(False)

    @Slot(int)
    def _scheduler_kind_changed(self, _index: int) -> None:
        kind = self._current_scheduler_kind()
        self._configured_scheduler_kind = kind
        self._verified_scheduler = None
        if self._current_scheduler_mode() is SlurmCommandMode.MANUAL:
            self._manual_bin_directory = ""
            self._slurm_bin_directory.clear()
        self._show_configured_scheduler_state(self._current_scheduler_mode())
        self._apply_scheduler_kind_ui()

    def _apply_scheduler_kind_ui(self) -> None:
        kind = self._current_scheduler_kind()
        self._scheduler_type.setText(scheduler_display_name(kind))
        if kind is SchedulerKind.SLURM:
            self._resource_stack.setCurrentWidget(self._slurm_resources)
            self._behavior_stack.setCurrentIndex(0)
        else:
            self._resource_stack.setCurrentWidget(self._lsf_resources)
            self._behavior_stack.setCurrentIndex(1)
        self._slurm_resources.setEnabled(kind is SchedulerKind.SLURM)
        self._lsf_resources.setEnabled(kind is SchedulerKind.LSF)
        self._behavior_stack.widget(0).setEnabled(kind is SchedulerKind.SLURM)
        self._behavior_stack.widget(1).setEnabled(kind is SchedulerKind.LSF)
        for label, editor in (
            (self._lsf_env_label, self._lsf_env_directory),
            (self._lsf_library_label, self._lsf_library_directory),
            (self._lsf_server_label, self._lsf_server_directory),
        ):
            label.setVisible(kind is SchedulerKind.LSF)
            editor.setVisible(kind is SchedulerKind.LSF)
            editor.setEnabled(
                kind is SchedulerKind.LSF and not self._scheduler_running
            )
        self._sync_configured_runtime()
        self._update_orca_controls()

    @Slot()
    def _lsf_resource_mode_changed(self, *_args) -> None:
        mode = LsfResourceRequirementMode(self._lsf_resource_mode.currentData())
        structured = mode is LsfResourceRequirementMode.SPAN_RUSAGE
        self._lsf_hosts.setEnabled(structured)
        self._lsf_memory_gb.setEnabled(structured)
        self._lsf_hosts_label.setText(
            "Execution hosts:" if structured else "Execution hosts (not submitted):"
        )
        self._lsf_memory_label.setText(
            "Memory reservation (LSF rusage):"
            if structured
            else "Memory reservation (not submitted):"
        )
        self._lsf_resource_note.setText(
            "Moltage will generate the structured span/rusage #BSUB -R request."
            if structured
            else "No #BSUB -R directive will be generated; LSF/site defaults "
            "control host placement and memory."
        )

    def _active_mpi_tasks(self) -> int:
        return (
            self._ntasks.value()
            if self._current_scheduler_kind() is SchedulerKind.SLURM
            else self._lsf_ntasks.value()
        )

    def _current_scheduler_mode(self) -> SlurmCommandMode:
        return (
            SlurmCommandMode.MANUAL
            if self._manual_mode.isChecked()
            else SlurmCommandMode.AUTOMATIC
        )

    @Slot(bool)
    def _scheduler_mode_changed(self, _checked: bool = False) -> None:
        mode = self._current_scheduler_mode()
        if mode is self._last_scheduler_mode:
            return
        if self._last_scheduler_mode is SlurmCommandMode.MANUAL:
            self._manual_bin_directory = self._slurm_bin_directory.text()
        self._verified_scheduler = None
        self._last_scheduler_mode = mode
        self._show_configured_scheduler_state(mode)
        self._apply_scheduler_kind_ui()

    @Slot(str)
    def _scheduler_directory_edited(self, text: str) -> None:
        if self._current_scheduler_mode() is SlurmCommandMode.MANUAL:
            self._manual_bin_directory = text
            self._verified_scheduler = None
            self._scheduler_status.setText("Not verified")
            self._sbatch_path.setText("—")
            self._slurm_version.setText("—")
            self._detection_method.setText("Manual configuration")

    @Slot(str)
    def _lsf_environment_directory_edited(self, _text: str) -> None:
        if self._current_scheduler_kind() is not SchedulerKind.LSF:
            return
        self._verified_scheduler = None
        self._scheduler_status.setText("Not verified")
        self._slurm_version.setText("—")

    def _show_configured_scheduler_state(self, mode: SlurmCommandMode) -> None:
        directory = (
            self._automatic_bin_directory
            if mode is SlurmCommandMode.AUTOMATIC
            else self._manual_bin_directory
        )
        self._slurm_bin_directory.blockSignals(True)
        self._slurm_bin_directory.setText(directory or "")
        self._slurm_bin_directory.blockSignals(False)
        self._slurm_bin_directory.setEnabled(mode is SlurmCommandMode.MANUAL)
        self._manual_scheduler_kind.setEnabled(mode is SlurmCommandMode.MANUAL)
        kind = self._current_scheduler_kind()
        submit_name = "sbatch" if kind is SchedulerKind.SLURM else "bsub"
        if directory:
            self._scheduler_status.setText("Saved location; verification required")
            self._sbatch_path.setText(
                str(PurePosixPath(directory) / submit_name)
            )
            self._slurm_version.setText("Not verified in this session")
            self._detection_method.setText(
                "Cached automatic location"
                if mode is SlurmCommandMode.AUTOMATIC
                else "Manual configuration"
            )
        else:
            self._scheduler_status.setText("Not detected")
            self._sbatch_path.setText("—")
            self._slurm_version.setText("—")
            self._detection_method.setText("—")
        self._scheduler_check_button.setText(
            "Verify"
            if mode is SlurmCommandMode.MANUAL
            else "Re-detect" if directory else "Detect Scheduler"
        )
        self._scheduler_check_button.setEnabled(
            self._connection_service is not None
        )

    @Slot()
    def _check_scheduler(self) -> None:
        if (
            self._connection_service is None
            or self._scheduler_workers
            or self._runtime_workers
        ):
            return
        mode = self._current_scheduler_mode()
        if mode is SlurmCommandMode.MANUAL:
            try:
                directory = validate_slurm_bin_directory(
                    self._slurm_bin_directory.text()
                )
            except Exception as error:
                self._show_scheduler_error(error)
                return
            self._manual_bin_directory = directory
        lsf_env_directory = None
        lsf_library_directory = None
        lsf_server_directory = None
        if self._current_scheduler_kind() is SchedulerKind.LSF:
            entered_values = (
                (
                    self._lsf_env_directory,
                    validate_lsf_env_directory,
                ),
                (
                    self._lsf_library_directory,
                    validate_lsf_library_directory,
                ),
                (
                    self._lsf_server_directory,
                    validate_lsf_server_directory,
                ),
            )
            normalized_values = []
            for editor, validator in entered_values:
                entered = editor.text().strip()
                if not entered:
                    normalized_values.append(None)
                    continue
                try:
                    normalized = validator(entered)
                except Exception as error:
                    self._show_scheduler_error(error)
                    return
                editor.setText(normalized)
                normalized_values.append(normalized)
            (
                lsf_env_directory,
                lsf_library_directory,
                lsf_server_directory,
            ) = normalized_values
            if mode is SlurmCommandMode.MANUAL and any(
                value is None for value in normalized_values
            ):
                self._show_scheduler_error(
                    ValueError(
                        "Enter the LSF configuration, library, and server directories."
                    )
                )
                return
        self._scheduler_retry_pending = False
        self._scheduler_retry_password = None
        self._scheduler_trust_retry_used = False
        self._scheduler_password_retry_used = False
        self._start_scheduler_worker(mode, None)

    def _start_scheduler_worker(
        self,
        mode: SlurmCommandMode,
        password: str | None,
    ) -> None:
        if self._connection_service is None:
            return
        self._set_scheduler_running(True)
        directory = (
            self._manual_bin_directory
            if mode is SlurmCommandMode.MANUAL
            else None
        )
        worker = _SchedulerWorker(
            self._connection_service,
            self._profile,
            mode,
            directory,
            self._current_scheduler_kind(),
            (
                self._lsf_env_directory.text().strip() or None
                if self._current_scheduler_kind() is SchedulerKind.LSF
                else None
            ),
            (
                self._lsf_library_directory.text().strip() or None
                if self._current_scheduler_kind() is SchedulerKind.LSF
                else None
            ),
            (
                self._lsf_server_directory.text().strip() or None
                if self._current_scheduler_kind() is SchedulerKind.LSF
                else None
            ),
            password,
        )
        worker.signals.succeeded.connect(self._scheduler_succeeded)
        worker.signals.failed.connect(self._scheduler_failed)
        worker.signals.finished.connect(self._scheduler_worker_finished)
        self._scheduler_workers.add(worker)
        self._scheduler_status.setText(
            "Verifying configured scheduler location..."
            if mode is SlurmCommandMode.MANUAL
            else "Detecting Slurm or LSF..."
        )
        self._thread_pool.start(worker)

    @Slot(object)
    def _scheduler_succeeded(self, result: object) -> None:
        self._scheduler_retry_pending = False
        self._scheduler_retry_password = None
        if not isinstance(result, SlurmDiscoveryResult):
            self._show_scheduler_error(
                RuntimeError("scheduler detection returned an invalid result")
            )
            return
        mode = self._current_scheduler_mode()
        if mode is SlurmCommandMode.AUTOMATIC:
            self._automatic_bin_directory = result.bin_directory
        else:
            self._manual_bin_directory = result.bin_directory
        self._verified_scheduler = result
        self._configured_scheduler_kind = result.scheduler_kind
        self._set_scheduler_kind_selector(result.scheduler_kind)
        self._slurm_bin_directory.blockSignals(True)
        self._slurm_bin_directory.setText(result.bin_directory)
        self._slurm_bin_directory.blockSignals(False)
        if result.scheduler_kind is SchedulerKind.LSF:
            for editor, value in (
                (self._lsf_env_directory, result.lsf_env_directory),
                (
                    self._lsf_library_directory,
                    result.lsf_library_directory,
                ),
                (self._lsf_server_directory, result.lsf_server_directory),
            ):
                editor.blockSignals(True)
                editor.setText(value or "")
                editor.blockSignals(False)
        self._scheduler_status.setText(
            f"✓ {scheduler_display_name(result.scheduler_kind)} detected"
        )
        self._sbatch_path.setText(result.sbatch_path)
        self._slurm_version.setText(result.version_text)
        self._detection_method.setText(_scheduler_source_label(result.discovery_source))
        self._scheduler_check_button.setText(
            "Verify" if mode is SlurmCommandMode.MANUAL else "Re-detect"
        )
        self._apply_scheduler_kind_ui()

    @Slot(object)
    def _scheduler_failed(self, error: object) -> None:
        if (
            isinstance(error, PasswordRequiredError)
            and not self._scheduler_password_retry_used
        ):
            password, accepted = QInputDialog.getText(
                self,
                "Password required",
                f"Password for {self._profile.name}:",
                QLineEdit.EchoMode.Password,
            )
            if accepted and password:
                self._scheduler_password_retry_used = True
                self._scheduler_retry_password = password
                self._scheduler_retry_pending = True
                self._scheduler_status.setText(
                    "Password received; retrying scheduler detection."
                )
                return
            self._scheduler_status.setText("Scheduler detection cancelled.")
            return
        if (
            isinstance(error, UnknownHostKey)
            and self._known_hosts is not None
            and not self._scheduler_trust_retry_used
        ):
            if self._confirm_unknown_host(error.info):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_scheduler_error(trust_error)
                    return
                self._scheduler_trust_retry_used = True
                self._scheduler_retry_pending = True
                self._scheduler_status.setText(
                    "Host key trusted; retrying scheduler detection."
                )
                return
            self._scheduler_status.setText(
                "Scheduler detection cancelled; host key was not trusted."
            )
            return
        self._scheduler_retry_pending = False
        self._scheduler_retry_password = None
        self._show_scheduler_error(error)

    @Slot(object)
    def _scheduler_worker_finished(self, worker: object) -> None:
        if not isinstance(worker, _SchedulerWorker):
            return
        self._scheduler_workers.discard(worker)
        if self._scheduler_workers:
            return
        if self._scheduler_retry_pending:
            self._scheduler_retry_pending = False
            self._start_scheduler_worker(
                self._current_scheduler_mode(),
                self._scheduler_retry_password,
            )
            return
        self._scheduler_retry_password = None
        self._set_scheduler_running(False)

    def _set_scheduler_running(self, running: bool) -> None:
        self._scheduler_running = running
        self._manual_runtime_button.setEnabled(not running)
        self._automatic_mode.setEnabled(not running)
        self._manual_mode.setEnabled(not running)
        self._manual_scheduler_kind.setEnabled(
            not running
            and self._current_scheduler_mode() is SlurmCommandMode.MANUAL
        )
        self._slurm_bin_directory.setEnabled(
            not running
            and self._current_scheduler_mode() is SlurmCommandMode.MANUAL
        )
        for editor in (
            self._lsf_env_directory,
            self._lsf_library_directory,
            self._lsf_server_directory,
        ):
            editor.setEnabled(
                not running
                and self._current_scheduler_kind() is SchedulerKind.LSF
            )
        self._scheduler_check_button.setEnabled(
            not running and self._connection_service is not None
        )
        self._runtime_discovery_button.setEnabled(
            not running and self._connection_service is not None
        )
        self._save_button.setEnabled(not running)
        self._cancel_button.setEnabled(not running)
        self._apply_scheduler_kind_ui()

    def _confirm_unknown_host(
        self,
        info: HostKeyInfo,
        purpose: str = "scheduler detection",
    ) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unknown SSH host key")
        box.setText(f"Trust this host key for {purpose}?")
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

    def _show_scheduler_error(self, error: object) -> None:
        message = str(error) if isinstance(error, Exception) else "Scheduler check failed"
        mode = self._current_scheduler_mode()
        if isinstance(error, AuthenticationError):
            title = "Authentication failed"
        elif isinstance(error, PasswordRequiredError):
            title = "Password required"
        elif isinstance(error, HostKeyMismatch):
            title = "SSH host key mismatch"
        elif isinstance(error, ConnectionTestError):
            title = "Connection failed"
        elif isinstance(error, RemoteExecutorError):
            title = "Connection failed"
        else:
            title = (
                "Scheduler configuration invalid"
                if mode is SlurmCommandMode.MANUAL
                else "Scheduler not detected"
            )
        self._scheduler_status.setText(message)
        QMessageBox.critical(self, title, message)

    def _show_saved_runtime(self) -> None:
        try:
            tokens = shlex.split(self._launch_command.text(), posix=True)
        except ValueError:
            tokens = []
        fhi_path = tokens[-1] if tokens and tokens[-1].startswith("/") else None
        if self._working_fhi_runtime is not None:
            fhi_path = self._working_fhi_runtime.executable_path
        runtime = self._working_aitranss_runtime
        self._fhi_runtime_path.setText(fhi_path or "—")
        self._fhi_species_root.setText(
            self._working_species_root or "—"
        )
        self._aitranss_runtime_path.setText(
            runtime.executable_path if runtime is not None else "—"
        )
        self._runtime_status.setText(
            "Configured runtime; startup not tested" if self._working_fhi_runtime is not None else
            "Saved verified runtime"
            if fhi_path is not None and runtime is not None
            else "Discovery required"
        )
        self._aitranss_runtime_status.setText(
            "Saved verified runtime" if runtime is not None else "Discovery required"
        )
        self._runtime_discovery_button.setEnabled(
            self._connection_service is not None
        )
        self._aitranss_discovery_button.setEnabled(
            self._connection_service is not None
        )

    @Slot()
    def _discover_runtime(self) -> None:
        if (
            self._connection_service is None
            or self._runtime_workers
            or self._scheduler_workers
        ):
            return
        self._begin_runtime_discovery(self._working_runtime_hints)

    @Slot()
    def _discover_orca(self) -> None:
        self._begin_orca_operation(discover=True)

    @Slot()
    def _validate_orca(self) -> None:
        self._begin_orca_operation(discover=False)

    def _begin_orca_operation(self, *, discover: bool) -> None:
        if (
            self._connection_service is None
            or self._orca_workers
            or self._scheduler_workers
            or self._runtime_workers
        ):
            return
        try:
            location = self._orca_location()
            if not discover and not location.location:
                raise ValueError("Enter the exact absolute ORCA executable path")
            if not discover and location.environment.mode is RuntimeEnvironmentMode.AUTO:
                raise ValueError(
                    "Select No setup, Environment modules, or Setup script before manual validation"
                )
        except Exception as error:
            self._show_orca_error(error)
            return
        if location.environment.mode is RuntimeEnvironmentMode.SCRIPT:
            answer = QMessageBox.question(
                self,
                "Execute selected environment setup?",
                "ORCA validation will source the explicitly selected trusted "
                "environment-only script on the server. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._orca_operation_is_discovery = discover
        self._orca_retry_pending = False
        self._orca_retry_password = None
        self._orca_trust_retry_used = False
        self._orca_password_retry_used = False
        self._start_orca_worker(location, None)

    def _start_orca_worker(
        self,
        location: RuntimeLocation,
        password: str | None,
    ) -> None:
        if self._connection_service is None:
            return
        self._orca_running = True
        self._update_orca_controls()
        self._save_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._orca_status.setText(
            "Discovering ORCA runtime..."
            if self._orca_operation_is_discovery
            else "Validating the exact ORCA runtime..."
        )
        worker = _OrcaRuntimeWorker(
            self._connection_service,
            self._profile,
            scheduler_kind=self._current_scheduler_kind(),
            location=location,
            discover=self._orca_operation_is_discovery,
            password=password,
        )
        worker._moltage_location = location
        worker.signals.succeeded.connect(self._orca_succeeded)
        worker.signals.failed.connect(self._orca_failed)
        worker.signals.finished.connect(self._orca_worker_finished)
        self._orca_workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(object)
    def _orca_succeeded(self, result: object) -> None:
        self._orca_retry_pending = False
        self._orca_retry_password = None
        if isinstance(result, OrcaRuntimeConfiguration):
            runtime = result
            diagnostics = ()
        elif isinstance(result, OrcaDiscoveryResult):
            diagnostics = result.diagnostics
            runtime = self._select_orca_candidate(result.candidates)
            if runtime is None:
                self._orca_status.setText(
                    "No ORCA runtime was selected. "
                    + ("; ".join(diagnostics) if diagnostics else "")
                )
                return
        else:
            self._show_orca_error(
                RuntimeError("ORCA runtime check returned an invalid result")
            )
            return
        self._set_orca_fields(
            RuntimeLocation(
                runtime.executable_path,
                runtime.environment,
                RuntimeLocationKind.EXECUTABLE,
            )
        )
        self._working_orca_runtime = runtime
        self._working_runtime_hints = replace(
            self._manual_hints(),
            orca=RuntimeLocation(
                runtime.executable_path,
                runtime.environment,
                RuntimeLocationKind.EXECUTABLE,
            ),
        )
        self._show_orca_runtime()
        if diagnostics:
            self._orca_status.setToolTip("\n".join(diagnostics))

    def _select_orca_candidate(
        self,
        candidates: tuple[OrcaRuntimeCandidate, ...],
    ) -> OrcaRuntimeConfiguration | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].runtime
        labels = tuple(
            f"{item.runtime.executable_path} — "
            f"{item.runtime.version_evidence.version or 'version unverified'} — "
            f"{item.source_label}"
            for item in candidates
        )
        selected, accepted = QInputDialog.getItem(
            self,
            "Select ORCA runtime",
            "Multiple independently verified candidates were found:",
            labels,
            0,
            False,
        )
        if not accepted:
            return None
        return candidates[labels.index(selected)].runtime

    @Slot(object)
    def _orca_failed(self, error: object) -> None:
        if isinstance(error, PasswordRequiredError) and not self._orca_password_retry_used:
            password, accepted = QInputDialog.getText(
                self,
                "Password required",
                f"Password for {self._profile.name}:",
                QLineEdit.EchoMode.Password,
            )
            if accepted and password:
                self._orca_password_retry_used = True
                self._orca_retry_password = password
                self._orca_retry_pending = True
                self._orca_status.setText("Password received; retrying ORCA check.")
                return
        if (
            isinstance(error, UnknownHostKey)
            and self._known_hosts is not None
            and not self._orca_trust_retry_used
        ):
            if self._confirm_unknown_host(error.info, "ORCA runtime validation"):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_orca_error(trust_error)
                    return
                self._orca_trust_retry_used = True
                self._orca_retry_pending = True
                self._orca_status.setText("Host key trusted; retrying ORCA check.")
                return
        self._orca_retry_pending = False
        self._show_orca_error(error)

    @Slot(object)
    def _orca_worker_finished(self, worker: object) -> None:
        if not isinstance(worker, _OrcaRuntimeWorker):
            return
        self._orca_workers.discard(worker)
        if self._orca_workers:
            return
        if self._orca_retry_pending:
            self._orca_retry_pending = False
            location = worker._moltage_location
            self._start_orca_worker(location, self._orca_retry_password)
            return
        self._orca_running = False
        self._save_button.setEnabled(True)
        self._cancel_button.setEnabled(True)
        self._update_orca_controls()

    def _show_orca_error(self, error: object) -> None:
        message = str(error) if isinstance(error, Exception) else "ORCA runtime check failed"
        self._orca_status.setText(message)
        QMessageBox.critical(self, "ORCA runtime unavailable", message)

    def _start_runtime_worker(self, password: str | None) -> None:
        if self._connection_service is None:
            return
        modules = tuple(
            self._modules.item(index).text()
            for index in range(self._modules.count())
        )
        self._set_runtime_running(True)
        worker = _RuntimeWorker(
            self._connection_service,
            self._profile,
            modules,
            password,
            self._discovery_hints,
            self._allow_setup_scripts,
            self._step4_srun_candidate(),
        )
        worker.signals.succeeded.connect(self._runtime_succeeded)
        worker.signals.failed.connect(self._runtime_failed)
        worker.signals.finished.connect(self._runtime_worker_finished)
        self._runtime_workers.add(worker)
        self._runtime_status.setText("Discovering server runtimes...")
        self._thread_pool.start(worker)

    @Slot(object)
    def _runtime_succeeded(self, result: object) -> None:
        self._runtime_retry_pending = False
        self._runtime_retry_password = None
        if not isinstance(result, RuntimeDiscoveryResult):
            self._show_runtime_error(
                RuntimeError("Server runtime discovery returned an invalid result")
            )
            return
        if self._manual_dialog is not None:
            self._manual_dialog.receive_result(result)
            return
        if (not result.fhi_aims_candidates or not result.aitranss_candidates
                or any(
                    item.species_root_path is None
                    for item in result.fhi_aims_candidates
                )
                or any(item.environment is not None for item in
                                    (*result.fhi_aims_candidates, *result.aitranss_candidates))):
            self._open_manual_runtime(result)
            return
        fhi = self._select_runtime_candidate(
            "Select FHI-aims module",
            result.fhi_aims_candidates,
        )
        if fhi is None:
            self._runtime_status.setText(
                "Runtime selection cancelled; settings were not changed."
            )
            return
        aitranss = self._select_runtime_candidate(
            "Select AITRANSS module",
            result.aitranss_candidates,
        )
        if aitranss is None:
            self._runtime_status.setText(
                "Runtime selection cancelled; settings were not changed."
            )
            return
        try:
            launch_command = launch_command_with_verified_fhi_path(
                self._launch_command.text(),
                fhi.executable_path,
            )
            runtime = AitranssRuntimeConfiguration(
                modules=aitranss.modules,
                executable_path=aitranss.executable_path,
            )
        except Exception as error:
            self._show_runtime_error(error)
            return
        self._modules.clear()
        self._modules.addItems(fhi.modules)
        self._launch_command.setText(launch_command)
        self._working_aitranss_runtime = runtime
        if (
            self._working_slurm_aitranss_launch_mode
            is SlurmAitranssLaunchMode.SRUN
            and self._working_slurm_aitranss_srun_path is None
        ):
            candidates = tuple(
                path
                for path in (
                    fhi.launcher_path,
                    *fhi.launcher_candidates,
                    *result.verified_srun_paths,
                )
                if path and PurePosixPath(path).name == "srun"
            )
            if candidates:
                self._working_slurm_aitranss_srun_path = candidates[0]
        self._working_species_root = fhi.species_root_path
        self._working_runtime_hints = replace(
            self._manual_hints(),
            fhi_species_defaults_path=self._working_species_root,
        )
        self._fhi_runtime_path.setText(fhi.executable_path)
        self._fhi_species_root.setText(
            self._working_species_root or "—"
        )
        self._aitranss_runtime_path.setText(aitranss.executable_path)
        self._runtime_status.setText("✓ Server runtimes verified")
        self._aitranss_runtime_status.setText("✓ AITRANSS runtime verified")
        self._runtime_status.setToolTip("\n".join(result.diagnostics))
        self._aitranss_runtime_status.setToolTip("\n".join(result.diagnostics))

    def _select_runtime_candidate(
        self,
        title: str,
        candidates: tuple[RuntimeCandidate, ...],
    ) -> RuntimeCandidate | None:
        if len(candidates) == 1:
            return candidates[0]
        labels = tuple(
            f"{candidate.module_name} — {candidate.executable_path}"
            for candidate in candidates
        )
        selected, accepted = QInputDialog.getItem(
            self,
            title,
            "Verified module:",
            labels,
            0,
            False,
        )
        if not accepted:
            return None
        return candidates[labels.index(selected)]

    @Slot(object)
    def _runtime_failed(self, error: object) -> None:
        if (
            isinstance(error, PasswordRequiredError)
            and not self._runtime_password_retry_used
        ):
            password, accepted = QInputDialog.getText(
                self,
                "Password required",
                f"Password for {self._profile.name}:",
                QLineEdit.EchoMode.Password,
            )
            if accepted and password:
                self._runtime_password_retry_used = True
                self._runtime_retry_password = password
                self._runtime_retry_pending = True
                self._runtime_status.setText(
                    "Password received; retrying server runtime discovery."
                )
                return
            self._runtime_status.setText("Runtime discovery cancelled.")
            return
        if (
            isinstance(error, UnknownHostKey)
            and self._known_hosts is not None
            and not self._runtime_trust_retry_used
        ):
            if self._confirm_unknown_host(error.info, "runtime discovery"):
                try:
                    self._known_hosts.trust(error.info)
                except Exception as trust_error:
                    self._show_runtime_error(trust_error)
                    return
                self._runtime_trust_retry_used = True
                self._runtime_retry_pending = True
                self._runtime_status.setText(
                    "Host key trusted; retrying server runtime discovery."
                )
                return
            self._runtime_status.setText(
                "Runtime discovery cancelled; host key was not trusted."
            )
            return
        self._runtime_retry_pending = False
        self._runtime_retry_password = None
        self._show_runtime_error(error)

    @Slot(object)
    def _runtime_worker_finished(self, worker: object) -> None:
        if not isinstance(worker, _RuntimeWorker):
            return
        self._runtime_workers.discard(worker)
        if self._runtime_workers:
            return
        if self._runtime_retry_pending:
            self._runtime_retry_pending = False
            self._start_runtime_worker(self._runtime_retry_password)
            return
        self._runtime_retry_password = None
        self._set_runtime_running(False)

    def _set_runtime_running(self, running: bool) -> None:
        self._runtime_running = running
        self._manual_runtime_button.setEnabled(not running)
        self._aitranss_manual_runtime_button.setEnabled(not running)
        if self._manual_dialog is not None:
            self._manual_dialog.set_busy(running)
        self._runtime_discovery_button.setEnabled(
            not running and self._connection_service is not None
        )
        self._aitranss_discovery_button.setEnabled(
            not running and self._connection_service is not None
        )
        self._scheduler_check_button.setEnabled(
            not running and self._connection_service is not None
        )
        self._save_button.setEnabled(not running)
        self._cancel_button.setEnabled(not running)
        for widget in (
            self._modules,
            self._add_module_button,
            self._edit_module_button,
            self._remove_module_button,
            self._launch_command,
        ):
            widget.setEnabled(not running)
        self._sync_configured_runtime()

    def _show_runtime_error(self, error: object) -> None:
        message = (
            str(error) if isinstance(error, Exception) else "Runtime discovery failed"
        )
        if isinstance(error, AuthenticationError):
            title = "Authentication failed"
        elif isinstance(error, PasswordRequiredError):
            title = "Password required"
        elif isinstance(error, HostKeyMismatch):
            title = "SSH host key mismatch"
        elif isinstance(error, (ConnectionTestError, RemoteExecutorError)):
            title = "Connection failed"
        else:
            title = "Server runtime not detected"
        self._runtime_status.setText(message)
        self._aitranss_runtime_status.setText(message)
        if self._manual_dialog is not None:
            self._manual_dialog.status.setPlainText(message)
        QMessageBox.critical(self._manual_dialog or self, title, message)

    @Slot()
    def _add_module(self) -> None:
        module_name, accepted = QInputDialog.getText(
            self,
            "Add environment module",
            "Module name:",
        )
        if accepted:
            self._append_valid_module(module_name)

    @Slot()
    def _edit_module(self) -> None:
        item = self._modules.currentItem()
        if item is None:
            return
        module_name, accepted = QInputDialog.getText(
            self,
            "Edit environment module",
            "Module name:",
            text=item.text(),
        )
        if not accepted:
            return
        try:
            normalized = normalize_module_name(module_name)
        except Exception as error:
            self._show_validation_error(error)
            return
        item.setText(normalized)

    @Slot()
    def _remove_module(self) -> None:
        row = self._modules.currentRow()
        if row >= 0:
            self._modules.takeItem(row)

    def _append_valid_module(self, module_name: str) -> None:
        try:
            normalized = normalize_module_name(module_name)
        except Exception as error:
            self._show_validation_error(error)
            return
        self._modules.addItem(normalized)

    @Slot()
    def _save(self) -> bool:
        if self._has_active_workers():
            self._scheduler_status.setText(
                "Wait for the current server check to finish before saving."
            )
            return False
        if not self._slurm_output.text().strip():
            answer = QMessageBox.question(
                self,
                "Save incomplete cluster settings?",
                "Output file name remains empty. Save anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            orca_hint = self._orca_location()
            self._working_runtime_hints = replace(
                self._manual_hints(),
                orca=orca_hint,
            )
            self._selected_preset = self._collect_preset()
            self._selected_aitranss_runtime = self._working_aitranss_runtime
            self._selected_orca_runtime = self._working_orca_runtime
        except Exception as error:
            self._show_validation_error(error)
            return False
        self.accept()
        return self.result() == QDialog.DialogCode.Accepted

    def _editable_state(self) -> dict[str, tuple[str, object]]:
        modules = tuple(
            self._modules.item(index).text()
            for index in range(self._modules.count())
        )
        return {
            "scheduler_kind": (
                "Manual scheduler type",
                self._manual_scheduler_kind.currentData(),
            ),
            "scheduler_mode": ("Command location", self._current_scheduler_mode()),
            "scheduler_directory": (
                "Scheduler command directory",
                self._slurm_bin_directory.text(),
            ),
            "lsf_env_directory": (
                "LSF configuration directory",
                self._lsf_env_directory.text(),
            ),
            "lsf_library_directory": (
                "LSF library directory",
                self._lsf_library_directory.text(),
            ),
            "lsf_server_directory": (
                "LSF server directory",
                self._lsf_server_directory.text(),
            ),
            "slurm_account": ("Slurm account", self._slurm_account.text()),
            "slurm_partition": ("Slurm partition", self._slurm_partition.text()),
            "slurm_qos": ("Slurm QoS", self._slurm_qos.text()),
            "slurm_nodes": ("Slurm nodes", self._nodes.value()),
            "slurm_tasks": ("Slurm MPI tasks", self._ntasks.value()),
            "slurm_cpus": ("Slurm CPUs per task", self._cpus_per_task.value()),
            "slurm_runtime": (
                "Slurm maximum runtime",
                self._runtime_hours.value(),
            ),
            "slurm_memory": ("Slurm memory limit", self._memory_gb.value()),
            "slurm_openmp": ("Slurm OpenMP threads", self._omp_num_threads.value()),
            "slurm_no_requeue": (
                "Slurm rerun policy",
                self._no_requeue.isChecked(),
            ),
            "slurm_export": (
                "Slurm submission environment",
                self._export_none.isChecked(),
            ),
            "slurm_unset_export": (
                "Inherited Slurm environment",
                self._unset_slurm_export_env.isChecked(),
            ),
            "lsf_queue": ("LSF queue", self._lsf_queue.text()),
            "lsf_project": ("LSF project", self._lsf_project.text()),
            "lsf_resource_mode": (
                "LSF resource requirement",
                self._lsf_resource_mode.currentData(),
            ),
            "lsf_hosts": ("LSF execution hosts", self._lsf_hosts.value()),
            "lsf_tasks": ("LSF MPI job slots", self._lsf_ntasks.value()),
            "lsf_runtime": (
                "LSF maximum runtime",
                self._lsf_runtime_hours.value(),
            ),
            "lsf_memory": (
                "LSF memory reservation",
                self._lsf_memory_gb.value(),
            ),
            "lsf_no_requeue": (
                "LSF rerun policy",
                self._lsf_no_requeue.isChecked(),
            ),
            "lsf_export": (
                "LSF submission environment",
                self._lsf_export_none.isChecked(),
            ),
            "module_purge": (
                "Environment module reset",
                self._module_purge.isChecked(),
            ),
            "modules": ("Environment modules", modules),
            "launch_command": ("FHI-aims launch command", self._launch_command.text()),
            "output_filename": ("Output file name", self._slurm_output.text()),
            "fhi_runtime": ("FHI-aims runtime", self._working_fhi_runtime),
            "species_root": ("Species definitions root", self._working_species_root),
            "aitranss_runtime": ("AITRANSS runtime", self._working_aitranss_runtime),
            "aitranss_launch_mode": (
                "AITRANSS launch mode",
                self._working_slurm_aitranss_launch_mode,
            ),
            "aitranss_srun": (
                "AITRANSS srun path",
                self._working_slurm_aitranss_srun_path,
            ),
            "runtime_hints": ("Runtime search hints", self._working_runtime_hints),
            "orca_executable": ("ORCA executable", self._orca_executable.text()),
            "orca_environment_mode": (
                "ORCA environment",
                self._orca_environment_mode.currentData(),
            ),
            "orca_modules": ("ORCA modules", self._orca_modules.text()),
            "orca_setup_script": (
                "ORCA setup script",
                self._orca_setup_script.text(),
            ),
            "orca_runtime": ("ORCA runtime", self._working_orca_runtime),
        }

    def _changed_field_labels(self) -> tuple[str, ...]:
        current = self._editable_state()
        return tuple(
            current[key][0]
            for key in current
            if self._editable_baseline.get(key, ("", object()))[1] != current[key][1]
        )

    def _prompt_unsaved_changes(self, changed_fields: tuple[str, ...]) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved cluster settings")
        box.setText("You have unsaved changes.")
        box.setInformativeText(
            "Changed fields:\n" + "\n".join(f"• {label}" for label in changed_fields)
        )
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is save:
            return "save"
        if box.clickedButton() is discard:
            return "discard"
        return "cancel"

    def _has_active_workers(self) -> bool:
        return bool(
            self._scheduler_workers
            or self._runtime_workers
            or self._species_directory_workers
            or self._orca_workers
        )

    def _reject_without_prompt(self) -> None:
        self._dismiss_bypass = True
        try:
            super().reject()
        finally:
            self._dismiss_bypass = False

    def _collect_preset(self) -> SlurmExecutionPreset:
        modules = tuple(
            self._modules.item(index).text()
            for index in range(self._modules.count())
        )
        command_mode = self._current_scheduler_mode()
        if command_mode is SlurmCommandMode.AUTOMATIC:
            bin_directory = self._automatic_bin_directory
        else:
            bin_directory = validate_slurm_bin_directory(
                self._slurm_bin_directory.text()
            )
        scheduler_kind = self._current_scheduler_kind()
        is_slurm = scheduler_kind is SchedulerKind.SLURM
        lsf_env_directory = None
        lsf_library_directory = None
        lsf_server_directory = None
        if not is_slurm:
            if self._lsf_env_directory.text().strip():
                lsf_env_directory = validate_lsf_env_directory(
                    self._lsf_env_directory.text().strip()
                )
            if self._lsf_library_directory.text().strip():
                lsf_library_directory = validate_lsf_library_directory(
                    self._lsf_library_directory.text().strip()
                )
            if self._lsf_server_directory.text().strip():
                lsf_server_directory = validate_lsf_server_directory(
                    self._lsf_server_directory.text().strip()
                )
        if (
            not is_slurm
            and command_mode is SlurmCommandMode.MANUAL
            and any(
                value is None
                for value in (
                    lsf_env_directory,
                    lsf_library_directory,
                    lsf_server_directory,
                )
            )
        ):
            raise ValueError(
                "Enter the LSF configuration, library, and server directories."
            )
        return SlurmExecutionPreset(
            nodes=(self._nodes.value() if is_slurm else self._lsf_hosts.value()),
            ntasks=(self._ntasks.value() if is_slurm else self._lsf_ntasks.value()),
            cpus_per_task=(self._cpus_per_task.value() if is_slurm else 1),
            runtime_minutes=runtime_minutes_from_hours(
                self._runtime_hours.value()
                if is_slurm
                else self._lsf_runtime_hours.value()
            ),
            memory_gb=(
                self._memory_gb.value()
                if is_slurm
                else self._lsf_memory_gb.value()
            ),
            no_requeue=(
                self._no_requeue.isChecked()
                if is_slurm
                else self._lsf_no_requeue.isChecked()
            ),
            export_none=(
                self._export_none.isChecked()
                if is_slurm
                else self._lsf_export_none.isChecked()
            ),
            unset_slurm_export_env=(
                self._unset_slurm_export_env.isChecked() if is_slurm else False
            ),
            omp_num_threads=(self._omp_num_threads.value() if is_slurm else 1),
            module_purge=self._module_purge.isChecked(),
            modules=modules,
            launch_command=self._launch_command.text(),
            slurm_output_filename=self._slurm_output.text(),
            slurm_command_mode=command_mode,
            slurm_bin_directory=bin_directory,
            lsf_env_directory=lsf_env_directory,
            lsf_library_directory=lsf_library_directory,
            lsf_server_directory=lsf_server_directory,
            fhi_runtime=self._working_fhi_runtime,
            scheduler_kind=scheduler_kind,
            fhi_species_defaults_path=self._working_species_root,
            slurm_account=(self._slurm_account.text() if is_slurm else None),
            slurm_partition=(
                self._slurm_partition.text() if is_slurm else None
            ),
            slurm_qos=(self._slurm_qos.text() if is_slurm else None),
            lsf_queue=(self._lsf_queue.text() if not is_slurm else None),
            lsf_project=(self._lsf_project.text() if not is_slurm else None),
            lsf_resource_requirement_mode=(
                self._lsf_resource_mode.currentData() if not is_slurm else None
            ),
            slurm_aitranss_launch_mode=(
                self._working_slurm_aitranss_launch_mode if is_slurm else None
            ),
            slurm_aitranss_srun_path=(
                self._working_slurm_aitranss_srun_path if is_slurm else None
            ),
        )

    def _show_validation_error(self, error: Exception) -> None:
        QMessageBox.critical(self, "Invalid cluster settings", str(error))

    def accept(self) -> None:
        if self._has_active_workers():
            self._scheduler_status.setText(
                "Wait for the scheduler check to finish before closing."
            )
            return
        super().accept()

    def reject(self) -> None:
        if self._dismiss_bypass:
            super().reject()
            return
        if self._has_active_workers():
            self._scheduler_status.setText(
                "Wait for the scheduler check to finish before closing."
            )
            return
        changed_fields = self._changed_field_labels()
        if not changed_fields:
            self._reject_without_prompt()
            return
        choice = self._prompt_unsaved_changes(changed_fields)
        if choice == "save":
            self._save()
        elif choice == "discard":
            self._reject_without_prompt()

    def closeEvent(self, event) -> None:
        if self._dismiss_bypass:
            event.accept()
            return
        if not self.isVisible():
            event.accept()
            return
        event.ignore()
        self.reject()


def _positive_spin_box(parent: QWidget) -> QSpinBox:
    spin_box = QSpinBox(parent)
    spin_box.setRange(1, 2_000_000_000)
    return spin_box


def _scheduler_source_label(source: SlurmDiscoverySource) -> str:
    labels = {
        SlurmDiscoverySource.CURRENT_ENVIRONMENT: "Remote environment",
        SlurmDiscoverySource.LOGIN_SHELL: "Login shell",
        SlurmDiscoverySource.CACHED_AUTOMATIC: "Cached automatic location",
        SlurmDiscoverySource.MANUAL: "Manual configuration",
    }
    return labels[source]
