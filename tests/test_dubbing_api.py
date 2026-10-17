from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import dubbing
from novelvideo.audio.dubbing_contracts import DialogueSnapshot, DubbingPreparation, DubbingTake
from novelvideo.audio.dubbing_service import make_source_snapshot_hash
from novelvideo.audio.dubbing_store import DubbingStore


def make_client(monkeypatch, tmp_path: Path):
    context = SimpleNamespace(
        project_id="demo",
        output_dir=str(tmp_path),
        state_dir=str(tmp_path),
    )
    resolved = SimpleNamespace(ctx=context, project_dir=tmp_path)

    async def get_beats(_episode):
        return []

    async def close():
        pass

    sqlite_store = SimpleNamespace(
        db_path=tmp_path / "data.db",
        get_beats_as_dicts=get_beats,
        close=close,
    )

    async def resolve(*_args, **_kwargs):
        return resolved

    async def make_store(_ctx):
        return sqlite_store

    monkeypatch.setattr(dubbing, "resolve_project_scope", resolve)
    monkeypatch.setattr(dubbing, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(
        dubbing,
        "load_effective_groups",
        lambda *_args, **_kwargs: [SimpleNamespace(id="group-1", director_revision_id="script-r1")],
    )
    line = DialogueSnapshot(
        dialogue_id="line-1",
        character_id="char-1",
        group_id="group-1",
        script_revision="script-r1",
        source_revision="video-r1:source",
        text="走吧。",
        voice_version_id="voice-v1",
    )
    monkeypatch.setattr(
        dubbing,
        "load_group_dialogue_sources",
        lambda **_kwargs: ([line], "script-r1", "video-r1:source"),
    )
    app = FastAPI()
    app.include_router(dubbing.router, prefix="/api/v1")
    app.dependency_overrides[dubbing.get_api_user] = lambda: {
        "id": "user-1",
        "username": "tester",
    }
    return TestClient(app), sqlite_store, line


def seed_take(tmp_path: Path, line: DialogueSnapshot) -> tuple[DubbingStore, DubbingTake]:
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    prep = DubbingPreparation(
        preparation_id="prep-1",
        episode=1,
        group_id="group-1",
        script_revision="script-r1",
        source_revision="video-r1:source",
        expected_revision=0,
        source_snapshot_hash=make_source_snapshot_hash(
            script_revision="script-r1",
            source_revision="video-r1:source",
            dialogues=[line],
        ),
        dialogue_ids=(line.dialogue_id,),
        source_dialogue_ids=(line.dialogue_id,),
        dialogues=(line,),
        source_dialogues=(line,),
    )
    store.save_preparation(prep)
    audio = tmp_path / "assets" / "dubbing" / "take.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"immutable-audio")
    take = DubbingTake(
        take_id="take-1",
        preparation_id=prep.preparation_id,
        task_id="task-1",
        dialogue_id=line.dialogue_id,
        input_hash=hashlib.sha256(b"input").hexdigest(),
        voice_version_id=line.voice_version_id,
        audio_path=audio.relative_to(tmp_path).as_posix(),
        audio_sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
        actual_duration_seconds=1.5,
    )
    store.save_take(take)
    return store, take


def test_get_dubbing_exposes_group_adoption_and_protected_take_url(monkeypatch, tmp_path):
    client, _sqlite, line = make_client(monkeypatch, tmp_path)
    store, take = seed_take(tmp_path, line)
    store.adopt_takes(
        episode=1,
        group_id="group-1",
        take_ids=[take.take_id],
        expected_revision=0,
        actor="user-1",
        reason="reviewed",
    )

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["adoption"]["revision"] == 1
    assert data["adoption"]["by_dialogue"]["line-1"]["take_id"] == "take-1"
    assert data["takes"][0]["url"].startswith(
        "/static/projects/demo/assets/dubbing/take.wav?v="
    )
    assert data["dialogues"][0]["voice_version_id"] == "voice-v1"


def test_adopt_endpoint_is_cas_and_does_not_edit_a_manifest(monkeypatch, tmp_path):
    client, _sqlite, line = make_client(monkeypatch, tmp_path)
    store, take = seed_take(tmp_path, line)
    manifest = tmp_path / "h3_manifest.json"
    manifest.write_text('{"timeline":"untouched"}', encoding="utf-8")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing/adopt",
        json={"take_ids": [take.take_id], "expected_revision": 0, "reason": "reviewed"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["adoption"]["by_dialogue"]["line-1"]["adopted_by"] == "user-1"
    assert manifest.read_text(encoding="utf-8") == '{"timeline":"untouched"}'
    stale = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing/adopt",
        json={"take_ids": [take.take_id], "expected_revision": 0, "reason": "stale"},
    )
    assert stale.status_code == 409
