from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_zero_volume_audio_is_not_mixed(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs
    base = tmp_path / 'base.mp4'
    base.write_bytes(b'video')
    render = AsyncMock()
    monkeypatch.setattr(jobs, '_render_audio_clip', render)
    monkeypatch.setattr(jobs, '_run_cmd', AsyncMock())
    await jobs._mix_audio_tracks(base_video_path=base, final_output_path=tmp_path / 'final.mp4',
        audio_items=[{'source_path': 'audio.wav', 'source_start': 0, 'source_end': 2, 'volume': 0}],
        temp_dir=tmp_path)
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_volume_video_reaches_renderer_unchanged(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs
    render = AsyncMock()
    async def concat(paths, output_path):
        output_path.write_bytes(b'video')
    monkeypatch.setattr(jobs.shutil, 'which', lambda name: name)
    monkeypatch.setattr(jobs, '_render_video_clip', render)
    monkeypatch.setattr(jobs, '_concat_media_segments', concat)
    monkeypatch.setattr(jobs, '_verify_rendered_video', AsyncMock(return_value={'duration_seconds': 2}))
    await jobs.run_freezone_video_compose(project_dir=tmp_path, job_id='zero-volume',
        title='', canvas_id='', tracks=[{'kind': 'video', 'items': [{
            'item_id': 'clip', 'source_path': 'video.mp4', 'source_start': 0,
            'source_end': 2, 'timeline_start': 0, 'volume': 0}]}])
    assert render.await_args.kwargs['volume'] == 0
