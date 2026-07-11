# WORKPLAN v0.10.4 — clear the v0.10.3 review's residual findings

**Why this exists.** The v0.10.3 Fable re-review (13-agent workflow, 2026-07-10) verified the
patch clean — no high/medium findings, the PII history rewrite independently proven
unrecoverable, 6/6 discrimination spot-checks passed. It left **9 residual low/note findings**:
forward-looking gaps in the new controls plus a few display/message honesty nits. This patch
closes all nine.

**Protocol** (same as prior workplans). Fable reviewed; **Opus executed**. Every code fix has a
DISCRIMINATING test (reverted in place, confirmed the test fails, restored clean). Safety
direction one-way. `python -m pytest -q -p no:faulthandler` after each item; GUI tests
standalone (known Tk-teardown flakes pass standalone). No writes to the bike; no real COM port.

---

## F1 — shared PII-shape module + harden the release gate  [PII-GATE-1, PII-GATE-2, PII-HYG-2]

- [x] **PII-HYG-2 / PII-GATE-1 / PII-GATE-2.** Moved the VIN/serial shape detectors into one
  module `tests/_pii_shapes.py`, imported by BOTH the release gate and the fixture redaction
  guards so they can't drift. Hardened the shapes: the VIN now matches an all-UPPER **or**
  all-lower 17-char token (catches a lowercase paste; single-case avoids mixed-case code-id
  false positives) and uses explicit non-alphanumeric lookarounds instead of `\b` so
  underscore-delimited tokens (`log_<vin>_parsed.txt`) are caught. The gate now BOM-aware-decodes
  UTF-8 **and UTF-16** (Windows PowerShell `>` writes UTF-16LE, which the old NUL=binary skip
  missed) and also scans the tracked file **paths**. Added a second gate test asserting every
  skipped (undecodable) tracked file is a genuine binary extension, so a silently-missed text
  file fails loudly. **Discrimination:** a lowercase-VIN paste AND a UTF-16 file with a VIN both
  make the gate FAIL (the old code passed the UTF-16 one); green on the clean tree.

## F2 — D2 transport write guard hardening  [D2-FAILOPEN-COLDCACHE, D2-STALE-CACHE-LOGOUT]

- [x] **D2-FAILOPEN.** `write_setting` now fails CLOSED: after the cold-cache lazy `set` read, if
  no parseable dump came back (`known_setting_names` still None) it raises instead of letting an
  unverifiable write reach the wire. **Discrimination:** a `PromptOnlyPort` (prompt, no settings
  table) makes `write_setting` raise "could not read/parse"; reverting the guard lets `set spfront
  22` hit the wire and the test fails.
- [x] **D2-STALE-CACHE-LOGOUT.** `exec_command` now invalidates `known_setting_names` on `logout`
  too (not just `login <pw>`), so a post-logout write is re-checked against the level-0 dump.
  Adjusted the `--selftest` write-flow (reordered so "write refused while logged out" expects the
  transport refusal and the value-validator check runs while logged in, where the name is present).
  **Discrimination:** login→set→logout leaves the cache None and a subsequent `write_setting` is
  refused; reverting the logout branch keeps the stale names and the test fails.

## F3 — message / display honesty  [D4-AWAKE-MSG-STALE, REG-1, REG-2]

- [x] **D4-AWAKE-MSG-STALE.** The "console is awake" quiet-read message no longer asserts the
  console is awake NOW (the flag never expires) — it offers both readings (a silent command vs a
  mid-session sleep) and restores the re-probe advice. **Discrimination:** the test matches the
  new "other reads succeeded earlier" wording; the old "is awake" message fails it.
- [x] **REG-1.** The write-options browser only says "(read after login)" for a verified name
  while NOT logged in; once logged in, a name still absent reads "(not in the live dump)" (login
  already happened and didn't reveal it). **Discrimination:** logged-in + a dump missing spfront
  must not contain "(read after login)"; reverting the `not self.logged_in` guard fails it.
- [x] **REG-2.** `health.py` labels each motor-cutback threshold's provenance PER VALUE
  (`110 C` live vs `145 C (default)`), so a mix — or a garbled single value — is never printed
  like a live read. **Discrimination:** the mixed case (only motstage1 live) flags only the
  defaulted motstage2; reverting to the joint `from_bike` label fails it.

## F4 — hygiene  [PII-HYG-1]

- [x] **PII-HYG-1.** Deleted the stale `.git/ORIG_HEAD` that still named the pruned pre-rewrite
  commit (`git update-ref -d ORIG_HEAD`). No tracked-file change; the object it named no longer
  exists anywhere.

## F5 — release gate

- [x] Bumped `__init__.py` + `pyproject.toml` to **0.10.4**. Full suite green (137 non-GUI + 44
  GUI); `--selftest` + `--smoketest` green; rebuilt `dist/openmbb.exe` (frozen `--selftest` exit
  0) and the installer via ISCC `/DAppVersion=0.10.4`. Not tagged/pushed.

---

## Not doing (from the review, with reasons)
- Not catching a serial glued mid-word between alnum chars (`mod17gb1234x`) — inherent to any
  boundary-based shape; catching it would over-match hashes. The realistic vector (underscore
  filenames, whitespace-delimited pastes) IS caught.
- No writes to the bike; the write path stays gated and unused.

## Progress log
- F1: tests/_pii_shapes.py (shared, hardened shapes); test_release_gate rewritten (UTF-16 decode + path scan + binary-skip guard test); test_rev41_fixture uses find_pii_shapes. Discrimination verified (lowercase + UTF-16 VIN both caught).
- F2: transport.write_setting fails closed on unparseable dump; cache invalidated on logout; cli.py selftest reordered. PromptOnlyPort + logout-invalidation tests; both discriminate.
- F3: hedged quiet-console message; browser "(read after login)" gated on not-logged-in; health per-value default labels + _setting_num_live helper. Three tests; all discriminate.
- F4: stale .git/ORIG_HEAD deleted.
- F5: bumped 0.10.4; suite + selftest + smoketest green; exe + installer rebuilt (frozen --selftest exit 0). Not tagged/pushed.
