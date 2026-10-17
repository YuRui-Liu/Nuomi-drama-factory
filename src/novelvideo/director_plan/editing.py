from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from pydantic import ValidationError
from ulid import ULID

from .models import (
    AddShotFromTemplate,
    AssetMigrationReport,
    DeleteShot,
    DirectorEdit,
    DirectorPlanRevision,
    MergeAdjacentGroups,
    MoveShot,
    NarrativeGroupPlan,
    ReorderGroups,
    ReorderShots,
    ShotPlan,
    SourceSpan,
    SplitGroup,
    UpdateShot,
    UpdateShots,
    ValidationIssue,
    ValidationReport,
)
from .validation import validate_director_plan


class DirectorEditError(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        details = "; ".join(issue.message for issue in report.issues)
        super().__init__(details or "director edit failed")


def apply_edit(
    revision: DirectorPlanRevision,
    command: DirectorEdit,
    source_spans: Sequence[SourceSpan],
) -> DirectorPlanRevision:
    """Apply one structural command and return a validated child revision."""
    try:
        groups = _dispatch_edit(revision.groups, command, source_spans)
        groups = _link_shot_handoffs(groups)
        _validate_shot_source_order(groups, source_spans)
    except DirectorEditError:
        raise
    except (KeyError, ValidationError, ValueError) as exc:
        raise _edit_error("invalid_edit", str(exc)) from exc

    candidate = revision.model_copy(update={"groups": groups})
    report = validate_director_plan(candidate, source_spans)
    if not report.passed:
        raise DirectorEditError(_humanize_validation_report(report))
    return candidate.model_copy(
        update={
            "revision_id": str(ULID()),
            "parent_revision_id": revision.revision_id,
            "status": "review_required",
            "edit_source": "human",
            "validation_report": report,
            "migration_report": AssetMigrationReport(),
            "created_at": datetime.now(timezone.utc),
            "activated_at": None,
        }
    )


def _dispatch_edit(
    groups: tuple[NarrativeGroupPlan, ...],
    command: DirectorEdit,
    source_spans: Sequence[SourceSpan],
) -> tuple[NarrativeGroupPlan, ...]:
    if isinstance(command, SplitGroup):
        return _split_group(groups, command, source_spans)
    if isinstance(command, MergeAdjacentGroups):
        return _merge_groups(groups, command)
    if isinstance(command, MoveShot):
        return _move_shot(groups, command, source_spans)
    if isinstance(command, ReorderGroups):
        return _reorder_groups(groups, command)
    if isinstance(command, AddShotFromTemplate):
        return _add_shot_from_template(groups, command)
    if isinstance(command, DeleteShot):
        return _delete_shot(groups, command)
    if isinstance(command, ReorderShots):
        return _reorder_shots(groups, command)
    if isinstance(command, UpdateShot):
        return _update_shot(groups, command)
    if isinstance(command, UpdateShots):
        ids = [update.shot_id for update in command.updates]
        if len(ids) != len(set(ids)):
            raise _edit_error("duplicate_shot_update", "Each shot can be updated only once per batch.")
        for update in command.updates:
            groups = _update_shot(groups, update)
        return groups
    raise _edit_error("unsupported_edit", "Unsupported director edit command.")


def _split_group(
    groups: tuple[NarrativeGroupPlan, ...],
    command: SplitGroup,
    source_spans: Sequence[SourceSpan],
) -> tuple[NarrativeGroupPlan, ...]:
    group_index = _group_index(groups, command.group_id)
    current = groups[group_index]
    shot_index = _shot_index(current.shots, command.before_shot_id)
    if shot_index == 0:
        raise _edit_error("invalid_split", "Split must leave a shot on both sides.")
    left_shots = current.shots[:shot_index]
    right_shots = current.shots[shot_index:]
    left = current.model_copy(
        update={
            "source_span_ids": _source_ids_for_shots(left_shots, source_spans),
            "shots": left_shots,
        }
    )
    right = current.model_copy(
        update={
            "id": f"ng-{ULID()}",
            "source_span_ids": _source_ids_for_shots(right_shots, source_spans),
            "shots": right_shots,
        }
    )
    return _renumber(groups[:group_index] + (left, right) + groups[group_index + 1 :])


def _merge_groups(
    groups: tuple[NarrativeGroupPlan, ...], command: MergeAdjacentGroups
) -> tuple[NarrativeGroupPlan, ...]:
    left_index = _group_index(groups, command.left_group_id)
    right_index = _group_index(groups, command.right_group_id)
    if right_index != left_index + 1:
        raise _edit_error("groups_not_adjacent", "Only adjacent groups can be merged.")
    left, right = groups[left_index], groups[right_index]
    if left.creative_contract_version != right.creative_contract_version:
        raise _edit_error("creative_contract_version_mismatch", "Merged groups must use the same creative contract.")
    if left.creative_contract_version is None and len(left.shots) + len(right.shots) > 4:
        raise _edit_error("too_many_shots", "Merged group cannot contain more than 4 shots.")
    merged = left.model_copy(
        update={
            "source_span_ids": left.source_span_ids + right.source_span_ids,
            "shots": left.shots + right.shots,
        }
    )
    return _renumber(groups[:left_index] + (merged,) + groups[right_index + 1 :])


def _move_shot(
    groups: tuple[NarrativeGroupPlan, ...],
    command: MoveShot,
    source_spans: Sequence[SourceSpan],
) -> tuple[NarrativeGroupPlan, ...]:
    source_group_index, source_shot_index = _find_shot(groups, command.shot_id)
    target_group_index = _group_index(groups, command.target_group_id)
    source_group = groups[source_group_index]
    target_group = groups[target_group_index]
    moving = source_group.shots[source_shot_index]

    if source_group_index != target_group_index and len(source_group.shots) == 1:
        raise _edit_error("empty_group", "Moving the shot would leave an empty group.")
    target_length = len(target_group.shots) - int(source_group_index == target_group_index)
    if command.index > target_length:
        raise _edit_error("invalid_index", "Target shot index is out of range.")
    if source_group.creative_contract_version != target_group.creative_contract_version:
        raise _edit_error("creative_contract_version_mismatch", "Moving shots requires matching creative contracts.")
    if source_group_index != target_group_index and target_group.creative_contract_version is None and len(target_group.shots) >= 4:
        raise _edit_error("too_many_shots", "Target group cannot contain more than 4 shots.")

    mutable = list(groups)
    if source_group_index == target_group_index:
        shots = list(source_group.shots)
        shots.pop(source_shot_index)
        shots.insert(command.index, moving)
        mutable[source_group_index] = _with_shots(source_group, tuple(shots), source_spans)
    else:
        source_shots = list(source_group.shots)
        source_shots.pop(source_shot_index)
        target_shots = list(target_group.shots)
        target_shots.insert(command.index, moving)
        mutable[source_group_index] = _with_shots(source_group, tuple(source_shots), source_spans)
        mutable[target_group_index] = _with_shots(target_group, tuple(target_shots), source_spans)
    return _renumber(tuple(mutable))


def _reorder_groups(
    groups: tuple[NarrativeGroupPlan, ...], command: ReorderGroups
) -> tuple[NarrativeGroupPlan, ...]:
    expected = [group.id for group in groups]
    if len(command.group_ids) != len(groups) or set(command.group_ids) != set(expected):
        raise _edit_error(
            "invalid_group_order", "Reorder must include every group exactly once."
        )
    by_id = {group.id: group for group in groups}
    return _renumber(tuple(by_id[group_id] for group_id in command.group_ids))


def _add_shot_from_template(
    groups: tuple[NarrativeGroupPlan, ...], command: AddShotFromTemplate
) -> tuple[NarrativeGroupPlan, ...]:
    group_index = _group_index(groups, command.group_id)
    group = groups[group_index]
    if group.creative_contract_version is None and len(group.shots) >= 4:
        raise _edit_error("too_many_shots", "Legacy groups cannot contain more than 4 shots.")
    if len(group.shots) >= 100:
        raise _edit_error("too_many_shots", "A group cannot contain more than 100 shots.")
    template_index = _shot_index(group.shots, command.template_shot_id)
    template = group.shots[template_index]
    added = template.model_copy(update={"id": f"shot-{ULID()}"})
    shots = list(group.shots)
    shots.insert(template_index + 1, added)
    updated = group.model_copy(update={"shots": tuple(shots)})
    return groups[:group_index] + (updated,) + groups[group_index + 1 :]


def _delete_shot(
    groups: tuple[NarrativeGroupPlan, ...], command: DeleteShot
) -> tuple[NarrativeGroupPlan, ...]:
    group_index, shot_index = _find_shot(groups, command.shot_id)
    group = groups[group_index]
    if len(group.shots) <= 1:
        raise _edit_error("empty_group", "A narrative group must keep at least one shot.")
    removed = group.shots[shot_index]
    remaining = group.shots[:shot_index] + group.shots[shot_index + 1 :]
    covered_spans = {span_id for shot in remaining for span_id in shot.source_span_ids}
    missing_spans = set(removed.source_span_ids) - covered_spans
    if missing_spans:
        raise _edit_error(
            "shot_source_would_be_uncovered",
            "Reassign this shot's only source coverage before deleting it: "
            + ", ".join(sorted(missing_spans)),
        )
    covered_dialogue = {
        source_id for shot in remaining for source_id in shot.dialogue_source_ids
    }
    missing_dialogue = set(removed.dialogue_source_ids) - covered_dialogue
    if missing_dialogue:
        raise _edit_error(
            "shot_dialogue_would_be_uncovered",
            "Reassign this shot's only dialogue references before deleting it: "
            + ", ".join(sorted(missing_dialogue)),
        )
    updated = group.model_copy(update={"shots": remaining})
    return groups[:group_index] + (updated,) + groups[group_index + 1 :]


def _reorder_shots(
    groups: tuple[NarrativeGroupPlan, ...], command: ReorderShots
) -> tuple[NarrativeGroupPlan, ...]:
    group_index = _group_index(groups, command.group_id)
    group = groups[group_index]
    expected = [shot.id for shot in group.shots]
    if len(command.shot_ids) != len(expected) or set(command.shot_ids) != set(expected):
        raise _edit_error(
            "invalid_shot_order", "Reorder must include every group shot exactly once."
        )
    by_id = {shot.id: shot for shot in group.shots}
    updated = group.model_copy(
        update={"shots": tuple(by_id[shot_id] for shot_id in command.shot_ids)}
    )
    return groups[:group_index] + (updated,) + groups[group_index + 1 :]


def _link_shot_handoffs(
    groups: tuple[NarrativeGroupPlan, ...],
) -> tuple[NarrativeGroupPlan, ...]:
    """Keep creative outgoing references aligned after a structural edit."""
    flat = [shot for group in groups for shot in group.shots]
    next_by_id = {
        shot.id: flat[index + 1].id if index + 1 < len(flat) else None
        for index, shot in enumerate(flat)
    }
    linked_groups = []
    for group in groups:
        linked_shots = []
        for shot in group.shots:
            if shot.creative is None:
                linked_shots.append(shot)
                continue
            handoff = shot.creative.handoff.model_copy(
                update={"next_shot_id": next_by_id[shot.id]}
            )
            creative = shot.creative.model_copy(update={"handoff": handoff})
            linked_shots.append(shot.model_copy(update={"creative": creative}))
        linked_groups.append(group.model_copy(update={"shots": tuple(linked_shots)}))
    return tuple(linked_groups)


def _update_shot(
    groups: tuple[NarrativeGroupPlan, ...], command: UpdateShot
) -> tuple[NarrativeGroupPlan, ...]:
    group_index, shot_index = _find_shot(groups, command.shot_id)
    current_group = groups[group_index]
    updates = command.model_dump(
        exclude={"kind", "shot_id"}, exclude_none=True
    )
    changed = type(current_group.shots[shot_index]).model_validate({
        **current_group.shots[shot_index].model_dump(mode="python"), **updates,
    })
    if command.asset_requirements is not None:
        for requirement in changed.asset_requirements:
            if not set(requirement.evidence_source_ids).issubset(changed.source_span_ids):
                raise _edit_error("invalid_asset_evidence", "Asset evidence must belong to the edited shot source spans.")
        if changed.cinematography is not None:
            declared = {item.entity_key for item in changed.asset_requirements
                        if item.kind in {"character_identity", "character_state"}}
            visible = {item.subject_id for item in changed.cinematography.subjects}
            if declared != visible:
                raise _edit_error("blocking_subject_mismatch", "Character requirements must match structured visible subjects.")
    changed_shots = list(current_group.shots)
    changed_shots[shot_index] = changed
    changed_group = current_group.model_copy(update={"shots": tuple(changed_shots)})
    return groups[:group_index] + (changed_group,) + groups[group_index + 1 :]


def _with_shots(
    group: NarrativeGroupPlan,
    shots: tuple[ShotPlan, ...],
    source_spans: Sequence[SourceSpan],
) -> NarrativeGroupPlan:
    return group.model_copy(
        update={"shots": shots, "source_span_ids": _source_ids_for_shots(shots, source_spans)}
    )


def _source_ids_for_shots(
    shots: tuple[ShotPlan, ...], source_spans: Sequence[SourceSpan]
) -> tuple[str, ...]:
    used = {span_id for shot in shots for span_id in shot.source_span_ids}
    return tuple(span.id for span in sorted(source_spans, key=lambda item: item.ordinal) if span.id in used)


def _validate_shot_source_order(
    groups: tuple[NarrativeGroupPlan, ...], source_spans: Sequence[SourceSpan]
) -> None:
    ordinal_by_id = {span.id: span.ordinal for span in source_spans}
    ordinals = [
        ordinal_by_id[span_id]
        for group in groups
        for shot in group.shots
        for span_id in shot.source_span_ids
        if span_id in ordinal_by_id
    ]
    if any(current < previous for previous, current in zip(ordinals, ordinals[1:])):
        raise _edit_error("source_order_mismatch", "Edit violates source order.")


def _group_index(groups: tuple[NarrativeGroupPlan, ...], group_id: str) -> int:
    for index, group in enumerate(groups):
        if group.id == group_id:
            return index
    raise _edit_error("group_not_found", f"Group {group_id!r} was not found.")


def _shot_index(shots: tuple[ShotPlan, ...], shot_id: str) -> int:
    for index, shot in enumerate(shots):
        if shot.id == shot_id:
            return index
    raise _edit_error("shot_not_found", f"Shot {shot_id!r} was not found.")


def _find_shot(
    groups: tuple[NarrativeGroupPlan, ...], shot_id: str
) -> tuple[int, int]:
    for group_index, group in enumerate(groups):
        for shot_index, shot in enumerate(group.shots):
            if shot.id == shot_id:
                return group_index, shot_index
    raise _edit_error("shot_not_found", f"Shot {shot_id!r} was not found.")


def _renumber(groups: tuple[NarrativeGroupPlan, ...]) -> tuple[NarrativeGroupPlan, ...]:
    return tuple(
        group.model_copy(update={"ordinal": index})
        for index, group in enumerate(groups, start=1)
    )


def _humanize_validation_report(report: ValidationReport) -> ValidationReport:
    replacements: dict[str, Callable[[str], str]] = {
        "hard_boundary_crossed": lambda _message: "Edit crosses a hard boundary.",
        "source_order_mismatch": lambda _message: "Edit violates source order.",
    }
    return report.model_copy(
        update={
            "issues": tuple(
                issue.model_copy(update={"message": replacements[issue.code](issue.message)})
                if issue.code in replacements
                else issue
                for issue in report.issues
            )
        }
    )


def _edit_error(code: str, message: str) -> DirectorEditError:
    return DirectorEditError(
        ValidationReport(
            passed=False,
            issues=(ValidationIssue(code=code, message=message, location="command"),),
        )
    )
