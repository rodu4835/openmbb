"""Config persistence + save-path behavior (no display needed)."""

import os

from openmbb import config
from openmbb.transport import SessionLogger


def test_config_roundtrip(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".openmbb"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_dir / "config.json")

    # G1: default is a fixed, user-visible dir (never None / never cwd)
    assert config.get_log_dir() == config.DEFAULT_LOG_DIR
    assert config.set_log_dir(str(tmp_path / "logs"))
    assert config.get_log_dir() == str(tmp_path / "logs")
    assert config.set_log_dir(None)              # clearing falls back to default
    assert config.get_log_dir() == config.DEFAULT_LOG_DIR


def test_config_read_is_failure_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "does_not_exist.json")
    assert config.load_config() == {}
    assert config.get_log_dir() == config.DEFAULT_LOG_DIR


def test_config_atomic_write_and_corrupt_recovery(tmp_path, monkeypatch):
    # G5: a corrupt config is set aside (not silently dropped) and recoverable
    cfg_dir = tmp_path / ".openmbb"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_dir / "config.json")
    assert config.set_log_dir(str(tmp_path / "x"))
    assert config.get_log_dir() == str(tmp_path / "x")
    (cfg_dir / "config.json").write_text("{ not valid json", encoding="utf-8")
    assert config.load_config() == {}                       # tolerant
    assert (cfg_dir / "config.json.bad").exists()           # set aside
    assert config.config_was_corrupt()


def test_session_folders_are_collision_proof(tmp_path):
    # G7: two loggers created back-to-back must not share a folder
    a = SessionLogger(base_dir=str(tmp_path), tag="x")
    b = SessionLogger(base_dir=str(tmp_path), tag="x")
    assert a.dir != b.dir


def test_session_logger_uses_base_and_subfolder(tmp_path):
    log = SessionLogger(base_dir=str(tmp_path), tag="unit")
    # sessions always nest under a self-contained openmbb-sessions folder
    assert os.path.normpath(log.dir).startswith(
        os.path.normpath(str(tmp_path / "openmbb-sessions")))
    assert os.path.isdir(log.dir)
    log.raw("TX", b"hi")
    assert os.path.isfile(log.raw_path)
