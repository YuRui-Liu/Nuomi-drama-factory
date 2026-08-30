"""Whole-episode planning with isolated H3 segment repair and caching."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import Agent, PromptedOutput

from .h3_director_plan import H3DirectorPlan
from .h3_prompt_compiler import H3_PROMPT_COMPILER_VERSION
from .h3_prompt_optimizer import (
    H3PromptContext,
    H3PromptOptimizationResult,
    compile_and_gate_h3_plan,
)
from .h3_prompt_profile import (
    H3_DIRECTOR_SYSTEM_PROMPT,
    H3_PROMPT_PROFILE_ID,
    H3_PROMPT_PROFILE_VERSION,
)
from .h3_prompt_quality import H3PromptQualityError, H3PromptQualityReport
from .h3_timeline import H3DirectorSegment
from .models import H3Mode


_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_PACK_FORMAT_VERSION = 1
DirectorModelFactory = Callable[[], Any]


class H3SegmentPromptPlan(BaseModel):
    model_config = _MODEL_CONFIG
    segment_id: str = Field(min_length=1)
    director_plan: H3DirectorPlan


class H3EpisodePromptPack(BaseModel):
    model_config = _MODEL_CONFIG
    episode: int = Field(gt=0)
    director_revision_id: str = Field(min_length=1)
    segments: tuple[H3SegmentPromptPlan, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_segment_ids(self) -> "H3EpisodePromptPack":
        ids = [item.segment_id for item in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("episode prompt pack contains duplicate segment IDs")
        return self


class H3EpisodeVideoSegment(BaseModel):
    """Adapter DTO matching the VideoSegment boundary plus H3 source facts."""

    model_config = _MODEL_CONFIG
    segment_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    shot_ids: tuple[str, ...] = Field(min_length=1)
    duration_seconds: float = Field(gt=0, le=15, allow_inf_nan=False)
    style_snapshot_id: str = Field(min_length=1)
    source_segment: H3DirectorSegment
    context: H3PromptContext
    mode: H3Mode
    summary: str = Field(min_length=1)
    character_anchor: str = ""
    scene_anchor: str = ""

    @model_validator(mode="after")
    def matches_source_segment(self) -> "H3EpisodeVideoSegment":
        if self.source_segment.segment_id != self.segment_id:
            raise ValueError("source segment ID must match video segment ID")
        if self.source_segment.duration_seconds != self.duration_seconds:
            raise ValueError("source segment duration must match video segment duration")
        return self


class H3EpisodeInput(BaseModel):
    model_config = _MODEL_CONFIG
    episode: int = Field(gt=0)
    director_revision_id: str = Field(min_length=1)
    style_hash: str = Field(min_length=1)
    style_video: Mapping[str, Any]
    segments: tuple[H3EpisodeVideoSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_segment_ids(self) -> "H3EpisodeInput":
        ids = [item.segment_id for item in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("episode input contains duplicate segment IDs")
        return self


class H3EpisodeSegmentResult(BaseModel):
    model_config = _MODEL_CONFIG
    segment_id: str
    prompt: str
    plan: H3DirectorPlan
    quality_report: H3PromptQualityReport
    input_hash: str = Field(min_length=64, max_length=64)
    cache_hit: bool = False
    compiler_version: int = H3_PROMPT_COMPILER_VERSION
    format_version: int = _PACK_FORMAT_VERSION


class H3EpisodeOptimizationResult(BaseModel):
    model_config = _MODEL_CONFIG
    episode: int
    director_revision_id: str
    segments: tuple[H3EpisodeSegmentResult, ...]


class H3EpisodePackOptimizer:
    def __init__(
        self,
        agent: Any,
        cache_dir: Path | str,
        *,
        quality_revisions: int = 2,
    ) -> None:
        self._agent = agent
        self._cache_dir = Path(cache_dir)
        self._quality_revisions = min(2, max(0, quality_revisions))

    async def optimize(self, value: H3EpisodeInput) -> H3EpisodeOptimizationResult:
        cached: dict[str, H3EpisodeSegmentResult] = {}
        misses: list[H3EpisodeVideoSegment] = []
        hashes: dict[str, str] = {}
        paths: dict[str, Path] = {}
        for entry in value.segments:
            input_hash = _segment_input_hash(value, entry)
            hashes[entry.segment_id] = input_hash
            path = _cache_path(self._cache_dir, entry.segment_id, input_hash)
            paths[entry.segment_id] = path
            result = _load_cache(path, input_hash)
            if result is None:
                misses.append(entry)
            else:
                cached[entry.segment_id] = result.model_copy(
                    update={"cache_hit": True}
                )

        if misses:
            response = await self._agent.run(_episode_task(value))
            pack = H3EpisodePromptPack.model_validate(response.output)
            _validate_pack(pack, value, require_all=True)
            plans = {item.segment_id: item.director_plan for item in pack.segments}
            for entry in misses:
                plan = plans[entry.segment_id]
                try:
                    result = _compile(entry, plan, hashes[entry.segment_id])
                except H3PromptQualityError as exc:
                    result = await self._repair(
                        value,
                        entry,
                        plan,
                        exc,
                        hashes[entry.segment_id],
                    )
                wrapped = _wrap(entry.segment_id, result)
                _save_cache(paths[entry.segment_id], wrapped)
                cached[entry.segment_id] = wrapped

        return H3EpisodeOptimizationResult(
            episode=value.episode,
            director_revision_id=value.director_revision_id,
            segments=tuple(cached[item.segment_id] for item in value.segments),
        )

    async def _repair(
        self,
        value: H3EpisodeInput,
        entry: H3EpisodeVideoSegment,
        plan: H3DirectorPlan,
        failure: H3PromptQualityError,
        input_hash: str,
    ) -> H3PromptOptimizationResult:
        current = plan
        current_failure = failure
        for _attempt in range(self._quality_revisions):
            response = await self._agent.run(
                _repair_task(value, entry, current, current_failure)
            )
            pack = H3EpisodePromptPack.model_validate(response.output)
            _validate_pack(pack, value, expected_ids={entry.segment_id})
            current = pack.segments[0].director_plan
            try:
                return _compile(entry, current, input_hash)
            except H3PromptQualityError as exc:
                current_failure = exc
        raise current_failure


def create_h3_episode_pack_optimizer(
    *,
    cache_dir: Path | str,
    director_model_factory: DirectorModelFactory | None = None,
    model_settings: dict[str, Any] | None = None,
) -> H3EpisodePackOptimizer:
    from .h3_prompt_optimizer import (
        _default_director_model_factory,
        _default_model_settings,
        _non_negative_int_env,
    )

    factory = director_model_factory or _default_director_model_factory
    settings = model_settings if model_settings is not None else _default_model_settings()
    kwargs: dict[str, Any] = {}
    if settings is not None:
        kwargs["model_settings"] = settings
    agent = Agent(
        factory(),
        system_prompt=H3_DIRECTOR_SYSTEM_PROMPT,
        output_type=PromptedOutput(H3EpisodePromptPack),
        name="MiniMax H3 Episode Director Planner",
        retries={"tools": 1, "output": 3},
        **kwargs,
    )
    return H3EpisodePackOptimizer(
        agent,
        cache_dir,
        quality_revisions=_non_negative_int_env(
            "DRAMACLAW_H3_PROMPT_QUALITY_REVISIONS", 2
        ),
    )


def _compile(
    entry: H3EpisodeVideoSegment, plan: H3DirectorPlan, input_hash: str
) -> H3PromptOptimizationResult:
    return compile_and_gate_h3_plan(
        plan,
        segment=entry.source_segment,
        context=entry.context,
        mode=entry.mode,
        input_hash=input_hash,
    )


def _wrap(
    segment_id: str, result: H3PromptOptimizationResult
) -> H3EpisodeSegmentResult:
    return H3EpisodeSegmentResult(
        segment_id=segment_id,
        prompt=result.prompt,
        plan=result.plan,
        quality_report=result.quality_report,
        input_hash=result.input_hash,
    )


def _segment_input_hash(
    episode: H3EpisodeInput, entry: H3EpisodeVideoSegment
) -> str:
    payload = {
        "format_version": _PACK_FORMAT_VERSION,
        "director_revision_id": episode.director_revision_id,
        "segment_id": entry.segment_id,
        "group_id": entry.group_id,
        "shot_ids": entry.shot_ids,
        "style_hash": episode.style_hash,
        "style_snapshot_id": entry.style_snapshot_id,
        "first_frame_sha256": entry.context.first_frame_sha256,
        "last_frame_sha256": entry.context.last_frame_sha256,
        "compiler_version": H3_PROMPT_COMPILER_VERSION,
        "profile": f"{H3_PROMPT_PROFILE_ID}@{H3_PROMPT_PROFILE_VERSION}",
        "mode": entry.mode.value,
        "source": entry.source_segment.model_dump(
            mode="json", exclude={"first_frame", "last_frame"}
        ),
        "context": entry.context.model_dump(mode="json"),
        "style_video": dict(episode.style_video),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _episode_task(value: H3EpisodeInput) -> str:
    items = []
    for index, entry in enumerate(value.segments):
        items.append(_prompt_segment(value, index, entry))
    payload = {
        "episode": value.episode,
        "director_revision_id": value.director_revision_id,
        "style_video": dict(value.style_video),
        "segments": items,
    }
    return (
        "Return one H3EpisodePromptPack covering every supplied VideoSegment. "
        "Keep identity, scene geography, screen direction and pacing continuous. "
        "Each director_plan must obey the versioned MiniMax H3 director profile.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _prompt_segment(
    value: H3EpisodeInput, index: int, entry: H3EpisodeVideoSegment
) -> dict[str, Any]:
    previous = value.segments[index - 1].summary if index > 0 else ""
    following = value.segments[index + 1].summary if index + 1 < len(value.segments) else ""
    return {
        "segment_id": entry.segment_id,
        "group_id": entry.group_id,
        "shot_ids": entry.shot_ids,
        "duration_seconds": entry.duration_seconds,
        "mode": entry.mode.value,
        "summary": entry.summary,
        "previous_summary": previous,
        "next_summary": following,
        "source_prompt": entry.source_segment.prompt,
        "dialogue": entry.source_segment.dialogue,
        "speaker": entry.source_segment.speaker,
        "tone": entry.source_segment.tone,
        "character_anchor": entry.character_anchor,
        "scene_anchor": entry.scene_anchor,
        "visual_description": entry.context.visual_description,
        "narration": entry.context.narration,
        "first_frame_sha256": entry.context.first_frame_sha256,
        "last_frame_sha256": entry.context.last_frame_sha256,
    }


def _repair_task(
    value: H3EpisodeInput,
    entry: H3EpisodeVideoSegment,
    plan: H3DirectorPlan,
    failure: H3PromptQualityError,
) -> str:
    index = next(
        index for index, candidate in enumerate(value.segments) if candidate is entry
    )
    previous = value.segments[index - 1].summary if index > 0 else ""
    following = value.segments[index + 1].summary if index + 1 < len(value.segments) else ""
    payload = {
        "episode": value.episode,
        "director_revision_id": value.director_revision_id,
        "style_video": dict(value.style_video),
        "segment": _prompt_segment(value, index, entry),
        "previous_summary": previous,
        "next_summary": following,
        "quality_report": failure.report.model_dump(mode="json"),
        "previous_candidate": plan.model_dump(mode="json"),
    }
    return (
        "QUALITY_REVISION_REQUIRED. Return an H3EpisodePromptPack containing "
        "only the failing segment. Change only what resolves the listed issues.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _validate_pack(
    pack: H3EpisodePromptPack,
    value: H3EpisodeInput,
    *,
    require_all: bool = False,
    expected_ids: set[str] | None = None,
) -> None:
    if pack.episode != value.episode or pack.director_revision_id != value.director_revision_id:
        raise ValueError("episode prompt pack identity does not match request")
    actual = {item.segment_id for item in pack.segments}
    expected = expected_ids or {item.segment_id for item in value.segments}
    if actual != expected or (require_all and len(pack.segments) != len(value.segments)):
        raise ValueError("episode prompt pack segment coverage does not match request")


def _cache_path(root: Path, segment_id: str, input_hash: str) -> Path:
    safe_segment = hashlib.sha256(segment_id.encode("utf-8")).hexdigest()[:16]
    return root / f"{safe_segment}-{input_hash}.json"


def _load_cache(path: Path, input_hash: str) -> H3EpisodeSegmentResult | None:
    if not path.is_file():
        return None
    try:
        result = H3EpisodeSegmentResult.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if result.input_hash != input_hash or result.format_version != _PACK_FORMAT_VERSION:
        return None
    return result


def _save_cache(path: Path, result: H3EpisodeSegmentResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(result.model_dump_json(), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "H3EpisodeInput",
    "H3EpisodeOptimizationResult",
    "H3EpisodePackOptimizer",
    "H3EpisodePromptPack",
    "H3EpisodeSegmentResult",
    "H3EpisodeVideoSegment",
    "H3SegmentPromptPlan",
    "create_h3_episode_pack_optimizer",
]
