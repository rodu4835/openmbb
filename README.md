# OpenMBB

**Read your Gen2 Zero motorcycle's own diagnostics** over the service port —
battery health, motor controller, error logs, settings — back them up, and make
sense of them offline, ending in a plain verdict on the pack. A safe,
**read-first** desktop app for the **MBB serial console** (OBD-II / C3 port); any
writes are whitelist-only and gated. Try the whole thing with the built-in
**simulator** — no bike needed. Windows-11-native look (Sun Valley theme).

> ⚠️ **USE AT YOUR OWN RISK.** OpenMBB is an unofficial, independent hobby tool
> for diagnosing **your own** vehicle. It is **not affiliated with, authorized,
> endorsed by, or sponsored by Zero Motorcycles, Inc. or Sevcon/BorgWarner**;
> those names are used only to describe the hardware it talks to. Writing settings
> or sending console commands **can damage your motorcycle, brick components,
> void your warranty, or create an unsafe riding condition, and may be
> irreversible.** It has been verified on a **single 2017 Zero FXS (MBB rev 41)**
> only — behavior on any other model or firmware is untested. **No warranty; the
> authors accept no liability** for any damage, injury, or loss. See
> [LICENSE](LICENSE).

OpenMBB is a **general read / diagnostics tool** for the Gen2 MBB console:
connect, read everything the bike will tell you (identity, BMS, motor
controller, stats, error log), and analyze it offline. The analysis ends in a
graded **verdict** on the battery — healthy, worth a look, walk away, or
**cannot tell** — and that last one is a real answer: it grades only what a
single capture can judge with no second bike to compare against, and names every
check it could not answer rather than letting silence read as a pass. **Writing
is optional and off by default** — the Writes tab doubles as a *reference of
what's adjustable*
(each whitelisted setting shown with its effect and risk), and any change is
gated behind login + a master unlock + a per-write confirm. The gearing
calculator is just one of the optional analysis helpers, not the focus.

## Compatibility

OpenMBB talks to the **MBB (Main Bike Board) console** at 38400 baud with the
Gen2 command set (`bms`, `set`, `spfront`, `maxcustspmph`, …). That pins the scope:

| | |
|---|---|
| **Verified** | 2017 Zero FXS (MBB firmware rev 41) |
| **Should work (same console, UNVERIFIED)** | Other Gen2 MBB Zeros of the ~2013–2019 era — **S, SR, DS, DSR, FX** (and the later **FXE**, a Gen2-platform rebadge) — *if* they run the same 38400-baud MBB console. Command set assumed compatible but **untested**; treat writes with extra caution. The write panel auto-adapts — it only shows whitelisted settings that appear in *that* bike's live dump. |
| **Not compatible as-is** | Pre-2013 **Gen1** (9600 baud) and 2020+ **Cypher III / SR-F generation** (SR/F, SR/S, DSR/X, newer S/DS) — different baud *and* a different console language. |

The console baud is fixed at 38400 (the Gen2 rate). Gen1/Gen3 would need both a
different baud and a different command set, so OpenMBB is a Gen2 tool by design.

Built and tested on **Windows and Linux** (CI covers both). Runs from source on
macOS too, but that isn't exercised by CI.

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

Requires **Python 3.9+** (that is what the package metadata enforces; CI runs
3.12, so 3.9–3.11 are supported but untested); `pyserial` and `sv-ttk` install
automatically (if `sv-ttk` is ever unavailable the app falls back to a built-in
theme in whichever mode you have selected, light or dark). Fonts
and the file-manager "open folder" action work cross-platform. To **update**:
`git pull`, then re-run `pip install -e .` if dependencies changed.

> If the `openmbb` command isn't found after install, your Python `Scripts` dir
> isn't on PATH — use `python -m openmbb.cli` (same flags).

### Build the binaries yourself

```
pip install ".[dev]"                  # adds PyInstaller + Pillow (quote it — zsh
                                      # expands the brackets otherwise)
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
(opens the **Session library**: every saved capture with its date, odometer,
SOC and pack verdict, no bike needed) and **Connect**. A **Simulator mode** toggle
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
   recovers when the read finishes; observed live) — and each contactor open
   leaves a **permanent "Line Contactor o/c — VERY SEVERE" error-log entry** the
   app can't clear, so only read the event log when you need it. During a heavy
   read the app shows a **blocking "Reading — please wait" window** that locks the
   rest of the UI until it finishes, then offers **Continue** (the console is
   single-threaded — nothing else can run mid-dump). One failed command no longer
   discards the pass, but **Writes** unlocks only once the essential reads
   (`version`, `status`, `stats`) plus a parsed `set` succeed: that parsed dump is
   the backup on disk, and nothing is written without one (the login in step 2 is
   the other half of that gate). **Login** itself opens on Connect alone — it only
   reads. The session's power state is stamped (`session_meta.txt`); a baseline
   pulled **on the charger** is flagged, because an isolation reading taken there
   is falsely low and the SOC context is a charging one, not the bike's resting
   one. *You can stop here — reads are the whole point of day one.*
2. **Login** — explicit. **On rev 41 the tunables (`spfront`/`sprear`/regen/…)
   are login-gated: `set` at level 0 shows identity only.** The password box is
   pre-filled with the last one that worked (or a community-known one — `tpsreport`,
   `wideopenthrottle`); press **Login**, or type a different one (masked in
   everything written to the session folder). If a password you typed works and
   isn't community-known, the app *offers* to remember it — say yes and it is
   stored in clear text in `~/.openmbb/config.json` and tried automatically next
   time; clear saved ones via **Tools → Settings → Login**. Success is confirmed
   by the read-only `login` **level query** (`Login Level: N`), not by guessing at
   the reply wording. On success the post-login `set` is saved as the authoritative
   pre-change backup and the tunables appear.
3. **Writes** — locked behind login + master unlock toggle + per-write confirm.
   Rows appear only for settings that are BOTH on the whitelist AND present in
   the live dump. Click a row's New value cell to type a value, then Write. Every
   write: re-read current → confirm old→new with effect/risk → auto-backup full
   settings → journal the intent *before it hits the wire* → send → read-back
   verify → journal the result. A changed row shows **↺ Reset** to restore the
   last-read value; a read-back mismatch points you to it.

**File → Session library…** lists every capture in your save folder as a row you
can actually read: when it was taken, the odometer, the SOC, and the pack
verdict. Verdicts are read in the background after the list is on screen (each
one re-reads about a megabyte of event log) and cached beside the capture, so a
folder you have opened before comes back instantly. A capture pulled without
`+event log` says **no log** rather than pretending to a verdict, and one whose
log won't read says **unreadable** — neither ever renders as a pass. You can
attach a **note** to any capture (`before the re-gear`, `after the firmware
update`); it is written into the capture folder, so it travels with the capture
when you copy or share it.

The **Analyze** tab is always available (no bike needed) and interprets a saved
session folder — or the current one:

- **Health** — one row per reading in the capture: SOC vs pack voltage, cell
  balance and spread, pack capacity, charge cycles, odometer, lifetime
  efficiency, lifetime peak temps, isolation resistance and the effective
  gearing ratio. Rows with a threshold — and any live console warning — are
  flagged ok / watch / alert; the rest are informational. Click a row for what
  it is and what a healthy reading looks like.
- **Condition** — what the ride and charge *samples* say about the pack, as
  against Health's single current readings. Needs the event log in the session
  folder (`eventlogdump`): after a **Pull full database** without it, most rows
  read **Not determined** — a question this capture couldn't answer, never a
  pass. (**Load ride log (.txt)** feeds Rides and Charts only.) A one-line
  **verdict** grades, worst-wins, only what one capture can judge with no second
  bike to compare against: the weakest cell against its own pack average under
  load, absolute cell voltage under load, resting cell spread, and any live
  isolation or warning Health flagged. Charge capacity — charge accepted between
  103 and 113 V, a comparable index rather than the pack's total, and the one
  capacity figure a firmware SOC rescale can't move — and the BMS discharge
  allowance are **measured, not graded**; logged faults are counted, not graded.
  It also flags a **Statistics RESET** (every "lifetime" figure on Health dates
  from there, not from the bike's build date), shows the bike's MBB / BMS / dash
  clocks against the capturing machine's clock — more than ten minutes out and
  the Rides timestamps are shifted to match — and drops cell readings a firmware
  change left undecodable rather than believing them.
- **Rides** — per-ride start time, distance, SOC used, SOC per unit distance,
  peak pack/motor temperature and peak rpm, read **straight off the bike**:
  `eventlogdump` prints the full event log as decoded text (including per-sample
  riding entries — SOC, pack/motor temp, voltage, rpm, odometer), which OpenMBB
  parses directly. No Zero app, no `.bin` files, no external decoder. Distances
  and temperatures follow the units set in **Tools → Settings**, so the column
  reads SOC%/km or SOC%/mi accordingly. Where the bike's own clock is more than
  ten minutes off the machine that captured the session, the start times are
  shifted onto the capture's clock and the totals line says so. Pull it with
  **Pull ride log from bike**, or **Load ride log (.txt)** for a log you
  already have.
- **Charts** — dependency-free plots of the ride-log series, plus `Trend:` metrics
  across your saved **real-hardware** pulls (pack capacity, charge cycles, temps,
  effective gearing) on a dated timeline. Pick a date **Range**, or **drag to
  zoom** any x-window (double-click resets).
- **Compare** — pick 2+ sessions to see exactly which **settings changed** between
  them; the over-time trends live on the Charts tab.

The analysis parsers are deliberately tolerant: fields the capture doesn't
include show `n/a` rather than failing. The **Condition** tab goes the other
way and names what it could not measure: a check this capture can't answer is
listed as **not determined**, never scored as a pass.

## Safety model

It's your own bike, so OpenMBB does **not** put a hard wall between you and the
console — but it makes the dangerous things deliberate, loud, and hard to do by
accident:

- **Destructive commands are gated behind an informed-consent dialog, not
  hard-blocked.** For the known-destructive set — `format/erase eeprom`,
  `settingsrst`, `statsrst`, log clears/adds, `reset`, `exit_to_bl`, `test`,
  `wdt`, `timing`, `can`, `charger`, `sevcon preop`, and the ones the real rev-41
  menu revealed (`dtc_clear`, `force_all_storage_mode`, `blcmds`, `burn`) — the
  raw Console will not send them casually: it shows a dialog spelling out what the
  command does, what could happen, and how (if at all) to recover, and makes you
  **type `confirm`** first. There is no such dialog on the read commands. Bare
  `eeprom` (the read-only "EEPROM usage" summary) and `obd` are ordinary reads;
  `eeprom` with any argument is treated as destructive.
- **What IS refused outright, with no override:** any command containing a
  **control character**, non-ASCII, or a second line (so a pasted
  `status⏎settingsrst` can't smuggle another command onto the wire). That refusal
  is unconditional — there is no dialog that gets past it.
- **What is refused *pending an informed confirm*:** every `set <name> <value>`
  write from the raw box, and the two-token `set <name>` single-setting *view*
  (its no-value behaviour is unverified on rev 41 — it could prompt-to-write), so
  read values from the full `set` dump instead. These stop at the **type
  `confirm`** dialog rather than a hard wall: typing it deliberately sends the
  command. The guided path is the safe one — writes through the Writes tab go via
  `Transport.write_setting`, which re-validates against the whitelist.
- **Verified on ONE bike.** The blocklist, whitelist, parsers and per-command
  recovery notes were checked against a single **2017 Zero FXS at MBB rev 41**.
  On any other model or firmware they are *untested* — a "confirm" you type there
  is trusting guidance that may not apply to your hardware. The app warns when it
  can't confirm your bike is a verified FXS rev 41.
- A typed password is masked in **everything** written to disk for the whole
  session (not just the one command), so a late console echo can't leak it.
- **Regen/thermal guards are blocklisted, not writable through the guided flow.**
  `sevnoregspeed`, `sevmaxregv`, `sevnoregfull`, `motstage1`/`motstage2`,
  `ctrlstage1`/`ctrlstage2` and `sevmaxdischgcur` sit on the blocklist rather than
  the write whitelist, so the Writes tab never shows a row for one (rows come only
  from settings that are on the whitelist AND in your live dump) and
  `Transport.write_setting` refuses them outright. **Help → Command reference** is
  where each guard, what it protects, and the cost of changing it are written
  down. Typed into the raw Console, `set <guard> <value>` is treated like any
  other blocklisted command: the **type `confirm`** dialog, not a hard wall.
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
  cli.py         entry point + analyze / sessions + --selftest / --smoketest
  parsers.py     tolerant console-output parsers (bms/stats/status/ride log)
  sessions.py    load saved session folders for analysis
  report.py      saved session -> JSON-ready report (the headless path)
  health.py      health snapshot (typed metrics + ok/watch/alert status)
  condition.py   pack condition from the ride/charge SAMPLES + the verdict + clocks
  rides.py       ride-log summaries + effective gearing from the odometer
  gearing.py     gearing math (teeth -> ratio -> speedo settings)
  compare.py     settings diff + the pack's own figures across sessions
  library.py     the saved captures on disk: rows, notes, cached verdicts
  charts.py      series extraction for the dependency-free plots
  config.py      saved preferences (units, theme, save location, logins)
  dialogs.py     themed message boxes, centred on the parent window
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

## Analyzing a session without the bike

A session folder is self-contained, so the analysis runs anywhere — no
motorcycle, no serial port, no GUI. Useful for looking at a capture on another
machine, diffing two of your own, or letting someone else look at yours.

```bash
openmbb sessions                       # list saved session folders, newest first
openmbb sessions --logdir <path>       # list under a different save base
openmbb sessions --json                # the same list, structured
openmbb analyze <session-folder>       # the full report as text
openmbb analyze <folder> --json        # the same report, structured
openmbb analyze <folder> --units F     # temperatures in Fahrenheit
openmbb analyze <folder> --fail-on-alert   # exit 1 if any HEALTH metric is at alert
```

The text report is five blocks — **Health, Rides, Clocks, Condition, Verdict**,
in that order. Rides needs an event log in the capture and Clocks needs a `stats`
read; the other three always print, and say what they could not determine.
Abbreviated from a real rev-41 capture:

```
== Clocks ==
  MBB (writes the event log): 08/19/2026 15:58:01
  BMS:                        08/19/2026 15:58:03 (epoch 1787180283)
  Dash:                       16:08
  This capture was taken at:  2026-08-19 16:05:13 (the capturing machine's clock)
  -> the bike's MBB clock is 7 m behind

== Condition (pack) ==
  log covers 06/24/2026 21:59:31 -> 08/13/2026 18:03:28  (1231 ride, 1476 charge samples)
  charge accepted 103-113 V: median 17.83 Ah over 43 sessions
  weakest cell under load: 3165 mV at 136 A, 85% SOC, 30 C
  discharge allowance: median 85% · worst 22% at 59 C / 27% SOC
  could not determine: whether the statistics were ever reset: none found, but
    this log only reaches back to 06/24/2026, so a reset before that would not appear

== Verdict ==
  Nothing in this capture looks wrong with the pack
    [ok     ] Weakest cell vs pack     median 63.6 mV below the pack average under
                                       load, over 678 loaded samples
    [ok     ] Lowest cell under load   3165 mV, from 678 riding samples
    [ok     ] Cell spread at rest      2 mV (read at 96 % state of charge)
```

**Exit codes.** `0` on success, and always unless you pass `--fail-on-alert`.
`1` only with `--fail-on-alert`, and only when a **health** metric is at alert —
narrower than it sounds, because that count comes off the health rows alone, so a
`concern` verdict raised by a Condition check still exits `0`. `2` for a path
that isn't a directory, or a directory holding neither an `NNN_<cmd>.txt` capture
nor a settings dump. `--fail-on-alert` is there so this can drive a script or a
scheduled check rather than only a pair of eyes.

**On the JSON.** Each health metric is
`{label, value, unit, display, status, note}`. `value` is the datum — a number
where the metric is numeric — and `unit` names it, so a threshold comparison
needs no string parsing:

```json
{ "label": "Max battery temp (lifetime)", "value": 60.0, "unit": "C",
  "display": "60 C", "status": "alert",
  "note": "highest EVER recorded, not the current temperature; ..." }
```

`value` and `unit` stay in canonical Celsius whatever `--units` says, because
every threshold in the health module compares in Celsius. `display` **and**
`note` follow `--units` — the note carries the thresholds, so it renders in the
same scale as the number above it. Read `value`/`unit` programmatically and
`display`/`note` for humans; the top-level `"units"` names the scale they were
rendered in. Every other temperature in the JSON is Celsius and its key says so
(`max_pack_temp_c`, `at_pack_temp_c`). `--units` defaults to `C` and does **not**
read the temperature setting saved by the GUI.

The top level carries `session`, `units`, `counts`, `health`, `rides`,
`ride_source`, `ride_log_truncated`, `clocks`, `condition` and `verdict`.
`condition` and `verdict` are always present — when a capture holds no event log
their `undetermined` list and an `unknown` level are the answer, rather than the
keys being absent.

Ride telemetry is included when the session contains an event-log capture
(`eventlogdump`, or the legacy `dumplogs`). A baseline pull deliberately skips
the heavy event log, so `"rides": null` there is expected rather than a failure —
and the Condition block will say which checks it could not answer as a result.

## First real session checklist

1. Meter check: FTDI Orange idles ~3.3 V vs Black; Red taped off.
2. Connect (Phase 0). Garbage at 38400 ⇒ suspect Tx/Rx swap — stop.
3. Pull full database before anything else. Recent firmware may be newer than the
   community docs — the captured `help`/`set` output is the ground truth; the
   simulator's menus are synthetic samples, not a live capture.
4. Login attempt only after the backup is on disk.
5. No writes on day one. Bring the session folder home first.

## Development & contributing

```bash
pip install ".[dev]"      # editable install + test/build tools
python -m pytest -q       # full suite (GUI tests need a display; skip cleanly without one)
openmbb --selftest        # headless transport/safety self-test
openmbb --smoketest       # frozen-GUI/pyserial smoke test
```

Everything runs against the built-in **simulator** — you can develop and test the
whole app with no bike attached. Issues and pull requests are welcome; please run
`pytest` + `--selftest` before opening a PR, though CI now runs the full suite on
Windows and Linux for every pull request and every push to `main`
([tests.yml](.github/workflows/tests.yml)), so you will get a second opinion
either way.

**A capture from a different bike is the most valuable thing you can contribute.**
A `set`/`help` dump from another Gen2 model or firmware ground-truths the safety
lists, which are checked against a single 2017 FXS rev 41 today. A full capture
including the event log is worth more still: the Condition tab's verdict grades
only what one capture can judge without a second bike to compare against, and two
of its measurements stay descriptive rather than graded until another pack has
been measured.

## Security

Found a way to get the tool to send something dangerous it shouldn't, or a way it
could damage a bike? Please report it privately — see [SECURITY.md](SECURITY.md).

## License

**MIT** — Copyright © 2026 rodu4835. See [LICENSE](LICENSE). Bundled third-party
components (pyserial, sv-ttk, Tcl/Tk, the CPython runtime) keep their own
licenses — see [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).

*OpenMBB is an independent community project and is not affiliated with, endorsed
by, or sponsored by Zero Motorcycles, Inc. or Sevcon/BorgWarner. "Zero
Motorcycles", "Sevcon", and related marks belong to their respective owners and
are used here only to describe the hardware this tool communicates with.*
