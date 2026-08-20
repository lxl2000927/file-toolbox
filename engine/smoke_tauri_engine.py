"""Exercise the packaged Tauri Python engine over its JSON-RPC stdio protocol."""

from __future__ import annotations

import json
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, TextIO

import cv2
import fitz
import numpy as np
import zxingcpp
from pypdf import PdfWriter


READY_TIMEOUT_SECONDS = 30
SHUTDOWN_TIMEOUT_SECONDS = 10
TASK_TIMEOUT_SECONDS = 120


class EngineProtocol:
    def __init__(self, process: subprocess.Popen[str], token: str) -> None:
        self.process = process
        self.token = token
        self.next_request_id = 1
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.backlog: list[dict[str, Any]] = []
        self.stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            daemon=True,
        )
        self.stdout_thread.start()

    def _read_stdout(self, stream: TextIO | None) -> None:
        if stream is None:
            self.messages.put(None)
            return
        try:
            for line in stream:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise RuntimeError(f"engine wrote non-object JSON: {message!r}")
                self.messages.put(message)
        except Exception as exc:
            self.messages.put({"_protocol_error": str(exc)})
        self.messages.put(None)

    def _next_message(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for engine protocol message")
        try:
            message = self.messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("timed out waiting for engine protocol message") from exc
        if message is None:
            raise RuntimeError(f"engine exited before responding (exit code {self.process.poll()})")
        if "_protocol_error" in message:
            raise RuntimeError(f"engine protocol read failed: {message['_protocol_error']}")
        return message

    def _wait_for_message(self, deadline: float, predicate) -> dict[str, Any]:
        for index, message in enumerate(self.backlog):
            if predicate(message):
                return self.backlog.pop(index)
        while True:
            message = self._next_message(deadline)
            if predicate(message):
                return message
            self.backlog.append(message)

    def wait_for_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        self._wait_for_message(deadline, lambda message: message.get("method") == "ready")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.process.stdin is None:
            raise RuntimeError("engine stdin is unavailable")
        request_id = self.next_request_id
        self.next_request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
            "auth": self.token,
        }
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        message = self._wait_for_message(deadline, lambda message: message.get("id") == request_id)
        if "error" in message:
            raise RuntimeError(f"{method} failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"{method} returned a non-object result: {result!r}")
        return result

    def wait_for_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + TASK_TIMEOUT_SECONDS

        def is_task_notification(message: dict[str, Any]) -> bool:
            params = message.get("params")
            return (
                message.get("method") in {"task.progress", "task.log", "task.complete"}
                and isinstance(params, dict)
                and params.get("task_id") == task_id
            )

        while True:
            message = self._wait_for_message(deadline, is_task_notification)
            if message.get("method") == "task.complete":
                return message["params"]


def _drain(stream: TextIO | None) -> None:
    if stream is not None:
        for _ in stream:
            pass


def cleanup_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass


def _engine_path() -> Path:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_tauri_engine.py <engine.exe>")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"engine executable does not exist: {executable}")
    return executable


def _create_scan_fixture(temporary_directory: Path) -> tuple[Path, Path, Path]:
    qr_path = temporary_directory / "marker.png"
    barcode = zxingcpp.create_barcode("tauri-scan-smoke", zxingcpp.BarcodeFormat.QRCode)
    qr_image = np.asarray(zxingcpp.write_barcode_to_image(barcode, size_hint=300))
    if not cv2.imwrite(str(qr_path), qr_image):
        raise RuntimeError("failed to write QR smoke fixture")

    pdf_path = temporary_directory / "four-pages.pdf"
    document = fitz.open()
    try:
        for page_index in range(4):
            page = document.new_page(width=612, height=792)
            page.insert_text((72, 72), f"Smoke page {page_index + 1}")
            if page_index in {0, 2}:
                page.insert_image(fitz.Rect(156, 216, 456, 516), filename=str(qr_path))
        document.save(pdf_path)
    finally:
        document.close()

    output_directory = temporary_directory / "output"
    output_directory.mkdir()
    return qr_path, pdf_path, output_directory


def main() -> None:
    executable = _engine_path()
    process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="file-toolbox-tauri-smoke-") as temporary_directory:
            temporary_path = Path(temporary_directory)
            qr_path, scan_pdf_path, output_directory = _create_scan_fixture(temporary_path)
            pdf_path = temporary_path / "one-page.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            environment = os.environ.copy()
            token = secrets.token_urlsafe(32)
            environment["FILE_TOOLBOX_ENGINE_TOKEN"] = token
            environment["FILE_TOOLBOX_ENGINE_DEBUG_ERRORS"] = "0"
            process = subprocess.Popen(
                [str(executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                shell=False,
                env=environment,
            )
            threading.Thread(target=_drain, args=(process.stderr,), daemon=True).start()
            protocol = EngineProtocol(process, token)
            protocol.wait_for_ready()

            ping = protocol.request("ping")
            if ping.get("pong") is not True:
                raise RuntimeError(f"ping did not return pong: {ping!r}")

            validation = protocol.request("pdf_split.validate", {"pdf_path": str(pdf_path)})
            if validation.get("valid") is not True or validation.get("page_count") != 1:
                raise RuntimeError(f"pdf validation failed: {validation!r}")

            reference_preview = protocol.request(
                "scan_split.preview_reference", {"reference_image_path": str(qr_path)}
            )
            if reference_preview.get("ok") is not True or not reference_preview.get("data_url"):
                raise RuntimeError(f"scan reference preview failed: {reference_preview!r}")

            scan_options = {"detection_mode": "qrcode", "qrcode_text_contains": "tauri-scan-smoke"}
            probe = protocol.request("scan_split.probe_page", {
                "pdf_path": str(scan_pdf_path),
                "reference_image_path": str(qr_path),
                "page_index": 0,
                "options": scan_options,
            })
            probe_complete = protocol.wait_for_task(probe["task_id"])
            if probe_complete.get("ok") is not True or not probe_complete.get("result", {}).get("marked"):
                raise RuntimeError(f"scan probe failed: {probe_complete!r}")

            quick_scan = protocol.request("scan_split.scan_only", {
                "pdf_path": str(scan_pdf_path),
                "reference_image_path": str(qr_path),
                "page_limit": 4,
                "options": scan_options,
            })
            quick_scan_complete = protocol.wait_for_task(quick_scan["task_id"])
            marker_pages = quick_scan_complete.get("result", {}).get("marker_pages")
            if quick_scan_complete.get("ok") is not True or not marker_pages:
                raise RuntimeError(f"quick scan failed: {quick_scan_complete!r}")

            execution = protocol.request("scan_split.execute_async", {
                "pdf_path": str(scan_pdf_path),
                "reference_image_path": str(qr_path),
                "output_dir": str(output_directory),
                "options": scan_options,
            })
            execution_complete = protocol.wait_for_task(execution["task_id"])
            output_files = execution_complete.get("result", {}).get("output_files", [])
            if execution_complete.get("ok") is not True or not output_files or not all(
                Path(output_file).is_file() for output_file in output_files
            ):
                raise RuntimeError(f"scan execution failed: {execution_complete!r}")

            shutdown = protocol.request("shutdown")
            if not shutdown:
                raise RuntimeError("shutdown returned an empty response")
            if process.stdin is not None:
                process.stdin.close()
            exit_code = process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            if exit_code != 0:
                raise RuntimeError(f"engine exited with code {exit_code}")

            print(json.dumps({
                "ready": True,
                "pong": True,
                "pdf_valid": True,
                "page_count": 1,
                "scan_preview": True,
                "scan_probe": True,
                "scan_quick": True,
                "scan_execution": True,
            }, separators=(",", ":")))
    finally:
        cleanup_process(process)


if __name__ == "__main__":
    main()
