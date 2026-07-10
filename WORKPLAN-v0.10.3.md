# WORKPLAN v0.10.3 — post-review patch (raw-box safety, PII scrub, honest text)

**Why this exists.** The v0.10.2 multi-agent review (27 agents, 2026-07-10) confirmed all
four tiers landed correctly and all 9 fix-tests discriminate — but it surfaced 22 verified
findings. One is a real safety hole that predates v0.10.2 (the **raw command box sends the
contactor-dropping heavy dumps with no warning**), one is **blocking for any push** (the
owner's real VIN + serials are committed in plaintext in three local commits), and a cluster
of user-facing text now contradicts the release's own fixes. This patch closes them.

**How this works (same protocol as v0.10.1/v0.10.2).** Fable planned; **Opus executes**.
Work tiers top→bottom. For each item: do the work, add/adjust the **Accept** test, run the
suite, tick the box, append a one-line Progress-log note. No half-checked boxes.

**Rules of engagement**
- **Never open a real COM port.** Dev/test is `--sim`, pytest, `--selftest`, `--smoketest`.
- Small, local changes; match existing style (plain %-formatting, no type annotations).
- **Every fix gets a test that DISCRIMINATES** — revert the fix, confirm the test fails,
  restore. Restore the tree clean before moving on.
- Safety direction is one-way: always OK to block/warn more, never less.
- `python -m pytest -q -p no:faulthandler` after every item (GUI tests standalone — the
  known Tk-teardown flakes are `test_login_decided_by_level_query` and
  `test_write_records_before_verify_failure`; both pass standalone).
- Commit per tier: `v0.10.3: <tier> <summary>` — **except Tier E2 (history rewrite), which
  has its own protocol.**
- **PII discipline for THIS workplan:** never copy the owner's real VIN/serial values into
  any file, commit message, or test. When a step needs them (history search), read them from
  the old commits into shell variables only. Refer to them as "the VIN" / "the serials".

**Ground truth** (real console captures, 2026-07-10 live session):
`<Documents>\OpenMBB\openmbb-sessions\2026-07-10_124738_435640_COM4\` — the review verified
the vendored fixture `tests/fixtures/rev41_postlogin_set.txt` is byte-identical to the real
`020_set.txt` except the two same-width placeholders, so the fixture can stand in for it.

---

## Tier A — SAFETY: the raw command box (the one true wire-risk finding)

- [x] **A1 (HIGH) Route raw-box heavy commands through the confirm dialog + normalize
  head classification.** Review finding SAFE-1 (verified high): `_raw_send` (gui.py ~867)
  checks only `command_blocked()`, so typing `eventlogdump` or `dumpall` into the raw box
  runs the contactor-dropping ~1 MB dump with **no warning** and `_read_cmd`'s 15 s dump
  idle instead of the heavy button's 30 s — the exact timeout that produced the live
  `### TRUNCATED` capture. Finding SAFE-2 (low): `_read_cmd` classifies dumps by exact
  full-string match (`cmd in LONG_COMMANDS`) while the transport uses the lowercased first
  token, so `Eventlogdump` / `eventlogdump 5` typed raw would get quick-read timeouts
  (2.5 s idle / 60 s max) — a guaranteed mid-dump cut. Fix both at once:
  (a) in `_raw_send`, compute `head = cmd.strip().split()[0].lower()`; if `head in
  HEAVY_COMMANDS`, route through `self._read_heavy(cmd)` so the raw box gets the same
  contactor-warning confirm and 30 s idle as the buttons (send the command verbatim —
  classify by head, don't rewrite what the user typed);
  (b) in `_read_cmd`, classify `is_dump` by the same normalized head, not exact string.
  **Accept:** GUI-flow tests — `_raw_send` with `raw_var` set to `"eventlogdump"` (and to
  `"dumpall"`) pops the confirm (monkeypatched `askokcancel` recording calls) and sends
  NOTHING when the user cancels; a variant like `"eventlogdump 5"` typed raw is classified
  dump-class (capture the `idle_timeout` passed to `transport.exec_command` via monkeypatch
  and assert ≥ the heavy value). Verify discrimination on both (a) and (b).

- [x] **A2 (MEDIUM) Pin the FULL BASELINE sequence with a GUI-level regression test.**
  Review finding REL-4 (verified): nothing tests what `_baseline` actually SENDS — the
  reviewer patched the gui-side list and re-added `eventlogdump` to the baseline and the
  whole suite stayed green (only the transport-constants test exists, which that edit
  doesn't touch). The v0.10.2 A2 acceptance criterion promised this test; it was never
  written. **Accept:** a GUI-flow test that monkeypatches `app.transport.exec_command` to
  record every command, runs `_baseline`, and asserts: no member of `HEAVY_COMMANDS` was
  sent, `"dumplogs"` was not sent, and `"errorlogdump"` + `"set"` WERE sent. Verify
  discrimination by appending a heavy command to the seq in `_baseline` → test fails →
  restore.

## Tier B — PII scrub (**BLOCKING: nothing gets pushed/published until Tier B + E2 are done**)

Review findings PII-1 (verified medium — repo is private and un-pushed, so exposure today is
zero, but a routine push of this branch publishes the identifiers in tree AND history) and
PII-2 (note — local username in absolute paths across tracked docs).

- [x] **B1 (HIGH-priority hygiene) Remove the PII literals from the two no-PII guard
  tests.** `tests/test_rev41_fixture.py` embeds the real VIN + 3 serials as literals at
  ~line 35 (`test_fixture_carries_no_pii`) and ~line 146 (`test_postlogin_fixture_carries_no_pii`
  — added by v0.10.2 itself). Replace both with checks that (1) assert the placeholders ARE
  present (`REDACTEDVIN000000`, `REDACTEDMBB00`), and (2) assert no token matching the
  identifier SHAPES appears other than the placeholders:
  `re.findall(r"\b[0-9A-HJ-NPR-Z]{17}\b", text)` must yield nothing (the placeholder
  contains `I` so it can't match a VIN-charset token), and `re.search(r"(?i)sj\d{4}zer\d{4}", text)`
  and `re.search(r"(?i)17gb\d{4}", text)` must be None. **Accept:** both tests still pass on
  the clean fixtures; planting a VIN-shaped token in a fixture copy makes the shape check
  fail (prove in a temp file, not by editing the fixture). No real identifier remains
  anywhere in `tests/` (`git grep` of the shapes over tracked files).

- [x] **B2 Scrub the tracked docs.** The real identifiers sit in `WORKPLAN-v0.10.md`
  (~lines 64-65, a pasted chunk of the real `set` dump, and ~line 161) and
  `docs/review-2026-07-09.md` (~line 15). Replace the identifier values with the same-width
  placeholders used by the fixtures. Also (PII-2) replace absolute Windows
  user-home paths (the `Users\<name>\...` form) with `<repo>`/`<Documents>`/`<home>`
  placeholders across ALL tracked docs
  (`WORKPLAN-v0.10*.md`, `docs/*.md`) — enumerate with a grep for the drive-letter pattern.
  (Default decision: scrub in place and keep the docs tracked. Alternative — untracking the
  process docs entirely — only if scrubbing proves messy; note which was done.)
  **Accept:** `git grep` over tracked files for the three identifier shapes (B1 regexes) and
  for `C:\\Users\\` returns nothing (excluding this workplan's own regex definitions if any).

- [x] **B3 Add a repo-wide PII gate test.** New test (e.g. in `tests/test_release_gate.py`):
  walk `git ls-files` (text files only, skip binaries), apply the three shape regexes from
  B1, allowlist the placeholders, assert zero matches. This makes the release gate catch a
  future paste of a real capture. Tune until it has zero false positives on the clean tree.
  **Accept:** test passes on the scrubbed tree; temporarily planting a VIN-shaped token in a
  tracked file makes it fail (verify, then restore).

## Tier C — stale / overstated user-facing text (the app must not contradict its own fixes)

- [x] **C1 (MEDIUM) Fix the in-app help texts in gui.py.** Review FID-2/REL-2 (verified):
  the F1 `INSTRUCTIONS_TEXT` (~line 74) still says FULL BASELINE captures "the ~1 MB log
  dump" — the flagship v0.10.2 change removed exactly that (baseline = quick reads + `set` +
  the ~1 KB `errorlogdump`; the ~1 MB reads are behind the warned Heavy buttons). A user who
  believes the stale text would treat a contactor click during baseline as expected. Also:
  `SAFETY_TEXT` (~line 124) still lists `eeprom` as wholly refused (bare `eeprom` is now an
  allowed read; args/format/erase refused), and the Analyze blurb (~line 95) says rides are
  "parsed from the log dump" (they come from a loaded zero-log-parser `.txt` on rev 41).
  Rewrite all three to match the code (mirror the README's corrected wording; mention the
  Heavy row + contactor caveat in Phase 1). **Accept:** string tests in the style of
  `test_connect_guidance_is_cable_agnostic`: `INSTRUCTIONS_TEXT` must NOT claim the baseline
  includes a ~1 MB/event-log dump and MUST mention the heavy confirm; `SAFETY_TEXT` must say
  bare `eeprom` is an allowed read. Verify discrimination (restore the old sentence → fail).

- [x] **C2 (MEDIUM) Supersede docs/first-read-session.md.** Review REL-1 (verified): the doc
  shipped by Tier A commit `b6f80fb` is the unrevised PRE-Tier-A plan — it references
  v0.10.1 (incl. the "confirm About says v0.10.1" step that now always fails), says baseline
  captures "the ~1 MB dumplogs" (invalid command + removed), and its evidence-queue item
  telling the owner to capture `eventlogdump` carries NO contactor warning. Add a prominent
  banner at the top: "**SUPERSEDED — this session was completed 2026-07-10.** Current
  behavior (v0.10.2+): baseline = quick reads + set + errorlogdump; `dumplogs` does not
  exist on rev 41; `eventlogdump`/`dumpall` are explicit warned buttons — on a keyed-on bike
  they can make the BMS briefly OPEN the drivetrain contactor (recovers when the read
  finishes). See README → Phase flow." Then correct the §5 eventlogdump instruction in-place
  to carry the contactor warning, and fix the version references. **Accept:** the doc's
  first screen carries the banner; a grep of the doc finds no un-caveated eventlogdump
  capture instruction and no claim that baseline includes dumplogs.

- [x] **C3 (MEDIUM) Make the write-options browser and guards honest about THIS bike.**
  Review FID-1 (both dimensions, verified): 7 of 14 `WRITE_WHITELIST` entries (`brakeregen`,
  `brakefilter`, `noregenstopped`, `reserve_sw`, `reserve_pct`, `fuelgaugepes`, `chgstby`)
  and ALL 8 `READONLY_GUARDS` never appear in the real rev-41 dump — login never reveals
  them — yet `_show_write_options` labels every absent entry "(read after login)" (a claim
  ground truth disproves) and the guards' hard-coded numbers (100/145 C, 4160 mV) read as
  live facts. Changes:
  (a) in `_show_write_options`, render absent-from-live-dump entries as something like
  "(not in the live dump — appears only if YOUR bike exposes it; not seen on the verified
  2017 FXS rev 41)" — keep "(read after login)" ONLY before any settings dump exists;
  (b) add one header line to the guards section (browser + Writes-tab pane) noting these are
  documented/Sevcon-side values that rev 41 does not expose via `set`;
  (c) health.py (~lines 87-91): when `_setting_num` fell back to the 100/145 defaults, say
  so — "cutback stages at 100 / 145 C (documented defaults — not read from this bike)";
  (d) cli.py selftest label: rename the "whitelist-x-live" check to say it measures the SIM
  (e.g. "whitelist-x-sim"), since the 14-row match is only true because the sim invents the
  missing 7; (e) fix the B1 test comment in `test_rev41_fixture.py` (~163) that claims every
  whitelist name is a real bike setting — the assertion only covers the 7 live names; make
  the comment match the assertion. **Accept:** GUI test — after a (sim) login+set ingest,
  `_show_write_options` output does NOT contain "(read after login)" for a name absent from
  the live dump, and DOES contain the "not in the live dump" phrasing (update
  `test_write_options_browser_needs_no_login` for the pre-connect state it covers);
  a health test asserting the "(documented defaults" suffix appears exactly when the
  settings dump lacks motstage1/2. Verify discrimination on (a) and (c).

## Tier D — hardening (LOW; all verified-real but bounded — do after A-C, skip any that balloon)

- [x] **D1 Canonicalize validated write values.** Review FID-1-safety (low): `_v_int_range`
  uses `int()`, which accepts `'1_0'` (=10) and `'+22'`; `write_setting` sends the ORIGINAL
  string, and the readback compare `first_number('1_0')` == `'1'` could journal a false
  VERIFIED if the console's atoi stops at the underscore. Tighten `_v_int_range` to require
  `re.fullmatch(r"[0-9]+", str(v).strip())` before `int()` (all whitelisted int settings are
  non-negative). **Accept:** validator tests — `'1_0'`, `'+22'`, `' 2 2 '` refused; `'22'`
  accepted; existing range checks unchanged. Verify discrimination.

- [x] **D2 Move the not-in-live-dump write refusal into the transport.** Review SAFE-1-fidelity
  (low): the "name must be in a fresh live dump" check lives only in the GUI write job, so a
  headless/scripted caller could put `set brakeregen yes` on the wire — a name never
  observed on rev 41 (the class of assumption v0.10.2 exists to retire), and the project's
  own architecture note says safety enforcement lives at the transport layer. In
  `Transport.write_setting`, before composing the write: run `set`, parse it, refuse
  (BlockedCommandError) if the name is absent. Only blocks more; the GUI's own pre-flight
  stays. **Accept:** transport test — sim-login, `write_setting` on a whitelisted name that
  the live dump does NOT contain raises with a "not present in the live settings dump"
  reason (the sim DOES contain brakeregen, so drive it with a ScriptedPort whose `set` reply
  lacks the name, or monkeypatch the parsed dump); `write_setting("spfront", "22")` still
  works end-to-end in the sim. Verify discrimination.

- [x] **D3 Re-gate on raw-box `logout`.** Review SAFE-3 (note): `logout` typed raw
  de-escalates the console but the GUI keeps `logged_in=True`, the Writes tab enabled, and
  the master unlock armed (fail-closed downstream, but a confusing lie). In `_read_cmd`'s
  `done()` (or `_raw_send`), when the head is `logout`: clear `self.logged_in`, set
  `unlock_var` False, re-run the gate refresh — mirroring the reboot re-gate. **Accept:**
  GUI test — sim connect→login→raw `logout` → `app.logged_in` is False and the unlock
  toggle is disarmed. Verify discrimination.

- [x] **D4 obd: run it LAST in the baseline and fix the quiet-error text.** Review
  FID-5/FID-2-safety (low/note): `obd` output has never been captured live yet auto-runs
  BEFORE `set` (the backup the whole flow depends on); and a no-output `obd` raises
  ConsoleQuietError claiming the bike is "asleep or keyed off" even when 11 reads just
  succeeded. Reorder the baseline seq so `obd` runs after `set` + `errorlogdump` (keep
  READ_COMMANDS order for the buttons; just build the baseline seq accordingly), and give
  ConsoleQuietError a distinct message when other commands in the same session already
  succeeded (e.g. "no response to 'obd' — the console is awake (other reads succeeded);
  this command may not produce output on this firmware"). Update the transport comment to
  say obd is menu-verified but its output is not yet captured. **Accept:** the baseline
  recording test (A2) additionally asserts `obd` comes after `set`; a transport test covers
  the awake-vs-asleep quiet message split. Verify discrimination on the ordering.

- [x] **D5 Sim fidelity to the 23-row ground truth.** Review FID-4 (low): the sim's
  post-login dump shows ~14 whitelist rows + populated guards; the real bike shows 7 rows +
  "(not in live dump)" — at the bike that difference reads as "something broke". Default
  decision (keep it small): ADD the 7 missing real names (`drive_mode`, `is_dnr_board`,
  `maxcustsprpm`, `maxcustspkph`, `maxcusttqx10`, `maxcustregcotqx10`, `maxcustregbrtqx10`)
  with values tracking their mph/_allow siblings, gate the sim's bare-`eeprom` reply behind
  `logged_in` (the real level-0 menu doesn't list it), and add `obd` to both sim help menus
  (the real menus list it at both levels). Do NOT delete the synthetic-only names in this
  pass (several tests lean on them) — instead mark them with a comment. **Accept:** a test
  parses the sim's post-login dump and asserts all 23 real rev-41 names are present; sim
  `eeprom` at level 0 answers like an unknown/denied command; sim `help` contains `obd`.

- [x] **D6 Parser: honest docstring + the no-gap guard.** Review PARSE-1/2/3 (note/low —
  none reachable on real rev-41 output): (a) the `_split_desc_value` docstring overclaims
  ("ALWAYS parted by the alignment pad"; "a blank value yields value=''" is only true while
  firmware padding survives) — reword to state the real assumptions; (b) in the no-gaps
  branch, use `val_edge`: if the gap-free region ends at/before `val_edge`, return it as the
  DESC with value='' (it lies wholly in the desc column) instead of labeling it a value.
  Skip the tie-break rework (synthetic-only; document it as a known limitation in the
  docstring). **Accept:** fixture test — a blank-value row with trailing padding STRIPPED
  now parses as desc=text/value='' (currently desc=''/value=text); existing edge cases stay
  green. Verify discrimination.

## Tier E — release gate

- [x] **E1 Version + rebuild.** Bump `__init__.py` + `pyproject.toml` to **0.10.3**. Full
  suite green (GUI standalone-flakes noted above); `--selftest` + `--smoketest` green;
  rebuild `dist\openmbb.exe` (frozen `--selftest` exit 0) and the installer via ISCC
  `/DAppVersion=0.10.3` (ISCC.exe is under `%LOCALAPPDATA%\Programs\Inno Setup 6\`).
  **Do NOT tag/release/push.**

- [x] **E2 History rewrite — run ONLY after every other tier is committed and the B-scrubs
  are in.** The identifiers live in three commits (per the review: `e7651e1`, `ca223df`,
  `f182930`), unreachable from origin (origin has only `main` at v0.9.0 — verified clean).
  Protocol:
  1. Tree clean, all v0.10.3 tiers committed.
  2. Recover the identifier strings into SHELL VARIABLES ONLY (e.g. from
     `git show f182930:tests/test_rev41_fixture.py`) — never into a file or commit message.
  3. For each: `git log --all --oneline -S"$ID"` → enumerate every dirty commit; find the
     EARLIEST one; `CLEAN_BASE` = its parent. (Do not assume the review's three — verify.)
  4. `git branch backup-prerewrite` (safety net).
  5. `git reset --soft $CLEAN_BASE`, then commit the whole tree as one or two fresh commits
     (e.g. "v0.10.x hardening (squashed): v0.10.1 + v0.10.2 + v0.10.3 — PII-scrubbed
     history"). The TREE must be byte-identical to pre-rewrite HEAD (`git diff
     backup-prerewrite` → empty).
  6. Verify each `$ID`: `git log --all -S"$ID" --oneline` must show hits ONLY via
     `backup-prerewrite`. Then delete `backup-prerewrite`,
     `git reflog expire --expire=now --all`, `git gc --prune=now`, and re-run the `-S`
     searches → zero hits anywhere.
  7. Full suite once more at the new HEAD; B3's gate test green.
  **Accept:** step 5's empty diff, step 6's zero `-S` hits, suite green. If anything goes
  sideways mid-rewrite, `backup-prerewrite` restores the exact prior state — do not
  improvise beyond this protocol.

---

## Explicitly NOT doing (from the review, with reasons)
- No writes to the bike; the boolean write token stays unverified/fail-closed.
- Not pruning `WRITE_WHITELIST` to the 7 live names — the generic-Gen2 "options reference"
  is intentional; D2's transport guard + C3's honest labels close the actual gaps.
- Not reworking the parser tie-break (PARSE-3) — synthetic-only on rev 41; documented
  instead (D6).
- Not capturing real `obd` output — that needs the next live session (keyed-off/charger
  reads); D4 just makes the app safe/honest about not having it yet.

## Progress log

### Tier A — raw-box safety (complete)
- A1: raw box normalizes the head and routes eventlogdump/dumpall (+ variants like "eventlogdump 5") through _read_heavy — same contactor confirm + 30s idle as the buttons; _read_cmd classifies is_dump by lowercased first token so a variant gets 900s dump max_time not the 60s cap. Tests: raw heavy pops confirm + cancel sends nothing; "eventlogdump 5" gets idle>=30 + max>=900. Discrimination verified on both (a routing revert and the exact-match revert).
- A2: new GUI test records every command _baseline actually sends and asserts no HEAVY_COMMANDS, no dumplogs, errorlogdump+set present. Discrimination verified (appending eventlogdump to seq fails it).

### Tier B — PII scrub (complete)
- Deleted the three old workplans (WORKPLAN-v0.10/.10.1/.10.2 — user request; also cleared WORKPLAN-v0.10.md's real VIN+serial and all their username paths).
- B1: the two no-PII guard tests now use a shared _assert_redacted() — placeholders present + zero VIN-shape (charset excludes I/O/Q; placeholder has an 'I') / MBB-serial-shape / module-serial-shape tokens; no real identifier literal remains in tests/. Discrimination proven with a planted token in a temp copy.
- B2: scrubbed docs/review-2026-07-09.md (MBB serial -> REDACTEDMBB00; 45 username paths -> <repo>) and docs/first-read-session.md (2 paths). Repo-wide survey: zero PII shapes, zero durha username paths in tracked files.
- B3: tests/test_release_gate.py walks git ls-files (text only), asserts zero VIN/serial-shape tokens (placeholders allowlisted; VIN shape requires a letter+digit mix to avoid 17-digit false positives). Passes clean; a planted VIN token makes it fail.

### Tier C — honest text (complete)
- C1: INSTRUCTIONS_TEXT phase-1 rewritten (baseline = quick reads + set + errorlogdump, "NO heavy dumps"; heavy eventlogdump/dumpall are separate confirmed buttons w/ the contactor caveat); Analyze/Rides now says "ride log you load (.txt)"; SAFETY_TEXT says bare eeprom is an allowed read (args refused) + lists dtc_clear/force_all_storage_mode/blcmds/burn. Two string tests; discriminate by asserting new phrases present + stale phrases absent.
- C2: docs/first-read-session.md carries a prominent SUPERSEDED banner (current v0.10.2+ behavior: baseline drops the ~1 MB event log; dumplogs invalid; heavy dumps are warned buttons w/ contactor risk); the §5 eventlogdump capture instruction now carries the contactor warning; baseline line corrected.
- C3: added safety.REV41_FXS_SETTINGS (the 7 whitelist names confirmed in the real dump). _show_write_options now distinguishes verified names ("read after login") from names the bike never exposes ("not in the live dump" + "NOT seen on the verified 2017 FXS rev 41" note); guards section (browser + Writes tab) labelled documented/Sevcon-side. health.py appends "(documented defaults — not read from this bike)" when motstage1/2 aren't in the dump. cli.py selftest label renamed whitelist-x-sim. B1 test comment corrected + cross-checks set(live)==REV41_FXS_SETTINGS. Discrimination verified on the browser-honesty and health-defaults fixes.

### Tier D — hardening (complete)
- D1: _v_int_range requires re.fullmatch("[0-9]+") before int() — refuses '1_0'/'+22'/'0x14'/Unicode digits/internal-space forms that int() would accept and the console might parse differently; ' 22 ' still strips+passes, '22' passes. Self-discriminating (revert makes '1_0'->10 validate).
- D2: Transport gained a known_setting_names cache (updated on a bare `set` dump, invalidated on login). write_setting refuses a name absent from the live dump at the TRANSPORT layer (lazily reads `set` once if the cache is cold) — closes the scripted-caller hole where `set brakeregen yes` could reach the wire. GUI write flow already reads `set`, so no extra traffic there (verified: the set-call-counting GUI tests still pass). Updated the pre-login write test to expect the transport refusal. Discrimination verified.
- D3: raw-box `logout` in _read_cmd's done() clears logged_in, disarms unlock_var, re-runs _apply_gates (mirrors the reboot re-gate). GUI test; discrimination verified.
- D4: baseline runs `obd` LAST (after set + errorlogdump) since its output is never-captured-live; Transport.saw_any_response lets ConsoleQuietError distinguish "console awake (other reads succeeded)" from "asleep/keyed off". A2 test asserts obd-after-set; transport test covers the message split. Ordering discrimination verified.
- D5: added the 7 missing real rev-41 names to SIM_SETTINGS (drive_mode/is_dnr_board + the RPM/KPH/x10 custom-mode twins, real values); marked the synthetic-only names; gated the sim's bare `eeprom` behind login (real level-0 menu omits it); added `obd` to the sim help menus. Tests assert all 23 real names present + eeprom level-0 gating + obd in help.
- D6: _split_desc_value docstring rewritten (states the real single-token/single-space assumptions + documents the multi-token/tie limits as not-defended); the no-gap branch now uses val_edge — a gap-free run ending at/before the Value column is a DESCRIPTION with value="" (fixes a right-trimmed blank-value row). Fixture test; discrimination verified.

### Tier E — release gate
- E1: bumped __init__.py + pyproject.toml to 0.10.3; full suite green (134 non-GUI + 42 GUI, 1 Tk-teardown flake-skip); --selftest + --smoketest green; rebuilt dist/openmbb.exe (frozen --selftest exit 0, 61 PASS) + installer via ISCC /DAppVersion=0.10.3. Not tagged/pushed.
- E2: history rewritten. -G shape searches found the PII in 4 commits (e7651e1/ca223df/f182930 added it, edf0673 removed it) — earliest e7651e1, so CLEAN_BASE = its parent 5e239e6 (v0.9.0, verified PII-free + matches origin/main). Branched backup-prerewrite, `git reset --soft 5e239e6`, squashed all 20 v0.10.x commits into one clean commit; `git diff backup-prerewrite` empty (tree byte-identical). Deleted the backup, expired all reflogs, gc --prune=now; the old commits are gone (e7651e1 no longer a valid object) and all three PII-shape searches over --all return ZERO. Suite + B3 gate green at the new HEAD. Not tagged/pushed.
