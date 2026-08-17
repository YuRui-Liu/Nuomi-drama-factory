"""Run the published MiniMax H3 image-to-video workflow on RunningHub."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlsplit

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.runtime.configuration import (
    RunningHubRuntimeConfiguration,
)
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderTaskSnapshot,
)


_ASPECT_RATIO_VALUES = {
    "1:1": "1:1 (Square)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)",
    "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}


class _RunningHubClient(Protocol):
    async def __aenter__(self): ...
    async def __aexit__(self, *args: object) -> None: ...
    async def upload(self, path: str | Path) -> str: ...
    async def submit(self, workflow_id: str, node_info: object) -> str: ...
    async def query(self, task_id: str) -> ProviderTaskSnapshot: ...
    async def download(self, url: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class MiniMaxH3VideoResult:
    content: bytes
    filename: str
    provider_task_id: str


async def generate_minimax_h3_video(
    runtime: RunningHubRuntimeConfiguration,
    *,
    first_frame: str | None,
    last_frame: str | None,
    prompt: str,
    duration: float,
    aspect_ratio: str,
    seed: int | None = None,
    poll_interval: float = 2.0,
    max_polls: int = 900,
    client_factory: Callable[[], _RunningHubClient] | None = None,
) -> MiniMaxH3VideoResult:
    """Generate one H3 video while deliberately ignoring native-audio outputs."""
    if not first_frame and not last_frame:
        raise ValueError("MiniMax H3 图生视频至少需要首帧或尾帧")
    if not prompt.strip():
        raise ValueError("MiniMax H3 视频提示词不能为空")
    if duration <= 0:
        raise ValueError("MiniMax H3 视频时长必须大于 0")
    if max_polls <= 0:
        raise ValueError("max_polls must be positive")

    workflow_id = runtime.workflow_id(MediaCapability.VIDEO_I2VA)
    factory = client_factory or runtime.create_client
    async with factory() as client:
        node_info: list[dict[str, object]] = []
        if first_frame:
            node_info.append(
                {
                    "nodeId": "114",
                    "fieldName": "image",
                    "fieldValue": await client.upload(first_frame),
                }
            )
        if last_frame:
            node_info.append(
                {
                    "nodeId": "141",
                    "fieldName": "image",
                    "fieldValue": await client.upload(last_frame),
                }
            )
        if first_frame and not last_frame:
            node_info.append(
                {
                    "nodeId": "133",
                    "fieldName": "last_frame",
                    "fieldValue": None,
                }
            )
        elif last_frame and not first_frame:
            node_info.append(
                {
                    "nodeId": "133",
                    "fieldName": "first_frame",
                    "fieldValue": None,
                }
            )
        node_info.extend(
            [
                {
                    "nodeId": "115",
                    "fieldName": "aspect_ratio",
                    "fieldValue": _ASPECT_RATIO_VALUES.get(aspect_ratio, aspect_ratio),
                },
                {
                    "nodeId": "133",
                    "fieldName": "prompt",
                    "fieldValue": prompt.strip(),
                },
                {
                    "nodeId": "135",
                    "fieldName": "value",
                    "fieldValue": float(duration),
                },
            ]
        )
        if seed is not None:
            node_info.append(
                {
                    "nodeId": "131",
                    "fieldName": "noise_seed",
                    "fieldValue": seed,
                }
            )

        task_id = await client.submit(workflow_id, node_info)
        for _ in range(max_polls):
            snapshot = await client.query(task_id)
            if snapshot.status in {"failed", "cancelled"}:
                raise RuntimeError(snapshot.provider_message or "RunningHub MiniMax H3 生成失败")
            if snapshot.status == "succeeded":
                video = next(
                    (
                        item
                        for item in snapshot.results
                        if item.node_id == "136"
                        and (
                            (item.output_type or "").lower() == "video"
                            or PurePosixPath(urlsplit(item.url).path).suffix.lower()
                            in {".mp4", ".mov", ".webm", ".mkv"}
                        )
                    ),
                    None,
                )
                if video is None:
                    raise RuntimeError("RunningHub MiniMax H3 未返回节点 136 的视频")
                content = await client.download(video.url)
                suffix = PurePosixPath(urlsplit(video.url).path).suffix.lower()
                filename = f"video{suffix if suffix in {'.mp4', '.mov', '.webm', '.mkv'} else '.mp4'}"
                return MiniMaxH3VideoResult(content, filename, task_id)
            await asyncio.sleep(poll_interval)
    raise TimeoutError("RunningHub MiniMax H3 视频生成等待超时")


__all__ = ["MiniMaxH3VideoResult", "generate_minimax_h3_video"]
