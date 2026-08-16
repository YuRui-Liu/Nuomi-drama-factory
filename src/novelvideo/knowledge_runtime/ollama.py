"""Minimal Ollama model discovery and embedding probe client."""

from __future__ import annotations

from datetime import datetime, timezone
from numbers import Real
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .settings import KnowledgeRuntimeError, OllamaProbeResult

_MODEL_FIELDS = {"name", "model", "modified_at", "size", "digest"}
_DETAIL_FIELDS = {
    "format",
    "family",
    "families",
    "parameter_size",
    "quantization_level",
    "parent_model",
}


def normalize_ollama_base_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise KnowledgeRuntimeError(
            "Ollama base URL must use http or https.",
            code="OLLAMA_INVALID_BASE_URL",
        )
    path = parsed.path.rstrip("/")
    for endpoint in ("/api/embed", "/api/tags"):
        if path.endswith(endpoint):
            path = path[: -len(endpoint)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def _safe_model(model: dict[str, Any]) -> dict[str, Any]:
    safe = {key: model[key] for key in _MODEL_FIELDS if key in model}
    details = model.get("details")
    if isinstance(details, dict):
        safe_details = {key: details[key] for key in _DETAIL_FIELDS if key in details}
        if safe_details:
            safe["details"] = safe_details
    return safe


async def _request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.RequestError as exc:
        raise KnowledgeRuntimeError(
            "Ollama service is unreachable.", code="OLLAMA_UNREACHABLE"
        ) from exc
    if not response.is_success:
        raise KnowledgeRuntimeError(
            f"Ollama returned HTTP {response.status_code}.", code="OLLAMA_HTTP_ERROR"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise KnowledgeRuntimeError(
            "Ollama returned invalid JSON.", code="OLLAMA_INVALID_JSON"
        ) from exc


async def list_ollama_models(
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    root = normalize_ollama_base_url(base_url)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        payload = await _request_json(http, "GET", f"{root}/api/tags")
    finally:
        if owns_client:
            await http.aclose()
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise KnowledgeRuntimeError(
            "Ollama model list has an invalid shape.", code="OLLAMA_INVALID_RESPONSE"
        )
    return [_safe_model(model) for model in payload["models"] if isinstance(model, dict)]


def _validate_embeddings(payload: Any) -> list[float]:
    if not isinstance(payload, dict) or not isinstance(payload.get("embeddings"), list):
        raise KnowledgeRuntimeError(
            "Ollama embedding response has an invalid shape.",
            code="OLLAMA_INVALID_RESPONSE",
        )
    embeddings = payload["embeddings"]
    if not embeddings:
        raise KnowledgeRuntimeError(
            "Ollama returned no embedding vector.", code="OLLAMA_EMPTY_EMBEDDING"
        )
    dimensions = {
        len(vector) for vector in embeddings if isinstance(vector, list) and vector
    }
    if len(dimensions) > 1:
        raise KnowledgeRuntimeError(
            "Ollama returned inconsistent embedding dimensions.",
            code="OLLAMA_DIMENSION_MISMATCH",
        )
    if len(embeddings) != 1:
        raise KnowledgeRuntimeError(
            "Ollama returned the wrong number of embedding vectors.",
            code="OLLAMA_EMBEDDING_COUNT_MISMATCH",
        )
    vector = embeddings[0]
    if (
        not isinstance(vector, list)
        or not vector
        or any(not isinstance(value, Real) or isinstance(value, bool) for value in vector)
    ):
        raise KnowledgeRuntimeError(
            "Ollama returned an invalid embedding vector.",
            code="OLLAMA_EMPTY_EMBEDDING",
        )
    return vector


async def probe_ollama_embedding(
    base_url: str,
    model: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> OllamaProbeResult:
    root = normalize_ollama_base_url(base_url)
    selected_model = str(model or "").strip()
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        models = await list_ollama_models(root, client=http)
        selected = next(
            (
                item
                for item in models
                if selected_model
                in {str(item.get("name") or ""), str(item.get("model") or "")}
            ),
            None,
        )
        if selected is None:
            raise KnowledgeRuntimeError(
                "The selected Ollama model is not installed.",
                code="OLLAMA_MODEL_NOT_FOUND",
            )
        payload = await _request_json(
            http,
            "POST",
            f"{root}/api/embed",
            json={"model": selected_model, "input": ["dimension probe"]},
        )
        vector = _validate_embeddings(payload)
    finally:
        if owns_client:
            await http.aclose()
    return OllamaProbeResult(
        model=selected_model,
        dimension=len(vector),
        digest=str(selected.get("digest") or ""),
        probed_at=datetime.now(timezone.utc).isoformat(),
    )
