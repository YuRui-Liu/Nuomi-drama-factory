"""Structured text runtimes selected by a frozen task route."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar
from types import SimpleNamespace

from pydantic import ValidationError
from pydantic_ai import Agent, BinaryContent, PromptedOutput

from novelvideo.knowledge_runtime.codex import (
    CodexCliStructuredBackend as _BaseCodexCliStructuredBackend,
    StructuredImage,
    validate_structured_images,
)
from novelvideo.text_task_runtime.models import (
    AgentTaskRouteSnapshot,
    validate_text_task_model_name,
)

T = TypeVar("T")


def _reject_unsupported_skill(snapshot: AgentTaskRouteSnapshot) -> None:
    if snapshot.skill_id or snapshot.skill_version:
        raise ValueError(
            "Task routes do not support skill_id/skill_version with current runtimes"
        )


class StructuredTextRuntime(Protocol):
    snapshot: AgentTaskRouteSnapshot

    async def run_structured(
        self,
        *,
        prompt: str,
        output_type: type[T],
        system_prompt: str = "",
        validation_context: dict[str, Any] | None = None,
        images: list[StructuredImage] | None = None,
    ) -> T: ...


class RoutedCodexCliStructuredBackend(_BaseCodexCliStructuredBackend):
    """Codex backend that applies the frozen route to every CLI invocation."""

    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str | None = None,
        codex_bin: str | None = None,
    ) -> None:
        super().__init__(codex_bin=codex_bin, model=model)
        self.reasoning_effort = reasoning_effort

    def build_argv(
        self,
        *,
        cwd: str,
        output_path: str,
        schema_path: str | None,
    ) -> list[str]:
        from novelvideo.knowledge_runtime.codex import build_codex_exec_argv

        model = validate_text_task_model_name(self.model)
        argv = build_codex_exec_argv(
            codex_bin=self.codex_bin,
            cwd=cwd,
            output_path=output_path,
            schema_path=schema_path,
            model=model,
        )
        if self.reasoning_effort:
            argv[-1:-1] = [
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
            ]
        return argv


class CodexStructuredRuntime:
    def __init__(
        self,
        snapshot: AgentTaskRouteSnapshot,
        *,
        backend: Any | None = None,
    ) -> None:
        if snapshot.runtime != "codex":
            raise ValueError("CodexStructuredRuntime requires a codex route")
        _reject_unsupported_skill(snapshot)
        if backend is None:
            backend = RoutedCodexCliStructuredBackend(
                model=snapshot.model,
                reasoning_effort=snapshot.reasoning_effort,
            )
        self.snapshot = snapshot
        self._backend = backend

    async def run_structured(
        self,
        *,
        prompt: str,
        output_type: type[T],
        system_prompt: str = "",
        validation_context: dict[str, Any] | None = None,
        images: list[StructuredImage] | None = None,
    ) -> T:
        kwargs = {}
        if images:
            validate_structured_images(images)
            kwargs["images"] = images
        if validation_context is not None:
            kwargs["validation_context"] = validation_context
        return await self._backend.acreate_structured_output(
            prompt, system_prompt, output_type, **kwargs
        )


def _default_model_api_agent_factory(**kwargs: Any) -> Any:
    return Agent(**kwargs)


class ModelApiStructuredRuntime:
    def __init__(
        self,
        snapshot: AgentTaskRouteSnapshot,
        *,
        agent_factory: Callable[..., Any] = _default_model_api_agent_factory,
    ) -> None:
        if snapshot.runtime != "model_api":
            raise ValueError("ModelApiStructuredRuntime requires a model_api route")
        _reject_unsupported_skill(snapshot)
        self.snapshot = snapshot
        self._agent_factory = agent_factory

    async def run_structured(
        self,
        *,
        prompt: str,
        output_type: type[T],
        system_prompt: str = "",
        validation_context: dict[str, Any] | None = None,
        images: list[StructuredImage] | None = None,
    ) -> T:
        from novelvideo.config import get_newapi_text_pydantic_model

        agent_kwargs: dict[str, Any] = {
            "model": get_newapi_text_pydantic_model(
                "TEXT_TASK_RUNTIME_MODEL",
                self.snapshot.model,
                model_name_override=self.snapshot.model,
            ),
            # DeepSeek thinking models reject native tool output because it adds
            # tool_choice. PromptedOutput validates typed JSON without tools.
            "output_type": PromptedOutput(output_type),
            "system_prompt": system_prompt,
        }
        if self.snapshot.reasoning_effort:
            agent_kwargs["model_settings"] = {
                "openai_reasoning_effort": self.snapshot.reasoning_effort
            }
        if validation_context is not None:
            agent_kwargs["validation_context"] = validation_context
        agent = self._agent_factory(**agent_kwargs)
        user_prompt = prompt
        if images:
            validate_structured_images(images)
            user_prompt = [prompt, *[BinaryContent(data=image.data, media_type=image.media_type) for image in images]]
        result = await agent.run(user_prompt)
        output = getattr(result, "output", result)
        if isinstance(output, output_type):
            return output
        return output_type.model_validate(output)


def build_text_task_runtime(snapshot: AgentTaskRouteSnapshot) -> StructuredTextRuntime:
    if snapshot.runtime == "codex":
        return CodexStructuredRuntime(snapshot)
    return ModelApiStructuredRuntime(snapshot)


_CURRENT_TEXT_TASK_RUNTIME: ContextVar[StructuredTextRuntime | None] = ContextVar(
    "current_text_task_runtime",
    default=None,
)


def current_text_task_runtime() -> StructuredTextRuntime | None:
    return _CURRENT_TEXT_TASK_RUNTIME.get()


class StructuredRuntimeAgent:
    """Small Agent-compatible facade for planners that only call ``run``."""

    def __init__(
        self,
        runtime: StructuredTextRuntime,
        *,
        output_type: type[Any],
        system_prompt: str = "",
        validation_context: dict[str, Any] | None = None,
        output_retries: int = 0,
    ) -> None:
        self.runtime = runtime
        self.output_type = output_type
        self.system_prompt = system_prompt
        self.validation_context = validation_context
        self.output_retries = max(0, int(output_retries))
        self.model_name = runtime.snapshot.model

    async def run(self, prompt: str, *, images: list[StructuredImage] | None = None) -> Any:
        frozen_images = tuple(images or ())
        if frozen_images:
            validate_structured_images(list(frozen_images))
        attempt_prompt = prompt
        for attempt in range(self.output_retries + 1):
            output = await self.runtime.run_structured(
                prompt=attempt_prompt,
                output_type=self.output_type,
                system_prompt=self.system_prompt,
                validation_context=self.validation_context,
                **({"images": list(frozen_images)} if frozen_images else {}),
            )
            try:
                if self.validation_context is not None and hasattr(
                    self.output_type, "model_validate"
                ):
                    payload = (
                        output.model_dump() if hasattr(output, "model_dump") else output
                    )
                    output = self.output_type.model_validate(
                        payload,
                        context=self.validation_context,
                    )
            except ValidationError as exc:
                if attempt >= self.output_retries:
                    raise
                error = str(exc)[:3000]
                attempt_prompt = f"""{prompt}

## 上次结构化结果未通过业务校验
{error}

请严格根据原始输入修正上述字段。禁止补写原文中不存在的实体名称，只返回符合既定 Schema 的完整结果。
"""
                continue
            return SimpleNamespace(output=output)
        raise AssertionError("unreachable")


@contextmanager
def text_task_runtime_scope(
    snapshot: AgentTaskRouteSnapshot,
) -> Iterator[StructuredTextRuntime]:
    runtime = build_text_task_runtime(snapshot)
    token = _CURRENT_TEXT_TASK_RUNTIME.set(runtime)
    try:
        yield runtime
    finally:
        _CURRENT_TEXT_TASK_RUNTIME.reset(token)
