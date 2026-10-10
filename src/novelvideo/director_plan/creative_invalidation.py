"""Dependency-specific invalidation without deleting or replacing saved media."""
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from novelvideo.narrative_groups.models import GroupStageState, StageName

from .keyframes import project_keyframe
from .models import DirectorPlanRevision, NarrativeGroupPlan


def _static_dependencies(revision: DirectorPlanRevision, group: NarrativeGroupPlan) -> dict[str, Any]:
    return {
        "frames": [project_keyframe({
            **shot.model_dump(mode="json"), "creative_contract_version": 1,
            "director_revision_id": revision.revision_id,
            "scene_name": group.scene_anchor, "time_of_day": group.time_anchor,
        }).dependency_hash for shot in group.shots],
        "assets": [[item.model_dump(mode="json") for item in shot.asset_requirements]
                   for shot in group.shots],
        "group_style_id": group.style_snapshot_id,
        "project_style_id": revision.project_style_snapshot_id,
        "style": revision.project_style_snapshot.model_dump(mode="json") if revision.project_style_snapshot else None,
        "geography": group.creative_context.model_dump(mode="json", include={
            "scene_id", "geography", "landmarks", "action_axis", "available_camera_regions",
            "light_source_ids", "initial_relationships",
        }) if group.creative_context else None,
    }


def invalidate_creative_stages(
    old_revision: DirectorPlanRevision | None,
    new_revision: DirectorPlanRevision,
    group_id: str,
    previous_stages: Mapping[StageName, GroupStageState],
) -> dict[StageName, GroupStageState]:
    """Preserve exact history/assets and mark only affected shared stage outputs.

    Missing baselines and legacy projections are conservatively stale. An
    unchanged group in a newly adopted revision retains its stages. Existing
    staleness is never cleared by comparison, and no generation is launched.
    """
    old = next((g for g in old_revision.groups if g.id == group_id), None) if old_revision else None
    new = next((g for g in new_revision.groups if g.id == group_id), None)
    static_changed = video_changed = True
    reason = "director_projection_baseline_changed"
    if (old is not None and new is not None and old_revision is not None
            and old.creative_contract_version == new.creative_contract_version == 1
            and tuple(s.id for s in old.shots) == tuple(s.id for s in new.shots)):
        try:
            static_changed = _static_dependencies(old_revision, old) != _static_dependencies(new_revision, new)
        except (ValueError, TypeError, KeyError):
            # Unprojectable historical data must not certify current media.
            static_changed = True
        video_changed = static_changed or old.model_dump(mode="json") != new.model_dump(mode="json")
        video_changed = video_changed or old_revision.source_script_hash != new_revision.source_script_hash
        reason = "director_static_dependencies_changed" if static_changed else "director_video_dependencies_changed"
    result = dict(previous_stages)
    for name in ("sketch", "render", "video"):
        changed = static_changed if name != "video" else video_changed
        if changed and name in result:
            current = result[name]
            reasons = list(filter(None, current.stale_reason.split(";")))
            if reason not in reasons:
                reasons.append(reason)
            result[name] = replace(current, needs_regeneration=True, stale_reason=";".join(reasons))
    return result
