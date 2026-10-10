import pytest


def test_caption_turns_are_sequential_and_explicitly_estimated():
    from novelvideo.export import episode_export

    assert hasattr(episode_export, "build_caption_track"), "shared caption track missing"


def test_caption_layout_and_provenance_round_trip(tmp_path):
    from novelvideo.export.captions import CaptionEvent, CaptionTrack, estimate_events, render_ass, render_srt

    events = estimate_events("line1", "甲：你去哪？\n乙：我要去山上寻找那位失踪已经很久的朋友，然后带他回家。", 0, 6, "sha256:abc")
    assert len(events) >= 2
    assert all(e.source == "estimated" and e.review_status == "needs_review" for e in events)
    assert all(len(e.text.splitlines()) <= 2 and all(len(line) <= 16 for line in e.text.splitlines()) for e in events)
    assert all(a.end <= b.start for a, b in zip(events, events[1:]))
    assert not any("你去哪" in e.text and "我要去" in e.text for e in events)
    track = CaptionTrack(duration=6, events=events, audio_fingerprint="sha256:abc")
    restored = CaptionTrack.model_validate_json(track.model_dump_json())
    assert restored == track
    ass = render_ass(track, 720, 1280)
    assert "PlayResX: 720" in ass and "PlayResY: 1280" in ass
    assert ",32," in ass and ",1.5," in ass and ",58,58,128," in ass
    assert render_srt(track).count(" --> ") == len(events)
    assert ",48," in render_ass(track, 1080, 1920)


@pytest.mark.parametrize("events", [
    [(0, 3), (2, 4)], [(0, 7)], [(-1, 1)], [(2, 2)],
])
def test_invalid_caption_timeline_rejected(events):
    from novelvideo.export.captions import CaptionEvent, CaptionTrack

    with pytest.raises(ValueError):
        CaptionTrack(duration=6, audio_fingerprint="abc", events=[CaptionEvent(
            dialogue_id=str(i), speaker="", text="你好", start=a, end=b,
            source="manual", review_status="approved", audio_fingerprint="abc",
        ) for i, (a, b) in enumerate(events)])


@pytest.mark.asyncio
async def test_actual_clip_offsets_cache_and_stale_audio(tmp_path, monkeypatch):
    from novelvideo.export.episode_export import build_caption_track, build_srt_content
    from novelvideo.task_backend.runners import video

    first, second = tmp_path / "one.mp4", tmp_path / "two.mp4"
    first.write_bytes(b"original audio one")
    second.write_bytes(b"original audio two")
    sources = [video.VideoSpan(first, (1,)), video.VideoSpan(second, (2,))]
    beats = [{"beat_number": 1, "narration_segment": "你去哪？"},
             {"beat_number": 2, "narration_segment": "我要回家。"}]
    monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *args: sources)
    track = await build_caption_track(tmp_path, 1, beats, actual_durations=[1.2, 2.3])
    assert track.duration == 3.5
    assert track.events[1].start == 1.2
    assert track.timing_basis == "actual"
    approved = track.model_copy(update={"events": [event.model_copy(update={
        "source": "manual", "review_status": "approved"}) for event in track.events]})
    saved = tmp_path / "videos/episodes/ep001.caption-events.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(approved.model_dump_json())
    assert await build_caption_track(tmp_path, 1, beats) == approved
    assert "00:00:01,200 --> 00:00:03,500" in await build_srt_content(tmp_path, 1, beats)
    with pytest.raises(ValueError, match="CAPTION_TIMELINE_CHANGED"):
        await build_caption_track(tmp_path, 1, beats, actual_durations=[1.3, 2.3])
    with pytest.raises(ValueError, match="CAPTION_TIMELINE_CHANGED"):
        await build_caption_track(tmp_path, 1, beats, actual_durations=[1.3, 2.2])
    second.write_bytes(b"regenerated different audio")
    with pytest.raises(ValueError, match="CAPTION_SOURCE_CHANGED"):
        await build_caption_track(tmp_path, 1, beats)


def test_question_answer_without_punctuation_remain_separate():
    from novelvideo.export.captions import estimate_events
    events = estimate_events("d", "甲：你去哪 乙：我要回家", 0, 4, "sha")
    assert len(events) == 2
    assert [e.speaker for e in events] == ["甲", "乙"]


def test_long_caption_prefers_clause_boundary_without_changing_words():
    from novelvideo.export.captions import estimate_events
    text = "我们今天必须沿着这条小路一直往前走，才能找到那位已经失踪了很久的老朋友。"
    events = estimate_events("d", text, 0, 8, "sha")
    assert events[0].text.endswith("，")
    assert "".join(e.text.replace("\n", "") for e in events) == text


@pytest.mark.asyncio
async def test_director_dialogue_outside_actual_clip_is_not_silently_dropped(tmp_path):
    from types import SimpleNamespace
    from novelvideo.export.episode_export import build_caption_track
    from novelvideo.task_backend.runners.video import VideoSpan
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    entries = tuple(SimpleNamespace(
        segment=SimpleNamespace(segment_id=str(i), beat_number=i, dialogue=text, speaker=""),
        start_seconds=start, end_seconds=end, external_audio_path=None,
    ) for i, text, start, end in [(1, "问题？", 0, 2), (2, "回答。", 2, 4)])
    with pytest.raises(ValueError, match="CAPTION_TIMELINE_CHANGED"):
        await build_caption_track(tmp_path, 1, [], sources=[VideoSpan(source, (1, 2), entries)],
                                  actual_durations=[1.5])


@pytest.mark.parametrize("command", ["N", "n", "h"])
def test_ass_literal_backslash_commands_render_as_one_literal_line(tmp_path, command):
    import shutil
    import subprocess
    from novelvideo.export.captions import CaptionEvent, CaptionTrack, render_ass

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    if " subtitles " not in filters.stdout:
        pytest.skip("ffmpeg lacks libass")

    def bounds(text):
        track = CaptionTrack(duration=1, audio_fingerprint="test", events=[CaptionEvent(
            dialogue_id="d", text=text, start=0, end=1, source="manual",
            review_status="approved", audio_fingerprint="test")])
        ass = tmp_path / "literal.ass"
        ass.write_text(render_ass(track, 720, 720, "Arial"))
        frame = subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=720x720:d=1",
            "-vf", f"subtitles={ass}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ], check=True, capture_output=True).stdout
        points = [(i % 720, i // 720) for i, pixel in enumerate(frame) if pixel > 100]
        assert points
        return (max(x for x, y in points) - min(x for x, y in points),
                max(y for x, y in points) - min(y for x, y in points))

    plain_width, plain_height = bounds(f"A{command}B")
    literal_width, literal_height = bounds(f"A\\{command}B")
    assert literal_height <= plain_height + 3, "literal ASS command became a line break"
    assert literal_width > plain_width + 3, "literal backslash/command was consumed"
