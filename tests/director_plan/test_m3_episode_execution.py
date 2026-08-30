from __future__ import annotations

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.narrative_groups.service import (
    load_materialized_groups,
    record_video_segment_result,
)
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3DirectorSegment,
    H3TimelineEntry,
)


def _legacy_group(group_id: str, ordinal: int) -> NarrativeGroupPlan:
    source_id = f"s{ordinal}"
    return NarrativeGroupPlan(
        id=group_id, ordinal=ordinal, source_span_ids=(source_id,),
        scene_anchor="room", time_anchor="day", objective="advance",
        visible_turn="changed",
        relation_to_previous="single" if ordinal == 1 else "progressive",
        shots=(ShotPlan(
            id=f"shot-{ordinal}", source_span_ids=(source_id,), subject="hero",
            action="moves", visible_start_state="left", visible_end_state="right",
            duration_seconds=3,
        ),),
    )


def _activate_legacy(tmp_path) -> None:
    revision = DirectorPlanRevision.new(
        episode=1, source_script_hash="hash", director_model="director",
        prompt_version="v2", project_style_snapshot_id="legacy-snapshot",
        groups=(_legacy_group("ng-01", 1), _legacy_group("ng-02", 2)),
    ).model_copy(update={
        "status": "review_required", "validation_report": ValidationReport(passed=True)
    })
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)


def test_legacy_active_plan_materializes_with_stable_style_snapshot(tmp_path) -> None:
    _activate_legacy(tmp_path)

    groups = load_materialized_groups(tmp_path, 1)

    assert [group.generation_batches[0]["style_snapshot_id"] for group in groups] == [
        "legacy-snapshot", "legacy-snapshot"
    ]


def test_segment_failure_is_durable_and_does_not_erase_sibling_success(tmp_path) -> None:
    _activate_legacy(tmp_path)
    groups = load_materialized_groups(tmp_path, 1)
    first_id = str(groups[0].video_segments[0]["id"])
    second_id = str(groups[1].video_segments[0]["id"])

    record_video_segment_result(
        tmp_path, 1, "ng-01", first_id, status="completed",
        provider_task_id="provider-1", result={"output_path": "one.mp4"},
    )
    record_video_segment_result(
        tmp_path, 1, "ng-02", second_id, status="failed", error="transport failed",
    )

    reloaded = load_materialized_groups(tmp_path, 1)
    assert reloaded[0].video_segments[0]["status"] == "completed"
    assert reloaded[0].video_segments[0]["provider_task_id"] == "provider-1"
    assert reloaded[1].video_segments[0]["status"] == "failed"
    assert reloaded[1].video_segments[0]["error"] == "transport failed"


def test_final_manifest_preserves_each_segment_outcome_and_provider_id() -> None:
    from novelvideo.task_backend.runners.narrative_group_video import (
        _finalize_segment_manifest,
    )

    def entry(segment_id: str, start: int) -> H3TimelineEntry:
        return H3TimelineEntry(
            segment=H3DirectorSegment(
                segment_id=segment_id, beat_number=start + 1, prompt="move",
                duration_seconds=1, first_frame=f"{segment_id}.png",
            ),
            start_frame=start * 24, frame_count=24, status="submitted",
        )

    manifest = H3DirectorOutputManifest(
        entries=(entry("seg-ok", 0), entry("seg-failed", 1)),
        status="submitted",
    )

    finalized = _finalize_segment_manifest(
        manifest,
        generated={"seg-ok": ("provider-ok", "ok.mp4")},
        failed_ids={"seg-failed"},
        physical_video="combined.mp4",
        provider_parameters={},
        actual_output={},
    )

    assert finalized.status == "transport_failed"
    assert finalized.entries[0].status == "completed"
    assert finalized.entries[0].provider_task_id == "provider-ok"
    assert finalized.entries[1].status == "transport_failed"
    assert finalized.entries[1].provider_task_id is None
