"""事实溯源：把 brief 里的每条 fact 引文回到剧本正文里逐字定位。

比较前去掉空白字符，这样换行、全角/半角空格不会造成假阴性；
其余字符必须完全一致，避免"意思相近"被当成出处。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import Issue

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", text)


def resolve_fact_sources(brief: Mapping, documents: Mapping[int, str]) -> list[Issue]:
    """返回 fact 出处问题；空列表表示每条 fact 都能在对应集数正文里找到引文。"""

    issues: list[Issue] = []
    for fact in brief.get("facts", []) or []:
        source = fact.get("source") or {}
        episode = source.get("episode")
        quote = str(source.get("quote") or "")
        body = documents.get(episode)
        if not body or not quote:
            issues.append(
                Issue(
                    code="brief_source_missing",
                    severity="error",
                    message=f"fact {fact.get('field')} has no resolvable source",
                    field="facts",
                )
            )
            continue
        if _normalize(quote) not in _normalize(body):
            issues.append(
                Issue(
                    code="brief_source_missing",
                    severity="error",
                    message=f"quote not found in episode {episode}: {quote}",
                    field="facts",
                )
            )
    return issues
