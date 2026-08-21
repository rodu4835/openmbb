"""End-to-end GUI flow in the simulator: connect -> baseline -> login -> write.

Skipped automatically where Tk has no display. Run with the real interpreter
on Windows; drives the app headlessly by pumping the event loop.
"""

import gc
import os
import time

import pytest

from openmbb.safety import WRITE_WHITELIST
from openmbb.transport import first_number


@pytest.fixture
def app(monkeypatch):
    tk = pytest.importorskip("tkinter")
    from openmbb import dialogs as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: errors.append(a))
    import tempfile
    from pathlib import Path
    # hermetic config: unit/password prefs must never touch the user's real
    # ~/.openmbb/config.json (several tests call _apply_units/_apply_temp_units).
    from openmbb import config as _cfg
    _tmpcfg = Path(tempfile.mkdtemp(prefix="zccfg_")) / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_DIR", _tmpcfg.parent)
    monkeypatch.setattr(_cfg, "CONFIG_PATH", _tmpcfg)
    from openmbb.gui import build_gui
    # hermetic: sessions go to a temp dir, never the user's configured location.
    # Build directly (no throwaway probe root) and skip cleanly with no display.
    try:
        application = build_gui(sim=True, log_dir=tempfile.mkdtemp(prefix="zcflow_"))
    except tk.TclError:
        pytest.skip("no display available for Tk")
    application._errors = errors
    yield application
    # A5 safety net: a test that opened the heavy-read modal must not leak a
    # grabbed Toplevel + a running indeterminate Progressbar into teardown.
    try:
        if getattr(application, "_read_modal_up", False):
            application._close_read_modal()
    except Exception:
        pass
    # Deterministic teardown. Tk Variable objects (StringVar/BooleanVar/…) hold
    # reference cycles through their widgets, so they aren't freed the instant the
    # app is destroyed — they linger until CPython's cyclic GC runs, which can be
    # mid-way through a *later* test's app.update(). When it fires there, the dead
    # Variables' __del__ calls into a torn-down interpreter ("main thread is not in
    # main loop") and can disrupt the live test's event pump. So: cancel any pending
    # after() callbacks (Watch timer, listen countdown), destroy, then force a
    # collect here so each test drains the *previous* test's Tk cycles in a
    # controlled spot instead of letting them accumulate and fire mid-test.
    try:
        for aid in application.tk.call("after", "info"):
            try:
                application.after_cancel(aid)
            except Exception:
                pass
    except Exception:
        pass
    try:
        application.destroy()
    except Exception:
        pass
    gc.collect()


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

    # write blocked without the unlock toggle -> value unchanged from the last read
    app._write_value("spfront", "22")
    app.update()
    assert not app._row_differs_from_baseline("spfront")     # refused

    # unlock and write for real — the write is async (send -> verify -> ingest), so
    # wait for the read-back value to land; then the row differs from the last read
    # (so it offers '↺ Reset') and the on-disk journal recorded it.
    app.unlock_var.set(True)
    app._write_value("spfront", "22")
    assert _pump(app, lambda: first_number(
        app.settings.get("spfront", {}).get("value", "")) == "22")
    assert app._row_differs_from_baseline("spfront")
    assert os.path.exists(os.path.join(app.logger.dir, "writes_journal.txt"))
    backups = [f for f in os.listdir(app.logger.dir)
               if f.startswith("settings_backup")]
    assert backups
    assert not app._errors


def test_login_box_prefilled_no_try_known_button(app):
    # owner: Login is now Password:[box=default][Login] — the box is pre-filled
    # with a community-known default (no saved password in the hermetic config),
    # and the separate "Try known passwords" button is gone.
    from openmbb.gui import COMMUNITY_PASSWORDS
    assert app.login_pw.get() == COMMUNITY_PASSWORDS[0]
    def _texts(w, out):
        try:
            out.append(str(w.cget("text")))
        except Exception:
            pass
        for c in w.winfo_children():
            _texts(c, out)
    texts = []
    _texts(app, texts)
    assert not any("try known passwords" in t.lower() for t in texts)
    assert any(t == "Login" for t in texts)          # the single login button


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


def test_condition_tab_reports_a_reset_and_names_what_it_could_not_answer(app):
    # The Condition tab is what someone reads standing next to a bike they do
    # not know, so the two things it must never do are stay silent about a
    # statistics reset and let an unanswerable check look like a pass.
    from openmbb import sessions
    log = (" 08027  06/13/2026 06:52:42  Entering Bootloader by request\n"
           " 08028                    0  Stats Read Failed, Resetting All Stats to Defaults\n"
           " 08029  06/24/2026 21:59:31  Riding  PackTemp: h 27C, PackSOC:100%, "
           "Vpack:115.1V, BattAmps: 99, AmbTemp: 18C, MotRPM:1109, Odo: 6404km, "
           "Curr limit: 520 A (100%), MinCell: 4024mV\n")
    app._analyze_set(sessions.Session("x", {"eventlogdump": log}, ""))
    app.update()
    rows = [app.cond_tree.item(i) for i in app.cond_tree.get_children()]
    checks = {r["values"][0] for r in rows}
    assert "Statistics RESET" in checks
    reset = [r for r in rows if r["values"][0] == "Statistics RESET"][0]
    assert "06/13/2026 06:52:42" in str(reset["values"][1])
    assert reset["tags"] == ["attention"] or "attention" in reset["tags"]
    # no charge session in this log, so capacity must be declared, not omitted
    undetermined = " ".join(str(r["values"][1]) for r in rows
                            if r["values"][0] == "Not determined")
    assert "charge capacity" in undetermined


def test_condition_tab_is_empty_until_a_session_is_loaded(app):
    # opening the app and looking at Condition before loading anything must show
    # nothing at all, rather than a set of checks that read as passes
    assert app.analyze_session is None
    assert app.cond_tree.get_children() == ()


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

    # gearing calculator (now a Tools -> Gearing calculator dialog)
    gwin = app._show_gearing_calc()
    app.gear_front.set("22")
    app.gear_rear.set("88")
    app._gearing_compute()
    gtext = app.txt_gearing.get("1.0", "end")
    assert "4.00" in gtext and "spfront = 22" in gtext
    # bad gearing input is rejected gracefully, not crashed
    app.gear_circ.set("0")
    app._gearing_compute()
    assert "positive" in app.txt_gearing.get("1.0", "end").lower()
    gwin.destroy()

    # compare de-dupes the same session (adding current twice keeps one)
    app._compare_add_current()
    app._compare_add_current()
    app.update()
    assert len(app.compare_list) == 1
    assert not app._errors


def test_menubar_normalized_labels_no_chevrons(app):
    # A2 + T0.3: the menu bar is a themed ttk Menubutton strip (not the OS-white
    # native menubar), now with conventional labels File / Tools / Help and with
    # the down-chevron indicator element stripped from the menubutton layout.
    import tkinter.ttk as ttk
    style = ttk.Style()
    found = {}

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Menubutton):
                found[str(c.cget("text"))] = str(c.cget("style"))
            walk(c)

    walk(app)
    assert {"File", "Tools", "Help"} <= set(found)
    # the old app-specific labels are gone
    assert not ({"Session", "Bike"} & set(found))
    # and the app no longer installs a native menubar
    assert not app.cget("menu")

    # the chevron/indicator element was stripped from the menubutton layout
    def has_indicator(layout):
        for name, opts in layout:
            if "indicator" in name.lower():
                return True
            if "children" in opts and has_indicator(opts["children"]):
                return True
        return False

    assert not has_indicator(style.layout(found["File"]))


def test_menu_popup_builds_and_reorganized(app):
    # The dropdown is a custom themed Toplevel (no native white border), reorganized
    # so Tools has a COM-port fly-out submenu + a Settings dialog (units moved into
    # Settings), and the COM submenu + Settings build cleanly.
    import tkinter as tk
    import tkinter.ttk as ttk

    for specs in (app._file_menu(), app._tools_menu(), app._help_menu()):
        assert specs and all(s[0] in ("cmd", "sep", "radio", "submenu") for s in specs)
    tools = app._tools_menu()
    assert any(s[0] == "submenu" for s in tools)                    # COM-port fly-out
    assert any(s[0] == "cmd" and "settings" in s[1].lower() for s in tools)
    assert any(s[0] == "cmd" and "issue" in s[1].lower() for s in app._help_menu())
    # the COM-port submenu builds a valid spec list (no ports -> an info row)
    com = app._com_port_menu()
    assert com and all(s[0] in ("cmd", "sep", "radio") for s in com)
    assert any(s[0] == "cmd" and "refresh" in s[1].lower() for s in com)
    # the units radios now live in the Settings dialog, bound to the unit vars
    assert app.units_var.get() in ("km", "mi")
    assert app.temp_units_var.get() in ("C", "F")
    sw = app._show_settings("Units")
    assert isinstance(sw, tk.Toplevel)
    sw.destroy()

    # find the File menubutton and open its popup
    mb = None

    def walk(w):
        nonlocal mb
        for c in w.winfo_children():
            if isinstance(c, ttk.Menubutton) and str(c.cget("text")) == "File":
                mb = c
            walk(c)

    walk(app)
    assert mb is not None
    app._menu_popup(mb, app._file_menu())
    pops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
    assert pops                                    # a themed popup appeared
    assert app._open_menu is not None
    for w in pops:
        w.destroy()
    app._open_menu = None
    app._open_submenu = None


def _descendants(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out.extend(_descendants(c))
    return out


def test_select_port_sets_next_connect_port(app):
    # the Tools -> COM port fly-out picks the port for the next Connect and mirrors
    # it into the Connect-tab combobox; the menu label reflects the choice.
    app._select_port("COM7")
    assert app.port_var.get() == "COM7"
    assert app.cbo_port.get() == "COM7"
    assert app._current_port_label() == "COM7"     # not connected -> shows selection


def test_bike_about_merged_two_tab_window(app):
    # Bike info + About are one two-tab popup; each entry opens its own tab.
    import tkinter as tk
    import tkinter.ttk as ttk
    for active in ("Bike info", "About"):
        w = app._bike_about_window(active)
        assert isinstance(w, tk.Toplevel)
        nbs = [c for c in _descendants(w) if isinstance(c, ttk.Notebook)]
        assert nbs and len(nbs[0].tabs()) == 2     # exactly two tabs
        w.destroy()
    assert not app._errors


def test_connect_shows_banner_not_autojump(app):
    # owner: connect no longer auto-jumps to Read — it reveals a "Connected"
    # confirmation (no button) and the pre-connect controls collapse away.
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert app.connect_success.winfo_manager() == "pack"       # banner is shown
    assert "Connected" in app.lbl_connect_success.cget("text")
    assert not hasattr(app, "btn_continue_read")               # button removed
    # the now-pointless pre-connect controls are hidden once connected
    assert app.connect_row.winfo_manager() == ""
    assert app.connect_help.winfo_manager() == ""
    assert app.nb.index(app.nb.select()) == 0                  # still on Connect (no jump)


def test_analyze_charts_render_every_metric(app):
    # the new Charts tab plots ride-log telemetry on a tk.Canvas (no matplotlib);
    # every metric draws without error, and an empty log shows a friendly message.
    recs = [{"odo_km": 100 + i * 0.5, "soc": 92 - i, "vpack": 116.0 - i * 0.2,
             "pack_temp_c": 24 + i * 0.3, "motor_temp_c": 40 + i * 0.5,
             "motamps": 40 + i, "ts": "05/16/2026 08:%02d:00" % i}
            for i in range(24)]
    app._render_ride_records(recs, "test log")
    app.update_idletasks()
    assert app._ride_records
    cv = app.chart_canvas
    for metric in app._CHART_METRICS:
        app.chart_metric.set(metric)
        app._render_charts()
        app.update_idletasks()
        assert cv.find_all(), "nothing drawn for %s" % metric   # canvas has items
    # temperature unit switch re-renders in F without error
    app.temp_units_var.set("F")
    app._apply_temp_units()
    app.update_idletasks()
    # empty ride set -> a guidance message, still no crash
    app._render_ride_records([], "empty")
    app.update_idletasks()
    assert cv.find_all()
    assert not app._errors


def test_themed_dialog_builds_and_centers(app):
    # T0.2: the REAL themed dialog (not the monkeypatched wrapper) builds,
    # centers on the app window, and tears down cleanly. Auto-dismiss it so the
    # modal wait_window() returns during the headless test.
    import tkinter as tk
    from openmbb import dialogs

    def dismiss():
        for w in app.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()

    app.after(120, dismiss)
    val = dialogs._dialog("OpenMBB", "A themed, centered dialog.", "info",
                          [("OK", True, True)], default=True)
    assert val is True
    assert not [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]


def test_landing_is_front_door(app):
    # T1: a landing 'front door' shows first (notebook hidden) with a blurb and
    # the two entry actions; leaving it reveals the notebook on the Connect tab.
    import tkinter.ttk as ttk
    assert hasattr(app, "landing")
    assert app.landing.winfo_manager() == "pack"     # landing is visible
    assert app.nb.winfo_manager() != "pack"          # notebook hidden behind it

    labels = set()

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Button):
                labels.add(str(c.cget("text")))
            walk(c)

    walk(app.landing)
    assert "Connect" in labels                        # the two entry actions
    assert "Analyze" in labels
    assert not any("instructions" in t.lower() for t in labels)   # moved to Help

    # the simulator toggle lives on the landing (owner couldn't find it in a menu)
    checks = []

    def walk_cb(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Checkbutton):
                checks.append(str(c.cget("text")))
            walk_cb(c)

    walk_cb(app.landing)
    assert any("simulator" in t.lower() for t in checks)

    app._leave_landing()
    assert app.nb.winfo_manager() == "pack"           # notebook now shown
    assert app.landing.winfo_manager() != "pack"
    assert app.nb.index(app.nb.select()) == 0          # on the Connect tab


def test_cable_wizard_verifies_and_offers_connect(app):
    # "Test your cable" opens a self-contained wizard; running the test in sim mode
    # verifies the link and then offers Connect & probe / Retry / Cancel.
    import tkinter as tk
    from tkinter import ttk
    app._show_cable_wizard()
    wins = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
    assert wins, "the cable wizard should open a dialog"
    wiz = wins[-1]

    def find_btn(sub):
        out = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and sub.lower() in str(c.cget("text")).lower():
                    out.append(c)
                walk(c)

        walk(wiz)
        return out[0] if out else None

    start = find_btn("Start test")
    assert start is not None
    start.invoke()
    assert _pump(app, lambda: find_btn("Connect") is not None, timeout=20)
    for w in [x for x in app.winfo_children() if isinstance(x, tk.Toplevel)]:
        w.destroy()


def test_sim_toggle_shows_indicator(app):
    # toggling simulator mode is NOT silent — it updates the single status-bar
    # indicator (owner: the redundant landing badge was removed).
    app.sim_var.set(False)
    app._on_sim_toggle()
    assert str(app.lbl_sim.cget("text")) == ""
    assert not hasattr(app, "_sim_badge")              # landing badge removed
    app.sim_var.set(True)
    app._on_sim_toggle()
    assert "SIMULATOR" in str(app.lbl_sim.cget("text"))


def test_dashboard_layout(app):
    # T2: the connected view has a status header + the primary "Pull full
    # database" accent action, and the header tracks connection state.
    assert hasattr(app, "dash_header")
    assert "Pull full database" in str(app.btn_baseline.cget("text"))
    assert str(app.btn_baseline.cget("style")) == app.sty["accent"]
    assert "Not connected" in str(app.dash_header.cget("text"))
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert "CONNECTED" in str(app.dash_header.cget("text"))


def test_action_bar_hidden_until_connected(app):
    # T3 (owner feedback): the global safe-quit affordance is HIDDEN until
    # connected, shown once connected + idle, and hidden again during any op unsafe
    # to interrupt (busy); safe-quit still refuses (doesn't close) if busy.
    assert hasattr(app, "btn_safequit")
    assert not hasattr(app, "btn_done")   # the "Done — what's next?" hub was retired
    # before connecting: not shown at all
    assert app.btn_safequit.winfo_manager() == ""
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert app.btn_safequit.winfo_manager() == "pack"     # connected + idle: visible
    app._set_busy(True)
    assert app.btn_safequit.winfo_manager() == ""         # busy: hidden again
    app._safe_disconnect()                # must refuse while busy
    assert app.winfo_exists()
    app._set_busy(False)
    assert app.btn_safequit.winfo_manager() == "pack"


def test_pull_shows_confirmation_under_button(app):
    # after a successful pull, a "✓ Full database pulled & backed up" confirmation
    # shows in the progress label under the Pull button (owner: no bottom banner /
    # no extra buttons / no "what next").
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    assert "backed up" in app.lbl_prog.cget("text").lower()
    assert not hasattr(app, "read_success")            # the bottom-bar banner is gone
    assert not hasattr(app, "btn_done")


def test_writes_flow_logs_in_and_lands_on_writes(app):
    # _start_writes_flow (Read banner / Gearing "Set up writes") logs in with no
    # extra click and lands on Writes when a backup already exists.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._start_writes_flow()
    assert _pump(app, lambda: app.logged_in)          # logged in without a 2nd click
    assert _pump(app, lambda: "writes" in
                 str(app.nb.tab(app.nb.select(), "text")).lower())


def test_writes_flow_without_baseline_runs_backup_first(app):
    # _start_writes_flow with NO backup yet must run the pull (saves a backup),
    # then log in, then land on Writes — instead of dead-ending on a locked tab.
    # askokcancel is True in the fixture, so it proceeds.
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert not app.baseline_done
    app._start_writes_flow()
    assert _pump(app, lambda: app.baseline_done, timeout=120)   # backup ran first
    assert _pump(app, lambda: app.logged_in)                    # then logged in
    assert _pump(app, lambda: "writes" in
                 str(app.nb.tab(app.nb.select(), "text")).lower())
    assert str(app.nb.tab(3, "state")) == "normal"              # Writes now unlocked


def test_writes_flow_offline_asks_to_connect(app, monkeypatch):
    # from the offline Gearing tab, _start_writes_flow with no live session must
    # guide the user to connect (not try to run a pull with no transport).
    from openmbb import dialogs as mb
    infos = []
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: infos.append(a))
    assert not app.connected
    app._start_writes_flow()
    assert infos and "Connect" in str(infos[0])


def test_home_screen_is_reachable_again(app):
    # P3: the Home screen is a place, not a one-shot splash — File -> Home screen
    # re-shows it after startup, and it's listed in the File menu.
    assert any(s[0] == "cmd" and "home" in s[1].lower() for s in app._file_menu())
    app._leave_landing()
    assert app.landing.winfo_manager() != "pack"      # left Home
    app._show_landing()
    assert app.landing.winfo_manager() == "pack"      # back on Home
    assert app.nb.winfo_manager() != "pack"           # notebook hidden behind it


def test_home_connect_button_returns_to_live_session(app):
    # the Home 'Connect & probe' button while ALREADY connected must not reconnect (tear
    # down the live session) — it just returns to the Read tab.
    app._connect()
    assert _pump(app, lambda: app.connected)
    tr = app.transport
    app._show_landing()
    app._landing_connect()                             # already connected
    assert app.transport is tr                         # same live session, not reset
    assert app.nb.index(app.nb.select()) == 1          # Read tab
    assert app.landing.winfo_manager() != "pack"


def test_landing_offers_offline_analyze(app):
    # P3: the Home screen has a no-bike lane into Analyze (a past session).
    labels = _all_label_text(app.landing)
    assert any(t.strip().lower() == "analyze" for t in labels)


def test_read_command_line_clears_and_history(app):
    # Read page command line: Enter sends the command, clears the box like a
    # terminal, and ↑/↓ cycles through what you've typed.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app.raw_var.set("status")
    app._cmd_enter()
    assert app.raw_var.get() == ""                 # cleared after send
    assert "status" in app._cmd_history
    app._cmd_history_nav(-1)                        # ↑ recalls it
    assert app.raw_var.get() == "status"
    app._cmd_history_nav(1)                         # ↓ past the end clears
    assert app.raw_var.get() == ""


def test_console_tab_routes_output_and_guards_connection(app, monkeypatch):
    # the raw command line lives on the Console tab now; output goes to ITS console
    # (not the Read output), and it refuses to send when not connected.
    from openmbb import dialogs as mb
    infos = []
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: infos.append(a))
    assert hasattr(app, "txt_console") and hasattr(app, "raw_var")
    app.raw_var.set("status")
    app._cmd_enter()                                   # not connected -> guarded
    assert infos and "Connect" in str(infos[0])
    app._connect()
    assert _pump(app, lambda: app.connected)
    app.raw_var.set("status")
    app._cmd_enter()
    assert _pump(app, lambda: "### status" in app.txt_console.get("1.0", "end"))
    assert "### status" not in app.txt_out.get("1.0", "end")   # not on the Read output


def test_gearing_calculator_is_in_tools(app):
    # gearing is a calculator, not analysis — it moved to Tools -> Gearing calculator.
    import tkinter as tk
    assert any(s[0] == "cmd" and "gearing" in s[1].lower() for s in app._tools_menu())
    w = app._show_gearing_calc()
    assert isinstance(w, tk.Toplevel)
    app.gear_front.set("22")
    app.gear_rear.set("88")
    app._gearing_compute()
    assert "4.00" in app.txt_gearing.get("1.0", "end")
    w.destroy()


def test_dangerous_command_needs_typed_confirm(app, monkeypatch):
    # F: a blocklisted command is no longer hard-refused from the raw box — it
    # routes through the type-"confirm" gate, and is dropped if not confirmed.
    app._connect()
    assert _pump(app, lambda: app.connected)
    seen = []
    monkeypatch.setattr(app, "_confirm_dangerous",
                        lambda cmd, reason: seen.append(cmd) or False)
    app.raw_var.set("dtc_clear")
    app._raw_send()
    assert seen == ["dtc_clear"]                    # the confirm gate was shown


def test_confirm_bypasses_transport_block(tmp_path):
    # transport: confirmed=True lets a blocklisted command through; control chars
    # (multi-line smuggling) are still refused regardless of confirm.
    from openmbb.transport import Transport, SessionLogger
    from openmbb.sim import SimPort
    from openmbb.safety import BlockedCommandError
    tr = Transport(SimPort(), SessionLogger(base_dir=str(tmp_path), tag="c"))
    with pytest.raises(BlockedCommandError):
        tr.exec_command("dtc_clear", idle_timeout=0.5)        # blocked w/o confirm
    out = tr.exec_command("dtc_clear", confirmed=True, idle_timeout=0.5)
    assert isinstance(out, str)                                # sent with confirm
    with pytest.raises(BlockedCommandError):
        tr.exec_command("a\nsettingsrst", confirmed=True)     # newline still refused


def test_command_reference_opens_html(app, monkeypatch):
    # G: Help -> Command reference opens the stylized HTML reference page.
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: opened.append(a) or True)
    app._show_command_reference()
    assert opened


def test_analyze_help_enriches_notes(app):
    # T4: the novice explanations (analyze_help.json) load and enrich a health
    # metric's row note with what-it-is / how-it-fits / healthy context.
    hm = app._analyze_help_map()
    assert hm, "analyze_help.json should load"
    assert "cell balance" in hm and "isolation resistance" in hm
    enriched = app._enrich_note({"label": "Cell balance", "note": "base note"}, hm)
    assert "base note" in enriched
    assert "What it is" in enriched and "In the system" in enriched
    # a metric with no help entry is returned unchanged (additive only)
    assert app._enrich_note({"label": "zzz", "note": "x"}, hm) == "x"


def test_write_help_enriches_setting(app):
    # T5: write-options explanations load and produce plain-language help for a
    # whitelisted setting (shown on the row + in the confirm popup); unknown -> none.
    from openmbb.safety import WRITE_WHITELIST
    wm = app._write_help_map()
    assert wm, "write_options_help.json should load"
    present = [n for n in WRITE_WHITELIST if n.lower() in wm]
    assert present, "at least one whitelisted setting should have help"
    lines = app._write_help_lines(present[0])
    assert lines and any(("What it does" in l) or ("Caution" in l) for l in lines)
    assert app._write_help_lines("zzz_nope") == []


def test_menu_dialogs_open(app, monkeypatch):
    # T6b: Instructions + Wiring now open the stylized HTML in the browser; patch
    # webbrowser.open so the test doesn't actually launch a browser.
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: opened.append(a) or True)
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
    # Instructions + Wiring opened HTML pages (the other three stay in-app)
    assert len(opened) >= 2


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

    monkeypatch.setattr(app, "_make_port", lambda pn, sim: TwoWakeSim())
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

    def mk(pn, sim):
        holder["p"] = NeverPromptSim()
        return holder["p"]

    monkeypatch.setattr(app, "_make_port", mk)
    app._connect()
    assert _pump(app, lambda: holder.get("p") is not None and holder["p"].closed)
    assert not app.connected


def test_cable_verify_is_the_wizard(app):
    # verification lives in ONE place now: the Connect-tab "Test your cable" button
    # opens the cable wizard (no separate inline listen path).
    from openmbb.gui import VERIFY_LABEL
    assert app.btn_verify.cget("text") == VERIFY_LABEL
    assert not hasattr(app, "btn_listen")          # the old inline-verify button is gone
    assert not hasattr(app, "_listen_only")        # and its method
    app._show_cable_wizard()                       # the wizard builds without a bike
    app.update()
    assert not app._errors


def test_watch_requires_connection_then_runs(app):
    # E1: Watch needs a live connection; once connected it announces + stops cleanly.
    app.watch_var.set(True)
    app._toggle_watch()                              # not connected -> refused
    assert not app.watch_var.get()
    app._connect()
    assert _pump(app, lambda: app.connected)
    app.watch_var.set(True)
    app._toggle_watch()
    assert "WATCH started" in app.txt_out.get("1.0", "end")
    app.watch_var.set(False)
    app._toggle_watch()
    assert "WATCH stopped" in app.txt_out.get("1.0", "end")


def test_the_saved_report_is_the_whole_analysis_and_leaks_nothing(app):
    # E2: the page File -> Save condition report writes. It used to be the health
    # rows re-derived by hand, carrying no pack condition, no charging, no clocks
    # and no verdict - most of what makes it worth handing to anyone.
    from openmbb import sessions
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    s = sessions.load_session(app.logger.dir)
    report = app._build_health_report(s)
    assert "OpenMBB condition report" in report
    for section in ("== Health", "== Verdict =="):
        assert section in report, section
    assert any(tag in report for tag in ("[OK", "[WATCH", "[ALERT", "[INFO"))
    assert "What this is:" in report and "What it is not:" in report
    # no identifiers, and no path either: a saved-session path on Windows carries
    # the account name, which is not a VIN and would otherwise survive every check
    assert "ZEROSIMVIN" not in report and "SIM-MBB" not in report
    assert s.dir not in report and "Path:" not in report


def test_recent_sessions_lists_current(app):
    # E3: sessions are discoverable in-app, most-recent first.
    import os
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._read_cmd("status")
    assert _pump(app, lambda: "### status" in app.txt_out.get("1.0", "end"))
    root, recent = app._recent_sessions()
    assert recent and os.path.basename(app.logger.dir) == recent[0]


def test_health_tree_copies_as_tsv(app):
    # E4: the health table serializes to TSV (headers + rows) for the clipboard.
    from openmbb import sessions
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._analyze_set(sessions.load_session(app.logger.dir))
    app.update()
    tsv = app._tree_as_tsv(app.health_tree)
    assert "\t" in tsv and "Metric" in tsv and "Displayed SOC" in tsv


def test_login_offers_to_remember_typed_password(app, monkeypatch, tmp_path):
    # E5: a working typed password not already known can be remembered (config).
    from openmbb import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".openmbb")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".openmbb" / "config.json")
    monkeypatch.setattr("openmbb.gui.COMMUNITY_PASSWORDS", [])   # so tpsreport is "new"
    from openmbb import dialogs as mb
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: True)    # user opts to remember
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app.login_pw.set("tpsreport")
    app._login_custom()
    assert _pump(app, lambda: app.logged_in)
    assert "tpsreport" in config.get_saved_passwords()


def test_rides_render_honors_unit_preference(app, monkeypatch, tmp_path):
    # E6: with miles selected, the Rides distance column + totals show mi.
    from openmbb import config, parsers
    from openmbb.sim import SimPort
    from openmbb.transport import Transport, SessionLogger
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".openmbb")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".openmbb" / "config.json")
    config.set_units("mi")
    tr = Transport(SimPort(), SessionLogger(base_dir=str(tmp_path), tag="r"))
    recs = parsers.parse_ride_log(tr.exec_command("dumpall"))
    app._render_ride_records(recs, "test")
    assert str(app.ride_tree.heading("km")["text"]) == "Distance mi"
    assert " mi ·" in app.lbl_ride_totals.cget("text")
    assert str(app.ride_tree.heading("socpkm")["text"]) == "SOC%/mi"


def test_ride_temperatures_follow_the_unit_setting_and_gaps_say_n_a(app):
    # the Rides table showed raw Celsius under a fixed "Max pack C" heading
    # whatever was picked in Tools, and printed a missing datum as "None"
    from openmbb import config
    config.set_temp_units("F")
    recs = [{"ts": "t1", "soc": 50, "odo_km": 100.0, "pack_temp_c": 30.0,
             "motor_temp_c": 40.0, "motrpm": 1200},
            {"ts": "t2", "soc": 48, "odo_km": 100.0, "pack_temp_c": None,
             "motor_temp_c": None, "motrpm": None}]      # same odo -> no distance
    app._render_ride_records(recs, "test")
    assert str(app.ride_tree.heading("ptemp")["text"]) == "Max pack F"
    totals = app.lbl_ride_totals.cget("text")
    assert "86 F" in totals                              # the 30 C pack maximum
    assert "None" not in totals and "n/a" in totals      # no distance -> no SOC/km
    row = [str(v) for v in
           app.ride_tree.item(app.ride_tree.get_children()[0])["values"]]
    assert "86" in row and "104" in row and "n/a" in row


def test_tree_tag_colours_follow_a_live_theme_switch(app):
    # tag_configure COPIES the colour into the widget, so unlike the ttk styles
    # sv-ttk restyles for us, tree tags kept whatever palette they were built
    # with: switching to light left INFO rows at the dark palette's near-white
    # #e6e6e6 on a white surface, and the Writes risk column at its palest.
    from openmbb import theme
    app._set_theme("light")
    light = theme.PALETTES["light"]
    assert str(app.health_tree.tag_configure("info", "foreground")) == light["fg"]
    assert str(app.health_tree.tag_configure("alert", "foreground")) == light["danger"]
    assert str(app.tree.tag_configure("safe", "foreground")) == light["green"]
    assert str(app.tree.tag_configure("caution", "foreground")) == light["warn"]
    # the Condition tree is the third one and follows the same way
    assert str(app.cond_tree.tag_configure("measured", "foreground")) == light["fg"]
    assert str(app.cond_tree.tag_configure("attention", "foreground")) == light["warn"]
    assert str(app.cond_tree.tag_configure("unknown", "foreground")) == light["dim"]
    app._set_theme("dark")
    dark = theme.PALETTES["dark"]
    assert str(app.health_tree.tag_configure("info", "foreground")) == dark["fg"]
    assert str(app.tree.tag_configure("safe", "foreground")) == dark["green"]


def test_login_tab_is_concise_read_only(app):
    # D1 (v0.12): the Login intro is trimmed to WHAT logging in does — read-only,
    # reveals the tunables, unlocks Writes — with no password strings listed.
    blob = " ".join(_all_label_text(app)).lower()
    assert "read-only" in blob and "changes nothing on the bike" in blob
    assert "unlocks the writes tab" in blob
    # the actual password strings must NOT be on the login screen
    from openmbb.gui import COMMUNITY_PASSWORDS
    assert all(pw.lower() not in blob for pw in COMMUNITY_PASSWORDS)


def test_writes_tab_scrollbar_trimmed_and_no_redundant_button(app, monkeypatch):
    # v0.12 D2/D3/D4: the Writes tab has a visible scrollbar and a concise UNLOCK
    # line, and no longer duplicates the read-only options button (that lives in the
    # Bike menu). The browser itself still works + defines "live dump".
    import tkinter.ttk as ttk

    def has_scrollbar(w):
        return isinstance(w, ttk.Scrollbar) or any(has_scrollbar(c)
                                                   for c in w.winfo_children())
    writes_tab = app.nb.nametowidget(app.nb.tabs()[3])
    assert has_scrollbar(writes_tab)                              # D3
    labels = " ".join(_all_label_text(app))
    assert "Arming UNLOCK only enables writing" in labels         # D2 trimmed text
    assert "What can I change?" not in labels                     # D4 button removed
    captured = {}
    monkeypatch.setattr(app, "_info_window", lambda t, x: captured.update(text=x))
    app._show_write_options()                                     # Bike-menu path still works
    low = captured["text"].lower()
    assert "live dump" in low and "settings that really exist on your bike" in low


def test_write_confirm_explains_backup_verify_revert(app, monkeypatch):
    # D2: the write-confirm dialog spells out the backup -> verify -> Reset story at
    # the moment of the (nervous) first write.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    from openmbb import dialogs as mb
    seen = []
    monkeypatch.setattr(mb, "askokcancel",
                        lambda title, text=None, **k: seen.append(text) or False)
    app.unlock_var.set(True)
    app._write_value("spfront", "22")
    assert _pump(app, lambda: seen)                    # the confirm fired
    low = seen[-1].lower()
    assert "backup" in low and "verify" in low and "reset" in low


def test_writes_reset_to_default(app, monkeypatch):
    # owner: after changing a setting, its row offers '↺ Reset', which restores the
    # last full read's value through the same safe write flow — no revert journal.
    from openmbb import dialogs as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: "spfront" in app.tree.get_children())
    base = first_number(app._baseline_settings["spfront"])
    app.unlock_var.set(True)
    app._write_value("spfront", "22")
    assert _pump(app, lambda: first_number(app.settings["spfront"]["value"]) == "22")
    assert app._row_differs_from_baseline("spfront")
    assert "Reset" in app.tree.item("spfront", "values")[4]      # inline affordance

    # click the action cell (#5) with no pending -> resets to the last-read value
    monkeypatch.setattr(app.tree, "identify_row", lambda y: "spfront")
    monkeypatch.setattr(app.tree, "identify_column", lambda x: "#5")

    class E:
        x = y = 5
    app._writes_action_click(E())
    assert _pump(app, lambda: first_number(app.settings["spfront"]["value"]) == base)
    assert not app._row_differs_from_baseline("spfront")         # back to default


def test_safe_disconnect_resets_to_clean_slate(app):
    # owner: after Safely disconnect, a reconnect must NOT look pre-pulled — the
    # green command borders, loaded analysis, and last-read snapshot all clear.
    import tkinter as tk
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._read_cmd("status")                   # leave text in the Read console
    assert _pump(app, lambda: "### status" in app.txt_out.get("1.0", "end"))
    app._analyze_use_current()
    assert app.analyze_session is not None
    app._baseline_mark("status", "ok")
    assert str(app.cmd_cells["status"].cget("bg")) == app._cell_color("ok")
    app.baseline_heavy_var.set(True)          # opt-ins / zoom must also re-earn
    app._chart_xzoom = (1.0, 2.0)
    app._cmd_history = ["bms", "status"]      # console history
    app.compare_list = [object()]             # a loaded Compare session
    app._analyze_hint_shown = True
    gwin = app._show_gearing_calc()           # a stray helper window
    assert gwin.winfo_exists()
    mb, fn = app._menubuttons[0]              # an open top-menu, too
    app._menu_popup(mb, fn())
    assert app._open_menu is not None

    app._safe_disconnect()
    app.update()
    assert not app.connected and not app.baseline_done
    assert app._baseline_settings == {}
    assert app.analyze_session is None
    assert str(app.cmd_cells["status"].cget("bg")) == str(app._cell_bg)  # cleared
    assert app.baseline_heavy_var.get() is False
    assert app._chart_xzoom is None
    # the previous session's consoles/history/compare/helper windows are all gone
    assert app.txt_out.get("1.0", "end").strip() == ""
    assert app.txt_probe.get("1.0", "end").strip() == ""
    assert app._cmd_history == [] and app.compare_list == []
    assert app._analyze_hint_shown is False
    assert not [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
    assert app._open_menu is None and app._menu_dismiss_id is None   # menu refs cleared


def test_top_menu_switches_in_one_action_without_grab(app):
    # owner: switching top menus must not need two clicks. No local grab (a grab
    # swallows the sibling-button click); each button's click opens its menu directly
    # (closing any open one), and a re-click toggles it closed.
    app.update()
    (mb_a, fn_a), (mb_b, fn_b) = app._menubuttons[0], app._menubuttons[1]
    app._menu_popup(mb_a, fn_a())
    assert app._open_menu is not None and app._open_menu_anchor is mb_a
    assert app._menu_dismiss_id is not None            # click-to-dismiss armed
    assert not app.grab_current()                      # NO grab held

    app._menu_popup(mb_b, fn_b())                      # switch in one action
    assert app._open_menu is not None and app._open_menu_anchor is mb_b

    # Escape closes it via the ONE main-window binding (not per-popup accumulation)
    app._dismiss_open_menu()
    assert app._open_menu is None and app._menu_dismiss_id is None

    app._menu_popup(mb_a, fn_a())
    app._menu_popup(mb_a, fn_a())                      # re-click toggles closed
    assert app._open_menu is None and app._menu_dismiss_id is None


def test_writes_inline_edit_stages_and_writes(app):
    # owner: editing happens IN the table — a pending value shows in the row + a
    # Write affordance; clicking Write applies it; unchanged clears the pending;
    # the old separate 'New value' input box is gone.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: "spfront" in app.tree.get_children())
    assert not hasattr(app, "newval_var")                 # old input box removed
    app._set_pending_write("spfront", "22")
    assert app._pending_writes.get("spfront") == "22"
    vals = app.tree.item("spfront", "values")
    assert vals[3] == "22" and "Write" in vals[4]         # new value + write affordance
    app._set_pending_write("spfront", "20")               # == current -> pending cleared
    assert "spfront" not in app._pending_writes
    app.unlock_var.set(True)
    app._write_value("spfront", "22")
    assert _pump(app, lambda: first_number(
        app.settings.get("spfront", {}).get("value", "")) == "22")


def test_writes_click_routing_opens_editor_and_writes(app, monkeypatch):
    # owner bug: "i click on an item and nothing lets me enter a value". A single
    # click on the 'New value' cell (#4) must open the inline editor; a double-click
    # anywhere on the row opens it too; clicking 'Write' (#5) applies a pending value.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: "spfront" in app.tree.get_children())

    opened = []
    monkeypatch.setattr(app, "_open_new_editor", lambda row: opened.append(row))
    monkeypatch.setattr(app.tree, "identify_row", lambda y: "spfront")

    class E:
        x = y = 5

    # single click on the New-value column -> opens the editor (no double-click needed)
    monkeypatch.setattr(app.tree, "identify_column", lambda x: "#4")
    assert app._writes_action_click(E()) == "break"
    assert opened == ["spfront"]

    # double-click anywhere on the row (here the Setting column) -> also opens it
    opened.clear()
    monkeypatch.setattr(app.tree, "identify_column", lambda x: "#1")
    assert app._writes_edit_cell(E()) == "break"
    assert opened == ["spfront"]

    # click the Write cell with a pending value staged -> applies it
    written = []
    monkeypatch.setattr(app, "_write_value", lambda n, v: written.append((n, v)))
    app._pending_writes["spfront"] = "23"
    monkeypatch.setattr(app.tree, "identify_column", lambda x: "#5")
    assert app._writes_action_click(E()) == "break"
    assert written == [("spfront", "23")]


def test_analyze_defaults_to_current_session(app):
    # owner: opening the Analyze tab with a live capture auto-loads the current
    # session — no manual 'Use current session' click needed.
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._read_cmd("bms")
    assert _pump(app, lambda: "### bms" in app.txt_out.get("1.0", "end"))
    assert app.analyze_session is None
    app.nb.select(app._ANALYZE_TAB)
    app._on_tab_changed()
    assert app.analyze_session is not None


def test_connect_hides_controls_then_restores_on_failure(app, monkeypatch):
    # owner: after clicking Connect the port/verify/connect controls hide (only the
    # 'Connecting…' line + log show); they come back only if the attempt fails.
    app.sim_var.set(True)
    app._connect()
    # mid-attempt (before the worker finishes) the pre-connect row is hidden
    assert app.connect_row.winfo_manager() == ""
    assert app.connect_busy.winfo_manager() == "pack"
    assert _pump(app, lambda: app.connected)
    # success: controls stay hidden, the success banner shows
    assert app.connect_row.winfo_manager() == ""
    assert app.connect_success.winfo_manager() == "pack"

    # now force a failing connect and confirm the controls are restored for a retry
    from openmbb import gui as _gui
    monkeypatch.setattr(_gui.Transport, "listen",
                        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    app._connect()
    assert _pump(app, lambda: app.connect_row.winfo_manager() == "pack")
    assert app.connect_busy.winfo_manager() == ""
    # the fresh console isn't left blank after the error dialog — a retry line shows
    assert "try again" in app.txt_probe.get("1.0", "end").lower()


def test_connect_button_says_connect(app):
    # owner: the button (and the wizard's) should say "Connect", not "Connect & probe".
    from openmbb.gui import CONNECT_LABEL
    assert CONNECT_LABEL == "Connect"
    assert app.btn_connect.cget("text") == "Connect"


def test_set_has_a_command_button(app):
    # owner: the pull's "[12/15] reading set" step had no button — now it does (in its
    # pull position), so its cell lights green during a pull like every other command.
    assert "set" in app.cmd_btns and "set" in app.cmd_cells
    assert app.cmd_btns["set"].cget("text") == "set"


def test_command_buttons_are_laid_out_in_pull_order(app):
    # owner: the green 'captured' borders should fill row by row (r0c0, r0c1, r1c0,
    # ...) — so the buttons must be gridded in the EXACT order Pull full database
    # runs them (baseline_read_order: reads, set, error log, obd LAST).
    from openmbb.gui import baseline_read_order
    order = baseline_read_order()
    assert order[-1] == "obd"                       # obd runs (and sits) last
    assert order.index("set") < order.index("errorlogdump")
    for i, cmd in enumerate(order):
        assert cmd in app.cmd_cells                 # every pull step has a button
        info = app.cmd_cells[cmd].grid_info()        # ...gridded row-major, in order
        assert int(info["row"]) == i // 2 and int(info["column"]) == i % 2


def test_tooltips_are_dark_themed(app):
    # owner: hover hints were the OS pale-yellow — restyle to the dark panel theme.
    import tkinter as tk
    from openmbb.theme import PALETTE as _P
    btn = app.cmd_btns["status"]
    # drive the tooltip via its stored callable, NOT a synthetic <Enter>: the
    # button lives on the (hidden-behind-landing) Read tab so winfo_viewable()==0,
    # and X11/xvfb with no window manager drops crossing events to non-viewable
    # widgets — event_generate("<Enter>") fires on Windows but not on the Linux CI.
    btn._owl_tip_show()
    app.update()
    tips = [w for w in btn.winfo_children() if isinstance(w, tk.Toplevel)]
    assert tips, "hovering a command shows a tooltip"
    lbl = tips[0].winfo_children()[0]
    assert str(lbl.cget("bg")) == _P["panel"]        # dark surface, not #ffffe0
    assert str(lbl.cget("fg")) == _P["fg"]           # light text
    btn._owl_tip_hide()


def test_connect_page_copy_is_calm_and_current(app):
    # owner: the connect-fail fallback had stale text + orange-warning styling. The
    # help is now muted (not P['warn']) and uses the ACTUAL button labels; the
    # 'Connecting…' line is neutral, not a green success style.
    from openmbb.gui import VERIFY_LABEL, CONNECT_LABEL
    assert str(app.connect_help.cget("style")) == "Muted.TLabel"
    txt = app.connect_help.cget("text")
    assert VERIFY_LABEL in txt and CONNECT_LABEL in txt
    busy_lbl = app.connect_busy.winfo_children()[0]
    assert str(busy_lbl.cget("style")) == "Muted.TLabel"


def test_writes_description_appears_only_on_click(app):
    # owner: remove the default placeholder description — the card is hidden until a
    # setting row is clicked, then it shows that setting's description.
    assert app.effect_panel.winfo_manager() == ""        # hidden at build (no text)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: "spfront" in app.tree.get_children())
    app.tree.selection_set("spfront")
    app._show_effect()
    assert app.effect_panel.winfo_manager() == "pack"    # appears on click
    assert app.effect_card.winfo_children()              # ...with the description
    app._hide_effect()
    assert app.effect_panel.winfo_manager() == ""        # hidden again (reset/disconnect)


def test_writes_description_card_scrolls_and_is_readable(app):
    # owner: (C) two-finger scroll must work over the description card (its dynamic
    # labels get the page wheel binding); (D) it reads as a card — panel-coloured
    # surface, labels on that surface, comfortable body measure/size.
    from openmbb.theme import PALETTE as _P
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: "spfront" in app.tree.get_children())
    app.tree.selection_set("spfront")
    app._show_effect()

    assert str(app.effect_card.cget("bg")) == _P["panel"]           # D: card surface
    labels = [w for w in app.effect_card.winfo_children()
              if w.winfo_class() == "Label"]
    assert labels
    assert all(str(w.cget("bg")) == _P["panel"] for w in labels)    # D: on the card
    # C: every dynamic label carries the page's wheel binding (no scroll dead zone)
    assert all(w.bind("<MouseWheel>") for w in labels)
    # cget("wraplength") returns a str on Windows Tk but a _tkinter.Tcl_Obj on
    # Linux Tk — int() rejects the Tcl_Obj (and `or 0` can't catch it, it's
    # truthy), so normalize via str() first (works on both).
    def _wrap(w):
        return int(str(w.cget("wraplength") or 0))
    bodies = [w for w in labels if _wrap(w) > 0]
    assert bodies and all(_wrap(w) <= 700 for w in bodies)                  # D measure


def test_baseline_heavy_optin_includes_eventlog(app, monkeypatch):
    # owner: an opt-in checkbox folds the heavy event log into Pull full database
    # (informed-consent confirm — askokcancel is True in the fixture).
    app._connect()
    assert _pump(app, lambda: app.connected)
    sent = []
    real = app.transport.exec_command
    monkeypatch.setattr(app.transport, "exec_command",
                        lambda cmd, *a, **k: (sent.append(cmd), real(cmd, *a, **k))[1])
    app.baseline_heavy_var.set(True)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    assert "eventlogdump" in sent                       # heavy dump was included


def test_baseline_without_heavy_notes_not_captured(app):
    # a pull WITHOUT the opt-in tells the user the heavy log wasn't captured (so
    # nobody assumes the ride history is in there)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app.baseline_heavy_var.set(False)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    out = app.txt_out.get("1.0", "end").lower()
    assert "not captured" in out and "eventlogdump" in out


def test_baseline_marks_commands_green(app):
    # each command's status-border cell turns green (ok) once captured in a pull
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app.update()
    green = app._cell_color("ok")
    assert any(str(cell.cget("bg")) == green for cell in app.cmd_cells.values())


def test_watch_flash_pulses_cell(app):
    # Watch pulses its command cell blue each time it fires a read
    app._flash_watch()
    assert str(app.watch_cell.cget("bg")) == "#5aa8ff"


def test_chart_range_filters_by_window(app):
    # the Charts 'Range' selector windows the trend points (newest-relative)
    import datetime as dt
    now = dt.datetime.now().timestamp()
    pts = [(now - 400 * 86400, 1), (now - 100 * 86400, 2), (now - 10 * 86400, 3)]
    app.chart_range.set("All")
    assert len(app._apply_chart_range(pts)) == 3
    app.chart_range.set("Last 30 days")
    assert app._apply_chart_range(pts) == [(now - 10 * 86400, 3)]
    app.chart_range.set("Last 6 mo")
    assert len(app._apply_chart_range(pts)) == 2


def test_sim_trend_synthesizes_year_labeled(app):
    # in simulator mode a trend chart with too few real pulls synthesizes ~a year
    # of history so the user sees the shape — clearly labelled SIMULATED.
    app.sim_var.set(True)
    pts = app._sim_trend_points("Trend: charge cycles", False, "C")
    assert len(pts) >= 20
    assert pts[-1][0] - pts[0][0] > 300 * 86400            # spans ~a year
    assert pts[-1][1] >= pts[0][1]                          # cycles grow toward newest
    app.chart_metric.set("Trend: charge cycles")
    app._render_charts()
    app.update()
    cv = app.chart_canvas
    texts = [cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"]
    assert any("SIMULATED" in t for t in texts)


def test_trends_exclude_sim_and_listen_sessions(app, tmp_path, monkeypatch):
    # owner data-integrity: the real-hardware trend must NOT fold in --sim or
    # cable-test (_listen) folders — they save fake bms/stats that would poison it.
    root = tmp_path / "openmbb-sessions"
    root.mkdir()

    def mkfolder(name, cap):
        d = root / name
        d.mkdir()
        (d / "007_bms.txt").write_text(
            "# command: bms\n\nPack capacity: %s Ah\n" % cap, encoding="utf-8")
    mkfolder("2026-01-01_000000_000000_COM4", 6.4)
    mkfolder("2026-01-02_000000_000000_sim", 9.9)
    mkfolder("2026-01-03_000000_000000_listen", 1.1)
    monkeypatch.setattr(app, "log_dir", str(tmp_path))
    app._trend_cache = None
    names = [n for _mt, n, _b, _s in app._load_trend_sessions()]
    assert any(n.endswith("_COM4") for n in names)
    assert not any(n.endswith(("_sim", "_listen")) for n in names)


def test_trend_note_says_real_pulls(app, monkeypatch):
    # the chart note calls out that a trend is built from REAL pulls (so the
    # sim-exclusion is visible), and is not the SIMULATED preview.
    import datetime as dt
    now = dt.datetime.now().timestamp()
    monkeypatch.setattr(app, "_load_trend_sessions", lambda: [
        (now - 40 * 86400, "a_COM4", {"capacity_ah": 6.5}, {}),
        (now - 5 * 86400, "b_COM4", {"capacity_ah": 6.4}, {})])
    app.sim_var.set(False)
    app.chart_metric.set("Trend: pack capacity")
    app.chart_range.set("All")
    app._render_charts()
    app.update()
    cv = app.chart_canvas
    texts = [cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"]
    assert any("real pull" in t for t in texts)
    assert not any("SIMULATED" in t for t in texts)


def test_compare_renders_diff_and_points_to_charts(app, monkeypatch):
    # v0.18 E: Compare is now the settings-diff; the capacity/gearing trend text
    # blocks moved to the Charts tab ('Trend:' metrics).
    from openmbb import sessions, gui as _gui
    app.compare_list = [sessions.Session("/x/a_COM4", {}, ""),
                        sessions.Session("/x/b_COM4", {}, "")]
    monkeypatch.setattr(_gui.compare_mod, "compare_sessions", lambda ordered: {
        "settings_diff": [("spfront", "20", "22")],
        "capacity_trend": [("a", 6.5), ("b", 6.4)],
        "gearing_trend": [("a", 4.5, "x"), ("b", 4.0, "y")]})
    app._render_compare()
    app.update()
    out = app.txt_compare.get("1.0", "end")
    assert "SETTINGS CHANGED" in out and "spfront" in out
    assert "Charts tab" in out                       # pointer to the trends
    assert "LEARNED PACK CAPACITY" not in out        # moved off Compare
    assert "EFFECTIVE GEARING RATIO" not in out


def test_compare_shows_the_pack_moving_and_names_a_repeat_offender(app, monkeypatch):
    # The one thing a single capture cannot give an owner. Both figures here are
    # comparable across a firmware change - the index is read between fixed pack
    # voltages, the deviation is a cell against its own pack - which is why they
    # are worth a table when the SOC gauge is not.
    from openmbb import sessions, gui as _gui
    app.compare_list = [sessions.Session("/x/jul_COM4", {}, ""),
                        sessions.Session("/x/aug_COM4", {}, "")]
    monkeypatch.setattr(_gui.compare_mod, "compare_sessions", lambda ordered: {
        "settings_diff": [], "capacity_trend": [], "gearing_trend": [],
        "pack_trend": [
            {"session": "jul_COM4", "charge_index_ah": 17.92, "charge_sessions": 4,
             "cell_deviation_mv": 67.1, "loaded_samples": 900,
             "low_cell_mv": 3674.0, "low_cell_index": 28, "soc_pct": 45.0},
            {"session": "aug_COM4", "charge_index_ah": 17.83, "charge_sessions": 3,
             "cell_deviation_mv": 63.6, "loaded_samples": 850,
             "low_cell_mv": 4078.0, "low_cell_index": 28, "soc_pct": 96.0}],
        "weakest_cell": {"cell": 28, "times": 2, "of_captures": 2,
                         "always": True, "graded": False}})
    app._render_compare()
    app.update()
    out = app.txt_compare.get("1.0", "end")
    assert "PACK OVER TIME" in out and "nothing here is graded" in out
    assert "17.92" in out and "3674 mV @ cell 28" in out
    assert "at 96% SOC" in out            # the caveat travels with the reading
    assert "Cell 28 is the weakest reading in 2 of 2 captures" in out
    assert "Charts tab" in out            # dated timelines still live there


def test_compare_survives_a_capture_that_named_no_cell(app, monkeypatch):
    # an older console prints the voltage without attributing it; the row must
    # still render rather than blanking the whole table
    from openmbb import sessions, gui as _gui
    app.compare_list = [sessions.Session("/x/a_COM4", {}, ""),
                        sessions.Session("/x/b_COM4", {}, "")]
    monkeypatch.setattr(_gui.compare_mod, "compare_sessions", lambda ordered: {
        "settings_diff": [], "capacity_trend": [], "gearing_trend": [],
        "pack_trend": [{"session": "a_COM4", "charge_index_ah": None,
                        "charge_sessions": 0, "cell_deviation_mv": None,
                        "loaded_samples": 0, "low_cell_mv": 3798.0,
                        "low_cell_index": None, "soc_pct": None}],
        "weakest_cell": None})
    app._render_compare()
    app.update()
    out = app.txt_compare.get("1.0", "end")
    assert "3798 mV" in out and "@ cell" not in out
    assert "n/a" in out                   # missing reads as missing, not as 0
    assert "is the weakest reading" not in out


def test_gearing_trend_metric_available_and_computes(app, monkeypatch):
    # v0.18 E: an 'effective gearing' Trend metric exists on Charts, derived per
    # session from the odometer (rev/km) with the default wheel circ.
    from openmbb.gui import _trend_gearing_ratio
    assert "Trend: effective gearing" in app._CHART_METRICS
    r = _trend_gearing_ratio({}, {"odo_motor_rev": 13_000_000, "odo_km": 6800})
    assert r is not None and r > 0
    assert _trend_gearing_ratio({}, {"odo_motor_rev": None, "odo_km": None}) is None
    import datetime as dt
    now = dt.datetime.now().timestamp()
    monkeypatch.setattr(app, "_load_trend_sessions", lambda: [
        (now - 40 * 86400, "a_COM4", {}, {"odo_motor_rev": 13_000_000, "odo_km": 6800}),
        (now - 5 * 86400, "b_COM4", {}, {"odo_motor_rev": 13_200_000, "odo_km": 6850})])
    app.sim_var.set(False)
    app.chart_metric.set("Trend: effective gearing")
    app.chart_range.set("All")
    app._chart_xzoom = None
    app._render_charts()
    app.update()
    cv = app.chart_canvas
    texts = [cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"]
    assert any("real pull" in t for t in texts)


def test_chart_drag_sets_xzoom_and_double_click_resets(app):
    # v0.18 D: dragging across the plot sets an x-window in DATA coords (via the
    # transform _chart_line stashes); double-click clears it.
    app._chart_xform = (0.0, 100.0, 60, 460)     # data 0..100 <-> px 60..460
    app._chart_xzoom = None
    app._chart_rubber = None

    class E:
        pass
    app._chart_drag = 160                         # pressed at px 160 -> data 25
    rel = E()
    rel.x = 360                                   # released at px 360 -> data 75
    app._chart_release(rel)
    lo, hi = app._chart_xzoom
    assert abs(lo - 25.0) < 0.01 and abs(hi - 75.0) < 0.01
    app._chart_zoom_reset()
    assert app._chart_xzoom is None


def test_chart_xform_cleared_on_empty_render_so_stale_drag_is_noop(app):
    # v0.18 review (critical/major): every render invalidates _chart_xform; only a
    # successful draw re-stashes it. So zooming into an empty band (or switching to a
    # no-data metric) leaves it None, and a follow-up drag can't invert pixels with a
    # stale transform from the previous chart.
    app._ride_records = [{"odo_km": 100.0, "soc": 90.0},
                         {"odo_km": 110.0, "soc": 80.0}]
    app.chart_metric.set("SOC vs distance")
    app._chart_xzoom = None
    app._render_charts()
    app.update()
    assert app._chart_xform is not None            # a normal render stashes it
    app._chart_xzoom = (500.0, 600.0)              # zoom to an empty band (no points)
    app._render_charts()
    app.update()
    assert app._chart_xform is None                # cleared -> a follow-up drag no-ops
    app._chart_drag = 100

    class E:
        pass
    rel = E()
    rel.x = 300
    app._chart_release(rel)
    assert app._chart_xzoom == (500.0, 600.0)      # release bailed (no stale zoom)


def test_chart_tiny_drag_is_a_click_not_a_zoom(app):
    # a drag under 8 px is a click — it must NOT set a zoom
    app._chart_xform = (0.0, 100.0, 60, 460)
    app._chart_xzoom = None
    app._chart_rubber = None

    class E:
        pass
    app._chart_drag = 200
    rel = E()
    rel.x = 204                                   # 4 px -> click
    app._chart_release(rel)
    assert app._chart_xzoom is None


def test_chart_zoom_out_of_range_shows_reset_hint(app):
    # zooming to an x-window with no points shows the reset hint (not a crash)
    app._ride_records = [{"odo_km": 100.0, "soc": 90.0},
                         {"odo_km": 110.0, "soc": 80.0},
                         {"odo_km": 120.0, "soc": 70.0}]
    app.chart_metric.set("SOC vs distance")
    app._chart_xzoom = None
    app._render_charts()
    app.update()
    app._chart_xzoom = (500.0, 600.0)             # distances here are only 0..20
    app._render_charts()
    app.update()
    cv = app.chart_canvas
    texts = [cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"]
    assert any("zoomed range" in t.lower() for t in texts)


def test_dumpall_tip_explains_it_is_redundant(app):
    # v0.18 C decision: dumpall is NOT folded into the pull (it repeats what the
    # pull + event-log opt-in already capture) — the tip says so instead.
    from openmbb.gui import READ_TIPS
    tip = READ_TIPS["dumpall"].lower()
    assert "rarely needed" in tip and "event log" in tip


def test_read_points_to_analyze_once(app):
    # C1: the first read prints a one-time pointer to Analyze (don't nag every read).
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._read_cmd("status")
    assert _pump(app, lambda: "Use current session" in app.txt_out.get("1.0", "end"))
    before = app.txt_out.get("1.0", "end").count("Use current session")
    app._read_cmd("bms")
    assert _pump(app, lambda: "### bms" in app.txt_out.get("1.0", "end"))
    assert app.txt_out.get("1.0", "end").count("Use current session") == before  # not repeated


def test_health_note_seeded_with_hint(app):
    # C3: the per-row explanations are discoverable (a visible hint, not a blank).
    assert "click any metric row" in app.lbl_health_note.cget("text").lower()


def test_write_options_guards_are_sim_aware(app, monkeypatch):
    # C4: the guards header must not flatly claim "shows —" when the sim shows
    # numbers — it explains the sim fills examples, so sim != bug.
    captured = {}
    monkeypatch.setattr(app, "_info_window", lambda t, x: captured.update(text=x))
    app._show_write_options()
    low = captured["text"].lower()
    assert "simulator" in low and "if your bike exposes" in low


def test_analyze_empty_folder_warns(app, monkeypatch, tmp_path):
    # C6: loading a folder with no session data warns instead of silently n/a-ing.
    from openmbb import dialogs as mb
    from openmbb import sessions
    warns = []
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: warns.append(a))
    app._analyze_set(sessions.Session(str(tmp_path), {}, ""))
    assert warns and "no readable OpenMBB session data" in str(warns[0])
    assert "no readable data" in app.lbl_loaded.cget("text")


def test_locked_writes_tab_explains(app, monkeypatch):
    # C7: "Open Writes tab" / a locked tab must say what unlocks it, not no-op.
    from openmbb import dialogs as mb
    infos = []
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: infos.append(a))
    app._goto_writes()                              # not logged in
    assert infos and "Writes tab opens" in str(infos[-1])


def test_read_tab_has_legend_and_tooltips(app):
    # B6/T2: the dashboard gives plain-language section cues (not a wall of
    # jargon) plus a hover tip for every read.
    from openmbb.gui import READ_TIPS
    from openmbb.transport import READ_COMMANDS, DUMP_COMMANDS, HEAVY_COMMANDS
    labels = " ".join(_all_label_text(app)).lower()
    assert "commands" in labels and "pull full database" in labels
    for cmd in READ_COMMANDS + DUMP_COMMANDS + HEAVY_COMMANDS:
        assert READ_TIPS.get(cmd), "no tooltip for read button %r" % cmd


def test_connect_narrates_probe_steps_live(app):
    # B3: Connect & Probe streams what it's doing into the connect console
    # (version read, firmware check) rather than only moving a progress bar.
    app._connect()
    assert _pump(app, lambda: app.connected)
    log = app.txt_probe.get("1.0", "end").lower()
    assert "reading firmware version" in log
    assert "prompt" in log and "connected" in log


def test_simulator_is_a_toggle_not_a_port(monkeypatch):
    # v0.13: simulator is a toggle (sim_var, on the landing), NOT a port-dropdown
    # entry. Off in real mode; flipping it on makes connect/verify use the sim. The
    # Connect button reads "Connect & Probe" (not the literal "&&").
    import tempfile
    tk = pytest.importorskip("tkinter")
    from openmbb.gui import build_gui, CONNECT_LABEL
    from openmbb.sim import SimPort
    try:
        app = build_gui(sim=False, log_dir=tempfile.mkdtemp(prefix="a1_"))
    except tk.TclError:
        pytest.skip("no display available for Tk")
    try:
        assert hasattr(app, "sim_var") and app.sim_var.get() is False  # off in real mode
        assert not any("SIM" in str(v).upper()                          # not a port entry
                       for v in app.cbo_port.cget("values"))
        assert isinstance(app._make_port("", True), SimPort)           # sim -> SimPort
        assert app.btn_connect.cget("text") == CONNECT_LABEL           # "Connect"
    finally:
        app.destroy()


def test_refresh_ports_reports_when_none_found(app, monkeypatch):
    # A2: a refresh that finds no COM ports must give feedback (not a silent blank
    # list) and point to the simulator toggle at its real location.
    monkeypatch.setattr("openmbb.gui.list_serial_ports", lambda: [])
    app._refresh_ports()
    app.update()
    log = app.txt_probe.get("1.0", "end")
    assert "No COM ports found" in log and "Device Manager" in log
    assert "Simulator mode" in log and "Settings" in log


def test_no_port_selected_error_points_to_simulator(app, monkeypatch):
    # A3: with simulator OFF and no port, the no-port error speaks GUI-language
    # (Refresh + the Tools-menu simulator), never "--sim".
    from openmbb import dialogs as mb
    errs = []
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: errs.append(a))
    app.sim_var.set(False)          # real mode
    app.port_var.set("")
    app._connect()
    app.update()
    assert errs and "explore without a bike" in str(errs[0])
    assert "Simulator mode" in str(errs[0]) and "--sim" not in str(errs[0])


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
    # C4: the Connect-tab guidance must not tell the owner to physically
    # disconnect/reconnect the FTDI Orange (TX) — the harness is a fixed 3-wire
    # cable. (The status bar's "● DISCONNECTED" is a different, legitimate use.)
    guidance = next((t for t in _all_label_text(app)
                     if "test your cable" in t.lower()
                     and "isolation" in t.lower()), None)
    assert guidance is not None                    # sanity: found the Connect blurb
    low = guidance.lower()
    assert "disconnect" not in low
    assert "reconnect" not in low
    assert "orange (tx)" not in low


def test_instructions_text_matches_current_baseline_behavior():
    # the F1 instructions must reflect the CURRENT app: Pull full database excludes
    # heavy dumps; Rides come STRAIGHT OFF THE BIKE via eventlogdump (not the rejected
    # zero-log-parser); Login is a single 'Login' button; writes revert via ↺ Reset.
    from openmbb.gui import INSTRUCTIONS_TEXT
    low = INSTRUCTIONS_TEXT.lower()
    assert "and the ~1 mb log dump" not in low     # the stale baseline claim is gone
    assert "no heavy dumps" in low                 # the pull explicitly excludes them
    assert "eventlogdump" in low and "contactor" in low   # heavy reads are gated + warned
    # retired vocabulary must be gone
    for stale in ("zero-log-parser", "try this password", "try known passwords",
                  "connect & probe", "revert button", "quick reads"):
        assert stale not in low, "stale instruction term: %s" % stale
    assert "pull ride log from bike" in low        # correct Rides source (off the bike)
    assert "reset to restore" in low               # writes revert via inline ↺ Reset


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


def test_login_open_without_baseline_but_writes_gated(app):
    # C1: Login is read-only, so it opens as soon as you're connected — no FULL
    # BASELINE needed. The backup requirement stays where it matters: WRITES.
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert not app.baseline_done
    assert str(app.nb.tab(2, "state")) == "normal"      # Login open pre-baseline
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert str(app.nb.tab(3, "state")) == "disabled"    # Writes still gated (no backup)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    assert str(app.nb.tab(3, "state")) == "normal"      # backup exists -> Writes opens


def test_baseline_incomplete_stays_locked(app, monkeypatch):
    # C6/C17: an empty settings dump must NOT mark a baseline (so Writes stays gated)
    app._connect()
    assert _pump(app, lambda: app.connected)
    orig = app.transport.exec_command

    def blank_set(cmd, *a, **k):
        if cmd == "set":
            return ""
        return orig(cmd, *a, **k)

    monkeypatch.setattr(app.transport, "exec_command", blank_set)
    app._baseline()
    assert _pump(app, lambda: "DATABASE PULL INCOMPLETE"
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
    from openmbb import dialogs as mb
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
    # a session with no event-log riding entries guides the user to pull it from the
    # bike or load a file (no records to show)
    import tempfile
    from openmbb import sessions
    app.analyze_session = sessions.Session(
        tempfile.mkdtemp(), {"dumplogs": "packed binary junk, no riding lines here"}, "")
    app._render_rides()
    app.update()
    low = app.lbl_ride_totals.cget("text").lower()
    assert "pull ride log" in low and "load ride log" in low


def test_rides_parses_session_eventlogdump(app):
    # owner: ride telemetry comes straight off the bike via the console's
    # eventlogdump (decoded text) — no Zero app, no .bin, no external decoder. The
    # Rides tab parses the session's OWN event log, matching the real rev-41 format.
    from openmbb import sessions
    log = (
        " 00489   05/28/2026 11:57:59   Riding   PackTemp: h 29C, l 27C, "
        "PackSOC:100%, Vpack:113.941V, MotAmps: 251, BattAmps: 46, MotTemp: 29C, "
        "CtrlTemp: 22C, MotRPM: 497, Odo: 5369km\n"
        " 00490   05/28/2026 11:58:59   Riding   PackTemp: h 30C, l 27C, "
        "PackSOC: 98%, Vpack:109.560V, MotAmps: 258, BattAmps: 122, MotTemp: 37C, "
        "CtrlTemp: 25C, MotRPM:1569, Odo: 5370km\n")
    app.analyze_session = sessions.Session("x", {"eventlogdump": log}, "")
    app._render_rides()
    app.update()
    assert len(app._ride_records) == 2                 # both riding samples parsed
    assert app.ride_tree.get_children()                # rendered as rows
    assert "event log" in app.lbl_ride_totals.cget("text").lower()  # sourced from it


def test_write_unchanged_readback_reports_clamp(app, monkeypatch):
    # B1: a write that reads back UNCHANGED from the pre-write value is a SILENT
    # CLAMP (the console can reply SUCCESS yet keep the old value — verified live
    # with maxcustspmph capping at 89). The warning must say the value did not
    # stick / is capped, NOT the generic "read-back mismatch … restore …".
    from openmbb import dialogs as mb
    warned = []
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: warned.append(a))
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: len(app.settings) >= 30)
    # make the write a no-op so the read-back stays at the OLD value -> clamp case
    monkeypatch.setattr(app.transport, "write_setting", lambda *a, **k: "no-op")
    app.unlock_var.set(True)
    app._write_value("spfront", "22")
    assert _pump(app, lambda: warned)                        # the clamp warning fired
    msg = str(warned[-1]).lower()
    assert "unchanged" in msg and ("did not stick" in msg or "capped" in msg)
    assert "mismatch" not in msg                             # NOT the generic wording


def test_write_changed_but_wrong_readback_points_to_reset(app, monkeypatch):
    # B1: a read-back that CHANGED but not to what we asked (got != new AND
    # got != old) still gets the generic mismatch wording + ↺ Reset pointer.
    import re
    from openmbb import dialogs as mb
    warned = []
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: warned.append(a))
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)
    assert _pump(app, lambda: len(app.settings) >= 30)
    monkeypatch.setattr(app.transport, "write_setting", lambda *a, **k: "no-op")
    # The write flow reads `set` twice: (1) live old_val, then (2) the verify
    # read. Doctor ONLY the 2nd so old_val stays 20 while the read-back is 99 —
    # a genuine CHANGED-but-wrong result (99 != 22 asked, 99 != 20 old).
    real = app.transport.exec_command
    calls = {"set": 0}

    def doctored(cmd, *a, **k):
        out = real(cmd, *a, **k)
        if cmd == "set":
            calls["set"] += 1
            if calls["set"] >= 2:
                out = "\n".join(
                    re.sub(r"\d+", "99", ln) if ln.strip().startswith("spfront")
                    else ln for ln in out.splitlines())
        return out

    monkeypatch.setattr(app.transport, "exec_command", doctored)
    app.unlock_var.set(True)
    app._write_value("spfront", "22")
    assert _pump(app, lambda: warned)
    msg = str(warned[-1]).lower()
    assert "mismatch" in msg                                 # generic branch
    assert "unchanged" not in msg


def test_connect_while_busy_does_not_reset_state(app):
    # T3: Connect during an in-flight op must be refused BEFORE the destructive
    # reset — the port and the last-read baseline must survive
    app._connect()
    assert _pump(app, lambda: app.connected)
    tr = app.transport
    app._baseline_settings = {"spfront": "20"}              # pretend a prior clean read
    app._busy = True
    app._connect()                                          # must refuse, not reset
    app.update()
    assert app.transport is tr and app.connected            # port not yanked
    assert app._baseline_settings == {"spfront": "20"}      # state not wiped
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
    from openmbb import dialogs as mb
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
    from openmbb import dialogs as mb
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
    from openmbb import dialogs as mb
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

    monkeypatch.setattr(app, "_make_port", lambda pn, sim: GarbledVer())
    app._connect()
    assert _pump(app, lambda: not app._busy)
    assert not app.connected


def test_probe_warns_on_unknown_firmware_rev(app, monkeypatch):
    # T15/C3: a different firmware rev connects but warns
    from openmbb import dialogs as mb
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

    monkeypatch.setattr(app, "_make_port", lambda pn, sim: Rev99())
    app._connect()
    assert _pump(app, lambda: app.connected)      # still connects
    assert warned                                 # but warned about the rev
    assert "99" in app.dash_header.cget("text")   # rev surfaced in the dashboard header


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
    monkeypatch.setattr(app, "_make_port", lambda pn, sim: _login_sim(attempt, level)())
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: not app._busy)
    assert app.logged_in is expect_in


def test_on_close_confirm_journals_and_closes(app, monkeypatch):
    # T18/D3: the accept-close-while-busy path journals a trace and closes
    from openmbb import dialogs as mb
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

    monkeypatch.setattr(app, "_make_port", lambda pn, sim: ClosableSim())
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
    # D4/C8: a glitch AFTER the write but before verify must still leave a PENDING
    # line in the on-disk journal (the bike may have changed)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._baseline()
    assert _pump(app, lambda: app.baseline_done, timeout=120)
    app._login()
    assert _pump(app, lambda: app.logged_in)

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
    app._write_value("spfront", "22")
    assert _pump(app, lambda: os.path.exists(app.transport.logger.journal_path))
    journal = open(app.transport.logger.journal_path, encoding="utf-8").read()
    assert "PENDING" in journal


# ==========================================================================
# v0.20 Tier A — heavy-read BLOCKING progress modal
# ==========================================================================

def test_read_modal_scaffold_grabs_and_is_nonblocking(app):
    # A1: opening the modal sets the interlock flag, RETURNS immediately (no
    # wait_window), and takes an application grab once the window is viewable.
    win = app._show_read_modal("Reading 'eventlogdump' from the bike")
    assert app._read_modal_up is True            # flag set synchronously (non-blocking)
    assert app._read_modal_win is win
    assert win.winfo_exists()
    app.update()                                  # let the grab-retry loop run
    # the SOFTWARE interlock (_read_modal_up) is what blocks I/O and is tested
    # elsewhere; the grab is a best-effort UX layer. Where a grab IS taken it must
    # be THIS window — but xvfb with no window manager legitimately takes none.
    assert app.grab_current() in (win, None)
    app._close_read_modal()
    assert app._read_modal_up is False
    assert not app.grab_current()


def test_read_modal_gate_refuses_silently_and_allows_owned_read(app, monkeypatch):
    # A2-guard: while the modal is up, a stray _run_bg is refused SILENTLY (the
    # "Busy" popup is itself a grab-stealing dialog); the ONE modal-owned launch
    # (allow_during_modal) still runs; Watch self-skips.
    from openmbb import dialogs as mb
    infos = []
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: infos.append(a))
    app._read_modal_up = True
    app._busy = False
    ran = []
    app._run_bg(lambda: ran.append("stray"), None)          # stray op -> refused
    app.update()
    assert not ran and not infos                            # refused AND silent
    app._run_bg(lambda: ran.append("owned"), None, allow_during_modal=True)
    assert _pump(app, lambda: "owned" in ran)               # modal-owned read runs
    # Watch is gated by _read_modal_up (grab does not suppress the after() timer)
    fired = []
    monkeypatch.setattr(app, "_flash_watch", lambda: fired.append(1))
    app.watch_var.set(True)
    app._busy = False
    app._watch_tick()
    assert not fired                                        # skipped while modal up
    app.watch_var.set(False)
    app._read_modal_up = False


def test_heavy_outcome_messages_classify(app):
    # A4: the finalizer's outcome classifier — clean / graceful-NOTE / error.
    assert app._heavy_outcome_msg("plain dump output", None) == \
        "Done — the read completed and is saved."
    graceful = app._heavy_outcome_msg(
        "…\n### NOTE: event log captured (8573 of 8573 entries) …", None)
    assert "resync" in graceful.lower() and "usable" in graceful.lower()
    err = app._heavy_outcome_msg(None, RuntimeError("MBB rebooted"))
    assert "stopped early" in err.lower() and "rebooted" in err


def test_run_bg_error_with_on_error_still_shows_dialog(app):
    # A4 REGRESSION (Review 2): the connect flow passes on_error yet relies on the
    # default showerror. Guarding showerror on `on_error is None` would swallow it.
    # With a caller-supplied on_error and NO suppress flag, showerror MUST fire.
    app._errors.clear()
    seen = []
    app._run_bg(lambda: (_ for _ in ()).throw(RuntimeError("bad cable")),
                on_error=lambda: seen.append(1))
    assert _pump(app, lambda: app._errors)                  # default dialog NOT suppressed
    assert seen                                             # on_error also ran


def test_heavy_read_error_flips_modal_to_continue_no_showerror(app, monkeypatch):
    # A4: a console error DURING a modal heavy read suppresses the default
    # showerror and flips the SAME modal to a single Continue (no double dialog);
    # `then` does NOT run on the error path.
    from openmbb import dialogs as mb
    from openmbb.transport import ConsoleRebootError
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._errors.clear()
    monkeypatch.setattr(app.transport, "exec_command",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ConsoleRebootError("MBB rebooted mid-dump")))
    then_ran = []
    app._read_heavy("eventlogdump", then=lambda: then_ran.append(1))
    assert _pump(app, lambda: app._read_modal_btnrow is not None
                 and app._read_modal_btnrow.winfo_children(), timeout=10)
    assert not app._errors                                  # default showerror suppressed
    assert not then_ran                                     # `then` skipped on error
    app._read_modal_btnrow.winfo_children()[0].invoke()     # Continue
    assert _pump(app, lambda: not app._read_modal_up)
    assert not app.grab_current()


def test_heavy_read_success_runs_then_on_continue(app, monkeypatch):
    # A2/A4: a heavy read raises the modal, and `then` runs only AFTER the user
    # clicks Continue (not before).
    from openmbb import dialogs as mb
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    app._connect()
    assert _pump(app, lambda: app.connected)
    then_ran = []
    app._read_heavy("eventlogdump", then=lambda: then_ran.append(1))
    assert _pump(app, lambda: app._read_modal_btnrow is not None
                 and app._read_modal_btnrow.winfo_children(), timeout=15)
    assert not then_ran                                     # deferred to Continue
    app._read_modal_btnrow.winfo_children()[0].invoke()     # Continue
    assert _pump(app, lambda: then_ran and not app._read_modal_up)


def test_read_modal_teardown_releases_grab(app):
    # A5: the single teardown owner releases the grab, clears the flag, and leaves
    # no stray Toplevel.
    import tkinter as tk
    win = app._show_read_modal("Reading 'eventlogdump' from the bike")
    app.update()                                  # allow the grab where supported
    assert app._read_modal_up and win.winfo_exists()
    app._close_read_modal()
    app.update()
    assert not app._read_modal_up
    assert not app.grab_current()                 # grab released (negative — WM-safe)
    assert not [w for w in app.winfo_children()
                if isinstance(w, tk.Toplevel) and w.winfo_exists()]


def test_menu_and_f1_inert_while_read_modal_up(app, monkeypatch):
    # A6: the custom Menubuttons open with NO grab, and F1 launches a browser — both
    # must be inert while the heavy-read modal is up (grab is the belt, this the suspenders).
    opened, shown = [], []
    monkeypatch.setattr(app, "_menu_popup", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(app, "_show_instructions", lambda *a, **k: shown.append(1))
    mb0 = app._menubuttons[0][0]
    app._read_modal_up = True
    mb0.event_generate("<Button-1>")
    app.event_generate("<F1>")
    app.update()
    # Inert is the load-bearing assertion: with the modal up, nothing opens. On a
    # real WM (Windows local + CI) the synthetic click/key IS delivered and the
    # gate blocks it (discriminating); xvfb with no WM may drop the event (still
    # nothing opens). Either way this holds.
    assert not opened and not shown
    # Positive re-check (modal down → the same click opens a menu) depends on the
    # synthetic click being delivered, which xvfb-no-WM can't guarantee — so it's
    # best-effort, not asserted.
    app._read_modal_up = False
    mb0.event_generate("<Button-1>")
    app.update()
    app._dismiss_open_menu()


def test_heavy_confirm_names_permanent_very_severe_entry(app, monkeypatch):
    # C1: the pre-dump confirm must warn that each dump leaves a PERMANENT
    # "VERY SEVERE" error-log entry (not just the contactor click).
    from openmbb import dialogs as mb
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(a) or False)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._read_heavy("eventlogdump")
    app.update()
    assert asked
    text = str(asked[0]).lower()
    assert "permanent" in text and "very severe" in text


def test_heavy_read_while_busy_is_refused_without_a_stuck_modal(app, monkeypatch):
    # Audit fix: clicking a heavy read while another op is in flight must be
    # refused BEFORE the blocking modal opens — otherwise _run_bg refuses the
    # read behind the modal and the UI is left permanently grab-locked.
    from openmbb import dialogs as mb
    infos = []
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: infos.append(a))
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._busy = True                       # an op is in flight
    app._read_heavy("eventlogdump")
    app.update()
    assert not app._read_modal_up          # NO modal opened
    assert not app.grab_current()          # nothing grab-locked
    assert infos                           # user was told it's busy
    app._busy = False


def test_modal_read_refused_tears_down_the_modal(app, monkeypatch):
    # Audit fix, safety net: if a read slips in and sets _busy AFTER the modal
    # opened (e.g. a Watch tick during the confirm), the modal-owned _run_bg is
    # refused — and the modal must tear itself down, not stay grab-locked.
    from openmbb import dialogs as mb
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: None)
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._show_read_modal("Reading 'eventlogdump' from the bike")
    assert app._read_modal_up
    app._busy = True                       # the race: busy when the read launches
    app._read_cmd("eventlogdump", modal=True)
    app.update()
    assert not app._read_modal_up          # torn down, not stuck
    assert not app.grab_current()
    app._busy = False


def test_the_log_derived_trend_metrics_are_offered_and_plot(app, monkeypatch):
    # These two are the only trend lines that survive a firmware change, which is
    # the whole reason they exist: "Trend: pack capacity" is what the BMS SAYS,
    # and the 2026-06-13 reflash moved that number without touching the pack.
    import time as _time
    assert "Trend: charge index" in app._CHART_METRICS
    assert "Trend: weakest cell deviation" in app._CHART_METRICS
    now = _time.time()
    monkeypatch.setattr(app, "_load_log_trend", lambda: [
        (now - 40 * 86400, "jul_COM4",
         {"charge_index_ah": 17.92, "cell_deviation_mv": 67.1}),
        (now - 5 * 86400, "aug_COM4",
         {"charge_index_ah": 17.83, "cell_deviation_mv": 63.6})])
    app.sim_var.set(False)
    app.chart_range.set("All")
    for metric in ("Trend: charge index", "Trend: weakest cell deviation"):
        app.chart_metric.set(metric)
        app._chart_xzoom = None
        app._render_charts()
        app.update()
        drawn = app.chart_canvas.find_all()
        assert drawn, "%s drew nothing" % metric
        text = " ".join(app.chart_canvas.itemcget(i, "text")
                        for i in drawn
                        if app.chart_canvas.type(i) == "text")
        # a falling line here reads as "my pack is dying" unless the chart says
        # plainly that this is an index
        assert "an index, not capacity" in text
        assert "2 real pulls" in text


def test_a_capture_with_no_event_log_is_skipped_not_plotted_as_zero(app, monkeypatch):
    # a pull taken without '+event log' has nothing to measure; reporting 0 Ah
    # would draw a cliff that never happened
    monkeypatch.setattr(app, "_load_log_trend", lambda: [
        (1.0, "a_COM4", {"charge_index_ah": None, "cell_deviation_mv": None})])
    app.sim_var.set(False)
    app.chart_metric.set("Trend: charge index")
    app.chart_range.set("All")
    app._chart_xzoom = None
    app._render_charts()
    app.update()
    text = " ".join(app.chart_canvas.itemcget(i, "text")
                    for i in app.chart_canvas.find_all()
                    if app.chart_canvas.type(i) == "text")
    assert "No saved sessions with this metric" in text
    assert "+event log" in text          # names the actual cause


def test_the_expensive_log_history_is_read_once_and_only_when_asked(app, monkeypatch):
    # every full pull saves about a megabyte of event log, so building this for
    # 24 captures is not something a chart resize should do
    calls = []
    real = app.__class__._load_log_trend
    monkeypatch.setattr(app.__class__, "_load_log_trend",
                        lambda self: (calls.append(1), real(self))[1])
    app._log_trend_cache = None
    app.sim_var.set(False)
    app.chart_range.set("All")
    app.chart_metric.set("Trend: pack capacity")     # a cheap metric
    app._render_charts()
    app.update()
    assert calls == [], "a bms-derived chart must not touch the event logs"
    app.chart_metric.set("Trend: charge index")
    app._render_charts()
    app._render_charts()
    app.update()
    assert len(calls) == 2                            # asked twice...
    assert app._log_trend_cache is not None            # ...parsed once


# --- the session library -----------------------------------------------------

def _lib_walk(w, out=None):
    out = [] if out is None else out
    out.append(w)
    for c in w.winfo_children():
        _lib_walk(c, out)
    return out


def _lib_when(y, mo, d, h, mi):
    """A local-clock epoch, computed rather than hardcoded — a literal would
    read as a different date in another timezone."""
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def _lib_row(**over):
    row = {"folder": "/x/2026-08-19_160449_675068_COM4",
           "name": "2026-08-19_160449_675068_COM4",
           "when": _lib_when(2026, 8, 19, 16, 4), "odo_km": 7924.0, "soc_pct": 96.0,
           "cycles": 512, "note": "", "has_event_log": True, "is_sim": False,
           "commands": 12, "verdict": None, "verdict_headline": None}
    row.update(over)
    return row


def _open_library(app, monkeypatch, rows, verdict=None):
    """Open the library over `rows` and return (window, treeview)."""
    import tkinter as tk
    from openmbb import gui as _gui
    monkeypatch.setattr(_gui.library_mod, "scan", lambda root, **kw: rows)
    monkeypatch.setattr(_gui.library_mod, "deep_verdict",
                        (lambda folder, **kw: verdict) if not callable(verdict)
                        else verdict)
    before = set(app.winfo_children())
    app._open_session_library()
    app.update()
    win = [w for w in app.winfo_children()
           if isinstance(w, tk.Toplevel) and w not in before][-1]
    tree = [w for w in _lib_walk(win) if w.winfo_class() == "Treeview"][0]
    return win, tree


def test_a_library_row_says_what_the_folder_name_never_could(app):
    # `2026-08-19_160449_675068_COM4` tells an owner nothing about which capture
    # it is; making that folder legible is the point of the whole screen.
    # One test, one app: each `app` builds a fresh Tk root, and this file has
    # enough of them already.
    from openmbb import config
    config.set_units("km")
    when, odo, soc, verdict, note = app._lib_row_values(_lib_row(), None)
    assert "2026-08-19" in when and odo == "7924 km" and soc == "96%" and note == ""

    # the failure mode this column exists to avoid: a blank cell beside a bike
    # you do not know, read as "fine"
    assert verdict == "reading..."
    # a capture taken without '+event log' can never reach a verdict, and says so
    # rather than spinning forever
    assert app._lib_row_values(_lib_row(has_event_log=False), None)[3] == "no log"
    assert app._lib_row_values(_lib_row(), {"level": "watch",
                                            "headline": "x"})[3] == "watch"

    # a note is one row tall whatever was typed into it
    multi = _lib_row(note="before the re-gear\nand after the reflash")
    assert app._lib_row_values(multi, None)[4] == \
        "before the re-gear and after the reflash"

    # missing readings read as missing, not as zero
    blank = app._lib_row_values(_lib_row(odo_km=None, soc_pct=None), None)
    assert blank[1] == "-" and blank[2] == "-"

    config.set_units("mi")
    assert app._lib_row_values(_lib_row(), None)[1] == "4924 mi"
    config.set_units("km")


def test_the_library_lists_captures_and_fills_verdicts_in_afterwards(app,
                                                                     monkeypatch):
    rows = [_lib_row(), _lib_row(name="2026-07-10_124738_435640_COM4",
                                 folder="/x/2026-07-10_124738_435640_COM4",
                                 when=_lib_when(2026, 7, 10, 12, 47), odo_km=6809.0, soc_pct=45.0,
                                 note="before the re-gear")]
    win, tree = _open_library(app, monkeypatch, rows,
                              {"level": "ok", "headline": "fine"})
    vals = [tree.item(i, "values") for i in tree.get_children()]
    assert len(vals) == 2
    assert "2026-08-19" in vals[0][0] and "before the re-gear" in vals[1][4]
    # verdicts arrive on idle ticks after the list is already built - the dialog
    # itself opens without reading a single event log
    for _ in range(8):
        app.update()
    assert [v[3] for v in
            (tree.item(i, "values") for i in tree.get_children())] == ["ok", "ok"]
    win.destroy()


def test_a_verdict_that_will_not_read_says_so_rather_than_passing(app, monkeypatch):
    # the whole column exists to avoid one thing: a cell beside a bike you do
    # not know that reads as "fine" when nothing was actually measured
    def _boom(folder, **_kw):
        raise OSError("event log unreadable")

    rows = [_lib_row(), _lib_row(name="b", folder="/x/b")]
    win, tree = _open_library(app, monkeypatch, rows, _boom)
    assert len(tree.get_children()) == 2          # the dialog opened regardless
    for _ in range(8):
        app.update()
    levels = [tree.item(i, "values")[3] for i in tree.get_children()]
    assert levels == ["unreadable", "unreadable"]
    assert "ok" not in levels
    win.destroy()


def test_a_note_is_written_into_the_capture_folder(app, tmp_path, monkeypatch):
    from openmbb import library
    folder = tmp_path / "2026-08-19_160449_675068_COM4"
    folder.mkdir()
    win, tree = _open_library(app, monkeypatch, [_lib_row(folder=str(folder))])
    ent = [w for w in _lib_walk(win) if w.winfo_class() == "TEntry"][0]
    ent.delete(0, "end")
    ent.insert(0, "after the 2026-06-13 reflash")
    btn = [w for w in _lib_walk(win) if w.winfo_class() == "TButton"
           and str(w.cget("text")) == "Save note"][0]
    btn.invoke()
    app.update()
    # written where the capture is, not into a central file that a copied folder
    # would leave behind
    assert library.read_note(str(folder)) == "after the 2026-06-13 reflash"
    assert tree.item(tree.get_children()[0], "values")[4] == \
        "after the 2026-06-13 reflash"
    win.destroy()


def test_closing_the_library_stops_the_verdict_read(app, monkeypatch):
    # reading verdicts costs a megabyte of event log each; a closed window must
    # not keep chewing through them
    calls = []
    rows = [_lib_row(folder="/x/%d" % i, name="cap%d" % i) for i in range(5)]
    win, _tree = _open_library(app, monkeypatch, rows,
                               lambda folder, **kw: calls.append(folder) or None)
    win.destroy()
    app.update()
    before = len(calls)
    for _ in range(8):
        app.update()
    assert len(calls) == before


def test_a_report_that_withholds_itself_leaves_no_file_behind(app, tmp_path,
                                                              monkeypatch):
    # condition_report refuses to return a page carrying an identifier; the save
    # path must surface that rather than write a partial file
    import tkinter.filedialog as _fd
    from openmbb import gui as _gui, sessions
    monkeypatch.setattr(app, "_session_has_data", lambda s: True)
    app.analyze_session = sessions.Session(str(tmp_path), {"eventlogdump": ""}, "")

    seen = {}

    def _capture(s, **kw):
        seen["kw"] = kw
        return "REPORT BODY"

    monkeypatch.setattr(_gui.report_mod, "condition_report", _capture)
    out = tmp_path / "r.txt"
    monkeypatch.setattr(_fd, "asksaveasfilename", lambda **kw: str(out))
    app._save_health_report()
    assert out.read_text(encoding="utf-8") == "REPORT BODY"
    assert "version" in seen["kw"] and "temp_units" in seen["kw"]

    out2 = tmp_path / "r2.txt"
    monkeypatch.setattr(_fd, "asksaveasfilename", lambda **kw: str(out2))

    def _withhold(s, **kw):
        raise RuntimeError("condition report withheld: it still carries VIN")

    monkeypatch.setattr(_gui.report_mod, "condition_report", _withhold)
    app._save_health_report()
    assert not out2.exists()
    assert any("withheld" in str(a) for a in app._errors)


# --- inspecting a bike you do not know ---------------------------------------

def test_the_inspection_flow_gates_on_parking_and_ticks_from_real_state(app,
                                                                        monkeypatch):
    # The whole point is that a tick means something happened, not that a button
    # was clicked. A flow that believes its own checklist will report a pull that
    # failed halfway as complete.
    win = app._open_inspection()
    app.update()
    rows = app._inspect_rows
    assert len(rows) == len(app._INSPECT_STEPS) == 6

    # nothing is offered until the bike is confirmed parked - the one thing here
    # the app cannot check for itself
    app._inspect_refresh(rows)
    assert not any(app._inspect_done(i) for i in range(6))
    assert app._inspect_ready(0) and not app._inspect_ready(1)
    assert not app._inspect_ready(2)

    app._inspect_parked()
    app._inspect_refresh(rows)
    assert app._inspect_done(0)
    # the cable test is optional, so it must NOT stand between parking and
    # Connect - a flow that insisted on it would be wrong rather than careful
    assert app._inspect_ready(1) and app._inspect_ready(2)
    assert not app._inspect_ready(3)          # not connected yet

    # ...and connecting ticks step 3 without anyone telling the flow it did
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._inspect_refresh(rows)
    assert app._inspect_done(2) and app._inspect_ready(3)
    assert not app._inspect_done(3)           # nothing pulled yet
    win.destroy()


def test_the_inspection_window_survives_the_connect_it_starts(app):
    # every _connect tears the helper windows down, and this window drives
    # Connect from one of its own buttons - so without an exemption the window a
    # user is working through vanishes the instant they use it
    import tkinter as tk
    win = app._open_inspection()
    app.update()
    app._connect()
    assert _pump(app, lambda: app.connected)
    assert win.winfo_exists()
    # ...while the ordinary helper windows still go
    app._show_gearing_calc()
    app.update()
    app._close_transient_toplevels()
    assert win.winfo_exists()
    assert not [w for w in app.winfo_children()
                if isinstance(w, tk.Toplevel) and w is not win]
    win.destroy()


def test_the_event_log_step_hands_off_to_the_gated_read(app, monkeypatch):
    # this is the step that leaves a permanent entry on someone else's bike, so
    # it must go through the existing confirm rather than around it
    called = {}
    monkeypatch.setattr(app, "_read_heavy",
                        lambda cmd, **kw: called.setdefault("cmd", cmd))
    app._inspect_heavy()
    assert called["cmd"] == "eventlogdump"


def test_the_verdict_step_is_reachable_without_the_event_log(app, monkeypatch):
    # a verdict from a capture with no event log is mostly "Not determined", and
    # being shown that is the point - it is not a pass and it is not nothing
    app._inspect_parked()
    app.baseline_done = True
    assert app._inspect_ready(5)
    assert not app._inspect_done(4)
    app._inspect_verdict()
    app.update()
    assert "Analyze" in str(app.nb.tab(app.nb.select(), "text"))
    assert "Condition" in str(app.analyze_nb.tab(app.analyze_nb.select(), "text"))


def test_the_flow_is_offered_in_the_tools_menu(app):
    labels = [e[1] for e in app._tools_menu() if e[0] == "cmd"]
    assert any("Inspect a bike" in str(l) for l in labels)


# --- regressions from the pre-push review ------------------------------------

def test_a_verdict_does_not_outlive_its_session(app):
    # _reset_session_state cleared analyze_session but not _cond_verdict, so
    # after a disconnect the Condition tab kept a bold green "OK" over an empty
    # table - and the inspection flow ticked "Read the verdict" for a bike nobody
    # had connected to yet
    app._connect()
    assert _pump(app, lambda: app.connected)
    app._cond_verdict = {"level": "ok", "headline": "bike A looked fine"}
    app._paint_verdict()
    app.cond_tree.insert("", "end", values=("Charge capacity", "17.8 Ah"))
    app._inspect_confirmed_parked = True
    app._inspect_cable_tested = True

    app._reset_session_state()
    app.update()
    assert app._cond_verdict is None
    assert not app.cond_tree.get_children()
    assert "bike A" not in app.lbl_cond_verdict.cget("text")
    assert app._inspect_done(5) is False
    # the on-trust ticks belong to one session too
    assert app._inspect_done(0) is False and app._inspect_done(1) is False


def test_the_inspection_flow_follows_the_bike_not_the_analyze_tab(app, tmp_path):
    # _inspect_session preferred analyze_session, so a saved capture opened from
    # the session library mid-inspection ticked step 5 with nothing read from the
    # seller's bike and presented THAT bike's verdict as this one's
    from openmbb import sessions
    app._connect()
    assert _pump(app, lambda: app.connected)
    live = app.logger.dir

    other = tmp_path / "someone-elses-bike"
    other.mkdir()
    (other / "001_eventlogdump.txt").write_text(
        "# command: eventlogdump\n# time: 12:00:00.000\n\n" + ("x" * 20000),
        encoding="utf-8")
    app._analyze_set(sessions.load_session(str(other)))
    app.update()

    # the flow reads the live capture, not whatever Analyze happens to hold
    s = app._inspect_session()
    assert s is not None
    assert os.path.normcase(s.dir) == os.path.normcase(live)
    # ...so the other bike's big event log does not tick step 5
    assert app._inspect_done(4) is False
    # ...and its verdict is not this bike's
    app._cond_verdict = {"level": "ok", "headline": "the other bike"}
    assert app._inspect_done(5) is False


def test_the_charts_timeline_is_the_capture_date_not_the_folder_mtime(app, tmp_path,
                                                                     monkeypatch):
    # writing the library's verdict cache into a capture folder bumps its mtime,
    # and the trend metrics plotted each capture AT that mtime - so opening the
    # library once flattened a year of history into one second, newest-first,
    # i.e. plotted in reverse
    import os as _os
    root = tmp_path / "sessions"
    root.mkdir()
    names = ["2026-01-05_120000_111111_COM4", "2026-05-05_120000_222222_COM4",
             "2026-08-05_120000_333333_COM4"]
    for i, n in enumerate(names):
        d = root / n
        d.mkdir()
        (d / "001_bms.txt").write_text(
            "# command: bms\n# time: 12:00:00.000\n\n  - Pack Capacity : %d AH\n"
            % (52 - i), encoding="utf-8")
    # every folder restamped to the same instant, newest-first, exactly as the
    # library's cache write did
    now = 1_800_000_000
    for i, n in enumerate(reversed(names)):
        _os.utime(str(root / n), (now + i, now + i))

    monkeypatch.setattr(app, "_session_root", lambda: str(root))
    app._trend_cache = None
    trend = app._load_trend_sessions()
    stamps = [t for t, _n, _b, _s in trend]
    assert len(stamps) == 3
    assert stamps == sorted(stamps)                       # oldest -> newest
    # and they are months apart, not seconds
    assert stamps[-1] - stamps[0] > 180 * 86400
    assert [n for _t, n, _b, _s in trend] == names        # in capture order


def test_switching_to_miles_reaches_the_condition_tab(app, tmp_path, monkeypatch):
    # Condition carries Consumption and Range in the distance unit, and was the
    # one tab still reading km after a switch to miles
    from openmbb import config, sessions
    calls = []
    monkeypatch.setattr(app, "_render_condition", lambda: calls.append(1))
    app.analyze_session = sessions.Session(str(tmp_path), {"bms": ""}, "")
    app.units_var.set("mi")
    app._apply_units()
    assert calls, "_apply_units never re-rendered Condition"
    config.set_units("km")


def test_the_inspection_window_is_a_singleton(app):
    # each window carries its own 400 ms poll, and they stacked
    a = app._open_inspection()
    app.update()
    b = app._open_inspection()
    app.update()
    assert a is b
    a.destroy()


def test_the_event_log_tick_does_not_reread_a_megabyte_every_poll(app, monkeypatch):
    # the poll ran sessions.load_session() - a whole-folder read including the
    # ~1 MB event log - on the Tk main thread, four times immediately and then
    # every 400 ms
    from openmbb import sessions as _sessions
    app._connect()
    assert _pump(app, lambda: app.connected)
    loads = []
    real = _sessions.load_session
    monkeypatch.setattr(_sessions, "load_session",
                        lambda d: (loads.append(d), real(d))[1])
    for i in range(6):
        app._inspect_done(4)
    assert loads == [], "the event-log tick still loads the whole session"
    # and it answers correctly off the filesystem
    assert app._inspect_done(4) is False
    with open(os.path.join(app.logger.dir, "099_eventlogdump.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("# command: eventlogdump\n\n" + "x" * 20000)
    assert app._inspect_done(4) is True
