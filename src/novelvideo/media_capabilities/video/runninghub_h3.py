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
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorSegment,
    build_h3_timeline_data,
)


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


def _director_timeline_payload(
    *,
    first_frame_url: str,
    last_frame_url: str | None,
    prompt: str,
    duration: float,
    aspect_ratio: str,
) -> str:
    """Build the Director node's single-segment timeline contract."""
    from novelvideo.media_capabilities.video.runtime import (
        _director_timeline_payload as build_director_payload,
    )

    timeline = build_h3_timeline_data(
        (
            H3DirectorSegment(
                segment_id="segment-1",
                beat_number=1,
                prompt=prompt.strip(),
                duration_seconds=duration,
                first_frame="legacy-first-frame",
                last_frame="legacy-last-frame" if last_frame_url else None,
            ),
        ),
        strict_first_frame=True,
    )
    uploaded_frames: dict[str, object] = {
        "legacy-first-frame": {"imageFile": first_frame_url},
    }
    if last_frame_url:
        uploaded_frames["legacy-last-frame"] = {"imageFile": last_frame_url}
    return build_director_payload(
        timeline,
        uploaded_frames,
        aspect_ratio=aspect_ratio,
        resolution=None,
    )


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
    """Legacy single-shot wrapper over the H3 Director timeline workflow."""
    if not first_frame:
        raise ValueError("MiniMax H3 Director 单镜生成需要首帧")
    if not prompt.strip():
        raise ValueError("MiniMax H3 视频提示词不能为空")
    if duration <= 0:
        raise ValueError("MiniMax H3 视频时长必须大于 0")
    if max_polls <= 0:
        raise ValueError("max_polls must be positive")

    workflow_id = runtime.workflow_id(
        MediaCapability.VIDEO_FL2VA if last_frame else MediaCapability.VIDEO_I2VA
    )
    factory = client_factory or runtime.create_client
    async with factory() as client:
        first_frame_url = await client.upload(first_frame)
        last_frame_url = await client.upload(last_frame) if last_frame else None
        timeline_data = _director_timeline_payload(
            first_frame_url=first_frame_url,
            last_frame_url=last_frame_url,
            prompt=prompt,
            duration=duration,
            aspect_ratio=aspect_ratio,
        )
        from novelvideo.media_capabilities.runtime.compiler import compile_node_info
        from novelvideo.media_capabilities.video.runtime import (
            _director_semantic_values,
            load_h3_workflow_profile,
        )

        profile = load_h3_workflow_profile(workflow_id=workflow_id)
        node_info = compile_node_info(
            profile,
            {**_director_semantic_values(timeline_data), "timeline_data": timeline_data},
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
                            if item.node_id == "7"
                        and (
                            (item.output_type or "").lower() == "video"
                            or PurePosixPath(urlsplit(item.url).path).suffix.lower()
                            in {".mp4", ".mov", ".webm", ".mkv"}
                        )
                    ),
                    None,
                )
                if video is None:
                    raise RuntimeError("RunningHub MiniMax H3 Director 未返回节点 7 的视频")
                content = await client.download(video.url)
                suffix = PurePosixPath(urlsplit(video.url).path).suffix.lower()
                filename = f"video{suffix if suffix in {'.mp4', '.mov', '.webm', '.mkv'} else '.mp4'}"
                return MiniMaxH3VideoResult(content, filename, task_id)
            await asyncio.sleep(poll_interval)
    raise TimeoutError("RunningHub MiniMax H3 视频生成等待超时")


__all__ = ["MiniMaxH3VideoResult", "generate_minimax_h3_video"]
