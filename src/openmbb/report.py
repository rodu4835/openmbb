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
        "clocks": condition.clock_check(session),
        "bike_state": _bike_state(session),
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

    out += _bike_state_lines(report.get("bike_state") or {})
    out += _clock_lines(report.get("clocks") or {})
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


def _bike_state(session):
    """Switch positions, outputs and run time - captured on every pull since the
    beginning and never once read. When a Gen2 bike will not move, the answer is
    usually a switch in here rather than anything in the pack.

    The odometer travels with it so the run-time cross-check can be made without
    the session.
    """
    return {
        "inputs": parsers.parse_inputs(session.cmd("inputs")),
        "outputs": parsers.parse_outputs(session.cmd("outputs")),
        "runtime": parsers.parse_runtime(session.cmd("runtime")),
        "odo_km": parsers.parse_stats(session.cmd("stats")).get("odo_km"),
    }


def _bike_state_lines(s):
    """The interlocks, and a run-time cross-check with teeth."""
    if not s or not (s.get("inputs") or s.get("runtime")):
        return []
    out = ["", "== Bike state =="]
    i = s.get("inputs") or {}
    if i:
        holds = []
        if str(i.get("kickstand", "")).lower().startswith("down"):
            holds.append("kickstand DOWN")
        if str(i.get("kill_switch", "")).lower().startswith(("off", "stop")):
            holds.append("kill switch OFF")
        if str(i.get("throttle_enabled", "")).lower().startswith("dis"):
            holds.append("throttle NOT enabled")
        out.append("  key %s \u00b7 kill switch %s \u00b7 kickstand %s \u00b7 brake %s"
                   % (i.get("key_on", "?"), i.get("kill_switch", "?"),
                      i.get("kickstand", "?"), i.get("brake_switch", "?")))
        out.append("  " + ("would not move as captured: " + ", ".join(holds)
                           if holds else "no interlock was holding it"))
    o = s.get("outputs") or {}
    if str(o.get("warning_light", "")).lower() == "on":
        out.append("  ! the dash warning light was ON at capture")
    r = s.get("runtime") or {}
    if r.get("run_s"):
        hours = r["run_s"] / 3600.0
        line = "  run time %.1f h" % hours
        if r.get("charge_s"):
            line += " \u00b7 charger time %.1f h" % (r["charge_s"] / 3600.0)
        out.append(line)
        km = s.get("odo_km")
        if km and hours > 0:
            avg = km / hours
            out.append("  odometer / run time = %.0f km/h average%s"
                       % (avg, " \u2014 impossible, so the two counters do not cover "
                               "the same period, which means the statistics were "
                               "reset" if avg > 120 else ""))
    return out


def _clock_lines(c):
    """What the bike's several clocks read, and the correction for its event log.

    A Gen2 bike carries at least three clocks, set independently: the MBB's
    timestamps the event log, the BMS prints an epoch beside its own, and the
    dash keeps a third. They disagree — by seconds on one bike, by hours on
    another — so a ride-log timestamp means nothing until you know which clock
    wrote it and how far out that clock was.
    """
    if not c or not c.get("mbb_clock"):
        return []
    out = ["", "== Clocks =="]
    out.append("  MBB (writes the event log): %s" % c["mbb_clock"])
    if c.get("bms_clock"):
        out.append("  BMS:                        %s%s"
                   % (c["bms_clock"],
                      "" if not c.get("bms_epoch") else " (epoch %d)" % c["bms_epoch"]))
    if c.get("dash_clock"):
        out.append("  Dash:                       %s" % c["dash_clock"])
    if c.get("captured_at"):
        out.append("  This capture was taken at:  %s (the capturing machine's clock)"
                   % c["captured_at"])
        out.append("  -> the bike's MBB clock is %s"
                   % condition.describe_offset(c["offset_s"]))
    if c.get("console_renders_epoch_at_h") is not None:
        out.append("  The console renders its stored counter at %+g h, so its printed "
                   "clock and" % c["console_renders_epoch_at_h"])
        out.append("  the epoch beside it are not the same number.")
    if c.get("worth_correcting"):
        out.append("  ! Event-log timestamps are offset by more than ten minutes. Add "
                   "the figure above")
        out.append("    to any time in the Rides or Condition blocks to read it in "
                   "this capture's local time.")
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
    for f in c.get("faults") or []:
        out.append("  %-27s %d logged, %s"
                   % (f["name"] + ":", f["count"], condition.fault_span(f)))
    for ev in c.get("stats_resets") or []:
        out.append("  ! statistics were RESET at %s - every 'lifetime' figure "
                   "above dates from then, not from" % (ev["when"] or "an unlogged time"))
        out.append("    the bike's build date")
    out += _charging_lines(c.get("charging") or {})
    for u in c.get("undetermined") or []:
        out.append("  could not determine: %s" % u)
    return out


def _charging_lines(ch):
    """How the bike is charged. Habits, not faults - nothing here is graded,
    because the thresholds that would turn "plugged in at 46 C" into a verdict
    need a population nobody has. It earns its place anyway: time spent sitting
    at full is the largest calendar-ageing term there is, and it is the only
    thing in this whole report an owner can change this afternoon."""
    if not ch:
        return []
    out = ["", "  -- charging (habits, not graded) --"]
    if ch.get("sessions"):
        line = "  %d charge sessions" % ch["sessions"]
        if ch.get("span_days"):
            line += " over %g days" % ch["span_days"]
        if ch.get("per_week"):
            line += " (%g a week)" % ch["per_week"]
        out.append(line)
    if ch.get("start_soc_median") is not None:
        out.append("  plugged in at a median %g%% SOC (lowest %g%%)"
                   % (ch["start_soc_median"], ch["start_soc_min"]))
    if ch.get("held_full_h"):
        line = "  sat at FULL with the charger still attached: %g h" % ch["held_full_h"]
        if ch.get("held_full_share") is not None:
            line += " - %.0f%% of the logged period" % (ch["held_full_share"] * 100)
        out.append(line)
        out.append("    %d spells, median %g h, longest %g h"
                   % (ch["holds"], ch["held_full_median_h"], ch["held_full_max_h"]))
        out.append("    time at full is the main driver of calendar ageing; "
                   "unplugging when it finishes costs nothing")
    if ch.get("hot_plugins"):
        out.append("  plugged in with the pack still hot (>= %g C): %d of %d sessions"
                   % (ch["hot_plugin_c"], ch["hot_plugins"], ch["sessions"]))
    if ch.get("peak_amps_median") is not None:
        out.append("  charge current: median peak %g A, highest %g A"
                   % (ch["peak_amps_median"], ch["peak_amps_max"]))
    if not ch.get("taper_resolvable"):
        out.append("    the taper is NOT visible here: charging is sampled every "
                   "~10 min at whole-amp resolution,")
        out.append("    which is coarser than the constant-voltage knee it would "
                   "take to see one")
    return out
