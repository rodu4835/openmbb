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

# The always-present dropdown choice that runs the built-in simulator (no bike /
# cable needed). Selecting it makes _make_port hand back a SimPort.
SIM_CHOICE = "SIMULATOR (no bike)"

# Connect-tab button labels — the two-step flow reads verify-first so nobody has
# to infer that the (receive-only) link check comes before the live connect.
VERIFY_LABEL = "1 · Verify link"
CONNECT_LABEL = "2 · Connect & Probe"

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

The app is phase-gated — each phase unlocks the next. You can stop after any
phase; closing the window never loses data (everything is saved as you go).

PHASE 0 — CONNECT
  Pick your COM port from the dropdown — or pick "SIMULATOR (no bike)" to explore
  the whole tool with no bike or cable attached. Two steps, in order: click
  "1 · Verify link" first — it only LISTENS (transmits nothing), so it safely
  proves your cable + baud and that the bike is talking (power the bike during
  the ~45 s window to catch the boot banner). Then "2 · Connect & Probe" wakes
  the console prompt and reads the firmware version. Garbage output at 38400 baud
  usually means the Tx/Rx wires are swapped — stop and recheck.

PHASE 1 — READ
  Click any command button for a one-off read. To advance, click the blue
  ★ FULL BASELINE button: it runs the quick reads + the full settings dump
  (your backup) + the small error log — NO heavy dumps. Individual reads do NOT
  unlock Login — only FULL BASELINE does, so a backup exists before any change.
  The heavy log reads (eventlogdump / dumpall) sit behind their OWN buttons and
  confirm first: on a keyed-on bike a long ~1 MB dump can make the BMS briefly
  OPEN the drivetrain contactor (a click + flashing dash; it recovers when the
  read finishes). They are NOT part of the routine baseline.

PHASE 2 — LOGIN
  Explicit. "Try known passwords" attempts the community-known ones in order;
  or type a specific password and "Try this password" (it is masked in the logs
  and never saved to disk). Both failing is fine — the tool stays read-only.
  Success unlocks Writes. A password is masked in the logs and never written to
  disk unless YOU say yes when it offers to remember it after a successful login
  (clear saved ones via Session → Forget saved login passwords) — nothing to
  hand-edit.

PHASE 3 — WRITES
  Triple-gated: logged in + the master UNLOCK WRITES switch + a per-write
  confirm dialog. Only whitelisted settings that actually exist on your bike
  appear. Each write re-reads the current value, backs up all settings, sends
  the change, reads it back to verify, and journals it (with a Revert button).

ANALYZE (always available, no bike needed)
  Reads a saved session folder (or the current one) and interprets it:
    - Health : SOC vs voltage, cell balance, capacity, temps, cycles, and the
               effective gearing ratio, each flagged ok / watch / alert.
    - Rides  : per-ride distance, SOC%/km, and temps from a ride log you load
               (.txt) — rev 41 doesn't stream ride telemetry as console text, so
               use a decoded zero-log-parser export.
    - Compare: pick 2+ sessions to see settings changes and capacity / gearing
               trends over time (battery degradation tracking).
    - Gearing: enter new front/rear teeth to get the ratio and the exact
               spfront/sprear/rwhcirc values to write.

TIP: Session menu -> "Open session folder" jumps to where everything is saved.
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

Read-first, whitelist-only writes. The transport layer refuses dangerous
commands from EVERY path (buttons and the raw box), including:
  format/erase eeprom (bare eeprom is an allowed read; eeprom with arguments
  is refused), settingsrst, statsrst, log clears/adds, reset, exit_to_bl,
  dtc_clear, force_all_storage_mode, blcmds, burn, test, wdt, timing, can,
  charger, sevcon preop, and any "set" of a protected value (abs_disable,
  bypass_bms, ov_* overrides, motstage*/ctrlstage* thermal limits,
  sevnoregspeed/sevmaxregv/sevnoregfull regen guards, model/vin/serial identity).

Those regen and thermal guards are shown READ-ONLY in the Writes tab so you can
see them without being able to change them.

Writable settings are limited to speedo/gearing, custom-mode speed/torque/
regen, and a few gauge/charge options — each with an effect + risk note and
value limits. Coast regen of exactly 0 is refused (fishtail risk).
"""


def build_gui(sim=False, preselect_port=None, log_dir=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from . import config

    P = PALETTE

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("%s v%s  —  Zero MBB console (Gen2)" % (APP_NAME, __version__))
            self.geometry("1080x760")
            self.minsize(900, 620)
            self.sty = apply_theme(self)
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
            self._build_menubar()
            self._build_statusbar()
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
            self._apply_gates()
            self._refresh_save_label()

        # -- helpers ---------------------------------------------------------
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
            bar = ttk.Frame(self, padding=(8, 6))
            bar.pack(fill="x")
            self.lbl_conn = ttk.Label(bar, text="● DISCONNECTED",
                                      foreground=P["danger"],
                                      font=(self.sty["ui"], 10, "bold"))
            self.lbl_conn.pack(side="left")
            self.lbl_ver = ttk.Label(bar, text="", foreground=P["green"])
            self.lbl_ver.pack(side="left", padx=16)
            self.lbl_login = ttk.Label(bar, text="not logged in",
                                       foreground=P["dim"])
            self.lbl_login.pack(side="left", padx=16)
            self.lbl_sess = ttk.Label(bar, text="session: (none yet)",
                                      foreground=P["dim"], cursor="hand2")
            self.lbl_sess.pack(side="right")
            self.lbl_sess.bind("<Button-1>", lambda e: self._open_session_folder())
            ttk.Label(bar, text="v%s" % __version__,
                      foreground=P["dim"]).pack(side="right", padx=16)

        # -- menu bar --------------------------------------------------------
        def _dark_menu(self, parent):
            """A dropdown tk.Menu coloured to match the dark theme (dropdowns DO
            honour bg/fg on Windows, unlike the native menubar strip)."""
            return tk.Menu(parent, tearoff=0, bg=P["panel"], fg=P["fg"],
                           activebackground=P["sel"], activeforeground="#eafff2",
                           disabledforeground=P["dim"], relief="flat", borderwidth=0)

        def _build_menubar(self):
            # A2: the native Tk menubar strip is OS-drawn white on Windows and
            # ignores colours. Use a themed ttk Menubutton bar (dark) with dark
            # dropdown menus instead.
            bar = ttk.Frame(self)
            bar.pack(side="top", fill="x")

            def add_menu(text, build):
                mb = ttk.Menubutton(bar, text=text, direction="below")
                menu = self._dark_menu(mb)
                build(menu)
                mb["menu"] = menu
                mb.pack(side="left", padx=(4, 0), pady=1)

            def build_session(sess):
                sess.add_command(label="Set save location…",
                                 command=self._set_log_dir)
                sess.add_command(label="Open session folder",
                                 command=self._open_session_folder)
                sess.add_command(label="Open recent session…",          # E3
                                 command=self._open_recent_session)
                sess.add_command(label="Copy session path",
                                 command=self._copy_session_path)
                sess.add_separator()
                sess.add_command(label="Save health report…",           # E2
                                 command=self._save_health_report)
                units = self._dark_menu(sess)                           # E6
                self.units_var = tk.StringVar(value=config.get_units())
                units.add_radiobutton(label="Kilometers (km)", value="km",
                                      variable=self.units_var,
                                      command=self._apply_units)
                units.add_radiobutton(label="Miles (mi)", value="mi",
                                      variable=self.units_var,
                                      command=self._apply_units)
                sess.add_cascade(label="Distance units", menu=units)
                sess.add_command(label="Forget saved login passwords",  # E5
                                 command=self._forget_passwords)
                sess.add_separator()
                sess.add_command(label="Refresh COM ports",
                                 command=self._refresh_ports)
                sess.add_separator()
                sess.add_command(label="Exit", command=self._on_close)

            def build_bike(bike):
                bike.add_command(label="Bike info…", command=self._show_bike_info)
                bike.add_command(label="Write options (read-only)…",
                                 command=self._show_write_options)

            def build_help(hlp):
                hlp.add_command(label="Instructions   (F1)",
                                command=self._show_instructions)
                hlp.add_command(label="Wiring diagram", command=self._show_wiring)
                hlp.add_command(label="Safety notes", command=self._show_safety)
                hlp.add_separator()
                hlp.add_command(label="About", command=self._show_about)

            add_menu("Session", build_session)
            add_menu("Bike", build_bike)
            add_menu("Help", build_help)
            self.bind("<F1>", lambda e: self._show_instructions())

        def _info_window(self, title, text):
            win = tk.Toplevel(self)
            win.title("%s — %s" % (APP_NAME, title))
            win.geometry("760x560")
            win.configure(bg=P["console"])
            frame = ttk.Frame(win)
            frame.pack(fill="both", expand=True)
            sb = ttk.Scrollbar(frame)
            sb.pack(side="right", fill="y")
            txt = tk.Text(frame, wrap="word", font=(self.sty["mono"], 10),
                          bg=P["console"], fg=P["termfg"], relief="flat",
                          padx=16, pady=14, insertbackground=P["fg"],
                          yscrollcommand=sb.set)
            txt.pack(side="left", fill="both", expand=True)
            sb.config(command=txt.yview)
            txt.insert("1.0", text)
            txt.config(state="disabled")
            self._attach_copy(txt)       # E4: copyable info dialogs (write options…)
            win.transient(self)
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
            if self.logger:
                self.lbl_sess.config(text="session: %s" % self.logger.dir)
            else:
                self.lbl_sess.config(text="save to: %s" % self._session_root())

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
            root, recent = self._recent_sessions()
            if not recent:
                messagebox.showinfo(APP_NAME, "No saved sessions yet in:\n%s" % root)
                return
            win = tk.Toplevel(self)
            win.title("%s — Recent sessions" % APP_NAME)
            win.geometry("620x380")
            win.transient(self)
            ttk.Label(win, text="Most recent sessions in %s:" % root,
                      wraplength=590, justify="left").pack(anchor="w", padx=10,
                                                           pady=(10, 4))
            lb = tk.Listbox(win, font=(self.sty["mono"], 9))
            for d in recent:
                lb.insert("end", d)
            lb.pack(fill="both", expand=True, padx=10, pady=4)
            lb.selection_set(0)

            def open_sel(_e=None):
                sel = lb.curselection()
                if not sel:
                    return
                folder = os.path.join(root, recent[sel[0]])
                win.destroy()
                try:
                    self._analyze_set(sessions.load_session(folder))
                    self.nb.select(self.nb.index("end") - 1)   # the Analyze tab
                except Exception as e:
                    messagebox.showerror(APP_NAME, "Couldn't load session:\n%s" % e)

            lb.bind("<Double-Button-1>", open_sel)
            ttk.Button(win, text="Open in Analyze", style=self.sty["accent"],
                       command=open_sel).pack(pady=(0, 10))
            win.focus_set()

        # -- E5: forget saved passwords --------------------------------------
        def _forget_passwords(self):
            n = len(config.get_saved_passwords())
            if not n:
                messagebox.showinfo(APP_NAME, "No saved login passwords to forget.")
                return
            if messagebox.askyesno(APP_NAME, "Forget %d saved login password(s)?" % n):
                config.clear_saved_passwords()
                messagebox.showinfo(APP_NAME, "Saved login passwords cleared.")

        # -- E6: distance units ----------------------------------------------
        def _apply_units(self):
            config.set_units(self.units_var.get())
            if self.analyze_session:      # re-render distances in the new unit
                self._render_rides()

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
            for m in health_mod.health_snapshot(s):
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
                messagebox.showinfo(APP_NAME, "No session data to report yet — run a "
                    "FULL BASELINE (or load a session on the Analyze tab) first.")
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

        def _show_bike_info(self):
            lines = ["BIKE INFO", ""]
            facts = self._bike_facts()
            if facts:
                for k, v in facts:
                    lines.append("  %-18s : %s" % (k, v))
            elif not self.connected:
                lines.append("Not connected. Connect on the Connect tab, then run")
                lines.append("FULL BASELINE for model / serial / gearing details.")
            else:
                lines.append("Connected, but no baseline yet — click FULL BASELINE")
                lines.append("on the Read tab to populate model / gearing / serials.")
            lines += ["", "Login level :", "  %s" % ("logged in"
                      if self.logged_in else "not logged in")]
            lines += ["", "Session folder:",
                      "  %s" % (self.logger.dir if self.logger else "(none yet)")]
            self._info_window("Bike info", "\n".join(lines))

        def _show_instructions(self, _evt=None):
            self._info_window("Instructions", INSTRUCTIONS_TEXT)

        def _show_wiring(self):
            self._info_window("Wiring", WIRING_TEXT)

        def _show_safety(self):
            self._info_window("Safety notes", SAFETY_TEXT)

        def _show_about(self):
            import sys
            text = (
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
            self._info_window("About", text)

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
            self.lbl_conn.config(
                text="● CONNECTED (%s)" % ("SIMULATOR" if sim else self.port_var.get())
                if self.connected else "● DISCONNECTED",
                foreground=P["green"] if self.connected else P["danger"])
            self.lbl_login.config(
                text="logged in (level shown in Login tab)" if self.logged_in
                else "not logged in",
                foreground=P["green"] if self.logged_in else P["dim"])

        def _tab_unlock_hint(self, idx):
            # C7: plain-language "here's how to unlock this phase" for a locked tab.
            if idx in (1, 2):     # Read + Login both just need a connection
                return ("The %s tab opens once you Connect & Probe on the Connect tab."
                        % ("Read" if idx == 1 else "Login"))
            if idx == 3:
                if not self.connected:
                    return ("The Writes tab opens once you're connected, logged in, and "
                            "have run ★ FULL BASELINE (a backup must exist before any "
                            "write).")
                need = []
                if not self.logged_in:
                    need.append("log in on the Login tab")
                if not self.baseline_done:
                    need.append("run ★ FULL BASELINE on the Read tab (saves a backup)")
                return "The Writes tab opens once you " + " and ".join(need) + "."
            return "That tab isn't available yet — finish the earlier phase first."

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
            self.lbl_ver.config(text="")
            self._refresh_write_rows()
            self._apply_gates()

        def _on_close(self):
            """Window X / Session→Exit: guard an in-flight operation, then release
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

        def _run_bg(self, fn, done=None):
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current operation.")
                return
            self._busy = True

            def worker():
                try:
                    result = fn()
                    err = None
                except Exception as e:      # surface everything to the UI
                    result, err = None, e

                def finish():
                    self._busy = False
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

        # -- Phase 0: Connect --------------------------------------------------
        def _build_connect_tab(self):
            f = ttk.Frame(self.nb, padding=12)
            self.nb.add(f, text=" 0 · Connect ")
            row = ttk.Frame(f)
            row.pack(fill="x")
            ttk.Label(row, text="Port:").pack(side="left")
            # SIMULATOR is ALWAYS offered (real mode too) so anyone can explore the
            # tool with no bike/cable — the Instructions' "(or SIMULATOR)" is true and
            # no separate --sim build/shortcut is needed. A real port is preselected
            # when one exists; otherwise SIMULATOR, so first launch lands somewhere.
            real_ports = list_serial_ports()
            ports = [SIM_CHOICE] + real_ports
            if sim:
                default = SIM_CHOICE
            else:
                default = preselect_port or (real_ports[0] if real_ports else SIM_CHOICE)
            self.port_var = tk.StringVar(value=default)
            self.cbo_port = ttk.Combobox(row, textvariable=self.port_var,
                                         values=ports, width=22)
            self.cbo_port.pack(side="left", padx=6)
            ttk.Button(row, text="Refresh", command=self._refresh_ports).pack(side="left")
            self.btn_listen = ttk.Button(row, text=VERIFY_LABEL,
                                         command=self._listen_only)
            self.btn_listen.pack(side="left", padx=(12, 0))
            self.btn_connect = ttk.Button(row, text=CONNECT_LABEL,
                                          style=self.sty["accent"],
                                          command=self._connect)
            self.btn_connect.pack(side="left", padx=12)

            ttk.Label(f, text=(
                "Two quick steps — do them in order (Step 1 never transmits, so it's "
                "safe on any cable):\n"
                "  STEP 1 — click '1 · Verify link': it only LISTENS (sends nothing) "
                "to confirm your cable + baud are good and the bike is talking. Power "
                "the bike during the ~45 s window — key ON, or just plug in the AC "
                "charger (the bike shows Mode: Charging).\n"
                "  STEP 2 — click '2 · Connect & Probe' to open the live session.\n"
                "  No bike yet? Pick 'SIMULATOR (no bike)' in the Port list to explore "
                "everything at the desk.\n"
                "  Cable wiring + pinout: Help → Wiring diagram. Isolation-resistance "
                "reads are only valid OFF the charger."),
                justify="left", padding=(0, 10),
                foreground=P["warn"]).pack(anchor="w")

            self.txt_probe = self._console_text(f, 16)
            self.txt_probe.pack(fill="both", expand=True)

        def _refresh_ports(self):
            real_ports = list_serial_ports()
            self.cbo_port.config(values=[SIM_CHOICE] + real_ports)
            if not hasattr(self, "txt_probe"):
                return
            # A2: give Refresh visible feedback — a blank list otherwise looks broken
            if real_ports:
                self._probe_log("COM ports found: %s" % ", ".join(real_ports))
            else:
                self._probe_log(
                    "No COM ports found. Plug in the FTDI cable and click Refresh; if "
                    "it still doesn't appear, install the FTDI VCP driver (Windows: "
                    "Device Manager -> Ports). Meanwhile you can pick '%s' to explore "
                    "the tool without a bike." % SIM_CHOICE)

        def _probe_log(self, text):
            self.txt_probe.config(state="normal")
            self.txt_probe.insert("end", text + "\n")
            self.txt_probe.see("end")
            self.txt_probe.config(state="disabled")

        def _make_port(self, port_name):
            """Open a SimPort or a real serial port for `port_name`."""
            if sim or port_name == SIM_CHOICE:
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

        def _listen_only(self):
            """STAGE 1: open the port and only LISTEN (never transmit in software),
            so it proves RX wiring/baud with zero risk on any fixed cable."""
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current operation.")
                return
            if self.connected:
                messagebox.showinfo(APP_NAME, "Already connected. Stage-1 listen is "
                                    "for BEFORE connecting. Restart the app to run it.")
                return
            port_name = self.port_var.get().strip()
            if not port_name:
                messagebox.showerror(APP_NAME, "No port selected. Pick your COM port "
                                     "(click Refresh after plugging in the cable), or "
                                     "choose '%s' to explore without a bike." % SIM_CHOICE)
                return
            is_simport = sim or port_name == SIM_CHOICE
            self._ensure_log_dir()          # G2: fall back if the save folder died

            # A4: the listen window is silent for up to 45 s — say so up front + run
            # a countdown on the button so it never looks like a hang.
            secs = 2 if is_simport else 45
            self._probe_log("\n=== Listening (Stage 1, no transmit) — power the bike "
                            "NOW (key ON or plug in the AC charger); ~%d s… ===" % secs)
            self.btn_listen.config(state="disabled")
            self.btn_connect.config(state="disabled")

            def job():
                port = self._make_port(port_name)
                logger = SessionLogger(base_dir=self.log_dir, tag="listen")
                try:
                    data = Transport(port, logger).listen(secs)
                finally:
                    try:
                        port.close()
                    except Exception:
                        pass
                sigs = [s.decode() for s in (b"Zero Motorcycles MBB", b"Reset Source:",
                        b"Checking EEPROM", b"ZERO MBB>") if s in data]
                return len(data), sigs, logger.dir

            def done(result):
                nbytes, sigs, folder = result
                self._probe_log("\n=== STAGE-1 LISTEN (no transmit) -> %s ===" % folder)
                self._probe_log("  received %d bytes" % nbytes)
                if sigs:
                    self._probe_log("  banner signatures seen: %s" % ", ".join(sigs))
                    self._probe_log("  Link verified — RX wiring + baud look GOOD. "
                                    "Now click '2 · Connect & Probe' to start the "
                                    "session.")
                elif nbytes == 0:
                    self._probe_log("  NOTHING received. Power the bike DURING the "
                                    "listen window (key ON or plug in the charger); "
                                    "check GND→pin 5 and the bike-Tx→FTDI-RXD wire.")
                else:
                    self._probe_log("  data received but no banner signature — if it "
                                    "looks like garbage the baud is wrong or Tx/Rx "
                                    "are swapped.")

            self._run_bg(job, done)
            self._listen_countdown(secs)

        def _listen_countdown(self, remaining):
            # A4: tick the Listen button while the (silent) listen window is open;
            # restore both buttons the moment the job finishes — success OR error
            # (done() only fires on success, so this also covers the error path).
            if not self._busy:
                for b in (self.btn_listen, self.btn_connect):
                    try:
                        b.config(state="normal")
                    except Exception:
                        pass
                try:
                    self.btn_listen.config(text=VERIFY_LABEL)
                except Exception:
                    pass
                return
            try:
                self.btn_listen.config(text="Listening… %d s" % max(0, remaining))
            except Exception:
                pass
            self.after(1000, lambda: self._listen_countdown(remaining - 1))

        def _connect(self):
            # T3: honor the busy guard BEFORE the destructive reset. _reset_session_state
            # closes the port + wipes the journal; running it ahead of the _busy check
            # would yank the port out from under an in-flight write (between send and
            # verify) and clear the revert list, only to then refuse the connect.
            if self._busy:
                messagebox.showinfo(APP_NAME, "Busy — wait for the current operation.")
                return
            port_name = self.port_var.get().strip()
            if not port_name:
                messagebox.showerror(APP_NAME, "No port selected. Pick your COM port "
                                     "(click Refresh after plugging in the cable), or "
                                     "choose '%s' to explore without a bike." % SIM_CHOICE)
                return
            # D1: every connection re-earns its phases (drops any --sim rehearsal
            # state and closes a previously-open port before reopening).
            self._reset_session_state()
            self._ensure_log_dir()          # G2: fall back if the save folder died
            is_simport = sim or port_name == SIM_CHOICE

            def job():
                # B3: narrate each step into the connect console AS IT HAPPENS (the
                # worker enqueues to _cbq; the main loop pumps it) — no more staring
                # at a bare progress bar.
                def log(msg):
                    self._cbq.put(lambda m=msg: self._probe_log(m))

                tag = "sim" if is_simport else port_name.replace(":", "")
                logger = SessionLogger(base_dir=self.log_dir, tag=tag)
                log("Session folder: %s" % logger.dir)
                port = self._make_port(port_name)
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
                            "- Click '1 · Verify link' to prove RX wiring first.\n"
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
                self.lbl_ver.config(text="MBB firmware rev %s"
                                    % (rev if rev is not None else "?"))
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
                self.nb.select(1)

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
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text=" 1 · Read ")

            ttk.Label(f, text="One-shot reads — each grabs a snapshot of the bike's "
                      "own data. Hover a button for what it shows; interpret the "
                      "numbers on the Analyze tab. Or run ★ FULL BASELINE to capture "
                      "everything at once.", foreground=P["dim"], wraplength=940,
                      justify="left").pack(anchor="w", pady=(0, 6))
            btns = ttk.Frame(f)
            btns.pack(fill="x")
            quick = READ_COMMANDS + DUMP_COMMANDS
            for i, cmd in enumerate(quick):
                b = ttk.Button(btns, text=cmd, width=14,
                               command=lambda c=cmd: self._read_cmd(c))
                b.grid(row=i // 7, column=i % 7, padx=2, pady=2, sticky="w")
                self._add_tooltip(b, READ_TIPS.get(cmd, ""))
            self.btn_baseline = ttk.Button(
                btns, text="★ FULL BASELINE", width=18,
                style=self.sty["accent"], command=self._baseline)
            self.btn_baseline.grid(row=2, column=0, columnspan=2, padx=2, pady=(6, 2),
                                   sticky="w")
            self.prg = ttk.Progressbar(btns, mode="determinate", length=260)
            self.prg.grid(row=2, column=2, columnspan=3, padx=8, sticky="w")
            self.lbl_prog = ttk.Label(btns, text="")
            self.lbl_prog.grid(row=2, column=5, columnspan=2, sticky="w")
            # A2: the heavy log dumps get their OWN row with a warning-colored label
            # and go through a confirm dialog — they can make the BMS drop the
            # drivetrain contactor, so they must never be run casually or in baseline.
            hrow = ttk.Frame(f)
            hrow.pack(fill="x", pady=(4, 0))
            ttk.Label(hrow, text="Heavy (⚠ may open the contactor):",
                      foreground=P["warn"]).pack(side="left", padx=(0, 6))
            for cmd in HEAVY_COMMANDS:
                hb = ttk.Button(hrow, text=cmd, width=14,
                                command=lambda c=cmd: self._read_heavy(c))
                hb.pack(side="left", padx=2)
                self._add_tooltip(hb, READ_TIPS.get(cmd, ""))

            # E1: live "Watch" — repeat one light read on a timer (reads only, so it
            # stays fully inside the safety model). Great for a charge session.
            wrow = ttk.Frame(f)
            wrow.pack(fill="x", pady=(4, 0))
            self.watch_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(wrow, text="Watch (repeat a read)",
                            variable=self.watch_var,
                            command=self._toggle_watch).pack(side="left")
            self.watch_cmd = tk.StringVar(value="status")
            ttk.Combobox(wrow, textvariable=self.watch_cmd, width=9, state="readonly",
                         values=["status", "bms", "inputs", "sevcon", "dash",
                                 "chargers"]).pack(side="left", padx=6)
            ttk.Label(wrow, text="every").pack(side="left")
            self.watch_secs = tk.StringVar(value="5")
            ttk.Combobox(wrow, textvariable=self.watch_secs, width=4, state="readonly",
                         values=["3", "5", "10", "30"]).pack(side="left", padx=4)
            ttk.Label(wrow, text="s").pack(side="left")

            self.txt_out = self._console_text(f, 20)
            self.txt_out.pack(fill="both", expand=True, pady=(8, 4))

            raw = ttk.Frame(f)
            raw.pack(fill="x")
            ttk.Label(raw, text="Raw command (blocklist enforced):").pack(side="left")
            self.raw_var = tk.StringVar()
            ent = ttk.Entry(raw, textvariable=self.raw_var, width=44)
            ent.pack(side="left", padx=6)
            ent.bind("<Return>", lambda e: self._raw_send())
            ttk.Button(raw, text="Send", command=self._raw_send).pack(side="left")
            ttk.Label(raw, foreground=P["warn"],
                      text="  undocumented commands: read-only intent, at your own risk"
                      ).pack(side="left")

        def _read_heavy(self, cmd):
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
            self._read_cmd(cmd, idle_timeout=30.0)   # console pauses mid-dump

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

        def _read_cmd(self, cmd, quiet=False, idle_timeout=None):
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
                    progress_cb=prog if is_dump else None)
                return out, self.transport.last_saved_path

            def done(result):
                out, path = result
                self.lbl_prog.config(text="")
                if not quiet:
                    self._out("\n### %s  (saved: %s)\n%s" % (cmd, os.path.basename(path), out))
                    # C1: point first-timers at where the numbers get interpreted
                    # (once — don't nag on every read).
                    if not getattr(self, "_analyze_hint_shown", False):
                        self._out("  → To interpret these (ok / watch / alert), open "
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

        def _raw_send(self):
            cmd = self.raw_var.get().strip()
            if not cmd:
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
            if reason:
                messagebox.showwarning(APP_NAME, "REFUSED: %s" % reason)
                return
            # A1 (SAFE-1): a HEAVY log dump typed into the raw box must get the
            # SAME contactor warning + confirm + long idle timeout as the Heavy
            # buttons — otherwise `eventlogdump`/`dumpall` (or a variant like
            # `eventlogdump 5`) would drop the drivetrain contactor with no
            # warning and truncate under the short read timeout. Route by head.
            if head in HEAVY_COMMANDS:
                self._read_heavy(cmd)
                return
            # (T6: the 2-token `set <name>` form is now refused outright by
            # command_blocked, so the old prompt-for-value snap-out is dead code.)
            self._read_cmd(cmd)

        def _baseline(self):
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
                        self.lbl_prog.config(text="baseline: %s (%d/%d)"
                                             % (c, i + 1, len(seq))),
                        self.prg.config(maximum=len(seq), value=i),
                        self._out("  [%d/%d] reading %s…" % (i + 1, len(seq), c))))
                    is_dump = cmd in LONG_COMMANDS
                    prog = None
                    if is_dump:
                        def prog(n, c=cmd):
                            self._cbq.put(lambda: self.lbl_prog.config(
                                text="baseline: %s (%d KB)" % (c, n // 1024)))
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
                    self._apply_gates()
                    self.lbl_prog.config(text="baseline complete")
                    self._out("\n=== FULL BASELINE captured -> %s ===" % self.logger.dir)
                    if errors:
                        self._out("(%d command(s) failed; retry them with the read "
                                  "buttons: %s)" % (len(errors), ", ".join(errors)))
                    # C1: a backup exists now — steer to interpretation + next phase.
                    self._out("→ Open the Analyze tab and click 'Use current session' "
                              "to see battery health, temps and gearing flagged "
                              "ok / watch / alert.")
                    self._out("Backup saved — the Writes tab is now available after "
                              "you log in (Login is open any time you're connected).")
                else:
                    self.lbl_prog.config(text="baseline incomplete")
                    self._out("\n[!] BASELINE INCOMPLETE — essential reads missing/"
                              "unparsed: %s. No backup was saved, so Writes stays "
                              "LOCKED; fix the link and re-run FULL BASELINE."
                              % ", ".join(missing))

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
                self._out("[note] baseline captured while CHARGING — the isolation "
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
            f = ttk.Frame(self.nb, padding=12)
            self.nb.add(f, text=" 2 · Login ")
            ttk.Label(f, text=(
                "Some tuning settings sit behind the console's service login. The "
                "passwords below are the SERVICE passwords Zero owners have "
                "documented publicly over the years (%s) — logging in only REVEALS "
                "those settings; it is read-only and changes nothing on the bike. "
                "Click 'Try known passwords' to attempt them in order, or type your "
                "own and 'Try this password'. If they all fail, no problem — the tool "
                "stays read-only. On success you reach login LEVEL 2 (the tuning "
                "level): the full `set` list appears and the Writes tab unlocks. A "
                "typed password is masked in the logs and never saved to disk unless "
                "you say yes when it offers to remember it after login (clear saved "
                "ones via Session → Forget saved login passwords)."
                % ", ".join(COMMUNITY_PASSWORDS)),
                wraplength=920, justify="left").pack(anchor="w")

            row = ttk.Frame(f)
            row.pack(fill="x", pady=8)
            ttk.Button(row, text="Try known passwords", style=self.sty["accent"],
                       command=self._login).pack(side="left")
            ttk.Label(row, text="     Password:").pack(side="left")
            self.login_pw = tk.StringVar()
            ent = ttk.Entry(row, textvariable=self.login_pw, width=22, show="*")
            ent.pack(side="left", padx=4)
            ent.bind("<Return>", lambda e: self._login_custom())
            ttk.Button(row, text="Try this password",
                       command=self._login_custom).pack(side="left")

            self.txt_login = self._console_text(f, 24)
            self.txt_login.pack(fill="both", expand=True)

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

        def _login(self, passwords=None, redact=False):
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
                    return
                self.logged_in = True
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
                            "Session → Forget saved login passwords."):
                        config.add_saved_password(used)
                        self._login_log("Password remembered — it'll be tried "
                                        "automatically next session (Session menu → "
                                        "Forget saved login passwords to clear).")
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

            self._run_bg(job, done)

        # -- Phase 3: Writes -------------------------------------------------------
        def _build_write_tab(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text=" 3 · Writes ")

            top = ttk.Frame(f)
            top.pack(fill="x")
            self.unlock_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(top, text="UNLOCK WRITES (master gate)",
                            style=self.sty["toggle"],
                            variable=self.unlock_var).pack(side="left")
            # D3: a read-only "what could I change" reference, right here (not only
            # buried in the Bike menu).
            ttk.Button(top, text="What can I change? (read-only)",
                       command=self._show_write_options).pack(side="right")

            # D4: say what arming the toggle actually does (and what it does NOT).
            ttk.Label(f, foreground=P["dim"], wraplength=940, justify="left",
                      text="Arming UNLOCK WRITES changes nothing by itself — it only "
                      "enables the Write… button. Every write still asks you to "
                      "confirm, backs up all settings first, reads the value back to "
                      "verify, and logs it so you can Revert. The rows below are the "
                      "settings from YOUR bike's live `set` dump (what the console "
                      "reported after login) that are safe to change.").pack(
                          anchor="w", pady=(2, 0))
            ttk.Label(f, text=WRITE_PANEL_CONTEXT, foreground=P["warn"],
                      wraplength=940, justify="left").pack(anchor="w", pady=(2, 4))

            cols = ("name", "current", "risk")
            self.tree = ttk.Treeview(f, columns=cols, show="headings", height=9)
            for c, w in zip(cols, (170, 300, 420)):
                self.tree.heading(c, text=c.title())
                self.tree.column(c, width=w, anchor="w")
            self.tree.pack(fill="x", pady=(8, 4))
            self._attach_tree_copy(self.tree)       # E4
            self.tree.tag_configure("safe", foreground="#7fe0a0")
            self.tree.tag_configure("caution", foreground=P["warn"])
            self.tree.bind("<<TreeviewSelect>>", self._show_effect)

            self.lbl_effect = ttk.Label(f, text="Select a setting to see its effect.",
                                        wraplength=1000, justify="left", padding=(0, 4))
            self.lbl_effect.pack(anchor="w")

            wrow = ttk.Frame(f)
            wrow.pack(fill="x", pady=4)
            ttk.Label(wrow, text="New value:").pack(side="left")
            self.newval_var = tk.StringVar()
            ttk.Entry(wrow, textvariable=self.newval_var, width=16).pack(side="left", padx=6)
            ttk.Button(wrow, text="Write…", style=self.sty["danger"],
                       command=self._write).pack(side="left")

            ttk.Label(f, text="Safety guards (read-only, never writable) — Sevcon-side "
                      "/ documented thresholds. A value shows only if your bike "
                      "exposes it in `set`; rev 41 usually doesn't (you'll see "
                      "'(not in live dump)'). The simulator fills in examples.",
                      foreground=P["dim"], wraplength=940, justify="left").pack(
                          anchor="w", pady=(10, 2))
            self.txt_guards = self._console_text(f, 6, fg=P["warn"])
            self.txt_guards.pack(fill="x")

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

        def _refresh_write_rows(self):
            if not hasattr(self, "tree"):
                return
            self.tree.delete(*self.tree.get_children())
            for name in self.settings_order:
                if name in WRITE_WHITELIST and name in self.settings:
                    label, effect, risk, _v, _w = WRITE_WHITELIST[name]
                    tag = "safe" if risk.startswith("SAFE") else "caution"
                    self.tree.insert("", "end", iid=name, tags=(tag,), values=(
                        name, self.settings[name]["value"], risk.split(" - ")[0]))
            # guards pane
            lines = []
            for gname, gdesc in READONLY_GUARDS:
                val = self.settings.get(gname, {}).get("value", "(not in live dump)")
                lines.append("  %-16s %-24s %s" % (gname, val, gdesc))
            self.txt_guards.config(state="normal")
            self.txt_guards.delete("1.0", "end")
            self.txt_guards.insert("end", "\n".join(lines))
            self.txt_guards.config(state="disabled")

        def _show_effect(self, _evt=None):
            sel = self.tree.selection()
            if not sel:
                return
            name = sel[0]
            label, effect, risk, _v, _w = WRITE_WHITELIST[name]
            self.lbl_effect.config(text="%s — %s\nEFFECT: %s\nRISK: %s"
                                   % (name, label, effect, risk))

        def _write(self):
            sel = self.tree.selection()
            if not sel:
                messagebox.showinfo(APP_NAME, "Select a setting row first.")
                return
            if not self.unlock_var.get():
                messagebox.showwarning(APP_NAME, "Writes are locked. Toggle the master "
                                       "'UNLOCK WRITES' gate first.")
                return
            name = sel[0]
            new_val = self.newval_var.get().strip()
            if not new_val:
                messagebox.showinfo(APP_NAME, "Enter a new value.")
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
                text = ("%s — %s\n\n%s  ->  %s\n\nEFFECT: %s\nRISK: %s\n%s\n"
                        "What happens when you click OK:\n"
                        "  1. a full backup of ALL current settings is saved to the "
                        "session folder,\n"
                        "  2. the change is sent, then read back to VERIFY it took,\n"
                        "  3. it's recorded in the writes journal — select it and "
                        "click 'Revert selected' (below the table) to undo it.\n\n"
                        "Proceed?"
                        % (name, label, old_val, new_val, effect, risk,
                           ("\nWARNING: %s\n" % warn) if warn else ""))
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
            """Stage a revert (select the row + fill the old value); the user then
            presses Write to apply it through the normal confirm/backup flow."""
            self.newval_var.set(first_number(old))
            for iid in self.tree.get_children():
                if iid == name:
                    self.tree.selection_set(iid)
                    break
            messagebox.showinfo(APP_NAME, "Revert staged: %s -> %s. Review and press "
                                "Write… to apply (same confirm/backup flow)." % (name, old))

        def _revert(self):
            sel = self.lst_journal.curselection()
            if not sel:
                return
            name, old, new = self.journal_entries[sel[0]]
            self._stage_revert(name, old)

        # -- Analyze tab (Health / Rides / Compare / Gearing) ----------------
        def _build_analyze_tab(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text=" 4 · Analyze ")

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
                                 "explanation of what it means and why it's "
                                 "ok / watch / alert.")
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
            ttk.Label(rbtns, text="  (a zero-log-parser decoded .txt)",
                      foreground=P["dim"]).pack(side="left")
            self.lbl_ride_totals = ttk.Label(
                rf, text="No ride log loaded. Rev 41 doesn't stream ride data as "
                "console text, so click 'Load ride log (.txt)' above — that file is a "
                "DECODED log from the community zero-log-parser tool (it converts a raw "
                "MBB/BMS log into readable lines). OpenMBB's own eventlogdump / "
                "errorlogdump are NOT that input.",
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

            # Gearing
            gf = ttk.Frame(sub, padding=8)
            sub.add(gf, text=" Gearing ")
            grow = ttk.Frame(gf)
            grow.pack(fill="x")
            # B5: default to FX/FXS factory gearing (20/90), not any one owner's
            # re-gear — this is a generic calculator, seed it with stock.
            self.gear_front = tk.StringVar(value="20")
            self.gear_rear = tk.StringVar(value="90")
            self.gear_circ = tk.StringVar(value=str(gearing_mod.DEFAULT_CIRC_MM))
            for label, var, w in (("Front teeth", self.gear_front, 6),
                                  ("Rear teeth", self.gear_rear, 6),
                                  ("Wheel circ mm", self.gear_circ, 8)):
                ttk.Label(grow, text=label + ":").pack(side="left", padx=(0, 2))
                ttk.Entry(grow, textvariable=var, width=w).pack(side="left", padx=(0, 10))
            ttk.Button(grow, text="Compute", style=self.sty["accent"],
                       command=self._gearing_compute).pack(side="left")
            self.txt_gearing = self._console_text(gf, 12)
            self.txt_gearing.pack(fill="both", expand=True, pady=(8, 4))
            grow2 = ttk.Frame(gf)
            grow2.pack(fill="x")
            ttk.Button(grow2, text="Copy spfront/sprear/rwhcirc",
                       command=self._gearing_copy).pack(side="left")
            ttk.Button(grow2, text="Open Writes tab",
                       command=self._goto_writes).pack(side="left", padx=6)
            self._gearing_compute()   # populate with the default 22/88 plan

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
                    "bms.txt / stats.txt from a Read or FULL BASELINE). The metrics "
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
            if not self.logger:
                messagebox.showinfo(APP_NAME, "No live session yet — connect and "
                                    "capture first, or load a saved folder.")
                return
            if self._busy:      # D7: a capture is mid-write; files are partial
                messagebox.showinfo(APP_NAME, "A capture is still running — wait for "
                                    "it to finish before analyzing the current session.")
                return
            self._analyze_set(sessions.load_session(self.logger.dir))

        def _render_health(self):
            self.health_tree.delete(*self.health_tree.get_children())
            self._health_notes = {}
            if not self.analyze_session:
                return
            for i, m in enumerate(health_mod.health_snapshot(self.analyze_session)):
                iid = str(i)
                self.health_tree.insert("", "end", iid=iid, tags=(m["status"],),
                                        values=(m["label"], m["value"],
                                                m["status"].upper()))
                self._health_notes[iid] = m["note"]

        def _health_note(self, _evt=None):
            sel = self.health_tree.selection()
            note = self._health_notes.get(sel[0], "") if sel else ""
            self.lbl_health_note.config(text=note or self._health_hint)

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
            self.lbl_ride_totals.config(
                text="Ride telemetry isn't a console command on this firmware — use "
                     "'Load ride log (.txt)' above to analyze a zero-log-parser export.")

        def _render_ride_records(self, recs, source):
            self.ride_tree.delete(*self.ride_tree.get_children())
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
