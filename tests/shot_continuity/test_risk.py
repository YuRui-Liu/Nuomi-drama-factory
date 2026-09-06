from __future__ import annotations

import pytest

from novelvideo.director_plan.models import ShotPlan
from novelvideo.shot_continuity.models import (
    BoundaryState,
    CameraLock,
    PropLock,
    SceneLock,
    ShotContinuityContract,
    SubjectLock,
)
from novelvideo.shot_continuity.risk import (
    ShotRiskSignals,
    audit_h3_shot,
    continuity_score,
    identity_score,
    motion_score,
    signals_for_shot,
    spatial_score,
)


def _shot(**overrides: object) -> ShotPlan:
    values: dict[str, object] = {
        "id": "shot-1",
        "source_span_ids": ("span-1",),
        "subject": "Lin",
        "action": "waits",
        "space_anchor": "hall",
        "visible_start_state": "standing",
        "visible_end_state": "standing",
        "camera_angle": "eye level",
        "composition": "centered",
        "camera_motion": "static",
        "duration_seconds": 3,
    }
    values.update(overrides)
    return ShotPlan(**values)  # type: ignore[arg-type]


def _contract(**overrides: object) -> ShotContinuityContract:
    values: dict[str, object] = {
        "revision": 1,
        "shot_id": "shot-1",
        "scene_id": "scene-1",
        "scene": SceneLock(),
        "subjects": (SubjectLock(subject_id="Lin"),),
        "camera": CameraLock(shot_size="medium", angle="eye level"),
        "boundary": BoundaryState(carry_in="standing", planned_carry_out="standing"),
    }
    values.update(overrides)
    return ShotContinuityContract(**values)  # type: ignore[arg-type]


def test_shot_risk_signals_defaults_and_validation() -> None:
    signals = ShotRiskSignals()

    assert signals.subject_count == 1
    assert signals.action_beats == 1
    assert signals.direction_changes == 0
    with pytest.raises(ValueError):
        ShotRiskSignals(subject_count=-1)
    with pytest.raises(ValueError):
        ShotRiskSignals(action_beats=-1)


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("over_shoulder", "Over-the-shoulder framing", True),
        ("over_shoulder", "双人反打", True),
        ("strong_occlusion", "face occluded by smoke", True),
        ("strong_occlusion", "人物被门遮挡", True),
        ("topology_change", "change location through a gate", True),
        ("topology_change", "随后换场", True),
        ("complex_camera", "slow crane move", True),
        ("complex_camera", "手持跟随", True),
    ],
)
def test_signals_for_shot_recognizes_english_and_chinese_keywords(
    field: str, text: str, expected: bool
) -> None:
    signals = signals_for_shot(_shot(action=text), _contract())

    assert getattr(signals, field) is expected


def test_signals_for_shot_derives_counts_axes_boundaries_and_props() -> None:
    contract = _contract(
        predecessor_shot_id="shot-0",
        predecessor_revision=1,
        scene=SceneLock(axis="180 degree line"),
        subjects=tuple(SubjectLock(subject_id=f"subject-{index}") for index in range(3)),
        props=(PropLock(prop_id="letter", critical=True),),
    )

    signals = signals_for_shot(
        _shot(
            action="moves left, then right；随后前进，同时后退",
            continuous_with_next=True,
        ),
        contract,
    )

    assert signals.subject_count == 3
    assert signals.action_beats == 4
    assert signals.direction_changes == 3
    assert signals.exact_axis is True
    assert signals.has_predecessor is True
    assert signals.exact_boundary is True
    assert signals.critical_prop_handoff is True


def test_direction_changes_count_unique_action_tokens() -> None:
    signals = signals_for_shot(
        _shot(action="left right left"),
        _contract(),
    )

    assert signals.direction_changes == 1


@pytest.mark.parametrize(
    "action",
    [
        "leftovers brighten",
        "The leftovers brighten straightforwardly.",
    ],
)
def test_direction_changes_ignore_english_direction_substrings(action: str) -> None:
    signals = signals_for_shot(_shot(action=action), _contract())

    assert signals.direction_changes == 0
    assert motion_score(signals).level == 0


def test_direction_changes_ignore_non_action_shot_text() -> None:
    signals = signals_for_shot(
        _shot(action="waits", camera_angle="left", composition="subject on right"),
        _contract(),
    )

    assert signals.direction_changes == 0


def test_text_heuristics_do_not_clear_unrelated_dimensions() -> None:
    signals = signals_for_shot(
        _shot(action="过肩拍摄，然后人物被遮住，然后向左移动，然后向右移动", camera_motion="环绕"),
        _contract(
            scene=SceneLock(axis="locked"),
            subjects=(
                SubjectLock(subject_id="one"),
                SubjectLock(subject_id="two"),
                SubjectLock(subject_id="three"),
            ),
            props=(PropLock(prop_id="key", critical=True),),
        ),
    )

    assert spatial_score(signals).level == 2
    assert identity_score(signals).level == 2
    assert motion_score(signals).level == 2
    assert continuity_score(signals).level == 2


@pytest.mark.parametrize(
    ("signals", "level", "reasons"),
    [
        (ShotRiskSignals(), 0, ()),
        (ShotRiskSignals(subject_count=2), 1, ("multiple_subjects",)),
        (
            ShotRiskSignals(over_shoulder=True, exact_axis=True, topology_change=True),
            2,
            ("over_shoulder", "exact_axis", "topology_change"),
        ),
    ],
)
def test_spatial_score_matrix(
    signals: ShotRiskSignals, level: int, reasons: tuple[str, ...]
) -> None:
    score = spatial_score(signals)
    assert (score.level, score.reasons) == (level, reasons)


@pytest.mark.parametrize(
    ("signals", "level", "reasons"),
    [
        (ShotRiskSignals(), 0, ()),
        (ShotRiskSignals(subject_count=2), 1, ("two_subjects",)),
        (ShotRiskSignals(subject_count=3), 2, ("three_or_more_subjects",)),
        (ShotRiskSignals(strong_occlusion=True), 2, ("strong_occlusion",)),
    ],
)
def test_identity_score_matrix(
    signals: ShotRiskSignals, level: int, reasons: tuple[str, ...]
) -> None:
    score = identity_score(signals)
    assert (score.level, score.reasons) == (level, reasons)


@pytest.mark.parametrize(
    ("signals", "level", "reasons"),
    [
        (ShotRiskSignals(), 0, ()),
        (ShotRiskSignals(action_beats=2), 1, ("multi_beat_motion",)),
        (ShotRiskSignals(direction_changes=1), 1, ("multi_beat_motion",)),
        (ShotRiskSignals(action_beats=4), 2, ("too_many_action_beats",)),
        (
            ShotRiskSignals(direction_changes=2),
            2,
            ("repeated_direction_change",),
        ),
        (
            ShotRiskSignals(complex_camera=True, action_beats=3),
            2,
            ("complex_camera_competes_with_action",),
        ),
    ],
)
def test_motion_score_matrix(
    signals: ShotRiskSignals, level: int, reasons: tuple[str, ...]
) -> None:
    score = motion_score(signals)
    assert (score.level, score.reasons) == (level, reasons)


@pytest.mark.parametrize(
    ("signals", "level", "reasons"),
    [
        (ShotRiskSignals(), 0, ()),
        (ShotRiskSignals(has_predecessor=True), 1, ("has_predecessor",)),
        (ShotRiskSignals(exact_boundary=True), 2, ("exact_boundary",)),
        (
            ShotRiskSignals(exact_boundary=True, critical_prop_handoff=True),
            2,
            ("exact_boundary", "critical_prop_handoff"),
        ),
    ],
)
def test_continuity_score_matrix(
    signals: ShotRiskSignals, level: int, reasons: tuple[str, ...]
) -> None:
    score = continuity_score(signals)
    assert (score.level, score.reasons) == (level, reasons)


def test_audit_reports_independent_dimensions_and_stable_unique_blockers() -> None:
    report = audit_h3_shot(
        ShotRiskSignals(
            subject_count=3,
            over_shoulder=True,
            action_beats=4,
            exact_boundary=True,
        ),
        ref_available=False,
        director_world_available=False,
    )

    assert [
        report.motion.level,
        report.spatial.level,
        report.identity.level,
        report.continuity.level,
    ] == [2, 2, 2, 2]
    assert report.blockers == (
        "shot_rewrite_required",
        "director_world_required",
        "reference_capability_required",
    )


def test_audit_capabilities_only_satisfy_their_own_dimensions() -> None:
    report = audit_h3_shot(
        ShotRiskSignals(subject_count=3, over_shoulder=True),
        ref_available=True,
        director_world_available=False,
    )

    assert report.blockers == ("director_world_required",)


@pytest.mark.parametrize(
    ("signals", "levels", "blockers"),
    [
        (ShotRiskSignals(subject_count=1), (0, 0, 0, 0), ()),
        (
            ShotRiskSignals(over_shoulder=True, exact_axis=True),
            (2, 0, 0, 0),
            ("director_world_required",),
        ),
        (
            ShotRiskSignals(subject_count=3, strong_occlusion=True),
            (1, 2, 0, 0),
            ("reference_capability_required",),
        ),
        (
            ShotRiskSignals(action_beats=4, complex_camera=True),
            (0, 0, 2, 0),
            ("shot_rewrite_required",),
        ),
        (
            ShotRiskSignals(exact_boundary=True, critical_prop_handoff=True),
            (0, 0, 0, 2),
            (),
        ),
    ],
)
def test_audit_plan_matrix_keeps_dimensions_independent(
    signals: ShotRiskSignals,
    levels: tuple[int, int, int, int],
    blockers: tuple[str, ...],
) -> None:
    report = audit_h3_shot(
        signals,
        ref_available=False,
        director_world_available=False,
    )

    assert (
        report.spatial.level,
        report.identity.level,
        report.motion.level,
        report.continuity.level,
    ) == levels
    assert report.blockers == blockers
