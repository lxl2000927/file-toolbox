import os
import sys
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
from ui.main_window import MainWindow
from utils.style_manager import StyleManager


SETTINGS_SCHEMA_VERSION = 1


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _migrate_startup_settings():
    app_settings = QSettings("FileToolbox", "App")
    current_version = _to_int(app_settings.value("settingsSchemaVersion", 0), 0)
    if current_version >= SETTINGS_SCHEMA_VERSION:
        return

    main_settings = QSettings("FileToolbox", "MainWindow")
    try:
        if main_settings.value("geometryVersion", None) is None:
            main_settings.setValue("geometryVersion", 0)
            main_settings.sync()
    except Exception:
        pass

    try:
        app_settings.setValue("settingsSchemaVersion", SETTINGS_SCHEMA_VERSION)
        app_settings.sync()
    except Exception:
        pass


def _startup_profile_enabled() -> bool:
    v = str(os.getenv("FILETOOLBOX_STARTUP_PROFILE", "") or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _startup_profile_path() -> str:
    base_dir = os.getenv("APPDATA") or os.path.expanduser("~")
    out_dir = os.path.join(base_dir, "FileToolbox")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        return ""
    return os.path.join(out_dir, "startup_profile.txt")


def main():
    profile_on = _startup_profile_enabled()
    t0 = time.perf_counter()
    marks: list[tuple[str, float]] = []

    def mark(label: str):
        if not profile_on:
            return
        marks.append((label, time.perf_counter()))

    mark("enter_main")
    app = QApplication(sys.argv)
    mark("QApplication_ready")

    StyleManager.apply_global_style(app)
    mark("style_applied")

    _migrate_startup_settings()
    mark("settings_migrated")

    window = MainWindow()
    mark("MainWindow_ready")
    window.setWindowTitle("PDF Split")
    window.show()
    mark("window_shown")

    if profile_on:
        out_path = _startup_profile_path()
        if out_path:
            try:
                lines = []
                lines.append(f"frozen={bool(getattr(sys, 'frozen', False))}")
                last = t0
                for label, ts in marks:
                    lines.append(f"{label}\t{(ts - t0) * 1000.0:.1f}ms\t+{(ts - last) * 1000.0:.1f}ms")
                    last = ts
                with open(out_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n\n")
            except Exception:
                pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
