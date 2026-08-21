"""The session library — what is on disk, at a glance.

Two properties matter here. A verdict that has not been computed must never read
as a pass, and a note must stay with the capture it describes rather than in a
central file that a copied folder leaves behind.
"""

import json
import os

from openmbb import library

BMS = """
*               BMS Data               *
  - Pack SOC                  :  61%
  - Pack Sum Voltage          :  106.412 V
  - Lowest Cell Voltage       :  3798 mV ( Cell 12 )
  - Pack Capacity             :  52 AH
  - Num Charge Cycles         :  512
"""

STATS = """
  - Firmware Revision         :  41
  - Odometer                  :  14088220 motor rev
                              :  6155 km
"""

# two rides hard enough to load the pack, and one charge across the window the
# capacity index is measured over
LOG = "\n".join(
    [" 05/16/2026 08:12:%02d Riding PackTemp: 24C, PackSOC: %d%%, "
     "Vpack:106.412V, MotAmps: 120, MotRPM:3100, Odo:6120km, "
     "BattAmps: 90, MinCell: 3705mV, AmbTemp: 21C" % (s % 60, 61 - s // 20)
     for s in range(0, 120)])


def _capture(folder, commands):
    folder.mkdir(parents=True, exist_ok=True)
    for i, (cmd, body) in enumerate(sorted(commands.items())):
        (folder / ("%03d_%s.txt" % (i + 1, cmd))).write_text(
            "# command: %s\n# time: 12:00:0%d.000\n\n%s" % (cmd, i % 10, body),
            encoding="utf-8")
    return str(folder)


def test_a_folder_becomes_a_row_you_can_actually_read(tmp_path):
    # the folder name is a timestamp and a serial port; on its own it tells an
    # owner nothing about which capture it is
    f = _capture(tmp_path / "2026-07-10_124738_435640_COM4",
                 {"bms": BMS, "stats": STATS})
    row = library.summarize(f)
    assert row["odo_km"] == 6155 and row["soc_pct"] == 61 and row["cycles"] == 512
    assert row["has_event_log"] is False
    # the capturing machine's clock, off the folder name - NOT the bike's clock,
    # which is the thing this project exists to catch being wrong
    import datetime as dt
    when = dt.datetime.fromtimestamp(row["when"])
    assert (when.year, when.month, when.day, when.hour) == (2026, 7, 10, 12)


def test_a_folder_named_anything_else_still_gets_a_time(tmp_path):
    f = _capture(tmp_path / "somewhere", {"bms": BMS})
    row = library.summarize(f)
    assert row["when"] > 0            # falls back to the folder mtime


def test_an_uncomputed_verdict_is_none_not_ok(tmp_path):
    # the one failure mode that matters: a blank verdict column beside a bike
    # you do not know must not read as a pass
    f = _capture(tmp_path / "cap", {"bms": BMS, "stats": STATS})
    assert library.summarize(f)["verdict"] is None
    assert library.cached_verdict(f) is None
    # and a capture with no event log cannot reach one at all
    assert library.deep_verdict(f) is None


def test_the_verdict_is_cached_beside_the_capture(tmp_path):
    f = _capture(tmp_path / "cap", {"bms": BMS, "stats": STATS,
                                    "eventlogdump": LOG})
    first = library.deep_verdict(f)
    assert first is not None and first["level"] in ("ok", "watch", "concern",
                                                    "unknown")
    assert os.path.exists(os.path.join(f, library.SUMMARY_FILE))
    assert library.cached_verdict(f) == first
    # the cache is beside the capture, so a copied folder brings its verdict
    assert library.deep_verdict(f, use_cache=True) == first


def test_a_cache_from_older_checks_is_recomputed_not_believed(tmp_path):
    # caching exists to skip re-reading a megabyte, not to freeze a judgement
    # the code has since changed its mind about
    f = _capture(tmp_path / "cap", {"bms": BMS, "eventlogdump": LOG})
    with open(os.path.join(f, library.SUMMARY_FILE), "w", encoding="utf-8") as fh:
        json.dump({"version": library.SUMMARY_VERSION - 1,
                   "level": "concern", "headline": "stale"}, fh)
    assert library.cached_verdict(f) is None
    assert library.deep_verdict(f)["headline"] != "stale"


def test_a_note_lives_with_the_capture_and_clears_cleanly(tmp_path):
    f = _capture(tmp_path / "cap", {"bms": BMS})
    assert library.read_note(f) == ""
    library.write_note(f, "  after the 2026-06-13 reflash  ")
    assert library.read_note(f) == "after the 2026-06-13 reflash"
    assert library.summarize(f)["note"] == "after the 2026-06-13 reflash"
    # a note file sitting in the folder must not be mistaken for a command
    from openmbb import sessions
    assert "session_note" not in sessions.load_session(f).commands
    library.write_note(f, "   ")
    assert library.read_note(f) == ""
    assert not os.path.exists(os.path.join(f, library.NOTE_FILE))


def test_the_list_is_ordered_by_capture_time_not_by_folder_mtime(tmp_path):
    # copying a capture onto another machine rewrites its mtime; a library that
    # then called a 2026-07 pull the newest one would be lying about the only
    # column an owner uses to find things
    root = tmp_path / "sessions"
    _capture(root / "2026-08-19_160449_675068_COM4", {"bms": BMS, "stats": STATS})
    _capture(root / "2026-07-10_124738_435640_COM4", {"bms": BMS, "stats": STATS})
    (root / "not-a-session").mkdir()          # no command output: not a capture
    rows = library.scan(str(root))
    assert [r["name"] for r in rows] == ["2026-08-19_160449_675068_COM4",
                                         "2026-07-10_124738_435640_COM4"]
    assert all(r["verdict"] is None for r in rows)


def test_scanning_somewhere_that_is_not_there_is_empty_not_an_error(tmp_path):
    assert library.scan(str(tmp_path / "nope")) == []
