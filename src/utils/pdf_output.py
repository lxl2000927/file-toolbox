from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

try:
    import pypdf
except ImportError:
    pypdf = None

from src.utils.path_utils import make_unique_output_path, make_unique_temp_path


CancelCheck = Callable[[], bool]
OutputCallback = Callable[[str, list[int], float], None]


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
    used_paths = used_paths or set()
    outputs: list[str] = []

    with open(pdf_path, "rb") as src_f:
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
            out_path = make_unique_output_path(output_dir, normalized_jobs[0].filename, used_paths)
            tmp_name = os.path.basename(out_path)
            tmp_path = make_unique_temp_path(output_dir, tmp_name, used_paths)
            try:
                shutil.copy2(pdf_path, tmp_path)
                os.replace(tmp_path, out_path)
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
            if _is_cancelled(cancel_check):
                if cleanup_outputs_on_cancel:
                    try:
                        if os.path.exists(out_path):
                            os.remove(out_path)
                    except Exception:
                        pass
                    _remove_paths(outputs)
                    raise RuntimeError("已取消")
                outputs.append(out_path)
                if on_output:
                    on_output(out_path, list(normalized_jobs[0].page_indexes), time.perf_counter() - started_at)
                return outputs
            outputs.append(out_path)
            if on_output:
                on_output(out_path, list(normalized_jobs[0].page_indexes), time.perf_counter() - started_at)
            return outputs

        for job in normalized_jobs:
            if _is_cancelled(cancel_check):
                if cleanup_outputs_on_cancel:
                    raise RuntimeError("已取消")
                return outputs
            if not job.page_indexes:
                continue
            started_at = time.perf_counter()
            out_path = make_unique_output_path(output_dir, job.filename, used_paths)
            tmp_name = os.path.basename(out_path)
            tmp_path = make_unique_temp_path(output_dir, tmp_name, used_paths)
            try:
                with open(tmp_path, "wb") as out_f:
                    writer = pypdf.PdfWriter()
                    for page_index in job.page_indexes:
                        if _is_cancelled(cancel_check):
                            raise RuntimeError("已取消")
                        writer.add_page(reader.pages[page_index])
                    writer.write(out_f)
                os.replace(tmp_path, out_path)
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
            if _is_cancelled(cancel_check):
                if cleanup_outputs_on_cancel:
                    try:
                        if os.path.exists(out_path):
                            os.remove(out_path)
                    except Exception:
                        pass
                    _remove_paths(outputs)
                    raise RuntimeError("已取消")
                outputs.append(out_path)
                if on_output:
                    on_output(out_path, list(job.page_indexes), time.perf_counter() - started_at)
                return outputs
            outputs.append(out_path)
            if on_output:
                on_output(out_path, list(job.page_indexes), time.perf_counter() - started_at)

    return outputs
