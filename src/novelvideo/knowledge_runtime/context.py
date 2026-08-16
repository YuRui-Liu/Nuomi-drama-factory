"""Project-scoped knowledge runtime routing."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol

from .codex import CodexCliStructuredBackend
from .settings import (
    KnowledgeRuntimeError,
    OllamaSettings,
    load_knowledge_runtime_settings,
)


class StructuredTextBackend(Protocol):
    async def acreate_structured_output(
        self,
        text_input: str,
        system_prompt: str,
        response_model: type[Any],
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True)
class KnowledgeRuntimeContext:
    username: str
    project_name: str
    state_dir: Path
    text_backend: StructuredTextBackend
    embedding: OllamaSettings


_CURRENT_KNOWLEDGE_RUNTIME: contextvars.ContextVar[
    KnowledgeRuntimeContext | None
] = contextvars.ContextVar("novelvideo_knowledge_runtime", default=None)


@contextmanager
def knowledge_runtime_scope(
    context: KnowledgeRuntimeContext,
) -> Iterator[KnowledgeRuntimeContext]:
    token = _CURRENT_KNOWLEDGE_RUNTIME.set(context)
    try:
        yield context
    finally:
        _CURRENT_KNOWLEDGE_RUNTIME.reset(token)


def require_knowledge_runtime_context() -> KnowledgeRuntimeContext:
    context = _CURRENT_KNOWLEDGE_RUNTIME.get()
    if context is None:
        raise KnowledgeRuntimeError(
            "Knowledge runtime context is missing.",
            code="KNOWLEDGE_RUNTIME_CONTEXT_MISSING",
        )
    return context


def build_project_knowledge_runtime(project_context: Any) -> KnowledgeRuntimeContext:
    settings = load_knowledge_runtime_settings()
    if not settings.model or settings.dimension <= 0:
        raise KnowledgeRuntimeError(
            "请先在设置中选择并测试 Ollama Embedding 模型。",
            code="OLLAMA_PROBE_REQUIRED",
        )
    return KnowledgeRuntimeContext(
        username=str(project_context.owner_username),
        project_name=str(project_context.project_name),
        state_dir=Path(project_context.state_dir),
        text_backend=CodexCliStructuredBackend(),
        embedding=settings,
    )


__all__ = [
    "KnowledgeRuntimeContext",
    "StructuredTextBackend",
    "build_project_knowledge_runtime",
    "knowledge_runtime_scope",
    "require_knowledge_runtime_context",
]
