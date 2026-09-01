from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.agents import identity_planner
from novelvideo.structured_extraction import ChunkCharacterOutput
from novelvideo.task_backend.registry import get_project_task_runner_registration
from novelvideo.task_backend.runners import identity, ingest  # noqa: F401


def test_ingest_and_identity_planner_freeze_knowledge_extraction_route() -> None:
    for task_type in ("ingest_fast", "identity_planner"):
        registration = get_project_task_runner_registration(task_type)
        assert registration is not None
        assert registration.text_task_role == "knowledge_extraction"


def test_structured_character_extraction_uses_current_text_runtime(monkeypatch) -> None:
    from novelvideo import structured_extraction
    from novelvideo.text_task_runtime import runtime as runtime_module

    routed_runtime = SimpleNamespace(snapshot=SimpleNamespace(model="gpt-5.6-sol"))
    monkeypatch.setattr(
        runtime_module,
        "current_text_task_runtime",
        lambda: routed_runtime,
    )
    monkeypatch.setattr(
        "pydantic_ai.Agent",
        lambda *_args, **_kwargs: pytest.fail("legacy model API agent was created"),
    )

    agent = structured_extraction._create_agent()

    assert agent.runtime is routed_runtime
    assert agent.output_type is ChunkCharacterOutput


def test_identity_planner_agent_uses_current_text_runtime(monkeypatch) -> None:
    from novelvideo.text_task_runtime import runtime as runtime_module

    routed_runtime = SimpleNamespace(snapshot=SimpleNamespace(model="gpt-5.6-sol"))
    monkeypatch.setattr(
        runtime_module,
        "current_text_task_runtime",
        lambda: routed_runtime,
    )
    monkeypatch.setattr(
        identity_planner,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: pytest.fail("legacy DeepSeek model was resolved"),
    )

    agent = identity_planner._create_identity_agent(
        output_type=identity_planner.EpisodeCastList,
        model_env="IDENTITY_PLANNER_CAST_MODEL",
        thinking_env="IDENTITY_PLANNER_CAST_THINKING_LEVEL",
        default_thinking_level="low",
    )

    assert agent.runtime is routed_runtime
    assert agent.output_type is identity_planner.EpisodeCastList
