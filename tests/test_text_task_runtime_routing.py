from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from pydantic_ai import PromptedOutput

from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from novelvideo.text_task_runtime.runtime import (
    CodexStructuredRuntime,
    ModelApiStructuredRuntime,
    current_text_task_runtime,
    text_task_runtime_scope,
)


class Answer(BaseModel):
    value: str


class FakeCodexBackend:
    async def acreate_structured_output(
        self, text_input, system_prompt, response_model, **kwargs
    ):
        assert text_input == "prompt"
        assert system_prompt == "system"
        assert kwargs == {}
        return response_model(value="ok")


class FakeAgentResult:
    output = Answer(value="ok")


class FakeModelApiAgent:
    async def run(self, prompt):
        assert prompt == "prompt"
        return FakeAgentResult()


async def test_codex_and_model_api_return_the_same_structured_type():
    codex = CodexStructuredRuntime(
        AgentTaskRouteSnapshot(
            task_role="director_plan",
            source="task",
            runtime="codex",
            model="gpt-5.6-sol",
        ),
        backend=FakeCodexBackend(),
    )
    captured = {}

    def agent_factory(**kwargs):
        captured.update(kwargs)
        return FakeModelApiAgent()

    model_api = ModelApiStructuredRuntime(
        AgentTaskRouteSnapshot(
            task_role="director_plan",
            source="task",
            runtime="model_api",
            model="deepseek-v4-flash",
            reasoning_effort="high",
        ),
        agent_factory=agent_factory,
    )

    assert await codex.run_structured(
        prompt="prompt", output_type=Answer, system_prompt="system"
    ) == Answer(value="ok")
    assert await model_api.run_structured(
        prompt="prompt", output_type=Answer, system_prompt="system"
    ) == Answer(value="ok")
    assert isinstance(captured["output_type"], PromptedOutput)
    assert captured["output_type"].outputs == Answer
    assert captured["model_settings"] == {"openai_reasoning_effort": "high"}
    assert "tool_choice" not in captured["model_settings"]


def test_codex_reasoning_effort_is_present_in_real_backend_argv(tmp_path):
    runtime = CodexStructuredRuntime(
        AgentTaskRouteSnapshot(
            task_role="director_plan",
            source="task",
            runtime="codex",
            model="gpt-5.6-sol",
            reasoning_effort="high",
        )
    )

    argv = runtime._backend.build_argv(
        cwd=str(tmp_path),
        output_path=str(tmp_path / "out.txt"),
        schema_path=None,
    )

    assert ["-c", 'model_reasoning_effort="high"'] == argv[
        argv.index("-c") : argv.index("-c") + 2
    ]


def test_model_api_runtime_rejects_direct_snapshot_with_unsupported_skill():
    snapshot = AgentTaskRouteSnapshot(
        task_role="director_plan",
        source="task",
        runtime="model_api",
        model="deepseek-v4-flash",
        skill_id="director-plan",
    )

    with pytest.raises(ValueError, match="skill_id"):
        ModelApiStructuredRuntime(snapshot)


async def test_runtime_scope_is_concurrency_safe_and_resets():
    async def read_runtime(runtime_name: str) -> str:
        snapshot = AgentTaskRouteSnapshot(
            task_role="director_plan",
            source="task",
            runtime=runtime_name,
            model="model",
        )
        with text_task_runtime_scope(snapshot):
            await asyncio.sleep(0)
            current = current_text_task_runtime()
            assert current is not None
            return current.snapshot.runtime

    assert await asyncio.gather(read_runtime("codex"), read_runtime("model_api")) == [
        "codex",
        "model_api",
    ]
    assert current_text_task_runtime() is None


def test_runtime_scope_resets_after_exception():
    snapshot = AgentTaskRouteSnapshot(
        task_role="director_plan",
        source="global",
        runtime="codex",
        model="gpt-5.6-sol",
    )
    try:
        with text_task_runtime_scope(snapshot):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert current_text_task_runtime() is None
