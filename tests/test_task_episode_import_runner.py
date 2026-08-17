from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_runner_commits_with_cas_and_returns_per_episode_results(monkeypatch):
    from novelvideo.task_backend.runners import episode_import

    captured = {}

    class Service:
        async def commit(self, batch):
            captured.update(batch)
            return SimpleNamespace(target_revision=5)

    monkeypatch.setattr(episode_import, "_build_service", lambda ctx: _async(Service()))
    envelope = {"payload": {
        "target_revision": 5,
        "snapshot": {"preview_id": "p", "expected_revision": 4,
        "resolutions": {"1": "overwrite", "2": "skip"},
        "items": [{"episode_number": 1}, {"episode_number": 2}, {"episode_number": 3}]},
        "items": [{"episode_number": 1}, {"episode_number": 2}, {"episode_number": 3}],
    }}

    result = await episode_import._run_episode_import(envelope, SimpleNamespace())

    assert captured["expected_revision"] == 4
    assert result == {
        "target_revision": 5,
        "episodes": [
            {"episode_number": 1, "status": "overwritten"},
            {"episode_number": 2, "status": "skipped"},
            {"episode_number": 3, "status": "added"},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolutions", "code"),
    [
        ({}, "EPISODE_IMPORT_GRAPH_INCREMENT_FAILED"),
        ({"1": "overwrite"}, "EPISODE_IMPORT_GRAPH_REBUILD_FAILED"),
    ],
)
async def test_runner_maps_graph_failures_to_stable_codes(monkeypatch, resolutions, code):
    from novelvideo.episode_import_service import EpisodeImportGraphError
    from novelvideo.task_backend.runners import episode_import

    class Service:
        async def commit(self, batch):
            raise EpisodeImportGraphError("provider detail must not become the contract")

    monkeypatch.setattr(episode_import, "_build_service", lambda ctx: _async(Service()))
    envelope = {"payload": {"snapshot": {
        "preview_id": "p", "expected_revision": 0,
        "resolutions": resolutions, "items": [],
    }}}

    with pytest.raises(RuntimeError, match=f"^{code}$"):
        await episode_import._run_episode_import(envelope, SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad item")])
async def test_runner_does_not_misclassify_non_graph_failures(monkeypatch, error):
    from novelvideo.task_backend.runners import episode_import

    class Service:
        async def commit(self, batch):
            raise error

    monkeypatch.setattr(episode_import, "_build_service", lambda ctx: _async(Service()))
    envelope = {"payload": {"snapshot": {
        "preview_id": "p", "expected_revision": 0,
        "resolutions": {"1": "overwrite"}, "items": [],
    }}}

    with pytest.raises(type(error), match=str(error)):
        await episode_import._run_episode_import(envelope, SimpleNamespace())


@pytest.mark.asyncio
async def test_runner_preserves_revision_conflict_code(monkeypatch):
    from novelvideo.episode_source_store import EpisodeSourceRevisionConflict
    from novelvideo.task_backend.runners import episode_import

    class Service:
        async def commit(self, batch):
            raise EpisodeSourceRevisionConflict("changed")

    monkeypatch.setattr(episode_import, "_build_service", lambda ctx: _async(Service()))
    envelope = {"payload": {"snapshot": {
        "preview_id": "p", "expected_revision": 0, "resolutions": {}, "items": [],
    }}}
    with pytest.raises(RuntimeError, match="^EPISODE_IMPORT_REVISION_CONFLICT$"):
        await episode_import._run_episode_import(envelope, SimpleNamespace())


async def _async(value):
    return value
