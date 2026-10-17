from __future__ import annotations

import hashlib
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest


def _preparation(dialogues, *, preparation_id="prep-1"):
    from novelvideo.audio.dubbing_contracts import DubbingPreparation

    snapshot_hash = hashlib.sha256(
        "|".join(item.dialogue_id for item in dialogues).encode()
    ).hexdigest()
    return DubbingPreparation(
        preparation_id=preparation_id,
        episode=1,
        group_id="group-1",
        script_revision="script-r1",
        source_revision="video-r1:source-hash",
        expected_revision=0,
        source_snapshot_hash=snapshot_hash,
        dialogue_ids=tuple(item.dialogue_id for item in dialogues),
        dialogues=tuple(dialogues),
    )


@pytest.mark.parametrize(
    ("provider", "model", "endpoint"),
    (
        ("newapi", "index-tts-2", "https://gateway.example/v1"),
        ("fal", "IndexTTS2", "https://fal.example/index-tts-2"),
    ),
)
def test_selected_provider_compiles_index_v2_and_maps_text_emotion(monkeypatch, provider, model, endpoint):
    from novelvideo import config
    from novelvideo.audio.dubbing_service import compile_dialogue_performance
    from novelvideo.task_backend.runners import narrative_group_dubbing as runner

    monkeypatch.setattr(config, "INDEXTTS2_PROVIDER", provider)
    monkeypatch.setattr(config, "INDEXTTS2_NEWAPI_MODEL", "index-tts-2")
    monkeypatch.setattr(config, "INDEXTTS2_FAL_ENDPOINT", endpoint)
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(base_url=endpoint, api_key="secret"),
    )

    compiled = compile_dialogue_performance(
        "别进去。", {"mode": "emotion_text", "payload": "压低声音", "direction": "克制"}
    )

    assert compiled["index_provider"] == provider
    assert compiled["model"] == model
    assert compiled["workflow_version"].startswith(f"IndexTTS2-v2:{provider}:")
    assert compiled["compiler_version"] == "1"
    assert compiled["effective"] == {
        "text": "别进去。",
        "mode": "text",
        "emotion_text": "压低声音",
    }
    generator = runner._configured_generator({"provider": provider, "model": model})
    assert generator.provider == provider
    assert generator.model == model


def test_configured_index_provider_rejects_unavailable_emotion_modes_before_submission(monkeypatch):
    from novelvideo import config
    from novelvideo.audio.dubbing_service import compile_dialogue_performance

    monkeypatch.setattr(config, "INDEXTTS2_PROVIDER", "newapi")
    monkeypatch.setattr(config, "INDEXTTS2_NEWAPI_MODEL", "index-tts-2")
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(base_url="https://gateway.example/v1", api_key="secret"),
    )

    with pytest.raises(ValueError, match="emotion_mode_not_supported"):
        compile_dialogue_performance(
            "别进去。", {"mode": "emotion_audio", "payload": "assets/emotion.wav"}
        )


@pytest.mark.asyncio
async def test_newapi_compiled_text_emotion_maps_to_existing_generator_parameters(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.audio.dubbing_service import _synthesize, compile_dialogue_performance
    from novelvideo.generators.indextts2_fal import IndexTTS2FalClient

    monkeypatch.setattr(config, "INDEXTTS2_PROVIDER", "newapi")
    monkeypatch.setattr(config, "INDEXTTS2_NEWAPI_MODEL", "index-tts-2")
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(base_url="https://gateway.example/v1", api_key="secret"),
    )
    compiled = compile_dialogue_performance(
        "别进去。", {"mode": "emotion_text", "payload": "压住怒气"}
    )
    provider_signature = __import__("inspect").signature(IndexTTS2FalClient.generate)
    assert {"prompt", "audio_url", "output_path", "emotion_prompt"}.issubset(provider_signature.parameters)

    class ExistingAdapterShape:
        calls = []

        async def generate(self, *, prompt, audio_url, output_path, emotion_prompt=""):
            self.calls.append((prompt, audio_url, output_path, emotion_prompt))
            return SimpleNamespace(success=True)

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"wave")
    adapter = ExistingAdapterShape()
    await _synthesize(
        generator=adapter,
        text="别进去。",
        reference_path=reference,
        output_path=tmp_path / "take.wav",
        compiled=compiled,
    )
    prompt, audio_url, _output, emotion_prompt = adapter.calls[0]
    assert prompt == "别进去。"
    assert audio_url.startswith("data:audio/x-wav;base64,")
    assert emotion_prompt == "压住怒气"


@pytest.mark.asyncio
async def test_unknown_submission_reconciles_without_resubmitting(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.audio.dubbing_contracts import DialogueSnapshot, DubbingPreparation
    from novelvideo.audio.dubbing_service import (
        compile_dialogue_performance,
        enqueue_group_dubbing,
    )
    from novelvideo.audio.dubbing_store import DubbingStore

    monkeypatch.setattr(config, "INDEXTTS2_PROVIDER", "fal")
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(base_url="https://gateway.example/v1", api_key="secret"),
    )
    line = DialogueSnapshot(
        dialogue_id="line-1",
        character_id="char-1",
        group_id="group-1",
        text="出发。",
        voice_version_id="voice-v1",
        performance=compile_dialogue_performance("出发。", {"mode": "neutral"}),
    )
    prep = DubbingPreparation(
        preparation_id="prep-1",
        episode=1,
        group_id="group-1",
        script_revision="script-r1",
        source_revision="video-r1:source",
        expected_revision=0,
        source_snapshot_hash=hashlib.sha256(b"snapshot").hexdigest(),
        dialogue_ids=(line.dialogue_id,),
        dialogues=(line,),
        source_dialogues=(line,),
    )
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(prep)
    monkeypatch.setattr(
        "novelvideo.audio.dubbing_service.validate_preparation_current", lambda *_: None
    )

    class Backend:
        calls = 0

        async def enqueue_project_task(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(task_state=SimpleNamespace(task_id=""))

    class TaskManager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

    backend = Backend()
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        state_dir=str(tmp_path),
        project_id="demo",
    )
    kwargs = dict(
        ctx=ctx,
        dubbing_store=store,
        preparation=prep,
        expected_revision=0,
        request_id="request-1",
        task_backend=backend,
        task_manager=TaskManager(),
    )

    first = await enqueue_group_dubbing(**kwargs)
    second = await enqueue_group_dubbing(**kwargs)

    assert first["status"] == "submission_unknown"
    assert second["status"] == "submission_unknown"
    assert backend.calls == 1


@pytest.mark.asyncio
async def test_group_runner_generates_candidate_takes_at_original_speed_and_keeps_manifest_untouched(tmp_path, monkeypatch):
    from novelvideo.audio.dubbing_contracts import DialogueSnapshot
    from novelvideo.audio.dubbing_service import run_dubbing_task
    from novelvideo.audio.dubbing_store import DubbingStore
    from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
    from novelvideo.audio import narrative_group_dubbing
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorOutputManifest,
        H3DirectorSegment,
        build_h3_timeline_data,
        save_h3_director_manifest,
    )

    voice_path = tmp_path / "voice.wav"
    voice_path.write_bytes(b"voice")
    project_manifest = tmp_path / "group.manifest.json"
    segment = H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="p", first_frame="frame.png",
        duration_seconds=9,
        dialogue_lines=({"speaker": "张三", "text": "别进去。"},),
    )
    save_h3_director_manifest(
        project_manifest,
        H3DirectorOutputManifest(
            physical_video=str(tmp_path / "video.mp4"),
            entries=build_h3_timeline_data((segment,)).entries,
        ),
    )
    original_manifest = project_manifest.read_bytes()
    dialogue = DialogueSnapshot(
        dialogue_id="dialogue-1", character_id="character-1", identity_id="adult",
        speaker="张三", script_revision="script-r1", source_revision="video-r1:source-hash",
        group_id="group-1", text="别进去。", voice_version_id="voice-v1",
        target_duration_seconds=9, performance={"mode": "neutral", "direction": "压住怒气"},
    )
    preparation = _preparation([dialogue])
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(preparation)
    task, _ = store.create_task(
        episode=1, group_id="group-1", preparation_id=preparation.preparation_id,
        request_id="request-1", payload_hash="a" * 64,
        dialogue_ids=(dialogue.dialogue_id,),
    )

    class FakeGenerator:
        calls = []

        async def generate(self, *, prompt, reference_audio_path, output_path, emotion_prompt="", emotion_audio_path=None, emotion_vector=None, emotion_alpha=None):
            self.calls.append((prompt, Path(reference_audio_path), emotion_prompt, emotion_audio_path, emotion_vector))
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(output), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b"\0\0" * 80000)
            return SimpleNamespace(success=True, audio_path=str(output))

    generator = FakeGenerator()

    class Versions:
        def get_version(self, version_id):
            assert version_id == "voice-v1"
            return {
                "version_id": version_id,
                "character_id": "character-1",
                "identity_id": "adult",
                "path": str(voice_path),
                "audio_sha256": hashlib.sha256(voice_path.read_bytes()).hexdigest(),
            }

    async def fixed_duration(_):
        return 10.0

    monkeypatch.setattr(narrative_group_dubbing, "_probe_audio_duration", fixed_duration, raising=False)
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure("test-index", max_concurrency=3, capability_limits={"tts.voice_clone": 3})

    result = await run_dubbing_task(
        store=store,
        task_id=task.task_id,
        project_dir=tmp_path,
        sqlite_store=SimpleNamespace(),
        voice_versions=Versions(),
        generator=generator,
        coordinator=coordinator,
        provider_id="test-index",
    )

    assert result["status"] == "completed"
    assert len(generator.calls) == 1
    assert generator.calls[0][0] == "别进去。"
    take = store.get_take(result["take_ids"][0])
    assert take.actual_duration_seconds == 10.0
    assert take.target_duration_seconds == 9.0
    assert take.duration_delta_seconds == 1.0
    assert take.provider == "newapi"
    assert take.model == "index-tts-2"
    assert take.compiler_version == "1"
    assert store.get_group_state(1, "group-1")["adoptions"] == {}
    assert project_manifest.read_bytes() == original_manifest


@pytest.mark.asyncio
async def test_missing_fixed_voice_stops_before_any_index_generation(tmp_path):
    from novelvideo.audio.dubbing_contracts import DialogueSnapshot
    from novelvideo.audio.dubbing_service import run_dubbing_task
    from novelvideo.audio.dubbing_store import DubbingStore
    from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator

    line = DialogueSnapshot(
        dialogue_id="dialogue-1", character_id="character-1", identity_id="adult",
        speaker="张三", script_revision="script-r1", source_revision="video-r1:source-hash",
        group_id="group-1", text="别进去。", voice_version_id="missing-voice-version",
    )
    preparation = _preparation([line])
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(preparation)
    task, _ = store.create_task(
        episode=1, group_id="group-1", preparation_id=preparation.preparation_id,
        request_id="request-1", payload_hash="a" * 64,
        dialogue_ids=(line.dialogue_id,),
    )

    class Versions:
        def get_version(self, _):
            return None

    class Generator:
        calls = 0

        async def generate(self, **_):
            self.calls += 1

    generator = Generator()
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure("test-index", max_concurrency=1)

    result = await run_dubbing_task(
        store=store, task_id=task.task_id, project_dir=tmp_path,
        sqlite_store=SimpleNamespace(), voice_versions=Versions(), generator=generator,
        coordinator=coordinator, provider_id="test-index",
    )

    assert result["status"] == "failed"
    assert result["failed_dialogue_ids"] == ["dialogue-1"]
    assert generator.calls == 0

