"""Tests for config.py — environment variable and config file loading."""

import os
import pytest

from koncile_mcp.config import Config, DEFAULT_API_URL, CONFIG_FILE, _read_config_file


def test_from_env_with_all_vars(monkeypatch):
    monkeypatch.setenv("KONCILE_API_URL", "https://custom-api.koncile.ai/")
    monkeypatch.setenv("KONCILE_API_KEY", "sk-abc")
    monkeypatch.setenv("KONCILE_REQUEST_TIMEOUT", "30")

    cfg = Config.from_env()
    assert cfg.api_url == "https://custom-api.koncile.ai"  # trailing slash stripped
    assert cfg.api_key == "sk-abc"
    assert cfg.request_timeout == 30.0


def test_from_env_default_url(monkeypatch):
    monkeypatch.delenv("KONCILE_API_URL", raising=False)
    monkeypatch.setenv("KONCILE_API_KEY", "sk-abc")

    cfg = Config.from_env()
    assert cfg.api_url == DEFAULT_API_URL


def test_from_env_default_timeout(monkeypatch):
    monkeypatch.setenv("KONCILE_API_KEY", "sk-abc")
    monkeypatch.delenv("KONCILE_REQUEST_TIMEOUT", raising=False)

    cfg = Config.from_env()
    assert cfg.request_timeout == 120.0


def test_from_env_missing_key_exits(monkeypatch):
    monkeypatch.setenv("KONCILE_API_URL", "http://localhost:8000")
    monkeypatch.delenv("KONCILE_API_KEY", raising=False)
    monkeypatch.setattr("koncile_mcp.config._read_config_file", lambda: {})

    with pytest.raises(SystemExit):
        Config.from_env()


def test_from_env_key_missing_without_url_exits(monkeypatch):
    monkeypatch.delenv("KONCILE_API_URL", raising=False)
    monkeypatch.delenv("KONCILE_API_KEY", raising=False)
    monkeypatch.setattr("koncile_mcp.config._read_config_file", lambda: {})

    with pytest.raises(SystemExit):
        Config.from_env()


def test_config_file_fallback(monkeypatch, tmp_path):
    config_file = tmp_path / "config"
    config_file.write_text("KONCILE_API_KEY=sk-from-file\nKONCILE_API_URL=https://file.api.ai\n")
    monkeypatch.delenv("KONCILE_API_KEY", raising=False)
    monkeypatch.delenv("KONCILE_API_URL", raising=False)
    monkeypatch.setattr("koncile_mcp.config.CONFIG_FILE", config_file)

    cfg = Config.from_env()
    assert cfg.api_key == "sk-from-file"
    assert cfg.api_url == "https://file.api.ai"


def test_env_overrides_config_file(monkeypatch, tmp_path):
    config_file = tmp_path / "config"
    config_file.write_text("KONCILE_API_KEY=sk-from-file\n")
    monkeypatch.setenv("KONCILE_API_KEY", "sk-from-env")
    monkeypatch.setattr("koncile_mcp.config.CONFIG_FILE", config_file)

    cfg = Config.from_env()
    assert cfg.api_key == "sk-from-env"


def test_config_is_frozen():
    cfg = Config(api_url="http://localhost", api_key="key", request_timeout=10)
    with pytest.raises(AttributeError):
        cfg.api_url = "http://other"
