from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def obsolete_runner_scopes_project_knowledge_runtime_around_graph_commit(monkeypatch):
    from novelvideo.task_backend.runners import episode_import

    runtime = object()
    active_runtime = None

    @contextmanager
    def fake_scope(value):
        nonlocal active_runtime
        active_runtime = value
        try:
            yield value
        finally:
            active_runtime = None

    class Service:
        async def commit(self, _batch):
            assert active_runtime is runtime
            return SimpleNamespace(target_revision=1)

    ctx = SimpleNamespace(project_name="demo")
    monkeypatch.setattr(
        episode_import, "build_project_knowledge_runtime", lambda value: runtime
    )
    monkeypatch.setattr(episode_import, "knowledge_runtime_scope", fake_scope)
    monkeypatch.setattr(
        episode_import, "_build_service", lambda value, **_kwargs: _async(Service())
    )

    await episode_import._run_episode_import_with_runtime(
        {
            "payload": {
                "snapshot": {
                    "preview_id": "p",
                    "expected_revision": 0,
                    "resolutions": {},
                    "items": [],
                }
            }
        },
        ctx,
    )

    assert active_runtime is None


@pytest.mark.asyncio
async def obsolete_runner_publishes_graph_progress_and_logs(monkeypatch):
    from novelvideo.task_backend.runners import episode_import

    updates: list[dict] = []

    class Manager:
        def update_progress_for_project(self, *_args, **kwargs):
            updates.append(kwargs)

    class Service:
        async def commit(self, _batch):
            return SimpleNamespace(target_revision=1)

    async def build(_ctx, **callbacks):
        callbacks["on_progress"](0.3, "构建知识图谱...")
        callbacks["on_log"]("知识图谱数据项 1/3")
        return Service()

    monkeypatch.setattr(episode_import, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(episode_import, "_build_service", build)

    await episode_import._run_episode_import(
        {
            "__run_task_id": "task-1",
            "payload": {
                "snapshot": {
                    "preview_id": "p",
                    "expected_revision": 0,
                    "resolutions": {},
                    "items": [],
                }
            },
        },
        SimpleNamespace(),
    )

    assert [item["current_task"] for item in updates] == [
        "构建知识图谱...",
        "知识图谱数据项 1/3",
    ]
    assert updates[0]["progress"] > 0.01
    assert updates[1]["logs"] == ["知识图谱数据项 1/3"]


@pytest.mark.asyncio
async def obsolete_runner_commits_with_cas_and_returns_per_episode_results(monkeypatch):
    from novelvideo.task_backend.runners import episode_import

    captured = {}

    class Service:
        async def commit(self, batch):
            captured.update(batch)
            return SimpleNamespace(target_revision=5)

    monkeypatch.setattr(
        episode_import, "_build_service", lambda ctx, **_kwargs: _async(Service())
    )
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
async def obsolete_runner_maps_graph_failures_to_stable_codes(monkeypatch, resolutions, code):
    from novelvideo.episode_import_service import EpisodeImportGraphError
    from novelvideo.task_backend.runners import episode_import

    class Service:
        async def commit(self, batch):
            raise EpisodeImportGraphError("provider detail must not become the contract")

    monkeypatch.setattr(
        episode_import, "_build_service", lambda ctx, **_kwargs: _async(Service())
    )
    envelope = {"payload": {"snapshot": {
        "preview_id": "p", "expected_revision": 0,
        "resolutions": resolutions, "items": [],
    }}}

    with pytest.raises(RuntimeError, match=f"^{code}$"):
        await episode_import._run_episode_import(envelope, SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad item")])
async def obsolete_runner_does_not_misclassify_non_graph_failures(monkeypatch, error):
    from novelvideo.task_backend.runners import episode_import

    class Service:
        async def commit(self, batch):
            raise error

    monkeypatch.setattr(
        episode_import, "_build_service", lambda ctx, **_kwargs: _async(Service())
    )
    envelope = {"payload": {"snapshot": {
        "preview_id": "p", "expected_revision": 0,
        "resolutions": {"1": "overwrite"}, "items": [],
    }}}

    with pytest.raises(type(error), match=str(error)):
        await episode_import._run_episode_import(envelope, SimpleNamespace())


@pytest.mark.asyncio
async def obsolete_runner_preserves_revision_conflict_code(monkeypatch):
    from novelvideo.episode_source_store import EpisodeSourceRevisionConflict
    from novelvideo.task_backend.runners import episode_import

    class Service:
        async def commit(self, batch):
            raise EpisodeSourceRevisionConflict("changed")

    monkeypatch.setattr(
        episode_import, "_build_service", lambda ctx, **_kwargs: _async(Service())
    )
    envelope = {"payload": {"snapshot": {
        "preview_id": "p", "expected_revision": 0, "resolutions": {}, "items": [],
    }}}
    with pytest.raises(RuntimeError, match="^EPISODE_IMPORT_REVISION_CONFLICT$"):
        await episode_import._run_episode_import(envelope, SimpleNamespace())


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_source_commit_finishes_before_graph_task_is_enqueued(monkeypatch):
    from novelvideo.task_backend.runners import episode_import

    order = []

    class Repository:
        async def prepare_import(self, batch):
            order.append("prepare")
            return SimpleNamespace(target_revision=5, has_changes=True)

        async def commit_prepared(self, prepared):
            order.append("source-committed")

        async def discard_prepared(self, prepared):
            order.append("discard")

    monkeypatch.setattr(episode_import, "_build_repository", lambda ctx: _async(Repository()))
    result = await episode_import._run_episode_import(
        {
            "payload": {
                "snapshot": {
                    "preview_id": "p",
                    "expected_revision": 4,
                    "resolutions": {"2": "overwrite"},
                    "items": [{"episode_number": 2}, {"episode_number": 3}],
                }
            }
        },
        SimpleNamespace(),
    )

    assert order == ["prepare", "source-committed"]
    assert result["source_committed"] is True
    assert result["graph_index"] == "pending"


@pytest.mark.asyncio
async def test_source_commit_does_not_call_legacy_graph_service(monkeypatch):
    from novelvideo.task_backend.runners import episode_import

    class Repository:
        async def prepare_import(self, batch):
            return SimpleNamespace(target_revision=1, has_changes=False)

        async def commit_prepared(self, prepared):
            return None

        async def discard_prepared(self, prepared):
            return None

    monkeypatch.setattr(episode_import, "_build_repository", lambda ctx: _async(Repository()))
    monkeypatch.setattr(
        episode_import,
        "_build_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy graph path called")),
        raising=False,
    )
    result = await episode_import._run_episode_import(
        {"payload": {"snapshot": {"preview_id": "p", "expected_revision": 0, "resolutions": {}, "items": []}}},
        SimpleNamespace(),
    )
    assert result["target_revision"] == 1
