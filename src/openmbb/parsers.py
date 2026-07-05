"""Tolerant parsers: captured console text -> structured data.

The REAL bike (firmware rev 41) may format its output differently from the
simulator and from the 2014-era community samples, so every parser here is
label-fuzzy and degrades to None rather than raising. Feed it whatever the bike
printed; missing fields come back None and the UI shows "n/a".
"""

import re

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def num(s):
    """First number in a string as float, or None."""
    if s is None:
        return None
    m = _NUM.search(str(s))
    return float(m.group(0)) if m else None


def all_nums(s):
    """Every number in a string, as floats."""
    return [float(x) for x in _NUM.findall(str(s))] if s is not None else []


def parse_kv(text):
    """Parse 'label : value' (or 'label - value') lines into {label_lower: value}.

    Tolerant of leading bullets/dashes/stars and the '=== header ===' lines the
    simulator emits (those are skipped). First occurrence of a label wins.
    """
    out = {}
    for line in (text or "").splitlines():
        s = re.sub(r"^[\s\-\*]+", "", line.rstrip())
        if s.startswith("="):
            continue
        if ":" in s:
            k, _, v = s.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k and k not in out:
                out[k] = v
    return out


def find(kv, *needles):
    """Value of the first key that contains ALL needle words (all lowercase)."""
    for k, v in kv.items():
        if all(n in k for n in needles):
            return v
    return None


def parse_odometer(text):
    """Return (motor_rev, km) from a stats/dash block.

    Handles the multi-line Zero format where km/miles are continuation lines
    with an empty label, e.g.:
        Odometer      : 33956225 motor rev
                      : 15489 km
                      : 9624 miles
    """
    motor_rev = km = None
    saw_odo = False        # inside the lifetime-Odometer block (for continuations)
    for line in (text or "").splitlines():
        low = line.lower()
        stripped = line.strip()
        is_cont = stripped.startswith(":")          # a bare-label continuation
        if "odo" in low and "trip" not in low:
            saw_odo = True
        elif stripped and not is_cont:
            saw_odo = False                          # a new real label ends the block
        # only read from the true (non-trip) odometer line or its continuation
        if not (("odo" in low and "trip" not in low) or (is_cont and saw_odo)):
            continue
        if (motor_rev is None and "rev" in low
                and "firm" not in low and "revision" not in low):
            m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:motor\s*)?rev", low)
            if m:
                motor_rev = float(m.group(1))
        if km is None and "km/h" not in low and "wh" not in low:
            m = re.search(r"(-?\d+(?:\.\d+)?)\s*km\b", low)   # the km-bound number
            if m:
                km = float(m.group(1))
    return motor_rev, km


def parse_bms(text):
    """Normalize `bms` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n):
        return find(kv, *n)
    temps = all_nums(g("pack", "temp"))
    cap_raw = g("capacity") or g("pack", "capacity")
    caps = all_nums(cap_raw)
    remaining = None
    if len(caps) > 1 and "remain" in (cap_raw or "").lower():
        remaining = caps[1]
    return {
        "soc_pct": num(g("pack", "soc")) or num(g("soc")),
        "fuel_pct": num(g("fuel")),
        "pack_v": num(g("pack", "voltage")) or num(g("voltage")),
        "low_cell_mv": num(g("lowest", "cell")) or num(g("low", "cell")),
        "high_cell_mv": num(g("highest", "cell")) or num(g("high", "cell")),
        "balance_mv": num(g("balance")),
        "capacity_ah": caps[0] if caps else None,
        "remaining_ah": remaining,
        "cycles": num(g("cycle")),
        "pack_max_temp_c": max(temps) if temps else None,
        "bms_fw_rev": g("bms", "firmware", "rev") or g("firmware", "rev"),
    }


def parse_stats(text):
    """Normalize `stats` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n):
        return find(kv, *n)
    motor_rev, km = parse_odometer(text)
    return {
        "fw_rev": g("firmware", "rev"),
        "odo_km": km,
        "odo_motor_rev": motor_rev,
        "max_batt_temp_c": num(g("max", "battery", "temp")),
        "max_motor_temp_c": num(g("max", "motor", "temp")),
        "max_ctrl_temp_c": num(g("max", "controller", "temp")),
        "lifetime_wh_km": num(g("lifetime")) or num(g("efficiency")),
        "top_speed_mph": (all_nums(g("top", "speed"))[-1]
                          if all_nums(g("top", "speed")) else None),
        "max_motor_rpm": num(g("max", "motor", "speed")) or num(g("max", "rpm")),
    }


def parse_status(text):
    """Normalize `status` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n):
        return find(kv, *n)
    cap_raw = g("capacity")
    caps = all_nums(cap_raw)
    return {
        "mode": g("mode"),
        "soc_pct": num(g("soc")) or num(g("battery", "soc")),
        "motor_temp_c": num(g("motor", "temp")),
        "ctrl_temp_c": num(g("controller", "temp")),
        "capacity_ah": caps[0] if caps else None,
        "remaining_ah": (caps[1] if len(caps) > 1 else None),
        "faults": g("number", "faults") or g("faults"),
    }


_RIDE_FIELDS = {
    "soc": r"soc",
    "vpack": r"vpack",
    "temp_c": r"packtemp|pack temp|mottemp",
    "motrpm": r"motrpm|mot rpm",
    "motamps": r"motamps|mot amps",
    "odo_km": r"odo",
}
_TS_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}:\d{2}")


def _ride_field(line, pattern):
    m = re.search(r"(?:%s)\s*[:=]?\s*(-?\d+(?:\.\d+)?)" % pattern, line, re.I)
    return float(m.group(1)) if m else None


def parse_ride_log(text):
    """Extract riding records from a dumplogs/eventlog block.

    Returns a list of dicts (ts, soc, vpack, temp_c, motrpm, motamps, odo_km);
    only lines that mention "riding" and yield at least an soc and an odometer
    are kept, so charging/boot lines are ignored.
    """
    records = []
    for line in (text or "").splitlines():
        if "riding" not in line.lower():
            continue
        rec = {name: _ride_field(line, pat) for name, pat in _RIDE_FIELDS.items()}
        if rec["soc"] is None or rec["odo_km"] is None:
            continue
        ts = _TS_RE.search(line)
        rec["ts"] = ts.group(0) if ts else None
        records.append(rec)
    return records
