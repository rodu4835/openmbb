# WORKPLAN v0.11 — first-time-owner UX pass (from the walkthrough review)

**Why this exists.** A 5-persona first-time-owner walkthrough of v0.10.4 (2026-07-10) produced
39 reality-checked findings. The headline: a downloaded-exe owner hits a wall at the front door
(no way to reach the simulator, blank port list with no guidance), then meets a string of
smaller confusions, doc inconsistencies, and missing everyday pathways. This release makes the
**simulator a first-class in-app option** and clears the rest.

**Protocol.** Opus executes. Small, local changes; match existing style. Behavioural fixes get a
DISCRIMINATING test (revert → test fails → restore). `pytest -q -p no:faulthandler` after each
item; GUI tests standalone. Never open a real COM port. Commit per tier.

---

## Tier A — the front door: SIMULATOR as an option + connect flow  [OB-01/02/03, MP-07, obs-2, obs-8]

- [x] **A1 SIMULATOR always in the port dropdown** (real mode too), so an exe user can explore
  with no bike and the Instructions' "(or SIMULATOR)" is true. `_make_port` already returns a
  SimPort for "SIMULATOR"; just always offer it. Give it a friendly label distinct from a COM
  port. Removes the need for a separate `--sim` build/shortcut.
- [x] **A2 Empty/failed port refresh feedback**: when no COM ports are found, show a
  "(no COM ports found)" state + a Device-Manager/driver hint in the Connect log; when ports
  are found, log them so Refresh visibly did something. SIMULATOR always present so the list is
  never truly empty.
- [x] **A3 No-port-selected error → GUI-speak**: replace "Pick a COM port (or run with --sim)."
  with guidance naming Refresh + SIMULATOR (never a CLI flag).
- [x] **A4 Listen-only feedback**: on click, immediately log "listening now — power the bike"
  + a countdown, and disable the Listen/Connect buttons for the window, so the ~10-45 s silence
  isn't mistaken for a hang.
- [x] **A5 "Connect && Probe" double-ampersand**: the button shows a literal `&&`. Fix the Tk
  ampersand so it reads "Connect & Probe" like every message refers to it.

## Tier B — text/label honesty & consistency  [OB-04/05/06/07, obs-4/6, A5/A6, OB-08]

- [x] **B1 Reconcile F1 Instructions with the Connect tab**: add Stage-1 Listen to Phase 0;
  mention SIMULATOR is in the dropdown; drop the impossible "bake into COMMUNITY_PASSWORDS in
  gui.py" advice (replaced by Tier E in-app save). Reframe the README opener to the user pitch.
- [x] **B2 Wiring dialog power line**: mirror the Connect tab's "key ON OR plug in the AC
  charger" + the off-charger isolation caveat (currently demands key ON).
- [x] **B3 Connect checklist order**: physical prerequisites (wiring, port location) before the
  click-steps.
- [x] **B4 Kill the last `dumplogs` reference** in the Rides pre-session text (command doesn't
  exist on rev 41).
- [x] **B5 De-personalize the developer's bike data** (aligns with the generic-tool direction):
  attribute model-specific / measured numbers as examples, not universal fact — spfront/sprear/
  rwhcirc effect text, the WRITE_PANEL_CONTEXT SoC sentence, reserve_sw SoH claim, and the
  Gearing tab defaults (start at detected/stock, not the author's 14/56 re-gear).
- [x] **B6 Read-tab affordance**: the bare command buttons + jargon (MBB/rev 41/Sevcon/
  isolation) get a one-line legend / short descriptions so a first-timer knows what each does.

## Tier C — numbers with meaning  [obs-3, A2/A3/A4/A7/A8, obs-5, guards-pane]

- [x] **C1 Point reads at Analyze**: after a successful read / FULL BASELINE, append one line —
  "To interpret these (ok/watch/alert), open Analyze → Use current session."
- [x] **C2 Lifetime-max relabel**: "Max battery temp" / "Max motor temp" → "(lifetime)" with a
  note "highest ever recorded, not the current temperature"; same idea for the capacity row's
  degradation-looking wording.
- [x] **C3 Health explanations discoverable**: surface the per-row note without requiring a
  click (inline, or a visible "select a row for details" hint) — and the charging-false-low
  explanation in plain language.
- [x] **C4 Guards pane contradiction**: the header says rev 41 doesn't expose these (they show
  "—") but the sim shows numbers. Soften the header to "shown if your bike exposes them; on the
  verified rev 41 they're usually not in the `set` dump" so sim ≠ bug is clear.
- [x] **C5 Rides source path**: name the zero-log-parser project + what input it needs (and that
  OpenMBB's own event/error-log dumps are not that input), so the tab isn't a dead end.
- [x] **C6 Folder-load with no data**: warn ("no readable session data in that folder") instead
  of silently rendering all n/a.
- [x] **C7 Disabled tabs explain themselves**: clicking a locked Login/Writes tab (or "Open
  Writes tab" before login) says what unlocks it, instead of a silent no-op.

## Tier D — login / writes trust  [login-level2, confirm-dialog, write-options jargon, unlock]

- [ ] **D1 Login tab plain-language**: whose passwords ("community-documented service
  passwords"), that trying them is read-safe, what "level 2" means, and what unlocks on success.
- [ ] **D2 Write-confirm dialog**: add the backup → send → read-back verify → journal (with
  one-click Revert) story, right where the nervous first write happens.
- [ ] **D3 Define "live dump"** in the write-options browser + surface it from the Writes tab
  (a button), not only the Bike menu.
- [ ] **D4 UNLOCK toggle**: a one-line explanation of what arming it does (and that a write still
  needs a per-write confirm).

## Tier E — new pathways sized for a small Tk app  [MP-01..06]

- [ ] **E1 Live "Watch"**: re-run a chosen read every N s, append timestamped, auto-stop on
  disconnect; reads-only so it stays inside the safety model.
- [ ] **E2 Health report export**: "Save health report…" → a plain-text one-pager (identity +
  health metrics + gearing) the owner can post/share.
- [ ] **E3 Recent sessions**: a Session-menu "Open recent session…" list (from the sessions
  folder) so old sessions aren't hunted through an OS picker.
- [ ] **E4 Copyable output**: right-click / button "Copy" on the console + Analyze text; make the
  read/health/rides text selectable+copyable.
- [ ] **E5 In-app password save**: on a successful login, offer to remember it (config file, not
  gui.py) so the exe user never edits source.
- [ ] **E6 Units preference**: a Session-menu unit toggle (mi/km) applied to the display where
  the app computes distances/speeds; note where values are the bike's own strings.

## Tier F — release
- [ ] Bump to 0.11.0; full suite + selftest + smoketest green; rebuild exe + installer. Not
  tagged/pushed.

## Progress log
### Tier A — front door (complete)
- A1: SIM_CHOICE ('SIMULATOR (no bike)') always in the port dropdown (real mode too); real port preselected when present else SIMULATOR; _make_port/_listen_only/_connect use SIM_CHOICE. A5: button now 'Connect & Probe' (was literal '&&').
- A2: _refresh_ports logs found ports, or a no-ports + FTDI-driver/Device-Manager hint (list never truly empty since SIMULATOR is always offered).
- A3: no-port-selected error rewritten to GUI-speak (Refresh + SIMULATOR), never '--sim'.
- A4: listen window announces itself immediately + a live 'Listening… Ns' button countdown + disabled buttons; recovers on success OR error. Reworded start line so it doesn't collide with the existing STAGE-1 result marker.
- Tests: real-mode dropdown + button text, refresh-empty feedback, no-port error wording, listen announce/countdown/recover. A1 discrimination verified.
### Tier B — text honesty & consistency (complete)
- B1: Instructions Phase 0 now covers SIMULATOR + Stage-1 Listen; Phase 2 drops the 'edit gui.py' password advice (points at Session -> Remember login password, added in E5); README opener reframed to the user pitch (read your bike / simulator).
- B2: Wiring dialog mirrors 'key ON OR plug in the AC charger' + the off-charger isolation caveat.
- B3: Connect checklist reordered — wiring + port location first, then power, then the click-steps; adds a 'no bike yet? pick SIMULATOR' hint.
- B4/C5: Rides guidance drops the phantom 'dumplogs' and names the real source (community zero-log-parser, decoded .txt; OpenMBB's own dumps are NOT that input).
- B5: de-personalized the developer's bike data — spfront/sprear/rwhcirc/reserve_sw/fuelgaugepes effect text + WRITE_PANEL_CONTEXT are now generic; gearing defaults seed FX/FXS factory 20/90 (not the author's 22/88 re-gear); KNOWN_SETUPS labels generic. Two gearing tests updated to the new labels.
- B6: Read tab gains a plain-language legend + a hover tooltip (READ_TIPS) for every read/heavy button.
### Tier C — numbers with meaning (complete)
- C1: first read prints a one-time '-> open Analyze -> Use current session' pointer; FULL BASELINE completion adds an Analyze pointer.
- C2: max temps relabelled 'Max motor/battery temp (lifetime)' with 'highest EVER recorded, not the current temperature'; Pack capacity value/note clarified as design-nominal + current-charge, not degradation.
- C3: the Health per-row explanation label is seeded with a visible 'click any metric row…' hint (was blank until clicked).
- C4: both guards headers softened — a value shows only if the bike exposes it in `set`; rev 41 usually doesn't ('—'); the SIMULATOR fills examples (so sim != bug).
- C5: (done in B4) Rides names the real zero-log-parser source.
- C6: loading a folder with no readable session data warns + flags 'no readable data' instead of silent all-n/a.
- C7: clicking a locked tab (or 'Open Writes tab' before login) explains what unlocks that phase.
(append per item)
