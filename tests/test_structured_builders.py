from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_structured_scene_input_accepts_aliases_and_scene_type() -> None:
    from novelvideo.structured_builders import StructuredSceneInput

    scene = StructuredSceneInput(
        name="旧车站·夜",
        location="旧车站",
        aliases=("老车站",),
        scene_type="exterior",
    )

    assert scene.aliases == ("老车站",)
    assert scene.scene_type == "exterior"


def _inputs():
    from novelvideo.structured_builders import (
        StructuredCharacterInput,
        StructuredEpisodeInput,
        StructuredSceneInput,
        StructuredSourceRef,
        stable_logical_id,
    )

    content = "沈青走进旧车站，站在破损的时刻表下。"
    character_id = stable_logical_id("character", "沈青")
    scene_id = stable_logical_id("scene", "旧车站", "夜")
    reference = StructuredSourceRef(
        episode_number=1,
        source_start=0,
        source_end=len(content),
        quote=content,
    )
    return (
        [
            StructuredEpisodeInput(
                number=1,
                title="归来",
                raw_content=content,
                summary="沈青深夜回到旧车站。",
                character_ids=(character_id,),
                scene_ids=(scene_id,),
            )
        ],
        [
            StructuredCharacterInput(
                name="沈青",
                aliases=("阿青",),
                role="主角",
                face="短发、清晰眉眼、冷白肤色",
                build="清瘦高挑",
                gender="女",
                description="多年后返乡的调查记者",
                source_refs=(reference,),
            )
        ],
        [
            StructuredSceneInput(
                name="旧车站·夜",
                location="旧车站",
                time="夜",
                environment="废弃站台，锈蚀铁轨，冷色月光",
                spatial_anchors=("站台", "时刻表", "铁轨"),
                source_refs=(reference,),
            )
        ],
    )


def test_builder_maps_required_fields_and_stable_logical_references() -> None:
    from novelvideo.structured_builders import build_structured_publication

    episodes, characters, scenes = _inputs()
    first = build_structured_publication(
        episodes=episodes,
        characters=characters,
        scenes=scenes,
    )
    second = build_structured_publication(
        episodes=episodes,
        characters=characters,
        scenes=scenes,
    )

    assert first == second
    character = first.characters[0]
    assert character.logical_id.startswith("character:")
    assert character.model.face_prompt == "短发、清晰眉眼、冷白肤色"
    assert character.model.role == "主角"
    assert character.model.body_type == "清瘦高挑"
    assert character.source_refs[0].quote.startswith("沈青")

    scene = first.scenes[0]
    assert scene.logical_id.startswith("scene:")
    assert scene.location == "旧车站"
    assert scene.model.time_of_day == "夜"
    assert scene.model.environment_prompt.startswith("废弃站台")
    notes = json.loads(scene.model.notes)
    assert notes["spatial_anchors"] == ["站台", "时刻表", "铁轨"]
    assert notes["source_refs"][0]["episode_number"] == 1

    episode = first.episodes[0]
    assert episode.logical_id == "episode:0001"
    assert episode.model.character_names == ["沈青"]
    assert episode.model.scene_menu[0].scene_id == "旧车站·夜"


def test_builder_rejects_unverifiable_source_refs_before_publication() -> None:
    from novelvideo.structured_builders import (
        StructuredCharacterInput,
        StructuredEpisodeInput,
        StructuredSourceRef,
        build_structured_publication,
    )

    episode = StructuredEpisodeInput(number=1, title="归来", raw_content="真实原文")
    character = StructuredCharacterInput(
        name="虚构角色",
        source_refs=(
            StructuredSourceRef(
                episode_number=1,
                source_start=0,
                source_end=4,
                quote="并不存在",
            ),
        ),
    )
    with pytest.raises(ValueError, match="source ref"):
        build_structured_publication(
            episodes=[episode], characters=[character], scenes=[]
        )


def test_builder_persists_same_location_time_variants_with_distinct_names() -> None:
    from novelvideo.structured_builders import (
        StructuredEpisodeInput,
        StructuredSceneInput,
        build_structured_publication,
        stable_logical_id,
    )

    day_id = stable_logical_id("scene", "客厅", "日")
    night_id = stable_logical_id("scene", "客厅", "夜")
    publication = build_structured_publication(
        episodes=[
            StructuredEpisodeInput(
                number=1,
                title="日夜",
                raw_content="客厅从白天进入夜晚。",
                scene_ids=(day_id, night_id),
            )
        ],
        characters=[],
        scenes=[
            StructuredSceneInput(name="客厅", location="客厅", time="日"),
            StructuredSceneInput(name="客厅", location="客厅", time="夜"),
        ],
    )

    assert [item.model.name for item in publication.scenes] == ["客厅·日", "客厅·夜"]
    assert publication.episodes[0].model.scene_menu[0].scene_id == "客厅·日"
    assert publication.episodes[0].model.scene_menu[1].scene_id == "客厅·夜"


@pytest.fixture
async def structured_store(tmp_path: Path):
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        "user/structured",
        output_dir=str(tmp_path / "project"),
        state_dir=str(tmp_path / "state"),
    )
    await store.initialize()
    await store.load_graph_state()
    try:
        yield store
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_episode_character_scene_publish_is_one_atomic_transaction(
    structured_store,
) -> None:
    from novelvideo.models import NovelCharacter, NovelEpisode, NovelScene
    from novelvideo.structured_builders import (
        build_structured_publication,
        publish_structured_publication,
    )

    store = structured_store
    await store.add_episode(
        NovelEpisode(number=1, title="上一版", raw_content="上一版正式内容")
    )
    await store.add_character(
        NovelCharacter(name="沈青", face_prompt="上一版面容", role="配角")
    )
    await store.add_scene(
        NovelScene(name="旧车站·夜", environment_prompt="上一版环境")
    )
    await store.load_graph_state()

    db = await store._ensure_db()
    await db.execute(
        """CREATE TRIGGER fail_structured_scene BEFORE INSERT ON scenes
        WHEN NEW.name = '旧车站·夜'
        BEGIN SELECT RAISE(ABORT, 'scene publish failed'); END"""
    )
    await db.commit()
    publication = build_structured_publication(
        episodes=_inputs()[0],
        characters=_inputs()[1],
        scenes=_inputs()[2],
    )

    with pytest.raises(Exception, match="scene publish failed"):
        await publish_structured_publication(store, publication)

    await store.load_graph_state()
    assert store.get_episode(1).title == "上一版"
    assert store.get_character("沈青").face_prompt == "上一版面容"
    assert (await store.get_scene("旧车站·夜")).environment_prompt == "上一版环境"


@pytest.mark.asyncio
async def test_successful_atomic_publish_refreshes_episode_graph_compatible_cache(
    structured_store,
) -> None:
    from novelvideo.structured_builders import (
        build_structured_publication,
        publish_structured_publication,
    )

    publication = build_structured_publication(
        episodes=_inputs()[0],
        characters=_inputs()[1],
        scenes=_inputs()[2],
    )
    result = await publish_structured_publication(structured_store, publication)

    assert result == {"episodes": 1, "characters": 1, "scenes": 1}
    assert structured_store.get_episode(1).character_names == ["沈青"]
    assert structured_store.get_character("沈青").body_type == "清瘦高挑"
    scene = await structured_store.get_scene("旧车站·夜")
    assert scene.time_of_day == "夜"


@pytest.mark.asyncio
async def test_before_commit_failure_rolls_back_the_publication(
    structured_store,
) -> None:
    from novelvideo.models import NovelEpisode
    from novelvideo.structured_builders import (
        build_structured_publication,
        publish_structured_publication,
    )

    await structured_store.add_episode(
        NovelEpisode(
            number=1,
            title="上一版",
            raw_content="上一版正式内容",
        )
    )
    publication = build_structured_publication(
        episodes=_inputs()[0],
        characters=_inputs()[1],
        scenes=_inputs()[2],
    )

    async def reject_ready() -> None:
        raise RuntimeError("ready transition failed")

    with pytest.raises(RuntimeError, match="ready transition failed"):
        await publish_structured_publication(
            structured_store,
            publication,
            before_commit=reject_ready,
        )

    await structured_store.load_graph_state()
    assert structured_store.get_episode(1).title == "上一版"
    assert structured_store.get_character("沈青") is None


@pytest.mark.asyncio
async def test_republishing_replaces_stale_evidence_from_previous_run(
    structured_store,
) -> None:
    from novelvideo.structured_builders import (
        build_structured_publication,
        publish_structured_publication,
    )

    publication = build_structured_publication(
        episodes=_inputs()[0],
        characters=_inputs()[1],
        scenes=_inputs()[2],
    )
    await publish_structured_publication(structured_store, publication, run_id="old-run")
    await publish_structured_publication(structured_store, publication, run_id="current-run")

    evidence = await structured_store.list_entity_evidence("character", "沈青")
    assert evidence
    assert {item["run_id"] for item in evidence} == {"current-run"}
