"""Structured text runtimes selected by a frozen task route."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar

from pydantic_ai import Agent

from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot

T = TypeVar("T")


class StructuredTextRuntime(Protocol):
    snapshot: AgentTaskRouteSnapshot

    async def run_structured(
        self, *, prompt: str, output_type: type[T], system_prompt: str = ""
    ) -> T: ...


class CodexStructuredRuntime:
    def __init__(
        self,
        snapshot: AgentTaskRouteSnapshot,
        *,
        backend: Any | None = None,
    ) -> None:
        if snapshot.runtime != "codex":
            raise ValueError("CodexStructuredRuntime requires a codex route")
        if backend is None:
            from novelvideo.knowledge_runtime.codex import CodexCliStructuredBackend

            backend = CodexCliStructuredBackend(model=snapshot.model)
        self.snapshot = snapshot
        self._backend = backend

    async def run_structured(
        self, *, prompt: str, output_type: type[T], system_prompt: str = ""
    ) -> T:
        return await self._backend.acreate_structured_output(
            prompt,
            system_prompt,
            output_type,
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
        self.snapshot = snapshot
        self._agent_factory = agent_factory

    async def run_structured(
        self, *, prompt: str, output_type: type[T], system_prompt: str = ""
    ) -> T:
        from novelvideo.config import get_newapi_text_pydantic_model

        agent_kwargs: dict[str, Any] = {
            "model": get_newapi_text_pydantic_model(
                "TEXT_TASK_RUNTIME_MODEL",
                self.snapshot.model,
                model_name_override=self.snapshot.model,
            ),
            "output_type": output_type,
            "system_prompt": system_prompt,
        }
        if self.snapshot.reasoning_effort:
            agent_kwargs["model_settings"] = {
                "openai_reasoning_effort": self.snapshot.reasoning_effort
            }
        agent = self._agent_factory(**agent_kwargs)
        result = await agent.run(prompt)
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

