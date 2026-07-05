"""Headless tests for the safety + transport layers (no display needed).

Run with:  python -m pytest   (or)   openmbb --selftest
"""

import os
import tempfile

import pytest

from openmbb.safety import (WRITE_WHITELIST, BlockedCommandError,
                            _v_coast_regen, command_blocked)
from openmbb.sim import SimPort
from openmbb.transport import (READ_COMMANDS, SessionLogger, Transport,
                               first_number, parse_settings_dump)


def make_transport():
    tmp = tempfile.mkdtemp(prefix="zctest_")
    return Transport(SimPort(), SessionLogger(base_dir=tmp, tag="test"))


BLOCKED = [
    "format eeprom", "erase eeprom", "eeprom dump 0 16", "settingsrst", "statsrst",
    "eventlogclear", "errorlogadd hi", "reset", "exit_to_bl", "test isolation -v",
    "wdt reset", "timing", "can", "charger", "sevcon preop", "bluetooth reset",
    "set abs_disable On", "set motstage2 200", "set sevmaxregv 4300",
    "set ov_kickstand Yes", "set vin 123", "set debug_level 3",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocked_commands_refused(cmd):
    tr = make_transport()
    with pytest.raises(BlockedCommandError):
        tr.exec_command(cmd)


def test_reads_allowed():
    tr = make_transport()
    for cmd in READ_COMMANDS:
        assert len(tr.exec_command(cmd)) > 20


def test_sevcon_faults_is_a_read():
    assert command_blocked("sevcon faults") is None


def test_settings_parse():
    tr = make_transport()
    settings, order = parse_settings_dump(tr.exec_command("set"))
    assert len(settings) >= 30
    assert settings["spfront"]["value"] == "20"
    live_whitelist = [n for n in order if n in WRITE_WHITELIST]
    assert len(live_whitelist) == len(WRITE_WHITELIST)


def test_dump_streams_and_reports_progress():
    tr = make_transport()
    seen = []
    big = tr.exec_command("dumplogs", idle_timeout=3.0, max_time=60.0,
                          progress_cb=lambda n: seen.append(n))
    assert len(big) > 100_000
    assert len(seen) > 3


def test_write_requires_login_then_verifies_and_reverts():
    tr = make_transport()
    assert "denied" in tr.exec_command("set spfront 22").lower()
    assert "logged in" in tr.exec_command("login tpsreport").lower()
    tr.exec_command("set spfront 22")
    s2, _ = parse_settings_dump(tr.exec_command("set"))
    assert first_number(s2["spfront"]["value"]) == "22"
    tr.exec_command("set spfront 20")
    s3, _ = parse_settings_dump(tr.exec_command("set"))
    assert first_number(s3["spfront"]["value"]) == "20"


def test_validators():
    assert not _v_coast_regen("0")[0]           # fishtail guard
    assert _v_coast_regen("6")[0]
    assert not WRITE_WHITELIST["maxcustsp"][3]("103")[0]
    assert WRITE_WHITELIST["noregenstopped"][4]("No") is not None


def test_session_files_written():
    tr = make_transport()
    tr.exec_command("status")
    tr.exec_command("bms")
    assert os.path.isfile(tr.logger.raw_path)
    assert len(os.listdir(tr.logger.dir)) >= 2
