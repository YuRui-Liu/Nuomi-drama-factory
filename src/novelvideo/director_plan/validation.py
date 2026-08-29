from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .models import (
    DirectorPlanRevision,
    SourceSpan,
    ValidationIssue,
    ValidationReport,
)


def validate_director_plan(
    revision: DirectorPlanRevision,
    source_spans: Sequence[SourceSpan],
) -> ValidationReport:
    """Validate a director plan without mutating the plan or its source spans."""
    issues: list[ValidationIssue] = []
    spans_by_id = {span.id: span for span in source_spans}
    ordered_spans = sorted(source_spans, key=lambda span: span.ordinal)
    expected_ids = [span.id for span in ordered_spans]
    grouped_ids = [span_id for group in revision.groups for span_id in group.source_span_ids]
    grouped_counts = Counter(grouped_ids)
    coverage_location = "groups.0.source_span_ids"

    for span_id in expected_ids:
        if grouped_counts[span_id] == 0:
            _add_issue(
                issues,
                "missing_source_span",
                f"Source span {span_id!r} is not assigned to a group.",
                coverage_location,
            )

    seen_source_ids: set[str] = set()
    for group_index, group in enumerate(revision.groups):
        for span_id in group.source_span_ids:
            if span_id in seen_source_ids:
                _add_issue(
                    issues,
                    "duplicate_source_span",
                    f"Source span {span_id!r} is assigned more than once.",
                    f"groups.{group_index}.source_span_ids",
                )
            seen_source_ids.add(span_id)

    first_known_ids = list(dict.fromkeys(span_id for span_id in grouped_ids if span_id in spans_by_id))
    expected_known_ids = [span_id for span_id in expected_ids if span_id in first_known_ids]
    has_unknown_group_source = any(span_id not in spans_by_id for span_id in grouped_ids)
    if first_known_ids != expected_known_ids or has_unknown_group_source:
        mismatch_group_index = _first_order_mismatch_group(revision, spans_by_id)
        _add_issue(
            issues,
            "source_order_mismatch",
            "Grouped source spans do not follow source ordinal order.",
            f"groups.{mismatch_group_index}.source_span_ids",
        )

    seen_group_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    seen_shot_ids: set[str] = set()
    for group_index, group in enumerate(revision.groups):
        group_location = f"groups.{group_index}"
        if group.id in seen_group_ids:
            _add_issue(
                issues,
                "duplicate_id",
                f"Group id {group.id!r} is not unique.",
                f"{group_location}.id",
            )
        seen_group_ids.add(group.id)

        if group.ordinal in seen_ordinals:
            _add_issue(
                issues,
                "duplicate_id",
                f"Group ordinal {group.ordinal} is not unique.",
                f"{group_location}.ordinal",
            )
        seen_ordinals.add(group.ordinal)
        if group.ordinal != group_index + 1:
            _add_issue(
                issues,
                "non_contiguous_ordinal",
                "Group ordinals must be consecutive and follow group order.",
                f"{group_location}.ordinal",
            )

        group_spans = [
            spans_by_id[span_id]
            for span_id in group.source_span_ids
            if span_id in spans_by_id
        ]
        if len({(span.scene, span.time) for span in group_spans}) > 1:
            _add_issue(
                issues,
                "hard_boundary_crossed",
                "A narrative group cannot cross a scene or time boundary.",
                f"{group_location}.source_span_ids",
            )

        for field_name in ("objective", "visible_turn"):
            if not getattr(group, field_name).strip():
                _add_issue(
                    issues,
                    "incomplete_group_semantics",
                    f"Group {field_name} must not be blank.",
                    f"{group_location}.{field_name}",
                )

        group_source_ids = set(group.source_span_ids)
        for shot_index, shot in enumerate(group.shots):
            shot_location = f"{group_location}.shots.{shot_index}"
            if shot.id in seen_shot_ids:
                _add_issue(
                    issues,
                    "duplicate_id",
                    f"Shot id {shot.id!r} is not unique.",
                    f"{shot_location}.id",
                )
            seen_shot_ids.add(shot.id)

            if any(span_id not in group_source_ids for span_id in shot.source_span_ids):
                _add_issue(
                    issues,
                    "invalid_shot_source",
                    "Shot source spans must belong to their narrative group.",
                    f"{shot_location}.source_span_ids",
                )

            for field_name in (
                "subject",
                "action",
                "visible_start_state",
                "visible_end_state",
            ):
                if not getattr(shot, field_name).strip():
                    _add_issue(
                        issues,
                        "incomplete_action_detail",
                        f"Shot {field_name} must not be blank.",
                        f"{shot_location}.{field_name}",
                    )
            if (
                shot.visible_start_state.strip()
                and shot.visible_start_state.strip() == shot.visible_end_state.strip()
            ):
                _add_issue(
                    issues,
                    "incomplete_action_detail",
                    "Shot visible start and end states must differ.",
                    f"{shot_location}.visible_end_state",
                )

            for span_id in shot.dialogue_source_ids:
                span = spans_by_id.get(span_id)
                if span is None or not span.dialogue_text.strip():
                    _add_issue(
                        issues,
                        "invalid_dialogue_source",
                        f"Dialogue source {span_id!r} is unknown or has no dialogue.",
                        f"{shot_location}.dialogue_source_ids",
                    )

    return ValidationReport(passed=not issues, issues=tuple(issues), version=1)


def _first_order_mismatch_group(
    revision: DirectorPlanRevision, spans_by_id: dict[str, SourceSpan]
) -> int:
    previous_ordinal: int | None = None
    for group_index, group in enumerate(revision.groups):
        for span_id in group.source_span_ids:
            span = spans_by_id.get(span_id)
            if span is None:
                continue
            if previous_ordinal is not None and span.ordinal < previous_ordinal:
                return group_index
            previous_ordinal = span.ordinal
    return 0


def _add_issue(
    issues: list[ValidationIssue], code: str, message: str, location: str
) -> None:
    issues.append(
        ValidationIssue(
            code=code,
            message=message,
            location=location,
            severity="error",
        )
    )
