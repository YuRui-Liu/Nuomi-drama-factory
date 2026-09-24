"""Contract tests for runtime presets on the task-runtime config API."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api.routes import model_gateway
from novelvideo.text_task_runtime.settings import load_global_routes

CONFIG_PATH = "/api/v1/model-gateway/task-runtime/config"


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Keep the real sqlite settings store but point STATE_DIR at a tmp dir.

    Mirrors tests/test_model_gateway_settings.py so the round-trip never touches
    the developer's state.
    """
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(model_gateway.router, prefix="/api/v1")
    with TestClient(app) as test_client:
        yield test_client


def test_task_runtime_config_round_trips_runtime_presets(client) -> None:
    payload = {
        "routes": {
            "director_plan": {
                "runtime": "deepseek_harness",
                "model": "ignored-placeholder",
                "reasoning_effort": None,
                "skill_id": None,
                "skill_version": None,
                "fallback": "stop",
            }
        },
        "runtime_presets": {
            "deepseek_harness": {
                "model": "deepseek-v4-flash-vision-exp",
                "reasoning_effort": "low",
            }
        },
    }

    response = client.put(CONFIG_PATH, json=payload)

    assert response.status_code == 200

    saved = load_global_routes()
    assert saved.runtime_presets["deepseek_harness"].model == "deepseek-v4-flash-vision-exp"

    fetched = client.get(CONFIG_PATH)
    assert fetched.status_code == 200
    data = fetched.json()["data"]
    assert data["runtime_presets"]["deepseek_harness"]["reasoning_effort"] == "low"
    harness_role = next(r for r in data["roles"] if r["id"] == "director_plan")
    assert harness_role["route"]["model"] == "deepseek-v4-flash-vision-exp"


def test_task_runtime_config_rejects_unknown_runtime_preset_key(client) -> None:
    saved_first = client.put(
        CONFIG_PATH,
        json={
            "routes": {},
            "runtime_presets": {
                "codex": {"model": "gpt-5.6-sol", "reasoning_effort": "low"}
            },
        },
    )
    assert saved_first.status_code == 200

    rejected = client.put(
        CONFIG_PATH,
        json={
            "routes": {},
            "runtime_presets": {
                "bogus_runtime": {"model": "gpt-5.6-sol", "reasoning_effort": "low"}
            },
        },
    )

    # 未知 runtime 名必须由 API 层拒绝（422），而不是落到 Pydantic 内部成为 500。
    assert rejected.status_code == 422

    saved = load_global_routes()
    assert "bogus_runtime" not in saved.runtime_presets
    # 422 请求不落盘：先前保存的 codex preset 原样保留。
    assert saved.runtime_presets["codex"].model == "gpt-5.6-sol"


def test_task_runtime_config_returns_builtin_preset_before_any_save(client) -> None:
    fetched = client.get(CONFIG_PATH)

    assert fetched.status_code == 200
    assert fetched.json()["data"]["runtime_presets"] == {
        "deepseek_harness": {
            "model": "deepseek-v4-flash-vision-exp",
            "reasoning_effort": "low",
        }
    }


def test_task_runtime_config_replaces_runtime_presets_wholesale(client) -> None:
    first = client.put(
        CONFIG_PATH,
        json={
            "routes": {},
            "runtime_presets": {
                "codex": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
                "deepseek_harness": {
                    "model": "deepseek-v4-flash",
                    "reasoning_effort": "medium",
                },
            },
        },
    )
    assert first.status_code == 200

    second = client.put(CONFIG_PATH, json={"routes": {}})
    assert second.status_code == 200

    presets = client.get(CONFIG_PATH).json()["data"]["runtime_presets"]
    # A runtime absent from the request loses its saved preset...
    assert "codex" not in presets
    # ...and deepseek_harness falls back to the built-in default.
    assert presets["deepseek_harness"] == {
        "model": "deepseek-v4-flash-vision-exp",
        "reasoning_effort": "low",
    }
    assert load_global_routes().runtime_presets == {}
