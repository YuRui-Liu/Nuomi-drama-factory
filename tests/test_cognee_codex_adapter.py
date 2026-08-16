from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from novelvideo.knowledge_runtime.context import (
    KnowledgeRuntimeContext,
    knowledge_runtime_scope,
)
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError, OllamaSettings


class Extracted(BaseModel):
    name: str


class FakeBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def acreate_structured_output(self, *_args, **_kwargs):
        self.calls += 1
        return Extracted(name="林默")


@pytest.mark.asyncio
async def test_gateway_uses_scoped_codex_backend(monkeypatch) -> None:
    from novelvideo.cognee import codex_adapter

    class FakeGateway:
        @staticmethod
        async def acreate_structured_output(*_args, **_kwargs):
            raise AssertionError("original gateway should not run")

    monkeypatch.setattr(
        codex_adapter.importlib,
        "import_module",
        lambda _name: SimpleNamespace(LLMGateway=FakeGateway),
    )
    backend = FakeBackend()
    context = KnowledgeRuntimeContext(
        username="owner",
        project_name="demo",
        state_dir=Path("."),
        text_backend=backend,
        embedding=OllamaSettings(model="bge-m3", dimension=1024),
    )

    codex_adapter.install_codex_llm_gateway_adapter()
    with knowledge_runtime_scope(context):
        result = await FakeGateway.acreate_structured_output("正文", "提取", Extracted)

    assert result.name == "林默"
    assert backend.calls == 1


@pytest.mark.asyncio
async def test_gateway_without_scope_fails_closed(monkeypatch) -> None:
    from novelvideo.cognee import codex_adapter

    class FakeGateway:
        @staticmethod
        async def acreate_structured_output(*_args, **_kwargs):
            raise AssertionError("original gateway should not run")

    monkeypatch.setattr(
        codex_adapter.importlib,
        "import_module",
        lambda _name: SimpleNamespace(LLMGateway=FakeGateway),
    )
    codex_adapter.install_codex_llm_gateway_adapter()

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await FakeGateway.acreate_structured_output("正文", "提取", Extracted)
    assert exc_info.value.code == "KNOWLEDGE_RUNTIME_CONTEXT_MISSING"


def test_init_cognee_no_longer_requires_newapi_key(monkeypatch) -> None:
    from novelvideo.cognee import config

    fake_config = SimpleNamespace(
        set_llm_provider=lambda _value: None,
        set_llm_model=lambda _value: None,
        set_llm_api_key=lambda _value: None,
        set_embedding_provider=lambda _value: None,
        set_embedding_model=lambda _value: None,
        set_embedding_dimensions=lambda _value: None,
        set_embedding_api_key=lambda _value: None,
    )
    monkeypatch.delenv("NEWAPI_API_KEY", raising=False)
    monkeypatch.setattr(config, "COGNEE_AVAILABLE", True)
    monkeypatch.setattr(config, "cognee", SimpleNamespace(config=fake_config))
    monkeypatch.setattr(config, "cognee_gateway_restart_required", lambda: False)
    monkeypatch.setattr(config, "install_codex_llm_gateway_adapter", lambda: None)
    monkeypatch.setattr(config, "_resolve_llm_provider", lambda: "newapi")
    monkeypatch.setattr(config, "_resolve_llm_api_key", lambda *_args: "")
    monkeypatch.setattr(
        config,
        "load_knowledge_runtime_settings",
        lambda: OllamaSettings(model="bge-m3", dimension=1024),
    )
    monkeypatch.setattr(
        config,
        "_apply_ollama_embedding_env",
        lambda *_args: ("ollama", "bge-m3", "1024", ""),
    )
    monkeypatch.setattr(config, "_apply_cognee_runtime_defaults", lambda: None)
    monkeypatch.setattr(config, "_patch_cognee_embedding_timeout", lambda: None)
    monkeypatch.setattr(config, "_install_insufficient_credits_log_filter", lambda: None)
    monkeypatch.setattr(config, "_patch_cognee_embedding_gateway", lambda: None)
    monkeypatch.setattr(config, "_install_cognee_pipeline_concurrency", lambda: None)

    config.init_cognee()

    assert config.os.environ["LLM_PROVIDER"] == "custom"
    assert config.os.environ["LLM_MODEL"] == "codex-cli"
