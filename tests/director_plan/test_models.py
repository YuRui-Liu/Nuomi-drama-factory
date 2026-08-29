from datetime import timezone

import pytest
from pydantic import ValidationError
from ulid import ULID

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    SourceSpan,
)


def make_shot(**overrides: object) -> ShotPlan:
    values: dict[str, object] = {
        "id": "shot-1",
        "source_span_ids": ("span-1",),
        "subject": "hero",
        "action": "opens the door",
        "visible_start_state": "door closed",
        "visible_end_state": "door open",
        "duration_seconds": 3.0,
    }
    values.update(overrides)
    return ShotPlan(**values)  # type: ignore[arg-type]


def make_group(**overrides: object) -> NarrativeGroupPlan:
    values: dict[str, object] = {
        "id": "group-1",
        "ordinal": 1,
        "source_span_ids": ("span-1",),
        "scene_anchor": "hallway",
        "time_anchor": "night",
        "objective": "enter the room",
        "visible_turn": "the door opens",
        "relation_to_previous": "single",
        "shots": (make_shot(),),
    }
    values.update(overrides)
    return NarrativeGroupPlan(**values)  # type: ignore[arg-type]


def test_models_are_frozen_and_forbid_extra_fields() -> None:
    span = SourceSpan(id="span-1", ordinal=1, scene="hallway", time="night", text="He enters.")

    with pytest.raises(ValidationError, match="frozen"):
        span.text = "changed"  # type: ignore[misc]

    with pytest.raises(ValidationError, match="extra"):
        SourceSpan(
            id="span-1",
            ordinal=1,
            scene="hallway",
            time="night",
            text="He enters.",
            unexpected=True,
        )


@pytest.mark.parametrize("shot_count", [0, 6])
def test_group_requires_between_one_and_five_shots(shot_count: int) -> None:
    with pytest.raises(ValidationError):
        make_group(shots=tuple(make_shot(id=f"shot-{index}") for index in range(shot_count)))


@pytest.mark.parametrize("duration", [0, -1, 15.01])
def test_shot_duration_must_be_positive_and_at_most_fifteen(duration: float) -> None:
    with pytest.raises(ValidationError):
        make_shot(duration_seconds=duration)


def test_revision_new_creates_a_draft_with_ulid_and_utc_timestamp() -> None:
    revision = DirectorPlanRevision.new(
        episode=1,
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(make_group(),),
    )

    assert str(ULID.from_str(revision.revision_id)) == revision.revision_id
    assert revision.status == "draft"
    assert revision.parent_revision_id is None
    assert revision.created_at.tzinfo is timezone.utc
