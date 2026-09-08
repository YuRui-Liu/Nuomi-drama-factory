from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import threading
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
    assert all(item.entity_id != "咖啡馆_暴雨版" for item in scene_bindings)


def test_binding_projection_retains_unchanged_full_catalog_alongside_overlay():
    requirements = (
        SimpleNamespace(kind="scene_base", entity_key="旧车站", required=True),
        SimpleNamespace(kind="scene_base", entity_key="咖啡馆", required=True),
    )
    shot = SimpleNamespace(id="shot-1", dramatic_beat_ids=(), asset_requirements=requirements)
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=(SimpleNamespace(id="group-1", dramatic_beat_ids=(), shots=(shot,)),),
    )

    bindings = _episode_asset_bindings(
        asset_kind="scene",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=(NovelScene(name="咖啡馆", environment_prompt="新规划"),),
        scenes=(
            NovelScene(name="旧车站"),
            NovelScene(name="咖啡馆", environment_prompt="旧规划"),
        ),
        props=(),
        characters=(),
    )

    assert [item.entity_id for item in bindings] == ["旧车站", "咖啡馆"]


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
    old_binding = _binding("prop", "旧道具")
    await store.add_episode(NovelEpisode(number=1, title="第一集", prop_menu=old_menu))
    await store.replace_planned_reference_bindings_atomic(1, ("prop",), (old_binding,))

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
    assert await store.list_planned_reference_bindings(1) == [old_binding]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "raw_menu", "canonical_menu"),
    [
        ("scene", [{"scene_id": "咖啡馆"}], [SceneMenuItem(scene_id="咖啡馆")]),
        ("prop", [{"prop_id": "手电"}], [PropMenuItem(prop_id="手电")]),
        ("scene", ["历史场景"], []),
        ("prop", ["历史道具"], []),
    ],
)
async def test_asset_menu_baseline_canonicalizes_legacy_json(
    tmp_path, kind, raw_menu, canonical_menu
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    episode = NovelEpisode(number=1, title="第一集")
    if kind == "scene":
        episode.scene_menu_json = json.dumps(raw_menu, ensure_ascii=False)
    else:
        episode.prop_menu_json = json.dumps(raw_menu, ensure_ascii=False)
    await store.add_episode(episode)
    baseline_items = raw_menu if raw_menu and isinstance(raw_menu[0], str) else canonical_menu
    baseline = store.asset_menu_baseline_digest(baseline_items, asset_kind=kind)

    if kind == "scene":
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(),
            scene_menu=tuple(canonical_menu),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=baseline,
            bindings=None,
        )
    else:
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(),
            prop_menu=tuple(canonical_menu),
            prop_baseline_digests={},
            episode_prop_menu_baseline_digest=baseline,
            bindings=None,
        )


@pytest.mark.asyncio
async def test_publish_prop_plan_atomic_exposes_entity_menu_and_binding_together(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    prop = NovelProp(name="强光手电", visual_prompt="黑色金属")
    menu = (PropMenuItem(prop_id=prop.name),)

    await store.publish_prop_plan_atomic(
        episode_number=1,
        props=(prop,),
        prop_menu=menu,
        prop_baseline_digests={},
        episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(
            [], asset_kind="prop"
        ),
        bindings=(_binding("prop", prop.name),),
    )

    assert (await store.get_prop(prop.name)).visual_prompt == prop.visual_prompt
    assert (await store.list_episodes())[0].prop_menu == list(menu)
    assert await store.list_planned_reference_bindings(1) == [_binding("prop", prop.name)]


@pytest.mark.asyncio
async def test_publish_scene_plan_atomic_rolls_back_all_state_on_binding_failure(
    tmp_path, monkeypatch
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    old_menu = [SceneMenuItem(scene_id="旧场景")]
    old_binding = _binding("scene_base", "旧场景")
    await store.add_episode(NovelEpisode(number=1, title="第一集", scene_menu=old_menu))
    await store.replace_planned_reference_bindings_atomic(
        1, ("scene_base", "scene_variant"), (old_binding,)
    )

    async def fail_insert(db, binding):
        raise RuntimeError("binding insert failed")

    monkeypatch.setattr(store, "_insert_planned_reference_binding", fail_insert)
    with pytest.raises(RuntimeError, match="binding insert failed"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                old_menu, asset_kind="scene"
            ),
            bindings=(_binding("scene_base", "咖啡馆"),),
        )

    assert await store.get_scene("咖啡馆") is None
    assert (await store.list_episodes())[0].scene_menu == old_menu
    assert await store.list_planned_reference_bindings(1) == [old_binding]


@pytest.mark.asyncio
async def test_publish_scene_plan_fails_closed_on_planning_field_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelScene(name="咖啡馆", environment_prompt="旧描述")
    await store.add_scene(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.scene_plan_baseline_digest(original)
    await store.update_scene("咖啡馆", environment_prompt="用户并发描述")

    with pytest.raises(ValueError, match="scene plan conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(original.model_copy(update={"environment_prompt": "规划描述"}),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={"咖啡馆": baseline},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                [], asset_kind="scene"
            ),
            bindings=(),
        )

    assert (await store.get_scene("咖啡馆")).environment_prompt == "用户并发描述"


@pytest.mark.asyncio
async def test_publish_prop_plan_fails_closed_on_menu_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.asset_menu_baseline_digest([], asset_kind="prop")
    concurrent = [PropMenuItem(prop_id="用户道具")]
    await store.update_episode(1, prop_menu=concurrent)

    with pytest.raises(ValueError, match="prop menu conflict"):
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(NovelProp(name="手电"),),
            prop_menu=(PropMenuItem(prop_id="手电"),),
            prop_baseline_digests={},
            episode_prop_menu_baseline_digest=baseline,
            bindings=(),
        )

    assert await store.get_prop("手电") is None
    assert (await store.list_episodes())[0].prop_menu == concurrent


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scene", "prop"])
async def test_asset_publish_rejects_new_name_that_appeared_concurrently(tmp_path, kind):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    if kind == "scene":
        await store.add_scene(NovelScene(name="同名资产", notes="用户创建"))
        with pytest.raises(ValueError, match="scene now exists"):
            await store.publish_scene_plan_atomic(
                episode_number=1,
                scenes=(NovelScene(name="同名资产", environment_prompt="规划"),),
                scene_menu=(SceneMenuItem(scene_id="同名资产"),),
                scene_baseline_digests={},
                episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                    [], asset_kind="scene"
                ),
                bindings=(),
            )
        assert (await store.get_scene("同名资产")).notes == "用户创建"
    else:
        await store.add_prop(NovelProp(name="同名资产", notes="用户创建"))
        with pytest.raises(ValueError, match="prop now exists"):
            await store.publish_prop_plan_atomic(
                episode_number=1,
                props=(NovelProp(name="同名资产", visual_prompt="规划"),),
                prop_menu=(PropMenuItem(prop_id="同名资产"),),
                prop_baseline_digests={},
                episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(
                    [], asset_kind="prop"
                ),
                bindings=(),
            )
        assert (await store.get_prop("同名资产")).notes == "用户创建"


@pytest.mark.asyncio
async def test_prop_publish_preserves_concurrent_notes(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelProp(name="手电", visual_prompt="旧外观", notes="原备注")
    await store.add_prop(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.prop_plan_baseline_digest(original)
    await store.update_prop("手电", notes="用户并发备注")

    await store.publish_prop_plan_atomic(
        episode_number=1,
        props=(original.model_copy(update={"visual_prompt": "规划外观"}),),
        prop_menu=(PropMenuItem(prop_id="手电"),),
        prop_baseline_digests={"手电": baseline},
        episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(
            [], asset_kind="prop"
        ),
        bindings=(),
    )

    persisted = await store.get_prop("手电")
    assert persisted.visual_prompt == "规划外观"
    assert persisted.notes == "用户并发备注"


def test_episode_asset_planners_use_dedicated_text_task_route():
    import novelvideo.task_backend.runners.episode_assets  # noqa: F401
    from novelvideo.task_backend.registry import get_project_task_runner_registration

    for task_type in ("episode_scene_planner", "episode_prop_planner"):
        registration = get_project_task_runner_registration(task_type)
        assert registration is not None
        assert registration.text_task_role == "episode_asset_planning"


@pytest.mark.asyncio
async def test_runner_long_draft_does_not_block_activation_and_stale_revision_is_rejected(
    tmp_path, monkeypatch
):
    import novelvideo.agents.asset_compiler as compiler_module
    import novelvideo.cognee as cognee_module
    import novelvideo.director_plan.store as director_store_module
    import novelvideo.sqlite_store as sqlite_module
    import novelvideo.task_backend.runners.episode_assets as runner

    requirement = SimpleNamespace(kind="scene_base", entity_key="咖啡馆", required=True)
    shot = SimpleNamespace(
        id="shot-1", dramatic_beat_ids=("beat-1",), asset_requirements=(requirement,)
    )
    group = SimpleNamespace(
        id="group-1", dramatic_beat_ids=("beat-1",), shots=(shot,)
    )
    old_plan = SimpleNamespace(revision_id="director-old", groups=(group,))
    new_plan = SimpleNamespace(revision_id="director-new", groups=(group,))
    draft_started = asyncio.Event()
    release_draft = asyncio.Event()
    activation_finished = threading.Event()
    published_revision_ids: list[str] = []
    block_publish = False
    publish_started = asyncio.Event()
    release_publish = asyncio.Event()

    class FakeDirectorPlanStore:
        lock = threading.Lock()
        active = old_plan

        def __init__(self, project_dir):
            pass

        @contextmanager
        def lock_active_revision(self, episode):
            with self.lock:
                yield self.active

        def activate_new(self):
            with self.lock:
                type(self).active = new_plan
                activation_finished.set()

    class FakeSQLiteStore:
        def __init__(self, *args, **kwargs):
            self.scenes = [NovelScene(name="全量旧场景")]

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def list_scenes(self):
            return list(self.scenes)

        async def list_props(self):
            return []

        async def publish_scene_plan_atomic(self, **kwargs):
            nonlocal block_publish
            published_revision_ids.extend(
                binding.source_plan_revision_id for binding in kwargs["bindings"]
            )
            if block_publish:
                publish_started.set()
                await release_publish.wait()

    class FakeCogneeStore:
        def __init__(self, *args, sqlite_store, **kwargs):
            self.sqlite_store = sqlite_store
            self.episode = NovelEpisode(number=1, title="第一集")

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        def get_episode(self, episode):
            return self.episode

        def get_all_characters(self):
            return []

    class FakeCompiler:
        def __init__(self, store, *, director_plan):
            assert director_plan.revision_id == "director-old"

        async def build_scene_plan_draft(self, episode, **kwargs):
            draft_started.set()
            await release_draft.wait()
            return compiler_module.ScenePlanDraft(
                scenes=(NovelScene(name="咖啡馆"),),
                scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
                new_count=1,
                scene_baseline_digests={},
                episode_scene_menu_baseline_digest="baseline",
            )

    class FakeUsageMeter:
        async def set_project_llm_usage_context(self, **kwargs):
            pass

    monkeypatch.setattr(compiler_module, "AssetCompiler", FakeCompiler)
    monkeypatch.setattr(cognee_module, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(sqlite_module, "SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(director_store_module, "DirectorPlanStore", FakeDirectorPlanStore)
    monkeypatch.setattr(runner, "get_usage_meter", lambda: FakeUsageMeter())
    monkeypatch.setattr(
        runner,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *args, **kwargs: None),
    )
    ctx = SimpleNamespace(
        owner_username="owner",
        project_name="project",
        owner_project_label="owner/project",
        project_id="owner/project",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    envelope = {
        "task_type": "episode_scene_planner",
        "episode": 1,
        "payload": {"asset_kind": "scene"},
    }

    task = asyncio.create_task(runner._run_episode_asset_planner(envelope, ctx))
    await draft_started.wait()
    activator = threading.Thread(target=FakeDirectorPlanStore(tmp_path).activate_new)
    activator.start()
    await asyncio.sleep(0.05)
    assert activation_finished.is_set()
    release_draft.set()
    with pytest.raises(ValueError, match="ACTIVE_DIRECTOR_PLAN_STALE"):
        await task
    await asyncio.to_thread(activator.join, 5)

    assert published_revision_ids == []

    FakeDirectorPlanStore.active = old_plan
    activation_finished.clear()
    block_publish = True
    task = asyncio.create_task(runner._run_episode_asset_planner(envelope, ctx))
    await publish_started.wait()
    activator = threading.Thread(target=FakeDirectorPlanStore(tmp_path).activate_new)
    activator.start()
    await asyncio.sleep(0.05)
    assert not activation_finished.is_set()
    release_publish.set()
    result = await task
    await asyncio.to_thread(activator.join, 5)

    assert published_revision_ids == ["director-old"]
    assert activation_finished.is_set()
    assert result["binding_count"] == 1
    assert result["binding_statuses"] == {"ready": 1}


@pytest.mark.asyncio
async def test_scene_commit_reports_cache_refresh_pending_without_rolling_back(
    tmp_path, monkeypatch
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))

    original_refresh = store.load_graph_state

    async def fail_refresh():
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(store, "load_graph_state", fail_refresh)
    result = await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(NovelScene(name="咖啡馆"),),
        scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
        scene_baseline_digests={},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
            [], asset_kind="scene"
        ),
        bindings=(_binding("scene_base", "咖啡馆"),),
    )

    assert result == {"committed": True, "cache_refresh_pending": True}
    assert await store.get_scene("咖啡馆") is not None
    assert (await store.list_episodes())[0].scene_menu == [SceneMenuItem(scene_id="咖啡馆")]
    assert await store.list_planned_reference_bindings(1) == [
        _binding("scene_base", "咖啡馆")
    ]
    assert store._cache_refresh_pending is True

    monkeypatch.setattr(store, "load_graph_state", original_refresh)
    await store.load_graph_state()
    assert store._cache_refresh_pending is False


@pytest.mark.asyncio
async def test_scene_publish_rejects_stale_unchanged_catalog_used_for_projection(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    unchanged = NovelScene(name="旧车站", environment_prompt="投影时描述")
    await store.add_scene(unchanged)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    scene_catalog_baseline = store.scene_catalog_baseline_digest([unchanged])
    prop_catalog_baseline = store.prop_catalog_baseline_digest([])
    await store.update_scene("旧车站", environment_prompt="并发修改")

    with pytest.raises(ValueError, match="scene catalog conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                [], asset_kind="scene"
            ),
            scene_catalog_baseline_digest=scene_catalog_baseline,
            prop_catalog_baseline_digest=prop_catalog_baseline,
            bindings=(_binding("scene_base", "咖啡馆"),),
        )

    assert await store.get_scene("咖啡馆") is None
    assert await store.list_planned_reference_bindings(1) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["create", "delete"])
async def test_scene_publish_rejects_created_or_deleted_projection_catalog_entity(
    tmp_path, mutation
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    existing_prop = NovelProp(name="旧钥匙", visual_prompt="铜钥匙")
    await store.add_prop(existing_prop)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    scene_catalog_baseline = store.scene_catalog_baseline_digest([])
    prop_catalog_baseline = store.prop_catalog_baseline_digest([existing_prop])
    if mutation == "create":
        await store.add_prop(NovelProp(name="新钥匙", visual_prompt="银钥匙"))
    else:
        assert await store.delete_prop("旧钥匙") is True

    with pytest.raises(ValueError, match="prop catalog conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                [], asset_kind="scene"
            ),
            scene_catalog_baseline_digest=scene_catalog_baseline,
            prop_catalog_baseline_digest=prop_catalog_baseline,
            bindings=(_binding("scene_base", "咖啡馆"),),
        )

    assert await store.get_scene("咖啡馆") is None
    assert await store.list_planned_reference_bindings(1) == []


def test_legacy_string_menu_digest_preserves_string_identity():
    assert SQLiteStore.asset_menu_baseline_digest(
        ["场景甲"], asset_kind="scene"
    ) != SQLiteStore.asset_menu_baseline_digest(["场景乙"], asset_kind="scene")


def test_mixed_legacy_menu_digest_preserves_source_order():
    structured = {"scene_id": "咖啡馆"}
    assert SQLiteStore.asset_menu_baseline_digest(
        ["旧场景", structured], asset_kind="scene"
    ) != SQLiteStore.asset_menu_baseline_digest(
        [structured, "旧场景"], asset_kind="scene"
    )


@pytest.mark.asyncio
async def test_runner_cache_refresh_treats_cognee_false_status_as_pending():
    from novelvideo.task_backend.runners.episode_assets import _refresh_asset_caches

    class SQLiteCache:
        async def load_graph_state(self):
            return None

    class CogneeCache:
        async def load_graph_state(self):
            return False

    assert await _refresh_asset_caches(SQLiteCache(), CogneeCache()) is False
