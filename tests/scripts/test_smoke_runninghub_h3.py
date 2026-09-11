from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.h3_prompt import compile_h3
from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityError
from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.smoke_runninghub_h3 import (
    WORKFLOW_ID,
    build_smoke_timeline_data,
    timeline_summary,
)

smoke_runninghub_h3 = sys.modules[build_smoke_timeline_data.__module__]


def _official_prompt(mode: H3Mode) -> str:
    return compile_h3(
        MotionSpec(action="The actor looks up while the camera remains stable."),
        mode,
        duration_seconds=5,
    )


def test_smoke_uses_director_workflow_and_single_timeline_payload() -> None:
    payload = json.loads(
        build_smoke_timeline_data(
            first_frame_url="first.png",
            last_frame_url="last.png",
            prompt=_official_prompt(H3Mode.FL2VA),
        )
    )

    assert WORKFLOW_ID == "2089723723468328961"
    assert payload["version"] == 5
    assert payload["frameRate"] == 24
    assert payload["output"]["aspectRatio"] == "9:16 (竖版宽屏)"
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["genImage"]["imageFile"] == "first.png"
    assert payload["segments"][0]["endImage"]["imageFile"] == "last.png"
    assert timeline_summary(payload) == (1, 124)


def test_smoke_can_build_landscape_payload() -> None:
    payload = json.loads(
        build_smoke_timeline_data(
            first_frame_url="first.png",
            last_frame_url=None,
            prompt=_official_prompt(H3Mode.I2VA),
            aspect_ratio="16:9",
        )
    )

    assert payload["output"]["aspectRatio"] == "16:9 (宽屏)"


def test_smoke_rejects_non_official_prompt_before_building_payload() -> None:
    with pytest.raises(H3PromptQualityError):
        build_smoke_timeline_data(
            first_frame_url="first.png",
            last_frame_url=None,
            prompt="镜头稳定",
        )


@pytest.mark.asyncio
async def test_smoke_rejects_non_official_prompt_before_loading_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        smoke_runninghub_h3,
        "_load_key",
        lambda _env_file: pytest.fail("credentials loaded before prompt inspection"),
    )

    with pytest.raises(H3PromptQualityError):
        await smoke_runninghub_h3._run(SimpleNamespace(
            task_id=None,
            last_image=None,
            prompt="raw prompt",
        ))
