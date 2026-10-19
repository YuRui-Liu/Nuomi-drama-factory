"""可选的智能配乐：先查素材库，没有再回退到生成。

匹配复用 `music.matching.match_assets`（纯匹配、无生成副作用）；配乐默认
duck（对白出现时压低音量），由合成阶段统一混音。
"""

from __future__ import annotations

from novelvideo.music.matching import match_assets


def _duration_ms(asset: dict) -> int:
    """素材时长（毫秒）；真实匹配结果用 durationMs，缺键一律按 0 处理。"""
    value = asset.get("durationMs", asset.get("duration_ms", 0))
    return int(value or 0)


def _pick_asset(matches: list[dict], duration_ms: int) -> tuple[dict, bool]:
    """优先覆盖：时长够用的素材里取最短一条；都不够则取最长一条并循环。"""
    sufficient = [asset for asset in matches if _duration_ms(asset) >= duration_ms]
    if sufficient:
        return min(sufficient, key=_duration_ms), False
    return max(matches, key=_duration_ms), True


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
        asset, loop = _pick_asset(matches, duration_ms)
        return {
            "group_id": group["id"],
            "source": "library",
            "asset": asset,
            "duck": True,
            "loop": loop,
        }
    return {
        "group_id": group["id"],
        "source": "generate",
        "prompt": mood,
        "duration_ms": duration_ms,
        "duck": True,
        "loop": False,
    }
