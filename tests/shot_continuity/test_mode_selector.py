from __future__ import annotations

import pytest

from novelvideo.shot_continuity.mode_selector import select_h3_mode


@pytest.mark.parametrize(
    ("requested", "last", "exact_terminal", "mode", "reason"),
    [
        ("i2va", False, False, "i2va", "explicit_i2va"),
        ("fl2va", True, False, "fl2va", "explicit_fl2va"),
        ("auto", False, False, "i2va", "soft_terminal"),
        ("auto", True, True, "fl2va", "reachable_exact_terminal"),
    ],
)
def test_select_h3_mode_success_matrix(
    requested: str,
    last: bool,
    exact_terminal: bool,
    mode: str,
    reason: str,
) -> None:
    decision = select_h3_mode(
        requested=requested,  # type: ignore[arg-type]
        has_first_frame=True,
        has_last_frame=last,
        endpoint_reachable=True,
        exact_terminal_state=exact_terminal,
        motion_level=0,
    )

    assert decision.mode == mode
    assert decision.reason_codes == (reason,)
    assert decision.blockers == ()


@pytest.mark.parametrize(
    ("requested", "last", "exact_terminal", "expected"),
    [
        ("fl2va", False, False, ("last_frame_required",)),
        ("i2va", True, True, ("exact_terminal_requires_fl2va",)),
        ("auto", False, True, ("last_frame_required",)),
    ],
)
def test_select_h3_mode_explicit_and_auto_boundaries(
    requested: str,
    last: bool,
    exact_terminal: bool,
    expected: tuple[str, ...],
) -> None:
    decision = select_h3_mode(
        requested=requested,  # type: ignore[arg-type]
        has_first_frame=True,
        has_last_frame=last,
        endpoint_reachable=True,
        exact_terminal_state=exact_terminal,
        motion_level=0,
    )

    assert decision.mode is None
    assert decision.blockers == expected


def test_select_h3_mode_blockers_follow_required_priority_and_deduplicate() -> None:
    decision = select_h3_mode(
        requested="fl2va",
        has_first_frame=False,
        has_last_frame=False,
        endpoint_reachable=False,
        exact_terminal_state=False,
        motion_level=2,
    )

    assert decision.mode is None
    assert decision.reason_codes == ()
    assert decision.blockers == (
        "first_frame_required",
        "unreachable_motion",
        "last_frame_required",
    )


def test_unreachable_endpoint_blocks_even_when_motion_risk_is_low() -> None:
    decision = select_h3_mode(
        requested="auto",
        has_first_frame=True,
        has_last_frame=True,
        endpoint_reachable=False,
        exact_terminal_state=False,
        motion_level=0,
    )

    assert decision.blockers == ("unreachable_motion",)
