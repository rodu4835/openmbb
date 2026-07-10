"""Replay REAL console bytes through Transport.exec_command.

This is the layer no other test exercised with ground truth: the v0.10 detectors
(reboot, quiet, start-of-line prompt termination, truncation/resync) all look at
the raw wire, and the simulator differs from the real console EXACTLY there.
Feeding the verbatim captures is what catches detector-class bugs — e.g. the
reboot false-positive where the real rev-41 `version` reply ends with
'Reset Source: Power-On' and every real connect would have failed at Phase 0.
"""

import os
import tempfile

import pytest

from openmbb.gui import _looks_like_version, _parse_fw_rev
from openmbb.transport import ConsoleRebootError, SessionLogger, Transport

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _reply_bytes(name):
    """The console's on-the-wire reply for a capture: verbatim bytes ending at
    the prompt (trailing CR/LF stripped, the prompt's trailing space kept)."""
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read().rstrip(b"\r\n")


class ReplayPort:
    """Returns a fixed reply stream, but only AFTER the command is written (so the
    pre-write flush drain sees an empty wire, like a real port)."""

    def __init__(self, reply):
        self._reply = reply
        self._armed = False

    def read(self, n=1):
        if self._armed:
            self._armed = False
            return self._reply
        return b""

    @property
    def in_waiting(self):
        return len(self._reply) if self._armed else 0

    def write(self, data):
        self._armed = True
        return len(data)

    def close(self):
        pass


def _replay(name):
    port = ReplayPort(_reply_bytes(name))
    return Transport(port, SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcr_"),
                                         tag="t"))


def test_real_version_reply_does_not_trip_reboot_detector():
    # THE showstopper regression: the real rev-41 `version` reply ends with
    # 'Reset Source: Power-On'. It must NOT be read as a reboot — otherwise every
    # real connect (gui probe runs `version`) fails at Phase 0. This passes the
    # verbatim captured bytes through the real transport read path.
    out = _replay("rev41_version.txt").exec_command("version")
    assert "Firmware Rev : 41" in out
    assert "TRUNCATED" not in out
    assert _looks_like_version(out)
    assert _parse_fw_rev(out) == 41


def test_real_boot_banner_does_trip_reboot_detector():
    # a genuine reboot (boot banner with the ' - Checking ...' self-test lines)
    # MUST still raise — the fix keys on those lines, not 'Reset Source:'
    with pytest.raises(ConsoleRebootError):
        _replay("rev41_boot_banner.txt").exec_command("version", idle_timeout=0.3)
