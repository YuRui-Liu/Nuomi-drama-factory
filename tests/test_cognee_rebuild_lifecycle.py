from __future__ import annotations

import pytest

from novelvideo.cognee.store import CogneeStore


@pytest.mark.asyncio
async def test_prune_releases_cached_graph_engine_before_deleting_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = CogneeStore.__new__(CogneeStore)
    store.state_dir = "project-state"

    monkeypatch.setattr(
        store,
        "_release_cognee_graph_engine",
        lambda: events.append("release"),
    )

    monkeypatch.setattr("os.path.exists", lambda _path: True)

    def fake_rmtree(path: str) -> None:
        assert path.endswith("cognee_system")
        events.append("delete")

    monkeypatch.setattr("shutil.rmtree", fake_rmtree)

    await store._prune_cognee_only()

    assert events == ["release", "delete"]
