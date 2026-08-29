from datetime import datetime, timezone

import pytest

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    SourceSpan,
)
from novelvideo.director_plan.validation import validate_director_plan


def make_span(
    span_id: str,
    ordinal: int,
    *,
    scene: str = "hallway",
    time: str = "night",
    dialogue_text: str = "",
) -> SourceSpan:
    return SourceSpan(
        id=span_id,
        ordinal=ordinal,
        scene=scene,
        time=time,
        text=f"text for {span_id}",
        dialogue_text=dialogue_text,
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


def make_revision(groups: tuple[NarrativeGroupPlan, ...]) -> DirectorPlanRevision:
    return DirectorPlanRevision(
        revision_id="revision-1",
        episode=1,
        status="draft",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=groups,
        created_at=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
    )


def issue_pairs(report: object) -> list[tuple[str, str]]:
    return [(issue.code, issue.location) for issue in report.issues]  # type: ignore[attr-defined]


def test_accepts_a_complete_valid_plan() -> None:
    spans = (
        make_span("span-1", 1, dialogue_text="Open it."),
        make_span("span-2", 2),
    )
    shot = make_shot(
        source_span_ids=("span-1", "span-2"),
        dialogue_source_ids=("span-1",),
    )
    revision = make_revision(
        (make_group(source_span_ids=("span-1", "span-2"), shots=(shot,)),)
    )

    report = validate_director_plan(revision, spans)

    assert report.passed is True
    assert report.issues == ()
    assert report.version == 1


def test_reports_missing_duplicate_and_out_of_order_source_spans() -> None:
    spans = tuple(make_span(f"span-{index}", index) for index in range(1, 5))
    group = make_group(source_span_ids=("span-2", "span-1", "span-2"))

    report = validate_director_plan(make_revision((group,)), spans)

    assert issue_pairs(report) == [
        ("missing_source_span", "groups.0.source_span_ids"),
        ("missing_source_span", "groups.0.source_span_ids"),
        ("duplicate_source_span", "groups.0.source_span_ids"),
        ("source_order_mismatch", "groups.0.source_span_ids"),
    ]


def test_duplicate_occurrence_that_moves_backward_also_reports_order_mismatch() -> None:
    spans = (make_span("span-1", 1), make_span("span-2", 2))
    group = make_group(source_span_ids=("span-1", "span-2", "span-1"))

    report = validate_director_plan(make_revision((group,)), spans)

    assert issue_pairs(report) == [
        ("duplicate_source_span", "groups.0.source_span_ids"),
        ("source_order_mismatch", "groups.0.source_span_ids"),
    ]


def test_reports_an_unknown_group_source_as_an_order_mismatch() -> None:
    group = make_group(source_span_ids=("span-1", "unknown"))

    report = validate_director_plan(
        make_revision((group,)), (make_span("span-1", 1),)
    )

    assert issue_pairs(report) == [
        ("source_order_mismatch", "groups.0.source_span_ids")
    ]


def test_reports_group_crossing_a_scene_or_time_boundary() -> None:
    spans = (
        make_span("span-1", 1),
        make_span("span-2", 2, scene="bedroom"),
        make_span("span-3", 3, time="morning"),
    )
    group = make_group(source_span_ids=("span-1", "span-2", "span-3"))

    report = validate_director_plan(make_revision((group,)), spans)

    assert ("hard_boundary_crossed", "groups.0.source_span_ids") in issue_pairs(report)


@pytest.mark.parametrize("field", ["objective", "visible_turn"])
def test_reports_blank_group_semantics(field: str) -> None:
    group = make_group(**{field: " \t "})

    report = validate_director_plan(
        make_revision((group,)), (make_span("span-1", 1),)
    )

    assert issue_pairs(report) == [("incomplete_group_semantics", f"groups.0.{field}")]


def test_reports_shot_sources_outside_its_group() -> None:
    spans = (make_span("span-1", 1), make_span("span-2", 2))
    shot = make_shot(source_span_ids=("span-2",))
    group = make_group(source_span_ids=("span-1",), shots=(shot,))

    report = validate_director_plan(make_revision((group,)), spans)

    assert ("invalid_shot_source", "groups.0.shots.0.source_span_ids") in issue_pairs(
        report
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"subject": "  "}, "subject"),
        ({"action": "\t"}, "action"),
        ({"visible_start_state": " "}, "visible_start_state"),
        ({"visible_end_state": "\n"}, "visible_end_state"),
        (
            {"visible_start_state": " door closed ", "visible_end_state": "door closed"},
            "visible_end_state",
        ),
    ],
)
def test_reports_incomplete_action_detail(
    overrides: dict[str, object], field: str
) -> None:
    group = make_group(shots=(make_shot(**overrides),))

    report = validate_director_plan(
        make_revision((group,)), (make_span("span-1", 1),)
    )

    assert issue_pairs(report) == [
        ("incomplete_action_detail", f"groups.0.shots.0.{field}")
    ]


@pytest.mark.parametrize(
    "dialogue_span",
    [
        make_span("unknown", 2, dialogue_text="Known words"),
        make_span("span-2", 2),
    ],
)
def test_reports_unknown_or_non_dialogue_sources(dialogue_span: SourceSpan) -> None:
    spans = (make_span("span-1", 1), dialogue_span)
    shot = make_shot(dialogue_source_ids=(dialogue_span.id,))

    if dialogue_span.id == "unknown":
        spans = spans[:1]

    report = validate_director_plan(make_revision((make_group(shots=(shot,)),)), spans)

    assert ("invalid_dialogue_source", "groups.0.shots.0.dialogue_source_ids") in (
        issue_pairs(report)
    )


def test_reports_duplicate_group_and_shot_ids() -> None:
    first = make_group(shots=(make_shot(id="shot-duplicate"),))
    second = make_group(
        id="group-1",
        ordinal=2,
        source_span_ids=("span-2",),
        shots=(make_shot(id="shot-duplicate", source_span_ids=("span-2",)),),
    )
    spans = (make_span("span-1", 1), make_span("span-2", 2))

    report = validate_director_plan(make_revision((first, second)), spans)

    assert ("duplicate_id", "groups.1.id") in issue_pairs(report)
    assert ("duplicate_id", "groups.1.shots.0.id") in issue_pairs(report)


def test_reports_non_contiguous_and_duplicate_group_ordinals() -> None:
    groups = (
        make_group(),
        make_group(
            id="group-2",
            ordinal=1,
            source_span_ids=("span-2",),
            shots=(make_shot(id="shot-2", source_span_ids=("span-2",)),),
        ),
    )
    spans = (make_span("span-1", 1), make_span("span-2", 2))

    report = validate_director_plan(make_revision(groups), spans)

    assert ("duplicate_id", "groups.1.ordinal") in issue_pairs(report)
    assert ("non_contiguous_ordinal", "groups.1.ordinal") in issue_pairs(report)


def test_collects_multiple_errors_deterministically_without_mutating_inputs() -> None:
    spans = (make_span("span-1", 1), make_span("span-2", 2))
    shot = make_shot(subject=" ", source_span_ids=("outside",))
    revision = make_revision(
        (make_group(source_span_ids=("span-2",), objective="", shots=(shot,)),)
    )
    revision_before = revision.model_dump(mode="json")
    spans_before = tuple(span.model_dump(mode="json") for span in spans)

    first = validate_director_plan(revision, spans)
    second = validate_director_plan(revision, spans)

    assert first == second
    assert first.passed is False
    assert all(issue.severity == "error" for issue in first.issues)
    assert revision.model_dump(mode="json") == revision_before
    assert tuple(span.model_dump(mode="json") for span in spans) == spans_before
    assert {issue.code for issue in first.issues} >= {
        "missing_source_span",
        "incomplete_group_semantics",
        "invalid_shot_source",
        "incomplete_action_detail",
    }
