import pytest


@pytest.mark.asyncio
async def test_caption_editor_revision_and_no_removal(tmp_path):
    from novelvideo.export import caption_editor
    beats = [{"beat_number": 1, "narration_segment": "你去哪？我要回家。"}]
    draft = await caption_editor.get_track(tmp_path, 1, beats)
    assert draft["track"]["events"][0]["review_status"] == "needs_review"
    edited = dict(draft["track"])
    edited["events"] = [dict(e, source="manual", review_status="approved") for e in edited["events"]]
    # Text-only estimates cannot be approved without actual source duration.
    with pytest.raises(ValueError, match="CAPTION_ACTUAL_TIMING_REQUIRED"):
        await caption_editor.save_track(tmp_path, 1, beats, draft["revision"], edited)
    edited["events"] = []
    with pytest.raises(ValueError, match="CAPTION_CONTENT_REMOVED"):
        await caption_editor.save_track(tmp_path, 1, beats, draft["revision"], edited)


@pytest.mark.asyncio
async def test_caption_editor_atomic_revision_and_rebuild(tmp_path, monkeypatch):
    from novelvideo.export import caption_editor, episode_export
    from novelvideo.task_backend.runners import video
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"audio")
    monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *a: [video.VideoSpan(source, (1,))])
    async def duration(*a): return 4.0
    monkeypatch.setattr(episode_export, "caption_video_duration", duration)
    beats = [{"beat_number": 1, "narration_segment": "你好。"}]
    draft = await caption_editor.get_track(tmp_path, 1, beats)
    track = draft["track"]
    track["events"][0].update(source="manual", review_status="approved", start=0.5)
    saved = await caption_editor.save_track(tmp_path, 1, beats, draft["revision"], track)
    assert saved["revision"] != draft["revision"]
    with pytest.raises(ValueError, match="CAPTION_REVISION_CHANGED"):
        await caption_editor.save_track(tmp_path, 1, beats, draft["revision"], track)
    source.write_bytes(b"new audio")
    with pytest.raises(ValueError, match="CAPTION_SOURCE_CHANGED"):
        await caption_editor.get_track(tmp_path, 1, beats)
    rebuilt = await caption_editor.rebuild_track(tmp_path, 1, beats, saved["revision"])
    assert rebuilt["track"]["events"][0]["review_status"] == "needs_review"
    assert list((tmp_path / "videos/episodes/caption-history").glob("*.json"))


def test_caption_api_read_save_and_stale_conflict(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import generation
    async def context(*args):
        return tmp_path, [{"beat_number": 1, "narration_segment": "你好。"}]
    monkeypatch.setattr(generation, "_caption_context", context)
    app = FastAPI()
    app.include_router(generation.router)
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "test"}
    client = TestClient(app)
    url = "/projects/demo/episodes/1/captions"
    response = client.get(url)
    assert response.status_code == 200
    document = response.json()["data"]
    response = client.put(url, json=document)
    assert response.status_code == 200
    assert client.put(url, json=document).status_code == 409
    assert client.post(url + "/rebuild", json={}).status_code == 422
