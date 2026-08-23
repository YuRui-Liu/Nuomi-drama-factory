"""Execution adapters for scene-aware video workflow definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowUnavailable,
)
from novelvideo.project_context import ProjectContext


class VideoWorkflowAdapter(Protocol):
    """Execute one registered workflow without exposing provider details."""

    adapter_key: str

    async def generate_narrative_group(
        self,
        ctx: ProjectContext,
        *,
        segments: tuple[Any, ...],
        output_path: str,
        aspect_ratio: str,
        resolution: str | None,
    ) -> Any: ...


class VideoWorkflowAdapters:
    """Resolve execution adapters by a workflow definition's adapter key."""

    def __init__(self, adapters: Iterable[VideoWorkflowAdapter]) -> None:
        self._by_key: dict[str, VideoWorkflowAdapter] = {}
        for adapter in adapters:
            key = str(adapter.adapter_key).strip()
            if key in self._by_key:
                raise ValueError(f"duplicate video workflow adapter: {key}")
            self._by_key[key] = adapter

    def resolve(self, adapter_key: str) -> VideoWorkflowAdapter:
        try:
            return self._by_key[adapter_key]
        except KeyError as exc:
            raise VideoWorkflowUnavailable(
                f"unknown video workflow adapter: {adapter_key}"
            ) from exc


class H3WorkflowAdapter:
    """Preserve the production H3 path behind the generic adapter contract."""

    adapter_key = "minimax-h3"

    def __init__(
        self,
        generator: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        if generator is None:
            from novelvideo.media_capabilities.video.runtime import (
                generate_h3_director_video,
            )

            generator = generate_h3_director_video
        self._generator = generator

    async def generate_narrative_group(
        self,
        ctx: ProjectContext,
        *,
        segments: tuple[Any, ...],
        output_path: str,
        aspect_ratio: str,
        resolution: str | None,
    ) -> Any:
        return await self._generator(
            ctx,
            segments=segments,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )


__all__ = [
    "H3WorkflowAdapter",
    "VideoWorkflowAdapter",
    "VideoWorkflowAdapters",
]
