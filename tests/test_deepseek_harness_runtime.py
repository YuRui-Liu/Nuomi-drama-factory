from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
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
from novelvideo.text_task_runtime.runtime import build_text_task_runtime
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
    # Nuomi only asks dsh for structured JSON, never for agentic work, so every
    # tool plugin is disabled by an overlay patch (see the regression test below).
    assert argv[3:5] == ("--patch", str(isolated_harness_home / deepseek_harness.DSH_TOOL_FREE_PATCH_NAME))
    assert argv[5].startswith("system\n\ntask\n")
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

    # argv = [dsh, --profile, headless, --patch, <file>, <request>]
    request = spawn.call_args.args[5]
    assert request.startswith("system\n\ntask\n")
    assert "Return only JSON matching this schema:" in request
    assert '"value"' in request

    # str 输出不应拼接任何 Schema。
    proc.communicate.return_value = (b"plain text", b"")
    assert await runtime().run_structured(prompt="task", output_type=str) == "plain text"
    assert "schema" not in spawn.call_args.args[5]


@pytest.mark.asyncio
async def test_text_output_keeps_a_deliberate_code_fence(monkeypatch, dsh):
    """纯文本答案本身可能就是有意的 markdown 代码块，不能被剥离围栏。"""

    proc = stub_process(b"```\nplain answer\n```")
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await runtime().run_structured(prompt="task", output_type=str)

    assert result == "```\nplain answer\n```"
    assert "```" in result


@pytest.mark.asyncio
async def test_structured_output_still_unwraps_a_code_fence(monkeypatch, dsh):
    """结构化分支仍必须剥离围栏，否则 JSON 解析会失败。"""

    proc = stub_process(b'```json\n{"value": "ok"}\n```')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    result = await runtime().run_structured(prompt="task", output_type=Answer)

    assert result.value == "ok"


@pytest.mark.asyncio
async def test_images_use_configured_deepseek_vision_model_without_dsh_text_call(monkeypatch, isolated_harness_home):
    from novelvideo.knowledge_runtime.codex import StructuredImage
    from pydantic_ai import BinaryContent

    spawn = AsyncMock()
    monkeypatch.setattr(deepseek_harness.asyncio, "create_subprocess_exec", spawn)
    observed = {}

    def fake_model(model_name, *, timeout_seconds):
        observed["model"] = model_name
        return object()

    class FakeAgent:
        def __init__(self, **kwargs):
            observed["agent"] = kwargs

        async def run(self, user_prompt):
            observed["prompt"] = user_prompt
            return SimpleNamespace(output=Answer(value="ok"))

    monkeypatch.setattr(deepseek_harness, "_vision_model", fake_model, raising=False)
    monkeypatch.setattr(deepseek_harness, "Agent", FakeAgent, raising=False)

    result = await runtime().run_structured(
        prompt="describe", system_prompt="system", output_type=Answer,
        images=[StructuredImage(b"real-image-bytes", "image/png")],
    )

    assert result.value == "ok"
    assert observed["model"] == "deepseek-v4-flash-vision-exp"
    assert observed["agent"]["system_prompt"] == "system"
    assert observed["prompt"][0] == "describe"
    assert isinstance(observed["prompt"][1], BinaryContent)
    assert observed["prompt"][1].data == b"real-image-bytes"
    assert observed["prompt"][1].media_type == "image/png"
    spawn.assert_not_called()


def test_vision_model_requires_existing_deepseek_credential(monkeypatch):
    import novelvideo.config  # load the project's .env before masking the key

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(KnowledgeRuntimeError) as error:
        deepseek_harness._vision_model("deepseek-v4-flash-vision-exp", timeout_seconds=10)
    assert error.value.code == "DSH_VISION_KEY_MISSING"
    assert "DEEPSEEK_API_KEY" in str(error.value)


@pytest.mark.asyncio
async def test_invalid_image_is_rejected_before_any_deepseek_request(monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(deepseek_harness, "_vision_model", model)

    with pytest.raises(ValueError, match="StructuredImage"):
        await runtime().run_structured(prompt="task", output_type=Answer, images=[object()])

    model.assert_not_awaited()


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
async def test_unsupported_reasoning_effort_retries_without_the_effort(
    monkeypatch, dsh, isolated_harness_home
):
    """dsh exits before working when the routed model rejects the effort.

    One unsupported preset must not take down every text task, so the runtime
    retries once and lets the model use its own default effort.
    """

    monkeypatch.setattr(
        deepseek_harness,
        "_resolve_default_model",
        lambda _snapshot: ("deepseek-v4-flash-vision-exp", "medium"),
    )
    rejected = stub_process(
        returncode=1,
        stderr=(
            b'dsh: UNSUPPORTED_REASONING_EFFORT: provider "deepseek-official" '
            b'model "deepseek-v4-flash-vision-exp" does not support reasoning '
            b'effort "medium"\n'
        ),
    )
    accepted = stub_process(b'{"value":"ok"}')
    spawn = AsyncMock(side_effect=[rejected, accepted])
    monkeypatch.setattr(deepseek_harness.asyncio, "create_subprocess_exec", spawn)

    result = await runtime().run_structured(prompt="task", output_type=Answer)

    assert result.value == "ok"
    assert spawn.await_count == 2
    settings = (isolated_harness_home / "settings.yaml").read_text(encoding="utf-8")
    assert "reasoningEffort" not in settings


@pytest.mark.asyncio
async def test_other_exec_failures_are_not_retried(
    monkeypatch, dsh, isolated_harness_home
):
    proc = stub_process(
        returncode=1,
        stderr=b'dsh: MISSING_CREDENTIAL: no API key for provider "deepseek-official"\n',
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(deepseek_harness.asyncio, "create_subprocess_exec", spawn)

    with pytest.raises(KnowledgeRuntimeError):
        await runtime().run_structured(prompt="task", output_type=Answer)

    assert spawn.await_count == 1


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
async def test_failure_surfaces_dshs_own_structured_error(monkeypatch, dsh):
    """dsh 的结构化错误行是失败时唯一的诊断证据，必须出现在异常消息里。"""

    proc = stub_process(
        returncode=1,
        stderr=(
            b"dsh: reasoning: thinking...\n"
            b'dsh: MISSING_CREDENTIAL: no API key for provider route '
            b'"deepseek-official"\n'
        ),
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)

    message = str(error.value)
    assert error.value.code == "DSH_EXEC_FAILED"
    assert "MISSING_CREDENTIAL" in message
    assert "no API key" in message
    assert "退出码 1" in message
    # 只是「余额」这种含糊指引的时代结束了：必须给出可操作方向。
    assert "DEEPSEEK_API_KEY" in message
    assert "DSH_HOME" in message
    # 推理流本体仍然不许进入消息。
    assert "thinking" not in message


@pytest.mark.asyncio
async def test_failure_never_leaks_the_reasoning_trace(monkeypatch, dsh):
    """关键安全测试：stderr 上的推理流（可能含用户小说内容）绝不能进异常消息。"""

    canary = "REASONING-LEAK-CANARY"
    proc = stub_process(
        returncode=1,
        stderr=(
            f"dsh: reasoning: {canary} the user's novel text follows\n".encode()
            + (f"dsh: reasoning: {canary} more private content\n".encode() * 20)
        ),
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)

    message = str(error.value)
    assert error.value.code == "DSH_EXEC_FAILED"
    assert canary not in message
    assert "novel text" not in message
    # 没有合法错误行时必须诚实地说「没解析出来」，而不是编一个原因。
    assert "未能从 dsh 输出中解析出具体原因" in message


@pytest.mark.asyncio
async def test_oversized_dsh_error_is_truncated(monkeypatch, dsh):
    """超长错误行不能撑爆日志：消息长度必须有界且带省略号。"""

    proc = stub_process(
        returncode=1, stderr=b"dsh: SOME_CODE: " + b"x" * 1000 + b"\n"
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)

    message = str(error.value)
    assert "SOME_CODE" in message
    assert "…" in message
    assert len(message) < 1000
    assert "x" * 400 not in message


@pytest.mark.asyncio
async def test_multiline_dsh_error_is_collapsed(monkeypatch, dsh):
    """多行/含制表符的错误消息必须被折叠，异常消息保持单行。"""

    proc = stub_process(returncode=1, stderr=b"dsh: SOME_CODE: line1\nline2\n")
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)
    message = str(error.value)
    assert "line1" in message
    assert "\n" not in message

    # 行内连续空白同样被折叠为单个空格。
    proc.communicate.return_value = (
        b"",
        b"dsh: SOME_CODE:   spaced\t\tout   message \n",
    )
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)
    assert "SOME_CODE: spaced out message）" in str(error.value)


@pytest.mark.asyncio
async def test_only_the_last_dsh_error_line_is_reported(monkeypatch, dsh):
    """dsh 可能先后报多个错误，退出原因以最后一条为准。"""

    proc = stub_process(
        returncode=1,
        stderr=(
            b"dsh: FIRST_CODE: first problem\n"
            b"dsh: SECOND_CODE: second problem\n"
        ),
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)

    message = str(error.value)
    assert "SECOND_CODE" in message
    assert "second problem" in message
    assert "FIRST_CODE" not in message
    assert "first problem" not in message


def test_dsh_error_extraction_ignores_non_error_output():
    """直接单测提取函数：非法 UTF-8、无错误行、空 stderr 都退化为 None。"""

    assert deepseek_harness._dsh_error_from_stderr(None) is None
    assert deepseek_harness._dsh_error_from_stderr(b"") is None
    assert deepseek_harness._dsh_error_from_stderr(b"plain failure text") is None
    assert deepseek_harness._dsh_error_from_stderr(b"dsh: reasoning: hmm") is None
    # 小写错误码不是 dsh 的错误格式。
    assert deepseek_harness._dsh_error_from_stderr(b"dsh: missing_credential: x") is None
    # 非法 UTF-8 用 replace 解码，不能抛异常。
    assert deepseek_harness._dsh_error_from_stderr(b"dsh: A_B: \xff\xfe") == (
        "A_B",
        "��",
    )


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


def test_harness_home_expands_tilde_from_env(monkeypatch):
    """docker env-file / compose 不会像 shell 一样展开 ``~``，必须由我们展开。"""

    monkeypatch.setenv("NOVELVIDEO_DSH_HOME", "~/nuomi-dsh-test")

    home = deepseek_harness.harness_home()

    assert home.is_absolute()
    assert home == Path.home() / "nuomi-dsh-test"
    assert str(home).startswith(str(Path.home()))
    assert "~" not in str(home)
    assert home != Path.home() / ".dsh"


def test_build_text_task_runtime_selects_harness():
    built = build_text_task_runtime(
        AgentTaskRouteSnapshot(
            runtime="deepseek_harness",
            model="deepseek-v4-flash-vision-exp",
            task_role="director_plan",
            source="global",
        )
    )
    assert type(built).__name__ == "DeepSeekHarnessStructuredRuntime"


def test_build_text_task_runtime_keeps_existing_branches():
    """新增分支不得打乱既有分发。"""

    codex = build_text_task_runtime(
        AgentTaskRouteSnapshot(
            runtime="codex", model="gpt-5.6-sol", task_role="director_plan", source="global"
        )
    )
    assert type(codex).__name__ == "CodexStructuredRuntime"

    workbuddy = build_text_task_runtime(
        AgentTaskRouteSnapshot(
            runtime="workbuddy", model="default-model", task_role="director_plan", source="global"
        )
    )
    assert type(workbuddy).__name__ == "WorkBuddyStructuredRuntime"

    model_api = build_text_task_runtime(
        AgentTaskRouteSnapshot(
            runtime="model_api", model="deepseek-v4-flash", task_role="director_plan", source="global"
        )
    )
    assert type(model_api).__name__ == "ModelApiStructuredRuntime"


@pytest.mark.asyncio
async def test_structured_calls_disable_every_model_facing_tool(
    monkeypatch, dsh, isolated_harness_home
):
    """Regression: 假鹿蜀/h3 视频任务超时。

    The H3 episode prompt optimizer ran as a full coding agent and spent 52 bash,
    26 grep and 22 read calls over 598s before DSH_EXEC_TIMEOUT_SECONDS killed
    it; the group then failed with "produced no plan for segment(s) ...".
    A structured call must not be able to touch the filesystem or the shell.
    """

    proc = stub_process(b'{"value":"ok"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    await runtime().run_structured(prompt="task", output_type=Answer)

    patch_path = isolated_harness_home / deepseek_harness.DSH_TOOL_FREE_PATCH_NAME
    assert patch_path.is_file()
    patch = patch_path.read_text(encoding="utf-8")
    for plugin in (
        "tool-bash",
        "tool-pwsh",
        "tool-jobs",
        "tool-fs",
        "tool-fs-search",
        "tool-skill",
        "tool-subagent",
        "tool-subagent-control",
        "tool-workflow",
        "tool-goal",
        "tool-ralph",
        "tool-web",
    ):
        assert f"- id: {plugin}\n  disabled: true" in patch


@pytest.mark.asyncio
async def test_tool_free_patch_is_written_once_and_repaired(
    monkeypatch, dsh, isolated_harness_home
):
    isolated_harness_home.mkdir(parents=True, exist_ok=True)
    patch_path = isolated_harness_home / deepseek_harness.DSH_TOOL_FREE_PATCH_NAME
    patch_path.write_text("# stale\n", encoding="utf-8")
    proc = stub_process(b'{"value":"ok"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    await runtime().run_structured(prompt="task", output_type=Answer)

    assert patch_path.read_text(encoding="utf-8") == deepseek_harness.DSH_TOOL_FREE_PATCH


@pytest.mark.asyncio
async def test_timeout_names_the_execution_budget(monkeypatch, dsh, isolated_harness_home):
    monkeypatch.setenv("DSH_EXEC_TIMEOUT_SECONDS", "1234")
    proc = SimpleNamespace(
        returncode=None, communicate=AsyncMock(side_effect=asyncio.TimeoutError)
    )
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr(deepseek_harness, "terminate_process_tree", AsyncMock())

    with pytest.raises(asyncio.TimeoutError) as error:
        await runtime().run_structured(prompt="task", output_type=Answer)

    # Type is preserved so upstream keeps treating it as a soft failure, but the
    # message must say which limit was hit.
    assert "1234" in str(error.value)
    assert "DSH_EXEC_TIMEOUT_SECONDS" in str(error.value)


class StrictKind(BaseModel):
    kind: Literal["a"]


@pytest.mark.asyncio
async def test_rejected_payload_names_the_field_and_the_rule(
    monkeypatch, dsh, isolated_harness_home
):
    """Regression: the h3 optimizer's DSH_OUTPUT_INVALID hid the real rule.

    The payload was valid JSON violating a cross-field rule, so the fixed message
    left both the operator and the retry with nothing to act on.
    """

    proc = stub_process(b'{"kind": "b"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=StrictKind)

    assert error.value.code == "DSH_OUTPUT_INVALID"
    assert "kind" in str(error.value)
    assert "Input should be 'a'" in str(error.value)


@pytest.mark.asyncio
async def test_rejected_payload_never_echoes_the_model_text(
    monkeypatch, dsh, isolated_harness_home
):
    # pydantic's error items carry `input`, and the JSON decoder's message carries
    # a document snippet: neither may reach the error.
    proc = stub_process(b'{"kind": "TOPSECRET"}')
    monkeypatch.setattr(
        deepseek_harness.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=StrictKind)
    assert "TOPSECRET" not in str(error.value)

    proc = stub_process(b'{"kind": "TOPSECRET", ')
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt="task", output_type=StrictKind)
    assert "TOPSECRET" not in str(error.value)
