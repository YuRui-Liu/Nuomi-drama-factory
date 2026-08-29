from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_ingest_runner_routes_structured_project_without_cognee(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.knowledge_pipeline import (
        KNOWLEDGE_PIPELINE_STRUCTURED,
        STATUS_STRUCTURED_PENDING,
    )
    from novelvideo.task_backend.runners import ingest

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(
        json.dumps(
            {
                "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
                "knowledge_pipeline_status": STATUS_STRUCTURED_PENDING,
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, object]] = []

    class FakeStore:
        def __init__(self, *_args, **kwargs):
            calls.append(("store", kwargs))

        async def initialize(self):
            calls.append(("initialize", None))

        async def close(self):
            calls.append(("close", None))

    async def fake_structured(store, path, **kwargs):
        calls.append(("structured", (store, path, kwargs.get("spine_template"))))
        kwargs["on_progress"](0.5, "确定性切分")
        kwargs["on_log"]("分块 1/2")
        return {"pipeline": "structured_v1", "run_id": "run-1"}

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeStore)
    monkeypatch.setattr(
        "novelvideo.structured_ingest.ingest_source_text_structured",
        fake_structured,
    )
    monkeypatch.setattr(
        ingest,
        "build_project_knowledge_runtime",
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError("structured_v1 must not build a Cognee runtime")
        ),
    )
    progress: list[dict] = []
    monkeypatch.setattr(
        ingest,
        "get_task_manager",
        lambda: SimpleNamespace(
            update_progress_for_project=lambda *_args, **kwargs: progress.append(kwargs)
        ),
    )
    ctx = SimpleNamespace(
        owner_project_label="owner/demo",
        output_dir=tmp_path / "output",
        state_dir=state_dir,
    )

    result = await ingest._run_ingest_fast(
        {
            "payload": {
                "novel_path": str(tmp_path / "novel.txt"),
                "config": {"spine_template": "narrated", "rebuild": True},
            }
        },
        ctx,
    )

    assert result == {"pipeline": "structured_v1", "run_id": "run-1"}
    assert [name for name, _ in calls] == ["store", "initialize", "structured", "close"]
    assert progress[0]["progress"] == 0.5
    assert progress[1]["progress"] is None


@pytest.mark.asyncio
async def test_structured_project_rejects_ai_episode_planning_before_enqueue(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.api.routes import episodes
    from novelvideo.api.schemas import EpisodePlanRequest
    from novelvideo.knowledge_pipeline import (
        KNOWLEDGE_PIPELINE_STRUCTURED,
        KnowledgePipelineUnsupported,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(
        json.dumps({"knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED}),
        encoding="utf-8",
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "novel.txt").write_text("第一章\n正文", encoding="utf-8")

    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(
            ctx=SimpleNamespace(project_id="project-1"),
            output_dir=str(project_dir),
            project_dir=str(project_dir),
            state_dir=str(state_dir),
        )

    async def reject_enqueue(*_args, **_kwargs):
        raise AssertionError("unsupported planning must be rejected before enqueue")

    monkeypatch.setattr(episodes, "resolve_project_scope", resolve)
    monkeypatch.setattr(
        episodes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=reject_enqueue),
    )

    response = await episodes.plan_episodes(
        "project-1",
        EpisodePlanRequest(target_episodes=8, planning_mode="ai_events"),
        {"username": "owner"},
    )

    assert response == {
        "ok": False,
        "code": KnowledgePipelineUnsupported.error_code,
        "error": "该项目只支持按章节/集号的确定性分集",
    }

@pytest.mark.asyncio
async def test_ingest_start_rejects_stale_pipeline_selection_before_enqueue(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.api.routes import ingest
    from novelvideo.api.schemas import IngestStart
    from novelvideo.knowledge_pipeline import (
        COGNEE_LEGACY,
        KNOWLEDGE_PIPELINE_STRUCTURED,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(
        json.dumps({"knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED}),
        encoding="utf-8",
    )

    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(
            ctx=SimpleNamespace(project_id="project-1", state_dir=state_dir),
            state_dir=str(state_dir),
            username="owner",
            project_name="demo",
            project_dir=tmp_path / "project",
        )

    async def reject_enqueue(*_args, **_kwargs):
        raise AssertionError("stale pipeline selection must be rejected before enqueue")

    monkeypatch.setattr(ingest, "resolve_project_scope", resolve)
    monkeypatch.setattr(
        ingest,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=reject_enqueue),
    )

    response = await ingest.start_ingest(
        "project-1",
        IngestStart(
            filename="missing.txt",
            knowledge_pipeline=COGNEE_LEGACY,
        ),
        {"username": "owner"},
    )

    assert response == {
        "ok": False,
        "code": "KNOWLEDGE_PIPELINE_STALE_SELECTION",
        "error": "项目知识管线已变更，请刷新后重试",
    }
