"""Load saved session folders back into memory for analysis.

A session folder (written by transport.SessionLogger) holds one file per
command — `NNN_<cmd>.txt` with a `# command: <cmd>` header — plus
`settings_baseline_*.txt` / `settings_backup_*.txt` raw settings dumps.
"""

import glob
import os
import re


class Session:
    def __init__(self, folder, commands, settings_text):
        self.dir = folder
        self.name = os.path.basename(os.path.normpath(folder))
        self.commands = commands          # {command_name: response_text}
        self.settings_text = settings_text

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
    for path in sorted(glob.glob(os.path.join(folder, "*.txt")), key=_seq):
        base = os.path.basename(path)
        if base.startswith(("settings_baseline", "settings_backup")):
            continue
        cmd = _header_command(path)
        if cmd:
            commands[cmd] = _read_response(path)   # sorted => latest wins
    # prefer the clean baseline settings dump if one was captured
    settings_text = ""
    baselines = sorted(glob.glob(os.path.join(folder, "settings_baseline*.txt")))
    if baselines:
        with open(baselines[-1], encoding="utf-8", errors="replace") as f:
            settings_text = f.read()
    elif "set" in commands:
        settings_text = commands["set"]
    return Session(folder, commands, settings_text)


def list_sessions(base):
    """Newest-first list of session folders under <base>/openmbb-sessions/."""
    root = os.path.join(base or os.getcwd(), "openmbb-sessions")
    if not os.path.isdir(root):
        return []
    dirs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    return sorted(dirs, reverse=True)
