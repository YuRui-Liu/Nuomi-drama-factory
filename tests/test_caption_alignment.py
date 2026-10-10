from novelvideo.export.captions import CaptionTrack, estimate_events


def test_alignment_preserves_script_and_requires_review():
    from novelvideo.export.caption_alignment import align_words
    track = CaptionTrack(duration=4, audio_fingerprint="test", events=estimate_events("a", "你好。", 0, 4, "test"))
    result, issues = align_words(track, [{"word": "你好", "start": 1., "end": 2., "probability": .99}])
    assert result.events[0].text == "你好。"
    assert (result.events[0].start, result.events[0].end) == (1, 2)
    assert result.events[0].source == "aligned"
    assert result.events[0].review_status == "needs_review"
    assert not issues


def test_alignment_keeps_unmatched_dialogue_and_reports_extra_speech():
    from novelvideo.export.caption_alignment import align_words
    track = CaptionTrack(duration=4, audio_fingerprint="test", events=estimate_events("a", "再见。", 0, 4, "test"))
    result, issues = align_words(track, [{"word": "你好", "start": 1., "end": 2., "probability": .99}])
    assert result.events == track.events
    assert {issue["kind"] for issue in issues} == {"unmatched_script", "extra_speech"}


def test_conflict_reverts_only_affected_aligned_events():
    from novelvideo.export.caption_alignment import align_words
    events = (estimate_events("a", "甲", 0, 2, "test") + estimate_events("b", "乙", 2, 4, "test")
              + estimate_events("c", "丙", 4, 6, "test") + estimate_events("d", "丁", 6, 8, "test"))
    track = CaptionTrack(duration=8, audio_fingerprint="test", events=events)
    result, issues = align_words(track, [{"word": "甲", "start": 1, "end": 3, "probability": .99},
                                       {"word": "丁", "start": 6.5, "end": 7, "probability": .99}])
    assert result.events[:3] == track.events[:3]
    assert result.events[3].source == "aligned"
    assert result.events[3].start == 6.5
    assert [i["dialogue_id"] for i in issues if i["kind"] == "timing_conflict"] == ["a:1"]


def test_conflict_reversion_iterates_when_restored_estimate_creates_new_overlap():
    from novelvideo.export.caption_alignment import align_words
    events = (estimate_events("a", "甲", 0, 2, "test") + estimate_events("b", "乙", 2, 4, "test")
              + estimate_events("c", "丙", 4, 6, "test") + estimate_events("d", "丁", 6, 8, "test"))
    track = CaptionTrack(duration=8, audio_fingerprint="test", events=events)
    result, issues = align_words(track, [{"word": "甲", "start": 2.5, "end": 3, "probability": .99},
                                       {"word": "乙", "start": 3.5, "end": 4.5, "probability": .99},
                                       {"word": "丁", "start": 6.5, "end": 7, "probability": .99}])
    assert result.events[:3] == track.events[:3]
    assert result.events[3].source == "aligned"
    assert {i["dialogue_id"] for i in issues if i["kind"] == "timing_conflict"} == {"a:1", "b:1"}


import pytest


@pytest.mark.parametrize("explicit, data_root, expected", [
    (None, "/persistent/data", "/persistent/data/cache/huggingface/hub"),
    ("/custom/hub", "/persistent/data", "/custom/hub"),
    (None, None, None),
])
def test_model_cache_configuration(monkeypatch, explicit, data_root, expected):
    from novelvideo.export.caption_alignment import _model_cache_dir
    for key, value in [("NUOMI_CAPTION_MODEL_CACHE", explicit), ("NOVELVIDEO_DATA_ROOT", data_root)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    assert _model_cache_dir() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type, expected", [("LocalEntryNotFoundError", "NUOMI_CAPTION_MODEL_CACHE"), ("ModuleNotFoundError", "mlx-whisper"), ("ValueError", "识别失败")])
async def test_local_adapter_errors_are_actionable_without_traceback(tmp_path, monkeypatch, error_type, expected):
    from novelvideo.export import caption_alignment as module
    async def fail(*args, **kwargs):
        raise RuntimeError(f"CAPTION_ALIGNMENT_UNAVAILABLE: Traceback (most recent call last):\n  private data\n{error_type}: failure")
    monkeypatch.setattr(module, "_run", fail)
    with pytest.raises(RuntimeError) as error:
        await module.transcribe_local(tmp_path / "audio.wav", "test/model", None)
    assert expected in str(error.value)
    assert error_type in str(error.value)
    assert "Traceback" not in str(error.value)
    assert "private data" not in str(error.value)


@pytest.mark.asyncio
async def test_alignment_cached_and_preserves_revision(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.media_capabilities.video.h3_timeline import DialogueSource
    from novelvideo.export import caption_alignment as module
    from novelvideo.task_backend.runners import video
    source = video.VideoSpan(tmp_path / "adopted.mp4", (1,), entries=(SimpleNamespace(dialogue_source=DialogueSource.H3_NATIVE),))
    monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *a: [source])
    track = CaptionTrack(duration=4, source_durations=[4], audio_fingerprint="test", events=estimate_events("a", "你好。", 0, 4, "test"))
    async def build(*a, **kw): return track.model_copy(deep=True)
    monkeypatch.setattr(module, "build_caption_track", build)
    commands = []
    async def run(*args, **kwargs): commands.append(args)
    monkeypatch.setattr(module, "_run", run)
    calls = []
    async def adapter(*args):
        calls.append(args)
        return [{"word": "你好", "start": 1, "end": 2, "probability": .99}]
    saved = await module.align_track(tmp_path, 1, [], "unsaved", adapter=adapter)
    assert saved["track"]["events"][0]["source"] == "aligned"
    assert str(source.video_path) in commands[0]
    await module.align_track(tmp_path, 1, [], saved["revision"], adapter=adapter)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="REVISION_CHANGED"):
        await module.align_track(tmp_path, 1, [], "unsaved", adapter=adapter)


def test_low_confidence_stays_estimated():
    from novelvideo.export.caption_alignment import align_words
    track = CaptionTrack(duration=4, audio_fingerprint="test", events=estimate_events("a", "你好。", 0, 4, "test"))
    result, issues = align_words(track, [{"word": "你好", "start": 1., "end": 2., "probability": .2}])
    assert result.events == track.events
    assert issues[0]["kind"] == "low_confidence"


@pytest.mark.asyncio
async def test_external_tts_sources_are_not_aligned_to_original_mix(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.export import caption_alignment as module
    from novelvideo.task_backend.runners import video
    from novelvideo.media_capabilities.video.h3_timeline import DialogueSource
    source = video.VideoSpan(tmp_path / "adopted.mp4", (1,), entries=(SimpleNamespace(dialogue_source=DialogueSource.EXTERNAL_TTS),))
    monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *a: [source])
    with pytest.raises(ValueError, match="NATIVE_ONLY"):
        await module.align_track(tmp_path, 1, [], "unsaved")


@pytest.mark.parametrize("bad", [
    [{"word": "你", "start": 1, "end": 2}, {"word": "好", "start": 0, "end": .5}, {"word": "啊", "start": 3, "end": 4}],
    [{"word": "你好啊", "start": float("nan"), "end": 4}],
    [{"word": "你好啊", "start": 1, "end": float("inf")}],
])
def test_invalid_word_timeline_never_aligns(bad):
    from novelvideo.export.caption_alignment import align_words
    track = CaptionTrack(duration=4, audio_fingerprint="test", events=estimate_events("a", "你好啊", 0, 4, "test"))
    with pytest.raises(ValueError, match="CAPTION_ALIGNMENT_INVALID"):
        align_words(track, [{**word, "probability": .99} for word in bad])


def test_zero_duration_token_reported_without_losing_valid_speech():
    from novelvideo.export.caption_alignment import align_words
    track = CaptionTrack(duration=4, audio_fingerprint="test", events=estimate_events("a", "你好。", 0, 4, "test"))
    result, issues = align_words(track, [{"word": "。", "start": 0, "end": 0, "probability": .99},
                                       {"word": "你好", "start": 1, "end": 2, "probability": .99}])
    assert result.events[0].source == "aligned"
    assert issues[0]["kind"] == "zero_duration_token"


@pytest.mark.asyncio
async def test_legacy_span_cannot_use_original_video_audio(tmp_path, monkeypatch):
    from novelvideo.export import caption_alignment as module
    from novelvideo.task_backend.runners import video
    monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *a: [video.VideoSpan(tmp_path / "legacy.mp4", (1,))])
    with pytest.raises(ValueError, match="NATIVE_ONLY"):
        await module.align_track(tmp_path, 1, [], "unsaved")


@pytest.mark.asyncio
async def test_script_changed_during_recognition_is_not_published(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.export import caption_alignment as module
    from novelvideo.task_backend.runners import video
    from novelvideo.media_capabilities.video.h3_timeline import DialogueSource
    source = video.VideoSpan(tmp_path / "adopted.mp4", (1,), entries=(SimpleNamespace(dialogue_source=DialogueSource.H3_NATIVE),))
    monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *a: [source])
    current_beats = [{"narration_segment": "你好。"}]
    original_beats = [dict(current_beats[0])]
    async def build(root, episode, beats, **kwargs):
        fingerprint = beats[0]["narration_segment"]
        return CaptionTrack(duration=4, source_durations=[4], audio_fingerprint=fingerprint,
                            events=estimate_events("a", fingerprint, 0, 4, fingerprint))
    async def run(*a, **kw): pass
    async def adapter(*a):
        current_beats[0]["narration_segment"] = "再见。"
        return [{"word": "你好", "start": 1, "end": 2, "probability": .99}]
    async def reload_beats(): return current_beats
    monkeypatch.setattr(module, "build_caption_track", build)
    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(ValueError, match="CAPTION_SOURCE_CHANGED"):
        await module.align_track(tmp_path, 1, original_beats, "unsaved", adapter=adapter, reload_beats=reload_beats)
    assert not module._path(tmp_path, 1).exists()
