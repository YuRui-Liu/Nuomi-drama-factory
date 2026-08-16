"""Public API for local knowledge-runtime configuration and probing."""

from .ollama import (
    list_ollama_models,
    normalize_ollama_base_url,
    probe_ollama_embedding,
)
from .codex import (
    CodexCliStatus,
    CodexCliStructuredBackend,
    get_codex_cli_status,
)
from .settings import (
    KnowledgeRuntimeError,
    OllamaProbeResult,
    OllamaSettings,
    load_knowledge_runtime_settings,
    save_knowledge_runtime_settings,
)
from .context import (
    KnowledgeRuntimeContext,
    build_project_knowledge_runtime,
    knowledge_runtime_scope,
    require_knowledge_runtime_context,
)

__all__ = [
    "KnowledgeRuntimeError",
    "KnowledgeRuntimeContext",
    "CodexCliStatus",
    "CodexCliStructuredBackend",
    "OllamaProbeResult",
    "OllamaSettings",
    "list_ollama_models",
    "get_codex_cli_status",
    "build_project_knowledge_runtime",
    "knowledge_runtime_scope",
    "load_knowledge_runtime_settings",
    "normalize_ollama_base_url",
    "probe_ollama_embedding",
    "require_knowledge_runtime_context",
    "save_knowledge_runtime_settings",
]
