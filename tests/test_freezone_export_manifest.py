from __future__ import annotations

import hashlib

import pytest


def _adopted_take(tmp_path):
    from novelvideo.audio.dubbing_contracts import DubbingPreparation, DubbingTake
    from novelvideo.audio.dubbing_store import DubbingStore

    audio_path = tmp_path / "audio" / "take.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"adopted take bytes")
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(
        DubbingPreparation(
            preparation_id="prep-1",
            episode=4,
            group_id="group-1",
            script_revision="script-r1",
            source_revision="director-r1",
            expected_revision=0,
            source_snapshot_hash="a" * 64,
            dialogue_ids=("dialogue-1",),
            dialogues=(),
        )
    )
    take = DubbingTake(
        take_id="take-1",
        preparation_id="prep-1",
        task_id="task-1",
        dialogue_id="dialogue-1",
        input_hash="b" * 64,
        voice_version_id="voice-1",
        audio_path="audio/take.wav",
        audio_sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
        actual_duration_seconds=1.5,
        status="succeeded",
        technical_status="passed",
    )
    store.save_take(take)
    store.adopt_takes(
        episode=4,
        group_id="group-1",
        take_ids=[take.take_id],
        expected_revision=0,
        actor="user-1",
        reason="reviewed",
    )
    return store, audio_path


def _tracks(source_url: str = "/static/alice/demo/audio/take.wav") -> list[dict]:
    return [
        {
            "track_id": "audio-1",
            "kind": "audio",
            "items": [
                {
                    "item_id": "clip-1",
                    "source_url": source_url,
                    "source_path": "",
                    "source_node_id": "audio-node-9",
                    "timeline_start": 3.0,
                    "source_start": 0.25,
                    "source_end": 1.25,
                    "volume": 0.65,
                    "muted": False,
                    "dubbing_source": {
                        "take_id": "take-1",
                        "dialogue_id": "dialogue-1",
                        "group_id": "group-1",
                    },
                }
            ],
        },
        {
            "track_id": "video-1",
            "kind": "video",
            "items": [
                {
                    "item_id": "clip-v1",
                    "source_url": "/static/alice/demo/video.mp4",
                    "source_path": "",
                    "source_node_id": "video-node-2",
                    "timeline_start": 0,
                    "source_start": 0,
                    "source_end": 2,
                    "volume": 1,
                    "muted": False,
                }
            ],
        },
    ]


def test_client_node_id_does_not_prove_media_provenance(tmp_path):
    from novelvideo.freezone.export_manifest import build_export_manifest
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'video')
    tracks = [_tracks()[1]]
    tracks[0]['items'][0]['source_path'] = str(source)
    tracks[0]['items'][0]['source_node_id'] = 'unverified-client-claim'
    manifest = build_export_manifest(project_id='project-1', project_dir=tmp_path,
        resolution='720p', fps=30, fps_origin='default', tracks=tracks, dubbing_store=None)
    assert manifest['verification'] == 'legacy_unverified'
    assert manifest['tracks'][0]['items'][0]['source_status'] == 'legacy_unverified'


@pytest.mark.parametrize("speed", [0.1, 4.1])
def test_export_rejects_speed_outside_compositor_range(tmp_path, speed):
    from novelvideo.freezone.export_manifest import ExportManifestError, build_export_manifest
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'video')
    tracks = [_tracks()[1]]
    tracks[0]['items'][0].update(source_path=str(source), speed=speed)
    with pytest.raises(ExportManifestError, match='speed'):
        build_export_manifest(project_id='project-1', project_dir=tmp_path,
            resolution='720p', fps=30, fps_origin='default', tracks=tracks, dubbing_store=None)


def test_export_manifest_accepts_supported_playback_speed(tmp_path):
    from novelvideo.freezone.export_manifest import build_export_manifest

    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    tracks = [_tracks()[1]]
    tracks[0]["items"][0].update(source_path=str(source), speed=3)

    manifest = build_export_manifest(
        project_id="project-1",
        project_dir=tmp_path,
        resolution="720p",
        fps=30,
        fps_origin="default",
        tracks=tracks,
        dubbing_store=None,
    )

    assert manifest["tracks"][0]["items"][0]["speed"] == 3


@pytest.mark.parametrize(
    ("speed", "timeline_start", "timeline_end"),
    [
        (1.0, 5.1, 5.5),
        (1.0, 4.9, 5.1),
        (3.0, 1.6, 1.7),
    ],
    ids=["fully-after-output", "partially-after-output", "partial-after-speedup"],
)
def test_export_freeze_rejects_subtitles_beyond_last_video_frame(
    tmp_path, speed, timeline_start, timeline_end
):
    from novelvideo.freezone.export_manifest import ExportManifestError, build_export_manifest

    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    tracks = [{
        "track_id": "video-1",
        "kind": "video",
        "items": [{
            "item_id": "video-clip",
            "source_url": "/video.mp4",
            "source_path": str(source),
            "timeline_start": 0.0,
            "source_start": 0.0,
            "source_end": 5.0,
            "speed": speed,
        }],
    }]
    subtitle_tracks = [{
        "track_id": "subtitles",
        "cues": [{
            "cue_id": "late-caption",
            "text": "Visible only in the sidecar",
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            "timing": "manual",
            "timing_confirmed": True,
        }],
    }]

    with pytest.raises(ExportManifestError, match="beyond video output"):
        build_export_manifest(
            project_id="project-1",
            project_dir=tmp_path,
            resolution="720p",
            fps=30,
            fps_origin="default",
            tracks=tracks,
            subtitle_tracks=subtitle_tracks,
            dubbing_store=None,
        )


def test_export_execution_rejects_subtitle_past_frozen_video_end(tmp_path):
    from novelvideo.freezone.export_manifest import (
        ExportManifestError,
        build_export_manifest,
        verify_export_source_integrity,
    )

    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    tracks = [{
        "track_id": "video-1",
        "kind": "video",
        "items": [{
            "item_id": "video-clip",
            "source_url": "/video.mp4",
            "source_path": str(source),
            "timeline_start": 0.0,
            "source_start": 0.0,
            "source_end": 5.0,
            "speed": 1.0,
        }],
    }]
    valid_subtitles = [{
        "track_id": "subtitles",
        "cues": [{
            "cue_id": "caption",
            "text": "On screen",
            "timeline_start": 4.5,
            "timeline_end": 4.9,
            "timing": "manual",
            "timing_confirmed": True,
        }],
    }]
    manifest = build_export_manifest(
        project_id="project-1",
        project_dir=tmp_path,
        resolution="720p",
        fps=30,
        fps_origin="default",
        tracks=tracks,
        subtitle_tracks=valid_subtitles,
        dubbing_store=None,
    )
    changed_subtitles = [{
        "track_id": "subtitles",
        "cues": [{
            **valid_subtitles[0]["cues"][0],
            "timeline_start": 4.9,
            "timeline_end": 5.1,
        }],
    }]

    with pytest.raises(ExportManifestError, match="beyond video output"):
        verify_export_source_integrity(
            tmp_path,
            tracks,
            manifest,
            subtitle_tracks=changed_subtitles,
        )


def test_export_manifest_freezes_content_settings_sources_and_adopted_take(tmp_path):
    from novelvideo.freezone.export_manifest import build_export_manifest

    store, audio_path = _adopted_take(tmp_path)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"source video")
    tracks = _tracks()
    tracks[0]["items"][0]["source_path"] = str(audio_path)
    tracks[1]["items"][0]["source_path"] = str(video_path)

    manifest = build_export_manifest(
        project_id="project-1",
        project_dir=tmp_path,
        resolution="720p",
        fps=24,
        fps_origin="user",
        tracks=tracks,
        dubbing_store=store,
    )

    assert manifest["snapshot_hash"] == manifest["timeline_revision"]
    assert len(manifest["snapshot_hash"]) == 64
    assert manifest["verification"] == "legacy_unverified"  # Video node ID is not server-verified provenance.
    audio = manifest["tracks"][0]["items"][0]
    assert audio["source_node_id"] == "audio-node-9"
    assert audio["source_start"] == 0.25
    assert audio["source_end"] == 1.25
    assert audio["volume"] == 0.65
    assert audio["source_media_sha256"] == hashlib.sha256(audio_path.read_bytes()).hexdigest()
    assert audio["dubbing_source"] == {
        "take_id": "take-1",
        "episode": 4,
        "group_id": "group-1",
        "dialogue_id": "dialogue-1",
        "audio_sha256": audio["source_media_sha256"],
        "adoption_revision": 1,
    }
    assert manifest["settings"] == {
        "resolution": "720p",
        "resolution_origin": "default",
        "fps": 24,
        "fps_origin": "user",
        "background_color": "#000000",
        "keep_original_audio": True,
    }


def test_export_manifest_hash_changes_when_frozen_clip_edit_changes(tmp_path):
    from novelvideo.freezone.export_manifest import build_export_manifest

    store, audio_path = _adopted_take(tmp_path)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"source video")
    tracks = _tracks()
    tracks[0]["items"][0]["source_path"] = str(audio_path)
    tracks[1]["items"][0]["source_path"] = str(video_path)
    one = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", tracks=tracks, dubbing_store=store,
    )
    edited = _tracks()
    edited[0]["items"][0]["source_path"] = str(audio_path)
    edited[1]["items"][0]["source_path"] = str(video_path)
    edited[0]["items"][0]["volume"] = 0.5
    two = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", tracks=edited, dubbing_store=store,
    )

    assert one["snapshot_hash"] != two["snapshot_hash"]
    assert one["settings"]["fps_origin"] == "default"


@pytest.mark.parametrize("adopted", [False, True])
def test_export_manifest_rejects_unadopted_or_wrongly_scoped_dubbing_source(
    tmp_path, adopted
):
    from novelvideo.audio.dubbing_contracts import DubbingPreparation, DubbingTake
    from novelvideo.audio.dubbing_store import DubbingStore
    from novelvideo.freezone.export_manifest import ExportManifestError, build_export_manifest

    audio_path = tmp_path / "audio" / "take.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"take")
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(
        DubbingPreparation(
            preparation_id="prep-1", episode=4, group_id="group-1",
            script_revision="script-r1", source_revision="director-r1",
            expected_revision=0, source_snapshot_hash="a" * 64,
            dialogue_ids=("dialogue-1",), dialogues=(),
        )
    )
    store.save_take(
        DubbingTake(
            take_id="take-1", preparation_id="prep-1", task_id="task-1",
            dialogue_id="dialogue-1", input_hash="b" * 64,
            voice_version_id="voice-1", audio_path="audio/take.wav",
            audio_sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
            actual_duration_seconds=1, status="succeeded", technical_status="passed",
        )
    )
    if adopted:
        store.adopt_takes(
            episode=4, group_id="group-1", take_ids=["take-1"],
            expected_revision=0, actor="user-1", reason="reviewed",
        )
    tracks = _tracks()
    tracks[0]["items"][0]["source_path"] = str(audio_path)
    if adopted:
        tracks[0]["items"][0]["dubbing_source"]["group_id"] = "another-group"

    with pytest.raises(ExportManifestError, match="dubbing|adopted|adoption"):
        build_export_manifest(
            project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
            fps_origin="default", tracks=tracks, dubbing_store=store,
        )


def test_legacy_export_is_marked_unverified_and_source_change_is_rejected(tmp_path):
    from novelvideo.freezone.export_manifest import (
        ExportManifestError,
        build_export_manifest,
        verify_export_source_integrity,
    )

    source = tmp_path / "legacy.mp4"
    source.write_bytes(b"old source")
    tracks = [
        {
            "track_id": "video-1", "kind": "video", "items": [{
                "item_id": "clip-1", "source_url": "/legacy.mp4",
            "source_path": str(source),
                "timeline_start": 0, "source_start": 1, "source_end": 3,
                "volume": 1, "muted": False,
            }],
        }
    ]
    manifest = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", tracks=tracks, dubbing_store=None,
    )
    assert manifest["verification"] == "legacy_unverified"
    verify_export_source_integrity(tmp_path, tracks, manifest)

    source.write_bytes(b"changed source after freeze")
    with pytest.raises(ExportManifestError, match="source changed"):
        verify_export_source_integrity(tmp_path, tracks, manifest)


@pytest.mark.parametrize("field,value", [("volume", 0.25), ("source_start", 1.5)])
def test_execution_cannot_change_frozen_clip_settings(tmp_path, field, value):
    from novelvideo.freezone.export_manifest import (
        ExportManifestError,
        build_export_manifest,
        verify_export_source_integrity,
    )

    source = tmp_path / "legacy.mp4"
    source.write_bytes(b"stable source")
    tracks = [{
        "track_id": "video-1", "kind": "video", "items": [{
            "item_id": "clip-1", "source_url": "/legacy.mp4", "source_path": str(source),
            "source_node_id": "node-1", "timeline_start": 0, "source_start": 1,
            "source_end": 3, "volume": 1, "muted": False,
        }],
    }]
    manifest = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", tracks=tracks, dubbing_store=None,
    )
    changed = [{**tracks[0], "items": [{**tracks[0]["items"][0], field: value}]}]

    with pytest.raises(ExportManifestError, match="clip settings"):
        verify_export_source_integrity(tmp_path, changed, manifest)


def test_export_manifest_rejects_duplicate_track_or_item_identity(tmp_path):
    from novelvideo.freezone.export_manifest import ExportManifestError, build_export_manifest

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    item = {
        "item_id": "same-clip", "source_url": "/source.mp4", "source_path": str(source),
        "source_start": 0, "source_end": 1,
    }
    tracks = [
        {"track_id": "video-1", "kind": "video", "items": [item]},
        {"track_id": "audio-1", "kind": "audio", "items": [{**item}]},
    ]

    with pytest.raises(ExportManifestError, match="unique"):
        build_export_manifest(
            project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
            fps_origin="default", tracks=tracks, dubbing_store=None,
        )


@pytest.mark.parametrize(
    ("setting", "value", "error"),
    [
        ("resolution", "720p", "resolution"),
        ("fps", 24, "FPS"),
        ("background_color", "#ffffff", "background color"),
        ("keep_original_audio", False, "original-audio"),
    ],
)
def test_execution_settings_cannot_change_after_freeze(tmp_path, setting, value, error):
    from novelvideo.freezone.export_manifest import (
        ExportManifestError,
        build_export_manifest,
        verify_export_source_integrity,
    )

    source = tmp_path / "source.mp4"
    source.write_bytes(b"stable media")
    tracks = [{
        "track_id": "video-1", "kind": "video", "items": [{
            "item_id": "clip-1", "source_url": "/source.mp4", "source_path": str(source),
            "source_node_id": "node-1", "source_start": 0, "source_end": 1,
        }],
    }]
    manifest = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", background_color="#000000", keep_original_audio=True,
        tracks=tracks, dubbing_store=None,
    )
    execution = {
        "resolution": "1080p", "fps": 30, "background_color": "#000000",
        "keep_original_audio": True,
    }
    execution[setting] = value

    with pytest.raises(ExportManifestError, match=error):
        verify_export_source_integrity(
            tmp_path, tracks, manifest, project_id="project-1", **execution,
        )


def test_sidecar_persists_manifest_and_project_relative_artifact(tmp_path):
    import json
    from novelvideo.freezone.export_manifest import build_export_manifest, write_export_sidecar

    source = tmp_path / "source.mp4"
    source.write_bytes(b"stable media")
    output = tmp_path / "outputs" / "freezone_video_compose" / "job.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"rendered media")
    tracks = [{
        "track_id": "video-1", "kind": "video", "items": [{
            "item_id": "clip-1", "source_url": "/source.mp4", "source_path": str(source),
            "source_node_id": "node-1", "source_start": 0, "source_end": 1,
        }],
    }]
    manifest = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", tracks=tracks, dubbing_store=None,
    )
    sidecar_path = output.with_suffix(".manifest.json")

    result = write_export_sidecar(sidecar_path, manifest, output, project_dir=tmp_path)

    assert result["manifest"] == manifest
    assert result["artifact"]["path"] == "outputs/freezone_video_compose/job.mp4"
    assert json.loads(sidecar_path.read_text(encoding="utf-8")) == result


def test_compose_job_rejects_execution_settings_before_ffmpeg(tmp_path):
    import asyncio
    from novelvideo.freezone.export_manifest import ExportManifestError, build_export_manifest
    from novelvideo.freezone.jobs import run_freezone_video_compose

    source = tmp_path / "source.mp4"
    source.write_bytes(b"stable media")
    tracks = [{
        "track_id": "video-1", "kind": "video", "items": [{
            "item_id": "clip-1", "source_url": "/source.mp4", "source_path": str(source),
            "source_node_id": "node-1", "source_start": 0, "source_end": 1,
        }],
    }]
    manifest = build_export_manifest(
        project_id="project-1", project_dir=tmp_path, resolution="1080p", fps=30,
        fps_origin="default", background_color="#000000", keep_original_audio=True,
        tracks=tracks, dubbing_store=None,
    )

    with pytest.raises(ExportManifestError, match="background color"):
        asyncio.run(
            run_freezone_video_compose(
                project_dir=tmp_path,
                job_id="compose-1",
                resolution="1080p",
                fps=30,
                background_color="#ffffff",
                keep_original_audio=True,
                tracks=tracks,
                export_manifest=manifest,
                project_id="project-1",
            )
        )


def test_compose_request_schema_preserves_source_node_and_dubbing_provenance():
    from novelvideo.api.schemas import FreezoneVideoComposeRequest

    request = FreezoneVideoComposeRequest.model_validate({
        "fps": 24,
        "tracks": [{
            "track_id": "audio-1",
            "kind": "audio",
            "items": [{
                "item_id": "clip-1",
                "source_url": "/static/alice/demo/take.wav",
                "source_end": 2,
                "source_node_id": "audio-node-1",
                "dubbing_source": {
                    "take_id": "take-1", "dialogue_id": "dialogue-1",
                    "group_id": "group-1",
                },
            }],
        }],
    })

    item = request.tracks[0].items[0]
    assert item.source_node_id == "audio-node-1"
    assert item.dubbing_source.model_dump(exclude_none=True) == {
        "take_id": "take-1", "dialogue_id": "dialogue-1", "group_id": "group-1",
    }
    assert request.fps == 24


def test_compose_api_rejects_unknown_dubbing_take_before_starting_job(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone

    class _SqliteStore:
        db_path = tmp_path / "data.db"

        async def close(self):
            return None

    video = tmp_path / "source.mp4"
    audio = tmp_path / "take.wav"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    async def fake_resolve(_project, _user, *, required_role="editor"):
        return None, "alice", "demo", tmp_path, str(tmp_path)

    async def fake_sqlite_store(_username, _project):
        return _SqliteStore()

    started = []
    monkeypatch.setattr(freezone, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone, "make_sqlite_store", fake_sqlite_store)
    monkeypatch.setattr(freezone, "_start_freezone_video_compose_task", lambda **kwargs: started.append(kwargs))

    app = FastAPI()
    app.include_router(freezone.router)
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}
    response = TestClient(app).post(
        "/projects/demo/freezone/video/compose",
        json={
            "tracks": [
                {"track_id": "video-1", "kind": "video", "items": [{
                    "item_id": "video-clip", "source_url": "/source.mp4", "source_end": 2,
                }]},
                {"track_id": "audio-1", "kind": "audio", "items": [{
                    "item_id": "audio-clip", "source_url": "/take.wav", "source_end": 1,
                    "dubbing_source": {
                        "take_id": "unknown", "dialogue_id": "dialogue-1", "group_id": "group-1",
                    },
                }]},
            ],
        },
    )

    assert response.status_code == 422
    assert "dubbing" in str(response.json()).lower()
    assert started == []


def test_compose_api_uses_project_resolution_and_returns_frozen_manifest(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone

    source = tmp_path / "source.mp4"
    source.write_bytes(b"project source")

    async def fake_resolve(_project, _user, *, required_role="editor"):
        return None, "alice", "demo", tmp_path, str(tmp_path)

    started = []
    monkeypatch.setattr(freezone, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone, "load_project_config_file", lambda *_args: {"video_resolution": "720p"})
    monkeypatch.setattr(freezone, "_start_freezone_video_compose_task", lambda **kwargs: started.append(kwargs))
    monkeypatch.setattr(freezone, "_new_job_id", lambda: "compose-1")

    app = FastAPI()
    app.include_router(freezone.router)
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}
    response = TestClient(app).post(
        "/projects/demo/freezone/video/compose",
        json={"tracks": [{"track_id": "video-1", "kind": "video", "items": [{
            "item_id": "video-clip", "source_url": "/source.mp4", "source_end": 2,
            "source_node_id": "video-node-1",
        }]}]},
    )

    assert response.status_code == 200
    accepted_manifest = response.json()["data"]["export_manifest"]
    assert accepted_manifest["settings"] == {
        "resolution": "720p",
        "resolution_origin": "project",
        "fps": 30,
        "fps_origin": "default",
        "background_color": "#000000",
        "keep_original_audio": True,
    }
    assert accepted_manifest["snapshot_hash"] == accepted_manifest["timeline_revision"]
    assert started[0]["resolution"] == "720p"
    assert started[0]["export_manifest"] == accepted_manifest

