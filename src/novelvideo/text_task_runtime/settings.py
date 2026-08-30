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
)

TEXT_TASK_ROUTING_KEY = "text_task_routing_v1"


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
    if project_override is not None:
        route = _apply_override(route, project_override)
        source = "project"
    if task_override is not None:
        route = _apply_override(route, task_override)
        source = "task"
    try:
        return AgentTaskRouteSnapshot(
            task_role=clean_role,
            source=source,
            **route.model_dump(),
        )
    except ValidationError:
        raise

