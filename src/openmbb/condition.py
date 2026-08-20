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

import datetime as _dt
import re

from . import parsers

# The one capacity measurement on this platform that does NOT go through the SOC
# display. Charge accepted between two fixed pack voltages is a physical
# quantity: across the 2026-06-13 firmware update on the reference bike it moved
# ~3% while the displayed scale moved ~27%, so this survives a firmware change
# and anything referenced to the gauge does not. That property is what makes it
# usable on a bike whose firmware you do not know.
#
# The window is deliberately narrow and well inside the pack's range: wide enough
# to be a real measurement, narrow enough that most charge sessions traverse it
# completely. It covers roughly the top-middle of the pack, NOT the whole of it,
# so the figure is a comparable index, not the pack's total capacity.
CAPACITY_WINDOW_V = (103.0, 113.0)

# Charging samples arrive about every 10 minutes; anything longer than this is a
# gap in the record rather than an interval to integrate across.
_MAX_STEP_S = 1800.0
_SESSION_GAP_S = 3600.0
_TS_FMT = "%m/%d/%Y %H:%M:%S"

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


def _when(rec):
    try:
        return _dt.datetime.strptime(rec.get("ts") or "", _TS_FMT)
    except ValueError:
        return None


def _sessions(records):
    """Charge records grouped into sessions, split on gaps in the record."""
    stamped = sorted(((_when(r), r) for r in records if _when(r)), key=lambda p: p[0])
    out, cur = [], []
    for i, (t, r) in enumerate(stamped):
        if cur and (t - stamped[i - 1][0]).total_seconds() > _SESSION_GAP_S:
            out.append(cur)
            cur = []
        cur.append((t, r))
    if cur:
        out.append(cur)
    return out


def charge_capacity(charge_records, window=CAPACITY_WINDOW_V):
    """Amp-hours the pack accepted between two fixed pack voltages.

    Gauge-independent by construction — it reads only pack voltage, pack current
    and the clock, so a firmware change that relabels the SOC display cannot move
    it. Returns None when no charge session in the capture traverses the whole
    window, which is the common case for a short capture and must be reported as
    "could not determine" rather than as a healthy result.

    Only intervals with BOTH endpoints inside the window are counted, so the
    figure runs a few percent under the true window charge. That bias is
    identical for every bike and every firmware measured this way, which is what
    matters for comparing one against another.
    """
    lo, hi = window
    per_session = []
    for sess in _sessions(charge_records):
        volts = [r.get("vpack") for _t, r in sess if r.get("vpack") is not None]
        if not volts or min(volts) > lo or max(volts) < hi:
            continue                      # never traversed the window
        ah = 0.0
        for (ta, a), (tb, b) in zip(sess, sess[1:]):
            va, vb = a.get("vpack"), b.get("vpack")
            ia, ib = a.get("battamps"), b.get("battamps")
            if None in (va, vb, ia, ib):
                continue
            if not (lo <= va <= hi and lo <= vb <= hi):
                continue
            secs = (tb - ta).total_seconds()
            if not 0 < secs <= _MAX_STEP_S:
                continue
            ah += abs((ia + ib) / 2.0) * secs / 3600.0
        if ah > 0:
            per_session.append((ah, sess[0][0]))
    if not per_session:
        return None
    vals = sorted(a for a, _t in per_session)
    return {
        "window_v": [lo, hi],
        "sessions": len(vals),
        "median_ah": round(vals[len(vals) // 2], 2),
        "min_ah": round(vals[0], 2),
        "max_ah": round(vals[-1], 2),
        "first_session": min(t for _a, t in per_session).strftime(_TS_FMT),
        "last_session": max(t for _a, t in per_session).strftime(_TS_FMT),
        "gauge_independent": True,
    }


def assess(event_log):
    """Everything this module can say about a pack, from one event log.

    Every check that could not be answered is named in `undetermined` with the
    reason, because the caller is standing next to a bike they do not know: a
    check that silently returns nothing reads as a pass, and that is the one
    failure mode this module exists to avoid.

    There is no score and no verdict. Two of the three measurements have no
    reference to be judged against yet — one healthy bike is not a baseline —
    and the third is an index, not a capacity. What this returns is what was
    measured, what it was measured over, and what could not be measured at all.
    """
    rides = parsers.parse_ride_log(event_log)
    charges = parsers.parse_charge_log(event_log)
    first, last = log_coverage(rides)

    resets = stats_reset_events(event_log)
    sag = cell_sag(rides)
    der = derate_profile(rides)
    cap = charge_capacity(charges)

    undetermined = []
    if not rides:
        undetermined.append("no riding samples in this capture — pull the event "
                            "log (eventlogdump) to answer anything here")
    if sag is None and rides:
        undetermined.append("weakest cell under load: no sample carried both a "
                            "MinCell reading and real current")
    if der is None and rides:
        undetermined.append("discharge allowance: this firmware does not print a "
                            "current limit on its riding lines")
    if cap is None:
        undetermined.append("charge capacity: no charge session in this capture "
                            "crossed the whole %g-%g V window"
                            % (CAPACITY_WINDOW_V[0], CAPACITY_WINDOW_V[1]))
    if not resets:
        undetermined.append("whether the statistics were ever reset: none found, "
                            "but this log only reaches back to %s, so a reset "
                            "before that would not appear" % (first or "an unknown date"))

    return {
        "coverage": {"first": first, "last": last,
                     "ride_samples": len(rides), "charge_samples": len(charges)},
        "stats_resets": resets,
        "cell_sag": sag,
        "derate": der,
        "charge_capacity": cap,
        "undetermined": undetermined,
    }
