"""Compare sessions over time: settings diff + capacity / gearing trends.

Sessions are passed oldest-first. Everything is tolerant of missing captures
(a session without a `bms` or `stats` block just contributes None to a trend).
"""

from . import gearing, rides
from .parsers import parse_bms, parse_stats
from .transport import first_number, parse_settings_dump


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
    return {
        "settings_diff": diff,
        "capacity_trend": capacity_trend,
        "gearing_trend": gearing_trend,
    }
