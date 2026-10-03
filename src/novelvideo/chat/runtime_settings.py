"""Secret-free, instance-local configuration for the TEAM chat Agent."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from novelvideo.shared.runtime_env import edition
from novelvideo.text_runtime_settings import _connect
from novelvideo.text_task_runtime.models import TextTaskReasoningEffort, validate_text_task_model_name

SETTINGS_KEY = "chat_runtime_config"
ChatBackend = Literal["hermes", "codex", "workbuddy", "deepseek_harness"]
DEFAULT_MODELS = {
    "hermes": "", "codex": "gpt-5.6-sol", "workbuddy": "default-model",
    "deepseek_harness": "deepseek-v4-flash-vision-exp",
}


class ChatRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: ChatBackend = "hermes"
    model: str = ""
    reasoning_effort: TextTaskReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_model(self):
        model = self.model.strip()
        if model or self.backend != "hermes":
            model = validate_text_task_model_name(model)
        object.__setattr__(self, "model", model)
        return self


def _environment_settings() -> ChatRuntimeSettings:
    backend = (os.getenv("DRAMACLAW_CHAT_BACKEND") or os.getenv("SUPERTALE_CHAT_BACKEND") or "hermes").strip().lower()
    model = os.getenv("DRAMACLAW_CHAT_MODEL") or os.getenv("SUPERTALE_CHAT_MODEL") or DEFAULT_MODELS.get(backend, "")
    effort = os.getenv("DRAMACLAW_CHAT_REASONING_EFFORT") or os.getenv("SUPERTALE_CHAT_REASONING_EFFORT") or None
    return ChatRuntimeSettings(backend=backend, model=model, reasoning_effort=effort)


def load_chat_runtime_settings() -> ChatRuntimeSettings:
    if edition() != "team":
        return _environment_settings()
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM runtime_settings WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    finally:
        conn.close()
    return ChatRuntimeSettings.model_validate_json(row[0]) if row else _environment_settings()


def save_chat_runtime_settings(*, backend: str, model: str, reasoning_effort: str | None = None) -> ChatRuntimeSettings:
    if edition() != "team":
        raise PermissionError("Chat runtime settings require TEAM edition")
    settings = ChatRuntimeSettings(backend=backend, model=model, reasoning_effort=reasoning_effort)
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (SETTINGS_KEY, json.dumps(settings.model_dump()), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return settings
