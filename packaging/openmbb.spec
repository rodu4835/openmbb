# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a single-file OpenMBB executable.

Runs the same on Windows and Linux (PyInstaller is not a cross-compiler, so
each OS builds its own binary):  pyinstaller packaging/openmbb.spec

Bundles the Python interpreter, Tk, pyserial, and the sv-ttk theme (whose .tcl
files must be collected explicitly) into one self-contained executable.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

# sv-ttk ships Tcl theme files that a bare import scan misses.
datas, binaries, hiddenimports = collect_all("sv_ttk")

# Imported lazily / inside functions, so name them explicitly.
hiddenimports += [
    "serial", "serial.tools", "serial.tools.list_ports",
    "tkinter", "tkinter.ttk", "tkinter.font",
    "tkinter.filedialog", "tkinter.messagebox",
]

here = SPECPATH                                     # dir of this spec file
src = os.path.abspath(os.path.join(here, "..", "src"))

# Window-icon PNGs (loaded at runtime via importlib.resources).
datas += [(os.path.join(src, "openmbb", "assets"), "openmbb/assets")]

a = Analysis(
    [os.path.join(here, "pyinstaller_entry.py")],
    pathex=[src],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="openmbb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # GUI app: no console window on Windows
    disable_windowed_traceback=False,   # still show a dialog if it crashes
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # .exe / taskbar / Explorer icon; PyInstaller only embeds icons on Windows.
    icon=(os.path.join(here, "icon", "openmbb.ico")
          if sys.platform == "win32" else None),
)
