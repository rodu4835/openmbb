"""OpenMBB — phase-gated serial console for Gen2 MBB-based Zero motorcycles.

Phases:  0 Connect -> 1 Read (baseline) -> 2 Login -> 3 Writes (whitelist-only)

Safety model (enforced in the TRANSPORT layer, not just the UI):
  * BLOCKLIST as an informed-consent gate: destructive/dangerous commands are
    refused from every flow, and the raw box sends one ONLY after the owner has
    read the consequences and typed 'confirm' (SECURITY.md calls that intended
    behaviour, and it is). The refusals with NO override are the control
    character / non-ASCII / multi-line ones.
  * Writes through the write flow are WHITELIST-only, and only for settings
    actually present in the live `set` dump; a raw-box `set` faces the same
    typed-'confirm' gate rather than the whitelist.
  * Every write: re-read current -> confirm old->new -> auto-backup full
    settings dump -> journal intent -> send -> read-back verify -> journal
    what the bike actually reported (a refusal is recorded in its own words).

Console work is done PARKED, key on, kill switch off. Never while riding.
"""

APP_NAME = "OpenMBB"
__version__ = "0.28.0"

# The day this version was released, ISO. Bumped WITH __version__ and
# checked against the tag by the release gate. It exists so a copy can say
# how old it is without asking anybody: OpenMBB makes no network requests,
# so it can never know what is newer, only how stale it is itself.
__release_date__ = "2026-09-01"
