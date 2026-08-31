from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from ulid import ULID


SourceBlockKind = Literal[
    "scene_header",
    "cast",
    "action",
    "speaker",
    "dialogue",
    "parenthetical",
    "transition",
    "chapter_card",
    "frontmatter",
    "formatting",
    "unclassified",
]
SceneSemanticStatus = Literal["parsed", "validated", "reused", "stale", "failed"]
ScreenplaySemanticStatus = Literal[
    "draft",
    "validating",
    "review_required",
    "active",
    "superseded",
    "abandoned",
    "failed",
]
SemanticValidationSeverity = Literal["error", "warning"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRange(FrozenModel):
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("source range end must not precede start")
        return self


def _validate_ordered_ranges(
    ranges: tuple[SourceRange, ...],
) -> tuple[SourceRange, ...]:
    for previous, current in zip(ranges, ranges[1:]):
        if current.start_line <= previous.end_line:
            raise ValueError("source ranges must be ordered and non-overlapping")
    return ranges


class SourceBlock(FrozenModel):
    id: str = Field(min_length=1)
    ordinal: int = Field(gt=0)
    kind: SourceBlockKind
    text: str
    source_range: SourceRange


class Scene(FrozenModel):
    id: str = Field(min_length=1)
    ordinal: int = Field(gt=0)
    source_range: SourceRange
    heading: str
    interior_exterior: Literal[
        "interior", "exterior", "interior_exterior", "unspecified"
    ] = "unspecified"
    location: str = ""
    time_of_day: str = ""
    characters: tuple[str, ...] = ()
    blocks: tuple[SourceBlock, ...] = ()
    content_hash: str = Field(min_length=1)
    status: SceneSemanticStatus = "parsed"

    @model_validator(mode="after")
    def validate_blocks(self) -> Self:
        block_ids = [block.id for block in self.blocks]
        if len(set(block_ids)) != len(block_ids):
            raise ValueError("source block ids must be unique")

        ordinals = [block.ordinal for block in self.blocks]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("source block ordinals must be unique")
        if ordinals and set(ordinals) != set(range(1, len(ordinals) + 1)):
            raise ValueError("source block ordinals must be contiguous")

        ordered_blocks = sorted(self.blocks, key=lambda item: item.ordinal)
        _validate_ordered_ranges(tuple(block.source_range for block in ordered_blocks))
        for block in self.blocks:
            if (
                block.source_range.start_line < self.source_range.start_line
                or block.source_range.end_line > self.source_range.end_line
            ):
                raise ValueError("source block must belong to its scene")
        return self


class DramaticBeat(FrozenModel):
    id: str = Field(min_length=1)
    ordinal: int = Field(gt=0)
    scene_id: str = Field(min_length=1)
    source_ranges: tuple[SourceRange, ...] = Field(min_length=1)
    characters: tuple[str, ...] = ()
    goal: str
    obstacle: str
    action: str
    reaction: str
    turn: str
    result: str
    emotional_shift: str
    dialogue_source_ids: tuple[str, ...] = ()
    estimated_duration_seconds: float = Field(gt=0, le=30)
    must_show: tuple[str, ...] = Field(min_length=1)
    script_facts: tuple[str, ...] = Field(min_length=1)
    director_interpretation: tuple[str, ...] = ()
    stale: bool = False
    stale_reason: str | None = None

    @field_validator("source_ranges", mode="after")
    @classmethod
    def validate_source_ranges(
        cls, ranges: tuple[SourceRange, ...]
    ) -> tuple[SourceRange, ...]:
        return _validate_ordered_ranges(ranges)


class SemanticValidationIssue(FrozenModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: SemanticValidationSeverity = "error"
    location: str | None = None
    scene_id: str | None = None
    beat_id: str | None = None
    source_range: SourceRange | None = None


class SemanticValidationReport(FrozenModel):
    passed: bool = False
    issues: tuple[SemanticValidationIssue, ...] = ()
    version: int = Field(default=1, gt=0)

    @classmethod
    def from_issues(
        cls,
        issues: tuple[SemanticValidationIssue, ...],
        *,
        version: int = 1,
    ) -> Self:
        return cls(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            version=version,
        )


class ScreenplaySemanticRevision(FrozenModel):
    revision_id: str = Field(min_length=1)
    parent_revision_id: str | None = None
    episode: int = Field(gt=0)
    source_revision: int = Field(gt=0)
    source_hash: str = Field(min_length=1)
    version: int = Field(default=1, gt=0)
    status: ScreenplaySemanticStatus = "draft"
    scenes: tuple[Scene, ...]
    beats: tuple[DramaticBeat, ...]
    metadata_blocks: tuple[SourceBlock, ...] = ()
    invalidated_beat_ids: tuple[str, ...] = ()
    validation_report: SemanticValidationReport = SemanticValidationReport()
    created_at: AwareDatetime
    activated_at: AwareDatetime | None = None

    @field_validator("created_at", "activated_at", mode="after")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_domain_invariants(self) -> Self:
        scene_ids = [scene.id for scene in self.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("scene ids must be unique")

        scene_ordinals = [scene.ordinal for scene in self.scenes]
        if len(set(scene_ordinals)) != len(scene_ordinals):
            raise ValueError("scene ordinals must be unique")
        if set(scene_ordinals) != set(range(1, len(scene_ordinals) + 1)):
            raise ValueError("scene ordinals must be contiguous")

        ordered_scenes = sorted(self.scenes, key=lambda item: item.ordinal)
        _validate_ordered_ranges(tuple(scene.source_range for scene in ordered_scenes))

        beat_ids = [beat.id for beat in self.beats]
        if len(set(beat_ids)) != len(beat_ids):
            raise ValueError("beat ids must be unique")

        beats_by_scene: defaultdict[str, list[DramaticBeat]] = defaultdict(list)
        for item in self.beats:
            beats_by_scene[item.scene_id].append(item)
        for scene_id, scene_beats in beats_by_scene.items():
            ordinals = [item.ordinal for item in scene_beats]
            if len(set(ordinals)) != len(ordinals):
                raise ValueError("beat ordinals must be unique within a scene")
            if set(ordinals) != set(range(1, len(ordinals) + 1)):
                raise ValueError("beat ordinals must be contiguous within a scene")

        scenes_by_id = {scene.id: scene for scene in self.scenes}
        for item in self.beats:
            owning_scene = scenes_by_id.get(item.scene_id)
            if owning_scene is None:
                raise ValueError("beat must reference an existing scene")
            for source_range in item.source_ranges:
                if (
                    source_range.start_line < owning_scene.source_range.start_line
                    or source_range.end_line > owning_scene.source_range.end_line
                ):
                    raise ValueError("beat source range must belong to its scene")

        if self.status == "active":
            if any(
                issue.severity == "error" for issue in self.validation_report.issues
            ):
                raise ValueError("active revision cannot contain errors")
            if not self.validation_report.passed:
                raise ValueError("active revision requires a passing report")
        return self

    def beats_for(self, scene_id: str) -> tuple[DramaticBeat, ...]:
        return tuple(
            sorted(
                (beat for beat in self.beats if beat.scene_id == scene_id),
                key=lambda item: item.ordinal,
            )
        )

    @classmethod
    def new(
        cls,
        *,
        episode: int,
        source_revision: int,
        source_hash: str,
        scenes: tuple[Scene, ...],
        beats: tuple[DramaticBeat, ...],
        metadata_blocks: tuple[SourceBlock, ...] = (),
        validation_report: SemanticValidationReport = SemanticValidationReport(),
        parent_revision_id: str | None = None,
    ) -> Self:
        return cls(
            revision_id=str(ULID()),
            parent_revision_id=parent_revision_id,
            episode=episode,
            source_revision=source_revision,
            source_hash=source_hash,
            status="draft",
            scenes=scenes,
            beats=beats,
            metadata_blocks=metadata_blocks,
            validation_report=validation_report,
            created_at=datetime.now(timezone.utc),
        )


__all__ = [
    "DramaticBeat",
    "FrozenModel",
    "Scene",
    "SceneSemanticStatus",
    "ScreenplaySemanticRevision",
    "ScreenplaySemanticStatus",
    "SemanticValidationIssue",
    "SemanticValidationReport",
    "SemanticValidationSeverity",
    "SourceBlock",
    "SourceBlockKind",
    "SourceRange",
]
