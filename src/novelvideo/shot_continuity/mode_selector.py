"""Select an H3 generation mode from endpoint constraints."""

from __future__ import annotations

from typing import Literal

from .models import H3ModeDecision


def select_h3_mode(
    *,
    requested: Literal["auto", "i2va", "fl2va"],
    has_first_frame: bool,
    has_last_frame: bool,
    exact_terminal_state: bool,
    endpoint_reachable: bool,
    motion_level: Literal[0, 1, 2],
) -> H3ModeDecision:
    """Resolve explicit or automatic mode selection with stable blocker priority."""
    blockers: list[str] = []
    if not has_first_frame:
        blockers.append("first_frame_required")
    if motion_level == 2 or not endpoint_reachable:
        blockers.append("unreachable_motion")
    if requested == "fl2va" and not has_last_frame:
        blockers.append("last_frame_required")
    if requested == "i2va" and exact_terminal_state:
        blockers.append("exact_terminal_requires_fl2va")
    if requested == "auto" and exact_terminal_state and not has_last_frame:
        blockers.append("last_frame_required")

    unique_blockers = tuple(dict.fromkeys(blockers))
    if unique_blockers:
        return H3ModeDecision(
            requested=requested,
            mode=None,
            blockers=unique_blockers,
        )

    if requested == "i2va":
        return H3ModeDecision(
            requested=requested,
            mode="i2va",
            reason_codes=("explicit_i2va",),
        )
    if requested == "fl2va":
        return H3ModeDecision(
            requested=requested,
            mode="fl2va",
            reason_codes=("explicit_fl2va",),
        )
    if exact_terminal_state:
        return H3ModeDecision(
            requested=requested,
            mode="fl2va",
            reason_codes=("reachable_exact_terminal",),
        )
    return H3ModeDecision(
        requested=requested,
        mode="i2va",
        reason_codes=("soft_terminal",),
    )
