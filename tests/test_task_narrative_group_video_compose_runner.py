import asyncio
from types import SimpleNamespace

import pytest


def test_dialogue_source_recompose_only_runs_episode_composer(tmp_path, monkeypatch):
    from novelvideo.narrative_groups.service import (
        advance_revision, group_beats, record_stage_result, save_groups,
    )
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource, H3DirectorOutputManifest, H3DirectorSegment,
        build_h3_timeline_data, save_h3_director_manifest,
    )
    from novelvideo.task_backend.runners import narrative_group_video_compose as subject

    save_groups(tmp_path, 1, group_beats([{"id": "beat-1", "beat_number": 1}]))
    advance_revision(tmp_path, 1, "ng-01", "video")
    video_path = tmp_path / "videos" / "group.mp4"
    manifest_path = tmp_path / "videos" / "group.manifest.json"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(video_path),
        entries=build_h3_timeline_data((H3DirectorSegment(
            segment_id="beat-1", beat_number=1, prompt="p", duration_seconds=1,
            first_frame="frame.png", dialogue_source=DialogueSource.H3_NATIVE,
        ),)).entries,
    ))
    record_stage_result(
        tmp_path, 1, "ng-01", "video", expected_revision=1, status="completed",
        video_asset=str(video_path), manifest_asset=str(manifest_path),
    )
    calls = []

    async def beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}]

    def compose(envelope, ctx):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        calls.append((envelope, ctx))
        return {"video_path": "final.mp4"}

    monkeypatch.setattr(subject, "_load_canonical_beats", beats)
    monkeypatch.setattr("novelvideo.task_backend.runners.video._video_has_audio_stream", lambda _path: True)
    monkeypatch.setattr("novelvideo.task_backend.runners.video.run_compose_episode", compose)
    ctx = SimpleNamespace(output_dir=str(tmp_path))

    result = subject.run_narrative_group_video_compose({
        "__run_task_id": "recompose-task-1",
        "episode": 1,
        "payload": {"group_id": "ng-01", "revision": 1, "span_index": 0,
                    "dialogue_source": "h3_native"},
    }, ctx)

    assert result["recomposition_only"] is True
    assert len(calls) == 1
    assert calls[0][0]["task_type"] == "compose_episode"
    assert calls[0][0]["payload"]["beats"] == [{"id": "beat-1", "beat_number": 1}]


def test_local_composition_orders_groups_and_segments_and_records_rules():
    from novelvideo.task_backend.runners.narrative_group_video_compose import (
        SegmentCompositionItem,
        build_local_composition_plan,
    )

    plan = build_local_composition_plan(
        (
            SegmentCompositionItem(2, 1, "g2-s1.mp4", "time_jump"),
            SegmentCompositionItem(
                1, 2, "g1-s2.mp4", "progressive", has_leading_dialogue=True
            ),
            SegmentCompositionItem(1, 1, "g1-s1.mp4", "single"),
        )
    )

    assert plan.paths == ("g1-s1.mp4", "g1-s2.mp4", "g2-s1.mp4")
    assert [(rule.kind, rule.frames, rule.audio, rule.audio_ms) for rule in plan.transitions] == [
        ("hard_cut", 0, "j_cut", 300),
        ("dissolve", 8, "none", 0),
    ]
    assert all(rule.source.startswith("relation:") for rule in plan.transitions)
