"""Tolerant parsers: captured console text -> structured data.

The REAL bike (firmware rev 41) may format its output differently from the
simulator and from the 2014-era community samples, so every parser here is
label-fuzzy and degrades to None rather than raising. Feed it whatever the bike
printed; missing fields come back None and the UI shows "n/a".
"""

import re

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def num(s):
    """First number in a string as float, or None.

    Guards the malformed-decimal token the MBB firmware can emit — the real
    ``chargers`` dump printed ``0.-51 A`` (should be ``-0.51``). The regex would
    match just the leading ``0`` and silently return ``0.0``, turning a real
    negative reading into a wrong zero. When the match is immediately followed by
    ``.`` and then a sign/digit (a garbled/truncated decimal, e.g. ``0.-51``),
    return None (renders as n/a) instead of a misleading value. A plain trailing
    period (``6809.``) is NOT affected — it isn't followed by a sign/digit.
    """
    if s is None:
        return None
    s = str(s)
    m = _NUM.search(s)
    if not m:
        return None
    end = m.end()
    if end + 1 < len(s) and s[end] == "." and s[end + 1] in "-+0123456789":
        return None
    return float(m.group(0))


def all_nums(s):
    """Every number in a string, as floats."""
    return [float(x) for x in _NUM.findall(str(s))] if s is not None else []


def first_val(*vals):
    """First value that is not None. Unlike `a or b`, a legitimate 0 / 0.0 is
    kept — so a pack at 0% SOC or a 0 C temperature is read as data, not missing."""
    for v in vals:
        if v is not None:
            return v
    return None


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


def top_speed_mph(text):
    """Top speed in MPH from a stats block.

    rev 41 prints the two units as separate lines, the second with a bare label:
        Top Speed     : 144 KPH
                      : 90 MPH
    while the simulator puts both on one line ("137 kph (85 mph)"). Read the
    explicit MPH figure wherever it sits, and convert from KPH only when the
    block never names MPH — so this can never hand back a KPH number as MPH.
    """
    block, saw = [], False
    for line in (text or "").splitlines():
        low = line.lower()
        stripped = line.strip()
        is_cont = stripped.startswith(":")           # a bare-label continuation
        if "top" in low and "speed" in low:
            saw = True
        elif stripped and not is_cont:
            saw = False                              # a new real label ends the block
        if saw:
            block.append(low)
    blob = " ".join(block)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*mph", blob)
    if m:
        return float(m.group(1))
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*k(?:p|m/)h", blob)
    return round(float(m.group(1)) * 0.621371, 1) if m else None


def parse_bms(text):
    """Normalize `bms` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n):
        return find(kv, *n)
    # only the real-sensor pack temps, not the -100C unused-sensor placeholders
    temps = [t for t in all_nums(g("pack", "temp")) if t > -50]
    cap_raw = first_val(g("capacity"), g("pack", "capacity"))
    caps = all_nums(cap_raw)
    # rev 41 gives the remaining charge its OWN label ("Pack Capacity Remaining
    # : 19 AH"); the simulator packs both onto one line ("52 Ah (32 Ah
    # remaining)"). Read the separate label first, then fall back to that form.
    remaining = num(g("capacity", "remaining"))
    if remaining is None and len(caps) > 1 and "remain" in (cap_raw or "").lower():
        remaining = caps[1]
    return {
        "soc_pct": first_val(num(g("pack", "soc")), num(g("soc"))),
        "fuel_pct": num(g("fuel")),
        "pack_v": first_val(num(g("pack", "voltage")), num(g("voltage"))),
        "low_cell_mv": first_val(num(g("lowest", "cell")), num(g("low", "cell"))),
        "high_cell_mv": first_val(num(g("highest", "cell")), num(g("high", "cell"))),
        "balance_mv": num(g("balance")),
        "capacity_ah": caps[0] if caps else None,
        "remaining_ah": remaining,
        "cycles": num(g("cycle")),
        "pack_max_temp_c": max(temps) if temps else None,
        # F5: isolation resistance (healthy = megohms; low can be an on-charger
        # false-positive). First 'isolation' key is the steady reading; it pins
        # at 0x7FFE (32766) when the measurement tops out. The live sample keys
        # off "instant iso", which the "isolation" needle can never reach, and
        # the BMS's own fault flag is the authoritative yes/no.
        "isolation_kohm": num(g("isolation")),
        "instant_isolation_kohm": num(g("instant", "iso")),
        "isolation_fault": g("isolation", "fault"),
        "bms_fw_rev": first_val(g("bms", "firmware", "rev"), g("firmware", "rev")),
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
        "lifetime_wh_km": first_val(num(g("lifetime")), num(g("efficiency"))),
        "top_speed_mph": top_speed_mph(text),
        "max_motor_rpm": first_val(num(g("max", "motor", "speed")), num(g("max", "rpm"))),
    }


def warning_lines(text):
    """Every 'WARNING : ...' message from a status block (its own section on the
    real bike). Returns a list of the messages, without the 'WARNING' prefix."""
    out = []
    for line in (text or "").splitlines():
        s = re.sub(r"^[\s\-\*]+", "", line.strip())
        # require the colon so the "Warning Messages" section HEADER is not matched
        m = re.match(r"WARNING\s*:\s*(.+)", s, re.I)
        if m and "no warning" not in m.group(1).lower():
            out.append(m.group(1).strip())
    return out


def parse_status(text):
    """Normalize `status` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n):
        return find(kv, *n)
    cap_raw = g("capacity")
    caps = all_nums(cap_raw)
    return {
        "mode": g("mode"),
        "soc_pct": first_val(num(g("soc")), num(g("battery", "soc"))),
        "motor_temp_c": num(g("motor", "temp")),
        "ctrl_temp_c": num(g("controller", "temp")),
        "capacity_ah": caps[0] if caps else None,
        # "Total Capacity" and "Remaining Capacity" are separate labels on rev 41
        "remaining_ah": first_val(num(g("remaining", "capacity")),
                                  caps[1] if len(caps) > 1 else None),
        "faults": first_val(g("number", "faults"), g("faults")),
        "warnings": warning_lines(text),
    }


_RIDE_FIELDS = {
    "soc": r"soc",
    "vpack": r"vpack",
    "motrpm": r"motrpm|mot rpm",
    "motamps": r"motamps|mot amps",
    "odo_km": r"odo",
    # rev 41 prints three more on every riding line that the tool was discarding:
    # pack-side current (energy accounting, as distinct from MOTOR current), the
    # weakest cell (sag under load is a far better cell test than spread at rest,
    # which moves with SOC), and ambient (separates a hot pack from a hot day).
    "battamps": r"battamps|batt amps",
    "mincell_mv": r"mincell|min cell",
    "amb_temp_c": r"ambtemp|amb temp",
}
_TS_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}:\d{2}")


def _ride_field(line, pattern):
    m = re.search(r"(?:%s)\s*[:=]?\s*(-?\d+(?:\.\d+)?)" % pattern, line, re.I)
    return float(m.group(1)) if m else None


def _pack_temp(line):
    # F3: 'PackTemp: h 27C, l 26C' -> the high reading (27); also plain 'PackTemp: 24C'.
    # Pack temp is safety-relevant (60 C = BMS cutback); motor temp at 60 C is benign.
    m = re.search(r"pack\s*temp\s*:?\s*(?:h\s*)?(-?\d+)", line, re.I)
    return float(m.group(1)) if m else None


def _curr_limit_pct(line):
    """The BMS discharge allowance as a percentage, from 'Curr limit: 520 A (100%)'.

    100 means no cutback. A pack that derates early — low percentages at moderate
    temperature and mid SOC — is saying something about itself, so the percentage
    is the datum worth keeping, not the amps it currently allows.
    """
    m = re.search(r"curr\s*limit\s*:?\s*[\d.]+\s*A\s*\(\s*(\d+)\s*%\)", line, re.I)
    return float(m.group(1)) if m else None


def parse_ride_log(text):
    """Extract riding records from a dumplogs/eventlog block.

    Returns a list of dicts (ts, soc, vpack, pack_temp_c, motor_temp_c, motrpm,
    motamps, odo_km); only lines that mention "riding" and yield at least an soc
    and an odometer are kept, so charging/boot lines are ignored. Pack and motor
    temperature are separate fields — conflating them hides a real thermal alert.
    """
    records = []
    for line in (text or "").splitlines():
        if "riding" not in line.lower():
            continue
        rec = {name: _ride_field(line, pat) for name, pat in _RIDE_FIELDS.items()}
        if rec["soc"] is None or rec["odo_km"] is None:
            continue
        rec["pack_temp_c"] = _pack_temp(line)
        rec["curr_limit_pct"] = _curr_limit_pct(line)
        rec["motor_temp_c"] = _ride_field(line, r"mottemp|motor temp")
        ts = _TS_RE.search(line)
        rec["ts"] = ts.group(0) if ts else None
        records.append(rec)
    return records
