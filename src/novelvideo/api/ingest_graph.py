"""Complete structured knowledge graph and revisioned, non-production annotations.

Annotations deliberately never rename or mutate screenplay/production assets.
Their SQLite transaction is independent of the production store.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.utils.screenplay_scene_parser import parse_scene_blocks


class NodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=2048)
    label: str | None = Field(default=None, min_length=1, max_length=500)
    properties: dict[str, JsonValue] | None = None

    @field_validator("label")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("label must not be blank")
        return value


class EdgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=2048)
    relation: str | None = Field(default=None, min_length=1, max_length=500)
    source: str | None = Field(default=None, min_length=1, max_length=2048)
    target: str | None = Field(default=None, min_length=1, max_length=2048)
    properties: dict[str, JsonValue] | None = None

    @field_validator("relation")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("relation must not be blank")
        return value


class GraphPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(min_length=1, max_length=128)
    node_updates: list[NodeUpdate] = Field(default_factory=list, max_length=500)
    edge_updates: list[EdgeUpdate] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def bounded_json(self):
        if len(_json(self.model_dump()).encode()) > 256_000:
            raise ValueError("图谱更新超过 256KB 上限")
        return self


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _finish(nodes, edges):
    degree = dict.fromkeys(nodes, 0)
    for edge in edges.values():
        for endpoint in set((edge["source"], edge["target"])):
            degree[endpoint] += 1
    for key, node in nodes.items():
        node["degree"] = degree[key]
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "total_nodes": len(nodes), "total_edges": len(edges), "truncated": False}


async def build_structured_graph(store: Any, *, sources=None) -> dict[str, Any]:
    """Use explicit memberships and parsed scene metadata, never invent relations."""
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def node(kind, name, properties=None):
        key = f"{kind}:{name}"
        nodes.setdefault(key, {"id": key, "label": str(name), "type": kind, "properties": {}})
        nodes[key]["properties"].update(properties or {})
        return key

    def edge(source, target, relation, properties=None):
        key = _digest([source, target, relation])
        edges.setdefault(key, {"id": key, "source": source, "target": target, "relation": relation, "properties": properties or {}})

    characters = await store.list_characters()
    scenes = await store.list_scenes()
    props = await store.list_props()
    episodes = await store.list_episodes()
    aliases = {}
    for kind, assets in (("Character", characters), ("Scene", scenes), ("Prop", props)):
        for asset in assets:
            node(kind, asset.name, {**asset.model_dump(mode="json"), "provenance": "production_asset"})
            for alias in getattr(asset, "aliases", []):
                aliases[(kind, alias)] = asset.name

    def ref(kind, name):
        return node(kind, aliases.get((kind, name), name))

    if sources is None:
        sources = await EpisodeSourceStore(store).list_sources()
    source_by_number = {source.episode_number: source for source in sources}
    episode_by_number = {episode.number: episode for episode in episodes}
    for number in sorted(set(source_by_number) | set(episode_by_number)):
        episode = episode_by_number.get(number)
        source = source_by_number.get(number)
        data = episode.model_dump(mode="json") if episode else {}
        text = source.content if source else (getattr(episode, "raw_content", "") or "")
        # Keep hashes rather than large screenplay payloads in graph attributes.
        for key in ("raw_content", "adapted_content", "beat_source_text"):
            data.pop(key, None)
        data.update(number=number, source_hash=_digest(text), provenance="episode_source")
        if source:
            data["source_revision"] = source.source_revision
        episode_id = node("Episode", number, data)
        nodes[episode_id]["label"] = (source.title if source else episode.title) or f"第{number}集"
        for name in getattr(episode, "character_names", []):
            edge(episode_id, ref("Character", name), "HAS_CHARACTER")
        for kind, field, name_key, relation in (("Scene", "scene_menu_json", "scene_id", "HAS_SCENE"), ("Prop", "prop_menu_json", "prop_id", "HAS_PROP")):
            for item in json.loads(getattr(episode, field, "[]") or "[]"):
                name = item.get(name_key) if isinstance(item, dict) else item
                if name:
                    edge(episode_id, ref(kind, name), relation)
        for block in parse_scene_blocks(text):
            scene_id = ref("Scene", block.location) if block.location else None
            if scene_id:
                edge(episode_id, scene_id, "HAS_SCENE", {"provenance": "screenplay_header"})
            for name in block.characters:
                character_id = ref("Character", name)
                edge(episode_id, character_id, "HAS_CHARACTER", {"provenance": "screenplay_header"})
                if scene_id:
                    edge(character_id, scene_id, "APPEARS_IN", {"provenance": "screenplay_header"})
    for prop in props:
        if prop.owner:
            edge(ref("Character", prop.owner), ref("Prop", prop.name), "OWNS")
    return _finish(nodes, edges)


def _connect(state_dir):
    path = Path(state_dir) / "knowledge_graph_annotations.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS graph_annotations (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, payload TEXT NOT NULL)")
    db.commit()
    return db


def _load(db):
    row = db.execute("SELECT version, payload FROM graph_annotations WHERE id=1").fetchone()
    return (row[0], json.loads(row[1])) if row else (0, {"nodes": {}, "edges": {}})


def _snapshot(base, version, overrides):
    graph = copy.deepcopy(base)
    node_ids = {node["id"] for node in graph["nodes"]}
    for kind in ("nodes", "edges"):
        for item in graph[kind]:
            update = overrides[kind].get(item["id"], {})
            # A later source rebuild can remove an annotated endpoint.
            if kind == "edges" and any(update.get(key, item[key]) not in node_ids for key in ("source", "target")):
                update = {key: value for key, value in update.items() if key not in ("source", "target")}
            for key, value in update.items():
                if key == "properties":
                    for prop, content in value.items():
                        if content is None:
                            item["properties"].pop(prop, None)
                        else:
                            item["properties"][prop] = content
                else:
                    item[key] = value
    graph.update(_finish({node["id"]: node for node in graph["nodes"]}, {edge["id"]: edge for edge in graph["edges"]}))
    graph.update(revision=_digest([base, version]), editable=True, edit_semantics="graph_annotations", knowledge_pipeline="structured_v1")
    return graph


def read_graph(state_dir: str | Path, base: dict[str, Any]) -> dict[str, Any]:
    db = _connect(state_dir)
    try:
        version, overrides = _load(db)
        return _snapshot(base, version, overrides)
    finally:
        db.close()


def update_graph(
    state_dir: str | Path, base: dict[str, Any], patch: GraphPatch
) -> dict[str, Any]:
    db = _connect(state_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        version, overrides = _load(db)
        current = _snapshot(base, version, overrides)
        if patch.revision != current["revision"]:
            raise HTTPException(409, "图谱已更新，请刷新后重试")
        node_ids = {item["id"] for item in base["nodes"]}
        for kind, updates in (("nodes", patch.node_updates), ("edges", patch.edge_updates)):
            known_ids = {item["id"] for item in base[kind]}
            seen = set()
            for update in updates:
                if update.id not in known_ids or update.id in seen:
                    raise HTTPException(422, "图谱节点或关系不存在，或重复更新")
                seen.add(update.id)
                fields = update.model_dump(exclude_none=True, exclude={"id"})
                if kind == "edges" and any(fields[key] not in node_ids for key in ("source", "target") if key in fields):
                    raise HTTPException(422, "关系端点不存在")
                existing = overrides[kind].setdefault(update.id, {})
                properties = fields.pop("properties", None)
                existing.update(fields)
                if properties is not None:
                    existing.setdefault("properties", {}).update(properties)
        db.execute("INSERT INTO graph_annotations VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET version=excluded.version, payload=excluded.payload", (version + 1, _json(overrides)))
        result = _snapshot(base, version + 1, overrides)
        db.commit()
        return result
    finally:
        db.close()
