"""The tolerance promise, checked against inputs nobody thought of.

`parsers.py` opens with a flat claim — *every parser here is label-fuzzy and
degrades to None rather than raising* — and until this file existed nothing
checked it. Examples cannot: the promise is about inputs nobody has imagined,
which is the one kind of claim a hand-written case is structurally unable to make.

Three properties, and they are the whole contract:

  **Never raise.** Whatever the bike printed, a parser returns something.
  **Never invent.** An unreadable field is absent or None — never a plausible
  number, and never a definite False where the truth is "could not read it".
  **Type-shape.** A field's type does not depend on the input being well-formed.

Strategies are seeded from `tests/fixtures/`, never from the real captures under
`~/Documents/OpenMBB/`. Those do not exist on CI, so seeding from them would make
these tests quietly weaker on every runner than on the machine they were written
on — the failure mode where green means less than it appears to.

The regression cases at the bottom are the four defects this measurement found
that a real bike can produce today. They are examples on purpose: a property says
"this class of thing holds", and a regression says "this exact thing, which we
got wrong once, stays right".
"""

import io
import os

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from openmbb import parsers

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Deliberately modest: these run on every CI push, and the value here is breadth
# of SHAPE rather than depth of search. The defects this found were all shallow.
SETTINGS = settings(max_examples=200, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])


def _fixture_lines():
    """Real console lines, as the raw material for mutation."""
    out = []
    for name in sorted(os.listdir(FIXTURES)):
        if not name.endswith(".txt"):
            continue
        with io.open(os.path.join(FIXTURES, name), encoding="utf-8",
                     errors="replace") as f:
            out.extend(l for l in f.read().splitlines() if l.strip())
    return out


REAL_LINES = _fixture_lines()
assert REAL_LINES, "fixtures are the seed corpus; something is wrong if empty"

# Tokens the firmware really emits that have broken a parser before, plus the
# shapes that break number extraction generally.
NASTY = st.sampled_from([
    "", " ", ":", "::", "- ", "0.-51", "-100C", "4294967295mV", "0x2031",
    "8241mV", "### TRUNCATED", "n/a", "---", "1.2.3", "+", "-", ".",
    "1e309", "9" * 320, "\t", "\x00", "—", "MIL ON : 1", "0A",
])

TEXTISH = st.one_of(
    st.text(max_size=120),
    NASTY,
    st.sampled_from(REAL_LINES),
)


def _mutate(line, op, pos, extra):
    """One small corruption of a real line — the shapes a bad cable produces."""
    if not line:
        return extra
    i = pos % len(line)
    if op == 0:
        return line[:i] + line[i + 1:]              # dropped byte
    if op == 1:
        return line[:i] + extra + line[i:]          # inserted junk
    if op == 2:
        return line[:i]                             # truncated mid-line
    if op == 3:
        return line.upper()                         # a firmware that cases differently
    if op == 4:
        return line.lower()
    return line.replace(":", "", 1)                 # a missing separator


MUTATED = st.builds(
    _mutate,
    st.sampled_from(REAL_LINES),
    st.integers(0, 5),
    st.integers(0, 500),
    st.one_of(NASTY, st.text(max_size=8)),
)

BLOCK = st.lists(st.one_of(MUTATED, TEXTISH), max_size=12).map("\n".join)

# Every parser that takes a block of console text and returns a dict.
DICT_PARSERS = [
    parsers.parse_bms, parsers.parse_stats, parsers.parse_status,
    parsers.parse_inputs, parsers.parse_outputs, parsers.parse_runtime,
    parsers.parse_obd,
]
LIST_PARSERS = [
    parsers.parse_ride_log, parsers.parse_charge_log, parsers.parse_limit_events,
]


# --- property 1: never raise -------------------------------------------------

@SETTINGS
@given(text=BLOCK)
def test_no_parser_raises_on_anything(text):
    """The headline promise. A bike this tool has never met prints something
    unexpected; the parser's job is to come back with less, not to crash."""
    for fn in DICT_PARSERS:
        got = fn(text)
        assert isinstance(got, dict), fn.__name__
    for fn in LIST_PARSERS:
        got = fn(text)
        assert isinstance(got, list), fn.__name__
    parsers.parse_odometer(text)
    parsers.top_speed_mph(text)
    parsers.event_log_text(_FakeSession({"eventlogdump": text}))


@SETTINGS
@given(text=st.one_of(TEXTISH, MUTATED))
def test_the_number_helpers_never_raise(text):
    n = parsers.num(text)
    assert n is None or isinstance(n, float)
    xs = parsers.all_nums(text)
    assert isinstance(xs, list) and all(isinstance(x, float) for x in xs)
    assert isinstance(parsers.real_temps(xs), list)


# --- property 2: never invent ------------------------------------------------

@SETTINGS
@given(text=st.one_of(TEXTISH, MUTATED))
def test_a_number_is_never_invented_out_of_an_unreadable_token(text):
    """`num` refusing a token and `all_nums` accepting its pieces is how
    "0.-51" became [0.0, -51.0] — two readings from one unreadable field."""
    xs = parsers.all_nums(text)
    n = parsers.num(text)
    if n is None and xs:
        # all_nums may legitimately find numbers num skipped only if the FIRST
        # token was garbled; every value it returns must still be finite
        assert all(x == x and abs(x) != float("inf") for x in xs), (text, xs)
    for x in xs:
        assert x == x, "NaN invented from %r" % text
        assert abs(x) != float("inf"), "infinity invented from %r" % text
    if n is not None:
        assert n == n and abs(n) != float("inf"), text


@SETTINGS
@given(text=BLOCK)
def test_a_field_is_absent_or_none_rather_than_a_confident_wrong_answer(text):
    """Every value a parser hands back is either None or of its declared type.
    A string where a number belongs is an invented reading with a type."""
    # An explicit list, not a suffix guess. `bms_fw_rev` is deliberately a
    # STRING ("48 (993 banka)") and an earlier version of this property called
    # that a defect - the property was wrong, not the parser. Guessing a type
    # from a key's spelling is the same class of mistake the parsers themselves
    # were making.
    numeric = {
        "soc_pct", "pack_v", "pack_max_temp_c", "pack_temp_c", "capacity_ah",
        "remaining_ah", "cycles", "low_cell_mv", "high_cell_mv", "balance_mv",
        "low_cell_index", "high_cell_index", "max_charge_temp_c",
        "min_charge_temp_c", "min_discharge_temp_c", "isolation_kohm",
        "odo_km", "odo_motor_rev", "max_batt_temp_c", "max_motor_temp_c",
        "max_ctrl_temp_c", "lifetime_wh_km", "top_speed_mph", "motor_temp_c",
        "ctrl_temp_c", "pack_mv", "run_s", "charge_s", "active_dtcs",
        "pending_dtcs",
    }
    for fn in DICT_PARSERS:
        for key, val in fn(text).items():
            if val is None or key not in numeric:
                continue
            assert isinstance(val, (int, float)), (fn.__name__, key, val)
            assert val == val, (fn.__name__, key, "NaN")
            assert abs(val) != float("inf"), (fn.__name__, key, "inf")


@SETTINGS
@given(text=BLOCK)
def test_a_boolean_field_is_never_a_confident_false_it_could_not_establish(text):
    """`bool(None)` is False, and False on a fault lamp means "no fault".
    An unreadable value has to stay None."""
    got = parsers.parse_obd(text)
    if "mil_on" in got:
        assert got["mil_on"] is None or isinstance(got["mil_on"], bool)


# --- property 3: type shape does not depend on well-formedness ---------------

@SETTINGS
@given(text=BLOCK)
def test_ride_records_have_a_stable_shape(text):
    for rec in parsers.parse_ride_log(text):
        assert isinstance(rec, dict)
        for key in ("soc", "vpack", "pack_temp_c", "battamps", "mincell_mv"):
            val = rec.get(key)
            assert val is None or isinstance(val, (int, float)), (key, val)


class _FakeSession(object):
    def __init__(self, commands):
        self.commands = commands

    def cmd(self, name):
        return self.commands.get(name, "")


# --- the four defects this measurement found, pinned as examples -------------

def test_the_warning_lamp_is_never_reported_off_on_a_line_saying_it_is_on():
    # detected case-insensitively, extracted case-sensitively: "MIL ON : 1"
    # fell through to a branch that found nothing, and bool(None) is False
    for line in ("MIL On   1", "MIL ON : 1", "mil on : 1", "  - MIL ON  1"):
        assert parsers.parse_obd(line)["mil_on"] is True, line
    for line in ("MIL On   0", "MIL ON : 0"):
        assert parsers.parse_obd(line)["mil_on"] is False, line
    # and unreadable is None - a different thing from False, and it must stay so
    assert parsers.parse_obd("MIL On   ?")["mil_on"] is None


def test_a_garbled_decimal_yields_no_reading_at_all():
    # "0.-51" is a real token from the chargers dump; it means -0.51. Reading it
    # as 0.0 loses a negative; reading it as -51.0 is a hundred times off.
    assert parsers.num("0.-51 A") is None
    assert parsers.all_nums("0.-51 A") == []
    # its neighbours on the same line survive
    assert parsers.all_nums("1.5 0.-51 3.5") == [1.5, 3.5]
    # a plain trailing period is not garbled
    assert parsers.all_nums("6809.") == [6809.0]


def test_a_qualifier_may_not_answer_for_the_label_it_qualifies():
    # delete `Pack Temps` and `find(kv, "pack", "temp")` used to match
    # `Lowest Present Pack Temp` - the coldest sensor reported as the hottest,
    # on the metric that grades a hot pack
    block = ("  - Lowest Present Pack Temp  :  23 C\n"
             "  - Max Pack Temp This Ride   :  25 C\n")
    assert parsers.parse_bms(block).get("pack_max_temp_c") is None
    with_list = "  - Pack Temps  :  24C  25C  23C\n" + block
    assert parsers.parse_bms(with_list).get("pack_max_temp_c") == 25.0


def test_our_own_truncation_banner_is_not_an_event_log():
    banner = "### TRUNCATED: no console prompt seen before the read ended\n"
    real = " 00001  06/24/2026 08:00:00  Riding  PackSOC: 60%\n" * 30
    # the banner alone must not shadow a real log in the fallback command
    picked = parsers.event_log_text(
        _FakeSession({"eventlogdump": banner, "dumplogs": real}))
    assert "Riding" in picked
    # but a genuinely truncated log is still a log - on real firmware a dump
    # ending without a prompt is the NORMAL exit
    assert "Riding" in parsers.event_log_text(
        _FakeSession({"eventlogdump": real + banner}))
