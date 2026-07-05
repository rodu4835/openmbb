"""Ride-log analysis: structured ride records -> per-ride summaries + gearing.

Consumes the records produced by parsers.parse_ride_log(). Splits a log into
individual rides, summarizes SOC%/km and temps per ride, and derives the
effective final-drive ratio from the odometer pair in `stats`.
"""

from . import gearing


def split_rides(records):
    """Split a flat record list into individual rides.

    A new ride starts when SOC jumps up (a recharge happened) or the odometer
    goes backwards / jumps a lot (a gap in captured samples).
    """
    segments, cur, prev = [], [], None
    for r in records:
        if prev is not None:
            soc_jump = (r.get("soc") or 0) - (prev.get("soc") or 0)
            odo_delta = (r.get("odo_km") or 0) - (prev.get("odo_km") or 0)
            if soc_jump > 8 or odo_delta < 0 or odo_delta > 5:
                if len(cur) >= 2:
                    segments.append(cur)
                cur = []
        cur.append(r)
        prev = r
    if len(cur) >= 2:
        segments.append(cur)
    return segments


def _span(seg, key):
    vals = [r[key] for r in seg if r.get(key) is not None]
    return vals


def segment_summary(seg):
    socs, odos = _span(seg, "soc"), _span(seg, "odo_km")
    temps, rpms = _span(seg, "temp_c"), _span(seg, "motrpm")
    dist = (max(odos) - min(odos)) if len(odos) >= 2 else 0.0
    dsoc = (max(socs) - min(socs)) if len(socs) >= 2 else 0.0
    return {
        "start_ts": seg[0].get("ts") if seg else None,
        "samples": len(seg),
        "distance_km": round(dist, 1),
        "soc_used_pct": round(dsoc, 1),
        "soc_per_km": round(dsoc / dist, 2) if dist > 0 else None,
        "max_temp_c": max(temps) if temps else None,
        "max_rpm": max(rpms) if rpms else None,
    }


def summarize_rides(records):
    """Return {'rides': [per-ride summaries], 'totals': {...}}."""
    segs = split_rides(records)
    rides = [segment_summary(s) for s in segs]
    dist = sum(r["distance_km"] for r in rides)
    rated = [r["soc_per_km"] for r in rides if r["soc_per_km"] is not None]
    temps = [r["max_temp_c"] for r in rides if r["max_temp_c"] is not None]
    return {
        "rides": rides,
        "totals": {
            "ride_count": len(rides),
            "total_km": round(dist, 1),
            "mean_soc_per_km": round(sum(rated) / len(rated), 2) if rated else None,
            "max_temp_c": max(temps) if temps else None,
            "samples": len(records),
        },
    }


def revs_per_km(motor_rev, km):
    """Motor revs per km from the two odometer readings (exact, from stats)."""
    if not motor_rev or not km:
        return None
    return motor_rev / float(km)


def effective_ratio(rpk, circ_mm):
    """Effective final-drive ratio the MBB is programmed for."""
    if not rpk or not circ_mm:
        return None
    return rpk * float(circ_mm) / 1_000_000.0


def gearing_from_stats(stats, circ_mm):
    """(effective_ratio, revs_per_km, nearest_setup_description) from stats."""
    rpk = revs_per_km(stats.get("odo_motor_rev"), stats.get("odo_km"))
    r = effective_ratio(rpk, circ_mm)
    desc, _ = gearing.nearest_known(r)
    return r, rpk, desc
