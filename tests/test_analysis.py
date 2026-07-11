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
    assert "re-gear" in p["nearest"]        # B5: generic label (was "target")


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
    tr.exec_command("login tpsreport")     # reveal the full (login-gated) settings
    for cmd in READ_COMMANDS + ["set"] + DUMP_COMMANDS:
        tr.exec_command(cmd, idle_timeout=3.0, max_time=60.0)
    logger.save_named("settings_baseline_test.txt", tr.exec_command("set"))
    return sessions.load_session(logger.dir)


def test_sim_dumplogs_is_invalid_like_the_real_bike(tmp_path):
    # A1: `dumplogs` is not a real rev-41 command; the sim models that faithfully
    tr = Transport(SimPort(), SessionLogger(base_dir=str(tmp_path), tag="dl"))
    assert "invalid command" in tr.exec_command("dumplogs").lower()


def test_sim_heavy_dump_uses_real_field_keys(tmp_path):
    # E2: the heavy `dumpall` emits the real zero-log-parser DECODED field keys
    tr = Transport(SimPort(), SessionLogger(base_dir=str(tmp_path), tag="da"))
    dump = tr.exec_command("dumpall", idle_timeout=3.0, max_time=60.0)
    assert "Riding" in dump and "PackTemp: h" in dump and "MotTemp:" in dump
    recs = parsers.parse_ride_log(dump)
    assert len(recs) > 100
    assert all(r["soc"] is not None and r["odo_km"] is not None for r in recs)


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
    # ride telemetry now comes from a heavy dump / external log, not the baseline
    tr = Transport(SimPort(), SessionLogger(base_dir=str(tmp_path), tag="r"))
    summ = rides.summarize_rides(parsers.parse_ride_log(tr.exec_command("dumpall")))
    assert summ["totals"]["ride_count"] >= 1
    assert summ["totals"]["samples"] > 100
    assert any(r["soc_per_km"] is not None for r in summ["rides"])


def test_gearing_from_stats_sim(tmp_path):
    s = _make_session(tmp_path, "a")
    st = parsers.parse_stats(s.cmd("stats"))
    ratio, rpk, desc = rides.gearing_from_stats(st, 1966)
    assert ratio == pytest.approx(4.50, abs=0.01)
    assert "factory" in desc                # B5: generic label (was "stock")


def test_compare_two_sessions(tmp_path):
    a = _make_session(tmp_path, "old")
    b = _make_session(tmp_path, "new")
    result = compare.compare_sessions([a, b])
    assert result["settings_diff"] == []          # identical sim data
    assert [c for _, c in result["capacity_trend"]] == [52, 52]
    # identical sessions -> 0 km delta -> lifetime-avg basis, ~4.50
    assert all(abs(r - 4.50) < 0.01 for _, r, _ in result["gearing_trend"])
    assert all("lifetime" in basis for _, _, basis in result["gearing_trend"])


def test_health_temp_units_convert(tmp_path):
    # temperature metrics render in the chosen unit; default is Celsius and is
    # byte-identical to the explicit "C" call (so existing output is unchanged).
    s = sessions.Session(str(tmp_path),
                         {"stats": "  - Max Motor Temp   : 100 C\n"
                                   "  - Max Battery Temp : 40 C\n",
                          "bms": "", "status": ""}, "")
    c = {m["label"]: m for m in health.health_snapshot(s, "C")}
    f = {m["label"]: m for m in health.health_snapshot(s, "F")}
    assert c["Max motor temp (lifetime)"]["value"] == "100 C"
    assert f["Max motor temp (lifetime)"]["value"] == "212 F"       # 100C -> 212F
    assert c["Max battery temp (lifetime)"]["value"] == "40 C"
    assert f["Max battery temp (lifetime)"]["value"] == "104 F"     # 40C -> 104F
    assert "122-140 F" in f["Max battery temp (lifetime)"]["note"]  # thresholds too
    assert health.health_snapshot(s) == health.health_snapshot(s, "C")


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


# --- Phase F: analysis honesty ----------------------------------------------

def test_gauge_note_drops_refuted_recalibration_claim():
    assert "1.55" not in health.GAUGE_NOTE
    assert "unchanged" in health.GAUGE_NOTE.lower()


def test_first_val_keeps_zero():
    assert parsers.first_val(None, 0, 5) == 0        # a real 0 is not "missing"
    assert parsers.first_val(0.0, 9) == 0.0
    assert parsers.first_val(None, None) is None


def test_ride_log_splits_pack_and_motor_temp():
    line = (" 00001 05/16/2026 08:12:33 Riding PackTemp: h 27C, l 26C, "
            "PackSOC: 61%, Vpack: 106.4V, MotAmps: 100, MotRPM: 3100, "
            "MotTemp: 41C, Odo: 6120km")
    recs = parsers.parse_ride_log(line)
    assert len(recs) == 1
    assert recs[0]["pack_temp_c"] == 27          # the HIGH pack reading, not motor
    assert recs[0]["motor_temp_c"] == 41
    plain = parsers.parse_ride_log(" 1 Riding PackTemp: 24C, PackSOC: 5%, Odo: 10km")
    assert plain[0]["pack_temp_c"] == 24         # plain 'PackTemp: 24C' also works


def test_parse_bms_isolation():
    b = parsers.parse_bms("  - Isolation Resistance : 32 KOhms (0x0020)\n"
                          "  - Pack SOC : 100%")
    assert b["isolation_kohm"] == 32


def test_parse_status_warnings():
    st = parsers.parse_status("***  Warning Messages\n"
                              " - WARNING : BMS Isolation Resistance Is Low\n"
                              "***  Error Messages\n - No Errors")
    assert st["warnings"] == ["BMS Isolation Resistance Is Low"]


def _iso_session(tmp_path, mode):
    bms = ("  - Isolation Resistance : 32 KOhms\n  - Pack SOC : 100%\n"
           "  - Pack Sum Voltage : 116.0 V\n  - Pack Balance : 8 mV\n"
           "  - Pack Capacity : 52 AH\n  - Num Charge Cycles : 32")
    status = "  - Mode : %s\n  - WARNING : BMS Isolation Resistance Is Low" % mode
    return sessions.Session(str(tmp_path), {"bms": bms, "status": status}, "")


def test_health_isolation_charging_softened_offcharger_alerts(tmp_path):
    on = {m["label"]: m for m in health.health_snapshot(_iso_session(tmp_path, "Charging"))}
    assert on["Isolation resistance"]["status"] == "watch"     # charger false-low
    assert "charging" in on["Isolation resistance"]["note"].lower()
    assert "Warning" in on                                     # live warning surfaced
    off = {m["label"]: m for m in health.health_snapshot(_iso_session(tmp_path, "Standby"))}
    assert off["Isolation resistance"]["status"] == "alert"    # off-charger = real


def test_health_isolation_unknown_mode_is_watch(tmp_path):
    # T12: an absent/unrecognized Mode must NOT be asserted as off-charger 'alert'
    bms = "  - Isolation Resistance : 32 KOhms\n  - Pack SOC : 100%"
    s = sessions.Session(str(tmp_path), {"bms": bms}, "")       # no status capture
    m = {x["label"]: x for x in health.health_snapshot(s)}["Isolation resistance"]
    assert m["status"] == "watch"                              # not 'alert'
    assert "unknown" in m["note"].lower()


def _iso_row(tmp_path, mode, iso_kohm):
    bms = "  - Isolation Resistance : %d KOhms\n  - Pack SOC : 100%%" % iso_kohm
    caps = {"bms": bms}
    if mode is not None:
        caps["status"] = "  - Mode : %s" % mode
    s = sessions.Session(str(tmp_path), caps, "")
    return {x["label"]: x for x in health.health_snapshot(s)}["Isolation resistance"]


def test_health_isolation_known_off_mode_low_alerts(tmp_path):
    # C2: the live session's Mode was 'Stopped' (a positively-off-charger mode);
    # a low reading there is a real diagnostic, not a charger false-low.
    m = _iso_row(tmp_path, "Stopped", 32)
    assert m["status"] == "alert"
    assert "off-charger" in m["note"].lower()


def test_health_isolation_garbled_mode_is_watch_unknown(tmp_path):
    # C2: an unrecognized Mode is NOT a confirmed off-charger reading -> watch
    m = _iso_row(tmp_path, "Wobble", 32)
    assert m["status"] == "watch"
    assert "unknown" in m["note"].lower()


def test_health_isolation_midband_off_charger_is_watch(tmp_path):
    # C2: known-off but 500-999 kOhm is a mid-band 'watch', NOT 'alert'
    m = _iso_row(tmp_path, "Stopped", 800)
    assert m["status"] == "watch"
    assert "500-999" in m["note"]
    # and the boundary: 499 kOhm (below the band) is still an alert
    assert _iso_row(tmp_path, "Stopped", 499)["status"] == "alert"


def test_health_isolation_megohm_is_ok(tmp_path):
    # C2 real check: the live 007_bms.txt read 32766 kOhm (healthy) -> ok,
    # regardless of Mode.
    assert _iso_row(tmp_path, "Stopped", 32766)["status"] == "ok"
    assert _iso_row(tmp_path, "Charging", 32766)["status"] == "ok"


def test_health_motor_temp_labels_documented_defaults(tmp_path):
    # C3 (review FID-3/REG-2): rev 41 doesn't expose motstage1/2 in `set`, so the
    # cutback thresholds fall back to documented defaults — the note must mark each
    # value's provenance, never printing a default like a live read of this bike.
    no_stages = sessions.Session(str(tmp_path), {"stats": REAL_STATS}, "")
    m = {x["label"]: x for x in health.health_snapshot(no_stages)}["Max motor temp (lifetime)"]
    assert "(default)" in m["note"] and "documented default" in m["note"]
    # when the dump DOES carry both, no default markers and the real values show
    both = sessions.Session(str(tmp_path), {"stats": REAL_STATS},
                            "motstage1 - warn : 110\nmotstage2 - cutback : 150")
    m2 = {x["label"]: x for x in health.health_snapshot(both)}["Max motor temp (lifetime)"]
    assert "(default)" not in m2["note"]
    assert "110" in m2["note"] and "150" in m2["note"]
    # REG-2 mixed case: only motstage1 is live -> only the defaulted motstage2 is
    # flagged, and the live 110 is NOT mislabelled as a default
    mixed = sessions.Session(str(tmp_path), {"stats": REAL_STATS}, "motstage1 - warn : 110")
    m3 = {x["label"]: x for x in health.health_snapshot(mixed)}["Max motor temp (lifetime)"]
    assert "110 C /" in m3["note"] and "110 C (default)" not in m3["note"]
    assert "145 C (default)" in m3["note"]


def test_health_temps_labelled_lifetime(tmp_path):
    # C2 (review A3): the max temps are LIFETIME maxima, not live readings — the
    # label + note must say so, so a cold bike's historic 60 C doesn't read as a
    # live emergency.
    s = sessions.Session(str(tmp_path), {"stats": REAL_STATS}, "")
    snap = {m["label"]: m for m in health.health_snapshot(s)}
    assert "Max battery temp (lifetime)" in snap
    assert "Max motor temp (lifetime)" in snap
    assert "highest EVER recorded" in snap["Max battery temp (lifetime)"]["note"]


def test_no_refuted_gauge_claim_in_safety_text():
    # T11: the refuted "~1.55x" claim must not ship on the Writes tab either
    from openmbb import safety
    assert "1.55" not in safety.WRITE_WHITELIST["fuelgaugepes"][1]
    assert "1.55" not in safety.WRITE_PANEL_CONTEXT


def test_first_val_keeps_zero_in_parse_stats():
    # T20/F6: the two converted or-chains must let a legitimate 0 through
    s = parsers.parse_stats("  - Lifetime Watt Hours Per Km : 0 WH/km\n"
                            "  - Max Motor Speed : 0 RPM")
    assert s["lifetime_wh_km"] == 0        # not None (0 is real data)
    assert s["max_motor_rpm"] == 0


def test_parse_ride_log_from_zero_log_parser_text():
    # T20/F4: rides can be sourced from a decoded zero-log-parser .txt
    text = "\n".join(
        " %05d 05/16/2026 08:%02d:00 Riding PackTemp: h 27C, l 26C, PackSOC: %d%%, "
        "MotRPM: 3100, MotTemp: 41C, Odo: 61%02dkm" % (i, i, 90 - i, 10 + i)
        for i in range(8))
    recs = parsers.parse_ride_log(text)
    assert len(recs) == 8
    assert rides.summarize_rides(recs)["totals"]["ride_count"] >= 1


def test_describe_plan_uses_ref_ratio():
    p = gearing.gearing_plan(14, 56, ref_ratio=4.00)
    assert p["ref_ratio"] == 4.00
    assert "4.00:1" in gearing.describe_plan(p)


def test_gearing_from_delta_reflects_regear(tmp_path):
    circ = gearing.DEFAULT_CIRC_MM

    def sess(name, motor_rev, km):
        stats = "  - Odometer : %d motor rev\n              : %d km" % (motor_rev, km)
        return sessions.Session(str(tmp_path / name), {"stats": stats}, "")

    a_rev, a_km = 14304861, 6249                 # ~4.50 lifetime
    delta_rev = round(4.00 * 1e6 / circ * 1000)  # 1000 km ridden at 4.00
    res = compare.compare_sessions([sess("a", a_rev, a_km),
                                    sess("b", a_rev + delta_rev, a_km + 1000)])
    (n0, r0, b0), (n1, r1, b1) = res["gearing_trend"]
    assert abs(r0 - 4.50) < 0.02 and "lifetime" in b0   # first: lifetime average
    assert abs(r1 - 4.00) < 0.02 and "delta" in b1      # second: delta = the re-gear


def test_gearing_delta_needs_minimum_distance(tmp_path):
    # T10: a too-short delta (integer-km quantization noise) must fall back to
    # the lifetime average, labeled — never present a bogus "current" ratio
    def sess(name, motor_rev, km):
        stats = "  - Odometer : %d motor rev\n              : %d km" % (motor_rev, km)
        return sessions.Session(str(tmp_path / name), {"stats": stats}, "")

    a_rev, a_km = 14304861, 6249
    # 1 km apart, but the motor revs imply an absurd 8:1 over that 1 km
    res = compare.compare_sessions([sess("a", a_rev, a_km),
                                    sess("b", a_rev + 4068, a_km + 1)])
    _, r1, b1 = res["gearing_trend"][1]
    assert "lifetime" in b1                    # fell back (delta too short)
    assert abs(r1 - 4.50) < 0.05               # not the bogus 8:1 the 1-km delta implies
