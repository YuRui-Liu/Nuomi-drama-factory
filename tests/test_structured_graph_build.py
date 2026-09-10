from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def structured_store(tmp_path: Path):
    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    store = SQLiteStore(
        "user/structured",
        output_dir=str(project_dir),
        state_dir=str(state_dir),
    )
    await store.initialize()
    await store.load_graph_state()
    try:
        yield store
    finally:
        await store.close()


def test_character_artifact_cache_is_scoped_to_locked_snapshot():
    from novelvideo.structured_builders import _decode_character_artifact

    artifact = json.dumps(
        {
            "excluded_names": ["周禾"],
            "characters": [{"name": "梁真", "design_proposals": [{}, {}, {}]}],
        },
        ensure_ascii=False,
    )
    assert _decode_character_artifact(artifact, {"周禾"}) is not None
    assert _decode_character_artifact(artifact, set()) is None


def test_legacy_character_artifact_without_lock_snapshot_is_not_reused():
    from novelvideo.structured_builders import _decode_character_artifact

    artifact = json.dumps(
        [{"name": "梁真", "design_proposals": [{}, {}, {}]}],
        ensure_ascii=False,
    )
    assert _decode_character_artifact(artifact, set()) is None


@pytest.mark.asyncio
async def test_structured_runner_rejects_ai_episode_planning_before_store_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.knowledge_pipeline import KnowledgePipelineUnsupported
    from novelvideo.task_backend.runners import graph_build

    output_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    output_dir.mkdir()
    state_dir.mkdir()
    (output_dir / "novel.txt").write_text("第一章\n正文", encoding="utf-8")
    (state_dir / "project_config.json").write_text(
        json.dumps({"knowledge_pipeline": "structured_v1"}), encoding="utf-8"
    )
    ctx = SimpleNamespace(output_dir=output_dir, state_dir=state_dir)

    async def forbidden_store(_ctx):
        raise AssertionError("AI mode must fail before any store/runtime is opened")

    monkeypatch.setattr(graph_build, "_load_store", forbidden_store)
    sys.modules.pop("novelvideo.agents.episode_planner", None)
    with pytest.raises(KnowledgePipelineUnsupported, match="deterministic"):
        await graph_build._run_build_episodes(
            {"payload": {"config": {"planning_mode": "ai"}}}, ctx
        )
    assert "novelvideo.agents.episode_planner" not in sys.modules


@pytest.mark.asyncio
async def test_structured_runner_opens_plain_sqlite_store(tmp_path: Path) -> None:
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.task_backend.runners.graph_build import _load_store

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(
        json.dumps({"knowledge_pipeline": "structured_v1"}), encoding="utf-8"
    )
    ctx = SimpleNamespace(
        owner_project_label="user/project",
        output_dir=tmp_path / "project",
        state_dir=state_dir,
    )
    store = await _load_store(ctx)
    try:
        assert type(store) is SQLiteStore
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_deterministic_chapter_build_preserves_existing_episode_assets(
    structured_store,
) -> None:
    from novelvideo.models import NovelEpisode

    store = structured_store
    store.save_novel_content("第一章 归来\n甲。\n\n第二章 旧友\n乙。")
    await store.add_episode(
        NovelEpisode(
            number=1,
            title="用户旧标题",
            raw_content="旧正文",
            identity_ids=["沈青_记者"],
            scene_menu=[{"scene_id": "旧车站"}],
            prop_menu=[{"prop_id": "录音笔"}],
            sketch_colors_json='{"沈青_记者":"red"}',
        )
    )

    episodes = await store.build_episodes_from_chapters()

    assert [episode.number for episode in episodes] == [1, 2]
    assert episodes[0].raw_content.startswith("第一章")
    assert episodes[0].identity_ids == ["沈青_记者"]
    assert episodes[0].scene_menu[0].scene_id == "旧车站"
    assert episodes[0].prop_menu[0].prop_id == "录音笔"
    assert episodes[0].sketch_colors_json == '{"沈青_记者":"red"}'


@pytest.mark.asyncio
async def test_structured_character_build_adds_missing_without_overwriting_user_asset(
    structured_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.models import NovelCharacter
    from novelvideo.structured_builders import build_characters_structured
    from novelvideo.structured_extraction import MergedCharacter

    store = structured_store
    store.save_novel_content("第一章\n林默遇见沈青。")
    (Path(store.state_dir) / "project_config.json").write_text(
        json.dumps({"spine_template": "narrated"}), encoding="utf-8"
    )
    await store.add_character(
        NovelCharacter(name="林默", role="用户设定", face_prompt="用户面容")
    )
    await store.load_graph_state()

    async def fake_extract(_chunks, **_kwargs):
        def proposals(prefix: str):
            result = [
                    {
                        "proposal_id": f"{prefix}-a",
                        "title": "冷峻骨相",
                        "recommended": True,
                        "face_shape": "窄长脸，颧骨清晰",
                        "facial_features": ["眼窝偏深", "鼻梁笔直"],
                        "hair_style": "利落短发",
                        "distinctive_features": ["左眉尾断眉"],
                        "identity_anchors": ["窄长脸", "深眼窝", "左眉尾断眉"],
                        "asymmetry_detail": "左眉尾断眉",
                    },
                    {
                        "proposal_id": f"{prefix}-b",
                        "title": "克制圆润",
                        "face_shape": "短圆脸，下颌柔和",
                        "facial_features": ["右眼略窄", "鼻头微圆"],
                        "hair_style": "自然侧分发",
                        "distinctive_features": ["右眼下浅痣"],
                        "identity_anchors": ["短圆脸", "右眼略窄", "右眼下浅痣"],
                        "asymmetry_detail": "右眼下浅痣",
                    },
                    {
                        "proposal_id": f"{prefix}-c",
                        "title": "坚毅方正",
                        "face_shape": "方脸，下颌角明确",
                        "facial_features": ["眉弓较高", "薄唇"],
                        "hair_style": "略乱寸发",
                        "distinctive_features": ["左侧嘴角旧疤"],
                        "identity_anchors": ["方脸", "高眉弓", "左嘴角旧疤"],
                        "asymmetry_detail": "左侧嘴角旧疤",
                    },
            ]
            if prefix == "shen":
                for index, proposal in enumerate(result, start=1):
                    proposal["face_shape"] = [
                        "菱形脸，颧骨外扩",
                        "鹅蛋脸，额头饱满",
                        "三角脸，下巴收尖",
                    ][index - 1]
                    proposal["facial_features"] = [
                        ["厚下唇", "耳垂偏小"],
                        ["平直眉", "薄上唇"],
                        ["下垂眼尾", "贴面耳"],
                    ][index - 1]
                    proposal["hair_style"] = [
                        "低位盘发",
                        "齐肩黑色直发",
                        "蓬松短卷发",
                    ][index - 1]
                    proposal["distinctive_features"] = [
                        ["右耳上缘小缺口"],
                        ["鼻尖左侧浅痣"],
                        ["右眼尾短疤"],
                    ][index - 1]
                    proposal["identity_anchors"] = [
                        proposal["face_shape"],
                        proposal["facial_features"][0],
                        proposal["distinctive_features"][0],
                    ]
                    proposal["asymmetry_detail"] = proposal["distinctive_features"][0]
            return result
        return [
            MergedCharacter(
                name="林默",
                description="模型描述",
                design_proposals=proposals("lin"),
            ),
            MergedCharacter(
                name="沈青",
                gender="female",
                description="调查记者",
                design_proposals=proposals("shen"),
            ),
        ]

    monkeypatch.setattr(
        structured_extraction, "extract_characters_from_chunks", fake_extract
    )
    added = await build_characters_structured(store)

    assert added == ["沈青"]
    assert store.get_character("林默").role == "用户设定"
    assert store.get_character("林默").face_prompt == "用户面容"
    assert store.get_character("沈青").description == "调查记者"


@pytest.mark.asyncio
async def test_character_build_preserves_profile_when_roster_proposal_collides(
    structured_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.character_visual import CharacterVisualWorkspaceStore
    from novelvideo.structured_builders import build_characters_structured
    from novelvideo.structured_extraction import MergedCharacter

    store = structured_store
    store.save_novel_content("第一章\n谢秋月遇见赵班头。")
    (Path(store.state_dir) / "project_config.json").write_text(
        json.dumps({"spine_template": "narrated"}), encoding="utf-8"
    )

    def proposal(
        proposal_id: str,
        *,
        face_shape: str,
        facial_feature: str,
        hair_style: str,
        distinctive_feature: str,
        recommended: bool = False,
    ) -> dict[str, object]:
        return {
            "proposal_id": proposal_id,
            "title": proposal_id,
            "recommended": recommended,
            "face_shape": face_shape,
            "facial_features": [facial_feature, "鼻梁笔直"],
            "hair_style": hair_style,
            "distinctive_features": [distinctive_feature],
            "identity_anchors": [
                face_shape,
                facial_feature,
                distinctive_feature,
            ],
            "asymmetry_detail": distinctive_feature,
        }

    shared = {
        "face_shape": "窄长脸，颧骨清晰",
        "facial_feature": "眼窝偏深",
        "hair_style": "利落短发",
        "distinctive_feature": "左眉尾断眉",
    }
    first_proposals = [
        proposal("xqy-01", **shared, recommended=True),
        proposal(
            "xqy-02",
            face_shape="短圆脸，下颌柔和",
            facial_feature="右眼略窄",
            hair_style="自然侧分发",
            distinctive_feature="右眼下浅痣",
        ),
        proposal(
            "xqy-03",
            face_shape="方脸，下颌角明确",
            facial_feature="眉弓较高",
            hair_style="略乱寸发",
            distinctive_feature="左侧嘴角旧疤",
        ),
    ]
    second_proposals = [
        proposal("zbt-01", **shared, recommended=True),
        proposal(
            "zbt-02",
            face_shape="菱形脸，颧骨外扩",
            facial_feature="厚下唇",
            hair_style="低位盘发",
            distinctive_feature="右耳上缘小缺口",
        ),
        proposal(
            "zbt-03",
            face_shape="三角脸，下巴收尖",
            facial_feature="下垂眼尾",
            hair_style="蓬松短卷发",
            distinctive_feature="右眼尾短疤",
        ),
    ]

    async def fake_extract(_chunks, **_kwargs):
        return [
            MergedCharacter(
                name="谢秋月",
                biography="谢家人物。",
                design_proposals=first_proposals,
            ),
            MergedCharacter(
                name="赵班头",
                biography="负责巡视的班头。",
                design_proposals=second_proposals,
            ),
        ]

    monkeypatch.setattr(
        structured_extraction, "extract_characters_from_chunks", fake_extract
    )

    result = await build_characters_structured(store)

    assert store.get_character("赵班头").description == "负责巡视的班头。"
    assert result.stats["proposal_failed"] == ["赵班头"]
    assert result.as_task_result(total=2)["proposal_failed"] == 1
    workspace = CharacterVisualWorkspaceStore(store.project_dir).get("赵班头")
    assert workspace is not None
    assert workspace.design_proposals[0].quality_issues == [
        "roster_collision:xqy-01"
    ]
    assert workspace.design_proposals[2].quality_issues == []


@pytest.mark.asyncio
async def test_character_build_ignores_previously_rejected_roster_proposals(
    structured_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.character_visual import (
        CharacterDesignProposal,
        CharacterNarrativeProfile,
        CharacterVisualWorkspace,
        CharacterVisualWorkspaceStore,
    )
    from novelvideo.models import NovelCharacter
    from novelvideo.structured_builders import build_characters_structured
    from novelvideo.structured_extraction import MergedCharacter

    store = structured_store
    store.save_novel_content("第一章\n旧角色遇见新角色。")
    (Path(store.state_dir) / "project_config.json").write_text(
        json.dumps({"spine_template": "narrated"}), encoding="utf-8"
    )
    await store.add_character(NovelCharacter(name="旧角色"))
    await store.load_graph_state()

    rejected = CharacterDesignProposal(
        proposal_id="old-rejected",
        title="上一轮失败提案",
        recommended=True,
        face_shape="窄长脸，颧骨清晰",
        facial_features=["眼窝偏深", "鼻梁笔直"],
        hair_style="利落短发",
        distinctive_features=["左眉尾断眉"],
        identity_anchors=["窄长脸，颧骨清晰", "眼窝偏深", "左眉尾断眉"],
        asymmetry_detail="左眉尾断眉",
        quality_issues=["roster_collision:another-old-proposal"],
    )
    CharacterVisualWorkspaceStore(store.project_dir).save(
        CharacterVisualWorkspace(
            character_id="旧角色",
            profile=CharacterNarrativeProfile(
                character_id="旧角色", name="旧角色"
            ),
            design_proposals=[rejected],
        )
    )

    proposals = [
        rejected.model_copy(
            update={
                "proposal_id": "new-01",
                "title": "新提案一",
                "quality_issues": [],
            }
        ).model_dump(mode="json"),
        CharacterDesignProposal(
            proposal_id="new-02",
            title="新提案二",
            face_shape="短圆脸，下颌柔和",
            facial_features=["右眼略窄", "鼻头微圆"],
            hair_style="自然侧分发",
            distinctive_features=["右眼下浅痣"],
            identity_anchors=["短圆脸，下颌柔和", "右眼略窄", "右眼下浅痣"],
            asymmetry_detail="右眼下浅痣",
        ).model_dump(mode="json"),
        CharacterDesignProposal(
            proposal_id="new-03",
            title="新提案三",
            face_shape="方脸，下颌角明确",
            facial_features=["眉弓较高", "薄唇"],
            hair_style="略乱寸发",
            distinctive_features=["左侧嘴角旧疤"],
            identity_anchors=["方脸，下颌角明确", "眉弓较高", "左侧嘴角旧疤"],
            asymmetry_detail="左侧嘴角旧疤",
        ).model_dump(mode="json"),
    ]

    async def fake_extract(_chunks, **_kwargs):
        return [MergedCharacter(name="新角色", design_proposals=proposals)]

    monkeypatch.setattr(
        structured_extraction, "extract_characters_from_chunks", fake_extract
    )

    result = await build_characters_structured(store)

    assert result.stats["proposal_failed"] == []
    workspace = CharacterVisualWorkspaceStore(store.project_dir).get("新角色")
    assert workspace is not None
    assert all(not proposal.quality_issues for proposal in workspace.design_proposals)


@pytest.mark.asyncio
async def test_character_build_does_not_report_failed_workspace_skipped_by_late_lock(
    structured_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.character_visual import CharacterVisualWorkspaceStore
    from novelvideo.models import NovelCharacter
    from novelvideo.structured_builders import build_characters_structured
    from novelvideo.structured_extraction import MergedCharacter

    store = structured_store
    store.save_novel_content("第一章\n赵班头巡街。")
    (Path(store.state_dir) / "project_config.json").write_text(
        json.dumps({"spine_template": "narrated"}), encoding="utf-8"
    )
    await store.add_character(NovelCharacter(name="赵班头"))
    await store.load_graph_state()

    repeated = {
        "face_shape": "窄长脸，颧骨清晰",
        "facial_features": ["眼窝偏深", "鼻梁笔直"],
        "hair_style": "利落短发",
        "distinctive_features": ["左眉尾断眉"],
        "identity_anchors": ["窄长脸，颧骨清晰", "眼窝偏深", "左眉尾断眉"],
        "asymmetry_detail": "左眉尾断眉",
    }
    proposals = [
        {
            "proposal_id": f"zbt-0{index}",
            "title": f"赵班头提案 {index}",
            "recommended": index == 1,
            **repeated,
        }
        for index in range(1, 4)
    ]

    async def fake_extract(_chunks, **_kwargs):
        return [MergedCharacter(name="赵班头", design_proposals=proposals)]

    original_publish = store.publish_character_analysis_atomic

    async def lock_before_publish(*args, **kwargs):
        await store.set_character_extraction_locked("赵班头", True)
        return await original_publish(*args, **kwargs)

    monkeypatch.setattr(
        structured_extraction, "extract_characters_from_chunks", fake_extract
    )
    monkeypatch.setattr(
        store, "publish_character_analysis_atomic", lock_before_publish
    )

    result = await build_characters_structured(store)

    assert result.stats["locked_skipped"] == ["赵班头"]
    assert result.stats["proposal_failed"] == []
    assert CharacterVisualWorkspaceStore(store.project_dir).get("赵班头") is None


@pytest.mark.asyncio
async def test_structured_scene_build_is_atomic_and_preserves_existing_scene(
    structured_store,
) -> None:
    from novelvideo.models import NovelScene
    from novelvideo.structured_builders import build_scenes_structured

    store = structured_store
    store.save_novel_content(
        "1-1 林家客厅 日 内\n人物：林默\n林默推门。\n\n"
        "1-2 巷口 夜 外\n人物：林默\n林默离开。"
    )
    (Path(store.state_dir) / "project_config.json").write_text(
        json.dumps({"spine_template": "drama"}), encoding="utf-8"
    )
    await store.add_scene(
        NovelScene(name="林家客厅", environment_prompt="用户场景提示")
    )

    result = await build_scenes_structured(store)

    assert result["scenes"] == 2
    assert result["added_scenes"] == 1
    assert (await store.get_scene("林家客厅")).environment_prompt == "用户场景提示"
    assert (await store.get_scene("巷口")).scene_type == "exterior"


@pytest.mark.asyncio
async def test_structured_prop_build_explicitly_defers(structured_store) -> None:
    from novelvideo.structured_builders import build_props_structured

    result = await build_props_structured(structured_store)
    assert result["props"] == 0
    assert result["mode"] == "episode_on_demand"
    assert result["message"]
