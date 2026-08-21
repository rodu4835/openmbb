# OpenMBB — work plan

Written 2026-08-21, after the condition-check work shipped in v0.22.0/v0.22.1.

Ordered by what unblocks the most. Each item says what it is, why it earns its place, and
what it costs. Tick items off as they land; when one is done, note the version it shipped
in rather than deleting it, so the reasoning survives.

**The organising constraint:** only one motorcycle has ever been measured. The condition
check can *describe* but not *grade* two of its three measurements, and every threshold
that exists is one part measurement, one part chemistry reasoning. Anything that breaks
that ceiling is worth more than anything that polishes what sits behind it.

---

## Tier 1 — breaks the one-bike ceiling

- [x] **Share-safe capture export.** *(shipped: `openmbb redact`, File -> Export
  share-safe copy)* A "Share this capture" action producing a bundle with
  the VIN, serial numbers and passwords stripped, safe to post on a forum or send to a
  maintainer. *Why first:* it is the only item that turns users into a calibration set,
  and it also removes a real hazard — today a user who wants help has no safe way to hand
  over a capture. The redaction map already exists from building the PII gate
  (`tests/_pii_shapes.py`, `SessionLogger.add_redaction`). *Cost:* small.

- [x] **Cross-capture history.** *(shipped: a PACK OVER TIME table on Compare, plus
  "Trend: charge index" and "Trend: weakest cell deviation" on Charts. Lifetime maxima
  were already plotted.)* Everything today answers from a single capture. Track the
  gauge-independent capacity index (Ah accepted 103–113 V), weakest-cell deviation, and
  lifetime maxima across every saved session, and plot them. *Why:* the reference bike
  reads 17.83 and 17.92 Ah on two captures — half a percent apart — so the measurement is
  stable enough that a year of them is a genuine degradation curve. No other Zero tool
  does this well. *Cost:* medium; `compare.py` and the Charts trend code already exist.

- [x] **Track the weakest cell by identity, not just by voltage.** *(shipped: `bms` now
  yields `low_cell_index`/`high_cell_index`; Compare names a cell that keeps returning,
  and prints the SOC each reading was taken at so a near-full capture is discounted rather
  than trusted.)* `bms` names the cell:
  `Lowest Cell Voltage : 3674 mV ( Cell 28 )`. The reference bike reads Cell 28, Cell 28,
  Cell 25 across three captures. *Why:* a voltage says the pack is uneven; an index says
  *which cell*, and a specific cell is a repairable thing. *Cost:* small, once history
  exists. Depends on the item above.

- [x] **Parse the commands captured on every pull and never read.** *(shipped:
  inputs/outputs/runtime/obd -> Fault codes on Health, and a Bike state block.
  sevcon/chargers/bluetooth still unread.)* `sevcon`,
  `inputs`, `outputs`, `chargers`, `bluetooth`, `obd`, `runtime`. In value order:
  - `inputs` — kickstand, brake, throttle switch states: the "why won't it go into gear"
    answer, currently captured and discarded.
  - `obd` — DTCs and MIL status, as a plain "no fault codes" line.
  - `runtime` — run vs charge time, which with the odometer gives duty cycle.
  - `sevcon` — controller temperature and faults; the second most expensive part on the
    bike.
  *Cost:* small each, independent of one another.

## Tier 2 — what an operator actually reaches for

- [x] **Used-bike inspection mode.** *(shipped: Tools -> Inspect a bike..., six steps that
  hand off to the app's own controls. Every tick is read back from app state rather than
  from having been clicked, and the event-log step goes through the existing contactor
  confirm rather than around it)* A guided flow: connect, run these reads in this
  order, do not start the heavy event-log read without the owner's consent, here is the
  verdict. *Why:* this is the scenario the condition check was built for, and it currently
  requires knowing which buttons to press in which order.

- [x] **Session library.** *(shipped: File -> Session library..., replacing the
  recent-folders listbox. Verdicts are read in the background and cached beside the
  capture)* Replace "Load session folder…" with a browsable list: date,
  odometer, verdict at a glance. *Why:* three captures already require remembering which
  timestamped folder is which.

- [x] **Charge-behaviour analysis.** *(shipped: condition.charge_behaviour +
  full_charge_holds, on the Condition tab and in the report. Taper turned out NOT to be
  resolvable at ~10-min, whole-amp sampling, and the report says so rather than inventing
  one)* ~1,470 charging samples per capture are used only for
  the capacity index. They also carry time spent sitting at 100% (the main driver of
  calendar aging), charge taper, and charger consistency over time. *Why:* real battery
  longevity advice from data already on disk.

- [x] **Range estimate, honestly done.** *(shipped: rides.consumption +
  rides.range_estimate, on the Condition tab and in the report. Built on the deepest ride
  actually logged rather than the BMS capacity, and it names the extrapolation instead of
  smoothing it over)* From measured Wh/km and the capacity index: "at
  your recent consumption, a full charge is about X km", with its own error bars and the
  temperature caveat. *Why:* unlike the dash's guess, this one can say what it rests on.

- [x] **Export a condition report.** *(shipped: File -> Save condition report..., built on
  report.condition_report so the saved page and the Analyze tab cannot drift. It withholds
  itself rather than hand over a page carrying an identifier, and it keeps the save path -
  which carries the Windows account name - off the page)* A dated page fit to hand a
  seller, or keep. *Why:* a
  verdict you cannot show anyone is half a verdict.

- [x] **Update check.** *(decided, and DELIBERATELY NOT BUILT AS WRITTEN.)* The item
  asked for a quiet "0.23.1 is available". A program that opens no socket cannot say that
  sentence, and the machinery to let it say one was judged to cost more than the sentence
  is worth. What shipped instead is the answerable half of the question.

  **The finding that decided it:** the application has never made a network request. No
  HTTP client, no sockets - the only outbound thing is `webbrowser.open()`, at call sites
  a user clicks. That posture was strong, real, and *nowhere written down*: a grep of
  README and SECURITY for a network promise returned nothing. (The README's "offline" is
  about not needing the bike - the section it sits in is headed "Analyzing a session
  without the bike" - so it was never the promise it looked like.)

  So the choice was not "add a feature" but "spend the strongest sentence this project
  could write". An opt-in check is default-off, which never reaches the person the item
  was written about, and it permanently downgrades *"OpenMBB makes no network requests"*
  to *"...except one you turned on"*. A promise enforced by `grep` and a test outranks one
  enforced by a toggle, for a tool whose users plug laptops into bikes they may be buying.

  **Shipped:** `version.py` and `__release_date__`, so a build knows its own age. Past 45
  days the home screen says how old it is, admits outright that OpenMBB cannot tell it
  what is newer, and offers a link. Every uncertain case - a clock behind the release
  date, a clock a decade out, a missing stamp - produces silence rather than a guess.
  `Network: none` in About, `--version` in the CLI, a `## Privacy` section in the README,
  an outbound request made an in-scope finding in SECURITY.md, and `tests/test_no_network.py`
  which fails the build if a network module is ever imported or a URL appears that is not
  on its allow-list.

  **What it does not do, stated honestly:** it catches slow drift - the case the item
  leads with, an author weeks behind - and it does *not* catch the fast-burst case, where
  four releases land in three days and a two-day-old copy is not stale. For that, the
  README points at GitHub's own Watch -> Custom -> Releases, which notifies in minutes and
  costs OpenMBB nothing.

## Tier 3 — more from the data we already read

- [x] **Normalise temperature against ambient.** *(shipped, but NOT as written - see
  below)* `AmbTemp` separates a hot pack from a hot day, which is exactly the distinction
  the 60 C battery alert needs before it means anything.

  **Two things in the original item were false, and the investigation is worth more than
  the feature.** First, `AmbTemp` was never unused: `rides.consumption` has always
  reported the ambient range a measurement was taken across. Second, and decisively, the
  sensor does not measure one thing. On the reference bike it reads a median 29 C between
  midnight and 06:00 while charging, where the *same sensor* reads 16 C riding at those
  same hours, and it sits at or above the pack in 107 of 1424 charging samples. It is on
  the bike, not in the air. So no aggregate of it is computed anywhere, and an ambient may
  only ever be printed beside a pack temperature off the same log line.

  A `pack rise over ambient` metric was designed, measured, and **rejected**: across two
  captures of the same bike the absolute peak moved 1 C while the rise p90 moved 4 C, so
  normalising against a soaked ~1 C-resolution thermistor *adds* variance to the steadier
  number already on screen. Rise also turns out to be a ride-duration statistic (median
  +11 C in the first 5 minutes, +31 C by 15-20), so reporting it as a pack property would
  have dressed up how long the bike gets ridden.

  What shipped instead is provenance: `condition.pack_peak` / `lifetime_peak` /
  `lifetime_peak_note`, one sentence on the existing `undetermined` list. It says where
  the hottest figure came from, and that the lifetime counter is *not* a log reading -
  which matters, because the two disagree in BOTH directions on the reference bike
  (2026-08-19 reports 60 C where the log's hottest sample is 59 C; 2026-07-10 reports
  59 C where the log holds a genuine 60 C). Ambient enters only as a disconfirmer: it can
  rule the weather out of a hot reading, never in. The error is one-directional and
  conservative, because sensor soak inflates ambient, which only ever *weakens* a
  disconfirmation.

- [x] **The graded battery row can under-report against the same capture's log.** *(fixed:
  `health_snapshot` takes an optional `log_peak_c` and grades the HIGHER of the counter and
  the log; the display shows both when they differ, since "[ALERT] 59 C" against a 60 C
  band reads as a contradiction. Taking the higher can only make a bike look worse, never
  better, which is the safe direction when a buyer is relying on it. The kwarg is optional
  so the session library is not made to re-read a megabyte per row - there is a test
  pinning that.)* On
  2026-07-10 `stats` says 59 C and `health.py` grades that `watch` (`59 < 60`), while that
  capture's own event log holds a genuine 60 C sample — the `alert` band of the same
  function. The prose above now names the higher number on the same page, but the *grade*
  is still taken from the counter alone. Fixing it properly means letting
  `health_snapshot` see the event log, which breaks the documented invariant that it only
  re-parses short command output (`library.py`). Wants an optional `log_peak_c` kwarg.

- [x] **`parsers.find` is substring-based, and "discharge" contains "charge".** *(fixed:
  a needle now has to START a word)* `find(kv, "min", "charge", "temp")` returned **-25 C**
  off the `Min Discharge Temp` line, which really does precede `Min Charge Temp` on the
  bike. A word-START anchor rather than a whole-word one is deliberate: the parsers lean on
  prefix matches throughout (`rev` → `revision`, `batt` → `battery`) and those are
  intended, while a SUFFIX match never is. Proved behaviour-preserving by diffing every
  parsed field of every parser across all three real captures plus the simulator before
  changing it: zero fields moved.

- [x] **A `-100 C` sentinel is live in the BMS pack-temperature output** *(named:
  `parsers.real_temps` + `UNUSED_SENSOR_C`/`TEMP_FLOOR_C`, replacing an inline `> -50`.
  Worth being precise - today's only consumer is `max(temps)`, which a low sentinel cannot
  drag down, so the guard is currently INERT. It exists for the consumer that has not been
  written yet - a mean, a minimum, a spread - and now that consumer has something to reach
  for. The floor is -50, not 0: this platform states a Min Discharge Temp of -25 C and a
  bike left outside genuinely reads below zero.)* —
  `Pack Temps : 27C 27C 27C 28C -100C -100C -100C -100C`. Any future consumer of that
  field has to exclude it.

- [x] **Decode the error-log conditions.** *(investigated; the premise was wrong and the
  fix is a REFUSAL, not a decoding.)* "The module did not answer" could not be
  substantiated and must not appear in the tool - it is a plausible mechanism, not a
  demonstrated one.

  What the data does prove is arithmetic, needs no reference bike and no firmware
  knowledge: **`maxv` (0 mV) is below `minv` (4294967295 mV)**, and no max/min pair over a
  non-empty set can invert. So the three fields are placeholders, not readings. The
  refusal predicate is `maxv < minv` rather than matching the 0xFFFFFFFF constant, because
  the inversion test holds on any bike and any firmware while a constant match is
  pattern-recognition on one machine.

  Also established: the sentinel is 100% POST-reflash (earliest 06/24/2026, eleven days
  after), so it is not a stale-layout artifact - the same firmware wrote and read it.

- [ ] **Refuse the module-connect aggregate triple, and report the association.** Ship the
  `maxv < minv` refusal above as a console-echo note (raw bytes stay visible, unedited),
  plus a per-capture measured association on the fault line: on the reference bike all 54
  module-connect failures fell within 60 s of a "BMS Disable - High Temp". Printed as an
  association, never as a cause - the entry does not state one. Computed per capture, and
  omitted entirely when there are no thermal disables. *Cost:* small; the design is
  settled, only the building is left.

- [x] **Attempt the undecodable entries.** *(CLOSED - there is nothing to decode, and the
  premise does not describe this data path.)* The "about 5%" figure is not reproducible by
  any measure: the true rate is **16 rows out of 25,415 entry-reads**, and there are zero
  `???` markers anywhere in the tree. Those 16 are not an unknown entry type at all - they
  are a 64-word ARM stack dump the firmware printed deliberately during one watchdog
  reset, self-labelled by the `Stack: 0x2000F970` entry directly above them. Resolving
  them to symbols needs a Rev 12 firmware image that is not obtainable, so they stay raw
  and are already rendered correctly.

  The `0x.. ???` rendering belongs to BINARY-log decoders. This MBB rejects `dumplogs`
  outright ("Sorry, 'dumplogs' is an invalid command"), so that path could not be
  exercised - recorded as not run, never as a pass and never as a refutation.

  **The real undecodable population is something else entirely, 24x larger, and already
  handled correctly**: 1,051 records of firmware-revision layout mismatch, splitting
  perfectly at the 2026-06-13 reflash (526/526 and 525/525 corrupt before, 0 of 16,729
  after). The existing guard in `_mode_samples` refuses them, which is right - a wrong
  cell voltage there reads as a pack that never sags, the unsafe direction. Never attempt
  to decode that population.

- [x] **`parse_ride_log` ignores the `BattTemp:` dialect** *(handled, and deliberately
  NOT by merging it into `pack_temp_c`.)* It is parsed into its own `batt_temp_c`, so the
  reading is not thrown away, and kept out of the peak. `pack_temp_c` means the HIGHEST
  module - `PackTemp: h 60C, l 58C` gives 60 - which is what makes it comparable with the
  BMS lifetime counter. This dialect prints a single number and no capture available
  establishes whether that is the max, a mean across modules, or one sensor. If it is a
  mean, reading it as a peak reports the pack COOLER than it got, and this tool grades a
  hot pack - so the error would run in the unsafe direction. It is no longer silent: the
  peak sentence names the dialect and says exactly what is unknown about it.
  **Still wants ground truth** on which firmware emits it and what the number means; that
  is a question for a second bike, not for more code.

- [x] **`openmbb analyze` renders distance in km only** *(fixed: `--distance km|mi`, and
  `format_report(dist_units=...)`. Distances stay canonical in kilometres in the report
  dict exactly as temperatures stay Celsius, so JSON consumers and every threshold in the
  codebase are untouched; only the rendering converts. Note per-km RATES convert the other
  way - Wh/mi is a larger number than Wh/km - which has its own test.)*

- [x] **Read `Max Charge Temp` from the bike** instead of hardcoding the same 50 C in
  `health.py`'s note. *(shipped: `parse_bms` now yields `max_charge_temp_c`,
  `min_charge_temp_c` and `min_discharge_temp_c`; the Health note quotes the bike's own
  range where it states one and says "documented default, not read from this bike" where
  it does not — the REG-2 treatment the motor row already had.)* The **grading bands are
  deliberately untouched**: `Max Charge Temp` is a CHARGE limit and the row it annotates is
  a lifetime maximum that may have been set while riding, so grading by it would be
  measuring one thing with another thing's ruler. There is a test with a bike stating 45 C
  that pins this, because a bike stating 50 C cannot distinguish the bike's figure from the
  hardcoded band.

- [ ] **Rename or re-describe the "Module connect failures" fault class.** On the
  reference bike 100% of them are downstream of thermal disables, so the name implies a
  connection defect the evidence does not support. The count is honest; the label may not
  be. Wants a second bike before renaming.

- [ ] **`dumplogs` is called but is not a real rev-41 command.** `transport.py` records
  that the bike replies "invalid command", yet `condition`/`health`/`library` still fall
  back to `session.cmd("dumplogs")`. Establish whether that is a harmless dead path kept
  for pre-rev-41 captures or a silent fallback masking a missing read.

## Tier 4 — structural

- [ ] **Split `gui.py`.** ~5,000 lines, ~95% inside one nested class inside one function,
  which is why `test_gui_flow.py` needs 2,400. The device-read orchestration is trapped in
  there. *What it unlocks:* `openmbb read --port COM3` — headless capture, scriptable,
  able to run unattended on a Raspberry Pi. The single biggest structural unlock in the
  project. *Cost:* large, and it wants the bike to verify against.

- [ ] **Version the capture format.** Session folders carry no schema version. We were
  just bitten by *firmware* record layouts changing underneath us; our own format should
  state what it is.

- [ ] **More redacted fixtures from real captures.** Every parser defect found this week
  came from real hardware output, and the one committed fixture already contained all four
  shapes — nobody had asked it the right questions.

- [ ] **Property-based tests on the parsers.** Generate malformed console output and
  assert the invariant that actually matters: never raise, never invent a value. Cheap
  insurance given how many field-shape assumptions turned out wrong.

## Open questions, not tasks

- **Does the console's −7 h clock rendering follow US daylight saving?** All captures so
  far are inside US DST. The issue #1 reporter is sending a capture after **1 November
  2026**, which settles it. If it tracks US DST, the clock correction must too.
- **Calibration.** The moment a capture from a second Gen2 bike exists, revisit
  `derate_profile` and `cell_sag`: both currently describe rather than grade, and both
  become real tests with one more data point.
- **What displayed 0% means.** The lowest reading ever logged on the reference bike is
  13%, so nothing measures the bottom of the gauge.

---

## Shipped

- [x] **Share-safe capture export** — `openmbb redact`, File → Export share-safe copy.
  Verifies its own output and discards a bundle it cannot vouch for. *v0.23.0*
- [x] **Cross-capture history** — a PACK OVER TIME table on Compare, and `Trend: charge
  index` / `Trend: weakest cell deviation` on Charts. The two metrics here a firmware
  reflash cannot move. *v0.23.0*
- [x] **Weakest cell by identity** — `bms` names the cell and the tool was throwing the
  attribution away; a cell that keeps returning is reported with the SOC each reading was
  taken at. *v0.23.0*
- [x] **The four commands captured on every pull and never read** — `inputs`, `outputs`,
  `runtime`, `obd` → fault codes on Health and a Bike state block. *v0.23.0*
- [x] **Session library and notes** — File → Session library…, ordered by when each
  capture was *taken*. Notes live in the capture folder, so they travel with a copy.
  *v0.23.0*
- [x] **Charging habits** — time spent sitting at full with the charger attached, read
  from the charger events because the samples stop when the charge does (13 h of 430 on
  the reference bike). The taper is not reported, because at 10-minute whole-amp sampling
  it is not there to report. *v0.23.0*
- [x] **Condition report export** — File → Save condition report…, which withholds itself
  rather than hand over a page carrying an identifier. *v0.23.0*
- [x] **Measured consumption and range** — signed Wh/km, and a range from the deepest
  discharge actually logged rather than the BMS capacity, with the extrapolation named.
  *v0.23.0*
- [x] **Used-bike inspection mode** — Tools → Inspect a bike…, six steps whose ticks are
  read back from app state rather than from having been clicked. *v0.23.0*
- [x] **Adversarial pre-push review, and its 15 fixes** — 7 lenses, 2 skeptics per
  finding, 18 mutation-checked regression tests. Caught a library verdict that showed
  "ok" for a pack graded "concern", a cache write that reversed every Charts timeline, an
  inspection flow that read another bike's capture, and a `redact` that could delete the
  capture it was pointed at. *v0.23.0*

- [x] **Pack condition check and verdict** — Analyze → Condition, plus Condition and
  Verdict blocks in `openmbb analyze`. Grades only what needs no reference bike. *v0.22.0*
- [x] **Clock offset measured, not configured** — the bike keeps three independently-set
  clocks; the capture records the machine's clock beside the bike's. *v0.22.1*
- [x] **Stale-record guard** — ride records written by older firmware have fabricated
  trailing fields, which read as a pack whose weakest cell never sags. *v0.22.0*
- [x] **Legacy weakest-cell channel** — recovers cell data from discharge-limit events
  when the modern channel is unreadable. *v0.22.0*
- [x] **SOC gauge note corrected** — the 2026-06-13 reflash rescaled the display ~1.4×
  while the pack moved ~3%. *v0.22.0*
- [x] **CI on pull requests** — 372 tests, both OSes, on every PR. *v0.21.0*
- [x] **Light mode fixed** — Treeview tag colours were baked in at build time. *v0.21.0*
