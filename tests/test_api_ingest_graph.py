from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from novelvideo.api.ingest_graph import GraphPatch, build_structured_graph, read_graph, update_graph
from novelvideo.models import NovelEpisode, NovelProp


@pytest.fixture
def store(tmp_path):
    return SimpleNamespace(
        state_dir=tmp_path,
        list_characters=AsyncMock(return_value=[]),
        list_scenes=AsyncMock(return_value=[]),
        list_props=AsyncMock(return_value=[NovelProp(name="信", owner="小明")]),
        list_episodes=AsyncMock(return_value=[NovelEpisode(number=1, title="启程", raw_content="第一集\n1-1 客厅 日 内\n人物：小明、小红\n小明：出发。")]),
    )


@pytest.mark.asyncio
async def test_source_only_graph_has_real_entities_and_complete_neighbors(store):
    graph = await build_structured_graph(store, sources=[])
    assert {node["label"] for node in graph["nodes"]} >= {"小明", "小红", "客厅", "信", "启程"}
    assert graph["truncated"] is False
    assert graph["total_edges"] >= 5
    assert all(node["degree"] == sum(node["id"] in (edge["source"], edge["target"]) for edge in graph["edges"]) for node in graph["nodes"])


@pytest.mark.asyncio
async def test_annotations_save_reload_conflict_and_atomic_validation(store):
    base = await build_structured_graph(store, sources=[])
    graph = read_graph(store.state_dir, base)
    node_id = graph["nodes"][0]["id"]
    patch = GraphPatch(revision=graph["revision"], node_updates=[{"id": node_id, "label": "新标签", "properties": {"review": "已核对"}}])
    saved = update_graph(store.state_dir, base, patch)
    assert saved["revision"] != graph["revision"]
    assert read_graph(store.state_dir, base) == saved
    assert saved["nodes"][0]["properties"]["review"] == "已核对"
    with pytest.raises(HTTPException) as conflict:
        update_graph(store.state_dir, base, patch)
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        update_graph(store.state_dir, base, GraphPatch(revision=saved["revision"], node_updates=[{"id": node_id, "label": "不应保存"}], edge_updates=[{"id": saved["edges"][0]["id"], "target": "missing"}]))
    assert invalid.value.status_code == 422
    assert read_graph(store.state_dir, base) == saved


@pytest.mark.asyncio
async def test_source_changes_invalidate_revision(store):
    base = await build_structured_graph(store, sources=[])
    old = read_graph(store.state_dir, base)
    base["nodes"][0]["properties"]["source_revision"] = 2
    with pytest.raises(HTTPException) as conflict:
        update_graph(store.state_dir, base, GraphPatch(revision=old["revision"]))
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_edge_edit_is_persisted_and_degrees_are_recomputed(store):
    base = await build_structured_graph(store, sources=[])
    current = read_graph(store.state_dir, base)
    edge = current["edges"][0]
    saved = update_graph(store.state_dir, base, GraphPatch(revision=current["revision"], edge_updates=[{"id": edge["id"], "target": "Prop:信", "relation": "提及", "properties": {"备注": "人工检查"}}]))
    updated = next(item for item in saved["edges"] if item["id"] == edge["id"])
    assert updated["target"] == "Prop:信"
    assert updated["relation"] == "提及"
    assert updated["properties"]["备注"] == "人工检查"
    assert read_graph(store.state_dir, base) == saved
    assert all(node["degree"] == sum(node["id"] in (item["source"], item["target"]) for item in saved["edges"]) for node in saved["nodes"])


@pytest.mark.asyncio
async def test_source_documents_work_without_any_formal_episode(store):
    store.list_episodes.return_value = []
    source = SimpleNamespace(episode_number=60, title="结局", content="第60集\n60-1 医院 夜 内\n人物：医生、病人\n医生：没事了。", source_revision=7)
    graph = await build_structured_graph(store, sources=[source])
    episode = next(node for node in graph["nodes"] if node["id"] == "Episode:60")
    assert episode["properties"]["source_revision"] == 7
    assert {node["label"] for node in graph["nodes"]} >= {"医生", "病人", "医院"}


@pytest.mark.asyncio
async def test_api_read_permissions_write_permissions_and_roundtrip(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import ingest

    app = FastAPI()
    app.include_router(ingest.router)
    app.dependency_overrides[ingest.get_sqlite_store] = lambda: store
    app.dependency_overrides[ingest.get_api_user] = lambda: {"id": "reader"}
    role = "viewer"

    async def resolve(project, user, required_role):
        if required_role == "editor" and role == "viewer":
            raise HTTPException(403, "Forbidden")
        return SimpleNamespace(state_dir=store.state_dir, ctx=SimpleNamespace(effective_role=role))

    monkeypatch.setattr(ingest, "resolve_project_scope", resolve)
    monkeypatch.setattr(ingest, "knowledge_pipeline_from_state_dir", lambda _: "structured_v1")
    original_builder = build_structured_graph

    async def build(_):
        return await original_builder(store, sources=[])

    monkeypatch.setattr(ingest, "build_structured_graph", build)
    with TestClient(app) as client:
        graph = client.get("/projects/demo/ingest/graph").json()["data"]
        assert graph["editable"] is False
        patch = {"revision": graph["revision"], "node_updates": [{"id": graph["nodes"][0]["id"], "label": "注释标题"}]}
        assert client.patch("/projects/demo/ingest/graph", json=patch).status_code == 403
        role = "editor"
        saved = client.patch("/projects/demo/ingest/graph", json=patch)
        assert saved.status_code == 200
        assert saved.json()["data"]["nodes"][0]["label"] == "注释标题"
        assert client.get("/projects/demo/ingest/graph").json()["data"] == saved.json()["data"]
        assert client.patch("/projects/demo/ingest/graph", json=patch).status_code == 409
        patch["revision"] = saved.json()["data"]["revision"]
        patch["node_updates"][0]["type"] = "forbidden"
        assert client.patch("/projects/demo/ingest/graph", json=patch).status_code == 422


@pytest.mark.asyncio
async def test_concurrent_saves_have_one_winner_and_null_deletes_property(store):
    import asyncio

    base = await build_structured_graph(store, sources=[])
    graph = read_graph(store.state_dir, base)
    node = graph["nodes"][0]
    patch = GraphPatch(revision=graph["revision"], node_updates=[{"id": node["id"], "properties": {"name": None}}])
    results = await asyncio.gather(*(asyncio.to_thread(update_graph, store.state_dir, base, patch) for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    reloaded = read_graph(store.state_dir, base)
    assert "name" not in reloaded["nodes"][0]["properties"]


@pytest.mark.asyncio
async def test_real_sqlite_source_storage(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore("graph-test", output_dir=str(tmp_path / "output"), state_dir=str(tmp_path / "state"))
    try:
        await store.initialize()
        db = await store._ensure_db()
        await db.execute("INSERT INTO episode_sources (episode_number,title,raw_content,content_hash,source_filename,source_revision,imported_at,updated_at) VALUES (1, '源剧本', '第一集\n1-1 天台 日 外\n人物：小王\n小王：你好。', 'hash', 'demo.txt', 1, '', '')")
        await db.commit()
        graph = await build_structured_graph(store)
        assert {node["label"] for node in graph["nodes"]} >= {"源剧本", "小王", "天台"}
    finally:
        await store.close()
