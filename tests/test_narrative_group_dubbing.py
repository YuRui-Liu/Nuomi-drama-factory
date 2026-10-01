from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_resolves_each_speaker_before_any_generation(tmp_path):
    from novelvideo.audio.narrative_group_dubbing import resolve_lines
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    segment = H3DirectorSegment(segment_id="s", beat_number=1, prompt="p",
        first_frame=None, duration_seconds=9,
        dialogue_lines=({"speaker": "甲", "text": "你好"}, {"speaker": "乙", "text": "再见"}))
    calls = []

    async def voice(beat, store):
        calls.append(beat["speaker"])
        return (tmp_path / "voice.wav", "hash") if beat["speaker"] == "甲" else None

    with pytest.raises(RuntimeError, match="乙.*声线"):
        await resolve_lines(segment, SimpleNamespace(), resolver=voice)
    assert calls == ["甲", "乙"]


def test_audio_paths_are_group_and_version_specific(tmp_path):
    from novelvideo.audio.narrative_group_dubbing import dubbing_directory
    assert dubbing_directory(tmp_path / "group-03_r3.manifest.json", 0) != dubbing_directory(tmp_path / "group-04_r3.manifest.json", 0)
    assert dubbing_directory(tmp_path / "group-03_r3.manifest.json", 0) != dubbing_directory(tmp_path / "group-03_r4.manifest.json", 0)


@pytest.mark.asyncio
async def test_preparation_clones_each_voice_and_publishes_only_after_audio_ready(tmp_path, monkeypatch):
    from pathlib import Path
    from contextlib import nullcontext
    from novelvideo.audio import narrative_group_dubbing as subject
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorSegment, H3DirectorOutputManifest, build_h3_timeline_data,
        save_h3_director_manifest, load_h3_director_manifest, DialogueSource,
    )
    from novelvideo.narrative_groups import service
    from novelvideo.api import deps
    from novelvideo.media_capabilities.audio import stem_separator

    path = tmp_path / "group-03_r3.manifest.json"
    segment = H3DirectorSegment(segment_id="s", beat_number=1, prompt="p", first_frame="frame.png",
        duration_seconds=9, dialogue_source=DialogueSource.H3_NATIVE,
        dialogue_lines=({"speaker": "甲", "text": "你好"}, {"speaker": "乙", "text": "再见"}))
    save_h3_director_manifest(path, H3DirectorOutputManifest(physical_video=str(tmp_path / "video.mp4"),
        entries=build_h3_timeline_data((segment,)).entries))
    state = {"revision": 3, "manifest_asset": str(path)}
    monkeypatch.setattr(service, "stage_payload", lambda *_: state)
    monkeypatch.setattr(service, "_sidecar_guard", lambda *_: nullcontext())
    async def close(): pass
    async def store(_): return SimpleNamespace(close=close)
    async def lines(segment, store):
        return [(line, tmp_path / f"{line.speaker}.wav", line.speaker) for line in segment.dialogue_lines]
    monkeypatch.setattr(deps, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(subject, "resolve_lines", lines)
    calls = []
    class Separator:
        available = True
        async def separate(self, *_):
            calls.append("separate")
            return SimpleNamespace(no_vocals=tmp_path / "amb.wav", vocals=tmp_path / "vocals.wav")
    monkeypatch.setattr(stem_separator, "DemucsStemSeparator", Separator)
    monkeypatch.setattr(subject, "_default_indextts2_generator", lambda: object())
    async def clone(**kwargs):
        assert load_h3_director_manifest(path).entries[0].dialogue_source == DialogueSource.H3_NATIVE
        calls.append(kwargs["reference_path"].stem)
        kwargs["output_path"].write_bytes(b"audio")
        return SimpleNamespace(success=True)
    async def run(*cmd):
        if cmd[0] == "ffprobe": return "8.0"
        Path(cmd[-1]).write_bytes(b"audio")
        return ""
    monkeypatch.setattr(subject, "_generate_with_reference_audio", clone)
    monkeypatch.setattr(subject, "_run", run)
    output = await subject.prepare_external_audio(SimpleNamespace(output_dir=tmp_path), 1, "group-03", 3, 0)
    assert calls == ["separate", "甲", "乙"]
    updated = load_h3_director_manifest(path)
    assert updated.entries[0].external_audio_path == output
    assert updated.entries[0].dialogue_source == DialogueSource.EXTERNAL_TTS
    assert updated.ambience_stem_status == "succeeded"
