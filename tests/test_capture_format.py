"""What a session folder claims to be, and what this build agrees to read.

Session folders carried no schema version. The firmware taught this project the
lesson from the other side: the 2026-06-13 reflash changed a ride record's
layout underneath a reader with no way to know, and 1,051 records came back as
plausible nonsense rather than as an error. The stamp is that same hazard from
OUR side, closed before it happens instead of after.

It does nothing today, and these tests are mostly a back-compatibility proof
rather than a feature test: every capture in existence is format 1 - including
all three real ones, written by three different app versions - so what is being
pinned here is that none of them changed meaning.
"""

import os

import pytest

from openmbb import sessions
from openmbb.sessions import CAPTURE_FORMAT, CaptureFormatError, capture_format

REAL = os.path.join(os.path.expanduser("~"), "Documents", "OpenMBB",
                    "openmbb-sessions")


def _capture(tmp_path, name, meta=None):
    """The smallest folder `load_session` will accept."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "001_bms.txt").write_text(
        "# command: bms\n# time: 16:04:49.000\n\nPack Voltage : 113.000V\n",
        encoding="utf-8")
    if meta is not None:
        (d / "session_meta.txt").write_text(meta, encoding="utf-8")
    return str(d)


# --------------------------------------------- item 12: every session stamps
#
# The stamp writer lived on the full-pull path, so listen captures,
# connect-without-pull sessions and selftest runs carried no meta file at all -
# the insurance excluded exactly the capture shape a646b7c had promoted to
# first-class.

@pytest.mark.parametrize("tag", ["listen", "selftest", "COM3"])
def test_every_session_states_its_format_from_the_moment_it_exists(tmp_path, tag):
    from openmbb.transport import SessionLogger

    lg = SessionLogger(base_dir=str(tmp_path), tag=tag)
    meta = os.path.join(lg.dir, "session_meta.txt")
    assert os.path.isfile(meta), "a session with no meta file cannot say what it is"
    text = open(meta, encoding="utf-8").read()
    assert "capture_format: %d" % CAPTURE_FORMAT in text
    assert "app_version:" in text and "time:" in text
    assert capture_format(lg.dir) == CAPTURE_FORMAT


def test_the_pull_path_enriches_the_stamp_without_losing_it(tmp_path):
    """The full pull rewrites this file. Nothing the base stamp established may
    disappear when it does - a capture that stated its format at 12:00 and
    stopped stating it at 12:05 is worse than one that never did."""
    from openmbb.transport import SessionLogger

    lg = SessionLogger(base_dir=str(tmp_path), tag="COM3")
    before = open(os.path.join(lg.dir, "session_meta.txt"), encoding="utf-8").read()
    # what the pull path writes (gui._write_session_meta), same keys plus more
    lg.save_named("session_meta.txt",
                  "OpenMBB session metadata\n"
                  "capture_format: %d\n"
                  "time: 2026-08-29T14:30:22\n"
                  "app_version: 9.9.9\n"
                  "firmware_rev: 41\n"
                  "power_mode: Stopped\n" % CAPTURE_FORMAT)
    after = open(os.path.join(lg.dir, "session_meta.txt"), encoding="utf-8").read()
    for key in ("capture_format:", "time:", "app_version:"):
        assert key in before and key in after, key
    assert capture_format(lg.dir) == CAPTURE_FORMAT


# ----------------------------------------------------------- one decoder
#
# a646b7c gave `redact` a BOM-aware decoder and left the loaders on UTF-8 only,
# so the two halves of the tool disagreed about what a capture IS. Both failures
# below were reproduced before the fix.

def _utf16_capture(tmp_path, name, meta):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "001_bms.txt").write_bytes(
        ("# command: bms\n# time: 12:00:00.000\n\nPack Voltage : 113.2\n")
        .encode("utf-16"))
    (d / "session_meta.txt").write_bytes(meta.encode("utf-16"))
    return d


def test_a_utf16_capture_still_has_its_commands(tmp_path):
    # `redact` calls this capture first-class; `analyze` found zero commands in
    # it, because _header_command decoded UTF-8 only
    d = _utf16_capture(tmp_path, "cap", "capture_format: 1\n"
                                        "time: 2026-08-29T14:30:22\n")
    s = sessions.load_session(str(d))
    assert sorted(s.commands) == ["bms"]
    assert "Pack Voltage" in s.cmd("bms")
    assert sessions._session_date(str(d)) == "2026-08-29"


def test_a_utf16_stamp_from_the_future_is_refused_not_read_as_absent(tmp_path):
    # the exact false pass the stamp exists to prevent: a key that is PRESENT,
    # reported absent, so the capture reads as format 1 and gets believed
    d = _utf16_capture(tmp_path, "cap", "capture_format: %d\n"
                       % (CAPTURE_FORMAT + 1))
    with pytest.raises(CaptureFormatError):
        capture_format(str(d))


def test_one_stray_byte_does_not_cost_the_whole_capture(tmp_path):
    """The reader is deliberately more forgiving than the export.

    `redact` refuses bytes it cannot decode - it must not vouch for what it
    could not read. A reader has the opposite duty: losing one character to a
    stray byte beats losing the capture.
    """
    d = tmp_path / "cap"
    d.mkdir()
    (d / "001_bms.txt").write_bytes(
        b"# command: bms\n\nPack Voltage : 113.2 \xff\n")
    s = sessions.load_session(str(d))
    assert sorted(s.commands) == ["bms"]
    assert "Pack Voltage" in s.cmd("bms")


def test_the_loaders_and_the_export_use_the_same_decoder(tmp_path):
    # one decoder, not two that agree today: the disagreement is the bug
    from openmbb import redact as _redact

    assert sessions.redact.decode_text is _redact.decode_text
    p = tmp_path / "f.txt"
    p.write_bytes("caf\u00e9 \u2014 113.2 V\n".encode("utf-16"))
    assert sessions.read_text(str(p)) == _redact.decode_text(
        p.read_bytes())[0]


# ------------------------------------------------------------- provenance
#
# `source:` is a new KEY, not a new format: an old reader ignores it and is not
# made wrong by it, which is exactly the bar the bump comment sets. What these
# pin is that adding it did not change what any existing folder means.

def test_the_meta_file_outranks_the_folder_name(tmp_path):
    # the name is the field a privacy fix has to normalise away, and the one a
    # person can rename; the meta file is what OpenMBB itself wrote down
    d = _capture(tmp_path, "2026-08-29_143022_1_sim",
                 "capture_format: 1\nsource: COM7\n")
    assert sessions.session_source(str(d)) == "COM7"


def test_a_capture_written_before_source_existed_still_says_it_is_a_sim(tmp_path):
    d = _capture(tmp_path, "2026-08-29_143022_1_sim", "capture_format: 1\n")
    assert sessions.session_source(str(d)) == "simulator"
    assert sessions.not_from_a_bike(str(d))


def test_a_collision_suffix_does_not_un_mark_a_simulator(tmp_path):
    # a build before the fix wrote `..._sim_1` on a same-microsecond collision,
    # which endswith("_sim") answers False to - the folder silently rejoined
    # the trend line as real history
    d = _capture(tmp_path, "2026-08-29_143022_1_sim_1", "capture_format: 1\n")
    assert sessions.not_from_a_bike(str(d))


def test_silence_is_not_a_claim_about_where_it_came_from(tmp_path):
    # ...but it is still real history, because that is what format 1 means and
    # every capture ever taken is untagged
    d = _capture(tmp_path, "2026-08-29_143022_1_COM3", "capture_format: 1\n")
    assert sessions.session_source(str(d)) is None
    assert not sessions.not_from_a_bike(str(d))


def test_a_same_microsecond_collision_keeps_the_tag_at_the_end(tmp_path):
    import datetime as _real
    import types
    from openmbb import transport as _tr

    frozen = _real.datetime(2026, 8, 29, 14, 30, 22, 123456)

    class _Fixed(_real.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    saved = _tr._dt
    _tr._dt = types.SimpleNamespace(datetime=_Fixed)
    try:
        a = _tr.SessionLogger(base_dir=str(tmp_path), tag="sim")
        b = _tr.SessionLogger(base_dir=str(tmp_path), tag="sim")
    finally:
        _tr._dt = saved
    assert a.dir != b.dir, "the collision path did not run"
    # Assert the NAME SHAPE, not just the reading of it. _NOT_A_BIKE_RE
    # deliberately tolerates a trailing counter so that folders an older build
    # already wrote still read correctly - which means a not_from_a_bike()
    # assertion alone stays green with this fix reverted. The mutation runner
    # caught exactly that.
    for d in (a.dir, b.dir):
        assert os.path.basename(d).endswith("_sim"), os.path.basename(d)
        assert sessions.not_from_a_bike(d), d


def test_the_identity_a_report_prints_is_built_from_what_openmbb_wrote(tmp_path):
    d = _capture(tmp_path, "Daves-FXS-preinspection",
                 "capture_format: 1\ntime: 2026-08-29T14:30:22\nsource: COM3\n")
    label, banner = sessions.capture_identity(str(d))
    assert label == "2026-08-29 14:30, from COM3"
    assert banner is None
    assert "Daves" not in label


def test_a_folder_with_nothing_to_go_on_says_so_rather_than_guessing(tmp_path):
    d = _capture(tmp_path, "my-bike-notes")
    assert sessions.capture_identity(str(d)) == ("(not recorded)", None)


def test_a_folder_that_makes_no_claim_is_format_one(tmp_path):
    """Silence has exactly one honest reading. Every capture written before the
    stamp existed is a format-1 capture, and refusing them would refuse every
    capture that currently exists - including the three this project measures."""
    assert CAPTURE_FORMAT == 1
    # no session_meta.txt at all (a CLI-driven capture never writes one)
    assert capture_format(_capture(tmp_path, "nometa")) == 1
    # a meta file from before the stamp existed
    assert capture_format(_capture(tmp_path, "oldmeta", meta=(
        "OpenMBB session metadata\ntime: 2026-08-19T16:10:11\n"
        "app_version: 0.21.0\nfirmware_rev: 41\n"))) == 1


@pytest.mark.skipif(not os.path.isdir(REAL), reason="no real captures here")
def test_the_real_captures_are_format_one_and_still_load():
    """Written by v0.10.1, v0.19.1 and v0.21.0 - three app versions, one capture
    format. That is the distinction the stamp exists to make: `app_version` was
    never a format version and was never comparable as one."""
    folders = [os.path.join(REAL, d) for d in os.listdir(REAL)
               if os.path.isdir(os.path.join(REAL, d))
               and not d.endswith(("_sim", "_listen"))]
    assert folders, REAL
    for f in folders:
        assert capture_format(f) == 1, f
        assert sessions.load_session(f).commands, f


def test_a_capture_from_the_future_is_refused_and_says_which_is_older(tmp_path):
    """A folder claiming a version we cannot read is a could-not-run case, and
    those never read as a pass. The message has to name both numbers, or the
    person holding it cannot tell which of the two things to update."""
    folder = _capture(tmp_path, "future", meta=(
        "OpenMBB session metadata\ncapture_format: 2\n"
        "time: 2027-01-01T00:00:00\n"))
    with pytest.raises(CaptureFormatError) as excinfo:
        sessions.load_session(folder)
    msg = str(excinfo.value)
    assert "format 2" in msg and "format 1" in msg
    assert "NEWER OpenMBB" in msg


@pytest.mark.parametrize("value", ["", "   ", "banana", "1.2", "v1", "0x1",
                                   "0", "-1"])
def test_a_version_that_is_not_a_version_is_refused_not_guessed(tmp_path, value):
    """A key present with an unreadable value is MALFORMED, not absent: something
    meant to state a version and failed. Quietly reading it as 1 is exactly the
    false pass the stamp exists to prevent."""
    folder = _capture(tmp_path, "bad_%s" % abs(hash(value)), meta=(
        "OpenMBB session metadata\ncapture_format: %s\n"
        "time: 2026-08-19T16:10:11\n" % value))
    with pytest.raises(CaptureFormatError):
        sessions.load_session(folder)


def test_the_stamp_does_not_move_the_date_the_folder_already_carried(tmp_path):
    """The stamp is inserted as line 2, above `time:`, and `_session_date` greps
    the same file. A capture whose date shifted would reorder the whole library,
    which is the one column an owner uses to find things."""
    folder = _capture(tmp_path, "2026-08-19_160449_675068_COM4", meta=(
        "OpenMBB session metadata\ncapture_format: 1\n"
        "time: 2026-08-19T16:10:11\napp_version: 0.24.0\n"))
    s = sessions.load_session(folder)
    assert s.captured_at.get("bms", "").startswith("2026-08-19")


def test_a_stamped_capture_at_the_current_format_reads_normally(tmp_path):
    """What this build writes, this build reads - the round trip that makes the
    stamp insurance rather than a tripwire."""
    folder = _capture(tmp_path, "current", meta=(
        "OpenMBB session metadata\ncapture_format: %d\n"
        "time: 2026-08-19T16:10:11\n" % CAPTURE_FORMAT))
    assert capture_format(folder) == CAPTURE_FORMAT
    assert sessions.load_session(folder).cmd("bms")
