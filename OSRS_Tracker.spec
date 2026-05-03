# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OSRS Price Tracker.

Build with:
    pyinstaller OSRS_Tracker.spec

Produces:
    dist/OSRS Tracker.app   (macOS)
    dist/OSRS Tracker/      (other platforms)
"""

import os
import sys

block_cipher = None

# If an icon.icns exists in the project root, bundle it.
ICON_PATH = 'icon.icns' if os.path.exists('icon.icns') else None

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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleVersion': '1.0.0',
            'NSHighResolutionCapable': 'True',
            'LSMinimumSystemVersion': '10.13.0',
        },
    )
