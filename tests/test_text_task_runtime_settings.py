from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from novelvideo.text_task_runtime.models import (
    AgentTaskRoute,
    AgentTaskRouteOverride,
    AgentTaskRoutingConfig,
)
from novelvideo.text_task_runtime.settings import (
    TEXT_TASK_ROUTING_KEY,
    load_global_routes,
    load_project_routes,
    resolve_agent_task_route,
)


def test_project_override_is_frozen_and_secret_free():
    snapshot = resolve_agent_task_route(
        task_role="director_plan",
        global_route=AgentTaskRoute(runtime="model_api", model="deepseek-v4-flash"),
        project_override=AgentTaskRouteOverride(
            runtime="codex", model="gpt-5.6-sol", reasoning_effort="high"
        ),
    )

    assert snapshot.runtime == "codex"
    assert snapshot.source == "project"
    assert snapshot.task_role == "director_plan"
    assert "api_key" not in snapshot.model_dump(mode="json")


def test_route_enums_reject_unsupported_values():
    with pytest.raises(ValidationError):
        AgentTaskRoute(runtime="chat", model="gpt-5")
    with pytest.raises(ValidationError):
        AgentTaskRoute(runtime="codex", model="gpt-5", fallback="silent")
    with pytest.raises(ValidationError):
        AgentTaskRoute(runtime="codex", model="gpt-5", reasoning_effort="extreme")


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.6-sol&whoami",
        "gpt-5.6-sol|whoami",
        "gpt-5.6-sol%PATH%",
        "gpt-5.6-sol\nwhoami",
    ],
)
def test_codex_model_rejects_windows_shell_metacharacters(model):
    with pytest.raises(ValidationError, match="model"):
        AgentTaskRoute(runtime="codex", model=model)


@pytest.mark.parametrize(
    "payload",
    [
        {"fallback": "retry"},
        {"fallback": "explicit_backup"},
        {"backup_runtime": "model_api"},
        {"backup_model": "deepseek-v4-flash"},
    ],
)
def test_unimplemented_fallback_configuration_is_rejected(payload):
    with pytest.raises(ValidationError):
        AgentTaskRoute(runtime="codex", model="gpt-5.6-sol", **payload)


def test_empty_overrides_do_not_change_source():
    snapshot = resolve_agent_task_route(
        task_role="director_plan",
        global_route=AgentTaskRoute(runtime="model_api", model="deepseek-v4-flash"),
        project_override=AgentTaskRouteOverride(),
        task_override=AgentTaskRouteOverride(),
    )

    assert snapshot.source == "global"


def test_noop_overrides_do_not_claim_route_source():
    snapshot = resolve_agent_task_route(
        task_role="director_plan",
        global_route=AgentTaskRoute(runtime="model_api", model="deepseek-v4-flash"),
        project_override=AgentTaskRouteOverride(
            runtime="model_api", model="deepseek-v4-flash"
        ),
        task_override=AgentTaskRouteOverride(fallback="stop"),
    )

    assert snapshot.source == "global"


@pytest.mark.parametrize(
    ("runtime", "skill_id", "skill_version"),
    [
        ("codex", "director-plan", None),
        ("codex", None, "v1"),
        ("model_api", "director-plan", "v1"),
    ],
)
def test_unsupported_skill_fields_are_rejected(
    runtime, skill_id, skill_version
):
    with pytest.raises(ValueError, match="skill_id"):
        resolve_agent_task_route(
            task_role="director_plan",
            global_route=AgentTaskRoute(
                runtime=runtime,
                model="gpt-5.6-sol",
                skill_id=skill_id,
                skill_version=skill_version,
            ),
        )


def test_load_global_routes_uses_independent_settings_key(monkeypatch):
    stored = AgentTaskRoutingConfig(
        routes={
            "director_plan": AgentTaskRouteOverride(
                runtime="codex", model="gpt-5.6-sol"
            )
        }
    )
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_model_gateway_settings",
        lambda: {TEXT_TASK_ROUTING_KEY: stored.model_dump_json()},
    )

    assert load_global_routes() == stored


def test_load_project_routes_reads_project_config_state_dir(monkeypatch, tmp_path):
    stored = {
        "routes": {
            "director_plan": {"runtime": "model_api", "model": "deepseek-v4-flash"}
        }
    }
    monkeypatch.setattr(
        "novelvideo.project_config.load_project_config_file_from_state_dir",
        lambda state_dir: {TEXT_TASK_ROUTING_KEY: json.dumps(stored)},
    )
    ctx = type("Ctx", (), {"state_dir": tmp_path})()

    config = load_project_routes(ctx)

    assert config.routes["director_plan"].runtime == "model_api"
