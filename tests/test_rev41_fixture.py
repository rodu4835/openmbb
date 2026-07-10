"""Parser tests against the REAL (redacted) 2017 FXS rev-41 console capture.

The fixture at tests/fixtures/rev41_level0_console.txt is the verbatim
multi-command sweep from the owner's own bike (mbb-console-2026-06-21_222834),
with the VIN and serial numbers replaced by same-length placeholders. It is the
ground truth the parsers must handle — the simulator is only a synthetic double.
"""

import os
import re

import pytest

from openmbb import parsers
from openmbb.safety import WRITE_WHITELIST
from openmbb.transport import parse_settings_dump

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "rev41_level0_console.txt")

# B1: the REAL post-login `set` dump from the owner's bike (2026-07-10 live
# session, file 020_set.txt), VIN + MBB serial redacted to same-width tokens.
# This is the ground truth for the write-whitelist setting NAMES.
POSTLOGIN_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                                 "rev41_postlogin_set.txt")


def _load():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def _load_postlogin():
    with open(POSTLOGIN_FIXTURE, encoding="utf-8") as f:
        return f.read()


def _section(text, name):
    """The output block for command `name` in the '===== > cmd =====' capture."""
    m = re.search(r"=====\s*>\s*%s\s*=====\n(.*?)(?:\n=====\s*>|\Z)"
                  % re.escape(name), text, re.S)
    return m.group(1) if m else ""


# B1: the fixtures MUST be redacted, but the guard tests must not themselves
# carry the owner's real VIN/serials as literals (that just relocates the PII into
# tracked source). Assert by SHAPE instead: the same-width placeholders are present
# and no token matching a real VIN/serial shape survives. The VIN charset excludes
# I/O/Q, and the placeholder REDACTEDVIN000000 contains an 'I', so it can never be a
# 17-char VIN-shape false positive.
_VIN_SHAPE = re.compile(r"\b[0-9A-HJ-NPR-Z]{17}\b")
_MBB_SERIAL_SHAPE = re.compile(r"(?i)sj\d{4}zer\d{4}")     # e.g. an MBB/BMS serial
_MODULE_SERIAL_SHAPE = re.compile(r"(?i)17gb\d{4}")        # e.g. a module serial


def _assert_redacted(text):
    assert "REDACTEDVIN000000" in text        # the VIN placeholder is present
    assert "REDACTEDMBB00" in text            # the serial placeholder is present
    assert not _VIN_SHAPE.findall(text)       # no real 17-char VIN-shape token
    assert _MBB_SERIAL_SHAPE.search(text) is None
    assert _MODULE_SERIAL_SHAPE.search(text) is None


def test_fixture_carries_no_pii():
    _assert_redacted(_load())


def test_parse_rev41_columnar_settings():
    settings, order = parse_settings_dump(_section(_load(), "set"))
    assert settings["model_year"]["value"] == "2017"
    assert settings["firmware_rev"]["value"] == "41"
    assert settings["board_id"]["value"] == "7"
    assert settings["model"]["value"] == "FXS"
    assert settings["region"]["value"] == "0x00"
    assert settings["serial"]["value"] == "REDACTEDMBB00"
    assert settings["vin"]["value"] == "REDACTEDVIN000000"
    # the Psudo table above the Settings table parses too
    assert settings["runtime"]["value"] == "00000:03:01:02"
    assert set(order) >= {"model_year", "serial", "vin", "firmware_rev",
                          "board_id", "model", "region", "runtime"}


def _columnar(rows):
    """Build a rev-41-geometry columnar table (ruler + at cols 0/21/61/74) with
    values RIGHT-aligned ending at col 73 — the real bike's layout."""
    ruler = "+" + "-" * 20 + "+" + "-" * 39 + "+" + "-" * 12 + "+" + "-" * 5
    out = [" Setting Name".ljust(21) + " Setting Desc".ljust(40) + "Value", ruler]
    for name, desc, value in rows:
        r = ((" " + name).ljust(21) + " " + desc)
        out.append(r.ljust(73 - len(value)) + value)
    out += ["", "ZERO MBB> "]
    return "\n".join(out)


def test_columnar_value_extraction_edge_cases():
    # T8: position-aware value extraction across the cases the old split broke
    dump = _columnar([
        ("spfront",  "Front sprocket teeth", "22"),          # single-token
        ("vin",      "Bike VIN", "REDACTEDVIN000000"),       # 17ch overflow LEFT
        ("wide",     "Speed cap", "85 MPH  ( 137 KPH )"),    # 2 internal spaces (R11)
        ("blankval", "Description only here", ""),            # empty value col (R12)
        ("blankdesc", "", "42"),                              # empty DESC col (C1)
        ("twospace", "Coast  regen  cap", "55"),             # 2+-space DESC (C1)
        ("__stray line with no name column__", "", ""),      # non-identifier (R10)
        ("sprear",   "Rear sprocket teeth", "56"),           # must survive the stray row
    ])
    s, _ = parse_settings_dump(dump)
    assert s["spfront"]["value"] == "22"
    assert s["vin"]["value"] == "REDACTEDVIN000000"          # overflow kept whole
    assert s["wide"]["value"] == "85 MPH  ( 137 KPH )"       # internal 2-space kept
    assert s["blankval"]["value"] == ""                      # not the description
    # C1 regressions the v0.10.1 "first 2+-group = desc" split got wrong:
    assert s["blankdesc"]["value"] == "42"                   # value kept, NOT moved to desc
    assert s["blankdesc"]["desc"] == ""
    assert s["twospace"]["value"] == "55"                    # desc's 2-space run didn't leak
    assert s["twospace"]["desc"] == "Coast  regen  cap"      # whole wordy desc kept
    assert "sprear" in s and s["sprear"]["value"] == "56"    # stray row didn't abort


def test_columnar_blank_value_without_trailing_pad():
    # D6 (review PARSE-1): a blank-value row whose trailing padding was stripped
    # (the line ends right after the description, no 2+-space gap anywhere) must
    # parse as desc=text / value="" — NOT value=<description>. Real ruler geometry.
    ruler = "+" + "-" * 20 + "+" + "-" * 39 + "+" + "-" * 12 + "+" + "-" * 5
    header = " Setting Name".ljust(21) + " Setting Desc".ljust(40) + "Value"
    row = (" killpol").ljust(21) + " " + "Kill Switch Polarity"   # ends mid-desc col
    dump = "\n".join([header, ruler, row, "", "ZERO MBB> "])
    s, _ = parse_settings_dump(dump)
    assert "killpol" in s
    assert s["killpol"]["value"] == ""                 # NOT the description
    assert s["killpol"]["desc"] == "Kill Switch Polarity"


def test_rev41_level0_hides_tunables():
    # the ⭐ finding: spfront/sprear/rwhcirc are login-gated, ABSENT at level 0
    settings, _ = parse_settings_dump(_section(_load(), "set"))
    for tunable in ("spfront", "sprear", "rwhcirc", "maxcustspmph"):
        assert tunable not in settings


def test_bare_login_reports_level():
    login = _section(_load(), "login")
    m = re.search(r"Login Level:\s*(\d+)", login)     # ground truth: note the colon
    assert m and int(m.group(1)) == 0


def test_parse_real_bms_section():
    bms = parsers.parse_bms(_section(_load(), "bms"))
    assert bms["soc_pct"] == 100
    assert bms["pack_v"] == pytest.approx(116.002)
    assert bms["low_cell_mv"] == 4148
    assert bms["high_cell_mv"] == 4156
    assert bms["balance_mv"] == 8
    assert bms["capacity_ah"] == 52
    assert bms["cycles"] == 32


def test_parse_real_status_section():
    st = parsers.parse_status(_section(_load(), "status"))
    assert st["mode"] == "Charging"
    assert st["soc_pct"] == 100


def test_parse_real_stats_odometer():
    st = parsers.parse_stats(_section(_load(), "stats"))
    assert st["odo_motor_rev"] == 14260802
    assert st["odo_km"] == 6249                       # not the 3883 miles line
    assert "41" in str(st["fw_rev"])
    assert st["max_motor_temp_c"] == 87
    assert st["max_batt_temp_c"] == 59


# --- B1: post-login `set` dump vs the write whitelist -------------------------

def test_postlogin_fixture_carries_no_pii():
    _assert_redacted(_load_postlogin())


def test_postlogin_reveals_real_tunables():
    # login DID reveal the gearing/custom-mode tunables that level 0 hides
    settings, _ = parse_settings_dump(_load_postlogin())
    assert settings["spfront"]["value"] == "20"
    assert settings["sprear"]["value"] == "90"
    assert settings["rwhcirc"]["value"] == "1972"     # the real bike's circumference
    # the raw/x10 twins and the KPH/RPM speed forms exist alongside the mph one
    assert settings["maxcustspmph"]["value"] == "89"
    assert settings["maxcustsprpm"]["value"] == "5445"
    assert settings["maxcusttq_allowed"]["value"] == "100"


def test_whitelist_names_match_real_rev41_set_dump():
    # B1 acceptance (comment corrected in C3): the write whitelist is intentionally
    # GENERIC across Gen2 models, so not every whitelist name exists on this bike —
    # but every real rev-41 name it claims must actually be in the dump. Assert the
    # 7 verified names ARE present (gearing + the renamed custom-mode options — a
    # regression guard against the old invented maxcustsp/maxcusttq/... names).
    from openmbb.safety import REV41_FXS_SETTINGS
    settings, order = parse_settings_dump(_load_postlogin())
    live = [n for n in order if n in WRITE_WHITELIST]
    assert set(live) >= {"spfront", "sprear", "rwhcirc"}          # gearing
    assert "maxcustspmph" in live                                 # real custom-mode name
    assert {"maxcusttq_allowed", "maxcustregcotq_allow",
            "maxcustregbrtq_allow"} <= set(live)                  # the other three
    assert len(live) > 3
    # C3: REV41_FXS_SETTINGS must equal exactly the whitelist names actually in the
    # real dump — so the UI's "verified on rev 41" claim tracks ground truth.
    assert set(live) == set(REV41_FXS_SETTINGS)
    # the retired invented names must NOT appear in the whitelist anymore
    for dead in ("maxcustsp", "maxcusttq", "maxcustregcotq", "maxcustregbrtq"):
        assert dead not in WRITE_WHITELIST
