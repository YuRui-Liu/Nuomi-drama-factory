from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .models import GraphEntity, GraphEvent, GraphRelation, MergedEpisodeGraph


class GraphWriteBackend(Protocol):
    """Provider-neutral port for writing an already extracted candidate graph."""

    async def remove_episode_contributions(self, episodes: set[int]) -> None: ...

    async def upsert_entities(self, entities: Sequence[GraphEntity]) -> None: ...

    async def upsert_events(self, events: Sequence[GraphEvent]) -> None: ...

    async def upsert_relations(self, relations: Sequence[GraphRelation]) -> None: ...

    async def index_embeddings(self, *, batch_size: int = 64) -> None: ...


class EpisodeGraphWriter:
    """Writes structured graph data directly; this layer never runs a text LLM."""

    def __init__(self, backend: GraphWriteBackend, *, embedding_batch_size: int = 64) -> None:
        if embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be positive")
        self._backend = backend
        self._embedding_batch_size = embedding_batch_size

    async def replace_sources(
        self, affected_episodes: set[int], graph: MergedEpisodeGraph
    ) -> None:
        await self._backend.remove_episode_contributions(set(affected_episodes))
        await self._backend.upsert_entities(graph.entities)
        await self._backend.upsert_events(graph.events)
        await self._backend.upsert_relations(graph.relations)
        await self._backend.index_embeddings(batch_size=self._embedding_batch_size)
