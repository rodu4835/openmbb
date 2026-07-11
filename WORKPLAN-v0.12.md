# WORKPLAN v0.12 — owner's hands-on-v0.11 notes

**Why this exists.** After installing v0.11.0 the owner drove the app and left a set of
first-hand UX notes. This release works through them.

**Protocol.** Opus executes. Small, local changes; match existing style. Behavioural fixes get
a DISCRIMINATING test where practical; theming/OS-chrome is verified by smoketest (no crash) +
what's testable. `pytest -q -p no:faulthandler` after each item; GUI tests standalone. Never
open a real COM port. Commit per tier.

---

## Tier A — dark window chrome  [note: "window bar still white, top menu still white"]

- [x] **A1 Dark title bar.** The app content is dark but the OS title bar is light. On
  Windows 10/11 set `DWMWA_USE_IMMERSIVE_DARK_MODE` on the top-level HWND (ctypes, best-effort,
  Windows-only, only when the dark theme is active). Must never crash on non-Windows / older
  builds.
- [x] **A2 Dark menu bar.** The native Tk menubar strip is OS-drawn white and ignores bg on
  Windows. Replace it with a themed menubar (a ttk frame of Menubutton widgets) so the strip is
  dark, and color the dropdown menus dark (bg/fg). Keep every existing command (Session / Bike /
  Help, incl. all the v0.11 additions) and the F1 binding.

## Tier B — the Connect screen  [notes: "expected Connect first but should verify cable first";
   "first screen should be verify bike is connected"; "orange text too wordy, assumes cable
   already built — move build detail to Instructions"; "want live output during probe, not just
   a bar"]

- [x] **B1 Verify-first framing.** Make the cable/link check the obvious FIRST action: reframe
  the primary flow as (1) pick port → (2) Verify link (the Listen-only check, renamed to read as
  a verify step) → (3) Connect & Probe. The user should not have to infer that Listen-only comes
  first. (Rename the button / lead the checklist with it; keep Connect & Probe as the second
  step.)
- [x] **B2 Trim the orange text.** The Connect-tab checklist mixes cable-BUILD detail (pinout,
  "Red taped off") that assumes you haven't built the cable — that belongs in the Wiring dialog /
  README, not the first screen. Cut it to what you DO on this screen (pick port, verify, connect,
  the SIMULATOR option, the isolation-off-charger note). The full pinout stays in Help → Wiring.
- [x] **B3 Live probe narration.** During Connect & Probe, narrate the steps into the connect
  console ("listening…", "waking prompt…", "reading version…", "checking firmware…") as they
  happen, instead of only moving a progress bar. Same idea for FULL BASELINE — stream each
  command as it runs.

## Tier C — gating  [note: "don't like being gated; want connect → login directly; but
   prioritize safety over speed"]

- [x] **C1 Login reachable straight after Connect.** Login is READ-ONLY (it only reveals the
  tunable settings), so it should not require FULL BASELINE. Drop the baseline requirement from
  the Login gate. Keep the real safety rule where it belongs: **the WRITES tab stays gated on a
  settings backup existing** (baseline_done) AND login — so "no write without a backup" holds,
  now enforced at the write step, not as a side effect of an earlier phase. Update the locked-tab
  hints accordingly and keep nudging toward FULL BASELINE (smart first move) without blocking
  login on it.

## Tier D — login / writes text + Writes UX  [notes: "login tab too much text, don't list the
   passwords, just say what login lets you do"; "writes top text too long"; "didn't know Writes
   scrolls — add a visible scrollbar"; "What can I change? button is redundant on Writes since
   clicking a row already shows the description"]

- [x] **D1 Trim the Login intro.** Cut to a short "what logging in lets you do" (reveals the
  tunable settings, unlocks Writes). Remove the literal password strings from the screen; the
  provenance/detail lives in Help → Instructions.
- [x] **D2 Trim the Writes top text.** Shorten the explanatory block at the top of the Writes
  tab to the essentials.
- [x] **D3 Visible scrollbar on Writes.** The Writes tab scrolls but nothing signals it — add a
  visible scrollbar (or make the whole tab scroll with an obvious bar) so the journal/guards
  below the fold are discoverable.
- [x] **D4 Remove the redundant Writes-tab options button.** "What can I change? (read-only)"
  duplicates the per-row description on the Writes tab. Remove it from the Writes tab; keep the
  Bike-menu entry (its real value is browsing options BEFORE login / when the table is empty).

## Tier E — release
- [x] Bump to 0.12.0; full suite + selftest + smoketest green; rebuild exe + installer. Not
  tagged/pushed.

## Progress log
### Tier A — dark chrome (complete)
- A1: _apply_dark_titlebar() sets DWMWA_USE_IMMERSIVE_DARK_MODE (attr 20, fallback 19) on the top-level HWND via ctypes — Windows-only, best-effort, never blocks launch. Smoketest confirms no crash.
- A2: replaced the native white menubar with a themed ttk Menubutton bar (Session/Bike/Help) + dark dropdown menus (_dark_menu); all v0.11 commands + F1 preserved. Test asserts the Menubuttons exist and no native menu is installed.
### Tier B — Connect screen (complete)
- B1: buttons renamed to a numbered verify-first flow — '1 · Verify link' (VERIFY_LABEL) then '2 · Connect & Probe' (CONNECT_LABEL); checklist + Instructions Phase 0 lead with the verify step.
- B2: trimmed the orange checklist to what you DO on this screen (verify -> connect, SIMULATOR option, isolation caveat); the FTDI pinout/build detail now points to Help -> Wiring diagram.
- B3: Connect & Probe narrates each step live into the connect console (listening / waking / reading version / checking firmware) via _cbq instead of only a progress bar; FULL BASELINE streams a per-command '[i/N] reading <cmd>…' line. Tests updated for the new labels + a live-narration test.
### Tier C — gating (complete)
- C1: Login now opens as soon as you're connected (it's read-only) — the baseline requirement moved OFF the Login gate. The WRITES tab keeps the real safety rule: connected + logged_in + baseline_done (a backup must exist before any write). Updated _tab_unlock_hint (Read/Login just need connect; Writes lists what's missing) and the baseline complete/incomplete messages (backup-based, not 'Phase 2 unlocked'). New test proves login works pre-baseline while Writes stays gated until the backup exists.
### Tier D — login/writes text + Writes UX (complete)
- D1: Login intro trimmed to WHAT logging in does (read-only, reveals tunables, unlocks Writes) — no password strings on screen.
- D2: Writes top block cut to one concise UNLOCK line (dropped the separate panel-context paragraph).
- D3: the Writes tab is now a scrollable frame with a VISIBLE scrollbar (_scrollable_tab helper: canvas + ttk.Scrollbar + mousewheel-on-hover) so the guards/journal below the fold are discoverable.
- D4: removed the redundant 'What can I change?' button from the Writes tab (it duplicated the per-row description); the read-only reference stays in the Bike menu.
### Suite hardening (pre-release, commit fd88329)
- Running the whole GUI suite in one process surfaced two TEST-HARNESS issues (product paths unchanged; every test passed standalone): (1) an internal-error crash ~test 64 from _scrollable_tab's canvas.bind_all("<MouseWheel>")/unbind_all churning the interpreter-global "all" bindtag across app lifecycles — fixed by binding the wheel directly on the canvas (visible scrollbar drag works regardless); (2) a cross-test assert-False flake (test_raw_box_logout_regates) from dead Tk Variable cycles GC'd mid-way through a later test's app.update() firing __del__ into a torn-down interpreter — fixed with deterministic fixture teardown (cancel pending after() callbacks, destroy, gc.collect()). Full GUI suite now 65 passed / 1 skipped / 0 warnings (was 16) / 167s (was 226s).
### Tier E — release (complete)
- Bumped pyproject.toml + src/openmbb/__init__.py to 0.12.0. Verified GREEN from source: non-GUI 140 passed, GUI 65 passed/1 skipped, --selftest PASS, --smoketest PASS. Rebuilt dist/openmbb.exe (PyInstaller --clean, editable src confirms 0.12.0) — frozen --selftest + --smoketest both PASS. Rebuilt packaging/Output/openmbb-setup-windows-x64.exe via ISCC /DAppVersion=0.12.0 (Inno Setup 6.7.3), installer ProductVersion 0.12.0. NOT tagged/pushed.
(append per item)
