# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds the OpenMBB executable in one of two shapes.

Driven by packaging/build.py, which sets two environment variables:

  OPENMBB_BUILD_MODE    onefile (default) — the portable single file.
                        onedir            — openmbb.exe beside _internal/, the
                                            tree the Windows installer ships;
                                            nothing self-extracts at launch.
  OPENMBB_VERSION_FILE  Windows only: the version resource build.py generated
                        from openmbb.__version__ (Properties > Details).

Runs the same on Windows and Linux (PyInstaller is not a cross-compiler, so
each OS builds its own binary). Running the spec by hand with neither variable
set builds the portable onefile, as it always has.

Bundles the Python interpreter, Tk, pyserial, and the sv-ttk theme (whose .tcl
files must be collected explicitly).

UPX is pinned OFF on both EXE and COLLECT below. PyInstaller enables it
whenever an `upx` binary is on PATH unless the spec says otherwise, and a
--noupx on the command line is ignored once a spec file is given — so these
two keyword arguments are the only pin there is.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

mode = os.environ.get("OPENMBB_BUILD_MODE", "onefile")
if mode not in ("onefile", "onedir"):
    raise SystemExit("OPENMBB_BUILD_MODE must be 'onefile' or 'onedir', not %r" % (mode,))

# PyInstaller embeds version resources on Windows only; elsewhere the variable
# is not consulted at all rather than passed through and quietly ignored.
version_file = os.environ.get("OPENMBB_VERSION_FILE") if sys.platform == "win32" else None
if version_file and not os.path.exists(version_file):
    raise SystemExit("OPENMBB_VERSION_FILE points at nothing: %s" % version_file)

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

# Everything both shapes share. `upx=False` here is one half of the pin.
exe_options = dict(
    name="openmbb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: no console window on Windows
    disable_windowed_traceback=False,   # still show a dialog if it crashes
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # .exe / taskbar / Explorer icon; PyInstaller only embeds icons on Windows.
    icon=(os.path.join(here, "icon", "openmbb.ico")
          if sys.platform == "win32" else None),
    version=version_file,
)

if mode == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **exe_options,
    )
else:
    # The exe holds only the bootloader and the scripts; binaries and data go
    # beside it in _internal/, laid down by COLLECT. Nothing unpacks at launch.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_options,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,          # the other half of the pin
        upx_exclude=[],
        name="openmbb",     # -> <distpath>/openmbb/{openmbb.exe,_internal/}
    )
