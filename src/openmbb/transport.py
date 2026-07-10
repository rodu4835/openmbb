"""Serial transport, session logging, and settings-dump parsing.

Works over real pyserial or the simulator (sim.SimPort) — anything with
read/write/close/in_waiting. Enforces the safety blocklist on every send and
saves every command response to its own session file.
"""

import datetime as _dt
import os
import re
import threading
import time

from .safety import BlockedCommandError, command_blocked


class ConsoleRebootError(RuntimeError):
    """The MBB rebooted mid-session (its boot banner appeared unsolicited)."""


class ConsoleQuietError(RuntimeError):
    """A known read command got no response at all (console asleep / keyed off)."""

BAUD = 38400
NEWLINE = b"\r\n"
PROMPT_RE = re.compile(rb"ZERO MBB>\s*$|MBB>\s*$")
# The prompt only counts as "response complete" when it stands at the START of a
# line (the console prints it on its own line). Matching "MBB>" anywhere would
# let a log line that merely ends in "MBB>" truncate a ~1 MB dump mid-stream.
PROMPT_LINE_RE = re.compile(rb"(?:^|[\r\n])[ \t]*(?:ZERO )?MBB>[ \t]*$")

READ_COMMANDS = ["version", "help", "status", "stats", "runtime", "bms",
                 "sevcon", "chargers", "inputs", "outputs", "dash", "obd"]
# `obd` ("Show all obd info") is a read, present in the rev-41 menu at BOTH login
# levels (003_help.txt / 019_help.txt). D4: it is menu-verified but its OUTPUT has
# not been captured live yet, so the FULL BASELINE runs it LAST (after `set` +
# errorlogdump) — a stall or empty reply can't then delay the settings backup.
# `dumplogs` is NOT a real rev-41 command - the bike replies "invalid command"
# (confirmed on the 2017 FXS). The real log reads are `eventlogdump` (HEAVY, see
# below) and `errorlogdump` (small, ~1 KB). Only the small one is safe to auto-run
# in the FULL BASELINE.
DUMP_COMMANDS = ["errorlogdump"]
# HEAVY reads: at 38400 baud these stream for MINUTES. Dumping the full event log
# (~8600 entries / ~1 MB) monopolizes the MBB long enough that it stops servicing
# the Sevcon/CAN bus, so the BMS protectively OPENS THE DRIVETRAIN CONTACTOR
# (observed live: audible click + flashing dash, recovered when the read finished).
# These are NEVER auto-run - the GUI gates them behind an explicit confirm dialog.
HEAVY_COMMANDS = ["eventlogdump", "dumpall"]
# commands that get the long dump-class read timeouts + truncation/resync handling
LONG_COMMANDS = set(DUMP_COMMANDS) | set(HEAVY_COMMANDS)


class SessionLogger:
    def __init__(self, base_dir=None, tag="session"):
        # microsecond precision so two connects in the same second (a fast
        # failed-probe retry) never share a folder and overwrite each other
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        # sessions always live in a self-contained "openmbb-sessions" folder
        # under the chosen base (or cwd), so picking any directory never
        # scatters timestamped folders loose into it.
        root = os.path.join(base_dir or os.getcwd(), "openmbb-sessions")
        self.dir = os.path.join(root, "%s_%s" % (stamp, tag))
        os.makedirs(self.dir, exist_ok=True)
        self.raw_path = os.path.join(self.dir, "session_raw.log")
        self.journal_path = os.path.join(self.dir, "writes_journal.txt")
        self._seq = 0
        self._lock = threading.Lock()
        # Substrings (e.g. a typed password) masked on EVERY write to disk, for
        # the life of the session - not just the one command that supplied it.
        # A late echo caught by a later command's drain is masked too.
        self.redactions = set()

    def add_redaction(self, secret):
        if secret:
            self.redactions.add(secret)

    def _mask(self, text):
        for secret in self.redactions:
            text = text.replace(secret, "****")
        return text

    def _ts(self):
        return _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def raw(self, direction, data):
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        text = self._mask(text)
        with self._lock, open(self.raw_path, "a", encoding="utf-8") as f:
            for line in text.splitlines(keepends=True):
                f.write("[%s] %s %r\n" % (self._ts(), direction, line))

    def save_command(self, cmd, response):
        cmd, response = self._mask(cmd), self._mask(response)
        with self._lock:
            self._seq += 1
            seq = self._seq
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", cmd)[:40] or "cmd"
        path = os.path.join(self.dir, "%03d_%s.txt" % (seq, slug))
        with open(path, "w", encoding="utf-8") as f:
            f.write("# command: %s\n# time: %s\n\n%s" % (cmd, self._ts(), response))
        return path

    def save_named(self, name, content):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._mask(content))
        return path

    def journal_write(self, name, old, new, ok):
        # ok=None -> PENDING (intent journaled BEFORE the write reaches the wire)
        status = "PENDING" if ok is None else ("VERIFIED" if ok else "UNVERIFIED")
        line = "%s | %s | %s -> %s | %s\n" % (
            _dt.datetime.now().isoformat(timespec="seconds"),
            name, old, new, status)
        with self._lock, open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(line)


class Transport:
    """Sends commands, streams responses with idle-timeout, enforces blocklist."""

    def __init__(self, port, logger):
        self.port = port          # object with .read(n)/.write(bytes)/.in_waiting
        self.logger = logger
        self.lock = threading.Lock()
        self.last_saved_path = None
        # set True when a read ended without a start-of-line prompt (a truncated
        # or aborted stream); the next command resynchronizes the wire first so
        # leftover bytes can't be misattributed to it.
        self.last_truncated = False
        # D2: names seen in the most recent full `set` dump. None = no dump read
        # yet this login level. write_setting refuses a name that isn't here, so a
        # scripted caller can't write a setting the bike never exposes. Invalidated
        # on login (the level change reveals a different set of settings).
        self.known_setting_names = None
        # D4: flips True once any command gets a non-empty reply — lets a later
        # empty read distinguish "console asleep" from "this command is silent".
        self.saw_any_response = False

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass

    def _drain(self, quiet=0.3, max_time=2.0):
        end = time.time() + max_time
        buf = b""
        last = time.time()
        while time.time() < end:
            chunk = self.port.read(4096)
            if chunk:
                buf += chunk
                last = time.time()
            elif time.time() - last > quiet:
                break
            else:
                time.sleep(0.02)
        if buf:
            self.logger.raw("RX", buf)
        return buf

    def listen(self, seconds):
        with self.lock:
            return self._drain(quiet=seconds, max_time=seconds)

    def send_raw_newline(self):
        with self.lock:
            self.logger.raw("TX", NEWLINE)
            self.port.write(NEWLINE)
            return self._drain(quiet=0.6, max_time=3.0)

    def _resync(self, max_time=30.0):
        """Drain a still-streaming / desynchronized wire until it is quiet AND a
        start-of-line prompt is seen, so a prior truncated read cannot pollute
        the next command's response. Caller must hold self.lock."""
        end = time.time() + max_time
        last = time.time()
        buf = b""
        while time.time() < end:
            chunk = self.port.read(4096)
            now = time.time()
            if chunk:
                buf += chunk
                last = now
            elif PROMPT_LINE_RE.search(buf[-40:]) and now - last > 1.0:
                break
            elif now - last > 2.0:
                break
            else:
                time.sleep(0.02)
        if buf:
            self.logger.raw("RX", buf)

    def exec_command(self, cmd, idle_timeout=2.0, max_time=30.0, progress_cb=None,
                     redact=None, _write_ok=False):
        """Send `cmd`, return the response text. Refuses control characters and
        anything the blocklist bans. If `redact` is given, that substring (e.g. a
        typed password) is registered with the session logger and masked in
        EVERYTHING written to disk for the rest of the session — including a late
        echo caught by a later command's drain. `_write_ok` is private: only
        Transport.write_setting sets it, to permit one validated write."""
        # Fail closed on control characters: the whole string goes on the wire,
        # so an embedded CR/LF would smuggle a second (possibly blocked) command
        # past the first-token check. Single-line, 7-bit ASCII commands only.
        for ch in str(cmd):
            o = ord(ch)
            if o < 0x20 or o == 0x7f:
                raise BlockedCommandError(
                    "command contains a control character (0x%02x) - refused; "
                    "pasted / multi-line input is not allowed." % o)
            # R7: the console is 7-bit ASCII. int() accepts Unicode digits (e.g.
            # the Arabic-Indic '٢٢' validates as 22), but they cannot go on the
            # wire - previously encode(errors="replace") turned them into '??'.
            # Refuse them here so a validated write can never emit corrupted bytes.
            if o > 0x7e:
                raise BlockedCommandError(
                    "command contains a non-ASCII character (U+%04X) - refused; "
                    "the console is 7-bit ASCII." % o)
        reason = command_blocked(cmd, allow_write=_write_ok)
        if reason:
            raise BlockedCommandError(reason)
        if redact:
            self.logger.add_redaction(redact)

        head = (str(cmd).strip().split() or [""])[0].lower()
        is_dump = head in LONG_COMMANDS

        with self.lock:
            if self.last_truncated:
                self.last_truncated = False
                self._resync()
            self._drain(quiet=0.1, max_time=0.3)  # flush stale bytes
            # the guard above proved cmd is 7-bit ASCII, so encode strictly (no
            # lossy 'replace' that could put '?' bytes on the wire)
            wire = cmd.encode("ascii") + NEWLINE
            self.logger.raw("TX", wire)     # logger masks any registered secrets
            self.port.write(wire)
            buf = b""
            start = time.time()
            last = time.time()
            prompt_terminated = False
            while True:
                chunk = self.port.read(4096)
                now = time.time()
                if chunk:
                    buf += chunk
                    last = now
                    if progress_cb:
                        progress_cb(len(buf))
                else:
                    got_any = len(buf) > 0
                    # the prompt only ends the read when it stands at the start
                    # of a line; a real-hardware dump needs a longer confirming
                    # lull so a brief mid-stream pause isn't mistaken for the end
                    # (the sim delivers in one burst, so it keeps the fast lull)
                    prompt_seen = got_any and PROMPT_LINE_RE.search(buf[-40:])
                    lull = 1.5 if (is_dump and not getattr(self.port, "is_sim",
                                                           False)) else 0.15
                    if prompt_seen and now - last > lull:
                        prompt_terminated = True
                        break
                    if got_any and now - last > idle_timeout:
                        break
                    if not got_any and now - start > max(5.0, idle_timeout):
                        break
                    time.sleep(0.02)
                if now - start > max_time:
                    break
            self.last_truncated = bool(buf) and not prompt_terminated

        self.logger.raw("RX", buf)
        if buf:
            self.saw_any_response = True     # D4: the console has proven it's awake
        # D6: a mid-session reboot prints its boot banner unsolicited. NOTE: the
        # real rev-41 `version` reply ALSO ends with 'Reset Source: Power-On'
        # (it reports the last reset cause every time — verified against
        # ZERO FXS/logs/mbb-console-2026-06-21_222750.txt), so 'Reset Source:'
        # is NOT a reboot signal. The boot banner is distinguished by its power-on
        # self-test lines (' - Checking 5V Supply ...', ' - Checking EEPROM ...'),
        # which never appear in a normal command reply.
        if b"Checking EEPROM" in buf or b"Checking 5V Supply" in buf:
            raise ConsoleRebootError(
                "The MBB rebooted mid-session (boot banner seen). Login level and "
                "console state are lost — reconnect on the Connect tab before "
                "continuing.")
        # a known read that returns NOTHING means the console is asleep / keyed
        # off — a distinct, actionable error rather than a silent empty result.
        # D4: if OTHER reads already succeeded this session the console is provably
        # awake, so an empty reply is command-specific (e.g. `obd` may just not
        # print on this firmware) — say so instead of "asleep / keyed off".
        if not buf and head in READ_COMMANDS:
            if self.saw_any_response:
                raise ConsoleQuietError(
                    "No response to '%s' — the console is awake (other reads "
                    "succeeded this session), so this command may not produce "
                    "output on this firmware." % cmd)
            raise ConsoleQuietError(
                "No response to '%s' — the console may be asleep or the bike keyed "
                "off. Re-probe on the Connect tab." % cmd)
        text = buf.decode("utf-8", errors="replace")
        # strip our own echo and the trailing prompt
        lines = text.splitlines()
        if lines and lines[0].strip().endswith(cmd):
            lines = lines[1:]
        while lines and PROMPT_RE.search(lines[-1].encode()):
            lines = lines[:-1]
        result = "\n".join(lines).strip("\n")
        if self.last_truncated:
            result += ("\n### TRUNCATED: no console prompt seen before the read "
                       "ended (max_time/idle) - capture is incomplete ###")
        # D2: keep the known-settings cache current. A bare `set` dump defines the
        # set of writable names; a `login <pw>` changes the login level and so what
        # `set` would show, so it invalidates the cache (forcing a fresh read).
        stripped = str(cmd).strip()
        if stripped.lower() == "set" and result.strip():
            parsed, _ = parse_settings_dump(result)
            if parsed:
                self.known_setting_names = set(parsed)
        elif head == "login" and len(stripped.split()) >= 2:
            self.known_setting_names = None

        # spec: every command response gets its own file (transport-level, so
        # headless/scripted use is covered too, not just the GUI). The logger
        # masks any registered secrets on write.
        self.last_saved_path = self.logger.save_command(cmd, result)
        return result

    def write_setting(self, name, value, idle_timeout=2.5, max_time=30.0):
        """The ONLY sanctioned path for a `set <name> <value>` write. The value
        is validated against the whitelist (command_blocked with the write
        capability) before anything reaches the wire; blocklist and control-
        character checks still apply. Raises BlockedCommandError if refused."""
        # T5: an empty or whitespace value would tokenize to the 2-token
        # `set <name>` form (an unverified prompt-for-value on rev 41); a
        # multi-token value/name is not a single sanctioned write. Fail closed
        # so write_setting can never emit anything but exactly `set NAME VALUE`.
        if len(str(name).split()) != 1 or len(str(value).split()) != 1:
            raise BlockedCommandError(
                "write value/name must be a single token (got name=%r value=%r)."
                % (name, value))
        # D2 (review SAFE-1-fidelity): refuse a name absent from the live settings
        # dump at the TRANSPORT layer, not just in the GUI — so a headless/scripted
        # caller can't write a whitelisted-but-never-observed name (e.g. brakeregen
        # on rev 41) either. If no `set` has been read at this login level yet, read
        # one now to check. (In the GUI the write flow has already read `set`, so
        # this adds no extra traffic.)
        if self.known_setting_names is None:
            self.exec_command("set", idle_timeout=idle_timeout, max_time=max_time)
        if self.known_setting_names is not None and name not in self.known_setting_names:
            raise BlockedCommandError(
                "'%s' is not in the current settings dump - refusing to write "
                "(log in first, or your bike may not expose this setting)." % name)
        return self.exec_command("set %s %s" % (name, value),
                                 idle_timeout=idle_timeout, max_time=max_time,
                                 _write_ok=True)


_RULER_RE = re.compile(r"^\s*\+[-+]{3,}\s*$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _column_bounds(ruler):
    """Indices of every '+' in a +---+---+ ruler line = column boundaries."""
    return [i for i, ch in enumerate(ruler) if ch == "+"]


def _slice_columns(line, bounds):
    """Split `line` at the ruler's '+' positions into stripped column strings."""
    cols = []
    for i in range(len(bounds)):
        start = bounds[i]
        end = bounds[i + 1] if i + 1 < len(bounds) else len(line)
        cols.append(line[start:end].strip())
    return cols


def _split_desc_value(region, val_edge):
    """Split a columnar row's description+value `region` into (desc, value).

    T8/R11/R12: the value is RIGHT-aligned in (and may overflow LEFT of) the
    Value column, whose left edge is `val_edge` (an offset within `region`); the
    description is left-aligned to its left. They are parted by the alignment pad
    — the WIDEST run of 2+ spaces in the region. On the real rev-41 output this
    works because every description is single-spaced and every value is a single
    token, so the alignment pad is always the widest gap; splitting there handles
    a blank description (the widest gap is the leading pad, so desc=""), a wordy
    description with its own 2+-space run (narrower than the pad, so it stays in
    the desc), and a multi-token value with an internal 2-space run (also narrower
    than the pad) — none of which the v0.10.1 "first 2+-group" regex got right.

    Known limits (not reachable on rev-41 `set` output, so not defended): if a
    firmware ever emitted a multi-token value whose internal gap is >= the
    alignment pad, or an exact width tie between that gap and the pad, the split
    could land inside the value. A blank value only yields value="" while the
    firmware's trailing padding survives — if the line is right-trimmed the row
    has no 2+-space gap at all, which the no-gap branch below handles by column
    position."""
    gaps = [(m.end() - m.start(), m.start(), m.end())
            for m in re.finditer(r" {2,}", region)]
    if not gaps:
        # No 2+-space run: a single contiguous run of text (or empty). Use the
        # column position — text that ends at/before the Value column's left edge
        # lies wholly in the DESCRIPTION column (a real value is right-aligned to
        # the right of val_edge), so it is a desc with a blank value, not a value.
        stripped = region.strip()
        if stripped and len(region.rstrip()) <= val_edge:
            return stripped, ""
        return "", stripped                # a lone right-aligned value, no desc
    width = max(g[0] for g in gaps)
    # the alignment pad is the widest gap; on a (rare) tie, take the one nearest
    # the Value column's left edge so we never split inside a wide value.
    _, start, end = min((g for g in gaps if g[0] == width),
                        key=lambda g: abs(g[1] - val_edge))
    return region[:start].strip(), region[end:].strip()


def _parse_columnar_settings(lines, settings, order):
    """Parse the rev-41 columnar `set` tables: a +---+---+ ruler under a header
    row, then fixed-width data rows (name in col 0, value in the 'Value' column,
    default col index 2). Verified against the real 2017 FXS rev-41 capture."""
    i, n = 0, len(lines)
    while i < n:
        if not _RULER_RE.match(lines[i]):
            i += 1
            continue
        bounds = _column_bounds(lines[i])
        if len(bounds) < 2:
            i += 1
            continue
        # header = nearest non-blank line above the ruler; use it to find which
        # column holds the value, defaulting to col index 2 (name, desc, value).
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        hcols = [c.lower() for c in _slice_columns(lines[j], bounds)] if j >= 0 else []
        val_idx = hcols.index("value") if "value" in hcols else min(2, len(bounds) - 1)
        right = bounds[val_idx + 1] if val_idx + 1 < len(bounds) else None
        k = i + 1
        skipped = 0
        while k < n:
            row = lines[k]
            s = row.strip()
            if not s:                       # blank lines inside a table are skipped
                k += 1
                continue
            if (_RULER_RE.match(row) or s.startswith("*")
                    or PROMPT_RE.search(row.encode("utf-8", "replace"))):
                break                       # banner / next ruler / prompt ends it
            name = row[bounds[0]:bounds[1]].strip()
            if not _IDENT_RE.match(name):
                # T8/R10: a non-identifier row (a wrapped continuation line, or a
                # stray line) must NOT abort the table and drop every setting after
                # it — skip it. Bail only after several in a row (we've almost
                # certainly left the table with no prompt/ruler to stop us).
                skipped += 1
                if skipped >= 3:
                    break
                k += 1
                continue
            skipped = 0
            end = right if right is not None else len(row)
            # T8: position-aware desc/value split on the alignment pad (the widest
            # 2+-space run). `val_edge` is the Value column's left edge relative to
            # the region, used only to break a width tie. This keeps a VIN whole,
            # keeps a value with an internal 2-space run whole, keeps a wordy
            # (2+-space) description whole, and yields value="" for a blank value
            # column and desc="" for a blank description column.
            region = row[bounds[1]:end]
            val_edge = bounds[val_idx] - bounds[1]
            desc, value = _split_desc_value(region, val_edge)
            if name not in settings:
                order.append(name)
            settings[name] = {"desc": desc, "value": value, "raw": row.rstrip()}
            k += 1
        i = k


def _parse_legacy_settings(lines, settings, order):
    """Parse the 2014-2016 / simulator shape: `name - Description : value`."""
    for line in lines:
        m = re.match(r"^\s{0,4}([A-Za-z_][A-Za-z0-9_]*)\s+-\s+(.*)$", line)
        if not m:
            continue
        name, rest = m.group(1), m.group(2).rstrip()
        if name in ("help",) or name in settings:   # don't clobber columnar rows
            continue
        if ":" in rest:
            desc, _, value = rest.partition(":")
            desc, value = desc.strip(), value.strip()
        else:
            parts = rest.rsplit(None, 1)
            if len(parts) == 2:
                desc, value = parts[0].strip(), parts[1].strip()
            else:
                desc, value = rest, ""
        if name not in settings:
            order.append(name)
        settings[name] = {"desc": desc, "value": value, "raw": line.rstrip()}


def parse_settings_dump(text):
    """Tolerant parser for the `set` menu. Returns (dict name -> info, order).

    Handles TWO shapes, so it works on the real bike and the simulator alike:
      * rev-41 columnar table (real 2017 FXS): a +---+---+ ruler, then
        `name  Description  <right-aligned value>  Units` rows.
      * 2014-2016 / simulator: `spfront  - Sprocket Teeth Front : 28`.
    Columnar rows are parsed first; the legacy pass only adds names not already
    seen, so a dump in either format parses cleanly and neither clobbers.
    """
    settings, order = {}, []
    lines = (text or "").splitlines()
    _parse_columnar_settings(lines, settings, order)
    _parse_legacy_settings(lines, settings, order)
    return settings, order


def first_number(value):
    m = re.search(r"-?\d+", str(value))
    return m.group(0) if m else str(value).strip()


def nonprintable_ratio(data):
    """Fraction of bytes in `data` that are not printable text (for wrong-baud /
    Tx-Rx-swap detection). TAB/CR/LF count as printable."""
    if not data:
        return 0.0
    ok = sum(1 for b in data if 0x20 <= b < 0x7f or b in (0x09, 0x0a, 0x0d))
    return 1.0 - ok / len(data)


def looks_like_prompt(data):
    """True if `data` (bytes) plausibly ends at the console prompt: a start-of-
    line 'MBB>' OR mostly-printable text whose last non-empty line ends in '>'.
    A bare '>' inside random garbage does NOT count (that was the old bug)."""
    if not data:
        return False
    if PROMPT_LINE_RE.search(data[-64:]):
        return True
    if nonprintable_ratio(data) > 0.2:
        return False
    text = data.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.rstrip().endswith(">")
    return False


def open_real_port(port_name):
    import serial  # lazy: only needed for real hardware
    return serial.Serial(port=port_name, baudrate=BAUD, bytesize=8,
                         parity="N", stopbits=1, timeout=0.05)


def list_serial_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        # pyserial missing from the bundle — distinct from "no ports plugged in";
        # surface it instead of masquerading as an empty list
        print("WARNING: pyserial is not available — the COM-port list will be empty. "
              "Reinstall OpenMBB or `pip install pyserial`.")
        return []
    try:
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []
