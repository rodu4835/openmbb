# WORKPLAN v0.18 — close the remaining gaps (Fable-planned, Opus executes)

Source: owner's session-audit request 2026-07-11 ("anything left we didn't integrate?").
Audit found 4 gaps + 2 open decisions after the v0.17.x batches (8c4d868..60cb88f).

## Standing constraints (verbatim, non-negotiable)
- No writes to the bike as part of dev work. Never open a real COM port in dev/test
  (`--sim` / pytest only).
- Workers never touch Tk; all widget mutation via `self._cbq`. `after()` callbacks
  guard `winfo_exists()`.
- Commits: author `durha010 <durha010@gmail.com>`, **no Claude co-author trailer**.
- Every tier lands with discriminating tests + green full suite before commit.
- Password/secret redaction and PII shape-guards must keep passing untouched.

---

## Tier A — BUG: Trend charts must not mix simulator data into real trends
**Priority: highest (data integrity).** Owner said the lifetime trend default is "the
data that was done on real hardware"; today a `--sim` rehearsal pull saves fake
`bms`/`stats` into the same sessions root and pollutes the real trend line.

- File: `src/openmbb/gui.py`, `_load_trend_sessions()`.
- Session folder names end with the connect tag: `_sim`, `_listen`, or the port
  (e.g. `_COM4`) — tag is set in `_connect` (`tag = "sim" if is_simport else ...`)
  and by the listen wizard. **Skip folders whose name ends `_sim` or `_listen`.**
  (`_listen` folders have no bms/stats anyway; skipping also saves parse time.)
- Keep `_trend_cache` semantics unchanged (still invalidated on a new pull).
- The synthetic-preview path (`_sim_trend_points`, `SIMULATED` label) is unaffected:
  in sim mode with <2 REAL pulls it now triggers more often, which is correct.
- Chart note: change `"%d pulls"` → `"%d real pulls"` so the filtering is visible.
- Tests (`tests/test_gui_flow.py`):
  1. Create two fake session folders under a tmp sessions root — `..._COM4` and
     `..._sim` — each with a parsable `NNN_bms.txt` (different capacity values) and
     `# command:` headers. Point the app's session root there (monkeypatch
     `_recent_sessions` or `log_dir`). Assert `_load_trend_sessions()` returns only
     the `_COM4` entry.
  2. Assert the rendered trend note contains "real pull".

## Tier B — Heavy-read false "TRUNCATED" on a complete eventlogdump
On the real bike the capture ended `### TRUNCATED` (idle timeout during the
contactor stall) even though all 8,595 entries arrived. "Pull ride log from bike"
must not scare the user with "capture is incomplete" on a complete log.

- File: `src/openmbb/transport.py`, `exec_command()` — the banner append at
  ~line 324 (`if self.last_truncated: result += "### TRUNCATED..."`).
- **Decouple wire-state from verdict.** Keep `last_truncated = True` (the resync on
  the next command is correct and must stay). But before appending the banner, if
  the command head is `eventlogdump`, run a completeness check on `result`:
  - Header: `Printing (\d+) of (\d+) log entries` → promised N.
  - Count entry lines (`^\s+\d{5}\s` — the 5-digit entry column) → got M.
  - If N parsed and M >= N: append instead
    `### NOTE: event log complete (all N entries) — console prompt not seen at the
    end; the link will resync on the next command ###`.
  - Else: keep the existing TRUNCATED banner unchanged.
- GUI: `_rides_after_pull` already reloads the session; no scary popup needed —
  but if the loaded eventlogdump text contains the TRUNCATED banner (not the
  complete-NOTE), have the Rides totals label mention the capture may be partial.
- Optional (recommended): in `_read_heavy`, raise eventlogdump idle_timeout
  30 → 45 s (the real contactor stall ate the 15 s baseline idle window; 30 s
  worked but with no margin).
- Tests (non-GUI, `tests/test_safety_transport.py`):
  1. Fake port streams `Printing 3 of 3 log entries..` + 3 entry lines, then goes
     silent (no prompt) → result contains "event log complete", NOT "incomplete";
     `last_truncated` still True (next command resyncs).
  2. Same but only 2 of 3 entries → TRUNCATED banner preserved.
  3. A non-eventlogdump command that times out keeps the TRUNCATED banner (no
     behavior change).

## Tier C — DECISION: `dumpall` in the heavy opt-in — recommend **NO** (docs instead)
Owner's literal ask included dumpall, but dumpall ≈ stats+inputs+settings+logs, all
of which the pull already captures individually (+ eventlogdump when opted in). A
second multi-minute dump doubles the contactor-exposure window for ~zero new data.
- Action (if owner accepts the recommendation):
  - Opt-in checkbox tooltip: state plainly that `dumpall` is NOT included and why
    (redundant with the pull + event log).
  - `dumpall` heavy-button tooltip (`READ_TIPS`): add "rarely needed — a full pull
    with the event log opt-in captures the same data".
- ALTERNATIVE (owner override): second checkbox "＋ also dumpall (heavy)" wired the
  same way (append to `seq` behind the same single confirm; mark cells identically).
- Test: tooltip/text assertions only (or the alternative's `sent` assertion).

## Tier D — Charts: drag-to-zoom ("zoom level" ask; Range presets alone ≠ zoom)
Minimal scope, canvas-native:
- `chart_canvas` bindings: `<ButtonPress-1>` + `<B1-Motion>` draw an x-range rubber
  band (canvas rectangle, dashed, accent color); `<ButtonRelease-1>` converts the
  two pixel x's to DATA coords and stores `self._chart_xzoom = (lo, hi)`, then
  re-renders. `<Double-1>` (and changing Range/metric) clears `_chart_xzoom`.
- To invert pixel→data: have `_chart_line` stash its transform on the instance
  (`self._chart_xform = (xlo, xhi, x0, x1)`) each render.
- Apply the zoom in `_chart_line` by filtering each series' pts to the window
  (keep ≥2 pts or ignore the zoom); when zoomed, append "· zoomed (double-click to
  reset)" to the corner note.
- Guard: drags < 8 px are clicks — ignore (don't fight the double-click reset).
- Tests: unit-level — fake `_chart_xform`, assert pixel→data conversion and that
  a zoom window filters pts; assert double-click clears `_chart_xzoom`.

## Tier E — Compare tab: slim to settings-diff (+ move gearing to a Trend chart)
Owner-offered consolidation, never green-lit — confirm at review, then:
- `_render_compare` keeps the SETTINGS CHANGED block; drop the LEARNED PACK
  CAPACITY and EFFECTIVE GEARING trend blocks (capacity already exists as
  "Trend: pack capacity"); print a pointer line "Trends across pulls live on the
  Charts tab ('Trend:' metrics)".
- Add `"Trend: effective gearing"` to `_SESSION_TRENDS` so nothing is lost:
  per-session ratio extraction the same way `compare_mod.compare_sessions` derives
  it (reuse that helper on each loaded session; ylabel "ratio (:1)", not a temp).
- Keep `compare.py` module intact (tests elsewhere may use it).
- Tests: compare output has settings-diff + pointer, no capacity/gearing blocks;
  the new gearing trend metric renders points from ≥2 fake sessions.

## Tier F — Release/process
- Reconcile docs with the new reality: `assets/info.html` heavy-dump note +
  `assets/command_reference.json` (`eventlogdump` "never auto-run" → "opt-in fold
  into Pull full database behind a confirm; also Analyze → Rides → Pull ride log").
  README Rides section if it mentions the external-parser workflow.
- Bump version → **0.18.0** (`pyproject.toml` + `src/openmbb/__init__.py`).
- Full suite green → commit (durha010, no trailer) → `packaging/build_and_install.ps1
  -Force` → launch `--sim` for owner preview.
- Push to GitHub remains the owner's explicit call — do not push unprompted.

## Execution order
A → B → C (2-line docs if recommendation accepted) → D → E → F.
A and B are independent; C/D/E each independent; F last. Commit per tier after a
green targeted run; one full-suite run before the F build.
