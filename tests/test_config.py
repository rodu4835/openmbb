"""Config persistence + save-path behavior (no display needed)."""

import os

from openmbb import config
from openmbb.transport import SessionLogger


def test_config_roundtrip(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".openmbb"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_dir / "config.json")

    assert config.get_log_dir() is None          # nothing saved yet
    assert config.set_log_dir(str(tmp_path / "logs"))
    assert config.get_log_dir() == str(tmp_path / "logs")
    assert config.set_log_dir(None)              # clearing falls back to default
    assert config.get_log_dir() is None


def test_config_read_is_failure_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "does_not_exist.json")
    assert config.load_config() == {}
    assert config.get_log_dir() is None


def test_session_logger_uses_base_and_subfolder(tmp_path):
    log = SessionLogger(base_dir=str(tmp_path), tag="unit")
    # sessions always nest under a self-contained openmbb-sessions folder
    assert os.path.normpath(log.dir).startswith(
        os.path.normpath(str(tmp_path / "openmbb-sessions")))
    assert os.path.isdir(log.dir)
    log.raw("TX", b"hi")
    assert os.path.isfile(log.raw_path)
