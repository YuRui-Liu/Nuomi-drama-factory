"""Execute the published RunningHub Qwen3 voice-design workflow."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.runtime.configuration import (
    RunningHubRuntimeConfiguration,
)


async def generate_qwen3_voice_sample(
    runtime: RunningHubRuntimeConfiguration,
    *,
    audition_text: str,
    voice_description: str,
    language: str = "Chinese",
    poll_interval: float = 2.0,
    max_polls: int = 180,
) -> tuple[bytes, str]:
    """Submit, poll and download one Qwen3 voice-design audition."""
    audition_text = audition_text.strip()
    voice_description = voice_description.strip()
    if not audition_text or not voice_description:
        raise ValueError("试听文本和音色描述不能为空")

    workflow_id = runtime.workflow_id(MediaCapability.TTS_VOICE_DESIGN)
    # Published workflow contract: 14=text, 15=instruct. Node 22 keeps
    # language=Auto inside the immutable workflow and must not be overridden.
    node_info = [
        {"nodeId": "14", "fieldName": "text", "fieldValue": audition_text},
        {"nodeId": "15", "fieldName": "text", "fieldValue": voice_description},
    ]
    async with runtime.create_client() as client:
        task_id = await client.submit(workflow_id, node_info)
        for _ in range(max_polls):
            snapshot = await client.query(task_id)
            if snapshot.status in {"failed", "cancelled"}:
                raise RuntimeError(snapshot.provider_message or "RunningHub 音色设计失败")
            if snapshot.status == "succeeded":
                if not snapshot.results:
                    raise RuntimeError("RunningHub 音色设计未返回音频")
                result = snapshot.results[0]
                content = await client.download(result.url)
                suffix = PurePosixPath(urlsplit(result.url).path).suffix.lower()
                filename = f"voice{suffix if suffix in {'.wav', '.mp3', '.m4a', '.aac', '.ogg'} else '.wav'}"
                return content, filename
            await asyncio.sleep(poll_interval)
    raise TimeoutError("RunningHub 音色设计等待超时")


__all__ = ["generate_qwen3_voice_sample"]
