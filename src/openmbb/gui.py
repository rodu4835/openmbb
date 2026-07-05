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
from .safety import (READONLY_GUARDS, WRITE_PANEL_CONTEXT, WRITE_WHITELIST,
                     command_blocked)
from .sim import SimPort
from .theme import PALETTE, apply_theme
from .transport import (DUMP_COMMANDS, PROMPT_RE, READ_COMMANDS, SessionLogger,
                        Transport, first_number, list_serial_ports,
                        open_real_port, parse_settings_dump)


# MBB console login passwords tried by the "Try known passwords" button, in
# order. These are community-reported guesses (unverified per firmware). When a
# password is confirmed to work on a bike, ADD IT HERE so it is tried
# automatically and no one has to type it again.
COMMUNITY_PASSWORDS = ["tpsreport", "wideopenthrottle"]


INSTRUCTIONS_TEXT = """\
HOW TO USE OPENMBB

The app is phase-gated — each phase unlocks the next. You can stop after any
phase; closing the window never loses data (everything is saved as you go).

PHASE 0 — CONNECT
  Pick the COM port (or SIMULATOR) and click "Connect & Probe". The app looks
  for the console prompt and reads the firmware version. Garbage output at
  38400 baud usually means the Tx/Rx wires are swapped — stop and recheck.

PHASE 1 — READ
  Click any command button for a one-off read. To advance, click the blue
  ★ FULL BASELINE button: it captures every read plus the full settings dump
  (your backup) and the ~1 MB log dump. Individual reads do NOT unlock Login —
  only FULL BASELINE does. This guarantees a backup exists before any change.

PHASE 2 — LOGIN
  Explicit. "Try known passwords" attempts the community-known ones in order;
  or type a specific password and "Try this password" (it is masked in the logs
  and never saved to disk). Both failing is fine — the tool stays read-only.
  Success unlocks Writes. A confirmed password can be baked into the built-in
  list (COMMUNITY_PASSWORDS in gui.py) so it is tried automatically thereafter.

PHASE 3 — WRITES
  Triple-gated: logged in + the master UNLOCK WRITES switch + a per-write
  confirm dialog. Only whitelisted settings that actually exist on your bike
  appear. Each write re-reads the current value, backs up all settings, sends
  the change, reads it back to verify, and journals it (with a Revert button).

ANALYZE (always available, no bike needed)
  Reads a saved session folder (or the current one) and interprets it:
    - Health : SOC vs voltage, cell balance, capacity, temps, cycles, and the
               effective gearing ratio, each flagged ok / watch / alert.
    - Rides  : per-ride distance, SOC%/km, and temps parsed from the log dump.
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

Before connecting: bike PARKED on stand, key ON, kill switch OFF. Confirm the
FTDI Orange line idles ~3.3 V vs Black and the Red lead is taped off.
Never stream the console while riding.
"""

SAFETY_TEXT = """\
SAFETY MODEL

Read-first, whitelist-only writes. The transport layer refuses dangerous
commands from EVERY path (buttons and the raw box), including:
  format/erase/eeprom, settingsrst, statsrst, log clears/adds, reset,
  exit_to_bl, test, wdt, timing, can, charger, sevcon preop, and any "set" of
  a protected value (abs_disable, bypass_bms, ov_* overrides, motstage*/
  ctrlstage* thermal limits, sevnoregspeed/sevmaxregv/sevnoregfull regen
  guards, model/vin/serial identity).

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

            self._build_menubar()
            self._build_statusbar()
            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            self._build_connect_tab()
            self._build_read_tab()
            self._build_login_tab()
            self._build_write_tab()
            self._build_analyze_tab()
            self._apply_gates()
            self._refresh_save_label()

        # -- helpers ---------------------------------------------------------
        def _console_text(self, parent, height, fg=None):
            return tk.Text(parent, height=height, state="disabled",
                           font=(self.sty["mono"], 9), bg=P["console"],
                           fg=fg or P["termfg"], insertbackground=P["fg"],
                           selectbackground=P["sel"],
                           selectforeground="#eafff2",
                           relief="flat", padx=10, pady=8,
                           highlightthickness=1,
                           highlightbackground=P["panel"],
                           highlightcolor=P["panel"])

        def _pump_cbq(self):
            try:
                while True:
                    fn = self._cbq.get_nowait()
                    fn()
            except queue.Empty:
                pass
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
        def _build_menubar(self):
            m = tk.Menu(self)

            sess = tk.Menu(m, tearoff=0)
            sess.add_command(label="Set save location…",
                             command=self._set_log_dir)
            sess.add_command(label="Open session folder",
                             command=self._open_session_folder)
            sess.add_command(label="Copy session path",
                             command=self._copy_session_path)
            sess.add_separator()
            sess.add_command(label="Refresh COM ports", command=self._refresh_ports)
            sess.add_separator()
            sess.add_command(label="Exit", command=self.destroy)
            m.add_cascade(label="Session", menu=sess)

            bike = tk.Menu(m, tearoff=0)
            bike.add_command(label="Bike info…", command=self._show_bike_info)
            m.add_cascade(label="Bike", menu=bike)

            hlp = tk.Menu(m, tearoff=0)
            hlp.add_command(label="Instructions   (F1)",
                            command=self._show_instructions)
            hlp.add_command(label="Wiring diagram", command=self._show_wiring)
            hlp.add_command(label="Safety notes", command=self._show_safety)
            hlp.add_separator()
            hlp.add_command(label="About", command=self._show_about)
            m.add_cascade(label="Help", menu=hlp)

            self.config(menu=m)
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
            win.transient(self)
            win.focus_set()
            return win

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
            states = [
                "normal",
                "normal" if self.connected else "disabled",
                "normal" if (self.connected and self.baseline_done) else "disabled",
                "normal" if (self.connected and self.baseline_done and self.logged_in)
                else "disabled",
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
            ports = (["SIMULATOR"] if sim else []) + list_serial_ports()
            if sim:
                default = "SIMULATOR"
            else:
                default = preselect_port or (ports[0] if ports else "")
            self.port_var = tk.StringVar(value=default)
            self.cbo_port = ttk.Combobox(row, textvariable=self.port_var,
                                         values=ports, width=18)
            self.cbo_port.pack(side="left", padx=6)
            ttk.Button(row, text="Refresh", command=self._refresh_ports).pack(side="left")
            self.btn_connect = ttk.Button(row, text="Connect && Probe",
                                          style=self.sty["accent"],
                                          command=self._connect)
            self.btn_connect.pack(side="left", padx=12)

            ttk.Label(f, text=(
                "Checklist before connecting to the real bike:\n"
                "  1. Bike PARKED on stand, key ON, kill switch OFF.\n"
                "  2. FTDI Red (+5 V) connected to NOTHING; Orange idles ~3.3 V vs Black.\n"
                "  3. GND→pin 5 (Teal), FTDI RXD←pin 8 bike Tx (Black/White), "
                "FTDI TXD→pin 9 bike Rx (Red/White).\n"
                "  4. Port is under the seat. 38400 8-N-1, newline CR-LF."),
                justify="left", padding=(0, 10),
                foreground=P["warn"]).pack(anchor="w")

            self.txt_probe = self._console_text(f, 18)
            self.txt_probe.pack(fill="both", expand=True)

        def _refresh_ports(self):
            ports = (["SIMULATOR"] if sim else []) + list_serial_ports()
            self.cbo_port.config(values=ports)

        def _probe_log(self, text):
            self.txt_probe.config(state="normal")
            self.txt_probe.insert("end", text + "\n")
            self.txt_probe.see("end")
            self.txt_probe.config(state="disabled")

        def _connect(self):
            port_name = self.port_var.get().strip()
            if not port_name:
                messagebox.showerror(APP_NAME, "Pick a COM port (or run with --sim).")
                return

            def job():
                tag = "sim" if (sim or port_name == "SIMULATOR") else port_name.replace(":", "")
                logger = SessionLogger(base_dir=self.log_dir, tag=tag)
                if sim or port_name == "SIMULATOR":
                    port = SimPort()
                else:
                    port = open_real_port(port_name)
                tr = Transport(port, logger)
                notes = []
                notes.append("Session folder: %s" % logger.dir)
                notes.append("Listening 3 s for unsolicited output...")
                pre = tr.listen(3 if not getattr(port, "is_sim", False) else 0.3)
                notes.append("  got %d bytes" % len(pre))
                notes.append("Sending bare CR-LF, looking for prompt...")
                resp = tr.send_raw_newline()
                text = resp.decode("utf-8", errors="replace")
                prompt = bool(PROMPT_RE.search(resp.strip()[-24:] if resp else b""))
                notes.append("  response: %r" % text[-80:])
                if not prompt and b">" in resp:
                    prompt = True  # tolerant: any >-prompt counts, rev 41 may differ
                if not prompt:
                    raise RuntimeError(
                        "No prompt detected.\n"
                        "- Check COM port and that the key is ON.\n"
                        "- Try newline CR only (terminal test) before rewiring.\n"
                        "- Garbage characters at 38400 usually mean Tx/Rx swapped —\n"
                        "  STOP and re-check wiring, do not guess.\n"
                        "Raw bytes were logged to session_raw.log.")
                ver = tr.exec_command("version", idle_timeout=1.5)
                return logger, tr, notes, ver

            def done(result):
                logger, tr, notes, ver = result
                self.logger, self.transport = logger, tr
                self.connected = True
                self.version_text = ver
                for n in notes:
                    self._probe_log(n)
                self._probe_log("PROMPT OK — connected.\n")
                self._probe_log(ver)
                m = re.search(r"Firmware Rev\s*:?\s*(\w+)", ver)
                self.lbl_ver.config(text="MBB firmware rev %s" % m.group(1) if m else "")
                self._refresh_save_label()
                self._apply_gates()
                self.nb.select(1)

            self._run_bg(job, done)

        # -- Phase 1: Read -----------------------------------------------------
        def _build_read_tab(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text=" 1 · Read ")

            btns = ttk.Frame(f)
            btns.pack(fill="x")
            for i, cmd in enumerate(READ_COMMANDS + DUMP_COMMANDS):
                b = ttk.Button(btns, text=cmd, width=14,
                               command=lambda c=cmd: self._read_cmd(c))
                b.grid(row=i // 7, column=i % 7, padx=2, pady=2, sticky="w")
            self.btn_baseline = ttk.Button(
                btns, text="★ FULL BASELINE", width=18,
                style=self.sty["accent"], command=self._baseline)
            self.btn_baseline.grid(row=2, column=0, columnspan=2, padx=2, pady=(6, 2),
                                   sticky="w")
            self.prg = ttk.Progressbar(btns, mode="determinate", length=260)
            self.prg.grid(row=2, column=2, columnspan=3, padx=8, sticky="w")
            self.lbl_prog = ttk.Label(btns, text="")
            self.lbl_prog.grid(row=2, column=5, columnspan=2, sticky="w")

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

        def _read_cmd(self, cmd, quiet=False, done_cb=None):
            is_dump = cmd in DUMP_COMMANDS

            def job():
                def prog(nbytes):
                    self._cbq.put(lambda: self.lbl_prog.config(
                        text="%s: %d KB" % (cmd, nbytes // 1024)))
                out = self.transport.exec_command(
                    cmd, idle_timeout=15.0 if is_dump else 2.5,
                    max_time=900.0 if is_dump else 60.0,
                    progress_cb=prog if is_dump else None)
                return out, self.transport.last_saved_path

            def done(result):
                out, path = result
                self.lbl_prog.config(text="")
                if not quiet:
                    self._out("\n### %s  (saved: %s)\n%s" % (cmd, os.path.basename(path), out))
                if cmd == "set":
                    self._ingest_settings(out)
                if cmd == "help" and not self.logged_in:
                    self.help_logged_out = out
                if done_cb:
                    done_cb(out)

            self._run_bg(job, done)

        def _raw_send(self):
            cmd = self.raw_var.get().strip()
            if not cmd:
                return
            reason = command_blocked(cmd)
            if reason:
                messagebox.showwarning(APP_NAME, "REFUSED: %s" % reason)
                return
            self._read_cmd(cmd)

        def _baseline(self):
            seq = READ_COMMANDS + ["set"] + DUMP_COMMANDS

            def job():
                results = {}
                for i, cmd in enumerate(seq):
                    self._cbq.put(lambda c=cmd, i=i: (
                        self.lbl_prog.config(text="baseline: %s (%d/%d)"
                                             % (c, i + 1, len(seq))),
                        self.prg.config(maximum=len(seq), value=i)))
                    is_dump = cmd in DUMP_COMMANDS
                    out = self.transport.exec_command(
                        cmd, idle_timeout=15.0 if is_dump else 2.5,
                        max_time=900.0 if is_dump else 60.0)
                    results[cmd] = out
                stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.logger.save_named("settings_baseline_%s.txt" % stamp,
                                       results.get("set", ""))
                return results

            def done(results):
                self.prg.config(value=0)
                self.lbl_prog.config(text="baseline complete")
                self._out("\n=== FULL BASELINE captured -> %s ===" % self.logger.dir)
                if "help" in results and not self.logged_in:
                    self.help_logged_out = results["help"]
                self._ingest_settings(results.get("set", ""))
                self.baseline_done = True
                self._apply_gates()
                self._out("Phase 2 (Login) unlocked.")

            self._run_bg(job, done)

        def _ingest_settings(self, dump_text):
            settings, order = parse_settings_dump(dump_text)
            if settings:
                self.settings, self.settings_order = settings, order
                self._out("[parsed %d settings from live dump]" % len(settings))
                self._refresh_write_rows()

        # -- Phase 2: Login ------------------------------------------------------
        def _build_login_tab(self):
            f = ttk.Frame(self.nb, padding=12)
            self.nb.add(f, text=" 2 · Login ")
            ttk.Label(f, text=(
                "Login is explicit and never automatic. Click 'Try known "
                "passwords' to attempt the community-known ones (%s), or type a "
                "specific password and 'Try this password'. Both may fail — the "
                "tool just stays read-only then. A typed password is never saved "
                "to disk (it is masked in the logs)." % ", ".join(COMMUNITY_PASSWORDS)),
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
            pws = [p for p in (passwords if passwords is not None
                               else COMMUNITY_PASSWORDS) if p]
            if not pws:
                messagebox.showinfo(APP_NAME, "Enter a password to try.")
                return

            def shown(pw):
                return "****" if redact else pw

            def job():
                attempts = []
                success = False
                used = None
                for pw in pws:
                    out = self.transport.exec_command(
                        "login %s" % pw, idle_timeout=2.0,
                        redact=pw if redact else None)
                    attempts.append((pw, out))
                    if re.search(r"logged in|level\s*[1-9]", out, re.I):
                        success = True
                        used = pw
                        break
                post = {}
                if success:
                    post["help"] = self.transport.exec_command("help", idle_timeout=2.5)
                    post["set"] = self.transport.exec_command("set", idle_timeout=4.0,
                                                              max_time=120.0)
                return attempts, success, post, used

            def done(result):
                attempts, success, post, used = result
                for pw, out in attempts:
                    masked = out.replace(pw, "****") if redact else out
                    self._login_log(">>> login %s\n%s\n" % (shown(pw), masked))
                if not success:
                    self._login_log("Rejected — staying read-only. (MBB passwords "
                                    "are community-held; try another.)")
                    return
                self.logged_in = True
                if redact:
                    self._login_log("LOGIN OK with your typed password. It was NOT "
                                    "saved. To have it tried automatically next "
                                    "time, add it to COMMUNITY_PASSWORDS in gui.py.")
                else:
                    self._login_log("LOGIN OK (login %s). Re-captured help + settings."
                                    % used)
                if self.help_logged_out and post.get("help"):
                    diff = "\n".join(difflib.unified_diff(
                        self.help_logged_out.splitlines(),
                        post["help"].splitlines(),
                        "help (logged out)", "help (logged in)", lineterm=""))
                    self._login_log("\n--- help diff (new commands revealed) ---\n"
                                    + (diff or "(no differences)"))
                if post.get("set"):
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
            ttk.Label(top, text="  " + WRITE_PANEL_CONTEXT, foreground=P["warn"],
                      wraplength=760, justify="left").pack(side="left")

            cols = ("name", "current", "risk")
            self.tree = ttk.Treeview(f, columns=cols, show="headings", height=9)
            for c, w in zip(cols, (170, 300, 420)):
                self.tree.heading(c, text=c.title())
                self.tree.column(c, width=w, anchor="w")
            self.tree.pack(fill="x", pady=(8, 4))
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

            ttk.Label(f, text="Safety guards (read-only, never writable):",
                      foreground=P["dim"]).pack(anchor="w", pady=(10, 2))
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
                text = ("%s — %s\n\n%s -> %s\n\nEFFECT: %s\nRISK: %s\n%s\n"
                        "A full settings backup will be saved first. Proceed?"
                        % (name, label, old_val, new_val, effect, risk,
                           ("\nWARNING: %s\n" % warn) if warn else ""))
                if not messagebox.askokcancel("Confirm write", text):
                    return

                def job2():
                    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    self.logger.save_named("settings_backup_%s.txt" % stamp, dump)
                    self.transport.exec_command("set %s %s" % (name, new_val),
                                                idle_timeout=2.5)
                    verify = self.transport.exec_command("set", idle_timeout=4.0,
                                                         max_time=120.0)
                    live2, _ = parse_settings_dump(verify)
                    got = live2.get(name, {}).get("value", "")
                    verified = first_number(got) == first_number(new_val)
                    self.logger.journal_write(name, old_val, new_val, verified)
                    return old_val, got, verified, verify

                def done2(r2):
                    old_val2, got, verified, verify_dump = r2
                    self.journal_entries.append((name, old_val2, new_val))
                    self.lst_journal.insert(
                        "end", "%s: %s -> %s  [%s]" % (name, old_val2, new_val,
                                                       "verified" if verified else
                                                       "READBACK MISMATCH: %r" % got))
                    self._ingest_settings(verify_dump)
                    if not verified:
                        messagebox.showwarning(APP_NAME,
                                               "Read-back mismatch for %s: bike reports %r. "
                                               "Check the raw log." % (name, got))
                self._run_bg(job2, done2)

            self._run_bg(job, confirm_and_send)

        def _revert(self):
            sel = self.lst_journal.curselection()
            if not sel:
                return
            name, old, new = self.journal_entries[sel[0]]
            self.newval_var.set(first_number(old))
            for iid in self.tree.get_children():
                if iid == name:
                    self.tree.selection_set(iid)
                    break
            messagebox.showinfo(APP_NAME, "Revert staged: %s -> %s. Review and press "
                                "Write… to apply (same confirm/backup flow)." % (name, old))

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
            self.health_tree.bind("<<TreeviewSelect>>", self._health_note)
            self.lbl_health_note = ttk.Label(hf, text="", wraplength=980,
                                             foreground=P["dim"], justify="left")
            self.lbl_health_note.pack(anchor="w", pady=(6, 0))

            # Rides
            rf = ttk.Frame(sub, padding=8)
            sub.add(rf, text=" Rides ")
            self.lbl_ride_totals = ttk.Label(rf, text="Load a session with a "
                                             "dumplogs capture to analyze rides.",
                                             foreground=P["dim"])
            self.lbl_ride_totals.pack(anchor="w")
            rcols = ("start", "km", "soc", "socpkm", "temp", "rpm")
            heads = ("Start", "Distance km", "SOC used %", "SOC%/km",
                     "Max temp C", "Max rpm")
            self.ride_tree = ttk.Treeview(rf, columns=rcols, show="headings", height=11)
            for c, h, w in zip(rcols, heads, (150, 100, 90, 90, 90, 90)):
                self.ride_tree.heading(c, text=h)
                self.ride_tree.column(c, width=w, anchor="w")
            self.ride_tree.pack(fill="both", expand=True, pady=(6, 0))

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
            self.gear_front = tk.StringVar(value="22")
            self.gear_rear = tk.StringVar(value="88")
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
                       command=lambda: self.nb.select(3)).pack(side="left", padx=6)
            self._gearing_compute()   # populate with the default 22/88 plan

        def _analyze_set(self, session):
            self.analyze_session = session
            self.lbl_loaded.config(text="loaded: %s" % session.name,
                                   foreground=P["green"])
            self._render_health()
            self._render_rides()

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
            self.lbl_health_note.config(text=note)

        def _render_rides(self):
            self.ride_tree.delete(*self.ride_tree.get_children())
            if not self.analyze_session:
                self.lbl_ride_totals.config(text="Load a session with a dumplogs "
                                            "capture to analyze rides.")
                return
            recs = parsers.parse_ride_log(self.analyze_session.cmd("dumplogs"))
            summ = rides.summarize_rides(recs)
            t = summ["totals"]
            if not summ["rides"]:
                self.lbl_ride_totals.config(text="No riding records found in this "
                                            "session's dumplogs capture.")
                return
            self.lbl_ride_totals.config(
                text="%d rides · %.1f km · mean %s SOC%%/km · max temp %s C · %d samples"
                % (t["ride_count"], t["total_km"], t["mean_soc_per_km"],
                   t["max_temp_c"], t["samples"]))
            for r in summ["rides"]:
                self.ride_tree.insert("", "end", values=(
                    r["start_ts"] or "?", r["distance_km"], r["soc_used_pct"],
                    r["soc_per_km"] if r["soc_per_km"] is not None else "n/a",
                    r["max_temp_c"] if r["max_temp_c"] is not None else "n/a",
                    r["max_rpm"] if r["max_rpm"] is not None else "n/a"))

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
            for n, r in res["gearing_trend"]:
                self._compare_out("  %-28s %s"
                                  % (n, ("%.2f:1" % r) if r is not None else "n/a"))

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
