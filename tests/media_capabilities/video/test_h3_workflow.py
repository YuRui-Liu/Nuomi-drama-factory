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

    assert profile.workflow_id == "2089723723468328961"
    assert compile_node_info(
        profile,
        {
            "task_type": "fl2v — 首尾帧生视频(First-Last Frame)",
            "global_prompt": "",
            "frame_rate": 24,
            "width": 416,
            "height": 736,
            "ref_max_size": 736,
            "total_frames": 124,
            "timeline_data": '{"entries":[]}',
        },
    ) == [
        {"nodeId": "12", "fieldName": "frame_rate", "fieldValue": 24},
        {"nodeId": "12", "fieldName": "global_prompt", "fieldValue": ""},
        {"nodeId": "12", "fieldName": "height", "fieldValue": 736},
        {"nodeId": "12", "fieldName": "ref_max_size", "fieldValue": 736},
        {
            "nodeId": "12",
            "fieldName": "task_type",
            "fieldValue": "fl2v — 首尾帧生视频(First-Last Frame)",
        },
        {
            "nodeId": "12",
            "fieldName": "timeline_data",
            "fieldValue": '{"entries":[]}',
        },
        {"nodeId": "12", "fieldName": "total_frames", "fieldValue": 124},
        {"nodeId": "12", "fieldName": "width", "fieldValue": 416},
    ]
    assert profile.outputs == {"video": {"node_id": "7", "media_type": "video"}}
