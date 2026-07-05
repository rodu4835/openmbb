"""Health snapshot: a saved session -> a list of readable health metrics.

Each metric is {label, value, status, note}. status is one of:
  ok    — within a healthy range
  watch — elevated / worth keeping an eye on
  alert — past a safety threshold
  info  — informational, no judgement
Everything degrades gracefully: a field the capture didn't include shows "n/a".
"""

from . import gearing, rides
from .parsers import parse_bms, parse_stats, parse_status
from .transport import first_number, parse_settings_dump

GAUGE_NOTE = ("Post-2026-06-13 firmware reads SOC ~1.55x steeper than older "
              "firmware; ~100.8 V at rest now shows ~20% (was ~38%).")


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
        out.append(_metric("Max motor temp", "%g C" % mot_t, st,
                           "cutback stages at %g / %g C" % (s1, s2)))
    batt_t = stats.get("max_batt_temp_c") or bms.get("pack_max_temp_c")
    if batt_t is not None:
        st = "ok" if batt_t < 50 else ("watch" if batt_t < 60 else "alert")
        out.append(_metric("Max battery temp", "%g C" % batt_t, st,
                           "charge tapers ~43-50 C, operation stop ~50-60 C"))

    circ = _setting_num(settings, "rwhcirc", gearing.DEFAULT_CIRC_MM)
    ratio, rpk, desc = rides.gearing_from_stats(stats, circ)
    if ratio is not None:
        out.append(_metric("Effective gearing", "%.2f:1" % ratio, "info",
                           "%s (%.0f motor-rev/km @ %g mm)"
                           % (desc or "?", rpk, circ)))
    return out
