from __future__ import annotations

from pathlib import Path
import pytest

from novelvideo.project_context import ProjectContext


class _Manager:
    def update_progress_for_project(self, *args, **kwargs) -> None:
        pass


class _ClosableStore:
    def __init__(self) -> None:
        self.closed = False
        self.initialized = False
        self.graph_loaded = False

    async def initialize(self) -> None:
        self.initialized = True

    async def load_graph_state(self) -> None:
        self.graph_loaded = True

    async def close(self) -> None:
        self.closed = True

    def get_all_characters(self) -> list:
        return []

    def get_sketch_colors(self, episode: int) -> dict:
        return {}


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_123",
        project_name="demo",
        owner_type="user",
        owner_id="user_123",
        owner_username="alice",
        requester_user_id="user_123",
        requester_username="alice",
        requester_principals=(("user", "user_123"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_beat_video_prompt_runner_closes_sqlite_store(monkeypatch, tmp_path):
    from novelvideo.api import deps
    from novelvideo.api.routes import scripts
    from novelvideo.task_backend.runners import script as runner

    store = _ClosableStore()

    async def fake_make_sqlite_store_for_context(ctx):
        return store

    async def fake_generate_and_save_beat_video_prompt(**kwargs):
        return {"field": "video_prompt", "prompt": "camera move"}

    monkeypatch.setattr(runner, "get_task_manager", lambda: _Manager())
    monkeypatch.setattr(deps, "make_sqlite_store_for_context", fake_make_sqlite_store_for_context)
    monkeypatch.setattr(
        scripts,
        "_generate_and_save_beat_video_prompt",
        fake_generate_and_save_beat_video_prompt,
    )

    result = await runner._run_beat_video_prompt(
        {"episode": 1, "beat_num": 2, "payload": {"output_dir": str(tmp_path)}},
        _ctx(tmp_path),
    )

    assert result["prompt"] == "camera move"
    assert store.closed is True
