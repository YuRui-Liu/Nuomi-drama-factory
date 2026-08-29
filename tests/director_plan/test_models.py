import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from ulid import ULID

from novelvideo.director_plan.models import (
    AssetMigrationReport,
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


def make_revision(**overrides: object) -> DirectorPlanRevision:
    values: dict[str, object] = {
        "revision_id": "01K00000000000000000000000",
        "episode": 1,
        "status": "draft",
        "source_script_hash": "sha256:abc",
        "director_model": "director-v1",
        "prompt_version": "v2",
        "project_style_snapshot_id": "style-1",
        "groups": (make_group(),),
        "created_at": datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return DirectorPlanRevision(**values)  # type: ignore[arg-type]


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
    with pytest.raises(ValidationError) as exc_info:
        make_group(shots=tuple(make_shot(id=f"shot-{index}") for index in range(shot_count)))

    assert exc_info.value.errors()[0]["loc"] == ("shots",)


@pytest.mark.parametrize("shot_count", [1, 5])
def test_group_accepts_shot_count_boundaries(shot_count: int) -> None:
    group = make_group(
        shots=tuple(make_shot(id=f"shot-{index}") for index in range(shot_count))
    )

    assert len(group.shots) == shot_count


@pytest.mark.parametrize("duration", [0, -1, 15.01])
def test_shot_duration_must_be_positive_and_at_most_fifteen(duration: float) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_shot(duration_seconds=duration)

    assert exc_info.value.errors()[0]["loc"] == ("duration_seconds",)


def test_shot_accepts_fifteen_second_duration() -> None:
    assert make_shot(duration_seconds=15).duration_seconds == 15


def test_asset_migration_items_are_recursively_immutable() -> None:
    report = AssetMigrationReport(
        items=({"asset_id": "asset-1", "metadata": {"tags": ["one", "two"]}},)
    )

    with pytest.raises(TypeError):
        report.items[0]["asset_id"] = "changed"
    with pytest.raises(TypeError):
        report.items[0]["metadata"]["new"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        report.items[0]["metadata"]["tags"][0] = "changed"  # type: ignore[index]


def test_asset_migration_items_dump_as_plain_json_objects() -> None:
    report = AssetMigrationReport(items=({"metadata": {"tags": ["one", "two"]}},))
    expected = {"items": [{"metadata": {"tags": ["one", "two"]}}]}

    assert report.model_dump() == expected
    assert json.loads(report.model_dump_json()) == expected


@pytest.mark.parametrize("field", ["created_at", "activated_at"])
def test_revision_rejects_naive_datetimes(field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_revision(**{field: datetime(2026, 8, 29, 12)})

    assert exc_info.value.errors()[0]["loc"] == (field,)


def test_revision_normalizes_aware_datetimes_to_utc() -> None:
    china_time = timezone(timedelta(hours=8))
    revision = make_revision(
        created_at=datetime(2026, 8, 29, 20, tzinfo=china_time),
        activated_at=datetime(2026, 8, 29, 21, tzinfo=china_time),
    )

    assert revision.created_at == datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    assert revision.created_at.tzinfo is timezone.utc
    assert revision.activated_at == datetime(2026, 8, 29, 13, tzinfo=timezone.utc)
    assert revision.activated_at.tzinfo is timezone.utc


def test_revision_json_contains_id_and_utc_timestamp() -> None:
    payload = json.loads(make_revision().model_dump_json())

    assert payload["revision_id"] == "01K00000000000000000000000"
    assert payload["created_at"].endswith(("Z", "+00:00"))


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
