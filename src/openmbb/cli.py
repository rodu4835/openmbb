"""Command-line entry point + headless diagnostics.

  openmbb                 # launch GUI on real serial (needs pyserial)
  openmbb --sim           # simulator (no hardware)
  openmbb --port COM4     # preselect a port
  openmbb --selftest      # headless transport/safety tests
  openmbb --smoketest     # build the GUI once and exit
"""

import argparse
import sys


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
    seen = []
    big = tr.exec_command("dumpall", idle_timeout=3.0, max_time=60.0,
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
    # the gated write path still validates the value (0% coast regen refused)
    try:
        tr.write_setting("maxcustregcotq_allow", "0")
        check("write_setting validates value", False)
    except BlockedCommandError:
        check("write_setting validates value", True)
    denied = tr.write_setting("spfront", "22")
    check("write denied while logged out", "denied" in denied.lower())
    out = tr.exec_command("login tpsreport")
    check("login ok", "logged in" in out.lower())
    tr.write_setting("spfront", "22")
    s2, _ = parse_settings_dump(tr.exec_command("set"))
    check("write verified (spfront=22)", first_number(s2["spfront"]["value"]) == "22")
    tr.write_setting("spfront", "20")
    s3, _ = parse_settings_dump(tr.exec_command("set"))
    check("revert verified (spfront=20)", first_number(s3["spfront"]["value"]) == "20")

    print("validators:")
    check("coast regen 0 refused", not _v_coast_regen("0")[0])
    check("coast regen 6 accepted", _v_coast_regen("6")[0])
    check("maxcustsp 103 refused", not WRITE_WHITELIST["maxcustspmph"][3]("103")[0])
    check("noregenstopped No warns", WRITE_WHITELIST["noregenstopped"][4]("No") is not None)

    print("session files:")
    check("raw log exists", os.path.isfile(logger.raw_path))
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


def main():
    # attach a console for the headless flags before argparse needs to print
    if any(a in ("--selftest", "--smoketest", "--help", "-h") for a in sys.argv[1:]):
        _ensure_console()
    ap = argparse.ArgumentParser(
        description="OpenMBB — serial console for Gen2 MBB-based Zero motorcycles")
    ap.add_argument("--sim", action="store_true", help="simulator mode (no hardware)")
    ap.add_argument("--port", help="preselect a COM port")
    ap.add_argument("--logdir", help="base dir for session logs (overrides saved config)")
    ap.add_argument("--selftest", action="store_true", help="headless tests, no GUI")
    ap.add_argument("--smoketest", action="store_true", help="build GUI once and exit")
    args = ap.parse_args()

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
