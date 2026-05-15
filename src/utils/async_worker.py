from dataclasses import dataclass
import traceback

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


@dataclass(frozen=True)
class WorkerError:
    exc_type: str
    message: str
    traceback: str


class WorkerSignals(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(object)


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(
                WorkerError(
                    exc_type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(),
                )
            )
