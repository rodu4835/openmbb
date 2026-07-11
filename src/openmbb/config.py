"""Tiny persistent config (JSON in the user's home dir).

Remembers the chosen log/save base directory so it survives across runs. Kept
dead simple and failure-tolerant — a missing or unreadable config never stops
the app, it just falls back to the default. Writes are atomic and a corrupt
config is set aside (not silently discarded) so the user can be told once.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".openmbb"
CONFIG_PATH = CONFIG_DIR / "config.json"
# G1: a fixed, user-visible default instead of the process cwd. cwd varies by
# how the app was launched — from the installer's desktop shortcut it lands
# inside the install dir under %LocalAppData%, where sessions are easy to lose.
DEFAULT_LOG_DIR = str(Path.home() / "Documents" / "OpenMBB")


def _bad_path():
    return CONFIG_PATH.with_suffix(".json.bad")


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}                 # fresh install — not an error
    except Exception:
        # G5: a corrupt/truncated config is set aside rather than silently
        # dropped, so config_was_corrupt() can surface a one-time note
        try:
            os.replace(str(CONFIG_PATH), str(_bad_path()))
        except Exception:
            pass
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # G5: write atomically (tmp + os.replace) so a crash mid-write can't
        # leave a truncated config that loses the saved location
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(CONFIG_PATH))
        return True
    except Exception:
        return False


def get_log_dir():
    """Configured save base dir, or the fixed default (never None)."""
    return load_config().get("log_dir") or DEFAULT_LOG_DIR


def set_log_dir(path):
    cfg = load_config()
    cfg["log_dir"] = str(path) if path else None
    return save_config(cfg)


def get(key, default=None):
    """Read one config value (never raises)."""
    return load_config().get(key, default)


def set(key, value):
    """Write one config value (atomic; returns True on success)."""
    cfg = load_config()
    cfg[key] = value
    return save_config(cfg)


# E5: remembered login passwords (opt-in). These are the publicly-documented
# service passwords, not secrets, so plaintext in the user's own config is
# acceptable — the same class of value that used to be hard-coded in source.
def get_saved_passwords():
    val = load_config().get("saved_passwords") or []
    return [p for p in val if isinstance(p, str) and p]


def add_saved_password(pw):
    if not pw:
        return False
    saved = get_saved_passwords()
    if pw in saved:
        return True
    saved.append(pw)
    return set("saved_passwords", saved)


def clear_saved_passwords():
    return set("saved_passwords", [])


# E6: display unit preference ("mi" or "km"); default km (the bike's native unit).
def get_units():
    return "mi" if load_config().get("units") == "mi" else "km"


def set_units(units):
    return set("units", "mi" if units == "mi" else "km")


# temperature display preference ("C" or "F"); default C (the bike's native unit).
def get_temp_units():
    return "F" if load_config().get("temp_units") == "F" else "C"


def set_temp_units(units):
    return set("temp_units", "F" if units == "F" else "C")


def config_was_corrupt():
    """True if a prior load found a corrupt config and set it aside."""
    return _bad_path().exists()
