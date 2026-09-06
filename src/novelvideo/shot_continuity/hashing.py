"""Deterministic JSON serialization and hashing helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value with a stable representation."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of the canonical UTF-8 JSON representation."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
