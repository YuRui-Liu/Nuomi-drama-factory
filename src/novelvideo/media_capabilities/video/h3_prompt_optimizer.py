"""Typed, cached prompt optimization for MiniMax H3 frame-conditioned modes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .h3_prompt import render_h3_optimized_prompt
from .h3_timeline import H3DirectorSegment
from .models import H3Mode


_FORMAT_VERSION = 1
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class H3PromptContext(BaseModel):
    model_config = _MODEL_CONFIG
    visual_description: str
    narration: str
    prev_summary: str
    next_summary: str
    first_frame_sha256: str = Field(min_length=1)
    last_frame_sha256: str | None = None
    model_id: str = Field(min_length=1)


class H3PromptStructuredOutput(BaseModel):
    model_config = _MODEL_CONFIG
    integrated_multimodal_description: str = Field(min_length=1)
    overall_soundscape: str = Field(min_length=1)
    non_diegetic_music: str = Field(min_length=1)


class H3PromptOptimizationResult(BaseModel):
    model_config = _MODEL_CONFIG
    prompt: str = Field(min_length=1)
    input_hash: str = Field(min_length=64, max_length=64)
    format_version: int = _FORMAT_VERSION
    cache_hit: bool = False


class H3PromptOptimizer:
    def __init__(self, agent: Any, cache_dir: Path | str):
        self._agent = agent
        self._cache_dir = Path(cache_dir)

    async def optimize_segment(
        self,
        segment: H3DirectorSegment,
        context: H3PromptContext,
        mode: H3Mode,
    ) -> H3PromptOptimizationResult:
        mode = H3Mode(mode)
        if mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
            raise ValueError("H3 prompt optimization supports only i2va and fl2va")
        input_hash = _input_hash(segment, context, mode)
        cache_path = self._cache_dir / f"{segment.segment_id}-{input_hash}.json"
        cached = _load_cache(cache_path, input_hash)
        if cached is not None:
            return cached.model_copy(update={"cache_hit": True})

        # The draft is context only. It is never returned if typed generation fails.
        response = await self._agent.run(_build_task(segment, context, mode))
        output = H3PromptStructuredOutput.model_validate(response.output)
        prompt = render_h3_optimized_prompt(
            mode=mode,
            duration_seconds=segment.duration_seconds,
            dialogue=segment.dialogue,
            speaker=segment.speaker,
            tone=segment.tone,
            **output.model_dump(),
        )
        result = H3PromptOptimizationResult(prompt=prompt, input_hash=input_hash)
        _save_cache(cache_path, result)
        return result


def _input_hash(
    segment: H3DirectorSegment, context: H3PromptContext, mode: H3Mode
) -> str:
    payload = {
        "format_version": _FORMAT_VERSION,
        "mode": mode.value,
        "segment": segment.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_task(
    segment: H3DirectorSegment, context: H3PromptContext, mode: H3Mode
) -> str:
    return f"""你是 MiniMax H3 {mode.value} 视频提示词优化器。
优先使用中文，只返回约定的三个 typed 字段，不添加标签或时刻。
动作必须遵守输入帧可见事实并保持单向、连续、可拍摄。
对白必须准确、可辨识；画面动作需支持说话者口型、语气和给定时间段。

草稿：{segment.prompt}
时长：{segment.duration_seconds} 秒
说话者：{segment.speaker}
对白：{segment.dialogue}
语气：{segment.tone}
画面：{context.visual_description}
叙事：{context.narration}
前文：{context.prev_summary}
后文：{context.next_summary}
"""


def _load_cache(
    path: Path, expected_hash: str
) -> H3PromptOptimizationResult | None:
    if not path.is_file():
        return None
    try:
        result = H3PromptOptimizationResult.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if result.input_hash != expected_hash or result.format_version != _FORMAT_VERSION:
        return None
    return result


def _save_cache(path: Path, result: H3PromptOptimizationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temp.write_text(result.model_dump_json(), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


__all__ = [
    "H3PromptContext",
    "H3PromptOptimizationResult",
    "H3PromptOptimizer",
    "H3PromptStructuredOutput",
]
