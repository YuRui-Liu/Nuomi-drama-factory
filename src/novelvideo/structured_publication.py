"""Build a validated structured publication from source text."""

from __future__ import annotations

import inspect
from typing import Any

from novelvideo.chapter_detector import ChapterDetector
from novelvideo.structured_builders import (
    StructuredCharacterInput,
    StructuredEpisodeInput,
    StructuredPublication,
    StructuredSceneInput,
    StructuredSourceRef,
    build_structured_publication,
    stable_logical_id,
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


def _ref_for_global_span(
    ranges: list[tuple[int, int, int, str, str]],
    *,
    start: int,
    end: int,
    quote: str,
) -> StructuredSourceRef | None:
    for number, episode_start, episode_end, _title, content in ranges:
        if episode_start < 0 or start < episode_start or end > episode_end:
            continue
        local_start = start - episode_start
        local_end = end - episode_start
        if content[local_start:local_end] != quote:
            return None
        return StructuredSourceRef(
            episode_number=number,
            source_start=local_start,
            source_end=local_end,
            quote=quote,
        )
    return None


async def build_structured_publication_from_source(
    text: str,
    spine_template: str | None,
    *,
    on_log: Any = None,
) -> StructuredPublication:
    """Extract and validate all episodes, characters, scenes, and evidence."""
    from novelvideo.story_analysis import chunk_source_text
    from novelvideo.structured_extraction import extract_characters_from_chunks
    from novelvideo.utils.screenplay_scene_parser import parse_scene_blocks

    ranges = _episode_source_ranges(text)
    if not ranges:
        raise ValueError("未检测到可发布的分集内容")
    chunks = chunk_source_text(text, spine_template)
    if not chunks:
        raise ValueError("原文切分结果为空，无法提取正式资产")

    extracted = extract_characters_from_chunks(chunks, on_log=on_log)
    merged_characters = await extracted if inspect.isawaitable(extracted) else extracted
    character_inputs: list[StructuredCharacterInput] = []
    episode_character_ids: dict[int, list[str]] = {
        number: [] for number, *_rest in ranges
    }
    for item in merged_characters:
        refs: list[StructuredSourceRef] = []
        for evidence in item.evidence:
            quote = str(evidence.get("evidence_text") or "")
            reference = _ref_for_global_span(
                ranges,
                start=int(evidence.get("source_start", -1)),
                end=int(evidence.get("source_end", -1)),
                quote=quote,
            )
            if reference is not None and reference not in refs:
                refs.append(reference)
        if not refs:
            raise ValueError(f"角色 {item.name} 缺少可核验的原文证据")
        logical_id = stable_logical_id("character", item.name)
        for reference in refs:
            if logical_id not in episode_character_ids[reference.episode_number]:
                episode_character_ids[reference.episode_number].append(logical_id)
        character_inputs.append(
            StructuredCharacterInput(
                name=item.name,
                aliases=tuple(sorted(item.aliases)),
                role=item.role,
                face=item.face or item.description,
                build=item.build,
                gender=item.gender,
                description=item.description,
                source_refs=tuple(refs),
            )
        )

    scene_by_id: dict[str, StructuredSceneInput] = {}
    episode_scene_ids: dict[int, list[str]] = {number: [] for number, *_rest in ranges}
    if str(spine_template or "").strip() == "drama":
        search_cursor = 0
        default_episode = ranges[0][0]
        for block in parse_scene_blocks(text):
            location = str(block.location or "").strip()
            if not location:
                continue
            time_of_day = str(block.time_of_day or "").strip()
            logical_id = stable_logical_id("scene", location, time_of_day)
            quote = str(block.header_line or "").strip()
            refs: tuple[StructuredSourceRef, ...] = ()
            if quote:
                start = text.find(quote, search_cursor)
                if start < 0:
                    start = text.find(quote)
                if start >= 0:
                    search_cursor = start + len(quote)
                    reference = _ref_for_global_span(
                        ranges, start=start, end=start + len(quote), quote=quote
                    )
                    if reference is not None:
                        refs = (reference,)
            details = "；".join(line for line in block.lines[:4] if line.strip())
            scene_by_id.setdefault(
                logical_id,
                StructuredSceneInput(
                    name=location,
                    location=location,
                    time=time_of_day,
                    environment=(f"{location}，{details}" if details else location),
                    source_refs=refs,
                    scene_type=(
                        "exterior" if block.interior_exterior == "外" else "interior"
                    ),
                ),
            )
            episode_number = int(block.episode or default_episode)
            if episode_number not in episode_scene_ids:
                episode_number = default_episode
            if logical_id not in episode_scene_ids[episode_number]:
                episode_scene_ids[episode_number].append(logical_id)

    episode_inputs = [
        StructuredEpisodeInput(
            number=number,
            title=title,
            raw_content=content,
            summary=content[:200].strip() + ("..." if len(content) > 200 else ""),
            character_ids=tuple(episode_character_ids[number]),
            scene_ids=tuple(episode_scene_ids[number]),
        )
        for number, _start, _end, title, content in ranges
    ]
    return build_structured_publication(
        episodes=episode_inputs,
        characters=character_inputs,
        scenes=scene_by_id.values(),
    )


__all__ = ["build_structured_publication_from_source"]
