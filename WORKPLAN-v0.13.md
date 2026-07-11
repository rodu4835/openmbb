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

## Design language (decided defaults — owner can push further)
Vaporwave as an **accent layer on the existing readable dark base**, NOT a
readability-killing full re-theme. The showcase/landing + headers + primary
buttons get the neon treatment; body text and the serial console keep the calm
dark base for legibility.

**Accent palette (added to theme.py as PALETTE keys):**
- `vw_pink`   #ff5fd2   (primary neon / headings, "hot" actions)
- `vw_purple` #b06bff   (secondary accent, gradients)
- `vw_cyan`   #22d3ee   (links, the blue "Pull full database" action)
- `vw_mint`   #5cffb1   (success / connected / safe)
- `vw_yellow` #fff59d   (caution highlight)
- `vw_bg0`    #120a24 / `vw_bg1` #1c1440 / `vw_bg2` #0a0f2e  (showcase gradient)
- `vw_grid`   #2a2350   (grid / scanline lines)

Fonts: system only, **no external CDNs** (the app is used in a garage, possibly
offline). Headers "Segoe UI Semibold", body "Segoe UI", mono "Cascadia Mono" /
"Consolas". All generated HTML must be fully self-contained (inline CSS, embedded
SVG, zero network requests).

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
- [ ] Bump 0.13.0; full suite + selftest + smoketest green; rebuild exe+installer.
  Not tagged/pushed.

## Progress log
(append per item)
