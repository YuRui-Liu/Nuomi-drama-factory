"""Deterministic screenplay parsing with exact source-line evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from novelvideo.screenplay_semantics.models import Scene, SourceBlock, SourceBlockKind, SourceRange
from novelvideo.utils.screenplay_scene_parser import (
    LABELED_CHARACTER_RE,
    LABELED_LOCATION_RE,
    SPEAKER_LINE_RE,
    ParsedSourceLine,
    enumerate_screenplay_lines,
    is_scene_start_line,
    parse_character_line,
    parse_scene_blocks,
    parse_location_header_relaxed,
)


@dataclass(frozen=True)
class ParsedScreenplayDocument:
    scenes: tuple[Scene, ...]
    metadata_blocks: tuple[SourceBlock, ...]


def split_speaker_delivery(label: str) -> tuple[str, str]:
    """Separate authored delivery (including OS) from a speaker's identity.

    This does not infer visible cast: a broadcast remains an audio source,
    while named offscreen speech retains its character identity.
    """
    match = re.fullmatch(r"\s*(.+?)\s*[（(]([^（）()]*)[）)]\s*", label)
    if match:
        return match[1].strip(), match[2].strip()
    return label.strip(), ""


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
    field = _screenplay_field(stripped)
    if field:
        label, body = field
        if label in {"人物", "出场人物", "角色"}:
            return "cast"
        if not body:
            return "formatting"
        return "dialogue" if label == "对白" else "action"
    if stripped.startswith(("（", "(")) and stripped.endswith(("）", ")")):
        return "parenthetical"
    if re.match(r"^(?:切至|转场|淡出|淡入|黑场|字幕)[：:]?", stripped):
        return "transition"
    if SPEAKER_LINE_RE.match(stripped):
        return "dialogue"
    if stripped.startswith(("△", "▲", "【")):
        return "action"
    return "action"


def _screenplay_field(text: str) -> tuple[str, str] | None:
    # Normalize markup only for classification; source evidence stays verbatim.
    normalized = re.sub(r"^\s*(?:[-*+]\s+|#{1,6}\s+)?", "", text).replace("**", "").strip()
    match = re.match(r"^(人物|出场人物|角色|动作|对白|结尾钩子)\s*[：:]\s*(.*)$", normalized)
    return (match[1], match[2]) if match else None


def recover_scene_fields(scene: Scene) -> Scene:
    """Recover old misclassified fields without changing IDs or source evidence."""
    characters = list(scene.characters)
    blocks = []
    for block in scene.blocks:
        field = _screenplay_field(block.text)
        if field:
            label, body = field
            if label in {"人物", "出场人物", "角色"}:
                for character in parse_character_line(f"人物：{body}"):
                    if character not in characters:
                        characters.append(character)
            block = block.model_copy(update={"kind": _content_kind(block.text)})
        blocks.append(block)
    return scene.model_copy(update={"characters": tuple(characters), "blocks": tuple(blocks)})


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
        if re.fullmatch(r"<!--.*-->", value):
            metadata.append(_block(line, len(metadata) + 1, "formatting"))
            continue
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
        if (current_header is not None and not current_blocks
                and not current_location and LABELED_LOCATION_RE.match(value)):
            location = parse_location_header_relaxed(value)
            if location:
                current_location, current_time, interior = location
                current_interior = {"内": "interior", "外": "exterior"}.get(interior, "unspecified")
                metadata.append(_block(line, len(metadata) + 1, "formatting"))
                last_scene_line = line.number
                continue
        if value and is_scene_start_line(value):
            flush_scene()
            current_header = line
            parsed_header = parse_scene_blocks([value])[0]
            current_location = parsed_header.location
            current_time = parsed_header.time_of_day
            if parsed_header.interior_exterior:
                current_interior = {
                    "内": "interior",
                    "外": "exterior",
                }.get(parsed_header.interior_exterior, "unspecified")
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
        field = _screenplay_field(value)
        cast_value = f"{field[0]}：{field[1]}" if field else value
        cast = LABELED_CHARACTER_RE.match(cast_value)
        if cast and not current_blocks:
            for character in parse_character_line(cast_value):
                if character not in current_characters:
                    current_characters.append(character)
            metadata.append(_block(line, len(metadata) + 1, "cast"))
            last_scene_line = line.number
            continue
        current_blocks.append(_block(line, len(current_blocks) + 1, _content_kind(value)))
        last_scene_line = line.number

    flush_scene()
    return ParsedScreenplayDocument(scenes=tuple(scenes), metadata_blocks=tuple(metadata))


__all__ = ["ParsedScreenplayDocument", "parse_screenplay_document"]
