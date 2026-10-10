from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_explicit_draft_remap_preserves_overrun_dialogue(tmp_path):
    from novelvideo.export.episode_export import build_caption_track
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'fixture')
    segment = SimpleNamespace(dialogue='后一句。', beat_number=1, segment_id='s2', speaker='甲')
    span = SimpleNamespace(video_path=source, manifest_path=None, original_audio_path=None,
                           ambience_stem_path=None, beat_numbers=(1,), is_director=True,
                           entries=[SimpleNamespace(segment=segment, external_audio_path=None,
                                                    start_seconds=2., end_seconds=4.)])
    track = await build_caption_track(tmp_path, 1, [], sources=[span], actual_durations=[1.5],
                                      allow_estimated_remap=True)
    assert ''.join(e.text for e in track.events) == '后一句。'
    assert track.events[0].start == .75
    assert track.events[-1].end == 1.5
    assert all(e.source == 'estimated' and e.review_status == 'needs_review' for e in track.events)
    assert track.timing_notes


@pytest.mark.asyncio
async def test_caption_duration_uses_video_frames_not_audio_padding(monkeypatch):
    from novelvideo.export import episode_export
    async def probe(*args):
        return {'streams': [{'codec_type': 'video', 'nb_frames': '670', 'r_frame_rate': '24/1', 'duration': '27.916667'},
                            {'codec_type': 'audio', 'duration': '27.956'}]}
    monkeypatch.setattr(episode_export, '_probe_caption_media', probe)
    assert await episode_export.caption_video_duration('clip.mp4') == pytest.approx(670/24)


@pytest.mark.asyncio
async def test_audio_fallback_is_not_verified_video_time(tmp_path, monkeypatch):
    from novelvideo.export import episode_export
    from novelvideo.task_backend.runners.video import VideoSpan
    async def failed(*args): raise ValueError('no video frames')
    async def audio(*args): return 4.
    monkeypatch.setattr(episode_export, 'caption_video_duration', failed)
    monkeypatch.setattr(episode_export, 'get_audio_duration_async', audio)
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'fixture')
    track = await episode_export.build_caption_track(tmp_path, 1, [{'beat_number': 1, 'narration_segment': '你好。'}], sources=[VideoSpan(path, (1,))])
    assert track.timing_basis == 'estimated'
