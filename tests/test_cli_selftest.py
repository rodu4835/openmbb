"""The pre-bike-day check, and the record the contactor gate promised.

`openmbb --selftest` is what you run before driving to a motorcycle. It proves
the blocklist, the transport, the write chain, and - the reason it exists at all
- that pyserial actually ships in the frozen bundle, since a missing pyserial
otherwise shows only as an empty COM-port list once you are standing at the bike.

It was broken for eleven commits. 7479947 moved the contactor gate into
`transport.exec_command`, where no caller can go around it, and updated every
caller except this one; `--selftest` then died on an uncaught BlockedCommandError
at "dump with progress:", taking the four sections after it - including the
pyserial check - with it. Nothing caught that, because its entire coverage was
that the string `--selftest` appears in `--help` output.

So these tests assert the END of the run, not the beginning: a wall of [PASS]
lines followed by a crash is precisely the failure this file exists to catch.
"""

import os
import subprocess
import sys

import pytest

from openmbb.safety import BlockedCommandError
from openmbb.sim import SimPort
from openmbb.transport import SessionLogger, Transport


@pytest.fixture(scope="module")
def selftest_run():
    """One real run, shared by every assertion below - it costs ~7 s."""
    return subprocess.run([sys.executable, "-m", "openmbb.cli", "--selftest"],
                          capture_output=True, text=True, timeout=600)


def test_the_selftest_runs_all_the_way_to_the_end(selftest_run):
    r = selftest_run
    assert r.returncode == 0, (r.stdout[-2000:] + r.stderr[-2000:])
    assert "SELFTEST PASSED" in r.stdout
    assert "FAIL" not in r.stdout
    # The sections that come AFTER the point it used to die. Asserting these is
    # what stops a mid-run death hiding behind the [PASS] lines before it.
    for section in ("write flow:", "validators:", "session files:", "frozen deps:"):
        assert section in r.stdout, "never reached %r" % section
    # the one that is the whole point of running this before a bike day
    assert "pyserial import + comports() works" in r.stdout


def test_the_selftest_exercises_the_gate_that_broke_it(selftest_run):
    """It went unnoticed because nothing in the selftest ever touched the heavy
    gate. It does now, so the next caller left un-updated fails here first."""
    out = selftest_run.stdout
    assert "[PASS] heavy read refused without consent" in out
    assert "[PASS] confirmed=True does not satisfy the contactor gate" in out
    assert "[PASS] heavy-read consent journalled" in out


# --- the record the gate promised --------------------------------------------

class DeadPort:
    """A wire that accepts nothing: write() raises. Stands in for the read that
    does not survive - a contactor trip, an unplugged cable - which is exactly
    the case the consent record has to outlive."""

    is_sim = False
    in_waiting = 0

    def read(self, n=1):
        return b""

    def write(self, data):
        raise IOError("the wire died")


def _transport(tmp_path, port=None):
    return Transport(port or SimPort(),
                     SessionLogger(base_dir=str(tmp_path), tag="test"))


def _journal(tr):
    if not os.path.exists(tr.logger.journal_path):
        return ""
    with open(tr.logger.journal_path, encoding="utf-8") as f:
        return f.read()


def test_the_consent_is_recorded_before_the_first_byte(tmp_path):
    """The docstring promised this and the code did not do it: heavy_consent was
    read once as a truthy gate and dropped.

    It has to be written BEFORE the wire, because the record exists for exactly
    the reads that do not finish. A heavy read can leave a permanent "Line
    Contactor o/c" entry on a bike that is not yours; if that entry turns up
    later, this line is the only thing that can say the owner agreed.
    """
    tr = _transport(tmp_path, DeadPort())
    with pytest.raises(OSError):
        tr.exec_command("dumpall", heavy_consent="owner said yes, bike parked")
    text = _journal(tr)
    assert "HEAVY READ CONSENTED" in text
    assert "owner said yes, bike parked" in text
    assert "dumpall" in text


def test_a_refused_heavy_read_leaves_no_consent_record(tmp_path):
    """The record says a human agreed, so it may only exist where one did."""
    tr = _transport(tmp_path)
    with pytest.raises(BlockedCommandError):
        tr.exec_command("dumpall")
    assert "CONSENTED" not in _journal(tr)


def test_the_consent_record_is_masked_like_everything_else(tmp_path):
    """The consent string is free text typed by a person, and every other write
    to disk masks the session's registered secrets. A password echoed into the
    consent sentence must not survive in the journal either."""
    tr = _transport(tmp_path, DeadPort())
    tr.logger.add_redaction("tpsreport")
    with pytest.raises(OSError):
        tr.exec_command("dumpall", heavy_consent="typed tpsreport at the dialog")
    text = _journal(tr)
    assert "tpsreport" not in text
    assert "****" in text


def test_an_ordinary_read_is_not_journalled(tmp_path):
    """Only the heavy reads carry this cost, so only they earn a line. A journal
    that logged every `bms` would bury the two entries that matter."""
    tr = _transport(tmp_path)
    tr.exec_command("bms")
    assert _journal(tr) == ""
