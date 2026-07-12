# WORKPLAN v0.18.1 — owner's hands-on v0.18 reports (Fable-planned, Opus executes)

Source: owner testing installed v0.18.0 (2026-07-12). Four reports, all root-caused
by a 4-agent read-only investigation (workflow w81cmt967 — cite lines verified there).

## Standing constraints (verbatim)
- No writes to the bike in dev; never open a real COM port in dev/test (sim/pytest).
- Workers never touch Tk; `_cbq` only. `after()` callbacks guard `winfo_exists()`.
- Commits authored `durha010 <durha010@gmail.com>`, NO Claude trailer.
- **Do NOT reintroduce `bind_all`/`unbind_all` for wheel scrolling** — it churned the
  interpreter-global bindtag across app lifecycles and broke the test suite once
  already (fixed in the v0.12 suite-hardening; keep bindings per-widget).
- Every tier: discriminating tests + green suite before commit.

---

## Tier A — Safe disconnect = a REAL clean slate (report: "still brings up previous windows")
The "previous windows" are the four text consoles and several panels that are never
cleared — reconnecting shows the prior session's output everywhere.

`_reset_session_state` (gui.py ~1652) must additionally clear:
1. **All four consoles**: `txt_probe`, `txt_out`, `txt_login`, `txt_console` — add a
   small helper `_clear_console(widget)` (config normal → delete 1.0/end → disabled),
   applied with `getattr(self, name, None)` guards (reset can run before widgets exist).
   NOTE: `_reset_session_state` also runs at the START of every `_connect`, which is
   desirable — a retry begins with a fresh Connect log (also fixes stale error text
   mixing into the new attempt).
2. **Console command history**: `self._cmd_history = []`, `self._cmd_hist_idx = 0`.
3. **One-time hint flag**: `self._analyze_hint_shown = False`.
4. **Compare tab**: `self.compare_list = []` + clear `txt_compare` (guarded).
5. **Writes description panel**: call `self._show_effect_hint()` (guarded on
   `effect_panel` existing) so a stale setting description doesn't survive.
6. **Watch checkbox**: `watch_var.set(False)` (the tick auto-stops on disconnect, but
   the checkbox can stay visually armed if no tick fires in between).
7. **Transient Toplevels** (gearing calculator, recent-sessions picker, info windows):
   iterate `self.winfo_children()`, `destroy()` any `tk.Toplevel` (try/except each) —
   "previous windows" may literally include these.
- Tests: extend `test_safe_disconnect_resets_to_clean_slate` — put text in all four
  consoles + cmd history + compare_list + hint flag, open the gearing calc, select a
  Writes row (populate effect panel); after `_safe_disconnect()` assert all cleared,
  no Toplevels remain, and the effect panel shows the hint text.

## Tier B — Connect-fail fallback page: stale copy + styling
When a real-mode connect fails, `_restore_connect_controls` re-shows `connect_row` +
`connect_help`, which carry pre-v0.17 tone/vocabulary:
1. **`connect_help` is an orange warning wall** (`foreground=P["warn"]`, gui.py ~1894)
   — restyle to the calm muted look (`style="Muted.TLabel"` / `P["dim"]`), and REWRITE
   the copy to the current flow + ACTUAL button labels (verify against VERIFY_LABEL /
   CONNECT_LABEL constants; mention the Simulator toggle on the Home screen and
   Help → Wiring diagram; keep it 2–3 short sentences, not a paragraph block).
2. **`connect_busy` uses Good.TLabel (green)** for "Connecting…" (~1901) — green is a
   success colour; use Muted (neutral in-progress).
3. On failure, after restoring controls, `_probe_log` one plain line like
   "Attempt failed — adjust the port (or turn on Simulator mode) and try again." so
   the fresh console isn't empty after the error dialog closes.
4. Standardize "Home screen" wording in this copy (matches File → Home).
- Tests: connect_help foreground is NOT `P["warn"]`; its text contains the current
  button labels and no retired vocabulary; connect_busy style is not Good.TLabel;
  a failed `_connect` (monkeypatched listen → raise) restores controls AND leaves the
  "Attempt failed" line in txt_probe.

## Tier C — Two-finger scroll dead zones on the Writes page
Root cause (verified): `_bind_page_wheel` walks children ONCE at build time; the
description panel's widgets are created LATER (`_show_effect` / `_show_effect_hint`
destroy + rebuild children per selection) so they have no `<MouseWheel>` binding —
the wheel dies over the panel area.
1. Factor the recursive binder out of `_bind_page_wheel` into
   `_bind_wheel_subtree(widget, handler)` (same exclusions: Treeview/Text/Listbox).
   Store the page handler on the frame (`f._owl_wheel` — the pattern already used on
   the Read tab's action column).
2. At the END of `_show_effect` and `_show_effect_hint`, call
   `self._bind_wheel_subtree(self.effect_panel, <stored handler>)` so every rebuilt
   child scrolls the page. (Tier D's card widgets are created in the same paths, so
   this covers them too — implement C before or together with D.)
3. Do NOT use bind_all (see standing constraints).
- Tests: after `_show_effect` on a real row, recursively assert every widget under
  `effect_panel` has a non-empty `bind("<MouseWheel>")`; same after
  `_show_effect_hint()`.

## Tier D — Description panel: make it actually readable (a card)
The restyle still reads poorly: ~980px wraplength (way too long a measure), 9pt body,
4px paragraph gaps, 12px indent, ttk labels floating on the page background, and
P["dim"] hint text is low-contrast. Rebuild as a **card**:
1. In `_build_write_tab`: `effect_panel` hosts a `tk.Frame` card — bg `P["panel"]`,
   flat, 1px border via `highlightthickness=1, highlightbackground="#39394a"` (the
   menu-separator grey; matches the app's flat style — do NOT use relief="raised").
   Inner padding frame (bg panel) padx=12, pady=10. Stash as `self.effect_card`.
2. ALL card children are `tk.Label(bg=P["panel"])` (not ttk — theme TLabel bg floats).
3. Type hierarchy: title `(ui, 12, bold)` fg P["fg"]; RISK chip = ONE grouped frame
   (badge ` RISK ` 8pt bold on green/warn bg + risk text 9pt coloured, 4px gap);
   keyword labels `(ui, 10, bold)` colour-coded (EFFECT #8fd0ff / What-it-does green /
   Caution warn / Note dim); body `(ui, 10)` fg P["fg"], `wraplength=680`,
   indented `padx=(20, 4)`; paragraph spacing `pady=(6, 0)`.
4. Hint + Note text: 10pt and use a slightly brighter local grey `#aab2c5` (do NOT
   change the global P["dim"] — app-wide ripple).
5. Keep `_write_help_lines` (string form) for the confirm dialog — only the panel
   changes. `_clear_effect` clears the card's inner frame.
6. Re-bind wheel after rebuild (Tier C hook).
- Tests: card bg == P["panel"] and its labels' bg match (no floating ttk); body/hint
  labels use font size ≥ 10 and wraplength ≤ 700; the RISK chip renders for a safe
  and a caution setting; panel resets to hint on `_safe_disconnect` (Tier A overlap).

## Tier E — Verify + release
- Full suite green → commit per tier (A+B can be one commit if small; C+D together).
- Adversarial review workflow over the whole diff (the established 2-phase
  review→verify shape); fix confirmed findings.
- Bump 0.18.0 → **0.18.1** (pyproject + __init__), rebuild + install
  (`packaging/build_and_install.ps1 -Force`), launch WITHOUT `--sim`.
- No push (owner's call).

## Execution order
A → B (both touch the connect/reset path) → C+D together (same rebuild paths) → E.
