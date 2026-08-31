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

EM = chr(0x2014)

from . import health, parsers

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




# --- how the bike is charged, which is a habit rather than a fault -----------

# The charger events the MBB writes around a charge. Two chargers are fitted on
# most Gen2 bikes (a 720 W and a 1200 W Calex), so a single plug-in writes more
# than one line; the state machine below only reacts to the first of each burst.
_CHG_CONNECT_RE = re.compile(
    r"charger\s*\d+\s+connected|power\s+on\s+.*onboard\s+charger", re.I)
_CHG_DISCONNECT_RE = re.compile(
    r"charger\s*\d+\s+disconnected|power\s+off\s+.*onboard\s+charger", re.I)
_CHG_STANDBY_RE = re.compile(r"entering\s+charge\s+standby", re.I)
# A ride ends a spell whether or not the unplug was recorded. Measured on the
# reference bike: a standby at 07/09 13:23 was followed by a ride at 15:02 with
# no disconnect line in between, and the next recorded disconnect was three days
# later - so a spell bounded only by disconnects claimed 91 hours at full for a
# bike that had been out riding.
_RIDING_RE = re.compile(r"\briding\b", re.I)

# A pack is hottest right after a hard ride. Plugging in then, rather than
# letting it cool, is the charging habit with the clearest effect on ageing -
# and unlike almost everything else here it is free to change.
HOT_PLUGIN_C = 45.0


def _log_events(event_log):
    """(datetime, line) for every timestamped line, oldest first."""
    out = []
    for line in (event_log or "").splitlines():
        m = _TS_RE.search(line)
        if not m:
            continue
        try:
            out.append((_dt.datetime.strptime(m.group(0), _TS_FMT), line))
        except ValueError:
            continue
    out.sort(key=lambda p: p[0])
    return out


def full_charge_holds(event_log):
    """Spells spent sitting at full charge with the charger still attached.

    Measured between "Entering Charge Standby Mode" - the charger reporting the
    pack full - and the charger being unplugged. This cannot be read from the
    charging samples: the bike stops writing them when the charge finishes, so a
    pack that then sat plugged in for three days leaves no samples at all. On the
    reference bike the samples account for 13 hours of the 422 that the events
    show.

    A spell ends at the FIRST evidence the bike stopped sitting there: the
    charger being unplugged, or the bike being ridden. Riding matters as much as
    the unplug, because the unplug is not always recorded - and a spell bounded
    only by disconnects will happily run straight through a ride.

    A spell still open at the end of the buffer is dropped rather than guessed
    at, so the total runs under the truth rather than over it.
    """
    holds, pending = [], None
    for t, line in _log_events(event_log):
        if _CHG_DISCONNECT_RE.search(line) or _RIDING_RE.search(line):
            if pending is not None:
                secs = (t - pending).total_seconds()
                if secs > 0:
                    holds.append((pending, secs))
                pending = None
        elif _CHG_CONNECT_RE.search(line):
            # a plug-in with a spell still open means the unplug went unrecorded,
            # and an unbounded spell is not a measurement
            pending = None
        elif _CHG_STANDBY_RE.search(line):
            # Leaving and re-entering standby is the charger topping the pack up
            # while it sits at full, so only the FIRST entry opens the spell -
            # the whole stretch counts as time held at full.
            if pending is None:
                pending = t
    return holds


def charge_behaviour(event_log, charge_records=None):
    """What the charging record says about how this bike is looked after.

    Returns None when there is no charging in the capture. Nothing here is
    graded: these are habits, not faults, and the thresholds that would turn
    "charged at 46 C" into a verdict do not exist without a population to set
    them against. What it does do is put a number on the one thing an owner can
    change today.
    """
    records = (parsers.parse_charge_log(event_log) if charge_records is None
               else charge_records)
    sessions = _sessions(records)
    holds = full_charge_holds(event_log)
    events = _log_events(event_log)
    if not sessions and not holds:
        return None

    span_days = None
    if events:
        span_days = (events[-1][0] - events[0][0]).total_seconds() / 86400.0
        if span_days <= 0:
            span_days = None

    starts, hot_plugins, peaks = [], 0, []
    for sess in sessions:
        socs = [r["soc"] for _t, r in sess if r.get("soc") is not None]
        temps = [r["pack_temp_c"] for _t, r in sess if r.get("pack_temp_c") is not None]
        amps = [abs(r["battamps"]) for _t, r in sess if r.get("battamps") is not None]
        if socs:
            starts.append(socs[0])
        if temps and temps[0] >= HOT_PLUGIN_C:
            hot_plugins += 1
        if amps:
            peaks.append(max(amps))

    hold_hours = sorted(secs / 3600.0 for _t, secs in holds)
    total_held_h = sum(hold_hours)
    out = {
        "sessions": len(sessions),
        "span_days": round(span_days, 1) if span_days else None,
        "per_week": (round(len(sessions) / (span_days / 7.0), 1)
                     if span_days and span_days >= 7 else None),
        "start_soc_median": _median(starts),
        "start_soc_min": min(starts) if starts else None,
        "hot_plugins": hot_plugins,
        "hot_plugin_c": HOT_PLUGIN_C,
        "peak_amps_median": _median(peaks),
        "peak_amps_max": max(peaks) if peaks else None,
        "holds": len(hold_hours),
        "held_full_h": round(total_held_h, 1) if hold_hours else None,
        "held_full_median_h": (round(hold_hours[len(hold_hours) // 2], 2)
                               if hold_hours else None),
        "held_full_max_h": round(hold_hours[-1], 1) if hold_hours else None,
        "held_full_share": (round(total_held_h / (span_days * 24.0), 3)
                            if hold_hours and span_days else None),
        # Sampled every ten minutes at whole-amp resolution, which is coarser
        # than the constant-voltage knee it would take to see a taper. Saying so
        # is the honest answer; a taper "measured" at this resolution would be
        # an artefact of the sampling.
        "taper_resolvable": False,
        "graded": False,
    }
    return out


def _median(values):
    if not values:
        return None
    v = sorted(values)
    return v[len(v) // 2]



# --- where the hottest reading came from, and what the air was doing at it ---
#
# An ambient temperature may only ever be printed alongside a pack temperature
# that came from the SAME log line. Not a neighbouring sample, not an average,
# not a ride's starting value, and never from a charging record. The sensor is on
# the bike, not in the air: while riding it settles to something like the air
# within a few minutes, but on a charger it climbs to meet the pack - measured on
# the reference bike, a median 29 C between midnight and 06:00 where the same
# sensor reads 16 C riding at those same hours, reaching 51 C against a 54 C
# pack, and reading at or above the pack in 107 of 1424 charging samples. The
# pack-minus-ambient difference during a charge measures the enclosure and the
# sensor, not the pack. The arithmetic works; the measurement does not.
#
# An ambient temperature may never move a threshold, and none is passed to
# anything that has one. An ambient-adjusted limit can only ever FORGIVE - a
# seller's bike pulled on a hot day would score better for it - which converts a
# check into a pass it did not earn. Ambient can rule the weather out of a hot
# reading; it can never rule it in. That asymmetry is why the error here is safe:
# sensor soak inflates ambient, which makes the day look warmer, which makes the
# disconfirmation WEAKER rather than stronger.
#
# The lifetime maximum in `stats` is not a log reading and is never presented as
# explained or corrected by one. The two channels disagree in BOTH directions on
# the reference bike: the 2026-08-19 capture reports 60 C where the log's hottest
# sample anywhere is 59 C, and the 2026-07-10 capture reports 59 C where the log
# holds a genuine 60 C sample. Even where a log sample ties the lifetime figure,
# six samples tied it at three different ambients - a candidate set, not a value.
#
# No aggregate of the ambient channel is computed anywhere, because a median over
# it would silently average two different physical quantities together.


def pack_peak(ride_records):
    """The hottest pack reading in the log, and the air at that same reading.

    Riding records only. The ambient figures are taken from the tying samples
    themselves and from nothing else, so they describe the moment rather than the
    capture. Returns None when no riding record carries a readable pack
    temperature - which is the honest answer for the `BattTemp:` dialect, not a
    reason to reach for another source.
    """
    with_temp = [r for r in ride_records or []
                 if r.get("pack_temp_c") is not None]
    if not with_temp:
        return None
    peak = max(r["pack_temp_c"] for r in with_temp)
    ties = [r for r in with_temp if r["pack_temp_c"] == peak]
    ambs = [r["amb_temp_c"] for r in ties if r.get("amb_temp_c") is not None]
    return {
        "pack_temp_c": peak,
        "ts": ties[0].get("ts"),
        "ties": len(ties),
        "amb_low_c": min(ambs) if ambs else None,
        "amb_high_c": max(ambs) if ambs else None,
        "amb_samples": len(ambs),
        "ride_samples": len(ride_records or []),
        "graded": False,
    }


def lifetime_peak(peak, stat_c, n_ride_records, batt_temp_dialect=False):
    """How the bike's lifetime temperature counter stands against this log.

    `case` is one of:
      outside_log    - the counter is higher than anything the log holds
      log_is_hotter  - the LOG holds a sample hotter than the counter reports
      log_reaches_it - the log contains the counter's own figure
      no_pack_temp   - riding records exist but none carry a pack temperature

    Neither channel corrects the other and neither is presented as doing so.
    """
    if stat_c is None or not n_ride_records:
        return None
    if peak is None:
        return {"stat_c": stat_c, "log_peak_c": None, "ts": None, "ties": 0,
                "amb_low_c": None, "amb_high_c": None, "amb_samples": 0,
                "case": "no_pack_temp", "graded": False,
                "batt_temp_dialect": batt_temp_dialect}
    log_peak = peak["pack_temp_c"]
    if log_peak > stat_c:
        case = "log_is_hotter"
    elif log_peak < stat_c:
        case = "outside_log"
    else:
        case = "log_reaches_it"
    out = dict(peak)
    out.pop("ride_samples", None)
    out.update({"stat_c": stat_c, "log_peak_c": log_peak, "case": case,
                "graded": False})
    out.pop("pack_temp_c", None)
    return out


def _amb_phrase(lp, temp_units):
    """"at 19 C to 23 C ambient", or "" when this firmware prints no ambient."""
    if not lp or not lp.get("amb_samples"):
        return ""
    lo, hi = lp["amb_low_c"], lp["amb_high_c"]
    if lo == hi:
        return ", at %s ambient" % health.fmt_temp(lo, temp_units)
    return ", at %s to %s ambient" % (health.fmt_temp(lo, temp_units),
                                      health.fmt_temp(hi, temp_units))


# The clause that keeps a reader from over-reading the ambient figure. It travels
# with every sentence that prints one, and with none that does not.
_SENSOR_CAVEAT = ("That ambient figure is the bike's own sensor, which reads the "
                  "bike's heat for the first minutes of a ride - it can rule the "
                  "weather out of a hot reading, never in.")
_SENSOR_CAVEAT_PLURAL = _SENSOR_CAVEAT.replace("That ambient figure is",
                                               "Those ambient figures are")


def lifetime_peak_note(lp, temp_units="C"):
    """One sentence about where the pack's hottest figure came from, or None.

    Composed here and nowhere else, so the GUI and the report cannot drift - the
    same arrangement `fault_span` already uses.
    """
    if not lp:
        return None
    stat = health.fmt_temp(lp["stat_c"], temp_units)

    if lp["case"] == "no_pack_temp":
        base = ("the pack's peak temperature: not one riding record in this "
                "capture carries a pack temperature this tool can read, so the "
                "bike's lifetime counter (%s) is the only thermal figure here "
                "and it cannot be placed in time." % stat)
        if lp.get("batt_temp_dialect"):
            # the reading exists; what it MEANS is what is missing
            base += (" This firmware prints a single `BattTemp` rather than the "
                     "high/low pair, and whether that is the hottest module, an "
                     "average across them, or one sensor is not established - "
                     "so it is not read as a peak. If it is an average, treating "
                     "it as one would report the pack cooler than it got.")
        return base

    log_peak = health.fmt_temp(lp["log_peak_c"], temp_units)
    amb = _amb_phrase(lp, temp_units)
    caveat = ("" if not amb else " " + (_SENSOR_CAVEAT_PLURAL if lp["ties"] > 1
                                        else _SENSOR_CAVEAT))
    ties_note = "" if lp["ties"] <= 1 else " (%d samples tie it)" % lp["ties"]

    if lp["case"] == "outside_log":
        body = ("the air at the lifetime peak: the bike's lifetime counter "
                "reports %s and no line in this log reaches it - the hottest "
                "riding sample here is %s on %s%s%s. The counter is not a log "
                "reading, so nothing in this capture can say when the pack was "
                "%s or how warm the air was."
                % (stat, log_peak, lp["ts"], amb, ties_note, stat))
    elif lp["case"] == "log_is_hotter":
        body = ("which reading is the pack's real maximum: this log holds %s on "
                "%s%s - hotter than the %s the bike's lifetime counter reports. "
                "They are separate channels and neither corrects the other; the "
                "log sample was actually taken, so %s is the peak this capture "
                "can prove."
                % (log_peak, lp["ts"], amb, stat, log_peak))
    elif lp["ties"] > 1:
        body = ("the air at the lifetime peak: this log reaches the %s the "
                "bike's lifetime counter reports, on %d samples%s. The counter "
                "and the log are separate channels, so those are candidates for "
                "the peak, not the peak - there is no single ambient to attach "
                "to the lifetime figure."
                % (stat, lp["ties"], amb))
    else:
        body = ("the air at the lifetime peak: one sample in this log reaches "
                "the %s the bike's lifetime counter reports - %s%s. The counter "
                "and the log are separate channels, so that is the nearest this "
                "capture comes to the lifetime figure, not proof it is the same "
                "moment." % (stat, lp["ts"], amb))

    if not amb:
        body += (" This firmware prints no ambient temperature on its riding "
                 "lines, so a hot pack and a hot day cannot be told apart in "
                 "this capture.")
    return body + caveat


def assess(event_log, max_batt_temp_c=None, temp_units="C"):
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

    # Where the hottest reading came from. `max_batt_temp_c` is the bike's
    # LIFETIME counter, out of `stats` - a different channel from the log, which
    # is the whole point of saying anything about it. Passing None simply omits
    # the sentence; nothing here is graded and no threshold sees any of it.
    peak = pack_peak(rides)
    lifetime = lifetime_peak(
        peak, max_batt_temp_c, len(rides),
        batt_temp_dialect=any(r.get("batt_temp_c") is not None for r in rides))
    # what else was in the log around the module-connect failures. An
    # association, never a cause - the firmware does not name one.
    module_ctx = module_failure_context(event_log)
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
    # rides out on `undetermined` rather than growing a surface of its own: it is
    # precisely a statement about what this capture could not establish, and the
    # Condition tab, the report block and the verdict caveats all already render
    # that list
    note = lifetime_peak_note(lifetime, temp_units)
    if note:
        undetermined.append(note)

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
        "module_failures": module_ctx,
        "pack_peak": peak,
        "lifetime_peak": lifetime,
        # how the bike is CHARGED, as against what the pack did while charging.
        # Habits, not faults - and the one measurement in this module an owner
        # can act on the same afternoon.
        "charging": charge_behaviour(event_log, charges),
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

def _driver_clause(checks, level):
    """The checks that produced `level`, named, for the headline.

    A buyer who reads one line should learn WHICH finding earned it. Without
    this the harsh branches said "get the pack checked" whatever drove them -
    on the reference bike that was isolation resistance, and the sentence gave
    no way to know.
    """
    def _label(c):
        # "Warning" is the check's NAME, not a finding - a driver clause of
        # just "- warning" tells a reader in a driveway nothing. That check
        # carries the console's own message in `detail`, so use it.
        if c.get("name") == "Warning" and str(c.get("detail") or "").strip():
            return str(c["detail"]).strip()
        return c["name"].lower()

    named = [_label(c) for c in (checks or []) if c.get("level") == level]
    if not named:
        return ""
    if len(named) == 1:
        return " %s %s" % (EM, named[0])
    return " %s %s" % (EM, ", ".join(named))


#: The three checks EVERY capture is scored on, answered or not. They are added
#: unconditionally (each with an `unknown` fallback), which is what makes a
#: fraction over them mean the same thing on every bike.
#:
#: The isolation and warning rows are deliberately NOT here. They exist only
#: when they fire, so counting them moved the denominator with the motorcycle -
#: the same evidence read "2 of 3" on a healthy pack and "2 of 4" on a faulted
#: one. They are NAMED instead, by _driver_clause, which is the surface that
#: carries them.
PACK_CHECKS = ("Weakest cell vs pack", "Lowest cell under load",
               "Cell spread at rest")


def _headline(level, answered, total, checks=None):
    """The one line a buyer reads.

    It carries the evidence count, because a clean result off one check out of
    three is not the same statement as a clean result off all three - and
    putting that difference in a separate 'confidence' field is putting it
    where nobody looks. That argument was made for the OK branch and then not
    applied to the harsh ones, which is how "Walk away" printed over a capture
    whose cell checks were mostly unanswered - two of four on the pull taken
    WITHOUT the event log (2026-08-29_212001). The full capture from the same
    evening answers all four and carries no count, which is correct: two
    tellings of this got the number wrong and a third got the capture wrong.

    SCOPE: this verdict covers the BATTERY SYSTEM - cell health, isolation,
    live warnings. Fault-count rows (OBD, Sevcon) are beyond-pack notes and do
    not move the level; see beyond_pack_notes.
    """
    # Parenthetical on the harsh branches: they already carry an em-dash for
    # the driver, and two in one line is a sentence nobody reads standing in a
    # driveway.
    missing = ""
    if answered < total:
        missing = (" (%d of %d pack checks unanswered)"
                   % (total - answered, total))
    if level == "concern":
        return ("Walk away, or get it checked before you buy%s%s"
                % (_driver_clause(checks, "concern"), missing))
    if level == "watch":
        return ("Worth a closer look%s%s"
                % (_driver_clause(checks, "watch"), missing))
    if level == "unknown":
        return "Cannot tell from this capture - it does not contain the evidence"
    if answered < total:
        return ("Nothing wrong in what this capture could measure - but %d of "
                "%d pack checks went unanswered" % (total - answered, total))
    return "Nothing in this capture looks wrong with the pack"


#: Health rows that report a fault the PACK verdict does not cover. Both, not
#: one: the Sevcon row is new, but the OBD row has been invisible to the
#: verdict since the verdict existed, and fixing the new label alone would
#: leave the identical hole one label over.
_BEYOND_PACK_LABELS = ("Fault codes", "Sevcon faults")


def beyond_pack_notes(metrics):
    """Findings a reader must not walk past because the pack verdict is green.

    The verdict is about the PACK. That scope is deliberate and it is what
    makes the thing trustworthy - its checks are pack checks against
    chemistry-grounded thresholds, and "confidence: full" counts pack questions
    answered, not bike questions. So this does not widen the claim.

    What it refuses to do is stay quiet. A stored fault code sits on the Health
    tab in red while the verdict says "nothing wrong with the pack", and a
    buyer reading the green line at the end of an inspection has been walked
    past it. Each note carries the row's own words and the clause that matters:
    the verdict above does not cover this.
    """
    out = []
    for m in metrics or []:
        if m.get("label") not in _BEYOND_PACK_LABELS:
            continue
        if m.get("status") not in ("watch", "alert"):
            continue
        what = str(m.get("display") or m.get("value") or "").strip()
        out.append("%s: %s - the verdict above is about the PACK and does not "
                   "cover this." % (m["label"], what))
    return out


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

    # Scored over the fixed pack checks only - see PACK_CHECKS. A conditional
    # row that fired is reported by being NAMED in the headline, never by
    # quietly enlarging the denominator underneath it.
    scored = [c for c in checks if c["name"] in PACK_CHECKS]
    answered = [c for c in scored if c["level"] != "unknown"]
    # The LEVEL still comes from every check, scored or not: a fired isolation
    # row must be able to drive the verdict even though it does not appear in
    # the fraction. Only the counting narrowed.
    level = "unknown"
    for c in checks:
        if _RANK[c["level"]] > _RANK[level]:
            level = c["level"]
    if not answered and not [c for c in checks if c["level"] != "unknown"]:
        level = "unknown"
    return {
        "level": level,
        # Deliberately NOT folded into `level`: see beyond_pack_notes. A pack
        # verdict that went amber because the motor controller has a fault
        # would be lying about the pack, which is the one thing this function
        # is for.
        "beyond_pack": beyond_pack_notes(metrics),
        "headline": _headline(level, len(answered), len(scored), checks),
        "checks": checks,
        "answered": len(answered),
        "total_checks": len(scored),
        "confidence": ("none" if not answered else
                       "partial" if len(answered) < len(scored) else "full"),
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


# How close a thermal disable has to sit to a module-connect failure before the
# two are worth mentioning in the same breath. Sixty seconds is generous enough
# to survive a clock that ticks in whole seconds and tight enough that unrelated
# events do not pair up by accident.
MODULE_FAILURE_WINDOW_S = 60.0

_MODULE_FAIL_RE = re.compile(r"cannot\s+connect\s+module", re.I)
_HIGH_TEMP_DISABLE_RE = re.compile(r"disable.*high\s*temp|high\s*temp.*disable", re.I)


def module_failure_context(event_log):
    """How many module-connect failures sat beside a thermal disable, or None.

    An ASSOCIATION and nothing more. The firmware writes "Cannot Connect Module
    00" without naming a reason, so this counts what else was in the log at the
    time and says so - it never states a cause, and the wording it feeds must
    not either. On the reference bike the association is total (18/18, 18/18,
    54/54 across three captures), which is striking and still not a mechanism.

    Returns None when the capture has no module failures, and omits the
    comparison entirely when it has no thermal disables to compare against - a
    bike that never logged one says nothing either way, and "0 of 54" would read
    as evidence of absence.
    """
    rows = [l for l in (event_log or "").splitlines() if _LOG_ENTRY_RE.match(l)]
    fails, hots = [], []
    for line in rows:
        when = _ts_of(line)
        if _MODULE_FAIL_RE.search(line):
            fails.append(when)
        elif _HIGH_TEMP_DISABLE_RE.search(line):
            if when:
                hots.append(_dt.datetime.strptime(when, _TS_FMT))
    if not fails:
        return None
    if not hots:
        return {"total": len(fails), "near_high_temp": None,
                "window_s": MODULE_FAILURE_WINDOW_S}
    near = 0
    for when in fails:
        if not when:
            continue
        try:
            t = _dt.datetime.strptime(when, _TS_FMT)
        except ValueError:
            continue
        if any(abs((t - h).total_seconds()) <= MODULE_FAILURE_WINDOW_S
               for h in hots):
            near += 1
    return {"total": len(fails), "near_high_temp": near,
            "window_s": MODULE_FAILURE_WINDOW_S}


def module_failure_note(ctx):
    """The association line, or "" when there is nothing to compare against."""
    if not ctx or ctx.get("near_high_temp") is None or not ctx["near_high_temp"]:
        return ""
    n, total = ctx["near_high_temp"], ctx["total"]
    how_many = "all %d" % total if n == total else "%d of %d" % (n, total)
    return ("%s fell within %d s of a high-temperature disable in this capture "
            "\u2014 association only; the log does not state a cause"
            % (how_many, int(ctx["window_s"])))


def fault_span(f):
    """A fault class's date range, phrased for a reader. Some console entries
    carry no clock at all — they are logged before the bike knows the time —
    and saying so beats printing a question mark.

    The dates are the ONSETS' dates. A class whose every frame was a clearing has
    no onset in this capture to date, which is a real thing to say rather than a
    gap to fill with the clearing's timestamp.
    """
    if not f.get("first"):
        if f.get("clearings"):
            return "no onset in this capture (every frame was a clearing)"
        return "no dates logged"
    if f["first"] == f["last"]:
        return f["first"]
    return "%s to %s" % (f["first"], f["last"])


def fault_detail(f):
    """"23 frames: 13 onsets, 10 clearings", or "" when nothing cleared."""
    if not f.get("clearings"):
        return ""
    return "%d frames: %d onset%s, %d clearing%s" % (
        f["count"], f["onsets"], "" if f["onsets"] == 1 else "s",
        f["clearings"], "" if f["clearings"] == 1 else "s")


# A logged entry begins with its five-digit record number. Anything else in the
# captured text is console output that happened to arrive during the read -
# notably the asynchronous DEBUG trace the firmware emits when something goes
# wrong WHILE the tool is talking to it. Counting those made the same bike report
# different totals on two reads of the same log: 26 Sevcon frames against a table
# holding 23, and 16 precharge problems against 12.
_LOG_ENTRY_RE = re.compile(r"^\s*\d{5}\s")

# A Sevcon emergency frame carrying error code 0x0000 is the controller saying
# the fault has CLEARED. Ten of one capture's 23 frames were clearings, and the
# last of them dated the bike's most recent "fault" to what was actually a reset.
_EMCY_CODE_RE = re.compile(r"error\s*code[:\s]*0x([0-9a-f]{4})", re.I)


def _is_clearing(line):
    m = _EMCY_CODE_RE.search(line)
    return bool(m) and int(m.group(1), 16) == 0


def fault_history(event_log):
    """Counted, dated fault classes from an event log. Ungraded on purpose.

    The error log is a small rolling buffer holding a handful of entries; the
    event log holds thousands, so this reads the event log and sees months where
    the error log sees days.

    Two things this is careful about. Only NUMBERED log entries are counted, so a
    burst of live DEBUG trace caught mid-read cannot inflate a total. And a frame
    that says a fault CLEARED does not date the fault: the count keeps every
    frame, because a controller resetting itself ten times is worth seeing, but
    `first` and `last` come from onsets alone.
    """
    out = []
    for name, pattern in _FAULT_CLASSES:
        rx = re.compile(pattern, re.I)
        stamps = []
        count = clearings = 0
        for line in (event_log or "").splitlines():
            if not _LOG_ENTRY_RE.match(line) or not rx.search(line):
                continue
            count += 1
            if _is_clearing(line):
                clearings += 1
                continue                 # counted, but it dates nothing
            ts = _TS_RE.search(line)
            if ts:
                stamps.append(ts.group(0))
        if count:
            out.append({"name": name, "count": count,
                        "onsets": count - clearings, "clearings": clearings,
                        "first": stamps[0] if stamps else None,
                        "last": stamps[-1] if stamps else None,
                        "graded": False})
    return sorted(out, key=lambda f: -f["count"])


# --- clocks ------------------------------------------------------------------
#
# A Gen2 bike carries at least three clocks that are set independently and drift
# apart: the MBB's (which timestamps the event log), the BMS's (which prints an
# epoch beside it), and the dash's. On the reference bike the MBB and BMS agree
# within seconds while the dash runs ten minutes ahead. On a bike reported from
# the Czech Republic the MBB reads seven hours behind local while the BMS reads
# nine, because one counter was set to local time and the other to UTC.
#
# The fix needs no timezone database and no setting. OpenMBB records the
# capturing machine's clock next to every command, and the bike prints its own
# clock in `stats`, so the two sides of one instant are both on disk: their
# difference is exactly what must be added to any MBB-rendered timestamp — the
# whole event log — to read in the time the capture was taken in.
_CLOCK_RE = re.compile(r"(\d\d/\d\d/\d{4}\s+\d\d:\d\d:\d\d)")
_EPOCH_RE = re.compile(r"\(\s*(\d{9,11})\s*,")

# Under this the bike is merely drifting, not offset, and shifting the display
# would be noise. The reference bike drifted 2, 3 and 7 minutes across captures.
CLOCK_DRIFT_TOLERANCE_S = 600


def _parse_stamp(text, fmt):
    try:
        return _dt.datetime.strptime(text, fmt)
    except (TypeError, ValueError):
        return None


def clock_check(session):
    """Every clock the bike reports, and the correction for its event log.

    `offset_s` is capture-machine minus bike: add it to an MBB-rendered
    timestamp to get the time the capture was taken in. None when the capture
    lacks either side of the pair, which must be said rather than assumed to be
    zero.
    """
    stats_txt = session.cmd("stats") or ""
    bms_txt = session.cmd("bms") or ""
    dash_txt = session.cmd("dash") or ""

    m = _CLOCK_RE.search(_first_line_with(stats_txt, "system time"))
    mbb = _parse_stamp(m.group(1) if m else None, "%m/%d/%Y %H:%M:%S")
    bms_line = _first_line_with(bms_txt, "bms clock")
    m = _CLOCK_RE.search(bms_line)
    bms = _parse_stamp(m.group(1) if m else None, "%m/%d/%Y %H:%M:%S")
    m = _EPOCH_RE.search(bms_line)
    epoch = int(m.group(1)) if m else None
    m = re.search(r"(\d\d:\d\d)", _first_line_with(dash_txt, "clock"))
    dash = m.group(1) if m else None

    pc = _parse_stamp(session.captured_at.get("stats"), "%Y-%m-%d %H:%M:%S")
    offset_s = int((pc - mbb).total_seconds()) if (pc and mbb) else None

    # printed-versus-counter: how the console renders the epoch it stores. On
    # both bikes measured this is -7 h, US Pacific, where they are built.
    render_h = None
    if bms and epoch:
        as_utc = _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).replace(
            tzinfo=None)
        render_h = round((bms - as_utc).total_seconds() / 3600.0, 2)

    return {
        "mbb_clock": mbb.strftime("%m/%d/%Y %H:%M:%S") if mbb else None,
        "bms_clock": bms.strftime("%m/%d/%Y %H:%M:%S") if bms else None,
        "bms_epoch": epoch,
        "dash_clock": dash,
        "captured_at": session.captured_at.get("stats"),
        "offset_s": offset_s,
        "mbb_vs_bms_s": int((mbb - bms).total_seconds()) if (mbb and bms) else None,
        "console_renders_epoch_at_h": render_h,
        "worth_correcting": bool(offset_s is not None
                                 and abs(offset_s) > CLOCK_DRIFT_TOLERANCE_S),
    }


def _first_line_with(text, needle):
    for line in (text or "").splitlines():
        if needle in line.lower():
            return line
    return ""


def describe_offset(seconds):
    """A signed offset as something a person reads, e.g. '7 h 12 m behind'."""
    if seconds is None:
        return "unknown"
    s = abs(int(seconds))
    h, m = s // 3600, (s % 3600) // 60
    parts = ("%d h " % h if h else "") + ("%d m" % m if (m or not h) else "")
    return "%s %s" % (parts.strip(), "behind" if seconds > 0 else "ahead")


def shift_timestamp(ts, offset_s, fmt="%m/%d/%Y %H:%M:%S"):
    """An MBB-rendered timestamp moved into the capture's local time."""
    t = _parse_stamp(ts, fmt)
    if t is None or offset_s is None:
        return ts
    return (t + _dt.timedelta(seconds=offset_s)).strftime(fmt)


# --- what both surfaces say, decided and worded once -------------------------
#
# The report and the Condition tab legitimately look different - prose lines
# against label/finding rows - and unifying their LAYOUT would fight both. What
# may not differ is whether a fact appears and what it claims. Everything below
# returns None when the fact should not be shown, so a surface cannot keep the
# sentence while losing the condition that earns it.
#
# A drifted number is visibly wrong. A drifted caveat is invisibly wrong, and a
# caveat is the only thing between a measurement and somebody trusting it
# further than it goes.


def coverage_note(cov):
    """How much of the ride log could answer the cell question at all.

    Records written before a firmware change do not survive being re-read by a
    newer one: the trailing fields come back as stale bytes and `_mode_samples`
    refuses them. That refusal is right, and it was silent - the report
    advertised 1137 ride samples while every cell answer rested on 635.

    The load-bearing words are "UNMEASURED here, not clean". An unmeasured
    stretch is not a clean one, and this is the sentence that says so on both
    surfaces at once.
    """
    total = (cov or {}).get("ride_samples") or 0
    with_cell = (cov or {}).get("ride_samples_with_cell")
    if not total or with_cell is None or with_cell >= total:
        return None
    return (
        "cell voltage readable on %d of %d ride records %s the other %d carry a "
        "value no cell can hold (records written before a firmware change, "
        "re-read by a newer one). Every cell answer here is measured over those "
        "%d only; what the pack did across the rest of this window is "
        "UNMEASURED here, not clean." % (with_cell, total, '—',
                                         total - with_cell, with_cell))


def show_cell_floor(a):
    """Whether the unloaded-floor row earns its place beside the sag row.

    Duplicated verbatim on both surfaces, which is worse than a duplicated
    sentence: get them out of step and the two disagree about which rows EXIST,
    not merely about how one reads.
    """
    floor, sag = (a or {}).get("cell_floor"), (a or {}).get("cell_sag")
    return bool(floor) and (not sag or floor["source"] != "riding samples")


def capacity_caveat():
    """Why the charge index is not the pack's capacity.

    The tab used to say this in four words and the report in two lines. The
    fuller wording wins: it names the MECHANISM, which is what makes the number
    survive a firmware reflash, and that is the whole reason to trust it.
    """
    return ("a comparable index, not the pack's capacity - it reads only pack "
            "voltage and current, so a firmware change that relabels the SOC "
            "display cannot move it")


def range_caveat(rng):
    """What the range figure rests on, when it rests on an extrapolation."""
    if not rng or not rng.get("is_extrapolation"):
        return None
    note = ("an UPPER BOUND on what the gauge implies, not a distance anyone "
            "has ridden: it assumes the SOC scale is linear and that 0%% is "
            "reachable, and the lowest this log has ever seen is %g%%"
            % rng["soc_floor_pct"])
    if rng.get("extrapolation_x"):
        note = ("scaled up %gx from what was actually ridden - " % rng["extrapolation_x"]) + note
    return note


def full_hold_note(ch):
    """Time spent sitting at full, and why it is worth an owner's attention."""
    if not ch or not ch.get("held_full_h"):
        return None
    return ("time at full is the main driver of calendar ageing; unplugging "
            "when it finishes costs nothing")


def taper_note(ch):
    """Why no charge taper is reported.

    The confirmed divergence this whole item started from. The report guarded
    the sentence on `taper_resolvable`; the tab baked it into the charge-current
    row unconditionally. Both read true today only because the field is a
    hardcoded False - the day `assess` learns to resolve a taper, the tab would
    have gone on insisting it cannot see one.
    """
    if not ch or ch.get("taper_resolvable"):
        return None
    return ("the taper is NOT visible here: charging is sampled every ~10 min "
            "at whole-amp resolution, which is coarser than the "
            "constant-voltage knee it would take to see one")
