# OpenMBB — First Live Read-Only Session Plan

> **⚠ SUPERSEDED — this session was completed on 2026-07-10.** Kept as a record.
> The app has since changed (v0.10.2+): **FULL BASELINE now runs the quick reads +
> `set` + the small `errorlogdump` only — NOT the ~1 MB event log.** `dumplogs`
> does not exist on rev 41 (the bike replies "invalid command"). The heavy reads
> `eventlogdump` / `dumpall` are now separate, explicitly-confirmed buttons because
> on a keyed-on bike a long dump can make the BMS briefly **OPEN the drivetrain
> contactor** (a click + flashing dash; it recovers when the read finishes — this
> was observed live during exactly that read). The version references below are
> historical. **For current behavior, follow the README → Phase flow, not the
> version-specific checklist here.**

**Goal:** first real hardware session using **OpenMBB v0.10.1** on the 2017 Zero FXS
(MBB rev 41). **READS ONLY** — Stage-1 listen → Connect → FULL BASELINE → login
(view only). **No writes to the bike.** Also captures the four "hardware evidence
queue" items so the deferred write-path work can be finished later.

Verdict backing this: the v0.10.1 delta review confirmed the connect path is proven
against the real captured bytes (the showstopper reboot false-positive is dead), all
day-one operations are reads, and the write gates are double-layered and unused. This
session is **cleared to proceed.**

Physical hookup was already proven once on 2026-06-21 (see
`..\..\mbb-console-session-2026-06-21.md`) — this repeats it through the app.

---

## 0. PREFLIGHT (at the desk, before the bike)

- [ ] **Version — already updated to v0.10.1 (2026-07-10).** The desktop / Start-menu
  **OpenMBB** shortcut was upgraded in place; the installed exe is byte-identical
  (SHA256) to the fixed build. Just double-click the **OpenMBB** desktop icon and
  **confirm Help → About says v0.10.1** before heading to the bike. (If it ever shows
  0.9.0 you launched a stale copy — reinstall
  `<repo>\packaging\Output\openmbb-setup-windows-x64.exe`.)
- [ ] **Desk sanity check with the simulator** (no bike): launch, pick **SIMULATOR**
  in the port box, click **Connect && Probe**. You should see "PROMPT OK — connected",
  firmware rev 41, and Phase 1 unlock. Click around Read / Login / Analyze so the flow
  is familiar. Close it before the real session.
- [ ] **Meter the cable:** FTDI **Orange (TX) idles ~3.3 V** vs Black; **Red (+5 V) is
  taped off and connected to NOTHING.**
- [ ] **Find the COM port** (FTDI FT232R). In PowerShell:
  `Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_0403'`
  — note the COMx (it was **COM4** last time; may differ).
- [ ] **Sessions save to** `<Documents>\OpenMBB\openmbb-sessions\<stamp>_<port>\`
  by default (shown/clickable in the top status strip). Fine as-is; change via
  Session → Set save location… only if you want to.

### Wiring (recap — port is under the seat on 2017+ FX/FXS)
| FTDI wire | OBD-II pin (pigtail) | role |
|---|---|---|
| Black (GND) | 5 (Teal) | diagnostic ground |
| Yellow (RXD) | 8 (Blk/White) | bike MBB **Tx** |
| Orange (TXD) | 9 (Red/White) | bike MBB **Rx** |
| **Red (+5 V)** | — | **NEVER connected** |
38400 baud, 8-N-1, no flow control, CR-LF.

### Power the console — pick ONE
- **Easiest: plug in the AC wall charger.** It wakes the MBB/BMS; the console is fully
  live for reads (bike shows `Mode: Charging`). No key-on needed. **Caveat:**
  isolation-resistance reads are *not valid on the charger* — the app flags this
  automatically. (This is how the 2026-06-21 session was powered.)
- **Or key ON** (bike parked on stand, kill switch OFF) if you want a clean isolation
  reading. Never stream the console while riding.

---

## 1. STAGE 1 — LISTEN ONLY (prove RX wiring with zero risk)

The app's "Listen only (Stage 1)" button opens the port and **never transmits** — it
opens read-only and only reads bytes, verified TX-silent by an automated test
(`test_listen_never_transmits`). It proves the RX path and baud safely.

- [ ] **TX stays connected — your cable is a fixed GND+Tx+Rx harness, no way to unplug
  FTDI Orange. That's fine.** On a cable where you *could* unplug TX you'd get an extra
  *physical* guarantee that nothing can be sent; yours relies on the app's **software**
  TX-silence instead, which the review specifically verified. So Stage-1 listen is still
  safe and worth doing (it proves RX/baud before Connect & Probe sends the first byte).
- [ ] In OpenMBB, pick your **COMx** in the port box.
- [ ] Click **"Listen only (Stage 1)"**. It listens ~45 s and reports the byte count +
  whether boot-banner signatures were seen.
- [ ] **During the listen window, power the bike** (plug in the charger, or key ON) so
  the **boot banner streams** while it's listening.
- [ ] **Expected:** "banner signatures seen" (e.g. `Zero Motorcycles MBB`,
  `Reset Source:`, `Checking EEPROM`) and a healthy byte count.
- [ ] **ABORT conditions:**
  - *Nothing received* → RX wiring or power problem (check GND + bike-Tx→FTDI-RXD, and
    that the bike actually powered during the window).
  - *Garbage / non-text bytes* → wrong baud or **Tx/Rx swapped**. STOP and re-check
    wiring; do not proceed.

---

## 2. STAGE 2 — CONNECT & PROBE

- [ ] Click **"Connect && Probe"**. The app wakes the `ZERO MBB>` prompt (with retry),
  rejects garbage instead of trusting a stray `>`, and reads `version`.
- [ ] **Expected:** "PROMPT OK — connected", and **MBB firmware rev 41** in the status
  strip. Phase 1 (Read) unlocks.
- [ ] **This is the exact path the showstopper fix repaired** — if it connects and shows
  rev 41, the fix is confirmed on real hardware. 🎉
- [ ] If it fails with "wrong baud / Tx-Rx" or "no prompt", go back to Stage 1.
- [ ] If it warns "firmware rev X — verified against rev 41 only", note the rev and stop
  to reassess (shouldn't happen on your bike).

---

## 3. PHASE 1 — FULL BASELINE (the read that matters)

- [ ] Click the blue **★ FULL BASELINE**. It captures the quick reads + the settings
  dump (`set`) + the small `errorlogdump`. The ~1 MB event log is **NOT** in the
  baseline (see the superseded banner) — capture it separately if you want it. Reads only.
- [ ] It saves the settings dump *before* the long dumps, stamps the session power state
  (`session_meta.txt`), and unlocks Phase 2 only if the essential reads succeeded.
- [ ] **On the charger, expect** a `Mode: Charging` note and an isolation "read while
  charging — known false-low" flag on the Analyze → Health tab. That's correct behavior,
  not a fault (your 2026-06-21 reading was 32 kΩ on-charger = suspect).
- [ ] **You can stop here** — a clean baseline is the whole point of day one.

**Interpreting the output — IMPORTANT:** treat the **Analyze tab (Settings / Health)
as advisory** on day one. The **raw session files are ground truth**:
`session_raw.log` (every byte) and the per-command `NNN_*.txt` files in the session
folder. (Two known cosmetic quirks the review found, neither of which triggers on your
real data: a settings row with a *blank description* could mislabel its value, and an
*unrecognized* status Mode could over-flag isolation. Your bike has descriptions on
every row and Mode `Charging`/`Standby`, so both behave correctly — but when in doubt,
read the raw `.txt`.)

---

## 4. PHASE 2 — LOGIN (VIEW ONLY — see your write OPTIONS, don't write)

You have no writes planned — this step is purely to **see what's changeable**. Logging in
*elevates the console* so the full settings list appears, but it does **not** change any
setting (only `set <name> <value>` writes, which we are not doing). The open question:
**does login reveal the additional/writable settings, and what are your write options?**

- [ ] Go to the **Login** tab. Click **"Try known passwords"** (tries `tpsreport`, then
  `wideopenthrottle`). Or type `tpsreport` in the field and "Try this password"
  (`tpsreport` = the community MBB candidate; `wideopenthrottle` is BMS-level, a
  different subsystem — expected to fail on the MBB).
- [ ] **Both failing is a FINE outcome** — the tool stays read-only and you've learned
  the passwords don't work on rev 41. Do not force anything.
- [ ] **On success:** the app re-captures `help` + `set` and saves the post-login
  settings as the authoritative baseline. **Look at the Writes tab: which whitelisted
  settings now appear, with their current values + effect/risk?** That's your menu of
  *write options* — e.g. custom-mode speed/torque/regen, gauge/charge behavior, and the
  speedo/gearing constants (`spfront`/`sprear`/`rwhcirc`) among them. Just READ the list;
  you're seeing what you *could* change, not changing it.
  - If the list is empty at level 0 but populates after login → the tunables are
    login-gated on rev 41 (expected).
  - If nothing appears even logged in → note it; the writable settings aren't exposed on
    this firmware. Either answer is useful.
- [ ] **DO NOT WRITE.** Do not toggle "UNLOCK WRITES". Do not use the raw box for any
  `set <name> <value>`. (The app blocks all of that anyway; just don't fight it.)

---

## 5. WHAT TO CAPTURE (the "hardware evidence queue")

These read-only captures document how your bike's console actually behaves (and confirm
which readouts/write-options the tool can show). Everything is already in the session
folder; note these specifically:

1. **Login reply wording** — the exact text from `login <password>` (success AND
   failure) and from bare `login` (the level line). → pins the login-success detection.
2. **Post-login `set` format** — the full logged-in settings dump verbatim (does it show
   the tunables? what column layout?). → confirms/extends the columnar parser and whether
   the single-setting `set <name>` view is a read (so the 2-token refusal can be relaxed).
3. **Real `eventlogdump` format** — is it decoded "Riding …" text or a
   packed/hex blob? → determines whether the Rides analysis works or needs
   zero-log-parser. **⚠ Capture this deliberately via the Heavy (`eventlogdump`)
   button — NOT in baseline: a full event-log dump can briefly OPEN the drivetrain
   contactor (dash flashes, recovers when done). Bike safely PARKED.** (`dumplogs`
   is not a command on rev 41 — the bike replies "invalid command".)
4. **(Optional) Clean isolation re-read** — later, **unplug the charger, let it sit dry,
   key ON**, and re-read `bms`/`status`. Recovers to megohms → the 32 kΩ was the charger
   (ignore). Still low dry+unplugged → a real diagnostic (see the 2026-06-21 doc §3.4).

**NOT captured this session:** the accepted boolean token (yes/no vs 1/0) — that requires
a supervised *write*, which is out of scope for day one.

---

## 6. WRAP UP

- [ ] Session → **Open session folder**; confirm `session_raw.log`,
  `settings_baseline_*.txt`, the per-command `.txt` files, and (if you logged in)
  `settings_baseline_postlogin_*.txt` are all there.
- [ ] Copy the session folder somewhere safe (it's your backup + the evidence-queue data).
- [ ] Note the answers to the ⭐ question (tunables after login?) and the three evidence
  items for the next planning round.

## Safety notes
- At rest / key-off the HV is behind an open contactor — normal handling is fine.
- Stage-1 listen never transmits (verified TX-silent) — use it to sanity check any new
  cable/baud before Connect & Probe sends the first byte.
- Never connect FTDI Red (+5 V).
- Reads only. No `set <name> <value>`, no UNLOCK WRITES, no settingsrst/format/etc. (the
  app hard-blocks these, but the intent is: this is a listen-and-read day).

---

### After this session
OpenMBB is a **generic Gen2 MBB read/diagnostics tool** — the point of day one (and the
foreseeable future) is to *read your bike and see what's changeable*, not to write. Bring
the captures home; the login/dumplogs findings just confirm what the tool can display and
which write options exist. **No write session is planned.** The only follow-up work is a
small, optional **v0.10.2** quality pass (parser/isolation polish + a couple of tests that
should discriminate) — none of it needed for reading. If you ever *do* decide to change a
setting later, that would be its own supervised write session, planned separately.
