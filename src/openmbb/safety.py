"""Blocklist, write whitelist, and validators.

The blocklist is enforced by the transport layer (see transport.Transport),
so no UI path — including the raw-command box — can send a blocked command.
"""

# Commands whose FIRST TOKEN can never be sent. (format/erase cover
# "format eeprom" / "erase eeprom"; eeprom covers show/dump/set.)
BLOCKED_COMMANDS = {
    "format", "erase", "eeprom", "settingsrst", "statsrst",
    "eventlogclear", "errorlogclear", "eventlogadd", "errorlogadd",
    "reset", "exit_to_bl", "test", "wdt", "timing", "can", "charger",
}
BLOCKED_PREFIXES = ("ov_",)

# Settings that can never be written via `set <name> ...` (safety interlocks,
# thermal thresholds, regen guards, install-time identity).
BLOCKED_SETTINGS = {
    "abs_disable", "bypass_bms", "ignore_iso_err", "inhibit_iso",
    "model", "model_year", "vin", "serial",
    "motstage1", "motstage2", "ctrlstage1", "ctrlstage2",
    "sevmaxdischgcur", "sevnoregspeed", "sevmaxregv", "sevnoregfull",
    "kill", "test_mode", "safe_ov_sw", "req_loopback",
    "bridge_can", "bike_can_en", "sevcon_can_en",
    "prewait", "prechg", "pretimeout", "moddiff", "manmod", "allowdiffmods",
    "rcoscadjust",
}

# Regen/thermal guards to DISPLAY read-only in the Writes tab.
READONLY_GUARDS = [
    ("sevnoregspeed", "Blocks regen above ~70 mph (over-current / over-charge guard)"),
    ("sevmaxregv", "Cell-voltage regen cutoff (4160 mV = overcharge guard)"),
    ("sevnoregfull", "Blocks regen at 100% SoC"),
    ("motstage1", "Motor temp warning threshold (100 C)"),
    ("motstage2", "Motor temp cutback/shutdown threshold (145 C)"),
    ("ctrlstage1", "Controller temp warning threshold"),
    ("ctrlstage2", "Controller temp cutback threshold"),
    ("sevmaxdischgcur", "Sevcon max battery discharge amps"),
]


def _v_int_range(lo, hi):
    def check(v):
        try:
            n = int(str(v).strip())
        except ValueError:
            return False, "must be a whole number"
        if not (lo <= n <= hi):
            return False, "must be between %d and %d" % (lo, hi)
        return True, ""
    return check


def _v_coast_regen(v):
    ok, msg = _v_int_range(0, 100)(v)
    if not ok:
        return ok, msg
    if int(str(v).strip()) == 0:
        return False, ("exactly 0%% coast regen is refused: 0%% coasting regen "
                       "can cause fishtailing in low traction. Use a low nonzero value.")
    return True, ""


def _v_yes_no(v):
    if str(v).strip().lower() in ("yes", "no", "on", "off", "1", "0"):
        return True, ""
    return False, "must be Yes or No"


# name -> (label, effect text, risk text, validator, warn_fn or None)
WRITE_WHITELIST = {
    "spfront": (
        "Sprocket teeth, front",
        "Input to speedometer/odometer calculation ONLY. Set to the physically "
        "installed tooth count (stock 20; belt re-gear 22; chain fallback 14).",
        "SAFE - display math only. Wrong values skew speed/odo/Wh-per-km.",
        _v_int_range(10, 40), None),
    "sprear": (
        "Sprocket teeth, rear",
        "Input to speedometer/odometer calculation ONLY. Set to the physically "
        "installed tooth count (stock 90; belt re-gear 88; chain fallback 56).",
        "SAFE - display math only.",
        _v_int_range(30, 150), None),
    "rwhcirc": (
        "Rear wheel circumference (mm)",
        "Input to speedometer calculation. Trim so speedo matches GPS "
        "(effective value from ride logs was ~1966 mm).",
        "SAFE - display math only.",
        _v_int_range(1500, 2400), None),
    "maxcustsp": (
        "Custom mode: max speed (mph)",
        "Top speed in Custom ride mode. Same knob the Zero app writes. "
        "Bike's physical ceiling is 85 mph; console accepts up to 102.",
        "SAFE - factory-supported parameter space.",
        _v_int_range(20, 102), None),
    "maxcusttq": (
        "Custom mode: max torque (%)",
        "Torque cap in Custom mode, % of what the Sevcon allows.",
        "SAFE - factory-supported parameter space.",
        _v_int_range(10, 100), None),
    "maxcustregcotq": (
        "Custom mode: coast regen (%)",
        "Off-throttle (coast) regen in Custom mode. Low values = free coasting "
        "for efficiency.",
        "SAFE, but exactly 0 is refused (fishtail risk in low traction).",
        _v_coast_regen, None),
    "maxcustregbrtq": (
        "Custom mode: brake regen (%)",
        "Regen applied when the brake switch triggers, in Custom mode.",
        "SAFE - factory-supported parameter space.",
        _v_int_range(0, 100), None),
    "brakeregen": (
        "Apply regen on brake switch",
        "Whether the brake light switch applies max brake regen.",
        "CAUTION - modest changes only; affects brake feel.",
        _v_yes_no, None),
    "brakefilter": (
        "Brake switch filter (ms)",
        "Delay after brake switch closes before light + regen trigger.",
        "CAUTION - keep near default (100 ms).",
        _v_int_range(0, 1000), None),
    "noregenstopped": (
        "Prevent regen at low speed",
        "Blocks regen below ~13 mph.",
        "CAUTION - safe-direction only: leave enabled (Yes). Disabling removes "
        "a low-speed stability guard.",
        _v_yes_no,
        lambda v: ("Setting this to 'No' removes the low-speed regen guard. "
                   "Strongly consider leaving it Yes."
                   if str(v).strip().lower() in ("no", "off", "0") else None)),
    "reserve_sw": (
        "Use SOC reserve partition",
        "Treats part of the battery as a 'fuel reserve' partition.",
        "CAUTION - changes what the gauge withholds, not real capacity. Pack is "
        "~65-70% SoH; don't reduce your margin.",
        _v_yes_no, None),
    "reserve_pct": (
        "Reserve partition size (%)",
        "How much SoC the reserve partition withholds (default 33%).",
        "CAUTION - gauge behavior only, adds zero real Ah.",
        _v_int_range(0, 50), None),
    "fuelgaugepes": (
        "Fuel gauge pessimism (%)",
        "SoC display buffer (default 5%). Interacts with the 2026-06-13 "
        "firmware gauge rescale (~1.55x steeper, learned capacity ~34 Ah).",
        "CAUTION - reducing it removes low-SOC margin on an aged pack.",
        _v_int_range(0, 20),
        lambda v: ("Lowering pessimism removes displayed-SOC safety margin."
                   if str(v).strip().isdigit() and int(v) < 5 else None)),
    "chgstby": (
        "Charge standby time (min)",
        "How long the BMS sleeps after reaching 100% before waking to "
        "rebalance cells (default 1440 = 24 h).",
        "CAUTION - affects balancing cadence; the default is usually right.",
        _v_int_range(60, 10080), None),
}

WRITE_PANEL_CONTEXT = (
    "Context: the 2026-06-13 firmware update rescaled the SOC gauge (~1.55x "
    "steeper than before; learned usable capacity ~34 Ah). Gauge-related "
    "settings interact with that. Change ONE thing at a time."
)


class BlockedCommandError(Exception):
    pass


def command_blocked(cmd):
    """Return a human-readable reason if `cmd` must not be sent, else None."""
    tokens = str(cmd).strip().split()
    if not tokens:
        return None
    head = tokens[0].lower()
    if head in BLOCKED_COMMANDS:
        return "'%s' is on the hard blocklist (destructive/dangerous)." % head
    if head.startswith(BLOCKED_PREFIXES):
        return "'%s' is a safety-interlock override (ov_*) - never sent." % head
    if head == "sevcon" and len(tokens) > 1 and tokens[1].lower() == "preop":
        return "'sevcon preop' toggles controller pre-op mode (a write) - blocked."
    if head == "bluetooth" and len(tokens) > 1:
        return "'bluetooth' with arguments can modify the BT module - blocked."
    if head == "set" and len(tokens) >= 2:
        name = tokens[1].lower()
        if name in BLOCKED_SETTINGS or name.startswith(BLOCKED_PREFIXES):
            return "setting '%s' is protected (safety guard / identity) - blocked." % name
        if len(tokens) >= 3 and name not in WRITE_WHITELIST:
            return ("setting '%s' is not on the write whitelist - "
                    "writes are whitelist-only." % name)
    return None
