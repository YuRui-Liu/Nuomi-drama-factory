"""Typed, cached prompt optimization for MiniMax H3 frame-conditioned modes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from .h3_prompt import render_h3_optimized_prompt
from .h3_timeline import H3DirectorSegment
from .models import H3Mode


_FORMAT_VERSION = 2
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class H3PromptOptimizationError(RuntimeError):
    """Optimization failed without producing a usable prompt or cache entry."""


class H3PromptContext(BaseModel):
    model_config = _MODEL_CONFIG
    visual_description: str
    narration: str
    prev_summary: str
    next_summary: str
    first_frame_sha256: str = Field(min_length=1)
    last_frame_sha256: str | None = None
    model_id: str = Field(min_length=1)
    dialogue_required: bool = False


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
        try:
            mode = H3Mode(mode)
            if mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
                raise ValueError("H3 prompt optimization supports only i2va and fl2va")
            _validate_dialogue_contract(segment, dialogue_required=context.dialogue_required)
            input_hash = _input_hash(segment, context, mode)
            segment_key = hashlib.sha256(
                segment.segment_id.encode("utf-8")
            ).hexdigest()[:16]
            cache_path = self._cache_dir / f"{segment_key}-{input_hash}.json"
            cached = _load_cache(cache_path, input_hash)
            if cached is not None:
                return cached.model_copy(update={"cache_hit": True})

            response = await self._agent.run(_build_task(segment, context, mode))
            try:
                output = H3PromptStructuredOutput.model_validate(response.output)
            except Exception as exc:
                raise ValueError(f"invalid typed output: {exc}") from exc
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
        except H3PromptOptimizationError:
            raise
        except Exception as exc:
            raise H3PromptOptimizationError(
                f"H3 prompt optimization failed: {exc}"
            ) from exc


def create_h3_prompt_optimizer(*, cache_dir: Path | str) -> H3PromptOptimizer:
    """Create the production typed H3 optimizer using the NewAPI text route."""
    from novelvideo.config import (
        get_newapi_text_pydantic_model,
        get_newapi_text_pydantic_model_settings,
    )
    from novelvideo.official_defaults import DEFAULT_H3_PROMPT_OPTIMIZER_MODEL

    settings = get_newapi_text_pydantic_model_settings(
        "H3_PROMPT_OPTIMIZER_THINKING_LEVEL", "low"
    )
    kwargs: dict[str, Any] = {}
    if settings is not None:
        kwargs["model_settings"] = settings
    agent = Agent(
        get_newapi_text_pydantic_model(
            "H3_PROMPT_OPTIMIZER_MODEL", DEFAULT_H3_PROMPT_OPTIMIZER_MODEL
        ),
        system_prompt=(
            "You are a MiniMax H3 video prompt director. Follow the official H3 "
            "prompt-writing guide and return only the typed fields. Write visual and "
            "audio descriptions in English while preserving all input-frame facts. "
            "Do not quote or rewrite dialogue; the renderer injects the exact original line."
        ),
        output_type=H3PromptStructuredOutput,
        name="MiniMax H3 Prompt Optimizer",
        **kwargs,
    )
    return H3PromptOptimizer(agent, cache_dir)


def _validate_dialogue_contract(
    segment: H3DirectorSegment, *, dialogue_required: bool
) -> None:
    dialogue = segment.dialogue.strip()
    speaker = segment.speaker.strip()
    if (dialogue_required or speaker or segment.tone.strip()) and not dialogue:
        raise ValueError("dialogue is required for a segment with dialogue intent")
    if dialogue and not speaker:
        raise ValueError("speaker is required for recognizable dialogue")


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
严格按 H3 官方规范使用 English 编写三个 typed 字段，不添加字段标签或首尾帧指令。
integrated_multimodal_description 必须以 [Shot 1] 开始，动作遵守输入帧事实并连续可拍摄。
程序会在渲染阶段注入原始对白；不得改写、翻译、引用或重复对白正文。
画面动作仍须支持指定说话者的口型与表演，语气为空时从上下文自然推断。

草稿：{segment.prompt}
时长：{segment.duration_seconds} 秒
说话者：{segment.speaker}
对白：{segment.dialogue}
语气：{segment.tone}
对白必需：{'是' if context.dialogue_required else '否'}
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
    "H3PromptOptimizationError",
    "H3PromptOptimizationResult",
    "H3PromptOptimizer",
    "H3PromptStructuredOutput",
    "create_h3_prompt_optimizer",
]
