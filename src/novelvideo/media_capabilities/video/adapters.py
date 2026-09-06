"""Execution adapters for scene-aware video workflow definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.narrative_groups.video_references import ResolvedVideoReference
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
    workflow_parameters: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    resolution: str | None = None
    mode: str = "auto"
    reference_revision: int | None = None
    global_references: tuple[ResolvedVideoReference, ...] = ()
    reference_limit: int | None = None
    provider_workflow_id: str | None = None
    on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None

    def __post_init__(self) -> None:
        parameters = dict(self.workflow_parameters)
        parameters.setdefault("resolution", str(self.resolution or "720p"))
        object.__setattr__(self, "workflow_parameters", MappingProxyType(parameters))


@dataclass(frozen=True, slots=True)
class NarrativeGroupVideoResult:
    output_path: str
    provider_task_id: str | None
    actual_mode: str
    provider_parameters: dict[str, object] = field(default_factory=dict)
    actual_output: dict[str, int] = field(default_factory=dict)


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
        on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> Awaitable[H3GenerationResult]: ...


class H3ReferenceDirectorGenerator(Protocol):
    def __call__(
        self,
        ctx: ProjectContext,
        *,
        segments: tuple[H3DirectorSegment, ...],
        output_path: str,
        aspect_ratio: str,
        resolution: str | None,
        mode: str,
        global_references: tuple[ResolvedVideoReference, ...],
        reference_limit: int,
        workflow_id: str,
        on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
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
        from novelvideo.media_capabilities.video.h3_size_settings import (
            resolve_h3_size_setting,
        )

        try:
            resolution = request.workflow_parameters["resolution"]
        except KeyError as exc:
            raise ValueError("H3 workflow parameter 'resolution' is required") from exc
        setting = resolve_h3_size_setting(resolution, request.aspect_ratio)
        kwargs = {
            "segments": request.segments,
            "output_path": request.output_path,
            "aspect_ratio": request.aspect_ratio,
            "resolution": setting.resolution,
        }
        if request.on_provider_submitted is not None:
            kwargs["on_provider_submitted"] = request.on_provider_submitted
        generated = await self._generator(ctx, **kwargs)
        return NarrativeGroupVideoResult(
            output_path=str(generated.output_path),
            provider_task_id=(
                str(generated.provider_task_id)
                if generated.provider_task_id is not None
                else None
            ),
            actual_mode=str(generated.actual_mode),
            provider_parameters={
                "megapixels": setting.megapixels,
                "multiple": setting.multiple,
                "width": setting.width,
                "height": setting.height,
                "longEdge": setting.long_edge,
                "refMaxSize": setting.ref_max_size,
            },
            actual_output=(
                dict(generated_actual_output)
                if (generated_actual_output := getattr(generated, "actual_output", None))
                else {"width": setting.width, "height": setting.height}
            ),
        )


class H3ReferenceWorkflowAdapter:
    """Execute the reference-aware H3 workflow from a frozen request snapshot."""

    adapter_key = "minimax-h3-ref"

    def __init__(self, generator: H3ReferenceDirectorGenerator | None = None) -> None:
        if generator is None:
            from novelvideo.media_capabilities.video.h3_reference_runtime import (
                generate_h3_reference_director_video,
            )

            generator = generate_h3_reference_director_video
        self._generator = generator

    async def generate_narrative_group(
        self,
        ctx: ProjectContext,
        request: NarrativeGroupVideoRequest,
    ) -> NarrativeGroupVideoResult:
        from novelvideo.media_capabilities.video.h3_size_settings import (
            resolve_h3_size_setting,
        )

        if not request.global_references:
            raise ValueError("H3 reference workflow requires global references")
        if request.reference_limit is None:
            raise ValueError("H3 reference workflow requires a reference limit")
        if request.provider_workflow_id is None:
            raise ValueError("H3 reference workflow requires a provider workflow ID")
        resolution = request.workflow_parameters.get("resolution")
        if resolution is None:
            raise ValueError("H3 workflow parameter 'resolution' is required")
        setting = resolve_h3_size_setting(resolution, request.aspect_ratio)
        kwargs = {
            "segments": request.segments,
            "output_path": request.output_path,
            "aspect_ratio": request.aspect_ratio,
            "resolution": setting.resolution,
            "mode": request.mode,
            "global_references": request.global_references,
            "reference_limit": request.reference_limit,
            "workflow_id": request.provider_workflow_id,
        }
        if request.on_provider_submitted is not None:
            kwargs["on_provider_submitted"] = request.on_provider_submitted
        generated = await self._generator(ctx, **kwargs)
        return NarrativeGroupVideoResult(
            output_path=str(generated.output_path),
            provider_task_id=(
                str(generated.provider_task_id)
                if generated.provider_task_id is not None
                else None
            ),
            actual_mode=str(generated.actual_mode),
            provider_parameters={
                "workflowId": request.provider_workflow_id,
                "megapixels": setting.megapixels,
                "multiple": setting.multiple,
                "width": setting.width,
                "height": setting.height,
                "longEdge": setting.long_edge,
                "refMaxSize": setting.ref_max_size,
            },
            actual_output=(
                dict(generated_actual_output)
                if (generated_actual_output := getattr(generated, "actual_output", None))
                else {"width": setting.width, "height": setting.height}
            ),
        )


__all__ = [
    "H3ReferenceWorkflowAdapter",
    "H3WorkflowAdapter",
    "NarrativeGroupVideoRequest",
    "NarrativeGroupVideoResult",
    "VideoWorkflowAdapter",
    "VideoWorkflowAdapters",
]
