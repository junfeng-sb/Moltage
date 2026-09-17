"""Background worker for read-only remote species input export."""

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from moltage.app.input_export import (
    AimsInputExportRequest,
    AimsInputExportService,
)


class AimsInputExportWorkerSignals(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class AimsInputExportWorker(QRunnable):
    """Run one confirmed export without blocking the Qt event loop."""

    def __init__(
        self,
        service: AimsInputExportService,
        request: AimsInputExportRequest,
    ) -> None:
        super().__init__()
        self._service = service
        self._request = request
        self.signals = AimsInputExportWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.export(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = None
            self.signals.finished.emit()
