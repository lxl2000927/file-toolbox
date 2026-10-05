from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

try:
    import pypdf
except ImportError:
    pypdf = None

from src.utils.path_utils import make_unique_output_path, make_unique_temp_path
from src.utils.input_guard import open_input
from src.utils.file_metadata import copy_stream_metadata
from src.utils.atomic_output import publish_exclusive
from src.utils.output_resources import CheckedOutputStream, check_disk


CancelCheck = Callable[[], bool]
OutputCallback = Callable[[str, list[int], float], None]


_OUTPUT_RESERVATION_LOCK = threading.Lock()
_RESERVED_OUTPUT_PATHS: set[str] = set()


@dataclass(frozen=True)
class PdfOutputJob:
    filename: str
    page_indexes: Sequence[int]


def _is_cancelled(cancel_check: Optional[CancelCheck]) -> bool:
    try:
        return bool(cancel_check and cancel_check())
    except Exception:
        return False


def _remove_paths(paths: Sequence[str]) -> None:
    for path in reversed(list(paths)):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _reserve_output_paths(output_dir: str, filename: str, used_paths: set[str]) -> tuple[str, str, set[str]]:
    with _OUTPUT_RESERVATION_LOCK:
        unavailable = set(used_paths)
        unavailable.update(_RESERVED_OUTPUT_PATHS)
        out_path = make_unique_output_path(output_dir, filename, unavailable)
        temp_name = f".{os.path.basename(out_path)}.{uuid.uuid4().hex}"
        tmp_path = make_unique_temp_path(output_dir, temp_name, unavailable)
        reserved = {_normalized_path(out_path), _normalized_path(tmp_path)}
        _RESERVED_OUTPUT_PATHS.update(reserved)
        used_paths.update(reserved)
        return out_path, tmp_path, reserved


def _release_output_paths(reserved: set[str]) -> None:
    with _OUTPUT_RESERVATION_LOCK:
        _RESERVED_OUTPUT_PATHS.difference_update(reserved)


def _publish_reserved_output(tmp_path, out_path, output_dir, filename, used_paths, reserved):
    # Reservations only coordinate legacy writers in this process. A workbench
    # job or another process may publish meanwhile: never replace its file.
    for _ in range(10000):
        try:
            publish_exclusive(tmp_path, out_path)
            return out_path
        except FileExistsError:
            with _OUTPUT_RESERVATION_LOCK:
                unavailable = set(used_paths) | _RESERVED_OUTPUT_PATHS
                out_path = make_unique_output_path(output_dir, filename, unavailable)
                normalized = _normalized_path(out_path)
                reserved.add(normalized)
                _RESERVED_OUTPUT_PATHS.add(normalized)
                used_paths.add(normalized)
    raise RuntimeError('无法生成唯一的输出文件名（尝试次数超过上限）')


def write_pdf_output_jobs(
    pdf_path: str,
    *,
    output_dir: str,
    jobs: Sequence[PdfOutputJob],
    used_paths: Optional[set[str]] = None,
    cancel_check: Optional[CancelCheck] = None,
    on_output: Optional[OutputCallback] = None,
    cleanup_outputs_on_cancel: bool = True,
) -> list[str]:
    if pypdf is None:
        raise RuntimeError("缺少依赖：pypdf")
    if not jobs:
        return []

    os.makedirs(output_dir, exist_ok=True)
    check_disk(output_dir)
    if used_paths is None:
        used_paths = set()
    outputs: list[str] = []

    with open_input(pdf_path) as src_f:
        reader = pypdf.PdfReader(src_f)
        total_pages = len(reader.pages)
        if total_pages <= 0:
            raise RuntimeError("PDF文件没有页面")

        # [Bug#10 Fix] 页码越界改为显式报错（原 max(0,min(...)) 静默钳制会导致
        # 多个 job 输出重复的最后一页而非报错，掩盖调用方 bug）。仅保留 int() 转换。
        normalized_jobs: list[PdfOutputJob] = []
        for job in jobs:
            norm_idx: list[int] = []
            for p in job.page_indexes:
                pi = int(p)
                if pi < 0 or pi >= total_pages:
                    raise RuntimeError(
                        f"页码越界: {pi}（有效范围 0-{total_pages - 1}），文件: {job.filename}"
                    )
                norm_idx.append(pi)
            normalized_jobs.append(PdfOutputJob(job.filename, norm_idx))

        if len(normalized_jobs) == 1 and list(normalized_jobs[0].page_indexes) == list(range(total_pages)):
            if _is_cancelled(cancel_check):
                if cleanup_outputs_on_cancel:
                    raise RuntimeError("已取消")
                return outputs
            started_at = time.perf_counter()
            out_path, tmp_path, reserved = _reserve_output_paths(output_dir, normalized_jobs[0].filename, used_paths)
            try:
                try:
                    src_f.seek(0)
                    with open(tmp_path, "xb") as out_f:
                        check_disk(output_dir, os.fstat(src_f.fileno()).st_size)
                        checked = CheckedOutputStream(out_f, output_dir)
                        shutil.copyfileobj(src_f, checked)
                        checked.finish()
                        copy_stream_metadata(src_f, out_f)
                    out_path = _publish_reserved_output(tmp_path, out_path, output_dir,
                        normalized_jobs[0].filename, used_paths, reserved)
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    if _is_cancelled(cancel_check):
                        if cleanup_outputs_on_cancel:
                            _remove_paths(outputs)
                            raise
                        return outputs
                    raise
                if not cleanup_outputs_on_cancel:
                    # Resumable callers commit the artifact before check() may
                    # block and announce that the task is safely paused.
                    outputs.append(out_path)
                    if on_output:
                        on_output(out_path, list(normalized_jobs[0].page_indexes), time.perf_counter() - started_at)
                    _is_cancelled(cancel_check)
                    return outputs
                if _is_cancelled(cancel_check):
                    _remove_paths([out_path])
                    _remove_paths(outputs)
                    raise RuntimeError("已取消")
                outputs.append(out_path)
                if on_output:
                    on_output(out_path, list(normalized_jobs[0].page_indexes), time.perf_counter() - started_at)
                return outputs
            finally:
                _release_output_paths(reserved)

        for job in normalized_jobs:
            if _is_cancelled(cancel_check):
                if cleanup_outputs_on_cancel:
                    raise RuntimeError("已取消")
                return outputs
            if not job.page_indexes:
                continue
            started_at = time.perf_counter()
            out_path, tmp_path, reserved = _reserve_output_paths(output_dir, job.filename, used_paths)
            try:
                try:
                    with open(tmp_path, "xb") as out_f:
                        writer = pypdf.PdfWriter()
                        for page_index in job.page_indexes:
                            if _is_cancelled(cancel_check):
                                raise RuntimeError("已取消")
                            writer.add_page(reader.pages[page_index])
                        checked = CheckedOutputStream(out_f, output_dir)
                        writer.write(checked)
                        checked.finish()
                    out_path = _publish_reserved_output(tmp_path, out_path, output_dir,
                        job.filename, used_paths, reserved)
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    if _is_cancelled(cancel_check):
                        if cleanup_outputs_on_cancel:
                            _remove_paths(outputs)
                            raise
                        return outputs
                    raise
                if not cleanup_outputs_on_cancel:
                    outputs.append(out_path)
                    if on_output:
                        on_output(out_path, list(job.page_indexes), time.perf_counter() - started_at)
                    if _is_cancelled(cancel_check):
                        return outputs
                    continue
                if _is_cancelled(cancel_check):
                    _remove_paths([out_path])
                    _remove_paths(outputs)
                    raise RuntimeError("已取消")
                outputs.append(out_path)
                if on_output:
                    on_output(out_path, list(job.page_indexes), time.perf_counter() - started_at)
            finally:
                _release_output_paths(reserved)

    return outputs
