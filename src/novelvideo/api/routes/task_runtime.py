"""CE management API for global task-concurrency settings."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from novelvideo.api.routes.media_capabilities import require_media_capability_admin
from novelvideo.ports import get_task_backend
from novelvideo.shared.runtime_env import is_ce_effective
from novelvideo.task_backend.limits import (
    project_lane_effective_active_limit,
    project_user_lane_active_limit,
)
from novelvideo.task_concurrency_settings import (
    TASK_CONCURRENCY_LANES,
    TaskConcurrencySettingsError,
    load_task_concurrency_settings,
    save_task_concurrency_settings,
)

_ENV_LIMIT_PREFIXES = (
    "ST_PROJECT_MAX_ACTIVE",
    "ST_PROJECT_MIN_ACTIVE",
    "ST_PROJECT_USER_MAX_ACTIVE",
    "ST_CE_GLOBAL_MAX_ACTIVE",
)
_TASK_CONCURRENCY_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["default", "video", "world", "ffmpeg"],
                "properties": {
                    lane: {"type": "integer", "minimum": 1, "maximum": 32}
                    for lane in ("default", "video", "world", "ffmpeg")
                },
            }
        }
    },
}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskConcurrencyBody(_Body):
    default: int = Field(ge=1, le=32, strict=True)
    video: int = Field(ge=1, le=32, strict=True)
    world: int = Field(ge=1, le=32, strict=True)
    ffmpeg: int = Field(ge=1, le=32, strict=True)


router = APIRouter(
    prefix="/task-runtime",
    dependencies=[Depends(require_media_capability_admin)],
)


def _error(status_code: int, error_code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "errorCode": error_code, "message": message},
    )


def _ce_only_error() -> JSONResponse:
    return _error(
        403,
        "TASK_CONCURRENCY_CE_ONLY",
        "Task concurrency settings are only available in CE.",
    )


def _lane_managed_by_environment(lane: str) -> bool:
    suffix = f"_{lane.upper()}_TASKS"
    return any(
        os.environ.get(f"{prefix}{suffix}", "").strip()
        for prefix in _ENV_LIMIT_PREFIXES
    )


def _runtime_data() -> dict[str, Any]:
    configured = load_task_concurrency_settings().lanes
    backend_status = get_task_backend().lane_runtime_status()
    lanes: dict[str, dict[str, Any]] = {}
    restart_required = False

    for lane in TASK_CONCURRENCY_LANES:
        state = backend_status[lane]
        running_limits = {
            "project": project_lane_effective_active_limit(lane, eligible_user_count=1),
            "user": project_user_lane_active_limit(lane),
            "executor": int(state["executor_limit"]),
        }
        managed_by_environment = _lane_managed_by_environment(lane)
        if not managed_by_environment and any(
            configured[lane] != value for value in running_limits.values()
        ):
            restart_required = True
        lanes[lane] = {
            "configured": configured[lane],
            "running_limits": running_limits,
            "active": int(state["active"]),
            "managed_by_environment": managed_by_environment,
        }

    return {"restart_required": restart_required, "lanes": lanes}


@router.get("/concurrency")
def get_concurrency() -> Any:
    if not is_ce_effective():
        return _ce_only_error()
    return {"ok": True, "data": _runtime_data()}


@router.put(
    "/concurrency",
    openapi_extra={"requestBody": _TASK_CONCURRENCY_REQUEST_BODY},
)
async def put_concurrency(request: Request) -> Any:
    if not is_ce_effective():
        return _ce_only_error()
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(
            422,
            "TASK_CONCURRENCY_INVALID",
            "All four lane values must be integers from 1 to 32.",
        )
    try:
        parsed = TaskConcurrencyBody.model_validate(body)
    except ValidationError:
        return _error(
            422,
            "TASK_CONCURRENCY_INVALID",
            "All four lane values must be integers from 1 to 32.",
        )
    try:
        save_task_concurrency_settings(parsed.model_dump())
    except TaskConcurrencySettingsError as exc:
        return _error(422, exc.code, str(exc))
    except (OSError, sqlite3.Error):
        return _error(
            503,
            "TASK_CONCURRENCY_SAVE_FAILED",
            "Task concurrency settings could not be saved.",
        )
    return {"ok": True, "data": _runtime_data()}
