"""The build's antivirus-facing promises, pinned where they can be checked
without building anything.

A user's Defender flagged a release as Trojan:Win32/Wacatac.C!ml, a heuristic
(the !ml) rather than a signature. The response is three build-time properties:
the installer ships a --onedir tree so nothing self-extracts at launch, UPX is
pinned off, and both Windows exes carry a version resource generated from
openmbb.__version__. Each of those is one careless spec edit from silently
un-happening, and CI would still be green - the frozen binaries are only
exercised on a tag build. These tests hold the contract between build.py, the
spec, the Inno script, the one-click PowerShell and the workflow, which is what
keeps the three files that ship agreeing about which shape ships where.
"""

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGING = REPO / "packaging"


def _build_module():
    """packaging/build.py, imported as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("openmbb_build", PACKAGING / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_version_tuple_pads_to_four_and_refuses_what_filevers_cannot_hold():
    """A Windows FILEVERSION is four 16-bit integers. Anything the package
    version string carries beyond that is refused, not guessed at."""
    b = _build_module()
    assert b.version_tuple("0.28.0") == (0, 28, 0, 0)
    assert b.version_tuple("1.2.3.4") == (1, 2, 3, 4)
    assert b.version_tuple("7") == (7, 0, 0, 0)
    for bad in ("0.28.0rc1", "0.28.0.0.1", "", "v0.28", "0.65536", "0..1"):
        with pytest.raises(ValueError):
            b.version_tuple(bad)


def test_the_version_resource_is_built_from_the_package_version():
    """The number embedded in the exe is the __version__ literal in
    src/openmbb/__init__.py - the value the CI tag check compares the tag
    against - so the exe's Details, the tag and the package are one number or
    the build fails.

    The oracle is the file, read with the same regex build_and_install.ps1
    uses, NOT `import openmbb`: the suite has that module cached, so comparing
    against it would be comparing a value with itself and could never fail."""
    init_py = (REPO / "src" / "openmbb" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([\d.]+)"', init_py, re.M)
    assert m, "src/openmbb/__init__.py has no __version__ literal"
    b = _build_module()
    assert b.package_version() == m.group(1)
    b.version_tuple(m.group(1))   # expressible, or build.py would refuse


@pytest.mark.skipif(sys.platform != "win32",
                    reason="PyInstaller's version-resource module imports pefile and win32api")
def test_the_generated_version_file_round_trips_through_pyinstaller(tmp_path):
    """Written with PyInstaller's own classes and read back with the loader the
    EXE step uses, so a serialisation surprise fails here rather than as an exe
    with blank Details."""
    from PyInstaller.utils.win32 import versioninfo as vi

    b = _build_module()
    path = b.write_version_file("0.28.0", str(tmp_path / "vi.txt"))
    info = vi.load_version_info_from_text_file(path)
    strings = {s.name: s.val
               for table in info.kids[0].kids       # StringFileInfo -> StringTable
               for s in table.kids}                 # StringTable -> StringStruct
    assert strings["FileVersion"] == "0.28.0"
    assert strings["ProductVersion"] == "0.28.0"
    assert strings["CompanyName"] == "OpenMBB"
    assert strings["ProductName"] == "OpenMBB"
    assert strings["OriginalFilename"] == "openmbb.exe"
    assert strings["FileDescription"]
    assert "MIT" in strings["LegalCopyright"]
    # 0.28.0.0 packed the way the PE header holds it: MS = major<<16 | minor
    assert info.ffi.fileVersionMS == (0 << 16) | 28
    assert info.ffi.fileVersionLS == 0


def test_spec_pins_upx_off_on_both_shapes():
    """PyInstaller turns UPX on whenever an `upx` binary is on PATH unless the
    spec says otherwise, and it ignores --noupx once a spec file is given. So
    the spec is the pin, and it has to be on the EXE and on the onedir COLLECT
    both - or a runner with upx installed compresses one shape and not the
    other."""
    spec = (PACKAGING / "openmbb.spec").read_text(encoding="utf-8")
    code = [line.split("#", 1)[0] for line in spec.splitlines()]   # not the comments
    assert not any("upx=True" in line for line in code)
    pins = sum("upx=False" in line for line in code)
    assert pins == 2, "one pin per shape (EXE options and COLLECT), found %d" % pins
    assert "COLLECT(" in spec and "exclude_binaries=True" in spec


def test_the_two_shapes_cannot_overwrite_each_other():
    """On Linux the onefile binary is literally dist/openmbb - the very name
    the onedir tree would want. Separate dist and work directories per shape
    make building both in one checkout safe in either order."""
    b = _build_module()
    one, tree = b.paths("onefile"), b.paths("onedir")
    assert one["distpath"] != tree["distpath"]
    assert one["workpath"] != tree["workpath"]
    assert tree["product"].startswith(tree["distpath"] + os.sep)
    assert not tree["product"].startswith(one["product"])


def test_the_installer_ships_the_onedir_tree_end_to_end():
    """One contract across three files: the Inno script takes a directory, the
    one-click PowerShell builds that directory, and the workflow builds, verifies
    and hands over that same directory. The old single-exe define must be gone
    everywhere, because a stale /DExeSrc would compile a working installer that
    ships the self-extracting shape this change exists to retire."""
    iss = (PACKAGING / "openmbb.iss").read_text(encoding="utf-8")
    ps1 = (PACKAGING / "build_and_install.ps1").read_text(encoding="utf-8")
    yml = (REPO / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    # Both scripts explain their own guards in comments that name the very
    # things the guards forbid, so the negative checks look at code only.
    iss_code = [l for l in iss.splitlines() if not l.lstrip().startswith(";")]
    ps1_code = [l for l in ps1.splitlines() if not l.lstrip().startswith("#")]

    assert 'Source: "{#DistDir}\\*"' in iss
    assert "recursesubdirs" in iss and "createallsubdirs" in iss
    assert '\\_internal")' in iss           # the compile-time check that it IS a tree
    # No pre-copy wipe of _internal: Inno keeps no backup of what [InstallDelete]
    # removes, so a user who declines to close a running OpenMBB would be left
    # with a half-deleted tree. The .iss says why at the [Files] section.
    assert not any(l.strip() == "[InstallDelete]" for l in iss_code)

    assert "--mode onedir" in ps1 and "/DDistDir=" in ps1
    assert "'_internal'" in ps1              # the installed layout is asserted, not assumed
    # A GUI-subsystem exe does not set $LASTEXITCODE under the call operator, so
    # the verify step must Start-Process -Wait the installed exe, never `& $InstallExe`.
    assert not any("& $InstallExe" in l for l in ps1_code)
    assert sum("Start-Process -FilePath $InstallExe" in l for l in ps1_code) == 2

    assert "--mode onefile" in yml and "--mode onedir" in yml
    assert "/DDistDir=" in yml
    assert "SHA256SUMS.txt" in yml
    assert ".VersionInfo" in yml             # CI checks the resource, not just the exit code

    for text, name in ((iss, "iss"), (ps1, "ps1"), (yml, "yml")):
        assert "ExeSrc" not in text, "%s still carries the retired single-exe define" % name
