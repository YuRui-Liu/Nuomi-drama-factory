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


def _seed_preparation(tmp_path: Path, line: DialogueSnapshot):
    preparation = DubbingPreparation(
        preparation_id="prep-generate",
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
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(preparation)
    return store, preparation


def test_new_group_dubbing_submission_is_gated_before_enqueue(monkeypatch, tmp_path):
    from novelvideo.freezone.orchestration.models import OrchestrationError

    client, _sqlite, line = make_client(monkeypatch, tmp_path)
    _store, preparation = _seed_preparation(tmp_path, line)

    def blocked(_state_root, _output_root, episode, stage, **_kwargs):
        assert episode == 1 and stage == "audio"
        raise OrchestrationError(
            "production_stage_blocked", "screenplay required",
            stage="audio", required_stage="screenplay", status="pending",
            reason="not_ready",
        )

    monkeypatch.setattr(dubbing, "admit_registered_episode_stage", blocked)
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing/generate",
        json={"preparation_id": preparation.preparation_id, "expected_revision": 0,
              "request_id": "dubbing-request-1"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["required_stage"] == "screenplay"


def test_exact_group_dubbing_replay_skips_new_submission_gate(monkeypatch, tmp_path):
    client, _sqlite, line = make_client(monkeypatch, tmp_path)
    store, preparation = _seed_preparation(tmp_path, line)
    store.create_task(
        episode=1, group_id="group-1", preparation_id=preparation.preparation_id,
        request_id="dubbing-request-1", payload_hash="a" * 64,
        dialogue_ids=(line.dialogue_id,),
    )

    def should_not_gate(*_args, **_kwargs):
        raise AssertionError("an exact durable request replay must not be gated")

    async def replay(**_kwargs):
        return {"task_id": "existing", "status": "queued"}

    monkeypatch.setattr(dubbing, "admit_registered_episode_stage", should_not_gate)
    monkeypatch.setattr(dubbing, "enqueue_group_dubbing", replay)
    monkeypatch.setattr(dubbing, "get_task_backend", lambda: SimpleNamespace())
    monkeypatch.setattr(dubbing, "get_task_manager", lambda: SimpleNamespace())
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing/generate",
        json={"preparation_id": preparation.preparation_id, "expected_revision": 0,
              "request_id": "dubbing-request-1"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["data"]["task_id"] == "existing"


def test_new_group_dubbing_retry_is_gated_before_enqueue(monkeypatch, tmp_path):
    from novelvideo.freezone.orchestration.models import OrchestrationError

    client, _sqlite, line = make_client(monkeypatch, tmp_path)
    store, preparation = _seed_preparation(tmp_path, line)
    failed, _created = store.create_task(
        episode=1, group_id="group-1", preparation_id=preparation.preparation_id,
        request_id="initial-request", payload_hash="b" * 64,
        dialogue_ids=(line.dialogue_id,),
    )
    store.update_task(
        failed.task_id, status="failed", failed_dialogue_ids=(line.dialogue_id,),
    )

    def blocked(_state_root, _output_root, episode, stage, **_kwargs):
        assert episode == 1 and stage == "audio"
        raise OrchestrationError(
            "production_stage_blocked", "screenplay required",
            stage="audio", required_stage="screenplay", status="pending",
            reason="not_ready",
        )

    async def should_not_retry(**_kwargs):
        raise AssertionError("retry must be admitted before re-enqueue")

    monkeypatch.setattr(dubbing, "admit_registered_episode_stage", blocked)
    monkeypatch.setattr(dubbing, "retry_group_dubbing", should_not_retry)
    monkeypatch.setattr(dubbing, "get_task_backend", lambda: SimpleNamespace())
    monkeypatch.setattr(dubbing, "get_task_manager", lambda: SimpleNamespace())
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing/retry",
        json={"task_id": failed.task_id, "failed_dialogue_ids": [line.dialogue_id],
              "request_id": "retry-request"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["required_stage"] == "screenplay"


def test_exact_group_dubbing_retry_replay_skips_new_submission_gate(monkeypatch, tmp_path):
    client, _sqlite, line = make_client(monkeypatch, tmp_path)
    store, preparation = _seed_preparation(tmp_path, line)
    retry, _created = store.create_task(
        episode=1, group_id="group-1", preparation_id=preparation.preparation_id,
        request_id="retry-request", payload_hash="c" * 64,
        dialogue_ids=(line.dialogue_id,),
    )
    store.update_task(retry.task_id, status="queued", backend_task_id="existing")

    def should_not_gate(*_args, **_kwargs):
        raise AssertionError("an exact durable retry replay must not be gated")

    async def replay(**_kwargs):
        return {"dubbing_task_id": retry.task_id, "task_id": "existing", "status": "queued"}

    monkeypatch.setattr(dubbing, "admit_registered_episode_stage", should_not_gate)
    monkeypatch.setattr(dubbing, "retry_group_dubbing", replay)
    monkeypatch.setattr(dubbing, "get_task_backend", lambda: SimpleNamespace())
    monkeypatch.setattr(dubbing, "get_task_manager", lambda: SimpleNamespace())
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/group-1/dubbing/retry",
        json={"task_id": "failed-prior", "failed_dialogue_ids": [line.dialogue_id],
              "request_id": "retry-request"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["data"]["task_id"] == "existing"


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
