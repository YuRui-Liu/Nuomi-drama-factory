"""Pure domain helpers for importing episode source documents."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import re
from typing import Literal, Mapping, Sequence
from uuid import uuid4


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUMBER_TOKEN = r"[0-9零〇一二两三四五六七八九十百千]+"
_CONTENT_PATTERNS = (
    re.compile(rf"第\s*({_NUMBER_TOKEN})\s*[集章]"),
    re.compile(r"\bepisode\s*[-_:#]?\s*0*([1-9][0-9]*)\b", re.IGNORECASE),
)
_FILENAME_PATTERNS = (
    *_CONTENT_PATTERNS,
    re.compile(r"(?:^|[^a-z0-9])e\s*[-_]?\s*0*([1-9][0-9]*)(?:$|[^0-9])", re.IGNORECASE),
)
_EPISODE_HEADING_PATTERN = re.compile(
    rf"^(?:\A\ufeff)?[ \t]*(?:#{{1,6}}[ \t]*)?(?:"
    rf"第\s*(?P<chinese>{_NUMBER_TOKEN})\s*集"
    r"|episode\s*[-_:#]?\s*0*(?P<english>[1-9][0-9]*)\b"
    r")[^\r\n]*(?:\r?\n|$)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class EpisodeNumberDetection:
    episode_number: int | None
    source: Literal["body", "filename", "manual"] | None
    content_episode_number: int | None = None
    filename_episode_number: int | None = None

    @property
    def has_filename_mismatch(self) -> bool:
        return (
            self.content_episode_number is not None
            and self.filename_episode_number is not None
            and self.content_episode_number != self.filename_episode_number
        )


@dataclass(frozen=True, slots=True)
class EpisodeCandidate:
    source_filename: str
    content: str
    episode_number: int | None = None
    number_source: Literal["body", "filename", "manual"] | None = None
    file_id: str = ""
    title: str = ""
    content_hash: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def filename(self) -> str:
        return self.source_filename


EpisodeSourceCandidate = EpisodeCandidate


@dataclass(frozen=True, slots=True)
class EpisodeResolution:
    imports: tuple[EpisodeCandidate, ...]
    skipped: tuple[EpisodeCandidate, ...]
    overwritten_episode_numbers: frozenset[int]


def _parse_number(token: str) -> int | None:
    if token.isascii() and token.isdecimal():
        value = int(token)
        return value if value > 0 else None

    total = 0
    current = 0
    for character in token:
        if character in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[character]
        elif character in _CHINESE_UNITS:
            unit = _CHINESE_UNITS[character]
            total += (current or 1) * unit
            current = 0
        else:
            return None
    value = total + current
    return value if value > 0 else None


def _first_number(text: str, patterns: Sequence[re.Pattern[str]]) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _parse_number(match.group(1))
    return None


def detect_episode_number(content: str, filename: str) -> EpisodeNumberDetection:
    """Detect the content number first, falling back to the source filename."""
    content_number = _first_number(content, _CONTENT_PATTERNS)
    filename_number = _first_number(Path(filename).stem, _FILENAME_PATTERNS)
    if content_number is not None:
        return EpisodeNumberDetection(
            content_number, "body", content_number, filename_number
        )
    if filename_number is not None:
        return EpisodeNumberDetection(
            filename_number, "filename", None, filename_number
        )
    return EpisodeNumberDetection(None, None)


def _extract_title(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    first_line = first_line.lstrip("#").strip()
    for pattern in _CONTENT_PATTERNS:
        match = pattern.match(first_line)
        if match:
            return first_line[match.end() :].lstrip(" :：-_—").strip()
    return ""


def build_episode_candidate(filename: str, content: str) -> EpisodeCandidate:
    detection = detect_episode_number(content, filename)
    warnings: tuple[str, ...] = ()
    if detection.has_filename_mismatch:
        warnings = (
            "正文集号 "
            f"{detection.content_episode_number} 与文件名集号 "
            f"{detection.filename_episode_number} 不一致",
        )
    return EpisodeCandidate(
        source_filename=filename,
        content=content,
        episode_number=detection.episode_number,
        number_source=detection.source,
        file_id=uuid4().hex,
        title=_extract_title(content),
        content_hash=content_sha256(content),
        warnings=warnings,
    )


def split_episode_candidates(
    filename: str, content: str
) -> tuple[EpisodeCandidate, ...]:
    """Split a collection document on dedicated episode heading lines."""
    boundaries = tuple(
        match
        for match in _EPISODE_HEADING_PATTERN.finditer(content)
        if _parse_number(match.group("chinese") or match.group("english"))
        is not None
    )
    if len(boundaries) < 2:
        return (build_episode_candidate(filename, content),)

    preface = content[: boundaries[0].start()]
    candidates: list[EpisodeCandidate] = []
    for index, boundary in enumerate(boundaries):
        next_start = (
            boundaries[index + 1].start()
            if index + 1 < len(boundaries)
            else len(content)
        )
        heading_segment = content[boundary.start() : next_start]
        candidate = build_episode_candidate(
            filename, boundary.group().removeprefix("\ufeff")
        )
        candidate_content = (
            f"{preface}{heading_segment}" if index == 0 else heading_segment
        )
        warnings = list(candidate.warnings)
        if index == 0 and preface.strip():
            warnings.append("首集包含合集前言")
        if not content[boundary.end() : next_start].strip():
            warnings.append("分集标题后缺少正文")
        candidates.append(
            replace(
                candidate,
                content=candidate_content,
                content_hash=content_sha256(candidate_content),
                warnings=tuple(warnings),
            )
        )
    return tuple(candidates)


def inspect_episode_source(filename: str, content: str) -> EpisodeSourceCandidate:
    return build_episode_candidate(filename, content)


def apply_manual_episode_numbers(
    candidates: Sequence[EpisodeCandidate], manual_numbers: Mapping[str, int]
) -> tuple[EpisodeCandidate, ...]:
    result: list[EpisodeCandidate] = []
    for candidate in candidates:
        if candidate.episode_number is not None:
            result.append(candidate)
            continue
        identity = candidate.file_id or candidate.source_filename
        if identity not in manual_numbers:
            raise ValueError(f"{candidate.source_filename} 必须手动指定集号")
        number = manual_numbers[identity]
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError("手动指定的集号必须是正整数")
        result.append(
            replace(candidate, episode_number=number, number_source="manual")
        )
    return tuple(result)


def resolve_episode_candidates(
    candidates: Sequence[EpisodeCandidate],
    existing_numbers: set[int] | frozenset[int],
    decisions: Mapping[int, str],
) -> EpisodeResolution:
    numbers: list[int] = []
    for candidate in candidates:
        if candidate.episode_number is None:
            raise ValueError(f"{candidate.source_filename} 缺少集号")
        if (
            isinstance(candidate.episode_number, bool)
            or not isinstance(candidate.episode_number, int)
            or candidate.episode_number <= 0
        ):
            raise ValueError("集号必须是正整数")
        numbers.append(candidate.episode_number)

    duplicate_numbers = sorted(
        number for number in set(numbers) if numbers.count(number) > 1
    )
    if duplicate_numbers:
        rendered = ", ".join(str(number) for number in duplicate_numbers)
        raise ValueError(f"批次内部存在重复集号: {rendered}")

    imports: list[EpisodeCandidate] = []
    skipped: list[EpisodeCandidate] = []
    overwritten: set[int] = set()
    for candidate in sorted(candidates, key=lambda item: item.episode_number or 0):
        number = candidate.episode_number
        if number not in existing_numbers:
            imports.append(candidate)
            continue
        decision = decisions.get(number)
        if decision not in {"overwrite", "skip"}:
            raise ValueError(
                f"第 {number} 集冲突必须明确选择 overwrite 或 skip"
            )
        if decision == "skip":
            skipped.append(candidate)
        else:
            imports.append(candidate)
            overwritten.add(number)

    return EpisodeResolution(
        imports=tuple(imports),
        skipped=tuple(skipped),
        overwritten_episode_numbers=frozenset(overwritten),
    )


def content_sha256(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def canonical_novel_text(episodes: Sequence[EpisodeCandidate]) -> str:
    numbers: list[int] = []
    for episode in episodes:
        if (
            isinstance(episode.episode_number, bool)
            or not isinstance(episode.episode_number, int)
            or episode.episode_number <= 0
        ):
            raise ValueError("合成原文的每一集都必须有合法集号")
        numbers.append(episode.episode_number)
    if len(numbers) != len(set(numbers)):
        raise ValueError("合成原文不能包含重复集号")

    ordered = sorted(episodes, key=lambda episode: episode.episode_number or 0)
    body = "\n\n".join(episode.content.strip() for episode in ordered)
    return f"{body}\n" if body else ""


def validate_resolutions(
    items: Sequence[EpisodeCandidate],
    existing: Mapping[int, int] | set[int] | frozenset[int],
    resolutions: Mapping[int, str],
) -> EpisodeResolution:
    existing_numbers = set(existing)
    return resolve_episode_candidates(items, existing_numbers, resolutions)


def compose_canonical_novel(sources: Sequence[EpisodeCandidate]) -> str:
    return canonical_novel_text(sources)
