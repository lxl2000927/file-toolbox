from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


PYMUPDF_RUNTIME_HOOK = Path(__file__).with_name("pyi_rth_pymupdf_stdio.py")


HIDDEN_IMPORTS = [
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
]

for module in ("cv2", "fitz", "zxingcpp"):
    HIDDEN_IMPORTS.extend(collect_submodules(module))

NATIVE_BINARIES = []
for module in ("cv2", "fitz", "zxingcpp"):
    NATIVE_BINARIES.extend(collect_dynamic_libs(module))

EXCLUDES = []
