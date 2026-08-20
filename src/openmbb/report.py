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

from . import condition, health, parsers, rides, sessions, transport

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
            # Re-derive that from the stored entry counts instead of trusting the
            # capture-time banner: a session written by an older OpenMBB carries a
            # flat TRUNCATED note even when all but a couple of entries arrived,
            # and telling the owner to re-pull a heavy read on that is wrong.
            promised, got = transport.eventlog_completeness(ride_text)
            complete = transport.eventlog_complete_enough(promised, got)
            truncated = (not complete if complete is not None
                         else "### TRUNCATED" in ride_text)
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
        # what the ride/charge samples say about the PACK, as distinct from the
        # health block's single-reading metrics. Empty-ish rather than absent
        # when there is no event log: its `undetermined` list is the answer.
        "condition": condition.assess(ride_text),
        "verdict": condition.verdict(condition.assess(ride_text), metrics),
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
                # the ride block follows the requested units too — F health rows
                # above a hard-coded C here read as one capture in two scales
                out.append("  %s: %s"
                           % (label, health.fmt_temp(t[key], report["units"])))
    else:
        out += ["", "No ride telemetry in this session "
                    "(pull the event log from the bike to add it)."]

    out += _condition_lines(report.get("condition") or {})
    out += _verdict_lines(report.get("verdict") or {})
    out.append("")
    return "\n".join(out)


def _verdict_lines(v):
    """The buyer's line. Deliberately last: it is a summary of what is above it,
    and a reader who stops here should still have been told what went
    unanswered, which is why the count is in the headline and not in a field."""
    if not v:
        return []
    out = ["", "== Verdict ==", "  %s" % v["headline"]]
    for c in v["checks"]:
        out.append("    [%-7s] %-24s %s" % (c["level"], c["name"], c["detail"]))
    for c in v["caveats"]:
        out.append("    note: %s" % c)
    return out


def _condition_lines(c):
    """The pack-condition block. Nothing here is graded: two of the three
    measurements have no reference bike to be judged against, and the third is a
    comparable index rather than the pack's capacity. What could not be measured
    is printed, because a silently missing check reads as a pass."""
    if not c:
        return []
    out = ["", "== Condition (pack) =="]
    cov = c.get("coverage") or {}
    if cov.get("first"):
        out.append("  log covers %s -> %s  (%d ride, %d charge samples)"
                   % (cov["first"], cov["last"], cov["ride_samples"],
                      cov["charge_samples"]))
    cap = c.get("charge_capacity")
    if cap:
        out.append("  charge accepted %g-%g V: median %g Ah over %d sessions"
                   % (cap["window_v"][0], cap["window_v"][1], cap["median_ah"],
                      cap["sessions"]))
        out.append("    a comparable index, not the pack's capacity - it reads "
                   "only pack voltage and current, so a firmware change that")
        out.append("    relabels the SOC display cannot move it")
    floor = c.get("cell_floor")
    if floor and (not c.get("cell_sag")
                  or floor["source"] != "riding samples"):
        out.append("  lowest cell under load: %g mV  (from %d %s)"
                   % (floor["min_cell_mv"], floor["samples"], floor["source"]))
    sag = c.get("cell_sag")
    if sag:
        out.append("  weakest cell under load: %g mV at %g A, %g%% SOC, %g C"
                   % (sag["min_cell_mv"], sag["at_amps"], sag["at_soc_pct"],
                      sag["at_pack_temp_c"]))
    der = c.get("derate")
    if der:
        out.append("  discharge allowance: median %g%%, worst %g%% at %g C / %g%% SOC"
                   % (der["median_pct"], der["worst_pct"],
                      der["worst_at_pack_temp_c"], der["worst_at_soc_pct"]))
    for ev in c.get("stats_resets") or []:
        out.append("  ! statistics were RESET at %s - every 'lifetime' figure "
                   "above dates from then, not from" % (ev["when"] or "an unlogged time"))
        out.append("    the bike's build date")
    for u in c.get("undetermined") or []:
        out.append("  could not determine: %s" % u)
    return out
