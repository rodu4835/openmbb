"""End-to-end GUI flow in the simulator: connect -> baseline -> login -> write.

Skipped automatically where Tk has no display. Run with the real interpreter
on Windows; drives the app headlessly by pumping the event loop.
"""

import os
import time

import pytest

from openmbb.safety import WRITE_WHITELIST
from openmbb.transport import first_number


@pytest.fixture
def app(monkeypatch):
    tk = pytest.importorskip("tkinter")
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: errors.append(a))
    import tempfile
    from openmbb.gui import build_gui
    # hermetic: sessions go to a temp dir, never the user's configured location.
    # Build directly (no throwaway probe root) and skip cleanly with no display.
    try:
        application = build_gui(sim=True, log_dir=tempfile.mkdtemp(prefix="zcflow_"))
    except tk.TclError:
        pytest.skip("no display available for Tk")
    application._errors = errors
    yield application
    try:
        application.destroy()
    except Exception:
        pass


def _pump(app, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        time.sleep(0.01)
        if cond():
            return True
    return False


def test_full_flow(app):
    app._connect()
    assert _pump(app, lambda: app.connected)

    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    # level-0 baseline: identity only, no tunables and no whitelist rows yet
    assert len(app.settings) < 10
    assert len(app.tree.get_children()) == 0

    app._login()
    assert _pump(app, lambda: app.logged_in)
    # after login the full settings dump + whitelist rows appear
    assert _pump(app, lambda: len(app.settings) >= 30)
    assert len(app.tree.get_children()) == len(WRITE_WHITELIST)

    # write blocked without the unlock toggle
    app.tree.selection_set("spfront")
    app.newval_var.set("22")
    app._write()
    app.update()
    assert not app.journal_entries      # refused

    # unlock and write for real — the write is async (send -> verify -> ingest),
    # so wait for the read-back value to actually land, not just the journal entry
    app.unlock_var.set(True)
    app._write()
    assert _pump(app, lambda: first_number(
        app.settings.get("spfront", {}).get("value", "")) == "22")
    assert len(app.journal_entries) > 0
    backups = [f for f in os.listdir(app.logger.dir)
               if f.startswith("settings_backup")]
    assert backups
    assert not app._errors


def test_login_custom_password_is_redacted(app):
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)

    app.login_pw.set("tpsreport")            # the sim's accepted password
    app._login_custom()
    assert _pump(app, lambda: app.logged_in)

    # a typed password must never be persisted to the raw session log
    with open(app.logger.raw_path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    assert "tpsreport" not in raw
    assert "login ****" in raw
    assert not app._errors


def test_analyze_tab(app):
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)

    # load the just-captured session into the Analyze tab
    app._analyze_use_current()
    app.update()
    assert app.analyze_session is not None
    assert len(app.health_tree.get_children()) > 3      # health metrics rendered
    # rev-41 has no console ride telemetry -> Rides tab guides to 'Load ride log'
    assert "load ride log" in app.lbl_ride_totals.cget("text").lower()

    # gearing calculator
    app.gear_front.set("22")
    app.gear_rear.set("88")
    app._gearing_compute()
    gtext = app.txt_gearing.get("1.0", "end")
    assert "4.00" in gtext and "spfront = 22" in gtext

    # compare de-dupes the same session (adding current twice keeps one)
    app._compare_add_current()
    app._compare_add_current()
    app.update()
    assert len(app.compare_list) == 1

    # bad gearing input is rejected gracefully, not crashed
    app.gear_circ.set("0")
    app._gearing_compute()
    assert "positive" in app.txt_gearing.get("1.0", "end").lower()
    assert not app._errors


def test_menu_dialogs_open(app):
    app._connect()
    assert _pump(app, lambda: app.connected)
    for opener in (app._show_instructions, app._show_wiring, app._show_safety,
                   app._show_about, app._show_bike_info):
        opener()
        app.update()
    # bike info should surface the firmware rev parsed from the version banner
    facts = dict(app._bike_facts())
    assert facts.get("MBB firmware rev") == "41"
    assert not app._errors


def test_write_options_browser_needs_no_login(app, monkeypatch):
    # D2: the read-only "write options" reference opens WITHOUT connect / login /
    # unlock and lists the whole whitelist, so "see what I could change" is
    # decoupled from being unlocked to change it. It must touch no gate.
    captured = {}
    monkeypatch.setattr(app, "_info_window",
                        lambda title, text: captured.update(text=text))
    assert not app.connected                       # no session at all
    app._show_write_options()
    text = captured["text"]
    for name in WRITE_WHITELIST:                    # every option is listed
        assert name in text
    assert "read after login" in text              # verified names appear post-login
    assert "NEVER writable" in text                # the read-only guards section
    # C3: even pre-connect, the names the verified bike never exposes carry the
    # honesty note (not just a bare "read after login" that implies they will show)
    assert "not seen on the verified 2017 FXS rev 41" in text.lower() \
        or "NOT seen on the verified 2017 FXS rev 41" in text
    assert not app.unlock_var.get()                # nothing got unlocked


def test_write_options_browser_honest_after_real_dump(app, monkeypatch):
    # C3 (review FID-1): after ingesting the REAL rev-41 post-login dump, the 7
    # whitelist names the bike never exposes must NOT read "(read after login)"
    # (that falsely implies login reveals them) — they get the "not seen on rev 41"
    # note; the 7 real names show their live values.
    import os
    from openmbb.transport import parse_settings_dump
    from openmbb.safety import REV41_FXS_SETTINGS
    fx = os.path.join(os.path.dirname(__file__), "fixtures", "rev41_postlogin_set.txt")
    real, order = parse_settings_dump(open(fx, encoding="utf-8").read())
    app.settings = real                            # pretend the real post-login set was read
    app.settings_order = order
    captured = {}
    monkeypatch.setattr(app, "_info_window",
                        lambda title, text: captured.update(text=text))
    app._show_write_options()
    text = captured["text"]
    absent = [n for n in WRITE_WHITELIST if n not in real]
    assert absent and all(n not in REV41_FXS_SETTINGS for n in absent)  # sanity
    assert "(read after login)" not in text        # every name is either live or flagged
    assert "not in the live dump" in text          # the honest label for absent names
    assert "NOT seen on the verified 2017 FXS rev 41" in text


def test_write_options_no_read_after_login_once_logged_in(app, monkeypatch):
    # REG-1: a REV41-verified name that is absent from the dump AFTER login must not
    # still say "(read after login)" — login already happened and didn't reveal it.
    app.logged_in = True
    app.settings = {"sprear": {"value": "90"}}     # a post-login dump missing spfront etc.
    captured = {}
    monkeypatch.setattr(app, "_info_window",
                        lambda title, text: captured.update(text=text))
    app._show_write_options()
    text = captured["text"]
    assert "(read after login)" not in text        # never promised once logged in
    assert "spfront" in text and "(not in the live dump)" in text


# --- Phase C: connect/probe honesty -----------------------------------------

def test_wake_retry_connects(app, monkeypatch):
    # C2: the console needs a second CR-LF to wake — the retry loop must succeed
    from openmbb.sim import SimPort

    class TwoWakeSim(SimPort):
        def __init__(self):
            super().__init__(greet=False)
            self._wakes = 0

        def _handle(self, cmd):
            if cmd == "":
                self._wakes += 1
                if self._wakes < 2:
                    return                  # swallow the first wake — no prompt
            super()._handle(cmd)

    monkeypatch.setattr(app, "_make_port", lambda pn: TwoWakeSim())
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert not app._errors


def test_failed_probe_closes_port(app, monkeypatch):
    # C5: a probe that never sees a prompt must close the port, not leak it
    from openmbb.sim import SimPort
    holder = {}

    class NeverPromptSim(SimPort):
        def __init__(self):
            super().__init__(greet=False)
            self.closed = False

        def _handle(self, cmd):
            return                          # never responds -> no prompt ever

        def close(self):
            self.closed = True

    def mk(pn):
        holder["p"] = NeverPromptSim()
        return holder["p"]

    monkeypatch.setattr(app, "_make_port", mk)
    app._connect()
    assert _pump(app, lambda: holder.get("p") is not None and holder["p"].closed)
    assert not app.connected


def test_listen_only_reports_without_connecting(app):
    # C4: Stage-1 listen reports bytes + banner signature and never connects
    app._listen_only()
    assert _pump(app, lambda: "STAGE-1 LISTEN" in app.txt_probe.get("1.0", "end"))
    assert not app.connected
    assert "ZERO MBB>" in app.txt_probe.get("1.0", "end")   # sim greet detected


def test_simulator_offered_in_real_mode(monkeypatch):
    # A1/A5: SIMULATOR must be in the port dropdown even in REAL (non-sim) mode so a
    # downloaded-exe owner can explore without a bike; and the Connect button reads
    # "Connect & Probe" (not the literal "&&").
    import tempfile
    tk = pytest.importorskip("tkinter")
    from openmbb.gui import build_gui, SIM_CHOICE
    from openmbb.sim import SimPort
    try:
        app = build_gui(sim=False, log_dir=tempfile.mkdtemp(prefix="a1_"))
    except tk.TclError:
        pytest.skip("no display available for Tk")
    try:
        assert SIM_CHOICE in app.cbo_port.cget("values")     # offered in real mode
        assert isinstance(app._make_port(SIM_CHOICE), SimPort)   # maps to the sim
        assert app.btn_connect.cget("text") == "Connect & Probe"  # not "&&"
    finally:
        app.destroy()


def test_refresh_ports_reports_when_none_found(app, monkeypatch):
    # A2: a refresh that finds no COM ports must give feedback (not a silent blank
    # list); SIMULATOR stays offered so the list is never truly empty.
    from openmbb.gui import SIM_CHOICE
    monkeypatch.setattr("openmbb.gui.list_serial_ports", lambda: [])
    app._refresh_ports()
    app.update()
    log = app.txt_probe.get("1.0", "end")
    assert "No COM ports found" in log and "Device Manager" in log
    assert SIM_CHOICE in app.cbo_port.cget("values")


def test_no_port_selected_error_points_to_simulator(app, monkeypatch):
    # A3: the no-port error speaks GUI-language (Refresh + SIMULATOR), never "--sim".
    import tkinter.messagebox as mb
    errs = []
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: errs.append(a))
    app.port_var.set("")
    app._connect()
    app.update()
    assert errs and "explore without a bike" in str(errs[0])
    assert "--sim" not in str(errs[0])


def test_listen_only_announces_and_counts_down(app):
    # A4: the listen window announces itself immediately (not silent) and the button
    # shows it's working, so the wait can't be mistaken for a hang.
    app._listen_only()
    app.update()
    assert "Listening (Stage 1" in app.txt_probe.get("1.0", "end")   # announced up front
    assert str(app.btn_listen.cget("state")) == "disabled"     # busy signal
    # ...and it recovers to a normal, clickable button once the window closes
    assert _pump(app, lambda: str(app.btn_listen.cget("state")) == "normal")
    assert app.btn_listen.cget("text") == "Listen only (Stage 1)"


def _all_label_text(widget):
    out = []
    try:
        out.append(str(widget.cget("text")))
    except Exception:
        pass
    for child in widget.winfo_children():
        out.extend(_all_label_text(child))
    return out


def test_connect_guidance_is_cable_agnostic(app):
    # C4: the Connect-tab checklist and the Stage-1 listen result must not tell
    # the owner to physically disconnect/reconnect the FTDI Orange (TX). Listen
    # is TX-silent in software and the owner's harness is a fixed 3-wire cable,
    # so unplugging TX is optional, not a required step. (The status bar's
    # "● DISCONNECTED" is a different, legitimate use — target the checklist.)
    checklist = next((t for t in _all_label_text(app)
                      if "staged bring-up" in t.lower()), None)
    assert checklist is not None                   # sanity: found the checklist
    low = checklist.lower()
    assert "disconnect" not in low
    assert "reconnect" not in low
    assert "orange (tx)" not in low
    # the runtime listen-result guidance is clean too
    app._listen_only()
    assert _pump(app, lambda: "STAGE-1 LISTEN" in app.txt_probe.get("1.0", "end"))
    result = app.txt_probe.get("1.0", "end").lower()
    assert "connect & probe" in result             # sanity: got the GOOD guidance
    assert "reconnect" not in result


def test_instructions_text_matches_current_baseline_behavior():
    # C1 (review REL-2/FID-2): the F1 instructions must not claim FULL BASELINE
    # captures the ~1 MB log dump (Tier A moved the heavy dumps out of the baseline
    # behind a contactor-warning confirm), and Rides no longer come from a console
    # "log dump" on rev 41.
    from openmbb.gui import INSTRUCTIONS_TEXT
    low = INSTRUCTIONS_TEXT.lower()
    assert "and the ~1 mb log dump" not in low     # the stale baseline claim is gone
    assert "no heavy dumps" in low                 # baseline explicitly excludes them
    assert "eventlogdump" in low and "contactor" in low   # heavy reads are gated + warned
    assert "parsed from the log dump" not in low   # stale Rides source
    assert "ride log you load" in low              # correct Rides source


def test_safety_text_reflects_eeprom_read_and_new_blocks():
    # C1: bare eeprom is now an allowed read (B2); the destructive commands the
    # real rev-41 menu revealed are blocked.
    from openmbb.gui import SAFETY_TEXT
    assert "bare eeprom is an allowed read" in SAFETY_TEXT
    assert "format/erase/eeprom" not in SAFETY_TEXT     # the old over-claim is gone
    for cmd in ("dtc_clear", "force_all_storage_mode", "blcmds", "burn"):
        assert cmd in SAFETY_TEXT


def test_baseline_tolerant_saves_and_gates(app, monkeypatch):
    # C6: a failing dump doesn't discard the pass; the settings baseline is
    # saved before the dumps and essentials still unlock Phase 2. C7: meta file.
    app._connect()
    assert _pump(app, lambda: app.connected)
    orig = app.transport.exec_command

    def flaky(cmd, *a, **k):
        if cmd == "errorlogdump":
            raise RuntimeError("simulated cable hiccup")
        return orig(cmd, *a, **k)

    monkeypatch.setattr(app.transport, "exec_command", flaky)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    files = os.listdir(app.logger.dir)
    assert any(f.startswith("settings_baseline") for f in files)
    assert "session_meta.txt" in files


def test_baseline_incomplete_stays_locked(app, monkeypatch):
    # C6/C17: an empty settings dump must NOT unlock Phase 2
    app._connect()
    assert _pump(app, lambda: app.connected)
    orig = app.transport.exec_command

    def blank_set(cmd, *a, **k):
        if cmd == "set":
            return ""
        return orig(cmd, *a, **k)

    monkeypatch.setattr(app.transport, "exec_command", blank_set)
    app._baseline()
    assert _pump(app, lambda: "BASELINE INCOMPLETE"
                 in app.txt_out.get("1.0", "end"), timeout=120)
    assert not app.baseline_done


def test_session_meta_flags_charging(app):
    # C7: an on-charger baseline is stamped and flagged
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._write_session_meta("  - Mode                   : Charging")
    meta = os.path.join(app.logger.dir, "session_meta.txt")
    assert os.path.isfile(meta)
    assert "power_mode: Charging" in open(meta, encoding="utf-8").read()
    assert "CHARGING" in app.txt_out.get("1.0", "end")


# --- Phase D: GUI state machine & write-flow integrity ----------------------

def test_pump_survives_callback_exception(app):
    # D2: a raising callback must not kill the pump (which would strand _busy)
    ran = []

    def boom():
        raise RuntimeError("boom in a callback")

    app._cbq.put(boom)
    app._cbq.put(lambda: ran.append("after"))
    assert _pump(app, lambda: ran == ["after"])


def test_on_close_warns_when_busy(app, monkeypatch):
    # D3: closing during an operation must prompt, not silently interrupt
    import tkinter.messagebox as mb
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(1) or False)
    app._busy = True
    app._on_close()               # declines -> must NOT destroy
    assert asked and app.winfo_exists()
    app._busy = False


def test_reboot_clears_login(app, monkeypatch):
    # D6: a mid-session reboot error re-locks login/baseline
    from openmbb.transport import ConsoleRebootError
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)

    def boom(cmd, *a, **k):
        raise ConsoleRebootError("MBB rebooted mid-session")

    monkeypatch.setattr(app.transport, "exec_command", boom)
    app._read_cmd("status")
    assert _pump(app, lambda: not app.logged_in)
    assert not app.baseline_done


def test_analyze_refuses_current_while_busy(app):
    # D7: don't read the live session folder while a capture is mid-write
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._busy = True
    app._analyze_use_current()
    app.update()
    assert app.analyze_session is None       # refused
    app._busy = False


def test_post_login_baseline_saved(app):
    # D8: the post-login settings dump is saved as a labeled baseline
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    files = os.listdir(app.logger.dir)
    assert any(f.startswith("settings_baseline_postlogin") for f in files)


def test_rides_no_console_telemetry_guides_to_load(app):
    # A1: a session with no decoded ride records guides the user to Load ride log
    # (rev-41 has no console ride-telemetry command)
    import tempfile
    from openmbb import sessions
    app.analyze_session = sessions.Session(
        tempfile.mkdtemp(), {"dumplogs": "packed binary junk, no riding lines here"}, "")
    app._render_rides()
    app.update()
    assert "load ride log" in app.lbl_ride_totals.cget("text").lower()


def test_write_mismatch_offers_revert(app, monkeypatch):
    # F8: a read-back mismatch offers an immediate revert to the old value
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: True)      # accept the offer
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: len(app.settings) >= 30)
    # make the write a no-op so the read-back stays at the old value -> mismatch
    monkeypatch.setattr(app.transport, "write_setting", lambda *a, **k: "no-op")
    app.tree.selection_set("spfront")
    app.newval_var.set("22")
    app.unlock_var.set(True)
    app._write()
    assert _pump(app, lambda: app.newval_var.get() == "20")       # revert staged


def test_connect_while_busy_does_not_reset_state(app):
    # T3: Connect during an in-flight op must be refused BEFORE the destructive
    # reset — the port and revert trail must survive
    app._connect()
    assert _pump(app, lambda: app.connected)
    tr = app.transport
    app.journal_entries.append(("spfront", "20", "22"))     # pretend a prior write
    app._busy = True
    app._connect()                                          # must refuse, not reset
    app.update()
    assert app.transport is tr and app.connected            # port not yanked
    assert app.journal_entries                              # revert trail intact
    app._busy = False


def test_baseline_reboot_aborts_and_stays_locked(app, monkeypatch):
    # T4: a reboot mid-baseline must abort + re-gate, not be tallied as a failed
    # command that lets baseline complete
    from openmbb.transport import ConsoleRebootError
    app._connect()
    assert _pump(app, lambda: app.connected)
    orig = app.transport.exec_command

    def boom(cmd, *a, **k):
        if cmd == "errorlogdump":
            raise ConsoleRebootError("MBB rebooted mid-baseline")
        return orig(cmd, *a, **k)

    monkeypatch.setattr(app.transport, "exec_command", boom)
    app._baseline()
    assert _pump(app, lambda: not app._busy, timeout=120)
    assert not app.baseline_done                            # not declared complete


def test_heavy_dump_requires_confirmation(app, monkeypatch):
    # A2: a heavy log dump (contactor risk) must go through a confirm dialog
    import tkinter.messagebox as mb
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(a) or False)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._read_heavy("eventlogdump")          # user declines -> nothing runs
    app.update()
    assert asked                             # the warning WAS shown
    assert "contactor" in str(asked[0]).lower()   # and it names the real risk


def test_raw_box_heavy_command_confirms_and_can_cancel(app, monkeypatch):
    # A1 (SAFE-1): eventlogdump/dumpall typed into the raw command box must get the
    # SAME contactor confirm as the Heavy buttons, and send NOTHING if the user cancels.
    import tkinter.messagebox as mb
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(a) or False)
    app._connect()
    assert _pump(app, lambda: app.connected)
    sent = []
    monkeypatch.setattr(app.transport, "exec_command",
                        lambda cmd, *a, **k: sent.append(cmd) or "")
    for heavy in ("eventlogdump", "dumpall"):
        asked.clear()
        app.raw_var.set(heavy)
        app._raw_send()
        app.update()
        assert asked, heavy                          # the contactor confirm WAS shown
        assert "contactor" in str(asked[0]).lower()  # and names the real risk
        assert heavy not in sent                     # user cancelled -> nothing sent


def test_raw_box_heavy_variant_gets_dump_class_timeouts(app, monkeypatch):
    # A1/SAFE-2: a raw-box heavy VARIANT ("eventlogdump 5") is sent verbatim, gets the
    # contactor confirm, the 30 s heavy idle, AND the 900 s dump max_time — never the
    # 60 s cap that would truncate a multi-minute dump mid-stream.
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)   # user proceeds
    app._connect()
    assert _pump(app, lambda: app.connected)
    sent = []

    def rec(cmd, *a, **k):
        sent.append({"cmd": cmd, "idle": k.get("idle_timeout"), "max": k.get("max_time")})
        return ""

    monkeypatch.setattr(app.transport, "exec_command", rec)
    app.raw_var.set("eventlogdump 5")
    app._raw_send()
    assert _pump(app, lambda: any(s["cmd"] == "eventlogdump 5" for s in sent))
    s = next(s for s in sent if s["cmd"] == "eventlogdump 5")
    assert s["idle"] is not None and s["idle"] >= 30.0    # heavy idle (A1)
    assert s["max"] is not None and s["max"] >= 900.0     # dump-class max_time (SAFE-2)


def test_baseline_never_sends_heavy_or_dumplogs(app, monkeypatch):
    # A2 (REL-4): the FULL BASELINE must never auto-send a contactor-dropping heavy
    # dump (or the phantom `dumplogs`) — only the quick reads + set + the small
    # errorlogdump. Pin the actual sent sequence so a gui-side regression can't slip
    # a heavy command back into the routine read while the rest of the suite stays green.
    from openmbb.transport import HEAVY_COMMANDS
    app._connect()
    assert _pump(app, lambda: app.connected)
    sent = []
    orig = app.transport.exec_command

    def rec(cmd, *a, **k):
        sent.append(cmd)
        return orig(cmd, *a, **k)

    monkeypatch.setattr(app.transport, "exec_command", rec)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    for h in HEAVY_COMMANDS:
        assert h not in sent                 # eventlogdump/dumpall NEVER in baseline
    assert "dumplogs" not in sent            # the phantom command is gone
    assert "errorlogdump" in sent and "set" in sent   # the real ones DID run
    # D4: obd (output never captured live) runs LAST — after the settings backup
    assert "obd" in sent and sent.index("obd") > sent.index("set")


def test_reboot_regate_disarms_master_unlock(app, monkeypatch):
    # T7: a reboot re-gate must also drop the master UNLOCK WRITES toggle
    from openmbb.transport import ConsoleRebootError
    app._connect()
    assert _pump(app, lambda: app.connected)
    app.unlock_var.set(True)

    def boom(*a, **k):
        raise ConsoleRebootError("MBB rebooted")

    monkeypatch.setattr(app.transport, "exec_command", boom)
    app._read_cmd("status")
    assert _pump(app, lambda: not app.unlock_var.get())


def test_raw_box_logout_regates(app):
    # D3 (review SAFE-3): `logout` typed in the raw box de-escalates the console, so
    # the GUI must drop its login state + master unlock — otherwise the Writes tab
    # stays visibly unlocked against a level-0 console.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    app.unlock_var.set(True)                        # arm the master unlock
    app.raw_var.set("logout")
    app._raw_send()
    assert _pump(app, lambda: not app.logged_in)    # login state dropped
    assert not app.unlock_var.get()                 # and the master unlock disarmed


def test_stale_log_dir_falls_back(app, monkeypatch, tmp_path):
    # G2: a configured save folder that can't be created must not block — the app
    # falls back to the default instead of raising a raw OSError at connect time
    from openmbb import config
    good = str(tmp_path / "good")
    monkeypatch.setattr(config, "DEFAULT_LOG_DIR", good)
    (tmp_path / "afile").write_text("x")            # a file where a dir would go
    app.log_dir = str(tmp_path / "afile" / "cannot_make_dir_under_a_file")
    app._ensure_log_dir()
    assert app.log_dir == good
    assert os.path.isdir(good)


def test_probe_fails_on_garbled_version(app, monkeypatch):
    # T15/C3: a non-empty but unrecognized `version` banner fails the probe
    from openmbb.sim import SimPort

    class GarbledVer(SimPort):
        def _handle(self, cmd):
            if cmd.strip().lower() == "version":
                self._push(cmd.encode() + b"\r\n")
                self._respond("xyzzy nothing recognizable here")
                return
            super()._handle(cmd)

    monkeypatch.setattr(app, "_make_port", lambda pn: GarbledVer())
    app._connect()
    assert _pump(app, lambda: not app._busy)
    assert not app.connected


def test_probe_warns_on_unknown_firmware_rev(app, monkeypatch):
    # T15/C3: a different firmware rev connects but warns
    import tkinter.messagebox as mb
    from openmbb.sim import SimPort
    warned = []
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: warned.append(a))

    class Rev99(SimPort):
        def _handle(self, cmd):
            if cmd.strip().lower() == "version":
                self._push(cmd.encode() + b"\r\n")
                self._respond("== Main Bike Board ==\n  Firmware Rev : 99")
                return
            super()._handle(cmd)

    monkeypatch.setattr(app, "_make_port", lambda pn: Rev99())
    app._connect()
    assert _pump(app, lambda: app.connected)      # still connects
    assert warned                                 # but warned about the rev
    assert app.lbl_ver.cget("text").endswith("99")


def test_reconnect_re_earns_phases(app):
    # T16/D1: a second connect must drop all prior phase state
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: len(app.settings) >= 30)
    app.unlock_var.set(True)
    app._connect()                                # reconnect
    assert _pump(app, lambda: app.connected)
    assert not app.baseline_done and not app.logged_in
    assert app.settings == {} and not app.unlock_var.get()


def _login_sim(attempt_reply, level):
    from openmbb.sim import SimPort

    class LoginSim(SimPort):
        def _handle(self, cmd):
            parts = cmd.split()
            if parts and parts[0].lower() == "login":
                self._push(cmd.encode() + b"\r\n")
                if len(parts) == 1:
                    self._respond("Login Level: %d" % level)
                else:
                    self._respond(attempt_reply)
                return
            super()._handle(cmd)

    return LoginSim


@pytest.mark.parametrize("attempt,level,expect_in", [
    ("you are not logged in", 0, False),      # level query says 0 -> stays locked
    ("Access granted, welcome", 2, True),     # unfamiliar wording, level 2 -> unlocks
    ("login failed: incorrect password", 0, False),   # fail word vetoes
])
def test_login_decided_by_level_query(app, monkeypatch, attempt, level, expect_in):
    # T17/D5: login success comes from the read-only `login` level query, not the
    # attempt wording
    monkeypatch.setattr(app, "_make_port", lambda pn: _login_sim(attempt, level)())
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: not app._busy)
    assert app.logged_in is expect_in


def test_on_close_confirm_journals_and_closes(app, monkeypatch):
    # T18/D3: the accept-close-while-busy path journals a trace and closes
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    app._connect()
    assert _pump(app, lambda: app.connected)
    tr = app.transport
    destroyed = []
    monkeypatch.setattr(app, "destroy", lambda: destroyed.append(1))
    app._busy = True
    app._on_close()
    assert destroyed                                        # proceeded to close
    journal = open(tr.logger.journal_path, encoding="utf-8").read()
    assert "app closed while busy" in journal
    app._busy = False


def test_ingest_settings_warns_and_preserves_on_unparsed(app):
    # T19/B3: a non-empty dump that parses to nothing warns and keeps the old dict
    app._connect()
    assert _pump(app, lambda: app.connected)
    app.settings = {"spfront": {"value": "20"}}
    app._ingest_settings("some output the parser does not recognize at all here")
    app.update()
    assert "not recognized" in app.txt_out.get("1.0", "end").lower()
    assert app.settings == {"spfront": {"value": "20"}}    # preserved, not wiped


def test_reconnect_closes_old_transport(app, monkeypatch):
    # T20/C5: reconnecting must close the previously-open port (not leak it)
    from openmbb.sim import SimPort
    ports = []

    class ClosableSim(SimPort):
        def __init__(self):
            super().__init__()
            self.closed = False
            ports.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(app, "_make_port", lambda pn: ClosableSim())
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert ports[0].closed                        # first port closed on reconnect


def test_compare_add_current_refused_while_busy(app):
    # T20/D7: don't read the live session folder mid-capture
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._busy = True
    app._compare_add_current()
    app.update()
    assert not app.compare_list                            # refused
    app._busy = False


def test_load_ride_log_from_file(app, monkeypatch, tmp_path):
    # T20/F4: load rides from an external zero-log-parser .txt
    from tkinter import filedialog
    text = "\n".join(
        " %05d 05/16/2026 08:%02d:00 Riding PackTemp: h 27C, l 26C, PackSOC: %d%%, "
        "MotRPM: 3100, MotTemp: 41C, Odo: 61%02dkm" % (i, i, 90 - i, 10 + i)
        for i in range(8))
    f = tmp_path / "ride.txt"
    f.write_text(text, encoding="utf-8")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda *a, **k: str(f))
    app._load_ride_log()
    app.update()
    assert len(app.ride_tree.get_children()) >= 1


def test_write_records_before_verify_failure(app, monkeypatch):
    # D4/C8: a glitch AFTER the write but before verify must still leave a
    # revert entry + a PENDING journal line (the bike may have changed)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)

    app.tree.selection_set("spfront")
    app.newval_var.set("22")
    app.unlock_var.set(True)
    orig = app.transport.exec_command
    calls = {"set": 0}

    def flaky(cmd, *a, **k):
        if cmd == "set":                     # the plain re-read/verify dumps
            calls["set"] += 1
            if calls["set"] >= 2:            # fail the post-write verify read
                raise RuntimeError("glitch during verify")
        return orig(cmd, *a, **k)

    monkeypatch.setattr(app.transport, "exec_command", flaky)
    app._write()
    assert _pump(app, lambda: len(app.journal_entries) > 0)
    journal = open(app.transport.logger.journal_path, encoding="utf-8").read()
    assert "PENDING" in journal
