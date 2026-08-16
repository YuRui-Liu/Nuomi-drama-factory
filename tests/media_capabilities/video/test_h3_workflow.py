from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from novelvideo.media_capabilities.models import WorkflowProfile
from novelvideo.media_capabilities.runtime.compiler import compile_node_info


PROFILE_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "runninghub"
    / "minimax_h3_video_profile.json"
)


def test_minimax_h3_profile_locks_workflow_semantic_bindings() -> None:
    profile = WorkflowProfile.model_validate(
        json.loads(PROFILE_FIXTURE.read_text(encoding="utf-8"))
    )

    assert profile.workflow_id == "2087934731806658562"
    assert compile_node_info(
        profile,
        {
            "first_frame": "first-frame.png",
            "last_frame": "last-frame.png",
            "prompt": "女孩从门口跑到窗边",
            "duration": 5,
            "seed": 7,
            "fps": 24,
            "aspect_ratio": "9:16 (Portrait Widescreen)",
        },
    ) == [
        {"nodeId": "114", "fieldName": "image", "fieldValue": "first-frame.png"},
        {
            "nodeId": "115",
            "fieldName": "aspect_ratio",
            "fieldValue": "9:16 (Portrait Widescreen)",
        },
        {"nodeId": "131", "fieldName": "noise_seed", "fieldValue": 7},
        {"nodeId": "132", "fieldName": "fps", "fieldValue": 24},
        {
            "nodeId": "133",
            "fieldName": "prompt",
            "fieldValue": "女孩从门口跑到窗边",
        },
        {"nodeId": "135", "fieldName": "value", "fieldValue": 5},
        {"nodeId": "141", "fieldName": "image", "fieldValue": "last-frame.png"},
    ]
    assert profile.outputs == {
        "video": {"node_id": "136", "media_type": "video"}
    }
