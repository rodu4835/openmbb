"""Tiny persistent config (JSON in the user's home dir).

Currently just remembers the chosen log/save base directory so it survives
across runs. Kept dead simple and failure-tolerant — a missing or unreadable
config never stops the app, it just falls back to defaults.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".openmbb"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def get_log_dir():
    """Return the configured save base dir, or None to use the default (cwd)."""
    d = load_config().get("log_dir")
    return d or None


def set_log_dir(path):
    cfg = load_config()
    cfg["log_dir"] = str(path) if path else None
    return save_config(cfg)
