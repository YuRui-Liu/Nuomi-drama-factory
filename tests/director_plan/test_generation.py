import pytest
from pydantic import ValidationError

from novelvideo.director_plan.generation import (
    plan_generation_batches,
    plan_video_segments,
)
from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan


REVISION_ID = "revision-1"
STYLE_HASH = "style-hash-1"


def generation_batches(group: NarrativeGroupPlan):
    return plan_generation_batches(
        group, revision_id=REVISION_ID, style_snapshot_hash=STYLE_HASH
    )


def video_segments(group: NarrativeGroupPlan):
    return plan_video_segments(
        group, revision_id=REVISION_ID, style_snapshot_hash=STYLE_HASH
    )


def shot(index: int, **updates: object) -> ShotPlan:
    values: dict[str, object] = {
        "id": f"shot-{index}",
        "source_span_ids": (f"span-{index}",),
        "subject": "hero",
        "action": f"action {index}",
        "visible_start_state": f"state {index}",
        "visible_end_state": f"state {index + 1}",
        "space_anchor": "room",
        "duration_seconds": 3,
    }
    values.update(updates)
    return ShotPlan(**values)  # type: ignore[arg-type]


def group_with_shots(count: int, **shot_updates: object) -> NarrativeGroupPlan:
    return NarrativeGroupPlan(
        id="ng-01",
        ordinal=1,
        source_span_ids=tuple(f"span-{index}" for index in range(1, count + 1)),
        scene_anchor="room",
        time_anchor="day",
        objective="cross the room",
        visible_turn="the door opens",
        relation_to_previous="single",
        shots=tuple(shot(index, **shot_updates) for index in range(1, count + 1)),
        style_snapshot_id="style-snapshot-1",
    )


@pytest.mark.parametrize(
    ("count", "layouts"),
    [
        (1, ("single",)),
        (2, ("diptych",)),
        (3, ("triptych",)),
        (4, ("grid_2x2",)),
    ],
)
def test_generation_batches_never_have_blank_cells(
    count: int, layouts: tuple[str, ...]
) -> None:
    batches = generation_batches(group_with_shots(count))

    assert tuple(batch.layout for batch in batches) == layouts
    assert sum(len(batch.shot_ids) for batch in batches) == count
    assert all(batch.capacity == len(batch.shot_ids) for batch in batches)
    assert all(batch.group_id == "ng-01" for batch in batches)
    assert all(batch.style_snapshot_id == "style-snapshot-1" for batch in batches)


def test_video_segments_default_to_one_shot_and_merge_only_continuous_action() -> None:
    group = group_with_shots(4).model_copy(
        update={
            "shots": (
                shot(1),
                shot(2, continuous_with_next=True),
                shot(3),
                shot(4),
            )
        }
    )

    segments = video_segments(group)

    assert [segment.shot_ids for segment in segments] == [
        ("shot-1",),
        ("shot-2", "shot-3"),
        ("shot-4",),
    ]
    assert [segment.continuity_reason for segment in segments] == [
        "single_shot",
        "continuous_action",
        "single_shot",
    ]


@pytest.mark.parametrize(
    "second_updates",
    [
        {"subject": "villain"},
        {"space_anchor": "street"},
        {"duration_seconds": 8},
    ],
    ids=["different-subject", "different-space", "over-fifteen-seconds"],
)
def test_segment_merge_requires_same_subject_space_and_max_duration(
    second_updates: dict[str, object],
) -> None:
    first_duration = 8 if "duration_seconds" in second_updates else 3
    group = group_with_shots(2).model_copy(
        update={
            "shots": (
                shot(1, continuous_with_next=True, duration_seconds=first_duration),
                shot(2, **second_updates),
            )
        }
    )

    assert [segment.shot_ids for segment in video_segments(group)] == [
        ("shot-1",),
        ("shot-2",),
    ]


def test_generation_models_are_frozen() -> None:
    batch = generation_batches(group_with_shots(1))[0]
    segment = video_segments(group_with_shots(1))[0]

    with pytest.raises(ValidationError, match="frozen"):
        batch.layout = "diptych"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        segment.duration_seconds = 9  # type: ignore[misc]


def test_public_generation_api_requires_revision_and_style_hash() -> None:
    group = group_with_shots(1)

    with pytest.raises(TypeError):
        plan_generation_batches(group)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        plan_video_segments(group)  # type: ignore[call-arg]
