import os
import runpy
from pathlib import Path

from engine import tauri_package_profile


def test_tauri_package_profile_includes_scanner_dependencies_and_native_binaries():
    assert {
        "src.core.rename_engine",
        "src.core.pdf_split_engine",
        "src.core.pdf_scan_split_engine",
        "src.utils.history_manager",
        "src.utils.path_utils",
        "src.utils.pdf_output",
        "pypdf",
        "fitz",
        "numpy",
        "cv2",
        "zxingcpp",
    } <= set(tauri_package_profile.HIDDEN_IMPORTS)
    assert not {
        "src.core.pdf_scan_split_engine",
        "cv2",
        "numpy",
        "fitz",
        "zxingcpp",
    } & set(tauri_package_profile.EXCLUDES)
    assert isinstance(getattr(tauri_package_profile, "NATIVE_BINARIES", None), list)
    assert set(tauri_package_profile.HIDDEN_IMPORTS).isdisjoint(tauri_package_profile.EXCLUDES)


def test_tauri_package_profile_runs_pymupdf_stdio_hook_before_server_imports(monkeypatch):
    hook = getattr(tauri_package_profile, "PYMUPDF_RUNTIME_HOOK", None)

    assert hook == Path(tauri_package_profile.__file__).with_name("pyi_rth_pymupdf_stdio.py")
    assert hook.is_file()
    monkeypatch.delenv("PYMUPDF_MESSAGE", raising=False)
    monkeypatch.delenv("PYMUPDF_LOG", raising=False)
    runpy.run_path(str(hook))
    assert os.environ["PYMUPDF_MESSAGE"] == "logging:name=file_toolbox.pymupdf,level=30"
    assert os.environ["PYMUPDF_LOG"] == "logging:name=file_toolbox.pymupdf,level=20"

    spec = Path(tauri_package_profile.__file__).with_name("engine-tauri.spec").read_text(encoding="utf-8")
    assert "runtime_hooks=[str(PYMUPDF_RUNTIME_HOOK)]" in spec
