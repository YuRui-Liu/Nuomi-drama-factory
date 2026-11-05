from __future__ import annotations

import copy
import json

import pytest

from novelvideo.creation.production_progress import (
    ProgressConflict,
    ProgressValidation,
    ProductionProgress,
    approve_stage,
    complete_stage,
    configure_progress,
    fail_stage,
    new_progress,
    should_pause_after,
    should_stop_after,
)


def test_review_requires_both_storyboard_outputs():
    state = new_progress("003", mode="review")

    with pytest.raises(ProgressValidation, match="grid"):
        complete_stage(state, "storyboard", "h1", {"table": "table.json"})

    result = complete_stage(
        state,
        "storyboard",
        "h1",
        {"table": "table.json", "grid": "grid.png"},
    )

    assert result["stages"]["storyboard"]["status"] == "awaiting_review"
    assert result["stages"]["storyboard"]["approval_hash"]


def test_auto_mode_skips_the_three_default_review_stops():
    state = new_progress("003", mode="auto")
    state = complete_stage(state, "screenplay", "script-input", {"script": "script.md"})
    state = complete_stage(state, "assets", "assets-input", {"reuse_checklist": "assets.json"})
    state = complete_stage(
        state,
        "storyboard",
        "storyboard-input",
        {"table": "table.json", "grid": "grid.png"},
    )

    assert [state["stages"][stage]["status"] for stage in ("screenplay", "assets", "storyboard")] == [
        "ready",
        "ready",
        "ready",
    ]


def test_stop_after_takes_precedence_over_auto_mode():
    state = new_progress("003", mode="auto", stop_after="storyboard")
    state = complete_stage(state, "storyboard", "board-input", {"table": "table.json", "grid": "grid.png"})

    assert state["stages"]["storyboard"]["status"] == "ready"
    assert should_stop_after(state, "storyboard") is True
    assert should_stop_after(state, "video") is False
    assert should_pause_after(state, "storyboard") is True


def test_changing_mode_preserves_completed_and_pending_stage_records():
    state = new_progress("003", mode="review")
    state = complete_stage(state, "video", "video-input", {"video": "video.mp4"})
    before = copy.deepcopy(state["stages"])

    changed = configure_progress(state, mode="auto")

    assert changed["mode"] == "auto"
    assert changed["stages"] == before


def test_switching_to_auto_releases_the_current_review_gate_without_fake_approval():
    state = new_progress("003", mode="review")
    state = complete_stage(state, "screenplay", "script-input", {"script": "script.md"})

    assert should_pause_after(state, "screenplay") is True
    assert should_pause_after(state, "assets") is False
    continued = configure_progress(state, mode="auto")

    assert continued["stages"]["screenplay"]["status"] == "ready"
    assert continued["stages"]["screenplay"]["review_resolution"] == "auto"
    assert continued["stages"]["screenplay"]["approved_hash"] is None
    assert should_pause_after(continued, "screenplay") is False


def test_manual_approval_clears_a_review_pause():
    state = new_progress("003", mode="review")
    state = complete_stage(state, "assets", "assets-input", {"assets": "assets.json"})
    assert should_pause_after(state, "assets") is True

    approved = approve_stage(state, "assets", state["stages"]["assets"]["approval_hash"])

    assert should_pause_after(approved, "assets") is False


def test_approval_requires_the_current_displayed_result_fingerprint():
    state = new_progress("003", mode="review")
    state = complete_stage(state, "screenplay", "script-input", {"script": "script.md"})
    old_fingerprint = state["stages"]["screenplay"]["approval_hash"]
    changed = complete_stage(state, "screenplay", "script-input", {"script": "updated-script.md"})

    with pytest.raises(ProgressConflict, match="fingerprint"):
        approve_stage(changed, "screenplay", old_fingerprint)

    approved = approve_stage(changed, "screenplay", changed["stages"]["screenplay"]["approval_hash"])
    assert approved["stages"]["screenplay"]["status"] == "ready"
    assert approved["stages"]["screenplay"]["approved_hash"] == changed["stages"]["screenplay"]["approval_hash"]


def test_changed_upstream_fingerprint_invalidates_only_dependent_downstream_stages():
    state = new_progress("003", mode="auto")
    state = complete_stage(state, "screenplay", "script-v1", {"script": "script.md"})
    state = complete_stage(state, "assets", "assets-v1", {"assets": "assets.json"})
    state = complete_stage(state, "storyboard", "board-v1", {"table": "table.json", "grid": "grid.png"})
    state = complete_stage(state, "video", "video-v1", {"video": "video.mp4"})
    state = complete_stage(state, "audio", "audio-v1", {"audio": "audio.wav"})
    state = complete_stage(state, "composition", "compose-v1", {"composition": "final.mp4"})
    state = complete_stage(state, "audio", "audio-v2", {"audio": "audio-new.wav"})

    assert state["stages"]["screenplay"]["status"] == "ready"
    assert state["stages"]["assets"]["status"] == "ready"
    assert state["stages"]["storyboard"]["status"] == "ready"
    assert state["stages"]["video"]["status"] == "ready"
    assert state["stages"]["audio"]["status"] == "ready"
    assert state["stages"]["composition"]["status"] == "pending"


def test_failed_episode_does_not_mutate_another_episodes_progress():
    first = new_progress("003", mode="auto")
    second = new_progress("004", mode="review")
    second_before = copy.deepcopy(second)

    failed = fail_stage(first, "screenplay", "missing source", input_hash="h1")

    assert failed["blocked"]["episode_id"] == "003"
    assert failed["stages"]["screenplay"]["status"] == "failed"
    assert second == second_before


def test_recompleting_same_outputs_recovers_a_failed_stage():
    state = new_progress("003", mode="review")
    outputs = {"script": "episodes/003/script.md"}
    completed = complete_stage(state, "screenplay", "script-input", outputs)
    failed = fail_stage(completed, "screenplay", "temporary provider failure", input_hash="script-input")

    recovered = complete_stage(failed, "screenplay", "script-input", outputs)

    stage = recovered["stages"]["screenplay"]
    assert stage["status"] == "awaiting_review"
    assert stage["error"] is None
    assert stage["approval_hash"] == completed["stages"]["screenplay"]["approval_hash"]
    assert recovered["blocked"] is None


def test_persisted_progress_checks_real_output_files_and_rejects_symlinks(tmp_path):
    state_root = tmp_path / "state"
    output_root = tmp_path / "project"
    progress = ProductionProgress(state_root, output_root=output_root)
    assert progress.get("003")["episode_id"] == "003"
    output = output_root / "episodes" / "003" / "script.md"
    output.parent.mkdir(parents=True)
    output.write_text("完整剧本", encoding="utf-8")

    completed = progress.complete(
        "003", "screenplay", "script-input", {"script": "episodes/003/script.md"}
    )
    assert completed["stages"]["screenplay"]["outputs"]["script"]["sha256"]
    assert json.loads((state_root / "creation" / "progress" / "003.json").read_text(encoding="utf-8")) == completed

    outside = output_root.parent / "outside-script.md"
    outside.write_text("outside", encoding="utf-8")
    (output_root / "episodes" / "003" / "outside.md").symlink_to(outside)
    with pytest.raises(ProgressValidation, match="symlink"):
        progress.complete("003", "screenplay", "other-input", {"script": "episodes/003/outside.md"})


def test_persisted_approval_rejects_changed_output_after_review(tmp_path):
    progress = ProductionProgress(tmp_path)
    script = tmp_path / "episodes" / "003" / "script.md"
    script.parent.mkdir(parents=True)
    script.write_text("reviewed", encoding="utf-8")
    state = progress.complete("003", "screenplay", "script-input", {"script": "episodes/003/script.md"})
    fingerprint = state["stages"]["screenplay"]["approval_hash"]
    script.write_text("changed after review", encoding="utf-8")

    with pytest.raises(ProgressConflict, match="changed"):
        progress.approve("003", "screenplay", fingerprint)


def test_invalid_episode_ids_and_missing_outputs_are_rejected(tmp_path):
    progress = ProductionProgress(tmp_path)

    with pytest.raises(ProgressValidation):
        progress.get("../003")
    with pytest.raises(ProgressValidation, match="does not exist"):
        progress.complete("003", "screenplay", "h1", {"script": "episodes/003/missing.md"})
