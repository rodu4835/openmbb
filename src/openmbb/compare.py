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


def compare_sessions(sessions):
    """Return settings diff (first vs last) and per-session trends."""
    capacity_trend, gearing_trend = [], []
    for s in sessions:
        bms = parse_bms(s.cmd("bms"))
        stats = parse_stats(s.cmd("stats"))
        capacity_trend.append((s.name, bms.get("capacity_ah")))
        rpk = rides.revs_per_km(stats.get("odo_motor_rev"), stats.get("odo_km"))
        gearing_trend.append((s.name, rides.effective_ratio(rpk, _circ(s))))
    diff = settings_diff(sessions[0], sessions[-1]) if len(sessions) >= 2 else []
    return {
        "settings_diff": diff,
        "capacity_trend": capacity_trend,
        "gearing_trend": gearing_trend,
    }
