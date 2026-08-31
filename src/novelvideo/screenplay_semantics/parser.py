"""Deterministic screenplay parsing with exact source-line evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from novelvideo.screenplay_semantics.models import Scene, SourceBlock, SourceBlockKind, SourceRange
from novelvideo.utils.screenplay_scene_parser import (
    LABELED_CHARACTER_RE,
    SPEAKER_LINE_RE,
    ParsedSourceLine,
    enumerate_screenplay_lines,
    is_scene_start_line,
    parse_character_line,
    parse_location_header,
)


@dataclass(frozen=True)
class ParsedScreenplayDocument:
    scenes: tuple[Scene, ...]
    metadata_blocks: tuple[SourceBlock, ...]


def _block(line: ParsedSourceLine, ordinal: int, kind: SourceBlockKind) -> SourceBlock:
    return SourceBlock(
        id=f"line-{line.number}",
        ordinal=ordinal,
        kind=kind,
        text=line.text,
        source_range=SourceRange(start_line=line.number, end_line=line.number),
    )


def _content_kind(text: str) -> SourceBlockKind:
    stripped = text.strip()
    if stripped.startswith(("（", "(")) and stripped.endswith(("）", ")")):
        return "parenthetical"
    if re.match(r"^(?:切至|转场|淡出|淡入|黑场|字幕)[：:]?", stripped):
        return "transition"
    if SPEAKER_LINE_RE.match(stripped):
        return "dialogue"
    if stripped.startswith(("△", "▲", "【")):
        return "action"
    return "action"


def _metadata_kind(text: str, *, in_frontmatter: bool) -> SourceBlockKind:
    if in_frontmatter or text == "---":
        return "frontmatter"
    if re.match(r"^#{1,6}\s+", text):
        return "chapter_card"
    if not text or re.fullmatch(r"[-=*—_]{3,}", text):
        return "formatting"
    return "unclassified"


def parse_screenplay_document(text: str) -> ParsedScreenplayDocument:
    lines = enumerate_screenplay_lines(text)
    scenes: list[Scene] = []
    metadata: list[SourceBlock] = []
    current_header: ParsedSourceLine | None = None
    current_location = ""
    current_time = ""
    current_interior: str = "unspecified"
    current_characters: list[str] = []
    current_blocks: list[SourceBlock] = []
    last_scene_line = 0
    in_frontmatter = False
    frontmatter_seen = False

    def flush_scene() -> None:
        nonlocal current_header, current_location, current_time, current_interior
        nonlocal current_characters, current_blocks, last_scene_line
        if current_header is None:
            return
        ordinal = len(scenes) + 1
        end_line = max(last_scene_line, current_header.number)
        digest_payload = "\n".join(
            [current_header.text, *(block.text for block in current_blocks)]
        )
        digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        scenes.append(
            Scene(
                id=f"scene-{ordinal}-{digest[:10]}",
                ordinal=ordinal,
                source_range=SourceRange(
                    start_line=current_header.number,
                    end_line=end_line,
                ),
                heading=current_header.text,
                interior_exterior=current_interior,
                location=current_location,
                time_of_day=current_time,
                characters=tuple(current_characters),
                blocks=tuple(current_blocks),
                content_hash=digest,
            )
        )
        current_header = None
        current_location = ""
        current_time = ""
        current_interior = "unspecified"
        current_characters = []
        current_blocks = []
        last_scene_line = 0

    for line in lines:
        value = line.text
        if current_header is None and value == "---":
            kind = "frontmatter"
            metadata.append(_block(line, len(metadata) + 1, kind))
            in_frontmatter = not in_frontmatter if not frontmatter_seen or in_frontmatter else False
            if not in_frontmatter:
                frontmatter_seen = True
            continue
        if current_header is None and in_frontmatter:
            metadata.append(_block(line, len(metadata) + 1, "frontmatter"))
            continue
        if value and is_scene_start_line(value):
            flush_scene()
            current_header = line
            parsed_location = parse_location_header(value)
            if parsed_location:
                current_location, current_time, marker = parsed_location
                current_interior = "interior" if marker == "内" else "exterior"
            last_scene_line = line.number
            continue
        if current_header is None:
            if value:
                metadata.append(
                    _block(
                        line,
                        len(metadata) + 1,
                        _metadata_kind(value, in_frontmatter=False),
                    )
                )
            continue
        if not value:
            continue
        cast = LABELED_CHARACTER_RE.match(value)
        if cast and not current_blocks:
            for character in parse_character_line(value):
                if character not in current_characters:
                    current_characters.append(character)
            last_scene_line = line.number
            continue
        current_blocks.append(_block(line, len(current_blocks) + 1, _content_kind(value)))
        last_scene_line = line.number

    flush_scene()
    return ParsedScreenplayDocument(scenes=tuple(scenes), metadata_blocks=tuple(metadata))


__all__ = ["ParsedScreenplayDocument", "parse_screenplay_document"]
