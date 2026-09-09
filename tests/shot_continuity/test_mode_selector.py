from __future__ import annotations

from collections.abc import Collection

import pytest

from novelvideo.shot_continuity.mode_selector import select_h3_mode
from novelvideo.shot_continuity.models import H3ModeInputSnapshot


@pytest.mark.parametrize(
    ("first", "last", "references", "mode", "reason"),
    [
        (False, False, False, "t2va", "h3.auto_text"),
        (True, False, False, "i2va", "h3.auto_first"),
        (True, True, False, "fl2va", "h3.auto_first_last"),
        (False, True, False, "l2va", "h3.auto_last"),
        (False, False, True, "ref2va", "h3.auto_references"),
        (True, True, True, "ref2va", "h3.auto_references"),
    ],
)
def test_auto_mode_freezes_the_complete_input_matrix(
    first: bool,
    last: bool,
    references: bool,
    mode: str,
    reason: str,
) -> None:
    decision = select_h3_mode(
        requested="auto",
        has_first_frame=first,
        has_last_frame=last,
        has_references=references,
        endpoint_reachable=True,
        exact_terminal_state=False,
        motion_level=0,
    )

    assert decision.mode == mode
    assert decision.reason_codes == (reason,)
    assert decision.blockers == ()
    assert decision.input_snapshot == H3ModeInputSnapshot(
        has_first_frame=first,
        has_last_frame=last,
        reference_count=int(references),
    )


@pytest.mark.parametrize(
    ("requested", "first", "last", "references"),
    [
        ("t2va", False, False, False),
        ("i2va", True, False, False),
        ("fl2va", True, True, False),
        ("l2va", False, True, False),
        ("ref2va", False, False, True),
    ],
)
def test_all_explicit_modes_succeed_without_rewriting(
    requested: str,
    first: bool,
    last: bool,
    references: bool,
) -> None:
    decision = select_h3_mode(
        requested=requested,  # type: ignore[arg-type]
        has_first_frame=first,
        has_last_frame=last,
        has_references=references,
        endpoint_reachable=True,
        exact_terminal_state=False,
        motion_level=0,
    )

    assert decision.mode == requested
    assert decision.reason_codes == (f"h3.explicit_{requested}",)
    assert decision.blockers == ()


@pytest.mark.parametrize(
    ("requested", "first", "last", "references", "expected"),
    [
        (
            "t2va",
            True,
            True,
            True,
            (
                "h3.first_frame_forbidden",
                "h3.last_frame_forbidden",
                "h3.references_forbidden",
            ),
        ),
        ("i2va", False, False, False, ("h3.first_frame_required",)),
        (
            "i2va",
            True,
            True,
            True,
            ("h3.last_frame_forbidden", "h3.references_forbidden"),
        ),
        (
            "fl2va",
            False,
            False,
            False,
            ("h3.first_frame_required", "h3.last_frame_required"),
        ),
        ("fl2va", True, True, True, ("h3.references_forbidden",)),
        ("l2va", False, False, False, ("h3.last_frame_required",)),
        (
            "l2va",
            True,
            True,
            True,
            ("h3.first_frame_forbidden", "h3.references_forbidden"),
        ),
        ("ref2va", False, False, False, ("h3.references_required",)),
        (
            "ref2va",
            True,
            True,
            True,
            ("h3.first_frame_forbidden", "h3.last_frame_forbidden"),
        ),
    ],
)
def test_explicit_modes_report_all_missing_and_forbidden_inputs(
    requested: str,
    first: bool,
    last: bool,
    references: bool,
    expected: tuple[str, ...],
) -> None:
    decision = select_h3_mode(
        requested=requested,  # type: ignore[arg-type]
        has_first_frame=first,
        has_last_frame=last,
        has_references=references,
        endpoint_reachable=True,
        exact_terminal_state=False,
        motion_level=0,
    )

    assert decision.mode is None
    assert decision.reason_codes == ()
    assert decision.blockers == expected


@pytest.mark.parametrize("mode", ["fl2va", "l2va"])
@pytest.mark.parametrize(
    ("motion_level", "endpoint_reachable"),
    [(2, True), (0, False), (2, False)],
)
def test_unreachable_motion_only_blocks_terminal_interpolation_modes(
    mode: str,
    motion_level: int,
    endpoint_reachable: bool,
) -> None:
    decision = select_h3_mode(
        requested=mode,  # type: ignore[arg-type]
        has_first_frame=mode == "fl2va",
        has_last_frame=True,
        endpoint_reachable=endpoint_reachable,
        exact_terminal_state=False,
        motion_level=motion_level,  # type: ignore[arg-type]
    )

    assert decision.mode is None
    assert decision.blockers == ("h3.unreachable_motion",)


@pytest.mark.parametrize("mode", ["t2va", "i2va", "ref2va"])
def test_non_interpolation_modes_ignore_unreachable_motion(mode: str) -> None:
    decision = select_h3_mode(
        requested=mode,  # type: ignore[arg-type]
        has_first_frame=mode == "i2va",
        has_last_frame=False,
        has_references=mode == "ref2va",
        endpoint_reachable=False,
        exact_terminal_state=False,
        motion_level=2,
    )

    assert decision.mode == mode
    assert decision.blockers == ()


@pytest.mark.parametrize("mode", ["t2va", "i2va"])
def test_exact_terminal_state_requires_a_terminal_capable_mode(mode: str) -> None:
    decision = select_h3_mode(
        requested=mode,  # type: ignore[arg-type]
        has_first_frame=mode == "i2va",
        has_last_frame=False,
        endpoint_reachable=True,
        exact_terminal_state=True,
        motion_level=0,
    )

    assert decision.mode is None
    assert decision.blockers == (
        "h3.exact_terminal_requires_terminal_frame",
    )


@pytest.mark.parametrize("mode", ["fl2va", "l2va", "ref2va"])
def test_terminal_capable_modes_support_exact_terminal_state(mode: str) -> None:
    decision = select_h3_mode(
        requested=mode,  # type: ignore[arg-type]
        has_first_frame=mode == "fl2va",
        has_last_frame=mode in {"fl2va", "l2va"},
        has_references=mode == "ref2va",
        endpoint_reachable=True,
        exact_terminal_state=True,
        motion_level=0,
    )

    assert decision.mode == mode
    assert decision.blockers == ()


def test_supported_modes_blocks_without_fallback_and_keeps_stable_priority() -> None:
    supported: Collection[str] = {"i2va"}
    decision = select_h3_mode(
        requested="fl2va",
        has_first_frame=False,
        has_last_frame=False,
        endpoint_reachable=False,
        exact_terminal_state=False,
        motion_level=2,
        supported_modes=supported,  # type: ignore[arg-type]
    )

    assert decision.mode is None
    assert decision.blockers == (
        "h3.first_frame_required",
        "h3.last_frame_required",
        "h3.unreachable_motion",
        "h3.mode_unsupported_by_workflow",
    )
    assert len(decision.blockers) == len(set(decision.blockers))


def test_auto_reference_priority_ignores_extra_frames_and_terminal_risk() -> None:
    decision = select_h3_mode(
        requested="auto",
        has_first_frame=True,
        has_last_frame=True,
        has_references=True,
        endpoint_reachable=False,
        exact_terminal_state=True,
        motion_level=2,
    )

    assert decision.mode == "ref2va"
    assert decision.blockers == ()
    assert decision.input_snapshot == H3ModeInputSnapshot(
        has_first_frame=True,
        has_last_frame=True,
        reference_count=1,
    )


def test_legacy_call_shape_remains_valid_and_still_freezes_a_snapshot() -> None:
    decision = select_h3_mode(
        requested="auto",
        has_first_frame=True,
        has_last_frame=False,
        endpoint_reachable=True,
        exact_terminal_state=False,
        motion_level=0,
    )

    assert decision.mode == "i2va"
    assert decision.input_snapshot == H3ModeInputSnapshot(
        has_first_frame=True,
        has_last_frame=False,
        reference_count=0,
    )
