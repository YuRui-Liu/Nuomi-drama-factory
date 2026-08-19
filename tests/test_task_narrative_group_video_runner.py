from pathlib import Path
from types import SimpleNamespace


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


def test_group_video_submits_all_beats_in_one_director_task_and_records_manifest(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)
    submitted = []

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
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), project_id="demo")
    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1, "model": "MiniMax-H3", "mode": "auto"}},
        ctx,
    )

    assert len(submitted) == 1
    assert [segment.segment_id for segment in submitted[0][1]] == ["beat-1", "beat-2"]
    assert result["status"] == "completed"
    state = load_groups(tmp_path, 1)[0].stages["video"]
    assert state.video_asset.endswith("ng-01_r1.mp4")
    assert state.manifest_asset.endswith("ng-01_r1.manifest.json")


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

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), project_id="demo")
    narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    state = load_groups(tmp_path, 1)[0].stages["video"]
    assert state.revision == 2
    assert state.status == "queued"
