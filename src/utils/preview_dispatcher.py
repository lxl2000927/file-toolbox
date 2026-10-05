"""Run one preview in the background, retaining only the latest pending request."""
import logging
import threading


class PreviewCancelled(RuntimeError):
    pass


class LatestPreviewWorker:
    def __init__(self):
        self._condition = threading.Condition()
        self._pending = None
        self._active = None
        self._closed = False
        self._thread = None

    @staticmethod
    def _complete(callback, result, error):
        try:
            callback(result, error)
        except Exception:
            logging.exception("Could not deliver reference preview response")

    def submit(self, work, complete):
        with self._condition:
            if self._closed:
                raise RuntimeError("预览服务已关闭")
            replaced = self._pending
            self._pending = (work, complete)
            if self._active is not None:
                self._active.set()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="reference-preview", daemon=True)
                self._thread.start()
            self._condition.notify()
        if replaced is not None:
            self._complete(replaced[1], None, PreviewCancelled("预览请求已被更新"))

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                work, complete = self._pending
                self._pending = None
                cancelled = threading.Event()
                self._active = cancelled
            result, error = None, None
            try:
                result = work(cancelled)
                if cancelled.is_set():
                    raise PreviewCancelled("预览请求已被更新")
            except Exception as exc:
                error = exc
            self._complete(complete, result, error)
            with self._condition:
                self._active = None

    def close(self):
        with self._condition:
            self._closed = True
            pending = self._pending
            self._pending = None
            if self._active is not None:
                self._active.set()
            self._condition.notify_all()
        if pending is not None:
            self._complete(pending[1], None, PreviewCancelled("预览服务已关闭"))
