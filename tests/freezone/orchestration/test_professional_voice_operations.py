from copy import deepcopy
from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError


def _voice_run():
    node = {
        "id": "voice",
        "type": "characterVoiceNode",
        "data": {
            "characterId": "character-1",
            "characterName": "阿远",
            "identityId": "identity-1",
            "slot": "default",
            "voiceDescription": "沉稳、清晰",
            "auditionText": "我回来了。",
        },
    }
    return {
        "project_id": "project-1",
        "actor": {"id": "user-1"},
        "snapshot": {"nodes": [node], "edges": []},
    }, node


@pytest.mark.asyncio
async def test_prepare_design_compiles_a_local_snapshot_without_enqueue(monkeypatch, tmp_path):
    from novelvideo.api.routes import characters
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.media_capabilities.tts import character_voice, voice_recommendation
    from novelvideo import ports

    run, node = _voice_run()
    character = SimpleNamespace(
        name="阿远", character_id="character-1",
        identities=[SimpleNamespace(identity_id="identity-1")],
    )
    store = SimpleNamespace(
        db_path=tmp_path / "project.db",
        get_character=lambda _name: character,
        get_all_characters=lambda: [character],
    )

    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(project_id="project-1"), "user-1", "project", tmp_path, tmp_path, store

    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    monkeypatch.setattr(character_voice, "prepare_character_voice_request", lambda *_a, **_k: {
        "character_name": "阿远", "slot": "default", "voice_description": "编译后的声线",
        "audition_text": "我回来了。", "language": "Auto", "profile_digest": "profile-1",
    })
    monkeypatch.setattr(voice_recommendation, "voice_project_context", lambda _db: {"style": "warm"})
    monkeypatch.setattr(character_voice, "voice_snapshot_digest", lambda value: "context-1" if value else "empty")

    def forbidden_backend():
        raise AssertionError("prepare_design must not enqueue paid generation")

    monkeypatch.setattr(ports, "get_task_backend", forbidden_backend)
    result = await voice.submit(run, {"id": "attempt-prepare"}, node, "prepare_design")

    assert result["local"] is True
    preview = result["result"]["voiceDesignPreview"]
    assert preview["request"]["voice_description"] == "编译后的声线"
    assert preview["request"]["design_context_digest"] == "context-1"
    assert preview["input_fingerprint"]


@pytest.mark.asyncio
async def test_design_consumes_matching_prepared_snapshot_and_rejects_stale_inputs(monkeypatch, tmp_path):
    from novelvideo.api.routes import characters
    from novelvideo.freezone.orchestration.adapters import voice
    from novelvideo.media_capabilities.tts import character_voice, voice_recommendation
    from novelvideo import ports

    run, node = _voice_run()
    character = SimpleNamespace(
        name="阿远", character_id="character-1",
        identities=[SimpleNamespace(identity_id="identity-1")],
    )
    store = SimpleNamespace(
        db_path=tmp_path / "project.db",
        get_character=lambda _name: character,
        get_all_characters=lambda: [character],
    )
    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(project_id="project-1"), "user-1", "project", tmp_path, tmp_path, store

    prepared = []
    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    def prepare(*_args, **_kwargs):
        prepared.append(True)
        return {"character_name": "阿远", "slot": "default", "voice_description": "编译后的声线",
                "audition_text": "我回来了。", "language": "Auto", "profile_digest": "profile-1"}
    monkeypatch.setattr(character_voice, "prepare_character_voice_request", prepare)
    monkeypatch.setattr(voice_recommendation, "voice_project_context", lambda _db: {"style": "warm"})
    monkeypatch.setattr(character_voice, "voice_snapshot_digest", lambda _value: "context-1")
    payloads = []
    async def enqueue(_ctx, **kwargs):
        payloads.append(kwargs["payload"])
        return SimpleNamespace(task_state=SimpleNamespace(task_id="task-1"), backend="local", queue="default")
    monkeypatch.setattr(ports, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))

    preview_receipt = await voice.submit(run, {"id": "prepare-attempt"}, node, "prepare_design")
    frozen_preview = preview_receipt["result"]["voiceDesignPreview"]
    tampered_preview = deepcopy(frozen_preview)
    tampered_preview["request"]["voice_description"] = "不在冻结快照中的声音"
    node["data"]["voiceDesignPreview"] = tampered_preview
    with pytest.raises(OrchestrationError) as tampered:
        await voice.submit(run, {"id": "tampered-design-attempt"}, node, "design")
    assert tampered.value.code == "input_conflict"
    assert payloads == []

    node["data"]["voiceDesignPreview"] = frozen_preview
    result = await voice.submit(run, {"id": "design-attempt"}, node, "design")

    assert result["task_type"] == "character_voice_design"
    assert payloads[0]["voice_description"] == "编译后的声线"
    assert len(prepared) == 3

    node["data"]["voiceDescription"] = "改过的声音"
    with pytest.raises(OrchestrationError) as exc:
        await voice.submit(run, {"id": "stale-attempt"}, node, "design")
    assert exc.value.code == "input_conflict"
    assert len(payloads) == 1
    assert len(prepared) == 3


def test_professional_voice_operations_are_registered_as_available():
    from novelvideo.freezone.orchestration.adapters import available_operations
    from novelvideo.freezone.orchestration.capabilities import capabilities

    available = available_operations()
    assert {("characterVoiceNode", "prepare_design"), ("characterVoiceNode", "design"),
            ("audioNode", "prepare_dubbing"), ("audioNode", "generate_dubbing")} <= available
    catalog = {entry["node_type"]: entry for entry in capabilities(available)}
    for node_type, operation in (
        ("characterVoiceNode", "prepare_design"),
        ("characterVoiceNode", "design"),
        ("audioNode", "prepare_dubbing"),
        ("audioNode", "generate_dubbing"),
    ):
        status = next(item for item in catalog[node_type]["operations"] if item["name"] == operation)
        assert status["available"] is True


def test_only_pure_voice_preview_skips_durable_submission_budget(tmp_path):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter

    adapter = NodeAdapter(tmp_path)
    run, voice_node = _voice_run()
    voice_node["data"]["operation"] = "prepare_design"
    attempt = {"node_id": "voice"}
    assert adapter.requires_external_submission(run, attempt) is False

    audio_run, audio_node = _dubbing_run()
    audio_node["data"]["operation"] = "prepare_dubbing"
    audio_attempt = {"node_id": "audio"}
    assert adapter.requires_external_submission(audio_run, audio_attempt) is True


def _dubbing_run():
    voice_node = {"id": "voice", "type": "characterVoiceNode", "data": {
        "characterId": "character-1", "identityId": "identity-1", "voiceVersionId": "version-1",
    }}
    audio_node = {"id": "audio", "type": "audioNode", "data": {
        "episodeNumber": 2, "groupId": "group-1", "dialogueIds": ["dialogue-1"],
        "performance": {"dialogue-1": {"mode": "neutral", "direction": "平静"}},
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"}, "snapshot": {
        "nodes": [voice_node, audio_node], "edges": [{"source": "voice", "target": "audio"}],
    }}
    return run, audio_node


def _dubbing_state():
    return {"ok": True, "data": {
        "group_id": "group-1", "episode": 2, "script_revision": "script-rev-1",
        "source_revision": "source-rev-1", "revision": 4,
        "dialogues": [{"dialogue_id": "dialogue-1", "character_id": "character-1",
                       "identity_id": "identity-1", "text": "试音文本"}],
        "tasks": [],
    }}


def _preparation_record():
    return {
        "preparation_id": "preparation-1", "episode": 2, "group_id": "group-1",
        "script_revision": "script-rev-1", "source_revision": "source-rev-1",
        "expected_revision": 4, "source_snapshot_hash": "a" * 64,
        "dialogue_ids": ["dialogue-1"], "source_dialogue_ids": ["dialogue-1"],
        "dialogues": [{"dialogue_id": "dialogue-1", "character_id": "character-1",
                       "identity_id": "identity-1", "speaker": "阿远", "group_id": "group-1",
                       "text": "试音文本", "voice_version_id": "version-1",
                       "performance": {"mode": "neutral"}}],
        "voice_versions": [{"character_id": "character-1", "identity_id": "identity-1",
                            "version_id": "version-1"}],
        "source_dialogues": [], "missing_voices": [], "issues": [],
    }


def _saved_preparation(body, node):
    from novelvideo.freezone.orchestration.adapters import dubbing

    result = _preparation_record()
    result["preparation_id"] = body.preparation_id
    result["requested_dialogue_ids"] = body.dialogue_ids
    bindings = [item.model_dump(mode="json") for item in body.voice_versions]
    result["request_fingerprint"] = dubbing._preparation_request_fingerprint(
        episode=2, group_id="group-1", script_revision=body.script_revision,
        requested_dialogue_ids=body.dialogue_ids,
        bindings=bindings,
        performance=node["data"]["performance"],
    )
    result["input_fingerprint"] = dubbing._preparation_input_fingerprint(
        episode=2, group_id="group-1", script_revision=body.script_revision,
        source_revision="source-rev-1", group_revision=4,
        dialogue_ids=["dialogue-1"],
        bindings=bindings,
        performance=node["data"]["performance"],
    )
    return result


@pytest.mark.asyncio
async def test_prepare_dubbing_returns_durable_snapshot_without_generating(monkeypatch):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, node = _dubbing_run()
    calls = {"prepare": [], "generate": []}
    saved = {}
    async def get(*_args):
        return _dubbing_state()
    async def historical(_run, _episode, _group, preparation_id):
        return saved.get(preparation_id)
    async def prepare(project, episode, group, body, actor):
        calls["prepare"].append(body)
        data = _saved_preparation(body, node)
        saved[body.preparation_id] = data
        return {"ok": True, "data": data}
    async def generate(*_args):
        calls["generate"].append(True)
        raise AssertionError("prepare_dubbing must not generate audio")
    monkeypatch.setattr(api, "get_dubbing", get)
    monkeypatch.setattr(api, "prepare_dubbing", prepare)
    monkeypatch.setattr(api, "generate_dubbing", generate)
    monkeypatch.setattr(dubbing, "_historical_preparation", historical)

    result = await dubbing.submit(run, {"id": "prepare-attempt"}, node, "prepare_dubbing")

    assert result["local"] is True
    frozen = result["result"]["dubbingPreparation"]
    assert frozen["preparation_id"].startswith("agent-prep-")
    assert frozen["expected_revision"] == 4
    assert frozen["input_fingerprint"]
    assert calls["prepare"][0].preparation_id
    assert calls["generate"] == []

    retried = await dubbing.submit(run, {"id": "prepare-attempt"}, node, "prepare_dubbing")
    assert retried["result"] == result["result"]
    assert len(calls["prepare"]) == 1

    node["data"]["operation"] = "prepare_dubbing"
    recovered = await dubbing.reconcile_submission(
        run, {"id": "prepare-attempt", "node_id": "audio"},
    )
    assert recovered["status"] == "submitted"
    assert recovered["submission"]["preparation_id"] == frozen["preparation_id"]
    assert len(calls["prepare"]) == 1

    node["data"]["performance"]["dialogue-1"]["direction"] = "改为愤怒"
    with pytest.raises(OrchestrationError) as exc:
        await dubbing.submit(run, {"id": "prepare-attempt"}, node, "prepare_dubbing")
    assert exc.value.code == "idempotency_conflict"
    assert len(calls["prepare"]) == 1


@pytest.mark.asyncio
async def test_generate_dubbing_consumes_matching_snapshot_without_repreparing(monkeypatch):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.freezone.orchestration.adapters import dubbing

    run, node = _dubbing_run()
    prepare_calls, generate_calls = [], []
    saved = {}
    async def get(*_args):
        return _dubbing_state()
    async def historical(_run, _episode, _group, preparation_id):
        return saved.get(preparation_id)
    async def prepare(_project, _episode, _group, body, _actor):
        prepare_calls.append(body)
        data = _saved_preparation(body, node)
        saved[body.preparation_id] = data
        return {"ok": True, "data": data}
    async def generate(project, episode, group, body, actor):
        generate_calls.append(body)
        return {"ok": True, "data": {
            "dubbing_task_id": "task-1", "task_id": "backend-1",
            "request_id": "generate-attempt", "status": "queued",
        }}
    monkeypatch.setattr(api, "get_dubbing", get)
    monkeypatch.setattr(api, "prepare_dubbing", prepare)
    monkeypatch.setattr(api, "generate_dubbing", generate)
    monkeypatch.setattr(dubbing, "_historical_preparation", historical)

    prepared = await dubbing.submit(run, {"id": "prepare-attempt"}, node, "prepare_dubbing")
    node["data"]["dubbingPreparation"] = prepared["result"]["dubbingPreparation"]
    result = await dubbing.submit(run, {"id": "generate-attempt"}, node, "generate_dubbing")

    assert len(prepare_calls) == 1
    assert generate_calls[0].preparation_id.startswith("agent-prep-")
    assert generate_calls[0].expected_revision == 4
    assert generate_calls[0].request_id == "generate-attempt"
    assert result["preparation_id"] == prepared["preparation_id"]
    assert result["dubbing_task_id"] == "task-1"

    node["data"]["performance"]["dialogue-1"]["direction"] = "改为愤怒"
    with pytest.raises(OrchestrationError) as exc:
        await dubbing.submit(run, {"id": "stale-generate"}, node, "generate_dubbing")
    assert exc.value.code == "input_conflict"
    assert len(generate_calls) == 1

