"""Build a validated structured publication from source text."""

from __future__ import annotations

from typing import Any

from novelvideo.chapter_detector import ChapterDetector
from novelvideo.structured_builders import (
    StructuredEpisodeInput,
    StructuredPublication,
    build_structured_publication,
)


def _episode_source_ranges(text: str) -> list[tuple[int, int, int, str, str]]:
    ranges: list[tuple[int, int, int, str, str]] = []
    cursor = 0
    for chapter in ChapterDetector().detect(text):
        if chapter.is_fallback:
            content = text
            start = 0
            end = len(text)
            cursor = end
        else:
            content = str(chapter.content or "")
            start = text.find(content, cursor)
            if start < 0:
                start = text.find(content)
            if start < 0:
                start = -1
                end = -1
            else:
                end = start + len(content)
                cursor = end
        title = next(
            (line.strip() for line in content.splitlines() if line.strip()),
            f"第{chapter.number}集",
        )
        ranges.append((int(chapter.number), start, end, title, content))
    return ranges


async def build_structured_publication_from_source(
    text: str,
    spine_template: str | None,
    *,
    on_log: Any = None,
) -> StructuredPublication:
    """Build episodes only; canonical assets are created by explicit user actions."""
    ranges = _episode_source_ranges(text)
    if not ranges:
        raise ValueError("未检测到可发布的分集内容")

    episode_inputs = [
        StructuredEpisodeInput(
            number=number,
            title=title,
            raw_content=content,
            summary=content[:200].strip() + ("..." if len(content) > 200 else ""),
            character_ids=(),
            scene_ids=(),
        )
        for number, _start, _end, title, content in ranges
    ]
    return build_structured_publication(
        episodes=episode_inputs,
        characters=(),
        scenes=(),
    )


__all__ = ["build_structured_publication_from_source"]
