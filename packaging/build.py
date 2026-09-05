"""Build the OpenMBB executable for the current OS.

    python packaging/build.py                 # --mode onefile (the default)
    python packaging/build.py --mode onedir

Two shapes, one spec (packaging/openmbb.spec reads OPENMBB_BUILD_MODE):

  onefile  the portable download - dist/openmbb.exe on Windows, dist/openmbb on
           Linux. One self-extracting file, as every release so far.
  onedir   the tree the Windows installer ships - dist/onedir/openmbb/ holding
           openmbb.exe beside an _internal/ directory. Nothing unpacks at
           launch. The onefile bootloader extracting itself into %TEMP% is the
           single biggest reason an unsigned PyInstaller build trips heuristic
           antivirus (Defender's Wacatac!ml class of detection), and an
           installer can lay down a directory just as easily as a file.

On Windows both shapes carry a version resource (Properties > Details: company,
product, description, FileVersion/ProductVersion). It is generated HERE from
openmbb.__version__ every build, so the exe can never disagree with the package.

UPX is pinned off in the spec, on both EXE and COLLECT. PyInstaller ignores
--noupx when a spec file is given (it only affects spec generation), so the spec
is the only place that pin can live.

Requires pyinstaller (pip install .[dev]). PyInstaller cannot cross-compile -
run this on each OS you want a binary for, or let CI do it
(.github/workflows/build.yml).
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
SPEC = os.path.join(HERE, "openmbb.spec")
LICENSE = os.path.join(ROOT, "LICENSE")

MODES = ("onefile", "onedir")
EXE_NAME = "openmbb.exe" if sys.platform == "win32" else "openmbb"

# What Windows shows in Properties > Details. Company/product match the
# installer's AppPublisher/AppName; the copyright line is read from LICENSE so
# it cannot drift from the licence actually shipped.
COMPANY = "OpenMBB"
PRODUCT = "OpenMBB"
DESCRIPTION = "OpenMBB - serial console and diagnostics for Gen2 Zero motorcycles"


def paths(mode):
    """Where a mode's build lands, relative to the cwd PyInstaller runs from
    (the repo root - CI and build_and_install.ps1 both cd there).

    onedir gets its own distpath and workpath so the two shapes never overwrite
    each other: on Linux the onefile binary is literally `dist/openmbb`, the
    very name the onedir tree would want."""
    if mode == "onefile":
        return {"distpath": "dist", "workpath": "build",
                "product": os.path.join("dist", EXE_NAME)}
    return {"distpath": os.path.join("dist", "onedir"),
            "workpath": os.path.join("build", "onedir"),
            "product": os.path.join("dist", "onedir", "openmbb", EXE_NAME)}


def package_version():
    """openmbb.__version__ imported from src/, the same value the CI tag check
    compares the tag against - so the version resource, the tag and the
    package are one number or the build fails."""
    sys.path.insert(0, SRC)
    try:
        from openmbb import __version__
    finally:
        sys.path.remove(SRC)
    return __version__


def version_tuple(version):
    """'0.28.0' -> (0, 28, 0, 0). A Windows FILEVERSION is four 16-bit
    integers, so anything else in the version string is refused rather than
    guessed at."""
    parts = str(version).split(".")
    if not 1 <= len(parts) <= 4 or not all(re.fullmatch(r"[0-9]+", p) for p in parts):
        raise ValueError(
            "cannot express %r as a Windows FILEVERSION (up to four dotted integers)"
            % (version,))
    nums = [int(p) for p in parts]
    if any(n > 0xFFFF for n in nums):
        raise ValueError("%r has a component over 65535, which FILEVERSION cannot hold"
                         % (version,))
    return tuple(nums + [0] * (4 - len(nums)))


def copyright_line():
    with open(LICENSE, encoding="utf-8") as fh:
        m = re.search(r"^Copyright \(c\) (.+)$", fh.read(), re.M)
    if not m:
        raise ValueError("LICENSE has no 'Copyright (c) ...' line to put in the version resource")
    return "Copyright (c) %s. MIT License." % m.group(1).strip()


def write_version_file(version, path):
    """Write the version resource PyInstaller embeds via EXE(version=...).

    Built with PyInstaller's own classes and serialised with str(), then read
    back through the loader PyInstaller itself uses - so a format surprise
    fails here, in the build, and not as an exe with blank Details."""
    from PyInstaller.utils.win32 import versioninfo as vi

    t = version_tuple(version)
    info = vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=t, prodvers=t),
        kids=[
            vi.StringFileInfo([vi.StringTable("040904B0", [   # US English, Unicode
                vi.StringStruct("CompanyName", COMPANY),
                vi.StringStruct("FileDescription", DESCRIPTION),
                vi.StringStruct("FileVersion", version),
                vi.StringStruct("InternalName", "openmbb"),
                vi.StringStruct("LegalCopyright", copyright_line()),
                vi.StringStruct("OriginalFilename", "openmbb.exe"),
                vi.StringStruct("ProductName", PRODUCT),
                vi.StringStruct("ProductVersion", version),
            ])]),
            vi.VarFileInfo([vi.VarStruct("Translation", [1033, 1200])]),
        ])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(info))
    vi.load_version_info_from_text_file(path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the OpenMBB executable.")
    ap.add_argument("--mode", choices=MODES, default="onefile",
                    help="onefile: the portable download (default). "
                         "onedir: the tree the Windows installer ships.")
    args = ap.parse_args(argv)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller not installed. Run:  pip install .[dev]")

    version = package_version()
    p = paths(args.mode)
    env = dict(os.environ, OPENMBB_BUILD_MODE=args.mode)

    if sys.platform == "win32":
        # Outside every workpath, because --clean empties those.
        vf = os.path.abspath(os.path.join("build", "openmbb-version-info.txt"))
        write_version_file(version, vf)
        env["OPENMBB_VERSION_FILE"] = vf
        print("version resource: v%s -> %s" % (version, vf))

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", p["distpath"], "--workpath", p["workpath"], SPEC]
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, env=env)

    out = os.path.abspath(p["product"])
    if not os.path.exists(out):
        sys.exit("PyInstaller finished but %s is missing" % out)
    print("\nBuilt (%s, v%s): %s" % (args.mode, version, out))


if __name__ == "__main__":
    main()
