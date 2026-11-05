"""Acceptance for imported role voice selection through external-TTS composition.

Only the paid TTS transport and the Demucs separation boundary are faked. Voice
candidate import/review/version selection, the narrative-group audio runner,
FFmpeg preparation, and episode composition use their production implementations.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT = "studio-audio-acceptance"


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for audio composition acceptance")
    return executable


def _make_tone(path: Path, frequency: int, duration: float, *, volume: float = 0.12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
            "-af", f"volume={volume}", "-ac", "1", "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_native_video(path: Path, duration: float = 4.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0x24384a:s=180x320:r=24:d={duration}",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-af", "volume=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _amplitude_at(path: Path, frequency: float, *, seconds: float = 1.0) -> float:
    sample_rate = 22050
    decoded = subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-t", str(seconds), "-ac", "1", "-ar", str(sample_rate),
            "-f", "f32le", "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    samples = array.array("f")
    samples.frombytes(decoded.stdout)
    if not samples:
        return 0.0
    if sys.byteorder != "little":
        samples.byteswap()
    count = len(samples)
    real = 0.0
    imaginary = 0.0
    for index, sample in enumerate(samples):
        phase = 2 * math.pi * frequency * index / sample_rate
        real += sample * math.cos(phase)
        imaginary += sample * math.sin(phase)
    return 2 * math.hypot(real, imaginary) / count


def test_imported_approved_voice_runs_external_tts_and_composes_without_native_dialogue(
    tmp_path, monkeypatch,
):
    _ffmpeg()

    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import voice_candidates
    from novelvideo.audio import narrative_group_dubbing
    from novelvideo.media_capabilities.audio import stem_separator
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource,
        H3DirectorOutputManifest,
        H3DirectorSegment,
        H3SourceDialogueLine,
        build_h3_timeline_data,
        load_h3_director_manifest,
        save_h3_director_manifest,
    )
    from novelvideo.models import NovelCharacter, NovelVisualBeat
    from novelvideo.narrative_groups.service import (
        advance_revision,
        group_beats,
        record_stage_result,
        save_groups,
    )
    from novelvideo.project_context import ProjectContext
    from novelvideo.ports.local.tasks import SQLiteCancellationStore
    from novelvideo.ports.registry import register_port
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.task_backend.runners import (
        narrative_group_video_compose,
        video as video_runner,
    )

    output_root = tmp_path / "project"
    state_root = tmp_path / "state"
    runtime_root = tmp_path / "runtime"
    for root in (output_root, state_root, runtime_root):
        root.mkdir(parents=True)
    ctx = ProjectContext(
        project_id=PROJECT,
        project_name="acceptance",
        owner_type="user",
        owner_id="user-1",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        requester_principals=(("user", "user-1"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_root,
        state_dir=state_root,
        runtime_dir=runtime_root,
        is_home_node=True,
    )

    store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(output_root),
        state_dir=str(state_root),
    )

    async def prepare_project_store():
        await store.initialize()
        await store.add_character(NovelCharacter(
            name="阿远",
            voice_facts={"vocalization_mode": "dialogue", "provenance": "human"},
        ))
        await store.add_visual_beats([NovelVisualBeat(
            episode_number=1,
            beat_number=1,
            narration="走吧。",
            visual_description="阿远站在旧站台。",
            audio_type="dialogue",
            speaker="阿远",
        )])
        await store.load_graph_state()

    asyncio.run(prepare_project_store())
    character = store.get_character("阿远")
    candidates = VoiceCandidateStore(store.db_path, output_root)

    async def resolve_voice_scope(project, name, _user, *, read_only=False):
        assert project == PROJECT
        assert name == "阿远"
        assert read_only in (True, False)
        return ctx, store, store.get_character(name), candidates

    monkeypatch.setattr(voice_candidates, "resolve_voice_scope", resolve_voice_scope)
    monkeypatch.setattr(
        voice_candidates,
        "make_static_url_for_context",
        lambda _ctx, path, **_kwargs: f"/static/{path}",
    )

    app = FastAPI()
    app.include_router(voice_candidates.router)
    app.dependency_overrides[get_api_user] = lambda: {
        "id": "user-1", "username": "tester",
    }
    imported_voice = _make_tone(tmp_path / "imported-voice.wav", 330, 2.0)
    imported_bytes = imported_voice.read_bytes()
    with TestClient(app) as client:
        imported = client.post(
            f"/projects/{PROJECT}/characters/%E9%98%BF%E8%BF%9C/voice-candidates/import",
            data={
                "kind": "dialogue",
                "slot": "default",
                "source": "licensed studio reference",
                "rights": "licensed for this production",
            },
            files={"file": ("voice.wav", imported_bytes, "audio/wav")},
        )
        assert imported.status_code == 200, imported.text
        candidate = imported.json()["data"]
        assert candidate["status"] == "qc_unavailable"
        assert candidate["report"]["technical"]["passed"] is True

        approved = client.post(
            f"/projects/{PROJECT}/characters/%E9%98%BF%E8%BF%9C/voice-candidates/"
            f"{candidate['candidate_id']}/approve",
            json={"confirm": True, "reason": "试听确认角色声线"},
        )
        assert approved.status_code == 200, approved.text
        adopted = approved.json()["data"]
        assert adopted["status"] == "approved"
        assert adopted["voice_version_id"]

        versions = client.get(
            f"/projects/{PROJECT}/characters/%E9%98%BF%E8%BF%9C/voice-versions",
        )
        assert versions.status_code == 200, versions.text
        current = versions.json()["data"]["current"]
        assert current == [{
            "identity_id": "",
            "slot": "default",
            "version_id": adopted["voice_version_id"],
        }]

    voice_version = VoiceVersionStore(store.db_path).get_current(
        character.character_id, identity_id="", slot="default",
    )
    assert voice_version["version_id"] == adopted["voice_version_id"]
    assert voice_version["candidate_id"] == candidate["candidate_id"]
    assert (output_root / voice_version["path"]).read_bytes() == imported_bytes

    native_video = _make_native_video(output_root / "videos" / "group-1.mp4")
    segment = H3DirectorSegment(
        segment_id="beat-1",
        beat_number=1,
        prompt="阿远停下脚步，低声说走吧。",
        duration_seconds=4,
        first_frame="fixture-frame.png",
        dialogue_source=DialogueSource.H3_NATIVE,
        dialogue_lines=(H3SourceDialogueLine(speaker="阿远", text="走吧。"),),
    )
    groups_root = output_root
    save_groups(groups_root, 1, group_beats([{"id": "beat-1", "beat_number": 1}]))
    _group, revision = advance_revision(groups_root, 1, "ng-01", "video")
    assert revision == 1
    manifest_path = output_root / "videos" / "group-1.manifest.json"
    save_h3_director_manifest(
        manifest_path,
        H3DirectorOutputManifest(
            physical_video=str(native_video),
            entries=build_h3_timeline_data((segment,)).entries,
        ),
    )
    record_stage_result(
        groups_root,
        1,
        "ng-01",
        "video",
        expected_revision=revision,
        status="completed",
        video_asset=str(native_video),
        manifest_asset=str(manifest_path),
    )

    ambience_calls = []

    class OfflineDemucs:
        available = True

        async def separate(self, _source, output_dir):
            output_dir = Path(output_dir)
            ambience_calls.append(output_dir)
            no_vocals = _make_tone(output_dir / "no_vocals.wav", 220, 4.0)
            vocals = _make_tone(output_dir / "vocals.wav", 440, 4.0)
            return SimpleNamespace(no_vocals=no_vocals, vocals=vocals)

    monkeypatch.setattr(stem_separator, "DemucsStemSeparator", OfflineDemucs)
    monkeypatch.setattr(narrative_group_dubbing, "_default_indextts2_generator", lambda: object())
    tts_audio = _make_tone(tmp_path / "tts-response.wav", 880, 2.0)
    tts_calls = []

    async def fake_paid_tts(*, generator, prompt, reference_path, output_path,
                            emotion_prompt, audio_url_builder):
        del generator, emotion_prompt, audio_url_builder
        tts_calls.append({
            "prompt": prompt,
            "reference_path": Path(reference_path),
            "output_path": Path(output_path),
        })
        shutil.copyfile(tts_audio, output_path)
        return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(
        narrative_group_dubbing,
        "_generate_with_reference_audio",
        fake_paid_tts,
    )

    class TaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: TaskManager())
    register_port(
        "cancellation_store",
        SQLiteCancellationStore(tmp_path / "task-cancellations.db"),
    )
    result = narrative_group_video_compose.run_narrative_group_video_compose(
        {
            "__run_task_id": "studio-audio-compose",
            "episode": 1,
            "payload": {
                "project_dir": str(output_root),
                "group_id": "ng-01",
                "revision": revision,
                "span_index": 0,
                "prepare_external_audio": True,
                "dialogue_source": "external_tts",
                "resolution": "180x320",
            },
        },
        ctx,
    )

    assert result["recomposition_only"] is True
    assert len(ambience_calls) == 1
    assert len(tts_calls) == 1
    assert tts_calls[0]["prompt"] == "走吧。"
    assert tts_calls[0]["reference_path"].read_bytes() == imported_bytes
    assert voice_version["audio_sha256"] == adopted["sha256"]

    updated_manifest = load_h3_director_manifest(manifest_path)
    entry = updated_manifest.entries[0]
    assert entry.dialogue_source is DialogueSource.EXTERNAL_TTS
    assert entry.segment.dialogue_source is DialogueSource.EXTERNAL_TTS
    assert entry.external_audio_path and Path(entry.external_audio_path).is_file()
    assert updated_manifest.ambience_stem_status == "succeeded"
    assert Path(updated_manifest.ambience_stem_path).is_file()
    assert updated_manifest.ambience_stem_path.endswith("no_vocals.wav")

    final_path = Path(result["video_path"])
    assert final_path.is_file() and final_path.stat().st_size > 0
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type", "-of", "json", str(final_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert {item["codec_type"] for item in json.loads(probe.stdout)["streams"]} == {
        "audio", "video",
    }

    ambience_amplitude = _amplitude_at(final_path, 220)
    native_dialogue_amplitude = _amplitude_at(final_path, 440)
    external_tts_amplitude = _amplitude_at(final_path, 880)
    assert ambience_amplitude > 0.003
    assert external_tts_amplitude > 0.003
    assert native_dialogue_amplitude < min(ambience_amplitude, external_tts_amplitude) * 0.2

    asyncio.run(store.close())
