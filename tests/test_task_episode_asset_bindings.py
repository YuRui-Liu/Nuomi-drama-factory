from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from novelvideo.models import NovelEpisode, NovelProp, NovelScene, PropMenuItem, SceneMenuItem
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_backend.runners.episode_assets import _episode_asset_bindings


def _digest(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _binding(kind: str, entity_id: str) -> PlannedReferenceBinding:
    return PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        asset_kind=kind,
        entity_id=entity_id,
        base_entity_id="咖啡馆" if kind == "scene_variant" else "",
        variant_id="暴雨版" if kind == "scene_variant" else "",
        asset_slot_id=f"slot:{kind}:{entity_id}",
        status="ready",
        resolution="auto_matched",
        display_label=entity_id,
    )


def test_scene_and_prop_binding_projection_overlays_draft_entities():
    requirements = (
        SimpleNamespace(kind="scene_base", entity_key="咖啡馆", required=True),
        SimpleNamespace(
            kind="scene_state",
            entity_key="咖啡馆",
            visible_change="暴雨版",
            required=True,
        ),
        SimpleNamespace(kind="prop", entity_key="强光手电", required=True),
    )
    shot = SimpleNamespace(id="shot-1", dramatic_beat_ids=("beat-1",), asset_requirements=requirements)
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=(SimpleNamespace(id="group-1", dramatic_beat_ids=("beat-1",), shots=(shot,)),),
    )
    scenes = (
        NovelScene(name="咖啡馆"),
        NovelScene(name="雨中咖啡馆", base_scene_id="咖啡馆", variant_id="暴雨版"),
    )
    props = (NovelProp(name="强光手电"),)

    scene_bindings = _episode_asset_bindings(
        asset_kind="scene",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=scenes,
        scenes=(),
        props=(),
        characters=(),
    )
    prop_bindings = _episode_asset_bindings(
        asset_kind="prop",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=props,
        scenes=scenes,
        props=(),
        characters=(),
    )

    assert [(item.asset_kind, item.entity_id) for item in scene_bindings] == [
        ("scene_base", "咖啡馆"),
        ("scene_variant", "雨中咖啡馆"),
    ]
    assert [(item.base_entity_id, item.variant_id) for item in scene_bindings[1:]] == [
        ("咖啡馆", "暴雨版")
    ]
    assert [(item.asset_kind, item.entity_id) for item in prop_bindings] == [
        ("prop", "强光手电")
    ]


@pytest.mark.asyncio
async def test_publish_scene_plan_atomic_exposes_entity_menu_and_binding_together(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    scene = NovelScene(name="咖啡馆", environment_prompt="四面空间描述")
    menu = (SceneMenuItem(scene_id="咖啡馆"),)

    await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(scene,),
        scene_menu=menu,
        scene_baseline_digests={},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest([]),
        bindings=(_binding("scene_base", "咖啡馆"),),
    )

    persisted_scene = await store.get_scene("咖啡馆")
    assert persisted_scene.name == scene.name
    assert persisted_scene.environment_prompt == scene.environment_prompt
    assert (await store.list_episodes())[0].scene_menu == list(menu)
    assert await store.list_planned_reference_bindings(1) == [_binding("scene_base", "咖啡馆")]


@pytest.mark.asyncio
async def test_publish_prop_plan_atomic_rolls_back_all_state_on_binding_failure(tmp_path, monkeypatch):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    old_menu = [PropMenuItem(prop_id="旧道具")]
    await store.add_episode(NovelEpisode(number=1, title="第一集", prop_menu=old_menu))

    async def fail_insert(db, binding):
        raise RuntimeError("binding insert failed")

    monkeypatch.setattr(store, "_insert_planned_reference_binding", fail_insert)
    with pytest.raises(RuntimeError, match="binding insert failed"):
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(NovelProp(name="强光手电", visual_prompt="黑色金属"),),
            prop_menu=(PropMenuItem(prop_id="强光手电"),),
            prop_baseline_digests={},
            episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(old_menu),
            bindings=(_binding("prop", "强光手电"),),
        )

    assert await store.get_prop("强光手电") is None
    assert (await store.list_episodes())[0].prop_menu == old_menu


@pytest.mark.asyncio
async def test_publish_scene_plan_fails_closed_on_menu_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.asset_menu_baseline_digest([])
    concurrent_menu = [SceneMenuItem(scene_id="用户场景")]
    await store.update_episode(1, scene_menu=concurrent_menu)

    with pytest.raises(ValueError, match="scene menu conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=baseline,
            bindings=(),
        )

    assert await store.get_scene("咖啡馆") is None
    assert (await store.list_episodes())[0].scene_menu == concurrent_menu


@pytest.mark.asyncio
async def test_scene_publish_preserves_concurrent_non_planning_fields(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelScene(name="咖啡馆", environment_prompt="旧描述", notes="原备注")
    await store.add_scene(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.scene_plan_baseline_digest(original)
    await store.update_scene("咖啡馆", notes="用户并发备注")

    await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(original.model_copy(update={"environment_prompt": "新描述"}),),
        scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
        scene_baseline_digests={"咖啡馆": baseline},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest([]),
        bindings=(),
    )

    persisted = await store.get_scene("咖啡馆")
    assert persisted.environment_prompt == "新描述"
    assert persisted.notes == "用户并发备注"


@pytest.mark.asyncio
async def test_prop_publish_fails_closed_on_planning_field_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelProp(name="手电", visual_prompt="旧外观", notes="用户备注")
    await store.add_prop(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.prop_plan_baseline_digest(original)
    await store.update_prop("手电", visual_prompt="用户并发外观")

    with pytest.raises(ValueError, match="prop plan conflict"):
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(original.model_copy(update={"visual_prompt": "规划外观"}),),
            prop_menu=(PropMenuItem(prop_id="手电"),),
            prop_baseline_digests={"手电": baseline},
            episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest([]),
            bindings=(),
        )

    assert (await store.get_prop("手电")).visual_prompt == "用户并发外观"
