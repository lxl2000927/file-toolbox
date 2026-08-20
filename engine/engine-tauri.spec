from pathlib import Path
import sys

sys.path.insert(0, str(Path(SPECPATH).resolve()))
from tauri_package_profile import EXCLUDES, HIDDEN_IMPORTS, NATIVE_BINARIES, PYMUPDF_RUNTIME_HOOK


engine_dir = Path(SPECPATH).resolve()
project_root = str(engine_dir.parent)

a = Analysis(
    [str(engine_dir / "server.py")],
    pathex=[project_root, str(engine_dir)],
    binaries=NATIVE_BINARIES,
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PYMUPDF_RUNTIME_HOOK)],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
