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

- [ ] **Update check.** A quiet "0.22.1 is available". *Why:* this project shipped four
  releases in three days and its own author ran five commits behind for weeks.
  **Held for a decision, not for effort.** This is the only item in Tiers 1-2 that makes
  the program talk to the network, and the shape is an owner's call rather than an
  implementation detail: opt-in or on by default, which endpoint, and what (if anything)
  the request reveals about who is asking. The tool's users are people plugging a laptop
  into a bike's diagnostic port, and several of them care about exactly that. Everything
  else here can be built and judged afterwards; this one should be decided first.

- [x] **Session notes.** *(shipped: a note per capture, written into the capture folder so
  it travels with a copy)* Annotate a capture — "before the re-gear", "after the firmware
  update". *Why:* the 2026-06-13 reflash would have been obvious immediately if the
  capture had carried a note saying so.

## Tier 3 — more from the data we already read

- [ ] **Normalise temperature against ambient.** `AmbTemp` is parsed and unused. It
  separates a hot pack from a hot day, which is exactly the distinction the 60 C battery
  alert needs before it means anything.

- [ ] **Decode the error-log conditions.** `modv=0mV, maxv=0mV, minv=4294967295mV` is
  shown raw; that is a sentinel and two zeroes, and it means "the module did not answer".
  Say so.

- [ ] **Attempt the undecodable entries.** About 5% of event-log entries are raw hex even
  Zero's own decoder renders as `0x.. ???`. Worth one investigation to see whether any
  carry a known shape.

- [ ] **`parse_ride_log` ignores the `BattTemp:` dialect**, so pack temperature silently
  drops out of logs in that format. Needs ground truth on which firmware emits it.

- [ ] **`openmbb analyze` renders distance in km only** — the CLI has `--units` for
  temperature and no distance flag, while the GUI honours a miles preference.

- [ ] **Read `Max Charge Temp` from the bike** instead of hardcoding the same 50 C in
  `health.py`'s note. It is captured at permission level 0 and discarded, and the file
  already reads motor thresholds from the bike this way.

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
