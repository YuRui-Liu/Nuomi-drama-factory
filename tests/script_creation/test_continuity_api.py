import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def test_continuity_fact_api_preserves_sources_and_filters_context(tmp_path, monkeypatch):
    from novelvideo.api.routes import script_creation
    from novelvideo.script_creation.store import DocumentStore

    state_dir = tmp_path / "project-one"
    state_dir.mkdir()

    async def seed():
        store = DocumentStore(state_dir / "data.db")
        await store.initialize()
        bible = await store.create(kind="brief", title="故事圣经", markdown="简报",
                                   client_mutation_id="api-bible")
        source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                    markdown="主角拾起钥匙", client_mutation_id="api-source")
        await store.adopt_revision(source.id, revision_id=source.current_revision_id,
                                   client_mutation_id="api-source-adopt")
        return bible, source

    bible, source = asyncio.run(seed())
    roles = []
    async def resolve(project, user, *, required_role="viewer"):
        roles.append(required_role)
        if project != "one":
            raise HTTPException(404, detail="project not found")
        if required_role == "editor" and user["role"] == "viewer":
            raise HTTPException(403, detail="editor required")
        return SimpleNamespace(state_dir=str(state_dir),
                               ctx=SimpleNamespace(state_dir=str(state_dir), project_id=project))

    monkeypatch.setattr(script_creation, "resolve_project_scope", resolve)
    app = FastAPI()
    app.include_router(script_creation.router, prefix="/api/v1")
    user = {"role": "editor"}
    app.dependency_overrides[script_creation.get_api_user] = lambda: user
    base = "/api/v1/projects/one/script-creation"
    with TestClient(app) as http:
        payload = {
            "subject_id": "hero", "predicate": "owns", "object_id": "key",
            "state": "planned", "episode_start": 1, "episode_end": 1,
            "source_document_id": source.id, "source_revision_id": source.current_revision_id,
            "source_locator": f"block:{source.revision.blocks[0].id}",
            "client_mutation_id": "api-fact-create",
        }
        created = http.post(base + f"/documents/{bible.id}/continuity-facts", json=payload)
        assert created.status_code == 200, created.text
        candidate = created.json()["data"]
        assert candidate["adoption_status"] == "candidate"
        assert http.get(base + "/continuity-context?episode_number=1").json()["data"]["facts"] == []
        listed = http.get(base + f"/documents/{bible.id}/continuity-facts")
        assert listed.json()["data"][0]["source_revision_id"] == source.current_revision_id

        user["role"] = "viewer"
        forbidden = http.post(base + f"/continuity-facts/{candidate['id']}/adopt",
                              json={"client_mutation_id": "api-fact-adopt-blocked"})
        assert forbidden.status_code == 403
        assert http.get(base + "/continuity-context?episode_number=1").status_code == 200
        user["role"] = "editor"

        adopted = http.post(base + f"/continuity-facts/{candidate['id']}/adopt",
                            json={"client_mutation_id": "api-fact-adopt"})
        assert adopted.status_code == 200, adopted.text
        context = http.get(base + "/continuity-context?episode_number=1")
        fact = context.json()["data"]["facts"][0]
        assert fact["id"] == candidate["id"]
        assert fact["source_document_id"] == source.id
        assert fact["source_revision_id"] == source.current_revision_id
        assert fact["source_locator"] == f"block:{source.revision.blocks[0].id}"
        assert "viewer" in roles and "editor" in roles
