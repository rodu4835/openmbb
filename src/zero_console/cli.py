"""Command-line entry point + headless diagnostics.

  zero-console                 # launch GUI on real serial (needs pyserial)
  zero-console --sim           # simulator (no hardware)
  zero-console --port COM4     # preselect a port
  zero-console --selftest      # headless transport/safety tests
  zero-console --smoketest     # build the GUI once and exit
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
                "set vin 123", "set debug_level 3"]:
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
    dump = tr.exec_command("set")
    settings, order = parse_settings_dump(dump)
    check("parsed >= 30 settings (got %d)" % len(settings), len(settings) >= 30)
    check("spfront present = 20", settings.get("spfront", {}).get("value") == "20")
    n_rows = len([n for n in order if n in WRITE_WHITELIST])
    check("whitelist-x-live = %d rows" % n_rows, n_rows == len(WRITE_WHITELIST))

    print("dump with progress:")
    seen = []
    big = tr.exec_command("dumplogs", idle_timeout=3.0, max_time=60.0,
                          progress_cb=lambda n: seen.append(n))
    check("dumplogs > 100 KB (got %d KB)" % (len(big) // 1024), len(big) > 100_000)
    check("progress callback fired (%d times)" % len(seen), len(seen) > 3)

    print("write flow:")
    denied = tr.exec_command("set spfront 22")
    check("write denied while logged out", "denied" in denied.lower())
    out = tr.exec_command("login tpsreport")
    check("login ok", "logged in" in out.lower())
    tr.exec_command("set spfront 22")
    s2, _ = parse_settings_dump(tr.exec_command("set"))
    check("write verified (spfront=22)", first_number(s2["spfront"]["value"]) == "22")
    tr.exec_command("set spfront 20")
    s3, _ = parse_settings_dump(tr.exec_command("set"))
    check("revert verified (spfront=20)", first_number(s3["spfront"]["value"]) == "20")

    print("validators:")
    check("coast regen 0 refused", not _v_coast_regen("0")[0])
    check("coast regen 6 accepted", _v_coast_regen("6")[0])
    check("maxcustsp 103 refused", not WRITE_WHITELIST["maxcustsp"][3]("103")[0])
    check("noregenstopped No warns", WRITE_WHITELIST["noregenstopped"][4]("No") is not None)

    print("session files:")
    check("raw log exists", os.path.isfile(logger.raw_path))
    check("per-command files saved (%d)" % len(os.listdir(logger.dir)),
          len(os.listdir(logger.dir)) > 10)

    print()
    if failures:
        print("SELFTEST FAILED (%d): %s" % (len(failures), failures))
        return 1
    print("SELFTEST PASSED - all checks green.")
    return 0


def smoketest():
    import time
    from .gui import build_gui
    app = build_gui(sim=True)
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


def main():
    ap = argparse.ArgumentParser(description="Zero Console — 2017 Zero FXS MBB tool")
    ap.add_argument("--sim", action="store_true", help="simulator mode (no hardware)")
    ap.add_argument("--port", help="preselect a COM port")
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
                  "run the simulator:  zero-console --sim")
            sys.exit(1)
    from .gui import build_gui
    app = build_gui(sim=args.sim, preselect_port=args.port)
    app.mainloop()


if __name__ == "__main__":
    main()
