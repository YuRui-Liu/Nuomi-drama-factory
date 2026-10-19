"""可选的智能配乐：先查素材库，没有再回退到生成。

匹配复用 `music.matching.match_assets`（纯匹配、无生成副作用）；配乐默认
duck（对白出现时压低音量），由合成阶段统一混音。
"""

from __future__ import annotations

from novelvideo.music.matching import match_assets


def plan_group_music(
    *,
    group: dict,
    mood: str,
    duration_ms: int,
    enabled: bool,
    assets: list[dict],
) -> dict | None:
    if not enabled:
        return None
    matches = match_assets(assets, query=mood, duration_ms=duration_ms, instrumental_only=True)
    if matches:
        return {"group_id": group["id"], "source": "library", "asset": matches[0], "duck": True}
    return {
        "group_id": group["id"],
        "source": "generate",
        "prompt": mood,
        "duration_ms": duration_ms,
        "duck": True,
    }
