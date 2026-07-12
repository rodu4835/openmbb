# WORKPLAN v0.19 — whole-app cohesion + README install docs (Fable-planned, Opus executes)

Source: owner request 2026-07-12 ("review it in its entirety — dialogs, popups,
documents, README, instructions, grammar — all cohesive and up to date; README
should also cover pull and install procedures"). Grounded by a 7-agent audit
(workflow w1l4slito): ~20 real defects across README, in-app help, assets, docs.

## Standing constraints (verbatim)
- Commits authored `durha010 <durha010@gmail.com>`, NO Claude trailer. No push.
- Suite green before each commit; update any tests that assert edited strings.
- Copy edits only — NO behavior changes in this release. Safety warnings stay
  emphatic (ALL-CAPS contactor/park warnings are intentional tone).

## Canonical vocabulary (single source for all edits)
"Connect" (never "Connect & probe"), "Test your cable", "Pull full database"
(never "FULL BASELINE" in user-facing text), "command reads" (never "quick
reads"), "Home screen", "Safely disconnect", "UNLOCK WRITES", "↺ Reset" (never
"Revert button"), ride telemetry = eventlogdump straight off the bike (never
zero-log-parser / .bin / Zero app).

---

## Tier A — In-app Instructions text (HIGHEST: users read this via F1/Help)
The fallback instructions block in gui.py (~lines 100–170) predates four reworks:
1. gui.py:133 — "it runs the quick reads + the full settings dump" → "it runs the
   command reads + the full settings dump".
2. gui.py:143 — `or type a specific password and "Try this password"` — that
   button doesn't exist; the Login tab is a pre-filled box + a **Login** button →
   "or type a password and press Login (masked in the logs…)".
3. gui.py:153 — "journals it (with a Revert button)" → "journals it — a changed
   row shows ↺ Reset to restore the last-read value".
4. gui.py:162 — "rev 41 doesn't stream ride telemetry as console text, so use a
   decoded zero-log-parser export" — WRONG twice over → "pull it straight from
   the bike (Analyze → Rides → 'Pull ride log from bike'), or load a saved .txt".
5. gui.py:110–115-ish — if this block still walks "Listen only (Stage 1)" /
   "Connect & Probe", align to "Test your cable" + "Connect".
6. **assets/instructions.html** almost certainly mirrors this text — apply the
   SAME four fixes there (the auditor's silence on it is not trusted; grep it).
7. Sweep: `grep -rn` the whole src/ for these tokens in USER-FACING strings and
   fix every hit: `probe`, `FULL BASELINE`, `quick reads`, `zero-log-parser`,
   `Revert`, `Try this password`, `dumplogs`, `Connect & probe`. Internal code
   comments may keep FULL BASELINE where it names the concept, but prefer
   updating cheap ones (gui.py:72, 1534–36; sim.py:434 comment).
8. If the "Save health report" export emits "run FULL BASELINE" (the audit saw
   `lines.append("FULL BASELINE for model / serial / gearing details.")` quoted
   in README:350 — find the real source in gui.py) → "run Pull full database".
- Tests: existing instruction/help tests may assert old substrings — update.
  Add one test asserting the instructions text does NOT contain "zero-log-parser",
  "Try this password", or "Connect & probe".

## Tier B — README overhaul + Install section
1. Fix stale terms: L110 + L228 "Connect & Probe/probe" → "Connect"; L110-112
   "Listen only (Stage 1)" → "'Test your cable' (listen-only wizard)"; L114 +
   L229 (+ any other) "FULL BASELINE" → "**Pull full database**"; "quick reads" →
   "command reads". L350-area code excerpt: re-quote from the FIXED source.
2. Add a short **Home screen** paragraph before the phase flow: two buttons —
   **Analyze** (saved sessions, no bike) and **Connect**; Simulator toggle lives
   there; sim state shows in the status bar.
3. Add `session_meta.txt` to the session-folder contents list (~L212).
4. NEW **Install & update** section (replaces/absorbs the scattered Download +
   Install-from-source bits). Owner-requested. Draft (Opus: VERIFY every path/
   command exists before committing — esp. whether `packaging/build.py` is real
   or the README's current reference is itself stale; the known-good entry point
   is `packaging/build_and_install.ps1` + `build-and-install.bat`):

   ### Install & update
   **Windows installer (easiest).** Download `openmbb-setup-windows-x64.exe`
   from Releases and run it — per-user, no admin rights, Start-menu entry,
   optional desktop icon, clean uninstaller. Launch from the Start menu or the
   desktop icon.

   **Portable exe.** Download `openmbb-windows-x64.exe` and double-click — no
   install, runs anywhere.

   **Run from source (any OS).**
   ```
   git clone https://github.com/rodu4835/openmbb
   cd openmbb
   python -m venv .venv
   .venv\Scripts\activate            # (Linux/macOS: source .venv/bin/activate)
   pip install -e .
   openmbb --sim                     # explore with the simulator, no bike needed
   openmbb                           # real mode (needs pyserial + an FTDI cable)
   ```

   **Build the exe + installer yourself (Windows).** Requires PyInstaller (in
   the dev extras) and Inno Setup 6:
   ```
   pip install .[dev]
   powershell -File packaging\build_and_install.ps1        # or build-and-install.bat
   ```
   This builds `dist\openmbb.exe`, then the installer in `packaging\Output\`,
   installs it per-user, and verifies with `--selftest` / `--smoketest`.
   (`-Force` closes a running OpenMBB first.)

   **Updating.** Installer users: run the newer setup exe over the old install.
   Source users: `git pull`, then `pip install -e .` again if dependencies
   changed.
5. Grammar batch: L58-59 sentence fragment ("Fonts and the file-manager 'open
   folder' action work cross-platform."); L94 "Bike PARKED" → "Bike parked, kill
   switch OFF."; L149 backticks around `eventlogdump`; em-dash spacing
   consistency (" — " with spaces, per the file's dominant style); keep feature
   names capitalized as the UI shows them (Range, Trend:) — consistent, not
   sentence-cased.

## Tier C — Packaged assets
1. info.html:86 — "<b>Connect &amp; probe</b>" → "<b>Connect</b>".
2. command_reference.json:84 (obd) + :98 (errorlogdump) — "the full baseline" →
   "a full database pull" (prose fields only; keep JSON valid — validate after).
3. Grep instructions.html / wiring.html / info.html for the Tier-A token list;
   fix hits. Re-validate both JSON help files parse.

## Tier D — Repo hygiene
1. `docs/first-read-session.md` — superseded historical doc: move to
   `docs/archive/first-read-session.md` unchanged (its SUPERSEDED banner stays;
   don't rewrite history inside it).
2. Move `WORKPLAN-v0.18.md`, `WORKPLAN-v0.18.1.md`, and (after execution) THIS
   file to `docs/dev/` — planning artifacts don't belong in a public repo root.
3. pyproject metadata verified current (description/version) — no change.

## Tier E — Verify + release
- Full suite green (fix any string-assert tests) → commits per tier (A+C can be
  one "in-app copy" commit; B its own; D its own).
- Light adversarial review is OPTIONAL here (copy-only); instead add the Tier-A
  negative test + JSON validation. If any edit touched a *string used by logic*
  (e.g. a label a test clicks by name), run the affected tests explicitly.
- Bump **0.19.0** (docs/cohesion milestone; pyproject + __init__), rebuild +
  install, launch (no --sim).
- No push (owner's call).

## Execution order
A → C (same token sweep) → B (README, biggest single file) → D → E.
