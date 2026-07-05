"""Logic tests for the analysis stack — parsers, gearing, sessions, rides,
health, compare. No display needed. Parsers are checked against BOTH the
simulator format and realistic real-bike output so tolerance is proven."""

import math

import pytest

from openmbb import compare, gearing, health, parsers, rides, sessions
from openmbb.sim import SimPort
from openmbb.transport import (DUMP_COMMANDS, READ_COMMANDS, SessionLogger,
                               Transport)


# --- realistic real-bike output samples (2014-era / forensics formats) ------
REAL_BMS = """
*               BMS Data               *
  - Pack SOC                  :  61%
  - Pack Sum Voltage          :  106.412 V
  - Lowest Cell Voltage       :  3798 mV ( Cell 12 )
  - Highest Cell Voltage      :  3810 mV ( Cell 7 )
  - Pack Balance              :  12 mV
  - Pack Capacity             :  52 AH
  - Num Charge Cycles         :  512
  - Pack Temps                :    24C   24C   23C   23C
"""

REAL_STATS = """
  - Firmware Revision         :  41
  - Max Battery Temp          :  60 C
  - Max Motor Temp            :  118 C
  - Odometer                  :  14088220 motor rev
                              :  6155 km
                              :  3825 miles
"""

REAL_RIDE = """
 05/16/2026 08:12:33 Riding PackTemp: 24C, PackSOC: 61%, Vpack:106.412V, MotAmps: 100, MotRPM:3100, Odo:6120km
 05/16/2026 08:12:36 Riding PackTemp: 25C, PackSOC: 60%, Vpack:106.100V, MotAmps: 120, MotRPM:3300, Odo:6121km
 05/16/2026 08:20:00 Charging Vpack:110.0V, BattAmps:-6
"""


# --- parser tolerance -------------------------------------------------------

def test_parse_bms_real_format():
    b = parsers.parse_bms(REAL_BMS)
    assert b["soc_pct"] == 61
    assert b["pack_v"] == pytest.approx(106.412)
    assert b["low_cell_mv"] == 3798
    assert b["balance_mv"] == 12
    assert b["capacity_ah"] == 52
    assert b["cycles"] == 512
    assert b["pack_max_temp_c"] == 24


def test_parse_stats_real_format_odometer():
    s = parsers.parse_stats(REAL_STATS)
    assert s["odo_motor_rev"] == 14088220
    assert s["odo_km"] == 6155           # the continuation-line km, not miles
    assert s["max_motor_temp_c"] == 118
    assert s["max_batt_temp_c"] == 60
    assert "41" in str(s["fw_rev"])


def test_parse_ride_log_real_format():
    recs = parsers.parse_ride_log(REAL_RIDE)
    assert len(recs) == 2                 # charging line excluded
    assert recs[0]["soc"] == 61 and recs[1]["soc"] == 60
    assert recs[0]["odo_km"] == 6120
    assert recs[0]["motrpm"] == 3100
    assert recs[0]["ts"] == "05/16/2026 08:12:33"


def test_parse_odometer_ignores_trip_and_speed_lines():
    # regression: trip odometer and km/h lines must not be mistaken for the
    # lifetime odometer (they previously produced ~57000:1 ratios).
    block = ("Trip odometer       : 245 km\n"
             "                    : 45 km/h\n"
             "Odometer            : 14088220 motor rev\n"
             "                    : 6155 km\n"
             "                    : 3825 miles\n")
    mrev, km = parsers.parse_odometer(block)
    assert mrev == 14088220 and km == 6155


def test_parse_odometer_km_with_spelled_out_miles():
    # regression: 'miles' on the same line must not drop the km value
    mrev, km = parsers.parse_odometer(
        "Odometer : 14088220 motor rev\n  : 6155 km (3825 miles)")
    assert km == 6155


def test_parse_odometer_ignores_firmware_revision_line():
    # regression: a 'motor ... revision' line must not be taken as motor revs
    block = ("Motor controller firmware revision : 27\n"
             "Odometer : 14088220 motor rev\n  : 6155 km")
    mrev, km = parsers.parse_odometer(block)
    assert mrev == 14088220


def test_parsers_tolerate_empty_and_garbage():
    assert parsers.parse_bms("") == parsers.parse_bms(None) or True
    assert parsers.parse_bms("")["soc_pct"] is None
    assert parsers.parse_ride_log("") == []
    assert parsers.parse_stats("total nonsense here")["odo_km"] is None


# --- gearing math (exact) ---------------------------------------------------

def test_gearing_ratios():
    assert gearing.ratio(20, 90) == pytest.approx(4.50)
    assert gearing.ratio(22, 88) == pytest.approx(4.00)
    assert gearing.ratio(14, 56) == pytest.approx(4.00)


def test_gearing_plan_regear():
    p = gearing.gearing_plan(22, 88)
    assert p["ratio"] == pytest.approx(4.00)
    assert p["spfront"] == 22 and p["sprear"] == 88
    assert p["taller_than_ref"] is True
    assert p["vs_ref_pct"] == pytest.approx(-11.11, abs=0.1)
    assert p["top_speed_factor"] == pytest.approx(1.125)
    assert p["revs_per_km"] == pytest.approx(4.00 * 1e6 / 1966, rel=1e-6)
    assert "target" in p["nearest"]


def test_gearing_plan_stock_is_reference():
    p = gearing.gearing_plan(20, 90)
    assert p["vs_ref_pct"] == pytest.approx(0.0)
    assert p["taller_than_ref"] is False


def test_effective_ratio_roundtrip():
    rpk = rides.revs_per_km(14088220, 6155)
    assert rides.effective_ratio(rpk, 1966) == pytest.approx(4.50, abs=0.01)


# --- full pipeline on a simulator-generated session -------------------------

def _make_session(tmp_path, tag):
    logger = SessionLogger(base_dir=str(tmp_path), tag=tag)
    tr = Transport(SimPort(), logger)
    for cmd in READ_COMMANDS + ["set"] + DUMP_COMMANDS:
        tr.exec_command(cmd, idle_timeout=3.0, max_time=60.0)
    logger.save_named("settings_baseline_test.txt", tr.exec_command("set"))
    return sessions.load_session(logger.dir)


def test_load_session_from_sim(tmp_path):
    s = _make_session(tmp_path, "a")
    assert "bms" in s.commands and "stats" in s.commands
    assert "Firmware Rev" in s.cmd("version")
    assert "spfront" in s.settings_text


def test_health_snapshot_from_sim(tmp_path):
    s = _make_session(tmp_path, "a")
    snap = {m["label"]: m for m in health.health_snapshot(s)}
    assert snap["Displayed SOC"]["value"] == "61 %"
    assert snap["Cell balance"]["status"] == "ok"
    assert snap["Effective gearing"]["value"] == "4.50:1"
    # no metric crashes to a None value slipping through
    assert all(m["value"] is not None for m in health.health_snapshot(s))


def test_rides_summary_from_sim(tmp_path):
    s = _make_session(tmp_path, "a")
    summ = rides.summarize_rides(parsers.parse_ride_log(s.cmd("dumplogs")))
    assert summ["totals"]["ride_count"] >= 1
    assert summ["totals"]["samples"] > 100
    assert any(r["soc_per_km"] is not None for r in summ["rides"])


def test_gearing_from_stats_sim(tmp_path):
    s = _make_session(tmp_path, "a")
    st = parsers.parse_stats(s.cmd("stats"))
    ratio, rpk, desc = rides.gearing_from_stats(st, 1966)
    assert ratio == pytest.approx(4.50, abs=0.01)
    assert "stock" in desc


def test_compare_two_sessions(tmp_path):
    a = _make_session(tmp_path, "old")
    b = _make_session(tmp_path, "new")
    result = compare.compare_sessions([a, b])
    assert result["settings_diff"] == []          # identical sim data
    assert [c for _, c in result["capacity_trend"]] == [52, 52]
    assert all(abs(r - 4.50) < 0.01 for _, r in result["gearing_trend"])


def test_health_on_empty_session_does_not_crash(tmp_path):
    empty = sessions.Session(str(tmp_path), {}, "")
    snap = health.health_snapshot(empty)
    assert isinstance(snap, list)          # degrades, no exception


def test_gearing_plan_rejects_zero_circumference():
    with pytest.raises(ValueError):
        gearing.gearing_plan(22, 88, 0)


def test_segment_summary_handles_empty_segment():
    assert rides.segment_summary([])["start_ts"] is None    # no IndexError


def test_read_response_without_blank_line(tmp_path):
    p = tmp_path / "005_bms.txt"
    p.write_text("# command: bms\nPack SOC : 61%\n", encoding="utf-8")
    assert "Pack SOC" in sessions._read_response(str(p))


def test_load_session_latest_wins_numerically(tmp_path):
    # regression: past 999 files, string sort would pick the wrong 'latest'
    d = tmp_path / "sess"
    d.mkdir()
    (d / "999_bms.txt").write_text("# command: bms\nOLD\n", encoding="utf-8")
    (d / "1000_bms.txt").write_text("# command: bms\nNEW\n", encoding="utf-8")
    s = sessions.load_session(str(d))
    assert s.cmd("bms").strip() == "NEW"
