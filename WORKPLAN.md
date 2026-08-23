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

### 1. Fix `--selftest`: it crashes at HEAD ⚠ *blocks the release*

Commit `7479947` moved the contactor gate into `transport.exec_command`
(`heavy_consent` required for `HEAVY_COMMANDS`) and updated every caller — except
`cli.py:72`, where the selftest's own `dumpall` check now raises an uncaught
`BlockedCommandError`. The run dies at "dump with progress:", never reaching the
write-flow, validator, session-file, or **frozen-pyserial** checks — and that last one
exists precisely because a missing pyserial otherwise shows only as an empty COM-port
list *at the bike*. The selftest is the pre-bike-day check, and it is broken.

Root lesson: the selftest has no test. Its only coverage is that `--selftest` appears in
`--help` output.

- **Change:** pass `heavy_consent="selftest: simulator only"` at `cli.py:72`. Then add
  `tests/test_cli_selftest.py`: run `openmbb --selftest` as a subprocess, assert exit 0
  **and** that the output contains `SELFTEST PASSED` and the section headers after the
  crash point (`write flow:`, `frozen deps:`), so a future mid-run death cannot hide.
- **Mutation:** remove the `heavy_consent` argument → the new test fails.
- Unreleased, so no user has the broken build; nothing runs at a bike until this lands.

### 2. Capture-format stamp — design settled, ~1–2 h *(fold into the release)*

The investigation completed; the plan never absorbed it. The stamp **does nothing
today** (every capture in existence is format 1) — it is forward-only insurance, priced
accordingly, and the design is final:

- `capture_format: 1` as line 2 of `session_meta.txt`. Bare integer, no minor — the
  reader has exactly one decision (read or refuse), and a second version-shaped string
  nobody compares would sit three lines above `app_version`, which is already one.
  Verified: the value round-trips `redact` unchanged.
- Constant `CAPTURE_FORMAT = 1` at the top of `sessions.py`, beside the loader that
  enforces it (the `SUMMARY_VERSION` pattern). The comment carries the bump rule — bump
  **only** when an old reader would produce a *wrong* answer rather than a *missing* one,
  which is exactly six things: whose clock `# time:` records; the folder-name stamp
  shape; `NNN_` latest-wins semantics; which `settings_baseline*` is authoritative; the
  `_sim`/`_listen` tags; what `# command:` means as a dict key. A new file, key, or
  sidecar is **not** a bump. Raising the constant obliges, in the same commit, either
  the branch that reads the old format or the sentence that refuses it.
- Reader (`load_session`): absent → format 1 (every existing capture, proven against all
  three real ones). Known integer → read. Newer integer or malformed/empty value →
  **refuse loudly** with wording that says which version and which program is older — a
  folder claiming a version we cannot read is a could-not-run case, and those never read
  as a pass. Read with a line-anchored `^[ \t]*capture_format:(.*)$` then strip; a bare
  key with an empty value is malformed, not absent.
- **Mutations:** absent-stamp folder refused → back-compat test fails; newer-stamp folder
  read anyway → refusal test fails.

Two incidental defects from the same investigation, same commit or adjacent:

- `redact.redact_session` will vouch for a folder that is not a capture (no
  `# command:` files at all → `verified_clean: True` over nothing). Refuse instead.
- `openmbb analyze`'s has-data guard (`cli.py` ~208) accepts `has_settings` matched by
  *filename*, so a capture whose command headers are all unreadable still prints a
  report and exits 0. Guard on parsed content.
- Record as a known limitation (decide later, don't fix blind): a second pull into a
  live folder overwrites `session_meta.txt` wholesale, so the meta describes the last
  pull while the files describe both.

### 3. Cut the release — v0.24.0

After items 1–2. In it: the fault counts anchored to real log entries and clearings no
longer dating faults (`da499fb`), the unmeasured-stretch disclosure and baseline-sort
fix (`d49fa37`), the write-chain and contactor gates in code (`7479947`), the version
age stamp and `## Privacy` (`56ae56f`), the parser tolerance work (`e734f23`,
`99b2c07`), fixtures (`ea9ac5c`), find/limits (`ac04b2d`), health/distance (`1028fc3`),
sentinel/BattTemp (`f4a6e84`), module-connect refusal (`e888f5f`). Same sequence as
v0.23.x: push → CI green → tag → verify assets.

### 4. Encode the mutation-check discipline as a repo artifact

Sixteen commits say "mutation-checked N/N" — **52+ named mutations** — and every one was
run from a scratchpad script that no longer exists. The practice that provides this
project's evidence standard has no artifact: a future refactor that quietly weakens a
guard is caught by nothing, and the deferred `gui.py` split is precisely the refactor
that would need these re-run.

- **Change:** `tests/mutations.py` — a manifest of entries
  `(label, file, old_string, new_string, must_fail_test)` (the exact shape every
  scratchpad script already used; the 16 commit bodies are the source to reconstruct
  from), plus a runner: apply one mutation, run the named test, assert it fails,
  restore. Not part of the default suite (it edits source); a separate opt-in command
  documented in the file header, run before releases and after refactors.
- **Mutation of the mutation runner:** point one manifest entry at a test that passes
  regardless → the runner must report it as NOT CAUGHT.

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
