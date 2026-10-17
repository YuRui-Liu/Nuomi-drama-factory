"""Durable coordinator for adopted director-plan narrative-group production."""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
import re
import threading
import time
from typing import Any

from novelvideo.chat.agent_tools import AgentToolSession
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.production_batch import BatchFailure, run_batch
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import (
    await_envelope_with_cancel_watch,
    raise_if_envelope_cancel_requested,
    remaining_timeout_seconds,
)
from novelvideo.task_state import get_task_manager

TASK_TYPE = "director_workspace_production"
_MAX_GROUPS = 50
_MAX_SUBMISSIONS = 100
_HTTP_RESULT_TIMEOUT_SECONDS = 65
_POLL_INTERVAL_SECONDS = 2.0
_ACTIVE_GROUP_STATUSES = {"queued", "pending", "running"}
_TERMINAL_TASK_STATUSES = {
    "completed", "failed", "cancelled", "canceled", "partial_failure"
}


def _required_text(value: object, label: str, *, maximum: int = 128) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is invalid")
    return result


def _safe_group_id(value: object) -> str:
    group_id = _required_text(value, "group id")
    if (
        group_id in {".", ".."}
        or any(char in group_id for char in ("/", "\\", "%", "?", "#"))
        or any(ord(char) < 32 for char in group_id)
    ):
        raise ValueError("group id is not a safe project API path segment")
    return group_id


def _episode_aspect_ratio(value: object) -> str:
    if not isinstance(value, str):
        raise BatchFailure("aspect_ratio_required: project orientation is missing")
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", value)
    if match is None:
        raise BatchFailure("aspect_ratio_required: project orientation is missing or unsupported")
    width, height = int(match[1]), int(match[2])
    if min(width, height) <= 0 or width == height:
        raise BatchFailure("aspect_ratio_required: project orientation is missing or unsupported")
    return "9:16" if width < height else "16:9"


def _decode_tool_response(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise BatchFailure("invalid_response: project API returned non-JSON data") from None
    if not isinstance(value, dict):
        raise BatchFailure("invalid_response: project API returned an invalid response")
    return value


def _scope_path(
    method: str,
    path: str,
    *,
    episode: int,
    selected_group_ids: set[str],
) -> None:
    """Constrain AgentToolSession requests to this pinned production operation."""
    if method == "GET" and path in {
        "",
        "freezone/orchestration/capabilities",
        "tasks",
        f"episodes/{episode}/director-plans",
        f"episodes/{episode}/narrative-groups",
    }:
        return
    group_prefix = re.escape(f"episodes/{episode}/narrative-groups/")
    group_path = re.fullmatch(group_prefix + r"([^/]+)/(.+)", path)
    if group_path is None or group_path[1] not in selected_group_ids:
        raise BatchFailure("production_scope_violation: request is outside the selected groups")
    suffix = group_path[2]
    if method == "GET" and suffix == "render/references":
        return
    if method == "POST" and suffix in {
        "render/generate",
        "render/regenerate",
        "render/split",
        "video/generate",
    }:
        return
    raise BatchFailure("production_scope_violation: operation is unavailable in director production")


def _active_revision(store: DirectorPlanStore, episode: int, revision_id: str):
    revision = store.load_active(episode)
    if revision is None or revision.status != "active" or revision.revision_id != revision_id:
        raise BatchFailure(
            "director_plan_changed: the selected director revision is no longer adopted"
        )
    return revision


def run_director_workspace_production(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    """Run selected narrative groups through the existing group batch algorithm."""
    payload = envelope.get("payload") or {}
    try:
        episode = int(payload.get("episode"))
        revision_id = _required_text(payload.get("revision_id"), "revision id")
        raw_group_ids = payload.get("group_ids")
        if not isinstance(raw_group_ids, list) or not 1 <= len(raw_group_ids) <= _MAX_GROUPS:
            raise ValueError("group selection must contain 1 to 50 narrative groups")
        group_ids = [_safe_group_id(value) for value in raw_group_ids]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("group selection must not contain duplicates")
        retry_failed = payload.get("retry_failed", False)
        if not isinstance(retry_failed, bool):
            raise ValueError("retry_failed must be boolean")
        through = payload.get('through', 'video')
        if through not in {'render', 'video'}:
            raise ValueError('director production stops at render or video, never compose')
        if episode <= 0 or episode != int(envelope.get("episode") or 0):
            raise ValueError("episode scope is invalid")
        if envelope.get("task_type") not in {None, TASK_TYPE}:
            raise ValueError("task type is invalid")
        scope = _required_text(envelope.get("scope"), "task scope", maximum=256)
        username = _required_text(ctx.requester_username, "requester username", maximum=256)
        project_id = _required_text(ctx.project_id, "project id")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid director workspace production request: {exc}") from exc

    plan_store = DirectorPlanStore(ctx.output_dir)
    revision = _active_revision(plan_store, episode, revision_id)
    available_group_ids = [_safe_group_id(group.id) for group in revision.groups]
    if len(available_group_ids) != len(set(available_group_ids)):
        raise BatchFailure("director_plan_invalid: adopted plan has duplicate narrative groups")
    if not set(group_ids).issubset(available_group_ids):
        raise BatchFailure("group_changed: selected group is no longer in the adopted director plan")
    get_task_manager().update_progress_for_project(
        ctx, TASK_TYPE, episode, scope=scope, progress=0.0,
        current_task="读取已保存制作设置",
        metadata={'revision_id': revision_id, 'group_ids': list(group_ids), 'through': through},
        expected_task_id=str(envelope.get("__run_task_id") or "") or None,
    )

    async def run() -> dict[str, Any]:
        async with AgentToolSession(
            username=username,
            scope_kind="project",
            project_id=project_id,
        ) as session:
            loop = asyncio.get_running_loop()
            stopped = threading.Event()
            selected = set(group_ids)

            def checkpoint() -> None:
                if stopped.is_set():
                    from novelvideo.task_backend.cancel import TaskCancelled

                    raise TaskCancelled()
                raise_if_envelope_cancel_requested(
                    envelope,
                    task_type=TASK_TYPE,
                    episode=episode,
                    scope=scope,
                )
                _active_revision(plan_store, episode, revision_id)

            def request(method: str, path: str, body: dict[str, Any] | None = None):
                method = str(method).upper()
                path = str(path).strip("/")
                if through == 'render' and method == 'POST' and path.endswith('/video/generate'):
                    raise BatchFailure('production_scope_violation: render-only authorization cannot generate video')
                _scope_path(
                    method,
                    path,
                    episode=episode,
                    selected_group_ids=selected,
                )
                checkpoint()
                tool_name = "dramaclaw_get" if method == "GET" else "dramaclaw_post"
                arguments: dict[str, Any] = {
                    "path": f"/projects/{project_id}" + (f"/{path}" if path else ""),
                }
                if body is not None:
                    arguments["body"] = body
                call = asyncio.run_coroutine_threadsafe(
                    session.call(tool_name, arguments), loop
                )
                try:
                    response = _decode_tool_response(
                        call.result(timeout=_HTTP_RESULT_TIMEOUT_SECONDS)
                    )
                except FutureTimeout:
                    call.cancel()
                    if method != "GET":
                        raise BatchFailure(
                            "submission_unknown: project API response timed out; inspect project tasks before retrying"
                        ) from None
                    raise BatchFailure("api_error: project API read timed out") from None
                checkpoint()
                if response.get("ok") is not True:
                    status_code = response.get("status_code")
                    if method != "GET":
                        raise BatchFailure(
                            "api_error: production submission was rejected"
                            + (f" (HTTP {status_code})" if status_code else "")
                        )
                    raise BatchFailure(
                        "api_error: project read was rejected"
                        + (f" (HTTP {status_code})" if status_code else "")
                    )
                return response

            def wait_task(task_id: str) -> dict[str, Any]:
                timeout = remaining_timeout_seconds(envelope, default_seconds=3600)
                deadline = time.monotonic() + (timeout or 3600)
                while True:
                    checkpoint()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BatchFailure(
                            "wait_timeout: child task is still running; no duplicate was submitted"
                        )
                    response = request("GET", "tasks")
                    tasks = response.get("data")
                    if not isinstance(tasks, list) or any(not isinstance(item, dict) for item in tasks):
                        raise BatchFailure("invalid_response: expected a list of project tasks")
                    task = next(
                        (item for item in tasks if str(item.get("task_id") or "") == task_id),
                        None,
                    )
                    if task is not None and task.get("status") in _TERMINAL_TASK_STATUSES:
                        result = task.get("result") or {}
                        if task.get("status") != "completed" or (
                            isinstance(result, dict) and result.get("qc_passed") is False
                        ):
                            raise BatchFailure(
                                "child_task_failed: "
                                + task_id
                                + " status="
                                + str(task.get("status"))
                            )
                        return task
                    time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

            def emit(event: dict[str, Any]) -> None:
                if event.get("event") not in {"stage", "episode"}:
                    return
                checkpoint()
                manager = get_task_manager()
                manager.update_progress_for_project(
                    ctx,
                    TASK_TYPE,
                    episode,
                    scope=scope,
                    progress=(0.95 if event.get("event") == "episode" else 0.5),
                    current_task=(
                        ("叙事组分镜图已完成" if through == 'render' else "叙事组视频生产已完成")
                        if event.get("event") == "episode"
                        else f"叙事组 {str(event.get('group_id') or '')[:128]} · "
                        f"{str(event.get('stage') or '')[:16]} · {str(event.get('status') or '')[:32]}"
                    ),
                    logs=[
                        "director_workspace_production "
                        + json.dumps(event, ensure_ascii=False, sort_keys=True)
                    ],
                    metadata={'revision_id': revision_id, 'group_ids': list(group_ids), 'through': through},
                    expected_task_id=str(envelope.get("__run_task_id") or "") or None,
                )

            def execute_batch() -> dict[str, Any]:
                capability = request("GET", "freezone/orchestration/capabilities").get("data")
                runtime = capability.get("runtime") if isinstance(capability, dict) else None
                lanes = runtime.get("lanes") if isinstance(runtime, dict) else None
                try:
                    default_limit = lanes["default"]
                    video_limit = lanes["video"]
                except (KeyError, TypeError):
                    raise BatchFailure(
                        "concurrency_settings_required: read saved default and video concurrency before production"
                    ) from None
                if any(type(value) is not int or value < 1 for value in (default_limit, video_limit)):
                    raise BatchFailure(
                        "concurrency_settings_required: saved task concurrency is invalid"
                    )
                project = request("GET", "").get("data")
                project_data = project if isinstance(project, dict) else {}
                aspect_ratio = _episode_aspect_ratio(project_data.get("aspect_ratio"))
                summary = run_batch(
                    request=request,
                    wait_task=wait_task,
                    episodes=[episode],
                    through=through,
                    with_sketch=False,
                    max_submissions=_MAX_SUBMISSIONS,
                    aspect_ratio=aspect_ratio,
                    retry_failed=retry_failed,
                    emit=emit,
                    concurrency={
                        "sketch": default_limit,
                        "render": default_limit,
                        "video": video_limit,
                    },
                    group_ids_by_episode={episode: group_ids},
                )
                return {
                    "status": "completed" if summary.get("ok") is True else "partial_failure",
                    "through": through,
                    "revision_id": revision_id,
                    "summary": summary,
                }

            worker = asyncio.create_task(asyncio.to_thread(execute_batch))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                stopped.set()
                try:
                    await asyncio.shield(worker)
                except (BatchFailure, Exception):
                    pass
                raise

    return asyncio.run(await_envelope_with_cancel_watch(run(), envelope, task_type=TASK_TYPE))


__all__ = ["TASK_TYPE", "run_director_workspace_production"]
