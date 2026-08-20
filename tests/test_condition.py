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
