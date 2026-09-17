"""Background workers for read-only AITRANSS preflight and explicit Step 4."""

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from moltage.app.transport_submission import (
    Step4SettingsRetryRequest,
    Step4SubmissionRequest,
    TransportWorkflowSubmissionService,
)
from moltage.domain.server_profile import ServerProfile


class _Signals(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class AitranssPreflightWorker(QRunnable):
    def __init__(
        self,
        service: TransportWorkflowSubmissionService,
        profile: ServerProfile,
        supplied_password: str | None,
    ) -> None:
        super().__init__()
        self.signals = _Signals()
        self._service = service
        self._profile = profile
        self._password = supplied_password
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.preflight_aitranss(
                self._profile,
                self._password,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._password = None
            self.signals.finished.emit(self)


class Step4SubmissionWorker(QRunnable):
    def __init__(
        self,
        service: TransportWorkflowSubmissionService,
        request: Step4SubmissionRequest,
    ) -> None:
        super().__init__()
        self.signals = _Signals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.submit_step4(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = None
            self.signals.finished.emit(self)


class Step4SettingsRetryWorker(QRunnable):
    """Submit one edited cancelled-Step-4 attempt off the GUI thread."""

    def __init__(
        self,
        service: TransportWorkflowSubmissionService,
        request: Step4SettingsRetryRequest,
    ) -> None:
        super().__init__()
        self.signals = _Signals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.retry_cancelled_step4_with_settings(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = None
            self.signals.finished.emit(self)
