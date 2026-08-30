"""Structured text runtimes selected by a frozen task route."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import tempfile
from typing import Any, Protocol, TypeVar

from pydantic_ai import Agent, PromptedOutput

from novelvideo.knowledge_runtime.codex import (
    CodexCliStructuredBackend as _BaseCodexCliStructuredBackend,
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
        self, *, prompt: str, output_type: type[T], system_prompt: str = ""
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

    async def _run_once(
        self, prompt: str, *, schema: dict[str, Any] | None
    ) -> str:
        from novelvideo.knowledge_runtime import codex as codex_runtime
        from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

        with tempfile.TemporaryDirectory(prefix="dramaclaw-text-task-") as raw_dir:
            temp_dir = Path(raw_dir)
            output_path = temp_dir / "result.txt"
            schema_path: Path | None = None
            if schema is not None:
                schema_path = temp_dir / "schema.json"
                schema_path.write_text(
                    json.dumps(
                        codex_runtime.normalize_codex_output_schema(schema),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            argv = self.build_argv(
                cwd=str(temp_dir),
                output_path=str(output_path),
                schema_path=str(schema_path) if schema_path else None,
            )
            process_argv = codex_runtime.normalize_codex_process_argv(argv)
            try:
                process = await codex_runtime._create_codex_process(
                    process_argv,
                    stdin=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise KnowledgeRuntimeError(
                    f"Codex CLI 无法启动: {exc}", code="CODEX_NOT_INSTALLED"
                ) from exc
            try:
                stdout, stderr = await process.communicate(prompt.encode("utf-8"))
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                raise
            if process.returncode != 0:
                message = stderr.decode("utf-8", errors="replace").strip()
                code = (
                    "CODEX_NOT_AUTHENTICATED"
                    if "not logged in" in message.lower()
                    else "CODEX_EXEC_FAILED"
                )
                raise KnowledgeRuntimeError(
                    message[:600] or "Codex 执行失败。", code=code
                )
            if output_path.exists():
                return output_path.read_text(encoding="utf-8")
            return stdout.decode("utf-8", errors="replace")


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
        _reject_unsupported_skill(snapshot)
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
            # DeepSeek thinking models reject native tool output because it adds
            # tool_choice. PromptedOutput validates typed JSON without tools.
            "output_type": PromptedOutput(output_type),
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
