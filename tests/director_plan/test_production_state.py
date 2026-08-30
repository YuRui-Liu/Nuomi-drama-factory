from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.generation import build_production_plan
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    StyleProjections,
    StyleSnapshot,
    ProductionPlan,
    canonical_production_plan_hash,
)
from novelvideo.director_plan.production_state import (
    GenerationBatchState,
    ProductionCleanupReport,
    ProductionError,
    ProductionArtifactResult,
    ProductionExecutionState,
    ProductionQualityReport,
    QualityIssue,
    VideoSegmentState,
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


def _revision(
    count: int,
    *,
    revision_id: str = "revision-1",
    group_id: str = "group-1",
    style_hash: str = "style-hash-1",
    reverse_shots: bool = False,
) -> DirectorPlanRevision:
    shots = tuple(_shot(index) for index in range(1, count + 1))
    if reverse_shots:
        shots = tuple(reversed(shots))
    snapshot = StyleSnapshot(
        snapshot_id="style-snapshot-1",
        style_id="style-1",
        style_version="v1",
        catalog_hash="catalog-hash-1",
        style_hash=style_hash,
        projections=StyleProjections(
            director="director",
            image="image",
            video="video",
            panel_tag="panel",
        ),
    )
    return DirectorPlanRevision(
        revision_id=revision_id,
        episode=1,
        status="active",
        source_script_hash="script-hash",
        director_model="director-model",
        prompt_version="v1",
        project_style_snapshot_id="style-snapshot-1",
        project_style_snapshot=snapshot,
        groups=(
            NarrativeGroupPlan(
                id=group_id,
                ordinal=1,
                source_span_ids=tuple(
                    f"span-{index}" for index in range(1, count + 1)
                ),
                scene_anchor="room",
                time_anchor="day",
                objective="cross the room",
                visible_turn="the door opens",
                relation_to_previous="single",
                shots=shots,
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


@pytest.mark.parametrize(
    "changed",
    [
        {"group_id": "group-2"},
        {"reverse_shots": True},
        {"style_hash": "style-hash-2"},
    ],
    ids=["group", "ordered-shots", "style-hash"],
)
def test_production_ids_change_with_identity_inputs(
    changed: dict[str, object],
) -> None:
    original = build_production_plan(_revision(2))
    updated = build_production_plan(_revision(2, **changed))  # type: ignore[arg-type]

    assert original.generation_batches[0].id != updated.generation_batches[0].id
    assert original.video_segments[0].id != updated.video_segments[0].id


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("model", "gpt-image-3"),
        ("aspect_ratio", "16:9"),
        ("resolution", "4K"),
        ("reference_image_limit", 4),
        ("retry_limit", 5),
    ],
)
def test_production_configuration_changes_plan_hash(
    setting: str, value: object
) -> None:
    original = build_production_plan(_revision(2))
    updated = build_production_plan(_revision(2), **{setting: value})

    assert original.production_plan_hash != updated.production_plan_hash
    assert getattr(updated, setting) == value
    assert getattr(updated.generation_batches[0], setting) == value


def test_production_plan_rejects_noncanonical_hash() -> None:
    plan = build_production_plan(_revision(2))
    payload = plan.model_dump(mode="json")
    payload["episode"] = 2

    with pytest.raises(ValidationError, match="canonical"):
        ProductionPlan.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "style_hash",
        "batch_settings",
        "shot_coverage",
        "duplicate_ids",
    ],
)
def test_production_plan_enforces_cross_field_consistency(mutation: str) -> None:
    plan = build_production_plan(_revision(2))
    payload = plan.model_dump(mode="json")
    if mutation == "style_hash":
        payload["generation_batches"][0]["style_snapshot_hash"] = "other"
    elif mutation == "batch_settings":
        payload["generation_batches"][0]["model"] = "other-model"
    elif mutation == "shot_coverage":
        payload["video_segments"] = payload["video_segments"][:-1]
    else:
        payload["video_segments"][0]["id"] = payload["generation_batches"][0]["id"]
    payload["production_plan_hash"] = canonical_production_plan_hash(payload)

    with pytest.raises(ValidationError):
        ProductionPlan.model_validate(payload)


def test_production_plan_hash_is_lowercase_sha256() -> None:
    plan = build_production_plan(_revision(2))

    assert len(plan.production_plan_hash) == 64
    assert plan.production_plan_hash == plan.production_plan_hash.lower()
    assert set(plan.production_plan_hash) <= set("0123456789abcdef")


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
        production_plan_hash="a" * 64,
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


def test_execution_error_quality_cleanup_and_stage_round_trip() -> None:
    state = ProductionExecutionState(
        revision_id="revision-1",
        production_plan_hash="a" * 64,
        generation_batches=(
            GenerationBatchState(
                production_id="batch-1",
                stage="failed",
                provider="grsai",
                request_id="request-1",
                job_id="job-1",
                result=ProductionArtifactResult(
                    uri="grid.png", sha256="a" * 64, mime_type="image/png"
                ),
                error=ProductionError(
                    code="provider_failed", message="failed", retryable=True
                ),
                quality_report=ProductionQualityReport(
                    passed=False,
                    issues=(QualityIssue(code="blur", message="too blurry"),),
                ),
                cleanup_report=ProductionCleanupReport(
                    source_uri="grid.png",
                    output_uris=("shot-1.png", "shot-2.png"),
                    passed=True,
                    message="split complete",
                ),
            ),
        ),
        video_segments=(
            VideoSegmentState(
                production_id="segment-1",
                stage="polling",
                provider="minimax",
                request_id="request-segment-1",
                job_id="job-segment-1",
            ),
        ),
    )

    restored = ProductionExecutionState.model_validate_json(state.model_dump_json())

    assert restored == state
    assert restored.generation_batches[0].stage == "failed"
    assert restored.video_segments[0].stage == "polling"


@pytest.mark.parametrize(
    "item",
    [
        {"production_id": "batch-1", "stage": "persisted"},
        {"production_id": "batch-1", "stage": "failed"},
        {
            "production_id": "batch-1",
            "stage": "pending",
            "result": {"uri": "grid.png"},
        },
        {
            "production_id": "batch-1",
            "stage": "polling",
            "provider": "provider",
            "request_id": "request",
        },
    ],
)
def test_execution_item_rejects_invalid_stage_payload(item: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GenerationBatchState.model_validate(item)


@pytest.mark.parametrize("sha256", ["", "a" * 63, "A" * 64, "g" * 64])
def test_artifact_sha256_requires_lowercase_64_hex(sha256: str) -> None:
    with pytest.raises(ValidationError):
        ProductionArtifactResult(uri="grid.png", sha256=sha256)


def test_execution_state_rejects_duplicate_or_cross_collection_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ProductionExecutionState(
            revision_id="revision-1",
            production_plan_hash="a" * 64,
            generation_batches=(GenerationBatchState(production_id="item-1"),),
            video_segments=(VideoSegmentState(production_id="item-1"),),
        )


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_production_state_rejects_blank_boundaries(value: str) -> None:
    with pytest.raises(ValidationError):
        ProductionExecutionState(
            revision_id=value,
            production_plan_hash="a" * 64,
        )
    with pytest.raises(ValidationError):
        ProductionArtifactResult(uri=value)


def test_cleanup_report_rejects_blank_uris_and_pre_result_stage() -> None:
    with pytest.raises(ValidationError):
        ProductionCleanupReport(source_uri=" ")
    with pytest.raises(ValidationError):
        ProductionCleanupReport(source_uri="grid.png", output_uris=(" ",))
    with pytest.raises(ValidationError):
        GenerationBatchState(
            production_id="batch-1",
            stage="pending",
            cleanup_report=ProductionCleanupReport(source_uri="grid.png"),
        )
