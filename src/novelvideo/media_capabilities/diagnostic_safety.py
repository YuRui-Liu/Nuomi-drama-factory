"""Shared conservative secret detection for media diagnostics persistence and display."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit


SENSITIVE_KEY_PARTS = frozenset(
    {
        "auth",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "passwd",
        "password",
        "secret",
        "signature",
        "token",
    }
)
SENSITIVE_COMPACT_KEYS = frozenset(
    {
        "apikey",
        "apisecret",
        "accesstoken",
        "authkey",
        "bearertoken",
        "clientsecret",
        "privatekey",
        "refreshtoken",
        "secretkey",
    }
)
SIGNED_QUERY_KEYS = frozenset(
    {"awsaccesskeyid", "googleaccessid", "policy", "sig", "signature"}
)

_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{0,63})\s*[=:]\s*\S+"
)
_AUTH_VALUE = re.compile(
    r"(?i)(?:\bauthorization\s*:\s*)?\b(?:bearer|basic)\s+\S+"
)
_COOKIE_HEADER = re.compile(r"(?im)^\s*(?:set-cookie|cookie)\s*:\s*\S+")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_URL = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def normalize_sensitive_key(key: object) -> tuple[str, tuple[str, ...], str]:
    text = str(key)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    normalized = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    segments = tuple(segment for segment in normalized.split("_") if segment)
    return normalized, segments, "".join(segments)


def is_sensitive_key(key: object) -> bool:
    normalized, segments, compact = normalize_sensitive_key(key)
    pairs = set(zip(segments, segments[1:]))
    return bool(
        SENSITIVE_KEY_PARTS.intersection(segments)
        or normalized in SENSITIVE_KEY_PARTS
        or compact in SENSITIVE_COMPACT_KEYS
        or pairs.intersection({("api", "key"), ("private", "key")})
    )


def _url_contains_sensitive_value(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            return True
        if contains_sensitive_value(unquote(parsed.path), inspect_urls=False):
            return True
        for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
            if contains_sensitive_value(query_value, inspect_urls=False):
                return True
            compact = re.sub(r"[^a-z0-9]", "", key.casefold())
            if (
                key.casefold().startswith(("x-amz-", "x-goog-"))
                or compact in SIGNED_QUERY_KEYS
                or is_sensitive_key(key)
            ):
                return True
        for key, _ in parse_qsl(parsed.fragment, keep_blank_values=True):
            if is_sensitive_key(key):
                return True
    except ValueError:
        return True
    return False


def contains_sensitive_value(value: str, *, inspect_urls: bool = True) -> bool:
    """Return whether text contains a credential-like value without echoing it."""
    if _AUTH_VALUE.search(value) or _COOKIE_HEADER.search(value) or _JWT.search(value):
        return True
    if any(is_sensitive_key(match.group(1)) for match in _ASSIGNMENT.finditer(value)):
        return True
    if inspect_urls and any(
        _url_contains_sensitive_value(match.group(0)) for match in _URL.finditer(value)
    ):
        return True
    return False


__all__ = [
    "SENSITIVE_COMPACT_KEYS",
    "SENSITIVE_KEY_PARTS",
    "SIGNED_QUERY_KEYS",
    "contains_sensitive_value",
    "is_sensitive_key",
    "normalize_sensitive_key",
]
