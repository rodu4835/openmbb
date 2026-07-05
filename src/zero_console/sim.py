"""Simulator: a fake serial port replaying real MBB console transcripts.

Fixtures come from the Unofficial Zero Manual wiki mirror (2014 SR / 2016 DSR
captures), adapted to this 2017 FXS: 20/90 sprockets, ZF6.5 2-brick pack,
Calex 720W+1200W chargers, MBB rev 41 / BMS rev 48 (post 2026-06-13 update).
The REAL rev-41 output may differ — the live capture is always ground truth.
"""

import queue
import re

SIM_SETTINGS = {
    # name: (desc, value)  -- value strings mimic real dump formatting
    "model":          ("Model Name", "FXS"),
    "model_year":     ("Model Year", "2017"),
    "vin":            ("VIN Number", "ZEROSIMVIN0000000"),
    "serial":         ("Serial Number", "SIM-MBB-0000"),
    "runtime":        ("Total Run Time", "00013:05:40:10"),
    "secidle":        ("Seconds Idle Before Turn Off", "7200"),
    "motstage1":      ("Motor Temp Stage1", "100 C"),
    "motstage2":      ("Motor Temp Stage2", "145 C"),
    "ctrlstage1":     ("Controller Temp Stage1", "70 C"),
    "ctrlstage2":     ("Controller Temp Stage2", "75 C"),
    "spfront":        ("Sprocket Teeth Front", "20"),
    "sprear":         ("Sprocket Teeth Rear", "90"),
    "rwhcirc":        ("Rear Wheel Circum", "1966 mm"),
    "kill":           ("Kill Switch Polarity", "closedrun (1)"),
    "zeroneutral":    ("Neutral When Off Throt", "Yes"),
    "zerothresh":     ("Throttle Off Threshold", "750 mV"),
    "brakeregen":     ("Apply Regen On Brake", "Yes"),
    "brakefilter":    ("Brake Switch Filter", "100 ms"),
    "noregenstopped": ("Prevent Regen When Stopped", "Yes"),
    "fuelgaugepes":   ("Fuel Gauge Pessimism", "5%"),
    "sevnoregspeed":  ("Prevent Regen At High Speed", "Yes"),
    "sevmaxregrpm":   ("Max Regen RPM", "4500 rpm"),
    "sevnoregfull":   ("Prevent Regen When Full", "Yes"),
    "sevmaxregv":     ("Max Regen Cell Voltage", "4160 mV"),
    "sevmaxdischgcur": ("Sevcon Max Batt Dischg Amps", "520 A"),
    "ov_kickstand":   ("Override Kickstand Disable", "No"),
    "ov_mot_temp":    ("Override Mot Temp Disable", "No"),
    "ov_cont_temp":   ("Override Ctrl Temp Disable", "No"),
    "ov_batt_temp":   ("Override Batt Temp Disable", "No"),
    "ov_low_batt":    ("Override Low Batt Disable", "No"),
    "bypass_bms":     ("Bypass BMS interface", "No"),
    "ignore_iso_err": ("Ignore isolation error", "No"),
    "debug_level":    ("Debug Level", "1"),
    "reserve_sw":     ("Use a reserve partition", "No"),
    "reserve_pct":    ("Reserve partition", "33%"),
    "logrun":         ("Log freq while riding", "3000 ms"),
    "logchg":         ("Log freq while charging", "600000 ms"),
    "chgstby":        ("Charge Standby Time", "1440 min"),
    "maxcustsp":      ("Max Custom Speed", "85 MPH ( 137 KPH ) ( 5385 RPM )"),
    "maxcusttq":      ("Max Custom Torque", "100 ( 100 % of allowed )"),
    "maxcustregcotq": ("Max Custom Regen Coast Torque", "6 (  60 % of allowed )"),
    "maxcustregbrtq": ("Max Custom Regen Brake Torque", "80 ( 80 % of allowed )"),
    "test_mode":      ("Test Mode", "Off"),
    "abs_disable":    ("Disable ABS via terminal", "Off"),
}

SIM_VERSION = """\
*
*                                                           *
*                  Zero Motorcycles MBB                     *
*                                                           *
*                 Board Name : MBB PCB ASSY 17MY IMPL       *
*                   Board PN : 41-08072                     *
*                  Board Rev : 07                           *
*              Firmware Name : FIRMWARE MBB 17MY IMPL       *
*                Firmware PN : 75-08103                     *
*               Firmware Rev : 41                           *
*                      Built : Nov 17 2024 14:09:05         *
*"""

SIM_HELP_LOGGED_OUT = """\
*
*                        Main Menu                          *
*

  help            - Display this Help screen
  login           - Show login level, or login with password
  logout          - Log out of MBB
  version         - Display board and firmware revision
  status          - Show the status of the Main Bike Board
  stats           - Display All statistics
  runtime         - Show Total Run Time
  set             - Show all settings, or edit a specific setting
  bms             - Display BMS data
  bluetooth       - Display/modify Bluetooth connection
  sevcon          - Display Sevcon Motor Controller Data
  chargers        - Show info for all charger ports
  inputs          - Show all inputs
  outputs         - Show all outputs
  dash            - Show all dash info
  eventlogdump    - Display the contents of the event log
  errorlogdump    - Display the contents of the error log
  dumpall         - Dump all data (stats, inputs, settings, and logs"""

SIM_HELP_LOGGED_IN = SIM_HELP_LOGGED_OUT + """
  statsrst        - Reset All statistics
  settingsrst     - Reset all settings to defaults
  sevcon faults   - Display all Sevcon Faults
  sevcon preop    - Toggles Preop Mode
  charger         - Set parameters for all charger ports
  eventlogclear   - Clears the Event log  Destroys all existing log entries.
  eventlogadd     - Adds an arbitrary string to the Event Log
  errorlogclear   - Clears the Event log  Destroys all existing log entries.
  errorlogadd     - Adds an arbitrary string to the Error Log
  eeprom          - Show EEPROM usage
  eeprom dump x y - Dump <y> bytes of EEPROM starting at addr <x>
  eeprom set x y  - Set a byte in Eeprom at address x to y
  format eeprom   - Reset All To Factory Defaults (Reformat EEPROM)
  erase eeprom    - Erase entire EEPROM
  reset           - Reset CPU with SW reset
  exit_to_bl      - Exit main app and start bootloader
  timing          - Display system timing
  can             - Display CANbus information
  wdt reset       - Force Watchdog Timeout
  test            - Run Specific Test, or show test options menu"""

SIM_STATUS = """\
*
*                        Bike Status                        *
*

***  Bike
   - Mode                   : Standby
   - OB Charger 0 Attached : No
   - OB Charger 1 Attached : No

***  Motor Controller
   - On                     :  No
   - CAN connected          : Yes
   - In Operational Mode    : Yes
   - Drive Mode             : Custom
   - Motor Temp             :  24C - Normal
   - Controller Temp        :  22C - Normal
   - Number of Faults       :   0

***  Batteries
   - Num Modules in System  :   1
   - Num Registered Modules :   1
   - Total SOC              :  61 %
   - Pessimistic SOC        :  56 %
   - Total Capacity         :  52 AH
   - Remaining Capacity     :  32 AH

***  Info Messages
 - INFO    : Kill switch is in OFF position
 - INFO    : Kickstand switch is in DOWN position

***  Warning Messages
 - No Warnings

***  Error Messages
 - No Errors"""

SIM_STATS = """\

*               Statistics             *

  - Board Revision            :  07
  - Firmware Revision         :  41

  - Num Resets                :  412
  - Num Watchdog Resets       :  1
  - Num Abnormal Resets       :  0

  - System Time               :  07/05/2026 10:12:33
  - Total run time            :  00007:19:30:25
  - Total charger time        :  00003:01:23:41

  - Max Battery Temp          :  60 C
  - Max Motor Temp            :  118 C
  - Max Controller Temp       :  52 C

  - Lifetime Watt Hours Per Km : 64.80 WH/km (25.72 mi/kWh)

  - Odometer                  :  14100033 motor rev
                              :  6155 km
                              :  3825 miles

  - Top Speed                 :  137 KPH
                              :  85 MPH
  - Max Motor Speed           :  5449 RPM"""

SIM_BMS = """\

*               BMS Data               *

*  BMS #0  ***
  - BMS Board Rev             :  17
  - BMS Firmware Rev          :  48
  - BMS Serial Number         : 2017_bms_redacted
  - CAN Rx in last second     :   55

  - Pack SOC                  :  61%
  - Fuel Gauge                :  56%

  - Model Year                : 2017
  - Pack Capacity             :  52 AH
  - Pack Capacity Remaining   :  32 AH
  - Number Of Bricks          :   2

  - Pack Discharge Current    :   0 A
  - Pack Sum Voltage          :  106.412 V
  - Lowest Cell Voltage       :  3798 mV ( Cell 12 )
  - Highest Cell Voltage      :  3810 mV ( Cell 7 )
  - Pack Balance              :  12 mV

  - Isolation Resistance      :  10486 KOhms (0x28F6)

                              :      0     1     2     3
  - Pack Temps                :    24C   24C   23C   23C
  - Max Pack Temp This Ride   :  24 C

  - Max Charge Temp           :  50 C
  - Num Charge Cycles         :  512

  CAN Status
  - Charger Attached          :   No
  - Batt Unbalanced           :   No
  - Isolation Fault           :   No
  - BMS Internal Fault        :   No"""

SIM_SEVCON = """\

*             Sevcon Data              *

  Received PDOs
  - Motor speed               :     0 RPM
  - Motor Temp                :    24 C
  - Max Motor Temp This Ride  :    24 C
  - Controller Temp           :    22 C
  - Battery Voltage           :   106.250 V

  Transmitted PDOs
  --Drive Control--
  - Max Batt Chg Current      :   -40 A
  - Max Batt Dishg Current    :   520 A

  - Sevcon Firmware Rev       : 0712.0002
  - In Operational Mode       : Yes

  Active Sevcon Faults
  - Number of Faults          :    0"""

SIM_CHARGERS = """\

*             Charger Data             *

***  Charger #0  *
  - Charger Name              : Calex 720W
  - Attached                  : No
  - Enabled                   : No

***  Charger #1  *
  - Charger Name              : Calex 1200W
  - Attached                  : No
  - Enabled                   : No

  - Charge Current Is Flowing          : No"""

SIM_INPUTS = """\
*
*                      All Measurements                     *
*

  - Key On                    :       Yes  - Raw : 1 (1 at last read)
  - Pack Voltage              :  106350 mV (932 ADC)
  - 3.3V Supply               :  3298 mV (561 ADC)
  - 5V Supply                 :  5011 mV (856 ADC)
  - Kill Switch Pos           :      Stop  - Raw : 0 mV (0 ADC)
  - Kickstand Switch Pos      :      Down  - Raw : 2997 mV (1023 ADC)
  - Brake Switch              :       Off
  - Board Temp                :  26 C  (561 ADC)
  - Ambient Temp              :  19 C  (655 ADC)"""

SIM_OUTPUTS = """\

*             All Outputs              *

  - System On                 :   On
  - DC/DC Converter En        :  Off
  - Warning Light             :  Off
  - Temp Warning LED          :  Off
  - Charge LED                :  Off
  - Armed LED                 :  Off
  - Sevcon Controller En      :  Off
  - Sevcon Custom Mode        :   On"""

SIM_DASH = """\
*
*                        Dash Info                          *
*

  - Status Data               :  0x 00 00 16 22 05 FA 02 02
  - Clock (24H)               :  10:12
  - Odometer                  :  006155.2 Km
  - Time since Dash CAN RX    :    54 ms"""

SIM_RUNTIME = """\

*                Run Time              *

  - Total run time            :  00007:19:30:25
  - Total charger time        :  00003:01:23:41"""

SIM_EVENTLOG = "\n".join(
    ["  Event Log (most recent last)"] +
    ["  %05d 07/0%d/2026 0%d:1%d:0%d Riding  PackTemp: 2%dC, PackSOC: %d%%, Vpack:10%d.%03dV, MotAmps: %3d, MotRPM:%4d, Odo:61%02dkm"
     % (7000 + i, 1 + i % 4, 8 + i % 3, i % 6, i % 10, i % 4, 90 - i * 3,
        6 - i % 3, 100 + i * 37, 60 + i * 9, 3100 + i * 60, 20 + i)
     for i in range(12)])

SIM_ERRORLOG = """\
  Error Log (most recent last)
  00001 06/13/2026 06:53:01 WDT Timeout (post-flash first boot)
  00002 06/13/2026 12:36:13 WARNING: Current sensor calibration has not been set!"""

PW_ACCEPTED = "tpsreport"


class SimPort:
    """Fake serial port replaying fixtures. Interface-compatible with the
    subset of pyserial that Transport uses: read/write/close/in_waiting."""

    def __init__(self, greet=True):
        self._rx = queue.Queue()
        self._linebuf = b""
        self._settings = {k: [d, v] for k, (d, v) in SIM_SETTINGS.items()}
        self.logged_in = False
        self.is_sim = True
        if greet:
            self._push(b"\r\nZERO MBB> ")

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
        self._push(text.encode() + b"\r\n\r\nZERO MBB> ")

    def _settings_dump(self):
        lines = ["*", "*                   System Settings Menu                    *", "*", ""]
        for name, (desc, value) in self._settings.items():
            pad = " " * max(1, 16 - len(name))
            if name in ("model", "vin", "serial", "model_year", "runtime"):
                lines.append("  %s%s- %-28s %s" % (name, pad, desc, value))
            else:
                lines.append("  %s%s- %-28s: %s" % (name, pad, desc, value))
        return "\n".join(lines)

    def _handle(self, cmd):
        if not cmd:
            self._push(b"\r\nZERO MBB> ")
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
                " %05d 0%d/1%d/2026 0%d:%02d:%02d Riding  PackTemp: %dC, PackSOC: %d%%,"
                " Vpack: 10%d.%03dV, MotAmps: %3d, MotRPM: %4d, Odo: 6%03dkm"
                % (i, 5 + (i % 2), i % 10, 7 + i % 12, i % 60, (i * 7) % 60,
                   22 + i % 30, 100 - (i % 80), 6 - i % 5, i * 13 % 1000,
                   40 + i % 220, 2400 + (i * 17) % 3000, 100 + i % 100)
                for i in range(2600))
            self._respond("  Printing 2600 of 2600 log entries..\n" + big)
        elif head == "login":
            if len(parts) == 1:
                self._respond("  Login level: %d" % (2 if self.logged_in else 0))
            elif parts[1] == PW_ACCEPTED:
                self.logged_in = True
                self._respond("  Logged in at level 2")
            else:
                self._respond("  Login failed")
        elif head == "logout":
            self.logged_in = False
            self._respond("  Logged out")
        elif head == "bluetooth":
            self._respond("\n*            Bluetooth Data            *\n\n  - Connected : No")
        elif head == "set":
            if len(parts) == 1:
                self._respond(self._settings_dump())
            elif len(parts) >= 3:
                name = parts[1].lower()
                if not self.logged_in:
                    self._respond("  Access denied. Please login.")
                elif name in self._settings:
                    unit = re.search(r"[A-Za-z%]+\s*$", self._settings[name][1])
                    val = parts[2]
                    if unit and not re.search(r"[A-Za-z%]", val):
                        val = "%s %s" % (val, unit.group(0).strip())
                    self._settings[name][1] = val
                    self._respond("  %s set to %s" % (name, val))
                else:
                    self._respond("  Unknown setting: %s" % name)
            else:
                name = parts[1].lower()
                if name in self._settings:
                    d, v = self._settings[name]
                    self._respond("  %s - %s : %s" % (name, d, v))
                else:
                    self._respond("  Unknown setting: %s" % name)
        else:
            self._respond("  Unknown command. Type help for command list.")
