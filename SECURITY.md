# Security Policy

OpenMBB talks to a motorcycle's Main Bike Board over a serial console and can, on
explicit confirmation, **write settings** or **send destructive commands**. A flaw
that lets the tool send something dangerous it shouldn't — or that could damage a
bike — is treated as a security issue, not just a bug.

## Reporting a vulnerability

Please report suspected security issues **privately** rather than opening a public
issue, so a fix can land before details are widely known:

- Use GitHub's **[Report a vulnerability](../../security/advisories/new)** (Security
  → Advisories) on this repository, **or**
- Open a normal issue that says only *"security — please contact me"* (no details),
  and the details can be exchanged privately.

Helpful things to include: what you did, what the tool sent (or would send) to the
bike, the model/firmware, and why it's dangerous.

## Scope

In scope — examples of what's worth reporting:

- A way to get a **destructive command onto the wire with no typed `confirm` at
  all** — that is the line worth defending. Note that the raw box's refusal of a
  blocklisted `set` is *deliberately* overridable by typing `confirm`, so "I typed
  confirm and it sent it" is intended behaviour, not a finding. The refusals that
  are unconditional, and whose bypass would be a real report, are the control
  character / non-ASCII / multi-line ones.
- A write that **skips the whitelist re-validation** in `Transport.write_setting`.
- A path that could **brick a component or leave the bike in an unsafe state**
  more easily than the documented, confirmed flow.
- A **PII leak** (VIN, serials, passwords) into logs, saved reports, or the repo.

Out of scope:

- The *documented, confirmed* behavior of destructive commands. OpenMBB is
  deliberately read-first but does **not** hard-block dangerous commands on your
  own bike — it gates them behind a typed `confirm`. That's by design (see the
  README "Safety model"), not a vulnerability.
- Anything requiring a bike other than a **2017 Zero FXS at MBB rev 41**, which is
  the only hardware the safety lists are verified against — but a report that the
  tool is *unsafe* on another model is very welcome.

## No warranty

OpenMBB is an unofficial hobby tool provided with no warranty (see LICENSE). It is
not affiliated with Zero Motorcycles or Sevcon/BorgWarner.
