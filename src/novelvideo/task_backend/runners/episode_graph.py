"""Background episode graph indexing without Cognee's cognify pipeline."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from cognee.infrastructure.engine import DataPoint
from cognee.infrastructure.engine.models.FieldAnnotations import Embeddable

from novelvideo.episode_graph.checkpoints import EpisodeGraphCheckpointStore
from novelvideo.episode_graph.models import EpisodeGraphSource
from novelvideo.episode_graph.service import EpisodeGraphBuildService
from novelvideo.episode_import_service import CogneeShadowBuild, CogneeShadowGraph
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.knowledge_runtime.context import (
    build_project_knowledge_runtime,
    knowledge_runtime_scope,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


class EpisodeGraphPoint(DataPoint):
    text: Annotated[str, Embeddable()]
    kind: str
    name: str
    attributes_json: str
    source_episodes_json: str


def _node_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"novelvideo:episode-graph:{key}"))


class CogneeGraphCandidate:
    """Concrete Cognee graph/vector writer bound to one candidate runtime."""

    def __init__(self, *, shadow: CogneeShadowBuild, store: Any, graph: Any, vector: Any) -> None:
        self.shadow = shadow
        self._store = store
        self._graph = graph
        self._vector = vector
        self._points: list[EpisodeGraphPoint] = []

    async def remove_episode_contributions(self, episodes: set[int]) -> None:
        # Candidates are revision-unique and always start empty.
        if not await self._graph.is_empty():
            await self._graph.delete_graph()

    async def upsert_entities(self, entities) -> None:
        nodes = []
        for entity in entities:
            key = f"entity:{entity.kind}:{entity.name}"
            identifier = _node_id(key)
            props = {
                "name": entity.name,
                "type": entity.kind,
                "attributes_json": json.dumps(entity.attributes, ensure_ascii=False, sort_keys=True),
                "source_episodes_json": json.dumps(sorted(entity.source_episodes)),
            }
            point = EpisodeGraphPoint(
                id=UUID(identifier),
                text=entity.name,
                kind=entity.kind,
                name=entity.name,
                attributes_json=props["attributes_json"],
                source_episodes_json=props["source_episodes_json"],
            )
            nodes.append(point)
            self._points.append(point)
        if nodes:
            await self._graph.add_nodes(nodes)

    async def upsert_events(self, events) -> None:
        nodes = []
        for event in events:
            key = f"event:{event.episode}:{event.ordinal}"
            identifier = _node_id(key)
            props = {
                "name": event.description,
                "type": "event",
                "episode": event.episode,
                "ordinal": event.ordinal,
                "attributes_json": json.dumps(event.attributes, ensure_ascii=False, sort_keys=True),
                "source_episodes_json": json.dumps(sorted(event.source_episodes)),
            }
            point = EpisodeGraphPoint(
                id=UUID(identifier),
                text=event.description,
                kind="event",
                name=event.description,
                attributes_json=props["attributes_json"],
                source_episodes_json=props["source_episodes_json"],
            )
            nodes.append(point)
            self._points.append(point)
        if nodes:
            await self._graph.add_nodes(nodes)

    async def upsert_relations(self, relations) -> None:
        edges = [
            (
                _node_id(relation.source_key),
                _node_id(relation.target_key),
                relation.relation_type,
                {
                    "episode": relation.episode,
                    "attributes_json": json.dumps(relation.attributes, ensure_ascii=False, sort_keys=True),
                    "source_episodes_json": json.dumps(sorted(relation.source_episodes)),
                },
            )
            for relation in relations
        ]
        if edges:
            await self._graph.add_edges(edges)

    async def index_embeddings(self, *, batch_size: int = 64) -> None:
        collection = EpisodeGraphPoint.__name__
        if not await self._vector.has_collection(collection):
            await self._vector.create_collection(collection, EpisodeGraphPoint)
        for offset in range(0, len(self._points), batch_size):
            await self._vector.create_data_points(
                collection, self._points[offset : offset + batch_size]
            )

    async def commit_embedding_binding(self) -> None:
        """Persist the binding only after this isolated candidate is ready."""
        from novelvideo.project_config import (
            commit_cognee_embedding_binding_in_state_dir,
        )

        binding = getattr(self._store, "cognee_embedding_binding", None)
        if binding is not None and binding.provider == "ollama":
            commit_cognee_embedding_binding_in_state_dir(
                self._store.state_dir,
                binding,
            )

    async def close(self) -> None:
        result = self._store.close()
        if hasattr(result, "__await__"):
            await result


class CogneeCandidateManager:
    def __init__(self, ctx: ProjectContext) -> None:
        self._ctx = ctx

    async def create_candidate(self, target_revision: int) -> CogneeGraphCandidate:
        from cognee.infrastructure.databases.graph import get_graph_engine
        from cognee.infrastructure.databases.vector import get_vector_engine
        from novelvideo.cognee.store import CogneeStore

        build_dir = Path(self._ctx.state_dir) / "cognee_builds" / f"{target_revision}-{uuid4().hex}"
        runtime_dir = build_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        store = CogneeStore.for_explicit_runtime(
            self._ctx.owner_project_label,
            project_dir=self._ctx.output_dir,
            state_dir=self._ctx.state_dir,
            cognee_runtime_dir=runtime_dir,
        )
        # This runtime is revision-unique and empty.  Rebinding it cannot mix
        # vectors from the previously active index.
        store.knowledge_rebuild_requested = True
        await store.initialize()
        graph = await get_graph_engine()
        vector = get_vector_engine()
        return CogneeGraphCandidate(
            shadow=CogneeShadowBuild(target_revision, build_dir, runtime_dir),
            store=store,
            graph=graph,
            vector=vector,
        )

    async def discard_candidate(self, candidate: CogneeGraphCandidate) -> None:
        await candidate.close()


async def _build_repository(ctx: ProjectContext) -> EpisodeSourceStore:
    from novelvideo.api.deps import make_sqlite_store_for_context

    return EpisodeSourceStore(await make_sqlite_store_for_context(ctx))


async def _build_graph_service(ctx: ProjectContext) -> EpisodeGraphBuildService:
    return EpisodeGraphBuildService(
        checkpoints=EpisodeGraphCheckpointStore(Path(ctx.state_dir).parent),
        candidates=CogneeCandidateManager(ctx),
    )


def _build_activation_graph(ctx: ProjectContext) -> CogneeShadowGraph:
    return CogneeShadowGraph(
        project_name=ctx.owner_project_label,
        project_dir=ctx.output_dir,
        state_dir=ctx.state_dir,
    )


async def _run_episode_graph_index(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    target_revision = int(payload["target_revision"])
    repository = await _build_repository(ctx)
    if await repository.current_revision() != target_revision:
        raise RuntimeError("EPISODE_GRAPH_REVISION_STALE")
    sources = [
        EpisodeGraphSource(
            number=item.episode_number,
            title=item.title,
            content=item.content,
            source_revision=target_revision,
        )
        for item in await repository.list_sources()
    ]
    manager = get_task_manager()
    scope = str(envelope.get("scope") or f"revision:{target_revision}")
    completed = 0
    total = max((len(sources) + 4) // 5, 1)

    def on_group_event(event: str, group: Any) -> None:
        nonlocal completed
        if event in {"completed", "checkpoint"}:
            completed += 1
        progress = min(0.1 + 0.75 * completed / total, 0.85)
        message = f"图谱分组 {group.key}: {event} ({completed}/{total})"
        manager.update_progress_for_project(
            ctx, "episode_graph_index", 0, scope=scope, progress=progress,
            current_task=message, logs=[message],
            expected_task_id=str(envelope.get("__run_task_id") or "") or None,
        )

    service = await _build_graph_service(ctx)
    result = await service.build(
        sources, target_revision=target_revision, on_group_event=on_group_event
    )
    activation_graph = _build_activation_graph(ctx)
    activation = await activation_graph.activate_shadow(result.candidate.shadow)
    try:
        await result.candidate.commit_embedding_binding()
        await activation_graph.finalize_activation(activation)
    except BaseException:
        await activation_graph.restore_active(activation)
        raise
    await result.candidate.close()
    await repository.delete_graph_outbox(target_revision)
    return {
        "target_revision": target_revision,
        "group_count": result.group_count,
        "entity_count": len(result.graph.entities),
        "event_count": len(result.graph.events),
        "relation_count": len(result.graph.relations),
    }


async def _run_with_runtime(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    runtime = build_project_knowledge_runtime(ctx)
    with knowledge_runtime_scope(runtime):
        return await _run_episode_graph_index(envelope, ctx)


def run_episode_graph_index(envelope: dict[str, Any], ctx: ProjectContext):
    return asyncio.run(await_envelope_with_cancel_watch(
        _run_with_runtime(envelope, ctx), envelope, task_type="episode_graph_index"
    ))


register_project_task_runner(
    "episode_graph_index", run_episode_graph_index, text_task_role="knowledge_extraction"
)
