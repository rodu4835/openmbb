"""OpenMBB — phase-gated serial console for Gen2 MBB-based Zero motorcycles.

Phases:  0 Connect -> 1 Read (baseline) -> 2 Login -> 3 Writes (whitelist-only)

Safety model (enforced in the TRANSPORT layer, not just the UI):
  * HARD BLOCKLIST: destructive/dangerous commands can never be sent, from any
    UI path including the raw-command box.
  * Writes are WHITELIST-only, and only for settings actually present in the
    live `set` dump.
  * Every write: re-read current -> confirm old->new -> auto-backup full
    settings dump -> send -> read-back verify -> journal (per-entry revert).

Console work is done PARKED, key on, kill switch off. Never while riding.
"""

APP_NAME = "OpenMBB"
__version__ = "0.27.0"

# The day this version was released, ISO. Bumped WITH __version__ and
# checked against the tag by the release gate. It exists so a copy can say
# how old it is without asking anybody: OpenMBB makes no network requests,
# so it can never know what is newer, only how stale it is itself.
__release_date__ = "2026-08-31"
