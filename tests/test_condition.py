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
