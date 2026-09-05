from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from novelvideo.knowledge_runtime import OllamaSettings
from novelvideo.text_runtime_settings import TextRuntimeSettings


def test_init_cognee_uses_text_runtime_without_replacing_embedding_gateway(monkeypatch) -> None:
    from novelvideo.cognee import config

    text_settings = TextRuntimeSettings(
        source="database",
        provider="deepseek",
        base_url="https://deepseek.example/v1",
        api_key="deepseek-key",
        model="deepseek-model",
    )
    embedding_settings = OllamaSettings(
        base_url="http://127.0.0.1:11434",
        model="bge-m3:latest",
        dimension=1024,
        digest="sha256:def",
        batch_size=8,
        probed_at="now",
    )
    fake_config = SimpleNamespace()
    monkeypatch.setattr(config, "COGNEE_AVAILABLE", True)
    monkeypatch.setattr(config, "cognee", SimpleNamespace(config=fake_config))
    monkeypatch.setattr(config, "load_text_runtime_settings", lambda: text_settings, raising=False)
    monkeypatch.setattr(config, "load_knowledge_runtime_settings", lambda: embedding_settings)
    monkeypatch.setattr(config, "install_codex_llm_gateway_adapter", lambda: None)
    monkeypatch.setattr(config, "_apply_cognee_runtime_defaults", lambda: None)
    monkeypatch.setattr(config, "_patch_cognee_embedding_timeout", lambda: None)
    monkeypatch.setattr(config, "_install_insufficient_credits_log_filter", lambda: None)
    monkeypatch.setattr(config, "_install_cognee_pipeline_concurrency", lambda: None)
    monkeypatch.setattr(config, "_clear_cognee_embedding_and_vector_engine_caches", lambda: None)

    config.init_cognee()

    assert os.environ["LLM_ENDPOINT"] == text_settings.base_url
    assert os.environ["LLM_API_KEY"] == text_settings.api_key
    assert os.environ["LLM_MODEL"] == f"openai/{text_settings.model}"
    assert os.environ["EMBEDDING_ENDPOINT"] == "http://127.0.0.1:11434/api/embed"
    assert os.environ["EMBEDDING_MODEL"] == embedding_settings.model
    assert os.environ.get("EMBEDDING_API_KEY", "") != text_settings.api_key


def test_project_storage_uses_configured_ascii_cognee_root(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.cognee import config

    state_dir = tmp_path / "项目状态"
    cognee_root = tmp_path / "cognee-ascii"
    seen: dict[str, str] = {}
    fake_config = SimpleNamespace(
        system_root_directory=lambda value: seen.__setitem__("system", value),
        data_root_directory=lambda value: seen.__setitem__("data", value),
    )
    monkeypatch.setenv("NOVELVIDEO_COGNEE_ROOT", str(cognee_root))
    monkeypatch.setattr(config, "_apply_cognee_runtime_defaults", lambda: None)

    system_dir, data_dir = config.apply_cognee_project_storage_context(
        state_dir,
        SimpleNamespace(config=fake_config),
    )

    assert Path(system_dir).parent == Path(data_dir).parent
    assert Path(system_dir).parent.parent == cognee_root
    assert Path(system_dir).name == "cognee_system"
    assert Path(data_dir).name == "cognee_data"
    assert seen == {"system": system_dir, "data": data_dir}


def test_apply_ollama_embedding_env_uses_native_provider(monkeypatch) -> None:
    from novelvideo.cognee import config

    monkeypatch.setenv("EMBEDDING_API_KEY", "old-key")
    monkeypatch.setenv("NEWAPI_API_KEY", "old-newapi-key")
    cleared: list[bool] = []
    monkeypatch.setattr(
        config,
        "_clear_cognee_embedding_and_vector_engine_caches",
        lambda: cleared.append(True),
        raising=False,
    )
    settings = OllamaSettings(
        base_url="http://127.0.0.1:11434",
        model="bge-m3:latest",
        dimension=1024,
        digest="sha256:def",
        batch_size=6,
        probed_at="now",
    )

    result = config._apply_ollama_embedding_env(settings)

    assert result == ("ollama", "bge-m3:latest", "1024", "")
    assert os.environ["EMBEDDING_PROVIDER"] == "ollama"
    assert os.environ["EMBEDDING_ENDPOINT"] == "http://127.0.0.1:11434/api/embed"
    assert os.environ["EMBEDDING_BATCH_SIZE"] == "6"
    assert os.environ["HUGGINGFACE_TOKENIZER"] == "BAAI/bge-m3"
    assert "EMBEDDING_API_KEY" not in os.environ
    assert cleared == [True]


def test_apply_ollama_embedding_env_binds_qwen3_tokenizer(monkeypatch) -> None:
    from novelvideo.cognee import config

    monkeypatch.delenv("HUGGINGFACE_TOKENIZER", raising=False)
    settings = OllamaSettings(
        base_url="http://127.0.0.1:11434",
        model="qwen3-embedding:0.6b",
        dimension=1024,
        digest="sha256:qwen",
        batch_size=8,
        probed_at="now",
    )

    config._apply_ollama_embedding_env(settings)

    assert os.environ["HUGGINGFACE_TOKENIZER"] == "Qwen/Qwen3-Embedding-0.6B"


def test_ollama_embedding_tokenizer_is_offline_and_does_not_load_huggingface() -> None:
    from novelvideo.cognee import config

    config._install_cognee_ollama_offline_tokenizer()

    from cognee.infrastructure.databases.vector.embeddings.OllamaEmbeddingEngine import (
        OllamaEmbeddingEngine,
    )

    engine = object.__new__(OllamaEmbeddingEngine)
    engine.max_completion_tokens = 512
    tokenizer = engine.get_tokenizer()

    assert type(tokenizer).__name__ == "TikTokenTokenizer"
    assert tokenizer.count_tokens("本地 Ollama 不应访问 Hugging Face") > 0


def test_init_cognee_configures_native_ollama_without_newapi(monkeypatch) -> None:
    from novelvideo.cognee import config

    settings = OllamaSettings(
        base_url="http://127.0.0.1:11434",
        model="bge-m3:latest",
        dimension=1024,
        digest="sha256:def",
        batch_size=8,
        probed_at="now",
    )
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
    monkeypatch.setattr(config, "install_codex_llm_gateway_adapter", lambda: None)
    monkeypatch.setattr(config, "load_knowledge_runtime_settings", lambda: settings, raising=False)
    monkeypatch.setattr(config, "_resolve_llm_provider", lambda: "newapi")
    monkeypatch.setattr(config, "_resolve_llm_api_key", lambda *_args: "")
    monkeypatch.setattr(config, "_apply_cognee_runtime_defaults", lambda: None)
    monkeypatch.setattr(config, "_patch_cognee_embedding_timeout", lambda: None)
    monkeypatch.setattr(config, "_install_insufficient_credits_log_filter", lambda: None)
    monkeypatch.setattr(config, "_patch_cognee_embedding_gateway", lambda: None)
    monkeypatch.setattr(config, "_install_cognee_pipeline_concurrency", lambda: None)
    monkeypatch.setattr(config, "_clear_cognee_embedding_and_vector_engine_caches", lambda: None, raising=False)

    config.init_cognee()

    assert os.environ["EMBEDDING_PROVIDER"] == "ollama"
    assert os.environ["EMBEDDING_MODEL"] == settings.model
    assert os.environ["EMBEDDING_DIMENSIONS"] == "1024"
    assert os.environ["EMBEDDING_ENDPOINT"] == "http://127.0.0.1:11434/api/embed"
