from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.text_task_runtime import deepseek_harness
from novelvideo.text_task_runtime.models import (
    AgentTaskRouteSnapshot,
    AgentTaskRoutingConfig,
    RuntimePreset,
)
from novelvideo.text_task_runtime.settings import runtime_preset_for


class Answer(BaseModel):
    value: str


def runtime(model="deepseek-v4-flash-vision-exp", effort="low"):
    return deepseek_harness.DeepSeekHarnessStructuredRuntime(
        AgentTaskRouteSnapshot(
            runtime="deepseek_harness",
            model=model,
            reasoning_effort=effort,
            task_role="director_plan",
            source="global",
        )
    )


def stub_process(stdout=b"", returncode=0, stderr=b""):
    return SimpleNamespace(
        returncode=returncode, communicate=AsyncMock(return_value=(stdout, stderr))
    )


@pytest.fixture(autouse=True)
def isolated_harness_home(tmp_path, monkeypatch):
    """Every test writes into a throwaway home, never the real ~/.dsh or state dir."""

    home = tmp_path / "dsh-home"
    monkeypatch.setenv("NOVELVIDEO_DSH_HOME", str(home))
    monkeypatch.setattr(
        deepseek_harness, "load_global_routes", lambda: AgentTaskRoutingConfig()
    )
    return home


@pytest.fixture
def dsh(monkeypatch):
    monkeypatch.setattr(deepseek_harness, "dsh_command", lambda: "/app/dsh")
    return "/app/dsh"


@pytest.mark.asyncio
async def test_headless_argv_carries_task_and_dsh_home_env(
    monkeypatch, dsh, isolated_harness_home
):
    proc = stub_process(b'{"value":"ok"}')
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(deepseek_harness.asyncio, "create_subprocess_exec", spawn)

    result = await runtime().run_structured(
        prompt="task", system_prompt="system", output_type=Answer
    )

    assert result.value == "ok"
    argv = spawn.call_args.args
    assert argv[0] == "/app/dsh"
    assert argv[0] == deepseek_harness.dsh_command()
    assert argv[1:3] == ("--profile", "headless")
    assert argv[3].startswith("system\n\ntask\n")
    # headless 只接受一个 task 参数：模型/推理强度必须走 settings.yaml。
    assert "--model" not in argv and "--effort" not in argv
    env = spawn.call_args.kwargs["env"]
    assert env["DSH_HOME"] == str(isolated_harness_home)
    assert Path(spawn.call_args.kwargs["cwd"]).name.startswith("nuomi-dsh-")
    proc.communicate.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_settings_document_declares_official_provider(
    monkeypatch, dsh, isolated_harness_home
):
    proc = stub_process(b'{"value":"ok"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    await runtime().run_structured(prompt="task", output_type=Answer)

    # pyyaml 不是本仓库依赖，直接断言文件文本。
    text = (isolated_harness_home / "settings.yaml").read_text(encoding="utf-8")
    assert text == (
        "agent-default-model:\n"
        "  provider: deepseek-official\n"
        "  model: deepseek-v4-flash-vision-exp\n"
        "  reasoningEffort: low\n"
    )


@pytest.mark.asyncio
async def test_settings_document_omits_absent_reasoning_effort(
    monkeypatch, dsh, isolated_harness_home
):
    monkeypatch.setattr(
        deepseek_harness,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            runtime_presets={"deepseek_harness": RuntimePreset(model="preset-only")}
        ),
    )
    proc = stub_process(b'{"value":"ok"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    await runtime().run_structured(prompt="task", output_type=Answer)

    text = (isolated_harness_home / "settings.yaml").read_text(encoding="utf-8")
    assert text == (
        "agent-default-model:\n"
        "  provider: deepseek-official\n"
        "  model: preset-only\n"
    )
    assert "reasoningEffort" not in text


@pytest.mark.asyncio
async def test_call_time_preset_wins_over_snapshot(monkeypatch, dsh, isolated_harness_home):
    """入队快照可能已过期：settings.yaml 必须采用「调用时」的运行时级 preset。"""

    config = AgentTaskRoutingConfig(
        runtime_presets={
            "deepseek_harness": RuntimePreset(
                model="model-from-preset", reasoning_effort="xhigh"
            )
        }
    )
    monkeypatch.setattr(deepseek_harness, "load_global_routes", lambda: config)
    proc = stub_process(b'{"value":"ok"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    await runtime(model="snapshot-model", effort="minimal").run_structured(
        prompt="task", output_type=Answer
    )

    text = (isolated_harness_home / "settings.yaml").read_text(encoding="utf-8")
    assert "model: model-from-preset" in text
    assert "reasoningEffort: xhigh" in text
    assert "snapshot-model" not in text
    assert "minimal" not in text
    # 该行为正是 runtime_preset_for 对同一份全局配置的解析结果。
    assert runtime_preset_for(config, "deepseek_harness") == RuntimePreset(
        model="model-from-preset", reasoning_effort="xhigh"
    )


@pytest.mark.asyncio
async def test_broken_settings_store_falls_back_to_snapshot(
    monkeypatch, dsh, isolated_harness_home
):
    def explode():
        raise ValueError("storage is unavailable")

    monkeypatch.setattr(deepseek_harness, "load_global_routes", explode)
    proc = stub_process(b'{"value":"ok"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    await runtime(model="snapshot-model", effort="minimal").run_structured(
        prompt="task", output_type=Answer
    )

    text = (isolated_harness_home / "settings.yaml").read_text(encoding="utf-8")
    assert "model: snapshot-model" in text
    assert "reasoningEffort: minimal" in text


@pytest.mark.asyncio
async def test_schema_is_appended_only_for_structured_output(monkeypatch, dsh):
    proc = stub_process(b'{"value":"ok"}')
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(deepseek_harness.asyncio, "create_subprocess_exec", spawn)

    await runtime().run_structured(prompt="task", system_prompt="system", output_type=Answer)

    request = spawn.call_args.args[3]
    assert request.startswith("system\n\ntask\n")
    assert "Return only JSON matching this schema:" in request
    assert '"value"' in request

    # str 输出不应拼接任何 Schema。
    proc.communicate.return_value = (b"plain text", b"")
    assert await runtime().run_structured(prompt="task", output_type=str) == "plain text"
    assert "schema" not in spawn.call_args.args[3]


@pytest.mark.asyncio
async def test_images_are_never_silently_discarded(monkeypatch, isolated_harness_home):
    spawn = AsyncMock()
    monkeypatch.setattr(deepseek_harness.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer, images=[object()])
    assert error.value.code == "DSH_IMAGES_UNSUPPORTED"
    assert "图片输入" in str(error.value)
    spawn.assert_not_called()


def test_missing_cli_reports_dsh_not_installed(monkeypatch):
    monkeypatch.delenv("DSH_BIN", raising=False)
    monkeypatch.setattr(deepseek_harness.shutil, "which", lambda _name: None)
    with pytest.raises(KnowledgeRuntimeError) as error:
        deepseek_harness.dsh_command()
    assert error.value.code == "DSH_NOT_INSTALLED"


def test_dsh_bin_is_preferred_over_path(monkeypatch, tmp_path):
    explicit = tmp_path / "dsh"
    explicit.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("DSH_BIN", str(explicit))
    monkeypatch.setattr(
        deepseek_harness.shutil, "which", lambda _name: "/usr/local/bin/dsh"
    )
    assert deepseek_harness.dsh_command() == str(explicit.resolve())


@pytest.mark.asyncio
async def test_failure_does_not_expose_process_output(monkeypatch, dsh):
    proc = stub_process(b"secret", returncode=1, stderr=b"secret")
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)
    assert error.value.code == "DSH_EXEC_FAILED"
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_start_failure_is_reported_without_output(monkeypatch, dsh):
    monkeypatch.setattr(
        deepseek_harness.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=OSError("secret spawn failure")),
    )
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)
    assert error.value.code == "DSH_START_FAILED"
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stdout",
    [b"not json", b'{"other": 1}', b'{"value": ' , b"\xff\xfe secret"],
)
async def test_invalid_stdout_reports_dsh_output_invalid(monkeypatch, dsh, stdout):
    proc = stub_process(stdout)
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)
    assert error.value.code == "DSH_OUTPUT_INVALID"
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_cancellation_cleans_up_process(monkeypatch, dsh):
    proc = SimpleNamespace(
        returncode=None, communicate=AsyncMock(side_effect=asyncio.CancelledError)
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    terminate = AsyncMock()
    monkeypatch.setattr(deepseek_harness, "terminate_process_tree", terminate)
    with pytest.raises(asyncio.CancelledError):
        await runtime().run_structured(prompt="task", output_type=Answer)
    terminate.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_timeout_is_not_rewrapped(monkeypatch, dsh):
    proc = SimpleNamespace(
        returncode=None, communicate=AsyncMock(side_effect=asyncio.TimeoutError)
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    terminate = AsyncMock()
    monkeypatch.setattr(deepseek_harness, "terminate_process_tree", terminate)
    with pytest.raises(asyncio.TimeoutError):
        await runtime().run_structured(prompt="task", output_type=Answer)
    terminate.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_non_positive_timeout_is_rejected(monkeypatch, dsh):
    monkeypatch.setenv("DSH_EXEC_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError):
        await runtime().run_structured(prompt="task", output_type=Answer)


def test_harness_home_prefers_env_and_never_aliases_user_dsh(monkeypatch, tmp_path):
    custom = tmp_path / "custom-dsh"
    monkeypatch.setenv("NOVELVIDEO_DSH_HOME", str(custom))
    assert deepseek_harness.harness_home() == custom

    monkeypatch.delenv("NOVELVIDEO_DSH_HOME")
    from novelvideo import config

    assert deepseek_harness.harness_home() == Path(config.STATE_DIR) / "dsh"
    assert deepseek_harness.harness_home() != Path.home() / ".dsh"


def test_runtime_rejects_foreign_or_skill_routes():
    with pytest.raises(ValueError):
        deepseek_harness.DeepSeekHarnessStructuredRuntime(
            AgentTaskRouteSnapshot(
                runtime="codex",
                model="gpt-5.6-sol",
                task_role="identity_sheet_qc",
                source="global",
            )
        )
    with pytest.raises(ValueError):
        deepseek_harness.DeepSeekHarnessStructuredRuntime(
            AgentTaskRouteSnapshot(
                runtime="deepseek_harness",
                model="deepseek-v4-flash-vision-exp",
                skill_id="some-skill",
                task_role="director_plan",
                source="global",
            )
        )


def test_settings_document_is_replaced_atomically(monkeypatch, tmp_path):
    home = tmp_path / "atomic-home"
    target = deepseek_harness._write_settings_document(
        home, model="deepseek-v4-flash-vision-exp", effort="low"
    )
    assert target == home / "settings.yaml"
    assert target.read_text(encoding="utf-8").startswith("agent-default-model:\n")
    # 没有留下半截临时文档。
    assert [path.name for path in home.iterdir()] == ["settings.yaml"]


def test_settings_document_survives_an_awkward_model_name(tmp_path):
    home = tmp_path / "awkward-home"
    deepseek_harness._write_settings_document(home, model="vendor: model", effort="low")
    text = (home / "settings.yaml").read_text(encoding="utf-8")
    # 含冒号+空格的模型名必须被引号包裹，否则 dsh 会读到坏掉的 YAML。
    assert 'model: "vendor: model"' in text


def test_harness_home_default_ignores_ambient_dsh_home(monkeypatch, tmp_path):
    monkeypatch.delenv("NOVELVIDEO_DSH_HOME", raising=False)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "user-owned-dsh"))
    from novelvideo import config

    assert deepseek_harness.harness_home() == Path(config.STATE_DIR) / "dsh"
    assert deepseek_harness.harness_home() != Path(os.environ["DSH_HOME"])
