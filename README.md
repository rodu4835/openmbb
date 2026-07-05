# Zero Console

Phase-gated GUI for the 2017 Zero FXS **MBB serial console** (OBD-II / C3 port).
Windows-11-native look (Sun Valley theme), read-first, whitelist-only writes.

> Personal diagnostic tool for **your own** vehicle. Not affiliated with Zero
> Motorcycles. No warranty — see LICENSE. Changing settings may affect your
> vehicle warranty; you are responsible for what you write to your bike.

## Install

```
git clone <your-private-repo-url> zero-console
cd zero-console
pip install .            # or:  pip install -e .   for a dev/editable install
```

Then run:

```
zero-console             # real serial (auto-lists COM ports)
zero-console --sim       # simulator — click through everything at the desk
zero-console --port COM4 # preselect a port
zero-console --selftest  # headless transport/safety tests
zero-console --smoketest # build the GUI once, sim-connect, exit
```

Requires Python 3.9+. Dependencies (`pyserial`, `sv-ttk`) install automatically.
If `sv-ttk` is ever unavailable the app falls back to a built-in dark theme.

> If the `zero-console` command isn't found after install, your Python
> `Scripts` dir isn't on PATH — use `python -m zero_console.cli` (same flags).

## Wiring (FTDI TTL-232R-3V3 → OBD-II J1962, port under the seat)

| FTDI wire | OBD pin | Pigtail wire | Role |
|---|---|---|---|
| Black (GND) | 5 | Teal | Diagnostic ground |
| Yellow (RXD) | 8 | Black/White | bike MBB **Tx** |
| Orange (TXD) | 9 | Red/White | bike MBB **Rx** |
| **Red (+5 V)** | — | — | **NEVER connected** |

38400 baud, 8-N-1, no flow control, newline CR-LF. Bike PARKED, key ON,
kill switch OFF. Never stream the console while riding.

## Phase flow

0. **Connect** — pick port, probe for the `ZERO MBB>` prompt, capture `version`.
1. **Read** — per-command buttons; **FULL BASELINE** captures everything incl.
   the settings dump (your backup) and `dumplogs` (~1 MB, progress shown).
   Unlocks Login. *You can stop here — reads are the whole point of day one.*
2. **Login** — explicit; tries `tpsreport` then `wideopenthrottle`. Both failing
   is a valid outcome (tool stays read-only). On success: re-captures help +
   settings, diffs the logged-in menu.
3. **Writes** — locked behind login + master unlock toggle + per-write confirm.
   Rows appear only for settings that are BOTH on the whitelist AND present in
   the live dump. Every write: re-read current → confirm old→new with
   effect/risk → auto-backup full settings → send → read-back verify → journal
   (per-entry revert).

## Safety model

- **Hard blocklist in the transport layer** (every UI path incl. the raw box):
  `format/erase/eeprom`, `settingsrst`, `statsrst`, log clears/adds, `reset`,
  `exit_to_bl`, `test`, `wdt`, `timing`, `can`, `charger`, `sevcon preop`, and
  `set` of any protected name (`abs_disable`, `bypass_bms`, `ov_*`,
  `motstage*/ctrlstage*`, `sevnoregspeed/sevmaxregv/sevnoregfull`,
  `model/vin/serial`, …).
- Regen/thermal guards are shown **read-only** in the Writes tab.
- Validators: coast regen of exactly 0 is refused (fishtail risk);
  `noregenstopped No` warns; range limits on every numeric.

## Package layout

```
src/zero_console/
  __init__.py    version + module docstring
  safety.py      blocklist, write whitelist, validators
  transport.py   serial transport, session logging, settings parser
  sim.py         fake serial port (real MBB transcripts, adapted to this bike)
  theme.py       Sun Valley (Win11) theme with dark fallback
  gui.py         the four phase-gated Tkinter panels
  cli.py         entry point + --selftest / --smoketest
tests/           pytest suite (safety/transport headless + GUI flow)
```

## Session output

Everything lands in `<save-base>/zero-console-sessions/<timestamp>_<port>/`:
`session_raw.log` (every byte, timestamped), one file per command,
`settings_baseline_*.txt`, `settings_backup_*.txt` (auto, pre-write),
`writes_journal.txt`.

**Where it saves:** by default the `<save-base>` is the current working
directory. Change it via **Session → Set save location…** (remembered across
runs in `~/.zero-console/config.json`) or per-launch with `--logdir <path>`.
The current save target is shown in the top status strip and is clickable.

## First real session checklist

1. Meter check: FTDI Orange idles ~3.3 V vs Black; Red taped off.
2. Connect & probe (Phase 0). Garbage at 38400 ⇒ suspect Tx/Rx swap — stop.
3. FULL BASELINE before anything else. Firmware rev 41 is newer than all
   community docs — the captured `help`/`set` output is the ground truth; the
   simulator's menus are 2014-era transcripts adapted to this bike.
4. Login attempt only after the baseline is on disk.
5. No writes on day one. Bring the session folder home first.
