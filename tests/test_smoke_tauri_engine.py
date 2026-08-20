import io
import queue
import subprocess

import fitz
import pytest

from engine.smoke_tauri_engine import EngineProtocol, _create_scan_fixture, cleanup_process


class FakeProcess:
    def __init__(self, *, exited: bool = False, terminate_times_out: bool = False) -> None:
        self.exited = exited
        self.terminate_times_out = terminate_times_out
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.events: list[str] = []
        self.wait_count = 0

    def poll(self) -> int | None:
        self.events.append("poll")
        return 0 if self.exited else None

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")

    def wait(self, timeout: int) -> int:
        self.events.append(f"wait:{timeout}")
        self.wait_count += 1
        if self.terminate_times_out and self.wait_count == 1:
            raise subprocess.TimeoutExpired("engine.exe", timeout)
        self.exited = True
        return 0


def test_cleanup_process_kills_after_terminate_timeout_without_masking_active_exception():
    process = FakeProcess(terminate_times_out=True)

    with pytest.raises(RuntimeError, match="original smoke failure"):
        try:
            raise RuntimeError("original smoke failure")
        finally:
            cleanup_process(process)

    assert process.events == ["poll", "terminate", "wait:10", "kill", "wait:10"]
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


def test_cleanup_process_only_closes_streams_for_already_exited_process():
    process = FakeProcess(exited=True)

    cleanup_process(process)

    assert process.events == ["poll"]
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


class FakeProtocolProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()

    def poll(self) -> None:
        return None


def test_wait_for_task_uses_out_of_order_completion_preserved_by_request_backlog():
    protocol = EngineProtocol.__new__(EngineProtocol)
    protocol.process = FakeProtocolProcess()
    protocol.token = "test-token"
    protocol.next_request_id = 1
    protocol.messages = queue.Queue()
    protocol.backlog = []
    protocol.messages.put({
        "jsonrpc": "2.0",
        "method": "task.complete",
        "params": {"task_id": "probe-1", "ok": True, "result": {"marked": True}},
    })
    protocol.messages.put({"jsonrpc": "2.0", "id": 1, "result": {"task_id": "probe-1"}})

    response = protocol.request("scan_split.probe_page", {"pdf_path": "fixture.pdf"})
    completion = protocol.wait_for_task(response["task_id"])

    assert completion["ok"] is True
    assert completion["result"]["marked"] is True
    assert protocol.backlog == []


def test_create_scan_fixture_generates_qr_and_four_page_pdf(tmp_path):
    qr_path, pdf_path, output_directory = _create_scan_fixture(tmp_path)

    assert qr_path.is_file()
    assert output_directory.is_dir()
    with fitz.open(pdf_path) as document:
        assert len(document) == 4
