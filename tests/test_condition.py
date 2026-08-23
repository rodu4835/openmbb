"""Condition checks — the gauge-independent half.

No display and no hardware needed. Every case here is built from the shapes the
real 2017 FXS actually printed, not from the simulator, because the whole point
of this module is what it says about a bike nobody has a baseline for.
"""

import os

import pytest

from openmbb import condition, library, parsers, report, rides, sessions

# The real reset sequence: the bootloader line carries a clock, the reset entry
# logged immediately after it does not (the console had just rebooted).
RESET_LOG = """
 08026     06/13/2026 06:50:11   Riding      PackSOC: 61%, Odo: 6400km
 08027     06/13/2026 06:52:42   Entering Bootloader by request
 08028                       0   Stats Read Failed, Resetting All Stats to Defaults
 08029     06/13/2026 07:10:03   Riding      PackSOC: 61%, Odo: 6400km
"""

RIDE = """
 00001  06/24/2026 21:59:31  Riding  PackTemp: h 27C, PackSOC:100%, Vpack:115.1V, BattAmps:   9, AmbTemp: 18C, MotRPM:1109, Odo: 6404km, Curr limit: 520 A (100%), MinCell: 4024mV
 00002  06/24/2026 22:05:31  Riding  PackTemp: h 30C, PackSOC: 86%, Vpack:110.0V, BattAmps:  99, AmbTemp: 18C, MotRPM:3423, Odo: 6410km, Curr limit: 470 A (90%), MinCell: 3600mV
 00003  06/24/2026 22:08:31  Riding  PackTemp: h 40C, PackSOC: 64%, Vpack:106.0V, BattAmps: 171, AmbTemp: 18C, MotRPM:5057, Odo: 6416km, Curr limit: 260 A (50%), MinCell: 3300mV
"""


def test_stats_reset_is_found_and_dated_from_the_bootloader_line():
    # the reset entry itself has no clock, so it inherits the bootloader's
    events = condition.stats_reset_events(RESET_LOG)
    assert len(events) == 1
    assert events[0]["when"] == "06/13/2026 06:52:42"
    assert events[0]["bootloader"] == "06/13/2026 06:52:42"
    assert "Resetting All Stats" in events[0]["line"]


def test_no_reset_in_a_log_that_has_none():
    assert condition.stats_reset_events(RIDE) == []
    assert condition.stats_reset_events("") == []
    assert condition.stats_reset_events(None) == []


def test_log_coverage_bounds_every_other_answer():
    # a reset absent from a window that does not reach back far enough is not
    # evidence of no reset, so the window travels with the finding
    recs = parsers.parse_ride_log(RIDE)
    assert condition.log_coverage(recs) == ("06/24/2026 21:59:31",
                                            "06/24/2026 22:08:31")
    assert condition.log_coverage([]) == (None, None)


def test_cell_sag_reads_the_worst_cell_under_load_only():
    recs = parsers.parse_ride_log(RIDE)
    sag = condition.cell_sag(recs)
    # the 4024 mV sample sits at 9 A — not under load, so it is not the answer
    assert sag["min_cell_mv"] == 3300
    assert sag["at_amps"] == 171
    assert sag["at_soc_pct"] == 64
    assert sag["loaded_samples"] == 2
    assert sag["graded"] is False        # uncalibrated until a second bike


def test_cell_sag_is_none_when_nothing_was_under_load():
    idle = parsers.parse_ride_log(
        " 1 06/24/2026 21:59:31 Riding PackSOC:100%, BattAmps: 2, "
        "Odo: 6404km, MinCell: 4024mV")
    assert condition.cell_sag(idle) is None


def test_derate_profile_describes_rather_than_grades():
    recs = parsers.parse_ride_log(RIDE)
    d = condition.derate_profile(recs)
    assert d["samples"] == 3
    assert d["worst_pct"] == 50 and d["median_pct"] == 90
    assert d["worst_at_pack_temp_c"] == 40 and d["worst_at_soc_pct"] == 64
    assert d["graded"] is False
    # a healthy reference bike sat below 100% on most samples, so a count of
    # held-back samples would fail a good pack — hence buckets, not a verdict
    assert sum(d["buckets"].values()) == 3


def test_derate_and_sag_degrade_to_none_on_a_dialect_without_them():
    old = parsers.parse_ride_log(
        " 05/16/2026 08:12:33 Riding PackTemp: 24C, PackSOC: 61%, "
        "Vpack:106.412V, MotAmps: 100, MotRPM:3100, Odo:6120km")
    assert condition.derate_profile(old) is None
    assert condition.cell_sag(old) is None


# --- charge side: the one measurement that ignores the SOC display -----------

def _charge_log(start_v=100.0, step_v=1.0, n=19, amps=-5, step_s=600):
    """A synthetic charge session: samples `step_s` apart, pack voltage rising."""
    import datetime as dt
    t = dt.datetime(2026, 6, 24, 22, 0, 0)
    out = []
    for i in range(n):
        out.append(" %05d     %s   Charging   PackTemp: h 27C, AmbTemp: 16C, "
                   "PackSOC: %d%%, Vpack:%.3fV, BattAmps: %4d, Mods: 01"
                   % (i + 1, (t + dt.timedelta(seconds=i * step_s)).strftime(
                       "%m/%d/%Y %H:%M:%S"), 20 + i * 4, start_v + i * step_v, amps))
    return "\n".join(out)


def test_charge_log_is_parsed_separately_from_riding():
    c = parsers.parse_charge_log(_charge_log())
    assert len(c) == 19
    assert c[0]["vpack"] == 100.0 and c[0]["battamps"] == -5
    assert c[0]["odo_km"] is None            # a charging line carries no odometer
    # and the riding parser must not pick these up
    assert parsers.parse_ride_log(_charge_log()) == []


def test_charge_capacity_integrates_between_fixed_voltages():
    # 1 V per 600 s at 5 A: each interval is 5 * 600/3600 = 0.8333 Ah, and the
    # 103-113 V window contains 10 intervals with both endpoints inside it
    cap = condition.charge_capacity(parsers.parse_charge_log(_charge_log()))
    assert cap["sessions"] == 1
    assert cap["median_ah"] == round(10 * 5 * 600 / 3600.0, 2)     # 8.33 Ah
    assert cap["window_v"] == [103.0, 113.0]
    assert cap["gauge_independent"] is True


def test_charge_capacity_is_none_when_the_window_is_never_crossed():
    # a session that stops at 108 V never traverses 103-113, so there is no
    # measurement - and that must read as "could not determine", not as zero
    assert condition.charge_capacity(
        parsers.parse_charge_log(_charge_log(start_v=100.0, n=9))) is None
    assert condition.charge_capacity([]) is None


# --- the assessment ----------------------------------------------------------

def test_assess_reports_a_reset_it_found():
    a = condition.assess(RESET_LOG + RIDE + _charge_log())
    assert a["stats_resets"][0]["when"] == "06/13/2026 06:52:42"
    # having found one, it must not also claim it could not determine this
    assert not any("ever reset" in u for u in a["undetermined"])


def test_assess_names_every_check_it_could_not_answer():
    a = condition.assess("")
    assert a["coverage"]["ride_samples"] == 0
    joined = " ".join(a["undetermined"])
    assert "no riding samples" in joined
    assert "charge capacity" in joined
    assert "ever reset" in joined            # silence is not a pass
    assert a["cell_sag"] is None and a["derate"] is None


def test_assess_qualifies_a_missing_reset_with_the_log_window():
    # the August capture reaches back only to after that bike's own update, so
    # "no reset found" there is meaningless without the window beside it
    a = condition.assess(RIDE + _charge_log())
    assert a["stats_resets"] == []
    reason = [u for u in a["undetermined"] if "ever reset" in u][0]
    assert "06/24/2026 21:59:31" in reason


# --- records written by an older firmware -----------------------------------

# The real shape: rev 41 re-reads a shorter rev-12 record with its own layout
# and runs off the end, so the two trailing fields are stale bytes. 8241 mV is
# 0x2031, the ASCII " 1". Measured at 502 of 502 pre-update ride records.
STALE_RECORD = (" 04101  06/01/2026 02:16:49  Riding  PackTemp: h 26C, "
                "PackSOC: 74%, Vpack:115.177V, BattAmps: 120, MotRPM:3300, "
                "Odo: 5399km, Curr limit: 8295 A (31%), MinCell: 8241mV")


def test_a_fabricated_cell_voltage_is_refused():
    r = parsers.parse_ride_log(STALE_RECORD)[0]
    assert r["mincell_mv"] is None
    # the real fields on the same line still decode
    assert r["soc"] == 74 and r["odo_km"] == 5399 and r["battamps"] == 120


def test_the_whole_trailing_pair_goes_not_just_the_impossible_one():
    # the fabricated Curr limit PERCENTAGE lands inside 0-100 (observed: 0, 30,
    # 31, 90, 91), so it cannot be caught by range-checking itself. Both fields
    # sit at the end of the record, so one being impossible condemns the pair.
    r = parsers.parse_ride_log(STALE_RECORD)[0]
    assert r["curr_limit_pct"] is None


def test_the_guard_is_two_sided():
    # the fabrications seen here run high, but a rev-12 decode of the same era
    # contains a 66 mV MinCell, so a one-sided guard would let that through
    low = STALE_RECORD.replace("MinCell: 8241mV", "MinCell: 66mV")
    assert parsers.parse_ride_log(low)[0]["mincell_mv"] is None
    good = STALE_RECORD.replace("MinCell: 8241mV", "MinCell: 3411mV")
    r = parsers.parse_ride_log(good)[0]
    assert r["mincell_mv"] == 3411 and r["curr_limit_pct"] == 31


def test_a_wholly_stale_log_says_so_instead_of_reading_as_healthy():
    # The used-bike case that makes this matter: the seller reflashed the MBB,
    # so every retained ride record predates the flash. Unguarded, cell_sag
    # returned 8241 mV - a pack whose weakest cell never sags - which is the
    # most expensive wrong answer this tool could give.
    a = condition.assess("\n".join([STALE_RECORD] * 5))
    assert a["coverage"]["ride_samples"] == 5
    assert a["coverage"]["ride_samples_with_cell"] == 0
    assert a["cell_sag"] is None
    assert a["derate"] is None
    reason = [u for u in a["undetermined"] if "weakest cell" in u][0]
    assert "NOT ONE" in reason and "firmware" in reason


# --- the legacy weakest-cell channel ----------------------------------------

LIMIT_LOG = """
 00124  05/28/2026 02:41:23  Batt Dischg Cur Limited    379 A (72%), MinCell: 3567mV, MaxPackTemp: 49C
 00312  05/28/2026 02:43:02  Batt Dischg Cur Limited    383 A (73%), MinCell: 3214mV, MaxPackTemp: 38C
 00424  05/28/2026 12:05:59  Batt Dischg Cur Limited    483 A (92%), MinCell: 3492mV, MaxPackTemp: 40C
"""


def test_limit_events_carry_a_cell_voltage_of_their_own():
    ev = parsers.parse_limit_events(LIMIT_LOG)
    assert len(ev) == 3
    assert ev[0]["kind"] == "discharge"
    assert ev[0]["limit_amps"] == 379 and ev[0]["limit_pct"] == 72
    assert ev[0]["mincell_mv"] == 3567 and ev[0]["pack_temp_c"] == 49


def test_limit_events_get_the_same_plausibility_guard():
    bad = LIMIT_LOG.replace("MinCell: 3567mV", "MinCell: 8241mV")
    assert len(parsers.parse_limit_events(bad)) == 2      # the fabricated one drops


def test_cell_floor_prefers_the_riding_channel_when_it_has_one():
    rides = parsers.parse_ride_log(RIDE)
    floor = condition.cell_floor(rides, parsers.parse_limit_events(LIMIT_LOG))
    assert floor["source"] == "riding samples"
    assert floor["min_cell_mv"] == 3300         # from RIDE, not the 3214 above


def test_cell_floor_falls_back_when_the_riding_records_are_undecodable():
    # The reflashed-bike case: every riding record predates the flash and its
    # trailing fields are stale bytes, so the modern channel is silent. The
    # current-limit events survive, and they hold the worst reading of all.
    stale = "\n".join([STALE_RECORD] * 4) + LIMIT_LOG
    a = condition.assess(stale)
    assert a["coverage"]["ride_samples_with_cell"] == 0
    assert a["cell_sag"] is None                 # riding channel correctly dead
    assert a["cell_floor"]["source"] == "discharge-limit events"
    assert a["cell_floor"]["min_cell_mv"] == 3214
    assert a["cell_floor"]["samples"] == 3
    # and with the floor recovered it must NOT claim the cells are unmeasured
    assert not any("weakest cell" in u for u in a["undetermined"])


def test_cell_floor_is_none_when_neither_channel_has_anything():
    assert condition.cell_floor([], []) is None


# --- the verdict -------------------------------------------------------------

def _loaded_log(mincell_mv, vpack=112.0, n=40, amps=120):
    """n loaded riding samples with a given weakest cell."""
    import datetime as dt
    t = dt.datetime(2026, 6, 24, 22, 0, 0)
    return "\n".join(
        " %05d  %s  Riding  PackTemp: h 30C, PackSOC: %d%%, Vpack:%.3fV, "
        "BattAmps: %d, Odo: %dkm, Curr limit: 520 A (100%%), MinCell: %dmV"
        % (i + 1, (t + dt.timedelta(seconds=i * 60)).strftime("%m/%d/%Y %H:%M:%S"),
           90 - i, vpack, amps, 6400 + i, mincell_mv)
        for i in range(n))


def test_a_matched_pack_under_load_reads_ok():
    # 112 V / 28 = 4000 mV average; a cell at 3940 is 60 mV down, in line with
    # the one healthy pack measured (64-67 mV median)
    v = condition.verdict(condition.assess(_loaded_log(3940)))
    assert v["level"] == "ok"
    assert v["confidence"] in ("partial", "full")
    assert "went unanswered" in v["headline"] or "looks wrong" in v["headline"]


def test_a_cell_far_below_its_siblings_is_a_concern():
    # 4000 mV average, this cell at 3700 = 300 mV down, past CELL_DEV_WATCH_MV
    v = condition.verdict(condition.assess(_loaded_log(3700)))
    assert v["level"] == "concern"
    assert "Walk away" in v["headline"]
    dev = [c for c in v["checks"] if c["name"] == "Weakest cell vs pack"][0]
    assert dev["level"] == "concern"


def test_a_middling_deviation_is_worth_a_look():
    v = condition.verdict(condition.assess(_loaded_log(3850)))   # 150 mV down
    assert v["level"] == "watch"
    assert "closer look" in v["headline"]


def test_an_absolutely_low_cell_is_a_concern_whatever_the_spread():
    # every cell low together: deviation stays small, but 2700 mV under load is
    # past the chemistry floor and must be caught by the absolute check
    v = condition.verdict(condition.assess(_loaded_log(2700, vpack=76.0)))
    floor = [c for c in v["checks"] if c["name"] == "Lowest cell under load"][0]
    assert floor["level"] == "concern"
    assert v["level"] == "concern"


def test_a_capture_with_no_evidence_says_cannot_tell():
    v = condition.verdict(condition.assess(""))
    assert v["level"] == "unknown"
    assert v["confidence"] == "none"
    assert "Cannot tell" in v["headline"]
    assert all(c["level"] == "unknown" for c in v["checks"])


def test_a_gently_ridden_capture_cannot_answer_the_cell_questions():
    # the seller charges it and lets it sit: samples exist, none under load
    v = condition.verdict(condition.assess(_loaded_log(4020, amps=3)))
    assert v["level"] == "unknown"
    assert "Cannot tell" in v["headline"]


def test_a_partial_answer_says_so_in_the_headline_not_just_a_field():
    # the reflashed-bike shape: riding records undecodable, but the legacy
    # discharge-limit channel still carries a cell voltage
    a = condition.assess("\n".join([STALE_RECORD] * 4) + LIMIT_LOG)
    v = condition.verdict(a)
    assert v["level"] == "ok"
    assert v["confidence"] == "partial"
    # the count must be IN the headline - a buyer does not read a side field
    assert "unanswered" in v["headline"]


def test_the_worst_check_decides_the_verdict():
    a = condition.assess(_loaded_log(3940))          # ok on its own
    metrics = [{"label": "Cell spread", "value": 120.0, "display": "120 mV",
                "status": "alert"},
               {"label": "Displayed SOC", "value": 50, "display": "50 %",
                "status": "info"}]
    v = condition.verdict(a, metrics)
    assert v["level"] == "concern"


def test_a_stats_reset_becomes_a_caveat_on_the_verdict():
    v = condition.verdict(condition.assess(RESET_LOG + _loaded_log(3940)))
    assert any("statistics were reset" in c.lower() for c in v["caveats"])


# --- clocks ------------------------------------------------------------------

def _clock_session(mbb, bms_line, captured, dash="11:04"):
    return sessions.Session("x", {
        "stats": "  - System Time               :  %s" % mbb,
        "bms": "  - BMS Clock                 :  %s" % bms_line,
        "dash": "  - Clock (24H)               :  %s" % dash,
    }, "", {"stats": captured})


def test_the_bike_clock_offset_is_measured_not_configured():
    # Both sides of one instant are on disk: the bike prints its clock in
    # `stats`, and the capture records the machine's clock beside it. No
    # timezone database and no user setting are involved.
    s = _clock_session("08/19/2026 15:58:01",
                       "08/19/2026 15:58:03 ( 1787180283, 0x6A8634FB )",
                       "2026-08-19 16:05:13")
    c = condition.clock_check(s)
    assert c["offset_s"] == 432                      # 7 m 12 s behind
    assert "7 m behind" in condition.describe_offset(c["offset_s"])
    assert c["worth_correcting"] is False            # drift, not an offset


def test_a_bike_hours_out_is_flagged_and_correctable():
    # The reported Czech case: ride data 7 h behind local while the BMS clock
    # reads 9 h behind, because the two counters were set differently.
    s = _clock_session("08/10/2026 09:18:20",
                       "08/10/2026 07:17:04 ( 1786371424, 0x6A79DD60 )",
                       "2026-08-10 16:18:22")
    c = condition.clock_check(s)
    assert c["worth_correcting"] is True
    assert "7 h" in condition.describe_offset(c["offset_s"])
    # the MBB and BMS counters are two hours apart - that is the 7-versus-9
    assert round(c["mbb_vs_bms_s"] / 3600.0) == 2
    # and an event-log timestamp shifts into the capture's local time
    assert condition.shift_timestamp("08/10/2026 09:00:00",
                                     c["offset_s"]) == "08/10/2026 16:00:02"


def test_the_console_renders_its_stored_counter_seven_hours_back():
    # Measured identically on two bikes in different countries, so this is a
    # firmware constant rather than anything set per bike.
    for bms_line in ("08/19/2026 15:58:03 ( 1787180283, 0x6A8634FB )",
                     "08/10/2026 07:17:04 ( 1786371424, 0x6A79DD60 )"):
        c = condition.clock_check(_clock_session("01/01/2026 00:00:00", bms_line,
                                                 "2026-01-01 00:00:00"))
        assert c["console_renders_epoch_at_h"] == -7


def test_a_capture_missing_either_side_says_unknown():
    # no capture-time header -> the offset is not knowable, and must not be zero
    s = _clock_session("08/19/2026 15:58:01",
                       "08/19/2026 15:58:03 ( 1787180283, 0x6A8634FB )", None)
    c = condition.clock_check(s)
    assert c["offset_s"] is None and c["worth_correcting"] is False
    assert condition.describe_offset(None) == "unknown"
    # and a timestamp with no known offset is returned untouched
    assert condition.shift_timestamp("08/19/2026 12:00:00", None) == "08/19/2026 12:00:00"


def test_clock_check_survives_a_session_with_no_clocks_at_all():
    c = condition.clock_check(sessions.Session("x", {}, ""))
    assert c["mbb_clock"] is None and c["offset_s"] is None


# --- how the bike is charged -------------------------------------------------

def _chg_line(ts, event):
    return " 00001     %s   %s" % (ts, event)


def _chg_sample(ts, soc, v, amps, temp=30):
    """One charging sample. Keep them under an hour apart: the real bike writes
    one about every ten minutes, and a wider spacing splits what should be a
    single charge into two sessions."""
    return ("00001     %s   Charging                   PackTemp: h %dC, l %dC, "
            "AmbTemp: 20C, PackSOC: %d%%, Vpack:%.3fV, BattAmps: %4d, Mods: 10, "
            "MbbChgEn" % (ts, temp, temp - 2, soc, v, amps))


PLUG_IN = "Calex 720W Charger 0 Connected"
UNPLUG = "Calex 720W Charger 0 Disconnected"
STANDBY = "Entering Charge Standby Mode"


def test_a_spell_at_full_runs_from_charge_complete_to_the_unplug():
    # the measurement the charging SAMPLES cannot make: the bike stops writing
    # them when the charge finishes, so a pack that then sat plugged in for
    # three days leaves no samples at all
    log = "\n".join([
        _chg_line("07/01/2026 20:00:00", PLUG_IN),
        _chg_sample("07/01/2026 20:10:00", 40, 104.0, -6),
        _chg_sample("07/01/2026 20:40:00", 70, 110.0, -6),
        _chg_sample("07/01/2026 21:10:00", 100, 116.0, -5),
        _chg_line("07/01/2026 23:05:00", STANDBY),
        _chg_line("07/04/2026 05:05:00", UNPLUG),
    ])
    holds = condition.full_charge_holds(log)
    assert len(holds) == 1
    _start, secs = holds[0]
    assert secs == pytest.approx(54 * 3600, abs=60)      # 07/01 23:05 -> 07/04 05:05


def test_a_ride_ends_a_spell_even_with_no_unplug_recorded():
    # measured on the reference bike: a standby at 07/09 13:23 was followed by a
    # ride at 15:02 with no disconnect line between them, and the next recorded
    # disconnect was three days later. Bounded only by disconnects, that reads as
    # 91 hours at full for a bike that had been out riding.
    log = "\n".join([
        _chg_line("07/09/2026 10:00:00", PLUG_IN),
        _chg_line("07/09/2026 13:23:45", STANDBY),
        " 00001     07/09/2026 15:02:29   Riding                     PackTemp: h 38C, "
        "l 35C, PackSOC:100%, Vpack:115.186V, MotAmps: 40, MotRPM:2000, Odo:100km",
        _chg_line("07/13/2026 08:37:32", UNPLUG),
    ])
    holds = condition.full_charge_holds(log)
    assert len(holds) == 1
    assert holds[0][1] == pytest.approx(98.7 * 60, abs=60)   # 13:23:45 -> 15:02:29


def test_a_spell_still_open_at_the_end_of_the_log_is_dropped():
    # the log is a rolling buffer, so its end is arbitrary; an unbounded spell is
    # not a measurement, and the total must run under the truth rather than over
    log = "\n".join([
        _chg_line("07/01/2026 20:00:00", PLUG_IN),
        _chg_line("07/01/2026 23:05:00", STANDBY),
    ])
    assert condition.full_charge_holds(log) == []
    # ...and so is one where a fresh plug-in appears with no unplug before it
    log2 = log + "\n" + _chg_line("07/02/2026 08:00:00", PLUG_IN)
    assert condition.full_charge_holds(log2) == []


def test_topping_up_at_full_does_not_split_the_spell():
    # leaving and re-entering standby is the charger topping the pack up while it
    # sits at full; the whole stretch is time at full, not two short spells
    log = "\n".join([
        _chg_line("07/01/2026 23:05:00", STANDBY),
        _chg_line("07/02/2026 02:00:00", "Leaving Charge Standby Mode"),
        _chg_line("07/02/2026 02:20:00", STANDBY),
        _chg_line("07/02/2026 07:05:00", UNPLUG),
    ])
    holds = condition.full_charge_holds(log)
    assert len(holds) == 1
    assert holds[0][1] == pytest.approx(8 * 3600, abs=60)


def test_charge_behaviour_reports_habits_and_grades_none_of_them():
    log = "\n".join([
        _chg_line("07/01/2026 20:00:00", PLUG_IN),
        _chg_sample("07/01/2026 20:10:00", 30, 103.0, -6, temp=50),
        _chg_sample("07/01/2026 20:40:00", 80, 110.0, -5, temp=40),
        _chg_sample("07/01/2026 21:10:00", 100, 116.0, -5, temp=35),
        _chg_line("07/01/2026 23:05:00", STANDBY),
        _chg_line("07/02/2026 07:05:00", UNPLUG),
    ])
    ch = condition.charge_behaviour(log)
    assert ch["sessions"] == 1
    assert ch["start_soc_median"] == 30 and ch["start_soc_min"] == 30
    assert ch["hot_plugins"] == 1          # pack was at 50 C when plugged in
    assert ch["peak_amps_max"] == 6
    assert ch["held_full_h"] == 8.0 and ch["holds"] == 1
    # habits, not faults: nothing here carries a level, and the taper is not
    # resolvable at ~10-minute, whole-amp sampling
    assert ch["graded"] is False and ch["taper_resolvable"] is False
    assert "level" not in ch


def test_a_cool_plug_in_is_not_counted_as_a_hot_one():
    log = "\n".join([
        _chg_line("07/01/2026 20:00:00", PLUG_IN),
        _chg_sample("07/01/2026 20:10:00", 30, 103.0, -6, temp=25),
        _chg_sample("07/01/2026 20:40:00", 100, 116.0, -5, temp=30),
    ])
    assert condition.charge_behaviour(log)["hot_plugins"] == 0


def test_a_capture_with_no_charging_says_nothing_rather_than_zero():
    assert condition.charge_behaviour("") is None
    assert condition.charge_behaviour(RIDE) is None


def test_the_report_prints_the_charging_block_and_its_caveat(tmp_path):
    log = "\n".join([
        _chg_line("07/01/2026 20:00:00", PLUG_IN),
        _chg_sample("07/01/2026 20:10:00", 30, 103.0, -6, temp=50),
        _chg_sample("07/01/2026 20:40:00", 100, 116.0, -5, temp=35),
        _chg_line("07/01/2026 23:05:00", STANDBY),
        _chg_line("07/02/2026 07:05:00", UNPLUG),
    ])
    s = sessions.Session(str(tmp_path), {"eventlogdump": log}, "")
    text = report.format_report(report.analyze_session(s))
    assert "charging (habits, not graded)" in text
    assert "sat at FULL with the charger still attached: 8 h" in text
    assert "plugged in with the pack still hot" in text
    # the caveat has to travel with the number, or a reader assumes a taper was
    # looked for and found healthy
    assert "the taper is NOT visible here" in text


# --- where the hottest reading came from -------------------------------------
#
# The sensor called AmbTemp is on the bike, not in the air. Measured on the
# reference bike: a median 29 C between midnight and 06:00 while charging, where
# the SAME sensor reads 16 C riding at those same hours, and reading at or above
# the pack in 107 of 1424 charging samples. So an ambient may only ever appear
# beside a pack temperature off the same log line, and never as an aggregate.

def _hot(ts, pack, amb=None, soc=60):
    amb_bit = "" if amb is None else "AmbTemp: %dC, " % amb
    return (" 00001     %s   Riding                     PackTemp: h %dC, l %dC, "
            "%sPackSOC: %d%%, Vpack:110.000V, BattAmps:  30, MotAmps:  30, "
            "MotRPM:3000, Odo: 6000km, MinCell: 3700mV"
            % (ts, pack, pack - 2, amb_bit, soc))


def test_the_ambient_comes_from_the_same_line_as_the_peak():
    # a neighbouring sample's ambient is a different moment, and on this sensor
    # possibly a different physical quantity
    recs = parsers.parse_ride_log("\n".join([
        _hot("06/24/2026 08:00:00", 40, amb=40),      # cooler pack, hot "ambient"
        _hot("06/24/2026 08:01:00", 59, amb=19),      # THE peak
        _hot("06/24/2026 08:02:00", 41, amb=41),
    ]))
    pk = condition.pack_peak(recs)
    assert pk["pack_temp_c"] == 59
    assert pk["amb_low_c"] == 19 and pk["amb_high_c"] == 19
    assert pk["ties"] == 1 and pk["graded"] is False


def test_every_sample_tying_the_peak_is_reported_not_just_the_first():
    # the reference bike ties its log maximum on six samples at three different
    # ambients: a candidate set, not a value
    recs = parsers.parse_ride_log("\n".join([
        _hot("06/24/2026 08:00:00", 59, amb=19),
        _hot("06/24/2026 08:01:00", 59, amb=23),
        _hot("06/24/2026 08:02:00", 59, amb=20),
        _hot("06/24/2026 08:03:00", 50, amb=30),
    ]))
    pk = condition.pack_peak(recs)
    assert pk["ties"] == 3
    assert pk["amb_low_c"] == 19 and pk["amb_high_c"] == 23


def test_a_firmware_with_no_pack_temperature_yields_no_peak():
    # the BattTemp: dialect - the honest answer is nothing, not a substitute
    recs = [{"soc": 60, "amb_temp_c": 20, "pack_temp_c": None, "ts": "x"}]
    assert condition.pack_peak(recs) is None
    assert condition.pack_peak([]) is None


def test_the_lifetime_counter_and_the_log_disagree_in_both_directions():
    # measured on the reference bike: 2026-08-19 reports 60 C where the log's
    # hottest sample is 59 C, and 2026-07-10 reports 59 C where the log holds 60
    recs = parsers.parse_ride_log(_hot("06/24/2026 08:00:00", 59, amb=19))
    pk = condition.pack_peak(recs)
    assert condition.lifetime_peak(pk, 60.0, len(recs))["case"] == "outside_log"
    assert condition.lifetime_peak(pk, 59.0, len(recs))["case"] == "log_reaches_it"
    assert condition.lifetime_peak(pk, 58.0, len(recs))["case"] == "log_is_hotter"
    # no counter, or no rides, means no claim at all
    assert condition.lifetime_peak(pk, None, len(recs)) is None
    assert condition.lifetime_peak(pk, 60.0, 0) is None


def test_the_note_never_attaches_an_ambient_to_the_lifetime_counter():
    # the durable guard on the invariant: the counter is not a log reading, and
    # an ambient beside it would be fabricated
    recs = parsers.parse_ride_log("\n".join([
        _hot("06/24/2026 08:00:00", 59, amb=19),
        _hot("06/24/2026 08:01:00", 59, amb=23)]))
    pk = condition.pack_peak(recs)
    for stat in (58.0, 59.0, 60.0):
        lp = condition.lifetime_peak(pk, stat, len(recs))
        note = condition.lifetime_peak_note(lp)
        # the failure this guards is the helpful-sounding join: "the
        # counter reports 60 C, at 19 C ambient". An ambient may only
        # ever be bound to the LOG sample, which was actually taken.
        for glue in (", at ", " at "):
            assert ("%g C%s" % (stat, glue)) not in note, (stat, note)


def test_the_note_says_so_when_the_firmware_prints_no_ambient():
    # never a dangling "at None C ambient", and never silence either
    recs = parsers.parse_ride_log(_hot("06/24/2026 08:00:00", 59))
    lp = condition.lifetime_peak(condition.pack_peak(recs), 60.0, len(recs))
    note = condition.lifetime_peak_note(lp)
    assert "a hot pack and a hot day cannot be told apart" in note
    # the sentence may SAY the word while printing no ambient VALUE,
    # which is exactly the distinction: no dangling "at None C ambient",
    # and no silence either
    assert " C ambient" not in note and " F ambient" not in note
    assert "None" not in note
    # the sensor caveat only makes sense beside a figure, so it is dropped
    assert "rule the weather out" not in note


def test_the_note_follows_the_requested_units():
    recs = parsers.parse_ride_log(_hot("06/24/2026 08:00:00", 59, amb=19))
    lp = condition.lifetime_peak(condition.pack_peak(recs), 60.0, len(recs))
    f = condition.lifetime_peak_note(lp, "F")
    assert "140 F" in f and "138 F" in f and "66 F" in f
    assert " C" not in f


def test_a_capture_with_no_pack_temperatures_says_which_figure_survives():
    recs = [{"soc": 60, "amb_temp_c": 20, "pack_temp_c": None, "ts": "x"}]
    lp = condition.lifetime_peak(condition.pack_peak(recs), 60.0, len(recs))
    assert lp["case"] == "no_pack_temp"
    note = condition.lifetime_peak_note(lp)
    assert "not one riding record" in note and "cannot be placed in time" in note


def test_the_new_context_never_reaches_a_graded_surface():
    """THE structural test, and the one that must survive every refactor.

    An ambient-adjusted threshold can only ever FORGIVE - a seller's bike
    pulled on a hot day would score better for it - which converts a check
    into a pass it did not earn. So the verdict must be identical whether or
    not the lifetime figure and units are passed.

    The fixture has to be one that actually grades something, or the test
    passes against code that has quietly dropped a check: `_loaded_log(3850)`
    is 150 mV down and grades "watch".
    """
    # the fixture must carry BOTH a graded check and an ambient reading:
    # without the deviation there is no grade to move, and without the
    # ambient a mutation that keys off the weather cannot even fire
    log = _loaded_log(3850).replace("PackTemp: h 30C,",
                                    "PackTemp: h 30C, AmbTemp: 34C,")
    plain = condition.verdict(condition.assess(log))
    withctx = condition.verdict(condition.assess(log, 60.0, "F"))
    assert plain["level"] == "watch"
    assert any(c["level"] != "unknown" for c in plain["checks"])
    lp = condition.assess(log, 60.0)["lifetime_peak"]
    assert lp and lp["amb_high_c"] == 34        # the weather IS visible
    assert plain["level"] == withctx["level"]
    assert plain["headline"] == withctx["headline"]
    assert plain["checks"] == withctx["checks"]


def test_the_peak_agrees_with_what_the_rides_block_reports():
    # two code paths, one bike: they must not be able to disagree about the
    # hottest reading in the same log
    log = "\n".join([_hot("06/24/2026 08:%02d:00" % i, 40 + i, amb=20)
                      for i in range(20)])
    recs = parsers.parse_ride_log(log)
    assert (condition.pack_peak(recs)["pack_temp_c"]
            == rides.summarize_rides(recs)["totals"]["max_pack_temp_c"])


_REAL_CAPTURES = os.path.join(os.path.expanduser("~"), "Documents",
                              "OpenMBB", "openmbb-sessions")


@pytest.mark.skipif(not os.path.isdir(_REAL_CAPTURES),
                    reason="reference captures not present")
def test_the_condition_block_follows_the_requested_units():
    """Three Celsius numbers were printed into a Fahrenheit report - in the page
    built to be handed to a buyer.

    Runs against a real capture on purpose. The lines at risk are the
    weakest-cell, discharge-allowance and plugged-in-hot lines, and a synthetic
    log that carries no loaded cell reading, no current limit and no charging
    renders none of them - so a synthetic version of this test passes without
    ever exercising what it claims to guard.
    """
    import re
    folder = os.path.join(_REAL_CAPTURES, "2026-08-19_160449_675068_COM4")
    if not os.path.isdir(folder):
        pytest.skip("reference capture not present")
    body = report.format_report(
        report.analyze_folder(folder, temp_units="F")).split(
        "== Condition (pack) ==")[1]
    # the three lines this exists for must actually be present...
    for needle in ("weakest cell under load", "discharge allowance", "still hot"):
        assert needle in body, needle
    # ...and none of them may carry Celsius
    assert not re.search(r"\d+\s*C\b", body), body
    cbody = report.format_report(
        report.analyze_folder(folder, temp_units="C")).split(
        "== Condition (pack) ==")[1]
    assert not re.search(r"\d+\s*F\b", cbody)


# --- unpopulated sensor slots, and an older temperature dialect --------------

def test_the_unused_sensor_sentinel_never_reaches_an_average():
    # a real bms prints four live sensors and four empty slots:
    #   Pack Temps : 27C 27C 27C 28C -100C -100C -100C -100C
    # averaged unguarded that is -36 C for a pack sitting at 27
    raw = [27.0, 27.0, 27.0, 28.0, -100.0, -100.0, -100.0, -100.0]
    live = parsers.real_temps(raw)
    assert live == [27.0, 27.0, 27.0, 28.0]
    assert sum(live) / len(live) == pytest.approx(27.25)


def test_the_floor_keeps_temperatures_a_bike_really_reaches():
    # NOT "drop the negatives": this platform states a Min Discharge Temp of
    # -25 C, and a bike left outside in winter genuinely reads below zero
    assert parsers.real_temps([-25.0]) == [-25.0]
    assert parsers.real_temps([-12.0, -11.0, -100.0]) == [-12.0, -11.0]
    assert parsers.real_temps([None, 20.0]) == [20.0]


def test_the_real_bms_block_reads_only_its_live_sensors():
    """The sentinel is filtered before anything consumes the list.

    Worth being precise about what this protects. The only consumer today
    is `max(temps)`, and a -100 C sentinel cannot drag a maximum down - so
    removing the filter would not change this assertion, and a test that
    implied otherwise would be claiming a bug that is not reachable.

    The filter is there for the consumer that has not been written yet: a
    mean, a minimum, or a spread across modules, any of which the sentinel
    would wreck. `real_temps` exists so that consumer has something to
    reach for instead of an inline magic number it would have to notice.
    """
    block = ("*               BMS Data               *\n"
             "  - Pack Temps                :    27C   27C   27C   28C "
             "-100C -100C -100C -100C\n")
    assert parsers.parse_bms(block)["pack_max_temp_c"] == 28
    # what the filter is really for, at the level it acts
    live = parsers.real_temps([27.0, 27.0, 27.0, 28.0] + [-100.0] * 4)
    assert len(live) == 4
    assert min(live) == 27 and sum(live) / len(live) == pytest.approx(27.25)


OLD_TEMP_DIALECT = """
 00001     05/12/2023 09:14:02   Riding      SOC: 88%, Vpack:114.900V, MotAmps:  12, BattAmps:  10, MotTemp:  38C, BattTemp:  24C, PackSOC: 88%, MotRPM:1200, Odo: 6100km
 00002     05/12/2023 09:15:02   Riding      SOC: 86%, Vpack:114.100V, MotAmps:  20, BattAmps:  18, MotTemp:  41C, BattTemp:  25C, PackSOC: 86%, MotRPM:2400, Odo: 6104km
"""


def test_the_older_battemp_dialect_is_kept_but_not_read_as_a_peak():
    # pack_temp_c is the HIGHEST module ("PackTemp: h 60C"), which is what makes
    # it comparable with the BMS lifetime counter. This dialect prints one number
    # and nothing establishes whether it is the max, a mean, or one sensor - and
    # if it is a mean, using it as a peak reports the pack COOLER than it got,
    # which is the unsafe direction for a tool that grades a hot pack.
    recs = parsers.parse_ride_log(OLD_TEMP_DIALECT)
    assert recs[0]["batt_temp_c"] == 24        # the reading is not thrown away
    assert recs[0]["pack_temp_c"] is None      # but it is not a peak
    assert condition.pack_peak(recs) is None


def test_the_modern_dialect_is_untouched_by_that():
    recs = parsers.parse_ride_log(
        " 00001  06/24/2026 08:00:00  Riding  PackTemp: h 59C, l 57C, "
        "PackSOC: 60%, Vpack:110.0V, BattAmps: 30, Odo: 6000km")
    assert recs[0]["pack_temp_c"] == 59
    assert recs[0]["batt_temp_c"] is None


def test_the_report_names_the_dialect_rather_than_going_quiet(tmp_path):
    s = sessions.Session(str(tmp_path),
                         {"eventlogdump": OLD_TEMP_DIALECT,
                          "stats": "  - Max Battery Temp : 60 C\n"}, "")
    text = report.format_report(report.analyze_session(s))
    assert "not one riding record in this capture carries a pack temperature" in text
    assert "prints a single `BattTemp`" in text
    assert "report the pack cooler than it got" in text


# --- what counts as a logged fault, and what dates one -----------------------

def _emcy(rec, ts, code):
    return (" %05d     %s   SEVCON CAN EMCY Frame      Error Code: 0x%s, "
            "Error Reg: 0x44" % (rec, ts, code))


def test_live_console_trace_caught_mid_read_is_not_a_logged_fault():
    # The firmware emits an asynchronous DEBUG trace when something goes wrong
    # WHILE the tool is talking to it. Counting those made the same bike report
    # different totals on two reads: 26 Sevcon frames against a table of 23.
    log = "\n".join([
        _emcy(1, "06/26/2026 08:12:27", "4000"),
        "ZERO MBB> DEBUG:        326648  ..\\src\\Application\\zero_mbb_can.c "
        ": line 214 - SEVCON CAN EMCY Frame Error Code: 0x4000",
    ])
    f = [x for x in condition.fault_history(log) if "Sevcon" in x["name"]][0]
    assert f["count"] == 1, "console trace was counted as a log entry"


def test_a_fault_clearing_does_not_date_the_fault():
    # Error Code 0x0000 is the controller saying the fault CLEARED. Ten of one
    # capture's 23 frames were clearings, and the last of them dated the bike's
    # most recent "fault" to what was actually a reset.
    log = "\n".join([
        _emcy(1, "06/26/2026 08:12:27", "4000"),      # onset
        _emcy(2, "08/12/2026 19:13:10", "3100"),      # onset
        _emcy(3, "08/12/2026 19:14:55", "0000"),      # clearing
    ])
    f = [x for x in condition.fault_history(log) if "Sevcon" in x["name"]][0]
    # every frame still counts - a controller resetting itself is worth seeing
    assert f["count"] == 3
    assert f["onsets"] == 2 and f["clearings"] == 1
    # ...but the dates are the onsets'
    assert f["last"] == "08/12/2026 19:13:10"
    assert "19:14:55" not in condition.fault_span(f)
    assert condition.fault_detail(f) == "3 frames: 2 onsets, 1 clearing"


def test_a_class_that_only_ever_cleared_says_so_rather_than_borrowing_a_date():
    log = _emcy(1, "08/12/2026 19:14:55", "0000")
    f = [x for x in condition.fault_history(log) if "Sevcon" in x["name"]][0]
    assert f["count"] == 1 and f["onsets"] == 0
    assert f["first"] is None
    assert "no onset in this capture" in condition.fault_span(f)
    assert "19:14:55" not in condition.fault_span(f)


def test_a_class_with_nothing_to_clear_prints_no_detail():
    log = _emcy(1, "06/26/2026 08:12:27", "4000")
    f = [x for x in condition.fault_history(log) if "Sevcon" in x["name"]][0]
    assert condition.fault_detail(f) == ""

    # And the classes that carry NO error code at all - most of them - must not
    # be read as clearings for want of one. Absence of a code is absence of
    # evidence, not evidence of a reset; treating it as one would strip the dates
    # off every precharge and module-connect entry in the log.
    pre = " 00001     06/24/2026 22:20:35   Precharge Lost. CapV: 78V"
    g = [x for x in condition.fault_history(pre) if "Precharge" in x["name"]][0]
    assert g["count"] == 1 and g["clearings"] == 0 and g["onsets"] == 1
    assert g["first"] == "06/24/2026 22:20:35"
    assert condition.fault_detail(g) == ""


def test_the_report_says_the_dates_are_onsets_when_anything_cleared(tmp_path):
    log = "\n".join([
        _emcy(1, "06/26/2026 08:12:27", "4000"),
        _emcy(2, "08/12/2026 19:14:55", "0000"),
    ])
    s = sessions.Session(str(tmp_path), {"eventlogdump": log}, "")
    text = report.format_report(report.analyze_session(s))
    assert "2 frames: 1 onset, 1 clearing" in text
    assert "the dates above are the onsets" in text


# --- three fields that are not readings, and a reply that is not a log -------

MODULE_FAIL_LINE = (
    " 00063     06/24/2026 22:20:27   ERROR: Cannot Connect Module 00! "
    "modv=0mV, maxv=0mV, minv=4294967295mV, raw0:101372mV, raw1:0mV, "
    "cur0:0A, cur1:0A, diff_allowed:1575mV")

REFUSAL = (" Sorry, 'dumplogs' is an invalid command. "
           'Type "help" for a list of commands')


def test_the_aggregate_triple_is_refused_on_arithmetic_not_on_a_constant():
    # maxv (0) below minv (4294967295) cannot happen over a non-empty set, and
    # 4294967295 mV is not 4,294,967 volts. That is the whole argument - it needs
    # no firmware knowledge and holds on any bike.
    d = parsers.decode_module_connect_failure(MODULE_FAIL_LINE)
    assert d["aggregates"] is None
    assert "maxv 0 mV is below minv 4294967295 mV" in d["aggregates_refused_because"]
    # the number must be legible: the absurdity IS the evidence
    assert "4294967295" in d["aggregates_refused_because"]
    assert "e+09" not in d["aggregates_refused_because"]

    # And the reason the predicate is `maxv < minv` rather than a match on
    # 0xFFFFFFFF: a DIFFERENT sentinel must still be refused. On the reference
    # bike both tests agree, so only a fixture where they diverge can show that
    # the general one was chosen. 65535 is 0xFFFF, the 16-bit equivalent.
    other = MODULE_FAIL_LINE.replace("minv=4294967295mV", "minv=65535mV")
    d2 = parsers.decode_module_connect_failure(other)
    assert d2["aggregates"] is None, "a 16-bit sentinel slipped through"
    assert "maxv 0 mV is below minv 65535 mV" in d2["aggregates_refused_because"]


def test_what_the_line_does_measure_is_kept():
    d = parsers.decode_module_connect_failure(MODULE_FAIL_LINE)
    assert d["module"] == "00"
    # raw0 moves line to line and sits at a plausible pack voltage, so it is real
    assert d["raw0_mv"] == 101372
    assert d["diff_allowed_mv"] == 1575


def test_a_populated_triple_is_accepted_so_the_guard_is_not_just_always_refuse():
    ok = MODULE_FAIL_LINE.replace("modv=0mV, maxv=0mV, minv=4294967295mV",
                                  "modv=114170mV, maxv=114180mV, minv=114160mV")
    d = parsers.decode_module_connect_failure(ok)
    assert d["aggregates"] == {"modv_mv": 114170, "maxv_mv": 114180,
                               "minv_mv": 114160}
    assert d["aggregates_refused_because"] is None


def test_it_never_states_why_the_module_was_ineligible():
    # "the module did not answer" is a plausible mechanism and it is not in the
    # line. The entry states no cause and neither may this.
    d = parsers.decode_module_connect_failure(MODULE_FAIL_LINE)
    blob = repr(d).lower()
    for forbidden in ("did not answer", "not answer", "no response", "timeout",
                      "too hot", "overheat"):
        assert forbidden not in blob, forbidden
    assert parsers.decode_module_connect_failure("Riding PackSOC: 60%") is None


def test_the_thermal_association_is_reported_as_an_association():
    log = "\n".join([
        " 00001     06/24/2026 22:20:20   BMS Disable - High Temp",
        MODULE_FAIL_LINE,
        " 00099     07/01/2026 10:00:00   ERROR: Cannot Connect Module 00! modv=0mV",
    ])
    ctx = condition.module_failure_context(log)
    assert ctx["total"] == 2 and ctx["near_high_temp"] == 1
    note = condition.module_failure_note(ctx)
    assert "1 of 2" in note
    assert "association only" in note and "does not state a cause" in note
    # never a causal claim
    assert "because" not in note.lower() and "caused" not in note.lower()


def test_no_thermal_disables_means_no_comparison_rather_than_zero_of_n():
    # "0 of 54" would read as evidence of absence; a bike that logged no thermal
    # disable says nothing either way
    log = " 00099     07/01/2026 10:00:00   ERROR: Cannot Connect Module 00! modv=0mV"
    ctx = condition.module_failure_context(log)
    assert ctx["total"] == 1 and ctx["near_high_temp"] is None
    assert condition.module_failure_note(ctx) == ""
    # and no failures at all is None, not an empty tally
    assert condition.module_failure_context("Riding PackSOC: 60%") is None


def test_the_consoles_refusal_is_not_an_event_log(tmp_path):
    # `dumplogs` is not a real rev-41 command. The saved reply is 77 non-empty
    # characters, which every `if text.strip()` fallback accepted as a log - so a
    # capture with no log at all reported that it had one.
    assert parsers.is_console_refusal(REFUSAL) is True
    assert parsers.is_console_refusal("") is False
    # a real log is not mistaken for a refusal, however long
    assert parsers.is_console_refusal(
        " 00001  06/24/2026 Riding PackSOC: 60%\n" * 50) is False

    folder = tmp_path / "cap"
    folder.mkdir()
    (folder / "001_bms.txt").write_text("# command: bms\n\n  - Pack SOC : 61%\n",
                                        encoding="utf-8")
    (folder / "016_dumplogs.txt").write_text(
        "# command: dumplogs\n\n" + REFUSAL + "\n", encoding="utf-8")
    s = sessions.load_session(str(folder))
    assert parsers.event_log_text(s) == ""
    assert library.summarize(str(folder))["has_event_log"] is False
    assert library.deep_verdict(str(folder)) is None


def test_a_real_log_still_wins_over_a_refusal_sitting_beside_it(tmp_path):
    folder = tmp_path / "cap"
    folder.mkdir()
    real = "\n".join(
        " 00001  06/24/2026 08:%02d:00  Riding  PackTemp: h 30C, PackSOC: %d%%, "
        "Vpack:110.0V, BattAmps: 30, Odo: %dkm" % (i, 90 - i, 6000 + i)
        for i in range(30))
    (folder / "015_eventlogdump.txt").write_text(
        "# command: eventlogdump\n\n" + real, encoding="utf-8")
    (folder / "016_dumplogs.txt").write_text(
        "# command: dumplogs\n\n" + REFUSAL + "\n", encoding="utf-8")
    s = sessions.load_session(str(folder))
    assert len(parsers.event_log_text(s)) > 500
    assert library.summarize(str(folder))["has_event_log"] is True


# --- the coverage limit, and which baseline is the backup --------------------

def test_an_unmeasured_stretch_of_the_ride_log_is_stated_not_swallowed(tmp_path):
    """condition.assess has always computed how much of the ride log can answer
    the cell question, and its own comment calls it "a coverage limit, not a
    pass". Nothing outside the tests read it.

    On the reference bike's July captures the report advertised 1137 ride
    samples while every cell answer rested on 635 of them, and the other 502 -
    records written before a firmware change, re-read by a newer one - were
    never mentioned. That is this project's own rule broken by a rendering gap,
    with the right number already in the dict.
    """
    good = " 00001  06/24/2026 08:%02d:00  Riding  PackTemp: h 30C, PackSOC: %d%%, " \
           "Vpack:110.000V, BattAmps: 120, Odo: %dkm, MinCell: 3700mV"
    # 8241 mV is 0x2031, the ASCII " 1" - the stale-record fabrication
    stale = " 00001  06/24/2026 09:%02d:00  Riding  PackTemp: h 30C, PackSOC: %d%%, " \
            "Vpack:110.000V, BattAmps: 120, Odo: %dkm, MinCell: 8241mV"
    log = "\n".join([good % (i, 90 - i, 6000 + i) for i in range(10)]
                    + [stale % (i, 80 - i, 6100 + i) for i in range(20)])

    a = condition.assess(log)
    cov = a["coverage"]
    assert cov["ride_samples"] == 30
    assert cov["ride_samples_with_cell"] == 10      # the guard refused 20

    s = sessions.Session(str(tmp_path), {"eventlogdump": log}, "")
    text = report.format_report(report.analyze_session(s))
    assert "cell voltage readable on 10 of 30 ride records" in text
    assert "the other 20 carry a value no cell can hold" in text
    # the load-bearing phrase: an unmeasured stretch is not a clean one
    assert "UNMEASURED" in text and "not clean" in text


def test_a_capture_with_every_record_readable_says_nothing_extra(tmp_path):
    good = " 00001  06/24/2026 08:%02d:00  Riding  PackTemp: h 30C, PackSOC: %d%%, " \
           "Vpack:110.000V, BattAmps: 120, Odo: %dkm, MinCell: 3700mV"
    log = "\n".join(good % (i, 90 - i, 6000 + i) for i in range(12))
    a = condition.assess(log)
    assert a["coverage"]["ride_samples_with_cell"] == a["coverage"]["ride_samples"]
    s = sessions.Session(str(tmp_path), {"eventlogdump": log}, "")
    text = report.format_report(report.analyze_session(s))
    assert "cell voltage readable on" not in text
    assert "UNMEASURED" not in text


def test_the_settings_backup_is_the_latest_one_not_the_last_alphabetically(tmp_path):
    """`sorted(...)[-1]` compared whole filenames, and "postlogin" beats a bare
    timestamp because "p" > "2" - so an EARLIER post-login dump always won over
    a LATER plain baseline. Reachable by pulling, logging in, pulling again.

    This text is what the write gate re-reads as the backup a write's undo
    depends on, so choosing the wrong one is not cosmetic.
    """
    (tmp_path / "settings_baseline_20260819_170000.txt").write_text(
        "later plain\n", encoding="utf-8")
    (tmp_path / "settings_baseline_postlogin_20260819_160000.txt").write_text(
        "earlier postlogin\n", encoding="utf-8")
    picked = sessions._newest_baseline(str(tmp_path))
    assert picked.endswith("settings_baseline_20260819_170000.txt"), picked
    assert "later plain" in sessions.load_session(str(tmp_path)).settings_text


def test_on_the_same_timestamp_the_fuller_postlogin_dump_wins(tmp_path):
    # a tie is the one case where the post-login dump is the better answer: it
    # is the one with the login-gated settings in it
    for name, body in (("settings_baseline_20260819_160000.txt", "plain\n"),
                       ("settings_baseline_postlogin_20260819_160000.txt",
                        "postlogin\n")):
        (tmp_path / name).write_text(body, encoding="utf-8")
    assert "postlogin" in sessions.load_session(str(tmp_path)).settings_text


def test_a_baseline_with_no_readable_stamp_is_ranked_low_not_dropped(tmp_path):
    (tmp_path / "settings_baseline_handedited.txt").write_text(
        "no stamp\n", encoding="utf-8")
    assert sessions._newest_baseline(str(tmp_path)) is not None
    (tmp_path / "settings_baseline_20260819_160000.txt").write_text(
        "stamped\n", encoding="utf-8")
    assert "stamped" in sessions.load_session(str(tmp_path)).settings_text
