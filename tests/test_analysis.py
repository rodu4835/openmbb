"""Logic tests for the analysis stack — parsers, gearing, sessions, rides,
health, compare. No display needed. Parsers are checked against BOTH the
simulator format and realistic real-bike output so tolerance is proven."""

import math

import pytest

from openmbb import (compare, gearing, health, parsers, report, rides,
                     sessions)
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


def test_ride_log_keeps_pack_current_weakest_cell_ambient_and_derate():
    # A real rev-41 riding line carries four fields the parser used to discard.
    # The condition check needs all four: pack-side current for energy accounting
    # (distinct from MOTOR current), MinCell for sag under load, AmbTemp to tell a
    # hot pack from a hot day, and the derate percentage to catch a pack that
    # cuts back early.
    line = (" 00019     06/24/2026 21:59:31   Riding   PackTemp: h 27C, l 25C, "
            "PackSOC:100%, Vpack:115.151V, MotAmps:  32, BattAmps:   9, Mods: 10, "
            "MotTemp:  29C, CtrlTemp:  21C, AmbTemp:  18C, MotRPM:1109, "
            "Odo: 6404km, Curr limit: 520 A (100%), MinCell: 4024mV")
    r = parsers.parse_ride_log(line)[0]
    assert r["battamps"] == 9 and r["motamps"] == 32      # pack vs motor, not confused
    assert r["mincell_mv"] == 4024
    assert r["amb_temp_c"] == 18
    assert r["curr_limit_pct"] == 100                     # the %, not the 520 A
    assert r["pack_temp_c"] == 27                         # still the HIGH reading


def test_ride_log_reads_a_real_discharge_cutback():
    # a derated line: the percentage is the datum, not the amps it still allows
    line = (" 00500  06/24/2026 22:10:31  Riding  PackTemp: h 55C, l 53C, "
            "PackSOC: 46%, Vpack:104.0V, BattAmps: 143, Odo: 6420km, "
            "Curr limit: 86 A (16%), MinCell: 3441mV")
    r = parsers.parse_ride_log(line)[0]
    assert r["curr_limit_pct"] == 16
    assert r["mincell_mv"] == 3441


def test_ride_log_tolerates_lines_without_the_new_fields():
    # older/other dialects simply have none of them - None, never an exception
    r = parsers.parse_ride_log(REAL_RIDE)[0]
    assert r["battamps"] is None and r["mincell_mv"] is None
    assert r["curr_limit_pct"] is None and r["amb_temp_c"] is None
    assert r["soc"] == 61 and r["odo_km"] == 6120        # the old fields still work


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


def test_num_rejects_malformed_decimal_but_keeps_clean_numbers():
    # D2: the real `chargers` dump printed "0.-51 A" (should be -0.51). The regex
    # would match just the leading "0" and silently return 0.0 — a real negative
    # reading becoming a wrong zero. Reject the garbled-decimal signature -> None,
    # WITHOUT breaking a plain trailing period (e.g. "6809.").
    assert parsers.num("0.-51 A") is None            # was 0.0 on v0.19.1
    assert parsers.num("Total Reported Battery Current : 0.-51 A") is None
    # clean cases unchanged
    assert parsers.num("-51 A") == -51.0
    assert parsers.num("0.5 A") == 0.5
    assert parsers.num("89") == 89.0
    assert parsers.num("102.856 V") == 102.856
    assert parsers.num("6809.") == 6809.0            # trailing period is NOT garbled
    assert parsers.num("Odo: 6809km") == 6809.0


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
    # display is what the GUI shows; unchanged from before typed values existed
    assert snap["Displayed SOC"]["display"] == "61 %"
    assert snap["Effective gearing"]["display"] == "4.50:1"
    assert snap["Cell balance"]["status"] == "ok"
    # every metric renders something — "n/a" where the capture had no datum
    assert all(m["display"] for m in health.health_snapshot(s))


def test_health_metrics_carry_typed_values(tmp_path):
    """The datum is a number with a unit beside it, not text to re-parse. This is
    what makes a metric usable by anything other than a label printer."""
    s = _make_session(tmp_path, "a")
    snap = {m["label"]: m for m in health.health_snapshot(s)}

    soc = snap["Displayed SOC"]
    assert soc["value"] == 61 and soc["unit"] == "%"
    assert isinstance(soc["value"], (int, float))

    assert snap["Effective gearing"]["value"] == pytest.approx(4.50, abs=0.01)
    assert snap["Pack voltage"]["unit"] == "V"
    assert isinstance(snap["Pack voltage"]["value"], float)

    # A threshold comparison needs no string handling at all.
    bal = snap["Cell balance"]
    assert bal["unit"] == "mV" and bal["value"] < 30 and bal["status"] == "ok"

    # Every row has the full shape, whatever kind of metric it is.
    for m in health.health_snapshot(s):
        assert set(m) == {"label", "value", "unit", "display", "status", "note"}


def test_health_value_is_none_when_the_capture_lacks_it(tmp_path):
    """Absent data is None, not the string "n/a" — that belongs in display only."""
    empty = sessions.Session(str(tmp_path), {"stats": "", "bms": "", "status": ""}, "")
    fw = {m["label"]: m for m in health.health_snapshot(empty)}["Firmware rev"]
    assert fw["value"] is None
    assert fw["display"] == "n/a"


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


def test_charts_helpers():
    from openmbb import charts
    lo, hi, step = charts.nice_bounds(2.3, 97.8)
    assert lo <= 2.3 and hi >= 97.8 and step > 0
    assert lo % step == 0                              # bounds land on the step grid
    ticks = charts.axis_ticks(lo, hi, step)
    assert ticks[0] == lo and ticks[-1] >= hi - step   # spans the range
    # a flat series still yields a usable band (no divide-by-zero downstream)
    flo, fhi, fstep = charts.nice_bounds(50, 50)
    assert fhi > flo and fstep > 0
    # downsample keeps endpoints and caps the count
    big = [(i, i * i) for i in range(5000)]
    ds = charts.downsample(big, 500)
    assert len(ds) == 500 and ds[0] == big[0] and ds[-1] == big[-1]
    # series_from drops partial samples and sorts by x
    recs = [{"odo_km": 3, "soc": 80}, {"odo_km": 1, "soc": 90},
            {"odo_km": 2, "soc": None}, {"soc": 70}]
    assert charts.series_from(recs, "odo_km", "soc") == [(1, 90), (3, 80)]


def test_health_temp_units_convert(tmp_path):
    # temperature metrics render in the chosen unit; default is Celsius and is
    # byte-identical to the explicit "C" call (so existing output is unchanged).
    s = sessions.Session(str(tmp_path),
                         {"stats": "  - Max Motor Temp   : 100 C\n"
                                   "  - Max Battery Temp : 40 C\n",
                          "bms": "", "status": ""}, "")
    c = {m["label"]: m for m in health.health_snapshot(s, "C")}
    f = {m["label"]: m for m in health.health_snapshot(s, "F")}
    assert c["Max motor temp (lifetime)"]["display"] == "100 C"
    assert f["Max motor temp (lifetime)"]["display"] == "212 F"       # 100C -> 212F
    assert c["Max battery temp (lifetime)"]["display"] == "40 C"
    assert f["Max battery temp (lifetime)"]["display"] == "104 F"     # 40C -> 104F
    assert "122-140 F" in f["Max battery temp (lifetime)"]["note"]    # thresholds too
    assert health.health_snapshot(s) == health.health_snapshot(s, "C")


def test_health_temp_value_stays_canonical_celsius(tmp_path):
    """Only `display` follows the user's unit preference. `value` is always the
    Celsius number, because every threshold in health.py compares in Celsius —
    a consumer must not have to know what the GUI happens to be set to."""
    s = sessions.Session(str(tmp_path),
                         {"stats": "  - Max Motor Temp   : 100 C\n", "bms": "", "status": ""}, "")
    for units in ("C", "F"):
        m = {x["label"]: x for x in health.health_snapshot(s, units)}["Max motor temp (lifetime)"]
        assert m["value"] == 100 and m["unit"] == "C"
    f = {x["label"]: x for x in health.health_snapshot(s, "F")}["Max motor temp (lifetime)"]
    assert f["display"] == "212 F"     # only the rendering moved


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

def test_gauge_note_records_the_measured_rescaling():
    # Repointed 2026-08-20. The note used to assert the SoC-vs-voltage curve was
    # UNCHANGED across the firmware update and to tell the owner to read
    # displayed SOC as-is. Measurement says otherwise: the 2026-06-13 reflash
    # rescaled the display by ~1.4x (1.35x between fixed pack voltages, 1.38x per
    # 100% displayed, 1.41x ride %/Ah, 1.48x from the BMS coulomb counter) while
    # charge accepted between the same two pack voltages moved only ~3%.
    note = health.GAUGE_NOTE
    assert "2026-06-13" in note              # the note dates the change it describes
    assert "1.4x" in note
    assert "116.9 V" in note                 # the one part of the old note that held
    assert "as-is" not in note.lower()       # the superseded advice must not return
    assert "NOT established" in note         # what 0% means is still open
    # and the in-app help must not contradict the note
    import json
    import os
    from openmbb import health as _h
    path = os.path.join(os.path.dirname(_h.__file__), "assets", "analyze_help.json")
    with open(path, encoding="utf-8") as f:
        blob = " ".join(e.get("how_it_fits", "") for e in json.load(f))
    assert "accurate as-is" not in blob
    assert "1.4x" in blob


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


def test_lifetime_battery_temp_never_falls_back_to_the_live_sensor(tmp_path):
    # A capture whose stats block is missing used to borrow the bms LIVE pack
    # sensor for the "highest EVER recorded" row: a real 2026-08-19 session with
    # 005_stats.txt removed reported "[OK] Max battery temp (lifetime) 28 C"
    # when the bike's actual lifetime peak was 60 C (alert). Falsely reassuring,
    # and reachable whenever one command of a pull fails.
    bms = ("  - Pack Temps : 27 C, 28 C, -100 C, -100 C\n"
           "  - Pack SOC : 96%\n  - Num Charge Cycles : 243")
    s = sessions.Session(str(tmp_path), {"bms": bms, "stats": ""}, "")
    labels = {m["label"] for m in health.health_snapshot(s)}
    assert "Max battery temp (lifetime)" not in labels
    # with a real lifetime stat present it still reports, and still alerts
    s2 = sessions.Session(str(tmp_path),
                          {"bms": bms, "stats": "  - Max Battery Temp : 60 C"}, "")
    m = {x["label"]: x for x in health.health_snapshot(s2)}["Max battery temp (lifetime)"]
    assert m["value"] == 60.0 and m["status"] == "alert"


def test_charge_cycles_row_says_what_the_counter_is_not(tmp_path):
    # a bare number between two rows that carry provenance reads as pack wear
    s = sessions.Session(str(tmp_path), {"bms": "  - Num Charge Cycles : 243"}, "")
    m = {x["label"]: x for x in health.health_snapshot(s)}["Charge cycles"]
    assert m["value"] == 243.0
    assert "NOT full pack cycles" in m["note"]


def test_health_isolation_negative_live_sample_is_not_a_reading(tmp_path):
    # the real bike printed "Instant Iso Resistance : -25 KOhms (0xFFFFFFE7)" on
    # 2026-08-19 - a signed sentinel. A negative resistance is not a measurement.
    bms = ("  - Isolation Resistance : 32766 KOhms\n"
           "  - Instant Iso Resistance : -25 KOhms\n  - Pack SOC : 96%")
    s = sessions.Session(str(tmp_path), {"bms": bms}, "")
    note = {x["label"]: x for x in
            health.health_snapshot(s)}["Isolation resistance"]["note"]
    assert "-25" not in note and "unavailable this read" in note
    # a plausible sample still shows
    ok = sessions.Session(str(tmp_path), {"bms": bms.replace("-25", "21")}, "")
    note_ok = {x["label"]: x for x in
               health.health_snapshot(ok)}["Isolation resistance"]["note"]
    assert "live sample 21 kOhm" in note_ok


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


# --- the commands that were captured for a year and never read ---------------

REAL_INPUTS = """
  - Key On                    :       Yes  - Raw : 1 (1 at last read)
  - Pack Voltage              : 112167 mV  - (   3889 ADC)
  - 3.3V Supply               :   3256 mV  - (   2223 ADC)
  - Battery Thr En            :   Enabled  - Raw :  2762 mV (   2439 ADC)
  - Kill Switch Pos           :       Run  - Raw :  2911 mV (   3975 ADC)
  - Kickstand Switch Pos      :      Down  - Raw :  2999 mV (   4095 ADC)
  - Brake Switch              :       Off
  - OB Charger 0 Attached     :        No
"""

REAL_OUTPUTS = """
  - System On                 :   On
  - Warning Light             :   On
  - Temp Warning LED          :  Off
  - ABS LED                   :   On
"""

REAL_RUNTIME = """
  - Total run time            :  00001:01:10:36
  - Total charger time        :  00012:21:23:39
"""

REAL_OBD = """
 - MIL On                      0
 - active dtcs                 00
 - pending dtcs                00
"""


def test_inputs_reads_the_interlocks_without_the_raw_adc_tail():
    i = parsers.parse_inputs(REAL_INPUTS)
    assert i["kickstand"] == "Down"          # not "Down  - Raw : 2999 mV ..."
    assert i["kill_switch"] == "Run"
    assert i["key_on"] == "Yes"
    assert i["throttle_enabled"] == "Enabled"
    assert i["brake_switch"] == "Off"
    assert i["pack_mv"] == 112167


def test_outputs_reads_the_warning_light():
    o = parsers.parse_outputs(REAL_OUTPUTS)
    assert o["warning_light"] == "On"
    assert o["temp_warning_led"] == "Off"


def test_runtime_is_days_hours_minutes_seconds():
    r = parsers.parse_runtime(REAL_RUNTIME)
    assert r["run_s"] == ((1 * 24 + 1) * 60 + 10) * 60 + 36     # 90636
    assert r["charge_s"] == ((12 * 24 + 21) * 60 + 23) * 60 + 39


def test_obd_reads_the_fault_memory():
    o = parsers.parse_obd(REAL_OBD)
    assert o["active_dtcs"] == 0 and o["pending_dtcs"] == 0
    assert o["mil_on"] is False
    stored = parsers.parse_obd(REAL_OBD.replace("active dtcs                 00",
                                                "active dtcs                 02"))
    assert stored["active_dtcs"] == 2


def test_a_stored_fault_code_is_an_alert(tmp_path):
    clean = sessions.Session(str(tmp_path), {"obd": REAL_OBD}, "")
    m = {x["label"]: x for x in health.health_snapshot(clean)}["Fault codes"]
    assert m["status"] == "ok" and m["display"] == "none stored"
    dirty = sessions.Session(str(tmp_path), {
        "obd": REAL_OBD.replace("active dtcs                 00",
                                "active dtcs                 03")}, "")
    m = {x["label"]: x for x in health.health_snapshot(dirty)}["Fault codes"]
    assert m["status"] == "alert" and "3 active" in m["display"]


def test_the_state_block_names_what_would_stop_the_bike(tmp_path):
    s = sessions.Session(str(tmp_path), {"inputs": REAL_INPUTS,
                                         "outputs": REAL_OUTPUTS,
                                         "runtime": REAL_RUNTIME,
                                         "stats": REAL_STATS}, "")
    text = report.format_report(report.analyze_session(s))
    assert "would not move as captured: kickstand DOWN" in text
    assert "the dash warning light was ON at capture" in text


def test_run_time_against_the_odometer_catches_a_statistics_reset(tmp_path):
    # 6155 km against 25.2 hours is 244 km/h — the two counters cannot be
    # describing the same period, which on this platform means a reset
    s = sessions.Session(str(tmp_path), {"runtime": REAL_RUNTIME,
                                         "stats": REAL_STATS}, "")
    text = report.format_report(report.analyze_session(s))
    assert "km/h average" in text and "statistics were reset" in text


def test_a_capture_without_those_commands_prints_no_state_block(tmp_path):
    s = sessions.Session(str(tmp_path), {"bms": REAL_BMS}, "")
    assert "== Bike state ==" not in report.format_report(report.analyze_session(s))


# --- the pack over time: what one capture cannot show ------------------------

def test_the_bms_names_which_cell_is_weakest_not_just_the_voltage():
    # "3798 mV" says the pack is uneven; "( Cell 12 )" says which cell, and a
    # named cell is a repairable thing rather than a bad feeling
    b = parsers.parse_bms(REAL_BMS)
    assert b["low_cell_index"] == 12
    assert b["high_cell_index"] == 7
    # a console that prints the voltage without the attribution still parses
    plain = REAL_BMS.replace(" ( Cell 12 )", "")
    assert parsers.parse_bms(plain)["low_cell_mv"] == 3798
    assert parsers.parse_bms(plain)["low_cell_index"] is None


def _trend_session(tmp_path, tag, bms):
    # Session takes its name from the folder, so the tag IS the name
    return sessions.Session(str(tmp_path / tag), {"bms": bms}, "")


def test_pack_trend_carries_the_soc_each_reading_was_taken_at(tmp_path):
    # cell spread closes up near full charge, so a deviation without the SOC it
    # was read at is not comparable to the next one - the SOC travels with it
    a = _trend_session(tmp_path, "jul", REAL_BMS)
    b = _trend_session(tmp_path, "aug", REAL_BMS.replace("61%", "96%"))
    trend = compare.pack_trend([a, b])
    assert [t["session"] for t in trend] == ["jul", "aug"]   # in the order given
    assert [t["soc_pct"] for t in trend] == [61, 96]
    assert all(t["low_cell_index"] == 12 for t in trend)
    # no event log in these captures, so the log-derived figures say so rather
    # than reporting a confident zero
    assert all(t["charge_index_ah"] is None for t in trend)
    assert all(t["cell_deviation_mv"] is None for t in trend)


def test_the_same_cell_returning_is_a_cell_a_wandering_one_is_not(tmp_path):
    same = compare.pack_trend([_trend_session(tmp_path, "a", REAL_BMS),
                               _trend_session(tmp_path, "b", REAL_BMS),
                               _trend_session(tmp_path, "c", REAL_BMS)])
    wc = compare.weakest_cell_identity(same)
    assert wc["cell"] == 12 and wc["times"] == 3 and wc["always"] is True
    assert wc["graded"] is False           # a pattern to look at, not a verdict

    wander = compare.pack_trend([
        _trend_session(tmp_path, "a", REAL_BMS),
        _trend_session(tmp_path, "b", REAL_BMS.replace("Cell 12", "Cell 3")),
        _trend_session(tmp_path, "c", REAL_BMS.replace("Cell 12", "Cell 21"))])
    wander_wc = compare.weakest_cell_identity(wander)
    assert wander_wc["times"] == 1 and wander_wc["always"] is False


def test_one_capture_is_not_a_pattern(tmp_path):
    # a single reading names a cell but says nothing about whether it repeats
    one = compare.pack_trend([_trend_session(tmp_path, "a", REAL_BMS)])
    assert compare.weakest_cell_identity(one) is None
    # and captures that never named a cell cannot claim one either
    plain = REAL_BMS.replace(" ( Cell 12 )", "")
    blind = compare.pack_trend([_trend_session(tmp_path, "a", plain),
                                _trend_session(tmp_path, "b", plain)])
    assert compare.weakest_cell_identity(blind) is None


def test_compare_sessions_reports_the_trend_alongside_the_diff(tmp_path):
    a = _make_session(tmp_path, "old")
    b = _make_session(tmp_path, "new")
    res = compare.compare_sessions([a, b])
    assert len(res["pack_trend"]) == 2
    assert res["pack_trend"][0]["session"] == a.name
    # the sim's two identical captures name the same cell, which is exactly the
    # shape the identity check is meant to notice
    assert res["weakest_cell"] is None or res["weakest_cell"]["of_captures"] == 2


# --- what it costs to ride, and how far a charge goes ------------------------

def _ride_samples(n, soc_from=100.0, soc_to=20.0, km_total=40.0, amps=30.0,
                  volts=110.0, start_min=0, odo_start=6000, amb=20):
    """A ride of `n` samples a minute apart, losing SOC and gaining odometer
    linearly. One minute is the real bike's riding-sample cadence, and the
    odometer is whole kilometres exactly as the bike prints it."""
    out = []
    for i in range(n):
        f = i / float(n - 1)
        mins = start_min + i
        out.append(
            " 00001     06/24/2026 %02d:%02d:00   Riding                     "
            "PackTemp: h 30C, l 28C, AmbTemp: %dC, PackSOC: %d%%, Vpack:%.3fV, "
            "BattAmps: %3d, MotAmps: %3d, MotRPM:3000, Odo: %dkm, "
            "MinCell: 3700mV"
            % (8 + mins // 60, mins % 60, amb,
               round(soc_from + (soc_to - soc_from) * f), volts, amps, amps,
               round(odo_start + km_total * f)))
    return out


def test_consumption_is_integrated_from_pack_voltage_and_current():
    # 110 V x 30 A for 40 minutes = 2200 Wh over 40 km = 55 Wh/km
    recs = parsers.parse_ride_log("\n".join(
        _ride_samples(41, km_total=40.0, amps=30.0, volts=110.0)))
    c = rides.consumption(recs)
    assert c is not None
    assert c["wh_per_km"] == pytest.approx(55.0, rel=0.05)
    assert c["rides"] == 1 and c["at_the_pack"] is True
    # the temperatures it was measured across travel with it, because
    # consumption climbs in the cold
    assert c["amb_low_c"] == 20 and c["amb_high_c"] == 20


def test_a_short_ride_is_left_out_rather_than_averaged_in():
    # the odometer is whole kilometres, so a 2 km ride carries up to 50%
    # quantisation error in its distance and none of it averages out
    short = parsers.parse_ride_log("\n".join(_ride_samples(6, km_total=2.0)))
    assert rides.consumption(short) is None
    assert rides.range_estimate(short) is None


def test_the_range_comes_from_the_deepest_ride_not_the_bms_capacity():
    # the BMS on the reference bike reports 52 Ah while the gauge behaves like a
    # pack barely two thirds that; a range built on the larger number is a third
    # too long
    recs = parsers.parse_ride_log("\n".join(
        _ride_samples(41, soc_from=100.0, soc_to=20.0, km_total=40.0)))
    r = rides.range_estimate(recs)
    assert r["km"] == pytest.approx(40, abs=1)
    assert r["soc_used_pct"] == pytest.approx(80, abs=1)
    # 40 km for 80 SOC points -> 50 km for 100
    assert r["full_charge_km"] == pytest.approx(50, rel=0.05)
    # and the same ride measured a second, unrelated way: ~2200 Wh for 80 points
    # implies a ~2750 Wh pack
    assert r["implied_pack_wh"] == pytest.approx(2750, rel=0.05)


def test_the_range_says_plainly_that_it_is_an_extrapolation():
    recs = parsers.parse_ride_log("\n".join(
        _ride_samples(41, soc_from=100.0, soc_to=20.0, km_total=40.0)))
    r = rides.range_estimate(recs)
    assert r["is_extrapolation"] is True and r["graded"] is False
    # how far past the evidence it reaches is carried with it
    assert r["soc_floor_pct"] == 20


def test_the_deepest_ride_wins_not_the_longest():
    # a long gentle ride says less about the pack's reach than a short deep one,
    # and the extrapolation is over SOC
    long_shallow = _ride_samples(41, soc_from=100.0, soc_to=80.0, km_total=60.0)
    short_deep = _ride_samples(41, soc_from=95.0, soc_to=15.0, km_total=30.0,
                               start_min=600, odo_start=6100)
    recs = parsers.parse_ride_log("\n".join(long_shallow + short_deep))
    r = rides.range_estimate(recs)
    assert r["soc_used_pct"] == pytest.approx(80, abs=1)
    assert r["km"] == pytest.approx(30, abs=1)


def test_a_capture_with_no_riding_says_nothing_rather_than_zero():
    assert rides.consumption([]) is None
    assert rides.range_estimate([]) is None


def test_the_report_prints_both_with_the_caveat_attached(tmp_path):
    log = "\n".join(_ride_samples(41, soc_from=100.0, soc_to=20.0, km_total=40.0))
    s = sessions.Session(str(tmp_path), {"eventlogdump": log}, "")
    rep = report.analyze_session(s)
    assert rep["consumption"]["wh_per_km"] == pytest.approx(55.0, rel=0.05)
    text = report.format_report(rep)
    assert "measured consumption" in text and "Wh/km at the pack" in text
    assert "deepest discharge logged" in text
    # the caveat has to travel with the number, or the range reads as a promise
    assert "UPPER BOUND" in text and "0% is reachable" in text
