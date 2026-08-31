"""Load saved session folders back into memory for analysis.

A session folder (written by transport.SessionLogger) holds one file per
command — `NNN_<cmd>.txt` with a `# command: <cmd>` header — plus
`settings_baseline_*.txt` / `settings_backup_*.txt` raw settings dumps.
"""

import glob
import os
import re


#: The capture format this build writes, and the highest it can read.
#:
#: Bump ONLY when an older reader would produce a WRONG answer rather than a
#: MISSING one. That is a far higher bar than "the folder changed": a new file, a
#: new key, a new sidecar are all things an old reader simply ignores, and none
#: of them is a bump. Exactly six things can make an old reader wrong, because
#: six pieces of meaning here are carried by position or convention rather than
#: by a label that an old reader could fail to find:
#:
#:   1. whose clock `# time:` records (the capturing MACHINE's, not the bike's)
#:   2. the folder-name stamp shape, which is how captures are ordered
#:   3. `NNN_` latest-wins, when one command was run more than once
#:   4. which `settings_baseline*` is authoritative (see `_newest_baseline`)
#:   5. the `_sim` / `_listen` name tags, which mark data that is NOT from a bike
#:   6. what `# command:` means as a dict key
#:
#: Raising this obliges you, in the same commit, to write either the branch that
#: reads the old format or the sentence that refuses it.
CAPTURE_FORMAT = 1

_CAPTURE_FORMAT_RE = re.compile(r"^[ \t]*capture_format:(.*)$", re.M)
_SOURCE_RE = re.compile(r"^[ \t]*source:(.*)$", re.M)
_META_TIME_RE = re.compile(r"^[ \t]*time:\s*(\S+)", re.M)

#: `<YYYY-MM-DD>_<HHMMSS>_<micro>` - the machine-written prefix every session
#: folder starts with. Everything after it is a tag, and the whole name is
#: something a person can rename.
_NAME_STAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\d)_(\d\d)(\d\d)(\d\d)_\d+")

#: The name tags that mark data that is NOT from a motorcycle. The optional
#: trailing counter is tolerated because a build before the collision fix could
#: write `..._sim_1` (see SessionLogger).
_NOT_A_BIKE_RE = re.compile(r"_(sim|listen|selftest)(?:_\d+)?$")

#: What each of those sources is, in words a reader can act on: the banner
#: shouted at the top of a report, and the phrase that reads naturally mid-line.
_SOURCE_WORDS = {
    "simulator": ("SIMULATOR DATA", "the simulator"),
    "sim": ("SIMULATOR DATA", "the simulator"),
    "listen": ("A CABLE TEST", "a cable test"),
    "selftest": ("A SELF-TEST", "a self-test"),
}


def _meta_text(folder):
    try:
        with open(os.path.join(folder, "session_meta.txt"),
                  encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def session_source(folder):
    """What this capture came off: "simulator", "listen", "selftest", a port
    name, or None when nothing on disk says.

    `source:` in session_meta.txt is authoritative. A capture written before
    that field existed falls back to the `_sim` / `_listen` NAME tags, which is
    the only evidence it carries.

    None means "nothing recorded", not "a bike". Under format 1 an untagged
    folder IS real history - that is what the trend line has always assumed -
    so a caller deciding whether to TRUST data should keep reading silence as
    real. A caller about to CLAIM something about the source must not.
    """
    m = _SOURCE_RE.search(_meta_text(folder))
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = _NOT_A_BIKE_RE.search(os.path.basename(os.path.normpath(folder)))
    if m:
        return "simulator" if m.group(1) == "sim" else m.group(1)
    return None


def not_from_a_bike(folder):
    """True only when the capture SAYS it came off something other than a
    motorcycle. Silence is False: under format 1 an untagged folder is real
    history, and every capture taken before `source:` existed is untagged."""
    return (session_source(folder) or "").lower() in _SOURCE_WORDS


def capture_identity(folder):
    """A capture label fit to print on a page handed to a stranger, plus the
    words for a banner when the page is not describing a motorcycle at all.

    Never the folder's own name. `condition_report` printed `session.name` -
    whatever the folder happens to be called - into the one artifact this
    project builds to be handed over, and the PII gate guarding that page scans
    for four MOTORCYCLE identifier shapes, so `Daves-FXS-preinspection` went
    through it clean. Filtering the name to a safe CHARSET does not fix it
    either: a person's name is already in the safe charset.

    So the label is rebuilt from what OpenMBB itself wrote down - when the
    capture was taken and what it came off - and the folder name contributes
    only its machine-written timestamp prefix, and only when the meta file is
    missing.

    Returns (label, banner_words_or_None).
    """
    src = session_source(folder)
    words = _SOURCE_WORDS.get((src or "").lower())
    banner = words[0] if words else None
    when = None
    m = _META_TIME_RE.search(_meta_text(folder))
    if m:
        when = m.group(1).strip().replace("T", " ")[:16]
    if not when:
        m = _NAME_STAMP_RE.match(os.path.basename(os.path.normpath(folder)))
        if m:
            when = "%s %s:%s" % (m.group(1), m.group(2), m.group(3))
    where = words[1] if words else src
    if when and where:
        label = "%s, from %s" % (when, where)
    elif when:
        label = when
    elif where:
        label = "from %s" % where
    else:
        label = "(not recorded)"
    return label, banner


class CaptureFormatError(Exception):
    """A capture this build must not pretend to understand."""


def capture_format(folder):
    """The format `folder` claims to be, as an int.

    A folder that makes no claim is format 1. Every capture written before the
    stamp existed is one, there is no other honest reading of silence, and it is
    true of all three real captures.

    Raises CaptureFormatError for a claim this build cannot honour: a format
    newer than it knows, or a value that is not an integer. A key that is present
    with an unreadable value is MALFORMED, not absent - something meant to state
    a version and failed - and quietly reading that as 1 would be exactly the
    false pass the stamp exists to prevent.
    """
    try:
        with open(os.path.join(folder, "session_meta.txt"),
                  encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return CAPTURE_FORMAT          # no meta file at all: a format-1 capture
    m = _CAPTURE_FORMAT_RE.search(text)
    if m is None:
        return CAPTURE_FORMAT          # meta file, but from before the stamp
    raw = m.group(1).strip()
    try:
        claimed = int(raw)
        if claimed < 1:
            # int() takes "0" and "-1" and neither is > CAPTURE_FORMAT, so this
            # would otherwise read as a fine format-0 capture. There has never
            # been a format below 1.
            raise ValueError(raw)
    except ValueError:
        raise CaptureFormatError(
            "%s says capture_format: %r, which is not a version number. The "
            "file is damaged or was not written by OpenMBB; reading it as a "
            "format-1 capture would be a guess." % (folder, raw))
    if claimed > CAPTURE_FORMAT:
        raise CaptureFormatError(
            "this capture is format %d and this OpenMBB reads up to format %d - "
            "it was written by a NEWER OpenMBB than this one. Update OpenMBB "
            "rather than trust a partial read of it." % (claimed, CAPTURE_FORMAT))
    return claimed


class Session:
    def __init__(self, folder, commands, settings_text, captured_at=None):
        self.dir = folder
        self.name = os.path.basename(os.path.normpath(folder))
        self.commands = commands          # {command_name: response_text}
        self.settings_text = settings_text
        # {command_name: "YYYY-MM-DD HH:MM:SS"} — when the capturing MACHINE ran
        # each command. The bike prints its own clock in `stats` and `bms`, so
        # holding both sides of the same instant is what makes a clock offset
        # measurable without knowing anybody's timezone.
        self.captured_at = captured_at or {}

    def cmd(self, name):
        return self.commands.get(name, "")

    def __repr__(self):
        return "Session(%s, %d commands)" % (self.name, len(self.commands))


def _header_command(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return None
    m = re.match(r"#\s*command:\s*(.+)", first)
    return m.group(1).strip() if m else None


def _header_time(path):
    """The '# time: HH:MM:SS.mmm' the logger stamps on every command file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            f.readline()
            m = re.match(r"#\s*time:\s*(\d\d:\d\d:\d\d)", f.readline())
            return m.group(1) if m else None
    except OSError:
        return None


def _session_date(folder):
    """The capture date, from session_meta.txt — the per-command headers carry a
    time but no date."""
    try:
        with open(os.path.join(folder, "session_meta.txt"),
                  encoding="utf-8", errors="replace") as f:
            m = re.search(r"time:\s*(\d{4}-\d\d-\d\d)", f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def _read_response(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    if not txt.startswith("# command:"):
        return txt
    # drop the leading '#' header line(s), then one optional blank separator —
    # tolerant of a header that is not followed by a blank line.
    lines = txt.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    if i < len(lines) and lines[i].strip() == "":
        i += 1
    return "".join(lines[i:])


def _seq(path):
    """Numeric sequence prefix of a session file, so the highest-numbered
    duplicate wins regardless of digit width (001 vs 1000)."""
    m = re.match(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else -1


_BASELINE_STAMP_RE = re.compile(r"_(\d{8}_\d{6})\.txt$")


def _newest_baseline(folder):
    """The most recent settings baseline in a folder, or None.

    Ordered by the timestamp EMBEDDED IN THE NAME, not by the name. A plain
    lexical sort compared the whole filename, and "postlogin" beats a bare
    timestamp because "p" > "2" - so an earlier post-login dump always won over a
    later plain baseline. Reachable by pulling, logging in, then pulling again,
    and both July captures already hold both kinds of file.

    This text is what the write gate re-reads and re-parses as the backup a
    write's undo depends on, so picking the wrong one is not cosmetic.

    A file with no parseable stamp sorts oldest rather than being dropped: it is
    still a baseline, and losing it entirely would be worse than ranking it low.
    On a tie the post-login dump wins, because it is the fuller of the two - it
    is the one that has the login-gated settings in it.
    """
    found = []
    for path in glob.glob(os.path.join(folder, "settings_baseline*.txt")):
        m = _BASELINE_STAMP_RE.search(os.path.basename(path))
        stamp = m.group(1) if m else ""
        postlogin = "postlogin" in os.path.basename(path).lower()
        found.append((stamp, postlogin, path))
    if not found:
        return None
    found.sort()
    return found[-1][2]


def load_session(folder):
    """Load one session folder into a Session (latest response per command).

    Raises CaptureFormatError if the folder states a format this build cannot
    read. Refusing at the loader rather than at each surface is deliberate:
    nothing can analyze what it could not load, so there is no path by which a
    capture we do not understand reaches a verdict.
    """
    capture_format(folder)          # refuses before anything is believed
    commands = {}
    captured_at = {}
    day = _session_date(folder)
    for path in sorted(glob.glob(os.path.join(folder, "*.txt")), key=_seq):
        base = os.path.basename(path)
        if base.startswith(("settings_baseline", "settings_backup")):
            continue
        cmd = _header_command(path)
        if cmd:
            commands[cmd] = _read_response(path)   # sorted => latest wins
            stamp = _header_time(path)
            if day and stamp:
                captured_at[cmd] = "%s %s" % (day, stamp)
    # prefer the clean baseline settings dump if one was captured
    settings_text = ""
    baselines = _newest_baseline(folder)
    if baselines:
        with open(baselines, encoding="utf-8", errors="replace") as f:
            settings_text = f.read()
    elif "set" in commands:
        settings_text = commands["set"]
    return Session(folder, commands, settings_text, captured_at)


def list_sessions(base):
    """Newest-first list of session folders under <base>/openmbb-sessions/."""
    root = os.path.join(base or os.getcwd(), "openmbb-sessions")
    if not os.path.isdir(root):
        return []
    dirs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    return sorted(dirs, reverse=True)
