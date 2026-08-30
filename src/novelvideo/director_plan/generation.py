from __future__ import annotations

import hashlib
import json

from .models import (
    DirectorPlanRevision,
    GenerationBatchPlan,
    NarrativeGroupPlan,
    ProductionPlan,
    ShotPlan,
    VideoSegmentPlan,
)


_LAYOUTS = {
    1: ("single", 1, 1),
    2: ("diptych", 1, 2),
    3: ("triptych", 1, 3),
    4: ("grid_2x2", 2, 2),
}


def plan_generation_batches(
    group: NarrativeGroupPlan,
    *,
    revision_id: str | None = None,
) -> tuple[GenerationBatchPlan, ...]:
    """Plan image calls for one narrative group without blank cells."""
    style_snapshot_id = _require_style_snapshot(group)
    counts = (3, 2) if len(group.shots) == 5 else (len(group.shots),)
    batches: list[GenerationBatchPlan] = []
    offset = 0
    for batch_index, count in enumerate(counts, start=1):
        shots = group.shots[offset : offset + count]
        layout, rows, columns = _LAYOUTS[count]
        batches.append(
            GenerationBatchPlan(
                id=_production_id(
                    "batch",
                    revision_id,
                    group.id,
                    tuple(shot.id for shot in shots),
                    style_snapshot_id,
                    fallback_index=batch_index,
                ),
                group_id=group.id,
                shot_ids=tuple(shot.id for shot in shots),
                layout=layout,
                rows=rows,
                columns=columns,
                capacity=count,
                style_snapshot_id=style_snapshot_id,
            )
        )
        offset += count
    return tuple(batches)


def plan_video_segments(
    group: NarrativeGroupPlan,
    *,
    revision_id: str | None = None,
) -> tuple[VideoSegmentPlan, ...]:
    """Plan independent video calls from shots belonging to one group."""
    style_snapshot_id = _require_style_snapshot(group)
    shot_groups: list[list[ShotPlan]] = []
    current: list[ShotPlan] = []
    for shot in group.shots:
        if not current:
            current = [shot]
            continue
        if _can_merge(current, shot, group.scene_anchor):
            current.append(shot)
        else:
            shot_groups.append(current)
            current = [shot]
    if current:
        shot_groups.append(current)

    return tuple(
        VideoSegmentPlan(
            id=_production_id(
                "segment",
                revision_id,
                group.id,
                tuple(shot.id for shot in shots),
                style_snapshot_id,
                fallback_index=index,
            ),
            group_id=group.id,
            shot_ids=tuple(shot.id for shot in shots),
            duration_seconds=sum(shot.duration_seconds for shot in shots),
            continuity_reason=(
                "continuous_action" if len(shots) > 1 else "single_shot"
            ),
            audio_mode="project_default",
            style_snapshot_id=style_snapshot_id,
        )
        for index, shots in enumerate(shot_groups, start=1)
    )


def build_production_plan(revision: DirectorPlanRevision) -> ProductionPlan:
    """Derive the complete immutable production plan for one revision."""
    batches: list[GenerationBatchPlan] = []
    segments: list[VideoSegmentPlan] = []
    for raw_group in revision.groups:
        group = raw_group
        if not (group.style_snapshot_id or "").strip():
            group = group.model_copy(
                update={"style_snapshot_id": revision.project_style_snapshot_id}
            )
        batches.extend(
            plan_generation_batches(group, revision_id=revision.revision_id)
        )
        segments.extend(plan_video_segments(group, revision_id=revision.revision_id))

    payload = {
        "revision_id": revision.revision_id,
        "episode": revision.episode,
        "generation_batches": [item.model_dump(mode="json") for item in batches],
        "video_segments": [item.model_dump(mode="json") for item in segments],
    }
    production_plan_hash = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return ProductionPlan(
        **payload,
        production_plan_hash=production_plan_hash,
    )


def _production_id(
    kind: str,
    revision_id: str | None,
    group_id: str,
    shot_ids: tuple[str, ...],
    style_snapshot_id: str,
    *,
    fallback_index: int,
) -> str:
    if revision_id is None:
        return f"{kind}:{group_id}:{fallback_index}"
    identity = json.dumps(
        [revision_id, group_id, list(shot_ids), style_snapshot_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{revision_id}:{group_id}:{digest}"


def _can_merge(
    current: list[ShotPlan], next_shot: ShotPlan, group_space: str
) -> bool:
    previous = current[-1]
    previous_space = previous.space_anchor.strip() or group_space
    next_space = next_shot.space_anchor.strip() or group_space
    return (
        previous.continuous_with_next
        and previous.subject.strip() == next_shot.subject.strip()
        and previous_space == next_space
        and sum(shot.duration_seconds for shot in current)
        + next_shot.duration_seconds
        <= 15
    )


def _require_style_snapshot(group: NarrativeGroupPlan) -> str:
    style_snapshot_id = (group.style_snapshot_id or "").strip()
    if not style_snapshot_id:
        raise ValueError("narrative group requires style_snapshot_id")
    return style_snapshot_id
