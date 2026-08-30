from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.generation import build_production_plan
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
)
from novelvideo.director_plan.production_state import (
    GenerationBatchState,
    ProductionArtifactResult,
    ProductionExecutionState,
)


def _shot(index: int) -> ShotPlan:
    return ShotPlan(
        id=f"shot-{index}",
        source_span_ids=(f"span-{index}",),
        subject="hero",
        action=f"action {index}",
        visible_start_state=f"state {index}",
        visible_end_state=f"state {index + 1}",
        duration_seconds=3,
    )


def _revision(count: int, *, revision_id: str = "revision-1") -> DirectorPlanRevision:
    return DirectorPlanRevision(
        revision_id=revision_id,
        episode=1,
        status="active",
        source_script_hash="script-hash",
        director_model="director-model",
        prompt_version="v1",
        project_style_snapshot_id="style-snapshot-1",
        groups=(
            NarrativeGroupPlan(
                id="group-1",
                ordinal=1,
                source_span_ids=tuple(
                    f"span-{index}" for index in range(1, count + 1)
                ),
                scene_anchor="room",
                time_anchor="day",
                objective="cross the room",
                visible_turn="the door opens",
                relation_to_previous="single",
                shots=tuple(_shot(index) for index in range(1, count + 1)),
                style_snapshot_id="style-snapshot-1",
            ),
        ),
        created_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("count", "layout"),
    [(1, "single"), (2, "diptych"), (3, "triptych"), (4, "grid_2x2")],
)
def test_one_to_four_shots_use_exact_batch_layout(
    count: int, layout: str
) -> None:
    plan = build_production_plan(_revision(count))

    assert plan.generation_batches[0].layout == layout
    assert plan.generation_batches[0].shot_ids == tuple(
        f"shot-{index}" for index in range(1, count + 1)
    )


def test_production_plan_ids_and_hash_are_deterministic() -> None:
    first = build_production_plan(_revision(2))
    second = build_production_plan(_revision(2))

    assert first == second
    assert first.production_plan_hash == second.production_plan_hash
    assert first.generation_batches[0].id == second.generation_batches[0].id
    assert first.video_segments[0].id == second.video_segments[0].id


def test_production_plan_ids_change_with_revision() -> None:
    first = build_production_plan(_revision(2, revision_id="revision-1"))
    second = build_production_plan(_revision(2, revision_id="revision-2"))

    assert first.generation_batches[0].id != second.generation_batches[0].id
    assert first.video_segments[0].id != second.video_segments[0].id
    assert first.production_plan_hash != second.production_plan_hash


def test_execution_state_rejects_free_form_result_dicts() -> None:
    with pytest.raises(ValidationError):
        GenerationBatchState(
            production_id="batch-1",
            stage="persisted",
            result={"uri": "grid.png", "arbitrary": "not-typed"},
        )


def test_execution_state_is_frozen_and_uses_typed_results() -> None:
    state = ProductionExecutionState(
        revision_id="revision-1",
        production_plan_hash="plan-hash",
        generation_batches=(
            GenerationBatchState(
                production_id="batch-1",
                stage="persisted",
                provider="grsai",
                request_id="request-1",
                job_id="job-1",
                heartbeat_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
                result=ProductionArtifactResult(uri="grid.png"),
            ),
        ),
    )

    with pytest.raises(ValidationError, match="frozen"):
        state.production_plan_hash = "changed"  # type: ignore[misc]
