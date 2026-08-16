from __future__ import annotations

import json
import sqlite3

import pytest

from novelvideo import config
from novelvideo.knowledge_runtime import (
    KnowledgeRuntimeError,
    OllamaProbeResult,
    OllamaSettings,
    load_knowledge_runtime_settings,
    save_knowledge_runtime_settings,
)


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))


def test_save_requires_matching_successful_probe(monkeypatch, tmp_path) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    settings = OllamaSettings(model="nomic-embed-text")

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        save_knowledge_runtime_settings(settings)

    assert exc_info.value.code == "OLLAMA_PROBE_REQUIRED"

    mismatched_probe = OllamaProbeResult(
        model="other-model",
        dimension=768,
        digest="sha256:other",
        probed_at="2026-08-15T00:00:00+00:00",
    )
    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        save_knowledge_runtime_settings(settings, probe=mismatched_probe)

    assert exc_info.value.code == "OLLAMA_PROBE_REQUIRED"


def test_settings_round_trip_uses_dedicated_runtime_key(monkeypatch, tmp_path) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    probe = OllamaProbeResult(
        model="nomic-embed-text:latest",
        dimension=768,
        digest="sha256:abc123",
        probed_at="2026-08-15T01:02:03+00:00",
    )
    saved = save_knowledge_runtime_settings(
        OllamaSettings(
            base_url="http://localhost:11434/api/embed/",
            model="nomic-embed-text:latest",
            batch_size=16,
        ),
        probe=probe,
    )

    assert saved == OllamaSettings(
        base_url="http://localhost:11434",
        model="nomic-embed-text:latest",
        dimension=768,
        digest="sha256:abc123",
        batch_size=16,
        probed_at="2026-08-15T01:02:03+00:00",
    )
    assert load_knowledge_runtime_settings() == saved

    db_path = tmp_path / "state" / "local" / "settings.db"
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT key, value FROM runtime_settings"
        ).fetchall()
    assert [row[0] for row in rows] == ["knowledge_runtime_ollama_v1"]
    assert json.loads(rows[0][1]) == {
        "base_url": "http://localhost:11434",
        "provider": "ollama",
        "model": "nomic-embed-text:latest",
        "dimension": 768,
        "digest": "sha256:abc123",
        "batch_size": 16,
        "probed_at": "2026-08-15T01:02:03+00:00",
    }


def test_default_settings_are_local_ollama() -> None:
    assert OllamaSettings() == OllamaSettings(
        base_url="http://127.0.0.1:11434",
        model="",
        dimension=0,
        digest="",
        batch_size=8,
        probed_at="",
    )
