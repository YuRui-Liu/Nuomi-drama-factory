import hashlib
import json
from pathlib import Path

import pytest

from novelvideo.media_capabilities.tts.acceptance import VoiceAcceptanceStore


def batch(root: Path):
    root.mkdir()
    audio = b"existing-provider-audio"
    (root / "child.wav").write_bytes(audio)
    (root / "child.preview.wav").write_bytes(b"preview-wav")
    (root / "child.json").write_text(json.dumps({
        "sample": "child", "task_id": "task-1", "instruction": "儿童男声",
        "text": "你好", "sha256": hashlib.sha256(audio).hexdigest(),
        "provider_accounting": {"usage.consumeCoins": "6"},
    }))
    (root / "summary.json").write_text(json.dumps({"results": [{
        "sample": "child", "task_id": "task-1", "coins": "6", "duration": "6.24",
    }]}))
    return root


def test_import_review_persistence_and_idempotency(tmp_path):
    source = batch(tmp_path / "source")
    store = VoiceAcceptanceStore(tmp_path / "project")
    assert store.list_samples() == []
    store.import_batch(source)
    sample = store.list_samples()[0]
    assert sample["coins"] == "6"
    assert sample["status"] == "pending"
    assert sample["instruction"] == "儿童男声"
    store.review(sample["sample_id"], status="rejected", notes="有明显人声", actor="tester")
    store.import_batch(source)
    fresh = VoiceAcceptanceStore(tmp_path / "project").list_samples()
    assert len(fresh) == 1
    assert fresh[0]["status"] == "rejected"
    assert fresh[0]["notes"] == "有明显人声"
    assert fresh[0]["reviewed_by"] == "tester"
    assert VoiceAcceptanceStore(tmp_path / "other").list_samples() == []
    assert not (tmp_path / "project" / "data.db").exists()


def test_invalid_review_and_missing_sample(tmp_path):
    store = VoiceAcceptanceStore(tmp_path / "project")
    with pytest.raises(ValueError):
        store.review("missing", status="approved", notes="", actor="tester")
    with pytest.raises(KeyError):
        store.review("missing", status="passed", notes="", actor="tester")


def test_import_checks_audio_before_writing(tmp_path):
    source = batch(tmp_path / "source")
    (source / "child.wav").write_bytes(b"tampered")
    store = VoiceAcceptanceStore(tmp_path / "project")
    with pytest.raises(ValueError, match="hash"):
        store.import_batch(source)
    assert store.list_samples() == []


@pytest.mark.asyncio
async def test_api_checks_project_roles_and_never_binds_production(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from novelvideo.api.routes import voice_acceptance as routes

    store = VoiceAcceptanceStore(tmp_path / "project")
    store.import_batch(batch(tmp_path / "source"))
    sample_id = store.list_samples()[0]["sample_id"]
    roles = []

    async def resolve(project, user, *, required_role):
        roles.append((project, required_role))
        return SimpleNamespace(ctx=object(), project_dir=tmp_path / project)

    monkeypatch.setattr(routes, "resolve_project_scope", resolve)
    monkeypatch.setattr(routes, "make_static_url_for_context", lambda ctx, path, **kw: "/static/" + path)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_api_user] = lambda: {"username": "tester"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/projects/project/voice-acceptance")
        assert response.status_code == 200
        assert response.json()["data"][0]["url"].startswith("/static/assets/")
        response = await client.patch(f"/projects/project/voice-acceptance/{sample_id}",
                                      json={"status": "passed", "notes": "试听合格"})
        assert response.status_code == 200
        assert response.json()["data"]["reviewed_by"] == "tester"
        assert roles == [("project", "viewer"), ("project", "editor")]
        response = await client.patch(f"/projects/other/voice-acceptance/{sample_id}", json={"status": "passed"})
        assert response.status_code == 404
        response = await client.patch(f"/projects/project/voice-acceptance/{sample_id}", json={"status": "approved"})
        assert response.status_code == 422
        response = await client.patch(f"/projects/project/voice-acceptance/{sample_id}", json={"status": "passed", "character_name": "步知遥"})
        assert response.status_code == 422
        response = await client.get("/projects/project/voice-acceptance")
        assert response.json()["data"][0]["notes"] == "试听合格"
