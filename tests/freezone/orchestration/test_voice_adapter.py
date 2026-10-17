from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_submit_uses_attempt_id_as_task_and_candidate_idempotency_key(monkeypatch, tmp_path):
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.api.routes import characters
    from novelvideo.media_capabilities.tts import character_voice, voice_recommendation
    from novelvideo import ports

    character = SimpleNamespace(
        name="阿远", character_id="character-1",
        identities=[SimpleNamespace(identity_id="identity-1")],
    )
    store = SimpleNamespace(
        db_path=tmp_path / "data.db", get_character=lambda name: character,
        get_all_characters=lambda: [character],
    )
    ctx = SimpleNamespace(project_id="p")

    async def resolve(project, actor, **kwargs):
        return ctx, "u", "p", tmp_path, tmp_path, store

    payloads = []
    prepare_calls = []
    def prepare(*args, **kwargs):
        prepare_calls.append((args, kwargs))
        return {"character_name": "阿远", "slot": kwargs["slot"], "profile_digest": "digest"}

    async def enqueue(_ctx, **kwargs):
        payloads.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id="task-1"), backend="local", queue="default")

    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    monkeypatch.setattr(character_voice, "prepare_character_voice_request", prepare)
    monkeypatch.setattr(voice_recommendation, "voice_project_context", lambda _db: {"style": "warm"})
    monkeypatch.setattr(character_voice, "voice_snapshot_digest", lambda _context: "context-digest")
    monkeypatch.setattr(ports, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))

    attempt = {"id": "attempt-123"}
    node = {"id": "voice", "type": "characterVoiceNode", "data": {
        "characterId": "character-1", "characterName": "阿远（旧名）", "slot": "elder",
        "voiceDescription": "沉稳", "auditionText": "我回来了。", "identityId": "identity-1",
    }}
    result = await voice.submit({"project_id": "p", "actor": {"id": "u"}}, attempt, node, "design")

    assert result["scope"] == "attempt-123"
    assert result["task_type"] == "character_voice_design"
    assert payloads[0]["scope"] == "attempt-123"
    assert payloads[0]["payload"]["request_id"] == "attempt-123"
    assert payloads[0]["payload"]["identity_id"] == "identity-1"
    assert payloads[0]["payload"]["design_context_digest"] == "context-digest"
    assert prepare_calls[0][1]["language"] == "Auto"
    assert prepare_calls[0][1]["slot"] == "elder"
    assert prepare_calls[0][0][0].name == "阿远"
    assert "voice_version_id" not in result


@pytest.mark.asyncio
async def test_reconcile_submission_recovers_existing_task_receipt(monkeypatch):
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.api.routes import characters
    from novelvideo import task_state

    ctx = SimpleNamespace(project_id="p")
    store = SimpleNamespace(db_path="unused")
    async def resolve(*_args, **_kwargs):
        return ctx, "u", "p", ".", ".", store
    task = SimpleNamespace(task_id="task-1", status="running")
    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    monkeypatch.setattr(task_state, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_args, **kwargs: task if kwargs.get("scope") == "attempt-123" else None))

    result = await voice.reconcile_submission(
        {"project_id": "p", "actor": {}}, {"id": "attempt-123", "submission": None})

    assert result["status"] == "submitted"
    assert result["submission"]["scope"] == "attempt-123"
    assert result["submission"]["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_reconcile_submission_keeps_missing_receipts_unknown(monkeypatch):
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.api.routes import characters
    from novelvideo import task_state

    ctx = SimpleNamespace(project_id="p")
    store = SimpleNamespace(db_path="unused")
    async def resolve(*_args, **_kwargs):
        return ctx, "u", "p", ".", ".", store
    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    monkeypatch.setattr(task_state, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(voice, "_candidate_store", lambda *_args: object())
    monkeypatch.setattr(voice, "_candidate_for_request", lambda *_args: None)

    result = await voice.reconcile_submission(
        {"project_id": "p", "actor": {}}, {"id": "attempt-123", "submission": None})

    assert result["status"] == "submission_unknown"


@pytest.mark.asyncio
async def test_reconcile_submission_uses_candidate_as_durable_receipt(monkeypatch):
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.api.routes import characters
    from novelvideo import task_state

    ctx = SimpleNamespace(project_id="p")
    store = SimpleNamespace(db_path="unused")
    async def resolve(*_args, **_kwargs):
        return ctx, "u", "p", ".", ".", store
    candidate = {"candidate_id": "candidate-1", "path": "", "report": {}}
    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    monkeypatch.setattr(task_state, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(voice, "_candidate_store", lambda *_args: object())
    monkeypatch.setattr(voice, "_candidate_for_request", lambda *_args: candidate)

    result = await voice.reconcile_submission(
        {"project_id": "p", "actor": {}}, {"id": "attempt-123", "submission": None})

    assert result["status"] == "submitted"
    assert result["submission"]["candidate_id"] == "candidate-1"


@pytest.mark.asyncio
async def test_reconcile_emits_candidates_without_adopting(monkeypatch):
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.api.routes import characters, voice_candidates
    from novelvideo import task_state

    ctx = SimpleNamespace(project_id="p")
    candidate_store = SimpleNamespace(
        get=lambda candidate_id: {"candidate_id": candidate_id, "request_id": "attempt-123", "path": "voice.wav", "sha256": "a" * 64},
        project_dir=".")
    store = SimpleNamespace(db_path="unused")
    async def resolve(*_args, **_kwargs):
        return ctx, "u", "p", ".", ".", store
    task = SimpleNamespace(task_id="task-1", status="completed", result={"candidate_id": "candidate-1"}, error=None)
    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    monkeypatch.setattr(task_state, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_args, **_kwargs: task))
    monkeypatch.setattr(voice, "_candidate_store", lambda _store, _project_dir: candidate_store)
    monkeypatch.setattr(voice, "_candidate_for_request", lambda _store, _request_id: candidate_store.get("candidate-1"))
    monkeypatch.setattr(voice_candidates, "candidate_view", lambda _ctx, _store, row: {**row, "url": "/voice.wav"})

    result = await voice.reconcile(
        {"project_id": "p", "actor": {}}, {"scope": "attempt-123", "task_type": "character_voice_design"})

    assert result["status"] == "succeeded"
    assert result["result"]["voiceCandidates"][0]["candidate_id"] == "candidate-1"
    assert result["result"]["voiceCandidates"][0]["url"] == "/voice.wav"


@pytest.mark.asyncio
async def test_verify_checks_candidate_audio_and_attempt_ownership(monkeypatch, tmp_path):
    from novelvideo.freezone.orchestration.adapters import voice

    ctx = SimpleNamespace(project_id="p")
    candidate = {"candidate_id": "candidate-1", "request_id": "attempt-123",
                 "path": "assets/voice.wav", "sha256": "a" * 64}
    reads = []
    candidate_store = SimpleNamespace(
        get=lambda _candidate_id: candidate,
        read_audio=lambda candidate_id: reads.append(candidate_id) or b"audio",
    )
    async def resolve(*_args, **_kwargs):
        return ctx, tmp_path, SimpleNamespace(db_path="unused")

    monkeypatch.setattr(voice, "_resolve_scope", resolve)
    monkeypatch.setattr(voice, "_candidate_store", lambda *_args: candidate_store)
    artifacts = await voice.verify(
        {"project_id": "p", "actor": {}},
        {"id": "attempt-123", "result": {"voiceCandidates": [{"candidate_id": "candidate-1"}]}},
    )

    assert reads == ["candidate-1"]
    assert artifacts == [{"kind": "audio", "sha256": "a" * 64,
                          "path": "assets/voice.wav", "candidate_id": "candidate-1"}]

