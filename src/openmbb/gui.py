"""Tkinter GUI: four phase-gated panels (Connect / Read / Login / Writes) plus
an always-available Analyze tab (Health / Condition / Rides / Charts / Compare)."""

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

from . import APP_NAME, __release_date__, __version__
from . import version as version_mod
from . import charts as charts_mod
from . import compare as compare_mod
from . import condition as condition_mod
from . import redact as redact_mod
from . import report as report_mod
from . import gearing as gearing_mod
from . import library as library_mod
from . import health as health_mod
from . import parsers, rides, sessions
from .safety import (READONLY_GUARDS, REFUSED_ON_REV41,
                     REV41_FXS_SETTINGS, WRITE_PANEL_CONTEXT,
                     WRITE_WHITELIST, command_blocked)
from .sim import SimPort
from . import theme as theme_mod
from .theme import PALETTE, apply_theme
from .transport import (DUMP_COMMANDS, HEAVY_COMMANDS, LONG_COMMANDS,
                        READ_COMMANDS, ConsoleRebootError, SessionLogger, Transport,
                        first_number, list_serial_ports, looks_like_prompt,
                        nonprintable_ratio, open_real_port, parse_settings_dump,
                        timeouts_for)

# Connect-tab / cable-wizard button labels. Cable verification (receive-only) is
# offered via the "Test your cable" wizard; the live connect is a single button.
VERIFY_LABEL = "Test your cable"
CONNECT_LABEL = "Connect"

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
    "bluetooth": "Bluetooth radio status (connected / discoverable / idle).",
    "obd": "OBD summary (protocol, DTCs).",
    "set": "All tunable settings — the backup source a write reads first. Shows "
           "identity only until you log in; the tunables appear after login.",
    "errorlogdump": "The small error log (~1 KB, safe).",
    "eventlogdump": "The FULL event log (~1 MB, minutes) — can briefly OPEN the "
                    "drivetrain contactor. Park the bike first.",
    "dumpall": "Everything incl. logs (~1 MB, minutes) — same contactor caveat. "
               "Rarely needed: a full pull with the '＋ event log' opt-in already "
               "captures the same data (stats/inputs/settings + the event log).",
}


def baseline_read_order():
    """The command order a Pull full database runs (excluding the heavy event log):
    the command reads, then `set` (the settings backup), the small error log, and
    `obd` LAST — obd's live output is unproven, so a stall can't delay the backup.
    The Read tab's command buttons are laid out in THIS SAME order, so the green
    'captured' borders fill in row by row (left→right, top→bottom) as the pull runs
    — one source of truth keeps the buttons and the pull sequence in lock-step."""
    reads = [c for c in READ_COMMANDS if c != "obd"]
    return reads + ["set"] + DUMP_COMMANDS + (["obd"] if "obd" in READ_COMMANDS else [])


def _trend_gearing_ratio(bms, stats):
    """Per-session lifetime-average effective ratio from the odometer (default wheel
    circ — the trend cache doesn't carry per-session rwhcirc, and a constant circ
    preserves the SHAPE a trend shows; it steps after a re-gear once km accrue). The
    delta-based 'current' ratio lives in compare.compare_sessions (Compare tab)."""
    rev, km = stats.get("odo_motor_rev"), stats.get("odo_km")
    if not rev or not km:
        return None
    return rides.effective_ratio(rides.revs_per_km(rev, km),
                                 gearing_mod.DEFAULT_CIRC_MM)


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
  boot banner). Then "Connect" wakes the console prompt and reads the
  firmware version. Garbage output at 38400 baud usually means the Tx/Rx wires
  are swapped — stop and recheck.

READ
  Click any command button for a one-off read. To advance, click the blue
  Pull full database button: it runs the command reads + the full settings dump
  (your backup) + the small error log — NO heavy dumps. Individual reads do NOT
  unlock the writes flow — only Pull full database does, so a backup exists
  before any change. The heavy log reads (eventlogdump / dumpall) sit behind
  their OWN buttons and confirm first: on a keyed-on bike a long ~1 MB dump can
  make the BMS briefly OPEN the drivetrain contactor (a click + flashing dash; it
  recovers when the read finishes). They are NOT part of the routine pull.

LOGIN
  Explicit and READ-ONLY — it only reveals the tunable settings. The box is
  pre-filled with the last password that worked (or a community-known one); press
  Login, or type a different one. A failed attempt just leaves you read-only;
  success unlocks Writes. Passwords are masked in the logs and never written to
  disk unless YOU say yes when it offers to remember one after a successful login
  (clear saved ones via Tools → Settings → Login) — nothing to hand-edit.

WRITES
  Triple-gated: logged in + the master UNLOCK WRITES switch + a per-write
  confirm dialog. Only whitelisted settings that actually exist on your bike
  appear. Click a row's New value cell to type a value, then click Write. Each
  write re-reads the current value, backs up all settings, sends the change,
  reads it back to verify, and journals it to disk. Changed a setting? Its row
  shows ↺ Put back <value> — the value it held before YOUR first write to it
  this session. A row the BIKE moved is marked "moved by your <name> write" and
  is not offered as a write at all.

ANALYZE (always available, no bike needed)
  Reads a saved session folder (or the current one) and interprets it:
    - Health : SOC vs voltage, cell balance/spread, capacity, temps, cycles,
               odometer, efficiency and the effective gearing ratio. Rows with a
               threshold are flagged ok / watch / alert; the rest are
               informational. Click a row for a plain-language explanation.
    - Conditn: what the ride and charge SAMPLES say about the pack, ending in a
               verdict — healthy / worth a look / walk away / CANNOT TELL. It
               grades only what one capture can judge with no second bike to
               compare against, and names every check it could not answer rather
               than letting silence read as a pass. Also flags a statistics RESET
               and shows the bike's MBB/BMS/dash clocks against your own.
    - Rides  : per-ride distance, SOC per distance, and temps read STRAIGHT OFF
               THE BIKE — 'Pull ride log from bike' runs the console's
               eventlogdump (decoded text). No Zero app, no external decoder. Or
               load a saved .txt. Distances and temps follow your unit setting.
    - Charts : plot the ride-log series, or a 'Trend:' metric (pack capacity,
               charge cycles, temps, effective gearing) across your real pulls
               over time — pick a date Range, or drag to zoom (double-click resets).
    - Compare: pick 2+ sessions to see exactly which settings changed; the
               over-time trends live on the Charts tab.

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
exactly 0 disables it entirely (the write flow warns).
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
            self.sty = apply_theme(self, config.get_theme())
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
            self._baseline_settings = {}   # {name: value} from the last clean full read
            # WHO moved what. `_user_wrote` maps a setting to the value it held
            # immediately before this session's FIRST write to it - so Reset
            # always means "undo everything I did to this row", not "undo the
            # last step". `_moved_by` maps a row the BIKE changed to the write
            # that provoked it. Both come from the write flow; neither is a diff.
            self._user_wrote = {}
            self._moved_by = {}
            self._cmd_history = []      # raw command-line history (↑/↓)
            self._cmd_hist_idx = 0
            self._busy = False
            # A1: a heavy-read (eventlogdump/dumpall/ride-log/heavy-Pull) shows a
            # BLOCKING modal until the user clicks Continue. _read_modal_up is the
            # real software interlock (grab_set only stops human clicks, not
            # after()-timers or programmatic invokes); the widgets are stored so
            # progress/teardown can reach them; _last_read_error is stashed for the
            # modal finalizer (on_error is called zero-arg).
            self._read_modal_up = False
            self._read_modal_win = None
            self._read_modal_status = None
            self._read_modal_bar = None
            self._read_modal_btnrow = None
            self._last_read_error = None
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
            # _refresh_sim_badge used to run only from the toggle, so `openmbb
            # --sim` launched with no badge and no title tag - which IS the
            # bike-day scenario: a simulator window left open and mistaken for
            # the bike. Painted once here, after the widgets exist.
            self.after(0, self._refresh_sim_badge)
            self._build_menubar()
            self._build_statusbar()
            self._build_bottom_bar()   # T3: global safe-quit + "done" hub
            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            # C7: a click on a locked tab is silently ignored by Tk — intercept it
            # and say what unlocks that phase instead.
            self.nb.bind("<Button-1>", self._on_tab_click)
            # Analyze defaults to the live session (auto-loads on first open)
            self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")
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
            """Match the OS title bar to the app theme (Windows 10 2004+/11).
            Best-effort and Windows-only; never blocks launch on other platforms
            or older builds. Re-called on a theme switch so the bar follows."""
            if sys.platform != "win32":
                return
            try:
                import ctypes
                self.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                value = ctypes.c_int(1 if theme_mod.current_mode() == "dark" else 0)
                # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (build >= 19041); 19 before that
                for attr in (20, 19):
                    if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, attr, ctypes.byref(value),
                            ctypes.sizeof(value)) == 0:
                        break
            except Exception as exc:         # cosmetic only — never block launch
                print("dark title bar unavailable: %s" % exc)

        # -- appearance ------------------------------------------------------
        def _surface(self):
            """The current ttk frame background — what raw-tk widgets sit on."""
            try:
                return ttk.Style().lookup("TFrame", "background") or P["bg"]
            except tk.TclError:
                return P["bg"]

        def _set_theme(self, mode):
            """Switch appearance live and remember the choice.

            sv-ttk restyles every ttk widget in place, which is most of the UI
            and free. The raw-tk widgets Tk makes us paint ourselves — console
            panes, the scrolling canvases, the status cells — have to be
            repainted here. Menus and the info/settings windows are rebuilt each
            time they open, so they pick the new palette up on their own.
            """
            if mode == theme_mod.current_mode():
                return
            old = self._surface()
            self.sty = apply_theme(self, mode)
            config.set_theme(mode)
            new = self._surface()
            self._cell_bg = new              # idle status cells sit on the surface
            self._apply_dark_titlebar()      # follows the mode now, not always dark
            self._repaint_raw_tk(self, old, new)
            self._restyle_trees()            # tag colours don't follow sv-ttk
            self._paint_verdict()            # nor does a widget-set foreground

        # Treeview tag colours are copied into the widget by tag_configure, so
        # unlike the ttk styles sv-ttk restyles for us they do NOT follow a live
        # theme switch — they keep whatever palette was current when the tree was
        # built. Left unre-applied, light mode painted INFO rows in the dark
        # palette's near-white #e6e6e6 on a white surface (invisible), and the
        # Writes risk column — SAFE vs CAUTION — in its palest colours.
        _TREE_TAGS = {
            "health_tree": (("ok", "green"), ("watch", "warn"),
                            ("alert", "danger"), ("info", "fg")),
            # "moved" = the bike changed this row in response to a write of
            # ours. Dim, because it is information rather than an affordance.
            "tree": (("safe", "green"), ("caution", "warn"),
                     ("pending", "green"), ("reset", "accent"),
                     ("moved", "dim")),
            "cond_tree": (("measured", "fg"), ("attention", "warn"),
                          ("unknown", "dim")),
        }

        def _restyle_trees(self):
            """(Re-)apply every Treeview tag colour from the live palette."""
            for attr, tags in self._TREE_TAGS.items():
                tree = getattr(self, attr, None)
                if tree is None:
                    continue
                for tag, key in tags:
                    tree.tag_configure(tag, foreground=P[key])
            # the session library is a transient window rather than a tab, so it
            # is not in _TREE_TAGS; repaint it when it happens to be open
            lib = getattr(self, "_lib_tree", None)
            if lib is not None:
                try:
                    for tag, colour in self._LIB_TAGS:
                        lib.tag_configure(tag, foreground=P[colour])
                except tk.TclError:
                    self._lib_tree = None      # window has since closed

        def _repaint_raw_tk(self, widget, old, new):
            """Repaint the non-ttk widgets under `widget` for the active palette.

            Frames and labels are only repainted when they still carry the OLD
            surface colour: that leaves anything deliberately coloured — a status
            cell mid-pull, a RISK chip — exactly as the app set it.
            """
            same = lambda a, b: str(a).lower() == str(b).lower()   # noqa: E731
            for child in widget.winfo_children():
                cls = child.winfo_class()
                try:
                    if cls == "Text":
                        child.configure(bg=P["console"], fg=P["termfg"],
                                        insertbackground=P["fg"],
                                        selectbackground=P["sel"],
                                        selectforeground=P["selfg"],
                                        highlightbackground=P["panel"],
                                        highlightcolor=P["panel"])
                    elif cls == "Listbox":
                        child.configure(bg=P["console"], fg=P["termfg"],
                                        selectbackground=P["sel"],
                                        selectforeground=P["fg"],
                                        highlightbackground=P["panel"],
                                        highlightcolor=P["panel"])
                    elif cls == "Canvas":
                        child.configure(bg=new)
                    elif cls in ("Frame", "Label") and same(child.cget("bg"), old):
                        child.configure(bg=new)
                        if cls == "Label":
                            child.configure(fg=P["fg"])
                except tk.TclError:
                    pass                     # a widget without that option; skip it
                self._repaint_raw_tk(child, old, new)

        # -- landing / front door (T1) --------------------------------------
        def _build_landing(self):
            """A guided 'front door' shown over the notebook at startup: a short
            blurb + the two entry actions (verify the cable, or connect & read).
            Calm/modern; hands off into the existing Connect flow."""
            lf = ttk.Frame(self)
            self.landing = lf
            # (the simulator state shows once, in the status bar — no landing badge)
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
                       command=self._open_session_library
                       ).pack(side="left", padx=10, ipady=8)
            ttk.Button(brow, text="Connect", width=20,
                       style=self.sty["accent"],
                       command=self._landing_connect
                       ).pack(side="left", padx=10, ipady=8)

            # How old this copy is - a local, checkable fact - shown only once it
            # is worth mentioning. Never "an update is available": this program
            # makes no network requests and cannot know that. Silent for every
            # uncertain case, including a host clock that disagrees with the
            # build date. See version.py for why each clause is there.
            notice = version_mod.stale_notice(__version__, __release_date__)
            if notice:
                nf = ttk.Frame(inner)
                nf.pack(pady=(26, 0))
                ttk.Label(nf, text=notice, style="Muted.TLabel",
                          justify="center", wraplength=560).pack()
                link = ttk.Label(nf, text="Open the releases page",
                                 style="Muted.TLabel", cursor="hand2")
                link.pack(pady=(4, 0))
                link.configure(foreground=P["accent"])
                link.bind("<Button-1>", lambda _e: self._open_releases())

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
            prep info, run the (listen-only) test, then offer Connect / Retry /
            Cancel — no jumping to the Connect tab with its own buttons."""
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
                def _wizard_refresh():
                    ports = list_serial_ports()
                    cbo.config(values=ports)
                    self._pick_lone_port(ports)

                ttk.Button(prow, text="Refresh",
                           command=_wizard_refresh).pack(side="left")
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
                                     (CONNECT_LABEL, on_connect, True)])
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
                        selectforeground=P["selfg"],
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
        def _unbind_menu_dismiss(self):
            """Drop the main-window click-to-dismiss binding that's live only while a
            top menu is open (see _menu_popup — a specific-funcid unbind, never
            unbind_all, so it can't churn the interpreter-global bindtag)."""
            fid = getattr(self, "_menu_dismiss_id", None)
            if fid is not None:
                try:
                    self.unbind("<Button-1>", fid)
                except Exception:
                    pass
                self._menu_dismiss_id = None

        def _dismiss_open_menu(self):
            """Tear down the open top-menu popup + any fly-out (used when the main
            window moves — an absolute-positioned popup can't follow it)."""
            self._unbind_menu_dismiss()
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
            # tear down any menu already open; if it was THIS button's menu, that's a
            # toggle — close and stop (a re-click on an open menu button closes it).
            existing = getattr(self, "_open_menu", None)
            same = (existing is not None
                    and getattr(self, "_open_menu_anchor", None) is anchor)
            self._unbind_menu_dismiss()
            for attr in ("_open_submenu", "_open_menu"):
                win = getattr(self, attr, None)
                if win is not None:
                    try:
                        win.destroy()
                    except Exception:
                        pass
                setattr(self, attr, None)
            if same:
                self._open_menu_anchor = None
                return
            border, menu_bg, hover = P["menubd"], P["menubg"], P["menuhov"]

            def close_all():
                # destroy the submenu FIRST, then the root — be explicit rather
                # than relying on Tk to cascade, so no fly-out is left orphaned.
                self._unbind_menu_dismiss()
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
                        tk.Frame(body, bg=P["border"], height=1).pack(
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
            self._open_menu_anchor = anchor      # so a re-click on it toggles closed
            root.geometry("+%d+%d" % (anchor.winfo_rootx(),
                                      anchor.winfo_rooty() + anchor.winfo_height()))
            root.bind("<Escape>", lambda e: close_all())
            # (main-window Escape is bound ONCE in _build_menubar -> _dismiss_open_menu,
            # not per popup, so the bindings don't accumulate — review v0.18-UX.)
            # NO grab (owner: "still have to click twice to switch menus"). A local
            # grab redirects/swallows a click on a SIBLING menu button, so the first
            # click only closed the open menu and a second was needed to open the next.
            # Instead: each menu button's own <Button-1> opens its menu directly and
            # returns "break" (so it never reaches this handler), and _menu_popup closes
            # any menu already open — one click switches. A click ANYWHERE ELSE in the
            # main window reaches this dismissal and closes the menu; the popup and its
            # fly-out are separate toplevels, so clicks inside them never bubble here.
            self._unbind_menu_dismiss()
            self._menu_dismiss_id = self.bind(
                "<Button-1>", lambda e: close_all(), add="+")

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

            self._menubuttons = []              # (mb, specs_fn) — for one-click switch
            self._menu_dismiss_id = None        # live only while a menu is open
            self._open_menu_anchor = None       # which button's menu is open (toggle)
            # Escape closes an open menu — bound ONCE here (not per popup, which would
            # accumulate bindings); no-ops when nothing is open.
            self.bind("<Escape>", lambda e: self._dismiss_open_menu(), add="+")
            def add_menu(text, specs_fn):
                mb = ttk.Menubutton(bar, text=text, style=mb_style)
                mb._owl_menu_specs = specs_fn       # so a click switches menus
                # A6: while the heavy-read modal is up, the menu bar is inert (these
                # custom Menubuttons open with NO grab, so the modal's grab may not
                # cover them — gate explicitly). Return "break" so nothing opens.
                mb.bind("<Button-1>", lambda e, m=mb, f=specs_fn:
                        "break" if self._read_modal_up
                        else (self._menu_popup(m, f()), "break")[-1])
                mb.pack(side="left", padx=(4, 0), pady=1)
                self._menubuttons.append((mb, specs_fn))

            add_menu("File", self._file_menu)
            add_menu("Tools", self._tools_menu)
            add_menu("Help", self._help_menu)
            # F1 is a main-window binding that launches an external browser — also
            # gated during a heavy read (A6).
            self.bind("<F1>", lambda e: None if self._read_modal_up
                      else self._show_instructions())
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
                ("cmd", "Save condition report…", self._save_health_report),
                ("cmd", "Session library…", self._open_session_library),
                ("cmd", "Open session folder", self._open_session_folder),
                ("cmd", "Export share-safe copy…", self._export_share_safe),
                ("sep",),
                ("cmd", "Exit", self._on_close),
            ]

        def _tools_menu(self):
            return [
                ("cmd", "Inspect a bike…", self._open_inspection),
                ("cmd", "Bike info…", self._show_bike_info),
                ("cmd", "Gearing calculator…", self._show_gearing_calc),
                ("submenu", "COM port:  %s" % self._current_port_label(),
                 self._com_port_menu),
                ("submenu", "Appearance:  %s" % theme_mod.current_mode().capitalize(),
                 self._appearance_menu),
                ("sep",),
                ("cmd", "Settings…", self._show_settings),
            ]

        def _appearance_menu(self):
            cur = theme_mod.current_mode()
            return [("radio", mode.capitalize(), mode == cur,
                     lambda m=mode: self._set_theme(m))
                    for mode in theme_mod.MODES]

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
                ("cmd", "Releases & changelog\u2026   (you have v%s)" % __version__,
                 self._open_releases),
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

        def _open_releases(self):
            """Hand the releases page to the user's browser.

            The browser makes the request, not OpenMBB - the same arrangement
            "View project on GitHub" has had for the project's whole life. This
            program has no HTTP client and learns nothing about the result.
            """
            self._open_url("https://github.com/rodu4835/openmbb/releases")

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
            # the trend caches hold captures from the OLD root; keeping them
            # would plot another folder's history against the new location
            self._trend_cache = None
            self._log_trend_cache = None
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

        def _export_share_safe(self):
            """Write a copy of a session folder with the identifiers stripped.

            A capture is the most useful thing an owner can hand to somebody else
            and the most dangerous — it carries the VIN, the serials and every
            byte that crossed the wire. The export re-scans everything it writes
            and refuses to produce a bundle it cannot vouch for.
            """
            import shutil
            import tempfile
            from tkinter import filedialog
            base = config.get_log_dir()
            src = filedialog.askdirectory(
                title="Choose the session folder to share",
                initialdir=os.path.join(base, "openmbb-sessions")
                if os.path.isdir(os.path.join(base, "openmbb-sessions")) else base)
            if not src:
                return
            src = os.path.normpath(src.rstrip("\\/"))
            # A timestamp, never the source folder's name. `src + "-shared"`
            # left two near-identical FOLDERS side by side and opened a window
            # showing both, and picking the wrong one publishes the VIN and
            # every byte that crossed the wire. It also named the bundle after
            # the source, so a folder called after a person carried that name
            # into the file being handed over - and the export's own guard
            # catches identifier SHAPES, which a person's name is not.
            stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            # Built somewhere else entirely and zipped afterwards, so a
            # half-written bundle never appears next to the capture at all -
            # and so a REFUSAL never asks somebody where to save a file that is
            # not going to exist.
            work = tempfile.mkdtemp(prefix="openmbb-share-")
            dst = os.path.join(work, "openmbb-share-%s_REDACTED" % stamp)
            try:
                rep = redact_mod.redact_session(src, dst, overwrite=True)
            except RuntimeError as e:
                shutil.rmtree(work, ignore_errors=True)
                # verification failed: the partial bundle has already been removed
                messagebox.showerror(
                    APP_NAME, "Nothing was written.\n\n%s\n\nPlease report this — "
                    "an identifier the exporter does not recognise means the "
                    "redaction rules need widening before anything is shared." % e)
                return
            except ValueError as e:
                shutil.rmtree(work, ignore_errors=True)
                # A refusal about the REQUEST rather than the data: the chosen
                # source is not a capture, or the destination would sit inside
                # the capture or carry an identifier in its name. Uncaught, this
                # reached sys.stderr - which in the frozen windowed build goes
                # nowhere, so the menu item appeared to do nothing at all.
                messagebox.showerror(
                    APP_NAME, "Nothing was written.\n\n%s" % e)
                return
            except OSError as e:
                shutil.rmtree(work, ignore_errors=True)
                messagebox.showerror(APP_NAME, "Could not write the copy:\n\n%s" % e)
                return
            zip_path = filedialog.asksaveasfilename(
                title="Save the share-safe copy as",
                initialdir=os.path.dirname(src),
                initialfile="openmbb-share-%s_REDACTED.zip" % stamp,
                defaultextension=".zip",
                filetypes=[("Zip archive", "*.zip")])
            if not zip_path:
                shutil.rmtree(work, ignore_errors=True)
                return
            try:
                self._zip_folder(dst, zip_path)
            except OSError as e:
                shutil.rmtree(work, ignore_errors=True)
                messagebox.showerror(
                    APP_NAME, "The copy was made but could not be packed:\n\n%s" % e)
                return
            kinds = ", ".join("%s x%d" % (k, v) for k, v in rep["by_kind"].items())
            self._share_result_dialog(src, dst, work, zip_path, rep, kinds)

        def _zip_folder(self, folder, zip_path):
            """Pack `folder` into `zip_path`, one directory deep."""
            import zipfile
            root = os.path.basename(os.path.normpath(folder))
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for name in sorted(os.listdir(folder)):
                    p = os.path.join(folder, name)
                    if os.path.isfile(p):
                        z.write(p, arcname=os.path.join(root, name))

        def _share_result_dialog(self, src, dst, work, zip_path, rep, kinds):
            """What was scanned for, and what is still in there.

            The old dialog said the bundle "carries no VIN or serial number",
            which is precisely true and reads as an all-clear over a file that
            still holds the owner's own note, their machine's clock and the
            whole event log. It also said "have a look yourself" with no tool
            behind it.
            """
            import shutil
            win = tk.Toplevel(self)
            win.title("%s — Share-safe copy" % APP_NAME)
            win.transient(self)
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win.configure(bg=surface)
            outer = ttk.Frame(win, padding=(18, 16))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Share-safe copy written",
                      style="Heading.TLabel").pack(anchor="w")
            ttk.Label(outer, text=zip_path, style="Muted.TLabel",
                      wraplength=560).pack(anchor="w", pady=(2, 10))
            ttk.Label(
                outer, wraplength=560,
                text="Scanned for the VIN and the MBB, module and Sevcon serial "
                     "numbers \u2014 %d replaced%s across %d files, and every file "
                     "was re-scanned afterwards. It looks for those shapes and "
                     "nothing else: not names, not places, not people."
                     % (rep["identifiers_replaced"],
                        " (" + kinds + ")" if kinds else "", rep["files"])
            ).pack(anchor="w", pady=(0, 10))
            ttk.Label(outer, text="What is still in there",
                      style="Heading.TLabel").pack(anchor="w")
            ttk.Label(
                outer, wraplength=560,
                text="Your own note, the settings dump, your machine's clock, and "
                     "the full event log \u2014 when you rode, how far, when you "
                     "charged, how long it sat plugged in. That is not leftovers; "
                     "that is the data. Read it before you send it anywhere."
            ).pack(anchor="w", pady=(2, 14))
            row = ttk.Frame(outer)
            row.pack(fill="x")

            def done():
                shutil.rmtree(work, ignore_errors=True)
                try:
                    win.destroy()
                except Exception:
                    pass

            ttk.Button(row, text="Show me what changed",
                       command=lambda: self._show_redaction_diff(
                           src, dst, rep.get("pairs") or [])).pack(side="left")
            ttk.Button(row, text="Open the folder",
                       command=lambda: self._open_zip_folder(zip_path)
                       ).pack(side="left", padx=(8, 0))
            ttk.Button(row, text="Done", command=done).pack(side="right")
            win.protocol("WM_DELETE_WINDOW", done)
            self._share_win = win

        def _open_zip_folder(self, zip_path):
            try:
                open_in_file_manager(os.path.dirname(zip_path))
            except Exception:
                pass                      # the path is on screen either way

        @staticmethod
        def _diff_text(path):
            """A file's text however it was encoded, or None if it is not text.

            Uses the export's own decoder so the viewer reads exactly what the
            redactor read - a file this cannot decode is one the export refused
            to vouch for, and the two must not disagree about that.
            """
            try:
                with open(path, "rb") as f:
                    body, _enc = redact_mod._decode_text(f.read())
                return body
            except OSError:
                return None

        def _show_redaction_diff(self, src_dir, dst_dir, pairs):
            """Every file in the bundle, with what the redactor changed marked.

            The WHOLE bundle, file by file, and never only the files that
            changed: a changed-only view implies everything it left out was
            checked, and what actually happened is that those files were checked
            for four identifier shapes and nothing else. `session_note.txt`
            sorts first because it is the one file a person typed the contents
            of, and the one the redactor cannot help with at all.

            Nothing here is written to disk. The left-hand column is the
            unredacted original, which is the point - and is why it stays on
            this screen.
            """
            names = sorted(pairs or [],
                           key=lambda pr: (pr[0] != "session_note.txt",
                                           pr[0].lower()))
            if not names:
                messagebox.showinfo(APP_NAME, "There are no files to show.")
                return
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — What is being shared" % APP_NAME)
            win.geometry("940x660")
            win.configure(bg=surface)
            win.transient(self)
            outer = ttk.Frame(win, padding=(14, 12))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Every file in the bundle, exactly as it will "
                                  "be sent", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(
                outer, style="Muted.TLabel", wraplength=880,
                text="Struck-through lines are what the redactor took out; the "
                     "line under each one is what replaces it. A file with "
                     "nothing struck through was scanned and matched no "
                     "identifier \u2014 which is not the same as having been "
                     "checked for everything."
            ).pack(anchor="w", pady=(2, 10))
            body = ttk.Frame(outer)
            body.pack(fill="both", expand=True)
            left = ttk.Frame(body)
            left.pack(side="left", fill="y")
            lb = tk.Listbox(left, width=30, exportselection=False,
                            bg=P["field"], fg=P["fg"],
                            selectbackground=P["sel"], selectforeground=P["selfg"],
                            highlightthickness=0, borderwidth=0, activestyle="none")
            lb.pack(side="left", fill="y", expand=True)
            right = ttk.Frame(body)
            right.pack(side="left", fill="both", expand=True, padx=(12, 0))
            head = ttk.Label(right, text="", style="Heading.TLabel", wraplength=600)
            head.pack(anchor="w")
            wrap = ttk.Frame(right)
            wrap.pack(fill="both", expand=True, pady=(6, 0))
            txt = tk.Text(wrap, wrap="none", bg=P["console"], fg=P["termfg"],
                          insertbackground=P["termfg"], highlightthickness=0,
                          borderwidth=0, font=("Consolas", 9))
            ys = ttk.Scrollbar(wrap, orient="vertical", command=txt.yview)
            xs = ttk.Scrollbar(right, orient="horizontal", command=txt.xview)
            txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            ys.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            xs.pack(fill="x")
            txt.tag_configure("was", foreground=P["danger"], overstrike=True)
            txt.tag_configure("now", foreground=P["green"])
            txt.tag_configure("warn", foreground=P["warn"])
            txt.tag_configure("note", foreground=P["dim"])

            for src_name, _out in names:
                lb.insert("end", src_name)

            def show(_evt=None):
                sel = lb.curselection()
                if not sel:
                    return
                src_name, out_name = names[sel[0]]
                a = self._diff_text(os.path.join(src_dir, src_name))
                b = self._diff_text(os.path.join(dst_dir, out_name))
                txt.config(state="normal")
                txt.delete("1.0", "end")
                if src_name != out_name:
                    txt.insert("end", "the file NAME carried an identifier too: "
                                      "%s  \u2192  %s\n\n" % (src_name, out_name),
                               "warn")
                if src_name == "session_note.txt":
                    txt.insert("end", "This is your own note, and it goes exactly "
                                      "as you wrote it. The redactor does not look "
                                      "for names, places or people \u2014 only for "
                                      "the VIN and serial numbers.\n\n", "warn")
                if a is None or b is None:
                    txt.insert("end", "(not text this export can read \u2014 it was "
                                      "left out of the bundle)\n", "warn")
                    txt.config(state="disabled")
                    return
                al, bl = a.splitlines(), b.splitlines()
                changed = 0
                for i in range(max(len(al), len(bl))):
                    x = al[i] if i < len(al) else None
                    y = bl[i] if i < len(bl) else None
                    if x == y:
                        txt.insert("end", (y or "") + "\n")
                    else:
                        changed += 1
                        if x is not None:
                            txt.insert("end", x + "\n", "was")
                        if y is not None:
                            txt.insert("end", y + "\n", "now")
                if not changed:
                    txt.insert("1.0", "nothing in this file matched an identifier "
                                      "\u2014 it is going exactly as it is\n\n",
                               "note")
                head.config(text="%s  \u2014  %d line(s) changed"
                                 % (out_name, changed))
                txt.config(state="disabled")
                txt.yview_moveto(0)

            lb.bind("<<ListboxSelect>>", show)
            lb.selection_set(0)
            show()
            ttk.Button(outer, text="Close", command=win.destroy).pack(
                anchor="e", pady=(10, 0))
            self._diff_win = win

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

        # -- E3: the saved captures on disk -----------------------------------
        def _recent_sessions(self, limit=25):
            root = self._session_root()
            try:
                dirs = [d for d in os.listdir(root)
                        if os.path.isdir(os.path.join(root, d))]
            except OSError:
                return root, []
            # NOT mtime: writing the verdict cache into a capture folder, or
            # copying a capture between machines, rewrites it. The folder name
            # carries the capturing machine's clock, which is what "when" means.
            dirs.sort(key=lambda d: library_mod._captured_at(
                os.path.join(root, d), None), reverse=True)
            return root, dirs[:limit]

        # -- inspecting a bike you do not know --------------------------------
        # The scenario the condition check was built for, and until now the one
        # that needed the most prior knowledge: which buttons, in which order,
        # and which of them you must not press without the owner's say-so. Every
        # step below hands off to the app's own controls - nothing here reads or
        # writes anything the buttons do not.

        # (title, body, button label, action attribute)
        _INSPECT_STEPS = (
            ("Park it, and keep it parked",
             "Kickstand down, key OFF, and not somewhere it can roll. Everything "
             "below wakes a live console on a bike that can move under its own "
             "power, and step 5 can make the bike CLICK ITS CONTACTOR OPEN.",
             "I have parked it", "_inspect_parked"),
            ("Test your cable  (optional)",
             "Listen-only — it never transmits, so it cannot disturb a bike you "
             "are not responsible for. Proves the wiring and the baud rate "
             "before you touch anything. Skip straight to Connect if you have "
             "used this cable before.",
             VERIFY_LABEL, "_inspect_cable"),
            ("Connect",
             "Wakes the console and reads the version banner. Still read-only.",
             "Connect", "_landing_connect"),
            ("Pull full database",
             "The settings backup and the command reads — about two minutes, and "
             "no contactor risk. This is the part you always want: the Health "
             "tab, the clocks and the bike-state block are all read from it.",
             "Pull full database", "_inspect_pull"),
            ("Ask the owner before you read the event log",
             "The step that needs permission, and the only one here that leaves "
             "a mark on someone else's bike. The pack questions — the weakest "
             "cell, the charge index, how it has been charged — cannot be "
             "answered without it. Read the warning on the confirm in full "
             "before you agree to it on an owner's behalf.",
             "Read the event log", "_inspect_heavy"),
            ("Read the verdict",
             "Worst-wins across only what one capture can judge with no second "
             "bike to compare against. Read the Not determined rows too: a "
             "question this capture could not answer is not a pass, and a "
             "seller who charges the bike and lets it sit produces exactly that "
             "capture.",
             "Show the verdict", "_inspect_verdict"),
        )

        def _inspect_parked(self):
            self._inspect_confirmed_parked = True

        def _inspect_cable(self):
            # records that the test was RUN, not that it passed - the wizard
            # reports its own result, and this flow does not second-guess it
            self._inspect_cable_tested = True
            self._show_cable_wizard()

        def _inspect_pull(self):
            self._select_tab("Read")
            self._baseline()

        def _inspect_heavy(self):
            """Hand off to the existing gated read, warning and all."""
            self._read_heavy("eventlogdump")

        def _inspect_verdict(self):
            # UNCONDITIONALLY re-read the live capture. Reloading only when
            # analyze_session was None meant a saved capture opened mid-inspection
            # kept the Analyze tab - and step 6 - showing another bike's verdict;
            # and it meant completing the real event-log read AFTER pressing this
            # once did not refresh what the step was showing.
            if self.logger:
                try:
                    self._analyze_set(sessions.load_session(self.logger.dir))
                except Exception:
                    pass
            self._select_tab("Analyze")
            self._select_analyze_sub("Condition")

        def _select_analyze_sub(self, needle):
            nb = getattr(self, "analyze_nb", None)
            if nb is None:
                return
            for tid in nb.tabs():
                if needle.lower() in str(nb.tab(tid, "text")).lower():
                    nb.select(tid)
                    return

        def _inspect_session(self):
            """The capture being taken from the bike in front of you.

            The LIVE logger directory wins over whatever is loaded in Analyze.
            Preferring analyze_session let a saved capture - opened from the
            session library, which is reachable mid-inspection - tick step 5
            "Read the event log" with nothing read from this bike, and then
            present that other bike's verdict as this one's. Steps 4-6 are about
            what came off the machine in front of you or they are worthless.
            """
            if self.logger:
                try:
                    return sessions.load_session(self.logger.dir)
                except Exception:
                    return None
            return None

        def _inspect_has_event_log(self, min_bytes=4096):
            """Whether the LIVE capture holds a real event log, by stat alone.

            `min_bytes` guards against an empty or single-line file left by a read
            that never got going; a genuine dump is ~1 MB.
            """
            if not self.logger:
                return False
            try:
                names = os.listdir(self.logger.dir)
            except OSError:
                return False
            for name in names:
                if "eventlogdump" not in name.lower():
                    continue
                try:
                    if os.path.getsize(os.path.join(self.logger.dir, name)) >= min_bytes:
                        return True
                except OSError:
                    continue
            return False

        def _inspect_done(self, i):
            """Whether step `i` has actually happened.

            Read back from app state on every refresh rather than ticked off as
            the user clicks: a flow that believes its own checklist will happily
            report a pull that failed halfway as complete.
            """
            if i == 0:
                return bool(getattr(self, "_inspect_confirmed_parked", False))
            if i == 1:
                return bool(getattr(self, "_inspect_cable_tested", False))
            if i == 2:
                return bool(self.connected)
            if i == 3:
                return bool(self.baseline_done)
            if i == 4:
                # Cheap on purpose: this runs on the Tk main thread every 400 ms,
                # and sessions.load_session() reads the whole folder including the
                # ~1 MB event log. Whether a non-trivial eventlogdump file exists
                # is the actual question, and stat answers it.
                return self._inspect_has_event_log()
            if i == 5:
                # the verdict must be THIS capture's: _cond_verdict is set by
                # _render_condition for whatever session Analyze holds, which is
                # not necessarily the bike on the bench
                live = self._inspect_session()
                loaded = self.analyze_session
                if not live or not loaded:
                    return False
                if os.path.normcase(os.path.abspath(loaded.dir)) != \
                        os.path.normcase(os.path.abspath(live.dir)):
                    return False
                return bool(getattr(self, "_cond_verdict", None))
            return False

        def _inspect_ready(self, i):
            """Whether step `i` can be attempted yet.

            Only the parking confirmation gates everything, because it is the one
            thing here the app cannot check for itself. The cable test does not
            gate Connect - it is optional, and a flow that insisted on it would
            be wrong rather than careful. Step 6 is reachable without step 5 on
            purpose: a verdict from a capture with no event log is mostly Not
            determined, and being shown that is the point.
            """
            if i in (0, 1, 2):
                return self._inspect_done(0) or i == 0
            if i == 3:
                return bool(self.connected)
            if i in (4, 5):
                return bool(self.baseline_done)
            return False

        def _open_inspection(self):
            from . import dialogs
            # one window, not one per menu click - each carries its own 400 ms
            # poll, and they stack
            existing = getattr(self, "_inspect_win", None)
            if existing is not None:
                try:
                    if existing.winfo_exists():
                        existing.deiconify()
                        existing.lift()
                        return existing
                except tk.TclError:
                    pass
                self._inspect_win = None
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s — Inspect a bike" % APP_NAME)
            win.geometry("640x700")
            win.configure(bg=surface)
            win.transient(self)
            # this window drives Connect from one of its own buttons, and every
            # _connect tears the helper windows down - so it opts out
            win._openmbb_keep_open = True
            self._inspect_win = win
            outer = ttk.Frame(win, padding=(18, 16))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Inspecting a bike you don't know",
                      style="Heading.TLabel").pack(anchor="w")
            ttk.Label(outer, text="Work down the list. Each step hands off to the "
                                  "app's own controls, and each tick is read back "
                                  "from what actually happened rather than from "
                                  "your having clicked it.",
                      style="Muted.TLabel", wraplength=580).pack(anchor="w",
                                                                 pady=(2, 12))
            rows = []
            for i, (title, body, label, action) in enumerate(self._INSPECT_STEPS):
                fr = ttk.Frame(outer)
                fr.pack(fill="x", pady=(0, 10))
                head = ttk.Frame(fr)
                head.pack(fill="x")
                mark = ttk.Label(head, text="○", width=3)
                mark.pack(side="left")
                ttk.Label(head, text="%d. %s" % (i + 1, title),
                          style="Heading.TLabel").pack(side="left")
                btn = ttk.Button(head, text=label, command=getattr(self, action))
                btn.pack(side="right")
                ttk.Label(fr, text=body, style="Muted.TLabel",
                          wraplength=580).pack(anchor="w", padx=(28, 0))
                rows.append((mark, btn))
            self._inspect_rows = rows

            def refresh():
                try:
                    if not win.winfo_exists():
                        return
                except tk.TclError:
                    return
                self._inspect_refresh(rows)
                win.after(400, refresh)

            refresh()
            def _close():
                self._inspect_win = None
                win.destroy()

            win.protocol("WM_DELETE_WINDOW", _close)
            ttk.Button(outer, text="Close", command=_close).pack(
                side="right", pady=(8, 0))
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            return win

        def _inspect_refresh(self, rows):
            for i, (mark, btn) in enumerate(rows):
                done = self._inspect_done(i)
                mark.config(text="✓" if done else "○",
                            foreground=P["green"] if done else P["dim"])
                btn.state(["!disabled"] if self._inspect_ready(i) else ["disabled"])

        # -- the session library ---------------------------------------------
        # Three captures already make a folder list a memory test: they are named
        # `2026-07-10_124738_435640_COM4` and the only way to tell one from the
        # next is to open it. This shows what is actually in each one.
        _LIB_COLS = (("when", "When", 132, "w"),
                     ("odo", "Odometer", 92, "e"),
                     ("soc", "SOC", 56, "e"),
                     ("verdict", "Verdict", 96, "w"),
                     ("note", "Note", 220, "w"))

        _LIB_TAGS = (("ok", "green"), ("watch", "warn"), ("concern", "danger"),
                     ("unknown", "dim"), ("none", "dim"))

        def _lib_row_values(self, row, verdict):
            """One library row. `verdict` is None until it has been read."""
            when = "?"
            if row["when"]:
                try:
                    when = _dt.datetime.fromtimestamp(row["when"]).strftime(
                        "%Y-%m-%d %H:%M")
                except (ValueError, OSError):
                    pass
            unit = config.get_units()
            odo = row.get("odo_km")
            if odo is None:
                odo = "-"
            else:
                odo = ("%.0f mi" % (odo * 0.621371)) if unit == "mi" else "%.0f km" % odo
            soc = "-" if row.get("soc_pct") is None else "%.0f%%" % row["soc_pct"]
            if verdict:
                v = verdict["level"]
                # A stored fault the pack verdict does not cover must not read
                # as a clean row here either: the list is scanned faster than
                # the tab, and "ok" is the word people act on.
                if verdict.get("beyond_pack"):
                    v = "%s + faults" % v
            elif not row["has_event_log"]:
                # a capture taken without '+event log' can never reach a verdict,
                # and saying so beats a spinner that resolves to nothing
                v = "no log"
            elif row.get("read_failed"):
                v = "unreadable"          # tried, failed - never a quiet pass
            else:
                v = "reading..."
            note = row.get("note") or ""
            return (when, odo, soc, v, note.replace("\n", " ")[:120])

        def _open_session_library(self):
            from . import dialogs
            root = self._session_root()
            rows = library_mod.scan(root)
            if not rows:
                messagebox.showinfo(APP_NAME, "No saved sessions yet in:\n%s" % root)
                return
            surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            win = tk.Toplevel(self)
            win.title("%s \u2014 Session library" % APP_NAME)
            win.geometry("860x480")
            win.configure(bg=surface)
            win.transient(self)
            outer = ttk.Frame(win, padding=(18, 16))
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Session library",
                      style="Heading.TLabel").pack(anchor="w", pady=(0, 2))
            ttk.Label(outer, text=root, style="Muted.TLabel",
                      wraplength=800).pack(anchor="w", pady=(0, 10))

            body = ttk.Frame(outer)
            body.pack(fill="both", expand=True)
            sb = ttk.Scrollbar(body)
            sb.pack(side="right", fill="y")
            tree = ttk.Treeview(body, show="headings", selectmode="browse",
                                columns=[c[0] for c in self._LIB_COLS],
                                yscrollcommand=sb.set)
            for key, title, width, anchor in self._LIB_COLS:
                tree.heading(key, text=title)
                tree.column(key, width=width, anchor=anchor,
                            stretch=(key == "note"))
            # this dialog is built fresh each time, so it reads the live palette
            # here rather than registering with _restyle_trees (which exists for
            # the long-lived tab trees that outlive a theme switch)
            # tag_configure COPIES the colour, so these do not follow a live
            # theme switch on their own - the same trap _restyle_trees exists
            # for. The dialog is non-modal, so Tools -> Appearance is reachable
            # while it is open; register it for as long as it lives.
            for tag, colour in self._LIB_TAGS:
                tree.tag_configure(tag, foreground=P[colour])
            self._lib_tree = tree
            tree.pack(side="left", fill="both", expand=True)
            sb.config(command=tree.yview)

            for i, row in enumerate(rows):
                tree.insert("", "end", iid=str(i),
                            values=self._lib_row_values(row, None),
                            tags=("none" if not row["has_event_log"] else "unknown",))
            tree.selection_set("0")
            tree.focus("0")

            # -- the note for whatever is selected ---------------------------
            nrow = ttk.Frame(outer)
            nrow.pack(fill="x", pady=(12, 0))
            ttk.Label(nrow, text="Note:").pack(side="left")
            # bound to THIS window's interpreter rather than tkinter's default
            # root: the app only ever has one root in normal use, but a variable
            # that resolves against a different one reads back empty, and the
            # symptom is a note the user typed and pressed save on going nowhere
            note_var = tk.StringVar(master=win)
            ent = ttk.Entry(nrow, textvariable=note_var)
            ent.pack(side="left", fill="x", expand=True, padx=(8, 8))

            def _sel_row():
                sel = tree.selection()
                return rows[int(sel[0])] if sel else None

            def save_note(_e=None):
                row = _sel_row()
                if row is None:
                    return
                # the note is written into the capture folder, so a capture that
                # is copied or handed to a maintainer takes its note with it
                row["note"] = library_mod.write_note(row["folder"], note_var.get())
                tree.set(tree.selection()[0], "note", row["note"][:120])

            def on_select(_e=None):
                row = _sel_row()
                note_var.set(row["note"] if row else "")

            tree.bind("<<TreeviewSelect>>", on_select)
            ent.bind("<Return>", save_note)
            ttk.Button(nrow, text="Save note", command=save_note).pack(side="left")
            ttk.Label(outer, text="A note travels with the capture folder \u2014 "
                                  "\"before the re-gear\", \"after the firmware "
                                  "update\".",
                      style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
            on_select()

            def open_sel(_e=None):
                row = _sel_row()
                if row is None:
                    return
                win.destroy()
                try:
                    self._analyze_set(sessions.load_session(row["folder"]))
                    # _select_tab leaves the Home screen first if it's showing —
                    # a bare nb.select() would switch the HIDDEN notebook (owner:
                    # opening a recent session from Home did nothing visible).
                    self._select_tab("Analyze")
                except Exception as e:
                    messagebox.showerror(APP_NAME, "Couldn't load session:\n%s" % e)

            tree.bind("<Double-Button-1>", open_sel)
            brow = ttk.Frame(outer)
            brow.pack(fill="x", pady=(12, 0))
            ttk.Button(brow, text="Open in Analyze", style=self.sty["accent"],
                       command=open_sel).pack(side="right")
            ttk.Button(brow, text="Close", command=win.destroy).pack(
                side="right", padx=(0, 8))
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            tree.focus_set()

            # Verdicts cost a megabyte of event log each, so they are read one
            # capture per idle tick AFTER the list is already on screen. The
            # window is usable throughout, and a verdict already computed on an
            # earlier visit comes back from the cache beside the capture.
            self._lib_fill_verdicts(win, tree, rows, 0)

        def _lib_fill_verdicts(self, win, tree, rows, i):
            if i >= len(rows):
                return
            try:
                if not win.winfo_exists():
                    return                      # closed mid-read; nothing to fill
            except tk.TclError:
                return
            row = rows[i]
            if row["has_event_log"]:
                try:
                    v = library_mod.deep_verdict(row["folder"])
                except Exception:
                    v = None
                # A capture whose event log will not read must not sit on
                # "reading..." for ever, and must certainly not settle into a
                # level: row["read_failed"] makes the cell say so out loud.
                row["read_failed"] = v is None
                try:
                    tree.item(str(i), values=self._lib_row_values(row, v),
                              tags=((v["level"],) if v else ("unknown",)))
                except tk.TclError:
                    return
            win.after(1, lambda: self._lib_fill_verdicts(win, tree, rows, i + 1))

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
                # Condition carries Consumption and Range in the distance unit
                # too, and was the one tab left showing km after a switch to mi
                self._render_condition()
            self._render_charts()         # charts read the unit too

        def _apply_temp_units(self):
            config.set_temp_units(self.temp_units_var.get())
            if self.analyze_session:      # re-render temps in the new unit
                self._render_health()
                self._render_condition()  # carries pack temperatures too
                self._render_rides()      # the ride table carries temps too
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
                self._pick_lone_port(ports)
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
                lbl_pw.config(text=("%d saved password(s), stored in clear text in "
                                    "~/.openmbb/config.json and tried automatically "
                                    "on login. Forget them below." % c) if c
                              else "No saved passwords. After a successful login "
                                   "with a password you typed, you can choose to "
                                   "remember it — it is then stored in clear text "
                                   "in ~/.openmbb/config.json.")
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
            """The page File -> Save condition report writes.

            Delegates to report.condition_report so the saved page and the
            Analyze tab cannot drift apart: the old builder here re-derived the
            health rows by hand and carried none of the pack condition, the
            charging habits, the clocks or the verdict, which are most of what
            makes the page worth handing to anyone.

            That function refuses to return a page carrying anything shaped like
            an identifier, so a raised RuntimeError here is the guard doing its
            job and is shown to the user rather than swallowed.
            """
            return report_mod.condition_report(
                s, version=__version__, temp_units=config.get_temp_units())

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
                title="Save condition report", defaultextension=".txt",
                initialfile="openmbb-condition-report.txt",
                filetypes=[("Text file", "*.txt"), ("All files", "*.*")])
            if not path:
                return
            try:
                text = self._build_health_report(s)
            except RuntimeError as e:
                # the report withheld itself rather than hand over a page it
                # could not vouch for; say so instead of writing a partial file
                messagebox.showerror(APP_NAME, "%s\n\nNothing was written." % e)
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                messagebox.showinfo(APP_NAME, "Condition report saved to:\n%s" % path)
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
                lines.append("Not connected. Connect to your bike, then run")
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
                "  Released      : %s\n"
                # where a suspicious user looks, and the one line here they can
                # check for themselves
                "  Network       : none \u2014 OpenMBB makes no requests\n"
                "  Repo          : github.com/rodu4835/openmbb\n"
                "  License       : MIT (no warranty)\n\n"
                "Personal diagnostic tool for your own vehicle. Not affiliated\n"
                "with Zero Motorcycles."
                % (APP_NAME, __version__, self.sty.get("backend"),
                   sys.version.split()[0],
                   version_mod.describe_release(__release_date__)))

        def _show_about(self):
            self._bike_about_window("About")

        def _apply_gates(self):
            # C1: Login is READ-ONLY (it only reveals the tunable settings), so it
            # opens as soon as you're connected — no full database pull required. The
            # one hard safety rule lives on WRITES: a settings backup (a Pull full
            # database)
            # AND a login must both exist before anything can be written.
            states = [
                "normal",                                          # Connect
                "normal" if self.connected else "disabled",        # Read
                "normal" if self.connected else "disabled",        # Login (read-only)
                "normal" if (self.connected and self.logged_in and self.baseline_done)
                else "disabled",                                   # Writes (needs backup)
            ]
            prev = getattr(self, "_tab_states", None)
            for i, st in enumerate(states):
                self.nb.tab(i, state=st)
            # guide the eye: flash a tab's header the moment it becomes available
            # (disabled -> normal), e.g. Read/Login on connect, Writes on login+backup.
            if prev is not None:
                for i, st in enumerate(states):
                    if st == "normal" and i < len(prev) and prev[i] == "disabled":
                        self._flash_tab(i)
            self._tab_states = states
            self._refresh_dash_header()      # the single status line (conn/rev/login)
            self._refresh_action_buttons()   # T3: gate the action bar on connect/busy

        def _tab_at(self, x, y):
            try:
                return self.nb.index("@%d,%d" % (x, y))
            except Exception:
                return -1

        def _flash_tab(self, idx):
            """One accent-blue pulse OUTLINING a tab header (a box around it), to draw
            the eye when that tab just unlocked. Cosmetic + best-effort: locates the
            header by hit-testing the tab strip (ttk exposes no per-tab bbox); no-ops
            if it can't (e.g. headless)."""
            nb = self.nb
            try:
                nb.update_idletasks()
                # x-range: scan the strip at a y that lands inside the headers
                xs = [x for x in range(0, max(1, nb.winfo_width()), 3)
                      if self._tab_at(x, 10) == idx]
                if not xs:
                    return
                x0, x1 = min(xs), max(xs) + 3
                # y-range: hit-test down the header's centre for its top+bottom edges,
                # so the box sits ON the tab across themes/DPI (no hardcoded y)
                xc = (x0 + x1) // 2
                ys = [y for y in range(0, 60, 2) if self._tab_at(xc, y) == idx]
                if not ys:
                    return
                y0, y1 = max(0, min(ys) - 1), max(ys) + 2
                bw, blue, base = 2, "#5aa8ff", P["console"]
                # four thin frames = a box outline hugging the whole tab header
                edges = ((x0, y0, x1 - x0, bw),          # top
                         (x0, y1 - bw, x1 - x0, bw),     # bottom
                         (x0, y0, bw, y1 - y0),          # left
                         (x1 - bw, y0, bw, y1 - y0))     # right
                frames = []
                for fx, fy, fw, fh in edges:
                    fr = tk.Frame(nb, bg=blue)
                    fr.place(x=fx, y=fy, width=fw, height=fh)
                    frames.append(fr)

                def step(n):
                    if not frames or not frames[0].winfo_exists():
                        return
                    if n <= 0:
                        for fr in frames:
                            fr.destroy()
                        return
                    col = blue if n % 2 == 0 else base
                    for fr in frames:
                        fr.config(bg=col)
                    self.after(150, lambda: step(n - 1))
                step(5)
            except Exception:
                pass

        def _tab_unlock_hint(self, idx):
            # C7: plain-language "here's how to unlock this stage" for a locked tab.
            if idx in (1, 2):     # Read + Login both just need a connection
                return ("The %s tab opens once you Connect on the Connect tab."
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

        _ANALYZE_TAB = 4       # Connect0 Read1 Login2 Writes3 Analyze4 Console5

        def _on_tab_changed(self, _evt=None):
            """Owner: opening the Analyze tab with a live session captured but nothing
            loaded auto-loads the current session (silently — no popup). The user can
            still 'Load session folder…' to analyze a different one."""
            try:
                idx = self.nb.index(self.nb.select())
            except Exception:
                return
            if idx != self._ANALYZE_TAB or self.analyze_session is not None:
                return
            if not self.logger or self._busy:
                return
            try:
                s = sessions.load_session(self.logger.dir)
            except Exception:
                return
            if self._session_has_data(s):
                self._analyze_set(s)

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
            # A5: a disconnect/clean-slate must also release any read modal's grab.
            if getattr(self, "_read_modal_up", False):
                self._close_read_modal()
            self.transport = None
            self.logger = None
            self.connected = False
            self.baseline_done = False
            self.logged_in = False
            self.version_text = ""
            self.help_logged_out = ""
            self.settings = {}
            self.settings_order = []
            self._baseline_settings = {}
            self._user_wrote = {}
            self._moved_by = {}
            # a disconnect returns to a truly clean slate: drop the loaded analysis and
            # the pull's green command borders so a reconnect doesn't LOOK pre-pulled.
            self.analyze_session = None
            self._trend_cache = None
            self._log_trend_cache = None
            # A verdict outliving its session is the worst kind of stale state
            # here: it keeps a bold green "OK" over an empty Condition tab, and
            # it ticks the inspection flow's "Read the verdict" step for a bike
            # that has not been connected to yet.
            self._cond_verdict = None
            self._log_peak_c = None
            if getattr(self, "cond_tree", None) is not None:
                try:
                    self.cond_tree.delete(*self.cond_tree.get_children())
                except Exception:
                    pass
            self._paint_verdict()
            # the inspection flow's two on-trust ticks belong to one session too
            self._inspect_confirmed_parked = False
            self._inspect_cable_tested = False
            if hasattr(self, "unlock_var"):
                self.unlock_var.set(False)
            if hasattr(self, "baseline_heavy_var"):
                self.baseline_heavy_var.set(False)   # the '+event log' opt-in re-earns
            if getattr(self, "watch_var", None) is not None:
                self.watch_var.set(False)            # the repeat-read stops too
            self._chart_xzoom = None                 # drop any chart drag-zoom
            self._baseline_reset_marks()
            # wipe every text console so a reconnect doesn't show the PREVIOUS session's
            # output (owner: "still brings up previous windows"). This also runs at the
            # top of every _connect, so a retry starts with a clean Connect log.
            for cname in ("txt_probe", "txt_out", "txt_login", "txt_console",
                          "txt_compare"):
                self._clear_console(getattr(self, cname, None))
            self._cmd_history = []
            self._cmd_hist_idx = 0
            self._analyze_hint_shown = False
            self.compare_list = []                   # Compare tab: drop loaded sessions
            if hasattr(self, "effect_panel"):
                self._hide_effect()                  # hide the Writes description panel
            self._close_transient_toplevels()        # gearing calc / pickers / info wins
            self._hide_connect_success()     # a new/broken session re-earns it
            self._set_login_status(False)
            if hasattr(self, "lbl_prog"):
                self.lbl_prog.config(text="", foreground=P["dim"])
            if hasattr(self, "lbl_loaded"):
                self.lbl_loaded.config(text="no session loaded", foreground=P["dim"])
            for tname in ("health_tree", "ride_tree"):
                t = getattr(self, tname, None)
                if t is not None:
                    t.delete(*t.get_children())
            self._refresh_write_rows()
            self._apply_gates()          # also refreshes the dashboard header

        @staticmethod
        def _clear_console(widget):
            """Blank a read-only Text console (no-op if it doesn't exist yet)."""
            if widget is None:
                return
            try:
                widget.config(state="normal")
                widget.delete("1.0", "end")
                widget.config(state="disabled")
            except Exception:
                pass

        def _close_transient_toplevels(self):
            """Destroy any open helper windows (gearing calculator, session
            library, settings, info popups) so a disconnect leaves nothing behind.

            A window carrying `_openmbb_keep_open` survives. That exists for the
            inspection flow, which is a workflow rather than a popup: it drives
            Connect from one of its own buttons, and this runs at the top of every
            _connect - so without the exemption the window a user is working
            through would vanish the instant they used it.
            """
            # tear down an open top-menu FIRST so its refs (_open_menu/_open_submenu/
            # _menu_dismiss_id) are nulled — destroying its Toplevel below would else
            # leave dangling references (review v0.18.1).
            self._dismiss_open_menu()
            for w in list(self.winfo_children()):
                if isinstance(w, tk.Toplevel):
                    if getattr(w, "_openmbb_keep_open", False):
                        continue
                    try:
                        w.destroy()
                    except Exception:
                        pass

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
            # A5: release the read modal's grab deterministically (the _busy guard
            # above misses the post-read, pre-Continue window where _busy is already
            # False but the modal still holds the grab).
            if self._read_modal_up:
                self._close_read_modal()
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
            # also hidden while the heavy-read modal is up — otherwise it repacks
            # the moment finish() clears _busy (before Continue is pressed), floating
            # a live button outside the modal (A2-guard).
            if self.connected and not self._busy and not self._read_modal_up:
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

        def _run_bg(self, fn, done=None, on_error=None, allow_during_modal=False,
                    suppress_default_error=False):
            # A2-guard: refuse a second operation while busy OR while the heavy-read
            # modal is up (except the ONE modal-owned read, which passes
            # allow_during_modal). The modal refusal is SILENT — the "Busy" popup is
            # itself a grab_set+wait_window dialog that would STEAL the modal's grab.
            if self._busy or (self._read_modal_up and not allow_during_modal):
                if self._busy and not self._read_modal_up:
                    messagebox.showinfo(APP_NAME,
                                        "Busy — wait for the current operation.")
                return False        # refused — callers that opened a modal tear it down
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
                        # A4: stash the exception so a modal finalizer (invoked via
                        # the zero-arg on_error) can read it — on_error's signature
                        # stays zero-arg so the connect caller is unaffected.
                        self._last_read_error = err
                        # D6: a mid-session reboot invalidates the login/baseline
                        # state — re-gate before surfacing the error (this does NOT
                        # tear down a read modal; the finalizer owns that, A4).
                        if isinstance(err, ConsoleRebootError):
                            self.logged_in = False
                            self.baseline_done = False
                            # T7: also disarm the master unlock — otherwise the
                            # Writes tab re-opens with one gate pre-armed post-reboot
                            if hasattr(self, "unlock_var"):
                                self.unlock_var.set(False)
                            self._apply_gates()
                        if on_error:
                            try:
                                on_error()
                            except Exception:
                                pass
                        # A4: suppress the default error dialog only for the
                        # modal-owned read (its finalizer shows the error on the
                        # modal). NEVER gate on `on_error is None` — the connect flow
                        # passes on_error yet relies on this showerror.
                        if not suppress_default_error:
                            messagebox.showerror(APP_NAME, str(err))
                    elif done:
                        done(result)
                self._cbq.put(finish)
            threading.Thread(target=worker, daemon=True).start()
            return True         # accepted — the worker will run and call finish

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
        def _connect_version_line(self):
            """Which build is actually running, shown where you look before a pull.

            `pip install -e` updates the venv, not the frozen build the Start Menu
            launches - a bike-day pull was taken on v0.21.0 while the repo was on
            v0.25.0, and only session_meta.txt revealed it afterwards.
            """
            return "OpenMBB v%s (released %s)" % (__version__, __release_date__)

        def _build_connect_tab(self):
            f = self._new_tab(" Connect ", "Connect to your bike",
                              "verify the cable, then connect")
            # Which build is running, on the screen you are looking at when you
            # decide to pull from a motorcycle. See _connect_version_line.
            ttk.Label(f, text=self._connect_version_line(),
                      style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
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

            # calm/muted guidance (was an orange warning wall) using the ACTUAL button
            # labels; power the bike (key ON or AC charger), or explore in Simulator.
            self.connect_help = ttk.Label(f, text=(
                "Pick your COM port, then '%s' — it wakes the console and reads the "
                "firmware. Unsure of the cable? '%s' first: it only listens (sends "
                "nothing) to confirm the wiring + baud. Power the bike (key ON, or the "
                "AC charger in Charging mode).\n"
                "No bike? Turn on Simulator mode on the Home screen. Cable wiring: "
                "Help → Wiring diagram. Isolation reads are only valid OFF the charger."
                % (CONNECT_LABEL, VERIFY_LABEL)),
                justify="left", padding=(0, 10), style="Muted.TLabel")
            self.connect_help.pack(anchor="w")

            # while a connect attempt runs, the port/verify/connect controls are
            # hidden and this neutral "Connecting…" line shows instead — they come back
            # only if the attempt fails (so the user can adjust the port + retry).
            self.connect_busy = ttk.Frame(f)
            ttk.Label(self.connect_busy,
                      text="Connecting… waking the console and reading the firmware. "
                      "Progress shows in the log below.",
                      style="Muted.TLabel").pack(side="left")

            # success banner shown by _connect's done() (no button — the tabs are
            # right there). Hidden until connected; replaces the pre-connect controls.
            self.connect_success = ttk.Frame(f)
            self.lbl_connect_success = ttk.Label(self.connect_success, text="",
                                                 style="Good.TLabel")
            self.lbl_connect_success.pack(side="left")

            self.txt_probe = self._console_text(f, 16)
            self.txt_probe.pack(fill="both", expand=True)

        def _show_connecting(self):
            # a connect attempt just started: hide the pre-connect controls and show
            # the "Connecting…" line. They stay hidden on success; _restore brings
            # them back on failure.
            self.connect_row.pack_forget()
            self.connect_help.pack_forget()
            if hasattr(self, "connect_success"):
                self.connect_success.pack_forget()
            self.connect_busy.pack(fill="x", pady=(0, 8), before=self.txt_probe)

        def _restore_connect_controls(self):
            # connect FAILED: bring back the port/verify/connect controls so the user
            # can adjust and try again, and leave a plain retry line in the log (the
            # error dialog closes; the fresh console shouldn't just go blank).
            if hasattr(self, "connect_busy"):
                self.connect_busy.pack_forget()
            if hasattr(self, "connect_success"):
                self.connect_success.pack_forget()
            self.connect_row.pack(fill="x", before=self.txt_probe)
            self.connect_help.pack(anchor="w", before=self.txt_probe)
            self._probe_log("\nAttempt failed — adjust the COM port (or turn on "
                            "Simulator mode on the Home screen) and try again.")

        def _show_connect_success(self, text):
            # connected: drop the pre-connect controls (port / verify / connect /
            # how-to) — they're pointless now — and show just the banner + console.
            self.connect_row.pack_forget()
            self.connect_help.pack_forget()
            if hasattr(self, "connect_busy"):
                self.connect_busy.pack_forget()
            self.lbl_connect_success.config(text=text)
            self.connect_success.pack(fill="x", pady=(0, 8), before=self.txt_probe)

        def _hide_connect_success(self):
            if not hasattr(self, "connect_success"):
                return
            self.connect_success.pack_forget()
            # restore the pre-connect controls (order: port row, then the blurb)
            self.connect_row.pack(fill="x", before=self.txt_probe)
            self.connect_help.pack(anchor="w", before=self.txt_probe)

        def _pick_lone_port(self, ports):
            """Choose FOR the user only when there is nothing to choose between.

            Refresh used to fill a dropdown and leave the field blank, so Connect
            failed telling you to click Refresh - which you had just done. Three
            Refresh buttons had their own private lambda; this is the one rule
            they all use now. It never overrules a port already chosen, and never
            picks between two.
            """
            if len(ports) == 1 and not (self.port_var.get() or "").strip():
                self.port_var.set(ports[0])

        def _refresh_ports(self):
            real_ports = list_serial_ports()
            self.cbo_port.config(values=real_ports)
            self._pick_lone_port(real_ports)
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
            """Reflect the simulator state in the single status-bar indicator
            (the redundant landing badge was removed — one place is enough)."""
            on = bool(self.sim_var.get())
            lbl = getattr(self, "lbl_sim", None)
            if lbl is not None:
                lbl.config(text="◆ SIMULATOR MODE" if on else "")
            # A full rehearsal ran against the simulator AT a bike before anyone
            # noticed. A status-bar badge is not where you look with a motorcycle
            # in front of you; the title bar is visible from every tab.
            try:
                self.title("%s v%s%s  —  Zero MBB console (Gen2)"
                           % (APP_NAME, __version__,
                              "   [SIMULATOR — NOT YOUR BIKE]" if on else ""))
            except Exception:
                pass

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
                # "Click Refresh after plugging in the cable" was an incomplete
                # remedy: Refresh fills the LIST, and unless exactly one port is
                # present you must then pick from it. At a bike that reads as
                # "click Refresh and you are sorted".
                messagebox.showerror(APP_NAME, "No port selected. Click Refresh "
                                     "(after plugging in the cable), then PICK your "
                                     "COM port from the dropdown — Refresh fills the "
                                     "list; it only chooses for you when exactly one "
                                     "port is present. Or turn on Simulator mode (Home "
                                     "screen, or Tools → Settings → Connection) to "
                                     "explore without a bike.")
                return
            # D1: every connection re-earns its phases (drops any --sim rehearsal
            # state and closes a previously-open port before reopening).
            self._reset_session_state()
            self._ensure_log_dir()          # G2: fall back if the save folder died
            is_simport = self.sim_var.get()
            # hide the port/verify/connect controls while we connect; they come back
            # only if it fails (owner: no point re-offering them mid-attempt).
            self._show_connecting()

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

            self._run_bg(job, done, on_error=self._restore_connect_controls)

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
                # themed to match the dark UI (was a pale-yellow OS default): a dark
                # panel surface, light text, a thin accent-grey border (the 1px win bg
                # showing through the label's pad — same trick as the menu popups).
                win.configure(bg=P["tooltip"])
                tk.Label(win, text=text, background=P["panel"], foreground=P["fg"],
                         justify="left", wraplength=320, padx=8, pady=5,
                         font=(self.sty["ui"], 9)).pack(padx=1, pady=1)
                state["win"] = win

            def hide(_e=None):
                if state["win"] is not None:
                    state["win"].destroy()
                    state["win"] = None

            widget.bind("<Enter>", show)
            widget.bind("<Leave>", hide)
            widget.bind("<Destroy>", hide)
            # expose the show/hide callables so tests can drive the tooltip WITHOUT
            # a synthetic <Enter> — X11/xvfb (no window manager) drops crossing
            # events to a non-viewable widget, so event_generate is unreliable on CI.
            widget._owl_tip_show = show
            widget._owl_tip_hide = hide

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
            # opt-in: also pull the heavy event log as part of the database pull. OFF
            # by default (it's minutes-long and can click the contactor open); when a
            # pull runs WITHOUT it, the output says so, so nobody thinks it was captured.
            self.baseline_heavy_var = tk.BooleanVar(value=False)
            cbh = ttk.Checkbutton(
                right, text="＋ also pull the full event log (heavy)",
                variable=self.baseline_heavy_var, style=self.sty["toggle"])
            cbh.pack(anchor="w", pady=(6, 0))
            self._add_tooltip(cbh, "Includes eventlogdump in the pull — the full ride/"
                              "event history that feeds the Rides + Charts tabs. Heavy "
                              "(minutes) and can briefly click the drivetrain contactor "
                              "open; keep the bike safely parked. (dumpall is NOT added "
                              "— it just repeats data the pull + event log already "
                              "capture, for double the contactor exposure.)")
            # the colour a command 'cell' border shows at rest (matches the panel, so
            # it's invisible until a pull turns it blue=running / green=done / red=fail)
            self._cell_bg = ttk.Style().lookup("TFrame", "background") or P["bg"]
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
            # each command button sits in a thin tk.Frame whose bg is its status
            # 'border' — a pull lights it blue (running) then green (captured) so you
            # can see the database fill in, per command. cmd_cells maps command->frame.
            self.cmd_cells, self.cmd_btns = {}, {}
            # buttons are laid out in the EXACT order Pull full database runs them
            # (baseline_read_order — `set` included, `obd` last), so the green
            # 'captured' borders fill in row by row as the pull progresses.
            for i, cmd in enumerate(baseline_read_order()):
                cell = tk.Frame(quick, bg=self._cell_bg)
                cell.grid(row=i // 2, column=i % 2, padx=2, pady=2, sticky="ew")
                b = ttk.Button(cell, text=cmd, width=12,
                               command=lambda c=cmd: self._read_cmd(c))
                b.pack(fill="x", padx=2, pady=2)
                self._add_tooltip(b, READ_TIPS.get(cmd, ""))
                self.cmd_cells[cmd], self.cmd_btns[cmd] = cell, b

            # E1: live "Watch" — repeat one light read on a timer (reads only, so
            # it stays fully inside the safety model). Great for a charge session.
            wrow = ttk.Frame(right)
            wrow.pack(fill="x", pady=(8, 0))
            self.watch_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(wrow, text="Watch", variable=self.watch_var,
                            command=self._toggle_watch).pack(side="left")
            self.watch_cmd = tk.StringVar(value="status")
            # the watch command sits in a status-border cell too, so it pulses blue
            # each time the timer fires a read (the same blue a running command shows).
            self.watch_cell = tk.Frame(wrow, bg=self._cell_bg)
            self.watch_cell.pack(side="left", padx=4)
            ttk.Combobox(self.watch_cell, textvariable=self.watch_cmd, width=8,
                         state="readonly",
                         values=["status", "bms", "inputs", "sevcon", "dash",
                                 "chargers"]).pack(padx=2, pady=2)
            self.watch_secs = tk.StringVar(value="5")
            ttk.Combobox(wrow, textvariable=self.watch_secs, width=3, state="readonly",
                         values=["3", "5", "10", "30"]).pack(side="left")
            ttk.Label(wrow, text="s", style="Muted.TLabel").pack(side="left", padx=(2, 0))

            # heavy / special commands — set apart at the bottom
            ttk.Separator(right).pack(fill="x", pady=(12, 8))
            ttk.Label(right, text="⚠ Heavy — may open the contactor",
                      foreground=P["warn"], wraplength=280).pack(anchor="w")
            for cmd in HEAVY_COMMANDS:
                cell = tk.Frame(right, bg=self._cell_bg)   # status border, like above
                cell.pack(fill="x", pady=2)
                hb = ttk.Button(cell, text=cmd,
                                command=lambda c=cmd: self._read_heavy(c))
                hb.pack(fill="x", padx=2, pady=2)
                self._add_tooltip(hb, READ_TIPS.get(cmd, ""))
                self.cmd_cells[cmd], self.cmd_btns[cmd] = cell, hb

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
                # neutral wording — this line shows on the Home screen too (which has
                # no visible "Connect tab", just a Connect button), so don't name a tab.
                lbl.config(text="○ Not connected — click Connect to begin",
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

        # -- A1/A2/A3/A5: heavy-read blocking modal ---------------------------
        def _show_read_modal(self, title):
            """A1: an application-modal, NON-blocking progress dialog for a heavy
            read. grab_set blocks stray human clicks; _read_modal_up is the real
            software interlock. Driven entirely via _cbq — NEVER wait_window (that
            would re-enter _read_heavy). Mirrors the non-blocking _show_cable_wizard."""
            from . import dialogs
            try:
                surface = ttk.Style().lookup("TFrame", "background") or P["bg"]
            except Exception:
                surface = P["bg"]
            win = tk.Toplevel(self)
            win.title("Reading — please wait")
            win.configure(bg=surface)
            win.resizable(False, False)
            try:
                win.transient(self)
            except Exception:
                pass
            win.protocol("WM_DELETE_WINDOW", lambda: None)   # no close until done
            body = ttk.Frame(win, padding=(24, 20))
            body.pack(fill="both", expand=True)
            ttk.Label(body, text=title, style="Heading.TLabel").pack(anchor="w")
            # plain Label (not a StringVar) — StringVars add dead-cycle GC pressure
            # the Tk-suite teardown force-collects against.
            status = ttk.Label(body, text=(
                "Reading the full log from the bike — several minutes at 38400 baud. "
                "Please don't touch anything until it finishes: the console can only "
                "do one thing at a time. The bike may click the contactor; that's "
                "expected and recovers when the read completes."),
                wraplength=400, justify="left", style="Muted.TLabel")
            status.pack(anchor="w", pady=(8, 12))
            bar = ttk.Progressbar(body, mode="indeterminate")
            bar.pack(fill="x")
            try:
                bar.start(12)
            except Exception:
                pass
            btnrow = ttk.Frame(body)
            btnrow.pack(fill="x", pady=(16, 0))
            self._read_modal_win = win
            self._read_modal_status = status
            self._read_modal_bar = bar
            self._read_modal_btnrow = btnrow
            self._read_modal_up = True
            self._refresh_action_buttons()       # hide safe-quit behind the modal
            dialogs._dark_titlebar(win)
            dialogs._center(win, self)
            # grab_set raises TclError until the window is viewable (mapped); retry
            # on the event loop instead of silently giving up (the grab is the
            # human-facing block, so it must actually take).
            def _grab(n=40):
                if not self._alive(win) or win is not self._read_modal_win:
                    return
                try:
                    win.grab_set()
                except tk.TclError:
                    if n > 0:
                        win.after(20, lambda: _grab(n - 1))
            _grab()
            return win

        def _modal_status(self, text):
            """A3: push a status line to the read modal (no-op if it's gone)."""
            s = self._read_modal_status
            if self._read_modal_up and s is not None:
                try:
                    if s.winfo_exists():
                        s.config(text=text)
                except Exception:
                    pass

        def _heavy_outcome_msg(self, text, err):
            """A4: classify a heavy read's terminal state into a plain message."""
            if err is not None:
                return ("The read stopped early: %s\n\nWhat was captured so far is "
                        "saved to the session folder." % err)
            if text and "### NOTE: event log captured" in text:
                return ("Done. The full event log was captured. The console prompt "
                        "wasn't seen at the very end, so the link resyncs on the "
                        "next command — the ride/health data is usable.")
            if text and "### TRUNCATED" in text:
                return ("The read ended before the full log arrived. What was "
                        "captured is saved; you can retry with the bike parked.")
            return "Done — the read completed and is saved."

        def _read_modal_finish(self, message, then=None):
            """A4: terminal state — stop the bar, show `message`, reveal ONE
            Continue button that tears the modal down and (if given) runs `then`."""
            win = self._read_modal_win
            if win is None or not self._alive(win):
                self._close_read_modal()          # nothing to show; ensure teardown
                if then:
                    then()
                return
            try:
                if self._read_modal_bar is not None and self._alive(self._read_modal_bar):
                    self._read_modal_bar.stop()
            except Exception:
                pass
            self._modal_status(message)

            def _continue():
                self._close_read_modal()
                if then:
                    then()
            self._read_modal_set_continue(_continue)

        def _read_modal_set_continue(self, command):
            row = self._read_modal_btnrow
            if row is None or not self._alive(row):
                return
            for w in row.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
            ttk.Button(row, text="Continue", style=self.sty["accent"],
                       command=command).pack(side="right")

        def _close_read_modal(self):
            """A5: the ONE teardown owner — release grab, destroy, clear the flag.
            Called from Continue, _on_close, and disconnect. NOT from the reboot
            re-gate (the finalizer owns the flip-to-Continue there)."""
            bar = self._read_modal_bar
            if bar is not None:
                try:
                    if self._alive(bar):
                        bar.stop()
                except Exception:
                    pass
            win = self._read_modal_win
            if win is not None:
                try:
                    if self._alive(win):
                        win.grab_release()
                        win.destroy()
                except Exception:
                    pass
            self._read_modal_win = None
            self._read_modal_status = None
            self._read_modal_bar = None
            self._read_modal_btnrow = None
            self._read_modal_up = False
            self._refresh_action_buttons()

        @staticmethod
        def _alive(w):
            try:
                return bool(w.winfo_exists())
            except Exception:
                return False

        def _heavy_consent(self, cmd):
            """The consent record for a heavy read, or None if it is not one.

            Minted only after the user has been through the contactor confirm.
            It is journalled before the first byte, so the capture itself
            answers "did somebody agree to this?" once the dialog is gone.
            """
            head = (str(cmd).strip().split() or [""])[0].lower()
            if head not in HEAVY_COMMANDS:
                return None
            return ("gui dialog accepted at %s"
                    % _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

        def _read_heavy(self, cmd, confirmed=False, out=None, then=None):
            # Refuse up front while another op is in flight — otherwise we'd open
            # the blocking modal and then _run_bg would refuse the read behind it,
            # leaving a stuck grab (the modal-owned launch only exempts the
            # _read_modal_up gate, not _busy). Mirrors _show_cable_wizard.
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current "
                                    "operation to finish, then try the log read.")
                return
            # A2: a heavy log dump can make the BMS open the drivetrain contactor
            # (it starves the MBB's CAN servicing) AND writes a PERMANENT "Line
            # Contactor o/c — VERY SEVERE" entry to the error log every time (C1).
            # Gate it behind an explicit warning + confirm.
            if not messagebox.askokcancel(APP_NAME,
                    "'%s' reads the full log (~1 MB, several minutes at 38400 baud).\n\n"
                    "On a keyed-on bike this can make the BMS briefly OPEN the "
                    "drivetrain contactor — you'll hear a click and the dash will "
                    "flash; it recovers when the read finishes. Each time it does, "
                    "the bike writes a PERMANENT 'Line Contactor o/c — VERY SEVERE' "
                    "entry to its error log that this app CANNOT clear (errorlogclear "
                    "is blocked). The bike must be SAFELY PARKED (never do this while "
                    "riding).\n\nRead only when you need the log. Continue?" % cmd):
                return
            # A2: raise the blocking modal BEFORE the first wire byte, then run the
            # read (modal=True routes completion through the Continue finalizer).
            self._show_read_modal("Reading '%s' from the bike" % cmd)
            # timeouts come from the one table now (transport.timeouts_for), so
            # this command cannot get a different idle window depending on which
            # button started it
            idle, _mx = timeouts_for(cmd)
            self._read_cmd(cmd, idle_timeout=idle, confirmed=confirmed, out=out,
                           then=then, modal=True,
                           heavy_consent=self._heavy_consent(cmd))

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
            # skip a tick if a read is in flight OR the heavy-read modal is up
            # (grab_set does NOT suppress this after()-timer; _busy clears before
            # the user presses Continue, so _read_modal_up is the gate here).
            if not self._busy and not self._read_modal_up:
                self._flash_watch()              # pulse the dropdown blue on each send
                self._read_cmd(self.watch_cmd.get())
            try:
                secs = max(2, int(self.watch_secs.get()))
            except (ValueError, TypeError):
                secs = 5
            self.after(secs * 1000, self._watch_tick)

        def _read_cmd(self, cmd, quiet=False, idle_timeout=None, confirmed=False,
                      out=None, then=None, modal=False, heavy_consent=None):
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
                    def upd():
                        self.lbl_prog.config(text="%s: %d KB" % (cmd, nbytes // 1024))
                        self._modal_status("Reading %s… %d KB captured so far."
                                           % (cmd, nbytes // 1024))     # A3
                    self._cbq.put(upd)
                out = self.transport.exec_command(
                    cmd, idle_timeout=idle,
                    max_time=900.0 if is_dump else 60.0,
                    progress_cb=prog if is_dump else None, confirmed=confirmed,
                    heavy_consent=heavy_consent)
                return out, self.transport.last_saved_path

            def apply_read(result):
                """The per-read side effects (emit, ingest, hints, logout re-gate).
                Additive; `then` is handled separately so a modal read can defer it
                to Continue. Returns the raw command text (for outcome classing)."""
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
                return out

            if modal:
                # A4: success/graceful/truncation and errors ALL converge on the one
                # Continue button; the modal finalizer runs `then` (success only).
                def done(result):
                    text = apply_read(result)
                    self._read_modal_finish(self._heavy_outcome_msg(text, None), then)

                def on_err():
                    err = self._last_read_error
                    self._last_read_error = None
                    self.lbl_prog.config(text="")
                    # error path never runs `then` (a partial pull parses nothing)
                    self._read_modal_finish(self._heavy_outcome_msg(None, err), None)

                started = self._run_bg(job, done, on_error=on_err,
                                       allow_during_modal=True,
                                       suppress_default_error=True)
                if not started:
                    # a read slipped in between the modal opening and here (e.g. a
                    # Watch tick during the confirm) — don't leave a stuck grab.
                    self._close_read_modal()
                    messagebox.showinfo(APP_NAME, "Busy — wait for the current "
                                        "operation to finish, then try again.")
            else:
                def done(result):
                    apply_read(result)
                    if then:                 # e.g. re-render Rides after a log pull
                        then()
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

        def _hardware_verified(self):
            """True only when we've positively confirmed this is the ONE bike the
            safety lists / parsers / recovery notes were validated on: a 2017 Zero
            FXS at MBB rev 41. Unknown model (before a Pull), a different model, or
            a non-41 rev all count as UNVERIFIED so the write / destructive confirms
            warn that the guidance may not apply."""
            rev = _parse_fw_rev(self.version_text or "")
            model = ""
            try:
                model = str((self.settings.get("model") or {}).get("value", ""))
            except Exception:
                pass
            return rev in KNOWN_FIRMWARE_REVS and model.strip().upper() == "FXS"

        def _unverified_hw_note(self):
            """A leading warning block for a confirm dialog when the bike isn't a
            verified FXS rev 41 (empty string when it is)."""
            if self._hardware_verified():
                return ""
            return ("!! UNVERIFIED HARDWARE — this bike is not a confirmed 2017 Zero "
                    "FXS at MBB rev 41, the only hardware OpenMBB's safety lists, "
                    "parsers, and the effect/recovery notes below were checked "
                    "against. On a different model or firmware they may be WRONG. "
                    "Proceed only if you understand the risk.\n\n")

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

            if not self._hardware_verified():
                ttk.Label(body, text=(
                    "⚠ UNVERIFIED HARDWARE — this bike is not a confirmed 2017 Zero "
                    "FXS at MBB rev 41. The effect/recovery notes below were checked "
                    "only on that bike and may be WRONG for a different model or "
                    "firmware."), wraplength=470, justify="left",
                    foreground=P["danger"]).pack(anchor="w", pady=(0, 10))

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

        # -- per-command status borders + watch pulse (blue=running, green=done) ----
        def _cell_color(self, state):
            return {"run": "#5aa8ff", "ok": P["green"], "bad": P["danger"],
                    "idle": self._cell_bg}.get(state, self._cell_bg)

        def _baseline_mark(self, cmd, state):
            """Colour a command's status-border cell (no-op for commands without a
            button, e.g. `set`). Called on the main thread via _cbq."""
            cell = getattr(self, "cmd_cells", {}).get(cmd)
            if cell is None:
                return
            try:
                if cell.winfo_exists():
                    cell.config(bg=self._cell_color(state))
            except Exception:
                pass

        def _baseline_reset_marks(self):
            for cell in getattr(self, "cmd_cells", {}).values():
                try:
                    if cell.winfo_exists():
                        cell.config(bg=self._cell_bg)
                except Exception:
                    pass

        def _flash_watch(self, ms=260):
            """Pulse the Watch command cell blue for `ms`, then back to rest — a
            visible 'a read just went out' cue timed to the watch frequency."""
            cell = getattr(self, "watch_cell", None)
            if cell is None:
                return
            try:
                cell.config(bg="#5aa8ff")
                self.after(ms, lambda: cell.winfo_exists()
                           and cell.config(bg=self._cell_bg))
            except Exception:
                pass

        def _baseline(self, then=None):
            # D4: run `obd` LAST — after `set` (the backup) and errorlogdump —
            # because its output has never been captured live; if it stalls or
            # returns nothing, the settings backup is already safely on disk first.
            seq = baseline_read_order()      # same order the command buttons use
            # opt-in: fold the heavy event log into the pull (informed-consent confirm
            # once, up front — it carries the contactor risk). If they decline, the
            # pull runs without it and the completion note says it wasn't captured.
            include_heavy = bool(self.baseline_heavy_var.get())
            # minted once, after the confirm below, and handed to the one command
            # that needs it; None for every ordinary read in the sequence
            heavy_consent = None
            if include_heavy:
                if messagebox.askokcancel(APP_NAME,
                        "You asked to also pull the full event log (eventlogdump) as "
                        "part of this database pull.\n\nIt's ~1 MB and takes several "
                        "minutes at 38400 baud, and on a keyed-on bike the BMS may "
                        "briefly OPEN the drivetrain contactor (click + dash flash; it "
                        "recovers when the read finishes). Keep the bike SAFELY "
                        "PARKED.\n\nInclude it?"):
                    seq = seq + ["eventlogdump"]
                    heavy_consent = self._heavy_consent("eventlogdump")
                else:
                    include_heavy = False
                    self.baseline_heavy_var.set(False)

            def job():
                results, errors = {}, {}
                for i, cmd in enumerate(seq):
                    # B3: progress bar + label AND a live play-by-play line; plus the
                    # per-command status border turns blue while this one runs.
                    self._cbq.put(lambda c=cmd, i=i: (
                        self.lbl_prog.config(text="pulling: %s (%d/%d)"
                                             % (c, i + 1, len(seq))),
                        self.prg.config(maximum=len(seq), value=i),
                        self._baseline_mark(c, "run"),
                        self._out("  [%d/%d] reading %s…" % (i + 1, len(seq), c)),
                        self._modal_status("Reading %s  (%d of %d)…"      # A2 path 4
                                           % (c, i + 1, len(seq)))))
                    is_dump = cmd in LONG_COMMANDS
                    prog = None
                    if is_dump:
                        def prog(n, c=cmd):
                            self._cbq.put(lambda: (
                                self.lbl_prog.config(
                                    text="pulling: %s (%d KB)" % (c, n // 1024)),
                                self._modal_status("Reading %s… %d KB captured "
                                                   "so far." % (c, n // 1024))))
                    # C6: each command tolerant — one failure doesn't discard the pass
                    try:
                        idle, mx = timeouts_for(cmd)
                        out = self.transport.exec_command(
                            cmd, idle_timeout=idle, max_time=mx,
                            progress_cb=prog,
                            heavy_consent=(heavy_consent
                                           if cmd in HEAVY_COMMANDS else None))
                        results[cmd] = out
                        self._cbq.put(lambda c=cmd: self._baseline_mark(c, "ok"))
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
                        self._cbq.put(lambda c=cmd: self._baseline_mark(c, "bad"))
                return results, errors, include_heavy

            def done(result):
                results, errors, heavy_included = result
                self.prg.config(value=0)
                self.prg.pack_forget()          # progress bar only shows during a pull
                # C7: record power-state context (on-charger contaminates iso/SOC)
                self._write_session_meta(results.get("status", ""))
                for cmd in seq:
                    if cmd in errors:
                        self._out("  [FAILED] %s: %s" % (cmd, errors[cmd]))
                if results.get("help") and not self.logged_in:
                    self.help_logged_out = results["help"]
                self._ingest_settings(results.get("set", ""), snapshot_baseline=True)
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
                    self._log_trend_cache = None
                    self._apply_gates()
                    # confirmation right under the Pull button (owner)
                    self.lbl_prog.config(text="✓ Full database pulled & backed up",
                                         foreground=P["green"])
                    self._out("\n=== Full database pulled -> %s ===" % self.logger.dir)
                    if errors:
                        self._out("(%d command(s) failed; retry them with the read "
                                  "buttons: %s)" % (len(errors), ", ".join(errors)))
                    self._out("→ Backup saved. Review it on the Analyze tab.")
                    if not heavy_included:
                        self._out("  Note: the full event log (eventlogdump) was NOT "
                                  "captured — it's heavy (minutes, and can click the "
                                  "contactor open). Tick '＋ also pull the full event "
                                  "log' above and re-pull, or use Analyze → Rides → "
                                  "'Pull ride log from bike', to include it.")
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
            self._baseline_reset_marks()          # clear last pull's status borders
            self.prg.pack(fill="x", pady=(4, 2), before=self.lbl_prog)  # show progress
            if include_heavy:
                # A2 path 4: the heavy opt-in bypasses _read_heavy, so raise the SAME
                # blocking modal here (same contactor + stray-click exposure) and
                # route completion through the Continue finalizer.
                self._show_read_modal("Pulling the full database + event log")

                def done_modal(result):
                    done(result)                  # normal completion (gates, notes)
                    _res, errs, _hv = result
                    if errs:
                        msg = ("Database pull complete — %d command(s) failed (retry "
                               "them with the read buttons). Backup saved." % len(errs))
                    else:
                        msg = "Database pull complete — everything read and backed up."
                    self._read_modal_finish(msg, None)

                def on_err_modal():
                    err = self._last_read_error
                    self._last_read_error = None
                    self._read_modal_finish(
                        "The pull stopped early: %s\n\nAny data read before that is "
                        "saved to the session folder." % err, None)

                started = self._run_bg(job, done_modal, on_error=on_err_modal,
                                       allow_during_modal=True,
                                       suppress_default_error=True)
                if not started:                    # busy race — no stuck grab
                    self._close_read_modal()
                    messagebox.showinfo(APP_NAME, "Busy — wait for the current "
                                        "operation to finish, then re-run the pull.")
            else:
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
                    # Second line, and an integer with no minor part: the reader
                    # has exactly one decision to make (read it or refuse it),
                    # and a second version-shaped string nobody compares would
                    # sit three lines above app_version, which is already one.
                    "capture_format: %d" % sessions.CAPTURE_FORMAT,
                    "time: %s" % _dt.datetime.now().isoformat(timespec="seconds"),
                    "app_version: %s" % __version__,
                    "firmware_rev: %s" % (rev if rev is not None else "?"),
                    "power_mode: %s" % (mode or "?")]
            # Ask the transport what it is connected to rather than reading the
            # folder name. The `_sim` tag lives in that name - the very field a
            # privacy fix has to normalise away, and one a person can rename -
            # so a rehearsal against the shipped simulator produced a clean
            # report presentable as a bike's page. Omitted, never guessed, when
            # there is no port to ask: silence reads as format-1 real history,
            # and writing "serial" over a connection nobody checked is exactly
            # the false claim this stamp exists to prevent.
            port = getattr(getattr(self, "transport", None), "port", None)
            if port is not None:
                meta.append("source: %s" % (
                    "simulator" if isinstance(port, SimPort)
                    else (getattr(port, "port", None) or "serial")))
            if self.logger:
                self.logger.save_named("session_meta.txt", "\n".join(meta) + "\n")
            if mode and "charg" in mode.lower():
                self._out("[note] data pulled while CHARGING — the isolation "
                          "reading and SOC context are NOT valid off-charger; "
                          "re-read unplugged + dry before acting on them.")

        def _ingest_settings(self, dump_text, snapshot_baseline=False):
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
            # a "clean full read" (Pull full database / post-login set) defines the
            # reset-to-default target; a write-verify or a one-off `set` read does NOT
            # move it, so '↺ Reset' always points back to before you changed things.
            if snapshot_baseline:
                self._baseline_settings = {n: settings[n]["value"] for n in settings}
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
                            "tries it automatically next time? It is stored IN CLEAR "
                            "TEXT in your config file (~/.openmbb/config.json) — say no "
                            "if it is a password you use anywhere else. You can clear it "
                            "via Tools → Settings → Login."):
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
                    self._ingest_settings(post["set"], snapshot_baseline=True)
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

            def _fits():
                bbox = canvas.bbox("all")
                return bool(bbox) and (bbox[3] - bbox[1]) <= canvas.winfo_height()

            def _sync(_e=None):
                canvas.configure(scrollregion=canvas.bbox("all"))
                # when the content fits the viewport, pin it to the TOP — otherwise a
                # two-finger scroll drifts it into empty space, leaving a gap above the
                # header (owner: content height changes as the description card toggles).
                if _fits():
                    canvas.yview_moveto(0)
            f.bind("<Configure>", _sync)
            canvas.bind("<Configure>",
                        lambda e: (canvas.itemconfigure(win, width=e.width), _sync()))

            def _wheel(e):
                if not _fits():                  # only scroll when it actually overflows
                    canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            canvas.bind("<MouseWheel>", _wheel)
            # stash so the caller can extend wheel/two-finger scroll to the page's
            # static child widgets (they'd otherwise swallow the event). See
            # _bind_page_wheel.
            f._owl_wheel = _wheel
            return f

        def _bind_wheel_subtree(self, root, wheel):
            """Bind <MouseWheel> -> `wheel` on `root` and every descendant that doesn't
            scroll itself (Treeview / Text / Listbox). Safe to call repeatedly on a
            REBUILT subtree — per-widget binds only, never bind_all (which churned the
            interpreter-global bindtag across app lifecycles and broke the suite once)."""
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
            walk(root)

        def _bind_page_wheel(self, inner):
            """Route wheel / two-finger scroll over the page's static widgets to the
            scrollable canvas — child widgets otherwise eat the event so only the
            scrollbar drag worked."""
            self._bind_wheel_subtree(inner, getattr(inner, "_owl_wheel", None))

        # -- Phase 3: Writes -------------------------------------------------------
        def _build_write_tab(self):
            f = self._scrollable_tab(" Writes ")     # D3: visible scrollbar
            # header row: title on the left, the master UNLOCK toggle right-aligned
            # in the same row (owner) so the gate sits with the page title.
            hdr = ttk.Frame(f)
            hdr.pack(fill="x")
            htitle = ttk.Frame(hdr)
            htitle.pack(side="left", anchor="w")
            ttk.Label(htitle, text="Change a setting",
                      style="Heading.TLabel").pack(anchor="w")
            ttk.Label(htitle, text="advanced — whitelisted, backed up, reversible",
                      style="Muted.TLabel").pack(anchor="w", pady=(1, 0))
            self.unlock_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(hdr, text="UNLOCK WRITES (master gate)",
                            style=self.sty["toggle"],
                            variable=self.unlock_var).pack(side="right", anchor="e")
            ttk.Frame(f, height=12).pack()      # match _tab_header's bottom spacing
            # D2/D4: one concise line (no redundant options button — it duplicated the
            # per-row description; the read-only reference is Help → Command reference).
            ttk.Label(f, foreground=P["dim"], wraplength=940, justify="left",
                      text="Arming UNLOCK only enables writing — it changes nothing on "
                      "its own. To change a setting: click its 'New value' cell and type "
                      "a value (a '✎ Write →' appears on that row), arm UNLOCK WRITES "
                      "(top-right), then click Write. Every write backs up all settings "
                      "first, then reads the value back to verify — change one thing at a "
                      "time. A row you have written shows '↺ Put back <value>' — the "
                      "value it held before your first write to it this session. "
                      "Rows below are the settings your bike reports as safe to change."
                      ).pack(anchor="w", pady=(2, 6))

            # owner: edit in the table — double-click a row's "New value" to type a
            # value, then click "Write" on that same row. No separate input box.
            cols = ("name", "current", "risk", "new", "write")
            heads = ("Setting", "Current", "Risk", "New value", "")
            # Risk was 90 px and clipped mid-word ("SAFE, but exactly 0 is refus")
            # on the screen whose whole job is telling you what a write will do.
            widths = (150, 110, 260, 130, 90)
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
            self._restyle_trees()
            self.tree.bind("<<TreeviewSelect>>", self._show_effect)
            self.tree.bind("<Double-1>", self._writes_edit_cell)
            self.tree.bind("<Button-1>", self._writes_action_click)
            self._pending_writes = {}

            # description = a CARD (panel-grey, thin border) shown ONLY when a setting
            # row is clicked (owner: no default/placeholder text — the how-to lives in
            # the intro above). Built now but kept hidden until _show_effect packs it.
            self.effect_panel = ttk.Frame(f)      # packed on demand by _show_effect
            card = tk.Frame(self.effect_panel, bg=P["panel"], highlightthickness=1,
                            highlightbackground=P["border"], highlightcolor=P["border"])
            card.pack(fill="x")
            self.effect_card = tk.Frame(card, bg=P["panel"])   # inner padding surface
            self.effect_card.pack(fill="x", padx=12, pady=10)
            # remember the page's wheel handler so the rebuilt card re-binds two-finger
            # scroll (its dynamic labels would otherwise be a scroll dead zone).
            self._writes_wheel = getattr(f, "_owl_wheel", None)

            # (No separate revert journal (owner): a changed row shows '↺ Reset' inline,
            # which puts back the pre-write value through the same safe write
            # flow. The on-disk journal is still written as the audit trail.)

            self._bind_page_wheel(f)   # wheel/two-finger scroll over the whole page

        def _refresh_write_rows(self):
            if not hasattr(self, "tree"):
                return
            self.tree.delete(*self.tree.get_children())
            self._pending_writes = {}
            for name in self.settings_order:
                if name in WRITE_WHITELIST and name in self.settings:
                    self.tree.insert("", "end", iid=name, values=(name, "", "", "", ""))
                    self._render_write_row(name)

        def _put_back_value(self, name):
            """What this row held before YOUR first write to it, or None.

            Replaces `_row_differs_from_baseline`, whose docstring said "the user
            has changed this setting" and whose code meant "differs from the last
            clean full read". Those agree until they do not, and at a bike they
            did not: a row the BIKE moved differed from the snapshot, so the tool
            offered to undo a change the rider had never made.
            """
            pre = self._user_wrote.get(name)
            cur = self.settings.get(name, {}).get("value")
            if pre is None or cur is None:
                return None
            if first_number(pre) == first_number(cur):
                return None          # already back where it started
            return pre

        def _render_write_row(self, name):
            """Paint a row's cells + action from state: a staged '✎ Write →', or a
            '↺ Put back <value>' for a row YOU wrote, '↔ moved by ...' for one the
            bike moved, else nothing. Never a diff against a full read: that is
            what offered to undo changes the rider never made."""
            if name not in WRITE_WHITELIST or name not in self.settings:
                return
            _l, _e, risk, _v, _w = WRITE_WHITELIST[name]
            cur = self.settings.get(name, {}).get("value", "")
            base = (name, cur, risk.split(" - ")[0])
            pre = self._put_back_value(name)
            if name in self._pending_writes:
                self.tree.item(name, values=base + (self._pending_writes[name],
                               "✎ Write →"), tags=("pending",))
            elif pre is not None:
                # The value is IN the label. It is the value before your write,
                # whether or not that was a good one - "Reset"/"restore" implied
                # known-good, and at the bike the last-read value was a transient
                # the Zero app had written ninety seconds earlier.
                self.tree.item(name, values=base + (
                    "", "↺ Put back %s" % first_number(pre)), tags=("reset",))
            elif name in self._moved_by:
                # The bike moved this in response to a write of ours. Informational
                # only: no write affordance, because on rev 41 these are the
                # derived values that answer FAILED (see REFUSED_ON_REV41).
                self.tree.item(name, values=base + (
                    "", "↔ moved by your %s write" % self._moved_by[name]),
                    tags=("moved",))
            else:
                tag = "safe" if risk.startswith("SAFE") else "caution"
                self.tree.item(name, values=base + ("", ""), tags=(tag,))

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

        def _write_help_parts(self, name):
            """Structured (keyword, body, colour) rows for the styled description
            panel (the string form _write_help_lines still feeds the confirm dialog)."""
            it = self._write_help_map().get(str(name).strip().lower())
            if not it:
                return []
            out = []
            if it.get("what_it_does"):
                out.append(("What it does", it["what_it_does"], "#7fe0a0"))
            if it.get("caution"):
                out.append(("Caution", it["caution"], P["warn"]))
            if it.get("seen_on_rev41") is False:
                out.append(("Note", "not confirmed on the verified rev-41 bike.",
                            self._CARD_DIM))
            return out

        # Secondary text inside the effect card — a shade off P["dim"] so the
        # card reads as its own surface without an app-wide contrast change.
        # A property, not a class constant: a class attribute would freeze the
        # dark-mode value at import and survive a theme switch.
        @property
        def _CARD_DIM(self):
            return P["carddim"]

        def _clear_effect(self):
            for w in self.effect_card.winfo_children():
                w.destroy()

        def _effect_line(self, keyword, text, color):
            """A keyworded paragraph on the card: a bold colour-coded keyword, then an
            indented wrapped body at a comfortable reading measure (~680px, 10pt)."""
            tk.Label(self.effect_card, text=keyword, foreground=color, bg=P["panel"],
                     font=(self.sty["ui"], 10, "bold")).pack(anchor="w", pady=(6, 0))
            tk.Label(self.effect_card, text=text, wraplength=680, justify="left",
                     foreground=P["fg"], bg=P["panel"],
                     font=(self.sty["ui"], 10)).pack(anchor="w", padx=(18, 0))

        def _hide_effect(self):
            """No selection -> hide the description card entirely (owner: no default
            text; the card appears only when a setting row is clicked)."""
            self._clear_effect()
            try:
                self.effect_panel.pack_forget()
            except Exception:
                pass

        def _show_effect(self, _evt=None):
            sel = self.tree.selection()
            if not sel:
                return
            self._clear_effect()
            if self.effect_panel.winfo_manager() != "pack":
                self.effect_panel.pack(anchor="w", fill="x", pady=(8, 0))
            name = sel[0]
            label, effect, risk, _v, _w = WRITE_WHITELIST[name]
            tk.Label(self.effect_card, text="%s — %s" % (name, label), bg=P["panel"],
                     foreground=P["fg"], font=(self.sty["ui"], 12, "bold")).pack(
                         anchor="w")
            safe = risk.upper().startswith("SAFE")
            chip = tk.Frame(self.effect_card, bg=P["panel"])
            chip.pack(anchor="w", pady=(5, 2))
            tk.Label(chip, text=" RISK ", bg=(P["green"] if safe else P["warn"]),
                     fg=P["chipfg"], font=(self.sty["ui"], 8, "bold")).pack(
                         side="left", padx=(0, 5))
            tk.Label(chip, text=risk, bg=P["panel"],
                     foreground=(P["green"] if safe else P["warn"]),
                     font=(self.sty["ui"], 9)).pack(side="left")
            self._effect_line("EFFECT", effect, "#8fd0ff")
            for keyword, body, color in self._write_help_parts(name):
                self._effect_line(keyword, body, color)
            # dynamic labels just replaced the card contents -> re-arm two-finger scroll
            self._bind_wheel_subtree(self.effect_panel,
                                     getattr(self, "_writes_wheel", None))

        def _writes_edit_cell(self, event):
            """Double-click ANYWHERE on a whitelisted row -> inline editor over its
            'New value' cell (forgiving: you don't have to hit that column exactly)."""
            row = self.tree.identify_row(event.y)
            if row and row in WRITE_WHITELIST and row in self.settings:
                self._open_new_editor(row)
                return "break"

        def _open_new_editor(self, row):
            """Place an inline Entry over a row's 'New value' cell to type into. Used
            by both the single-click-on-the-cell and double-click-the-row paths."""
            if getattr(self, "_writes_editing_row", None) == row:
                return                           # already editing this row
            self.tree.selection_set(row)
            self.tree.see(row)
            self._show_effect()
            self.tree.update_idletasks()
            bbox = self.tree.bbox(row, "new")
            if not bbox:
                return
            self._writes_editing_row = row
            x, y, w, h = bbox
            var = tk.StringVar(value=self._pending_writes.get(row, ""))
            ent = tk.Entry(self.tree, textvariable=var, font=(self.sty["mono"], 10),
                           bg=P["field"], fg=P["fg"], insertbackground=P["fg"],
                           relief="flat", highlightthickness=1,
                           highlightbackground=P["green"], highlightcolor=P["green"])
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.icursor("end")
            ent.select_range(0, "end")

            done = {"v": False}                 # Return destroys -> FocusOut fires too

            def clear_marker():
                # only clear if it still points at THIS row (a new editor for a
                # different row may already have claimed it via focus-steal)
                if getattr(self, "_writes_editing_row", None) == row:
                    self._writes_editing_row = None

            def commit(_e=None):
                if done["v"]:
                    return
                done["v"] = True
                val = var.get().strip()
                try:
                    ent.destroy()
                except Exception:
                    pass
                clear_marker()
                self._set_pending_write(row, val)

            def cancel(_e=None):
                done["v"] = True
                clear_marker()
                ent.destroy()
            ent.bind("<Return>", commit)
            ent.bind("<FocusOut>", commit)
            ent.bind("<Escape>", cancel)

        def _set_pending_write(self, name, val):
            """Stage/clear an inline pending write for a row, then repaint it."""
            if name not in WRITE_WHITELIST or name not in self.settings:
                return
            if name in REFUSED_ON_REV41 and _parse_fw_rev(self.version_text) == 41:
                # Watched to fail three times at a bike. Offering the write
                # anyway sent the rider into three refusals chasing a change
                # the firmware computes for itself.
                messagebox.showinfo(
                    APP_NAME,
                    "%s cannot be written on this firmware.\n\nThe console "
                    "answers FAILED every time (verified on rev 41). It is a "
                    "DERIVED value: the bike computes it from its 'x10' sibling "
                    "against a gearing-dependent ceiling, which is why it moves "
                    "on its own when you change sprockets.\n\nOpenMBB reads and "
                    "shows it, but cannot write the 'x10' sibling either — that "
                    "one is not on the whitelist and the console refuses it from "
                    "the Console tab. What you CAN change are the sprocket "
                    "settings that move it." % name)
                self._pending_writes.pop(name, None)
                self._render_write_row(name)
                return
            cur = self.settings.get(name, {}).get("value", "")
            if not val or first_number(val) == first_number(cur):
                self._pending_writes.pop(name, None)
            else:
                self._pending_writes[name] = val
            self._render_write_row(name)

        def _writes_action_click(self, event):
            """Single click on the action cell (#5): apply a staged '✎ Write →', or
            '↺ Put back' the row to the value it held before your first write to it
            this session. A click on the 'New
            value' cell (#4) opens the inline editor."""
            row = self.tree.identify_row(event.y)
            if not row or row not in WRITE_WHITELIST or row not in self.settings:
                return
            col = self.tree.identify_column(event.x)
            if col == "#5":
                if row in self._pending_writes:
                    self._write_value(row, self._pending_writes[row])
                    return "break"
                pre = self._put_back_value(row)
                if pre is not None:
                    self._write_value(row, first_number(pre))
                    return "break"
                if row in self._moved_by:
                    messagebox.showinfo(APP_NAME,
                        "%s was changed by the bike, not by you.\n\nIt moved when "
                        "you wrote %s — the firmware computes it from other "
                        "settings. OpenMBB did not write it and will not offer "
                        "to; putting %s back is what moves it back."
                        % (row, self._moved_by[row], self._moved_by[row]))
                    return "break"
            if col == "#4":                      # New-value cell -> start editing
                self._open_new_editor(row)
                return "break"

        def _backup_on_disk(self):
            """The settings backup, re-read and re-parsed from disk.

            `baseline_done` only records that a dump once parsed in memory. This
            asks the stronger question the write gate is actually relying on:
            is there a file, right now, that still parses into settings? A write
            is the one operation whose undo IS that file.
            """
            logger = getattr(self, "logger", None)
            if logger is None or not getattr(logger, "dir", None):
                return None
            try:
                names = sorted(n for n in os.listdir(logger.dir)
                               if n.startswith("settings_baseline")
                               and n.endswith(".txt"))
            except OSError:
                return None
            for name in reversed(names):          # newest first
                path = os.path.join(logger.dir, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        parsed, _order = parse_settings_dump(fh.read())
                except OSError:
                    continue
                if parsed:
                    return path
            return None

        def _write_refused(self, why):
            messagebox.showwarning(
                APP_NAME,
                "Write refused \u2014 %s.\n\nEvery write on this bike needs all "
                "four: a connection, a login, a settings backup that parses, and "
                "the master UNLOCK WRITES gate. The backup is the only undo a "
                "write has." % why)

        def _write_value(self, name, new_val):
            # The four links of the write chain, checked HERE. Three of them used
            # to be enforced only by the Writes tab being disabled - a property of
            # the user interface, not a check this function performs.
            #
            # The firmware-refusal gate is here too, and not only at staging. It
            # was at staging alone, and the '↺ Reset' action calls this function
            # directly - so the exact bike-day sequence (write spfront, watch an
            # _allow row drift, click Reset) still sent a write the console
            # answers FAILED to. Every caller passes through here.
            if (name in REFUSED_ON_REV41
                    and _parse_fw_rev(self.version_text) == 41):
                return self._write_refused(
                    "%s cannot be written on this firmware — the console answers "
                    "FAILED every time (verified on rev 41). The bike computes it "
                    "from its 'x10' sibling against a gearing-dependent ceiling, "
                    "which is why it moves on its own when the sprockets change."
                    % name)
            if not self.connected:
                return self._write_refused("not connected to the bike")
            if not self.logged_in:
                return self._write_refused("not logged in")
            if not self._backup_on_disk():
                return self._write_refused(
                    "no settings backup on disk that still parses")
            if not self.unlock_var.get():
                return self._write_refused("the master UNLOCK WRITES gate is off")
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
                text = ("%s%s — %s\n\n%s  ->  %s\n\nEFFECT: %s\nRISK: %s\n%s\n"
                        "What happens when you click OK:\n"
                        "  1. a full backup of ALL current settings is saved to the "
                        "session folder,\n"
                        "  2. the change is sent, then read back to VERIFY it took.\n\n"
                        "Afterward, that row shows '↺ Put back %s' — this value, "
                        "the one it holds right now. Proceed?"
                        % (self._unverified_hw_note(), name, label, old_val, new_val,
                           effect, risk, notes, old_val))
                if not messagebox.askokcancel("Confirm write", text):
                    return

                def job2():
                    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    self.logger.save_named("settings_backup_%s.txt" % stamp, dump)
                    # D4: journal INTENT before the write reaches the wire, so a failure
                    # between send and verify still records that the bike may have
                    # changed — the on-disk journal is the audit trail the design leans
                    # on (the UI's revert is now the inline '↺ Reset' on the row).
                    self.logger.journal_write(name, old_val, new_val, ok=None)  # PENDING
                    # The reply is KEPT. It used to be discarded, and the dialog
                    # then asserted "the console reported SUCCESS" over a wire
                    # that had said FAILED.
                    reply = self.transport.write_setting(name, new_val,
                                                         idle_timeout=2.5)
                    said_kind, said = parsers.console_write_result(reply)
                    verify = self.transport.exec_command("set", idle_timeout=4.0,
                                                         max_time=120.0)
                    live2, _ = parse_settings_dump(verify)
                    got = live2.get(name, {}).get("value", "")
                    verified = first_number(got) == first_number(new_val)
                    # Both dumps were already read, so the collateral diff is
                    # free: writing spfront moved four regen settings nobody
                    # touched, and the revert did not put them back.
                    # `dump` is the pre-write full read from job(); parse it
                    # here because job()'s own `live` is not in this scope.
                    live_before, _ = parse_settings_dump(dump)
                    moved = parsers.settings_diff(live_before, live2,
                                                 ignore=(name,))
                    self.logger.journal_write(name, old_val, new_val, verified,
                                              got=got,
                                              said=said if said_kind == "failed"
                                              else None)
                    for mname, mold, mnew in moved:
                        # Not journal_write: this is not a write we performed,
                        # and calling it one forced a PENDING that reads as an
                        # interrupted authorized change.
                        self.logger.journal_observed(
                            mname, mold, mnew,
                            "moved by the %s write" % name)
                    return got, verified, verify, said_kind, said, moved

                def done2(r2):
                    got, verified, verify_dump, said_kind, said, moved = r2
                    # setdefault, not assignment: a second write to the same row
                    # must keep the ORIGINAL pre-value, or Reset would only undo
                    # the last step of several.
                    self._user_wrote.setdefault(name, old_val)
                    # ...and if this write put the row back where it started,
                    # there is nothing left to undo. AFTER the setdefault, and on
                    # the main thread: this block first landed in job2, where it
                    # ran before the pre-value existed and touched GUI state from
                    # a worker.
                    if verified and first_number(got) == first_number(
                            self._user_wrote.get(name, "")):
                        self._user_wrote.pop(name, None)
                    for _mn, _mo, _mnw in moved:
                        self._moved_by.setdefault(_mn, name)
                    self._ingest_settings(verify_dump)   # current now reflects the write
                    # A write can move settings the user never touched. Said
                    # first and always, verified or not: at the bike this went
                    # unreported and the rider's brake regen sat at 90% instead
                    # of 77% until it was restored by hand.
                    if moved:
                        messagebox.showwarning(APP_NAME,
                            "Your %s write also changed %d other setting(s) that "
                            "you did not ask for:\n\n%s\n\nThe bike did this, "
                            "not OpenMBB. Undoing %s may NOT put them back — "
                            "check them before you ride."
                            % (name, len(moved),
                               "\n".join("  %s: %s -> %s" % m for m in moved),
                               name))
                    # A read-back that did not come back is not a value, and
                    # nothing below may describe it as one. This is its own
                    # branch because every other branch's sentence would be a
                    # claim about a check that could not run.
                    unknown = not str(got).strip()
                    if not verified:
                        # What the console SAID is quoted, never asserted. This
                        # branch used to state "the console reported SUCCESS"
                        # literally - and said it at a bike over a reply that
                        # read FAILED.
                        if said_kind == "failed":
                            if unknown:
                                tail = ("The verify read did not return %s, so "
                                        "what the bike holds now is UNKNOWN — "
                                        "re-read the settings before you ride."
                                        % name)
                            elif moved or first_number(got) != first_number(old_val):
                                # "Nothing was changed" is a claim, and this
                                # write demonstrably changed something.
                                tail = ("%s now reads %r — and this write still "
                                        "moved other settings (see the previous "
                                        "message)." % (name, got)) if moved else (
                                    "%s now reads %r, which is neither the value "
                                    "you wrote nor the one it had."
                                    % (name, got))
                            else:
                                tail = "%s is still %r. Nothing was changed." % (
                                    name, got)
                            messagebox.showwarning(APP_NAME,
                                "The bike REFUSED this write. The console said:"
                                "\n\n    %s\n\n%s" % (said, tail))
                        elif unknown:
                            messagebox.showwarning(APP_NAME,
                                "Could not verify the write to %s: the read-back "
                                "did not return that setting at all.\n\nThis is "
                                "NOT a report that the bike changed or refused "
                                "anything — it means the check could not run. "
                                "What %s holds now is UNKNOWN; re-read the "
                                "settings before you ride.%s"
                                % (name, name,
                                   ("\n\nThe console said: %s" % said)
                                   if said else ""))
                        elif first_number(got) == first_number(old_val):
                            # "No harm done" is a claim about the whole write,
                            # and a write that moved other settings did harm
                            # somewhere. Same guard the refusal branch got; it
                            # was fixed there and missed here.
                            harm = ("" if moved else
                                    " (No harm done — the bike kept its "
                                    "previous value.)")
                            extra = ("\n\nBut this write DID move other "
                                     "settings — see the previous message."
                                     if moved else "")
                            messagebox.showwarning(APP_NAME,
                                "%s did not change: it still reads %r, the value it "
                                "had before your write of %r.%s%s%s"
                                % (name, got, new_val,
                                   ("\n\nThe console said: %s" % said)
                                   if said else "", harm, extra))
                        else:
                            # A third value: the bike took the command and chose
                            # its own figure. Naming that is more use than
                            # calling it a mismatch - the whitelist already
                            # documents this for maxcustspmph (102 -> 89).
                            messagebox.showwarning(APP_NAME,
                                "The bike set %s to %r, not the %r you asked for — "
                                "it clamped or adjusted the value.%s\n\nThe journal "
                                "records %r as what the bike now reports."
                                % (name, got, new_val,
                                   ("\n\nThe console said: %s" % said)
                                   if said else "", got))
                        try:
                            self.tree.selection_set(name)
                            self.tree.see(name)
                        except Exception:
                            pass
                self._run_bg(job2, done2)

            self._run_bg(job, confirm_and_send)

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

        # -- Analyze tab (Health / Condition / Rides / Charts / Compare) -----
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

            sub = self.analyze_nb = ttk.Notebook(f)
            sub.pack(fill="both", expand=True, pady=(8, 0))

            # Health
            hf = ttk.Frame(sub, padding=8)
            sub.add(hf, text=" Health ")
            cols = ("metric", "value", "status")
            self.health_tree = ttk.Treeview(hf, columns=cols, show="headings", height=12)
            for c, w in zip(cols, (200, 260, 90)):
                self.health_tree.heading(c, text=c.title())
                self.health_tree.column(c, width=w, anchor="w")
            self._restyle_trees()
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

            # Condition — what the ride/charge SAMPLES say about the pack, as
            # distinct from Health, which reads single current values. Nothing
            # here is graded: two of its three measurements have no reference
            # bike to be judged against yet.
            nf = ttk.Frame(sub, padding=8)
            sub.add(nf, text=" Condition ")
            self.lbl_cond_verdict = ttk.Label(
                nf, text="Load a session to assess the pack.",
                font=(self.sty["ui"], 11, "bold"), wraplength=980,
                justify="left", foreground=P["dim"])
            self.lbl_cond_verdict.pack(anchor="w", pady=(0, 8))
            ccols = ("check", "finding")
            self.cond_tree = ttk.Treeview(nf, columns=ccols, show="headings",
                                          height=12)
            for c, hd, w in zip(ccols, ("Check", "What the log says"), (210, 660)):
                self.cond_tree.heading(c, text=hd)
                self.cond_tree.column(c, width=w, anchor="w")
            self._restyle_trees()
            self.cond_tree.pack(fill="both", expand=True)
            self._attach_tree_copy(self.cond_tree)
            # Treeview cells do not wrap and these findings are sentences, so the
            # full text of the selected row goes here — the same pattern the
            # Health tab uses for its per-metric explanations.
            self._cond_hint = ("Click any row for its full text. The verdict grades "
                               "only what can be judged without another bike to "
                               "compare against: the weakest cell against its own "
                               "pack, absolute cell voltage under load, resting "
                               "spread, and faults. Charge capacity and discharge "
                               "allowance are measured but NOT graded. Rows marked "
                               "'not determined' are checks this capture could not "
                               "answer — they are not passes.")
            self.lbl_cond_note = ttk.Label(nf, text=self._cond_hint, wraplength=980,
                                           foreground=P["dim"], justify="left")
            self.lbl_cond_note.pack(anchor="w", pady=(6, 0))
            self.cond_tree.bind("<<TreeviewSelect>>", self._cond_note)

            # Rides
            rf = ttk.Frame(sub, padding=8)
            sub.add(rf, text=" Rides ")
            rbtns = ttk.Frame(rf)
            rbtns.pack(fill="x")
            ttk.Button(rbtns, text="Pull ride log from bike  →",
                       style=self.sty["accent"],
                       command=self._pull_ride_log).pack(side="left")
            ttk.Button(rbtns, text="Load ride log (.txt)…",
                       command=self._load_ride_log).pack(side="left", padx=8)
            self.lbl_ride_totals = ttk.Label(
                rf, text="Ride telemetry comes straight off the bike — no Zero app, no "
                ".bin files, no external decoder. 'Pull ride log from bike' runs the "
                "console's eventlogdump (the full event log). It's a heavy read (a few "
                "minutes) and can briefly click the drivetrain contactor open, so keep "
                "the bike SAFELY PARKED. Then you'll see per-ride distance, SOC%/km and "
                "temps here, and the ride charts on the Charts tab. (Already have a "
                "decoded log file? Use 'Load ride log (.txt)…'.)",
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

        def _session_log_peak(self, session):
            """The hottest pack sample in a session's event log, or None.

            Read once per session rather than per render: the Health tab redraws
            on a tab switch and on every unit change, and the event log is about
            a megabyte.
            """
            if session is None:
                return None
            text = parsers.event_log_text(session)
            if not text:
                return None
            pk = condition_mod.pack_peak(parsers.parse_ride_log(text))
            return pk["pack_temp_c"] if pk else None

        def _analyze_set(self, session):
            self.analyze_session = session
            self._log_peak_c = self._session_log_peak(session)
            self._render_health()
            self._render_condition()
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

        def _render_condition(self):
            """Fill the Condition tab from condition.assess()."""
            self.cond_tree.delete(*self.cond_tree.get_children())
            if not self.analyze_session:
                return
            text = parsers.event_log_text(self.analyze_session)
            tu = config.get_temp_units()
            # hoisted: assess now wants the lifetime temperature counter, and the
            # snapshot was being built a line later anyway
            metrics = health_mod.health_snapshot(
                self.analyze_session, tu,
                log_peak_c=getattr(self, "_log_peak_c", None))
            a = condition_mod.assess(
                text,
                max_batt_temp_c=parsers.parse_stats(
                    self.analyze_session.cmd("stats")).get("max_batt_temp_c"),
                temp_units=tu)
            self._cond_verdict = condition_mod.verdict(a, metrics)
            self._paint_verdict()

            def _t(c):
                return health_mod.fmt_temp(c, tu) or "n/a"

            def _row(check, finding, tag):
                self.cond_tree.insert("", "end", tags=(tag,),
                                      values=(check, finding))

            # First rows on the tab, above everything: these are the findings
            # the verdict deliberately does not cover, and burying them under
            # the measurements would repeat the failure they exist to fix.
            for _note in (self._cond_verdict or {}).get("beyond_pack") or []:
                _row("Beyond the pack verdict", _note, "attention")

            clk = condition_mod.clock_check(self.analyze_session)
            if clk.get("mbb_clock"):
                bits = ["MBB %s" % clk["mbb_clock"]]
                if clk.get("bms_clock"):
                    bits.append("BMS %s" % clk["bms_clock"])
                if clk.get("dash_clock"):
                    bits.append("dash %s" % clk["dash_clock"])
                _row("Clocks", " · ".join(bits), "measured")
            if clk.get("captured_at"):
                _row("Bike clock vs capture",
                     "%s · captured at %s%s"
                     % (condition_mod.describe_offset(clk["offset_s"]),
                        clk["captured_at"],
                        " · event-log times are shifted to match on the Rides tab"
                        if clk.get("worth_correcting") else ""),
                     "attention" if clk.get("worth_correcting") else "measured")
            cov = a["coverage"]
            if cov["first"]:
                _row("Log window", "%s → %s  ·  %d ride / %d charge samples"
                     % (cov["first"], cov["last"], cov["ride_samples"],
                        cov["charge_samples"]), "measured")
            # An unmeasured stretch is not a clean one. The sentence is
            # composed in condition.py because the saved page prints the same
            # one, and this is the phrase least able to afford a drift.
            _cov_note = condition_mod.coverage_note(cov)
            if _cov_note:
                _row("Cell readings available", _cov_note, "attention")
            cap = a["charge_capacity"]
            if cap:
                _row("Charge capacity",
                     "median %g Ah across %g–%g V · %d sessions · %s"
                     % (cap["median_ah"], cap["window_v"][0], cap["window_v"][1],
                        cap["sessions"], condition_mod.capacity_caveat()),
                     "measured")
            # what it costs to ride, and how far a charge goes. Measured from
            # the samples, NOT from the BMS's nominal capacity - which on this
            # platform is not what the gauge behaves like, and a range built on
            # it would be a third too long.
            recs = parsers.parse_ride_log(text)
            cons = rides.consumption(recs) if recs else None
            rng = rides.range_estimate(recs) if recs else None
            unit = "mi" if config.get_units() == "mi" else "km"
            dfac = 0.621371 if unit == "mi" else 1.0
            if cons:
                if cons.get("wh_per_km_low") is None:
                    # a "middle 80%" of one ride is that ride's number twice
                    band = "from %d ride(s) · too few for a spread" % cons["rides"]
                else:
                    band = ("middle 80%% of rides %g–%g · measured over %d rides"
                            % (round(cons["wh_per_km_low"] / dfac, 1),
                               round(cons["wh_per_km_high"] / dfac, 1),
                               cons["rides"]))
                _row("Consumption",
                     "%g Wh/%s at the pack · %s · at %s to %s ambient"
                     % (round(cons["wh_per_km"] / dfac, 1), unit, band,
                        _t(cons["amb_low_c"]), _t(cons["amb_high_c"])), "measured")
            if rng:
                _row("Range on a full charge",
                     "about %g %s · scaled from the deepest ride logged "
                     "(%g %s, %g%% → %g%% SOC)"
                     % (round(rng["full_charge_km"] * dfac, 1), unit,
                        round(rng["km"] * dfac, 1), unit, rng["from_soc_pct"],
                        rng["to_soc_pct"]),
                     "attention" if rng.get("is_extrapolation") else "measured")
                _rng_note = condition_mod.range_caveat(rng)
                if _rng_note:
                    _row("└ what that rests on", _rng_note, "unknown")
                if rng.get("implied_pack_wh"):
                    _row("└ pack size that implies",
                         "about %d Wh, reached from energy and SOC together — "
                         "worth holding against what the BMS reports"
                         % rng["implied_pack_wh"], "measured")
            floor = a["cell_floor"]
            if condition_mod.show_cell_floor(a):
                _row("Lowest cell, loaded",
                     "%g mV · from %d %s"
                     % (floor["min_cell_mv"], floor["samples"], floor["source"]),
                     "measured")
            sag = a["cell_sag"]
            if sag:
                _row("Weakest cell, loaded",
                     "%g mV at %g A · %g%% SOC · %s"
                     % (sag["min_cell_mv"], sag["at_amps"], sag["at_soc_pct"],
                        _t(sag["at_pack_temp_c"])), "measured")
            der = a["derate"]
            if der:
                _row("Discharge allowance",
                     "median %g%% · worst %g%% at %s / %g%% SOC"
                     % (der["median_pct"], der["worst_pct"],
                        _t(der["worst_at_pack_temp_c"]), der["worst_at_soc_pct"]),
                     "measured")
            ch = a.get("charging") or {}
            if ch.get("sessions"):
                bits = "%d sessions" % ch["sessions"]
                if ch.get("per_week"):
                    bits += " · %g a week" % ch["per_week"]
                if ch.get("start_soc_median") is not None:
                    bits += " · plugged in at a median %g%% SOC" % ch["start_soc_median"]
                _row("Charging", bits, "measured")
            if ch.get("held_full_h"):
                # The largest calendar-ageing term there is, invisible in the
                # charging samples (the bike stops writing them when the charge
                # finishes) and the one thing on this tab an owner can change
                # this afternoon. Flagged for attention, not graded: the level
                # would need a population nobody has.
                share = ("" if ch.get("held_full_share") is None
                         else " · %.0f%% of the logged period"
                         % (ch["held_full_share"] * 100))
                _row("Sat at FULL, plugged in",
                     "%g h%s · %d spells, longest %g h · %s"
                     % (ch["held_full_h"], share, ch["holds"],
                        ch["held_full_max_h"],
                        condition_mod.full_hold_note(ch)), "attention")
            if ch.get("hot_plugins"):
                _row("Plugged in hot",
                     "%d of %d sessions began at %s or above · a pack is hottest "
                     "right after a ride"
                     % (ch["hot_plugins"], ch["sessions"], _t(ch["hot_plugin_c"])),
                     "attention")
            if ch.get("peak_amps_median") is not None:
                _row("Charge current",
                     "median peak %g A · highest %g A"
                     % (ch["peak_amps_median"], ch["peak_amps_max"]), "measured")
            # Its own row now, on the same condition the saved page uses. The
            # sentence used to be baked into the row above unconditionally,
            # which read true only while `taper_resolvable` was a constant.
            _taper = condition_mod.taper_note(ch)
            if _taper:
                _row("Charge taper", _taper, "unknown")
            for f in a["faults"]:
                detail = condition_mod.fault_detail(f)
                _row(f["name"], "%d logged · %s · counted, not graded%s"
                     % (f["count"], condition_mod.fault_span(f),
                        (" · %s, dates are the onsets" % detail) if detail else ""),
                     "measured")
                if f["name"].lower().startswith("module connect"):
                    assoc = condition_mod.module_failure_note(
                        a.get("module_failures"))
                    if assoc:
                        _row("└ what was happening", assoc, "measured")
            for ev in a["stats_resets"]:
                _row("Statistics RESET",
                     "%s — every 'lifetime' figure on the Health tab dates "
                     "from here, not from the bike's build date"
                     % (ev["when"] or "an unlogged time"), "attention")
            for u in a["undetermined"]:
                _row("Not determined", u, "unknown")

        # the verdict colour is set on the widget, not through a ttk style, so
        # like the Treeview tags it does NOT follow a live theme switch on its
        # own and has to be re-applied
        _VERDICT_COLOUR = {"concern": "danger", "watch": "warn",
                           "ok": "green", "unknown": "dim"}

        def _cond_note(self, _evt=None):
            """Show the selected row in full; Treeview clips its cells."""
            sel = self.cond_tree.selection()
            if not sel:
                self.lbl_cond_note.config(text=self._cond_hint)
                return
            vals = self.cond_tree.item(sel[0])["values"]
            self.lbl_cond_note.config(
                text="%s: %s" % (vals[0], vals[1]) if len(vals) > 1
                else self._cond_hint)

        def _paint_verdict(self):
            v = getattr(self, "_cond_verdict", None)
            if not getattr(self, "lbl_cond_verdict", None):
                return
            if not v:
                self.lbl_cond_verdict.config(text="Load a session to assess the "
                                             "pack.", foreground=P["dim"])
                return
            self.lbl_cond_verdict.config(
                # A colon, not a dash: the headline now carries its own
                # em dash for the driving check, and three dashes in one line
                # is the sentence this project argues nobody parses.
                text="%s: %s" % (v["level"].upper(), v["headline"]),
                foreground=P[self._VERDICT_COLOUR.get(v["level"], "dim")])

        def _render_health(self):
            self.health_tree.delete(*self.health_tree.get_children())
            self._health_notes = {}
            if not self.analyze_session:
                return
            help_map = self._analyze_help_map()
            for i, m in enumerate(health_mod.health_snapshot(
                    self.analyze_session, config.get_temp_units(),
                    log_peak_c=getattr(self, "_log_peak_c", None))):
                iid = str(i)
                self.health_tree.insert("", "end", iid=iid, tags=(m["status"],),
                                        values=(m["label"], m["display"],
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
            # The ride telemetry IS available straight off the bike over serial: the
            # console's `eventlogdump` prints the full event log as decoded text,
            # including per-sample "Riding" entries (SOC / Vpack / pack+motor temp /
            # MotRPM / Odo). We parse that directly — no Zero app, no .bin files, and
            # no external decoder needed. (Legacy `dumplogs` captures + externally
            # loaded .txt exports still render, for back-compat.)
            self.ride_tree.delete(*self.ride_tree.get_children())
            s = self.analyze_session
            text, source = "", ""
            if s:
                text = s.cmd("eventlogdump") or ""
                if text.strip():
                    source = "session event log"
                else:
                    text = s.cmd("dumplogs") or ""
                    if parsers.is_console_refusal(text):
                        text = ""      # the console declining, not a legacy log
                    source = "session dumplogs (legacy)" if text.strip() else ""
            recs = parsers.parse_ride_log(text)
            if recs:
                # a genuinely truncated capture (not the "captured N of M" note) may
                # be missing entries — say so rather than imply the totals are whole.
                warn = ("   ⚠ the event-log capture ended early — some entries may be "
                        "missing; re-pull for the full log." if "### TRUNCATED" in text
                        else "")
                self._render_ride_records(recs, source, warn=warn)
                return
            self._ride_records = []
            self._render_charts()
            self.lbl_ride_totals.config(
                text="No ride telemetry in this session yet. Connect to the bike and "
                     "click 'Pull ride log from bike →' (runs the console's "
                     "eventlogdump — the full event log; takes a few minutes and may "
                     "briefly click the drivetrain contactor open, so the bike must be "
                     "safely parked). No Zero app or external decoder needed. You can "
                     "also 'Load ride log (.txt)…' to open a log file you already have.")

        def _render_ride_records(self, recs, source, warn=""):
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
            tu = config.get_temp_units()
            self.ride_tree.heading("km", text="Distance %s" % unit)
            self.ride_tree.heading("socpkm", text="SOC%%/%s" % unit)
            self.ride_tree.heading("ptemp", text="Max pack %s" % tu)
            self.ride_tree.heading("mtemp", text="Max motor %s" % tu)

            def _d(km):
                return round(km * f, 1) if isinstance(km, (int, float)) else km

            def _e(per_km):        # SOC% per km -> per the displayed distance unit
                return round(per_km / f, 2) if isinstance(per_km, (int, float)) else None

            def _t(c):             # a Celsius datum as a bare number in the user's scale
                if not isinstance(c, (int, float)):
                    return None
                return round(c * 9 / 5 + 32) if tu == "F" else c

            def _na(v):
                return "n/a" if v is None else v

            # The event log is timestamped by the bike's own MBB clock, which can
            # be hours out — one reported bike reads 7 h behind its owner's local
            # time. The capture records the machine's clock beside the bike's, so
            # the correction is measured rather than configured.
            clk = (condition_mod.clock_check(self.analyze_session)
                   if self.analyze_session else {})
            shift = clk.get("offset_s") if clk.get("worth_correcting") else None
            tnote = ("" if shift is None else
                     "  ·  times shifted %s to this capture's clock"
                     % condition_mod.describe_offset(-shift).replace(
                         "behind", "forward").replace("ahead", "back"))

            self.lbl_ride_totals.config(
                text="%s: %d rides · %.1f %s · mean %s SOC%%/%s · max pack %s / "
                "motor %s · %d samples%s"
                % (source, t["ride_count"], _d(t["total_km"]), unit,
                   _na(_e(t["mean_soc_per_km"])), unit,
                   _na(health_mod.fmt_temp(t["max_pack_temp_c"], tu)),
                   _na(health_mod.fmt_temp(t["max_motor_temp_c"], tu)),
                   t["samples"], warn + tnote))
            for r in summ["rides"]:
                self.ride_tree.insert("", "end", values=(
                    condition_mod.shift_timestamp(r["start_ts"], shift)
                    if r["start_ts"] else "?",
                    _d(r["distance_km"]), r["soc_used_pct"],
                    _na(_e(r["soc_per_km"])), _na(_t(r["max_pack_temp_c"])),
                    _na(_t(r["max_motor_temp_c"])), _na(r["max_rpm"])))

        # -- Analyze: Charts (dependency-free tk.Canvas plots) ---------------
        # ride-log time series + cross-session trends (one point per saved pull).
        _CHART_METRICS = ("SOC vs distance", "Pack voltage vs distance",
                          "Temperatures vs distance", "Motor current vs distance",
                          "SOC vs pack voltage", "Efficiency per ride (bar)",
                          "Trend: pack capacity", "Trend: charge cycles",
                          "Trend: max battery temp", "Trend: max motor temp",
                          "Trend: effective gearing", "Trend: charge index",
                          "Trend: weakest cell deviation")

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
            "Trend: effective gearing": (_trend_gearing_ratio,
                                         "effective ratio (:1)", False),
        }

        # The two metrics worth plotting over years, and the only two here that
        # survive a firmware change. "Trend: pack capacity" above is what the BMS
        # SAYS; the charge index is what the pack ACTUALLY accepted between two
        # fixed voltages, and the deviation is the weakest cell measured against
        # its own pack average — neither can be moved by an SOC rescale. Both come
        # out of the event log rather than `bms`, which is why they are cached
        # separately from the cheap ones.
        # label -> (key in the log-trend record, y-label)
        _LOG_TRENDS = {
            "Trend: charge index": ("charge_index_ah",
                                    "Ah accepted 103–113 V"),
            "Trend: weakest cell deviation": ("cell_deviation_mv",
                                              "weakest cell below pack avg (mV)"),
        }
        # a pull's event log is about a megabyte and costs ~0.1 s to parse, so the
        # history is read once, on demand, and only this far back
        _LOG_TREND_LIMIT = 24

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
                                lambda e: self._chart_reselect())
            # date-range window for the 'Trend:' charts (across saved pulls); default
            # 'All' = the lifetime of every real pull in your sessions folder. This is
            # a coarse window; drag on the plot to zoom into any x-range (see below).
            ttk.Label(row, text="Range:").pack(side="left", padx=(12, 0))
            self.chart_range = tk.StringVar(value="All")
            self.cbo_range = ttk.Combobox(row, textvariable=self.chart_range,
                                          state="readonly", width=12,
                                          values=list(self._CHART_RANGES))
            self.cbo_range.pack(side="left", padx=6)
            self.cbo_range.bind("<<ComboboxSelected>>",
                                lambda e: self._chart_reselect())
            ttk.Label(row, text="drag to zoom · double-click to reset",
                      style="Muted.TLabel").pack(side="left", padx=8)
            self.chart_canvas = tk.Canvas(cf, highlightthickness=0,
                                          bg=P["console"], height=380)
            self.chart_canvas.pack(fill="both", expand=True, pady=(8, 0))
            # redraw responsively; debounce so a resize drag isn't a redraw storm
            self.chart_canvas.bind("<Configure>", self._chart_on_resize)
            # drag-to-zoom the x-range (rubber band); double-click resets. _chart_line
            # stashes the last render's x-transform so we can invert pixel -> data.
            self._chart_xzoom = None       # (data_lo, data_hi) applied to every series
            self._chart_xform = None       # (xlo, xhi, x0px, x1px) from last render
            self._chart_drag = None        # press x (px) while a rubber band is active
            self._chart_rubber = None      # the rubber-band canvas rect id
            self.chart_canvas.bind("<ButtonPress-1>", self._chart_press)
            self.chart_canvas.bind("<B1-Motion>", self._chart_drag_motion)
            self.chart_canvas.bind("<ButtonRelease-1>", self._chart_release)
            self.chart_canvas.bind("<Double-1>", self._chart_zoom_reset)

        def _chart_reselect(self):
            # switching metric or range starts fresh — drop any active drag-zoom
            self._chart_xzoom = None
            self._render_charts()

        def _chart_press(self, e):
            self._chart_drag = e.x
            if self._chart_rubber is not None:
                self.chart_canvas.delete(self._chart_rubber)
                self._chart_rubber = None

        def _chart_drag_motion(self, e):
            if self._chart_drag is None:
                return
            cv = self.chart_canvas
            if self._chart_rubber is not None:
                cv.delete(self._chart_rubber)
            h = self._chart_size(cv)[1]
            self._chart_rubber = cv.create_rectangle(
                self._chart_drag, 10, e.x, h - 30, outline="#5aa8ff", dash=(3, 2))

        def _chart_release(self, e):
            start, self._chart_drag = self._chart_drag, None
            if self._chart_rubber is not None:
                self.chart_canvas.delete(self._chart_rubber)
                self._chart_rubber = None
            if start is None or abs(e.x - start) < 8:
                return                       # a click / tiny drag, not a zoom
            xform = self._chart_xform
            if not xform:
                return
            xlo, xhi, x0, x1 = xform
            if x1 <= x0 or xhi <= xlo:
                return

            def to_data(px):
                px = min(max(px, x0), x1)
                return xlo + (px - x0) / (x1 - x0) * (xhi - xlo)
            a, b = sorted((to_data(start), to_data(e.x)))
            if b > a:
                self._chart_xzoom = (a, b)
                self._render_charts()

        def _chart_zoom_reset(self, _e=None):
            if self._chart_xzoom is not None:
                self._chart_xzoom = None
                self._render_charts()

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
            """REAL-hardware saved sessions (oldest->newest) parsed to
            (mtime, name, bms, stats), cached so a chart resize doesn't re-read the
            folders each frame. Invalidated when a new pull is saved (see _baseline).

            Simulator (`_sim`) and cable-test (`_listen`) folders are EXCLUDED — the
            trend line is your bike's real history, and a --sim rehearsal saves fake
            bms/stats that would otherwise poison it (owner)."""
            cached = getattr(self, "_trend_cache", None)
            if cached is not None:
                return cached
            out = []
            try:
                root, names = self._recent_sessions(limit=60)
                for name in reversed(names):     # oldest first
                    if name.endswith(("_sim", "_listen")):
                        continue                 # not real-hardware data
                    try:
                        folder = os.path.join(root, name)
                        s = sessions.load_session(folder)
                        out.append((library_mod._captured_at(folder, s), name,
                                    parsers.parse_bms(s.cmd("bms")),
                                    parsers.parse_stats(s.cmd("stats"))))
                    except Exception:
                        pass
            except Exception:
                out = []
            out.sort(key=lambda r: r[0])         # oldest -> newest by capture time
            self._trend_cache = out
            return out

        # date-range windows for the Trend charts (relative to the newest pull, so a
        # short window never renders empty just because you haven't pulled today)
        _CHART_RANGES = {"All": None, "Last 12 mo": 365, "Last 6 mo": 182,
                         "Last 3 mo": 91, "Last 30 days": 30}

        def _apply_chart_range(self, pts):
            """Filter (time, value) points to the selected date window (newest-point
            relative). 'All' keeps everything."""
            rng = self.chart_range.get() if hasattr(self, "chart_range") else "All"
            days = self._CHART_RANGES.get(rng)
            if not days or not pts:
                return pts
            ref = max(t for t, _ in pts)
            cutoff = ref - days * 86400
            return [(t, v) for t, v in pts if t >= cutoff]

        def _sim_trend_points(self, metric, is_temp, tu):
            """Simulator only: synthesize a year-long trend (≈26 pulls) so the chart
            demonstrates what a real history looks like. Anchored on the sim's own
            current reading where available; clearly labelled SIMULATED on the chart."""
            import math
            extract = self._SESSION_TRENDS.get(metric, (None,))[0]
            anchor = None
            trend = self._load_trend_sessions() if extract else []
            if trend:
                v = extract(trend[-1][2], trend[-1][3])
                if isinstance(v, (int, float)):
                    anchor = float(v) * (9 / 5) + 32 if (is_temp and tu == "F") else float(v)
            if anchor is None:
                anchor = {"Trend: pack capacity": 100.0, "Trend: charge cycles": 60.0,
                          "Trend: max battery temp": 34.0 if tu != "F" else 93.0,
                          "Trend: max motor temp": 55.0 if tu != "F" else 131.0,
                          "Trend: effective gearing": 4.0,
                          "Trend: charge index": 17.9,
                          "Trend: weakest cell deviation": 65.0,
                          }.get(metric, 50.0)
            now = _dt.datetime.now().timestamp()
            span, n = 365 * 86400, 26
            pts = []
            for i in range(n):
                t = now - span + span * i / (n - 1)
                back = n - 1 - i                       # steps back from the newest
                if metric == "Trend: charge cycles":
                    val = max(0.0, anchor - back * 4)   # cycles grow toward newest
                elif metric == "Trend: pack capacity":
                    val = anchor + back * 0.15          # gentle decline toward newest
                elif metric == "Trend: effective gearing":
                    val = anchor + math.sin(i * 1.1) * 0.03   # ~flat (steps at a re-gear)
                elif is_temp:
                    val = anchor + math.sin(i * 1.1) * (5 if tu == "F" else 3)
                else:
                    val = anchor + math.sin(i * 1.1) * 3
                pts.append((t, round(val, 2)))
            return pts

        def _load_log_trend(self):
            """Event-log figures per saved pull (oldest->newest), built on demand.

            Deliberately separate from `_load_trend_sessions`: that one parses two
            short command outputs and is cheap enough to build whenever a chart is
            drawn, while this one re-reads the event log of every capture. Nothing
            asks for it until one of the `_LOG_TRENDS` metrics is selected.
            """
            cached = getattr(self, "_log_trend_cache", None)
            if cached is not None:
                return cached
            out = []
            try:
                root, names = self._recent_sessions(limit=60)
                real = [n for n in reversed(names)
                        if not n.endswith(("_sim", "_listen"))]
                for name in real[-self._LOG_TREND_LIMIT:]:
                    try:
                        folder = os.path.join(root, name)
                        sess = sessions.load_session(folder)
                        log = compare_mod._event_log(sess)
                        if not log:
                            continue
                        cap = condition_mod.charge_capacity(parsers.parse_charge_log(log))
                        dev = condition_mod.cell_deviation(parsers.parse_ride_log(log))
                        out.append((library_mod._captured_at(folder, sess), name, {
                            "charge_index_ah": cap["median_ah"] if cap else None,
                            "cell_deviation_mv": dev["median_mv"] if dev else None,
                        }))
                    except Exception:
                        pass
            except Exception:
                out = []
            out.sort(key=lambda r: r[0])
            self._log_trend_cache = out
            return out

        def _chart_note(self, cv, text):
            w, _h = self._chart_size(cv)
            cv.create_text(w - 22, 22, text=text, fill=P["dim"], anchor="ne",
                           font=(self.sty["ui"], 8))

        def _render_session_trend(self, cv, metric):
            log_metric = metric in self._LOG_TRENDS
            if log_metric:
                key, ylabel = self._LOG_TRENDS[metric]
                is_temp = False

                def extract(rec, _unused):
                    return rec.get(key)
                rows = [(mt, name, rec, None)
                        for mt, name, rec in self._load_log_trend()]
            else:
                extract, ylabel, is_temp = self._SESSION_TRENDS[metric]
                rows = self._load_trend_sessions()
            tu = config.get_temp_units()
            pts = []
            for mt, _name, bms, stats in rows:
                v = extract(bms, stats)
                if isinstance(v, (int, float)):
                    if is_temp and tu == "F":
                        v = round(v * 9 / 5 + 32)
                    pts.append((mt, v))          # x = capture time (a real timeline)
            # simulator: too few REAL pulls to show a trend -> synthesize a year so the
            # user can see the shape (labelled SIMULATED, never mixed with real data).
            synthetic = False
            if len(pts) < 2 and self.sim_var.get():
                pts = self._sim_trend_points(metric, is_temp, tu)
                synthetic = True
            pts = self._apply_chart_range(pts)
            if not pts:
                self._chart_msg(cv, "No saved sessions with this metric in this range."
                                + ("\n\nThis one is read out of the event log, so "
                                   "it only appears for pulls taken with "
                                   "'+event log' ticked." if log_metric else "")
                                + "\n\nPull full database on several visits to build "
                                "a trend over time, or widen the Range.")
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
            note = "%d %spull%s · %s" % (
                len(pts), "" if synthetic else "real ",
                "" if len(pts) == 1 else "s", self.chart_range.get())
            if log_metric:
                note += " · an index, not capacity"
            self._chart_note(cv, ("SIMULATED · " + note) if synthetic else note)

        def _render_charts(self):
            cv = getattr(self, "chart_canvas", None)
            if cv is None:
                return
            cv.delete("all")
            # invalidate the drag-zoom transform on EVERY render — only a successful
            # _chart_line draw re-stashes it. This means an empty/no-data render (incl.
            # zooming into an empty band, or switching metric/range) leaves _chart_xform
            # None, so a follow-up drag is a safe no-op instead of inverting pixels with
            # a stale transform from the previous chart. (review v0.18: critical + major)
            self._chart_xform = None
            metric = self.chart_metric.get()
            if metric in self._SESSION_TRENDS or metric in self._LOG_TRENDS:
                # cross-session trend (one point per saved pull, not a ride log)
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
            # drag-to-zoom: keep only points inside the selected x-window, then re-fit
            # Y to what's visible. Filter BEFORE downsample so we don't thin first.
            zoom = getattr(self, "_chart_xzoom", None)
            if zoom:
                lo, hi = zoom
                series = [(lbl, col, [(x, y) for x, y in pts if lo <= x <= hi])
                          for lbl, col, pts in series]
            series = [(lbl, col, charts_mod.downsample(pts))
                      for lbl, col, pts in series if pts]
            allpts = [p for _, _, pts in series for p in pts]
            if not allpts:
                self._chart_msg(cv, "No data in the zoomed range." if zoom
                                else "Not enough data to plot this metric.")
                if zoom:
                    self._chart_note(cv, "double-click to reset zoom")
                return
            w, h = self._chart_size(cv)
            x0, y0, x1, y1 = 60, 16, w - 18, h - 42
            if x1 - x0 < 60 or y1 - y0 < 60:
                return
            xs = [p[0] for p in allpts]
            ys = [p[1] for p in allpts]
            xlo, xhi, xstep = charts_mod.nice_bounds(min(xs), max(xs))
            ylo, yhi, ystep = charts_mod.nice_bounds(min(ys), max(ys))
            # remember this render's x-transform so a drag can invert pixel -> data
            self._chart_xform = (xlo, xhi, x0, x1)

            def sx(x):
                return x0 + (x - xlo) / (xhi - xlo) * (x1 - x0)

            def sy(y):
                return y1 - (y - ylo) / (yhi - ylo) * (y1 - y0)

            grid, axcol, fg = P["grid"], P["dim"], P["fg"]
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
            if zoom:
                cv.create_text(x1, h - 8, text="zoomed · double-click to reset",
                               fill=P["dim"], anchor="se", font=(self.sty["ui"], 8))

        def _chart_bar(self, cv, labels, values, ylabel):
            w, h = self._chart_size(cv)
            x0, y0, x1, y1 = 60, 16, w - 18, h - 54
            if x1 - x0 < 60 or y1 - y0 < 60 or not values:
                return
            ylo, yhi, ystep = charts_mod.nice_bounds(min(0, min(values)), max(values))

            def sy(y):
                return y1 - (y - ylo) / (yhi - ylo) * (y1 - y0)

            grid, axcol, fg = P["grid"], P["dim"], P["fg"]
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

        def _pull_ride_log(self):
            """Pull the ride telemetry straight off the bike: run the console's
            eventlogdump (decoded text, incl. per-sample Riding entries), then reload
            the session and re-render Rides/Charts. No Zero app / .bin / decoder."""
            if not self.connected:
                messagebox.showinfo(APP_NAME, "Connect to the bike first (Connect "
                    "tab), then Pull ride log. It runs the console's eventlogdump — "
                    "the full event log — which is where the per-ride telemetry lives. "
                    "No bike? Use 'Load ride log (.txt)…' to open a log you already "
                    "have, or turn on Simulator mode to see the layout.")
                return
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current read to "
                                    "finish, then Pull ride log.")
                return
            # routes through the heavy-read contactor warning + long idle timeout,
            # then re-renders this tab from the freshly-saved event log.
            self._read_heavy("eventlogdump", then=self._rides_after_pull)

        def _rides_after_pull(self):
            if not self.logger:
                return
            try:
                self._analyze_set(sessions.load_session(self.logger.dir))
            except Exception as e:
                messagebox.showerror(APP_NAME, "Pulled the log but couldn't reload "
                                     "the session:\n%s" % e)

        def _load_ride_log(self):
            path = filedialog.askopenfilename(
                title="Load a decoded ride log (.txt)",
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
            # the capture's own date, not the folder's mtime - see _recent_sessions
            ordered = sorted(self.compare_list,
                             key=lambda s: library_mod._captured_at(s.dir, s))
            self._compare_out("Comparing %d session(s), oldest -> newest:"
                              % len(ordered))
            for s in ordered:
                self._compare_out("  - %s" % s.name)
            if len(ordered) < 2:
                self._compare_out("\nAdd a second session to see the diff and trends.")
                return
            # Compare answers two questions a single capture cannot. What CHANGED
            # between two pulls (the settings diff), and how the PACK moved across
            # all of them. The dated timelines — capacity, charge cycles, temps,
            # gearing — stay on the Charts tab, which draws them properly.
            res = compare_mod.compare_sessions(ordered)
            self._compare_out("\nSETTINGS CHANGED (%s -> %s):"
                              % (ordered[0].name, ordered[-1].name))
            if res["settings_diff"]:
                for name, old, new in res["settings_diff"]:
                    self._compare_out("  %-16s %s -> %s" % (name, old, new))
            else:
                self._compare_out("  (none)")
            # What a single capture cannot show. These are the measurements that
            # survive a firmware change - the charge index is read between fixed
            # pack voltages, and the deviation is the weakest cell against its own
            # pack - so unlike the SOC gauge they stay comparable across time.
            def g(v):
                return "n/a" if v is None else ("%g" % v if isinstance(v, float)
                                                else str(v))

            trend = res.get("pack_trend") or []
            if trend:
                self._compare_out("\nPACK OVER TIME (nothing here is graded):")
                self._compare_out("  %-30s %9s %8s  %s"
                                  % ("capture", "index Ah", "dev mV", "weakest cell"))
                for r in trend:
                    cell = ("%s mV @ cell %s" % (g(r["low_cell_mv"]),
                                                 g(r["low_cell_index"]))
                            if r.get("low_cell_index") is not None
                            else "%s mV" % g(r["low_cell_mv"]))
                    self._compare_out("  %-30s %9s %8s  %s (at %s%% SOC)"
                                      % (r["session"][:30], g(r["charge_index_ah"]),
                                         g(r["cell_deviation_mv"]), cell,
                                         g(r["soc_pct"])))
                self._compare_out("  index Ah = charge accepted 103-113 V, a comparable "
                                  "index rather than the pack's capacity")
                self._compare_out("  dev mV   = how far the weakest cell sits below its "
                                  "own pack average under load")
            wc = res.get("weakest_cell")
            if wc:
                self._compare_out(
                    "\n  %s Cell %d is the weakest reading in %d of %d captures."
                    % ("!" if wc["always"] else "-", wc["cell"], wc["times"],
                       wc["of_captures"]))
                self._compare_out(
                    "    A voltage that wanders between cells is a pack breathing; the "
                    "same cell returning is a cell.")
                self._compare_out(
                    "    Read at rest, so discount any reading taken near full charge - "
                    "the spread closes up there.")
            self._compare_out("\nDated timelines for pack capacity, charge cycles, temps "
                              "and effective gearing live on the Charts tab — pick a "
                              "'Trend:' metric there.")

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
