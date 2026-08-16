from __future__ import annotations

import pytest

from novelvideo import config
from novelvideo.text_runtime_settings import (
    load_text_runtime_settings,
    save_text_runtime_settings,
    text_runtime_status,
)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)


def test_text_runtime_defaults(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    settings = load_text_runtime_settings()
    assert (settings.provider, settings.base_url, settings.model) == (
        "deepseek", "https://api.deepseek.com", "deepseek-v4-flash"
    )
    assert settings.api_key == ""
    assert settings.source == "database"


def test_text_runtime_roundtrip_and_key_lifecycle(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    saved = save_text_runtime_settings(
        provider="openai_compatible", base_url="https://llm.example/v1/",
        model="custom-model", api_key="sk-original-secret",
    )
    assert saved.base_url == "https://llm.example/v1"
    assert load_text_runtime_settings() == saved
    preserved = save_text_runtime_settings(
        provider="dramaclaw", base_url="http://gateway.internal/v1",
        model="new-model", api_key=None,
    )
    assert preserved.api_key == "sk-original-secret"
    cleared = save_text_runtime_settings(
        provider="dramaclaw", base_url="http://gateway.internal/v1",
        model="new-model", clear_api_key=True,
    )
    assert cleared.api_key == ""


@pytest.mark.parametrize("field,value", [
    ("provider", "other"), ("base_url", "ftp://bad.example"), ("model", "   ")
])
def test_text_runtime_rejects_invalid_values(monkeypatch, tmp_path, field, value):
    _isolate(monkeypatch, tmp_path)
    values = {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}
    values[field] = value
    with pytest.raises(ValueError):
        save_text_runtime_settings(**values)


def test_text_runtime_ee_reads_environment_not_database(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    save_text_runtime_settings(provider="deepseek", base_url="https://stale.example", model="stale", api_key="stale-secret")
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("TEXT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://ee.example/v1")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "ee-secret")
    monkeypatch.setenv("TEXT_MODEL_NAME", "ee-model")
    settings = load_text_runtime_settings()
    assert (settings.source, settings.provider, settings.base_url, settings.api_key, settings.model) == (
        "environment", "openai_compatible", "https://ee.example/v1", "ee-secret", "ee-model"
    )


def test_public_status_never_exposes_api_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    settings = save_text_runtime_settings(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-v4-flash", api_key="sk-super-secret")
    status = text_runtime_status(settings)
    assert status["configured"] is True
    assert status["apiKeyConfigured"] is True
    assert status["apiKeyPreview"] == "sk-s...cret"
    assert "api_key" not in status
    assert "sk-super-secret" not in repr(status)
