from __future__ import annotations

import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from core.pdf_scan_split_engine import PdfScanSplitEngine, PdfScanSplitOptions, PdfScanSplitResult


class PdfScanSplitWorker(QThread):
    progressChanged = pyqtSignal(int, int)
    logAppended = pyqtSignal(str)
    finishedWithResult = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        pdf_path: str,
        reference_image_path: str,
        output_dir: str,
        prefix: str,
        options: PdfScanSplitOptions,
        task: str = "scan_split",
        page_limit: int = 0,
        probe_page_index: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._reference_image_path = reference_image_path
        self._output_dir = output_dir
        self._prefix = prefix
        self._options = options
        self._task = str(task or "scan_split")
        self._page_limit = int(page_limit or 0)
        self._probe_page_index = int(probe_page_index or 0)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _cancel_check(self) -> bool:
        return bool(self._cancelled)

    def _progress(self, current: int, total: int):
        self.progressChanged.emit(int(current), int(total))

    def _log(self, text: str):
        try:
            self.logAppended.emit(str(text))
        except Exception:
            try:
                self.logAppended.emit(repr(text))
            except Exception:
                pass

    def run(self):
        try:
            if self._task == "scan_only":
                result: PdfScanSplitResult = PdfScanSplitEngine.scan_only(
                    self._pdf_path,
                    self._reference_image_path,
                    self._options,
                    page_limit=self._page_limit,
                    progress=self._progress,
                    log=self._log,
                    cancel_check=self._cancel_check,
                )
                self.finishedWithResult.emit(result)
                return
            if self._task == "probe_page":
                result = PdfScanSplitEngine.probe_page(
                    self._pdf_path,
                    self._reference_image_path,
                    self._options,
                    page_index=self._probe_page_index,
                    cancel_check=self._cancel_check,
                )
                self.finishedWithResult.emit(result)
                return

            result: PdfScanSplitResult = PdfScanSplitEngine.execute(
                self._pdf_path,
                self._reference_image_path,
                output_dir=self._output_dir,
                prefix=self._prefix,
                options=self._options,
                progress=self._progress,
                log=self._log,
                cancel_check=self._cancel_check,
            )
            self.finishedWithResult.emit(result)
        except Exception as e:
            try:
                self._log(traceback.format_exc())
            except Exception:
                pass
            self.failed.emit(str(e))
