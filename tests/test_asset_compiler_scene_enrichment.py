from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from novelvideo.models import NovelScene

ENRICHED_ENVIRONMENT_PROMPT = "正面：临街玻璃窗\n左侧：咖啡吧台\n右侧：木质书架\n背面：入口木门"
LEGACY_META_PROMPT = """正面：以“咖啡馆”最能代表地点身份的主入口作为正面；根据原文证据确定固定结构。
左侧：从正面视角向左延伸，布置与场景功能一致的侧墙和通道。
右侧：不要复制正面主体，只做合理连续补全。
背面：可为入口反向；必须和正面/左右侧构成完整 360 度闭合空间。
光源：使用中性默认状态的稳定环境光。
材质/风格：保持 interior 场景的固定建筑风格。
禁止元素：不出现人物、字幕、水印。"""


class _FakeSQLiteStore:
    def __init__(self, scenes: list[NovelScene] | None = None):
        self.scenes = {scene.name: scene for scene in scenes or []}
        self.added: list[NovelScene] = []
        self.updated: list[tuple[str, dict]] = []
        self.atomic_calls: list[tuple[list[str], bool]] = []
        self.published_menus: list[tuple[int, list, object]] = []
        self.publish_refresh_cache: list[bool] = []

    async def get_scene(self, name: str):
        return self.scenes.get(name)

    async def list_scenes(self):
        return list(self.scenes.values())

    async def add_scene(self, scene: NovelScene):
        self.added.append(scene)
        self.scenes[scene.name] = scene

    async def add_scenes_atomic(
        self, scenes: list[NovelScene], *, skip_existing: bool = True
    ):
        self.atomic_calls.append(([scene.name for scene in scenes], skip_existing))
        for scene in scenes:
            self.added.append(scene)
            self.scenes[scene.name] = scene
        return [scene.name for scene in scenes]

    async def publish_scene_plan_atomic(
        self,
        *,
        episode_number,
        scenes,
        scene_menu,
        scene_baseline_digests,
        episode_scene_menu_baseline_digest,
        bindings,
        refresh_cache=True,
    ):
        await self.add_scenes_atomic(list(scenes), skip_existing=False)
        self.published_menus.append((episode_number, list(scene_menu), bindings))
        self.publish_refresh_cache.append(refresh_cache)

    async def update_scene(self, name: str, **updates):
        self.updated.append((name, updates))
        scene = self.scenes[name]
        for key, value in updates.items():
            setattr(scene, key, value)
        return True


class _FakeCogneeStore:
    def __init__(
        self,
        scenes: list[NovelScene] | None = None,
        *,
        raw_content: str = "",
        project_dir: str = "",
    ):
        self.sqlite_store = _FakeSQLiteStore(scenes)
        self.project_dir = project_dir
        self.raw_content = raw_content
        self.updated: list[tuple[int, dict]] = []
        self.refresh_count = 0

    async def load_episode_content(self, ep_num: int):
        return self.raw_content

    async def update_episode(self, episode_number: int, **updates):
        self.updated.append((episode_number, updates))
        return None

    async def load_graph_state(self):
        self.refresh_count += 1


def _block(
    location: str = "咖啡馆",
    time_of_day: str = "夜",
    scene_number: int = 1,
):
    return SimpleNamespace(
        location=location,
        interior_exterior="内",
        time_of_day=time_of_day,
        characters=["陆辰", "沈月白"],
        header_line=f"场次（{scene_number}）地点：{location}，{time_of_day}，内；出场人物：陆辰、沈月白",
        lines=[
            "窗外暴雨如注，雨滴重重砸在玻璃上。",
            "咖啡馆内只有他们这一桌，昏黄的灯光将两人的影子拉得很长。",
            "沈月白将自己的手机推到桌子中央。",
        ],
    )


@pytest.mark.asyncio
async def test_compile_episode_scenes_reconciles_base_scene_before_planning(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return []

    async def fake_reconcile(self, source_text, episode, log):
        scene = await fake_enrich(
            scene_name="咖啡馆",
            scene_type="interior",
            context_lines=["窗外暴雨如注。"],
        )
        await self.cognee_store.sqlite_store.add_scene(scene)
        log("  AI补全基础场景: 咖啡馆")
        return ["咖啡馆"]

    async def fake_load_scene_blocks(self, episode):
        return [_block()]

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )

    store = _FakeCogneeStore()
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, new_count = await compiler.compile_episode_scenes(
        SimpleNamespace(number=1, title="第一集", beat_source_text="场次（1）地点：咖啡馆"),
        lambda _message: None,
    )

    assert scene_menu[0].scene_id == "咖啡馆"
    assert new_count == 0
    assert [scene.name for scene in store.sqlite_store.added] == ["咖啡馆"]
    assert store.sqlite_store.scenes["咖啡馆"].environment_prompt.startswith("正面：临街玻璃窗")


@pytest.mark.asyncio
async def test_compile_episode_scenes_skips_llm_reconcile_when_all_base_scenes_exist(
):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(
        name="咖啡馆",
        scene_type="interior",
        environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        description="雨夜咖啡馆",
    )

    store = _FakeCogneeStore([existing])
    compiler = asset_compiler.AssetCompiler(store)

    assert await compiler._all_scene_blocks_have_existing_base([_block()]) is True


@pytest.mark.asyncio
async def test_compile_episode_scenes_backfills_existing_empty_scene_prompt(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(name="咖啡馆", scene_type="interior", environment_prompt="")

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore([existing])
    compiler = asset_compiler.AssetCompiler(store)

    _scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert pending_scenes == []
    assert store.sqlite_store.updated == [
        (
            "咖啡馆",
            {
                "scene_type": "interior",
                "environment_prompt": ENRICHED_ENVIRONMENT_PROMPT,
                "description": "雨夜咖啡馆",
            },
        )
    ]
    assert existing.environment_prompt.startswith("正面：临街玻璃窗")


@pytest.mark.asyncio
async def test_compile_episode_scenes_keeps_existing_prompt(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(
        name="咖啡馆",
        scene_type="interior",
        environment_prompt="已有完整空间合同",
        description="已有描述",
    )
    enrich_calls = []

    async def fake_enrich(**kwargs):
        enrich_calls.append(kwargs)
        return NovelScene(name=kwargs["scene_name"], environment_prompt="不应使用")

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore([existing])
    compiler = asset_compiler.AssetCompiler(store)

    _scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert pending_scenes == []
    assert store.sqlite_store.updated == []
    assert enrich_calls == []
    assert existing.environment_prompt == "已有完整空间合同"


@pytest.mark.asyncio
async def test_compile_episode_scenes_repairs_legacy_meta_prompt_and_marks_existing_master_stale(
    monkeypatch, tmp_path
):
    import novelvideo.agents.asset_compiler as asset_compiler

    existing = NovelScene(
        name="咖啡馆",
        scene_type="interior",
        environment_prompt=LEGACY_META_PROMPT,
        description="旧描述",
    )
    master = tmp_path / "assets" / "scenes" / "咖啡馆" / "master.png"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"old")

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="新描述",
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore([existing], project_dir=str(tmp_path))
    compiler = asset_compiler.AssetCompiler(store)

    _scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert pending_scenes == []
    assert existing.environment_prompt == ENRICHED_ENVIRONMENT_PROMPT
    assert store.sqlite_store.updated == [
        (
            "咖啡馆",
            {
                "scene_type": "interior",
                "environment_prompt": ENRICHED_ENVIRONMENT_PROMPT,
                "description": "新描述",
                "stale_reference_kinds": ["master"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_base_scene_reconcile_does_not_partially_persist_when_enrichment_fails(
    monkeypatch,
):
    import novelvideo.agents.asset_compiler as asset_compiler
    from novelvideo.cognee.pipeline import ScenePromptQualityError

    calls = 0

    async def fake_enrich(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ScenePromptQualityError(
                "SCENE_PROMPT_QUALITY_FAILED: 设备间: meta_instruction:合理补全"
            )
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        )

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    store = _FakeCogneeStore()
    compiler = asset_compiler.AssetCompiler(store)
    output = asset_compiler.EpisodeBaseSceneReconcileOutput(
        scenes=[
            asset_compiler.BaseSceneReconcileDecision(
                action="create",
                scene_name="直播间",
                evidence_lines=["直播间连接设备间。"],
            ),
            asset_compiler.BaseSceneReconcileDecision(
                action="create",
                scene_name="设备间",
                evidence_lines=["直播间连接设备间。"],
            ),
        ]
    )

    with pytest.raises(ScenePromptQualityError):
        await compiler._apply_base_scene_reconcile_output(
            output,
            "直播间连接设备间。",
            SimpleNamespace(number=1),
            lambda _message: None,
        )

    assert store.sqlite_store.added == []


@pytest.mark.asyncio
async def test_compile_episode_scenes_does_not_partially_write_when_second_prompt_fails(
    monkeypatch,
):
    import novelvideo.agents.asset_compiler as asset_compiler
    from novelvideo.cognee.pipeline import ScenePromptQualityError

    scenes = [
        NovelScene(name="直播间", scene_type="interior", environment_prompt=""),
        NovelScene(name="设备间", scene_type="interior", environment_prompt=""),
    ]
    calls = 0

    async def fake_load_scene_blocks(self, episode):
        return [_block("直播间"), _block("设备间")]

    async def fake_enrich(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ScenePromptQualityError(
                "SCENE_PROMPT_QUALITY_FAILED: 设备间: meta_instruction:合理补全"
            )
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(scenes, raw_content="直播间连接设备间。")
    compiler = asset_compiler.AssetCompiler(store)

    with pytest.raises(ScenePromptQualityError):
        await compiler.compile_episode_scenes(SimpleNamespace(number=1), lambda _message: None)

    assert store.sqlite_store.atomic_calls == []
    assert store.sqlite_store.added == []
    assert store.sqlite_store.updated == []
    assert [scene.environment_prompt for scene in scenes] == ["", ""]
    assert store.updated == []


@pytest.mark.asyncio
async def test_build_scene_plan_draft_has_no_persistent_writes(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_load_scene_blocks(self, episode):
        return [_block("直播间")]

    async def fake_reconcile(self, source_text, episode, log):
        scene = NovelScene(
            name="直播间",
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        )
        await self.cognee_store.sqlite_store.add_scene(scene)
        return [scene]

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(raw_content="直播间内，灯光昏暗。")
    compiler = asset_compiler.AssetCompiler(store)
    episode = SimpleNamespace(number=1, scene_menu=[])

    draft = await compiler.build_scene_plan_draft(episode)

    assert [scene.name for scene in draft.scenes] == ["直播间"]
    assert [item.scene_id for item in draft.scene_menu] == ["直播间"]
    assert draft.new_count == 1
    assert draft.scene_baseline_digests == {}
    assert store.sqlite_store.added == []
    assert store.sqlite_store.atomic_calls == []
    assert store.updated == []


@pytest.mark.asyncio
async def test_scene_draft_tracks_existing_baseline_is_frozen_and_keeps_source_unchanged(
    monkeypatch,
):
    import novelvideo.agents.asset_compiler as asset_compiler

    original = NovelScene(name="咖啡馆", environment_prompt="", notes="用户备注")

    async def fake_load_scene_blocks(self, episode):
        return [_block("咖啡馆")]

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)
    store = _FakeCogneeStore([original], raw_content="咖啡馆内。")
    compiler = asset_compiler.AssetCompiler(store)

    draft = await compiler.build_scene_plan_draft(SimpleNamespace(number=1, scene_menu=[]))

    assert set(draft.scene_baseline_digests) == {"咖啡馆"}
    assert draft.scenes[0].environment_prompt == ENRICHED_ENVIRONMENT_PROMPT
    assert original.environment_prompt == ""
    assert store.sqlite_store.updated == []
    assert store.sqlite_store.atomic_calls == []
    with pytest.raises(ValidationError, match="frozen"):
        draft.new_count = 3
    with pytest.raises(ValidationError, match="extra_forbidden"):
        asset_compiler.ScenePlanDraft.model_validate(
            {**draft.model_dump(), "unexpected": True}
        )


@pytest.mark.asyncio
async def test_compile_episode_scenes_writes_all_prompt_repairs_once_atomically(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    scenes = [
        NovelScene(name="直播间", scene_type="interior", environment_prompt=""),
        NovelScene(name="设备间", scene_type="interior", environment_prompt=""),
    ]

    async def fake_load_scene_blocks(self, episode):
        return [_block("直播间"), _block("设备间")]

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(scenes, raw_content="直播间连接设备间。")
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, new_count = await compiler.compile_episode_scenes(
        SimpleNamespace(number=1), lambda _message: None
    )

    assert [item.scene_id for item in scene_menu] == ["直播间", "设备间"]
    assert new_count == 0
    assert store.sqlite_store.atomic_calls == [(["直播间", "设备间"], False)]
    assert store.sqlite_store.updated == []
    assert store.sqlite_store.published_menus == [(1, scene_menu, None)]
    assert store.updated == []


@pytest.mark.asyncio
async def test_compile_episode_scenes_creates_empty_time_plate_slot_for_repeated_time(
    monkeypatch,
):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            aliases=[],
            scene_type="interior",
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="咖啡馆",
        )

    async def fake_derived(self, scene_name, block):
        return []

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="咖啡馆",
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
                description="咖啡馆",
            )
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block(time_of_day="夜"), _block(time_of_day="夜")],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_夜晚"]
    assert scene_menu[1].base_scene_id == "咖啡馆"
    assert scene_menu[1].variant_id == ""
    assert scene_menu[1].time_of_day == "夜晚"
    time_plate = next(scene for scene in pending_scenes if scene.name == "咖啡馆_夜晚")
    assert time_plate.base_scene_id == "咖啡馆"
    assert time_plate.variant_id == ""
    assert time_plate.time_of_day == "夜晚"
    assert "空 plate 槽位" in time_plate.notes


@pytest.mark.asyncio
async def test_compile_episode_scenes_uses_narrated_fallback_without_scene_headers(
    monkeypatch, tmp_path
):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_extract(self, source_text, episode, log):
        assert "医院走廊" in source_text
        return [
            NovelScene(
                name="医院走廊",
                scene_type="interior",
                environment_prompt="正面：护士站与急诊指示牌\n左侧：病房门\n右侧：候诊椅\n背面：电梯间",
                description="急诊楼医院走廊",
            )
        ]

    async def fake_reconcile(self, source_text, episode, log):
        return []

    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_extract_narrated_episode_scenes",
        fake_extract,
        raising=False,
    )
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )

    store = _FakeCogneeStore(
        raw_content="林晚冲进医院走廊，护士站前的灯牌闪烁。",
        project_dir=str(tmp_path),
    )
    compiler = asset_compiler.AssetCompiler(store)
    episode = SimpleNamespace(number=1, title="第一集", beat_source_text="")

    scene_menu, new_count = await compiler.compile_episode_scenes(episode, lambda _message: None)

    assert new_count == 1
    assert scene_menu[0].scene_id == "医院走廊"
    assert store.sqlite_store.added[0].name == "医院走廊"
    assert store.sqlite_store.published_menus == [(1, scene_menu, None)]
    assert store.updated == []


@pytest.mark.asyncio
async def test_compile_scenes_does_not_promote_single_block_visual_state(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, blocks):
        return [
            asset_compiler.DerivedSceneRequirement(
                label="暴雨版",
                description="窗外暴雨，玻璃挂满水痕",
                lighting="昏黄灯光",
                atmosphere="潮湿雨夜空气",
                evidence_scene_headers=[blocks[0].header_line],
                affects_whole_environment=True,
                requires_shared_plate=True,
            )
        ]

    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="咖啡馆",
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
                description="雨夜咖啡馆",
            )
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block()],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["咖啡馆"]
    assert pending_scenes == []


@pytest.mark.asyncio
async def test_compile_scenes_reuses_existing_derived_scene(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_derived(self, scene_name, blocks):
        return [
            asset_compiler.DerivedSceneRequirement(
                label="封控版",
                description="入口和通道持续封闭",
                evidence_scene_headers=[block.header_line for block in blocks],
                affects_whole_environment=True,
                requires_shared_plate=True,
            )
        ]

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="咖啡馆",
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            ),
            NovelScene(
                name="咖啡馆_封控版",
                scene_type="interior",
                base_scene_id="咖啡馆",
                variant_id="封控版",
                variant_prompt="已有封控版",
            ),
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    scene_menu, pending_scenes = await compiler._compile_scenes(
        [_block(time_of_day="日", scene_number=1), _block(time_of_day="夜", scene_number=2)],
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_封控版"]
    assert pending_scenes == []


@pytest.mark.asyncio
async def test_compile_episode_scenes_persists_base_and_derived_as_normal_scenes(
    monkeypatch, tmp_path
):
    import novelvideo.agents.asset_compiler as asset_compiler

    async def fake_load_scene_blocks(self, episode):
        return [
            _block(time_of_day="日", scene_number=1),
            _block(time_of_day="夜", scene_number=2),
        ]

    async def fake_enrich(**kwargs):
        return NovelScene(
            name=kwargs["scene_name"],
            scene_type=kwargs["scene_type"],
            environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            description="雨夜咖啡馆",
        )

    async def fake_derived(self, scene_name, blocks):
        return [
            asset_compiler.DerivedSceneRequirement(
                label="封控版",
                description="入口和通道持续封闭",
                evidence_scene_headers=[block.header_line for block in blocks],
                affects_whole_environment=True,
                requires_shared_plate=True,
            )
        ]

    async def fake_reconcile(self, source_text, episode, log):
        scene = await fake_enrich(
            scene_name="咖啡馆",
            scene_type="interior",
            context_lines=["窗外暴雨如注。"],
        )
        await self.cognee_store.sqlite_store.add_scene(scene)
        return ["咖啡馆"]

    monkeypatch.setattr(asset_compiler.AssetCompiler, "_load_scene_blocks", fake_load_scene_blocks)
    monkeypatch.setattr(asset_compiler, "enrich_scene_environment_from_context", fake_enrich)
    monkeypatch.setattr(asset_compiler.AssetCompiler, "_analyze_derived_scenes", fake_derived)
    monkeypatch.setattr(
        asset_compiler.AssetCompiler,
        "_reconcile_base_scenes_from_text",
        fake_reconcile,
    )

    store = _FakeCogneeStore(project_dir=str(tmp_path))
    compiler = asset_compiler.AssetCompiler(store)
    scene_menu, new_count = await compiler.compile_episode_scenes(
        SimpleNamespace(number=1, title="第一集"),
        lambda _message: None,
    )

    assert new_count == 1
    assert [scene.name for scene in store.sqlite_store.added] == ["咖啡馆", "咖啡馆_封控版"]
    assert [item.scene_id for item in scene_menu] == ["咖啡馆", "咖啡馆_封控版"]
    assert scene_menu[1].base_scene_id == "咖啡馆"
    assert scene_menu[1].variant_id == "封控版"
    assert store.sqlite_store.published_menus == [(1, scene_menu, None)]
    assert store.updated == []


@pytest.mark.asyncio
async def test_base_scene_reconcile_does_not_create_existing_alias():
    import novelvideo.agents.asset_compiler as asset_compiler

    store = _FakeCogneeStore(
        [
            NovelScene(
                name="医院走廊",
                aliases=["急诊走廊"],
                scene_type="interior",
                environment_prompt=ENRICHED_ENVIRONMENT_PROMPT,
            )
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    created = await compiler._apply_base_scene_reconcile_output(
        asset_compiler.EpisodeBaseSceneReconcileOutput(
            scenes=[
                asset_compiler.BaseSceneReconcileDecision(
                    action="create",
                    scene_name="急诊走廊",
                    scene_type="interior",
                    evidence_lines=["急诊走廊里灯牌闪烁。"],
                )
            ]
        ),
        "急诊走廊里灯牌闪烁。",
        SimpleNamespace(number=1),
        lambda _message: None,
    )

    assert created == []
    assert sorted(store.sqlite_store.scenes) == ["医院走廊"]


@pytest.mark.asyncio
async def test_find_matching_scene_treats_independent_underscore_scene_as_base_candidate():
    import novelvideo.agents.asset_compiler as asset_compiler

    store = _FakeCogneeStore(
        [
            NovelScene(name="地下", aliases=[], scene_type="interior"),
            NovelScene(name="地下_主控室", aliases=["主控室"], scene_type="interior"),
            NovelScene(
                name="地下_漏水",
                base_scene_id="地下",
                variant_id="漏水",
                scene_type="interior",
            ),
        ]
    )
    compiler = asset_compiler.AssetCompiler(store)

    matched = await compiler._find_matching_scene("主控室")

    assert matched is not None
    assert matched.name == "地下_主控室"


@pytest.mark.asyncio
async def test_legacy_asset_compiler_recognizes_swallowed_cognee_refresh_failure(caplog):
    store = _FakeCogneeStore()

    async def failed_refresh():
        return False

    store.load_graph_state = failed_refresh
    compiler = __import__(
        "novelvideo.agents.asset_compiler", fromlist=["AssetCompiler"]
    ).AssetCompiler(store)

    assert await compiler._refresh_after_asset_publish() is False
    assert "cache refresh is pending" in caplog.text


@pytest.mark.asyncio
async def test_legacy_scene_publication_defers_shared_cache_refresh(monkeypatch):
    import novelvideo.agents.asset_compiler as asset_compiler

    store = _FakeCogneeStore()
    compiler = asset_compiler.AssetCompiler(store)
    draft = asset_compiler.ScenePlanDraft(
        scenes=(),
        scene_menu=(),
        new_count=0,
        scene_baseline_digests={},
        episode_scene_menu_baseline_digest="baseline",
    )

    async def build_draft(*_args, **_kwargs):
        return draft

    monkeypatch.setattr(compiler, "build_scene_plan_draft", build_draft)

    await compiler.compile_episode_scenes(SimpleNamespace(number=1))

    assert store.sqlite_store.publish_refresh_cache == [False]


def test_derived_scene_specs_require_cross_block_global_plate_evidence():
    import novelvideo.agents.asset_compiler as asset_compiler

    headers = {"场次1", "场次2"}
    normalized = asset_compiler.AssetCompiler._build_derived_scene_specs(
        [
            asset_compiler.DerivedSceneRequirement(
                label="暴雨积水版",
                evidence_scene_headers=["场次1"],
                affects_whole_environment=True,
                requires_shared_plate=True,
            ),
            asset_compiler.DerivedSceneRequirement(
                label="油灯侧光版",
                evidence_scene_headers=["场次1", "场次2"],
                affects_whole_environment=False,
                requires_shared_plate=True,
            ),
            asset_compiler.DerivedSceneRequirement(
                label="封控版",
                evidence_scene_headers=["场次1", "场次2"],
                affects_whole_environment=True,
                requires_shared_plate=True,
            ),
        ],
        valid_scene_headers=headers,
    )

    assert [item.label for item in normalized] == ["封控版"]


def test_derived_scene_specs_keep_only_two_highest_coverage_candidates():
    import novelvideo.agents.asset_compiler as asset_compiler

    headers = {"场次1", "场次2", "场次3", "场次4"}

    def candidate(label: str, evidence: list[str]):
        return asset_compiler.DerivedSceneRequirement(
            label=label,
            evidence_scene_headers=evidence,
            affects_whole_environment=True,
            requires_shared_plate=True,
        )

    normalized = asset_compiler.AssetCompiler._build_derived_scene_specs(
        [
            candidate("两场复用版", ["场次1", "场次2"]),
            candidate("四场复用版", ["场次1", "场次2", "场次3", "场次4"]),
            candidate("三场复用版", ["场次1", "场次2", "场次3"]),
        ],
        valid_scene_headers=headers,
    )

    assert [item.label for item in normalized] == ["四场复用版", "三场复用版"]
