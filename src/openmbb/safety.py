"""Blocklist, write whitelist, and validators.

The blocklist is enforced by the transport layer (see transport.Transport),
so no UI path — including the raw-command box — can send a blocked command.
"""

import re

# Commands whose FIRST TOKEN can never be sent. (format/erase cover
# "format eeprom" / "erase eeprom". Bare `eeprom` is a READ on rev 41 -
# "Show EEPROM usage" - so it is NOT a blocked head; `eeprom <args>` is
# refused separately in _line_blocked, keeping the read while blocking writes.)
BLOCKED_COMMANDS = {
    "format", "erase", "settingsrst", "statsrst",
    "eventlogclear", "errorlogclear", "eventlogadd", "errorlogadd",
    "reset", "exit_to_bl", "test", "wdt", "timing", "can", "charger",
    # from the real rev-41 `help` menu (confirmed live 2026-07-10): state-changing
    # / firmware-flashing / bootloader commands that must never be sent.
    "dtc_clear",              # clears stored DTCs
    "force_all_storage_mode", # forces battery modules into storage mode
    "blcmds",                 # bootloader command block
    "burn",                   # bluetooth BMS firmware updater
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

# C3: names CONFIRMED present in the real 2017 FXS rev-41 post-login `set` dump
# (ground truth 020_set.txt). The write whitelist is intentionally generic across
# Gen2 models, so it carries settings this bike never exposes; the UI checks this
# set so it can say "not seen on the verified rev 41" instead of implying those
# entries will appear after login. READONLY_GUARDS are all absent from the rev-41
# `set` dump (they are Sevcon-side / documented values), so none are listed here.
REV41_FXS_SETTINGS = frozenset({
    "spfront", "sprear", "rwhcirc",
    "maxcustspmph", "maxcusttq_allowed", "maxcustregcotq_allow", "maxcustregbrtq_allow",
})


def _v_int_range(lo, hi):
    def check(v):
        s = str(v).strip()
        # D1 (review FID-1): require plain decimal digits. int() also accepts
        # '1_0' (=10), '+22', and Unicode digits, but write_setting puts the RAW
        # string on the wire — so a value that validates as one number here but
        # the console's atoi parses as another (e.g. '1_0' -> 1) would journal a
        # false VERIFIED. All whitelisted int settings are non-negative.
        if not re.fullmatch(r"[0-9]+", s):
            return False, "must be a whole number (digits only)"
        n = int(s)
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
        "installed front sprocket tooth count (FX/FXS factory is 20).",
        "SAFE - display math only. Wrong values skew speed/odo/Wh-per-km.",
        _v_int_range(10, 40), None),
    "sprear": (
        "Sprocket teeth, rear",
        "Input to speedometer/odometer calculation ONLY. Set to the physically "
        "installed rear sprocket tooth count (FX/FXS factory is 90).",
        "SAFE - display math only.",
        _v_int_range(30, 150), None),
    "rwhcirc": (
        "Rear wheel circumference (mm)",
        "Input to speedometer calculation. Trim so the speedo matches GPS; the "
        "right value depends on your tire (typically ~1900-2000 mm).",
        "SAFE - display math only.",
        _v_int_range(1500, 2400), None),
    # Custom-mode keys use the REAL rev-41 setting names (confirmed live 2026-07-10;
    # the bike also exposes maxcustsprpm/maxcustspkph and the maxcust*x10 raw forms).
    "maxcustspmph": (
        "Custom mode: max speed (mph)",
        "Top speed in Custom ride mode (real name: maxcustspmph). Can be LOWERED, "
        "but NOT raised above the factory 89 mph — the console accepts up to 102 and "
        "reports SUCCESS, yet silently clamps the value back to 89 (verified live).",
        "SAFE - factory-supported; raising above 89 is a silent no-op (clamped).",
        _v_int_range(20, 102), None),
    "maxcusttq_allowed": (
        "Custom mode: max torque (% of allowed)",
        "Torque cap in Custom mode, % of what the Sevcon allows (real name: "
        "maxcusttq_allowed).",
        "SAFE - factory-supported parameter space.",
        _v_int_range(10, 100), None),
    "maxcustregcotq_allow": (
        "Custom mode: coast regen (% of allowed)",
        "Off-throttle (coast) regen in Custom mode, % of allowed (real name: "
        "maxcustregcotq_allow). Low values = free coasting for efficiency.",
        "SAFE, but exactly 0 is refused (fishtail risk in low traction).",
        _v_coast_regen, None),
    "maxcustregbrtq_allow": (
        "Custom mode: brake regen (% of allowed)",
        "Regen applied when the brake switch triggers, in Custom mode (real name: "
        "maxcustregbrtq_allow).",
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
        "CAUTION - changes what the gauge withholds, not real capacity. On an "
        "aged pack, don't reduce your reserve margin.",
        _v_yes_no, None),
    "reserve_pct": (
        "Reserve partition size (%)",
        "How much SoC the reserve partition withholds (default 33%).",
        "CAUTION - gauge behavior only, adds zero real Ah.",
        _v_int_range(0, 50), None),
    "fuelgaugepes": (
        "Fuel gauge pessimism (%)",
        "SoC display buffer (default 5%). This only shifts the DISPLAYED "
        "percentage; it doesn't change the actual pack charge.",
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
    "Context: gauge-related settings (reserve, fuel-gauge pessimism) change what "
    "the gauge SHOWS, not real capacity — they add zero real Ah. Change ONE thing "
    "at a time and re-read to verify."
)


class BlockedCommandError(Exception):
    pass


def command_blocked(cmd, allow_write=False):
    """Return a human-readable reason if `cmd` must not be sent, else None.

    Every line is screened, not just the first token of the whole string: an
    embedded CR/LF would put several console lines on the wire, so a pasted
    "status\\nsettingsrst" must not slip past on the strength of its allowed
    first line. (The transport ALSO refuses control characters outright, so a
    real multi-line command never reaches the wire — this is defense in depth
    and gives the raw-box path a specific reason.)

    `allow_write=True` is used ONLY by the gated write path
    (Transport.write_setting): it permits a single validated `set <name> <value>`
    whose name is whitelisted and whose value passes that setting's validator.
    Every ordinary caller leaves it False, so writes can never originate from
    the raw command box.
    """
    text = str(cmd)
    if "\n" in text or "\r" in text:
        for line in text.splitlines():
            reason = _line_blocked(line, allow_write=allow_write)
            if reason:
                return reason
        return ("multi-line input refused - send one command per line "
                "(a pasted newline could smuggle a blocked command).")
    return _line_blocked(text, allow_write=allow_write)


def _line_blocked(cmd, allow_write=False):
    tokens = str(cmd).strip().split()
    if not tokens:
        return None
    head = tokens[0].lower()
    if head in BLOCKED_COMMANDS:
        return "'%s' is on the hard blocklist (destructive/dangerous)." % head
    if head.startswith(BLOCKED_PREFIXES):
        return "'%s' is a safety-interlock override (ov_*) - never sent." % head
    if head == "eeprom" and len(tokens) > 1:
        return ("'eeprom' with arguments can read/modify raw EEPROM - only bare "
                "'eeprom' (the usage summary, a read) is allowed.")
    if head == "sevcon" and len(tokens) > 1 and tokens[1].lower() == "preop":
        return "'sevcon preop' toggles controller pre-op mode (a write) - blocked."
    if head == "bluetooth" and len(tokens) > 1:
        return "'bluetooth' with arguments can modify the BT module - blocked."
    if head == "set" and len(tokens) >= 2:
        name = tokens[1].lower()
        if name in BLOCKED_SETTINGS or name.startswith(BLOCKED_PREFIXES):
            return "setting '%s' is protected (safety guard / identity) - blocked." % name
        # T6: refuse the two-token `set <name>` form entirely. rev-41's no-value
        # behavior is UNVERIFIED and could be a prompt-for-value (i.e. a write),
        # not a read — so it fails closed for EVERY name until a real capture
        # confirms it (HW-evidence-queue item 2). Nothing is lost: the full `set`
        # dump already shows every setting's current value.
        if len(tokens) == 2:
            return ("'set %s' (single-setting view) is refused - the no-value form "
                    "is unverified on rev 41 (could be a prompt-for-value = a write). "
                    "Use the full `set` dump to read a value." % name)
        # Three-or-more-token `set <name> <value>` is a WRITE. Refused from every
        # ordinary path; only Transport.write_setting passes allow_write, and
        # even then the value must be exactly one token and pass the validator.
        if len(tokens) >= 3:
            if not allow_write:
                return ("writing settings from here is refused - use the Writes "
                        "tab, which backs up, confirms, verifies and journals "
                        "every change.")
            if name not in WRITE_WHITELIST:
                return ("setting '%s' is not on the write whitelist - "
                        "writes are whitelist-only." % name)
            if len(tokens) != 3:
                return ("write for '%s' must be a single value (got %d) - "
                        "refused." % (name, len(tokens) - 2))
            validator = WRITE_WHITELIST[name][3]
            ok, msg = validator(tokens[2])
            if not ok:
                return "value for '%s' rejected: %s" % (name, msg)
    return None
