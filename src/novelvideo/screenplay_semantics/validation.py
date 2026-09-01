"""Pure validation of extracted beats against screenplay evidence."""

from __future__ import annotations

from collections.abc import Sequence
import re

from novelvideo.screenplay_semantics.extractor import DramaticBeatDraft
from novelvideo.screenplay_semantics.models import (
    DramaticBeat,
    Scene,
    SemanticValidationIssue,
    SemanticValidationReport,
    SourceRange,
)

STORY_KINDS = {"action", "dialogue", "parenthetical", "transition"}


def _covered(line: int, ranges: tuple[SourceRange, ...]) -> bool:
    return any(item.start_line <= line <= item.end_line for item in ranges)


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", value)


def validate_scene_beats(
    scene: Scene,
    drafts: Sequence[DramaticBeatDraft],
) -> SemanticValidationReport:
    issues: list[SemanticValidationIssue] = []
    dialogue_ids = {block.id for block in scene.blocks if block.kind == "dialogue"}
    covered_lines: set[int] = set()

    for index, draft in enumerate(drafts):
        location = f"beats.{index}"
        for source_range in draft.source_ranges:
            if (
                source_range.start_line < scene.source_range.start_line
                or source_range.end_line > scene.source_range.end_line
            ):
                issues.append(SemanticValidationIssue(
                    code="source_range_outside_scene", message="节拍证据超出场次边界",
                    location=f"{location}.source_ranges", scene_id=scene.id,
                    source_range=source_range,
                ))
            lines = set(range(source_range.start_line, source_range.end_line + 1))
            if covered_lines.intersection(lines):
                issues.append(SemanticValidationIssue(
                    code="overlapping_beat_range", message="节拍证据范围重叠",
                    location=f"{location}.source_ranges", scene_id=scene.id,
                    source_range=source_range,
                ))
            covered_lines.update(lines)

        for character in draft.characters:
            if character not in scene.characters:
                issues.append(SemanticValidationIssue(
                    code="unknown_character", message=f"人物缺少场次证据：{character}",
                    location=f"{location}.characters", scene_id=scene.id,
                ))
        for source_id in draft.dialogue_source_ids:
            if source_id not in dialogue_ids:
                issues.append(SemanticValidationIssue(
                    code="invalid_dialogue_source", message=f"对白来源无效：{source_id}",
                    location=f"{location}.dialogue_source_ids", scene_id=scene.id,
                ))
        evidence = _normalize("\n".join(
            block.text for block in scene.blocks
            if _covered(block.source_range.start_line, draft.source_ranges)
        ))
        for fact in draft.script_facts:
            if _normalize(fact) not in evidence:
                issues.append(SemanticValidationIssue(
                    code="unsupported_script_fact", message=f"事实缺少原文证据：{fact}",
                    location=f"{location}.script_facts", scene_id=scene.id,
                ))

    for block in scene.blocks:
        if block.kind in STORY_KINDS and block.source_range.start_line not in covered_lines:
            issues.append(SemanticValidationIssue(
                code="uncovered_story_block", message="剧情行未被任何戏剧节拍覆盖",
                location="beats", scene_id=scene.id, source_range=block.source_range,
            ))
    return SemanticValidationReport.from_issues(tuple(issues))


def validate_revision_beats(
    scenes: Sequence[Scene],
    beats: Sequence[DramaticBeat],
    *,
    extra_issues: Sequence[SemanticValidationIssue] = (),
) -> SemanticValidationReport:
    """Validate every scene against the current, fully merged beat set."""
    issues: list[SemanticValidationIssue] = []
    for scene in sorted(scenes, key=lambda item: item.ordinal):
        drafts = tuple(
            DramaticBeatDraft.model_validate(beat.model_dump(exclude={
                "id", "ordinal", "scene_id", "stale", "stale_reason",
            }))
            for beat in sorted(
                (item for item in beats if item.scene_id == scene.id),
                key=lambda item: item.ordinal,
            )
        )
        issues.extend(validate_scene_beats(scene, drafts).issues)
    issues.extend(extra_issues)
    return SemanticValidationReport.from_issues(tuple(issues))


__all__ = ["STORY_KINDS", "validate_revision_beats", "validate_scene_beats"]
