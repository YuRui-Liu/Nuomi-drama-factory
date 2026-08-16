"""Persistent settings for the local knowledge runtime."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from novelvideo.sqlite_pragmas import configure_sqlite_connection

OLLAMA_SETTINGS_KEY = "knowledge_runtime_ollama_v1"


class KnowledgeRuntimeError(RuntimeError):
    """A knowledge-runtime failure with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = "http://127.0.0.1:11434"
    provider: str = field(default="ollama", init=False)
    model: str = ""
    dimension: int = 0
    digest: str = ""
    batch_size: int = 8
    probed_at: str = ""


@dataclass(frozen=True)
class OllamaProbeResult:
    model: str
    dimension: int
    digest: str
    probed_at: str


def _settings_db_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "settings.db"


def _connect() -> sqlite3.Connection:
    path = _settings_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    configure_sqlite_connection(connection)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
    connection.commit()
    return connection


def load_knowledge_runtime_settings() -> OllamaSettings:
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (OLLAMA_SETTINGS_KEY,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return OllamaSettings()
    try:
        payload = json.loads(str(row["value"]))
        return OllamaSettings(
            base_url=str(payload.get("base_url") or "http://127.0.0.1:11434"),
            model=str(payload.get("model") or ""),
            dimension=int(payload.get("dimension") or 0),
            digest=str(payload.get("digest") or ""),
            batch_size=int(payload.get("batch_size") or 8),
            probed_at=str(payload.get("probed_at") or ""),
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise KnowledgeRuntimeError(
            "Stored Ollama settings are invalid.", code="OLLAMA_SETTINGS_INVALID"
        ) from exc


def save_knowledge_runtime_settings(
    settings: OllamaSettings,
    *,
    probe: OllamaProbeResult | None = None,
) -> OllamaSettings:
    from .ollama import normalize_ollama_base_url

    if (
        probe is None
        or not settings.model.strip()
        or probe.model != settings.model
        or probe.dimension <= 0
    ):
        raise KnowledgeRuntimeError(
            "A successful probe for the selected Ollama model is required.",
            code="OLLAMA_PROBE_REQUIRED",
        )
    if settings.batch_size <= 0:
        raise KnowledgeRuntimeError(
            "Ollama batch size must be positive.", code="OLLAMA_SETTINGS_INVALID"
        )

    saved = replace(
        settings,
        base_url=normalize_ollama_base_url(settings.base_url),
        model=settings.model.strip(),
        dimension=probe.dimension,
        digest=probe.digest,
        probed_at=probe.probed_at,
    )
    payload = json.dumps(asdict(saved), ensure_ascii=False, separators=(",", ":"))
    now = datetime.now(timezone.utc).isoformat()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO runtime_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (OLLAMA_SETTINGS_KEY, payload, now),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return saved
