from __future__ import annotations

import pytest

from novelvideo.media_capabilities.video.runtime import (
    get_h3_concurrency_coordinator,
    load_h3_workflow_profile,
    resolve_h3_mode,
)


def test_production_profile_is_packaged_and_workflow_can_be_overridden() -> None:
    profile = load_h3_workflow_profile(workflow_id="9001")

    assert profile.id == "minimax-h3-video"
    assert profile.workflow_id == "9001"
    assert profile.bindings["first_frame"]["node_id"] == "114"
    assert profile.outputs["video"]["node_id"] == "136"


@pytest.mark.parametrize(
    ("requested", "last_frame", "expected"),
    [("auto", "last.png", "fl2va"), ("auto", None, "i2va"), ("i2va", None, "i2va")],
)
def test_h3_mode_uses_actual_frame_inputs(requested, last_frame, expected) -> None:
    assert resolve_h3_mode(requested, "first.png", last_frame).value == expected


def test_h3_mode_rejects_missing_first_or_required_last_frame() -> None:
    with pytest.raises(ValueError, match="first frame"):
        resolve_h3_mode("auto", None, None)
    with pytest.raises(ValueError, match="last frame"):
        resolve_h3_mode("fl2va", "first.png", None)


def test_h3_concurrency_is_shared_process_wide_per_provider() -> None:
    assert get_h3_concurrency_coordinator("runninghub-main") is get_h3_concurrency_coordinator("runninghub-main")
