import unicodedata
import json
from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

from .models import (
    EpisodeGraphExtraction,
    ConflictValues,
    GraphEntity,
    GraphEvent,
    GraphRelation,
    MergedEpisodeGraph,
)

Item = TypeVar("Item", GraphEntity, GraphEvent, GraphRelation)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _display(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _value_key(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_items(
    items: Sequence[Item], key: Callable[[Item], Hashable]
) -> tuple[list[Item], int]:
    merged: dict[Hashable, Item] = {}
    conflicts = 0
    ordered = sorted(items, key=lambda item: (_value_key(key(item)), _value_key(item)))
    for item in ordered:
        stable_key = key(item)
        if stable_key not in merged:
            merged[stable_key] = item.model_copy(deep=True)
            if isinstance(merged[stable_key], GraphEntity):
                merged[stable_key].name = _display(merged[stable_key].name)
            continue
        target = merged[stable_key]
        target.source_episodes.update(item.source_episodes)
        if isinstance(target, GraphEvent) and target.description != item.description:
            target.description = min(target.description, item.description, key=_value_key)
            conflicts += 1
        for name, value in item.attributes.items():
            if name not in target.attributes:
                target.attributes[name] = value
                continue
            current = target.attributes[name]
            values = current.values if isinstance(current, ConflictValues) else [current]
            if all(_value_key(value) != _value_key(existing) for existing in values):
                target.attributes[name] = ConflictValues(
                    values=sorted([*values, value], key=_value_key)
                )
                conflicts += 1
    return [merged[item_key] for item_key in sorted(merged, key=_value_key)], conflicts


def merge_extractions(extractions: Sequence[EpisodeGraphExtraction]) -> MergedEpisodeGraph:
    entities, entity_conflicts = _merge_items(
        [item for result in extractions for item in result.entities],
        lambda item: (item.kind, _normalize(item.name)),
    )
    events, event_conflicts = _merge_items(
        [item for result in extractions for item in result.events],
        lambda item: (item.episode, item.ordinal),
    )
    relations, relation_conflicts = _merge_items(
        [item for result in extractions for item in result.relations],
        lambda item: (
            _normalize(item.source_key),
            _normalize(item.relation_type),
            _normalize(item.target_key),
            item.episode,
        ),
    )
    return MergedEpisodeGraph(
        entities=entities,
        events=events,
        relations=relations,
        conflict_count=entity_conflicts + event_conflicts + relation_conflicts,
    )
