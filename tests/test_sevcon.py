"""The motor controller block, read at last.

`sevcon` was captured on every pull from the beginning and never parsed. The
work plan called the controller "the second most expensive part on the bike",
and until now a stored controller fault would not have reached a verdict at all
— on the tool whose headline scenario is inspecting a stranger's motorcycle.

Graded the way `obd` is: the FAULT COUNT is presence-or-absence and needs no
reference bike. The temperatures are measured and not graded, because nothing
has established what a warm Sevcon means on this platform.
"""

import io
import os

import pytest

from openmbb import health, parsers, sessions

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "rev41_sevcon.txt")
REAL = os.path.join(os.path.expanduser("~"), "Documents", "OpenMBB",
                    "openmbb-sessions")


def _fixture():
    with io.open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def test_the_controller_block_parses_every_field_it_is_read_for():
    sev = parsers.parse_sevcon(_fixture())
    assert sev["active_faults"] == 0
    assert sev["controller_temp_c"] == 27
    assert sev["max_controller_temp_c"] == 27
    assert sev["motor_temp_c"] == 35
    assert sev["max_motor_temp_c"] == 36
    assert sev["operational"] is True
    assert sev["fw_rev"] == "SN0066.23"


def test_a_mode_is_not_a_temperature():
    """`Motor Temp`, `Max Motor Temp This Ride`, `Age of motor temp data` and
    `Motor Temp Control Mode` all contain "motor temp".

    The last is a MODE — 0x01 — and a substring match would have reported it as
    the motor's temperature. This is the same shape as the bug that once read
    `Min Discharge Temp` as a charge limit, so it is pinned rather than trusted.
    """
    sev = parsers.parse_sevcon(_fixture())
    assert sev["motor_temp_c"] == 35          # not 1, and not 140 (the age)
    assert sev["controller_temp_c"] == 27     # not 173 (the age)

    # On THIS bike `Motor Temp` is printed before `Motor Temp Control Mode`, so
    # a first-match-wins lookup reaches the right line by luck and the exclude
    # changes nothing. The mutation runner caught that: the guard could be
    # deleted and this test stayed green.
    #
    # So ask the question another bike might: same real block, mode first.
    lines = _fixture().splitlines()
    mode = [ln for ln in lines if "Motor Temp Control Mode" in ln]
    assert len(mode) == 1, "fixture no longer carries the colliding label"
    reordered = chr(10).join(mode + [ln for ln in lines if ln not in mode])
    moved = parsers.parse_sevcon(reordered)
    assert moved["motor_temp_c"] == 35, (
        "a MODE was read as a temperature when it happened to come first")


def test_a_block_that_is_not_there_yields_nothing_rather_than_zero():
    """A controller that was never read must not report zero faults: that is a
    check which could not run, and it may never read as a pass."""
    assert parsers.parse_sevcon("") == {}
    assert parsers.parse_sevcon(None) == {}
    assert "active_faults" not in parsers.parse_sevcon(
        "Sorry, 'sevcon' is an invalid command")


def test_an_unreadable_operational_flag_is_none_not_false():
    """False would render as "not in operational mode" — a finding invented out
    of a value nobody could read."""
    text = _fixture().replace("- In Operational Mode       : Yes",
                              "- In Operational Mode       : ?")
    assert parsers.parse_sevcon(text)["operational"] is None


def test_a_stored_fault_reaches_the_health_tab_as_an_alert(tmp_path):
    """The whole point of the item: a controller fault has to surface where an
    inspection verdict can see it."""
    faulted = _fixture().replace("- Number of Faults          :    0",
                                 "- Number of Faults          :    3")
    s = sessions.Session(str(tmp_path), {"sevcon": faulted}, "")
    rows = [m for m in health.health_snapshot(s) if m["label"] == "Sevcon faults"]
    assert len(rows) == 1
    assert rows[0]["status"] == "alert"
    assert "3 active" in rows[0]["display"]


def test_a_clean_controller_reads_ok_and_shows_its_temperatures(tmp_path):
    s = sessions.Session(str(tmp_path), {"sevcon": _fixture()}, "")
    rows = [m for m in health.health_snapshot(s) if m["label"] == "Sevcon faults"]
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert "none active" in rows[0]["display"]
    # measured, and beside the fault count rather than graded on their own
    assert "27 C" in rows[0]["display"] and "36 C" in rows[0]["display"]


def test_no_sevcon_block_means_no_row_at_all(tmp_path):
    """Silence is not an OK. A capture without the block must leave the row out
    rather than assert a clean controller."""
    s = sessions.Session(str(tmp_path), {}, "")
    assert not [m for m in health.health_snapshot(s)
                if m["label"] == "Sevcon faults"]


def test_the_controller_odometer_is_never_printed_as_distance():
    """It reads 2.3752x the MBB's on every reference capture — identical to four
    decimals across two months of riding, so it is a scale factor and not the
    bike's mileage. Printing it would put a number on screen 2.4 times the real
    one."""
    sev = parsers.parse_sevcon(_fixture())
    assert sev["odo_km"] == 18821.3           # parsed, and kept for the ratio

    # ...and absent from every surface. Checked by VALUE rather than by reading
    # the source: health_snapshot uses `odo_km` legitimately for the MBB's own
    # odometer, so a source scan cannot tell the two figures apart.
    import tempfile
    from openmbb import report as report_mod

    folder = tempfile.mkdtemp(prefix="sevodo_")
    with io.open(os.path.join(folder, "001_sevcon.txt"), "w",
                 encoding="utf-8") as f:
        f.write("# command: sevcon\n# time: 12:00:00.000\n\n" + _fixture())
    s = sessions.load_session(folder)
    rendered = " ".join(
        "%s %s" % (m["label"], m["display"]) for m in health.health_snapshot(s))
    rendered += report_mod.format_report(report_mod.analyze_session(s))
    assert "18821" not in rendered, (
        "the controller's odometer reached a surface; it reads 2.375x the "
        "bike's real mileage")


@pytest.mark.skipif(not os.path.isdir(REAL), reason="no real captures here")
def test_every_reference_capture_reads_a_clean_controller():
    # `_sim` / `_listen` are NOT from a bike - the same rule gui.py and
    # library.py apply, and one the capture format treats as load-bearing. A
    # simulator run written to the default save location used to fail this
    # test by being asked bike questions.
    folders = [os.path.join(REAL, d) for d in os.listdir(REAL)
               if os.path.isdir(os.path.join(REAL, d))
               and not d.endswith(("_sim", "_listen"))]
    assert folders
    for f in folders:
        sev = parsers.parse_sevcon(sessions.load_session(f).cmd("sevcon"))
        assert sev.get("active_faults") == 0, f
        assert sev.get("operational") is True, f
        # the ratio that proves the odometer is not wheel distance
        mbb = parsers.parse_stats(sessions.load_session(f).cmd("stats"))
        if sev.get("odo_km") and mbb.get("odo_km"):
            assert 2.37 < sev["odo_km"] / mbb["odo_km"] < 2.38, f


def test_a_hex_fault_count_is_unreadable_not_zero():
    """`num("0x1C")` reads the leading zero and returns 0.0, so a count of 28
    faults rendered "none active" and a green OK row.

    Hex is not hypothetical in this block: the same real capture prints 0x01,
    0x00 and 0x010d0005 on neighbouring lines, so a firmware writing the count
    that way is an ordinary thing to meet. A value we cannot read produces NO
    key and therefore no row, which is what "could not read" looks like.
    """
    hexed = _fixture().replace("- Number of Faults          :    0",
                               "- Number of Faults          :    0x1C")
    sev = parsers.parse_sevcon(hexed)
    assert "active_faults" not in sev, "a hex count must not parse as a number"

    s = sessions.Session("x", {"sevcon": hexed}, "")
    assert not [m for m in health.health_snapshot(s)
                if m["label"] == "Sevcon faults"], (
        "an unreadable fault count must leave the row out, never render ok")
