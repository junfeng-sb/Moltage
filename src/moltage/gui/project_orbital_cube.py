"""Asynchronous GUI adapter for one on-demand project orbital Cube load."""

from dataclasses import replace

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from moltage.app.project_orbital_cube import (
    ProjectOrbitalCubeLoadRequest,
    ProjectOrbitalCubeService,
)


class ProjectOrbitalCubeWorkerSignals(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal(object)


class ProjectOrbitalCubeWorker(QRunnable):
    """Download and parse one Cube without blocking the GUI thread."""

    def __init__(
        self,
        service: ProjectOrbitalCubeService,
        request: ProjectOrbitalCubeLoadRequest,
    ) -> None:
        super().__init__()
        self.signals = ProjectOrbitalCubeWorkerSignals()
        self._service = service
        self._request = request
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.load(
                self._request,
                progress=self.signals.progress.emit,
            )
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self._request = replace(self._request, supplied_password=None)
            self.signals.finished.emit(self)
