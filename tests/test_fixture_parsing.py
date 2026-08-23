"""What the parsers say about real hardware output, asked the right questions.

Every parser defect this project has found came from real console text, and the
one committed fixture already contained all four PII shapes before anybody looked
for them. The lesson was not "get more fixtures" — it was that a fixture nobody
interrogates proves nothing.

So these are not smoke tests. Each one asks a fixture the specific question that
a guard elsewhere in the codebase exists to answer, against output the bike
really produced. If a guard is ever quietly removed, one of these fails with a
real number attached rather than a synthetic one.

The fixtures are redacted copies of a 2017 FXS at MBB rev 41 / BMS rev 48. They
are re-scanned for identifier shapes by tests/test_release_gate.py on every run.
"""

import io
import os

import pytest

from openmbb import condition, parsers

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with io.open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return "".join(l for l in f if not l.startswith("#"))


# --- the number guards, against the token that motivated them ---------------

def test_the_garbled_token_in_the_real_chargers_block_yields_no_reading():
    """`0.-09 A` is real output, not a fuzz case — the firmware prints it.

    It should be -0.09. Reading it as 0.0 loses a negative; reading it as -9
    is a hundred times off. Both are worse than nothing, so both helpers refuse
    the whole token.
    """
    text = fixture("rev41_chargers.txt")
    assert "0.-09" in text, "the fixture no longer carries the token it exists for"
    line = [l for l in text.splitlines() if "0.-09" in l][0]
    # the well-formed number on the same line still reads
    assert -1.0 in parsers.all_nums(line)
    # ...and the garbled one contributes nothing
    assert not any(abs(x) == 9.0 or x == 0.09 for x in parsers.all_nums(line))
    assert parsers.num("0.-09 A") is None


# --- the sentinel, and the cell attribution ---------------------------------

def test_the_real_bms_block_answers_the_questions_the_guards_ask():
    bms = parsers.parse_bms(fixture("rev41_bms.txt"))

    # the pack-temperature sensor list carries -100 placeholders; the maximum
    # must come from the live sensors only
    assert bms["pack_max_temp_c"] is not None
    assert bms["pack_max_temp_c"] > 0, "a -100 sentinel reached the maximum"

    # the BMS names WHICH cell is weakest, and a cell index has to be a cell
    for key in ("low_cell_index", "high_cell_index"):
        idx = bms.get(key)
        assert idx is None or 0 < idx <= 999, (key, idx)

    # the bike states its own thermal limits at permission level 0
    assert bms["max_charge_temp_c"] == 50
    assert bms["min_charge_temp_c"] == 0
    assert bms["min_discharge_temp_c"] == -25
    # and the find() collision that would answer min-charge from min-DIScharge
    assert bms["min_charge_temp_c"] != bms["min_discharge_temp_c"]


def test_a_bike_without_the_sensor_list_gets_no_maximum_rather_than_the_minimum():
    """The other-bike case, asked of real output.

    The qualifier guard is INERT on a complete capture - `Pack Temps` is present,
    so nothing falls through. It only does work on a firmware that omits that
    line, which is exactly the case these parsers advertise tolerating and
    exactly what no complete fixture can exercise. So remove the line from real
    output and ask again.

    Before the guard this returned 23 C, off `Lowest Present Pack Temp`: the
    pack's coldest sensor reported as its hottest, on the metric that grades a
    hot pack.
    """
    text = fixture("rev41_bms.txt")
    assert any("pack temps" in l.lower() for l in text.splitlines()), \
        "the fixture no longer has the line this test removes"
    without = "\n".join(l for l in text.splitlines()
                            if "pack temps" not in l.lower())
    # the neighbouring labels that used to answer are still there...
    assert any("lowest present pack temp" in l.lower()
               for l in without.splitlines())
    # ...and none of them may answer for the sensor list
    assert parsers.parse_bms(without).get("pack_max_temp_c") is None


def test_the_real_pack_temp_line_still_carries_the_sentinel():
    # if a future fixture refresh loses it, the guard above stops proving anything
    text = fixture("rev41_bms.txt")
    line = [l for l in text.splitlines() if "pack temps" in l.lower()][0]
    assert "-100" in line, "the fixture no longer exercises the sentinel"
    live = parsers.real_temps(parsers.all_nums(line))
    assert live and all(t > 0 for t in live)
    assert len(live) < len(parsers.all_nums(line)), "nothing was filtered"


# --- the fault block, which grades presence and absence ----------------------

def test_the_real_obd_block_reports_no_fault_as_a_fact_not_a_guess():
    obd = parsers.parse_obd(fixture("rev41_obd.txt"))
    # this bike had no stored codes; the point is that it says so with real
    # values rather than by the keys being absent
    assert obd["active_dtcs"] == 0
    assert obd["pending_dtcs"] == 0
    assert obd["mil_on"] is False          # False here means READ as off
    assert obd["mil_on"] is not None       # ...not "could not tell"


# --- the interlocks ----------------------------------------------------------

def test_the_real_inputs_block_strips_the_adc_tail_and_keeps_the_state():
    inp = parsers.parse_inputs(fixture("rev41_inputs.txt"))
    # "Down  - Raw : 2999 mV ( 4095 ADC)" must yield "Down", not the tail
    for key in ("kickstand", "kill_switch"):
        val = inp.get(key)
        assert val is None or ("Raw" not in val and "ADC" not in val), (key, val)
    # a rail that carries a number reads as a number
    assert isinstance(inp.get("pack_mv"), float)
    assert inp["pack_mv"] > 50000, "a pack voltage in mV should be five figures"


# --- the error log, and the triple that is refused ---------------------------

def test_the_real_error_log_carries_the_triple_that_must_be_refused():
    text = fixture("rev41_errorlogdump.txt")
    lines = [l for l in text.splitlines() if "cannot connect module" in l.lower()]
    if not lines:
        pytest.skip("this capture's error log holds no module-connect entries")
    d = parsers.decode_module_connect_failure(lines[0])
    assert d is not None
    # maxv below minv cannot happen over a non-empty set - that is the whole
    # argument, and it is arithmetic rather than firmware knowledge
    assert d["aggregates"] is None
    assert "below" in d["aggregates_refused_because"]
    # raw0 IS a live reading and survives
    assert d["raw0_mv"] is None or d["raw0_mv"] > 1000


# --- every fixture, against the contract ------------------------------------

@pytest.mark.parametrize("name", sorted(
    n for n in os.listdir(FIXTURES) if n.endswith(".txt")))
def test_every_fixture_parses_without_raising_and_invents_nothing(name):
    """The tolerance promise, against real text rather than generated text.

    Cheap, and it is the check that would have caught several of this month's
    defects: every parser is run over every fixture, including the ones whose
    output it was never meant to read.
    """
    text = fixture(name)
    for fn in (parsers.parse_bms, parsers.parse_stats, parsers.parse_status,
               parsers.parse_inputs, parsers.parse_outputs,
               parsers.parse_runtime, parsers.parse_obd):
        got = fn(text)
        assert isinstance(got, dict)
        for key, val in got.items():
            if isinstance(val, float):
                assert val == val, (name, fn.__name__, key, "NaN")
                assert abs(val) != float("inf"), (name, fn.__name__, key, "inf")
    for fn in (parsers.parse_ride_log, parsers.parse_charge_log,
               parsers.parse_limit_events):
        assert isinstance(fn(text), list)
    parsers.parse_odometer(text)
    parsers.top_speed_mph(text)
    condition.charge_behaviour(text)
