from types import SimpleNamespace


def test_dialogue_source_recompose_only_runs_episode_composer(tmp_path, monkeypatch):
    from novelvideo.narrative_groups.service import advance_revision, group_beats, save_groups
    from novelvideo.task_backend.runners import narrative_group_video_compose as subject

    save_groups(tmp_path, 1, group_beats([{"id": "beat-1", "beat_number": 1}]))
    advance_revision(tmp_path, 1, "ng-01", "video")
    calls = []

    async def beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}]

    def compose(envelope, ctx):
        calls.append((envelope, ctx))
        return {"video_path": "final.mp4"}

    monkeypatch.setattr(subject, "_load_canonical_beats", beats)
    monkeypatch.setattr("novelvideo.task_backend.runners.video.run_compose_episode", compose)
    ctx = SimpleNamespace(output_dir=str(tmp_path))

    result = subject.run_narrative_group_video_compose({
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
