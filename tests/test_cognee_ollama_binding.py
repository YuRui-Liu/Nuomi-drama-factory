from __future__ import annotations

import json

import pytest

from novelvideo.embedding_models import EmbeddingModelSpec
from novelvideo.knowledge_runtime import OllamaSettings
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.project_config import (
    inspect_cognee_embedding_binding_in_state_dir,
    prepare_cognee_embedding_binding_in_state_dir,
)


def current_ollama() -> OllamaSettings:
    return OllamaSettings(
        model="bge-m3:latest",
        dimension=1024,
        digest="sha256:def",
        batch_size=8,
        probed_at="now",
    )


def test_ollama_binding_has_full_stable_fingerprint() -> None:
    binding = EmbeddingModelSpec.ollama(
        model="bge-m3:latest",
        dimensions=1024,
        digest="sha256:def",
        generation=2,
    )

    assert binding.fingerprint == "ollama|bge-m3:latest|1024|sha256:def|2"


def test_legacy_newapi_binding_requires_explicit_rebuild(tmp_path) -> None:
    (tmp_path / "project_config.json").write_text(
        json.dumps(
            {
                "cognee_embedding_model": "DC-cognee-embedding-v2",
                "cognee_embedding_dimension": 1024,
            }
        ),
        encoding="utf-8",
    )

    status = inspect_cognee_embedding_binding_in_state_dir(tmp_path, current_ollama())

    assert status.ready is False
    assert status.rebuild_required is True
    assert status.reason == "legacy_newapi_binding"


def test_matching_ollama_binding_is_ready(tmp_path) -> None:
    (tmp_path / "project_config.json").write_text(
        json.dumps(
            {
                "cognee_embedding_binding": {
                    "provider": "ollama",
                    "model": "bge-m3:latest",
                    "dimension": 1024,
                    "digest": "sha256:def",
                    "generation": 2,
                }
            }
        ),
        encoding="utf-8",
    )

    status = inspect_cognee_embedding_binding_in_state_dir(tmp_path, current_ollama())

    assert status.ready is True
    assert status.rebuild_required is False
    assert status.binding is not None
    assert status.binding.fingerprint == "ollama|bge-m3:latest|1024|sha256:def|2"


def test_legacy_binding_requires_explicit_rebuild(tmp_path) -> None:
    (tmp_path / "project_config.json").write_text(
        json.dumps(
            {
                "cognee_embedding_model": "DC-cognee-embedding-v2",
                "cognee_embedding_dimension": 1024,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        prepare_cognee_embedding_binding_in_state_dir(
            tmp_path,
            current_ollama(),
            rebuild=False,
        )

    assert exc_info.value.code == "KNOWLEDGE_INDEX_REBUILD_REQUIRED"


def test_unbound_empty_project_is_bound_immediately(tmp_path) -> None:
    binding = prepare_cognee_embedding_binding_in_state_dir(
        tmp_path,
        current_ollama(),
        rebuild=False,
    )

    status = inspect_cognee_embedding_binding_in_state_dir(tmp_path, current_ollama())
    assert status.ready is True
    assert status.binding == binding


def test_rebuild_allows_legacy_binding_without_committing_early(tmp_path) -> None:
    config_path = tmp_path / "project_config.json"
    original = {
        "cognee_embedding_model": "DC-cognee-embedding-v2",
        "cognee_embedding_dimension": 1024,
    }
    config_path.write_text(json.dumps(original), encoding="utf-8")

    binding = prepare_cognee_embedding_binding_in_state_dir(
        tmp_path,
        current_ollama(),
        rebuild=True,
    )

    assert binding.provider == "ollama"
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
