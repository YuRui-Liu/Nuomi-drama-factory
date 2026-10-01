import io
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import BaseModel, Field

from novelvideo.knowledge_runtime.codex import StructuredImage
from novelvideo.text_task_runtime.runtime import StructuredRuntimeAgent


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(stream, format="PNG")
    return StructuredImage(data=stream.getvalue(), media_type="image/png")


class Output(BaseModel):
    count: int = Field(ge=1)


@pytest.mark.asyncio
async def test_agent_keeps_images_on_validation_retry():
    received = []

    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, **kwargs):
            received.append(kwargs)
            return {"count": 0 if len(received) == 1 else 1}

    agent = StructuredRuntimeAgent(Runtime(), output_type=Output,
        validation_context={}, output_retries=1)
    image = picture()
    result = await agent.run("inspect", images=[image])
    assert result.output.count == 1
    assert len(received) == 2
    assert all(call["images"] == [image] for call in received)
    assert "inspect" in received[1]["prompt"]


@pytest.mark.asyncio
async def test_text_only_agent_does_not_add_images_keyword():
    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, *, prompt, output_type, system_prompt, validation_context):
            return {"ok": True}

    result = await StructuredRuntimeAgent(Runtime(), output_type=dict).run("text")
    assert result.output == {"ok": True}


@pytest.mark.asyncio
async def test_malformed_structured_output_is_reasked_once():
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

    received = []

    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, **kwargs):
            received.append(kwargs)
            if len(received) == 1:
                raise KnowledgeRuntimeError(
                    "DeepSeek Harness 未返回符合要求的结构化结果。",
                    code="DSH_OUTPUT_INVALID",
                )
            return {"count": 1}

    agent = StructuredRuntimeAgent(Runtime(), output_type=Output, output_retries=1)

    result = await agent.run("plan")

    assert result.output["count"] == 1
    assert len(received) == 2
    assert "plan" in received[1]["prompt"]


@pytest.mark.asyncio
async def test_runtime_failure_without_output_code_is_not_retried():
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

    received = []

    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, **kwargs):
            received.append(kwargs)
            raise KnowledgeRuntimeError("dsh 不可用", code="DSH_EXEC_FAILED")

    agent = StructuredRuntimeAgent(Runtime(), output_type=Output, output_retries=2)

    with pytest.raises(KnowledgeRuntimeError):
        await agent.run("plan")

    assert len(received) == 1


@pytest.mark.asyncio
async def test_invalid_images_fail_before_runtime_call():
    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, **kwargs):
            pytest.fail("invalid image must not reach runtime")

    agent = StructuredRuntimeAgent(Runtime(), output_type=dict)
    with pytest.raises(ValueError):
        await agent.run("inspect", images=[picture()] * 9)


@pytest.mark.asyncio
async def test_image_rejection_does_not_fall_back_to_text():
    received = []

    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, **kwargs):
            received.append(kwargs)
            raise ValueError("vision unavailable")

    agent = StructuredRuntimeAgent(Runtime(), output_type=dict, output_retries=2)
    with pytest.raises(ValueError, match="vision unavailable"):
        await agent.run("inspect", images=[picture()])
    assert len(received) == 1


@pytest.mark.asyncio
async def test_schema_rejection_retry_carries_the_failing_field_and_rule():
    """Regression: h3 视频任务 DSH_OUTPUT_INVALID。

    The payload was valid JSON that broke a cross-field rule, so the old generic
    "not schema-shaped JSON" re-ask told the model nothing and it repeated the
    same violation. The runtime's structural detail must reach the retry prompt.
    """

    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

    received = []

    class Runtime:
        snapshot = SimpleNamespace(model="test")

        async def run_structured(self, **kwargs):
            received.append(kwargs)
            if len(received) == 1:
                raise KnowledgeRuntimeError(
                    "DeepSeek Harness 未返回符合要求的结构化结果。"
                    "（segments.0.director_plan: Value error, ref2va requires reference_summary）",
                    code="DSH_OUTPUT_INVALID",
                )
            return {"count": 1}

    agent = StructuredRuntimeAgent(Runtime(), output_type=Output, output_retries=1)

    await agent.run("plan")

    assert "ref2va requires reference_summary" in received[1]["prompt"]
    assert "segments.0.director_plan" in received[1]["prompt"]
