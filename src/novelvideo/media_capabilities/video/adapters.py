"""Execution adapters for scene-aware video workflow definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from typing import Protocol

from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.runtime import H3GenerationResult
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowUnavailable,
)
from novelvideo.project_context import ProjectContext


@dataclass(frozen=True, slots=True)
class NarrativeGroupVideoRequest:
    segments: tuple[H3DirectorSegment, ...]
    output_path: str
    aspect_ratio: str
    resolution: str | None = None


@dataclass(frozen=True, slots=True)
class NarrativeGroupVideoResult:
    output_path: str
    provider_task_id: str | None
    actual_mode: str


class VideoWorkflowAdapter(Protocol):
    """Execute one registered workflow without exposing provider details."""

    adapter_key: str

    async def generate_narrative_group(
        self,
        ctx: ProjectContext,
        request: NarrativeGroupVideoRequest,
    ) -> NarrativeGroupVideoResult: ...


class H3DirectorGenerator(Protocol):
    def __call__(
        self,
        ctx: ProjectContext,
        *,
        segments: tuple[H3DirectorSegment, ...],
        output_path: str,
        aspect_ratio: str,
        resolution: str | None,
    ) -> Awaitable[H3GenerationResult]: ...


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
        generator: H3DirectorGenerator | None = None,
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
        request: NarrativeGroupVideoRequest,
    ) -> NarrativeGroupVideoResult:
        generated = await self._generator(
            ctx,
            segments=request.segments,
            output_path=request.output_path,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
        )
        return NarrativeGroupVideoResult(
            output_path=str(generated.output_path),
            provider_task_id=(
                str(generated.provider_task_id)
                if generated.provider_task_id is not None
                else None
            ),
            actual_mode=str(generated.actual_mode),
        )


__all__ = [
    "H3WorkflowAdapter",
    "NarrativeGroupVideoRequest",
    "NarrativeGroupVideoResult",
    "VideoWorkflowAdapter",
    "VideoWorkflowAdapters",
]
