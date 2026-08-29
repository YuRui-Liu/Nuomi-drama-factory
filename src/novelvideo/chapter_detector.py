"""Knowledge-runtime-neutral chapter detection.

This module is safe for the ``structured_v1`` pipeline: importing it never
initializes Cognee or any graph/embedding runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from novelvideo.utils.screenplay_quality import extract_screenplay_candidate_lines


@dataclass(frozen=True, slots=True)
class ChapterInfo:
    number: int
    title: Optional[str]
    start_line: int
    end_line: int
    content: str
    is_fallback: bool = False


class ChapterDetector:
    """Detect Chinese/English chapter or episode headings deterministically."""

    PATTERNS = (
        r"^(?:#{1,6}\s*)?(?:《[^》\n]{1,40}》\s*)?第\s*([一二三四五六七八九十百千\d]+)\s*章(?=$|[\s:：《（(【\[\-—–、.。．])",
        r"^(?:#{1,6}\s*)?(?:《[^》\n]{1,40}》\s*)?第\s*([一二三四五六七八九十百千\d]+)\s*集(?=$|[\s:：《（(【\[\-—–、.。．])",
        r"^(?:#{1,6}\s*)?Chapter\s*(\d+)(?=$|[\s:：(\[\-—–.。．])",
        r"^(?:#{1,6}\s*)?Episode\s*(\d+)(?=$|[\s:：(\[\-—–.。．])",
    )
    CN_NUM_MAP = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "壹": 1,
        "二": 2,
        "贰": 2,
        "两": 2,
        "三": 3,
        "叁": 3,
        "四": 4,
        "肆": 4,
        "五": 5,
        "伍": 5,
        "六": 6,
        "陆": 6,
        "七": 7,
        "柒": 7,
        "八": 8,
        "捌": 8,
        "九": 9,
        "玖": 9,
        "十": 10,
        "拾": 10,
        "百": 100,
        "佰": 100,
        "千": 1000,
        "仟": 1000,
    }

    def detect(self, text: str) -> list[ChapterInfo]:
        lines = (text or "").split("\n")
        chapters: list[ChapterInfo] = []
        current_start: int | None = None
        current_num: int | None = None
        for index, line in enumerate(lines):
            chapter_num = self._match_chapter(line.strip())
            if chapter_num is None:
                continue
            if current_start is not None and current_num is not None:
                chapters.append(
                    ChapterInfo(
                        number=current_num,
                        title=None,
                        start_line=current_start,
                        end_line=index,
                        content="\n".join(lines[current_start:index]),
                    )
                )
            current_start = index
            current_num = chapter_num
        if current_start is not None and current_num is not None:
            chapters.append(
                ChapterInfo(
                    number=current_num,
                    title=None,
                    start_line=current_start,
                    end_line=len(lines),
                    content="\n".join(lines[current_start:]),
                )
            )
        if chapters:
            return chapters
        fallback = self._prepare_fallback_content(text or "")
        if not fallback:
            return []
        return [
            ChapterInfo(
                number=1,
                title="第1章",
                start_line=0,
                end_line=len(fallback.splitlines()) or len(lines),
                content=fallback,
                is_fallback=True,
            )
        ]

    def has_chapters(self, text: str, min_chapters: int = 2) -> bool:
        return len(self.detect(text)) >= min_chapters

    def get_chapter_count(self, text: str) -> int:
        return len(self.detect(text))

    def _match_chapter(self, line: str) -> int | None:
        for pattern in self.PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match and self._is_valid_title_tail(line[match.end() :]):
                return self._parse_number(match.group(1))
        return None

    @staticmethod
    def _is_valid_title_tail(tail: str) -> bool:
        stripped = tail.strip()
        if not stripped:
            return True
        if stripped[0] in ".。．":
            return not ChapterDetector._looks_like_sentence_tail(stripped[1:].strip())
        if stripped[0] in ":：《（(【[-—–、":
            return True
        return not ChapterDetector._looks_like_sentence_tail(stripped)

    @staticmethod
    def _looks_like_sentence_tail(tail: str) -> bool:
        return bool(re.search(r"[。\.…]\s*$", tail))

    def _parse_number(self, value: str) -> int:
        if value.isdigit():
            return int(value)
        if value in self.CN_NUM_MAP:
            return self.CN_NUM_MAP[value]
        result = 0
        current = 0
        for char in value:
            number = self.CN_NUM_MAP.get(char)
            if number is None:
                continue
            if number in {10, 100, 1000}:
                result += (current or 1) * number
                current = 0
            elif number:
                current = number
        return result + current or 1

    @staticmethod
    def _prepare_fallback_content(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        candidate_text = "\n".join(extract_screenplay_candidate_lines(text)).strip()
        return candidate_text or raw


__all__ = ["ChapterDetector", "ChapterInfo"]
