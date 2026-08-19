from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from novelvideo.media_capabilities.models import (
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.configuration import (
    RunningHubRuntimeConfiguration,
)
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
)
from novelvideo.media_capabilities.video.runninghub_h3 import (
    MiniMaxH3VideoResult,
    generate_minimax_h3_video,
)


@dataclass
class FakeClient:
    snapshots: list[ProviderTaskSnapshot]

    def __post_init__(self) -> None:
        self.uploaded: list[Path] = []
        self.submitted: tuple[str, list[dict[str, object]]] | None = None
        self.downloaded: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def upload(self, path: str | Path) -> str:
        source = Path(path)
        self.uploaded.append(source)
        return f"uploaded/{source.name}"

    async def submit(self, workflow_id: str, node_info: list[dict[str, object]]) -> str:
        self.submitted = (workflow_id, node_info)
        return "rh-task-1"

    async def query(self, task_id: str) -> ProviderTaskSnapshot:
        assert task_id == "rh-task-1"
        return self.snapshots.pop(0)

    async def download(self, url: str) -> bytes:
        self.downloaded.append(url)
        return b"video-only"


def runtime_with() -> RunningHubRuntimeConfiguration:
    return RunningHubRuntimeConfiguration(
        account=ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="keyring://runninghub-main",
            enabled=True,
        ),
        api_key="secret",
        workflows=RunningHubWorkflowSettings(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_frame", "last_frame", "expected_mode"),
    [
        ("first.png", None, "i2v"),
        ("first.png", "last.png", "fl2v"),
    ],
)
async def test_generate_minimax_h3_video_wraps_legacy_call_as_director_segment(
    first_frame: str,
    last_frame: str | None,
    expected_mode: str,
) -> None:
    client = FakeClient(
        [
            ProviderTaskSnapshot(status="queued"),
            ProviderTaskSnapshot(
                status="succeeded",
                results=(
                    ProviderResult(
                        url="https://rh-images.xiaoyaoyou.com/audio.mp3",
                        output_type="audio",
                        node_id="7",
                    ),
                    ProviderResult(
                        url="https://rh-images.xiaoyaoyou.com/video.mp4",
                        output_type="video",
                        node_id="7",
                    ),
                ),
            ),
        ]
    )

    result = await generate_minimax_h3_video(
        runtime_with(),
        first_frame=first_frame,
        last_frame=last_frame,
        prompt="女孩从门口跑到窗边",
        duration=5,
        aspect_ratio="9:16",
        seed=7,
        poll_interval=0,
        max_polls=2,
        client_factory=lambda: client,
    )

    assert result == MiniMaxH3VideoResult(
        content=b"video-only",
        filename="video.mp4",
        provider_task_id="rh-task-1",
    )
    assert client.submitted is not None
    workflow_id, node_info = client.submitted
    assert workflow_id == "2089723723468328961"
    assert node_info and len(node_info) == 1
    assert node_info[0]["nodeId"] == "12"
    assert node_info[0]["fieldName"] == "timeline_data"
    import json
    payload = json.loads(node_info[0]["fieldValue"])
    assert payload["timelineMode"] == expected_mode
    assert payload["frameRate"] == 24
    assert payload["totalFrames"] == 124
    assert len(payload["segments"]) == 1
    segment = payload["segments"][0]
    assert segment["prompt"] == "女孩从门口跑到窗边"
    assert segment["genImage"] == {"imageFile": "uploaded/first.png"}
    assert segment["endImage"] == (
        {"imageFile": "uploaded/last.png"} if last_frame else None
    )
    assert [path.name for path in client.uploaded] == (
        ["first.png", "last.png"] if last_frame else ["first.png"]
    )
    assert client.downloaded == ["https://rh-images.xiaoyaoyou.com/video.mp4"]


@pytest.mark.asyncio
async def test_generate_minimax_h3_video_rejects_missing_first_frame() -> None:
    client = FakeClient([])

    with pytest.raises(ValueError, match="首帧"):
        await generate_minimax_h3_video(
            runtime_with(),
            first_frame=None,
            last_frame=None,
            prompt="镜头缓慢推进",
            duration=5,
            aspect_ratio="9:16",
            client_factory=lambda: client,
        )


@pytest.mark.asyncio
async def test_generate_minimax_h3_video_times_out() -> None:
    client = FakeClient([ProviderTaskSnapshot(status="running")])

    with pytest.raises(TimeoutError, match="等待超时"):
        await generate_minimax_h3_video(
            runtime_with(),
            first_frame="first.png",
            last_frame=None,
            prompt="镜头缓慢推进",
            duration=5,
            aspect_ratio="9:16",
            poll_interval=0,
            max_polls=1,
            client_factory=lambda: client,
        )
