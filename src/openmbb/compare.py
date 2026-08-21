"""Compare sessions over time: settings diff + capacity / gearing trends.

Sessions are passed oldest-first. Everything is tolerant of missing captures
(a session without a `bms` or `stats` block just contributes None to a trend).
"""

from . import condition, gearing, parsers, rides
from .parsers import parse_bms, parse_stats
from .transport import first_number, parse_settings_dump

# A single capture can only describe a pack. A run of them can show it moving,
# which is the one thing an owner cannot get any other way — and the measurements
# below were chosen because they survive a firmware change, unlike the SOC gauge
# and unlike the nominal capacity the BMS reports.
RIDE_LOG_COMMANDS = ("eventlogdump", "dumplogs")


def _circ(session):
    settings, _ = parse_settings_dump(session.settings_text)
    if "rwhcirc" in settings:
        try:
            return float(first_number(settings["rwhcirc"]["value"]))
        except (TypeError, ValueError):
            pass
    return gearing.DEFAULT_CIRC_MM


def settings_diff(a, b):
    """List of (name, old, new) for settings whose value differs between two
    sessions (oldest `a` -> newest `b`)."""
    sa, _ = parse_settings_dump(a.settings_text)
    sb, _ = parse_settings_dump(b.settings_text)
    diff = []
    for k in sorted(set(sa) | set(sb)):
        va = sa.get(k, {}).get("value")
        vb = sb.get(k, {}).get("value")
        if va != vb:
            diff.append((k, va, vb))
    return diff


# Minimum distance between two captures for the delta-derived ratio to be
# trustworthy. The stats odometer km is an INTEGER, so d_km carries up to +/-1 km
# of truncation error; at 20 km that bounds the ratio error to ~5%. Below it, a
# short test-ride (the re-gear-verification case) would render a wildly wrong
# "current gearing", so fall back to the lifetime average and SAY so.
MIN_DELTA_KM = 20


def _event_log(session):
    """The capture's event log, or "" - a console refusal is not a log."""
    return parsers.event_log_text(session, RIDE_LOG_COMMANDS)


def pack_trend(sessions):
    """Per-session pack measurements, oldest first.

    Only gauge-independent or self-referencing figures go in here. The charge
    index is amp-hours accepted between two fixed pack voltages, so a firmware
    SOC rescale cannot move it; the deviation is the weakest cell against its own
    pack average, so it needs no reference bike; and the cell INDEX is what turns
    "the pack is uneven" into "this cell is".
    """
    out = []
    for s in sessions:
        log = _event_log(s)
        bms = parse_bms(s.cmd("bms"))
        cap = condition.charge_capacity(parsers.parse_charge_log(log)) if log else None
        dev = condition.cell_deviation(parsers.parse_ride_log(log)) if log else None
        out.append({
            "session": s.name,
            "charge_index_ah": cap["median_ah"] if cap else None,
            "charge_sessions": cap["sessions"] if cap else 0,
            "cell_deviation_mv": dev["median_mv"] if dev else None,
            "loaded_samples": dev["samples"] if dev else 0,
            "low_cell_mv": bms.get("low_cell_mv"),
            "low_cell_index": bms.get("low_cell_index"),
            "soc_pct": bms.get("soc_pct"),
        })
    return out


def weakest_cell_identity(trend):
    """Whether one cell keeps turning up as the weakest.

    A voltage that wanders between cells is a pack breathing. The same index
    returning capture after capture is a cell. Reports None when fewer than two
    captures name one, because a single reading is not a pattern.
    """
    seen = [t["low_cell_index"] for t in trend if t.get("low_cell_index") is not None]
    if len(seen) < 2:
        return None
    counts = {}
    for i in seen:
        counts[i] = counts.get(i, 0) + 1
    cell, n = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
    return {
        "cell": cell,
        "times": n,
        "of_captures": len(seen),
        "always": n == len(seen),
        # read at rest, so it moves with state of charge — the same pack looks
        # tighter near full, which is why the SOC each reading was taken at
        # travels with the trend rather than being averaged away
        "graded": False,
    }


def compare_sessions(sessions):
    """Return settings diff (first vs last) and per-session trends. Each
    gearing_trend entry is (name, ratio, basis) — basis names how the ratio was
    derived so a lifetime fallback is never mistaken for a fresh measurement."""
    capacity_trend, gearing_trend = [], []
    prev = None
    for s in sessions:
        bms = parse_bms(s.cmd("bms"))
        stats = parse_stats(s.cmd("stats"))
        capacity_trend.append((s.name, bms.get("capacity_ah")))
        cur = (stats.get("odo_motor_rev"), stats.get("odo_km"))
        ratio, basis = None, None
        # F2/T10: prefer the ratio implied by the DELTA since the previous session
        # (it reflects the CURRENTLY programmed gearing), but ONLY when the delta
        # is big enough that integer-km quantization can't distort it.
        if prev and None not in prev and None not in cur:
            d_rev, d_km = cur[0] - prev[0], cur[1] - prev[1]
            if d_rev > 0 and d_km >= MIN_DELTA_KM:
                ratio = rides.effective_ratio(d_rev / float(d_km), _circ(s))
                basis = "delta over %g km" % d_km
        if ratio is None:          # first session / too-short delta -> lifetime avg
            rpk = rides.revs_per_km(cur[0], cur[1])
            ratio = rides.effective_ratio(rpk, _circ(s))
            basis = "lifetime avg (lags a re-gear)"
        gearing_trend.append((s.name, ratio, basis))
        prev = cur
    diff = settings_diff(sessions[0], sessions[-1]) if len(sessions) >= 2 else []
    trend = pack_trend(sessions)
    return {
        "settings_diff": diff,
        "capacity_trend": capacity_trend,
        "gearing_trend": gearing_trend,
        "pack_trend": trend,
        "weakest_cell": weakest_cell_identity(trend),
    }
