from unittest.mock import AsyncMock

import pytest


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
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "人物：林晚"}]),
    )
    monkeypatch.setattr(
        LLMGateway,
        "acreate_structured_output",
        AsyncMock(side_effect=ValueError("upstream authentication failed")),
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
async def test_character_extraction_allows_successful_empty_llm_result(monkeypatch):
    import cognee
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "没有人类角色"}]),
    )
    monkeypatch.setattr(
        LLMGateway,
        "acreate_structured_output",
        AsyncMock(return_value=pipeline.CharacterEnrichmentList(characters=[])),
    )

    assert await pipeline.extract_characters_from_graph() == []
