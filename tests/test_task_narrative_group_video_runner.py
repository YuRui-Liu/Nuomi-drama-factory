from pathlib import Path
from types import SimpleNamespace
import asyncio


def _seed_group(tmp_path: Path):
    from novelvideo.narrative_groups.service import advance_revision, group_beats, record_stage_result, save_groups

    save_groups(tmp_path, 1, group_beats([
        {"id": "beat-1", "beat_number": 1, "video_prompt": "雨夜里他抬头。", "dialogue": "别走。", "speaker": "阿明", "tone": "急切"},
        {"id": "beat-2", "beat_number": 2, "video_prompt": "她停在门口。", "dialogue": "我会回来。", "speaker": "小雨", "tone": "克制"},
    ]))
    advance_revision(tmp_path, 1, "ng-01", "render")
    frame_one = tmp_path / "frame-1.png"
    frame_two = tmp_path / "frame-2.png"
    frame_one.write_bytes(b"one")
    frame_two.write_bytes(b"two")
    record_stage_result(
        tmp_path, 1, "ng-01", "render", expected_revision=1, status="completed",
        cell_assets=[
            {"cell": 0, "beat_id": "beat-1", "path": str(frame_one)},
            {"cell": 1, "beat_id": "beat-2", "path": str(frame_two)},
        ],
    )
    advance_revision(tmp_path, 1, "ng-01", "video")


def test_group_video_builds_dialogue_from_canonical_beat_fields(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import (
        _build_segments,
        _dialogue_required,
    )

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")
    beat = {
        "id": "beat-4",
        "beat_number": 4,
        "audio_type": "dialogue",
        "narration_segment": "趴下！",
        "speaker": "老郑_中年时期",
        "video_prompt": "老郑猛地把阿远按倒。",
    }

    segments = _build_segments(
        {},
        [beat],
        {"beat_ids": ["beat-4"], "cell_assets": [{"beat_id": "beat-4", "path": str(frame)}]},
    )

    assert segments[0].dialogue == "趴下！"
    assert segments[0].speaker == "老郑_中年时期"
    assert segments[0].tone == ""
    assert _dialogue_required(beat, segments[0]) is True


def test_group_video_optimizes_each_segment_concurrently_before_one_director_submit(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)
    submitted = []
    optimization_contexts = []
    active = 0
    max_active = 0

    class Optimizer:
        async def optimize_segment(self, segment, context, mode):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            optimization_contexts.append((segment.segment_id, context, mode))
            await asyncio.sleep(0)
            active -= 1
            return SimpleNamespace(prompt=f"优化：{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "雨夜里他抬头。", "dialogue": "别走。", "speaker": "阿明", "tone": "急切"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "她停在门口。", "dialogue": "我会回来。", "speaker": "小雨", "tone": "克制"},
        ]

    async def generate(ctx, *, segments, output_path, **_kwargs):
        submitted.append((ctx, segments, output_path))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="fl2va")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    monkeypatch.setattr(narrative_group_video, "create_h3_prompt_optimizer", lambda **_kwargs: Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")
    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1, "model": "MiniMax-H3", "mode": "auto"}},
        ctx,
    )

    assert len(submitted) == 1
    assert [segment.segment_id for segment in submitted[0][1]] == ["beat-1", "beat-2"]
    assert [segment.prompt for segment in submitted[0][1]] == ["优化：beat-1", "优化：beat-2"]
    assert [item[0] for item in optimization_contexts] == ["beat-1", "beat-2"]
    assert max_active == 2
    assert optimization_contexts[0][1].first_frame_sha256
    assert optimization_contexts[0][1].dialogue_required is True
    assert result["status"] == "completed"
    state = load_groups(tmp_path, 1)[0].stages["video"]
    assert state.video_asset.endswith("ng-01_r1.mp4")
    assert state.manifest_asset.endswith("ng-01_r1.manifest.json")


def test_group_video_optimizer_failure_does_not_submit_director_task(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)
    submitted = []

    class FailingOptimizer:
        async def optimize_segment(self, *_args, **_kwargs):
            raise RuntimeError("optimizer unavailable")

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(*_args, **_kwargs):
        submitted.append(True)
        raise AssertionError("must not submit")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    monkeypatch.setattr(narrative_group_video, "create_h3_prompt_optimizer", lambda **_kwargs: FailingOptimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")

    try:
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )
    except RuntimeError as exc:
        assert "optimizer unavailable" in str(exc)
    else:
        raise AssertionError("expected optimizer failure")

    assert submitted == []
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "failed"


def test_group_video_external_tts_requires_successful_stems(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return SimpleNamespace(prompt=segment.prompt)

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="i2va")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    monkeypatch.setattr(narrative_group_video, "create_h3_prompt_optimizer", lambda **_kwargs: Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", lambda *_args: (_ for _ in ()).throw(RuntimeError("demucs failed")))
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")

    try:
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )
    except RuntimeError as exc:
        assert "demucs failed" in str(exc)
    else:
        raise AssertionError("expected stem separation failure")
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "failed"


def test_group_video_all_h3_native_completes_without_stems(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return SimpleNamespace(prompt=segment.prompt)

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "dialogue_source": "h3_native"},
            {"id": "beat-2", "beat_number": 2, "dialogue_source": "h3_native"},
        ]

    async def generate(_ctx, *, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="i2va")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    monkeypatch.setattr(narrative_group_video, "create_h3_prompt_optimizer", lambda **_kwargs: Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", lambda *_args: (_ for _ in ()).throw(AssertionError("not requested")))
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")

    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    assert result["status"] == "completed"


def test_group_video_never_overwrites_a_newer_revision(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import advance_revision, load_groups

    _seed_group(tmp_path)

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, output_path, **_kwargs):
        advance_revision(tmp_path, 1, "ng-01", "video", regenerate=True)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="i2va")

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return SimpleNamespace(prompt=segment.prompt)

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    monkeypatch.setattr(narrative_group_video, "create_h3_prompt_optimizer", lambda **_kwargs: Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    async def separate(video, _directory):
        return {
            "original_audio_path": str(video), "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video), "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")
    narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    state = load_groups(tmp_path, 1)[0].stages["video"]
    assert state.revision == 2
    assert state.status == "queued"
