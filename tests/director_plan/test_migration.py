from __future__ import annotations

from datetime import datetime, timezone

import pytest

from novelvideo.director_plan.migration import (
    LegacyShotAsset,
    match_assets,
    match_one,
    update_decision,
)
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    SourceSpan,
)
from novelvideo.director_plan.planner import DirectorPlanDraft, DirectorPlanInput
from novelvideo.director_plan.service import DirectorPlanService


def _shot(
    shot_id: str,
    *,
    source_ids: tuple[str, ...] = ("s1",),
    subject: str = "阿远",
    action: str = "快步走到门边并停下",
) -> ShotPlan:
    return ShotPlan(
        id=shot_id,
        source_span_ids=source_ids,
        subject=subject,
        action=action,
        visible_start_state="站在走廊",
        visible_end_state="停在门边",
        shot_size="medium",
        camera_angle="eye_level",
        duration_seconds=5,
    )


def _plan(
    revision_id: str,
    shots: tuple[ShotPlan, ...],
    *,
    style_id: str = "style-a",
) -> DirectorPlanRevision:
    source_ids = tuple(dict.fromkeys(i for shot in shots for i in shot.source_span_ids))
    return DirectorPlanRevision(
        revision_id=revision_id,
        episode=1,
        status="review_required",
        source_script_hash="hash",
        director_model="model",
        prompt_version="v1",
        project_style_snapshot_id=style_id,
        groups=(
            NarrativeGroupPlan(
                id="ng-1",
                ordinal=1,
                source_span_ids=source_ids,
                scene_anchor="走廊",
                time_anchor="夜",
                objective="接近房门",
                visible_turn="抵达房门",
                relation_to_previous="single",
                shots=shots,
                style_snapshot_id=style_id,
            ),
        ),
        created_at=datetime.now(timezone.utc),
    )


def _asset(asset_id: str = "asset-1", *, style_hash: str = "style-a") -> LegacyShotAsset:
    return LegacyShotAsset(
        asset_id=asset_id,
        asset_path=f"assets/{asset_id}.png",
        old_shot_id="old-shot",
        source_span_ids=("s1",),
        subject="阿远",
        scene="走廊",
        action="快步走到门边并停下",
        shot_size="medium",
        camera_angle="eye_level",
        style_hash=style_hash,
    )


def test_matcher_auto_applies_only_high_confidence_same_style_assets() -> None:
    report = match_assets(
        old_plan=_plan("old", (_shot("old-shot"),)),
        new_plan=_plan("new", (_shot("new-shot"),)),
        assets=(_asset(),),
    )

    item = report.items[0]
    assert item.confidence == "high"
    assert item.decision == "accepted"
    assert item.reuse_mode == "formal"
    assert item.evidence.model_dump() == {
        "source_overlap": 1.0,
        "subject_overlap": 1.0,
        "scene_match": 1.0,
        "action_similarity": 1.0,
        "shot_semantic_similarity": 1.0,
    }
    assert item.score == 1.0


def test_different_style_can_only_be_reference() -> None:
    item = match_one(
        _asset(style_hash="style-a"),
        _shot("new-shot"),
        scene="走廊",
        style_hash="style-b",
    )

    assert item.reuse_mode == "reference_only"
    assert item.decision == "review"


def test_item_id_is_stable_and_manual_decision_is_immutable() -> None:
    item = match_one(
        _asset(), _shot("new-shot"), scene="走廊", style_hash="style-a"
    )
    repeated = match_one(
        _asset(), _shot("new-shot"), scene="走廊", style_hash="style-a"
    )

    assert item.item_id == repeated.item_id
    assert item.item_id.startswith("mig-")
    report = match_assets(
        old_plan=_plan("old", (_shot("old-shot"),)),
        new_plan=_plan("new", (_shot("new-shot"),)),
        assets=(_asset(),),
    )
    updated = update_decision(report, item.item_id, "rejected")
    assert updated.items[0].decision == "rejected"
    assert updated.items[0].manual_decision == "rejected"
    assert item.decision == "accepted"


def test_medium_and_low_confidence_matches_require_review() -> None:
    medium = match_one(
        _asset().model_copy(update={"action": "", "shot_size": "", "camera_angle": ""}),
        _shot("medium"),
        scene="走廊",
        style_hash="style-a",
    )
    low = match_one(
        _asset(),
        _shot("low", source_ids=("s9",), subject="陌生人", action="转身离开"),
        scene="天台",
        style_hash="style-a",
    )

    assert medium.confidence in {"medium", "low"}
    assert medium.decision == "review"
    assert low.confidence == "low"
    assert low.decision == "unmatched"


def test_conflicting_high_candidates_are_all_downgraded_to_review() -> None:
    new_plan = _plan("new", (_shot("new-1"), _shot("new-2")))
    report = match_assets(
        old_plan=_plan("old", (_shot("old-shot"),)),
        new_plan=new_plan,
        assets=(_asset(),),
    )

    assert [item.old_asset_id for item in report.items] == ["asset-1", "asset-1"]
    assert all(item.confidence == "high" for item in report.items)
    assert all(item.decision == "review" for item in report.items)
    assert all(item.conflict for item in report.items)


@pytest.mark.asyncio
async def test_service_persists_explainable_migration_on_review_revision() -> None:
    new_group = _plan("new", (_shot("new-shot"),)).groups[0]

    class Store:
        def __init__(self) -> None:
            self.saved = []

        def save(self, revision) -> None:
            self.saved.append(revision)

    class Planner:
        async def plan_episode(self, _input):
            return DirectorPlanDraft(groups=(new_group,))

    store = Store()
    result = await DirectorPlanService(store, Planner()).create_draft(
        DirectorPlanInput(
            episode=1,
            source_script_hash="hash",
            source_spans=(
                SourceSpan(
                    id="s1",
                    ordinal=1,
                    scene="走廊",
                    time="夜",
                    text="阿远走到门边",
                ),
            ),
            relevant_bible={},
            aspect_ratio="9:16",
            style_director={},
            project_style_snapshot_id="style-a",
        ),
        old_plan=_plan("old", (_shot("old-shot"),)),
        assets=(_asset(),),
    )

    assert result.migration_report.items[0]["decision"] == "accepted"
    assert result.migration_report.items[0]["evidence"]["source_overlap"] == 1.0
    assert store.saved[-1] is result
