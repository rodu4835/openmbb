"""Health snapshot: a saved session -> a list of readable health metrics.

Each metric is {label, value, status, note}. status is one of:
  ok    — within a healthy range
  watch — elevated / worth keeping an eye on
  alert — past a safety threshold
  info  — informational, no judgement
Everything degrades gracefully: a field the capture didn't include shows "n/a".
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


def _metric(label, value, status="info", note=""):
    return {"label": label, "value": value if value is not None else "n/a",
            "status": status, "note": note}


def _setting_num(settings, name, default=None):
    if name in settings:
        v = first_number(settings[name]["value"])
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    return default


def health_snapshot(session):
    bms = parse_bms(session.cmd("bms"))
    stats = parse_stats(session.cmd("stats"))
    status = parse_status(session.cmd("status"))
    settings, _ = parse_settings_dump(session.settings_text)
    out = []

    fw = stats.get("fw_rev") or bms.get("bms_fw_rev")
    out.append(_metric("Firmware rev", fw))

    soc = bms.get("soc_pct")
    if soc is None:
        soc = status.get("soc_pct")
    pack_v = bms.get("pack_v")
    out.append(_metric("Displayed SOC", "%g %%" % soc if soc is not None else None))
    out.append(_metric("Pack voltage", "%.2f V" % pack_v if pack_v is not None else None))
    if soc is not None and pack_v is not None:
        out.append(_metric("SOC vs voltage", "%g%% @ %.2f V" % (soc, pack_v),
                           "info", GAUGE_NOTE))

    bal = bms.get("balance_mv")
    if bal is not None:
        st = "ok" if bal < 30 else ("watch" if bal < 60 else "alert")
        out.append(_metric("Cell balance", "%g mV" % bal, st,
                           "spread across cells; <30 mV is healthy"))
    low = bms.get("low_cell_mv")
    if low is not None:
        out.append(_metric("Lowest cell", "%g mV" % low, "info"))

    cap, rem = bms.get("capacity_ah"), bms.get("remaining_ah")
    if cap is not None:
        val = "%g Ah" % cap + (" (%g Ah left)" % rem if rem is not None else "")
        out.append(_metric("Pack capacity", val, "info",
                           "nominal is 52 Ah; deep tracking of learned capacity "
                           "comes from the ride-log analysis"))
    cyc = bms.get("cycles")
    if cyc is not None:
        out.append(_metric("Charge cycles", "%g" % cyc, "info"))

    mot_t = stats.get("max_motor_temp_c")
    if mot_t is not None:
        s1 = _setting_num(settings, "motstage1", 100)
        s2 = _setting_num(settings, "motstage2", 145)
        st = "ok" if mot_t < s1 else ("watch" if mot_t < s2 else "alert")
        # C3: on rev 41 these thresholds are NOT in the `set` dump, so we fall back
        # to documented defaults — label them so the note isn't mistaken for a live
        # read of this bike's actual cutback points.
        from_bike = "motstage1" in settings and "motstage2" in settings
        src = "" if from_bike else " (documented defaults — not read from this bike)"
        out.append(_metric("Max motor temp", "%g C" % mot_t, st,
                           "cutback stages at %g / %g C%s" % (s1, s2, src)))
    batt_t = first_val(stats.get("max_batt_temp_c"), bms.get("pack_max_temp_c"))
    if batt_t is not None:
        st = "ok" if batt_t < 50 else ("watch" if batt_t < 60 else "alert")
        out.append(_metric("Max battery temp", "%g C" % batt_t, st,
                           "charge tapers ~43-50 C, operation stop ~50-60 C"))

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
        out.append(_metric("Isolation resistance", "%g kOhm" % iso, st,
                           ("healthy is megohms (>1000 kOhm). " + ctx).strip()))

    # F5: surface any live console warning as its own row
    for w in (status.get("warnings") or []):
        out.append(_metric("Warning", w, "watch", "live console warning message"))

    circ = _setting_num(settings, "rwhcirc", gearing.DEFAULT_CIRC_MM)
    ratio, rpk, desc = rides.gearing_from_stats(stats, circ)
    if ratio is not None:
        # F2: this is the LIFETIME-average ratio from the cumulative odometer —
        # it lags a recent re-gear for thousands of km. Say so in the note.
        out.append(_metric("Effective gearing", "%.2f:1" % ratio, "info",
                           "LIFETIME-average ratio (lags a recent re-gear for "
                           "thousands of km) — %s (%.0f motor-rev/km @ %g mm)"
                           % (desc or "?", rpk, circ)))
    return out
