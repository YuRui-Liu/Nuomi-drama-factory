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
    assert materialized.video_plan.units[0].id.startswith("segment:ng-01")


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
