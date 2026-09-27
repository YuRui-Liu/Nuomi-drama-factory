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
        return SimpleNamespace(state_dir=str(state_dir), ctx=None)

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
