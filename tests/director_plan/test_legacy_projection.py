from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.narrative_groups.models import (
    CellMapping,
    GridLayout,
    GroupStageState,
    NarrativeGroup,
    VideoPlan,
    VideoPlanUnit,
    VideoSettings,
)
from novelvideo.narrative_groups import service


def _shot(shot_id: str, source_span_id: str) -> ShotPlan:
    return ShotPlan(
        id=shot_id,
        source_span_ids=(source_span_id,),
        subject="hero",
        action="acts",
        visible_start_state="before",
        visible_end_state="after",
        duration_seconds=3,
    )


def _group(
    group_id: str,
    ordinal: int,
    source_span_ids: tuple[str, ...],
    shot_ids: tuple[str, ...],
) -> NarrativeGroupPlan:
    return NarrativeGroupPlan(
        id=group_id,
        ordinal=ordinal,
        source_span_ids=source_span_ids,
        scene_anchor="hallway",
        time_anchor="night",
        objective=f"objective-{ordinal}",
        visible_turn=f"turn-{ordinal}",
        relation_to_previous="single" if ordinal == 1 else "causal",
        shots=tuple(
            _shot(shot_id, source_span_ids[min(index, len(source_span_ids) - 1)])
            for index, shot_id in enumerate(shot_ids)
        ),
    )


def _activate(
    project_dir: Path,
    groups: tuple[NarrativeGroupPlan, ...],
    *,
    revision_id: str = "rev-active",
) -> DirectorPlanRevision:
    revision = DirectorPlanRevision(
        revision_id=revision_id,
        episode=1,
        status="review_required",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=groups,
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(project_dir)
    store.save(revision)
    return store.activate(1, revision.revision_id)


def test_active_plan_projects_exact_groups_without_legacy_bucketing(
    monkeypatch, tmp_path: Path
) -> None:
    spans = tuple(f"span-{index}" for index in range(1, 11))
    active = _activate(
        tmp_path,
        (
            _group("director-a", 1, spans[:5], ("shot-a1", "shot-a2")),
            _group("director-b", 2, spans[5:], ("shot-b1", "shot-b2", "shot-b3")),
        ),
    )

    def forbidden_legacy_ensure(*args, **kwargs):
        raise AssertionError("active projection must not call ensure_groups")

    monkeypatch.setattr(service, "ensure_groups", forbidden_legacy_ensure)

    groups = service.load_effective_groups(
        tmp_path,
        1,
        [{"id": f"beat-{index}"} for index in range(1, 13)],
    )

    assert [group.id for group in groups] == ["director-a", "director-b"]
    assert groups[0].beat_ids == spans[:5]
    assert groups[0].source_span_ids == spans[:5]
    assert groups[0].shot_ids == ("shot-a1", "shot-a2")
    assert groups[0].layout == GridLayout(rows=1, columns=2, capacity=2)
    assert groups[0].cell_to_beat == (
        CellMapping(cell=0, beat_id="shot-a1"),
        CellMapping(cell=1, beat_id="shot-a2"),
    )
    assert groups[0].objective == "objective-1"
    assert groups[0].visible_turn == "turn-1"
    assert groups[0].director_revision_id == active.revision_id


def test_without_active_plan_uses_legacy_ensure_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    sentinel = NarrativeGroup(
        id="ng-01",
        ordinal=1,
        beat_ids=("beat-1",),
        layout=GridLayout(rows=1, columns=1, capacity=1),
        cell_to_beat=(CellMapping(cell=0, beat_id="beat-1"),),
    )
    calls = []

    def fake_ensure(project_dir, episode, beats):
        calls.append((project_dir, episode, beats))
        return [sentinel]

    monkeypatch.setattr(service, "ensure_groups", fake_ensure)
    legacy_beats = [{"id": "beat-1"}]

    groups = service.load_effective_groups(tmp_path, 1, legacy_beats)

    assert groups == [sentinel]
    assert calls == [(tmp_path, 1, legacy_beats)]


def test_active_projection_preserves_same_id_legacy_generation_state(
    tmp_path: Path,
) -> None:
    video_plan = VideoPlan(
        revision=4,
        source="manual",
        units=(
            VideoPlanUnit(
                id="unit-01",
                beat_ids=("span-1",),
                mode="i2va",
                duration_seconds=5,
                reason="kept",
            ),
        ),
        total_duration_seconds=5,
    )
    video_settings = VideoSettings(
        workflow_id="runninghub:minimax-h3",
        revision=3,
        overrides={"resolution": "1080p"},
    )
    stages = {
        "sketch": GroupStageState(status="completed", revision=2),
        "render": GroupStageState(status="review", revision=3),
        "video": GroupStageState(status="completed", revision=4),
    }
    service.save_groups(
        tmp_path,
        1,
        [
            NarrativeGroup(
                id="director-a",
                ordinal=1,
                beat_ids=("span-1",),
                layout=GridLayout(rows=1, columns=1, capacity=1),
                cell_to_beat=(CellMapping(cell=0, beat_id="shot-1"),),
                video_plan=video_plan,
                video_settings=video_settings,
                stages=stages,
                errors=({"code": "kept"},),
                source_span_ids=("span-1",),
                shot_ids=("shot-1",),
                director_revision_id="rev-active",
            )
        ],
    )
    _activate(
        tmp_path,
        (_group("director-a", 1, ("span-1",), ("shot-1",)),),
    )

    [projected] = service.load_effective_groups(tmp_path, 1, [])

    assert projected.video_plan == video_plan
    assert projected.video_settings == video_settings
    assert projected.stages == stages
    assert projected.errors == ({"code": "kept"},)


def test_active_new_group_can_advance_and_persist_generation_revision(
    tmp_path: Path,
) -> None:
    _activate(
        tmp_path,
        (_group("director-new", 1, ("span-1",), ("shot-1",)),),
    )

    queued, revision = service.advance_revision(
        tmp_path, 1, "director-new", "sketch"
    )

    assert revision == 1
    assert queued.stages["sketch"].status == "queued"
    [persisted] = service.load_groups(tmp_path, 1)
    assert persisted.id == "director-new"
    assert persisted.shot_ids == ("shot-1",)
    assert persisted.stages["sketch"].revision == 1


def test_active_projection_clears_stale_generation_state_when_structure_changes(
    tmp_path: Path,
) -> None:
    settings = VideoSettings(
        workflow_id="runninghub:minimax-h3",
        revision=3,
        overrides={"resolution": "1080p"},
    )
    stale_plan = VideoPlan(
        revision=4,
        source="manual",
        units=(
            VideoPlanUnit(
                id="unit-01",
                beat_ids=("span-1",),
                mode="i2va",
                duration_seconds=5,
                reason="stale",
            ),
        ),
        total_duration_seconds=5,
    )
    stale_stages = {
        "sketch": GroupStageState(
            status="completed", revision=2, grid_asset="old-grid.png"
        ),
        "render": GroupStageState(status="completed", revision=2),
        "video": GroupStageState(
            status="completed", revision=4, video_asset="old-video.mp4"
        ),
    }
    service.save_groups(
        tmp_path,
        1,
        [
            NarrativeGroup(
                id="director-a",
                ordinal=1,
                beat_ids=("span-1",),
                layout=GridLayout(rows=1, columns=1, capacity=1),
                cell_to_beat=(CellMapping(cell=0, beat_id="shot-old"),),
                video_plan=stale_plan,
                video_settings=settings,
                stages=stale_stages,
                errors=({"code": "stale"},),
                source_span_ids=("span-1",),
                shot_ids=("shot-old",),
                director_revision_id="rev-old",
            )
        ],
    )
    _activate(
        tmp_path,
        (_group("director-a", 1, ("span-1", "span-2"), ("shot-new",)),),
        revision_id="rev-new",
    )

    [projected] = service.load_effective_groups(tmp_path, 1, [])

    assert projected.video_settings == settings
    assert projected.video_plan == VideoPlan()
    assert projected.stages == {
        "sketch": GroupStageState(),
        "render": GroupStageState(),
        "video": GroupStageState(),
    }
    assert projected.errors == ()
    assert projected.source_span_ids == ("span-1", "span-2")
    assert projected.shot_ids == ("shot-new",)
    assert projected.director_revision_id == "rev-new"

def test_active_projection_video_inputs_follow_shot_assets(tmp_path: Path) -> None:
    _activate(
        tmp_path,
        (_group("director-a", 1, ("line-11", "line-12"), ("shot-01-01", "shot-01-02")),),
    )
    service.load_effective_groups(tmp_path, 1, [])
    service.advance_revision(tmp_path, 1, "director-a", "render")
    service.record_stage_result(
        tmp_path,
        1,
        "director-a",
        "render",
        expected_revision=1,
        status="completed",
        cell_assets=(
            {"cell": 0, "beat_id": "shot-01-01", "path": "first.png"},
            {"cell": 1, "beat_id": "shot-01-02", "path": "second.png"},
        ),
    )

    [group] = service.load_effective_groups(tmp_path, 1, [])

    assert [item["beat_id"] for item in group.video_inputs] == [
        "shot-01-01",
        "shot-01-02",
    ]
    assert all(item["has_first_frame"] for item in group.video_inputs)


def test_active_projection_accepts_shot_partition_for_video_plan(tmp_path: Path) -> None:
    _activate(
        tmp_path,
        (_group("director-a", 1, ("line-11", "line-12"), ("shot-01-01", "shot-01-02")),),
    )
    [group] = service.load_effective_groups(tmp_path, 1, [])
    shots = service.generation_beats_for_group(tmp_path, 1, group.id, [])

    updated = service.update_video_plan(
        tmp_path,
        1,
        group.id,
        shots,
        expected_revision=group.video_plan.revision,
        units=[{"beat_ids": ["shot-01-01"]}, {"beat_ids": ["shot-01-02"]}],
    )

    assert [unit.beat_ids for unit in updated.video_plan.units] == [
        ("shot-01-01",),
        ("shot-01-02",),
    ]
    assert [unit.duration_seconds for unit in updated.video_plan.units] == [3.0, 3.0]
