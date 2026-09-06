from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api.auth import get_api_user

ADMIN_USER = {
    "id": "local",
    "username": "local",
    "role": "owner",
    "credential_kind": "user_session",
}
DEFAULT_LIMITS = {"default": 3, "video": 5, "world": 1, "ffmpeg": 1}


class FakeBackend:
    def __init__(
        self,
        *,
        executor_limits: dict[str, int] | None = None,
        active: dict[str, int] | None = None,
    ) -> None:
        self.executor_limits = executor_limits or DEFAULT_LIMITS
        self.active = active or {}

    def lane_runtime_status(self) -> dict[str, dict[str, int]]:
        return {
            lane: {
                "active": self.active.get(lane, 0),
                "queued": 0,
                "executor_limit": limit,
                "queue_limit": 64,
            }
            for lane, limit in self.executor_limits.items()
        }


@pytest.fixture
def client(monkeypatch, tmp_path):
    from novelvideo.api.routes import task_runtime
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    for lane in ("DEFAULT", "VIDEO", "WORLD", "FFMPEG"):
        for prefix in (
            "ST_PROJECT_MAX_ACTIVE",
            "ST_PROJECT_MIN_ACTIVE",
            "ST_PROJECT_USER_MAX_ACTIVE",
            "ST_CE_GLOBAL_MAX_ACTIVE",
        ):
            monkeypatch.delenv(f"{prefix}_{lane}_TASKS", raising=False)
    backend = FakeBackend()
    monkeypatch.setattr(task_runtime, "get_task_backend", lambda: backend)
    reset_process_task_concurrency_for_tests()
    app = FastAPI()
    app.include_router(task_runtime.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: ADMIN_USER
    with TestClient(app) as test_client:
        yield test_client
    reset_process_task_concurrency_for_tests()


def _values(default: object = 3) -> dict[str, object]:
    return {"default": default, "video": 5, "world": 1, "ffmpeg": 1}


def test_put_openapi_documents_top_level_lane_body(client) -> None:
    operation = client.app.openapi()["paths"]["/api/v1/task-runtime/concurrency"]["put"]
    request_body = operation["requestBody"]
    schema = request_body["content"]["application/json"]["schema"]

    assert request_body["required"] is True
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["default", "video", "world", "ffmpeg"]
    assert schema["properties"] == {
        lane: {"type": "integer", "minimum": 1, "maximum": 32}
        for lane in ("default", "video", "world", "ffmpeg")
    }


def test_get_and_put_concurrency_contract_keeps_running_snapshot(client) -> None:
    initial = client.get("/api/v1/task-runtime/concurrency")

    assert initial.status_code == 200
    assert initial.json() == {
        "ok": True,
        "data": {
            "restart_required": False,
            "lanes": {
                lane: {
                    "configured": limit,
                    "running_limits": {
                        "project": limit,
                        "user": limit,
                        "executor": limit,
                    },
                    "active": 0,
                    "managed_by_environment": False,
                }
                for lane, limit in DEFAULT_LIMITS.items()
            },
        },
    }

    response = client.put(
        "/api/v1/task-runtime/concurrency",
        json={"default": 8, "video": 6, "world": 2, "ffmpeg": 2},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["restart_required"] is True
    assert data["lanes"]["default"]["configured"] == 8
    assert data["lanes"]["default"]["running_limits"] == {
        "project": 3,
        "user": 3,
        "executor": 3,
    }


@pytest.mark.parametrize(
    "body",
    [
        _values(0),
        _values(33),
        _values(1.5),
        _values(True),
        _values("3"),
        {"default": 3, "video": 5, "world": 1},
        {**_values(), "extra": 1},
        {"wrong": _values()},
        None,
        [],
    ],
)
def test_put_rejects_invalid_body_with_stable_envelope(client, body) -> None:
    response = client.put("/api/v1/task-runtime/concurrency", json=body)

    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert response.json()["errorCode"] == "TASK_CONCURRENCY_INVALID"
    assert response.json()["message"]


def test_put_rejects_malformed_json_with_stable_envelope(client) -> None:
    response = client.put(
        "/api/v1/task-runtime/concurrency",
        content=b"{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert response.json()["errorCode"] == "TASK_CONCURRENCY_INVALID"
    assert response.json()["message"]


def test_environment_override_reports_actual_layers_and_skips_restart_flag(
    client, monkeypatch
) -> None:
    from novelvideo.api.routes import task_runtime

    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "4")
    monkeypatch.setenv("ST_PROJECT_MIN_ACTIVE_WORLD_TASKS", "3")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "2")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "6")
    monkeypatch.setattr(
        task_runtime,
        "get_task_backend",
        lambda: FakeBackend(
            executor_limits={**DEFAULT_LIMITS, "world": 6},
            active={"world": 2},
        ),
    )

    response = client.get("/api/v1/task-runtime/concurrency")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["restart_required"] is False
    assert data["lanes"]["world"] == {
        "configured": 1,
        "running_limits": {"project": 3, "user": 2, "executor": 6},
        "active": 2,
        "managed_by_environment": True,
    }


def test_restart_is_required_when_only_executor_layer_differs(
    client, monkeypatch
) -> None:
    from novelvideo.api.routes import task_runtime

    monkeypatch.setattr(
        task_runtime,
        "get_task_backend",
        lambda: FakeBackend(executor_limits={**DEFAULT_LIMITS, "default": 4}),
    )

    response = client.get("/api/v1/task-runtime/concurrency")

    assert response.status_code == 200
    assert response.json()["data"]["restart_required"] is True


def test_blank_environment_value_does_not_manage_lane(client, monkeypatch) -> None:
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_DEFAULT_TASKS", "   ")

    response = client.get("/api/v1/task-runtime/concurrency")

    assert response.status_code == 200
    lane = response.json()["data"]["lanes"]["default"]
    assert lane["managed_by_environment"] is False
    assert lane["running_limits"] == {"project": 3, "user": 3, "executor": 3}


@pytest.mark.parametrize("method", ["get", "put"])
def test_concurrency_api_is_ce_only(client, monkeypatch, method) -> None:
    monkeypatch.setenv("ST_EDITION", "ee")
    request = getattr(client, method)
    kwargs = {"json": _values()} if method == "put" else {}

    response = request("/api/v1/task-runtime/concurrency", **kwargs)

    assert response.status_code == 403
    assert response.json() == {
        "ok": False,
        "errorCode": "TASK_CONCURRENCY_CE_ONLY",
        "message": "Task concurrency settings are only available in CE.",
    }


@pytest.mark.parametrize(
    ("user", "detail"),
    [
        (
            {"id": "viewer", "role": "viewer", "credential_kind": "user_session"},
            "system administrator role required",
        ),
        (
            {"id": "agent", "role": "owner", "credential_kind": "agent_session"},
            "system media capability management requires a user session",
        ),
    ],
)
def test_concurrency_api_reuses_media_admin_permission(client, user, detail) -> None:
    client.app.dependency_overrides[get_api_user] = lambda: user

    response = client.get("/api/v1/task-runtime/concurrency")

    assert response.status_code == 403
    assert response.json() == {"detail": detail}


def test_put_reports_sqlite_failure_with_stable_envelope(client, monkeypatch) -> None:
    from novelvideo.api.routes import task_runtime

    def fail_save(_values):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(task_runtime, "save_task_concurrency_settings", fail_save)

    response = client.put("/api/v1/task-runtime/concurrency", json=_values())

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "errorCode": "TASK_CONCURRENCY_SAVE_FAILED",
        "message": "Task concurrency settings could not be saved.",
    }
