from __future__ import annotations

import json
import sqlite3

import pytest

from novelvideo import config
from novelvideo import model_gateway_settings as gateway
from novelvideo.text_runtime_settings import (
    load_text_runtime_settings,
    save_text_runtime_settings,
)


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, reference: str, value: str) -> None:
        self.values[reference] = value

    def get(self, reference: str) -> str | None:
        return self.values.get(reference)

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    store = MemoryCredentialStore()
    monkeypatch.setattr(gateway, "_credential_store", lambda: store)
    return store


def _database_values() -> dict[str, str]:
    with sqlite3.connect(gateway._settings_db_path()) as conn:
        return dict(conn.execute("SELECT key, value FROM runtime_settings"))


def test_gateway_and_relay_secrets_are_references_in_sqlite(isolated_store):
    gateway.save_official_newapi_key(api_key="official-secret")
    gateway.save_custom_newapi_gateway(
        base_url="https://gateway.example", api_key="custom-secret"
    )
    gateway.save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=1800,
        access_key_id="relay-ak",
        access_key_secret="relay-sk",
    )

    values = _database_values()
    assert all(secret not in repr(values) for secret in (
        "official-secret", "custom-secret", "relay-ak", "relay-sk"
    ))
    assert values["official_newapi_api_key"].startswith("credential://")
    assert gateway.get_effective_newapi_config().api_key == "custom-secret"
    assert gateway.get_effective_media_relay_config().access_key_secret == "relay-sk"


def test_provider_channel_secret_is_not_embedded_in_json(isolated_store):
    saved = gateway.save_newapi_provider_channels([
        {"provider": "openai", "upstreamKey": "upstream-secret", "baseUrl": ""}
    ])

    raw = _database_values()["custom_newapi_provider_channels"]
    assert "upstream-secret" not in raw
    assert "upstreamKeyRef" in raw
    assert saved[0]["upstreamKey"] == "upstream-secret"
    assert gateway.get_newapi_provider_channels()[0]["upstreamKey"] == "upstream-secret"


def test_legacy_gateway_secret_is_migrated_and_cleared_from_sqlite(isolated_store):
    gateway._write_many({
        "model_gateway_mode": gateway.MODE_OFFICIAL,
        "official_newapi_api_key": "legacy-gateway-secret",
    })

    assert gateway.get_effective_newapi_config().api_key == "legacy-gateway-secret"
    raw = _database_values()["official_newapi_api_key"]
    assert raw.startswith("credential://")
    assert "legacy-gateway-secret" not in raw


def test_legacy_text_secret_is_migrated_and_cleared_from_sqlite(isolated_store):
    conn = gateway._connect()
    try:
        conn.execute(
            "INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)",
            (
                "text_runtime_config",
                json.dumps({
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash",
                    "api_key": "legacy-text-secret",
                }),
                "now",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert load_text_runtime_settings().api_key == "legacy-text-secret"
    raw = _database_values()["text_runtime_config"]
    assert "legacy-text-secret" not in raw
    assert "api_key_ref" in raw

    saved = save_text_runtime_settings(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="new-text-secret",
    )
    assert saved.api_key == "new-text-secret"
    assert "new-text-secret" not in _database_values()["text_runtime_config"]
