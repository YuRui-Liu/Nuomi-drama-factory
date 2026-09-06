from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from novelvideo.narrative_groups.service import (
    advance_revision,
    group_beats,
    layout_for_group,
    load_groups,
    record_stage_result,
    record_video_segment_result,
    rollback_stage_revision,
    stage_history,
    rebuild_groups,
    save_groups,
    reserve_video_revision,
    sidecar_path,
    ensure_groups,
    update_video_plan,
)


def test_record_video_segment_result_compares_video_revision(tmp_path):
    group = ensure_groups(tmp_path, 1, [{"id": "beat-1"}])[0]
    save_groups(tmp_path, 1, [replace(
        group,
        video_segments=({"id": "segment-1", "status": "pending"},),
    )])
    advance_revision(tmp_path, 1, group.id, "video")

    updated = record_video_segment_result(
        tmp_path,
        1,
        group.id,
        "segment-1",
        status="completed",
        expected_revision=1,
    )

    assert updated.video_segments[0]["status"] == "completed"


def test_record_video_segment_result_rejects_stale_revision_without_write(
    tmp_path,
):
    group = ensure_groups(tmp_path, 1, [{"id": "beat-1"}])[0]
    original_segment = {"id": "segment-1", "status": "pending"}
    save_groups(tmp_path, 1, [replace(
        group,
        video_segments=(original_segment,),
    )])
    advance_revision(tmp_path, 1, group.id, "video")

    with pytest.raises(RuntimeError, match="video revision is stale"):
        record_video_segment_result(
            tmp_path,
            1,
            group.id,
            "segment-1",
            status="failed",
            error="old task",
            expected_revision=0,
        )

    assert load_groups(tmp_path, 1)[0].video_segments == (original_segment,)


@pytest.mark.parametrize(
    ("count", "shape"),
    [
        (1, (1, 1)),
        (2, (1, 2)),
        (3, (2, 2)),
        (4, (2, 2)),
        (5, (2, 3)),
        (6, (2, 3)),
        (7, (3, 3)),
        (9, (3, 3)),
    ],
)
def test_layout_for_group_uses_supported_shapes(count, shape):
    assert layout_for_group(count).shape == shape


def test_layout_rejects_empty_or_oversized_group():
    with pytest.raises(ValueError):
        layout_for_group(0)
    with pytest.raises(ValueError):
        layout_for_group(10)


def test_group_beats_spills_after_nine_and_keeps_order():
    beats = [SimpleNamespace(id=str(index)) for index in range(11)]

    groups = group_beats(beats)

    assert [group.beat_ids for group in groups] == [
        tuple(str(index) for index in range(9)),
        ("9", "10"),
    ]
    assert groups[1].cell_to_beat[0].beat_id == "9"
    assert groups[0].id == "ng-01"
    assert groups[1].id == "ng-02"


def test_group_beats_accepts_mapping_ids_without_creating_padding_cells():
    groups = group_beats([{"id": "beat-a"}, {"beat_id": "beat-b"}])

    assert [(item.cell, item.beat_id) for item in groups[0].cell_to_beat] == [
        (0, "beat-a"),
        (1, "beat-b"),
    ]
    assert groups[0].layout.capacity == 2


def test_group_beats_respects_scene_and_time_continuity():
    beats = [
        {"id": "1", "scene_id": "mall", "time_of_day": "night"},
        {"id": "2", "scene_id": "mall", "time_of_day": "night"},
        {"id": "3", "scene_id": "hall", "time_of_day": "night"},
        {"id": "4", "scene_id": "hall", "time_of_day": "later"},
    ]

    groups = group_beats(beats)

    assert [group.beat_ids for group in groups] == [("1", "2"), ("3",), ("4",)]


def test_stage_result_persists_assets_error_and_status(tmp_path):
    groups = group_beats([{"id": "1"}, {"id": "2"}])
    save_groups(tmp_path, 1, groups)
    advance_revision(tmp_path, 1, "ng-01", "render")

    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "render",
        status="partial_failure",
        grid_asset="grid.png",
        cell_assets=[{"cell": 0, "path": "beat-1.png"}],
        error="cell 1 failed",
        expected_revision=1,
    )

    stage = load_groups(tmp_path, 1)[0].stages["render"]
    assert stage.status == "partial_failure"
    assert stage.grid_asset == "grid.png"
    assert stage.cell_assets == ({"cell": 0, "path": "beat-1.png"},)
    assert stage.error == "cell 1 failed"


def test_concurrent_revision_updates_are_not_lost(tmp_path, monkeypatch):
    import threading
    from novelvideo.narrative_groups import service

    save_groups(tmp_path, 1, group_beats([{"id": "1"}]))
    # Simulate independent service instances that do not share an in-memory lock.
    monkeypatch.setattr(service, "_sidecar_lock", lambda *_: threading.RLock())

    with ThreadPoolExecutor(max_workers=8) as pool:
        revisions = list(
            pool.map(
                lambda _: advance_revision(
                    tmp_path, 1, "ng-01", "render", regenerate=True
                )[1],
                range(12),
            )
        )

    assert sorted(revisions) == list(range(1, 13))
    assert load_groups(tmp_path, 1)[0].stages["render"].revision == 12


def test_stale_revision_completion_cannot_overwrite_new_revision(tmp_path):
    save_groups(tmp_path, 1, group_beats([{"id": "1"}]))
    advance_revision(tmp_path, 1, "ng-01", "render")
    advance_revision(tmp_path, 1, "ng-01", "render", regenerate=True)

    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "render",
        expected_revision=1,
        status="completed",
        grid_asset="stale-r1.png",
    )

    stage = load_groups(tmp_path, 1)[0].stages["render"]
    assert stage.revision == 2
    assert stage.status == "queued"
    assert stage.grid_asset == ""


def test_concurrent_video_reservations_accept_only_one_expected_revision(tmp_path, monkeypatch):
    import threading
    from novelvideo.narrative_groups import service

    save_groups(tmp_path, 1, group_beats([{"id": "1"}]))
    monkeypatch.setattr(service, "_sidecar_lock", lambda *_: threading.RLock())

    def reserve():
        try:
            return reserve_video_revision(tmp_path, 1, "ng-01", expected_revision=0)[1].revision
        except RuntimeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))

    assert results.count(1) == 1
    assert sum(isinstance(item, RuntimeError) for item in results) == 1


def test_rebuild_resets_stage_when_mapping_changes(tmp_path):
    original = group_beats([{"id": "1"}, {"id": "2"}])
    save_groups(tmp_path, 1, original)
    advance_revision(tmp_path, 1, "ng-01", "render")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "render",
        expected_revision=1,
        status="completed",
        grid_asset="old.png",
    )

    rebuilt = rebuild_groups(tmp_path, 1, [{"id": "1"}, {"id": "3"}])

    assert rebuilt[0].beat_ids == ("1", "3")
    assert rebuilt[0].stages["render"].revision == 0
    assert rebuilt[0].stages["render"].grid_asset == ""


def test_stage_result_persists_provider_mode_frames_and_history(tmp_path):
    save_groups(tmp_path, 1, group_beats([{"id": "beat-1"}]))
    advance_revision(tmp_path, 1, "ng-01", "render")
    record_stage_result(
        tmp_path, 1, "ng-01", "render", expected_revision=1,
        status="completed", grid_asset="grid-r1.png",
        cell_assets=[{"cell": 0, "beat_id": "beat-1", "path": "cell-r1.png", "first_frame": True, "last_frame": False}],
        actual_provider="grsai", actual_model="gpt-image-2", actual_mode="grid",
    )
    advance_revision(tmp_path, 1, "ng-01", "render", regenerate=True)
    record_stage_result(
        tmp_path, 1, "ng-01", "render", expected_revision=2,
        status="completed", grid_asset="grid-r2.png",
        cell_assets=[{"cell": 0, "beat_id": "beat-1", "path": "cell-r2.png"}],
        actual_provider="grsai", actual_model="gpt-image-2", actual_mode="grid",
    )

    history = stage_history(tmp_path, 1, "ng-01", "render")
    assert [item["revision"] for item in history] == [1]
    restored = rollback_stage_revision(tmp_path, 1, "ng-01", "render", revision=1)
    assert restored.stages["render"].revision == 3
    assert restored.stages["render"].grid_asset == "grid-r1.png"
    assert restored.stages["render"].actual_provider == "grsai"
    assert restored.stages["render"].cell_assets[0]["first_frame"] is True


def test_ensure_groups_persists_deterministic_recommended_video_plan(tmp_path):
    beats = [
        {"id": "beat-1", "duration_seconds": 4},
        {"id": "beat-2", "video_duration": 5},
        {"id": "beat-3", "duration": 2},
        {"id": "beat-4", "duration_seconds": 3},
        {"id": "beat-5", "duration_seconds": 4},
    ]

    group = ensure_groups(tmp_path, 1, beats)[0]

    assert group.video_plan.to_dict() == {
        "revision": 1,
        "source": "recommended",
        "units": [
            {
                "id": "unit-01",
                "beat_ids": ["beat-1", "beat-2"],
                "mode": "fl2va",
                "duration_seconds": 9.0,
                "reason": "recommended_adjacent_pair",
            },
            {
                "id": "unit-02",
                "beat_ids": ["beat-3"],
                "mode": "i2va",
                "duration_seconds": 2.0,
                "reason": "recommended_singleton",
            },
            {
                "id": "unit-03",
                "beat_ids": ["beat-4", "beat-5"],
                "mode": "fl2va",
                "duration_seconds": 7.0,
                "reason": "recommended_adjacent_pair",
            },
        ],
        "total_duration_seconds": 18.0,
    }
    assert load_groups(tmp_path, 1)[0].video_plan == group.video_plan


def test_ensure_groups_migrates_legacy_sidecar_without_video_plan(tmp_path):
    import json

    beats = [{"id": "beat-1"}, {"id": "beat-2"}]
    save_groups(tmp_path, 1, group_beats(beats))
    payload = json.loads(sidecar_path(tmp_path, 1).read_text("utf-8"))
    for item in payload["groups"]:
        item.pop("video_plan", None)
    sidecar_path(tmp_path, 1).write_text(json.dumps(payload), encoding="utf-8")

    group = ensure_groups(tmp_path, 1, beats)[0]

    assert group.video_plan.revision == 1
    assert group.video_plan.units[0].beat_ids == ("beat-1", "beat-2")
    assert "video_plan" in sidecar_path(tmp_path, 1).read_text("utf-8")


def test_ensure_groups_migrates_missing_plan_when_canonical_beat_is_missing(tmp_path):
    import json

    original_beats = [
        {"id": "beat-1", "duration_seconds": 6},
        {"id": "beat-2", "duration_seconds": 2},
        {"id": "beat-3", "duration_seconds": 4},
    ]
    save_groups(tmp_path, 1, group_beats(original_beats))
    payload = json.loads(sidecar_path(tmp_path, 1).read_text("utf-8"))
    payload["groups"][0].pop("video_plan")
    sidecar_path(tmp_path, 1).write_text(json.dumps(payload), encoding="utf-8")

    group = ensure_groups(
        tmp_path,
        1,
        [original_beats[0], original_beats[2]],
    )[0]

    assert group.beat_ids == ("beat-1", "beat-2", "beat-3")
    assert tuple(
        beat_id for unit in group.video_plan.units for beat_id in unit.beat_ids
    ) == group.beat_ids
    assert group.video_plan.total_duration_seconds == 15.0
    assert load_groups(tmp_path, 1)[0].video_plan == group.video_plan


def test_ensure_groups_recommends_and_saves_invalid_nonempty_video_plan(tmp_path):
    import json

    beats = [{"id": "beat-1"}, {"id": "beat-2"}, {"id": "beat-3"}]
    save_groups(tmp_path, 1, group_beats(beats))
    payload = json.loads(sidecar_path(tmp_path, 1).read_text("utf-8"))
    payload["groups"][0]["video_plan"] = {
        "revision": 7,
        "source": "manual",
        "units": [
            {
                "id": "unit-01",
                "beat_ids": ["beat-2", "beat-1"],
                "mode": "fl2va",
                "duration_seconds": 10,
                "reason": "manual_adjacent_pair",
            },
            {
                "id": "unit-02",
                "beat_ids": ["beat-3"],
                "mode": "i2va",
                "duration_seconds": 5,
                "reason": "manual_singleton",
            },
        ],
        "total_duration_seconds": 15,
    }
    sidecar_path(tmp_path, 1).write_text(json.dumps(payload), encoding="utf-8")

    group = ensure_groups(tmp_path, 1, beats)[0]

    assert group.video_plan.revision == 1
    assert group.video_plan.source == "recommended"
    assert tuple(
        beat_id for unit in group.video_plan.units for beat_id in unit.beat_ids
    ) == group.beat_ids
    assert load_groups(tmp_path, 1)[0].video_plan == group.video_plan


def test_update_video_plan_validates_partition_and_invalidates_video_assets(tmp_path):
    beats = [{"id": f"beat-{index}"} for index in range(1, 4)]
    group = ensure_groups(tmp_path, 1, beats)[0]
    advance_revision(tmp_path, 1, group.id, "video")
    record_stage_result(
        tmp_path,
        1,
        group.id,
        "video",
        expected_revision=1,
        status="completed",
        video_asset="old.mp4",
        manifest_asset="old.json",
    )

    updated = update_video_plan(
        tmp_path,
        1,
        group.id,
        beats,
        expected_revision=1,
        units=[{"beat_ids": ["beat-1"]}, {"beat_ids": ["beat-2", "beat-3"]}],
    )

    assert updated.video_plan.revision == 2
    assert updated.video_plan.source == "manual"
    assert [unit.mode for unit in updated.video_plan.units] == ["i2va", "fl2va"]
    assert updated.stages["video"].revision == 1
    assert updated.stages["video"].status == "pending"
    assert updated.stages["video"].video_asset == ""
    with pytest.raises(RuntimeError, match="video plan revision is stale"):
        update_video_plan(
            tmp_path,
            1,
            group.id,
            beats,
            expected_revision=1,
            units=[{"beat_ids": ["beat-1", "beat-2"]}, {"beat_ids": ["beat-3"]}],
        )
    with pytest.raises(ValueError, match="complete ordered partition"):
        update_video_plan(
            tmp_path,
            1,
            group.id,
            beats,
            expected_revision=2,
            units=[{"beat_ids": ["beat-1", "beat-3"]}, {"beat_ids": ["beat-2"]}],
        )


def test_update_video_plan_uses_safe_duration_for_missing_canonical_beat(tmp_path):
    original_beats = [{"id": "beat-1", "duration_seconds": 3}, {"id": "beat-2"}]
    group = ensure_groups(tmp_path, 1, original_beats)[0]

    updated = update_video_plan(
        tmp_path,
        1,
        group.id,
        [original_beats[0]],
        expected_revision=1,
        units=[{"beat_ids": ["beat-1"]}, {"beat_ids": ["beat-2"]}],
    )

    assert [unit.duration_seconds for unit in updated.video_plan.units] == [3.0, 5.0]
    assert updated.video_plan.total_duration_seconds == 8.0


def test_video_plan_update_before_reservation_rejects_old_plan_task(tmp_path):
    beats = [{"id": "beat-1"}, {"id": "beat-2"}]
    group = ensure_groups(tmp_path, 1, beats)[0]

    updated = update_video_plan(
        tmp_path,
        1,
        group.id,
        beats,
        expected_revision=1,
        units=[{"beat_ids": ["beat-1"]}, {"beat_ids": ["beat-2"]}],
    )

    with pytest.raises(RuntimeError, match="video plan revision is stale"):
        reserve_video_revision(
            tmp_path,
            1,
            group.id,
            expected_revision=0,
            expected_plan_revision=1,
        )
    persisted = load_groups(tmp_path, 1)[0]
    assert persisted.video_plan == updated.video_plan
    assert persisted.stages["video"].status == "pending"
    assert persisted.stages["video"].revision == 0


@pytest.mark.parametrize("task_status", ["queued", "running"])
def test_video_reservation_before_plan_update_rejects_update(tmp_path, task_status):
    beats = [{"id": "beat-1"}, {"id": "beat-2"}]
    group = ensure_groups(tmp_path, 1, beats)[0]
    reserved, _ = reserve_video_revision(
        tmp_path,
        1,
        group.id,
        expected_revision=0,
        expected_plan_revision=1,
    )
    if task_status == "running":
        record_stage_result(
            tmp_path,
            1,
            group.id,
            "video",
            expected_revision=1,
            status="running",
        )

    with pytest.raises(RuntimeError, match=f"video stage is {task_status}"):
        update_video_plan(
            tmp_path,
            1,
            group.id,
            beats,
            expected_revision=1,
            units=[{"beat_ids": ["beat-1"]}, {"beat_ids": ["beat-2"]}],
        )

    persisted = load_groups(tmp_path, 1)[0]
    assert persisted.video_plan == reserved.video_plan
    assert persisted.stages["video"].status == task_status
    assert persisted.stages["video"].revision == 1
