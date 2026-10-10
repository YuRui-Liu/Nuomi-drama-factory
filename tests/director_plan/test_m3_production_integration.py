from __future__ import annotations

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    StyleProjections,
    StyleSnapshot,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.narrative_groups.service import load_materialized_groups


def _group() -> NarrativeGroupPlan:
    return NarrativeGroupPlan(
        id="ng-01", ordinal=1, source_span_ids=("s1", "s2"),
        scene_anchor="room", time_anchor="day", objective="leave",
        visible_turn="door opens", relation_to_previous="single",
        style_snapshot_id="snapshot-real",
        shots=tuple(
            ShotPlan(
                id=f"shot-{index}", source_span_ids=(f"s{index}",),
                subject="hero", action="walks", visible_start_state="inside",
                visible_end_state="outside", duration_seconds=3,
            )
            for index in (1, 2)
        ),
    )


def test_active_plan_materializes_generation_video_and_effective_style(tmp_path) -> None:
    snapshot = StyleSnapshot(
        snapshot_id="snapshot-real", style_id="anime", style_version="1",
        catalog_hash="catalog", style_hash="style-hash",
        projections=StyleProjections(
            director="director", image="image", video="video", panel_tag="anime"
        ),
    )
    revision = DirectorPlanRevision.new(
        episode=1, source_script_hash="script", director_model="deepseek",
        prompt_version="v2", project_style_snapshot_id=snapshot.snapshot_id,
        project_style_snapshot=snapshot, groups=(_group(),),
    ).model_copy(update={
        "status": "review_required", "validation_report": ValidationReport(passed=True)
    })
    # Activation requires a passed report; an empty report is passed.
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)

    materialized = load_materialized_groups(tmp_path, 1)[0]

    assert [item["layout"] for item in materialized.generation_batches] == ["diptych"]
    assert len(materialized.video_segments) == 2
    assert materialized.effective_style_snapshot["style_hash"] == "style-hash"
    assert materialized.video_plan.units[0].id.startswith(f"segment:{revision.revision_id}:ng-01:")
    from novelvideo.narrative_groups.service import load_effective_groups
    landscape = load_effective_groups(tmp_path, 1, [], aspect_ratio="16:9")[0]
    assert landscape.generation_batches[0]["aspect_ratio"] == "16:9"
    # Worker-side refresh must retain the last authoritative project ratio.
    assert load_materialized_groups(tmp_path, 1)[0].generation_batches[0]["aspect_ratio"] == "16:9"


def test_local_composition_plan_keeps_segment_order_and_transition_rules() -> None:
    from novelvideo.task_backend.runners.narrative_group_video_compose import (
        SegmentCompositionItem,
        build_local_composition_plan,
    )

    plan = build_local_composition_plan((
        SegmentCompositionItem(2, 1, "b.mp4", "time_jump"),
        SegmentCompositionItem(1, 1, "a.mp4", "single"),
    ))

    assert plan.paths == ("a.mp4", "b.mp4")
    assert plan.transitions[0].kind == "dissolve"
    assert plan.transitions[0].frames == 8


def test_capability_conflict_blocks_only_video_for_affected_group(tmp_path):
    from types import SimpleNamespace
    from novelvideo.narrative_groups.service import _materialize_active_groups

    original = _group()
    long_group = original.model_copy(update={"creative_contract_version": 1, "shots": (
        original.shots[0].model_copy(update={"duration_seconds": 20}),)})
    other = original.model_copy(update={"id": "ng-02", "ordinal": 2})
    active = SimpleNamespace(groups=(long_group, other), revision_id="revision-1",
                             project_style_snapshot=None, project_style_snapshot_id="snapshot-real")
    groups = _materialize_active_groups(tmp_path, 1, active)
    assert len(groups) == 2
    assert groups[0].generation_batches
    assert groups[0].stages["render"].status == "pending"
    assert groups[0].stages["video"].status == "failed"
    assert groups[0].errors[0]["code"] == "capability_conflict"
    assert "shot-1" in groups[0].errors[0]["message"]
    assert not groups[0].video_plan.units
    assert len(groups[1].video_plan.units) == 2
    assert _materialize_active_groups(tmp_path, 1, active)[0].errors == groups[0].errors
    repaired = long_group.model_copy(update={"shots": (
        long_group.shots[0].model_copy(update={"duration_seconds": 10}),)})
    active.groups = (repaired, other)
    active.revision_id = "revision-repaired"
    recovered = _materialize_active_groups(tmp_path, 1, active)[0]
    assert recovered.video_plan.units[0].duration_seconds == 10
    assert recovered.stages["video"].status == "pending"
    assert "capability_conflict" not in recovered.stages["video"].provider_parameters
    assert not recovered.errors


def test_large_creative_group_materializes_all_physical_packs(tmp_path):
    from types import SimpleNamespace
    from novelvideo.narrative_groups.service import _materialize_active_groups

    original = _group()
    group = original.model_copy(update={"creative_contract_version": 1, "shots": tuple(
        original.shots[0].model_copy(update={"id": f"shot-{index}", "duration_seconds": 5}) for index in range(10))})
    active = SimpleNamespace(groups=(group,), revision_id="revision-1",
                             project_style_snapshot=None, project_style_snapshot_id="snapshot-real")
    result = _materialize_active_groups(tmp_path, 1, active)[0]
    assert [len(batch["shot_ids"]) for batch in result.generation_batches] == [4, 4, 2]
    assert result.shot_ids == tuple(f"shot-{index}" for index in range(10))
    assert len(result.video_plan.units) == 10


def test_creative_materialization_retains_static_media_on_motion_revision(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.director_plan.test_creative_invalidation import revision, stages
    from novelvideo.narrative_groups.service import _materialize_active_groups, save_groups

    old = revision()
    new = revision(motion="move only after opening").model_copy(update={"revision_id": "changed-motion"})
    materialized = _materialize_active_groups(tmp_path, 1, old)[0]
    previous_stages = stages()
    save_groups(tmp_path, 1, [replace(materialized, stages=previous_stages)])
    monkeypatch.setattr(DirectorPlanStore, "load", lambda *args: old)
    updated = _materialize_active_groups(tmp_path, 1, new)[0]
    assert updated.stages["render"] == previous_stages["render"]
    assert updated.stages["video"].needs_regeneration
    assert updated.stages["video"].video_asset == "old.mp4"
    provenance = updated.generation_batches[0]["keyframe_projections"]
    assert provenance == {shot_id: {**value, "director_revision_id": new.revision_id}
                          for shot_id, value in materialized.generation_batches[0]["keyframe_projections"].items()}
    assert all(value["keyframe_projection_hash"] and value["dependency_fields"] for value in provenance.values())
