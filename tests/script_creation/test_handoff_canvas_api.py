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
    assert len(canvas["nodes"]) == 3
    node = next(item for item in canvas["nodes"] if item["type"] == "textAnnotationNode")
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
    assert node["data"]["formalScriptRef"] == {
        "kind": "episode_script", "project_id": "p", "document_id": handoff["document_id"],
        "revision_id": handoff["revision_id"], "episode_number": 4, "title": "第4集-one",
    }
    screenplay = next(item for item in canvas["nodes"] if item["type"] == "screenplayNode")
    director = next(item for item in canvas["nodes"] if item["type"] == "directorWorkspaceNode")
    assert screenplay["data"]["episodeNumber"] == 4
    assert screenplay["data"]["documentId"] == handoff["document_id"]
    assert screenplay["data"]["documentRevisionId"] == handoff["revision_id"]
    assert director["data"]["episodeNumber"] == 4
    assert director["data"]["sourceDocumentId"] == handoff["document_id"]


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


def test_agent_script_handoff_requires_current_independent_review(canvas_client):
    import asyncio

    from novelvideo.creation.agent_script_documents import AgentScriptDocuments
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.freezone.canvas_store import read_canvas

    http, _, state_dir = canvas_client
    documents = DocumentStore(state_dir / "data.db")
    service = AgentScriptDocuments(documents)

    async def seed(review=False):
        draft = await service.create_draft(
            request_id="agent-script-draft", project_id="p", canvas_id="concept-canvas",
            delegation_id="writer-delegation",
            writer={"adoption_id": "writer", "revision": 1, "name": "主笔"},
            episode_number=2, title="第二集·雨夜来信", markdown="## 2-1｜站台·夜·外\n女主收到来信。",
        )
        if review:
            await service.record_review(
                request_id="agent-script-review", project_id="p", canvas_id="concept-canvas",
                delegation_id="reviewer-delegation",
                reviewer={"adoption_id": "reviewer", "revision": 2, "name": "剧本编辑"},
                document_id=draft["document_ref"]["document_id"],
                revision_id=draft["document_ref"]["revision_id"],
                report={"conclusion": "passed", "summary": "人物动机与场景因果清楚。", "issues": []},
            )
        return draft

    base = "/api/v1/projects/p/script-creation"
    draft = asyncio.run(seed())
    ref = draft["document_ref"]
    handoff_request = {
        "document_id": ref["document_id"], "revision_id": ref["revision_id"],
        "reference_revisions": {}, "selected_entity_ids": [], "update_scope": {"mode": "none"},
        "fact_acknowledgement": {"mode": "unchecked", "reason": "人工确认"},
        "client_mutation_id": "agent-prepare-unreviewed",
    }
    blocked = http.post(base + "/handoffs/prepare", json=handoff_request)
    assert blocked.status_code == 422
    assert "review" in blocked.text.lower() or "审阅" in blocked.text

    draft = asyncio.run(seed(review=True))
    ref = draft["document_ref"]
    handoff_request = {**handoff_request, "document_id": ref["document_id"],
                       "revision_id": ref["revision_id"], "client_mutation_id": "agent-prepare-reviewed"}
    prepared = http.post(base + "/handoffs/prepare", json=handoff_request)
    assert prepared.status_code == 200, prepared.text
    handoff = prepared.json()["data"]
    stale_revision = http.put(base + f"/documents/{ref['document_id']}", json={
        "base_revision_id": ref["revision_id"], "markdown": "## 2-1｜站台·夜·外\n她决定寄出回信。",
        "client_mutation_id": "agent-script-edit-after-review",
    })
    assert stale_revision.status_code == 200
    rejected = http.post(base + f"/handoffs/{handoff['id']}/confirm", json={
        "expected_source_project_revision": handoff["expected_source_project_revision"],
        "client_mutation_id": "agent-confirm-after-edit",
    })
    assert rejected.status_code == 409
    assert "stale" in rejected.text.lower() or "revision changed" in rejected.text.lower()

    # A fresh independently reviewed version can be handed off, and its exact
    # episode identity is used by both screenplay and director workspace nodes.
    updated = stale_revision.json()["data"]
    async def review_updated():
        await service.record_review(
            request_id="agent-script-review-updated", project_id="p", canvas_id="concept-canvas",
            delegation_id="reviewer-delegation-2",
            reviewer={"adoption_id": "reviewer-2", "revision": 1, "name": "剧本编辑"},
            document_id=ref["document_id"], revision_id=updated["current_revision_id"],
            report={"conclusion": "passed", "summary": "改稿已复核。", "issues": []},
        )
    asyncio.run(review_updated())
    fresh_request = {**handoff_request, "revision_id": updated["current_revision_id"],
                     "client_mutation_id": "agent-prepare-reviewed-updated"}
    fresh = http.post(base + "/handoffs/prepare", json=fresh_request)
    assert fresh.status_code == 200, fresh.text
    fresh_handoff = fresh.json()["data"]
    confirmed = http.post(base + f"/handoffs/{fresh_handoff['id']}/confirm", json={
        "expected_source_project_revision": fresh_handoff["expected_source_project_revision"],
        "client_mutation_id": "agent-confirm-reviewed-updated",
    })
    assert confirmed.status_code == 202, confirmed.text
    canvas_result = post_canvas(http, fresh_handoff["id"])
    assert canvas_result.status_code == 200, canvas_result.text
    canvas = read_canvas(state_dir, canvas_result.json()["data"]["canvas_id"])
    assert {item["type"] for item in canvas["nodes"]} >= {
        "textAnnotationNode", "screenplayNode", "directorWorkspaceNode",
    }


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


async def _create_bound_proposal(state_dir, handoff, *, suffix):
    from novelvideo.script_creation.proposals import ProposalService
    from novelvideo.script_creation.store import DocumentStore

    documents = DocumentStore(state_dir / "data.db")
    await documents.initialize()
    document = await documents.get(handoff["document_id"])
    block = document.revision.blocks[0]
    before = block.markdown
    after = before.replace("第 initial 版", f"第 {suffix} 版")
    assert after != before
    proposal = await ProposalService(documents).create(
        document_id=document.id,
        base_revision_id=document.current_revision_id,
        block_id=block.id,
        start=0,
        end=len(before),
        before=before,
        after=after,
        reason="用户接受剧本修改提案",
        round_id=f"round-{suffix}",
        client_mutation_id=f"proposal-{suffix}",
    )
    return documents, document, proposal, after


async def _accept_bound_proposal(http, handoff, state_dir, *, suffix):
    documents, document, proposal, after = await _create_bound_proposal(
        state_dir, handoff, suffix=suffix
    )
    response = http.post(
        "/api/v1/projects/p/script-creation/proposals/accept",
        json={
            "proposal_ids": [proposal["id"]],
            "base_revision_id": document.current_revision_id,
            "client_mutation_id": f"accept-{suffix}",
        },
    )
    return documents, document, proposal, after, response


@pytest.mark.asyncio
async def test_accepting_bound_script_proposal_updates_same_source_without_new_episode(canvas_client):
    from novelvideo.creation.workspace import list_episodes
    from novelvideo.creation.production_progress import ProductionProgress
    from novelvideo.script_creation.proposals import ProposalService

    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(
        http,
        suffix="initial",
        markdown="## 1-1｜账房 · 夜 · 内\n甲：第 initial 版。",
    )
    original_canvas = post_canvas(http, handoff["id"])
    assert original_canvas.status_code == 200, original_canvas.text
    original_episode = original_canvas.json()["data"]
    progress_output = state_dir / "episodes" / "001" / "script.md"
    progress_output.parent.mkdir(parents=True, exist_ok=True)
    progress_output.write_text("已审核剧本", encoding="utf-8")
    progress = ProductionProgress(state_dir, output_root=state_dir)
    ready = progress.complete(
        "001", "screenplay", "reviewed-script", {"script": "episodes/001/script.md"}
    )
    progress.approve("001", "screenplay", ready["stages"]["screenplay"]["approval_hash"])

    documents, original_document, _proposal, after, response = await _accept_bound_proposal(
        http, handoff, state_dir, suffix="accepted"
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["current_revision_id"] != original_document.current_revision_id
    assert result["revision"]["markdown"] == after
    assert result["adopted_revision_id"] == result["current_revision_id"]
    assert result["source_sync"]["status"] == "synced"
    assert result["source_sync"]["handoff_id"] != handoff["id"]
    assert result["progress_invalidation"]["status"] == "invalidated"
    invalidated = progress.get("001", create=False)["stages"]["screenplay"]
    assert invalidated["status"] == "pending"
    assert invalidated["error"] == "script_document_updated"
    assert invalidated["approved_hash"] is None
    artifact = result["source_sync"]["artifact"]
    assert artifact["path"].endswith(f"/{result['current_revision_id']}.md")
    assert (state_dir / artifact["path"]).read_text(encoding="utf-8") == after
    current = await documents.get(original_document.id)
    assert current.current_revision_id == result["current_revision_id"]
    assert current.adopted_revision_id == result["current_revision_id"]
    assert [item["status"] for item in await ProposalService(documents).list(
        original_document.id
    )] == ["adopted"]
    episodes = list_episodes(state_dir)
    assert len(episodes) == 1
    assert episodes[0]["episode_id"] == original_episode["episode_id"]
    assert episodes[0]["canvas_id"] == original_episode["canvas_id"]
    handoffs = http.get(
        "/api/v1/projects/p/script-creation/handoffs?episode_number=1"
    ).json()["data"]
    assert len(handoffs) == 2


@pytest.mark.asyncio
async def test_accepting_unbound_script_proposal_does_not_invalidate_project_progress(canvas_client):
    from novelvideo.creation.production_progress import ProductionProgress

    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(
        http,
        suffix="unbound",
        markdown="## 1-1｜账房 · 夜 · 内\n甲：第 initial 版。",
    )
    progress_output = state_dir / "episodes" / "001" / "script.md"
    progress_output.parent.mkdir(parents=True, exist_ok=True)
    progress_output.write_text("项目已有审阅记录", encoding="utf-8")
    progress = ProductionProgress(state_dir, output_root=state_dir)
    ready = progress.complete(
        "001", "screenplay", "reviewed-script", {"script": "episodes/001/script.md"}
    )
    progress.approve("001", "screenplay", ready["stages"]["screenplay"]["approval_hash"])

    _documents, _document, _proposal, _after, response = await _accept_bound_proposal(
        http, handoff, state_dir, suffix="unbound-accepted"
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["source_sync"]["status"] == "synced"
    assert "progress_invalidation" not in result
    unchanged = progress.get("001", create=False)["stages"]["screenplay"]
    assert unchanged["status"] == "ready"
    assert unchanged["review_resolution"] == "approved"


@pytest.mark.asyncio
async def test_direct_save_of_bound_script_syncs_source_and_invalidates_progress(canvas_client):
    from novelvideo.creation.production_progress import ProductionProgress
    from novelvideo.creation.workspace import list_episodes

    http, _, state_dir = canvas_client
    handoff, before = create_confirmed_handoff(
        http,
        suffix="direct-save",
        markdown="## 1-1｜账房 · 夜 · 内\n甲：第 direct-save 版。",
    )
    canvas_response = post_canvas(http, handoff["id"])
    assert canvas_response.status_code == 200, canvas_response.text
    original_episode = canvas_response.json()["data"]
    progress_output = state_dir / "episodes" / "001" / "script.md"
    progress_output.parent.mkdir(parents=True, exist_ok=True)
    progress_output.write_text("已批准的前一版剧本", encoding="utf-8")
    progress = ProductionProgress(state_dir, output_root=state_dir)
    ready = progress.complete(
        "001", "screenplay", "previous-script", {"script": "episodes/001/script.md"}
    )
    progress.approve("001", "screenplay", ready["stages"]["screenplay"]["approval_hash"])
    after = before.replace("第 direct-save 版", "直接保存的新版本")

    response = http.put(
        f"/api/v1/projects/p/script-creation/documents/{handoff['document_id']}",
        json={
            "base_revision_id": handoff["revision_id"],
            "markdown": after,
            "client_mutation_id": "direct-save-bound-script",
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["revision"]["markdown"] == after
    assert result["adopted_revision_id"] == result["current_revision_id"]
    assert result["source_sync"]["status"] == "synced"
    assert result["source_sync"]["artifact"]["status"] == "ready"
    assert result["progress_invalidation"]["status"] == "invalidated"
    stage = progress.get("001", create=False)["stages"]["screenplay"]
    assert stage["status"] == "pending"
    assert stage["error"] == "script_document_updated"
    episodes = list_episodes(state_dir)
    assert len(episodes) == 1
    assert episodes[0]["episode_id"] == original_episode["episode_id"]

    progress_output.write_text(after, encoding="utf-8")
    recompleted = progress.complete(
        "001", "screenplay", "current-script", {"script": "episodes/001/script.md"}
    )
    progress.approve(
        "001", "screenplay", recompleted["stages"]["screenplay"]["approval_hash"]
    )
    replay = http.put(
        f"/api/v1/projects/p/script-creation/documents/{handoff['document_id']}",
        json={
            "base_revision_id": handoff["revision_id"],
            "markdown": after,
            "client_mutation_id": "direct-save-bound-script",
        },
    )
    assert replay.status_code == 200, replay.text
    assert "progress_invalidation" not in replay.json()["data"]
    assert progress.get("001", create=False)["stages"]["screenplay"]["status"] == "ready"


@pytest.mark.asyncio
async def test_restoring_bound_script_revision_reuses_current_source_sync(canvas_client):
    from novelvideo.creation.workspace import list_episodes

    http, _, state_dir = canvas_client
    initial, original_text = create_confirmed_handoff(
        http,
        suffix="restore",
        markdown="## 1-1｜账房 · 夜 · 内\n甲：初始文本。",
    )
    canvas_response = post_canvas(http, initial["id"])
    assert canvas_response.status_code == 200, canvas_response.text
    original_episode = canvas_response.json()["data"]
    changed = http.put(
        f"/api/v1/projects/p/script-creation/documents/{initial['document_id']}",
        json={
            "base_revision_id": initial["revision_id"],
            "markdown": "## 1-1｜账房 · 夜 · 内\n甲：后续改稿。",
            "client_mutation_id": "restore-bound-save",
        },
    )
    assert changed.status_code == 200, changed.text

    restored = http.post(
        f"/api/v1/projects/p/script-creation/documents/{initial['document_id']}/restore",
        json={
            "revision_id": initial["revision_id"],
            "base_revision_id": changed.json()["data"]["current_revision_id"],
            "client_mutation_id": "restore-bound-original",
        },
    )

    assert restored.status_code == 200, restored.text
    result = restored.json()["data"]
    assert result["revision"]["markdown"] == original_text
    assert result["source_sync"]["status"] == "synced"
    assert result["adopted_revision_id"] == result["current_revision_id"]
    assert len(list_episodes(state_dir)) == 1
    assert list_episodes(state_dir)[0]["episode_id"] == original_episode["episode_id"]


@pytest.mark.asyncio
async def test_agent_direct_save_waits_for_current_independent_review(canvas_client):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.script_creation.handoff import HandoffService
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.sqlite_store import SQLiteStore

    http, _, state_dir = canvas_client
    sqlite = SQLiteStore("test", str(state_dir), str(state_dir))
    await sqlite.initialize()
    documents = DocumentStore(sqlite.db_path)
    await documents.initialize()
    script = await documents.create(
        kind="episode_script",
        title="Agent 草稿",
        episode_number=1,
        markdown="## 1-1｜账房 · 夜 · 内\n甲：已确认绑定的 Agent 草稿。",
        source_origin={
            "kind": "agent_script_draft",
            "project_id": "p",
            "canvas_id": "concept-canvas",
            "delegation_id": "writer-task",
            "member_id": "writer",
            "member_revision": 1,
            "source_ref": None,
        },
        client_mutation_id="agent-direct-save-document",
    )
    service = HandoffService(
        documents, EpisodeSourceStore(sqlite), project_id="p",
    )
    await service.initialize()
    prepared = await service.prepare(
        document_id=script.id,
        revision_id=script.current_revision_id,
        reference_revisions={},
        selected_entity_ids=[],
        update_scope={"mode": "none"},
        fact_acknowledgement={"mode": "unchecked", "reason": "初始交接确认"},
        client_mutation_id="agent-direct-save-initial-prepare",
    )
    initial = await service.confirm(
        prepared["id"],
        expected_source_project_revision=prepared["expected_source_project_revision"],
        client_mutation_id="agent-direct-save-initial-confirm",
    )
    await sqlite.close()

    new_text = "## 1-1｜账房 · 夜 · 内\n甲：Agent 新版本尚未复核。"
    response = http.put(
        f"/api/v1/projects/p/script-creation/documents/{script.id}",
        json={
            "base_revision_id": script.current_revision_id,
            "markdown": new_text,
            "client_mutation_id": "agent-direct-save-unreviewed",
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["revision"]["markdown"] == new_text
    assert result["source_sync"]["status"] == "review_required"
    assert result["source_sync"]["code"] == "handoff_review_required"
    sqlite_check = SQLiteStore("test", str(state_dir), str(state_dir))
    await sqlite_check.initialize()
    try:
        source = (await EpisodeSourceStore(sqlite_check).list_sources())[0]
    finally:
        await sqlite_check.close()
    assert source.content == initial["snapshot"]["markdown"]
    assert len(http.get(
        "/api/v1/projects/p/script-creation/handoffs?episode_number=1"
    ).json()["data"]) == 1


@pytest.mark.asyncio
async def test_direct_save_conflict_preserves_manual_episode_source(canvas_client):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import EpisodeCandidate, content_sha256
    from novelvideo.sqlite_store import SQLiteStore

    http, _, state_dir = canvas_client
    handoff, _before = create_confirmed_handoff(
        http,
        suffix="direct-save-conflict",
        markdown="## 1-1｜账房 · 夜 · 内\n甲：已交接版本。",
    )
    sqlite = SQLiteStore("test", str(state_dir), str(state_dir))
    await sqlite.initialize()
    manual_text = "## 1-1｜账房 · 夜 · 内\n甲：Episode Source 的手工修订。"
    await EpisodeSourceStore(sqlite).upsert_sources(
        [EpisodeCandidate(
            source_filename="manual-episode-source.md",
            content=manual_text,
            episode_number=1,
            number_source="manual",
            title="手工来源",
            content_hash=content_sha256(manual_text),
        )],
        expected_revision=handoff["source_project_revision"],
    )
    await sqlite.close()

    accepted_text = "## 1-1｜账房 · 夜 · 内\n甲：直接保存的正式版本。"
    response = http.put(
        f"/api/v1/projects/p/script-creation/documents/{handoff['document_id']}",
        json={
            "base_revision_id": handoff["revision_id"],
            "markdown": accepted_text,
            "client_mutation_id": "direct-save-manual-conflict",
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["revision"]["markdown"] == accepted_text
    assert result["source_sync"]["status"] == "conflict"
    assert result["source_sync"]["code"] == "episode_source_changed"
    sqlite_check = SQLiteStore("test", str(state_dir), str(state_dir))
    await sqlite_check.initialize()
    try:
        source = (await EpisodeSourceStore(sqlite_check).list_sources())[0]
    finally:
        await sqlite_check.close()
    assert source.content == manual_text
    assert len(http.get(
        "/api/v1/projects/p/script-creation/handoffs?episode_number=1"
    ).json()["data"]) == 1


@pytest.mark.asyncio
async def test_manual_episode_source_conflict_keeps_accepted_proposal_and_source(canvas_client):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import EpisodeCandidate, content_sha256
    from novelvideo.script_creation.proposals import ProposalService
    from novelvideo.sqlite_store import SQLiteStore

    http, _, state_dir = canvas_client
    handoff, _ = create_confirmed_handoff(
        http,
        suffix="initial",
        markdown="## 1-1｜账房 · 夜 · 内\n甲：第 initial 版。",
    )
    sqlite = SQLiteStore("test", str(state_dir), str(state_dir))
    await sqlite.initialize()
    sources = EpisodeSourceStore(sqlite)
    manual_text = "## 1-1｜账房 · 夜 · 内\n甲：这是人工修改，必须保留。"
    await sources.upsert_sources(
        [EpisodeCandidate(
            source_filename="manual-edit.md",
            content=manual_text,
            episode_number=1,
            number_source="manual",
            title="人工版本",
            content_hash=content_sha256(manual_text),
        )],
        expected_revision=handoff["source_project_revision"],
    )
    await sqlite.close()

    documents, original_document, _proposal, accepted_text, response = await _accept_bound_proposal(
        http, handoff, state_dir, suffix="kept"
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["current_revision_id"] != original_document.current_revision_id
    assert result["revision"]["markdown"] == accepted_text
    assert result["source_sync"]["status"] == "conflict"
    assert result["source_sync"]["code"] == "episode_source_changed"
    check_sqlite = SQLiteStore("test", str(state_dir), str(state_dir))
    await check_sqlite.initialize()
    try:
        current_source = (await EpisodeSourceStore(check_sqlite).list_sources())[0]
    finally:
        await check_sqlite.close()
    assert current_source.content == manual_text
    assert [item["status"] for item in await ProposalService(documents).list(
        original_document.id
    )] == ["adopted"]
    handoffs = http.get(
        "/api/v1/projects/p/script-creation/handoffs?episode_number=1"
    ).json()["data"]
    assert len(handoffs) == 1
