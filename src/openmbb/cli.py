"""Command-line entry point + headless diagnostics.

  openmbb                 # launch GUI on real serial (needs pyserial)
  openmbb --sim           # simulator (no hardware)
  openmbb --port COM4     # preselect a port
  openmbb --selftest      # headless transport/safety tests
  openmbb --smoketest     # build the GUI once and exit

  openmbb sessions        # list saved captures, no bike needed
  openmbb analyze <dir>   # the full report from a saved capture
  openmbb redact <dir>    # share-safe copy, verified before it is handed over
"""

import argparse
import os
import sys

from . import __release_date__, __version__


def selftest():
    import os
    import tempfile
    from .safety import BlockedCommandError, WRITE_WHITELIST, _v_coast_regen, command_blocked
    from .sim import SimPort
    from .transport import (READ_COMMANDS, SessionLogger, Transport,
                            first_number, parse_settings_dump)

    failures = []

    def check(label, cond):
        print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp(prefix="zeroconsole_test_")
    logger = SessionLogger(base_dir=tmp, tag="selftest")
    tr = Transport(SimPort(), logger)

    print("blocklist:")
    for bad in ["format eeprom", "erase eeprom", "eeprom dump 0 16", "settingsrst",
                "statsrst", "eventlogclear", "errorlogadd hi", "reset", "exit_to_bl",
                "test isolation -v", "wdt reset", "timing", "can", "charger",
                "sevcon preop", "bluetooth reset", "set abs_disable On",
                "set motstage2 200", "set sevmaxregv 4300", "set ov_kickstand Yes",
                "set vin 123", "set debug_level 3",
                "dtc_clear", "force_all_storage_mode kick", "blcmds", "burn"]:
        try:
            tr.exec_command(bad)
            check("blocked: %s" % bad, False)
        except BlockedCommandError:
            check("blocked: %s" % bad, True)

    print("reads:")
    for cmd in READ_COMMANDS:
        out = tr.exec_command(cmd)
        check("read %s (%d chars)" % (cmd, len(out)), len(out) > 20)
    check("sevcon faults allowed (read)", command_blocked("sevcon faults") is None)

    print("settings parse:")
    l0, _ = parse_settings_dump(tr.exec_command("set"))     # login level 0
    check("level-0 hides tunables (identity only)", "spfront" not in l0 and len(l0) < 10)
    tr.exec_command("login tpsreport")                       # reveal the tunables
    dump = tr.exec_command("set")
    settings, order = parse_settings_dump(dump)
    check("parsed >= 30 settings (got %d)" % len(settings), len(settings) >= 30)
    check("spfront present = 20", settings.get("spfront", {}).get("value") == "20")
    n_rows = len([n for n in order if n in WRITE_WHITELIST])
    # C3: this measures the SIM's dump (which invents all whitelist names), NOT a
    # real bike — the real 2017 FXS rev-41 dump exposes only 7 of the whitelist
    # names (see safety.REV41_FXS_SETTINGS). Labelled so the number isn't misread.
    check("whitelist-x-sim = %d rows" % n_rows, n_rows == len(WRITE_WHITELIST))
    tr.exec_command("logout")     # back to read-only for the write-flow checks below

    print("dump with progress:")
    # The contactor gate lives in the transport so that it holds for every
    # caller - the GUI, a script, a REPL, this selftest. Prove it FIRES before
    # proving the read works: the gate is the safety property and the read is
    # only a capability. These two checks are here because their absence is
    # exactly what let this selftest sit broken - when the gate landed, nothing
    # exercised it, so nothing noticed that the selftest itself was the caller
    # that had not been updated.
    try:
        tr.exec_command("dumpall")
        check("heavy read refused without consent", False)
    except BlockedCommandError:
        check("heavy read refused without consent", True)
    # `confirmed` is the blocklist's flag, not this one. Conflating the two is
    # how the raw-console box would have reached the wire (transport docstring).
    try:
        tr.exec_command("dumpall", confirmed=True)
        check("confirmed=True does not satisfy the contactor gate", False)
    except BlockedCommandError:
        check("confirmed=True does not satisfy the contactor gate", True)
    seen = []
    # Consent is given here rather than waived, and the sentence is true: the
    # port is a SimPort, so there is no bike, no BMS and no contactor to open.
    # This string is what gets journalled into the session before the first byte.
    big = tr.exec_command("dumpall", idle_timeout=3.0, max_time=60.0,
                          heavy_consent="selftest: simulator only, no bike attached",
                          progress_cb=lambda n: seen.append(n))
    check("dumpall > 100 KB (got %d KB)" % (len(big) // 1024), len(big) > 100_000)
    check("progress callback fired (%d times)" % len(seen), len(seen) > 3)
    check("dumplogs is invalid on rev 41",
          "invalid command" in tr.exec_command("dumplogs").lower())

    print("write flow:")
    # a raw `set <name> <value>` must be refused from the ordinary path — writes
    # only go through the gated write_setting()
    try:
        tr.exec_command("set spfront 22")
        check("raw set-write refused", False)
    except BlockedCommandError:
        check("raw set-write refused", True)
    # embedded-newline injection must be refused (can't smuggle a blocked cmd)
    try:
        tr.exec_command("version\nsettingsrst")
        check("newline injection refused", False)
    except BlockedCommandError:
        check("newline injection refused", True)
    # D2: while logged out the transport refuses the write outright (spfront isn't
    # in the level-0 dump) — before it could reach the wire
    try:
        tr.write_setting("spfront", "22")
        check("write refused while logged out", False)
    except BlockedCommandError:
        check("write refused while logged out", True)
    out = tr.exec_command("login tpsreport")
    check("login ok", "logged in" in out.lower())
    # logged in (name now in the dump), the gated write path still validates the
    # value. This used to write 0 coast regen, which was refused on an unsourced
    # fishtail claim (item 19). It now tests the REAL bound: the validator still
    # guards the range, it just stopped inventing a hazard inside it.
    try:
        tr.write_setting("maxcustregcotq_allow", "101")
        check("write_setting validates value", False)
    except BlockedCommandError:
        check("write_setting validates value", True)
    tr.write_setting("spfront", "22")
    s2, _ = parse_settings_dump(tr.exec_command("set"))
    check("write verified (spfront=22)", first_number(s2["spfront"]["value"]) == "22")
    tr.write_setting("spfront", "20")
    s3, _ = parse_settings_dump(tr.exec_command("set"))
    check("revert verified (spfront=20)", first_number(s3["spfront"]["value"]) == "20")

    print("validators:")
    # 0 is a supported value (item 19): the refusal was unsourced folklore,
    # and Zero's own app writes 0. The write flow warns instead.
    check("coast regen 0 accepted with a warning",
          _v_coast_regen("0")[0]
          and WRITE_WHITELIST["maxcustregcotq_allow"][4]("0") is not None)
    check("coast regen 6 accepted, unwarned",
          _v_coast_regen("6")[0]
          and WRITE_WHITELIST["maxcustregcotq_allow"][4]("6") is None)
    check("coast regen 101 refused", not _v_coast_regen("101")[0])
    check("maxcustsp 103 refused", not WRITE_WHITELIST["maxcustspmph"][3]("103")[0])
    check("noregenstopped No warns", WRITE_WHITELIST["noregenstopped"][4]("No") is not None)

    print("session files:")
    check("raw log exists", os.path.isfile(logger.raw_path))
    # The capture has to be able to answer "did a human agree to the heavy read?"
    # long after the dialog is gone - that is the whole point of the gate.
    _j = ""
    if os.path.isfile(logger.journal_path):
        with open(logger.journal_path, encoding="utf-8") as _f:
            _j = _f.read()
    check("heavy-read consent journalled", "HEAVY READ CONSENTED" in _j)
    check("per-command files saved (%d)" % len(os.listdir(logger.dir)),
          len(os.listdir(logger.dir)) > 10)

    print("frozen deps:")
    # G3: prove pyserial actually ships in the (frozen) bundle — a missing
    # pyserial otherwise shows only as an empty COM-port list at the bike
    try:
        import serial  # noqa: F401
        from serial.tools import list_ports
        list_ports.comports()
        check("pyserial import + comports() works", True)
    except Exception as e:
        check("pyserial import + comports() (%s)" % e, False)

    print()
    if failures:
        print("SELFTEST FAILED (%d): %s" % (len(failures), failures))
        return 1
    print("SELFTEST PASSED - all checks green.")
    return 0


def smoketest():
    import tempfile
    import time
    from .gui import build_gui
    # G6: never drop a synthetic sim session into the user's real session root
    app = build_gui(sim=True, log_dir=tempfile.mkdtemp(prefix="openmbb_smoketest_"))
    app.update_idletasks()
    app.update()
    app._connect()
    for _ in range(200):
        app.update()
        time.sleep(0.01)
        if app.connected:
            break
    ok = app.connected
    backend = app.sty.get("backend")
    app.destroy()
    print("SMOKETEST %s - GUI built (theme: %s)%s." % (
        "PASSED" if ok else "FAILED", backend,
        ", sim connected & probed" if ok else " but sim connect failed"))
    return 0 if ok else 1


def _ensure_console():
    """G4: the frozen windowed exe (console=False) has sys.stdout=None, so
    --selftest / --help would print nothing. Attach the parent console on
    Windows so the diagnostic output is visible when run from a terminal."""
    if sys.stdout is not None:
        return
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.AttachConsole(-1)     # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
        except Exception:
            pass


# `redact` prints its result - and, more to the point, its refusals - to
# stdout. In the frozen windowed build sys.stdout is None until
# _ensure_console() runs, so without this every line it emits, including
# "export discarded", is silently dropped and the command looks like it
# did nothing.
HEADLESS_COMMANDS = ("analyze", "sessions", "redact")


def cmd_analyze(args):
    """Analyze a saved session folder. No hardware, no GUI, no serial port."""
    import os

    from . import report as report_mod
    from . import sessions as sessions_mod

    # load_session globs a directory: a wrong path yields an empty Session rather
    # than raising, so a bad argument would otherwise print an empty report and
    # exit 0. Check the folder, then check it actually held a capture.
    if not os.path.isdir(args.folder):
        print("No such session folder: %s" % args.folder, file=sys.stderr)
        return 2
    # A folder stating a format we cannot read is a could-not-run case, and exit
    # 1 belongs to --fail-on-alert ("this bike has an alert"). Exiting 1 here
    # would tell a script something about the motorcycle that we never learned.
    try:
        rep = report_mod.analyze_folder(args.folder, args.units)
    except sessions_mod.CaptureFormatError as e:
        print("Cannot analyze %s: %s" % (args.folder, e), file=sys.stderr)
        return 2
    if not rep["session"]["commands"] and not rep["session"]["has_settings"]:
        print("Nothing to analyze in %s — no recognizable command captures found.\n"
              "Expected a session folder written by OpenMBB (NNN_<cmd>.txt files)."
              % args.folder, file=sys.stderr)
        return 2

    if args.json:
        import json
        print(json.dumps(rep, indent=2, sort_keys=False))
    else:
        print(report_mod.format_report(rep, dist_units=args.distance))
    # A non-zero exit on `alert` lets this drive a script or a health check.
    return 1 if args.fail_on_alert and rep["counts"]["alert"] else 0


def cmd_sessions(args):
    """List saved session folders under the log directory."""
    from .config import get_log_dir
    from .sessions import list_sessions

    base = args.logdir or get_log_dir()
    found = list_sessions(base)
    if not found:
        print("No sessions under %s" % base)
        return 0
    if args.json:
        import json
        print(json.dumps({"base": base, "sessions": found}, indent=2))
    else:
        print("%d session(s) under %s" % (len(found), base))
        for path in found:
            print("  %s" % path)
    return 0


def cmd_redact(args):
    """Write a copy of a session folder with the identifiers stripped.

    The export verifies its own output: if any identifier survives, it deletes
    what it wrote and fails, rather than handing over a bundle that only looks
    redacted.
    """
    import json as _json
    from . import redact

    src = os.path.abspath(args.folder)
    if not os.path.isdir(src):
        print("No such session folder: %s" % args.folder, file=sys.stderr)
        return 2
    dst = os.path.abspath(args.out) if args.out else src.rstrip("\\/") + "-shared"
    try:
        rep = redact.redact_session(src, dst, overwrite=args.overwrite)
    except FileExistsError:
        print("%s already exists — pass --overwrite to replace it" % dst,
              file=sys.stderr)
        return 2
    except ValueError as e:
        # The three refusals that say "I will not do this with these arguments":
        # exporting into the capture itself, naming the export after an
        # identifier, and a source folder that is not a capture. Exit 2 is this
        # file's "I cannot do this"; exit 1 below means the DATA was bad, which
        # is a different thing to tell a script. Without this branch the refusal
        # left as a raw traceback.
        print("%s" % e, file=sys.stderr)
        return 2
    except RuntimeError as e:                      # verification failed
        print("%s" % e, file=sys.stderr)
        return 1

    if args.json:
        print(_json.dumps(rep, indent=2))
        return 0
    print("Share-safe copy written to %s" % rep["output"])
    print("  %d files, %d identifier(s) replaced%s"
          % (rep["files"], rep["identifiers_replaced"],
             (" (" + ", ".join("%s x%d" % (k, v)
                               for k, v in rep["by_kind"].items()) + ")")
             if rep["by_kind"] else ""))
    if rep["skipped"]:
        print("  skipped (unreadable): %s" % ", ".join(rep["skipped"]))
    print("  every output file was re-scanned: no VIN, no serial numbers. It")
    print("  looks for those identifier shapes and NOTHING else - your own")
    print("  notes, your machine's clock and the whole event log go exactly")
    print("  as written. Read it yourself before sending it anywhere.")
    return 0


def main():
    # attach a console for anything that prints before argparse needs it
    if any(a in ("--selftest", "--smoketest", "--help", "-h") or a in HEADLESS_COMMANDS
           for a in sys.argv[1:]):
        _ensure_console()
    ap = argparse.ArgumentParser(
        description="OpenMBB — serial console for Gen2 MBB-based Zero motorcycles",
        epilog="OpenMBB makes no network requests of any kind. Releases and "
               "changelog: https://github.com/rodu4835/openmbb/releases")
    # one parseable line, ISO date, because scripts read it
    ap.add_argument("--version", action="version",
                    version="openmbb %s (released %s)"
                            % (__version__, __release_date__))
    ap.add_argument("--sim", action="store_true", help="simulator mode (no hardware)")
    ap.add_argument("--port", help="preselect a COM port")
    ap.add_argument("--logdir", help="base dir for session logs (overrides saved config)")
    ap.add_argument("--selftest", action="store_true", help="headless tests, no GUI")
    ap.add_argument("--smoketest", action="store_true", help="build GUI once and exit")

    # Subcommands are optional so the bare `openmbb` (and every existing flag)
    # still launches the GUI exactly as before.
    sub = ap.add_subparsers(dest="command")

    p_an = sub.add_parser("analyze", help="analyze a saved session folder (no hardware)")
    p_an.add_argument("folder", help="path to a session folder")
    p_an.add_argument("--json", action="store_true", help="emit the structured report")
    p_an.add_argument("--units", choices=("C", "F"), default="C",
                      help="temperature units for display (default C)")
    p_an.add_argument("--distance", choices=("km", "mi"), default="km",
                      help="distance units for display (default km, the unit "
                           "the bike itself reports)")
    p_an.add_argument("--fail-on-alert", action="store_true",
                      help="exit 1 if any metric is at alert, for scripts")
    p_an.set_defaults(func=cmd_analyze)

    p_rd = sub.add_parser("redact",
                          help="write a share-safe copy of a session folder")
    p_rd.add_argument("folder", help="path to a session folder")
    p_rd.add_argument("--out", help="destination (default: <folder>-shared)")
    p_rd.add_argument("--overwrite", action="store_true",
                      help="replace the destination if it exists")
    p_rd.add_argument("--json", action="store_true", help="emit the report as JSON")
    p_rd.set_defaults(func=cmd_redact)

    p_se = sub.add_parser("sessions", help="list saved session folders")
    p_se.add_argument("--logdir", help="base dir to list (defaults to the saved config)")
    p_se.add_argument("--json", action="store_true", help="emit JSON")
    p_se.set_defaults(func=cmd_sessions)

    args = ap.parse_args()

    if getattr(args, "func", None):
        sys.exit(args.func(args))
    if args.selftest:
        sys.exit(selftest())
    if args.smoketest:
        sys.exit(smoketest())
    if not args.sim:
        try:
            import serial  # noqa: F401
        except ImportError:
            print("pyserial is not installed. Install the package (pip install .) or\n"
                  "run the simulator:  openmbb --sim")
            sys.exit(1)
    from .config import get_log_dir
    from .gui import build_gui
    app = build_gui(sim=args.sim, preselect_port=args.port,
                    log_dir=args.logdir or get_log_dir())
    app.mainloop()


if __name__ == "__main__":
    main()
