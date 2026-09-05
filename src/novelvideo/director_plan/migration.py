"""Deterministic, explainable matching of legacy shot assets to a new plan."""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Literal

from pydantic import Field

from .models import DirectorPlanRevision, FrozenModel, ShotPlan


class LegacyShotAsset(FrozenModel):
    asset_id: str
    asset_path: str
    asset_kind: Literal["image", "video"] = "image"
    old_shot_id: str
    source_span_ids: tuple[str, ...] = ()
    subject: str = ""
    scene: str = ""
    action: str = ""
    shot_size: str = ""
    camera_angle: str = ""
    style_hash: str


class MigrationEvidence(FrozenModel):
    source_overlap: float = Field(ge=0, le=1)
    subject_overlap: float = Field(ge=0, le=1)
    scene_match: float = Field(ge=0, le=1)
    action_similarity: float = Field(ge=0, le=1)
    shot_semantic_similarity: float = Field(ge=0, le=1)


class MigrationItem(FrozenModel):
    item_id: str
    old_asset_id: str
    old_asset_path: str
    old_asset_kind: Literal["image", "video"]
    old_shot_id: str
    new_shot_id: str
    suggested_shot_id: str
    adopted_shot_id: str | None = None
    score: float = Field(ge=0, le=1)
    confidence: Literal["high", "medium", "low"]
    reuse_mode: Literal["reuse", "reference_only"]
    suggested_decision: Literal["accepted", "review", "unmatched"]
    decision: Literal[
        "legacy_unbound", "accepted", "review", "unmatched", "rejected", "reference_only"
    ]
    manual_decision: Literal["accepted", "rejected", "reference_only"] | None = None
    conflict: bool = False
    evidence: MigrationEvidence


class MigrationReport(FrozenModel):
    items: tuple[MigrationItem, ...] = ()


def _set_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_set = {value.strip() for value in left if value.strip()}
    right_set = {value.strip() for value in right if value.strip()}
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 0.0


def _text_similarity(left: str, right: str) -> float:
    left = left.strip().casefold()
    right = right.strip().casefold()
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _semantic_similarity(asset: LegacyShotAsset, shot: ShotPlan) -> float:
    return (
        float(asset.shot_size.strip().casefold() == shot.shot_size.strip().casefold())
        + float(
            asset.camera_angle.strip().casefold()
            == shot.camera_angle.strip().casefold()
        )
    ) / 2


def match_one(
    asset: LegacyShotAsset,
    shot: ShotPlan,
    *,
    scene: str,
    style_hash: str,
) -> MigrationItem:
    evidence = MigrationEvidence(
        source_overlap=_set_overlap(asset.source_span_ids, shot.source_span_ids),
        subject_overlap=_text_similarity(asset.subject, shot.subject),
        scene_match=float(asset.scene.strip().casefold() == scene.strip().casefold()),
        action_similarity=_text_similarity(asset.action, shot.action),
        shot_semantic_similarity=_semantic_similarity(asset, shot),
    )
    score = round(
        0.35 * evidence.source_overlap
        + 0.20 * evidence.subject_overlap
        + 0.15 * evidence.scene_match
        + 0.15 * evidence.action_similarity
        + 0.15 * evidence.shot_semantic_similarity,
        6,
    )
    confidence: Literal["high", "medium", "low"] = (
        "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"
    )
    asset_style = asset.style_hash.strip()
    target_style = style_hash.strip()
    same_style = bool(asset_style and target_style and asset_style == target_style)
    suggested: Literal["accepted", "review", "unmatched"] = (
        "unmatched"
        if confidence == "low"
        else "accepted"
        if confidence == "high" and same_style
        else "review"
    )
    return MigrationItem(
        item_id="mig-"
        + sha256(f"{asset.asset_id}\0{shot.id}".encode("utf-8")).hexdigest()[:24],
        old_asset_id=asset.asset_id,
        old_asset_path=asset.asset_path,
        old_asset_kind=asset.asset_kind,
        old_shot_id=asset.old_shot_id,
        new_shot_id=shot.id,
        suggested_shot_id=shot.id,
        score=score,
        confidence=confidence,
        reuse_mode="reuse" if same_style else "reference_only",
        suggested_decision=suggested,
        decision="legacy_unbound",
        evidence=evidence,
    )


def match_assets(
    *,
    old_plan: DirectorPlanRevision,
    new_plan: DirectorPlanRevision,
    assets: tuple[LegacyShotAsset, ...],
) -> MigrationReport:
    old_shot_ids = {
        shot.id for group in old_plan.groups for shot in group.shots
    }
    candidates = tuple(asset for asset in assets if asset.old_shot_id in old_shot_ids)
    items: list[MigrationItem] = []
    for group in new_plan.groups:
        style_hash = group.style_snapshot_id or new_plan.project_style_snapshot_id
        for shot in group.shots:
            ranked = sorted(
                (
                    match_one(asset, shot, scene=group.scene_anchor, style_hash=style_hash)
                    for asset in candidates
                ),
                key=lambda item: (-item.score, item.old_asset_id),
            )
            if ranked:
                items.append(ranked[0])

    accepted_counts = Counter(item.old_asset_id for item in items)
    return MigrationReport(
        items=tuple(
            item.model_copy(update={"conflict": True})
            if accepted_counts[item.old_asset_id] > 1
            else item
            for item in items
        )
    )


def update_decision(
    report: MigrationReport,
    item_id: str,
    decision: Literal["accepted", "rejected", "reference_only"],
) -> MigrationReport:
    """Apply one explicit human decision without mutating the stored report."""
    matched = False
    items: list[MigrationItem] = []
    for item in report.items:
        if item.item_id != item_id:
            items.append(item)
            continue
        matched = True
        if decision == "accepted" and item.reuse_mode == "reference_only":
            raise ValueError("different-style assets cannot be formally accepted")
        update: dict[str, object] = {
            "decision": decision,
            "manual_decision": decision,
            "adopted_shot_id": item.suggested_shot_id if decision != "rejected" else None,
        }
        if decision == "reference_only":
            update["reuse_mode"] = "reference_only"
        items.append(item.model_copy(update=update))
    if not matched:
        raise KeyError(item_id)
    return MigrationReport(items=tuple(items))
