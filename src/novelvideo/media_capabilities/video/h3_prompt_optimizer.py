"""Typed, cached prompt optimization for MiniMax H3 frame-conditioned modes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.exceptions import ModelHTTPError

import httpx
from openai import APIConnectionError

from .h3_director_plan import H3DirectorPlan
from .h3_prompt_compiler import H3_PROMPT_COMPILER_VERSION, compile_h3_director_plan
from .h3_prompt_profile import (
    H3_DIRECTOR_SYSTEM_PROMPT,
    H3_PROMPT_PROFILE_ID,
    H3_PROMPT_PROFILE_VERSION,
)
from .h3_prompt_quality import (
    H3_PROMPT_QUALITY_VERSION,
    H3PromptQualityError,
    H3PromptQualityReport,
    inspect_h3_plan,
    normalize_h3_action_timeline,
)
from .h3_timeline import H3DirectorSegment
from .models import H3Mode


_FORMAT_VERSION = 4
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
DirectorModelFactory = Callable[[], Any]


class H3PromptOptimizationError(RuntimeError):
    """Optimization failed without producing a usable prompt or cache entry."""


class H3PromptOptimizationUnavailable(H3PromptOptimizationError):
    """The optional optimizer provider stayed unreachable after retries."""


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
    director_context: str = ""


H3PromptStructuredOutput = H3DirectorPlan


class H3PromptOptimizationResult(BaseModel):
    model_config = _MODEL_CONFIG
    prompt: str = Field(min_length=1)
    plan: H3DirectorPlan
    quality_report: H3PromptQualityReport
    input_hash: str = Field(min_length=64, max_length=64)
    prompt_profile_id: str = H3_PROMPT_PROFILE_ID
    prompt_profile_version: int = H3_PROMPT_PROFILE_VERSION
    compiler_version: int = H3_PROMPT_COMPILER_VERSION
    format_version: int = _FORMAT_VERSION
    cache_hit: bool = False


def compile_and_gate_h3_plan(
    plan: H3DirectorPlan,
    *,
    segment: H3DirectorSegment,
    context: H3PromptContext,
    mode: H3Mode,
    input_hash: str,
) -> H3PromptOptimizationResult:
    """Normalize, quality-gate and compile one typed H3 plan."""
    mode = H3Mode(mode)
    _validate_dialogue_contract(segment, dialogue_required=context.dialogue_required)
    if plan.mode is not mode:
        raise ValueError(
            f"director plan mode {plan.mode.value!r} does not match {mode.value!r}"
        )
    normalized = normalize_h3_action_timeline(plan)
    report = inspect_h3_plan(normalized, segment=segment, context=context)
    report.raise_for_failure()
    return H3PromptOptimizationResult(
        prompt=compile_h3_director_plan(normalized),
        plan=normalized,
        quality_report=report,
        input_hash=input_hash,
    )


class H3PromptOptimizer:
    def __init__(
        self,
        agent: Any,
        cache_dir: Path | str,
        *,
        max_attempts: int = 3,
        quality_revisions: int = 2,
        retry_base_delay_seconds: float = 0.5,
    ):
        self._agent = agent
        self._cache_dir = Path(cache_dir)
        self._max_attempts = max(1, max_attempts)
        self._quality_revisions = max(0, quality_revisions)
        self._retry_base_delay_seconds = max(0.0, retry_base_delay_seconds)

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

            base_task = _build_task(segment, context, mode)
            task = base_task
            for revision in range(self._quality_revisions + 1):
                response = await self._run_agent(task)
                try:
                    plan = H3DirectorPlan.model_validate(response.output)
                except Exception as exc:
                    raise ValueError(f"invalid typed director plan: {exc}") from exc
                if plan.mode is not mode:
                    raise ValueError(
                        f"director plan mode {plan.mode.value!r} does not match {mode.value!r}"
                    )
                plan = normalize_h3_action_timeline(plan)
                report = inspect_h3_plan(plan, segment=segment, context=context)
                if report.passed:
                    result = compile_and_gate_h3_plan(
                        plan,
                        segment=segment,
                        context=context,
                        mode=mode,
                        input_hash=input_hash,
                    )
                    _save_cache(cache_path, result)
                    return result
                if revision >= self._quality_revisions:
                    report.raise_for_failure()
                task = _build_quality_revision_task(base_task, plan, report)
            raise AssertionError("unreachable")
        except H3PromptQualityError:
            raise
        except H3PromptOptimizationError:
            raise
        except Exception as exc:
            raise H3PromptOptimizationError(
                f"H3 prompt optimization failed: {exc}"
            ) from exc

    async def _run_agent(self, task: str) -> Any:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._agent.run(task)
            except Exception as exc:
                if not _is_transient_provider_error(exc):
                    raise
                if attempt >= self._max_attempts:
                    raise H3PromptOptimizationUnavailable(
                        "H3 prompt optimization failed: Connection error after "
                        f"{attempt} attempts ({type(exc).__name__}: {exc})"
                    ) from exc
                delay = self._retry_base_delay_seconds * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)
        raise AssertionError("unreachable")


def create_h3_prompt_optimizer(
    *,
    cache_dir: Path | str,
    director_model_factory: DirectorModelFactory | None = None,
    model_settings: dict[str, Any] | None = None,
) -> H3PromptOptimizer:
    """Create a planner from a generic injected director-text model factory."""
    from novelvideo.text_task_runtime.runtime import (
        StructuredRuntimeAgent,
        current_text_task_runtime,
    )

    routed_runtime = current_text_task_runtime() if director_model_factory is None else None
    factory = director_model_factory or _default_director_model_factory
    settings = model_settings if model_settings is not None else _default_model_settings()
    kwargs: dict[str, Any] = {}
    if settings is not None:
        kwargs["model_settings"] = settings
    agent = (
        StructuredRuntimeAgent(
            routed_runtime,
            output_type=H3DirectorPlan,
            system_prompt=H3_DIRECTOR_SYSTEM_PROMPT,
        )
        if routed_runtime is not None
        else Agent(
            factory(),
            system_prompt=H3_DIRECTOR_SYSTEM_PROMPT,
            # DeepSeek thinking models reject tool_choice. PromptedOutput keeps the
            # typed validation contract without asking the provider to call a tool.
            output_type=PromptedOutput(H3DirectorPlan),
            name="MiniMax H3 Director Planner",
            retries={
                "tools": 1,
                "output": _positive_int_env(
                    "DRAMACLAW_H3_PROMPT_OUTPUT_RETRIES", 3
                ),
            },
            **kwargs,
        )
    )
    return H3PromptOptimizer(
        agent,
        cache_dir,
        max_attempts=_positive_int_env("DRAMACLAW_H3_PROMPT_MAX_ATTEMPTS", 3),
        quality_revisions=_non_negative_int_env(
            "DRAMACLAW_H3_PROMPT_QUALITY_REVISIONS", 2
        ),
        retry_base_delay_seconds=_non_negative_float_env(
            "DRAMACLAW_H3_PROMPT_RETRY_BASE_DELAY_SECONDS", 0.5
        ),
    )


def _default_director_model_factory() -> Any:
    from novelvideo.config import get_newapi_text_pydantic_model
    from novelvideo.official_defaults import DEFAULT_H3_PROMPT_OPTIMIZER_MODEL

    return get_newapi_text_pydantic_model(
        "H3_PROMPT_OPTIMIZER_MODEL",
        DEFAULT_H3_PROMPT_OPTIMIZER_MODEL,
    )


def _default_model_settings() -> dict[str, Any] | None:
    from novelvideo.config import get_pydantic_model_settings

    return get_pydantic_model_settings(
        thinking_level_override=os.getenv("H3_PROMPT_OPTIMIZER_THINKING_LEVEL", "low")
    )


def _is_transient_provider_error(exc: BaseException) -> bool:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__

    http_error = next(
        (item for item in chain if isinstance(item, ModelHTTPError)), None
    )
    if http_error is not None:
        return http_error.status_code in {408, 409, 425, 429} or (
            http_error.status_code >= 500
        )
    return any(
        isinstance(item, (APIConnectionError, httpx.TransportError)) for item in chain
    )


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _non_negative_float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _non_negative_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


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
        "prompt_profile": f"{H3_PROMPT_PROFILE_ID}@{H3_PROMPT_PROFILE_VERSION}",
        "compiler_version": H3_PROMPT_COMPILER_VERSION,
        "quality_version": H3_PROMPT_QUALITY_VERSION,
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
    total_frames = round(segment.duration_seconds * 24)
    terminal_rule = (
        "Use exactly one continuous shot. Describe Picture 1-to-Picture 2 "
        "differences and converge each difference before a final settle/end_lock "
        "at total_frames."
        if mode is H3Mode.FL2VA
        else "Anchor Picture 1 with establish at frame 0, then show concrete change."
    )
    return f"""H3_DIRECTOR_PROFILE={H3_PROMPT_PROFILE_ID}@{H3_PROMPT_PROFILE_VERSION}
Return one H3DirectorPlan object in English for {mode.value}; mode={mode.value}, fps=24, total_frames={total_frames}.
Picture 1 is the exact frame-0 truth. Preserve identity, clothing, props, lighting, geography, and screen direction.
Every dynamic camera requires type, direction, amplitude, and speed. Static cameras must explicitly use a static type.
Actions must cover every frame without gaps and progress through establish/prepare/execute/react/settle/end_lock as appropriate.
For every non-establish action, state action pacing or physical effort and a visible end state, not merely a subject plus direction.
Use concrete subject motion and visible results; never write 'moves naturally', 'camera slowly moves', or bare actions such as 'He walks forward.'
{terminal_rule}
Do not cut, teleport, morph, reset space, or invent visible text, UI, logos, particles, people, props, or locations.
Dialogue cues must concatenate to the exact source dialogue without rewriting, translating, normalizing punctuation, or changing whitespace.
Set stable speaker_id values (S1, S2...) and preserve the exact source speaker and language.

Source segment prompt: {segment.prompt}
Duration: {segment.duration_seconds} seconds ({total_frames} frames at 24 fps)
Speaker: {segment.speaker}
Verbatim dialogue: {segment.dialogue}
Performance tone: {segment.tone}
Dialogue required: {'yes' if context.dialogue_required else 'no'}
Picture 1 description: {context.visual_description}
Narration: {context.narration}
Previous context: {context.prev_summary}
Next context: {context.next_summary}
Picture 1 SHA-256: {context.first_frame_sha256}
Picture 2 SHA-256: {context.last_frame_sha256 or 'not supplied'}
Director-stage constraints: {context.director_context or 'none supplied; use only source and frame facts'}
"""


def _build_quality_revision_task(
    base_task: str,
    plan: H3DirectorPlan,
    report: H3PromptQualityReport,
) -> str:
    return f"""{base_task}

QUALITY_REVISION_REQUIRED
The previous candidate failed the deterministic pre-transport quality gate.
Return a complete corrected H3DirectorPlan, changing only what is necessary to resolve every issue.
Quality report: {report.model_dump_json()}
Previous candidate: {plan.model_dump_json()}
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
    "H3PromptOptimizationUnavailable",
    "H3PromptOptimizationResult",
    "H3PromptOptimizer",
    "H3PromptStructuredOutput",
    "compile_and_gate_h3_plan",
    "create_h3_prompt_optimizer",
]
