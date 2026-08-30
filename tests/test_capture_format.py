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
