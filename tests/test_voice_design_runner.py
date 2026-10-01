from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_voice_design_loads_sqlite_character_state_before_lookup(
    monkeypatch, tmp_path
):
    from novelvideo.models import NovelCharacter
    from novelvideo.task_backend.runners import voice_design

    character = NovelCharacter(name="阿远")
    calls: list[str] = []

    class FakeSQLiteStore:
        def __init__(self, *_args, **_kwargs):
            self.loaded = False

        async def initialize(self):
            calls.append("initialize")

        async def load_graph_state(self):
            self.loaded = True
            calls.append("load_graph_state")

        def get_character(self, name):
            calls.append("get_character")
            return character if self.loaded and name == "阿远" else None

        async def update_character(self, *_args, **_kwargs):
            calls.append("update_character")

        async def close(self):
            calls.append("close")

    async def fake_generate(*_args, **_kwargs):
        return b"wav", "sample.wav"

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_runninghub_runtime_configuration",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.tts.runninghub_voice_design.generate_qwen3_voice_sample",
        fake_generate,
    )
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.character_voice_storage.persist_character_voice_file",
        lambda **_kwargs: ("audio/sample.wav", "sha256", "now"),
    )
    monkeypatch.setattr(
        voice_design,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())

    ctx = SimpleNamespace(
        owner_project_label="local/demo",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="snapshot"):
        await voice_design._run_voice_design(
        {
            "scope": "character:阿远:voice:default",
            "payload": {
                "character_name": "阿远",
                "slot": "default",
                "audition_text": "我是阿远。",
                "voice_description": "青年男性，中低音。",
            },
        },
        ctx,
    )

    assert calls.index("load_graph_state") < calls.index("get_character")
    assert "update_character" not in calls


@pytest.mark.asyncio
async def test_voice_design_retains_candidate_and_existing_reference(monkeypatch, tmp_path):
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.task_backend.runners import voice_design

    ctx = SimpleNamespace(owner_project_label="voice_test", output_dir=tmp_path, state_dir=tmp_path)
    store = SQLiteStore("voice_test", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    role = NovelCharacter(name="阿远", reference_audio_path="old.wav", reference_audio_sha256="old",
                          voice_facts={"vocalization_mode": "dialogue", "provenance": "human"})
    await store.add_character(role)
    await store.close()
    payload = prepare_character_voice_request(role, slot="elder")
    payload["request_id"] = "request-1"
    submitted = []
    async def fake_generate(*args, **kwargs):
        submitted.append(kwargs)
        await kwargs["on_submitted"]("provider-task-1")
        return b"invalid-audio-candidate", "voice.wav"
    monkeypatch.setattr("novelvideo.media_capabilities.tts.runninghub_voice_design.generate_qwen3_voice_sample", fake_generate)
    monkeypatch.setattr("novelvideo.media_capabilities.runtime.configuration.load_runninghub_runtime_configuration", lambda *a: SimpleNamespace(workflow_id=lambda capability: "qwen-workflow"))
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())
    monkeypatch.setattr(voice_design, "get_task_manager", lambda: SimpleNamespace(update_progress_for_project=lambda *a, **k: None))
    result = await voice_design._run_voice_design({"payload": payload}, ctx)
    assert result["quality_status"] == "rejected"
    assert result["published"] is False
    candidates = VoiceCandidateStore(tmp_path / "data.db", tmp_path)
    assert candidates.get(result["candidate_id"])["provider_task_id"] == "provider-task-1"
    again = await voice_design._run_voice_design({"payload": payload}, ctx)
    assert again["candidate_id"] == result["candidate_id"]
    assert len(submitted) == 1
    store = SQLiteStore("voice_test", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.load_graph_state()
    assert store.get_character("阿远").reference_audio_path == "old.wav"
    await store.close()
