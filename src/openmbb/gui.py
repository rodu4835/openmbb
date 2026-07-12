"""Tkinter GUI: four phase-gated panels (Connect / Read / Login / Writes) plus
an always-available Analyze tab (Health / Rides / Compare / Gearing)."""

import datetime as _dt
import difflib
import os
import queue
import re
import subprocess
import sys
import threading
import time


def open_in_file_manager(path):
    """Open a folder in the OS file manager (Windows / macOS / Linux)."""
    if sys.platform == "win32":
        os.startfile(path)                       # noqa: only exists on Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

from . import APP_NAME, __version__
from . import charts as charts_mod
from . import compare as compare_mod
from . import gearing as gearing_mod
from . import health as health_mod
from . import parsers, rides, sessions
from .safety import (READONLY_GUARDS, REV41_FXS_SETTINGS, WRITE_PANEL_CONTEXT,
                     WRITE_WHITELIST, command_blocked)
from .sim import SimPort
from .theme import PALETTE, apply_theme
from .transport import (DUMP_COMMANDS, HEAVY_COMMANDS, LONG_COMMANDS,
                        READ_COMMANDS, ConsoleRebootError, SessionLogger, Transport,
                        first_number, list_serial_ports, looks_like_prompt,
                        nonprintable_ratio, open_real_port, parse_settings_dump)

# Connect-tab / cable-wizard button labels. Cable verification (receive-only) is
# offered via the "Test your cable" wizard; the live connect is a single button.
VERIFY_LABEL = "Test your cable"
CONNECT_LABEL = "Connect & probe"

# Plain-language description of each read command, shown as a hover tooltip so the
# bare firmware button names aren't a wall of jargon to a first-timer.
READ_TIPS = {
    "version": "Firmware + board revision.",
    "help": "The console's own command menu.",
    "status": "Overall bike status right now (mode, temps, SOC, faults).",
    "stats": "Lifetime statistics: odometer, top speed, all-time max temps.",
    "runtime": "Total run time and total charge time.",
    "bms": "Battery pack: cell voltages, balance, capacity, isolation, cycles.",
    "sevcon": "Motor controller (Sevcon) data.",
    "chargers": "Onboard charger port status.",
    "inputs": "Raw sensor inputs (rail voltages, kill switch, kickstand…).",
    "outputs": "Output states (DC/DC, warning light, contactor enable…).",
    "dash": "Instrument-cluster data (clock, odometer, CAN age).",
    "obd": "OBD summary (protocol, DTCs).",
    "errorlogdump": "The small error log (~1 KB, safe).",
    "eventlogdump": "The FULL event log (~1 MB, minutes) — can briefly OPEN the "
                    "drivetrain contactor. Park the bike first.",
    "dumpall": "Everything incl. logs (~1 MB, minutes) — same contactor caveat.",
}


# MBB console login passwords tried by the "Try known passwords" button, in
# order. These are community-reported guesses (unverified per firmware). When a
# password is confirmed to work on a bike, ADD IT HERE so it is tried
# automatically and no one has to type it again.
COMMUNITY_PASSWORDS = ["tpsreport", "wideopenthrottle"]

# Firmware revisions OpenMBB's safety lists, parsers and whitelist were verified
# against. A different rev still connects, but the user is warned before writes.
KNOWN_FIRMWARE_REVS = {41}


def _parse_fw_rev(ver_text):
    """Integer MBB firmware rev from a `version` banner, or None."""
    m = re.search(r"Firmware Rev\s*:?\s*(\d+)", ver_text or "")
    return int(m.group(1)) if m else None


def _looks_like_version(ver_text):
    """True if the text is plausibly an MBB version banner (not empty/garbage)."""
    return bool(re.search(r"MBB|Firmware|Board", ver_text or "", re.I))


INSTRUCTIONS_TEXT = """\
HOW TO USE OPENMBB

The app is staged — each stage unlocks the next. You can stop after any stage;
closing the window never loses data (everything is saved as you go).

CONNECT
  Pick your COM port from the dropdown — or turn on Simulator mode (on the Home
  screen, or Tools → Settings → Connection) to explore the whole tool with no
  bike or cable attached. Optional but recommended first: "Test your cable" — it
  only LISTENS (transmits nothing), so it safely proves your cable + baud and
  that the bike is talking (power the bike during the ~45 s window to catch the
  boot banner). Then "Connect & probe" wakes the console prompt and reads the
  firmware version. Garbage output at 38400 baud usually means the Tx/Rx wires
  are swapped — stop and recheck.

READ
  Click any command button for a one-off read. To advance, click the blue
  Pull full database button: it runs the quick reads + the full settings dump
  (your backup) + the small error log — NO heavy dumps. Individual reads do NOT
  unlock the writes flow — only Pull full database does, so a backup exists
  before any change. The heavy log reads (eventlogdump / dumpall) sit behind
  their OWN buttons and confirm first: on a keyed-on bike a long ~1 MB dump can
  make the BMS briefly OPEN the drivetrain contactor (a click + flashing dash; it
  recovers when the read finishes). They are NOT part of the routine pull.

LOGIN
  Explicit. "Try known passwords" attempts the community-known ones in order;
  or type a specific password and "Try this password" (it is masked in the logs
  and never saved to disk). Both failing is fine — the tool stays read-only.
  Success unlocks Writes. A password is masked in the logs and never written to
  disk unless YOU say yes when it offers to remember it after a successful login
  (clear saved ones via Tools → Settings → Login) — nothing to hand-edit.

WRITES
  Triple-gated: logged in + the master UNLOCK WRITES switch + a per-write
  confirm dialog. Only whitelisted settings that actually exist on your bike
  appear. Each write re-reads the current value, backs up all settings, sends
  the change, reads it back to verify, and journals it (with a Revert button).

ANALYZE (always available, no bike needed)
  Reads a saved session folder (or the current one) and interprets it:
    - Health : SOC vs voltage, cell balance/spread, capacity, temps, cycles,
               odometer, efficiency and the effective gearing ratio, each flagged
               ok / watch / alert.
    - Rides  : per-ride distance, SOC%/km, and temps from a ride log you load
               (.txt) — rev 41 doesn't stream ride telemetry as console text, so
               use a decoded zero-log-parser export.
    - Charts : plot the ride-log time series, or a 'Trend:' metric (capacity,
               cycles, temps) across your saved pulls over time.
    - Compare: pick 2+ sessions to see settings changes and capacity / gearing
               trends over time (battery degradation tracking).

The re-gear GEARING CALCULATOR lives in Tools → Gearing calculator.

TIP: File → "Open session folder" jumps to where everything is saved.
"""

WIRING_TEXT = """\
WIRING — FTDI TTL-232R-3V3  ->  OBD-II / C3 port (under the seat)

  FTDI Black  (GND)  -> OBD pin 5   (Diagnostic ground)
  FTDI Yellow (RXD)  -> OBD pin 8   (bike MBB Tx)
  FTDI Orange (TXD)  -> OBD pin 9   (bike MBB Rx)
  FTDI Red    (+5V)  -> NOT CONNECTED — never

  Serial: 38400 baud, 8-N-1, no flow control, newline CR-LF.

Before connecting: bike PARKED on stand, kill switch OFF. Power the console
either by turning the key ON, OR simply plug in the AC charger (it wakes the MBB
and the console is live for reads — the bike shows Mode: Charging). Confirm the
FTDI Orange line idles ~3.3 V vs Black and the Red lead is taped off.
Note: isolation-resistance reads are only valid OFF the charger.
Never stream the console while riding.
"""

SAFETY_TEXT = """\
SAFETY MODEL

Read-first, with a strong informed-consent gate for anything destructive. Nothing
is hard-blocked — it's your bike — but a dangerous command typed into the raw
command line makes you read what it does, what could happen, and how to recover,
then type "confirm" before it is sent. Commands that go through that gate include:
  format/erase eeprom (bare eeprom is an allowed read; eeprom with arguments is
  gated), settingsrst, statsrst, log clears/adds, reset, exit_to_bl, dtc_clear,
  force_all_storage_mode, blcmds, burn, test, wdt, timing, can, charger,
  sevcon preop, and any "set" of a protected value (abs_disable, bypass_bms,
  ov_* overrides, motstage*/ctrlstage* thermal limits, sevnoregspeed/sevmaxregv/
  sevnoregfull regen guards, model/vin/serial identity).

Input hygiene stays absolute: control characters and multi-line / pasted input
are ALWAYS refused (they could smuggle a second command). See Help → Command
reference for what every command does and its consequences.

The regen and thermal guards are shown READ-ONLY in the Writes tab. Writable
settings there are limited to speedo/gearing, custom-mode speed/torque/regen, and
a few gauge/charge options — each with an effect + risk note and value limits, and
the guided path backs up, verifies and journals every change. Coast regen of
exactly 0 is refused (fishtail risk).
"""


def build_gui(sim=False, preselect_port=None, log_dir=None):
    import tkinter as tk
    from tkinter import filedialog, ttk

    from . import config
    # v0.13/T0.2: themed, centered modal dialogs instead of the OS-grey message
    # boxes. Same API as tkinter.messagebox, so every call site is unchanged.
    from . import dialogs as messagebox

    P = PALETTE

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("%s v%s  —  Zero MBB console (Gen2)" % (APP_NAME, __version__))
            self.geometry("1080x760")
            self.minsize(900, 620)
            self.sty = apply_theme(self)
            self._set_taskbar_app_id()   # before the icon so Windows uses ours
            self._set_window_icon()
            self._apply_dark_titlebar()

            self.transport = None
            self.logger = None
            self.connected = False
            self.baseline_done = False
            self.logged_in = False
            self.version_text = ""
            self.help_logged_out = ""
            self.settings = {}
            self.settings_order = []
            self.journal_entries = []   # (name, old, new)
            self._cmd_history = []      # raw command-line history (↑/↓)
            self._cmd_hist_idx = 0
            self._busy = False
            self.analyze_session = None      # currently loaded Session for analysis
            self.compare_list = []           # [Session, ...] for the Compare panel
            # save base for session folders: explicit arg > saved config > cwd
            self.log_dir = log_dir or config.get_log_dir()
            # thread-safe UI callback queue: workers must never touch Tk
            # directly (Tcl is not thread-safe); they enqueue, main loop pumps
            self._cbq = queue.Queue()
            self.after(40, self._pump_cbq)

            # closing the window must release the serial port (and, once writes
            # exist, guard an in-flight write) — route X and Exit through _on_close
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            # simulator mode toggles on the Home screen + Tools -> Settings ->
            # Connection (not a port-dropdown entry);
            # starts on when launched with --sim.
            self.sim_var = tk.BooleanVar(value=sim)
            self._build_menubar()
            self._build_statusbar()
            self._build_bottom_bar()   # T3: global safe-quit + "done" hub
            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            # C7: a click on a locked tab is silently ignored by Tk — intercept it
            # and say what unlocks that phase instead.
            self.nb.bind("<Button-1>", self._on_tab_click)
            self._build_connect_tab()
            self._build_read_tab()
            self._build_login_tab()
            self._build_write_tab()
            self._build_analyze_tab()
            self._build_console_tab()
            self._apply_gates()
            self._refresh_save_label()
            self._build_landing()      # T1: guided front door over the notebook
            self._show_landing()

        # -- helpers ---------------------------------------------------------
        def _set_taskbar_app_id(self):
            """Give the process an explicit AppUserModelID so Windows shows OUR
            icon (and groups our own button) on the taskbar. Without it, a
            `pythonw` launch inherits Python's default taskbar icon regardless of
            the window icon. Cosmetic + Windows-only; never blocks launch."""
            if sys.platform != "win32":
                return
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "OpenMBB.ZeroMBBConsole")
            except Exception as exc:         # cosmetic only — never block launch
                print("taskbar app id unavailable: %s" % exc)

        def _set_window_icon(self):
            """Title-bar / taskbar / Alt-Tab icon (the gauge-bolt PNGs from
            openmbb/assets; the frozen .exe's own icon comes from the spec)."""
            try:
                from importlib.resources import files
                assets = files("openmbb") / "assets"
                photos = [tk.PhotoImage(data=(assets / ("icon_%d.png" % s)).read_bytes())
                          for s in (16, 32, 48, 64, 256)]
                self.iconphoto(True, *photos)
                self._icon_photos = photos   # Tk keeps no reference; we must
            except Exception as exc:         # cosmetic only — never block launch
                print("window icon unavailable: %s" % exc)

        def _apply_dark_titlebar(self):
            """Make the OS title bar dark to match the app (Windows 10 2004+/11).
            Best-effort and Windows-only — the app theme is always dark. Never
            blocks launch on other platforms or older builds."""
            if sys.platform != "win32":
                return
            try:
                import ctypes
                self.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                value = ctypes.c_int(1)
                # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (build >= 19041); 19 before that
                for attr in (20, 19):
                    if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, attr, ctypes.byref(value),
                            ctypes.sizeof(value)) == 0:
                        break
            except Exception as exc:         # cosmetic only — never block launch
                print("dark title bar unavailable: %s" % exc)

        # -- landing / front door (T1) --------------------------------------
        def _build_landing(self):
            """A guided 'front door' shown over the notebook at startup: a short
            blurb + the two entry actions (verify the cable, or connect & read).
            Calm/modern; hands off into the existing Connect flow."""
            lf = ttk.Frame(self)
            self.landing = lf
            # persistent badge (place()d/forgotten by _refresh_sim_badge) so it
            # tracks the simulator toggle, not just the startup state.
            self._sim_badge = ttk.Label(
                lf, text="SIMULATOR MODE  —  no bike connected",
                style="Accent.TLabel")
            self._refresh_sim_badge()
            inner = ttk.Frame(lf)
            inner.place(relx=0.5, rely=0.44, anchor="center")
            ttk.Label(inner, text=APP_NAME, style="Title.TLabel").pack()
            ttk.Label(inner, text="v%s   ·   Zero MBB console (Gen2)" % __version__,
                      style="Subtitle.TLabel").pack(pady=(2, 0))
            blurb = ("A read-first diagnostics console for Gen2 Zero motorcycles "
                     "(2013-2019).\nIt reads your bike's health, settings and logs "
                     "over a serial cable — and always\nmakes a backup before it "
                     "changes anything.")
            ttk.Label(inner, text=blurb, style="Subtitle.TLabel",
                      justify="center").pack(pady=(18, 28))

            # two entry actions only (owner): Analyze a saved session (no bike), or
            # Connect (verifies the link + connects). Instructions live in Help.
            brow = ttk.Frame(inner)
            brow.pack()
            ttk.Button(brow, text="Analyze", width=20,
                       command=self._open_recent_session
                       ).pack(side="left", padx=10, ipady=8)
            ttk.Button(brow, text="Connect", width=20,
                       style=self.sty["accent"],
                       command=self._landing_connect
                       ).pack(side="left", padx=10, ipady=8)

            foot = ttk.Frame(lf)
            foot.place(relx=0.5, rely=0.9, anchor="center")
            ttk.Label(foot, text="Work PARKED · key on · kill switch off · "
                      "never while riding", style="Muted.TLabel").pack()
            ttk.Checkbutton(foot, text="Simulator mode — no bike or cable needed",
                            variable=self.sim_var, command=self._on_sim_toggle,
                            style=self.sty["toggle"]).pack(pady=(12, 0))

        def _show_landing(self):
            self.nb.pack_forget()
            self.landing.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        def _leave_landing(self, action=None):
            self.landing.pack_forget()
            self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            try:
                self.nb.select(0)                 # Connect tab
            except Exception:
                pass
            self.update_idletasks()
            if action:
                action()

        _PREP_STEPS = ("First make sure:\n"
                       "  • the FTDI cable is plugged into the bike and your PC,\n"
                       "  • your COM port is selected on this screen (click Refresh "
                       "if it's missing),\n"
                       "  • the bike is powered — key ON, or plug in the AC charger.\n\n"
                       "No bike or cable yet? Cancel, then turn on Simulator mode "
                       "(Home screen, or Tools → Settings → Connection).\n\n")

        def _show_cable_wizard(self):
            """Self-contained 'Test your cable' wizard: pick the COM port + read the
            prep info, run the (listen-only) test, then offer Connect & probe /
            Retry / Cancel — no jumping to the Connect tab with its own buttons."""
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current operation.")
                return
            if self.connected:
                messagebox.showinfo(APP_NAME, "Already connected.")
                return
            from . import dialogs
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("Test your cable")
            win.configure(bg=surface)
            win.resizable(False, False)
            win.transient(self)

            body = ttk.Frame(win, padding=24)
            body.pack(fill="both", expand=True)
            ttk.Label(body, text="Test your cable", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(body, text="Listens for the bike — sends nothing, so it's safe "
                      "on any wiring.", style="Muted.TLabel").pack(anchor="w", pady=(1, 12))

            if self.sim_var.get():
                ttk.Label(body, text="◆ Simulator mode is ON — no cable "
                          "or COM port needed.", style="Accent.TLabel").pack(
                              anchor="w", pady=(0, 8))
            else:
                prow = ttk.Frame(body)
                prow.pack(fill="x", pady=(0, 6))
                ttk.Label(prow, text="COM port:").pack(side="left")
                cbo = ttk.Combobox(prow, textvariable=self.port_var,
                                   values=list_serial_ports(), width=18)
                cbo.pack(side="left", padx=8)
                ttk.Button(prow, text="Refresh",
                           command=lambda: cbo.config(values=list_serial_ports())
                           ).pack(side="left")
                ttk.Label(body, text="Plug in the FTDI cable, pick your port above, and "
                          "power the bike — key ON, or plug in the AC charger.",
                          style="Muted.TLabel", wraplength=440, justify="left").pack(
                              anchor="w", pady=(0, 8))

            status = ttk.Label(body, text="Ready when you are.", wraplength=460,
                               justify="left")
            status.pack(anchor="w", pady=(6, 14))
            btns = ttk.Frame(body)
            btns.pack(fill="x")

            def alive():
                try:
                    return bool(win.winfo_exists())
                except Exception:
                    return False

            def set_status(text, color=None):
                if alive():
                    status.config(text=text, foreground=color or P["fg"])

            def set_buttons(specs):
                if not alive():
                    return
                for w in btns.winfo_children():
                    w.destroy()
                for label, cmd, primary in reversed(specs):
                    ttk.Button(btns, text=label,
                               style="Accent.TButton" if primary else "TButton",
                               command=cmd).pack(side="right", padx=(8, 0))

            def close():
                try:
                    win.grab_release()
                except Exception:
                    pass
                win.destroy()

            def on_connect():
                close()
                self._leave_landing()
                self._connect()

            def start_test():
                port_name = self.port_var.get().strip()
                is_sim = self.sim_var.get()
                if not is_sim and not port_name:
                    set_status("Pick a COM port first (Refresh if it's not listed).",
                               P["warn"])
                    return
                self._ensure_log_dir()
                secs = 2 if is_sim else 45
                set_status("Listening… %s" % ("(~2 s, simulator)" if is_sim else
                           "(~45 s — power the bike NOW: key ON or AC charger)"))
                set_buttons([("Cancel", close, False)])

                def job():
                    port = self._make_port(port_name, is_sim)
                    logger = SessionLogger(base_dir=self.log_dir, tag="listen")
                    try:
                        data = Transport(port, logger).listen(secs)
                    finally:
                        try:
                            port.close()
                        except Exception:
                            pass
                    sigs = [s.decode() for s in (b"Zero Motorcycles MBB",
                            b"Reset Source:", b"Checking EEPROM", b"ZERO MBB>")
                            if s in data]
                    return len(data), sigs

                def done(result):
                    if not alive():
                        return
                    nbytes, sigs = result
                    if sigs:
                        set_status("✓ Link verified — the bike is talking (%d bytes: "
                                   "%s)." % (nbytes, ", ".join(sigs)), P["green"])
                        set_buttons([("Cancel", close, False),
                                     ("Retry", start_test, False),
                                     ("Connect & probe", on_connect, True)])
                    else:
                        set_status("✗ No recognizable signal (%d bytes). Check the "
                                   "cable, the COM port, and that the bike is powered, "
                                   "then Retry." % nbytes, P["warn"])
                        set_buttons([("Cancel", close, False),
                                     ("Retry", start_test, True)])

                self._run_bg(job, done)

            set_buttons([("Cancel", close, False), ("Start test", start_test, True)])
            win.protocol("WM_DELETE_WINDOW", close)
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            try:
                win.grab_set()
            except Exception:
                pass

        def _landing_connect(self):
            # already connected (came Home mid-session)? just go back to it — don't
            # tear down a live session by reconnecting.
            if self.connected:
                self._leave_landing()
                self.nb.select(1)          # the Read tab
                return
            # only leave the Home screen once the user has actually committed to
            # connecting — cancelling the confirm must leave them on Home.
            if self.sim_var.get():
                self._leave_landing()
                self._connect()
                return
            if messagebox.askokcancel(
                    "Before you connect",
                    "Connecting wakes the bike's console and reads it.\n\n"
                    + self._PREP_STEPS + "Connect now?"):
                self._leave_landing()
                self._connect()

        # -- consistent page scaffold (owner: make tab pages match the landing) --
        def _tab_header(self, parent, title, subtitle=""):
            """Every page opens with the landing's clean titled header — a heading
            + optional muted subtitle — so the tabs match the first page."""
            ttk.Label(parent, text=title, style="Heading.TLabel").pack(anchor="w")
            if subtitle:
                ttk.Label(parent, text=subtitle, style="Muted.TLabel").pack(
                    anchor="w", pady=(1, 14))
            else:
                ttk.Frame(parent, height=10).pack(anchor="w")

        def _new_tab(self, tab_label, title=None, subtitle=""):
            """A notebook tab with generous padding + an optional titled header."""
            f = ttk.Frame(self.nb, padding=18)
            self.nb.add(f, text=tab_label)
            if title:
                self._tab_header(f, title, subtitle)
            return f

        def _console_text(self, parent, height, fg=None):
            t = tk.Text(parent, height=height, state="disabled",
                        font=(self.sty["mono"], 9), bg=P["console"],
                        fg=fg or P["termfg"], insertbackground=P["fg"],
                        selectbackground=P["sel"],
                        selectforeground="#eafff2",
                        relief="flat", padx=10, pady=8,
                        highlightthickness=1,
                        highlightbackground=P["panel"],
                        highlightcolor=P["panel"])
            self._attach_copy(t)         # E4: right-click Copy on every console
            return t

        def _attach_copy(self, widget):
            """E4: right-click 'Copy selection / Copy all' on a Text widget, so read
            output can be pasted into a forum post or a message."""
            menu = tk.Menu(widget, tearoff=0)

            def copy_all():
                try:
                    txt = widget.get("1.0", "end-1c")
                except Exception:
                    return
                if txt:
                    self.clipboard_clear()
                    self.clipboard_append(txt)

            def copy_sel():
                try:
                    txt = widget.get("sel.first", "sel.last")
                except Exception:
                    txt = ""
                if txt:
                    self.clipboard_clear()
                    self.clipboard_append(txt)
                else:
                    copy_all()

            menu.add_command(label="Copy selection", command=copy_sel)
            menu.add_command(label="Copy all", command=copy_all)

            def popup(e):
                try:
                    menu.tk_popup(e.x_root, e.y_root)
                finally:
                    menu.grab_release()

            widget.bind("<Button-3>", popup)
            widget.bind("<Control-c>", lambda e: copy_sel())

        def _tree_as_tsv(self, tree):
            cols = tree["columns"]
            rows = [[str(tree.heading(c)["text"]) for c in cols]]
            for iid in tree.get_children():
                rows.append([str(v) for v in tree.item(iid, "values")])
            return "\n".join("\t".join(r) for r in rows)

        def _attach_tree_copy(self, tree):
            """E4: right-click 'Copy table' on a Treeview — rows out as TSV text."""
            menu = tk.Menu(tree, tearoff=0)

            def copy_table():
                text = self._tree_as_tsv(tree)
                if text:
                    self.clipboard_clear()
                    self.clipboard_append(text)

            menu.add_command(label="Copy table", command=copy_table)

            def popup(e):
                try:
                    menu.tk_popup(e.x_root, e.y_root)
                finally:
                    menu.grab_release()

            tree.bind("<Button-3>", popup)

        def _pump_cbq(self):
            # D2: one bad callback must NOT kill the pump — if it did, every
            # queued finish() would stop running and _busy would stay True
            # forever, soft-locking the app. Catch per-callback; always reschedule.
            try:
                while True:
                    fn = self._cbq.get_nowait()
                    try:
                        fn()
                    except Exception as exc:
                        try:
                            self._out("[callback error] %s" % exc)
                        except Exception:
                            pass
            except queue.Empty:
                pass
            finally:
                self.after(40, self._pump_cbq)

        def _build_statusbar(self):
            # owner: ONE connection/identity line lives here (the app's single
            # status row) — connection + firmware rev + VIN + login state, kept in
            # sync by _refresh_dash_header. The Read tab no longer repeats it.
            bar = ttk.Frame(self, padding=(8, 6))
            bar.pack(fill="x")
            self.dash_header = ttk.Label(bar, text="", style="Muted.TLabel")
            self.dash_header.pack(side="left")
            self.lbl_sim = ttk.Label(bar, text="", style="Accent.TLabel")
            self.lbl_sim.pack(side="right")

        # -- menu bar --------------------------------------------------------
        def _dismiss_open_menu(self):
            """Tear down the open top-menu popup + any fly-out (used when the main
            window moves — an absolute-positioned popup can't follow it)."""
            for attr in ("_open_submenu", "_open_menu"):
                win = getattr(self, attr, None)
                if win is not None:
                    try:
                        win.destroy()
                    except Exception:
                        pass
                setattr(self, attr, None)

        def _menu_popup(self, anchor, specs):
            """A custom themed dropdown (an overrideredirect Toplevel) so there's no
            native white border and full control over colours / spacing / hover —
            tk.Menu on Windows can't be styled that far. Specs items:
            ("cmd", label, cb) / ("sep",) / ("radio", label, selected, cb) /
            ("submenu", label, subspecs_fn) — a fly-out side menu."""
            existing = getattr(self, "_open_menu", None)
            if existing is not None:
                try:
                    existing.destroy()
                except Exception:
                    pass
            self._open_menu = None
            self._open_submenu = None
            border, menu_bg, hover = "#4a4470", "#1d1d26", "#33384a"

            def close_all():
                # destroy the submenu FIRST, then the root — be explicit rather
                # than relying on Tk to cascade, so no fly-out is left orphaned.
                for attr in ("_open_submenu", "_open_menu"):
                    win = getattr(self, attr, None)
                    if win is not None:
                        try:
                            win.destroy()
                        except Exception:
                            pass
                    setattr(self, attr, None)

            def close_submenu():
                sm = getattr(self, "_open_submenu", None)
                if sm is not None:
                    try:
                        sm.destroy()
                    except Exception:
                        pass
                    self._open_submenu = None

            def open_submenu(row, subspecs_fn):
                cur = getattr(self, "_open_submenu", None)
                if cur is not None and getattr(cur, "_owner_row", None) is row:
                    return                   # already open for this row (no flicker)
                close_submenu()
                sub = render(subspecs_fn(), self._open_menu)   # child of root -> in grab
                sub._owner_row = row
                self._open_submenu = sub
                row.update_idletasks()
                sub.geometry("+%d+%d" % (row.winfo_rootx() + row.winfo_width(),
                                         row.winfo_rooty() - 1))

            def render(items, parent_win):
                win = tk.Toplevel(parent_win)
                win.overrideredirect(True)
                win.configure(bg=border)
                body = tk.Frame(win, bg=menu_bg)
                body.pack(padx=1, pady=1)    # 1px win bg shows as a thin accent edge

                def add_item(label, cb=None, mark="", submenu_fn=None):
                    row = tk.Frame(body, bg=menu_bg)
                    row.pack(fill="x")
                    mk = tk.Label(row, text=mark, bg=menu_bg, fg=P["green"], width=2,
                                  font=(self.sty["ui"], 10))
                    mk.pack(side="left")
                    tx = tk.Label(row, text=label, bg=menu_bg, fg=P["fg"], anchor="w",
                                  padx=6, pady=5, font=(self.sty["ui"], 10))
                    tx.pack(side="left", fill="x", expand=True)
                    ar = tk.Label(row, text=("▸" if submenu_fn else ""), bg=menu_bg,
                                  fg=P["dim"], width=2, font=(self.sty["ui"], 10))
                    ar.pack(side="right")
                    parts = (row, mk, tx, ar)

                    def enter(_e):
                        for p in parts:
                            p.config(bg=hover)
                        if submenu_fn:
                            open_submenu(row, submenu_fn)
                        # NOTE: hovering a plain item does NOT close an open submenu
                        # — otherwise a diagonal move toward the fly-out (crossing a
                        # sibling row) would dismiss it before you reach it. It closes
                        # only on click, Escape, outside-click, or another submenu.

                    def leave(_e):
                        for p in parts:
                            p.config(bg=menu_bg)
                    for w in parts:
                        w.bind("<Enter>", enter)
                        w.bind("<Leave>", leave)
                        if submenu_fn:
                            w.bind("<Button-1>", lambda e, r=row, f=submenu_fn:
                                   (open_submenu(r, f), "break")[-1])
                        else:
                            w.bind("<Button-1>", lambda e, c=cb:
                                   (close_all(), c and c(), "break")[-1])

                for spec in items:
                    if spec[0] == "sep":
                        tk.Frame(body, bg="#39394a", height=1).pack(
                            fill="x", padx=8, pady=4)
                    elif spec[0] == "cmd":
                        add_item(spec[1], cb=spec[2])
                    elif spec[0] == "radio":
                        add_item(spec[1], cb=spec[3], mark="●" if spec[2] else "")
                    elif spec[0] == "submenu":
                        add_item(spec[1], submenu_fn=spec[2])
                win.update_idletasks()
                return win

            root = render(specs, self)
            self._open_menu = root
            root.geometry("+%d+%d" % (anchor.winfo_rootx(),
                                      anchor.winfo_rooty() + anchor.winfo_height()))
            root.bind("<Escape>", lambda e: close_all())
            try:
                root.grab_set()              # so a click anywhere dismisses it
            except Exception:
                pass

            def outside(e):
                if 0 <= e.x < root.winfo_width() and 0 <= e.y < root.winfo_height():
                    return
                w = self.winfo_containing(e.x_root, e.y_root)
                sm = getattr(self, "_open_submenu", None)
                if sm is not None and w is not None and \
                        (str(w) == str(sm) or str(w).startswith(str(sm) + ".")):
                    return                   # click landed inside the open submenu
                close_all()
                fn = getattr(w, "_owl_menu_specs", None)
                if fn is not None:           # clicked another menu button -> switch
                    self._menu_popup(w, fn())
            root.bind("<Button-1>", outside)

        def _build_menubar(self):
            # A themed ttk Menubutton bar with custom popup dropdowns (no native
            # white border). Strip the down-chevron indicator from the menubuttons.
            style = ttk.Style()
            mb_style = "Menubar.TMenubutton"
            try:
                def _drop_indicator(elems):
                    out = []
                    for name, opts in elems:
                        if "indicator" in name.lower():
                            continue
                        opts = dict(opts)
                        if "children" in opts:
                            opts["children"] = _drop_indicator(opts["children"])
                        out.append((name, opts))
                    return out
                style.layout(mb_style, _drop_indicator(style.layout("TMenubutton")))
            except tk.TclError:
                mb_style = "TMenubutton"

            self.units_var = tk.StringVar(value=config.get_units())
            self.temp_units_var = tk.StringVar(value=config.get_temp_units())
            bar = ttk.Frame(self)
            bar.pack(side="top", fill="x")

            def add_menu(text, specs_fn):
                mb = ttk.Menubutton(bar, text=text, style=mb_style)
                mb._owl_menu_specs = specs_fn       # so a click switches menus
                mb.bind("<Button-1>", lambda e, m=mb, f=specs_fn:
                        (self._menu_popup(m, f()), "break")[-1])
                mb.pack(side="left", padx=(4, 0), pady=1)

            add_menu("File", self._file_menu)
            add_menu("Tools", self._tools_menu)
            add_menu("Help", self._help_menu)
            self.bind("<F1>", lambda e: self._show_instructions())
            # an open dropdown is an absolute-positioned popup that can't follow the
            # window; dismiss it when the main window moves/resizes (like a native
            # menu). Only acts while a menu is open, and only for the main window.
            self.bind("<Configure>", lambda e: self._dismiss_open_menu()
                      if (e.widget is self and getattr(self, "_open_menu", None))
                      else None, add="+")

        def _file_menu(self):
            # save location + copy path now live in Tools -> Settings -> Session
            return [
                ("cmd", "Home screen", self._show_landing),
                ("sep",),
                ("cmd", "Save health report…", self._save_health_report),
                ("cmd", "Open recent session…", self._open_recent_session),
                ("cmd", "Open session folder", self._open_session_folder),
                ("sep",),
                ("cmd", "Exit", self._on_close),
            ]

        def _tools_menu(self):
            return [
                ("cmd", "Bike info…", self._show_bike_info),
                ("cmd", "Gearing calculator…", self._show_gearing_calc),
                ("submenu", "COM port:  %s" % self._current_port_label(),
                 self._com_port_menu),
                ("sep",),
                ("cmd", "Settings…", self._show_settings),
            ]

        def _current_port_label(self):
            if self.connected:
                return ("SIMULATOR" if self.sim_var.get()
                        else (self.port_var.get() or "connected"))
            return self.port_var.get() or "(none)"

        def _com_port_menu(self):
            # the fly-out list of COM ports; the OS list is queried fresh each time
            # this opens, so it's always current (Refresh just re-logs on Connect).
            items = []
            if self.connected:
                where = ("SIMULATOR" if self.sim_var.get()
                         else (self.port_var.get() or "?"))
                items += [("cmd", "Connected: %s" % where, None), ("sep",)]
            ports = list_serial_ports()
            cur = self.port_var.get()
            if ports:
                for p in ports:
                    items.append(("radio", p, p == cur,
                                  lambda pp=p: self._select_port(pp)))
            else:
                items.append(("cmd", "(no COM ports found)", None))
            items += [("sep",),
                      ("cmd", "Refresh ports", self._refresh_ports),
                      ("cmd", "Connection settings…",
                       lambda: self._show_settings("Connection"))]
            return items

        def _select_port(self, p):
            """Pick the port for the next Connect (mirrors it into the Connect-tab
            combobox). Doesn't touch a live connection."""
            self.port_var.set(p)
            if hasattr(self, "cbo_port"):
                try:
                    self.cbo_port.set(p)
                except Exception:
                    pass

        def _help_menu(self):
            return [
                ("cmd", "Instructions   (F1)", self._show_instructions),
                ("cmd", "Command reference", self._show_command_reference),
                ("cmd", "Wiring diagram", self._show_wiring),
                ("cmd", "Safety notes", self._show_safety),
                ("sep",),
                ("cmd", "View project on GitHub", self._open_repo),
                ("cmd", "Report an issue…", self._report_issue),
                ("cmd", "About", self._show_about),
            ]

        def _set_units(self, u):
            self.units_var.set(u)
            self._apply_units()

        def _open_url(self, url):
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass

        def _open_repo(self):
            self._open_url("https://github.com/rodu4835/openmbb")

        def _report_issue(self):
            try:
                import webbrowser
                webbrowser.open("https://github.com/rodu4835/openmbb/issues")
            except Exception:
                pass

        def _info_window(self, title, text):
            from . import dialogs
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — %s" % (APP_NAME, title))
            win.geometry("780x580")
            win.configure(bg=surface)
            win.transient(self)

            outer = ttk.Frame(win, padding=(18, 16))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text=title, style="Heading.TLabel").pack(anchor="w",
                                                                      pady=(0, 10))
            body = ttk.Frame(outer)
            body.pack(fill="both", expand=True)
            sb = ttk.Scrollbar(body)
            sb.pack(side="right", fill="y")
            txt = tk.Text(body, wrap="word", font=(self.sty["mono"], 10),
                          bg=P["console"], fg=P["termfg"], relief="flat",
                          padx=16, pady=14, insertbackground=P["fg"],
                          yscrollcommand=sb.set, highlightthickness=1,
                          highlightbackground=P["panel"], highlightcolor=P["panel"])
            txt.pack(side="left", fill="both", expand=True)
            sb.config(command=txt.yview)
            txt.insert("1.0", text)
            txt.config(state="disabled")
            self._attach_copy(txt)       # E4: copyable info dialogs (write options…)
            ttk.Button(outer, text="Close", command=win.destroy).pack(anchor="e",
                                                                      pady=(12, 0))
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            win.focus_set()
            return win

        def _tabbed_info_window(self, title, tabs, active=None):
            """A themed popup with a ttk.Notebook of read-only text tabs. `tabs` is
            a list of (tab_label, text); `active` (a label) selects the first tab
            shown — so one window backs several menu links, each opening its tab."""
            from . import dialogs
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — %s" % (APP_NAME, title))
            win.geometry("760x560")
            win.configure(bg=surface)
            win.transient(self)
            outer = ttk.Frame(win, padding=(16, 14))
            outer.pack(fill="both", expand=True)
            nb = ttk.Notebook(outer)
            nb.pack(fill="both", expand=True)
            for label, text in tabs:
                page = ttk.Frame(nb, padding=(2, 8))
                nb.add(page, text="  %s  " % label)
                body = ttk.Frame(page)
                body.pack(fill="both", expand=True)
                sb = ttk.Scrollbar(body)
                sb.pack(side="right", fill="y")
                txt = tk.Text(body, wrap="word", font=(self.sty["mono"], 10),
                              bg=P["console"], fg=P["termfg"], relief="flat",
                              padx=16, pady=14, insertbackground=P["fg"],
                              yscrollcommand=sb.set, highlightthickness=1,
                              highlightbackground=P["panel"], highlightcolor=P["panel"])
                txt.pack(side="left", fill="both", expand=True)
                sb.config(command=txt.yview)
                txt.insert("1.0", text)
                txt.config(state="disabled")
                self._attach_copy(txt)
                if active and active.lower() == label.lower():
                    nb.select(page)
            ttk.Button(outer, text="Close", command=win.destroy).pack(
                anchor="e", pady=(10, 0))
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            win.focus_set()
            return win

        def _show_write_options(self):
            """D2: a read-only reference of every adjustable (whitelisted) setting
            — what it does, its risk, and its current value if a session is loaded
            — so you can SEE your options WITHOUT logging in or unlocking writes.
            Purely informational: it never sends anything and touches no gate."""
            lines = [
                "WRITE OPTIONS  —  read-only reference",
                "",
                "These are the only settings OpenMBB will ever let you change (the",
                "write whitelist). This view is informational: actually writing still",
                "requires the Writes tab (login + master unlock + a per-write confirm),",
                "and only settings your bike actually reports can be written.",
                "",
                "(\"live dump\" below = the `set` list the console reported after you",
                "logged in — i.e. the settings that really exist on YOUR bike.)",
                "",
                WRITE_PANEL_CONTEXT,
                "",
                "-" * 60,
                "",
            ]
            for name, (label, effect, risk, _v, _w) in WRITE_WHITELIST.items():
                cur = self.settings.get(name, {}).get("value")
                verified = name in REV41_FXS_SETTINGS
                # C3/REG-1: be honest about the "no value yet" states. A verified
                # rev-41 name genuinely appears after login — but ONLY promise that
                # BEFORE login; once logged in, a name still absent won't appear, and
                # a name the verified bike never exposes must never say "read after
                # login" (that implies login reveals it when it won't on this bike).
                if cur:
                    cur_line = cur
                elif verified and not self.logged_in:
                    cur_line = "(read after login)"
                else:
                    cur_line = "(not in the live dump)"
                row = [
                    "● %s  —  %s" % (name, label),
                    "    current: %s" % cur_line,
                    "    risk:    %s" % risk,
                    "    effect:  %s" % effect,
                ]
                if not verified:
                    row.append("    note:    supported on other Gen2 models; NOT seen "
                               "on the verified 2017 FXS rev 41 — may not appear even "
                               "after login.")
                row.append("")
                lines += row
            lines += ["-" * 60, "",
                      "Safety guards (shown read-only, NEVER writable):",
                      "(Sevcon-side / documented thresholds. A value appears only if "
                      "your bike exposes it in `set`; the verified rev 41 usually "
                      "doesn't, so you'll see '—'. In the SIMULATOR they're filled in "
                      "with example values.)"]
            for gname, gdesc in READONLY_GUARDS:
                gval = self.settings.get(gname, {}).get("value")
                lines.append("    %-16s %-10s %s" % (gname, gval or "—", gdesc))
            self._info_window("Write options (read-only)", "\n".join(lines))

        def _session_root(self):
            return os.path.join(self.log_dir or os.getcwd(), "openmbb-sessions")

        def _refresh_save_label(self):
            # the status-bar session label was removed (owner: declutter the row);
            # the save location now lives in File / Tools -> Settings. Kept as a
            # no-op so existing callers don't need to change.
            return

        def _set_log_dir(self):
            chosen = filedialog.askdirectory(
                title="Choose where OpenMBB saves session logs",
                initialdir=self.log_dir or os.getcwd(), mustexist=True)
            if not chosen:
                return
            self.log_dir = os.path.normpath(chosen)
            saved = config.set_log_dir(self.log_dir)
            self._refresh_save_label()
            msg = "Session logs will be saved under:\n%s" % self._session_root()
            if not saved:
                msg += ("\n\n(Couldn't persist the choice to the config file; "
                        "it applies for this run only.)")
            if self.connected:
                msg += ("\n\nThe current session stays where it started — this "
                        "takes effect on your next Connect.")
            messagebox.showinfo(APP_NAME, msg)

        def _open_session_folder(self):
            target = self.logger.dir if self.logger else self._session_root()
            if not os.path.isdir(target):
                messagebox.showinfo(APP_NAME, "That folder doesn't exist yet. "
                                    "Logs will save under:\n%s" % self._session_root())
                return
            try:
                open_in_file_manager(target)
            except Exception as e:
                messagebox.showerror(APP_NAME, "Couldn't open folder:\n%s" % e)

        def _copy_session_path(self):
            path = self.logger.dir if self.logger else self._session_root()
            self.clipboard_clear()
            self.clipboard_append(path)
            messagebox.showinfo(APP_NAME, "Copied to clipboard:\n%s" % path)

        # -- E3: recent sessions ---------------------------------------------
        def _recent_sessions(self, limit=25):
            root = self._session_root()
            try:
                dirs = [d for d in os.listdir(root)
                        if os.path.isdir(os.path.join(root, d))]
            except OSError:
                return root, []
            dirs.sort(key=lambda d: os.path.getmtime(os.path.join(root, d)),
                      reverse=True)
            return root, dirs[:limit]

        def _open_recent_session(self):
            from . import dialogs
            root, recent = self._recent_sessions()
            if not recent:
                messagebox.showinfo(APP_NAME, "No saved sessions yet in:\n%s" % root)
                return
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — Recent sessions" % APP_NAME)
            win.geometry("640x420")
            win.configure(bg=surface)
            win.transient(self)
            outer = ttk.Frame(win, padding=(18, 16))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Recent sessions",
                      style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
            ttk.Label(outer, text=root, style="Muted.TLabel",
                      wraplength=600).pack(anchor="w", pady=(0, 10))
            body = ttk.Frame(outer)
            body.pack(fill="both", expand=True)
            sb = ttk.Scrollbar(body)
            sb.pack(side="right", fill="y")
            lb = tk.Listbox(body, font=(self.sty["mono"], 10),
                            bg=P["console"], fg=P["termfg"], relief="flat",
                            selectbackground=P["sel"], selectforeground=P["fg"],
                            highlightthickness=1, highlightbackground=P["panel"],
                            highlightcolor=P["panel"], activestyle="none",
                            yscrollcommand=sb.set)
            for d in recent:
                lb.insert("end", d)
            lb.pack(side="left", fill="both", expand=True)
            sb.config(command=lb.yview)
            lb.selection_set(0)

            def open_sel(_e=None):
                sel = lb.curselection()
                if not sel:
                    return
                folder = os.path.join(root, recent[sel[0]])
                win.destroy()
                try:
                    self._analyze_set(sessions.load_session(folder))
                    # _select_tab leaves the Home screen first if it's showing —
                    # a bare nb.select() would switch the HIDDEN notebook (owner:
                    # opening a recent session from Home did nothing visible).
                    self._select_tab("Analyze")
                except Exception as e:
                    messagebox.showerror(APP_NAME, "Couldn't load session:\n%s" % e)

            lb.bind("<Double-Button-1>", open_sel)
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=(12, 0))
            ttk.Button(row, text="Open in Analyze", style=self.sty["accent"],
                       command=open_sel).pack(side="right")
            ttk.Button(row, text="Cancel", command=win.destroy).pack(
                side="right", padx=(0, 8))
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            lb.focus_set()

        # -- E5: forget saved passwords --------------------------------------
        def _forget_passwords(self):
            n = len(config.get_saved_passwords())
            if not n:
                messagebox.showinfo(APP_NAME, "No saved login passwords to forget.")
                return
            if messagebox.askyesno(APP_NAME, "Forget %d saved login password(s)?" % n):
                config.clear_saved_passwords()
                messagebox.showinfo(APP_NAME, "Saved login passwords cleared.")

        # -- E6: distance + temperature units --------------------------------
        def _apply_units(self):
            config.set_units(self.units_var.get())
            if self.analyze_session:      # re-render distances in the new unit
                self._render_rides()
            self._render_charts()         # charts read the unit too

        def _apply_temp_units(self):
            config.set_temp_units(self.temp_units_var.get())
            if self.analyze_session:      # re-render temps in the new unit
                self._render_health()
            self._render_charts()

        # -- Settings (tabbed) -----------------------------------------------
        def _show_settings(self, active=None):
            """One tabbed popup for the app's preferences: serial port, display
            units, saved logins, and the session save location (owner: gather the
            scattered Tools items into a Settings dialog)."""
            from . import dialogs
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — Settings" % APP_NAME)
            win.geometry("620x470")
            win.configure(bg=surface)
            win.transient(self)
            outer = ttk.Frame(win, padding=(16, 14))
            outer.pack(fill="both", expand=True)
            nb = ttk.Notebook(outer)
            nb.pack(fill="both", expand=True)
            pages = {}

            def page(label):
                p = ttk.Frame(nb, padding=(14, 12))
                nb.add(p, text="  %s  " % label)
                pages[label] = p
                return p

            # --- Connection ---
            conn = page("Connection")
            ttk.Label(conn, text="Serial port", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(conn, text="Pick the COM port used on the next Connect. In "
                      "Simulator mode the port is ignored.", style="Muted.TLabel",
                      wraplength=560).pack(anchor="w", pady=(0, 8))
            prow = ttk.Frame(conn)
            prow.pack(fill="x")
            ttk.Label(prow, text="Port:").pack(side="left")
            cbo = ttk.Combobox(prow, textvariable=self.port_var,
                               values=list_serial_ports(), width=24)
            cbo.pack(side="left", padx=6)
            conn_status = ttk.Label(conn, text="", style="Muted.TLabel", wraplength=560)

            def _refresh_settings_ports():
                ports = list_serial_ports()
                cbo.config(values=ports)
                conn_status.config(text=("Found: %s" % ", ".join(ports)) if ports
                                   else "No COM ports found — plug in the FTDI cable "
                                        "and Refresh (or use Simulator mode).")
            ttk.Button(prow, text="Refresh", command=_refresh_settings_ports).pack(side="left")
            conn_status.pack(anchor="w", pady=(8, 0))
            if self.connected:
                where = "SIMULATOR" if self.sim_var.get() else self.port_var.get()
                ttk.Label(conn, text="Connected now to %s — a change here applies on "
                          "your next Connect." % where, style="Muted.TLabel",
                          wraplength=560).pack(anchor="w", pady=(8, 0))
            # simulator toggle — also on the Home screen, but reachable here once the
            # Home screen is dismissed (owner: it must not become unreachable).
            ttk.Separator(conn).pack(fill="x", pady=12)
            ttk.Checkbutton(conn, text="Simulator mode — explore with no bike or cable",
                            variable=self.sim_var, command=self._on_sim_toggle,
                            style=self.sty["toggle"]).pack(anchor="w")
            ttk.Label(conn, text="When on, Verify & Connect use a built-in simulator "
                      "and the COM port is ignored.", style="Muted.TLabel",
                      wraplength=560).pack(anchor="w", pady=(2, 0))

            # --- Units ---
            units = page("Units")
            ttk.Label(units, text="Distance", style="Heading.TLabel").pack(anchor="w")
            for lab, val in (("Kilometers (km)", "km"), ("Miles (mi)", "mi")):
                ttk.Radiobutton(units, text=lab, value=val, variable=self.units_var,
                                command=self._apply_units).pack(anchor="w")
            ttk.Separator(units).pack(fill="x", pady=12)
            ttk.Label(units, text="Temperature", style="Heading.TLabel").pack(anchor="w")
            for lab, val in (("Celsius (°C)", "C"), ("Fahrenheit (°F)", "F")):
                ttk.Radiobutton(units, text=lab, value=val,
                                variable=self.temp_units_var,
                                command=self._apply_temp_units).pack(anchor="w")

            # --- Login ---
            login = page("Login")
            ttk.Label(login, text="Saved login passwords",
                      style="Heading.TLabel").pack(anchor="w")
            lbl_pw = ttk.Label(login, style="Muted.TLabel", wraplength=560)
            lbl_pw.pack(anchor="w", pady=(0, 8))

            def _refresh_pw():
                c = len(config.get_saved_passwords())
                lbl_pw.config(text=("%d saved password(s) — remembered so 'Try known "
                                    "passwords' can reuse them." % c) if c
                              else "No saved passwords. After a successful login you "
                                   "can choose to remember it.")
            _refresh_pw()

            def _forget_and_refresh():
                self._forget_passwords()
                _refresh_pw()
            ttk.Button(login, text="Forget saved passwords",
                       command=_forget_and_refresh).pack(anchor="w")

            # --- Session ---
            sess = page("Session")
            ttk.Label(sess, text="Session save location",
                      style="Heading.TLabel").pack(anchor="w")
            ttk.Label(sess, text=self._session_root(), style="Muted.TLabel",
                      wraplength=560).pack(anchor="w", pady=(0, 8))
            srow = ttk.Frame(sess)
            srow.pack(anchor="w")
            ttk.Button(srow, text="Change…",
                       command=self._set_log_dir).pack(side="left")
            ttk.Button(srow, text="Open folder",
                       command=self._open_session_folder).pack(side="left", padx=6)
            ttk.Button(srow, text="Copy path",
                       command=self._copy_session_path).pack(side="left")

            ttk.Button(outer, text="Close", command=win.destroy).pack(
                anchor="e", pady=(10, 0))
            if active and active in pages:
                nb.select(pages[active])
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            win.focus_set()
            return win

        # -- E2: health report -----------------------------------------------
        def _build_health_report(self, s):
            st, _ = parse_settings_dump(s.settings_text or "")
            lines = ["OpenMBB health report",
                     "generated by OpenMBB v%s" % __version__,
                     "session: %s" % s.name, ""]
            lines.append("== Bike ==")
            for key, lab in (("model", "Model"), ("model_year", "Model year"),
                             ("firmware_rev", "Firmware rev"), ("board_id", "Board ID")):
                v = st.get(key, {}).get("value")
                if v:
                    lines.append("  %-14s %s" % (lab, v))
            # NOTE: VIN/serial are deliberately omitted so a shared report leaks no IDs.
            lines += ["", "== Health (ok / watch / alert) =="]
            for m in health_mod.health_snapshot(s, config.get_temp_units()):
                lines.append("  [%-5s] %-26s %s" % (m["status"].upper(),
                                                    m["label"], m["value"]))
                if m["note"]:
                    lines.append("           %s" % m["note"])
            lines.append("")
            return "\n".join(lines)

        def _save_health_report(self):
            s = self.analyze_session
            if s is None and self.logger:
                try:
                    s = sessions.load_session(self.logger.dir)
                except Exception:
                    s = None
            if not self._session_has_data(s):
                messagebox.showinfo(APP_NAME, "No session data to report yet — run "
                    "Pull full database (or load a session on the Analyze tab) first.")
                return
            path = filedialog.asksaveasfilename(
                title="Save health report", defaultextension=".txt",
                initialfile="openmbb-health-report.txt",
                filetypes=[("Text file", "*.txt"), ("All files", "*.*")])
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self._build_health_report(s))
                messagebox.showinfo(APP_NAME, "Health report saved to:\n%s" % path)
            except OSError as e:
                messagebox.showerror(APP_NAME, "Couldn't save the report:\n%s" % e)

        def _bike_facts(self):
            facts = []
            if self.version_text:
                for pat, label in [
                        (r"Firmware Rev\s*:?\s*(\w+)", "MBB firmware rev"),
                        (r"Board Rev\s*:?\s*(\w+)", "Board rev"),
                        (r"Firmware Built\s*:?\s*(.+)", "Firmware built")]:
                    mm = re.search(pat, self.version_text)
                    if mm:
                        facts.append((label, mm.group(1).strip()))
            for key, label in [("model", "Model"), ("model_year", "Model year"),
                               ("vin", "VIN"), ("serial", "Board serial"),
                               ("spfront", "Front sprocket"),
                               ("sprear", "Rear sprocket"),
                               ("rwhcirc", "Rear wheel circ")]:
                if key in self.settings:
                    facts.append((label, self.settings[key]["value"]))
            return facts

        def _bike_info_text(self):
            lines = ["BIKE INFO", ""]
            facts = self._bike_facts()
            if facts:
                for k, v in facts:
                    lines.append("  %-18s : %s" % (k, v))
            elif not self.connected:
                lines.append("Not connected. Connect on the Connect tab, then run")
                lines.append("Pull full database for model / serial / gearing details.")
            else:
                lines.append("Connected, but no data pulled yet — click Pull full")
                lines.append("database on the Read tab for model / gearing / serials.")
            lines += ["", "Login level :", "  %s" % ("logged in"
                      if self.logged_in else "not logged in")]
            lines += ["", "Session folder:",
                      "  %s" % (self.logger.dir if self.logger else "(none yet)")]
            return "\n".join(lines)

        def _bike_about_window(self, active):
            # owner: Bike info + About share one two-tab popup; each menu link opens
            # its own tab.
            return self._tabbed_info_window(
                "Bike info & About",
                [("Bike info", self._bike_info_text()),
                 ("About", self._about_text())],
                active)

        def _show_bike_info(self):
            self._bike_about_window("Bike info")

        def _open_html_help(self, filename, fallback_title, fallback_text, anchor=""):
            """Open a stylized, self-contained HTML help page in the browser (all the
            help pages are tabs in one info.html; `anchor` selects the tab via
            #hash). Reads the packaged asset bytes to a stable temp file (dev + the
            frozen build), then launches the browser. Falls back to the in-app text
            window if anything goes wrong."""
            try:
                import webbrowser, tempfile, os, pathlib
                from importlib.resources import files
                text = (files("openmbb") / "assets" / filename).read_text(encoding="utf-8")
                name = "openmbb_" + filename
                if anchor:
                    # bake the target tab into a per-tab temp file so it doesn't rely
                    # on the browser honouring a #fragment when it reuses an open tab.
                    text = text.replace(
                        "<!--TABINJECT-->",
                        "<script>window.OPENMBB_TAB=%r;</script>" % anchor)
                    stem, _, ext = filename.rpartition(".")
                    name = "openmbb_%s_%s.%s" % (stem or filename, anchor, ext or "html")
                tmp = os.path.join(tempfile.gettempdir(), name)
                with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                uri = pathlib.Path(tmp).as_uri() + (("#" + anchor) if anchor else "")
                if webbrowser.open(uri):
                    return
            except Exception:
                pass
            self._info_window(fallback_title, fallback_text)

        def _show_instructions(self, _evt=None):
            self._open_html_help("info.html", "Instructions", INSTRUCTIONS_TEXT,
                                 "instructions")

        def _show_wiring(self):
            self._open_html_help("info.html", "Wiring", WIRING_TEXT, "wiring")

        def _show_safety(self):
            self._open_html_help("info.html", "Safety notes", SAFETY_TEXT, "safety")

        def _about_text(self):
            import sys
            return (
                "%s  v%s\n\n"
                "Serial console & diagnostics for Gen2 MBB-based Zero\n"
                "electric motorcycles (~2013-2019: S/SR/DS/DSR/FX/FXS/FXE).\n"
                "Verified on the 2017 FXS (MBB rev 41).\n\n"
                "  Theme backend : %s\n"
                "  Python        : %s\n"
                "  Repo          : github.com/rodu4835/openmbb\n"
                "  License       : MIT (no warranty)\n\n"
                "Personal diagnostic tool for your own vehicle. Not affiliated\n"
                "with Zero Motorcycles."
                % (APP_NAME, __version__, self.sty.get("backend"),
                   sys.version.split()[0]))

        def _show_about(self):
            self._bike_about_window("About")

        def _apply_gates(self):
            # C1: Login is READ-ONLY (it only reveals the tunable settings), so it
            # opens as soon as you're connected — no FULL BASELINE required. The one
            # hard safety rule lives on WRITES: a settings backup (FULL BASELINE)
            # AND a login must both exist before anything can be written.
            states = [
                "normal",                                          # Connect
                "normal" if self.connected else "disabled",        # Read
                "normal" if self.connected else "disabled",        # Login (read-only)
                "normal" if (self.connected and self.logged_in and self.baseline_done)
                else "disabled",                                   # Writes (needs backup)
            ]
            for i, st in enumerate(states):
                self.nb.tab(i, state=st)
            self._refresh_dash_header()      # the single status line (conn/rev/login)
            self._refresh_action_buttons()   # T3: gate the action bar on connect/busy

        def _tab_unlock_hint(self, idx):
            # C7: plain-language "here's how to unlock this stage" for a locked tab.
            if idx in (1, 2):     # Read + Login both just need a connection
                return ("The %s tab opens once you Connect & probe on the Connect tab."
                        % ("Read" if idx == 1 else "Login"))
            if idx == 3:
                if not self.connected:
                    return ("The Writes tab opens once you're connected, logged in, and "
                            "have run Pull full database (a backup must exist before "
                            "any write).")
                need = []
                if not self.logged_in:
                    need.append("log in on the Login tab")
                if not self.baseline_done:
                    need.append("run Pull full database on the Read tab (saves a backup)")
                return "The Writes tab opens once you " + " and ".join(need) + "."
            return "That tab isn't available yet — finish the earlier stage first."

        def _on_tab_click(self, event):
            try:
                idx = self.nb.index("@%d,%d" % (event.x, event.y))
                disabled = str(self.nb.tab(idx, "state")) == "disabled"
            except Exception:
                return
            if disabled:
                messagebox.showinfo(APP_NAME, self._tab_unlock_hint(idx))

        def _goto_writes(self):
            if self.connected and self.baseline_done and self.logged_in:
                self.nb.select(3)
            else:
                messagebox.showinfo(APP_NAME, self._tab_unlock_hint(3))

        def _reset_session_state(self):
            """Every connection re-earns its phases. Drop all prior phase state so
            a --sim rehearsal (or a bike that was key-cycled) can't carry a stale
            login / baseline / settings into a fresh, real connection."""
            try:
                if self.transport:
                    self.transport.close()
            except Exception:
                pass
            self.transport = None
            self.logger = None
            self.connected = False
            self.baseline_done = False
            self.logged_in = False
            self.version_text = ""
            self.help_logged_out = ""
            self.settings = {}
            self.settings_order = []
            self.journal_entries = []
            if hasattr(self, "unlock_var"):
                self.unlock_var.set(False)
            if hasattr(self, "lst_journal"):
                self.lst_journal.delete(0, "end")
            self._hide_connect_success()     # a new/broken session re-earns it
            self._set_login_status(False)
            if hasattr(self, "lbl_prog"):
                self.lbl_prog.config(text="", foreground=P["dim"])
            self._refresh_write_rows()
            self._apply_gates()          # also refreshes the dashboard header

        def _on_close(self):
            """Window X / File→Exit: guard an in-flight operation, then release
            the serial port before quitting."""
            if self._busy:
                if not messagebox.askokcancel(APP_NAME,
                        "A serial operation — possibly a WRITE — is still running. "
                        "Closing now could interrupt it between send and verify. "
                        "Close anyway?"):
                    return
                try:            # leave a trace in case a write was mid-flight
                    if self.logger:
                        self.logger.journal_write("(app closed while busy)",
                                                  "-", "-", False)
                except Exception:
                    pass
            try:
                if self.transport:
                    self.transport.close()
            except Exception:
                pass
            self.destroy()

        # -- global safe-quit bar --------------------------------------------
        def _build_bottom_bar(self):
            bar = ttk.Frame(self, padding=(8, 6))
            bar.pack(side="bottom", fill="x")
            # T3: created here but HIDDEN until usable (owner: not visible until you
            # actually connect) — _refresh_action_buttons packs/forgets it. (The pull
            # confirmation lives under the Pull-full-database button now, not here.)
            self.btn_safequit = ttk.Button(bar, text="Safely disconnect",
                                            command=self._safe_disconnect)
            self._refresh_action_buttons()   # hidden until connected + idle

        def _set_busy(self, flag):
            """Single busy toggle; refresh the action-bar button state."""
            self._busy = flag
            self._refresh_action_buttons()

        def _refresh_action_buttons(self):
            """Show 'Safely disconnect' only when CONNECTED and not
            mid-operation — HIDDEN otherwise (not visible until you connect; gone
            during a write / heavy dump / pull that's unsafe to interrupt)."""
            sq = getattr(self, "btn_safequit", None)
            if sq is None:
                return
            sq.pack_forget()
            if self.connected and not self._busy:
                sq.pack(side="right")

        def _select_tab(self, needle):
            if getattr(self, "landing", None) is not None \
                    and self.landing.winfo_manager() == "pack":
                self._leave_landing()
            for tid in self.nb.tabs():
                if needle.lower() in str(self.nb.tab(tid, "text")).lower():
                    self.nb.select(tid)
                    return

        def _safe_disconnect(self):
            """'Safely disconnect' — never mid-operation. Release the port, tell the
            user it's safe to unplug, then return to the Home screen (does NOT quit;
            use the window X / File → Exit to close the app)."""
            if self._busy:
                messagebox.showinfo(APP_NAME, "An operation is still running — "
                                    "wait for it to finish before disconnecting.")
                return
            where = self.logger.dir if self.logger else None
            self._reset_session_state()      # closes the port, drops state, re-gates
            messagebox.showinfo("Disconnected — safe to unplug",
                                "Disconnected from the bike.\n\nIt is now safe to "
                                "unplug the cable and power off the motorcycle."
                                + (("\n\nSession saved to:\n%s" % where)
                                   if where else ""))
            self._show_landing()             # back to the Home screen

        def _start_writes_flow(self):
            """Get the user to Writes doing only the MISSING prerequisites, in
            order, with no extra clicks. Writes needs BOTH a saved backup (Pull
            full database) AND a login — offering a bare 'log in' led to a dead end
            when no backup existed yet (owner: the flow was wrong)."""
            if not self.connected:      # reachable from the offline Gearing tab
                messagebox.showinfo(APP_NAME, "Connect to your bike first — changing a "
                                    "setting needs a live session (a backup + login).")
                return
            if self.connected and self.logged_in and self.baseline_done:
                self._select_tab("Writes")
                return
            if not self.baseline_done:
                steps = "run Pull full database (saves a backup)"
                if not self.logged_in:
                    steps += ", then log you in"
                if not messagebox.askokcancel(
                        APP_NAME,
                        "The Writes tab needs a saved backup before anything can be "
                        "changed.\n\nI'll %s, then open Writes. This only reads the "
                        "bike — nothing is written.\n\nContinue?" % steps):
                    self._select_tab("Read")
                    return
                self._select_tab("Read")
                self._baseline(then=lambda ok: ok and self._continue_writes_login())
            else:
                self._continue_writes_login()

        def _continue_writes_login(self):
            """Second half of _start_writes_flow: log in if needed, then Writes.
            _goto_writes guards the tab-select on the real gate (connected +
            logged_in + baseline_done) so it never silently no-ops on a disabled
            tab — if a prerequisite is somehow missing, it says why."""
            if self.logged_in:
                self._goto_writes()
            else:
                self._select_tab("Login")
                self._login(then=lambda ok: ok and self._goto_writes())

        def _run_bg(self, fn, done=None):
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current operation.")
                return
            self._set_busy(True)

            def worker():
                try:
                    result = fn()
                    err = None
                except Exception as e:      # surface everything to the UI
                    result, err = None, e

                def finish():
                    self._set_busy(False)
                    if err is not None:
                        # D6: a mid-session reboot invalidates the login/baseline
                        # state — re-gate before surfacing the error
                        if isinstance(err, ConsoleRebootError):
                            self.logged_in = False
                            self.baseline_done = False
                            # T7: also disarm the master unlock — otherwise the
                            # Writes tab re-opens with one gate pre-armed post-reboot
                            if hasattr(self, "unlock_var"):
                                self.unlock_var.set(False)
                            self._apply_gates()
                        messagebox.showerror(APP_NAME, str(err))
                    elif done:
                        done(result)
                self._cbq.put(finish)
            threading.Thread(target=worker, daemon=True).start()

        def _out(self, text):
            self.txt_out.config(state="normal")
            self.txt_out.insert("end", text + "\n")
            self.txt_out.see("end")
            self.txt_out.config(state="disabled")

        def _console_out(self, text):
            self.txt_console.config(state="normal")
            self.txt_console.insert("end", text + "\n")
            self.txt_console.see("end")
            self.txt_console.config(state="disabled")

        # -- Phase 0: Connect --------------------------------------------------
        def _build_connect_tab(self):
            f = self._new_tab(" Connect ", "Connect to your bike",
                              "verify the cable, then connect & probe")
            # the pre-connect controls (port row + how-to blurb) are hidden once
            # connected — the success banner is all that's relevant then.
            row = self.connect_row = ttk.Frame(f)
            row.pack(fill="x")
            ttk.Label(row, text="Port:").pack(side="left")
            # The port list is real COM ports only. Exploring without a bike is the
            # "Simulator mode" toggle (Home screen / Settings), not a port entry.
            real_ports = list_serial_ports()
            default = preselect_port or (real_ports[0] if real_ports else "")
            self.port_var = tk.StringVar(value=default)
            self.cbo_port = ttk.Combobox(row, textvariable=self.port_var,
                                         values=real_ports, width=22)
            self.cbo_port.pack(side="left", padx=6)
            ttk.Button(row, text="Refresh", command=self._refresh_ports).pack(side="left")
            # verification lives in ONE place — the "Test your cable" wizard (also on
            # the Home screen). This button just opens it.
            self.btn_verify = ttk.Button(row, text=VERIFY_LABEL,
                                         command=self._show_cable_wizard)
            self.btn_verify.pack(side="left", padx=(12, 0))
            self.btn_connect = ttk.Button(row, text=CONNECT_LABEL,
                                          style=self.sty["accent"],
                                          command=self._connect)
            self.btn_connect.pack(side="left", padx=12)

            self.connect_help = ttk.Label(f, text=(
                "Pick your COM port, then click Connect & probe — it wakes the console "
                "and reads the firmware version. Not sure the cable is right? Click "
                "Test your cable first: it only LISTENS (transmits nothing) to confirm "
                "the wiring + baud before anything is sent. Power the bike during "
                "connect — key ON, or plug in the AC charger (Mode: Charging).\n"
                "No bike yet? Turn on Simulator mode on the Home screen (or Tools → "
                "Settings → Connection). Cable wiring + pinout: Help → Wiring diagram. "
                "Isolation-resistance reads are only valid OFF the charger."),
                justify="left", padding=(0, 10), foreground=P["warn"])
            self.connect_help.pack(anchor="w")

            # success banner shown by _connect's done() (no button — the tabs are
            # right there). Hidden until connected; replaces the pre-connect controls.
            self.connect_success = ttk.Frame(f)
            self.lbl_connect_success = ttk.Label(self.connect_success, text="",
                                                 style="Good.TLabel")
            self.lbl_connect_success.pack(side="left")

            self.txt_probe = self._console_text(f, 16)
            self.txt_probe.pack(fill="both", expand=True)

        def _show_connect_success(self, text):
            # connected: drop the pre-connect controls (port / verify / connect /
            # how-to) — they're pointless now — and show just the banner + console.
            self.connect_row.pack_forget()
            self.connect_help.pack_forget()
            self.lbl_connect_success.config(text=text)
            self.connect_success.pack(fill="x", pady=(0, 8), before=self.txt_probe)

        def _hide_connect_success(self):
            if not hasattr(self, "connect_success"):
                return
            self.connect_success.pack_forget()
            # restore the pre-connect controls (order: port row, then the blurb)
            self.connect_row.pack(fill="x", before=self.txt_probe)
            self.connect_help.pack(anchor="w", before=self.txt_probe)

        def _refresh_ports(self):
            real_ports = list_serial_ports()
            self.cbo_port.config(values=real_ports)
            if not hasattr(self, "txt_probe"):
                return
            # A2: give Refresh visible feedback — a blank list otherwise looks broken
            if real_ports:
                self._probe_log("COM ports found: %s" % ", ".join(real_ports))
            else:
                self._probe_log(
                    "No COM ports found. Plug in the FTDI cable and click Refresh; if "
                    "it still doesn't appear, install the FTDI VCP driver (Windows: "
                    "Device Manager -> Ports). Meanwhile you can turn on Simulator "
                    "mode (Home screen, or Tools → Settings → Connection) to explore.")

        def _on_sim_toggle(self):
            """Simulator toggle (Home screen / Settings): affects the NEXT connect."""
            on = self.sim_var.get()
            self._refresh_sim_badge()
            if hasattr(self, "txt_probe"):
                self._probe_log("Simulator mode %s." % (
                    "ON — no bike/cable needed; Verify & Connect use the simulator"
                    if on else "OFF — using the selected COM port"))
            self._refresh_dash_header()

        def _refresh_sim_badge(self):
            """Reflect the simulator state everywhere it's visible: the landing
            badge and a persistent status-bar indicator."""
            on = self.sim_var.get()
            badge = getattr(self, "_sim_badge", None)
            if badge is not None:
                if on:
                    badge.place(relx=0.5, rely=0.06, anchor="center")
                else:
                    badge.place_forget()
            lbl = getattr(self, "lbl_sim", None)
            if lbl is not None:
                lbl.config(text="◆ SIMULATOR MODE" if on else "")

        def _probe_log(self, text):
            self.txt_probe.config(state="normal")
            self.txt_probe.insert("end", text + "\n")
            self.txt_probe.see("end")
            self.txt_probe.config(state="disabled")

        def _make_port(self, port_name, is_sim):
            """Open a SimPort (simulator mode) or a real serial port. `is_sim` is
            resolved on the MAIN thread and passed in — never read a Tk var from a
            worker thread (Tcl is not thread-safe)."""
            if is_sim:
                return SimPort()
            return open_real_port(port_name)

        def _ensure_log_dir(self):
            """G2: a stale/removed configured save folder (deleted dir, unplugged
            USB) must never block connecting — fall back to the default with a note."""
            probe = self.log_dir or config.DEFAULT_LOG_DIR
            try:
                os.makedirs(probe, exist_ok=True)
                self.log_dir = probe
            except OSError:
                self._probe_log("[!] save folder %s is unavailable — using %s"
                                % (probe, config.DEFAULT_LOG_DIR))
                self.log_dir = config.DEFAULT_LOG_DIR
                try:
                    os.makedirs(self.log_dir, exist_ok=True)
                except OSError:
                    pass
            self._refresh_save_label()

        def _connect(self):
            # T3: honor the busy guard BEFORE the destructive reset. _reset_session_state
            # closes the port + wipes the journal; running it ahead of the _busy check
            # would yank the port out from under an in-flight write (between send and
            # verify) and clear the revert list, only to then refuse the connect.
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current operation.")
                return
            port_name = self.port_var.get().strip()
            if not self.sim_var.get() and not port_name:
                messagebox.showerror(APP_NAME, "No port selected. Pick your COM port "
                                     "(click Refresh after plugging in the cable), or "
                                     "turn on Simulator mode (Home screen, or Tools → "
                                     "Settings → Connection) to explore without a bike.")
                return
            # D1: every connection re-earns its phases (drops any --sim rehearsal
            # state and closes a previously-open port before reopening).
            self._reset_session_state()
            self._ensure_log_dir()          # G2: fall back if the save folder died
            is_simport = self.sim_var.get()

            def job():
                # B3: narrate each step into the connect console AS IT HAPPENS (the
                # worker enqueues to _cbq; the main loop pumps it) — no more staring
                # at a bare progress bar.
                def log(msg):
                    self._cbq.put(lambda m=msg: self._probe_log(m))

                tag = "sim" if is_simport else port_name.replace(":", "")
                logger = SessionLogger(base_dir=self.log_dir, tag=tag)
                log("Session folder: %s" % logger.dir)
                port = self._make_port(port_name, is_simport)
                tr = Transport(port, logger)
                try:
                    log("Listening %s for unsolicited output…"
                        % ("0.3 s" if is_simport else "3 s"))
                    pre = tr.listen(0.3 if is_simport else 3)
                    log("  got %d bytes" % len(pre))
                    # C2: the real console needs one or two CR-LFs to wake — retry
                    resp, prompt = b"", False
                    for attempt in range(1, 4):
                        log("Waking the console (attempt %d): sending CR-LF…" % attempt)
                        resp = tr.send_raw_newline()
                        if looks_like_prompt(pre + resp):
                            log("  ZERO MBB> prompt detected.")
                            prompt = True
                            break
                    blob = pre + resp
                    # C1: reject garbage — a bare '>' in noise no longer counts
                    if not prompt:
                        if nonprintable_ratio(blob) > 0.2:
                            raise RuntimeError(
                                "Received %d bytes of non-text data — wrong baud rate "
                                "or Tx/Rx swapped. Do NOT proceed; re-check wiring. "
                                "Raw bytes saved to session_raw.log." % len(blob))
                        raise RuntimeError(
                            "No prompt detected.\n"
                            "- Check the COM port and that the bike is powered "
                            "(key ON, or plug in the AC charger).\n"
                            "- Click 'Test your cable' to prove RX wiring first.\n"
                            "- Garbage at 38400 usually means Tx/Rx swapped — STOP.\n"
                            "Raw bytes were logged to session_raw.log.")
                    # C3: the version banner must actually parse (positive proof
                    # this is a Gen2 MBB console) before we unlock Phase 1
                    log("Reading firmware version…")
                    ver = tr.exec_command("version", idle_timeout=1.5)
                    if not _looks_like_version(ver):
                        raise RuntimeError(
                            "Reached a prompt, but the 'version' banner was empty or "
                            "unrecognized — not proceeding. Re-check the link (right "
                            "baud? bike powered?). Raw output saved.")
                    log("Checking firmware revision…")
                    return logger, tr, ver, _parse_fw_rev(ver)
                except Exception:
                    tr.close()            # C5: never leak the port on a failed probe
                    raise

            def done(result):
                logger, tr, ver, rev = result
                self.logger, self.transport = logger, tr
                self.connected = True
                self.version_text = ver
                self._probe_log("PROMPT OK — connected.\n")
                self._probe_log(ver)
                self._refresh_dash_header()   # surface the firmware rev in the header
                known = ", ".join(str(r) for r in sorted(KNOWN_FIRMWARE_REVS))
                if rev is None:
                    self._probe_log("\n[!] Could not parse the firmware rev from the "
                                    "banner — reads are fine; be cautious about writes.")
                elif rev not in KNOWN_FIRMWARE_REVS:
                    self._probe_log("\n[!] Firmware rev %s — OpenMBB's safety lists were "
                                    "verified against rev %s ONLY. Reads are fine; be "
                                    "very cautious about writes." % (rev, known))
                    messagebox.showwarning(APP_NAME, "Firmware rev %s is not the verified "
                                           "rev (%s). Safety lists/parsers were checked "
                                           "against rev %s only — reads are fine, be "
                                           "cautious about writes." % (rev, known, known))
                self._refresh_save_label()
                self._apply_gates()
                # owner: don't auto-jump to Read — confirm success here and let the
                # user click through.
                where = "SIMULATOR" if self.sim_var.get() else self.port_var.get()
                self._probe_log("\nConnected — the link is live and read-only. Open "
                                "the Read tab to pull the bike's data.")
                self._show_connect_success(
                    "✓  Connected to %s — the link is live and read-only." % where)

            self._run_bg(job, done)

        # -- Phase 1: Read -----------------------------------------------------
        def _add_tooltip(self, widget, text):
            """Show `text` in a small popup while the pointer hovers `widget`."""
            if not text:
                return
            state = {"win": None}

            def show(_e=None):
                if state["win"] is not None:
                    return
                x = widget.winfo_rootx() + 14
                y = widget.winfo_rooty() + widget.winfo_height() + 4
                win = tk.Toplevel(widget)
                win.wm_overrideredirect(True)
                win.wm_geometry("+%d+%d" % (x, y))
                tk.Label(win, text=text, background="#ffffe0", foreground="#222",
                         relief="solid", borderwidth=1, justify="left",
                         wraplength=340, padx=6, pady=3,
                         font=(self.sty["mono"], 8)).pack()
                state["win"] = win

            def hide(_e=None):
                if state["win"] is not None:
                    state["win"].destroy()
                    state["win"] = None

            widget.bind("<Enter>", show)
            widget.bind("<Leave>", hide)
            widget.bind("<Destroy>", hide)

        def _build_read_tab(self):
            # no tab header + no dashboard row here (owner): the single status line
            # at the top of the window carries connection / rev / VIN / login. This
            # tab is just the two-column body — LEFT the output/status console, RIGHT
            # the action column (primary "Pull full database" on top, raw box,
            # commands, heavy at bottom).
            f = self._new_tab(" Read ")

            body = ttk.Frame(f)
            body.pack(fill="both", expand=True)

            # RIGHT action column — a fixed-width SCROLLABLE panel so the heavy
            # reads at the bottom stay reachable at any window height (owner: the
            # caution buttons weren't visible at the default size). Mirrors the
            # Writes-tab scroll pattern (visible scrollbar + wheel/two-finger).
            rightwrap = ttk.Frame(body, width=312)
            rightwrap.pack(side="right", fill="y", padx=(12, 0))
            rightwrap.pack_propagate(False)
            rbg = ttk.Style().lookup("TFrame", "background") or P["bg"]
            rcanvas = tk.Canvas(rightwrap, highlightthickness=0, bg=rbg, width=300)
            rvsb = ttk.Scrollbar(rightwrap, orient="vertical", command=rcanvas.yview)
            rcanvas.configure(yscrollcommand=rvsb.set)
            rcanvas.pack(side="left", fill="both", expand=True)   # scrollbar packed on demand
            right = ttk.Frame(rcanvas)
            rwin = rcanvas.create_window((0, 0), window=right, anchor="nw")

            def _sync_scroll(_e=None):
                rcanvas.configure(scrollregion=rcanvas.bbox("all"))
                # show the scrollbar only when the content actually overflows (owner)
                need = right.winfo_reqheight() > rcanvas.winfo_height() + 2
                if need and not rvsb.winfo_ismapped():
                    rvsb.pack(side="right", fill="y", before=rcanvas)
                elif not need and rvsb.winfo_ismapped():
                    rvsb.pack_forget()
            right.bind("<Configure>", _sync_scroll)
            rcanvas.bind("<Configure>",
                         lambda e: (rcanvas.itemconfigure(rwin, width=e.width),
                                    _sync_scroll()))

            def _rwheel(e):
                rcanvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
                return "break"     # don't also cycle a combobox under the pointer
            rcanvas.bind("<MouseWheel>", _rwheel)
            right._owl_wheel = _rwheel

            self.btn_baseline = ttk.Button(right, text="Pull full database",
                                           style=self.sty["accent"],
                                           command=self._baseline)
            self.btn_baseline.pack(fill="x")
            self._add_tooltip(self.btn_baseline,
                              "Reads everything at once (health, settings, errors, "
                              "gearing) and saves a backup — the smart first move.")
            # no caption (owner): the progress bar shows only DURING a pull, and the
            # progress label is empty (invisible) when idle, so Commands sits right
            # under the button.
            self.prg = ttk.Progressbar(right, mode="determinate")     # packed on demand
            self.lbl_prog = ttk.Label(right, text="", style="Muted.TLabel",
                                      wraplength=280)
            self.lbl_prog.pack(fill="x", pady=(4, 0))

            ttk.Label(right, text="Commands", style="Heading.TLabel").pack(
                anchor="w", pady=(8, 2))
            quick = ttk.Frame(right)
            quick.pack(fill="x", pady=(2, 0))
            quick.columnconfigure(0, weight=1)
            quick.columnconfigure(1, weight=1)
            for i, cmd in enumerate(READ_COMMANDS + DUMP_COMMANDS):
                b = ttk.Button(quick, text=cmd, width=12,
                               command=lambda c=cmd: self._read_cmd(c))
                b.grid(row=i // 2, column=i % 2, padx=2, pady=2, sticky="ew")
                self._add_tooltip(b, READ_TIPS.get(cmd, ""))

            # E1: live "Watch" — repeat one light read on a timer (reads only, so
            # it stays fully inside the safety model). Great for a charge session.
            wrow = ttk.Frame(right)
            wrow.pack(fill="x", pady=(8, 0))
            self.watch_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(wrow, text="Watch", variable=self.watch_var,
                            command=self._toggle_watch).pack(side="left")
            self.watch_cmd = tk.StringVar(value="status")
            ttk.Combobox(wrow, textvariable=self.watch_cmd, width=8, state="readonly",
                         values=["status", "bms", "inputs", "sevcon", "dash",
                                 "chargers"]).pack(side="left", padx=4)
            self.watch_secs = tk.StringVar(value="5")
            ttk.Combobox(wrow, textvariable=self.watch_secs, width=3, state="readonly",
                         values=["3", "5", "10", "30"]).pack(side="left")
            ttk.Label(wrow, text="s", style="Muted.TLabel").pack(side="left", padx=(2, 0))

            # heavy / special commands — set apart at the bottom
            ttk.Separator(right).pack(fill="x", pady=(12, 8))
            ttk.Label(right, text="⚠ Heavy — may open the contactor",
                      foreground=P["warn"], wraplength=280).pack(anchor="w")
            for cmd in HEAVY_COMMANDS:
                hb = ttk.Button(right, text=cmd,
                                command=lambda c=cmd: self._read_heavy(c))
                hb.pack(fill="x", pady=2)
                self._add_tooltip(hb, READ_TIPS.get(cmd, ""))

            # LEFT: just the output/status window (the raw command entry moved to
            # the Console tab). Its top aligns with the right column's Pull button.
            left = ttk.Frame(body)
            left.pack(side="left", fill="both", expand=True)
            self.txt_out = self._console_text(left, 20)
            self.txt_out.pack(fill="both", expand=True)

            # route wheel/two-finger scroll over the action column's buttons to
            # its canvas (they'd otherwise swallow the event)
            self._bind_page_wheel(right)
            self._refresh_dash_header()

        def _refresh_dash_header(self):
            """Update the dashboard's connected/identity banner from known state.
            VIN comes from a settings read (Pull full database), so it shows a
            nudge until then. VIN is display-only — never written to a saved file."""
            lbl = getattr(self, "dash_header", None)
            if lbl is None:
                return
            if not self.connected:
                lbl.config(text="○ Not connected — connect on the Connect tab",
                           style="Muted.TLabel")
                return
            where = "SIMULATOR" if self.sim_var.get() else self.port_var.get()
            bits = ["● CONNECTED (%s)" % where]
            mm = re.search(r"Firmware Rev\s*:?\s*(\w+)", self.version_text or "")
            if mm:
                bits.append("MBB rev %s" % mm.group(1))
            vin = self.settings["vin"].get("value") if self.settings.get("vin") else None
            bits.append(("VIN %s" % vin) if vin else "VIN: run Pull full database")
            bits.append("logged in" if self.logged_in else "read-only")
            lbl.config(text="   ·   ".join(bits), style="Good.TLabel")

        def _read_heavy(self, cmd, confirmed=False, out=None):
            # A2: a heavy log dump can make the BMS open the drivetrain contactor
            # (it starves the MBB's CAN servicing). Gate it behind an explicit
            # warning + confirm — never let it run casually or in the baseline.
            if not messagebox.askokcancel(APP_NAME,
                    "'%s' reads the full log (~1 MB, several minutes at 38400 baud).\n\n"
                    "On a keyed-on bike this can make the BMS briefly OPEN the "
                    "drivetrain contactor — you'll hear a click and the dash will "
                    "flash; it recovers when the read finishes. The bike must be "
                    "SAFELY PARKED (never do this while riding).\n\nContinue?" % cmd):
                return
            self._read_cmd(cmd, idle_timeout=30.0, confirmed=confirmed, out=out)

        def _toggle_watch(self):
            # E1: start/stop the repeat-read timer.
            if self.watch_var.get():
                if not self.connected:
                    self.watch_var.set(False)
                    messagebox.showinfo(APP_NAME, "Connect first — Watch re-runs a "
                                        "read on a timer once you're connected.")
                    return
                self._out("\n=== WATCH started: '%s' every %s s (reads only). Uncheck "
                          "to stop. ===" % (self.watch_cmd.get(), self.watch_secs.get()))
                self._watch_tick()
            else:
                self._out("=== WATCH stopped ===")

        def _watch_tick(self):
            if not self.watch_var.get():
                return
            if not self.connected:               # auto-stop on disconnect
                self.watch_var.set(False)
                self._out("=== WATCH stopped (disconnected) ===")
                return
            if not self._busy:                   # skip a tick if a read is in flight
                self._read_cmd(self.watch_cmd.get())
            try:
                secs = max(2, int(self.watch_secs.get()))
            except (ValueError, TypeError):
                secs = 5
            self.after(secs * 1000, self._watch_tick)

        def _read_cmd(self, cmd, quiet=False, idle_timeout=None, confirmed=False,
                      out=None):
            emit = out or self._out          # button reads -> txt_out; Console -> its own
            # A1/SAFE-2: classify by the lowercased FIRST TOKEN (like the transport),
            # not an exact full-string match — so a raw-box variant such as
            # "eventlogdump 5" or "Eventlogdump" still gets dump-class timeouts
            # instead of a 60 s cut mid-stream.
            head = (cmd.strip().split() or [""])[0].lower()
            is_dump = head in LONG_COMMANDS
            idle = idle_timeout if idle_timeout is not None else (15.0 if is_dump else 2.5)

            def job():
                def prog(nbytes):
                    self._cbq.put(lambda: self.lbl_prog.config(
                        text="%s: %d KB" % (cmd, nbytes // 1024)))
                out = self.transport.exec_command(
                    cmd, idle_timeout=idle,
                    max_time=900.0 if is_dump else 60.0,
                    progress_cb=prog if is_dump else None, confirmed=confirmed)
                return out, self.transport.last_saved_path

            def done(result):
                out, path = result
                self.lbl_prog.config(text="")
                if not quiet:
                    emit("\n### %s  (saved: %s)\n%s" % (cmd, os.path.basename(path), out))
                    # C1: point first-timers at where the numbers get interpreted
                    # (once — don't nag on every read).
                    if not getattr(self, "_analyze_hint_shown", False):
                        emit("  → To interpret these (ok / watch / alert), open "
                             "the Analyze tab and click 'Use current session'.")
                        self._analyze_hint_shown = True
                if cmd == "set":
                    self._ingest_settings(out)
                if cmd == "help" and not self.logged_in:
                    self.help_logged_out = out
                # D3 (review SAFE-3): a raw-box `logout` de-escalates the console —
                # drop the GUI's login state + master unlock and re-gate, so the
                # Writes tab doesn't stay visibly unlocked against a level-0 console.
                # Mirrors the reboot re-gate in _run_bg.
                if head == "logout":
                    self.logged_in = False
                    if hasattr(self, "unlock_var"):
                        self.unlock_var.set(False)
                    self._apply_gates()

            self._run_bg(job, done)

        def _cmd_enter(self):
            """Command-line Enter: send the typed command, then clear the box +
            record it in history (↑/↓ recall it)."""
            cmd = self.raw_var.get().strip()
            if not cmd:
                return "break"
            if not self._cmd_history or self._cmd_history[-1] != cmd:
                self._cmd_history.append(cmd)
            self._cmd_hist_idx = len(self._cmd_history)
            self._console_out("› " + cmd)          # echo the input like a terminal
            self._raw_send(out=self._console_out)  # output -> the Console's console
            self.raw_var.set("")                   # clear like a terminal
            return "break"

        def _cmd_history_nav(self, delta):
            """↑/↓ through previously typed commands."""
            if not self._cmd_history:
                return "break"
            self._cmd_hist_idx = max(0, min(len(self._cmd_history),
                                            self._cmd_hist_idx + delta))
            self.raw_var.set(self._cmd_history[self._cmd_hist_idx]
                             if self._cmd_hist_idx < len(self._cmd_history) else "")
            try:
                self.ent_cmd.icursor("end")
            except Exception:
                pass
            return "break"

        def _raw_send(self, out=None):
            cmd = self.raw_var.get().strip()
            if not cmd:
                return
            if not self.connected:
                messagebox.showinfo(APP_NAME, "Connect to your bike first — the "
                                    "console sends live commands over the cable.")
                return
            toks = cmd.split()
            head = toks[0].lower() if toks else ""
            # A typed password must go through the Login tab: there it is masked
            # and the login level is confirmed. From the raw box it would be
            # logged in the clear AND elevate the console while the GUI still
            # believes it is "not logged in".
            if head == "login" and len(toks) >= 2:
                messagebox.showwarning(APP_NAME, "Use the Login tab to enter a "
                                       "password — it is masked there and the "
                                       "login level is confirmed. The raw box "
                                       "would record it in the clear.")
                return
            reason = command_blocked(cmd)
            confirmed = False
            if reason:
                # No hard wall (owner's own bike): show what it does / what could
                # happen / how to recover, and require typing "confirm" first.
                if not self._confirm_dangerous(cmd, reason):
                    return
                confirmed = True
            # A HEAVY log dump gets the contactor warning + long idle timeout; it is
            # not blocklisted, so it takes the normal (confirmed=False) path here.
            if head in HEAVY_COMMANDS:
                self._read_heavy(cmd, confirmed=confirmed, out=out)
                return
            self._read_cmd(cmd, confirmed=confirmed, out=out)

        def _load_cmd_ref(self):
            """Lazy-load assets/command_reference.json, keyed for confirm lookups."""
            cached = getattr(self, "_cmd_ref_cache", None)
            if cached is not None:
                return cached
            d = {}
            try:
                import json
                from importlib.resources import files
                raw = (files("openmbb") / "assets" / "command_reference.json"
                       ).read_text(encoding="utf-8")
                for it in json.loads(raw):
                    toks = str(it.get("name", "")).strip().lower().split()
                    if not toks:
                        continue
                    if toks[0] == "set" and len(toks) >= 2:
                        d.setdefault("set:" + toks[1], it)
                    elif toks[0].startswith("ov_"):
                        d.setdefault("ov_*", it)
                    elif len(toks) >= 2 and not toks[1].startswith(("<", "(")):
                        d.setdefault(toks[0] + " " + toks[1], it)
                        d.setdefault(toks[0], it)
                    else:
                        d.setdefault(toks[0], it)
            except Exception:
                d = {}
            self._cmd_ref_cache = d
            return d

        def _cmd_ref_lookup(self, cmd):
            ref = self._load_cmd_ref()
            toks = str(cmd).strip().split()
            if not toks:
                return None
            head = toks[0].lower()
            if head == "set" and len(toks) >= 2:
                hit = ref.get("set:" + toks[1].lower())
                if hit:
                    return hit
            if head.startswith("ov_") and "ov_*" in ref:
                return ref["ov_*"]
            if len(toks) >= 2:
                two = ref.get(head + " " + toks[1].lower())
                if two:
                    return two
            return ref.get(head)

        def _confirm_dangerous(self, cmd, reason):
            """Informed-consent gate for a destructive command: show what it does /
            what could happen / how to recover, and require typing 'confirm'. No
            hard block — returns True only if the owner deliberately confirms."""
            from . import dialogs
            ref = self._cmd_ref_lookup(cmd) or {}
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("Dangerous command")
            win.configure(bg=surface)
            win.resizable(False, False)
            win.transient(self)
            result = {"ok": False}

            body = ttk.Frame(win, padding=22)
            body.pack(fill="both", expand=True)
            danger = ref.get("danger", "dangerous")
            ttk.Label(body, text=("☢  CATASTROPHIC command" if danger == "catastrophic"
                                  else "⚠  Dangerous command"), style="Heading.TLabel",
                      foreground=P["danger"]).pack(anchor="w")
            ttk.Label(body, text=cmd, font=(self.sty["mono"], 12, "bold"),
                      foreground=P["warn"]).pack(anchor="w", pady=(2, 10))

            def line(label, text, color=None):
                if not text:
                    return
                row = ttk.Frame(body)
                row.pack(fill="x", pady=(0, 6))
                ttk.Label(row, text=label, style="Muted.TLabel", width=17,
                          anchor="nw").pack(side="left", anchor="n")
                ttk.Label(row, text=text, wraplength=430, justify="left",
                          foreground=color or P["fg"]).pack(side="left", fill="x",
                                                             expand=True)
            if ref:
                line("What it does", ref.get("what_it_does"))
                line("What could happen", ref.get("what_could_happen"), P["warn"])
                line("Reversible?", ref.get("reversible"))
                line("How to recover", ref.get("recovery"))
            else:
                line("Why flagged", reason, P["warn"])
                line("More", "See Help → Command reference for the details.")

            ttk.Separator(body).pack(fill="x", pady=(6, 10))
            ttk.Label(body, text="Type  confirm  to send this command:").pack(anchor="w")
            cvar = tk.StringVar()
            cent = ttk.Entry(body, textvariable=cvar)
            cent.pack(fill="x", pady=(4, 12))
            btns = ttk.Frame(body)
            btns.pack(fill="x")

            def do_send():
                if cvar.get().strip().lower() == "confirm":
                    result["ok"] = True
                    win.destroy()

            def do_cancel():
                win.destroy()
            ttk.Button(btns, text="Send", style=self.sty["danger"],
                       command=do_send).pack(side="right")
            ttk.Button(btns, text="Cancel", command=do_cancel).pack(side="right",
                                                                    padx=(0, 8))
            cent.bind("<Return>", lambda e: do_send())
            win.protocol("WM_DELETE_WINDOW", do_cancel)
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            try:
                win.grab_set()
            except Exception:
                pass
            cent.focus_set()
            win.wait_window()
            return result["ok"]

        def _show_command_reference(self):
            self._open_html_help("info.html", "Command reference",
                                 "Command reference (see the repo for the full page).",
                                 "reference")

        def _baseline(self, then=None):
            # D4: run `obd` LAST — after `set` (the backup) and errorlogdump —
            # because its output has never been captured live; if it stalls or
            # returns nothing, the settings backup is already safely on disk first.
            reads = [c for c in READ_COMMANDS if c != "obd"]
            seq = reads + ["set"] + DUMP_COMMANDS + (["obd"] if "obd" in READ_COMMANDS else [])

            def job():
                results, errors = {}, {}
                for i, cmd in enumerate(seq):
                    # B3: progress bar + label AND a live play-by-play line
                    self._cbq.put(lambda c=cmd, i=i: (
                        self.lbl_prog.config(text="pulling: %s (%d/%d)"
                                             % (c, i + 1, len(seq))),
                        self.prg.config(maximum=len(seq), value=i),
                        self._out("  [%d/%d] reading %s…" % (i + 1, len(seq), c))))
                    is_dump = cmd in LONG_COMMANDS
                    prog = None
                    if is_dump:
                        def prog(n, c=cmd):
                            self._cbq.put(lambda: self.lbl_prog.config(
                                text="pulling: %s (%d KB)" % (c, n // 1024)))
                    # C6: each command tolerant — one failure doesn't discard the pass
                    try:
                        out = self.transport.exec_command(
                            cmd, idle_timeout=15.0 if is_dump else 2.5,
                            max_time=900.0 if is_dump else 60.0, progress_cb=prog)
                        results[cmd] = out
                        # persist the settings baseline the MOMENT `set` returns —
                        # before the long dumps, so a later hiccup can't lose it
                        if cmd == "set" and out.strip():
                            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                            self.logger.save_named("settings_baseline_%s.txt" % stamp, out)
                    except ConsoleRebootError:
                        # T4: a reboot must ABORT the pass and re-gate (via
                        # _run_bg.finish), never be tallied as a per-command failure
                        # that lets baseline still be declared complete.
                        raise
                    except Exception as e:
                        errors[cmd] = str(e)
                return results, errors

            def done(result):
                results, errors = result
                self.prg.config(value=0)
                self.prg.pack_forget()          # progress bar only shows during a pull
                # C7: record power-state context (on-charger contaminates iso/SOC)
                self._write_session_meta(results.get("status", ""))
                for cmd in seq:
                    if cmd in errors:
                        self._out("  [FAILED] %s: %s" % (cmd, errors[cmd]))
                if results.get("help") and not self.logged_in:
                    self.help_logged_out = results["help"]
                self._ingest_settings(results.get("set", ""))
                # C6/C17: unlock Phase 2 only if the ESSENTIAL reads succeeded and
                # the settings dump actually parsed — not on empty/garbage captures
                st_settings, _ = parse_settings_dump(results.get("set", ""))
                missing = [c for c in ("version", "status", "stats")
                           if not results.get(c, "").strip()]
                if not st_settings:
                    missing.append("set(parsed)")
                if not missing:
                    self.baseline_done = True
                    self._trend_cache = None      # a new pull -> refresh trend charts
                    self._apply_gates()
                    # confirmation right under the Pull button (owner)
                    self.lbl_prog.config(text="✓ Full database pulled & backed up",
                                         foreground=P["green"])
                    self._out("\n=== Full database pulled -> %s ===" % self.logger.dir)
                    if errors:
                        self._out("(%d command(s) failed; retry them with the read "
                                  "buttons: %s)" % (len(errors), ", ".join(errors)))
                    self._out("→ Backup saved. Review it on the Analyze tab.")
                else:
                    self.lbl_prog.config(text="database pull incomplete",
                                         foreground=P["danger"])
                    self._out("\n[!] DATABASE PULL INCOMPLETE — essential reads missing/"
                              "unparsed: %s. No backup was saved, so Writes stays "
                              "LOCKED; fix the link and re-run Pull full database."
                              % ", ".join(missing))
                if then:                 # continue a chained flow (e.g. -> Writes)
                    then(self.baseline_done)

            self.lbl_prog.config(text="", foreground=P["dim"])          # reset from a prior run
            self.prg.pack(fill="x", pady=(4, 2), before=self.lbl_prog)  # show progress
            self._run_bg(job, done)

        def _write_session_meta(self, status_text):
            """C7: stamp the session with power mode + firmware rev, and warn when
            a baseline was captured on the charger (isolation/SOC then unreliable)."""
            mode = ""
            mm = re.search(r"Mode\s*:\s*(.+)", status_text or "")
            if mm:
                mode = mm.group(1).strip()
            rev = _parse_fw_rev(self.version_text)
            meta = ["OpenMBB session metadata",
                    "time: %s" % _dt.datetime.now().isoformat(timespec="seconds"),
                    "app_version: %s" % __version__,
                    "firmware_rev: %s" % (rev if rev is not None else "?"),
                    "power_mode: %s" % (mode or "?")]
            if self.logger:
                self.logger.save_named("session_meta.txt", "\n".join(meta) + "\n")
            if mode and "charg" in mode.lower():
                self._out("[note] data pulled while CHARGING — the isolation "
                          "reading and SOC context are NOT valid off-charger; "
                          "re-read unplugged + dry before acting on them.")

        def _ingest_settings(self, dump_text):
            settings, order = parse_settings_dump(dump_text)
            if not settings:
                # never silently keep a stale dict: say so when a non-empty dump
                # parsed to nothing (firmware format the parser doesn't know yet)
                if (dump_text or "").strip():
                    self._out("[WARNING] the 'set' output was not recognized by the "
                              "settings parser — the firmware format may differ. "
                              "Writes stay unavailable until the parser is updated; "
                              "the raw capture is saved.")
                return
            self.settings, self.settings_order = settings, order
            self._out("[parsed %d settings from live dump]" % len(settings))
            if len(settings) < 10:
                self._out("[note] only %d settings parsed — this looks like a "
                          "level-0 (identity-only) dump; the tunables "
                          "(spfront/sprear/…) appear after login on rev 41." % len(settings))
            self._refresh_write_rows()

        # -- Phase 2: Login ------------------------------------------------------
        def _build_login_tab(self):
            f = self._new_tab(" Login ", "Log in for more access",
                              "read-only — reveals the tunable settings")
            ttk.Label(f, text=(
                "Logging in is READ-ONLY — it reveals the tuning settings the console "
                "hides and unlocks the Writes tab. It changes nothing on the bike, and "
                "a failed attempt just leaves you read-only. The box is pre-filled with "
                "the last password that worked (or a community-known one); press Login, "
                "or type a different one."),
                wraplength=920, justify="left").pack(anchor="w")

            row = ttk.Frame(f)
            row.pack(fill="x", pady=8)
            ttk.Label(row, text="Password:").pack(side="left")
            self.login_pw = tk.StringVar(value=self._default_login_pw())
            # not masked (owner): these are community-known service passwords, and
            # seeing what you're about to try is more useful than hiding it. The
            # password is still REDACTED in the on-disk session logs.
            ent = ttk.Entry(row, textvariable=self.login_pw, width=24)
            ent.pack(side="left", padx=6)
            ent.bind("<Return>", lambda e: self._login_custom())
            ttk.Button(row, text="Login", style=self.sty["accent"],
                       command=self._login_custom).pack(side="left")
            # clear success/failure confirmation (owner)
            self.lbl_login_status = ttk.Label(row, text="", style="Good.TLabel")
            self.lbl_login_status.pack(side="left", padx=12)

            self.txt_login = self._console_text(f, 24)
            self.txt_login.pack(fill="both", expand=True)

        def _set_login_status(self, ok):
            lbl = getattr(self, "lbl_login_status", None)
            if lbl is None:
                return
            if ok:
                lbl.config(text="✓ Logged in — the Writes tab is unlocked",
                           foreground=P["green"])
            else:
                lbl.config(text="", foreground=P["dim"])

        def _default_login_pw(self):
            """Pre-fill the password box with the last password that worked (a saved
            one), else a community-known default — the user can change it. The
            'Set up writes' flow still auto-tries all known passwords under the hood."""
            saved = config.get_saved_passwords()
            if saved:
                return saved[-1]
            return COMMUNITY_PASSWORDS[0] if COMMUNITY_PASSWORDS else ""

        def _login_log(self, text):
            self.txt_login.config(state="normal")
            self.txt_login.insert("end", text + "\n")
            self.txt_login.see("end")
            self.txt_login.config(state="disabled")

        def _login_custom(self):
            pw = self.login_pw.get().strip()
            if not pw:
                messagebox.showinfo(APP_NAME, "Type a password first.")
                return
            self._login([pw], redact=True)

        def _login(self, passwords=None, redact=False, then=None):
            if passwords is not None:
                pws = [p for p in passwords if p]
            else:
                # E5: the "known passwords" button tries the public community ones
                # AND any the user chose to remember.
                pws = [p for p in COMMUNITY_PASSWORDS if p] + [
                    p for p in config.get_saved_passwords()
                    if p and p not in COMMUNITY_PASSWORDS]
            if not pws:
                messagebox.showinfo(APP_NAME, "Enter a password to try.")
                return
            # E5: a REMEMBERED password is the user's own — mask it on screen and
            # register it so it never reaches disk in clear (community passwords are
            # public and shown as-is).
            private = set() if redact else (set(config.get_saved_passwords())
                                            - set(COMMUNITY_PASSWORDS))
            for p in private:
                try:
                    self.transport.logger.add_redaction(p)
                except Exception:
                    pass

            def shown(pw):
                return "****" if (redact or pw in private) else pw

            def job():
                attempts = []
                success = False
                used = None
                level = 0
                for pw in pws:
                    out = self.transport.exec_command(
                        "login %s" % pw, idle_timeout=2.0,
                        redact=pw if redact else None)
                    # D5: confirm elevation via the READ-ONLY level query — bare
                    # `login` prints 'Login Level: N' (ground truth on this bike).
                    # The attempt's own wording is unverified on rev 41, so it is
                    # only a fast-path fallback, and explicit fail words veto it.
                    lvl_out = self.transport.exec_command("login", idle_timeout=2.0)
                    attempts.append((pw, out, lvl_out))
                    definite_fail = re.search(r"fail|denied|invalid|incorrect", out, re.I)
                    m = re.search(r"login\s*level\s*:?\s*(\d+)", lvl_out, re.I)
                    lvl = int(m.group(1)) if m else None
                    if lvl is not None and lvl >= 1 and not definite_fail:
                        success, used, level = True, pw, lvl
                        break
                    if (lvl is None and not definite_fail and re.search(
                            r"(?<!not )logged in|level\s*:?\s*[1-9]", out, re.I)):
                        success, used = True, pw
                        break
                post = {}
                if success:
                    post["help"] = self.transport.exec_command("help", idle_timeout=2.5)
                    post["set"] = self.transport.exec_command("set", idle_timeout=4.0,
                                                              max_time=120.0)
                return attempts, success, post, used, level

            def done(result):
                attempts, success, post, used, level = result
                for pw, out, _lvl_out in attempts:
                    masked = (out.replace(pw, "****") if (redact or pw in private)
                              else out)
                    self._login_log(">>> login %s\n%s\n" % (shown(pw), masked))
                if not success:
                    self._login_log("Rejected — staying read-only. (MBB passwords "
                                    "are community-held; try another.)")
                    if getattr(self, "lbl_login_status", None) is not None:
                        self.lbl_login_status.config(
                            text="✗ That password was rejected — still read-only",
                            foreground=P["danger"])
                    if then:
                        then(False)
                    return
                self.logged_in = True
                self._set_login_status(True)
                lvl_txt = " (level %d)" % level if level else ""
                if redact:
                    self._login_log("LOGIN OK%s with your typed password (masked in "
                                    "the logs)." % lvl_txt)
                else:
                    self._login_log("LOGIN OK%s (login %s). Re-captured help + settings."
                                    % (lvl_txt, shown(used)))
                # E5: offer to remember a WORKING password that isn't already known
                # or saved (i.e. one the user just typed) — no source editing.
                if used and used not in COMMUNITY_PASSWORDS \
                        and used not in config.get_saved_passwords():
                    if messagebox.askyesno(APP_NAME, "Remember this password so OpenMBB "
                            "tries it automatically next time? It is stored in your "
                            "config file (~/.openmbb/config.json). You can clear it via "
                            "Tools → Settings → Login."):
                        config.add_saved_password(used)
                        self._login_log("Password remembered — it'll be tried "
                                        "automatically next session (clear it via "
                                        "Tools → Settings → Login).")
                if self.help_logged_out and post.get("help"):
                    diff = "\n".join(difflib.unified_diff(
                        self.help_logged_out.splitlines(),
                        post["help"].splitlines(),
                        "help (logged out)", "help (logged in)", lineterm=""))
                    self._login_log("\n--- help diff (new commands revealed) ---\n"
                                    + (diff or "(no differences)"))
                if post.get("set"):
                    # D8: the post-login `set` is the first dump that shows the
                    # tunables' pre-change values — save it as the authoritative
                    # labeled baseline (the pre-login one is identity-only on rev 41)
                    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    self.logger.save_named("settings_baseline_postlogin_%s.txt" % stamp,
                                           post["set"])
                    self._login_log("post-login settings baseline saved — the "
                                    "authoritative pre-change backup.")
                    self._ingest_settings(post["set"])
                self._apply_gates()
                self._login_log("\nPhase 3 (Writes) unlocked — writes still require the "
                                "master unlock toggle + per-write confirmation.")
                if then:
                    then(True)

            self._run_bg(job, done)

        def _scrollable_tab(self, title):
            """A notebook tab whose content scrolls with a VISIBLE scrollbar.
            Returns the inner frame to build into (review D3)."""
            outer = ttk.Frame(self.nb)
            self.nb.add(outer, text=title)
            bg = ttk.Style().lookup("TFrame", "background") or P["bg"]
            canvas = tk.Canvas(outer, highlightthickness=0, bg=bg)
            vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            f = ttk.Frame(canvas, padding=18)
            win = canvas.create_window((0, 0), window=f, anchor="nw")
            f.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>",
                        lambda e: canvas.itemconfigure(win, width=e.width))
            def _wheel(e):
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            canvas.bind("<MouseWheel>", _wheel)
            # stash so the caller can extend wheel/two-finger scroll to the page's
            # static child widgets (they'd otherwise swallow the event). See
            # _bind_page_wheel.
            f._owl_wheel = _wheel
            return f

        def _bind_page_wheel(self, inner):
            """Route wheel / two-finger scroll over the page's static widgets to the
            scrollable canvas — child widgets otherwise eat the event so only the
            scrollbar drag worked. Widgets that scroll themselves (Treeview / Text /
            Listbox) are left alone."""
            wheel = getattr(inner, "_owl_wheel", None)
            if wheel is None:
                return

            def walk(w):
                if w.winfo_class() not in ("Treeview", "Text", "Listbox"):
                    try:
                        w.bind("<MouseWheel>", wheel)
                    except Exception:
                        pass
                for c in w.winfo_children():
                    walk(c)
            walk(inner)

        # -- Phase 3: Writes -------------------------------------------------------
        def _build_write_tab(self):
            f = self._scrollable_tab(" Writes ")     # D3: visible scrollbar
            self._tab_header(f, "Change a setting",
                             "advanced — whitelisted, backed up, reversible")
            self.unlock_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(f, text="UNLOCK WRITES (master gate)",
                            style=self.sty["toggle"],
                            variable=self.unlock_var).pack(anchor="w")
            # D2/D4: one concise line (no redundant options button — it duplicated the
            # per-row description; the read-only reference is Help → Command reference).
            ttk.Label(f, foreground=P["dim"], wraplength=940, justify="left",
                      text="Arming UNLOCK only enables the Write… button — it changes "
                      "nothing on its own. Every write backs up all settings, reads "
                      "the value back to verify, and is journaled so you can Revert. "
                      "Change one thing at a time. Rows below are the settings your "
                      "bike actually reports that are safe to change.").pack(
                          anchor="w", pady=(2, 6))

            # owner: edit in the table — double-click a row's "New value" to type a
            # value, then click "Write" on that same row. No separate input box.
            cols = ("name", "current", "risk", "new", "write")
            heads = ("Setting", "Current", "Risk", "New value", "")
            widths = (150, 120, 90, 140, 90)
            trow = ttk.Frame(f)
            trow.pack(fill="x", pady=(8, 4))
            self.tree = ttk.Treeview(trow, columns=cols, show="headings", height=9)
            tvsb = ttk.Scrollbar(trow, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=tvsb.set)
            tvsb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            for c, hd, w in zip(cols, heads, widths):
                self.tree.heading(c, text=hd)
                self.tree.column(c, width=w, anchor="w")
            self._attach_tree_copy(self.tree)       # E4
            self.tree.tag_configure("safe", foreground="#7fe0a0")
            self.tree.tag_configure("caution", foreground=P["warn"])
            self.tree.tag_configure("pending", foreground=P["green"])
            self.tree.bind("<<TreeviewSelect>>", self._show_effect)
            self.tree.bind("<Double-1>", self._writes_edit_cell)
            self.tree.bind("<Button-1>", self._writes_action_click)
            self._pending_writes = {}

            self.lbl_effect = ttk.Label(
                f, text="Double-click a row's 'New value' cell to edit it, then click "
                "'Write' on that row.", wraplength=1000, justify="left", padding=(0, 4))
            self.lbl_effect.pack(anchor="w")

            # (the read-only Sevcon safety guards moved off this action page — they
            # are documented context, available in Help -> Command reference and the
            # "Write options (read-only)" reference, not editable here.)

            ttk.Label(f, text="Writes journal (select an entry to revert):",
                      foreground=P["dim"]).pack(anchor="w", pady=(10, 2))
            jrow = ttk.Frame(f)
            jrow.pack(fill="both", expand=True)
            self.lst_journal = tk.Listbox(
                jrow, height=5, font=(self.sty["mono"], 9), bg=P["console"],
                fg=P["termfg"], selectbackground=P["sel"],
                selectforeground="#eafff2", relief="flat",
                highlightthickness=1, highlightbackground=P["panel"],
                highlightcolor=P["panel"])
            self.lst_journal.pack(side="left", fill="both", expand=True)
            ttk.Button(jrow, text="Revert selected", command=self._revert).pack(
                side="left", padx=8, anchor="n")

            self._bind_page_wheel(f)   # wheel/two-finger scroll over the whole page

        def _refresh_write_rows(self):
            if not hasattr(self, "tree"):
                return
            self.tree.delete(*self.tree.get_children())
            self._pending_writes = {}
            for name in self.settings_order:
                if name in WRITE_WHITELIST and name in self.settings:
                    label, effect, risk, _v, _w = WRITE_WHITELIST[name]
                    tag = "safe" if risk.startswith("SAFE") else "caution"
                    self.tree.insert("", "end", iid=name, tags=(tag,), values=(
                        name, self.settings[name]["value"], risk.split(" - ")[0],
                        "", ""))

        def _write_help_map(self):
            """T5: lazy-load the write-options explanations
            (assets/write_options_help.json), keyed by setting token. Empty dict if
            unavailable — enrichment is additive, so a missing file never breaks
            Writes."""
            cached = getattr(self, "_write_help_cache", None)
            if cached is not None:
                return cached
            data = {}
            try:
                from importlib.resources import files
                import json
                raw = (files("openmbb") / "assets" / "write_options_help.json"
                       ).read_text(encoding="utf-8")
                for it in json.loads(raw):
                    k = str(it.get("name") or "").strip().lower()
                    if k:
                        data[k] = it
            except Exception:
                data = {}
            self._write_help_cache = data
            return data

        def _write_help_lines(self, name):
            """Plain-language help for a write setting (what it does / caution /
            rev-41 note) — shared by the row description and the confirm dialog."""
            it = self._write_help_map().get(str(name).strip().lower())
            if not it:
                return []
            out = []
            if it.get("what_it_does"):
                out.append("What it does — %s" % it["what_it_does"])
            if it.get("caution"):
                out.append("Caution — %s" % it["caution"])
            if it.get("seen_on_rev41") is False:
                out.append("Note — not confirmed on the verified rev-41 bike.")
            return out

        def _show_effect(self, _evt=None):
            sel = self.tree.selection()
            if not sel:
                return
            name = sel[0]
            label, effect, risk, _v, _w = WRITE_WHITELIST[name]
            parts = ["%s — %s" % (name, label), "EFFECT: %s" % effect,
                     "RISK: %s" % risk] + self._write_help_lines(name)
            self.lbl_effect.config(text="\n".join(parts))

        def _writes_edit_cell(self, event):
            """Double-click the 'New value' cell -> an inline Entry to type into."""
            row = self.tree.identify_row(event.y)
            if not row or self.tree.identify_column(event.x) != "#4":   # "new" column
                return
            bbox = self.tree.bbox(row, "new")
            if not bbox:
                return
            x, y, w, h = bbox
            var = tk.StringVar(value=self._pending_writes.get(row, ""))
            ent = tk.Entry(self.tree, textvariable=var, font=(self.sty["mono"], 10),
                           bg=P["field"], fg=P["fg"], insertbackground=P["fg"],
                           relief="flat", highlightthickness=1,
                           highlightbackground=P["green"])
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.icursor("end")
            ent.select_range(0, "end")

            done = {"v": False}                 # Return destroys -> FocusOut fires too

            def commit(_e=None):
                if done["v"]:
                    return
                done["v"] = True
                val = var.get().strip()
                try:
                    ent.destroy()
                except Exception:
                    pass
                self._set_pending_write(row, val)
            ent.bind("<Return>", commit)
            ent.bind("<FocusOut>", commit)
            ent.bind("<Escape>", lambda e: (done.__setitem__("v", True), ent.destroy()))

        def _set_pending_write(self, name, val):
            """Stage/clear an inline pending write for a row: show the new value + a
            'Write' affordance in that row when it differs from the current value."""
            if name not in WRITE_WHITELIST or name not in self.settings:
                return
            _l, _e, risk, _v, _w = WRITE_WHITELIST[name]
            cur = self.settings.get(name, {}).get("value", "")
            base = (name, cur, risk.split(" - ")[0])
            tag = "safe" if risk.startswith("SAFE") else "caution"
            if not val or first_number(val) == first_number(cur):
                self._pending_writes.pop(name, None)
                self.tree.item(name, values=base + ("", ""), tags=(tag,))
            else:
                self._pending_writes[name] = val
                self.tree.item(name, values=base + (val, "✎ Write →"),
                               tags=("pending",))

        def _writes_action_click(self, event):
            """Single click on a row's 'Write' cell -> apply that row's pending value."""
            row = self.tree.identify_row(event.y)
            if row and self.tree.identify_column(event.x) == "#5" \
                    and row in self._pending_writes:
                self._write_value(row, self._pending_writes[row])
                return "break"

        def _write_value(self, name, new_val):
            if not self.unlock_var.get():
                messagebox.showwarning(APP_NAME, "Writes are locked. Toggle the master "
                                       "'UNLOCK WRITES' gate first.")
                return
            new_val = (new_val or "").strip()
            if not new_val or name not in WRITE_WHITELIST:
                return
            label, effect, risk, validator, warn_fn = WRITE_WHITELIST[name]
            ok, msg = validator(new_val)
            if not ok:
                messagebox.showerror(APP_NAME, "Rejected: %s %s" % (name, msg))
                return
            warn = warn_fn(new_val) if warn_fn else None

            def job():
                # 1. re-read current value live
                dump = self.transport.exec_command("set", idle_timeout=4.0, max_time=120.0)
                live, _ = parse_settings_dump(dump)
                if name not in live:
                    raise RuntimeError("'%s' not present in the live settings dump — "
                                       "refusing to write." % name)
                return dump, live[name]["value"]

            def confirm_and_send(result):
                dump, old_val = result
                notes = "".join("%s\n" % ln for ln in self._write_help_lines(name))
                if warn:
                    notes += "WARNING: %s\n" % warn
                text = ("%s — %s\n\n%s  ->  %s\n\nEFFECT: %s\nRISK: %s\n%s\n"
                        "What happens when you click OK:\n"
                        "  1. a full backup of ALL current settings is saved to the "
                        "session folder,\n"
                        "  2. the change is sent, then read back to VERIFY it took,\n"
                        "  3. it's recorded in the writes journal — select it and "
                        "click 'Revert selected' (below the table) to undo it.\n\n"
                        "Proceed?"
                        % (name, label, old_val, new_val, effect, risk, notes))
                if not messagebox.askokcancel("Confirm write", text):
                    return

                def job2():
                    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    self.logger.save_named("settings_backup_%s.txt" % stamp, dump)
                    # D4: journal INTENT before the write reaches the wire, so a
                    # failure between send and verify still records that the bike
                    # may have changed. Then, once the write is out, show a revert
                    # entry immediately (before verify) so a verify error can't
                    # lose it — the journal is the audit trail the design leans on.
                    self.logger.journal_write(name, old_val, new_val, ok=None)  # PENDING
                    self.transport.write_setting(name, new_val, idle_timeout=2.5)
                    self._cbq.put(lambda: self._record_write(name, old_val, new_val))
                    verify = self.transport.exec_command("set", idle_timeout=4.0,
                                                         max_time=120.0)
                    live2, _ = parse_settings_dump(verify)
                    got = live2.get(name, {}).get("value", "")
                    verified = first_number(got) == first_number(new_val)
                    self.logger.journal_write(name, old_val, new_val, verified)
                    return got, verified, verify

                def done2(r2):
                    got, verified, verify_dump = r2
                    self._set_last_journal_status(got, verified)
                    self._ingest_settings(verify_dump)
                    if not verified:
                        # F8: offer an immediate revert rather than only warning —
                        # esp. important for booleans, whose accepted token on rev
                        # 41 is unverified (a "Yes" might land as its opposite)
                        if messagebox.askyesno(APP_NAME,
                                "Read-back mismatch for %s: you wrote %r but the bike "
                                "reports %r. Stage a revert to the previous value (%s) "
                                "now?" % (name, new_val, got, old_val)):
                            self._stage_revert(name, old_val)
                self._run_bg(job2, done2)

            self._run_bg(job, confirm_and_send)

        def _record_write(self, name, old, new):
            """Add a revert entry the instant a write goes out (before verify)."""
            self.journal_entries.append((name, old, new))
            self.lst_journal.insert("end", "%s: %s -> %s  [pending verify]"
                                    % (name, old, new))

        def _set_last_journal_status(self, got, verified):
            """Update the just-added entry's label after the read-back verify."""
            if not self.journal_entries:
                return
            idx = len(self.journal_entries) - 1
            name, old, new = self.journal_entries[idx]
            label = ("%s: %s -> %s  [%s]"
                     % (name, old, new, "verified" if verified
                        else "READBACK MISMATCH: %r" % got))
            try:
                self.lst_journal.delete(idx)
                self.lst_journal.insert(idx, label)
            except Exception:
                pass

        def _stage_revert(self, name, old):
            """Stage a revert as an inline pending write on the setting's row; the
            user clicks 'Write' on that row to apply it (normal confirm/backup).
            Always give feedback — never fail silently if the row is missing."""
            if name in self.tree.get_children():
                self._set_pending_write(name, first_number(old))
                self.tree.selection_set(name)
                self.tree.see(name)
                messagebox.showinfo(APP_NAME, "Revert staged in the table: %s → %s. "
                                    "Click 'Write' on that row to apply (same "
                                    "confirm/backup flow)." % (name, old))
            else:
                messagebox.showwarning(APP_NAME, "Couldn't stage the revert for '%s' "
                                       "(its row isn't in the write table right now). "
                                       "Re-read the settings, then set it back to %s "
                                       "yourself." % (name, old))

        def _revert(self):
            sel = self.lst_journal.curselection()
            if not sel:
                return
            name, old, new = self.journal_entries[sel[0]]
            self._stage_revert(name, old)

        # -- Console tab (raw commands — doesn't fit the phased flow) ---------
        def _build_console_tab(self):
            f = self._new_tab(" Console ", "Developer console",
                              "advanced — send any console command to the bike")
            warn = ttk.Frame(f)
            warn.pack(fill="x", pady=(0, 8))
            ttk.Label(warn, text="⚠  This sends RAW commands to your bike. Reads are "
                      "safe; destructive commands are NOT hard-blocked but require a "
                      "typed 'confirm'. Know what a command does before you send it.",
                      foreground=P["warn"], wraplength=860, justify="left").pack(
                          side="left", fill="x", expand=True)
            ttk.Button(warn, text="Command reference",
                       command=self._show_command_reference).pack(side="right",
                                                                  padx=(8, 0))

            self.txt_console = self._console_text(f, 22)
            self.txt_console.pack(fill="both", expand=True, pady=(0, 6))

            cmdrow = ttk.Frame(f)
            cmdrow.pack(fill="x")
            ttk.Label(cmdrow, text="›", foreground=P["green"],
                      font=(self.sty["mono"], 13, "bold")).pack(side="left", padx=(2, 6))
            self.raw_var = tk.StringVar()
            self.ent_cmd = ttk.Entry(cmdrow, textvariable=self.raw_var,
                                     font=(self.sty["mono"], 10))
            self.ent_cmd.pack(side="left", fill="x", expand=True)
            self.ent_cmd.bind("<Return>", lambda e: self._cmd_enter())
            self.ent_cmd.bind("<Up>", lambda e: self._cmd_history_nav(-1))
            self.ent_cmd.bind("<Down>", lambda e: self._cmd_history_nav(1))
            ttk.Button(cmdrow, text="Send", command=self._cmd_enter).pack(
                side="left", padx=(6, 0))
            ttk.Label(f, text="type a command · Enter sends · ↑/↓ history · dangerous "
                      "commands need a typed 'confirm'", style="Muted.TLabel").pack(
                          anchor="w", pady=(2, 0))

        # -- Analyze tab (Health / Rides / Compare / Gearing) ----------------
        def _build_analyze_tab(self):
            f = self._new_tab(" Analyze ", "Analyze the data",
                              "health, rides, and comparisons")

            top = ttk.Frame(f)
            top.pack(fill="x")
            ttk.Button(top, text="Load session folder…",
                       command=self._analyze_load).pack(side="left")
            ttk.Button(top, text="Use current session",
                       command=self._analyze_use_current).pack(side="left", padx=6)
            self.lbl_loaded = ttk.Label(top, text="no session loaded",
                                        foreground=P["dim"])
            self.lbl_loaded.pack(side="left", padx=12)

            sub = ttk.Notebook(f)
            sub.pack(fill="both", expand=True, pady=(8, 0))

            # Health
            hf = ttk.Frame(sub, padding=8)
            sub.add(hf, text=" Health ")
            cols = ("metric", "value", "status")
            self.health_tree = ttk.Treeview(hf, columns=cols, show="headings", height=12)
            for c, w in zip(cols, (200, 260, 90)):
                self.health_tree.heading(c, text=c.title())
                self.health_tree.column(c, width=w, anchor="w")
            for tag, col in (("ok", "#7fe0a0"), ("watch", P["warn"]),
                             ("alert", P["danger"]), ("info", P["fg"])):
                self.health_tree.tag_configure(tag, foreground=col)
            self.health_tree.pack(fill="both", expand=True)
            self._attach_tree_copy(self.health_tree)       # E4
            self.health_tree.bind("<<TreeviewSelect>>", self._health_note)
            # C3: make the per-row explanations discoverable — seed the note with a
            # visible hint so nobody has to guess that clicking a row explains it.
            self._health_hint = ("Tip: click any metric row for a plain-language "
                                 "explanation — what it is, how it fits the bike's "
                                 "system, and what a healthy reading looks like.")
            self.lbl_health_note = ttk.Label(hf, text=self._health_hint,
                                             wraplength=980, foreground=P["dim"],
                                             justify="left")
            self.lbl_health_note.pack(anchor="w", pady=(6, 0))

            # Rides
            rf = ttk.Frame(sub, padding=8)
            sub.add(rf, text=" Rides ")
            rbtns = ttk.Frame(rf)
            rbtns.pack(fill="x")
            ttk.Button(rbtns, text="Load ride log (.txt)…",
                       command=self._load_ride_log).pack(side="left")
            ttk.Button(rbtns, text="Get zero-log-parser",
                       command=lambda: self._open_url(
                           "https://github.com/zero-motorcycle-community/zero-log-parser")
                       ).pack(side="left", padx=8)
            self.lbl_ride_totals = ttk.Label(
                rf, text="Ride telemetry does NOT come from a normal pull — rev 41 "
                "doesn't stream it as console text. To use this tab:\n"
                "  1. export your bike's raw log (a heavy dump, or pulled off the "
                "bike's storage),\n"
                "  2. run the community zero-log-parser tool on it (button above) to "
                "get a DECODED .txt,\n"
                "  3. click 'Load ride log (.txt)…' and pick that file.\n"
                "Then you'll see per-ride distance, SOC%/km and temps here, and the "
                "ride-log charts on the Charts tab.",
                foreground=P["dim"], wraplength=920, justify="left")
            self.lbl_ride_totals.pack(anchor="w", pady=(6, 0))
            rcols = ("start", "km", "soc", "socpkm", "ptemp", "mtemp", "rpm")
            heads = ("Start", "Distance km", "SOC used %", "SOC%/km",
                     "Max pack C", "Max motor C", "Max rpm")
            self.ride_tree = ttk.Treeview(rf, columns=rcols, show="headings", height=11)
            for c, h, w in zip(rcols, heads, (150, 95, 85, 80, 80, 85, 80)):
                self.ride_tree.heading(c, text=h)
                self.ride_tree.column(c, width=w, anchor="w")
            self.ride_tree.pack(fill="both", expand=True, pady=(6, 0))
            self._attach_tree_copy(self.ride_tree)       # E4

            # Charts — plot the ride-log time series on a themed canvas (no matplotlib)
            self._build_charts_tab(sub)

            # Compare
            cf = ttk.Frame(sub, padding=8)
            sub.add(cf, text=" Compare ")
            crow = ttk.Frame(cf)
            crow.pack(fill="x")
            ttk.Button(crow, text="Add loaded/current",
                       command=self._compare_add_current).pack(side="left")
            ttk.Button(crow, text="Add folder…",
                       command=self._compare_add_folder).pack(side="left", padx=6)
            ttk.Button(crow, text="Clear",
                       command=self._compare_clear).pack(side="left")
            self.txt_compare = self._console_text(cf, 16)
            self.txt_compare.pack(fill="both", expand=True, pady=(8, 0))

            # (Gearing is a calculator, not data analysis — it lives in
            # Tools -> Gearing calculator now.)

        def _show_gearing_calc(self):
            """A re-gear planning calculator (Tools menu): enter new front/rear teeth
            to get the ratio + the spfront/sprear/rwhcirc values to write."""
            from . import dialogs
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — Gearing calculator" % APP_NAME)
            win.geometry("620x500")
            win.configure(bg=surface)
            win.transient(self)
            outer = ttk.Frame(win, padding=(16, 14))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Gearing calculator",
                      style="Heading.TLabel").pack(anchor="w")
            ttk.Label(outer, text="Enter new front/rear teeth to get the ratio and the "
                      "exact spfront/sprear/rwhcirc values to write.",
                      style="Muted.TLabel", wraplength=580).pack(anchor="w", pady=(0, 10))
            grow = ttk.Frame(outer)
            grow.pack(fill="x")
            # B5: default to FX/FXS factory gearing (20/90) — a generic calculator.
            self.gear_front = tk.StringVar(value="20")
            self.gear_rear = tk.StringVar(value="90")
            self.gear_circ = tk.StringVar(value=str(gearing_mod.DEFAULT_CIRC_MM))
            for label, var, w in (("Front teeth", self.gear_front, 6),
                                  ("Rear teeth", self.gear_rear, 6),
                                  ("Wheel circ mm", self.gear_circ, 8)):
                ttk.Label(grow, text=label + ":").pack(side="left", padx=(0, 2))
                ent = ttk.Entry(grow, textvariable=var, width=w)
                ent.pack(side="left", padx=(0, 10))
                ent.bind("<Return>", lambda e: self._gearing_compute())
            ttk.Button(grow, text="Compute", style=self.sty["accent"],
                       command=self._gearing_compute).pack(side="left")
            self.txt_gearing = self._console_text(outer, 12)
            self.txt_gearing.pack(fill="both", expand=True, pady=(10, 8))
            brow = ttk.Frame(outer)
            brow.pack(fill="x")
            ttk.Button(brow, text="Copy spfront/sprear/rwhcirc",
                       command=self._gearing_copy).pack(side="left")
            ttk.Button(brow, text="Set up writes  →",
                       command=self._start_writes_flow).pack(side="left", padx=6)
            ttk.Button(brow, text="Close", command=win.destroy).pack(side="right")
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            self._gearing_compute()      # populate with the default plan
            return win

        def _session_has_data(self, s):
            if not s:
                return False
            if (s.settings_text or "").strip():
                return True
            return any((s.cmd(c) or "").strip()
                       for c in ("bms", "stats", "status", "version"))

        def _analyze_set(self, session):
            self.analyze_session = session
            self._render_health()
            self._render_rides()
            # C6: a folder with no readable session data would render as all-n/a with
            # no hint — flag it instead of silently "loading" nothing.
            if self._session_has_data(session):
                self.lbl_loaded.config(text="loaded: %s" % session.name,
                                       foreground=P["green"])
            else:
                self.lbl_loaded.config(text="loaded: %s — no readable data"
                                       % session.name, foreground=P["warn"])
                messagebox.showwarning(APP_NAME, "That folder has no readable OpenMBB "
                    "session data (it should contain per-command .txt files like "
                    "bms.txt / stats.txt from a Read or Pull full database). The metrics "
                    "will show n/a — pick a session folder created by the app.")

        def _analyze_load(self):
            base = self._session_root()
            folder = filedialog.askdirectory(
                title="Choose a session folder to analyze",
                initialdir=base if os.path.isdir(base) else (self.log_dir or os.getcwd()))
            if not folder:
                return
            try:
                self._analyze_set(sessions.load_session(folder))
            except Exception as e:
                messagebox.showerror(APP_NAME, "Couldn't load session:\n%s" % e)

        def _analyze_use_current(self):
            """Load the live session into Analyze. Returns True on success so
            callers (e.g. the Read banner) only navigate when there's data."""
            if not self.logger:
                messagebox.showinfo(APP_NAME, "No live session yet — connect and "
                                    "capture first, or load a saved folder.")
                return False
            if self._busy:      # D7: a capture is mid-write; files are partial
                messagebox.showinfo(APP_NAME, "A capture is still running — wait for "
                                    "it to finish before analyzing the current session.")
                return False
            self._analyze_set(sessions.load_session(self.logger.dir))
            return True

        def _render_health(self):
            self.health_tree.delete(*self.health_tree.get_children())
            self._health_notes = {}
            if not self.analyze_session:
                return
            help_map = self._analyze_help_map()
            for i, m in enumerate(health_mod.health_snapshot(
                    self.analyze_session, config.get_temp_units())):
                iid = str(i)
                self.health_tree.insert("", "end", iid=iid, tags=(m["status"],),
                                        values=(m["label"], m["value"],
                                                m["status"].upper()))
                self._health_notes[iid] = self._enrich_note(m, help_map)

        def _health_note(self, _evt=None):
            sel = self.health_tree.selection()
            note = self._health_notes.get(sel[0], "") if sel else ""
            self.lbl_health_note.config(text=note or self._health_hint)

        def _analyze_help_map(self):
            """T4: lazy-load the novice explanations (assets/analyze_help.json),
            keyed by metric label. Empty dict if unavailable — enrichment is
            purely additive, so a missing file never breaks Analyze."""
            cached = getattr(self, "_analyze_help_cache", None)
            if cached is not None:
                return cached
            data = {}
            try:
                from importlib.resources import files
                import json
                raw = (files("openmbb") / "assets" / "analyze_help.json"
                       ).read_text(encoding="utf-8")
                for it in json.loads(raw):
                    k = str(it.get("key") or it.get("label") or "").strip().lower()
                    if k:
                        data[k] = it
            except Exception:
                data = {}
            self._analyze_help_cache = data
            return data

        def _enrich_note(self, m, help_map):
            """Append the plain-language explanation (what it is / how it fits the
            system / healthy range) to a health metric's status note."""
            note = m.get("note") or ""
            it = help_map.get(str(m.get("label", "")).strip().lower())
            if it:
                extra = []
                if it.get("what"):
                    extra.append("What it is — %s" % it["what"])
                if it.get("how_it_fits"):
                    extra.append("In the system — %s" % it["how_it_fits"])
                if it.get("healthy_note"):
                    extra.append("Healthy — %s" % it["healthy_note"])
                if extra:
                    note = (note + "\n\n" if note else "") + "\n".join(extra)
            return note

        def _render_rides(self):
            # A1: rev-41 has no console command that streams ride telemetry as text
            # (`dumplogs` doesn't exist). Ride analysis is sourced from an external
            # zero-log-parser export via 'Load ride log (.txt)'. Old sessions that
            # DID capture a `dumplogs` file still render for backward compatibility.
            self.ride_tree.delete(*self.ride_tree.get_children())
            legacy = self.analyze_session.cmd("dumplogs") if self.analyze_session else ""
            recs = parsers.parse_ride_log(legacy)
            if recs:
                self._render_ride_records(recs, "session dumplogs (legacy capture)")
                return
            self._ride_records = []
            self._render_charts()
            self.lbl_ride_totals.config(
                text="Ride telemetry isn't a console command on this firmware — use "
                     "'Load ride log (.txt)' above to analyze a zero-log-parser export.")

        def _render_ride_records(self, recs, source):
            self.ride_tree.delete(*self.ride_tree.get_children())
            self._ride_records = list(recs or [])
            self._render_charts()                    # keep the Charts tab in sync
            summ = rides.summarize_rides(recs)
            t = summ["totals"]
            if not summ["rides"]:
                self.lbl_ride_totals.config(text="No riding records found (%s)." % source)
                return
            # E6: show distances in the user's chosen unit (default km, the bike's own)
            unit = "mi" if config.get_units() == "mi" else "km"
            f = 0.621371 if unit == "mi" else 1.0
            self.ride_tree.heading("km", text="Distance %s" % unit)

            def _d(km):
                return round(km * f, 1) if isinstance(km, (int, float)) else km

            self.lbl_ride_totals.config(
                text="%s: %d rides · %.1f %s · mean %s SOC%%/km · max pack %s C / "
                "motor %s C · %d samples"
                % (source, t["ride_count"], _d(t["total_km"]), unit,
                   t["mean_soc_per_km"], t["max_pack_temp_c"],
                   t["max_motor_temp_c"], t["samples"]))
            for r in summ["rides"]:
                self.ride_tree.insert("", "end", values=(
                    r["start_ts"] or "?", _d(r["distance_km"]), r["soc_used_pct"],
                    r["soc_per_km"] if r["soc_per_km"] is not None else "n/a",
                    r["max_pack_temp_c"] if r["max_pack_temp_c"] is not None else "n/a",
                    r["max_motor_temp_c"] if r["max_motor_temp_c"] is not None else "n/a",
                    r["max_rpm"] if r["max_rpm"] is not None else "n/a"))

        # -- Analyze: Charts (dependency-free tk.Canvas plots) ---------------
        # ride-log time series + cross-session trends (one point per saved pull).
        _CHART_METRICS = ("SOC vs distance", "Pack voltage vs distance",
                          "Temperatures vs distance", "Motor current vs distance",
                          "SOC vs pack voltage", "Efficiency per ride (bar)",
                          "Trend: pack capacity", "Trend: charge cycles",
                          "Trend: max battery temp", "Trend: max motor temp")

        # cross-session trends: label -> (extract(bms, stats), y-label, is_temp)
        _SESSION_TRENDS = {
            "Trend: pack capacity": (lambda b, s: b.get("capacity_ah"),
                                     "pack capacity (Ah)", False),
            "Trend: charge cycles": (lambda b, s: b.get("cycles"),
                                     "charge cycles", False),
            "Trend: max battery temp": (lambda b, s: s.get("max_batt_temp_c"),
                                        "max battery temp", True),
            "Trend: max motor temp": (lambda b, s: s.get("max_motor_temp_c"),
                                      "max motor temp", True),
        }

        def _build_charts_tab(self, sub):
            self._ride_records = []
            cf = ttk.Frame(sub, padding=8)
            sub.add(cf, text=" Charts ")
            row = ttk.Frame(cf)
            row.pack(fill="x")
            ttk.Label(row, text="Plot:").pack(side="left")
            self.chart_metric = tk.StringVar(value=self._CHART_METRICS[0])
            self.cbo_chart = ttk.Combobox(row, textvariable=self.chart_metric,
                                          state="readonly", width=26,
                                          values=list(self._CHART_METRICS))
            self.cbo_chart.pack(side="left", padx=6)
            self.cbo_chart.bind("<<ComboboxSelected>>",
                                lambda e: self._render_charts())
            ttk.Label(row, text="ride-log series, or 'Trend:' across your saved pulls",
                      style="Muted.TLabel").pack(side="left", padx=8)
            self.chart_canvas = tk.Canvas(cf, highlightthickness=0,
                                          bg=P["console"], height=380)
            self.chart_canvas.pack(fill="both", expand=True, pady=(8, 0))
            # redraw responsively; debounce so a resize drag isn't a redraw storm
            self.chart_canvas.bind("<Configure>", self._chart_on_resize)

        def _chart_on_resize(self, _evt=None):
            if getattr(self, "_chart_resize_job", None):
                try:
                    self.after_cancel(self._chart_resize_job)
                except Exception:
                    pass
            self._chart_resize_job = self.after(120, self._render_charts)

        def _fmt_tick(self, v):
            return "%d" % round(v) if abs(v - round(v)) < 1e-9 else "%.1f" % v

        def _chart_size(self, cv):
            # winfo_width() is 1 until the canvas is laid out (and in headless
            # tests) — fall back to a sensible default so we still draw.
            w, h = cv.winfo_width(), cv.winfo_height()
            return (w if w > 1 else 700), (h if h > 1 else 380)

        def _chart_msg(self, cv, text):
            w, h = self._chart_size(cv)
            cv.create_text(w / 2, h / 2, text=text, fill=P["dim"],
                           justify="center", width=max(240, w - 80),
                           font=(self.sty["ui"], 10))

        def _load_trend_sessions(self):
            """Saved sessions (oldest->newest) parsed to (mtime, name, bms, stats),
            cached so a chart resize doesn't re-read the folders each frame.
            Invalidated when a new pull is saved (see _baseline)."""
            cached = getattr(self, "_trend_cache", None)
            if cached is not None:
                return cached
            out = []
            try:
                root, names = self._recent_sessions(limit=60)
                for name in reversed(names):     # oldest first
                    try:
                        folder = os.path.join(root, name)
                        s = sessions.load_session(folder)
                        out.append((os.path.getmtime(folder), name,
                                    parsers.parse_bms(s.cmd("bms")),
                                    parsers.parse_stats(s.cmd("stats"))))
                    except Exception:
                        pass
            except Exception:
                out = []
            out.sort(key=lambda r: r[0])         # oldest -> newest by capture time
            self._trend_cache = out
            return out

        def _render_session_trend(self, cv, metric):
            extract, ylabel, is_temp = self._SESSION_TRENDS[metric]
            tu = config.get_temp_units()
            pts = []
            for mt, _name, bms, stats in self._load_trend_sessions():
                v = extract(bms, stats)
                if isinstance(v, (int, float)):
                    if is_temp and tu == "F":
                        v = round(v * 9 / 5 + 32)
                    pts.append((mt, v))          # x = capture time (a real timeline)
            if not pts:
                self._chart_msg(cv, "No saved sessions with this metric yet.\n\nPull "
                                "full database on several visits to build a trend "
                                "over time.")
                return
            if is_temp:
                ylabel = "%s (°%s)" % (ylabel, tu)

            def _datefmt(t):
                try:
                    return _dt.datetime.fromtimestamp(t).strftime("%m/%d")
                except Exception:
                    return ""
            self._chart_line(cv, [(metric.replace("Trend: ", ""), P["green"], pts)],
                             "date", ylabel, dots=True, xfmt=_datefmt)

        def _render_charts(self):
            cv = getattr(self, "chart_canvas", None)
            if cv is None:
                return
            cv.delete("all")
            metric = self.chart_metric.get()
            if metric in self._SESSION_TRENDS:      # cross-session trend (no ride log)
                self._render_session_trend(cv, metric)
                return
            recs = getattr(self, "_ride_records", [])
            unit = config.get_units()
            dfac = 0.621371 if unit == "mi" else 1.0
            dlabel = "distance (%s)" % ("mi" if unit == "mi" else "km")
            tu = config.get_temp_units()

            def tconv(c):
                return round(c * 9 / 5 + 32) if tu == "F" else c

            if not recs:
                self._chart_msg(cv, "No ride log loaded.\n\nAnalyze → Rides → 'Load "
                                "ride log (.txt)…' to plot the ride telemetry here.")
                return
            base = min((r["odo_km"] for r in recs
                        if isinstance(r.get("odo_km"), (int, float))), default=0.0)

            def dist_series(ykey, conv=None):
                pts = charts_mod.series_from(recs, "odo_km", ykey)
                return [((x - base) * dfac, (conv(y) if conv else y)) for x, y in pts]

            if metric == "SOC vs distance":
                self._chart_line(cv, [("SOC %", P["green"], dist_series("soc"))],
                                 dlabel, "state of charge (%)")
            elif metric == "Pack voltage vs distance":
                self._chart_line(cv, [("Vpack", "#5aa8ff", dist_series("vpack"))],
                                 dlabel, "pack voltage (V)")
            elif metric == "Temperatures vs distance":
                self._chart_line(cv, [
                    ("pack", P["warn"], dist_series("pack_temp_c", tconv)),
                    ("motor", P["danger"], dist_series("motor_temp_c", tconv))],
                    dlabel, "temperature (°%s)" % tu)
            elif metric == "Motor current vs distance":
                self._chart_line(cv, [("motor A", "#8fd0ff", dist_series("motamps"))],
                                 dlabel, "motor current (A)")
            elif metric == "SOC vs pack voltage":
                pts = charts_mod.series_from(recs, "vpack", "soc")
                self._chart_line(cv, [("SOC", P["green"], pts)],
                                 "pack voltage (V)", "state of charge (%)", dots=True)
            elif metric == "Efficiency per ride (bar)":
                summ = rides.summarize_rides(recs)
                bars = [(r["start_ts"] or ("ride %d" % (i + 1)),
                         (r["soc_per_km"] / dfac))
                        for i, r in enumerate(summ["rides"])
                        if r["soc_per_km"] is not None]
                if not bars:
                    self._chart_msg(cv, "No per-ride efficiency yet — the log needs "
                                    "distance + SOC samples.")
                    return
                self._chart_bar(cv, [b[0] for b in bars], [b[1] for b in bars],
                                "SOC %% used per %s" % ("mi" if unit == "mi" else "km"))

        def _chart_line(self, cv, series, xlabel, ylabel, dots=False, xfmt=None):
            xfmt = xfmt or self._fmt_tick        # x-tick label formatter (e.g. dates)
            series = [(lbl, col, charts_mod.downsample(pts))
                      for lbl, col, pts in series if pts]
            allpts = [p for _, _, pts in series for p in pts]
            if not allpts:
                self._chart_msg(cv, "Not enough data to plot this metric.")
                return
            w, h = self._chart_size(cv)
            x0, y0, x1, y1 = 60, 16, w - 18, h - 42
            if x1 - x0 < 60 or y1 - y0 < 60:
                return
            xs = [p[0] for p in allpts]
            ys = [p[1] for p in allpts]
            xlo, xhi, xstep = charts_mod.nice_bounds(min(xs), max(xs))
            ylo, yhi, ystep = charts_mod.nice_bounds(min(ys), max(ys))

            def sx(x):
                return x0 + (x - xlo) / (xhi - xlo) * (x1 - x0)

            def sy(y):
                return y1 - (y - ylo) / (yhi - ylo) * (y1 - y0)

            grid, axcol, fg = "#2a2d38", P["dim"], P["fg"]
            for xt in charts_mod.axis_ticks(xlo, xhi, xstep):
                gx = sx(xt)
                cv.create_line(gx, y0, gx, y1, fill=grid)
                cv.create_text(gx, y1 + 5, text=xfmt(xt), fill=axcol,
                               anchor="n", font=(self.sty["mono"], 8))
            for yt in charts_mod.axis_ticks(ylo, yhi, ystep):
                gy = sy(yt)
                cv.create_line(x0, gy, x1, gy, fill=grid)
                cv.create_text(x0 - 5, gy, text=self._fmt_tick(yt), fill=axcol,
                               anchor="e", font=(self.sty["mono"], 8))
            cv.create_rectangle(x0, y0, x1, y1, outline=axcol)
            cv.create_text((x0 + x1) / 2, h - 10, text=xlabel, fill=fg,
                           font=(self.sty["ui"], 9))
            cv.create_text(12, (y0 + y1) / 2, text=ylabel, fill=fg, angle=90,
                           font=(self.sty["ui"], 9))
            for lbl, col, pts in series:
                coords = []
                for x, y in pts:
                    coords += [sx(x), sy(y)]
                if len(coords) >= 4 and not dots:
                    cv.create_line(*coords, fill=col, width=2)
                if dots or len(pts) == 1:
                    for x, y in pts:
                        cx, cy = sx(x), sy(y)
                        cv.create_oval(cx - 1.6, cy - 1.6, cx + 1.6, cy + 1.6,
                                       fill=col, outline="")
            if len(series) > 1:
                lx, ly = x1 - 84, y0 + 10
                for lbl, col, _ in series:
                    cv.create_line(lx, ly, lx + 16, ly, fill=col, width=3)
                    cv.create_text(lx + 22, ly, text=lbl, fill=fg, anchor="w",
                                   font=(self.sty["mono"], 8))
                    ly += 15

        def _chart_bar(self, cv, labels, values, ylabel):
            w, h = self._chart_size(cv)
            x0, y0, x1, y1 = 60, 16, w - 18, h - 54
            if x1 - x0 < 60 or y1 - y0 < 60 or not values:
                return
            ylo, yhi, ystep = charts_mod.nice_bounds(min(0, min(values)), max(values))

            def sy(y):
                return y1 - (y - ylo) / (yhi - ylo) * (y1 - y0)

            grid, axcol, fg = "#2a2d38", P["dim"], P["fg"]
            for yt in charts_mod.axis_ticks(ylo, yhi, ystep):
                gy = sy(yt)
                cv.create_line(x0, gy, x1, gy, fill=grid)
                cv.create_text(x0 - 5, gy, text=self._fmt_tick(yt), fill=axcol,
                               anchor="e", font=(self.sty["mono"], 8))
            cv.create_rectangle(x0, y0, x1, y1, outline=axcol)
            n = len(values)
            slot = (x1 - x0) / n
            bw = min(48, slot * 0.6)
            for i, (lbl, val) in enumerate(zip(labels, values)):
                bx = x0 + slot * i + (slot - bw) / 2
                cv.create_rectangle(bx, sy(val), bx + bw, sy(0), fill=P["green"],
                                    outline="")
                cv.create_text(bx + bw / 2, sy(val) - 5, text=self._fmt_tick(val),
                               fill=fg, anchor="s", font=(self.sty["mono"], 8))
                short = str(lbl)[-8:]
                cv.create_text(bx + bw / 2, y1 + 5, text=short, fill=axcol,
                               anchor="ne", angle=35, font=(self.sty["mono"], 7))
            cv.create_text(12, (y0 + y1) / 2, text=ylabel, fill=fg, angle=90,
                           font=(self.sty["ui"], 9))

        def _load_ride_log(self):
            path = filedialog.askopenfilename(
                title="Load a zero-log-parser decoded ride log (.txt)",
                filetypes=[("Text log", "*.txt"), ("All files", "*.*")])
            if not path:
                return
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as e:
                messagebox.showerror(APP_NAME, "Couldn't read file:\n%s" % e)
                return
            self._render_ride_records(parsers.parse_ride_log(text),
                                      os.path.basename(path))

        def _compare_out(self, text):
            self.txt_compare.config(state="normal")
            self.txt_compare.insert("end", text + "\n")
            self.txt_compare.see("end")
            self.txt_compare.config(state="disabled")

        def _compare_add(self, s):
            if any(x.dir == s.dir for x in self.compare_list):
                messagebox.showinfo(APP_NAME, "That session is already in the "
                                    "comparison.")
                return
            self.compare_list.append(s)
            self._render_compare()

        def _compare_add_current(self):
            if self._busy and not self.analyze_session:   # D7: don't read a live
                messagebox.showinfo(APP_NAME, "A capture is still running — wait for "  # capture mid-write
                                    "it to finish before adding the current session.")
                return
            s = self.analyze_session
            if not s and self.logger:
                s = sessions.load_session(self.logger.dir)
            if not s:
                messagebox.showinfo(APP_NAME, "Load or capture a session first.")
                return
            self._compare_add(s)

        def _compare_add_folder(self):
            folder = filedialog.askdirectory(
                title="Add a session folder to the comparison",
                initialdir=self._session_root())
            if not folder:
                return
            try:
                s = sessions.load_session(folder)
            except Exception as e:
                messagebox.showerror(APP_NAME, "Couldn't load session:\n%s" % e)
                return
            self._compare_add(s)

        def _compare_clear(self):
            self.compare_list = []
            self.txt_compare.config(state="normal")
            self.txt_compare.delete("1.0", "end")
            self.txt_compare.config(state="disabled")

        def _render_compare(self):
            self.txt_compare.config(state="normal")
            self.txt_compare.delete("1.0", "end")
            self.txt_compare.config(state="disabled")
            # order oldest->newest by folder mtime (works for any folder name);
            # stable sort keeps insertion order when mtimes tie/are unavailable.
            def _mtime(s):
                try:
                    return os.path.getmtime(s.dir)
                except OSError:
                    return 0
            ordered = sorted(self.compare_list, key=_mtime)
            self._compare_out("Comparing %d session(s), oldest -> newest:"
                              % len(ordered))
            for s in ordered:
                self._compare_out("  - %s" % s.name)
            if len(ordered) < 2:
                self._compare_out("\nAdd a second session to see the diff and trends.")
                return
            res = compare_mod.compare_sessions(ordered)
            self._compare_out("\nSETTINGS CHANGED (%s -> %s):"
                              % (ordered[0].name, ordered[-1].name))
            if res["settings_diff"]:
                for name, old, new in res["settings_diff"]:
                    self._compare_out("  %-16s %s -> %s" % (name, old, new))
            else:
                self._compare_out("  (none)")
            self._compare_out("\nLEARNED PACK CAPACITY:")
            for n, cap in res["capacity_trend"]:
                self._compare_out("  %-28s %s Ah" % (n, cap if cap is not None else "n/a"))
            self._compare_out("\nEFFECTIVE GEARING RATIO:")
            for n, r, basis in res["gearing_trend"]:
                self._compare_out("  %-28s %-9s [%s]"
                                  % (n, ("%.2f:1" % r) if r is not None else "n/a",
                                     basis or "?"))

        def _gearing_plan(self):
            try:
                front = int(self.gear_front.get())
                rear = int(self.gear_rear.get())
                circ = int(float(self.gear_circ.get()))
            except ValueError:
                return None, "Enter whole numbers for teeth and wheel circumference."
            if front <= 0 or rear <= 0 or circ <= 0:
                return None, "Teeth counts and wheel circumference must be positive."
            return gearing_mod.gearing_plan(front, rear, circ), None

        def _gearing_compute(self):
            plan, err = self._gearing_plan()
            self.txt_gearing.config(state="normal")
            self.txt_gearing.delete("1.0", "end")
            if err:
                self.txt_gearing.insert("end", err)
            else:
                lines = [
                    gearing_mod.describe_plan(plan),
                    "",
                    "  Ratio            : %.3f:1" % plan["ratio"],
                    "  vs stock 4.50:1  : %+.1f%%  (%s)"
                    % (plan["vs_ref_pct"],
                       "taller" if plan["taller_than_ref"] else "shorter"),
                    "  Top-speed factor : %.3fx" % plan["top_speed_factor"],
                    "  Motor rev/km     : %.0f" % plan["revs_per_km"],
                    "  Closest setup    : %s" % plan["nearest"],
                    "",
                    "MBB settings to write (one at a time, via the Writes tab):",
                    "  spfront = %d" % plan["spfront"],
                    "  sprear  = %d" % plan["sprear"],
                    "  rwhcirc = %d mm   (then trim against GPS)" % plan["rwhcirc"],
                ]
                self.txt_gearing.insert("end", "\n".join(lines))
            self.txt_gearing.config(state="disabled")

        def _gearing_copy(self):
            plan, err = self._gearing_plan()
            if err:
                messagebox.showerror(APP_NAME, err)
                return
            text = "spfront=%d sprear=%d rwhcirc=%d" % (
                plan["spfront"], plan["sprear"], plan["rwhcirc"])
            self.clipboard_clear()
            self.clipboard_append(text)
            messagebox.showinfo(APP_NAME, "Copied:\n%s" % text)

    return App()
