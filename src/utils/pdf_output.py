from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

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


def write_pdf_output_jobs(
    pdf_path: str,
    *,
    output_dir: str,
    jobs: Sequence[PdfOutputJob],
    used_paths: Optional[set[str]] = None,
    cancel_check: Optional[CancelCheck] = None,
    on_output: Optional[OutputCallback] = None,
) -> list[str]:
    if PyPDF2 is None:
        raise RuntimeError("缺少依赖：PyPDF2")
    if not jobs:
        return []

    os.makedirs(output_dir, exist_ok=True)
    used_paths = used_paths or set()
    outputs: list[str] = []

    with open(pdf_path, "rb") as src_f:
        reader = PyPDF2.PdfReader(src_f)
        total_pages = len(reader.pages)
        if total_pages <= 0:
            raise RuntimeError("PDF文件没有页面")

        normalized_jobs = [
            PdfOutputJob(job.filename, [max(0, min(int(p), total_pages - 1)) for p in job.page_indexes])
            for job in jobs
        ]

        if len(normalized_jobs) == 1 and list(normalized_jobs[0].page_indexes) == list(range(total_pages)):
            if _is_cancelled(cancel_check):
                raise RuntimeError("已取消")
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
                raise
            outputs.append(out_path)
            if on_output:
                on_output(out_path, list(normalized_jobs[0].page_indexes), time.perf_counter() - started_at)
            return outputs

        for job in normalized_jobs:
            if _is_cancelled(cancel_check):
                raise RuntimeError("已取消")
            if not job.page_indexes:
                continue
            started_at = time.perf_counter()
            out_path = make_unique_output_path(output_dir, job.filename, used_paths)
            tmp_name = os.path.basename(out_path)
            tmp_path = make_unique_temp_path(output_dir, tmp_name, used_paths)
            try:
                with open(tmp_path, "wb") as out_f:
                    writer = PyPDF2.PdfWriter()
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
                raise
            outputs.append(out_path)
            if on_output:
                on_output(out_path, list(job.page_indexes), time.perf_counter() - started_at)

    return outputs
