"""Load saved session folders back into memory for analysis.

A session folder (written by transport.SessionLogger) holds one file per
command — `NNN_<cmd>.txt` with a `# command: <cmd>` header — plus
`settings_baseline_*.txt` / `settings_backup_*.txt` raw settings dumps.
"""

import glob
import os
import re


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


def load_session(folder):
    """Load one session folder into a Session (latest response per command)."""
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
    baselines = sorted(glob.glob(os.path.join(folder, "settings_baseline*.txt")))
    if baselines:
        with open(baselines[-1], encoding="utf-8", errors="replace") as f:
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
