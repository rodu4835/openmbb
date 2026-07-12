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
    "firmware_rev":   ("Firmware Revision", "41"),
    "board_id":       ("Board ID", "7"),
    "region":         ("Brake behavior region", "0x00"),
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
    # D5: the real 2017 FXS rev-41 post-login `set` (ground truth 020_set.txt) also
    # exposes drive_mode, is_dnr_board, and the custom-mode RPM/KPH + x10 twins that
    # sit alongside the mph/_allow forms. Include them (with the real values) so a
    # --sim rehearsal shows the same custom-mode rows the owner sees on the bike.
    "drive_mode":     ("Pres Drv Mode 123 norm 456 overide", "1"),
    "maxcustsprpm":         ("Max Custom Speed", "5445 RPM"),
    "maxcustspmph":         ("Max Custom Speed", "89 MPH"),
    "maxcustspkph":         ("Max Custom Speed", "143 KPH"),
    "maxcusttqx10":         ("Max Custom Torque x10", "1000 %x10"),
    "maxcusttq_allowed":    ("Max Custom Torque Percent of Allowed", "100 %"),
    "maxcustregcotqx10":    ("Max Custom Regen Coast Torque x10", "50 %x10"),
    "maxcustregcotq_allow": ("Max Cust Regen Coast Torque % of Allowed", "55 %"),
    "maxcustregbrtqx10":    ("Max Custom Regen Brake Torque x10", "70 %x10"),
    "maxcustregbrtq_allow": ("Max Cust Regen Brake Torque % of Allowed", "77 %"),
    "is_dnr_board":   ("Is D&R board", "0"),
    # NOTE: many entries above (motstage*, ctrlstage*, sev*, ov_*, brake*, reserve*,
    # fuelgaugepes, chgstby, logrun/logchg, zero*, debug_level) are SYNTHETIC — they
    # are NOT in the real rev-41 `set` dump. They exercise the parser/whitelist paths
    # but do not imply the bike exposes them (see safety.REV41_FXS_SETTINGS).
    "test_mode":      ("Diagnostic test mode", "Off"),
    "abs_disable":    ("Disable ABS via console", "Off"),
    # T9: exercise the T8/C1 columnar value extraction through the sim itself.
    # simwideval has BOTH a 2+-space DESCRIPTION (which the v0.10.1 regex
    # truncates, leaking the tail into the value) and a value with an internal
    # 2+-space run (which must stay whole); simblankval has a blank value (must
    # parse as "", never the description).
    "simwideval":     ("Synthetic  wide  value", "85 MPH  ( 137 KPH )"),
    "simblankval":    ("Synthetic blank value", ""),
}

# On the real rev-41 bike, `set` at login level 0 shows ONLY these identity
# items — the tunables (spfront/sprear/regen/...) are login-gated. The sim
# mirrors that so --sim rehearses the actual first-session sequence.
LEVEL0_NAMES = ["model_year", "serial", "vin", "firmware_rev", "board_id",
                "model", "region"]

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
  obd             - show all obd info
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
  Odometer (motor)    : 14088220 rev
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

SIM_OBD = """\
== OBD Info (synthetic) ==
  Protocol            : ISO 15765-4 (CAN 11/500)
  VIN                 : ZEROSIMVIN0000000
  Calibration ID      : SIM-CAL-0000
  Stored DTCs         : 0
  Pending DTCs        : 0
  MIL                 : Off"""

SIM_EVENTLOG = "\n".join(
    ["== Event Log (synthetic, most recent last) =="] +
    ["  %05d 07/0%d/2026 0%d:1%d:0%d Riding PackTemp: h %dC, l %dC, PackSOC: %d%%, "
     "Vpack: 10%d.%03dV, MotAmps: %d, MotRPM: %d, MotTemp: %dC, Odo: 61%02dkm"
     % (7000 + i, 1 + i % 4, 8 + i % 3, i % 6, i % 10, 24 + i % 4, 22 + i % 4,
        90 - i * 3, 6 - i % 3, 100 + i * 37, 60 + i * 9, 3100 + i * 60,
        30 + i * 2, 20 + i)
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

    def __init__(self, greet=True, level0_gated=True):
        self._rx = queue.Queue()
        self._linebuf = b""
        self._settings = {k: [d, v] for k, (d, v) in SIM_SETTINGS.items()}
        self.logged_in = False
        self.level0_gated = level0_gated   # hide tunables until login (like rev 41)
        self.is_sim = True
        self.written = b""        # every byte the tool has sent (test aid)
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
        self.written += data
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

    def _col_row(self, name, desc, value):
        """A rev-41-geometry data row: name in [0:21], desc left-aligned from 21,
        value RIGHT-aligned ending at col 73 when it fits (so a wide value like a
        VIN overflows LEFT into the desc column, exactly like the real bike and
        the case T8's parser must survive) — but clamped to keep a 2-space gap
        after the desc, so a long tunable value stays whole instead of colliding."""
        left = (" " + name).ljust(21) + " " + desc
        start = max(73 - len(value), len(left) + 2)
        return left.ljust(start) + value

    def _settings_dump(self, names=None):
        """Render settings in the rev-41 COLUMNAR table format (a +---+---+ ruler,
        then right-aligned fixed-width rows) so --sim exercises the real parser
        path — including the VIN-style left overflow. `names` restricts which
        settings appear (identity-only at level 0). NOTE: a 3-column ruler is used
        (value column runs to end-of-line) so a long tunable value is never cut at
        a units-column boundary; the parser is proven against the real 4-column
        narrow-column geometry directly in tests/test_rev41_fixture.py."""
        names = list(self._settings.keys()) if names is None else names
        ruler = "+" + "-" * 20 + "+" + "-" * 39 + "+" + "-" * 45
        pruler = " +" + "-" * 15 + "+" + "-" * 24 + "+" + "-" * 20
        rt = self._settings.get("runtime", ["Total Run Time", "00000:00:00:00"])[1]
        out = [
            "*************************************************************",
            "*                       MBB Settings                        *",
            "*************************************************************",
            "  To change settings, type:",
            '    "set <setting name> <value1> <value2> ..."',
            "     0x preceding the value may be used to indicate a hex number",
            "",
            " ************",
            " Psudo Settings",           # (verbatim firmware typo)
            " ************",
            "%-17s%-25s%s" % ("  Variable", " Description", "Value"),
            pruler,
            "",
            "%-17s%-25s  %s" % ("  runtime", " Total Run Time", rt),
            "",
            " **************",
            " Settings",
            " **************",
            " NV writes: 4242",
            "",
            "%-21s%-39s  %s" % (" Setting Name", " Setting Desc", "Value"),
            ruler,
        ]
        for name in names:
            if name == "runtime" or name not in self._settings:
                continue
            desc, value = self._settings[name]
            out.append(self._col_row(name, desc, value))
        return "\n".join(out)

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
        elif head == "obd":
            self._respond(SIM_OBD)
        elif head == "eeprom":
            # rev-41: bare `eeprom` is a READ ("Show EEPROM usage"), but it is only
            # listed in the LOGGED-IN menu (019_help.txt), not at level 0
            # (003_help.txt). D5: mirror that — at level 0 it's an unknown command.
            # (Args / format / erase eeprom are blocked upstream and never reach here.)
            if not self.logged_in:
                self._respond("  unknown command - type help")
            else:
                self._respond("== EEPROM Usage (synthetic) ==\n"
                              "  Used                : 5773 / 65536 bytes\n"
                              "  NV writes           : 5773")
        elif head == "dumplogs":
            # faithful to the real rev-41 bike: `dumplogs` is not a command
            self._respond("Sorry, 'dumplogs' is an invalid command. Type \"help\" "
                          "for a list of commands")
        elif head == "eventlogdump":
            self._respond(SIM_EVENTLOG)
        elif head == "errorlogdump":
            self._respond(SIM_ERRORLOG)
        elif head == "dumpall":
            # a heavy dump: synthetic ride-log-shaped lines (eventlogdump decoded
            # field keys: PackTemp h/l, MotTemp, PackSOC, MotRPM, Odo)
            big = "\n".join(
                " %05d 05/%02d/2026 %02d:%02d:%02d Riding PackTemp: h %dC, l %dC, "
                "PackSOC: %d%%, Vpack: 10%d.%03dV, MotAmps: %d, MotRPM: %d, "
                "MotTemp: %dC, Odo: 6%03dkm"
                % (i, 1 + i % 28, 8 + i % 12, i % 60, (i * 7) % 60,
                   24 + i % 8, 22 + i % 6, 100 - (i % 80), 6 - i % 5, i * 13 % 1000,
                   40 + i % 220, 2400 + (i * 17) % 3000, 30 + i % 60, 100 + i % 100)
                for i in range(2600))
            self._respond("== Log Dump (synthetic, 2600 entries) ==\n" + big)
        elif head == "login":
            if len(parts) == 1:
                # ground-truth phrasing (capital L, colon): "Login Level: N"
                self._respond("Login Level: %d" % (2 if self.logged_in else 0))
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
                # rev-41 gating: identity only until logged in
                if self.level0_gated and not self.logged_in:
                    self._respond(self._settings_dump(LEVEL0_NAMES))
                else:
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
