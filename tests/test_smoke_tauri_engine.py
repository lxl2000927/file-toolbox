import io
import subprocess

import pytest

from engine.smoke_tauri_engine import cleanup_process


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
