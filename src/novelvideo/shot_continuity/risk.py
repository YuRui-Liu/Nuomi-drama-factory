"""Deterministic H3 risk signals, scoring, and capability audit."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

from novelvideo.director_plan.models import ShotPlan

from .models import (
    RiskDimensionScore,
    ShotContinuityContract,
    ShotRiskReport,
)

_CLAUSE_SEPARATOR = re.compile(r"[,，;；]|\bthen\b|随后|然后|同时", re.IGNORECASE)
_DIRECTION_WORDS = ("left", "right", "forward", "backward", "左", "右", "前", "后")


@dataclass(frozen=True, slots=True)
class ShotRiskSignals:
    subject_count: int = 1
    over_shoulder: bool = False
    exact_axis: bool = False
    topology_change: bool = False
    strong_occlusion: bool = False
    action_beats: int = 1
    direction_changes: int = 0
    complex_camera: bool = False
    has_predecessor: bool = False
    exact_boundary: bool = False
    critical_prop_handoff: bool = False

    def __post_init__(self) -> None:
        for field in fields(self):
            if field.name not in {"subject_count", "action_beats", "direction_changes"}:
                continue
            value = getattr(self, field.name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field.name} must be a nonnegative integer")


def _contains(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def signals_for_shot(
    shot: ShotPlan, contract: ShotContinuityContract
) -> ShotRiskSignals:
    """Extract every language-dependent heuristic into structured signals."""
    text = " ".join(
        (
            shot.action,
            shot.space_anchor,
            shot.camera_angle,
            shot.composition,
            shot.camera_motion,
        )
    ).casefold()
    action_clauses = tuple(
        clause.strip() for clause in _CLAUSE_SEPARATOR.split(shot.action) if clause.strip()
    )
    action_text = shot.action.casefold()
    direction_hits = tuple(
        word
        for word in _DIRECTION_WORDS
        if (
            word in action_text
            if not word.isascii()
            else re.search(rf"\b{re.escape(word)}\b", action_text) is not None
        )
    )

    return ShotRiskSignals(
        subject_count=max(1, len(contract.subjects)),
        over_shoulder=_contains(
            text,
            ("over shoulder", "over-the-shoulder", "过肩", "反打"),
        ),
        exact_axis=bool(contract.scene.axis.strip() or contract.camera.axis.strip()),
        topology_change=_contains(
            text,
            ("new location", "change location", "换场", "穿越空间"),
        ),
        strong_occlusion=_contains(
            text,
            ("occluded", "occlusion", "遮挡", "遮住"),
        ),
        action_beats=max(1, len(action_clauses)),
        direction_changes=max(0, len(direction_hits) - 1),
        complex_camera=_contains(
            text,
            ("orbit", "crane", "drone", "handheld", "环绕", "升降", "航拍", "手持"),
        ),
        has_predecessor=contract.predecessor_shot_id is not None,
        exact_boundary=shot.continuous_with_next,
        critical_prop_handoff=any(prop.critical for prop in contract.props),
    )


def spatial_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    high_reasons = tuple(
        reason
        for active, reason in (
            (signals.over_shoulder, "over_shoulder"),
            (signals.exact_axis, "exact_axis"),
            (signals.topology_change, "topology_change"),
        )
        if active
    )
    if high_reasons:
        return RiskDimensionScore(dimension="spatial", level=2, reasons=high_reasons)
    if signals.subject_count >= 2:
        return RiskDimensionScore(
            dimension="spatial", level=1, reasons=("multiple_subjects",)
        )
    return RiskDimensionScore(dimension="spatial", level=0)


def identity_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    high_reasons = tuple(
        reason
        for active, reason in (
            (signals.subject_count >= 3, "three_or_more_subjects"),
            (signals.strong_occlusion, "strong_occlusion"),
        )
        if active
    )
    if high_reasons:
        return RiskDimensionScore(dimension="identity", level=2, reasons=high_reasons)
    if signals.subject_count == 2:
        return RiskDimensionScore(
            dimension="identity", level=1, reasons=("two_subjects",)
        )
    return RiskDimensionScore(dimension="identity", level=0)


def motion_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    high_reasons = tuple(
        reason
        for active, reason in (
            (signals.action_beats > 3, "too_many_action_beats"),
            (signals.direction_changes > 1, "repeated_direction_change"),
            (
                signals.complex_camera and signals.action_beats > 2,
                "complex_camera_competes_with_action",
            ),
        )
        if active
    )
    if high_reasons:
        return RiskDimensionScore(dimension="motion", level=2, reasons=high_reasons)
    if signals.action_beats >= 2 or signals.direction_changes == 1:
        return RiskDimensionScore(
            dimension="motion", level=1, reasons=("multi_beat_motion",)
        )
    return RiskDimensionScore(dimension="motion", level=0)


def continuity_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    high_reasons = tuple(
        reason
        for active, reason in (
            (signals.exact_boundary, "exact_boundary"),
            (signals.critical_prop_handoff, "critical_prop_handoff"),
        )
        if active
    )
    if high_reasons:
        return RiskDimensionScore(dimension="continuity", level=2, reasons=high_reasons)
    if signals.has_predecessor:
        return RiskDimensionScore(
            dimension="continuity", level=1, reasons=("has_predecessor",)
        )
    return RiskDimensionScore(dimension="continuity", level=0)


def audit_h3_shot(
    signals: ShotRiskSignals,
    ref_available: bool,
    director_world_available: bool,
) -> ShotRiskReport:
    """Score all dimensions independently and report missing H3 capabilities."""
    spatial = spatial_score(signals)
    identity = identity_score(signals)
    motion = motion_score(signals)
    continuity = continuity_score(signals)
    blockers: list[str] = []
    if motion.level == 2:
        blockers.append("shot_rewrite_required")
    if spatial.level == 2 and not director_world_available:
        blockers.append("director_world_required")
    if identity.level == 2 and not ref_available:
        blockers.append("reference_capability_required")

    return ShotRiskReport(
        spatial=spatial,
        identity=identity,
        motion=motion,
        continuity=continuity,
        blockers=tuple(dict.fromkeys(blockers)),
    )
