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

- [x] **Refuse the module-connect aggregate triple, and report the association.**
  *(shipped: `parsers.decode_module_connect_failure` and
  `condition.module_failure_context`.)* `modv`/`maxv`/`minv` are refused on the arithmetic
  - maxv 0 mV sits below minv 4294967295 mV, and no max/min pair over a non-empty set can
  invert. The predicate is `maxv < minv`, not a match on 0xFFFFFFFF, and there is a test
  with a 16-bit sentinel (65535) that only passes for the general form - on the reference
  bike both tests agree, so nothing else could show which was chosen. `raw0` is kept: it
  moves line to line and sits at a plausible pack voltage, so it is a real reading.

  The association is printed as an association: *"all 54 fell within 60 s of a
  high-temperature disable in this capture — association only; the log does not state a
  cause"*. Computed per capture (18/18, 18/18, 54/54 across the three), and omitted
  entirely when a capture logged no thermal disables, because "0 of 54" would read as
  evidence of absence. Nothing anywhere says why the module was ineligible; there is a
  test asserting the words "did not answer" never appear.

- [ ] **Rename or re-describe the "Module connect failures" fault class.** On the
  reference bike 100% of them are downstream of thermal disables, so the name implies a
  connection defect the evidence does not support. The count is honest; the label may not
  be. Wants a second bike before renaming.

- [x] **`dumplogs` is called but is not a real rev-41 command.** *(investigated: neither
  dead code nor a masked read - a third thing.)* It is NOT dead: a real capture contains
  `016_dumplogs.txt`. What that file contains is the console declining -
  *"Sorry, 'dumplogs' is an invalid command"* - which is 77 non-empty characters, so every
  `if text.strip()` fallback in the codebase accepted it as an event log.

  Nothing downstream was fooled into a wrong VERDICT: it parses to zero records and the
  checks correctly reported "cannot tell". But `library.summarize` reported
  `has_event_log: True` for a capture that has none, and the tool then walked a
  megabyte-sized code path to prove it had nothing to say. Fixed with one shared predicate
  (`parsers.is_console_refusal`) and one shared picker (`parsers.event_log_text`), so the
  five hand-rolled fallbacks cannot drift apart again.

## Tier 4 — structural

- [~] **Split `gui.py`.** **DEFERRED — planned in full, deliberately not started.**
  ~5,800 lines, ~95% inside one nested class inside one function. *What it would unlock:*
  `openmbb read --port COM3` — headless capture, scriptable, able to run unattended on a
  Raspberry Pi.

  A full extraction plan exists and is sound: strangler route, read-only surface,
  `openmbb probe` at day 5 and `openmbb read` at day 7, ten PRs, ~9 working days. It was
  not skipped for being hard. It was skipped because of what the planning turned up:

  1. **The urgent part was never the refactor.** The two defects that made this look
     urgent — the write chain enforcing one of its four links, and the contactor gate not
     existing below the GUI — were plain bugs, not consequences of the entanglement. Both
     are fixed. What remains is a capability, not a repair.
  2. **The headline feature is speculative here.** Unattended capture on a Pi is genuinely
     useful to somebody. The owner goes to his bike with a laptop and opens the GUI, and
     in the project's life to date the unattended case has never arisen.
  3. **It does not deliver what the item's title implies.** `gui.py` goes 5,817 → ~5,200,
     about 11%. Roughly 700 wire-touching lines leave and ~80 return as progress and
     consent plumbing. Read as "make this file manageable", the work fails; read as "unlock
     headless capture", it succeeds completely. The two readings lead to very different
     weeks and the title invites the wrong one.

  **What the planning is worth keeping:** the entanglement is not 863 lines of device code
  waiting to be moved. It is ~1,900 lines across 36 methods where serial work and dialog
  work are interleaved in the same breath — `_baseline` (148 lines), `_login` (119),
  `_connect` (110). Anybody who reopens this should start from that number, not from the
  file size.

  **Reopen when:** headless or unattended capture becomes something actually wanted, or a
  second person starts working in `gui.py` and the file's size becomes a collaboration
  cost rather than an aesthetic one.

- [ ] **Golden transcripts and richer port fakes.** *(Split out of the extraction above,
  which no longer gates it.)* Record the exact sequence of transport calls and the
  byte-normalised session folder the read path produces, commit them as goldens, and add
  port fakes for real-bike behaviours the simulator structurally cannot produce:
  - a **clamping** port — the write that answers `SUCCESS  maxcustspmph set to 102` and
    reads back `89`. That is the only write behaviour ever confirmed on this hardware
    (2026-07-12 captures 022→023) and **it has no test at all**;
  - a **no-prompt dump** port — on real firmware a dump ending with no console prompt is
    the NORMAL exit, and `SimPort._respond` always appends one, so the truncation and
    `_resync` paths are unreachable in the simulator by construction;
  - a **paced** port at 3,840 B/s, so idle timeouts and the confirming lull are actually
    exercised rather than short-circuited by `SimPort.is_sim`.

  *Why it stands alone:* it is the project's first behaviour-preserving oracle, it gives
  the silent-clamp branch its first coverage, and it is worth having whether or not any
  extraction ever follows. *Cost:* ~1.5 days, no bike needed.

- [ ] **Version the capture format.** Session folders carry no schema version. We were
  just bitten by *firmware* record layouts changing underneath us; our own format should
  state what it is.

- [ ] **More redacted fixtures from real captures.** Every parser defect found this week
  came from real hardware output, and the one committed fixture already contained all four
  shapes — nobody had asked it the right questions.

- [x] **Property-based tests on the parsers.** *(shipped: the measurement, five root-cause
  fixes, and `tests/test_parser_properties.py`.)* The contract under test is the module's
  own docstring - *"every parser here is label-fuzzy and degrades to None rather than
  raising"* - split into three properties: **never raise**, **never invent**, and
  **type-shape does not depend on the input being well-formed**. Strategies are seeded from
  `tests/fixtures/` and never from the real captures, which do not exist on CI - seeding
  from them would make the tests quietly weaker on every runner than on the machine they
  were written on.

  **Fixed, by root cause, each reproduced before being touched and each mutation-checked
  (7/7):**

  1. **`parse_obd` reported the warning lamp OFF on a line saying it was ON.** Detected
     case-insensitively, extracted case-sensitively, so `MIL ON : 1` fell past the
     exact-case branch, partitioned the un-lowered line on a lowercase needle, found
     nothing, and `bool(None)` collapsed to a definite `False`. Extraction is
     case-insensitive now, and an unreadable value is **`None`, not `False`** - a check
     that could not run may never read as a pass.
  2. **`all_nums` lacked the garbled-decimal guard `num` documents.** `0.-51` - a token the
     firmware really emits, six of them in the real captures - came back as `[0.0, -51.0]`,
     two readings invented from one unreadable field. It now refuses the **whole** token
     rather than its head: keeping the `-51` would be a hundred times off, which is worse
     than the wrong zero because it looks plausible. Neighbours on the same line survive.
  3. **`find()` answered from a neighbouring label when the primary one was absent.**
     Delete `Pack Temps` and `pack_max_temp_c` read 23 C off `Lowest Present Pack Temp` -
     the pack's **lowest** sensor reported as its **maximum**, on the metric that grades a
     hot pack, so the error ran in the unsafe direction. The fix is NOT strictness:
     label-fuzziness is the advertised contract and is what lets these parsers meet a bike
     nobody has seen. `find()` gained an `exclude` for qualifiers that **invert** an
     answer's meaning rather than merely widening it. It now returns `None`.
  4. **`event_log_text` accepted OpenMBB's own `### TRUNCATED` banner as an event log**,
     shadowing a real log in the fallback command. A genuinely truncated log is still a log
     and stays readable - on real firmware a dump ending without a prompt is the NORMAL
     exit - so only a reply that is *nothing but* the banner is refused.
  7. **Non-finite floats.** A 309-digit run overflows to `inf`, which then raises
     `OverflowError` in the Fahrenheit conversion and the derate profile. Pure fuzz - the
     longest real digit run is ten characters - but the promise is about *any* input, and
     one `_finite()` guard shared by `num` and `all_nums` closes the family.

  **The two UNCONFIRMED claims are now settled: neither reproduces, and the reason is
  visible.** `soc_pct` and `capacity_ah` do fall through to a neighbouring label when the
  primary is removed - `Total SOC` and `Total Capacity` - but both neighbours carry the
  same value, so the answer stays correct. That is the *same mechanism* as root cause 3
  with a harmless outcome, which is the useful way to hold it: one root cause, three
  instances, two benign and one dangerous.

  **One property found a defect the examples had already filed** (the `inf` case), which is
  the point of having both. One property was itself **wrong** and was corrected rather than
  the parser: it guessed a field's type from its key's spelling and called `bms_fw_rev`
  (deliberately the string `"48 (993 banka)"`) a defect - the same class of mistake the
  parsers were making.

- [x] **The five remaining parser root causes.** *(all closed; each reproduced first,
  each mutation-checked 4/4, and every one verified inert on the real captures.)* All were
  fuzz-reachable only, and all produced a value that is **not detectably wrong
  downstream** - which is why they were worth closing rather than leaving filed.
  - `_state_val` returned `""`, so `{"kickstand": ""}` was a present key holding no
    answer. `None` now, matching the `num()`-backed rails in the same block.
  - `_cell_index` accepted any integer, so "which cell is weakest" could be answered with
    a 23-digit number. Bounded, and a digit run longer than `int()` will take returns
    `None` rather than raising.
  - `parse_odometer`/`top_speed_mph` read the **tail** of a split digit run: drop one byte
    and "6249 km" arrives as "62 49 km", reading 49. A hundred times off and entirely
    plausible as an odometer, which is exactly what made it worth refusing. A number with
    another digit run immediately before it is now refused.
  - `real_temps` raised `TypeError` on None, a string or a number. It tolerates whatever
    it is handed now; no caller can produce those and the promise is not conditional on
    that staying true.
  - The `-100 C` sentinel on ride/charge samples is left **deliberately open** and is now
    the only one outstanding - see below.

- [ ] **The `-100 C` sentinel on ride and charge samples.** `parse_bms` drops it via
  `real_temps()`; `_mode_samples` does not, on any of its four temperature fields.
  **Latent, not active:** all five event logs in the real captures were scanned and zero
  riding or charging lines carry `-100`. Same shape as the `real_temps` guard itself -
  correct, and inert until a consumer meets the input. Left open rather than closed
  because the fix wants a decision about what a sample with an unpopulated sensor
  *means* (drop the field, drop the sample, or keep it and let the consumer filter),
  and nothing in the corpus forces the answer.
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
