from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
import io
import sys


def _disable_project_context(monkeypatch):
    from novelvideo.cognee import pipeline

    monkeypatch.setattr(pipeline, "_set_cognee_project_context", lambda **_: None)
    return pipeline


@pytest.mark.asyncio
async def test_character_extraction_propagates_graph_search_failure(monkeypatch):
    import cognee

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(side_effect=RuntimeError("graph unavailable")),
    )

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await pipeline.extract_characters_from_graph()


@pytest.mark.asyncio
async def test_character_extraction_propagates_llm_failure(monkeypatch):
    import cognee

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "人物：林晚"}]),
    )
    class FailingAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _prompt):
            raise ValueError("upstream authentication failed")

    monkeypatch.setattr(pipeline, "Agent", FailingAgent)
    monkeypatch.setattr(
        pipeline,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(ValueError, match="authentication"):
        await pipeline.extract_characters_from_graph()


@pytest.mark.asyncio
async def test_character_extraction_allows_empty_graph_context(monkeypatch):
    import cognee

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(cognee, "search", AsyncMock(return_value=[]))

    assert await pipeline.extract_characters_from_graph() == []


@pytest.mark.asyncio
async def test_character_extraction_logs_are_safe_on_windows_gbk(monkeypatch):
    from novelvideo.cognee import pipeline

    async def empty_search(**_kwargs):
        return []

    raw = io.BytesIO()
    gbk_stdout = io.TextIOWrapper(raw, encoding="gbk", errors="strict")
    monkeypatch.setattr("cognee.search", empty_search)
    monkeypatch.setattr(sys, "stdout", gbk_stdout)

    assert await pipeline.extract_characters_from_graph() == []
    gbk_stdout.flush()
    assert "[WARN]" in raw.getvalue().decode("gbk")


@pytest.mark.asyncio
async def test_character_extraction_allows_successful_empty_llm_result(monkeypatch):
    import cognee

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "没有人类角色"}]),
    )
    class EmptyAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _prompt):
            return SimpleNamespace(
                output=pipeline.CharacterEnrichmentList(characters=[])
            )

    monkeypatch.setattr(pipeline, "Agent", EmptyAgent)
    monkeypatch.setattr(
        pipeline,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )

    assert await pipeline.extract_characters_from_graph() == []


@pytest.mark.asyncio
async def test_character_extraction_uses_global_text_runtime_not_cognee_llm(monkeypatch):
    import cognee
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "人物：林晚，女性，主角"}]),
    )
    cognee_llm = AsyncMock(side_effect=AssertionError("Cognee LLM must not be used"))
    monkeypatch.setattr(LLMGateway, "acreate_structured_output", cognee_llm)

    model_calls = []
    monkeypatch.setattr(
        pipeline,
        "get_newapi_text_pydantic_model",
        lambda model_env, default_model: model_calls.append((model_env, default_model))
        or object(),
        raising=False,
    )

    class FakeAgent:
        def __init__(self, model, **kwargs):
            self.model = model
            self.kwargs = kwargs
            assert kwargs.get("model_settings") is None

        async def run(self, prompt):
            assert "人物：林晚" in prompt
            return SimpleNamespace(
                output=pipeline.CharacterEnrichmentList(
                    characters=[
                        pipeline.CharacterEnrichment(
                            name="林晚",
                            aliases=[],
                            role="主角",
                            is_main=True,
                            gender="女",
                            age_group="youth",
                            body_type="纤细",
                            description="冷静",
                            face_prompt="女性，青年，黑色长发，黑色眼睛，白皙肤色，鹅蛋脸",
                        )
                    ]
                )
            )

    monkeypatch.setattr(pipeline, "Agent", FakeAgent, raising=False)

    characters = await pipeline.extract_characters_from_graph()

    assert [character.name for character in characters] == ["林晚"]
    assert model_calls == [("CHARACTER_BUILD_MODEL", "deepseek-chat")]
    cognee_llm.assert_not_awaited()
