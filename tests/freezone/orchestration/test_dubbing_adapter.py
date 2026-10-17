from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError


def _inputs(*, group="group-1", episode=2, request_id="attempt-1"):
    run = {
        "project_id": "project-1", "actor": {"id": "user-1"},
        "snapshot": {
            "nodes": [
                {"id": "voice", "type": "characterVoiceNode", "data": {
                    "characterId": "character-1", "identityId": "identity-1",
                    "voiceVersionId": "version-1",
                }},
                {"id": "audio", "type": "audioNode", "data": {
                    "episodeNumber": episode, "groupId": group,
                    "dialogueIds": ["dialogue-1"],
                    "performance": {"dialogue-1": {"mode": "neutral"}},
                }},
            ],
            "edges": [{"source": "voice", "target": "audio"}],
        },
    }
    attempt = {"id": request_id, "node_id": "audio"}
    node = run["snapshot"]["nodes"][1]
    return run, attempt, node


def _audition_inputs(*, request_id="audition-attempt-1", version_id="version-1"):
    run, attempt, node = _inputs(request_id=request_id)
    run["snapshot"]["nodes"][0]["data"].update({
        "characterName": "阿远", "slot": "default",
    })
    node["data"].update({
        "operation": "audition",
        "auditionDraft": {
            "text": "我回来了。", "emotionPrompt": "平静",
            "voiceVersionId": version_id,
        },
    })
    return run, attempt, node


def _stub_audition_runtime(monkeypatch, task):
    from novelvideo.api.routes import freezone
    from novelvideo.freezone.orchestration.adapters import dubbing

    enqueue_calls = []
    context_roles = []
    receipts = {}
    ctx = SimpleNamespace(requester_username="requester", owner_username="owner")

    async def resolve(_run, *, role):
        context_roles.append(role)
        return ctx, "project-dir"

    async def enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        receipts[kwargs["job_id"]] = task

    monkeypatch.setattr(dubbing, "_audition_context", resolve)
    monkeypatch.setattr(dubbing, "_audition_task", lambda _ctx, job_id: receipts.get(job_id))
    monkeypatch.setattr(freezone, "_enqueue_freezone_background_job", enqueue)
    return enqueue_calls, context_roles


def _forbid_formal_dubbing_writes(monkeypatch):
    from novelvideo.api.routes import dubbing as api

    def forbidden(*_args, **_kwargs):
        raise AssertionError("free audition must not write formal dubbing records")

    for name in ("prepare_dubbing", "generate_dubbing", "adopt_dubbing"):
        if hasattr(api, name):
            monkeypatch.setattr(api, name, forbidden)


@pytest.mark.asyncio
async def test_submit_prepares_with_pinned_versions_then_generates_once(monkeypatch):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, node = _inputs()
    state = {"ok": True, "data": {
        "group_id": "group-1", "episode": 2, "script_revision": "script-rev-1",
        "dialogues": [{"dialogue_id": "dialogue-1", "character_id": "character-1",
                        "identity_id": "identity-1", "text": "试音文本"}],
        "tasks": [],
    }}
    calls = {"prepare": [], "generate": []}
    async def get(_project, _episode, _group, _actor):
        return state
    async def prepare(project, episode, group, body, actor):
        calls["prepare"].append((project, episode, group, body, actor))
        from novelvideo.audio.dubbing_contracts import DialogueSnapshot, DubbingPreparation
        line = DialogueSnapshot(
            dialogue_id="dialogue-1", character_id="character-1", identity_id="identity-1",
            speaker="阿远", script_revision=body.script_revision, source_revision="source-rev-1",
            group_id="group-1", text="试音文本", voice_version_id="version-1",
            performance={"mode": "neutral"},
        )
        preparation = DubbingPreparation(
            preparation_id=body.preparation_id, episode=episode, group_id=group,
            script_revision=body.script_revision, source_revision="source-rev-1",
            expected_revision=4, source_snapshot_hash="a" * 64,
            dialogue_ids=("dialogue-1",), dialogues=(line,),
            voice_versions=tuple(body.voice_versions),
        )
        return {"ok": True, "data": preparation.model_dump(mode="json")}
    async def generate(project, episode, group, body, actor):
        calls["generate"].append((project, episode, group, body, actor))
        return {"ok": True, "data": {
            "dubbing_task_id": "internal-task-1", "task_id": "backend-task-1",
            "request_id": "attempt-1", "status": "queued",
        }}
    async def forbidden_adopt(*_args, **_kwargs):
        raise AssertionError("the agent adapter must not adopt dubbing takes")
    monkeypatch.setattr(api, "get_dubbing", get)
    monkeypatch.setattr(api, "prepare_dubbing", prepare)
    monkeypatch.setattr(api, "generate_dubbing", generate)
    monkeypatch.setattr(api, "adopt_dubbing", forbidden_adopt)
    monkeypatch.setattr(dubbing, "_historical_preparation", lambda *_args: _async(None))

    result = await dubbing.submit(run, attempt, node, "group_dubbing")

    assert result["dubbing"] is True
    assert result["request_id"] == "attempt-1"
    assert result["dubbing_task_id"] == "internal-task-1"
    assert calls["prepare"][0][3].script_revision == "script-rev-1"
    assert calls["prepare"][0][3].dialogue_ids == ["dialogue-1"]
    assert [item.model_dump(mode="json") for item in calls["prepare"][0][3].voice_versions] == [
        {"character_id": "character-1", "identity_id": "identity-1", "version_id": "version-1"}
    ]
    assert calls["generate"][0][3].request_id == "attempt-1"


@pytest.mark.asyncio
async def test_submit_recovers_same_request_receipt_without_repreparing_or_generating(monkeypatch):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, node = _inputs()
    async def get(*_args):
        return {"ok": True, "data": {"tasks": [{
            "task_id": "internal-task-1", "backend_task_id": "backend-task-1",
            "request_id": "attempt-1", "preparation_id": "prep-1", "status": "running",
            "dialogue_ids": ["dialogue-1"],
        }]}}
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("a durable request receipt must prevent resubmission")
    monkeypatch.setattr(api, "get_dubbing", get)
    monkeypatch.setattr(api, "prepare_dubbing", forbidden)
    monkeypatch.setattr(api, "generate_dubbing", forbidden)

    result = await dubbing.submit(run, attempt, node, "group_dubbing")

    assert result["dubbing_task_id"] == "internal-task-1"
    assert result["task_id"] == "backend-task-1"
    assert result["preparation_id"] == "prep-1"


@pytest.mark.asyncio
async def test_reconcile_submission_confirms_missing_durable_task_receipt(monkeypatch):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, _ = _inputs()
    monkeypatch.setattr(api, "get_dubbing", lambda *_args: _async({"ok": True, "data": {"tasks": []}}))

    result = await dubbing.reconcile_submission(run, attempt)

    assert result["status"] == "not_submitted"
    assert result["evidence"] == "no_dubbing_task_receipt"


@pytest.mark.asyncio
async def test_reconcile_emits_exact_input_reused_take_without_adopting(monkeypatch):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, _ = _inputs()
    from novelvideo.audio.dubbing_store import line_input_hash

    line = {
        "dialogue_id": "dialogue-1", "character_id": "character-1",
        "identity_id": "identity-1", "text": "试音文本",
        "voice_version_id": "version-1", "performance": {"mode": "neutral"},
    }
    state = {"ok": True, "data": {
        "tasks": [{"task_id": "internal-task-1", "request_id": "attempt-1",
                   "preparation_id": "prep-1", "status": "completed",
                   "dialogue_ids": ["dialogue-1"], "failed_dialogue_ids": [], "error": ""}],
        "takes": [
            {"take_id": "take-reused", "task_id": "older-task", "dialogue_id": "dialogue-1",
             "status": "succeeded", "url": "/current.wav", "audio_sha256": "a" * 64,
             "technical_status": "passed", "input_hash": line_input_hash(line),
             "voice_version_id": "version-1", "preparation_id": "old-prep"},
            {"take_id": "take-prior", "task_id": "older-task", "dialogue_id": "dialogue-1",
             "status": "succeeded", "url": "/old.wav", "audio_sha256": "b" * 64,
             "technical_status": "passed", "input_hash": "b" * 64,
             "voice_version_id": "version-1", "preparation_id": "old-prep"},
        ],
        "preparation": {"preparation_id": "prep-1", "dialogues": [line]},
    }}
    monkeypatch.setattr(api, "get_dubbing", lambda *_args: _async(state))

    result = await dubbing.reconcile(run, {
        "dubbing": True, "dubbing_task_id": "internal-task-1", "request_id": "attempt-1",
        "group_id": "group-1", "episode": 2, "preparation_id": "prep-1",
    })

    assert result["status"] == "succeeded"
    assert [item["take_id"] for item in result["result"]["dubbingCandidates"]] == ["take-reused"]
    assert result["result"]["dubbingStatus"] == "completed"
    assert result["result"]["dubbingCandidates"][0]["voice_version_id"] == "version-1"


@pytest.mark.asyncio
async def test_audition_submits_fixed_voice_version_without_dialogue_or_timeline_writes(monkeypatch):
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, node = _audition_inputs()
    task = SimpleNamespace(task_id="free-audio-task-1", status="queued")
    enqueue_calls, context_roles = _stub_audition_runtime(monkeypatch, task)
    _forbid_formal_dubbing_writes(monkeypatch)

    result = await dubbing.submit(run, attempt, node, "audition")

    assert len(enqueue_calls) == 1
    queued = enqueue_calls[0]
    assert queued["task_type"] == "freezone_audio_speech"
    assert queued["payload"]["text"] == "我回来了。"
    assert queued["payload"]["emotion_prompt"] == "平静"
    assert queued["payload"]["voice_ref"] == {
        "scope": "character_voice_version", "character_name": "阿远",
        "character_id": "character-1", "identity_id": "identity-1",
        "slot": "default", "voice_id": "", "version_id": "version-1",
    }
    assert queued["payload"]["target_episode"] is None
    assert queued["payload"]["target_beat"] is None
    assert not {"dialogue_id", "dialogue_ids", "timeline", "take_id"} & set(queued["payload"])
    assert context_roles == ["editor"]
    assert result["audio_audition"] is True
    assert result["task_type"] == "freezone_audio_speech"
    assert result["task_id"] == "free-audio-task-1"
    assert result["voice_version_id"] == "version-1"
    assert result["request_id"] == "audition-attempt-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version_id", "expected_code"),
    [
        ("https://example.invalid/voice.wav", "invalid_input"),
        ("unbound-version", "input_required"),
    ],
)
async def test_audition_rejects_arbitrary_url_and_unbound_voice_versions_before_enqueue(
    monkeypatch, version_id, expected_code,
):
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, node = _audition_inputs(version_id=version_id)
    task = SimpleNamespace(task_id="must-not-be-created", status="queued")
    enqueue_calls, _context_roles = _stub_audition_runtime(monkeypatch, task)

    with pytest.raises(OrchestrationError) as exc:
        await dubbing.submit(run, attempt, node, "audition")

    assert exc.value.code == expected_code
    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_audition_reconcile_projects_only_the_receipted_audition_result(monkeypatch):
    from novelvideo.api.routes import freezone
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, attempt, node = _audition_inputs(request_id="audition-receipt-1")
    task = SimpleNamespace(task_id="free-audio-task-2", status="running", error="")
    enqueue_calls, context_roles = _stub_audition_runtime(monkeypatch, task)
    _forbid_formal_dubbing_writes(monkeypatch)
    result_lookups = []

    async def get_result(project, task_type, job_id, actor):
        result_lookups.append((project, task_type, job_id, actor))
        return {"ok": True, "data": {"url": "/projects/project-1/audition.wav"}}

    monkeypatch.setattr(freezone, "freezone_job_result", get_result)

    submission = await dubbing.submit(run, attempt, node, "audition")
    task.status = "completed"
    reconciled = await dubbing.reconcile(run, submission)

    assert len(enqueue_calls) == 1
    assert context_roles == ["editor", "viewer"]
    assert result_lookups == [(
        "project-1", "freezone_audio_speech", submission["job_id"], run["actor"],
    )]
    assert reconciled == {"status": "succeeded", "result": {
        "auditionResult": {
            "url": "/projects/project-1/audition.wav",
            "taskId": "free-audio-task-2",
            "jobId": submission["job_id"],
            "voiceVersionId": "version-1",
            "text": "我回来了。",
        },
    }}


async def _async(value):
    return value
