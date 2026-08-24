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

**State:** v0.24.0 released 2026-08-23 (tag `12f0b65`, public, three assets). One
commit local and unpushed (`a646b7c`, the listen-capture fix) pending the v0.24.1
bundle below. Suite 588 passed (~8 min); the skip count flickers 0–2 run to run —
that is item 8, now with direct evidence. 18 mutation entries, 18/18 caught. Three
real captures, one bike, newest 2026-08-19 — **no v0.23.x-or-later build has ever
touched real hardware**. The whole sprint was adversarially reviewed on 2026-08-24
(four lenses, 21 findings: 4 fix-now, 8 filed, 9 clean); everything below reflects
that review.

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

### 2. ~~Capture-format stamp~~ — **shipped** (`a199424`, listen fix `a646b7c`)

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

### 3. ~~Cut the release — v0.24.0~~ — **shipped** (tag `12f0b65`, public)

Tagged after CI went green on both OSes; three assets verified on the release.
CI earned its keep on the way: the first attempt failed a timing-critical
truncation test because the consent journal's file I/O sat ahead of the 0.3 s
pre-write drain — passed 12/12 locally on a fast machine, failed on a loaded
runner. The record moved inside the lock, immediately before `port.write()`
(`bef9984`), which is *tighter* than the promise required.

### 3a. ~~v0.24.1 — the sharing path, hardened~~ — **shipped** (`a6ddc21`)

The post-release review found two defects in the public binary and two in the
mutation runner. All four are small; land them as one bundle, then push
(`a646b7c` is already local), bump, tag v0.24.1 after CI.

**(a) `redact`'s refusal escapes as a traceback (CLI) and as silence (GUI).**
The not-a-capture guard raises `ValueError`; `cli.py:297-303` catches only
`FileExistsError`/`RuntimeError` (raw traceback), `gui.py:1346-1355` only
`RuntimeError`/`OSError` — in a frozen windowed build the menu item *does
nothing at all*. Honest history: the two pre-sprint `ValueError` refusals
(export-into-self, identifier-in-name) had the same hole; the sprint widened
the trigger from pathological destinations to any wrong source folder.
- Fix: catch `ValueError` at both sites — CLI exit 2 with the message, GUI
  `showerror` with it. Tests: CLI on a non-capture folder → exit 2, stderr
  carries "does not look like an OpenMBB capture", no traceback; GUI handler
  under the existing messagebox-interception pattern. Mutations: drop each
  new catch → its test fails.

**(b) `journal_consent` masks the consent text but not the command.** The
commit claimed "masked like everything else on disk"; an arg-carrying heavy
command (`eventlogdump 5` is an anticipated raw-box shape) holding a
registered secret reaches `writes_journal.txt` in the clear — and `redact`
copies that journal into "verified clean" bundles, since a password is not a
PII shape. Reproduced by the review.
- Fix: `self._mask(str(cmd).strip())` in `journal_consent`. Test: register a
  redaction, run a heavy command carrying it, assert `****` in the journal.
  Mutation: drop the mask (manifest entry exists for the consent side —
  extend it).

**(c) The mutation runner counts ANY nonzero pytest exit as CAUGHT.** A
misspelled or later-renamed test id ("no tests ran", exit 4) reads as a
perfect catch — "a check that could not run must never read as a pass",
violated inside the tool that enforces it. Reproduced.
- Fix: precheck each entry on the clean tree (named test must PASS, exit 0,
  else report ERROR not CAUGHT); after mutating, CAUGHT requires exit 1
  specifically (tests ran and failed); anything else is ERROR. Self-check:
  an entry naming a nonexistent test must report ERROR.

**(d) Runner hardening, same commit:** validate multi-edit entries at load
(both tuples, equal length — `zip` currently truncates or pairs characters);
preserve each file's original newline style on restore (unconditional
LF→CRLF dirties an LF checkout — this is a public repo with the whole
platform matrix in CI).

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

### 5. ~~The report/GUI mirror~~ — **shipped** (`813e2d5`)

**The mirror, measured (2026-08-24):** 14 facts are composed on both surfaces
(`report._condition_lines`/`_charging_lines`/`_consumption_lines` vs
`gui._render_condition`). Two are genuinely shared already (`fault_span`,
`fault_detail`, `module_failure_note` — the model to extend). The rest are
duplicated, and the two that bite are not the numbers but the **predicates**
and the **caveats**:

- **Confirmed semantic divergence — the taper caveat.** The report prints it
  *conditionally* (`if not ch.get("taper_resolvable")`, report.py ~565), so a
  log that ever resolved a taper would silence it correctly. The GUI prints it
  *unconditionally* beside charge current. `taper_resolvable` is hardcoded
  False today (condition.py:376), so both are currently truthful — the
  divergence is latent and bites the day `assess` learns to see a taper.
- **Duplicated predicate — the cell-floor row.** `floor and (not sag or
  floor["source"] != "riding samples")` appears verbatim on both sides. Change
  one and the surfaces disagree about *which rows exist*, not just wording.
- **The UNMEASURED sentence is pinned on one side only.** The coverage-limit
  mutation test asserts on report text; the tab's copy of the project's
  highest-stakes sentence has no test and can drift or vanish unnoticed.

**The design — composers, not a renderer.** The surfaces legitimately differ
in shape (prose lines vs label/finding/tag rows); unifying layout is not the
goal and would fight both. What must be single-sourced is the *decision* and
the *sentence*. Add small pure functions in `condition.py` (no imports of
report/gui), each returning `None` when the fact should not appear:

1. `taper_note(ch)` → str|None — kills the confirmed divergence first.
2. `coverage_note(cov)` → str|None carrying the UNMEASURED sentence; surfaces
   do their own wrapping. Move the existing mutation test onto the composer so
   it covers both surfaces at once.
3. `show_cell_floor(a)` → bool — the duplicated predicate, moved once.
4. `capacity_caveat()`, `range_caveat(rng)`, `full_hold_note(ch)` — the three
   remaining load-bearing sentences ("index, not capacity", "upper bound on
   what the gauge implies", "time at full ages a pack").

Then the test that keeps it fixed: **one structural cross-surface test** that
runs `assess()` on a synthetic log, renders both surfaces headlessly, and
asserts the *set of facts shown* matches a single expected list. That is the
test that catches "row exists on one surface only" forever.

**Mutations:** inline any one composition back into a single surface → the
cross-surface test fails; flip `taper_resolvable` handling on one side → the
taper test fails. **Not in scope:** unifying number formatting (cosmetic;
unify opportunistically), redesigning either surface. ~half a day.

### 5b. ~~Review fixes for the items 5+8 stack~~ — **shipped** (`3ad636e`)

The unpushed stack was adversarially reviewed 2026-08-24 (three lenses landed;
the fourth stalled and its ground — conftest import mechanics, 3.12-vs-3.14
surface, commit-count fidelity — was re-verified by hand). **The core holds:**
a fact-by-fact diff of both surfaces at HEAD vs v0.24.1 on all three real
captures plus sim shows *zero semantic shift*, the slice/token/wrap fears were
each checked and refuted, and the truncation rework is genuinely race-free.
Five findings block the push; all are small and fully specified:

1. **The retry leaks the half-built Tk root** (reproduced). `build_tk_or_fail`'s
   failed first build is never destroyed; it stays pinned as
   `tkinter._default_root` — which `dialogs.py:89` uses as the parent for real
   dialogs — and the leak is the *exact* mid-build-collection mechanism the
   retry exists to recover from. Fix: in the except branch, before the collect,
   destroy `tk._default_root` if it exists (wrapped in try/except TclError).
   Test: a build that fails partway leaves `_default_root` None afterwards.
2. **"The retry announces itself" is currently false** (reproduced). The
   `print()` runs inside a passing test, where pytest capture discards it.
   Fix: `warnings.warn(...)` instead — warnings surface in the terminal
   summary even for passing tests, which is the honest channel. Update the
   `capsys` assertion in test_display_honesty to `pytest.warns`.
3. **The terminal-summary probe fails silent** (established). Its bare-Tk probe
   is precisely the intermittent fault this item chased; on any exception it
   returns without a word, silencing the guard. Fix: except → write one line —
   "could not re-check the display; the skips above may or may not be honest"
   — uncertainty must be loud, not absent.
4. **The mirror test is vacuous for two of its six facts** (reproduced twice —
   found independently first-hand and by the lens). `_mirror_log` never
   produces `held_full_h` (needs an "Entering Charge Standby Mode" + a
   disconnect/riding line after samples at full; it stops charging at 92%) and
   never exercises the cell-floor row as *present* (always has decodable
   riding MinCell, so sag exists and the floor is suppressed). Extend the log
   — or add a second log — so all six facts are exercised **present**, then
   add the two missing manifest entries (tab loses the full-hold sentence; tab
   loses the floor row) that could not be written before because nothing could
   catch them. The `_mirror_log` docstring ("every fact") becomes true instead
   of aspirational.
5. **Two hygiene nits from the same lenses, cheap enough to fold in:**
   `report._wrap` keeps `break_on_hyphens=True`, so the taper sentence renders
   "whole-\namp" with a mid-word break — pass `break_on_hyphens=False`; and
   the page-phrase assertions around `tests/test_condition.py:539-544` (and
   the coverage test's page half) assert multi-word phrases against the
   *unflattened* wrapped page — flatten first, per the discipline the item-5
   commit itself stated.

Then push; CI (which has seen none of this) judges the stack whole.

**Filed, not fixed (from the same review):**

- **`coverage_note` asserts a cause it has not established.** "carry a value no
  cell can hold (records written before a firmware change...)" is true of all
  502 on the reference bike, but a record can also lack a cell reading
  entirely, and the sentence would then claim a mechanism the data does not
  show. Wants the composer to distinguish value-no-cell-can-hold from
  no-reading-at-all counts. Pre-existing wording (shipped in v0.24.0), so a
  plan item, not a push blocker.
- **Module-level skips bypass `pytest_runtest_logreport`** — a raw
  `pytest.skip(..., allow_module_level=True)` would evade the display-lie
  summary. And **the summary is advisory**: it cannot turn the exit red, so on
  CI a reintroduced lie still exits 0. Both are guards-of-guards; note them on
  item 8's ledger rather than build a third layer now.

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

### 8. ~~Test-estate hygiene — the skips are lying~~ — **shipped** (`17bf5ff`)

The cause was never the display. Letting the real error surface named it in one
run: `_tkinter.TclError: invalid command name "tcl_findLibrary"`, raised by a
**bare `Tk()`** in test_theme's fixture after test_gui_flow had built and
destroyed the application a hundred-odd times — a Tcl interpreter finalized by a
previous test's variables being collected mid-build. Intermittent (forty
build/destroy cycles in isolation do not reproduce it), which is exactly why it
hid: an intermittent skip looks like a quiet machine.

The display question is now asked once per session by a bare `Tk()`, which
touches no application code and so can only fail for the reason being asked
about. After that a `TclError` is a defect: one announced retry (the cause is
known and a forced collect clears it), then a failure carrying both errors.
`tests/conftest.py` holds it; a terminal-summary check guards against a raw
`pytest.skip("no display...")` being reintroduced somewhere new.

The truncation test was the same fault in another costume: `TimedPort` starts
its clock at construction, so its 0.2 s chunks raced the ≤0.3 s pre-write drain
that *discards* what it reads, leaving ~0.1 s of slack. `ArmedTimedPort` already
existed for this and says so in its docstring — delays from the first **write**,
so the flush sees an empty wire by construction. 15/15 consecutive runs.

**605 passed, 0 skipped** — the first run where zero is structural rather than
lucky.

**What the mutation runner caught on the way, worth remembering:** two of the
new tests could be silenced by the very fault they tested for. Turning
`pytest.fail` into `pytest.skip` inside the helper did not make them red — it
made them *skipped*, because `pytest.raises(fail.Exception)` does not match
`Skipped`, so the skip escaped and swallowed the test. A test that asserts "no
skip happens here" may not let a skip escape, or the skip is what reports the
result. All three now catch it explicitly.

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

### 10. One decoder for everything that reads a capture

The sprint gave `redact` a BOM-aware decoder and the loaders none, so the two
halves of the tool now disagree about what they can read (review, reproduced):
a UTF-16 `session_meta.txt` whose stamp says `capture_format: 2` is read as
**absent** → format 1 — a key present but invisible, the exact false pass the
stamp exists to prevent; and a UTF-16 capture `redact` now recognizes as
first-class yields **zero commands** in `analyze`, because `_header_command`
decodes UTF-8 only.
- **Change:** one shared BOM-sniffing read helper in `sessions.py`, used by
  `_header_command`, `_header_time`, `_session_date`, `capture_format`, and
  the baseline read. `redact._decode_text` is the model. Mutation: force the
  helper back to UTF-8-only → a UTF-16 fixture capture loses its commands.

### 11. A refused capture must be visible everywhere it is absent

Three surfaces now silently omit what they cannot read: `library.scan` (filed
under item 2), and both Charts trend loaders (`gui.py:5360`, `gui.py:5446` —
`except Exception: pass`), which present "pack over time" with an undisclosed
hole. The review reproduced it: one format-1 and one format-2 capture in the
root → one trend row, zero user-visible anything. (The sprint commit's claim
that GUI external-folder sites "already wrap it with a message box" was
overstated — the dialogs do, the loops don't.)
- **Change:** catch `CaptureFormatError` distinctly in all three loops, count
  it, and surface one line ("1 capture not shown — written by a newer
  OpenMBB"). Reachable only via future-format or damaged meta today, which is
  why it is filed rather than in the v0.24.1 bundle.

### 12. Sessions that never get a meta file never state their format

The stamp writer lives on the full-pull path (`gui._write_session_meta`), so
listen captures, connect-without-pull sessions, and selftest sessions carry no
`session_meta.txt` at all — the insurance excludes exactly the capture shape
`a646b7c` promoted to first-class. Absent-means-1 keeps them readable today;
on the day the format bumps, an unstamped listen capture from the NEWER
version would be read as format 1 by an older tool.
- **Change:** `SessionLogger` writes the minimal stamp (`capture_format`,
  `time`, `app_version`) when the session directory is created; the pull path
  keeps enriching it with firmware/power-mode as now (which also intersects
  item 9 — both are "move the record down to where every caller passes").

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
