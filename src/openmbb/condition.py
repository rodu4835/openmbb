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
    limits = parsers.parse_limit_events(event_log)
    first, last = log_coverage(rides)

    resets = stats_reset_events(event_log)
    faults = fault_history(event_log)
    sag = cell_sag(rides)
    dev = cell_deviation(rides)
    floor = cell_floor(rides, limits)
    der = derate_profile(rides)
    cap = charge_capacity(charges)

    with_cell = sum(1 for r in rides if r.get("mincell_mv") is not None)

    undetermined = []
    if not rides:
        undetermined.append("no riding samples in this capture — pull the event "
                            "log (eventlogdump) to answer anything here")
    if sag is None and rides and floor is None:
        undetermined.append(
            "weakest cell under load: no sample carried both a decodable cell "
            "voltage and real current" if with_cell else
            "weakest cell under load: NOT ONE ride record in this capture carries "
            "a decodable cell voltage. Records written before a firmware change "
            "do not survive being re-read by the newer firmware, so a reflashed "
            "bike can look silent here when it has simply not been measured")
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
                     "ride_samples": len(rides), "charge_samples": len(charges),
                     # how much of the ride log can answer the cell question at
                     # all — the rest was written by an older firmware and is
                     # not decodable, which is a coverage limit, not a pass
                     "ride_samples_with_cell": with_cell},
        "stats_resets": resets,
        "faults": faults,
        "cell_sag": sag,
        "cell_deviation": dev,
        "cell_floor": floor,
        "derate": der,
        "charge_capacity": cap,
        "undetermined": undetermined,
    }


def cell_floor(ride_records, limit_events):
    """Lowest cell voltage seen under real load, from whichever channel exists.

    An ABSOLUTE check: a lithium cell dragged low under load is stressed on any
    bike, so unlike the deviation-from-pack-average measure this needs no
    comparison at all. It reads the riding lines where the firmware puts a cell
    voltage there, and falls back to the current-limit events where it does not
    — which is what makes it survive a firmware boundary that leaves the riding
    records undecodable.

    `source` names the channel so the reader can see which evidence answered.
    """
    from_ride = [r for r in ride_records
                 if isinstance(r.get("battamps"), (int, float))
                 and r["battamps"] >= LOADED_AMPS
                 and isinstance(r.get("mincell_mv"), (int, float))]
    if from_ride:
        worst = min(from_ride, key=lambda r: r["mincell_mv"])
        return {"min_cell_mv": worst["mincell_mv"], "source": "riding samples",
                "samples": len(from_ride), "when": worst.get("ts"),
                "at_pack_temp_c": worst.get("pack_temp_c"), "graded": False}
    limited = [e for e in limit_events if e["kind"] == "discharge"]
    if limited:
        worst = min(limited, key=lambda e: e["mincell_mv"])
        # the BMS only logs these while holding current back, so the pack was
        # under genuine demand — but there is no current or pack voltage on the
        # line, so this cannot feed the deviation-from-average measure
        return {"min_cell_mv": worst["mincell_mv"],
                "source": "discharge-limit events", "samples": len(limited),
                "when": worst.get("ts"), "at_pack_temp_c": worst.get("pack_temp_c"),
                "graded": False}
    return None


# --- the verdict -------------------------------------------------------------
#
# Bands for the weakest cell's deviation below its OWN pack average under load.
# Self-referencing, so it needs no reference bike: the other 27 cells are the
# control. The statistic is the MEDIAN, not the worst sample - a failing cell
# drags the middle of the distribution up and keeps it there, while one hard
# launch produces a single deep outlier that says nothing about the pack. The
# reference bike has a 425 mV worst sample against a 64 mV median.
#
# MEASURED: the one healthy pack available reads a 64-67 mV median across two
# overlapping captures of the same bike, p90 101-110 mV.
# REASONED, not measured - this is the part that could be wrong on a bike unlike
# that one: at 28 cells in series, a cell sitting a quarter of a volt below its
# siblings under load is carrying roughly two to three times their internal
# resistance, which is a cell on its way out rather than a matched pack.
CELL_DEV_OK_MV = 100.0
CELL_DEV_WATCH_MV = 250.0

# Absolute floor, in the class that needs no comparison at all: a lithium cell
# dragged this low under load is stressed whatever other bikes do.
CELL_FLOOR_OK_MV = 3000.0
CELL_FLOOR_WATCH_MV = 2800.0

# Below this there is not enough loaded evidence to say anything about cells.
# 60-second samples inside one ride are highly correlated, so this is a floor on
# having looked at all, not a statistical sample size.
MIN_LOADED_SAMPLES = 30

_RANK = {"concern": 3, "watch": 2, "ok": 1, "unknown": 0}

# health.py grades ok/watch/alert/info; this module speaks in buyer's terms and
# has no "info" level, because a row that carries no judgement answers nothing.
_FROM_HEALTH = {"ok": "ok", "watch": "watch", "alert": "concern",
                "info": "unknown"}

def _headline(level, answered, total):
    """The one line a buyer reads. It carries the evidence count, because a
    clean result off one check out of three is not the same statement as a
    clean result off all three - and putting that difference in a separate
    'confidence' field is putting it where nobody looks."""
    if level == "concern":
        return "Walk away, or get the pack checked before you buy"
    if level == "watch":
        return "Worth a closer look - something here is not clean"
    if level == "unknown":
        return "Cannot tell from this capture - it does not contain the evidence"
    if answered < total:
        return ("Nothing wrong in what this capture could measure - but %d of %d "
                "checks went unanswered" % (total - answered, total))
    return "Nothing in this capture looks wrong with the pack"


def verdict(assessment, metrics=None):
    """A plain-language read on the pack, from one capture.

    Returns {level, headline, checks, confidence, caveats}. `level` is one of
    concern / watch / ok / unknown, worst-wins across the checks.

    Only checks that need NO reference bike are used: self-referencing ones
    where the pack is its own control, absolute ones fixed by chemistry, and
    presence-or-absence of a fault. Anything needing a population to compare
    against is left out rather than guessed at.

    "unknown" is a real answer and is never a polite way of saying fine. A
    capture with no hard riding in it, or whose records predate a firmware
    flash, genuinely cannot answer the cell questions - and a seller who charges
    the bike and lets it sit produces exactly that capture.
    """
    checks, caveats = [], []

    def add(name, level, detail):
        checks.append({"name": name, "level": level, "detail": detail})

    # 1. weakest cell vs its own pack average, under load  (self-referencing)
    dev = assessment.get("cell_deviation")
    if dev and dev["samples"] >= MIN_LOADED_SAMPLES:
        med = dev["median_mv"]
        lvl = ("ok" if med < CELL_DEV_OK_MV else
               "watch" if med < CELL_DEV_WATCH_MV else "concern")
        add("Weakest cell vs pack", lvl,
            "median %g mV below the pack average under load, over %d loaded "
            "samples" % (med, dev["samples"]))
    else:
        add("Weakest cell vs pack", "unknown",
            "not enough loaded samples carrying both a cell voltage and pack "
            "voltage (%d, need %d)"
            % (dev["samples"] if dev else 0, MIN_LOADED_SAMPLES))

    # 2. absolute cell floor under load  (chemistry)
    floor = assessment.get("cell_floor")
    if floor:
        mv = floor["min_cell_mv"]
        lvl = ("ok" if mv >= CELL_FLOOR_OK_MV else
               "watch" if mv >= CELL_FLOOR_WATCH_MV else "concern")
        add("Lowest cell under load", lvl,
            "%g mV, from %d %s" % (mv, floor["samples"], floor["source"]))
    else:
        add("Lowest cell under load", "unknown",
            "no channel in this capture carries a cell voltage under load")

    # 3. resting cell spread, and any live fault  (from the health metrics)
    by_label = {m["label"]: m for m in (metrics or [])}
    spread = by_label.get("Cell spread")
    if spread and spread.get("value") is not None:
        add("Cell spread at rest", _FROM_HEALTH.get(spread["status"], "unknown"),
            "%s (read at %s state of charge)"
            % (spread["display"],
               (by_label.get("Displayed SOC") or {}).get("display", "an unknown")))
    else:
        add("Cell spread at rest", "unknown", "no cell voltages in this capture")
    for label in ("Isolation resistance", "Warning"):
        m = by_label.get(label)
        if m and m.get("status") in ("watch", "alert"):
            add(label, _FROM_HEALTH[m["status"]],
                str(m.get("display") or m.get("value") or ""))

    # 4. preconditions that weaken whatever the checks said
    if assessment.get("stats_resets"):
        caveats.append("This bike's statistics were reset at %s, so every "
                       "'lifetime' figure is younger than the bike and cannot "
                       "be compared with another bike's."
                       % assessment["stats_resets"][0]["when"])
    # the "no reset found, but the log only reaches back to X" case is already
    # phrased by assess(), so it arrives with the rest of the undetermined list
    for u in assessment.get("undetermined") or []:
        caveats.append(u)

    answered = [c for c in checks if c["level"] != "unknown"]
    level = "unknown"
    for c in checks:
        if _RANK[c["level"]] > _RANK[level]:
            level = c["level"]
    if not answered:
        level = "unknown"
    return {
        "level": level,
        "headline": _headline(level, len(answered), len(checks)),
        "checks": checks,
        "answered": len(answered),
        "total_checks": len(checks),
        "confidence": ("none" if not answered else
                       "partial" if len(answered) < len(checks) else "full"),
        "caveats": caveats,
    }


def cell_deviation(records, cells=28):
    """How far the weakest cell sits below its OWN pack average, under load.

    The strongest check available without a reference bike, because the pack is
    its own control. Needs pack voltage as well as the cell reading, so it works
    only on the riding channel; `cells` is the series count, derived from the
    pack's resting voltage over its cell voltage where that is known.

    Reports the median rather than the worst sample: one hard launch produces a
    single deep outlier, while a genuinely weak cell keeps the whole
    distribution shifted.
    """
    devs = sorted((r["vpack"] * 1000.0 / cells) - r["mincell_mv"]
                  for r in records
                  if isinstance(r.get("vpack"), (int, float))
                  and isinstance(r.get("mincell_mv"), (int, float))
                  and isinstance(r.get("battamps"), (int, float))
                  and r["battamps"] >= LOADED_AMPS)
    if not devs:
        return None
    return {
        "samples": len(devs),
        "median_mv": round(devs[len(devs) // 2], 1),
        "p90_mv": round(devs[int(len(devs) * 0.9)], 1),
        "worst_mv": round(devs[-1], 1),
        "cells_assumed": cells,
        "graded": True,
    }


# Fault classes worth counting, and deliberately NOT the routine ones. On the
# reference bike - believed healthy - a six-week log holds 838 contactor
# openings, 161 power-on resets and 366 current-limit events, so counting those
# as faults would bury the reader in normal operation.
#
# What survives here still occurs on that healthy bike: 18 module connect
# failures, 33 precharge problems, 22 Sevcon emergency frames, 4 isolation
# events, 2 watchdog resets. So PRESENCE IS NOT A VERDICT. These are counted and
# dated, never graded - a buyer comparing 18 module failures against 300 can
# judge, and this module has no business pretending it knows where the line is
# from a single bike.
_FAULT_CLASSES = (
    ("Module connect failures", r"cannot connect module"),
    ("Precharge problems", r"precharge lost|failed to fully precharge|precharge decay"),
    ("Sevcon emergency frames", r"sevcon can emcy"),
    ("Isolation events", r"low chassis isolation|bms isolation fault"),
    ("Watchdog / abnormal resets", r"watchdog timer|abnormal reset"),
    ("Critical error shutdowns", r"critical error"),
    ("Cell voltage difference", r"max allowed voltage difference"),
    ("ABS errors", r"abs .{0,20}error"),
)


def fault_span(f):
    """A fault class's date range, phrased for a reader. Some console entries
    carry no clock at all — they are logged before the bike knows the time —
    and saying so beats printing a question mark."""
    if not f.get("first"):
        return "no dates logged"
    if f["first"] == f["last"]:
        return f["first"]
    return "%s to %s" % (f["first"], f["last"])


def fault_history(event_log):
    """Counted, dated fault classes from an event log. Ungraded on purpose.

    The error log is a small rolling buffer holding a handful of entries; the
    event log holds thousands, so this reads the event log and sees months where
    the error log sees days.
    """
    out = []
    for name, pattern in _FAULT_CLASSES:
        rx = re.compile(pattern, re.I)
        stamps = []
        count = 0
        for line in (event_log or "").splitlines():
            if not rx.search(line):
                continue
            count += 1
            ts = _TS_RE.search(line)
            if ts:
                stamps.append(ts.group(0))
        if count:
            out.append({"name": name, "count": count,
                        "first": stamps[0] if stamps else None,
                        "last": stamps[-1] if stamps else None,
                        "graded": False})
    return sorted(out, key=lambda f: -f["count"])
