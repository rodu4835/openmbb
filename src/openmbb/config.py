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


def config_was_corrupt():
    """True if a prior load found a corrupt config and set it aside."""
    return _bad_path().exists()
