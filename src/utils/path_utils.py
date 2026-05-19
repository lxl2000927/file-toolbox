from __future__ import annotations

import os


def make_unique_output_path(output_dir: str, filename: str, used_paths: set[str]) -> str:
    name = filename or "output.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    base, ext = os.path.splitext(name)
    candidate = os.path.join(output_dir, name)
    norm = os.path.normcase(os.path.abspath(candidate))
    if norm not in used_paths and not os.path.exists(candidate):
        used_paths.add(norm)
        return candidate
    counter = 2
    while True:
        candidate = os.path.join(output_dir, f"{base}_{counter}{ext}")
        norm = os.path.normcase(os.path.abspath(candidate))
        if norm not in used_paths and not os.path.exists(candidate):
            used_paths.add(norm)
            return candidate
        counter += 1


def make_unique_temp_path(output_dir: str, filename: str, used_paths: set[str]) -> str:
    candidate = os.path.join(output_dir, f"{filename}.tmp")
    norm = os.path.normcase(os.path.abspath(candidate))
    if norm not in used_paths and not os.path.exists(candidate):
        used_paths.add(norm)
        return candidate
    counter = 2
    while True:
        candidate = os.path.join(output_dir, f"{filename}.tmp{counter}")
        norm = os.path.normcase(os.path.abspath(candidate))
        if norm not in used_paths and not os.path.exists(candidate):
            used_paths.add(norm)
            return candidate
        counter += 1
