"""Headless tests for the safety + transport layers (no display needed).

Run with:  python -m pytest   (or)   openmbb --selftest
"""

import glob
import os
import tempfile
import time

import pytest

from openmbb.safety import (WRITE_WHITELIST, BlockedCommandError,
                            _v_coast_regen, command_blocked)
from openmbb.sim import SimPort
from openmbb.transport import (READ_COMMANDS, ConsoleQuietError,
                               ConsoleRebootError, SessionLogger, Transport,
                               first_number, looks_like_prompt,
                               nonprintable_ratio, parse_settings_dump)


def make_transport():
    tmp = tempfile.mkdtemp(prefix="zctest_")
    return Transport(SimPort(), SessionLogger(base_dir=tmp, tag="test"))


def make_transport_with_port():
    tmp = tempfile.mkdtemp(prefix="zctest_")
    port = SimPort()
    return Transport(port, SessionLogger(base_dir=tmp, tag="test")), port


class ScriptedPort:
    """Replays read() chunks back-to-back, but only AFTER the command is
    written — mimicking a real port whose response follows the write (so the
    pre-write flush drain sees an empty wire, not the queued response)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._armed = False
        self.written = b""

    def read(self, n=1):
        if self._armed and self._chunks:
            return self._chunks.pop(0)
        return b""

    @property
    def in_waiting(self):
        return sum(len(c) for c in self._chunks) if self._armed else 0

    def write(self, data):
        self.written += data
        self._armed = True
        return len(data)

    def close(self):
        pass


class PromptOnlyPort:
    """A live wire that answers EVERY command with just the prompt — no parseable
    `set` dump (models an unrecognized firmware whose `set` format the parser can't
    read). Used to prove write_setting fails closed on a cold, unparseable cache."""

    def __init__(self):
        self._buf = b""
        self.written = b""

    def read(self, n=1):
        out, self._buf = self._buf, b""
        return out

    @property
    def in_waiting(self):
        return len(self._buf)

    def write(self, data):
        self.written += data
        self._buf = b"\r\nZERO MBB> "        # prompt only, no settings table
        return len(data)

    def close(self):
        pass


class TimedPort:
    """Delivers each scripted chunk only after its scheduled delay; read()
    returns b'' until then. Lets a test create a real idle gap on the wire."""

    def __init__(self, schedule):        # schedule: [(delay_s, bytes), ...]
        self._sched = list(schedule)
        self._t0 = time.time()
        self.written = b""

    def read(self, n=1):
        if self._sched and (time.time() - self._t0) >= self._sched[0][0]:
            return self._sched.pop(0)[1]
        return b""

    @property
    def in_waiting(self):
        return 0

    def write(self, data):
        self.written += data
        return len(data)

    def close(self):
        pass


class ArmedTimedPort:
    """Like TimedPort, but delays are measured from the FIRST write — so the
    pre-write flush sees an empty wire and a test can place a real idle gap
    inside the response, AFTER the command is sent."""

    def __init__(self, schedule):
        self._sched = list(schedule)
        self._t0 = None
        self.written = b""

    def read(self, n=1):
        if self._t0 is None:
            return b""
        if self._sched and (time.time() - self._t0) >= self._sched[0][0]:
            return self._sched.pop(0)[1]
        return b""

    @property
    def in_waiting(self):
        return 0

    def write(self, data):
        if self._t0 is None:
            self._t0 = time.time()
        self.written += data
        return len(data)

    def close(self):
        pass


BLOCKED = [
    "format eeprom", "erase eeprom", "eeprom dump 0 16", "settingsrst", "statsrst",
    "eventlogclear", "errorlogadd hi", "reset", "exit_to_bl", "test isolation -v",
    "wdt reset", "timing", "can", "charger", "sevcon preop", "bluetooth reset",
    "set abs_disable On", "set motstage2 200", "set sevmaxregv 4300",
    "set ov_kickstand Yes", "set vin 123", "set debug_level 3",
    # A3: destructive commands from the real rev-41 `help` menu (confirmed live)
    "dtc_clear", "force_all_storage_mode kick", "blcmds", "burn",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocked_commands_refused(cmd):
    tr = make_transport()
    with pytest.raises(BlockedCommandError):
        tr.exec_command(cmd)


def test_reads_allowed():
    tr = make_transport()
    for cmd in READ_COMMANDS:
        assert len(tr.exec_command(cmd)) > 20


def test_command_lists_reflect_real_rev41():
    # A1: `dumplogs` is not a real command — it must be gone everywhere
    from openmbb.transport import DUMP_COMMANDS, HEAVY_COMMANDS, LONG_COMMANDS
    assert "dumplogs" not in DUMP_COMMANDS
    assert "dumplogs" not in LONG_COMMANDS
    assert "dumplogs" not in HEAVY_COMMANDS
    # A2: the contactor-risky dumps are HEAVY (explicit/warned) — NOT baseline dumps
    assert "eventlogdump" in HEAVY_COMMANDS and "dumpall" in HEAVY_COMMANDS
    assert "eventlogdump" not in DUMP_COMMANDS   # so the FULL BASELINE won't auto-run it
    assert DUMP_COMMANDS == ["errorlogdump"]     # only the small, safe log dump
    assert set(HEAVY_COMMANDS) <= LONG_COMMANDS  # they still get dump-class timeouts


def test_sevcon_faults_is_a_read():
    assert command_blocked("sevcon faults") is None


def test_obd_is_a_read_command():
    # B2: `obd` ("Show all obd info") is a read in the real rev-41 menu
    assert "obd" in READ_COMMANDS
    assert command_blocked("obd") is None
    tr = make_transport()
    assert len(tr.exec_command("obd")) > 20


def test_bare_eeprom_reads_but_args_are_blocked():
    # B2: bare `eeprom` = "Show EEPROM usage" (a read); anything with args, or
    # format/erase eeprom, stays blocked. Fail-closed: only the exact bare token.
    assert command_blocked("eeprom") is None
    assert command_blocked("eeprom set 0 1") is not None
    assert command_blocked("eeprom dump 0 16") is not None
    assert command_blocked("format eeprom") is not None
    assert command_blocked("erase eeprom") is not None
    tr = make_transport()
    # D5: bare `eeprom` is a logged-in menu item (019_help.txt), so read it after
    # login — the sim mirrors the real bike and treats it as unknown at level 0.
    tr.exec_command("login tpsreport")
    assert "eeprom usage" in tr.exec_command("eeprom").lower()   # the read returns data


def test_level0_gating_hides_tunables():
    # rev-41 (and now the sim): tunables are login-gated, identity shows at L0
    tr = make_transport()
    l0, _ = parse_settings_dump(tr.exec_command("set"))
    assert "spfront" not in l0
    assert l0.get("model", {}).get("value") == "FXS"
    assert len(l0) < 10


def test_settings_parse():
    tr = make_transport()
    tr.exec_command("login tpsreport")          # tunables appear only after login
    settings, order = parse_settings_dump(tr.exec_command("set"))
    assert len(settings) >= 30
    assert settings["spfront"]["value"] == "20"
    live_whitelist = [n for n in order if n in WRITE_WHITELIST]
    assert len(live_whitelist) == len(WRITE_WHITELIST)


def test_sim_exercises_columnar_value_extraction():
    # T9/C1: the sim's real-geometry `set` dump must round-trip a value with an
    # internal 2+-space run (kept whole) and a blank value ("") through
    # parse_settings_dump — so --sim exercises the T8 parser, not just fixtures.
    # This FAILS if the C1 position-aware split is reverted to the v0.10.1 regex.
    tr = make_transport()
    tr.exec_command("login tpsreport")
    settings, _ = parse_settings_dump(tr.exec_command("set"))
    # the 2+-space description stays whole AND the internal-2-space value stays
    # whole (the v0.10.1 regex would truncate the desc and pollute the value)
    assert settings["simwideval"]["desc"] == "Synthetic  wide  value"
    assert settings["simwideval"]["value"] == "85 MPH  ( 137 KPH )"
    assert settings["simblankval"]["value"] == ""
    assert settings["simblankval"]["desc"] == "Synthetic blank value"


# D5: every setting name in the REAL 2017 FXS rev-41 post-login `set` (020_set.txt)
REAL_REV41_SET_NAMES = (
    "model_year", "serial", "vin", "firmware_rev", "board_id", "model",
    "spfront", "sprear", "rwhcirc", "kill", "secidle", "drive_mode",
    "maxcustsprpm", "maxcustspmph", "maxcustspkph", "maxcusttqx10",
    "maxcusttq_allowed", "maxcustregcotqx10", "maxcustregcotq_allow",
    "maxcustregbrtqx10", "maxcustregbrtq_allow", "is_dnr_board", "region",
)


def test_sim_post_login_covers_real_rev41_names():
    # D5 (review FID-4): a --sim rehearsal must expose every name the real rev-41
    # post-login dump shows, so the Writes tab / options browser look the same at
    # the desk as on the bike (previously the sim was missing 7 real names).
    tr = make_transport()
    tr.exec_command("login tpsreport")
    settings, _ = parse_settings_dump(tr.exec_command("set"))
    missing = [n for n in REAL_REV41_SET_NAMES if n not in settings]
    assert not missing, "sim is missing real rev-41 names: %s" % missing


def test_sim_eeprom_gated_at_level0_but_obd_in_help():
    # D5: bare eeprom is a logged-in-only menu item on the real bike; obd is listed
    # at BOTH login levels.
    tr = make_transport()
    assert "unknown command" in tr.exec_command("eeprom").lower()   # level 0: gated
    assert "obd" in tr.exec_command("help").lower()                 # help lists obd
    tr.exec_command("login tpsreport")
    assert "eeprom usage" in tr.exec_command("eeprom").lower()      # post-login read


def test_dump_streams_and_reports_progress():
    tr = make_transport()
    seen = []
    big = tr.exec_command("dumpall", idle_timeout=3.0, max_time=60.0,
                          progress_cb=lambda n: seen.append(n))
    assert len(big) > 100_000
    assert len(seen) > 3


def test_write_requires_login_then_verifies_and_reverts():
    tr = make_transport()
    # a write must go through write_setting(), never a raw `set` command
    with pytest.raises(BlockedCommandError):
        tr.exec_command("set spfront 22")
    # D2: not logged in, the tunables aren't in the level-0 dump, so write_setting
    # refuses spfront at the transport layer (before the wire) rather than letting
    # the console deny it.
    with pytest.raises(BlockedCommandError):
        tr.write_setting("spfront", "22")
    assert "logged in" in tr.exec_command("login tpsreport").lower()
    tr.write_setting("spfront", "22")
    s2, _ = parse_settings_dump(tr.exec_command("set"))
    assert first_number(s2["spfront"]["value"]) == "22"
    tr.write_setting("spfront", "20")
    s3, _ = parse_settings_dump(tr.exec_command("set"))
    assert first_number(s3["spfront"]["value"]) == "20"


def test_write_setting_refuses_name_absent_from_live_dump():
    # D2 (review SAFE-1-fidelity): a whitelisted name the live `set` dump does NOT
    # contain must be refused at the TRANSPORT layer — even for a scripted caller
    # that never goes through the GUI's pre-flight. (On rev 41, brakeregen is
    # whitelisted but never appears in the dump.)
    tr, port = make_transport_with_port()
    port._settings.pop("brakeregen", None)          # model this bike not exposing it
    assert "brakeregen" in WRITE_WHITELIST           # it IS whitelisted...
    tr.exec_command("login tpsreport")
    with pytest.raises(BlockedCommandError, match="not in the current settings dump"):
        tr.write_setting("brakeregen", "Yes")        # ...but absent -> refused
    # a name that IS in the live dump still writes fine
    assert "spfront set to 22" in tr.write_setting("spfront", "22").lower()


def test_write_setting_fails_closed_on_unparseable_dump():
    # review D2-FAILOPEN: if the live `set` read yields no parseable dump (cold
    # cache stays None), the write must be REFUSED — never slip through the
    # cold-cache path onto the wire.
    tr = Transport(PromptOnlyPort(),
                   SessionLogger(base_dir=tempfile.mkdtemp(prefix="zfc_"), tag="t"))
    with pytest.raises(BlockedCommandError, match="could not read/parse"):
        tr.write_setting("spfront", "22")
    assert b"set spfront 22" not in tr.port.written   # nothing reached the wire


def test_logout_invalidates_write_cache():
    # review D2-STALE-CACHE-LOGOUT: after logout the cache must not still hold the
    # logged-in names; a later write of a tunable is re-checked against the (now
    # level-0) dump and refused.
    tr = make_transport()
    tr.exec_command("login tpsreport")
    tr.exec_command("set")                           # cache the post-login names
    assert tr.known_setting_names and "spfront" in tr.known_setting_names
    tr.exec_command("logout")
    assert tr.known_setting_names is None            # invalidated on logout
    with pytest.raises(BlockedCommandError, match="not in the current settings dump"):
        tr.write_setting("spfront", "22")            # level-0 re-read: spfront absent


# --- A1: control characters / newline injection -----------------------------

@pytest.mark.parametrize("cmd", ["version\nsettingsrst", "version\r\nformat eeprom",
                                 "status\rreset", "help\x03"])
def test_control_char_injection_refused_at_transport(cmd):
    tr, port = make_transport_with_port()
    before = port.written
    with pytest.raises(BlockedCommandError):
        tr.exec_command(cmd)
    assert port.written == before        # nothing reached the wire


def test_command_blocked_screens_every_line():
    assert command_blocked("status\nsettingsrst") is not None
    assert command_blocked("version\r\nformat eeprom") is not None
    # even an all-reads multi-line is refused (one command per line)
    assert command_blocked("status\nbms") is not None


# --- A2: writes only via the gated path, values validated -------------------

def test_raw_set_write_refused_even_after_login():
    tr = make_transport()
    tr.exec_command("login tpsreport")
    with pytest.raises(BlockedCommandError):
        tr.exec_command("set maxcustregcotq_allow 0")     # would be a raw write
    with pytest.raises(BlockedCommandError):
        tr.exec_command("set spfront 22")


def test_write_setting_validates_value():
    tr = make_transport()
    tr.exec_command("login tpsreport")
    with pytest.raises(BlockedCommandError):
        tr.write_setting("maxcustregcotq_allow", "0")     # fishtail guard
    with pytest.raises(BlockedCommandError):
        tr.write_setting("spfront", "999")          # out of range
    with pytest.raises(BlockedCommandError):
        tr.write_setting("spfront", "14 56")        # extra token
    assert "spfront set to 22" in tr.write_setting("spfront", "22").lower()


# --- A3: two-token `set <name>` fails closed for unknown names --------------

def test_two_token_set_refused_entirely():
    # T6: the 2-token `set <name>` form is refused for EVERY name (its no-value
    # behavior is unverified on rev 41 and could be a prompt-for-value = a write)
    assert command_blocked("set secidle") is not None       # not whitelisted
    assert command_blocked("set debug_level") is not None
    assert command_blocked("set spfront") is not None       # whitelisted, still refused
    assert command_blocked("set sprear") is not None


def test_write_setting_rejects_empty_or_multitoken_value():
    # T5: write_setting must never emit anything but `set NAME VALUE`; an empty
    # value degenerates to the refused 2-token form and a multi-token value/name
    # is not a single sanctioned write. Assert the T5-SPECIFIC message so this
    # can't be masked by T6's command_blocked refusal of the same string (which
    # would let the test pass even with the write_setting guard reverted).
    tr = make_transport()
    tr.exec_command("login tpsreport")
    for bad in ("", "   ", "22 33"):
        with pytest.raises(BlockedCommandError, match="single token"):
            tr.write_setting("spfront", bad)
    with pytest.raises(BlockedCommandError, match="single token"):
        tr.write_setting("sp front", "22")                  # multi-token name
    # R7: a Unicode-digit value passes the single-token check AND int()-validates
    # (int('٢٢') == 22), but must still be refused before it reaches the
    # wire as '??' — the transport's non-ASCII guard catches it.
    with pytest.raises(BlockedCommandError, match="non-ASCII"):
        tr.write_setting("spfront", "٢٢")
    assert "spfront set to 22" in tr.write_setting("spfront", "22").lower()


# --- A4: long-command truncation marker + resync ----------------------------

def test_truncated_read_marks_incomplete():
    # chunks start after the 0.1 s flush-drain window, then the stream goes
    # silent with no closing prompt -> the read is marked truncated.
    port = TimedPort([(0.2, b"dumpall\r\n"),
                      (0.22, b" partial data with no closing prompt\r\n")])
    tr = Transport(port, SessionLogger(base_dir=tempfile.mkdtemp(prefix="zctr_"),
                                       tag="t"))
    out = tr.exec_command("dumpall", idle_timeout=0.15, max_time=3.0)
    assert "TRUNCATED" in out
    assert tr.last_truncated is True


def test_next_command_after_truncation_is_clean():
    port = SimPort()
    tr = Transport(port, SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcrs_"),
                                       tag="t"))
    # leftover stream still on the wire from a prior truncated read
    port._push(b" LEFTOVER ride line odo 6120km\r\n\r\nZERO MBB> ")
    tr.last_truncated = True
    out = tr.exec_command("version")
    assert "Firmware Rev" in out
    assert "LEFTOVER" not in out          # resync swallowed it


# --- A5: a mid-stream 'MBB>' must not truncate a read -----------------------

def test_prompt_in_data_does_not_truncate():
    chunks = [
        b"status\r\n",
        b" some log line\r\n",
        b" sneaky tail ends in MBB>\r\n",       # prompt-like, mid-stream
        b" real payload after the fakeout\r\n",
        b"\r\nZERO MBB> ",                       # the real start-of-line prompt
    ]
    tr = Transport(ScriptedPort(chunks),
                   SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcp_"), tag="t"))
    out = tr.exec_command("status", idle_timeout=2.0)
    assert "real payload after the fakeout" in out
    assert "TRUNCATED" not in out
    assert tr.last_truncated is False


# --- A6: password redaction is session-scoped -------------------------------

def test_redaction_is_session_scoped():
    tr, port = make_transport_with_port()
    tr.exec_command("login tpsreport", redact="tpsreport")
    # a late echo of the password arrives during the NEXT command's drain
    port._push(b"login tpsreport (late echo)\r\n")
    tr.exec_command("version")
    raw = open(tr.logger.raw_path, encoding="utf-8", errors="replace").read()
    assert "tpsreport" not in raw
    for path in glob.glob(os.path.join(tr.logger.dir, "*.txt")):
        assert "tpsreport" not in open(path, encoding="utf-8", errors="replace").read()


# --- C1: garbage / prompt detection (probe safety) --------------------------

def test_nonprintable_ratio():
    assert nonprintable_ratio(b"") == 0.0
    assert nonprintable_ratio(b"ZERO MBB> \r\n") == 0.0
    assert nonprintable_ratio(bytes([0x80, 0x81, 0xff, 0x02, 0x9c])) == 1.0
    assert 0.4 < nonprintable_ratio(b"ok\x80\x81\xff") < 0.7   # 2 of 5 printable


def test_looks_like_prompt_accepts_real_prompt():
    assert looks_like_prompt(b"\r\nZERO MBB> ")
    assert looks_like_prompt(b"boot banner\r\nMBB> ")
    assert looks_like_prompt(b"some line\r\nfoo>")     # printable, ends in '>'


def test_looks_like_prompt_rejects_garbage():
    # wrong-baud noise that happens to contain 0x3e ('>') must NOT count
    garbage = bytes([0x80, 0x3e, 0xff, 0x01, 0x3e, 0x9c, 0xe2, 0x3e, 0x00])
    assert not looks_like_prompt(garbage)
    assert not looks_like_prompt(b"")
    assert not looks_like_prompt(b"plain text no prompt here")


# --- C4: Stage-1 listen never transmits -------------------------------------

def test_listen_never_transmits():
    tr, port = make_transport_with_port()
    tr.listen(0.1)
    assert port.written == b""          # a listen must be TX-silent


# --- T13 (A4): prove _resync actually resynchronizes ------------------------

def test_resync_drains_leftover_until_prompt():
    # a leftover that keeps ARRIVING over time (past a 0.3 s flush window) must be
    # drained by _resync up to the start-of-line prompt. A no-op _resync fails
    # this: the chunks would still be readable afterward.
    port = TimedPort([(0.0, b" LEFTOVER 1\r\n"),
                      (0.3, b" LEFTOVER 2\r\n\r\nZERO MBB> ")])
    tr = Transport(port, SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcrs2_"),
                                       tag="t"))
    tr._resync(max_time=5.0)
    assert port.read() == b""        # everything up to the prompt was consumed


def test_exec_command_resyncs_when_last_truncated(monkeypatch):
    tr, _ = make_transport_with_port()
    calls = []
    monkeypatch.setattr(tr, "_resync", lambda *a, **k: calls.append(1))
    tr.last_truncated = True
    tr.exec_command("version")
    assert calls == [1]              # resync invoked before the next command
    assert tr.last_truncated is False


# --- T14 (A5): a mid-stream 'MBB>' + real idle lull must not truncate --------

def test_prompt_in_data_with_idle_lull_not_truncated():
    # a data line ending 'MBB>' followed by a REAL idle gap: the old anywhere-match
    # PROMPT_RE would see 'MBB>' at the buffer end during the lull and truncate;
    # the start-of-line PROMPT_LINE_RE waits for the real prompt.
    port = ArmedTimedPort([
        (0.0,  b"status\r\n"),
        (0.02, b" a log line that ends in MBB>\r\n"),   # sneaky, at EOL
        (0.4,  b" real payload after the gap\r\n\r\nZERO MBB> "),
    ])
    tr = Transport(port, SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcp2_"),
                                       tag="t"))
    out = tr.exec_command("status", idle_timeout=2.0)
    assert "real payload after the gap" in out
    assert "TRUNCATED" not in out


# --- D4: journal PENDING intent line ----------------------------------------

def test_journal_pending_status():
    tr = make_transport()
    tr.logger.journal_write("spfront", "20", "22", ok=None)     # intent
    tr.logger.journal_write("spfront", "20", "22", ok=True)     # verified
    text = open(tr.logger.journal_path, encoding="utf-8").read()
    assert "PENDING" in text and "VERIFIED" in text


# --- D6: mid-session reboot + asleep detection ------------------------------

def test_reboot_banner_raises():
    chunks = [b"status\r\n",
              b" - Checking EEPROM ...... Okay\r\nReset Source: Power-On\r\n"
              b"\r\nZERO MBB> "]
    tr = Transport(ScriptedPort(chunks),
                   SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcrb_"), tag="t"))
    with pytest.raises(ConsoleRebootError):
        tr.exec_command("status", idle_timeout=1.0)


def test_silent_console_raises_quiet():
    # a known read that gets NOTHING back -> a distinct, actionable error
    tr = Transport(ScriptedPort([]),        # armed on write, but no response ever
                   SessionLogger(base_dir=tempfile.mkdtemp(prefix="zcq_"), tag="t"))
    with pytest.raises(ConsoleQuietError, match="asleep"):     # cold: never replied
        tr.exec_command("status", idle_timeout=0.5)


def test_quiet_error_distinguishes_awake_console():
    # D4: once ANY read has succeeded, a later empty reply is command-specific
    # (e.g. `obd` may not print on this firmware), NOT "console asleep / keyed off".
    tr = make_transport()
    assert tr.exec_command("version")           # a real sim reply -> saw_any_response
    tr.port = ScriptedPort([])                  # now the wire goes silent
    with pytest.raises(ConsoleQuietError, match="other reads succeeded earlier"):
        tr.exec_command("status", idle_timeout=0.5)


def test_validators():
    assert not _v_coast_regen("0")[0]           # fishtail guard
    assert _v_coast_regen("6")[0]
    assert not WRITE_WHITELIST["maxcustspmph"][3]("103")[0]
    assert WRITE_WHITELIST["noregenstopped"][4]("No") is not None


def test_int_validator_requires_plain_digits():
    # D1 (review FID-1): int() accepts '1_0'(=10)/'+22'/Unicode digits, but the RAW
    # string reaches the wire, so a value that validates one way and the console
    # parses another would journal a false VERIFIED. Require plain decimal digits.
    v = WRITE_WHITELIST["spfront"][3]                  # _v_int_range(10, 40)
    assert v("22")[0]                                  # a plain value still passes
    assert v(" 22 ")[0]                                # surrounding whitespace is stripped
    for bad in ("1_0", "+22", "2 2", "0x14", "٢٢", " 2_2"):
        assert not v(bad)[0], bad                      # all non-plain forms refused
    assert not v("9")[0] and not v("41")[0]            # range checks unchanged


def test_session_files_written():
    tr = make_transport()
    tr.exec_command("status")
    tr.exec_command("bms")
    assert os.path.isfile(tr.logger.raw_path)
    assert len(os.listdir(tr.logger.dir)) >= 2
