from __future__ import annotations

import os
import re


_RE_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}


def _safe_output_name(filename: str, default: str, *, require_pdf: bool) -> str:
    name = str(filename or default).strip() or default
    if "\x00" in name:
        raise ValueError("输出文件名包含非法字符")
    if os.path.isabs(name) or os.path.splitdrive(name)[0]:
        raise ValueError("输出文件名不能是绝对路径")
    if "/" in name or "\\" in name:
        raise ValueError("输出文件名不能包含路径分隔符")
    traversal_markers = {".", ".."}
    if name in traversal_markers or any(part in traversal_markers for part in re.split(r"[\\/]+", name)):
        raise ValueError("输出文件名不能包含相对目录")
    name = _RE_INVALID_FILENAME_CHARS.sub("_", name).strip(" .") or default
    if require_pdf and not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    stem, ext = os.path.splitext(name)
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    name = f"{stem}{ext}".strip(" .") or default
    return name


def _ensure_inside_output_dir(output_dir: str, candidate: str) -> None:
    # Bug5 Fix: check raw input BEFORE abspath() - after abspath() basename can never be "." or ".."
    _raw = (output_dir or "").strip()
    if _raw in (".", ".."):
        raise ValueError("输出目录不能是相对目录")
    root = os.path.abspath(_raw or ".")
    target = os.path.abspath(candidate)
    try:
        common = os.path.commonpath([root, target])
    except ValueError:
        common = ""
    if os.path.normcase(common) != os.path.normcase(root):
        raise ValueError("输出路径超出目标目录")


def make_unique_output_path(output_dir: str, filename: str, used_paths: set[str]) -> str:
    name = _safe_output_name(filename, "output.pdf", require_pdf=True)
    if len(name) > 255:
        base, ext = os.path.splitext(name)
        name = base[: 255 - len(ext)] + ext
    base, ext = os.path.splitext(name)
    candidate = os.path.join(output_dir, name)
    _ensure_inside_output_dir(output_dir, candidate)
    norm = os.path.normcase(os.path.abspath(candidate))
    if norm not in used_paths and not os.path.exists(candidate):
        used_paths.add(norm)
        return candidate
    counter = 2
    while counter <= 10000:
        candidate = os.path.join(output_dir, f"{base}_{counter}{ext}")
        _ensure_inside_output_dir(output_dir, candidate)
        norm = os.path.normcase(os.path.abspath(candidate))
        if norm not in used_paths and not os.path.exists(candidate):
            used_paths.add(norm)
            return candidate
        counter += 1
    raise RuntimeError("无法生成唯一的输出文件名（尝试次数超过上限）")


def make_unique_temp_path(output_dir: str, filename: str, used_paths: set[str]) -> str:
    filename = _safe_output_name(filename, "output.pdf", require_pdf=False)
    if len(filename) > 240:
        # Bug8 Fix: preserve file extension when truncating
        _base, _ext = os.path.splitext(filename)
        filename = _base[:240 - len(_ext)] + _ext
    candidate = os.path.join(output_dir, f"{filename}.tmp")
    _ensure_inside_output_dir(output_dir, candidate)
    norm = os.path.normcase(os.path.abspath(candidate))
    if norm not in used_paths and not os.path.exists(candidate):
        used_paths.add(norm)
        return candidate
    counter = 2
    while counter <= 10000:
        candidate = os.path.join(output_dir, f"{filename}.tmp{counter}")
        _ensure_inside_output_dir(output_dir, candidate)
        norm = os.path.normcase(os.path.abspath(candidate))
        if norm not in used_paths and not os.path.exists(candidate):
            used_paths.add(norm)
            return candidate
        counter += 1
    raise RuntimeError("无法生成唯一的临时文件路径（尝试次数超过上限）")
