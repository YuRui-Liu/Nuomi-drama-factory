"""Bounded, per-scene structured dramatic-beat extraction."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pydantic import Field

from novelvideo.screenplay_semantics.models import FrozenModel, Scene, SourceRange
from novelvideo.screenplay_semantics.prompts import SYSTEM_PROMPT, build_scene_prompt
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


class DramaticBeatDraft(FrozenModel):
    source_ranges: tuple[SourceRange, ...] = Field(min_length=1)
    characters: tuple[str, ...] = ()
    goal: str = Field(min_length=1)
    obstacle: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reaction: str = Field(min_length=1)
    turn: str = Field(min_length=1)
    result: str = Field(min_length=1)
    emotional_shift: str = Field(min_length=1)
    dialogue_source_ids: tuple[str, ...] = ()
    estimated_duration_seconds: float = Field(gt=0, le=30)
    must_show: tuple[str, ...] = Field(min_length=1)
    script_facts: tuple[str, ...] = Field(min_length=1)
    director_interpretation: tuple[str, ...] = ()


class SceneBeatDraft(FrozenModel):
    scene_id: str = Field(min_length=1)
    beats: tuple[DramaticBeatDraft, ...]


class SceneExtractionFailure(FrozenModel):
    scene_id: str = Field(min_length=1)
    error: str = Field(min_length=1)


SceneExtractionResult = SceneBeatDraft | SceneExtractionFailure
SceneInvoker = Callable[[Scene, str], Awaitable[SceneBeatDraft]]


async def _runtime_invoke(scene: Scene, prompt: str) -> SceneBeatDraft:
    runtime = current_text_task_runtime()
    if runtime is None:
        raise RuntimeError("screenplay semantics requires a frozen text-task runtime")
    output = await runtime.run_structured(
        prompt=prompt,
        output_type=SceneBeatDraft,
        system_prompt=SYSTEM_PROMPT,
    )
    return output if isinstance(output, SceneBeatDraft) else SceneBeatDraft.model_validate(output)


async def extract_scene_beats(
    scenes: Sequence[Scene],
    *,
    invoke: SceneInvoker | None = None,
    concurrency: int = 5,
) -> tuple[SceneExtractionResult, ...]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    semaphore = asyncio.Semaphore(concurrency)
    call = invoke or _runtime_invoke

    async def extract(scene: Scene) -> SceneExtractionResult:
        async with semaphore:
            try:
                result = await call(scene, build_scene_prompt(scene))
                if result.scene_id != scene.id:
                    raise ValueError(
                        f"scene id mismatch: expected {scene.id}, got {result.scene_id}"
                    )
                return result
            except Exception as exc:  # per-scene checkpointable failure boundary
                return SceneExtractionFailure(scene_id=scene.id, error=str(exc))

    return tuple(await asyncio.gather(*(extract(scene) for scene in scenes)))


__all__ = [
    "DramaticBeatDraft",
    "SceneBeatDraft",
    "SceneExtractionFailure",
    "SceneExtractionResult",
    "extract_scene_beats",
]
