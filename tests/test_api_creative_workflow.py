from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.script_creation.store import DocumentStore


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    from novelvideo.api.routes import creative_workflow

    async def resolve(project, user, *, required_role="viewer"):
        if project != "one":
            raise HTTPException(404, detail="project not found")
        if required_role == "editor" and user["role"] == "viewer":
            raise HTTPException(403, detail="editor required")
        state_dir = tmp_path / project
        state_dir.mkdir(exist_ok=True)
        context = SimpleNamespace(state_dir=str(state_dir), project_id=project)
        return SimpleNamespace(state_dir=str(state_dir), ctx=context)

    monkeypatch.setattr(creative_workflow, "resolve_project_scope", resolve)
    app = FastAPI()
    app.include_router(creative_workflow.router, prefix="/api/v1")
    app.dependency_overrides[creative_workflow.get_api_user] = lambda: {"role": "editor"}
    with TestClient(app) as http:
        yield http, tmp_path, creative_workflow


def test_generate_false_only_creates_container_and_task_replay_is_idempotent(client, monkeypatch):
    http, tmp_path, routes = client
    data_dir = tmp_path / "one"
    data_dir.mkdir(exist_ok=True)
    store = DocumentStore(data_dir / "data.db")
    import asyncio
    brief = asyncio.run(store.initialize())
    brief = asyncio.run(store.create(kind="brief", title="简报", markdown="林夏寻找失踪的姐姐。",
                                     client_mutation_id="route-brief"))
    calls = []

    async def enqueue(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id=f"task-{len(calls)}"),
                               backend="inline", queue=None)

    monkeypatch.setattr(routes, "enqueue_project_task", enqueue)
    base = "/api/v1/projects/one/creative-workflow/ideations"
    body = {"brief_document_id": brief.id, "brief_revision_id": brief.current_revision_id,
            "generate": False, "client_mutation_id": "container"}
    created = http.post(base, json=body)
    assert created.status_code == 202
    assert created.json()["data"]["run"]["status"] == "completed"
    assert created.json()["data"]["run"]["candidates"] == []
    assert calls == []

    generated_body = {**body, "generate": True, "client_mutation_id": "generated"}
    first = http.post(base, json=generated_body)
    replay = http.post(base, json=generated_body)
    assert first.status_code == replay.status_code == 202
    assert first.json()["data"]["run"]["id"] == replay.json()["data"]["run"]["id"]
    assert len(calls) == 1


def test_screenplay_review_poll_returns_null_before_review_is_requested(client):
    http, _, _ = client
    response = http.get(
        "/api/v1/projects/one/creative-workflow/screenplays/script/reviews/revision")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": None}
