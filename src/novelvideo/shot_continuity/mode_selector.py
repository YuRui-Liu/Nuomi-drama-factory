"""Select an H3 generation mode from endpoint constraints."""

from __future__ import annotations

from collections.abc import Collection
from typing import Literal

from .models import (
    H3ModeDecision,
    H3ModeInputSnapshot,
    H3RequestedMode,
    H3ResolvedMode,
)


def select_h3_mode(
    *,
    requested: H3RequestedMode,
    has_first_frame: bool,
    has_last_frame: bool,
    exact_terminal_state: bool,
    endpoint_reachable: bool,
    motion_level: Literal[0, 1, 2],
    has_references: bool | None = None,
    reference_count: int | None = None,
    supported_modes: Collection[H3ResolvedMode] | None = None,
) -> H3ModeDecision:
    """Resolve explicit or automatic mode selection with stable blocker priority."""
    valid_requested = {"auto", "t2va", "i2va", "fl2va", "l2va", "ref2va"}
    if requested not in valid_requested:
        raise ValueError("h3.requested_mode_invalid")

    if reference_count is not None and reference_count < 0:
        raise ValueError("h3.reference_count_invalid")
    frozen_reference_count = (
        int(bool(has_references)) if reference_count is None else reference_count
    )
    references_present = frozen_reference_count > 0
    if (
        has_references is not None
        and bool(has_references) != references_present
    ):
        raise ValueError("h3.reference_input_mismatch")

    snapshot = H3ModeInputSnapshot(
        has_first_frame=has_first_frame,
        has_last_frame=has_last_frame,
        reference_count=frozen_reference_count,
    )
    if requested == "auto":
        if references_present:
            mode: H3ResolvedMode = "ref2va"
            reason = "h3.auto_references"
        elif has_first_frame and has_last_frame:
            mode = "fl2va"
            reason = "h3.auto_first_last"
        elif has_first_frame:
            mode = "i2va"
            reason = "h3.auto_first"
        elif has_last_frame:
            mode = "l2va"
            reason = "h3.auto_last"
        else:
            mode = "t2va"
            reason = "h3.auto_text"
    else:
        mode = requested
        reason = f"h3.explicit_{mode}"

    blocker_flags = {
        "h3.first_frame_required": (
            requested != "auto" and mode in {"i2va", "fl2va"} and not has_first_frame
        ),
        "h3.last_frame_required": (
            requested != "auto" and mode in {"fl2va", "l2va"} and not has_last_frame
        ),
        "h3.references_required": (
            requested != "auto" and mode == "ref2va" and not references_present
        ),
        "h3.first_frame_forbidden": (
            requested != "auto"
            and mode in {"t2va", "l2va"}
            and has_first_frame
        ),
        "h3.last_frame_forbidden": (
            requested != "auto"
            and mode in {"t2va", "i2va"}
            and has_last_frame
        ),
        "h3.references_forbidden": (
            requested != "auto" and mode != "ref2va" and references_present
        ),
        "h3.unreachable_motion": (
            mode in {"fl2va", "l2va"}
            and (motion_level == 2 or not endpoint_reachable)
        ),
        "h3.exact_terminal_requires_terminal_frame": (
            exact_terminal_state and mode not in {"fl2va", "l2va", "ref2va"}
        ),
        "h3.mode_unsupported_by_workflow": (
            supported_modes is not None and mode not in supported_modes
        ),
    }
    blockers = tuple(code for code, blocked in blocker_flags.items() if blocked)
    if blockers:
        return H3ModeDecision(
            requested=requested,
            mode=None,
            input_snapshot=snapshot,
            blockers=blockers,
        )
    return H3ModeDecision(
        requested=requested,
        mode=mode,
        input_snapshot=snapshot,
        reason_codes=(reason,),
    )
