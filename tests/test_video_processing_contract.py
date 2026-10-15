from pathlib import Path

import pytest


def test_unconfigured_model_capabilities_fail_closed():
    from novelvideo.freezone.video_processing import capabilities, require_capability
    assert not capabilities()["extend"]["available"]
    with pytest.raises(ValueError, match="未配置"):
        require_capability("extend")


def test_ranges_are_half_open_and_finite():
    from novelvideo.freezone.video_processing import validate_range
    assert validate_range(0, 4, 4) == 4
    for start, end in [(-1, 2), (2, 2), (3, 2), (0, 5), (0, float("nan"))]:
        with pytest.raises(ValueError):
            validate_range(start, end, 4)


def test_stage_identity_is_stable_and_separate():
    from novelvideo.freezone.video_processing import stage_id
    assert stage_id("job", "upscale") == stage_id("job", "upscale")
    assert stage_id("job", "upscale") != stage_id("job", "interpolate")


@pytest.mark.asyncio
async def test_audio_separation_produces_mp3(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs
    commands = []
    async def run(cmd):
        commands.append(cmd)
        Path(cmd[-1]).touch()
    async def has_audio(_):
        return True
    monkeypatch.setattr(jobs.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(jobs, "_run_cmd", run)
    monkeypatch.setattr(jobs, "_probe_has_audio", has_audio)
    result = await jobs.run_freezone_audio_separate(project_dir=tmp_path, job_id="test", source_path="source.mp4")
    assert result["audio_path"].suffix == ".mp3"
    assert "libmp3lame" in commands[0]


def test_legacy_audio_result_is_readable(tmp_path):
    from novelvideo.freezone.video_processing import audio_result_path
    root = tmp_path / "freezone/_outputs/freezone_audio_separate"
    root.mkdir(parents=True)
    (root / "old.m4a").touch()
    assert audio_result_path(tmp_path, "old").suffix == ".m4a"
    (root / "old.mp3").touch()
    assert audio_result_path(tmp_path, "old").suffix == ".mp3"


@pytest.mark.asyncio
async def test_real_mp3_separation_preserves_audio_and_mutes_video(tmp_path):
    import asyncio
    import json
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    from novelvideo.freezone import jobs
    source = tmp_path / "source.mp4"
    await jobs._run_cmd(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)])
    result = await jobs.run_freezone_audio_separate(project_dir=tmp_path, job_id="real-mp3", source_path=str(source))
    process = await asyncio.create_subprocess_exec("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result["audio_path"]), stdout=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    assert process.returncode == 0
    media = json.loads(stdout)
    assert media["streams"][0]["codec_name"] == "mp3"
    assert abs(float(media["format"]["duration"]) - 1) < 0.15
    assert not await jobs._probe_has_audio(str(result["mute_video_path"]))
    assert await jobs._probe_has_audio(str(source))


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt_cache', [False, True])
async def test_stage_retry_does_not_rerun_completed_upscale(monkeypatch, tmp_path, corrupt_cache):
    from novelvideo.freezone import jobs, video_processing as service
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []
    failed = False
    async def execute(**kwargs):
        nonlocal failed
        stage = kwargs["request"]["operation"]
        calls.append((stage, kwargs["operation_id"]))
        if stage == "interpolate" and not failed:
            failed = True
            raise RuntimeError("transient failure")
        kwargs["output"].write_bytes(stage.encode())
        return kwargs["output"]
    async def duration(_):
        return 4.0
    async def stream(_):
        return {"width": 2560, "height": 1440, "fps": 60}
    async def has_audio(_):
        return False
    monkeypatch.setattr(jobs, "_probe_video_duration", duration)
    monkeypatch.setattr(jobs, "_probe_has_audio", has_audio)
    monkeypatch.setattr(service, "probe_stream", stream)
    monkeypatch.setattr(service, "ADAPTERS", {
        "upscale": service.ProcessingAdapter(execute, "test", "up", resolutions=("2k",)),
        "interpolate": service.ProcessingAdapter(execute, "test", "fps", frame_rates=(60,)),
    })
    payload = {"source_path": str(source), "source_fingerprint": service.fingerprint(source),
               "operation": "enhance", "resolution": "2k", "fps": 60}
    with pytest.raises(RuntimeError, match="transient"):
        await service.run_processing(project_dir=tmp_path, job_id="stable", payload=payload)
    if corrupt_cache:
        receipt = service.outputs_dir(tmp_path, 'freezone_video_upscale') / f'{service.stage_id("stable", "upscale")}.json'
        receipt.write_text('{"signature":')
    output, meta = await service.run_processing(project_dir=tmp_path, job_id="stable", payload=payload)
    assert output.exists()
    assert [stage for stage, _ in calls] == (["upscale", "interpolate", "upscale", "interpolate"] if corrupt_cache else ["upscale", "interpolate", "interpolate"])
    assert calls[0][1] != calls[1][1] == calls[-1][1]
    assert meta["stages"][0]["reused"] is not corrupt_cache
    if corrupt_cache:
        assert calls[0][1] == calls[2][1]


def test_stage_receipt_failed_replace_preserves_previous_cache(monkeypatch, tmp_path):
    from novelvideo.freezone.video_processing import _save_stage_receipt
    receipt = tmp_path / 'stage.json'
    receipt.write_text('{"valid": true}')
    def fail_replace(*args):
        raise OSError('interrupted replacement')
    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(OSError, match='interrupted'):
        _save_stage_receipt(receipt, {'valid': False})
    assert receipt.read_text() == '{"valid": true}'
    assert list(tmp_path.iterdir()) == [receipt]


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(0, 1), (1, 2), (2, 3)])
async def test_real_ffmpeg_adoption_preserves_duration_and_audio(tmp_path, start, end):
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    from novelvideo.freezone.jobs import _run_cmd, _probe_video_duration, _probe_has_audio
    from novelvideo.freezone.video_processing import adopt_segment
    source, candidate, output = [tmp_path / name for name in ("source.mp4", "candidate.mp4", "output.mp4")]
    await _run_cmd(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)])
    await _run_cmd(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=128x72:r=25:d=1", "-c:v", "libx264", str(candidate)])
    await adopt_segment(source, candidate, output, start, end)
    assert abs(await _probe_video_duration(str(output)) - 3) < 0.12
    assert await _probe_has_audio(str(output))
    assert source.exists() and candidate.exists()


@pytest.mark.asyncio
async def test_source_change_rejected_before_processing(tmp_path):
    from novelvideo.freezone.video_processing import run_processing
    source = tmp_path / "source.mp4"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="已变化"):
        await run_processing(project_dir=tmp_path, job_id="test", payload={"source_path": str(source), "source_fingerprint": "old", "operation": "extend"})


@pytest.mark.asyncio
async def test_audio_separation_without_audio_returns_none(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs
    async def run(cmd): Path(cmd[-1]).touch()
    async def no_audio(_): return False
    monkeypatch.setattr(jobs.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(jobs, "_run_cmd", run)
    monkeypatch.setattr(jobs, "_probe_has_audio", no_audio)
    result = await jobs.run_freezone_audio_separate(project_dir=tmp_path, job_id="silent", source_path="silent.mp4")
    assert result["audio_path"] is None
    assert result["mute_video_path"].exists()


@pytest.mark.asyncio
async def test_wrong_resolution_is_rejected_even_when_duration_matches(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs, video_processing as service
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    async def execute(**kwargs):
        kwargs["output"].write_bytes(b"output")
        return kwargs["output"]
    async def duration(_): return 4.0
    async def stream(_): return {"width": 1280, "height": 720, "fps": 30}
    monkeypatch.setattr(jobs, "_probe_video_duration", duration)
    monkeypatch.setattr(service, "probe_stream", stream)
    monkeypatch.setattr(service, "ADAPTERS", {"upscale": service.ProcessingAdapter(execute, "test", "up", resolutions=("4k",))})
    with pytest.raises(ValueError, match="实际分辨率"):
        await service.run_processing(project_dir=tmp_path, job_id="wrong", payload={"source_path": str(source), "source_fingerprint": service.fingerprint(source), "operation": "enhance", "resolution": "4k"})


@pytest.mark.asyncio
async def test_real_ffmpeg_continuation_handles_mixed_geometry_audio(tmp_path):
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    from novelvideo.freezone.jobs import _run_cmd, _probe_video_duration, _probe_has_audio
    from novelvideo.freezone.video_processing import join_continuation, probe_stream
    source, tail, output = [tmp_path / name for name in ("source.mp4", "tail.mp4", "joined.mp4")]
    await _run_cmd(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)])
    await _run_cmd(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=1", "-c:v", "libx264", str(tail)])
    await join_continuation(source, tail, output, 2, 1)
    assert abs(await _probe_video_duration(str(output)) - 3) < .12
    assert await _probe_has_audio(str(output))
    assert (await probe_stream(output))["width"] == 160
