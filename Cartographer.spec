# -*- mode: python ; coding: utf-8 -*-
# Cartographer - one-file PyInstaller build spec.
# Written by LJ "HawaiizFynest" Eblacas

import sys

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('cartographer/data/*.json', 'cartographer/data'),
           ('assets/icon.png', '.'),
           ('CHANGELOG.md', '.')],
    hiddenimports=['serial.tools.list_ports'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'PIL', 'PySide6', 'PyQt5'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Cartographer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if sys.platform == 'win32' else None,
)

# macOS: wrap into an .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Cartographer.app',
        icon='assets/icon.icns',
        bundle_identifier='digital.coloradovista.cartographer',
        info_plist={'NSHighResolutionCapable': 'True'},
    )
