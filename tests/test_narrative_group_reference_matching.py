from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import aiosqlite

from novelvideo.models import CharacterIdentity, NovelCharacter, NovelProp, NovelScene
from novelvideo.narrative_groups.reference_matching import (
    ensure_draft_scene_variant,
    match_reference_requirements,
)
from novelvideo.narrative_groups.reference_requirements import ReferenceRequirement
from novelvideo.sqlite_store import SQLiteStore


def _image(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    return str(path)


def _requirement(kind: str, entity_id: str, **values: str) -> ReferenceRequirement:
    return ReferenceRequirement(
        id=f"{kind}:{entity_id}", kind=kind, entity_id=entity_id, **values
    )


@pytest.fixture
async def project_store(tmp_path: Path):
    project_dir = tmp_path / "alice" / "demo"
    project_dir.mkdir(parents=True)
    store = SQLiteStore(str(project_dir))
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_matcher_distinguishes_exact_fallback_and_missing_identity(project_store):
    await project_store.add_character(
        NovelCharacter(
            name="阿青",
            identities=[
                CharacterIdentity(
                    identity_id="阿青_少年", character_name="阿青", identity_name="少年"
                )
            ],
        )
    )
    root = Path(project_store.project_dir)
    exact = _image(root / "assets/characters/阿青/identities/少年.png")
    portrait = _image(root / "assets/characters/小白/portrait.png")
    await project_store.add_character(NovelCharacter(name="小白"))

    preview = await match_reference_requirements(
        project_store,
        [
            _requirement("character_identity", "阿青_少年"),
            _requirement("character_identity", "小白_旅人"),
            _requirement("character_identity", "无名_默认"),
        ],
    )

    assert [item.status for item in preview.requirements] == [
        "matched",
        "fallback",
        "missing_asset",
    ]
    assert preview.requirements[0].bindings[0].image_path == exact
    assert preview.requirements[1].bindings[0].image_path == portrait
    assert preview.requirements[2].bindings == ()


@pytest.mark.asyncio
async def test_identity_matching_does_not_depend_on_warmed_store_cache(tmp_path):
    project_dir = tmp_path / "alice" / "cold-cache"
    project_dir.mkdir(parents=True)
    writer = SQLiteStore(str(project_dir))
    await writer.initialize()
    await writer.add_character(NovelCharacter(name="阿青"))
    await writer.close()
    _image(project_dir / "assets/characters/阿青/portrait.png")
    reader = SQLiteStore(str(project_dir))
    await reader.initialize()
    try:
        [matched] = (
            await match_reference_requirements(
                reader, [_requirement("character_identity", "阿青_少年")]
            )
        ).requirements
        assert matched.status == "fallback"
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_matcher_keeps_missing_prop_and_creates_variant_draft(project_store):
    await project_store.add_scene(NovelScene(name="谢家碑坊"))
    _image(Path(project_store.project_dir) / "assets/scenes/谢家碑坊/master.png")

    preview = await match_reference_requirements(
        project_store,
        [
            _requirement(
                "scene_variant",
                "谢家碑坊_暴雨天井",
                base_entity_id="谢家碑坊",
                variant_id="暴雨天井",
            ),
            _requirement("prop", "染血石碑"),
        ],
    )

    variant, prop = preview.requirements
    assert variant.status == "draft_variant"
    assert variant.available_actions == (
        "confirm_draft",
        "choose_variant",
        "use_base",
        "upload",
    )
    assert variant.bindings[0].decision == "fallback"
    stored = await project_store.get_scene("谢家碑坊_暴雨天井")
    assert (stored.name, stored.base_scene_id, stored.variant_id, stored.notes) == (
        "谢家碑坊_暴雨天井",
        "谢家碑坊",
        "暴雨天井",
        "由叙事组资产需求自动创建，尚未确认",
    )
    assert prop.status == "missing_asset"
    assert prop.bindings == ()
    assert prop.available_actions == ("choose_prop", "upload", "ignore")


@pytest.mark.asyncio
async def test_repeated_matching_keeps_auto_created_variant_in_draft_status(project_store):
    await project_store.add_scene(NovelScene(name="庭院"))
    requirement = _requirement(
        "scene_variant", "庭院_雨夜", base_entity_id="庭院", variant_id="雨夜"
    )

    first = await match_reference_requirements(project_store, [requirement])
    second = await match_reference_requirements(project_store, [requirement])

    assert first.requirements[0].status == "draft_variant"
    assert second.requirements[0].status == "draft_variant"


@pytest.mark.asyncio
async def test_draft_creation_does_not_treat_an_alias_as_exact_scene(project_store):
    await project_store.add_scene(
        NovelScene(name="人工雨景", aliases=["庭院_雨夜"], notes="正式资产")
    )

    created = await ensure_draft_scene_variant(project_store, "庭院", "雨夜")

    assert created.name == "庭院_雨夜"
    assert created.notes == "由叙事组资产需求自动创建，尚未确认"
    assert (await project_store.get_scene_exact("人工雨景")).notes == "正式资产"


@pytest.mark.asyncio
async def test_first_draft_preview_includes_insert_winner_once_in_candidates(project_store):
    await project_store.add_scene(NovelScene(name="庭院"))
    await project_store.add_scene(
        NovelScene(name="庭院_雪", base_scene_id="庭院", variant_id="雪")
    )
    requirement = _requirement(
        "scene_variant", "庭院_雨夜", base_entity_id="庭院", variant_id="雨夜"
    )

    [matched] = (await match_reference_requirements(project_store, [requirement])).requirements

    assert matched.candidate_asset_ids == ("庭院_雪", "庭院_雨夜")


@pytest.mark.asyncio
async def test_scene_and_prop_records_distinguish_matched_from_missing_image(project_store):
    root = Path(project_store.project_dir)
    await project_store.add_scene(NovelScene(name="庭院"))
    await project_store.add_scene(
        NovelScene(name="庭院_雪", base_scene_id="庭院", variant_id="雪")
    )
    await project_store.add_prop(NovelProp(name="玉佩"))
    await project_store.add_prop(NovelProp(name="书信"))
    scene_image = _image(root / "assets/scenes/庭院/master.png")
    variant_image = _image(root / "assets/scenes/庭院_雪/master.png")
    prop_image = _image(root / "assets/props/玉佩/reference_3view.png")

    preview = await match_reference_requirements(
        project_store,
        [
            _requirement("scene_base", "庭院", base_entity_id="庭院"),
            _requirement(
                "scene_variant", "庭院_雪", base_entity_id="庭院", variant_id="雪"
            ),
            _requirement("prop", "玉佩"),
            _requirement("prop", "书信"),
        ],
    )

    assert [item.status for item in preview.requirements] == [
        "matched",
        "matched",
        "matched",
        "missing_image",
    ]
    assert [item.bindings[0].image_path for item in preview.requirements[:3]] == [
        scene_image,
        variant_image,
        prop_image,
    ]


@pytest.mark.asyncio
async def test_matcher_rejects_paths_outside_assets(project_store, monkeypatch, tmp_path):
    await project_store.add_prop(NovelProp(name="越界"))
    escaped = _image(tmp_path / "escaped.png")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.reference_matching.compute_prop_reference_path",
        lambda *_args: escaped,
    )

    [matched] = (
        await match_reference_requirements(
            project_store, [_requirement("prop", "越界")]
        )
    ).requirements

    assert matched.status == "missing_image"
    assert matched.bindings == ()


@pytest.mark.asyncio
async def test_concurrent_draft_creation_never_overwrites_formal_variant(project_store):
    async def install_formal_variant(scene: NovelScene) -> bool:
        await project_store.add_scene(
            NovelScene(
                name=scene.name,
                base_scene_id="正式基础",
                variant_id="正式变体",
                notes="人工正式资产",
            )
        )
        return False

    project_store.add_scene_if_absent = install_formal_variant

    first, second = await asyncio.gather(
        ensure_draft_scene_variant(project_store, "庭院", "雨夜"),
        ensure_draft_scene_variant(project_store, "庭院", "雨夜"),
    )

    assert first.notes == second.notes == "人工正式资产"
    stored = await project_store.get_scene("庭院_雨夜")
    assert stored.base_scene_id == "正式基础"
    assert stored.variant_id == "正式变体"


@pytest.mark.asyncio
async def test_atomic_scene_insert_has_only_one_winner(tmp_path):
    project_dir = tmp_path / "alice" / "concurrent"
    project_dir.mkdir(parents=True)
    first_store = SQLiteStore(str(project_dir))
    second_store = SQLiteStore(str(project_dir))
    await first_store.initialize()
    await second_store.initialize()
    try:
        results = await asyncio.gather(
            first_store.add_scene_if_absent(NovelScene(name="庭院_雨夜", notes="first")),
            second_store.add_scene_if_absent(NovelScene(name="庭院_雨夜", notes="second")),
        )
        assert sorted(results) == [False, True]
        stored = await first_store.get_scene("庭院_雨夜")
        assert stored.notes in {"first", "second"}
    finally:
        await first_store.close()
        await second_store.close()


@pytest.mark.asyncio
async def test_same_store_concurrent_scene_insert_is_serialized(project_store):
    results = await asyncio.gather(
        project_store.add_scene_if_absent(NovelScene(name="庭院_雨夜", notes="first")),
        project_store.add_scene_if_absent(NovelScene(name="庭院_雨夜", notes="second")),
    )

    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_cancelled_atomic_insert_cannot_rollback_concurrent_add_scene(project_store):
    blocker = await aiosqlite.connect(project_store.db_path)
    await blocker.execute("BEGIN IMMEDIATE")
    atomic = asyncio.create_task(
        project_store.add_scene_if_absent(NovelScene(name="取消草稿"))
    )
    await asyncio.sleep(0.05)
    regular = asyncio.create_task(
        project_store.add_scene(NovelScene(name="正式场景", notes="必须持久化"))
    )
    await asyncio.sleep(0.05)
    atomic.cancel()
    await blocker.rollback()
    await blocker.close()

    with pytest.raises(asyncio.CancelledError):
        await atomic
    await regular

    assert await project_store.get_scene_exact("取消草稿") is None
    assert (await project_store.get_scene_exact("正式场景")).notes == "必须持久化"
