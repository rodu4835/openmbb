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
    return _finite(float(m.group(0)))


def _finite(x):
    """A float, or None if it is not a real reading.

    A long enough digit run overflows to inf - 309 characters does it - and inf
    then raises OverflowError downstream in the Fahrenheit conversion and in the
    derate profile. No console prints a 309-digit number, but the promise this
    module makes is about ANY input, and a value that cannot be compared or
    converted is not a reading. NaN is refused for the same reason.
    """
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def all_nums(s):
    """Every number in a string, as floats. Garbled decimals are dropped.

    `num` has always refused the malformed token the firmware really emits -
    "0.-51 A", which should be "-0.51" - because reading it as 0.0 turns a real
    negative into a wrong zero. `all_nums` did not share that guard and returned
    [0.0, -51.0], inventing TWO readings out of one unreadable token. Six such
    tokens are present in the real captures.

    Same rule as `num`, applied per match: a number immediately followed by "."
    and then a sign or digit is a garbled decimal, not a number.
    """
    if s is None:
        return []
    s = str(s)
    out, skip_to = [], -1
    for m in _NUM.finditer(s):
        if m.start() < skip_to:
            continue          # the tail of a garbled token already refused
        end = m.end()
        if end + 1 < len(s) and s[end] == "." and s[end + 1] in "-+0123456789":
            # Refuse the WHOLE token, not just its head. "0.-51" means -0.51, so
            # keeping the -51 would invent a reading a hundred times off - worse
            # than the 0.0 the missing guard used to produce, because it looks
            # plausible.
            tail = _NUM.search(s, end + 1)
            skip_to = tail.end() if tail else end + 1
            continue
        val = _finite(float(m.group(0)))
        if val is not None:
            out.append(val)
    return out


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


def find(kv, *needles, **kw):
    """Value of the first key in which every needle starts a word (lowercase).

    `exclude` names words that must NOT appear in the key. Label-fuzziness is
    this module's advertised contract - it is what lets these parsers meet a
    firmware nobody has seen - so `find` stays deliberately loose. But a
    qualifier can invert the meaning of the answer rather than merely widen it,
    and those the caller must rule out.

    The case that showed it: `find(kv, "pack", "temp")` reaches for the sensor
    list `Pack Temps`. Remove that line, as another firmware might, and the same
    call matches `Lowest Present Pack Temp` - reporting the pack's LOWEST sensor
    as its MAXIMUM, on the metric that grades a hot pack. The error ran in the
    unsafe direction, and the trigger was exactly the other-bike case this
    fuzziness exists to tolerate.
    """
    exclude = tuple(kw.pop("exclude", ()) or ())
    assert not kw, "unexpected kwargs: %s" % sorted(kw)
    for k, v in kv.items():
        if any(re.search(_WORD_START + re.escape(x), k) for x in exclude):
            continue
        if all(re.search(_WORD_START + re.escape(n), k) for n in needles):
            return v
    return None


def _unit_number(text, unit_re):
    """The number attached to a unit, or None if its digit run looks split.

    A reading is refused when another digit sits immediately before the matched
    run with only whitespace between them: that is one number broken in two, and
    taking the tail silently divides it. "6249 km" arriving as "62 49 km" would
    otherwise read 49.
    """
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:" + unit_re + r")", text)
    if not m:
        return None
    head = text[:m.start(1)]
    if head and head.rstrip() and head != head.rstrip():
        # whitespace immediately before the number - check what precedes it
        if head.rstrip()[-1].isdigit():
            return None
    return _finite(float(m.group(1)))


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
            motor_rev = _unit_number(low, r"(?:motor\s*)?rev")
        if km is None and "km/h" not in low and "wh" not in low:
            km = _unit_number(low, r"km\b")        # the km-bound number
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


# No Gen2 pack has this many cells - the reference bike is 28 in series. A
# number above it is a decode artifact rather than a cell, and returning it makes
# "which cell is weakest" answerable with something that is not a cell.
_MAX_PLAUSIBLE_CELL = 999


def _cell_index(text, needle):
    """The cell number the BMS attributes an extreme reading to, or None."""
    for line in (text or "").splitlines():
        if needle in line.lower():
            m = _CELL_IDX_RE.search(line)
            if not m:
                return None
            try:
                idx = int(m.group(1))
            except ValueError:          # a digit run longer than int() will take
                return None
            return idx if 0 < idx <= _MAX_PLAUSIBLE_CELL else None
    return None


def parse_bms(text):
    """Normalize `bms` output. All fields optional."""
    kv = parse_kv(text)
    def g(*n, **kw):
        return find(kv, *n, **kw)
    # only the real-sensor pack temps, not the -100C unused-sensor placeholders
    # `Pack Temps` is the sensor LIST. A superlative in the label means the
    # bike is answering a different question - `Lowest Present Pack Temp` is one
    # number, and taking max() of it reports the coldest sensor as the hottest.
    temps = real_temps(all_nums(
        g("pack", "temp", exclude=("lowest", "highest", "min", "max", "age"))))
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
    def g(*n, **kw):
        return find(kv, *n, **kw)
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
    def g(*n, **kw):
        return find(kv, *n, **kw)
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

    Tolerant of what it is handed, like everything else here: None, a string, a
    number, or a sequence carrying non-numbers all yield a list rather than a
    TypeError. No current caller can produce those, and the promise this module
    makes is not conditional on that staying true.
    """
    if values is None or isinstance(values, (str, bytes)):
        return []
    try:
        items = list(values)
    except TypeError:
        return []
    return [t for t in items
            if isinstance(t, (int, float)) and not isinstance(t, bool)
            and t == t and t > TEMP_FLOOR_C]


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


# OpenMBB's own marker, written by the transport when a read ended with no
# console prompt. It is not the bike's words, and a reply consisting only of it
# carries no records at all.
_OWN_TRUNCATION_RE = re.compile(r"^#{2,}\s*TRUNCATED", re.I | re.M)


def is_console_refusal(text):
    """True if a captured reply carries no command output to read.

    Two shapes qualify. The console DECLINING - "Sorry, 'dumplogs' is an invalid
    command" - and OpenMBB's own "### TRUNCATED" banner standing alone, which
    means a read produced a marker and nothing else.

    A genuinely truncated log is a different thing and stays readable: on real
    firmware a dump ending without a prompt is the NORMAL exit, and those replies
    carry a megabyte of records above the banner. Only a reply that is nothing
    BUT the banner is refused, which is why the size test comes first.

    Checked against the whole reply rather than the first line, because the
    header the capture writes sits above it.
    """
    body = (text or "").strip()
    if not body or len(body) > 400:      # a real log is orders of magnitude bigger
        return False
    return bool(_CONSOLE_REFUSAL_RE.search(body)
                or _OWN_TRUNCATION_RE.search(body))


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
    state = re.split(r"\s+-\s+Raw\b", val, maxsplit=1)[0].strip()
    # "" is a present key holding no answer, which reads as data downstream -
    # `{"kickstand": ""}` says the interlock WAS read and came back blank. The
    # num()-backed rails in the same block have always given None for this, and
    # None is the honest shape: the line was there and had nothing in it.
    return state or None


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


#: What rev 41 says after a `set`. Verified on the wire 2026-08-29:
#:   'SUCCESS  maxcustspmph set to 102'
#:   'FAILED  maxcustregcotq_allow could not be set to 55'
_WRITE_OK_RE = re.compile(r"^\s*SUCCESS\b(.*)$", re.M)
_WRITE_FAIL_RE = re.compile(r"^\s*FAILED\b(.*)$", re.M)


def console_write_result(reply):
    """What the console SAID about a write: ("success"|"failed", its own words).

    Returns (None, None) when the reply carries neither word - which is a real
    answer and must stay distinguishable from both. The tool used to assert "the
    console reported SUCCESS" on any unverified write, and at the bike it said
    that over a reply that read FAILED. Nothing here infers: it reports the
    console's line or reports nothing.

    The console's own sentence is returned so a surface can QUOTE it rather than
    paraphrase. `FAILED  maxcustregcotq_allow could not be set to 55` explains
    the refusal better than any inference drawn from a read-back diff.
    """
    text = str(reply or "")
    for kind, rx in (("failed", _WRITE_FAIL_RE), ("success", _WRITE_OK_RE)):
        m = rx.search(text)
        if m:
            said = ("%s %s" % (kind.upper(), m.group(1).strip())).strip()
            return kind, " ".join(said.split())
    return None, None


def settings_diff(before, after, ignore=()):
    """Names whose value moved between two parsed `set` dumps.

    Writing `spfront` on the reference bike changed four regen settings nobody
    touched, and reverting `spfront` did not put them back - the rider's brake
    regen sat at 90% instead of 77% until it was restored by hand. The write
    model assumed a write is local; the bike disagrees.

    Both dumps are already read on every write, so this costs nothing and needs
    no model of WHICH settings interact - which matters, because the behaviour is
    not uniform: `maxcustspmph` writes have no cross-effects at all, and
    `maxcustsprpm` tracked `spfront` and returned on its own.
    """
    moved = []
    for key, info in (after or {}).items():
        if key in ignore:
            continue
        was = (before or {}).get(key)
        if not was:
            continue
        old_v, new_v = was.get("value"), (info or {}).get("value")
        if old_v != new_v:
            moved.append((key, old_v, new_v))
    return sorted(moved)


def parse_sevcon(text):
    """The `sevcon` block: the motor controller's own view of itself.

    Read for the same reason `obd` is: a stored fault COUNT needs no reference
    bike and no calibration, so it is one of the few things a single capture can
    state flatly. The temperatures are returned because they are worth showing
    beside it, and they are NOT graded - nobody has established what a warm
    controller means on this platform, and inventing a threshold for the second
    most expensive part on the bike is exactly the kind of number this project
    refuses to print.

    Labels here overlap in ways that punish a substring match, which is why the
    excludes are explicit rather than incidental:

      `Motor Temp`  vs  `Max Motor Temp This Ride`  vs  `Age of motor temp data`
                    vs  `Motor Temp Control Mode`

    all four contain "motor temp", and the last is a MODE, not a temperature -
    it would have parsed as 0x01 degrees. `Controller Temp` and `Max Ctrl Temp`
    do not even share a word, so both spellings are asked for by name.
    """
    kv = parse_kv(text)
    if not kv:
        return {}

    def g(*needles, **kw):
        return find(kv, *needles, **kw)

    def n(value):
        """A number, unless it is written in hex - then it is unreadable.

        `num("0x1C")` reads the leading zero and returns 0.0, which in the
        graded field renders "none active" and an OK row: a fault count of 28
        presented as a clean bike. Hex is not hypothetical here - this very
        block prints 0x01, 0x00 and 0x010d0005 on neighbouring lines, so a
        firmware writing the count the same way is an ordinary thing to meet.

        Refused locally rather than in `num()`, which every parser shares; the
        general survey is a separate question. A refused value means the key is
        absent, which means no row at all - "could not read" said properly.
        """
        if value is None:
            return None
        if "0x" in str(value).lower():
            return None
        return num(value)

    out = {}
    for key, needles, excl in (
            ("motor_temp_c", ("motor", "temp"), ("max", "age", "control")),
            ("max_motor_temp_c", ("max", "motor", "temp"), ("age",)),
            ("controller_temp_c", ("controller", "temp"), ("max", "age")),
            ("max_controller_temp_c", ("max", "ctrl", "temp"), ("age",)),
    ):
        v = n(g(*needles, exclude=excl))
        if v is not None:
            out[key] = v

    faults = n(g("number", "faults"))
    if faults is not None:
        out["active_faults"] = faults

    mode = g("in", "operational", "mode")
    if mode is not None:
        low = str(mode).strip().lower()
        # None, not False, when the word is neither: a value we could not read
        # may never render as "not operational", which reads as a finding.
        out["operational"] = True if low.startswith("y") else (
            False if low.startswith("n") else None)

    for key, needles in (("fw_rev", ("sevcon", "firmware")),
                         ("serial", ("sevcon", "serial")),
                         ("dcf_rev", ("sevcon", "dcf"))):
        v = g(*needles)
        if v:
            out[key] = str(v).strip()

    odo = n(g("odometer", "km"))
    if odo is not None:
        # NOT the bike's mileage, and it must never be shown as though it were.
        # Measured on all three reference captures the moment this field first
        # parsed:
        #
        #   sevcon 16172.7 km  vs  mbb 6809.0 km    ratio 2.3752
        #   sevcon 16172.7 km  vs  mbb 6809.0 km    ratio 2.3752
        #   sevcon 18821.3 km  vs  mbb 7924.0 km    ratio 2.3752
        #
        # Identical to four decimals across two months and ~1,100 km of riding,
        # so it is a fixed scale factor, not drift. The controller's own km and
        # miles agree with each other (18821.3 / 11689.8 = 1.6100), so the block
        # is self-consistent - it is simply counting with a constant that is
        # not this bike's.
        #
        # The first explanation recorded here said the 2.37 WAS the reduction
        # between motor and wheel. This repo's own data refutes that:
        # gearing.py puts the FX/FXS stock reduction at 4.50, and the same
        # capture's `stats` measure 18,082,161 motor revs over 7,924 km =
        # 2,282 revs/km, almost exactly the 2,289 that 4.50 predicts. So ~4.49
        # sits between the motor and the wheel, and the Sevcon is running its
        # own distance model on a wrong constant (an assumed reduction near
        # 1.89, or an equivalent wheel-circumference error).
        #
        # Kept because the RATIO is the interesting quantity and a second bike
        # would establish whether that constant is a DCF default or per-bike.
        # Until then no surface prints it.
        out["odo_km"] = odo
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
            # Detected case-insensitively and, until this was measured, EXTRACTED
            # case-sensitively - so "MIL ON : 1" fell past the exact-case branch,
            # partitioned the UN-lowered line on a lowercase needle, found
            # nothing, and bool(None) collapsed to a definite False. The warning
            # lamp read OFF on a line that said it was ON, which is precisely the
            # failure this function's docstring claims fault codes are immune to.
            #
            # None now means "the line was there and its value could not be
            # read". That is a different thing from False and must stay so: a
            # check that could not run may never read as a pass.
            val = num(low.partition("mil on")[2])
            out["mil_on"] = None if val is None else bool(val)
        elif "active dtc" in low:
            out["active_dtcs"] = num(line.rsplit(None, 1)[-1])
        elif "pending dtc" in low:
            out["pending_dtcs"] = num(line.rsplit(None, 1)[-1])
    return out
