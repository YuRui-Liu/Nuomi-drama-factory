"""Validated schema for extension style catalog entries."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

FRAGMENT_KEYS = (
    "medium", "rendering", "lighting", "color", "camera", "constraints",
)
ALLOWED_CATEGORIES = frozenset(
    {"2d", "3d", "realistic", "chinese", "experimental"}
)

# Terms are grouped by story dimension so this policy remains easy to extend.
STORY_CONTENT_BIAS_TERMS = {
    "character": (
        "princess", "princesses", "prince", "emperor", "soldier", "soldiers",
        "detective", "cowboy",
        "公主", "王子", "皇帝", "士兵", "侦探",
    ),
    "era": (
        "tang dynasty", "song dynasty", "ming dynasty", "qing dynasty",
        "victorian era", "medieval", "1920s", "唐朝", "宋朝", "明朝", "清朝",
        "民国",
    ),
    "location": (
        "palace", "castle", "classroom", "hospital", "battlefield", "new york",
        "皇宫", "城堡", "教室", "医院", "战场",
    ),
    "costume": (
        "hanfu", "kimono", "wedding dress", "school uniform", "armor", "tuxedo",
        "汉服", "和服", "婚纱", "校服", "盔甲",
    ),
    "prop": (
        "sword", "gun", "smartphone", "umbrella", "dagger",
        "宝剑", "手枪", "手机", "雨伞",
    ),
    "action": (
        "running", "fighting", "fights", "kissing", "holding a", "dancing",
        "骑马", "奔跑", "打斗", "亲吻", "手持",
    ),
}

# Exact known names containing a story term but carrying no story-content bias.
# Add exceptions narrowly; Chinese terms otherwise use substring matching.
CHINESE_STORY_CONTENT_EXCEPTIONS = ("公主岭",)

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_PREVIEW_RE = re.compile(r"^/images/extension-styles/[^/]+\.webp$")


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an array of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must contain only strings")
    return tuple(item.strip() for item in value)


def _parse_fragments(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError("prompt_fragment must be an object")
    if set(value) != set(FRAGMENT_KEYS):
        raise ValueError(
            "prompt_fragment must contain exactly: " + ", ".join(FRAGMENT_KEYS)
        )
    return {
        key: _string_tuple(value[key], f"prompt_fragment.{key}")
        for key in FRAGMENT_KEYS
    }


def _story_bias_match(
    fragments: Mapping[str, tuple[str, ...]],
) -> tuple[str, str] | None:
    for key in FRAGMENT_KEYS:
        for phrase in fragments[key]:
            folded_phrase = phrase.casefold()
            chinese_scan_phrase = folded_phrase
            for exception in CHINESE_STORY_CONTENT_EXCEPTIONS:
                chinese_scan_phrase = chinese_scan_phrase.replace(
                    exception.casefold(), ""
                )
            for dimension, terms in STORY_CONTENT_BIAS_TERMS.items():
                for term in terms:
                    folded_term = term.casefold()
                    if folded_term.isascii():
                        pattern = rf"(?<!\w){re.escape(folded_term)}(?!\w)"
                        matched = re.search(pattern, folded_phrase) is not None
                    else:
                        matched = folded_term in chinese_scan_phrase
                    if matched:
                        return dimension, term
    return None


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(
                "source only supports JSON-compatible values with string keys"
            )
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("source only supports finite JSON-compatible numbers")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("source only supports JSON-compatible values")


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ExtensionStyle:
    id: str
    name: str
    category: str
    summary: str
    prompt_fragment: Mapping[str, tuple[str, ...]]
    use_cases: tuple[str, ...]
    preview_asset: str
    source: Mapping[str, Any]
    version: str

    def projection_input(self) -> dict[str, Any]:
        """Return a detached, deterministic input suitable for projection."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "prompt_fragment": {
                key: list(self.prompt_fragment[key]) for key in FRAGMENT_KEYS
            },
            "use_cases": list(self.use_cases),
            "preview_asset": self.preview_asset,
            "source": _deep_thaw(self.source),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtensionStyle":
        if not isinstance(data, Mapping):
            raise ValueError("style entry must be an object")
        style_id = _required_string(data, "id")
        if not style_id.startswith("drama_ext."):
            raise ValueError("id must use the drama_ext. namespace")

        category = _required_string(data, "category")
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"invalid category: {category}")

        preview_asset = _required_string(data, "preview_asset")
        if not _PREVIEW_RE.fullmatch(preview_asset):
            raise ValueError(
                "preview_asset must match /images/extension-styles/*.webp"
            )

        source = data.get("source")
        if not isinstance(source, Mapping):
            raise ValueError("source must be an object")
        if source.get("license_review") != "approved":
            raise ValueError("source.license_review must be approved")
        revision = source.get("imported_revision")
        if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
            raise ValueError(
                "source.imported_revision must be 40 lowercase hexadecimal characters"
            )

        fragments = _parse_fragments(data.get("prompt_fragment"))
        biased_match = _story_bias_match(fragments)
        if biased_match is not None:
            dimension, term = biased_match
            raise ValueError(
                f"prompt_fragment contains story content bias ({dimension}): {term}"
            )

        return cls(
            id=style_id,
            name=_required_string(data, "name"),
            category=category,
            summary=_required_string(data, "summary"),
            prompt_fragment=MappingProxyType(fragments),
            use_cases=_string_tuple(data.get("use_cases"), "use_cases"),
            preview_asset=preview_asset,
            source=_deep_freeze(source),
            version=_required_string(data, "version"),
        )


def compile_prompt_fragment(
    style_or_fragments: ExtensionStyle | Mapping[str, Sequence[str]],
) -> str:
    """Compile non-empty phrases in the catalog's fixed fragment order."""
    if isinstance(style_or_fragments, ExtensionStyle):
        fragments = style_or_fragments.prompt_fragment
    else:
        fragments = _parse_fragments(style_or_fragments)
    return ", ".join(
        phrase.strip()
        for key in FRAGMENT_KEYS
        for phrase in fragments[key]
        if phrase.strip()
    )


def load_catalog(path: str | Path) -> tuple[ExtensionStyle, ...]:
    """Load and validate a UTF-8 JSON extension style catalog."""
    with Path(path).open("r", encoding="utf-8") as handle:
        raw_catalog = json.load(handle)
    if not isinstance(raw_catalog, list):
        raise ValueError("catalog must be a JSON array")
    catalog = tuple(ExtensionStyle.from_dict(entry) for entry in raw_catalog)
    seen: set[str] = set()
    for style in catalog:
        if style.id in seen:
            raise ValueError(f"duplicate extension style id: {style.id}")
        seen.add(style.id)
    return catalog
