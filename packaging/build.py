"""Build the single-file OpenMBB executable for the current OS.

    python packaging/build.py

Produces dist/openmbb (Linux/macOS) or dist/openmbb.exe (Windows). Requires
pyinstaller (pip install .[dev]). PyInstaller cannot cross-compile — run this
on each OS you want a binary for, or let CI do it (.github/workflows/build.yml).
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "openmbb.spec")


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller not installed. Run:  pip install .[dev]")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC]
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)
    exe = "openmbb.exe" if sys.platform == "win32" else "openmbb"
    out = os.path.abspath(os.path.join(os.getcwd(), "dist", exe))
    print("\nBuilt:", out if os.path.exists(out) else "(check dist/)")


if __name__ == "__main__":
    main()
