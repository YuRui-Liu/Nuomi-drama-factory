"""Per-runtime model catalog for the text task routing panel.

The front-end renders a drop-down of suggested model IDs for each runtime.
WorkBuddy's CLI does not expose a `models` sub-command and the actual model
list comes from the cloud, so we have to seed the drop-down from somewhere
local:

* **workbuddy**: read the user/project ``.codebuddy/models.json`` (the file the
  WorkBuddy CLI itself honours for ``availableModels``/``models[].id``), then
  fall back to a small built-in default list.
* **codex**: the Codex CLI accepts arbitrary model IDs and exposes no
  enumeration command either. We seed a built-in default list that the user
  can extend via ``TEXT_TASK_DEFAULT_MODELS_CODEX`` (comma separated).
* **model_api**: model IDs are arbitrary OpenAI-compatible names configured
  via the ``兼容网关`` panel. We return an empty list and keep the field free-form.
* **deepseek_harness**: 模型由运行时级 preset 决定，内置默认仅作为下拉候选，
  可用 ``TEXT_TASK_DEFAULT_MODELS_DEEPSEEK_HARNESS`` 覆盖。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# The model IDs the WorkBuddy CLI actually advertises for `--model`. This is the
# list printed by `codebuddy --help` and embedded in the product's local
# `models` catalog; the server echoes the same set as "Currently supported
# models for your account".
#
# Deliberately NOT included: "Hy3"/"Hy4 preview"/"Deepseek-V4.1-Flash". Those
# belong to the separate *CodeBuddy* product (`Copilot` CLI, endpoint
# copilot.tencent.com) and are rejected by the WorkBuddy CLI with
# `400 model [...] service info not found`. IDs are also case-sensitive, so
# "Hy3" is not "hy3". Entries here can still be entitlement-gated per account;
# put the account's real list in `.codebuddy/models.json` `availableModels`.
_WORKBUDDY_DEFAULT_MODELS = (
    "default-model",
    "gemini-3.1-pro",
    "gemini-3.0-flash",
    "gemini-3.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "deepseek-v3-2-volc",
    "glm-5.0",
    "kimi-k2.5",
)
_CODEX_DEFAULT_MODELS = (
    "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
)
_DEEPSEEK_HARNESS_DEFAULT_MODELS = ("deepseek-v4-flash-vision-exp",)
_RUNTIMES = ("workbuddy", "codex", "model_api", "deepseek_harness")


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _read_models_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, IsADirectoryError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _models_json_paths() -> list[Path]:
    """Project level takes precedence over user level."""

    candidates: list[Path] = []
    project_dir = os.environ.get("NUOMI_PROJECT_ROOT", "").strip()
    if project_dir:
        candidates.append(Path(project_dir) / ".codebuddy" / "models.json")
    candidates.append(Path.cwd() / ".codebuddy" / "models.json")
    candidates.append(Path.home() / ".codebuddy" / "models.json")
    return candidates


def _load_workbuddy_models_from_models_json() -> tuple[list[str], bool]:
    """Return ``(explicit_ids, found)`` where ``found`` indicates the caller
    must not merge in the built-in defaults."""

    seen: set[str] = set()
    out: list[str] = []
    for path in _models_json_paths():
        data = _read_models_json(path)
        if data is None:
            continue
        available = data.get("availableModels")
        if isinstance(available, list) and available:
            for mid in available:
                if isinstance(mid, str):
                    mid = mid.strip()
                    if mid and mid not in seen:
                        seen.add(mid)
                        out.append(mid)
            return out, True  # availableModels is authoritative when present
        models = data.get("models")
        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict):
                    mid = entry.get("id")
                    if isinstance(mid, str):
                        mid = mid.strip()
                        if mid and mid not in seen:
                            seen.add(mid)
                            out.append(mid)
            if out:
                return out, True
    return out, False


def _default_models_for(runtime: str) -> list[str]:
    env_key = f"TEXT_TASK_DEFAULT_MODELS_{runtime.upper()}"
    raw = os.environ.get(env_key, "").strip()
    if raw:
        return _dedupe_preserve_order([m.strip() for m in raw.split(",") if m.strip()])
    if runtime == "workbuddy":
        return list(_WORKBUDDY_DEFAULT_MODELS)
    if runtime == "codex":
        return list(_CODEX_DEFAULT_MODELS)
    if runtime == "deepseek_harness":
        return list(_DEEPSEEK_HARNESS_DEFAULT_MODELS)
    return []


def get_runtime_model_catalog() -> dict[str, list[str]]:
    """Return ``{runtime: [model_id, ...]}`` for every known runtime.

    When the user (or the project) explicitly configures WorkBuddy models via
    ``.codebuddy/models.json``, that list wins and the built-in defaults are
    skipped — same semantics the WorkBuddy CLI applies to ``availableModels``.
    """

    result: dict[str, list[str]] = {}
    for runtime in _RUNTIMES:
        explicit: list[str] = []
        found = False
        if runtime == "workbuddy":
            explicit, found = _load_workbuddy_models_from_models_json()
        if found:
            merged = _dedupe_preserve_order(explicit)
        else:
            merged = _dedupe_preserve_order(_default_models_for(runtime))
        result[runtime] = merged
    return result
