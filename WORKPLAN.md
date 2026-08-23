# OpenMBB — work plan

Reassessed 2026-08-23 at `d49fa37` by a full-repo review (four lenses: plan-vs-code,
structure, test estate, hardware-blocked backlog). The previous plan's tiers served their
purpose and are retired: Tiers 1–2 closed in full, Tier 3 closed to one hardware-blocked
item. History now lives where it always really lived — the git log, whose commit messages
carry the full reasoning for every shipped item. This document is the part that has to be
current: what to do next, what needs a motorcycle, and what was deliberately not done.

**Working arrangement:** review and planning happen in Fable sessions; implementation in
Opus sessions. Queue items below are scaffolded for direct pickup — files, the change,
the tests, and the mutation each test must catch.

**State:** v0.23.1 released; **11 commits of user-visible work unreleased since** —
including two live-defect fixes users should have. Suite 562 passed (~8 min), CI green
both OSes. Three real captures, one bike, newest capture 2026-08-19 — which predates
v0.23.0, so **no v0.23.x build has ever touched real hardware**.

**The organising constraint, sharpened:** only one motorcycle has ever been measured, and
the code has now largely caught up with what one bike's data can teach. The next unit of
progress is **data, not code** — see *At a bike*. Code items below either prepare for
that data or protect what exists.

---

## Now — the implementation queue

### 1. ~~Fix `--selftest`~~ — **shipped `8fa44c4`**

Fixed, plus the cause underneath it: nothing in the selftest ever exercised the
heavy gate, so the caller that had not been updated broke a check nobody was
checking. It exercises it now, first and before the read it guards.

It also turned up a promise the gate was not keeping — `heavy_consent` was
documented as "journalled into the session before the first byte goes out" and
was journalled nowhere. `SessionLogger.journal_consent` now writes it, before the
wire, masked. That was load-bearing for *At a bike* step A.2, which asks you to
confirm the consent string reached the journal; it would have failed there.

### 2. ~~Capture-format stamp~~ — **shipped** (this commit)

`capture_format: 1` is line 2 of every new `session_meta.txt`; `CAPTURE_FORMAT`
and `capture_format()` live in `sessions.py` with the six bump triggers written
where the constant is; `load_session` refuses a format it cannot read *at the
loader*, so nothing can analyze what it could not load. Absent means 1, proven
against all three real captures — written by v0.10.1, v0.19.1 and v0.21.0, three
app versions and one format, which is the distinction the stamp exists to make.

Both incidental defects went with it: `redact` refused to vouch for a folder it
could not recognize as a capture (its clean bill only ever scanned for
*motorcycle* identifiers, so on any other folder it reported clean over names and
addresses it never looked for), and `has_settings` now means settings that
**parsed** rather than a filename that matched — `openmbb analyze` was printing a
report and exiting 0 over a capture whose headers were all unreadable, and to a
script driving `--fail-on-alert`, exit 0 means "all good".

**Two follow-ups it deliberately left open:**

- **The library drops a capture it cannot read instead of showing it.**
  `library.scan` catches every exception and skips the folder, so a future-format
  capture would silently vanish from the session library rather than say why —
  "your capture is gone" instead of "this copy of OpenMBB is too old to read it".
  The row state already exists (`read_failed` → `"unreadable"`, *"tried, failed -
  never a quiet pass"*); it wants `scan` to distinguish `CaptureFormatError` from
  junk and pass a reason through `_lib_row_values`. Left for a separate commit
  because it can only trigger against a capture from a **future** release, which
  cannot exist yet — so it costs nothing to land after v0.24.0.
- **A second pull into a live session folder overwrites `session_meta.txt`
  wholesale** (`save_named` opens `"w"`), so the meta describes the last pull
  while the `NNN_` files describe both. Recorded, not fixed: the right answer
  (append a second stamp block, or refuse, or leave it) wants a decision about
  what one folder holding two pulls *is*, and nothing forces that answer yet.

### 3. Cut the release — v0.24.0

Ready now — items 1 and 2 are in. In it: the restored pre-bike-day selftest and
the consent record (`8fa44c4`), the capture-format stamp and the two vouching
refusals, the fault counts anchored to real log entries and clearings no
longer dating faults (`da499fb`), the unmeasured-stretch disclosure and baseline-sort
fix (`d49fa37`), the write-chain and contactor gates in code (`7479947`), the version
age stamp and `## Privacy` (`56ae56f`), the parser tolerance work (`e734f23`,
`99b2c07`), fixtures (`ea9ac5c`), find/limits (`ac04b2d`), health/distance (`1028fc3`),
sentinel/BattTemp (`f4a6e84`), module-connect refusal (`e888f5f`). Same sequence as
v0.23.x: push → CI green → tag → verify assets.

### 4. ~~Encode the mutation-check discipline as a repo artifact~~ — **shipped**

`tests/mutations.py` — a manifest of `(label, file, old, new, must_fail_test)`
entries plus a runner. Not part of the default suite (it rewrites files under
`src/`), and it refuses to run against a dirty working tree so that
`git checkout -- .` is always a complete recovery if it is interrupted between
the write and the restore.

    python tests/mutations.py            # all entries
    python tests/mutations.py redact     # entries whose label matches

It reports three outcomes, not two. **CAUGHT** is the pass. **MISSED** means a
test stayed green while its own fix was removed — a finding, not a pass, and the
reason the entry exists at all. **STALE** means the anchor no longer matches the
source: the entry has stopped meaning anything and says so instead of being
silently skipped, which is how a manifest rots.

**Seeded with the 17 mutations from `8fa44c4` and `a199424`**, each verified at
the commit that introduced it. The ~52 named in earlier commit bodies are prose
and are NOT transcribed on faith — an entry nobody has watched fail is worth less
than no entry, so they get added as they are re-established. That backfill is
incremental and needs no ceremony.

### 5. The report/GUI mirror — stop composing the same fact twice

Structure lens, ranked first for will-bite: the GUI Condition tab hand-composes ~15 of
the same facts `report.py`'s `_*_lines` renderers compose independently
(`format_report`'s own docstring: *"Mirrors what the Analyze tab shows"*). One latent
divergence already exists (the taper caveat wording), and one past drift of this class
was already fixed by sharing composition (`fault_span`/`fault_detail`). The old plan's
claim that "the saved page and the Analyze tab cannot drift" oversold — the sharing that
exists is page↔CLI; the tab is a separate hand-maintained surface.

- **Change:** extend the `fault_span` pattern — small shared composers in `condition.py`
  / `report.py` that both surfaces call, starting with the facts that carry caveats
  (taper, range extrapolation, coverage limit), since a drifted caveat is a broken
  promise, not a cosmetic mismatch. Not a refactor of either surface; composition only.
- **Test:** for each shared fact, one test asserting the tab row text and the report
  line derive from the same composer output. **Mutation:** inline one composition back
  into `gui.py` → fails.

### 6. Parse `sevcon` — a controller fault must surface in an inspection verdict

The old plan's Tier-1 item was ticked with "(sevcon/chargers/bluetooth still unread)" in
its own note, and it stayed unread: `parsers.py` has no `parse_sevcon`. The plan's own
words call the controller "the second most expensive part on the bike" — and on a
bike-day inspection, a Sevcon fault would not surface in the verdict today. A redacted
`rev41_sevcon.txt` fixture already exists to build against.

- **Change:** `parse_sevcon` (temperature, fault state, serial already redact-mapped);
  a Health row; presence/absence grades like `obd`, measurements do not.
  **Mutation:** the fixture-interrogation pattern — remove the parse → the named
  fixture test fails.

### 7. Golden transcripts and richer port fakes (~1.5 days)

As previously filed, one claim corrected by the review: the silent clamp is *not*
entirely untested — a GUI-level clamp-warning test has existed since v0.20.0. The real
gap is **port-level**: no fake replies `SUCCESS` and reads back the old value *through
the transport*, and the truncation/`_resync` paths are unreachable in the sim by
construction (`SimPort._respond` always appends a prompt). Clamping port, no-prompt dump
port, paced port (3,840 B/s); transcript + normalised-folder goldens.

### 8. Test-estate hygiene — the skips are lying, mildly

The suite's "skipped" tally is not the benign no-display skip it claims: on a machine
*with* a display, a floating subset of GUI tests (varying run to run) skips with "no
display available for Tk" because `build_gui` intermittently throws `TclError` after
many app builds and the fixture mislabels every `TclError` as no-display. Green that
means less than it appears — this project's least favourite kind.

- **Change:** the fixture distinguishes "Tk genuinely unavailable" (skip, honestly
  labelled) from "TclError on the Nth build" (fail, or retry-once-then-fail); plus a
  session-end check that on a display-bearing machine, zero tests skipped for display
  reasons. Note for scheduling, not action: `test_gui_flow.py` is 72% of suite runtime
  (336 s of 464) at 2.05 s/test — structural (real 0.1 s drains per command even on
  SimPort), tolerable, documented here so nobody rediscovers it.

### 9. Journal a write at the transport, not only in the GUI

Found while fixing item 1. `journal_write` is called only from `gui.py`, so a
write driven through `transport.write_setting` by a script or a REPL reaches the
wire with **no record at all** — the same shape the contactor gate had before
`7479947` moved it down. The enforcement is already in the right place (value
validation, blocklist, and the name-must-exist-in-the-live-dump check all live in
the transport); only the *record* is still GUI-side.

Not a broken promise, which is why it was filed rather than folded into item 1:
the README passage describing "journal the intent before it hits the wire" is
explicitly walking through the GUI's write flow, not `write_setting`'s contract.

- **Change:** move the PENDING/VERIFIED journalling into `Transport.write_setting`
  (where `journal_consent` now sits), and have `gui.py` stop doing it itself so
  one write cannot produce two records. **Mutation:** journal only in the GUI →
  a headless-write test finds an empty journal.

---

## At a bike — the data-acquisition plan

Everything hardware-blocked, consolidated from six scattered locations (including the
extraction plan's bike-validation steps, which were otherwise recorded nowhere).
**Contactor events are a budget**: each heavy read can open the drivetrain contactor and
writes a permanent `Line Contactor o/c` error-log entry. A standard pull (incl. the
~1 KB `errorlogdump`) is contactor-free and needs no login. Cheap information first.

### A. The 2017 FXS — shakedown *(next visit to Ron's own bike)*

Nothing since v0.22.x has touched hardware. One visit, one contactor event, in order:

1. **Standard pull with the current build** — validates the v0.23.x read path end to
   end. Zero contactor cost.
2. **One event-log read** (1 contactor event, ~5 min at 38400) — validates the 45 s
   idle window (expect the completeness *note*, not the TRUNCATED banner), confirms the
   `heavy_consent` string landed in the session journal, and is the **4th trend point**
   for the charge-index degradation curve (17.92 / 17.92 / 17.83 Ah so far).
3. **Benign write + revert** (`spfront` → same value; ~5 min, zero contactor, needs
   login) — field-checks that the strengthened four-link write chain doesn't
   false-block a legitimate write.
4. *Opportunistic:* if any read fails naturally, run the next command anyway and keep
   the capture — that is the `_resync` field test, otherwise covered by queue item 7.
5. *Calendar-gated:* any `bms` read **after 1 Nov 2026** (level 0, zero cost) settles
   whether the −7 h clock rendering follows US DST (−8 h after the change).
6. *Someday:* a deliberate ride below the 13% logged floor answers what displayed 0%
   means; shares its contactor event with the next routine capture. Costs riding time,
   not budget.

### B. The 2016 FXS inspection *(the planned visit)*

The inspection flow is field-ready: listen-only cable test, read-only connect,
login-free pull, heavy read behind the owner-consent confirm. Sequence = the flow's own
six steps. What the data buys:

- **The free pull** (zero contactor, no login) settles four open questions at once:
  `set`/`help` ground truth for the safety layer on a second firmware; whether
  module-connect failures correlate with thermal disables on a bike that is not Ron's
  (the rename question); dump termination behaviour on other firmware; whether −100 °C
  appears on its riding/charging lines (the sentinel-semantics decision).
- **The one consented event log** (1 contactor event, owner's explicit OK — the flow
  will not let you skip asking) buys *different things depending on firmware*: an
  old-dialect bike ground-truths `BattTemp:` (single value vs the `bms` per-module
  temps minutes apart → max vs mean vs single sensor); a modern one provides
  calibration samples for `derate_profile`/`cell_sag`. Caveat now recorded: rev-12-era
  MinCell is a stuck constant, so `cell_sag` may be uncalibratable there regardless —
  the mail-in below may be the better calibration source.
- Afterwards: **Export share-safe copy** before the capture leaves the machine.

### C. The issue-#1 reporter — a mail-in, zero cost

A redacted capture from the Czech bike serves calibration, and after 1 Nov settles DST
independently. Blocked on exactly one thing: **the reply draft exists and has never been
posted.** Posting it is Ron's, not Claude's.

---

## Open decisions, not tasks

- **The −100 °C sentinel on ride/charge samples** — latent (zero occurrences in 5 real
  logs); the fix needs a semantics decision (drop field / drop sample / consumer
  filters) that only a real dead-sensor capture forces. Visit B may provide one.
- **"Module connect failures" rename** — 100% thermal-correlated on one bike; renaming
  on one machine's correlation is not this project's move. Visit B provides the second
  bike.
- **What displayed 0% means** — A.6.
- **DST** — A.5 or C, calendar-gated past 1 Nov 2026.
- **`session_meta.txt` on a second pull** — see queue item 2's limitation note.

## Deferred

- **Split `gui.py`** — planned in full, deliberately not started; reasoning in commit
  `5258b3e` (headline: the urgent parts were plain bugs, since fixed; the unlock is
  speculative here; the file barely shrinks). Counts refreshed 2026-08-23: 5,896 lines
  (was 5,817 at deferral) and the three most-entangled methods each grew — the file is
  still accreting. One **new** post-deferral fact: `_render_condition` is now ~180
  lines of pure Tk-thin rendering, which is *mirror* pressure, not serial entanglement —
  queue item 5 is the response, not reopening the split. The extraction plan's on-bike
  validation steps are preserved above in *At a bike*. Reopen when headless capture is
  actually wanted or a second person works in the file.

---

## Shipped

One line per ship, newest first; the full reasoning is the commit message.

| what | commit / tag |
|---|---|
| Unmeasured ride-log stretch disclosed; baseline chosen by timestamp not filename | `d49fa37` |
| Nine interrogated fixtures from real captures; property corpus 430→616 lines | `ea9ac5c` |
| Four fuzz-only parser root causes closed; −100 sentinel decision left open, stated | `99b2c07` |
| Parser tolerance promise honoured: MIL-ON casing, garbled decimals, qualifier labels, own-banner-as-log, non-finite floats | `e734f23` |
| Property-based measurement of the parser contract (measurement before guards) | `f1beefd` |
| Real-capture test paths resolved from HOME (was a hardcoded account path, public) | `f247ae2` |
| gui.py split deferred with reasoning; scaffolding split out | `5258b3e` |
| Write chain (4 links) and contactor gate enforced in code, not UI; one timeout table | `7479947` |
| Module-connect triple refused on arithmetic; console refusal is not a log | `e888f5f` |
| Version age stamp, `## Privacy`, no-network gate, `--version` (update check decided-not-built) | `56ae56f` |
| Fault counts anchored to logged entries; clearings no longer date faults | `da499fb` |
| `find()` word-start anchoring; bike's own thermal limits read | `ac04b2d` |
| Battery graded by hotter of counter/log; CLI distance units | `1028fc3` |
| −100 sentinel named (`real_temps`); `BattTemp` kept but not read as a peak | `f4a6e84` |
| **v0.23.1** — fault counts, fault dates, battery grade corrections | tag `v0.23.1` |
| Pack-peak provenance; ambient as disconfirmer only (rise metric measured and rejected) | `b445825` |
| **v0.23.0** — Tiers 1–2: redact, trends, cell identity, state parsers, library+notes, charging habits, condition report, consumption+range, inspection flow, 15 review fixes | tag `v0.23.0` |
| Condition check + verdict; clock offset measured; stale-record guard; SOC-gauge note | v0.22.x |
| CI on PRs; light mode fixed | v0.21.0 |
