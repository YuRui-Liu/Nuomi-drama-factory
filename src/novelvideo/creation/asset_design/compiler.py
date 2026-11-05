"""brief -> 提示词编译。

编译器是纯函数：同样的 brief、风格片段与节点参数，永远产出同样的
`prompt_text` 与指纹，方便回归比对与缓存。风格片段只在末尾注入一次。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .models import AssetDesignBrief, CompileResult, Fingerprint
from .store import AssetBriefStore

COMPILER_VERSION = "2"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """design 里既可能放 dict，也可能放 pydantic 模型（如 Wardrobe）。"""

    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return value
    return {}


def _line(label: str, value: str) -> str:
    cleaned = value.strip()
    return f"{label}: {cleaned}" if cleaned else ""


def compile_brief(
    brief: AssetDesignBrief,
    *,
    style_fragment: str,
    params: Mapping[str, object],
    style_id: str = "",
    style_revision: str = "",
) -> CompileResult:
    design = brief.design
    subject_parts = [
        _line("设计描述", str(design.get("description", ""))),
        _line("剪影", str(design.get("silhouette", ""))),
        _line("面部", str(design.get("face", ""))),
        _line("发型", str(design.get("hair", ""))),
    ]

    wardrobe: list[str] = []
    for item in design.get("wardrobe", []) or []:
        entry = _as_mapping(item)
        piece = str(entry.get("piece", "")).strip()
        if not piece:
            continue
        detail = "，".join(
            str(entry.get(key, "")).strip()
            for key in ("color", "material", "state")
            if str(entry.get(key, "")).strip()
        )
        wardrobe.append(f"- {piece}" + (f"：{detail}" if detail else ""))

    facts = [f"- {fact.field}: {fact.value}" for fact in brief.facts]
    anchors = [f"- {item}" for item in brief.consistency.anchors]
    subject = "\n".join(part for part in subject_parts if part)

    compiled = {
        "subject": subject,
        "facts": facts,
        "wardrobe": wardrobe,
        "anchors": anchors,
        "material": brief.render.material,
        "volume": brief.render.volume,
        "negative": list(brief.render.negative) + list(brief.consistency.forbidden),
        "params": dict(params),
    }

    lines = [
        part
        for part in (
            subject,
            "\n".join(facts),
            "\n".join(wardrobe),
            "\n".join(anchors),
            _line("材质", brief.render.material),
            _line("体积", brief.render.volume),
            style_fragment.strip(),
        )
        if part
    ]
    prompt_text = "\n".join(lines)
    return CompileResult(
        compiled=compiled,
        prompt_text=prompt_text,
        style_fragment=style_fragment.strip(),
        fingerprint=Fingerprint(
            brief_hash=AssetBriefStore.hash(brief),
            style_id=style_id,
            style_revision=style_revision,
            compiler_version=COMPILER_VERSION,
        ),
        issues=[],
    )
