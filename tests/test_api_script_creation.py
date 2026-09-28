from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    from novelvideo.api.routes import script_creation

    roles = []
    async def resolve(project, user, *, required_role="viewer"):
        roles.append(required_role)
        if project not in {"one", "two"}:
            raise HTTPException(404, detail="project not found")
        if required_role == "editor" and user["role"] == "viewer":
            raise HTTPException(403, detail="editor required")
        state_dir = tmp_path / project
        state_dir.mkdir(exist_ok=True)
        return SimpleNamespace(state_dir=str(state_dir), ctx=SimpleNamespace(state_dir=str(state_dir), project_id=project))

    async def make_store(ctx):
        from novelvideo.sqlite_store import SQLiteStore
        store = SQLiteStore("test", ctx.state_dir, ctx.state_dir)
        await store.initialize()
        return store

    monkeypatch.setattr(script_creation, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(script_creation, "resolve_project_scope", resolve)
    app = FastAPI()
    app.include_router(script_creation.router, prefix="/api/v1")
    user = {"role": "editor"}
    app.dependency_overrides[script_creation.get_api_user] = lambda: user
    with TestClient(app) as http:
        yield http, user, roles


def test_document_api_scope_roles_conflicts_and_restore(client):
    http, user, roles = client
    base = "/api/v1/projects/one/script-creation"
    created = http.post(base + "/documents", json={
        "kind": "brief", "title": "创作简报", "markdown": "甲\n\n乙", "client_mutation_id": "create",
    })
    assert created.status_code == 200
    document = created.json()["data"]
    document_id = document["id"]
    first_revision = document["current_revision_id"]
    assert document["revision"]["markdown"] == "甲\n\n乙"
    assert http.get(base + "/documents").json()["data"][0]["id"] == document_id
    assert http.get(base + f"/documents/{document_id}").status_code == 200
    assert http.get(f"/api/v1/projects/two/script-creation/documents/{document_id}").status_code == 404

    user["role"] = "viewer"
    assert http.get(base + f"/documents/{document_id}").status_code == 200
    assert http.post(base + "/documents", json={
        "kind": "brief", "title": "blocked", "client_mutation_id": "blocked",
    }).status_code == 403
    assert http.put(base + f"/documents/{document_id}", json={
        "base_revision_id": first_revision, "markdown": "blocked", "client_mutation_id": "blocked",
    }).status_code == 403
    user["role"] = "editor"

    updated = http.put(base + f"/documents/{document_id}", json={
        "base_revision_id": first_revision, "markdown": "甲\n\n丙", "client_mutation_id": "save",
    })
    assert updated.status_code == 200
    second_revision = updated.json()["data"]["current_revision_id"]
    stale = http.put(base + f"/documents/{document_id}", json={
        "base_revision_id": first_revision, "markdown": "冲突", "client_mutation_id": "stale",
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_revision_id"] == second_revision
    restored = http.post(base + f"/documents/{document_id}/restore", json={
        "revision_id": first_revision, "base_revision_id": second_revision, "client_mutation_id": "restore",
    })
    assert restored.status_code == 200
    assert restored.json()["data"]["revision"]["restored_from_revision_id"] == first_revision
    history = http.get(base + f"/documents/{document_id}/revisions")
    assert [item["id"] for item in history.json()["data"]] == [
        first_revision, second_revision, restored.json()["data"]["current_revision_id"],
    ]
    assert "viewer" in roles and "editor" in roles


def test_api_rejects_invalid_blocks_and_reused_mutation(client):
    http, _, _ = client
    base = "/api/v1/projects/one/script-creation"
    created = http.post(base + "/documents", json={
        "kind": "outline", "title": "大纲", "markdown": "甲\n\n乙", "client_mutation_id": "create",
    }).json()["data"]
    document_id = created["id"]
    revision_id = created["current_revision_id"]
    block_id = created["revision"]["blocks"][0]["id"]
    url = base + f"/documents/{document_id}"
    invalid = http.put(url, json={
        "base_revision_id": revision_id, "markdown": "甲乙", "client_mutation_id": "bad",
        "blocks": [{"id": block_id, "markdown": "甲"}, {"id": block_id, "markdown": "乙"}],
    })
    assert invalid.status_code == 422
    foreign = http.put(url, json={
        "base_revision_id": revision_id, "markdown": "甲", "client_mutation_id": "foreign",
        "blocks": [{"id": "someone-elses-id", "markdown": "甲"}],
    })
    assert foreign.status_code == 422
    saved = http.put(url, json={
        "base_revision_id": revision_id, "markdown": "甲\n\n丙", "client_mutation_id": "save",
    })
    assert saved.status_code == 200
    different_payload = http.put(url, json={
        "base_revision_id": revision_id, "markdown": "甲\n\n丁", "client_mutation_id": "save",
    })
    assert different_payload.status_code == 409


def test_entity_api_roles_cas_and_no_queue(client, monkeypatch):
    from novelvideo.api.routes import script_creation
    def forbidden(*args, **kwargs):
        raise AssertionError("narrative asset links must never queue media")
    monkeypatch.setattr(script_creation, "enqueue_project_task", forbidden)
    http, user, roles = client
    base = "/api/v1/projects/one/script-creation"
    doc = http.post(base + "/documents", json={"kind": "people", "title": "人物", "markdown": "## A", "client_mutation_id": "doc"}).json()["data"]
    args = dict(document_id=doc["id"], base_revision_id=doc["current_revision_id"], block_id=doc["revision"]["blocks"][0]["id"], name="A", client_mutation_id="entity", create_text={"name": "A", "description": "text"})
    response = http.post(base + "/entities", json=args)
    assert response.status_code == 200, response.text
    entity = response.json()["data"]
    assert entity["asset_id"]
    assert http.get(base + "/assets?asset_type=character").json()["data"][0]["asset_id"] == entity["asset_id"]
    user["role"] = "viewer"
    assert http.get(base + "/entities").status_code == 200
    assert http.post(base + "/entities", json=args).status_code == 403
    user["role"] = "editor"
    assert http.post(base + "/entities", json={**args, "base_revision_id": "stale", "client_mutation_id": "stale"}).status_code == 409
    assert http.post("/api/v1/projects/two/script-creation/entities", json={**args, "client_mutation_id": "foreign"}).status_code == 404


def test_handoff_api_freezes_draft_and_enforces_project_roles(client):
    http, user, roles = client
    base = "/api/v1/projects/one/script-creation"
    created = http.post(base + "/documents", json={
        "kind": "episode_script", "title": "第一集", "episode_number": 1,
        "markdown": "## 1-1｜账房 · 夜 · 内\n甲：你好。", "client_mutation_id": "handoff-create",
    })
    assert created.status_code == 200
    doc = created.json()["data"]
    request = {
        "document_id": doc["id"], "revision_id": doc["current_revision_id"],
        "reference_revisions": {}, "selected_entity_ids": [],
        "update_scope": {"mode": "none"},
        "fact_acknowledgement": {"mode": "unchecked", "reason": "人工确认"},
        "client_mutation_id": "handoff-prepare",
    }
    prepared = http.post(base + "/handoffs/prepare", json=request)
    assert prepared.status_code == 200
    handoff = prepared.json()["data"]
    assert handoff["snapshot"]["markdown"] == doc["revision"]["markdown"]
    assert http.get(base + "/handoffs", params={"episode_number": 1}).json()["data"][0]["id"] == handoff["id"]
    confirm = http.post(base + f"/handoffs/{handoff['id']}/confirm", json={
        "expected_source_project_revision": 0, "client_mutation_id": "handoff-confirm",
    })
    assert confirm.status_code == 202
    assert confirm.json()["data"]["status"] == "completed"
    assert http.get(base + f"/handoffs/{handoff['id']}").status_code == 200
    assert http.get(f"/api/v1/projects/two/script-creation/handoffs/{handoff['id']}").status_code == 404
    user["role"] = "viewer"
    assert http.get(base + f"/handoffs/{handoff['id']}").status_code == 200
    assert http.post(base + "/handoffs/prepare", json={**request, "client_mutation_id": "viewer"}).status_code == 403
    assert http.post(base + f"/handoffs/{handoff['id']}/retry").status_code == 403
    assert "viewer" in roles and "editor" in roles
