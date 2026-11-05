from pathlib import Path

import pytest

from novelvideo.creation.production_admission import (
    admit_managed_stage,
    admit_registered_episode_stage,
    classify_target_stage,
    composition_requires_audio,
    storyboard_operation_is_planning,
)
from novelvideo.creation.production_progress import ProductionProgress
from novelvideo.freezone.orchestration.models import OrchestrationError


SCOPE = {
    "canvas_id": "managed",
    "kind": "episode",
    "episode_id": "episode-3",
    "episode_number": 3,
    "status": "applied",
}


def _ready(progress: ProductionProgress, tmp_path: Path, stage: str, outputs: dict):
    for output in outputs.values():
        path = tmp_path / output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(stage.encode())
    return progress.complete("003", stage, f"caller-hash-{stage}", outputs)


def _ready_before_video(progress: ProductionProgress, tmp_path: Path):
    progress.configure("003", mode="auto")
    _ready(progress, tmp_path, "screenplay", {"script": "episodes/003/script.md"})
    _ready(progress, tmp_path, "assets", {"reuse_checklist": "episodes/003/assets.md"})
    return _ready(progress, tmp_path, "storyboard", {
        "table": "episodes/003/storyboard.md",
        "grid": "episodes/003/storyboard-grid.png",
    })


def test_video_admission_rechecks_storyboard_file_hashes(tmp_path):
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    _ready_before_video(progress, tmp_path)
    (tmp_path / "episodes/003/storyboard-grid.png").write_bytes(b"replaced after approval")

    with pytest.raises(OrchestrationError) as captured:
        admit_managed_stage(tmp_path, tmp_path, SCOPE, "video")

    assert captured.value.as_dict() == {
        "code": "production_stage_blocked",
        "message": "Managed production stage video is blocked by storyboard",
        "stage": "video",
        "required_stage": "storyboard",
        "status": "pending",
        "reason": "output_changed",
    }
    state = progress.get("003", create=False)
    assert state["stages"]["storyboard"]["status"] == "pending"
    assert state["stages"]["video"]["status"] == "pending"
    assert (tmp_path / "episodes/003/storyboard-grid.png").exists()


def test_video_admission_respects_auto_stop_after(tmp_path):
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    _ready_before_video(progress, tmp_path)
    progress.configure("003", stop_after="storyboard")

    with pytest.raises(OrchestrationError) as captured:
        admit_managed_stage(tmp_path, tmp_path, SCOPE, "video")

    assert captured.value.code == "production_stage_blocked"
    assert captured.value.details["reason"] == "stop_after"
    assert captured.value.details["required_stage"] == "storyboard"


def test_final_composition_respects_stop_after_audio_without_requiring_audio_output(tmp_path):
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    _ready_before_video(progress, tmp_path)
    _ready(progress, tmp_path, "video", {"video": "episodes/003/video.mp4"})
    progress.configure("003", stop_after="audio")

    with pytest.raises(OrchestrationError) as captured:
        admit_managed_stage(tmp_path, tmp_path, SCOPE, "composition", requires_audio=False)

    assert captured.value.details == {
        "stage": "composition",
        "required_stage": "audio",
        "status": "pending",
        "reason": "stop_after",
    }


def test_audio_stage_is_allowed_when_auto_stop_after_is_audio(tmp_path):
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    progress.configure("003", mode="auto")
    _ready(progress, tmp_path, "screenplay", {"script": "episodes/003/script.md"})
    progress.configure("003", stop_after="audio")

    assert admit_managed_stage(tmp_path, tmp_path, SCOPE, "audio")


def test_dubbing_is_independent_of_video_and_composition_requires_audio_only_when_used(tmp_path):
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    progress.configure("003", mode="auto")
    _ready(progress, tmp_path, "screenplay", {"script": "episodes/003/script.md"})

    assert admit_managed_stage(tmp_path, tmp_path, SCOPE, "audio")

    _ready(progress, tmp_path, "assets", {"reuse_checklist": "episodes/003/assets.md"})
    _ready(progress, tmp_path, "storyboard", {
        "table": "episodes/003/storyboard.md",
        "grid": "episodes/003/storyboard-grid.png",
    })
    _ready(progress, tmp_path, "video", {"video": "episodes/003/video.mp4"})

    silent = {"id": "compose", "type": "videoComposeNode", "data": {
        "episodeNumber": 3, "operation": "compose", "generateAudio": False,
    }}
    with_dialogue = {"id": "compose-audio", "type": "videoComposeNode", "data": {
        "episodeNumber": 3, "operation": "compose", "dialogueIds": ["d-1"],
    }}
    assert not composition_requires_audio(silent)
    assert composition_requires_audio(with_dialogue)
    assert admit_managed_stage(tmp_path, tmp_path, SCOPE, "composition")
    with pytest.raises(OrchestrationError) as captured:
        admit_managed_stage(tmp_path, tmp_path, SCOPE, "composition", requires_audio=True)
    assert captured.value.details["required_stage"] == "audio"


def test_storyboard_text_planning_requires_screenplay_but_not_assets(tmp_path):
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    progress.configure("003", mode="review")
    screenplay = _ready(progress, tmp_path, "screenplay", {"script": "episodes/003/script.md"})
    progress.approve("003", "screenplay", screenplay["stages"]["screenplay"]["approval_hash"])

    assert admit_managed_stage(
        tmp_path, tmp_path, SCOPE, "storyboard", planning_only=True,
    )
    with pytest.raises(ValueError):
        admit_managed_stage(tmp_path, tmp_path, SCOPE, "video", planning_only=True)

    node = {"type": "directorWorkspaceNode", "data": {"episodeNumber": 3}}
    assert storyboard_operation_is_planning(node, "prepare")
    assert not storyboard_operation_is_planning(node, "render_groups")


@pytest.mark.parametrize("node,operation,expected", [
    ({"type": "imageGenNode", "data": {"episodeNumber": 3, "artifactKind": "asset"}}, "generate", "assets"),
    ({"type": "imageNode", "data": {"episodeNumber": 3, "artifactKind": "asset"}}, "edit", "assets"),
    ({"type": "imageGenNode", "data": {"episodeNumber": 3, "episodeAssetRequirement": "[\"scene\",\"hall\"]", "episodeAssetType": "scene"}}, "generate", "assets"),
    ({"type": "videoDirectorNode", "data": {"episodeNumber": 3}}, "generate", "video"),
    ({"type": "audioNode", "data": {"episodeNumber": 3}}, "speech", "audio"),
    ({"type": "videoComposeNode", "data": {"episodeNumber": 3}}, "compose", "composition"),
    ({"type": "imageGenNode", "data": {"episodeNumber": 3, "artifactKind": "sketch"}}, "generate", None),
    ({"type": "videoDirectorNode", "data": {}}, "generate", None),
])
def test_classifier_requires_explicit_episode_stage_binding(node, operation, expected):
    assert classify_target_stage(node, operation, episode_number=3) == expected


def test_direct_episode_admission_resolves_only_applied_registered_episode(tmp_path, monkeypatch):
    from novelvideo.creation import canvas_workspaces

    monkeypatch.setattr(canvas_workspaces, "list_canvases", lambda _root: [{
        "canvas_id": "managed", "kind": "episode", "episode_id": "episode-3",
        "episode_number": 3, "status": "applied",
    }])
    progress = ProductionProgress(tmp_path, output_root=tmp_path)
    progress.configure("003", mode="review")
    (tmp_path / "episodes/003/script.md").parent.mkdir(parents=True)
    (tmp_path / "episodes/003/script.md").write_text("script", encoding="utf-8")
    screenplay = progress.complete("003", "screenplay", "caller-value", {
        "script": "episodes/003/script.md",
    })
    progress.approve("003", "screenplay", screenplay["stages"]["screenplay"]["approval_hash"])

    with pytest.raises(OrchestrationError) as captured:
        admit_registered_episode_stage(tmp_path, tmp_path, 3, "storyboard")

    assert captured.value.details["required_stage"] == "assets"
    assert captured.value.details["status"] == "pending"


def test_direct_episode_admission_leaves_nonapplied_and_unconfigured_scopes_open(tmp_path, monkeypatch):
    from novelvideo.creation import canvas_workspaces

    monkeypatch.setattr(canvas_workspaces, "list_canvases", lambda _root: [{
        "canvas_id": "draft", "kind": "episode", "episode_id": "episode-3",
        "episode_number": 3, "status": "draft",
    }])

    assert not admit_registered_episode_stage(tmp_path, tmp_path, 3, "video")

    monkeypatch.setattr(canvas_workspaces, "list_canvases", lambda _root: [{
        "canvas_id": "managed", "kind": "episode", "episode_id": "episode-3",
        "episode_number": 3, "status": "applied",
    }])
    assert not admit_registered_episode_stage(tmp_path, tmp_path, 3, "video")

