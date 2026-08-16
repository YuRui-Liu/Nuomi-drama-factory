from __future__ import annotations

import httpx
import pytest
import respx

from novelvideo.knowledge_runtime import (
    KnowledgeRuntimeError,
    list_ollama_models,
    normalize_ollama_base_url,
    probe_ollama_embedding,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("https://ollama.example/api/tags", "https://ollama.example"),
        ("https://ollama.example/prefix/api/embed/", "https://ollama.example/prefix"),
    ],
)
def test_normalize_ollama_base_url(raw: str, expected: str) -> None:
    assert normalize_ollama_base_url(raw) == expected


def test_normalize_ollama_base_url_rejects_non_http_scheme() -> None:
    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        normalize_ollama_base_url("file:///tmp/ollama")
    assert exc_info.value.code == "OLLAMA_INVALID_BASE_URL"


@pytest.mark.asyncio
async def test_list_ollama_models_returns_non_sensitive_metadata() -> None:
    payload = {
        "models": [
            {
                "name": "nomic-embed-text:latest",
                "model": "nomic-embed-text:latest",
                "modified_at": "2026-08-15T00:00:00Z",
                "size": 274302450,
                "digest": "sha256:abc123",
                "details": {"family": "nomic-bert", "parameter_size": "137M"},
                "secret": "must-not-leak",
            }
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.get("http://ollama.test:11434/api/tags").mock(
            return_value=httpx.Response(200, json=payload)
        )
        models = await list_ollama_models("http://ollama.test:11434/api/tags")

    assert models == [
        {
            "name": "nomic-embed-text:latest",
            "model": "nomic-embed-text:latest",
            "modified_at": "2026-08-15T00:00:00Z",
            "size": 274302450,
            "digest": "sha256:abc123",
            "details": {"family": "nomic-bert", "parameter_size": "137M"},
        }
    ]


@pytest.mark.asyncio
async def test_probe_ollama_embedding_returns_dimension_and_digest() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("http://ollama.test:11434/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "nomic-embed-text:latest",
                            "digest": "sha256:abc123",
                            "size": 1,
                        }
                    ]
                },
            )
        )
        embed_route = router.post("http://ollama.test:11434/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
        )
        result = await probe_ollama_embedding(
            "http://ollama.test:11434", "nomic-embed-text:latest"
        )

    assert embed_route.calls[0].request.content == (
        b'{"model":"nomic-embed-text:latest","input":["dimension probe"]}'
    )
    assert result.model == "nomic-embed-text:latest"
    assert result.dimension == 3
    assert result.digest == "sha256:abc123"
    assert result.probed_at.endswith("+00:00")


@pytest.mark.asyncio
async def test_probe_rejects_missing_model() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("http://ollama.test:11434/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        with pytest.raises(KnowledgeRuntimeError) as exc_info:
            await probe_ollama_embedding("http://ollama.test:11434", "missing")

    assert exc_info.value.code == "OLLAMA_MODEL_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"embeddings": []}, "OLLAMA_EMPTY_EMBEDDING"),
        ({"embeddings": [[0.1], [0.2]]}, "OLLAMA_EMBEDDING_COUNT_MISMATCH"),
        ({"embeddings": [[0.1, 0.2], [0.3]]}, "OLLAMA_DIMENSION_MISMATCH"),
    ],
)
async def test_probe_rejects_invalid_embedding_vectors(payload, code: str) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("http://ollama.test:11434/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={"models": [{"name": "embed", "digest": "sha256:d"}]},
            )
        )
        router.post("http://ollama.test:11434/api/embed").mock(
            return_value=httpx.Response(200, json=payload)
        )
        with pytest.raises(KnowledgeRuntimeError) as exc_info:
            await probe_ollama_embedding("http://ollama.test:11434", "embed")

    assert exc_info.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(503, text="down"), "OLLAMA_HTTP_ERROR"),
        (httpx.Response(200, content=b"not-json"), "OLLAMA_INVALID_JSON"),
    ],
)
async def test_list_models_maps_http_and_json_errors(response, code: str) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("http://ollama.test:11434/api/tags").mock(return_value=response)
        with pytest.raises(KnowledgeRuntimeError) as exc_info:
            await list_ollama_models("http://ollama.test:11434")

    assert exc_info.value.code == code


@pytest.mark.asyncio
async def test_list_models_maps_unreachable_service() -> None:
    request = httpx.Request("GET", "http://ollama.test:11434/api/tags")
    with respx.mock(assert_all_called=True) as router:
        router.get("http://ollama.test:11434/api/tags").mock(
            side_effect=httpx.ConnectError("unreachable", request=request)
        )
        with pytest.raises(KnowledgeRuntimeError) as exc_info:
            await list_ollama_models("http://ollama.test:11434")

    assert exc_info.value.code == "OLLAMA_UNREACHABLE"
