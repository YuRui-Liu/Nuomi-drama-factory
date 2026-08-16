"""Route Cognee structured text calls through the scoped Codex backend."""

from __future__ import annotations

import importlib
from typing import Any

from novelvideo.cognee.concurrency import llm_pipeline_limiter
from novelvideo.knowledge_runtime.context import require_knowledge_runtime_context


def install_codex_llm_gateway_adapter() -> None:
    gateway = importlib.import_module(
        "cognee.infrastructure.llm.LLMGateway"
    ).LLMGateway
    marker = "_novelvideo_codex_adapter_installed"
    if getattr(gateway, marker, False):
        return

    async def routed(
        text_input: str,
        system_prompt: str,
        response_model: type[Any],
        **kwargs: Any,
    ) -> Any:
        context = require_knowledge_runtime_context()
        async with llm_pipeline_limiter:
            return await context.text_backend.acreate_structured_output(
                text_input,
                system_prompt,
                response_model,
                **kwargs,
            )

    gateway.acreate_structured_output = staticmethod(routed)
    setattr(gateway, marker, True)


__all__ = ["install_codex_llm_gateway_adapter"]
