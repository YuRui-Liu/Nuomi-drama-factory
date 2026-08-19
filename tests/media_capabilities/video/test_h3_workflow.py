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
            "timeline_data": '{"entries":[]}',
        },
    ) == [
        {
            "nodeId": "12",
            "fieldName": "timeline_data",
            "fieldValue": '{"entries":[]}',
        },
    ]
    assert profile.outputs == {"video": {"node_id": "7", "media_type": "video"}}
