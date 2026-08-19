"""The headless report: a saved session folder in, a JSON-ready dict out.

This is the path that does not need the bike, a serial port, or the GUI — so
these tests build sessions on disk and never construct a Tk root.
"""

import json
import re
import subprocess
import sys

import pytest

from openmbb import report, sessions
from openmbb.sim import SimPort
from openmbb.transport import (DUMP_COMMANDS, READ_COMMANDS, SessionLogger,
                               Transport)

RIDE_LOG = """
 00001     05/12/2023 09:14:02   Riding      SOC: 88%, Vpack:114.900V, MotAmps:  12, BattAmps:  10, Mods: 11, MotTemp:  38C, BattTemp:  24C, PackSOC: 88%, Vcell:3.930V, MotRPM:1200, Odo: 6100km
 00002     05/12/2023 09:15:02   Riding      SOC: 86%, Vpack:114.100V, MotAmps:  20, BattAmps:  18, Mods: 11, MotTemp:  41C, BattTemp:  25C, PackSOC: 86%, Vcell:3.910V, MotRPM:2400, Odo: 6104km
 00003     05/12/2023 09:16:02   Riding      SOC: 84%, Vpack:113.700V, MotAmps:  18, BattAmps:  15, Mods: 11, MotTemp:  44C, BattTemp:  26C, PackSOC: 84%, Vcell:3.900V, MotRPM:2100, Odo: 6108km
"""


def _sim_session(tmp_path, tag="rep"):
    logger = SessionLogger(base_dir=str(tmp_path), tag=tag)
    tr = Transport(SimPort(), logger)
    tr.exec_command("login tpsreport")
    for cmd in READ_COMMANDS + ["set"] + DUMP_COMMANDS:
        tr.exec_command(cmd, idle_timeout=3.0, max_time=60.0)
    return logger.dir


# ------------------------------------------------------------------ structure

def test_report_shape_from_a_real_capture(tmp_path):
    rep = report.analyze_folder(_sim_session(tmp_path))
    assert set(rep) == {"session", "units", "counts", "health", "rides",
                        "ride_source", "ride_log_truncated"}
    assert rep["session"]["commands"]           # something was captured
    assert rep["health"]
    assert sum(rep["counts"].values()) == len(rep["health"])


def test_report_is_json_serializable_without_a_custom_encoder(tmp_path):
    """The whole point of the typed metrics: this survives json.dumps as-is."""
    rep = report.analyze_folder(_sim_session(tmp_path))
    round_tripped = json.loads(json.dumps(rep))
    assert round_tripped["health"][0]["label"] == rep["health"][0]["label"]


def test_thresholds_are_comparable_without_parsing_strings(tmp_path):
    """What an automated consumer actually does with the report."""
    rep = report.analyze_folder(_sim_session(tmp_path))
    temps = [m for m in rep["health"] if m["unit"] == "C" and m["value"] is not None]
    assert temps, "the sim capture should yield at least one temperature"
    assert all(isinstance(m["value"], (int, float)) for m in temps)
    # no regex, no split(), no strip("C")
    assert [m["label"] for m in temps if m["value"] > 1000] == []


# ---------------------------------------------------------------------- rides

def _session_with(tmp_path, **commands):
    return sessions.Session(str(tmp_path), dict(commands), "")


def test_rides_come_from_the_event_log_when_present(tmp_path):
    rep = report.analyze_session(_session_with(tmp_path, eventlogdump=RIDE_LOG))
    assert rep["ride_source"] == "eventlogdump"
    assert rep["rides"]["totals"]["samples"] == 3
    assert rep["ride_log_truncated"] is False


def test_rides_fall_back_to_the_legacy_dumplogs_command(tmp_path):
    rep = report.analyze_session(_session_with(tmp_path, dumplogs=RIDE_LOG))
    assert rep["ride_source"] == "dumplogs"
    assert rep["rides"]["totals"]["ride_count"] >= 1


def _eventlog(promised, kept, banner=""):
    """A synthetic eventlogdump: the 'Printing N of M' header, `kept` numbered
    entries, and optionally the banner a capture appended at the time."""
    rows = RIDE_LOG.strip().splitlines()
    out = ["Printing %d of %d log entries.." % (promised, promised)]
    for i in range(kept):
        out.append(re.sub(r"^ \d{5}", " %05d" % (i + 1), rows[i % len(rows)]))
    return "\n".join(out) + (("\n" + banner) if banner else "")


def test_completeness_is_re_derived_not_read_off_the_banner(tmp_path):
    # A session captured by an older OpenMBB carries a flat TRUNCATED banner even
    # when all but a couple of entries arrived (a real 2026-07-10 pull: 8593 of
    # 8595). Trusting it told the owner to re-run a 1 MB heavy read for nothing.
    stale = "### TRUNCATED: no console prompt seen before the read ended ###"
    rep = report.analyze_session(
        _session_with(tmp_path, eventlogdump=_eventlog(100, 98, stale)))
    assert rep["ride_log_truncated"] is False
    assert "floor" not in report.format_report(rep)


def test_a_genuinely_short_capture_is_still_flagged(tmp_path):
    rep = report.analyze_session(
        _session_with(tmp_path, eventlogdump=_eventlog(100, 50)))
    assert rep["ride_log_truncated"] is True
    assert "floor" in report.format_report(rep)


def test_a_truncated_capture_is_flagged_not_reported_as_whole(tmp_path):
    rep = report.analyze_session(
        _session_with(tmp_path, eventlogdump=RIDE_LOG + "\n### TRUNCATED\n"))
    assert rep["ride_log_truncated"] is True
    assert "floor" in report.format_report(rep)


def test_no_ride_telemetry_is_none_not_an_empty_summary(tmp_path):
    rep = report.analyze_session(_session_with(tmp_path, bms="", stats=""))
    assert rep["rides"] is None and rep["ride_source"] is None
    assert "No ride telemetry" in report.format_report(rep)


# ----------------------------------------------------------------------- text

def test_format_report_renders_display_not_raw_values(tmp_path):
    rep = report.analyze_folder(_sim_session(tmp_path), "F")
    text = report.format_report(rep)
    assert "Health:" in text and "== Health" in text
    # F was requested, so temperatures render in F even though value stays C
    assert " F" in text


def test_ride_temperatures_follow_the_requested_units(tmp_path):
    # the ride block was hard-coded to C: a report with health rows in F and ride
    # rows in C reads as one capture measured in two scales
    session = _session_with(tmp_path, eventlogdump=RIDE_LOG)
    rep_f = report.analyze_session(session, "F")
    assert rep_f["rides"]["totals"]["max_motor_temp_c"] == 44      # value stays C
    assert "max motor temp: 111 F" in report.format_report(rep_f)
    assert "max motor temp: 44 C" in report.format_report(
        report.analyze_session(session))                          # C unchanged


# ------------------------------------------------------------------------ CLI

def _cli(*args):
    return subprocess.run([sys.executable, "-m", "openmbb.cli", *args],
                          capture_output=True, text=True, timeout=180)


def test_cli_analyze_emits_valid_json(tmp_path):
    folder = _sim_session(tmp_path)
    r = _cli("analyze", folder, "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["health"]


def test_cli_analyze_rejects_a_folder_that_is_not_there(tmp_path):
    r = _cli("analyze", str(tmp_path / "nope"))
    assert r.returncode == 2
    assert "No such session folder" in r.stderr


def test_cli_analyze_rejects_a_folder_with_no_capture(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _cli("analyze", str(empty))
    assert r.returncode == 2
    assert "Nothing to analyze" in r.stderr


def test_cli_fail_on_alert_sets_the_exit_code(tmp_path):
    """So it can drive a script or a health check."""
    folder = _sim_session(tmp_path)
    rep = report.analyze_folder(folder)
    expected = 1 if rep["counts"]["alert"] else 0
    assert _cli("analyze", folder, "--fail-on-alert").returncode == expected
    # without the flag it always reports success
    assert _cli("analyze", folder).returncode == 0


def test_cli_sessions_lists_what_analyze_can_read(tmp_path):
    _sim_session(tmp_path)
    r = _cli("sessions", "--logdir", str(tmp_path), "--json")
    assert r.returncode == 0, r.stderr
    listed = json.loads(r.stdout)["sessions"]
    assert len(listed) == 1
    # the listed path is exactly what analyze accepts
    assert _cli("analyze", listed[0], "--json").returncode == 0


@pytest.mark.parametrize("flag", ["--selftest", "--smoketest"])
def test_existing_flags_still_parse(flag):
    """Subcommands were added as optional, so the pre-existing interface — and
    the bare `openmbb` that launches the GUI — must be untouched."""
    r = _cli("--help")
    assert r.returncode == 0
    assert flag in r.stdout
    assert "analyze" in r.stdout and "sessions" in r.stdout
