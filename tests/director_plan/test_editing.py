from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.editing import DirectorEditError, apply_edit
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    MergeAdjacentGroups,
    MoveShot,
    NarrativeGroupPlan,
    ReorderGroups,
    ShotPlan,
    SourceSpan,
    SplitGroup,
    UpdateShot,
)


def span(span_id: str, ordinal: int, *, scene: str = "room") -> SourceSpan:
    return SourceSpan(
        id=span_id,
        ordinal=ordinal,
        scene=scene,
        time="day",
        text=f"source {span_id}",
        dialogue_text="Hello" if span_id == "s2" else "",
    )


def shot(shot_id: str, *source_ids: str, **updates: object) -> ShotPlan:
    values: dict[str, object] = {
        "id": shot_id,
        "source_span_ids": source_ids,
        "subject": "hero",
        "action": f"acts in {shot_id}",
        "visible_start_state": f"{shot_id} starts",
        "visible_end_state": f"{shot_id} ends",
        "duration_seconds": 3,
    }
    values.update(updates)
    return ShotPlan(**values)  # type: ignore[arg-type]


def group(
    group_id: str,
    ordinal: int,
    source_ids: tuple[str, ...],
    shots: tuple[ShotPlan, ...],
    *,
    scene: str = "room",
) -> NarrativeGroupPlan:
    return NarrativeGroupPlan(
        id=group_id,
        ordinal=ordinal,
        source_span_ids=source_ids,
        scene_anchor=scene,
        time_anchor="day",
        objective=f"objective {group_id}",
        visible_turn=f"turn {group_id}",
        relation_to_previous="single" if ordinal == 1 else "progressive",
        shots=shots,
    )


def source_spans() -> tuple[SourceSpan, ...]:
    return tuple(span(f"s{index}", index) for index in range(1, 7))


def active_plan() -> DirectorPlanRevision:
    return DirectorPlanRevision(
        revision_id="rev-active",
        episode=1,
        status="active",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(
            group("ng-01", 1, ("s1", "s2"), (shot("shot-1", "s1"), shot("shot-2", "s2"))),
            group("ng-02", 2, ("s3", "s4", "s5", "s6"), (
                shot("shot-3", "s3", "s4"),
                shot("shot-5", "s5", "s6"),
            )),
        ),
        created_at=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
    )


def test_split_group_creates_child_revision_and_preserves_source_order() -> None:
    original = active_plan()

    edited = apply_edit(
        original,
        SplitGroup(group_id="ng-02", before_shot_id="shot-5"),
        source_spans(),
    )

    assert edited.revision_id != original.revision_id
    assert edited.parent_revision_id == original.revision_id
    assert edited.status == "review_required"
    assert edited.edit_source == "human"
    assert [item.source_span_ids for item in edited.groups] == [
        ("s1", "s2"),
        ("s3", "s4"),
        ("s5", "s6"),
    ]
    assert [item.ordinal for item in edited.groups] == [1, 2, 3]
    assert edited.validation_report.passed is True
    assert original == active_plan()


def test_merge_requires_adjacent_groups_and_creates_child_revision() -> None:
    split = apply_edit(
        active_plan(),
        SplitGroup(group_id="ng-02", before_shot_id="shot-5"),
        source_spans(),
    )

    merged = apply_edit(
        split,
        MergeAdjacentGroups(left_group_id="ng-02", right_group_id=split.groups[2].id),
        source_spans(),
    )

    assert merged.parent_revision_id == split.revision_id
    assert [item.source_span_ids for item in merged.groups] == [
        ("s1", "s2"),
        ("s3", "s4", "s5", "s6"),
    ]
    with pytest.raises(DirectorEditError, match="adjacent"):
        apply_edit(
            split,
            MergeAdjacentGroups(left_group_id="ng-01", right_group_id=split.groups[2].id),
            source_spans(),
        )


def test_move_shot_rejects_crossing_hard_scene_boundary() -> None:
    spans = source_spans() + (span("s7", 7, scene="street"),)
    plan = active_plan().model_copy(
        update={
            "groups": active_plan().groups
            + (group("ng-03", 3, ("s7",), (shot("shot-7", "s7"),), scene="street"),)
        }
    )

    with pytest.raises(DirectorEditError, match="hard boundary"):
        apply_edit(
            plan,
            MoveShot(shot_id="shot-5", target_group_id="ng-03", index=0),
            spans,
        )


def test_move_shot_rejects_source_order_reversal() -> None:
    with pytest.raises(DirectorEditError, match="source order"):
        apply_edit(
            active_plan(),
            MoveShot(shot_id="shot-1", target_group_id="ng-02", index=1),
            source_spans(),
        )


def test_move_shot_within_group_rejects_shot_source_order_reversal() -> None:
    with pytest.raises(DirectorEditError, match="source order"):
        apply_edit(
            active_plan(),
            MoveShot(shot_id="shot-1", target_group_id="ng-01", index=1),
            source_spans(),
        )


def test_reorder_groups_is_validated_against_source_order() -> None:
    with pytest.raises(DirectorEditError, match="source order"):
        apply_edit(
            active_plan(),
            ReorderGroups(group_ids=("ng-02", "ng-01")),
            source_spans(),
        )


def test_update_shot_changes_only_structured_fields_and_keeps_dialogue_source() -> None:
    edited = apply_edit(
        active_plan(),
        UpdateShot(
            shot_id="shot-2",
            action="answers while opening the door",
            dialogue_source_ids=("s2",),
        ),
        source_spans(),
    )

    changed = edited.groups[0].shots[1]
    assert changed.action == "answers while opening the door"
    assert changed.dialogue_source_ids == ("s2",)
    assert changed.subject == "hero"

    with pytest.raises(ValidationError, match="dialogue_text"):
        UpdateShot(shot_id="shot-2", dialogue_text="rewritten dialogue")  # type: ignore[call-arg]


def test_invalid_update_surfaces_validation_report_without_creating_revision() -> None:
    with pytest.raises(DirectorEditError) as exc_info:
        apply_edit(
            active_plan(),
            UpdateShot(shot_id="shot-2", dialogue_source_ids=("s1",)),
            source_spans(),
        )

    assert exc_info.value.report.passed is False
    assert any(issue.code == "invalid_dialogue_source" for issue in exc_info.value.report.issues)
