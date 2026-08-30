from __future__ import annotations

import asyncio

from pydantic import BaseModel

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
    model_api = ModelApiStructuredRuntime(
        AgentTaskRouteSnapshot(
            task_role="director_plan",
            source="task",
            runtime="model_api",
            model="deepseek-v4-flash",
        ),
        agent_factory=lambda **kwargs: FakeModelApiAgent(),
    )

    assert await codex.run_structured(
        prompt="prompt", output_type=Answer, system_prompt="system"
    ) == Answer(value="ok")
    assert await model_api.run_structured(
        prompt="prompt", output_type=Answer, system_prompt="system"
    ) == Answer(value="ok")


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

