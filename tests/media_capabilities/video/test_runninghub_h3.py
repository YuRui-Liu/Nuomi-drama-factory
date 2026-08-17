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
    ("first_frame", "last_frame", "expected_images", "disconnected_field"),
    [
        ("first.png", None, [("114", "uploaded/first.png")], "last_frame"),
        (None, "last.png", [("141", "uploaded/last.png")], "first_frame"),
        (
            "first.png",
            "last.png",
            [("114", "uploaded/first.png"), ("141", "uploaded/last.png")],
            None,
        ),
    ],
)
async def test_generate_minimax_h3_video_binds_frame_modes(
    first_frame: str | None,
    last_frame: str | None,
    expected_images: list[tuple[str, str]],
    disconnected_field: str | None,
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
                        node_id="136",
                    ),
                    ProviderResult(
                        url="https://rh-images.xiaoyaoyou.com/video.mp4",
                        output_type="video",
                        node_id="136",
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
    assert workflow_id == "2087934731806658562"
    assert [
        (item["nodeId"], item["fieldValue"])
        for item in node_info
        if item["fieldName"] == "image"
    ] == expected_images
    values = {item["nodeId"]: item["fieldValue"] for item in node_info}
    assert {key: values[key] for key in ("131", "133", "135")} == {
        "131": 7,
        "133": "女孩从门口跑到窗边",
        "135": 5.0,
    }
    assert client.downloaded == ["https://rh-images.xiaoyaoyou.com/video.mp4"]
    disconnected = [
        item
        for item in node_info
        if item["nodeId"] == "133"
        and item["fieldName"] in {"first_frame", "last_frame"}
    ]
    assert disconnected == (
        []
        if disconnected_field is None
        else [
            {
                "nodeId": "133",
                "fieldName": disconnected_field,
                "fieldValue": None,
            }
        ]
    )


@pytest.mark.asyncio
async def test_generate_minimax_h3_video_rejects_missing_frames() -> None:
    client = FakeClient([])

    with pytest.raises(ValueError, match="首帧或尾帧"):
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
