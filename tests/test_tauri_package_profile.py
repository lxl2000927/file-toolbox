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
