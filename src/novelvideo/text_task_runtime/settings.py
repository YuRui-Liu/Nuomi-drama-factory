"""Persistence and precedence rules for background text-task routes."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from novelvideo.text_task_runtime.models import (
    AgentTaskRoute,
    AgentTaskRouteOverride,
    AgentTaskRouteSnapshot,
    AgentTaskRoutingConfig,
    RuntimePreset,
)

TEXT_TASK_ROUTING_KEY = "text_task_routing_v1"

_ROLE_DEFAULTS = {
    "identity_sheet_qc": AgentTaskRoute(
        runtime="codex",
        model="gpt-5.6-sol",
        reasoning_effort="low",
        fallback="stop",
    ),
    "episode_asset_planning": AgentTaskRoute(
        runtime="codex",
        model="gpt-5.6-sol",
        reasoning_effort="low",
        fallback="stop",
    ),
}


_RUNTIME_PRESET_DEFAULTS: dict[str, RuntimePreset] = {
    "deepseek_harness": RuntimePreset(
        model="deepseek-v4-flash-vision-exp",
        reasoning_effort="low",
    ),
}


def runtime_preset_for(
    config: AgentTaskRoutingConfig, runtime: str
) -> RuntimePreset | None:
    """用户保存的 preset 优先，回落内置默认，两者皆无返回 None。"""

    saved = config.runtime_presets.get(runtime)
    if saved is not None:
        return saved
    return _RUNTIME_PRESET_DEFAULTS.get(runtime)


def _clamp_harness_route(
    snapshot: AgentTaskRouteSnapshot,
    config: AgentTaskRoutingConfig,
) -> AgentTaskRouteSnapshot:
    """deepseek_harness 的模型与推理强度由运行时级 preset 唯一决定。"""

    if snapshot.runtime != "deepseek_harness":
        return snapshot
    preset = runtime_preset_for(config, "deepseek_harness")
    if preset is None:
        return snapshot
    return snapshot.model_copy(
        update={
            "model": preset.model,
            "reasoning_effort": preset.reasoning_effort,
        }
    )


def default_agent_task_route(task_role: str) -> AgentTaskRoute:
    """Return the built-in baseline for a logical text-task role."""

    return _ROLE_DEFAULTS.get(task_role, AgentTaskRoute())


def _parse_routing_config(value: Any) -> AgentTaskRoutingConfig:
    if value in (None, ""):
        return AgentTaskRoutingConfig()
    if isinstance(value, str):
        value = json.loads(value)
    return AgentTaskRoutingConfig.model_validate(value)


def load_global_routes() -> AgentTaskRoutingConfig:
    from novelvideo.model_gateway_settings import get_model_gateway_settings

    settings = get_model_gateway_settings()
    return _parse_routing_config(settings.get(TEXT_TASK_ROUTING_KEY))


def save_global_routes(config: AgentTaskRoutingConfig) -> AgentTaskRoutingConfig:
    """Persist the validated global task-role routing table."""

    from novelvideo.model_gateway_settings import save_model_gateway_setting

    parsed = AgentTaskRoutingConfig.model_validate(config)
    save_model_gateway_setting(TEXT_TASK_ROUTING_KEY, parsed.model_dump_json())
    return parsed


def load_project_routes(ctx: Any) -> AgentTaskRoutingConfig:
    from novelvideo.project_config import load_project_config_file_from_state_dir

    settings = load_project_config_file_from_state_dir(ctx.state_dir)
    return _parse_routing_config(settings.get(TEXT_TASK_ROUTING_KEY))


def _apply_override(
    route: AgentTaskRoute, override: AgentTaskRouteOverride
) -> AgentTaskRoute:
    values = override.model_dump(exclude_none=True)
    return route.model_copy(update=values)


def resolve_agent_task_route(
    *,
    task_role: str,
    global_route: AgentTaskRoute,
    project_override: AgentTaskRouteOverride | None = None,
    task_override: AgentTaskRouteOverride | None = None,
) -> AgentTaskRouteSnapshot:
    """Resolve task > project > global precedence into a secret-free snapshot."""

    clean_role = str(task_role or "").strip()
    if not clean_role:
        raise ValueError("task_role is required")
    route = global_route
    source = "global"
    if project_override is not None and project_override.model_dump(exclude_none=True):
        updated_route = _apply_override(route, project_override)
        if updated_route != route:
            source = "project"
        route = updated_route
    if task_override is not None and task_override.model_dump(exclude_none=True):
        updated_route = _apply_override(route, task_override)
        if updated_route != route:
            source = "task"
        route = updated_route
    if route.skill_id or route.skill_version:
        raise ValueError(
            "Task routes do not support skill_id/skill_version with current runtimes"
        )
    try:
        return AgentTaskRouteSnapshot(
            task_role=clean_role,
            source=source,
            **route.model_dump(),
        )
    except ValidationError:
        raise


def resolve_configured_agent_task_route(
    *,
    ctx: Any,
    task_role: str,
    task_override: AgentTaskRouteOverride | dict[str, Any] | None = None,
) -> AgentTaskRouteSnapshot:
    """Resolve persisted routes once, at enqueue time."""

    global_config = load_global_routes()
    global_override = global_config.routes.get(task_role)
    global_route = default_agent_task_route(task_role)
    if global_override is not None:
        global_route = _apply_override(global_route, global_override)
    project_override = load_project_routes(ctx).routes.get(task_role)
    parsed_task_override = (
        AgentTaskRouteOverride.model_validate(task_override)
        if task_override is not None
        else None
    )
    snapshot = resolve_agent_task_route(
        task_role=task_role,
        global_route=global_route,
        project_override=project_override,
        task_override=parsed_task_override,
    )
    return _clamp_harness_route(snapshot, global_config)
