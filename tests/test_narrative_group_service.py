from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from novelvideo.narrative_groups.service import (
    advance_revision,
    group_beats,
    layout_for_group,
    load_groups,
    record_stage_result,
    rollback_stage_revision,
    stage_history,
    rebuild_groups,
    save_groups,
    reserve_video_revision,
)


@pytest.mark.parametrize(
    ("count", "shape"),
    [(1, (2, 2)), (4, (2, 2)), (5, (2, 3)), (6, (2, 3)), (7, (3, 3)), (9, (3, 3))],
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
    assert groups[0].layout.capacity == 4


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
