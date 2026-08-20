"""Condition checks: what one capture can honestly say about a pack.

Built for a specific job — standing next to a bike you do not know, deciding
whether its battery is sound, with no baseline to compare against and nobody to
ask. That job sets the rules this module follows:

  Nothing is scored on absence. A field the capture did not include is reported
  as undetermined, never quietly treated as a pass. A bike on unknown firmware
  will be missing things, and a verdict that hides that is worse than none.

  Nothing is graded without a reference. Measured against a healthy 2017 FXS,
  the BMS allowed less than full discharge current on 70% of ride samples — and
  on 90% of the samples taken at a cool pack and mid state of charge. A check
  that flagged "current held back" would therefore fail a healthy bike. Until
  there are captures from more than one bike to calibrate against, these
  functions DESCRIBE and leave the judgement to the reader.

  Sag beats spread. Cell balance at rest moves with state of charge — the same
  healthy pack reads 2 mV of spread at 96% and 11 mV at 45% — so resting spread
  is a weak test. The weakest cell under real current is a better one, and the
  ride log carries it per sample.

  Capacity is deliberately absent. On this platform a usable-capacity or
  state-of-health figure has to be derived against displayed SOC, and what
  displayed SOC means across a firmware change is an open question (HANDOFF.md
  8.4). A confident wrong capacity number is the most expensive thing this
  module could produce, so it produces none.

Pure by construction, like report.py: records and captured text in, JSON-ready
dicts out. No hardware, no serial port, no GUI.
"""

import re

# A firmware update is logged as a bootloader entry, and the statistics the bike
# keeps may not survive it. On the bike this was written against, the entry
# immediately after the bootloader line was "Stats Read Failed, Resetting All
# Stats to Defaults" — so every lifetime figure that bike reports dates from that
# morning, not from its build date. On a used bike that is the difference between
# "never been hot" and "not hot since someone wiped the counters".
_RESET_RE = re.compile(r"resett?ing\s+all\s+stats|stats\s+read\s+failed", re.I)
_BOOTLOADER_RE = re.compile(r"entering\s+bootloader", re.I)
_TS_RE = re.compile(r"\d\d/\d\d/\d{4}\s+\d\d:\d\d:\d\d")

# Below this the sample is not really under load, so the weakest cell is not
# being tested. Sag has to be read at current or it means nothing.
LOADED_AMPS = 50.0


def _ts_of(line):
    m = _TS_RE.search(line or "")
    return m.group(0) if m else None


def log_coverage(records):
    """(first, last) ride-sample timestamps, or (None, None).

    Every other answer here is bounded by this window. The event log is a
    rolling buffer: the 2026-08-19 capture from the reference bike reached back
    only to 06/24, which is AFTER that bike's firmware update, so "no reset
    found" in it means nothing on its own. Report the window with the finding.
    """
    stamped = [r.get("ts") for r in records if r.get("ts")]
    return (stamped[0], stamped[-1]) if stamped else (None, None)


def stats_reset_events(event_log):
    """Every point in an event log where the bike's statistics were reset.

    Newest last. `when` is None for entries the console logged without a clock,
    which is normal right after a reboot — the event is still real. Callers must
    pair an empty result with log_coverage(): absence of a reset in a window that
    does not reach back far enough is not evidence of no reset.
    """
    out = []
    prev_boot = None
    for line in (event_log or "").splitlines():
        if _BOOTLOADER_RE.search(line):
            prev_boot = _ts_of(line)
        if _RESET_RE.search(line):
            out.append({"when": _ts_of(line) or prev_boot,
                        "line": line.strip(),
                        "bootloader": prev_boot})
    return out


def cell_sag(records):
    """The weakest cell seen under real load, and the conditions it happened in.

    Descriptive, not graded: what counts as too low for this chemistry has not
    been established from more than one bike. None when the capture has no
    loaded samples carrying a MinCell reading — older firmware may not print
    one, and a log with no hard riding cannot answer the question either way.
    """
    loaded = [r for r in records
              if isinstance(r.get("battamps"), (int, float))
              and r["battamps"] >= LOADED_AMPS
              and isinstance(r.get("mincell_mv"), (int, float))]
    if not loaded:
        return None
    worst = min(loaded, key=lambda r: r["mincell_mv"])
    return {
        "min_cell_mv": worst["mincell_mv"],
        "at_amps": worst.get("battamps"),
        "at_soc_pct": worst.get("soc"),
        "at_pack_temp_c": worst.get("pack_temp_c"),
        "when": worst.get("ts"),
        "loaded_samples": len(loaded),
        "graded": False,
    }


def derate_profile(records):
    """How the BMS's discharge allowance was distributed over the capture.

    The percentage is the datum: 100 means the pack was allowed everything it
    asked for. Reported as a distribution rather than a pass/fail, because on the
    one healthy bike measured so far the allowance sat below 100 on most samples
    — see the module docstring. What a second bike is for is turning this from a
    description into a test.
    """
    pcts = [r["curr_limit_pct"] for r in records
            if isinstance(r.get("curr_limit_pct"), (int, float))]
    if not pcts:
        return None
    worst = min((r for r in records
                 if isinstance(r.get("curr_limit_pct"), (int, float))),
                key=lambda r: r["curr_limit_pct"])
    buckets = {}
    for p in pcts:
        key = "%d-%d" % (min(int(p) // 10 * 10, 100), min(int(p) // 10 * 10 + 9, 109))
        buckets[key] = buckets.get(key, 0) + 1
    ordered = sorted(pcts)
    return {
        "samples": len(pcts),
        "median_pct": ordered[len(ordered) // 2],
        "worst_pct": ordered[0],
        "worst_at_pack_temp_c": worst.get("pack_temp_c"),
        "worst_at_soc_pct": worst.get("soc"),
        "worst_when": worst.get("ts"),
        "buckets": buckets,
        "graded": False,
    }
