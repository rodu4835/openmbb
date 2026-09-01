"""The headless report: a saved session folder in, a JSON-ready dict out.

This is the path that does not need the bike, a serial port, or the GUI — so
these tests build sessions on disk and never construct a Tk root.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from openmbb import health, library, report, sessions
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
                        "consumption", "range", "ride_source",
                        "ride_log_truncated", "condition", "verdict", "clocks",
                        "bike_state"}
    # the bike carries several independently-set clocks and the event log is
    # timestamped by one of them, so what they read travels with the report
    assert "offset_s" in rep["clocks"]
    # the verdict is always present; "unknown" is its answer when a capture
    # carries no evidence, never an absent key that reads as fine
    assert rep["verdict"]["level"] in ("ok", "watch", "concern", "unknown")
    # the condition block is always present, even with nothing to say: its
    # `undetermined` list is the answer when a capture carries no event log
    assert "undetermined" in rep["condition"]
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


# --- the page you hand to someone else ---------------------------------------

def test_the_condition_report_leads_with_the_headline_not_the_level(tmp_path):
    # "OK" on its own reads as a clean bill of health for a capture that could
    # not measure anything; the headline is the form that carries the unanswered
    # count with it
    import datetime
    s = sessions.load_session(_sim_session(tmp_path))
    txt = report.condition_report(s, version="9.9.9",
                                  generated=datetime.datetime(2026, 8, 21, 9, 30))
    head = txt.splitlines()[:6]
    assert "OpenMBB condition report" in head[0]
    assert "2026-08-21 09:30" in txt and "v9.9.9" in txt
    # the capture is identified by WHEN it was taken, not by what its folder is
    # called - see test_the_report_never_prints_the_folders_own_name
    assert s.name not in txt
    v = report.analyze_session(s)["verdict"]
    assert v["headline"] in txt


def test_the_comparison_reaches_the_saved_page():
    """Item 28's mirror half: one composer, both surfaces. The Condition tab
    renders the same sentences (test_gui_flow), and this is the page a buyer
    reads - the surface pair this project has had to repair before.

    Driven with an assessment that HAS the metrics rather than a simulator
    capture, which carries no loaded cell reading, no current limit and no
    charge session - the first version of this test took a sim session, found
    nothing to compare, and returned before asserting anything. It passed with
    the render site deleted.
    """
    from openmbb import condition as _c

    c = {"cell_deviation": {"median_mv": 63.8, "samples": 657},
         "cell_sag": {"min_cell_mv": 3165.0, "at_amps": 136.0,
                      "at_soc_pct": 85.0, "at_pack_temp_c": 30.0},
         "derate": {"median_pct": 88.0, "worst_pct": 22.0, "samples": 1227,
                    "worst_at_pack_temp_c": 59.0, "worst_at_soc_pct": 27.0,
                    "worst_when": "07/31/2026 16:44:04", "buckets": {}},
         "charge_capacity": {"median_ah": 18.0, "sessions": 36,
                             "window_v": [103.0, 113.0]}}
    expected = _c.comparison_lines(c)
    assert expected, "the composer yielded nothing, so this proves nothing"

    body = "\n".join(report._condition_lines(c))

    assert "beside the one other measured Gen2" in body
    # the composer's own sentences, not a paraphrase of them
    for sentence in expected:
        head = sentence.split(":")[0]
        assert head in body, head


def test_the_report_never_prints_the_folders_own_name(tmp_path):
    # report.py printed `session.name` into the one page this project builds to
    # be handed to a stranger, and the PII gate guarding that page scans for
    # four MOTORCYCLE identifier shapes - so a folder named after a person went
    # through it clean and got vouched for. Filtering the name to a safe
    # CHARSET does not fix it: a person's name is already in the safe charset.
    src = _sim_session(tmp_path)
    renamed = os.path.join(os.path.dirname(src), "Daves-FXS-preinspection-2026")
    os.rename(src, renamed)
    s = sessions.load_session(renamed)
    txt = report.condition_report(s)
    assert "Daves" not in txt
    assert "preinspection" not in txt
    assert s.name not in txt


def test_a_simulator_capture_says_so_before_the_reader_reaches_a_number(tmp_path):
    # a clean page off the shipped simulator was presentable as a bike's page,
    # and nothing on it said otherwise
    s = sessions.load_session(_sim_session(tmp_path, tag="sim"))
    txt = report.condition_report(s)
    assert "SIMULATOR DATA - NOT FROM A MOTORCYCLE" in txt
    assert txt.index("NOT FROM A MOTORCYCLE") < txt.index("bike     :")


def test_a_real_capture_carries_no_such_banner(tmp_path):
    # the banner has to be absent when it should be, or it means nothing
    s = sessions.load_session(_sim_session(tmp_path, tag="COM7"))
    assert "NOT FROM A MOTORCYCLE" not in report.condition_report(s)


def test_the_report_carries_the_whole_analysis_not_just_health(tmp_path):
    # the old saved page had the health rows and nothing else, which left out
    # most of what makes it worth handing to anyone
    s = sessions.load_session(_sim_session(tmp_path))
    txt = report.condition_report(s)
    for section in ("== Health", "== Condition (pack) ==", "== Verdict =="):
        assert section in txt, section
    assert "What this is:" in txt and "What it is not:" in txt


def test_the_report_keeps_this_machine_off_the_page(tmp_path):
    # a saved-session path on Windows carries the account name. It is not a VIN,
    # which is exactly why it would otherwise survive every check here.
    s = sessions.load_session(_sim_session(tmp_path))
    txt = report.condition_report(s)
    assert s.dir not in txt
    assert "Path:" not in txt
    assert "carries no VIN and no serial numbers" in txt
    # ...and the ordinary rendering still shows it, since that one stays local
    assert "Path:" in report.format_report(report.analyze_session(s))


def test_a_report_it_cannot_vouch_for_is_withheld_entirely(tmp_path, monkeypatch):
    # this page exists to be handed to a stranger, so it gets the same treatment
    # the share-safe export does: withheld, not returned with a warning
    from openmbb import redact
    s = sessions.load_session(_sim_session(tmp_path))
    # assembled from fragments: a tracked file holding a whole shape-matching
    # token trips the release gate, which is scanning this repo for exactly that
    token = "5TESTVNPRZ" + "4200001"
    monkeypatch.setattr(redact, "find_pii_shapes", lambda text: [("VIN", token)])
    with pytest.raises(RuntimeError, match="withheld"):
        report.condition_report(s)


def test_the_report_names_the_model_but_not_the_bike(tmp_path):
    # the legacy/simulator dump shape, which parse_settings_dump also takes
    dump = (" model_year           - Model Year : 2017\n"
            " model                - Bike Model : FXS\n")
    s = sessions.Session(str(tmp_path), {"eventlogdump": RIDE_LOG}, dump)
    txt = report.condition_report(s)
    assert "2017 FXS" in txt
    # a capture with no settings dump says so rather than inventing a bike
    bare = sessions.Session(str(tmp_path), {"eventlogdump": RIDE_LOG}, "")
    assert "(not identified)" in report.condition_report(bare)


# --- the graded battery row may not report cooler than its own capture -------

def test_the_battery_grade_follows_the_hotter_of_the_two_channels(tmp_path):
    # On 2026-07-10 `stats` says 59 C and this graded "watch" (59 < 60), while
    # that same capture's log holds a genuine 60 C sample - the "alert" band of
    # the same function. Both describe the highest module ("PackTemp: h 60C" is
    # what the counter maxima too), so the honest maximum is whichever is larger.
    stats = "  - Max Battery Temp : 59 C\n"
    s = sessions.Session(str(tmp_path), {"stats": stats}, "")

    counter_only = {x["label"]: x for x in health.health_snapshot(s)}[
        "Max battery temp (lifetime)"]
    assert counter_only["status"] == "watch"          # the old, cooler answer

    with_log = {x["label"]: x for x in health.health_snapshot(s, log_peak_c=60.0)}[
        "Max battery temp (lifetime)"]
    assert with_log["status"] == "alert"
    # the value stays the counter - this row is labelled a lifetime figure and
    # attributing the log's number to it would be wrong - but both are shown,
    # because "[ALERT] 59 C" against a 60 C band reads as a contradiction
    assert with_log["value"] == 59
    assert with_log["display"] == "59 C (log: 60 C)"
    assert "grade follows the log" in with_log["note"]


def test_a_cooler_log_never_softens_the_counters_grade(tmp_path):
    # the direction matters: taking the higher can only ever make a bike look
    # WORSE, which is the safe way for a check to be wrong when a buyer relies
    # on it. A cooler log must not walk an alert back to a watch.
    s = sessions.Session(str(tmp_path), {"stats": "  - Max Battery Temp : 60 C\n"}, "")
    m = {x["label"]: x for x in health.health_snapshot(s, log_peak_c=20.0)}[
        "Max battery temp (lifetime)"]
    assert m["status"] == "alert" and m["display"] == "60 C"
    assert "grade follows the log" not in m["note"]


def test_the_library_is_not_made_to_read_a_megabyte_per_row():
    # health_snapshot is called for every capture in the save folder; the log
    # peak is optional precisely so that stays cheap
    import inspect
    sig = inspect.signature(health.health_snapshot)
    assert sig.parameters["log_peak_c"].default is None
    src = inspect.getsource(library.deep_verdict)
    assert "log_peak_c" not in src


def test_the_report_grades_from_the_log_it_already_read(tmp_path):
    # analyze_session reads the ride log anyway, so it has no excuse
    log = "\n".join(
        " 00001     06/24/2026 08:%02d:00   Riding   PackTemp: h 60C, l 58C, "
        "PackSOC: %d%%, Vpack:110.000V, BattAmps:  30, MotAmps: 30, "
        "MotRPM:3000, Odo: %dkm, MinCell: 3700mV" % (i, 90 - i, 6000 + i)
        for i in range(20))
    s = sessions.Session(str(tmp_path),
                         {"stats": "  - Max Battery Temp : 59 C\n",
                          "eventlogdump": log}, "")
    m = {x["label"]: x for x in report.analyze_session(s)["health"]}[
        "Max battery temp (lifetime)"]
    assert m["status"] == "alert"


# --- distances render in the unit that was asked for -------------------------

def test_distance_units_convert_and_rates_convert_the_other_way():
    # a mile is further than a kilometre, so Wh/mi is a LARGER number than
    # Wh/km for the same bike - the classic direction error
    assert report.fmt_km(100.0, "km") == "100.0 km"
    assert report.fmt_km(100.0, "mi") == "62.1 mi"
    assert report.fmt_per_km(90.6, "km") == "90.6"
    assert float(report.fmt_per_km(90.6, "mi")) > 90.6
    assert float(report.fmt_per_km(90.6, "mi")) == pytest.approx(145.8, abs=0.2)
    # missing reads as missing
    assert report.fmt_km(None, "mi") == "n/a"
    assert report.fmt_per_km(None, "mi") == "n/a"


def test_the_headless_report_honours_a_miles_request(tmp_path):
    log = "\n".join(
        " 00001     06/24/2026 08:%02d:00   Riding   PackTemp: h 30C, l 28C, "
        "AmbTemp: 20C, PackSOC: %d%%, Vpack:110.000V, BattAmps:  30, "
        "MotAmps: 30, MotRPM:3000, Odo: %dkm, MinCell: 3700mV"
        % (i, 100 - 2 * i, 6000 + 2 * i) for i in range(41))
    s = sessions.Session(str(tmp_path), {"eventlogdump": log}, "")
    rep = report.analyze_session(s)
    mi = report.format_report(rep, dist_units="mi")
    km = report.format_report(rep, dist_units="km")
    assert "mi from" in mi and "Wh/mi" in mi
    assert " km from" in km and "Wh/km" in km
    assert "Wh/km" not in mi and " km " not in mi.split("== Rides")[1][:400]
    # the DATA stays canonical whichever way it was rendered, so JSON consumers
    # and every threshold in the codebase keep reading kilometres
    assert rep["consumption"]["wh_per_km"] == pytest.approx(
        report.analyze_session(s)["consumption"]["wh_per_km"])
    assert "km" in str(rep["rides"]["totals"].keys())


def test_cli_analyze_refuses_a_capture_whose_headers_will_not_read(tmp_path):
    """`has_settings` was true on the strength of a FILENAME, so a folder whose
    command headers are all unreadable printed a report and exited 0 - and to a
    script driving --fail-on-alert, exit 0 means "all good"."""
    d = tmp_path / "badheaders"
    d.mkdir()
    (d / "settings_baseline_20260819_170000.txt").write_text(
        "garbage, not a settings dump\n", encoding="utf-8")
    (d / "001_bms.txt").write_text("no command header either\n", encoding="utf-8")
    r = _cli("analyze", str(d))
    assert r.returncode == 2
    assert "Nothing to analyze" in r.stderr


def test_cli_analyze_refuses_a_capture_from_a_newer_openmbb(tmp_path):
    """Exit 2, not 1: --fail-on-alert uses 1 to mean "this bike has an alert", so
    a capture that could not be READ must never leave through that door - a
    script would record a finding about a motorcycle nobody measured."""
    d = tmp_path / "future"
    d.mkdir()
    (d / "001_bms.txt").write_text(
        "# command: bms\n# time: 16:04:49.000\n\nPack Voltage : 113.000V\n",
        encoding="utf-8")
    (d / "session_meta.txt").write_text(
        "OpenMBB session metadata\ncapture_format: 2\n"
        "time: 2027-01-01T00:00:00\n", encoding="utf-8")
    r = _cli("analyze", str(d))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "format 2" in r.stderr and "format 1" in r.stderr
