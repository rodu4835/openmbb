# OpenMBB

**Read your Gen2 Zero motorcycle's own diagnostics** over the service port —
battery health, motor controller, error logs, settings — back them up, and make
sense of them offline. A safe, **read-first** desktop app for the **MBB serial
console** (OBD-II / C3 port); any writes are whitelist-only and gated. Try the
whole thing with the built-in **simulator** — no bike needed. Windows-11-native
look (Sun Valley theme).

> Personal diagnostic tool for **your own** vehicle. Not affiliated with Zero
> Motorcycles. No warranty — see LICENSE. Changing settings may affect your
> vehicle warranty; you are responsible for what you write to your bike.

OpenMBB is a **general read / diagnostics tool** for the Gen2 MBB console:
connect, read everything the bike will tell you (identity, BMS, motor
controller, stats, error log), and analyze it offline. **Writing is optional and
off by default** — the Writes tab doubles as a *reference of what's adjustable*
(each whitelisted setting shown with its effect and risk), and any change is
gated behind login + a master unlock + a per-write confirm. The gearing
calculator is just one of the optional analysis helpers, not the focus.

## Compatibility

OpenMBB talks to the **MBB (Main Bike Board) console** at 38400 baud with the
Gen2 command set (`bms`, `set`, `spfront`, `maxcustspmph`, …). That pins the scope:

| | |
|---|---|
| **Verified** | 2017 Zero FXS (MBB firmware rev 41) |
| **Should work (same console, unverified)** | Gen2 MBB Zeros, ~2013–2019: **S, SR, DS, DSR, FX, FXS, FXE**. The write panel auto-adapts — it only shows whitelisted settings that appear in *that* bike's live dump. |
| **Not compatible as-is** | Pre-2013 **Gen1** (9600 baud) and 2020+ **Cypher III / SR-F generation** (SR/F, SR/S, DSR/X, newer S/DS) — different baud *and* a different console language. |

The console baud is fixed at 38400 (the Gen2 rate). Gen1/Gen3 would need both a
different baud and a different command set, so OpenMBB is a Gen2 tool by design.

Runs on **Windows and Linux** (and macOS from source).

## Install & update

### Windows — installer (easiest)

Download `openmbb-setup-windows-x64.exe` from the repo's **Releases** page and run
it. It's a **per-user install — no admin rights**: Start-menu entry, optional
desktop icon, and a clean uninstaller. Launch it from the Start menu or the
desktop icon. To **update**, download the newer setup exe and run it over the old
install.

### Portable binary (no Python needed)

One self-contained file that bundles Python, Tk, and every dependency:

- **Windows (portable):** `openmbb-windows-x64.exe` — double-click to run from
  anywhere; nothing is installed.
- **Linux:** `openmbb-linux-x64` — `chmod +x openmbb-linux-x64 && ./openmbb-linux-x64`.

Release binaries are built and verified by CI (`.github/workflows/build.yml`) on
each tagged release.

### Run from source (any OS)

```
git clone https://github.com/rodu4835/openmbb
cd openmbb
python -m venv .venv
.venv\Scripts\activate           # Linux/macOS: source .venv/bin/activate
pip install -e .
```

Then:

```
openmbb --sim       # explore the whole tool with the simulator — no bike needed
openmbb             # real serial (auto-lists COM ports; needs an FTDI cable)
openmbb --port COM4 # preselect a port
openmbb --selftest  # headless transport/safety tests
openmbb --smoketest # build the GUI once, sim-connect, and exit
```

Requires **Python 3.9+**; `pyserial` and `sv-ttk` install automatically (if
`sv-ttk` is ever unavailable the app falls back to a built-in dark theme). Fonts
and the file-manager "open folder" action work cross-platform. To **update**:
`git pull`, then re-run `pip install -e .` if dependencies changed.

> If the `openmbb` command isn't found after install, your Python `Scripts` dir
> isn't on PATH — use `python -m openmbb.cli` (same flags).

### Build the binaries yourself

```
pip install .[dev]                    # adds PyInstaller + Pillow
python packaging/build.py             # -> dist/openmbb(.exe)  — portable, any OS
```

On **Windows**, to also build the installer (requires **Inno Setup 6**):

```
powershell -File packaging\build_and_install.ps1 -Force   # or: build-and-install.bat
```

This builds `dist\openmbb.exe`, then `packaging\Output\openmbb-setup-windows-x64.exe`,
installs it per-user, and verifies with `--selftest` / `--smoketest` (`-Force`
closes a running OpenMBB first). PyInstaller can't cross-compile — build a Windows
binary on Windows and a Linux binary on Linux, which is what the CI matrix does.

## Wiring (FTDI TTL-232R-3V3 → OBD-II J1962, port under the seat)

| FTDI wire | OBD pin | Pigtail wire | Role |
|---|---|---|---|
| Black (GND) | 5 | Teal | Diagnostic ground |
| Yellow (RXD) | 8 | Black/White | bike MBB **Tx** |
| Orange (TXD) | 9 | Red/White | bike MBB **Rx** |
| **Red (+5 V)** | — | — | **NEVER connected** |

38400 baud, 8-N-1, no flow control, newline CR-LF. Bike parked, kill switch OFF.
Never stream the console while riding. (Port location and pin-12 wiring vary by
year; on 2017+ FX/FXS the port is under the seat.)

**Powering the console:** you do *not* need to key the bike on — **plugging in
the AC charger wakes the MBB** and the console is fully live for reads (the bike
shows `Mode: Charging`). That's the easy way to do a read session. The one
caveat: **isolation-resistance readings are only valid off the charger** (the
onboard charger ties the HV bus to mains earth and makes the isolation monitor
read falsely low), so OpenMBB flags any isolation reading taken while charging.

## Phase flow

OpenMBB opens on a **Home screen**: a short blurb and two buttons — **Analyze**
(open a saved session, no bike needed) and **Connect**. A **Simulator mode** toggle
lives there too (its state shows in the status bar), so you can click through the
whole tool with no bike or cable. The staged flow below unlocks one step at a time
— you can stop after any stage, and closing the window never loses data.

0. **Connect** — optionally run **Test your cable** first: it opens the port and
   *only listens* (never transmits in software), so it proves RX wiring + baud with
   zero risk on any fixed cable — power the bike during the listen window to catch
   the boot banner. Then **Connect** wakes the `ZERO MBB>` prompt (retried), rejects
   wrong-baud/Tx-Rx garbage instead of trusting a stray `>`,
   and requires the `version` banner to parse (rev checked against the verified
   rev 41 — a different rev connects with a warning).
1. **Read** — per-command buttons; **Pull full database** captures the command
   reads + the settings dump (your backup) + the small `errorlogdump`. The heavy log
   reads (`eventlogdump`/`dumpall`, ~1 MB, minutes at 38400) are **not** in the
   default pull — they sit behind their own buttons with a confirm dialog (you
   can opt in to fold `eventlogdump` into a pull with a checkbox), because on
   a keyed-on bike a long dump can starve the MBB's CAN servicing enough that the
   BMS briefly **opens the drivetrain contactor** (a click + a flashing dash that
   recovers when the read finishes; observed live). One failed command no longer
   discards the pass, and Login unlocks only once the essential reads + a parsed
   `set` succeed. The session's power state is stamped (`session_meta.txt`); an
   on-charger baseline is flagged because isolation/SOC context isn't valid off
   the charger. *You can stop here — reads are the whole point of day one.*
2. **Login** — explicit. **On rev 41 the tunables (`spfront`/`sprear`/regen/…)
   are login-gated: `set` at level 0 shows identity only.** The password box is
   pre-filled with the last one that worked (or a community-known one — `tpsreport`,
   `wideopenthrottle`); press **Login**, or type a different one (masked in the logs,
   never saved to disk). Success is confirmed by the read-only `login` **level
   query** (`Login Level: N`), not by guessing at the reply wording. On success the
   post-login `set` is saved as the authoritative pre-change backup and the tunables
   appear.
3. **Writes** — locked behind login + master unlock toggle + per-write confirm.
   Rows appear only for settings that are BOTH on the whitelist AND present in
   the live dump. Click a row's New value cell to type a value, then Write. Every
   write: journal the intent *before it hits the wire* → re-read current → confirm
   old→new with effect/risk → auto-backup full settings → send → read-back verify →
   journal to disk. A changed row shows **↺ Reset** to restore the last-read value;
   a read-back mismatch points you to it.

The **Analyze** tab is always available (no bike needed) and interprets a saved
session folder — or the current one:

- **Health** — SOC vs pack voltage, cell balance, capacity, temps, charge
  cycles, and the effective gearing ratio, each flagged ok / watch / alert.
- **Rides** — per-ride distance, SOC%/km, and temps, read **straight off the
  bike**: `eventlogdump` prints the full event log as decoded text (including
  per-sample riding entries — SOC, pack/motor temp, voltage, rpm, odometer), which
  OpenMBB parses directly. No Zero app, no `.bin` files, no external decoder. Pull
  it with **Pull ride log from bike**, or **Load ride log (.txt)** for a log you
  already have.
- **Charts** — dependency-free plots of the ride-log series, plus `Trend:` metrics
  across your saved **real-hardware** pulls (pack capacity, charge cycles, temps,
  effective gearing) on a dated timeline. Pick a date **Range**, or **drag to
  zoom** any x-window (double-click resets).
- **Compare** — pick 2+ sessions to see exactly which **settings changed** between
  them; the over-time trends live on the Charts tab.

The analysis parsers are deliberately tolerant: fields the capture doesn't
include show `n/a` rather than failing.

## Safety model

- **Hard blocklist in the transport layer** (every UI path incl. the raw box):
  `format/erase eeprom`, `settingsrst`, `statsrst`, log clears/adds, `reset`,
  `exit_to_bl`, `test`, `wdt`, `timing`, `can`, `charger`, `sevcon preop`, plus
  the destructive commands the real rev-41 menu revealed (`dtc_clear`,
  `force_all_storage_mode`, `blcmds`, `burn`), and `set` of any protected name
  (`abs_disable`, `bypass_bms`, `ov_*`, `motstage*/ctrlstage*`,
  `sevnoregspeed/sevmaxregv/sevnoregfull`, `model/vin/serial`, …). Bare `eeprom`
  (the read-only "EEPROM usage" summary) and `obd` are allowed reads; `eeprom`
  with any argument is refused.
- **No smuggling and no back-door writes.** The transport refuses any command
  containing a control character (so a pasted `status⏎settingsrst` can't slip a
  second line onto the wire), and **all** `set <name> <value>` writes are refused
  from the raw box — writes go *only* through the gated Writes-tab flow
  (`Transport.write_setting`), which re-validates the value. The **two-token
  `set <name>`** single-setting *view* is also refused: its no-value behavior is
  unverified on rev 41 (it could be a prompt-for-value, i.e. a write), so read a
  value from the full `set` dump instead. That makes the "no UI path can send a
  blocked command" guarantee actually hold.
- A typed password is masked in **everything** written to disk for the whole
  session (not just the one command), so a late console echo can't leak it.
- Regen/thermal guards are shown **read-only** in the Writes tab.
- Validators: coast regen of exactly 0 is refused (fishtail risk);
  `noregenstopped No` warns; range limits on every numeric.
- A mid-session **reboot** (boot banner) or a **silent** console is detected and
  surfaced — login state is re-locked rather than trusted.

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
`session_meta.txt` (power mode, firmware rev, timestamp),
`settings_baseline_*.txt`, `settings_backup_*.txt` (auto, pre-write),
`writes_journal.txt`.

**Where it saves:** by default the `<save-base>` is **`~/Documents/OpenMBB`** (a
fixed, user-visible location — *not* the launch directory, so sessions never get
buried inside the install folder when you start from the desktop shortcut).
Change it via **Session → Set save location…** (remembered across runs in
`~/.openmbb/config.json`) or per-launch with `--logdir <path>`; if the configured
folder ever goes missing (deleted, unplugged drive) the app falls back to the
default instead of failing to connect. The current save target is shown in the
top status strip and is clickable.

## First real session checklist

1. Meter check: FTDI Orange idles ~3.3 V vs Black; Red taped off.
2. Connect (Phase 0). Garbage at 38400 ⇒ suspect Tx/Rx swap — stop.
3. Pull full database before anything else. Recent firmware may be newer than the
   community docs — the captured `help`/`set` output is the ground truth; the
   simulator's menus are synthetic samples, not a live capture.
4. Login attempt only after the backup is on disk.
5. No writes on day one. Bring the session folder home first.
