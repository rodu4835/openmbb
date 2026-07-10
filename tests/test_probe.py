"""Unit tests for connect-probe helpers in gui.py (no display needed — these
are module-level functions and importing openmbb.gui does not load tkinter)."""

from openmbb.gui import (KNOWN_FIRMWARE_REVS, _looks_like_version, _parse_fw_rev)


def test_parse_fw_rev():
    assert _parse_fw_rev("  Firmware Rev     : 41") == 41
    assert _parse_fw_rev("Firmware Rev 41") == 41
    assert _parse_fw_rev("Firmware Rev : 99  (build ...)") == 99
    assert _parse_fw_rev("no rev here") is None
    assert _parse_fw_rev("") is None
    assert _parse_fw_rev(None) is None


def test_looks_like_version():
    assert _looks_like_version("== Main Bike Board ==\n  Firmware Rev : 41")
    assert _looks_like_version("Board PN : 40-08064   Board Rev : 04")
    assert not _looks_like_version("")
    assert not _looks_like_version("random noise no keywords")


def test_known_revs_contains_41():
    assert 41 in KNOWN_FIRMWARE_REVS


def test_ensure_console_noop_when_stdout_present():
    # G4: with a real stdout (pytest), _ensure_console returns cleanly (no attach)
    from openmbb.cli import _ensure_console
    _ensure_console()


def test_list_serial_ports_returns_list():
    # G3: pyserial present -> a list; missing -> still a list (with a warning)
    from openmbb.transport import list_serial_ports
    assert isinstance(list_serial_ports(), list)
