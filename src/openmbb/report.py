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

import datetime
import textwrap

from . import condition, health, parsers, rides, sessions, transport

# Where ride telemetry lives in a capture, best first. `eventlogdump` is the
# full console event log; `dumplogs` is the older command kept for sessions
# captured before the switch.
RIDE_LOG_COMMANDS = ("eventlogdump", "dumplogs")


def analyze_session(session, temp_units="C"):
    """Everything derivable from a saved session, as one JSON-ready dict."""
    ride_text, ride_source = "", None
    for cmd in RIDE_LOG_COMMANDS:
        text = session.cmd(cmd) or ""
        # a console refusal ("'dumplogs' is an invalid command") is saved like
        # any other reply and is not empty, so `strip()` alone accepts it
        if text.strip() and not parsers.is_console_refusal(text):
            ride_text, ride_source = text, cmd
            break

    # the log's own hottest sample, so the graded battery row cannot report
    # cooler than the capture it came from
    _peak = condition.pack_peak(parsers.parse_ride_log(ride_text)) if ride_text else None
    metrics = health.health_snapshot(
        session, temp_units,
        log_peak_c=_peak["pack_temp_c"] if _peak else None)

    ride_summary = None
    consumption = range_est = None
    truncated = False
    if ride_text:
        records = parsers.parse_ride_log(ride_text)
        if records:
            ride_summary = rides.summarize_rides(records)
            # what it costs to ride this bike, and how far a charge goes -
            # measured from the samples rather than taken from the BMS's
            # nominal capacity, which on this platform is not what the gauge
            # behaves like
            consumption = rides.consumption(records)
            range_est = rides.range_estimate(records)
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

    # one assessment, not two: this parses the whole ~1 MB event log
    assessment = condition.assess(
        ride_text,
        # the bike's LIFETIME temperature counter, which is a different channel
        # from the log and is never corrected by it - only placed beside it
        max_batt_temp_c=parsers.parse_stats(session.cmd("stats")).get(
            "max_batt_temp_c"),
        temp_units=temp_units)
    return {
        "session": {
            "name": session.name,
            "path": session.dir,
            "commands": sorted(session.commands),
            # A settings file being PRESENT is not the same as it holding
            # settings. `openmbb analyze` reads this to decide whether the
            # folder held a capture at all, and a settings_baseline_*.txt of
            # garbage - a truncated pull, a half-written file - satisfied it on
            # the strength of its FILENAME, so analyze printed a report over
            # nothing and exited 0. For a script driving --fail-on-alert, exit 0
            # means "all good". A check that could not run must never read as a
            # pass, so this now says what its name claims: settings we parsed.
            "has_settings": bool(
                transport.parse_settings_dump(session.settings_text or "")[0]),
        },
        "units": temp_units,
        "counts": _count_by_status(metrics),
        "health": metrics,
        "rides": ride_summary,
        "consumption": consumption,
        "range": range_est,
        "ride_source": ride_source,
        "ride_log_truncated": truncated,
        # what the ride/charge samples say about the PACK, as distinct from the
        # health block's single-reading metrics. Empty-ish rather than absent
        # when there is no event log: its `undetermined` list is the answer.
        "clocks": condition.clock_check(session),
        "bike_state": _bike_state(session),
        "condition": assessment,
        "verdict": condition.verdict(assessment, metrics),
    }


# --- the page you hand to someone else ---------------------------------------

REPORT_FOOTER = """\
What this is: everything one console capture can establish about this bike,
measured rather than estimated, with the questions it could not answer named
alongside the ones it could.

What it is not: a substitute for a test ride, a look at the tyres and chain, or
a Zero dealer's diagnosis. The pack figures come from the bike's own event log,
so they describe the period the log covers - which on a Gen2 is a rolling buffer
a few weeks deep, not the bike's life. A capture with no hard riding in it
cannot answer the cell questions at all, and says so rather than passing them.

This report deliberately carries no VIN and no serial numbers."""


def condition_report(session, version=None, generated=None, temp_units="C"):
    """A dated page fit to hand a buyer, a seller, or your future self.

    Built on the same analysis the Analyze tab shows, so there is one set of
    numbers rather than two. The verdict's headline is repeated at the top
    because that is what a reader looks for first - headline and not level,
    because the headline is the form that carries the unanswered count with it,
    and "OK" on its own would read as a clean bill of health for a capture that
    could not measure anything.

    Raises RuntimeError if the finished page carries anything shaped like an
    identifier. This page exists to be handed to a stranger, so it gets the same
    treatment the share-safe export does: a report that cannot be vouched for is
    not returned at all.
    """
    from . import redact

    rep = analyze_session(session, temp_units)
    v = rep.get("verdict") or {}
    stamp = generated or datetime.datetime.now()
    head = [
        "OpenMBB condition report",
        "  bike     : %s" % (_bike_line(session) or "(not identified)"),
        "  capture  : %s" % session.name,
        "  generated: %s%s" % (stamp.strftime("%Y-%m-%d %H:%M"),
                               " by OpenMBB v%s" % version if version else ""),
        "",
    ]
    if v.get("headline"):
        head += ["  %s" % v["headline"], ""]
    text = ("\n".join(head) + format_report(rep, with_path=False)
            + "\n" + REPORT_FOOTER + "\n")

    leaks = redact.find_pii_shapes(text)
    if leaks:
        raise RuntimeError(
            "condition report withheld: it still carries %s"
            % ", ".join(sorted({label for label, _tok in leaks})))
    return text


def _bike_line(session):
    """Model and year, and nothing that identifies the individual bike."""
    st, _order = transport.parse_settings_dump(session.settings_text or "")
    bits = [st.get(k, {}).get("value") for k in ("model_year", "model")]
    return " ".join(str(b) for b in bits if b).strip()


def analyze_folder(folder, temp_units="C"):
    """Convenience: load a session folder from disk and analyze it."""
    return analyze_session(sessions.load_session(folder), temp_units)


def _count_by_status(metrics):
    counts = {"ok": 0, "watch": 0, "alert": 0, "info": 0}
    for m in metrics:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    return counts


MI_PER_KM = 0.621371


def fmt_km(km, units="km", places=1):
    """A kilometre figure in the requested unit, with its unit word attached.

    Distances stay in KILOMETRES everywhere in the report dict - that is what the
    bike reports and what every threshold is written against - exactly as
    temperatures stay in Celsius. Only the rendering converts.
    """
    if km is None:
        return "n/a"
    if units == "mi":
        return "%.*f mi" % (places, km * MI_PER_KM)
    return "%.*f km" % (places, km)


def fmt_per_km(per_km, units="km", places=1):
    """A per-kilometre RATE, which converts the other way: Wh/km -> Wh/mi is
    larger, not smaller, because a mile is further than a kilometre."""
    if per_km is None:
        return "n/a"
    if units == "mi":
        return "%.*f" % (places, per_km / MI_PER_KM)
    return "%.*f" % (places, per_km)


def format_report(report, with_path=True, dist_units="km"):
    """Human-readable rendering. Mirrors what the Analyze tab shows.

    `with_path=False` drops the session name and folder, for a caller that has
    already named the capture in its own header - and, more to the point, for one
    whose output leaves this machine. A saved-session path on Windows contains
    the account name, which is not something to print on a page handed to a
    stranger just because it happens not to be a VIN.
    """
    s, out = report["session"], []
    if with_path:
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
        out.append("  %d ride(s) over %s from %d samples"
                   % (t["ride_count"], fmt_km(t["total_km"], dist_units),
                      t["samples"]))
        if t["mean_soc_per_km"] is not None:
            out.append("  mean SOC use: %s %%/%s"
                       % (fmt_per_km(t["mean_soc_per_km"], dist_units, 2),
                          dist_units))
        for key, label in (("max_pack_temp_c", "max pack temp"),
                           ("max_motor_temp_c", "max motor temp")):
            if t[key] is not None:
                # the ride block follows the requested units too — F health rows
                # above a hard-coded C here read as one capture in two scales
                out.append("  %s: %s"
                           % (label, health.fmt_temp(t[key], report["units"])))
        out += _consumption_lines(report.get("consumption") or {},
                                  report.get("range") or {}, report["units"],
                                  dist_units)
    else:
        out += ["", "No ride telemetry in this session "
                    "(pull the event log from the bike to add it)."]

    out += _bike_state_lines(report.get("bike_state") or {})
    out += _clock_lines(report.get("clocks") or {})
    out += _condition_lines(report.get("condition") or {}, report["units"])
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
    # Directly under the headline, before the checks: this is the line a reader
    # who stops at the verdict has to see, and putting it after the caveats
    # would bury it exactly where the green OK already buried it.
    for note in v.get("beyond_pack") or []:
        out += _wrap(note, indent="  ! ", hang="    ")
    for c in v["checks"]:
        out.append("    [%-7s] %-24s %s" % (c["level"], c["name"], c["detail"]))
    for c in v["caveats"]:
        out.append("    note: %s" % c)
    return out


def _consumption_lines(c, r, units="C", dist_units="km"):
    """What it costs to ride, and how far a charge goes.

    Both are measured. Neither is graded: there is no reference bike to say
    whether 93 Wh/km is good for a 2017 FXS, and the range is an extrapolation
    whose weak step is named rather than smoothed over.
    """
    out = []
    if c:
        if c.get("wh_per_km_low") is None:
            # too few rides for a band. A "middle 80%" of one ride is that ride's
            # own number printed twice, which claims a precision that was not
            # measured but invented.
            out.append("  measured consumption: %s Wh/%s at the pack "
                       "(from %d ride(s) - too few for a spread)"
                       % (fmt_per_km(c["wh_per_km"], dist_units), dist_units,
                          c["rides"]))
        else:
            out.append("  measured consumption: %s Wh/%s at the pack "
                       "(middle 80%% of rides: %s-%s)"
                       % (fmt_per_km(c["wh_per_km"], dist_units), dist_units,
                          fmt_per_km(c["wh_per_km_low"], dist_units),
                          fmt_per_km(c["wh_per_km_high"], dist_units)))
        if c.get("amb_low_c") is not None:
            out.append("    over %d rides / %s, at %s to %s ambient - "
                       "consumption climbs in the cold"
                       % (c["rides"], fmt_km(c["km"], dist_units),
                          health.fmt_temp(c["amb_low_c"], units),
                          health.fmt_temp(c["amb_high_c"], units)))
    if r:
        out.append("  deepest discharge logged: %s from %g%% to %g%% SOC"
                   % (fmt_km(r["km"], dist_units), r["from_soc_pct"],
                      r["to_soc_pct"]))
        out.append("    scaled to a full charge that is about %s"
                   % fmt_km(r["full_charge_km"], dist_units))
        out += _wrap(condition.range_caveat(r), indent="    ", hang="    ")
        if r.get("implied_pack_wh"):
            out.append("    that ride implies a pack of about %d Wh, which is "
                       "worth holding against what the BMS reports"
                       % r["implied_pack_wh"])
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
        # "no interlock was holding it" is a claim about three specific switches,
        # so it may only be made when all three were actually read. The console
        # prints Key On and the supply rails BEFORE the interlocks, so a `inputs`
        # read that ended early leaves a populated dict with every interlock
        # missing - and the old test (`if holds`) could not fire, which rendered
        # a truncated read as a clean bill of mechanical health.
        unread = [lab for key, lab in (("kickstand", "kickstand"),
                                       ("kill_switch", "kill switch"),
                                       ("throttle_enabled", "throttle"))
                  if i.get(key) in (None, "")]
        if holds:
            out.append("  would not move as captured: " + ", ".join(holds))
        elif unread:
            out.append("  cannot say whether an interlock was holding it: this "
                       "capture never read %s" % ", ".join(unread))
        else:
            out.append("  no interlock was holding it")
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


def _condition_lines(c, units="C"):
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
    out += _coverage_limit_lines(cov)
    cap = c.get("charge_capacity")
    if cap:
        out.append("  charge accepted %g-%g V: median %g Ah over %d sessions"
                   % (cap["window_v"][0], cap["window_v"][1], cap["median_ah"],
                      cap["sessions"]))
        out += _wrap(condition.capacity_caveat(), indent="    ", hang="    ")
    floor = c.get("cell_floor")
    if condition.show_cell_floor(c):
        out.append("  lowest cell under load: %g mV  (from %d %s)"
                   % (floor["min_cell_mv"], floor["samples"], floor["source"]))
    sag = c.get("cell_sag")
    if sag:
        out.append("  weakest cell under load: %g mV at %g A, %g%% SOC, %s"
                   % (sag["min_cell_mv"], sag["at_amps"], sag["at_soc_pct"],
                      health.fmt_temp(sag["at_pack_temp_c"], units)))
    der = c.get("derate")
    if der:
        out.append("  discharge allowance: median %g%%, worst %g%% at %s / %g%% SOC"
                   % (der["median_pct"], der["worst_pct"],
                      health.fmt_temp(der["worst_at_pack_temp_c"], units),
                      der["worst_at_soc_pct"]))
    for f in c.get("faults") or []:
        out.append("  %-27s %d logged, %s"
                   % (f["name"] + ":", f["count"], condition.fault_span(f)))
        detail = condition.fault_detail(f)
        if detail:
            # a controller that resets itself is worth seeing, but a reset is not
            # a fault and must not date one
            out.append("    %s - the dates above are the onsets" % detail)
        if f["name"].lower().startswith("module connect"):
            assoc = condition.module_failure_note(c.get("module_failures"))
            if assoc:
                out.append("    %s" % assoc)
    for ev in c.get("stats_resets") or []:
        out.append("  ! statistics were RESET at %s - every 'lifetime' figure "
                   "above dates from then, not from" % (ev["when"] or "an unlogged time"))
        out.append("    the bike's build date")
    out += _charging_lines(c.get("charging") or {}, units)
    for u in c.get("undetermined") or []:
        out.append("  could not determine: %s" % u)
    return out


def _wrap(sentence, indent="  ", hang="    "):
    """One composed sentence, wrapped for the page.

    The sentence comes from condition.py so that the tab cannot word it
    differently; only the wrapping is this file's business.
    """
    # break_on_hyphens=False: the default splits "whole-amp resolution" across
    # lines as "whole-" / "amp", which reads as a typo in a sentence whose whole
    # job is to be believed.
    return textwrap.wrap(sentence, width=78, initial_indent=indent,
                         subsequent_indent=hang,
                         break_on_hyphens=False) if sentence else []


def _coverage_limit_lines(cov):
    """How much of the ride log could answer the cell question at all.

    The sentence itself now lives in `condition.coverage_note`, because the
    Condition tab carries the same one and "UNMEASURED here, not clean" is the
    most load-bearing phrase this project prints. A copy of it that could drift
    is a copy that could soften.
    """
    return _wrap(condition.coverage_note(cov))


def _charging_lines(ch, units="C"):
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
        out += _wrap(condition.full_hold_note(ch), indent="    ", hang="    ")
    if ch.get("hot_plugins"):
        out.append("  plugged in with the pack still hot (>= %s): %d of %d sessions"
                   % (health.fmt_temp(ch["hot_plugin_c"], units),
                      ch["hot_plugins"], ch["sessions"]))
    if ch.get("peak_amps_median") is not None:
        out.append("  charge current: median peak %g A, highest %g A"
                   % (ch["peak_amps_median"], ch["peak_amps_max"]))
    out += _wrap(condition.taper_note(ch), indent="    ", hang="    ")
    return out
