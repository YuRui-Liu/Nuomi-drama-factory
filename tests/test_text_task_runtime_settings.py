from __future__ import annotations

import json
from types import SimpleNamespace

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
    resolve_configured_agent_task_route,
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


def test_model_name_with_internal_spaces_is_accepted():
    route = AgentTaskRoute(runtime="workbuddy", model="Hy4 preview")
    assert route.model == "Hy4 preview"


def test_model_name_is_trimmed_on_assignment():
    route = AgentTaskRoute(runtime="workbuddy", model="  Hy4 preview  ")
    assert route.model == "Hy4 preview"


@pytest.mark.parametrize("model", ["", "   "])
def test_blank_model_names_are_rejected(model):
    with pytest.raises(ValidationError, match="model"):
        AgentTaskRoute(runtime="workbuddy", model=model)


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


def test_episode_asset_planning_defaults_to_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "novelvideo.text_task_runtime.settings.load_global_routes",
        lambda: AgentTaskRoutingConfig(),
    )
    monkeypatch.setattr(
        "novelvideo.text_task_runtime.settings.load_project_routes",
        lambda ctx: AgentTaskRoutingConfig(),
    )

    snapshot = resolve_configured_agent_task_route(
        ctx=type("Ctx", (), {"state_dir": tmp_path})(),
        task_role="episode_asset_planning",
    )

    assert snapshot.runtime == "codex"
    assert snapshot.model == "gpt-5.6-sol"
    assert snapshot.reasoning_effort == "low"
    assert snapshot.fallback == "stop"


def test_runtime_presets_round_trip_and_reject_unsafe_model():
    from novelvideo.text_task_runtime.models import (
        AgentTaskRoutingConfig,
        RuntimePreset,
    )

    config = AgentTaskRoutingConfig(
        runtime_presets={
            "deepseek_harness": RuntimePreset(
                model="deepseek-v4-flash-vision-exp",
                reasoning_effort="low",
            )
        }
    )
    reloaded = AgentTaskRoutingConfig.model_validate_json(config.model_dump_json())
    assert reloaded.runtime_presets["deepseek_harness"].model == "deepseek-v4-flash-vision-exp"
    assert reloaded.runtime_presets["deepseek_harness"].reasoning_effort == "low"

    with pytest.raises(ValidationError):
        RuntimePreset(model="  ")


def test_runtime_preset_is_frozen_and_forbids_extra_fields():
    from novelvideo.text_task_runtime.models import RuntimePreset

    preset = RuntimePreset(
        model="deepseek-v4-flash-vision-exp", reasoning_effort="low"
    )

    # frozen：preset 是配置快照，解析过程中不允许被就地改写。
    with pytest.raises(ValidationError):
        preset.model = "other-model"

    # extra="forbid"：多写的字段是隐藏的配置错误，不能被静默忽略。
    with pytest.raises(ValidationError):
        RuntimePreset(model="other-model", unexpected="x")


def test_routing_config_without_runtime_presets_still_loads():
    from novelvideo.text_task_runtime.models import AgentTaskRoutingConfig

    legacy = '{"routes": {"director_plan": {"runtime": "codex", "model": "gpt-5.6-sol"}}}'
    config = AgentTaskRoutingConfig.model_validate_json(legacy)
    assert config.runtime_presets == {}
    assert config.routes["director_plan"].model == "gpt-5.6-sol"


def test_harness_route_is_clamped_to_runtime_preset(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import (
        AgentTaskRouteOverride,
        AgentTaskRoutingConfig,
        RuntimePreset,
    )
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="deepseek_harness",
                    model="whatever-the-role-asked-for",
                    reasoning_effort="high",
                )
            },
            runtime_presets={
                # 故意取一个与内置默认不同的值：只有「用户保存的 preset 优先」的
                # 实现才会得到它，丢弃用户值或读错配置源都会改变结果。
                "deepseek_harness": RuntimePreset(
                    model="harness-user-choice",
                    reasoning_effort="high",
                )
            },
        ),
    )
    # preset 只存在于 global 配置；project 配置为空。若 clamp 误读 project
    # 配置，就会拿不到 preset（或拿到错误的值）而暴露出来。
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path),
        task_role="director_plan",
        task_override=AgentTaskRouteOverride(model="task-level-attempt", reasoning_effort="xhigh"),
    )
    assert snapshot.runtime == "deepseek_harness"
    assert snapshot.model == "harness-user-choice"
    assert snapshot.reasoning_effort == "high"
    # task_role/source 都是无默认值的必填字段，任何重建快照的实现都必须显式
    # 传入；这两条断言只能挡住极粗心的重建，保留它们只是最低保险。
    assert snapshot.task_role == "director_plan"
    assert snapshot.source == "task"


def test_task_override_switching_into_harness_is_clamped(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import (
        AgentTaskRouteOverride,
        AgentTaskRoutingConfig,
        RuntimePreset,
    )
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="codex", model="gpt-5.6-sol", reasoning_effort="low"
                )
            },
            runtime_presets={
                "deepseek_harness": RuntimePreset(
                    model="harness-user-choice",
                    reasoning_effort="high",
                )
            },
        ),
    )
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    # global 里该角色是 codex，是 task 级 override 把运行时切成 harness。
    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path),
        task_role="director_plan",
        task_override=AgentTaskRouteOverride(runtime="deepseek_harness"),
    )

    assert snapshot.runtime == "deepseek_harness"
    assert snapshot.model == "harness-user-choice"
    assert snapshot.reasoning_effort == "high"
    assert snapshot.source == "task"


def test_project_override_switching_into_harness_is_clamped(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import (
        AgentTaskRouteOverride,
        AgentTaskRoutingConfig,
        RuntimePreset,
    )
    from novelvideo.text_task_runtime import settings as runtime_settings

    # global 里该角色是 codex；preset 只在 global 的 runtime_presets 里。
    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="codex", model="gpt-5.6-sol", reasoning_effort="low"
                )
            },
            runtime_presets={
                # 与内置默认不同的值：只有「最终快照仍被 preset 覆盖」的实现才会得到它。
                "deepseek_harness": RuntimePreset(
                    model="harness-user-choice",
                    reasoning_effort="high",
                )
            },
        ),
    )
    # 是 project 级 override 把 runtime 切成 harness，并试图写入自定义 model/effort。
    monkeypatch.setattr(
        runtime_settings,
        "load_project_routes",
        lambda ctx: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="deepseek_harness",
                    model="project-level-attempt",
                    reasoning_effort="xhigh",
                )
            }
        ),
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path),
        task_role="director_plan",
    )

    # 项目级 override 无法逃逸 preset：clamp 施加在最终快照上。
    assert snapshot.runtime == "deepseek_harness"
    assert snapshot.model == "harness-user-choice"
    assert snapshot.reasoning_effort == "high"
    assert snapshot.source == "project"


def test_harness_defaults_when_no_preset_saved(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import AgentTaskRouteOverride, AgentTaskRoutingConfig
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={"director_plan": AgentTaskRouteOverride(runtime="deepseek_harness")}
        ),
    )
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path),
        task_role="director_plan",
    )
    assert snapshot.model == "deepseek-v4-flash-vision-exp"
    assert snapshot.reasoning_effort == "low"


def test_non_harness_route_is_not_clamped(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import AgentTaskRouteOverride, AgentTaskRoutingConfig
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="codex", model="gpt-5.6-sol", reasoning_effort="medium"
                )
            }
        ),
    )
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path), task_role="director_plan"
    )
    assert snapshot.model == "gpt-5.6-sol"
    assert snapshot.reasoning_effort == "medium"


def test_runtime_preset_for_prefers_user_saved_value():
    from novelvideo.text_task_runtime.models import (
        AgentTaskRoutingConfig,
        RuntimePreset,
    )
    from novelvideo.text_task_runtime.settings import runtime_preset_for

    config = AgentTaskRoutingConfig(
        runtime_presets={
            "deepseek_harness": RuntimePreset(
                model="harness-user-choice", reasoning_effort="high"
            )
        }
    )

    preset = runtime_preset_for(config, "deepseek_harness")

    assert preset is not None
    assert preset.model == "harness-user-choice"
    assert preset.reasoning_effort == "high"


def test_runtime_preset_for_falls_back_to_builtin_default():
    from novelvideo.text_task_runtime.models import AgentTaskRoutingConfig
    from novelvideo.text_task_runtime.settings import runtime_preset_for

    preset = runtime_preset_for(AgentTaskRoutingConfig(), "deepseek_harness")

    assert preset is not None
    assert preset.model == "deepseek-v4-flash-vision-exp"
    assert preset.reasoning_effort == "low"


def test_runtime_preset_for_returns_none_for_runtime_without_default():
    from novelvideo.text_task_runtime.models import AgentTaskRoutingConfig
    from novelvideo.text_task_runtime.settings import runtime_preset_for

    assert runtime_preset_for(AgentTaskRoutingConfig(), "workbuddy") is None
