# OpenMBB

Phase-gated GUI for the **MBB serial console** on Gen2 Zero electric
motorcycles (OBD-II / C3 port). Windows-11-native look (Sun Valley theme),
read-first, whitelist-only writes.

> Personal diagnostic tool for **your own** vehicle. Not affiliated with Zero
> Motorcycles. No warranty — see LICENSE. Changing settings may affect your
> vehicle warranty; you are responsible for what you write to your bike.

## Compatibility

OpenMBB talks to the **MBB (Main Bike Board) console** at 38400 baud with the
Gen2 command set (`spfront`, `maxcustsp`, `bms`, …). That pins the scope:

| | |
|---|---|
| **Verified** | 2017 Zero FXS (MBB firmware rev 41) |
| **Should work (same console, unverified)** | Gen2 MBB Zeros, ~2013–2019: **S, SR, DS, DSR, FX, FXS, FXE**. The write panel auto-adapts — it only shows whitelisted settings that appear in *that* bike's live dump. |
| **Not compatible as-is** | Pre-2013 **Gen1** (9600 baud) and 2020+ **Cypher III / SR-F generation** (SR/F, SR/S, DSR/X, newer S/DS) — different baud *and* a different console language. |

The console baud is fixed at 38400 (the Gen2 rate). Gen1/Gen3 would need both a
different baud and a different command set, so OpenMBB is a Gen2 tool by design.

Runs on **Windows and Linux** (and macOS from source).

## Download — single-file executable (no Python needed)

Grab the binary for your OS from the repo's **Releases** page — one self-contained
file that bundles Python, Tk, and all dependencies:

- **Windows:** `openmbb-windows-x64.exe` — double-click to run.
- **Linux:** `openmbb-linux-x64` — `chmod +x openmbb-linux-x64 && ./openmbb-linux-x64`.

These are built and verified by CI (`.github/workflows/build.yml`) on each tagged
release. To build one yourself for the OS you're on:

```
pip install .[dev]
python packaging/build.py      # -> dist/openmbb(.exe)
```

(PyInstaller can't cross-compile, so a Windows binary must be built on Windows and
a Linux binary on Linux — which is exactly what the CI matrix does.)

## Install from source (Python)

```
git clone https://github.com/rodu4835/openmbb
cd openmbb
pip install .            # or:  pip install -e .   for a dev/editable install
```

Then run:

```
openmbb             # real serial (auto-lists COM ports)
openmbb --sim       # simulator — click through everything at the desk
openmbb --port COM4 # preselect a port
openmbb --selftest  # headless transport/safety tests
openmbb --smoketest # build the GUI once, sim-connect, exit
```

Requires Python 3.9+. Dependencies (`pyserial`, `sv-ttk`) install automatically.
If `sv-ttk` is ever unavailable the app falls back to a built-in dark theme.
Fonts and the file-manager "open folder" action are cross-platform.

> If the `openmbb` command isn't found after install, your Python `Scripts` dir
> isn't on PATH — use `python -m openmbb.cli` (same flags).

## Wiring (FTDI TTL-232R-3V3 → OBD-II J1962, port under the seat)

| FTDI wire | OBD pin | Pigtail wire | Role |
|---|---|---|---|
| Black (GND) | 5 | Teal | Diagnostic ground |
| Yellow (RXD) | 8 | Black/White | bike MBB **Tx** |
| Orange (TXD) | 9 | Red/White | bike MBB **Rx** |
| **Red (+5 V)** | — | — | **NEVER connected** |

38400 baud, 8-N-1, no flow control, newline CR-LF. Bike PARKED, key ON,
kill switch OFF. Never stream the console while riding. (Port location and
pin-12 wiring vary by year; on 2017+ FX/FXS the port is under the seat.)

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

The **Analyze** tab is always available (no bike needed) and interprets a saved
session folder — or the current one:

- **Health** — SOC vs pack voltage, cell balance, capacity, temps, charge
  cycles, and the effective gearing ratio, each flagged ok / watch / alert.
- **Rides** — per-ride distance, SOC%/km, and temps parsed from the `dumplogs`
  capture.
- **Compare** — pick 2+ sessions to see settings changes and capacity / gearing
  trends over time (battery-degradation tracking).
- **Gearing** — enter new front/rear teeth to get the ratio and the exact
  `spfront` / `sprear` / `rwhcirc` values to write.

The analysis parsers are deliberately tolerant: fields the capture doesn't
include show `n/a` rather than failing.

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
src/openmbb/
  __init__.py    name, version, module docstring
  safety.py      blocklist, write whitelist, validators
  transport.py   serial transport, session logging, settings parser
  sim.py         fake serial port (synthetic fixtures for hardware-free use)
  theme.py       Sun Valley (Win11) theme with dark fallback
  gui.py         phase-gated panels + Analyze tab + menu bar
  cli.py         entry point + --selftest / --smoketest
  parsers.py     tolerant console-output parsers (bms/stats/status/ride log)
  sessions.py    load saved session folders for analysis
  health.py      health snapshot (metrics + ok/watch/alert status)
  rides.py       ride-log summaries + effective gearing from the odometer
  gearing.py     gearing math (teeth -> ratio -> speedo settings)
  compare.py     settings diff + capacity / gearing trends across sessions
tests/           pytest suite (safety/transport/config/analysis + GUI flow)
```

## Session output

Everything lands in `<save-base>/openmbb-sessions/<timestamp>_<port>/`:
`session_raw.log` (every byte, timestamped), one file per command,
`settings_baseline_*.txt`, `settings_backup_*.txt` (auto, pre-write),
`writes_journal.txt`.

**Where it saves:** by default the `<save-base>` is the current working
directory. Change it via **Session → Set save location…** (remembered across
runs in `~/.openmbb/config.json`) or per-launch with `--logdir <path>`. The
current save target is shown in the top status strip and is clickable.

## First real session checklist

1. Meter check: FTDI Orange idles ~3.3 V vs Black; Red taped off.
2. Connect & probe (Phase 0). Garbage at 38400 ⇒ suspect Tx/Rx swap — stop.
3. FULL BASELINE before anything else. Recent firmware may be newer than the
   community docs — the captured `help`/`set` output is the ground truth; the
   simulator's menus are synthetic samples, not a live capture.
4. Login attempt only after the baseline is on disk.
5. No writes on day one. Bring the session folder home first.
