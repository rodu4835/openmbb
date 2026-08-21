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


# A needle matches where it STARTS a word. Plain substring matching made
# "charge" match inside "dis-charge", so find(kv, "min", "charge", "temp")
# returned the value from `Min Discharge Temp` - which on the reference bike is
# -25 C, against a real Min Charge Temp of 0 C. It only ever gave the right
# answer for `Max Charge Temp` by the accident that no `Max Discharge Temp` line
# happened to precede it.
#
# Anchoring at a word start rather than requiring a whole word is deliberate:
# the parsers lean on prefix matches throughout ("rev" -> "revision", "batt" ->
# "battery"), and those are intended. A SUFFIX match never is.
_WORD_START = "(?<![a-z0-9])"


def find(kv, *needles):
    """Value of the first key in which every needle starts a word (lowercase)."""
    for k, v in kv.items():
        if all(re.search(_WORD_START + re.escape(n), k) for n in needles):
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


_CELL_IDX_RE = re.compile(r"\(\s*cell\s*(\d+)\s*\)", re.I)


def _cell_index(text, needle):
    """The cell number the BMS attributes a extreme reading to, or None."""
    for line in (text or "").splitlines():
        if needle in line.lower():
            m = _CELL_IDX_RE.search(line)
            return int(m.group(1)) if m else None
    return None


def parse_bms(text):
    """Normalize `bms` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n):
        return find(kv, *n)
    # only the real-sensor pack temps, not the -100C unused-sensor placeholders
    temps = real_temps(all_nums(g("pack", "temp")))
    cap_raw = first_val(g("capacity"), g("pack", "capacity"))
    caps = all_nums(cap_raw)
    # rev 41 gives the remaining charge its OWN label ("Pack Capacity Remaining
    # : 19 AH"); the simulator packs both onto one line ("52 Ah (32 Ah
    # remaining)"). Read the separate label first, then fall back to that form.
    # The pack's own declared operating limits, printed at permission level 0
    # and discarded until now. These are the bike's numbers rather than this
    # tool's documented defaults - but note they are CHARGE limits, so they
    # cannot legitimately grade a lifetime maximum that may have been set while
    # riding. They are reported, never used as a threshold.
    remaining = num(g("capacity", "remaining"))
    if remaining is None and len(caps) > 1 and "remain" in (cap_raw or "").lower():
        remaining = caps[1]
    return {
        "soc_pct": first_val(num(g("pack", "soc")), num(g("soc"))),
        "fuel_pct": num(g("fuel")),
        "pack_v": first_val(num(g("pack", "voltage")), num(g("voltage"))),
        "max_charge_temp_c": num(g("max", "charge", "temp")),
        "min_charge_temp_c": num(g("min", "charge", "temp")),
        "min_discharge_temp_c": num(g("min", "discharge", "temp")),
        "low_cell_mv": first_val(num(g("lowest", "cell")), num(g("low", "cell"))),
        # the console names the cell, not just the voltage:
        #   - Lowest Cell Voltage : 4078 mV ( Cell 25 )
        # a voltage says the pack is uneven; an INDEX says which cell, and a
        # specific cell is a repairable thing rather than a bad feeling
        "low_cell_index": _cell_index(text, "lowest cell"),
        "high_cell_index": _cell_index(text, "highest cell"),
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


# An unpopulated pack-temperature slot reads -100 C, and a real bike prints
# several: `Pack Temps : 27C 27C 27C 28C -100C -100C -100C -100C` is four live
# sensors and four empty slots. Averaging that list unguarded gives -36 C for a
# pack sitting at 27 C.
#
# The floor is NOT simply "drop the negatives". This platform legitimately
# reports temperatures below zero - the same bms block states a Min Discharge
# Temp of -25 C - and a bike left outside in winter genuinely reads below zero.
# -50 C sits below anything a Gen2 will survive being ridden at and well above
# the sentinel.
UNUSED_SENSOR_C = -100.0
TEMP_FLOOR_C = -50.0


def real_temps(values):
    """Drop unpopulated sensor slots from a list of temperatures.

    Reach for this rather than filtering by hand: the sentinel is not obviously
    a sentinel when you meet it, and an unguarded mean reads as a pack that is
    much colder than it is.
    """
    return [t for t in values if t is not None and t > TEMP_FLOOR_C]


def _pack_temp(line):
    # F3: 'PackTemp: h 27C, l 26C' -> the high reading (27); also plain 'PackTemp: 24C'.
    # Pack temp is safety-relevant (60 C = BMS cutback); motor temp at 60 C is benign.
    m = re.search(r"pack\s*temp\s*:?\s*(?:h\s*)?(-?\d+)", line, re.I)
    return float(m.group(1)) if m else None


def _batt_temp(line):
    """The older dialect's single `BattTemp: 24C`, or None.

    Deliberately NOT merged into pack_temp_c. That field is read as the pack's
    HIGHEST module - `PackTemp: h 60C, l 58C` gives 60 - which is what makes it
    comparable with the BMS lifetime counter. This dialect prints one number and
    no capture available establishes whether it is the hottest module, a mean
    across them, or a single sensor.

    If it is a mean, every peak derived from it reads COOLER than the pack
    actually got. This project grades a hot pack, so that error runs in the
    unsafe direction, and a reading that might be a mean must not be presented
    as a maximum. Keeping it in its own field loses nothing and claims nothing.
    """
    m = re.search(r"batt\s*temp\s*:?\s*(-?\d+)", line, re.I)
    return float(m.group(1)) if m else None


# A cell in a pack that is on its feet at all sits between these. A reading
# outside them is a decode artifact, not a cell. Two-sided on purpose: the
# fabrications seen on this platform run HIGH (8241 mV), but a rev-12 decode of
# the same era contains a 66 mV MinCell, so a one-sided guard would miss it.
CELL_MV_MIN, CELL_MV_MAX = 2000.0, 4300.0


def _curr_limit_pct(line):
    """The BMS discharge allowance as a percentage, from 'Curr limit: 520 A (100%)'.

    100 means no cutback. A pack that derates early — low percentages at moderate
    temperature and mid SOC — is saying something about itself, so the percentage
    is the datum worth keeping, not the amps it currently allows.
    """
    m = re.search(r"curr\s*limit\s*:?\s*[\d.]+\s*A\s*\(\s*(\d+)\s*%\)", line, re.I)
    return float(m.group(1)) if m else None


def _mode_samples(text, mode_word, required):
    """Every sample line for one bike mode, as dicts.

    `required` names the fields a line must carry to count: a line missing one
    of them is not a usable sample of that mode, rather than a sample with holes
    in it.
    """
    records = []
    for line in (text or "").splitlines():
        if mode_word not in line.lower():
            continue
        rec = {name: _ride_field(line, pat) for name, pat in _RIDE_FIELDS.items()}
        if any(rec.get(k) is None for k in required):
            continue
        rec["pack_temp_c"] = _pack_temp(line)
        rec["batt_temp_c"] = _batt_temp(line)
        rec["curr_limit_pct"] = _curr_limit_pct(line)
        rec["motor_temp_c"] = _ride_field(line, r"mottemp|motor temp")
        ts = _TS_RE.search(line)
        rec["ts"] = ts.group(0) if ts else None
        # Firmware re-reads records written by an OLDER firmware using its own,
        # longer layout and runs off the end of them, so the two trailing fields
        # come back as stale bytes: MinCell 8241 mV is 0x2031, the ASCII " 1".
        # Both fields sit at the end of the record, so when one is impossible
        # NEITHER is decodable — and the fabricated Curr limit percentage lands
        # inside 0-100 (observed: 0, 30, 31, 90, 91), so it cannot be caught by
        # range-checking itself. This is not an edge case on a used bike: if the
        # seller reflashed the MBB, every retained ride record predates the
        # flash, and an unguarded read reports a pack whose weakest cell never
        # sags at all. Measured: 502 of 502 pre-update ride records fabricated,
        # 0 of 635 after.
        mv = rec.get("mincell_mv")
        if mv is not None and not (CELL_MV_MIN <= mv <= CELL_MV_MAX):
            rec["mincell_mv"] = None
            rec["curr_limit_pct"] = None
        records.append(rec)
    return records


# The console's refusal, which several commands can produce:
#
#   Sorry, 'dumplogs' is an invalid command. Type "help" for a list of commands
#
# It is saved to the capture like any other reply, and it is not empty - so a
# bare `if text.strip()` accepts it as command output. `dumplogs` is the case
# that matters, because it is not a real rev-41 command and is still the
# fallback every event-log reader tries second.
_CONSOLE_REFUSAL_RE = re.compile(
    r"is an invalid command|invalid command\b|type \"help\" for a list", re.I)


def is_console_refusal(text):
    """True if a captured reply is the console declining, not answering.

    Checked against the whole reply rather than the first line, because the
    header the capture writes sits above it.
    """
    body = (text or "").strip()
    if not body or len(body) > 400:      # a real log is orders of magnitude bigger
        return False
    return bool(_CONSOLE_REFUSAL_RE.search(body))


def event_log_text(session, commands=("eventlogdump", "dumplogs")):
    """The first real event log in a session, or "".

    Shared so the several places that used to do this by hand cannot drift, and
    so a console refusal is rejected in all of them at once.
    """
    for cmd in commands:
        text = session.cmd(cmd) or ""
        if text.strip() and not is_console_refusal(text):
            return text
    return ""


def parse_ride_log(text):
    """Extract riding records from a dumplogs/eventlog block.

    Returns a list of dicts (ts, soc, vpack, pack_temp_c, motor_temp_c, motrpm,
    motamps, odo_km); only lines that mention "riding" and yield at least an soc
    and an odometer are kept, so charging/boot lines are ignored. Pack and motor
    temperature are separate fields — conflating them hides a real thermal alert.
    """
    return _mode_samples(text, "riding", ("soc", "odo_km"))


_LIMIT_RE = re.compile(
    r"Batt\s+(Dischg|Chg)\s+Cur\s+Limited\s+(\d+)\s*A\s*\(\s*([\d.]+)\s*%\)", re.I)


def parse_limit_events(text):
    """Current-limit events, which carry a weakest-cell reading of their own.

    A different channel from the riding lines, and the important one on older
    firmware: the BMS logs a line like

        Batt Dischg Cur Limited    379 A (72%), MinCell: 3567mV, MaxPackTemp: 49C

    every time it holds the discharge current back, and that line carries a
    cell voltage under genuine load. On this platform the two channels are
    complementary rather than redundant — the firmware that prints MinCell on
    its riding lines stops emitting these, and the firmware that emits these
    prints no MinCell on riding lines at all (366 of them against 0 either side
    of one bike's update). So a capture from a bike whose riding records carry
    no usable cell voltage is not necessarily silent about its cells.

    No pack voltage or state of charge on these lines, so they support an
    ABSOLUTE cell-floor check but not a comparison against the pack average.
    """
    out = []
    for line in (text or "").splitlines():
        m = _LIMIT_RE.search(line)
        if not m:
            continue
        mv = _ride_field(line, r"mincell|min cell")
        if mv is None or not (CELL_MV_MIN <= mv <= CELL_MV_MAX):
            continue
        ts = _TS_RE.search(line)
        out.append({
            "ts": ts.group(0) if ts else None,
            "kind": "discharge" if m.group(1).lower().startswith("dis") else "charge",
            "limit_amps": float(m.group(2)),
            "limit_pct": float(m.group(3)),
            "mincell_mv": mv,
            "pack_temp_c": _ride_field(line, r"maxpacktemp|max pack temp"),
        })
    return out


def parse_charge_log(text):
    """Extract charging records from the same block.

    A charging line carries no odometer, so pack voltage is required instead of
    distance — which suits what the charge side is for: measuring how much charge
    the pack accepts between two FIXED PACK VOLTAGES. That measurement does not
    go through the SOC display, which is the whole point of it. On this bike the
    displayed scale moved ~1.4x at a firmware update while charge accepted
    between the same two voltages moved ~3%, so anything anchored to the gauge is
    not comparable across firmware and anything anchored to voltage is.
    """
    return _mode_samples(text, "charging", ("soc", "vpack"))


# --- commands the tool captured for a year and never read --------------------

def _state_val(line):
    """The value from a state line, dropping the raw ADC tail the bike appends.

        - Kickstand Switch Pos      :      Down  - Raw : 2999 mV ( 4095 ADC)

    yields "Down". The raw counts are diagnostic detail for a bench session, not
    something a rider needs, and keeping them would bury the state they qualify.
    """
    _k, _sep, val = line.partition(":")
    return re.split(r"\s+-\s+Raw\b", val, maxsplit=1)[0].strip()


# --- a module-connect failure, and the three fields in it that are not data --
#
# A real line, from the reference bike:
#
#   ERROR: Cannot Connect Module 00! modv=0mV, maxv=0mV, minv=4294967295mV,
#   raw0:101372mV, raw1:0mV, cur0:0A, cur1:0A, diff_allowed:1575mV
#
# `modv`, `maxv` and `minv` are REFUSED, and the ground for refusing them is
# arithmetic rather than firmware knowledge: maxv (0 mV) is BELOW minv
# (4294967295 mV), and no max/min pair over a non-empty set can invert. Whatever
# those fields are, they are not a measured maximum and minimum, and 4294967295
# mV is not 4,294,967 volts.
#
# The predicate is `maxv < minv`, deliberately NOT a match on 0xFFFFFFFF. A
# constant match is pattern-recognition on one machine's output; the inversion
# test holds on any bike, any firmware and any module count, and it keeps working
# if a different sentinel shows up.
#
# What this must never say is WHY the module was ineligible. "The module did not
# answer" is a plausible mechanism and it is not in the line - the entry does not
# state a cause, and inventing one is exactly the confident wrongness the rest of
# this codebase refuses. raw0 IS a live reading (it moves line to line and sits at
# a plausible pack voltage) and may be read as one.

_MC_RE = re.compile(r"cannot\s+connect\s+module\s*(\w+)", re.I)

# a pack that exists at all is under this; anything above is not a voltage
_PLAUSIBLE_PACK_MV = 200000


def decode_module_connect_failure(line):
    """One 'Cannot Connect Module' entry, with its aggregates refused.

    Returns None for a line that is not one. `aggregates` is None whenever the
    triple cannot be believed, and `aggregates_refused_because` says which test
    it failed - never a cause for the failure itself, which the entry does not
    state.
    """
    m = _MC_RE.search(line or "")
    if not m:
        return None
    vals = {}
    for key in ("modv", "maxv", "minv", "raw0", "raw1", "diff_allowed"):
        mm = re.search(key + r"\s*[:=]\s*(-?\d+)", line, re.I)
        if mm:
            vals[key] = float(mm.group(1))
    out = {"module": m.group(1),
           "raw0_mv": vals.get("raw0"),
           "diff_allowed_mv": vals.get("diff_allowed"),
           "aggregates": None,
           "aggregates_refused_because": None}
    maxv, minv = vals.get("maxv"), vals.get("minv")
    if maxv is None or minv is None:
        out["aggregates_refused_because"] = "the entry carries no max/min pair"
        return out
    if maxv < minv:
        # %d, not %g: the whole point is that a reader sees 4294967295 rather
        # than 4.29497e+09, because the absurdity of the number IS the evidence
        out["aggregates_refused_because"] = (
            "maxv %d mV is below minv %d mV" % (maxv, minv))
        return out
    if any(v is not None and abs(v) > _PLAUSIBLE_PACK_MV
           for v in (vals.get("modv"), maxv, minv)):
        out["aggregates_refused_because"] = "a value is beyond any pack voltage"
        return out
    out["aggregates"] = {"modv_mv": vals.get("modv"), "maxv_mv": maxv,
                         "minv_mv": minv}
    return out


def parse_inputs(text):
    """The `inputs` block: switch positions and supply rails.

    These are the interlocks. When a Gen2 bike will not move, the answer is
    almost always in here — a kickstand still down, a kill switch at Off, a
    throttle the BMS has not enabled — and OpenMBB has been capturing it on every
    pull without ever showing it.
    """
    out = {}
    for line in (text or "").splitlines():
        low = line.lower()
        if ":" not in line:
            continue
        for key, needle in (("key_on", "key on"),
                            ("kill_switch", "kill switch"),
                            ("kickstand", "kickstand switch"),
                            ("start_switch", "start switch"),
                            ("brake_switch", "brake switch"),
                            ("throttle_enabled", "thr en"),
                            ("charger_attached", "charger 0 attached")):
            if needle in low and key not in out:
                out[key] = _state_val(line)
    for key, needle in (("pack_mv", "pack voltage"), ("supply_3v3", "3.3v supply"),
                        ("supply_5v", "5v supply")):
        for line in (text or "").splitlines():
            if needle in line.lower():
                out[key] = num(line.partition(":")[2])
                break
    return out


def parse_outputs(text):
    """The `outputs` block: what the MBB is driving, including the warning light."""
    out = {}
    for line in (text or "").splitlines():
        low = line.lower()
        if ":" not in line:
            continue
        for key, needle in (("warning_light", "warning light"),
                            ("temp_warning_led", "temp warning led"),
                            ("armed_led", "armed led"),
                            ("abs_light", "abs led"),
                            ("dcdc_enabled", "dc/dc converter en"),
                            ("system_on", "system on")):
            if needle in low and key not in out:
                out[key] = _state_val(line)
    return out


_RUNTIME_RE = re.compile(r"(\d+):(\d\d):(\d\d):(\d\d)")


def _runtime_seconds(line):
    m = _RUNTIME_RE.search(line or "")
    if not m:
        return None
    d, h, mi, s = (int(x) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def parse_runtime(text):
    """The `runtime` block, as seconds.

    Paired with the odometer this is a sanity check with teeth: run time that
    implies an impossible average speed means the two counters do not cover the
    same period, which on this platform means the statistics were reset.
    """
    out = {}
    for line in (text or "").splitlines():
        low = line.lower()
        if "total run time" in low:
            out["run_s"] = _runtime_seconds(line)
        elif "charger time" in low or "charge time" in low:
            out["charge_s"] = _runtime_seconds(line)
    return out


def parse_obd(text):
    """The `obd` block: fault codes, in the sense any mechanic would recognise.

    Presence or absence of a stored code needs no calibration and no reference
    bike, which makes it one of the few things a capture can say flatly.
    """
    out = {}
    for line in (text or "").splitlines():
        low = line.lower()
        if "mil on" in low:
            out["mil_on"] = bool(num(line.split("MIL On")[-1] if "MIL On" in line
                                     else line.partition("mil on")[2]))
        elif "active dtc" in low:
            out["active_dtcs"] = num(line.rsplit(None, 1)[-1])
        elif "pending dtc" in low:
            out["pending_dtcs"] = num(line.rsplit(None, 1)[-1])
    return out
