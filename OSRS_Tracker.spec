# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OSRS Price Tracker — cross-platform.

Build with:
    pyinstaller OSRS_Tracker.spec

Produces:
    dist/OSRS Tracker.app   (macOS)
    dist/OSRS Tracker.exe   (Windows — single self-contained executable)
    dist/OSRS Tracker/      (Linux — folder distribution)
"""

import os
import sys

sys.path.insert(0, SPECPATH)  # SPECPATH is injected by PyInstaller
from version import APP_VERSION

block_cipher = None

# Pick an OS-appropriate icon if one is present alongside the spec.
#   macOS  -> icon.icns
#   Windows -> icon.ico
#   Linux  -> no icon embedded (most desktop environments don't use one)
def _icon(name):
    path = os.path.join(SPECPATH, name)  # SPECPATH is injected by PyInstaller
    return path if os.path.exists(path) else None

if sys.platform == 'darwin':
    ICON_PATH = _icon('icon.icns')
elif sys.platform == 'win32':
    ICON_PATH = _icon('icon.ico')
else:
    ICON_PATH = None

# Hidden imports — modules PyInstaller's static analysis sometimes misses.
hidden = [
    "lxml.etree",
    "lxml._elementpath",
]

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim heavy unused libraries to keep the bundle small.
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib", "scipy", "pandas", "numpy",
        "IPython", "jupyter", "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_kwargs = dict(
    name='OSRS Tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,    # windowed app, no terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == 'win32':
    # Windows: produce a single self-contained .exe (onefile mode).
    # One file to double-click instead of a folder of DLLs. Trade-off: it
    # unpacks to %TEMP% on each launch (slower start) and unsigned onefile
    # builds are flagged by antivirus/SmartScreen more often.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        upx_exclude=[],
        runtime_tmpdir=None,
        icon=ICON_PATH,
        **exe_kwargs,
    )
else:
    # macOS and Linux: build a launcher EXE backed by a COLLECT folder,
    # then wrap that into an .app bundle on macOS.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_kwargs,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='OSRS Tracker',
    )

    # macOS .app bundle
    if sys.platform == 'darwin':
        app = BUNDLE(
            coll,
            name='OSRS Tracker.app',
            icon=ICON_PATH,
            bundle_identifier='com.nardoneyland.osrstracker',
            info_plist={
                'CFBundleShortVersionString': APP_VERSION,
                'CFBundleVersion': APP_VERSION,
                'NSHighResolutionCapable': 'True',
                'LSMinimumSystemVersion': '10.13.0',
            },
        )
