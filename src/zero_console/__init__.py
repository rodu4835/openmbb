"""Zero Console — phase-gated serial console tool for a 2017 Zero FXS MBB.

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

APP_NAME = "Zero Console"
__version__ = "0.4.0"
