"""Simulator: a fake serial port for hardware-free bring-up and testing.

NOTICE — all text below is SYNTHETIC. It is original, paraphrased sample data
authored for this project to exercise the parser and GUI without a bike. It is
NOT captured from a Zero Motorcycles device and does not reproduce Zero's
firmware output, manuals, or console text. Setting *names* and command *tokens*
are functional identifiers (needed so the tool speaks the same protocol); every
human-readable description, banner, and value here is made up. Identifiers such
as VIN and serial numbers are obvious placeholders, not a real vehicle's.

The real rev-41 output will differ — a live capture is always ground truth.
"""

import queue
import re

# Setting name -> (paraphrased description, sample value). Names are functional;
# descriptions and values are synthetic. Values keep the first-number-parseable
# shape the write validators expect. spfront/sprear reflect stock 20/90 gearing.
SIM_SETTINGS = {
    "model":          ("Model designation", "FXS"),
    "model_year":     ("Model year", "2017"),
    "vin":            ("Vehicle ID (placeholder)", "ZEROSIMVIN0000000"),
    "serial":         ("Board serial (placeholder)", "SIM-MBB-0000"),
    "runtime":        ("Cumulative run time", "00013:05:40:10"),
    "secidle":        ("Idle seconds before shutdown", "7200"),
    "motstage1":      ("Motor temp warn point", "100 C"),
    "motstage2":      ("Motor temp cutback point", "145 C"),
    "ctrlstage1":     ("Controller temp warn point", "70 C"),
    "ctrlstage2":     ("Controller temp cutback point", "75 C"),
    "spfront":        ("Front sprocket teeth (speedo input)", "20"),
    "sprear":         ("Rear sprocket teeth (speedo input)", "90"),
    "rwhcirc":        ("Rear wheel rollout", "1966 mm"),
    "kill":           ("Kill switch polarity", "closedrun (1)"),
    "zeroneutral":    ("Throttle-off acts as neutral", "Yes"),
    "zerothresh":     ("Throttle-off threshold", "750 mV"),
    "brakeregen":     ("Brake switch triggers regen", "Yes"),
    "brakefilter":    ("Brake switch debounce", "100 ms"),
    "noregenstopped": ("Block regen at low speed", "Yes"),
    "fuelgaugepes":   ("Fuel gauge pessimism", "5%"),
    "sevnoregspeed":  ("Block regen at high speed", "Yes"),
    "sevmaxregrpm":   ("Regen taper RPM", "4500 rpm"),
    "sevnoregfull":   ("Block regen at full charge", "Yes"),
    "sevmaxregv":     ("Regen cell-voltage cutoff", "4160 mV"),
    "sevmaxdischgcur": ("Max discharge current", "520 A"),
    "ov_kickstand":   ("Override kickstand interlock", "No"),
    "ov_mot_temp":    ("Override motor-temp interlock", "No"),
    "ov_cont_temp":   ("Override controller-temp interlock", "No"),
    "ov_batt_temp":   ("Override battery-temp interlock", "No"),
    "ov_low_batt":    ("Override low-battery interlock", "No"),
    "bypass_bms":     ("Bypass BMS interface", "No"),
    "ignore_iso_err": ("Ignore isolation error", "No"),
    "debug_level":    ("Log verbosity", "1"),
    "reserve_sw":     ("Enable SOC reserve", "No"),
    "reserve_pct":    ("Reserve partition size", "33%"),
    "logrun":         ("Ride log interval", "3000 ms"),
    "logchg":         ("Charge log interval", "600000 ms"),
    "chgstby":        ("Post-charge rebalance sleep", "1440 min"),
    "maxcustsp":      ("Custom mode speed cap", "85 MPH ( 137 KPH ) ( 5385 RPM )"),
    "maxcusttq":      ("Custom mode torque cap", "100 ( 100 % of allowed )"),
    "maxcustregcotq": ("Custom mode coast regen", "6 ( 60 % of allowed )"),
    "maxcustregbrtq": ("Custom mode brake regen", "80 ( 80 % of allowed )"),
    "test_mode":      ("Diagnostic test mode", "Off"),
    "abs_disable":    ("Disable ABS via console", "Off"),
}

# Synthetic version banner. Only the "Firmware Rev" line is parsed by the GUI.
SIM_VERSION = """\
== Main Bike Board ==
  Board            : Main Bike Board (simulated)
  Board Rev        : 07
  Firmware Rev     : 41
  Firmware Built   : Nov 17 2024
  (synthetic sample output — not a real device)"""

# Command tokens are functional (the tool sends these); descriptions are ours.
SIM_HELP_LOGGED_OUT = """\
== Main Menu ==
  help            - show this menu
  login           - show or set login level
  logout          - drop login level
  version         - board and firmware revision
  status          - overall bike status
  stats           - lifetime statistics
  runtime         - total run time
  set             - list settings, or edit one
  bms             - battery management data
  bluetooth       - bluetooth link info
  sevcon          - motor controller data
  chargers        - charger port info
  inputs          - raw input measurements
  outputs         - output states
  dash            - instrument cluster data
  eventlogdump    - print the event log
  errorlogdump    - print the error log
  dumpall         - print stats, inputs, settings and logs"""

SIM_HELP_LOGGED_IN = SIM_HELP_LOGGED_OUT + """
  statsrst        - reset statistics
  settingsrst     - reset settings to defaults
  sevcon faults   - list controller faults
  sevcon preop    - toggle controller pre-op mode
  charger         - set charger port parameters
  eventlogclear   - erase the event log
  eventlogadd     - append a line to the event log
  errorlogclear   - erase the error log
  errorlogadd     - append a line to the error log
  eeprom          - eeprom usage / dump / set
  format eeprom   - factory reset (reformat eeprom)
  erase eeprom    - erase entire eeprom
  reset           - software reset
  exit_to_bl      - drop to bootloader
  timing          - system timing
  can             - canbus info
  wdt reset       - force watchdog timeout
  test            - run a diagnostic test"""

SIM_STATUS = """\
== Bike Status (synthetic) ==
  Mode                : Standby
  Charger attached    : No
  Motor controller    : idle, 0 faults
  Drive mode          : Custom
  Motor temp          : 24 C (normal)
  Controller temp     : 22 C (normal)
  Battery SOC         : 61 %
  Pessimistic SOC     : 56 %
  Pack capacity       : 52 Ah (32 Ah remaining)
  Kill switch         : OFF
  Kickstand           : Down
  Warnings            : none
  Errors              : none"""

SIM_STATS = """\
== Statistics (synthetic) ==
  Board rev           : 07
  Firmware rev        : 41
  Resets              : 412 (1 watchdog)
  Max battery temp    : 60 C
  Max motor temp      : 118 C
  Max controller temp : 52 C
  Lifetime efficiency : 64.8 Wh/km
  Odometer            : 6155 km (3825 mi)
  Top speed           : 137 kph (85 mph)
  Max motor speed     : 5449 rpm"""

SIM_BMS = """\
== BMS Data (synthetic) ==
  BMS board rev       : 17
  BMS firmware rev    : 48
  BMS serial          : SIM-BMS-0000
  Pack SOC            : 61 %
  Fuel gauge          : 56 %
  Model year          : 2017
  Pack capacity       : 52 Ah (32 Ah remaining)
  Bricks              : 2
  Discharge current   : 0 A
  Pack voltage        : 106.412 V
  Lowest cell         : 3798 mV (cell 12)
  Highest cell        : 3810 mV (cell 7)
  Pack balance        : 12 mV
  Isolation           : 10486 kOhm
  Pack temps          : 24 24 23 23 C
  Max charge temp     : 50 C
  Charge cycles       : 512
  Faults              : none"""

SIM_SEVCON = """\
== Motor Controller (synthetic) ==
  Motor speed         : 0 rpm
  Motor temp          : 24 C
  Controller temp     : 22 C
  Battery voltage     : 106.25 V
  Max charge current  : -40 A
  Max discharge cur.  : 520 A
  Firmware            : 0712.0002
  Operational         : Yes
  Faults              : 0"""

SIM_CHARGERS = """\
== Chargers (synthetic) ==
  Charger 0           : onboard 720W, not attached
  Charger 1           : onboard 1200W, not attached
  Charge current flow : No"""

SIM_INPUTS = """\
== Inputs (synthetic) ==
  Key on              : Yes
  Pack voltage        : 106350 mV
  3.3V rail           : 3298 mV
  5V rail             : 5011 mV
  Kill switch         : Stop
  Kickstand           : Down
  Brake switch        : Off
  Board temp          : 26 C
  Ambient temp        : 19 C"""

SIM_OUTPUTS = """\
== Outputs (synthetic) ==
  System on           : On
  DC/DC converter     : Off
  Warning light       : Off
  Temp warning LED    : Off
  Charge LED          : Off
  Armed LED           : Off
  Controller enable   : Off
  Custom mode         : On"""

SIM_DASH = """\
== Dash (synthetic) ==
  Clock               : 10:12
  Odometer            : 6155.2 km
  Dash CAN age        : 54 ms"""

SIM_RUNTIME = """\
== Run Time (synthetic) ==
  Total run time      : 00007:19:30:25
  Total charge time   : 00003:01:23:41"""

SIM_EVENTLOG = "\n".join(
    ["== Event Log (synthetic, most recent last) =="] +
    ["  %05d 07/0%d/2026 0%d:1%d:0%d  riding  packTemp %dC  soc %d%%  "
     "vpack 10%d.%03dV  motAmps %3d  motRPM %4d  odo 61%02dkm"
     % (7000 + i, 1 + i % 4, 8 + i % 3, i % 6, i % 10, 24 + i % 4, 90 - i * 3,
        6 - i % 3, 100 + i * 37, 60 + i * 9, 3100 + i * 60, 20 + i)
     for i in range(12)])

SIM_ERRORLOG = """\
== Error Log (synthetic, most recent last) ==
  00001 06/13/2026 06:53:01  watchdog timeout (first boot after update)
  00002 06/13/2026 12:36:13  current-sensor calibration flag not set (self-test)"""

# Community-known console login string; used only so the sim accepts a login.
PW_ACCEPTED = "tpsreport"
PROMPT = b"\r\nZERO MBB> "   # functional: the tool matches this prompt


class SimPort:
    """Fake serial port replaying the synthetic fixtures above. Interface-
    compatible with the subset of pyserial that Transport uses."""

    def __init__(self, greet=True):
        self._rx = queue.Queue()
        self._linebuf = b""
        self._settings = {k: [d, v] for k, (d, v) in SIM_SETTINGS.items()}
        self.logged_in = False
        self.is_sim = True
        if greet:
            self._push(PROMPT)

    # -- port interface -----------------------------------------------------
    def read(self, n=1):
        chunks = []
        try:
            while len(b"".join(chunks)) < n:
                chunks.append(self._rx.get_nowait())
        except queue.Empty:
            pass
        return b"".join(chunks)[:n]

    @property
    def in_waiting(self):
        return self._rx.qsize()

    def write(self, data):
        self._linebuf += data
        while b"\n" in self._linebuf:
            line, _, self._linebuf = self._linebuf.partition(b"\n")
            self._handle(line.strip(b"\r").decode("ascii", errors="replace"))
        return len(data)

    def close(self):
        pass

    # -- behavior -----------------------------------------------------------
    def _push(self, data):
        for i in range(0, len(data), 512):
            self._rx.put(data[i:i + 512])

    def _respond(self, text):
        self._push(text.encode() + PROMPT)

    def _settings_dump(self):
        lines = ["== System Settings (synthetic) =="]
        for name, (desc, value) in self._settings.items():
            pad = " " * max(1, 16 - len(name))
            if name in ("model", "vin", "serial", "model_year", "runtime"):
                lines.append("  %s%s- %-32s %s" % (name, pad, desc, value))
            else:
                lines.append("  %s%s- %-32s: %s" % (name, pad, desc, value))
        return "\n".join(lines)

    def _handle(self, cmd):
        if not cmd:
            self._push(PROMPT)
            return
        parts = cmd.split()
        head = parts[0].lower()
        self._push(cmd.encode() + b"\r\n")  # echo

        if head == "help":
            self._respond(SIM_HELP_LOGGED_IN if self.logged_in else SIM_HELP_LOGGED_OUT)
        elif head == "version":
            self._respond(SIM_VERSION)
        elif head == "status":
            self._respond(SIM_STATUS)
        elif head == "stats":
            self._respond(SIM_STATS)
        elif head == "runtime":
            self._respond(SIM_RUNTIME)
        elif head == "bms":
            self._respond(SIM_BMS)
        elif head == "sevcon":
            self._respond(SIM_SEVCON)
        elif head == "chargers":
            self._respond(SIM_CHARGERS)
        elif head == "inputs":
            self._respond(SIM_INPUTS)
        elif head == "outputs":
            self._respond(SIM_OUTPUTS)
        elif head == "dash":
            self._respond(SIM_DASH)
        elif head == "eventlogdump":
            self._respond(SIM_EVENTLOG)
        elif head == "errorlogdump":
            self._respond(SIM_ERRORLOG)
        elif head == "dumplogs" or head == "dumpall":
            big = "\n".join(
                " %05d 0%d/1%d/2026 0%d:%02d:%02d  riding  packTemp %dC  soc %d%%  "
                "vpack 10%d.%03dV  motAmps %3d  motRPM %4d  odo 6%03dkm"
                % (i, 5 + (i % 2), i % 10, 7 + i % 12, i % 60, (i * 7) % 60,
                   22 + i % 30, 100 - (i % 80), 6 - i % 5, i * 13 % 1000,
                   40 + i % 220, 2400 + (i * 17) % 3000, 100 + i % 100)
                for i in range(2600))
            self._respond("== Log Dump (synthetic, 2600 entries) ==\n" + big)
        elif head == "login":
            if len(parts) == 1:
                self._respond("  login level: %d" % (2 if self.logged_in else 0))
            elif parts[1] == PW_ACCEPTED:
                self.logged_in = True
                self._respond("  logged in at level 2")
            else:
                self._respond("  login failed")
        elif head == "logout":
            self.logged_in = False
            self._respond("  logged out")
        elif head == "bluetooth":
            self._respond("== Bluetooth (synthetic) ==\n  Connected : No")
        elif head == "set":
            if len(parts) == 1:
                self._respond(self._settings_dump())
            elif len(parts) >= 3:
                name = parts[1].lower()
                if not self.logged_in:
                    self._respond("  access denied - please login")
                elif name in self._settings:
                    unit = re.search(r"[A-Za-z%]+\s*$", self._settings[name][1])
                    val = parts[2]
                    if unit and not re.search(r"[A-Za-z%]", val):
                        val = "%s %s" % (val, unit.group(0).strip())
                    self._settings[name][1] = val
                    self._respond("  %s set to %s" % (name, val))
                else:
                    self._respond("  unknown setting: %s" % name)
            else:
                name = parts[1].lower()
                if name in self._settings:
                    d, v = self._settings[name]
                    self._respond("  %s - %s : %s" % (name, d, v))
                else:
                    self._respond("  unknown setting: %s" % name)
        else:
            self._respond("  unknown command - type help")
