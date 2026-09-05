from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_episode_graph_runner_reads_revision_snapshot_and_activates_candidate(monkeypatch):
    from novelvideo.task_backend.runners import episode_graph

    events = []

    class Repository:
        async def current_revision(self):
            return 7

        async def list_sources(self):
            return [
                SimpleNamespace(episode_number=number, title=f"E{number}", content=f"content {number}")
                for number in range(2, 31)
            ]

        async def delete_graph_outbox(self, revision):
            events.append(("outbox-deleted", revision))

    class Candidate:
        shadow = object()

        async def commit_embedding_binding(self):
            events.append(("binding-committed", None))

        async def close(self):
            return None

    class Service:
        async def build(self, sources, *, target_revision, on_group_event):
            assert len(sources) == 29
            assert {item.source_revision for item in sources} == {7}
            for index in range(6):
                group = SimpleNamespace(key=f"g{index}")
                on_group_event("started", group)
                on_group_event("completed", group)
            return SimpleNamespace(target_revision=7, group_count=6, candidate=Candidate(), graph=SimpleNamespace(entities=[], events=[], relations=[]))

    class Activation:
        async def activate_shadow(self, shadow):
            events.append(("activate", shadow))
            return object()

        async def finalize_activation(self, token):
            events.append(("finalize", token))

    monkeypatch.setattr(episode_graph, "_build_repository", lambda ctx: _async(Repository()))
    monkeypatch.setattr(episode_graph, "_build_graph_service", lambda ctx: _async(Service()))
    monkeypatch.setattr(episode_graph, "_build_activation_graph", lambda ctx: Activation())
    monkeypatch.setattr(episode_graph, "get_task_manager", lambda: SimpleNamespace(update_progress_for_project=lambda *a, **kw: events.append(("progress", kw))))

    result = await episode_graph._run_episode_graph_index(
        {"__run_task_id": "t", "payload": {"target_revision": 7, "changed_episode_numbers": list(range(2, 31))}},
        SimpleNamespace(),
    )
    assert result["group_count"] == 6
    assert len([item for item in events if item[0] == "progress"]) >= 12
    assert [item[0] for item in events[-4:]] == [
        "activate",
        "binding-committed",
        "finalize",
        "outbox-deleted",
    ]


@pytest.mark.asyncio
async def test_episode_graph_runner_rejects_stale_revision(monkeypatch):
    from novelvideo.task_backend.runners import episode_graph

    class Repository:
        async def current_revision(self):
            return 8

    monkeypatch.setattr(episode_graph, "_build_repository", lambda ctx: _async(Repository()))
    with pytest.raises(RuntimeError, match="EPISODE_GRAPH_REVISION_STALE"):
        await episode_graph._run_episode_graph_index(
            {"payload": {"target_revision": 7}}, SimpleNamespace()
        )


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_cognee_candidate_writes_datapoints_instead_of_node_tuples():
    from novelvideo.episode_graph.models import GraphEntity
    from novelvideo.task_backend.runners.episode_graph import CogneeGraphCandidate

    captured = []

    class Graph:
        async def add_nodes(self, nodes):
            captured.extend(nodes)

    candidate = CogneeGraphCandidate(
        shadow=object(),
        store=SimpleNamespace(),
        graph=Graph(),
        vector=SimpleNamespace(),
    )
    await candidate.upsert_entities([
        GraphEntity(
            name="周禾",
            kind="character",
            attributes={"description": "幸存者"},
            source_episodes={1},
        )
    ])

    assert len(captured) == 1
    assert captured[0].name == "周禾"
    assert captured[0].type == "EpisodeGraphPoint"
    assert captured[0].kind == "character"
    assert captured[0].attributes_json == '{"description": "幸存者"}'


@pytest.mark.asyncio
async def test_inline_backend_drains_durable_graph_outbox_on_its_stable_loop(monkeypatch):
    from novelvideo.ports.local import tasks as local_tasks
    import novelvideo.episode_source_store as source_store_module

    calls = []

    class Repository:
        async def list_graph_outbox(self):
            return [
                {"target_revision": 7, "changed_episode_numbers": [2]},
                {"target_revision": 8, "changed_episode_numbers": [3]},
            ]

        async def delete_graph_outbox(self, revision):
            calls.append(("deleted", revision))

    async def make_store(ctx):
        return object()

    monkeypatch.setattr("novelvideo.api.deps.make_sqlite_store_for_context", make_store)
    backend = local_tasks.InlineTaskBackend()
    monkeypatch.setattr(
        source_store_module,
        "EpisodeSourceStore",
        lambda _store: Repository(),
    )

    async def enqueue(ctx, **kwargs):
        calls.append(("enqueued", kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(backend, "enqueue_project_task", enqueue)
    await backend._drain_episode_graph_outbox(SimpleNamespace())
    assert [call[1]["scope"] for call in calls if call[0] == "enqueued"] == ["revision:8"]
    assert ("deleted", 7) in calls
    assert ("deleted", 8) not in calls


@pytest.mark.asyncio
async def test_episode_graph_failure_keeps_outbox(monkeypatch):
    from novelvideo.task_backend.runners import episode_graph

    deleted = []

    class Repository:
        async def current_revision(self): return 7
        async def list_sources(self):
            return [SimpleNamespace(episode_number=7, title="E7", content="content")]
        async def delete_graph_outbox(self, revision): deleted.append(revision)

    class Service:
        async def build(self, *args, **kwargs): raise RuntimeError("graph failed")

    monkeypatch.setattr(episode_graph, "_build_repository", lambda ctx: _async(Repository()))
    monkeypatch.setattr(episode_graph, "_build_graph_service", lambda ctx: _async(Service()))
    monkeypatch.setattr(episode_graph, "get_task_manager", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="graph failed"):
        await episode_graph._run_episode_graph_index(
            {"scope": "revision:7", "payload": {"target_revision": 7}}, SimpleNamespace()
        )
    assert deleted == []
