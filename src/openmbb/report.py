"""One saved session -> a structured report, plus a text rendering of it.

`analyze_session` is deliberately pure: a Session in, a JSON-ready dict out. No
hardware, no serial port, no GUI, no I/O beyond the folder read that
`sessions.load_session` already did. That is what lets `openmbb analyze` run on
a session someone emailed you, and what makes the same analysis callable from
anything that is not this program.

The report is JSON-safe by construction — every value is a str, number, bool,
None, list, or dict — so `json.dumps` on the result never needs a custom
encoder.
"""

from . import health, parsers, rides, sessions

# Where ride telemetry lives in a capture, best first. `eventlogdump` is the
# full console event log; `dumplogs` is the older command kept for sessions
# captured before the switch.
RIDE_LOG_COMMANDS = ("eventlogdump", "dumplogs")


def analyze_session(session, temp_units="C"):
    """Everything derivable from a saved session, as one JSON-ready dict."""
    metrics = health.health_snapshot(session, temp_units)

    ride_text, ride_source = "", None
    for cmd in RIDE_LOG_COMMANDS:
        text = session.cmd(cmd) or ""
        if text.strip():
            ride_text, ride_source = text, cmd
            break

    ride_summary = None
    truncated = False
    if ride_text:
        records = parsers.parse_ride_log(ride_text)
        if records:
            ride_summary = rides.summarize_rides(records)
            # A capture that ended early is missing entries, so its totals are a
            # floor, not a measurement. Say so rather than let them read as whole.
            truncated = "### TRUNCATED" in ride_text
        else:
            ride_source = None

    return {
        "session": {
            "name": session.name,
            "path": session.dir,
            "commands": sorted(session.commands),
            "has_settings": bool((session.settings_text or "").strip()),
        },
        "units": temp_units,
        "counts": _count_by_status(metrics),
        "health": metrics,
        "rides": ride_summary,
        "ride_source": ride_source,
        "ride_log_truncated": truncated,
    }


def analyze_folder(folder, temp_units="C"):
    """Convenience: load a session folder from disk and analyze it."""
    return analyze_session(sessions.load_session(folder), temp_units)


def _count_by_status(metrics):
    counts = {"ok": 0, "watch": 0, "alert": 0, "info": 0}
    for m in metrics:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    return counts


def format_report(report):
    """Human-readable rendering. Mirrors what the Analyze tab shows."""
    s, out = report["session"], []
    out.append("Session: %s" % s["name"])
    out.append("Path:    %s" % s["path"])
    c = report["counts"]
    out.append("Health:  %d ok / %d watch / %d alert  (%d informational)"
               % (c["ok"], c["watch"], c["alert"], c["info"]))
    out.append("")
    out.append("== Health (ok / watch / alert) ==")
    for m in report["health"]:
        out.append("  [%-5s] %-26s %s" % (m["status"].upper(), m["label"], m["display"]))
        if m["note"]:
            out.append("           %s" % m["note"])

    ride = report.get("rides")
    if ride:
        t = ride["totals"]
        out += ["", "== Rides (from %s) ==" % report.get("ride_source")]
        if report.get("ride_log_truncated"):
            out.append("  ! the event-log capture ended early — totals are a floor,")
            out.append("    not a complete measurement. Re-pull for the full log.")
        out.append("  %d ride(s) over %s km from %d samples"
                   % (t["ride_count"], t["total_km"], t["samples"]))
        if t["mean_soc_per_km"] is not None:
            out.append("  mean SOC use: %s %%/km" % t["mean_soc_per_km"])
        for key, label in (("max_pack_temp_c", "max pack temp"),
                           ("max_motor_temp_c", "max motor temp")):
            if t[key] is not None:
                out.append("  %s: %g C" % (label, t[key]))
    else:
        out += ["", "No ride telemetry in this session "
                    "(pull the event log from the bike to add it)."]
    out.append("")
    return "\n".join(out)
