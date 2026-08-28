"""Public, credential-free diagnostics for media task displays."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import JsonValue

from novelvideo.media_capabilities.diagnostic_safety import (
    SIGNED_QUERY_KEYS as _SIGNED_QUERY_KEYS,
    contains_sensitive_value,
    is_sensitive_key,
)


PUBLIC_DIAGNOSTIC_FIELDS: Final[tuple[str, ...]] = (
    "provider",
    "channel",
    "logical_model",
    "resolved_model",
    "mode",
    "aspect",
    "duration",
    "quality",
    "workflow_id",
    "workflow_version",
    "reference_summary",
    "upstream_task_id",
    "status",
    "retries",
    "fallback_reason",
)

_ABSOLUTE_PATH_TOKEN = re.compile(
    r"(?<![\w./\\])(?:[A-Za-z]:[\\/]|[\\/]{2}|/)"
    r"[^\s'\"<>()\[\]{},;!?，。；：！？]+"
)


def _sanitize_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED]"
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = parsed.hostname
    if not hostname:
        return "[REDACTED]"
    try:
        port = parsed.port
    except ValueError:
        return "[REDACTED]"
    safe_host = f"[{hostname}]" if ":" in hostname else hostname
    safe_netloc = f"{safe_host}:{port}" if port is not None else safe_host
    if contains_sensitive_value(unquote(parsed.path), inspect_urls=False):
        return "[REDACTED]"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    unsafe_query = any(
        key.casefold().startswith(("x-amz-", "x-goog-"))
        or re.sub(r"[^a-z0-9]", "", key.casefold()) in _SIGNED_QUERY_KEYS
        or is_sensitive_key(key)
        or contains_sensitive_value(query_value, inspect_urls=False)
        for key, query_value in query
    )
    if unsafe_query:
        return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", ""))
    fragment = parsed.fragment
    fragment_pairs = parse_qsl(fragment, keep_blank_values=True)
    if contains_sensitive_value(fragment, inspect_urls=False) or any(
        is_sensitive_key(key) for key, _ in fragment_pairs
    ):
        fragment = ""
    return urlunsplit(
        (parsed.scheme, safe_netloc, parsed.path, urlencode(query), fragment)
    )


def _sanitize_string(value: str) -> str:
    url = _sanitize_url(value)
    if url is not None:
        return url
    if contains_sensitive_value(value) or _ABSOLUTE_PATH_TOKEN.search(value):
        return "[REDACTED]"
    return value


_REDACTED: Final = "[REDACTED]"
_MAX_PUBLIC_DEPTH: Final = 16
_MAX_PUBLIC_NODES: Final = 512


@dataclass
class _TraversalBudget:
    nodes: int = 0
    exceeded: bool = False
    visited: set[int] = field(default_factory=set)


def _sanitize_value(
    value: Any,
    *,
    key: object | None = None,
    depth: int = 0,
    budget: _TraversalBudget,
) -> JsonValue:
    budget.nodes += 1
    if budget.nodes > _MAX_PUBLIC_NODES:
        budget.exceeded = True
        return _REDACTED
    if depth > _MAX_PUBLIC_DEPTH:
        return _REDACTED
    if key is not None and is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in budget.visited:
            return _REDACTED
        budget.visited.add(identity)
        sanitized: dict[str, JsonValue] = {}
        for item_key, item_value in value.items():
            item_key_text = str(item_key)
            if contains_sensitive_value(item_key_text, inspect_urls=False):
                sanitized[_REDACTED] = _REDACTED
                continue
            sanitized[item_key_text] = _sanitize_value(
                item_value,
                key=item_key,
                depth=depth + 1,
                budget=budget,
            )
            if budget.exceeded:
                return _REDACTED
        return sanitized
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in budget.visited:
            return _REDACTED
        budget.visited.add(identity)
        sanitized_items: list[JsonValue] = []
        for item in value:
            sanitized_items.append(
                _sanitize_value(item, depth=depth + 1, budget=budget)
            )
            if budget.exceeded:
                return _REDACTED
        return sanitized_items
    if isinstance(value, str):
        return _sanitize_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_string(str(value))


def _sanitize_public_field(value: Any, *, key: str) -> JsonValue:
    budget = _TraversalBudget()
    sanitized = _sanitize_value(value, key=key, budget=budget)
    return _REDACTED if budget.exceeded else sanitized


def sanitize_diagnostics(diagnostics: Mapping[str, Any] | None) -> dict[str, JsonValue]:
    """Project diagnostics onto the stable public allowlist and redact values."""
    if not isinstance(diagnostics, Mapping):
        return {}
    return {
        field: _sanitize_public_field(diagnostics[field], key=field)
        for field in PUBLIC_DIAGNOSTIC_FIELDS
        if field in diagnostics
    }


__all__ = [
    "PUBLIC_DIAGNOSTIC_FIELDS",
    "contains_sensitive_value",
    "is_sensitive_key",
    "sanitize_diagnostics",
]
