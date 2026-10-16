import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def canvas_client(tmp_path: Path, monkeypatch):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import script_creation

    state_dir = tmp_path / "project-state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(json.dumps({"creation_mode": "agent_canvas_v1"}))
    user = {"id": "alice", "role": "editor"}

    async def resolve(project, _user, *, required_role="viewer"):
        if project != "p":
            raise HTTPException(404, detail="project not found")
        if required_role == "editor" and _user["role"] != "editor":
            raise HTTPException(403, detail="editor required")
        ctx = SimpleNamespace(
            state_dir=state_dir,
            output_dir=state_dir,
            project_id=project,
            requester_user_id=_user["id"],
            home_node_id="local",
            is_home_node=_user.get("is_home_node", True),
        )
        return SimpleNamespace(state_dir=str(state_dir), ctx=ctx)

    async def make_store(ctx):
        from novelvideo.sqlite_store import SQLiteStore

        store = SQLiteStore("test", str(ctx.state_dir), str(ctx.state_dir))
        await store.initialize()
        return store

    monkeypatch.setattr(script_creation, "resolve_project_scope", resolve)
    monkeypatch.setattr(script_creation, "make_sqlite_store_for_context", make_store)
    app = FastAPI()
    app.include_router(script_creation.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: user
    with TestClient(app) as http:
        yield http, user, state_dir


def create_confirmed_handoff(http, *, suffix, number=1, markdown=None, confirm=True):
    base = "/api/v1/projects/p/script-creation"
    markdown = markdown or f"## {number}-1｜账房 · 夜 · 内\n甲：第 {suffix} 版。"
    created = http.post(base + "/documents", json={
        "kind": "episode_script",
        "title": f"第{number}集-{suffix}",
        "episode_number": number,
        "markdown": markdown,
        "client_mutation_id": f"doc-{suffix}",
    })
    assert created.status_code == 200, created.text
    document = created.json()["data"]
    prepared = http.post(base + "/handoffs/prepare", json={
        "document_id": document["id"],
        "revision_id": document["current_revision_id"],
        "reference_revisions": {},
        "selected_entity_ids": [],
        "update_scope": {"mode": "none"},
        "fact_acknowledgement": {"mode": "unchecked", "reason": "人工确认"},
        "client_mutation_id": f"prepare-{suffix}",
    })
    assert prepared.status_code == 200, prepared.text
    handoff = prepared.json()["data"]
    if not confirm:
        return handoff, markdown
    confirmed = http.post(base + f"/handoffs/{handoff['id']}/confirm", json={
        "expected_source_project_revision": handoff["expected_source_project_revision"],
        "client_mutation_id": f"confirm-{suffix}",
    })
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["data"]["status"] == "completed"
    return confirmed.json()["data"], markdown


def post_canvas(http, handoff_id, *, body=None, project="p"):
    url = f"/api/v1/projects/{project}/script-creation/handoffs/{handoff_id}/canvas"
    return http.post(url) if body is None else http.post(url, json=body)


def test_confirmed_handoff_creates_managed_episode_with_visible_semantic_script(canvas_client):
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import list_episodes
    from novelvideo.freezone.canvas_store import read_canvas

    http, _, state_dir = canvas_client
    handoff, markdown = create_confirmed_handoff(http, suffix="one", number=4)

    response = post_canvas(http, handoff["id"])

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "applied"
    assert result["episode_id"] and result["canvas_id"] and result["operation_id"]
    assert list_episodes(state_dir) == [{
        "episode_id": result["episode_id"],
        "canvas_id": result["canvas_id"],
        "title": "第4集-one",
        "number": 4,
        "revision": 1,
        "status": "applied",
    }]

    canvas = read_canvas(state_dir, result["canvas_id"])
    assert canvas["metadata"]["episode_id"] == result["episode_id"]
    assert len(canvas["nodes"]) == 1
    node = canvas["nodes"][0]
    assert node["type"] == "textAnnotationNode"
    assert node["data"]["content"] == markdown
    assert node["data"]["displayName"] == "第4集-one"
    assert node["data"]["referenceOnly"] is True
    semantic_ref = node["data"]["semanticRef"]
    assert semantic_ref["kind"] == "story_fragment"
    assert semantic_ref["revision"] == 1
    assert ObjectStore(state_dir).read(semantic_ref["object_id"])["payload"] == {
        "title": "第4集-one",
        "text": markdown,
        "episode_id": result["episode_id"],
        "canvas_id": result["canvas_id"],
    }
    assert node["data"]["sourceSnapshot"]["handoff_id"] == handoff["id"]
    assert node["data"]["sourceSnapshot"]["source_hash"] == handoff["source_hash"]


def test_handoff_canvas_replay_is_stable_across_editors_and_later_source_changes(canvas_client):
    http, user, _state_dir = canvas_client
    first, _ = create_confirmed_handoff(http, suffix="first")
    created = post_canvas(http, first["id"])
    assert created.status_code == 200, created.text
    original = created.json()["data"]

    # A later, separately confirmed source for this ordinal must not mutate the
    # first handoff's already-created canvas or make its replay stale.
    create_confirmed_handoff(http, suffix="later")
    user["id"] = "bob"
    replay = post_canvas(http, first["id"])

    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == original


def test_stale_unbridged_handoff_is_rejected_without_claiming_ordinal(canvas_client):
    from novelvideo.creation.workspace import list_episodes

    http, _, state_dir = canvas_client
    stale, _ = create_confirmed_handoff(http, suffix="stale")
    current, _ = create_confirmed_handoff(http, suffix="current")

    response = post_canvas(http, stale["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "handoff_source_changed"
    assert response.json()["detail"]["message"]
    assert not (state_dir / "creation" / "objects.sqlite3").exists()
    assert post_canvas(http, current["id"]).status_code == 200
    assert [episode["number"] for episode in list_episodes(state_dir)] == [1]


def test_another_handoff_cannot_take_over_a_bridged_episode_number(canvas_client):
    from novelvideo.creation.workspace import list_episodes

    http, _, state_dir = canvas_client
    first, _ = create_confirmed_handoff(http, suffix="owner")
    original = post_canvas(http, first["id"])
    assert original.status_code == 200, original.text
    second, _ = create_confirmed_handoff(http, suffix="contender")

    conflict = post_canvas(http, second["id"])

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "episode_number_conflict"
    assert conflict.json()["detail"]["message"]
    episodes = list_episodes(state_dir)
    assert len(episodes) == 1
    assert episodes[0]["episode_id"] == original.json()["data"]["episode_id"]
    assert post_canvas(http, first["id"]).json()["data"] == original.json()["data"]


def test_legacy_project_is_rejected_before_creation_store_initialization(canvas_client):
    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="legacy")
    (state_dir / "project_config.json").write_text(json.dumps({"display_name": "legacy"}))

    response = post_canvas(http, handoff["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legacy_project_readonly"
    assert not (state_dir / "creation" / "objects.sqlite3").exists()


def test_canvas_bridge_requires_editor_role(canvas_client):
    http, user, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="editor-required")
    user["role"] = "viewer"

    response = post_canvas(http, handoff["id"])

    assert response.status_code == 403
    assert not (state_dir / "creation" / "objects.sqlite3").exists()


def test_canvas_bridge_requires_project_home_node(canvas_client):
    http, user, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="home-required")
    user["is_home_node"] = False

    response = post_canvas(http, handoff["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_not_on_this_node"
    assert not (state_dir / "creation" / "objects.sqlite3").exists()


def test_canvas_bridge_rejects_handoff_from_another_project(canvas_client):
    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="project-bound")

    response = post_canvas(http, handoff["id"], project="other")

    assert response.status_code == 404
    assert not (state_dir / "creation" / "objects.sqlite3").exists()


def test_handoff_canvas_rejects_client_supplied_script_content(canvas_client):
    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="body")

    response = post_canvas(http, handoff["id"], body={"markdown": "客户端正文"})

    assert response.status_code == 422
    assert not (state_dir / "creation" / "objects.sqlite3").exists()


def test_unconfirmed_handoff_cannot_initialize_or_claim_a_canvas(canvas_client):
    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="unconfirmed", confirm=False)

    response = post_canvas(http, handoff["id"])

    assert response.status_code == 409
    assert "确认" in response.json()["detail"]["message"]
    assert not (state_dir / "creation" / "objects.sqlite3").exists()


def test_projection_interruption_retries_the_same_bridge_without_duplicates(canvas_client, monkeypatch):
    from novelvideo.creation import projection
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import list_episodes

    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(http, suffix="recover")
    original_apply = projection.ProjectionService.apply
    calls = 0

    def interrupt_once(service, operation_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("projection interrupted")
        return original_apply(service, operation_id)

    monkeypatch.setattr(projection.ProjectionService, "apply", interrupt_once)
    with pytest.raises(OSError, match="projection interrupted"):
        post_canvas(http, handoff["id"])
    pending = list_episodes(state_dir)
    assert len(pending) == 1 and pending[0]["status"] == "running"

    monkeypatch.setattr(projection.ProjectionService, "apply", original_apply)
    recovered = post_canvas(http, handoff["id"])

    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["data"]["status"] == "applied"
    assert recovered.json()["data"]["episode_id"] == pending[0]["episode_id"]
    assert recovered.json()["data"]["canvas_id"] == pending[0]["canvas_id"]
    assert len(list_episodes(state_dir)) == 1
    with ObjectStore(state_dir).transaction() as conn:
        assert conn.execute("SELECT count(*) FROM script_handoff_bridges").fetchone()[0] == 1


def test_existing_managed_episode_number_is_never_taken_over(canvas_client):
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace, list_episodes

    http, _, state_dir = canvas_client
    objects = ObjectStore(state_dir)
    manual = EpisodeWorkspace(objects, project_id="p").create(
        "alice", EpisodeCreate(request_id="manual-episode", title="人工创建的第一集"),
    )
    ProjectionService(objects).apply(manual.operation_id)
    handoff, _ = create_confirmed_handoff(http, suffix="same-number")

    response = post_canvas(http, handoff["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "episode_number_conflict"
    episodes = list_episodes(state_dir)
    assert len(episodes) == 1
    assert episodes[0]["episode_id"] == manual.object_refs[0]["object_id"]
    assert episodes[0]["title"] == "人工创建的第一集"


def test_imported_episode_script_can_be_confirmed_and_bridged(canvas_client):
    import asyncio

    from novelvideo.creation.workspace import list_episodes
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    http, _, state_dir = canvas_client
    source_markdown = "第1集\n1-1｜账房 · 夜 · 内\n甲：账簿在哪里？"

    async def seed_source():
        sqlite = SQLiteStore("p", str(state_dir), str(state_dir))
        await sqlite.initialize()
        try:
            await EpisodeSourceStore(sqlite).upsert_sources(
                [build_episode_candidate("E01.md", source_markdown)], expected_revision=0,
            )
        finally:
            await sqlite.close()

    asyncio.run(seed_source())
    base = "/api/v1/projects/p/script-creation"
    imported_response = http.post(base + "/imports", json={"episode_number": 1})
    assert imported_response.status_code == 200, imported_response.text
    imported = imported_response.json()["data"]
    assert imported["source_origin"]["source_episode_number"] == 1

    prepared_response = http.post(base + "/handoffs/prepare", json={
        "document_id": imported["id"],
        "revision_id": imported["current_revision_id"],
        "reference_revisions": {},
        "selected_entity_ids": [],
        "update_scope": {"mode": "none"},
        "fact_acknowledgement": {"mode": "unchecked", "reason": "检查导入剧本"},
        "client_mutation_id": "imported-handoff-prepare",
    })
    assert prepared_response.status_code == 200, prepared_response.text
    prepared = prepared_response.json()["data"]
    confirmed_response = http.post(base + f"/handoffs/{prepared['id']}/confirm", json={
        "expected_source_project_revision": prepared["expected_source_project_revision"],
        "client_mutation_id": "imported-handoff-confirm",
    })
    assert confirmed_response.status_code == 202, confirmed_response.text
    confirmed = confirmed_response.json()["data"]

    bridged = post_canvas(http, confirmed["id"])

    assert bridged.status_code == 200, bridged.text
    data = bridged.json()["data"]
    assert data["status"] == "applied"
    assert data["number"] == 1
    assert list_episodes(state_dir)[0]["episode_id"] == data["episode_id"]
