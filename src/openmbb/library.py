"""The saved-capture library: what is on disk, at a glance.

Three captures already make "Load session folder..." a memory test — the folders
are named `2026-07-10_124738_435640_COM4` and the only way to tell one from
another is to open it. This module turns a folder into a row an owner can read:
when it was taken, the odometer, the verdict, and whatever they wrote down about
it at the time.

Two costs are kept apart on purpose. The CHEAP summary parses `bms` and `stats`,
two short command outputs, and is fast enough to build for every folder the
moment a list is opened. The DEEP one re-reads the event log, about a megabyte
per capture, to reach a verdict — so it is computed on request and cached beside
the capture, because a verdict does not change once the capture is written.

Notes live in the capture folder rather than in a central file. A capture that is
copied to another machine, or handed to a maintainer, takes its note with it, and
a note is worth most exactly then: `2026-06-13 reflash` on the capture either side
of it would have saved this project a week.
"""

import hashlib
import json
import os
import time

from . import condition, health, parsers, sessions

NOTE_FILE = "session_note.txt"
SUMMARY_FILE = "session_summary.json"

# Bumped when a cached verdict would be computed differently, so an old cache is
# recomputed rather than believed.
#   2: verdicts were being computed WITHOUT the health metrics, which forced the
#      resting cell-spread check to "unknown" and dropped the isolation and
#      warning-light checks entirely - a bike the Condition tab grades "concern"
#      showed a green "ok" here, in the one column a buyer triages on.
SUMMARY_VERSION = 2


# --- notes -------------------------------------------------------------------

def read_note(folder):
    """The note written about this capture, or "" if there is none."""
    try:
        with open(os.path.join(folder, NOTE_FILE), encoding="utf-8",
                  errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def write_note(folder, text):
    """Save (or, given empty text, delete) the note for a capture."""
    path = os.path.join(folder, NOTE_FILE)
    text = (text or "").strip()
    if not text:
        try:
            os.remove(path)
        except OSError:
            pass
        return ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    return text


# --- the cheap half ----------------------------------------------------------

def _captured_at(folder, session):
    """When the capture was taken, best available.

    The folder name carries the capturing machine's clock and is the most
    trustworthy source here — the bike's own clock is the thing this project
    exists to catch being wrong.
    """
    name = os.path.basename(os.path.normpath(folder))
    parts = name.split("_")
    if len(parts) >= 2 and len(parts[0]) == 10 and len(parts[1]) == 6:
        try:
            return time.mktime(time.strptime(parts[0] + parts[1],
                                             "%Y-%m-%d%H%M%S"))
        except ValueError:
            pass
    try:
        return os.path.getmtime(folder)
    except OSError:
        return 0.0


def summarize(folder):
    """A row for the library, from `bms` and `stats` only.

    Never reads the event log. `verdict` is None here — not "ok" — because a
    verdict that has not been computed must not read as a pass.
    """
    s = sessions.load_session(folder)
    bms = parsers.parse_bms(s.cmd("bms"))
    stats = parsers.parse_stats(s.cmd("stats"))
    log = _event_log_text(s)
    return {
        "folder": folder,
        "name": s.name,
        "when": _captured_at(folder, s),
        "odo_km": stats.get("odo_km"),
        "soc_pct": bms.get("soc_pct"),
        "cycles": bms.get("cycles"),
        "note": read_note(folder),
        # a capture taken without '+event log' can never reach a verdict, and
        # saying so up front is kinder than a spinner that resolves to nothing
        "has_event_log": bool(log),
        "is_sim": s.name.endswith(("_sim", "_listen")),
        "commands": len(s.commands),
        "verdict": None,
        "verdict_headline": None,
    }


def _event_log_text(session):
    for cmd in ("eventlogdump", "dumplogs"):
        text = session.cmd(cmd) or ""
        if text.strip():
            return text
    return ""


def scan(root, limit=60):
    """Summaries for the captures under `root`, newest first.

    A folder that will not parse, or that holds no command output at all, is
    skipped rather than allowed to empty or clutter the list — one bad capture in
    a save directory should not hide the others.
    """
    try:
        names = [d for d in os.listdir(root)
                 if os.path.isdir(os.path.join(root, d))]
    except OSError:
        return []
    # mtime picks WHICH folders to look at (cheap, and close enough), but the
    # order comes from when each capture was actually taken. Copying a capture
    # onto another machine rewrites its mtime, and a library that then showed a
    # 2026-07 pull as the newest one would be lying about the only column an
    # owner uses to find things.
    names.sort(key=lambda d: os.path.getmtime(os.path.join(root, d)), reverse=True)
    out = []
    for name in names[:limit]:
        try:
            row = summarize(os.path.join(root, name))
        except Exception:
            continue
        if not row["commands"]:
            continue      # a folder with no command output is not a capture
        out.append(row)
    out.sort(key=lambda r: r["when"], reverse=True)
    return out


# --- the expensive half, cached beside the capture ---------------------------

def _log_fingerprint(log_text):
    """What the verdict was computed FROM, as a digest of the log itself.

    The module used to assume a verdict does not change once a capture is
    written. That is false: the app appends to live session folders, and a
    truncated first `eventlogdump` followed by a complete re-read leaves a
    higher-numbered file that `load_session` prefers - so a cache keyed only on
    the code version keeps serving the verdict of the partial log.

    Digesting the TEXT rather than stat-ing a filename matters, because which
    file `_event_log_text` ends up reading depends on the command headers and on
    `eventlogdump` winning over `dumplogs` - not on how the names sort. Hashing a
    megabyte costs a couple of milliseconds against the ~100 ms parse it guards.
    """
    if not log_text:
        return None
    return {"sha256": hashlib.sha256(log_text.encode("utf-8", "replace")).hexdigest(),
            "chars": len(log_text)}


_UNREAD = object()


def cached_verdict(folder, log_text=_UNREAD):
    """A verdict computed on an earlier visit, or None.

    Returns None for a cache written by an older version of the checks, or from a
    different event log, rather than trusting it — the point of caching is to
    skip re-reading a megabyte, not to freeze a judgement that the code, or the
    evidence, has since moved on from.

    The evidence check is done HERE rather than left to the caller, because a
    caller that forgets it is precisely the defect this closes. Pass `log_text`
    when you have already read the log; otherwise it is read to check.
    """
    try:
        with open(os.path.join(folder, SUMMARY_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("version") != SUMMARY_VERSION:
        return None
    if log_text is _UNREAD:
        try:
            log_text = _event_log_text(sessions.load_session(folder))
        except Exception:
            return None
    if data.get("evidence") != _log_fingerprint(log_text):
        return None
    return data


def deep_verdict(folder, use_cache=True):
    """Read the event log and reach a verdict, caching the result.

    Returns {level, headline, version} or None when the capture has no event log
    to read. The cache is written into the capture folder, so it travels with a
    copied capture and costs nothing to regenerate if it is lost.
    """
    s = sessions.load_session(folder)
    log = _event_log_text(s)
    if not log:
        return None
    if use_cache:
        hit = cached_verdict(folder, log)
        if hit is not None:
            return hit
    # The health metrics are not optional. Without them condition.verdict forces
    # the resting cell-spread check to "unknown" and never appends the isolation
    # or warning-light checks, so a bike graded "concern - walk away" on the
    # Condition tab renders as a green "ok" in this list. health_snapshot only
    # re-parses the short command outputs already loaded above.
    metrics = health.health_snapshot(s)
    v = condition.verdict(condition.assess(log), metrics)
    data = {"version": SUMMARY_VERSION, "level": v["level"],
            "headline": v["headline"], "evidence": _log_fingerprint(log)}
    # Writing into the capture folder bumps the folder's mtime, and the Charts
    # trend metrics plot each capture AT its folder mtime - so simply opening
    # this list would restamp every capture to "now" and flatten a year of
    # history into one second. Put the clock back.
    before = None
    try:
        st = os.stat(folder)
        before = (st.st_atime, st.st_mtime)
    except OSError:
        pass
    try:
        with open(os.path.join(folder, SUMMARY_FILE), "w", encoding="utf-8",
                  newline="\n") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass          # a read-only capture folder is not a reason to fail
    else:
        if before is not None:
            try:
                os.utime(folder, before)
            except OSError:
                pass
    return data
