import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from novelvideo.script_creation.store import DocumentConflict, DocumentStore, DocumentValidation
from tests.script_creation.test_generation_api import client  # noqa: F401


@pytest.fixture
async def supplement_setup(tmp_path):
    from novelvideo.models import CharacterIdentity, NovelCharacter, NovelScene
    from novelvideo.script_creation.episode_asset_supplement import EpisodeAssetSupplementService
    from novelvideo.sqlite_store import SQLiteStore

    assets = SQLiteStore("test", output_dir=str(tmp_path))
    await assets.initialize()
    store = DocumentStore(assets.db_path)
    await store.initialize()
    service = EpisodeAssetSupplementService(store)
    await service.initialize()
    await assets.add_character(NovelCharacter(name="林舟", description="已有角色"))
    await assets.add_character_identity("林舟", CharacterIdentity(
        identity_id="linzhou-youth", character_name="林舟", identity_name="少年时期"))
    await assets.add_scene(NovelScene(name="旧仓库", environment_prompt="已有场景"))
    await assets.add_scene(NovelScene(
        name="旧仓库-雨后", base_scene_id="旧仓库", variant_id="雨后", variant_prompt="地面积水"))
    document = await store.create(
        kind="episode_script",
        title="第 1 集",
        episode_number=1,
        markdown=(
            "# 第 1 集\n\n"
            "## 1. 旧仓库 内 夜\n\n"
            "动作：林舟把赤绳结交给闻溪，作为识别盟友的信物。\n"
            "动作：旧仓库封控版中，所有出口和通道均被持续封闭。\n"
            "动作：林舟少年时期和旧仓库雨后的稳定形态在回忆中再次出现。\n"
            "动作：林舟的衣服被雨打湿。"
        ),
        client_mutation_id="screenplay",
    )
    yield store, assets, service, document
    await assets.close()


async def test_supplement_manifest_is_evidence_bound_episode_local_and_reversible(supplement_setup):
    from novelvideo.script_creation.episode_asset_supplement import EpisodeAssetSupplementOutput

    store, assets, service, document = supplement_setup
    catalog = await service._asset_catalog()
    linzhou = next(item for item in catalog if item["name"] == "林舟")
    warehouse = next(item for item in catalog if item["name"] == "旧仓库")
    assert linzhou["variants"] == [{"variant_id": "linzhou-youth", "variant_name": "少年时期"}]
    assert warehouse["variants"] == [{"variant_id": "旧仓库-雨后", "variant_name": "雨后"}]
    evidence_blocks = document.revision.blocks

    def quote(text):
        block = next(block for block in evidence_blocks if text in block.markdown)
        return {"block_id": block.id, "quote": text}

    output = EpisodeAssetSupplementOutput.model_validate({"items": [
        {"action": "new_asset", "asset_type": "character", "name": "闻溪",
         "evidence": [quote("林舟把赤绳结交给闻溪，作为识别盟友的信物。")],
         "reason": "闻溪参与身份识别转折，是本集重要新角色。"},
        {"action": "reuse", "asset_type": "character", "name": "林舟",
         "existing_asset_id": linzhou["asset_id"],
         "evidence": [quote("林舟把赤绳结交给闻溪，作为识别盟友的信物。")],
         "reason": "与已有角色名完全一致。"},
        {"action": "stable_variant", "asset_type": "scene", "name": "旧仓库",
         "base_asset_id": warehouse["asset_id"], "variant_name": "封控版",
         "evidence": [quote("动作：旧仓库封控版中，所有出口和通道均被持续封闭。")],
         "reason": "整体空间变化需要独立场景参考。"},
        {"action": "stable_variant", "asset_type": "character", "name": "林舟",
         "base_asset_id": linzhou["asset_id"], "variant_name": "少年时期",
         "evidence": [quote("动作：林舟少年时期和旧仓库雨后的稳定形态在回忆中再次出现。")],
         "reason": "沿用目录中已有的稳定身份变体。"},
        {"action": "stable_variant", "asset_type": "scene", "name": "旧仓库",
         "base_asset_id": warehouse["asset_id"], "variant_name": "雨后",
         "evidence": [quote("动作：林舟少年时期和旧仓库雨后的稳定形态在回忆中再次出现。")],
         "reason": "沿用目录中已有的场景变体。"},
        {"action": "reuse", "asset_type": "character", "name": "林舟",
         "existing_asset_id": linzhou["asset_id"], "existing_variant_id": "linzhou-youth",
         "variant_name": "少年时期",
         "evidence": [quote("林舟把赤绳结交给闻溪，作为识别盟友的信物。")],
         "reason": "不应在没有变体文字依据时匹配身份。"},
        {"action": "new_asset", "asset_type": "prop", "name": "赤绳结",
         "evidence": [quote("林舟把赤绳结交给闻溪，作为识别盟友的信物。")],
         "reason": "信物承担识别盟友的剧情作用。"},
        {"action": "stable_variant", "asset_type": "prop", "name": "赤绳结",
         "base_asset_id": "not-a-real-asset", "variant_name": "雨湿版",
         "evidence": [quote("动作：林舟的衣服被雨打湿。")],
         "reason": "临时状态。"},
        {"action": "new_asset", "asset_type": "prop", "name": "不存在的戒指",
         "evidence": [quote("动作：旧仓库封控版中，所有出口和通道均被持续封闭。")],
         "reason": "不应通过来源校验。"},
    ]})

    def snapshot_tables():
        with sqlite3.connect(assets.db_path) as db:
            db.row_factory = sqlite3.Row
            return {
                table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY name")]
                for table in ("characters", "scenes", "props")
            }

    before = snapshot_tables()
    items, rejected = service._manifest_items(output, document, catalog)
    assert [item["action"] for item in items] == [
        "new_asset", "reuse", "stable_variant", "reuse", "reuse", "keep_as_text",
        "new_asset", "keep_as_text", "keep_as_text",
    ]
    assert items[1]["existing_asset_id"] == linzhou["asset_id"]
    assert items[2]["base_asset_id"] == warehouse["asset_id"]
    assert items[3]["existing_asset_id"] == linzhou["asset_id"]
    assert items[3]["existing_variant_id"] == "linzhou-youth"
    assert items[4]["existing_asset_id"] == warehouse["asset_id"]
    assert items[4]["existing_variant_id"] == "旧仓库-雨后"
    assert items[5]["existing_variant_id"] is None
    assert "衣服被雨打湿" in items[7]["evidence"][0]["quote"]
    assert rejected == []

    run = await service.start(document_id=document.id,
        base_revision_id=document.current_revision_id, client_mutation_id="supplement")

    class Runtime:
        async def run_structured(self, **kwargs):
            assert kwargs["output_type"] is EpisodeAssetSupplementOutput
            request = json.loads(kwargs["prompt"])
            assert request["revision_id"] == document.current_revision_id
            return output

    ready = await service.execute(run["id"], runtime=Runtime(), task_id="task")
    assert ready["status"] == "ready"
    assert len(ready["items"]) == 9
    assert ready["items"][2]["action"] == "stable_variant"
    assert ready["items"][2]["base_asset_id"] == warehouse["asset_id"]
    assert ready["items"][3]["existing_variant_id"] == "linzhou-youth"
    assert ready["items"][5]["action"] == "keep_as_text"
    assert ready["items"][7]["action"] == "keep_as_text"
    assert ready["items"][4]["base_asset_id"] is None

    assert snapshot_tables() == before
    deleted = await service.delete(run["id"])
    assert deleted == {"id": run["id"], "deleted": True}
    assert await service.list(document.id) == []


async def test_supplement_start_is_revision_bound_and_only_accepts_episode_scripts(supplement_setup):
    store, _assets, service, document = supplement_setup
    run = await service.start(document_id=document.id,
        base_revision_id=document.current_revision_id, client_mutation_id="one")
    replay = await service.start(document_id=document.id,
        base_revision_id=document.current_revision_id, client_mutation_id="one")
    assert replay["id"] == run["id"]

    await store.save(document.id, base_revision_id=document.current_revision_id,
        markdown="# 更新后的剧本\n\n动作：林舟离开。", client_mutation_id="edit")
    with pytest.raises(DocumentConflict):
        await service.start(document_id=document.id,
            base_revision_id=document.current_revision_id, client_mutation_id="stale")

    design = await store.create(kind="people", title="人物", markdown="## 林舟",
        client_mutation_id="people")
    with pytest.raises(DocumentValidation):
        await service.start(document_id=design.id,
            base_revision_id=design.current_revision_id, client_mutation_id="wrong-kind")


async def test_supplement_covers_named_core_characters_with_full_episode_context(
        supplement_setup, monkeypatch):
    from novelvideo.script_creation.episode_asset_supplement import EpisodeAssetSupplementOutput

    store, _assets, service, document = supplement_setup
    markdown = "\n\n".join([
        "# 第 1 集",
        "## 1. 旧仓库 内 夜",
        "动作：林舟冲进旧仓库，发现陈奶奶守在门边，门灯突然熄灭。",
        "陈奶奶：林舟，楼道又黑了，药还没送来。",
        "林舟：我先把门灯修好，再送你回家。",
        "动作：林舟蹲下检查门灯，陈奶奶站在一旁等他。",
        "陈奶奶：我就在这儿等你。",
    ])
    document = await store.save(
        document.id,
        base_revision_id=document.current_revision_id,
        markdown=markdown,
        client_mutation_id="core-character-coverage-script",
    )
    full_catalog = await service._asset_catalog()
    catalog_without_characters = [
        asset for asset in full_catalog if asset["asset_type"] != "character"
    ]

    async def catalog_without_character_assets():
        return catalog_without_characters

    monkeypatch.setattr(service, "_asset_catalog", catalog_without_character_assets)
    run = await service.start(
        document_id=document.id,
        base_revision_id=document.current_revision_id,
        client_mutation_id="core-character-coverage",
    )

    def quote(text):
        block = next(block for block in document.revision.blocks if text in block.markdown)
        return {"block_id": block.id, "quote": text}

    class Runtime:
        async def run_structured(self, **kwargs):
            request = json.loads(kwargs["prompt"])
            assert request["blocks"] == [
                {"id": block.id, "markdown": block.markdown}
                for block in document.revision.blocks
            ]
            assert request["existing_assets"] == catalog_without_characters
            assert not any(
                asset["asset_type"] == "character"
                for asset in request["existing_assets"]
            )
            return {"items": [
                {"action": "new_asset", "asset_type": "character", "name": "林舟",
                 "evidence": [quote("林舟：我先把门灯修好，再送你回家。")],
                 "reason": "反复行动并承担本集主线的核心人物。"},
                {"action": "new_asset", "asset_type": "character", "name": "陈奶奶",
                 "evidence": [quote("陈奶奶：林舟，楼道又黑了，药还没送来。"),
                              quote("陈奶奶：我就在这儿等你。")],
                 "reason": "反复出场并推动本集求助冲突的核心人物。"},
                {"action": "reuse", "asset_type": "scene", "name": "旧仓库",
                 "existing_asset_id": next(
                     asset["asset_id"] for asset in catalog_without_characters
                     if asset["asset_type"] == "scene" and asset["name"] == "旧仓库"
                 ),
                 "evidence": [quote("动作：林舟冲进旧仓库，发现陈奶奶守在门边，门灯突然熄灭。")],
                 "reason": "与目录中的场景名称完全一致。"},
                {"action": "keep_as_text",
                 "evidence": [quote("动作：林舟蹲下检查门灯，陈奶奶站在一旁等他。")],
                 "reason": "门灯熄灭是本集临时状态，保留在镜头文字中。"},
            ]}

    ready = await service.execute(run["id"], runtime=Runtime(), task_id="task-core-coverage")

    assert ready["status"] == "ready"
    assert [(item["action"], item["asset_type"], item["name"]) for item in ready["items"]] == [
        ("new_asset", "character", "林舟"),
        ("new_asset", "character", "陈奶奶"),
        ("reuse", "scene", "旧仓库"),
        ("keep_as_text", None, ""),
    ]
    assert ready["items"][1]["evidence"] == [
        quote("陈奶奶：林舟，楼道又黑了，药还没送来。"),
        quote("陈奶奶：我就在这儿等你。"),
    ]


def test_episode_asset_supplement_api_queues_polls_and_deletes(client, tmp_path, monkeypatch):
    from novelvideo.api.routes import script_creation as api
    from novelvideo.script_creation.episode_asset_supplement import EpisodeAssetSupplementService
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.sqlite_store import SQLiteStore

    async def make(ctx):
        if ctx is not None:
            ctx.output_dir = tmp_path
        store = SQLiteStore("test", output_dir=str(tmp_path))
        await store.initialize()
        return store

    monkeypatch.setattr(api, "make_sqlite_store_for_context", make)
    queued = []

    async def enqueue(ctx, **kwargs):
        queued.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id="supplement-task"))

    monkeypatch.setattr(api, "enqueue_project_task", enqueue)
    http, _user, _ = client
    base = "/api/v1/projects/one/script-creation"
    doc = http.post(base + "/documents", json={
        "kind": "episode_script", "title": "第 1 集", "episode_number": 1,
        "markdown": "# 第 1 集\n\n## 1. 旧仓库 内 夜\n\n动作：林舟递出赤绳结。",
        "client_mutation_id": "screenplay",
    }).json()["data"]
    body = {"document_id": doc["id"], "base_revision_id": doc["current_revision_id"],
            "client_mutation_id": "supplement"}
    response = http.post(base + "/episode-asset-supplements", json=body)
    assert response.status_code == 202
    run = response.json()["data"]
    assert run["task_id"] == "supplement-task"
    assert queued[0]["task_type"] == "script_creation_episode_asset_supplement"
    assert queued[0]["scope"] == f"episode-asset-supplement:{run['id']}"
    assert http.get(base + "/episode-asset-supplements", params={"document_id": doc["id"]}).json()["data"][0]["id"] == run["id"]
    assert http.get(base + "/episode-asset-supplements/" + run["id"]).status_code == 200

    async def finish():
        store = DocumentStore(tmp_path / "data.db")
        service = EpisodeAssetSupplementService(store)
        await service.initialize()
        class Runtime:
            async def run_structured(self, **kwargs):
                evidence_block = next(block for block in doc["revision"]["blocks"]
                    if "动作：林舟递出赤绳结。" in block["markdown"])
                return {"items": [{"action": "keep_as_text", "evidence": [
                    {"block_id": evidence_block["id"],
                     "quote": "动作：林舟递出赤绳结。"}], "reason": "暂不判断是否需要单独建资产。"}]}
        return await service.execute(run["id"], runtime=Runtime(), task_id="supplement-task")

    ready = asyncio.run(finish())
    assert ready["status"] == "ready"
    deleted = http.delete(base + "/episode-asset-supplements/" + run["id"])
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert http.get(base + "/episode-asset-supplements/" + run["id"]).status_code == 404
