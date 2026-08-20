"""Condition checks — the gauge-independent half.

No display and no hardware needed. Every case here is built from the shapes the
real 2017 FXS actually printed, not from the simulator, because the whole point
of this module is what it says about a bike nobody has a baseline for.
"""

from openmbb import condition, parsers

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
