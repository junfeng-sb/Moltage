"""Bound one status refresh without making shutdown wait for network I/O."""

from collections.abc import Callable
from threading import Lock, Thread
from time import monotonic

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Qt, Signal, Slot

from moltage.remote.executor import RemoteOperationStopped, RemoteOperationStopToken


STATUS_REFRESH_TIMEOUT_MS = 60_000


class StatusRefreshTimeout(RuntimeError):
    """A refresh deadline expired, not a calculation or scheduler timeout."""


class _RefreshResults(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)


class StatusRefreshSession(QObject):
    """Deliver only current results from one cooperative, daemon refresh."""

    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    stopped = Signal()
    finished = Signal(object)

    def __init__(
        self,
        operation: Callable[[RemoteOperationStopToken, Callable[[str], None]], object],
        parent: QObject | None = None,
        *,
        timeout_ms: int = STATUS_REFRESH_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent if parent is not None else QCoreApplication.instance())
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("status refresh timeout must be a positive integer")
        self._operation = operation
        self._stop_token = RemoteOperationStopToken()
        self._active = False
        self._started = False
        self._deadline = 0.0
        self._signal_lock = Lock()
        self._results = _RefreshResults(self)
        self._results.progress.connect(self._report_progress)
        self._results.succeeded.connect(self._succeeded)
        self._results.failed.connect(self._failed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._timed_out)

    def start(self) -> None:
        if self._started:
            raise RuntimeError("a status refresh session can only start once")
        self._started = True
        self._active = True
        self._deadline = monotonic() + self._timer.interval() / 1000.0
        self._timer.start()
        # Capture no widget/session in the daemon. Qt owns result delivery;
        # deleting the receiver disconnects it without joining network I/O.
        operation, stop_token, results = self._operation, self._stop_token, self._results
        signal_lock = self._signal_lock
        self._operation = None

        def report(message: str) -> None:
            with signal_lock:
                stop_token.checkpoint()
                results.progress.emit(message)

        def run() -> None:
            try:
                stop_token.checkpoint()
                result = operation(stop_token, report)
                stop_token.checkpoint()
            except RemoteOperationStopped:
                return
            except Exception as error:
                with signal_lock:
                    if not stop_token.is_requested:
                        results.failed.emit(error)
            else:
                with signal_lock:
                    if not stop_token.is_requested:
                        results.succeeded.emit(result)

        Thread(target=run, name="moltage-status-refresh", daemon=True).start()

    def request_stop(self) -> None:
        """Finish locally now; never wait for the transport or cancel a job."""

        if not self._active:
            return
        self._active = False
        self._timer.stop()
        # The lock covers signal emission only, never network I/O. Once stop
        # returns, Qt can delete the receiver safely even if I/O stays blocked.
        with self._signal_lock:
            self._stop_token.request_stop()
        self.stopped.emit()
        self.finished.emit(self)

    def _accept_result(self) -> bool:
        if not self._active:
            return False
        if monotonic() >= self._deadline:
            self._timed_out()
            return False
        return True

    @Slot(str)
    def _report_progress(self, message: str) -> None:
        if self._accept_result():
            self.progress.emit(message)

    @Slot(object)
    def _succeeded(self, result: object) -> None:
        if not self._accept_result():
            return
        # Finish emission on the daemon before a receiver deletes this QObject.
        with self._signal_lock:
            self._active = False
        self._timer.stop()
        self.succeeded.emit(result)
        self.finished.emit(self)

    @Slot(object)
    def _failed(self, error: object) -> None:
        if not self._accept_result():
            return
        with self._signal_lock:
            self._active = False
        self._timer.stop()
        self.failed.emit(error)
        self.finished.emit(self)

    @Slot()
    def _timed_out(self) -> None:
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        with self._signal_lock:
            self._stop_token.request_stop()
        seconds = self._timer.interval() / 1000.0
        self.failed.emit(StatusRefreshTimeout(
            f"Status refresh timed out after {seconds:g} seconds. "
            "Last known task states were retained. No calculation was cancelled; "
            "check the connection and refresh again."
        ))
        self.finished.emit(self)
