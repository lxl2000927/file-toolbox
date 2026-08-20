from engine.tauri_package_profile import EXCLUDES, HIDDEN_IMPORTS


def test_tauri_package_profile_keeps_pdf_and_excludes_scan_dependencies():
    assert {
        "src.core.rename_engine",
        "src.core.pdf_split_engine",
        "src.utils.history_manager",
        "src.utils.path_utils",
        "src.utils.pdf_output",
        "pypdf",
    } <= set(HIDDEN_IMPORTS)
    assert {
        "src.core.pdf_scan_split_engine",
        "cv2",
        "numpy",
        "fitz",
        "zxingcpp",
    } <= set(EXCLUDES)
    assert set(HIDDEN_IMPORTS).isdisjoint(EXCLUDES)
