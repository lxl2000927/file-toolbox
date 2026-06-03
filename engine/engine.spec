# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules
from pathlib import Path


project_root = str(Path(SPECPATH).resolve().parent)
hiddenimports = [
    'src.core.rename_engine',
    'src.core.pdf_split_engine',
    'src.core.pdf_scan_split_engine',
    'src.utils.history_manager',
    'src.utils.path_utils',
    'src.utils.pdf_output',
    'PyPDF2',
    'fitz',
    'numpy',
    'cv2',
    'zxingcpp',
]
hiddenimports += collect_submodules('cv2')
hiddenimports += collect_submodules('fitz')
hiddenimports += collect_submodules('zxingcpp')

binaries = []
binaries += collect_dynamic_libs('cv2')
binaries += collect_dynamic_libs('fitz')
binaries += collect_dynamic_libs('zxingcpp')

a = Analysis(
    ['server.py'],
    pathex=[project_root],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='engine',
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
