from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _disable_project_context(monkeypatch):
    from novelvideo.cognee import pipeline

    monkeypatch.setattr(pipeline, "_set_cognee_project_context", lambda **_: None)
    return pipeline


@pytest.mark.asyncio
async def test_prop_extraction_propagates_graph_search_failure(monkeypatch):
    import cognee

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(side_effect=RuntimeError("graph unavailable")),
    )

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await pipeline.extract_props_from_graph()


@pytest.mark.asyncio
async def test_prop_extraction_propagates_structured_provider_failure(monkeypatch):
    import cognee
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "重要道具：传国玉玺"}]),
    )
    monkeypatch.setattr(
        LLMGateway,
        "acreate_structured_output",
        AsyncMock(side_effect=ValueError("structured response invalid")),
    )

    with pytest.raises(ValueError, match="structured response invalid"):
        await pipeline.extract_props_from_graph()


@pytest.mark.asyncio
async def test_prop_extraction_allows_true_empty_graph_search(monkeypatch):
    import cognee
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(cognee, "search", AsyncMock(return_value=[]))
    provider = AsyncMock(side_effect=AssertionError("provider must not run"))
    monkeypatch.setattr(LLMGateway, "acreate_structured_output", provider)

    assert await pipeline.extract_props_from_graph() == []
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_prop_extraction_rejects_nonempty_search_without_usable_context(
    monkeypatch,
):
    import cognee

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "   "}]),
    )

    with pytest.raises(RuntimeError, match="usable context"):
        await pipeline.extract_props_from_graph()


@pytest.mark.asyncio
async def test_prop_extraction_allows_successful_empty_structured_result(monkeypatch):
    import cognee
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    pipeline = _disable_project_context(monkeypatch)
    monkeypatch.setattr(
        cognee,
        "search",
        AsyncMock(return_value=[{"search_result": "没有重要道具"}]),
    )
    monkeypatch.setattr(
        LLMGateway,
        "acreate_structured_output",
        AsyncMock(return_value=SimpleNamespace(props=[])),
    )

    assert await pipeline.extract_props_from_graph() == []
