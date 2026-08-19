from __future__ import annotations

import json

from scripts.smoke_runninghub_h3 import (
    WORKFLOW_ID,
    build_smoke_timeline_data,
    timeline_summary,
)


def test_smoke_uses_director_workflow_and_single_timeline_payload() -> None:
    payload = json.loads(
        build_smoke_timeline_data(
            first_frame_url="first.png",
            last_frame_url="last.png",
            prompt="人物说：我来了。",
        )
    )

    assert WORKFLOW_ID == "2089723723468328961"
    assert payload["version"] == 5
    assert payload["frameRate"] == 24
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["genImage"]["imageFile"] == "first.png"
    assert payload["segments"][0]["endImage"]["imageFile"] == "last.png"
    assert timeline_summary(payload) == (1, 124)
