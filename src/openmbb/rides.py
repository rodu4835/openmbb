"""Ride-log analysis: structured ride records -> per-ride summaries + gearing.

Consumes the records produced by parsers.parse_ride_log(). Splits a log into
individual rides, summarizes SOC%/km and temps per ride, and derives the
effective final-drive ratio from the odometer pair in `stats`.
"""

import datetime as _dt

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
    p_temps, m_temps = _span(seg, "pack_temp_c"), _span(seg, "motor_temp_c")
    rpms = _span(seg, "motrpm")
    dist = (max(odos) - min(odos)) if len(odos) >= 2 else 0.0
    dsoc = (max(socs) - min(socs)) if len(socs) >= 2 else 0.0
    return {
        "start_ts": seg[0].get("ts") if seg else None,
        "samples": len(seg),
        "distance_km": round(dist, 1),
        "soc_used_pct": round(dsoc, 1),
        "soc_per_km": round(dsoc / dist, 2) if dist > 0 else None,
        "max_pack_temp_c": max(p_temps) if p_temps else None,
        "max_motor_temp_c": max(m_temps) if m_temps else None,
        "max_rpm": max(rpms) if rpms else None,
    }


def summarize_rides(records):
    """Return {'rides': [per-ride summaries], 'totals': {...}}."""
    segs = split_rides(records)
    rides = [segment_summary(s) for s in segs]
    dist = sum(r["distance_km"] for r in rides)
    rated = [r["soc_per_km"] for r in rides if r["soc_per_km"] is not None]
    p_temps = [r["max_pack_temp_c"] for r in rides if r["max_pack_temp_c"] is not None]
    m_temps = [r["max_motor_temp_c"] for r in rides if r["max_motor_temp_c"] is not None]
    return {
        "rides": rides,
        "totals": {
            "ride_count": len(rides),
            "total_km": round(dist, 1),
            "mean_soc_per_km": round(sum(rated) / len(rated), 2) if rated else None,
            "max_pack_temp_c": max(p_temps) if p_temps else None,
            "max_motor_temp_c": max(m_temps) if m_temps else None,
            "samples": len(records),
        },
    }


# --- what it actually costs to ride this bike --------------------------------

# Samples further apart than this are a gap in the record, not an interval to
# integrate across. Riding samples arrive about once a minute.
_MAX_RIDE_STEP_S = 300.0

# A ride shorter than this is mostly the first and last odometer rounding: the
# odometer is whole kilometres, so a 2 km ride carries up to 50% quantisation
# error in its distance and none of it averages out.
MIN_RIDE_KM = 5.0

_RIDE_TS_FMT = "%m/%d/%Y %H:%M:%S"


def _ride_when(rec):
    try:
        return _dt.datetime.strptime(rec.get("ts") or "", _RIDE_TS_FMT)
    except ValueError:
        return None


def _segment_energy(seg):
    """(watt-hours drawn, kilometres covered) across one ride segment.

    Integrated from pack voltage and pack current, so it is what the PACK
    delivered - not what reached the wheel, and not what the bike's own lifetime
    Wh/km counter reports. Those are different numbers and the report says which
    one it is showing.
    """
    wh = km = 0.0
    for a, b in zip(seg, seg[1:]):
        ta, tb = _ride_when(a), _ride_when(b)
        if ta is None or tb is None:
            continue
        secs = (tb - ta).total_seconds()
        if not 0 < secs <= _MAX_RIDE_STEP_S:
            continue
        v, i = a.get("vpack"), a.get("battamps")
        if v is None or i is None:
            continue
        wh += abs(v * i) * secs / 3600.0
        oa, ob = a.get("odo_km"), b.get("odo_km")
        if oa is not None and ob is not None and 0 <= ob - oa < 50:
            km += ob - oa
    return wh, km


def consumption(records):
    """Energy per kilometre, measured ride by ride. None if nothing qualifies.

    Reported as a median with a middle-80% band rather than one number, because
    the spread is real and large: on the reference bike, rides run from about 70
    to about 109 Wh/km depending on how they were ridden. A single figure would
    imply a precision this does not have.
    """
    per_ride = []
    total_wh = total_km = 0.0
    for seg in split_rides(records):
        wh, km = _segment_energy(seg)
        total_wh += wh
        total_km += km
        if km >= MIN_RIDE_KM and wh > 0:
            per_ride.append(wh / km)
    if not per_ride:
        return None
    per_ride.sort()
    n = len(per_ride)
    ambs = sorted(r["amb_temp_c"] for r in records
                  if r.get("amb_temp_c") is not None)
    return {
        "rides": n,
        "km": round(total_km, 1),
        "wh_per_km": round(per_ride[n // 2], 1),
        "wh_per_km_low": round(per_ride[n // 10], 1),
        "wh_per_km_high": round(per_ride[min(n - 1, int(n * 0.9))], 1),
        # consumption climbs in the cold, so the temperatures this was measured
        # across are part of the measurement rather than a footnote to it
        "amb_low_c": ambs[0] if ambs else None,
        "amb_high_c": ambs[-1] if ambs else None,
        "at_the_pack": True,
    }


def range_estimate(records):
    """How far a full charge goes, from the deepest discharge actually logged.

    Deliberately NOT derived from the BMS capacity figure. On the reference bike
    the BMS reports 52 Ah nominal while the gauge behaves like a pack barely two
    thirds that size, and a range built on the larger number would be a third
    too long.

    Instead: take the deepest single discharge in the log, which is a distance
    and an SOC drop both actually ridden, and scale it to a full 0-100%. The
    scaling is the weak step and is reported as such - it assumes the SOC scale
    is linear and that 0% is reachable, and on this platform neither is
    established. `soc_floor_pct` carries the lowest SOC the log has ever seen so
    a reader can judge how far the extrapolation reaches past the evidence.
    """
    best = None
    for seg in split_rides(records):
        socs = [r["soc"] for r in seg if r.get("soc") is not None]
        wh, km = _segment_energy(seg)
        if len(socs) < 2 or km < MIN_RIDE_KM:
            continue
        used = max(socs) - min(socs)
        if used <= 0:
            continue
        if best is None or used > best["soc_used_pct"]:
            best = {"km": round(km, 1), "soc_used_pct": round(used, 1),
                    "from_soc_pct": max(socs), "to_soc_pct": min(socs),
                    "start_ts": seg[0].get("ts"), "_wh": wh}
    if best is None:
        return None
    all_socs = [r["soc"] for r in records if r.get("soc") is not None]
    out = dict(best)
    wh = out.pop("_wh")
    out["full_charge_km"] = round(best["km"] * 100.0 / best["soc_used_pct"], 1)
    # A cross-check with teeth, and free: the same ride measured two unrelated
    # ways. Energy integrated from pack voltage and current, scaled by the SOC
    # drop, gives the pack size the gauge is behaving like - and it can be held
    # against what the BMS claims. On the reference bike that comes out near
    # 3.2 kWh against a reported 52 Ah (~5.7 kWh) nominal, which is the same
    # discrepancy the SOC-rescale note on the Health tab describes, reached from
    # a completely different direction.
    out["implied_pack_wh"] = round(wh * 100.0 / best["soc_used_pct"])
    out["soc_floor_pct"] = min(all_socs) if all_socs else None
    # the honest framing: an upper bound on what the gauge implies, not a
    # distance anyone has ridden
    out["is_extrapolation"] = best["soc_used_pct"] < 100
    out["graded"] = False
    return out


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
