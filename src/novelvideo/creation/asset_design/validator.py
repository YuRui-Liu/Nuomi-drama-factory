"""V3 校验：挡住三类噪声。

1. 风格重复——风格片段属于软件层，设计稿里再写一遍会和渲染参数打架；
2. 禁词——水印、文字、真人照片等会让出图不可用；
3. 事实冲突——设计稿与剧本正文、或与自己的 negative 列表互相矛盾。

另有若干 warning，不阻断但提示补锚点。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .models import AssetDesignBrief, Issue

NOISE_TERMS = ("水印", "文字", "真人照片", "参考图风格", "摄影级")
STYLE_MIN_LEN = 4

# (剧本里的否定表达, 设计稿里的对应词)——出现前者就不该出现后者。
_CONTRADICTIONS = (("无胡子", "胡茬"),)

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", text)


def _text_of(brief: AssetDesignBrief) -> str:
    return " ".join(str(value) for value in brief.design.values() if isinstance(value, str))


def _wardrobe_pieces(brief: AssetDesignBrief) -> list[str]:
    pieces: list[str] = []
    for item in brief.design.get("wardrobe", []) or []:
        if isinstance(item, Mapping):
            piece = str(item.get("piece", "")).strip()
        else:
            piece = str(getattr(item, "piece", "")).strip()
        if piece:
            pieces.append(piece)
    return pieces


def validate_brief(
    brief: AssetDesignBrief, *, documents: Mapping[int, str], style_fragment: str
) -> list[Issue]:
    issues: list[Issue] = []
    text = _text_of(brief)

    fragment = style_fragment.strip()
    if fragment and len(_normalize(fragment)) >= STYLE_MIN_LEN and _normalize(fragment) in _normalize(text):
        issues.append(
            Issue(
                code="brief_style_duplicate",
                severity="error",
                message="style fragment must not appear in the brief",
                field="design",
            )
        )

    for term in NOISE_TERMS:
        if term in text:
            issues.append(
                Issue(
                    code="brief_noise_term",
                    severity="error",
                    message=f"noise term not allowed: {term}",
                    field="design",
                )
            )

    negative = " ".join(brief.render.negative)
    for piece in _wardrobe_pieces(brief):
        if piece in negative:
            issues.append(
                Issue(
                    code="brief_fact_conflict",
                    severity="error",
                    message=f"wardrobe piece {piece} is also forbidden",
                    field="design.wardrobe",
                )
            )

    screenplay = " ".join(str(doc) for doc in documents.values())
    for negated, design_term in _CONTRADICTIONS:
        if negated in screenplay and design_term in text:
            issues.append(
                Issue(
                    code="brief_fact_conflict",
                    severity="error",
                    message=f"design contradicts screenplay fact: {negated}",
                    field="design",
                )
            )

    if len(brief.consistency.anchors) < 2:
        issues.append(
            Issue(
                code="brief_anchor_thin",
                severity="warning",
                message="fewer than two consistency anchors",
                field="consistency.anchors",
            )
        )

    return issues
