from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.knowledge_runtime.context import (
    KnowledgeRuntimeContext,
    require_knowledge_runtime_context,
)
from novelvideo.knowledge_runtime.settings import OllamaSettings
from novelvideo.task_backend.runners import ingest


@pytest.mark.asyncio
async def test_ingest_context_covers_store_lifecycle(monkeypatch, tmp_path: Path) -> None:
    seen: list[str] = []

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            pass

        async def prepare_knowledge_rebuild(self):
            seen.append("prepare:demo")

        async def initialize(self):
            seen.append(f"initialize:{require_knowledge_runtime_context().project_name}")

        async def ingest_novel_fast(self, *_args, **_kwargs):
            seen.append(f"ingest:{require_knowledge_runtime_context().project_name}")
            return {"ok": True}

        async def close(self):
            seen.append(f"close:{require_knowledge_runtime_context().project_name}")

    class FakeManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    runtime = KnowledgeRuntimeContext(
        username="owner",
        project_name="demo",
        state_dir=tmp_path,
        text_backend=SimpleNamespace(),
        embedding=OllamaSettings(model="bge-m3", dimension=1024),
    )
    monkeypatch.setattr("novelvideo.cognee.CogneeStore", FakeStore)
    monkeypatch.setattr(ingest, "get_task_manager", lambda: FakeManager())
    monkeypatch.setattr(ingest, "build_project_knowledge_runtime", lambda _ctx: runtime)

    ctx = SimpleNamespace(
        owner_project_label="owner/demo",
        output_dir=tmp_path,
        project_name="demo",
        owner_username="owner",
        state_dir=tmp_path,
    )
    result = await ingest._run_ingest_fast(
        {
            "payload": {
                "novel_path": str(tmp_path / "novel.txt"),
                "config": {"rebuild": True},
            }
        },
        ctx,
    )

    assert result == {"ok": True}
    assert seen == [
        "prepare:demo",
        "initialize:demo",
        "ingest:demo",
        "close:demo",
    ]
