from concurrent.futures import ThreadPoolExecutor

import pytest

from novelvideo.voice_assets.store import AssetInput, Tags, VoiceAssetStore


def test_collection_deduplicates_concurrent_saves_and_preserves_metadata(tmp_path):
    store = VoiceAssetStore(tmp_path)
    def save(_):
        return store.save("u", b"audio", ".wav", 1, AssetInput(name="沉稳", tags=Tags(timbre=["低沉"])), {"project": "A"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save, range(8)))
    assert sum(created for _, created in results) == 1
    assert len({asset["id"] for asset, _ in results}) == 1
    asset, created = store.save("u", b"audio", ".wav", 1, AssetInput(name="不要覆盖"), {"project": "B"})
    assert not created and asset["name"] == "沉稳"
    assert VoiceAssetStore(tmp_path).read("u", asset["id"])[1] == b"audio"
    with pytest.raises(KeyError):
        store.read("other", asset["id"])


def test_tag_combinations_and_independent_copy(tmp_path):
    store = VoiceAssetStore(tmp_path / "library")
    asset, _ = store.save("u", b"a", ".mp3", 1, AssetInput(name="将军100%", tags=Tags(timbre=["低沉", "低沉"], identity=["将军"], personality=["沉稳"])), {})
    store.save("u", b"b", ".wav", 2, AssetInput(name="学生", tags=Tags(timbre=["清亮"])), {})
    assert store.list("u", tags={"timbre": ["沙哑", "低沉"], "identity": ["将军"]})["total"] == 1
    assert store.list("u", tags={"timbre": ["清亮"], "identity": ["将军"]})["total"] == 0
    assert store.list("u", q="%")["total"] == 1
    assert store.tags("u")["identity"] == ["将军"]
    assert store.tags("other")["timbre"] == []
    target = tmp_path / "project.wav"
    target.write_bytes(store.read("u", asset["id"])[1])
    store.delete("u", asset["id"])
    assert target.read_bytes() == b"a"


def test_validation_and_failed_write(tmp_path, monkeypatch):
    from pathlib import Path
    with pytest.raises(ValueError):
        AssetInput(name="  ")
    with pytest.raises(ValueError):
        Tags(timbre=["x" * 31])
    store = VoiceAssetStore(tmp_path)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError):
        store.save("u", b"a", ".wav", 1, AssetInput(name="a"), {})
    assert store.list("u")["total"] == 0


@pytest.mark.asyncio
async def test_cross_project_adoption_is_reviewed_and_survives_library_deletion(tmp_path, monkeypatch):
    from novelvideo.api.routes import global_voice_assets as routes
    from novelvideo.api.routes.voice_candidates import voice_readiness
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore
    library = VoiceAssetStore(tmp_path / "library")
    asset, _ = library.save("u", b"audio", ".wav", 1, AssetInput(name="将军"), {"project": "A"})
    project = tmp_path / "B"
    sql = SQLiteStore("B", output_dir=str(project), state_dir=str(tmp_path))
    await sql.initialize()
    role = NovelCharacter(name="乙", voice_facts={"vocalization_mode": "dialogue", "provenance": "human"})
    await sql.add_character(role)
    candidates = VoiceCandidateStore(sql.db_path, project)
    async def resolve(*args, **kwargs):
        return None, sql, sql.get_character("乙"), candidates
    monkeypatch.setattr(routes, "resolve_voice_scope", resolve)
    monkeypatch.setattr(routes, "storage", lambda: library)
    monkeypatch.setattr(routes, "check_voice_audio", lambda *args, **kwargs: {"status": "qc_unavailable", "technical": {"passed": True}})
    body = routes.Adoption(slot="default", expected_reference={"path": "", "sha256": ""}, request_id="once")
    try:
        result = await routes.adopt("B", "乙", asset["id"], body, {"id": "u"})
        candidate = candidates.get(result["data"]["candidate_id"])
        assert candidate["status"] == "approved"
        assert voice_readiness(sql.get_character("乙"), candidates)["production_ready"]
        library.delete("u", asset["id"])
        assert candidates.read_audio(candidate["candidate_id"]) == b"audio"
        assert await routes.adopt("B", "乙", asset["id"], body, {"id": "u"}) == result
    finally:
        await sql.close()


def test_identity_rejects_missing_user_and_scoped_agent():
    from fastapi import HTTPException
    from novelvideo.api.routes.global_voice_assets import owner_id
    for user in ({}, {"id": "u", "credential_kind": "agent_session"}):
        with pytest.raises(HTTPException):
            owner_id(user)


@pytest.mark.asyncio
async def test_http_collection_approval_and_owner_boundaries(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from novelvideo.api.routes import global_voice_assets as routes
    from novelvideo.api.auth import get_api_user
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    candidates = VoiceCandidateStore(tmp_path / "project.db", tmp_path / "project")
    row = candidates.create("source", {"character_name": "甲", "slot": "default", "kind": "dialogue", "profile_digest": "x"})
    candidates.save_audio(row["candidate_id"], b"audio", "voice.wav")
    async def resolve(*args, **kwargs):
        return None, None, SimpleNamespace(name="甲"), candidates
    library = VoiceAssetStore(tmp_path / "library")
    monkeypatch.setattr(routes, "resolve_voice_scope", resolve)
    monkeypatch.setattr(routes, "storage", lambda: library)
    monkeypatch.setattr(routes, "probe_voice_sample_duration_seconds", lambda path: 2)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_api_user] = lambda: {"id": "u"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/projects/A/characters/甲/voice-candidates/{row['candidate_id']}/favorite"
        assert (await client.post(url, json={"name": "甲"})).status_code == 409
        with candidates.connection() as db:
            db.execute("UPDATE character_voice_candidates SET status='approved'")
        result = await client.post(url, json={"name": "甲", "tags": {"identity": ["将军"]}})
        assert result.status_code == 200
        asset_id = result.json()["data"]["asset"]["id"]
        assert (await client.get(f"/voice-assets/{asset_id}/audio")).content == b"audio"
        assert (await client.post(url, json={"name": "不能覆盖"})).json()["data"]["created"] is False
        assert (await client.get("/voice-assets", params={"identity": "将军"})).json()["data"]["total"] == 1
        app.dependency_overrides[get_api_user] = lambda: {"id": "other"}
        assert (await client.get(f"/voice-assets/{asset_id}/audio")).status_code == 404
        assert (await client.delete(f"/voice-assets/{asset_id}")).status_code == 404
        assert (await client.get("/voice-assets/tags")).json()["data"]["identity"] == []
