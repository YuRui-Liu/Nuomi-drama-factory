from __future__ import annotations

from collections.abc import Sequence

import pytest

from novelvideo.episode_graph.models import GraphEntity, GraphEvent, GraphRelation, MergedEpisodeGraph
from novelvideo.episode_graph.writer import EpisodeGraphWriter


class MemoryBackend:
    def __init__(self) -> None:
        self.removed: set[int] | None = None
        self.entities: Sequence[GraphEntity] = ()
        self.events: Sequence[GraphEvent] = ()
        self.relations: Sequence[GraphRelation] = ()
        self.embedding_batch_size: int | None = None

    async def remove_episode_contributions(self, episodes: set[int]) -> None:
        self.removed = episodes

    async def upsert_entities(self, entities: Sequence[GraphEntity]) -> None:
        self.entities = entities

    async def upsert_events(self, events: Sequence[GraphEvent]) -> None:
        self.events = events

    async def upsert_relations(self, relations: Sequence[GraphRelation]) -> None:
        self.relations = relations

    async def index_embeddings(self, *, batch_size: int = 64) -> None:
        self.embedding_batch_size = batch_size


@pytest.mark.asyncio
async def test_writer_replaces_only_affected_sources_and_batches_embeddings():
    backend = MemoryBackend()
    graph = MergedEpisodeGraph(
        entities=[GraphEntity(name="王总", kind="character", source_episodes={1, 2})],
        events=[GraphEvent(episode=2, ordinal=1, description="会面", source_episodes={2})],
        relations=[
            GraphRelation(
                source_key="character:王总",
                relation_type="appears_in",
                target_key="event:2:1",
                episode=2,
                source_episodes={2},
            )
        ],
    )

    await EpisodeGraphWriter(backend).replace_sources({2}, graph)

    assert backend.removed == {2}
    assert backend.entities[0].source_episodes == {1, 2}
    assert len(backend.events) == len(backend.relations) == 1
    assert backend.embedding_batch_size == 64


@pytest.mark.asyncio
async def test_writer_never_requires_or_calls_cognify(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "cognee",
        SimpleNamespace(cognify=lambda *args, **kwargs: pytest.fail("must not cognify")),
    )
    backend = MemoryBackend()
    await EpisodeGraphWriter(backend).replace_sources({2}, MergedEpisodeGraph())
    assert backend.removed == {2}
