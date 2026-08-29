"""Validated structured extraction models and atomic formal-asset publication."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

from novelvideo.models import NovelCharacter, NovelEpisode, NovelScene


EntityKind = Literal["episode", "character", "scene"]


@dataclass(frozen=True, slots=True)
class StructuredSourceRef:
    episode_number: int
    source_start: int
    source_end: int
    quote: str


@dataclass(frozen=True, slots=True)
class StructuredEpisodeInput:
    number: int
    title: str
    raw_content: str
    summary: str = ""
    character_ids: tuple[str, ...] = ()
    scene_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuredCharacterInput:
    name: str
    aliases: tuple[str, ...] = ()
    role: str = ""
    face: str = ""
    build: str = ""
    gender: str = ""
    description: str = ""
    source_refs: tuple[StructuredSourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuredSceneInput:
    name: str
    location: str
    time: str = ""
    environment: str = ""
    spatial_anchors: tuple[str, ...] = ()
    source_refs: tuple[StructuredSourceRef, ...] = ()
    aliases: tuple[str, ...] = ()
    scene_type: str = "interior"


@dataclass(frozen=True, slots=True)
class PublishedEpisode:
    logical_id: str
    model: NovelEpisode


@dataclass(frozen=True, slots=True)
class PublishedCharacter:
    logical_id: str
    model: NovelCharacter
    source_refs: tuple[StructuredSourceRef, ...]


@dataclass(frozen=True, slots=True)
class PublishedScene:
    logical_id: str
    location: str
    model: NovelScene
    source_refs: tuple[StructuredSourceRef, ...]


@dataclass(frozen=True, slots=True)
class StructuredPublication:
    episodes: tuple[PublishedEpisode, ...] = field(default_factory=tuple)
    characters: tuple[PublishedCharacter, ...] = field(default_factory=tuple)
    scenes: tuple[PublishedScene, ...] = field(default_factory=tuple)


def _normalized_identity_part(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = " ".join(text.strip().casefold().split())
    return re.sub(r"[^\w\u3400-\u9fff]+", "-", text).strip("-")


def stable_logical_id(kind: EntityKind, *parts: object) -> str:
    """Return a stable semantic ID that does not change with enriched fields."""
    if kind == "episode" and parts:
        try:
            number = int(parts[0])
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            return f"episode:{number:04d}"
    normalized = "\x1f".join(_normalized_identity_part(part) for part in parts)
    if not normalized.replace("\x1f", ""):
        raise ValueError(f"{kind} logical ID requires a non-empty semantic key")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{digest}"


def _require_unique(values: Iterable[object], key, label: str) -> None:
    seen: set[object] = set()
    for value in values:
        identity = key(value)
        if identity in seen:
            raise ValueError(f"duplicate {label}: {identity}")
        seen.add(identity)


def _validate_ref(
    reference: StructuredSourceRef,
    episodes: dict[int, StructuredEpisodeInput],
) -> None:
    episode = episodes.get(reference.episode_number)
    if episode is None:
        raise ValueError("source ref episode does not exist")
    start = int(reference.source_start)
    end = int(reference.source_end)
    if start < 0 or end <= start or end > len(episode.raw_content):
        raise ValueError("source ref offsets are outside the episode")
    if episode.raw_content[start:end] != reference.quote:
        raise ValueError("source ref quote does not match the episode source")


def _refs_payload(refs: tuple[StructuredSourceRef, ...]) -> list[dict[str, Any]]:
    return [asdict(reference) for reference in refs]


def build_structured_publication(
    *,
    episodes: Iterable[StructuredEpisodeInput],
    characters: Iterable[StructuredCharacterInput],
    scenes: Iterable[StructuredSceneInput],
) -> StructuredPublication:
    """Validate a complete extraction before any formal row is touched."""
    episode_inputs = tuple(episodes)
    character_inputs = tuple(characters)
    scene_inputs = tuple(scenes)
    _require_unique(episode_inputs, lambda item: item.number, "episode number")
    _require_unique(character_inputs, lambda item: item.name, "character name")
    _require_unique(
        scene_inputs,
        lambda item: stable_logical_id("scene", item.location, item.time),
        "scene logical ID",
    )
    scene_name_counts = Counter(item.name.strip() for item in scene_inputs)

    episode_by_number = {item.number: item for item in episode_inputs}
    if any(number <= 0 for number in episode_by_number):
        raise ValueError("episode number must be positive")
    if any(not item.raw_content.strip() for item in episode_inputs):
        raise ValueError("episode source must not be empty")

    published_characters: list[PublishedCharacter] = []
    characters_by_id: dict[str, PublishedCharacter] = {}
    for item in character_inputs:
        if not item.name.strip():
            raise ValueError("character name must not be empty")
        for reference in item.source_refs:
            _validate_ref(reference, episode_by_number)
        logical_id = stable_logical_id("character", item.name)
        published = PublishedCharacter(
            logical_id=logical_id,
            model=NovelCharacter(
                name=item.name.strip(),
                aliases=list(item.aliases),
                role=item.role.strip(),
                gender=item.gender.strip(),
                body_type=item.build.strip(),
                description=item.description.strip(),
                face_prompt=item.face.strip(),
            ),
            source_refs=item.source_refs,
        )
        characters_by_id[logical_id] = published
        published_characters.append(published)

    published_scenes: list[PublishedScene] = []
    scenes_by_id: dict[str, PublishedScene] = {}
    for item in scene_inputs:
        if not item.name.strip() or not item.location.strip():
            raise ValueError("scene name and location must not be empty")
        for reference in item.source_refs:
            _validate_ref(reference, episode_by_number)
        logical_id = stable_logical_id("scene", item.location, item.time)
        storage_name = item.name.strip()
        if scene_name_counts[storage_name] > 1:
            discriminator = item.time.strip() or item.location.strip()
            if discriminator and discriminator != storage_name:
                storage_name = f"{storage_name}·{discriminator}"
        notes = json.dumps(
            {
                "structured_logical_id": logical_id,
                "location": item.location.strip(),
                "spatial_anchors": list(item.spatial_anchors),
                "source_refs": _refs_payload(item.source_refs),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        published = PublishedScene(
            logical_id=logical_id,
            location=item.location.strip(),
            model=NovelScene(
                name=storage_name,
                aliases=list(item.aliases),
                scene_type=item.scene_type.strip() or "interior",
                time_of_day=item.time.strip(),
                environment_prompt=item.environment.strip(),
                description=item.environment.strip(),
                notes=notes,
            ),
            source_refs=item.source_refs,
        )
        scenes_by_id[logical_id] = published
        published_scenes.append(published)

    published_episodes: list[PublishedEpisode] = []
    for item in episode_inputs:
        unknown_characters = set(item.character_ids) - set(characters_by_id)
        unknown_scenes = set(item.scene_ids) - set(scenes_by_id)
        if unknown_characters:
            raise ValueError("episode references an unknown character logical ID")
        if unknown_scenes:
            raise ValueError("episode references an unknown scene logical ID")
        character_names = [characters_by_id[value].model.name for value in item.character_ids]
        scene_menu = [
            {
                "scene_id": scenes_by_id[value].model.name,
                "time_of_day": scenes_by_id[value].model.time_of_day,
            }
            for value in item.scene_ids
        ]
        published_episodes.append(
            PublishedEpisode(
                logical_id=stable_logical_id("episode", item.number),
                model=NovelEpisode(
                    number=item.number,
                    title=item.title.strip(),
                    raw_content=item.raw_content,
                    beat_source_text=item.raw_content,
                    content_summary=item.summary.strip(),
                    character_names=character_names,
                    scene_menu=scene_menu,
                ),
            )
        )

    return StructuredPublication(
        episodes=tuple(published_episodes),
        characters=tuple(published_characters),
        scenes=tuple(published_scenes),
    )


async def _write_episode(db: Any, episode: NovelEpisode) -> None:
    await db.execute(
        """INSERT INTO episodes (
        number, title, raw_content, beat_source_text, content_summary,
        character_names, scene_menu_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(number) DO UPDATE SET
        title=excluded.title, raw_content=excluded.raw_content,
        beat_source_text=excluded.beat_source_text,
        content_summary=excluded.content_summary,
        character_names=excluded.character_names,
        scene_menu_json=excluded.scene_menu_json,
        updated_at=datetime('now')""",
        (
            episode.number,
            episode.title,
            episode.raw_content,
            episode.beat_source_text,
            episode.content_summary,
            json.dumps(episode.character_names, ensure_ascii=False),
            episode.scene_menu_json,
        ),
    )


async def _write_character(db: Any, character: NovelCharacter) -> int:
    cursor = await db.execute(
        """INSERT INTO characters (
        name, aliases_json, role, is_main, gender, age_group, body_type,
        description, face_prompt, appearance_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO NOTHING""",
        (
            character.name,
            json.dumps(character.aliases, ensure_ascii=False),
            character.role,
            int(character.is_main),
            character.gender,
            character.age_group,
            character.body_type,
            character.description,
            character.face_prompt,
            character.appearance_details,
        ),
    )
    return int(cursor.rowcount or 0)


async def _write_scene(db: Any, scene: NovelScene) -> None:
    await db.execute(
        """INSERT INTO scenes (
        name, aliases_json, scene_type, base_scene_id, variant_id, time_of_day,
        environment_prompt, variant_prompt, description, spatial_layout_image, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO NOTHING""",
        (
            scene.name,
            json.dumps(scene.aliases, ensure_ascii=False),
            scene.scene_type,
            scene.base_scene_id,
            scene.variant_id,
            scene.time_of_day,
            scene.environment_prompt,
            scene.variant_prompt,
            scene.description,
            scene.spatial_layout_image,
            scene.notes,
        ),
    )


async def publish_structured_publication(
    store: Any, publication: StructuredPublication, *, run_id: str | None = None
) -> dict[str, int]:
    """Publish all formal rows in one SQLite transaction or leave all unchanged."""
    db = await store._ensure_db()
    await db.execute("BEGIN IMMEDIATE")
    try:
        for item in publication.episodes:
            await _write_episode(db, item.model)
        for item in publication.characters:
            await _write_character(db, item.model)
        for item in publication.scenes:
            await _write_scene(db, item.model)
        if run_id:
            from novelvideo.structured_evidence import write_publication_evidence
            await write_publication_evidence(db, run_id=run_id, publication=publication)
        await db.commit()
    except BaseException:
        await db.rollback()
        await store.load_graph_state()
        raise
    await store.load_graph_state()
    return {
        "episodes": len(publication.episodes),
        "characters": len(publication.characters),
        "scenes": len(publication.scenes),
    }


PROP_BUILD_DEFERRED_MESSAGE = "道具将在分集规划时按需生成"
SCENE_BUILD_DEFERRED_MESSAGE = "解说剧场景将在分集规划时按需生成"


def spine_template_for(store: Any) -> str:
    from novelvideo.project_config import load_project_config_file_from_state_dir

    config = load_project_config_file_from_state_dir(store.state_dir)
    return str(config.get("spine_template") or "drama").strip()


def _report(callback: Any, progress: float, task: str) -> None:
    if callback:
        callback(progress, task)


def _log(callback: Any, message: str) -> None:
    if callback:
        callback(message)


async def build_characters_structured(
    store: Any,
    *,
    on_progress: Any = None,
    on_log: Any = None,
) -> list[str]:
    """Extract source-bound characters and atomically add only missing rows."""
    from novelvideo.novel_source import require_imported_novel
    from novelvideo.story_analysis import chunk_source_text, source_sha256
    from novelvideo.structured_extraction import extract_characters_from_chunks
    from novelvideo.structured_ingest import (
        STRUCTURED_PIPELINE_VERSION,
        STRUCTURED_SCHEMA_VERSION,
    )

    text = require_imported_novel(store.project_dir)
    template = spine_template_for(store)
    chunks = chunk_source_text(text, template)
    if not chunks:
        raise ValueError("原文切分结果为空，无法构建角色")
    identity = f"{source_sha256(text)}:{STRUCTURED_SCHEMA_VERSION}:{template}"
    run_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    reusable = await store.get_reusable_analysis_run(
        source_sha256=source_sha256(text),
        schema_version=int(STRUCTURED_SCHEMA_VERSION),
        pipeline_version=STRUCTURED_PIPELINE_VERSION,
        spine_template=template,
    )
    merged = []
    if reusable and reusable.get("status") == "completed":
        artifact = await store.get_analysis_artifact(reusable["run_id"], "characters")
        if artifact:
            from novelvideo.structured_extraction import MergedCharacter

            merged = [
                MergedCharacter(
                    name=item["name"],
                    aliases=set(item.get("aliases") or []),
                    role=item.get("role", ""),
                    face=item.get("face", ""),
                    build=item.get("build", ""),
                    gender=item.get("gender", ""),
                    description=item.get("description", ""),
                    evidence=list(item.get("evidence") or []),
                    chunk_ids=set(item.get("chunk_ids") or []),
                )
                for item in json.loads(artifact)
            ]
            run_id = reusable["run_id"]
    if not merged:
        await store.start_analysis_run(
            run_id=run_id,
            pipeline_version=STRUCTURED_PIPELINE_VERSION,
            schema_version=int(STRUCTURED_SCHEMA_VERSION),
            spine_template=template,
            source_sha256=source_sha256(text),
            source_length=len(text),
            chunks=chunks,
        )
        _report(on_progress, 0.1, "从原文片段提取角色...")
        try:
            extracted = extract_characters_from_chunks(chunks, on_log=on_log)
            merged = await extracted if inspect.isawaitable(extracted) else extracted
            await store.save_analysis_artifact(
                run_id,
                "characters",
                json.dumps(
                    [
                        {
                            "name": item.name,
                            "aliases": sorted(item.aliases),
                            "role": item.role,
                            "face": item.face,
                            "build": item.build,
                            "gender": item.gender,
                            "description": item.description,
                            "evidence": item.evidence,
                            "chunk_ids": sorted(item.chunk_ids),
                        }
                        for item in merged
                    ],
                    ensure_ascii=False,
                ),
            )
        except BaseException as exc:
            await store.finish_analysis_run(run_id, status="failed", error=str(exc))
            raise

    candidates = [
        NovelCharacter(
            name=item.name,
            aliases=sorted(item.aliases),
            role=item.role,
            gender=item.gender,
            body_type=item.build,
            description=item.description,
            face_prompt=item.face or item.description,
        )
        for item in merged
    ]
    _report(on_progress, 0.8, "原子发布新增角色...")
    try:
        added = await store.publish_character_analysis_atomic(
            run_id,
            candidates,
            {item.name: list(item.evidence) for item in merged},
        )
    except BaseException as exc:
        await store.finish_analysis_run(run_id, status="failed", error=str(exc))
        raise
    _log(on_log, f"已新增 {len(added)} 个角色，保留已有 {len(candidates) - len(added)} 个")
    _report(on_progress, 1.0, "角色构建完成")
    return added


async def build_scenes_structured(
    store: Any,
    *,
    on_progress: Any = None,
    on_log: Any = None,
) -> dict[str, Any]:
    """Deterministically publish screenplay locations; defer narrated scenes."""
    from novelvideo.novel_source import require_imported_novel
    from novelvideo.utils.screenplay_scene_parser import parse_scene_blocks

    text = require_imported_novel(store.project_dir)
    if spine_template_for(store) != "drama":
        _log(on_log, SCENE_BUILD_DEFERRED_MESSAGE)
        _report(on_progress, 1.0, "无需提前构建场景")
        return {"scenes": 0, "added_scenes": 0, "mode": "episode_on_demand", "message": SCENE_BUILD_DEFERRED_MESSAGE}
    unique: dict[str, NovelScene] = {}
    for block in parse_scene_blocks(text):
        name = str(block.location or "").strip()
        if not name or name in unique:
            continue
        details = "；".join(line for line in block.lines[:4] if line.strip())
        prompt = f"{name}，{details}" if details else name
        unique[name] = NovelScene(
            name=name,
            scene_type="exterior" if block.interior_exterior == "外" else "interior",
            time_of_day=block.time_of_day,
            environment_prompt=prompt,
            description=details,
        )
    _report(on_progress, 0.8, "原子发布新增场景...")
    added = await store.add_scenes_atomic(list(unique.values()), skip_existing=True)
    _log(on_log, f"已新增 {len(added)} 个场景，保留已有 {len(unique) - len(added)} 个")
    _report(on_progress, 1.0, "场景构建完成")
    return {"scenes": len(unique), "added_scenes": len(added), "mode": "script"}


async def build_props_structured(
    store: Any,
    *,
    on_progress: Any = None,
    on_log: Any = None,
) -> dict[str, Any]:
    _log(on_log, PROP_BUILD_DEFERRED_MESSAGE)
    _report(on_progress, 1.0, "道具按分集生成")
    return {"props": 0, "mode": "episode_on_demand", "message": PROP_BUILD_DEFERRED_MESSAGE}


__all__ = [
    "PublishedCharacter",
    "PublishedEpisode",
    "PublishedScene",
    "StructuredCharacterInput",
    "StructuredEpisodeInput",
    "StructuredPublication",
    "StructuredSceneInput",
    "StructuredSourceRef",
    "build_structured_publication",
    "build_characters_structured",
    "build_props_structured",
    "build_scenes_structured",
    "publish_structured_publication",
    "spine_template_for",
    "stable_logical_id",
]
