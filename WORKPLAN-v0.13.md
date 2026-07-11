# WORKPLAN v0.13 — guided "showcase" UX redesign

**Owner brain-blabbed a full UX vision (2026-07-10). Owner said "get going," Opus
executes.** This is a MULTI-INCREMENT release. Work tier by tier, keep the app
green + launchable after every tier, keep the read-first / backup-before-write
safety model intact. Owner reviews between increments.

Protocol: small coherent changes, match style, a discriminating test where
practical, theming verified by smoketest (no crash). Never open a real COM port.
`pytest -q -p no:faulthandler` after each item; GUI tests standalone. Commit per
tier (local, not pushed).

---

## Design language (REVISED 2026-07-10 after live preview)
First attempt was a full-neon "vaporwave" Tk theme. The owner previewed it live
and it was **too vibrant / not modern / hard on the eyes**. It is **reverted**.

**DECISION:** keep the clean, modern **Sun Valley (sv-ttk) dark** theme the owner
already liked in v0.12 — low saturation, easy on the eyes — with only **restrained
accent colours** for headings/status: calm blue `#5aa8ff`, calm green `#57c07b`
(theme.py `ACCENT_BLUE` / `ACCENT_GREEN`, plus Title/Heading/Subtitle/Accent/Good
label styles). **No neon.** The "showcase" feel must come from good layout,
spacing and typography — not loud colour.

Fonts: system only, **no external CDNs** (garage use, possibly offline). All
generated HTML stays fully self-contained (inline CSS, embedded SVG, no network).
OPEN: the HTML help pages (instructions/wiring) are still on the brighter palette
— decide whether to tone them down to match the calmer app.

---

## Tier 0 — Foundation & theme
- [ ] **T0.1 Vaporwave accents** added to `theme.py` PALETTE (above), plus ttk
  styles for neon headings + a blue "primary" button style. Base stays readable.
- [ ] **T0.2 Themed, centered modal dialogs.** Replace every `tkinter.messagebox`
  (info / askokcancel / askyesno / showerror / showwarning) with an in-app modal
  that is themed like the app and **centered on the main window**. One helper
  module; all call sites migrated. Covers "all popups styled the same + centered."
- [x] **T0.3 Menu bar normalize.** Remove the ttk.Menubutton chevrons; rename to
  conventional labels **File / Tools / Help** (About under Help). Was Session /
  Bike / Help with dropdown chevrons.

## Tier 1 — Landing / showcase page
- [ ] First screen on open: a short blurb (what OpenMBB is) + exactly two entry
  actions — **Test your cable** (listen-only link check) and **Connect and read**
  (connect + probe + baseline). Full vaporwave styling. Leads INTO the existing
  flow (does not throw away the tabs underneath — the landing is a front door).

## Tier 2 — Connected dashboard
- [ ] After connect: a clear **CONNECTED** banner + **VIN** and key identity/status
  up top (VIN display-only — NEVER written to any saved file). Layout:
  - LEFT: output console + status.
  - RIGHT action column, top→bottom: **Pull full database** (baseline) — BLUE
    (`vw_cyan`) at top → **input box** (raw cmd) → plain click-to-run read buttons
    → the **special/heavy** commands set apart at the bottom.

## Tier 3 — Completion flow & global safe-quit
- [ ] **"Are you done?"** themed, centered popup: **Save & disconnect** (announce
  it's safe to unplug / power off the bike, then quit) · **Go to Analyze** · **Log
  in for more access → Writes**.
- [ ] **Global "Safely disconnect & quit"** affordance reachable anywhere it's
  safe, **disabled during any operation that would cause damage if interrupted**
  (mid-write / heavy dump) — reuse the existing `_busy` guard.

## Tier 4 — Analyze (educational)
- [ ] Review pulled data WITH plain-English explanations for novices: what each
  value is + how it fits the bike's system. (Content generated in Tier 6 workflow.)

## Tier 5 — Writes page restyle
- [ ] Clear, stylized, informational; safety popups (themed/centered) that build
  understanding before any write. Keep whitelist-only + backup-before-write.

## Tier 6 — Informational content (stylized, self-contained HTML)
- [ ] **Better Instructions** → a super-clean self-contained **HTML** page opened
  in the browser (webbrowser.open); no new deps.
- [ ] **Better Wiring diagram** → a real **engineering drawing (SVG)** of the
  FTDI↔OBD/DLC cable, accurate to the repo's verified pinout, presented in a
  styled HTML wrapper (title block, pin table, legend).
- [ ] **Write-options** + other informational dialogs restyled consistently.
  (These content artifacts are being generated in parallel NOW.)

## Tier 7 — Release
- [x] Bump 0.13.0; full suite + selftest + smoketest green; rebuild exe+installer.
  Not tagged/pushed.

## Progress log
ALL TIERS COMPLETE (2026-07-10). Each tier committed after a green GUI suite;
owner reviewed live between tiers (previews via `python -m openmbb.cli --sim`).
- Theme: full-neon vaporwave was tried, owner found it too vibrant / not modern →
  REVERTED to the calm Sun Valley dark + restrained accents. (Lesson: preview big
  visual changes before committing.)
- Action-bar buttons (Done / Safely disconnect & quit) gated on connect + not-busy
  per owner feedback.
- T7: bumped 0.13.0, verified green source + frozen (non-GUI 140, GUI 71/2-skip,
  --selftest, --smoketest), rebuilt dist/openmbb.exe + installer (ProductVersion
  0.13.0), installed per-user and launched. NOT tagged/pushed.
