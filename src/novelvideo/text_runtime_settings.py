"""Settings for the global, ordinary-text model runtime."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from novelvideo.model_gateway_settings import mask_secret
from novelvideo.shared.runtime_env import is_ce_effective
from novelvideo.sqlite_pragmas import configure_sqlite_connection

DEFAULT_PROVIDER = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
VALID_PROVIDERS = {"deepseek", "dramaclaw", "openai_compatible"}
SETTINGS_KEY = "text_runtime_config"


@dataclass(frozen=True)
class TextRuntimeSettings:
    source: str
    provider: str
    base_url: str
    api_key: str
    model: str


def _settings_db_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "settings.db"


def _connect() -> sqlite3.Connection:
    path = _settings_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    configure_sqlite_connection(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
    conn.commit()
    return conn


def _normalize(provider: str, base_url: str, model: str) -> tuple[str, str, str]:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in VALID_PROVIDERS:
        raise ValueError(f"unsupported text model provider: {normalized_provider}")
    normalized_url = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("baseUrl must be a valid http or https URL")
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise ValueError("model must be non-empty")
    return normalized_provider, normalized_url, normalized_model


def _from_values(*, source: str, provider: str, base_url: str, api_key: str, model: str) -> TextRuntimeSettings:
    provider, base_url, model = _normalize(provider, base_url, model)
    return TextRuntimeSettings(
        source=source,
        provider=provider,
        base_url=base_url,
        api_key=str(api_key or "").strip(),
        model=model,
    )


def load_text_runtime_settings() -> TextRuntimeSettings:
    """Resolve CE from SQLite on every call and EE from deployment env."""
    if not is_ce_effective():
        return _from_values(
            source="environment",
            provider=os.environ.get("TEXT_MODEL_PROVIDER", DEFAULT_PROVIDER),
            base_url=os.environ.get("TEXT_MODEL_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("TEXT_MODEL_API_KEY", ""),
            model=os.environ.get("TEXT_MODEL_NAME", DEFAULT_MODEL),
        )

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (SETTINGS_KEY,)
        ).fetchone()
    finally:
        conn.close()
    payload = json.loads(str(row[0])) if row else {}
    return _from_values(
        source="database",
        provider=payload.get("provider", DEFAULT_PROVIDER),
        base_url=payload.get("base_url", DEFAULT_BASE_URL),
        api_key=payload.get("api_key", ""),
        model=payload.get("model", DEFAULT_MODEL),
    )


def save_text_runtime_settings(
    *, provider: str, base_url: str, model: str,
    api_key: str | None = None, clear_api_key: bool = False,
) -> TextRuntimeSettings:
    """Persist CE settings, preserving an omitted API key unless cleared."""
    if not is_ce_effective():
        raise PermissionError("text runtime settings can only be saved in CE")
    provider, base_url, model = _normalize(provider, base_url, model)
    current_key = load_text_runtime_settings().api_key
    next_key = "" if clear_api_key else current_key if api_key is None else str(api_key).strip()
    settings = TextRuntimeSettings(
        source="database", provider=provider, base_url=base_url,
        api_key=next_key, model=model,
    )
    payload = asdict(settings)
    payload.pop("source")
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (SETTINGS_KEY, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return settings


def text_runtime_status(settings: TextRuntimeSettings | None = None) -> dict[str, object]:
    effective = settings or load_text_runtime_settings()
    key_configured = bool(effective.api_key)
    return {
        "source": effective.source,
        "provider": effective.provider,
        "baseUrl": effective.base_url,
        "model": effective.model,
        "configured": bool(key_configured and effective.base_url and effective.model),
        "apiKeyConfigured": key_configured,
        "apiKeyPreview": mask_secret(effective.api_key),
    }
