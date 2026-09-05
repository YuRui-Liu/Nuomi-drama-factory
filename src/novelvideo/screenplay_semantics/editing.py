"""Immutable manual edits for dramatic beats."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, ValidationError
from ulid import ULID

from novelvideo.screenplay_semantics.models import (
    DramaticBeat, FrozenModel, ScreenplaySemanticRevision, SourceRange,
)


class SemanticEditError(ValueError):
    pass


class SplitBeat(FrozenModel):
    type: Literal["split"] = "split"
    beat_id: str
    before_line: int = Field(gt=0)


class MergeAdjacentBeats(FrozenModel):
    type: Literal["merge"] = "merge"
    first_beat_id: str
    second_beat_id: str


class ReorderBeats(FrozenModel):
    type: Literal["reorder"] = "reorder"
    scene_id: str
    beat_ids: tuple[str, ...] = Field(min_length=1)


class UpdateBeat(FrozenModel):
    type: Literal["update"] = "update"
    beat_id: str
    goal: str | None = None
    obstacle: str | None = None
    action: str | None = None
    reaction: str | None = None
    turn: str | None = None
    result: str | None = None
    emotional_shift: str | None = None
    estimated_duration_seconds: float | None = Field(default=None, gt=0, le=30)
    must_show: tuple[str, ...] | None = None
    script_facts: tuple[str, ...] | None = None
    director_interpretation: tuple[str, ...] | None = None


SemanticEdit = Annotated[
    SplitBeat | MergeAdjacentBeats | ReorderBeats | UpdateBeat,
    Field(discriminator="type"),
]


def _renumber(items: list[DramaticBeat], scene_id: str) -> list[DramaticBeat]:
    ordinal = 0
    result: list[DramaticBeat] = []
    for item in items:
        if item.scene_id == scene_id:
            ordinal += 1
            item = item.model_copy(update={"ordinal": ordinal})
        result.append(item)
    return result


def apply_semantic_edit(
    revision: ScreenplaySemanticRevision, command: SemanticEdit
) -> ScreenplaySemanticRevision:
    beats = list(revision.beats)
    invalidated: tuple[str, ...]

    if isinstance(command, SplitBeat):
        index = next((i for i, item in enumerate(beats) if item.id == command.beat_id), -1)
        if index < 0:
            raise SemanticEditError("beat not found")
        original = beats[index]
        if len(original.source_ranges) != 1:
            raise SemanticEditError("split currently requires one contiguous source range")
        source = original.source_ranges[0]
        if not source.start_line < command.before_line <= source.end_line:
            raise SemanticEditError("split line must be inside the beat evidence range")
        left = original.model_copy(update={
            "id": f"{original.id}-a",
            "source_ranges": (SourceRange(start_line=source.start_line, end_line=command.before_line - 1),),
        })
        right = original.model_copy(update={
            "id": f"{original.id}-b", "ordinal": original.ordinal + 1,
            "source_ranges": (SourceRange(start_line=command.before_line, end_line=source.end_line),),
        })
        beats[index:index + 1] = [left, right]
        beats = _renumber(beats, original.scene_id)
        invalidated = (original.id,)
    elif isinstance(command, MergeAdjacentBeats):
        first_index = next((i for i, item in enumerate(beats) if item.id == command.first_beat_id), -1)
        second_index = next((i for i, item in enumerate(beats) if item.id == command.second_beat_id), -1)
        if first_index < 0 or second_index != first_index + 1:
            raise SemanticEditError("beats must be adjacent")
        first, second = beats[first_index], beats[second_index]
        if first.scene_id != second.scene_id:
            raise SemanticEditError("beats must belong to the same scene")
        merged = first.model_copy(update={
            "id": f"{first.id}-merged",
            "source_ranges": tuple(sorted(
                first.source_ranges + second.source_ranges,
                key=lambda item: (item.start_line, item.end_line),
            )),
            "characters": tuple(dict.fromkeys(first.characters + second.characters)),
            "must_show": first.must_show + second.must_show,
            "script_facts": first.script_facts + second.script_facts,
            "dialogue_source_ids": first.dialogue_source_ids + second.dialogue_source_ids,
            "estimated_duration_seconds": min(30, first.estimated_duration_seconds + second.estimated_duration_seconds),
            "result": second.result, "emotional_shift": second.emotional_shift,
        })
        beats[first_index:second_index + 1] = [merged]
        beats = _renumber(beats, first.scene_id)
        invalidated = (first.id, second.id)
    elif isinstance(command, ReorderBeats):
        scene_items = [item for item in beats if item.scene_id == command.scene_id]
        if set(command.beat_ids) != {item.id for item in scene_items}:
            raise SemanticEditError("reorder must contain every beat in the scene exactly once")
        by_id = {item.id: item for item in scene_items}
        ordered = [by_id[item_id] for item_id in command.beat_ids]
        iterator = iter(ordered)
        beats = [next(iterator) if item.scene_id == command.scene_id else item for item in beats]
        beats = _renumber(beats, command.scene_id)
        invalidated = command.beat_ids
    elif isinstance(command, UpdateBeat):
        index = next((i for i, item in enumerate(beats) if item.id == command.beat_id), -1)
        if index < 0:
            raise SemanticEditError("beat not found")
        updates = command.model_dump(exclude={"type", "beat_id"}, exclude_none=True)
        beats[index] = beats[index].model_copy(update=updates)
        invalidated = (command.beat_id,)
    else:  # pragma: no cover - discriminated union keeps this unreachable
        raise SemanticEditError("unsupported semantic edit")

    payload = revision.model_dump(mode="python")
    payload.update({
        "revision_id": str(ULID()), "parent_revision_id": revision.revision_id,
        "status": "review_required", "beats": tuple(beats),
        "invalidated_beat_ids": invalidated,
        "created_at": datetime.now(timezone.utc), "activated_at": None,
    })
    try:
        return ScreenplaySemanticRevision.model_validate(payload)
    except ValidationError as exc:
        raise SemanticEditError(f"semantic edit produced invalid evidence: {exc}") from exc


__all__ = ["MergeAdjacentBeats", "ReorderBeats", "SemanticEdit", "SemanticEditError", "SplitBeat", "UpdateBeat", "apply_semantic_edit"]
