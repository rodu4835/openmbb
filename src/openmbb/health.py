"""Health snapshot: a saved session -> a list of health metrics.

Each metric is {label, value, unit, display, status, note}:

  value    the datum itself — a number where the metric is numeric, a string
           where it is not (firmware rev, a warning message), or None when the
           capture did not include it. ALWAYS in canonical units, so a
           threshold comparison never has to care what the user set.
  unit     canonical unit for `value` ("V", "mV", "C", "kOhm", ...) or None.
  display  the human-facing rendering, honouring `temp_units`. This is what the
           GUI shows; it is the only field that changes with a display setting.
  status   ok | watch | alert | info  (see below)
  note     rationale, thresholds, and caveats

Why value and display are separate: temperatures render in F when the user asks
for it, but every threshold in here is in Celsius. Keeping `value` canonical
means an automated consumer gets stable numbers, while the GUI reads `display`.
Anything programmatic should use value/unit and ignore display.

status is one of:
  ok    — within a healthy range
  watch — elevated / worth keeping an eye on
  alert — past a safety threshold
  info  — informational, no judgement

Everything degrades gracefully: a field the capture didn't include has
value None and displays "n/a".
"""

from . import gearing, rides
from .parsers import first_val, parse_bms, parse_stats, parse_status
from .transport import first_number, parse_settings_dump

# F1: the matched before/after firmware log analysis (MBB 12 vs 41, ~1100
# samples) REFUTED the "gauge reads ~1.55x steeper" theory — the SoC-vs-voltage
# curve and the 100% top voltage were unchanged. Read displayed SOC as-is.
GAUGE_NOTE = ("Matched before/after firmware analysis (MBB 12 vs 41) found the "
              "SoC-vs-voltage curve UNCHANGED (+/-1%) and 100% top voltage "
              "unchanged (116.9 V). Read displayed SOC as-is — do NOT mentally "
              "inflate a low reading.")


# T12: Modes that POSITIVELY mean "not on the charger" (seen in real rev-41
# captures — the live session's Mode was `Stopped`). Anything else — a blank
# Mode, or an unrecognized/garbled one — leaves the charge state UNKNOWN, which
# must NOT be treated as a confirmed off-charger reading.
KNOWN_OFF_MODES = ("stopped", "standby", "run", "running", "riding", "idle")

# The BMS pins its steady isolation reading at 0x7FFE when the measurement is at
# the top of its scale — a ceiling code, not a 32.8 MOhm measurement. Seen
# bit-identical across every healthy real capture while neighbouring fields moved.
ISO_CEILING_KOHM = 32766


def _metric(label, value, unit=None, status="info", note="", display=None):
    """One metric row. `display` defaults to "<value> <unit>"; pass it explicitly
    for composites ("61% @ 116.24 V") or any non-default formatting."""
    if display is None:
        if value is None:
            display = "n/a"
        elif unit:
            display = "%s %s" % (_num(value), unit)
        else:
            display = "%s" % (_num(value),)
    return {"label": label, "value": value, "unit": unit,
            "display": display, "status": status, "note": note}


def fmt_temp(v, temp_units="C"):
    """A Celsius datum rendered for display. Every threshold in this program
    compares in Celsius; this is the one place a temperature follows the user's
    setting, so anything showing a temperature should render it through here.
    Returns None for a missing datum — the caller decides how to say "n/a"."""
    if v is None:
        return None
    return "%g F" % round(v * 9 / 5 + 32) if temp_units == "F" else "%g C" % v


def _num(v):
    """Render a number the way %g does (no trailing .0) but leave strings alone."""
    return "%g" % v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _setting_num(settings, name, default=None):
    if name in settings:
        v = first_number(settings[name]["value"])
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    return default


def _setting_num_live(settings, name, default):
    """(value, is_live): the live parsed number if `name` is present AND numeric,
    else the documented `default` flagged not-live. Used so a defaulted threshold
    is never presented as a read of this bike (review REG-2)."""
    if name in settings:
        try:
            return float(first_number(settings[name]["value"])), True
        except (TypeError, ValueError):
            pass
    return float(default), False


def health_snapshot(session, temp_units="C"):
    bms = parse_bms(session.cmd("bms"))
    stats = parse_stats(session.cmd("stats"))
    status = parse_status(session.cmd("status"))
    settings, _ = parse_settings_dump(session.settings_text)
    out = []

    def _t(v):
        return fmt_temp(v, temp_units)

    def _rng(a, b):
        if temp_units == "F":
            return "%d-%d F" % (round(a * 9 / 5 + 32), round(b * 9 / 5 + 32))
        return "%d-%d C" % (a, b)

    fw = stats.get("fw_rev") or bms.get("bms_fw_rev")
    out.append(_metric("Firmware rev", fw))

    soc = bms.get("soc_pct")
    if soc is None:
        soc = status.get("soc_pct")
    pack_v = bms.get("pack_v")
    out.append(_metric("Displayed SOC", soc, "%"))
    out.append(_metric("Pack voltage", pack_v, "V",
                       display=("%.2f V" % pack_v) if pack_v is not None else None))
    if soc is not None and pack_v is not None:
        # A cross-check row, not a measurement — both numbers already have their
        # own rows above, so this one carries the caveat rather than a datum.
        out.append(_metric("SOC vs voltage", None,
                           display="%g%% @ %.2f V" % (soc, pack_v), note=GAUGE_NOTE))

    bal = bms.get("balance_mv")
    if bal is not None:
        st = "ok" if bal < 30 else ("watch" if bal < 60 else "alert")
        out.append(_metric("Cell balance", bal, "mV", status=st,
                           note="spread across cells; <30 mV is healthy"))
    low = bms.get("low_cell_mv")
    if low is not None:
        out.append(_metric("Lowest cell", low, "mV"))
    high = bms.get("high_cell_mv")
    if high is not None:
        out.append(_metric("Highest cell", high, "mV"))
    if low is not None and high is not None:
        spread = high - low
        st = "ok" if spread < 40 else ("watch" if spread < 80 else "alert")
        out.append(_metric("Cell spread", spread, "mV", status=st,
                           note="highest minus lowest cell — a large spread signals "
                                "imbalance"))

    cap, rem = bms.get("capacity_ah"), bms.get("remaining_ah")
    if cap is not None:
        val = "%g Ah nominal" % cap + (" (%g Ah at this charge)" % rem
                                       if rem is not None else "")
        out.append(_metric("Pack capacity", cap, "Ah", display=val,
                           note="the design nominal (~52 Ah) and how much is left at the "
                                "CURRENT charge — not a degradation measurement. Learned "
                                "capacity comes from the ride-log analysis."))
    cyc = bms.get("cycles")
    if cyc is not None:
        out.append(_metric("Charge cycles", cyc))
    odo = stats.get("odo_km")
    if odo is not None:
        out.append(_metric("Odometer", odo, "km", note="lifetime distance"))
    eff = stats.get("lifetime_wh_km")
    if eff is not None:
        out.append(_metric("Lifetime efficiency", eff, "Wh/km",
                           note="lifetime-average energy use"))

    mot_t = stats.get("max_motor_temp_c")
    if mot_t is not None:
        s1, live1 = _setting_num_live(settings, "motstage1", 100)
        s2, live2 = _setting_num_live(settings, "motstage2", 145)
        st = "ok" if mot_t < s1 else ("watch" if mot_t < s2 else "alert")
        # C3/REG-2: rev 41 exposes NEITHER threshold in `set`, so both fall back to
        # documented defaults — label each value's provenance so a mix (or a garbled
        # single value) is never presented as a live read of this bike.
        def _lbl(v, live):
            return _t(v) if live else "%s (default)" % _t(v)
        note = ("highest EVER recorded, not the current temperature. Cutback "
                "stages at %s / %s" % (_lbl(s1, live1), _lbl(s2, live2)))
        if not (live1 and live2):
            note += " — '(default)' = documented default, not read from this bike"
        # value stays Celsius whatever the user displays in: every threshold
        # compared above is Celsius, so that is the canonical unit.
        out.append(_metric("Max motor temp (lifetime)", mot_t, "C",
                           status=st, note=note, display=_t(mot_t)))
    batt_t = first_val(stats.get("max_batt_temp_c"), bms.get("pack_max_temp_c"))
    if batt_t is not None:
        st = "ok" if batt_t < 50 else ("watch" if batt_t < 60 else "alert")
        out.append(_metric("Max battery temp (lifetime)", batt_t, "C",
                           status=st, display=_t(batt_t),
                           note="highest EVER recorded, not the current temperature; "
                                "charge tapers ~%s, operation stop ~%s"
                                % (_rng(43, 50), _rng(50, 60))))
    ctrl_t = stats.get("max_ctrl_temp_c")
    if ctrl_t is not None:
        st = "ok" if ctrl_t < 70 else ("watch" if ctrl_t < 90 else "alert")
        out.append(_metric("Max controller temp (lifetime)", ctrl_t, "C",
                           status=st, display=_t(ctrl_t),
                           note="Sevcon controller — highest ever recorded"))

    # F5: isolation resistance — healthy is megohms (>1000 kΩ). A low reading on
    # the charger is a documented false-positive, so soften the flag when the
    # status Mode is Charging and say why.
    iso = bms.get("isolation_kohm")
    if iso is not None:
        # T12: charge state is THREE-valued. Charging is the owner's normal power
        # setup and the #1 false-low condition; 'not charging' is only KNOWN when
        # Mode positively says so (KNOWN_OFF_MODES) — an absent/unrecognized Mode
        # must NOT be asserted as off-charger. Threshold aligns with the note:
        # >=1000 kOhm ok; 500-999 kOhm is a mid-band (watch, not alert).
        mode = (status.get("mode") or "").lower()
        mode_word = (mode.replace(",", " ").split() or [""])[0]
        charging = mode_word.startswith("charg")
        known_off = mode_word in KNOWN_OFF_MODES
        if iso >= 1000:
            st, ctx = "ok", ""
        elif charging:
            st, ctx = "watch", ("Read while CHARGING — known false-low; re-read "
                                "unplugged + dry before acting.")
        elif known_off and iso >= 500:
            st, ctx = "watch", ("Mildly low (500-999 kOhm) off-charger — keep an "
                                "eye on it; re-read unplugged + dry to confirm.")
        elif known_off:
            st, ctx = "alert", "Low off-charger is a real diagnostic; investigate."
        else:
            st, ctx = "watch", ("Charge state unknown — a low reading on the charger "
                                "is a documented false-low; re-read unplugged + dry "
                                "before acting.")
        note = ("healthy is megohms (>1000 kOhm). " + ctx).strip()
        # the live sample and the BMS's own verdict were captured all along and
        # never shown; the flag is the authoritative yes/no, the sample is noisy
        detail = []
        inst = bms.get("instant_isolation_kohm")
        if inst is not None and inst >= 0:
            detail.append("live sample %g kOhm" % inst)
        elif inst is not None:
            # the field can come back as a signed sentinel (a real 2026-08-19 read
            # printed "-25 KOhms (0xFFFFFFE7)"); a negative resistance is not a
            # measurement, so say it was unavailable rather than render nonsense
            detail.append("live sample unavailable this read")
        if bms.get("isolation_fault"):
            detail.append("BMS isolation fault: %s" % bms["isolation_fault"])
        if detail:
            note += "  (%s)" % "; ".join(detail)
        out.append(_metric("Isolation resistance", iso, "kOhm", status=st, note=note,
                           display=("at or above %g kOhm (sensor ceiling)" % iso
                                    if iso >= ISO_CEILING_KOHM else None)))

    # F5: surface any live console warning as its own row
    for w in (status.get("warnings") or []):
        out.append(_metric("Warning", w, status="watch",
                           note="live console warning message"))

    circ = _setting_num(settings, "rwhcirc", gearing.DEFAULT_CIRC_MM)
    ratio, rpk, desc = rides.gearing_from_stats(stats, circ)
    if ratio is not None:
        # F2: this is the LIFETIME-average ratio from the cumulative odometer —
        # it lags a recent re-gear for thousands of km. Say so in the note.
        out.append(_metric("Effective gearing", ratio, display="%.2f:1" % ratio,
                           note="LIFETIME-average ratio (lags a recent re-gear for "
                                "thousands of km) — %s (%.0f motor-rev/km @ %g mm)"
                                % (desc or "?", rpk, circ)))
    return out
